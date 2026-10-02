#!/usr/bin/env python3
"""latex-live: LaTeX Workshop-style live preview without the extension.

Usage (from a paper root):  live.py [<doc>[.tex]] [--port 44100] [--page-limit N] [--build-dir DIR] [--no-build] [--no-open] [--host-tex]

- Without <doc>, it builds the root .tex file (one with a \\documentclass) whose sources
  were edited last; the viewer switches to another root file.
- `latexmk -pvc` rebuilds into build/ whenever any input file changes (latexmkrc),
  as job <doc>-live so stale <doc>.aux/.bbl from in-place builds are never read.
- pdflatex/bibtex run in the TeX Live 2024 image (close to Overleaf) via one
  short-lived rootless podman container per call (~0.2 s each); --host-tex
  uses the host's TeX installation instead. latexmk itself stays on the host.
- latex_report.py publishes <doc>.pdf after successful runs only and prints
  file:line diagnostics (picked up by the VS Code task's problem matcher).
- <doc>.pdf opens in a VS Code editor tab (via `code`, when run from a VS Code
  terminal); the vscode-pdf extension (mathematic.vscode-pdf) reloads it on each
  publish, staying on the same page.
- A PDF.js viewer (viewer.html) is also served: it reloads in place keeping exact
  scroll and zoom, and double-clicking the PDF jumps VS Code to the matching
  source line (SyncTeX inverse search, via `code -g`); forward.py does the
  reverse (source line -> highlighted spot in the viewer). The viewer also shows
  build status (click an error to open it), a page-limit check (also without
  the inline notes, from a background build), search, an outline, page
  thumbnails, the notes and a pre-submission check (including stale generated
  figures and consistency lints) in a sidebar, hover previews of references,
  highlights of what changed since the last build (text and figures), hints on
  where to save space, a notes-free view, and a latexdiff against any git
  commit. On a remote machine, forward the port (e.g. in VS Code's Ports panel)
  and open the localhost URL; with LATEX_LIVE_HOST set, a direct URL on that host
  is printed too (e.g. a name a tunnel exposes). Anything that reaches the port
  can send requests, so every request needs the token (kept in ./token next to
  live.py; ?t=TOKEN once, then a SameSite=Strict cookie).
- One instance per document: a second one (e.g. another VS Code window opening
  the same folder) prints the running viewer's URL and exits.
"""

import argparse
import collections
import ctypes
import fcntl
import glob
import gzip
import hashlib
import hmac
import http.cookies
import http.server
import json
import os
import queue
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.parse

HERE = os.path.dirname(os.path.realpath(__file__))
TEXLIVE_IMAGE = "docker.io/minidocks/texlive:2024-full"
IMAGE_MISSING = f"{TEXLIVE_IMAGE} is not pulled (to fix: podman pull {TEXLIVE_IMAGE})"
# Build directory, relative to the paper root (--build-dir; LATEX_LIVE_BUILD for latexmkrc,
# latex_report.py, and forward.py). With a non-default one, <doc>.pdf is published there too,
# so a second instance can run beside the usual build without touching its files.
BUILD = os.environ.get("LATEX_LIVE_BUILD", "build")
PUBLISH = "" if BUILD == "build" else BUILD
TOKEN_FILE = os.path.join(HERE, "token")


def load_token():
    """Stable per-user secret, so the viewer URL can be bookmarked."""
    try:
        with open(TOKEN_FILE) as f:
            return f.read().strip()
    except FileNotFoundError:
        token = secrets.token_urlsafe(16)
        fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(token)
        return token


class DualStackServer(http.server.ThreadingHTTPServer):
    address_family = socket.AF_INET6

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)  # accept IPv4 too
        super().server_bind()
STATIC = {"/viewer.html"}  # plus everything under /pdfjs/


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map, ".mjs": "text/javascript"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=HERE, **kwargs)

    def authorize(self, query):
        """Every client needs ?t=TOKEN once, then the cookie (forwarded traffic arrives via loopback too)."""
        self.set_cookie = False
        token = self.server.token
        cookie = http.cookies.SimpleCookie(self.headers.get("Cookie", "")).get("latex_live")
        if cookie and hmac.compare_digest(cookie.value, token):
            return True
        if hmac.compare_digest(query.get("t", [""])[0], token):
            self.set_cookie = True
            return True
        return False

    def end_headers(self):
        if getattr(self, "set_cookie", False):
            self.send_header("Set-Cookie", f"latex_live={self.server.token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000")
        super().end_headers()

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(url.path)
        if not self.authorize(urllib.parse.parse_qs(url.query)):
            return self.send_error(403, f"open the URL with ?t=<token> from {TOKEN_FILE}")
        if path == "/":
            self.send_response(302)
            self.send_header("Location", f"/viewer.html?file={urllib.parse.quote(self.server.default_pdf)}")
            self.end_headers()
        elif path.startswith(("/pdf/", "/status/", "/outline/", "/notes/", "/check/", "/graphics/", "/margin/")):
            name = path.split("/", 2)[2]
            if not name.endswith(".pdf") or os.path.basename(name) != name:
                return self.send_error(404)
            pdf = pdf_path(self.server.paper_root, name)
            doc_pdf = re.sub(r"(-head)?-(clean|diff|head)\.pdf$", ".pdf", name)  # the document a derived PDF belongs to
            if path.startswith("/status/"):
                body = json.dumps(status(self.server, name)).encode()
            elif path.startswith("/outline/"):
                body = json.dumps(toc_outline(self.server.paper_root, doc_pdf, name.endswith("-clean.pdf"))).encode()
            elif path.startswith("/notes/"):
                body = json.dumps(notes(self.server.paper_root, doc_pdf)).encode()
            elif path.startswith("/check/"):
                body = json.dumps(checks(self.server, doc_pdf)).encode()
            elif path.startswith("/graphics/"):
                body = json.dumps(graphics(self.server.paper_root, doc_pdf)).encode()
            elif path.startswith("/margin/"):
                body = json.dumps(margin_notes(self.server.paper_root, doc_pdf)).encode()
            elif os.path.isfile(pdf):
                with open(pdf, "rb") as f:
                    body = f.read()
            else:
                return self.send_error(404)
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf" if path.startswith("/pdf/") else "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif path == "/synctex":
            self.send_text(*synctex_edit(self.server.paper_root, urllib.parse.parse_qs(url.query)))
        elif path == "/open":
            self.send_text(*open_source(self.server.paper_root, urllib.parse.parse_qs(url.query)))
        elif path == "/forward":
            self.send_json(*forward_request(self.server.paper_root, urllib.parse.parse_qs(url.query)))
        elif path == "/commits":
            self.send_json(200, commits(self.server.paper_root))
        elif path == "/diff":
            self.send_json(*start_diff(self.server, urllib.parse.parse_qs(url.query)))
        elif path == "/docs":
            self.send_json(200, {"doc": self.server.default_pdf[:-4], "docs": root_docs(self.server.paper_root)})
        elif path == "/switch":
            self.send_json(*request_switch(self.server, urllib.parse.parse_qs(url.query)))
        elif path in STATIC or (path.startswith("/pdfjs/") and ".." not in path and not path.endswith("/")):
            super().do_GET()
        else:
            self.send_error(404)

    def send_json(self, status, value):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, status, text):
        body = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # the viewer polls twice a second; keep the terminal clean
        pass


def image_pulled():
    """Whether the TeX Live image is present (~30 ms). Checked per use, not once: it can be removed
    (e.g. by a cleanup job) or pulled while the server runs."""
    return subprocess.run(["podman", "image", "exists", TEXLIVE_IMAGE]).returncode == 0


def synctex_unavailable():
    """IMAGE_MISSING when SyncTeX needs the image (no host synctex) and it is missing, else None."""
    return None if shutil.which("synctex") or image_pulled() else IMAGE_MISSING


def image_problem(server):
    """IMAGE_MISSING for the viewer while builds or SyncTeX need the image and it is missing, else
    None; checked at most every 5 s (the viewer polls twice a second)."""
    if not server.tex_exec and shutil.which("synctex"):
        return None
    if time.time() - server.image_checked > 5:
        server.image_checked, server.image_ok = time.time(), image_pulled()
    return None if server.image_ok else IMAGE_MISSING


def run_synctex(paper_root, *args):
    """Run `synctex <args>` and return its result records (dicts of the Key:value lines).

    The host has no synctex binary, so it runs in the TeX Live image (~0.2 s).
    """
    cmd = ["synctex", *args]
    if not shutil.which("synctex"):
        cmd = ["podman", "run", "--rm", "--pull=never", "--network=none", "--security-opt", "label=disable",
               "-v", f"{paper_root}:{paper_root}", "-w", paper_root, TEXLIVE_IMAGE, *cmd]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    records = []
    for line in out.splitlines():
        key, sep, value = line.partition(":")
        if key == "Output":  # each result record starts with Output:
            records.append({})
        if sep and records:
            records[-1][key] = value
    return records


def job_file(paper_root, pdf_name, ext):
    """build/<doc>-live<ext> for the published <doc>.pdf."""
    return os.path.join(paper_root, BUILD, f"{pdf_name[:-4]}-live{ext}")


def clean_file(paper_root, pdf_name, ext):
    """build/clean/<doc>-clean<ext>: the notes-free pass (latex_report.py; its PDF moves to build/)."""
    return os.path.join(paper_root, BUILD, "clean", f"{pdf_name[:-4]}-clean{ext}")


def pdf_path(paper_root, name):
    """Published <doc>.pdf in the paper root; derived <doc>-clean.pdf / -diff.pdf / -head.pdf /
    -head-clean.pdf in build/."""
    if re.search(r"-(clean|diff|head)\.pdf$", name):
        return os.path.join(paper_root, BUILD, name)
    return os.path.join(paper_root, PUBLISH, name)


def bib_span(paper_root, files, cache={}):
    """Where the bibliography starts and ends: ({"page", "y"}, {"page", "y"}) (y in points
    from the page top), or None. Maps the first and last lines of the .bbl to the PDF, cached
    per build. files = (synctex_gz, bbl, pdf).
    """
    synctex_gz, bbl, pdf = files
    try:
        mtime = os.stat(synctex_gz).st_mtime_ns
        with open(bbl, errors="replace") as f:
            last = sum(1 for _ in f)
    except OSError:
        return None
    if cache.get(synctex_gz, (None,))[0] != mtime:
        def where(line):
            rec = next((r for r in run_synctex(paper_root, "view", "-i", f"{line}:0:{bbl}", "-o", pdf) if "Page" in r), None)
            return rec and {"page": int(rec["Page"]), "y": float(rec["v"]) - float(rec["H"])}
        start = where(1)
        # The last lines (\end{thebibliography}, blank ones) may have no boxes: walk back to one that does.
        end = next(filter(None, (where(line) for line in range(last, max(1, last - 8), -1))), None) if start else None
        cache[synctex_gz] = (mtime, start and (start, end))
    return cache[synctex_gz][1]


