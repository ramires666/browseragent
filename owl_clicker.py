import time
import random
import pyautogui
import pygetwindow as gw
import ctypes
import keyboard as kb

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.1

SCREENSHOT_PATH = r"W:\_python\OWL\browser_screen.jpg"

_has_cyrillic = lambda t: any(0x0400 < ord(c) < 0x0500 for c in t)

_user32 = ctypes.windll.user32

_SAVED_CURSOR = None


def _save_cursor():
    global _SAVED_CURSOR
    _SAVED_CURSOR = pyautogui.position()


def _restore_cursor():
    if _SAVED_CURSOR:
        pyautogui.moveTo(_SAVED_CURSOR.x, _SAVED_CURSOR.y, duration=random.uniform(0.08, 0.2))


def _move_from_saved(dest_x, dest_y, duration=0.2):
    """Плавное human-like движение: от сохранённой позиции к цели."""
    _restore_cursor()
    time.sleep(random.uniform(0.03, 0.08))
    pyautogui.moveTo(
        dest_x + random.randint(-40, 40),
        dest_y + random.randint(-40, 40),
        duration=random.uniform(duration * 0.6, duration * 1.2)
    )
    pyautogui.moveTo(dest_x, dest_y, duration=random.uniform(duration * 0.3, duration * 0.6))
    _save_cursor()


def _set_keyboard_layout(layout_hex):
    """Переключает раскладку клавиатуры через Windows API."""
    handle = _user32.LoadKeyboardLayoutW(layout_hex, 0x0001)  # KLF_ACTIVATE
    _user32.ActivateKeyboardLayout(handle, 0x0000)
    time.sleep(0.05)


def _get_window_position(page):
    """Возвращает словарь с screenX, screenY, outerW, outerH, innerW, innerH, dpr браузера."""
    return page.evaluate("""() => {
        return {
            screenX: window.screenX,
            screenY: window.screenY,
            outerW: window.outerWidth,
            outerH: window.outerHeight,
            innerW: window.innerWidth,
            innerH: window.innerHeight,
            dpr: window.devicePixelRatio || 1.0
        };
    }""")


def _get_system_dpi_scale():
    """Возвращает системный DPI-scale Windows (1.0, 1.15, 1.25, 1.5, 2.0 и т.д.).
    На 4K-мониторах с 115% Windows-зумом возвращает 1.15.
    pyautogui работает в ФИЗИЧЕСКИХ пикселях (когда процесс DPI-aware),
    а Chrome отдаёт CSS-пиксели — этот множитель нужен для конвертации."""
    try:
        sf = ctypes.windll.shcore.GetScaleFactorForDevice(0)
        return sf / 100.0
    except Exception:
        try:
            dpi = ctypes.windll.user32.GetDpiForSystem()
            return dpi / 96.0
        except Exception:
            return 1.0


_DPI_SCALE = _get_system_dpi_scale()
print(f"[CLICKER INIT] System DPI scale = {_DPI_SCALE}")


def _ensure_browser_focus(page):
    """Фокусит окно через CDP + Win32 без кликов."""
    try:
        page.bring_to_front()
    except Exception:
        pass
    _focus_browser_window(page)
    time.sleep(0.2)


def viewport_to_screen(page, vx, vy):
    """Конвертирует viewport-координаты Chrome (CSS px) в физические пиксели для pyautogui.
    На Windows с DPI != 100% Chrome отдаёт CSS-пиксели, а pyautogui (DPI-aware процесс)
    ожидает физические. Множитель _DPI_SCALE компенсирует это."""
    info = _get_window_position(page)
    w_diff = info["outerW"] - info["innerW"]
    h_diff = info["outerH"] - info["innerH"]
    border_w = w_diff // 2
    top_chrome = h_diff - border_w
    # CSS-координаты точки клика на экране
    sx_css = info["screenX"] + border_w + vx
    sy_css = info["screenY"] + top_chrome + vy
    # Физические пиксели для pyautogui
    sx = int(round(sx_css * _DPI_SCALE))
    sy = int(round(sy_css * _DPI_SCALE))
    print(f"[V2S] viewport=({vx},{vy}) css=({sx_css},{sy_css}) phys=({sx},{sy}) "
          f"dpi={_DPI_SCALE} screenX={info['screenX']} screenY={info['screenY']} "
          f"outer={info['outerW']}x{info['outerH']} inner={info['innerW']}x{info['innerH']} "
          f"border_w={border_w} top_chrome={top_chrome} dpr={info.get('dpr')}")
    return sx, sy


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


