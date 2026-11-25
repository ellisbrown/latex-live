"""The paper's files and the build's: where each is (job_file, pdf_path...), which of them are the
paper's own (in_paper) or in the build directory (in_build), and reading and copying what TeX wrote within bounds and
without following a link (open_output, read_capped, read_output, copy_output), and the build's folders that the host
writes in, never through a link either (build_folder), since the paper's TeX decides how big those files are, and in
shared mode writes them in containers."""

import contextlib
import glob
import hashlib
import io
import json
import os
import re
import shutil
import stat
import sys

from . import config


def job_file(paper_root, pdf_name, ext):
    """build/<doc>-live<ext> for the published <doc>.pdf."""
    return os.path.join(paper_root, config.BUILD, f"{pdf_name[:-4]}-live{ext}")


def fls_file(paper_root, pdf_name):
    """The list of files the last successful pass of the published <doc>.pdf read: the copy of its
    recorder file (.fls) kept after each successful pass (latex_report.py, or shared_build),
    build/<doc>-live.fls.done. pdflatex rewrites the .fls itself during every pass, so mid-build it
    lacks the files read later (notes, note macros, the editor's files would come and go); before the
    first successful pass, it is all there is."""
    done = job_file(paper_root, pdf_name, ".fls.done")
    return done if os.path.isfile(done) else job_file(paper_root, pdf_name, ".fls")


def clean_file(paper_root, pdf_name, ext):
    """build/clean/<doc>-clean<ext>: the notes-free pass (latex_report.py; its PDF moves to build/)."""
    return os.path.join(paper_root, config.BUILD, "clean", f"{pdf_name[:-4]}-clean{ext}")


def shown_synctex(paper_root, pdf_name, clean=False):
    """The SyncTeX file of the PDF on show, the published <doc>.pdf (or with clean, the notes-free one): the newer of
    the build's own and the copy kept in build/shown/ when the PDF was published (latex_report.keep_synctex). A pass
    that fails removes the build's own, while the PDF on show stays the last good pass's. For synctex, the .pdf beside
    it (which need not exist)."""
    own = (clean_file if clean else job_file)(paper_root, pdf_name, ".synctex.gz")
    kept = os.path.join(paper_root, config.BUILD, "shown", os.path.basename(own))
    return kept if mtime(kept) > mtime(own) else own


def shown_synctexes(paper_root, clean=False):
    """shown_synctex of every document built here (or of their notes-free builds), newest first, with its mtime."""
    pattern = "*-clean.synctex.gz" if clean else "*-live.synctex.gz"
    build = glob.escape(os.path.join(paper_root, config.BUILD))
    names = {os.path.basename(p) for folder in ("clean" if clean else "", "shown")
             for p in glob.glob(os.path.join(build, folder, pattern))}
    found = [shown_synctex(paper_root, name.rsplit("-", 1)[0] + ".pdf", clean) for name in names]
    return sorted(((path, mtime(path)) for path in found), key=lambda p: p[1], reverse=True)


def open_output(path):
    """A file of the build's, open for reading in binary, or None if there is none or it is not a regular
    file. In shared mode, never through a symbolic link: the build folder is written in containers that
    run the coauthors' sources."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | (os.O_NOFOLLOW if config.SHARED else 0))
    except OSError:
        return None
    f = open(fd, "rb")
    if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
        f.close()
        return None
    return f


def create_output(path):
    """A new file of the build's at path, open for writing in binary: whatever is there first removed, a link too (the
    link, never what it points to), so that nothing is written through a link that TeX in a container left there."""
    with contextlib.suppress(FileNotFoundError):
        os.unlink(path)
    return open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644), "wb")


def build_folder(paper_root, rel, fresh=False):
    """The path of build/<rel>, made a folder of the build's own: each of its parts below the build folder a folder (made
    if missing), not a link or a file, which are removed first (the link, never what it points to), so that what the
    host writes there stays in build/, whatever TeX in a container left there; with fresh, the last part emptied first
    (made anew; OSError if it can't be). Each part is checked through the descriptor of the one before it."""
    base = os.path.join(paper_root, config.BUILD)
    parts = [part for part in os.path.normpath(rel).split(os.sep) if part]
    if not parts or any(part in (".", "..") for part in parts):
        raise ValueError(f"not a folder in the build folder: {rel}")
    os.makedirs(base, exist_ok=True)  # (the build folder itself is the owner's: in shared mode, the containers' mount)
    fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for i, part in enumerate(parts):
            try:
                st = os.stat(part, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                st = None
            if st and stat.S_ISDIR(st.st_mode) and fresh and i == len(parts) - 1:  # (what can't be removed: OSError)
                if sys.version_info >= (3, 11):
                    shutil.rmtree(part, dir_fd=fd)
                else:  # (by its path: its parents were checked just now)
                    shutil.rmtree(os.path.join(base, *parts[:i + 1]))
            elif st and not stat.S_ISDIR(st.st_mode):
                os.unlink(part, dir_fd=fd)
            with contextlib.suppress(FileExistsError):
                os.mkdir(part, 0o755, dir_fd=fd)
            fd, parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0), dir_fd=fd), fd
            os.close(parent)
    finally:
        os.close(fd)
    return os.path.join(base, *parts)


