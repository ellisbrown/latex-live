"""Running programs on the paper's files: TeX tools in the TeX Live image (confined in shared mode,
through tex-exec), SyncTeX and poppler, each in a slot (SPAWNS) and under a deadline, and stopping a
shared instance's containers; and the bounded runner that these, git and the agent plugin share (run_bounded)."""

import collections
import contextlib
import itertools
import os
import signal
import resource
import shlex
import shutil
import subprocess
import sys
import threading
import time

from . import config
from .files import redact_root
from .standby import remove_containers


def image_pulled():
    """Whether podman has the TeX Live image (~30 ms). Checked per use, not once: it can be removed
    (e.g. by a cleanup job) or pulled while the server runs."""
    return bool(shutil.which("podman")) and subprocess.run(["podman", "image", "exists", config.TEXLIVE_IMAGE]).returncode == 0


def failure(server, e):
    """What a build made on request that failed says in the viewer (the downloads: build_export, build_review; the
    baselines and the diff: build_base, build_diff), in 300 characters at most. In shared mode, where coauthors read
    it, without the server's folders (TeX names the paper's files relative to it, but Python's errors name them in
    full) or the command line of a TeX run that took too long."""
    if config.SHARED and isinstance(e, subprocess.TimeoutExpired):
        return f"a TeX run took more than {e.timeout / 60:g} minutes"
    text = str(e)
    if config.SHARED:
        text = redact_root(text, server.paper_root).replace(config.HERE + os.sep, "latex-live" + os.sep).replace(config.HERE, "latex-live")
    return text[:300]


def synctex_unavailable():
    """Why SyncTeX can't run (no host synctex, and no TeX Live image to run it in), else None."""
    if shutil.which("synctex") or image_pulled():
        return None
    return config.IMAGE_MISSING if shutil.which("podman") else "no synctex: install TeX Live (MacTeX on macOS)"


def image_problem(server):
    """For the viewer: IMAGE_MISSING while builds or SyncTeX need the image and it is missing, or why
    SyncTeX can't run on a host-TeX setup; else None. Checked at most every 5 s (the viewer polls
    twice a second)."""
    if not server.tex_exec and shutil.which("synctex"):
        return None
    if time.time() - server.image_checked > 5:
        server.image_checked = time.time()
        server.image_note = synctex_unavailable() if not server.tex_exec else None if image_pulled() else config.IMAGE_MISSING
    return server.image_note


# At most this many SyncTeX runs and builds made on request at once (run_synctex, the downloads, the
# baselines, the diff): each request may start one, often a container, and in shared mode any coauthor
# can send requests, so they must not pile up on the owner's machine. In shared mode, where latex-live also
# runs the build itself, one of the four slots is the live build's own (LIVE_SPAWN, shared_tool), so that
# downloads can't keep it waiting; the notes-free pass takes one of the other three. Nothing that holds
# a slot waits for anything a waiter holds: a slot is taken around one tool run, with no lock held.
SPAWNS = threading.BoundedSemaphore(3 if config.SHARED else 4)
LIVE_SPAWN = threading.BoundedSemaphore(1)
RUN_SEQ = itertools.count(1)  # (run_tool: each run's own container label)


def run_tool(argv, cwd, timeout=600, kind="tool", env=None, capture=0, slot=None, preexec_fn=None, group=False,
             started=None, stop_failed=False):
    """Run a tool (argv: with its container's prefix, tex-exec or podman, if any) in a slot (SPAWNS, or
    slot), with stdin and stderr closed and stdout discarded, or with capture, its first capture bytes
    kept (run_bounded): (its exit status, or None once it ran past timeout seconds; its output). In shared mode its
    container gets a label of its own (LATEX_LIVE_RUN) and the same deadline (LATEX_LIVE_TIMEOUT, which
    tex-exec gives podman), and past the timeout (with stop_failed, whenever it fails) it is removed before the slot is
    let go: stopping the podman client alone would leave it running. With group, it runs in a process group of its own,
    all of which is stopped past the timeout or once it exits (a shell that runs tools in turn: stopping the shell would
    leave the one it runs, and its hold on the output). started, if given, is called with the process once it runs."""
    label = f"{kind}-{next(RUN_SEQ)}"
    env = {**(os.environ if env is None else env), "LATEX_LIVE_RUN": label, "LATEX_LIVE_TIMEOUT": str(int(timeout))}
    with slot or SPAWNS:
        ran = run_bounded(argv, timeout, cwd=cwd, env=env, limit=capture or None, group=group, drain=10,
                          preexec_fn=preexec_fn, started=started)
        if ran.code is None or stop_failed and ran.code != 0:
            stop_containers(label)
    return ran.code, ran.out.decode("utf-8", errors="replace")


