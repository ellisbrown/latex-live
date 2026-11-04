#!/usr/bin/env python3
"""Smoke test for latex-live: python3 check.py (a minute or two; needs TeX, as live.py does).

1. The Python compiles without warnings (and with Python 3.9, if there is one); the viewer's import
   map is valid JSON, comes before its module code, and maps every bare module name that the viewer
   and the vendored editor (codemirror/) import to a file here; the viewer's script parses (with
   Node, if there is one); instances starting at once agree on one token, never an empty one.
   The review copy's layout keeps notes in order, apart, and on their pages, and continues the ones
   that don't fit after the paper; its matching tells apart two authors' notes with the same text,
   and gives no note the record of a protected marker that holds its text. The downloads read TeX's
   logs, .aux, .bbl and SyncTeX files only up to a bound, and say so past it (a stand-in for TeX
   writes the probe pass's log), and read a log whose wrapped lines split a UTF-8 character.
   In shared mode, the downloads built on request say plainly that the TeX Live image is missing, the
   review copy reads its notes from the files of the last good build (only the paper's own), and
   the inputs kept for its probe leave out symbolic links.
2. A scratch paper in a temporary directory (two columns, lineno line numbers, inline notes, a git
   repository with a pushed commit and a local one) gets a live.py instance, with the hub on port
   44180 (or the first free block of ports above it; LATEX_LIVE_CHECK_PORT sets it), state of its own
   (not the user's) and none of the user's LATEX_LIVE_* settings:
   - the build publishes main.pdf, and the notes-free pass main-clean.pdf;
   - the hub redirects an old bookmark, needs the token or its cookie, and forwards every
     endpoint (POST /rebuild too); the hub and the previews refuse HEAD and bad or oversized POST
     bodies; SyncTeX finds a source line; /instances lists the instance;
   - /me reports the owner with every capability; the editor's modules are served; SyncTeX answers
     as JSON without opening the editor, while GET /open, POST /open and plain /synctex still open
     it (a stand-in that logs its arguments); the hub takes a source file's save above the other
     actions' size limit, refuses bodies above its limits, and survives a non-ASCII token;
     /favicon.ico is answered, not refused;
   - the HEAD and last-push baselines build (they differ: one commit is unpushed), and the
     notes-free baseline's line numbers match the live notes-free build's (two passes with lineno);
   - the download without notes is an exact build (its line numbers match the notes-free view's),
     made once per build of the document, and the review copy places its pages on wider ones with
     every note in a gutter (none overlapping, the protected marker and the resolved note left out);
     a document without notes gets a clear failure, not a file;
   - POST /switch moves the preview to another document and GET /switch back, and POST /diff and
     GET /diff build a latexdiff (the viewer's POST forms and the old GET ones, through the hub);
   - a second document runs in its own process behind the same port, and /papers links both; the
     hub, refused by a preview that a stale entry says is another paper's, looks again and doesn't
     misroute; after a switch, the old document's viewers still reach their preview;
   - a second instance for the same document says it is already running; --replace takes over,
     and so does any new instance when the running one no longer answers; the hub moves to
     another instance on the same port, and a hub that no longer answers is replaced;
   - stopping the instance removes it from /instances.
3. A shared-mode instance (LATEX_LIVE_USERS) on the hub's port + 11 serves a clone of the paper on branch
   "shared" to users of every role, behind a stand-in for an authenticating proxy (an identity
   header), with personal-link tokens, auto-commit, protected text and the editor stand-in. Shared
   mode builds only in the TeX Live image: where podman or the image is missing, all of this part
   but its first two checks (which need neither) is skipped, with a "skip:" line.
   - tex-exec and --host-tex refuse the host's TeX, and tex-exec's container options are hardened
     (10 minutes at most, among others); it builds in the hardened image (without the paper's
     latexmkrc; TeX can't read absolute paths), serves exactly its port and only loopback
     peers, prints its public URL and no token anywhere (nor does a second instance that finds it
     running), and stays out of the hub; edits it finds uncommitted at the start are committed as the
     owner's;
   - Host names, the identity header (none, two, empty, unknown, on another Host), tokens and
     cookies, a page that says what to do for a browser without a working link, users added (and a
     broken users file) without a restart, /me (whether the paper defines the user's note macro, and
     no document switch, for anyone) and every endpoint per role, the Origin rules for POSTs, POST
     body limits, and non-owners pinned to the document;
   - the download without notes: a viewer may ask; several asking at once share one build, made once
     per build of the document, in the hardened image;
   - the copy with notes in the margin (/review), likewise: a viewer may ask, several asking at once
     share one build, made once per build of the document; another document's copy and POST /review
     are refused; its probe pass can't read absolute paths either; a request for it or for the export
     from another site's page (Sec-Fetch-Site, or an Origin not the viewer's) is refused and starts
     nothing; and the host's TeX never runs (a stand-in for it on PATH);
   - notes as oneself only: the editor's own macro yes, the owner's no, others' notes no,
     resolving anyone's yes, protected text kept, a macro the paper lacks explained; notes, the
     list of notes and the note macros keep working while the paper rebuilds;
   - the editor's files API: what it lists and refuses (../, absolute paths, %2e%2e, .git, build/,
     a symlink to the token, a new file, too big, not UTF-8), reading, saving, a stale save that
     merges, a conflict that writes nothing (and says who saved meanwhile), a stale save without and
     then with its base text, a save whose answer got lost followed by more typing on that line,
     CRLF files; status ?open=; SyncTeX as JSON that never opens the editor (only the owner's POST
     open does), with a line past a file's end moved to its last line; diffs only against commits
     that HEAD builds on;
   - auto-commit: each user's edits are committed as that user (by the owner) once they are
     quiet, or at the owner's POST commit, only the files the server wrote, on the shared branch
     only, past the paper's hooks and signing settings, and after a git lock goes away; a write to
     a file that holds another user's uncommitted edits waits for their commit (503 while git is
     busy), and every write waits out a merge in the shared copy (503); the HEAD baseline is not
     rebuilt for each commit; the edits build;
   - an edit still uncommitted when the instance stops (a merge in progress) is committed as its
     writer's at the next start; restarted with an https public URL, cookies are Secure on its
     Host; the identity header from a peer whose uid is not LATEX_LIVE_PROXY_UID is not trusted
     (Linux).
Exits non-zero on the first failure.
"""

import glob
import gzip
import hashlib
import http.client
import ipaddress
import json
import os
import re
import secrets
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


def free_ports(first=44180, count=12, last=44999):
    """The first of count consecutive ports from first on that are all free now. On a busy machine an
    outgoing connection can hold a port in this range (it is inside Linux's ephemeral range), and the
    hub needs its exact port; LATEX_LIVE_CHECK_PORT picks the block instead."""
    base = first
    while base + count - 1 <= last:
        busy = next((port for port in range(base, base + count) if not port_free(port)), None)
        if busy is None:
            return base
        base = busy + 1  # (no block that holds the busy port will do)
    sys.exit(f"check: no {count} free consecutive ports from {first} to {last}")


def port_free(port):
    """Whether a server could listen on port now, on every address (as live.py does: "::", dual-stack)."""
    with socket.socket(socket.AF_INET6) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        try:
            s.bind(("::", port))
        except OSError:
            return False
    return True


PORT = int(os.environ.get("LATEX_LIVE_CHECK_PORT") or free_ports())  # the hub's; the previews' own ports follow
SHARED_PORT = PORT + 11  # the shared-mode instance's (shared_setup), past the previews' ports
PUBLIC = "http://paper.invalid:8443"  # its LATEX_LIVE_PUBLIC_URL: the origin browsers would use
PROXY_HOST = "proxy.invalid"  # its LATEX_LIVE_PROXY_HOST: the Host of the proxy's requests (via_proxy)
CAPS = ("comment", "edit", "rebuild", "compare", "switch", "openHost", "download")  # what GET me says a user can do
# What each role can do in shared mode (no one switches documents: a shared preview serves one; everyone may download
# the paper without its notes), and commenting only with a note macro the paper defines (check_me).
ROLE_CAN = {"viewer": ("download",), "commenter": ("comment", "download"), "editor": ("comment", "edit", "rebuild", "compare", "download"),
            "owner": ("comment", "edit", "rebuild", "compare", "openHost", "download")}
JS_TYPE_RE = re.compile(r"(?:text|application)/(?:x-)?javascript\b")
SOURCES = ("main.tex", "body.tex", "people.tex", "generated.inc")  # what the shared copy's build reads (settled)

PAPER = r"""\documentclass[twocolumn]{article}
\usepackage{xcolor}
\usepackage[switch]{lineno}
\linenumbers
\newif\ifshowtodos \showtodostrue
\newcommand{\todotxt}[1]{\ifshowtodos\textcolor{red}{#1}\fi}
\newcommand{\eb}[1]{\todotxt{[EB: #1]}}\input{people}
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
\end{document}
"""
# The coauthors' note macros (the users of shared_setup), in a file of their own so that main.tex
# keeps its line numbers.
PEOPLE = r"""\newcommand{\ed}[1]{\todotxt{[ED: #1]}}
\newcommand{\cm}[1]{\todotxt{[CM: #1]}}
"""
# The start of body.tex, for the notes in shared mode (note_checks), near the top of page 1: a paragraph
# with a note of the owner's, and one that the owner's protected-text marker ends (LATEX_LIVE_PROTECTED).
NOTE_PARAGRAPHS = r"""
Quokka narwhal axolotl pangolin tapir gecko. \eb{An owner note to resolve.}

Walrus yak zebu ibex okapi. \eb{as written}

"""
# A change to the shared copy that only the test makes, before the shared instance starts: no one's saves
# account for it, so the server commits it then, as the owner's (shared_start_checks). It also tells whether
# TeX can read an absolute path there (shared mode runs it with openin_any=p).
LOCAL_CHANGE = r"""\IfFileExists{/etc/passwd}{\typeout{latex-live-check: TeX read an absolute path}}
  {\typeout{latex-live-check: TeX refused an absolute path}}
"""
# A change that only the test makes once the shared instance runs (to refs.bib, which the build doesn't read):
# the server must not commit it while it runs (commit_checks), only as the owner's at its next start
# (shared_stop_checks).
LATER_CHANGE = "% A change made beside the server, which it did not write.\n"
IMPORT_RES = (re.compile(r"""^\s*(?:import|export)\s[^;'"]*?\bfrom\s*["']([^"']+)["']""", re.M),
              re.compile(r"""^\s*import\s*["']([^"']+)["']""", re.M),
              re.compile(r"""\bimport\(\s*["']([^"']+)["']\s*\)"""))


LOGS = []  # the instances' output, shown on failure
# For the instances: their own token and instance list (live_checks), a protected marker, and none of
# the user's own settings.
ENV = {k: v for k, v in os.environ.items() if not k.startswith("LATEX_LIVE_") and k != "VSCODE_IPC_HOOK_CLI"}
ENV["LATEX_LIVE_PROTECTED"] = r"\\eb\{human-written\}"


def redact(text):
    """Never print a token: as a URL parameter, a cookie, or itself."""
    text = re.sub(r"(\bt=|latex_live=)[A-Za-z0-9_-]{8,}", r"\1REDACTED", str(text))  # (\b: not TeX's "format=pdflatex")
    for secret in (TOKEN[0], *SECRETS):
        if secret:
            text = text.replace(secret, "REDACTED")
    return text


def prints_token(text):
    """Whether output shows a token: the instances' own, a personal link's, or one in a ?t= link."""
    return bool(re.search(r"\bt=[A-Za-z0-9_-]{8,}", text)) or any(secret and secret in text for secret in (TOKEN[0], *SECRETS))


def fail(msg):
    for path in LOGS:
        with open(path, errors="replace") as f:
            print(f"--- {os.path.basename(path)} (last lines)\n{redact(''.join(f.readlines()[-25:]))}", flush=True)
    sys.exit(f"FAIL: {redact(msg)}")


def ok(msg):
    print(f"ok: {msg}", flush=True)


def wait_for(what, test, timeout=180, step=1):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if result := test():
            return result
        time.sleep(step)
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
                                   env={**ENV, "LATEX_LIVE_STATE": state}, stdout=subprocess.PIPE, text=True)
                  for _ in range(12)]
        tokens = {r.communicate()[0].strip() for r in racers}
        if len(tokens) != 1 or "" in tokens:
            fail(f"instances starting at once got {len(tokens)} different tokens{' (one empty)' if '' in tokens else ''}")
    ok("instances starting at once agree on one token, never an empty one")
    layout_checks()
    matching_checks()
    bounds_checks()
    confinement_checks()


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


def confinement_checks():
    """Shared mode's guards around the downloads built on request (live.py), without TeX: with the TeX Live image
    missing, the export and the review copy fail with a message that says so (never the host's TeX); the review
    copy's notes come from the last good build's list of files (.fls.done, not the .fls a build is rewriting), and
    only from the paper's own files (not a link to a file elsewhere); the inputs kept for its probe leave out
    symbolic links. Outside shared mode, links count as before."""
    sys.path.insert(0, HERE)
    import live
    saved, tmp = (live.SHARED, live.image_pulled), tempfile.mkdtemp(prefix="latex-live-check-")
    try:
        live.SHARED, live.image_pulled = True, lambda: False
        root = os.path.join(tmp, "paper")
        build = os.path.join(root, live.BUILD)
        os.makedirs(build)
        server = types.SimpleNamespace(paper_root=root, default_pdf="main.pdf", tex_exec="false", export={}, review={},
                                       exported={}, export_lock=threading.Lock(), review_lock=threading.Lock(),
                                       download_lock=threading.Lock())
        try:
            live.exact_export(server, "main.pdf")
            fail("with the TeX Live image missing, the export in shared mode went ahead")
        except RuntimeError as e:
            if live.IMAGE_MISSING not in str(e):
                fail(f"with the TeX Live image missing, the export in shared mode says: {e}")
        server.review = {"pdf": "main.pdf", "source": "1", "state": "running"}
        live.build_review(server, "main.pdf", "1")
        if server.review.get("state") != "failed" or live.IMAGE_MISSING not in server.review.get("msg", ""):
            fail(f"with the TeX Live image missing, the review copy in shared mode: {server.review}")

        outside = os.path.join(tmp, "outside.tex")
        for path, text in ((os.path.join(root, "main.tex"), "\\newcommand{\\eb}[1]{\\todotxt{[EB: #1]}}\n\\eb{in main}\n"),
                           (os.path.join(root, "late.tex"), "\\eb{read late in the build}\n"),
                           (outside, "\\eb{in a file outside the paper}\n")):
            with open(path, "w") as f:
                f.write(text)
        os.symlink(outside, os.path.join(root, "away.tex"))
        fls = os.path.join(build, "main-live.fls")
        with open(fls, "w") as f:  # (as pdflatex rewrites it during a pass: only the files read so far)
            f.write(f"PWD {root}\nINPUT main.tex\n")
        with open(fls + ".done", "w") as f:  # (the last good build's)
            f.write(f"PWD {root}\nINPUT main.tex\nINPUT late.tex\nINPUT away.tex\nINPUT {outside}\n")
        found = sorted(n["body"] for n in live.review_notes(root, "main.pdf"))
        if found != ["in main", "read late in the build"]:
            fail(f"the review copy's notes in shared mode are not the last good build's, in the paper's own files: {found}")

        out = os.path.join(tmp, "export")
        os.makedirs(out)
        with open(os.path.join(out, "main-export.aux"), "w") as f:
            f.write("\\relax\n")
        os.symlink(outside, os.path.join(out, "main-export.bbl"))
        live.keep_inputs(out)
        if sorted(os.listdir(os.path.join(out, "inputs"))) != ["main-export.aux"]:
            fail(f"the inputs kept for the probe in shared mode: {sorted(os.listdir(os.path.join(out, 'inputs')))} (no links wanted)")
        live.SHARED = False
        live.keep_inputs(out)
        found = [n["body"] for n in live.review_notes(root, "main.pdf")]
        if (sorted(os.listdir(os.path.join(out, "inputs"))) != ["main-export.aux", "main-export.bbl"]
                or "in a file outside the paper" not in found):
            fail("outside shared mode, the review copy no longer follows links as it did")
    finally:
        live.SHARED, live.image_pulled = saved
        shutil.rmtree(tmp, ignore_errors=True)
    ok("in shared mode, the downloads built on request say plainly that the TeX Live image is missing, and the review copy"
       " reads the last good build's own files and keeps no links for its probe")


