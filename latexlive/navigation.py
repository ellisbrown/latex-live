"""Between the PDF and the sources, for the viewer: forward search for its lists (/forward),
inverse search (/synctex), and opening a source line in the owner's editor (/open)."""

import glob
import os
import shutil
import subprocess
import threading
import time

from . import config
from .files import mtime
from .notes import margin_notes, notes, shown_notes
from .roles import at_least
from .sources import editable_files
from .synctex import forward_target, inverse_search, synctex_input
from .tools import synctex_unavailable


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


def open_source(paper_root, query):
    """Open a project file at a line in VS Code (build-error links in the viewer)."""
    try:
        src, line = os.path.realpath(os.path.join(paper_root, query["file"][0])), int(query["line"][0])
    except (KeyError, ValueError):
        return 400, "expected file, line"
    if not src.startswith(os.path.realpath(paper_root) + os.sep) or not os.path.isfile(src):
        return 404, "not a file in the paper"
    return open_in_vscode(src, line, paper_root)


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


def open_in_editor(pdf):
    """Open the PDF in a VS Code tab once it exists (the first build may still be running)."""
    code = editor_cli()
    if not code:
        return print("=== latex-live: not in a VS Code terminal and no LATEX_LIVE_EDITOR; skipping editor tab", flush=True)
    while not os.path.isfile(pdf):
        time.sleep(1)
    subprocess.run([code, "-r", pdf], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
