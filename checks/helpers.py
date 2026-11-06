"""What the checks share: files written as an editor would (put), read and git; starting and stopping instances of
live.py and requests to them through the hub; stand-ins for TeX and the editor; whether podman has the TeX Live
image; and finding a point in the PDF that SyncTeX maps back to a source line."""

import http.client
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import urllib.parse

from .base import ENV, HERE, LOGS, PORT, START_LOCK, TOKEN, fail, hub


CAPS = ("comment", "edit", "rebuild", "compare", "switch", "openHost", "download")  # what GET me says a user can do

JS_TYPE_RE = re.compile(r"(?:text|application)/(?:x-)?javascript\b")


def put(path, text):
    """Replace a file at once, as another editor would (a new inode, so no cached version survives it)."""
    put_bytes(path, text.encode("utf-8"))


def put_bytes(path, data):
    """put, for bytes (making the folder it goes in, if need be)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path + ".check-new", "wb") as f:
        f.write(data)
    os.replace(path + ".check-new", path)


def tex_stand_in(tmp):
    """A stand-in for TeX in the downloads (export.exact_export, review.run_probe: server.tex_exec, a command prefix)
    that runs no TeX: it logs its arguments to tmp/calls, writes tmp/log as the job's log in its output folder
    (and tmp/pdf, if there is one, as its PDF), and exits with the code in tmp/exit (0 if none)."""
    script = os.path.join(tmp, "tex.py")
    with open(script, "w") as f:
        f.write("import os, shutil, sys\n"
                "here, args = os.path.dirname(os.path.abspath(__file__)), sys.argv[1:]\n"
                "with open(os.path.join(here, 'calls'), 'a') as f:\n"
                "    f.write(' '.join(args) + '\\n')\n"
                "out = next((a.split('=', 1)[1] for a in args if a.startswith('-output-directory=')), '.')\n"
                "job = next((a.split('=', 1)[1] for a in args if a.startswith('-jobname=')), 'texput')\n"
                "for name, ext in (('log', '.log'), ('pdf', '.pdf')):\n"
                "    if os.path.exists(os.path.join(here, name)):\n"
                "        shutil.copyfile(os.path.join(here, name), os.path.join(out, job + ext))\n"
                "sys.exit(int(open(os.path.join(here, 'exit')).read()) if os.path.exists(os.path.join(here, 'exit')) else 0)\n")
    return f"{shlex.quote(sys.executable)} {shlex.quote(script)}"


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=check", "-c", "user.email=check@example.com", *args], cwd=cwd, check=True,
                   capture_output=True)


def start(paper, doc="main", *extra, env=None, port=PORT):
    """An instance of live.py; its output goes to proc.log."""
    with START_LOCK:
        LOGS.append(os.path.join(paper, "..", f"live-{len(LOGS)}.log"))
        path = LOGS[-1]
    with open(path, "w") as log:
        proc = subprocess.Popen([sys.executable, os.path.join(HERE, "live.py"), f"{doc}.tex", "--no-open", "--port", str(port), *extra],
                                cwd=paper, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, env=env or ENV)
    proc.log = path
    return proc


def request(path, token=None, method="GET", body=None, cookie=None, headers=None):
    """(status, headers, body) of a request to the hub, without following redirects."""
    conn = http.client.HTTPConnection("127.0.0.1", hub["port"], timeout=30)
    if token:
        path += ("&" if "?" in path else "?") + f"t={token}"
    sent = {"Cookie": f"latex_live={cookie}"} if cookie else {}
    if body is not None:
        sent["Content-Type"] = "application/json"
    sent.update(headers or {})
    conn.request(method, path, json.dumps(body) if body is not None else None, sent)
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r.status, dict(r.getheaders()), data


def get(path, token, doc="main"):
    """A preview's endpoint, through the hub."""
    status, headers, data = request(f"{hub['base']}{doc}{path}", token)
    if status != 200:
        raise OSError(f"{status} {data[:200]!r}")
    kind = headers.get("Content-Type", "")
    return json.loads(data) if kind.startswith("application/json") else data.decode() if kind.startswith("text/") else data


def hub_get(path, token):
    """get, failing the check (with the answer) instead of raising."""
    try:
        return get(path, token)
    except Exception as e:
        fail(f"{path} (through the hub): {type(e).__name__}: {e}")


