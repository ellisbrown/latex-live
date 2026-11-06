"""The checks of the checkout as it is, before anything runs: the Python compiles without warnings (and with
Python 3.9, if there is one), the viewer's import map and modules are valid (and parse, with Node, if there is
one), and instances starting at once agree on one token."""

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
from .helpers import put, read_text


IMPORT_RES = (re.compile(r"""^\s*(?:import|export)\s[^;'"]*?\bfrom\s*["']([^"']+)["']""", re.M),
              re.compile(r"""^\s*import\s*["']([^"']+)["']""", re.M),
              re.compile(r"""\bimport\(\s*["']([^"']+)["']\s*\)"""))


def python_files():
    """The Python files of the checkout: the scripts, the server's package (latexlive/) and the smoke test's
    (checks/)."""
    return ["live.py", "latex_report.py", "forward.py", "check.py",
            *sorted(os.path.relpath(p, HERE) for p in glob.glob(os.path.join(HERE, "latexlive", "*.py"))),
            *sorted(os.path.relpath(p, HERE) for p in glob.glob(os.path.join(HERE, "checks", "*.py")))]


def static_checks():
    for name in python_files():
        r = subprocess.run([sys.executable, "-W", "error", "-m", "py_compile", os.path.join(HERE, name)],
                           capture_output=True, text=True)
        if r.returncode:
            fail(f"{name} does not compile:\n{r.stderr}")
    ok("the Python compiles")
    oldest = shutil.which("python3.9")  # (the oldest supported, as macOS ships)
    for name in python_files() if oldest else ():
        r = subprocess.run([oldest, "-c", "import sys; compile(open(sys.argv[1]).read(), sys.argv[1], 'exec')",
                            os.path.join(HERE, name)], capture_output=True, text=True)
        if r.returncode:
            fail(f"{name} does not compile with Python 3.9:\n{r.stderr}")
    if oldest:
        ok("it compiles with Python 3.9 too")
    with open(os.path.join(HERE, "viewer.html")) as f:
        code = viewer_scripts(f.read())
    node = shutil.which("node")
    if not node:
        print("skip: no node, so whether the viewer's script parses is not checked", flush=True)
    else:
        with tempfile.TemporaryDirectory(prefix="latex-live-check-") as tmp:
            for name, text in code.items():  # (each as a .mjs file: Node parses it as a module then)
                path = os.path.join(tmp, name.replace("/", "-") + ".mjs")
                put(path, text)
                r = subprocess.run([node, "--check", path], capture_output=True, text=True)
                if r.returncode:
                    fail(f"the viewer's {name} does not parse:\n{r.stderr}")
        ok(f"the viewer's script parses ({len(code)} module{'s' if len(code) > 1 else ''})")
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
    return code


def imported(code):
    """What JavaScript code imports, statically or with import("...")."""
    return {m.group(1) for pattern in IMPORT_RES for m in pattern.finditer(code)}


def bare_imports(code):
    """The bare module names (not ./, ../ or / paths, nor URLs) that JavaScript code imports."""
    return {n for n in imported(code) if not n.startswith((".", "/")) and not re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", n)}
