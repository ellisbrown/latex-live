"""The browser editor's file API: GET /files lists the sources it opens and the paper's figures, GET /file reads one,
and POST /file saves one, merged (git merge-file) with what was saved meanwhile since the version the edits
started from; GET /completions lists what it completes in \\ref, \\cite and \\includegraphics, and GET /search
finds text in all of them. The Files tab sends a figure (GET /asset), makes a source file (POST /file/create),
uploads a figure (POST /file/upload), and renames or removes a source or figure (POST /file/rename, /file/delete). The
History tab puts a source back as a commit left it (POST /history/restore).

A file's version is a hash of its bytes; the editor works with \\n line ends, and a file that uses \\r\\n
gets them back on save. Reads and saves go through read_editable and write_source under NOTE_LOCK, as notes
do, so the two never overwrite each other, and neither follows a link swapped in since the file was checked."""

import base64
import binascii
import os
import re
import subprocess
import tempfile
import threading
import time

from . import config
from .commits import commit_others, paused, remember_write, updating
from .git import commit_sha, file_at_commit
from .latex import brace_group
from .sources import (
    ADDED_LIMIT, ASSET_EXTS, ASSET_SIZE, ASSETS_LIMIT, EDITABLE_EXTS, EDITABLE_SIZE, NOTE_LOCK, SourceChanged, add_file,
    asset_real, cached_version, create_source, doc_assets, doc_figures, editable_files, editable_real, file_version, invalidate_assets, move_file,
    new_path, open_source, oversized_source, project_assets, read_editable, remember_added, remove_file, write_source, wrote)


MERGE_LABELS = ("saved meanwhile", "base", "your edit")  # (git's conflict markers name the two sides)
TOO_BIG = "the editor saves files of up to 4 MB"


def files_listing(server, user):
    """GET /files: {"files": [{"path", "size"}], "main", "assets": [{"path", "size"}], "uploadSize"}: the files the
    browser editor opens, and the figures that the Files tab lists for user (listed_assets) and the largest it uploads."""
    files = []
    for rel, real in editable_files(server).items():
        try:
            files.append({"path": rel, "size": os.stat(real).st_size})
        except OSError:
            pass
    return {"files": files, "main": f"{server.default_pdf[:-4]}.tex", "assets": listed_assets(server, user),
            "uploadSize": ASSET_SIZE}


def listed_assets(server, user):
    """The figures that user's Files tab lists: the paper's (project_assets); in shared mode, a coauthor's are the
    document's (doc_assets), as their sources are."""
    return doc_assets(server) if config.SHARED and user["role"] != "owner" else project_assets(server)


def user_asset(server, user, rel):
    """The real path of rel if it is a figure that user may see, rename and remove (asset_real; in shared mode, for a
    coauthor, one of the document's: doc_figures), else None."""
    real = asset_real(server.paper_root, rel)
    return real if real and (not config.SHARED or user["role"] == "owner" or rel in doc_figures(server)) else None


LABEL_RE = re.compile(r"\\label\s*\{([^{}\n]{1,200})\}")
BIB_ENTRY_RE = re.compile(r"@(\w+)\s*[{(]\s*([^,\s{}()]+)\s*,")
BIB_TITLE_RE = re.compile(r"\btitle\s*=\s*([{\"])", re.I)
COMPLETIONS_LIMIT = 5000


def completions(server, user, *, cache={}):
    """GET /completions: what the browser editor offers in \\ref, \\cite and \\includegraphics, and shows when the
    pointer rests on one, from the files it opens (so nothing an editor couldn't read there) and the paper's figures:
    {"labels": [{"label", "file", "line"}] from the \\label commands outside comments, "labelContext": {label: the
    line it is on (or the one above, for a \\label alone on its line)}, "keys": [{"key", "title", "file", "line"}] from
    the .bib files' entries, "figures": [path] (listed_assets: those user's Files tab lists)}, at most COMPLETIONS_LIMIT
    labels and keys. Each file is read again only once it has changed."""
    labels, keys, context = [], [], {}
    for rel, real in editable_files(server).items():
        if not rel.endswith((".tex", ".bib")) or len(labels) >= COMPLETIONS_LIMIT and len(keys) >= COMPLETIONS_LIMIT:
            continue
        try:
            st = os.stat(real)
            entry = cache.get(real)
            if not entry or entry[0] != (st.st_mtime_ns, st.st_size):
                text = read_editable(real).decode("utf-8", errors="replace")
                found = (bib_completions(text), {}) if rel.endswith(".bib") else label_lines(text, True)
                entry = cache[real] = ((st.st_mtime_ns, st.st_size), *found)
        except (OSError, SourceChanged):
            continue
        found, around = entry[1:]
        if rel.endswith(".bib"):
            keys += [{**key, "file": rel} for key in found[:COMPLETIONS_LIMIT - len(keys)]]
        else:
            for label, line in found[:COMPLETIONS_LIMIT - len(labels)]:
                labels.append({"label": label, "file": rel, "line": line})
                if label in around:
                    context.setdefault(label, around[label])
    if len(cache) > 256:
        cache.clear()
    return {"labels": labels, "labelContext": context, "keys": keys, "figures": [a["path"] for a in listed_assets(server, user)]}