def copy_output(src, dst):
    """Copy a file of the build's (open_output: in shared mode never through a link) to dst (create_output); whether
    there was one (if not, dst is left as it was)."""
    f = open_output(src)
    if f is None:
        return False
    with f, create_output(dst) as g:
        shutil.copyfileobj(f, g)
    return True


def read_capped(path, limit=config.READ_LIMIT, whole=False):
    """A file's text (bytes that don't decode replaced), at most its first limit bytes (with whole: None
    for a longer file), or None if there is none (open_output)."""
    f = open_output(path)
    if f is None:
        return None
    with f:
        data = f.read(limit + 1)
    if len(data) > limit:
        if whole:
            return None
        data = data[:limit]
    return data.decode("utf-8", errors="replace")


def fls_inputs(fls):
    """The INPUT lines of a recorder file (.fls) that pdflatex wrote, as given (read_capped)."""
    return [line[6:].strip() for line in (read_capped(fls) or "").splitlines()[:config.RECORD_LIMIT] if line.startswith("INPUT ")]


def pdf_path(paper_root, name):
    """Published <doc>.pdf in the paper root; derived <doc>-clean.pdf / -diff.pdf / -head.pdf /
    -head-clean.pdf / -push.pdf / -push-clean.pdf / -export.pdf / -review.pdf / -annotated.pdf in build/."""
    if re.search(r"-(clean|diff|head|push|export|review|annotated)\.pdf$", name):
        return os.path.join(paper_root, config.BUILD, name)
    return os.path.join(paper_root, config.PUBLISH, name)


def read_json(path):
    """A small JSON file of the build's (status, forward search), or None (also past JSON_LIMIT)."""
    text = read_capped(path, config.JSON_LIMIT, whole=True)
    try:
        return json.loads(text) if text is not None else None
    except (ValueError, RecursionError):
        return None


def mtime(path):
    return os.stat(path).st_mtime_ns if os.path.exists(path) else 0


def child_aux(folder, name):
    """The .aux file that \\@input{name} in an .aux in folder names, if it is in folder or below it (not
    an absolute path, nor .. out of it, nor through a link: the paper's TeX writes the name), else None."""
    path = os.path.normpath(os.path.join(folder, name))
    if os.path.isabs(name) or not within(path, os.path.normpath(folder)):
        return None
    if os.path.realpath(path) != os.path.join(os.path.realpath(folder), os.path.relpath(path, folder)):  # (a link on the way)
        return None
    return path


# Where a path is. Every check that a path is in a folder goes through within; a path relative to the paper root that
# may name a file of the paper's (no .., no dot part, not in build/) through plain_rel; an absolute path of one of the
# paper's own files through in_paper. (sources builds the editor's and the Files tab's checks on these: paper_file,
# new_path, own_file, changed_path; git the History tab's: shown_path.)
def within(path, folder):
    """Whether path is below folder (not folder itself): both absolute (or both relative to the same folder, but never
    "."), and alike, normalized or with links resolved (os.path.realpath), as the caller has them."""
    return path != folder and path.startswith(os.path.join(folder, ""))


def plain_rel(rel):
    """rel normalized, if it is a path relative to the paper root (a string, not empty, without NUL, not absolute) that
    stays in it (no .. out of it), names no dot file or folder (.git, .env), and is not in the build directory
    (in_build: in any case); else None. Says nothing of links: callers check the resolved path too."""
    if not isinstance(rel, str) or not rel or "\0" in rel or os.path.isabs(rel):
        return None
    norm = os.path.normpath(rel)
    if any(part.startswith(".") for part in norm.split(os.sep)) or in_build(norm):  # (".", ".." too)
        return None
    return norm


def in_build(rel):
    """Whether rel (relative to the paper root, normalized) is the build directory or in it, in any case (where the file
    system ignores case, Build/ is build/)."""
    folded, build = rel.casefold(), os.path.normpath(config.BUILD).casefold()
    return folded == build or folded.startswith(os.path.join(build, ""))


