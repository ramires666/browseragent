import json
import time
import base64
import requests
from owl_llm import API_URL, API_KEY
from owl_clicker import click_fallback

RECAPTCHA_SCREENSHOT_PATH = r"W:\_python\OWL\_recaptcha_challenge.jpg"

RECAPTCHA_SYSTEM_PROMPT = """
You are solving a reCAPTCHA image challenge.

You receive:
1. Challenge instruction text (e.g. "Select all squares with traffic lights")
2. A screenshot of the image grid

Identify which tiles match the challenge description.

Return exactly one JSON object:
{
  "tiles": [0, 3, 5],
  "reason": "these contain traffic lights"
}

Rules:
- Tile index 0 = top-left. Read left-to-right, top-to-bottom.
- If no tiles match the instruction, return {"tiles": [], "reason": "none match"}
- If the challenge is already solved (green checkmark visible), return {"done": true}
- If the images are unclear and you need new ones, return {"skip": true}
- Return ONLY valid JSON, no extra text.
"""


def _find_bframe(page):
    for frame in page.frames:
        if "recaptcha/api2/bframe" in frame.url.lower():
            return frame
    return None


def _find_anchor_frame(page):
    for frame in page.frames:
        if "recaptcha/api2/anchor" in frame.url.lower():
            return frame
    return None


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


def _get_tiles(page):
    bframe = _find_bframe(page)
    if not bframe:
        return None
    try:
        iframe_box = bframe.frame_element().bounding_box()
        if not iframe_box:
            return None
        tiles_rel = bframe.evaluate("""() => {
            const cells = document.querySelectorAll('.rc-imageselect-tile, td[class*="tile"], td.rc-imageselect-tile');
            if (!cells.length) return [];
            return Array.from(cells).map((el, i) => {
                const r = el.getBoundingClientRect();
                return { index: i, x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2) };
            });
        }""")
        if not tiles_rel:
            return None
        tiles = []
        for t in tiles_rel:
            tiles.append({
                "index": t["index"],
                "x": int(iframe_box["x"] + t["x"]),
                "y": int(iframe_box["y"] + t["y"]),
            })
        return tiles
    except Exception as e:
        print(f"[RECAPTCHA] get_tiles error: {e}")
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


def _ask_llm_for_tiles(challenge_text, screenshot_path):
    with open(screenshot_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("utf-8")

    payload = {
        "model": "gui-owl",
        "messages": [
            {"role": "system", "content": RECAPTCHA_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Challenge: {challenge_text}\nWhich tiles match?"},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
                ]
            }
        ],
        "temperature": 0.1,
        "max_tokens": 300,
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


def is_recaptcha_challenge(page):
    """Проверяет, висит ли сейчас reCAPTCHA challenge (bframe с видимыми плитками)."""
    bframe = _find_bframe(page)
    if not bframe:
        return False
    try:
        iframe_el = bframe.frame_element()
        box = iframe_el.bounding_box()
        if not box or box["width"] < 100 or box["height"] < 100:
            return False
        return True
    except Exception:
        return False


def solve(page, max_rounds=5):
    """Разгадывает reCAPTCHA challenge. Возвращает True если успешно."""
    print("[RECAPTCHA] === НАЧАЛО РАЗГАДЫВАНИЯ ===")

    for round_idx in range(max_rounds):
        print(f"\n[RECAPTCHA] Раунд {round_idx + 1}/{max_rounds}")

        if _is_solved(page):
            print("[RECAPTCHA] Уже решено!")
            return True

        challenge_text = get_challenge_text(page)
        print(f"[RECAPTCHA] Текст задания: {challenge_text}")

        if not challenge_text:
            print("[RECAPTCHA] Не удалось получить текст задания, жду 1с и пробую снова...")
            time.sleep(1)
            continue

        bframe = _find_bframe(page)
        if not bframe:
            print("[RECAPTCHA] bframe потерян")
            return False

        iframe_el = bframe.frame_element()
        iframe_el.screenshot(path=RECAPTCHA_SCREENSHOT_PATH, type="jpeg", quality=90)

        result = _ask_llm_for_tiles(challenge_text, RECAPTCHA_SCREENSHOT_PATH)
        if not result:
            time.sleep(1)
            continue

        if result.get("done"):
            print("[RECAPTCHA] LLM сообщила что уже решено")
            return True

        if result.get("skip"):
            print("[RECAPTCHA] LLM хочет пропустить (новые картинки)")
            skip_btn = _get_skip_button(page)
            if skip_btn:
                click_fallback(page, skip_btn[0], skip_btn[1])
                print(f"[RECAPTCHA] Кликнул 'Пропустить/Обновить' через pyautogui")
            time.sleep(1)
            continue

        tiles_to_click = result.get("tiles", [])
        if not tiles_to_click:
            print("[RECAPTCHA] LLM не выбрала ни одной плитки. Пропускаю раунд.")
            skip_btn = _get_skip_button(page)
            if skip_btn:
                click_fallback(page, skip_btn[0], skip_btn[1])
                print(f"[RECAPTCHA] Кликнул 'Пропустить'")
            time.sleep(1)
            continue

        tiles = _get_tiles(page)
        if not tiles:
            print("[RECAPTCHA] Не удалось получить позиции плиток, жду...")
            time.sleep(1)
            continue

        for t_idx in tiles_to_click:
            matched = [t for t in tiles if t["index"] == t_idx]
            if matched:
                t = matched[0]
                print(f"[RECAPTCHA] Клик по плитке {t_idx} через pyautogui (viewport {t['x']}, {t['y']})")
                click_fallback(page, t["x"], t["y"])
                time.sleep(0.3)
            else:
                print(f"[RECAPTCHA] Плитка с индексом {t_idx} не найдена")

        verify_btn = _get_verify_button(page)
        if verify_btn:
            print(f"[RECAPTCHA] Клик 'Проверить' через pyautogui (viewport {verify_btn[0]}, {verify_btn[1]})")
            click_fallback(page, verify_btn[0], verify_btn[1])
        else:
            print("[RECAPTCHA] Кнопка 'Проверить' не найдена")

        time.sleep(2)

        if _is_solved(page):
            print("[RECAPTCHA] РЕШЕНО!")
            return True

        incorrect = page.evaluate("""() => {
            const el = document.querySelector('.rc-imageselect-incorrect-response');
            return el && el.style.display !== 'none';
        }""")
        if incorrect:
            print("[RECAPTCHA] Неправильный ответ, пробую снова...")
            time.sleep(1)
            continue

    print("[RECAPTCHA] Достигнут лимит попыток")
    return False