def label_lines(text, with_context=False):
    """[(label, line)] of the \\label commands outside comments in a .tex file's text; with_context, also {label: the
    line it is on, or for a \\label alone on its line the line above it (a caption, a heading), at most 140
    characters}."""
    found, line, at = [], 1, 0
    bare = re.sub(r"(?<!\\)%.*", "", text)
    for m in LABEL_RE.finditer(bare):
        line, at = line + bare.count("\n", at, m.start()), m.start()
        found.append((m.group(1), line))
    if not with_context:
        return found
    lines, context = bare.split("\n"), {}
    for label, n in found:
        here = lines[n - 1].strip()
        if here == f"\\label{{{label}}}" and n >= 2 and lines[n - 2].strip():
            here = lines[n - 2].strip()
        context.setdefault(label, " ".join(here.split())[:140])
    return found, context


def bib_completions(text):
    """[{"key", "title", "line"}] of a .bib file's entries, in one pass (a forgiving reading: the title as written, its
    braces dropped, at most 120 characters)."""
    entries = [m for m in BIB_ENTRY_RE.finditer(text) if m.group(1).lower() not in ("comment", "string", "preamble")]
    found, line, at = [], 1, 0
    for m, after in zip(entries, [*entries[1:], None]):
        title = BIB_TITLE_RE.search(text, m.end(), after.start() if after else len(text))
        if title and title.group(1) == "{":
            value = brace_group(text[title.start(1):title.start(1) + 2000], 0)[0]
        elif title:
            end = text.find('"', title.end(), title.end() + 2000)
            value = text[title.end():end] if end >= 0 else ""
        else:
            value = ""
        line, at = line + text.count("\n", at, m.start()), m.start()
        title = " ".join(value.replace("{", "").replace("}", "").split())[:120]
        found.append({"key": m.group(2), "title": title, "line": line})
    return found


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
    except (OSError, SourceChanged):
        return None


