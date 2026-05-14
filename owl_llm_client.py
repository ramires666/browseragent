import os
import requests
from dotenv import load_dotenv

load_dotenv()

_config = None


def get_config():
    global _config
    if _config is not None:
        return _config

    backend = os.getenv("LLM_BACKEND", "llama").strip().lower()
    if backend != "vllm":
        backend = "llama"

    defaults = {
        "llama": "http://127.0.0.1:8080/v1/chat/completions",
        "vllm": "http://127.0.0.1:8000/v1/chat/completions",
    }

    _config = {
        "backend": backend,
        "url": os.getenv("API_URL", defaults[backend]),
        "api_key": os.getenv("LLM_API_KEY", ""),
        "model": os.getenv("LLM_MODEL", "gui-owl"),
    }
    return _config


def reset_config():
    global _config
    _config = None


def make_request(payload, timeout=180, tag=""):
    cfg = get_config()

    if not payload.get("model"):
        payload = {**payload, "model": cfg["model"]}

    headers = {}
    if cfg["api_key"]:
        headers["Authorization"] = f"Bearer {cfg['api_key']}"

    prefix = tag or cfg["backend"].upper()

    try:
        r = requests.post(cfg["url"], json=payload, headers=headers, timeout=timeout)
        print(f"[{prefix} STATUS] {r.status_code}")
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[{prefix}] Ошибка: {e}")
        return None
