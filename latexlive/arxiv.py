"""What an arXiv upload would publish, since arXiv publishes the source: comments, notes, hidden
blocks, private terms, personal paths, and files the build doesn't need (arxiv_source); and the
package that the viewer makes (POST /arxiv: make_package), without the comments, notes and hidden
blocks, and checked by building it alone."""

import bisect
import collections
import hashlib
import io
import os
import re
import shutil
import sys
import tarfile
import tempfile
import threading
import time
import zipfile

from . import config
from .export import exact_export, waiting_for_build
from .files import fls_file, mtime, open_output, pdf_path, project_inputs, project_tex_files, read_output
from .latex import DOCCLASS_RE, RERUN_RE, brace_group, false_blocks, read_tex, root_docs, source_tree
from .notes import note_kinds, note_use_re
from .standby import DATE_RES
from .tools import failure, pdf_pages_text, pdf_tool, run_tool, tex_argv


ARCHIVE_EXTS = (".tar.gz", ".tgz", ".zip")
COMMENT_RE = re.compile(r"(?<!\\)(?:\\\\)*%(.*)")  # (also right after a \\ line break)
PARAM_RE = re.compile(r"(?<!\\)(?:\\\\)*#")  # (a macro parameter, as in a definition's body: not \#)
PERSONAL_PATH_RE = re.compile(r"(?<![\w.~])/(?:home|Users|checkpoint)/[^\s{}]*|(?<![\w~])~/[^\s{}]*"
                              r"|(?<![\w\\])[A-Za-z]:[\\/](?:[\w .-]+[\\/])+[^\s{}]*|\b(?i:file):/[^\s{}]*")


LEFTOVER_RE = re.compile(r"\.(?:aux|log|out|toc|lof|lot|fls|fdb_latexmk|synctex(?:\.gz)?|blg|bcf|run\.xml|brf|nav|snm|vrb|xdv|dvi)$"
                         r"|(?:^|/)(?:\.DS_Store|\._[^/]*|__MACOSX/.*|\.git/.*)$")


def arxiv_package(paper_root, doc=None):
    """The paper root's newest arXiv package, a .tar.gz, .tgz or .zip file or a directory with
    "arxiv" in its name (a directory with a root document, so not, e.g., sections-arxiv/), or the
    one the viewer made for doc (build/<doc>-arxiv.tar.gz: make_package): (mtime, is archive, path),
    or None. A directory's mtime is its newest file's; on a tie the archive wins (it is what gets
    uploaded)."""
    made = os.path.join(paper_root, config.BUILD, f"{doc}-arxiv.tar.gz")
    found = [(os.path.getmtime(made), True, made)] if doc and os.path.isfile(made) else []
    for name in os.listdir(paper_root):
        path = os.path.join(paper_root, name)
        if "arxiv" not in name.lower():
            continue
        if os.path.isdir(path) and root_docs(path):
            times = [os.path.getmtime(os.path.join(d, f)) for d, _, files in os.walk(path) for f in files]
            if times:
                found.append((max(times), False, path))
        elif name.lower().endswith(ARCHIVE_EXTS):
            found.append((os.path.getmtime(path), True, path))
    return max(found, default=None)


def file_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def package_files(path, limit=50e6):
    """[(name, size, bytes)] for the files in an archive or directory (bytes None above limit)."""
    if os.path.isdir(path):
        files = []
        for d, dirs, names in os.walk(path):
            dirs.sort()
            for n in sorted(names):
                p = os.path.join(d, n)
                size = os.path.getsize(p)
                files.append((os.path.relpath(p, path), size, file_bytes(p) if size <= limit else None))
        return files
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            return [(os.path.normpath(i.filename), i.file_size, z.read(i) if i.file_size <= limit else None)
                    for i in z.infolist() if not i.is_dir()]
    with tarfile.open(path) as t:
        return [(os.path.normpath(m.name), m.size, t.extractfile(m).read() if m.size <= limit else None) for m in t if m.isfile()]


def pdf_strings(data, cache={}):
    """A PDF's metadata (pdfinfo) and text (pdftotext), from its bytes; "" without poppler."""
    key = hashlib.sha1(data).digest()
    if key not in cache:
        out = []
        with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
            f.write(data)
            f.flush()
            for cmd in (["pdfinfo", f.name], ["pdftotext", "-q", f.name, "-"]):
                out.append(pdf_tool(cmd, timeout=30))
        cache[key] = "\n".join(out)
    return cache[key]


