from playwright.sync_api import sync_playwright, Page

BROWSER_VIEWPORT = {"width": 1440, "height": 960}

def create_browser(headless=False):
    p = sync_playwright().start()
    browser = p.chromium.launch(headless=headless)
    page = browser.new_page(viewport=BROWSER_VIEWPORT)
    return p, browser, page


def close_browser(playwright, browser):
    try:
        browser.close()
    except Exception:
        pass
    try:
        playwright.stop()
    except Exception:
        pass


def annotate_and_extract_elements(page: Page):
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


def get_focused_id(page: Page):
    return page.evaluate("""() => {
        const el = document.activeElement;
        if (!el) return null;
        return el.getAttribute('data-ai-id');
    }""")


def resolve_locator(page: Page, element_id: str):
    return page.locator(f'[data-ai-id="{element_id}"]').first
