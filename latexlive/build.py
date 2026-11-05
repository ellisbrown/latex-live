"""The live build of the owner's previews: latexmk -pvc for the current document, started and
stopped on the main thread, rebuilds and document switches, and the build's state."""

import ctypes
import os
import signal
import subprocess
import sys
import threading
import time

from . import config
from .files import clean_file, job_file, mtime, read_json
from .instances import record_port, take_lock
from .latex import root_docs


def build_running(server, pdf_name):
    """Whether latexmk (in shared mode, shared_build) is building pdf_name now: its passes may still publish
    it before the run ends."""
    if config.SHARED:
        return pdf_name == server.default_pdf and server.shared["building"]
    root = server.paper_root
    return mtime(job_file(root, pdf_name, ".building")) > mtime(job_file(root, pdf_name, ".status.json"))


def build_status(server, pdf_name, clean=False):
    """The last run's result (latex_report.py's build/<job>.status.json), or with clean, the notes-free
    pass's; in shared mode, shared_build's, kept in memory (the paper's TeX can write those files)."""
    if config.SHARED:
        return server.shared["clean" if clean else "status"] if pdf_name == server.default_pdf else None
    return read_json((clean_file if clean else job_file)(server.paper_root, pdf_name, ".status.json"))


def set_doc(server, doc):
    """Serve doc as the document being built, and remember it as the last one (pick_doc)."""
    old = getattr(server, "default_pdf", "")[:-4]
    if old and old != doc:  # (its viewers' hub URLs still name it: HubServer.find)
        server.was = [d for d in getattr(server, "was", []) if d != doc] + [old]
    server.default_pdf = f"{doc}.pdf"
    server.bases, server.base_checked = {"head": {}, "push": {}}, {"head": 0, "push": 0}
    if server.diff.get("state") != "running":
        server.diff = {}
    with open(os.path.join(server.paper_root, config.BUILD, "latex-live-doc"), "w") as f:
        f.write(doc)


def start_latexmk(server, force=False):
    """latexmk -pvc for the current document. Main thread only: see request_switch.

    After a failed build, latexmk only reports the old failure until a source changes; -g (also
    with force) makes it build again, e.g. after a restart, or once latex_report.py has removed a
    damaged .aux."""
    if server.no_build or config.SHARED:  # (shared mode never runs latexmk: shared_build)
        return
    doc = server.default_pdf[:-4]
    force = force or (read_json(job_file(server.paper_root, server.default_pdf, ".status.json")) or {}).get("ok") is False
    env = {**server.env, "LATEX_LIVE_DOC": doc}
    if config.PUBLISH:
        env["LATEX_LIVE_PUBLISH"] = os.path.join(config.PUBLISH, server.default_pdf)  # latex_report.py
    # On Linux, PR_SET_PDEATHSIG: latexmk gets SIGTERM if this process dies, however it dies.
    # Elsewhere (macOS), main's signal handlers stop it.
    server.latexmk = subprocess.Popen(
        ["latexmk", "-r", os.path.join(config.HERE, "latexmkrc"), "-pvc", *(["-g"] if force else []), f"-jobname={doc}-live", doc],
        env=env,
        preexec_fn=(lambda: ctypes.CDLL("libc.so.6").prctl(1, signal.SIGTERM)) if sys.platform.startswith("linux") else None,
    )


def retry_build(server):
    """Rebuild from scratch when latex_report.py asks (build/<doc>-live.retry): the .aux was cut off,
    or came from another TeX installation. Removes the job's generated files (latexmk would rebuild
    its rules from an old .log or .fls), so latexmk starts as on a first run. At most once a minute, in case a source keeps causing it."""
    retry = job_file(server.paper_root, server.default_pdf, ".retry")
    if not os.path.exists(retry):
        return
    os.remove(retry)
    if time.monotonic() - server.retried < 60:
        print("=== latex-live: asked to rebuild again within a minute; not rebuilding by itself (save a file to retry)",
              flush=True)
        return
    server.retried = time.monotonic()
    stop_latexmk(server)
    sys.path.insert(0, config.HERE)
    import latex_report
    for ext in (*latex_report.AUX_EXTS, ".fdb_latexmk", ".fls", ".log", ".bbl", ".blg"):
        try:
            os.remove(job_file(server.paper_root, server.default_pdf, ext))
        except OSError:
            pass
    start_latexmk(server, force=True)


def stop_latexmk(server):
    proc, server.latexmk = server.latexmk, None
    if proc:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def on_main_thread(server, job):
    """Run job(server) -> (status, body) on the main thread and return its result. Jobs that
    (re)start latexmk must run there: its parent-death signal fires when the thread that started
    it exits, so a request thread must not start it."""
    done, result = threading.Event(), {}
    server.requests.put((job, result, done))
    if not done.wait(30):
        return 504, {"error": "this is taking a while; reload in a moment"}
    return result["status"], result["body"]


def request_switch(server, query):
    """/switch?doc=NAME: build another root document instead (switch_doc)."""
    doc = query.get("doc", [""])[0]
    if doc not in root_docs(server.paper_root):
        return 404, {"error": f"{doc}.tex is not a root document here"}
    return on_main_thread(server, lambda server: switch_doc(server, doc))


def switch_doc(server, doc):
    """Stop building the current document and build doc. Returns (status, body) for /switch."""
    if config.SHARED:  # (a shared instance builds one document, and never with latexmk)
        return 403, {"error": f"this preview shares {server.default_pdf} only"}
    if doc == server.default_pdf[:-4]:
        return 200, {"doc": doc}
    lock, other = take_lock(doc)
    if not lock:
        return 409, {"error": f"{doc}.tex is open in another latex-live instance (port {other.get('port', '?')})",
                     "port": other.get("port")}
    stop_latexmk(server)
    server.lock.close()
    server.lock = lock
    set_doc(server, doc)
    record_port(server)
    start_latexmk(server)
    print(f"=== latex-live: now building {doc}.tex", flush=True)
    return 200, {"doc": doc}


def rebuild_doc(server):
    """POST /rebuild: build the document again from the top (latexmk -g), e.g. after another build
    replaced its PDF, which then no longer matches the SyncTeX data. Returns (status, body)."""
    if server.no_build:
        return 409, {"error": "this instance only serves the viewer (--no-build)"}
    stop_latexmk(server)
    start_latexmk(server, force=True)
    print(f"=== latex-live: rebuilding {server.default_pdf[:-4]}.tex (from the viewer)", flush=True)
    return 202, {"doc": server.default_pdf[:-4]}