def ago(t):
    return f"{t / 86400:.0f} d" if t >= 86400 else f"{t / 3600:.0f} h" if t >= 3600 else f"{t / 60:.0f} min"


def excerpt(text, n=60):
    text = " ".join(text.split())
    return text[:n] + "…" if len(text) > n else text


def arxiv_source(paper_root, pdf_name):
    """What an arXiv upload would publish, since arXiv publishes the source: (level, detail, items).

    Reads the newest arXiv package (arxiv_package: the paper root's, or the one the viewer made), else
    the project files the build reads (what a naive upload of the sources would expose). Flags % comments, note macros (published
    even when hidden), \\iffalse blocks and comment environments, LATEX_LIVE_PRIVATE_TERMS (a regular
    expression for codenames and other internal names), personal paths, files the build doesn't
    read, large files, leftover build files, and a missing .bbl. An item in a package file gives
    package-path:line, and opens the project source when the file is the same there, or the line
    is found in the build's sources (e.g. in a package that inlines every \\input).
    """
    doc = pdf_name[:-4]
    fls = fls_file(paper_root, pdf_name)
    package = arxiv_package(paper_root, doc)
    if package:
        label = os.path.basename(package[2]) + ("/" if not package[1] else "")
        try:
            files = package_files(package[2])
        except (OSError, EOFError, tarfile.TarError, zipfile.BadZipFile) as e:
            return "warn", f"{label}: can't read it ({e})", []
    else:
        files = [(p, os.path.getsize(os.path.join(paper_root, p)), file_bytes(os.path.join(paper_root, p)))
                 for p in project_inputs(paper_root, fls)]
        if not files:
            return "info", "no arXiv package, and no build yet to list the sources", []
    data = {name: d for name, _, d in files}
    texts = {name: d.decode("utf-8", errors="replace") for name, d in data.items()
             if d is not None and not name.lower().endswith(".pdf") and b"\0" not in d[:8192]}
    # Comments blanked at the same length, so positions carry over.
    blanked = {name: COMMENT_RE.sub(lambda m: re.sub(r"[^\\]", " ", m.group(0)), t) for name, t in texts.items()}
    tex = [name for name in texts if name.endswith(".tex")]
    mains = [name for name in tex if "/" not in name and DOCCLASS_RE.search(blanked[name])]
    # The project sources behind the package: this build's, or those of the package's own root file.
    main = mains[0][:-4] if len(mains) == 1 else doc
    same_doc = main == doc
    sources = project_tex_files(paper_root, fls) if same_doc else sorted(source_tree(paper_root, main))
    if package:
        same = {name for name in tex if os.path.isfile(os.path.join(paper_root, name))
                and file_bytes(os.path.join(paper_root, name)) == data[name]}
        lines_at = {}
        for path in sources:
            with open(path, errors="replace") as f:
                for n, line in enumerate(f, 1):
                    lines_at.setdefault(line.strip(), (os.path.relpath(path, paper_root), n))
    else:
        same, lines_at = set(texts), {}

    def item(what, name, pos=None):
        """A check item for something in a file, at pos in its text: package-path:line, and the
        project file and line to open when known."""
        if pos is None:
            return {"text": f"{name}: {what}"}
        text = texts[name]
        n = text.count("\n", 0, pos) + 1
        line = text[text.rfind("\n", 0, pos) + 1:].split("\n", 1)[0].strip()
        loc = (name, n) if name in same else lines_at.get(line) if len(line) >= 20 else None
        return {"text": f"{name}:{n}: {what}" if package else what, **({"file": loc[0], "line": loc[1]} if loc else {})}

    found = collections.defaultdict(list)
    terms = os.environ.get("LATEX_LIVE_PRIVATE_TERMS", "").strip()
    try:
        private = re.compile(terms) if terms else None
    except re.error as e:
        private = None
        found["config"].append({"text": f"LATEX_LIVE_PRIVATE_TERMS is not a regular expression ({e})"})

    def context(text, m):
        """The match with up to 25 characters of its line on each side."""
        start, end = text.rfind("\n", 0, m.start()) + 1, text.find("\n", m.end())
        end = len(text) if end < 0 else end
        return ("…" if m.start() - start > 25 else "") + " ".join(text[max(start, m.start() - 25):min(end, m.end() + 25)].split()) \
            + ("…" if end - m.end() > 25 else "")

    path_text = lambda m: excerpt(m.group(0).rstrip(".,;:)"))  # (without a sentence's full stop)
    for name, size, d in files:
        for m in private.finditer(name) if private else ():
            found["private"].append(item(f"private term \"{m.group(0)}\" in the file name", name))
        if name in texts:
            text = texts[name]
            for m in private.finditer(text) if private else ():
                found["private"].append(item(f"private term \"{m.group(0)}\": {context(text, m)}", name, m.start()))
            for m in PERSONAL_PATH_RE.finditer(text):
                found["path"].append(item(f"personal path: {path_text(m)}", name, m.start()))
        elif d is not None and name.lower().endswith(".pdf"):
            strings = pdf_strings(d)
            for m in private.finditer(strings) if private else ():
                found["private"].append(item(f"private term in the PDF's text or metadata: {context(strings, m)}", name))
            for m in PERSONAL_PATH_RE.finditer(strings):
                found["path"].append(item(f"personal path in the PDF's text or metadata: {path_text(m)}", name))
        if size > 10e6:
            found["large"].append(item(f"{size / 1e6:.1f} MB", name))
    total = sum(size for _, size, _ in files)
    if package and total > 50e6:
        found["large"].append({"text": f"the package holds {total / 1e6:.0f} MB of files (arXiv's limit is 50 MB)"})

    # Comments, notes, and hidden blocks in the .tex files.
    kinds = {**note_kinds(blanked[name] for name in tex), **note_kinds(read_tex(p) for p in sources), "todo": "todo"}
    comments = []
    for name in tex:
        text, blank = texts[name], blanked[name]
        comments += [(name, "% " + excerpt(m.group(1)), m.start()) for m in COMMENT_RE.finditer(text)
                     if re.search(r"\w", m.group(1))]  # (not rules like % ====)
        for m in note_use_re(kinds).finditer(blank):
            body = brace_group(blank, m.end())[0]
            if not PARAM_RE.search(body) and body.strip():  # (not a definition's body, nor a note the package emptied: strip_source)
                found["note"].append(item(f"note \\{m.group(1)}{{{excerpt(body)}}}", name, m.start()))
        hidden = [(s, e, "\\iffalse block") for s, e in false_blocks(blank)]
        hidden += [(m.start(), m.end(), "comment environment")
                   for m in re.finditer(r"\\begin\s*\{comment\}[\s\S]*?(?:\\end\s*\{comment\}|\Z)", blank)]
        for start, end, what in hidden:
            body = re.sub(r"^\\iffalse|^\\begin\s*\{comment\}|\\fi$|\\end\s*\{comment\}$", "", text[start:end])
            lines = text.count("\n", start, end) + 1
            found["iffalse"].append(item(f"{what}, {lines} line(s): {excerpt(body)}", name, start))

    if package:
        # Files the build doesn't read: not among this build's inputs, and not named in the package.
        names = set(data)
        read = set(project_inputs(paper_root, fls)) if same_doc else set()
        code = "\n".join(blanked.values())
        for name in sorted(names):
            stem = os.path.splitext(os.path.basename(name))[0]
            if LEFTOVER_RE.search(name) or name.endswith(".pdf") and name[:-4] + ".tex" in mains:
                found["leftover"].append(item("leftover build file (arXiv needs only the .bbl)", name))
            elif not (name in mains or name.endswith(".bbl") and name[:-4] + ".tex" in mains or name in read
                      or os.path.basename(name).startswith("00README")  # (arXiv's processing instructions)
                      or re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(stem), code, re.I)):
                found["unused"].append(item("not used by the build", name))
        for name in mains:
            if re.search(r"\\bibliography\s*\{|\\addbibresource\b", blanked[name]) and name[:-4] + ".bbl" not in names:
                found["bbl"].append({"text": f"{name[:-4]}.bbl is missing: arXiv doesn't run BibTeX, so the references would be empty"})
        newest = max(((os.path.getmtime(os.path.join(paper_root, p)), p) for p in project_inputs(paper_root, fls)) if same_doc
                     else ((os.path.getmtime(p), os.path.relpath(p, paper_root)) for p in sources), default=None)
        if newest and newest[0] > package[0] + 60:
            found["stale"].append({"text": f"stale: {newest[1]} changed {ago(newest[0] - package[0])} after {label} was made; remake it",
                                   **({"file": newest[1], "line": 1} if newest[1].endswith(".tex") else {})})

    # Most serious first; comments last, with a count per file first when they don't all fit.
    order = ("private", "path", "bbl", "config", "stale", "leftover", "note", "iffalse", "unused", "large")
    items = [it for kind in order for it in found[kind]]
    room = max(0, 50 - len(items))
    per_file = collections.Counter(name for name, _, _ in comments)
    if len(comments) > room and len(per_file) > 1:
        firsts = {}
        for name, what, pos in comments:
            firsts.setdefault(name, (what, pos))
        heads = [{**item("", name, firsts[name][1]), "text": f"{name}: {k} comment(s), e.g. {firsts[name][0]}"}
                 for name, k in per_file.items()]
        items += heads
        room -= len(heads)
    items += [item(what, name, pos) for name, what, pos in comments[:max(0, room)]]
    found["comment"] = comments
    counts = {"private": "private term(s)", "path": "personal path(s)", "bbl": "missing .bbl", "comment": "comment(s)",
              "note": "note(s)", "iffalse": "hidden block(s)", "unused": "unused file(s)", "large": "large file(s)",
              "leftover": "leftover build file(s)"}
    where = (f"{label}{' (stale)' if found['stale'] else ''}, {len(files)} files, {total / 1e6:.1f} MB" if package
             else f"no arXiv package; what a naive upload of the sources would expose ({len(files)} files)")
    summary = ", ".join(f"{len(found[k])} {what}" for k, what in counts.items() if found[k])
    level = ("fail" if found["private"] or found["path"] or found["bbl"] else
             "warn" if any(found[k] for k in ("config", "stale", "leftover", "note", "iffalse", "unused", "large", "comment")) else "ok")
    if not package and level != "ok":  # (no upload yet: a preview)
        level = "warn" if level == "fail" else "info"
    return level, f"{where}: {summary or 'no comments, notes, hidden blocks, private terms or personal paths'}", items


