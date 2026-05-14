import json
import os
import time
import random
from dotenv import load_dotenv
from owl_clicker import click_human_like
from owl_recaptcha_llm import ask_llm_for_clicks, detect_recaptcha_via_vision

_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(dotenv_path=_ENV_PATH, override=True)
load_dotenv(override=True)

RECAPTCHA_SCREENSHOT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_recaptcha_challenge.jpg")


def _debug():
    return os.getenv("RECAPTCHA_DEBUG", "").lower() in ("1", "true", "yes")


def _debug_report():
    val = os.getenv("RECAPTCHA_DEBUG", "(not set)")
    print(f"[RECAPTCHA DEBUG] os.getenv('RECAPTCHA_DEBUG') = \"{val}\"")
    print(f"[RECAPTCHA DEBUG] _debug() = {_debug()}")


print(f"\n[OWL_RECAPTCHA] Модуль загружен | .env: {_ENV_PATH}")
_debug_report()


def _wait_step(label, detail=None):
    print(f"\n[WAIT_STEP] вызван: \"{label}\" | RECAPTCHA_DEBUG={_debug()}")
    if not _debug():
        return
    print(f"\n{'=' * 55}")
    print(f"  [{label}]")
    if detail:
        print(f"  {detail}")
    print(f"{'=' * 55}")
    input("  >>> Нажми Enter для продолжения... ")
    print()


def _random_delay(min_s=0.15, max_s=0.5):
    time.sleep(random.uniform(min_s, max_s))


def _find_anchor_frame(page):
    for frame in page.frames:
        if "recaptcha/api2/anchor" in frame.url.lower():
            return frame
    return None


def _find_bframe(page):
    for frame in page.frames:
        url = frame.url.lower()
        if "recaptcha/api2/bframe" in url:
            return frame
    for frame in page.frames:
        url = frame.url.lower()
        if "recaptcha" in url and "bframe" in url:
            return frame
    for frame in page.frames:
        url = frame.url.lower()
        if "recaptcha" in url and "anchor" not in url:
            return frame
    return None


def _click_checkbox(page):
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


def _get_tiles(page):
    bframe = _find_bframe(page)
    if not bframe:
        return None
    try:
        iframe_box = bframe.frame_element().bounding_box()
        if not iframe_box:
            return None
        print(f"[RECAPTCHA] iframe_box: x={iframe_box['x']:.0f} y={iframe_box['y']:.0f} w={iframe_box['width']:.0f} h={iframe_box['height']:.0f}")
        tiles_rel = bframe.evaluate("""() => {
            const cells = document.querySelectorAll('.rc-imageselect-tile, td[class*="tile"], td.rc-imageselect-tile, table.rc-imageselect-table td');
            if (!cells.length) return [];
            return Array.from(cells).map((el, i) => {
                const r = el.getBoundingClientRect();
                return { index: i, x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2) };
            });
        }""")
        if not tiles_rel:
            return None
        print(f"[RECAPTCHA] найдено {len(tiles_rel)} плиток в iframe")
        tiles = []
        for t in tiles_rel:
            tile_x = int(iframe_box["x"] + t["x"])
            tile_y = int(iframe_box["y"] + t["y"])
            print(f"[RECAPTCHA] tile[{t['index']}]: iframe_center({t['x']},{t['y']}) -> viewport({tile_x},{tile_y})")
            tiles.append({
                "index": t["index"],
                "x": tile_x,
                "y": tile_y,
            })
        return tiles
    except Exception as e:
        print(f"[RECAPTCHA] get_tiles error: {e}")
        return None


