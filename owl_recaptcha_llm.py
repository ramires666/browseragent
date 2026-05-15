import json
import base64
import re
from owl_llm_client import get_config, make_request

SYSTEM_PROMPT = """You see a screenshot of a reCAPTCHA image-selection challenge.
The image contains a grid of tiles (usually 3x3 or 4x4).
Find ALL tiles that match the instruction (each whole grid cell that contains the target object).

For each matching tile, you must MEASURE its center pixel in THIS specific image and return it.

COORDINATE SYSTEM:
- Coordinates are ACTUAL IMAGE PIXELS measured from the top-left corner of the image.
- x is the pixel column from the left edge (0 = leftmost column).
- y is the pixel row from the top edge (0 = topmost row).
- Image width and height are given in the user message — your values must fit inside.
- Return the geometric CENTER of each tile, not its corner.

FORMAT RULES:
- x and y MUST be plain integers, NEVER arrays/lists.
- Do NOT normalize — never divide by 1000, never use fractions.
- Do NOT reuse numbers from the schema below — those are placeholders, not real coords.

OUTPUT SHAPE (strict JSON, no markdown, no extra text):
{"clicks":[{"x":<int>,"y":<int>}, ...],"reason":"<short explanation>"}

The <int> placeholders MUST be replaced with the real measured pixel centers
of the matching tiles in the actual image you are looking at. Each tile gets
its own object inside the clicks array. Different tiles have different x AND
different y unless they share a row or column.

If no tiles match: {"clicks":[],"skip":true}
If the challenge appears already solved: {"done":true}"""


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


