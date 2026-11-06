#!/usr/bin/env python3
"""The warm standbys of the live build and of its pass without notes: a pdflatex run that has already
read the paper's preamble and waits, so that a save pays only for the body.

latexmkrc runs each pdflatex pass of the owner's previews through this file (run_pass) when live.py
asks for standbys (LATEX_LIVE_STANDBY_OWNER, its pid; on unless LATEX_LIVE_STANDBY=0), as
`standby.py pdflatex ARGS...` with the arguments latexmk gives pdflatex; the TeX command's prefix is
LATEX_LIVE_EXEC, as before. A pass either takes a standby or runs pdflatex as it always did, and
starts the next standby. latex_report.py runs the notes-free pass's TeX runs through run_tex the same
way (see "The notes-free pass" below). live.py stops a document's standbys when it starts, switches
documents, rebuilds from scratch and stops (stop_standbys). Shared mode builds without latexmk: no
standby.

The mechanism
- A standby runs the pass's own command with TeX's output directory moved to
  R/standby/<job>-<id>/out/ (S; R: the build folder) and the pass's output directory (B: R itself, or
  R/clean/ for the notes-free pass) first on TEXINPUTS: TeX looks for a file in S, then in B, then where
  it always does (a normal pass looks in B, then there). S holds a copy of
  the root file's lines before line L, the line that \\begin{document} starts, under the root's name,
  so `\\input{<root>}` reads the copy (and LaTeX's file name, \\CurrentFile and hooks are a normal
  pass's). The copy ends in TAIL, which, in a group, checks that the preamble ended outside any group
  or conditional, borrows a read stream that is not open, opens the FIFO in S and \\read-s a line
  from it. That blocks until a pass releases it.
- A pass takes a waiting standby (take): it claims it (S's folder/claimed, made exclusively), copies
  into S the job's files that TeX reads from B (seed), opens the FIFO once TeX waits on it, checks
  that nothing the preamble saw has changed (before_release), removes the copy and writes the line.
  TeX then makes every character "other" and the end of a line active, opens the real root file with
  the \\input primitive, skips its first L-1 lines, and ends the group at the end of line L-1: from
  line L on it reads the real file, with catcodes, \\endlinechar and everything else as the preamble
  left them, so errors and SyncTeX name the real file and lines. The pass waits for TeX to exit,
  checks what it read (after_release), moves every file the recorder file (.fls) lists as written into
  B, with S's paths in the .fls, the log and the SyncTeX file rewritten to B's and the copy's to the
  root's (finish), and exits with TeX's status. latexmk, latex_report.py and the viewer find what a
  normal pass leaves.
- A standby's keeper process (keep) starts its TeX run (in the TeX Live image through tex-exec, with a
  label and a deadline; on the host only with --host-tex), notes when it started and what the paper's
  folders held, and stops it: when live.py has gone, when the standby is older than MAX_AGE or the
  date has changed (then it starts a fresh one), and when asked (SIGTERM).

The invariant: a pass's PDF, log, .fls and SyncTeX file say what a normal pass at that moment would
have written, or the pass is a normal one. The PDF's bytes differ only in its creation date and ID,
which are the standby's start (at most MAX_AGE earlier). (The SyncTeX file numbers the root file twice,
as the copy and as itself, so the numbers of the files read after it are one higher: it maps the same
places to the same files and lines.)

A pass runs normally, saying why once, unless all of these hold:
- live.py runs it (not a manual latexmk), the engine is pdflatex, and its arguments are the usual
  ones: -recorder, one output directory (B: R or a folder in it), one job name, \\input of a root file
  (with .tex) in the paper root, after anything (the notes-free pass's NOTES_OFF),
  no shell escape; LATEX_LIVE_EXEC is empty (the host's TeX) or tex-exec (the image only); the root's
  first \\begin{document} starts a line after the first, and no earlier line has one;
- a standby of the same key waits (else one is started for the next pass). The key covers the
  arguments, B, the working folder, LATEX_LIVE_EXEC, the environment, the root's name, L and the
  preamble's bytes. Standbys of other keys are stopped;
- the key is verified for the current contents of the files its preamble read, with the same TeX
  format as the last pass. Until then (after a preamble edit, say), a pass runs normally, with a
  standby beside it on the same inputs (the waiting one, or one it starts); when both succeed, no file
  they read changed meanwhile, and the standby's PDF equals the normal one but for dates and IDs and
  its .fls lists the same files, the key is verified; when they differ, the standby is off for the key
  until one of those files changes (verify). A waiting standby whose preamble read a file that has
  changed since is stopped first (outdated);
- at the release (before_release): the standby waits on its FIFO within READY_WAIT, is younger than
  MAX_AGE and from today (local time and UTC); no name has appeared in the paper's folders or in B
  since it started (a probe such as \\IfFileExists that missed a file could now find it); the files a
  verified preamble read are unchanged;
- after it (after_release, from the .fls): TeX stopped at the release; the preamble read the same
  files as the verified one, each last changed (mtime and ctime) before the standby started; no job
  file was read from B rather than S (the copies cover them), no graphic was found in B (where a
  normal pass would not look for it), and no file read from the paper is also in B (where a normal
  pass would have read it); the root file did not change during the pass; the same TeX format.
A standby that stops before its release (an error in its preamble) is not started again for its key
until a file it read changes; one that can't start (e.g. the image is missing) is retried after RETRY.
Files outside the paper and B (the TeX installation, personal TeX trees) are taken not to change
while a standby waits, at most MAX_AGE.

The notes-free pass (latex_report.py notes_free_pass, run_tex): job <doc>-clean, B = R/clean/; its
standbys and records are in R/standby/ beside the live build's (<doc>-clean-<id>/, <doc>-clean.json).
- Its arguments start the root's \\input with NOTES_OFF, which the standby runs before the copy: part of
  its preamble (and of the key). They have -recorder, and the root's name with .tex (given a name
  without it, LaTeX compares the sizes of the files found with and without it, which differ between the
  copy and the root file); a pass without standbys runs as before.
- What it reads of B, the main build's .aux, .bbl and the like that latex_report.py copies there
  (SEED_EXTS) and its own last run's files, are the job's files in B: seed copies them into S.
- With lineno it runs twice (latex_report.py uses_lineno), each run a pass: the first starts the next
  standby once released, and the second takes that one (ready waits while it reads the preamble).
- An early notes-free pass (latex_report.py run_early) is one too, run in place in B. When it is
  stopped mid-run (its process group: the SIGTERM handler stops its standby), latex_report.py stop_early
  calls stop_taken, which waits until the standby it took or was verifying against, its keeper and its
  container are gone, before it puts B back as it was; the standby it started for the next pass waits.
  Its run's container never gets the early pass's label (LATEX_LIVE_EARLY_LABEL: keeper_env).
- One pass at a time per job uses standbys (a lock, R/standby/<job>.lock; another one waits LOCK_WAIT,
  else runs normally); TeX's output goes nowhere; past its timeout a run is stopped (TimedOut); its
  phases go into the live build's timing file as clean-* (Pass.timing). Anything doubtful or failing
  hands the run back to latex_report.py, which runs it as it always did.
LATEX_LIVE_CLEAN_STANDBY=0 turns these off alone (live.py passes LATEX_LIVE_CLEAN_STANDBY=1 otherwise).

At most one standby waits per job (a document's live build, and its notes-free pass), plus the one a pass
is using (and, briefly, one being verified). A pass removes the remains of dead standbys; a keeper stops
its TeX run when live.py dies (also after kill -9) or the pass that took it does, and its TeX run gets
SIGTERM if the keeper dies (on Linux); a container stops at its deadline (DEADLINE) whatever happens, and
live.py removes leftovers by label (stop_standbys).

This file imports nothing from latexlive: latexmkrc runs it as a script (python3 -I).
"""

