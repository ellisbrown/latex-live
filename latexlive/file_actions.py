"""The Files tab's actions and the History tab's restore: GET /asset sends a figure, POST /file/create makes a source
file, POST /file/upload uploads a figure, POST /file/rename and /file/delete rename and remove a source or figure,
and POST /history/restore puts a source back as a commit left it; each as its user, within the daily budget of a
coauthor's writes (commits.source_budget), through sources (never over a file that is there, nor into build/), and
remembered for its commit (remember_write)."""

import base64
import binascii
import os
import threading
import time

from . import config
from .commits import charge, commit_others, paused, remember_write, source_budget, unpaid, updating
from .editor import editor_text, remember_version, user_asset
from .git import commit_sha, file_at_commit
from .sources import (
    ADDED_LIMIT, ASSET_EXTS, ASSET_SIZE, ASSETS_LIMIT, EDITABLE_EXTS, EDITABLE_SIZE, NOTE_LOCK, SourceChanged, add_file,
    create_source, editable_files, file_version, invalidate_assets, move_file, new_path, open_source, oversized_source,
    project_assets, remember_added, remove_file, write_source, wrote)


# The Files tab's figures, as GET /asset sends them (an image's own type; the handler adds a CSP that runs nothing in
# one opened alone, e.g. an SVG's scripts).
ASSET_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
               ".svg": "image/svg+xml", ".pdf": "application/pdf", ".eps": "application/postscript"}


def asset_request(server, query, user):
    """GET /asset?path=<rel>: (status, content type, bytes) of a figure that user may see (user_asset), as open_source
    reads it; 404 for any other path."""
    rel = os.path.normpath(query.get("path", [""])[0] or ".")
    if not (real := user_asset(server, user, rel)):
        return 404, None, b""
    try:
        with open_source(real, limit=ASSET_SIZE) as f:
            data = f.read(ASSET_SIZE + 1)
    except (OSError, SourceChanged):
        return 404, None, b""
    if len(data) > ASSET_SIZE:  # (grown since it was checked)
        return 404, None, b""
    return 200, ASSET_TYPES.get(os.path.splitext(rel)[1].lower(), "application/octet-stream"), data


# Uploads: at most ASSET_SIZE bytes each, the paper's figures (project_assets) at most UPLOAD_TOTAL bytes and
# ASSETS_LIMIT files, and in shared mode a coauthor's uploads at most UPLOAD_RATE bytes a day (UPLOAD_WINDOW), so that
# neither the copy nor its git history (LATEX_LIVE_COMMIT: an upload, committed, removed, uploaded again…) grows
# without end. UPLOAD_SLOTS: uploads read at once (each body is held in memory: web.POST_LIMITS).
UPLOAD_TOTAL, UPLOAD_RATE, UPLOAD_WINDOW = 250_000_000, 100_000_000, 86400  # (bytes, bytes, seconds)
UPLOAD_SLOTS = threading.BoundedSemaphore(2)

def files_changed(server, user, add=(), drop=()):
    """After a Files tab action for user (holding NOTE_LOCK): the files added are listed from now on, those dropped no
    longer (remember_added); the lists of sources and figures are made again; and each path is committed as user's
    (remember_write, kind "files": added to git, or removed)."""
    remember_added(server, add, drop)
    unpaid(server, user, [*drop, *(add if drop else ())])  # (a rename's new name too: source_budget)
    server.editable = None
    invalidate_assets(server)
    for rel in (*drop, *add):
        remember_write(server, user, rel, "files")
    for rel in drop:
        server.last_writer.pop(rel, None)
    for rel in add:
        if rel.lower().endswith(EDITABLE_EXTS):  # (who saved a source, should a save conflict: save_file's "by")
            wrote(server, user, rel)


def create_request(server, req, user):
    """POST /file/create {path, text?}: make a new source file (create_source: a .tex, .bib, .sty or .cls file, never
    over one that is there: 409) holding text, as user, within source_budget (413, 429). 200 {"path"}."""
    rel, text = req.get("path"), req.get("text", "")
    if not isinstance(rel, str) or not isinstance(text, str):
        return 400, {"error": "expected path (and maybe text)"}
    with NOTE_LOCK:
        if paused(server):
            return updating()
        if len(server.added) >= ADDED_LIMIT:
            return 409, {"error": f"{ADDED_LIMIT} files were added here already: remove some first"}
        try:
            norm = new_path(server.paper_root, rel, EDITABLE_EXTS)
            refusal, cost = source_budget(server, user, norm, b"", text.encode("utf-8"), adding=True)
            if refusal:
                return refusal
            if not commit_others(server, user, norm):  # (a removal of this name, someone else's, not committed yet)
                return updating()
            create_source(server.paper_root, norm, text)
            charge(server, user, cost)
        except ValueError as e:
            return 400, {"error": f"{rel.strip()}: {e}"}
        except FileExistsError:
            return 409, {"error": f"{norm} is there already"}
        except SourceChanged:
            return 409, {"error": f"a folder on the way to {norm} changed; try again"}
        except UnicodeEncodeError:
            return 400, {"error": "the text is not valid Unicode"}
        except OSError as e:
            return 500, {"error": f"could not make {norm}: {e.strerror or e}"}
        files_changed(server, user, add=[norm])
    return 200, {"path": norm}


