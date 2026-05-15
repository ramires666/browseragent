"""
СИНТЕТИЧЕСКИЙ ТЕСТ КИКАТЕЛЯ
============================
Создаёт страницу с iframe (имитация bframe), внутри 3x3 сетка кнопок.
Каждый клик пишет номер тайла в window.parent.clickHistory.
Прогоняем click_human_like для каждого из 9 тайлов и проверяем что
реально кликнулся именно нужный тайл.

Если click_human_like мажет — лог покажет где промах и насколько.
"""
import os
import sys
import time
import json
from owl_browser import create_browser, close_browser
from owl_clicker import click_human_like, viewport_to_screen
import pyautogui


# HTML страницы: main page с iframe в позиции (left=100, top=50).
# Внутри iframe ещё одна обёртка с верхним header (имитирует rc-imageselect-payload).
# Сетка 3x3 кнопок 100x100, каждая onclick -> parent.recordClick(N).
IFRAME_HTML = """<!DOCTYPE html>
<html><head><style>
  *{box-sizing:border-box;margin:0;padding:0;font-family:Arial,sans-serif}
  body{background:#fafafa}
  .header{height:80px;background:#4285f4;color:#fff;padding:16px;font-size:16px}
  .grid{display:grid;grid-template-columns:repeat(3,100px);grid-template-rows:repeat(3,100px);gap:2px;padding:8px;background:#222}
  .rc-imageselect-tile{
    width:100px;height:100px;border:none;cursor:pointer;font-size:42px;
    font-weight:bold;color:#000;display:flex;align-items:center;justify-content:center;
  }
  .rc-imageselect-tile:hover{outline:3px solid yellow}
</style></head><body>
<div class="header">CLICK on tile N (synthetic test)</div>
<div class="grid">
  <button class="rc-imageselect-tile" style="background:#ff8a80" onclick="parent.recordClick(1, this)">1</button>
  <button class="rc-imageselect-tile" style="background:#ffd180" onclick="parent.recordClick(2, this)">2</button>
  <button class="rc-imageselect-tile" style="background:#ffff8d" onclick="parent.recordClick(3, this)">3</button>
  <button class="rc-imageselect-tile" style="background:#ccff90" onclick="parent.recordClick(4, this)">4</button>
  <button class="rc-imageselect-tile" style="background:#a7ffeb" onclick="parent.recordClick(5, this)">5</button>
  <button class="rc-imageselect-tile" style="background:#80d8ff" onclick="parent.recordClick(6, this)">6</button>
  <button class="rc-imageselect-tile" style="background:#b388ff" onclick="parent.recordClick(7, this)">7</button>
  <button class="rc-imageselect-tile" style="background:#ff80ab" onclick="parent.recordClick(8, this)">8</button>
  <button class="rc-imageselect-tile" style="background:#ea80fc" onclick="parent.recordClick(9, this)">9</button>
</div>
</body></html>"""


def _build_main_html():
    iframe_srcdoc = IFRAME_HTML.replace('"', '&quot;')
    return f"""<!DOCTYPE html>
<html><head><style>
  body{{margin:0;padding:0;background:#eef}}
  .wrap{{position:absolute;left:100px;top:50px;border:1px solid #888}}
  iframe{{border:none;width:324px;height:404px;display:block}}
</style></head><body>
<h2 style="margin:8px">SYNTHETIC CLICKER TEST</h2>
<div class="wrap"><iframe id="testframe" srcdoc="{iframe_srcdoc}"></iframe></div>
<script>
  window.clickHistory = [];
  window.lastClicked = null;
  window.lastClickRect = null;
  window.recordClick = function(n, el) {{
    window.lastClicked = n;
    window.clickHistory.push(n);
    if (el) {{
      const r = el.getBoundingClientRect();
      window.lastClickRect = {{
        n: n,
        left: r.left, top: r.top, width: r.width, height: r.height,
        cx: r.left + r.width/2, cy: r.top + r.height/2,
      }};
    }}
    console.log('CLICK', n);
  }};
</script>
</body></html>"""


def _find_test_iframe(page):
    """Находит наш тестовый iframe в page.frames."""
    for f in page.frames:
        if f != page.main_frame:
            return f
    return None


