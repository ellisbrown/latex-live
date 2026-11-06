"""Running programs on the paper's files: TeX tools in the TeX Live image (confined in shared mode,
through tex-exec), SyncTeX and poppler, each in a slot (SPAWNS) and under a deadline, and stopping a
shared instance's containers."""

import contextlib
import itertools
import os
import signal
import resource
import shlex
import shutil
import subprocess
import threading
import time

from . import config


def image_pulled():
    """Whether podman has the TeX Live image (~30 ms). Checked per use, not once: it can be removed
    (e.g. by a cleanup job) or pulled while the server runs."""
    return bool(shutil.which("podman")) and subprocess.run(["podman", "image", "exists", config.TEXLIVE_IMAGE]).returncode == 0


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


def run_tool(argv, cwd, timeout=600, kind="tool", env=None, capture=0, slot=None, preexec_fn=None, group=False):
    """Run a tool (argv: with its container's prefix, tex-exec or podman, if any) in a slot (SPAWNS, or
    slot), with stdin and stderr closed and stdout discarded, or with capture, its first capture bytes
    kept: (its exit status, or None once it ran past timeout seconds; its output). In shared mode its
    container gets a label of its own (LATEX_LIVE_RUN) and the same deadline (LATEX_LIVE_TIMEOUT, which
    tex-exec gives podman), and past the timeout it is removed before the slot is let go: stopping the
    podman client alone would leave it running. With group, it runs in a process group of its own, all of
    which is stopped past the timeout (a shell that runs tools in turn: stopping the shell would leave the
    one it runs, and its hold on the output)."""
    label = f"{kind}-{next(RUN_SEQ)}"
    env = {**(os.environ if env is None else env), "LATEX_LIVE_RUN": label, "LATEX_LIVE_TIMEOUT": str(int(timeout))}
    out = bytearray()
    with slot or SPAWNS:
        proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                stdout=subprocess.PIPE if capture else subprocess.DEVNULL, preexec_fn=preexec_fn,
                                start_new_session=group)
        reader = None
        if capture:
            def read():  # (all of it is read, so that the tool never waits on a full pipe; the first capture bytes kept)
                for chunk in iter(lambda: proc.stdout.read(1 << 16), b""):
                    out.extend(chunk[:max(0, capture - len(out))])
            reader = threading.Thread(target=read, daemon=True)
            reader.start()
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if group:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    pass
            proc.kill()
            proc.wait()
            stop_containers(label)
            code = None
        if reader:
            reader.join(10)
            proc.stdout.close()
    return code, out.decode("utf-8", errors="replace")


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
    memory and that much CPU time."""
    def limits():
        resource.setrlimit(resource.RLIMIT_AS, (1 << 30, 1 << 30))
        resource.setrlimit(resource.RLIMIT_CPU, (timeout, timeout))
    try:
        code, out = run_tool(argv, None, timeout, kind="poppler", capture=config.OUTPUT_LIMIT, slot=contextlib.nullcontext(),
                             preexec_fn=limits if config.SHARED else None)
    except OSError:
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
    return f"'{os.path.join(config.HERE, 'tex-exec')}' {config.TEXLIVE_IMAGE} '{paper_root}'"


def stop_containers(run=None, kind=None):
    """Stop this shared instance's containers (tex-exec labels them with INSTANCE), or one run's (its
    LATEX_LIVE_RUN label, e.g. "main-12"), or those of one kind of run ("main", "clean"), at once. Once
    the podman client is stopped, a container keeps running; this stops the container itself. In every state,
    not only running: a client stopped between creating its container and starting it (as stopping the
    instance stops its process group) leaves the container created, which nothing else would remove."""
    if not config.SHARED:
        return
    filters = ["--filter", f"label=latex-live.instance={config.INSTANCE}"]
    filters += ["--filter", f"label=latex-live.run={run}"] if run else ["--filter", f"label=latex-live.kind={kind}"] if kind else []
    try:
        ids = subprocess.run(["podman", "ps", "-aq", *filters], capture_output=True, text=True, timeout=30).stdout.split()
        if ids:
            subprocess.run(["podman", "rm", "-f", "-t", "0", *ids], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        pass