def viewer_scripts(page):
    """The viewer's inline module code, once its import map checks out: valid JSON, ahead of the module
    code, every target a file here given relative to the viewer (so that it also resolves under the hub's
    prefix), and every bare module name that the viewer or a vendored editor module imports mapped."""
    scripts, imports = [], None
    for m in re.finditer(r"<script\b([^>]*)>(.*?)</script>", page, re.S):
        attrs, code = m.group(1), m.group(2)
        kind = re.search(r"""\btype\s*=\s*["']?([^"'\s>]+)""", attrs)
        kind = kind.group(1) if kind else ""
        src = re.search(r"""\bsrc\s*=\s*["']?([^"'\s>]+)""", attrs)
        if src:
            if not os.path.isfile(os.path.join(HERE, urllib.parse.urlsplit(src.group(1)).path.lstrip("/"))):
                fail(f"the viewer loads {src.group(1)}, which is not in the checkout")
        elif kind == "importmap":
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
        found = os.path.isdir(real) if name.endswith("/") else os.path.isfile(real)
        if not path.startswith("./") or not real.startswith(HERE + os.sep) or not found:
            fail(f"the viewer's import map sends {name} to {target!r}, not to a file here given as ./...")
    code = {"viewer.html": "\n".join(scripts)}
    for path in sorted(glob.glob(os.path.join(HERE, "codemirror", "*.js"))):
        with open(path, encoding="utf-8") as f:
            code[os.path.relpath(path, HERE)] = f.read()
    for name, text in code.items():
        for spec in sorted(bare_imports(text)):
            if spec not in imports and not any(key.endswith("/") and spec.startswith(key) for key in imports):
                fail(f"{name} imports {spec}, which the viewer's import map does not map")
    ok(f"the viewer's import map maps every module that the viewer and its editor import ({len(imports)} names)")
    return scripts


def bare_imports(code):
    """The bare module names (not ./, ../ or / paths, nor URLs) that JavaScript code imports, statically
    or with import("...")."""
    names = {m.group(1) for pattern in IMPORT_RES for m in pattern.finditer(code)}
    return {n for n in names if not n.startswith((".", "/")) and not re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", n)}


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=check", "-c", "user.email=check@example.com", *args], cwd=cwd, check=True,
                   capture_output=True)


def make_paper(tmp):
    subprocess.run(["git", "init", "-q", "--bare", os.path.join(tmp, "remote.git")], check=True)
    paper = os.path.join(tmp, "paper")
    os.makedirs(paper)
    with open(os.path.join(paper, "main.tex"), "w") as f:
        f.write(PAPER)
    with open(os.path.join(paper, "people.tex"), "w") as f:
        f.write(PEOPLE)
    words = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu".split()
    with open(os.path.join(paper, "body.tex"), "w") as f:
        f.write(NOTE_PARAGRAPHS + "\n\n".join(" ".join(words[(i * 7 + j) % 12] for j in range(90)) for i in range(24)) + "\n")
    git(paper, "init", "-q", "-b", "main")
    git(paper, "add", "-A")
    git(paper, "commit", "-qm", "first")
    git(paper, "remote", "add", "origin", os.path.join(tmp, "remote.git"))
    git(paper, "push", "-q", "-u", "origin", "main")
    with open(os.path.join(paper, "body.tex"), "a") as f:
        f.write("\nomega omega omega.\n")
    git(paper, "commit", "-qam", "unpushed")
    return paper


def start(paper, doc="main", *extra, env=None, port=PORT):
    LOGS.append(os.path.join(paper, "..", f"live-{len(LOGS)}.log"))
    log = open(LOGS[-1], "w")
    return subprocess.Popen([sys.executable, os.path.join(HERE, "live.py"), f"{doc}.tex", "--no-open", "--port", str(port), *extra],
                            cwd=paper, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, env=env or ENV)


def request(path, token=None, method="GET", body=None, cookie=None, headers=None):
    """(status, headers, body) of a request to the hub, without following redirects."""
    conn = http.client.HTTPConnection("127.0.0.1", hub["port"], timeout=30)
    if token:
        path += ("&" if "?" in path else "?") + f"t={token}"
    sent = {"Cookie": f"latex_live={cookie}"} if cookie else {}
    if body is not None:
        sent["Content-Type"] = "application/json"
    sent.update(headers or {})
    conn.request(method, path, json.dumps(body) if body is not None else None, sent)
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


def hub_get(path, token):
    """get, failing the check (with the answer) instead of raising."""
    try:
        return get(path, token)
    except Exception as e:
        fail(f"{path} (through the hub): {type(e).__name__}: {e}")


def read_hub():
    """The hub's port and pid (HUB_FILE), if it answers."""
    try:
        with open(os.path.join(ENV["LATEX_LIVE_STATE"], "hub.json")) as f:
            hub.update(json.load(f))
        return request("/hub/ping", TOKEN[0])[0] == 200
    except (OSError, ValueError, KeyError):
        return False


