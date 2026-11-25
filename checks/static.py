"""The checks of the checkout as it is, before anything runs: the Python compiles without warnings (and with
Python 3.9, if there is one), the viewer's import map and modules are valid (and parse and link, with Node, if
there is one: every name a module imports is one its module exports), every element that they look up by id is in viewer.html, and instances starting at once agree on one token."""

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse

from .base import ENV, HERE, fail, ok
from .helpers import read_text


IMPORT_RES = (re.compile(r"""^\s*(?:import|export)\s[^;'"]*?\bfrom\s*["']([^"']+)["']""", re.M),
              re.compile(r"""^\s*import\s*["']([^"']+)["']""", re.M),
              re.compile(r"""\bimport\(\s*["']([^"']+)["']\s*\)"""))


# What pdfview.js's swapDocument uses of PDF.js's page views, which are not its public interface: keeping the old
# canvas while a page redraws, the viewer's document as a field, each view's optional content, and the event that
# says every view has its page.
PAGE_SWAP = ["keepCanvasWrapper = false", "this.pdfDocument = pdfDocument;", "this._optionalContentConfigPromise = ",
             '"pagesloaded"', "case canvasWrapperNode:"]
# What layout.js's gutters for the margin bubbles rely on: PDF.js fits the page width to the container's clientWidth,
# and reckons which pages are in view (with the whole width) in its _getVisiblePages.
GUTTERS = ["(this.container.clientWidth - hPadding)", "_getVisiblePages() {", "right = left + scrollEl.clientWidth;"]


def python_files():
    """The Python files of the checkout: the scripts, the server's package (latexlive/) and the smoke test's
    (checks/)."""
    return ["live.py", "latex_report.py", "forward.py", "check.py",
            *sorted(os.path.relpath(p, HERE) for p in glob.glob(os.path.join(HERE, "latexlive", "*.py"))),
            *sorted(os.path.relpath(p, HERE) for p in glob.glob(os.path.join(HERE, "checks", "*.py")))]


# Compiles each file given (by path) and prints what doesn't compile, for one Python process to check them all.
COMPILE = ("import sys\n"
           "for path in sys.argv[1:]:\n"
           "    try:\n"
           "        with open(path, 'rb') as f:\n"  # (bytes: compile honours an encoding declaration, whatever the locale)
           "            compile(f.read(), path, 'exec')\n"
           "    except (SyntaxError, ValueError, Warning) as e:\n"
           "        print(f'{path}: {type(e).__name__}: {e}')\n")


def static_checks():
    paths = python_files()
    r = subprocess.run([sys.executable, "-W", "error", "-c", COMPILE, *paths], cwd=HERE, capture_output=True, text=True)
    if r.returncode or r.stdout:
        fail(f"the Python does not compile:\n{r.stdout}{r.stderr}")
    ok("the Python compiles")
    oldest = shutil.which("python3.9")  # (the oldest supported, as macOS ships)
    if oldest:
        r = subprocess.run([oldest, "-c", COMPILE, *paths], cwd=HERE, capture_output=True, text=True)
        if r.returncode or r.stdout:
            fail(f"the Python does not compile with Python 3.9:\n{r.stdout}{r.stderr}")
        ok("it compiles with Python 3.9 too")
    with open(os.path.join(HERE, "viewer.html")) as f:
        page = f.read()
    code, imports = viewer_scripts(page)
    ids = set(re.findall(r'\bid="([^"]+)"', page))
    missing = sorted({f"{name}: {m.group(1)}" for name, text in code.items() for m in re.finditer(r'\$\("([^"]+)"\)', text)
                      if m.group(1) not in ids})
    if missing:
        fail(f"the viewer's modules look up elements that viewer.html doesn't have: {missing}")
    ok(f"every element that the viewer's modules look up by id is in viewer.html ({len(ids)} ids)")
    node = shutil.which("node")
    if not node:
        print("skip: no node, so whether the viewer's modules parse and link is not checked", flush=True)
    else:
        linked = link_modules(node, HERE, imports, viewer_roots(code, imports), code.get("viewer.html"))
        if linked["problems"]:
            fail("the viewer's modules do not parse or link (the viewer would stay blank):\n" + "\n".join(linked["problems"]))
        ok(f"the viewer's modules parse and link ({len(code)} of the viewer's, {linked['modules']} with the editor's and PDF.js's)")
    pdf_viewer = read_text(os.path.join(HERE, "pdfjs", "web", "pdf_viewer.mjs"))
    missing = [s for s in PAGE_SWAP if s not in pdf_viewer]
    if missing:
        fail(f"the vendored pdf_viewer.mjs lacks what the viewer's in-place reload (pdfview.js swapDocument) uses: {missing}")
    ok("the vendored PDF.js viewer has what the in-place reload uses")
    missing = [s for s in GUTTERS if s not in pdf_viewer]
    if missing:
        fail(f"the vendored pdf_viewer.mjs lacks what the margin bubbles' gutters (layout.js) rely on: {missing}")
    ok("the vendored PDF.js viewer measures as the margin bubbles' gutters expect")
    with tempfile.TemporaryDirectory(prefix="latex-live-token-") as state:  # (instances starting at once)
        racers = [subprocess.Popen([sys.executable, "-c", "from latexlive.auth import load_token; print(load_token())"], cwd=HERE,
                                   env={**ENV, "LATEX_LIVE_STATE": state}, stdout=subprocess.PIPE, text=True)
                  for _ in range(12)]
        tokens = {r.communicate()[0].strip() for r in racers}
        if len(tokens) != 1 or "" in tokens:
            fail(f"instances starting at once got {len(tokens)} different tokens{' (one empty)' if '' in tokens else ''}")
    ok("instances starting at once agree on one token, never an empty one")


