import json
import os
import base64
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("LLM_API_KEY", "")
API_URL = os.getenv("API_URL", "http://127.0.0.1:8080/v1/chat/completions")
SCREENSHOT_PATH = os.getenv("SCREENSHOT_PATH", r"W:\_python\OWL\browser_screen.jpg")
SYSTEM_PROMPT_PATH = os.getenv("SYSTEM_PROMPT_PATH", "system_prompt.txt")
JSON_SCHEMA_ENABLED = os.getenv("JSON_SCHEMA_ENABLED", "").lower() in ("1", "true", "yes")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "4000"))

_SYSTEM_PROMPT_HARDCODED = """\
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

def _load_system_prompt():
    try:
        with open(SYSTEM_PROMPT_PATH, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if content:
                return content
    except Exception:
        pass
    return _SYSTEM_PROMPT_HARDCODED

SYSTEM_PROMPT = _load_system_prompt()


def repair_json(text):
    stripped = text.strip()
    if not stripped:
        return stripped
    try:
        json.loads(stripped)
        return stripped
    except json.JSONDecodeError:
        pass
    if stripped.count("{") > stripped.count("}"):
        stripped += "}"
    if stripped.count("[") > stripped.count("]"):
        stripped += "]"
    try:
        json.loads(stripped)
        return stripped
    except json.JSONDecodeError:
        pass
    if stripped.rstrip().endswith('"') and not stripped.rstrip().endswith('\\"'):
        pass
    else:
        idx = stripped.rfind(':"')
        if idx > 0 and not stripped.endswith('"}'):
            stripped += '"}'
    try:
        json.loads(stripped)
        return stripped
    except json.JSONDecodeError:
        pass
    return text


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


def _ask_model_to_fix_json(bad_text):
    """Просит модель извлечь валидный JSON из своего же некорректного ответа."""
    import requests as req
    fix_prompt = f"""The following text was supposed to be valid JSON but is not. Extract ONLY the valid JSON part. If there is no valid JSON, try to fix it by completing it properly.

Text:
```
{bad_text[:2000]}
```

Return ONLY the corrected JSON, no explanations."""

    payload = {
        "model": "gui-owl",
        "messages": [
            {"role": "system", "content": "You extract and fix JSON. Return ONLY valid JSON."},
            {"role": "user", "content": fix_prompt}
        ],
        "temperature": 0.0,
        "max_tokens": 1000,
        "stream": False,
    }

    headers = {}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"

    try:
        r = req.post(API_URL, json=payload, headers=headers, timeout=120)
        r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
        fixed = (msg.get("content") or msg.get("reasoning_content") or "").strip()
        brace = fixed.find("{")
        if brace >= 0:
            fixed = fixed[brace:]
        json.loads(fixed)
        return fixed
    except Exception as e:
        print(f"[REPAIR MODEL] ошибка: {e}")
        return ""


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
        "max_tokens": MAX_TOKENS,
        "stream": False,
    }

    if JSON_SCHEMA_ENABLED:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "browser_action",
                "schema": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["click", "type", "press", "goto", "wait", "done"]
                        },
                        "id": {"type": "string"},
                        "text": {"type": "string"},
                        "key": {"type": "string"},
                        "url": {"type": "string"},
                        "seconds": {"type": "number"},
                        "reason": {"type": "string"}
                    },
                    "required": ["action"],
                    "additionalProperties": False
                }
            }
        }
        print("[JSON_SCHEMA] response_format включён")

    headers = {}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"

    r = requests.post(API_URL, json=payload, headers=headers, timeout=180)
    print("[MODEL STATUS]", r.status_code)
    r.raise_for_status()
    data = r.json()
    msg = data["choices"][0]["message"]
    raw = (msg.get("content") or "").strip()
    if not raw:
        raw = (msg.get("reasoning_content") or "").strip()
        if raw:
            print("[MODEL] content пустой, использую reasoning_content")

    print("[MODEL RAW]", raw[:500])

    if not raw:
        print("[ERROR] Модель вернула пустой ответ")
        print("[MODEL BODY]", r.text[:500])
        return ""

    brace = raw.find("{")
    if brace > 0:
        raw = raw[brace:]

    repaired = repair_json(raw)
    if repaired != raw:
        print("[REPAIR JSON] было:", raw[-100:])
        print("[REPAIR JSON] стало:", repaired[-100:])

    try:
        json.loads(repaired)
        return repaired
    except json.JSONDecodeError:
        print("[REPAIR JSON] даже repair_json не помог. Прошу модель исправить...")
        fixed = _ask_model_to_fix_json(raw)
        if fixed:
            print("[REPAIR JSON] модель исправила:", fixed[:200])
            return fixed
        print("[REPAIR JSON] модель не смогла исправить, возвращаю как есть")
        return repaired