import ctypes
import errno
import fcntl
import gzip
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import traceback

MAX_AGE = 600  # (s: a waiting standby is replaced after this long, so that its dates are at most this old)
DEADLINE = 3600  # (s: a standby's container stops after this long, whatever happens to its keeper)
READY_WAIT = 60  # (s: how long a pass waits for a standby still reading its preamble)
RETRY = 60  # (s: after a standby could not start, how long until the next try)
WATCH_LIMIT = 20000  # (the most names in the paper's folders that a standby watches)
SCHEME = "2"  # (part of every key: a change to how standbys work voids the records)
PASS_ONLY = ("LATEX_LIVE_EARLY_LABEL", "LATEX_LIVE_CLEAN_PASS")  # (what ties a notes-free pass's runs to it: never a standby's)
NOT_KEY = {"_", "SHLVL", "PWD", "OLDPWD", "TRACEPARENT", "TRACESTATE", "LATEX_LIVE_STANDBY_OWNER", *PASS_ONLY}  # (they vary by process)
LOCK_WAIT = 10  # (s: how long a notes-free pass waits for another one's standby work, run_tex, before it runs normally)
NAME_RE = re.compile(r"[A-Za-z0-9_+-][A-Za-z0-9_.+-]*")
OUTDIR_RE = re.compile(r"--?output-directory=(.*)")
JOBNAME_RE = re.compile(r"--?jobname=(.*)")
SOURCE_RE = re.compile(r"(?s)(.*)\\input\{(" + NAME_RE.pattern + r"\.tex)\}")
FOLDER_RE = re.compile(r"(" + NAME_RE.pattern + r")-[0-9a-f]+-[0-9]+")
REFUSED_RE = re.compile(r"--?(shell-escape|enable-write18|aux-directory=.*|ini|output-format=.*)")
BEGIN_RE = re.compile(rb"\\begin\s*\{\s*document\s*\}")
COMMENT_RE = re.compile(rb"(?<!\\)%.*")
FORMAT_RE = re.compile(rb"\(preloaded format=([^)]*)\)")
GRAPHICS = (".pdf", ".png", ".jpg", ".jpeg", ".jbig2", ".jb2", ".eps", ".mps")
# The job's files in B that TeX doesn't read, not copied into S (seed): TeX's large outputs, and latexmk's
# and latex-live's records. One read from B all the same puts the pass in doubt (after_release).
NOT_READ = (".log", ".pdf", ".synctex.gz", ".synctex(busy)", ".synctex", ".fls", ".fls.done", ".fdb_latexmk", ".lock",
            ".building", ".status.json", ".published.json", ".forward.json", ".retry", ".tmp", ".timing")

# The end of the preamble copy. SKIP: L-1; FIFO: the FIFO's name in S; ROOT: the root file's name. It
# expects of the catcodes the preamble leaves only that \ { } # % and letters do their usual jobs, and it
# makes local assignments only (so not with \globaldefs set).
TAIL = r"""\ifnum\globaldefs=0 \else\errmessage{latex-live standby: the preamble sets globaldefs}\fi
\begingroup\catcode126=13 \lccode126=13 \lowercase{\def\latexlivestandbyskip#1~}{\advance\count255 -1 %
\ifnum\count255>0 \expandafter\latexlivestandbyskip\else\expandafter\endgroup\fi}%
\expandafter\let\expandafter\latexlivestandbyinput\csname @@input\endcsname
\def\latexlivestandbyfree{\advance\count255 -1 \ifnum\count255<0 \errmessage{latex-live standby: no free read stream}%
\else\ifeof\count255 \else\expandafter\expandafter\expandafter\latexlivestandbyfree\fi\fi}%
\def\latexlivestandbyother{\catcode\count255=12 \advance\count255 1 \ifnum\count255<256 \expandafter\latexlivestandbyother\fi}%
\def\latexlivestandbyresume{%
\count255=\currentiflevel \ifnum\count255=0 \else\errmessage{latex-live standby: the preamble ends in a conditional}\fi
\count255=\currentgrouplevel \ifnum\count255=1 \else\errmessage{latex-live standby: the preamble ends in a group}\fi
\count255=16 \latexlivestandbyfree
\openin\count255=FIFO
\ifeof\count255 \errmessage{latex-live standby: no FIFO}\fi
\read\count255 to\latexlivestandbygo
\ifeof\count255 \errmessage{latex-live standby: stopped before its release}\fi
\closein\count255
\count255=0 \latexlivestandbyother \catcode13=13 \endlinechar=13 \count255=SKIP %
\expandafter\latexlivestandbyskip\latexlivestandbyinput"ROOT" }%
\latexlivestandbyresume
"""


def say(message):
    print(f"=== latex-live: {message}", flush=True)


def note_time(p, event, detail=""):
    """With LATEX_LIVE_TIMING=1, note when a phase of pass p happened, in B/<job>.timing, or in the file its caller
    named, with a prefix and the name of the pass (p.timing: the notes-free pass's go in the live build's, as clean-*,
    each naming its pass; how a run went is then the event: clean-standby, clean-verify or clean-normal;
    latexlive/timing.py)."""
    if p.env.get("LATEX_LIVE_TIMING") == "1" and p.job:
        path, prefix, name = p.timing or (p.file(".timing"), "", "")
        if name:
            event, detail = ("standby" if detail == "verified" else detail) if event == "standby" else event, name
        try:
            with open(path, "a") as f:
                f.write(f"{time.time():.6f} {prefix}{event}{' ' + detail if detail else ''}\n")
        except OSError:
            pass


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_json(path, value):
    put(path, json.dumps(value).encode())


def put(path, data):
    """Write a file in one step (a reader sees the old one or the new one)."""
    with open(path + ".tmp", "wb") as f:
        f.write(data)
    os.replace(path + ".tmp", path)


def remove(folder):
    shutil.rmtree(folder, ignore_errors=True)


def stamp(path):
    """[mtime, ctime, size] of a file (ns), or None if there is none."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return [st.st_mtime_ns, st.st_ctime_ns, st.st_size]


def changed_since(st, t0):
    """Whether a file with stamp st may have changed at or after t0 (ns): its mtime or ctime is not clearly
    before it. File times come from a coarse clock, and in whole seconds on some file systems."""
    margin = 50_000_000 if st[0] % 1_000_000_000 else 2_000_000_000
    return max(st[0], st[1]) >= t0 - margin


def process_stat(pid):
    """(state, start time in clock ticks) of a process, from /proc (Linux), or (None, None)."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            fields = f.read().rsplit(")", 1)[1].split()
        return fields[0], int(fields[19])
    except (OSError, ValueError, IndexError):
        return None, None


def process_start(pid):
    """When a process started (on Linux), to tell it from a later one with its pid."""
    return process_stat(pid)[1]


def alive(pid, start=None):
    """Whether process pid runs: not gone, not a zombie that its parent has yet to reap, and, given start, the
    one that started then."""
    try:
        os.kill(int(pid), 0)
    except (OSError, TypeError, ValueError):
        return False
    state, started = process_stat(pid)
    return state != "Z" and (start is None or started in (None, start))