# The arXiv package (POST /arxiv): the files the exact export read, without the sources' comments and
# notes, built on its own the way arXiv builds it, and compared with the export.

TEX_SOURCE_EXTS = (".tex", ".tikz")  # (hand-written TeX: not a class, a style file, a .bbl or a PostScript figure, whose % lines mean something)
VERBATIM_ENVS = ("verbatim", "verbatim*", "Verbatim", "Verbatim*", "BVerbatim", "LVerbatim", "lstlisting", "minted",
                 "alltt", "filecontents", "filecontents*")
VERBATIM_ARGS = ("url", "href", "path")  # (commands whose first argument TeX reads verbatim: a % there is text)
CONTROL_RE = re.compile(r"\\([A-Za-z]+|.)", re.S)
OWN_COMMENT_ENV_RE = re.compile(r"\\(?:re)?newenvironment\s*\{comment\}|\\(?:New|Renew|Declare)DocumentEnvironment\s*\{comment\}"
                                r"|\\includecomment\s*\{comment\}")
PACKAGE_LIMIT = 200e6  # (bytes of files read into the package; arXiv takes 50 MB)


def scan_line(line):
    """Where a line's % comment starts, or None: the first % neither escaped (\\%; \\\\% is a line break,
    then a comment) nor in a verbatim argument; and the spans of those arguments before it (\\verb|...|,
    \\url{...}, \\href's first, \\path{...})."""
    i, spans = 0, []
    while i < len(line):
        if line[i] == "%":
            return i, spans
        if line[i] != "\\":
            i += 1
            continue
        m = CONTROL_RE.match(line, i)
        if not m:  # (a backslash at the end of the line)
            break
        i = m.end()
        if m.group(1) == "verb":
            i += line.startswith("*", i)
            close = line.find(line[i], i + 1) if i < len(line) else -1
            i = len(line) if close < 0 else close + 1
            spans.append((m.start(), i))
        elif m.group(1) in VERBATIM_ARGS:
            j = len(line) - len(line[i:].lstrip(" \t"))
            if line.startswith("{", j):
                i = brace_group(line, j)[1]
                spans.append((m.start(), i))
    return None, spans