def upload_request(server, req, user):
    """POST /file/upload {path, data (base64)}: upload a figure (add_file: one of ASSET_EXTS, never over a file that is
    there: 409), as user, within the limits above (413, 429). Sources are made empty (create_request) and written by the
    editor's save, never uploaded. 200 {"path", "size"}."""
    rel, data = req.get("path"), req.get("data")
    if not isinstance(rel, str) or not isinstance(data, str):
        return 400, {"error": "expected path and data (base64)"}
    if rel.strip().lower().endswith(EDITABLE_EXTS):
        return 400, {"error": "a source file is made with New file and written in the editor, not uploaded"}
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        return 400, {"error": "data is not base64"}
    if not raw:
        return 400, {"error": "the file is empty"}
    if len(raw) > ASSET_SIZE:
        return 413, {"error": f"a figure uploads up to {ASSET_SIZE // 1_000_000} MB"}
    uid, now = user["id"], time.monotonic()
    with NOTE_LOCK:
        if paused(server):
            return updating()
        try:
            norm = new_path(server.paper_root, rel, ASSET_EXTS)
        except ValueError as e:
            return 400, {"error": f"{rel.strip()}: {e}"}
        invalidate_assets(server)
        figures = project_assets(server)
        if len(figures) >= ASSETS_LIMIT:
            return 413, {"error": f"the paper has {ASSETS_LIMIT} figures, the most the Files tab lists: remove some first"}
        if sum(a["size"] for a in figures) + len(raw) > UPLOAD_TOTAL:
            return 413, {"error": f"the paper's figures would pass {UPLOAD_TOTAL // 1_000_000} MB: remove some first"}
        uploads = server.uploads.setdefault(uid, [])
        uploads[:] = [(t, n) for t, n in uploads if now - t < UPLOAD_WINDOW]
        if config.SHARED and user["role"] != "owner" and sum(n for _, n in uploads) + len(raw) > UPLOAD_RATE:
            return 429, {"error": f"you uploaded {UPLOAD_RATE // 1_000_000} MB today, the most a coauthor uploads in a day"}
        if len(server.added) >= ADDED_LIMIT:
            return 409, {"error": f"{ADDED_LIMIT} files were added here already: remove some first"}
        if not commit_others(server, user, norm):
            return updating()
        try:
            add_file(server.paper_root, norm, raw)
        except FileExistsError:
            return 409, {"error": f"{norm} is there already: pick another name, or remove it first"}
        except SourceChanged:
            return 409, {"error": f"a folder on the way to {norm} changed; try again"}
        except (OSError, ValueError) as e:
            return 500, {"error": f"could not save {norm}: {getattr(e, 'strerror', None) or e}"}
        uploads.append((now, len(raw)))
        files_changed(server, user, add=[norm])
    return 200, {"path": norm, "size": len(raw)}


def managed(server, user, rel):
    """rel, normalized, if user's Files tab may rename or remove it: a source the editor opens (editable_files) or a
    figure they may see (user_asset), but the document's main file; else None."""
    norm = os.path.normpath(rel) if isinstance(rel, str) and rel.strip() and "\0" not in rel else None
    if not norm or norm == f"{server.default_pdf[:-4]}.tex":
        return None
    return norm if norm in editable_files(server) or user_asset(server, user, norm) else None


def rename_request(server, req, user):
    """POST /file/rename {from, to}: rename a source or figure (managed; move_file: a source stays a source and a figure
    a figure, never over a file that is there: 409), as user. 200 {"from", "path"}."""
    old, new = req.get("from"), req.get("to")
    if not isinstance(old, str) or not isinstance(new, str):
        return 400, {"error": "expected from and to"}
    with NOTE_LOCK:
        if paused(server):
            return updating()
        if not (norm := managed(server, user, old)):
            return 404, {"error": f"{old}: not a file that can be renamed here (the main file can't)"}
        exts = EDITABLE_EXTS if norm.lower().endswith(EDITABLE_EXTS) else ASSET_EXTS
        if new.strip().lower().endswith((*EDITABLE_EXTS, *ASSET_EXTS)) and not new.strip().lower().endswith(exts):
            return 400, {"error": "a source stays a source, and a figure a figure"}
        try:
            target = new_path(server.paper_root, new, exts)
        except ValueError as e:
            return 400, {"error": f"{new.strip()}: {e}"}
        if target == norm:
            return 400, {"error": f"{norm} has that name already"}
        if not (commit_others(server, user, norm) and commit_others(server, user, target)):
            return updating()
        try:
            move_file(server.paper_root, norm, target)
        except FileNotFoundError:
            return 404, {"error": f"{norm} is not there"}
        except FileExistsError:
            return 409, {"error": f"{target} is there already"}
        except SourceChanged:
            return 409, {"error": f"{norm} or a folder on the way changed; try again"}
        except (OSError, ValueError) as e:
            return 500, {"error": f"could not rename {norm}: {getattr(e, 'strerror', None) or e}"}
        files_changed(server, user, add=[target], drop=[norm])
    return 200, {"from": norm, "path": target}