def viewer_scripts(page):
    """The viewer's module code by file (its modules in viewer/, and inline code in viewer.html if any),
    once its import map checks out: valid JSON, ahead of the module code, every target a file here given
    relative to the viewer (so that it also resolves under the hub's prefix), and every bare module name
    that the viewer or a vendored editor module imports mapped; and once every module that the viewer
    imports by path is a file here, given relative to the importing file (so that it also resolves there)."""
    scripts, imports, modules = [], None, False
    for m in re.finditer(r"<script\b([^>]*)>(.*?)</script>", page, re.S):
        attrs, code = m.group(1), m.group(2)
        kind = re.search(r"""\btype\s*=\s*["']?([^"'\s>]+)""", attrs)
        kind = kind.group(1) if kind else ""
        src = re.search(r"""\bsrc\s*=\s*["']?([^"'\s>]+)""", attrs)
        if src:
            if not os.path.isfile(os.path.join(HERE, urllib.parse.urlsplit(src.group(1)).path.lstrip("/"))):
                fail(f"the viewer loads {src.group(1)}, which is not in the checkout")
            modules = modules or kind == "module"
        elif kind == "importmap":
            if modules:
                fail("the viewer's import map comes after its module code, so browsers would ignore it")
            try:
                imports = json.loads(code)["imports"]
            except (ValueError, KeyError, TypeError) as e:
                fail(f"the viewer's import map is not JSON with an imports object ({type(e).__name__}: {e})")
            if not isinstance(imports, dict):
                fail("the viewer's import map has no imports object")
        elif kind in ("", "module"):
            scripts.append(code)
            modules = True
        else:
            fail(f"check.py does not check the viewer's <script{attrs}>")
    if imports is None:
        fail("the viewer has no import map (a script of type importmap), which the browser editor's modules need")
    for name, target in imports.items():
        path = urllib.parse.urlsplit(target).path if isinstance(target, str) else ""
        real = os.path.normpath(os.path.join(HERE, path))
        found = os.path.isdir(real) if name.endswith("/") else os.path.isfile(real)
        if not path.startswith("./") or not real.startswith(HERE + os.sep) or not found:
            fail(f"the viewer's import map sends {name} to {target!r}, not to a file here given as ./...")
    code = {"viewer.html": "\n".join(scripts)} if scripts else {}
    for path in sorted(glob.glob(os.path.join(HERE, "viewer", "*.js"))):
        code[os.path.relpath(path, HERE)] = read_text(path)
    vendored = {os.path.relpath(path, HERE): read_text(path) for path in sorted(glob.glob(os.path.join(HERE, "codemirror", "*.js")))}
    for name, text in {**code, **vendored}.items():
        for spec in sorted(bare_imports(text)):
            if spec not in imports and not any(key.endswith("/") and spec.startswith(key) for key in imports):
                fail(f"{name} imports {spec}, which the viewer's import map does not map")
    for name, text in code.items():
        for spec in sorted(imported(text) - bare_imports(text)):
            real = os.path.normpath(os.path.join(HERE, os.path.dirname(name), urllib.parse.urlsplit(spec).path))
            if not spec.startswith(("./", "../")) or not real.startswith(HERE + os.sep) or not os.path.isfile(real):
                fail(f"the viewer's {name} imports {spec}, not a file here given relative to it")
    ok(f"the viewer's import map maps every module that the viewer and its editor import ({len(imports)} names); the"
       " viewer's own imports are files here")
    return code, imports


def imported(code):
    """What JavaScript code imports, statically or with import("...")."""
    return {m.group(1) for pattern in IMPORT_RES for m in pattern.finditer(code)}


def bare_imports(code):
    """The bare module names (not ./, ../ or / paths, nor URLs) that JavaScript code imports."""
    return {n for n in imported(code) if not n.startswith((".", "/")) and not re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", n)}


def viewer_roots(code, imports):
    """The modules to link from (paths relative to the checkout): the viewer's own, and every module that they
    import, by path or by a name in the import map, statically or with import("..."), which linking alone would
    not reach (as the browser editor's modules, which the viewer loads on demand)."""
    roots = [name for name in code if name != "viewer.html"]
    for name, text in code.items():
        for spec in sorted(imported(text)):
            if spec in bare_imports(text):
                prefix = max((k for k in imports if k.endswith("/") and spec.startswith(k)), key=len, default=None)
                target = imports[spec] if spec in imports else prefix and imports[prefix] + spec[len(prefix):]
            else:
                target = None if re.match(r"[A-Za-z][A-Za-z0-9+.-]*:|/", spec) else os.path.join(os.path.dirname(name), spec)
            if target:
                roots.append(os.path.relpath(os.path.normpath(os.path.join(HERE, urllib.parse.urlsplit(target).path)), HERE))
    return list(dict.fromkeys(roots))


def link_modules(node, root, imports, roots, inline=None):
    """{modules, problems} from linking ES modules under root with Node (link.mjs), without running them: what
    doesn't parse, an import of no file, a name imported that its module doesn't export."""
    r = subprocess.run([node, "--experimental-vm-modules", "--no-warnings", os.path.join(HERE, "checks", "link.mjs")],
                       input=json.dumps({"root": root, "imports": imports, "roots": roots, "inline": inline}), capture_output=True,
                       text=True, timeout=120)
    try:
        return json.loads(r.stdout)
    except ValueError:
        fail(f"node could not link the viewer's modules (exit {r.returncode}):\n{r.stdout}{r.stderr}")
