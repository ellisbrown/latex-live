"""The viewer in a browser (browser_checks, which preview runs on its instance, and shared mode's checks as each
role): headless Chromium, through Playwright, loads the viewer and the PDF, opens every sidebar tab, the shortcuts'
list, the palette and (for an editor) the source pane, and no page error or console error comes up meanwhile, nor
anything the page's Content-Security-Policy refuses; and the keyboard gets around (the sidebar's tabs, the shortcuts'
dialog, the palette's box, a file's row in the Files tab), with the tabs in one row; and looking again in the Files tab with
nothing changed draws no row anew. Optional: without the playwright
package or a Chromium for it, it says so
and checks nothing (pip install playwright, then playwright install chromium; or LATEX_LIVE_CHECK_CHROMIUM, a
Chromium's path). Only the checks use Playwright: latex-live itself needs nothing beyond Python's standard library."""

import json
import os

from .base import fail, ok


def browser_checks(url, editor=True, answer=None, who="the viewer"):
    """Check the viewer at url (with its token, which fail's redaction keeps out of the output) in headless Chromium;
    without editor, not the source pane (a viewer's or commenter's). answer: for each request the browser makes,
    (status, [(header, value)], body) to answer it with, instead of the network (shared mode's trusted proxy)."""
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
            if answer:
                origin = "/".join(url.split("/", 3)[:3])
                page.route("**/*", lambda route: fulfil(route, answer, origin))
            page.add_init_script("addEventListener('securitypolicyviolation', (e) => console.error("
                                 "`CSP refused ${e.violatedDirective}: ${e.blockedURI || 'inline'}`))")
            page.on("pageerror", lambda e: errors.append(f"page error: {e}"))
            # (a fetch of a PDF that a newer one replaced is aborted: PDF.js's normal net::ERR_ABORTED)
            page.on("console", lambda m: m.type == "error" and "ERR_ABORTED" not in m.text and errors.append(f"console error: {m.text}"))
            policy = page.goto(url).header_value("content-security-policy") or ""
            if "script-src 'self'" not in policy or "object-src 'none'" not in policy:
                errors.append(f"the page's Content-Security-Policy is {policy!r}")
            page.wait_for_selector(".page canvas", timeout=30_000)
            if page.locator("#welcome").is_visible():  # (the welcome guide, a coauthor's first visit in shared mode)
                page.click("#welcome .ok")
            if page.locator("#side").is_hidden():
                page.click("#toggleSide")
            tabs = page.eval_on_selector_all("#tabs [data-tab]", "tabs => tabs.filter(t => !t.hidden).map(t => t.dataset.tab)")
            if not tabs:
                errors.append("the sidebar has no tabs")
            for tab in tabs:
                page.click(f'#tabs [data-tab="{tab}"]')
                page.wait_for_selector(f"#side #{tab}.panel:not([hidden])", timeout=10_000)
            # The keyboard: the tabs are one stop, and the arrow keys move along them.
            page.focus(f'#tabs [data-tab="{(tabs or ["outline"])[-1]}"]')
            page.keyboard.press("Home")
            if tabs and page.evaluate("document.activeElement.dataset.tab") != tabs[0]:
                errors.append("Home in the sidebar's tabs doesn't go to the first tab")
            # The shortcuts' list is a dialog: it takes the focus, keeps Tab in, and gives the focus back on Esc.
            page.keyboard.press("?")
            page.wait_for_selector("#keys:not([hidden])", timeout=10_000)
            page.keyboard.press("Tab")
            if not page.evaluate("!!document.activeElement.closest('#keys')"):
                errors.append("the shortcuts' list doesn't keep the focus")
            page.keyboard.press("Escape")
            if page.evaluate("document.activeElement.dataset.tab") != (tabs or [None])[0] or page.locator("#keys").is_visible():
                errors.append("Esc doesn't close the shortcuts' list, giving the focus back")
            page.keyboard.press("Meta+k" if page.evaluate("/^Mac|^iP/.test(navigator.platform)") else "Control+k")
            page.wait_for_selector("#paletteBackdrop:not([hidden])", timeout=10_000)
            chosen = page.get_attribute("#paletteInput", "aria-activedescendant")
            if page.get_attribute("#paletteInput", "role") != "combobox" or not chosen or not page.locator(f"#{chosen}[role=option]").count():
                errors.append("the palette's box doesn't say which option is chosen (combobox, aria-activedescendant)")
            page.keyboard.press("Escape")
            if editor:
                page.click("#layoutSplit")
                page.wait_for_selector("#editorPane:not([hidden]) .cm-content", timeout=10_000)
                # With the source pane open, the editor's eight tabs still fit in one row.
                tops = page.eval_on_selector_all("#tabs [data-tab]", "tabs => tabs.filter(t => !t.hidden).map(t => t.offsetTop)")
                if len(set(tops)) > 1:
                    errors.append(f"the sidebar's tabs take {len(set(tops))} rows")
                if "files" in tabs:
                    files_tab_checks(page, errors, redraws=not answer)  # (shared mode: others' comings and goings redraw)
            page.wait_for_timeout(500)  # (for what the last click started)
        except Error as e:
            lines = str(e).strip().splitlines()
            errors.append(f"{type(e).__name__}: {lines[0]}{''.join(f' ({l.strip()})' for l in lines if l.strip().startswith('waiting for'))}")
        finally:
            browser.close()
    if errors:
        fail(f"in a browser, {who}:\n" + "\n".join(errors))
    ok(f"in a browser (headless Chromium), {who} loads the PDF and opens every sidebar tab ({len(tabs)}, in one row), the"
       f" shortcuts, the palette{' and the source pane' if editor else ''}, from the keyboard too, with no page or console"
       " error, nor anything its CSP refuses")


