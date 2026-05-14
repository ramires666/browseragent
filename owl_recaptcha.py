import json
import time
import random
import base64
import requests
from owl_llm import API_URL, API_KEY
from owl_clicker import click_human_like

RECAPTCHA_SCREENSHOT_PATH = r"W:\_python\OWL\_recaptcha_challenge.jpg"

RECAPTCHA_SYSTEM_PROMPT = """
You are solving a reCAPTCHA image challenge. Look at the screenshot: it shows a grid of square images and a challenge instruction at the top.

Your job:
1. Read the challenge instruction (e.g. "select all squares with traffic lights")
2. Look at EVERY image in the grid carefully
3. Return the (x, y) pixel center of EVERY image that matches

The screenshot size (width x height) is provided below. Coordinates are in screenshot pixels, (0,0) = top-left.

Return exactly this JSON format:
{"clicks":[{"x":100,"y":80},{"x":280,"y":80},{"x":100,"y":200}],"reason":"these 3 contain traffic lights"}

Rules:
- CRITICAL: Find ALL matching images, not just one. Each matching image = one click.
- Clicking a wrong image = FAIL. Missing a correct image = FAIL.
- Click COORDINATES must be DISTINCT for each image (different x,y each time).
- Coordinates should be the CENTER of each image tile.
- If no images match: {"clicks":[],"skip":true,"reason":"none match"}
- If green checkmark visible (already solved): {"done":true}
- If tiles are unclear/blurry: {"skip":true}
- Return ONLY valid JSON.
"""


def _random_delay(min_s=0.15, max_s=0.5):
    time.sleep(random.uniform(min_s, max_s))


def _find_anchor_frame(page):
    for frame in page.frames:
        if "recaptcha/api2/anchor" in frame.url.lower():
            return frame
    return None


def _find_bframe(page):
    for frame in page.frames:
        if "recaptcha/api2/bframe" in frame.url.lower():
            return frame
    return None


def _click_checkbox(page):
    """Находит и кликает чекбокс 'Я не робот' (anchor iframe) через pyautogui."""
    anchor = _find_anchor_frame(page)
    if not anchor:
        return False
    try:
        box = anchor.frame_element().bounding_box()
        if not box:
            return False
        vx = int(box["x"] + box["width"] / 2)
        vy = int(box["y"] + box["height"] / 2)
        print(f"[RECAPTCHA] Клик 'Я не робот' через pyautogui (viewport {vx}, {vy})")
        click_human_like(page, vx, vy)
        return True
    except Exception as e:
        print(f"[RECAPTCHA] Ошибка клика чекбокса: {e}")
        return False


def get_challenge_text(page):
    bframe = _find_bframe(page)
    if not bframe:
        return None
    try:
        return bframe.evaluate("""() => {
            const el = document.querySelector('.rc-imageselect-instructions') ||
                       document.querySelector('.rc-imageselect-desc-wrapper') ||
                       document.querySelector('[class*="instruction"]');
            return el ? el.innerText.trim() : '';
        }""")
    except Exception as e:
        print(f"[RECAPTCHA] get_challenge_text error: {e}")
        return None


def _get_bframe_box(page):
    bframe = _find_bframe(page)
    if not bframe:
        return None
    try:
        return bframe.frame_element().bounding_box()
    except Exception:
        return None


def _get_verify_button(page):
    bframe = _find_bframe(page)
    if not bframe:
        return None
    try:
        iframe_box = bframe.frame_element().bounding_box()
        if not iframe_box:
            return None
        btn = bframe.evaluate("""() => {
            const el = document.querySelector('.rc-imageselect-verify button, button[class*="verify"], #recaptcha-verify-button');
            if (!el) return null;
            const r = el.getBoundingClientRect();
            return { x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2) };
        }""")
        if btn:
            return (int(iframe_box["x"] + btn["x"]), int(iframe_box["y"] + btn["y"]))
        return None
    except Exception:
        return None


def _get_skip_button(page):
    bframe = _find_bframe(page)
    if not bframe:
        return None
    try:
        iframe_box = bframe.frame_element().bounding_box()
        if not iframe_box:
            return None
        btn = bframe.evaluate("""() => {
            const el = document.querySelector('.rc-imageselect-reload button, a[class*="refresh"], button[class*="refresh"]');
            if (!el) return null;
            const r = el.getBoundingClientRect();
            return { x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2) };
        }""")
        if btn:
            return (int(iframe_box["x"] + btn["x"]), int(iframe_box["y"] + btn["y"]))
        return None
    except Exception:
        return None


def _dedup_coords(clicks, threshold=25):
    """Убирает дубликаты координат (ближе threshold px друг к другу)."""
    unique = []
    for c in clicks:
        cx, cy = c.get("x", 0), c.get("y", 0)
        dup = False
        for u in unique:
            ux, uy = u["x"], u["y"]
            if abs(cx - ux) <= threshold and abs(cy - uy) <= threshold:
                dup = True
                break
        if not dup:
            unique.append(c)
    return unique


def _ask_llm_for_clicks(challenge_text, screenshot_path, bframe_box):
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
        "max_tokens": 800,
        "stream": False,
    }

    headers = {}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"

    r = requests.post(API_URL, json=payload, headers=headers, timeout=180)
    r.raise_for_status()
    raw = r.json()["choices"][0]["message"]["content"]
    print(f"[RECAPTCHA LLM RAW] {raw}")

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print(f"[RECAPTCHA] LLM вернула невалидный JSON: {raw}")
        return None


