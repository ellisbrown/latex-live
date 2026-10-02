#!/usr/bin/env python3
"""Post-build hook for latex-live (see latexmkrc next to this file).

Usage: latex_report.py <root> ok|fail   (run from the paper root)
       latex_report.py --notes-free <root> <doc>   (the background pass; see start_notes_free_build)

<root> is the latexmk job name (build/<root>.*). On success, atomically
publishes build/<root>.pdf to ./$LATEX_LIVE_DOC.pdf (default ./<root>.pdf) so
the viewer reloads once, on a complete file. Then prints errors and undefined
refs/citations, and overfull hboxes (as info) as
`/abs/path.tex:LINE: error|warning|info: msg` lines, which a tasks.json problem
matcher turns into Problems-panel entries. Also writes build/<root>.status.json
for the browser viewer's build-status banner.

When the sources have inline notes (macros that go through \todotxt), a
successful run also starts a background pass without them
(build/<doc>-clean.pdf), for the viewer's notes-free view and its page count as
it would be for submission. That pass records its result in
build/clean/<doc>-clean.status.json, for the viewer's banner.
"""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time

BUILD = os.environ.get("LATEX_LIVE_BUILD", "build")  # live.py --build-dir
ERROR_RE = re.compile(r"^(\S+?\.(?:tex|sty|cls|bbl)):(\d+): (.*)$")
UNDEF_RE = re.compile(r"(Reference|Citation) [`'](.+?)' on page \S+ undefined")
MULTI_RE = re.compile(r"Label [`'](.+?)' multiply defined")
COMMENT_RE = re.compile(r"(?<!\\)%.*")
BOX_RE = re.compile(r"^Overfull \\hbox \((.+?)\) (?:in paragraph|in alignment|detected) at lines? (?:\d+--)?(\d+)")
FILE_TOKEN_RE = re.compile(r"[^\s()]+")


def emit(path, line, severity, msg):
    print(f"{os.path.abspath(path)}:{line}: {severity}: {msg}")


def input_tex_files(root, doc):
    """Project .tex files that the last run actually read, from the .fls recorder file."""
    files = []
    try:
        with open(os.path.join(BUILD, f"{root}.fls")) as f:
            for line in f:
                if line.startswith("INPUT ") and line.rstrip().endswith(".tex"):
                    path = os.path.normpath(line[6:].strip())
                    if not os.path.isabs(path) and path not in files:
                        files.append(path)
    except OSError:
        files = [f"{doc}.tex"]
    return files


def locate(key, files, commands):
    """First non-comment source line where `key` appears inside one of `commands`."""
    pat = re.compile(r"\\(?:%s)\*?(?:\[[^]]*\])*\{[^}]*(?<![\w:.-])%s(?![\w:.-])" % (commands, re.escape(key)))
    for path in files:
        try:
            with open(path, errors="replace") as f:
                for n, line in enumerate(f, 1):
                    if pat.search(COMMENT_RE.sub("", line)):
                        return path, n
        except OSError:
            pass
    return None


def overfull_boxes(log):
    """(path, line, msg) for overfull hboxes in project files.

    The log names no file for these, so track TeX's file stack: it prints
    "(path" when it opens a file and ")" when it closes it (live.py sets
    max_print_line so paths are not wrapped). Box contents printed after each
    warning can hold unbalanced parentheses, so skip them. "at lines A--B":
    only B is sure to be in the current file (A is where the paragraph began).
    """
    boxes, stack, skipping = [], [], False
    for line in log.splitlines():
        if skipping:
            skipping = bool(line.strip())
            continue
        m = BOX_RE.match(line)
        if m:
            top = next((f for f in reversed(stack) if f), None)
            if top and os.path.isfile(top) and not os.path.isabs(top) and not top.startswith(BUILD + os.sep):
                boxes.append((top, int(m.group(2)), f"Overfull \\hbox ({m.group(1)})"))
            skipping = True
            continue
        for i, ch in enumerate(line):
            if ch == "(":
                tok = FILE_TOKEN_RE.match(line, i + 1)
                # A file-like token opens a file (project or TeX Live); other "(" are plain text.
                stack.append(os.path.normpath(tok.group()) if tok and "." in tok.group() else None)
            elif ch == ")" and stack:
                stack.pop()
    return boxes


def write_status(root, status, directory=BUILD):
    """Atomically write <directory>/<root>.status.json (read by live.py's /status endpoint)."""
    path = os.path.join(directory, f"{root}.status.json")
    with open(path + ".tmp", "w") as f:
        json.dump(status, f)
    os.replace(path + ".tmp", path)


# Blank the inline notes: this paper's preamble routes \todo and the per-author note macros
# through \todotxt (gated by \ifshowtodos); redefine both once the preamble has run.
NOTES_OFF = r"\AtBeginDocument{\ifdefined\todotxt\renewcommand\todotxt[1]{}\fi\ifdefined\showtodosfalse\showtodosfalse\fi}"


