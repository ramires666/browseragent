import time
import random
import pyautogui
import pyperclip
import pygetwindow as gw

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.1

SCREENSHOT_PATH = r"W:\_python\OWL\browser_screen.jpg"


def _get_window_position(page):
    """Возвращает словарь с screenX, screenY, outerW, outerH, innerW, innerH браузера."""
    return page.evaluate("""() => {
        return {
            screenX: window.screenX,
            screenY: window.screenY,
            outerW: window.outerWidth,
            outerH: window.outerHeight,
            innerW: window.innerWidth,
            innerH: window.innerHeight
        };
    }""")


def viewport_to_screen(page, vx, vy):
    info = _get_window_position(page)
    w_diff = info["outerW"] - info["innerW"]
    h_diff = info["outerH"] - info["innerH"]
    border_w = w_diff // 2
    top_chrome = h_diff - border_w
    sx = info["screenX"] + border_w + vx
    sy = info["screenY"] + top_chrome + vy
    result = int(sx), int(sy)
    print(f"[VIEWPORT→SCREEN] viewport=({vx},{vy}) screen=({result[0]},{result[1]}) "
          f"screenX={info['screenX']} screenY={info['screenY']} "
          f"outer={info['outerW']}x{info['outerH']} inner={info['innerW']}x{info['innerH']} "
          f"border_w={border_w} top_chrome={top_chrome}")
    return result


def _focus_browser_window(page):
    """Пытается переключить фокус на окно браузера через pygetwindow."""
    title = page.title()
    if not title:
        title = "chrome"
    try:
        windows = gw.getWindowsWithTitle(title)
        if not windows:
            windows = gw.getWindowsWithTitle("Chrome")
        if windows:
            win = windows[0]
            if not win.isActive:
                win.activate()
                time.sleep(0.5)
                if not win.isActive:
                    win.minimize()
                    time.sleep(0.1)
                    win.restore()
                    time.sleep(0.3)
    except Exception:
        pass


def click_fallback(page, vx, vy):
    """Клик pyautogui по координатам viewport (vx, vy)."""
    sx, sy = viewport_to_screen(page, vx, vy)
    _focus_browser_window(page)
    pyautogui.moveTo(sx, sy, duration=0.2)
    time.sleep(0.1)
    pyautogui.click()
    time.sleep(0.3)


def click_human_like(page, vx, vy):
    """Человекоподобный клик pyautogui: jitter + кривая траектория + случайная задержка."""
    jx = random.randint(-3, 3)
    jy = random.randint(-3, 3)
    sx, sy = viewport_to_screen(page, vx + jx, vy + jy)
    _focus_browser_window(page)

    dest_x = sx + random.randint(-2, 2)
    dest_y = sy + random.randint(-2, 2)

    pyautogui.moveTo(
        dest_x + random.randint(-50, 50),
        dest_y + random.randint(-50, 50),
        duration=random.uniform(0.1, 0.25)
    )
    pyautogui.moveTo(dest_x, dest_y, duration=random.uniform(0.08, 0.2))
    time.sleep(random.uniform(0.05, 0.15))
    pyautogui.click()
    time.sleep(random.uniform(0.15, 0.35))


def double_click_fallback(page, vx, vy):
    """Двойной клик pyautogui по координатам viewport (vx, vy)."""
    sx, sy = viewport_to_screen(page, vx, vy)
    _focus_browser_window(page)
    pyautogui.moveTo(sx, sy, duration=0.2)
    time.sleep(0.1)
    pyautogui.doubleClick()
    time.sleep(0.3)


def type_fallback(page, text):
    """Вставляет текст через Ctrl+V (буфер обмена). Надёжнее чем pyautogui.write() для Unicode/кириллицы."""
    _focus_browser_window(page)
    time.sleep(0.3)
    try:
        pyperclip.copy(text)
        time.sleep(0.1)
        clip_check = pyperclip.paste()
        print(f"[TYPE] clipboard: '{clip_check[:30]}'")
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.3)
    except Exception as e:
        print(f"[TYPE] pyperclip failed ({e}), fallback to pyautogui.write()")
        pyautogui.write(text, interval=0.08)


def type_js_fallback(page, el_id, text):
    """Вставляет текст через JS (value + events). Запасной вариант когда pyautogui+clipboard не работает."""
    print(f"[TYPE JS] устанавливаю value элемента {el_id} через DOM")
    escaped = text.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
    result = page.evaluate(f"""() => {{
        const el = document.querySelector('[data-ai-id="{el_id}"]');
        if (!el) return 'NO_EL';
        el.focus();
        el.value = '{escaped}';
        el.dispatchEvent(new Event('input', {{bubbles: true, cancelable: true}}));
        el.dispatchEvent(new Event('change', {{bubbles: true, cancelable: true}}));
        return el.value;
    }}""")
    print(f"[TYPE JS] результат: '{result}'")
    return result == text


def press_fallback(page, key):
    """Нажимает клавишу через pyautogui."""
    _focus_browser_window(page)
    time.sleep(0.1)
    pyautogui.press(key)
    time.sleep(0.2)


def find_element_coords(elements, element_id):
    """Ищет элемент по id в списке elements и возвращает (x, y) или None."""
    for el in elements:
        if el["id"] == element_id:
            return el["x"], el["y"]
    return None
