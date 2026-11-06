"""Starting an instance: the command line, the checks before shared mode starts, the server and
its state (serve), and the main loop, which runs latexmk and stops it."""

import argparse
import collections
import os
import queue
import shutil
import signal
import sys
import threading
import time

from . import DESCRIPTION, config
from .auth import load_token, load_users, users
from .build import retry_build, set_doc, start_latexmk, stop_doc_standbys, stop_latexmk
from .commits import commit_at_exit, start_commits
from .files import pdf_path
from .handler import Handler
from .hub import hub_path, hub_port, hub_watch, replace_stuck_hub
from .instances import instance_answers, instances, paper_id, record_port, runs_here, stop_instance, take_lock
from .latex import paper_root_of, pick_doc, root_docs
from .navigation import open_in_editor
from .shared_build import start_shared_build
from .shared_watch import watch_build_size
from .status import code_updated
from .timing import start_timing
from .tools import container_exec, image_pulled, stop_containers
from .web import DualStackServer, LoopbackServer, taken_port_advice


def serve(paper_root, default_pdf, port, page_limit):
    """The instance's server, set up; main starts serving (server.serve_forever in a thread)."""
    # A small range, for tunnels that forward only a few ports. A shared instance takes exactly its
    # port: the proxy sends coauthors there, and must not reach another preview that holds it.
    for p in [port] if config.SHARED else range(port, port + 10):
        try:
            server = (LoopbackServer if config.SHARED else DualStackServer)(("::", p), Handler)
            break
        except OSError:
            continue
    else:
        sys.exit((f"latex-live: port {port} is taken; a shared instance serves exactly --port." if config.SHARED
                  else f"latex-live: no free port in {port}-{port + 9}.") + taken_port_advice(port))
    server.daemon_threads = True
    server.git_name = None  # (owner_name)
    server.paper_root = paper_root
    server.paper_id = paper_id(paper_root)  # (checked against the hub's requests: Handler.misdirected)
    server.default_pdf = default_pdf
    server.page_limit = page_limit
    server.diff = {}
    server.export, server.review = {}, {}  # (start_export, start_review)
    server.download_lock = threading.Lock()  # (their states: checked and started at once, never over a newer one)
    server.review_lock = threading.Lock()  # (build_review)
    server.export_lock, server.exported = threading.Lock(), {}  # (exact_export)
    server.bases, server.base_checked = {"head": {}, "push": {}}, {"head": 0, "push": 0}  # (base_status)
    server.base_locks = {"head": threading.Lock(), "push": threading.Lock()}
    # The tex-exec prefix, as for latexmk (main sets it). In shared mode the confined image from the
    # start: a request (an export, a baseline) may come before main gets there, and must not run the host's TeX.
    server.tex_exec = container_exec(os.path.realpath(paper_root)) if config.SHARED else ""
    server.image_checked, server.image_note = 0, None
    server.latexmk, server.lock, server.stopping, server.retried, server.no_build = None, None, False, -60, False
    server.requests = queue.Queue()  # document switches and rebuilds, for the main thread (on_main_thread)
    server.editable = None  # (editable_files)
    server.versions, server.versions_lock = collections.OrderedDict(), threading.Lock()  # (remember_version)
    server.last_saves = collections.OrderedDict()  # (save_file: by user, file and editor, the last 256)
    server.last_writer = {}  # (wrote)
    server.autocommit, server.pending, server.write_seq, server.commit_said = False, {}, 0, None  # (remember_write)
    server.pending_lock = threading.Lock()
    server.pending_file = os.path.join(config.STATE, "pending", f"{server.paper_id}.json")  # (save_pending)
    server.git_dir = None  # (git_dir)
    server.results, server.results_lock = {}, threading.Lock()  # (shared_result)
    server.checks, server.checks_running, server.checks_lock = {}, {}, threading.Lock()  # (cached_checks)
    # shared_build's state: whether a run is on, its status and the notes-free pass's, what it published, the
    # tools' states (bib, biber, idx) and the rebuilds' generation (gen), the files it missed or depends on beyond
    # its file list (missing, deps), and the requests to build again (POST /rebuild: admit; at most every 20 s
    # for coauthors), and what stopped runs (overflow, no_image).
    server.shared = {"building": False, "status": None, "clean": None, "published": None, "wanted": threading.Event(),
                     "force": False, "rebuilt": -60, "proc": None, "clean_seq": 0, "bib": None, "biber": None, "idx": None,
                     "gen": 0, "retried": -60, "overflow": 0, "overflows": 0, "no_image": False, "missing": [], "deps": [],
                     "admit": threading.Lock(), "clean_lock": threading.Lock()}
    server.token = load_token()
    return server


