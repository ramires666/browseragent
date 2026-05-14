import os
import json
import time
import traceback

from owl_browser import (
    create_browser,
    close_browser,
    annotate_and_extract_elements,
    get_focused_id,
)
from owl_llm import ask_model, SCREENSHOT_PATH
from owl_clicker import (
    click_fallback,
    click_human_like,
    double_click_fallback,
    type_fallback,
    type_js_fallback,
    press_fallback,
    find_element_coords
)
from owl_task_plans import GOOGLE_SEARCH_PLAN
from owl_recaptcha import (
    is_recaptcha_challenge,
    ensure_recaptcha_challenge,
    has_recaptcha_on_page,
    detect_recaptcha_via_vision,
    solve as solve_recaptcha
)


COOKIE_PATH = os.getenv("COOKIE_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.json"))
SAVED_COOKIES = False


def save_cookies(page):
    global SAVED_COOKIES
    try:
        cookies = page.context.cookies()
        with open(COOKIE_PATH, "w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
        print(f"[COOKIES] Сохранено {len(cookies)} кук в {COOKIE_PATH}")
        SAVED_COOKIES = True
    except Exception as e:
        print(f"[COOKIES] Ошибка сохранения: {e}")


def load_cookies(page):
    if not os.path.exists(COOKIE_PATH):
        print(f"[COOKIES] Файл {COOKIE_PATH} не найден")
        return
    try:
        with open(COOKIE_PATH, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        page.context.add_cookies(cookies)
        print(f"[COOKIES] Загружено {len(cookies)} кук из {COOKIE_PATH}")
    except Exception as e:
        print(f"[COOKIES] Ошибка загрузки: {e}")


CAPTCHA_KEYWORDS = [
    "captcha", "recaptcha", "verify you're human", "verify your identity",
    "i'm not a robot", "i am not a robot", "security check",
    "подтвердите", "капча", "рекапча", "не робот", "проверка безопасности",
    "человек", "антибот", "пожалуйста, подтвердите"
]


GOOGLE_BLOCK_KEYWORDS = [
    "google has blocked", "our systems have detected", "unusual traffic",
    "sorry...", "automated queries", "this page appears to be automated",
    "blocked the request", "google blocked"
]


def page_text(page):
    try:
        return page.evaluate("() => document.body?.innerText?.slice(0, 3000) || ''")
    except Exception:
        return ""


def detect_google_block(page):
    text = page_text(page).lower()
    for kw in GOOGLE_BLOCK_KEYWORDS:
        if kw in text:
            print(f"  [GOOGLE BLOCK] триггер: \"{kw}\"")
            return True
    return False


def handle_google_block(page):
    print("\n[GOOGLE BLOCK] Обнаружена блокировка Google. Жду 3с для загрузки reCAPTCHA...")
    time.sleep(3)

    for attempt in range(3):
        vx, vy = _find_recaptcha_checkbox(page)
        if vx is not None:
            print(f"[GOOGLE BLOCK] Чекбокс найден, клик через pyautogui (viewport {vx:.0f}, {vy:.0f})")
            click_human_like(page, int(vx), int(vy))

            for wait_s in [2, 2, 3]:
                print(f"[GOOGLE BLOCK] Жду {wait_s}с появления challenge...")
                time.sleep(wait_s)
                if is_recaptcha_challenge(page):
                    print("[GOOGLE BLOCK] Challenge появился!")
                    return True

            print(f"[GOOGLE BLOCK] Challenge не найден через фреймы — пробую через скриншот...")
            if detect_recaptcha_via_vision(page, SCREENSHOT_PATH):
                print("[GOOGLE BLOCK] Challenge найден через скриншот!")
                return True

            print(f"[GOOGLE BLOCK] Попытка {attempt + 1}/3")
            continue

        print(f"[GOOGLE BLOCK] iframe не найден, попытка {attempt + 1}/3, жду 1с...")
        time.sleep(1)

    print("[GOOGLE BLOCK] Не удалось активировать reCAPTCHA за 3 попытки.")
    page.screenshot(path=SCREENSHOT_PATH, type="jpeg", quality=85, full_page=False)
    text = page_text(page)
    print(f"[GOOGLE BLOCK] Текст страницы: {text[:500]}")
    return False


def _find_recaptcha_checkbox(page):
    for frame in page.frames:
        url = frame.url.lower()
        if "recaptcha/api2/anchor" in url:
            el = frame.frame_element()
            box = el.bounding_box()
            if box:
                return (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)

    locator = page.locator('iframe[src*="recaptcha/api2/anchor"], iframe[title*="recaptcha"], iframe[src*="recaptcha"]')
    count = locator.count()
    if count > 0:
        for i in range(count):
            box = locator.nth(i).bounding_box()
            if box:
                return (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)

    locator = page.locator('[class*="recaptcha"], [id*="recaptcha"]')
    count = locator.count()
    if count > 0:
        box = locator.first.bounding_box()
        if box:
            return (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)

    return None, None


def detect_captcha(page, elements):
    text = page_text(page)
    lower_text = text.lower()

    for kw in CAPTCHA_KEYWORDS:
        if kw in lower_text:
            print(f"  [CAPTCHA TRIGGER] найдено: \"{kw}\"")
            return True

    for el in elements:
        for field in ("text", "label"):
            val = (el.get(field) or "").lower()
            for kw in CAPTCHA_KEYWORDS:
                if kw in val:
                    print(f"  [CAPTCHA TRIGGER] элемент {el['id']}: \"{kw}\"")
                    return True

    return False


VISION_VERIFY_PROMPT = """
You see a screenshot taken AFTER performing this action: __ACTION__

Look at the screenshot carefully and verify if the action succeeded.
- For a click: is the target element now activated/selected/focused?
- For typing: does the input field contain the typed text?
- For pressing a key: did the expected change happen?

Return ONLY valid JSON:
{"ok":true,"reason":"the element is now focused"}
or
{"ok":false,"reason":"the click missed, element is not focused"}
"""


def verify_action_via_screenshot(page, action):
    """После выполнения действия делает скриншот и спрашивает LLM, сработало ли."""
    import base64
    import requests
    from owl_llm import API_URL, API_KEY, SCREENSHOT_PATH, repair_json, _ask_model_to_fix_json

    time.sleep(0.8)
    page.screenshot(path=SCREENSHOT_PATH, type="jpeg", quality=85, full_page=False)

    with open(SCREENSHOT_PATH, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("utf-8")

    viewport = page.viewport_size
    prompt = VISION_VERIFY_PROMPT.replace("__ACTION__", json.dumps(action, ensure_ascii=False))

    payload = {
        "model": "gui-owl",
        "messages": [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Verify action: {json.dumps(action, ensure_ascii=False)}"},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
                ]
            }
        ],
        "temperature": 0.1,
        "max_tokens": 400,
        "stream": False,
    }

    headers = {}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"

    try:
        r = requests.post(API_URL, json=payload, headers=headers, timeout=180)
        r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
        raw = (msg.get("content") or msg.get("reasoning_content") or "").strip()
        print(f"[VERIFY RAW] {raw[:300]}")
        brace = raw.find("{")
        if brace >= 0:
            raw = raw[brace:]
        close = raw.rfind("}")
        if close >= 0:
            raw = raw[:close + 1]
        raw = repair_json(raw)
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            fixed = _ask_model_to_fix_json(raw)
            if fixed:
                result = json.loads(fixed)
            else:
                raise
        return result.get("ok", False), result.get("reason", "")
    except Exception as e:
        print(f"[VERIFY ERROR] {e}")
        return True, "verify failed, assume ok"


VISION_FALLBACK_PROMPT = """
You see a screenshot of a browser. The system needs to perform an action but cannot find the target element in the DOM.

Action to perform: __ACTION__

Look at the screenshot carefully. Find the element that matches the action description.

CRITICAL: Return ONLY a JSON object. Do NOT output any reasoning, chain-of-thought, or text before or after the JSON. Your entire response must be valid JSON.

Return EXACT pixel coordinates (x, y) of the CENTER of that element in viewport:
{"found":true,"x":200,"y":350,"reason":"the search button is at these coordinates"}
or if not found:
{"found":false,"reason":"element not visible in screenshot"}

After you output the JSON, STOP immediately.
"""


def vision_fallback(page, action, elements, action_label=""):
    """Универсальный fallback: скриншот + LLM ищет элемент и возвращает координаты для клика."""
    import base64
    import requests
    from owl_llm import API_URL, API_KEY

    print(f"[VISION FALLBACK] Ищу элемент через скриншот... действие: {action_label or action}")
    time.sleep(0.5)
    page.screenshot(path=SCREENSHOT_PATH, type="jpeg", quality=90, full_page=False)

    with open(SCREENSHOT_PATH, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("utf-8")

    viewport = page.viewport_size
    prompt = VISION_FALLBACK_PROMPT.replace("__ACTION__", json.dumps(action, ensure_ascii=False))

    payload = {
        "model": "gui-owl",
        "messages": [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Viewport: {viewport['width']}x{viewport['height']}. Find element for action: {json.dumps(action, ensure_ascii=False)}"},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
                ]
            }
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
        r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
        raw = (msg.get("content") or "").strip()
        if not raw:
            raw = (msg.get("reasoning_content") or "").strip()
        print(f"[VISION FALLBACK RAW] {raw[:500]}")

        if not raw:
            print("[VISION FALLBACK] LLM вернула пустой ответ")
            return False

        brace = raw.find("{")
        if brace >= 0:
            raw = raw[brace:]

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            from owl_llm import repair_json, _ask_model_to_fix_json
            repaired = repair_json(raw)
            if repaired != raw:
                try:
                    result = json.loads(repaired)
                except json.JSONDecodeError:
                    fixed = _ask_model_to_fix_json(raw)
                    if fixed:
                        result = json.loads(fixed)
                    else:
                        print(f"[VISION FALLBACK] JSON не исправляется: {raw[:200]}")
                        return False
            else:
                fixed = _ask_model_to_fix_json(raw)
                if fixed:
                    result = json.loads(fixed)
                else:
                    print(f"[VISION FALLBACK] JSON невалиден: {raw[:200]}")
                    return False
        if result.get("found") and "x" in result and "y" in result:
            vx, vy = int(result["x"]), int(result["y"])
            print(f"[VISION FALLBACK] LLM указала координаты ({vx}, {vy}) — кликаю через pyautogui")
            click_human_like(page, vx, vy)
            return True
        else:
            print(f"[VISION FALLBACK] LLM не нашла элемент: {result.get('reason', 'no reason')}")
            return False
    except Exception as e:
        print(f"[VISION FALLBACK] Ошибка: {e}")
        return False


def same_action(a, b):
    if not a or not b:
        return False
    return json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)


def do_action(page, action, elements, focused_id=None):
    kind = action.get("action")
    print("[ACTION]", action)

    if kind == "goto":
        page.goto(action["url"], wait_until="domcontentloaded")
        return False

    if kind == "click":
        el_id = action["id"]
        coords = find_element_coords(elements, el_id)
        if coords:
            print(f"[PYAUTOGUI] click {el_id} по DOM координатам ({coords[0]}, {coords[1]})")
            click_human_like(page, coords[0], coords[1])
            return False
        print(f"[PYAUTOGUI] DOM координат нет для {el_id}, пробую vision fallback...")
        ok = vision_fallback(page, action, elements, action_label=f"click {el_id}")
        if not ok:
            print(f"[PYAUTOGUI] Ничего не помогло — ошибка")
            raise
        return False

    if kind == "type":
        el_id = action["id"]
        text = action["text"]
        coords = find_element_coords(elements, el_id)
        if coords:
            if focused_id == el_id:
                print(f"[PYAUTOGUI] {el_id} уже в фокусе — без клика, сразу печатаю")
            else:
                print(f"[PYAUTOGUI] click + type в {el_id} по DOM координатам ({coords[0]}, {coords[1]})")
                click_human_like(page, coords[0], coords[1])
                time.sleep(0.5)
            type_fallback(page, text)
            time.sleep(0.3)
            ok2, reason = verify_action_via_screenshot(page, action)
            if not ok2:
                print(f"[VERIFY] Текст не ввёлся: {reason}. Пробую JS fallback...")
                time.sleep(0.5)
                js_ok = type_js_fallback(page, el_id, text)
                if js_ok:
                    ok2 = True
                    print("[TYPE JS] успешно")
                else:
                    print("[TYPE JS] не сработал")
                    dom_val = page.evaluate(f"() => {{ const el = document.querySelector('[data-ai-id=\"{el_id}\"]'); return el ? el.value || el.textContent || '' : 'NO_EL' }}")
                    print(f"[DOM VERIFY] value элемента {el_id} = '{dom_val}'")
                    if dom_val and dom_val != 'NO_EL' and text.lower() in dom_val.lower():
                        print("[DOM VERIFY] текст найден в DOM — считаем успехом")
                        ok2 = True
            if ok2:
                print("[PYAUTOGUI] Enter после ввода текста")
                press_fallback(page, "enter")
            else:
                print("[PYAUTOGUI] Текст не ввёлся — не нажимаю Enter")
            return False
        print(f"[PYAUTOGUI] DOM координат нет для {el_id}, пробую vision fallback...")
        ok = vision_fallback(page, action, elements, action_label=f"type into {el_id}")
        if ok:
            time.sleep(0.5)
            type_fallback(page, text)
            ok2, reason = verify_action_via_screenshot(page, action)
            if not ok2:
                print(f"[VERIFY] Текст не ввёлся: {reason}. Пробую JS fallback...")
                time.sleep(0.5)
                js_ok = type_js_fallback(page, el_id, text)
                if js_ok:
                    ok2 = True
            if ok2:
                print("[PYAUTOGUI] Enter после ввода текста")
                press_fallback(page, "enter")
        else:
            print(f"[PYAUTOGUI] Ничего не помогло — ошибка")
            raise
        return False

    if kind == "press":
        key = action["key"]
        print(f"[PYAUTOGUI] press '{key}'")
        press_fallback(page, key)
        return False

    if kind == "wait":
        seconds = float(action.get("seconds", 1))
        page.wait_for_timeout(int(seconds * 1000))
        return False

    if kind == "done":
        return True

    raise ValueError(f"Unknown action: {kind}")


def main():
    print("[START]")
    TASK_FILE = "task.txt"
    task_from_file = None
    try:
        with open(TASK_FILE, "r", encoding="utf-8") as f:
            task_from_file = f.read().strip()
    except Exception:
        pass

    if task_from_file:
        task = task_from_file
        print(f"[TASK FROM FILE] {task}")
    else:
        task = input("Введите задачу: ").strip()
        if not task:
            print("Пустая задача.")
            return

    print("\n" + "█" * 55)
    print("  SYSTEM PROMPT (инструкция модели):")
    print("█" * 55)
    from owl_llm import SYSTEM_PROMPT as sp
    for line in sp.strip().splitlines():
        print(f"  {line}")
    print("█" * 55 + "\n")

    history = []
    last_action = None
    repeat_count = 0
    google_block_attempts = 0
    playwright = browser = page = None

    try:
        playwright, browser, page = create_browser(headless=False)
        load_cookies(page)
        page.goto("https://www.google.com", wait_until="domcontentloaded")

        for step in range(20):
            print(f"\n========== STEP {step + 1} ==========")

            elements = annotate_and_extract_elements(page)
            focused_id = get_focused_id(page)

            print("[FOCUSED]", focused_id)
            print("[ELEMENTS]", len(elements))
            for el in elements[:20]:
                print(el)

            if detect_google_block(page):
                google_block_attempts += 1
                print(f"\n[GOOGLE BLOCK] Попытка {google_block_attempts}/3")

                if google_block_attempts >= 3:
                    print("[GOOGLE BLOCK] 3 попытки не помогли.")
                    print("    Пройди проверку вручную в окне браузера,")
                    print("    затем нажми Enter чтобы продолжить...")
                    input("    >> ")
                    print("[CONTINUE]")
                    time.sleep(1)
                    continue

                handle_google_block(page)

                if is_recaptcha_challenge(page):
                    print("[GOOGLE BLOCK] reCAPTCHA challenge появился, решаю...")
                    solved = solve_recaptcha(page)
                    if solved:
                        google_block_attempts = 0
                        print("[CAPTCHA] reCAPTCHA разгадана!")
                        save_cookies(page)
                        time.sleep(0.5)
                        continue
                    else:
                        print("[CAPTCHA] reCAPTCHA не решена, прошу помощи...")
                        print("    Разгадай вручную, затем нажми Enter...")
                        input("    >> ")
                        print("[CONTINUE]")
                        time.sleep(1)
                        continue
                else:
                    continue

            if ensure_recaptcha_challenge(page):
                print("\n[!] ОБНАРУЖЕНА reCAPTCHA — пробую разгадать автоматически...")
                solved = solve_recaptcha(page)
                if solved:
                    print("[CAPTCHA] reCAPTCHA разгадана! Продолжаю.")
                    save_cookies(page)
                    time.sleep(0.5)
                    continue
                else:
                    print("[CAPTCHA] Авторазгадывание не помогло — прошу помощи вручную.")

            if has_recaptcha_on_page(page):
                print("\n[!] reCAPTCHA на странице, но авторешение не сработало")
                print("    Разгадай reCAPTCHA вручную в окне браузера,")
                print("    затем нажми Enter чтобы продолжить...")
                input("    >> ")
                print("[CONTINUE]")
                time.sleep(1)
                continue

            page.screenshot(path=SCREENSHOT_PATH, type="jpeg", quality=85, full_page=False)

            task_with_plan = task
            task_lower = task.lower()
            if any(w in task_lower for w in ["найди", "поиск", "гугл", "google", "найди", "search"]):
                task_with_plan = f"{task}\n\n{GOOGLE_SEARCH_PLAN}"

            print(f"\n>>> ОТПРАВЛЯЮ ПРОМПТ | Задача: \"{task}\" | Элементов: {len(elements)} | История: {len(history)} шагов")
            raw = ask_model(
                task=task_with_plan,
                screenshot_path=SCREENSHOT_PATH,
                elements=elements,
                current_url=page.url,
                current_title=page.title(),
                focused_id=focused_id,
                history=history
            )

            print("[RAW]", raw)

            try:
                action = json.loads(raw)
            except Exception:
                print("[ERROR] Модель вернула невалидный JSON")
                print(raw)
                break

            print("\n" + "─" * 55)
            print(f"  ЗАДАЧА: {task}")
            print(f"  ШАГ {step + 1}")
            print(f"  URL:    {page.url}")
            print(f"  TITLE:  {page.title()}")
            print(f"  ДЕЙСТВИЕ: {json.dumps(action, ensure_ascii=False, indent=2)}")
            print("─" * 55)

            if detect_captcha(page, elements):
                if ensure_recaptcha_challenge(page):
                    print("\n[!] ОБНАРУЖЕНА reCAPTCHA — пробую разгадать автоматически...")
                    solved = solve_recaptcha(page)
                    if solved:
                        print("[CAPTCHA] reCAPTCHA разгадана! Продолжаю.")
                        save_cookies(page)
                        time.sleep(0.5)
                        continue
                    else:
                        print("[CAPTCHA] Авторазгадывание не помогло — прошу помощи.")

                if has_recaptcha_on_page(page) or is_recaptcha_challenge(page):
                    print("\n[!] reCAPTCHA на странице, авторешение не сработало")
                    print("    Разгадай вручную, затем нажми Enter...")
                    input("    >> ")
                    print("[CONTINUE]")
                    time.sleep(1)
                    continue

                print("\n[!] ОБНАРУЖЕНА КАПЧА / ПРОВЕРКА БЕЗОПАСНОСТИ")
                print("    Подтверди капчу вручную в окне браузера,")
                print("    затем нажми Enter чтобы продолжить...")
                input("    >> ")
                print("[CONTINUE]")
                page.screenshot(path=SCREENSHOT_PATH, type="jpeg", quality=85, full_page=False)
                print(f"\n>>> ПОВТОРНЫЙ ПРОМПТ ПОСЛЕ КАПЧИ | Задача: \"{task}\"")
                raw = ask_model(
                    task=task,
                    screenshot_path=SCREENSHOT_PATH,
                    elements=elements,
                    current_url=page.url,
                    current_title=page.title(),
                    focused_id=focused_id,
                    history=history
                )
                print("[RAW]", raw)
                try:
                    action = json.loads(raw)
                except Exception:
                    print("[ERROR] Модель вернула невалидный JSON после капчи")
                    print(raw)
                    break
                print("\n" + "─" * 55)
                print(f"  НОВОЕ ДЕЙСТВИЕ: {json.dumps(action, ensure_ascii=False, indent=2)}")
                print("─" * 55)

            if same_action(action, last_action):
                repeat_count += 1
            else:
                repeat_count = 0

            if repeat_count >= 2:
                print("[LOOP GUARD] Повтор одного и того же действия. Останавливаюсь.")
                break

            try:
                finished = do_action(page, action, elements, focused_id)
            except Exception as e:
                print("[ERROR] Не удалось выполнить действие даже с fallback")
                print(e)
                traceback.print_exc()
                break

            history.append({
                "step": step + 1,
                "url": page.url,
                "title": page.title(),
                "focused_id_before_next_step": focused_id,
                "action": action
            })

            last_action = action

            if finished:
                print("[DONE]")
                break

            time.sleep(1)

    except Exception as e:
        print("[FATAL]", e)
        traceback.print_exc()
    finally:
        if browser and playwright:
            close_browser(playwright, browser)
        print("[EXIT]")


if __name__ == "__main__":
    main()
