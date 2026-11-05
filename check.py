#!/usr/bin/env python3
"""Smoke test for latex-live: python3 check.py (about a minute; needs TeX, as live.py does).

1. The Python compiles without warnings (and with Python 3.9, if there is one); the viewer's import
   map is valid JSON, comes before its module code, and maps every bare module name that the viewer
   and the vendored editor (codemirror/) import to a file here; the viewer's script parses (with
   Node, if there is one).
   The review copy's layout keeps notes in order, apart, and on their pages, and continues the ones
   that don't fit after the paper; its matching tells apart two authors' notes with the same text,
   and gives no note the record of a protected marker that holds its text. The downloads read TeX's
   logs, .aux, .bbl and SyncTeX files only up to a bound, and say so past it (a stand-in for TeX
   writes the probe pass's log), and read a log whose wrapped lines split a UTF-8 character. An export
   that failed or timed out fails again at once for the same build; the downloads wait for a build
   in progress.
2. A scratch paper in a temporary directory (two columns, lineno line numbers, inline notes, a
   git repository with a pushed commit and a local one) gets a live.py instance, with the hub on
   port 44180 and state of its own (not the user's):
   - the build publishes main.pdf, and the notes-free pass main-clean.pdf;
   - the hub redirects an old bookmark, needs the token or its cookie, and forwards every
     endpoint (POST /rebuild too); SyncTeX finds a source line; /instances lists the instance;
   - the HEAD and last-push baselines build (they differ: one commit is unpushed), and the
     notes-free baseline's line numbers match the live notes-free build's (two passes with lineno);
   - the download without notes is an exact build, and the review copy places its pages on wider
     ones with every note in a gutter (none overlapping, the protected marker and the resolved
     note left out), and its last pass loads no package that the paper's TeX wrote; a document
     without notes gets a clear failure, not a file;
   - the browser editor: /me answers, its modules are served, SyncTeX answers as JSON without
     opening the editor (a stand-in that logs its arguments); its files API lists and refuses
     the right files (../, absolute paths, .git, build/, a link out of the paper, a new file, too
     big, not UTF-8), reads, saves, merges a stale save, writes nothing on a conflict, asks for the
     base text it lacks, survives a save whose answer got lost (but not another tab's save of the
     same lines), and keeps CRLF line ends and the file's mode; status ?open= follows the open
     file; the hub takes a save above the other actions' size limit;
   - a second document runs in its own process behind the same port, and /papers links both;
   - a second instance for the same document says it is already running; --replace takes over,
     and so does any new instance when the running one no longer answers; the hub moves to
     another instance on the same port, and a hub that no longer answers is replaced;
   - stopping the instance removes it from /instances.
Exits non-zero on the first failure.
"""

import glob
import gzip
import hashlib
import http.client
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
import urllib.parse
import urllib.request
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))


def free_ports(start, count=8):
    """The first of count consecutive ports from start (in steps of count) that all bind: the
    kernel also hands out ports in this range to outgoing connections."""
    for port in range(start, start + 400, count):
        socks = []
        try:
            for p in range(port, port + count):
                s = socket.socket(socket.AF_INET6)
                socks.append(s)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # (as live.py's servers: ports that closed connections still hold are free)
                s.bind(("::", p))
            return port
        except OSError:
            pass
        finally:
            for s in socks:
                s.close()
    raise SystemExit(f"FAIL: no {count} free ports from {start}")


PORT = free_ports(44180)  # the hub's; the previews' own ports follow

PAPER = r"""\documentclass[twocolumn]{article}
\usepackage{xcolor}
\usepackage[switch]{lineno}
\linenumbers
\newif\ifshowtodos \showtodostrue
\newcommand{\todotxt}[1]{\ifshowtodos\textcolor{red}{#1}\fi}
\newcommand{\eb}[1]{\todotxt{[EB: #1]}}
\begin{document}
\section{Introduction}
\eb{A note long enough that hiding it moves the text below it. A note long enough that hiding it
moves the text below it. A note long enough that hiding it moves the text below it.}
\input{body}
\section{Method}\label{sec:method}
See \ref{sec:method}. A sentence with a note \eb{a note in a paragraph, about \ref{sec:method}} and more
words after it.\footnote{A footnote \eb{a note in a footnote}.} \ifnum0=1 \eb{a note in a branch that is off}\fi
% \eb{a resolved note}
\eb{human-written}
% In the review copy's probe pass only: a package that TeX writes into that pass's folder, which the
% copy's last pass uses and must not load from there.
\makeatletter\ifdefined\latexlive@record\newwrite\planted\immediate\openout\planted=lmodern.sty
\immediate\write\planted{\noexpand\typeout{latex-live-check: a package that TeX wrote ran}}\immediate\closeout\planted\fi\makeatother
\end{document}
"""


IMPORT_RES = (re.compile(r"""^\s*(?:import|export)\s[^;'"]*?\bfrom\s*["']([^"']+)["']""", re.M),
              re.compile(r"""^\s*import\s*["']([^"']+)["']""", re.M),
              re.compile(r"""\bimport\(\s*["']([^"']+)["']\s*\)"""))
JS_TYPE_RE = re.compile(r"(?:text|application)/(?:x-)?javascript\b")


LOGS = []  # the instances' output, shown on failure
# For the instances: their own token and instance list (live_checks), and a protected marker.
ENV = dict(os.environ, LATEX_LIVE_PROTECTED=r"\\eb\{human-written\}")


def redact(text):
    """Never print the token: as a URL parameter, a cookie, or itself."""
    text = re.sub(r"(t=|latex_live=)[A-Za-z0-9_-]{8,}", r"\1REDACTED", str(text))
    return text.replace(TOKEN[0], "REDACTED") if TOKEN[0] else text


def fail(msg):
    for path in LOGS:
        with open(path, errors="replace") as f:
            print(f"--- {os.path.basename(path)} (last lines)\n{redact(''.join(f.readlines()[-25:]))}", flush=True)
    sys.exit(f"FAIL: {redact(msg)}")


def ok(msg):
    print(f"ok: {msg}", flush=True)


def wait_for(what, test, timeout=180):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if result := test():
            return result
        time.sleep(1)
    fail(f"timed out waiting for {what}")


def static_checks():
    for name in ("live.py", "latex_report.py", "forward.py", "check.py"):
        r = subprocess.run([sys.executable, "-W", "error", "-m", "py_compile", os.path.join(HERE, name)],
                           capture_output=True, text=True)
        if r.returncode:
            fail(f"{name} does not compile:\n{r.stderr}")
    ok("the Python compiles")
    oldest = shutil.which("python3.9")  # (the oldest supported, as macOS ships)
    for name in ("live.py", "latex_report.py", "forward.py", "check.py") if oldest else ():
        r = subprocess.run([oldest, "-c", "import sys; compile(open(sys.argv[1]).read(), sys.argv[1], 'exec')",
                            os.path.join(HERE, name)], capture_output=True, text=True)
        if r.returncode:
            fail(f"{name} does not compile with Python 3.9:\n{r.stderr}")
    if oldest:
        ok("it compiles with Python 3.9 too")
    with open(os.path.join(HERE, "viewer.html")) as f:
        scripts = viewer_scripts(f.read())
    node = shutil.which("node")
    if not node:
        return print("skip: no node, so the viewer's script is not checked", flush=True)
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False) as f:
        f.write("\n".join(scripts))
    r = subprocess.run([node, "--check", f.name], capture_output=True, text=True)
    os.remove(f.name)
    if r.returncode:
        fail(f"the viewer's script does not parse:\n{r.stderr}")
    ok("the viewer's script parses")
    with tempfile.TemporaryDirectory(prefix="latex-live-token-") as state:  # (instances starting at once)
        racers = [subprocess.Popen([sys.executable, "-c", "import live; print(live.load_token())"], cwd=HERE,
                                   env={**os.environ, "LATEX_LIVE_STATE": state}, stdout=subprocess.PIPE, text=True)
                  for _ in range(12)]
        tokens = {r.communicate()[0].strip() for r in racers}
        if len(tokens) != 1 or "" in tokens:
            fail(f"instances starting at once got {len(tokens)} different tokens{' (one empty)' if '' in tokens else ''}")
    ok("instances starting at once agree on one token, never an empty one")
    layout_checks()
    matching_checks()
    bounds_checks()
    failed_export_checks()
    waiting_download_checks()


