"""Writing the paper's sources: the files the browser editor opens, the lock that every write
(a note, a save) takes, writing a file in one step unless it changed meanwhile, who wrote which
version of a file, and the Files tab's changes (a file made, uploaded, renamed or removed)."""

import contextlib
import errno
import glob
import hashlib
import json
import os
import re
import secrets
import stat
import threading
import time

from . import config
from .auth import owner_name
from .files import fls_file, in_build, plain_rel, project_inputs, read_json
from .latex import source_tree


NOTE_LOCK = threading.Lock()  # one read-modify-write of a source file at a time (a note, an editor's save, a commit)


class SourceChanged(Exception):
    """The source file changed between reading it and writing it back."""


class SourceTooBig(Exception):
    """A write would take a source past EDITABLE_SIZE, which the editor no longer opens."""


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


def read_editable_text(real):
    """read_editable's bytes as text, bytes that don't decode replaced (what the completions and the Refs panel read)."""
    return read_editable(real).decode("utf-8", errors="replace")


def read_source(path):
    """A source file's exact text (line endings kept), from its real path (open_source)."""
    with open_source(path) as f:
        return f.read().decode("utf-8")


def write_source(path, old, new):
    """Write new over a source file, at its real path, in one step (latexmk never reads half of it),
    unless it no longer holds old: an editor saved it in the meantime. The file and the new one beside
    it are opened in its folder without following links (source_folder), so a link swapped in since
    the file was checked is never followed (SourceChanged). Never past EDITABLE_SIZE bytes (SourceTooBig): the
    editor, the saves and History's restore would no longer take the file."""
    data = new.encode("utf-8")
    if len(data) > EDITABLE_SIZE:
        raise SourceTooBig()
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
                f.write(data)
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
    return paper_file(paper_root, rel, EDITABLE_EXTS, EDITABLE_SIZE)


def paper_file(paper_root, rel, exts, limit):
    """The real path of rel, relative to the paper root, if it is an existing regular file there with one of exts, of
    at most limit bytes, outside the build directory and dot folders and not a dot file, also once symbolic links are
    resolved; else None."""
    if not plain_rel(rel):
        return None
    root = os.path.realpath(paper_root)
    real = os.path.realpath(os.path.join(root, rel))
    for path in (rel, os.path.relpath(real, root)):  # (as named, and once links are resolved)
        if not (norm := plain_rel(path)) or os.path.splitext(norm)[1].lower() not in exts:
            return None
    try:
        st = os.stat(real)
    except (OSError, ValueError):
        return None
    return real if stat.S_ISREG(st.st_mode) and st.st_size <= limit else None


# The figures and images in the paper (asset_real), which the editor completes in \includegraphics.
ASSET_EXTS = (".png", ".jpg", ".jpeg", ".pdf", ".svg", ".eps", ".webp", ".gif")
ASSET_SIZE = 10_000_000  # bytes
ASSETS_LIMIT, ASSETS_DEPTH, ASSETS_TTL = 400, 4, 5  # (project_assets: how many, how deep, how long a list does, in s)


def asset_real(paper_root, rel):
    """The real path of rel, relative to the paper root, if it is a figure or image there (paper_file, with
    ASSET_EXTS, at most ASSET_SIZE), else None. A .pdf in the paper root is a document or its build, never a figure."""
    real = paper_file(paper_root, rel, ASSET_EXTS, ASSET_SIZE)
    if real and real.lower().endswith(".pdf"):
        if os.sep not in os.path.normpath(rel) or os.path.dirname(real) == os.path.realpath(paper_root):
            return None
    return real


