"""Writing the paper's sources: the files the browser editor opens, the lock that every write
(a note, a save) takes, writing a file in one step unless it changed meanwhile, and who wrote which
version of a file."""

import glob
import hashlib
import os
import shutil
import stat
import threading
import time

from . import config
from .auth import owner_name
from .files import fls_file, project_inputs
from .latex import source_tree


NOTE_LOCK = threading.Lock()  # one read-modify-write of a source file at a time (a note, an editor's save, a commit)


class SourceChanged(Exception):
    """The source file changed between reading it and writing it back."""


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


# The files the browser editor opens (editable_real): sources of at most 4 MB.
EDITABLE_EXTS = (".tex", ".bib", ".sty", ".cls")
EDITABLE_SIZE = 4_000_000  # bytes (a save sends the text, and sometimes the base text too: POST_LIMITS)


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


def file_version(data):
    """A source file's version: the first 20 hex digits of the SHA-256 of its bytes."""
    return hashlib.sha256(data).hexdigest()[:20]


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
