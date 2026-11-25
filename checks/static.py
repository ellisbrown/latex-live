"""The checks of the checkout as it is, before anything runs: the Python compiles without warnings (and with
Python 3.9, if there is one), the viewer's import map and modules are valid (and parse and link, with Node, if
there is one: every name a module imports is one its module exports), every element that they look up by id is in viewer.html, instances starting at once agree on one token, and the docs keep up with the code (docs_checks)."""

import ast
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
    code_lines = [re.sub(r"(^|\s)#.*", "", line) for line in read_text(os.path.join(HERE, "latexmkrc")).splitlines()]
    bare = [line.strip() for line in code_lines if re.search(r"(?<![$\w])python", line) and "LATEX_LIVE_PYTHON" not in line]
    if bare or "LATEX_LIVE_PYTHON" not in read_text(os.path.join(HERE, "latexlive", "main.py")):
        fail(f"latexmkrc runs Python other than through live.py's own (LATEX_LIVE_PYTHON, which main.py sets): {bare}")
    ok("latexmkrc runs its Python helpers with live.py's own interpreter (LATEX_LIVE_PYTHON), not a python3 from the PATH")
    with tempfile.TemporaryDirectory(prefix="latex-live-token-") as state:  # (instances starting at once)
        racers = [subprocess.Popen([sys.executable, "-c", "from latexlive.auth import load_token; print(load_token())"], cwd=HERE,
                                   env={**ENV, "LATEX_LIVE_STATE": state}, stdout=subprocess.PIPE, text=True)
                  for _ in range(12)]
        tokens = {r.communicate()[0].strip() for r in racers}
        if len(tokens) != 1 or "" in tokens:
            fail(f"instances starting at once got {len(tokens)} different tokens{' (one empty)' if '' in tokens else ''}")
    ok("instances starting at once agree on one token, never an empty one")
    docs_checks()


def docs_checks():
    """The docs keep up with the code: CONTRIBUTING.md's maps of latexlive/ and checks/, and the module lists in their
    __init__, name every module, in an order where each imports only from those before it; every LATEX_LIVE_* setting
    that the code reads is in the user docs (docs/) or in CONTRIBUTING.md's list of the internal ones, which names
    nothing the code no longer uses."""
    problems = []
    for package, heading in (("latexlive", "### The server (`latexlive/`)"), ("checks", "### The checks (`checks/`)")):
        problems += module_map_problems(package, heading)
    problems += settings_problems()
    contributing = read_text(os.path.join(HERE, "CONTRIBUTING.md"))
    if not any("LATEX_LIVE_USERS" in p for p in settings_problems(docs="", contributing=contributing)) \
            or not any("LATEX_LIVE_RUN" in p for p in settings_problems(contributing="")) \
            or not any("lacks ['hub']" in p for p in module_map_problems("latexlive", "### The server (`latexlive/`)",
                                                                       contributing.replace("| `hub` |", "| hub |"))):
        problems.append("the docs' guards miss a setting or a module left out (settings_problems, module_map_problems)")
    if problems:
        fail("the docs don't match the code:\n" + "\n".join(problems))
    ok("CONTRIBUTING.md maps every module of latexlive/ and checks/ (bottom up, as each package's __init__ lists them), and"
       " every LATEX_LIVE_* setting is documented (docs/, or CONTRIBUTING.md's internal ones)")


def module_map_problems(package, heading, contributing=None):
    """What is wrong with CONTRIBUTING.md's map of package under heading (the modules named in its table's second
    column, but not in parentheses) and the list in the package's __init__ (after "from the bottom up"): a module
    missing, a name that is no module, or a module listed after one that it imports."""
    text = contributing if contributing is not None else read_text(os.path.join(HERE, "CONTRIBUTING.md"))
    modules = {os.path.basename(p)[:-3] for p in glob.glob(os.path.join(HERE, package, "*.py"))} - {"__init__"}
    table = re.search(r"^\|.*(?:\n\|.*)*", text.split(heading, 1)[1], re.M) if heading in text else None  # (its first table)
    rows = re.findall(r"^\|[^|\n]*\|([^|\n]*)\|", table.group(0) if table else "", re.M)
    order = list(dict.fromkeys(re.findall(r"`([^`]+)`", re.sub(r"\([^()]*\)", "", "".join(rows)))))
    if not order:
        return [f"CONTRIBUTING.md has no table of modules under {heading!r}"]
    parts = re.split(r"from the bottom up[^:]*:", read_text(os.path.join(HERE, package, "__init__.py")), maxsplit=1, flags=re.I)
    listed = re.split(r"[\s,]+", parts[1].split(".", 1)[0].strip()) if len(parts) == 2 else []
    problems = [f"CONTRIBUTING.md's map of {package}/ lacks {sorted(modules - set(order))}"] if modules - set(order) else []
    problems += [f"CONTRIBUTING.md's map of {package}/ names {sorted(set(order) - modules)}, which are no modules"] \
        if set(order) - modules else []
    if listed != order:
        problems.append(f"{package}/__init__.py lists its modules otherwise than CONTRIBUTING.md: {listed}, not {order}")
    for i, name in enumerate(order):
        if name not in modules:
            continue
        deps = set()
        for node in ast.walk(ast.parse(read_text(os.path.join(HERE, package, f"{name}.py")))):
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                deps |= {node.module.split(".")[0]} if node.module else {a.name for a in node.names}
        later = sorted(d for d in deps if d in order[i + 1:])
        if later:
            problems.append(f"{package}/{name}.py imports {later}, listed after it in CONTRIBUTING.md")
    return problems


# The files whose LATEX_LIVE_* names settings_problems looks up in the docs: what runs latex-live (not the checks).
SETTINGS_CODE = ["live.py", "latex_report.py", "forward.py", "latexmkrc", "tex-exec", "latexlive/*.py"]


def settings_problems(docs=None, contributing=None):
    """What is wrong with the docs of the LATEX_LIVE_* settings: a name in the code (SETTINGS_CODE), or one that checks/
    reads from the environment (LATEX_LIVE_CHECK_*), in neither docs/ nor CONTRIBUTING.md's "Settings that only
    latex-live sets"; one named in either that nothing uses; or one in both."""
    name_re = re.compile(r"\bLATEX_LIVE_[A-Z0-9_]*[A-Z0-9]\b")
    used = {n for pattern in SETTINGS_CODE for path in glob.glob(os.path.join(HERE, pattern)) for n in name_re.findall(read_text(path))}
    used |= {n for path in glob.glob(os.path.join(HERE, "checks", "*.py"))
             for n in re.findall(r"""environ(?:\.get\(|\[)\s*["'](LATEX_LIVE_CHECK_[A-Z0-9_]+)""", read_text(path))}
    if docs is None:
        docs = "".join(read_text(path) for path in glob.glob(os.path.join(HERE, "docs", "*.md")))
    text = contributing if contributing is not None else read_text(os.path.join(HERE, "CONTRIBUTING.md"))
    heading = "### Settings that only latex-live sets"
    internal = set(name_re.findall(re.split(r"\n#{2,3} ", text.split(heading, 1)[1], maxsplit=1)[0])) if heading in text else set()
    documented = set(name_re.findall(docs))
    problems = [f"{name} is in neither docs/ nor CONTRIBUTING.md's internal settings" for name in sorted(used - documented - internal)]
    problems += [f"{name} is in docs/ or CONTRIBUTING.md's internal settings, but no code uses it"
                 for name in sorted((internal | documented) - used)]
    problems += [f"{name} is in CONTRIBUTING.md's internal settings, but docs/ document it for users" for name in sorted(internal & documented)]
    return problems


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