def refs_start(paper_root, pdf_name, clean=False):
    """Where the bibliography starts, in the live build or the notes-free one."""
    f = clean_file if clean else job_file
    span = bib_span(paper_root, (f(paper_root, pdf_name, ".synctex.gz"), f(paper_root, pdf_name, ".bbl"),
                                 f(paper_root, pdf_name, ".pdf")))
    return span and span[0]


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def mtime(path):
    return os.stat(path).st_mtime_ns if os.path.exists(path) else 0


DOCCLASS_RE = re.compile(r"^[^%\n]*\\documentclass\b", re.M)


def root_docs(paper_root):
    """The root documents in the paper root: the .tex files with a \\documentclass that is not commented out."""
    docs = []
    for name in sorted(os.listdir(paper_root)):
        if name.endswith(".tex"):
            try:
                with open(os.path.join(paper_root, name), errors="replace") as f:
                    if DOCCLASS_RE.search(f.read(65536)):
                        docs.append(name[:-4])
            except OSError:
                pass
    return docs


def source_tree(paper_root, doc):
    """The .tex files that doc reads through \\input and \\include, from the source (no build needed)."""
    seen, todo = set(), [os.path.join(paper_root, f"{doc}.tex")]
    while todo:
        path = todo.pop()
        if path in seen or not os.path.isfile(path):
            continue
        seen.add(path)
        for m in INPUT_RE.finditer(read_tex(path)):
            name = m.group(1).strip()
            todo.append(os.path.join(paper_root, name if name.endswith(".tex") else name + ".tex"))
    return seen


def pick_doc(paper_root, docs):
    """The root document whose sources were edited last. On a tie (the newest file is shared, e.g.
    a preamble), the one last built here."""
    try:
        with open(os.path.join(paper_root, BUILD, "latex-live-doc")) as f:
            last = f.read().strip()
    except OSError:
        last = None

    def edited(doc):
        return max((os.path.getmtime(path) for path in source_tree(paper_root, doc)), default=0)

    return max(docs, key=lambda doc: (edited(doc), doc == last))


LAYOUT_RE = re.compile(r"^latex-live layout: (.+)$", re.M)


def layout(paper_root, pdf_name, cache={}):
    """The text area of the last build, as latexmkrc's pretex hook printed it to the log, in PDF points
    from the page's top-left: {W, H, left, top, bottom, leading, columns}. None until a build has
    printed it. A build that stopped before \\begin{document} keeps the previous value."""
    log = job_file(paper_root, pdf_name, ".log")
    stamp = mtime(log)
    if cache.get(log, (None,))[0] != stamp:
        try:
            with open(log, errors="replace") as f:
                m = LAYOUT_RE.search(f.read())
        except OSError:
            m = None
        value = cache.get(log, (None, None))[1]
        if m:
            try:  # TeX points (72.27 per inch) to PDF points (72); glue keeps only its natural size
                pw, ph, hoff, side, voff, top, head, sep, tw, th, cw, skip = (
                    float(re.match(r"-?[\d.]+", v.strip()).group()) * 72 / 72.27 for v in m.group(1).split(","))
                y = 72 + voff + top + head + sep  # TeX's origin is 1in from the top-left corner
                value = {"W": round(pw, 2), "H": round(ph, 2), "left": round(72 + hoff + side, 2), "top": round(y, 2),
                         "bottom": round(y + th, 2), "leading": round(skip, 2), "columns": 2 if cw < 0.75 * tw else 1}
            except (AttributeError, ValueError):
                pass
        cache[log] = (stamp, value)
    return cache[log][1]


# Main-text page limits of submission styles, for documents run without --page-limit.
VENUE_PAGE_LIMITS = {r"iclr\d{4}_conference": 9, r"neurips_\d{4}": 9, r"colm\d{4}_conference": 9}
USEPACKAGE_RE = re.compile(r"\\usepackage\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")


def page_limit(server, pdf_name):
    """--page-limit, else the limit of the venue style the document loads, else None."""
    if server.page_limit:
        return server.page_limit
    packages = {p.strip() for m in USEPACKAGE_RE.finditer(read_tex(os.path.join(server.paper_root, pdf_name[:-4] + ".tex")))
                for p in m.group(1).split(",")}
    return next((n for pattern, n in VENUE_PAGE_LIMITS.items() if any(re.fullmatch(pattern, p) for p in packages)), None)


def status(server, pdf_name):
    """Everything the viewer polls for: PDF version, build state, forward-search target, page limit
    and layout (and the notes-free build's PDF, to measure it too), and which document the server
    builds (viewers of another one switch to it)."""
    root = server.paper_root
    pdf = pdf_path(root, pdf_name)
    version = str(mtime(pdf)) if os.path.isfile(pdf) else None
    if pdf_name.endswith("-diff.pdf"):  # a latexdiff PDF (start_diff)
        d = server.diff
        return {"version": version, "building": d.get("state") == "running", "forward": None, "pageLimit": None,
                "layout": layout(root, pdf_name.replace("-diff.pdf", ".pdf")), "diff": d, "build": d.get("state") == "failed" and {"ok": False, "time": d["time"], "errors": [{"file": None, "line": None, "msg": d["msg"]}]}}
    limit = page_limit(server, pdf_name)
    info = {
        "version": version,
        "building": mtime(job_file(root, pdf_name, ".building")) > mtime(job_file(root, pdf_name, ".status.json")),
        "build": read_json(job_file(root, pdf_name, ".status.json")),
        "forward": read_json(job_file(root, pdf_name, ".forward.json")),
        "pageLimit": limit,
        "refs": refs_start(root, pdf_name) if limit else None,
        "layout": layout(root, pdf_name),
        "doc": server.default_pdf,
    }
    clean_pdf = pdf_path(root, pdf_name[:-4] + "-clean.pdf")
    if os.path.isfile(clean_pdf):
        info["clean"] = {"version": str(mtime(clean_pdf)), "refs": refs_start(root, pdf_name, clean=True) if limit else None}
    info["head"] = head_status(server, pdf_name)
    info["texImage"] = image_problem(server)
    return info


TOC_LEVELS = {"part": 0, "chapter": 0, "section": 1, "subsection": 2, "subsubsection": 3, "paragraph": 4}
TEX_SYMBOLS = {"Delta": "Δ", "alpha": "α", "beta": "β", "gamma": "γ", "times": "×", "&": "&", "%": "%", "_": "_", "xspace": " ",
               "ldots": "…", "dots": "…"}
MACRO_DEF_RE = re.compile(r"\\(?:(?:re|provide)?newcommand\*?|DeclareRobustCommand\*?)\s*\{?\\([a-zA-Z@]+)\}?\s*(?=\{)|\\def\\([a-zA-Z@]+)(?=\{)")
INPUT_RE = re.compile(r"\\(?:input|include|subfile)\s*\{([^{}]+)\}")


def brace_group(text, i):
    """The balanced {...} group starting at text[i] == "{": (content, index after it)."""
    depth = 0
    for j in range(i, len(text)):
        if text[j - 1] != "\\":
            depth += {"{": 1, "}": -1}.get(text[j], 0)
        if depth == 0:
            return text[i + 1:j], j + 1
    return "", len(text)


def project_macros(paper_root, fls):
    """Zero-argument macro definitions from the project's .tex files (those the build read).

    Definitions are read in TeX's order, from the root file (the first one read) down through
    \\input/\\include, so a redefinition wins as in TeX, e.g. one in the root after \\input{preamble}.
    Files not reached that way only add macros that are still undefined.
    """
    macros = {}
    try:
        with open(fls) as f:
            paths = [os.path.normpath(os.path.join(paper_root, l[6:].strip()))
                     for l in f if l.startswith("INPUT ") and l.rstrip().endswith(".tex")]
    except OSError:
        return macros
    paths = [p for p in dict.fromkeys(paths)
             if p.startswith(os.path.join(paper_root, "")) and not p.startswith(os.path.join(paper_root, BUILD, ""))]
    done = set()

    def read(path, define):
        done.add(path)
        try:
            with open(path, errors="replace") as f:
                text = re.sub(r"(?<!\\)%.*\n?[ \t]*", "", f.read())  # (a comment also eats the line break)
        except OSError:
            return
        pos = 0
        for m in [*INPUT_RE.finditer(text), None]:
            for d in MACRO_DEF_RE.finditer(text, pos, m.start() if m else len(text)):
                body, _ = brace_group(text, d.end())
                if "#" not in body:
                    define(d.group(1) or d.group(2), body)
            if m:
                pos = m.end()
                child = os.path.normpath(os.path.join(paper_root, m.group(1).strip()))
                child = next((c for c in (child, child + ".tex") if c in paths and c not in done), None)
                if child:
                    read(child, define)

    if paths:
        read(paths[0], macros.__setitem__)
    for path in paths:
        if path not in done:
            read(path, macros.setdefault)
    return macros


def tex_to_text(tex, macros, depth=0, typeset=False):
    """Rough plain text for a TeX snippet: expand project macros, keep a few symbols, drop other commands.
    \texorpdfstring gives its bookmark form, or with typeset=True the typeset one."""
    while (i := tex.find("\\texorpdfstring")) >= 0:
        typeset_text, k = brace_group(tex, tex.find("{", i))
        pdf_text, k = brace_group(tex, tex.find("{", k))
        tex = tex[:i] + (typeset_text if typeset else pdf_text) + tex[k:]
    if typeset:  # conditionals: the \else branch (e.g. a glyph's non-bold form)
        tex = re.sub(r"\\if\w*(?:[^\\]|\\(?!else|fi\b))*?\\else((?:[^\\]|\\(?!fi\b))*)\\fi\b", lambda m: m.group(1).strip(), tex)

    def macro(m):
        name = m.group(1)
        if name in macros and depth < 6:
            return tex_to_text(macros[name], macros, depth + 1, typeset)
        return TEX_SYMBOLS.get(name, "")
    return re.sub(r"\\([a-zA-Z@]+|.)\s*", macro, tex).replace("{", "").replace("}", "")