# Files a run writes for the next one to read. A run killed while writing one leaves it cut off,
# and every later run stops while reading it, before it can write a good one.
AUX_EXTS = (".aux", ".out", ".toc", ".lof", ".lot")


def damaged_aux(log, root):
    """Whether the run stopped while reading one of its own AUX_EXTS files."""
    m = re.search(r"^(?:! |\S+:\d+: |Runaway argument\?)", log, re.M)  # the first error
    return bool(m) and any(f"{root}{ext}" in log[max(0, m.start() - 300):m.start()] for ext in AUX_EXTS)


def rebuild_from_scratch(root):
    """Ask live.py to rebuild the main job without its AUX_EXTS files (build/<root>.retry). It removes
    them once latexmk has stopped, with its other generated files, so latexmk starts as on a first run."""
    open(os.path.join(BUILD, f"{root}.retry"), "w").close()


def has_notes(files):
    """Whether any of the sources defines notes that NOTES_OFF can blank."""
    for path in files:
        try:
            with open(path, errors="replace") as f:
                if "\\todotxt" in f.read():
                    return True
        except OSError:
            pass
    return False


def start_notes_free_build(root, doc):
    """One background pdflatex pass without notes, reusing this run's .aux/.bbl (notes_free_pass).

    Runs as job <doc>-clean in build/clean/ and then moves the PDF to build/<doc>-clean.pdf
    (pdflatex writes its PDF page by page, so the viewer must not see it early). One pass
    is enough to measure length. A newer build's pass replaces an unfinished one.
    """
    out = os.path.join(BUILD, "clean")
    job = f"{doc}-clean"
    os.makedirs(out, exist_ok=True)
    try:
        with open(os.path.join(out, "pid")) as f:
            pid = int(f.read())
        command = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout
        if "--notes-free" in command and doc in command:
            os.killpg(pid, signal.SIGTERM)
    except (OSError, ValueError):
        pass
    for ext in (".aux", ".bbl", ".out", ".toc"):
        if os.path.exists(os.path.join(BUILD, root + ext)):
            shutil.copyfile(os.path.join(BUILD, root + ext), os.path.join(out, job + ext))
    proc = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--notes-free", root, doc], start_new_session=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open(os.path.join(out, "pid"), "w") as f:
        f.write(str(proc.pid))


def tex_format(log):
    """The format a log's run used, e.g. "pdflatex 2024.6.15": it tells TeX installations apart."""
    m = re.search(r"\(preloaded format=([^)]*)\)", log[:2000])
    return m.group(1) if m else None


def notes_free_pass(root, doc):
    """The notes-free pass (start_notes_free_build): publish build/<doc>-clean.pdf, and record the
    result, with the first errors on failure, in build/clean/<doc>-clean.status.json.

    The pass reads the main build's .aux. If that came from another TeX installation (e.g. the
    host's, while the TeX Live image was missing), its package data may not load here: then the
    main document is rebuilt from scratch.
    """
    out = os.path.join(BUILD, "clean")
    job = f"{doc}-clean"
    tex = f"{os.environ.get('LATEX_LIVE_EXEC', '')} pdflatex -interaction=batchmode -halt-on-error -file-line-error -synctex=1"
    cmd = f"{tex} -output-directory={out} -jobname={job} '{NOTES_OFF}\\input{{{doc}}}' >/dev/null 2>&1"
    ran = subprocess.run(["sh", "-c", cmd]).returncode == 0
    stamp = time.strftime("%H:%M:%S")
    if ran and os.path.isfile(os.path.join(out, f"{job}.pdf")):
        os.replace(os.path.join(out, f"{job}.pdf"), os.path.join(BUILD, f"{job}.pdf"))
        return write_status(job, {"ok": True, "time": stamp}, out)

    def read(path):
        try:
            with open(path, errors="replace") as f:
                return f.read()
        except OSError:
            return ""

    log = read(os.path.join(out, f"{job}.log"))
    errors = []
    for line in log.splitlines():
        m = ERROR_RE.match(line)
        if m and not m.group(3).lstrip().startswith("==> Fatal error"):
            errors.append({"file": rel(m.group(1)), "line": int(m.group(2)), "msg": m.group(3).strip()})
    if not errors:
        bang = next((l for l in log.splitlines() if l.startswith("! ")), None)
        errors = [{"file": None, "line": None, "msg": bang[2:] if bang else f"see {out}/{job}.log"}]
    status = {"ok": False, "time": stamp, "errors": errors[:3]}
    main_format = tex_format(read(os.path.join(BUILD, f"{root}.log")))
    if main_format and tex_format(log) and main_format != tex_format(log):
        rebuild_from_scratch(root)
        status["msg"] = f"the main build used another TeX installation ({main_format}); rebuilding it"
    write_status(job, status, out)


