import json
import base64
import requests
from owl_llm import API_URL, API_KEY

SYSTEM_PROMPT = """Return ONLY raw JSON. No thinking, no explanations.

Look at the reCAPTCHA screenshot. Find ALL tiles that match the instruction.
Return the (x,y) pixel center of each matching tile.

FORMAT (copy exactly):
{"clicks":[{"x":100,"y":200},{"x":300,"y":200}],"reason":"why"}

Rules:
- Coordinates in screenshot pixels, (0,0) = top-left
- Every tile that matches = one entry in clicks
- If no matches: {"clicks":[],"skip":true}
- If already solved: {"done":true}
- Return ONLY the JSON, nothing before or after"""


def _llm_request(payload):
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
        print(f"[RECAPTCHA RAW] {raw[:300]}")
        if not raw:
            return None
        brace = raw.find("{")
        if brace >= 0:
            raw = raw[brace:]
        close = raw.rfind("}")
        if close >= 0:
            raw = raw[:close + 1]
        result = json.loads(raw)
        if not isinstance(result, dict) or "clicks" not in result:
            print(f"[RECAPTCHA] Нет поля 'clicks' в ответе")
            return None
        return result
    except Exception as e:
        print(f"[RECAPTCHA] Ошибка: {e}")
        return None


def ask_llm_for_clicks(challenge_text, screenshot_path, bframe_box):
    with open(screenshot_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("utf-8")
    payload = {
        "model": "gui-owl",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": "Return pixel coordinates of matching tiles."},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
            ]}
        ],
        "temperature": 0.1,
        "max_tokens": 4000,
        "stream": False,
    }
    result = _llm_request(payload)
    if not result:
        print("[RECAPTCHA] Невалидный ответ, пробую ещё раз с correction...")
        payload["messages"].append({"role": "assistant", "content": json.dumps(result) if result else "wrong format"})
        payload["messages"].append({"role": "user", "content": "Return ONLY {\"clicks\":[{\"x\":100,\"y\":200}],\"reason\":\"...\"}"})
        result = _llm_request(payload)
    return result


def find_challenge_via_screenshot(page, full_screenshot_path):
    page.screenshot(path=full_screenshot_path, type="jpeg", quality=90, full_page=False)
    with open(full_screenshot_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("utf-8")
    viewport = page.viewport_size
    payload = {
        "model": "gui-owl",
        "messages": [
            {"role": "system", "content": "Return ONLY JSON: {\"found\":true} or {\"found\":false}. No text."},
            {"role": "user", "content": [
                {"type": "text", "text": f"Is there a reCAPTCHA challenge grid visible in this {viewport['width']}x{viewport['height']} screenshot?"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
            ]}
        ],
        "temperature": 0.1,
        "max_tokens": 500,
        "stream": False,
    }
    result = _llm_request(payload)
    return result and result.get("found")


def detect_recaptcha_via_vision(page, screenshot_path):
    result = find_challenge_via_screenshot(page, screenshot_path)
    if result:
        print("[RECAPTCHA VISION] challenge найден!")
        return True
    print("[RECAPTCHA VISION] challenge не найден")
    return False
