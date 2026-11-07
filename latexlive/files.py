"""The paper's files and the build's: where each is (job_file, pdf_path...), which of them are the
paper's own (in_paper), and reading what TeX wrote within bounds (open_output, read_capped, read_output),
since the paper's TeX decides how big those files are."""

import glob
import hashlib
import json
import os
import re
import stat

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
    -head-clean.pdf / -push.pdf / -push-clean.pdf / -export.pdf / -review.pdf in build/."""
    if re.search(r"-(clean|diff|head|push|export|review)\.pdf$", name):
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
    if os.path.isabs(name) or not path.startswith(os.path.join(os.path.normpath(folder), "")):
        return None
    if os.path.realpath(path) != os.path.join(os.path.realpath(folder), os.path.relpath(path, folder)):  # (a link on the way)
        return None
    return path


def in_paper(paper_root, path):
    """Whether path (absolute, normalized) is one of the paper's own files: in the paper root, and not in
    the build directory, whose files the build generates. (The .fls a build writes can name any path.)
    In shared mode, also once symbolic links are resolved: coauthors write notes into these files, so a
    link to a file elsewhere is not one (the owner's own previews keep such links, as they always did);
    and not a dot file or a file in a dot folder, which TeX doesn't read there (openin_any=p), however a
    file list that the paper's TeX forged names it."""
    if not path.startswith(os.path.join(paper_root, "")) or path.startswith(os.path.join(paper_root, config.BUILD, "")):
        return False
    if not config.SHARED:
        return True
    real, root = os.path.realpath(path), os.path.realpath(paper_root)
    dotted = any(part.startswith(".") for rel in (os.path.relpath(path, paper_root), os.path.relpath(real, root))
                 for part in rel.split(os.sep))
    return (real.startswith(os.path.join(root, "")) and not real.startswith(os.path.join(os.path.realpath(os.path.join(root, config.BUILD)), ""))
            and not dotted)


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
    st = os.stat(path)
    key = (path, st.st_mtime_ns, st.st_size)
    if key not in cache:
        with open(path, "rb") as f:
            cache[key] = hashlib.sha1(f.read()).hexdigest()[:16]
    return cache[key]


def read_output(paper_root, path, encoding=None):
    """A file that TeX or BibTeX wrote (a log, an .aux, a .bbl), read whole for the downloads: its text (bytes
    that don't decode replaced), or None if there is none. Raises RuntimeError past OUTPUT_LIMIT."""
    try:
        with open(path, encoding=encoding, errors="replace") as f:
            text = f.read(config.OUTPUT_LIMIT + 1)
    except OSError:
        return None
    if len(text) > config.OUTPUT_LIMIT:
        raise RuntimeError(too_big(os.path.relpath(path, paper_root), config.OUTPUT_LIMIT))
    return text


def too_big(name, limit, what=""):
    """What a download says when a file of TeX's output is past its limit (read_output, run_probe, synctex_pages)."""
    return f"{name} is over {limit >> 20} MB{what}, more than the downloads read (does the paper write to it in a loop?)"


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
