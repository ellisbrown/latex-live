#!/usr/bin/env python3
"""latex-live: LaTeX Workshop-style live preview without the extension.

Usage (from a paper root):  live.py [<doc>[.tex] | PATH] [--port 44100] [--page-limit N] [--build-dir DIR] [--no-build] [--no-open] [--host-tex]

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
  the inline notes, from a background build), search, a numbered outline, page
  thumbnails, the notes, the figures and tables, and a pre-submission check
  (including stale generated figures, consistency lints, and what the arXiv
  source would expose) in a sidebar, hover previews of references, highlights
  of what changed since the last build (text and figures), hints on where to
  save space, a notes-free view, and a latexdiff
  against any git commit. Notes can be written from the viewer too: select text,
  and the note macro of the author picked in the toolbar goes into the source
  after it (also edit, resolve, reply, and delete). On a remote machine, forward the port (e.g. in VS Code's Ports panel)
  and open the localhost URL; with LATEX_LIVE_HOST set, a direct URL on that host
  is printed too (e.g. a name a tunnel exposes). Anything that reaches the port
  can send requests, so every request needs the token (in ~/.config/latex-live;
  ?t=TOKEN once, then a SameSite=Strict cookie).
- One port for every preview (the hub, 44100): each instance runs on its own
  port from 44101, and the one that holds the hub's lock forwards
  /p/<paper>/<doc>/ to the right one; / lists them.
- One instance per document: a second one (e.g. another VS Code window opening
  the same folder) prints the running viewer's URL and exits, or with --replace
  stops the first and runs instead; one that no longer answers is replaced. /papers
  lists every running instance on the machine.
- Shared mode (LATEX_LIVE_USERS=users.json): coauthors open this instance through an
  authenticating reverse proxy on this machine, or with personal links, each with
  the role the users file gives them (viewer, commenter, editor, owner). It serves
  exactly --port, to this machine only, and stays out of the hub. It builds without
  latexmk (which parses what TeX writes on the host): live.py runs pdflatex, bibtex,
  biber and makeindex itself, each in a confined container of the TeX Live image.
- The LaTeX can also be edited in the viewer, beside the PDF (the browser editor): a save
  is merged with what was saved to the file meanwhile (git merge-file), and in shared mode
  with LATEX_LIVE_COMMIT=1 each coauthor's edits and notes are committed to git as theirs.
"""

import argparse
import bisect
import collections
import contextlib
import ctypes
import fcntl
import glob
import gzip
import hashlib
import html
import http.client
import http.cookies
import http.server
import itertools
import json
import math
import os
import queue
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import traceback
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))  # (latexlive/ next to this file, wherever it is run from)
from latexlive import config  # noqa: E402
from latexlive.auth import (  # noqa: E402
    LOOPBACK_ORIGIN_RE, ROLES, USER_ID_RE, host_kinds, is_loopback, load_token, load_users, owner_name,
    owner_principal, peer_uid, principal, same_secret, users, users_owner)
from latexlive.files import (  # noqa: E402
    aux_tree, child_aux, clean_file, file_hash, fls_file, in_paper, job_file, mtime, open_output, pdf_path,
    project_inputs, project_tex_files, read_build, read_capped, read_json, read_output, too_big)
from latexlive.git import commits, git_busy, git_operation, git_read_env, pushed_commit  # noqa: E402
from latexlive.latex import (  # noqa: E402
    DOCCLASS_RE, INPUT_RE, RERUN_RE, blank_comments, brace_group, false_blocks, paper_root_of, pick_doc,
    project_macros, read_tex, root_docs, source_tree, tex_to_text)
from latexlive.roles import READ_PATHS, allowed, at_least, least_role, shared_pdfs, viewer_status  # noqa: E402
from latexlive.tools import (  # noqa: E402
    LIVE_SPAWN, RUN_SEQ, SPAWNS, container_argv, container_exec, image_problem, image_pulled, pdf_pages_text, pdf_tool,
    run_tool, stop_containers, synctex_unavailable, tex_argv)


def me(server, user):
    """GET me: who the viewer's user is and what they may do, so the viewer shows only that (without
    shared mode: the owner, who may do everything). macroDefined: whether the paper defines the user's
    note macro, without which a coauthor can't comment; openHost, in shared mode: whether the owner's
    editor opens here (a service has none); download: whether they may download the builds made on
    request, the paper without its notes (export) and with its notes in the margin (review), which
    take the same role."""
    defined = bool(user["macro"]) and user["macro"] in paper_note_kinds(server.paper_root, server.default_pdf)
    can = lambda path: allowed(user, "POST", path)
    return {"id": user["id"], "name": user["name"] or owner_name(server), "macro": user["macro"], "role": user["role"],
            "shared": config.SHARED, "owner": owner_name(server), "macroDefined": defined,
            "can": {"comment": can("/note") and (user["role"] == "owner" or defined), "edit": can("/file"),
                    "rebuild": can("/rebuild"), "compare": can("/diff"), "switch": can("/switch"),
                    "openHost": can("/open") and (not config.SHARED or editor_cli() is not None),
                    "download": can("/export") and can("/review"), "checks": allowed(user, "GET", "/check/")}}


def letters(text):
    """text's letters only, in lower case, ligatures taken apart (to find a note's text in a PDF's, whose line
    breaks, hyphens, line numbers and references differ)."""
    return re.sub(r"[\W\d_]+", "", unicodedata.normalize("NFKC", text).lower())


def shown_notes(server, pdf_name, found):
    """Of the notes found (notes, margin_notes), those that the published PDF shows, for viewers and
    commenters: a note counts if the start of its text (letters) is in the PDF's text (pdftotext), so that notes
    in a false branch (\\iffalse, a switch that is off), or all of them while \\todotxt hides them, stay
    with the editors, who read the sources anyway. Without poppler, none."""
    pdf = pdf_path(server.paper_root, pdf_name)
    key = (pdf, mtime(pdf))
    kept = getattr(server, "pdf_text", None)  # (one PDF's, the last asked for: (key, text))
    if kept and kept[0] == key:
        text = kept[1]
    else:
        text = letters(" ".join(pdf_pages_text(pdf))) if os.path.isfile(pdf) else ""
        server.pdf_text = (key, text)
    shown = lambda note: (start := letters(note.get("text") or "")[:20]) and start in text
    return [note for note in found if shown(note)]


POST_LIMIT, POST_LIMITS = 100_000, {"/file": 8_000_000}  # bytes of a POST body (the browser editor saves whole files)


def content_length(headers, limit):
    """(n, None) for a request body of n bytes (its Content-Length; none: 0), else (None, the status to
    answer): 400 unless it is a single plain number, 413 if it is over limit."""
    values = headers.get_all("Content-Length") or ["0"]
    if len({v.strip() for v in values}) != 1 or not re.fullmatch(r"[0-9]{1,15}", values[0].strip()):
        return None, 400
    n = int(values[0].strip())
    return (n, None) if n <= limit else (None, 413)


class DualStackServer(http.server.ThreadingHTTPServer):
    address_family = socket.AF_INET6

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)  # accept IPv4 too
        super().server_bind()

    def handle_error(self, request, client_address):
        """Print what went wrong in a request, unless the client just left mid-answer (a reload cuts a
        download short): that is no error, and its traceback would only clutter the log."""
        if not isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
            super().handle_error(request, client_address)


class LoopbackServer(DualStackServer):
    """A shared instance's server: on every interface, like the others (so 127.0.0.1 and [::1] both
    work), but it drops a connection from another machine before reading anything: coauthors come
    through the proxy, which runs here. At most CONNECTIONS connections at once (each holds a thread
    until it ends or times out): past that, it drops new ones, which a public tunnel could otherwise
    open without end."""
    CONNECTIONS = 64

    def server_activate(self):
        self.slots, self.holding, self.slots_lock = threading.BoundedSemaphore(self.CONNECTIONS), set(), threading.Lock()
        super().server_activate()

    def verify_request(self, request, client_address):
        return is_loopback(client_address[0])

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            return self.shutdown_request(request)
        with self.slots_lock:
            self.holding.add(request)
        super().process_request(request, client_address)

    def shutdown_request(self, request):
        super().shutdown_request(request)
        with self.slots_lock:
            held = request in self.holding
            self.holding.discard(request)
        if held:
            self.slots.release()


STATIC = {"/viewer.html"}  # plus everything under /pdfjs/ and /codemirror/


class Handler(http.server.SimpleHTTPRequestHandler):
    timeout = 60  # (a client that stops sending mid-request frees its thread)
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map, ".mjs": "text/javascript", ".js": "text/javascript"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=config.HERE, **kwargs)

    def authorize(self, query):
        """Who is asking (a principal), or None, with self.denial saying why (deny). Every client needs
        ?t=TOKEN once, then the cookie (forwarded traffic arrives via loopback too). In shared mode, a
        user's own token works the same way, or the trusted proxy names the user (proxy_user); and the
        Host must be one of this instance's names (host_kinds)."""
        self.set_cookie, self.hosts = None, set()
        self.denial = (403, "open your link again (the one with ?t= in it)", None)
        if config.SHARED:  # (a browser with an old or revoked personal link, or none, gets a page that says what to do)
            self.denial = (*self.denial[:2], lambda: bad_link_page(owner_name(self.server)))
            hosts = self.headers.get_all("Host") or []
            self.hosts = host_kinds(hosts[0]) if len(hosts) == 1 else set()
            if not self.hosts:
                self.denial = (403, "this preview does not answer to that host name", None)
                return None
            if config.IDENTITY_HEADER and self.headers.get_all(config.IDENTITY_HEADER) is not None:
                return self.proxy_user()  # (never by token or cookie: they stay out of the proxy's traffic)
        cookie = http.cookies.SimpleCookie(self.headers.get("Cookie", "")).get("latex_live")
        cookie = cookie.value if cookie else ""
        for value in (cookie, query.get("t", [""])[0]):
            user = self.token_user(value)
            if user:
                if value != cookie:
                    self.set_cookie = value
                return user
        return None

    def token_user(self, value):
        """The principal a token is for: the owner (the instance's token), or in shared mode the user
        whose personal link it is; else None."""
        if not value:
            return None
        if same_secret(value, self.server.token):  # (never with an empty token: same_secret refuses one, as load_token does)
            return owner_principal("owner-token")
        for entry in users().values() if config.SHARED else ():
            if entry["token"] and same_secret(value, entry["token"]):
                return principal(entry, "token")
        return None

    def proxy_user(self):
        """The user the trusted proxy names in IDENTITY_HEADER (exactly once), or None. Any program on
        this machine can connect and send that header, so it counts only with the proxy's Host
        (LATEX_LIVE_PROXY_HOST) and from a socket of the proxy's uid (LATEX_LIVE_PROXY_UID), where set."""
        values = self.headers.get_all(config.IDENTITY_HEADER)
        name = values[0].strip() if len(values) == 1 else ""
        entry = None
        if not USER_ID_RE.fullmatch(name):
            self.denial = (403, f"expected one user name in {config.IDENTITY_HEADER}", None)
        elif config.PROXY_HOST and "proxy" not in self.hosts:
            self.denial = (403, f"{config.IDENTITY_HEADER} counts only from the proxy (its Host)", None)
        elif config.PROXY_UID is not None and peer_uid(self.client_address, self.connection.getsockname()) != config.PROXY_UID:
            self.denial = (403, f"{config.IDENTITY_HEADER} counts only from the proxy (its uid)", None)
        elif not (entry := users().get(name)):
            owner = owner_name(self.server)
            text = f"You are signed in as {name}, but this preview isn't shared with you. Ask {owner} to add you."
            self.denial = (403, text, unshared_page(name, owner))
        return entry and principal(entry, "proxy")

    def deny(self):
        """Answer a request that authorize turned down: 403, as a page for a browser where there is one
        (made only now, if it is a function)."""
        status, text, page = self.denial
        page = page() if callable(page) else page
        if page and "text/html" in self.headers.get("Accept", ""):
            return self.send_html(page, status)
        if page:
            return self.send_text(status, text)
        self.send_error(status, text)

    def origin_allowed(self):
        """Whether a POST in shared mode comes from the viewer's own page, so that no other site can make
        a coauthor's browser act: Origin is the public one when the Host is the public or the proxy's,
        a loopback one (any port: forwards remap it) when the Host is a loopback name. Without Origin,
        only from a program with a token (curl, check.py), at a loopback name."""
        origins = self.headers.get_all("Origin") or []
        if not origins:
            return "loopback" in self.hosts and self.principal["via"] != "proxy"
        origin = origins[0].strip() if len(origins) == 1 else ""
        if self.hosts & {"public", "proxy"} and config.PUBLIC_ORIGIN and origin == config.PUBLIC_ORIGIN:
            return True
        return "loopback" in self.hosts and bool(LOOPBACK_ORIGIN_RE.fullmatch(origin))

    def permitted(self, method, path, query, before=None):
        """Whether the principal's role allows the request (allowed); non-owners see only the shared
        document (shared_pdfs). Answers 403 if not, after calling before (if given). (Without shared
        mode everyone is the owner.)"""
        user = self.principal
        if not allowed(user, method, path):
            before and before()
            need = least_role(method, path)
            self.send_json(403, {"error": "a shared preview serves one document" if need is None
                                 else f"this takes the {need} role here (yours is {user['role']})"})
            return False
        if user["role"] == "owner":
            return True
        name = path.split("/", 2)[2] if path.startswith(READ_PATHS) else query.get("file", [None])[0] if path == "/synctex" else None
        if name is not None and name not in shared_pdfs(self.server, user):
            before and before()
            self.send_json(403, {"error": f"this preview shares {self.server.default_pdf} only"})
            return False
        return True

    def do_HEAD(self):  # (not served: SimpleHTTPRequestHandler's would skip the token)
        self.send_error(405)

    def misdirected(self):
        """A request the hub meant for another paper (HubHandler.forward): answered 421."""
        want = self.headers.get("X-Latex-Live-Paper")
        if want and want != self.server.paper_id:
            self.send_error(421, f"this preview is {self.server.paper_id}, not {want}")
            return True
        return False

    def end_headers(self):
        if getattr(self, "set_cookie", None):  # (Secure where the browser reaches it over https: shared mode's public URL)
            secure = "; Secure" if config.SHARED and (config.PUBLIC_ORIGIN or "").startswith("https:") and "loopback" not in getattr(self, "hosts", ()) else ""
            self.send_header("Set-Cookie", f"latex_live={self.set_cookie}; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000{secure}")
        if self.path.startswith("/viewer.html"):  # revalidate, so an update shows on the next reload
            self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        if config.SHARED:  # (no other site may frame the viewer, nor learn its URL; VS Code's Simple Browser frames an owner's own)
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
            self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(url.path)
        query = urllib.parse.parse_qs(url.query)
        self.principal = self.authorize(query)
        if not self.principal:
            return self.deny() if config.SHARED else self.send_error(403, f"open the URL with ?t=<token> from {config.TOKEN_FILE}")
        if self.misdirected() or not self.permitted("GET", path, query):
            return
        if path == "/":
            self.send_response(302)
            self.send_header("Location", f"viewer.html?file={urllib.parse.quote(self.server.default_pdf)}")  # (relative: also under the hub)
            self.end_headers()
        elif path.startswith(READ_PATHS):
            name = path.split("/", 2)[2]
            if not name.endswith(".pdf") or os.path.basename(name) != name:
                return self.send_error(404)
            pdf = pdf_path(self.server.paper_root, name)
            doc_pdf = re.sub(r"(-head|-push)?-(clean|diff|head|push|export|review)\.pdf$", ".pdf", name)  # the document a derived PDF belongs to
            below_editor = config.SHARED and not at_least(self.principal, "editor")  # (they see the current PDF and what it shows)
            if path.startswith("/status/"):  # (?base=push: a viewer shows changes since the last push; ?open=: its editor's file)
                info = status(self.server, name, query.get("base") == ["push"])
                if "open" in query and allowed(self.principal, "GET", "/file"):
                    info["source"] = source_status(self.server, query["open"][0])
                body = json.dumps(viewer_status(info) if below_editor else info).encode()
            elif path.startswith("/outline/"):
                body = json.dumps(toc_outline(self.server.paper_root, doc_pdf, name.endswith("-clean.pdf"))).encode()
            elif path.startswith("/notes/"):
                found = notes(self.server.paper_root, doc_pdf)
                body = json.dumps(shown_notes(self.server, doc_pdf, found) if below_editor else found).encode()
            elif path.startswith("/authors/"):
                body = json.dumps(note_authors(self.server.paper_root, doc_pdf)).encode()
            elif path.startswith("/check/"):
                body = json.dumps(cached_checks(self.server, doc_pdf)).encode()
            elif path.startswith("/graphics/"):  # (figure files, where in the sources, git's state: not in the PDF)
                body = json.dumps([] if below_editor else graphics(self.server.paper_root, doc_pdf)).encode()
            elif path.startswith("/margin/"):
                found = margin_notes(self.server.paper_root, doc_pdf)
                body = json.dumps(shown_notes(self.server, doc_pdf, found) if below_editor else found).encode()
            elif path.startswith("/floats/"):
                found = floats(self.server.paper_root, doc_pdf, name.endswith("-clean.pdf"))
                if below_editor:  # (the label names and where in the sources: not in the PDF)
                    found = [{k: v for k, v in f.items() if k not in ("keys", "file", "line", "last")} for f in found]
                body = json.dumps(found).encode()
            else:
                return self.send_pdf(pdf)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.write_body(body)
        elif path == "/me":
            self.send_json(200, me(self.server, self.principal))
        elif path == "/synctex":  # (JSON for the browser; without shared mode and json=1 it opens the editor here)
            if config.SHARED or query.get("json") == ["1"]:
                self.send_json(*synctex_lookup(self.server, query, self.principal))
            else:
                self.send_text(*synctex_edit(self.server.paper_root, query))
        elif path == "/files":
            self.send_json(200, files_listing(self.server))
        elif path == "/file":
            self.send_json(*file_request(self.server, query))
        elif path in ("/open", "/diff", "/switch", "/export", "/review") and config.SHARED:  # (only as POST, which checks the Origin)
            self.send_error(405, "use POST")
        elif path == "/open":
            self.send_text(*open_source(self.server.paper_root, query))
        elif path == "/forward":
            self.send_json(*forward_request(self.server, query, self.principal))
        elif path == "/commits":
            self.send_json(200, commits(self.server.paper_root))
        elif path == "/export":
            self.send_json(*start_export(self.server))
        elif path == "/review":
            self.send_json(*start_review(self.server))
        elif path == "/diff":
            self.send_json(*start_diff(self.server, query.get("rev", [""])[0]))
        elif path == "/instances":
            self.send_json(200, {"self": os.getpid(), "instances": instances()})
        elif path == "/papers":
            self.send_html(papers_page(self.server, self.headers.get("Host", "localhost")))
        elif path == "/docs":  # (a coauthor sees only the shared document)
            docs = root_docs(self.server.paper_root) if self.principal["role"] == "owner" else [self.server.default_pdf[:-4]]
            self.send_json(200, {"doc": self.server.default_pdf[:-4], "docs": docs})
        elif path == "/switch":
            self.send_json(*request_switch(self.server, query))
        elif path == "/favicon.ico":  # (no icon; nothing to refuse either: browsers ask on every page)
            self.send_response(204)
            self.end_headers()
        elif path in STATIC or (path.startswith(("/pdfjs/", "/codemirror/")) and ".." not in path and not path.endswith("/")):
            super().do_GET()
        else:
            self.send_error(404)

    def do_POST(self):
        """Actions (POST /note, /file, /rebuild, /switch, /diff, /open, /commit), and the builds made on
        request (POST /export, /review, and in shared mode /baseline). Only with the cookie, which SameSite=Strict keeps off other
        sites' requests, and a JSON body, which other sites can't send without asking first; in shared
        mode, also only from the viewer's own page (origin_allowed), and as the role allows, which shared
        mode checks before it reads the body (a refusal reads a small body still, so that the answer isn't
        lost to a reset; a large one, not)."""
        path = urllib.parse.urlsplit(self.path).path
        self.principal = self.authorize({})
        if not self.principal:
            return self.deny() if config.SHARED else self.send_error(403, "open the viewer URL with ?t=<token> first")
        if self.misdirected():
            return
        length, error = content_length(self.headers, POST_LIMITS.get(path, POST_LIMIT))
        if error:
            return self.send_error(error)

        def drain():
            if length <= 1 << 16:
                with contextlib.suppress(OSError):
                    self.rfile.read(length)

        if config.SHARED and not self.origin_allowed():
            drain()
            return self.send_error(403, "not from this preview's own page (Origin)")
        if config.SHARED and not self.permitted("POST", path, {}, before=drain):
            return
        try:
            data = self.rfile.read(length)
        except OSError:  # (the client stopped sending: the timeout)
            return
        if not config.SHARED and not self.permitted("POST", path, {}):
            return
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return self.send_error(415, "expected a JSON body")
        try:
            req = json.loads(data or b"{}")
        except (ValueError, RecursionError):
            return self.send_error(400, "bad JSON")
        if path == "/note" and isinstance(req, dict):
            self.send_json(*note_request(self.server, req, self.principal))
        elif path == "/file" and isinstance(req, dict):
            self.send_json(*save_request(self.server, req, self.principal))
        elif path == "/commit":
            self.send_json(*commit_request(self.server))
        elif path == "/rebuild" and config.SHARED:
            self.send_json(*shared_rebuild_request(self.server, self.principal))
        elif path == "/rebuild":
            self.send_json(*on_main_thread(self.server, rebuild_doc))
        elif path == "/baseline" and config.SHARED and isinstance(req, dict):
            self.send_json(*baseline_request(self.server, req.get("kind")))
        elif path == "/switch" and isinstance(req, dict):
            self.send_json(*request_switch(self.server, json_query(req, "doc")))
        elif path == "/diff" and isinstance(req, dict):
            rev = json_query(req, "rev").get("rev", [""])[0]
            self.send_json(*start_diff(self.server, rev, any_rev=self.principal["role"] == "owner"))
        elif path == "/open" and isinstance(req, dict):
            self.send_text(*open_source(self.server.paper_root, json_query(req, "file", "line")))
        elif path == "/export":
            self.send_json(*start_export(self.server))
        elif path == "/review":
            self.send_json(*start_review(self.server))
        else:
            self.send_error(404)

    def send_json(self, status, value):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.write_body(body)

    def send_pdf(self, pdf):
        """A PDF, streamed from its file (open_output: in shared mode never through a link), in parts of 256 KB."""
        f = open_output(pdf)
        if f is None:
            return self.send_error(404)
        with f:
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(os.fstat(f.fileno()).st_size))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                shutil.copyfileobj(f, self.wfile, 1 << 18)
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True

    def write_body(self, body):
        """Write a response body in parts of 256 KB: the socket timeout bounds each part's write, not a
        whole PDF or source file on a slow link. A client that leaves meanwhile (a reload cuts a PDF's
        download short) just stops it."""
        view = memoryview(body)
        try:
            for i in range(0, len(body), 1 << 18):
                self.wfile.write(view[i:i + (1 << 18)])
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def send_html(self, text, status=200):
        body = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.write_body(body)

    def send_text(self, status, text):
        body = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.write_body(body)

    def log_message(self, *args):  # the viewer polls twice a second; keep the terminal clean
        pass


def json_query(req, *keys):
    """A JSON body's keys as parse_qs gives a query string's (strings and integers), for the handlers
    that GET requests share."""
    return {key: [str(req[key])] for key in keys if isinstance(req.get(key), (str, int)) and not isinstance(req.get(key), bool)}


PAGE_STYLE = "<style>body { font: 15px system-ui, sans-serif; background: #323639; color: #eee; margin: 3em; max-width: 40em; }</style>"


def unshared_page(name, owner):
    """The page for someone the proxy signed in whom the users file doesn't list."""
    return f"""<!doctype html><meta charset="utf-8"><title>latex-live: not shared with you</title>
{PAGE_STYLE}
<h2>This preview isn't shared with you</h2>
<p>You are signed in as <b>{html.escape(name)}</b>, but this preview isn't shared with you. Ask {html.escape(owner)} to add you.</p>"""


def bad_link_page(owner):
    """The page for a browser in shared mode without a link that works: an old or revoked personal link,
    or none (it says nothing about the link itself, nor where anything is)."""
    return f"""<!doctype html><meta charset="utf-8"><title>latex-live: this link doesn't work</title>
{PAGE_STYLE}
<h2>This link doesn't work</h2>
<p>It may be old, or your access may have ended. Ask {html.escape(owner)} for a new one.</p>"""


def run_synctex(paper_root, *args):
    """Run `synctex <args>` and return its result records (dicts of the Key:value lines).

    Without a synctex on the host, it runs in the TeX Live image (~0.2 s); with neither, there are
    no records (synctex_unavailable says why). In shared mode always in the image, confined: SyncTeX
    parses the build's files, which the paper's TeX writes. At most four at once (SPAWNS), each for
    up to a minute.
    """
    cmd = ["synctex", *args]
    if config.SHARED or not shutil.which("synctex"):
        if not shutil.which("podman"):
            return []
        cmd = [*container_argv(paper_root), *cmd]
    try:
        code, out = run_tool(cmd, paper_root, timeout=60, kind="synctex", capture=4 << 20)
    except OSError:
        return []
    records = []
    for line in out.splitlines():
        key, sep, value = line.partition(":")
        if key == "Output":  # each result record starts with Output:
            records.append({})
        if sep and records:
            records[-1][key] = value
    return records


def synctex_records(synctex_gz):
    """The lines of a build's SyncTeX file, decompressed as they are read, up to SYNCTEX_LIMIT bytes and
    RECORD_LIMIT lines in all (a file the paper's TeX made could hold far more); none if there is no file."""
    f = open_output(synctex_gz)
    if f is None:
        return
    size = 0
    with f, gzip.open(f, "rt", errors="replace") as text:
        for n in range(config.RECORD_LIMIT):
            try:
                record = text.readline(1 << 16)  # (a line past that comes in parts)
            except (OSError, EOFError, zlib.error):  # (not gzip, or cut off)
                return
            size += len(record)
            if not record or size > config.SYNCTEX_LIMIT:
                return
            yield record


def to_int(text, default=None):
    """int(text), or default for text that isn't a number (SyncTeX's output comes from files the paper's TeX wrote)."""
    try:
        return int(text)
    except (TypeError, ValueError):
        return default


BIB_SPAN_LOCK = threading.Lock()  # (bib_span: one lookup per build, however many viewers poll at once)


def bib_span(paper_root, files, cache={}):
    """Where the bibliography starts and ends: ({"page", "y"}, {"page", "y"}) (y in points
    from the page top), or None. Maps the first and last lines of the .bbl to the PDF, cached
    per build (each viewer's status poll asks, and a lookup may take several SyncTeX runs).
    files = (synctex_gz, bbl, pdf).
    """
    synctex_gz, bbl, pdf = files
    try:
        mtime = os.stat(synctex_gz).st_mtime_ns
        text = read_capped(bbl)
        if text is None:
            return None
        last = text.count("\n") + 1
    except OSError:
        return None
    if cache.get(synctex_gz, (None,))[0] != mtime:
        with BIB_SPAN_LOCK:
            if cache.get(synctex_gz, (None,))[0] != mtime:  # (another request may have looked it up meanwhile)
                def where(line):
                    rec = next((r for r in run_synctex(paper_root, "view", "-i", f"{line}:0:{bbl}", "-o", pdf) if "Page" in r), None)
                    try:
                        return rec and {"page": int(rec["Page"]), "x": float(rec["h"]), "y": float(rec["v"]) - float(rec["H"])}
                    except (KeyError, ValueError):  # (a record that isn't what SyncTeX writes)
                        return None
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


LAYOUT_RE = re.compile(r"^latex-live layout: (.+)$", re.M)


def layout(paper_root, pdf_name, cache={}):
    """The text area of the last build, as latexmkrc's pretex hook printed it to the log, in PDF points
    from the page's top-left: {W, H, left, top, bottom, width, colwidth, leading, columns}. None until a build has
    printed it. A build that stopped before \\begin{document} keeps the previous value."""
    log = job_file(paper_root, pdf_name, ".log")
    stamp = mtime(log)
    if cache.get(log, (None,))[0] != stamp:
        m = LAYOUT_RE.search(read_capped(log) or "")  # (the line comes once the preamble has run: near the start)
        value = cache.get(log, (None, None))[1]
        if m:
            try:  # TeX points (72.27 per inch) to PDF points (72); glue keeps only its natural size
                pw, ph, hoff, side, voff, top, head, sep, tw, th, cw, skip = (
                    float(re.match(r"-?[\d.]+", v.strip()).group()) * 72 / 72.27 for v in m.group(1).split(","))
                y = 72 + voff + top + head + sep  # TeX's origin is 1in from the top-left corner
                value = {"W": round(pw, 2), "H": round(ph, 2), "left": round(72 + hoff + side, 2), "top": round(y, 2),
                         "bottom": round(y + th, 2), "width": round(tw, 2), "colwidth": round(cw, 2), "leading": round(skip, 2),
                         "columns": 2 if cw < 0.75 * tw else 1}
            except (AttributeError, ValueError):
                pass
        cache[log] = (stamp, value)
    return cache[log][1]


# Main-text page limits of submission styles, for documents run without --page-limit.
VENUE_PAGE_LIMITS = {r"iclr\d{4}_conference": 9, r"neurips_\d{4}": 9, r"colm\d{4}_conference": 9, r"icml\d{4}": 8,
                     r"cvpr": 8, r"iccv": 8}
USEPACKAGE_RE = re.compile(r"\\usepackage\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")


def page_limit(server, pdf_name):
    """--page-limit, else the limit of the venue style the document loads, else None."""
    if server.page_limit:
        return server.page_limit
    packages = {p.strip() for m in USEPACKAGE_RE.finditer(read_tex(os.path.join(server.paper_root, pdf_name[:-4] + ".tex")))
                for p in m.group(1).split(",")}
    return next((n for pattern, n in VENUE_PAGE_LIMITS.items() if any(re.fullmatch(pattern, p) for p in packages)), None)


def forward_asked(found):
    """forward.py's request (build/<job>.forward.json: {seq, page, x, y, w, h, clean?}) as read, or in
    shared mode only its numbers (the paper's TeX can write that file too), or None if it has none."""
    if not config.SHARED or found is None:
        return found
    numbers = lambda d: {k: d[k] for k in ("seq", "page", "x", "y", "w", "h")
                         if isinstance(d, dict) and type(d.get(k)) in (int, float)}
    target = numbers(found)
    if len(target) != 6:
        return None
    clean = numbers(found.get("clean"))
    return {**target, **({"clean": clean} if len(clean) == 5 else {})}


def status(server, pdf_name, push=False):
    """Everything the viewer polls for: PDF version, build state, forward-search target, page limit
    and layout (and the notes-free build's PDF, to measure it too), which document the server
    builds (viewers of another one switch to it), and the change baselines (push: a viewer shows
    changes since the last push, so that baseline gets built). In shared mode a GET builds nothing
    (the viewer asks for a baseline with POST /baseline), and the build's state, status and what it
    published come from shared_build, not from build/, where the paper's TeX writes."""
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
        "building": build_running(server, pdf_name),
        "build": build_status(server, pdf_name),
        "forward": forward_asked(read_json(job_file(root, pdf_name, ".forward.json"))),
        "pageLimit": limit,
        "refs": refs_start(root, pdf_name),  # where the main text ends (page badge, space hints)
        "layout": layout(root, pdf_name),
        "doc": server.default_pdf,
    }
    info["cleanBuild"] = build_status(server, pdf_name, clean=True)  # (the notes-free pass)
    published = server.shared["published"] if config.SHARED else read_json(job_file(root, pdf_name, ".published.json"))  # (what was published last)
    if published and os.path.isfile(pdf):
        st = os.stat(pdf)
        if [st.st_mtime_ns, st.st_size] != [published.get("mtime"), published.get("size")]:
            info["overwritten"] = time.strftime("%H:%M:%S", time.localtime(st.st_mtime))
    clean_pdf = pdf_path(root, pdf_name[:-4] + "-clean.pdf")
    if os.path.isfile(clean_pdf):
        info["clean"] = {"version": str(mtime(clean_pdf)), "refs": refs_start(root, pdf_name, clean=True)}
    info["head"] = base_status(server, pdf_name, "head", build=not config.SHARED)
    info["push"] = base_status(server, pdf_name, "push", build=push and not config.SHARED)
    info["texImage"] = image_problem(server)
    info["latexLive"] = {"version": config.VERSION, "updated": code_updated()}
    return info


def code_updated(started={}):
    """Whether live.py or VERSION on disk differs from what this process runs (e.g. after a git
    pull, or a release that only bumps VERSION): the viewer then asks for a restart.
    latex_report.py and forward.py run anew each time."""
    paths = [os.path.join(config.HERE, name) for name in ("live.py", "VERSION")]
    stamp = [mtime(path) for path in paths]
    if "hash" not in started:
        started.update(hash=[file_hash(path) for path in paths], stamp=stamp, updated=False)
    elif stamp != started["stamp"]:
        started.update(stamp=stamp, updated=[file_hash(path) for path in paths] != started["hash"])
    return started["updated"]


