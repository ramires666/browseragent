import json
import os
import base64
import requests
from owl_llm import API_URL, API_KEY

RECAPTCHA_SYSTEM_PROMPT = """
You are solving a reCAPTCHA image challenge. Look at the screenshot: it shows a grid of square images and a challenge instruction at the top.

IMPORTANT: Output ONLY raw JSON. Do NOT include thinking, reasoning, explanations, or any text before or after the JSON.

Your job:
1. Read the challenge instruction (e.g. "select all squares with traffic lights" or "select all images with a fire hydrant")
2. Look at EVERY image in the grid carefully
3. Return the (x, y) pixel center of EVERY tile that matches

The screenshot size (width x height) is provided below. Coordinates are in screenshot pixels, (0,0) = top-left.

Return exactly this JSON format:
{"clicks":[{"x":100,"y":80},{"x":280,"y":80},{"x":100,"y":200}],"reason":"these 3 contain fire hydrant"}

CRITICAL RULES FOR SPLIT-OBJECT CHALLENGES:
- The target object may be SPLIT across multiple adjacent tiles (like a puzzle).
- Select EVERY tile that contains ANY PART of the target object — even a small corner, edge, or fragment.
- Look at the BORDERS of each tile carefully: if part of the object is cut off at the edge of a tile,
  the adjacent tile probably also contains a fragment.
- Example: one bus might be spread across 4 tiles — ALL 4 must be selected.
- Missing a tile that contains a small piece of the object = FAIL.
- Click COORDINATES must be DISTINCT for each tile (different x,y each time).
- If no tiles match: {"clicks":[],"skip":true,"reason":"none match"}
- If green checkmark visible (already solved): {"done":true}
- If tiles are unclear/blurry: {"skip":true}
- Return ONLY valid JSON.
"""

FIND_CHALLENGE_PROMPT = """
You are looking at a screenshot of a webpage that may have a reCAPTCHA challenge visible.

IMPORTANT: Output ONLY raw JSON. Do NOT include thinking, reasoning, or any text before or after the JSON.

The reCAPTCHA challenge looks like a grid of images (3x3 or 4x4) with a text instruction at the top
like "Select all squares with traffic lights" or similar. There is also a "Verify" or "Skip" button.

Look carefully at the screenshot and determine:
1. Is a reCAPTCHA image challenge currently visible? (a grid of thumbnails to click)
2. If YES — return the viewport pixel coordinates of the CENTER of each image tile.
3. If NO — return {"found": false}

Return exactly this JSON format for YES:
{"found":true,"clicks":[{"x":100,"y":200},{"x":300,"y":200},{"x":100,"y":350}],"reason":"these 3 have traffic lights"}

Return for NO:
{"found":false,"reason":"no challenge grid visible"}

Coordinates are in viewport pixels, (0,0) = top-left of the visible browser window.
"""


def _repair_json(text):
    stripped = text.strip()
    if not stripped:
        return stripped
    try:
        json.loads(stripped)
        return stripped
    except json.JSONDecodeError:
        pass
    if stripped.count("{") > stripped.count("}"):
        stripped += "}"
    if stripped.count("[") > stripped.count("]"):
        stripped += "]"
    try:
        json.loads(stripped)
        return stripped
    except json.JSONDecodeError:
        pass
    idx = stripped.rfind(':"')
    if idx > 0 and not stripped.endswith('"}'):
        stripped += '"}'
    try:
        json.loads(stripped)
        return stripped
    except json.JSONDecodeError:
        pass
    return text


def _extract_message(raw_json, label):
    msg = raw_json["choices"][0]["message"]
    content = (msg.get("content") or "").strip()
    if not content:
        content = (msg.get("reasoning_content") or "").strip()
    print(f"[{label} RAW] {content[:500]}")
    return content


def _llm_request(payload, label):
    headers = {}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    try:
        r = requests.post(API_URL, json=payload, headers=headers, timeout=180)
        print(f"[{label} STATUS] {r.status_code}")
        r.raise_for_status()
        data = r.json()
        raw = _extract_message(data, label)
        if not raw:
            print(f"[{label}] Пустой ответ модели")
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        repaired = _repair_json(raw)
        if repaired != raw:
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                pass

        brace_start = raw.find("{")
        if brace_start >= 0:
            json_part = raw[brace_start:]
            try:
                return json.loads(json_part)
            except json.JSONDecodeError:
                repaired2 = _repair_json(json_part)
                if repaired2 != json_part:
                    try:
                        return json.loads(repaired2)
                    except json.JSONDecodeError:
                        pass

        print(f"[{label}] Невалидный JSON: {raw[:300]}")
        print(f"[{label}] Полный ответ ({len(raw)} символов), ищу JSON...")
        import re
        json_matches = re.findall(r'\{[^{}]*\}', raw, re.DOTALL)
        for m in json_matches:
            try:
                return json.loads(m)
            except json.JSONDecodeError:
                pass
        longer = re.findall(r'\{(?:[^{}]|(?:\{[^{}]*\}))*\}', raw, re.DOTALL)
        for m in longer:
            try:
                return json.loads(m)
            except json.JSONDecodeError:
                pass

        return None
    except Exception as e:
        print(f"[{label}] Ошибка запроса: {e}")
        return None


def ask_llm_for_clicks(challenge_text, screenshot_path, bframe_box):
    with open(screenshot_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("utf-8")

    w = int(bframe_box["width"])
    h = int(bframe_box["height"])

    payload = {
        "model": "gui-owl",
        "messages": [
            {"role": "system", "content": RECAPTCHA_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Screenshot size: {w}x{h} pixels.\n"
                            f"Challenge: \"{challenge_text}\"\n\n"
                            "List the (x,y) center of EVERY matching tile. "
                            "Each tile must have DIFFERENT coordinates."
                        )
                    },
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
                ]
            }
        ],
        "temperature": 0.1,
        "max_tokens": 2500,
        "stream": False,
    }

    return _llm_request(payload, "RECAPTCHA CHALLENGE")


def find_challenge_via_screenshot(page, full_screenshot_path):
    page.screenshot(path=full_screenshot_path, type="jpeg", quality=90, full_page=False)

    with open(full_screenshot_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("utf-8")

    viewport = page.viewport_size
    w, h = viewport["width"], viewport["height"]

    payload = {
        "model": "gui-owl",
        "messages": [
            {"role": "system", "content": FIND_CHALLENGE_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Viewport size: {w}x{h}. Is there a reCAPTCHA challenge here?"},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
                ]
            }
        ],
        "temperature": 0.1,
        "max_tokens": 2500,
        "stream": False,
    }

    return _llm_request(payload, "RECAPTCHA VISION")


def detect_recaptcha_via_vision(page, screenshot_path):
    result = find_challenge_via_screenshot(page, screenshot_path)
    if result and result.get("found"):
        print("[RECAPTCHA VISION] challenge найден через скриншот!")
        return True
    print("[RECAPTCHA VISION] challenge не найден через скриншот")
    return False