def _get_test_tiles(page, frame):
    """Аналог _get_tiles но для тестового iframe (не привязан к 'bframe' URL)."""
    iframe_box = frame.frame_element().bounding_box()
    if not iframe_box:
        return None, None
    tiles_rel = frame.evaluate("""() => {
        const cells = document.querySelectorAll('.rc-imageselect-tile');
        return Array.from(cells).map((el, i) => {
            const r = el.getBoundingClientRect();
            return {index:i, cx:r.left+r.width/2, cy:r.top+r.height/2,
                    left:r.left, top:r.top, w:r.width, h:r.height};
        });
    }""")
    if not tiles_rel:
        return None, None
    tiles = []
    for t in tiles_rel:
        tile_x = int(iframe_box["x"] + t["cx"])
        tile_y = int(iframe_box["y"] + t["cy"])
        tiles.append({
            "index": t["index"],
            "x": tile_x, "y": tile_y,
            "iframe_left": t["left"], "iframe_top": t["top"],
            "iframe_w": t["w"], "iframe_h": t["h"],
        })
    return tiles, iframe_box


def _get_element_under_cursor(page, sx_phys, sy_phys):
    """Обратная конверсия: физические pyautogui-пиксели -> viewport CSS-пиксели.
    Использует тот же DPI scale что viewport_to_screen."""
    from owl_clicker import _DPI_SCALE
    info = page.evaluate("""() => ({
        screenX: window.screenX, screenY: window.screenY,
        outerW: window.outerWidth, outerH: window.outerHeight,
        innerW: window.innerWidth, innerH: window.innerHeight,
        dpr: window.devicePixelRatio
    })""")
    w_diff = info["outerW"] - info["innerW"]
    h_diff = info["outerH"] - info["innerH"]
    border_w = w_diff // 2
    top_chrome = h_diff - border_w
    # phys -> css
    sx_css = sx_phys / _DPI_SCALE
    sy_css = sy_phys / _DPI_SCALE
    vx = sx_css - info["screenX"] - border_w
    vy = sy_css - info["screenY"] - top_chrome
    return int(round(vx)), int(round(vy)), info


def run_test():
    """Runs the synthetic clicker test.

    Returns (clicker_ok, p, browser, page, frame, tiles, iframe_box) so the
    caller can reuse the same browser session for the LLM phase.
    """
    print("\n" + "="*60)
    print("  СИНТЕТИЧЕСКИЙ ТЕСТ КИКАТЕЛЯ")
    print("="*60)
    p, browser, page = create_browser(headless=False)

    page.set_content(_build_main_html())
    page.wait_for_timeout(800)  # wait for iframe load
    page.bring_to_front()
    page.wait_for_timeout(400)

    frame = _find_test_iframe(page)
    assert frame is not None, "iframe not found"

    # Получаем DPI и системную инфу
    info = page.evaluate("""() => ({
        screenX:window.screenX, screenY:window.screenY,
        outerW:window.outerWidth, outerH:window.outerHeight,
        innerW:window.innerWidth, innerH:window.innerHeight,
        dpr: window.devicePixelRatio
    })""")
    screen_size = pyautogui.size()
    print(f"\nСИСТЕМА:")
    print(f"  pyautogui screen size: {screen_size}")
    print(f"  window.devicePixelRatio: {info['dpr']}")
    print(f"  window.screenX/Y: ({info['screenX']}, {info['screenY']})")
    print(f"  window.outer: {info['outerW']}x{info['outerH']}")
    print(f"  window.inner: {info['innerW']}x{info['innerH']}")

    tiles, iframe_box = _get_test_tiles(page, frame)
    assert tiles and len(tiles) == 9, f"Expected 9 tiles, got {len(tiles) if tiles else 0}"
    print(f"\nIFRAME box: x={iframe_box['x']:.0f} y={iframe_box['y']:.0f} "
          f"w={iframe_box['width']:.0f} h={iframe_box['height']:.0f}")
    print(f"TILES (viewport coords):")
    for t in tiles:
        print(f"  tile {t['index']+1}: viewport=({t['x']},{t['y']})  "
              f"iframe_rect=(left={t['iframe_left']:.0f},top={t['iframe_top']:.0f},"
              f"w={t['iframe_w']:.0f},h={t['iframe_h']:.0f})")

    # === ТЕСТЫ ===
    results = []
    for target in range(1, 10):
        t = tiles[target - 1]
        vx, vy = t["x"], t["y"]

        # reset history
        page.evaluate("() => { window.lastClicked = null; window.lastClickRect = null; }")

        sx, sy = viewport_to_screen(page, vx, vy)
        pyautogui.moveTo(sx, sy, duration=0.15)
        pos_after_move = pyautogui.position()

        # вычислить где курсор по версии страницы
        vx_back, vy_back, _ = _get_element_under_cursor(page, pos_after_move.x, pos_after_move.y)

        print(f"\n--- TARGET tile {target} ---")
        print(f"  expected viewport: ({vx},{vy})")
        print(f"  viewport_to_screen -> ({sx},{sy})")
        print(f"  pyautogui.position() after moveTo: ({pos_after_move.x},{pos_after_move.y})")
        print(f"  reverse-converted viewport: ({vx_back},{vy_back})  delta=({vx_back-vx},{vy_back-vy})")

        click_human_like(page, vx, vy)
        page.wait_for_timeout(150)

        clicked = page.evaluate("() => window.lastClicked")
        click_rect = page.evaluate("() => window.lastClickRect")

        ok = (clicked == target)
        results.append({
            "target": target, "clicked": clicked, "ok": ok,
            "viewport": [vx, vy], "screen": [sx, sy],
            "cursor": [pos_after_move.x, pos_after_move.y],
            "reverse_viewport": [vx_back, vy_back],
            "delta_viewport": [vx_back - vx, vy_back - vy],
        })
        mark = "OK " if ok else "FAIL"
        print(f"  {mark} clicked={clicked} (expected {target})")

    # === SUMMARY ===
    print("\n" + "="*60)
    print("  РЕЗУЛЬТАТ")
    print("="*60)
    ok_count = sum(1 for r in results if r["ok"])
    print(f"Попадание: {ok_count}/9")
    for r in results:
        mark = "OK " if r["ok"] else "FAIL"
        print(f"  {mark} target={r['target']} clicked={r['clicked']}  "
              f"viewport={r['viewport']}  screen={r['screen']}  "
              f"cursor_after={r['cursor']}  delta_v={r['delta_viewport']}")

    # Сохранить json для анализа
    with open("_synth_click_result.json", "w", encoding="utf-8") as f:
        json.dump({"info": info, "screen_size": list(screen_size),
                   "iframe_box": iframe_box, "tiles": tiles, "results": results},
                  f, ensure_ascii=False, indent=2)
    print(f"\nЛог сохранён в _synth_click_result.json")
    clicker_ok = (ok_count == 9)
    return clicker_ok, p, browser, page, frame, tiles, iframe_box