def rel(path):
    return os.path.relpath(path) if os.path.abspath(path).startswith(os.getcwd() + os.sep) else path


def main():
    if sys.argv[1] == "--notes-free":
        return notes_free_pass(sys.argv[2], sys.argv[3])
    root, status = sys.argv[1], sys.argv[2]
    doc = os.environ.get("LATEX_LIVE_DOC") or root
    dest = os.environ.get("LATEX_LIVE_PUBLISH") or f"{doc}.pdf"  # live.py --build-dir: inside that directory
    stamp = time.strftime("%H:%M:%S")
    try:
        with open(os.path.join(BUILD, f"{root}.log"), errors="replace") as f:
            log = f.read()
    except OSError:
        log = ""

    errors = []
    for line in log.splitlines():
        m = ERROR_RE.match(line)
        # -halt-on-error appends "==> Fatal error occurred..." at the same file:line; skip it.
        if m and not m.group(3).lstrip().startswith("==> Fatal error") and m.groups() not in errors:
            errors.append(m.groups())
    for path, line, msg in errors:
        emit(path, line, "error", msg)

    # Warnings wrap at 79 columns in the log; unwrap before matching.
    flat = re.sub(r"\n(?!\n)", "", log)
    files = input_tex_files(root, doc)
    seen, undefined = set(), []
    for kind, key in UNDEF_RE.findall(flat):
        if (kind, key) in seen:
            continue
        seen.add((kind, key))
        cmds = r"[a-zA-Z]*ref|[a-zA-Z]*cite[a-zA-Z]*" if kind == "Reference" else r"[a-zA-Z]*cite[a-zA-Z]*"
        loc = locate(key, files, cmds) or (f"{doc}.tex", 1)
        emit(*loc, "warning", f"Undefined {kind.lower()} '{key}'")
        undefined.append({"kind": kind.lower(), "key": key, "file": rel(loc[0]), "line": loc[1]})
    for key in dict.fromkeys(MULTI_RE.findall(flat)):
        loc = locate(key, files, "label") or (f"{doc}.tex", 1)
        emit(*loc, "warning", f"Label '{key}' multiply defined")
    boxes = overfull_boxes(log)
    for path, line, msg in boxes:
        emit(path, line, "info", msg)

    # latexmk can report success for a run that made no PDF, e.g. "Nothing to do" after a failed run.
    ok = status == "ok" and not errors and os.path.isfile(os.path.join(BUILD, f"{root}.pdf"))
    summary = [{"file": rel(p), "line": int(n), "msg": m.strip()} for p, n, m in errors]
    if not ok and not summary:  # e.g. a TeX error without file:line, or a bibtex failure
        bang = next((l for l in log.splitlines() if l.startswith("! ")), None)
        summary = [{"file": None, "line": None, "msg": bang[2:] if bang else f"see {BUILD}/{root}.log"}]
    previous = {}
    try:
        with open(os.path.join(BUILD, f"{root}.status.json")) as f:
            previous = json.load(f)
    except (OSError, ValueError):
        pass
    write_status(root, {"ok": ok, "time": stamp, "errors": summary,
                        "warnings": len(seen) + len(set(MULTI_RE.findall(flat))), "overfull": len(boxes),
                        "undefined": undefined, "tex": tex_format(log)})

    if ok:
        # Atomic rename, not an in-place write: vscode-pdf reloads on "created"
        # as well as "changed", and never sees a half-written file.
        tmp = os.path.join(os.path.dirname(dest), f".{os.path.basename(dest)}.tmp")
        shutil.copyfile(os.path.join(BUILD, f"{root}.pdf"), tmp)
        os.replace(tmp, dest)
        print(f"=== live-reload: published {dest} at {stamp}")
        if has_notes(files):
            start_notes_free_build(root, doc)
    else:
        if not errors:  # no file:line errors parsed; show the tail of the log instead
            print("\n".join(log.splitlines()[-25:]))
        print(f"=== live-reload: BUILD FAILED at {stamp}; kept previous {dest} (full log: {BUILD}/{root}.log)")
        # The .aux this run read may be unreadable: cut off by an interrupted run, or written by
        # another TeX installation, whose packages store their data differently (e.g. the host's
        # TeX, while the TeX Live image was missing).
        other = previous.get("tex") and tex_format(log) and previous["tex"] != tex_format(log)
        if damaged_aux(log, root) or other:
            rebuild_from_scratch(root)
            why = f"was written by another TeX installation ({previous['tex']})" if other else "was cut off (by an interrupted run?)"
            print(f"=== live-reload: {BUILD}/{root}.aux {why}; rebuilding from scratch")


if __name__ == "__main__":
    main()