def delete_request(server, req, user):
    """POST /file/delete {path}: remove a source or figure (managed; remove_file), as user. 200 {"path"}."""
    rel = req.get("path")
    if not isinstance(rel, str):
        return 400, {"error": "expected path"}
    with NOTE_LOCK:
        if paused(server):
            return updating()
        if not (norm := managed(server, user, rel)):
            return 404, {"error": f"{rel}: not a file that can be removed here (the main file can't)"}
        if not commit_others(server, user, norm):
            return updating()
        try:
            remove_file(server.paper_root, norm)
        except FileNotFoundError:
            return 404, {"error": f"{norm} is not there"}
        except SourceChanged:
            return 409, {"error": f"{norm} changed; try again"}
        except OSError as e:
            return 500, {"error": f"could not remove {norm}: {e.strerror or e}"}
        files_changed(server, user, drop=[norm])
    return 200, {"path": norm}


OVERSIZED_LIMIT = 16 * EDITABLE_SIZE  # (the most of a source past EDITABLE_SIZE that a restore reads to write over it)


def restore_request(server, req, user):
    """POST /history/restore {rev, path, version?}: put a source back as commit rev (a hexadecimal id; for a coauthor, one
    that HEAD builds on) left it, as user: written over the file the editor opens, or a source of the document that grew
    past what the editor opens (oversized_source; only if it is still at version, when given: else 409), or, if it is
    gone, made again (create_source: never over anything that is there, 409; then listed
    and committed as a file the Files tab made); within source_budget (413, 429). 200 {"path", "version", "created"}."""
    rev, rel, version = req.get("rev"), req.get("path"), req.get("version")
    if not isinstance(rev, str) or not isinstance(rel, str) or not isinstance(version, (str, type(None))):
        return 400, {"error": "expected rev and path (and maybe version)"}
    rel = os.path.normpath(rel.strip() or ".")
    if not rel.lower().endswith(EDITABLE_EXTS):
        return 400, {"error": f"only sources ({', '.join(EDITABLE_EXTS)}) are restored here"}
    if not (sha := commit_sha(server.paper_root, rev, any_rev=user["role"] == "owner")):
        return 404, {"error": "no such commit here"}
    data, why = file_at_commit(server.paper_root, sha, rel, EDITABLE_SIZE)
    if data is None:
        return 404, {"error": why}
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return 415, {"error": f"{rel} is not UTF-8 there, so the editor can't restore it"}
    with NOTE_LOCK:
        if paused(server):
            return updating()
        real = editable_files(server).get(rel) or oversized_source(server, rel)  # (a source past 4 MB: put back too)
        try:
            if real:
                with open_source(real, limit=OVERSIZED_LIMIT) as f:
                    raw = f.read()
                if version is not None and file_version(raw) != version:
                    return 409, {"error": f"{rel} changed meanwhile: look again before restoring it"}
                written = os.path.relpath(real, os.path.realpath(server.paper_root))
                if raw != data:
                    refusal, cost = source_budget(server, user, written, raw, data)
                    if refusal:
                        return refusal
                    if not commit_others(server, user, written):
                        return updating()
                    write_source(real, raw.decode("utf-8"), text)
                    charge(server, user, cost)
                    remember_write(server, user, written, "edit")
                    wrote(server, user, written, file_version(data))
            else:
                if len(server.added) >= ADDED_LIMIT:
                    return 409, {"error": f"{ADDED_LIMIT} files were added here already: remove some first"}
                rel = new_path(server.paper_root, rel, EDITABLE_EXTS)
                refusal, cost = source_budget(server, user, rel, b"", data, adding=True)
                if refusal:
                    return refusal
                if not commit_others(server, user, rel):
                    return updating()
                create_source(server.paper_root, rel, text)
                charge(server, user, cost)
                files_changed(server, user, add=[rel])
        except ValueError as e:
            return 400, {"error": f"{rel}: {e}"}
        except FileExistsError:
            return 409, {"error": f"{rel} is there, but not as a file the editor opens, so it was left as it is"}
        except SourceChanged:
            return 409, {"error": f"{rel} or a folder on the way changed; try again"}
        except UnicodeDecodeError:
            return 415, {"error": f"{rel} is not UTF-8, so the editor can't restore it"}
        except OSError as e:
            return 500, {"error": f"could not restore {rel}: {e.strerror or e}"}
        new_version = file_version(data)
        remember_version(server, rel, new_version, editor_text(text)[0])
    return 200, {"path": rel, "version": new_version, "created": not real}
