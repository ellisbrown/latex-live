#!/usr/bin/env python3
"""Post-build hook for latex-live (see latexmkrc next to this file).

Usage: latex_report.py <root> ok|fail   (run from the paper root)
       latex_report.py --pass <root>   (after each successful pdflatex run; see publish)
       latex_report.py --notes-free <root> <doc>   (the background pass; see start_notes_free_build)

In shared mode live.py builds without latexmk and uses run_report, seed_notes_free and
notes_free_pass from here instead.

<root> is the latexmk job name (build/<root>.*). On success, atomically
publishes build/<root>.pdf to ./$LATEX_LIVE_DOC.pdf (default ./<root>.pdf) so
the viewer reloads once, on a complete file. Then prints errors and undefined
refs/citations, and overfull hboxes (as info) as
`/abs/path.tex:LINE: error|warning|info: msg` lines, which a tasks.json problem
matcher turns into Problems-panel entries. Also writes build/<root>.status.json
for the browser viewer's build-status banner, and after each successful pass keeps
the list of files it read (build/<root>.fls.done), from which live.py lists the
browser editor's files.

When the sources have inline notes (macros that go through \todotxt), a
successful run also starts a background pass without them
(build/<doc>-clean.pdf), for the viewer's notes-free view and its page count as
it would be for submission, from a warm standby of its own when it can
(latexlive/standby.py run_tex). That pass records its result in
build/clean/<doc>-clean.status.json, for the viewer's banner.
"""

import fcntl
import importlib.util
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time

BUILD = os.environ.get("LATEX_LIVE_BUILD", "build")  # live.py --build-dir
HERE = os.path.dirname(os.path.realpath(__file__))
ERROR_RE = re.compile(r"^(\S+?\.(?:tex|sty|cls|bbl)):(\d+): (.*)$")
UNDEF_RE = re.compile(r"(Reference|Citation) [`'](.+?)' on page \S+ undefined")
MULTI_RE = re.compile(r"Label [`'](.+?)' multiply defined")
COMMENT_RE = re.compile(r"(?<!\\)%.*")
BOX_RE = re.compile(r"^Overfull \\hbox \((.+?)\) (?:in paragraph|in alignment|detected) at lines? (?:\d+--)?(\d+)")
FILE_TOKEN_RE = re.compile(r"[^\s()]+")
LOG_LIMIT = 64 << 20  # (the most of a log read: the paper decides how much TeX writes)


def note_time(root, event, detail="", when=None):
    """With LATEX_LIVE_TIMING=1, note when a phase of job root's build happened, in build/<root>.timing, which
    live.py reads (latexlive/timing.py); not in shared mode, which notes them itself (and reads nothing back
    from build/). A notes-free pass's events name the pass ($LATEX_LIVE_CLEAN_PASS, unless detail is given), so
    that live.py can leave a superseded pass's out of the next run's line (timing.NAMING)."""
    if event.startswith("clean-") and not detail:
        detail = os.environ.get("LATEX_LIVE_CLEAN_PASS", "")
    if os.environ.get("LATEX_LIVE_TIMING") == "1" and not os.environ.get("LATEX_LIVE_USERS"):
        try:
            with open(os.path.join(BUILD, f"{root}.timing"), "a") as f:
                f.write(f"{time.time() if when is None else when:.6f} {event}{' ' + detail if detail else ''}\n")
        except OSError:
            pass


def newest_source(root):
    """(mtime, path) of the newest file in the paper that the last pass read (its .fls), or None: the save
    that started the run, for its timing."""
    newest = None
    try:
        with open(os.path.join(BUILD, f"{root}.fls"), errors="surrogateescape") as f:
            paths = {os.path.normpath(line[6:].rstrip("\n")) for line in f if line.startswith("INPUT ")}
    except OSError:
        return None
    for path in paths:
        if os.path.isabs(path) or path.startswith(BUILD + os.sep) or path.startswith(".."):
            continue
        try:
            when = os.stat(path).st_mtime
        except OSError:
            continue
        if newest is None or when > newest[0]:
            newest = (when, path)
    return newest


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


