import subprocess
import sys
import os

REQUIREMENTS_FILE = "requirements.txt"


def run(cmd, desc):
    print(f"\n==> {desc}...")
    result = subprocess.run(cmd, shell=True)
    if result.returncode != 0:
        print(f"[FAIL] {desc} (код {result.returncode})")
        sys.exit(1)
    print(f"[OK] {desc}")
    return result


def check_imports():
    modules = ["playwright", "pyautogui", "pygetwindow", "requests"]
    for m in modules:
        try:
            __import__(m)
            print(f"  [OK] {m} imported")
        except ImportError:
            print(f"  [FAIL] {m} не импортируется")
            sys.exit(1)


def main():
    print("=" * 50)
    print("  Установка OWL Browser Agent")
    print("=" * 50)

    print(f"\nPython: {sys.version}")

    if not os.path.exists(REQUIREMENTS_FILE):
        print(f"[FAIL] {REQUIREMENTS_FILE} не найден")
        sys.exit(1)

    run(
        f"{sys.executable} -m pip install -r {REQUIREMENTS_FILE}",
        "Установка Python-зависимостей"
    )

    run(
        f"{sys.executable} -m playwright install chromium",
        "Установка браузера Chromium для Playwright"
    )

    print("\n==> Проверка импорта модулей...")
    check_imports()
    print("[OK] Все модули импортируются")

    print("\n" + "=" * 50)
    print("  Установка завершена!")
    print(f"  Запуск: {sys.executable} owl_agent.py")
    print("=" * 50)


if __name__ == "__main__":
    main()
