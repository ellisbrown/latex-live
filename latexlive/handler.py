"""An instance's requests: the viewer and its files, the JSON endpoints and the actions, each as
the asker's role allows (Handler)."""

import contextlib
import json
import os
import re
import urllib.parse

from . import config
from .auth import owner_name
from .baselines import baseline_request, start_diff
from .build import on_main_thread, rebuild_doc, request_switch
from .checks import cached_checks
from .commits import commit_request
from .document import floats, graphics, toc_outline
from .editor import completions, file_request, files_listing, save_request, source_status
from .export import start_export
from .files import pdf_path
from .git import commits
from .hub import papers_page
from .instances import instances
from .latex import root_docs
from .navigation import editor_cli, forward_request, open_source, synctex_edit, synctex_lookup
from .note_writer import note_request
from .notes import margin_notes, note_authors, notes, paper_note_kinds, shown_notes
from .review import start_review
from .roles import READ_PATHS, STATIC_DIRS, allowed, at_least, least_role, shared_pdfs, viewer_status
from .shared_build import shared_rebuild_request
from .status import status
from .web import BaseHandler, POST_LIMIT, POST_LIMITS, content_length, json_query


def me(server, user):
    """GET me: who the viewer's user is and what they may do, so the viewer shows only that (without
    shared mode: the owner, who may do everything). macroDefined: whether the paper defines the user's
    note macro, without which a coauthor can't comment; openHost, in shared mode: whether the owner's
    editor opens here (a service has none); download: whether they may download the builds made on
    request, the paper without its notes (export) and with its notes in the margin (review), which
    take the same role; signOut: whether the browser signed in with a token (POST logout forgets it),
    not through the proxy."""
    defined = bool(user["macro"]) and user["macro"] in paper_note_kinds(server.paper_root, server.default_pdf)
    can = lambda path: allowed(user, "POST", path)
    return {"id": user["id"], "name": user["name"] or owner_name(server), "macro": user["macro"], "role": user["role"],
            "shared": config.SHARED, "owner": owner_name(server), "macroDefined": defined,
            "can": {"comment": can("/note") and (user["role"] == "owner" or defined), "edit": can("/file"),
                    "rebuild": can("/rebuild"), "compare": can("/diff"), "switch": can("/switch"),
                    "openHost": can("/open") and (not config.SHARED or editor_cli() is not None),
                    "download": can("/export") and can("/review"), "checks": allowed(user, "GET", "/check/"),
                    "signOut": user["via"] != "proxy"}}


STATIC = {"/viewer.html"}  # plus the files in the folders of STATIC_DIRS (static_file)
ACTIONS = ("/open", "/diff", "/switch", "/export", "/review")  # (POST only: a GET of one is answered 405)


def static_file(path):
    """Whether path (unquoted) is one of the viewer's own files, which SimpleHTTPRequestHandler sends from
    the checkout: viewer.html (STATIC), or a file in pdfjs/, codemirror/ or viewer/ (STATIC_DIRS), never
    through .. or a link out of its folder."""
    if path in STATIC:
        return True
    if not path.startswith(STATIC_DIRS) or ".." in path or path.endswith("/") or "\0" in path:
        return False
    folder = os.path.realpath(os.path.join(config.HERE, path.split("/")[1]))
    return os.path.realpath(os.path.join(config.HERE, path.lstrip("/"))).startswith(os.path.join(folder, ""))


class Handler(BaseHandler):
    """An instance's requests: who is asking (authorize), whether their role allows the request
    (permitted), then the answer: the viewer and its files and the JSON endpoints (GET), and the actions
    and the builds made on request (POST)."""

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

    def misdirected(self):
        """A request the hub meant for another paper (HubHandler.forward): answered 421."""
        want = self.headers.get("X-Latex-Live-Paper")
        if want and want != self.server.paper_id:
            self.send_error(421, f"this preview is {self.server.paper_id}, not {want}")
            return True
        return False

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(url.path)
        query = urllib.parse.parse_qs(url.query)
        self.principal = self.authorize(query)
        if not self.principal:
            return self.deny()
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
            doc_pdf = re.sub(r"(-head|-push)?-(clean|diff|head|push|export|review|annotated)\.pdf$", ".pdf", name)  # the document a derived PDF belongs to
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
        elif path == "/completions":
            self.send_json(200, completions(self.server))
        elif path == "/file":
            self.send_json(*file_request(self.server, query))
        elif path in ACTIONS:  # (only as POST, which checks the Origin; a viewer from before says this)
            self.send_json(405, {"error": "this preview takes that as a POST now: reload the page"})
        elif path == "/forward":
            self.send_json(*forward_request(self.server, query, self.principal))
        elif path == "/commits":
            self.send_json(200, commits(self.server.paper_root))
        elif path == "/instances":
            self.send_json(200, {"self": os.getpid(), "instances": instances()})
        elif path == "/papers":
            self.send_html(papers_page(self.server, self.headers.get("Host", "localhost")))
        elif path == "/docs":  # (a coauthor sees only the shared document)
            docs = root_docs(self.server.paper_root) if self.principal["role"] == "owner" else [self.server.default_pdf[:-4]]
            self.send_json(200, {"doc": self.server.default_pdf[:-4], "docs": docs})
        elif path == "/favicon.ico":  # (no icon; nothing to refuse either: browsers ask on every page)
            self.send_response(204)
            self.end_headers()
        elif static_file(path):
            super().do_GET()
        else:
            self.send_error(404)

    def do_POST(self):
        """Actions (POST /note, /file, /rebuild, /switch, /diff, /open, /commit, /logout), and the builds made on
        request (POST /export, /review, and in shared mode /baseline). Only with the session cookie, which
        SameSite=Strict keeps off other sites' requests, a JSON body, which other sites can't send without asking
        first, and from the viewer's own page (origin_allowed), as the role allows; both are checked before the body
        is read (a refusal reads a small body still, so that the answer isn't lost to a reset; a large one, not)."""
        path = urllib.parse.urlsplit(self.path).path
        self.principal = self.authorize({})
        if not self.principal:
            return self.deny()
        if self.misdirected():
            return
        length, error = content_length(self.headers, POST_LIMITS.get(path, POST_LIMIT))
        if error:
            return self.send_error(error)

        def drain():
            if length <= 1 << 16:
                with contextlib.suppress(OSError):
                    self.rfile.read(length)

        if not self.origin_allowed():
            drain()
            return self.send_error(403, "not from this preview's own page (Origin)")
        if not self.permitted("POST", path, {}, before=drain):
            return
        try:
            data = self.rfile.read(length)
        except OSError:  # (the client stopped sending: the timeout)
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
        elif path == "/logout":
            self.logout()
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
            self.send_text(*open_source(self.server.paper_root, json_query(req, "file", "line", "column")))
        elif path == "/export":
            self.send_json(*start_export(self.server))
        elif path == "/review":
            self.send_json(*start_review(self.server))
        else:
            self.send_error(404)
