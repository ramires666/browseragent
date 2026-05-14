import json
import time
import base64
import requests
from owl_llm import API_URL, API_KEY
from owl_clicker import click_human_like

RECAPTCHA_SCREENSHOT_PATH = r"W:\_python\OWL\_recaptcha_challenge.jpg"

RECAPTCHA_SYSTEM_PROMPT = """
You are solving a reCAPTCHA image challenge. You look at a screenshot showing a grid of images and a challenge instruction.

Your job:
1. Understand the challenge (e.g. "select all squares with traffic lights")
2. Find every image that matches
3. Return the exact (x, y) pixel coordinates of the CENTER of each matching image

The screenshot dimensions are known to you. Return coordinates in screenshot pixels.

Return exactly one JSON object:
{
  "clicks": [{"x": 120, "y": 80}, {"x": 300, "y": 80}, {"x": 120, "y": 200}],
  "reason": "these three images contain traffic lights"
}

Rules:
- Coordinates must be pixel positions from the TOP-LEFT of the screenshot.
- Include ALL matching images. Clicking a wrong image will fail the challenge.
- If no images match, return {"clicks": [], "skip": true, "reason": "none match, need new images"}
- If challenge is already solved (green checkmark visible), return {"done": true}
- If images are unclear, return {"skip": true}
- Return ONLY valid JSON, no extra text.
"""


def _random_delay(min_s=0.15, max_s=0.5):
    time.sleep(random.uniform(min_s, max_s))


def _human_like_click(page, vx, vy):
    click_human_like(page, vx, vy)


def _find_bframe(page):
    for frame in page.frames:
        if "recaptcha/api2/bframe" in frame.url.lower():
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
                            f"Challenge text: \"{challenge_text}\"\n\n"
                            "Return (x,y) center coordinates of each matching tile."
                        )
                    },
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
                ]
            }
        ],
        "temperature": 0.1,
        "max_tokens": 500,
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
        iframe_el.screenshot(path=RECAPTCHA_SCREENSHOT_PATH, type="jpeg", quality=90)

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
                _human_like_click(page, skip_btn[0], skip_btn[1])
            _random_delay(0.5, 1)
            continue

        clicks_data = result.get("clicks", [])
        if not clicks_data:
            print("[RECAPTCHA] LLM не выбрала ни одной точки. Пропускаю.")
            skip_btn = _get_skip_button(page)
            if skip_btn:
                _human_like_click(page, skip_btn[0], skip_btn[1])
            _random_delay(0.5, 1)
            continue

        for pt in clicks_data:
            sx = pt.get("x", 0)
            sy = pt.get("y", 0)
            vx = int(bframe_box["x"] + sx)
            vy = int(bframe_box["y"] + sy)
            print(f"[RECAPTCHA] Клик по координатам ({vx}, {vy}) через pyautogui (jitter + random delay)")
            _human_like_click(page, vx, vy)
            _random_delay(0.2, 0.6)

        verify_btn = _get_verify_button(page)
        if verify_btn:
            print(f"[RECAPTCHA] Клик 'Проверить' через pyautogui ({verify_btn[0]}, {verify_btn[1]})")
            _random_delay(0.3, 0.7)
            _human_like_click(page, verify_btn[0], verify_btn[1])
        else:
            print("[RECAPTCHA] Кнопка 'Проверить' не найдена")

        time.sleep(2)

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
            _random_delay(0.5, 1)
            continue

    print("[RECAPTCHA] Достигнут лимит попыток")
    return False