def strip_source(text, kinds=(), comment_env=True):
    """A .tex file's text as the arXiv package holds it, and what came out: (text, {comments, hidden,
    notes}). Each change leaves what TeX typesets as it was:

    - A line that is only a comment goes whole (its % swallowed the line's end, so the lines on either
      side join as before); a comment after code keeps its %, which still swallows the line's end, and
      loses its text. A bare % stays.
    - An \\iffalse ... \\fi block, and a comment environment (unless the sources define their own or
      include it: comment_env False), go whole when they take whole lines.
    - The notes (uses of the note macros in kinds, hidden in the package: NOTES_OFF) keep their
      macro and lose their text: \\eb{} (a note is no longer in the PDF, but its text would be in the source).

    What TeX reads verbatim (the environments of VERBATIM_ENVS, \\verb, \\url's argument) stays as it is.
    The package's own build, compared with the export, checks the whole (build_arxiv)."""
    lines = text.splitlines(keepends=True)
    kinds_of = []  # per line: "keep", "drop", or (code, comment text or None)
    verbatim, in_comment, found = None, False, collections.Counter()
    for line in lines:
        body = line.rstrip("\r\n")
        if verbatim:
            verbatim = None if f"\\end{{{verbatim}}}" in body else verbatim
            kinds_of.append("keep")
            continue
        if in_comment:
            in_comment = not re.search(r"\\end\s*\{comment\}", body)
            kinds_of.append("drop")
            continue
        cut, spans = scan_line(body)
        code = body if cut is None else body[:cut]
        opens = min(((code.find(f"\\begin{{{env}}}"), env) for env in VERBATIM_ENVS if f"\\begin{{{env}}}" in code), default=None)
        if opens:  # (the rest of the line is the environment's body)
            rest = body[opens[0]:]
            verbatim = None if f"\\end{{{opens[1]}}}" in rest[len(opens[1]) + 8:] else opens[1]
            kinds_of.append("keep")
        elif comment_env and re.match(r"\s*\\begin\s*\{comment\}", code):
            in_comment = not re.search(r"\\end\s*\{comment\}", code)
            found["hidden"] += 1
            kinds_of.append("drop")
        else:
            kinds_of.append((code, None if cut is None else body[cut + 1:], spans))
    # \iffalse blocks, in the code without its comments: each that starts and ends a line goes whole.
    starts, code_text = [], ""
    for kind in kinds_of:
        starts.append(len(code_text))
        code_text += (kind[0] if isinstance(kind, tuple) else "") + "\n"
    starts.append(len(code_text))
    for start, end in false_blocks(code_text):
        first, last = bisect.bisect_right(starts, start) - 1, bisect.bisect_right(starts, end - 1) - 1
        if (code_text[end - 3:end] == "\\fi" and not code_text[starts[first]:start].strip()
                and not code_text[end:starts[last + 1]].strip()):
            kinds_of[first:last + 1] = ["drop"] * (last + 1 - first)
            found["hidden"] += 1
    out, verbatim_spans, at = [], [], 0
    for line, kind in zip(lines, kinds_of):
        if kind == "keep":
            verbatim_spans.append((at, at + len(line)))
        elif kind == "drop":
            line = ""
        else:
            if kind[1] is not None and kind[1].strip():  # (not a bare %)
                found["comments"] += 1
                line = kind[0] + "%" + line[len(line.rstrip("\r\n")):] if kind[0].strip() else ""
            verbatim_spans += [(at + s, at + e) for s, e in kind[2]]  # (\verb|\eb{...}| shows a note's macro: it stays)
        out.append(line)
        at += len(line)
    text = "".join(out)
    for m in reversed(list(note_use_re(kinds).finditer(text)) if kinds else []):
        body, end = brace_group(text, m.end())
        if body.strip() and not PARAM_RE.search(body) and not any(s <= m.start() < e for s, e in verbatim_spans):
            text = text[:m.end()] + "{}" + text[end:]
            found["notes"] += 1
    return text, found


