"""The browser editor's file API: GET /files lists the sources it opens and the paper's figures, GET /file reads one,
and POST /file saves one, merged (git merge-file) with what was saved meanwhile since the version the edits
started from; GET /completions lists what it completes in \\ref, \\cite and \\includegraphics, and GET /search
finds text in all of them. (The Files tab's actions and the History tab's restore: file_actions.)

A file's version is a hash of its bytes; the editor works with \\n line ends, and a file that uses \\r\\n
gets them back on save. Reads and saves go through read_editable and write_source under NOTE_LOCK, as notes
do, so the two never overwrite each other, and neither follows a link swapped in since the file was checked."""

import os
import re
import tempfile
import time

from . import config
from .commits import charge, commit_others, paused, remember_write, source_budget, updating
from .git import run_git
from .latex import bib_entries, parsed_file
from .sources import (
    ASSET_SIZE, EDITABLE_SIZE, NOTE_LOCK, SourceChanged, asset_real, cached_version, doc_assets, doc_figures, editable_files,
    editable_real, file_version, project_assets, read_editable, read_editable_text, write_source, wrote)


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
COMPLETIONS_LIMIT = 5000


def completions(server, user):
    """GET /completions: what the browser editor offers in \\ref, \\cite and \\includegraphics, and shows when the
    pointer rests on one, from the files it opens (so nothing an editor couldn't read there) and the paper's figures:
    {"labels": [{"label", "file", "line"}] from the \\label commands outside comments, "labelContext": {label: the
    line it is on (or the one above, for a \\label alone on its line)}, "keys": [{"key", "title", "file", "line"}] from
    the .bib files' entries, "figures": [path] (listed_assets: those user's Files tab lists)}, at most COMPLETIONS_LIMIT
    labels and keys. Each file is read again only once it has changed (parsed_file, as the Refs panel reads them)."""
    labels, keys, context = [], [], {}
    for rel, real in editable_files(server).items():
        if not rel.endswith((".tex", ".bib")) or len(labels) >= COMPLETIONS_LIMIT and len(keys) >= COMPLETIONS_LIMIT:
            continue
        try:
            if rel.endswith(".bib"):
                entries = parsed_file(real, bib_entries, read_editable_text)[:max(0, COMPLETIONS_LIMIT - len(keys))]
                keys += [{**key, "file": rel} for key in bib_completions(entries)]
                continue
            found, around = parsed_file(real, label_context, read_editable_text)
        except (OSError, SourceChanged):
            continue
        for label, line in found[:COMPLETIONS_LIMIT - len(labels)]:
            labels.append({"label": label, "file": rel, "line": line})
            if label in around:
                context.setdefault(label, around[label])
    return {"labels": labels, "labelContext": context, "keys": keys, "figures": [a["path"] for a in listed_assets(server, user)]}


def label_context(text):
    """label_lines with their context (the completions' reading of a .tex file)."""
    return label_lines(text, True)


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


def bib_completions(entries):
    """[{"key", "title", "line"}] of a .bib file's entries (latex.bib_entries'), the title at most 120 characters."""
    return [{"key": e["key"], "title": e["fields"].get("title", "")[:120], "line": e["line"]} for e in entries]


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
        except (OSError, RuntimeError, UnicodeDecodeError) as e:
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
        r = run_git(tmp, ["-c", "merge.conflictStyle=merge", "merge-file", "-p", "-q", *labels, *paths],
                    limit=2 * sum(os.path.getsize(path) for path in paths) + 1_000_000, timeout=60)  # (all of it)
    if r.code is None or r.more or not 0 <= r.code <= 127:  # (git's count of conflicts stops at 127; it fails with 255)
        raise RuntimeError(r.err.strip() or f"git merge-file failed ({r.code})")
    return r.code, r.out.decode("utf-8")


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