def print_urls(port, path="/"):
    token = load_token()
    print(f"=== latex-live: viewer (on a remote machine, forward port {port} first): http://localhost:{port}{path}?t={token}", flush=True)
    if (host := os.environ.get("LATEX_LIVE_HOST")) and not config.SHARED:  # (a shared instance answers only on this machine)
        print(f"=== latex-live: viewer, direct: http://{host}:{port}{path}?t={token}", flush=True)


def print_shared_urls(port):
    """Shared mode: the link for coauthors, with no token in it (the proxy names them; a personal link
    adds ?t= with the user's own token), then the owner's own on this machine, also without its token:
    a shared instance usually runs as a service, whose output goes to a log that others may read."""
    where = f"{config.PUBLIC_ORIGIN}/" if config.PUBLIC_ORIGIN else "the proxy's address (LATEX_LIVE_PUBLIC_URL would print it here)"
    print(f"=== latex-live: shared with the users in {config.USERS_FILE}: {where} (a personal link adds ?t=<their token>)", flush=True)
    print(f"=== latex-live: on this machine: http://localhost:{port}/ (add your token the first time: it is in {config.TOKEN_FILE})",
          flush=True)


def check_shared(args):
    """Before a shared instance starts: stop on a setting it can't run with (SHARED_PROBLEMS, the users
    file, host TeX, the owner's own state folder, the identity header without the proxy's uid, or off
    Linux, where that can't be checked), warn about weak ones, confine every TeX run from here on
    (tex-exec reads these variables; every run passes the environment on), and drop git's variables
    from the environment, which could point the commits elsewhere or sign them as someone else."""
    if config.SHARED_PROBLEMS:
        sys.exit("\n".join(f"latex-live: {problem}" for problem in config.SHARED_PROBLEMS))
    try:
        load_users(config.USERS_FILE)
    except (OSError, ValueError) as e:
        sys.exit(f"latex-live: the users file {config.USERS_FILE}: {e}")
    users()  # (warns if others can read it)
    if args.host_tex or not shutil.which("podman"):
        sys.exit("latex-live: a shared instance builds only in the TeX Live image (coauthors write the sources), "
                 + ("so not with --host-tex" if args.host_tex else "with rootless podman, which is not installed here"))
    if os.path.realpath(config.STATE) == os.path.realpath(config.DEFAULT_STATE):
        sys.exit(f"latex-live: a shared instance needs a state folder of its own (LATEX_LIVE_STATE), not {config.DEFAULT_STATE}: there"
                 " it would share your previews' token and list itself among them")
    if config.IDENTITY_HEADER and config.PROXY_UID is None:
        sys.exit(f"latex-live: with LATEX_LIVE_IDENTITY_HEADER, set LATEX_LIVE_PROXY_UID to the uid the proxy runs as: any"
                 f" program on this machine can send {config.IDENTITY_HEADER} and act as any user in the users file")
    if config.IDENTITY_HEADER and not os.path.exists("/proc/net/tcp"):
        sys.exit(f"latex-live: LATEX_LIVE_IDENTITY_HEADER needs Linux (its /proc/net/tcp tells which uid sent a request), so that"
                 f" only the proxy can send {config.IDENTITY_HEADER}; elsewhere, coauthors use the token links")
    if config.IDENTITY_HEADER and not config.PUBLIC_ORIGIN:
        print("=== latex-live: without LATEX_LIVE_PUBLIC_URL, coauthors who come through the proxy can read but not comment"
              " or edit (their browser's Origin can't be checked)", flush=True)
    os.environ.update(LATEX_LIVE_REQUIRE_IMAGE="1", LATEX_LIVE_SANDBOX="1", LATEX_LIVE_INSTANCE=config.INSTANCE)
    for key in [key for key in os.environ if key.startswith("GIT_")]:  # (GIT_DIR, GIT_INDEX_FILE, GIT_AUTHOR_NAME, ...:
        del os.environ[key]                                            # the commits go to the paper's own checkout, as theirs)


def print_hub_urls(doc, fallback_port, wait=0):
    """The doc's viewer URLs through the hub (waiting up to wait seconds for one to serve it),
    else on fallback_port."""
    end = time.monotonic() + wait
    while not (hub := hub_port()) and time.monotonic() < end:
        time.sleep(0.2)
    if hub:
        print_urls(hub, hub_path(paper_id(os.getcwd()), doc))
    else:
        print_urls(fallback_port)


