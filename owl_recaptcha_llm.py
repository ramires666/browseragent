import json
import base64
import re
import requests
from owl_llm import API_URL, API_KEY

SYSTEM_PROMPT = """Look at the screenshot. Find ALL objects that match the instruction.
Return pixel coordinates (x,y) center of each matching object.

IMPORTANT: Use a 1000x1000 coordinate system. The image should be thought of as 1000x1000 pixels.
Map every pixel in the image to this 0-1000 range.

FORMAT:
{"clicks":[{"x":200,"y":300},{"x":500,"y":700}],"reason":"which objects match and why"}

If no matches: {"clicks":[],"skip":true}
If done: {"done":true}"""


def _extract_json(raw):
    trimmed = raw.strip()
    if trimmed.startswith("["):
        close = trimmed.rfind("]")
        if close >= 0:
            trimmed = trimmed[:close + 1]
    elif trimmed.startswith("{"):
        close = trimmed.rfind("}")
        if close >= 0:
            trimmed = trimmed[:close + 1]
    else:
        brace = trimmed.find("{")
        if brace >= 0:
            trimmed = trimmed[brace:]
            close = trimmed.rfind("}")
            if close >= 0:
                trimmed = trimmed[:close + 1]
        else:
            return None
    try:
        return json.loads(trimmed)
    except json.JSONDecodeError:
        return None


def _parse_reasoning(raw):
    """Парсит reasoning-текст: ищет (Contains ...) с Row/Col.
    Возвращает {"tiles": [...], "grid_cols": N, "grid_rows": M} или None."""
    grid = re.search(r'(\d+)\s*x\s*(\d+).*?grid', raw, re.IGNORECASE)
    if not grid:
        grid = re.search(r'(?:grid|is)\s*(\d+)\s*x\s*(\d+)', raw, re.IGNORECASE)
    rows = int(grid.group(1)) if grid else 3
    cols = int(grid.group(2)) if grid else 3

    indices = set()
    for line in raw.split("\n"):
        has_contains = re.search(r'\(Contains', line, re.IGNORECASE)
        if not has_contains:
            continue
        rc = re.search(r'[Rr]ow\s*(\d+)\s*[,;:].*?[Cc]ol(?:umn)?\s*(\d+)', line)
        if rc:
            r, c = int(rc.group(1)) - 1, int(rc.group(2)) - 1
            if 0 <= r < rows and 0 <= c < cols:
                indices.add(r * cols + c)
            continue
        cell = re.search(r'[Cc]ell\s*\((\d+)\s*,\s*(\d+)\)', line)
        if cell:
            r, c = int(cell.group(1)), int(cell.group(2))
            if 0 <= r < rows and 0 <= c < cols:
                indices.add(r * cols + c)

    if indices:
        return {"tiles": sorted(indices), "grid_cols": cols, "grid_rows": rows}
    return None


def _normalize_result(parsed):
    """Приводит любой формат ответа LLM к {"clicks":[{"x":int,"y":int}]}
    или {"tiles":[...], "grid_cols":N, "grid_rows":M}."""
    if not parsed:
        return None
    if isinstance(parsed, dict):
        if "clicks" in parsed:
            return parsed
        if "tiles" in parsed:
            return parsed
    if isinstance(parsed, list):
        clicks = []
        for item in parsed:
            if isinstance(item, dict):
                if "click" in item:
                    val = item["click"]
                    if isinstance(val, (list, tuple)) and len(val) == 2:
                        clicks.append({"x": int(val[0]), "y": int(val[1])})
                elif "x" in item and "y" in item:
                    clicks.append({"x": int(item["x"]), "y": int(item["y"])})
        if clicks:
            return {"clicks": clicks}
    return None


