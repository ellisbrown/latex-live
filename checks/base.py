"""Where the checks run and how they report: the checkout (HERE), the block of ports they take (below the ports
that outgoing connections take), the environment of the instances they start (ENV: a state folder of their own,
none of the user's LATEX_LIVE_* settings), and the output (ok, fail, wait_for, redacting tokens) of checks that run in
threads beside each other (in_thread), the first failure stopping them all."""

import os
import re
import socket
import sys
import threading
import time
import traceback

from latexlive.web import DualStackServer, ephemeral, ephemeral_ports, port_below_ephemeral  # (check.py puts the checkout on the path)


HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # (the checkout, which holds checks/)

PORTS = 24  # (the block of ports the checks take, from PORT on)


def free_ports(count):
    """The first port of count consecutive ones that are all free now and that no outgoing connection may take
    later (they are below the ephemeral ports, ephemeral_ports): the hub needs its exact port, and on a busy
    machine an outgoing connection could otherwise hold one of them just when an instance binds it. From
    44180 where that is outside the ephemeral ports (macOS), else from a few thousand below them (Linux:
    28180 below 32768)."""
    first_ephemeral, last_ephemeral, _ = ephemeral_ports()
    if not any(ephemeral(port) for port in range(44180, 44180 + count)):
        first, last = 44180, 44999
    else:
        first = port_below_ephemeral(180) or 1024
        last = first_ephemeral - 1
    base = first
    while base + count - 1 <= last:
        busy = next((port for port in range(base, base + count) if not port_free(port) or ephemeral(port)), None)
        if busy is None:
            return base
        base = busy + 1  # (no block that holds the busy port will do)
    sys.exit(f"check: no {count} free consecutive ports from {first} to {last} (outside {first_ephemeral}-{last_ephemeral},"
             " where outgoing connections take theirs); set LATEX_LIVE_CHECK_PORT to the first of a free block")


def port_free(port):
    """Whether a server could listen on port now, on every address, with the socket options of live.py's servers
    (DualStackServer: "::", dual-stack, and its address and port reuse)."""
    with socket.socket(DualStackServer.address_family) as s:
        if DualStackServer.allow_reuse_address:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if getattr(DualStackServer, "allow_reuse_port", False) and hasattr(socket, "SO_REUSEPORT"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        try:
            s.bind(("::", port))
            s.listen()
        except OSError:
            return False
    return True


PORT = int(os.environ.get("LATEX_LIVE_CHECK_PORT") or free_ports(PORTS))  # the hub's; the previews' own ports follow
if os.environ.get("LATEX_LIVE_CHECK_PORT") and any(ephemeral(port) for port in range(PORT, PORT + PORTS)):
    print(f"note: LATEX_LIVE_CHECK_PORT={PORT}: ports {PORT}-{PORT + PORTS - 1} include ones that outgoing connections take,"
          " so an instance may find its port held by one", flush=True)
SHARED_PORT = PORT + 11  # the shared-mode instance's (shared_setup), past the previews' ports
DECOY_PORT = PORT + 12  # a stand-in for a program that a lock file written in build/ points to (lock_spoof_checks)
STANDBY_PORT = PORT + 13  # the hub of the warm standby's checks (standby_run), with state of its own; its preview's follow
EARLY_PORT = PORT + 16  # the hub of the early notes-free pass's checks (early_run), with state of its own; its preview's follow
CLEAN_PORT = PORT + 19  # the hub of the notes-free pass's warm standby's checks (clean_standby_run), likewise
LANDSCAPE_PORT = PORT + 22  # the hub of the turned pages' checks (landscape_run), likewise

LOGS = []  # the instances' output, shown on failure
# The shared-mode checks run in a thread of their own, beside the others (live_checks): output is printed whole,
# and the first failure stops both threads.
OUT_LOCK, START_LOCK, STOPPING = threading.Lock(), threading.Lock(), threading.Event()
# For the instances: their own token and instance list (live_checks), a protected marker, and none of
# the user's own settings.
ENV = {k: v for k, v in os.environ.items() if not k.startswith("LATEX_LIVE_") and k != "VSCODE_IPC_HOOK_CLI"}
ENV["LATEX_LIVE_PROTECTED"] = r"\\eb\{human-written\}"


def redact(text):
    """Never print a token: as a URL parameter, a cookie, or itself."""
    text = re.sub(r"(\bt=|latex_live=)[A-Za-z0-9_-]{8,}", r"\1REDACTED", str(text))  # (\b: not TeX's "format=pdflatex")
    for secret in (TOKEN[0], *SECRETS):
        if secret:
            text = text.replace(secret, "REDACTED")
    return text


def prints_token(text):
    """Whether output shows a token: the instances' own, a personal link's, or one in a ?t= link."""
    return bool(re.search(r"\bt=[A-Za-z0-9_-]{8,}", text)) or any(secret and secret in text for secret in (TOKEN[0], *SECRETS))


def fail(msg):
    with OUT_LOCK:
        if not STOPPING.is_set():  # (once: a failure in one thread makes the other stop, and maybe fail too)
            STOPPING.set()
            for path in LOGS:
                with open(path, errors="replace") as f:
                    print(f"--- {os.path.basename(path)} (last lines)\n{redact(''.join(f.readlines()[-25:]))}", flush=True)
            print(f"FAIL: {redact(msg)}", file=sys.stderr, flush=True)
    sys.exit(1)


def stopping():
    """Stop this thread if the other one failed (it said why)."""
    if STOPPING.is_set():
        sys.exit(1)


def ok(msg):
    stopping()
    with OUT_LOCK:
        print(f"ok: {msg}", flush=True)


def wait_for(what, test, timeout=180, step=0.1):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        stopping()
        if result := test():
            return result
        time.sleep(step)
    fail(f"timed out waiting for {what}")


hub, TOKEN = {}, [None]
SECRETS = []  # the shared-mode instance's tokens (shared_setup): never printed, never in an answer's body


def in_thread(name, target, *args):
    """Run target(*args) in a thread of its own, beside the other checks. Returns the thread; its .failed says
    whether a check failed."""
    def run():
        try:
            target(*args)
        except SystemExit:  # (fail said why)
            thread.failed = True
        except BaseException:
            thread.failed = True
            with OUT_LOCK:
                STOPPING.set()
                traceback.print_exc()

    thread = threading.Thread(target=run, name=name, daemon=True)
    thread.failed = False
    thread.start()
    return thread


def _quiet(test):
    try:
        return test()
    except (OSError, ValueError, KeyError, TypeError):
        return None
