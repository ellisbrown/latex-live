"""GET /status/: everything the viewer polls for, and whether latex-live changed on disk since
this process started; and who has the paper open (POST /presence, and the list in an editor's status)."""

import hashlib
import os
import re
import secrets
import time

from . import config
from .auth import owner_name
from .baselines import base_status
from .build import build_running, build_status
from .document import layout, page_limit, uncounted_sections
from .files import file_hash, job_file, mtime, pdf_path, read_json
from .sources import editable_files
from .synctex import PENDING, refs_start
from .tools import image_problem


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


def with_refs(info, refs):
    """info with where the main text ends (refs), or refsPending while that is being looked up (refs_start
    without wait: the viewer shows a new PDF at once, and measures its main text once refs come)."""
    return {**info, "refs": None, "refsPending": True} if refs is PENDING else {**info, "refs": refs}


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
        "uncounted": uncounted_sections(server, pdf_name),
        "layout": layout(root, pdf_name),
        "doc": server.default_pdf,
    }
    info = with_refs(info, refs_start(root, pdf_name, wait=False))  # where the main text ends (page badge, space hints)
    info["cleanBuild"] = build_status(server, pdf_name, clean=True)  # (the notes-free pass)
    published = server.shared["published"] if config.SHARED else read_json(job_file(root, pdf_name, ".published.json"))  # (what was published last)
    if published and os.path.isfile(pdf):
        st = os.stat(pdf)
        if [st.st_mtime_ns, st.st_size] != [published.get("mtime"), published.get("size")]:
            info["overwritten"] = time.strftime("%H:%M:%S", time.localtime(st.st_mtime))
    clean_pdf = pdf_path(root, pdf_name[:-4] + "-clean.pdf")
    if os.path.isfile(clean_pdf):
        info["clean"] = with_refs({"version": str(mtime(clean_pdf))}, refs_start(root, pdf_name, clean=True, wait=False))
    info["head"] = base_status(server, pdf_name, "head", build=not config.SHARED)
    info["push"] = base_status(server, pdf_name, "push", build=push and not config.SHARED)
    info["texImage"] = image_problem(server)
    info["latexLive"] = {"version": config.VERSION, "updated": code_updated()}
    if config.TIMING:  # (the viewer logs how long each new PDF took to reach the screen: this clock tells it when it was published)
        info["timing"] = {"now": time.time()}
    return info


# Who has the paper open: each editor's (or the owner's) page says so with POST /presence when its file or line
# changes, and every few seconds besides; a page that stops saying so drops out after PRESENCE_TTL seconds. Each
# user has at most PRESENCE_TABS pages listed, and the list at most PRESENCE_LIMIT, so that no one crowds out the others.
PRESENCE_TTL, PRESENCE_TABS, PRESENCE_LIMIT, PRESENCE_LINE = 30, 4, 256, 10_000_000
PAGE_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
PRESENCE_SALT = secrets.token_bytes(16)  # (a new one each run: the keys mean nothing outside it)


def presence_key(*parts):
    """An opaque name for a user, or one of their pages, that tells them apart from the others without
    saying who they are (their id stays with the server)."""
    return hashlib.sha256(PRESENCE_SALT + "\0".join(parts).encode()).hexdigest()[:12]


def presence_request(server, req, user):
    """POST /presence: {tab, open, line} from a page (tab: its own name), the file open in its source pane
    (only one of the files the editor opens: editable_files; else none) and the cursor's line in it; or
    {tab, leave: true} as the page closes. Answers with the page's key in the list (tab), or 429 once the
    user has PRESENCE_TABS pages listed (or the list is full) until one drops out."""
    tab = req.get("tab")
    if not isinstance(tab, str) or not PAGE_ID_RE.fullmatch(tab):
        return 400, {"error": "expected tab: the page's name"}
    uid, now = user["id"] or "owner", time.monotonic()
    key = (uid, tab)
    if req.get("leave") is True:
        with server.presence_lock:
            server.presence.pop(key, None)
        return 200, {}
    shown = req.get("open")
    shown = shown if isinstance(shown, str) and shown in editable_files(server, fresh=False) else None
    line = req.get("line")
    line = min(max(line, 1), PRESENCE_LINE) if shown and type(line) is int else None
    name = (user["name"] or owner_name(server))[:80]  # (as the users file gives it; the owner's, as coauthors see it)
    with server.presence_lock:
        table = server.presence
        for gone in [k for k, entry in table.items() if now - entry["seen"] > PRESENCE_TTL]:
            del table[gone]
        if key not in table and sum(k[0] == uid for k in table) >= PRESENCE_TABS:
            return 429, {"error": f"at most {PRESENCE_TABS} pages of each user are listed"}
        if key not in table and len(table) >= PRESENCE_LIMIT:
            return 429, {"error": f"{PRESENCE_LIMIT} pages are listed already, the most there can be"}
        table[key] = {"uid": uid, "user": presence_key(uid), "tab": presence_key(uid, tab), "name": name, "file": shown,
                      "line": line, "seen": now}
        return 200, {"tab": table[key]["tab"]}


def presence(server, user):
    """Who has the paper open, for an editor's or the owner's status: per page listed in the last
    PRESENCE_TTL seconds, the user's name and key (not their id or role), the page's key, the file open
    in its source pane and the line, and whether it is the asker's (you). It only reads: a page that
    dropped out leaves the table on the next POST /presence."""
    now = time.monotonic()
    with server.presence_lock:
        listed = [entry for entry in server.presence.values() if now - entry["seen"] <= PRESENCE_TTL]
    return sorted(({**{k: entry[k] for k in ("user", "tab", "name", "file", "line")}, "you": entry["uid"] == (user["id"] or "owner")}
                   for entry in listed), key=lambda p: (p["name"], p["user"], p["tab"]))


def server_files():
    """The files of latex-live this process runs: live.py, VERSION, the modules in latexlive/, and latex_report.py,
    which they import too (the downloads, the baselines, the checks and shared mode's builder)."""
    package = os.path.join(config.HERE, "latexlive")
    return [os.path.join(config.HERE, name) for name in ("live.py", "VERSION", "latex_report.py")] + sorted(
        os.path.join(package, name) for name in os.listdir(package) if name.endswith(".py"))


def code_hash(path):
    """file_hash, or None for a file gone (a module a git pull removed)."""
    try:
        return file_hash(path)
    except OSError:
        return None


def code_updated(started={}):
    """Whether one of server_files on disk differs from what this process runs (e.g. after a git pull, or a
    release that only bumps VERSION), also a module added or removed: the viewer then asks for a restart.
    (forward.py, and latex_report.py as latexmk's hooks, run anew each time.)"""
    paths = server_files()
    stamp = [(path, mtime(path)) for path in paths]
    if "hash" not in started:
        started.update(hash=[(path, code_hash(path)) for path in paths], stamp=stamp, updated=False)
    elif stamp != started["stamp"]:
        started.update(stamp=stamp, updated=[(path, code_hash(path)) for path in paths] != started["hash"])
    return started["updated"]