def put(path, text):
    """Replace a file at once, as another editor would (a new inode, so no cached version survives it)."""
    put_bytes(path, text.encode("utf-8"))


def put_bytes(path, data):
    """put, for bytes."""
    with open(path + ".check-new", "wb") as f:
        f.write(data)
    os.replace(path + ".check-new", path)


def tex_stand_in(tmp):
    """A stand-in for TeX in the downloads (live.exact_export, live.run_probe: server.tex_exec, a command prefix)
    that runs no TeX: it logs its arguments to tmp/calls, writes tmp/log as the job's log in its output folder
    (and tmp/pdf, if there is one, as its PDF), and exits with the code in tmp/exit (0 if none)."""
    script = os.path.join(tmp, "tex.py")
    with open(script, "w") as f:
        f.write("import os, shutil, sys\n"
                "here, args = os.path.dirname(os.path.abspath(__file__)), sys.argv[1:]\n"
                "with open(os.path.join(here, 'calls'), 'a') as f:\n"
                "    f.write(' '.join(args) + '\\n')\n"
                "out = next((a.split('=', 1)[1] for a in args if a.startswith('-output-directory=')), '.')\n"
                "job = next((a.split('=', 1)[1] for a in args if a.startswith('-jobname=')), 'texput')\n"
                "for name, ext in (('log', '.log'), ('pdf', '.pdf')):\n"
                "    if os.path.exists(os.path.join(here, name)):\n"
                "        shutil.copyfile(os.path.join(here, name), os.path.join(out, job + ext))\n"
                "sys.exit(int(open(os.path.join(here, 'exit')).read()) if os.path.exists(os.path.join(here, 'exit')) else 0)\n")
    return f"{shlex.quote(sys.executable)} {shlex.quote(script)}"


def bounds_checks():
    """The downloads read TeX's output within bounds (live.OUTPUT_LIMIT and live.SYNCTEX_LIMIT, made smaller here),
    without TeX: a log, an .aux or a .bbl past its bound (live.read_output), the probe pass's log (live.run_probe,
    with a stand-in for TeX) and a SyncTeX file uncompressed (live.synctex_pages) stop a download with a message
    that says so, before they are read whole; within them, the probe's records are read as before, and so is a log
    whose wrapped lines split a UTF-8 character."""
    sys.path.insert(0, HERE)
    import live
    saved, tmp = (live.OUTPUT_LIMIT, live.SYNCTEX_LIMIT), tempfile.mkdtemp(prefix="latex-live-check-")
    try:
        live.OUTPUT_LIMIT = live.SYNCTEX_LIMIT = 1 << 20
        root = os.path.join(tmp, "paper")
        out = os.path.join(root, "build", "review", "main")
        os.makedirs(out)
        aux = os.path.join(out, "main-export.aux")
        put(aux, "\\relax\n" * 1000)
        if live.read_output(root, aux) != "\\relax\n" * 1000 or live.read_output(root, aux + ".gone") is not None:
            fail("live.read_output does not read a file within its bound as it is, or has a file that is gone")
        put_bytes(aux, b"Overfull \\hbox in caf\xc3\n\xa9\n")  # (TeX wraps its log at 79 bytes, splitting a UTF-8 character)
        try:
            if "Overfull" not in (live.read_output(root, aux) or ""):
                raise ValueError("nothing read")
        except ValueError as e:
            fail(f"live.read_output does not read a log whose wrapping split a UTF-8 character: {e}")
        put(aux, "\\relax\n" * 200_000)
        try:
            live.read_output(root, aux)
            fail("live.read_output read a file past its bound")
        except RuntimeError as e:
            if "build/review/main/main-export.aux is over 1 MB" not in str(e):
                fail(f"a file past its bound: {e}")

        stand_in = os.path.join(tmp, "tex")
        os.makedirs(stand_in)
        server = types.SimpleNamespace(paper_root=root, tex_exec=tex_stand_in(stand_in))
        geo = live.review_geometry({"W": 612, "H": 792, "columns": 1})
        notes = [{"id": 1, "macro": "eb", "body": "Check this", "call": "\\eb{Check this}"}]
        records = ("latex-live|pos|./body.tex|7|1|4000000|50000000|[EB: Check this]\n"
                   "latex-live|snippet|1|0|2|100.0pt|10.0pt|2.0pt\nlatex-live|snippet|1|1|3|90.0pt|8.0pt|1.0pt\n"
                   "latex-live|pages|1\n")
        put(os.path.join(stand_in, "log"), f"This is pdfTeX\n{'Overfull hbox in a paragraph' * 3}\n{records}"
                                           "Output written on main-export.pdf (3 pages, 1234 bytes).\n")
        put(os.path.join(stand_in, "pdf"), "%PDF-1.5\n")
        probe = live.run_probe(server, out, "main-export", "main", notes, geo)
        if (probe.get("pages"), [r[:3] for r in probe.get("pos", [])], sorted(probe.get("snippets", {}).get(1, {})),
                probe.get("pdf")) != (1, [["body.tex", 7, 1]], [0, 1], True) or not os.path.isfile(os.path.join(out, "probe.pdf")):
            fail(f"the probe's records, read from its log line by line, are not as written: {probe}")
        put(os.path.join(stand_in, "log"), "x" * 99 + "\n" + ("latex-live|seen|./body.tex|7|[EB: Check this]\n" * 30_000) + records)
        try:
            live.run_probe(server, out, "main-export", "main", notes, geo)
            fail("the probe's log was read past its bound")
        except RuntimeError as e:
            if "build/review/main/main-export.log is over 1 MB" not in str(e):
                fail(f"the probe's log past its bound: {e}")

        synctex = os.path.join(tmp, "export.synctex.gz")
        head = ("SyncTeX Version:1\nInput:1:/paper/body.tex\nContent:\n{1\n[1,7:4736286,4736286:30000000,40000000,0\n"
                "(1,7:4736286,9000000:30000000,655360,0\n")
        with gzip.open(synctex, "wt") as f:
            f.write(head + "h1,7:4736286,9000000:100000\n" * 10 + ")\n]\n}1\n")
        if list(live.synctex_pages(synctex)) != [1]:
            fail(f"live.synctex_pages does not read a small SyncTeX file: {live.synctex_pages(synctex)}")
        with gzip.open(synctex, "wt") as f:  # (a few KB compressed)
            f.write(head + "h1,7:4736286,9000000:100000\n" * 50_000 + ")\n]\n}1\n")
        try:
            live.synctex_pages(synctex)
            fail("a SyncTeX file was read past its bound")
        except RuntimeError as e:
            if "export.synctex.gz is over 1 MB uncompressed" not in str(e):
                fail(f"a SyncTeX file past its bound: {e}")
    finally:
        live.OUTPUT_LIMIT, live.SYNCTEX_LIMIT = saved
        shutil.rmtree(tmp, ignore_errors=True)
    ok("the downloads read TeX's logs, .aux, .bbl and SyncTeX files only up to a bound, past which they stop and say so;"
       " the probe's records are read from its log line by line; a log whose wrapping split a UTF-8 character reads")


