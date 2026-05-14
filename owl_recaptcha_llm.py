import json
import os
import base64
import requests
import re
from owl_llm import API_URL, API_KEY, _ask_model_to_fix_json

RECAPTCHA_SYSTEM_PROMPT = """
You are solving a reCAPTCHA image challenge. Look at the screenshot: it shows a grid of square images and a challenge instruction at the top.

IMPORTANT: Output ONLY raw JSON. Do NOT include thinking, reasoning, explanations, or any text before or after the JSON.

Your job:
1. Read the challenge instruction (e.g. "select all squares with traffic lights" or "select all images with a fire hydrant")
2. Look at EVERY image in the grid carefully
3. Determine the EXACT pixel boundaries of the grid and each tile within it

The screenshot includes the FULL challenge area. Tiles are indexed row-by-row, left-to-right, top-to-bottom. Grid can be 3x3, 4x4, 3x4, or 4x3.

Return EXACTLY this JSON format with PRECISE screenshot pixel coordinates:
{
  "tiles": [0, 3, 6],
  "grid": {
    "x": 10,
    "y": 60,
    "cell_w": 120,
    "cell_h": 120,
    "cols": 3,
    "rows": 3
  },
  "reason": "top-left, middle-left, bottom-left contain the target"
}

- "grid.x" = left edge of the first tile in screenshot pixels
- "grid.y" = top edge of the first tile in screenshot pixels (AFTER the instruction text)
- "grid.cell_w" = width of one tile in pixels
- "grid.cell_h" = height of one tile in pixels
- "grid.cols" / "grid.rows" = grid dimensions

CRITICAL RULES FOR SPLIT-OBJECT CHALLENGES:
- The target object may be SPLIT across multiple adjacent tiles (like a puzzle).
- Select EVERY tile that contains ANY PART of the target object — even a small corner, edge, or fragment.
- Look at the BORDERS of each tile carefully: if part of the object is cut off at the edge of a tile,
  the adjacent tile probably also contains a fragment.
- Example: one bus might be spread across 4 tiles — ALL 4 must be selected.
- Missing a tile that contains a small piece of the object = FAIL.
- If no tiles match: {"tiles":[],"skip":true,"reason":"none match"}
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
            close = json_part.rfind("}")
            if close >= 0:
                json_part = json_part[:close + 1]
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

        print(f"[{label}] Пытаюсь _ask_model_to_fix_json...")
        fixed = _ask_model_to_fix_json(raw)
        if fixed:
            try:
                return json.loads(fixed)
            except json.JSONDecodeError:
                pass

        print(f"[{label}] Парсю reasoning text для (Contains ...) маркеров...")
        parsed = _parse_captcha_reasoning(raw)
        if parsed:
            return parsed

        return None
    except Exception as e:
        print(f"[{label}] Ошибка запроса: {e}")
        return None


def _parse_captcha_reasoning(text):
    """Парсит reasoning-текст модели: ищет Row/Col (Contains ...) маркеры.
    Пример: 'Row 1, Col 1: A bicycle leaning. (Contains bicycle)'
    Возвращает: {"tiles": [0, 4, 6], "grid": {"cols": 3, "rows": 3, ...}}
    или None при неудаче."""
    import re

    grid_match = re.search(r'(\d+)\s*x\s*(\d+)\s*grid', text, re.IGNORECASE)
    if grid_match:
        rows, cols = int(grid_match.group(1)), int(grid_match.group(2))
    else:
        grid_match = re.search(r'(\d+)\s*rows?\s*(?:and\s*)?(\d+)\s*cols?', text, re.IGNORECASE)
        if grid_match:
            rows, cols = int(grid_match.group(1)), int(grid_match.group(2))
        else:
            rows, cols = 3, 3

    contains_indices = set()
    lines = text.split('\n')
    for line in lines:
        match = re.search(r'[Rr]ow\s*(\d+)\s*[;,:]\s*[Cc]ol(?:umn)?\s*(\d+)', line)
        if not match:
            match = re.search(r'[Rr]ow\s*(\d+)\s*[;,:].*?[Cc]ol(?:umn)?\s*(\d+)', line)
        if match:
            r, c = int(match.group(1)) - 1, int(match.group(2)) - 1
            if 0 <= r < rows and 0 <= c < cols and re.search(r'\(Contains', line, re.IGNORECASE):
                idx = r * cols + c
                contains_indices.add(idx)

    if contains_indices:
        result = {"tiles": sorted(list(contains_indices)), "grid": {"cols": cols, "rows": rows}}
        print(f"[RECAPTCHA PARSED] из reasoning: tiles={result['tiles']} grid={cols}x{rows}")
        return result

    contains_indices = set()
    for line in lines:
        if re.search(r'\(Contains', line, re.IGNORECASE):
            col_match = re.search(r'[Cc]ol(?:umn)?\s*(\d+)', line)
            row_match = re.search(r'[Rr]ow\s*(\d+)', line)
            if col_match and row_match:
                r, c = int(row_match.group(1)) - 1, int(col_match.group(2)) - 1
                if 0 <= r < rows and 0 <= c < cols:
                    idx = r * cols + c
                    contains_indices.add(idx)

    if contains_indices:
        result = {"tiles": sorted(list(contains_indices)), "grid": {"cols": cols, "rows": rows}}
        print(f"[RECAPTCHA PARSED] из reasoning: tiles={result['tiles']} grid={cols}x{rows}")
        return result

    return None


def ask_llm_for_clicks(challenge_text, screenshot_path, bframe_box):
    with open(screenshot_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("utf-8")

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
                            f"Challenge: \"{challenge_text}\"\n\n"
                            "Return tile indices AND grid pixel boundaries in this screenshot."
                        )
                    },
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
                ]
            }
        ],
        "temperature": 0.1,
        "max_tokens": 4000,
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
        "max_tokens": 4000,
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
