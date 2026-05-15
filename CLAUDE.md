# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

OWL is a vision-based browser automation agent. It takes screenshots of web pages, sends them to a local LLM (vllm or llama), and executes the returned JSON actions via Playwright (DOM) and PyAutoGUI (OS-level mouse/keyboard). Designed for Russian-language tasks; includes reCAPTCHA solving and Google anti-bot handling.

## Setup & Running

```bash
# Install dependencies and Playwright Chromium
python install.py

# Run the agent (reads task from task.txt or prompts interactively)
python owl_agent.py

# Quick LLM API connectivity test
python test_gui_owl.py
```

## Configuration

Copy `.env.example` to `.env`. Key variables:

| Variable | Description |
|----------|-------------|
| `LLM_BACKEND` | `vllm` (port 8000) or `llama` (port 8080) |
| `API_URL` | OpenAI-compatible endpoint, e.g. `http://127.0.0.1:9999/v1/chat/completions` |
| `LLM_MODEL` | Model name sent to API |
| `LLM_API_KEY` | Bearer token |
| `SCREENSHOT_PATH` | Where browser screenshots are saved |
| `SYSTEM_PROMPT_PATH` | Path to `system_prompt.txt` |
| `JSON_SCHEMA_ENABLED` | `false` (set `true` only if backend supports OpenAI JSON schema) |
| `MAX_TOKENS` | Max tokens in LLM response (default 3000) |
| `RECAPTCHA_DEBUG` | `true` to pause at each captcha step for manual inspection |
| `COOKIE_PATH` | JSON file for persisting browser cookies |

The task to execute is read from `task.txt` at startup (or prompted if absent).

## Architecture

```
owl_agent.py          — Main loop: coordinates browser, LLM, clicker, captcha
owl_browser.py        — Playwright wrapper: annotates DOM elements with IDs, extracts visible elements
owl_llm.py            — LLM interface: formats prompts with screenshot+elements, parses/repairs JSON
owl_llm_client.py     — Backend abstraction: handles llama vs vllm HTTP differences
owl_clicker.py        — PyAutoGUI wrapper: human-like mouse/keyboard, Cyrillic input, RU/EN layout switching
owl_recaptcha.py      — Detects Google blocks and reCAPTCHA iframes, orchestrates solving flow
owl_recaptcha_llm.py  — Vision-based captcha solver: sends screenshot to LLM, maps tiles to 1000×1000 grid
owl_task_plans.py     — Reusable multi-step instruction templates injected into LLM prompts
```

**Action loop (owl_agent.py):**
1. Screenshot browser → `owl_llm.py` formats prompt → `owl_llm_client.py` POSTs to LLM
2. LLM returns JSON action: `{action: "click"|"type"|"press"|"goto"|"wait"|"done", ...}`
3. `owl_agent.py` executes via `owl_clicker.py` (OS-level) or Playwright (DOM)
4. If Google block / reCAPTCHA detected → `owl_recaptcha.py` → `owl_recaptcha_llm.py`
5. Repeat up to 20 steps

## Key Behaviors

- **Coordinate system:** LLM operates on a 1000×1000 normalized grid; `owl_clicker.py` converts to actual screen pixels accounting for DPI and Playwright viewport (1440×960).
- **Cyrillic input:** Windows API (`ctypes`) switches keyboard layout before typing Russian text; `pyperclip` clipboard paste is used as fallback.
- **Element lookup:** Primary = Playwright DOM element by annotated ID; fallback = LLM vision to locate coordinates in screenshot.
- **Cookie persistence:** Cookies saved after each action to `COOKIE_PATH`; loaded on next run.
- **Human-like behavior:** Random mouse jitter, curved paths, random delays between actions.