def failed_export_checks():
    """An export that failed fails again at once for the same build of the document (live.exact_export, with a
    stand-in for TeX), as the review copy asks for it after /export did, and so does one that timed out; the
    next build is exported again."""
    sys.path.insert(0, HERE)
    import live
    tmp = tempfile.mkdtemp(prefix="latex-live-check-")
    try:
        root = os.path.join(tmp, "paper")
        os.makedirs(os.path.join(root, live.BUILD))
        published = os.path.join(root, "main.pdf")
        put(published, "%PDF-1.5\n")
        stand_in = os.path.join(tmp, "tex")
        os.makedirs(stand_in)
        put(os.path.join(stand_in, "log"), "This is pdfTeX\n./main.tex:3: Undefined control sequence.\n")
        put(os.path.join(stand_in, "exit"), "1")
        server = types.SimpleNamespace(paper_root=root, tex_exec=tex_stand_in(stand_in), export={}, exported={},
                                       export_lock=threading.Lock())

        def runs(what, error="./main.tex:3: Undefined control sequence."):
            try:
                live.exact_export(server, "main.pdf")
                fail(f"{what} went ahead, though TeX failed")
            except RuntimeError as e:
                if not str(e).startswith(error):
                    fail(f"{what} failed with: {e}")
            with open(os.path.join(stand_in, "calls")) as f:
                return len(f.read().splitlines())

        def new_build():
            st = os.stat(published)
            os.utime(published, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

        if (runs("the export"), runs("the export again, for the same build")) != (1, 1):
            fail("an export that failed was built again for the same build of the document")
        new_build()
        if runs("the export of a new build") != 2:
            fail("an export that failed was not built again for a new build of the document")
        new_build()

        def too_long(*args, **kwargs):
            with open(os.path.join(stand_in, "calls"), "a") as f:
                f.write("timed out\n")
            raise subprocess.TimeoutExpired(args[0], 600)

        live.subprocess = types.SimpleNamespace(run=too_long, TimeoutExpired=subprocess.TimeoutExpired)
        if (runs("an export that times out", "a pass took over 10 minutes"), runs("it again", "a pass took over 10 minutes")) != (3, 3):
            fail("an export that timed out was built again for the same build of the document")
    finally:
        live.subprocess = subprocess
        shutil.rmtree(tmp, ignore_errors=True)
    ok("an export that failed or timed out fails again at once for the same build (no second build for the review"
       " copy), and is built again for the next one")


def waiting_download_checks():
    """/export and /review wait while the document builds (live.start_export, live.start_review): a pass may publish
    the PDF before the run ends, and a download made then would lack the run's last pass; once the run ends, they
    start (a build of theirs that runs is reported as it is). With --no-build, a stale marker holds nothing up."""
    sys.path.insert(0, HERE)
    import live
    tmp = tempfile.mkdtemp(prefix="latex-live-check-")
    try:
        root = os.path.join(tmp, "paper")
        os.makedirs(os.path.join(root, live.BUILD))
        put(os.path.join(root, "main.pdf"), "%PDF-1.5\n")
        status, building = live.job_file(root, "main.pdf", ".status.json"), live.job_file(root, "main.pdf", ".building")
        put(status, "{}")
        put(building, "")
        os.utime(building, (time.time() + 5, time.time() + 5))  # (a run that started after the last one ended)
        server = types.SimpleNamespace(paper_root=root, default_pdf="main.pdf", export={}, review={}, no_build=True)
        if live.waiting_for_build(server, "main.pdf", {}) is not None:
            fail("with --no-build, the downloads wait for a build that a stopped one left marked as running")
        server.no_build = False
        for name, start in (("/export", live.start_export), ("/review", live.start_review)):
            code, state = start(server)
            if (code, state.get("state"), state.get("step")) != (200, "running", "waiting for the build in progress") or server.export or server.review:
                fail(f"{name} during a build: {code} {state}, {server.export or server.review}")
        server.export = {"pdf": "main.pdf", "state": "running", "step": "its own"}
        if live.start_export(server)[1].get("step") != "its own":
            fail("/export during a build did not report its own build that runs")
        os.utime(status, (time.time() + 10, time.time() + 10))  # (the run ended)
        if live.waiting_for_build(server, "main.pdf", {}) is not None:
            fail("/export still waits after the build ended")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    ok("the downloads wait while the document builds, and start once the run ends (with --no-build, at once)")


def viewer_scripts(page):
    """The viewer's inline module code, once its import map checks out: valid JSON, ahead of the module
    code, every target a file here given relative to the viewer (so that it also resolves under the hub's
    prefix), and every bare module name that the viewer or a vendored editor module imports mapped."""
    scripts, imports = [], None
    for m in re.finditer(r"<script\b([^>]*)>(.*?)</script>", page, re.S):
        attrs, code = m.group(1), m.group(2)
        kind = re.search(r"""\btype\s*=\s*["']?([^"'\s>]+)""", attrs)
        kind = kind.group(1) if kind else ""
        if kind == "importmap":
            if scripts:
                fail("the viewer's import map comes after its module code, so browsers would ignore it")
            try:
                imports = json.loads(code)["imports"]
            except (ValueError, KeyError, TypeError) as e:
                fail(f"the viewer's import map is not JSON with an imports object ({type(e).__name__}: {e})")
            if not isinstance(imports, dict):
                fail("the viewer's import map has no imports object")
        elif kind in ("", "module"):
            scripts.append(code)
        else:
            fail(f"check.py does not check the viewer's <script{attrs}>")
    if imports is None:
        fail("the viewer has no import map (a script of type importmap), which the browser editor's modules need")
    for name, target in imports.items():
        path = urllib.parse.urlsplit(target).path if isinstance(target, str) else ""
        real = os.path.normpath(os.path.join(HERE, path))
        if not path.startswith("./") or not real.startswith(HERE + os.sep) or not os.path.isfile(real):
            fail(f"the viewer's import map sends {name} to {target!r}, not to a file here given as ./...")
    code = {"viewer.html": "\n".join(scripts)}
    for path in sorted(glob.glob(os.path.join(HERE, "codemirror", "*.js"))):
        with open(path, encoding="utf-8") as f:
            code[os.path.relpath(path, HERE)] = f.read()
    for name, text in code.items():
        names = {m.group(1) for pattern in IMPORT_RES for m in pattern.finditer(text)}
        for spec in sorted(n for n in names if not n.startswith((".", "/")) and not re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", n)):
            if spec not in imports:
                fail(f"{name} imports {spec}, which the viewer's import map does not map")
    ok(f"the viewer's import map maps every module that the viewer and its editor import ({len(imports)} names)")
    return scripts


def layout_checks():
    """The review copy's gutters (live.place_notes, live.review_layout), on made-up notes."""
    sys.path.insert(0, HERE)
    import random
    import live
    rng = random.Random(7)
    for _ in range(200):
        n = rng.randint(1, 12)
        heights = [rng.uniform(5, 60) for _ in range(n)]
        want = sorted(rng.uniform(0, 800) for _ in range(n))
        if sum(heights) + 5 * (n - 1) > 700:
            continue
        tops = live.place_notes(want, heights, 40, 740, 5)
        if any(tops[i] + heights[i] + 5 > tops[i + 1] + 1e-6 for i in range(n - 1)) or tops[0] < 40 - 1e-6 \
                or tops[-1] + heights[-1] > 740 + 1e-6:
            fail(f"place_notes overlaps or leaves the gutter: {want} {heights} -> {tops}")
    geo = live.review_geometry({"W": 612, "H": 792, "columns": 2})
    notes = [{"id": i, "page": 1 + i // 40, "x": 100 + 300 * (i % 2), "y": 60 + (i * 17) % 700, "author": "EB", "body": "x",
              "macro": "eb", "file": "a.tex", "line": i, "how": "exact"} for i in range(80)]
    sizes = {i: {0: [100 + 2 * i, 150, 6, 40], 1: [101 + 2 * i, 150, 5, 30]} for i in range(80)}
    plan = live.review_layout(notes, {"pdf": True, "snippets": sizes}, geo, 2)
    if sorted(n["id"] for n in plan["notes"]) != list(range(80)) or [n["n"] for n in plan["notes"]] != list(range(1, 81)):
        fail("review_layout lost or renumbered notes")
    gutters = {}
    for n in plan["notes"]:
        gutters.setdefault((n["page"], n["side"]), []).append(n)
    for (page, side), group in gutters.items():
        placed = [n for n in group if n["top"] is not None]
        if [n["n"] for n in placed] != sorted(n["n"] for n in placed) or any(n["continued"] for n in placed):
            fail(f"page {page}, {side} gutter: notes out of order")
        for a, b in zip(placed, placed[1:]):
            if a["top"] + a["sizes"][a["size"]]["h"] > b["top"] + 1e-6:
                fail(f"page {page}, {side} gutter: notes {a['n']} and {b['n']} overlap")
        if any(n["top"] is None and not n["continued"] for n in group) or not all(n["size"] == 1 for n in group):
            fail(f"page {page}, {side} gutter: crowded notes not set smaller and continued")
    if plan["pages"] <= plan["paper"]:
        fail("crowded notes did not continue on pages after the paper")
    ok("the review copy's gutters keep notes in order and apart, set crowded ones smaller, then continue them")


def matching_checks():
    """The review copy's matching of the probe's records to notes (live.match_records): two authors'
    notes with the same text, on one line, each get their own record; a protected marker (no note, but
    recorded as its macro typesets it) whose text holds a note's, in another file or below the note in
    its own, gives that note no record, while a note whose typeset text no record has (an unusual macro)
    still gets the record that holds its text."""
    sys.path.insert(0, HERE)
    import live
    templates = {"eb": r"{\color{Emerald}[\textbf{EB:} #1]}", "ab": r"{\color{Blue}[\textbf{AB:} #1]}"}
    notes = [{"id": i, "macro": macro, "file": "a.tex", "line": 5, "last": 5, "body": "Check this",
              "typeset": live.squeeze(templates[macro].replace("#1", "Check this"))} for i, macro in ((1, "eb"), (2, "ab"))]
    records = [("pos", "a.tex", 5, 1, 100.0, 200.0, r"{\color {Emerald}[\textbf {EB:} Check this]}"),
               ("pos", "a.tex", 5, 1, 300.0, 200.0, r"{\color {Blue}[\textbf {AB:} Check this]}")]
    matched = {k: [r[4] for r in v] for k, v in live.match_records(notes, records).items()}
    if matched != {1: [100.0], 2: [300.0]}:
        fail(f"two authors' notes with the same text got each other's records: {matched}")
    note = {"id": 3, "macro": "eb", "file": "b.tex", "line": 43, "last": 44, "body": "human-written",
            "typeset": live.squeeze(templates["eb"].replace("#1", "human-written"))}
    own = ("pos", "b.tex", 44, 8, 415.0, 207.0, r"{\color {Emerald}[\textbf {EB:} human-written]}")
    for where in ("a.tex", "b.tex"):  # (the marker's record comes first, as its page does)
        marker = ("pos", where, 213, 5, 170.0, 222.0, r"{\color {Cyan}{[\textbf {CH:} human-written]}}")
        matched = {k: [r[3] for r in v] for k, v in live.match_records([note], [marker, own]).items()}
        if matched != {3: [8]}:
            fail(f"a protected marker in {where} (page 5) and a note with its text in b.tex (page 8): the note got pages {matched}")
    odd = {**note, "typeset": "[EB:human-written]"}  # (as review_notes would make it of a macro it reads otherwise than TeX)
    if live.match_records([odd], [own]) != {3: [own]}:
        fail("a note whose typeset text no record has lost the record that holds its text")
    ok("the review copy tells apart two authors' notes with the same text, and gives no note a protected marker's record")


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=check", "-c", "user.email=check@example.com", *args], cwd=cwd, check=True,
                   capture_output=True)


def make_paper(tmp):
    subprocess.run(["git", "init", "-q", "--bare", os.path.join(tmp, "remote.git")], check=True)
    paper = os.path.join(tmp, "paper")
    os.makedirs(paper)
    with open(os.path.join(paper, "main.tex"), "w") as f:
        f.write(PAPER)
    words = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu".split()
    with open(os.path.join(paper, "body.tex"), "w") as f:
        f.write("\n\n".join(" ".join(words[(i * 7 + j) % 12] for j in range(90)) for i in range(24)) + "\n")
    git(paper, "init", "-q", "-b", "main")
    git(paper, "add", "-A")
    git(paper, "commit", "-qm", "first")
    git(paper, "remote", "add", "origin", os.path.join(tmp, "remote.git"))
    git(paper, "push", "-q", "-u", "origin", "main")
    with open(os.path.join(paper, "body.tex"), "a") as f:
        f.write("\nomega omega omega.\n")
    git(paper, "commit", "-qam", "unpushed")
    return paper


def start(paper, doc="main", *extra):
    LOGS.append(os.path.join(paper, "..", f"live-{len(LOGS)}.log"))
    log = open(LOGS[-1], "w")
    return subprocess.Popen([sys.executable, os.path.join(HERE, "live.py"), f"{doc}.tex", "--no-open", "--port", str(PORT), *extra],
                            cwd=paper, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, env=ENV)


def request(path, token=None, method="GET", body=None, cookie=None):
    """(status, headers, body) of a request to the hub, without following redirects."""
    conn = http.client.HTTPConnection("127.0.0.1", hub["port"], timeout=30)
    if token:
        path += ("&" if "?" in path else "?") + f"t={token}"
    headers = {"Cookie": f"latex_live={cookie}"} if cookie else {}
    if body is not None:
        headers["Content-Type"] = "application/json"
    conn.request(method, path, json.dumps(body) if body is not None else None, headers)
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r.status, dict(r.getheaders()), data


def get(path, token, doc="main"):
    """A preview's endpoint, through the hub."""
    status, headers, data = request(f"{hub['base']}{doc}{path}", token)
    if status != 200:
        raise OSError(f"{status} {data[:200]!r}")
    kind = headers.get("Content-Type", "")
    return json.loads(data) if kind.startswith("application/json") else data.decode() if kind.startswith("text/") else data


def read_hub():
    """The hub's port and pid (HUB_FILE), if it answers."""
    try:
        with open(os.path.join(ENV["LATEX_LIVE_STATE"], "hub.json")) as f:
            hub.update(json.load(f))
        return request("/hub/ping", TOKEN[0])[0] == 200
    except (OSError, ValueError, KeyError):
        return False


hub, TOKEN = {}, [None]


def ln_records(path):
    with open(path, errors="replace") as f:
        return [line for line in f if "\\@LN" in line]


def stop(proc):
    try:
        os.killpg(proc.pid, signal.SIGCONT)
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(20)
    except ProcessLookupError:
        pass
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)