def keep_inputs(root):
    """Copy this successful pass's list of the files it read, build/<root>.fls, to build/<root>.fls.done,
    in one step. live.py lists the browser editor's files from that copy: pdflatex rewrites the .fls
    itself during every pass, so mid-build it lacks the files read later. A failed run keeps the copy
    of the last good pass, which matches the published PDF."""
    fls = os.path.join(BUILD, f"{root}.fls")
    try:
        shutil.copyfile(fls, fls + ".done.tmp")
        os.replace(fls + ".done.tmp", fls + ".done")
    except OSError:
        pass


def write_json(path, value):
    """Atomically write value to path (read by live.py's /status endpoint)."""
    with open(path + ".tmp", "w") as f:
        json.dump(value, f)
    os.replace(path + ".tmp", path)


def write_status(root, status, directory=BUILD):
    """Atomically write <directory>/<root>.status.json."""
    write_json(os.path.join(directory, f"{root}.status.json"), status)


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
    (pdflatex writes its PDF page by page, so the viewer must not see it early). One pass is
    enough to measure length, two with line numbers (notes_free_pass). A newer build's pass
    replaces an unfinished one.
    """
    out = os.path.join(BUILD, "clean")
    os.makedirs(out, exist_ok=True)
    try:
        with open(os.path.join(out, "pid")) as f:
            pid = int(f.read())
        command = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout
        if ("--notes-free" in command or "--early" in command) and doc in command:  # (an early pass kept: run_early)
            os.killpg(pid, signal.SIGTERM)
    except (OSError, ValueError):
        pass
    seed_notes_free(root, doc)
    name = f"{os.getpid()}-{time.time_ns()}"  # (the pass's events name it: note_time)
    note_time(root, "clean-start", name)
    proc = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--notes-free", root, doc], start_new_session=True,
                            env={**os.environ, "LATEX_LIVE_CLEAN_PASS": name},
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open(os.path.join(out, "pid"), "w") as f:
        f.write(str(proc.pid))


SEED_EXTS = (".aux", ".bbl", ".brf", ".out", ".toc")  # (what the notes-free pass reads of the main build; .brf: hyperref's back-references)


def seed_notes_free(root, doc, out=None):
    """Copy this run's .aux, .bbl and the like into build/clean/ (or out) for the notes-free pass to read."""
    out = out or os.path.join(BUILD, "clean")
    os.makedirs(out, exist_ok=True)
    for ext in SEED_EXTS:
        if os.path.exists(os.path.join(BUILD, root + ext)):
            shutil.copyfile(os.path.join(BUILD, root + ext), os.path.join(out, f"{doc}-clean{ext}"))


# The early notes-free pass. The notes-free pass needs the main build's .aux and the like (SEED_EXTS), which only
# the run's last pass settles, so it would start only once the run has ended, and the notes-free view would show a
# change one pass later than the view with notes. Instead, latexmkrc copies those files to build/clean-early/ before
# each pass of the main build (the files that pass reads), and starts run_early beside it, which runs the
# notes-free pass from them in build/clean/ (in place, as the normal one), and waits before it publishes anything.
# The run's last pass reads what the run ends with, so its early pass is the very pass that the end-of-run report
# would start, provided nothing else that pass reads differs: then the report keeps it (keep_early), and it
# publishes; else the report stops it, puts build/clean/ back as it was, and starts the notes-free pass as before.
EARLY = os.path.join(BUILD, "clean-early")
NAMES_LIMIT = 20000  # (the most names in the paper's folders that an early pass watches; past that, none starts)


def early_lock():
    """build/clean-early/lock, held (flock) while an early pass starts and while the report decides on it."""
    os.makedirs(EARLY, exist_ok=True)
    lock = open(os.path.join(EARLY, "lock"), "a+")
    fcntl.flock(lock, fcntl.LOCK_EX)
    return lock


def paper_names():
    """{folder: sorted names} for the paper's folders (not the build folder, nor dot folders), through links to
    folders too, each real folder once; None past NAMES_LIMIT names. A name that appears or goes there while an
    early pass runs may change what TeX finds (\\IfFileExists, say), so the pass is then not kept."""
    names, count, seen = {}, 0, set()
    for folder, dirs, files in os.walk(".", followlinks=True):
        real = os.path.realpath(folder)
        if real in seen:
            dirs[:] = []
            continue
        seen.add(real)
        rel = os.path.normpath(folder)
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and os.path.normpath(os.path.join(rel, d)) != os.path.normpath(BUILD))
        names[rel] = sorted(dirs + files)
        count += len(names[rel])
        if count > NAMES_LIMIT:
            return None
    return names