def toc_outline(paper_root, pdf_name, clean=False):
    """Outline entries [{level, title, page, dest}] from the .toc (of the notes-free build with
    clean=True, whose pages differ), for PDFs without bookmarks.

    \contentsline{type}{title}{page label}{anchor}: the anchor (a named destination) is
    empty when hyperref doesn't handle the entry; the viewer then finds the title on the page.
    """
    try:
        with open((clean_file if clean else job_file)(paper_root, pdf_name, ".toc"), errors="replace") as f:
            text = f.read()
    except OSError:
        return []
    macros = project_macros(paper_root, job_file(paper_root, pdf_name, ".fls"))
    entries, i = [], 0
    while (i := text.find("\\contentsline", i)) >= 0:
        args, i = [], i + len("\\contentsline")
        while len(args) < 4 and (j := text.find("{", i)) >= 0 and not text[i:j].strip():
            arg, i = brace_group(text, j)
            args.append(arg)
        if len(args) == 4 and args[0] in TOC_LEVELS:
            title = tex_to_text(re.sub(r"\\numberline\s*\{([^}]*)\}", r"\1  ", args[1]), macros)
            title = re.sub(r" ([:;,.?!)])", r"\1", " ".join(title.split()))
            entries.append({"level": TOC_LEVELS[args[0]], "title": title,
                            "page": args[2], "dest": args[3] or None})
    return entries


def forward_target(paper_root, src, line, clean=False):
    """Forward search: where source line src:line (absolute path) is in the most recent build that
    has it (or in the notes-free build). Returns (pdf, {"page", "x", "y", "w", "h"}) in points from
    the page's top-left, or None.
    """
    pattern = os.path.join("clean", "*-clean.synctex.gz") if clean else "*-live.synctex.gz"
    jobs = sorted(glob.glob(os.path.join(paper_root, BUILD, pattern)), key=os.path.getmtime, reverse=True)
    for synctex_gz in jobs:
        pdf = synctex_gz[: -len(".synctex.gz")] + ".pdf"
        records = [r for r in run_synctex(paper_root, "view", "-i", f"{line}:0:{src}", "-o", pdf) if "Page" in r]
        if not records:
            continue
        # A long source line can span several output lines: cover all of them on the first page.
        page = records[0]["Page"]
        boxes = [(float(r["h"]), float(r["v"]) - float(r["H"]), abs(float(r["W"])), float(r["H"]))
                 for r in records if r["Page"] == page]
        x, y = min(b[0] for b in boxes), min(b[1] for b in boxes)
        return pdf, {"page": int(page), "x": x, "y": y,
                     "w": max(b[0] + b[2] for b in boxes) - x, "h": max(b[1] + b[3] for b in boxes) - y}
    return None


def forward_request(paper_root, query):
    """/forward?file=<relative path>&line=N[&clean=1]: forward search for the viewer's lists."""
    try:
        src, line = os.path.realpath(os.path.join(paper_root, query["file"][0])), int(query["line"][0])
    except (KeyError, ValueError):
        return 400, {"error": "expected file, line"}
    if not src.startswith(os.path.realpath(paper_root) + os.sep):
        return 404, {"error": "not a file in the paper"}
    found = forward_target(paper_root, src, line, clean=query.get("clean") == ["1"])
    if found:
        return 200, found[1]
    if missing := synctex_unavailable():
        return 503, {"error": missing}
    return 404, {"error": f"{query['file'][0]}:{line} is not in the PDF"}


def project_tex_files(paper_root, fls):
    """The project's .tex files that the build read, in reading order (not generated ones in build/)."""
    try:
        with open(fls) as f:
            paths = [os.path.normpath(l[6:].strip()) for l in f if l.startswith("INPUT ") and l.rstrip().endswith(".tex")]
    except OSError:
        return []
    root = os.path.join(paper_root, "")
    paths = [os.path.join(paper_root, p) for p in dict.fromkeys(paths)]
    return [p for p in paths if p.startswith(root) and not p.startswith(os.path.join(paper_root, BUILD, ""))]


def read_tex(path):
    """File text with comments blanked (line numbers kept)."""
    try:
        with open(path, errors="replace") as f:
            return re.sub(r"(?<!\\)%.*", "", f.read())
    except OSError:
        return ""


NOTE_DEF_RE = re.compile(r"\\(?:re|provide)?newcommand\*?\s*\{?\\([a-zA-Z]+)\}?\s*\[1\]\s*(?=\{)")


def note_kinds(texts):
    """{note macro: author label} from the macro definitions in texts."""
    kinds = {}
    for text in texts:
        for m in NOTE_DEF_RE.finditer(text):
            body, _ = brace_group(text, m.end())
            if "\\todotxt" in body:
                label = re.search(r"\\textbf\{([^}:]+):?\}", body)
                kinds[m.group(1)] = label.group(1) if label else m.group(1)
    return kinds


def note_use_re(kinds):
    return re.compile(r"\\(%s)\s*(?=\{)" % "|".join(sorted(kinds, key=len, reverse=True)))


def notes(paper_root, pdf_name):
    """The inline notes in the build's files, in reading order: [{author, text, file, line}].

    Note macros are the one-argument commands whose body goes through \todotxt (this
    project's show/hide switch), e.g. \todo and the per-author \eb, \ch; the author label
    is the bold "XX:" in the body.
    """
    files = project_tex_files(paper_root, job_file(paper_root, pdf_name, ".fls"))
    texts = {path: read_tex(path) for path in files}
    kinds = note_kinds(texts.values())
    if not kinds:
        return []
    macros = project_macros(paper_root, job_file(paper_root, pdf_name, ".fls"))
    found = []
    for path, text in texts.items():
        for m in note_use_re(kinds).finditer(text):
            body, _ = brace_group(text, m.end())
            words = " ".join(tex_to_text(body, macros).split())
            found.append({"author": kinds[m.group(1)], "text": words[:400], "file": os.path.relpath(path, paper_root),
                          "line": text.count("\n", 0, m.start()) + 1})
    return found


SYNCTEX_POINT_RE = re.compile(r"([gkx$])(\d+),(\d+):(-?\d+),(-?\d+)")  # glue, kerns, points, math


def synctex_points(synctex_gz, cache={}):
    """{(file, line): [(page, x, y)]}: where each source line's words, spaces, and math
    landed (points from the page's top-left, y at the baseline), from a build's SyncTeX file."""
    key = (synctex_gz, mtime(synctex_gz))
    if key not in cache:
        inputs, points, page = {}, {}, 0
        with gzip.open(synctex_gz, "rt", errors="replace") as f:
            for record in f:
                if record.startswith("Input:"):
                    tag, _, path = record[6:].rstrip("\n").partition(":")
                    inputs[tag] = os.path.normpath(path)
                elif record.startswith("{"):
                    page = int(record[1:])
                elif m := SYNCTEX_POINT_RE.match(record):
                    pt = 72 / 72.27 / 65536  # scaled points to PDF points
                    points.setdefault((inputs.get(m.group(2)), int(m.group(3))), []).append(
                        (page, round(int(m.group(4)) * pt, 1), round(int(m.group(5)) * pt, 1)))
        cache.clear()
        cache[key] = points
    return cache[key]


# Where a note's surrounding prose stops: paragraph breaks, environments, list items, headings.
PROSE_BREAK_RE = re.compile(r"\n[ \t]*\n|\\(?:begin|end)\{[^}]*\}|\\item\b|\\\\|"
                            r"\\(?:sub)*section\*?\{[^{}]*\}|\\paragraph\*?\{[^{}]*\}|\\caption\{")
# Rendered unpredictably (math, citations, cross-references): context stops there too.
OPAQUE_RE = re.compile(r"\$[^$]*\$|\\\(.*?\\\)|\\[a-zA-Z]*(?:cite|ref)[a-zA-Z]*\*?(?:\[[^\]]*\])*\{[^}]*\}")


def margin_notes(paper_root, pdf_name):
    """The inline notes placed in the notes-free PDF, for the viewer's margin bubbles:
    [{author, text, file, line, points: [[page, x, y], ...], side, before, after}].

    points are where the note's source line (or, if that line typeset nothing without its
    notes, the nearest line of the same paragraph) landed; side says which of those it is
    ("line", "before", "after"). before/after are the few typeset words around the note, which
    the viewer finds in the page text to put the note's caret between two words.
    """
    synctex_gz = clean_file(paper_root, pdf_name, ".synctex.gz")
    fls = job_file(paper_root, pdf_name, ".fls")
    if not os.path.exists(synctex_gz):
        return []
    points = synctex_points(synctex_gz)
    texts = {path: read_tex(path) for path in project_tex_files(paper_root, fls)}
    kinds = note_kinds(texts.values())
    if not kinds:
        return []
    macros = project_macros(paper_root, fls)
    use = note_use_re(kinds)

    area = layout(paper_root, pdf_name) or {"left": 72, "top": 72, "bottom": 720}  # (1in margins until a build says)

    def at(path, line):
        """The line's points in the text area, without columns: the page frame, header, and
        line numbers are shipped out while some line is current; the left-margin glue repeats on
        each line of a paragraph."""
        found = [p for p in points.get((path, line), [])
                 if p[1] >= area["left"] - 0.5 and area["top"] - 0.5 <= p[2] <= area["bottom"] + 0.5]
        column = collections.Counter((p[0], p[1]) for p in found)
        return [p for p in found if column[p[0], p[1]] < 5] or None

    def prose(tex, keep):
        """Typeset words of tex without notes, cut at its last (keep="end") or first opaque part."""
        while m := use.search(tex):
            tex = tex[:m.start()] + tex[brace_group(tex, m.end())[1]:]
        tex = OPAQUE_RE.sub(" \ue000 ", re.sub(r"\\label\{[^}]*\}|\x7f", "", tex))
        words = " ".join(tex_to_text(tex, macros, typeset=True).split())
        return words.rsplit("\ue000", 1)[-1].strip() if keep == "end" else words.split("\ue000", 1)[0].strip()

    found = []
    for path, text in texts.items():
        with open(path, errors="replace") as f:
            raw = f.read().split("\n")
        # A commented-out line is not a paragraph break (\x7f keeps it from reading as blank), and
        # notes in \iffalse blocks are not typeset.
        text = "\n".join("\x7f" if r.lstrip().startswith("%") else t for r, t in zip(raw, text.split("\n")))
        text = re.sub(r"\\iffalse\b.*?\\fi\b", lambda m: re.sub(r"[^\n]", " ", m.group()), text, flags=re.S)
        for m in use.finditer(text):
            body, end = brace_group(text, m.end())
            line = text.count("\n", 0, m.start()) + 1
            head = text[max(0, m.start() - 400):m.start()]
            cut = [b for b in PROSE_BREAK_RE.finditer(head)][-1:]
            head = head[cut[0].end() if cut else 0:]
            if cut and re.fullmatch(r"\\end\{(?:figure|table)\*?\}", cut[0].group()) and not prose(head, "end"):
                # a note right after a float: anchor it to the end of the float's caption
                float_end = m.start() - len(head)
                begin = max(text.rfind("\\begin{figure", 0, float_end), text.rfind("\\begin{table", 0, float_end))
                caption = text.rfind("\\caption{", max(begin, 0), float_end)
                if begin >= 0 and caption >= 0:
                    head = brace_group(text, caption + len("\\caption{") - 1)[0]
            tail = text[end:end + 400]
            tail = tail[:next((b.start() for b in PROSE_BREAK_RE.finditer(tail)), len(tail))]
            before, after = " ".join(prose(head, "end").split()[-6:]), " ".join(prose(tail, "start").split()[:6])
            # The note's line, else the nearest line of its paragraph that typeset something, else
            # ("near") any line close by: a caption's words are tagged with a line after it.
            last = text.count("\n", 0, end) + 1
            where, side = at(path, line), "line"
            for start, step, name in ((line - 1, -1, "before"), (last + 1, 1, "after")):
                n = start
                while not where and 0 < n <= len(raw) and raw[n - 1].strip() and abs(n - line) <= 8:
                    where, side = at(path, n), name
                    n += step
            for n in sorted(range(max(1, line - 30), last + 31), key=lambda n: (abs(n - line), n < line)):
                if where:
                    break
                where, side = at(path, n), "near"
            if not where:
                continue
            found.append({"author": kinds[m.group(1)], "text": " ".join(tex_to_text(body, macros).split())[:600],
                          "file": os.path.relpath(path, paper_root), "line": line,
                          "points": [list(p) for p in dict.fromkeys(where)], "side": side,
                          "before": before, "after": after})
    return found