TOC_LEVELS = {"part": 0, "chapter": 0, "section": 1, "subsection": 2, "subsubsection": 3, "paragraph": 4}


def contents_lines(text):
    """The \\contentsline{type}{title}{page label}{anchor} entries in text, as argument lists."""
    found, i = [], 0
    while (i := text.find("\\contentsline", i)) >= 0:
        args, i = [], i + len("\\contentsline")
        while len(args) < 4 and (j := text.find("{", i)) >= 0 and not text[i:j].strip():
            arg, i = brace_group(text, j)
            args.append(arg)
        if len(args) == 4:
            found.append(args)
    return found


WRITTEN_RE = re.compile(r"\\@writefile\{(toc|lof|lot)\}(?=\{)|\\@input\{([^}]+\.aux)\}")


def written_contents(aux, ext, depth=0, budget=None):
    """The contents lines a build wrote for its .toc, .lof or .lot (ext) through its .aux, and the
    .aux files that one inputs (\\include). Every build writes them, whether or not the document
    prints that list. The paper's TeX writes those names: only .aux files in the same folder or below
    it are read (child_aux), AUX_BUDGET bytes in all."""
    budget = [config.AUX_BUDGET, 200] if budget is None else budget
    text = read_capped(aux, min(config.READ_LIMIT, budget[0])) or ""
    budget[0] -= len(text)
    found = []
    for m in WRITTEN_RE.finditer(text):
        if m.group(1) == ext:
            found += contents_lines(brace_group(text, m.end())[0])
        elif m.group(2) and depth < 3 and budget[0] > 0 and budget[1] > 0 and (child := child_aux(os.path.dirname(aux), m.group(2))):
            budget[1] -= 1
            found += written_contents(child, ext, depth + 1, budget)
    return found


def numbered(arg, macros):
    """(number, text) of a contents-line title: \\numberline{2.1}Title -> ("2.1", "Title")."""
    m = re.match(r"\s*\\numberline\s*\{([^{}]*)\}", arg)
    text = tex_to_text(arg[m.end():] if m else arg, macros)
    return (m.group(1).strip() or None) if m else None, re.sub(r" ([:;,.?!)])", r"\1", " ".join(text.split()))


def toc_outline(paper_root, pdf_name, clean=False):
    """Outline entries [{level, num, title, page, dest}] (of the notes-free build with clean=True,
    whose pages differ). The viewer numbers the PDF's bookmarks with them, and shows them as the
    outline of a PDF without bookmarks.

    From the contents lines the build wrote to its .aux, else its .toc. The anchor (a named
    destination) is empty when hyperref doesn't handle the entry; the viewer then finds the title
    on the page.
    """
    f = clean_file if clean else job_file
    lines = written_contents(f(paper_root, pdf_name, ".aux"), "toc")
    if not lines:
        text = read_capped(f(paper_root, pdf_name, ".toc"))
        if text is None:
            return []
        lines = contents_lines(text)
    macros = project_macros(paper_root, fls_file(paper_root, pdf_name))
    entries = []
    for kind, title, page, dest in lines:
        if kind in TOC_LEVELS:
            num, title = numbered(title, macros)
            entries.append({"level": TOC_LEVELS[kind], "num": num, "title": title, "page": page, "dest": dest or None})
    return entries


SYNCTEX_BOX_RE = re.compile(r"([\[(h])(\d+),(\d+):(-?\d+),(-?\d+):(-?\d+),(-?\d+),(-?\d+)")  # vboxes, hboxes


def synctex_boxes(synctex_gz, cache={}):
    """{(file, line): [(page, left, top, right, bottom)]}: the boxes each source line made (points
    from the page's top-left), from a build's SyncTeX file."""
    key = (synctex_gz, mtime(synctex_gz))
    if key not in cache:
        inputs, boxes, page = {}, {}, 0
        pt = 72 / 72.27 / 65536  # scaled points to PDF points
        for record in synctex_records(synctex_gz):
            if record.startswith("Input:"):
                tag, _, path = record[6:].rstrip("\n").partition(":")
                inputs[tag] = os.path.normpath(path)
            elif record.startswith("{"):
                page = to_int(record[1:], page)
            elif m := SYNCTEX_BOX_RE.match(record):
                x, y, w, h, d = (int(v) * pt for v in m.groups()[3:])
                boxes.setdefault((inputs.get(m.group(2)), int(m.group(3))), []).append(
                    (page, round(x, 1), round(y - h, 1), round(x + w, 1), round(y + d, 1)))
        cache.clear()
        cache[key] = boxes
    return cache[key]


FLOAT_ENV_RE = re.compile(r"\\(begin|end)\s*\{((?:sideways|wrap)?(?:figure|table))\*?\}")
CAPTION_RE = re.compile(r"\\caption\s*(?=[\[{])")  # (not \captionof or \subcaption)
LABEL_RE = re.compile(r"\\label\s*\{([^{}]*)\}")
NEWLABEL_RE = re.compile(r"\\newlabel\{([^{}]*)\}\{\{([^{}]*)\}")


SCALED_RE = re.compile(r"\\(?:resizebox|scalebox|rotatebox|reflectbox|adjustbox)\b|\\begin\{adjustbox\}")
ARG_RE = re.compile(r"\s*([\[{])")


def scaled_lines(env, first):
    """Lines of a float's environment (env, from line first) that end content drawn scaled or rotated
    (\\resizebox, \\scalebox, adjustbox...), whose SyncTeX boxes have its size before scaling."""
    lines = set()
    for m in SCALED_RE.finditer(env):
        i = m.end()
        if m.group().startswith("\\begin"):
            i = max(env.find("\\end{adjustbox}", i), i)
        else:  # to the end of its arguments
            while a := ARG_RE.match(env, i):
                i = brace_group(env, a.end(1) - 1)[1] if a.group(1) == "{" else (env.find("]", a.end()) + 1 or len(env))
        lines.update(range(first + env.count("\n", 0, m.start()), first + env.count("\n", 0, i) + 1))
    return lines


def floats(paper_root, pdf_name, clean=False):
    """The figures and tables that were typeset, in reading order, for the viewer's Figures tab and
    the placement check:
    [{kind, num, caption, page, label, dest, box, extent, keys, file, line, last}]. page is the PDF
    page, label its printed number; box is [left, top, right, bottom] in points from the page's
    top-left, cut at the page's bottom edge; extent is the box as typeset (not cut, nor widened by
    content drawn scaled), if known; keys are its \\label names; line and last are the lines of
    its environment in file.

    The environments come from the source; SyncTeX tells which were typeset and where (the boxes
    their lines made). Numbers come from their \\label, else the order of their captions; captions
    and anchors from the list of figures and tables the build wrote to its .aux.
    """
    f = clean_file if clean else job_file
    synctex_gz = f(paper_root, pdf_name, ".synctex.gz")
    if not os.path.exists(synctex_gz):
        return []
    boxes = synctex_boxes(synctex_gz)
    macros = project_macros(paper_root, fls_file(paper_root, pdf_name))
    area = layout(paper_root, pdf_name) or {"W": 612, "H": 792, "top": 72, "bottom": 720}
    tall = area["bottom"] - area["top"] - 1  # (no float is as tall as the text block)
    listed = {}
    for kind, ext in (("figure", "lof"), ("table", "lot")):
        listed[kind] = []
        for typ, title, page, dest in written_contents(f(paper_root, pdf_name, ".aux"), ext):
            if typ == kind:
                num, caption = numbered(title, macros)
                listed[kind].append({"num": num, "caption": caption, "label": page, "dest": dest or None})
    label_nums = dict(NEWLABEL_RE.findall(read_capped(f(paper_root, pdf_name, ".aux")) or ""))
    found, taken = [], {"figure": 0, "table": 0}
    for path in project_tex_files(paper_root, fls_file(paper_root, pdf_name)):
        text, open_at = read_tex(path), []
        for m in FLOAT_ENV_RE.finditer(text):
            if m.group(1) == "begin":
                open_at.append(m.start())
                continue
            if not open_at:
                continue
            start = open_at.pop()
            if open_at:  # (a float inside another one: part of it)
                continue
            first, last = text.count("\n", 0, start) + 1, text.count("\n", 0, m.end()) + 1
            made = [b for line in range(first, last + 1) for b in boxes.get((path, line), [])
                    # Not the page, its text block, header and footer (a page shipped out while one of these
                    # lines was current), nor an unscaled image or an empty box. A float may start low and
                    # run off the page.
                    if b[1] >= 1 and b[3] <= area["W"] and area["top"] - 15 <= b[2] <= area["bottom"] + 15
                    and 0 < b[4] - b[2] < tall and b[3] > b[1]]
            if not made:  # not typeset (e.g. in an \\iffalse block)
                continue
            kind = "table" if m.group(2).endswith("table") else "figure"
            env = text[start:m.end()]
            captions = len(CAPTION_RE.findall(env))
            entry = listed[kind][taken[kind]] if captions and taken[kind] < len(listed[kind]) else {}
            taken[kind] += captions
            nums = [label_nums[name] for name in LABEL_RE.findall(env) if name in label_nums]
            if nums and nums[0] != entry.get("num"):  # the label knows better than the order
                entry = next((e for e in listed[kind] if e["num"] == nums[0]), {"num": nums[0]})
            page = collections.Counter(b[0] for b in made).most_common(1)[0][0]
            on = [b for b in made if b[0] == page]
            skip = scaled_lines(env, first)
            sized = [] if m.group(2).startswith("sideways") else [  # (rotated: SyncTeX has the boxes unrotated)
                b for line in range(first, last + 1) if line not in skip for b in boxes.get((path, line), []) if b in on]
            found.append({"kind": kind, "num": entry.get("num"), "caption": entry.get("caption", ""), "page": page,
                          "label": entry.get("label"), "dest": entry.get("dest"),
                          "box": [min(b[1] for b in on), min(b[2] for b in on), max(b[3] for b in on),
                                  min(area["H"], max(b[4] for b in on))],  # (a float can run off the page)
                          "extent": sized and [min(b[1] for b in sized), min(b[2] for b in sized), max(b[3] for b in sized),
                                               max(b[4] for b in sized)] or None, "keys": LABEL_RE.findall(env),
                          "file": os.path.relpath(path, paper_root), "line": first, "last": last})
    return sorted(found, key=lambda x: (x["page"], x["box"][1]))


def forward_target(paper_root, src, line, clean=False):
    """Forward search: where source line src:line (absolute path) is in the most recent build that
    has it (or in the notes-free build). Returns (pdf, {"page", "x", "y", "w", "h"}) in points from
    the page's top-left, or None.
    """
    pattern = os.path.join("clean", "*-clean.synctex.gz") if clean else "*-live.synctex.gz"
    jobs = sorted(glob.glob(os.path.join(paper_root, config.BUILD, pattern)), key=os.path.getmtime, reverse=True)
    for synctex_gz in jobs:
        pdf = synctex_gz[: -len(".synctex.gz")] + ".pdf"
        records = [r for r in run_synctex(paper_root, "view", "-i", f"{line}:0:{src}", "-o", pdf) if "Page" in r]
        if not records:
            continue
        # A long source line can span several output lines: cover all of them on the first page.
        page = records[0]["Page"]
        try:
            boxes = [(float(r["h"]), float(r["v"]) - float(r["H"]), abs(float(r["W"])), float(r["H"]))
                     for r in records if r["Page"] == page]
            int(page)
        except (KeyError, ValueError):  # (a record that isn't what SyncTeX writes)
            continue
        x, y = min(b[0] for b in boxes), min(b[1] for b in boxes)
        return pdf, {"page": int(page), "x": x, "y": y,
                     "w": max(b[0] + b[2] for b in boxes) - x, "h": max(b[1] + b[3] for b in boxes) - y}
    return None


def shared_result(server, key, compute):
    """compute()'s result for key, computed once: a request for the same key while it runs waits for it,
    and later ones reuse it (keys hold what the result depends on, e.g. the build's version). This keeps
    many identical requests, e.g. from several open viewers, to one container run. At most 512 kept."""
    with server.results_lock:
        entry = server.results.get(key)
        mine = entry is None
        if mine:
            if len(server.results) >= 512:
                server.results.clear()
            entry = server.results[key] = {"done": threading.Event()}
    if not mine:
        entry["done"].wait()
        if "value" in entry:
            return entry["value"]
        return compute()  # (the first one failed)
    try:
        entry["value"] = compute()
    except BaseException:
        with server.results_lock:
            if server.results.get(key) is entry:
                del server.results[key]
        raise
    finally:
        entry["done"].set()
    return entry["value"]


def forward_request(server, query, user=None):
    """/forward?file=<relative path>&line=N[&clean=1]: forward search for the viewer's lists. The same
    lookup is made once per build: requests for it while it runs wait for its answer (shared_result).
    In shared mode, below editor, only for the notes the PDF shows them (shown_notes), whose rows are
    what they click: other lines would tell them which source files exist, and where."""
    paper_root = server.paper_root
    try:
        src, line = os.path.realpath(os.path.join(paper_root, query["file"][0])), int(query["line"][0])
    except (KeyError, ValueError):
        return 400, {"error": "expected file, line"}
    if not src.startswith(os.path.realpath(paper_root) + os.sep):
        return 404, {"error": "not a file in the paper"}
    if config.SHARED and user is not None and not at_least(user, "editor"):
        pdf = server.default_pdf
        shown = shown_notes(server, pdf, notes(paper_root, pdf) + margin_notes(paper_root, pdf))
        if not any(n.get("line") == line and os.path.realpath(os.path.join(paper_root, n.get("file") or "")) == src for n in shown):
            return 404, {"error": "that line is not in the PDF"}  # (as for any line not in it: no hint either way)
    clean = query.get("clean") == ["1"]
    pattern = os.path.join("clean", "*-clean.synctex.gz") if clean else "*-live.synctex.gz"
    builds = tuple(sorted((path, mtime(path)) for path in glob.glob(os.path.join(glob.escape(os.path.join(paper_root, config.BUILD)), pattern))))
    found = shared_result(server, ("forward", src, line, clean, builds), lambda: forward_target(paper_root, src, line, clean))
    if found:
        return 200, found[1]
    if missing := synctex_unavailable():
        return 503, {"error": missing}
    return 404, {"error": "that line is not in the PDF"}


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


def blank_notes(text, kinds):
    """text with its notes (uses of the note macros in kinds) blanked (line numbers kept)."""
    for m in reversed(list(note_use_re(kinds).finditer(text)) if kinds else []):
        end = brace_group(text, m.end())[1]
        text = text[:m.start()] + re.sub(r"[^\n]", " ", text[m.start():end]) + text[end:]
    return text


def notes(paper_root, pdf_name):
    """The inline notes in the build's files, in reading order: [{author, text, file, line, ...}]
    (plus note_fields, for editing them from the viewer).

    Note macros are the one-argument commands whose body goes through \\todotxt (this
    project's show/hide switch), e.g. \\todo and the per-author \\eb, \\ch; the author label
    is the bold "XX:" in the body.
    """
    files = project_tex_files(paper_root, fls_file(paper_root, pdf_name))
    texts = {path: read_tex(path) for path in files}
    kinds = note_kinds(texts.values())
    if not kinds:
        return []
    macros = project_macros(paper_root, fls_file(paper_root, pdf_name))
    found = []
    for path, text in texts.items():
        for m in note_use_re(kinds).finditer(text):
            body, end = brace_group(text, m.end())
            words = " ".join(tex_to_text(body, macros).split())
            found.append({"author": kinds[m.group(1)], "text": words[:400], "file": os.path.relpath(path, paper_root),
                          "line": text.count("\n", 0, m.start()) + 1, **note_fields(text, m, end)})
    return found


SYNCTEX_POINT_RE = re.compile(r"([gkx$])(\d+),(\d+):(-?\d+),(-?\d+)")  # glue, kerns, points, math


def synctex_points(synctex_gz, cache={}):
    """{(file, line): [(page, x, y)]}: where each source line's words, spaces, and math
    landed (points from the page's top-left, y at the baseline), from a build's SyncTeX file."""
    key = (synctex_gz, mtime(synctex_gz))
    if key not in cache:
        inputs, points, page = {}, {}, 0
        for record in synctex_records(synctex_gz):
            if record.startswith("Input:"):
                tag, _, path = record[6:].rstrip("\n").partition(":")
                inputs[tag] = os.path.normpath(path)
            elif record.startswith("{"):
                page = to_int(record[1:], page)
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
    [{author, text, file, line, points: [[page, x, y], ...], side, before, after, ...}] (plus note_fields).

    points are where the note's source line (or, if that line typeset nothing without its
    notes, the nearest line of the same paragraph) landed; side says which of those it is
    ("line", "before", "after"). before/after are the few typeset words around the note, which
    the viewer finds in the page text to put the note's caret between two words.
    """
    synctex_gz = clean_file(paper_root, pdf_name, ".synctex.gz")
    fls = fls_file(paper_root, pdf_name)
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
                          "before": before, "after": after, **note_fields(text, m, end)})
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


NOTE_ESCAPES = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "%": r"\%", "&": r"\&", "#": r"\#", "_": r"\_",
                "$": r"\$", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
# Characters pdflatex sets from UTF-8 without extra packages: ASCII, Latin-1, and the usual
# typographic ones. Others (✓, →, emoji) would stop the build, so notes refuse them.
TYPESETTABLE = set(map(chr, range(0x20, 0x7F))) | set(map(chr, range(0xA0, 0x100))) | set("‘’“”–—…•€")
NOTE_LOCK = threading.Lock()  # one read-modify-write of a source file at a time
CMD_RE = re.compile(r"\\([a-zA-Z@]+)\*?|\\.?", re.S)
# Commands whose (first) argument is not typeset as written: references, citations, labels,
# environment names, file names, colors, lengths.
NO_TEXT_ARG_RE = re.compile(r"[a-zA-Z]*ref|[a-zA-Z]*cite[a-zA-Z]*|label|begin|end|includegraphics|input|include|"
                            r"url|href|[hv]space|(?:text|page)?color|definecolor|setlength|addtolength")
HEADING_RE = re.compile(r"\\(?:part|chapter|(?:sub)*section|(?:sub)?paragraph|title)\*?\s*$")
PARAGRAPH_BREAK_RE = re.compile(r"\n[ \t]*\n")


class SourceChanged(Exception):
    """The source file changed between reading it and writing it back."""


def untypesettable(text):
    """An error message for the first character of text that pdflatex can't set, or None."""
    c = next((c for c in text if c not in TYPESETTABLE and not c.isspace()), None)
    return c and f"pdflatex can't set “{c}” (U+{ord(c):04X}); write it out in words"


def escape_note(text):
    """Plain text as a note macro's argument: one line, TeX's special characters escaped."""
    return "".join(NOTE_ESCAPES.get(c, c) for c in " ".join(text.split()))


def plain_note(tex):
    """The plain text that escape_note turns into tex, or None if tex has other TeX in it."""
    out, i = [], 0
    while i < len(tex):
        if tex[i] == "\\":
            c = next((c for c, e in NOTE_ESCAPES.items() if tex.startswith(e, i)), None)
            if c is None:
                return None
            out.append(c)
            i += len(NOTE_ESCAPES[c])
        elif tex[i] in NOTE_ESCAPES:
            return None
        else:
            out.append(tex[i])
            i += 1
    return " ".join("".join(out).split())


def note_fields(text, m, end):
    """What the viewer needs to edit or delete the note macro use m (body up to end) in text."""
    body = text[m.end() + 1:end - 1]
    return {"macro": m.group(1), "tex": " ".join(body.split()), "plain": plain_note(body),
            "protected": bool(config.PROTECTED_RE and config.PROTECTED_RE.search(text[m.start():end]))}


def paper_note_kinds(paper_root, pdf_name):
    """{note macro: author label} of the document's note macros (note_kinds), from the files its last
    successful build read."""
    return note_kinds(read_tex(p) for p in project_tex_files(paper_root, fls_file(paper_root, pdf_name)))

def note_authors(paper_root, pdf_name):
    """[{macro, author}]: the note macros the viewer can write, from their definitions."""
    return [{"macro": macro, "author": author} for macro, author in paper_note_kinds(paper_root, pdf_name).items()]


def plain_key(text):
    """Letters and digits of text, lowercased and without accents: PDF text and source compare on these."""
    return "".join(c for c in unicodedata.normalize("NFKD", text).casefold() if c.isalnum())


def source_letters(raw, lo, hi):
    """(key, at): plain_key of raw[lo:hi] as it would typeset, roughly (without comments, command
    names, or the arguments of NO_TEXT_ARG_RE commands), with at[i] the index in raw of key[i]."""
    key, at, i = [], [], lo
    while i < hi:
        c = raw[i]
        if c == "%":  # to the end of the line
            i = raw.find("\n", i)
            i = hi if i < 0 else i
        elif c == "\\":
            m = CMD_RE.match(raw, i)
            i = m.end()
            if m.group(1) and NO_TEXT_ARG_RE.fullmatch(m.group(1)):
                braced = False
                while i < hi:
                    if raw[i].isspace():
                        i += 1
                    elif raw[i] == "[":
                        i = raw.find("]", i)
                        i = hi if i < 0 else i + 1
                    elif raw[i] == "{" and not braced:
                        i, braced = brace_group(raw, i)[1], True
                    else:
                        break
        else:
            for k in plain_key(c):
                key.append(k)
                at.append(i)
            i += 1
    return "".join(key), at


def token_end(raw, i):
    """Index after the rest of the token raw[i - 1] belongs to: on through punctuation, closing
    braces, and attached commands such as ~\\cite{...}, to the next space outside braces and math;
    never past a comment, an alignment tab &, or a row break \\\\."""
    depth = 0
    math = raw.count("$", raw.rfind("\n", 0, i) + 1, i) % 2 == 1
    while i < len(raw):
        c = raw[i]
        if c == "\\":
            if raw.startswith("\\\\", i):
                break
            i = CMD_RE.match(raw, i).end()
            continue
        if c in "%&" or (c.isspace() and depth == 0 and not math):
            break
        if c == "$":
            math = not math
        elif c == "{":
            depth += 1
        elif c == "}":
            depth = max(0, depth - 1)
        i += 1
    return i


def note_site(raw, line, selection):
    """(index, dropped): where in raw a note on the selected PDF text goes, given the line SyncTeX
    put the selection's end on: after the token where the selection's last words end. Trailing
    words not found in the source (math, references) are dropped (dropped counts them); index is
    None if none are found."""
    words = [k for k in map(plain_key, selection.split()) if k]
    starts = [0] + [m.end() for m in re.finditer("\n", raw)]
    line = min(max(line, 1), len(starts))
    for radius in (3, 12, 40):  # SyncTeX's line is usually right; look further only if need be
        lo = starts[max(0, line - 1 - radius)]
        hi = starts[line + radius] if line + radius < len(starts) else len(raw)
        key, at = source_letters(raw, lo, hi)
        for end in range(len(words), 0, -1):
            for span in (4, 3, 2, 1):
                chunk = "".join(words[end - span:end]) if span <= end else ""
                # (one short word alone matches too easily, unless it is the whole selection)
                if len(chunk) < 3 or (span == 1 and len(words) > 1 and len(chunk) < 6):
                    continue
                hits = [m.start() for m in re.finditer(re.escape(chunk), key)]
                if hits:
                    best = min(hits, key=lambda h: abs(bisect.bisect_right(starts, at[h]) - line))
                    return token_end(raw, at[best + len(chunk) - 1] + 1), len(words) - end
    return None, 0


def enclosing_group(text, lo, pos):
    """Index of the innermost { still open at pos, scanning from lo, or None."""
    stack = []
    for j in range(lo, pos):
        if j and text[j - 1] == "\\":
            continue
        if text[j] == "{":
            stack.append(j)
        elif text[j] == "}" and stack:
            stack.pop()
    return stack[-1] if stack else None


def safe_site(raw, pos, note_macros=()):
    """(index, protected): where a note meant for raw[pos] goes. Out of a heading's argument or
    another note (after it), and out of text marked LATEX_LIVE_PROTECTED: after the caption or other
    group holding the marker, or after a marker that ends the paragraph or starts the next one."""
    text = blank_comments(raw)  # (a commented-out marker protects nothing)
    breaks = list(PARAGRAPH_BREAK_RE.finditer(text))
    start = max([b.end() for b in breaks if b.end() <= pos], default=0)
    following = [b for b in breaks if b.start() >= pos]
    end = following[0].start() if following else len(text)
    note_re = re.compile(r"\\(?:%s)\s*$" % "|".join(map(re.escape, note_macros))) if note_macros else None
    group, outside = enclosing_group(text, start, pos), None
    while group is not None:  # innermost to outermost
        if HEADING_RE.search(text, start, group) or (note_re and note_re.search(text, start, group)):
            outside = group
        group = enclosing_group(text, start, group)
    if outside is not None:
        pos = brace_group(text, outside)[1]
    if not config.PROTECTED_RE:
        return pos, False
    markers = list(config.PROTECTED_RE.finditer(text, start, end))
    if following:  # a marker opening the next paragraph protects this one
        m = config.PROTECTED_RE.match(text, re.compile(r"\s*").match(text, following[0].end()).end())
        markers += [m] if m else []
    sites = []
    for m in markers:
        group = enclosing_group(text, start, m.start())
        sites.append(brace_group(text, group)[1] if group is not None else m.end())
    site = min((s for s in sites if s >= pos), default=None)
    return (site, True) if site is not None else (pos, False)


def read_source(path):
    """A source file's exact text (line endings kept)."""
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def write_source(path, old, new):
    """Write new over a source file in one step (latexmk never reads half of it), unless it no longer
    holds old: an editor saved it in the meantime."""
    path = os.path.realpath(path)
    if read_source(path) != old:
        raise SourceChanged()
    tmp = f"{path}.latex-live-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(new)
    shutil.copymode(path, tmp)
    os.replace(tmp, path)


def note_request(server, req, user):
    """POST /note, from user (a principal): add a note at a selection in the PDF, or edit, resolve, reply
    to, or delete one. Returns (status, JSON).

    Every request names pdf (the published PDF), whose last build lists the paper's .tex files.
    add: page, x, y (PDF points from the page's top-left, at the selection's end), clean (the
    notes-free PDF), selection (its text), macro, and text (plain; escaped here).
    edit / resolve / reply / delete: source (a .tex path in the paper), line, macro, and tex (the
    note's argument as /notes and /margin gave it); edit also text (plain) or latex (written as is);
    resolve also who (the resolver's note macro, for the initials) and text (the reason); reply also
    who (the replier's note macro) and text (plain).

    A coauthor in shared mode (user, a principal other than the owner) writes as their own note
    macro whatever macro or who the request names, edits and deletes only their own notes, writes
    LaTeX (latex) only as an editor, and only in the shared document; anyone who may comment may
    resolve anyone's note. While the shared copy can't take a write (a git merge in progress, or
    others' edits there can't be committed first), 503 (updating).
    """
    root = server.paper_root
    try:
        action, pdf = req.get("action", "add"), req["pdf"]
        if not isinstance(pdf, str) or not pdf.endswith(".pdf") or os.path.basename(pdf) != pdf:
            return 400, {"error": "bad pdf"}
        coauthor = user["role"] != "owner"
        if coauthor and not at_least(user, "commenter"):
            return 403, {"error": "viewers can read the notes but not write them"}
        if coauthor and pdf != server.default_pdf:
            return 403, {"error": f"this preview shares {server.default_pdf} only"}
        if coauthor and "latex" in req and user["role"] == "commenter":
            return 403, {"error": "commenters write notes as plain text (LaTeX takes the editor role)"}
        files = {os.path.realpath(p): p for p in project_tex_files(root, fls_file(root, pdf))}
        kinds = note_kinds(read_tex(p) for p in files.values())
        if coauthor and user["macro"] not in kinds:
            return 403, {"error": f"your note macro \\{user['macro']} is not defined in the paper" if user["macro"]
                         else "the users file gives you no note macro, so you can't write notes"}
        if coauthor and action in ("add", "resolve", "reply"):  # (as themselves, whoever the request says)
            req = {**req, ("macro" if action == "add" else "who"): user["macro"]}
        macro = req["macro"]
        if macro not in kinds:
            return 400, {"error": f"\\{macro} is not a note macro of this paper"}
        if coauthor and action in ("edit", "delete") and macro != user["macro"]:
            return 403, {"error": "you can edit or delete only your own notes"}
        if action == "add":
            found = inverse_search(root, pdf, int(req["page"]), float(req["x"]), float(req["y"]), bool(req.get("clean")))
            if isinstance(found[0], int):
                return found[0], {"error": found[1]}
            src, line = found
        elif action in ("edit", "resolve", "reply", "delete"):
            src, line = os.path.join(root, req["source"]), int(req["line"])
            if action in ("resolve", "reply") and req["who"] not in kinds:
                return 400, {"error": f"\\{req['who']} is not a note macro of this paper"}
        else:
            return 400, {"error": f"unknown action {action}"}
        src = os.path.realpath(src)
        rel = os.path.relpath(src, root)
        if src not in files:
            return 403, {"error": f"{rel} is not one of the paper's .tex files"}
        with NOTE_LOCK:
            if paused(server) or not commit_others(server, user, rel):  # (LATEX_LIVE_COMMIT: others' edits there are theirs)
                return updating()
            raw = read_source(src)
            if action == "add":
                result = add_note(src, rel, raw, line, macro, kinds[macro], req, list(kinds))
            else:
                result = change_note(src, rel, raw, line, macro, action, req, kinds)
            if result[0] == 200:
                remember_write(server, user, rel, "notes")  # (to be committed as theirs: LATEX_LIVE_COMMIT)
                wrote(server, user, rel)
        return result
    except UnicodeDecodeError:
        return 415, {"error": "the source file is not UTF-8"}
    except (KeyError, TypeError, ValueError) as e:
        return 400, {"error": f"bad request ({type(e).__name__}: {e})"}
    except SourceChanged:
        return 409, {"error": "the source file changed meanwhile (saved in an editor?); try again"}


def add_note(src, rel, raw, line, macro, author, req, note_macros):
    """Write \\<macro>{text} after the selected words near line of raw (the text of src)."""
    typed = unicodedata.normalize("NFC", str(req["text"]))
    if error := untypesettable(typed):
        return 400, {"error": error}
    text = escape_note(typed)
    if not text:
        return 400, {"error": "empty note"}
    pos, dropped = note_site(raw, line, str(req.get("selection", "")))
    if pos is None:
        return 422, {"error": f"couldn't find the selected words near {rel}:{line}; select a few words of plain text"}
    site, protected = safe_site(raw, pos, note_macros)
    write_source(src, raw, raw[:site] + f" \\{macro}{{{text}}}" + raw[site:])
    return 200, {"source": rel, "line": raw.count("\n", 0, site) + 1, "author": author, "protected": protected,
                 "dropped": dropped, "macro": macro, "tex": text}


def cut_note(raw, start, end):
    """(new, after): raw without the note at raw[start:end], and the index in new where the line
    after the note's begins. A note alone on its line(s) takes the line with it; otherwise the
    space before it goes too, as add_note wrote it."""
    bol, eol = raw.rfind("\n", 0, start) + 1, raw.find("\n", end)
    eol = len(raw) if eol < 0 else eol
    if not raw[bol:start].strip() and not raw[end:eol].strip():  # the note's own line: remove the line
        return (raw[:bol] + raw[eol + 1:], bol) if eol < len(raw) else (raw[:max(0, bol - 1)], max(0, bol - 1))
    spaced = start > 0 and raw[start - 1] in " \t" and (end == len(raw) or raw[end].isspace())
    new = raw[:start - spaced] + raw[end:]
    return new, new.find("\n", start - spaced) + 1 or len(new)