def command_line(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().decode(errors="replace").replace("\0", " ")
    except OSError:
        return subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout


def today(when=None):
    """The date at time when (s; default now), in local time and in UTC."""
    when = time.time() if when is None else when
    return [time.strftime("%Y-%m-%d", time.localtime(when)), time.strftime("%Y-%m-%d", time.gmtime(when))]


def label_of(paper_root, build, job):
    """The label of a document's standby containers: one per paper, build folder and job."""
    return hashlib.sha1(f"{os.path.realpath(paper_root)}\0{os.path.normpath(build)}".encode()).hexdigest()[:12] + "-" + job


def fls_entries(fls):
    """The ("INPUT" or "OUTPUT", normalized path) entries of a recorder file."""
    try:
        with open(fls, errors="surrogateescape") as f:
            lines = f.read(64 << 20).splitlines()
    except OSError:
        return []
    return [(kind, os.path.normpath(path)) for kind, _, path in (line.partition(" ") for line in lines)
            if kind in ("INPUT", "OUTPUT")]


def written_folders(build, job):
    """B's subfolders (relative to B) that the last pass wrote files in, from its .fls."""
    found = set()
    for kind, path in fls_entries(os.path.join(build, f"{job}.fls")):
        if kind == "OUTPUT" and path.startswith(build + os.sep):
            found.add(os.path.dirname(os.path.relpath(path, build)))
    return sorted(found - {""})


def tex_format(log):
    """The format a log's run used (e.g. "pdflatex 2024.6.15"), or None."""
    try:
        with open(log, "rb") as f:
            m = FORMAT_RE.search(f.read(2000))
    except OSError:
        return None
    return m and m.group(1).decode(errors="replace")


# ---------------------------------------------------------------------------------------------------
# A pass: what latexmk asked for, and whether a standby can do it.

class Pass:
    """One pdflatex pass (argv: pdflatex and its arguments), run from the paper root: one that latexmk asked for,
    or the notes-free pass (run_tex). build: the build folder (R, which holds the standbys' folder); out: the
    pass's output directory (B: R itself, or the notes-free pass's R/clean/). why: why a standby can't run it, else
    None. timing: where note_time notes its phases, their prefix and the pass's name (default B/<job>.timing); timeout: how
    long its TeX run may take (s), else TimedOut; quiet: whether TeX's output goes nowhere (else to stdout)."""

    def __init__(self, argv, env, timing=None, timeout=None, quiet=False):
        self.argv, self.env, self.cwd = list(argv), env, os.getcwd()
        self.exec = shlex.split(env.get("LATEX_LIVE_EXEC", ""))
        self.build = os.path.normpath(env.get("LATEX_LIVE_BUILD", "build"))
        self.out = self.build
        self.timing, self.timeout, self.quiet = timing, timeout, quiet
        self.owner = self.job = self.root = None
        self.why = self.check()

    def check(self):
        argv, env = self.argv, self.env
        owner = env.get("LATEX_LIVE_STANDBY_OWNER", "")
        if not owner.isdigit() or not alive(owner):
            return "off"  # (a manual latexmk, or LATEX_LIVE_STANDBY=0)
        self.owner = int(owner)
        if len(argv) < 3 or argv[0] != "pdflatex":
            return f"the engine is {argv[0] if argv else 'none'}, not pdflatex"
        if self.exec and os.path.basename(self.exec[0]) != "tex-exec":
            return "LATEX_LIVE_EXEC is not latex-live's tex-exec"
        options, m = argv[1:-1], SOURCE_RE.fullmatch(argv[-1])
        refused = next((o for o in options if not o.startswith("-") or REFUSED_RE.fullmatch(o)), None)
        if refused:
            return f"pdflatex gets {refused}"
        if env.get("shell_escape", "") in ("t", "y", "1"):
            return "shell escape is on"
        if "-recorder" not in options and "--recorder" not in options:
            return "pdflatex runs without -recorder"
        outdirs = [os.path.normpath(o.group(1)) for o in map(OUTDIR_RE.fullmatch, options) if o]
        jobs = [o.group(1) for o in map(JOBNAME_RE.fullmatch, options) if o]
        if len(outdirs) != 1 or len(jobs) != 1 or not NAME_RE.fullmatch(jobs[0]) or not m:
            return "pdflatex's arguments are not the usual ones"
        self.out = outdirs[0]
        for name in (self.build, self.out):
            if os.path.isabs(name) or ".." in name.split(os.sep) or not re.fullmatch(r"[A-Za-z0-9_./+-]+", name):
                return f"the build folder's name ({name})"
        if self.out != self.build and (not self.out.startswith(self.build + os.sep)
                                       or os.path.relpath(self.out, self.build).split(os.sep)[0] == "standby"):
            return f"the output directory ({self.out}) is not the build folder or one in it"
        self.job, self.root = jobs[0], m.group(2)
        if not os.path.isfile(self.root):
            return f"{self.root} is not in the paper root"
        return None

    def run_plain(self):
        """Run the pass as a normal one, in place of this process."""
        sys.stdout.flush()
        argv = [*self.exec, *self.argv]
        os.execvp(argv[0], argv)

    def popen(self):
        """Start the pass as a normal one, beside this process."""
        quiet = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL} if self.quiet else {}
        return subprocess.Popen([*self.exec, *self.argv], env=self.env, **quiet)

    def preamble(self):
        """(the root's lines before \\begin{document}, L: the line it starts), or (None, why not)."""
        try:
            with open(self.root, "rb") as f:
                data = f.read(16 << 20)
        except OSError as e:
            return None, f"{self.root}: {e.strerror}"
        lines = data.splitlines()  # (as TeX splits them: at \n, \r\n and \r)
        for i, line in enumerate(lines):
            if BEGIN_RE.search(COMMENT_RE.sub(b"", line)):
                if i and re.match(rb"[ \t]*\\begin\s*\{\s*document\s*\}", line):
                    return lines[:i], i + 1
                break
        return None, "its \\begin{document} does not start a line of its own after the first"

    def key(self, lines, line):
        args = [a for a in self.argv if not OUTDIR_RE.fullmatch(a)]
        env = sorted((k, v) for k, v in self.env.items() if k not in NOT_KEY)
        h = hashlib.sha256(json.dumps([SCHEME, args, self.out, os.path.realpath(self.cwd), self.exec, env, self.root, line]).encode())
        h.update(b"\n".join(lines))
        return h.hexdigest()[:32]

    def folder(self):
        return os.path.join(self.build, "standby")

    def file(self, ext):
        return os.path.join(self.out, f"{self.job}{ext}")


# ---------------------------------------------------------------------------------------------------
# A document's records (build/standby/<job>.json): verified and refused keys, what was said, the last pass.

