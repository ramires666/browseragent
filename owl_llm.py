import json
import os
import base64
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("LLM_API_KEY", "")
API_URL = os.getenv("API_URL", "http://127.0.0.1:8080/v1/chat/completions")
SCREENSHOT_PATH = os.getenv("SCREENSHOT_PATH", r"W:\_python\OWL\browser_screen.jpg")

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


def build_element_text(elements):
    lines = []
    for el in elements[:80]:
        lines.append(
            f'{el["id"]}: tag={el["tag"]}, type={el["type"]}, label="{el["label"]}", text="{el["text"]}", x={el["x"]}, y={el["y"]}'
        )
    return "\n".join(lines)


def build_history_text(history):
    lines = []
    for i, h in enumerate(history[-8:], 1):
        lines.append(f"{i}. {json.dumps(h, ensure_ascii=False)}")
    return "\n".join(lines) if lines else "No history yet."


def ask_model(task, screenshot_path, elements, current_url, current_title, focused_id, history):
    with open(screenshot_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("utf-8")

    element_text = build_element_text(elements)
    history_text = build_history_text(history)

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

    headers = {}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"

    r = requests.post(API_URL, json=payload, headers=headers, timeout=180)
    print("[MODEL STATUS]", r.status_code)
    print("[MODEL BODY]", r.text)
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"]