Ran = collections.namedtuple("Ran", "code out err more open")


def run_bounded(argv, timeout, cwd=None, env=None, data=None, limit=None, stop_at_limit=False, err_kept=0, group=False,
                grace=0, drain=2, preexec_fn=None, started=None):
    """Run argv for at most timeout seconds, with what it prints bounded: Ran(code: its exit status, or None once it ran
    past timeout; out: the first limit bytes of its stdout, which is discarded with limit None; err: the last err_kept
    bytes of its stderr, else it is closed; more: it printed more than limit bytes, and with stop_at_limit it was stopped
    then; open: what it started still holds its stdout open). Its stdin is data, else closed. Each pipe has a thread of
    its own, so that none fills while another waits. Past the timeout it is killed; with group, it runs in a process
    group of its own, which gets SIGTERM past the timeout and, grace seconds later and in any case once it exits,
    SIGKILL: nothing it started outlives it. The readers are waited for drain seconds at most (a child that left the
    group may hold a pipe open; each reader closes its pipe itself). started, if given, is called with the process
    once it runs. OSError if it doesn't start."""
    proc = subprocess.Popen(argv, cwd=cwd, env=env, preexec_fn=preexec_fn, start_new_session=group,
                            stdin=subprocess.DEVNULL if data is None else subprocess.PIPE,
                            stdout=subprocess.DEVNULL if limit is None else subprocess.PIPE,
                            stderr=subprocess.PIPE if err_kept else subprocess.DEVNULL)
    if started:
        started(proc)
    out, err, over = bytearray(), bytearray(), threading.Event()
    state = {"lock": threading.Lock(), "finished": False}  # (finished: nothing may signal its group any more)

    def stop():
        with state["lock"]:
            if not state["finished"]:
                kill_group(proc.pid, signal.SIGKILL) if group else proc.kill()

    def feed():
        try:
            proc.stdin.write(data)
        except OSError:  # (it stopped reading: what it printed says why)
            pass
        finally:
            with contextlib.suppress(OSError):
                proc.stdin.close()

    def read_out():
        with proc.stdout:  # (closed by its reader, even when that ends only after this call: an escaped child held it)
            while chunk := proc.stdout.read1(1 << 16):
                room = max(0, limit - len(out))
                out.extend(chunk[:room])
                if len(chunk) > room:
                    over.set()
                    if stop_at_limit:
                        return stop()

    def read_err():
        with proc.stderr:
            while chunk := proc.stderr.read1(1 << 16):
                err.extend(chunk)
                del err[:-err_kept]

    feeder, reader, err_reader = [threading.Thread(target=f, daemon=True) if wanted else None
                                  for f, wanted in ((feed, data is not None), (read_out, limit is not None), (read_err, err_kept))]
    for t in (feeder, reader, err_reader):
        if t:
            t.start()
    timed_out = False
    try:
        proc.wait(timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        if not group:
            proc.kill()
        elif grace:
            kill_group(proc.pid, signal.SIGTERM)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(grace)
    finally:
        with state["lock"]:  # (what it left running, which may hold its pipes open; the group's id is free for another
            if group:        # process only once none of the group is left, and pids are handed out in turn, so this
                kill_group(proc.pid, signal.SIGKILL)  # signals nothing else; after this, stop() signals nothing)
            state["finished"] = True
        proc.wait()
        end = time.monotonic() + drain
        for t in (reader, err_reader, feeder):  # (the readers first: a feed held up by an escaped child waits last)
            if t:
                t.join(max(0, end - time.monotonic()))
    return Ran(None if timed_out else proc.returncode, bytes(out),
               b"" if err_reader and err_reader.is_alive() else bytes(err), over.is_set(), bool(reader and reader.is_alive()))


def kill_group(pid, sig):
    with contextlib.suppress(ProcessLookupError, PermissionError):  # (gone already)
        os.killpg(pid, sig)


def tex_argv(server, *argv):
    """argv for a TeX tool as the server runs it: through its tex_exec (tex-exec in the TeX Live image,
    confined in shared mode), or on the host when that is empty (--host-tex)."""
    return [*shlex.split(server.tex_exec or ""), *argv]


def pdf_pages_text(pdf):
    """Plain text of each page (pdftotext), or [] without poppler."""
    out = pdf_tool(["pdftotext", "-q", pdf, "-"])
    return out.split("\f") if out else []


def pdf_tool(argv, timeout=60):
    """What a poppler tool (pdfinfo, pdffonts, pdfimages, pdftotext) prints about a PDF the build made, or
    "" without poppler, or once it has run for timeout seconds; at most OUTPUT_LIMIT bytes of it. In shared
    mode, where the paper's TeX made the PDF and poppler parses it on this machine, also with at most 1 GB of
    memory (but on macOS, whose programs map more than that before they start) and that much CPU time, or less
    where the hard limits are lower; a limit the system refuses is left out."""
    wanted = [(resource.RLIMIT_CPU, max(1, int(timeout)))] + ([] if sys.platform == "darwin" else [(resource.RLIMIT_AS, 1 << 30)])

    def limits():
        for kind, value in wanted:
            try:
                hard = resource.getrlimit(kind)[1]
                value = value if hard == resource.RLIM_INFINITY else min(value, hard)
                resource.setrlimit(kind, (value, value))
            except (ValueError, OSError):
                pass
    try:
        code, out = run_tool(argv, None, timeout, kind="poppler", capture=config.OUTPUT_LIMIT, slot=contextlib.nullcontext(),
                             preexec_fn=limits if config.SHARED else None)
    except (OSError, subprocess.SubprocessError):  # (no poppler; or the child failed before it ran it)
        return ""
    return out if code is not None else ""


def container_argv(paper_root):
    """Argument list that runs a tool in the TeX Live image (for subprocess, with cwd = paper root). In
    shared mode, through tex-exec, which confines it there (LATEX_LIVE_SANDBOX) and never falls back to
    the host's TeX (LATEX_LIVE_REQUIRE_IMAGE)."""
    if config.SHARED:
        return [os.path.join(config.HERE, "tex-exec"), config.TEXLIVE_IMAGE, os.path.realpath(paper_root)]
    return ["podman", "run", "--rm", "--init", "--pull=never", "--network=none", "--security-opt", "label=disable",
            "-v", f"{paper_root}:{paper_root}", "-w", os.path.realpath(paper_root), "-e", "max_print_line", config.TEXLIVE_IMAGE]


def container_exec(paper_root):
    """Command prefix that runs a TeX tool in the TeX Live image (see tex-exec), or on the host
    while the image is missing."""
    return f"'{os.path.join(config.HERE, 'tex-exec')}' {shlex.quote(config.TEXLIVE_IMAGE)} '{paper_root}'"


def stop_containers(run=None, kind=None):
    """Stop this shared instance's containers (tex-exec labels them with INSTANCE), or one run's (its
    LATEX_LIVE_RUN label, e.g. "main-12"), or those of one kind of run ("main", "clean"), at once. Once
    the podman client is stopped, a container keeps running; this stops the container itself. In every state,
    not only running: a client stopped between creating its container and starting it (as stopping the
    instance stops its process group) leaves the container created, which nothing else would remove."""
    if not config.SHARED:
        return
    remove_containers(f"latex-live.instance={config.INSTANCE}",
                      *([f"latex-live.run={run}"] if run else [f"latex-live.kind={kind}"] if kind else []))