def start_arxiv(server):
    """POST /arxiv: the arXiv package of the current build (build_arxiv), for downloading as
    build/<doc>-arxiv.tar.gz (GET /arxiv.tar.gz). Built once per build of the document; until then,
    its state. The owner's in shared mode (least_role): it holds every source file the build reads."""
    pdf = server.default_pdf
    if not os.path.isfile(pdf_path(server.paper_root, pdf)):
        return 409, {"state": "failed", "msg": f"{pdf} has not built yet"}
    with server.download_lock:  # (from however many viewers at once: one build)
        a = server.arxiv
        if waiting := waiting_for_build(server, pdf, a):
            return 200, waiting
        source = str(mtime(pdf_path(server.paper_root, pdf)))
        if a.get("pdf") == pdf and (a.get("state") == "running" or a.get("source") == source):  # (a failure too, until the next build)
            return 200, a
        server.arxiv = {"pdf": pdf, "source": source, "state": "running", "step": "the exact build without notes",
                        "time": time.strftime("%H:%M:%S"), "msg": ""}
        threading.Thread(target=build_arxiv, args=(server, pdf, source), daemon=True).start()
        return 200, server.arxiv


def build_arxiv(server, pdf, source):
    """The /arxiv thread: the package (make_package), its state for the viewer."""
    def step(text):
        with server.download_lock:  # (not over a newer package's state: start_arxiv)
            if server.arxiv.get("pdf") == pdf and server.arxiv.get("source") == source:
                server.arxiv = {**server.arxiv, "step": text}

    try:
        state = {"state": "done", **make_package(server, pdf, step)}
    except Exception as e:  # (shown in the viewer)
        state = {"state": "failed", "msg": failure(server, e)}
    with server.download_lock:
        if server.arxiv.get("pdf") == pdf and server.arxiv.get("source") == source:
            server.arxiv = {**server.arxiv, **state}