def _is_solved(page):
    bframe = _find_bframe(page)
    if not bframe:
        return False
    try:
        return bframe.evaluate("""() => {
            const el = document.querySelector('.rc-imageselect-incorrect-response, .rc-imageselect-error');
            if (el && el.style.display !== 'none') return false;
            const check = document.querySelector('.rc-imageselect-checkmark');
            if (check) return true;
            return document.querySelector('.rc-imageselect-payload[style*="display: none"]') !== null;
        }""")
    except Exception:
        return False


def has_recaptcha_on_page(page):
    """Проверяет, есть ли на странице reCAPTCHA (чекбокс или challenge)."""
    return _find_anchor_frame(page) is not None or _find_bframe(page) is not None


def is_recaptcha_challenge(page):
    """Проверяет challenge (bframe). Если есть только чекбокс — кликает его сначала."""
    anchor = _find_anchor_frame(page)
    bframe = _find_bframe(page)

    if bframe:
        try:
            box = bframe.frame_element().bounding_box()
            if box and box["width"] >= 100 and box["height"] >= 100:
                return True
        except Exception:
            pass

    if anchor and not bframe:
        print("[RECAPTCHA] Найден чекбокс 'Я не робот'. Кликаю...")
        _click_checkbox(page)
        print("[RECAPTCHA] Жду 2с появления challenge...")
        time.sleep(2)

        bframe = _find_bframe(page)
        if bframe:
            try:
                box = bframe.frame_element().bounding_box()
                if box and box["width"] >= 100 and box["height"] >= 100:
                    print("[RECAPTCHA] Challenge появился после клика по чекбоксу")
                    return True
            except Exception:
                pass
        print("[RECAPTCHA] Challenge не появился после клика по чекбоксу")
        return False

    return False


def solve(page, max_rounds=5):
    """Разгадывает reCAPTCHA challenge через LLM vision + pyautogui."""
    print("[RECAPTCHA] === НАЧАЛО РАЗГАДЫВАНИЯ ===")

    for round_idx in range(max_rounds):
        print(f"\n[RECAPTCHA] Раунд {round_idx + 1}/{max_rounds}")

        if _is_solved(page):
            print("[RECAPTCHA] Уже решено!")
            return True

        challenge_text = get_challenge_text(page)
        print(f"[RECAPTCHA] Текст задания: {challenge_text}")

        if not challenge_text:
            print("[RECAPTCHA] Не удалось получить текст задания, жду 1с...")
            time.sleep(1)
            continue

        bframe_box = _get_bframe_box(page)
        if not bframe_box:
            print("[RECAPTCHA] bframe не найден")
            return False

        bframe = _find_bframe(page)
        iframe_el = bframe.frame_element()
        iframe_el.screenshot(path=RECAPTCHA_SCREENSHOT_PATH, type="jpeg", quality=95)

        result = _ask_llm_for_clicks(challenge_text, RECAPTCHA_SCREENSHOT_PATH, bframe_box)
        if not result:
            _random_delay(0.5, 1)
            continue

        if result.get("done"):
            print("[RECAPTCHA] LLM сообщила что уже решено")
            return True

        if result.get("skip"):
            print("[RECAPTCHA] LLM хочет пропустить (новые картинки)")
            skip_btn = _get_skip_button(page)
            if skip_btn:
                click_human_like(page, skip_btn[0], skip_btn[1])
            _random_delay(0.5, 1)
            continue

        raw_clicks = result.get("clicks", [])
        if not raw_clicks:
            print("[RECAPTCHA] LLM не выбрала ни одной точки. Пропускаю.")
            skip_btn = _get_skip_button(page)
            if skip_btn:
                click_human_like(page, skip_btn[0], skip_btn[1])
            _random_delay(0.5, 1)
            continue

        clicks_data = _dedup_coords(raw_clicks, threshold=20)
        print(f"[RECAPTCHA] Координат до дедупликации: {len(raw_clicks)}, после: {len(clicks_data)}")

        for pt in clicks_data:
            sx = pt.get("x", 0)
            sy = pt.get("y", 0)
            vx = int(bframe_box["x"] + sx)
            vy = int(bframe_box["y"] + sy)
            print(f"[RECAPTCHA] Клик по картинке в ({vx}, {vy}) viewport")
            click_human_like(page, vx, vy)
            _random_delay(0.25, 0.7)

        verify_btn = _get_verify_button(page)
        if verify_btn:
            print(f"[RECAPTCHA] Кнопка 'Проверить' в ({verify_btn[0]}, {verify_btn[1]}) viewport")
            _random_delay(0.4, 0.8)
            click_human_like(page, verify_btn[0], verify_btn[1])
        else:
            print("[RECAPTCHA] Кнопка 'Проверить' не найдена")

        time.sleep(2.5)

        if _is_solved(page):
            print("[RECAPTCHA] РЕШЕНО!")
            return True

        incorrect = False
        try:
            bf = _find_bframe(page)
            if bf:
                incorrect = bf.evaluate("""() => {
                    const el = document.querySelector('.rc-imageselect-incorrect-response');
                    return el && el.style.display !== 'none';
                }""")
        except Exception:
            pass
        if incorrect:
            print("[RECAPTCHA] Неправильный ответ, пробую снова...")
            _random_delay(0.8, 1.5)
            continue

    print("[RECAPTCHA] Достигнут лимит попыток")
    return False