def files_tab_checks(page, errors, redraws=True):
    """The Files tab: a file's row opens from the keyboard; with redraws, looking again with nothing changed draws no row
    anew (the tab draws its tree only when what it shows changes, so that a row keeps the focus)."""
    from playwright.sync_api import Error
    page.click('#tabs [data-tab="files"]')
    for tries in range(3):  # (a row is found anew each time: a build or a change of the list may redraw them meanwhile)
        try:
            row = page.locator('#files .files-file[data-key^="s:"]:not(.on)').first  # (a source: a figure previews)
            row.wait_for(timeout=10_000)
            path = row.get_attribute("data-key")[2:]
            page.locator(f'#files .files-file[data-key={json.dumps("s:" + path)}]').press("Enter")  # (focuses it, then the key)
            # (not wait_for_function, which evaluates a string in the page: its policy refuses that)
            page.wait_for_selector(f"#editorTabs .tab.on[data-path={json.dumps(path)}]", timeout=5_000)
            break
        except Error:
            if tries == 2:
                raise
    if not redraws:
        return
    page.wait_for_selector(f'#files .files-file.on[data-key={json.dumps("s:" + path)}]', timeout=10_000)
    page.locator("#files .files-file").first.evaluate("row => { row.dataset.marked = 'yes'; }")
    with page.expect_response(lambda r: r.url.split("?")[0].endswith("/files"), timeout=10_000):
        page.click("#files .panel-refresh")
    page.wait_for_timeout(300)  # (the answer drawn, if it were)
    if not page.locator("#files .files-file[data-marked=yes]").count():
        errors.append("looking again in the Files tab with nothing changed draws its rows anew")


def fulfil(route, answer, origin):
    """Answer a request of the browser's to origin with answer(method, path and query, body); refuse any other."""
    request = route.request
    if not request.url.startswith(origin + "/"):
        return route.abort()
    status, headers, body = answer(request.method, request.url[len(origin):], request.post_data_buffer)
    route.fulfill(status=status, headers={key: value for key, value in headers if key.lower() != "content-length"}, body=body)