def copy_output(src, dst):
    """Copy a file of the build's (open_output: in shared mode never through a link); whether there was one."""
    f = open_output(src)
    if f is None:
        return False
    with f, open(dst, "wb") as g:
        shutil.copyfileobj(f, g)
    return True


def make_package(server, pdf, step):
    """build/<doc>-arxiv.tar.gz: what an arXiv submission of the document needs, made from the exact
    export (exact_export), in build/arxiv/<doc>/. Its files are those the export's last pass read in the
    paper (its recorder file; the live build's before an export had one), with the .tex files' comments,
    hidden blocks and notes taken out (strip_source), the notes switched off where the main file starts
    (NOTES_OFF, as the export does), and the export's .bbl (arXiv doesn't run BibTeX) and .ind as
    <doc>.bbl and <doc>.ind. Then it builds alone in build/arxiv/<doc>/test/, as arXiv would (pdflatex
    until the references settle, no BibTeX, nothing from the paper's folder: in the TeX Live image, and
    in shared mode confined), and its PDF is compared with the export's, dates and IDs aside
    (standby.DATE_RES). A package that doesn't build is not published; one that builds differently
    is, and says where it differs. What the viewer says: {files, size, comments, hidden, notes, same,
    differs, version}."""
    root, doc = server.paper_root, pdf[:-4]
    out = os.path.join(root, config.BUILD, "arxiv", doc)
    job = f"{doc}-export"
    sys.path.insert(0, config.HERE)
    import latex_report

    def keep(export):  # the export's PDF, file list, and the generated files the package holds
        shutil.rmtree(out, ignore_errors=True)
        os.makedirs(os.path.join(out, "export"))
        copy_output(os.path.join(root, config.BUILD, f"{job}.pdf"), os.path.join(out, "export.pdf"))
        for ext in (".fls", ".bbl", ".ind"):
            copy_output(os.path.join(export, job + ext), os.path.join(out, "export", job + ext))

    with server.arxiv_lock:  # (one at a time: they share build/arxiv/<doc>/)
        exact_export(server, pdf, keep)
        step("the package")
        fls = os.path.join(out, "export", f"{job}.fls")
        names = project_inputs(root, fls if os.path.isfile(fls) else fls_file(root, pdf))
        files, total = {}, 0
        for name in names:
            f = open_output(os.path.join(root, name))
            if f is None:
                continue
            with f:
                files[name] = f.read(int(PACKAGE_LIMIT) + 1 - total)
            total += len(files[name])
            if total > PACKAGE_LIMIT:
                raise RuntimeError(f"the build reads more than {PACKAGE_LIMIT / 1e6:.0f} MB of files (arXiv takes 50 MB)")
        tex = {name: data.decode("utf-8", "surrogateescape") for name, data in files.items() if name.endswith(TEX_SOURCE_EXTS)}
        kinds = note_kinds(tex.values())
        comment_env = not any(OWN_COMMENT_ENV_RE.search(COMMENT_RE.sub("", t)) for t in tex.values())
        found = collections.Counter()
        for name, text in tex.items():
            text, counts = strip_source(text, kinds, comment_env)
            found.update(counts)
            if name == f"{doc}.tex" and (m := DOCCLASS_RE.search(text)) and (kinds or "showtodos" in text):
                at = m.end() - len("\\documentclass")  # (on the same line: \pdfoutput=1 stays where arXiv looks for it)
                text = text[:at] + latex_report.NOTES_OFF + text[at:]
            files[name] = text.encode("utf-8", "surrogateescape")
        for ext in (".bbl", ".ind"):
            path = os.path.join(out, "export", job + ext)
            if os.path.isfile(path):
                files[doc + ext] = file_bytes(path)
        if f"{doc}.tex" not in files:
            raise RuntimeError(f"the export's file list doesn't name {doc}.tex")
        test = os.path.join(out, "test")
        for name, data in files.items():
            os.makedirs(os.path.dirname(os.path.join(test, name)), exist_ok=True)
            with open(os.path.join(test, name), "wb") as f:
                f.write(data)
        package = os.path.join(out, f"{doc}-arxiv.tar.gz")
        with tarfile.open(package, "w:gz") as t:
            for name in sorted(files):
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(files[name]), 0o644
                info.mtime = int(os.path.getmtime(os.path.join(root, name)) if name in names else time.time())
                t.addfile(info, io.BytesIO(files[name]))

        step("the package's own build")
        env = {k: v for k, v in os.environ.items() if k not in ("TEXINPUTS", "BIBINPUTS", "BSTINPUTS")}  # (the package alone)
        argv = tex_argv(server, "pdflatex", "-interaction=batchmode", "-halt-on-error", "-file-line-error", doc)
        for passes in range(1, 7):
            code, _ = run_tool(argv, test, kind="arxiv", env=env)
            log = read_output(root, os.path.join(test, f"{doc}.log")) or ""
            if code is None:
                raise RuntimeError(f"a pass of the package's own build took over 10 minutes; see {config.BUILD}/arxiv/{doc}/test/{doc}.log")
            if code != 0:
                first = next((l for l in log.splitlines() if l.startswith("! ") or re.match(r"^\S+:\d+: ", l)), "")
                raise RuntimeError(f"the package doesn't build on its own: {first or 'pdflatex failed'}"
                                   f" (see {config.BUILD}/arxiv/{doc}/test/{doc}.log)")
            if passes >= 2 and not RERUN_RE.search(log):  # (a cold start: at least two passes, as for the citations)
                break

        step("comparing it with the PDF without notes")
        differs = pdf_difference(os.path.join(test, f"{doc}.pdf"), os.path.join(out, "export.pdf"))
        published = os.path.join(root, config.BUILD, f"{doc}-arxiv.tar.gz")
        os.replace(package, published)
        return {"files": len(files), "size": os.path.getsize(published), "comments": found["comments"], "hidden": found["hidden"],
                "notes": found["notes"], "same": differs is None, "differs": differs or "", "version": str(mtime(published))}


def masked_pdf(path):
    """A PDF's bytes (open_output) with its dates and IDs blanked (standby.DATE_RES), or None."""
    f = open_output(path)
    if f is None:
        return None
    with f:
        data = f.read()
    for date in DATE_RES:
        data = date.sub(lambda m: m.group(1) + b"0" * len(m.group(2)), data)
    return data


def pdf_difference(a, b):
    """Where PDF a differs from PDF b, dates and IDs aside, in words; None where it doesn't."""
    data = masked_pdf(a)
    if data is None:
        return "it wrote no PDF"
    if data == masked_pdf(b):
        return None
    pages_a, pages_b = pdf_pages_text(a), pdf_pages_text(b)
    if len(pages_a) != len(pages_b):
        return f"{len(pages_a) - 1} pages rather than {len(pages_b) - 1}"  # (pdftotext ends each page with \f)
    page = next((n for n, (x, y) in enumerate(zip(pages_a, pages_b), 1) if x.split() != y.split()), None)
    return f"page {page}'s text differs" if page else "the same text, laid out or drawn differently"