def project_assets(server):
    """The paper's figures and images, [{"path", "size"}] by path: those the document's last build read, then the
    others in the paper root and its folders, ASSETS_DEPTH deep, up to ASSETS_LIMIT in all. A list up to ASSETS_TTL
    seconds old does (the editor asks for completions while typing); invalidate_assets forgets it."""
    cached = getattr(server, "assets", None)
    if cached and time.monotonic() - cached[0] < ASSETS_TTL and cached[1] == server.default_pdf:
        return cached[2]
    root = os.path.realpath(server.paper_root)
    found = {}

    def add(rel):
        if rel not in found and len(found) < ASSETS_LIMIT and (real := asset_real(root, rel)):
            with contextlib.suppress(OSError):
                found[rel] = os.stat(real).st_size

    for rel in project_inputs(root, fls_file(root, server.default_pdf)):
        add(os.path.normpath(rel))
    walk = os.walk(root) if len(found) < ASSETS_LIMIT else ()  # (figures not in the document yet; it follows no links)
    for folder, dirs, names in walk:
        here = os.path.relpath(folder, root)
        depth = 0 if here == "." else here.count(os.sep) + 1
        dirs[:] = sorted(d for d in dirs if depth < ASSETS_DEPTH and not d.startswith(".")
                         and not in_build(os.path.normpath(os.path.join(here, d))))
        for name in sorted(names):
            if os.path.splitext(name)[1].lower() in ASSET_EXTS and not name.startswith("."):
                add(name if here == "." else os.path.join(here, name))
        if len(found) >= ASSETS_LIMIT:
            break
    assets = [{"path": rel, "size": size} for rel, size in sorted(found.items())]
    server.assets = (time.monotonic(), server.default_pdf, assets)
    return assets


def invalidate_assets(server):
    """Forget the list of figures (project_assets), e.g. once a file is added, renamed or removed."""
    server.assets = None


def editable_files(server, fresh=True):
    """The files the browser editor opens, {path relative to the paper root: real path}, the main file
    first, then by path: those of the document's files that editable_real allows, which are the files
    its last build read (fls_file), its main file, the .bib files in the paper root and in the folders
    in it (as find_bib_entry looks for them), the files its \\input and \\include commands name
    (source_tree, so they show before the first build), and the sources added through the Files tab (server.added).
    Without fresh, a list up to 2 s old does (status polls)."""
    cached = server.editable
    if not fresh and cached and time.monotonic() - cached[0] < 2 and cached[1] == server.default_pdf:
        return cached[2]
    found = {}
    for rel in document_sources(server):
        if real := editable_real(server.paper_root, rel):
            found[rel] = real
    server.editable = (time.monotonic(), server.default_pdf, found)
    return found


def document_sources(server):
    """The paths that editable_files looks at (relative to the paper root, normalized), the main file first."""
    root, doc = server.paper_root, server.default_pdf[:-4]
    main = f"{doc}.tex"
    names = [main, *project_inputs(root, fls_file(root, server.default_pdf))]
    names += [os.path.relpath(p, root) for pattern in ("*.bib", os.path.join("*", "*.bib"))
              for p in glob.glob(os.path.join(glob.escape(root), pattern))]
    names += [os.path.relpath(os.path.normpath(p), root) for p in source_tree(root, doc)]
    names += [rel for rel in getattr(server, "added", ()) if rel.lower().endswith(EDITABLE_EXTS)]
    return sorted({os.path.normpath(name) for name in names}, key=lambda rel: (rel != main, rel))


def oversized_source(server, rel):
    """The real path of rel if it is one of the document's sources (document_sources) that the editor doesn't open only
    because it is past EDITABLE_SIZE (editable_real but for the size), else None: History's restore may put it back."""
    if rel not in document_sources(server):
        return None
    real = paper_file(server.paper_root, rel, EDITABLE_EXTS, float("inf"))
    try:
        return real if real and os.path.getsize(real) > EDITABLE_SIZE else None
    except OSError:
        return None


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


# The Files tab's changes (file_actions.py): files made, uploaded, renamed or removed through the server. A new name holds
# NAME_RE's parts, at most ASSETS_DEPTH folders deep (where project_assets looks), never in the build directory nor a
# .pdf in the paper root (a document's build); an upload is a figure (ASSET_EXTS), never a source, which only the
# editor's save writes; and nothing is written over a file that is there.
NAME_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_+.-]{0,99}")  # (no space, comma, colon, quote or control character)
ADDED_LIMIT = 400  # files added through the server that it keeps listing (server.added)