def run_llm_test(page, frame, tiles, iframe_box):
    """LLM end-to-end phase: screenshot iframe -> overlay -> ask LLM -> click -> verify."""
    from owl_recaptcha import _build_numbered_overlay
    from owl_recaptcha_llm import ask_llm_for_tile_indices
    from PIL import Image

    SHOT_PATH = "_synth_iframe.jpg"
    NUMBERED_PATH = "_synth_numbered.jpg"

    print("\n" + "="*60)
    print("  LLM END-TO-END ТЕСТ")
    print("="*60)

    # Screenshot of just the iframe
    frame.frame_element().screenshot(path=SHOT_PATH, type="jpeg", quality=95)
    with Image.open(SHOT_PATH) as img:
        shot_w, shot_h = img.size
    print(f"  iframe screenshot: {shot_w}x{shot_h}  iframe CSS: {iframe_box['width']:.0f}x{iframe_box['height']:.0f}")
    scale = shot_w / iframe_box["width"] if iframe_box["width"] > 0 else 1.0
    print(f"  scale = {scale:.4f}")

    # Build numbered overlay
    result_path, num_tiles = _build_numbered_overlay(
        SHOT_PATH, tiles, iframe_box, scale, NUMBERED_PATH, upscale=2
    )
    if not result_path:
        print("  ERROR: _build_numbered_overlay вернула None — нет PIL? Пропускаем LLM тест.")
        return False, []

    # Define rounds: (question, expected_tile_set_1based)
    rounds = [
        ("select all green or teal coloured tiles", {4, 5}),
        ("select the orange tile", {2}),
        ("select the blue tile", {6}),
    ]

    round_results = []
    clicker_all_ok = True  # N/A (empty from LLM) does NOT fail

    print(f"\n  {'Round':<6} {'Question':<45} {'LLM tiles':<14} {'Expected':<12} "
          f"{'llm_reasonable':<16} {'clicker_ok'}")
    print("  " + "-"*110)

    for i, (question, expected_set) in enumerate(rounds, 1):
        print(f"\n--- LLM ROUND {i}: \"{question}\" ---")

        # Reset DOM history
        page.evaluate("() => { window.clickHistory = []; window.lastClicked = null; }")

        llm_result, raw_text = ask_llm_for_tile_indices(question, NUMBERED_PATH, 9)

        if llm_result is None or "tiles" not in llm_result:
            print(f"  LLM returned None or no 'tiles' key. raw={str(raw_text)[:200]}")
            round_results.append({
                "round": i, "question": question, "expected": sorted(expected_set),
                "llm_tiles": None, "clicker_ok": "N/A", "llm_reasonable": False,
                "raw": str(raw_text)[:500],
            })
            print(f"  {'Round':<6} {'Question':<45} {'N/A':<14} {str(sorted(expected_set)):<12} "
                  f"{'False':<16} N/A")
            continue

        llm_tiles = llm_result.get("tiles", [])
        reason = llm_result.get("reason", "")
        print(f"  LLM tiles={llm_tiles}  reason={reason!r}")

        # Click each tile the LLM chose
        for n in llm_tiles:
            if 1 <= n <= len(tiles):
                click_human_like(page, tiles[n - 1]["x"], tiles[n - 1]["y"])
                page.wait_for_timeout(150)
            else:
                print(f"  WARNING: LLM returned tile index {n} out of range 1..{len(tiles)}, skipping")

        # Read back what was actually clicked
        history = page.evaluate("() => window.clickHistory")

        # clicker_ok: history matches llm_tiles exactly (order matters per spec)
        if not llm_tiles:
            clicker_ok_val = "N/A"
        else:
            clicker_ok_val = (history == llm_tiles)
            if not clicker_ok_val:
                clicker_all_ok = False

        llm_reasonable = bool(set(llm_tiles) & expected_set)

        print(f"  clickHistory={history}  clicker_ok={clicker_ok_val}  "
              f"llm_reasonable={llm_reasonable}  expected={sorted(expected_set)}")

        round_results.append({
            "round": i, "question": question, "expected": sorted(expected_set),
            "llm_tiles": llm_tiles, "clicker_ok": clicker_ok_val,
            "llm_reasonable": llm_reasonable, "reason": reason,
            "click_history": history,
        })

        print(f"  {'Round':<6} {question:<45} {str(llm_tiles):<14} {str(sorted(expected_set)):<12} "
              f"{str(llm_reasonable):<16} {clicker_ok_val}")

    # Summary table
    print("\n" + "="*60)
    print("  LLM ТЕСТ — ИТОГ")
    print("="*60)
    print(f"  {'#':<4} {'Question':<45} {'LLM':<14} {'Expected':<12} {'Reasonable':<12} {'ClickerOK'}")
    print("  " + "-"*100)
    for r in round_results:
        print(f"  {r['round']:<4} {r['question']:<45} {str(r['llm_tiles']):<14} "
              f"{str(r['expected']):<12} {str(r['llm_reasonable']):<12} {r['clicker_ok']}")

    # Save results
    with open("_synth_llm_result.json", "w", encoding="utf-8") as f:
        json.dump({
            "shot_path": SHOT_PATH,
            "numbered_path": NUMBERED_PATH,
            "shot_size": [shot_w, shot_h],
            "scale": scale,
            "rounds": round_results,
            "clicker_all_ok": clicker_all_ok,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n  Результаты сохранены в _synth_llm_result.json")
    return clicker_all_ok, round_results


if __name__ == "__main__":
    clicker_ok, p, browser, page, frame, tiles, iframe_box = run_test()
    try:
        if not clicker_ok:
            print("\nКликатель провалился (не 9/9) — LLM тест пропущен.")
            sys.exit(1)

        print("\n9/9 — запускаю LLM тест в том же браузере...")
        llm_all_ok, round_results = run_llm_test(page, frame, tiles, iframe_box)

        overall = clicker_ok and llm_all_ok
        print("\n" + "="*60)
        print(f"  ИТОГ: clicker={'PASS' if clicker_ok else 'FAIL'}  "
              f"llm_clicker={'PASS' if llm_all_ok else 'FAIL'}  "
              f"OVERALL={'PASS' if overall else 'FAIL'}")
        print("="*60)
        sys.exit(0 if overall else 1)
    finally:
        page.wait_for_timeout(500)
        close_browser(p, browser)
