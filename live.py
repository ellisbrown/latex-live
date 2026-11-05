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
import collections
import contextlib
import fcntl
import html
import http.client
import http.cookies
import http.server
import json
import os
import queue
import re
import shutil
import signal
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))  # (latexlive/ next to this file, wherever it is run from)
from latexlive import config  # noqa: E402
from latexlive.auth import (  # noqa: E402
    LOOPBACK_ORIGIN_RE, USER_ID_RE, host_kinds, is_loopback, load_token, load_users, owner_name, owner_principal,
    peer_uid, principal, same_secret, users)
from latexlive.baselines import baseline_request, start_diff  # noqa: E402
from latexlive.build import (  # noqa: E402
    on_main_thread, rebuild_doc, request_switch, retry_build, set_doc, start_latexmk, stop_latexmk)
from latexlive.checks import cached_checks  # noqa: E402
from latexlive.commits import commit_at_exit, commit_request, start_commits  # noqa: E402
from latexlive.document import floats, graphics, toc_outline  # noqa: E402
from latexlive.editor import file_request, files_listing, save_request, source_status  # noqa: E402
from latexlive.export import start_export  # noqa: E402
from latexlive.files import open_output, pdf_path  # noqa: E402
from latexlive.git import commits  # noqa: E402
from latexlive.instances import (  # noqa: E402
    instance_answers, instances, is_live_instance, paper_id, record_port, runs_here, stop_instance, take_lock)
from latexlive.latex import paper_root_of, pick_doc, root_docs  # noqa: E402
from latexlive.navigation import editor_cli, forward_request, open_in_editor, open_source, synctex_edit, synctex_lookup  # noqa: E402
from latexlive.note_writer import note_request  # noqa: E402
from latexlive.notes import margin_notes, note_authors, notes, paper_note_kinds, shown_notes  # noqa: E402
from latexlive.review import start_review  # noqa: E402
from latexlive.roles import READ_PATHS, allowed, at_least, least_role, shared_pdfs, viewer_status  # noqa: E402
from latexlive.shared_build import shared_rebuild_request, start_shared_build  # noqa: E402
from latexlive.shared_watch import watch_build_size  # noqa: E402
from latexlive.status import code_updated, status  # noqa: E402
from latexlive.tools import container_exec, image_pulled, stop_containers  # noqa: E402


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