def new_path(paper_root, rel, exts):
    """rel, normalized, if a new file with one of exts may go there (see above): its folders, where there, are folders
    of the paper, not links. Else ValueError, saying why. (FileExistsError comes when it is written.)"""
    if not isinstance(rel, str) or not rel.strip() or "\0" in rel:
        raise ValueError("expected a file name")
    norm = os.path.normpath(rel.strip())
    parts = norm.split(os.sep)
    if os.path.isabs(norm) or not all(NAME_RE.fullmatch(part) for part in parts):
        raise ValueError("a name holds letters, digits, -, _, + and dots (not first), with / between folders")
    ext = os.path.splitext(norm)[1].lower()
    if ext not in exts:
        raise ValueError(f"expected a {', '.join(exts)} file")
    if len(parts) - 1 > ASSETS_DEPTH:
        raise ValueError(f"at most {ASSETS_DEPTH} folders deep")
    if in_build(norm):
        raise ValueError("the build folder holds what the build writes")
    if ext == ".pdf" and len(parts) == 1:
        raise ValueError("a PDF figure goes in a folder: a PDF in the paper's folder is a document's")
    root = folder = os.path.realpath(paper_root)
    for part in parts[:-1]:
        folder = os.path.join(folder, part)
        if os.path.islink(folder) or os.path.lexists(folder) and not os.path.isdir(folder):
            raise ValueError(f"{os.path.relpath(folder, root)} is not a folder of the paper")
    return norm


def paper_folder(paper_root, folder, make=False):
    """A descriptor of folder (relative to the paper root), opened one part at a time from the paper root without
    following a link, as source_folder opens a source's; with make, the parts not there yet are made first.
    SourceChanged if a part is a link or a file now."""
    fd = source_folder(os.path.join(os.path.realpath(paper_root), "x"))
    try:
        for part in [p for p in folder.split(os.sep) if p]:
            if make:
                with contextlib.suppress(FileExistsError):
                    os.mkdir(part, 0o755, dir_fd=fd)
            try:
                fd, parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd), fd
            except OSError as e:
                if e.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise SourceChanged() from e
                raise
            os.close(parent)
    except BaseException:
        os.close(fd)
        raise
    return fd


def create_source(paper_root, rel, text):
    """Make rel, a new source file (new_path, EDITABLE_EXTS), holding text, never over a file that is there
    (FileExistsError). Returns rel normalized."""
    norm, data = new_path(paper_root, rel, EDITABLE_EXTS), text.encode("utf-8")  # (UnicodeEncodeError before a folder is made)
    folder = paper_folder(paper_root, os.path.dirname(norm), make=True)
    name = os.path.basename(norm)
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=folder)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(name, dir_fd=folder)
            raise
    finally:
        os.close(folder)
    return norm


def add_file(paper_root, rel, data):
    """Write data as rel, a new figure (new_path, ASSET_EXTS), in one step: into a file of a random name beside it, then
    linked to its name, which fails where a file is there (FileExistsError: nothing written over, even one that came
    meanwhile), and the random name removed. Returns rel normalized."""
    norm = new_path(paper_root, rel, ASSET_EXTS)
    folder = paper_folder(paper_root, os.path.dirname(norm), make=True)
    tmp = f".latex-live-up-{secrets.token_hex(8)}"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=folder)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.link(tmp, os.path.basename(norm), src_dir_fd=folder, dst_dir_fd=folder, follow_symlinks=False)
        finally:
            with contextlib.suppress(OSError):
                os.unlink(tmp, dir_fd=folder)
    finally:
        os.close(folder)
    return norm


def own_file(paper_root, rel):
    """(rel normalized, its real path) if it is a source or figure of the paper (editable_real, asset_real) reached
    without a link on the way, so that renaming or removing it changes that file alone; else FileNotFoundError."""
    norm = os.path.normpath(rel) if isinstance(rel, str) and rel.strip() and "\0" not in rel else None
    real = norm and (editable_real(paper_root, norm) or asset_real(paper_root, norm))
    if not real or real != os.path.join(os.path.realpath(paper_root), norm):
        raise FileNotFoundError(rel)
    return norm, real


