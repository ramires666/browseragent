import json
import base64
import re
import requests
from owl_llm import API_URL, API_KEY

SYSTEM_PROMPT = """Look at the screenshot. Find ALL objects that match the instruction.
Return pixel coordinates (x,y) center of each matching object.

IMPORTANT:
- Use a 1000x1000 coordinate system. Think of the image as 1000x1000.
- x and y MUST be plain numbers, NEVER arrays/lists.
- {"x": 500, "y": 300} — CORRECT
- {"x": [500, 300]} — WRONG, never do this

FORMAT (strict JSON, no markdown, no extra text):
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


def _fix_single_click(pt):
    """Приводит один click-объект к {"x":float,"y":float} из любых форматов."""
    if not isinstance(pt, dict):
        return None
    x_val = pt.get("x")
    y_val = pt.get("y")

    # {"x": [839, 117], "y": ...} — x содержит [x, y]
    if isinstance(x_val, (list, tuple)):
        if len(x_val) >= 2:
            if not y_val or y_val in (0, [0], "0"):
                x_val, y_val = x_val[0], x_val[1]
            else:
                x_val = sum(x_val) / len(x_val)
        elif len(x_val) == 1:
            x_val = x_val[0]
        else:
            x_val = 0

    if isinstance(y_val, (list, tuple)):
        if len(y_val) >= 1:
            y_val = sum(y_val) / len(y_val)
        else:
            y_val = 0

    try:
        fx = float(x_val) if x_val is not None else None
        fy = float(y_val) if y_val is not None else None
    except (TypeError, ValueError):
        return None

    if fx is not None and fy is not None:
        return {"x": fx, "y": fy}
    return None


def _normalize_result(parsed):
    """
    Универсальный парсер. Приводит ЛЮБОЙ формат к
    {"clicks":[{"x":float,"y":float}], ...}
    или {"tiles":[...], "grid_cols":N, "grid_rows":M}.
    """
    if not parsed:
        return None

    # Массив-обёртка [{"clicks":[...]}] -> {"clicks":[...]}
    if isinstance(parsed, list) and len(parsed) == 1 and isinstance(parsed[0], dict):
        inner = parsed[0]
        if "clicks" in inner or "tiles" in inner:
            parsed = inner

    if isinstance(parsed, dict):
        result = dict(parsed)

        if "clicks" in result:
            raw_clicks = result["clicks"]
            if isinstance(raw_clicks, list):
                fixed = [_fix_single_click(pt) for pt in raw_clicks if isinstance(pt, dict)]
                fixed = [c for c in fixed if c is not None]
                if fixed:
                    result["clicks"] = fixed
                    return result
            # clicks пустой или все битые
            return result

        if "tiles" in result:
            return result

        # {"x":..., "y":...} без "clicks"
        item = _fix_single_click(result)
        if item:
            rest = {k: v for k, v in result.items() if k not in ("x", "y")}
            return {"clicks": [item], **rest}

        # {"click": [x, y], ...}
        if "click" in result:
            val = result["click"]
            if isinstance(val, (list, tuple)) and len(val) == 2:
                rest = {k: v for k, v in result.items() if k != "click"}
                return {"clicks": [{"x": float(val[0]), "y": float(val[1])}], **rest}

    if isinstance(parsed, list):
        clicks = []
        for item in parsed:
            if isinstance(item, dict):
                pt = _fix_single_click(item)
                if pt:
                    clicks.append(pt)
                elif "click" in item:
                    val = item["click"]
                    if isinstance(val, (list, tuple)) and len(val) == 2:
                        clicks.append({"x": float(val[0]), "y": float(val[1])})
        if clicks:
            return {"clicks": clicks}

    return None


def _coords_look_valid(normalized, bframe_box=None):
    """Проверяет что clicks есть и не выглядят мусором."""
    if not normalized:
        return False
    if normalized.get("done") or normalized.get("skip"):
        return True
    if "clicks" in normalized:
        clicks = normalized["clicks"]
        if not clicks or not isinstance(clicks, list):
            return False
        for pt in clicks:
            if not isinstance(pt, dict):
                return False
            x, y = pt.get("x"), pt.get("y")
            if x is None or y is None:
                return False
            try:
                fx, fy = float(x), float(y)
            except (TypeError, ValueError):
                return False
            # В 0-1000 пространстве — должны быть > 0 и <= 1000
            if fx <= 0 and fy <= 0:
                return False
        return True
    if "tiles" in normalized:
        return bool(normalized["tiles"])
    return False


def ask_llm_for_clicks(challenge_text, screenshot_path, bframe_box):
    from PIL import Image
    with open(screenshot_path, "rb") as f:
        image_data = f.read()
        image_b64 = base64.b64encode(image_data).decode("utf-8")
    with Image.open(screenshot_path) as img:
        img_w, img_h = img.size

    headers = {}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"

    def _make_request(current_payload):
        try:
            r = requests.post(API_URL, json=current_payload, headers=headers, timeout=180)
            print(f"[RECAPTCHA STATUS] {r.status_code}")
            r.raise_for_status()
            data = r.json()
            raw = (data["choices"][0]["message"].get("content") or "").strip()
            if not raw:
                raw = (data["choices"][0]["message"].get("reasoning_content") or "").strip()
            print(f"[RECAPTCHA RAW] {raw[:500]}")
            return raw
        except Exception as e:
            print(f"[RECAPTCHA] Ошибка запроса: {e}")
            return None

    def _build_payload(correction_msg=None):
        msgs = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": f"Image is {img_w}x{img_h}px. Think of it as 1000x1000. Return center coords of each matching object in 0-1000 space."},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
            ]}
        ]
        if correction_msg:
            msgs.append({"role": "user", "content": correction_msg})
        return {
            "model": "gui-owl",
            "messages": msgs,
            "temperature": 0.1,
            "max_tokens": 4000,
            "stream": False,
        }

    # Попытка 1: чистый запрос
    payload = _build_payload()
    raw = _make_request(payload)
    if not raw:
        return None, None

    parsed = _extract_json(raw)
    normalized = _normalize_result(parsed) if parsed else None

    if normalized and _coords_look_valid(normalized, bframe_box):
        print("[RECAPTCHA] Найден JSON с clicks")
        return normalized, None

    # Коррекция: попытка 2
    print("[RECAPTCHA] Координаты невалидны, отправляю на коррекцию...")
    correction = (
        f"Your response had format issues. Coordinates must be plain numbers (0-1000), not arrays.\n"
        f"Your response: {raw[:1000]}\n\n"
        f"Return ONLY this exact JSON:\n"
        f"{{\"clicks\":[{{\"x\":200,\"y\":300}},{{\"x\":500,\"y\":700}}],\"reason\":\"why\"}}"
    )
    payload2 = _build_payload(correction)
    raw2 = _make_request(payload2)
    if raw2:
        parsed2 = _extract_json(raw2)
        normalized2 = _normalize_result(parsed2) if parsed2 else None
        if normalized2 and _coords_look_valid(normalized2, bframe_box):
            print("[RECAPTCHA] Коррекция успешна!")
            return normalized2, None

    # Попытка 3: сброс, строгий промпт
    print("[RECAPTCHA] Повторная попытка со строгим промптом...")
    strict_prompt = (
        f"You MUST return ONLY this exact JSON format with no extra text:\n"
        f'{{"clicks":[{{"x":200,"y":300}},{{"x":500,"y":700}}],"reason":"why"}}\n\n'
        f"IMPORTANT: x and y are numbers, NOT arrays. Example: {{\"x\":200,\"y\":300}} NOT {{\"x\":[200,300]}}.\n"
        f"Image is {img_w}x{img_h}px. Use 0-1000 coordinate space."
    )
    payload3 = {
        "model": "gui-owl",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": strict_prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
            ]}
        ],
        "temperature": 0.05,
        "max_tokens": 4000,
        "stream": False,
    }
    raw3 = _make_request(payload3)
    if raw3:
        parsed3 = _extract_json(raw3)
        normalized3 = _normalize_result(parsed3) if parsed3 else None
        if normalized3 and _coords_look_valid(normalized3, bframe_box):
            print("[RECAPTCHA] Повторная попытка успешна!")
            return normalized3, None

    # Ничего не сработало
    print("[RECAPTCHA] Все попытки исчерпаны")
    return normalized or normalized2 or normalized3 or None, raw or raw2 or raw3


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