def read_hub():
    """The hub's port and pid (HUB_FILE), if it answers."""
    try:
        with open(os.path.join(ENV["LATEX_LIVE_STATE"], "hub.json")) as f:
            hub.update(json.load(f))
        return request("/hub/ping", TOKEN[0])[0] == 200
    except (OSError, ValueError, KeyError):
        return False


def stop(proc):
    try:
        os.killpg(proc.pid, signal.SIGCONT)
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(20)
    except ProcessLookupError:
        pass
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)


def text_point(lookup, file, line):
    """(page, x, y) in main.pdf that SyncTeX maps back to file near line, from a forward search; lookup(path)
    answers a GET as JSON. This also checks that synctex with json=1 answers {file, line}."""
    box = lookup(f"/forward?file={urllib.parse.quote(file)}&line={line}")
    tried = []
    for share in (0.5, 0.3, 0.7, 0.15, 0.85):
        page, x, y = box["page"], box["x"] + box["w"] * share, box["y"] + box["h"] / 2
        found = lookup(f"/synctex?file=main.pdf&page={page}&x={x:.2f}&y={y:.2f}&json=1")
        if not isinstance(found, dict):
            fail(f"synctex with json=1 did not answer JSON: {found!r}")
        if found.get("file") == file and isinstance(found.get("line"), int) and abs(found["line"] - line) <= 3:
            return page, x, y
        tried.append(found)
    fail(f"synctex with json=1 maps no point of the forward search for {file}:{line} back to it: {tried}")


def editor_stub(tmp, name):
    """A stand-in for the editor's command-line tool (LATEX_LIVE_EDITOR) that only logs its arguments, one
    call per line: (its path, the log's)."""
    stub, log = os.path.join(tmp, f"{name}.sh"), os.path.join(tmp, f"{name}.log")
    with open(stub, "w") as f:
        f.write(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {shlex.quote(log)}\n")
    os.chmod(stub, 0o755)
    return stub, log


def editor_calls(log):
    try:
        with open(log) as f:
            return f.read().splitlines()
    except FileNotFoundError:
        return []


def post_length(port, path, length, headers=()):
    """The status of a POST that announces length bytes but sends none, so the server must refuse it from
    the header alone (None: no answer within 10 s)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.putrequest("POST", path, skip_host=any(k.lower() == "host" for k, _ in headers), skip_accept_encoding=True)
        for key, value in (*headers, ("Content-Type", "application/json"), ("Content-Length", str(length))):
            conn.putheader(key, value)
        conn.endheaders()
        return conn.getresponse().status
    except (OSError, http.client.HTTPException):
        return None
    finally:
        conn.close()


def pdf_pages_and_text(pdf):
    info = subprocess.run(["pdfinfo", pdf], capture_output=True, text=True).stdout
    m = re.search(r"^Pages:\s+(\d+)", info, re.M)
    return m and int(m.group(1)), subprocess.run(["pdftotext", "-layout", pdf, "-"], capture_output=True, text=True).stdout


def texlive_image():
    """(The TeX Live image that live.py builds in, its config.TEXLIVE_IMAGE; whether podman has it here.)"""
    with open(os.path.join(HERE, "latexlive", "config.py"), encoding="utf-8") as f:
        image = re.search(r'^TEXLIVE_IMAGE = "([^"]+)"', f.read(), re.M).group(1)
    return image, bool(shutil.which("podman")) and subprocess.run(["podman", "image", "exists", image], timeout=60).returncode == 0


def make_state(path):
    """A state folder of its own, with a fresh token (so none carries over from this checkout)."""
    os.makedirs(path, mode=0o700)
    token = secrets.token_urlsafe(16)
    fd = os.open(os.path.join(path, "token"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token)
    return token


def write_text(root, name, text):
    """Write a file of a scratch paper as given (line endings too)."""
    path = os.path.join(root, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def mtime_ns(path):
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return 0


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def read_text(path):
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def git_out(cwd, *args):
    """git's output, without its optional locks (so that it never holds up the server's commits)."""
    r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    if r.returncode:
        fail(f"git {' '.join(args)}: {r.stderr}")
    return r.stdout.rstrip("\n")


def read_json_file(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None