def in_paper(paper_root, path):
    """Whether path (absolute, normalized) is one of the paper's own files: in the paper root, and not in
    the build directory (in any case: in_build), whose files the build generates. (The .fls a build writes can name any path.)
    In shared mode, also once symbolic links are resolved: coauthors write notes into these files, so a
    link to a file elsewhere is not one (the owner's own previews keep such links, as they always did);
    and not a dot file or a file in a dot folder, which TeX doesn't read there (openin_any=p), however a
    file list that the paper's TeX forged names it."""
    if not within(path, paper_root) or in_build(os.path.relpath(path, paper_root)):
        return False
    if not config.SHARED:
        return True
    real, root = os.path.realpath(path), os.path.realpath(paper_root)
    return (within(real, root) and not within(real, os.path.realpath(os.path.join(root, config.BUILD)))
            and all(plain_rel(rel) for rel in (os.path.relpath(path, paper_root), os.path.relpath(real, root))))


def project_tex_files(paper_root, fls):
    """The project's .tex files that the build read, in reading order (not generated ones in build/)."""
    paths = [os.path.normpath(os.path.join(paper_root, os.path.normpath(p))) for p in dict.fromkeys(fls_inputs(fls)) if p.endswith(".tex")]
    return [p for p in dict.fromkeys(paths) if in_paper(paper_root, p)]


def project_inputs(paper_root, fls):
    """Project files the build read (relative paths, not generated ones in build/)."""
    paths = [os.path.normpath(p) for p in fls_inputs(fls)]
    found = []
    for p in dict.fromkeys(paths):
        full = os.path.normpath(os.path.join(paper_root, p))
        if in_paper(paper_root, full) and os.path.isfile(full):
            found.append(os.path.relpath(full, paper_root))
    return list(dict.fromkeys(found))


def file_hash(path, cache={}):
    """A short hash of a file's bytes, read again only when its stat changes (at most 1024 kept)."""
    st = os.stat(path)
    key = (path, st.st_mtime_ns, st.st_size)
    if (found := cache.get(key)) is None:
        with open(path, "rb") as f:
            found = hashlib.sha1(f.read()).hexdigest()[:16]
        if len(cache) >= 1024:
            cache.clear()
        cache[key] = found
    return found


def read_output(paper_root, path, encoding=None):
    """A file that TeX or BibTeX wrote (a log, an .aux, a .bbl), read whole for the downloads: its text (bytes
    that don't decode replaced), or None if there is none (open_output: in shared mode never through a link). Raises
    RuntimeError past OUTPUT_LIMIT."""
    f = open_output(path)
    if f is None:
        return None
    with f, io.TextIOWrapper(f, encoding=encoding, errors="replace") as text_file:
        text = text_file.read(config.OUTPUT_LIMIT + 1)
    if len(text) > config.OUTPUT_LIMIT:
        raise RuntimeError(too_big(os.path.relpath(path, paper_root), config.OUTPUT_LIMIT))
    return text


def too_big(name, limit, what=""):
    """What a download says when a file of TeX's output is past its limit (read_output, run_probe, synctex_pages)."""
    return f"{name} is over {limit // 1_000_000} MB{what}, more than the downloads read (does the paper write to it in a loop?)"


def read_build(path, limit=config.READ_LIMIT):
    """A file a TeX tool wrote in build/, as text (its first limit bytes: read_capped), or "" if there is none."""
    return read_capped(path, limit) or ""


def aux_tree(aux, folder=None):
    """The text of a job's .aux (aux) and of the .aux files it reads in build/ (\\@input, as \\include
    writes one per chapter), as bibtex reads them: at most 200 files, AUX_BUDGET bytes in all, none
    outside build/ (child_aux), or outside folder if given."""
    texts, todo, seen, budget = [aux], [aux], set(), config.AUX_BUDGET - len(aux)
    while todo and len(seen) < 200 and budget > 0:
        for name in re.findall(r"^\\@input\{([^{}]+)\}", todo.pop(), re.M):
            path = child_aux(folder or config.BUILD, name)
            if not path or path in seen or len(seen) >= 200 or budget <= 0:
                continue
            seen.add(path)
            texts.append(text := read_build(path, min(config.READ_LIMIT, budget)))
            budget -= len(text)
            todo.append(text)
    return "\n".join(texts)


def redactor(paper_root):
    """A function that gives a text with the paper's folder (as given, and once links are resolved) as ".", so that
    what the server shows (the build's log, the reason a build failed) names the paper's files relative to it, never the
    server's folders."""
    roots = sorted({os.path.abspath(paper_root), os.path.realpath(paper_root)} - {os.sep}, key=len, reverse=True)
    if not roots:  # (a paper at /: nothing to hide)
        return lambda text: text
    pattern = re.compile("(?:" + "|".join(map(re.escape, roots)) + r")(/|(?![\w~-]|\.[\w~-]))")
    return lambda text: pattern.sub(lambda m: "./" if m[1] else ".", text)


def redact_root(text, paper_root):
    """text with the paper's folder as "." (redactor)."""
    return redactor(paper_root)(text)