def _extract_tile_indices(result):
    """Ищет в ответе LLM любые индексы плиток.
    Принимает: [0,3,6] (flat), [[0,0],[0,3],[1,2]] (row,col), ["0","3","6"] (str),
    [{"row":1,"col":2},...] (dicts), {"tiles":[0,3,6]} (ключ 'tiles')."""
    if not result:
        return None

    def _is_dimension_pair(lst):
        return len(lst) == 2 and all(isinstance(v, int) and v >= 2 and v <= 8 for v in lst)

    for key, val in result.items():
        if not isinstance(val, list) or len(val) == 0:
            continue

        if _is_dimension_pair(val):
            continue

        if all(isinstance(v, dict) and "row" in v and "col" in v for v in val):
            cols = result.get("cols")
            if not cols:
                dims = result.get("grid_dimensions") or result.get("dims")
                if isinstance(dims, list) and len(dims) == 2:
                    _, cols = dims[0], dims[1]
                else:
                    cols = 4
            flat = [(int(v["row"]) - 1) * cols + (int(v["col"]) - 1) for v in val]
            print(f"[RECAPTCHA] найдены row/col dicts в '{key}': {flat}")
            return flat

        if all(isinstance(v, int) and 0 <= v <= 48 for v in val):
            print(f"[RECAPTCHA] найдены flat индексы в '{key}': {val}")
            return val

        if all(isinstance(v, str) and v.isdigit() for v in val):
            as_int = [int(v) for v in val]
            print(f"[RECAPTCHA] найдены str индексы в '{key}': {as_int}")
            return as_int

        if all(isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(x, int) for x in v) for v in val):
            flat = [r * 8 + c for r, c in val]
            print(f"[RECAPTCHA] найдены [row,col] пары в '{key}': {val} -> flat={flat}")
            return flat

    return None


def _extract_grid(result):
    """Ищет в ответе LLM информацию о сетке в любом формате."""
    if not result:
        return None
    for key, val in result.items():
        if not isinstance(val, dict):
            continue
        key_lower = key.lower()
        d = val
        if not ("grid" in key_lower or "coordinate" in key_lower or "boundar" in key_lower or "cell" in key_lower):
            continue
        x = d.get("x") or d.get("start_x")
        y = d.get("y") or d.get("start_y")
        cw = d.get("cell_w") or d.get("cell_width") or d.get("width")
        ch = d.get("cell_h") or d.get("cell_height") or d.get("height")
        cols = d.get("cols")
        rows = d.get("rows")
        if x is not None and y is not None and cw and ch:
            if not cols:
                grid_w = d.get("width") or d.get("grid_width")
                if grid_w:
                    cols = max(1, round(grid_w / cw))
            if not cols:
                cols = 3
            if not rows:
                rows = 3
            print(f"[RECAPTCHA] grid из '{key}': x={x} y={y} cell={cw}x{ch} {cols}x{rows}")
            return {"x": int(x), "y": int(y), "cell_w": int(cw), "cell_h": int(ch), "cols": int(cols), "rows": int(rows)}
        if cols and rows:
            print(f"[RECAPTCHA] grid из '{key}': {cols}x{rows} (без пиксельных границ)")
            return {"cols": int(cols), "rows": int(rows)}
    return None