hub, TOKEN = {}, [None]
SECRETS = []  # the shared-mode instance's tokens (shared_setup): never printed, never in an answer's body


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
    ENV["LATEX_LIVE_EDITOR"], editor_log = editor_stub(tmp, "editor")  # (not the user's editor: owner_checks)
    paper = make_paper(tmp)
    for name in ("appendix", "extra"):
        with open(os.path.join(paper, f"{name}.tex"), "w") as f:
            f.write(f"\\documentclass{{article}}\n\\begin{{document}}\nThe {name}.\n\\end{{document}}\n")
    procs, sharing = [start(paper)], []  # (sharing: the shared-mode instance, shared_setup)
    try:
        sh = shared_setup(tmp, sharing)  # (it builds while the hub's checks run; None without podman or the image)
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
        owner_checks(token, own, editor_log)

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
        before = get("/status/main.pdf", token)
        status, _, data = request(f"{hub['base']}main/rebuild", method="POST", body={}, cookie=token)
        if status != 202:
            fail(f"POST /rebuild through the hub: {status} {data[:200]!r}")

        def rebuilt():  # (and its notes-free pass: the download below is of one build, which nothing replaces meanwhile)
            info = get("/status/main.pdf", token)
            return info["version"] != before["version"] and not info["building"] and info["clean"]["version"] != before["clean"]["version"]

        wait_for("the rebuild that POST /rebuild started", lambda: _quiet(rebuilt))
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

        if sh:
            shared_start_checks(sh, own, token)
            identity_checks(sh)
            role_checks(sh)
            export_checks(sh)
            review_shared_checks(sh)
            note_checks(sh)
            editor_checks(sh)
            commit_checks(sh)
            shared_stop_checks(sh, sharing)
        switch_checks(token)

        procs.append(start(paper, "appendix"))  # (a second document of the same paper, in its own process)
        # (switch_checks built appendix.pdf already, so wait for the hub to find this process, not just for the PDF)
        wait_for("the appendix's own process, through the hub",
                 lambda: _quiet(lambda: get("/instances", token, "appendix")["self"] == procs[1].pid))
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
        # (another instance may have taken the hub while the new one still starts: an instance stopped before it has
        # started latexmk exits without removing itself from the list, so let it build first)
        wait_for("extra.pdf through the hub", lambda: _quiet(lambda: get("/status/extra.pdf", token, "extra")["version"]))
    finally:
        for proc in procs + sharing:
            stop(proc)
    left = os.listdir(os.path.join(state, "instances"))
    if left:
        fail(f"stopped instances are still listed: {left}")
    ok("stopped instances are no longer listed")


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
    column, numbered and apart, without the protected marker or the resolved note. (Two of its notes
    are there for shared mode's checks, at the start of body.tex: NOTE_PARAGRAPHS; outside shared mode,
    "as written" is a note like any other.)"""
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
            written_pages(os.path.join(build, "review", "main", "main-review.log")) != pages:
        fail(f"the review copy has {plan['pages']} pages ({plan['paper']} of the paper), the export {pages}")
    if plan["width"] <= plan["W"]:
        fail(f"the review copy's pages are no wider than the paper's ({plan['width']} <= {plan['W']})")
    if drawn_pages(review, plan["left"]) != pages:
        fail(f"the review copy should place each page of the paper unscaled, {plan['left']} points from its left edge:"
             f" {drawn_pages(review, plan['left'])} of {pages} are")
    texts = [n["text"] for n in plan["notes"]]
    want = ["hiding it moves", "An owner note to resolve", "as written", "a note in a paragraph", "a note in a footnote"]
    if len(texts) != len(want) or any(sum(w in t for t in texts) != 1 for w in want):
        fail(f"the review copy's notes are not the five expected (no protected marker, resolved note, or note in a branch"
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
    if reviewed.get("version") != get("/review", token).get("version"):
        fail("asking again rebuilt the review copy, though the document had not changed")
    ok(f"the review copy: {pages} pages as exported, unscaled on wider ones, with {len(texts)} notes in both gutters, apart"
       " (the protected marker, the resolved note, and the one in a branch that is off left out)")


def owner_checks(token, own, editor_log):
    """The owner's own preview (not shared): GET me, the editor's modules, SyncTeX as JSON, the editor
    stand-in through GET /open (kept outside shared mode), POST /open and plain /synctex, and the POST
    body limits of the hub and of the preview's own port (own; a source file's save may be bigger than
    other actions)."""
    me = hub_get("/me", token)
    can = me.get("can") if isinstance(me, dict) and isinstance(me.get("can"), dict) else {}
    if not isinstance(me, dict) or me.get("shared") is not False or me.get("role") != "owner" or not all(can.get(c) is True for c in CAPS):
        fail(f"GET me outside shared mode is not the owner with every capability: {me}")
    status, headers, data = request(f"{hub['base']}main/codemirror/view.js", token)
    kind = next((value for key, value in headers.items() if key.lower() == "content-type"), "")  # (static files send Content-type)
    with open(os.path.join(HERE, "codemirror", "view.js"), "rb") as f:
        if status != 200 or data != f.read() or not JS_TYPE_RE.match(kind):
            fail(f"/codemirror/view.js through the hub: {status} {kind}")
    if (status := request(f"{hub['base']}main/favicon.ico", token)[0]) not in (200, 204):  # (browsers ask on every page)
        fail(f"/favicon.ico through the hub: {status}, not answered")
    ok("GET me reports the owner with every capability; the editor's modules are served as JavaScript; /favicon.ico is answered")

    page, x, y = text_point(lambda path: hub_get(path, token), "main.tex", 9)
    if editor_calls(editor_log):
        fail(f"synctex with json=1 opened the editor: {editor_calls(editor_log)}")
    for path, method, body in ((f"{hub['base']}main/open?file=main.tex&line=9", "GET", None),
                               (f"{hub['base']}main/open", "POST", {"file": "main.tex", "line": 10}),
                               (f"{hub['base']}main/synctex?file=main.pdf&page={page}&x={x:.2f}&y={y:.2f}", "GET", None)):
        status, _, data = request(path, method=method, body=body, cookie=token)
        if status != 200:
            fail(f"{method} {path} (not shared): {status} {data[:200]!r}")
    calls = wait_for("the editor stand-in, three times", lambda: len(editor_calls(editor_log)) >= 3 and editor_calls(editor_log),
                     timeout=10, step=0.2)
    if (len(calls) != 3 or not all(c.startswith("-g ") and re.search(r"/main\.tex:\d+$", c) for c in calls)
            or not any(c.endswith("main.tex:9") for c in calls) or not any(c.endswith("main.tex:10") for c in calls)):
        fail(f"the editor stand-in got {calls}, not -g .../main.tex:9, :10 and the double-clicked line")
    ok("SyncTeX answers JSON without opening the editor; GET /open, POST /open and plain /synctex open it")

    status, _, data = request(f"{hub['base']}main/file", method="POST", cookie=token,
                              body={"path": "body.tex", "base": "0" * 20, "text": "x" * (1 << 20)})
    if status != 409 or not _quiet(lambda: json.loads(data)["stale"]):
        fail(f"a 1 MiB save through the hub: {status} {data[:200]!r} (want 409 stale: within the 8 MB limit for files)")
    for path, length in (("file", 9 << 20), ("note", 100_001)):
        if (status := post_length(hub["port"], f"{hub['base']}main/{path}", length, [("Cookie", f"latex_live={token}")])) != 413:
            fail(f"a {length}-byte POST /{path} through the hub: {status}, not 413")
    for length, wanted in ((100_001, (413,)), ("-1", (400, 411, 413)), ("many", (400, 411))):
        if (status := post_length(own, "/note", length, [("Cookie", f"latex_live={token}")])) not in wanted:
            fail(f"a POST /note announcing {length!r} bytes on the preview's own port: {status or 'no answer within 10 s'}")
    for path, headers in (("status/main.pdf?t=%C3%A9", {}), ("status/main.pdf", {"Cookie": "latex_live=\u00e9"})):
        try:
            status = request(f"{hub['base']}main/{path}", headers=headers)[0]
        except (OSError, http.client.HTTPException) as e:
            fail(f"a non-ASCII token makes the hub drop the connection ({type(e).__name__}: {e})")
        if status != 403:
            fail(f"a non-ASCII token through the hub: {status}, not 403")
    ok("the hub takes a 1 MiB save of a source file, refuses bigger bodies (as the preview does a bad Content-Length),"
       " and refuses a non-ASCII token")


def switch_checks(token):
    """Outside shared mode, through the hub: POST /switch (as the viewer sends it) builds another document and
    GET /switch (as older viewers send it) goes back; POST /diff and GET /diff each build a latexdiff against
    HEAD."""
    status, _, data = request(f"{hub['base']}main/switch", method="POST", body={"doc": "appendix"}, cookie=token)
    if status != 200 or _quiet(lambda: json.loads(data)["doc"]) != "appendix":
        fail(f"POST /switch {{doc: appendix}} through the hub: {status} {data[:200]!r}")
    wait_for("appendix.pdf, after POST /switch", lambda: _quiet(lambda: get("/status/appendix.pdf", token, "appendix")["version"]))
    status, _, data = request(f"{hub['base']}appendix/switch?doc=main", token)
    if status != 200 or _quiet(lambda: json.loads(data)["doc"]) != "main" or hub_get("/docs", token)["doc"] != "main":
        fail(f"GET /switch?doc=main through the hub: {status} {data[:200]!r}")
    ok("POST /switch builds another document, and GET /switch goes back (outside shared mode, through the hub)")
    if not texlive_image()[1] and not shutil.which("latexdiff"):
        return print("skip: no TeX Live image and no latexdiff here, so POST /diff and GET /diff are not checked", flush=True)

    def diffed():
        diff = hub_get("/status/main-diff.pdf", token).get("diff") or {}
        if diff.get("state") == "failed":
            fail(f"the latexdiff against HEAD failed: {diff.get('msg')}")
        return diff.get("state") == "done"

    for method, path, body in (("POST", "diff", {"rev": "HEAD"}), ("GET", "diff?rev=HEAD", None)):
        status, _, data = request(f"{hub['base']}main/{path}", method=method, body=body, cookie=token)
        if status != 200 or _quiet(lambda: json.loads(data)["state"]) != "running":
            fail(f"{method} /{path} through the hub: {status} {data[:200]!r}")
        wait_for(f"the latexdiff that {method} /{path} started", diffed, timeout=120, step=0.5)
    status, headers, _ = request(f"{hub['base']}main/pdf/main-diff.pdf", token)
    if status != 200 or headers.get("Content-Type") != "application/pdf":
        fail(f"/pdf/main-diff.pdf through the hub: {status} {headers.get('Content-Type')}")
    ok("POST /diff and GET /diff build a latexdiff against HEAD (outside shared mode, through the hub)")


def text_point(lookup, file, line):
    """(page, x, y) in main.pdf that SyncTeX maps back to file near line, from a forward search; lookup(path)
    answers a GET as JSON. This also checks that synctex with json=1 answers {file, line}."""
    box = lookup(f"/forward?file={urllib.parse.quote(file)}&line={line}")
    tried = []
    for share in (0.5, 0.3, 0.7, 0.15, 0.85):
        page, x, y = box["page"], box["x"] + box["w"] * share, box["y"] + box["h"] / 2
        found = lookup(f"/synctex?file=main.pdf&page={page}&x={x:.2f}&y={y:.2f}&json=1")
        if not isinstance(found, dict):
            fail(f"synctex with json=1 did not answer JSON: {found!r}")
        if found.get("file") == file and isinstance(found.get("line"), int) and abs(found["line"] - line) <= 3:
            return page, x, y
        tried.append(found)
    fail(f"synctex with json=1 maps no point of the forward search for {file}:{line} back to it: {tried}")


def editor_stub(tmp, name):
    """A stand-in for the editor's command-line tool (LATEX_LIVE_EDITOR) that only logs its arguments, one
    call per line: (its path, the log's)."""
    stub, log = os.path.join(tmp, f"{name}.sh"), os.path.join(tmp, f"{name}.log")
    with open(stub, "w") as f:
        f.write(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {shlex.quote(log)}\n")
    os.chmod(stub, 0o755)
    return stub, log


def host_tex_stub(tmp):
    """Stand-ins for the host's pdflatex and bibtex that log their arguments and fail, for the front of the
    shared instance's PATH: shared mode must never run them (all its TeX runs go through tex-exec, into the
    image). (Their folder, the log, which editor_calls reads.)"""
    folder, log = os.path.join(tmp, "host-tex"), os.path.join(tmp, "host-tex.log")
    os.makedirs(folder)
    for tool in ("pdflatex", "bibtex"):
        with open(os.path.join(folder, tool), "w") as f:
            f.write(f"#!/bin/sh\nprintf '{tool} %s\\n' \"$*\" >> {shlex.quote(log)}\nexit 1\n")
        os.chmod(os.path.join(folder, tool), 0o755)
    return folder, log


def editor_calls(log):
    try:
        with open(log) as f:
            return f.read().splitlines()
    except FileNotFoundError:
        return []


def post_length(port, path, length, headers=()):
    """The status of a POST that announces length bytes but sends none, so the server must refuse it from
    the header alone (None: no answer within 10 s)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.putrequest("POST", path, skip_host=any(k.lower() == "host" for k, _ in headers), skip_accept_encoding=True)
        for key, value in (*headers, ("Content-Type", "application/json"), ("Content-Length", str(length))):
            conn.putheader(key, value)
        conn.endheaders()
        return conn.getresponse().status
    except (OSError, http.client.HTTPException):
        return None
    finally:
        conn.close()


# Shared mode: one instance (shared_setup) serves a copy of the paper to the users of a users file, who
# come through a trusted proxy (an identity header) or with a personal link (a token of their own).
def shared_users():
    """The users file's users: the owner, an editor (ed) and a commenter (cm) whose note macros the paper
    defines, a viewer, and an editor with only a personal link (tk), whose macro the paper lacks."""
    return {"owner1": {"name": "Owner Example", "email": "owner@example.org", "macro": "eb", "role": "owner"},
            "ed": {"name": "Eden Doyle", "email": "eden@example.org", "macro": "ed", "role": "editor"},
            "cm": {"name": "Casey Moss", "email": "casey@example.org", "macro": "cm", "role": "commenter"},
            "vw": {"name": "Val Wren", "email": "val@example.org", "macro": "vw", "role": "viewer"},
            "tk": {"name": "Toni Kay", "email": "toni@example.org", "macro": "tk", "role": "editor",
                   "token": secrets.token_urlsafe(18)}}


def shared_setup(tmp, sharing):
    """Check that shared mode refuses the host's TeX, then start the shared-mode instance (it builds while
    the hub's checks run; sharing holds it, to be stopped). Returns what the shared checks need, or None
    where shared mode can't run: it builds only in the TeX Live image, with podman."""
    state = os.path.join(tmp, "shared-state")
    sh = {"tmp": tmp, "state": state, "token": make_state(state), "users": shared_users(), "users_file": os.path.join(tmp, "users.json")}
    SECRETS.extend([sh["token"]] + [user["token"] for user in sh["users"].values() if "token" in user])
    sh["root"] = make_shared_paper(tmp, state)
    stub, sh["editor_log"] = editor_stub(tmp, "shared-editor")
    host_tex, sh["host_tex_log"] = host_tex_stub(tmp)
    sh["env"] = {**ENV, "LATEX_LIVE_STATE": state, "LATEX_LIVE_USERS": sh["users_file"], "LATEX_LIVE_IDENTITY_HEADER": "x-test-user",
                 "LATEX_LIVE_PROXY_HOST": PROXY_HOST, "LATEX_LIVE_PUBLIC_URL": PUBLIC, "LATEX_LIVE_COMMIT": "1",
                 "LATEX_LIVE_COMMIT_IDLE": "2", "LATEX_LIVE_PROTECTED": r"\\eb\{as written\}", "LATEX_LIVE_EDITOR": stub,
                 "PATH": host_tex + os.pathsep + ENV.get("PATH", os.defpath)}
    if sys.platform.startswith("linux"):  # (the server looks the peer's uid up in /proc/net)
        sh["env"]["LATEX_LIVE_PROXY_UID"] = str(os.getuid())
    write_users(sh)
    tex_exec_check(tmp)
    host_tex_check(sh)
    image, pulled = texlive_image()
    if not pulled:
        print(f"skip: no podman or no {image} here, so shared mode, which builds only in that image, is not checked", flush=True)
        return None
    sh["refs"] = git_out(sh["root"], "for-each-ref", "--format=%(refname) %(objectname)")
    sh["base"] = git_out(sh["root"], "rev-parse", "shared")
    sh["proc"] = start(sh["root"], env=sh["env"], port=SHARED_PORT)
    sharing.append(sh["proc"])
    sh["log"] = LOGS[-1]
    return sh


def texlive_image():
    """(The TeX Live image that live.py builds in, its TEXLIVE_IMAGE; whether podman has it here.)"""
    with open(os.path.join(HERE, "live.py"), encoding="utf-8") as f:
        image = re.search(r'^TEXLIVE_IMAGE = "([^"]+)"', f.read(), re.M).group(1)
    return image, bool(shutil.which("podman")) and subprocess.run(["podman", "image", "exists", image], timeout=60).returncode == 0


def make_state(path):
    """A state folder of its own, with a fresh token (so none carries over from this checkout)."""
    os.makedirs(path, mode=0o700)
    token = secrets.token_urlsafe(16)
    fd = os.open(os.path.join(path, "token"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token)
    return token


def write_text(root, name, text):
    """Write a file of a scratch paper as given (line endings too)."""
    path = os.path.join(root, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def make_shared_paper(tmp, state):
    """The copy of the scratch paper that shared mode serves: a clone of its remote on branch "shared", with
    the files that the editor checks need (committed there), a branch that HEAD does not build on ("side"),
    git settings and hooks that the server's commits must override, files that the server must never list,
    serve, write or commit, latexmkrc files that latexmk must not read, and a change only the test makes."""
    root = os.path.join(tmp, "shared")
    subprocess.run(["git", "clone", "-q", "-b", "main", os.path.join(tmp, "remote.git"), root], check=True, capture_output=True)
    git(root, "checkout", "-q", "-b", "side")
    write_text(root, "side.tex", "A commit that the shared branch does not build on.\n")
    git(root, "add", "side.tex")
    git(root, "commit", "-qm", "side")
    git(root, "checkout", "-q", "-b", "shared", "main")
    for name, text in (("generated.inc", "Generated quetzal sentence.\n\n"), ("refs.bib", "@misc{one, title = {One}}\n"),
                       (os.path.join("sub", "refs.bib"), "@misc{two, title = {Two}}\n"),
                       ("crlf.bib", "@misc{three,\r\n  title = {Three},\r\n}\r\n")):
        write_text(root, name, text)
    with open(os.path.join(root, "body.tex"), "a") as f:
        f.write("\n\\input{generated.inc}\n")  # (typeset text from a file the editor does not offer)
    git(root, "add", "-A")
    git(root, "commit", "-qm", "shared fixtures")
    os.chmod(os.path.join(root, "crlf.bib"), 0o640)  # (a save keeps the mode; git does not track this one)
    for key, value in (("commit.gpgsign", "true"), ("gpg.program", "false"), ("user.name", "Not The Owner"),
                       ("user.email", "not-the-owner@example.invalid")):
        subprocess.run(["git", "-C", root, "config", key, value], check=True)
    hooks = os.path.join(root, ".git", "hooks")
    os.makedirs(hooks, exist_ok=True)
    for hook in ("pre-commit", "prepare-commit-msg", "commit-msg", "post-commit"):
        with open(os.path.join(hooks, hook), "w") as f:
            f.write(f"#!/bin/sh\n: > {shlex.quote(os.path.join(tmp, 'hook-ran'))}\nexit 1\n")
        os.chmod(os.path.join(hooks, hook), 0o755)
    write_text(root, "appendix.tex", "\\documentclass{article}\n\\begin{document}\nThe appendix.\n\\end{document}\n")
    write_text(root, "reviews.pdf", "%PDF-1.4\n% another PDF in the paper root\n")
    os.symlink(os.path.join(state, "token"), os.path.join(root, "leak.bib"))
    write_text(root, "big.bib", "%" + " " * (5 << 20) + "\n")  # (over the editor's 4 MB limit)
    with open(os.path.join(root, "latin.bib"), "wb") as f:
        f.write("@misc{four, title = {Caf\u00e9}}\n".encode("latin-1"))
    write_text(root, ".private.bib", "@misc{five}\n")
    write_text(root, os.path.join("build", "stray.bib"), "@misc{six}\n")
    ran = os.path.join(tmp, "latexmkrc-ran").replace("\\", "\\\\").replace("'", "\\'")
    for name in ("latexmkrc", ".latexmkrc"):
        write_text(root, name, f"open(my $f, '>', '{ran}'); close($f);\n")
    with open(os.path.join(root, "people.tex"), "a") as f:
        f.write(LOCAL_CHANGE)
    return root


def write_users(sh, text=None):
    """(Re)write the users file (mode 0600) with a later mtime, so that the server reads it again."""
    path = sh["users_file"]
    fd = os.open(path + ".new", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps({"users": sh["users"]}, indent=1) if text is None else text)
    os.replace(path + ".new", path)
    sh["users_mtime"] = stamp = max(sh.get("users_mtime", 0) + 2_000_000_000, time.time_ns())
    os.utime(path, ns=(stamp, stamp))


def tex_exec_check(tmp):
    """tex-exec with LATEX_LIVE_REQUIRE_IMAGE=1 (shared mode) refuses to run a tool on the host while the
    image is missing; without it, it falls back to the host as before. With LATEX_LIVE_SANDBOX=1 (shared
    mode), it confines the container, as a stand-in for podman shows: no capabilities, no new privileges,
    at most 512 processes and 10 minutes (a caller's timeout stops only the podman client), no shell
    escape, and TeX's files only below its folder; live.sandbox_args confines the containers it starts
    itself the same way."""
    cmd = [os.path.join(HERE, "tex-exec"), "localhost/latex-live-check-missing:none", tmp, "true"]
    fallback = subprocess.run(cmd, cwd=tmp, env=ENV, capture_output=True, text=True, timeout=60)
    required = subprocess.run(cmd, cwd=tmp, env={**ENV, "LATEX_LIVE_REQUIRE_IMAGE": "1"}, capture_output=True, text=True, timeout=60)
    if fallback.returncode:
        fail(f"tex-exec without the image no longer falls back to the host's tool: {fallback.stderr}")
    if not required.returncode or not required.stderr.strip():
        fail(f"tex-exec with LATEX_LIVE_REQUIRE_IMAGE=1 and no image ran the host's tool or said nothing (exit {required.returncode})")
    stub = os.path.join(tmp, "podman-stub")
    os.makedirs(stub)
    with open(os.path.join(stub, "podman"), "w") as f:  # (the image exists; a run prints its arguments, one per line)
        f.write('#!/bin/sh\n[ "$1" = image ] && exit 0\nprintf \'%s\\n\' "$@"\n')
    os.chmod(os.path.join(stub, "podman"), 0o755)
    confined = subprocess.run([os.path.join(HERE, "tex-exec"), "localhost/latex-live-check:none", tmp, "pdflatex", "x.tex"], cwd=tmp,
                              env={**ENV, "LATEX_LIVE_SANDBOX": "1", "PATH": stub + os.pathsep + ENV.get("PATH", os.defpath)},
                              capture_output=True, text=True, timeout=60)
    sys.path.insert(0, HERE)
    import live
    shared, live.SHARED = live.SHARED, True
    started = live.sandbox_args()
    live.SHARED = shared
    want = ("--cap-drop=all", "--security-opt=no-new-privileges", "--pids-limit=512", "--timeout=600", "openin_any=p",
            "openout_any=p", "shell_escape=f")
    args = confined.stdout.splitlines()
    if confined.returncode or not all(w in args for w in want) or args[-3:] != ["localhost/latex-live-check:none", "pdflatex", "x.tex"]:
        fail(f"tex-exec with LATEX_LIVE_SANDBOX=1 runs podman with {args} (exit {confined.returncode}), not with all of {want}")
    if not all(w in started for w in want):
        fail(f"live.sandbox_args in shared mode is {started}, not all of {want}")
    ok("tex-exec refuses the host's TeX when shared mode requires the image, and confines the container there (10 minutes at most)")


def host_tex_check(sh):
    """Shared mode refuses --host-tex: collaborators' LaTeX must not run in the host's TeX, which can read
    the owner's files."""
    proc = subprocess.Popen([sys.executable, os.path.join(HERE, "live.py"), "appendix.tex", "--host-tex", "--no-open", "--port",
                             str(SHARED_PORT)], cwd=sh["root"], env=sh["env"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, start_new_session=True)
    try:
        out = proc.communicate(timeout=30)[0]
    except subprocess.TimeoutExpired:
        stop(proc)
        fail("shared mode with --host-tex kept running (it must refuse to build with the host's TeX)")
    if not proc.returncode or not out.strip():
        fail(f"shared mode with --host-tex exited {proc.returncode} without refusing:\n{out}")
    if f"{proc.pid}.json" in recorded(sh["state"]):
        fail("the instance that refused --host-tex is still listed")
    ok("shared mode refuses --host-tex")


def send(method, path, headers=(), body=None, port=SHARED_PORT, addr="127.0.0.1", timeout=30, quiet=False):
    """(status, [(header, value)], body) of one request to an instance's own port, with exactly these headers
    (pairs, so that one can repeat; Host defaults to the address); a body goes as JSON unless it is bytes.
    quiet: None instead of failing when nothing answers. No answer may carry a shared-mode token."""
    conn = http.client.HTTPConnection(addr, port, timeout=timeout)
    names = {key.lower() for key, _ in headers}
    data = body if body is None or isinstance(body, bytes) else json.dumps(body).encode()
    try:
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        if "host" not in names:
            conn.putheader("Host", f"[{addr}]:{port}" if ":" in addr else f"{addr}:{port}")
        if data is not None:
            if "content-type" not in names:
                conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(len(data)))
        for key, value in headers:
            conn.putheader(key, value)
        conn.endheaders(data)
        response = conn.getresponse()
        result = response.status, response.getheaders(), response.read()
    except (OSError, http.client.HTTPException) as e:
        if quiet:
            return None
        fail(f"{method} {path}: no answer ({type(e).__name__}: {e})")
    finally:
        conn.close()
    if any(secret.encode() in result[2] for secret in SECRETS):
        fail(f"{method} {path} answered with a token in its body")
    return result


def header(result, name):
    return next((value for key, value in result[1] if key.lower() == name.lower()), None)


def via_proxy(user, path, method="GET", body=None, origin=PUBLIC, headers=()):
    """A request as the trusted proxy forwards it for user: its Host, the identity header (the configured
    name, in another case), the address it adds, and the browser's Origin on a POST. The proxy's requests
    must never get a cookie."""
    sent = [("Host", PROXY_HOST), ("X-Test-User", user), ("X-Forwarded-For", "203.0.113.7")]
    sent += [("Origin", origin)] if method == "POST" and origin else []
    result = send(method, path, sent + list(headers), body)
    if header(result, "Set-Cookie"):
        fail(f"{method} {path} for {user} through the proxy set a cookie")
    return result


def with_token(token, path, method="GET", body=None, host=None, origin=None):
    """A request with a token's cookie, on a loopback Host (127.0.0.1:port) unless host says otherwise."""
    sent = ([("Host", host)] if host else []) + [("Cookie", f"latex_live={token}")] + ([("Origin", origin)] if origin else [])
    return send(method, path, sent, body)


def expect(result, status, what):
    """result's body, as JSON if it is JSON, failing unless its status is status (or one of them)."""
    wanted = status if isinstance(status, tuple) else (status,)
    if result[0] not in wanted:
        fail(f"{what}: {result[0]}, not {' or '.join(map(str, wanted))} ({result[2][:300]!r})")
    if (header(result, "Content-Type") or "").startswith("application/json"):
        try:
            return json.loads(result[2])
        except ValueError:
            fail(f"{what}: not JSON ({result[2][:300]!r})")
    return result[2].decode(errors="replace")


def expect_dict(result, status, what):
    value = expect(result, status, what)
    if not isinstance(value, dict):
        fail(f"{what}: not a JSON object ({str(value)[:300]})")
    return value


def as_viewer(path):
    """A GET for the viewer vw through the proxy (JSON, or text)."""
    return expect(via_proxy("vw", path), 200, f"GET {path} for a viewer")


def check_me(me, what, role, defined=True, **fields):
    """GET me's answer: shared mode, the owner's name, the role and its capabilities (ROLE_CAN; commenting,
    but for the owner, only if the paper defines the user's note macro: defined, its macroDefined), and
    the given fields."""
    want = {"role": role, "shared": True, "owner": "Owner Example", "macroDefined": defined, **fields}
    got = {key: me.get(key) for key in want}
    can = {c: c in ROLE_CAN[role] and (c != "comment" or defined or role == "owner") for c in CAPS}
    got_can = {c: (me.get("can") if isinstance(me.get("can"), dict) else {}).get(c) for c in CAPS}
    if got != want or got_can != can:
        fail(f"GET me for {what}: {got}, can {got_can}; not {want}, can {can}")


def mtime_ns(path):
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return 0


def settled(root):
    """Whether the shared copy's build has caught up with every edit of its sources: its last make finished
    (status.json after the last pass began), and that pass began after the last edit. (Note actions read
    the file list that a pass rewrites as it goes.)"""
    began = mtime_ns(os.path.join(root, "build", "main-live.building"))
    done = mtime_ns(os.path.join(root, "build", "main-live.status.json"))
    return done > 0 and done >= began >= max(mtime_ns(os.path.join(root, name)) for name in SOURCES)


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def read_text(path):
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def put(path, text):
    """Replace a file at once, as another editor would (a new inode, so no cached version survives it)."""
    put_bytes(path, text.encode("utf-8"))


def put_bytes(path, data):
    """put, for bytes."""
    with open(path + ".check-new", "wb") as f:
        f.write(data)
    os.replace(path + ".check-new", path)


def version_of(data):
    """A file version as the editor API gives it: the first 20 hex digits of the sha256 of its bytes."""
    return hashlib.sha256(data).hexdigest()[:20]


def line_of(text, needle):
    return text.count("\n", 0, text.index(needle)) + 1


def with_paragraph(text, i, new):
    """body.tex's text with its i-th one-line paragraph of greek words replaced (they follow NOTE_PARAGRAPHS)."""
    lines = text.split("\n")
    lines[line_of(text, "Walrus yak") - 1 + 2 * i] = new
    return "\n".join(lines)


def git_out(cwd, *args):
    """git's output, without its optional locks (so that it never holds up the server's commits)."""
    r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    if r.returncode:
        fail(f"git {' '.join(args)}: {r.stderr}")
    return r.stdout.rstrip("\n")


def recorded(state):
    """The instances that a state folder lists."""
    try:
        return os.listdir(os.path.join(state, "instances"))
    except FileNotFoundError:
        return []


def read_json_file(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def outside_address():
    """This machine's address on its network (not loopback), or None."""
    for family, probe in ((socket.AF_INET, "192.0.2.1"), (socket.AF_INET6, "2001:db8::1")):
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as s:
                s.connect((probe, 9))  # (sends nothing: it only picks the address that traffic would leave from)
                address = s.getsockname()[0]
        except OSError:
            continue
        if not ipaddress.ip_address(address.split("%")[0]).is_loopback:
            return address
    return None


def shared_start_checks(sh, own, hub_token):
    """The shared-mode instance's build and start: hardened, alone on exactly its port (none other when it
    is taken), loopback peers only, its public URL printed and no token anywhere (nor when another instance
    finds it running), no part in the hub, and the edits it found uncommitted committed as the owner's."""
    root, cookie = sh["root"], [("Cookie", f"latex_live={sh['token']}")]
    status_file = os.path.join(root, "build", "main-live.status.json")

    def published():
        if sh["proc"].poll() is not None:  # (e.g. its port is taken, perhaps as another program's ephemeral port)
            fail(f"the shared-mode instance exited (status {sh['proc'].returncode}) before its first build")
        info = read_json_file(status_file)
        if info and info.get("ok") is False:
            fail(f"the shared copy does not build in the hardened image: {info.get('errors')}")
        return os.path.isfile(os.path.join(root, "main.pdf"))

    wait_for("the shared copy's main.pdf", published)
    clean_status = os.path.join(root, "build", "clean", "main-clean.status.json")
    wait_for("the shared copy's notes-free pass", lambda: os.path.isfile(clean_status))
    if not (read_json_file(clean_status) or {}).get("ok"):
        fail(f"the shared copy's notes-free pass failed in the hardened image: {read_json_file(clean_status)}")

    def answering():
        r = send("GET", "/me", cookie, quiet=True)
        if r and r[0] != 200:
            fail(f"GET me with the shared instance's owner cookie: {r[0]} {r[2][:200]!r}")
        return r

    wait_for("the shared-mode instance", answering, timeout=60)
    sh["version0"] = expect_dict(with_token(sh["token"], "/status/main.pdf"), 200, "the shared copy's status")["version"]
    with open(sh["log"], errors="replace") as f:
        printed = f.read()
    if PUBLIC not in printed:
        fail(f"the shared-mode instance does not print its public URL ({PUBLIC})")
    if prints_token(printed):
        fail("the shared-mode instance prints a token (its own, a personal link's, or a ?t= link), which its log keeps")
    if re.search(r"/p/[^/\s]+/[^/\s]+/", printed) or os.path.exists(os.path.join(sh["state"], "hub.lock")):
        fail("the shared-mode instance takes part in the hub (a hub URL, or hub.lock in its state)")
    entry = read_json_file(os.path.join(sh["state"], "instances", f"{sh['proc'].pid}.json")) or {}
    lock = read_json_file(os.path.join(root, "build", "main-live.lock")) or {}
    if entry.get("port") != SHARED_PORT or lock.get("port") != SHARED_PORT:
        fail(f"the shared-mode instance is not recorded on port {SHARED_PORT}: instance {entry}, lock {lock}")
    if os.path.exists(os.path.join(sh["tmp"], "latexmkrc-ran")):
        fail("latexmk read the paper's own latexmkrc (shared mode runs it with -norc)")
    with open(os.path.join(root, "build", "main-live.log"), errors="replace") as f:
        texlog = f.read()
    if "latex-live-check: TeX refused an absolute path" not in texlog:
        fail("the shared copy's build " + ("read /etc/passwd: TeX runs without openin_any=p" if "TeX read an absolute path" in texlog
                                           else "logged nothing from the probe in people.tex"))
    ok(f"shared mode builds in the hardened image (no paper latexmkrc, no absolute paths), on port {SHARED_PORT}, out of the hub,"
       " printing its public URL and no token")

    # The test's change to people.tex (LOCAL_CHANGE), uncommitted when the instance started: no one's writes account
    # for it, so it is committed then, as the owner's, rather than in the next coauthor's commit of that file.
    shas = wait_for("the commit of the edits found uncommitted at the start",
                    lambda: git_out(root, "rev-list", f"{sh['base']}..shared").split(), timeout=20, step=0.2)
    found = [(git_out(root, "show", "-s", "--format=%an <%ae>|%cn <%ce>|%s", sha).split("|"), commit_user(root, sha),
              git_out(root, "diff-tree", "--no-commit-id", "--name-only", "-r", sha).split()) for sha in shas]
    owner = "Owner Example <owner@example.org>"
    if (len(found) != 1 or found[0][0][:2] != [owner, owner] or "people.tex" not in found[0][0][2]
            or found[0][1:] != ("unattributed", ["people.tex"])):
        fail(f"the edit found uncommitted at the start is not one commit of people.tex, as the owner's (Latex-Live-Shared:"
             f" unattributed): {found}")
    sh["base"] = shas[0]  # (the users' commits come after it: commit_checks)
    with open(os.path.join(root, "refs.bib"), "a") as f:
        f.write(LATER_CHANGE)
    ok("the edit found uncommitted at the start is committed then, as the owner's, alone")

    second = subprocess.Popen([sys.executable, os.path.join(HERE, "live.py"), "appendix.tex", "--no-build", "--no-open", "--port",
                               str(SHARED_PORT)], cwd=root, env=sh["env"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                              start_new_session=True)
    try:
        out = second.communicate(timeout=30)[0]
    except subprocess.TimeoutExpired:
        stop(second)
        fail(f"a second shared-mode instance kept running though port {SHARED_PORT} is taken (shared mode uses no other port)")
    if not second.returncode or str(SHARED_PORT) not in out:
        fail(f"a second shared-mode instance on the taken port {SHARED_PORT} exited {second.returncode} without saying so:\n{out}")
    if f"{second.pid}.json" in recorded(sh["state"]):
        fail("the instance that found its port taken is still listed")
    ok("a shared-mode instance whose port is taken says so and exits")
    portless = subprocess.run([sys.executable, os.path.join(HERE, "live.py"), "appendix.tex", "--no-build", "--no-open"],
                              cwd=root, env=sh["env"], capture_output=True, text=True, timeout=60)
    if not portless.returncode or "--port" not in portless.stdout + portless.stderr:
        fail(f"a shared-mode instance without --port exited {portless.returncode} without asking for it:\n"
             f"{portless.stdout + portless.stderr}")
    ok("a shared-mode instance needs --port (it never takes the hub's)")
    again = subprocess.run([sys.executable, os.path.join(HERE, "live.py"), "main.tex", "--no-build", "--no-open", "--port",
                            str(SHARED_PORT)], cwd=root, env=sh["env"], capture_output=True, text=True, timeout=60)
    out = again.stdout + again.stderr
    if again.returncode or "already running" not in out or PUBLIC not in out or prints_token(out):
        fail(f"a second shared-mode instance for main.tex exited {again.returncode} without saying it is already running, with"
             f" the public URL and no token:\n{out}")
    ok("a second shared-mode instance for the same document says it is already running, with the public URL and no token")

    expect(send("GET", "/me", cookie, addr="::1"), 200, "the owner's cookie over IPv6 loopback ([::1])")
    address = outside_address()
    if not address:
        return print("skip: no network address here, so the loopback-only check is not made", flush=True)
    control = send("GET", "/docs", [("Host", f"localhost:{own}"), ("Cookie", f"latex_live={hub_token}")], port=own, addr=address,
                   timeout=5, quiet=True)
    answer = send("GET", "/me", [("Host", f"localhost:{SHARED_PORT}")] + cookie, addr=address, timeout=5, quiet=True)
    if answer is not None:
        fail(f"the shared-mode instance answered a peer at {address}, which is not loopback ({answer[0]})")
    if control is None:
        return print(f"skip: {address} does not reach this machine's own ports, so the loopback-only check proves little", flush=True)
    ok(f"the shared-mode instance answers over loopback (127.0.0.1, [::1]) but not from {address}")


def identity_checks(sh):
    """Who is asking: the Host allowlist, the identity header and its edge cases, tokens and cookies, GET me
    for each kind of user, and users added (or a broken users file) without a restart."""
    owner, tk = sh["token"], sh["users"]["tk"]["token"]
    cookie, proxy = [("Cookie", f"latex_live={owner}")], [("Host", PROXY_HOST)]
    for host in ("evil.example", f"evil.example:{SHARED_PORT}", "paper.invalid.evil.example:8443"):
        expect(send("GET", "/me", [("Host", host)] + cookie), 403, f"the owner's cookie with Host {host} (DNS rebinding)")
    for host in (f"localhost:{SHARED_PORT}", "localhost", f"127.0.0.1:{SHARED_PORT}", f"[::1]:{SHARED_PORT}"):
        expect(send("GET", "/me", [("Host", host)] + cookie), 200, f"the owner's cookie with Host {host}")
    expect(send("GET", "/me", [("Host", "paper.invalid:8443"), ("Cookie", f"latex_live={tk}")]), 200,
           "a personal link's cookie with the public Host")
    ok("the shared instance answers loopback, public and proxy Host names only")

    expect(send("GET", "/me", proxy), 403, "the proxy's Host without the identity header or a token")
    for values in (("ed", "ed"), ("ed", "cm"), ("",)):
        expect(send("GET", "/me", proxy + [("X-Test-User", v) for v in values]), 403, f"identity headers {values}")
    for name in ("ED", "ed,cm", "ed cm", "../ed", "\u00e9"):
        expect(send("GET", "/me", proxy + [("x-test-user", name)]), 403, f"the identity {name!r}, which no user has")
    unknown = send("GET", "/viewer.html?file=main.pdf", proxy + [("X-Test-User", "stranger"), ("Accept", "text/html")])
    page = unknown[2].decode(errors="replace")
    if unknown[0] != 403 or "stranger" not in page or "Owner Example" not in page:
        fail(f"a signed-in user who is not in the users file: {unknown[0]}, not 403 naming them and the owner ({page[:300]!r})")
    for host in ("paper.invalid:8443", f"{PROXY_HOST}:80", f"localhost:{SHARED_PORT}"):
        expect(send("GET", "/me", [("Host", host), ("X-Test-User", "ed")] + cookie), 403,
               f"the identity header with Host {host}, not the proxy's (even with the owner's cookie)")
    me = expect_dict(via_proxy("ed", f"/me?t={owner}"), 200, "ed through the proxy, with the owner's ?t=")
    other = expect_dict(via_proxy("vw", "/me", headers=cookie), 200, "vw through the proxy, with the owner's cookie")
    spaced = expect_dict(send("GET", "/me", proxy + [("X-Test-User", " ed ")]), 200, "the identity ' ed '")
    if (me.get("id"), other.get("id"), spaced.get("id")) != ("ed", "vw", "ed"):
        fail(f"through the proxy, a token or spaces changed who is asking: {me.get('id')}, {other.get('id')}, {spaced.get('id')}")
    ok("the identity header counts only alone, non-empty, a known user, with the proxy's Host; it beats tokens (no cookie set)")

    users = sh["users"]
    for uid in ("owner1", "ed", "cm", "vw"):  # (the paper defines note macros for all but the viewer)
        user = users[uid]
        check_me(expect_dict(via_proxy(uid, "/me"), 200, f"GET me for {uid}"), f"{uid} through the proxy", user["role"],
                 defined=uid != "vw", id=uid, name=user["name"], macro=user["macro"])
    for token, what, role, fields in ((tk, "the personal link", "editor",
                                       {"id": "tk", "name": "Toni Kay", "macro": "tk", "defined": False}),
                                      (owner, "the owner's token", "owner", {})):
        first = send("GET", f"/me?t={token}")
        check_me(expect_dict(first, 200, f"{what} as ?t="), f"{what} as ?t=", role, **fields)
        set_cookie = header(first, "Set-Cookie") or ""
        if (not set_cookie.startswith(f"latex_live={token}") or "httponly" not in set_cookie.lower()
                or "samesite=strict" not in set_cookie.lower() or re.search(r";\s*secure\b", set_cookie, re.I)):
            fail(f"{what} as ?t= sets the cookie {redact(set_cookie)!r}: want latex_live, HttpOnly, SameSite=Strict, not Secure (http)")
        check_me(expect_dict(with_token(token, "/me"), 200, f"{what} as a cookie"), f"{what} as a cookie", role, **fields)
    for path, headers, what in (("/me?t=%C3%A9", [], "?t= with a non-ASCII character"),
                                ("/me", [("Cookie", "latex_live=\u00e9")], "a cookie with a non-ASCII character"),
                                ("/me", [("Cookie", "latex_live=" + "x" * 22)], "a wrong token")):
        r = send("GET", path, headers, quiet=True)
        if r is None:
            fail(f"{what} drops the connection (hmac.compare_digest raised?)")
        if r[0] != 403 or (header(r, "Content-Type") or "").startswith("text/html"):
            fail(f"{what}: {r[0]} {header(r, 'Content-Type')}, not 403 (and no page: the client did not ask for HTML)")
    ok("GET me answers for each role, through the proxy or a personal link (its cookie: HttpOnly, SameSite=Strict),"
       " and says whether the paper defines the user's note macro (commenting needs it); no one switches documents")

    for path, what in ((f"/viewer.html?file=main.pdf&t={secrets.token_urlsafe(18)}", "a personal link that is not (or no longer) valid"),
                       ("/viewer.html?file=main.pdf", "no link at all")):
        r = send("GET", path, [("Accept", "text/html,application/xhtml+xml")])
        page = r[2].decode(errors="replace")
        if (r[0] != 403 or not (header(r, "Content-Type") or "").startswith("text/html") or "Owner Example" not in page
                or sh["state"] in page or header(r, "Set-Cookie")):
            fail(f"a browser with {what}: {r[0]} {header(r, 'Content-Type')}, not a 403 page that names the owner to ask"
                 f" (and says nothing about the state folder): {page[:300]!r}")
    ok("a browser without a working link gets a page that says whom to ask")

    users["nw"] = {"name": "Nia West", "email": "nia@example.org", "macro": "nw", "role": "viewer"}
    write_users(sh)
    wait_for("a user added to the users file to get in (no restart)", lambda: via_proxy("nw", "/me")[0] == 200, timeout=10, step=0.2)
    write_users(sh, "{ not JSON")
    for uid in ("ed", "nw"):
        expect(via_proxy(uid, "/me"), 200, f"{uid} once the users file is broken (the last good one stays)")
    with open(sh["log"], errors="replace") as f:
        if "still using the users as they were" not in f.read():
            fail("the shared-mode instance did not read the broken users file (or said nothing about it)")
    write_users(sh)
    ok("the users file is read again when it changes; a broken one leaves the last good one in place")


READS = ("/viewer.html?file=main.pdf", "/pdf/main.pdf", "/pdf/main-clean.pdf", "/status/main.pdf", "/status/main-diff.pdf",
         "/outline/main.pdf", "/notes/main.pdf", "/authors/main.pdf", "/graphics/main.pdf", "/margin/main.pdf", "/floats/main.pdf",
         "/check/main.pdf", "/forward?file=main.tex&line=9", "/docs", "/me", "/pdfjs/build/pdf.min.mjs")


def role_checks(sh):
    """What each role may do (reads, the role table, owner-only actions), the pinning of non-owners to the
    shared document, the old GET actions gone, CSRF (the Origin rules, no CORS), POST body limits, and
    SyncTeX as JSON that never opens the editor."""
    root, owner = sh["root"], sh["token"]
    for path in READS:
        as_viewer(path)
    authors = as_viewer("/authors/main.pdf")
    if not isinstance(authors, list) or not {"eb", "ed", "cm"} <= {a.get("macro") for a in authors if isinstance(a, dict)}:
        fail(f"/authors does not list every note macro for a viewer: {authors}")
    home = via_proxy("vw", "/")
    location = header(home, "Location") or ""
    if home[0] != 302 or not location.endswith("viewer.html?file=main.pdf") or "/p/" in location:
        fail(f"GET / in shared mode: {home[0]} to {header(home, 'Location')}, not to viewer.html?file=main.pdf without a hub prefix")
    script = via_proxy("ed", "/codemirror/view.js")
    if (script[0] != 200 or script[2] != read_bytes(os.path.join(HERE, "codemirror", "view.js"))
            or not JS_TYPE_RE.match(header(script, "Content-Type") or "")):
        fail(f"/codemirror/view.js for the editor: {script[0]} {header(script, 'Content-Type')}")
    for path in ("/codemirror/", "/codemirror/../live.py", "/codemirror/%2e%2e/live.py", "/codemirror/nope.js"):
        expect(via_proxy("ed", path), 404, f"GET {path}")
    expect(via_proxy("vw", "/favicon.ico"), (200, 204), "GET /favicon.ico for a viewer (browsers ask on every page)")
    ok("a viewer reads everything about the shared document; the editor's modules are served, nothing else; /favicon.ico is"
       " answered")

    for path in ("/status/appendix.pdf", "/notes/appendix.pdf", "/outline/appendix.pdf", "/authors/appendix.pdf", "/graphics/appendix.pdf",
                 "/margin/appendix.pdf", "/floats/appendix.pdf", "/check/appendix.pdf", "/pdf/reviews.pdf", "/pdf/appendix.pdf",
                 "/pdf/appendix-export.pdf", "/synctex?file=appendix.pdf&page=1&x=100&y=100&json=1"):
        expect(via_proxy("vw", path), (400, 403, 404), f"GET {path} for a viewer (only the shared document's names)")
    expect(with_token(owner, "/pdf/reviews.pdf"), 200, "the owner reading another PDF of the paper")
    expect(via_proxy("ed", "/note", "POST", {"action": "edit", "pdf": "appendix.pdf", "source": "appendix.tex", "line": 3, "macro": "ed",
                                             "tex": "x", "text": "y"}), (400, 403, 404), "a note for another document")
    ok("non-owners are pinned to the shared document")

    probe = {"path": "refs.bib", "base": "0" * 20, "text": "@misc{one}\n"}  # (a save on a version never seen: 409, writes nothing)
    owner_only = (("GET", "/instances", None), ("GET", "/papers", None), ("POST", "/switch", {"doc": "main"}),
                  ("POST", "/open", {"file": "main.tex", "line": 9}), ("POST", "/commit", {}))
    editor_up = (("GET", "/files", None), ("GET", "/file?path=body.tex", None), ("POST", "/file", probe), ("POST", "/rebuild", {}),
                 ("GET", "/commits", None), ("POST", "/diff", {"rev": "HEAD"}))
    for uid in ("vw", "cm", "ed"):
        for method, path, body in owner_only + (() if uid == "ed" else editor_up):
            expect(via_proxy(uid, path, method, body), 403, f"{method} {path} for {sh['users'][uid]['role']} {uid}")
    for result, who in ((with_token(owner, "/switch", "POST", {"doc": "appendix"}), "with the owner's token"),
                        (via_proxy("owner1", "/switch", "POST", {"doc": "appendix"}), "for the owner, through the proxy")):
        expect(result, 403, f"POST switch {who} (a shared preview serves one document)")
    for method, path, body, status in (("GET", "/files", None, 200), ("GET", "/file?path=body.tex", None, 200),
                                       ("GET", "/commits", None, 200), ("POST", "/file", probe, 409)):
        expect(via_proxy("ed", path, method, body), status, f"{method} {path} for the editor")
    listing = expect_dict(with_token(owner, "/instances"), 200, "GET instances for the owner")
    if listing.get("self") != sh["proc"].pid:
        fail(f"GET instances for the owner: {listing}")
    for path in ("/papers", "/files", "/commits"):
        expect(with_token(owner, path), 200, f"GET {path} for the owner")
    for path in ("/switch?doc=main", "/diff?rev=HEAD", "/open?file=main.tex&line=9"):
        if (r := with_token(owner, path))[0] == 200:
            fail(f"GET {path} still acts in shared mode (only POST may: it carries an Origin) ({r[2][:200]!r})")
    preflight = send("OPTIONS", "/note", [("Host", PROXY_HOST), ("X-Test-User", "ed"), ("Origin", "http://evil.example"),
                                          ("Access-Control-Request-Method", "POST"), ("Access-Control-Request-Headers", "content-type")])
    if any(key.lower().startswith("access-control-") for key, _ in preflight[1]):
        fail(f"OPTIONS is answered with CORS headers: {preflight[1]}")
    if editor_calls(sh["editor_log"]):
        fail(f"the editor stand-in ran: {editor_calls(sh['editor_log'])}")
    ok("roles: viewers read, commenters also comment, editors also edit, rebuild and compare, only the owner does the rest,"
       " but for switching documents, which no one does; the old GET actions are gone; no CORS")

    proxy, owners = [("Host", PROXY_HOST), ("X-Test-User", "ed")], [("Cookie", f"latex_live={owner}")]
    public = [("Host", "paper.invalid:8443"), ("Cookie", f"latex_live={sh['users']['tk']['token']}")]
    for headers, allowed, what in (
            (proxy + [("Origin", PUBLIC)], True, "the public origin, through the proxy"),
            (proxy, False, "no Origin, through the proxy"),
            (proxy + [("Origin", "null")], False, "Origin null, through the proxy"),
            (proxy + [("Origin", PUBLIC + "/")], False, "the public origin and a slash"),
            (proxy + [("Origin", "https://paper.invalid:8443")], False, "the public origin's host on https"),
            (proxy + [("Origin", "http://paper.invalid")], False, "the public origin's host on another port"),
            (proxy + [("Origin", "http://other.paper.invalid:8443")], False, "a sibling of the public host"),
            (proxy + [("Origin", f"http://localhost:{SHARED_PORT}")], False, "a loopback origin, through the proxy"),
            (owners, True, "a token and no Origin on a loopback Host (curl, check.py)"),
            (owners + [("Origin", "http://localhost:5000")], True, "a loopback origin on another port"),
            (owners + [("Origin", f"http://127.0.0.1:{SHARED_PORT}")], True, "the 127.0.0.1 origin"),
            (owners + [("Origin", f"http://[::1]:{SHARED_PORT}")], True, "the [::1] origin"),
            (owners + [("Origin", PUBLIC)], False, "the public origin on a loopback Host"),
            (owners + [("Origin", "null")], False, "Origin null with a token"),
            (owners + [("Origin", f"http://localhost.evil.example:{SHARED_PORT}")], False, "a look-alike of localhost"),
            (public + [("Origin", PUBLIC)], True, "a personal link with the public origin on the public Host"),
            (public, False, "a personal link without Origin on the public Host"),
            (public + [("Origin", f"http://localhost:{SHARED_PORT}")], False, "a loopback origin on the public Host")):
        result = send("POST", "/file", headers, probe)
        if result[0] != (409 if allowed else 403):
            fail(f"a POST with {what}: {result[0]} {result[2][:200]!r} (want {'409, past the Origin check' if allowed else '403'})")
    expect(send("POST", "/note", proxy + [("Origin", PUBLIC), ("Content-Type", "text/plain")], b"{}"), 415, "a POST that is not JSON")
    ok("POSTs need the public origin through the proxy and on the public Host, a loopback one (or none, with a token) on loopback")

    ed = proxy + [("Origin", PUBLIC)]
    for path, length, wanted, what in (("/note", 100_001, (413,), "a note over 100 000 bytes"),
                                       ("/file", 9 << 20, (413,), "a file over 8 MB"),
                                       ("/note", "-1", (400, 411, 413), "a negative Content-Length"),
                                       ("/note", "many", (400, 411), "a Content-Length that is not a number")):
        if (status := post_length(SHARED_PORT, path, length, ed)) not in wanted:
            fail(f"{what}: {status or 'no answer within 10 s'}, not {' or '.join(map(str, wanted))}")
    expect(send("POST", "/file", ed, {**probe, "text": "x" * (1 << 20)}), 409, "a 1 MiB save (within the 8 MB limit for files)")
    ok("POST bodies: 100 000 bytes for actions, 8 MB for a file's save; a bad Content-Length is refused before reading")

    text = read_text(os.path.join(root, "body.tex"))
    page, x, y = text_point(as_viewer, "body.tex", line_of(text, "Quokka narwhal"))
    query = f"/synctex?file=main.pdf&page={page}&x={x:.2f}&y={y:.2f}"
    for result, who in ((via_proxy("vw", query), "a viewer"), (via_proxy("ed", query), "the editor"),
                        (with_token(owner, query), "the owner")):
        if expect_dict(result, 200, f"GET synctex without json=1 for {who} (JSON in shared mode)").get("file") != "body.tex":
            fail(f"GET synctex without json=1 for {who}: {result[2][:200]!r}")
    box = as_viewer("/forward?file=generated.inc&line=1")
    for share in (0.5, 0.3, 0.7):
        found = expect_dict(via_proxy("vw", f"/synctex?file=main.pdf&page={box['page']}&x={box['x'] + box['w'] * share:.2f}"
                                            f"&y={box['y'] + box['h'] / 2:.2f}&json=1"), 200, "synctex for generated text")
        if "file" in found and found["file"] is None:
            if not found.get("reason"):
                fail(f"synctex for text from generated.inc (not editable) gives no reason: {found}")
            break
    else:
        fail(f"synctex for text from generated.inc (not editable) does not answer file: null: {found}")
    if editor_calls(sh["editor_log"]):
        fail(f"SyncTeX opened the editor on the owner's machine in shared mode: {editor_calls(sh['editor_log'])}")

    # A line past the end of its file, as SyncTeX gives now and then (e.g. in a table that macros build), goes to
    # the file's last line, with a note saying so: for a moment, the shared copy's SyncTeX data says so of every
    # line of body.tex (the build has settled, so nothing rewrites it meanwhile).
    wait_for("the shared copy's build to settle", lambda: settled(root), timeout=120)
    synctex = os.path.join(root, "build", "main-live.synctex.gz")
    data, st = read_bytes(synctex), os.stat(synctex)
    records = gzip.decompress(data).decode("latin-1")
    tags = re.findall(r"^Input:(\d+):.*/body\.tex$", records, re.M)
    moved = re.sub(r"^([\[(vhxkg$](?:%s)),\d+" % "|".join(tags), r"\1,99999", records, flags=re.M) if tags else records
    if moved == records:
        fail(f"no SyncTeX records of body.tex in {synctex}")
    try:
        put_bytes(synctex, gzip.compress(moved.encode("latin-1")))
        clamped = expect_dict(via_proxy("vw", query + "&json=1"), 200, "synctex for a line past the end of body.tex")
    finally:
        put_bytes(synctex, data)
        os.utime(synctex, ns=(st.st_atime_ns, st.st_mtime_ns))  # (as it was: what was computed from it still holds)
    last = text.count("\n") + (not text.endswith("\n"))
    if clamped.get("file") != "body.tex" or clamped.get("line") != last or not clamped.get("note"):
        fail(f"synctex for line 99999 of body.tex, which has {last}: {clamped}, not its last line, with a note")
    ok("SyncTeX answers JSON for everyone in shared mode (file: null where the editor can't go, the last line for one past"
       " the end), and opens nothing")


def export_checks(sh):
    """The download without notes in shared mode: a viewer may ask for it (a copy of what they read), several
    asking at once share one build, it is built once per build of the document, in the hardened image (TeX
    can't read absolute paths there), and coauthors get that PDF."""
    root = sh["root"]
    wait_for("the shared copy's build to settle", lambda: settled(root), timeout=120)
    asked, users = {}, ("vw", "cm", "ed", "vw")
    askers = [threading.Thread(target=lambda i=i, uid=uid: asked.update({i: send("GET", "/export", [("Host", PROXY_HOST),
                                                                                                  ("X-Test-User", uid)], quiet=True)}))
              for i, uid in enumerate(users)]
    for thread in askers:
        thread.start()
    for thread in askers:
        thread.join()
    if sorted(asked) != list(range(len(users))) or None in asked.values():
        fail(f"of {len(users)} requests for the download without notes at once, some got no answer")
    answers = [expect_dict(asked[i], 200, f"GET export for {uid}, with others at once") for i, uid in enumerate(users)]
    if len({(a.get("source"), a.get("time")) for a in answers}) != 1 or any(a.get("state") not in ("running", "done") for a in answers):
        fail(f"requests for the download without notes at once did not share one build: {answers}")

    def exported():
        e = expect_dict(via_proxy("vw", "/export"), 200, "GET export for a viewer")
        if e.get("state") == "failed":
            fail(f"the shared copy's download without notes failed: {e.get('msg')}")
        return e if e.get("state") == "done" else None

    done = wait_for("the shared copy's download without notes", exported, timeout=240)
    pdf = via_proxy("vw", "/pdf/main-export.pdf")
    if pdf[0] != 200 or header(pdf, "Content-Type") != "application/pdf" or not pdf[2].startswith(b"%PDF"):
        fail(f"/pdf/main-export.pdf for a viewer: {pdf[0]} {header(pdf, 'Content-Type')}")
    with open(os.path.join(root, "build", "export", "main", "main-export.log"), errors="replace") as f:
        texlog = f.read()
    if "latex-live-check: TeX refused an absolute path" not in texlog:
        fail("the download without notes " + ("read /etc/passwd: its TeX runs without openin_any=p, not in the hardened image"
                                               if "TeX read an absolute path" in texlog else "logged nothing from the probe"))
    if expect_dict(via_proxy("cm", "/export"), 200, "GET export for a commenter").get("version") != done["version"]:
        fail("asking again rebuilt the shared copy's download without notes, though the document had not changed")
    ok("a viewer may download the shared paper without its notes: one build for several asking at once, once per build of"
       " the document, in the hardened image")


def review_shared_checks(sh):
    """The copy with notes in the margin in shared mode (GET /review), as for the export: a request from another site's
    page is refused before it starts anything (the export's too), a viewer may ask, several asking at once share one
    build, made once per build of the document, and coauthors get that PDF; another document's copy is refused, and
    so is POST /review (the owner's, as whatever the role table doesn't list). It is confined: its probe pass can't
    read an absolute path (openin_any=p), and the host's TeX never runs."""
    root = sh["root"]
    wait_for("the shared copy's build to settle", lambda: settled(root), timeout=120)
    proxy, owner = [("Host", PROXY_HOST), ("X-Test-User", "vw")], [("Cookie", f"latex_live={sh['token']}")]
    for headers, what in ((proxy + [("Sec-Fetch-Site", "cross-site")], "Sec-Fetch-Site: cross-site"),
                          (proxy + [("Sec-Fetch-Site", "same-site")], "Sec-Fetch-Site: same-site"),
                          (proxy + [("Origin", "http://evil.example")], "another site's Origin"),
                          (proxy + [("Origin", "null")], "Origin: null"),
                          (proxy + [("Origin", PUBLIC), ("Origin", PUBLIC)], "two Origin headers"),
                          (proxy + [("Origin", f"http://localhost:{SHARED_PORT}")], "a loopback Origin through the proxy"),
                          (owner + [("Origin", "http://evil.example")], "another site's Origin, with the owner's token")):
        for path in ("/review", "/export"):
            expect(send("GET", path, headers), 403, f"GET {path} with {what}")
    time.sleep(1)  # (a review copy that had started would have its folder by now)
    if os.path.exists(os.path.join(root, "build", "review")):
        fail("a request for the copy with notes in the margin from another site's page started a build")
    for headers, what in ((proxy + [("Sec-Fetch-Site", "same-origin")], "Sec-Fetch-Site: same-origin"),
                          (proxy + [("Origin", PUBLIC)], "the public Origin through the proxy"),
                          (owner + [("Origin", f"http://localhost:{SHARED_PORT}")], "a loopback Origin with the owner's token"),
                          (proxy + [("Sec-Fetch-Site", "none")], "Sec-Fetch-Site: none (typed in)")):
        expect_dict(send("GET", "/export", headers), 200, f"GET /export with {what}")
    ok("GET /review and /export from another site's page are refused (Sec-Fetch-Site, or an Origin not the viewer's) and"
       " start nothing; the viewer's own requests, and those with neither header, go ahead")

    asked, askers = {}, (("vw", []), ("cm", [("Sec-Fetch-Site", "same-origin")]), ("ed", [("Origin", PUBLIC)]), ("vw", []))
    threads = [threading.Thread(target=lambda i=i, uid=uid, extra=extra: asked.update({i: send(
        "GET", "/review", [("Host", PROXY_HOST), ("X-Test-User", uid), *extra], quiet=True)})) for i, (uid, extra) in enumerate(askers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if sorted(asked) != list(range(len(askers))) or None in asked.values():
        fail(f"of {len(askers)} requests for the copy with notes in the margin at once, some got no answer")
    answers = [expect_dict(asked[i], 200, f"GET review for {uid}, with others at once") for i, (uid, _) in enumerate(askers)]
    if len({(a.get("source"), a.get("time")) for a in answers}) != 1 or any(a.get("state") not in ("running", "done") for a in answers):
        fail(f"requests for the copy with notes in the margin at once did not share one build: {answers}")

    def reviewed():
        r = expect_dict(via_proxy("vw", "/review"), 200, "GET review for a viewer")
        if r.get("state") == "failed":
            fail(f"the shared copy's copy with notes in the margin failed: {r.get('msg')}")
        return r if r.get("state") == "done" else None

    done = wait_for("the shared copy's copy with notes in the margin", reviewed, timeout=240)
    # (one build for all of them: for a few seconds, nothing builds the copy again, which would empty its folder first
    # and change its state's step, then its version)
    plan, end = os.path.join(root, "build", "review", "main", "main-review.json"), time.monotonic() + 4
    while time.monotonic() < end:
        again = expect_dict(via_proxy("vw", "/review"), 200, "GET review for a viewer, once it is done")
        if (again.get("version"), again.get("step")) != (done.get("version"), done.get("step")) or not os.path.isfile(plan):
            fail(f"the copy with notes in the margin was built again for the same build of the document: {again}")
        time.sleep(0.2)
    pdf = via_proxy("vw", "/pdf/main-review.pdf")
    if pdf[0] != 200 or header(pdf, "Content-Type") != "application/pdf" or not pdf[2].startswith(b"%PDF"):
        fail(f"/pdf/main-review.pdf for a viewer: {pdf[0]} {header(pdf, 'Content-Type')}")
    if pdf[2] != read_bytes(os.path.join(root, "build", "main-review.pdf")) or not done.get("notes"):
        fail(f"the viewer's copy with notes in the margin is not the one built, or has no notes: {done}")
    expect(via_proxy("vw", "/pdf/appendix-review.pdf"), 403, "GET /pdf/appendix-review.pdf for a viewer (another document's)")
    for uid in ("vw", "cm", "ed"):
        expect(via_proxy(uid, "/review", "POST", {}), 403, f"POST review for {sh['users'][uid]['role']} {uid}")
    if editor_calls(sh["host_tex_log"]):
        fail(f"shared mode ran the host's TeX: {editor_calls(sh['host_tex_log'])}")
    try:
        with open(os.path.join(root, "build", "review", "main", "main-export.log"), errors="replace") as f:
            probed = f.read()
    except FileNotFoundError:  # (the probe pass did not run)
        probed = ""
    if "latex-live-check: TeX refused an absolute path" not in probed or "latex-live|pages|" not in probed:
        fail("the review copy's probe pass " + ("read /etc/passwd: its TeX runs without openin_any=p, not in the hardened image"
                                                if "TeX read an absolute path" in probed
                                                else "did not get through people.tex and the notes"))
    if expect_dict(via_proxy("cm", "/review"), 200, "GET review for a commenter").get("version") != done["version"]:
        fail("asking again rebuilt the shared copy's copy with notes in the margin, though the document had not changed")
    ok(f"a viewer may download the shared paper with its {done['notes']} notes in the margin: one build for several asking at"
       " once, once per build of the document, confined (the probe can't read absolute paths; never the host's TeX); another"
       " document's copy and POST /review are refused")


def note(sh, uid, req):
    """POST note as a user (through the proxy, or tk with its personal link), for the shared document."""
    body = {"pdf": "main.pdf", **req}
    if uid == "tk":
        return with_token(sh["users"]["tk"]["token"], "/note", "POST", body)
    return via_proxy(uid, "/note", "POST", body)


def note_ok(sh, uid, req, what):
    """A note action that must succeed, at once (a build in progress makes no difference: notes_while_rebuilding)."""
    return expect_dict(note(sh, uid, req), 200, what)


def commit_user(root, sha):
    """The user whose edits a commit holds, from its Latex-Live-Shared trailer (None without exactly one)."""
    trailer = re.findall(r"^Latex-Live-Shared: *(\S+) *$", git_out(root, "show", "-s", "--format=%B", sha), re.M)
    return trailer[0] if len(trailer) == 1 else None


def wait_commit(sh, uid, paths, what):
    """Wait for the background auto-commit of uid's edits to paths (once uid has been quiet for
    LATEX_LIVE_COMMIT_IDLE), with no one else's commit in between. Each user writes in a phase of their own,
    so that one user's commit can't hold another's edits of the same file."""
    root = sh["root"]

    def landed():
        new = git_out(root, "rev-list", f"{sh['tip']}..shared").split()
        if any(commit_user(root, sha) != uid for sha in new):
            fail(f"a commit not of {uid}'s appeared while only {uid} wrote: {[(sha[:9], commit_user(root, sha)) for sha in new]}")
        unsaved = subprocess.run(["git", "-C", root, "diff", "--quiet", "HEAD", "--", *paths],
                                 env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"}).returncode
        return new if new and not unsaved else None

    sh["tip"] = wait_for(f"the auto-commit of {what}", landed, timeout=20, step=0.5)[0]


def note_checks(sh):
    """Notes in shared mode: each user writes as themselves (their own macro, never another's), edits and
    deletes only their own notes, writes LaTeX only as an editor, and may resolve anyone's note; protected
    text keeps its rules; a user whose note macro the paper lacks is told so. Each user's edits are
    committed in the background once they have been quiet for a while."""
    root = sh["root"]
    path = os.path.join(root, "body.tex")
    sh["tip"] = sh["base"]  # (wait_commit: the newest commit seen)
    wait_for("the shared copy's build to catch up", lambda: settled(root), timeout=120)
    text = read_text(path)
    first, second = line_of(text, "Quokka narwhal"), line_of(text, "Walrus yak")
    at_first = text_point(as_viewer, "body.tex", first)

    def add(point, selection, words, macro):
        return {"action": "add", "page": point[0], "x": point[1], "y": point[2], "selection": selection, "text": words, "macro": macro}

    owners = {"source": "body.tex", "line": first, "macro": "eb", "tex": "An owner note to resolve."}
    marker = {"source": "body.tex", "line": second, "macro": "eb", "tex": "as written"}
    for uid, req, what in (("vw", add(at_first, "axolotl pangolin", "A viewer's note", "vw"), "a viewer adding a note"),
                           ("ed", {"action": "edit", **owners, "text": "Rewritten"}, "the editor editing the owner's note"),
                           ("ed", {"action": "delete", **owners}, "the editor deleting the owner's note"),
                           ("cm", {"action": "edit", **owners, "latex": "\\textbf{Rewritten}"},
                            "a commenter writing LaTeX into the owner's note"),
                           ("cm", {"action": "resolve", **marker, "who": "cm", "text": "Done"},
                            "a commenter resolving the protected-text marker")):
        expect(note(sh, uid, req), 403, what)
    refused = str(expect(note(sh, "tk", add(at_first, "axolotl pangolin", "A note", "tk")), 403,
                         "a user whose note macro the paper lacks"))
    if "tk" not in refused or "defined" not in refused:
        fail(f"a user whose note macro the paper lacks is not told so: {refused[:300]}")
    if read_text(path) != text:
        fail("a refused note action changed body.tex")
    ok("notes: a viewer, the owner's note, LaTeX from a commenter, the protected marker and a missing macro are refused")

    one = note_ok(sh, "ed", add(at_first, "axolotl pangolin", "ED note one", "ed"), "the editor adding a note")
    if (one.get("macro"), one.get("source")) != ("ed", "body.tex") or "\\ed{ED note one}" not in read_text(path):
        fail(f"the editor's new note is not \\ed{{ED note one}} in body.tex: {one}")
    wait_commit(sh, "ed", ("body.tex",), "the editor's note")
    two = note_ok(sh, "cm", add(at_first, "tapir gecko", "CM note one", "cm"), "a commenter adding a note")
    if two.get("macro") != "cm" or "\\cm{CM note one}" not in read_text(path):
        fail(f"the commenter's new note is not \\cm{{CM note one}} in body.tex: {two}")
    kept = note_ok(sh, "cm", add(text_point(as_viewer, "body.tex", second), "ibex okapi", "CM on protected", "cm"),
                   "a commenter's note on protected text")
    text = read_text(path)
    if kept.get("protected") is not True or not 0 <= text.find("\\eb{as written}") < text.find("\\cm{CM on protected}"):
        fail(f"a note on protected text did not go after the marker: {kept}")
    wait_commit(sh, "cm", ("body.tex",), "the commenter's notes")
    ok("an editor and a commenter add notes with their own macros (one on protected text goes after the marker),"
       " each committed once they are quiet")

    wait_for("the shared copy's build to catch up", lambda: settled(root), timeout=120)
    text = read_text(path)
    eds = {"source": "body.tex", "line": one["line"], "macro": "ed", "tex": "ED note one"}
    cms = {"source": "body.tex", "line": two["line"], "macro": "cm", "tex": "CM note one"}
    for uid, req, what in (("cm", {"action": "edit", **eds, "text": "Hijacked"}, "a commenter editing the editor's note"),
                           ("ed", {"action": "edit", **cms, "text": "Hijacked"}, "the editor editing a commenter's note"),
                           ("ed", {"action": "delete", **cms}, "the editor deleting a commenter's note"),
                           ("cm", {"action": "edit", **cms, "latex": "CM \\emph{note}"}, "a commenter writing LaTeX into their own note"),
                           ("vw", {"action": "resolve", **eds, "who": "vw", "text": "No"}, "a viewer resolving a note")):
        expect(note(sh, uid, req), 403, what)
    if read_text(path) != text:
        fail("a refused note action changed body.tex")
    posing = note(sh, "ed", add(at_first, "Quokka narwhal", "Not as eb", "eb"))
    text = read_text(path)
    if ("\\eb{Not as eb}" in text or posing[0] not in (200, 400, 403)
            or (posing[0] == 200 and (expect_dict(posing, 200, "").get("macro") != "ed" or "\\ed{Not as eb}" not in text))):
        fail(f"the editor asking to write as \\eb: {posing[0]} {posing[2][:200]!r} (want it refused, or written as \\ed)")
    note_ok(sh, "ed", {"action": "edit", **eds, "latex": "ED note \\emph{one}"}, "the editor writing LaTeX into their own note")
    wait_commit(sh, "ed", ("body.tex",), "the editor's note edit")
    note_ok(sh, "cm", {"action": "edit", **cms, "text": "CM note edited"}, "a commenter editing their own note")
    reply = note_ok(sh, "cm", {"action": "reply", **eds, "tex": "ED note \\emph{one}", "who": "ed", "text": "CM reply"},
                    "a commenter replying (naming someone else as who)")
    note_ok(sh, "cm", {"action": "resolve", **owners, "who": "ed", "text": "Done by CM"}, "a commenter resolving the owner's note")
    wait_commit(sh, "cm", ("body.tex",), "the commenter's edit, reply and resolution")
    text = read_text(path)
    for want in ("\\ed{ED note \\emph{one}}", "\\cm{CM note edited}", "\\cm{CM reply}"):
        if want not in text:
            fail(f"body.tex lacks {want} after the edits")
    if reply.get("macro") != "cm" or "\\ed{CM reply}" in text:
        fail(f"a commenter's reply was written as someone else: {reply}")
    if (not re.search(r"^% RESOLVED \S+ \(cm\): Done by CM$", text, re.M)
            or re.search(r"^[^%\n]*\\eb\{An owner note to resolve\.\}", text, re.M)):
        fail(f"the owner's note is not resolved under the commenter's initials:\n{text[:700]}")
    ok("notes: no one edits or deletes others' notes or writes as someone else; editors may write LaTeX; anyone who comments resolves")
    notes_while_rebuilding(sh)


def notes_while_rebuilding(sh):
    """Notes keep working while the shared copy rebuilds, and so do the lists of notes and of note macros: the
    server reads the paper's files from its last successful build, not from the list of the files read (the
    .fls) that pdflatex rewrites as each pass goes. Each note edit (the editor's, of their own note) is sent as
    soon as a pass has begun, while that list lacks body.tex, and makes the build go another pass."""
    root = sh["root"]
    fls = os.path.join(root, "build", "main-live.fls")
    state = {"edits": 0, "asked": 0.0}  # (note edits so far; when a rebuild was last asked for)

    def mid_pass():
        try:
            with open(fls, errors="replace") as f:
                return "body.tex" not in f.read()
        except OSError:
            return True

    def until(test, what):
        """Wait for test(), asking for a rebuild whenever the build has settled for a while (a pass missed)."""
        while not test():
            if time.monotonic() > end:
                fail(f"timed out waiting for {what}, after {state['edits']} note edits while the shared copy rebuilds")
            if time.monotonic() - state["asked"] > 5 and settled(root):
                expect(via_proxy("ed", "/rebuild", "POST", {}), 202, "the editor rebuilding")
                state["asked"] = time.monotonic()
            time.sleep(0.002)

    wait_for("the shared copy's build to catch up", lambda: settled(root), timeout=120)
    listed = as_viewer("/notes/main.pdf")
    mine = [n for n in listed if isinstance(n, dict) and n.get("macro") == "ed" and n.get("file") == "body.tex"]
    if not mine:
        fail(f"/notes lists no note of the editor's in body.tex: {listed}")
    req = {"action": "edit", "source": "body.tex", "line": mine[0]["line"], "macro": "ed", "tex": mine[0]["tex"]}
    during, end = 0, time.monotonic() + 60
    while state["edits"] < 6:
        until(mid_pass, "a pass of the build to begin")
        state["edits"] += 1
        text = f"ED note, edited during pass {state['edits']}"
        note_ok(sh, "ed", {**req, "text": text}, f"the editor editing their own note as a pass of the build begins ({state['edits']})")
        req["tex"] = text
        during += mid_pass()  # (pdflatex still had not read body.tex: the note was written mid-pass)
        now = as_viewer("/notes/main.pdf")
        authors = {a.get("macro") for a in as_viewer("/authors/main.pdf") if isinstance(a, dict)}
        if len(now) != len(listed) or not {"eb", "ed", "cm"} <= authors:
            fail(f"while the shared copy rebuilds, /notes lists {len(now)} notes, not {len(listed)}, and /authors {sorted(authors)}")
        until(lambda: not mid_pass(), "the pass to read body.tex")
    if not during:
        fail("no note edit was answered before pdflatex read body.tex, so this check met no pass in progress")
    wait_commit(sh, "ed", ("body.tex",), "the editor's note edits during the rebuilds")
    ok(f"notes, /notes and /authors keep working while the paper rebuilds ({during} of {state['edits']} note edits were"
       " answered mid-pass, before pdflatex read body.tex again)")


def save(uid, req, status, what):
    return expect_dict(via_proxy(uid, "/file", "POST", req), status, what)


def editor_checks(sh):
    """The browser editor's files API (for the editor ed), status ?open=, auto-commit waiting out a git lock
    (and another user's write of the same file waiting for that commit), writes waiting out a merge in the
    shared copy, comparing (diff) only with commits that HEAD builds on, rebuilding, and the owner's POST open
    (the editor stand-in's only call)."""
    root, owner = sh["root"], sh["token"]
    path, crlf_path, token_file = os.path.join(root, "body.tex"), os.path.join(root, "crlf.bib"), os.path.join(sh["state"], "token")
    listing = expect_dict(via_proxy("ed", "/files"), 200, "GET files for the editor")
    files = [f for f in listing.get("files") or [] if isinstance(f, dict)]
    paths = [f.get("path") for f in files]
    if listing.get("main") != "main.tex" or paths[:1] != ["main.tex"] or paths[1:] != sorted(paths[1:]):
        fail(f"GET files does not list main.tex first, then the rest by path: {listing}")
    missing = [p for p in ("body.tex", "people.tex", "refs.bib", "sub/refs.bib", "crlf.bib") if p not in paths]
    offered = [p for p in paths if p.startswith(("build/", ".git/")) or any(part.startswith(".") for part in p.split("/"))
               or p in ("leak.bib", "big.bib", "appendix.tex", "generated.inc", "side.tex", "reviews.pdf", "latexmkrc")]
    sizes = {f.get("path"): f.get("size") for f in files}
    if missing or offered or sizes.get("body.tex") != os.path.getsize(path):
        fail(f"GET files: missing {missing}, offering {offered}, body.tex size {sizes.get('body.tex')}: {paths}")
    ok("GET files lists the paper's sources and .bib files (main.tex first), not build/, .git, dot files, links out, big files"
       " or other documents")

    disk = read_bytes(path)
    got = expect_dict(via_proxy("ed", "/file?path=body.tex"), 200, "GET file body.tex")
    want = {"path": "body.tex", "text": disk.decode(), "version": version_of(disk), "eol": "\n"}
    if {key: got.get(key) for key in want} != want:
        fail(f"GET file body.tex: {str(got)[:300]}, not its text, version {version_of(disk)} and eol \\n")
    if expect_dict(via_proxy("ed", "/file?path=sub/refs.bib"), 200, "GET file sub/refs.bib").get("text") != "@misc{two, title = {Two}}\n":
        fail("GET file sub/refs.bib does not give its text")
    new = with_paragraph(got["text"], 3, "Paragraph three, saved by the editor.")
    saved = save("ed", {"path": "body.tex", "base": got["version"], "text": new}, 200, "a save on the current version")
    disk = read_bytes(path)
    if disk.decode() != new or saved.get("version") != version_of(disk) or saved.get("merged") or saved.get("text", new) != new:
        fail(f"a save on the current version: {str(saved)[:300]}")
    ok("GET file gives the text and its version; a save on the current version writes it")

    base = expect_dict(via_proxy("ed", "/file?path=body.tex"), 200, "GET file body.tex")
    put(path, with_paragraph(base["text"], 24, "Paragraph twenty-four, changed on disk meanwhile."))
    older = with_paragraph(base["text"], 1, "Paragraph one, saved from an older copy.")
    merged = save("ed", {"path": "body.tex", "base": base["version"], "text": older}, 200, "a save on an older version")
    disk = read_bytes(path)
    if (merged.get("merged") is not True or merged.get("text") != disk.decode() or merged.get("version") != version_of(disk)
            or "Paragraph twenty-four, changed on disk meanwhile." not in disk.decode()
            or "Paragraph one, saved from an older copy." not in disk.decode()):
        fail(f"a save on an older version did not merge with the change on disk: {str(merged)[:300]}")
    base = expect_dict(via_proxy("ed", "/file?path=body.tex"), 200, "GET file body.tex")
    theirs = with_paragraph(base["text"], 1, "Paragraph one, changed on disk (theirs).")
    mine = with_paragraph(base["text"], 1, "Paragraph one, changed in the browser (mine).")
    put(path, theirs)
    clash = save("ed", {"path": "body.tex", "base": base["version"], "text": mine}, 409, "a save that conflicts with the file on disk")
    disk = read_bytes(path)
    if disk.decode() != theirs or b"<<<<<<<" in disk or b">>>>>>>" in disk:
        fail("a conflicting save changed the file on disk")
    marked = str(clash.get("merged"))
    if (clash.get("conflict") is not True or clash.get("version") != version_of(disk) or clash.get("text") != theirs
            or "<<<<<<< " not in marked or ">>>>>>> " not in marked or "|||||||" in marked):
        fail(f"a conflicting save's answer: {str(clash)[:300]} (want the file's version and text, and git's merge with two-sided"
             " conflict markers)")
    save("ed", {"path": "body.tex", "base": clash["version"], "text": mine, "force": True}, 200,
         "the same save with force (use my version)")
    if read_text(path) != mine:
        fail("a save with force did not write the text")
    copy = with_paragraph(read_text(path), 20, "Paragraph twenty, changed on disk before the copy.")
    put(path, copy)  # (a version the server never served or wrote)
    put(path, with_paragraph(copy, 22, "Paragraph twenty-two, changed on disk after the copy."))
    edit = with_paragraph(copy, 3, "Paragraph three, saved again from that copy.")
    stale = save("ed", {"path": "body.tex", "base": version_of(copy.encode()), "text": edit}, 409,
                 "a save on a version the server never saw")
    disk = read_bytes(path)
    if stale.get("stale") is not True or stale.get("version") != version_of(disk) or stale.get("text") != disk.decode():
        fail(f"a save on an unknown version: {str(stale)[:300]}")
    again = save("ed", {"path": "body.tex", "base": version_of(copy.encode()), "base_text": copy, "text": edit}, 200,
                 "the same save with its base text")
    text = read_text(path)
    if again.get("merged") is not True or any(s not in text for s in ("Paragraph twenty, changed", "Paragraph twenty-two, changed",
                                                                      "Paragraph three, saved again")):
        fail(f"a save with its base text did not merge: {str(again)[:300]}")
    ok("a stale save merges; a conflict writes nothing and answers both texts; force writes; an unknown base asks for its text")

    base = expect_dict(via_proxy("ed", "/file?path=body.tex"), 200, "GET file body.tex")
    once = with_paragraph(base["text"], 7, "Paragraph seven, saved once.")
    save("ed", {"path": "body.tex", "base": base["version"], "text": once}, 200, "a save (whose answer the editor never gets)")
    more = with_paragraph(base["text"], 7, "Paragraph seven, saved once, then typed on.")
    save("ed", {"path": "body.tex", "base": base["version"], "text": more}, 200,
         "the next save, typed on in the same line, from the version before the save whose answer got lost")
    if read_text(path) != more:
        fail("the save after one whose answer got lost did not write its text")
    theirs = with_paragraph(base["text"], 7, "Paragraph seven, as another editor changed it in an older copy.")
    clash = expect_dict(with_token(sh["users"]["tk"]["token"], "/file", "POST",
                                   {"path": "body.tex", "base": base["version"], "text": theirs}),
                        409, "another editor's save of that line from the same older version")
    if clash.get("conflict") is not True or clash.get("by") != sh["users"]["ed"]["name"] or read_text(path) != more:
        fail(f"another editor's save of that line from the same older version: {str(clash)[:300]} (want a conflict that says"
             f" by: {sh['users']['ed']['name']}, who saved meanwhile, and nothing written)")
    ok("a save whose answer got lost doesn't conflict with its user's next save of the same line; another user's does,"
       " and says who saved meanwhile")

    crlf = expect_dict(via_proxy("ed", "/file?path=crlf.bib"), 200, "GET file crlf.bib")
    raw = read_bytes(crlf_path)
    if crlf.get("eol") != "\r\n" or crlf.get("text") != raw.decode().replace("\r\n", "\n") or crlf.get("version") != version_of(raw):
        fail(f"GET file of a CRLF file: {crlf}")
    edited = crlf["text"].replace("Three", "Four")
    done = save("ed", {"path": "crlf.bib", "base": crlf["version"], "text": edited}, 200, "a save of a CRLF file")
    raw = read_bytes(crlf_path)
    if raw != edited.replace("\n", "\r\n").encode() or done.get("version") != version_of(raw):
        fail(f"a save of a CRLF file did not keep its line endings: {raw!r}")
    if os.stat(crlf_path).st_mode & 0o777 != 0o640:
        fail(f"a save did not keep the file's mode (0640, now {os.stat(crlf_path).st_mode & 0o777:o})")
    expect(via_proxy("ed", "/file?path=latin.bib"), 415, "GET file of a file that is not UTF-8")
    for name in ("../shared-state/token", token_file, ".git/config", "build/main-live.log", "build/stray.bib", "leak.bib", "big.bib",
                 ".private.bib", "appendix.tex", "generated.inc", "nope.tex"):
        expect(via_proxy("ed", "/file?path=" + urllib.parse.quote(name, safe="")), 404, f"GET file {name!r} (not editable)")
    for query in ("%2e%2e%2fshared-state%2ftoken", "%2e%2e/shared-state/token", "%252e%252e%252fshared-state%252ftoken"):
        expect(via_proxy("ed", "/file?path=" + query), 404, f"GET file?path={query}")
    expect(via_proxy("ed", "/file?path="), (400, 404), "GET file without a path")
    before = {p: read_bytes(p) for p in (token_file, os.path.join(root, ".git", "config"), os.path.join(root, "build", "stray.bib"))}
    for name in ("../shared-state/token", token_file, "leak.bib", ".git/config", "build/stray.bib", "big.bib", "new.tex", "sub/new.bib"):
        expect(via_proxy("ed", "/file", "POST", {"path": name, "base": "0" * 20, "text": "Overwritten.\n", "force": True}), (403, 404),
               f"POST file {name!r} (not editable)")
    if (any(read_bytes(p) != data for p, data in before.items()) or os.path.exists(os.path.join(root, "new.tex"))
            or os.path.exists(os.path.join(root, "sub", "new.bib")) or os.path.getsize(os.path.join(root, "big.bib")) != (5 << 20) + 2):
        fail("a refused save changed or made a file")
    ok("CRLF files keep their line endings (and saves the mode); files that are not UTF-8 get 415; paths outside the editable"
       " set get 404")
    wait_commit(sh, "ed", ("body.tex", "crlf.bib"), "the editor's saves")

    def opened(name):
        source = expect_dict(via_proxy("ed", "/status/main.pdf?open=" + urllib.parse.quote(name, safe="")), 200,
                             f"status ?open={name}").get("source")
        return (source.get("path"), source.get("version")) if isinstance(source, dict) else (None, None)

    if opened("body.tex") != ("body.tex", version_of(read_bytes(path))):
        fail(f"status ?open=body.tex: {opened('body.tex')}, not its version")
    put(path, with_paragraph(read_text(path), 5, "Paragraph five, changed on disk while open."))
    if (opened("body.tex") != ("body.tex", version_of(read_bytes(path)))
            or opened("crlf.bib") != ("crlf.bib", version_of(read_bytes(crlf_path)))):
        fail("status ?open= does not give a file's version as on disk (after a change, or for a CRLF file)")
    for name in ("../shared-state/token", "leak.bib", "nope.tex"):
        if opened(name)[1]:
            fail(f"status ?open={name} gives a version (not an editable file)")
    lock = os.path.join(root, ".git", "index.lock")  # (as while someone runs git in the shared copy)
    open(lock, "w").close()
    current = expect_dict(via_proxy("ed", "/file?path=body.tex"), 200, "GET file body.tex")
    save("ed", {"path": "body.tex", "base": current["version"], "text": current["text"].replace("Walrus yak zebu", "Walrus yak and zebu")},
         200, "an editor's save inside protected text (no server-side check, by design)")
    saved = time.monotonic()
    if "Walrus yak and zebu" not in read_text(path):
        fail("an editor's save inside protected text was not written")
    # Before it writes the commenter's note in body.tex, the server commits the editor's edit there (a commit holds
    # one person's edits), which it can't while git is busy: so the note waits a moment, then is refused (503, to be
    # tried again), with nothing written.
    cms = [n for n in as_viewer("/notes/main.pdf") if isinstance(n, dict) and n.get("macro") == "cm" and n.get("tex") == "CM note edited"]
    if not cms:
        fail("/notes does not list the commenter's note CM note edited")
    later = {"action": "edit", "source": "body.tex", "line": cms[0]["line"], "macro": "cm", "tex": "CM note edited",
             "text": "CM note, edited once git was free"}
    before = read_bytes(path)
    if (expect_dict(note(sh, "cm", later), 503, "a commenter's note in body.tex while the editor's edit there can't be committed")
            .get("retry") is not True or read_bytes(path) != before):
        fail("a commenter's note, refused while the editor's edit in the same file can't be committed, is not to be tried again,"
             " or changed body.tex")
    time.sleep(max(0.0, saved + 4 - time.monotonic()))  # (two quiet periods since the save)
    if git_out(root, "rev-list", f"{sh['tip']}..shared"):
        fail("auto-commit committed while .git/index.lock existed")
    os.remove(lock)
    wait_commit(sh, "ed", ("body.tex",), "the editor's save, once .git/index.lock is gone")
    note_ok(sh, "cm", later, "the commenter's note, once the editor's edit is committed")
    wait_commit(sh, "cm", ("body.tex",), "the commenter's note")
    ok("status ?open= follows the open file; editors may change protected text; edits wait out a git lock, then commit,"
       " and others' writes to the same file wait for that (503 meanwhile)")

    # During a merge in the shared copy (as when the owner merges the paper's main branch into it), writes are
    # refused (503, to be tried again): the merge's commit would take them, or its abort lose them. After it, they
    # go through.
    current = expect_dict(via_proxy("ed", "/file?path=body.tex"), 200, "GET file body.tex")
    nine = {"path": "body.tex", "base": current["version"],
            "text": with_paragraph(current["text"], 9, "Paragraph nine, saved once the merge was over.")}
    after = {**later, "tex": later["text"], "text": "CM note, edited once the merge was over"}
    git(root, "-c", "core.hooksPath=/dev/null", "merge", "--no-ff", "--no-commit", "side")
    if not os.path.isfile(os.path.join(root, ".git", "MERGE_HEAD")):
        fail("git merge --no-commit side left no merge in progress in the shared copy")
    before = read_bytes(path)
    for result, what in ((via_proxy("ed", "/file", "POST", nine), "the editor's save"), (note(sh, "cm", after), "a commenter's note")):
        if expect_dict(result, 503, f"{what} during a merge in the shared copy").get("retry") is not True:
            fail(f"{what} during a merge in the shared copy is refused, but not to be tried again: {result[2][:200]!r}")
    if read_bytes(path) != before:
        fail("a write during a merge in the shared copy changed body.tex")
    git(root, "-c", "core.hooksPath=/dev/null", "merge", "--abort")
    save("ed", nine, 200, "the editor's save, once the merge is over")
    wait_commit(sh, "ed", ("body.tex",), "the editor's save after the merge")
    note_ok(sh, "cm", after, "the commenter's note, once the merge is over")
    wait_commit(sh, "cm", ("body.tex",), "the commenter's note after the merge")
    ok("during a merge in the shared copy, saves and notes are refused (503, to be tried again) and write nothing;"
       " after it, they go through")

    expect(via_proxy("ed", "/diff", "POST", {"rev": "side"}), 403, "the editor comparing with a commit that HEAD does not build on")
    expect(via_proxy("ed", "/diff", "POST", {"rev": "--output=x"}), (400, 403, 404), "the editor comparing with a bad revision")
    expect(via_proxy("ed", "/diff", "POST", {"rev": "main"}), 200, "the editor comparing with a commit that HEAD builds on")
    expect(with_token(owner, "/diff", "POST", {"rev": "side"}), (200, 409), "the owner comparing with any commit")
    if not isinstance(expect_dict(via_proxy("ed", "/commits"), 200, "GET commits for the editor").get("commits"), list):
        fail("GET commits for the editor has no commits list")
    expect(via_proxy("ed", "/rebuild", "POST", {}), 202, "the editor rebuilding")
    if editor_calls(sh["editor_log"]):
        fail(f"the editor stand-in ran for someone other than the owner: {editor_calls(sh['editor_log'])}")
    expect(with_token(owner, "/open", "POST", {"file": "main.tex", "line": 9}), 200, "the owner's POST open")
    calls = wait_for("the owner's POST open to run the editor stand-in", lambda: editor_calls(sh["editor_log"]), timeout=10, step=0.2)
    if len(calls) != 1 or not calls[0].startswith("-g ") or not calls[0].endswith("/main.tex:9"):
        fail(f"the editor stand-in got {calls}, not one -g .../main.tex:9")
    ok("editors compare only with commits HEAD builds on (the owner with any) and rebuild; only the owner's POST open runs the editor")


def commit_checks(sh):
    """Auto-commit: the owner's POST commit commits what is pending; every commit since the start is one
    user's edits, authored as that user from the users file and committed as the owner, with only files the
    server wrote; only the shared branch moved; the paper's hooks and signing settings did not apply; the
    HEAD baseline was not rebuilt for each commit."""
    root, users = sh["root"], sh["users"]
    flushed = expect_dict(with_token(sh["token"], "/commit", "POST", {}), 200, "the owner's POST commit")
    if not isinstance(flushed.get("commits"), list):
        fail(f"POST commit's answer has no commits list: {flushed}")
    if os.path.exists(os.path.join(sh["tmp"], "hook-ran")):
        fail("the paper's git hooks ran for a commit (the server commits with core.hooksPath=/dev/null)")
    shas = git_out(root, "rev-list", "--reverse", f"{sh['base']}..shared").split()
    if not shas:
        fail("nothing was committed on the shared branch")
    wrote = {}
    for sha in shas:
        name, email, cname, cemail, parents, subject = git_out(
            root, "show", "-s", "--format=%an%x1f%ae%x1f%cn%x1f%ce%x1f%P%x1f%s", sha).split("\x1f", 5)
        files = git_out(root, "diff-tree", "--no-commit-id", "--name-only", "-r", sha).split()
        uid = commit_user(root, sha)
        user = users.get(uid) or {}
        if (not user or (name, email) != (user["name"], user["email"]) or (cname, cemail) != ("Owner Example", "owner@example.org")
                or len(parents.split()) != 1 or not subject.startswith(f"{user['name']}: ") or not files
                or not all(f in subject for f in files)):
            fail(f"commit {sha[:9]}: author {name} <{email}>, committer {cname} <{cemail}>, {subject!r}, user {uid}, files {files}"
                 " (want the user's name and email from the users file, the owner's as committer, '<Name>: ...' naming the files,"
                 " a Latex-Live-Shared trailer)")
        wrote.setdefault(uid, set()).update(files)
    if wrote != {"ed": {"body.tex", "crlf.bib"}, "cm": {"body.tex"}}:
        fail(f"the commits hold other files, or other users' edits, than the server wrote for each: {wrote}")
    if git_out(root, "symbolic-ref", "HEAD") != "refs/heads/shared":
        fail("HEAD is no longer the shared branch")
    others = [line for line in git_out(root, "for-each-ref", "--format=%(refname) %(objectname)").splitlines()
              if not line.startswith("refs/heads/shared ")]
    if others != [line for line in sh["refs"].splitlines() if not line.startswith("refs/heads/shared ")]:
        fail(f"auto-commit moved refs other than the shared branch: {others}")
    if subprocess.run(["git", "-C", root, "merge-base", "--is-ancestor", sh["base"], "shared"]).returncode:
        fail("the shared branch no longer builds on where it started")
    status = git_out(root, "status", "--porcelain", "--untracked-files=normal").splitlines()
    changed = [line for line in status if not line.startswith("??")]
    untracked = sorted(line[3:] for line in status if line.startswith("??"))
    if changed != [" M refs.bib"]:
        fail(f"after POST commit, the tracked changes are {changed}, not just the test's own change to refs.bib (LATER_CHANGE)")
    if untracked != sorted([".latexmkrc", ".private.bib", "appendix.tex", "big.bib", "build/", "latexmkrc", "latin.bib", "leak.bib",
                            "main.pdf", "reviews.pdf"]):
        fail(f"the shared copy has unexpected untracked files: {untracked}")
    if os.path.isdir(os.path.join(root, "build", "head")):
        fail("shared mode built the HEAD baseline though no viewer asked for base=head (each commit moves HEAD)")
    ok(f"auto-commit: {len(shas)} commits, each one user's (author from the users file, committed by the owner), only files"
       " the server wrote, on the shared branch only")


def shared_stop_checks(sh, sharing):
    """The edits build. An edit still uncommitted when the instance stops (git in the middle of a merge, which
    the stop doesn't wait out) is kept in its state folder for the next start; stopping removes the instance
    from its list. Restarted without a build on an https public URL, it commits that edit as its writer's,
    and the test's change beside it (LATER_CHANGE) as the owner's; a personal link's cookie is Secure on the
    public Host only, and with a proxy uid that is not the peer's (Linux), the identity header counts for
    nothing. Neither run printed a token or a traceback."""
    root, path, pending = sh["root"], os.path.join(sh["root"], "body.tex"), os.path.join(sh["state"], "pending", "*.json")

    def built():
        if not settled(root):
            return None
        info = expect_dict(via_proxy("vw", "/status/main.pdf"), 200, "the shared copy's status")
        result = info.get("build") or {}
        if result.get("ok") is False:
            fail(f"the shared copy no longer builds after the edits: {result.get('errors')}")
        return result.get("ok") and not info.get("building") and info.get("version") != sh["version0"]

    wait_for("the shared copy to build the edits", built, timeout=120)
    ok("the shared copy builds with everyone's edits and notes")

    tip = git_out(root, "rev-parse", "shared")
    current = expect_dict(via_proxy("ed", "/file?path=body.tex"), 200, "GET file body.tex")
    last = with_paragraph(current["text"], 11, "Paragraph eleven, saved just before the stop.")
    save("ed", {"path": "body.tex", "base": current["version"], "text": last}, 200, "a save just before the stop")
    git(root, "-c", "core.hooksPath=/dev/null", "merge", "--no-ff", "--no-commit", "side")  # (before the save's quiet period ends)
    stop(sh["proc"])
    sharing.remove(sh["proc"])
    if recorded(sh["state"]):
        fail(f"the stopped shared-mode instance is still listed: {recorded(sh['state'])}")
    kept = [read_json_file(p) or {} for p in glob.glob(pending)]
    if (git_out(root, "rev-parse", "shared") != tip
            or [k.get("pending", {}).get("ed", {}).get("paths") for k in kept] != [{"body.tex": "edit"}]):
        fail(f"the editor's save just before a stop during a merge is committed, or not kept for the next start: {kept}")
    quiet_log(sh["log"], "the shared-mode instance")
    git(root, "-c", "core.hooksPath=/dev/null", "merge", "--abort")
    if read_text(path) != last:
        fail("aborting the merge undid the editor's save (a mistake in this check)")
    linux = sys.platform.startswith("linux")
    env = {**sh["env"], "LATEX_LIVE_PUBLIC_URL": "https://paper.invalid"}
    if linux:
        env["LATEX_LIVE_PROXY_UID"] = str(os.getuid() + 1)
    proc = start(root, "main", "--no-build", env=env, port=SHARED_PORT)
    sharing.append(proc)
    log = LOGS[-1]

    def answering():
        if proc.poll() is not None:
            fail(f"the restarted shared-mode instance exited (status {proc.returncode})")
        r = send("GET", "/me", [("Cookie", f"latex_live={sh['token']}")], quiet=True)
        return r is not None and r[0] == 200

    wait_for("the shared-mode instance, restarted", answering, timeout=60, step=0.5)
    end = time.monotonic() + 20  # (it commits what the last run left before it serves a write)
    while glob.glob(pending) or git_out(root, "status", "--porcelain", "--untracked-files=no"):
        if time.monotonic() > end:
            fail(f"the restarted instance left edits uncommitted ({git_out(root, 'status', '--porcelain', '--untracked-files=no')!r}),"
                 f" or kept for later: {[read_json_file(p) for p in glob.glob(pending)]}")
        time.sleep(0.2)
    shas = git_out(root, "rev-list", "--reverse", f"{tip}..shared").split()
    found = {commit_user(root, sha): (git_out(root, "show", "-s", "--format=%an <%ae>", sha),
                                      git_out(root, "diff-tree", "--no-commit-id", "--name-only", "-r", sha).split()) for sha in shas}
    want = {"ed": ("Eden Doyle <eden@example.org>", ["body.tex"]), "unattributed": ("Owner Example <owner@example.org>", ["refs.bib"])}
    if len(shas) != 2 or found != want:
        fail(f"at the restart, the commits are {found}: not the editor's save before the stop as theirs, and the change made"
             " beside the server (refs.bib) as the owner's")
    ok("an edit uncommitted when the instance stopped (a merge in progress) is committed as its writer's at the next start,"
       " and the change made beside the server as the owner's")

    tk = sh["users"]["tk"]["token"]
    for host, secure in (("paper.invalid", True), (None, False)):
        first = send("GET", f"/me?t={tk}", [("Host", host)] if host else [])
        set_cookie = header(first, "Set-Cookie") or ""
        if first[0] != 200 or bool(re.search(r";\s*secure\b", set_cookie, re.I)) != secure:
            fail(f"a personal link on {host or 'a loopback Host'} with an https public URL: {first[0]}, cookie {redact(set_cookie)!r}"
                 f" ({'' if secure else 'not '}Secure wanted)")
    if linux:
        expect(via_proxy("ed", "/me"), 403, "the identity header from a peer whose uid is not LATEX_LIVE_PROXY_UID")
    stop(proc)
    sharing.remove(proc)
    if recorded(sh["state"]):
        fail(f"the stopped shared-mode instance is still listed: {recorded(sh['state'])}")
    quiet_log(log, "the restarted shared-mode instance")
    if editor_calls(sh["host_tex_log"]):
        fail(f"shared mode ran the host's TeX: {editor_calls(sh['host_tex_log'])}")
    if not linux:
        print("skip: LATEX_LIVE_PROXY_UID is not checked here (it needs Linux's /proc/net)", flush=True)
    ok("with an https public URL, cookies are Secure on its Host only"
       + ("; the identity header counts only from a peer with LATEX_LIVE_PROXY_UID" if linux else "")
       + "; stopped shared instances are no longer listed")


def quiet_log(path, what):
    """Check an instance's output, once it has stopped: no token (its log may be kept, and read by others), and
    no traceback (an error the server did not expect)."""
    with open(path, errors="replace") as f:
        printed = f.read()
    if prints_token(printed):
        fail(f"{what} printed a token")
    if "Traceback (most recent call last)" in printed:
        fail(f"{what} printed a traceback:\n{printed[printed.index('Traceback (most recent call last)'):][:2000]}")


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
