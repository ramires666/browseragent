import json
import time
import base64
import traceback
import requests
from playwright.sync_api import sync_playwright

API_URL = "http://127.0.0.1:8080/v1/chat/completions"
SCREENSHOT_PATH = r"W:\_python\OWL\browser_screen.jpg"

SYSTEM_PROMPT = """
You are a browser automation agent.

You receive:
1) the user's task
2) a browser screenshot
3) a list of visible interactive elements with unique ids
4) the currently focused element id if any
5) the recent action history

Return exactly one JSON action at a time.

Allowed actions:
{"action":"click","id":"e3","reason":"..."}
{"action":"type","id":"e5","text":"GUI-Owl","reason":"..."}
{"action":"press","key":"Enter","reason":"..."}
{"action":"goto","url":"https://example.com","reason":"..."}
{"action":"wait","seconds":2,"reason":"..."}
{"action":"done","reason":"task completed"}

Rules:
- Return only valid JSON.
- Prefer using the provided element ids.
- Use only ids that exist in the element list.
- Do not repeat the same click on the same element if it is already focused and nothing changed.
- If the goal is to enter text into an input or textarea, prefer {"action":"type", ...} directly instead of click first.
- If an input/textarea is already focused, prefer type or press.
- One action per step.
- If the task is complete, return done.
"""

def annotate_and_extract_elements(page):
    script = r"""
() => {
    const old = document.querySelectorAll('[data-ai-id]');
    old.forEach(el => el.removeAttribute('data-ai-id'));

    function isVisible(el) {
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return (
            style &&
            style.visibility !== 'hidden' &&
            style.display !== 'none' &&
            rect.width > 4 &&
            rect.height > 4 &&
            rect.bottom >= 0 &&
            rect.right >= 0 &&
            rect.top <= (window.innerHeight || document.documentElement.clientHeight) &&
            rect.left <= (window.innerWidth || document.documentElement.clientWidth)
        );
    }

    function textOf(el) {
        return (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 120);
    }

    function labelOf(el) {
        return (
            el.getAttribute('aria-label') ||
            el.getAttribute('placeholder') ||
            el.getAttribute('title') ||
            el.getAttribute('name') ||
            ''
        ).trim().slice(0, 120);
    }

    const selectors = [
        'input',
        'textarea',
        'button',
        'select',
        'a[href]',
        '[role="button"]',
        '[contenteditable="true"]',
        'input[type="submit"]',
        'input[type="button"]',
        'div[role="button"]'
    ];

    const nodes = Array.from(document.querySelectorAll(selectors.join(',')));
    const result = [];
    let idx = 0;

    for (const el of nodes) {
        if (!isVisible(el)) continue;

        const id = `e${idx++}`;
        el.setAttribute('data-ai-id', id);

        const rect = el.getBoundingClientRect();
        result.push({
            "id": id,
            "tag": el.tagName.toLowerCase(),
            "type": (el.getAttribute('type') || '').toLowerCase(),
            "text": textOf(el),
            "label": labelOf(el),
            "x": Math.round(rect.left + rect.width / 2),
            "y": Math.round(rect.top + rect.height / 2)
        });
    }

    return result;
}
"""
    return page.evaluate(script)

def get_focused_id(page):
    script = r"""
() => {
    const el = document.activeElement;
    if (!el) return null;
    return el.getAttribute('data-ai-id');
}
"""
    return page.evaluate(script)

def ask_model(task, screenshot_path, elements, current_url, current_title, focused_id, history):
    with open(screenshot_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("utf-8")

    element_lines = []
    for el in elements[:80]:
        element_lines.append(
            f'{el["id"]}: tag={el["tag"]}, type={el["type"]}, label="{el["label"]}", text="{el["text"]}", x={el["x"]}, y={el["y"]}'
        )
    element_text = "\n".join(element_lines)

    history_lines = []
    for i, h in enumerate(history[-8:], 1):
        history_lines.append(f"{i}. {json.dumps(h, ensure_ascii=False)}")
    history_text = "\n".join(history_lines) if history_lines else "No history yet."

    user_text = f"""Task: {task}

Current URL: {current_url}
Current title: {current_title}
Focused element id: {focused_id}

Recent history:
{history_text}

Visible interactive elements:
{element_text}
"""

    payload = {
        "model": "gui-owl",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
                ]
            }
        ],
        "temperature": 0.1,
        "max_tokens": 300,
        "stream": False
    }

    r = requests.post(API_URL, json=payload, timeout=180)
    print("[MODEL STATUS]", r.status_code)
    print("[MODEL BODY]", r.text)
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"]

def resolve_locator(page, element_id):
    return page.locator(f'[data-ai-id="{element_id}"]').first

def same_action(a, b):
    if not a or not b:
        return False
    return json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)

def do_action(page, action):
    kind = action.get("action")
    print("[ACTION]", action)

    if kind == "goto":
        page.goto(action["url"], wait_until="domcontentloaded")
        return False

    if kind == "click":
        locator = resolve_locator(page, action["id"])
        locator.wait_for(state="visible", timeout=10000)
        locator.click(timeout=10000)
        return False

    if kind == "type":
        locator = resolve_locator(page, action["id"])
        locator.wait_for(state="visible", timeout=10000)
        locator.fill(action["text"], timeout=10000)
        return False

    if kind == "press":
        page.keyboard.press(action["key"])
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

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page(viewport={"width": 1440, "height": 960})
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
                finished = do_action(page, action)
            except Exception as e:
                print("[ERROR] Не удалось выполнить действие")
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

        browser.close()
        print("[EXIT]")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[FATAL]", e)
        traceback.print_exc()
        input("Нажми Enter для выхода...")