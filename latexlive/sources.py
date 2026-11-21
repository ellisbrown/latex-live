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
# Files that can be created in the editor, or uploaded / previewed as figures and assets.
CREATE_EXTS = EDITABLE_EXTS
ASSET_EXTS = (".png", ".jpg", ".jpeg", ".pdf", ".svg", ".eps", ".webp", ".gif")
UPLOAD_EXTS = (*EDITABLE_EXTS, *ASSET_EXTS)
UPLOAD_SIZE = 10_000_000  # bytes (raw file bytes; base64 upload body limit is in web.POST_LIMITS)


def _path_allowed(rel, exts):
    """Whether rel is a clean relative path with one of exts, free of .., NULs, dot segments, and BUILD."""
    if not isinstance(rel, str) or not rel or "\0" in rel or os.path.isabs(rel):
        return None
    norm = os.path.normpath(rel)
    parts = norm.split(os.sep)
    if (norm in (".", "..") or parts[0] == ".." or any(part.startswith(".") for part in parts)
            or os.path.splitext(norm)[1].lower() not in exts
            or norm == config.BUILD or norm.startswith(os.path.join(config.BUILD, ""))):
        return None
    return norm


def editable_real(paper_root, rel):
    """The real path of rel, a path relative to the paper root, if the browser editor may open it,
    else None: an existing regular .tex, .bib, .sty or .cls file of at most 4 MB in the paper root,
    not in the build directory, in .git or in another folder whose name starts with a dot, and not a
    dot file; all of that also once symbolic links are resolved (a link to a file elsewhere is not)."""
    if not _path_allowed(rel, EDITABLE_EXTS):
        return None
    root = os.path.realpath(paper_root)
    real = os.path.realpath(os.path.join(root, rel))
    if not _path_allowed(os.path.relpath(real, root), EDITABLE_EXTS):
        return None
    try:
        st = os.stat(real)
    except (OSError, ValueError):
        return None
    return real if stat.S_ISREG(st.st_mode) and st.st_size <= EDITABLE_SIZE else None


def asset_real(paper_root, rel):
    """The real path of rel if it is an existing regular figure/asset inside the paper root (up to
    UPLOAD_SIZE), else None: same confinement rules as editable_real. Root-level .pdf files are
    compiled outputs or standalone documents (never figure assets), so they are excluded."""
    norm = _path_allowed(rel, ASSET_EXTS)
    if not norm:
        return None
    if os.path.splitext(norm)[1].lower() == ".pdf" and os.path.dirname(norm) == "":
        return None
    root = os.path.realpath(paper_root)
    real = os.path.realpath(os.path.join(root, rel))
    rel_real = os.path.relpath(real, root)
    if not _path_allowed(rel_real, ASSET_EXTS):
        return None
    if os.path.splitext(rel_real)[1].lower() == ".pdf" and os.path.dirname(rel_real) == "":
        return None
    try:
        st = os.stat(real)
    except (OSError, ValueError):
        return None
    return real if stat.S_ISREG(st.st_mode) and st.st_size <= UPLOAD_SIZE else None


def safe_new_path(paper_root, rel, exts):
    """Validate rel as a new path inside paper_root with extension in exts, returning (norm_rel, real_path)
    or (None, None). Rejects any existing symlink ancestor that escapes paper_root."""
    norm = _path_allowed(rel, exts)
    if not norm:
        return None, None
    root = os.path.realpath(paper_root)
    parts = norm.split(os.sep)
    cur = root
    for part in parts[:-1]:
        cand = os.path.join(cur, part)
        if os.path.lexists(cand):
            if os.path.islink(cand) or not os.path.isdir(cand):
                return None, None
            resolved = os.path.realpath(cand)
            if resolved != root and not resolved.startswith(os.path.join(root, "")):
                return None, None
        cur = cand
    target = os.path.join(root, norm)
    if os.path.islink(target):
        return None, None
    return norm, target