def preview_cursor_at(page, vx, vy):
    """Перемещает курсор в целевую позицию без клика для визуальной проверки.
    Возвращает (sx, sy, actual_pos) где actual_pos = pyautogui.position()."""
    sx, sy = viewport_to_screen(page, vx, vy)
    pyautogui.moveTo(sx, sy, duration=0.25)
    actual = pyautogui.position()
    print(f"[CURSOR PREVIEW] target=({sx},{sy})  actual=({actual.x},{actual.y})  delta=({actual.x-sx},{actual.y-sy})")
    return sx, sy, actual


def click_fallback(page, vx, vy):
    """Клик pyautogui по координатам viewport (vx, vy)."""
    _ensure_browser_focus(page)
    sx, sy = viewport_to_screen(page, vx, vy)
    pyautogui.moveTo(sx, sy, duration=0.2)
    time.sleep(0.1)
    pyautogui.click()
    time.sleep(0.3)


def click_human_like(page, vx, vy):
    """Человекоподобный клик pyautogui: jitter + кривая траектория + случайная задержка."""
    _ensure_browser_focus(page)
    jx = random.randint(-3, 3)
    jy = random.randint(-3, 3)
    sx, sy = viewport_to_screen(page, vx + jx, vy + jy)
    dest_x = sx + random.randint(-2, 2)
    dest_y = sy + random.randint(-2, 2)
    _move_from_saved(dest_x, dest_y)
    time.sleep(random.uniform(0.05, 0.15))
    pyautogui.click()
    time.sleep(random.uniform(0.15, 0.35))


def double_click_fallback(page, vx, vy):
    """Двойной клик pyautogui по координатам viewport (vx, vy)."""
    _ensure_browser_focus(page)
    sx, sy = viewport_to_screen(page, vx, vy)
    pyautogui.moveTo(sx, sy, duration=0.2)
    time.sleep(0.1)
    pyautogui.doubleClick()
    time.sleep(0.3)


def type_fallback(page, text):
    """Побуквенный ввод через pyautogui.write() с переключением раскладки под кириллицу."""
    _ensure_browser_focus(page)
    has_cyrillic = _has_cyrillic(text)

    if has_cyrillic:
        _set_keyboard_layout("00000419")
        print(f"[TYPE] раскладка переключена на русскую")
    else:
        _set_keyboard_layout("00000409")
        print(f"[TYPE] раскладка переключена на английскую")

    time.sleep(0.1)

    _focus_browser_window(page)
    page.bring_to_front()
    time.sleep(0.3)

    _user32.ClipCursor(None)
    time.sleep(0.1)

    kb.write(text, delay=0.02)
    time.sleep(0.3)

    _set_keyboard_layout("00000409")
    if has_cyrillic:
        print(f"[TYPE] раскладка возвращена на английскую")
    print(f"[TYPE] напечатано: '{text[:30]}'")


def type_js_fallback(page, el_id, text):
    """Вставляет текст через JS (value + events). Запасной вариант когда pyautogui не сработал."""
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
    _ensure_browser_focus(page)
    pyautogui.press(key)
    time.sleep(0.2)


def find_element_coords(elements, element_id):
    """Ищет элемент по id в списке elements и возвращает (x, y) или None."""
    for el in elements:
        if el["id"] == element_id:
            return el["x"], el["y"]
    return None
