"""GET /status/: everything the viewer polls for, and whether latex-live changed on disk since
this process started."""

import os
import time

from . import config
from .baselines import base_status
from .build import build_running, build_status
from .document import layout, page_limit
from .files import file_hash, job_file, mtime, pdf_path, read_json
from .synctex import refs_start
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
    if config.TIMING:  # (the viewer logs how long each new PDF took to reach the screen: this clock tells it when it was published)
        info["timing"] = {"now": time.time()}
    return info


def server_files():
    """The files of latex-live this process runs: live.py, VERSION and the modules in latexlive/."""
    package = os.path.join(config.HERE, "latexlive")
    return [os.path.join(config.HERE, name) for name in ("live.py", "VERSION")] + sorted(
        os.path.join(package, name) for name in os.listdir(package) if name.endswith(".py"))


def code_hash(path):
    """file_hash, or None for a file gone (a module a git pull removed)."""
    try:
        return file_hash(path)
    except OSError:
        return None


def code_updated(started={}):
    """Whether live.py, VERSION or a module in latexlive/ on disk differs from what this process runs
    (e.g. after a git pull, or a release that only bumps VERSION), also a module added or removed: the
    viewer then asks for a restart. latex_report.py and forward.py run anew each time."""
    paths = server_files()
    stamp = [(path, mtime(path)) for path in paths]
    if "hash" not in started:
        started.update(hash=[(path, code_hash(path)) for path in paths], stamp=stamp, updated=False)
    elif stamp != started["stamp"]:
        started.update(stamp=stamp, updated=[(path, code_hash(path)) for path in paths] != started["hash"])
    return started["updated"]