def live_checks(tmp):
    ENV["LATEX_LIVE_STATE"] = state = os.path.join(tmp, "state")  # (not the user's: their previews keep their list)
    paper = make_paper(tmp)
    for name in ("appendix", "extra"):
        with open(os.path.join(paper, f"{name}.tex"), "w") as f:
            f.write(f"\\documentclass{{article}}\n\\begin{{document}}\nThe {name}.\n\\end{{document}}\n")
    editor_files(paper, state)
    ENV["LATEX_LIVE_EDITOR"], editor_log = editor_stub(tmp)
    procs = [start(paper)]
    try:
        build = os.path.join(paper, "build")
        wait_for("main.pdf", lambda: os.path.isfile(os.path.join(paper, "main.pdf")))
        ok("the build published main.pdf")
        clean_status = os.path.join(build, "clean", "main-clean.status.json")
        wait_for("the notes-free pass", lambda: os.path.isfile(clean_status))
        with open(clean_status) as f:
            if not json.load(f).get("ok"):
                fail(f"the notes-free pass failed: see {build}/clean/main-clean.log")
        if not os.path.isfile(os.path.join(build, "main-clean.pdf")):
            fail("no build/main-clean.pdf")
        ok("the notes-free pass published build/main-clean.pdf")

        with open(os.path.join(state, "token")) as f:
            token = TOKEN[0] = f.read().strip()
        wait_for("the hub", read_hub, timeout=20)
        if hub["port"] != PORT or hub["pid"] != procs[0].pid:
            fail(f"the hub is not on port {PORT} in the first instance: {hub}")
        with open(os.path.join(build, "main-live.lock")) as f:
            own = json.load(f)["port"]
        status, headers, _ = request("/viewer.html?file=main.pdf", token)  # (an old bookmark)
        if status != 302 or not re.fullmatch(r"/p/paper-[0-9a-f]{6}/main/viewer\.html\?file=main\.pdf", headers.get("Location", "")):
            fail(f"/viewer.html?file=main.pdf on the hub: {status} {headers.get('Location')}")
        hub["base"] = headers["Location"][: -len("main/viewer.html?file=main.pdf")]
        ok(f"the hub serves port {PORT} ({hub['base']}main/; the preview's own port is {own})")
        if request(f"{hub['base']}main/status/main.pdf")[0] != 403:
            fail("the hub answers without the token")
        if request(f"{hub['base']}main/status/main.pdf", cookie=token)[0] != 200:
            fail("the hub refuses its cookie")
        ok("the hub needs the token (or its cookie)")
        wrong = http.client.HTTPConnection("127.0.0.1", own, timeout=10)  # (as from a hub that looked up a stale port)
        wrong.request("GET", "/docs", headers={"Cookie": f"latex_live={token}", "X-Latex-Live-Paper": "other-000000"})
        if wrong.getresponse().status != 421:
            fail("a preview answered a request the hub meant for another paper")
        wrong.close()
        ok("a preview refuses a request meant for another paper (421), so the hub looks again")
        if request(f"{hub['base']}main/status/main.pdf", method="HEAD")[0] != 405 or request("/VERSION", method="HEAD")[0] != 405:
            fail("HEAD is answered (it would skip the token)")
        big = http.client.HTTPConnection("127.0.0.1", hub["port"], timeout=10)
        big.putrequest("POST", f"{hub['base']}main/note")
        for key, value in (("Cookie", f"latex_live={token}"), ("Content-Type", "application/json"), ("Content-Length", "1000000000")):
            big.putheader(key, value)
        big.endheaders()
        if big.getresponse().status != 413:
            fail("the hub accepts a huge POST body")
        big.close()
        direct = http.client.HTTPConnection("127.0.0.1", own, timeout=10)
        direct.request("HEAD", "/status/main.pdf", headers={"Cookie": f"latex_live={token}"})
        if direct.getresponse().status != 405:
            fail("a preview answers HEAD on its own port")
        direct.close()
        for length in ("-1", "abc", "1000000000"):
            direct = http.client.HTTPConnection("127.0.0.1", own, timeout=10)
            direct.putrequest("POST", "/note")
            for key, value in (("Cookie", f"latex_live={token}"), ("Content-Type", "application/json"), ("Content-Length", length)):
                direct.putheader(key, value)
            direct.endheaders()
            if direct.getresponse().status not in (400, 413):
                fail(f"a preview accepts Content-Length: {length} on its own port")
            direct.close()
        ok("the hub and the previews refuse HEAD and bad or oversized POST bodies")

        for path in ("/status/main.pdf?base=push", "/outline/main.pdf", "/notes/main.pdf", "/authors/main.pdf",
                     "/check/main.pdf", "/graphics/main.pdf", "/margin/main.pdf", "/floats/main.pdf", "/docs",
                     "/commits", "/papers", "/viewer.html", "/pdfjs/build/pdf.min.mjs", "/pdf/main.pdf"):
            try:
                get(path, token)
            except Exception as e:
                fail(f"{path} (through the hub): {e}")
        ok("the viewer's endpoints answer through the hub")
        with open(os.path.join(HERE, "VERSION")) as f:
            version = f.read().strip()
        reported = get("/status/main.pdf", token).get("latexLive")
        if reported != {"version": version, "updated": False}:
            fail(f"/status reports latexLive {reported}, not version {version}, not updated")
        ok(f"/status reports version {version}")
        notes = get("/notes/main.pdf", token)
        if not any("hiding it moves" in n.get("text", "") for n in notes):
            fail(f"/notes does not list the note: {str(notes)[:300]}")
        target = get("/forward?file=main.tex&line=9", token)
        if not target.get("page"):
            fail(f"SyncTeX found no target for main.tex:9: {target}")
        ok(f"SyncTeX: main.tex:9 is on page {target['page']}")
        listed = get("/instances", token)
        if not any(i["pid"] == procs[0].pid and hub["base"] == f"/p/{i['id']}/" for i in listed["instances"]):
            fail(f"/instances does not list the instance with its paper id: {listed}")
        ok("/instances lists the instance")

        def baselines():
            info = get("/status/main.pdf?base=push", token)
            head, push = info.get("head") or {}, info.get("push") or {}
            failed = [b for b in (head, push) if b.get("state") == "failed"]
            if failed:
                fail(f"a baseline build failed: {failed}")
            return info if head.get("clean") and push.get("clean") else None

        info = wait_for("the HEAD and last-push baselines", baselines)
        if info["head"]["sha"] == info["push"]["sha"]:
            fail(f"the last-push baseline is HEAD, but a commit is unpushed: {info}")
        ok(f"baselines: HEAD {info['head']['sha']}, last push {info['push']['sha']} ({info['push']['ref']})")
        clean = ln_records(os.path.join(build, "clean", "main-clean.aux"))
        head_clean = ln_records(os.path.join(build, "head", "main", "main-head-clean.aux"))
        if not clean:
            fail("the notes-free .aux has no lineno records")
        if clean != head_clean:
            fail("the notes-free HEAD baseline's line numbers differ from the live notes-free build's")
        ok("the notes-free baseline's line numbers match the live notes-free build's")
        status, _, data = request(f"{hub['base']}main/rebuild", method="POST", body={}, cookie=token)
        if status != 202:
            fail(f"POST /rebuild through the hub: {status} {data[:200]!r}")
        ok("POST /rebuild works through the hub")
        exported = wait_for("the PDF without notes", lambda: (lambda e: e if e.get("state") in ("done", "failed") else None)(
            get("/export", token)), timeout=240)
        if exported["state"] != "done":
            fail(f"the build without notes failed: {exported.get('msg')}")
        pdf = get("/pdf/main-export.pdf", token)
        log = open(os.path.join(build, "export", "main", "main-export.log"), errors="replace").read()
        if not pdf.startswith(b"%PDF") or re.search(r"Rerun to get|may have changed\. Rerun", log):
            fail("the PDF without notes is missing, or its references had not settled")
        if get("/export", token).get("version") != exported["version"]:
            fail("asking again rebuilt the PDF without notes, though the document had not changed")
        if ln_records(os.path.join(build, "export", "main", "main-export.aux")) != ln_records(os.path.join(build, "clean", "main-clean.aux")):
            fail("the download without notes has its line numbers in other places than the notes-free view")
        ok("the download without notes is an exact build (line numbers too), made once per build")
        review_checks(build, token)
        editor_checks(paper, state, token, own, editor_log)

        procs.append(start(paper, "appendix"))  # (a second document of the same paper, in its own process)
        wait_for("appendix.pdf through the hub", lambda: _quiet(lambda: get("/status/appendix.pdf", token, "appendix")["version"]))
        routed = wait_for("the appendix route", lambda: _quiet(lambda: (get("/instances", token, "appendix")["self"],
                                                                        get("/docs", token, "appendix")["doc"])), timeout=20)
        if routed != (procs[1].pid, "appendix"):
            fail(f"/p/.../appendix/ reaches process {routed[0]} building {routed[1]}, not the appendix's {procs[1].pid}")
        page = request("/papers", token)[2].decode()
        if f"{hub['base']}main/viewer.html" not in page or f"{hub['base']}appendix/viewer.html" not in page:
            fail(f"/papers does not link both documents through the hub:\n{page[:600]}")
        ok("a second document gets its own process, behind the same port")
        failed = wait_for("the appendix's review copy", lambda: _quiet(lambda: (lambda e: e if e.get("state") in ("done", "failed")
                                                                          else None)(get("/review", token, "appendix"))), timeout=120)
        if failed.get("state") != "failed" or "no notes" not in failed.get("msg", "") or \
                os.path.exists(os.path.join(build, "appendix-review.pdf")):
            fail(f"a review copy of a document without notes: {failed}")
        ok("a document without notes gets no review copy, and says why")
        ghost = os.path.join(state, "instances", "ghost.json")  # (a stale entry: another paper's id, this preview's port)
        with open(ghost, "w") as f:
            json.dump({"port": own, "pid": procs[1].pid, "root": "/elsewhere", "doc": "main", "id": "ghost-000000"}, f)
        status, _, data = wait_for("the hub to see the stale entry", lambda: (lambda r: r if r[0] != 404 else None)(
            request("/p/ghost-000000/main/docs", cookie=token)), timeout=10)  # (it rescans at most every 0.5 s)
        os.remove(ghost)
        if status != 502:
            fail(f"the hub forwarded a request for another paper to this preview's port: {status} {data[:200]!r}")
        ok("the hub, refused by a preview that is another paper's, looks again and doesn't misroute")
        if get("/switch?doc=extra", token).get("doc") != "extra":
            fail("switching main's preview to extra failed")
        if wait_for("main's old route", lambda: _quiet(lambda: get("/docs", token)["doc"]), timeout=20) != "extra":
            fail("after a switch, the old document's viewers no longer reach their preview")
        if get("/switch?doc=main", token, "extra").get("doc") != "main":
            fail("switching back to main failed")
        wait_for("main again", lambda: _quiet(lambda: get("/docs", token)["doc"] == "main"), timeout=20)
        ok("after a switch (two documents running), the old document's viewers still reach their preview")

        second = subprocess.run([sys.executable, os.path.join(HERE, "live.py"), "main.tex", "--no-open", "--port", str(PORT)],
                                cwd=paper, capture_output=True, text=True, timeout=60, env=ENV)
        if "already running" not in second.stdout or f":{PORT}{hub['base']}main/" not in second.stdout or second.returncode:
            fail(f"a second instance did not report the first, with its hub URL:\n{second.stdout}{second.stderr}")
        ok("a second instance reports the running one (its hub URL) and exits")
        procs.append(start(paper, "main", "--replace"))
        try:
            procs[0].wait(30)
        except subprocess.TimeoutExpired:
            fail("--replace did not stop the first instance")
        wait_for("the hub to move to another instance", lambda: read_hub() and hub["pid"] != procs[0].pid, timeout=20)
        if hub["port"] != PORT:
            fail(f"the hub moved to port {hub['port']}")
        wait_for("the replacement, through the hub", lambda: _quiet(lambda: get("/instances", token)["self"] == procs[2].pid))
        listed = get("/instances", token)["instances"]
        if sorted(i["pid"] for i in listed if i["root"] == os.path.realpath(paper)) != sorted([procs[1].pid, procs[2].pid]):
            fail(f"/instances after --replace: {listed}")
        ok(f"--replace takes over; the hub stays on port {PORT} (now in process {hub['pid']})")

        main_proc = procs[2]
        os.kill(main_proc.pid, signal.SIGSTOP)  # (holds the lock, answers nothing)
        third = subprocess.Popen([sys.executable, os.path.join(HERE, "live.py"), "main.tex", "--no-open", "--port", str(PORT)],
                                 cwd=paper, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True,
                                 env=ENV)
        procs.append(third)
        try:
            first_line = third.stdout.readline()
            threading.Thread(target=third.stdout.read, daemon=True).start()  # (keep reading: a full pipe would stall it)
            main_proc.wait(30)
        except subprocess.TimeoutExpired:
            fail("an instance that does not answer was not replaced")
        if "not answering" not in first_line:
            fail(f"replacing an instance that does not answer: {first_line}")
        wait_for("the instance that replaced the one not answering", lambda: _quiet(lambda: get("/instances", token)["self"] == third.pid))
        ok("an instance that does not answer is replaced")

        wait_for("the hub, after that", lambda: read_hub() and hub["pid"] != main_proc.pid, timeout=20)
        stuck = hub["pid"]
        os.kill(stuck, signal.SIGSTOP)  # (serves the hub, answers nothing)
        procs.append(start(paper, "extra"))  # (another document: only the hub's check can stop the stuck one)
        holder = next(p for p in procs if p.pid == stuck)
        try:
            holder.wait(60)  # (three tries over about 10 s first)
        except subprocess.TimeoutExpired:
            fail("a new instance did not replace the one serving the hub without answering")
        with open(LOGS[-1]) as f:
            if "serves the hub but is not answering" not in f.read():
                fail("the hub that did not answer was stopped some other way")
        wait_for("the hub to come back", lambda: read_hub() and hub["pid"] != stuck, timeout=20)
        if hub["port"] != PORT:
            fail(f"the hub came back on port {hub['port']}")
        ok("a hub that does not answer is replaced")
    finally:
        for proc in procs:
            stop(proc)
    left = os.listdir(os.path.join(state, "instances"))
    if left:
        fail(f"stopped instances are still listed: {left}")
    ok("stopped instances are no longer listed")


