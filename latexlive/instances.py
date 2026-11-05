"""The previews running on this machine: one instance per document (its lock in build/), the list
of running instances in the state folder, and stopping one."""

import fcntl
import glob
import hashlib
import http.client
import json
import os
import re
import signal
import subprocess
import time
import urllib.error
import urllib.request

from . import config


def take_lock(doc):
    """The lock on build/<doc>-live.lock (one instance per document), or (None, the holder's
    {port, pid, state}; no pid from older versions, which recorded only the port, and no state, its
    LATEX_LIVE_STATE, from those before 0.4). TeX can write in build/, so this is a hint to check
    (runs_here, instance_answers), not a fact."""
    lock = open(os.path.join(config.BUILD, f"{doc}-live.lock"), "a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.seek(0)
        text = lock.read().strip()
        lock.close()
        try:
            return None, json.loads(text) if text.startswith("{") else {"port": int(text)}
        except ValueError:
            return None, {}
    return lock, None


def record_port(server):
    """The viewer port and this process, in the lock file for later instances of the document,
    and in the list of running instances (instances)."""
    server.lock.truncate(0)
    server.lock.write(json.dumps({"port": server.server_address[1], "pid": os.getpid(), "state": config.STATE}))
    server.lock.flush()
    os.makedirs(config.INSTANCES, exist_ok=True)
    entry = {"port": server.server_address[1], "pid": os.getpid(), "root": server.paper_root, "doc": server.default_pdf[:-4],
             "was": getattr(server, "was", []),
             "id": paper_id(server.paper_root), "version": config.VERSION}
    with open(os.path.join(config.INSTANCES, f"{os.getpid()}.json"), "w") as f:
        json.dump(entry, f)


def is_live_instance(pid):
    """Whether pid is a running latex-live (not a reused pid)."""
    try:
        os.kill(pid, 0)
    except (OSError, TypeError):
        return False
    try:  # Linux: the exact argument list
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            argv = f.read().decode(errors="replace").split("\0")[:-1]
    except OSError:  # macOS: ps prints it joined, unquoted (a path may have spaces)
        command = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
        return bool(re.match(r"(?i)\S*python[0-9.]*\s+(-(?![cm]\b)\S+\s+)*(?!-)((?!\.py\s).)*?(^|/)live\.py(\s|$)", command))
    return is_live_argv(argv)


def is_live_argv(argv):
    """Whether argv is python running live.py as its script (not e.g. an editor on it, or python
    -c with live.py among its arguments)."""
    if not argv or not re.fullmatch(r"(?i)python[0-9.]*", os.path.basename(argv[0])):
        return False
    args = argv[1:]
    while args and args[0].startswith("-"):
        if args[0] in ("-c", "-m"):
            return False
        args = args[2:] if args[0] in ("-W", "-X") else args[1:]  # (options with a value)
    return bool(args) and os.path.basename(args[0]) == "live.py"


def runs_here(pid, root):
    """Whether pid is a latex-live instance (is_live_instance) working in root, a paper root (its
    working directory, as Linux's /proc tells; elsewhere, only its command line is checked). The lock
    file in build/ names the pid to stop, and the paper's TeX can write there: it must not make us
    stop another process."""
    if not os.path.isdir("/proc/self"):
        return is_live_instance(pid)
    try:
        cwd = os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return False
    return os.path.realpath(cwd) == os.path.realpath(root) and is_live_instance(pid)  # (its command line read after its folder)


def instances():
    """The running latex-live instances on this machine: [{port, pid, root, doc}], from
    INSTANCES (each removes its entry on exit; entries of crashed ones are dropped here), and
    from OLD_INSTANCES for instances of an older version."""
    found = []
    for path in glob.glob(os.path.join(config.INSTANCES, "*.json")) + glob.glob(os.path.join(config.OLD_INSTANCES, "*.json")):
        try:
            with open(path) as f:
                entry = json.load(f)
        except (OSError, ValueError):
            continue
        if is_live_instance(entry.get("pid")):
            found.append(entry)
        else:
            try:
                os.remove(path)
            except OSError:
                pass
    return sorted(found, key=lambda e: e["port"])


def paper_id(root):
    """A paper's id in hub URLs: its folder's name and a short hash of its path (and of the build
    directory, which a second instance of the same paper changes), stable across restarts."""
    real = os.path.realpath(root)
    key = real + ("" if config.BUILD == "build" else "\0" + config.BUILD)
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", os.path.basename(real)).strip("-") or "paper"
    return f"{slug}-{hashlib.sha1(key.encode()).hexdigest()[:6]}"


def instance_answers(port):
    """Whether an HTTP server answers on port within a few seconds, whatever it answers: the port of the
    instance that holds a document's lock, which it records in build/, where the paper's TeX can write.
    So the request carries no token (an instance with it refuses it, and still answers)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/docs", timeout=3):
            return True
    except urllib.error.HTTPError as e:
        e.close()
        return True
    except (OSError, ValueError, http.client.HTTPException):
        return False


def stop_instance(pid, root=None):
    """Stop another latex-live instance (it stops its latexmk); False if pid is not one (with root: one
    working in that paper root, runs_here), or won't stop."""
    if not (runs_here(pid, root) if root else is_live_instance(pid)):
        return False
    try:
        os.kill(pid, signal.SIGTERM)
        os.kill(pid, signal.SIGCONT)  # (a paused one handles SIGTERM only once it runs: then it stops its latexmk itself)
    except ProcessLookupError:  # (it exited meanwhile)
        pass
    for i in range(150):
        if not is_live_instance(pid):
            for folder in (config.INSTANCES, config.OLD_INSTANCES):  # (killed, it can't remove its entry itself)
                try:
                    os.remove(os.path.join(folder, f"{pid}.json"))
                except OSError:
                    pass
            return True
        if i == 100:
            os.kill(pid, signal.SIGKILL)
        time.sleep(0.1)
    return False
