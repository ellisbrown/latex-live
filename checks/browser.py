"""The viewer in a browser (browser_checks, which preview runs on its instance, and shared mode's checks as each
role): headless Chromium, through Playwright, loads the viewer and the PDF, opens every sidebar tab, the shortcuts'
list, the palette and (for an editor) the source pane, and no page error or console error comes up meanwhile, nor
anything the page's Content-Security-Policy refuses; and the keyboard gets around (the sidebar's tabs, the shortcuts'
dialog, which keeps the viewer's other keys from acting behind it, the palette's box, the Compare panel opened from the
palette, a file's row in the Files tab), with the tabs in one row; looking again in the Files tab with nothing changed
draws no row anew; and a request whose answer's body is cut off, or JSON null, is no success (net.js). Optional: without the playwright
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
    errors, compared = [], False
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(executable_path=os.environ.get("LATEX_LIVE_CHECK_CHROMIUM") or None)
        except Error as e:
            return print(f"skip: no Chromium for playwright ({str(e).strip().splitlines()[0]}), so the viewer is not checked in a"
                         " browser (playwright install chromium, or LATEX_LIVE_CHECK_CHROMIUM)", flush=True)
        def open_page():
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            if answer:
                origin = "/".join(url.split("/", 3)[:3])
                page.route("**/*", lambda route: fulfil(route, answer, origin))
            page.add_init_script("addEventListener('securitypolicyviolation', (e) => console.error("
                                 "`CSP refused ${e.violatedDirective}: ${e.blockedURI || 'inline'}`))")
            page.on("pageerror", lambda e: errors.append(f"page error: {e}"))
            # (a fetch of a PDF that a newer one replaced is aborted: PDF.js's normal net::ERR_ABORTED)
            page.on("console", lambda m: m.type == "error" and "ERR_ABORTED" not in m.text and errors.append(f"console error: {m.text}"))
            return page

        try:
            page = open_page()
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
            highlights = page.evaluate("document.getElementById('changesBtn').classList.contains('on')")
            for key in ("d", "e", "."):
                page.keyboard.press(key)
            if (page.locator("#keys").is_hidden() or page.locator("#editorPane").is_visible()
                    or page.evaluate("document.getElementById('changesBtn').classList.contains('on')") != highlights):
                errors.append("the viewer's shortcuts act behind the shortcuts' list")
            page.keyboard.press("Escape")
            if page.evaluate("document.activeElement.dataset.tab") != (tabs or [None])[0] or page.locator("#keys").is_visible():
                errors.append("Esc doesn't close the shortcuts' list, giving the focus back")
            page.keyboard.press("Meta+k" if page.evaluate("/^Mac|^iP/.test(navigator.platform)") else "Control+k")
            page.wait_for_selector("#paletteBackdrop:not([hidden])", timeout=10_000)
            chosen = page.get_attribute("#paletteInput", "aria-activedescendant")
            if page.get_attribute("#paletteInput", "role") != "combobox" or not chosen or not page.locator(f"#{chosen}[role=option]").count():
                errors.append("the palette's box doesn't say which option is chosen (combobox, aria-activedescendant)")
            page.keyboard.press("Escape")
            if page.evaluate("!document.getElementById('compare').hidden"):  # (in the ⋯ menu, which is closed: its own hidden)
                compare_checks(page, errors)
                compared = True
            net_checks(page, errors)
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
            if not answer:  # (the owner's preview: the same code as a coauthor's)
                cut_checks(open_page(), url, errors)
        except Error as e:
            lines = str(e).strip().splitlines()
            errors.append(f"{type(e).__name__}: {lines[0]}{''.join(f' ({l.strip()})' for l in lines if l.strip().startswith('waiting for'))}")
        finally:
            browser.close()
    if errors:
        fail(f"in a browser, {who}:\n" + "\n".join(errors))
    ok(f"in a browser (headless Chromium), {who} loads the PDF and opens every sidebar tab ({len(tabs)}, in one row), the"
       f" shortcuts, the palette{', the Compare panel' if compared else ''}{' and the source pane' if editor else ''}, from the"
       " keyboard too, with no page or console"
       " error, nor anything its CSP refuses; a cut-off or null answer is no success"
       + ("; where to cut fails quietly when drawing a page is cancelled, and two looks at once mark each spot once"
          if not answer else ""))


def cut_checks(page, url, errors):
    """Where to cut (✂, cut.js), in a page of its own: when drawing a page is cancelled while it looks (as a newer PDF
    replacing this one does), it fails quietly, with no unhandled rejection, and looks again next time; and two looks at
    once mark each spot once."""
    page.goto(url)
    page.wait_for_selector(".page canvas", timeout=30_000)
    got = page.evaluate("""async () => {
        const imp = (m) => import(new URL(`viewer/${m}`, document.baseURI).href);
        const cut = await imp("cut.js"), marks = await imp("marks.js"), pages = await imp("pages.js"), pdfview = await imp("pdfview.js");
        const until = async (done) => { for (let i = 0; i < 200 && !done(); i++) await new Promise((r) => setTimeout(r, 50)); return done(); };
        if (!await until(() => pages.displayedResult())) return null;
        const d = pdfview.doc, count = document.getElementById("cutCount");
        let cancels = 0;
        const cancelled = () => {
          cancels++;
          return { promise: Promise.reject(new pdfview.pdfjsLib.RenderingCancelledException("Rendering cancelled.")), cancel() {} };
        };
        d.getPage = (n) => Object.getPrototypeOf(d).getPage.call(d, n).then((p) => Object.assign(Object.create(p), { render: cancelled }));
        let failed;
        try {
          marks.setCutOn(true);
          count.textContent = "";
          cut.updateCut();  // (not awaited, as the viewer calls it)
          await until(() => count.textContent);
          failed = count.textContent;
        } finally {
          delete d.getPage;
        }
        await Promise.all([cut.updateCut(), cut.updateCut()]);
        return { failed, cancels, marked: [...marks.marks.cut.values()].reduce((n, v) => n + v.length, 0),
                 spots: (await cut.cutSpots(d, pages.displayedResult())).length };
    }""")
    page.wait_for_timeout(300)  # (for a page error, were a rejection unhandled)
    if got is None:
        errors.append("the main text was not measured within 10 s, so where to cut was not checked")
    elif not got["cancels"] or got["failed"] != "couldn't check":
        errors.append(f"where to cut, when drawing a page is cancelled, says {got['failed']!r}, not that it couldn't check")
    elif not got["spots"] or got["marked"] != got["spots"]:
        errors.append(f"where to cut, looked for twice at once: {got['marked']} marks for {got['spots']} spots")
    page.close()


def compare_checks(page, errors):
    """The Compare panel from the palette: its first commit takes the focus, the arrow keys move along the commits, and
    Esc closes it, giving the focus to a control rather than dropping it."""
    page.keyboard.press("Meta+k" if page.evaluate("/^Mac|^iP/.test(navigator.platform)") else "Control+k")
    page.wait_for_selector("#paletteBackdrop:not([hidden])", timeout=10_000)
    page.keyboard.type("compare with")
    page.wait_for_selector("#paletteResults [role=option]", timeout=10_000)
    page.keyboard.press("Enter")
    page.wait_for_selector("#comparePanel button.commit", timeout=10_000)
    rows = page.locator("#comparePanel .commit").count()
    at = "[...document.querySelectorAll('#comparePanel .commit')].indexOf(document.activeElement)"
    if page.evaluate(at) != 0:
        errors.append("the Compare panel's first commit doesn't take the focus when it opens")
    page.keyboard.press("ArrowDown")
    if page.evaluate(at) != min(1, rows - 1):
        errors.append("ArrowDown doesn't move along the Compare panel's commits")
    page.keyboard.press("Escape")
    if page.locator("#comparePanel").is_visible() or page.evaluate("document.activeElement === document.body"):
        errors.append("Esc doesn't close the Compare panel, giving the focus back")


def net_checks(page, errors):
    """getJSON's answer (net.js) when the body of a 200 is cut off after its headers, and when it is JSON null: no
    success either way (no answer, status 0, for the first), while a list is one."""
    got = page.evaluate("""async () => {
        const net = await import(new URL("viewer/net.js", document.baseURI).href);
        const fake = {
          "check-cut": () => new Response(new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode('{"a')); c.error(new Error("cut")); } }),
                                          { status: 200, headers: { "Content-Type": "application/json" } }),
          "check-null": () => new Response("null", { status: 200, headers: { "Content-Type": "application/json" } }),
          "check-list": () => new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } }),
        };
        const real = window.fetch;
        window.fetch = (url, options) => (fake[url] ? Promise.resolve(fake[url]()) : real(url, options));  // (only these: polls go on)
        try {
          const out = {};
          for (const path of Object.keys(fake)) out[path] = await net.getJSON(path);
          return { ...out, NO_ANSWER: net.NO_ANSWER };
        } finally {
          window.fetch = real;
        }
    }""")
    cut, null, listed = got["check-cut"], got["check-null"], got["check-list"]
    if cut["ok"] or cut["status"] != 0 or cut["error"] != got["NO_ANSWER"]:
        errors.append(f"a 200 whose body is cut off reads as {cut!r}, not as no answer")
    if null["ok"] or not null["error"]:
        errors.append(f"a 200 whose body is JSON null reads as {null!r}, not as a failure")
    if not listed["ok"]:
        errors.append(f"a 200 whose body is a list reads as {listed!r}, not as a success")


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