def _ensure_folder(paper_root, rel_dir):
    """Open (creating missing directories inside paper_root) rel_dir without following symbolic links,
    returning an open directory file descriptor. Raises SourceChanged if any segment is a link."""
    root = os.path.realpath(paper_root)
    fd = source_folder(os.path.join(root, "."))
    try:
        parts = [p for p in rel_dir.split(os.sep) if p and p != "."]
        for part in parts:
            try:
                os.mkdir(part, 0o755, dir_fd=fd)
            except FileExistsError:
                pass
            try:
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except OSError as e:
                if e.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise SourceChanged() from e
                raise
            os.close(fd)
            fd = nxt
    except BaseException:
        os.close(fd)
        raise
    return fd


def create_source(paper_root, rel, text=""):
    """Create a new text source file at rel inside paper_root without following links. Raises ValueError
    on disallowed path, FileExistsError if it already exists, SourceChanged on a symlink race."""
    norm, _ = safe_new_path(paper_root, rel, CREATE_EXTS)
    if not norm:
        raise ValueError("path is not an allowed source file inside the paper")
    folder = _ensure_folder(paper_root, os.path.dirname(norm))
    try:
        name = os.path.basename(norm)
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=folder)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(text.encode("utf-8"))
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(name, dir_fd=folder)
            raise
    finally:
        os.close(folder)
    return norm


def upload_file(paper_root, rel, data, overwrite=False):
    """Write uploaded bytes to rel inside paper_root atomically without following links. Raises ValueError
    on disallowed path/size, FileExistsError if overwrite is False and target exists."""
    norm, _ = safe_new_path(paper_root, rel, UPLOAD_EXTS)
    if not norm:
        raise ValueError("path or file type is not allowed inside the paper")
    if len(data) > UPLOAD_SIZE:
        raise ValueError("uploaded file is larger than 10 MB")
    folder = _ensure_folder(paper_root, os.path.dirname(norm))
    try:
        name = os.path.basename(norm)
        try:
            st = os.lstat(name, dir_fd=folder)
        except FileNotFoundError:
            st = None
        if st is not None:
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                raise SourceChanged()
            if not overwrite:
                raise FileExistsError(norm)
        tmp = f".{name}.latex-live-up-{os.getpid()}"
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp, dir_fd=folder)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=folder)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, name, src_dir_fd=folder, dst_dir_fd=folder)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp, dir_fd=folder)
            raise
    finally:
        os.close(folder)
    return norm


def _no_symlink_existing(paper_root, rel, exts):
    """Validate that rel is an existing non-symlink regular file inside paper_root with no symlink
    ancestor segments, returning (norm_rel, abs_path) or (None, None)."""
    norm = _path_allowed(rel, exts)
    if not norm:
        return None, None
    root = os.path.realpath(paper_root)
    cur = root
    for part in norm.split(os.sep):
        cand = os.path.join(cur, part)
        try:
            st = os.lstat(cand)
        except OSError:
            return None, None
        if stat.S_ISLNK(st.st_mode):
            return None, None
        cur = cand
    if not stat.S_ISREG(st.st_mode):
        return None, None
    return norm, cur


def rename_source(paper_root, old_rel, new_rel):
    """Rename a verified non-symlink regular source or asset file inside paper_root without
    overwriting an existing file."""
    old_norm, old_real = _no_symlink_existing(paper_root, old_rel, UPLOAD_EXTS)
    if not old_real:
        raise FileNotFoundError(old_rel)
    allowed_exts = EDITABLE_EXTS if os.path.splitext(old_norm)[1].lower() in EDITABLE_EXTS else UPLOAD_EXTS
    new_norm, _ = safe_new_path(paper_root, new_rel, allowed_exts)
    if not new_norm:
        raise ValueError("target path is not allowed inside the paper")
    if old_norm == new_norm:
        return new_norm
    src_fd = source_folder(old_real)
    dst_fd = None
    try:
        st = os.lstat(os.path.basename(old_real), dir_fd=src_fd)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            raise SourceChanged()
        dst_fd = _ensure_folder(paper_root, os.path.dirname(new_norm))
        dst_name = os.path.basename(new_norm)
        try:
            os.lstat(dst_name, dir_fd=dst_fd)
            raise FileExistsError(new_norm)
        except FileNotFoundError:
            pass
        os.rename(os.path.basename(old_real), dst_name, src_dir_fd=src_fd, dst_dir_fd=dst_fd)
    finally:
        os.close(src_fd)
        if dst_fd is not None:
            os.close(dst_fd)
    return new_norm