class Records:
    def __init__(self, p):
        self.path = os.path.join(p.folder(), f"{p.job}.json")
        self.data = read_json(self.path) or {}
        for name in ("verified", "refused", "said"):
            self.data.setdefault(name, [])
        self.dirty = False

    def save(self):
        if self.dirty:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            write_json(self.path, self.data)
            self.dirty = False

    def say_once(self, token, message):
        """Print message, unless it was printed under this token (a reason and a key) since live.py started."""
        if token not in self.data["said"]:
            self.data["said"] = (self.data["said"] + [token])[-100:]
            self.dirty = True
            say(message)

    def last(self, via, why=None):
        """Record how the last pass ran (via: standby, verify or normal), for check.py and for people."""
        self.data["last"] = {"via": via, "why": why, "time": time.time()}
        passes = self.data.setdefault("passes", {})
        passes[via] = passes.get(via, 0) + 1
        self.dirty = True

    def find(self, name, key):
        """The record of key in name (verified, refused) whose files are unchanged, or None."""
        for record in reversed(self.data[name]):
            if record["key"] == key and all(stamp(path) == st for path, st in record["inputs"]):
                if not record.get("until") or time.time() < record["until"]:
                    return record
        return None

    def paths(self, key):
        """The files that preambles of key read when they were verified (also those that have changed since)."""
        return sorted({path for r in self.data["verified"] if r["key"] == key for path, _ in r["inputs"]})

    def add(self, name, record, keep):
        self.data[name] = [r for r in self.data[name] if r["key"] != record["key"]][-(keep - 1):] + [record]
        self.dirty = True


# ---------------------------------------------------------------------------------------------------
# A standby's folder, build/standby/<job>-<id>/: TeX's output directory out/ (S), and standby.json (what it
# runs, written before it starts), keeper.json (when its TeX run started and what the paper's folders held
# then), claimed (who took it: a pass's pid, "keeper", or "stopped" by a pass that stopped it), done.json (its
# TeX run's exit status), tex.out (what TeX printed).

