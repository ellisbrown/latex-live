"""What a shared build depends on, for the builder to watch (shared_inputs), and the guard on the
size of build/, which the paper's TeX fills."""

import html
import os
import re
import shutil
import time

from . import config
from .files import fls_file, in_paper, job_file, project_inputs, project_tex_files
from .latex import INPUT_RE, read_tex, source_tree
from .sources import editable_files
from .tools import stop_containers


def shared_inputs(server):
    """The files a build of the shared document depends on: those the browser editor opens (the main
    file, what the last build read, the .bib files), the paper's other files the last good build and the
    last attempt read (figures, styles), the bibliographies and styles the .aux or .bcf name (at any depth:
    shared_deps), and the files a build missed (shared_missing), as {real path: their stat (mtime, size,
    inode), or None while there is none}."""
    root = server.paper_root
    paths = set(editable_files(server).values()) | set(server.shared["missing"]) | set(server.shared["deps"])
    for fls in (fls_file(root, server.default_pdf), job_file(root, server.default_pdf, ".fls")):
        paths |= {os.path.realpath(os.path.join(root, rel)) for rel in project_inputs(root, fls)}
    found = {}
    for path in paths:
        try:
            st = os.stat(path)
            found[path] = (st.st_mtime_ns, st.st_size, st.st_ino)
        except OSError:
            found[path] = None
    return found


MISSING_RE = re.compile(r"File `([^`'\n]{1,200})' not found")
MISSING_EXTS = ("", ".tex", ".sty", ".cls", ".bib", ".pdf", ".png", ".jpg", ".jpeg", ".eps")  # (as TeX and graphicx add them)


def paper_file(root, name, ext=""):
    """The real path of a file of the paper's that a build names (name, plus ext if it has no extension),
    or None if that is not one (in_paper: the paper's TeX writes these names)."""
    name = name.strip()
    if not name or "\0" in name:
        return None
    path = os.path.normpath(os.path.join(root, name + ("" if not ext or os.path.splitext(name)[1] else ext)))
    return os.path.realpath(path) if in_paper(root, path) else None


def shared_missing(server, log):
    """The paper's files that a build could not find, for shared_inputs to watch for them to appear: those
    its log names (LaTeX's "File `x' not found", with the extensions TeX and graphicx would try), and the
    \\input and \\include targets of the paper's .tex files that don't exist yet (an \\include of a missing
    file is no error)."""
    root, found = server.paper_root, []
    for name in dict.fromkeys(MISSING_RE.findall(log[-(1 << 20):])):
        found += [path for ext in (MISSING_EXTS if not os.path.splitext(name)[1] else ("",))
                  if (path := paper_file(root, name + ext))]
    texts = set(project_tex_files(root, fls_file(root, server.default_pdf))) | source_tree(root, server.default_pdf[:-4])
    for path in list(texts)[:500]:
        found += [target for m in INPUT_RE.finditer(read_tex(path)) if (target := paper_file(root, m.group(1), ".tex"))]
    return [path for path in dict.fromkeys(found) if not os.path.exists(path)][:400]


def shared_deps(server, auxes, bcf):
    """The bibliography files and styles that a build's .aux files (auxes: aux_tree's) and .bcf name, as real
    paths in the paper (those that exist and those that don't: TeX Live's own styles are elsewhere), for
    shared_inputs to watch: pdflatex's file list has none of them, and they can be in any folder."""
    root, found = server.paper_root, []
    for names in re.findall(r"^\\bibdata\{(.*)\}$", auxes, re.M)[:100]:
        found += [path for name in names.split(",")[:100] if (path := paper_file(root, name, ".bib"))]
    for name in re.findall(r"^\\bibstyle\{(.*)\}$", auxes, re.M)[:10]:
        found += [path] if (path := paper_file(root, name, ".bst")) else []
    for name in re.findall(r"<bcf:datasource[^>]*>([^<]+)</bcf:datasource>", bcf)[:100]:
        found += [path] if (path := paper_file(root, html.unescape(name))) else []
    return list(dict.fromkeys(found))[:300]


def build_size(server, most=200_000):
    """The disk space build/ takes (bytes), or None past most files and folders (too many to add up)."""
    total, seen = 0, 0
    for folder, dirs, names in os.walk(os.path.join(server.paper_root, config.BUILD)):
        seen += len(dirs) + len(names)
        if seen > most:
            return None
        for name in names:
            try:
                total += os.lstat(os.path.join(folder, name)).st_blocks * 512
            except OSError:
                pass
    return total


def own_lock(server, path):
    """Whether path is the lock file this instance holds (the same file as the one it has open)."""
    try:
        return os.path.samestat(os.lstat(path), os.fstat(server.lock.fileno()))
    except (OSError, AttributeError, ValueError):
        return False


def watch_build_size(server):
    """Shared mode, from the start (with --no-build too: the downloads, baselines and SyncTeX write there):
    the paper's TeX decides what it writes into build/ (--ulimit fsize bounds each file, not how many), so
    every second this adds up build/, and past LATEX_LIVE_BUILD_LIMIT (or 200 000 entries) it stops every
    container of this instance and empties build/ but for the lock file the instance holds (and a small
    latex-live-doc), which the next build fills again; the run it stopped says why (shared_run), and is tried again a few times, further
    apart each time."""
    root = os.path.join(server.paper_root, config.BUILD)
    while not server.stopping:
        size = build_size(server)
        if size is None or size > config.BUILD_LIMIT:
            server.shared["overflow"] = time.time_ns()
            stop_containers()
            print(f"=== latex-live: {config.BUILD}/ took more than {config.BUILD_LIMIT >> 20} MB (LATEX_LIVE_BUILD_LIMIT); stopped the"
                  " build and emptied it", flush=True)
            for name in os.listdir(root):
                path = os.path.join(root, name)
                if own_lock(server, path) or name == "latex-live-doc" and os.path.isfile(path) and not os.path.islink(path) \
                        and os.path.getsize(path) < 4096:
                    continue
                if os.path.isdir(path) and not os.path.islink(path):
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
        time.sleep(1)
