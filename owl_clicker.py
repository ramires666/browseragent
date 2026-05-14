import time
import pyautogui
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
    """Конвертирует координаты viewport (vx, vy) в абсолютные экранные координаты."""
    info = _get_window_position(page)
    w_diff = info["outerW"] - info["innerW"]
    h_diff = info["outerH"] - info["innerH"]

    left_chrome = w_diff // 2
    top_chrome = h_diff

    sx = info["screenX"] + left_chrome + vx
    sy = info["screenY"] + top_chrome + vy
    return int(sx), int(sy)


def _focus_browser_window(page):
    """Пытается переключить фокус на окно браузера через pygetwindow."""
    title = page.title()
    if not title:
        title = "chrome"
    try:
        windows = gw.getWindowsWithTitle(title)
        if windows:
            win = windows[0]
            if not win.isActive:
                win.activate()
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


def double_click_fallback(page, vx, vy):
    """Двойной клик pyautogui по координатам viewport (vx, vy)."""
    sx, sy = viewport_to_screen(page, vx, vy)
    _focus_browser_window(page)
    pyautogui.moveTo(sx, sy, duration=0.2)
    time.sleep(0.1)
    pyautogui.doubleClick()
    time.sleep(0.3)


def type_fallback(page, text):
    """Печатает текст через pyautogui (предварительно фокусит окно браузера)."""
    _focus_browser_window(page)
    time.sleep(0.2)
    pyautogui.write(text, interval=0.05)


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