def _coords_look_valid(normalized, img_w=None, img_h=None):
    """Проверяет что clicks есть и попадают в пределы изображения."""
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
            if fx <= 0 and fy <= 0:
                return False
            if img_w and img_h:
                # Допускаем небольшой выход за края (~5%), но не более чем в 1.5 раза
                if fx < 0 or fy < 0 or fx > img_w * 1.5 or fy > img_h * 1.5:
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

    _model = get_config()["model"]

    def _make_request(current_payload):
        data = make_request(current_payload, tag="RECAPTCHA")
        if not data:
            return None
        raw = (data["choices"][0]["message"].get("content") or "").strip()
        if not raw:
            raw = (data["choices"][0]["message"].get("reasoning_content") or "").strip()
        print(f"[RECAPTCHA RAW] {raw[:500]}")
        return raw

    user_text = (
        f"Image dimensions: {img_w}x{img_h} pixels. "
        f"Return the pixel-center (x,y) of EACH matching tile, "
        f"using absolute pixel coordinates of THIS image "
        f"(0 <= x <= {img_w}, 0 <= y <= {img_h}). "
        f"Instruction: {challenge_text}"
    )

    def _build_payload(correction_msg=None):
        msgs = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
            ]}
        ]
        if correction_msg:
            msgs.append({"role": "user", "content": correction_msg})
        return {
            "model": _model,
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

    if normalized and _coords_look_valid(normalized, img_w, img_h):
        print("[RECAPTCHA] Найден JSON с clicks")
        return normalized, None

    # Коррекция: попытка 2
    print("[RECAPTCHA] Координаты невалидны, отправляю на коррекцию...")
    correction = (
        f"Your response had format issues. "
        f"Coordinates must be plain INTEGER pixel coordinates measured from "
        f"the top-left of the image (valid: 0..{img_w} for x, 0..{img_h} for y). "
        f"Not arrays. Not normalized. Not 0-1000 — actual pixels.\n"
        f"Your response: {raw[:1000]}\n\n"
        f"Return ONLY this JSON shape (replace <int> with your measured values):\n"
        f"{{\"clicks\":[{{\"x\":<int>,\"y\":<int>}}, ...],\"reason\":\"<short>\"}}"
    )
    payload2 = _build_payload(correction)
    raw2 = _make_request(payload2)
    if raw2:
        parsed2 = _extract_json(raw2)
        normalized2 = _normalize_result(parsed2) if parsed2 else None
        if normalized2 and _coords_look_valid(normalized2, img_w, img_h):
            print("[RECAPTCHA] Коррекция успешна!")
            return normalized2, None

    # Попытка 3: сброс, строгий промпт
    print("[RECAPTCHA] Повторная попытка со строгим промптом...")
    strict_prompt = (
        f"Return ONLY this JSON shape (replace <int> with measured values):\n"
        f'{{"clicks":[{{"x":<int>,"y":<int>}}, ...],"reason":"<short>"}}\n\n'
        f"x and y are PLAIN INTEGERS in actual image pixel coordinates.\n"
        f"Image is {img_w}x{img_h}px. Valid range: 0..{img_w} for x, 0..{img_h} for y.\n"
        f"Each click is the CENTER pixel of one matching tile in THIS image.\n"
        f"Do not invent numbers — measure each tile center directly.\n"
        f"Instruction: {challenge_text}"
    )
    payload3 = {
        "model": _model,
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
        if normalized3 and _coords_look_valid(normalized3, img_w, img_h):
            print("[RECAPTCHA] Повторная попытка успешна!")
            return normalized3, None

    # Ничего не сработало
    print("[RECAPTCHA] Все попытки исчерпаны")
    return normalized or normalized2 or normalized3 or None, raw or raw2 or raw3


TILE_INDEX_PROMPT = """You are looking at a reCAPTCHA image-selection challenge.
The grid of tiles has been overlaid with bright YELLOW CIRCLES, each containing a tile NUMBER (1, 2, 3, ...).
The numbers appear in the top-left corner of each tile.

Your task: find ALL numbered tiles whose CONTENT matches the instruction.

Return ONLY a JSON object with the tile numbers, no markdown, no extra text:
{"tiles": [<int>, <int>, ...], "reason": "<one short sentence>"}

Rules:
- Each tile is a separate grid cell. A tile matches if it contains ANY visible part of the target object.
- Use the integer numbers visible inside the yellow circles. Numbers start at 1.
- If no tiles match: {"tiles": [], "skip": true}
- If the challenge looks already solved: {"done": true}
- Never include coordinates, never include strings — only integer tile numbers.
"""


def ask_llm_for_tile_indices(challenge_text, annotated_image_path, num_tiles):
    """Запрашивает у LLM номера тайлов (1..N) с пронумерованной картинки."""
    with open(annotated_image_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("utf-8")

    _model = get_config()["model"]
    user_text = (
        f"Instruction: {challenge_text}\n"
        f"The image has {num_tiles} numbered tiles (1..{num_tiles}).\n"
        f"Return the JSON object with the matching tile numbers."
    )

    def _build_payload(extra_msg=None, temperature=0.1):
        msgs = [
            {"role": "system", "content": TILE_INDEX_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
            ]},
        ]
        if extra_msg:
            msgs.append({"role": "user", "content": extra_msg})
        return {
            "model": _model,
            "messages": msgs,
            "temperature": temperature,
            "max_tokens": 800,
            "stream": False,
        }

    def _run(payload):
        data = make_request(payload, tag="RECAPTCHA-TILES")
        if not data:
            return None
        raw = (data["choices"][0]["message"].get("content") or "").strip()
        if not raw:
            raw = (data["choices"][0]["message"].get("reasoning_content") or "").strip()
        print(f"[RECAPTCHA-TILES RAW] {raw[:500]}")
        return raw

    def _parse_tiles(raw):
        if not raw:
            return None
        parsed = _extract_json(raw)
        if not isinstance(parsed, dict):
            return None
        if parsed.get("done"):
            return {"done": True}
        if parsed.get("skip"):
            return {"skip": True, "tiles": []}
        tiles_raw = parsed.get("tiles")
        if not isinstance(tiles_raw, list):
            return None
        cleaned = []
        for v in tiles_raw:
            try:
                n = int(v)
            except (TypeError, ValueError):
                continue
            if 1 <= n <= num_tiles and n not in cleaned:
                cleaned.append(n)
        return {"tiles": cleaned, "reason": parsed.get("reason", "")}

    raw = _run(_build_payload())
    result = _parse_tiles(raw)
    if result is not None and (result.get("done") or result.get("skip") or result.get("tiles") is not None):
        return result, raw

    correction = (
        f"Your response was not valid. Return ONLY:\n"
        f"{{\"tiles\": [1, 4, 7], \"reason\": \"short\"}}\n"
        f"Replace [1, 4, 7] with the actual matching tile numbers from the image "
        f"(integers in range 1..{num_tiles}). No coords, no strings."
    )
    raw2 = _run(_build_payload(correction, temperature=0.05))
    result2 = _parse_tiles(raw2)
    if result2 is not None:
        return result2, raw2

    return None, raw or raw2


def find_challenge_via_screenshot(page, full_screenshot_path):
    page.screenshot(path=full_screenshot_path, type="jpeg", quality=90, full_page=False)
    with open(full_screenshot_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("utf-8")
    viewport = page.viewport_size
    _model = get_config()["model"]
    payload = {
        "model": _model,
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
    try:
        data = make_request(payload, tag="RECAPTCHA")
        if not data:
            return False
        raw = data["choices"][0]["message"].get("content") or ""
        parsed = _extract_json(raw)
        return parsed.get("found") if parsed else False
    except Exception:
        return False


def detect_recaptcha_via_vision(page, screenshot_path):
    return find_challenge_via_screenshot(page, screenshot_path)