def change_note(src, rel, raw, line, macro, action, req, kinds):
    """Edit, resolve, reply to, or delete the \\<macro>{tex} use nearest line of raw (the text of src);
    kinds is {note macro: author label}."""
    text = re.sub(r"(?<!\\)%.*", lambda m: " " * len(m.group()), raw)  # (notes in comments don't count)
    want = " ".join(str(req["tex"]).split())
    uses = []
    for m in re.finditer(r"\\(%s)\s*(?=\{)" % re.escape(macro), text):
        end = brace_group(text, m.end())[1]
        if " ".join(text[m.end() + 1:end - 1].split()) == want:
            uses.append((abs(text.count("\n", 0, m.start()) + 1 - line), m.start(), m.end(), end))
    if not uses:
        return 409, {"error": f"this note is no longer in {rel} as shown; wait for the rebuild and try again"}
    _, start, brace, end = min(uses)  # the one nearest its line (lines move as the file is edited)
    if config.PROTECTED_RE and config.PROTECTED_RE.search(raw[start:end]):
        return 403, {"error": "this note marks protected text; edit it in the source if you mean to"}
    out = {"source": rel, "line": raw.count("\n", 0, start) + 1}
    if action == "delete":
        new = cut_note(raw, start, end)[0]
    elif action == "edit":
        typed = unicodedata.normalize("NFC", str(req["latex"] if "latex" in req else req["text"]))
        if error := untypesettable(typed):
            return 400, {"error": error}
        body = typed if "latex" in req else escape_note(typed)
        if not body.strip():
            return 400, {"error": "empty note (delete it instead)"}
        if brace_group("{" + body + "}", 0)[1] != len(body) + 2 or re.search(r"(?<!\\)%|\n[ \t]*\n", body):
            return 400, {"error": "unbalanced braces, a % comment, or a blank line in the note's LaTeX"}
        new = raw[:brace + 1] + body + raw[end - 1:]
        out["tex"] = " ".join(body.split())
    else:  # resolve, reply: typed text, on one line
        typed = " ".join(unicodedata.normalize("NFC", str(req["text"])).split())
        if error := untypesettable(typed):
            return 400, {"error": error}
        if not typed:
            return 400, {"error": "say why it is resolved" if action == "resolve" else "empty reply"}
        who = req["who"]
        if action == "reply":  # right after the note (or after the protected text it sits in)
            site, out["protected"] = safe_site(raw, end, list(kinds))
            out.update(line=raw.count("\n", 0, site) + 1, author=kinds[who], macro=who, tex=escape_note(typed))
            new = raw[:site] + f" \\{who}{{{out['tex']}}}" + raw[site:]
        else:  # commented out on its own lines after the note's line, every line with %, with the
            # line's indentation; a comment-only line doesn't end a paragraph
            bol = raw.rfind("\n", 0, start) + 1
            indent = re.match(r"[ \t]*", raw[bol:]).group()
            nl = "\r\n" if "\r\n" in raw[bol:raw.find("\n", end) + 1 or len(raw)] else "\n"
            lines = [f"RESOLVED {time.strftime('%Y-%m-%d')} ({kinds[who]}): {typed}"] + raw[start:end].split("\n")
            block = "".join(f"{indent}% {row.strip()}".rstrip() + nl for row in lines)
            new, after = cut_note(raw, start, end)
            new = new[:after] + ("" if after == 0 or new[after - 1] == "\n" else nl) + block + new[after:]
    write_source(src, raw, new)
    return 200, out


# The browser editor: GET /files lists the sources it opens, GET /file reads one, and POST /file
# saves one, merged (git merge-file) with what was saved meanwhile since the version the edits
# started from. A file's version is a hash of its bytes; the editor works with \n line ends, and a
# file that uses \r\n gets them back on save. Saves go through write_source under NOTE_LOCK, as
# notes do, so the two never overwrite each other.
EDITABLE_EXTS = (".tex", ".bib", ".sty", ".cls")
EDITABLE_SIZE = 4_000_000  # bytes (a save sends the text, and sometimes the base text too: POST_LIMITS)
MERGE_LABELS = ("saved meanwhile", "base", "your edit")  # (git's conflict markers name the two sides)


def editable_real(paper_root, rel):
    """The real path of rel, a path relative to the paper root, if the browser editor may open it,
    else None: an existing regular .tex, .bib, .sty or .cls file of at most 4 MB in the paper root,
    not in the build directory, in .git or in another folder whose name starts with a dot, and not a
    dot file; all of that also once symbolic links are resolved (a link to a file elsewhere is not)."""
    if not isinstance(rel, str) or not rel or "\0" in rel or os.path.isabs(rel):
        return None
    root = os.path.realpath(paper_root)
    real = os.path.realpath(os.path.join(root, rel))
    for path in (os.path.normpath(rel), os.path.relpath(real, root)):
        parts = path.split(os.sep)
        if (any(part.startswith(".") for part in parts) or os.path.splitext(path)[1].lower() not in EDITABLE_EXTS
                or path == config.BUILD or path.startswith(os.path.join(config.BUILD, ""))):
            return None
    try:
        st = os.stat(real)
    except (OSError, ValueError):
        return None
    return real if stat.S_ISREG(st.st_mode) and st.st_size <= EDITABLE_SIZE else None


def editable_files(server, fresh=True):
    """The files the browser editor opens, {path relative to the paper root: real path}, the main file
    first, then by path: those of the document's files that editable_real allows, which are the files
    its last build read (fls_file), its main file, the .bib files in the paper root and in the folders
    in it (as find_bib_entry looks for them), and the files its \\input and \\include commands name
    (source_tree, so they show before the first build). Without fresh, a list up to 2 s old does
    (status polls)."""
    cached = server.editable
    if not fresh and cached and time.monotonic() - cached[0] < 2 and cached[1] == server.default_pdf:
        return cached[2]
    root, doc = server.paper_root, server.default_pdf[:-4]
    main = f"{doc}.tex"
    names = [main, *project_inputs(root, fls_file(root, server.default_pdf))]
    names += [os.path.relpath(p, root) for pattern in ("*.bib", os.path.join("*", "*.bib"))
              for p in glob.glob(os.path.join(glob.escape(root), pattern))]
    names += [os.path.relpath(os.path.normpath(p), root) for p in source_tree(root, doc)]
    found = {}
    for rel in sorted({os.path.normpath(name) for name in names}, key=lambda rel: (rel != main, rel)):
        if real := editable_real(root, rel):
            found[rel] = real
    server.editable = (time.monotonic(), server.default_pdf, found)
    return found


def files_listing(server):
    """GET /files: {"files": [{"path", "size"}], "main"}, the files the browser editor opens."""
    files = []
    for rel, real in editable_files(server).items():
        try:
            files.append({"path": rel, "size": os.stat(real).st_size})
        except OSError:
            pass
    return {"files": files, "main": f"{server.default_pdf[:-4]}.tex"}


def file_version(data):
    """A source file's version: the first 20 hex digits of the SHA-256 of its bytes."""
    return hashlib.sha256(data).hexdigest()[:20]


def editor_text(raw):
    """(text, eol): a source file's text as the browser editor has it, with \\n line ends, and the line
    end the file uses, \\r\\n if most of its lines end so (a save puts it back), else \\n."""
    crlf = raw.count("\r\n")
    return raw.replace("\r\n", "\n"), "\r\n" if crlf and 2 * crlf >= raw.count("\n") else "\n"


def remember_version(server, rel, version, text):
    """Keep text as version of rel, the last 8 versions of each file the editor read or saved, so that
    a save from an older version can be merged with what was saved since (save_request). At most
    64 MB in all: the oldest go first."""
    with server.versions_lock:
        kept = server.versions  # {(rel, version): text}, oldest first
        kept.pop((rel, version), None)
        kept[(rel, version)] = text
        for key in [key for key in kept if key[0] == rel][:-8]:
            del kept[key]
        while len(kept) > 1 and sum(map(len, kept.values())) > 64_000_000:
            kept.popitem(last=False)


def source_status(server, rel):
    """status?open=<rel>: {"path", "version"} of a file the browser editor has open, so that it sees a
    change on disk within one poll; None if the editor doesn't open that path. The version is cached
    by the file's (path, mtime, size, inode): an atomic replace (write_source) is a new inode."""
    rel = os.path.normpath(rel or ".")
    real = editable_files(server, fresh=False).get(rel) and editable_real(server.paper_root, rel)
    if not real:
        return None
    try:
        return {"path": rel, "version": cached_version(real)}
    except OSError:
        return None


def cached_version(path, cache={}):
    """file_version of the file at path, read again only when its stat changes (see source_status)."""
    st = os.stat(path)
    if (version := cache.get((path, st.st_mtime_ns, st.st_size, st.st_ino))) is not None:
        return version
    with open(path, "rb") as f:
        st, data = os.fstat(f.fileno()), f.read()  # (the stat of the bytes read, should the file change now)
    if len(cache) >= 64:
        cache.clear()
    version = cache[(path, st.st_mtime_ns, st.st_size, st.st_ino)] = file_version(data)
    return version


def file_request(server, query):
    """GET /file?path=<rel>: {"path", "text" (\\n line ends), "version", "eol"} of a file the browser
    editor opens; 404 for any other path, whether or not it exists, and 415 for a file that is not
    UTF-8."""
    rel = os.path.normpath(query.get("path", [""])[0] or ".")
    real = editable_files(server).get(rel)
    if not real:
        return 404, {"error": "not a file the editor opens"}
    try:
        with open(real, "rb") as f:
            data = f.read()
        text, eol = editor_text(data.decode("utf-8"))
    except OSError:
        return 404, {"error": "not a file the editor opens"}
    except UnicodeDecodeError:
        return 415, {"error": f"{rel} is not UTF-8, so the editor can't open it"}
    version = file_version(data)
    remember_version(server, rel, version, text)
    return 200, {"path": rel, "text": text, "version": version, "eol": eol}


def save_request(server, req, user):
    """POST /file {path, base, text, base_text?, force?, editor?}: save a file from the browser editor,
    as user (a principal). Returns (status, JSON).

    base is the version the edits in text (the whole new text, \\n line ends) started from. If the
    file is still at base, or with force, text is written. Otherwise the edits are merged with what
    was saved since (merge_texts), from base's text: base_text, or the server's copy of that version
    (remember_version). Then 200 {"version", "merged", "text"}: text is what is now in the file when
    that is not the text sent (merged: true); 409 {"conflict": true, "version", "text", "merged"} when
    both changed the same lines, with nothing written (the file's version and text, and git's merge
    with conflict markers; plus "by", the name of who saved the file as it is now, when that was
    through the server); or 409 {"stale": true, "version", "text"} without base's text: send it again
    with base_text. While the shared copy can't take a write, 503 (updating)."""
    rel, text, base, base_text, force, editor = (req.get(key) for key in ("path", "text", "base", "base_text", "force",
                                                                          "editor"))
    if not (isinstance(rel, str) and isinstance(text, str) and isinstance(base, (str, type(None)))
            and isinstance(base_text, (str, type(None))) and isinstance(force, (bool, type(None)))
            and isinstance(editor, (str, type(None))) and len(editor or "") <= 64):
        return 400, {"error": "expected path, base and text (and maybe base_text, force, editor)"}
    rel = os.path.normpath(rel or ".")
    real = editable_files(server).get(rel)
    if not real:
        return 404, {"error": "not a file the editor opens"}
    try:
        text, base_text = (t if t is None else t.replace("\r\n", "\n") for t in (text, base_text))
        if len(text.encode("utf-8")) > EDITABLE_SIZE:
            return 413, {"error": "the editor saves files of up to 4 MB"}
        if base_text is not None:
            base_text.encode("utf-8")
    except UnicodeEncodeError:
        return 400, {"error": "the text is not valid Unicode"}
    for _ in range(3):  # (again when another program writes the file between our read and write)
        try:
            with NOTE_LOCK:
                return (updating() if paused(server)
                        else save_file(server, user, rel, real, text, base, base_text, bool(force), editor))
        except SourceChanged:
            continue
        except UnicodeDecodeError:
            return 415, {"error": f"{rel} is not UTF-8, so the editor can't save it"}
        except OSError as e:
            return 500, {"error": f"could not save {rel}: {e.strerror or e}"}
    return 409, {"error": f"{rel} keeps changing on disk; try again"}


def save_file(server, user, rel, real, sent, base, base_text, force, editor=None):
    """save_request's read, merge and write of rel (at real) with the text sent, for user, under
    NOTE_LOCK: (status, JSON).

    When the file is just as this editor's (the viewer page's own name for its saves, and this user's) last
    save of rel left it, though the edits start from another version, the answer to that save got lost (the
    editor then keeps the older base), and the new text builds on what was sent then: it is merged
    from that text instead, so that the editor's own save doesn't count as someone else's change, and
    an edit undone since (back to the older base) is undone in the file too. Another page's save never
    counts as this one's."""
    with open(real, "rb") as f:
        data = f.read()
    raw = data.decode("utf-8")
    current, eol = editor_text(raw)
    version = file_version(data)
    remember_version(server, rel, version, current)
    written = os.path.relpath(real, os.path.realpath(server.paper_root))
    text = sent
    if not force and base != version:
        own = editor and server.last_saves.get((user["id"], rel, editor))  # (the version it left, and that of the text sent)
        with server.versions_lock:
            base_text = server.versions.get((rel, base)) if base_text is None else base_text
            if own and own[0] == version:  # (this editor's last save, whose answer got lost)
                base_text = current if own[1] == version else server.versions.get((rel, own[1]), base_text)
        if base_text is None:
            return 409, {"stale": True, "version": version, "text": current,
                         "error": f"{rel} changed meanwhile, and the text your edits started from is not known here"}
        try:
            conflicts, text = merge_texts(current, base_text, sent)
        except (OSError, RuntimeError, UnicodeDecodeError, subprocess.SubprocessError) as e:
            return 422, {"error": f"could not merge your edits with the changes saved meanwhile ({e})"}
        if conflicts:
            by = server.last_writer.get(written)
            return 409, {"conflict": True, "version": version, "text": current, "merged": text,
                         **({"by": by[1]} if by and by[0] == version else {}),
                         "error": f"your edits and the changes saved meanwhile touch the same lines of {rel}"}
    new = text.replace("\n", eol) if eol != "\n" else text
    encoded = new.encode("utf-8")
    if len(encoded) > EDITABLE_SIZE:
        return 413, {"error": "the editor saves files of up to 4 MB"}
    new_version = file_version(encoded)
    if new != raw:
        if not commit_others(server, user, written):  # (LATEX_LIVE_COMMIT: what others wrote there is theirs)
            return updating()
        write_source(real, raw, new)
        remember_write(server, user, written, "edit")
        wrote(server, user, written, new_version)
    remember_version(server, rel, new_version, text)
    sent_version = new_version
    if text != sent:  # (merged: keep the text they sent too, should the answer get lost)
        sent_version = file_version((sent.replace("\n", eol) if eol != "\n" else sent).encode("utf-8"))
        remember_version(server, rel, sent_version, sent)
    if editor:
        server.last_saves[(user["id"], rel, editor)] = (new_version, sent_version)
        server.last_saves.move_to_end((user["id"], rel, editor))
        while len(server.last_saves) > 256:
            server.last_saves.popitem(last=False)
    return 200, {"version": new_version, "merged": text != sent, **({"text": text} if text != sent else {})}


def merge_texts(current, base, new):
    """Merge the edits from base to new into current (git merge-file, on files in a private temporary
    folder): (the number of conflicts, the merged text, with git's conflict markers where both sides
    changed the same lines: the two sides only, whatever style the user's git config asks for, which
    the editor's conflict handling expects). RuntimeError if git can't merge them (e.g. binary data)."""
    with tempfile.TemporaryDirectory(prefix="latex-live-merge-") as tmp:
        paths = [os.path.join(tmp, name) for name in ("current", "base", "new")]
        for path, text in zip(paths, (current, base, new)):
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text)
        labels = [arg for label in MERGE_LABELS for arg in ("-L", label)]
        r = subprocess.run(["git", "-c", "merge.conflictStyle=merge", "merge-file", "-p", "-q", *labels, *paths],
                           capture_output=True, cwd=tmp, timeout=60)
    if not 0 <= r.returncode <= 127:  # (git's count of conflicts stops at 127; it fails with 255)
        raise RuntimeError(r.stderr.decode(errors="replace").strip() or f"git merge-file failed ({r.returncode})")
    return r.returncode, r.stdout.decode("utf-8")


def wrote(server, user, rel, version=None):
    """Remember that user (a principal) wrote rel (relative to the paper root) through the server,
    leaving it at version (None: as it is now), so that a conflict over that version can say who saved
    it (save_file's "by")."""
    if version is None:
        try:
            version = cached_version(os.path.join(os.path.realpath(server.paper_root), rel))
        except OSError:
            return
    server.last_writer[rel] = (version, user["name"] or (owner_name(server) if user["role"] == "owner" else user["id"]))


# Auto-commit (shared mode, LATEX_LIVE_COMMIT=1): what the server writes for each user (their saves,
# their notes) is committed to git as theirs, one commit per pause of theirs (LATEX_LIVE_COMMIT_IDLE
# seconds), only the files the server wrote for them, with a "Latex-Live-Shared: <user id>" trailer;
# the owner commits everything pending at once with POST /commit. A commit takes a whole file, so
# before the server writes a file for one user, what it wrote there for others is committed first
# (commit_others): each commit holds one person's edits. Commits run under NOTE_LOCK, as writes do,
# so no write lands in a file while it is committed. They wait while git is busy (a merge, rebase or
# cherry-pick, or another git command holding the index). Who wrote what, not committed yet, is kept
# in the state folder too (save_pending), so that a restart, even after a crash, still commits it as
# theirs; edits found uncommitted at the start that no one's writes account for are committed then,
# as the owner's (UNATTRIBUTED), rather than in the next coauthor's commit of that file.
UNATTRIBUTED = "(unattributed)"  # (the pending entry of those; no user has this id: USER_ID_RE)


def remember_write(server, user, rel, kind):
    """Remember that the server wrote rel (relative to the paper root) for user (a principal), kind
    "edit" (a save) or "notes", to commit it as theirs once they pause (commit_loop)."""
    if not server.autocommit:
        return
    with server.pending_lock:
        server.write_seq += 1
        entry = server.pending.setdefault(user["id"], {"paths": {}})
        old = entry["paths"].get(rel)
        entry.update(user=user, due=time.monotonic() + config.COMMIT_IDLE)
        entry["paths"][rel] = ("edit" if kind == "edit" or (old and old[0] == "edit") else kind, server.write_seq)
        save_pending(server)


def commit_others(server, user, rel):
    """Before the server writes rel for user (holding NOTE_LOCK): commit what it wrote there for
    anyone else and hasn't committed yet, as theirs, so that the next commit of rel is user's alone.
    False if that can't be done within 2 s (git busy: another git command holds its index; or the
    commit failed, which commit_pending has said why): the caller then writes nothing (updating), so
    that no one's edits are ever committed as someone else's."""
    if not server.autocommit:
        return True
    end = time.monotonic() + 2
    while True:
        with server.pending_lock:
            others = [uid for uid, entry in server.pending.items() if uid != user["id"] and rel in entry["paths"]]
        if not others:
            return True
        commit_pending(server, others, only=rel)
        with server.pending_lock:
            if not any(uid in server.pending and rel in server.pending[uid]["paths"] for uid in others):
                return True
        if time.monotonic() > end:
            return False
        time.sleep(0.2)


def paused(server):
    """Why the shared copy takes no writes now: git in the middle of a merge, rebase, cherry-pick or
    revert there (git_operation), whose result would take the writes, or whose abort would lose them;
    else None. Only in shared mode: the owner's own previews write as they always did."""
    return git_operation(server) if config.SHARED else None


def updating():
    """The answer to a write that the shared copy can't take now (paused, commit_others): 503, which
    the viewer tries again, keeping the text meanwhile (the editor's, the note box's)."""
    return 503, {"error": "the shared copy is being updated; try again in a moment", "retry": True}


def save_pending(server):
    """Keep who wrote which files, not committed yet, in the state folder (server.pending_file), so
    that a restart, even after a crash, still commits them as theirs (load_pending). The caller holds
    pending_lock."""
    entries = {uid: {"user": {key: entry["user"].get(key) for key in ("id", "name", "email", "macro", "role")},
                     "paths": {rel: kind for rel, (kind, _) in entry["paths"].items()}}
               for uid, entry in server.pending.items() if entry["paths"]}
    path = server.pending_file
    try:
        if not entries:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            return
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
            json.dump({"root": server.paper_root, "pending": entries}, f)
        os.replace(tmp, path)
    except OSError as e:
        commit_notice(server, f"could not keep the list of uncommitted edits in {path} ({e})")


def load_pending(server):
    """The writes the last run left uncommitted (save_pending), pending again, to be committed as
    theirs right away: those of valid users (as load_users checks them) to files that are editable
    (editable_files; the state folder could have been changed meanwhile)."""
    root = os.path.realpath(server.paper_root)
    editable = set(editable_files(server).values())
    data = read_json(server.pending_file)
    saved = data.get("pending") if isinstance(data, dict) else None
    with server.pending_lock:
        for uid, entry in (saved or {}).items() if isinstance(saved, dict) else ():
            user, paths = (entry.get("user"), entry.get("paths")) if isinstance(entry, dict) else (None, None)
            if not (isinstance(user, dict) and user.get("id") == uid and user.get("role") in ROLES and isinstance(paths, dict)
                    and (uid == UNATTRIBUTED or USER_ID_RE.fullmatch(uid))):
                continue
            fields = {key: user.get(key) if isinstance(user.get(key), str) else None for key in ("id", "name", "email", "macro", "role")}
            if any(value and (len(value) > 200 or re.search(r"[\x00-\x1f\x7f<>]", value)) for value in fields.values()):
                continue  # (as load_users checks the users file: they go into the commit's author and subject)
            kept = {}
            for rel, kind in paths.items():
                if isinstance(rel, str) and kind in ("edit", "notes") and not os.path.isabs(rel) \
                        and os.path.realpath(os.path.join(root, rel)) in editable:
                    server.write_seq += 1
                    kept[os.path.relpath(os.path.realpath(os.path.join(root, rel)), root)] = (kind, server.write_seq)
            if kept:
                user = fields
                server.pending[uid] = {"user": {**user, "via": "restart"}, "paths": kept, "due": time.monotonic()}
        if server.pending:
            print("=== latex-live: the last run left edits uncommitted; committing them as theirs: "
                  + "; ".join(f"{e['user']['name'] or uid}: {', '.join(sorted(e['paths']))}" for uid, e in server.pending.items()),
                  flush=True)


def claim_leftovers(server):
    """At the start: the editable files that git tracks and that hold uncommitted edits no pending write
    accounts for (made before a crash, or outside the server), as one pending entry (UNATTRIBUTED),
    committed as the owner's before anyone writes those files again (commit_others)."""
    root = os.path.realpath(server.paper_root)
    with server.pending_lock:
        claimed = {rel for entry in server.pending.values() for rel in entry["paths"]}
    files = sorted({os.path.relpath(real, root) for real in editable_files(server).values()} - claimed)
    if not files:
        return
    git = ["git", "-C", server.paper_root, "--literal-pathspecs"]
    run = lambda args: subprocess.run(git + args, capture_output=True, encoding="utf-8", errors="surrogateescape",
                                      env=git_read_env(), timeout=60)
    r = run(["diff", "--name-only", "-z", "--relative", "HEAD", "--", *files])
    if r.returncode:  # (no commit yet: nothing to compare with)
        return
    changed, tracked = set(r.stdout.split("\0")), set(run(["ls-files", "-z", "--", *files]).stdout.split("\0"))
    left = [rel for rel in files if rel in changed and rel in tracked]
    if not left:
        return
    with server.pending_lock:
        entry = server.pending.setdefault(UNATTRIBUTED, {"paths": {}})
        entry.update(user={"id": UNATTRIBUTED, "name": None, "email": None, "macro": None, "role": "owner", "via": "start"},
                     due=time.monotonic())
        for rel in left:
            server.write_seq += 1
            entry["paths"][rel] = ("edit", server.write_seq)
        save_pending(server)
    print(f"=== latex-live: no one's saves account for the uncommitted edits in {', '.join(left)}; committing them as the"
          " owner's (Latex-Live-Shared: unattributed)", flush=True)