def paper_inputs(root):
    """The files in the paper (not in the build folder) that the main build's last pass read (its .fls)."""
    found = set()
    try:
        with open(os.path.join(BUILD, f"{root}.fls"), errors="surrogateescape") as f:
            for line in f:
                if line.startswith("INPUT "):
                    path = os.path.normpath(line[6:].rstrip("\n"))
                    if not os.path.isabs(path) and not path.startswith(("..", BUILD + os.sep)):
                        found.add(path)
    except OSError:
        pass
    return sorted(found)


def changed_since(path, t0):
    """Whether a file may have changed at or after t0 (ns), or is gone: its mtime or ctime is not clearly before
    it (file times come from a coarse clock, and in whole seconds on some file systems)."""
    try:
        st = os.stat(path)
    except OSError:
        return True
    margin = 50_000_000 if st.st_mtime_ns % 1_000_000_000 else 2_000_000_000
    return max(st.st_mtime_ns, st.st_ctime_ns) >= t0 - margin


def group_alive(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def stop_early(early):
    """Stop an early pass (its process group: its TeX run too), waiting until it is gone, and put build/clean/
    back as it was before the pass started (from build/clean-early/stash/). The caller holds early_lock."""
    pid = early.get("pid")
    if isinstance(pid, int) and group_alive(pid):
        for sig, wait in ((signal.SIGTERM, 10), (signal.SIGKILL, 5)):
            try:
                os.killpg(pid, sig)
            except OSError:
                pass
            end = time.monotonic() + wait
            while group_alive(pid) and time.monotonic() < end:
                time.sleep(0.01)
            if not group_alive(pid):
                break
    if early.get("label") and not early.get("texDone") and shutil.which("podman"):  # (its TeX run's container, which may outlive its podman client)
        try:
            ids = subprocess.run(["podman", "ps", "-aq", "--filter", f"label=latex-live.early={early['label']}"], capture_output=True,
                                 text=True, timeout=30).stdout.split()
            if ids:
                subprocess.run(["podman", "rm", "-f", "-t", "0", *ids], capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            pass
    if isinstance(pid, int) and os.path.isdir(os.path.join(BUILD, "standby")):
        standby_module().stop_taken(BUILD, pid)  # (the warm standby it ran from, and its container: notes_free_pass)
    out, stash = os.path.join(BUILD, "clean"), os.path.join(EARLY, "stash")
    if os.path.isdir(stash):
        shutil.rmtree(out, ignore_errors=True)
        shutil.copytree(stash, out, symlinks=True)
    for name in ("stash", early.get("seed") or "", "early.json", "names.json"):
        path = os.path.join(EARLY, name) if name else None
        if path and os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif path and os.path.exists(path):
            os.remove(path)


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def read_early():
    """The early pass's record (build/clean-early/early.json: pid, seed, started, label, state), or None."""
    early = read_json(os.path.join(EARLY, "early.json"))
    return early if isinstance(early, dict) else None


def count_early(how, why=None):
    """Record what became of an early pass (build/clean-early/outcomes.json: kept, dropped, and why the last
    was dropped), for check.py and for people."""
    path = os.path.join(EARLY, "outcomes.json")
    try:
        with open(path) as f:
            outcomes = json.load(f)
    except (OSError, ValueError):
        outcomes = {}
    outcomes[how] = outcomes.get(how, 0) + 1
    outcomes["last"] = {"how": how, "why": why, "time": time.time()}
    write_json(path, outcomes)


def run_early(root, doc, seed, began, owner):
    """The early notes-free pass beside a pass of the main build (latexmkrc; see EARLY): seed is the folder in
    build/clean-early/ with the files that pass reads (SEED_EXTS), began when latexmk began the pass (µs), owner
    latexmk's pid. Starts only when no other notes-free pass runs (one still running from an earlier build goes on,
    and the end-of-run report replaces it, as before), but stops the early pass of an earlier pass of the build,
    whose run went on (it read other files). Runs in place in build/clean/, from a copy of it as it was (to put
    back), and publishes only once the end-of-run report keeps it (keep_early); stops, putting build/clean/ back,
    if latexmk has gone."""
    if os.getpgrp() != os.getpid():  # (its own process group, its TeX run's too, which stop_early signals by its pid)
        os.setsid()
    os.environ["LATEX_LIVE_CLEAN_PASS"] = seed  # (its events name it: note_time)
    seed_dir = os.path.join(EARLY, seed)
    out = os.path.join(BUILD, "clean")
    with early_lock():
        early = read_early()
        try:
            if os.stat(os.path.join(BUILD, f"{root}.status.json")).st_mtime_ns > int(began) * 1000:
                early = "ended"  # (the run ended before this got here: nothing to do)
        except OSError:
            pass
        if early == "ended" or not has_notes(input_tex_files(root, doc)):
            shutil.rmtree(seed_dir, ignore_errors=True)
            return None
        if early and early.get("state") == "running":
            stop_early(early)  # (an earlier pass's: that pass's files changed)
        elif running_notes_free(doc):
            shutil.rmtree(seed_dir, ignore_errors=True)
            return None
        names = paper_names()
        if names is None:
            shutil.rmtree(seed_dir, ignore_errors=True)
            return None
        stash = os.path.join(EARLY, "stash")
        shutil.rmtree(stash, ignore_errors=True)
        if os.path.isdir(out):
            shutil.copytree(out, stash, symlinks=True)
        else:
            os.makedirs(stash)
        os.makedirs(out, exist_ok=True)
        for ext in SEED_EXTS:
            if os.path.exists(os.path.join(seed_dir, f"{doc}-clean{ext}")):
                shutil.copyfile(os.path.join(seed_dir, f"{doc}-clean{ext}"), os.path.join(out, f"{doc}-clean{ext}"))
        started = time.time_ns()
        label = f"{os.getpid()}-{started}"  # (its TeX run's container: stop_early)
        write_json(os.path.join(EARLY, "names.json"), names)
        write_json(os.path.join(EARLY, "early.json"), {"pid": os.getpid(), "seed": seed, "started": started, "label": label,
                                                       "state": "running"})
        with open(os.path.join(out, "pid"), "w") as f:
            f.write(str(os.getpid()))
    note_time(root, "clean-early")

    def kept():
        """Wait for the end-of-run report's word: whether this pass was kept. If latexmk has gone, or after ten
        minutes, put build/clean/ back and stop."""
        with early_lock():  # (its TeX runs have ended: no container to stop, stop_early)
            early = read_early()
            if early and early.get("pid") == os.getpid():
                write_json(os.path.join(EARLY, "early.json"), {**early, "texDone": True})
        end = time.monotonic() + 600
        while True:
            early = read_early() or {}
            if early.get("pid") != os.getpid():
                return False  # (stopped, and something else in its place: the report stops this one meanwhile)
            if early.get("state") == "kept":
                return True
            if time.monotonic() > end or not process_alive(int(owner)):
                with early_lock():
                    if (read_early() or {}).get("pid") == os.getpid():
                        stop_early({**early, "pid": None})  # (puts build/clean/ back; this process then ends)
                return False
            time.sleep(0.01)

    def publish(pdf):  # (as the normal pass does, but dated now: its TeX run may have ended a while before it was kept)
        dest = os.path.join(BUILD, f"{doc}-clean.pdf")
        os.replace(pdf, dest)
        os.utime(dest)
        return True

    notes_free_pass(root, doc, env={**os.environ, "LATEX_LIVE_EARLY_LABEL": label}, publish=publish, decide=kept)


def process_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def running_notes_free(doc):
    """Whether a notes-free pass of doc runs (build/clean/pid), as started by start_notes_free_build or kept."""
    try:
        with open(os.path.join(BUILD, "clean", "pid")) as f:
            pid = int(f.read())
        command = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout
    except (OSError, ValueError):
        return False
    return ("--notes-free" in command or "--early" in command) and doc in command


def keep_early(root, doc, drop=False):
    """The end-of-run report: keep the early notes-free pass of the run's last pass, so that it publishes, if it
    read what the pass that the report would start reads: the files of the main build it was started from are the
    ones the run ended with (SEED_EXTS), no file of the paper that the build read changed after it started, and no
    name appeared or went in the paper's folders. Returns whether it kept one; else (and with drop) stops any early
    pass, putting build/clean/ back as it was."""
    with early_lock():
        early = read_early()
        if not early or early.get("state") != "running":
            return False
        why = "the run failed, or has no notes" if drop else None
        if why is None and not process_alive(early.get("pid") or 0):
            why = "it stopped"
        if why is None:
            seed = os.path.join(EARLY, early.get("seed") or "")
            for ext in SEED_EXTS:
                ours, final = os.path.join(seed, f"{doc}-clean{ext}"), os.path.join(BUILD, f"{root}{ext}")
                if os.path.exists(ours) != os.path.exists(final) or os.path.exists(final) and not same_file(ours, final):
                    why = f"the run ended with another {ext}"
                    break
        if why is None:
            changed = next((p for p in paper_inputs(root) if changed_since(p, early["started"])), None)
            if changed:
                why = f"{changed} changed after it started"
        if why is None and paper_names() != read_json(os.path.join(EARLY, "names.json")):
            why = "a file appeared or went in the paper's folders"
        if why is None:
            early["state"] = "kept"
            write_json(os.path.join(EARLY, "early.json"), early)
            shutil.rmtree(os.path.join(EARLY, early["seed"]), ignore_errors=True)
            count_early("kept")
            note_time(root, "clean-kept", early["seed"])
            return True
        stop_early(early)
        count_early("dropped", why)
        note_time(root, "clean-dropped", early.get("seed") or "")
        return False


def same_file(a, b):
    try:
        with open(a, "rb") as f, open(b, "rb") as g:
            return f.read() == g.read()
    except OSError:
        return False


def uses_lineno(aux):
    """Whether a pass's .aux holds lineno's records. lineno (the review-mode line numbers of
    CVPR-style classes) places each number from the previous pass's .aux, so a pass seeded with
    the .aux of the build with notes puts numbers in the wrong column or at the wrong height
    wherever hiding the notes moved the text: such a document needs a second pass."""
    try:
        with open(aux, errors="replace") as f:
            return "\\@LN" in f.read()
    except OSError:
        return False


def tex_format(log):
    """The format a log's run used, e.g. "pdflatex 2024.6.15": it tells TeX installations apart."""
    m = re.search(r"\(preloaded format=([^)]*)\)", log[:2000])
    return m.group(1) if m else None


def use_standby(env=None):
    """Whether the notes-free pass runs from a warm standby of its own when it can (latexlive/standby.py run_tex): in
    live.py's build when its standbys are on (LATEX_LIVE_STANDBY_OWNER) and the notes-free pass's too
    (LATEX_LIVE_CLEAN_STANDBY=1, which live.py sets unless that setting is 0); never in shared mode."""
    env = os.environ if env is None else env
    return bool(env.get("LATEX_LIVE_STANDBY_OWNER")) and env.get("LATEX_LIVE_CLEAN_STANDBY") == "1" and not env.get("LATEX_LIVE_USERS")


def standby_module():
    """latexlive/standby.py, loaded by its path (latexlive/ on Python's path would hide modules of the standard library)."""
    if "latexlive_standby" not in sys.modules:
        spec = importlib.util.spec_from_file_location("latexlive_standby", os.path.join(HERE, "latexlive", "standby.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return sys.modules["latexlive_standby"]


def notes_free_pass(root, doc, env=None, out=None, publish=None, decide=None):
    """The notes-free pass (start_notes_free_build): publish build/<doc>-clean.pdf, and record the
    result, with the first errors on failure, in build/clean/<doc>-clean.status.json; also returns it.
    env: the TeX runs' environment (default: this process's), LATEX_LIVE_EXEC included; each run stops
    after 10 minutes (then the status has "timeout"). out: the folder it builds in (default
    build/clean/); publish(pdf): instead of moving the PDF to build/, what publishes it, returning
    whether it did (live.py's shared mode: only for the newest pass). decide(): called once the TeX runs
    have ended, whether to go on and record the result (an early pass, run_early: whether it was kept);
    if not, nothing is published or recorded, and None returned. In build/clean/, each TeX run takes a
    warm standby when it can (use_standby), and is otherwise run as before.

    The pass reads the main build's .aux. If that came from another TeX installation (e.g. the
    host's, while the TeX Live image was missing), its package data may not load here: then the
    main document is rebuilt from scratch.
    """
    standby = out is None and use_standby(env)
    out = out or os.path.join(BUILD, "clean")
    job = f"{doc}-clean"
    env = dict(os.environ if env is None else env, LATEX_LIVE_TIMEOUT="600")
    # (With standbys: -recorder, which they need, and the root's name with .tex, which LaTeX adds anyway; given a name
    # without it, LaTeX compares the sizes of the files it finds with and without it, which a standby's copy of the
    # preamble fails.)
    tex = ["pdflatex", "-interaction=batchmode", "-halt-on-error", "-file-line-error", "-synctex=1",
           *(["-recorder"] if standby else []), f"-output-directory={out}", f"-jobname={job}",
           f"{NOTES_OFF}\\input{{{doc}{'.tex' if standby else ''}}}"]
    argv = [*shlex.split(env.get("LATEX_LIVE_EXEC", "")), *tex]

    def run():
        if standby:  # (from a warm standby of its own when it can: latexlive/standby.py run_tex)
            module = standby_module()
            try:
                code = module.run_tex(tex, env, timeout=600, timing=(os.path.join(BUILD, f"{root}.timing"), "clean-",
                                                                     env.get("LATEX_LIVE_CLEAN_PASS", "")))
            except module.TimedOut:
                return None
            if code is not None:
                return code == 0
        try:
            return subprocess.run(argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  timeout=600).returncode == 0
        except subprocess.TimeoutExpired:
            return None

    ran = run()
    note_time(root, "clean-tex")
    if ran and uses_lineno(os.path.join(out, f"{job}.aux")):  # (a second pass reads the first's .aux)
        ran = run()
        note_time(root, "clean-tex")
    if decide is not None and not decide():
        return None
    stamp = time.strftime("%H:%M:%S")
    if ran is None:
        return {"ok": False, "time": stamp, "timeout": True,
                "errors": [{"file": None, "line": None, "msg": "the pass without notes took more than 10 minutes"}]}
    if ran and os.path.isfile(os.path.join(out, f"{job}.pdf")):
        if publish is None:
            os.replace(os.path.join(out, f"{job}.pdf"), os.path.join(BUILD, f"{job}.pdf"))
        elif not publish(os.path.join(out, f"{job}.pdf")):
            return {"ok": False, "time": stamp, "superseded": True, "errors": []}
        write_status(job, {"ok": True, "time": stamp}, out)
        note_time(root, "clean-published")
        return {"ok": True, "time": stamp}

    def read(path):
        try:
            with open(path, errors="replace") as f:
                return f.read(LOG_LIMIT)
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
    note_time(root, "clean-failed")
    return status


def rel(path):
    return os.path.relpath(path) if os.path.abspath(path).startswith(os.getcwd() + os.sep) else path


def publish(root, dest):
    """Copy build/<root>.pdf to dest, unless it is the copy published last. latexmkrc runs this
    after every successful pdflatex pass too, so the viewer shows the first pass at once when
    latexmk runs another (bibtex, changed references) instead of waiting for both; the later
    pass replaces it."""
    src = os.stat(os.path.join(BUILD, f"{root}.pdf"))
    record = os.path.join(BUILD, f"{root}.published.json")
    try:
        with open(record) as f:
            last = json.load(f)
        st = os.stat(dest)
        if last.get("src") == [src.st_mtime_ns, src.st_size] and [st.st_mtime_ns, st.st_size] == [last.get("mtime"), last.get("size")]:
            return False
    except (OSError, ValueError, AttributeError):
        pass
    # Atomic rename, not an in-place write: vscode-pdf reloads on "created"
    # as well as "changed", and never sees a half-written file.
    tmp = os.path.join(os.path.dirname(dest), f".{os.path.basename(dest)}.tmp")
    shutil.copyfile(os.path.join(BUILD, f"{root}.pdf"), tmp)
    os.replace(tmp, dest)
    st = os.stat(dest)  # so live.py can tell when another build (e.g. make) replaces it
    write_json(record, {"mtime": st.st_mtime_ns, "size": st.st_size, "src": [src.st_mtime_ns, src.st_size]})
    return True


def publish_dest(root):
    doc = os.environ.get("LATEX_LIVE_DOC") or root
    return doc, os.environ.get("LATEX_LIVE_PUBLISH") or f"{doc}.pdf"  # live.py --build-dir: inside that directory


def main():
    if sys.argv[1] == "--notes-free":
        return notes_free_pass(sys.argv[2], sys.argv[3])
    if sys.argv[1] == "--early":
        return run_early(*sys.argv[2:7])
    if sys.argv[1] == "--pass":
        root = sys.argv[2]
        note_time(root, "report")
        try:
            if os.path.isfile(os.path.join(BUILD, f"{root}.pdf")):
                keep_inputs(root)
                if publish(root, publish_dest(root)[1]):
                    note_time(root, "published")
        except OSError:
            pass  # the end-of-run report publishes it anyway
        return
    root, status = sys.argv[1], sys.argv[2]
    note_time(root, "end-report", status)
    if os.environ.get("LATEX_LIVE_TIMING") == "1" and (newest := newest_source(root)):
        note_time(root, "saved", newest[1], when=newest[0])
    doc, dest = publish_dest(root)
    stamp = time.strftime("%H:%M:%S")
    ok, report, log, files = run_report(root, status)
    previous = {}
    try:
        with open(os.path.join(BUILD, f"{root}.status.json")) as f:
            previous = json.load(f)
    except (OSError, ValueError):
        pass
    if ok:  # (before the status, which ends "building": live.py's downloads then use this PDF)
        keep_inputs(root)
        if publish(root, dest):
            note_time(root, "published")
    write_status(root, report)
    note_time(root, "status")

    if ok:
        print(f"=== live-reload: published {dest} at {stamp}")
        if has_notes(files):
            if not keep_early(root, doc):
                start_notes_free_build(root, doc)
        else:
            keep_early(root, doc, drop=True)
    else:
        if not report["errors"][0]["file"]:  # no file:line errors parsed; show the tail of the log instead
            print("\n".join(log.splitlines()[-25:]))
        print(f"=== live-reload: BUILD FAILED at {stamp}; kept previous {dest} (full log: {BUILD}/{root}.log)")
        # The .aux this run read may be unreadable: cut off by an interrupted run, or written by
        # another TeX installation, whose packages store their data differently (e.g. the host's
        # TeX, while the TeX Live image was missing).
        other = previous.get("tex") and report["tex"] and previous["tex"] != report["tex"]
        if damaged_aux(log, root) or other:
            rebuild_from_scratch(root)
            why = f"was written by another TeX installation ({previous['tex']})" if other else "was cut off (by an interrupted run?)"
            print(f"=== live-reload: {BUILD}/{root}.aux {why}; rebuilding from scratch")
        keep_early(root, doc, drop=True)


def run_report(root, status, files=None):
    """The end-of-run report of job root (status: latexmk's "ok" or "fail"): prints its errors,
    undefined references and overfull boxes as file:line diagnostics, and returns (whether it
    succeeded, the status for build/<root>.status.json, the log, the project's .tex files). files:
    those files, where undefined keys are looked up (default: from the .fls, input_tex_files)."""
    doc = publish_dest(root)[0]
    stamp = time.strftime("%H:%M:%S")
    try:
        with open(os.path.join(BUILD, f"{root}.log"), errors="replace") as f:
            log = f.read(LOG_LIMIT)
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
    files = input_tex_files(root, doc) if files is None else files
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
    return ok, {"ok": ok, "time": stamp, "errors": summary, "warnings": len(seen) + len(set(MULTI_RE.findall(flat))),
                "overfull": len(boxes), "undefined": undefined, "tex": tex_format(log)}, log, files


if __name__ == "__main__":
    main()