class Standby:
    def __init__(self, folder):
        self.dir = folder
        self.out = os.path.join(folder, "out")
        self.info = read_json(os.path.join(folder, "standby.json"))
        self.keeper = read_json(os.path.join(folder, "keeper.json"))

    def path(self, name):
        return os.path.join(self.dir, name)

    def claimed(self):
        try:
            with open(self.path("claimed")) as f:
                return f.read().strip() or "?"
        except OSError:
            return None

    def claim(self, who):
        """Take the standby, as who (a pid, or "keeper"); only one taker gets it."""
        try:
            fd = os.open(self.path("claimed"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except OSError:
            return False
        os.write(fd, str(who).encode())
        os.close(fd)
        return True

    def done(self):
        return read_json(self.path("done.json"))

    def keeper_alive(self):
        self.keeper = self.keeper or read_json(self.path("keeper.json"))
        return bool(self.keeper) and alive(self.keeper["pid"], self.keeper.get("start"))

    def keeper_lost(self):
        """Whether its keeper has died, or never recorded its start (it does within a moment)."""
        if self.keeper_alive():
            return False
        return bool(self.keeper) or not self.info or time.time() - self.info["created"] > 20

    def stop(self):
        """Have the keeper stop its TeX run and remove the folder (not waited for), and claim the standby
        meanwhile, so that no one takes it or counts it as waiting; remove it at once if the keeper is gone."""
        self.claim("stopped")
        if self.keeper_alive() and os.path.basename(self.dir) in command_line(self.keeper["pid"]):
            try:
                os.kill(self.keeper["pid"], signal.SIGTERM)
                return
            except OSError:
                pass
        remove(self.dir)


def standbys(p):
    try:
        names = sorted(os.listdir(p.folder()))
    except OSError:
        return []
    return [Standby(os.path.join(p.folder(), n)) for n in names
            if is_folder_of(n, p.job) and os.path.isdir(os.path.join(p.folder(), n, "out"))]


def is_folder_of(name, job=None):
    """Whether name is that of a standby's folder (of job, or of any): <job>-<id>-<pid>, as start_standby names it
    (so that the job x-live's standbys are not taken for those of x-live-clean, say)."""
    m = FOLDER_RE.fullmatch(name)
    return bool(m) and (job is None or m.group(1) == job)


def tidy(p, records, key):
    """Remove what earlier standbys left (dead ones, those of other keys, those a dead pass took), recording
    why those that stopped before their release stopped; return those of key that wait, newest first."""
    waiting = []
    for sb in standbys(p):
        if sb.info is None:
            if time.time() - os.stat(sb.dir).st_mtime > 60:  # (one being set up writes standby.json at once)
                remove(sb.dir)
            continue
        who, done = sb.claimed(), sb.done()
        if who == "verify":  # (its keeper judges it once its run ends: beside)
            if os.path.exists(sb.path("verdict.json")):
                merge(records, sb)
            elif not sb.keeper_alive():
                if done is not None:
                    judge(sb)
                merge(records, sb)
        elif who == "keeper":
            if done is not None:  # (its TeX run stopped before anyone took it)
                if done.get("reason"):
                    refuse(records, sb.info["key"], done)
                remove(sb.dir)
            elif not sb.keeper_alive():
                remove(sb.dir)
        elif who is not None:
            if not who.isdigit() or not alive(who):
                sb.stop()
        elif sb.keeper_lost() or done is not None:
            sb.stop()
        elif sb.info["key"] != key:
            sb.stop()
        else:
            waiting.append(sb)
    return sorted(waiting, key=lambda sb: sb.info["created"], reverse=True)


def outdated(sb, paths):
    """Whether one of the files (paths) changed after the standby started: its preamble read an old version."""
    sb.keeper = sb.keeper or read_json(sb.path("keeper.json"))
    for path in paths if sb.keeper else ():
        st = stamp(path)
        if st is None or changed_since(st, sb.keeper["t0"]):
            return True
    return False


def refuse(records, key, done):
    """Record that the standby of key stopped before its release (done: its keeper's account)."""
    until = None if done.get("error") else time.time() + RETRY
    records.add("refused", {"key": key, "reason": done["reason"], "inputs": done.get("inputs", []), "until": until}, 8)
    records.say_once(f"refused:{key}", f"no warm standby for this preamble: {done['reason']}"
                     + (f" (trying again in {RETRY} s)" if until else " (until a file it reads changes)"))


def start_standby(p, records, key, lines, line):
    """Start a standby for passes like p (key: its key; lines and line: the preamble and L), unless one of
    that key waits already or the key is refused. Returns it, or None."""
    if records.find("refused", key):
        return None
    for sb in standbys(p):
        if sb.info and sb.info["key"] == key and sb.claimed() is None and sb.done() is None and not sb.keeper_lost():
            return None
    folder = os.path.join(p.folder(), f"{p.job}-{time.time_ns():x}-{os.getpid()}")
    out = os.path.join(folder, "out")
    os.makedirs(out)
    fifo = f"{p.job}-standby.fifo"
    tail = TAIL.replace("SKIP", str(line - 1)).replace("FIFO", fifo).replace("ROOT", p.root)
    with open(os.path.join(out, p.root), "wb") as f:
        f.write(b"\n".join(lines) + b"\n" + tail.encode())
    os.mkfifo(os.path.join(out, fifo), 0o600)
    for sub in build_folders(p.out):  # (TeX writes an \include's .aux in B's subfolders, which latexmk makes)
        os.makedirs(os.path.join(out, sub), exist_ok=True)
    texinputs = p.env.get("TEXINPUTS")
    env = {"TEXINPUTS": f"{p.out}:{texinputs or ''}", "LATEX_LIVE_STANDBY_LABEL": label_of(p.cwd, p.build, p.job),
           "LATEX_LIVE_RUN": os.path.basename(folder), "LATEX_LIVE_TIMEOUT": str(DEADLINE)}
    argv = [f"-output-directory={out}" if OUTDIR_RE.fullmatch(a) else a for a in p.argv]
    write_json(os.path.join(folder, "standby.json"), {
        "key": key, "job": p.job, "root": p.root, "line": line, "build": p.build, "out": p.out, "argv": [*p.exec, *argv],
        "env": env, "pass": p.argv, "owner": p.owner, "owner_start": process_start(p.owner), "created": time.time(),
        "host": not p.exec})
    keeper_env = {k: v for k, v in p.env.items() if k not in PASS_ONLY}  # (the pass's, but what ties a run to it)
    subprocess.Popen([sys.executable, "-I", os.path.abspath(__file__), "--keep", folder], cwd=p.cwd, env=keeper_env,
                     start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return Standby(folder)


def out_of(info):
    """A standby's B (standby.json; one written before "out" was there has B = R)."""
    return info.get("out", info["build"])


def build_folders(build):
    """B's subfolders (relative to B), but the standbys' own."""
    found = []
    for folder, dirs, _ in os.walk(build):
        rel = os.path.relpath(folder, build)
        dirs[:] = sorted(d for d in dirs if not (rel == "." and d == "standby"))
        if rel != ".":
            found.append(rel)
        if len(found) > 500:
            break
    return found


# ---------------------------------------------------------------------------------------------------
# What the paper's folders and B held when a standby started, to tell whether a name appeared since.

def snapshot(build, job, out=None):
    """{folder: [mtime, names]} for the paper root's folders (not dot folders, nor R: build), through links to
    folders too (a file added in one is a name TeX may find), each real folder once; and for B (out, default R)
    and the subfolders of B the last pass wrote in; None past WATCH_LIMIT names."""
    out = out or build
    snap, count, seen = {}, 0, set()
    for folder, dirs, _ in os.walk(".", followlinks=True):
        rel = os.path.normpath(folder)
        real = os.path.realpath(folder)
        if real in seen:  # (a link back up, or a second link to the same folder)
            dirs[:] = []
            continue
        seen.add(real)
        dirs[:] = [d for d in dirs if not d.startswith(".") and os.path.normpath(os.path.join(rel, d)) != build]
        names = sorted(os.listdir(folder))
        count += len(names)
        if count > WATCH_LIMIT:
            return None
        snap[rel] = [os.stat(folder).st_mtime_ns, names]
    for rel in [out, *(os.path.join(out, d) for d in written_folders(out, job))]:
        try:
            snap[rel] = [os.stat(rel).st_mtime_ns, names_in(rel, out, job)]
        except OSError:
            pass
    return snap


def names_in(folder, out, job):
    """The names in a folder; in B (out), but temporary files, the standbys' folder and the job's files that TeX
    doesn't read (NOT_READ: one read all the same puts the pass in doubt)."""
    if folder != out and not folder.startswith(out + os.sep):
        return sorted(os.listdir(folder))
    return sorted(n for n in os.listdir(folder) if not n.endswith(".tmp") and not (folder == out and n == "standby")
                  and not (n.startswith(job + ".") and n.endswith(NOT_READ)))


def new_name(snap, out, job):
    """A name (folder/name) that has appeared in a snapshot's folders since it was taken, or None. (A name
    that went away can't make TeX find what it missed; what TeX read from it is checked by its stamp.)"""
    for folder, (mtime, names) in snap.items():
        try:
            if os.stat(folder).st_mtime_ns == mtime:
                continue
            added = set(names_in(folder, out, job)) - set(names)
        except OSError:
            continue
        if added:
            return os.path.normpath(os.path.join(folder, sorted(added)[0]))
    return None


# ---------------------------------------------------------------------------------------------------
# The keeper (standby.py --keep FOLDER): starts a standby's TeX run and stops it.

def keep(folder):
    sb = Standby(folder)
    info = sb.info
    if info is None:
        return
    stopping = []
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda *_: stopping.append(True))
    snap = snapshot(info["build"], info["job"], out_of(info))
    if snap is None:
        if sb.claim("keeper"):
            write_json(sb.path("done.json"), {"code": None, "error": True,
                                              "reason": f"the paper's folders hold more than {WATCH_LIMIT} files to watch"})
        return
    t0, days = time.time_ns(), today()
    linux = sys.platform.startswith("linux")  # (PR_SET_PDEATHSIG: TeX gets SIGTERM if the keeper dies)
    with open(sb.path("tex.out"), "wb") as out:
        proc = subprocess.Popen(info["argv"], env={**os.environ, **info["env"]}, stdin=subprocess.DEVNULL, stdout=out,
                                stderr=subprocess.STDOUT,
                                preexec_fn=(lambda: ctypes.CDLL("libc.so.6").prctl(1, signal.SIGTERM)) if linux else None)
    write_json(sb.path("keeper.json"), {"pid": os.getpid(), "start": process_start(os.getpid()), "tex": proc.pid, "t0": t0,
                                        "snapshot": snap})
    while True:
        try:
            code = proc.wait(0.25)
            break
        except subprocess.TimeoutExpired:
            pass
        who = sb.claimed()
        if stopping or not os.path.isdir(sb.out) or who and who.isdigit() and not alive(who):
            stop_tex(proc, info)  # (asked to; or the pass that took it has died)
            remove(folder)
            return
        if who is not None:
            continue
        owner = alive(info["owner"], info["owner_start"])
        stale = time.time_ns() - t0 > MAX_AGE * 1_000_000_000 or today() != days
        if (not owner or stale) and sb.claim("keeper"):
            stop_tex(proc, info)
            remove(folder)
            if owner:
                replace_standby(info)  # (a stale one's dates and \time would be off)
            return
    if stopping:
        remove(folder)
    elif sb.claim("keeper"):  # (no one took it: it stopped before its release)
        reason, error = stopped_why(sb, info, code)
        write_json(sb.path("done.json"), {"code": code, "reason": reason, "error": error,
                                          "inputs": preamble_reads(sb, t0)[0] if error else []})
    else:
        write_json(sb.path("done.json"), {"code": code})
        if sb.claimed() == "verify":  # (handed over by a verifying pass: beside)
            judge(sb)


def stop_tex(proc, info):
    """Stop a standby's TeX run: SIGTERM (podman passes it to the container), then SIGKILL, and then
    remove its container if it is still there."""
    proc.terminate()
    try:
        proc.wait(5)
        return
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    if not info["host"]:
        remove_containers("latex-live.run", info["env"]["LATEX_LIVE_RUN"])


def remove_containers(name, value):
    if not shutil.which("podman"):
        return
    try:
        ids = subprocess.run(["podman", "ps", "-aq", "--filter", f"label={name}={value}"], capture_output=True, text=True,
                             timeout=30).stdout.split()
        if ids:
            subprocess.run(["podman", "rm", "-f", "-t", "0", *ids], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        pass


def stopped_why(sb, info, code):
    """(why a standby's TeX run stopped before its release, whether the preamble stopped it with an error)."""
    try:
        with open(os.path.join(sb.out, f"{info['job']}.log"), "rb") as f:
            log = f.read(64 << 20).decode(errors="replace")
    except OSError:
        log = ""
    m = re.search(r"^(?:! (.*)|\S+:\d+: (.*))$", log, re.M)
    if m:
        return f"its preamble stops with an error ({(m.group(1) or m.group(2)).strip()[:120]})", True
    try:
        with open(sb.path("tex.out"), errors="replace") as f:
            printed = f.read(4096)
    except OSError:
        printed = ""
    if "is not pulled" in printed:
        return "the TeX Live image is missing (a standby never runs the host's TeX in its place)", False
    return f"its TeX run stopped at once (status {code})", False


def replace_standby(info):
    """Start a standby in place of a stale one, for the pass that started that one (in its keeper)."""
    p = Pass(info["pass"], os.environ)
    lines, line = p.preamble() if not p.why else (None, None)
    if lines is not None:
        records = Records(p)
        key = p.key(lines, line)
        tidy(p, records, key)
        start_standby(p, records, key, lines, line)
        records.save()


# ---------------------------------------------------------------------------------------------------
# Taking a standby.

def seed(p, sb):
    """Copy into S the job's files that TeX reads from B: <job>.* (but NOT_READ) and whatever else the last
    pass wrote in B. Never over a file TeX has made in S itself (its preamble may write some)."""
    names = set()
    try:
        names.update(n for n in os.listdir(p.out) if n.startswith(p.job + ".") and not n.endswith(NOT_READ))
    except OSError:
        pass
    for kind, path in fls_entries(p.file(".fls")):
        if kind == "OUTPUT" and path.startswith(p.out + os.sep) and not path.endswith(NOT_READ):
            names.add(os.path.relpath(path, p.out))
    for rel in sorted(names):
        src, dst = os.path.join(p.out, rel), os.path.join(sb.out, rel)
        try:
            with open(src, "rb") as g:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644), "wb") as f:
                    shutil.copyfileobj(g, f)
        except OSError:
            pass


def ready(sb, deadline, normal=None):
    """Wait until the standby's TeX run waits on its FIFO; return the FIFO open for writing, or None if it
    stopped, isn't ready by deadline (monotonic), or the normal pass beside it has ended."""
    fifo = os.path.join(sb.out, f"{sb.info['job']}-standby.fifo")
    checked = 0
    while True:
        try:
            return os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as e:
            if e.errno != errno.ENXIO:
                return None
        now = time.monotonic()
        if now > deadline or normal is not None and normal.poll() is not None:
            return None
        if now - checked > 0.1:
            checked = now
            if sb.done() is not None or sb.keeper_lost():
                return None
        time.sleep(0.005)


def release(sb, fd):
    """Let the standby's TeX run go on: remove the preamble copy (TeX has it open), so that its \\input
    finds the real root file, and write the line its \\read waits for."""
    try:
        os.unlink(os.path.join(sb.out, sb.info["root"]))
        os.write(fd, b"go\n")
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


class TimedOut(Exception):
    """A pass with a timeout (Pass.timeout) took longer: its TeX run was stopped."""


def wait_done(sb, timeout=None):
    """The standby's TeX run's exit status once it ends, or None if its keeper died first; past timeout (s), stop
    it and raise TimedOut."""
    checked = start = time.monotonic()
    while True:
        done = sb.done()
        if done is not None:
            return done.get("code")
        if time.monotonic() - checked > 0.1:
            checked = time.monotonic()
            if not sb.keeper_alive() and sb.done() is None:
                return None
            if timeout is not None and checked - start > timeout:
                sb.stop()
                raise TimedOut()
        time.sleep(0.005)


def wait_normal(p, proc):
    """The exit status of the normal pass proc; past p.timeout, stop it and raise TimedOut."""
    try:
        return proc.wait(p.timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise TimedOut() from None


def before_release(p, sb, record):
    """Why the standby, now waiting on its FIFO, must not be used, or None."""
    sb.keeper = sb.keeper or read_json(sb.path("keeper.json"))
    if not sb.keeper:
        return "it did not record its start"
    t0 = sb.keeper["t0"]
    if time.time_ns() - t0 > MAX_AGE * 1_000_000_000:
        return "it is too old"
    if today(t0 / 1e9) != today():
        return "the date has changed since it started"
    name = new_name(sb.keeper["snapshot"], p.out, p.job)
    if name:
        return f"{name} appeared after it started"
    for path, st in record["inputs"] if record else []:
        if stamp(path) != st or changed_since(st, t0):
            return f"its preamble's {path} changed"
    return None


def preamble_reads(sb, t0):
    """(the files that the standby's preamble read and that can change, [[path, stamp]...]: in the paper, in
    B, and on a host run anywhere; why the run is in doubt, or None), from its .fls."""
    info, out = sb.info, os.path.normpath(sb.out)
    build, job = out_of(info), info["job"]
    fifo = os.path.join(out, f"{job}-standby.fifo")
    entries = fls_entries(os.path.join(out, f"{job}.fls"))
    doubt = None if ("INPUT", fifo) in entries else "its TeX run did not stop at its release"
    root = os.path.realpath(".")
    reads, seen, before = [], set(), True
    for kind, path in entries:
        if kind != "INPUT" or path == out or path.startswith(out + os.sep) and path != fifo:
            continue
        if path == fifo:
            before = False
            continue
        rel = os.path.relpath(path, root) if path.startswith(root + os.sep) else path
        if not os.path.isabs(rel):
            in_build = rel == build or rel.startswith(build + os.sep)
            if in_build and (os.path.basename(rel).startswith(job + ".") or rel.lower().endswith(GRAPHICS)):
                doubt = doubt or f"it read {rel} from the build folder"
            elif not in_build and os.path.exists(os.path.join(build, rel)):
                doubt = doubt or f"{rel} is also in the build folder, where a normal pass looks first"
        elif not info["host"]:
            continue  # (the TeX Live image's files, which don't change)
        if before and rel not in seen:
            seen.add(rel)
            st = stamp(rel)
            if st is None or changed_since(st, t0):
                doubt = doubt or f"its preamble's {rel} changed after it started"
            reads.append([rel, st])
    return reads, doubt


def after_release(p, sb, record, code, root_stamp):
    """Why the finished run of the standby (of a verified key: record) is in doubt, or None."""
    reads, doubt = preamble_reads(sb, sb.keeper["t0"])
    used = tex_format(os.path.join(sb.out, f"{p.job}.log"))
    if code is None:
        doubt = "its keeper stopped"
    elif sorted(r[0] for r in reads) != sorted(r[0] for r in record["inputs"]):
        doubt = doubt or "its preamble read other files than the verified one"
    elif used != record["format"]:
        doubt = doubt or f"it ran another TeX ({used})"
    elif stamp(p.root) != root_stamp:
        doubt = doubt or f"{p.root} changed during the pass"
    return doubt


def rewrite(sb):
    """The (old, new) replacements that turn S's paths in TeX's output into B's, and the copy's into the root's."""
    out, root, build = os.path.normpath(sb.out), sb.info["root"], out_of(sb.info)
    return [(f"{out}/{root}".encode(), f"./{root}".encode()), (f"{out}/".encode(), f"{build}/".encode())]


def fixed(data, reps):
    for old, new in reps:
        data = data.replace(old, new)
    return data


def finish(p, sb):
    """Move the standby's output into B, as a normal pass leaves it (rewrite)."""
    out, job, reps = sb.out, p.job, rewrite(sb)
    fifo = f"INPUT {os.path.normpath(out)}/{job}-standby.fifo".encode()
    try:
        with open(os.path.join(out, f"{job}.fls"), "rb") as f:
            fls = [line for line in f.read().splitlines() if line != fifo]
        put(p.file(".fls"), fixed(b"\n".join(fls) + b"\n", reps))
    except OSError:
        pass
    for ext, packed in ((".log", False), (".synctex.gz", True)):
        try:
            with open(os.path.join(out, job + ext), "rb") as f:
                data = f.read()
            put(p.file(ext), gzip.compress(fixed(gzip.decompress(data), reps), 1, mtime=0) if packed else fixed(data, reps))
        except (OSError, EOFError, gzip.BadGzipFile):
            pass
    for kind, path in dict.fromkeys(fls_entries(os.path.join(out, f"{job}.fls"))):
        if kind != "OUTPUT" or not path.startswith(os.path.normpath(out) + os.sep):
            continue
        rel = os.path.relpath(path, out)
        src, dst = os.path.join(out, rel), os.path.join(p.out, rel)
        if rel in (f"{job}.log", f"{job}.synctex.gz"):  # (rewritten above)
            continue
        if os.path.isfile(src):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.replace(src, dst)
        elif rel == f"{job}.pdf" and os.path.isfile(dst):
            os.remove(dst)  # (TeX removed the PDF it had begun, as a failed normal pass does B's)
    busy = os.path.join(out, f"{job}.synctex(busy)")
    if os.path.isfile(busy):
        os.replace(busy, p.file(".synctex(busy)"))


DATE_RES = (re.compile(rb"(/(?:CreationDate|ModDate)\s*\(D:)([^)]*)"), re.compile(rb"(/ID\s*\[\s*<)([0-9A-Fa-f]*>\s*<[0-9A-Fa-f]*)"),
            re.compile(rb"()(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)?)"))


def masked(pdf):
    """A PDF's bytes with its dates and IDs blanked (keeping its length), or None if there is none."""
    try:
        with open(pdf, "rb") as f:
            data = f.read()
    except OSError:
        return None
    for date in DATE_RES:
        data = date.sub(lambda m: m.group(1) + b"0" * len(m.group(2)), data)
    return data


def fls_set(fls, reps=(), drop=None):
    return {(kind, os.path.normpath(fixed(path.encode(errors="surrogateescape"), reps).decode(errors="surrogateescape")))
            for kind, path in fls_entries(fls) if path != drop}


def judge(sb):
    """Compare a standby's run with the normal pass beside it (verify.json says when that started; reference.pdf
    and reference.fls are its output) and write the verdict, verdict.json, which merge records; whether this call
    did (only one does). In doubt if a file of the paper that the normal pass read changed after it started (the
    two may have read different versions of it), or as preamble_reads says."""
    try:
        os.close(os.open(sb.path("judged"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644))
    except OSError:
        return False  # (the pass and the keeper both may get here; one judges)
    job, out, started = sb.info["job"], sb.out, read_json(sb.path("verify.json"))["started"]
    keeper = sb.keeper or read_json(sb.path("keeper.json"))
    reads, doubt = preamble_reads(sb, keeper["t0"])
    if (sb.done() or {}).get("code") != 0:
        doubt = "its run failed"
    for kind, path in fls_entries(sb.path("reference.fls")):
        st = stamp(path) if kind == "INPUT" and not os.path.isabs(path) and not path.startswith(out_of(sb.info) + os.sep) else None
        if st and changed_since(st, started):
            doubt = doubt or f"{path} changed during the pass"
    verdict = {"key": sb.info["key"], "inputs": reads, "format": tex_format(os.path.join(out, f"{job}.log")), "doubt": doubt}
    if doubt is None:
        fifo = os.path.normpath(os.path.join(out, f"{job}-standby.fifo"))
        pdf, ours = masked(sb.path("reference.pdf")), masked(os.path.join(out, f"{job}.pdf"))
        if pdf is None or pdf != ours:
            verdict["mismatch"] = "its PDF differed from a normal pass's"
        elif fls_set(os.path.join(out, f"{job}.fls"), rewrite(sb), fifo) != fls_set(sb.path("reference.fls")):
            verdict["mismatch"] = "it read or wrote other files than a normal pass"
    write_json(sb.path("verdict.json"), verdict)
    return True


def merge(records, sb):
    """Record a standby's verdict (judge), and remove its folder."""
    verdict = read_json(sb.path("verdict.json"))
    if verdict and verdict["doubt"] is None:
        key = verdict["key"]
        if verdict.get("mismatch"):
            records.add("refused", {"key": key, "reason": verdict["mismatch"], "inputs": verdict["inputs"], "until": None}, 8)
            records.say_once(f"mismatch:{key}", f"the warm standby is off for this preamble: {verdict['mismatch']}")
        else:
            records.add("verified", {"key": key, "inputs": verdict["inputs"], "format": verdict["format"]}, 4)
    remove(sb.dir)


def take(p, records, key, sb, lines, line):
    """Run the pass with standby sb (claimed). Returns its exit status, or None when it is to run normally."""
    record = records.find("verified", key)
    if record and record["format"] != tex_format(p.file(".log")):
        record = None  # (another TeX than when it was verified)
    if record is None:
        return beside(p, records, key, sb, lines, line)
    root_stamp = stamp(p.root)
    seed(p, sb)
    fd = ready(sb, time.monotonic() + READY_WAIT)
    why = stopped(records, key, sb) if fd is None else before_release(p, sb, record)
    if why is None and not release(sb, fd):
        why = "its release failed"
    elif why is not None and fd is not None:
        os.close(fd)  # (its \read finds the FIFO's end, and TeX stops)
    if why is None:
        note_time(p, "released")
    start_standby(p, records, key, lines, line)  # (for the next pass)
    if why is None:
        code = wait_done(sb, p.timeout)
        note_time(p, "tex-end")
        why = after_release(p, sb, record, code, root_stamp)
        if why is None:
            finish(p, sb)
            note_time(p, "moved")
            if not p.quiet:
                show_output(sb)
            remove(sb.dir)
            records.last("standby")
            return code
    sb.stop()
    records.say_once(f"doubt:{key}:{why}", f"not using the warm standby for this pass: {why}")
    records.last("normal", why)
    return None


def beside(p, records, key, sb, lines, line):
    """Run the pass normally, with standby sb (claimed, of a key not verified yet) beside it on the same inputs
    (seeded before the normal pass starts), to verify it (judge): here when it ends first, else in its keeper
    once it ends. Returns the normal pass's exit status."""
    started = time.time_ns()
    seed(p, sb)
    normal = p.popen()
    fd = ready(sb, time.monotonic() + READY_WAIT, normal)
    if fd is None:
        why = stopped(records, key, sb) if normal.poll() is None else "the normal pass ended first"
    else:
        why = before_release(p, sb, None)
        if why is not None:
            os.close(fd)
        elif not release(sb, fd):
            why = "its release failed"
    start_standby(p, records, key, lines, line)  # (for the next pass)
    try:
        status = wait_normal(p, normal)
    except TimedOut:
        sb.stop()
        raise
    note_time(p, "tex-end")
    records.last("verify", why)
    if why is not None or status != 0:
        sb.stop()
        return status
    write_json(sb.path("verify.json"), {"started": started, "pass": os.getpid()})
    try:
        shutil.copyfile(p.file(".pdf"), sb.path("reference.pdf"))
        shutil.copyfile(p.file(".fls"), sb.path("reference.fls"))
    except OSError:
        sb.stop()
        return status
    put(sb.path("claimed"), b"verify")  # (from here on its keeper judges it when its run ends, if it hasn't yet)
    if sb.done() is not None and judge(sb):
        merge(records, sb)
    return status


def stopped(records, key, sb):
    """Why a standby didn't get ready (its run stopped: an error in its preamble, say, recorded; or it ran out of time)."""
    done = sb.done()
    if done is None or not sb.keeper:
        return "it was not ready in time" if done is None else "it stopped before its release"
    reason, error = stopped_why(sb, sb.info, done.get("code"))
    refuse(records, key, {"reason": reason, "error": error, "inputs": preamble_reads(sb, sb.keeper["t0"])[0] if error else []})
    return "it stopped before its release"


def show_output(sb):
    """Print what the standby's TeX run printed, as a normal pass would have."""
    try:
        with open(sb.path("tex.out"), "rb") as f:
            sys.stdout.buffer.write(f.read(1 << 20))
        sys.stdout.flush()
    except OSError:
        pass


def attempt(p):
    """Run pass p (one a standby can run: p.why is None) with a standby when it can. Returns TeX's exit status, or
    None when the caller is to run it normally."""
    os.makedirs(p.folder(), exist_ok=True)
    records = Records(p)
    lines, line = p.preamble()
    if lines is None:
        records.say_once(f"preamble:{p.root}", f"no warm standby for {p.root}: {line}")
        records.last("normal", line)
        records.save()
        return None
    key = p.key(lines, line)
    taken, paths = None, records.paths(key)
    for sb in tidy(p, records, key):
        if taken is None and not outdated(sb, paths) and sb.claim(os.getpid()):
            taken = sb
        else:
            sb.stop()  # (only one waits; one whose preamble read a file that has changed since is no use)
    if taken is None:
        refused = records.find("refused", key)
        fresh = start_standby(p, records, key, lines, line)
        if fresh is None or records.find("verified", key) or not fresh.claim(os.getpid()):
            records.last("normal", refused["reason"] if refused else "no standby was waiting")
            records.save()
            note_time(p, "standby", "normal")
            return None
        taken = fresh  # (verified on this pass, beside it: see take)
    note_time(p, "standby", "verified" if records.find("verified", key) else "verify")

    def stopped(*_):
        taken.stop()
        sys.exit(143)
    previous = signal.signal(signal.SIGTERM, stopped)
    try:
        code = take(p, records, key, taken, lines, line)
    finally:
        signal.signal(signal.SIGTERM, previous)
        records.save()
    if code is None:
        note_time(p, "standby", "normal")
    return code


def run_pass(argv):
    """latexmkrc's pdflatex command: the pass argv (pdflatex and its arguments), with a standby when it can."""
    p = Pass(argv, os.environ)
    if p.why:
        p.run_plain()
    code = attempt(p)
    if code is None:
        p.run_plain()
    sys.exit(code)


# ---------------------------------------------------------------------------------------------------
# For latex_report.py: the notes-free pass.

def run_tex(argv, env, timeout=None, timing=None):
    """Run the notes-free pass argv (pdflatex and its arguments, -recorder among them; env: its environment, with
    LATEX_LIVE_EXEC; from the paper root) from a standby of its own when it can, as run_pass does the live build's
    passes, with TeX's output going nowhere. Returns TeX's exit status, or None when the caller is to run it
    normally (as without standbys); raises TimedOut past timeout (s). timing: (file, prefix, pass name) for its phases
    (note_time). One such pass at a time per job uses the standbys (a lock); another one waits for it, at most
    LOCK_WAIT, else runs normally."""
    p = Pass(argv, env, timing=timing, timeout=timeout, quiet=True)
    if p.why:
        return None
    try:
        os.makedirs(p.folder(), exist_ok=True)
        with open(os.path.join(p.folder(), f"{p.job}.lock"), "a") as lock:
            end = time.monotonic() + LOCK_WAIT
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() > end:
                        return None
                    time.sleep(0.02)
            return attempt(p)
    except TimedOut:
        raise
    except Exception:  # (a bug here must not stop the pass: it runs normally, which is always right)
        try:
            with open(os.path.join(p.folder(), "error.log"), "a") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + traceback.format_exc())
        except OSError:
            pass
        return None


def stop_taken(build, pid):
    """Stop the standbys that the pass pid took, or was verifying against, waiting until their keepers, TeX runs
    and containers are gone, and remove their folders (latex_report.py: once it has stopped an early notes-free
    pass, whose standby would otherwise stop a moment later). Run from the paper root."""
    folder = os.path.join(build, "standby")
    try:
        names = sorted(n for n in os.listdir(folder) if is_folder_of(n))
    except OSError:
        return
    taken = []
    for name in names:
        sb = Standby(os.path.join(folder, name))
        who = sb.claimed()
        if who == str(pid) or who == "verify" and (read_json(sb.path("verify.json")) or {}).get("pass") == pid:
            taken.append(sb)
    halt(taken)
    for sb in taken:
        if sb.info and not sb.info.get("host"):
            remove_containers("latex-live.run", sb.info["env"]["LATEX_LIVE_RUN"])


# ---------------------------------------------------------------------------------------------------
# For live.py.

def stop_standbys(paper_root, build, job):
    """Stop a document's standbys and remove their folders and containers, and forget what was said about
    them (live.py: when it starts, switches documents, rebuilds from scratch and stops)."""
    folder = os.path.join(paper_root, build, "standby")
    try:
        names = [n for n in os.listdir(folder) if is_folder_of(n, job)]
    except OSError:
        names = []
    halt([Standby(os.path.join(folder, name)) for name in names])
    remove_containers("latex-live.standby", label_of(paper_root, build, job))
    records = read_json(os.path.join(folder, f"{job}.json"))
    if records and records.get("said"):
        records["said"] = []
        write_json(os.path.join(folder, f"{job}.json"), records)


def halt(sbs):
    """Stop these standbys' keepers (or TeX runs whose keeper was killed), waiting for them (then SIGKILL), and
    remove their folders."""
    keepers = []
    for sb in sbs:
        name = os.path.basename(sb.dir)
        if sb.keeper_alive() and name in command_line(sb.keeper["pid"]):
            try:
                os.kill(sb.keeper["pid"], signal.SIGTERM)
                keepers.append(sb)
            except OSError:
                pass
        elif sb.keeper and alive(sb.keeper.get("tex")) and name in command_line(sb.keeper["tex"]):
            try:
                os.kill(sb.keeper["tex"], signal.SIGTERM)  # (a TeX run whose keeper was killed)
            except OSError:
                pass
    end = time.monotonic() + 8
    while keepers and time.monotonic() < end:
        keepers = [sb for sb in keepers if sb.keeper_alive()]
        time.sleep(0.05)
    for sb in keepers:
        try:
            os.kill(sb.keeper["pid"], signal.SIGKILL)
        except OSError:
            pass
    for sb in sbs:
        remove(sb.dir)


def main(argv):
    if argv[:1] == ["--keep"]:
        keep(argv[1])
        return
    try:
        run_pass(argv)
    except Exception as e:  # (a bug here must not stop the build: the pass runs normally, which is always right)
        build = os.path.normpath(os.environ.get("LATEX_LIVE_BUILD", "build"))
        try:
            with open(os.path.join(build, "standby", "error.log"), "a") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + traceback.format_exc())
        except OSError:
            pass
        say(f"the warm standby failed ({type(e).__name__}: {e}; see {build}/standby/error.log); running the pass normally")
        sys.stdout.flush()
        argv = [*shlex.split(os.environ.get("LATEX_LIVE_EXEC", "")), *argv]
        os.execvp(argv[0], argv)


if __name__ == "__main__":
    main(sys.argv[1:])