def start_commits(server):
    """Shared mode with LATEX_LIVE_COMMIT=1: commit the users' writes from now on (commit_loop), if
    the paper is in a git checkout. First, before any write is served (NOTE_LOCK), what the last run
    left uncommitted: its users' writes as theirs (load_pending), the rest as the owner's
    (claim_leftovers)."""
    if not config.COMMIT:
        return
    try:
        r = subprocess.run(["git", "-C", server.paper_root, "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True)
    except OSError:
        return print("=== latex-live: git is not installed, so edits are not committed (LATEX_LIVE_COMMIT)", flush=True)
    if r.stdout.strip() != "true":
        return print("=== latex-live: the paper is not in a git checkout, so edits are not committed (LATEX_LIVE_COMMIT)", flush=True)
    server.autocommit = True
    with NOTE_LOCK:
        try:
            load_pending(server)
            claim_leftovers(server)
            if server.pending:
                commit_pending(server)
        except Exception:  # (the server runs on; commit_loop tries again)
            commit_notice(server, f"could not commit what the last run left:\n{traceback.format_exc().rstrip()}")
    threading.Thread(target=commit_loop, args=(server,), daemon=True).start()
    print(f"=== latex-live: each user's edits are committed as theirs after {config.COMMIT_IDLE:g} s of quiet (LATEX_LIVE_COMMIT)",
          flush=True)


def commit_loop(server):
    """The thread that commits each user's pending writes once they have been quiet for
    LATEX_LIVE_COMMIT_IDLE seconds (commit_pending). Whatever goes wrong, it says so once (with where,
    for a bug) and keeps going after a pause."""
    while True:
        with server.pending_lock:  # (it wakes when the first one is due, and at least every second for new writes)
            first = min((entry.get("due", math.inf) for entry in server.pending.values()), default=math.inf)
        time.sleep(min(1, max(0.05, first - time.monotonic())))
        try:
            with server.pending_lock:
                due = [uid for uid, entry in server.pending.items() if entry["due"] <= time.monotonic()]
            if due:
                with NOTE_LOCK:
                    commit_pending(server, due)
        except Exception:
            commit_notice(server, f"commits failed; trying again in a while:\n{traceback.format_exc().rstrip()}")
            time.sleep(10)


def commit_request(server):
    """POST /commit (the owner): commit every pending write now. {"commits": [{user, commit, subject,
    files}]}, with "error" if some could not be."""
    if not server.autocommit:
        return 409, {"error": "edits are not committed here (shared mode does that with LATEX_LIVE_COMMIT=1)"}
    try:
        with NOTE_LOCK:
            made, problem = commit_pending(server)
    except Exception as e:  # (answered, rather than a dropped connection)
        commit_notice(server, f"could not commit:\n{traceback.format_exc().rstrip()}")
        return 500, {"error": f"could not commit ({e})", "commits": []}
    if problem and not made:
        return 409, {"error": problem, "commits": []}
    return 200, {"commits": made, **({"error": problem} if problem else {})}


def commit_at_exit(server):
    """At shutdown: commit what is still pending, as theirs, waiting up to 5 s while another git command
    holds the index. What stays pending (git in the middle of a merge, say) is in the state folder,
    for the next start (load_pending)."""
    if not server.autocommit:
        return
    end = time.monotonic() + 5
    try:
        while server.pending:
            with NOTE_LOCK:
                commit_pending(server)
            if not server.pending or git_operation(server) or not git_busy(server) or time.monotonic() > end:
                break
            time.sleep(0.2)
    except Exception:
        print(f"=== latex-live: could not commit the pending edits:\n{traceback.format_exc().rstrip()}", flush=True)
    if server.pending:
        print(f"=== latex-live: edits stay uncommitted for now; the next start commits them as theirs ({server.pending_file})",
              flush=True)


def commit_pending(server, ids=None, only=None):
    """Commit the pending writes of the users in ids (default: everyone's; of the file only, if
    given), one commit per user (commit_files). The caller holds NOTE_LOCK. While git is busy
    (git_busy), nothing, and they are tried again later; a user's commit that fails (even through a
    bug here) is tried again in a while. Returns ([{user, commit, subject, files}], what went wrong
    or None)."""
    if busy := git_busy(server):
        commit_notice(server, busy)
        with server.pending_lock:  # (commit_loop looks again in a few seconds)
            for entry in server.pending.values():
                entry["due"] = max(entry["due"], time.monotonic() + 3)
        return [], busy
    made, problem = [], None
    with server.pending_lock:
        ids = [uid for uid in (list(server.pending) if ids is None else ids) if uid in server.pending]
    for uid in ids:
        with server.pending_lock:
            user, paths = server.pending[uid]["user"], dict(server.pending[uid]["paths"])
        paths = {rel: value for rel, value in paths.items() if only is None or rel == only}
        who = user["name"] or user["id"]
        try:
            done, failed = commit_files(server, user, paths), None
        except (OSError, RuntimeError, subprocess.SubprocessError) as e:
            done, failed = None, f"could not commit {who}'s edits: {e}"
        except Exception:  # (a bug: said once, with where; the others' commits go on)
            done, failed = None, f"could not commit {who}'s edits:\n{traceback.format_exc().rstrip()}"
        with server.pending_lock:
            entry = server.pending[uid]
            if failed:
                entry["due"] = time.monotonic() + max(10, min(config.COMMIT_IDLE, 60))  # (again in a while)
            else:  # (what was written again meanwhile stays pending)
                for rel, value in paths.items():
                    if entry["paths"].get(rel) == value:
                        del entry["paths"][rel]
                if not entry["paths"]:
                    del server.pending[uid]
                save_pending(server)
        if failed:
            problem = problem or failed
            commit_notice(server, failed)
        if done:
            made.append(done)
    if made and not problem:
        server.commit_said = None
    return made, problem


def commit_notice(server, message):
    """Print why commits wait or fail, once until the reason changes."""
    if message != server.commit_said:
        server.commit_said = message
        print(f"=== latex-live: {message}", flush=True)


def commit_files(server, user, paths):
    """Commit paths ({path relative to the paper root: (kind, sequence number)}) as user, as they are
    in the working tree and nothing else (git commit --only; the committer is the owner), leaving
    out what git doesn't track, what is gone, and what is as committed. Returns {user, commit,
    subject, files}, or None if nothing is left; RuntimeError if git refuses."""
    root = server.paper_root
    git = ["git", "-C", root, "--literal-pathspecs"]
    run = lambda args, env: subprocess.run(git + args, capture_output=True, encoding="utf-8", errors="surrogateescape", env=env,
                                           timeout=60)  # (NOTE_LOCK is held; file names as Python has them, even if not UTF-8)
    files = sorted(rel for rel in paths if os.path.isfile(os.path.join(root, rel)))
    if files:
        tracked = set(run(["ls-files", "-z", "--", *files], git_read_env()).stdout.split("\0"))
        r = run(["diff", "--name-only", "-z", "--relative", "HEAD", "--", *files], git_read_env())
        changed = set(r.stdout.split("\0")) if r.returncode == 0 else set(files)  # (no HEAD yet: all of them)
        files = [rel for rel in files if rel in tracked and rel in changed]
    if not files:
        return None
    owner, owner_email = committer(server)
    name = user["name"] or owner
    email = user["email"] or (owner_email if user["role"] == "owner" else "")
    listed = ", ".join(re.sub(r"[\x00-\x1f\x7f]", "?", rel) for rel in files[:3]) + (f" and {len(files) - 3} more" if len(files) > 3 else "")
    if user["id"] == UNATTRIBUTED:
        subject, trailer = f"Edits found uncommitted at the start: {listed}", "unattributed"
    else:
        verb = "edit" if any(paths[rel][0] == "edit" for rel in files) else "notes in"
        subject, trailer = f"{name}: {verb} {listed}", user["id"]
    env = {key: value for key, value in os.environ.items() if key != "GIT_OPTIONAL_LOCKS"}  # (a commit takes the index)
    r = run(["-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", "-c", "gc.auto=0", "-c", f"user.name={owner}",
             "-c", f"user.email={owner_email}", "commit", "--only", f"--author={name} <{email}>",
             "-m", f"{subject}\n\nLatex-Live-Shared: {trailer}", "--", *files], env)
    if r.returncode:
        raise RuntimeError((r.stderr.strip() or r.stdout.strip() or f"git commit failed ({r.returncode})").splitlines()[-1])
    sha = run(["rev-parse", "--short", "HEAD"], git_read_env()).stdout.strip()
    print(f"=== latex-live: committed {sha}: {subject}", flush=True)
    return {"user": trailer, "commit": sha, "subject": subject, "files": files}


def committer(server):
    """(name, email) that commits coauthors' edits: the users file's owner's, else git's user.name and
    user.email (when they hold no control character, < or >, as the users file's), else latex-live."""
    entry, found = users_owner() or {}, []
    for key in ("name", "email"):
        if not (value := entry.get(key)):
            value = subprocess.run(["git", "-C", server.paper_root, "config", f"user.{key}"], capture_output=True, text=True,
                                   timeout=60).stdout.strip()
            value = "" if re.search(r"[\x00-\x1f\x7f<>]", value) or len(value) > 200 else value
        found.append(value or "latex-live")
    return tuple(found)


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


def include_sites(paper_root, pdf_name):
    """Where each included graphic or \\input file is included: {relative path: (tex file, line)}."""
    fls = fls_file(paper_root, pdf_name)
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


def graphics(paper_root, pdf_name):
    """The graphics the build included: [{file, hash, dirty, unpushed, src, line}] (src:line is the
    \\includegraphics; dirty: changed since HEAD, or untracked; unpushed: likewise since the last
    push). The viewer compares hashes across rebuilds to mark figures whose files changed, or marks
    the dirty (unpushed) ones against HEAD (the last push)."""
    git = ["git", "-C", paper_root]
    changed = lambda rev: set(subprocess.run(git + ["diff", "--name-only", "--relative", rev], capture_output=True, text=True,
                                             env=git_read_env()).stdout.splitlines())
    untracked = set(subprocess.run(git + ["ls-files", "-o", "--exclude-standard"], capture_output=True, text=True).stdout.splitlines())
    dirty = changed("HEAD") | untracked
    pushed = pushed_commit(paper_root)[0]
    unpushed = changed(pushed) | untracked if pushed else set()
    found = []
    for path, (src, line) in include_sites(paper_root, pdf_name).items():
        if path.lower().endswith(GRAPHIC_EXTS):
            try:
                found.append({"file": path, "hash": file_hash(os.path.join(paper_root, path)), "dirty": path in dirty,
                              "unpushed": path in unpushed, "src": src, "line": line})
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
    skip = {".git", ".venv", "venv", "build", config.BUILD.split(os.sep)[0], "node_modules", "archive", "__pycache__"}
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
    aux, blg = read_capped(job_file(paper_root, pdf_name, ".aux")), read_capped(job_file(paper_root, pdf_name, ".blg"))
    if aux is None or blg is None:
        return [], []
    cited = {k.strip() for group in re.findall(r"\\citation\{([^}]*)\}", aux) for k in group.split(",")}
    bibs = re.findall(r"^Database file #\d+: (.+)$", blg, re.M)
    entries = []
    for bib in list(dict.fromkeys(bibs))[:100]:
        path = os.path.normpath(os.path.join(paper_root, bib.strip()))
        if not in_paper(paper_root, path):  # (the .blg names them, and the paper's TeX can write that file)
            continue
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
    fls = fls_file(paper_root, pdf_name)
    macros = project_macros(paper_root, fls)
    texts = {path: read_tex(path) for path in project_tex_files(paper_root, fls)}
    kinds = note_kinds(texts.values())
    blank = lambda m: re.sub(r"[^\n]", " ", m.group(0))
    out = []
    for path, text in texts.items():
        begin = text.find("\\begin{document}")
        if begin > 0:
            text = "\n" * text.count("\n", 0, begin) + text[begin:]
        text = blank_notes(text, kinds)
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
    for path in project_tex_files(paper_root, fls_file(paper_root, pdf_name)):
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


# References to labels: \ref, \cref, \Cref, \autoref, \pageref, project macros like \figref, ranges, \hyperref[...].
REF_RE = re.compile(r"\\[a-zA-Z]*ref\*?\s*\{([^{}]*)\}|\\[a-zA-Z]*refrange\*?\s*\{([^{}]*)\}\s*\{([^{}]*)\}|\\hyperref\s*\[([^\]]*)\]")
BACK_MATTER_RE = re.compile(r"\\(?:appendix|bibliography|printbibliography)\b|\\begin\{thebibliography\}")


def float_placement(paper_root, pdf_name, clean=False):
    """Figures and tables placed or referenced badly: (off, far, unreferenced, unlabeled) check items.

    off: it runs past the text area by more than 3 pt (perhaps off the page). far: a page of text
    (not only floats) lies between it and the first page that references one of its labels from
    outside it. unreferenced: none of its labels is referenced. unlabeled: it has a number but no
    label. A float in the back matter (after the bibliography or \\appendix) only counts references
    there: one referenced only from the main text is a pointer to the appendix, wherever it is.
    """
    found = floats(paper_root, pdf_name, clean)
    f = clean_file if clean else job_file
    fls = fls_file(paper_root, pdf_name)
    points = synctex_points(f(paper_root, pdf_name, ".synctex.gz"))
    area = layout(paper_root, pdf_name) or {"H": 792, "left": 72, "width": 468, "top": 72, "bottom": 720}
    texts = {path: read_tex(path) for path in project_tex_files(paper_root, fls)}
    kinds = note_kinds(texts.values())
    sites = include_sites(paper_root, pdf_name)

    def at(path, line):
        """Reading-order key of a source line: the lines of the \\input chain down to it."""
        rel, chain = os.path.relpath(path, paper_root), (line,)
        while rel in sites and len(chain) < 10:
            rel, n = sites[rel]
            chain = (n, *chain)
        return chain

    def placed(p):
        """Whether a SyncTeX point is in the text area (not the header, footer, or line numbers)."""
        return area["left"] - 0.5 <= p[1] <= area["left"] + area["width"] + 0.5 and area["top"] - 0.5 <= p[2] <= area["bottom"] + 0.5

    def page_of(path, line):
        """The page a source line was typeset on: its float's, else where its words (or the next
        lines', e.g. a reference at the end of a line) landed."""
        x = next((x for x in found if x["path"] == path and x["line"] <= line <= x["last"]), None)
        if x:
            return x["page"]
        return next((p[0] for n in (line, line + 1, line + 2, line - 1) for p in points.get((path, n), []) if placed(p)), None)

    by_page = {}
    for x in found:
        x["path"] = os.path.join(paper_root, x["file"])
        by_page.setdefault(x["page"], []).append(x["box"])
    # Pages with text outside their floats (a line's worth of words and spaces; a page of floats has a
    # few points of glue): only those put distance between a float and its reference.
    outside = collections.Counter(p[0] for pts in points.values() for p in pts if placed(p) and not any(
        l - 2 <= p[1] <= r + 2 and t - 2 <= p[2] <= b + 2 for l, t, r, b in by_page.get(p[0], ())))
    text_pages = {page for page, n in outside.items() if n >= 10}
    refs, ends = {}, []  # {label: [(reading-order key, path, line)]}; where the main text may end
    begin = None
    for path, text in texts.items():
        # (notes and \iffalse blocks are not in the submitted PDF)
        text = blank_notes(re.sub(r"\\iffalse\b.*?\\fi\b", lambda m: re.sub(r"[^\n]", " ", m.group()), text, flags=re.S), kinds)
        line_at = lambda i: text.count("\n", 0, i) + 1
        if begin is None and "\\begin{document}" in text:
            begin = at(path, line_at(text.find("\\begin{document}")))
        ends += [at(path, line_at(m.start())) for m in BACK_MATTER_RE.finditer(text)]
        for m in REF_RE.finditer(text):
            line = line_at(m.start())
            for label in ",".join(filter(None, m.groups())).split(","):
                refs.setdefault(label.strip(), []).append((at(path, line), path, line))
    back = min((key for key in ends if begin and key > begin), default=None)  # (not definitions in the preamble)
    off, far, unreferenced, unlabeled = [], [], [], []
    for x in found:
        name = f"{x['kind'].title()} {x['num']}" if x["num"] else f"Unnumbered {x['kind']}"
        item = {"file": x["file"], "line": x["line"], "page": x["page"]}
        if x["extent"]:
            left, top, right, bottom = x["extent"]
            past = [f"{d:.0f} pt {where}" for where, d in (
                ("above the text area", area["top"] - top), ("below the text area", bottom - area["bottom"]),
                ("into the left margin", area["left"] - left), ("into the right margin", right - area["left"] - area["width"]))
                if d > 3]
            if past:
                off.append({"text": f"{name} runs {', '.join(past)}" + (", off the page" if bottom > area["H"] else ""), **item})
        if not x["keys"]:
            if x["num"]:
                unlabeled.append({"text": f"{name} has no \\label, so the text can't reference it", **item})
            continue
        cited = [r for k in x["keys"] for r in refs.get(k, []) if not (r[1] == x["path"] and x["line"] <= r[2] <= x["last"])]
        if not cited:
            unreferenced.append({"text": f"{name} ({', '.join(x['keys'])}) is never referenced", **item})
            continue
        if back and at(x["path"], x["line"]) > back:
            cited = [r for r in cited if r[0] > back]
        first = min(filter(None, (page_of(r[1], r[2]) for r in cited)), default=None)
        if first and any(p in text_pages for p in range(min(x["page"], first) + 1, max(x["page"], first))):
            far.append({"text": f"{name} is {abs(x['page'] - first)} pages {'after' if x['page'] > first else 'before'} "
                                f"its first reference (page {first})", **item})
    return off, far, unreferenced, unlabeled


ARCHIVE_EXTS = (".tar.gz", ".tgz", ".zip")
COMMENT_RE = re.compile(r"(?<!\\)(?:\\\\)*%(.*)")  # (also right after a \\ line break)
PERSONAL_PATH_RE = re.compile(r"(?<![\w.~])/(?:home|Users|checkpoint)/[^\s{}]*|(?<![\w~])~/[^\s{}]*"
                              r"|(?<![\w\\])[A-Za-z]:[\\/](?:[\w .-]+[\\/])+[^\s{}]*|\b(?i:file):/[^\s{}]*")


LEFTOVER_RE = re.compile(r"\.(?:aux|log|out|toc|lof|lot|fls|fdb_latexmk|synctex(?:\.gz)?|blg|bcf|run\.xml|brf|nav|snm|vrb|xdv|dvi)$"
                         r"|(?:^|/)(?:\.DS_Store|\._[^/]*|__MACOSX/.*|\.git/.*)$")


def arxiv_package(paper_root):
    """The paper root's newest arXiv package, a .tar.gz, .tgz or .zip file or a directory with
    "arxiv" in its name (a directory with a root document, so not, e.g., sections-arxiv/):
    (mtime, is archive, path), or None. A directory's mtime is its newest file's; on a tie the
    archive wins (it is what gets uploaded)."""
    found = []
    for name in os.listdir(paper_root):
        path = os.path.join(paper_root, name)
        if "arxiv" not in name.lower():
            continue
        if os.path.isdir(path) and root_docs(path):
            times = [os.path.getmtime(os.path.join(d, f)) for d, _, files in os.walk(path) for f in files]
            if times:
                found.append((max(times), False, path))
        elif name.lower().endswith(ARCHIVE_EXTS):
            found.append((os.path.getmtime(path), True, path))
    return max(found, default=None)


def file_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def package_files(path, limit=50e6):
    """[(name, size, bytes)] for the files in an archive or directory (bytes None above limit)."""
    if os.path.isdir(path):
        files = []
        for d, dirs, names in os.walk(path):
            dirs.sort()
            for n in sorted(names):
                p = os.path.join(d, n)
                size = os.path.getsize(p)
                files.append((os.path.relpath(p, path), size, file_bytes(p) if size <= limit else None))
        return files
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            return [(os.path.normpath(i.filename), i.file_size, z.read(i) if i.file_size <= limit else None)
                    for i in z.infolist() if not i.is_dir()]
    with tarfile.open(path) as t:
        return [(os.path.normpath(m.name), m.size, t.extractfile(m).read() if m.size <= limit else None) for m in t if m.isfile()]


def pdf_strings(data, cache={}):
    """A PDF's metadata (pdfinfo) and text (pdftotext), from its bytes; "" without poppler."""
    key = hashlib.sha1(data).digest()
    if key not in cache:
        out = []
        with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
            f.write(data)
            f.flush()
            for cmd in (["pdfinfo", f.name], ["pdftotext", "-q", f.name, "-"]):
                out.append(pdf_tool(cmd, timeout=30))
        cache[key] = "\n".join(out)
    return cache[key]


def ago(t):
    return f"{t / 86400:.0f} d" if t >= 86400 else f"{t / 3600:.0f} h" if t >= 3600 else f"{t / 60:.0f} min"


def excerpt(text, n=60):
    text = " ".join(text.split())
    return text[:n] + "…" if len(text) > n else text


def arxiv_source(paper_root, pdf_name):
    """What an arXiv upload would publish, since arXiv publishes the source: (level, detail, items).

    Reads the paper root's arXiv package (arxiv_package), else the project files the build reads
    (what a naive upload of the sources would expose). Flags % comments, note macros (published
    even when hidden), \\iffalse blocks and comment environments, LATEX_LIVE_PRIVATE_TERMS (a regular
    expression for codenames and other internal names), personal paths, files the build doesn't
    read, large files, leftover build files, and a missing .bbl. An item in a package file gives
    package-path:line, and opens the project source when the file is the same there, or the line
    is found in the build's sources (e.g. in a package that inlines every \\input).
    """
    doc = pdf_name[:-4]
    fls = fls_file(paper_root, pdf_name)
    package = arxiv_package(paper_root)
    if package:
        label = os.path.basename(package[2]) + ("/" if not package[1] else "")
        try:
            files = package_files(package[2])
        except (OSError, EOFError, tarfile.TarError, zipfile.BadZipFile) as e:
            return "warn", f"{label}: can't read it ({e})", []
    else:
        files = [(p, os.path.getsize(os.path.join(paper_root, p)), file_bytes(os.path.join(paper_root, p)))
                 for p in project_inputs(paper_root, fls)]
        if not files:
            return "info", "no arXiv package, and no build yet to list the sources", []
    data = {name: d for name, _, d in files}
    texts = {name: d.decode("utf-8", errors="replace") for name, d in data.items()
             if d is not None and not name.lower().endswith(".pdf") and b"\0" not in d[:8192]}
    # Comments blanked at the same length, so positions carry over.
    blanked = {name: COMMENT_RE.sub(lambda m: re.sub(r"[^\\]", " ", m.group(0)), t) for name, t in texts.items()}
    tex = [name for name in texts if name.endswith(".tex")]
    mains = [name for name in tex if "/" not in name and DOCCLASS_RE.search(blanked[name])]
    # The project sources behind the package: this build's, or those of the package's own root file.
    main = mains[0][:-4] if len(mains) == 1 else doc
    same_doc = main == doc
    sources = project_tex_files(paper_root, fls) if same_doc else sorted(source_tree(paper_root, main))
    if package:
        same = {name for name in tex if os.path.isfile(os.path.join(paper_root, name))
                and file_bytes(os.path.join(paper_root, name)) == data[name]}
        lines_at = {}
        for path in sources:
            with open(path, errors="replace") as f:
                for n, line in enumerate(f, 1):
                    lines_at.setdefault(line.strip(), (os.path.relpath(path, paper_root), n))
    else:
        same, lines_at = set(texts), {}

    def item(what, name, pos=None):
        """A check item for something in a file, at pos in its text: package-path:line, and the
        project file and line to open when known."""
        if pos is None:
            return {"text": f"{name}: {what}"}
        text = texts[name]
        n = text.count("\n", 0, pos) + 1
        line = text[text.rfind("\n", 0, pos) + 1:].split("\n", 1)[0].strip()
        loc = (name, n) if name in same else lines_at.get(line) if len(line) >= 20 else None
        return {"text": f"{name}:{n}: {what}" if package else what, **({"file": loc[0], "line": loc[1]} if loc else {})}

    found = collections.defaultdict(list)
    terms = os.environ.get("LATEX_LIVE_PRIVATE_TERMS", "").strip()
    try:
        private = re.compile(terms) if terms else None
    except re.error as e:
        private = None
        found["config"].append({"text": f"LATEX_LIVE_PRIVATE_TERMS is not a regular expression ({e})"})

    def context(text, m):
        """The match with up to 25 characters of its line on each side."""
        start, end = text.rfind("\n", 0, m.start()) + 1, text.find("\n", m.end())
        end = len(text) if end < 0 else end
        return ("…" if m.start() - start > 25 else "") + " ".join(text[max(start, m.start() - 25):min(end, m.end() + 25)].split()) \
            + ("…" if end - m.end() > 25 else "")

    path_text = lambda m: excerpt(m.group(0).rstrip(".,;:)"))  # (without a sentence's full stop)
    for name, size, d in files:
        for m in private.finditer(name) if private else ():
            found["private"].append(item(f"private term \"{m.group(0)}\" in the file name", name))
        if name in texts:
            text = texts[name]
            for m in private.finditer(text) if private else ():
                found["private"].append(item(f"private term \"{m.group(0)}\": {context(text, m)}", name, m.start()))
            for m in PERSONAL_PATH_RE.finditer(text):
                found["path"].append(item(f"personal path: {path_text(m)}", name, m.start()))
        elif d is not None and name.lower().endswith(".pdf"):
            strings = pdf_strings(d)
            for m in private.finditer(strings) if private else ():
                found["private"].append(item(f"private term in the PDF's text or metadata: {context(strings, m)}", name))
            for m in PERSONAL_PATH_RE.finditer(strings):
                found["path"].append(item(f"personal path in the PDF's text or metadata: {path_text(m)}", name))
        if size > 10e6:
            found["large"].append(item(f"{size / 1e6:.1f} MB", name))
    total = sum(size for _, size, _ in files)
    if package and total > 50e6:
        found["large"].append({"text": f"the package holds {total / 1e6:.0f} MB of files (arXiv's limit is 50 MB)"})

    # Comments, notes, and hidden blocks in the .tex files.
    kinds = {**note_kinds(blanked[name] for name in tex), **note_kinds(read_tex(p) for p in sources), "todo": "todo"}
    comments = []
    for name in tex:
        text, blank = texts[name], blanked[name]
        comments += [(name, "% " + excerpt(m.group(1)), m.start()) for m in COMMENT_RE.finditer(text)
                     if re.search(r"\w", m.group(1))]  # (not rules like % ====)
        for m in note_use_re(kinds).finditer(blank):
            body = brace_group(blank, m.end())[0]
            if "#" not in body:  # (not a definition's body)
                found["note"].append(item(f"note \\{m.group(1)}{{{excerpt(body)}}}", name, m.start()))
        hidden = [(s, e, "\\iffalse block") for s, e in false_blocks(blank)]
        hidden += [(m.start(), m.end(), "comment environment")
                   for m in re.finditer(r"\\begin\s*\{comment\}[\s\S]*?(?:\\end\s*\{comment\}|\Z)", blank)]
        for start, end, what in hidden:
            body = re.sub(r"^\\iffalse|^\\begin\s*\{comment\}|\\fi$|\\end\s*\{comment\}$", "", text[start:end])
            lines = text.count("\n", start, end) + 1
            found["iffalse"].append(item(f"{what}, {lines} line(s): {excerpt(body)}", name, start))

    if package:
        # Files the build doesn't read: not among this build's inputs, and not named in the package.
        names = set(data)
        read = set(project_inputs(paper_root, fls)) if same_doc else set()
        code = "\n".join(blanked.values())
        for name in sorted(names):
            stem = os.path.splitext(os.path.basename(name))[0]
            if LEFTOVER_RE.search(name) or name.endswith(".pdf") and name[:-4] + ".tex" in mains:
                found["leftover"].append(item("leftover build file (arXiv needs only the .bbl)", name))
            elif not (name in mains or name.endswith(".bbl") and name[:-4] + ".tex" in mains or name in read
                      or os.path.basename(name).startswith("00README")  # (arXiv's processing instructions)
                      or re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(stem), code, re.I)):
                found["unused"].append(item("not used by the build", name))
        for name in mains:
            if re.search(r"\\bibliography\s*\{|\\addbibresource\b", blanked[name]) and name[:-4] + ".bbl" not in names:
                found["bbl"].append({"text": f"{name[:-4]}.bbl is missing: arXiv doesn't run BibTeX, so the references would be empty"})
        newest = max(((os.path.getmtime(os.path.join(paper_root, p)), p) for p in project_inputs(paper_root, fls)) if same_doc
                     else ((os.path.getmtime(p), os.path.relpath(p, paper_root)) for p in sources), default=None)
        if newest and newest[0] > package[0] + 60:
            found["stale"].append({"text": f"stale: {newest[1]} changed {ago(newest[0] - package[0])} after {label} was made; remake it",
                                   **({"file": newest[1], "line": 1} if newest[1].endswith(".tex") else {})})

    # Most serious first; comments last, with a count per file first when they don't all fit.
    order = ("private", "path", "bbl", "config", "stale", "leftover", "note", "iffalse", "unused", "large")
    items = [it for kind in order for it in found[kind]]
    room = max(0, 50 - len(items))
    per_file = collections.Counter(name for name, _, _ in comments)
    if len(comments) > room and len(per_file) > 1:
        firsts = {}
        for name, what, pos in comments:
            firsts.setdefault(name, (what, pos))
        heads = [{**item("", name, firsts[name][1]), "text": f"{name}: {k} comment(s), e.g. {firsts[name][0]}"}
                 for name, k in per_file.items()]
        items += heads
        room -= len(heads)
    items += [item(what, name, pos) for name, what, pos in comments[:max(0, room)]]
    found["comment"] = comments
    counts = {"private": "private term(s)", "path": "personal path(s)", "bbl": "missing .bbl", "comment": "comment(s)",
              "note": "note(s)", "iffalse": "hidden block(s)", "unused": "unused file(s)", "large": "large file(s)",
              "leftover": "leftover build file(s)"}
    where = (f"{label}{' (stale)' if found['stale'] else ''}, {len(files)} files, {total / 1e6:.1f} MB" if package
             else f"no arXiv package; what a naive upload of the sources would expose ({len(files)} files)")
    summary = ", ".join(f"{len(found[k])} {what}" for k, what in counts.items() if found[k])
    level = ("fail" if found["private"] or found["path"] or found["bbl"] else
             "warn" if any(found[k] for k in ("config", "stale", "leftover", "note", "iffalse", "unused", "large", "comment")) else "ok")
    if not package and level != "ok":  # (no upload yet: a preview)
        level = "warn" if level == "fail" else "info"
    return level, f"{where}: {summary or 'no comments, notes, hidden blocks, private terms or personal paths'}", items


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
    final = re.search(r"^[ \t]*(?:\\iclrfinalcopy\b|\\usepackage\s*\[[^\]]*\b(?:accepted|preprint)\b[^\]]*\]\s*\{icml\d{4}\})",
                      root_tex, re.M)
    if pages and re.search(r"\banonymous\b", pages[0], re.I):
        names = set()  # people named in the note-macro comments, e.g. "\eb ... % Ellis Brown"
        for path in project_tex_files(root, fls_file(root, pdf_name)):
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
        info = pdf_tool(["pdfinfo", pdf])
        # (ICML's submission style sets the metadata author to "Anonymous Authors".)
        meta_author = re.search(r"^Author:[ \t]*(?!anonymous\b)(\S.*)$", info, re.M | re.I)
        problems = [{"text": f"PDF metadata names an author: {meta_author.group(1).strip()}"}] if meta_author else []
        problems += [{**h, "text": "acknowledgments: " + h["text"]} for h in hits(r"\bAcknowledge?ments?\b", flags=0)]
        add("Anonymity", "fail" if problems else "warn" if anon else "ok",
            f"{len(problems)} problem(s), {len(anon)} identifying phrase(s) outside the references" if problems or anon
            else "anonymous author block; no names, affiliations, or links to identifying pages found",
            problems + anon)
    else:
        add("Anonymity", "info", "not checked: page 1 has no anonymous author block"
            + (f" ({final.group(0).strip()} is on)" if final else ""),
            [{"text": f"{final.group(0).strip()} prints the author names", "file": f"{doc}.tex",
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
    build = build_status(server, pdf_name) or {}
    undefined = build.get("undefined") or []
    marks = hits(r"\?\?")
    add("Undefined references and citations", "fail" if undefined or marks else "ok",
        f"{len(undefined)} undefined in the log, {len(marks)} '??' in the PDF" if undefined or marks else "none",
        [{"text": f"{u['kind']} '{u['key']}'", "file": u["file"], "line": u["line"]} for u in undefined] + marks)
    placeholders = hits(r"\b(?:TODO|TBD|TBA|FIXME|XXX)\b|lorem ipsum|\[citation needed\]", flags=0)
    add("Placeholder text", "warn" if placeholders else "ok",
        f"{len(placeholders)} in the {'notes-free ' if clean else ''}PDF" if placeholders else "none", placeholders)

    # Fonts.
    fonts = pdf_tool(["pdffonts", pdf]).splitlines()[2:]
    type3 = []
    if any(" Type 3 " in l for l in fonts):  # which pages (usually matplotlib figures; in shared mode, of the first 200)
        for n in range(1, min(len(pages), 200 if config.SHARED else len(pages)) + 1):
            out = pdf_tool(["pdffonts", "-f", str(n), "-l", str(n), pdf])
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
    for l in pdf_tool(["pdfimages", "-list", pdf]).splitlines()[2:]:
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

    # What the arXiv upload would publish (the source).
    add("arXiv source", *arxiv_source(root, pdf_name))

    # Bibliography.
    warnings = [l.strip()[9:] for l in (read_capped(job_file(root, pdf_name, ".blg")) or "").splitlines() if l.startswith("Warning--")]
    bib_items = []
    for w in dict.fromkeys(warnings):
        key = re.search(r" in (\S+)$", w)
        loc = key and find_bib_entry(root, key.group(1))
        bib_items.append({"text": w, **({"file": loc[0], "line": loc[1]} if loc else {})})
    add("Bibliography entries", "warn" if bib_items else "ok",
        f"{len(bib_items)} bibtex warning(s), e.g. missing fields" if bib_items else "no bibtex warnings", bib_items)

    # Generated figures and tables older than their scripts or data.
    run = "uv run python" if os.path.isfile(os.path.join(root, "pyproject.toml")) else "python"
    stale = sorted(stale_outputs(root, pdf_name), key=lambda x: (not x["dirty"], x["output"]))
    add("Generated figures and tables", "warn" if any(x["dirty"] for x in stale) else "info" if stale else "ok",
        f"{len(stale)} may be stale (a script or data file changed after them)" if stale
        else "no figure or table is older than the script or data that makes it",
        [{"text": f"{x['output']}: {x['source']} changed {ago(x['lag'])} later"
                  + (" (uncommitted)" if x["dirty"] else "")
                  + (f"; rerun {run} {x['source']}" if x["source"].endswith(".py") else ""),
          "file": x["site"][0], "line": x["site"][1], "open": x["source"]} for x in stale])

    # Where figures and tables landed, and whether the text references them.
    off, far, unreferenced, unlabeled = float_placement(root, pdf_name, clean)
    counts = [(off, "off the text area"), (unreferenced, "never referenced"),
              (far, "pages away from their first reference"), (unlabeled, "without a \\label")]
    add("Figure and table placement", "warn" if off or unreferenced else "info" if far or unlabeled else "ok",
        "; ".join(f"{len(xs)} {what}" for xs, what in counts if xs)
        or "all inside the text area, referenced, and near their first reference",
        off + unreferenced + far + unlabeled)

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
    sys.path.insert(0, config.HERE)
    import latex_report
    boxes = []
    if (log := read_capped(job_file(root, pdf_name, ".log"), config.OUTPUT_LIMIT)) is not None:
        cwd = os.getcwd()
        os.chdir(root)  # overfull_boxes checks project paths relative to the paper root
        try:
            boxes = latex_report.overfull_boxes(log)
        finally:
            os.chdir(cwd)
    add("Overfull lines", "info" if boxes else "ok", f"{len(boxes)} line(s) stick into the margin" if boxes else "none",
        [{"text": m, "file": p, "line": n} for p, n, m in boxes])
    return results


def cached_checks(server, pdf_name):
    """GET /check/: checks, which take seconds of the machine's time on a long paper, reused while the
    published PDF and its notes-free one stay the same, for up to 10 s (in shared mode, where editors
    may ask, 30 s; builds start at most every MIN_GAP s there). One computation
    per document at a time, without holding checks_lock meanwhile: requests that come while it runs wait
    for its result, and other documents' checks don't wait for it."""
    root = server.paper_root
    key = (mtime(pdf_path(root, pdf_name)), mtime(pdf_path(root, f"{pdf_name[:-4]}-clean.pdf")))

    def fresh(kept):
        age = time.monotonic() - kept["at"]
        return kept["key"] == key and age < (30 if config.SHARED else 10)

    with server.checks_lock:
        kept = server.checks.get(pdf_name)
        if kept and fresh(kept):
            return kept["results"]
        running = server.checks_running.get(pdf_name)
        mine = running is None
        if mine:
            running = server.checks_running[pdf_name] = threading.Event()
    if not mine:
        running.wait()
        with server.checks_lock:
            kept = server.checks.get(pdf_name)
        return kept["results"] if kept else checks(server, pdf_name)
    try:
        results = checks(server, pdf_name)
        with server.checks_lock:
            server.checks[pdf_name] = {"key": key, "at": time.monotonic(), "results": results}
        return results
    finally:
        with server.checks_lock:
            server.checks_running.pop(pdf_name, None)
        running.set()


def editor_cli():
    """The editor's command-line tool: LATEX_LIVE_EDITOR (e.g. cursor), else `code` in a VS Code terminal, else None."""
    if editor := os.environ.get("LATEX_LIVE_EDITOR"):
        return shutil.which(editor)
    return shutil.which("code") if "VSCODE_IPC_HOOK_CLI" in os.environ else None


def open_in_vscode(src, line, paper_root):
    """Open src at line in the editor (editor_cli). Without one, say so rather than fail quietly."""
    where = f"{os.path.relpath(src, paper_root)}:{line}"
    code, editor = editor_cli(), os.environ.get("LATEX_LIVE_EDITOR")
    if not code:
        why = (f"LATEX_LIVE_EDITOR ({editor}) is not a command here" if editor else
               "latex-live is not running in a VS Code terminal (start it with the VS Code task), and LATEX_LIVE_EDITOR is not set")
        return 409, f"{where}, not opened: {why}"
    subprocess.Popen([code, "-g", f"{src}:{line}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return 200, where


def synctex_input(paper_root, name, page, x, y, clean):
    """PDF position -> (the input file as SyncTeX recorded it, line), or (status, message) when there
    is none. name: the published PDF; x, y in PDF points from the page's top-left; clean for a
    position in the notes-free PDF."""
    if not name.endswith(".pdf") or os.path.basename(name) != name:
        return 400, "bad file"
    job = clean_file if clean else job_file
    pdf = job(paper_root, name, ".pdf")  # the job whose .synctex.gz we have
    if not os.path.isfile(pdf[:-4] + ".synctex.gz"):  # (the notes-free PDF itself moves to build/)
        return 404, f"no {os.path.relpath(pdf, paper_root)} yet"
    result = next((r for r in run_synctex(paper_root, "edit", "-o", f"{page}:{x:.2f}:{y:.2f}:{pdf}") if "Input" in r), {})
    if "Input" not in result:
        missing = synctex_unavailable()
        return (503, missing) if missing else (404, "no source location here")
    return os.path.normpath(result["Input"]), max(1, to_int(result.get("Line"), 1))


def inverse_search(paper_root, name, page, x, y, clean):
    """PDF position -> (source path, line), or (status, message) when there is none (synctex_input)."""
    found = synctex_input(paper_root, name, page, x, y, clean)
    if isinstance(found[0], int):
        return found
    src, line = found
    if src.startswith(os.path.join(paper_root, config.BUILD) + os.sep):  # generated, e.g. the table of contents
        return 404, f"generated text ({os.path.basename(src)}); no source line"
    return src, line


def synctex_lookup(server, query, user=None):
    """/synctex?file&page&x&y[&clean=1]&json=1: inverse search for the browser editor, which opens
    nothing here. Returns (status, {"file": the source's path in the editor's list (editable_files),
    "line"}), or {"file": None, "reason"} (200) for a spot whose source the editor doesn't open: a
    class file of TeX's, generated text in the build directory (for anyone but the owner, without the
    file's name: SyncTeX knows every file the build read)."""
    found = synctex_spot(server, query)
    if config.SHARED and user and user["role"] != "owner" and found[1].get("file") is None and "reason" in found[1]:
        return 200, {"file": None, "reason": "no source line the editor opens here"}
    return found


def synctex_spot(server, query):
    """synctex_lookup's answer, with the reason naming the file."""
    try:
        name = query["file"][0]
        page, x, y = int(query["page"][0]), float(query["x"][0]), float(query["y"][0])
    except (KeyError, ValueError):
        return 400, {"error": "expected file, page, x, y"}
    found = synctex_input(server.paper_root, name, page, x, y, query.get("clean") == ["1"])
    if isinstance(found[0], int):
        return found[0], {"error": found[1]}
    src, line = found
    root = os.path.realpath(server.paper_root)
    path = os.path.realpath(os.path.join(root, src))
    if path.startswith(os.path.realpath(os.path.join(root, config.BUILD)) + os.sep):
        return 200, {"file": None, "reason": f"generated text ({os.path.basename(src)}); no source line"}
    if not path.startswith(root + os.sep):
        return 200, {"file": None, "reason": f"{os.path.basename(src)} is not one of the paper's files"}
    rel = next((rel for rel, real in editable_files(server, fresh=False).items() if real == path), None)
    if not rel:
        return 200, {"file": None, "reason": f"{os.path.basename(src)} is not one of the files the editor opens"}
    try:
        with open(path, "rb") as f:
            data = f.read()
        lines = max(1, data.count(b"\n") + (not data.endswith(b"\n")))
    except OSError:
        lines = line
    if line > lines:  # (SyncTeX names lines past the end now and then, e.g. in a table built by macros)
        return 200, {"file": rel, "line": lines, "note": f"SyncTeX said line {line} of {rel}, which has {lines}: its last line instead"}
    return 200, {"file": rel, "line": line}


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
    found = inverse_search(paper_root, name, page, x, y, query.get("clean") == ["1"])
    if isinstance(found[0], int):
        return found
    return open_in_vscode(*found, paper_root)


def build_running(server, pdf_name):
    """Whether latexmk (in shared mode, shared_build) is building pdf_name now: its passes may still publish
    it before the run ends."""
    if config.SHARED:
        return pdf_name == server.default_pdf and server.shared["building"]
    root = server.paper_root
    return mtime(job_file(root, pdf_name, ".building")) > mtime(job_file(root, pdf_name, ".status.json"))


def build_status(server, pdf_name, clean=False):
    """The last run's result (latex_report.py's build/<job>.status.json), or with clean, the notes-free
    pass's; in shared mode, shared_build's, kept in memory (the paper's TeX can write those files)."""
    if config.SHARED:
        return server.shared["clean" if clean else "status"] if pdf_name == server.default_pdf else None
    return read_json((clean_file if clean else job_file)(server.paper_root, pdf_name, ".status.json"))


def waiting_for_build(server, pdf, state):
    """The state that /export and /review report while the document builds (a download made then would
    be of the build before, or of a pass before the last), or None when it doesn't, or when state (theirs)
    is a build of theirs that is running. Not with --no-build: no latexmk runs then, so a .building marker
    is one that a build stopped part way left behind."""
    if server.no_build or state.get("pdf") == pdf and state.get("state") == "running" or not build_running(server, pdf):
        return None
    return {"pdf": pdf, "state": "running", "step": "waiting for the build in progress", "time": time.strftime("%H:%M:%S"), "msg": ""}


def start_export(server):
    """/export: the document without its notes, built exactly (build_export), for downloading as
    build/<doc>-export.pdf. Built once per build of the document; until then, its state."""
    pdf = server.default_pdf
    if not os.path.isfile(pdf_path(server.paper_root, pdf)):
        return 409, {"state": "failed", "msg": f"{pdf} has not built yet"}
    with server.download_lock:  # (from however many viewers at once: one build)
        e = server.export
        if waiting := waiting_for_build(server, pdf, e):
            return 200, waiting
        source = str(mtime(pdf_path(server.paper_root, pdf)))  # (the build it reflects)
        if e.get("pdf") == pdf and (e.get("state") == "running" or e.get("source") == source):  # (a failure too, until the next build)
            return 200, e
        server.export = {"pdf": pdf, "source": source, "state": "running", "time": time.strftime("%H:%M:%S"), "msg": ""}
        threading.Thread(target=build_export, args=(server, pdf, source), daemon=True).start()
        return 200, server.export


def export_tools(run, root, out, job):
    """After the export's first pass (exact_export), the tools the paper uses, as the live build runs them
    (shared_run): bibtex for \\bibdata (in its .aux or a chapter's) or biber for a .bcf, and makeindex for
    an .idx. Whether one ran; RuntimeError if one failed, rather than an export missing its bibliography
    or index."""
    ran, where = False, os.path.relpath(out, root)
    if "\\bibdata" in aux_tree(read_output(root, os.path.join(out, f"{job}.aux")) or "", out):
        if not run(["bibtex", job], out, {"BIBINPUTS": f"{root}:", "BSTINPUTS": f"{root}:"}):
            raise RuntimeError(f"bibtex failed; see {where}/{job}.blg")
        ran = True
    elif os.path.exists(os.path.join(out, f"{job}.bcf")):
        if not run(["biber", f"--output-directory={out}", job], root):
            raise RuntimeError(f"biber failed; see {where}/{job}.blg")
        ran = True
    if os.path.exists(os.path.join(out, f"{job}.idx")):
        if not run(["makeindex", f"{job}.idx"], out):
            raise RuntimeError(f"makeindex failed; see {where}/{job}.ilg")
        ran = True
    return ran


def build_export(server, pdf, source):
    """The /export thread: the exact build without notes (exact_export), its state for the viewer."""
    try:
        exact_export(server, pdf)
        state = {"state": "done", "version": str(mtime(os.path.join(server.paper_root, config.BUILD, f"{pdf[:-4]}-export.pdf")))}
    except Exception as e:  # (shown in the viewer)
        state = {"state": "failed", "msg": failure(server, e)}
    with server.download_lock:  # (not over a newer export's state: start_export)
        if server.export.get("pdf") == pdf and server.export.get("source") == source:
            server.export = {**server.export, **state}


def failure(server, e):
    """What a download that failed says in the viewer (build_export, build_review), in 300 characters at most. In
    shared mode, where every coauthor reads it, without the server's folders (TeX names the paper's files relative
    to it, but Python's errors name them in full) or the command line of a TeX run that took too long."""
    if config.SHARED and isinstance(e, subprocess.TimeoutExpired):
        return f"a TeX run took more than {e.timeout / 60:g} minutes"
    text = str(e)
    for folder, name in ((os.path.realpath(server.paper_root), "."), (server.paper_root, "."), (config.HERE, "latex-live")) if config.SHARED else ():
        text = text.replace(folder + os.sep, "" if name == "." else name + os.sep).replace(folder, name)
    return text[:300]


def exact_export(server, pdf, keep=None):
    """build/<doc>-export.pdf: the document without notes, as it would be submitted. Unlike the
    quick notes-free pass (latex_report.notes_free_pass), which reuses the cross-references of the
    build with notes, this one runs bibtex for its own citations (a \\cite only in a note drops out
    of the bibliography) and pdflatex until the references settle, so page numbers in the contents,
    references and back-references are exact.

    Built in build/export/<doc>/ once per build of the document (the published PDF's mtime), one
    export at a time; with SyncTeX, and with the files its last pass read kept in inputs/, so that
    build_review can repeat that pass. keep(out), if given, runs before another export can replace
    this one (to copy what it needs). Raises RuntimeError when it fails, and at once when it failed
    for this build already (it would again: for the review copy after /export, say)."""
    root, doc = server.paper_root, pdf[:-4]
    out = os.path.join(root, config.BUILD, "export", doc)  # (per document: another instance may export another)
    job = f"{doc}-export"
    sys.path.insert(0, config.HERE)
    import latex_report

    def run(argv, cwd, env=None):
        code, _ = run_tool(tex_argv(server, *argv), cwd, kind="export", env={**os.environ, **(env or {})})
        if code is None:
            raise subprocess.TimeoutExpired(argv, 600)
        return code == 0

    def log():
        return read_output(root, os.path.join(out, f"{job}.log")) or ""

    with server.export_lock:
        source = str(mtime(pdf_path(root, pdf)))  # (the build it reflects)
        built, why = server.exported.get(pdf, (None, None))  # (the build last exported, and what failed then)
        if built == source and why:
            raise RuntimeError(why)
        if built != source or not os.path.isfile(os.path.join(root, config.BUILD, f"{job}.pdf")):
            if config.SHARED and not image_pulled():  # (said plainly, rather than as a pdflatex failure with no log)
                raise RuntimeError(f"{config.IMAGE_MISSING}; until then there is no download without notes (never the host's TeX here)")
            server.exported.pop(pdf, None)
            try:
                shutil.rmtree(out, ignore_errors=True)
                os.makedirs(out)
                for ext in (".aux", ".bbl", ".brf", ".out", ".toc"):  # (a head start; bibtex and the passes correct them)
                    if os.path.exists(job_file(root, pdf, ext)):
                        shutil.copyfile(job_file(root, pdf, ext), os.path.join(out, job + ext))
                tex = ["pdflatex", "-interaction=batchmode", "-halt-on-error", "-file-line-error", "-synctex=1",
                       f"-output-directory={out}", f"-jobname={job}", f"{latex_report.NOTES_OFF}\\input{{{doc}}}"]
                for passes in range(1, 7):
                    keep_inputs(out)
                    if not run(tex, root):
                        first = next((l for l in log().splitlines() if l.startswith("! ") or re.match(r"^\S+:\d+: ", l)), "")
                        raise RuntimeError(first or f"pdflatex failed; see {config.BUILD}/export/{doc}/{job}.log")
                    if passes == 1 and export_tools(run, root, out, job):  # (bibtex or biber, makeindex: then the passes again)
                        continue
                    settled = not re.search(r"Rerun to get|may have changed\. Rerun|Please rerun|rerunfilecheck Warning", log())
                    # (lineno places its numbers from the previous pass's .aux without asking for a rerun, so
                    # a first pass seeded with the build-with-notes .aux needs a second: latex_report.uses_lineno)
                    if settled and (passes >= 2 or not latex_report.uses_lineno(os.path.join(out, f"{job}.aux"))):
                        break
                else:  # (not exact, so not published)
                    raise RuntimeError(f"the references had not settled after {passes} passes; see {config.BUILD}/export/{doc}/{job}.log")
            except subprocess.TimeoutExpired:  # (remembered with the build, as every failure: it would fail again)
                server.exported[pdf] = (source, f"a pass took over 10 minutes; see {config.BUILD}/export/{doc}/{job}.log")
                raise RuntimeError(server.exported[pdf][1]) from None
            except RuntimeError as e:
                server.exported[pdf] = (source, str(e))
                raise
            os.replace(os.path.join(out, f"{job}.pdf"), os.path.join(root, config.BUILD, f"{job}.pdf"))
            server.exported[pdf] = (source, None)
            with server.download_lock:  # (built for the review copy: the download without notes needs no second build)
                e = server.export
                if not (e.get("pdf") == pdf and (e.get("state") == "running" or e.get("source") == source)):
                    server.export = {"pdf": pdf, "source": source, "state": "done", "time": time.strftime("%H:%M:%S"), "msg": "",
                                     "version": str(mtime(os.path.join(root, config.BUILD, f"{job}.pdf")))}
        if keep:
            keep(out)
    return out


AUX_SKIP = (".pdf", ".log", ".synctex.gz", ".blg", ".fls")  # (what a pass writes but doesn't read)


def keep_inputs(out):
    """Copy the files the next pass in out will read (.aux, .bbl, .toc..., an \\include's .aux) into
    out/inputs/."""
    inputs = os.path.join(out, "inputs")
    shutil.rmtree(inputs, ignore_errors=True)
    shutil.copytree(out, inputs, ignore=lambda folder, names: [n for n in names if n.endswith(AUX_SKIP)
                                                                or (folder == out and n == "inputs")] + links_in(folder, names))


def links_in(folder, names):
    """For shutil.copytree in shared mode, which copies what a link points to: the names in folder
    that are symbolic links, to leave out. TeX writes none, and a copy of TeX's output (which editors
    control) must bring in no file from elsewhere for the next pass to read, or the host to open."""
    return [n for n in names if config.SHARED and os.path.islink(os.path.join(folder, n))]


# The review copy (/review): the exact build without notes, its pages unchanged on wider pages, with
# each note typeset in a gutter beside its column (left of the page for a left column), numbered at
# its spot in the text and wired to it. For co-authors who don't run latex-live.
#
# Where the notes go comes from a probe: the last pass of the export again, with the same inputs,
# where \todotxt records the spot of each note it typesets (\pdfsavepos, written at shipout) instead
# of nothing. So that TeX finds the same line breaks, glue and margin kerning, the spaces, kerns and
# penalties before a note in a paragraph come off and go back after the record (in math, a record
# changes no spacing). A record counts only where its line (the innermost hbox) is typeset exactly as
# in the export, by their SyncTeX; other notes, and notes between paragraphs, are placed from the
# export's SyncTeX by source line instead. A note the probe never meets isn't typeset (it is in a
# conditional that is off, say) and is left out. After the last page, the probe also typesets every
# note as the paper shows it (its macros, colors, references), at two sizes, each on a page of its
# own, which the review copy places in the gutters as they are.
REVIEW_PROBE = r"""% latex-live: the review copy's probe (build_review in live.py), read before the document.
\ifdefined\ReadonlyShipoutCounter\else\endinput\fi
\makeatletter
\def\latexlive@log#1{\immediate\write-1{latex-live|#1}}
\def\latexlive@here{\ifdefined\CurrentFile\CurrentFilePath/\CurrentFile\fi|\the\inputlineno}
\def\latexlive@strip{\let\latexlive@next\relax
  \ifnum\lastnodetype=11 \edef\latexlive@tail{\hskip\the\lastskip\relax\unexpanded\expandafter{\latexlive@tail}}%
    \unskip\let\latexlive@next\latexlive@strip\fi
  \ifnum\lastnodetype=12 \edef\latexlive@tail{\kern\the\lastkern\relax\unexpanded\expandafter{\latexlive@tail}}%
    \unkern\let\latexlive@next\latexlive@strip\fi
  \ifnum\lastnodetype=13 \edef\latexlive@tail{\penalty\the\lastpenalty\relax\unexpanded\expandafter{\latexlive@tail}}%
    \unpenalty\let\latexlive@next\latexlive@strip\fi
  \latexlive@next}
% A note's spot (page, position), written at shipout.
\long\def\latexlive@record#1{\pdfsavepos\edef\latexlive@write{\write-1{latex-live|pos|\latexlive@here
  |\noexpand\the\ReadonlyShipoutCounter|\noexpand\the\pdflastxpos|\noexpand\the\pdflastypos|\detokenize{#1}}}%
  \latexlive@write}
% In a paragraph, the spaces, kerns and penalties before the note go back after the record, so the
% lines break where they did (elsewhere nothing breaks, and they stay before it, as in a cell's template).
% A record stops \ignorespaces (or TeX's look for an optional space), which in the export goes on past
% the vanished note, so the record skips the spaces after it where that is likely: after rigid or
% infinite glue (a template's, or \@esphack's after \label) and at the start of a paragraph (after a
% display), not after a word's space.
\protected\long\def\latexlive@mark#1{\begingroup\def\latexlive@tail{}\def\latexlive@then{}%
  \ifhmode
    \ifnum\lastnodetype<\z@\ifinner\else\def\latexlive@then{\ignorespaces}\fi\fi
    \ifnum\lastnodetype=11
      \ifnum\gluestretchorder\lastskip>\z@\def\latexlive@then{\ignorespaces}\fi
      \ifdim\gluestretch\lastskip=\z@\ifdim\glueshrink\lastskip=\z@\def\latexlive@then{\ignorespaces}\fi\fi
    \fi
    \ifinner\else\latexlive@strip\fi
  \fi
  \latexlive@record{#1}\latexlive@tail\expandafter\endgroup\latexlive@then}
\protected\long\def\latexlive@seen#1{\latexlive@log{seen|\latexlive@here|\detokenize{#1}}}
% At the start of a cell, whose template has begun (in a p column, in vertical mode); in the export,
% the look ahead for \omit skipped the spaces after the note.
\protected\long\def\latexlive@cell#1{\ifvmode\latexlive@seen{#1}\else\latexlive@record{#1}\expandafter\ignorespaces\fi}
% Typesetting, not writing to a file or a mark. Where an alignment looks ahead for \noalign or \omit,
% the probe must not start a row or a cell that the export doesn't: between rows, the note is logged
% from a \noalign of its own; at the start of a cell, from a protected macro, which starts the cell
% there, as the note's text does in the build with notes (so \omit can't follow it in a paper that
% builds).
\long\def\latexlive@todotxt#1{\ifx\protect\@typeset@protect
  \ifvmode\ifnum\currentgrouptype=6 \noalign{\latexlive@seen{#1}}\else\latexlive@seen{#1}\fi
  \else\ifnum\ifnum\currentgrouptype=6 \lastnodetype\else0\fi<0 \latexlive@cell{#1}\else\latexlive@mark{#1}\fi\fi\fi}
\ifcsname tex_shipout:D\endcsname\expandafter\let\expandafter\latexlive@shipout\csname tex_shipout:D\endcsname
\else\let\latexlive@shipout\shipout\fi
\newbox\latexlive@box \newdimen\latexlive@wide \newcount\latexlive@page
\def\latexlive@lines{\let\latexlive@next\relax
  \ifnum\lastnodetype=11 \unskip\let\latexlive@next\latexlive@lines\fi
  \ifnum\lastnodetype=12 \unkern\let\latexlive@next\latexlive@lines\fi
  \ifnum\lastnodetype=13 \unpenalty\let\latexlive@next\latexlive@lines\fi
  \ifx\latexlive@next\relax\setbox\tw@\lastbox\ifvoid\tw@\else
    \ifhbox\tw@\setbox\tw@\hbox{\unhbox\tw@}\fi
    \ifdim\wd\tw@>\latexlive@wide\global\latexlive@wide\wd\tw@\fi\let\latexlive@next\latexlive@lines\fi\fi
  \latexlive@next}
\long\def\latexlive@size#1#2#3#4{%
  \setbox\latexlive@box\vtop{\hsize=<WIDTH>bp\relax\linewidth\hsize\columnwidth\hsize\textwidth\hsize
    \normalfont#3\relax\raggedright\parindent\z@\parskip\z@\leftskip\z@\everypar{}%
    \ifdefined\nolinenumbers\nolinenumbers\fi\leavevmode#4\par}%
  \global\latexlive@wide\z@\setbox\z@\vbox{\unvcopy\latexlive@box\latexlive@lines}%
  \global\advance\latexlive@page\@ne
  \latexlive@log{snippet|#1|#2|\the\latexlive@page|\the\latexlive@wide|\the\ht\latexlive@box|\the\dp\latexlive@box}%
  \ifdim\latexlive@wide<\wd\latexlive@box\global\latexlive@wide\wd\latexlive@box\fi
  \begingroup\pdfpagewidth\latexlive@wide\pdfpageheight\dimexpr\ht\latexlive@box+\dp\latexlive@box+1sp\relax
  \pdfhorigin\z@\pdfvorigin\z@\latexlive@shipout\box\latexlive@box\endgroup}
% \latexlivecite{key}{label}: a citation only in notes, which the export's bibliography leaves out.
\def\latexlive@cite#1#2{\global\expandafter\def\csname b@\detokenize{#1}\ifdefined\@extra@binfo\@extra@binfo\fi\endcsname{#2}}
\long\def\latexlive@snippet#1#2{\latexlive@size{#1}0\footnotesize{#2}\latexlive@size{#1}1\scriptsize{#2}}
\def\latexlive@color#1#2#3{\ifdefined\convertcolorspec\begingroup
  \ifx\relax#2\relax\extractcolorspecs{#3}\latexlive@m\latexlive@s\else\def\latexlive@m{#2}\def\latexlive@s{#3}\fi
  \convertcolorspec\latexlive@m\latexlive@s{rgb}\latexlive@rgb\latexlive@log{color|#1|\latexlive@rgb}\endgroup\fi}
\def\latexlive@notes{\begingroup
  \latexlive@log{pages|\the\ReadonlyShipoutCounter}\global\latexlive@page\ReadonlyShipoutCounter
  \long\def\todotxt##1{##1}\ifdefined\showtodostrue\showtodostrue\fi
  \let\latexlivesnippet\latexlive@snippet\let\latexlivecolor\latexlive@color\let\latexlivecite\latexlive@cite
  \input{<NOTES>}\endgroup}
\AddToHook{enddocument/afterlastpage}{\latexlive@notes}
\AtBeginDocument{\ifdefined\todotxt\let\todotxt\latexlive@todotxt\fi\ifdefined\showtodosfalse\showtodosfalse\fi
  \ifdefined\xspaceaddexceptions\xspaceaddexceptions{\latexlive@mark}\fi}% (\xspace looks past a note it expands to nothing)
\makeatother
"""
REVIEW_HEAD = r"""\documentclass{article}
\usepackage[T1]{fontenc}
\usepackage{lmodern}
\usepackage{graphicx}
\usepackage{tikz}
\pagestyle{empty}
\tikzset{x=1bp, y=1bp, latexlive tag/.style={rounded corners=1.3bp, inner sep=.9bp, minimum height=6.6bp,
  minimum width=6.6bp, text=white, font=\sffamily\bfseries\fontsize{5.2}{5.2}\selectfont},
  latexlive mark/.style={latexlive tag, rounded corners=1bp, inner sep=.5bp, minimum height=5.2bp, minimum width=5.2bp,
  font=\sffamily\bfseries\fontsize{4.3}{4.3}\selectfont}}
% \latexlivepage{width}{height}{x}{page}{drawing}: a page of the review copy, with a page of the paper
% (or nothing) placed at x points from its left edge on its bottom edge, by TeX itself so that it lands
% exactly there (TikZ's coordinates are a little off), and the drawing over it (TikZ, in points from
% the bottom left)
\newcommand\latexlivepage[5]{\pdfpagewidth=#1bp\relax\pdfpageheight=#2bp\relax\pdfhorigin=0pt\pdfvorigin=0pt
  \shipout\vbox to\pdfpageheight{\vss\hbox{\begin{tikzpicture}[overlay]#5\end{tikzpicture}\kern#3bp\relax#4}}}
\begin{document}
"""
REVIEW_INNER, REVIEW_TAG, REVIEW_OUTER = 7, 13, 9  # a gutter, from the page edge out: space, the number, the note, space
REVIEW_MARGIN, REVIEW_GAP = 24, 5  # (top and bottom of a gutter; between notes)
BP = 72 / 72.27  # TeX points to PDF points


def start_review(server):
    """/review: the review copy of the current build (build_review), for downloading as
    build/<doc>-review.pdf. Built once per build of the document; until then, its state."""
    pdf = server.default_pdf
    if not os.path.isfile(pdf_path(server.paper_root, pdf)):
        return 409, {"state": "failed", "msg": f"{pdf} has not built yet"}
    with server.download_lock:  # (from however many viewers at once: one build)
        r = server.review
        if waiting := waiting_for_build(server, pdf, r):
            return 200, waiting
        source = str(mtime(pdf_path(server.paper_root, pdf)))
        if r.get("pdf") == pdf and (r.get("state") == "running" or r.get("source") == source):  # (a failure too, until the next build)
            return 200, r
        server.review = {"pdf": pdf, "source": source, "state": "running", "step": "the exact build without notes",
                         "time": time.strftime("%H:%M:%S"), "msg": ""}
        threading.Thread(target=build_review, args=(server, pdf, source), daemon=True).start()
        return 200, server.review


def build_review(server, pdf, source):
    """build/<doc>-review.pdf (see REVIEW_PROBE), built in build/review/<doc>/: the exact export,
    the probe (one pdflatex pass), the layout of the gutters, and a document that places the
    export's pages and the typeset notes with TikZ (one more pass, in .copy/ there: a folder that
    holds only that document and the two PDFs it places). The layout also goes to <doc>-review.json
    there. Published whole or not at all."""
    root, doc = server.paper_root, pdf[:-4]
    out = os.path.join(root, config.BUILD, "review", doc)
    job = f"{doc}-export"  # (the probe repeats the export's last pass, \jobname included)

    def step(text):
        with server.download_lock:  # (not over a newer review's state: start_review)
            if server.review.get("pdf") == pdf and server.review.get("source") == source:
                server.review = {**server.review, "step": text}

    def keep(export):  # the export's PDF and SyncTeX, and the inputs of its last pass, for the probe
        shutil.copyfile(os.path.join(root, config.BUILD, f"{job}.pdf"), os.path.join(out, "export.pdf"))
        shutil.copyfile(os.path.join(export, f"{job}.synctex.gz"), os.path.join(out, "export.synctex.gz"))
        shutil.copytree(os.path.join(export, "inputs"), out, dirs_exist_ok=True, ignore=links_in)

    with server.review_lock:  # (one at a time: they share build/review/<doc>/)
        try:
            if config.SHARED and not image_pulled():  # (said plainly, rather than as a pdflatex failure with no log)
                raise RuntimeError(f"{config.IMAGE_MISSING}; until then there is no copy with notes in the margin"
                                   " (never the host's TeX here)")
            notes = review_notes(root, pdf)
            if not notes:
                raise RuntimeError("the paper has no notes to show (resolved ones and protected markers don't count)")
            shutil.rmtree(out, ignore_errors=True)
            os.makedirs(out)
            exact_export(server, pdf, keep)
            step("placing the notes")
            area = layout(root, pdf) or {"W": 612, "H": 792, "left": 72, "top": 72, "bottom": 720, "width": 468, "columns": 1}
            geo = review_geometry(area)
            probe = run_probe(server, out, job, doc, notes, geo)
            placed = review_anchors(root, out, notes, probe, area)
            if not placed:
                raise RuntimeError("none of the notes is typeset (are they all in parts the document leaves out?)")
            pages = len(synctex_pages(os.path.join(out, "export.synctex.gz")))
            plan = review_layout(placed, probe, geo, pages)
            step("typesetting the review copy")
            with open(os.path.join(out, f"{doc}-review.json"), "w") as f:
                json.dump(plan, f, indent=1)
            # (TeX takes packages from its working folder first, and the paper's TeX writes into out: the last pass
            # runs in a folder made only now, with a name that starts with a dot, which no TeX run of the paper's can
            # write into either, as openout_any=p refuses it)
            copy = os.path.join(out, ".copy")
            os.makedirs(copy)
            for name in ("export.pdf", "probe.pdf"):
                if os.path.exists(os.path.join(out, name)):
                    os.replace(os.path.join(out, name), os.path.join(copy, name))
            with open(os.path.join(copy, f"{doc}-review.tex"), "w", encoding="utf-8") as f:
                f.write(review_tex(plan, geo, probe.get("colors", {}), notes))
            code, _ = run_tool(tex_argv(server, "pdflatex", "-interaction=batchmode", "-halt-on-error", f"{doc}-review.tex"), copy,
                               kind="review")
            if code is None:
                raise subprocess.TimeoutExpired("pdflatex", 600)
            log = read_output(root, os.path.join(copy, f"{doc}-review.log")) or ""
            written = re.search(r"Output written on .*?\((\d+) pages?", log.replace("\n", ""))
            if code != 0 or not written or int(written.group(1)) != plan["pages"]:
                first = next((l for l in log.splitlines() if l.startswith("! ")), "")
                raise RuntimeError(f"the review copy didn't build ({first or f'see {os.path.relpath(copy, root)}/{doc}-review.log'})")
            os.replace(os.path.join(copy, f"{doc}-review.pdf"), os.path.join(root, config.BUILD, f"{doc}-review.pdf"))
            state = {"state": "done", "version": str(mtime(os.path.join(root, config.BUILD, f"{doc}-review.pdf"))),
                     "notes": len(plan["notes"]), "approximate": sum(n["how"] != "exact" for n in plan["notes"]),
                     "continued": sum(n["top"] is None for n in plan["notes"])}
        except Exception as e:  # (shown in the viewer)
            state = {"state": "failed", "msg": failure(server, e)}
    with server.download_lock:  # (not over a newer review's state: start_review)
        if server.review.get("pdf") == pdf and server.review.get("source") == source:
            server.review = {**server.review, **state}


def review_notes(paper_root, pdf_name):
    """The notes the review copy shows, in source order: [{id, macro, author, file, line, col, last,
    call, body, typeset}]: not resolved notes (commented out), notes in \\iffalse blocks, notes inside
    another note (they show in it), or LATEX_LIVE_PROTECTED markers. call is the note as written and
    body its argument, as Latin-1 (the file's bytes as they are); typeset is what its macro hands to
    \\todotxt, squeezed (to tell apart notes of two authors with the same text), or None."""
    files = project_tex_files(paper_root, fls_file(paper_root, pdf_name))
    texts = [read_tex(p) for p in files]
    kinds = note_kinds(texts)
    if not kinds:
        return []
    templates = {}  # (note macro: the argument it gives \todotxt, with #1 in it)
    for text in texts:
        for m in NOTE_DEF_RE.finditer(text):
            body = brace_group(text, m.end())[0]
            at = re.search(r"\\todotxt\s*(?=\{)", body)
            if at:
                templates[m.group(1)] = brace_group(body, at.end())[0]
    use, found = note_use_re(kinds), []
    for path in files:
        try:
            with open(path, "rb") as f:
                raw = f.read().decode("latin-1")
        except OSError:
            continue
        text = blank_comments(raw)
        for start, end in false_blocks(text):
            text = text[:start] + re.sub(r"[^\n]", " ", text[start:end]) + text[end:]
        inside = 0
        for m in use.finditer(text):
            if m.start() < inside:
                continue
            inside = end = brace_group(text, m.end())[1]
            if config.PROTECTED_RE and config.PROTECTED_RE.search(text[m.start():end]):
                continue
            body = raw[m.end() + 1:end - 1]
            template = templates.get(m.group(1))
            found.append({"id": len(found) + 1, "macro": m.group(1), "author": kinds[m.group(1)],
                          "file": os.path.relpath(path, paper_root), "line": text.count("\n", 0, m.start()) + 1,
                          "col": m.start() - text.rfind("\n", 0, m.start()) - 1, "last": text.count("\n", 0, end) + 1,
                          "call": raw[m.start():end], "body": body,
                          "typeset": squeeze(note_source(template.replace("#1", body))) if template is not None else None})
    return found


def review_geometry(area):
    """The review copy's page: the export's (W x H, in points) with a gutter right of it, and with two
    columns one left of it too; snippet is the width the notes are typeset at."""
    two = area.get("columns") == 2
    gutter = round(area["W"] * (0.31 if two else 0.38))
    return {"W": area["W"], "H": area["H"], "two": two, "gutter": gutter, "left": gutter if two else 0,
            "width": area["W"] + gutter * (2 if two else 1), "snippet": gutter - REVIEW_INNER - REVIEW_TAG - REVIEW_OUTER}


def run_probe(server, out, job, doc, notes, geo):
    """The probe pass (REVIEW_PROBE) in out/, from the inputs of the export's last pass. Returns what
    its log records: {pages, pos: [[file, line, page, x, y, tex]], seen: [[file, line, tex]],
    snippets: {id: {size: [page, width, height, depth]}}, colors: {macro: [r, g, b]}}, with x, y in
    points from the page's top left, and sizes in points (width: of the widest line, which may be less
    than the page's, or more than the width set); {} if it didn't get as far as the notes. Raises
    RuntimeError for a log past OUTPUT_LIMIT."""
    rel = os.path.relpath(out, server.paper_root)
    with open(os.path.join(out, "probe.tex"), "w") as f:
        f.write(REVIEW_PROBE.replace("<WIDTH>", f"{geo['snippet']:.2f}").replace("<NOTES>", f"{rel}/notes"))
    with open(os.path.join(out, "notes.tex"), "w", encoding="latin-1") as f:
        for macro, (model, spec) in note_colors(server.paper_root, f"{doc}.pdf", {n["macro"] for n in notes}).items():
            f.write(f"\\latexlivecolor{{{macro}}}{{{model}}}{{{spec}}}\n")
        for key, label in note_citations(server.paper_root, f"{doc}.pdf", os.path.join(out, f"{job}.bbl"), notes).items():
            f.write(f"\\latexlivecite{{{key}}}{{{label}}}\n")
        for n in notes:
            f.write(f"\\latexlivesnippet{{{n['id']}}}{{{n['call']}}}\n")
    code, _ = run_tool(tex_argv(server, "pdflatex", "-interaction=batchmode", "-file-line-error", "-synctex=1",
                                f"-output-directory={out}", f"-jobname={job}", f"\\input{{{rel}/probe}}\\input{{{doc}}}"),
                       server.paper_root, kind="probe", env={**os.environ, "max_print_line": "1000000"})
    if code is None:
        return {}
    probe = {"pos": [], "seen": [], "snippets": {}, "colors": {}, "synctex": os.path.join(out, f"{job}.synctex.gz")}
    log = os.path.join(out, f"{job}.log")
    try:
        if os.path.getsize(log) > config.OUTPUT_LIMIT:
            raise RuntimeError(too_big(os.path.relpath(log, server.paper_root), config.OUTPUT_LIMIT))
        with open(log, "rb") as f:  # (only its records, and the line that says how many pages it wrote)
            lines = [line.decode("latin-1").rstrip("\r\n") for line in f if line.startswith((b"latex-live|", b"Output written on "))]
    except OSError:
        return {}
    for line in lines:
        if not line.startswith("latex-live|"):
            continue
        kind, *fields = line[len("latex-live|"):].split("|")
        try:
            if kind == "pos":
                path, ln, page, x, y = fields[:5]
                probe["pos"].append([os.path.normpath(path.lstrip("/")), int(ln), int(page), int(x) / 65536 * BP,
                                     geo["H"] - int(y) / 65536 * BP, "|".join(fields[5:])])
            elif kind == "seen":
                probe["seen"].append([os.path.normpath(fields[0].lstrip("/")), int(fields[1]), "|".join(fields[2:])])
            elif kind == "snippet":
                size = [float(v[:-2]) * BP for v in fields[3:6]]
                probe["snippets"].setdefault(int(fields[0]), {})[int(fields[1])] = [int(fields[2]), *size]
            elif kind == "color":
                probe["colors"][fields[0]] = [round(float(v), 4) for v in fields[1].split(",")]
            elif kind == "pages":
                probe["pages"] = int(fields[0])
        except (ValueError, IndexError):
            continue
    if "pages" not in probe:  # (it stopped before the notes: nothing it recorded can be trusted to be complete)
        return {}
    written = next(filter(None, (re.match(r"Output written on .*?\((\d+) pages?", line) for line in lines)), None)
    probe["pdf"] = os.path.isfile(os.path.join(out, f"{job}.pdf")) and bool(written) and int(written.group(1)) == (
        probe["pages"] + sum(len(s) for s in probe["snippets"].values()))
    if probe["pdf"]:
        os.replace(os.path.join(out, f"{job}.pdf"), os.path.join(out, "probe.pdf"))
    return probe


CITE_RE = re.compile(r"\\[a-zA-Z]*cite[a-zA-Z]*\*?\s*(?:\[[^\]]*\]\s*){0,2}\{([^{}]*)\}")
BIBITEM_RE = re.compile(r"\\bibitem\s*(?:\[(?:[^\[\]]|\[[^\]]*\])*\])?\s*\{([^{}]*)\}")


def note_citations(paper_root, pdf_name, bbl, notes):
    """{key: label} for the citations in notes that the export's bibliography (bbl) leaves out, being
    cited only in notes: the label they have in the build with notes, as natbib's authors and year
    (their number, or a numeric label, would point to another entry of the export's bibliography),
    else their key. As Latin-1, like the notes."""
    bibliography = read_output(paper_root, bbl, "latin-1")
    if bibliography is None:  # (no bibliography from BibTeX: \bibitem in the document, notes or not)
        return {}
    listed = set(BIBITEM_RE.findall(bibliography))
    keys = {k.strip() for n in notes for m in CITE_RE.finditer(n["body"]) for k in m.group(1).split(",")} - listed - {""}
    aux = read_output(paper_root, job_file(paper_root, pdf_name, ".aux"), "latin-1") or ""
    labels = {}
    for m in re.finditer(r"\\bibcite\{([^{}]*)\}(?=\{)", aux):
        labels[m.group(1)] = brace_group(aux, m.end())[0]
    found = {}
    for key in sorted(keys):
        label, name = labels.get(key, ""), escape_note(key)
        if label.startswith("{") and brace_group(label, 0)[1] < len(label):  # (natbib: {number}{year}{{authors}}{{full}})
            found[key] = "{" + name + "}" + label[brace_group(label, 0)[1]:]
        else:
            found[key] = label if label and not label.strip().isdigit() else name
    return found


def note_colors(paper_root, pdf_name, macros):
    """{note macro: (model, color)}: the first color its definition sets (\\color or \\textcolor),
    e.g. ("", "Emerald") or ("rgb", "0.1,0.5,0.2"), for its number and wire."""
    found = {}
    for path in project_tex_files(paper_root, fls_file(paper_root, pdf_name)):
        text = read_tex(path)
        for m in NOTE_DEF_RE.finditer(text):
            color = re.search(r"\\(?:text)?color\s*(?:\[([^\]]*)\])?\s*\{([^{}]*)\}", brace_group(text, m.end())[0])
            if m.group(1) in macros and m.group(1) not in found and color:
                found[m.group(1)] = (color.group(1) or "", color.group(2))
    return found


def synctex_pages(synctex_gz):
    """{page: [(kind, file, line, x, y, width, box)]}: a build's SyncTeX records in document order, in
    points from the page's top left. box is the hbox (a line) a record is directly in, as [left,
    baseline, right, start, contents] (start: after an indent box; contents: its records but the x
    ones, for comparing two builds), or None. Raises RuntimeError past SYNCTEX_LIMIT."""
    inputs, pages, page, stack, size = {}, {}, 0, [], 0
    raw = open_output(synctex_gz)
    if raw is None:
        raise FileNotFoundError(2, "No such file", synctex_gz)
    with raw, gzip.open(raw, "rt", errors="replace") as f:
        while record := f.readline(1 << 16):  # (a line past that comes in parts)
            size += len(record)
            if size > config.SYNCTEX_LIMIT:
                raise RuntimeError(too_big(os.path.basename(synctex_gz), config.SYNCTEX_LIMIT, " uncompressed"))
            c = record[:1]
            if record.startswith("Input:"):
                tag, _, path = record[6:].rstrip("\n").partition(":")
                inputs[tag] = os.path.normpath(path)
            elif c == "{":
                page, stack = to_int(record[1:], page), []
                pages[page] = []
            elif c in ")]":
                stack = stack[:-1]
            elif (m := SYNCTEX_ANY_RE.match(record)) and page in pages:
                kind, tag, line, h, v, w = m.group(1), m.group(2), int(m.group(3)), int(m.group(4)), int(m.group(5)), m.group(6)
                x, y, w = h / 65536 * BP, v / 65536 * BP, int(w or 0) / 65536 * BP
                box = stack[-1] if stack else None
                if kind == "h" and box and abs(x - box[0]) < 0.01 and box[3] == box[0]:
                    box[3] = x + w  # (a paragraph's indent)
                pages[page].append((kind, inputs.get(tag), line, x, y, w, box))
                if box is not None and kind != "x":
                    box[4].append((kind, round(x, 2), round(y, 2), round(w, 2)))
                if kind in "([":
                    stack.append([x, y, x + w, x, []] if kind == "(" else None)
    return pages


SYNCTEX_ANY_RE = re.compile(r"([\[(hvxkg$])(\d+),(\d+):(-?\d+),(-?\d+)(?::(-?\d+))?")


def synctex_same(a, b):
    """The pages two builds (synctex_pages) typeset the same way: the same records at the same
    places, whatever lines they are tagged with; b may have extra x records (one marks where text
    stopped, and the probe's records add some)."""
    def key(r):
        return r[0], round(r[3], 2), round(r[4], 2), round(r[5], 2)
    same = set()
    for page, recs in a.items():
        other, j = b.get(page, []), 0
        for r in recs:
            while j < len(other) and key(other[j]) != key(r) and other[j][0] == "x":
                j += 1
            if j >= len(other) or key(other[j]) != key(r):
                break
            j += 1
        else:
            if all(r[0] == "x" for r in other[j:]):
                same.add(page)
    return same


def same_line(export, probe):
    """A test of the probe's spots: same_line(page, x, y) says whether the line (innermost hbox)
    at that spot of the probe is typeset as in the export: at the same place, with the same records
    (whole pages typeset the same pass at once). A note's spot depends only on its line."""
    whole, lines = synctex_same(export, probe), {}
    for page, recs in export.items():
        for r in recs:
            if r[6] is not None:
                lines[(page, round(r[6][0], 2), round(r[6][1], 2), round(r[6][2], 2))] = r[6][4]

    def test(page, x, y):
        if page in whole:
            return True
        boxes = [r[6] for r in probe.get(page, []) if r[6] is not None and abs(r[6][1] - y) < 0.05
                 and r[6][0] - 0.5 <= x <= r[6][2] + 0.5]
        box = min(boxes, key=lambda b: b[2] - b[0], default=None)
        return bool(box) and lines.get((page, round(box[0], 2), round(box[1], 2), round(box[2], 2))) == box[4]
    return test


def squeeze(tex):
    """tex without its spaces, for comparing a note's source with the probe's \\detokenize of it."""
    tex = re.sub(r"\^\^([0-9a-f]{2})", lambda m: chr(int(m.group(1), 16)), tex)
    return re.sub(r"\s+", "", tex.replace("##", "#"))


def review_anchors(paper_root, out, notes, probe, area):
    """The notes that are typeset, each with its spot in the export: [{**note, page, x, y, how}],
    x, y in points from the page's top left (y at the baseline). how is "exact" (the probe's
    record, on a line typeset as in the export) or "line" (approximate: the export's SyncTeX, by
    source line, or else the probe's record on a line it typeset a little differently)."""
    export = synctex_pages(os.path.join(out, "export.synctex.gz"))
    trusted = same_line(export, synctex_pages(probe["synctex"])) if probe and os.path.isfile(probe["synctex"]) else None
    records = [("pos", *r) for r in probe.get("pos", [])] + [("seen", r[0], r[1], None, None, None, r[2]) for r in probe.get("seen", [])]
    matched = match_records(notes, records)
    lines = {}
    for page, recs in export.items():
        for r in recs:
            if r[0] in "hvxkg$" and r[1] and area["top"] - 15 <= r[4] <= area["bottom"] + 15 and 0 <= r[3] <= area["W"]:
                lines.setdefault((os.path.relpath(r[1], paper_root), r[2]), []).append((page, *r))
    placed = []
    for n in notes:
        recs = matched.get(n["id"], [])
        exact = [r for r in recs if r[0] == "pos" and trusted and trusted(r[3], r[4], r[5])]
        if exact:
            inside = [r for r in exact if area["top"] - 15 <= r[5] <= area["bottom"] + 15]
            r = (inside or exact)[0]
            placed.append({**n, "page": r[3], "x": round(r[4], 2), "y": round(r[5], 2), "how": "exact"})
            continue
        if not recs and ("pages" in probe or not any((n["file"], ln) in lines for ln in range(n["line"] - 3, n["last"] + 4))):
            continue  # (not typeset: the probe went through and never met it; or, without a probe, nothing near it shows)
        probed = [r for r in recs if r[0] == "pos"]  # (on a line the probe set a little differently)
        spot = None
        if not probed or any(r[1] == n["file"] and n["line"] <= r[2] <= n["last"] for r in probed):
            spot = line_anchor(paper_root, n, lines, vertical=any(r[0] == "seen" for r in recs))
        if not spot and probed:  # (read elsewhere, as in a macro defined in another file: the probe's spot, near enough)
            spot = probed[0][3:6]
        if spot:
            placed.append({**n, "page": spot[0], "x": round(spot[1], 2), "y": round(spot[2], 2), "how": "line"})
    return placed


def match_records(notes, records):
    """{note id: [record]}: which note each of the probe's records (kind, file, line, ..., tex) is
    about. A note is recorded when TeX typesets it, at the line TeX is reading then: its own last
    line, or later in an argument read whole (a caption). So a record goes to the note above it in
    its file whose text it holds (the longest such text, then the nearest note), else to a note in
    another file whose text it holds (one in a macro used here), preferring notes whose macro typesets
    exactly that (so two authors' notes with the same text don't swap); records of one line and text
    go in order. A note whose typeset text is recorded somewhere takes no record of another text,
    which is another macro's: a protected marker's, say (no note), whose text may hold the note's."""
    by_file, taken, found = {}, set(), {}
    for n in notes:
        by_file.setdefault(n["file"], []).append(n)
    keyed = {}
    for r in records:
        keyed.setdefault((r[1], r[2], squeeze(r[-1])), []).append(r)
    recorded = {tex for _, _, tex in keyed}

    def holds(n, tex):
        return squeeze(note_source(n["body"])) in tex and (n.get("typeset") == tex or n.get("typeset") not in recorded)

    for (path, line, tex), group in keyed.items():
        candidates = [n for n in (by_file.get(path, []) if path else notes) if n["last"] <= line and holds(n, tex)]
        if not candidates:  # (a note in a macro defined in another file, recorded where the macro is used)
            candidates = [n for n in notes if n["file"] != path and holds(n, tex)]
        candidates = [n for n in candidates if n.get("typeset") == tex] or candidates  # (its own author's)
        free = [n for n in candidates if n["id"] not in taken] or candidates
        chosen = sorted(free, key=lambda n: (len(squeeze(note_source(n["body"]))), n["last"], n["id"]))[-len(group):]
        for i, r in enumerate(group):
            if chosen:
                n = chosen[min(i, len(chosen) - 1)]
                taken.add(n["id"])
                found.setdefault(n["id"], []).append(r)
    return found


def note_source(body):
    """A note's argument as TeX reads it: without comments, blank lines as \\par."""
    body = re.sub(r"(?<!\\)%[^\n]*\n?[ \t]*", "", body)
    return re.sub(r"\n[ \t]*\n\s*", r"\\par ", body)


def line_anchor(paper_root, n, lines, vertical):
    """A note's spot from the export's SyncTeX, by source line, when the probe has none: after the
    words before it on its line (a note in a paragraph), else at the start of the next typeset line
    (a note before a paragraph or between two; in a file \\input there, if that comes first), else
    after the last typeset words before it; not in a float the note isn't in (floats move). (page, x,
    y) or None; lines is {(file, line): [(page, kind, file, line, x, y, width, box)]}."""
    path = os.path.join(paper_root, n["file"])
    if not vertical:
        with open(path, "rb") as f:
            raw = f.read().decode("latin-1").split("\n")
        before = re.sub(r"(?<!\\)%.*", "", raw[n["line"] - 1][:n["col"]] if n["line"] <= len(raw) else "")
        while m := re.search(r"\\(?:footnote|footnotetext|marginpar)\s*(?=\{)", before):  # (typeset elsewhere)
            before = before[:m.start()] + before[brace_group(before, m.end())[1]:]
        words = len(tex_to_text(re.sub(r"\$[^$]*(\$|$)", " ", before), {}).split())
        ends = [r for r in lines.get((n["file"], n["line"]), []) if r[1] == "x"]
        if words and len(ends) >= words:
            r = ends[words - 1]
            return r[0], r[4], r[5]
    text, floats = read_tex(path), []
    for m in FLOAT_ENV_RE.finditer(text):
        line = text.count("\n", 0, m.start()) + 1
        if m.group(1) == "begin":
            floats.append([line, line])
        elif floats:
            floats[-1][1] = line
    moved = {ln for a, b in floats if not a <= n["line"] <= b for ln in range(a, b + 1)}
    source = text.split("\n")
    for ln in range(n["last"], n["last"] + 40):
        recs = None if ln in moved else lines.get((n["file"], ln))
        for m in INPUT_RE.finditer(source[ln - 1] if not recs and ln <= len(source) else ""):
            name = os.path.normpath(m.group(1).strip())
            child = next((f for f in (name, name + ".tex") if any(k[0] == f for k in lines)), None)
            if child:
                recs = lines[min(k for k in lines if k[0] == child)]
                break
        if recs:  # (its first words: a line's first records can belong to the row or line before it)
            page, _, _, _, x, y, _, box = next((r for r in recs if r[1] == "x"), recs[0])
            return (page, box[3], box[1]) if box else (page, x, y)
    for ln in range(n["line"], max(0, n["line"] - 40), -1):
        if ln not in moved and (recs := lines.get((n["file"], ln))):
            return recs[-1][0], recs[-1][4], recs[-1][5]
    return None


def place_notes(want, heights, lo, hi, gap):
    """Tops for boxes of these heights, in this order, without overlap (gap between them), within
    lo..hi, each as near its wanted top as the others allow (least squares). Needs them to fit."""
    offsets, total = [], 0
    for h in heights:
        offsets.append(total)
        total += h + gap
    upper = hi - (total - gap)  # (where the first box's top can go, at most, with all below it)
    blocks = []  # (pooled adjacent violators: a block's boxes move together)
    for target in (w - o for w, o in zip(want, offsets)):
        blocks.append([target, 1])
        while len(blocks) > 1 and blocks[-2][0] * blocks[-1][1] > blocks[-1][0] * blocks[-2][1]:
            s, c = blocks.pop()
            blocks[-1][0] += s
            blocks[-1][1] += c
    shifts = [min(max(s / c, lo), upper) for s, c in blocks for _ in range(c)]
    return [s + o for s, o in zip(shifts, offsets)]


def review_layout(placed, probe, geo, pages):
    """Where everything goes on the review copy's pages: {pages, paper, width, height, left, W (the
    paper's page width), notes} (points from the top left), each note with its text, its number n,
    side, size (0 or 1: the smaller type),
    top (in its gutter), or continued (the page it continues on). Notes are numbered in reading order
    (page, column, top to bottom), and in each gutter they keep that order, each as near its spot as
    the others allow. When they don't fit, they are set smaller; when they still don't, the first
    ones that fit stay, and the rest continue on pages after the paper (continuation_pages), with a
    line in the gutter saying where."""
    W, H, snippet = geo["W"], geo["H"], geo["snippet"]
    for n in placed:
        n["side"] = "left" if geo["two"] and n["x"] < W / 2 else "right"
    placed.sort(key=lambda n: (n["page"], n["side"] == "right", n["y"], n["x"], n["id"]))
    snippets = probe.get("snippets", {}) if probe.get("pdf") else {}
    for number, n in enumerate(placed, 1):
        n["n"], sizes = number, snippets.get(n["id"], {})
        if 0 in sizes and 1 in sizes:
            n["sizes"] = []
            for page, text, ht, dp in (sizes[0], sizes[1]):
                w = max(text, snippet)  # (the page the probe shipped it on)
                scale = min(1, snippet / w)  # (a long word or URL sticks out: shrink it to fit)
                n["sizes"].append({"page": page, "w": round(w * scale, 2), "text": round(text * scale, 2),
                                   "ht": round(ht * scale, 2), "h": round((ht + dp) * scale, 2)})
        else:  # (the probe didn't typeset it: its text, roughly sized)
            lines = math.ceil((len(review_text(n)) + len(n["author"]) + 2) / (snippet / 3.7))
            n["sizes"] = [{"page": None, "w": snippet, "text": snippet, "ht": 6.0, "h": 8.6 * lines + 2} for _ in range(2)]
    lo, hi = REVIEW_MARGIN, H - REVIEW_MARGIN
    for _, group in itertools.groupby(placed, key=lambda n: (n["page"], n["side"])):
        group = list(group)
        for size in (0, 1):
            heights = [n["sizes"][size]["h"] for n in group]
            if sum(heights) + REVIEW_GAP * (len(group) - 1) <= hi - lo:
                keep = len(group)
                break
        else:  # (smaller type isn't enough: as many as fit, then a line saying where the rest are)
            keep = 0
            while keep < len(group) and sum(heights[:keep + 1]) + REVIEW_GAP * keep <= hi - lo - 12:
                keep += 1
        tops = place_notes([n["y"] - n["sizes"][size]["ht"] for n in group[:keep]], heights[:keep], lo,
                           hi - (12 if keep < len(group) else 0), REVIEW_GAP)
        for i, n in enumerate(group):
            n["size"], n["top"], n["continued"] = size, round(tops[i], 2) if i < keep else None, None
    extra = continuation_pages(placed, geo)
    for i, page in enumerate(extra):
        for n, *_ in page:
            n["continued"] = pages + 1 + i
    return {"pages": pages + len(extra), "paper": pages, "width": geo["width"], "height": H, "left": geo["left"], "W": W,
            "notes": [{**{k: v for k, v in n.items() if k not in ("call", "body", "typeset")}, "text": review_text(n)} for n in placed]}


def continuation_pages(notes, geo):
    """The notes that don't fit in their gutter (top None), on pages after the paper, in number order:
    [[(note, x, top, scale, heading)]], in columns as wide as a gutter's notes, top to bottom, under
    the number of the page they belong to (heading: whether it goes above this note); a note taller
    than a page is scaled to fit."""
    width = REVIEW_TAG + geo["snippet"]
    columns = max(1, int((geo["width"] - 2 * 36 + 18) // (width + 18)))
    top, bottom = 50, geo["H"] - 36
    pages, page, column, y, last = [], [], 0, top, None
    for n in sorted((n for n in notes if n["top"] is None), key=lambda n: n["n"]):
        heading = n["page"] != last or y == top
        h = (12 if heading else 0) + n["sizes"][0]["h"]
        scale = min(1, (bottom - top) / h)
        if y + h * scale > bottom and y > top:
            column, y = column + 1, top
            if column == columns:
                pages.append(page)
                page, column = [], 0
            if not heading:
                heading, h = True, h + 12
                scale = min(1, (bottom - top) / h)
        page.append((n, 36 + column * (width + 18), y, scale, heading))
        y, last = y + h * scale + 2 * REVIEW_GAP, n["page"]
    return pages + ([page] if page else [])


def review_text(n):
    """A note as plain text, for when the probe couldn't typeset it."""
    body = n["body"].encode("latin-1").decode("utf-8", errors="replace")
    return " ".join(tex_to_text(note_source(body), {}).split())


def review_tex(plan, geo, colors, notes):
    """The LaTeX source of the review copy (plan: review_layout; notes: the same notes, with their
    text, for any the probe couldn't typeset)."""
    W, H, left = geo["W"], geo["H"], geo["left"]
    text = {n["id"]: n for n in notes}
    out = [REVIEW_HEAD]
    for macro in sorted({n["macro"] for n in plan["notes"]}):
        r, g, b = colors.get(macro, (0.35, 0.35, 0.35))
        out.append(f"\\definecolor{{latexlive{macro}}}{{rgb}}{{{r},{g},{b}}}\n")

    def f(v):
        return f"{v:.2f}".rstrip("0").rstrip(".")

    def note(n, x, top, size, scale=1.0, tag_right=False):
        """The note with its top left at (x, top), and its number left of its first line (or right of
        its widest line, in a left gutter). Returns the TikZ code, and the number's center."""
        s, color = n["sizes"][size], f"latexlive{n['macro']}"
        w, ht = s["w"] * scale, s["ht"] * scale
        tag = (x + s["text"] * scale + REVIEW_TAG / 2 if tag_right else x - REVIEW_TAG / 2, H - top - ht + 2.4)
        if s["page"]:
            body = f"\\includegraphics[page={s['page']},width={f(w)}bp]{{probe.pdf}}"
        else:
            body = (f"\\parbox[t]{{{f(w)}bp}}{{\\raggedright\\sffamily\\fontsize{{7}}{{8.6}}\\selectfont\\textcolor{{{color}}}"
                    f"{{\\textbf{{{escape_note(n['author'])}:}}}} {escape_note(review_text(text[n['id']]))}}}")
        return (f"\\node[anchor=north west,inner sep=0pt,outer sep=0pt] at ({f(x)},{f(H - top)}) {{{body}}};\n"
                f"\\node[latexlive tag,fill={color}] at ({f(tag[0])},{f(tag[1])}) {{{n['n']}}};\n"), tag

    by_page = {}
    for n in plan["notes"]:
        by_page.setdefault(n["page"], []).append(n)
    for p in range(1, plan["paper"] + 1):
        wires, marks, boxes = [], [], []
        here = by_page.get(p, [])
        spread, run = {}, []  # (notes at one spot: their numbers side by side)
        for n in sorted(here, key=lambda n: (n["y"], n["x"], n["n"])) + [None]:
            if run and (n is None or abs(n["y"] - run[0]["y"]) > 1.5 or abs(n["x"] - run[0]["x"]) > 3):
                for i, m in enumerate(run):
                    spread[m["n"]] = (i - (len(run) - 1) / 2) * 6
                run = []
            run.append(n)
        for n in here:
            color, right = f"latexlive{n['macro']}", n["side"] == "right"
            ax, base = left + n["x"], H - n["y"]
            marks.append(f"\\draw[{color},line width=.7bp] ({f(ax)},{f(base - 2.2)}) -- ({f(ax)},{f(base + 6.4)});\n"
                         f"\\node[latexlive mark,fill={color}] at ({f(ax + spread[n['n']])},{f(base + 9.2)}) {{{n['n']}}};\n")
            if n["top"] is None:
                continue
            edge = left + W if right else left
            s = n["sizes"][n["size"]]
            x = edge + REVIEW_INNER + REVIEW_TAG if right else edge - REVIEW_INNER - REVIEW_TAG - s["text"]
            code, (tx, ty) = note(n, x, n["top"], n["size"], tag_right=not right)
            boxes.append(code)
            end = tx - 3.6 if right else tx + 3.6
            bend = max(ax, edge - 10) if right else min(ax, edge + 10)  # (the bend starts in the page's own margin)
            mid, y = (bend + end) / 2, base - 2.2
            wires.append(f"\\draw[{color},line width=.4bp,dash pattern=on 1.2bp off 1.1bp] ({f(ax)},{f(y)}) -- ({f(bend)},{f(y)});\n"
                         f"\\draw[{color},line width=.4bp] ({f(bend)},{f(y)}) .. controls ({f(mid)},{f(y)}) and ({f(mid)},{f(ty)})"
                         f" .. ({f(end)},{f(ty)});\n")
        for side in ("left", "right"):
            rest = [n for n in here if n["side"] == side and n["top"] is None]
            if rest:
                where = f"{rest[0]['n']}" + (f"--{rest[-1]['n']}" if len(rest) > 1 else "")
                x = left + W + REVIEW_INNER if side == "right" else REVIEW_OUTER
                boxes.append(f"\\node[anchor=south west,inner sep=0pt,font=\\sffamily\\itshape\\fontsize{{6.5}}{{8}}\\selectfont,"
                             f"text=black!65] at ({f(x)},{f(REVIEW_MARGIN)}) {{Notes {where} continue on page {rest[0]['continued']}.}};\n")
        # (gutters light gray, so the page's edges show; the page white under the paper's, which may have no background)
        paper = (f"\\fill[black!7] (0,0) rectangle ({f(plan['width'])},{f(H)});\n"
                 f"\\fill[white] ({f(left)},0) rectangle ({f(left + W)},{f(H)});\n")
        out.append(f"\\latexlivepage{{{f(plan['width'])}}}{{{f(H)}}}{{{f(left)}}}{{\\includegraphics[page={p}]{{export.pdf}}}}"
                   f"{{%\n{paper}{''.join(wires + marks + boxes)}}}\n")
    for i, page in enumerate(continuation_pages(plan["notes"], geo)):
        draw = [f"\\node[anchor=north west,inner sep=0pt,font=\\sffamily\\bfseries\\fontsize{{9}}{{11}}\\selectfont] at (36,{f(H - 24)})"
                f" {{Notes that didn't fit in the margin{' (continued)' if i else ''}}};\n"]
        for n, x, top, scale, heading in page:
            if heading:
                draw.append(f"\\node[anchor=north west,inner sep=0pt,font=\\sffamily\\bfseries\\fontsize{{6.5}}{{8}}\\selectfont,"
                            f"text=black!65] at ({f(x)},{f(H - top)}) {{Page {n['page']}}};\n")
            draw.append(note(n, x + REVIEW_TAG, top + (12 if heading else 0) * scale, 0, scale)[0])
        out.append(f"\\latexlivepage{{{f(plan['width'])}}}{{{f(H)}}}{{0}}{{}}{{%\n{''.join(draw)}}}\n")
    out.append("\\end{document}\n")
    return "".join(out)


def start_diff(server, rev, any_rev=True):
    """POST /diff {rev} (and GET /diff?rev= without shared mode): build <doc>-diff.pdf, a latexdiff of
    rev against the working tree, in the background. Without any_rev (a coauthor), only a commit that
    HEAD builds on: other branches, tags and the stash may hold what isn't shared. (Its history is: the
    shared copy is a branch of the paper's, and coauthors compare with any commit it builds on.)"""
    if not re.fullmatch(r"[\w./^~-]{1,80}", rev) or rev.startswith("-"):
        return 400, {"error": "bad rev"}
    if server.diff.get("state") == "running":
        return 409, {"error": "a diff is already building"}
    sha = subprocess.run(["git", "-C", server.paper_root, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"],
                         capture_output=True, text=True).stdout.strip()
    if not sha:
        return 404, {"error": f"unknown revision {rev}"}
    if not any_rev and subprocess.run(["git", "-C", server.paper_root, "merge-base", "--is-ancestor", sha, "HEAD"],
                                      capture_output=True).returncode != 0:
        return 403, {"error": f"{rev} is not a commit this version of the paper builds on"}
    server.diff = {"rev": rev, "sha": sha[:9], "state": "running", "time": time.strftime("%H:%M:%S"), "msg": ""}
    threading.Thread(target=build_diff, args=(server, sha), daemon=True).start()
    return 200, server.diff


def tex_pathspec():
    """The git pathspec of the paper's .tex files that baselines and diffs copy into build/ (where the
    containers see them): all of them; in shared mode, none in a dot folder or named with a dot."""
    return [":(glob)**/*.tex", *([":(exclude,glob)**/.*", ":(exclude,glob)**/.*/**"] if config.SHARED else [])]


def build_diff(server, sha):
    """latexdiff --flatten (TeX Live image) of <doc>.tex at sha vs now, then two pdflatex passes.

    Both sides are copies of just the .tex files (old: `git archive`; new: the working
    tree), so latexdiff doesn't inline a stale <doc>.bbl from the paper root; figures,
    styles, and the bibliography are the current ones. Runs in build/diff/; the PDF is
    published to build/<doc>-diff.pdf.
    """
    root = server.paper_root
    doc = server.default_pdf[:-4]
    out = os.path.join(root, config.BUILD, "diff")
    old, new = os.path.join(out, "old"), os.path.join(out, "new")
    job = f"{doc}-diff"

    def run(cmd, **kw):
        return subprocess.run(cmd, capture_output=True, text=True, cwd=root, **kw)

    try:
        for d in (old, new):
            shutil.rmtree(d, ignore_errors=True)
            os.makedirs(d)
        archive = subprocess.Popen(["git", "-C", root, "archive", sha, "--", *tex_pathspec()], stdout=subprocess.PIPE)
        subprocess.run(["tar", "-x", "-C", old], stdin=archive.stdout, check=True)
        archive.wait()
        tracked = run(["git", "ls-files", "-co", "--exclude-standard", "--", *tex_pathspec()]).stdout.splitlines()
        for rel_path in tracked:
            if os.path.isfile(os.path.join(root, rel_path)):
                os.makedirs(os.path.dirname(os.path.join(new, rel_path)), exist_ok=True)
                shutil.copyfile(os.path.join(root, rel_path), os.path.join(new, rel_path))
        pulled = image_pulled()
        if config.SHARED and not pulled:  # (shared mode never runs the host's TeX tools)
            raise RuntimeError(f"{config.IMAGE_MISSING}; meanwhile there is no diff")
        exec_ = container_argv(root) if pulled else []  # else the host's, if it has latexdiff
        code, text = run_tool([*exec_, "latexdiff", "--flatten", "--type=UNDERLINE", "--math-markup=coarse",
                               "--config", "PICTUREENV=(?:picture|DIFnomarkup|tikzpicture|NiceTabular|tabular)[\\w\\d*@]*",
                               os.path.join(old, f"{doc}.tex"), os.path.join(new, f"{doc}.tex")], root, timeout=300, kind="diff",
                              capture=config.OUTPUT_LIMIT + 1)
        if code != 0 or not text.strip():
            raise RuntimeError("latexdiff took more than 5 minutes" if code is None else "latexdiff failed")
        if len(text.encode()) > config.OUTPUT_LIMIT:
            raise RuntimeError(too_big("latexdiff's output", config.OUTPUT_LIMIT))
        with open(os.path.join(out, f"{job}.tex"), "w") as fh:
            fh.write(text)
        for ext in (".aux", ".bbl", ".brf"):  # current references, so the diff needs no bibtex run
            if os.path.exists(job_file(root, server.default_pdf, ext)):
                shutil.copyfile(job_file(root, server.default_pdf, ext), os.path.join(out, job + ext))
        tex = [*exec_, "pdflatex", "-interaction=nonstopmode", f"-output-directory={out}", f"-jobname={job}",
               os.path.relpath(os.path.join(out, f"{job}.tex"), root)]  # (relative: shared mode's TeX opens no absolute path)
        for _ in range(2):
            if run_tool(tex, root, timeout=300, kind="diff", env={**os.environ, "max_print_line": "10000"})[0] is None:
                raise RuntimeError("a pdflatex pass took more than 5 minutes")
        pdf = os.path.join(out, f"{job}.pdf")
        if not os.path.isfile(pdf):
            log = read_capped(os.path.join(out, f"{job}.log")) or ""
            raise RuntimeError(next((l for l in log.splitlines() if l.startswith("! ")), "pdflatex produced no PDF"))
        os.replace(pdf, os.path.join(root, config.BUILD, f"{job}.pdf"))
        server.diff = {**server.diff, "state": "done", "time": time.strftime("%H:%M:%S")}
    except Exception as e:  # reported in the diff viewer's banner
        server.diff = {**server.diff, "state": "failed", "time": time.strftime("%H:%M:%S"), "msg": str(e)[:300]}


def base_status(server, pdf_name, kind, build=True):
    """A baseline the viewer diffs against to mark changes: PDFs of the document as committed at
    HEAD ("head") or at the last push ("push", pushed_commit), rebuilt whenever that commit moves
    (build_base); with build=False, only the commit is reported (the push baseline is built only
    once a viewer shows it). When the last push is HEAD, the push baseline reuses HEAD's PDFs
    (share_head_base). The commit is checked every few seconds. The versions appear once a
    build has finished; None outside git (or with nothing pushed), and for a document the server
    is not building."""
    root = server.paper_root
    if pdf_name != server.default_pdf:
        return None
    lock = server.base_locks[kind]
    due = time.monotonic() - server.base_checked[kind] > 3
    if (due or build and server.bases[kind].get("state") == "waiting") and lock.acquire(blocking=False):
        try:
            b = server.bases[kind]
            if due:
                server.base_checked[kind] = time.monotonic()
                if kind == "head":
                    r = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True, text=True)
                    sha, ref = r.stdout.strip() if r.returncode == 0 else None, None
                else:
                    sha, ref = pushed_commit(root)
                if not sha:
                    server.bases[kind] = b = {}
                elif (sha, pdf_name) != (b.get("sha"), b.get("pdf")) and b.get("state") != "running":
                    server.bases[kind] = b = {"sha": sha, "ref": ref, "pdf": pdf_name, "state": "waiting", "msg": ""}
            sha = b.get("sha")
            if b.get("state") == "waiting" and build and os.path.isfile(job_file(root, pdf_name, ".aux")):
                head = server.bases["head"]
                # (in shared mode HEAD's baseline is built only when a viewer asks for it: then it isn't waited for)
                head_coming = not config.SHARED or head.get("built") == sha or head.get("state") in ("running", "failed")
                if kind == "push" and (head.get("sha"), head.get("pdf")) == (sha, pdf_name) and head_coming:
                    if head.get("built") == sha:  # (else wait for the HEAD build)
                        share_head_base(server, pdf_name)
                        b.update(state="done", built=sha)
                    elif head.get("state") == "failed":
                        b.update(state="failed", msg=head.get("msg", ""))
                else:
                    b["state"] = "running"
                    threading.Thread(target=build_base, args=(server, kind, sha, pdf_name), daemon=True).start()
        finally:
            lock.release()
    b = server.bases[kind]
    if b.get("pdf") != pdf_name:
        return None
    info = {"sha": b["sha"][:9], "ref": b["ref"], "state": b["state"], "msg": b.get("msg", "")}
    if b.get("built"):
        for key, name in (("version", f"-{kind}.pdf"), ("clean", f"-{kind}-clean.pdf")):
            path = pdf_path(root, pdf_name[:-4] + name)
            if os.path.isfile(path):
                info[key] = str(mtime(path))
    return info


def share_head_base(server, pdf_name):
    """The push baseline when nothing is unpushed: build/<doc>-push[-clean].pdf as links to the
    HEAD baseline's PDFs, which a rebuild of that baseline replaces rather than rewrites."""
    for suffix in ("", "-clean"):
        head, push = (pdf_path(server.paper_root, f"{pdf_name[:-4]}-{kind}{suffix}.pdf") for kind in ("head", "push"))
        try:
            os.remove(push)
        except FileNotFoundError:
            pass
        if os.path.isfile(head):
            try:
                os.link(head, push)
            except OSError:
                shutil.copy2(head, push)


def link_missing(source, target, skip=()):
    """Link each entry of the folder source into target that target lacks (but those named in skip, and
    dot entries, at any depth: .git, .env, which the build never needs), and do the same inside each
    folder that both have: git archive made those in target for the .tex files in them, and the rest of
    their files (fonts, images) must resolve there too."""
    for name in os.listdir(source):
        here, there = os.path.join(source, name), os.path.join(target, name)
        if name in skip or name.startswith("."):
            continue
        if not os.path.lexists(there):
            os.symlink(here, there)
        elif os.path.isdir(here) and not os.path.islink(here) and os.path.isdir(there) and not os.path.islink(there):
            link_missing(here, there)


def build_base(server, kind, sha, pdf_name):
    """The document as committed at sha, for the kind of baseline (base_status): build/<doc>-<kind>.pdf,
    plus build/<doc>-<kind>-clean.pdf without notes (when the live build has a notes-free PDF).

    Like build_diff, only the .tex files come from git (`git archive` into build/<kind>/<doc>/src/).
    Figures, styles, fonts and the bibliography resolve to the working tree's (through TEXINPUTS,
    and links for everything else, such as a class's font directory: link_missing), so
    the diff shows text edits; the viewer marks changed figures itself. The live build's .aux/.bbl
    seed the references: two passes with notes, then one without (two with line numbers,
    latex_report.uses_lineno).
    """
    root = server.paper_root
    doc = pdf_name[:-4]
    out = os.path.join(root, config.BUILD, kind, doc)
    src = os.path.join(out, "src")
    job = f"{doc}-{kind}"
    sys.path.insert(0, config.HERE)
    import latex_report

    def tex(jobname, source):
        code, _ = run_tool(tex_argv(server, "pdflatex", "-interaction=batchmode", f"-output-directory={out}", f"-jobname={jobname}",
                                    source), src, timeout=300, kind=f"base-{kind}", env={**os.environ, "TEXINPUTS": f"{src}:{root}:"})
        if code is None:
            raise RuntimeError("a pass took more than 5 minutes")
        return os.path.join(out, f"{jobname}.pdf")

    try:
        shutil.rmtree(out, ignore_errors=True)
        os.makedirs(src)
        if config.SHARED and not image_pulled():
            raise RuntimeError(f"{config.IMAGE_MISSING}; meanwhile there is no baseline")
        archive = subprocess.Popen(["git", "-C", root, "archive", sha, "--", *tex_pathspec()], stdout=subprocess.PIPE)
        subprocess.run(["tar", "-x", "-C", src], stdin=archive.stdout, check=True)
        archive.wait()
        if not os.path.isfile(os.path.join(src, f"{doc}.tex")):
            raise RuntimeError(f"{doc}.tex is not in {sha[:9]}")
        link_missing(root, src, skip=(".git", config.BUILD.split(os.sep)[0]))  # (fonts are not looked up through TEXINPUTS)
        for ext in (".aux", ".bbl", ".brf"):  # (.brf: hyperref's back-references)
            if os.path.exists(job_file(root, pdf_name, ext)):
                shutil.copyfile(job_file(root, pdf_name, ext), os.path.join(out, job + ext))
        for _ in range(2):
            pdf = tex(job, doc)
        if not os.path.isfile(pdf):
            raise RuntimeError(f"pdflatex produced no PDF; see {config.BUILD}/{kind}/{doc}/{job}.log")
        clean_pdf = None
        if os.path.isfile(pdf_path(root, f"{doc}-clean.pdf")):
            for ext in (".aux", ".bbl", ".brf", ".out", ".toc"):
                if os.path.exists(os.path.join(out, job + ext)):
                    shutil.copyfile(os.path.join(out, job + ext), os.path.join(out, f"{job}-clean{ext}"))
            clean_pdf = tex(f"{job}-clean", f"{latex_report.NOTES_OFF}\\input{{{doc}}}")
            if latex_report.uses_lineno(os.path.join(out, f"{job}-clean.aux")):
                clean_pdf = tex(f"{job}-clean", f"{latex_report.NOTES_OFF}\\input{{{doc}}}")
        os.replace(pdf, os.path.join(root, config.BUILD, f"{job}.pdf"))
        if clean_pdf and os.path.isfile(clean_pdf):
            os.replace(clean_pdf, os.path.join(root, config.BUILD, f"{job}-clean.pdf"))
        state = {"state": "done", "built": sha}
    except Exception as e:  # reported in the viewer's tooltip; the previous baseline stays
        state = {"state": "failed", "msg": str(e)[:300]}
    if server.bases[kind].get("pdf") == pdf_name:  # (not after a switch to another document)
        server.bases[kind] = {**server.bases[kind], **state}


def serve(paper_root, default_pdf, port, page_limit):
    """The instance's server, set up; main starts serving (server.serve_forever in a thread)."""
    # A small range, for tunnels that forward only a few ports. A shared instance takes exactly its
    # port: the proxy sends coauthors there, and must not reach another preview that holds it.
    for p in [port] if config.SHARED else range(port, port + 10):
        try:
            server = (LoopbackServer if config.SHARED else DualStackServer)(("::", p), Handler)
            break
        except OSError:
            continue
    else:
        sys.exit(f"latex-live: port {port} is taken; a shared instance serves exactly --port" if config.SHARED
                 else f"latex-live: no free port in {port}-{port + 9}")
    server.daemon_threads = True
    server.git_name = None  # (owner_name)
    server.paper_root = paper_root
    server.paper_id = paper_id(paper_root)  # (checked against the hub's requests: Handler.misdirected)
    server.default_pdf = default_pdf
    server.page_limit = page_limit
    server.diff = {}
    server.export, server.review = {}, {}  # (start_export, start_review)
    server.download_lock = threading.Lock()  # (their states: checked and started at once, never over a newer one)
    server.review_lock = threading.Lock()  # (build_review)
    server.export_lock, server.exported = threading.Lock(), {}  # (exact_export)
    server.bases, server.base_checked = {"head": {}, "push": {}}, {"head": 0, "push": 0}  # (base_status)
    server.base_locks = {"head": threading.Lock(), "push": threading.Lock()}
    # The tex-exec prefix, as for latexmk (main sets it). In shared mode the confined image from the
    # start: a request (an export, a baseline) may come before main gets there, and must not run the host's TeX.
    server.tex_exec = container_exec(os.path.realpath(paper_root)) if config.SHARED else ""
    server.image_checked, server.image_note = 0, None
    server.latexmk, server.lock, server.stopping, server.retried, server.no_build = None, None, False, -60, False
    server.requests = queue.Queue()  # document switches and rebuilds, for the main thread (on_main_thread)
    server.editable = None  # (editable_files)
    server.versions, server.versions_lock = collections.OrderedDict(), threading.Lock()  # (remember_version)
    server.last_saves = collections.OrderedDict()  # (save_file: by user, file and editor, the last 256)
    server.last_writer = {}  # (wrote)
    server.autocommit, server.pending, server.write_seq, server.commit_said = False, {}, 0, None  # (remember_write)
    server.pending_lock = threading.Lock()
    server.pending_file = os.path.join(config.STATE, "pending", f"{server.paper_id}.json")  # (save_pending)
    server.git_dir = None  # (git_dir)
    server.results, server.results_lock = {}, threading.Lock()  # (shared_result)
    server.checks, server.checks_running, server.checks_lock = {}, {}, threading.Lock()  # (cached_checks)
    # shared_build's state: whether a run is on, its status and the notes-free pass's, what it published, the
    # tools' states (bib, biber, idx) and the rebuilds' generation (gen), the files it missed or depends on beyond
    # its file list (missing, deps), and the requests to build again (POST /rebuild: admit; at most every 20 s
    # for coauthors), and what stopped runs (overflow, no_image).
    server.shared = {"building": False, "status": None, "clean": None, "published": None, "wanted": threading.Event(),
                     "force": False, "rebuilt": -60, "proc": None, "clean_seq": 0, "bib": None, "biber": None, "idx": None,
                     "gen": 0, "retried": -60, "overflow": 0, "overflows": 0, "no_image": False, "missing": [], "deps": [],
                     "admit": threading.Lock(), "clean_lock": threading.Lock()}
    server.token = load_token()
    return server


def open_in_editor(pdf):
    """Open the PDF in a VS Code tab once it exists (the first build may still be running)."""
    code = editor_cli()
    if not code:
        return print("=== latex-live: not in a VS Code terminal and no LATEX_LIVE_EDITOR; skipping editor tab", flush=True)
    while not os.path.isfile(pdf):
        time.sleep(1)
    subprocess.run([code, "-r", pdf], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def take_lock(doc):
    """The lock on build/<doc>-live.lock (one instance per document), or (None, the holder's
    {port, pid, state}; no pid from older versions, which recorded only the port, and no state, its
    LATEX_LIVE_STATE, from those before 0.4). TeX can write in build/, so this is a hint to check
    (runs_here, instance_answers), not a fact."""
    lock = open(os.path.join(config.BUILD, f"{doc}-live.lock"), "a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.seek(0)
        text = lock.read().strip()
        lock.close()
        try:
            return None, json.loads(text) if text.startswith("{") else {"port": int(text)}
        except ValueError:
            return None, {}
    return lock, None


def record_port(server):
    """The viewer port and this process, in the lock file for later instances of the document,
    and in the list of running instances (instances)."""
    server.lock.truncate(0)
    server.lock.write(json.dumps({"port": server.server_address[1], "pid": os.getpid(), "state": config.STATE}))
    server.lock.flush()
    os.makedirs(config.INSTANCES, exist_ok=True)
    entry = {"port": server.server_address[1], "pid": os.getpid(), "root": server.paper_root, "doc": server.default_pdf[:-4],
             "was": getattr(server, "was", []),
             "id": paper_id(server.paper_root), "version": config.VERSION}
    with open(os.path.join(config.INSTANCES, f"{os.getpid()}.json"), "w") as f:
        json.dump(entry, f)


def is_live_instance(pid):
    """Whether pid is a running latex-live (not a reused pid)."""
    try:
        os.kill(pid, 0)
    except (OSError, TypeError):
        return False
    try:  # Linux: the exact argument list
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            argv = f.read().decode(errors="replace").split("\0")[:-1]
    except OSError:  # macOS: ps prints it joined, unquoted (a path may have spaces)
        command = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
        return bool(re.match(r"(?i)\S*python[0-9.]*\s+(-(?![cm]\b)\S+\s+)*(?!-)((?!\.py\s).)*?(^|/)live\.py(\s|$)", command))
    return is_live_argv(argv)


def is_live_argv(argv):
    """Whether argv is python running live.py as its script (not e.g. an editor on it, or python
    -c with live.py among its arguments)."""
    if not argv or not re.fullmatch(r"(?i)python[0-9.]*", os.path.basename(argv[0])):
        return False
    args = argv[1:]
    while args and args[0].startswith("-"):
        if args[0] in ("-c", "-m"):
            return False
        args = args[2:] if args[0] in ("-W", "-X") else args[1:]  # (options with a value)
    return bool(args) and os.path.basename(args[0]) == "live.py"


def runs_here(pid, root):
    """Whether pid is a latex-live instance (is_live_instance) working in root, a paper root (its
    working directory, as Linux's /proc tells; elsewhere, only its command line is checked). The lock
    file in build/ names the pid to stop, and the paper's TeX can write there: it must not make us
    stop another process."""
    if not os.path.isdir("/proc/self"):
        return is_live_instance(pid)
    try:
        cwd = os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return False
    return os.path.realpath(cwd) == os.path.realpath(root) and is_live_instance(pid)  # (its command line read after its folder)


def instances():
    """The running latex-live instances on this machine: [{port, pid, root, doc}], from
    INSTANCES (each removes its entry on exit; entries of crashed ones are dropped here), and
    from OLD_INSTANCES for instances of an older version."""
    found = []
    for path in glob.glob(os.path.join(config.INSTANCES, "*.json")) + glob.glob(os.path.join(config.OLD_INSTANCES, "*.json")):
        try:
            with open(path) as f:
                entry = json.load(f)
        except (OSError, ValueError):
            continue
        if is_live_instance(entry.get("pid")):
            found.append(entry)
        else:
            try:
                os.remove(path)
            except OSError:
                pass
    return sorted(found, key=lambda e: e["port"])


def papers_page(server, host):
    """/papers: every running preview on this machine, with links to their viewers: through the
    hub (one port for all) where an instance has a paper id, else on its own port."""
    hostname = urllib.parse.urlsplit(f"//{host}").hostname or "localhost"
    hostname = f"[{hostname}]" if ":" in hostname else hostname  # (IPv6)
    hub = hub_port()
    rows = []
    for e in instances():
        viewer = f"viewer.html?file={urllib.parse.quote(e['doc'])}.pdf"
        if not server and e.get("id"):  # (the hub's own page: same origin, whatever port it is forwarded to)
            url = f"{hub_path(e['id'], e['doc'])}{viewer}"
        elif hub and e.get("id"):
            url = f"//{hostname}:{hub}{hub_path(e['id'], e['doc'])}{viewer}"
        else:
            url = f"//{hostname}:{e['port']}/{viewer}"
        here = " (this one)" if server and e["pid"] == os.getpid() else ""
        rows.append(f"<tr><td title='{html.escape(e['root'])}'>{html.escape(os.path.basename(e['root']))}</td>"
                    f"<td><a href='{html.escape(url)}'>{html.escape(e['doc'])}.tex</a>{here}</td>"
                    f"<td>{html.escape(e.get('version', ''))}</td></tr>")
    return f"""<!doctype html><meta charset="utf-8"><title>latex-live: previews</title>
<style>body {{ font: 14px system-ui, sans-serif; background: #323639; color: #eee; margin: 2em; }} a {{ color: #9cf; }}
td, th {{ padding: 4px 14px 4px 0; text-align: left; }} th {{ opacity: 0.7; font-weight: normal; }}</style>
<h2>Running previews</h2>
<table><tr><th>Paper</th><th>Document</th><th>Version</th></tr>{"".join(rows)}</table>"""


# The hub: one port (--port, 44100 by default) for every preview on the machine. Each preview
# still runs in its own process, with its own latexmk, terminal and port (from --port + 1); the
# one holding HUB_LOCK also serves the hub, which forwards /p/<paper id>/<doc>/... to that
# preview. When it exits, another preview takes over the lock and the same port within a second
# (once the port is free; it waits while another program holds it),
# so viewer URLs keep working; the last one to exit closes it.
def paper_id(root):
    """A paper's id in hub URLs: its folder's name and a short hash of its path (and of the build
    directory, which a second instance of the same paper changes), stable across restarts."""
    real = os.path.realpath(root)
    key = real + ("" if config.BUILD == "build" else "\0" + config.BUILD)
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", os.path.basename(real)).strip("-") or "paper"
    return f"{slug}-{hashlib.sha1(key.encode()).hexdigest()[:6]}"


def hub_path(id_, doc):
    return f"/p/{urllib.parse.quote(id_)}/{urllib.parse.quote(doc)}/"


def read_hub():
    """HUB_FILE: {port, pid} of the preview serving the hub, if that process is alive; else {}."""
    try:
        with open(config.HUB_FILE) as f:
            hub = json.load(f)
    except (OSError, ValueError):
        return {}
    return hub if is_live_instance(hub.get("pid")) else {}


def hub_port():
    """The hub's port, if a live preview serves it."""
    return read_hub().get("port")


class HubHandler(Handler):
    """The hub's requests: /p/<id>/<doc>/<path> goes to <path> on that preview's port, with the
    token, so the viewer works there unchanged (its URLs are relative). /papers lists the
    previews; / and /viewer.html?file=X (old bookmarks) go to the only matching preview."""

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def route(self, method):
        if config.SHARED:  # (a shared instance never starts or joins the hub: main; this keeps it so)
            return self.send_error(500, "a shared instance has no hub")
        url = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(url.query)
        if not self.authorize(query if method == "GET" else {}):
            return self.send_error(403, f"open the URL with ?t=<token> from {config.TOKEN_FILE}")
        if m := re.match(r"^/p/([^/]+)/([^/]+)(/.*)?$", url.path):
            id_, doc = urllib.parse.unquote(m[1]), urllib.parse.unquote(m[2])
            if not m[3]:
                return self.redirect(hub_path(id_, doc))
            return self.forward(method, id_, doc, m[3] + (f"?{url.query}" if url.query else ""))
        if url.path == "/hub/ping":
            return self.send_json(200, {"pid": os.getpid()})
        if url.path == "/papers":
            return self.send_html(papers_page(None, self.headers.get("Host", "localhost")))
        if url.path in ("/", "/viewer.html"):
            want = (query.get("file", [""])[0])[:-4]
            found = [e for e in self.server.table() if not want or e["doc"] == want]
            if len(found) == 1:
                return self.redirect(f"{hub_path(found[0]['id'], found[0]['doc'])}viewer.html?file="
                                     f"{urllib.parse.quote(found[0]['doc'])}.pdf")
            return self.redirect("/papers")
        self.send_error(404)

    def redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def forward(self, method, id_, doc, path):
        """Send the request on to the preview of doc in paper id_, and its answer back. The
        preview checks that it is that paper (X-Latex-Live-Paper): a port that its preview gave
        up and another paper's took since the last look answers 421, and the hub looks again."""
        target_path = urllib.parse.unquote(urllib.parse.urlsplit(path).path)
        length, error = content_length(self.headers, POST_LIMITS.get(target_path, POST_LIMIT))  # (the previews' own limits)
        if error:
            return self.send_error(error)
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items() if k.lower() in ("content-type", "range", "if-range")}
        headers.update({"Cookie": f"latex_live={self.server.token}", "X-Latex-Live-Paper": id_})
        for fresh in (False, True):  # (fresh: after a 421 or a refused connection, look again now)
            target = self.server.find(id_, doc, fresh)
            if not target:
                return self.send_json(404, {"error": f"no preview of {doc}.tex in {id_} is running"})
            conn = http.client.HTTPConnection("127.0.0.1", target["port"], timeout=120)
            try:
                conn.request(method, path, body, headers)
                resp = conn.getresponse()
            except (OSError, http.client.HTTPException) as e:
                conn.close()
                # Try again only if the request can't have been acted on: a POST that reached a
                # preview (e.g. a note written, then the answer lost) must not be sent twice.
                if fresh or (method != "GET" and not isinstance(e, ConnectionRefusedError)):
                    return self.send_json(502, {"error": f"the preview of {doc}.tex is not answering ({e})"})
                continue
            if resp.status != 421:
                break
            conn.close()
        else:
            return self.send_json(502, {"error": f"no preview of {doc}.tex in {id_} answers"})
        self.send_response(resp.status)
        for key, value in resp.getheaders():
            if key.lower() in ("set-cookie", "connection", "transfer-encoding", "server", "date"):
                continue
            if key.lower() == "location" and value.startswith("/"):
                value = hub_path(id_, doc).rstrip("/") + value
            self.send_header(key, value)
        self.end_headers()
        try:
            shutil.copyfileobj(resp, self.wfile)
        except (OSError, http.client.HTTPException):  # (either side went away mid-answer)
            pass
        finally:
            conn.close()


class HubServer(DualStackServer):
    daemon_threads = True
    found, found_at = [], -9.0  # (table)

    def __init__(self, *args):
        super().__init__(*args)
        self.misses = {}  # (id, doc) -> when a lookup last missed (find)

    def table(self):
        """The running previews (instances), looked up at most every 2 s."""
        if time.monotonic() - self.found_at > 2:
            self.found, self.found_at = [e for e in instances() if e.get("id")], time.monotonic()
        return self.found

    def find(self, id_, doc, fresh=False):
        """The preview building doc in paper id_ (looked up again at once if fresh, or on a miss,
        e.g. a preview just started or switched to doc, unless that route missed in the last 0.5 s:
        a viewer of a stopped preview keeps asking); else one that switched away from doc (its
        viewers follow it, info.doc); else the paper's only one."""
        exact = lambda: next((e for e in self.table() if e["id"] == id_ and e["doc"] == doc), None)
        if fresh:
            self.found_at = -9.0
        found = exact()
        if not found and time.monotonic() - self.misses.get((id_, doc), -9) > 0.5:
            self.misses = {k: t for k, t in self.misses.items() if time.monotonic() - t < 60}
            self.misses[(id_, doc)] = time.monotonic()
            self.found_at = -9.0
            found = exact()
        if found:
            return found
        same = [e for e in self.found if e["id"] == id_]
        switched = [e for e in same if doc in e.get("was", [])]
        return switched[-1] if switched else same[0] if len(same) == 1 else None


def hub_watch(port):
    """Serve the hub on port once this process holds HUB_LOCK (a thread: it waits while another
    preview serves it). Only that port, so the hub's address never changes: while something else
    holds it, the previews' own ports still work, and the hub keeps trying."""
    os.makedirs(config.STATE, mode=0o700, exist_ok=True)
    lock = open(config.HUB_LOCK, "a+")
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            time.sleep(1)
    told = False
    while True:
        try:
            hub = HubServer(("::", port), HubHandler)
            break
        except OSError:
            if not told:
                print(f"=== latex-live: port {port} is taken, so the hub waits for it; meanwhile each viewer works on its own"
                      " preview's port (a preview from before 0.2 may hold it: restart it)", flush=True)
                told = True
            time.sleep(2)
    if told:
        print(f"=== latex-live: the hub is on port {port} now", flush=True)
    hub.token = load_token()
    with open(config.HUB_FILE, "w") as f:
        json.dump({"port": port, "pid": os.getpid()}, f)
    hub.serve_forever()


def answers(port, path):
    """Whether latex-live answers path on port (within a few seconds)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}?t={load_token()}", timeout=3) as r:
            return r.status == 200
    except (OSError, ValueError, http.client.HTTPException):
        return False


def instance_answers(port):
    """Whether an HTTP server answers on port within a few seconds, whatever it answers: the port of the
    instance that holds a document's lock, which it records in build/, where the paper's TeX can write.
    So the request carries no token (an instance with it refuses it, and still answers)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/docs", timeout=3):
            return True
    except urllib.error.HTTPError as e:
        e.close()
        return True
    except (OSError, ValueError, http.client.HTTPException):
        return False


def hub_lock_held():
    """Whether some process holds HUB_LOCK (HUB_FILE outlives its writer)."""
    try:
        with open(config.HUB_LOCK, "a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lock, fcntl.LOCK_UN)
            return False
    except BlockingIOError:
        return True
    except OSError:
        return False


def replace_stuck_hub():
    """Before starting: stop the preview serving the hub if it holds the hub's lock but has not
    answered for a while (three tries over about 10 s, so that a busy machine doesn't cost another
    paper its preview), as with a document's lock (take_lock), so the hub doesn't stay down."""
    hub = read_hub()
    if not hub or hub["pid"] == os.getpid() or not hub_lock_held():
        return
    for i in range(3):
        if answers(hub["port"], "/hub/ping"):
            return
        time.sleep(2)
    if read_hub() != hub or not hub_lock_held():  # (it changed hands, or went, meanwhile)
        return
    print(f"=== latex-live: stopping process {hub['pid']}, which serves the hub but is not answering", flush=True)
    stop_instance(hub["pid"])


def stop_instance(pid, root=None):
    """Stop another latex-live instance (it stops its latexmk); False if pid is not one (with root: one
    working in that paper root, runs_here), or won't stop."""
    if not (runs_here(pid, root) if root else is_live_instance(pid)):
        return False
    try:
        os.kill(pid, signal.SIGTERM)
        os.kill(pid, signal.SIGCONT)  # (a paused one handles SIGTERM only once it runs: then it stops its latexmk itself)
    except ProcessLookupError:  # (it exited meanwhile)
        pass
    for i in range(150):
        if not is_live_instance(pid):
            for folder in (config.INSTANCES, config.OLD_INSTANCES):  # (killed, it can't remove its entry itself)
                try:
                    os.remove(os.path.join(folder, f"{pid}.json"))
                except OSError:
                    pass
            return True
        if i == 100:
            os.kill(pid, signal.SIGKILL)
        time.sleep(0.1)
    return False


def set_doc(server, doc):
    """Serve doc as the document being built, and remember it as the last one (pick_doc)."""
    old = getattr(server, "default_pdf", "")[:-4]
    if old and old != doc:  # (its viewers' hub URLs still name it: HubServer.find)
        server.was = [d for d in getattr(server, "was", []) if d != doc] + [old]
    server.default_pdf = f"{doc}.pdf"
    server.bases, server.base_checked = {"head": {}, "push": {}}, {"head": 0, "push": 0}
    if server.diff.get("state") != "running":
        server.diff = {}
    with open(os.path.join(server.paper_root, config.BUILD, "latex-live-doc"), "w") as f:
        f.write(doc)


def start_latexmk(server, force=False):
    """latexmk -pvc for the current document. Main thread only: see request_switch.

    After a failed build, latexmk only reports the old failure until a source changes; -g (also
    with force) makes it build again, e.g. after a restart, or once latex_report.py has removed a
    damaged .aux."""
    if server.no_build or config.SHARED:  # (shared mode never runs latexmk: shared_build)
        return
    doc = server.default_pdf[:-4]
    force = force or (read_json(job_file(server.paper_root, server.default_pdf, ".status.json")) or {}).get("ok") is False
    env = {**server.env, "LATEX_LIVE_DOC": doc}
    if config.PUBLISH:
        env["LATEX_LIVE_PUBLISH"] = os.path.join(config.PUBLISH, server.default_pdf)  # latex_report.py
    # On Linux, PR_SET_PDEATHSIG: latexmk gets SIGTERM if this process dies, however it dies.
    # Elsewhere (macOS), main's signal handlers stop it.
    server.latexmk = subprocess.Popen(
        ["latexmk", "-r", os.path.join(config.HERE, "latexmkrc"), "-pvc", *(["-g"] if force else []), f"-jobname={doc}-live", doc],
        env=env,
        preexec_fn=(lambda: ctypes.CDLL("libc.so.6").prctl(1, signal.SIGTERM)) if sys.platform.startswith("linux") else None,
    )


def retry_build(server):
    """Rebuild from scratch when latex_report.py asks (build/<doc>-live.retry): the .aux was cut off,
    or came from another TeX installation. Removes the job's generated files (latexmk would rebuild
    its rules from an old .log or .fls), so latexmk starts as on a first run. At most once a minute, in case a source keeps causing it."""
    retry = job_file(server.paper_root, server.default_pdf, ".retry")
    if not os.path.exists(retry):
        return
    os.remove(retry)
    if time.monotonic() - server.retried < 60:
        print("=== latex-live: asked to rebuild again within a minute; not rebuilding by itself (save a file to retry)",
              flush=True)
        return
    server.retried = time.monotonic()
    stop_latexmk(server)
    sys.path.insert(0, config.HERE)
    import latex_report
    for ext in (*latex_report.AUX_EXTS, ".fdb_latexmk", ".fls", ".log", ".bbl", ".blg"):
        try:
            os.remove(job_file(server.paper_root, server.default_pdf, ext))
        except OSError:
            pass
    start_latexmk(server, force=True)


def stop_latexmk(server):
    proc, server.latexmk = server.latexmk, None
    if proc:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def on_main_thread(server, job):
    """Run job(server) -> (status, body) on the main thread and return its result. Jobs that
    (re)start latexmk must run there: its parent-death signal fires when the thread that started
    it exits, so a request thread must not start it."""
    done, result = threading.Event(), {}
    server.requests.put((job, result, done))
    if not done.wait(30):
        return 504, {"error": "this is taking a while; reload in a moment"}
    return result["status"], result["body"]


def request_switch(server, query):
    """/switch?doc=NAME: build another root document instead (switch_doc)."""
    doc = query.get("doc", [""])[0]
    if doc not in root_docs(server.paper_root):
        return 404, {"error": f"{doc}.tex is not a root document here"}
    return on_main_thread(server, lambda server: switch_doc(server, doc))


def switch_doc(server, doc):
    """Stop building the current document and build doc. Returns (status, body) for /switch."""
    if config.SHARED:  # (a shared instance builds one document, and never with latexmk)
        return 403, {"error": f"this preview shares {server.default_pdf} only"}
    if doc == server.default_pdf[:-4]:
        return 200, {"doc": doc}
    lock, other = take_lock(doc)
    if not lock:
        return 409, {"error": f"{doc}.tex is open in another latex-live instance (port {other.get('port', '?')})",
                     "port": other.get("port")}
    stop_latexmk(server)
    server.lock.close()
    server.lock = lock
    set_doc(server, doc)
    record_port(server)
    start_latexmk(server)
    print(f"=== latex-live: now building {doc}.tex", flush=True)
    return 200, {"doc": doc}


def rebuild_doc(server):
    """POST /rebuild: build the document again from the top (latexmk -g), e.g. after another build
    replaced its PDF, which then no longer matches the SyncTeX data. Returns (status, body)."""
    if server.no_build:
        return 409, {"error": "this instance only serves the viewer (--no-build)"}
    stop_latexmk(server)
    start_latexmk(server, force=True)
    print(f"=== latex-live: rebuilding {server.default_pdf[:-4]}.tex (from the viewer)", flush=True)
    return 202, {"doc": server.default_pdf[:-4]}


# Shared mode builds without latexmk (shared_build). latexmk is Perl that runs on the host, and it
# parses the files that the paper's TeX writes (.aux, .bcf, .log): it hands \bibdata names from the
# .aux to a shell command (its kpsewhich), so coauthors who write the paper could run commands on this
# machine through it. Instead, live.py watches the paper's files itself and runs pdflatex, bibtex,
# biber and makeindex as argument lists, each in a container of its own (tex-exec, confined, with the
# paper read-only and only build/ writable), and it treats what they write in build/ as written by
# someone else: the run's state, its status and what it published stay in memory (server.shared).
# The same layout line as latexmkrc's $pre_tex_code (check.py checks that they agree).
LAYOUT_TEX = (r"\def\latexlivelayout{\typeout{latex-live layout:"
              r" \the\paperwidth,\the\paperheight,\the\hoffset,\the\oddsidemargin,\the\voffset,\the\topmargin,"
              r"\the\headheight,\the\headsep,\the\textwidth,\the\textheight,\the\columnwidth,\the\baselineskip}}"
              r"\ifdefined\AddToHook\AddToHook{begindocument/end}{\latexlivelayout}\else\AtBeginDocument{\latexlivelayout}\fi")


BIBDATA_RE = re.compile(r"^\\(?:citation|bibdata|bibstyle)\{.*\}$", re.M)


def start_shared_build(server):
    """Shared mode: build the document now, and again whenever its files change (shared_build)."""
    os.environ["LATEX_LIVE_DOC"] = server.default_pdf[:-4]  # (latex_report.run_report names the main file after it)
    threading.Thread(target=shared_build, args=(server,), daemon=True).start()


def shared_inputs(server):
    """The files a build of the shared document depends on: those the browser editor opens (the main
    file, what the last build read, the .bib files), the paper's other files the last good build and the
    last attempt read (figures, styles), the bibliographies and styles the .aux or .bcf name (at any depth:
    shared_deps), and the files a build missed (shared_missing), as {real path: their stat (mtime, size,
    inode), or None while there is none}."""
    root = server.paper_root
    paths = set(editable_files(server).values()) | set(server.shared["missing"]) | set(server.shared["deps"])
    for fls in (fls_file(root, server.default_pdf), job_file(root, server.default_pdf, ".fls")):
        paths |= {os.path.realpath(os.path.join(root, rel)) for rel in project_inputs(root, fls)}
    found = {}
    for path in paths:
        try:
            st = os.stat(path)
            found[path] = (st.st_mtime_ns, st.st_size, st.st_ino)
        except OSError:
            found[path] = None
    return found


MISSING_RE = re.compile(r"File `([^`'\n]{1,200})' not found")
MISSING_EXTS = ("", ".tex", ".sty", ".cls", ".bib", ".pdf", ".png", ".jpg", ".jpeg", ".eps")  # (as TeX and graphicx add them)


def paper_file(root, name, ext=""):
    """The real path of a file of the paper's that a build names (name, plus ext if it has no extension),
    or None if that is not one (in_paper: the paper's TeX writes these names)."""
    name = name.strip()
    if not name or "\0" in name:
        return None
    path = os.path.normpath(os.path.join(root, name + ("" if not ext or os.path.splitext(name)[1] else ext)))
    return os.path.realpath(path) if in_paper(root, path) else None


def shared_missing(server, log):
    """The paper's files that a build could not find, for shared_inputs to watch for them to appear: those
    its log names (LaTeX's "File `x' not found", with the extensions TeX and graphicx would try), and the
    \\input and \\include targets of the paper's .tex files that don't exist yet (an \\include of a missing
    file is no error)."""
    root, found = server.paper_root, []
    for name in dict.fromkeys(MISSING_RE.findall(log[-(1 << 20):])):
        found += [path for ext in (MISSING_EXTS if not os.path.splitext(name)[1] else ("",))
                  if (path := paper_file(root, name + ext))]
    texts = set(project_tex_files(root, fls_file(root, server.default_pdf))) | source_tree(root, server.default_pdf[:-4])
    for path in list(texts)[:500]:
        found += [target for m in INPUT_RE.finditer(read_tex(path)) if (target := paper_file(root, m.group(1), ".tex"))]
    return [path for path in dict.fromkeys(found) if not os.path.exists(path)][:400]


def shared_deps(server, auxes, bcf):
    """The bibliography files and styles that a build's .aux files (auxes: aux_tree's) and .bcf name, as real
    paths in the paper (those that exist and those that don't: TeX Live's own styles are elsewhere), for
    shared_inputs to watch: pdflatex's file list has none of them, and they can be in any folder."""
    root, found = server.paper_root, []
    for names in re.findall(r"^\\bibdata\{(.*)\}$", auxes, re.M)[:100]:
        found += [path for name in names.split(",")[:100] if (path := paper_file(root, name, ".bib"))]
    for name in re.findall(r"^\\bibstyle\{(.*)\}$", auxes, re.M)[:10]:
        found += [path] if (path := paper_file(root, name, ".bst")) else []
    for name in re.findall(r"<bcf:datasource[^>]*>([^<]+)</bcf:datasource>", bcf)[:100]:
        found += [path] if (path := paper_file(root, html.unescape(name))) else []
    return list(dict.fromkeys(found))[:300]


QUIET, MIN_GAP, MOST_WAIT = 1.0, 2.0, 10.0  # (s: shared_build's debounce, and the least time between two runs' starts)


def shared_build(server):
    """Shared mode's builder, a thread of its own: a run (shared_run) at the start, then whenever one of
    the files it depends on changes, goes or appears (shared_inputs, looked at twice a second; a change
    during a run makes another), or POST /rebuild asks for one (shared_rebuild_request), or the TeX Live
    image appears after a run found it missing. Never more than one run at a time, and for everyone
    alike: a change starts one once the files have been quiet for QUIET s (at most MOST_WAIT s after the
    first change), and no run starts within MIN_GAP s of the last one's start, so that saves in a row
    make one build, and no one can keep the machine building."""
    built, last_start, image_checked = None, -math.inf, time.monotonic()
    while not server.stopping:
        asked = server.shared["wanted"].wait(0.5)
        if not asked and server.shared["no_image"] and time.monotonic() - image_checked > 5:
            image_checked = time.monotonic()
            asked = image_pulled()
        now = shared_inputs(server)
        if not asked and now == built:  # (a file gone, or new, counts as a change too)
            continue
        if not asked:  # (a change: wait until the files are quiet)
            first = time.monotonic()
            while not server.stopping and time.monotonic() - first < MOST_WAIT:
                time.sleep(QUIET)
                again = shared_inputs(server)
                if again == now or server.shared["wanted"].is_set():
                    break
                now = again
        if (wait := last_start + MIN_GAP - time.monotonic()) > 0:
            time.sleep(wait)
        if server.stopping:
            break
        now = shared_inputs(server)
        server.shared["wanted"].clear()
        server.shared["force"] = False
        last_start = time.monotonic()
        try:
            shared_run(server)
        except Exception:  # (said, and the next change tries again)
            print(f"=== latex-live: the build failed:\n{traceback.format_exc().rstrip()}", flush=True)
            server.shared.update(building=False, status={"ok": False, "time": time.strftime("%H:%M:%S"),
                                                         "errors": [{"file": None, "line": None, "msg": "the build failed (see the server's log)"}]})
        # The files the run depends on, as they were when it started (files it read for the first time: as they are now).
        built = {path: now.get(path, stat) for path, stat in shared_inputs(server).items()}


def shared_tool(server, argv, kind="main", cwd=None, env=None):
    """Run a TeX tool (argv) in its own container (tex-exec, from the paper root or cwd), for up to 10 minutes,
    in the live build's slot (LIVE_SPAWN; another kind of run's: a SPAWNS slot); whether it succeeded. The
    container is labelled with a run of its own (e.g. main-12) and its kind, so that a rebuild stops the
    main run's (stop_containers); when the tool fails or runs out of time, its container is stopped before
    the slot is free again."""
    label = f"{kind}-{next(RUN_SEQ)}"
    with LIVE_SPAWN if kind == "main" else SPAWNS:
        proc = subprocess.Popen([*container_argv(server.paper_root), *argv], cwd=cwd or server.paper_root,
                                env={**server.env, **(env or {}), "LATEX_LIVE_RUN": label, "LATEX_LIVE_TIMEOUT": "600"},
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if kind == "main":
            server.shared["proc"] = proc
        try:
            proc.wait(timeout=600)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        finally:
            if proc.returncode != 0:
                stop_containers(label)
            if kind == "main":
                server.shared["proc"] = None
        return proc.returncode == 0


def build_size(server, most=200_000):
    """The disk space build/ takes (bytes), or None past most files and folders (too many to add up)."""
    total, seen = 0, 0
    for folder, dirs, names in os.walk(os.path.join(server.paper_root, config.BUILD)):
        seen += len(dirs) + len(names)
        if seen > most:
            return None
        for name in names:
            try:
                total += os.lstat(os.path.join(folder, name)).st_blocks * 512
            except OSError:
                pass
    return total


def own_lock(server, path):
    """Whether path is the lock file this instance holds (the same file as the one it has open)."""
    try:
        return os.path.samestat(os.lstat(path), os.fstat(server.lock.fileno()))
    except (OSError, AttributeError, ValueError):
        return False


def watch_build_size(server):
    """Shared mode, from the start (with --no-build too: the downloads, baselines and SyncTeX write there):
    the paper's TeX decides what it writes into build/ (--ulimit fsize bounds each file, not how many), so
    every second this adds up build/, and past LATEX_LIVE_BUILD_LIMIT (or 200 000 entries) it stops every
    container of this instance and empties build/ but for the lock file the instance holds (and a small
    latex-live-doc), which the next build fills again; the run it stopped says why (shared_run), and is tried again a few times, further
    apart each time."""
    root = os.path.join(server.paper_root, config.BUILD)
    while not server.stopping:
        size = build_size(server)
        if size is None or size > config.BUILD_LIMIT:
            server.shared["overflow"] = time.time_ns()
            stop_containers()
            print(f"=== latex-live: {config.BUILD}/ took more than {config.BUILD_LIMIT >> 20} MB (LATEX_LIVE_BUILD_LIMIT); stopped the"
                  " build and emptied it", flush=True)
            for name in os.listdir(root):
                path = os.path.join(root, name)
                if own_lock(server, path) or name == "latex-live-doc" and os.path.isfile(path) and not os.path.islink(path) \
                        and os.path.getsize(path) < 4096:
                    continue
                if os.path.isdir(path) and not os.path.islink(path):
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
        time.sleep(1)


def dep_stats(paths):
    """The stat (mtime, size) of each of paths that exists, to tell whether a tool needs to run again."""
    stats = []
    for path in paths:
        try:
            st = os.stat(path)
            stats.append((path, st.st_mtime_ns, st.st_size))
        except OSError:
            stats.append((path, None))
    return stats


def bib_state(server, aux):
    """What a bibtex run depends on: the citation, bibliography and style lines of the .aux files (aux:
    aux_tree's), and the .bib and .bst files they name in the paper (shared_deps), to tell whether bibtex
    needs to run again."""
    return hashlib.sha256(repr((BIBDATA_RE.findall(aux), dep_stats(shared_deps(server, aux, "")))).encode()).hexdigest()


def biber_state(server, bcf):
    """What a biber run depends on: the control file (.bcf: the citations, the options) and the data sources
    it names in the paper (shared_deps), to tell whether biber needs to run again."""
    return hashlib.sha256(repr((bcf, dep_stats(shared_deps(server, "", bcf)))).encode()).hexdigest()


def tool_failure(name, path):
    """What a failed bibtex, biber or makeindex says in the build's status: its name and the last lines of
    its log (path: .blg or .ilg), which coauthors can't open otherwise (at most 10 lines, 2000 characters)."""
    tail = "\n".join((read_capped(path, 1 << 20) or "").rstrip().splitlines()[-10:])[-2000:]
    return f"{name} failed" + (f"; the end of its log:\n{tail}" if tail else " (it wrote no log)")


def shared_run(server):
    """One build of the shared document (shared_build), as latexmk would make it: pdflatex in passes, with
    bibtex, biber or makeindex between them where the paper uses them, until the references settle (at
    most six passes). Each successful pass publishes the PDF (shared_publish) and keeps its list of files
    (build/<job>.fls.done); the run's status, from the log (latex_report.run_report, with the paper's own
    files only, and what bibtex, biber or makeindex said when they failed), stays in memory; and a
    successful run starts the notes-free pass (shared_notes_free). A rebuild stops a run part way (its
    containers: stop_containers) and starts it again; a tool's state is kept only when it succeeded, and
    only if no rebuild (server.shared["gen"]) came meanwhile, so that it runs again until it does."""
    sys.path.insert(0, config.HERE)
    import latex_report
    root, pdf = server.paper_root, server.default_pdf
    doc, job = pdf[:-4], f"{pdf[:-4]}-live"
    out = lambda ext: os.path.join(config.BUILD, job + ext)  # (relative to the paper root, as the tools see it)
    server.shared["building"] = True
    began, gen = time.time_ns(), server.shared["gen"]  # (in its status: the run reflects the files as they were then)
    failed = {}  # (bibtex, biber or makeindex: why it failed, in this run)
    try:
        stop_containers(kind="main")  # (one that a rebuild's stop missed: its client had not started it yet)
        server.shared["no_image"] = not image_pulled()
        if server.shared["no_image"]:
            raise RuntimeError(f"{config.IMAGE_MISSING}; until then nothing builds (never the host's TeX here)")
        tex = ["pdflatex", "-recorder", "-synctex=1", "-file-line-error", "-halt-on-error", "-interaction=batchmode",
               f"-output-directory={config.BUILD}", f"-jobname={job}", f"{LAYOUT_TEX}\\input{{{doc}}}"]
        ok = False

        def tool(name, state_key, state, argv, log_ext, **where):
            done = shared_tool(server, argv, kind=name, **where)
            failed[name] = None if done else tool_failure(name, out(log_ext))
            if done and server.shared["gen"] == gen:  # (failed: it runs again on the next pass and the next run)
                server.shared[state_key] = state
            return done

        for passes in range(1, 7):
            before = read_build(out(".aux"))
            ok = shared_tool(server, tex)
            if server.shared["force"]:  # (a rebuild stopped it: shared_build starts again)
                return
            if not ok:
                break
            latex_report.keep_inputs(job)
            shared_publish(server, job)
            aux, log, bcf = read_build(out(".aux")), read_build(out(".log")), read_build(out(".bcf"))
            auxes, ran = aux_tree(aux), False
            server.shared["deps"] = shared_deps(server, auxes, bcf)
            if "\\bibdata" in auxes:
                if (state := bib_state(server, auxes)) != server.shared["bib"]:
                    paths = f"{os.path.realpath(root)}{os.pathsep}"  # (bibtex runs in build/: the paper's .bib and .bst files)
                    ran = tool("bibtex", "bib", state, ["bibtex", job], ".blg", cwd=os.path.join(root, config.BUILD),
                               env={"BIBINPUTS": paths, "BSTINPUTS": paths})
            elif bcf:
                if (state := biber_state(server, bcf)) != server.shared["biber"] or re.search(r"Please \(?re\)?run Biber", log):
                    ran = tool("biber", "biber", state, ["biber", f"--output-directory={config.BUILD}", job], ".blg")
            idx = read_build(out(".idx"))
            if idx and (state := hashlib.sha256(idx.encode()).hexdigest()) != server.shared["idx"]:
                ran = tool("makeindex", "idx", state, ["makeindex", "-o", out(".ind"), out(".idx")], ".ilg") or ran
            if server.shared["force"]:
                return
            if not ran and not RERUN_RE.search(log) and aux == before:
                break
        if server.shared["overflow"] > began:  # (watch_build_size stopped it: tried again a few times, further apart)
            server.shared["overflows"] += 1
            if server.shared["overflows"] <= 3:
                retry = threading.Timer(5 * 2 ** server.shared["overflows"], server.shared["wanted"].set)
                retry.daemon = True
                retry.start()
            raise RuntimeError(f"the build wrote more than {config.BUILD_LIMIT >> 20} MB into {config.BUILD}/ (LATEX_LIVE_BUILD_LIMIT), so it"
                               f" was stopped and {config.BUILD}/ emptied")
        files = [os.path.relpath(p, root) for p in project_tex_files(root, fls_file(root, pdf))]
        ok, status, log, _ = latex_report.run_report(job, "ok" if ok else "fail", files)
        missing = shared_missing(server, log) + server.shared["missing"]  # (and those an earlier run missed, until they appear)
        server.shared["missing"] = [path for path in dict.fromkeys(missing) if not os.path.exists(path)][:400]
        if tools := [msg for msg in failed.values() if msg]:  # (a stale bibliography or index, with what the tool said)
            ok, status = False, {**status, "ok": False,
                                 "errors": [*status.get("errors", []), *({"file": None, "line": None, "msg": msg} for msg in tools)]}
        if ok:
            server.shared["overflows"] = 0
        if not ok and latex_report.damaged_aux(log, job) and time.monotonic() - server.shared["retried"] > 60 \
                and server.shared["gen"] == gen:
            server.shared["retried"] = time.monotonic()  # (a run stopped part way cut off an .aux: once more without them)
            for ext in latex_report.AUX_EXTS:
                try:
                    os.remove(out(ext))
                except OSError:
                    pass
            server.shared["wanted"].set()
        server.shared["status"] = {**status, "began": began}
        stamp = status["time"]
        print(f"=== live-reload: published {pdf} at {stamp}" if ok else f"=== live-reload: BUILD FAILED at {stamp}; kept the previous {pdf}",
              flush=True)
        if ok and latex_report.has_notes(files):
            shared_notes_free(server, job, doc)
    except RuntimeError as e:
        server.shared["status"] = {"ok": False, "time": time.strftime("%H:%M:%S"), "began": began,
                                   "errors": [{"file": None, "line": None, "msg": str(e)}]}
    finally:
        server.shared["building"] = False


def shared_publish(server, job):
    """Publish build/<job>.pdf as the shared document's PDF, in one step, and remember what was published
    (status's "overwritten" compares the PDF with it). Never through a symbolic link: build/ is written in
    the containers."""
    dest = pdf_path(server.paper_root, server.default_pdf)
    try:
        fd = os.open(os.path.join(server.paper_root, config.BUILD, f"{job}.pdf"), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return
    tmp = os.path.join(os.path.dirname(dest), f".{os.path.basename(dest)}.tmp")
    with open(fd, "rb") as src, open(tmp, "wb") as f:
        shutil.copyfileobj(src, f)
    os.replace(tmp, dest)
    st = os.stat(dest)
    server.shared["published"] = {"mtime": st.st_mtime_ns, "size": st.st_size}


def shared_notes_free(server, job, doc):
    """The notes-free pass after a successful run (latex_report.notes_free_pass), in a thread, in a SPAWNS
    slot and a container labelled "clean", in a folder of its own (build/clean-<n>/): a newer run's pass
    stops an older one's, and only the newest publishes, under clean_lock (its PDF to build/, its SyncTeX
    and .aux to build/clean/, where the viewer's lookups read them)."""
    sys.path.insert(0, config.HERE)
    import latex_report
    stop_containers(kind="clean")
    with server.shared["clean_lock"]:
        server.shared["clean_seq"] += 1
        seq = server.shared["clean_seq"]
    out = os.path.join(config.BUILD, f"clean-{seq}")
    for old in glob.glob(os.path.join(glob.escape(config.BUILD), "clean-*")):
        shutil.rmtree(old, ignore_errors=True)
    latex_report.seed_notes_free(job, doc, out)
    clean = os.path.join(config.BUILD, "clean")

    def publish(pdf):
        with server.shared["clean_lock"]:
            if seq != server.shared["clean_seq"]:  # (a newer run's pass came meanwhile)
                return False
            os.makedirs(clean, exist_ok=True)
            for ext in (".synctex.gz", ".aux", ".bbl", ".log"):
                if os.path.lexists(src := os.path.join(out, f"{doc}-clean{ext}")):
                    os.replace(src, os.path.join(clean, f"{doc}-clean{ext}"))
            os.replace(pdf, os.path.join(config.BUILD, f"{doc}-clean.pdf"))
            return True

    def run():
        with SPAWNS:  # (one of the slots that downloads share: the live build has its own)
            if seq != server.shared["clean_seq"]:
                return
            try:
                status = latex_report.notes_free_pass(job, doc, env={**server.env, "LATEX_LIVE_RUN": f"clean-{seq}"}, out=out,
                                                      publish=publish)
            except OSError as e:  # (its folder emptied under it: a newer pass's, or watch_build_size)
                status = {"ok": False, "time": time.strftime("%H:%M:%S"),
                          "errors": [{"file": None, "line": None, "msg": f"its files went missing ({e.strerror})"}]}
            if status.get("timeout"):
                stop_containers(f"clean-{seq}")
        with server.shared["clean_lock"]:
            if seq == server.shared["clean_seq"] and not status.get("superseded"):
                server.shared["clean"] = status
        shutil.rmtree(out, ignore_errors=True)

    threading.Thread(target=run, daemon=True).start()


def shared_rebuild_request(server, user):
    """POST /rebuild in shared mode: stop the run in progress (its containers too) and build again, with
    bibtex, biber and makeindex too (a new generation: what a run in progress learns about them is not
    kept). At most once every 20 s for the coauthors together (429; the owner, any time; for everyone,
    shared_build starts runs at least MIN_GAP s apart), so that they can't keep the machine busy."""
    with server.shared["admit"]:  # (two requests at once: one rebuild, and the other is refused)
        if user["role"] != "owner":
            if time.monotonic() - server.shared["rebuilt"] < 20:
                return 429, {"error": "it was just rebuilt; try again in a few seconds"}
            server.shared["rebuilt"] = time.monotonic()
        server.shared["gen"] += 1
        server.shared.update(force=True, bib=None, biber=None, idx=None)
        if proc := server.shared["proc"]:
            proc.terminate()
        stop_containers(kind="main")
        server.shared["wanted"].set()
    print(f"=== latex-live: rebuilding {server.default_pdf[:-4]}.tex (asked by {user['name'] or user['id']})", flush=True)
    return 202, {"doc": server.default_pdf[:-4]}


def baseline_request(server, kind):
    """POST /baseline {kind: "head" or "push"} in shared mode: build that baseline for the change
    highlights, once per commit (base_status), where status, a GET, only reports it."""
    if kind not in ("head", "push"):
        return 400, {"error": "kind is head or push"}
    return 200, base_status(server, server.default_pdf, kind) or {}


def print_urls(port, path="/"):
    token = load_token()
    print(f"=== latex-live: viewer (on a remote machine, forward port {port} first): http://localhost:{port}{path}?t={token}", flush=True)
    if (host := os.environ.get("LATEX_LIVE_HOST")) and not config.SHARED:  # (a shared instance answers only on this machine)
        print(f"=== latex-live: viewer, direct: http://{host}:{port}{path}?t={token}", flush=True)


def print_shared_urls(port):
    """Shared mode: the link for coauthors, with no token in it (the proxy names them; a personal link
    adds ?t= with the user's own token), then the owner's own on this machine, also without its token:
    a shared instance usually runs as a service, whose output goes to a log that others may read."""
    where = f"{config.PUBLIC_ORIGIN}/" if config.PUBLIC_ORIGIN else "the proxy's address (LATEX_LIVE_PUBLIC_URL would print it here)"
    print(f"=== latex-live: shared with the users in {config.USERS_FILE}: {where} (a personal link adds ?t=<their token>)", flush=True)
    print(f"=== latex-live: on this machine: http://localhost:{port}/ (add your token the first time: it is in {config.TOKEN_FILE})",
          flush=True)


def check_shared(args):
    """Before a shared instance starts: stop on a setting it can't run with (SHARED_PROBLEMS, the users
    file, host TeX, the owner's own state folder, the identity header without the proxy's uid, or off
    Linux, where that can't be checked), warn about weak ones, confine every TeX run from here on
    (tex-exec reads these variables; every run passes the environment on), and drop git's variables
    from the environment, which could point the commits elsewhere or sign them as someone else."""
    if config.SHARED_PROBLEMS:
        sys.exit("\n".join(f"latex-live: {problem}" for problem in config.SHARED_PROBLEMS))
    try:
        load_users(config.USERS_FILE)
    except (OSError, ValueError) as e:
        sys.exit(f"latex-live: the users file {config.USERS_FILE}: {e}")
    users()  # (warns if others can read it)
    if args.host_tex or not shutil.which("podman"):
        sys.exit("latex-live: a shared instance builds only in the TeX Live image (coauthors write the sources), "
                 + ("so not with --host-tex" if args.host_tex else "with rootless podman, which is not installed here"))
    if os.path.realpath(config.STATE) == os.path.realpath(config.DEFAULT_STATE):
        sys.exit(f"latex-live: a shared instance needs a state folder of its own (LATEX_LIVE_STATE), not {config.DEFAULT_STATE}: there"
                 " it would share your previews' token and list itself among them")
    if config.IDENTITY_HEADER and config.PROXY_UID is None:
        sys.exit(f"latex-live: with LATEX_LIVE_IDENTITY_HEADER, set LATEX_LIVE_PROXY_UID to the uid the proxy runs as: any"
                 f" program on this machine can send {config.IDENTITY_HEADER} and act as any user in the users file")
    if config.IDENTITY_HEADER and not os.path.exists("/proc/net/tcp"):
        sys.exit(f"latex-live: LATEX_LIVE_IDENTITY_HEADER needs Linux (its /proc/net/tcp tells which uid sent a request), so that"
                 f" only the proxy can send {config.IDENTITY_HEADER}; elsewhere, coauthors use the token links")
    if config.IDENTITY_HEADER and not config.PUBLIC_ORIGIN:
        print("=== latex-live: without LATEX_LIVE_PUBLIC_URL, coauthors who come through the proxy can read but not comment"
              " or edit (their browser's Origin can't be checked)", flush=True)
    os.environ.update(LATEX_LIVE_REQUIRE_IMAGE="1", LATEX_LIVE_SANDBOX="1", LATEX_LIVE_INSTANCE=config.INSTANCE)
    for key in [key for key in os.environ if key.startswith("GIT_")]:  # (GIT_DIR, GIT_INDEX_FILE, GIT_AUTHOR_NAME, ...:
        del os.environ[key]                                            # the commits go to the paper's own checkout, as theirs)


def print_hub_urls(doc, fallback_port, wait=0):
    """The doc's viewer URLs through the hub (waiting up to wait seconds for one to serve it),
    else on fallback_port."""
    end = time.monotonic() + wait
    while not (hub := hub_port()) and time.monotonic() < end:
        time.sleep(0.2)
    if hub:
        print_urls(hub, hub_path(paper_id(os.getcwd()), doc))
    else:
        print_urls(fallback_port)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("doc", nargs="?", help="root .tex file (with or without extension); default: the one whose"
                    " sources were edited last. The viewer switches between root files. A path (e.g. the editor's"
                    " current file) runs in that file's paper: it builds the file if it is a root one.")
    ap.add_argument("--version", action="version", version=f"latex-live {config.VERSION}")
    ap.add_argument("--port", type=int, help="the hub's port, which serves every preview (default:"
                    " $LATEX_LIVE_PORT, else the number in ~/.config/latex-live/port, else 44100); each preview also has"
                    " its own, from the next one. A shared instance (LATEX_LIVE_USERS) serves exactly this port, without"
                    " the hub, and needs it")
    ap.add_argument("--page-limit", type=int, help="main-text page limit to check in the viewer (default: the venue's,"
                    " for ICLR, NeurIPS, COLM, ICML, CVPR and ICCV styles)")
    ap.add_argument("--build-dir", default="build", help="build directory, relative to the paper root (default: build);"
                    " any other also receives <doc>.pdf, e.g. to run beside the usual instance")
    ap.add_argument("--no-build", action="store_true", help="viewer only; don't run latexmk")
    ap.add_argument("--no-open", action="store_true", help="don't open the PDF in a VS Code editor tab")
    ap.add_argument("--host-tex", action="store_true", help="use the host's TeX installation, not the TeX Live 2024 image")
    ap.add_argument("--replace", action="store_true", help="stop another instance building the same document and run here"
                    " (one that no longer answers is replaced anyway)")
    args = ap.parse_args()
    if args.port is None:
        if config.SHARED:  # (it would otherwise take the hub's port, which the owner's previews use)
            sys.exit("latex-live: a shared instance (LATEX_LIVE_USERS) needs --port: the port its proxy connects to")
        args.port = config.default_port()
    config.BUILD = os.environ["LATEX_LIVE_BUILD"] = os.path.normpath(args.build_dir)
    config.PUBLISH = "" if config.BUILD == "build" else config.BUILD
    if config.SHARED:
        check_shared(args)

    if args.doc and os.path.dirname(args.doc):  # a path: run in its paper
        path = os.path.abspath(args.doc)
        root = paper_root_of(path)
        if not root:
            sys.exit(f"latex-live: no root .tex file in or above {os.path.dirname(path)}")
        os.chdir(root)
        name = os.path.basename(path)
        args.doc = name if os.path.dirname(path) == root and name[:-4] in root_docs(root) else None

    docs = root_docs(os.getcwd())
    if args.doc:
        doc = args.doc[:-4] if args.doc.endswith(".tex") else args.doc
        if not os.path.isfile(f"{doc}.tex"):
            sys.exit(f"latex-live: {doc}.tex not found in {os.getcwd()}")
    elif docs:
        doc = pick_doc(os.getcwd(), docs)
    else:
        sys.exit(f"latex-live: no .tex file with a \\documentclass in {os.getcwd()}")

    # One instance per document; the lock file records the viewer port and process for later instances.
    os.makedirs(config.BUILD, exist_ok=True)
    lock, other = take_lock(doc)
    if not lock:
        port, pid, state = other.get("port"), other.get("pid"), other.get("state")
        holder = f"process {pid}, port {port}" if pid else f"port {port}"
        if port and instance_answers(port) and not args.replace:
            print(f"=== latex-live: already running for {doc}.tex ({holder}; another terminal or window)", flush=True)
            listed = any(e["pid"] == pid and e.get("id") for e in instances())  # (the port in build/ is a hint: take_lock)
            if isinstance(state, str) and os.path.realpath(state) != os.path.realpath(config.STATE):  # (its token is not ours)
                where = state if state.isprintable() else "another folder"
                print(f"=== latex-live: it keeps its state in {where} (LATEX_LIVE_STATE), so its link is not printed here", flush=True)
            elif config.SHARED:
                print_shared_urls(port)
            elif listed:
                print_hub_urls(doc, port)
            else:  # (not in this user's list of instances: its port is not trusted with the token)
                print(f"=== latex-live: it is not in {config.INSTANCES}, so its link is not printed here", flush=True)
            print("=== latex-live: --replace stops that one and runs here", flush=True)
            return
        if not pid:
            sys.exit(f"latex-live: {doc}.tex is held by an instance that is not answering ({holder}); stop it and retry")
        if not runs_here(pid, os.getcwd()):
            sys.exit(f"latex-live: {doc}.tex is locked, but the process its lock file names ({pid}) is not latex-live working in"
                     f" {os.getcwd()}, so it is not stopped; stop the instance for {doc}.tex yourself and retry")
        print(f"=== latex-live: stopping the instance for {doc}.tex ({holder})"
              + ("" if args.replace else ", which is not answering"), flush=True)
        if not stop_instance(pid, os.getcwd()):
            sys.exit(f"latex-live: could not stop process {pid}; stop it and retry")
        for _ in range(50):  # (an exiting instance stops looking live a moment before it lets go of the lock)
            lock, other = take_lock(doc)
            if lock:
                break
            time.sleep(0.1)
        else:
            sys.exit(f"latex-live: {doc}.tex is still locked; retry")
    if not config.SHARED:  # (a shared instance takes no part in the hub)
        replace_stuck_hub()
    server = serve(os.getcwd(), f"{doc}.pdf", args.port if config.SHARED else args.port + 1, args.page_limit)  # (the hub has --port)
    code_updated()  # (what this process runs)
    server.lock = lock
    set_doc(server, doc)
    record_port(server)
    if config.SHARED:  # (what the last run left uncommitted is committed before the first request: start_commits)
        start_commits(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print_shared_urls(server.server_address[1])
    else:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        threading.Thread(target=hub_watch, args=(args.port,), daemon=True).start()
        print_hub_urls(doc, server.server_address[1], wait=3)
        if os.environ.get("LATEX_LIVE_COMMIT", "").strip() == "1":
            print("=== latex-live: LATEX_LIVE_COMMIT works in shared mode only (LATEX_LIVE_USERS), so nothing is committed",
                  flush=True)
    others = [d for d in docs if d != doc]
    print(f"=== latex-live {config.VERSION}: building {doc}.tex" + (f"; the viewer switches to {', '.join(others)}" if others else ""),
          flush=True)
    if not args.no_open:
        threading.Thread(target=open_in_editor, args=(pdf_path(os.getcwd(), f"{doc}.pdf"),), daemon=True).start()

    server.no_build = args.no_build
    server.env = {**os.environ, "LATEX_LIVE_HOME": config.HERE, "max_print_line": "10000"}
    if config.SHARED:  # (the image even with --no-build: the baselines and downloads build too, never with the host's TeX)
        server.env["LATEX_LIVE_EXEC"] = os.environ["LATEX_LIVE_EXEC"] = server.tex_exec
        threading.Thread(target=watch_build_size, args=(server,), daemon=True).start()
        if not args.no_build:
            print("=== latex-live: TeX Live 2024 (podman), confined, each run in a container of its own" if image_pulled()
                  else f"=== latex-live: {config.IMAGE_MISSING}; until then nothing builds (never the host's TeX here)", flush=True)
            start_shared_build(server)
    elif args.no_build:
        pass
    elif args.host_tex or not shutil.which("podman"):
        print("=== latex-live: TeX from the host" + ("" if args.host_tex else " (no podman)")
              + ("" if shutil.which("pdflatex") else "; but there is no pdflatex: install TeX Live (MacTeX on macOS)"),
              flush=True)
    else:
        server.env["LATEX_LIVE_EXEC"] = server.tex_exec = container_exec(os.path.realpath(os.getcwd()))
        print("=== latex-live: TeX Live 2024 (podman)" if image_pulled()
              else f"=== latex-live: {config.IMAGE_MISSING}; until then, the host's TeX runs", flush=True)
    if not config.SHARED:
        start_latexmk(server)

    # Only signal latexmk here; the main loop reaps it.
    def stop(*_):
        server.stopping = True
        if server.latexmk:
            server.latexmk.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGHUP, stop)  # VS Code closing the task terminal
    try:  # until stopped, or latexmk exits by itself; meanwhile, switch documents and rebuild for the viewer
        while not server.stopping and not (server.latexmk and server.latexmk.poll() is not None):
            if not config.SHARED:
                retry_build(server)
            try:
                job, result, done = server.requests.get(timeout=1)
            except queue.Empty:
                continue
            try:
                result["status"], result["body"] = job(server)
            except Exception as e:
                result["status"], result["body"] = 500, {"error": f"failed: {e}"}
            done.set()
    except KeyboardInterrupt:
        pass
    finally:  # (also after an error: the pending edits are committed as theirs now, rather than in someone else's commit)
        server.stopping = True
        stop_latexmk(server)
        stop_containers()
        commit_at_exit(server)
        stop_containers()  # (one a build started while the first sweep ran)
        try:
            os.remove(os.path.join(config.INSTANCES, f"{os.getpid()}.json"))
        except OSError:
            pass


if __name__ == "__main__":
    main()