def move_file(paper_root, rel, new_rel):
    """Rename rel, a source or figure of the paper (own_file), to new_rel (new_path, with the extensions of rel's kind:
    a source stays a source, a figure a figure), never over a file that is there (a hard link to the new name, which
    fails then: FileExistsError, then the old name removed). Returns new_rel normalized."""
    old, real = own_file(paper_root, rel)
    norm = new_path(paper_root, new_rel, EDITABLE_EXTS if old.lower().endswith(EDITABLE_EXTS) else ASSET_EXTS)
    src = source_folder(real)
    try:
        if not stat.S_ISREG(os.lstat(os.path.basename(real), dir_fd=src).st_mode):
            raise SourceChanged()
        dst = paper_folder(paper_root, os.path.dirname(norm), make=True)
        try:
            os.link(os.path.basename(real), os.path.basename(norm), src_dir_fd=src, dst_dir_fd=dst, follow_symlinks=False)
            try:
                os.unlink(os.path.basename(real), dir_fd=src)
            except BaseException:  # (not renamed after all: the new name goes)
                with contextlib.suppress(OSError):
                    os.unlink(os.path.basename(norm), dir_fd=dst)
                raise
        finally:
            os.close(dst)
    finally:
        os.close(src)
    return norm


def remove_file(paper_root, rel):
    """Remove rel, a source or figure of the paper (own_file). Returns rel normalized."""
    norm, real = own_file(paper_root, rel)
    folder = source_folder(real)
    try:
        if not stat.S_ISREG(os.lstat(os.path.basename(real), dir_fd=folder).st_mode):
            raise SourceChanged()
        os.unlink(os.path.basename(real), dir_fd=folder)
    finally:
        os.close(folder)
    return norm


def changed_path(paper_root, rel):
    """Whether rel may be a path the Files tab changed (to commit it, as load_pending reads it back): one it could have
    made (no dot part, a source's or a figure's extension, outside the build directory), and gone, or one of the
    paper's own files there (own_file)."""
    norm = plain_rel(rel)
    if not norm or not norm.lower().endswith((*EDITABLE_EXTS, *ASSET_EXTS)):
        return False
    if not os.path.lexists(os.path.join(os.path.realpath(paper_root), norm)):
        return True
    try:
        return own_file(paper_root, norm)[0] == norm
    except FileNotFoundError:
        return False


def load_added(server):
    """The files added through the Files tab of the document (made, uploaded, renamed to), which it lists whether or
    not the document reads them yet, as the state folder keeps them for each document (remember_added), those still
    there. Again when the preview switches to another document."""
    server.added_file = os.path.join(config.STATE, "added", f"{server.paper_id}-{server.default_pdf[:-4]}.json")
    data = read_json(server.added_file)
    names = data.get("files") if isinstance(data, dict) and data.get("root") == server.paper_root else None
    root = os.path.realpath(server.paper_root)
    found = [os.path.normpath(rel) for rel in (names if isinstance(names, list) else [])
             if isinstance(rel, str) and rel and not os.path.isabs(rel) and (editable_real(root, rel) or asset_real(root, rel))]
    server.added = set(found[:ADDED_LIMIT])


def remember_added(server, add=(), drop=()):
    """Add files to the ones added through the Files tab (load_added) and drop others, and keep the list in the state
    folder. The caller holds NOTE_LOCK."""
    server.added = (server.added - set(drop)) | set(add)
    path = server.added_file
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
            json.dump({"root": server.paper_root, "files": sorted(server.added)}, f)
        os.replace(tmp, path)
    except OSError as e:
        print(f"=== latex-live: could not keep the list of files added through the viewer in {path} ({e})", flush=True)


def doc_figures(server):
    """The paths of the document's own figures, which a coauthor's Files tab lists and changes in shared mode: those
    its last build read and those added through the Files tab (not all figures: asset_real says which are)."""
    root = os.path.realpath(server.paper_root)
    return {os.path.normpath(rel) for rel in project_inputs(root, fls_file(root, server.default_pdf))} | getattr(server, "added", set())


def doc_assets(server):
    """The document's figures (doc_figures), of the paper's (project_assets)."""
    own = doc_figures(server)
    return [a for a in project_assets(server) if a["path"] in own]
