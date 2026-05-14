import json
import time
import traceback

from owl_browser import (
    create_browser,
    close_browser,
    annotate_and_extract_elements,
    get_focused_id,
    resolve_locator
)
from owl_llm import ask_model, SCREENSHOT_PATH
from owl_clicker import (
    click_fallback,
    double_click_fallback,
    type_fallback,
    press_fallback,
    find_element_coords
)


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
    print("\n[GOOGLE BLOCK] Обнаружена блокировка Google. Жду 1.5с для загрузки чекбокса...")
    time.sleep(1.5)

    for attempt in range(3):
        vx, vy = _find_recaptcha_checkbox(page)
        if vx is not None:
            print(f"[GOOGLE BLOCK] reCAPTCHA чекбокс найден, клик через pyautogui (viewport {vx}, {vy})")
            click_fallback(page, vx, vy)
            print("[GOOGLE BLOCK] Кликнул. Жду 1.5с...")
            time.sleep(1.5)
            print("[GOOGLE BLOCK] Завершено. Перехожу к шагу с капчей.")
            return True
        print(f"[GOOGLE BLOCK] reCAPTCHA iframe не найден, попытка {attempt + 1}/3, жду 1с...")
        time.sleep(1)

    print("[GOOGLE BLOCK] Не удалось найти reCAPTCHA. Пробую кликнуть по центру страницы через pyautogui (нижняя треть)...")
    page.screenshot(path=SCREENSHOT_PATH, type="jpeg", quality=85, full_page=False)

    text = page_text(page)
    print(f"[GOOGLE BLOCK] Текст на странице: {text[:500]}")
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


def same_action(a, b):
    if not a or not b:
        return False
    return json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)


def do_action(page, action, elements):
    kind = action.get("action")
    print("[ACTION]", action)

    if kind == "goto":
        page.goto(action["url"], wait_until="domcontentloaded")
        return False

    if kind == "click":
        el_id = action["id"]
        try:
            locator = resolve_locator(page, el_id)
            locator.wait_for(state="visible", timeout=5000)
            locator.click(timeout=5000)
        except Exception as e:
            print(f"[FALLBACK] Playwright click failed: {e}")
            coords = find_element_coords(elements, el_id)
            if coords:
                click_fallback(page, coords[0], coords[1])
                print(f"[FALLBACK] pyautogui click at viewport ({coords[0]}, {coords[1]})")
            else:
                print(f"[FALLBACK] Element {el_id} not found in list, retrying with full page screenshot...")
                raise
        return False

    if kind == "type":
        el_id = action["id"]
        text = action["text"]
        try:
            locator = resolve_locator(page, el_id)
            locator.wait_for(state="visible", timeout=5000)
            locator.fill(text, timeout=5000)
        except Exception as e:
            print(f"[FALLBACK] Playwright type failed: {e}")
            coords = find_element_coords(elements, el_id)
            if coords:
                click_fallback(page, coords[0], coords[1])
                type_fallback(page, text)
                print(f"[FALLBACK] pyautogui type at viewport ({coords[0]}, {coords[1]})")
            else:
                print(f"[FALLBACK] Element {el_id} not found in list")
                raise
        return False

    if kind == "press":
        key = action["key"]
        try:
            page.keyboard.press(key)
        except Exception as e:
            print(f"[FALLBACK] Playwright press failed: {e}")
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
    playwright = browser = page = None

    try:
        playwright, browser, page = create_browser(headless=False)
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
                handle_google_block(page)
                continue

            page.screenshot(path=SCREENSHOT_PATH, type="jpeg", quality=85, full_page=False)

            print(f"\n>>> ОТПРАВЛЯЮ ПРОМПТ | Задача: \"{task}\" | Элементов: {len(elements)} | История: {len(history)} шагов")
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
                finished = do_action(page, action, elements)
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