def _tiles_to_clicks(raw_tiles, result, bframe_box, page):
    """Преобразует tile индексы в viewport координаты.
    Приоритет: grid из LLM (точные границы из скриншота) -> DOM _get_tiles -> fallback."""
    if not raw_tiles:
        return []

    old_format = isinstance(raw_tiles[0], dict)
    if old_format:
        return _dedup_coords(raw_tiles, threshold=20)

    grid = _extract_grid(result)
    if grid:
        if "x" in grid and "y" in grid:
            clicks = []
            for idx in raw_tiles:
                col = idx % grid["cols"]
                row = idx // grid["cols"]
                scr_x = grid["x"] + col * grid["cell_w"] + grid["cell_w"] // 2
                scr_y = grid["y"] + row * grid["cell_h"] + grid["cell_h"] // 2
                vp_x = int(bframe_box["x"] + scr_x)
                vp_y = int(bframe_box["y"] + scr_y)
                print(f"[RECAPTCHA] tile {idx} (row={row} col={col}): screenshot_center({scr_x},{scr_y}) -> viewport({vp_x},{vp_y})")
                clicks.append({"x": vp_x, "y": vp_y, "index": idx})
            return clicks
        print(f"[RECAPTCHA] grid только {grid['cols']}x{grid['rows']}, расчитываю центры из bframe_box")
        bw = int(bframe_box["width"])
        bh = int(bframe_box["height"])
        cw = bw // grid["cols"]
        ch = bh // grid["rows"]
        clicks = []
        for idx in raw_tiles:
            col = idx % grid["cols"]
            row = idx // grid["cols"]
            vp_x = int(bframe_box["x"]) + col * cw + cw // 2
            vp_y = int(bframe_box["y"]) + row * ch + ch // 2
            print(f"[RECAPTCHA] tile {idx} (row={row} col={col}): viewport({vp_x},{vp_y})")
            clicks.append({"x": vp_x, "y": vp_y, "index": idx})
        return clicks

    tiles = _get_tiles(page)
    if not tiles:
        print("[RECAPTCHA] tiles_to_clicks: не могу получить плитки, возвращаю индексы как есть")
        return [{"x": int(bframe_box["x"]) + 50 + (i % 3) * 100, "y": int(bframe_box["y"]) + 50 + (i // 3) * 100}
                for i in raw_tiles]

    result_list = []
    for idx in raw_tiles:
        if 0 <= idx < len(tiles):
            result_list.append({"x": tiles[idx]["x"], "y": tiles[idx]["y"], "index": idx})
        else:
            print(f"[RECAPTCHA] tiles_to_clicks: индекс {idx} вне диапазона (0-{len(tiles)-1})")
    return result_list


def _snap_to_grid(coords, bframe_box, page):
    if not coords:
        return coords
    tiles = _get_tiles(page)
    if not tiles:
        print("[RECAPTCHA] snap_to_grid: плитки не найдены, raw координаты")
        return _dedup_coords(coords, threshold=20)
    snapped = []
    for c in coords:
        cx, cy = c.get("x", 0), c.get("y", 0)
        best_dist = 99999
        best_tile = None
        for t in tiles:
            tx = t["x"] - bframe_box["x"]
            ty = t["y"] - bframe_box["y"]
            dist = (cx - tx) ** 2 + (cy - ty) ** 2
            if dist < best_dist:
                best_dist = dist
                best_tile = (tx, ty)
        if best_tile and best_dist < 40000:
            snapped.append({"x": best_tile[0], "y": best_tile[1]})
        else:
            snapped.append(c)
    return _dedup_coords(snapped, threshold=15)


def _dedup_coords(clicks, threshold=25):
    unique = []
    for c in clicks:
        cx, cy = c.get("x", 0), c.get("y", 0)
        dup = False
        for u in unique:
            if abs(cx - u["x"]) <= threshold and abs(cy - u["y"]) <= threshold:
                dup = True
                break
        if not dup:
            unique.append(c)
    return unique


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


def _debug_frames(page):
    print("[RECAPTCHA DEBUG FRAMES] Все фреймы на странице:")
    for i, f in enumerate(page.frames):
        url = f.url[:120]
        try:
            el = f.frame_element()
            box = el.bounding_box()
            box_str = f"box=({box['x']:.0f},{box['y']:.0f} {box['width']:.0f}x{box['height']:.0f})" if box else "box=None"
        except Exception:
            box_str = "box=ERR"
        print(f"  [{i}] {box_str} {url}")


def has_recaptcha_on_page(page):
    return _find_anchor_frame(page) is not None or _find_bframe(page) is not None


def is_recaptcha_challenge(page):
    bframe = _find_bframe(page)
    if not bframe:
        _debug_frames(page)
        return False
    try:
        box = bframe.frame_element().bounding_box()
        if box:
            print(f"[RECAPTCHA] bframe box: {box['width']:.0f}x{box['height']:.0f} at ({box['x']:.0f},{box['y']:.0f})")
        if box and box["width"] >= 50 and box["height"] >= 50:
            return True
        print(f"[RECAPTCHA] bframe мал: {box['width']:.0f}x{box['height']:.0f} < 50x50")
    except Exception as e:
        print(f"[RECAPTCHA] bframe box error: {e}")
    return False


def ensure_recaptcha_challenge(page):
    if is_recaptcha_challenge(page):
        return True
    anchor = _find_anchor_frame(page)
    if not anchor:
        return False
    print("[RECAPTCHA] Чекбокс найден. Кликаю...")
    _click_checkbox(page)
    for wait_s in [2, 2, 3]:
        print(f"[RECAPTCHA] Жду {wait_s}с challenge...")
        time.sleep(wait_s)
        if is_recaptcha_challenge(page):
            print("[RECAPTCHA] Challenge появился (фреймы)!")
            return True
    print("[RECAPTCHA] Challenge не найден через фреймы — пробую скриншот...")
    from owl_llm import SCREENSHOT_PATH
    if detect_recaptcha_via_vision(page, SCREENSHOT_PATH):
        print("[RECAPTCHA] Challenge найден через скриншот!")
        return True
    print("[RECAPTCHA] Challenge не обнаружен")
    return False


def solve(page, max_rounds=5):
    print("\n" + "█" * 55)
    print("  RECAPTCHA SOLVER" + (" — DEBUG" if _debug() else ""))
    _debug_report()
    print("█" * 55)
    _wait_step("СТАРТ", "Начинаю разгадывание")

    for round_idx in range(max_rounds):
        print(f"\n{'─' * 55}\n  РАУНД {round_idx + 1}/{max_rounds}\n{'─' * 55}")

        if _is_solved(page):
            print("[RECAPTCHA] Уже решено!")
            return True

        challenge_text = get_challenge_text(page)
        print(f"[RECAPTCHA] Текст: \"{challenge_text}\"")

        if not challenge_text:
            print("[RECAPTCHA] Текст не получен, жду 1с...")
            time.sleep(1)
            continue

        bframe_box = _get_bframe_box(page)
        if not bframe_box:
            print("[RECAPTCHA] bframe не найден")
            return False

        print(f"[RECAPTCHA] bframe box: {bframe_box['width']:.0f}x{bframe_box['height']:.0f}")
        bframe = _find_bframe(page)
        bframe_el = bframe.frame_element()
        try:
            is_visible = bframe_el.is_visible()
        except Exception:
            is_visible = False
        if not is_visible:
            print("[RECAPTCHA] bframe невидим — челлендж пропал, перезапускаю")
            return False
        bframe_el.screenshot(path=RECAPTCHA_SCREENSHOT_PATH, type="jpeg", quality=95)

        from PIL import Image
        with Image.open(RECAPTCHA_SCREENSHOT_PATH) as img:
            shot_w, shot_h = img.size
        css_w, css_h = int(bframe_box["width"]), int(bframe_box["height"])
        scale = shot_w / css_w if css_w > 0 else 1.0
        print(f"[RECAPTCHA] скриншот {shot_w}x{shot_h} vs CSS {css_w}x{css_h}, scale={scale:.4f}")

        print(f">>> ОТПРАВЛЯЮ ЗАПРОС В LLM challenge: \"{challenge_text}\"")
        result, raw = ask_llm_for_clicks(challenge_text, RECAPTCHA_SCREENSHOT_PATH, bframe_box)

        if not result:
            print("[RECAPTCHA] LLM не вернула результат")
            time.sleep(1)
            continue

        print(f"[RECAPTCHA] ОТВЕТ LLM: {json.dumps(result, ensure_ascii=False, indent=2)}")
        if isinstance(result, dict):
            result["_scale"] = scale

        if result.get("done"):
            print("[RECAPTCHA] LLM: уже решено")
            return True
        if result.get("skip"):
            print("[RECAPTCHA] LLM: пропустить")
            skip_btn = _get_skip_button(page)
            if skip_btn:
                print(f"[RECAPTCHA] Skip в ({skip_btn[0]}, {skip_btn[1]})")
                click_human_like(page, skip_btn[0], skip_btn[1])
            _random_delay(0.5, 1)
            continue

        raw_clicks = result.get("clicks") or []
        tiles = result.get("tiles") or []
        clicks_data = []
        scale_val = result.get("_scale", 1.0)
        NORM = 1000.0

        if raw_clicks:
            bw = int(bframe_box["width"])
            bh = int(bframe_box["height"])
            for i, pt in enumerate(raw_clicks):
                if not isinstance(pt, dict):
                    print(f"[RECAPTCHA] клик {i} не словарь: {pt}, пропускаю")
                    continue
                x_val = pt.get("x", 0)
                y_val = pt.get("y", 0)
                if isinstance(x_val, (list, tuple)):
                    x_val = sum(x_val) / len(x_val) if x_val else 0
                if isinstance(y_val, (list, tuple)):
                    y_val = sum(y_val) / len(y_val) if y_val else 0
                try:
                    fx, fy = float(x_val), float(y_val)
                except (TypeError, ValueError):
                    print(f"[RECAPTCHA] клик {i} с некорректными координатами: {pt}, пропускаю")
                    continue
                # 1) Модель выдаёт в 0-1000 -> переводим в px скриншота
                nw = shot_w / NORM
                nh = shot_h / NORM
                sx = fx * nw
                sy = fy * nh
                # 2) screenshot px -> CSS px через scale
                cx = sx / scale_val
                cy = sy / scale_val
                if cx > bw or cy > bh or cx < 0 or cy < 0:
                    print(f"[RECAPTCHA] коорд ({fx},{fy})/NORM->({sx:.0f},{sy:.0f})px ВНЕ iframe ({bw}x{bh}) после scale={scale_val} -> CSS({cx:.0f},{cy:.0f}) — пропускаю")
                    continue
                vx = int(bframe_box["x"] + cx)
                vy = int(bframe_box["y"] + cy)
                print(f"[RECAPTCHA] ({fx},{fy})/NORM->({sx:.0f},{sy:.0f})px/scale->CSS({cx:.0f},{cy:.0f})->viewport({vx},{vy})")
                clicks_data.append({"x": vx, "y": vy})

        if not clicks_data and tiles:
            cols = result.get("grid_cols", 3)
            rows = result.get("grid_rows", 3)
            bw = int(bframe_box["width"])
            bh = int(bframe_box["height"])
            cw = bw // cols
            ch = bh // rows
            for idx in tiles:
                col = idx % cols
                row = idx // cols
                vx = int(bframe_box["x"]) + col * cw + cw // 2
                vy = int(bframe_box["y"]) + row * ch + ch // 2
                print(f"[RECAPTCHA] tile {idx} (row={row} col={col}) -> viewport({vx},{vy})")
                clicks_data.append({"x": vx, "y": vy})

        if not clicks_data:
            print("[RECAPTCHA] нет валидных кликов, пропускаю раунд")
            _random_delay(0.5, 1)
            continue

        print(f"[RECAPTCHA] Совпало плиток: {len(clicks_data)}")
        for i, pt in enumerate(clicks_data):
            cx, cy = pt["x"], pt["y"]
            if i == 0:
                time.sleep(0.5)
            print(f"[RECAPTCHA] Клик {i+1}/{len(clicks_data)} -> ({cx}, {cy})")
            click_human_like(page, cx, cy)
            if i == 0:
                _wait_step("ПОСЛЕ КЛИКА 1")
            else:
                _random_delay(0.25, 0.7)

        verify_btn = _get_verify_button(page)
        if verify_btn:
            print(f"[RECAPTCHA] Verify в ({verify_btn[0]}, {verify_btn[1]})")
            _random_delay(0.4, 0.8)
            click_human_like(page, verify_btn[0], verify_btn[1])
        else:
            print("[RECAPTCHA] Verify не найдена")
            _random_delay(0.3, 0.6)

        time.sleep(3)
        if _is_solved(page):
            print("[RECAPTCHA] Решено!")
            return True

        verify_btn = _get_verify_button(page)
        if verify_btn:
            print(f"[RECAPTCHA] Verify в ({verify_btn[0]}, {verify_btn[1]})")
            _random_delay(0.4, 0.8)
            click_human_like(page, verify_btn[0], verify_btn[1])
        else:
            print("[RECAPTCHA] Verify не найдена")
            _random_delay(0.3, 0.6)

        time.sleep(3)

        if _is_solved(page):
            print("[RECAPTCHA] РЕШЕНО!")
            return True

        incorrect = False
        bframe_alive = False
        try:
            bf = _find_bframe(page)
            if bf:
                bframe_alive = True
                incorrect = bf.evaluate("""() => {
                    const el = document.querySelector('.rc-imageselect-incorrect-response');
                    return el && el.style.display !== 'none';
                }""")
        except Exception:
            pass

        if incorrect:
            print("[RECAPTCHA] Неправильно")
            _random_delay(0.8, 1.5)
            continue
        if bframe_alive:
            print("[RECAPTCHA] Новые картинки! Анализирую заново...")
            time.sleep(1)
            continue

        print("[RECAPTCHA] bframe пропал, выход")
        return False

    print("[RECAPTCHA] Лимит попыток")
    return False
