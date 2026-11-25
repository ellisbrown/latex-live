"""The viewer in a browser (browser_checks, which preview runs on its instance): headless Chromium, through
Playwright, loads the viewer and the PDF, opens every sidebar tab, the palette and the source pane, and no page error
or console error comes up meanwhile. Optional: without the playwright package or a Chromium for it, it says so
and checks nothing (pip install playwright, then playwright install chromium; or LATEX_LIVE_CHECK_CHROMIUM, a
Chromium's path). Only the checks use Playwright: latex-live itself needs nothing beyond Python's standard library."""

import os

from .base import fail, ok


def browser_checks(url):
    """Check the viewer at url (with its token, which fail's redaction keeps out of the output) in headless Chromium."""
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        return print("skip: no playwright (pip install playwright; playwright install chromium), so the viewer is not checked"
                     " in a browser", flush=True)
    errors = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(executable_path=os.environ.get("LATEX_LIVE_CHECK_CHROMIUM") or None)
        except Error as e:
            return print(f"skip: no Chromium for playwright ({str(e).strip().splitlines()[0]}), so the viewer is not checked in a"
                         " browser (playwright install chromium, or LATEX_LIVE_CHECK_CHROMIUM)", flush=True)
        try:
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.on("pageerror", lambda e: errors.append(f"page error: {e}"))
            # (a fetch of a PDF that a newer one replaced is aborted: PDF.js's normal net::ERR_ABORTED)
            page.on("console", lambda m: m.type == "error" and "ERR_ABORTED" not in m.text and errors.append(f"console error: {m.text}"))
            page.goto(url)
            page.wait_for_selector(".page canvas", timeout=30_000)
            if page.locator("#side").is_hidden():
                page.click("#toggleSide")
            tabs = page.eval_on_selector_all("#tabs [data-tab]", "tabs => tabs.filter(t => !t.hidden).map(t => t.dataset.tab)")
            if not tabs:
                errors.append("the sidebar has no tabs")
            for tab in tabs:
                page.click(f'#tabs [data-tab="{tab}"]')
                page.wait_for_selector(f"#side #{tab}.panel:not([hidden])", timeout=10_000)
            page.keyboard.press("Meta+k" if page.evaluate("/^Mac|^iP/.test(navigator.platform)") else "Control+k")
            page.wait_for_selector("#paletteBackdrop:not([hidden])", timeout=10_000)
            page.keyboard.press("Escape")
            page.click("#layoutSplit")
            page.wait_for_selector("#editorPane:not([hidden]) .cm-content", timeout=10_000)
            page.wait_for_timeout(500)  # (for what the last click started)
        except Error as e:
            errors.append(f"{type(e).__name__}: {str(e).strip().splitlines()[0]}")
        finally:
            browser.close()
    if errors:
        fail("in a browser, the viewer:\n" + "\n".join(errors))
    ok(f"in a browser (headless Chromium), the viewer loads the PDF and opens every sidebar tab ({len(tabs)}), the palette"
       " and the source pane, with no page or console error")