def main():
    ap = argparse.ArgumentParser(description=DESCRIPTION)
    ap.add_argument("doc", nargs="?", help="root .tex file (with or without extension); default: the one whose"
                    " sources were edited last. The viewer switches between root files. A path (e.g. the editor's"
                    " current file) runs in that file's paper: it builds the file if it is a root one.")
    ap.add_argument("--version", action="version", version=f"latex-live {config.VERSION}")
    ap.add_argument("--port", type=int, help="the hub's port, which serves every preview (default:"
                    " $LATEX_LIVE_PORT, else the number in ~/.config/latex-live/port, else 44100); each preview also has"
                    " its own, from the next one. A shared instance (LATEX_LIVE_USERS) serves exactly this port, without"
                    " the hub, and needs it")
    ap.add_argument("--page-limit", type=int, help="main-text page limit to check in the viewer (default: the venue's,"
                    " for ICLR, NeurIPS, COLM, ICML, CVPR and ICCV styles)")
    ap.add_argument("--build-dir", default="build", help="build directory, relative to the paper root (default: build);"
                    " any other also receives <doc>.pdf, e.g. to run beside the usual instance")
    ap.add_argument("--no-build", action="store_true", help="viewer only; don't run latexmk")
    ap.add_argument("--no-open", action="store_true", help="don't open the PDF in a VS Code editor tab")
    ap.add_argument("--host-tex", action="store_true", help="use the host's TeX installation, not the TeX Live 2024 image")
    ap.add_argument("--replace", action="store_true", help="stop another instance building the same document and run here"
                    " (one that no longer answers is replaced anyway)")
    args = ap.parse_args()
    if args.port is None:
        if config.SHARED:  # (it would otherwise take the hub's port, which the owner's previews use)
            sys.exit("latex-live: a shared instance (LATEX_LIVE_USERS) needs --port: the port its proxy connects to")
        args.port = config.default_port()
    config.BUILD = os.environ["LATEX_LIVE_BUILD"] = os.path.normpath(args.build_dir)
    config.PUBLISH = "" if config.BUILD == "build" else config.BUILD
    if config.SHARED:
        check_shared(args)

    if args.doc and os.path.dirname(args.doc):  # a path: run in its paper
        path = os.path.abspath(args.doc)
        root = paper_root_of(path)
        if not root:
            sys.exit(f"latex-live: no root .tex file in or above {os.path.dirname(path)}")
        os.chdir(root)
        name = os.path.basename(path)
        args.doc = name if os.path.dirname(path) == root and name[:-4] in root_docs(root) else None

    docs = root_docs(os.getcwd())
    if args.doc:
        doc = args.doc[:-4] if args.doc.endswith(".tex") else args.doc
        if not os.path.isfile(f"{doc}.tex"):
            sys.exit(f"latex-live: {doc}.tex not found in {os.getcwd()}")
    elif docs:
        doc = pick_doc(os.getcwd(), docs)
    else:
        sys.exit(f"latex-live: no .tex file with a \\documentclass in {os.getcwd()}")

    # One instance per document; the lock file records the viewer port and process for later instances.
    os.makedirs(config.BUILD, exist_ok=True)
    lock, other = take_lock(doc)
    if not lock:
        port, pid, state = other.get("port"), other.get("pid"), other.get("state")
        holder = f"process {pid}, port {port}" if pid else f"port {port}"
        if port and instance_answers(port) and not args.replace:
            print(f"=== latex-live: already running for {doc}.tex ({holder}; another terminal or window)", flush=True)
            listed = any(e["pid"] == pid and e.get("id") for e in instances())  # (the port in build/ is a hint: take_lock)
            if isinstance(state, str) and os.path.realpath(state) != os.path.realpath(config.STATE):  # (its token is not ours)
                where = state if state.isprintable() else "another folder"
                print(f"=== latex-live: it keeps its state in {where} (LATEX_LIVE_STATE), so its link is not printed here", flush=True)
            elif config.SHARED:
                print_shared_urls(port)
            elif listed:
                print_hub_urls(doc, port)
            else:  # (not in this user's list of instances: its port is not trusted with the token)
                print(f"=== latex-live: it is not in {config.INSTANCES}, so its link is not printed here", flush=True)
            print("=== latex-live: --replace stops that one and runs here", flush=True)
            return
        if not pid:
            sys.exit(f"latex-live: {doc}.tex is held by an instance that is not answering ({holder}); stop it and retry")
        if not runs_here(pid, os.getcwd()):
            sys.exit(f"latex-live: {doc}.tex is locked, but the process its lock file names ({pid}) is not latex-live working in"
                     f" {os.getcwd()}, so it is not stopped; stop the instance for {doc}.tex yourself and retry")
        print(f"=== latex-live: stopping the instance for {doc}.tex ({holder})"
              + ("" if args.replace else ", which is not answering"), flush=True)
        if not stop_instance(pid, os.getcwd()):
            sys.exit(f"latex-live: could not stop process {pid}; stop it and retry")
        for _ in range(50):  # (an exiting instance stops looking live a moment before it lets go of the lock)
            lock, other = take_lock(doc)
            if lock:
                break
            time.sleep(0.1)
        else:
            sys.exit(f"latex-live: {doc}.tex is still locked; retry")
    if not config.SHARED:  # (a shared instance takes no part in the hub)
        replace_stuck_hub()
    server = serve(os.getcwd(), f"{doc}.pdf", args.port if config.SHARED else args.port + 1, args.page_limit)  # (the hub has --port)
    code_updated()  # (what this process runs)
    server.lock = lock
    set_doc(server, doc)
    record_port(server)
    start_timing(server)
    if config.SHARED:  # (what the last run left uncommitted is committed before the first request: start_commits)
        start_commits(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print_shared_urls(server.server_address[1])
    else:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        threading.Thread(target=hub_watch, args=(args.port,), daemon=True).start()
        print_hub_urls(doc, server.server_address[1], wait=3)
        if os.environ.get("LATEX_LIVE_COMMIT", "").strip() == "1":
            print("=== latex-live: LATEX_LIVE_COMMIT works in shared mode only (LATEX_LIVE_USERS), so nothing is committed",
                  flush=True)
    others = [d for d in docs if d != doc]
    print(f"=== latex-live {config.VERSION}: building {doc}.tex" + (f"; the viewer switches to {', '.join(others)}" if others else ""),
          flush=True)
    if not args.no_open:
        threading.Thread(target=open_in_editor, args=(pdf_path(os.getcwd(), f"{doc}.pdf"),), daemon=True).start()

    server.no_build = args.no_build
    server.env = {**os.environ, "LATEX_LIVE_HOME": config.HERE, "max_print_line": "10000"}
    if config.SHARED:  # (the image even with --no-build: the baselines and downloads build too, never with the host's TeX)
        server.env["LATEX_LIVE_EXEC"] = os.environ["LATEX_LIVE_EXEC"] = server.tex_exec
        threading.Thread(target=watch_build_size, args=(server,), daemon=True).start()
        if not args.no_build:
            print("=== latex-live: TeX Live 2024 (podman), confined, each run in a container of its own" if image_pulled()
                  else f"=== latex-live: {config.IMAGE_MISSING}; until then nothing builds (never the host's TeX here)", flush=True)
            start_shared_build(server)
    elif args.no_build:
        pass
    elif args.host_tex or not shutil.which("podman"):
        print("=== latex-live: TeX from the host" + ("" if args.host_tex else " (no podman)")
              + ("" if shutil.which("pdflatex") else "; but there is no pdflatex: install TeX Live (MacTeX on macOS)"),
              flush=True)
    else:
        server.env["LATEX_LIVE_EXEC"] = server.tex_exec = container_exec(os.path.realpath(os.getcwd()))
        print("=== latex-live: TeX Live 2024 (podman)" if image_pulled()
              else f"=== latex-live: {config.IMAGE_MISSING}; until then, the host's TeX runs", flush=True)
    if not config.SHARED:
        stop_doc_standbys(server)  # (what an instance that was killed left)
        start_latexmk(server)

    # Only signal latexmk here; the main loop reaps it.
    def stop(*_):
        server.stopping = True
        if server.latexmk:
            server.latexmk.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGHUP, stop)  # VS Code closing the task terminal
    try:  # until stopped, or latexmk exits by itself; meanwhile, switch documents and rebuild for the viewer
        while not server.stopping and not (server.latexmk and server.latexmk.poll() is not None):
            if not config.SHARED:
                retry_build(server)
            try:
                job, result, done = server.requests.get(timeout=1)
            except queue.Empty:
                continue
            try:
                result["status"], result["body"] = job(server)
            except Exception as e:
                result["status"], result["body"] = 500, {"error": f"failed: {e}"}
            done.set()
    except KeyboardInterrupt:
        pass
    finally:  # (also after an error: the pending edits are committed as theirs now, rather than in someone else's commit)
        server.stopping = True
        stop_latexmk(server)
        stop_doc_standbys(server)
        stop_containers()
        commit_at_exit(server)
        stop_containers()  # (one a build started while the first sweep ran)
        try:
            os.remove(os.path.join(config.INSTANCES, f"{os.getpid()}.json"))
        except OSError:
            pass