def delete_source(paper_root, rel):
    """Delete a verified non-symlink regular source or asset file inside paper_root without
    following links."""
    norm, real = _no_symlink_existing(paper_root, rel, UPLOAD_EXTS)
    if not real:
        raise FileNotFoundError(rel)
    folder = source_folder(real)
    try:
        name = os.path.basename(real)
        st = os.lstat(name, dir_fd=folder)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            raise SourceChanged()
        os.unlink(name, dir_fd=folder)
    finally:
        os.close(folder)
    return norm


def project_assets(server):
    """Figure and image assets inside the paper root (excluding build/, dot folders, and root doc PDFs),
    returned as [{"path", "size", "ext"}] sorted by path (capped at 400)."""
    root = os.path.realpath(server.paper_root)
    doc = server.default_pdf[:-4]
    skip_root_pdfs = {f"{doc}.pdf", f"{doc}-clean.pdf", f"{doc}-diff.pdf", f"{doc}-export.pdf",
                      f"{doc}-review.pdf", f"{doc}-annotated.pdf"}
    found = {}
    for rel in project_inputs(root, fls_file(root, server.default_pdf)):
        norm = os.path.normpath(rel)
        if norm in skip_root_pdfs:
            continue
        if real := asset_real(root, norm):
            try:
                found[norm] = os.stat(real).st_size
            except OSError:
                pass
    # Also scan up to 4 levels deep for figures not yet referenced in the last build.
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        depth = 0 if rel_dir == "." else rel_dir.count(os.sep) + 1
        dirnames[:] = [d for d in sorted(dirnames)
                       if not d.startswith(".") and d != config.BUILD and depth < 4
                       and not os.path.islink(os.path.join(dirpath, d))]
        for name in sorted(filenames):
            if len(found) >= 400:
                break
            if name.startswith("."):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext not in ASSET_EXTS:
                continue
            rel = name if rel_dir == "." else os.path.join(rel_dir, name)
            if rel in skip_root_pdfs or rel in found:
                continue
            if ext == ".pdf" and rel_dir == ".":
                continue
            if real := asset_real(root, rel):
                try:
                    found[rel] = os.stat(real).st_size
                except OSError:
                    pass
    return [{"path": rel, "size": size, "ext": os.path.splitext(rel)[1].lower()} for rel, size in sorted(found.items())]


def editable_files(server, fresh=True):
    """The files the browser editor opens, {path relative to the paper root: real path}, the main file
    first, then by path: those of the document's files that editable_real allows, which are the files
    its last build read (fls_file), its main file, the .bib files in the paper root and in the folders
    in it (as find_bib_entry looks for them), the files its \\input and \\include commands name
    (source_tree, so they show before the first build), and any files created through the editor.
    Without fresh, a list up to 2 s old does (status polls)."""
    cached = server.editable
    if not fresh and cached and time.monotonic() - cached[0] < 2 and cached[1] == server.default_pdf:
        return cached[2]
    root, doc = server.paper_root, server.default_pdf[:-4]
    main = f"{doc}.tex"
    names = [main, *project_inputs(root, fls_file(root, server.default_pdf))]
    names += [os.path.relpath(p, root) for pattern in ("*.bib", os.path.join("*", "*.bib"))
              for p in glob.glob(os.path.join(glob.escape(root), pattern))]
    names += [os.path.relpath(os.path.normpath(p), root) for p in source_tree(root, doc)]
    names += list(getattr(server, "created_files", ()))
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
