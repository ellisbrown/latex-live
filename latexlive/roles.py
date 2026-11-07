"""What each role may do and see in shared mode: the least role per request (least_role), the PDFs
a coauthor may ask for, and the status a viewer or commenter gets."""

from . import config
from .auth import ROLES


def least_role(method, path):
    """The least role that may make a request in shared mode, or None for what no one may there (switch:
    a shared preview serves one document); whatever is not listed is the owner's (open, instances,
    papers, commit). Downloading the paper without its notes (export) or with its notes in the margin
    (review) is reading it: each builds at most once per build of the document (start_export,
    start_review), and the baselines that change highlights compare with (editors) once per commit
    (base_status); each starts only with a POST, whose Origin says it comes from the viewer's own page
    (a GET of the downloads is answered 405, so that an old viewer says why). Anyone signed in may sign
    out (logout). The checks quote the sources' comments and the text in their false branches, and the
    baselines show earlier versions of the text, which only editors see (shared_pdfs, viewer_status)."""
    if method == "GET" and path.startswith("/check/"):  # (the checks quote the sources' comments and hidden text)
        return "editor"
    if method == "GET" and path.startswith(READ_PATHS + STATIC_DIRS):
        return "viewer"
    if method == "GET":
        return {"/": "viewer", "/me": "viewer", "/docs": "viewer", "/forward": "viewer", "/synctex": "viewer",
                "/viewer.html": "viewer", "/favicon.ico": "viewer", "/export": "viewer", "/review": "viewer", "/commits": "editor",
                "/files": "editor", "/file": "editor"}.get(path, "owner")
    if path == "/switch":
        return None
    return {"/note": "commenter", "/rebuild": "editor", "/diff": "editor", "/file": "editor", "/export": "viewer",
            "/review": "viewer", "/baseline": "editor", "/logout": "viewer"}.get(path, "owner")


def at_least(user, role):
    """Whether user (a principal) has role or one that may do more (ROLES)."""
    return ROLES.index(user["role"]) >= ROLES.index(role)


def allowed(user, method, path):
    """Whether user (a principal) may make a request: as least_role says in shared mode; without it,
    everyone is the owner, who may do everything."""
    if not config.SHARED:
        return True
    need = least_role(method, path)
    return need is not None and at_least(user, need)


def shared_pdfs(server, user):
    """The PDFs a non-owner may ask for: the shared document's and the downloads made from it (export,
    review, and the review's copy with the notes as comments, annotated); an editor, also its baselines and diff (head, push, diff), which show earlier versions of
    the text: viewers and commenters see the current PDF and what it shows (see viewer_status)."""
    doc = server.default_pdf[:-4]
    return {f"{doc}{suffix}.pdf" for suffix in ("", "-clean", "-export", "-review", "-annotated",
                                                *(("-diff", "-head", "-head-clean", "-push", "-push-clean")
                                                  if at_least(user, "editor") else ()))}


def viewer_status(info):
    """GET /status/ for a viewer or commenter: what the viewer needs to show the current PDF (its
    version, the build in progress, the layout and page limit, the notes-free PDF), and of the builds,
    whether they failed, when, and how many errors (the messages quote the sources, and name files that
    the PDF may not show); no baselines."""
    def build(b):
        if not isinstance(b, dict):
            return b
        n = len(b.get("errors") or [])
        return {"ok": b.get("ok"), "time": b.get("time"), "began": b.get("began"),
                "errors": [{"file": None, "line": None, "msg": f"{n} error{'' if n == 1 else 's'} (editors see them)"}] if n else []}
    keep = ("version", "building", "forward", "pageLimit", "refs", "refsPending", "layout", "doc", "overwritten", "clean", "texImage", "latexLive", "timing")
    return {**{key: info[key] for key in keep if key in info}, "build": build(info.get("build")),
            "cleanBuild": build(info.get("cleanBuild"))}


READ_PATHS = ("/pdf/", "/status/", "/outline/", "/notes/", "/check/", "/graphics/", "/margin/", "/floats/", "/authors/")
STATIC_DIRS = ("/pdfjs/", "/codemirror/", "/viewer/")  # (the folders of the viewer's own files: handler.static_file)