def open_source(paper_root, query):
    """Open a project file at a line in VS Code (build-error links in the viewer)."""
    try:
        src, line = os.path.realpath(os.path.join(paper_root, query["file"][0])), int(query["line"][0])
    except (KeyError, ValueError):
        return 400, "expected file, line"
    if not src.startswith(os.path.realpath(paper_root) + os.sep) or not os.path.isfile(src):
        return 404, "not a file in the paper"
    return open_in_vscode(src, line, paper_root)


def pdf_pages_text(pdf):
    """Plain text of each page (pdftotext), or [] without poppler."""
    try:
        out = subprocess.run(["pdftotext", "-q", pdf, "-"], capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return out.split("\f")


def find_bib_entry(paper_root, key):
    """(relative .bib path, line) of @type{key, in the paper's .bib files, or None."""
    pat = re.compile(r"@\w+\s*\{\s*%s\s*," % re.escape(key))
    for bib in sorted(glob.glob(os.path.join(paper_root, "*.bib")) + glob.glob(os.path.join(paper_root, "*", "*.bib"))):
        try:
            with open(bib, errors="replace") as f:
                for n, line in enumerate(f, 1):
                    if pat.search(line):
                        return os.path.relpath(bib, paper_root), n
        except OSError:
            pass
    return None


def root_flag(text, name):
    """The last uncommented \\<name>true / \\<name>false in the root file: True, False, or None."""
    found = re.findall(r"\\%s(true|false)\b" % name, text)
    return found[-1] == "true" if found else None


GRAPHIC_EXTS = (".pdf", ".png", ".jpg", ".jpeg", ".eps", ".svg")
INCLUDE_RE = re.compile(r"\\(includegraphics|includesvg)\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}|\\(input|include)\s*\{([^}]*)\}")


def project_inputs(paper_root, fls):
    """Project files the build read (relative paths, not generated ones in build/)."""
    try:
        with open(fls) as f:
            paths = [os.path.normpath(l[6:].strip()) for l in f if l.startswith("INPUT ")]
    except OSError:
        return []
    root = os.path.join(paper_root, "")
    found = []
    for p in dict.fromkeys(paths):
        full = os.path.join(paper_root, p)
        if full.startswith(root) and not full.startswith(os.path.join(paper_root, BUILD, "")) and os.path.isfile(full):
            found.append(os.path.relpath(full, paper_root))
    return found


def include_sites(paper_root, pdf_name):
    """Where each included graphic or \\input file is included: {relative path: (tex file, line)}."""
    fls = job_file(paper_root, pdf_name, ".fls")
    inputs = project_inputs(paper_root, fls)
    stems = {os.path.splitext(p)[0]: p for p in inputs}
    sites = {}
    for path in project_tex_files(paper_root, fls):
        text = read_tex(path)
        for m in INCLUDE_RE.finditer(text):
            arg = os.path.normpath((m.group(2) or m.group(4)).strip())
            arg = os.path.splitext(arg)[0] if arg.lower().endswith(GRAPHIC_EXTS + (".tex",)) else arg
            # The argument may be relative to a \graphicspath directory: match it as a path suffix.
            target = stems.get(arg) or next((p for s_, p in stems.items() if s_.endswith("/" + arg)), None)
            if target and target not in sites:
                sites[target] = (os.path.relpath(path, paper_root), text.count("\n", 0, m.start()) + 1)
    return sites


def file_hash(path, cache={}):
    st = os.stat(path)
    key = (path, st.st_mtime_ns, st.st_size)
    if key not in cache:
        with open(path, "rb") as f:
            cache[key] = hashlib.sha1(f.read()).hexdigest()[:16]
    return cache[key]


def graphics(paper_root, pdf_name):
    """The graphics the build included: [{file, hash, dirty, src, line}] (src:line is the
    \\includegraphics; dirty: changed since HEAD, or untracked). The viewer compares hashes across
    rebuilds to mark figures whose files changed, or marks the dirty ones against HEAD."""
    git = ["git", "-C", paper_root]
    dirty = set(subprocess.run(git + ["diff", "--name-only", "--relative", "HEAD"], capture_output=True, text=True).stdout.splitlines())
    dirty |= set(subprocess.run(git + ["ls-files", "-o", "--exclude-standard"], capture_output=True, text=True).stdout.splitlines())
    found = []
    for path, (src, line) in include_sites(paper_root, pdf_name).items():
        if path.lower().endswith(GRAPHIC_EXTS):
            try:
                found.append({"file": path, "hash": file_hash(os.path.join(paper_root, path)), "dirty": path in dirty,
                              "src": src, "line": line})
            except OSError:
                pass
    return found


def git_times(paper_root):
    """When each file last changed: {relative path: unix time}, from git history, or the file's
    mtime when it has uncommitted edits or is untracked (checkouts reset mtimes, commits don't)."""
    git = ["git", "-C", paper_root]
    times = {}
    log = subprocess.run(git + ["log", "-n", "3000", "--format=@%ct", "--name-only", "--no-renames", "--relative"],
                         capture_output=True, text=True).stdout
    when = 0
    for line in log.splitlines():
        if line.startswith("@"):
            when = int(line[1:])
        elif line and line not in times:
            times[line] = when
    dirty = subprocess.run(git + ["ls-files", "-m", "-o", "--exclude-standard"], capture_output=True, text=True).stdout.split("\n")
    for path in filter(None, dirty):
        try:
            times[path] = os.path.getmtime(os.path.join(paper_root, path))
        except OSError:
            times.pop(path, None)  # deleted
    return times, set(dirty)


SCRIPT_EXTS = (".py", ".r", ".jl", ".m", ".ipynb")
DATA_RE = re.compile(r"""["']([^"'\s{}]+\.(?:json|jsonl|csv|tsv|parquet|npz|npy|pkl|pickle|yaml|yml|txt))["']""")


def stale_outputs(paper_root, pdf_name):
    """Generated figures and tables that are older than the script that makes them or its data:
    [{output, source, lag (s), dirty (the newer source has uncommitted edits), site (tex, line)}].

    A script makes an output if it mentions the output's name (e.g. save_figure(fig, "name")) or
    the output's name starts with the script's name. Data files are the string literals in the
    script that name existing data files.
    """
    sites = include_sites(paper_root, pdf_name)
    outputs = [p for p in sites if p.lower().endswith(GRAPHIC_EXTS) or p.startswith("tables" + os.sep)]
    if not outputs:
        return []
    skip = {".git", ".venv", "venv", "build", BUILD.split(os.sep)[0], "node_modules", "archive", "__pycache__"}
    scripts = {}
    for d, dirs, files in os.walk(paper_root):
        dirs[:] = [x for x in dirs if x not in skip and not x.startswith(".")]
        for f in files:
            if f.lower().endswith(SCRIPT_EXTS):
                path = os.path.join(d, f)
                try:
                    with open(path, errors="replace") as fh:
                        scripts[os.path.relpath(path, paper_root)] = fh.read()
                except OSError:
                    pass
    if not scripts:
        return []
    times, dirty = git_times(paper_root)
    data_files = {}
    for d, dirs, files in os.walk(paper_root):
        dirs[:] = [x for x in dirs if x not in skip and not x.startswith(".")]
        for f in files:
            data_files.setdefault(f, os.path.relpath(os.path.join(d, f), paper_root))
    stale = []
    for out in outputs:
        stem = os.path.splitext(os.path.basename(out))[0]
        mention = re.compile(r"(?<![\w-])%s(?![\w-])" % re.escape(stem))
        makers = [s_ for s_, text in scripts.items()
                  if mention.search(text) or stem.startswith(os.path.splitext(os.path.basename(s_))[0] + "_")]
        if not makers or out not in times:
            continue
        newest = None
        for script in makers:
            sources = [script] + [data_files[os.path.basename(lit)] for lit in DATA_RE.findall(scripts[script])
                                  if os.path.basename(lit) in data_files]
            for src in sources:
                if src in times and times[src] > times[out] + 120 and (not newest or times[src] > times[newest]):
                    newest = src
        if newest:
            stale.append({"output": out, "source": newest, "lag": times[newest] - times[out],
                          "dirty": newest in dirty, "site": sites[out]})
    return stale


def parse_bib(path):
    """Entries of a .bib file: [{key, type, line, fields: {name: value}}] (a forgiving parser)."""
    try:
        with open(path, errors="replace") as f:
            text = f.read()
    except OSError:
        return []
    entries = []
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", text):
        if m.group(1).lower() in ("comment", "string", "preamble"):
            continue
        body, _ = brace_group(text, text.index("{", m.start()))
        fields, i = {}, body.index(",") + 1 if "," in body else len(body)
        for fm in re.finditer(r"(\w+)\s*=\s*", body[i:]):
            j = i + fm.end()
            if j >= len(body):
                break
            if body[j] == "{":
                value, _ = brace_group(body, j)
            elif body[j] == '"':
                k = body.find('"', j + 1)
                value = body[j + 1:k]
            else:
                value = re.match(r"[^,\s}]*", body[j:]).group(0)
            fields.setdefault(fm.group(1).lower(), value)
        entries.append({"key": m.group(2), "type": m.group(1).lower(), "line": text.count("\n", 0, m.start()) + 1,
                        "fields": fields})
    return entries


VENUES = {
    "NeurIPS": r"neurips|nips\b|neural information processing",
    "ICLR": r"iclr|international conference on learning representations",
    "ICML": r"icml|international conference on machine learning",
    "CVPR": r"cvpr|computer vision and pattern recognition",
    "ICCV": r"iccv|international conference on computer vision",
    "ECCV": r"eccv|european conference on computer vision",
    "ACL": r"^acl\b|annual meeting of the association for computational linguistics",
    "EMNLP": r"emnlp|empirical methods in natural language processing",
    "NAACL": r"naacl|north american chapter",
    "TMLR": r"tmlr|transactions on machine learning research",
    "COLM": r"colm|conference on language modeling",
    "AAAI": r"aaai",
    "TPAMI": r"pami|pattern analysis and machine intelligence",
}
ORDINALS = r"\b(?:\w+-)?(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth|\w+teenth|\w+tieth|\d+(?:st|nd|rd|th))\b"


def bib_consistency(paper_root, pdf_name):
    """Cited entries that are the same paper under two keys, and venues written several ways:
    (duplicates, venues), lists of check items."""
    try:
        with open(job_file(paper_root, pdf_name, ".aux"), errors="replace") as f:
            cited = {k.strip() for group in re.findall(r"\\citation\{([^}]*)\}", f.read()) for k in group.split(",")}
        with open(job_file(paper_root, pdf_name, ".blg"), errors="replace") as f:
            bibs = re.findall(r"^Database file #\d+: (.+)$", f.read(), re.M)
    except OSError:
        return [], []
    entries = []
    for bib in dict.fromkeys(bibs):
        path = os.path.join(paper_root, bib.strip())
        entries += [{**e, "file": os.path.relpath(path, paper_root)} for e in parse_bib(path) if e["key"] in cited]
    plain = lambda s_: " ".join(re.sub(r"\\[a-zA-Z]+|[{}]", "", s_).split())
    groups = {}
    for e in entries:
        f = e["fields"]
        title = re.sub(r"[^a-z0-9]", "", plain(f.get("title", "")).lower())
        arxiv = re.search(r"(\d{4}\.\d{4,5})", " ".join(f.get(k, "") for k in ("eprint", "journal", "url", "note", "volume")))
        for ident in filter(None, [title and len(title) > 15 and "t:" + title, arxiv and "a:" + arxiv.group(1)]):
            groups.setdefault(ident, {})[e["key"]] = e
    duplicates, seen = [], set()
    for group in groups.values():
        keys = tuple(sorted(group))
        if len(keys) > 1 and keys not in seen:
            seen.add(keys)
            for k in keys:
                e = group[k]
                duplicates.append({"text": f"same paper under keys {', '.join(keys)}: {plain(e['fields'].get('title', ''))[:70]}",
                                   "file": e["file"], "line": e["line"]})
    forms = {}
    for e in entries:
        venue = plain(e["fields"].get("booktitle") or e["fields"].get("journal") or "")
        name = next((n for n, pat in VENUES.items() if re.search(pat, venue, re.I)), None)
        if not name:
            continue
        norm = re.sub(r"\s+", " ", re.sub(r"\b(?:19|20)\d\d\b|%s|\bproceedings of( the)?\b|\b(?:the|ieee|cvf|acm|pmlr)\b|[^\w\s]" % ORDINALS, " ",
                                          venue, flags=re.I)).strip().lower()
        forms.setdefault(name, {}).setdefault(norm, []).append(e)
    venues = []
    for name, variants in forms.items():
        if len(variants) > 1:
            for norm, es in sorted(variants.items(), key=lambda kv: -len(kv[1])):
                e = es[0]
                venues.append({"text": f"{name} as \"{(e['fields'].get('booktitle') or e['fields'].get('journal')).strip()}\""
                                       f" ({len(es)} entr{'y' if len(es) == 1 else 'ies'}, e.g. {e['key']})",
                               "file": e["file"], "line": e["line"]})
    return duplicates, venues


REF_WORDS = {"figure": ["Figure", "Fig.", "figure", "Figs."], "table": ["Table", "Tab.", "table"],
             "section": ["Section", "Sec.", "section", "§"], "appendix": ["Appendix", "App.", "appendix"],
             "equation": ["Equation", "Eq.", "Eqn.", "equation"]}


DEFINITION_RE = re.compile(r"\\(?:(?:re|provide)?newcommand|DeclareRobustCommand|newenvironment|def|let)\b")


def prose_lines(paper_root, pdf_name):
    """(file, line, text) for the prose of the project's .tex files: comments, notes, math, and the
    arguments of labels, references, citations and includes removed; project macros expanded."""
    fls = job_file(paper_root, pdf_name, ".fls")
    macros = project_macros(paper_root, fls)
    texts = {path: read_tex(path) for path in project_tex_files(paper_root, fls)}
    kinds = note_kinds(texts.values())
    blank = lambda m: re.sub(r"[^\n]", " ", m.group(0))
    out = []
    for path, text in texts.items():
        begin = text.find("\\begin{document}")
        if begin > 0:
            text = "\n" * text.count("\n", 0, begin) + text[begin:]
        for m in reversed(list(note_use_re(kinds).finditer(text)) if kinds else []):
            end = brace_group(text, m.end())[1]
            text = text[:m.start()] + blank(re.match(r"[\s\S]*", text[m.start():end])) + text[end:]
        text = re.sub(r"\\(?:[a-zA-Z]*ref|label|cite[a-z]*|include\w*|input|url|href|begin|end|usepackage|graphicspath|"
                      r"hypersetup|bibliography\w*|newcommand|renewcommand|def|vspace|hspace|setlength|addtolength)\*?"
                      r"\s*(?:\[[^\]]*\]\s*)*\{[^{}]*\}", blank, text)
        text = re.sub(r"\$\\Delta\$", "Δ", text)  # keep names like $\Delta$Fusion
        text = re.sub(r"\$\$.*?\$\$|\$[^$]*\$|\\\(.*?\\\)|\\\[.*?\\\]", blank, text, flags=re.S)
        rel = os.path.relpath(path, paper_root)
        for n, line in enumerate(text.split("\n"), 1):
            if line.strip() and not DEFINITION_RE.search(line):
                out.append((rel, n, tex_to_text(re.sub(r"\\\\|&", " ", line), macros, typeset=True).replace("$", "")))
    return out


def lint(paper_root, pdf_name, pages, in_bib):
    """Consistency lints: (terms, refs, typos) check items."""
    lines = prose_lines(paper_root, pdf_name)
    sites = include_sites(paper_root, pdf_name)
    # Text inside the included figures (e.g. labels in a diagram), located at their \includegraphics.
    for path, (src, line) in sites.items():
        if path.lower().endswith(".pdf"):
            text = " ".join(pdf_pages_text(os.path.join(paper_root, path)))
            if text.strip():
                lines.append((src, line, text, path))
    key = lambda w: w.lower().replace("-", "").replace("δ", "delta").replace("∆", "delta")
    word_re = re.compile(r"[A-Za-zΔ∆][A-Za-zΔ∆0-9]*(?:-[A-Za-zΔ∆0-9]+)*")
    forms, spaced, hyphen_next, surface = {}, {}, {}, {}
    for entry in lines:
        words = word_re.findall(entry[2])
        for i, w in enumerate(words):
            nxt = words[i + 1].lower() if i + 1 < len(words) else None
            if len(key(w)) >= 5 and len(w) >= 3:
                v = w.lower().replace("∆", "δ")
                forms.setdefault(key(w), {}).setdefault(v, []).append(entry)
                surface.setdefault(v, collections.Counter())[w.replace("∆", "Δ")] += 1
                if "-" in w:
                    hyphen_next.setdefault(key(w), set()).add(nxt)
            if nxt:
                spaced.setdefault(key(w + nxt), []).append((f"{w} {nxt}".lower(), words[i + 2].lower() if i + 2 < len(words) else None, entry))
                surface.setdefault(f"{w} {nxt}".lower(), collections.Counter())[f"{w} {words[i + 1]}"] += 1
    shown = lambda v: surface[v].most_common(1)[0][0]  # as most often written, not lowercased
    terms = []
    for k, variants in forms.items():
        # "encoder free models" next to "encoder-free models" (not "trained from scratch" next to
        # "from-scratch encoder": hyphenating a modifier before its noun is right).
        for v, nxt, entry in spaced.get(k, []):
            if nxt and nxt in hyphen_next.get(k, ()):
                variants.setdefault(v, []).append(entry)
        if len(variants) < 2:  # (case is ignored, so the variants differ in hyphens, spaces, or Δ/Delta)
            continue
        ranked = sorted(variants.items(), key=lambda kv: -len(kv[1]))
        top, top_entries = ranked[0]
        for v, entries in ranked[1:]:
            figs = sorted({e[3] for e in entries if len(e) > 3})
            e = next((e for e in entries if len(e) <= 3), entries[0])
            terms.append({"text": f"\"{shown(v)}\" ×{len(entries)} vs \"{shown(top)}\" ×{len(top_entries)}"
                                  + (f" (incl. in {', '.join(os.path.basename(f) for f in figs)})" if figs else ""),
                          "file": e[0], "line": e[1]})
    # Reference styles in the rendered text (what cleveref and friends actually printed).
    refs = []
    for kind, words in REF_WORDS.items():
        seen = {}
        for n, text in enumerate(pages, 1):
            if in_bib(n):
                continue
            flat = " ".join(text.split())
            for w in words:
                for m in re.finditer(r"(?<![\w.])%s\s?(?:[A-Z]\.)?\d+(?!\s*:)" % re.escape(w), flat):
                    if w[0].islower() and m.start() >= 2 and flat[m.start() - 2] in ".?!":
                        continue
                    seen.setdefault(w, []).append(n)
        if len(seen) > 1:
            for w, ps in sorted(seen.items(), key=lambda kv: -len(kv[1])):
                refs.append({"text": f"{kind} references as \"{w} N\" ×{len(ps)} (pages {', '.join(map(str, sorted(set(ps))[:6]))})",
                             "page": ps[0]})
    # Doubled words and references that can break across lines.
    typos = []
    for path in project_tex_files(paper_root, job_file(paper_root, pdf_name, ".fls")):
        text = read_tex(path)
        rel = os.path.relpath(path, paper_root)
        for m in re.finditer(r"(?<![\\\w])([A-Za-z]{2,})\s+\1\b", text, re.I):
            if m.group(1).lower() not in ("that", "had", "is"):
                typos.append({"text": f"doubled word: \"{m.group(0).split()[0]} {m.group(0).split()[-1]}\"", "file": rel,
                              "line": text.count("\n", 0, m.start()) + 1})
        for m in re.finditer(r"\b(Figures?|Figs?\.|Tables?|Tab\.|Sections?|Sec\.|Appendix|App\.|Eq\.|Equation|Algorithm|Line)"
                             r"(\s+)\\(ref|eqref)\{", text):
            typos.append({"text": f"\"{m.group(1)} \\{m.group(3)}\": use ~ so the number can't wrap to the next line",
                          "file": rel, "line": text.count("\n", 0, m.start()) + 1})
    return terms, refs, typos


def checks(server, pdf_name):
    """Pre-submission checks: [{title, level (ok|info|warn|fail), detail, items: [{text, file?, line?, page?}]}].

    Looks at the notes-free PDF when there is one (what would be submitted), else the live one.
    The viewer adds the page-limit result itself.
    """
    root = server.paper_root
    doc = pdf_name[:-4]
    clean = os.path.isfile(pdf_path(root, f"{doc}-clean.pdf"))
    pdf = pdf_path(root, f"{doc}-clean.pdf") if clean else pdf_path(root, pdf_name)
    root_tex = read_tex(os.path.join(root, f"{doc}.tex"))
    pages = pdf_pages_text(pdf)
    f = clean_file if clean else job_file
    span = bib_span(root, (f(root, pdf_name, ".synctex.gz"), f(root, pdf_name, ".bbl"), f(root, pdf_name, ".pdf")))
    in_bib = (lambda p: span[0]["page"] <= p <= (span[1] or span[0])["page"]) if span else (lambda p: False)
    results = []

    def add(title, level, detail, items=()):
        results.append({"title": title, "level": level, "detail": detail, "items": list(items)[:50]})

    def hits(pattern, skip_bib=False, flags=re.I):
        """[{text, page}] for regex matches in the page text, with some context."""
        found = []
        for n, text in enumerate(pages, 1):
            if skip_bib and in_bib(n):
                continue
            flat = " ".join(text.split())
            for m in re.finditer(pattern, flat, flags):
                found.append({"text": "…" + flat[max(0, m.start() - 40):m.end() + 40] + "…", "page": n})
        return found

    # Anonymity, for documents that print an anonymous author block (as blind-review styles do).
    final = re.search(r"^\s*\\iclrfinalcopy\b", root_tex, re.M)
    if pages and re.search(r"\banonymous\b", pages[0], re.I):
        names = set()  # people named in the note-macro comments, e.g. "\eb ... % Ellis Brown"
        for path in project_tex_files(root, job_file(root, pdf_name, ".fls")):
            with open(path, errors="replace") as fh:
                names |= {m.strip() for m in re.findall(r"\\todotxt.*%\s*([A-Z][a-z]+(?: [A-Z][a-z]+)+)\s*$", fh.read(), re.M)}
        git_name = subprocess.run(["git", "-C", root, "config", "user.name"], capture_output=True, text=True).stdout.strip()
        if git_name:
            names.add(git_name)
        # Affiliations and other terms that identify the authors: LATEX_LIVE_ANON_TERMS, a regular
        # expression (alternatives separated by |), e.g. r"\bNYU\b|New York University".
        anon_terms = os.environ.get("LATEX_LIVE_ANON_TERMS", "").strip()
        ident = [r"\b%s\b" % re.escape(n) for n in sorted(names)] + ([f"(?:{anon_terms})"] if anon_terms else []) + [
            r"github\.com/\S+", r"huggingface\.co/\S+", r"\bwandb\b"]
        anon = hits("|".join(ident), skip_bib=True, flags=0)
        anon += hits(r"\b(?:our|my) (?:prior|previous|earlier|recent) (?:work|paper|study)|\bwe (?:previously|earlier) (?:showed|proposed|introduced|found)")
        info = subprocess.run(["pdfinfo", pdf], capture_output=True, text=True).stdout
        meta_author = re.search(r"^Author:[ \t]*(\S.*)$", info, re.M)
        problems = [{"text": f"PDF metadata names an author: {meta_author.group(1).strip()}"}] if meta_author else []
        problems += [{**h, "text": "acknowledgments: " + h["text"]} for h in hits(r"\bAcknowledge?ments?\b", flags=0)]
        add("Anonymity", "fail" if problems else "warn" if anon else "ok",
            f"{len(problems)} problem(s), {len(anon)} identifying phrase(s) outside the references" if problems or anon
            else "anonymous author block; no names, affiliations, or links to identifying pages found",
            problems + anon)
    else:
        add("Anonymity", "info", "not checked: page 1 has no anonymous author block"
            + (" (\\iclrfinalcopy is on)" if final else ""),
            [{"text": "\\iclrfinalcopy prints the author names", "file": f"{doc}.tex",
              "line": root_tex.count("\n", 0, final.start()) + 1}] if final else [])

    # Notes and draft switches.
    note_list = notes(root, pdf_name)
    shown = root_flag(root_tex, "showtodos")
    toc = [m for m in re.finditer(r"\\(\w*showtoc)true\b", root_tex)]
    items = []
    if shown:
        items.append({"text": "\\showtodostrue: notes are rendered (use \\showtodosfalse to submit)", "file": f"{doc}.tex",
                      "line": root_tex.count("\n", 0, root_tex.rfind("\\showtodostrue")) + 1})
    if toc:
        items.append({"text": f"\\{toc[-1].group(1)}true: the draft table of contents is on", "file": f"{doc}.tex",
                      "line": root_tex.count("\n", 0, toc[-1].start()) + 1})
    add("Draft notes and switches", "warn" if items else "info" if note_list else "ok",
        f"{len(note_list)} inline note(s) in the source" + ("; switch them off before submitting" if shown else "; hidden"),
        items)

    # Undefined references and citations, placeholders.
    build = read_json(job_file(root, pdf_name, ".status.json")) or {}
    undefined = build.get("undefined") or []
    marks = hits(r"\?\?")
    add("Undefined references and citations", "fail" if undefined or marks else "ok",
        f"{len(undefined)} undefined in the log, {len(marks)} '??' in the PDF" if undefined or marks else "none",
        [{"text": f"{u['kind']} '{u['key']}'", "file": u["file"], "line": u["line"]} for u in undefined] + marks)
    placeholders = hits(r"\b(?:TODO|TBD|TBA|FIXME|XXX)\b|lorem ipsum|\[citation needed\]", flags=0)
    add("Placeholder text", "warn" if placeholders else "ok",
        f"{len(placeholders)} in the {'notes-free ' if clean else ''}PDF" if placeholders else "none", placeholders)

    # Fonts.
    fonts = subprocess.run(["pdffonts", pdf], capture_output=True, text=True).stdout.splitlines()[2:]
    type3 = []
    if any(" Type 3 " in l for l in fonts):  # which pages (usually matplotlib figures)
        for n in range(1, len(pages) + 1):
            out = subprocess.run(["pdffonts", "-f", str(n), "-l", str(n), pdf], capture_output=True, text=True).stdout
            if " Type 3 " in out:
                type3.append(n)
    unembedded = [l.split()[0] for l in fonts if len(l.split()) >= 5 and re.search(r"\s(no)\s+(yes|no)\s+(yes|no)\s+\d+\s+\d+\s*$", l)]
    add("Fonts", "fail" if unembedded else "warn" if type3 else "ok",
        f"{len(fonts)} fonts; {len(unembedded)} not embedded; Type 3 fonts on {len(type3)} page(s)" if unembedded or type3
        else f"all {len(fonts)} fonts embedded; no Type 3 fonts",
        [{"text": f"not embedded: {n}"} for n in unembedded] +
        [{"text": "Type 3 font (often a matplotlib figure; some venues ask for Type 1/TrueType only)", "page": n} for n in type3])

    # Images.
    low = []
    for l in subprocess.run(["pdfimages", "-list", pdf], capture_output=True, text=True).stdout.splitlines()[2:]:
        c = l.split()
        if len(c) >= 14 and c[2] in ("image", "stencil") and c[12].isdigit() and int(c[3]) * int(c[4]) > 10000:
            ppi = min(int(c[12]), int(c[13]))
            if ppi < 150:
                low.append({"text": f"{ppi} ppi raster image ({c[3]}×{c[4]} px)", "page": int(c[0])})
    add("Raster image resolution", "warn" if low else "ok",
        f"{len(low)} image(s) below 150 ppi at print size" if low else "no raster images below 150 ppi", low)

    # File size.
    published = pdf_path(root, pdf_name)
    size = os.path.getsize(published) if os.path.isfile(published) else 0
    add("File size", "fail" if size > 50e6 else "warn" if size > 40e6 else "ok",
        f"{size / 1e6:.1f} MB (OpenReview's limit is typically 50 MB)")

    # Bibliography.
    try:
        with open(job_file(root, pdf_name, ".blg"), errors="replace") as fh:
            warnings = [l.strip()[9:] for l in fh if l.startswith("Warning--")]
    except OSError:
        warnings = []
    bib_items = []
    for w in dict.fromkeys(warnings):
        key = re.search(r" in (\S+)$", w)
        loc = key and find_bib_entry(root, key.group(1))
        bib_items.append({"text": w, **({"file": loc[0], "line": loc[1]} if loc else {})})
    add("Bibliography entries", "warn" if bib_items else "ok",
        f"{len(bib_items)} bibtex warning(s), e.g. missing fields" if bib_items else "no bibtex warnings", bib_items)

    # Generated figures and tables older than their scripts or data.
    ago = lambda t: f"{t / 86400:.0f} d" if t >= 86400 else f"{t / 3600:.0f} h" if t >= 3600 else f"{t / 60:.0f} min"
    run = "uv run python" if os.path.isfile(os.path.join(root, "pyproject.toml")) else "python"
    stale = sorted(stale_outputs(root, pdf_name), key=lambda x: (not x["dirty"], x["output"]))
    add("Generated figures and tables", "warn" if any(x["dirty"] for x in stale) else "info" if stale else "ok",
        f"{len(stale)} may be stale (a script or data file changed after them)" if stale
        else "no figure or table is older than the script or data that makes it",
        [{"text": f"{x['output']}: {x['source']} changed {ago(x['lag'])} later"
                  + (" (uncommitted)" if x["dirty"] else "")
                  + (f"; rerun {run} {x['source']}" if x["source"].endswith(".py") else ""),
          "file": x["site"][0], "line": x["site"][1], "open": x["source"]} for x in stale])

    # Consistency lints.
    terms, refs, typos = lint(root, pdf_name, pages, in_bib)
    add("Consistent terms", "info" if terms or refs else "ok",
        f"{len(terms)} term(s) spelled more than one way, {len(refs)} reference style(s) mixed" if terms or refs
        else "hyphenation, Δ/Delta names, and Figure/Section references are consistent", terms + refs)
    add("Typos", "warn" if typos else "ok",
        f"{len(typos)} doubled word(s) or breakable references" if typos else "no doubled words; references use ~",
        typos)
    duplicates, venues = bib_consistency(root, pdf_name)
    add("Bibliography consistency", "warn" if duplicates else "info" if venues else "ok",
        f"{len(duplicates)} entr(ies) cited twice under different keys; {len(venues)} venue spelling(s) differ"
        if duplicates or venues else "no duplicate entries; each venue is written one way", duplicates + venues)

    # Overfull boxes.
    sys.path.insert(0, HERE)
    import latex_report
    try:
        with open(job_file(root, pdf_name, ".log"), errors="replace") as fh:
            cwd = os.getcwd()
            os.chdir(root)  # overfull_boxes checks project paths relative to the paper root
            try:
                boxes = latex_report.overfull_boxes(fh.read())
            finally:
                os.chdir(cwd)
    except OSError:
        boxes = []
    add("Overfull lines", "info" if boxes else "ok", f"{len(boxes)} line(s) stick into the margin" if boxes else "none",
        [{"text": m, "file": p, "line": n} for p, n, m in boxes])
    return results


def open_in_vscode(src, line, paper_root):
    code = shutil.which("code")
    if code and "VSCODE_IPC_HOOK_CLI" in os.environ:
        subprocess.Popen([code, "-g", f"{src}:{line}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return 200, f"{os.path.relpath(src, paper_root)}:{line}"


def synctex_edit(paper_root, query):
    """Inverse search: PDF position -> source file:line, opened in VS Code. Returns (status, text).

    query: file (published PDF name), page, and x, y in PDF points from the page's top-left;
    clean=1 for a position in the notes-free PDF.
    """
    try:
        name = query["file"][0]
        page, x, y = int(query["page"][0]), float(query["x"][0]), float(query["y"][0])
    except (KeyError, ValueError):
        return 400, "expected file, page, x, y"
    if not name.endswith(".pdf") or os.path.basename(name) != name:
        return 400, "bad file"
    job = clean_file if query.get("clean") == ["1"] else job_file
    pdf = job(paper_root, name, ".pdf")  # the job whose .synctex.gz we have
    if not os.path.isfile(pdf[:-4] + ".synctex.gz"):  # (the notes-free PDF itself moves to build/)
        return 404, f"no {os.path.relpath(pdf, paper_root)} yet"
    result = next((r for r in run_synctex(paper_root, "edit", "-o", f"{page}:{x:.2f}:{y:.2f}:{pdf}") if "Input" in r), {})
    if "Input" not in result:
        missing = synctex_unavailable()
        return (503, missing) if missing else (404, "no source location here")
    src, line = os.path.normpath(result["Input"]), max(1, int(result["Line"]))
    if src.startswith(os.path.join(paper_root, BUILD) + os.sep):  # generated, e.g. the table of contents
        return 404, f"generated text ({os.path.basename(src)}); no source line"
    return open_in_vscode(src, line, paper_root)


def commits(paper_root):
    """Recent commits for the viewer's compare menu."""
    out = subprocess.run(["git", "-C", paper_root, "log", "-n", "40", "--date=format:%b %d %H:%M",
                          "--format=%h%x09%ad%x09%an%x09%s"], capture_output=True, text=True).stdout
    dirty = bool(subprocess.run(["git", "-C", paper_root, "status", "--porcelain", "--untracked-files=no"],
                                capture_output=True, text=True).stdout.strip())
    return {"dirty": dirty, "commits": [dict(zip(("rev", "date", "author", "subject"), l.split("\t", 3)))
                                        for l in out.splitlines()]}


def start_diff(server, query):
    """/diff?rev=REV: build <doc>-diff.pdf, a latexdiff of REV against the working tree, in the background."""
    rev = query.get("rev", [""])[0]
    if not re.fullmatch(r"[\w./^~-]{1,80}", rev) or rev.startswith("-"):
        return 400, {"error": "bad rev"}
    if server.diff.get("state") == "running":
        return 409, {"error": "a diff is already building"}
    sha = subprocess.run(["git", "-C", server.paper_root, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"],
                         capture_output=True, text=True).stdout.strip()
    if not sha:
        return 404, {"error": f"unknown revision {rev}"}
    server.diff = {"rev": rev, "sha": sha[:9], "state": "running", "time": time.strftime("%H:%M:%S"), "msg": ""}
    threading.Thread(target=build_diff, args=(server, sha), daemon=True).start()
    return 200, server.diff


def build_diff(server, sha):
    """latexdiff --flatten (TeX Live image) of <doc>.tex at sha vs now, then two pdflatex passes.

    Both sides are copies of just the .tex files (old: `git archive`; new: the working
    tree), so latexdiff doesn't inline a stale <doc>.bbl from the paper root; figures,
    styles, and the bibliography are the current ones. Runs in build/diff/; the PDF is
    published to build/<doc>-diff.pdf.
    """
    root = server.paper_root
    doc = server.default_pdf[:-4]
    out = os.path.join(root, BUILD, "diff")
    old, new = os.path.join(out, "old"), os.path.join(out, "new")
    job = f"{doc}-diff"

    def run(cmd, **kw):
        return subprocess.run(cmd, capture_output=True, text=True, cwd=root, **kw)

    try:
        for d in (old, new):
            shutil.rmtree(d, ignore_errors=True)
            os.makedirs(d)
        archive = subprocess.Popen(["git", "-C", root, "archive", sha, "--", ":(glob)**/*.tex"], stdout=subprocess.PIPE)
        subprocess.run(["tar", "-x", "-C", old], stdin=archive.stdout, check=True)
        archive.wait()
        tracked = run(["git", "ls-files", "-co", "--exclude-standard", "--", ":(glob)**/*.tex"]).stdout.splitlines()
        for rel_path in tracked:
            if os.path.isfile(os.path.join(root, rel_path)):
                os.makedirs(os.path.dirname(os.path.join(new, rel_path)), exist_ok=True)
                shutil.copyfile(os.path.join(root, rel_path), os.path.join(new, rel_path))
        exec_ = container_argv(root) if image_pulled() else []  # else the host's, if it has latexdiff
        r = run([*exec_, "latexdiff", "--flatten", "--type=UNDERLINE", "--math-markup=coarse",
                 "--config", "PICTUREENV=(?:picture|DIFnomarkup|tikzpicture|NiceTabular|tabular)[\\w\\d*@]*",
                 os.path.join(old, f"{doc}.tex"), os.path.join(new, f"{doc}.tex")], timeout=300)
        if r.returncode != 0 or not r.stdout.strip():
            raise RuntimeError("latexdiff failed: " + (r.stderr.strip().splitlines() or ["no output"])[-1])
        with open(os.path.join(out, f"{job}.tex"), "w") as fh:
            fh.write(r.stdout)
        for ext in (".aux", ".bbl"):  # current references, so the diff needs no bibtex run
            if os.path.exists(job_file(root, server.default_pdf, ext)):
                shutil.copyfile(job_file(root, server.default_pdf, ext), os.path.join(out, job + ext))
        tex = [*exec_, "pdflatex", "-interaction=nonstopmode", f"-output-directory={out}", f"-jobname={job}",
               os.path.join(out, f"{job}.tex")]
        for _ in range(2):
            r = run(tex, timeout=300, env={**os.environ, "max_print_line": "10000"})
        pdf = os.path.join(out, f"{job}.pdf")
        if not os.path.isfile(pdf):
            bang = next((l for l in r.stdout.splitlines() if l.startswith("! ")), "pdflatex produced no PDF")
            raise RuntimeError(bang)
        os.replace(pdf, os.path.join(root, BUILD, f"{job}.pdf"))
        server.diff = {**server.diff, "state": "done", "time": time.strftime("%H:%M:%S")}
    except Exception as e:  # reported in the diff viewer's banner
        server.diff = {**server.diff, "state": "failed", "time": time.strftime("%H:%M:%S"), "msg": str(e)[:300]}


def head_status(server, pdf_name):
    """The baseline the viewer diffs against to mark uncommitted changes: PDFs of the committed
    document (build_head), rebuilt whenever HEAD moves. HEAD is checked every few seconds. The
    versions appear once a build has finished; None outside git, and for a document the server
    is not building."""
    root = server.paper_root
    if pdf_name != server.default_pdf:
        return None
    if time.monotonic() - server.head_checked > 3 and server.head_lock.acquire(blocking=False):
        try:
            server.head_checked = time.monotonic()
            r = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True, text=True)
            sha = r.stdout.strip() if r.returncode == 0 else None
            h = server.head
            if not sha:
                server.head = {}
            elif ((sha, pdf_name) != (h.get("sha"), h.get("pdf")) and h.get("state") != "running"
                  and os.path.isfile(job_file(root, pdf_name, ".aux"))):
                server.head = {"sha": sha, "pdf": pdf_name, "state": "running", "msg": ""}
                threading.Thread(target=build_head, args=(server, sha, pdf_name), daemon=True).start()
        finally:
            server.head_lock.release()
    h = server.head
    if h.get("pdf") != pdf_name:
        return None
    info = {"sha": h["sha"][:9], "state": h["state"], "msg": h.get("msg", "")}
    if h.get("built"):
        for key, name in (("version", "-head.pdf"), ("clean", "-head-clean.pdf")):
            path = pdf_path(root, pdf_name[:-4] + name)
            if os.path.isfile(path):
                info[key] = str(mtime(path))
    return info


def build_head(server, sha, pdf_name):
    """The document as committed at sha: build/<doc>-head.pdf, plus build/<doc>-head-clean.pdf
    without notes (when the live build has a notes-free PDF).

    Like build_diff, only the .tex files come from git (`git archive` into build/head/<doc>/src/).
    Figures, styles, fonts and the bibliography resolve to the working tree's (through TEXINPUTS,
    and links for the top-level entries with no .tex files, such as a class's font directory), so
    the diff shows text edits; the viewer marks changed figures itself. The live build's .aux/.bbl
    seed the references: two passes with notes, then one without.
    """
    root = server.paper_root
    doc = pdf_name[:-4]
    out = os.path.join(root, BUILD, "head", doc)
    src = os.path.join(out, "src")
    job = f"{doc}-head"
    sys.path.insert(0, HERE)
    import latex_report

    def tex(jobname, source):
        cmd = (f"{server.tex_exec} pdflatex -interaction=batchmode -output-directory='{out}' -jobname={jobname}"
               f" '{source}' >/dev/null 2>&1")
        subprocess.run(["sh", "-c", cmd], cwd=src, timeout=300, env={**os.environ, "TEXINPUTS": f"{src}:{root}:"})
        return os.path.join(out, f"{jobname}.pdf")

    try:
        shutil.rmtree(out, ignore_errors=True)
        os.makedirs(src)
        archive = subprocess.Popen(["git", "-C", root, "archive", sha, "--", ":(glob)**/*.tex"], stdout=subprocess.PIPE)
        subprocess.run(["tar", "-x", "-C", src], stdin=archive.stdout, check=True)
        archive.wait()
        if not os.path.isfile(os.path.join(src, f"{doc}.tex")):
            raise RuntimeError(f"{doc}.tex is not in {sha[:9]}")
        for name in os.listdir(root):  # (fonts are not looked up through TEXINPUTS)
            if name not in (".git", BUILD.split(os.sep)[0]) and not os.path.lexists(os.path.join(src, name)):
                os.symlink(os.path.join(root, name), os.path.join(src, name))
        for ext in (".aux", ".bbl"):
            if os.path.exists(job_file(root, pdf_name, ext)):
                shutil.copyfile(job_file(root, pdf_name, ext), os.path.join(out, job + ext))
        for _ in range(2):
            pdf = tex(job, doc)
        if not os.path.isfile(pdf):
            raise RuntimeError(f"pdflatex produced no PDF; see {BUILD}/head/{doc}/{job}.log")
        clean_pdf = None
        if os.path.isfile(pdf_path(root, f"{doc}-clean.pdf")):
            for ext in (".aux", ".bbl", ".out", ".toc"):
                if os.path.exists(os.path.join(out, job + ext)):
                    shutil.copyfile(os.path.join(out, job + ext), os.path.join(out, f"{job}-clean{ext}"))
            clean_pdf = tex(f"{job}-clean", f"{latex_report.NOTES_OFF}\\input{{{doc}}}")
        os.replace(pdf, os.path.join(root, BUILD, f"{job}.pdf"))
        if clean_pdf and os.path.isfile(clean_pdf):
            os.replace(clean_pdf, os.path.join(root, BUILD, f"{job}-clean.pdf"))
        state = {"state": "done", "built": sha}
    except Exception as e:  # reported in the viewer's tooltip; the previous baseline stays
        state = {"state": "failed", "msg": str(e)[:300]}
    if server.head.get("pdf") == pdf_name:  # (not after a switch to another document)
        server.head = {**server.head, **state}


def serve(paper_root, default_pdf, port, page_limit):
    for p in range(port, port + 10):  # a small range, for tunnels that forward only a few ports
        try:
            server = DualStackServer(("::", p), Handler)
            break
        except OSError:
            continue
    else:
        sys.exit(f"latex-live: no free port in {port}-{port + 9}")
    server.daemon_threads = True
    server.paper_root = paper_root
    server.default_pdf = default_pdf
    server.page_limit = page_limit
    server.diff = {}
    server.head, server.head_checked, server.head_lock = {}, 0, threading.Lock()
    server.tex_exec = ""  # main: the tex-exec prefix, as for latexmk
    server.image_checked, server.image_ok = 0, True
    server.latexmk, server.lock, server.stopping = None, None, False
    server.requests = queue.Queue()  # document switches, for the main thread (request_switch)
    server.token = load_token()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def open_in_editor(pdf):
    """Open the PDF in a VS Code tab once it exists (the first build may still be running)."""
    code = shutil.which("code")
    if not code or "VSCODE_IPC_HOOK_CLI" not in os.environ:
        return print("=== latex-live: not in a VS Code terminal; skipping editor tab", flush=True)
    while not os.path.isfile(pdf):
        time.sleep(1)
    subprocess.run([code, "-r", pdf], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def take_lock(doc):
    """The lock on build/<doc>-live.lock (one instance per document), or (None, the holder's port)."""
    lock = open(os.path.join(BUILD, f"{doc}-live.lock"), "a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.seek(0)
        port = lock.read().strip() or "?"
        lock.close()
        return None, port
    return lock, None


def record_port(lock, port):
    """The viewer port, in the lock file, for later instances."""
    lock.truncate(0)
    lock.write(str(port))
    lock.flush()


def set_doc(server, doc):
    """Serve doc as the document being built, and remember it as the last one (pick_doc)."""
    server.default_pdf = f"{doc}.pdf"
    server.head, server.head_checked = {}, 0
    if server.diff.get("state") != "running":
        server.diff = {}
    with open(os.path.join(server.paper_root, BUILD, "latex-live-doc"), "w") as f:
        f.write(doc)


def start_latexmk(server):
    """latexmk -pvc for the current document. Main thread only: see request_switch."""
    if server.no_build:
        return
    doc = server.default_pdf[:-4]
    env = {**server.env, "LATEX_LIVE_DOC": doc}
    if PUBLISH:
        env["LATEX_LIVE_PUBLISH"] = os.path.join(PUBLISH, server.default_pdf)  # latex_report.py
    # PR_SET_PDEATHSIG: latexmk gets SIGTERM if this process dies, however it dies.
    server.latexmk = subprocess.Popen(
        ["latexmk", "-r", os.path.join(HERE, "latexmkrc"), "-pvc", f"-jobname={doc}-live", doc],
        env=env,
        preexec_fn=lambda: ctypes.CDLL("libc.so.6").prctl(1, signal.SIGTERM),
    )


def stop_latexmk(server):
    proc, server.latexmk = server.latexmk, None
    if proc:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def request_switch(server, query):
    """/switch?doc=NAME: build another root document instead. The main thread switches (switch_doc):
    latexmk's parent-death signal fires when the thread that started it exits, so a request
    thread must not start it."""
    doc = query.get("doc", [""])[0]
    if doc not in root_docs(server.paper_root):
        return 404, {"error": f"{doc}.tex is not a root document here"}
    done, result = threading.Event(), {}
    server.requests.put((doc, result, done))
    if not done.wait(30):
        return 504, {"error": "the switch is taking a while; reload in a moment"}
    return result["status"], result["body"]


def switch_doc(server, doc):
    """Stop building the current document and build doc. Returns (status, body) for /switch."""
    if doc == server.default_pdf[:-4]:
        return 200, {"doc": doc}
    lock, other = take_lock(doc)
    if not lock:
        return 409, {"error": f"{doc}.tex is open in another latex-live instance (port {other})"}
    stop_latexmk(server)
    server.lock.close()
    server.lock = lock
    record_port(lock, server.server_address[1])
    set_doc(server, doc)
    start_latexmk(server)
    print(f"=== latex-live: now building {doc}.tex", flush=True)
    return 200, {"doc": doc}


def container_argv(paper_root):
    """Argument list that runs a tool in the TeX Live image (for subprocess, with cwd = paper root)."""
    return ["podman", "run", "--rm", "--init", "--pull=never", "--network=none", "--security-opt", "label=disable",
            "-v", f"{paper_root}:{paper_root}", "-w", os.path.realpath(paper_root), "-e", "max_print_line", TEXLIVE_IMAGE]


def container_exec(paper_root):
    """Command prefix that runs a TeX tool in the TeX Live image (see tex-exec), or on the host
    while the image is missing."""
    return f"'{os.path.join(HERE, 'tex-exec')}' {TEXLIVE_IMAGE} '{paper_root}'"


def print_urls(port):
    token = load_token()
    print(f"=== latex-live: viewer (on a remote machine, forward port {port} first): http://localhost:{port}/?t={token}", flush=True)
    if host := os.environ.get("LATEX_LIVE_HOST"):
        print(f"=== latex-live: viewer, direct: http://{host}:{port}/?t={token}", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("doc", nargs="?", help="root .tex file (with or without extension); default: the one whose"
                    " sources were edited last. The viewer switches between root files.")
    ap.add_argument("--port", type=int, default=44100)
    ap.add_argument("--page-limit", type=int, help="main-text page limit to check in the viewer (default: the venue's,"
                    " for ICLR, NeurIPS and COLM styles)")
    ap.add_argument("--build-dir", default="build", help="build directory, relative to the paper root (default: build);"
                    " any other also receives <doc>.pdf, e.g. to run beside the usual instance")
    ap.add_argument("--no-build", action="store_true", help="viewer only; don't run latexmk")
    ap.add_argument("--no-open", action="store_true", help="don't open the PDF in a VS Code editor tab")
    ap.add_argument("--host-tex", action="store_true", help="use the host's TeX installation, not the TeX Live 2024 image")
    args = ap.parse_args()
    global BUILD, PUBLISH
    BUILD = os.environ["LATEX_LIVE_BUILD"] = os.path.normpath(args.build_dir)
    PUBLISH = "" if BUILD == "build" else BUILD

    docs = root_docs(os.getcwd())
    if args.doc:
        doc = args.doc[:-4] if args.doc.endswith(".tex") else args.doc
        if not os.path.isfile(f"{doc}.tex"):
            sys.exit(f"latex-live: {doc}.tex not found in {os.getcwd()}")
    elif docs:
        doc = pick_doc(os.getcwd(), docs)
    else:
        sys.exit(f"latex-live: no .tex file with a \\documentclass in {os.getcwd()}")

    # One instance per document; the lock file records the viewer port for later instances.
    os.makedirs(BUILD, exist_ok=True)
    lock, other = take_lock(doc)
    if not lock:
        print(f"=== latex-live: already running for {doc}.tex (another terminal or window)", flush=True)
        print_urls(other)
        return
    server = serve(os.getcwd(), f"{doc}.pdf", args.port, args.page_limit)
    server.lock = lock
    record_port(lock, server.server_address[1])
    set_doc(server, doc)
    print_urls(server.server_address[1])
    others = [d for d in docs if d != doc]
    print(f"=== latex-live: building {doc}.tex" + (f"; the viewer switches to {', '.join(others)}" if others else ""),
          flush=True)
    if not args.no_open:
        threading.Thread(target=open_in_editor, args=(pdf_path(os.getcwd(), f"{doc}.pdf"),), daemon=True).start()

    server.no_build = args.no_build
    server.env = {**os.environ, "LATEX_LIVE_HOME": HERE, "max_print_line": "10000"}
    if args.no_build:
        pass
    elif args.host_tex:
        print("=== latex-live: TeX from the host", flush=True)
    else:
        server.env["LATEX_LIVE_EXEC"] = server.tex_exec = container_exec(os.path.realpath(os.getcwd()))
        print("=== latex-live: TeX Live 2024 (podman)" if image_pulled()
              else f"=== latex-live: {IMAGE_MISSING}; until then, the host's TeX runs", flush=True)
    start_latexmk(server)

    # Only signal latexmk here; the main loop reaps it.
    def stop(*_):
        server.stopping = True
        if server.latexmk:
            server.latexmk.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGHUP, stop)  # VS Code closing the task terminal
    try:  # until stopped, or latexmk exits by itself; meanwhile, switch documents for the viewer
        while not server.stopping and not (server.latexmk and server.latexmk.poll() is not None):
            try:
                doc, result, done = server.requests.get(timeout=1)
            except queue.Empty:
                continue
            try:
                result["status"], result["body"] = switch_doc(server, doc)
            except Exception as e:
                result["status"], result["body"] = 500, {"error": f"switch failed: {e}"}
            done.set()
    except KeyboardInterrupt:
        pass
    stop_latexmk(server)


if __name__ == "__main__":
    main()
