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
    task = input("Введите задачу: ").strip()
    if not task:
        print("Пустая задача.")
        return

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

            page.screenshot(path=SCREENSHOT_PATH, type="jpeg", quality=85, full_page=False)

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