def ask_llm_for_clicks(challenge_text, screenshot_path, bframe_box):
    from PIL import Image
    with open(screenshot_path, "rb") as f:
        image_data = f.read()
        image_b64 = base64.b64encode(image_data).decode("utf-8")
    with Image.open(screenshot_path) as img:
        img_w, img_h = img.size
    payload = {
        "model": "gui-owl",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": f"Image is {img_w}x{img_h}px. Think of it as 1000x1000. Return center coords of each matching object in 0-1000 space."},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
            ]}
        ],
        "temperature": 0.1,
        "max_tokens": 4000,
        "stream": False,
    }
    headers = {}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    try:
        r = requests.post(API_URL, json=payload, headers=headers, timeout=180)
        print(f"[RECAPTCHA STATUS] {r.status_code}")
        r.raise_for_status()
        data = r.json()
        raw = (data["choices"][0]["message"].get("content") or "").strip()
        if not raw:
            raw = (data["choices"][0]["message"].get("reasoning_content") or "").strip()
        print(f"[RECAPTCHA RAW] {raw[:500]}")
        if not raw:
            return None, None

        parsed = _extract_json(raw)
        if parsed:
            normalized = _normalize_result(parsed)
            if normalized:
                print("[RECAPTCHA] Найден JSON с clicks")
                return normalized, None

        print("[RECAPTCHA] JSON без clicks, парсю reasoning...")
        reasoning = _parse_reasoning(raw)
        if reasoning:
            print(f"[RECAPTCHA] Reasoning parsed: tiles={reasoning['tiles']} grid={reasoning['grid_cols']}x{reasoning['grid_rows']}")
            return reasoning, raw

        print("[RECAPTCHA] Невалидный ответ, отправляю на коррекцию...")
        correction = (
            f"Your response was not in the required format. I need JSON with 'clicks' array.\n"
            f"Your response: {raw[:1000]}\n\n"
            f"Return ONLY this exact JSON format:\n"
            f"{{\"clicks\":[{{\"x\":100,\"y\":200}},{{\"x\":300,\"y\":200}}],\"reason\":\"why\"}}"
        )
        payload["messages"].append({"role": "assistant", "content": raw[:1500]})
        payload["messages"].append({"role": "user", "content": correction})
        try:
            r2 = requests.post(API_URL, json=payload, headers=headers, timeout=180)
            print(f"[RECAPTCHA RETRY STATUS] {r2.status_code}")
            r2.raise_for_status()
            raw2 = (r2.json()["choices"][0]["message"].get("content") or "").strip()
            if not raw2:
                raw2 = (r2.json()["choices"][0]["message"].get("reasoning_content") or "").strip()
            print(f"[RECAPTCHA RETRY RAW] {raw2[:500]}")
            if raw2:
                parsed2 = _extract_json(raw2)
                if parsed2:
                    norm2 = _normalize_result(parsed2)
                    if norm2:
                        print("[RECAPTCHA] Коррекция успешна!")
                        return norm2, None
        except Exception as e2:
            print(f"[RECAPTCHA] Ошибка коррекции: {e2}")

        return None, raw
    except Exception as e:
        print(f"[RECAPTCHA] Ошибка: {e}")
        return None, None


def find_challenge_via_screenshot(page, full_screenshot_path):
    page.screenshot(path=full_screenshot_path, type="jpeg", quality=90, full_page=False)
    with open(full_screenshot_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("utf-8")
    viewport = page.viewport_size
    payload = {
        "model": "gui-owl",
        "messages": [
            {"role": "system", "content": "Return ONLY JSON: {\"found\":true} or {\"found\":false}."},
            {"role": "user", "content": [
                {"type": "text", "text": f"reCAPTCHA challenge visible? viewport {viewport['width']}x{viewport['height']}"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
            ]}
        ],
        "temperature": 0.1,
        "max_tokens": 500,
        "stream": False,
    }
    headers = {}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    try:
        r = requests.post(API_URL, json=payload, headers=headers, timeout=180)
        r.raise_for_status()
        raw = r.json()["choices"][0]["message"].get("content") or ""
        parsed = _extract_json(raw)
        return parsed.get("found") if parsed else False
    except Exception:
        return False


def detect_recaptcha_via_vision(page, screenshot_path):
    return find_challenge_via_screenshot(page, screenshot_path)