def file_request(server, query):
    """GET /file?path=<rel>: {"path", "text" (\\n line ends), "version", "eol"} of a file the browser
    editor opens; 404 for any other path, whether or not it exists, and 415 for a file that is not
    UTF-8."""
    rel = os.path.normpath(query.get("path", [""])[0] or ".")
    real = editable_files(server).get(rel)
    if not real:
        return 404, {"error": "not a file the editor opens"}
    try:
        with NOTE_LOCK:  # (as a save reads it)
            data = read_editable(real)
        text, eol = editor_text(data.decode("utf-8"))
    except (OSError, SourceChanged):
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
    with base_text. While the shared copy can't take a write, 503 (updating); past what user may write
    (source_budget), 413 or 429, with nothing written."""
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
            return 413, {"error": TOO_BIG}
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
    data = read_editable(real)  # (never through a link swapped in since editable_files checked it, nor a big file)
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
        return 413, {"error": TOO_BIG}
    new_version = file_version(encoded)
    if new != raw:
        refusal, cost = source_budget(server, user, written, data, encoded)
        if refusal:
            return refusal
        if not commit_others(server, user, written):  # (LATEX_LIVE_COMMIT: what others wrote there is theirs)
            return updating()
        write_source(real, raw, new)
        charge(server, user, cost)
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


SEARCH_LIMIT, SEARCH_QUERY, SEARCH_TEXT = 200, 200, 200  # (matches, characters of a query, of a line shown)


def search_request(server, query):
    """GET /search?q=<text>&case=1&regex=1: the lines of the files the browser editor opens that hold q, the first
    match of each, [{"file", "line", "col" (from 0), "len", "text" (SEARCH_TEXT characters of the line, from a little
    before the match when it lies further in, after an ellipsis), "at" (where the match is in text)}], at most
    SEARCH_LIMIT of them ("truncated" then); col, len and at count UTF-16 code units, as the editor does.
    Case-insensitive unless case=1; q is a regular expression with regex=1, except in shared mode, where it is always
    taken as written (a coauthor's pattern could take the server's time)."""
    q = query.get("q", [""])[0]
    if not q.strip() or len(q) > SEARCH_QUERY:
        return 200, {"matches": [], "truncated": False}
    regex = query.get("regex") == ["1"] and not config.SHARED
    try:
        pattern = re.compile(q if regex else re.escape(q), 0 if query.get("case") == ["1"] else re.I)
    except re.error as e:
        return 400, {"error": f"not a regular expression: {e}"}
    matches, utf16 = [], lambda s: len(s.encode("utf-16-le")) // 2
    for rel, real in editable_files(server).items():
        try:
            text = read_editable(real).decode("utf-8", errors="replace")
        except (OSError, SourceChanged):
            continue
        for n, line in enumerate(text.split("\n"), 1):
            line = line.removesuffix("\r")
            if (m := pattern.search(line)) and m.end() > m.start():
                if len(matches) == SEARCH_LIMIT:
                    return 200, {"matches": matches, "truncated": True}
                start = 0 if m.end() <= SEARCH_TEXT else max(0, m.start() - 60)
                shown = ("…" if start else "") + line[start:start + SEARCH_TEXT]
                matches.append({"file": rel, "line": n, "col": utf16(line[:m.start()]), "len": utf16(m.group()),
                                "text": shown, "at": utf16(shown[:m.start() - start + (1 if start else 0)])})
    return 200, {"matches": matches, "truncated": False}


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

# Sources written (an editor's save, an entry added in the Refs tab, a file made, a restore): in shared mode a
# coauthor's at most SOURCE_RATE bytes a day (UPLOAD_WINDOW), counted as git stores them (source_budget), and the
# sources added through the Files tab (server.added) at most ADDED_SOURCES_TOTAL bytes in all, for the same reason.
SOURCE_RATE, ADDED_SOURCES_TOTAL = 100_000_000, 20_000_000  # (bytes)


def source_budget(server, user, rel, old, new, adding=False):
    """Whether user may write new (bytes) over old (b"" for a file made now: adding, as the Files tab adds it) at rel,
    relative to the paper root: (None, what it costs their day, for charge) or (the refusal, 0). It costs, with
    LATEX_LIVE_COMMIT, the whole of new when rel is not among their writes still to commit (their next commit stores
    the file whole), else the bytes that changed (changed_bytes): typing costs a few bytes, writing a file anew its
    size. Refused past SOURCE_RATE today (429, for a coauthor in shared mode), or when a source added through the
    Files tab grows and the added sources would pass ADDED_SOURCES_TOTAL (413, for everyone). The caller holds NOTE_LOCK."""
    added = adding or rel in server.added
    if added and len(new) > len(old) and added_size(server, rel) + len(new) > ADDED_SOURCES_TOTAL:
        return (413, {"error": f"the sources added here would pass {ADDED_SOURCES_TOTAL // 1_000_000} MB: remove some first"}), 0
    if not config.SHARED or user["role"] == "owner":
        return None, 0
    with server.pending_lock:
        pending = rel in server.pending.get(user["id"], {}).get("paths", {})
    cost = len(new) if server.autocommit and not pending else changed_bytes(old, new)
    now = time.monotonic()
    writes = server.writes.setdefault(user["id"], [])
    writes[:] = [(t, n) for t, n in writes if now - t < UPLOAD_WINDOW]
    if sum(n for _, n in writes) + cost > SOURCE_RATE:
        return (429, {"error": f"you saved {SOURCE_RATE // 1_000_000} MB of changes to the sources today, the most a coauthor"
                               " writes in a day; try again tomorrow"}), 0
    return None, cost


def charge(server, user, cost):
    """Count a write that source_budget allowed against user's day."""
    if cost:
        server.writes.setdefault(user["id"], []).append((time.monotonic(), cost))


def added_size(server, but):
    """The bytes of the sources added through the Files tab (server.added) but the one at but."""
    root = os.path.realpath(server.paper_root)
    total = 0
    for rel in server.added:
        if rel != but and rel.lower().endswith(EDITABLE_EXTS):
            try:
                total += os.lstat(os.path.join(root, rel)).st_size
            except OSError:
                pass
    return total


def changed_bytes(old, new):
    """The bytes of new that are not in old: new but the start and the end the two share (each found by halving)."""
    def shared(fits, most):
        lo, hi = 0, most
        while lo < hi:
            mid = (lo + hi + 1) // 2
            lo, hi = (mid, hi) if fits(mid) else (lo, mid - 1)
        return lo
    start = shared(lambda n: old[:n] == new[:n], min(len(old), len(new)))
    end = shared(lambda n: old[len(old) - n:] == new[len(new) - n:], min(len(old), len(new)) - start)
    return len(new) - start - end


def files_changed(server, user, add=(), drop=()):
    """After a Files tab action for user (holding NOTE_LOCK): the files added are listed from now on, those dropped no
    longer (remember_added); the lists of sources and figures are made again; and each path is committed as user's
    (remember_write, kind "files": added to git, or removed)."""
    remember_added(server, add, drop)
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
