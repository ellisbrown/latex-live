"""Writing the paper's sources: the files the browser editor opens, the lock that every write
(a note, a save) takes, writing a file in one step unless it changed meanwhile, and who wrote which
version of a file."""

import contextlib
import errno
import glob
import hashlib
import os
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


def source_folder(real):
    """A descriptor of the folder of real, a source's real path (no link in it: os.path.realpath), opened
    one folder at a time from / without following a link (O_NOFOLLOW): what is read or written in it is
    the file that was checked (editable_real), even if a folder on the way was swapped for a link since.
    SourceChanged if one was."""
    fd = os.open(os.sep, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in os.path.dirname(real).split(os.sep):
            if part:
                fd, parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd), fd
                os.close(parent)
    except OSError as e:
        os.close(fd)
        if e.errno in (errno.ELOOP, errno.ENOTDIR):
            raise SourceChanged() from e
        raise
    return fd


def open_source(real, folder=None, limit=None):
    """real (a source's real path), opened for reading in its folder (source_folder, or the descriptor
    folder) without following a link, and checked once open (fstat): a regular file (of at most limit
    bytes, if given). SourceChanged if a link or anything else is there now."""
    own = folder is None
    folder = source_folder(real) if own else folder
    try:
        fd = os.open(os.path.basename(real), os.O_RDONLY | os.O_NOFOLLOW, dir_fd=folder)
    except OSError as e:
        if e.errno in (errno.ELOOP, errno.ENOTDIR):
            raise SourceChanged() from e
        raise
    finally:
        if own:
            os.close(folder)
    f = os.fdopen(fd, "rb")
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or limit is not None and st.st_size > limit:
        f.close()
        raise SourceChanged()
    return f


def read_editable(real):
    """The bytes of real, a file the browser editor opens (editable_real), as open_source opens it: a regular
    file of at most EDITABLE_SIZE."""
    with open_source(real, limit=EDITABLE_SIZE) as f:
        return f.read(EDITABLE_SIZE + 1)


def read_source(path):
    """A source file's exact text (line endings kept), from its real path (open_source)."""
    with open_source(path) as f:
        return f.read().decode("utf-8")


def write_source(path, old, new):
    """Write new over a source file, at its real path, in one step (latexmk never reads half of it),
    unless it no longer holds old: an editor saved it in the meantime. The file and the new one beside
    it are opened in its folder without following links (source_folder), so a link swapped in since
    the file was checked is never followed (SourceChanged)."""
    folder = source_folder(path)
    try:
        with open_source(path, folder) as f:
            mode = stat.S_IMODE(os.fstat(f.fileno()).st_mode)
            if f.read().decode("utf-8") != old:
                raise SourceChanged()
        name, tmp = os.path.basename(path), f"{os.path.basename(path)}.latex-live-{os.getpid()}"
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp, dir_fd=folder)  # (one a write that failed left)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=folder)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(new.encode("utf-8"))
                os.fchmod(f.fileno(), mode)
            os.replace(tmp, name, src_dir_fd=folder, dst_dir_fd=folder)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp, dir_fd=folder)
            raise
    finally:
        os.close(folder)


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
    with open_source(path) as f:  # (never through a link swapped in since the file was checked)
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
        except (OSError, SourceChanged):
            return
    server.last_writer[rel] = (version, user["name"] or (owner_name(server) if user["role"] == "owner" else user["id"]))