def editor_files(paper, state):
    """Files for editor_checks beside the scratch paper's sources: .bib files it lists (one in a folder, one
    with CRLF line ends and mode 0640), and ones it must not offer: not UTF-8, too big, a dot file, and a
    link to the token, outside the paper."""
    os.makedirs(os.path.join(paper, "sub"))
    for name, data in (("refs.bib", b"@misc{one, title = {One}}\n"), ("sub/refs.bib", b"@misc{two, title = {Two}}\n"),
                       ("crlf.bib", b"@misc{three,\r\n  title = {Three}}\r\n"), ("latin.bib", "@misc{caf\u00e9}\n".encode("latin-1")),
                       ("big.bib", b"%" * (5 << 20) + b"\n"), (".private.bib", b"@misc{hidden}\n")):
        with open(os.path.join(paper, name), "wb") as f:
            f.write(data)
    os.chmod(os.path.join(paper, "crlf.bib"), 0o640)
    os.symlink(os.path.join(state, "token"), os.path.join(paper, "leak.bib"))


def editor_stub(tmp):
    """A stand-in for the editor's command-line tool (LATEX_LIVE_EDITOR) that only logs its arguments, one
    call per line: (its path, the log's)."""
    stub, log = os.path.join(tmp, "editor.sh"), os.path.join(tmp, "editor.log")
    with open(stub, "w") as f:
        f.write(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{log}'\n")
    os.chmod(stub, 0o755)
    return stub, log


def editor_calls(log):
    try:
        with open(log) as f:
            return f.read().splitlines()
    except FileNotFoundError:
        return []


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def version_of(data):
    """A file version as the editor API gives it: the first 20 hex digits of the sha256 of its bytes."""
    return hashlib.sha256(data).hexdigest()[:20]


def with_paragraph(text, i, new):
    """body.tex's text with its i-th paragraph (one line each, between blank lines) replaced."""
    lines = text.split("\n")
    lines[2 * i] = new
    return "\n".join(lines)


def post_length(port, path, length, token):
    """The status of a POST that announces length bytes but sends none, so the server must refuse it from
    the header alone (None: no answer within 10 s)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.putrequest("POST", path)
        for key, value in (("Cookie", f"latex_live={token}"), ("Content-Type", "application/json"), ("Content-Length", str(length))):
            conn.putheader(key, value)
        conn.endheaders()
        return conn.getresponse().status
    except (OSError, http.client.HTTPException):
        return None
    finally:
        conn.close()


def editor_checks(paper, state, token, own, editor_log):
    """The browser editor, through the hub: /me, its modules, SyncTeX as JSON (which opens nothing), the
    files API (listing, reading, saving, merging, conflicts, refusals), status ?open=, and the POST size
    limits for a source file's save."""
    def send(path, body=None):
        status, _, data = request(f"{hub['base']}main{path}", method="POST" if body is not None else "GET", body=body, cookie=token)
        try:
            return status, json.loads(data)
        except ValueError:
            return status, {"raw": data[:200]}

    def expect(path, body, wanted, what):
        status, answer = send(path, body)
        if status not in ((wanted,) if isinstance(wanted, int) else wanted):
            fail(f"{what}: {status} {str(answer)[:300]}, not {wanted}")
        return answer

    def save(body, wanted, what):
        return expect("/file", body, wanted, what)

    me = expect("/me", None, 200, "GET me")
    if me.get("role") != "owner" or (me.get("can") or {}).get("edit") is not True:
        fail(f"GET me does not say that the owner may edit: {me}")
    status, headers, data = request(f"{hub['base']}main/codemirror/view.js", token)
    kind = next((value for key, value in headers.items() if key.lower() == "content-type"), "")
    if status != 200 or data != read_bytes(os.path.join(HERE, "codemirror", "view.js")) or not JS_TYPE_RE.match(kind):
        fail(f"/codemirror/view.js through the hub: {status} {kind}")
    ok("GET me says the owner may edit; the editor's modules are served as JavaScript")

    box = expect("/forward?file=body.tex&line=5", None, 200, "forward search for body.tex:5")
    found = None
    for share in (0.5, 0.3, 0.7):
        query = f"file=main.pdf&page={box['page']}&x={box['x'] + box['w'] * share:.2f}&y={box['y'] + box['h'] / 2:.2f}&json=1"
        found = expect(f"/synctex?{query}", None, 200, "synctex with json=1")
        if found.get("file") == "body.tex" and isinstance(found.get("line"), int) and abs(found["line"] - 5) <= 3:
            break
    else:
        fail(f"synctex with json=1 maps no point of body.tex:5 back to it: {found}")
    if editor_calls(editor_log):
        fail(f"synctex with json=1 opened the editor: {editor_calls(editor_log)}")
    expect(f"/synctex?{query.replace('&json=1', '')}", None, 200, "plain synctex")
    calls = wait_for("plain synctex to run the editor stand-in", lambda: editor_calls(editor_log), timeout=10)
    if len(calls) != 1 or not calls[0].startswith("-g ") or "/body.tex:" not in calls[0]:
        fail(f"plain synctex ran the editor stand-in with {calls}, not -g .../body.tex:N")
    ok("SyncTeX answers JSON with the file and line, without opening the editor; plain synctex still opens it")

    path, crlf_path = os.path.join(paper, "body.tex"), os.path.join(paper, "crlf.bib")
    os.makedirs(os.path.join(paper, "build"), exist_ok=True)
    with open(os.path.join(paper, "build", "stray.bib"), "w") as f:
        f.write("@misc{stray}\n")
    listing = expect("/files", None, 200, "GET files")
    paths = [f.get("path") for f in listing.get("files") or []]
    if listing.get("main") != "main.tex" or paths[:1] != ["main.tex"] or paths[1:] != sorted(paths[1:]):
        fail(f"GET files does not list main.tex first, then the rest by path: {listing}")
    missing = [p for p in ("body.tex", "refs.bib", "sub/refs.bib", "crlf.bib", "latin.bib") if p not in paths]
    offered = [p for p in paths if p.startswith(("build/", ".git/")) or p in (".private.bib", "leak.bib", "big.bib", "appendix.tex")]
    if missing or offered:
        fail(f"GET files: missing {missing}, offering {offered}: {paths}")
    ok("GET files lists the paper's sources and .bib files (main.tex first), not build/, .git, dot files, links out, big files"
       " or other documents")

    disk = read_bytes(path)
    got = expect("/file?path=body.tex", None, 200, "GET file body.tex")
    if (got.get("text"), got.get("version"), got.get("eol")) != (disk.decode(), version_of(disk), "\n"):
        fail(f"GET file body.tex: {str(got)[:300]}")
    new = with_paragraph(got["text"], 3, "Paragraph three, saved by the editor.")
    saved = save({"path": "body.tex", "base": got["version"], "text": new}, 200, "a save on the current version")
    if read_bytes(path).decode() != new or saved.get("version") != version_of(new.encode()) or saved.get("merged"):
        fail(f"a save on the current version: {str(saved)[:300]}")
    base = expect("/file?path=body.tex", None, 200, "GET file body.tex")
    put(path, with_paragraph(base["text"], 20, "Paragraph twenty, changed on disk meanwhile."))
    older = with_paragraph(base["text"], 1, "Paragraph one, saved from an older copy.")
    merged = save({"path": "body.tex", "base": base["version"], "text": older}, 200, "a save on an older version")
    disk = read_bytes(path).decode()
    if (merged.get("merged") is not True or merged.get("text") != disk or "Paragraph twenty, changed" not in disk
            or "Paragraph one, saved from an older copy." not in disk):
        fail(f"a save on an older version did not merge with the change on disk: {str(merged)[:300]}")
    base = expect("/file?path=body.tex", None, 200, "GET file body.tex")
    theirs = with_paragraph(base["text"], 1, "Paragraph one, changed on disk (theirs).")
    mine = with_paragraph(base["text"], 1, "Paragraph one, changed in the browser (mine).")
    put(path, theirs)
    clash = save({"path": "body.tex", "base": base["version"], "text": mine}, 409, "a save that conflicts with the file on disk")
    marked = str(clash.get("merged"))
    if (read_bytes(path).decode() != theirs or clash.get("conflict") is not True or clash.get("text") != theirs
            or "<<<<<<< saved meanwhile" not in marked or ">>>>>>> your edit" not in marked or "|||||||" in marked):
        fail(f"a conflicting save wrote, or answered otherwise than with both texts and git's merge: {str(clash)[:300]}")
    save({"path": "body.tex", "base": clash["version"], "text": mine, "force": True}, 200, "the same save with force")
    if read_bytes(path).decode() != mine:
        fail("a save with force did not write the text")
    copy = with_paragraph(mine, 18, "Paragraph eighteen, changed on disk before the copy.")
    put(path, copy)  # (a version the server never served or wrote)
    put(path, with_paragraph(copy, 22, "Paragraph twenty-two, changed on disk after the copy."))
    edit = with_paragraph(copy, 3, "Paragraph three, saved again from that copy.")
    stale = save({"path": "body.tex", "base": version_of(copy.encode()), "text": edit}, 409, "a save on a version the server never saw")
    if stale.get("stale") is not True or stale.get("text") != read_bytes(path).decode():
        fail(f"a save on an unknown version: {str(stale)[:300]}")
    again = save({"path": "body.tex", "base": version_of(copy.encode()), "base_text": copy, "text": edit}, 200,
                 "the same save with its base text")
    text = read_bytes(path).decode()
    if again.get("merged") is not True or any(t not in text for t in ("eighteen, changed", "twenty-two, changed", "three, saved again")):
        fail(f"a save with its base text did not merge: {str(again)[:300]}")
    base = expect("/file?path=body.tex", None, 200, "GET file body.tex")
    save({"path": "body.tex", "base": base["version"], "text": with_paragraph(base["text"], 7, "Seven, saved once."),
          "editor": "tab-a"}, 200, "a save (whose answer the editor never gets)")
    more = with_paragraph(base["text"], 7, "Seven, saved once, then typed on.")
    save({"path": "body.tex", "base": base["version"], "text": more, "editor": "tab-a"}, 200,
         "the next save of that line, from the version before")
    if read_bytes(path).decode() != more:
        fail("the save after one whose answer got lost did not write its text")
    other = with_paragraph(base["text"], 7, "Seven, from another tab.")
    clash = save({"path": "body.tex", "base": base["version"], "text": other, "editor": "tab-b"}, 409,
                 "another tab's save of that line, from the same version")
    if clash.get("conflict") is not True or read_bytes(path).decode() != more:
        fail("another tab's save of the same line overwrote the first tab's instead of conflicting")
    base = expect("/file?path=body.tex", None, 200, "GET file body.tex")
    save({"path": "body.tex", "base": base["version"], "text": with_paragraph(base["text"], 9, "Nine, saved, then undone."),
          "editor": "tab-a"}, 200, "a save (whose answer the editor never gets)")
    save({"path": "body.tex", "base": base["version"], "text": base["text"], "editor": "tab-a"}, 200,
         "the same tab's save after undoing that edit")
    if read_bytes(path).decode() != base["text"]:
        fail("an edit undone after a save whose answer got lost stayed in the file")
    ok("a save writes; a stale save merges; a conflict writes nothing and answers both texts; force writes; an unknown base"
       " asks for its text; a save whose answer got lost doesn't conflict with the same tab's next one (and an undo of"
       " it undoes it in the file), but another tab's does")

    crlf = expect("/file?path=crlf.bib", None, 200, "GET file crlf.bib")
    raw = read_bytes(crlf_path)
    if crlf.get("eol") != "\r\n" or crlf.get("text") != raw.decode().replace("\r\n", "\n"):
        fail(f"GET file of a CRLF file: {crlf}")
    edited = crlf["text"].replace("Three", "Four")
    save({"path": "crlf.bib", "base": crlf["version"], "text": edited}, 200, "a save of a CRLF file")
    if read_bytes(crlf_path) != edited.replace("\n", "\r\n").encode() or os.stat(crlf_path).st_mode & 0o777 != 0o640:
        fail(f"a save of a CRLF file did not keep its line ends and mode: {read_bytes(crlf_path)!r}")
    expect("/file?path=latin.bib", None, 415, "GET file of a file that is not UTF-8")
    token_file = os.path.join(state, "token")
    for name in ("../state/token", token_file, ".git/config", "build/main-live.log", "build/stray.bib", "leak.bib", "big.bib",
                 ".private.bib", "appendix.tex", "nope.tex", "%2e%2e/state/token"):
        expect("/file?path=" + urllib.parse.quote(name, safe="%"), None, 404, f"GET file {name!r} (not editable)")
    before = {p: read_bytes(p) for p in (token_file, os.path.join(paper, ".git", "config"), os.path.join(paper, "build", "stray.bib"))}
    for name in ("../state/token", token_file, "leak.bib", ".git/config", "build/stray.bib", "big.bib", "new.tex", "sub/new.bib"):
        expect("/file", {"path": name, "base": "0" * 20, "text": "Overwritten.\n", "force": True}, 404, f"POST file {name!r}")
    if (any(read_bytes(p) != data for p, data in before.items()) or os.path.exists(os.path.join(paper, "new.tex"))
            or os.path.exists(os.path.join(paper, "sub", "new.bib")) or os.path.getsize(os.path.join(paper, "big.bib")) != (5 << 20) + 1):
        fail("a refused save changed or made a file")
    ok("CRLF files keep their line ends (and saves the mode); files that are not UTF-8 get 415; other paths get 404")

    def opened(name):
        source = expect("/status/main.pdf?open=" + urllib.parse.quote(name, safe=""), None, 200, f"status ?open={name}").get("source")
        return (source.get("path"), source.get("version")) if isinstance(source, dict) else (None, None)

    put(path, with_paragraph(read_bytes(path).decode(), 5, "Paragraph five, changed on disk while open."))
    if opened("body.tex") != ("body.tex", version_of(read_bytes(path))) or opened("leak.bib")[1] or opened("../state/token")[1]:
        fail("status ?open= does not give an open file's version as on disk, or gives one for a file the editor doesn't open")
    status, _, data = request(f"{hub['base']}main/file", method="POST", cookie=token,
                              body={"path": "body.tex", "base": "0" * 20, "text": "x" * (1 << 20)})
    if status != 409 or not _quiet(lambda: json.loads(data)["stale"]):
        fail(f"a 1 MiB save through the hub: {status} {data[:200]!r} (want 409 stale: within the 8 MB limit for files)")
    for where, port, path_, length in (("the hub", hub["port"], f"{hub['base']}main/file", 9 << 20),
                                       ("the hub", hub["port"], f"{hub['base']}main/note", 100_001),
                                       ("the preview's own port", own, "/file", 9 << 20)):
        if (status := post_length(port, path_, length, token)) != 413:
            fail(f"a {length}-byte POST {path_} on {where}: {status}, not 413")
    ok("status ?open= follows the open file; a 1 MiB save goes through the hub, and bodies past the limits are refused")


def written_pages(log):
    """The page count of a pdflatex run, from its log."""
    with open(log, errors="replace") as f:
        m = re.search(r"Output written on .*?\((\d+) pages?", f.read().replace("\n", ""))
    return int(m.group(1)) if m else None


def drawn_pages(pdf, left):
    """How many times a PDF's content streams draw an image or form unscaled with its bottom left at
    (left, 0): in the review copy of a two-column paper (a gutter on the left, so the paper's own
    figures don't count), once per page of the paper."""
    found = 0
    for m in re.finditer(rb"stream\r?\n", pdf):
        try:
            content = zlib.decompress(pdf[m.end():pdf.find(b"endstream", m.end())])
        except zlib.error:
            continue
        found += len(re.findall(rb"\b1 0 0 1 %s 0 cm\s*/\w+ Do" % f"{left:g}".encode(), content))
    return found


def review_checks(build, token):
    """The review copy of the scratch paper: the export's pages on wider ones, every note beside its
    column, numbered and apart, without the protected marker or the resolved note."""
    reviewed = wait_for("the review copy", lambda: (lambda e: e if e.get("state") in ("done", "failed") else None)(
        get("/review", token)), timeout=240)
    if reviewed["state"] != "done":
        fail(f"the review copy failed: {reviewed.get('msg')}")
    review = get("/pdf/main-review.pdf", token)
    if not review.startswith(b"%PDF"):
        fail("no review copy to download")
    with open(os.path.join(build, "review", "main", "main-review.json")) as f:
        plan = json.load(f)
    pages = written_pages(os.path.join(build, "export", "main", "main-export.log"))
    if not pages or plan["paper"] != pages or plan["pages"] != pages or \
            written_pages(os.path.join(build, "review", "main", ".copy", "main-review.log")) != pages:
        fail(f"the review copy has {plan['pages']} pages ({plan['paper']} of the paper), the export {pages}")
    if plan["width"] <= plan["W"]:
        fail(f"the review copy's pages are no wider than the paper's ({plan['width']} <= {plan['W']})")
    if drawn_pages(review, plan["left"]) != pages:
        fail(f"the review copy should place each page of the paper unscaled, {plan['left']} points from its left edge:"
             f" {drawn_pages(review, plan['left'])} of {pages} are")
    texts = [n["text"] for n in plan["notes"]]
    want = ["hiding it moves", "a note in a paragraph", "a note in a footnote"]
    if len(texts) != len(want) or any(sum(w in t for t in texts) != 1 for w in want):
        fail(f"the review copy's notes are not the three expected (no protected marker, resolved note, or note in a branch"
             f" that is off): {texts}")
    sides = {n["side"] for n in plan["notes"]}
    if sides != {"left", "right"}:
        fail(f"notes of both columns should go to both gutters: {sides}")
    exact = [n["text"][:20] for n in plan["notes"] if n["how"] != "exact" and "hiding it moves" not in n["text"]]
    if exact:
        fail(f"notes in a paragraph or a footnote placed only by source line: {exact}")
    placed = [n for n in plan["notes"] if n["top"] is not None]
    for a in placed:
        for b in placed:
            if a is not b and (a["page"], a["side"]) == (b["page"], b["side"]) and a["top"] <= b["top"] < a["top"] + a["sizes"][a["size"]]["h"]:
                fail(f"notes {a['n']} and {b['n']} overlap")
    with open(os.path.join(build, "review", "main", ".copy", "main-review.log"), errors="replace") as f:
        log = f.read()
    if not os.path.isfile(os.path.join(build, "review", "main", "lmodern.sty")):
        fail("the probe pass wrote no lmodern.sty into its folder (the scratch paper has it write one)")
    if "latex-live-check: a package that TeX wrote ran" in log:
        fail("the review copy's last pass loaded lmodern.sty from the folder the paper's TeX writes into")
    if reviewed.get("version") != get("/review", token).get("version"):
        fail("asking again rebuilt the review copy, though the document had not changed")
    ok(f"the review copy: {pages} pages as exported, unscaled on wider ones, with {len(texts)} notes in both gutters, apart"
       " (the protected marker, the resolved note, and the one in a branch that is off left out); its last pass loads no"
       " package that the paper's TeX wrote")


def _quiet(test):
    try:
        return test()
    except (OSError, ValueError, KeyError, TypeError):
        return None


def main():
    static_checks()
    with tempfile.TemporaryDirectory(prefix="latex-live-check-") as tmp:
        live_checks(tmp)
    print("all checks passed")


if __name__ == "__main__":
    main()
