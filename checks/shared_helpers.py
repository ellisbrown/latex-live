"""What shared mode's checks share: requests to the shared instance's own port (send), as the trusted proxy
forwards them for a user (via_proxy) or with a token's cookie (with_token), and what their answers must be
(expect); the shared copy's status and whether its build has settled; notes, saves and their auto-commits as
each user; the users file; and the containers that tex-exec labelled for an instance."""

import http.client
import json
import os
import re
import subprocess
import time

from .base import SECRETS, SHARED_PORT, _quiet, fail, wait_for
from .helpers import git_out, mtime_ns


PUBLIC = "http://paper.invalid:8443"  # its LATEX_LIVE_PUBLIC_URL: the origin browsers would use
PROXY_HOST = "proxy.invalid"  # its LATEX_LIVE_PROXY_HOST: the Host of the proxy's requests (via_proxy)

SOURCES = ("main.tex", "body.tex", "people.tex", "generated.inc")  # what the shared copy's build reads (settled)


def write_users(sh, text=None):
    """(Re)write the users file (mode 0600) with a later mtime, so that the server reads it again."""
    path = sh["users_file"]
    fd = os.open(path + ".new", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps({"users": sh["users"]}, indent=1) if text is None else text)
    os.replace(path + ".new", path)
    sh["users_mtime"] = stamp = max(sh.get("users_mtime", 0) + 2_000_000_000, time.time_ns())
    os.utime(path, ns=(stamp, stamp))


def send(method, path, headers=(), body=None, port=SHARED_PORT, addr="127.0.0.1", timeout=30, quiet=False):
    """(status, [(header, value)], body) of one request to an instance's own port, with exactly these headers
    (pairs, so that one can repeat; Host defaults to the address); a body goes as JSON unless it is bytes.
    quiet: None instead of failing when nothing answers. No answer may carry a shared-mode token."""
    conn = http.client.HTTPConnection(addr, port, timeout=timeout)
    names = {key.lower() for key, _ in headers}
    data = body if body is None or isinstance(body, bytes) else json.dumps(body).encode()
    try:
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        if "host" not in names:
            conn.putheader("Host", f"[{addr}]:{port}" if ":" in addr else f"{addr}:{port}")
        if data is not None:
            if "content-type" not in names:
                conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(len(data)))
        for key, value in headers:
            conn.putheader(key, value)
        conn.endheaders(data)
        response = conn.getresponse()
        result = response.status, response.getheaders(), response.read()
    except (OSError, http.client.HTTPException) as e:
        if quiet:
            return None
        fail(f"{method} {path}: no answer ({type(e).__name__}: {e})")
    finally:
        conn.close()
    if any(secret.encode() in result[2] for secret in SECRETS):
        fail(f"{method} {path} answered with a token in its body")
    return result


def header(result, name):
    return next((value for key, value in result[1] if key.lower() == name.lower()), None)


def via_proxy(user, path, method="GET", body=None, origin=PUBLIC, headers=()):
    """A request as the trusted proxy forwards it for user: its Host, the identity header (the configured
    name, in another case), the address it adds, and the browser's Origin on a POST. The proxy's requests
    must never get a cookie."""
    sent = [("Host", PROXY_HOST), ("X-Test-User", user), ("X-Forwarded-For", "203.0.113.7")]
    sent += [("Origin", origin)] if method == "POST" and origin else []
    result = send(method, path, sent + list(headers), body)
    if header(result, "Set-Cookie"):
        fail(f"{method} {path} for {user} through the proxy set a cookie")
    return result


def with_token(token, path, method="GET", body=None, host=None, origin=None):
    """A request with a token's cookie, on a loopback Host (127.0.0.1:port) unless host says otherwise."""
    sent = ([("Host", host)] if host else []) + [("Cookie", f"latex_live={token}")] + ([("Origin", origin)] if origin else [])
    return send(method, path, sent, body)


def expect(result, status, what):
    """result's body, as JSON if it is JSON, failing unless its status is status (or one of them)."""
    wanted = status if isinstance(status, tuple) else (status,)
    if result[0] not in wanted:
        fail(f"{what}: {result[0]}, not {' or '.join(map(str, wanted))} ({result[2][:300]!r})")
    if (header(result, "Content-Type") or "").startswith("application/json"):
        try:
            return json.loads(result[2])
        except ValueError:
            fail(f"{what}: not JSON ({result[2][:300]!r})")
    return result[2].decode(errors="replace")


def expect_dict(result, status, what):
    value = expect(result, status, what)
    if not isinstance(value, dict):
        fail(f"{what}: not a JSON object ({str(value)[:300]})")
    return value


def as_viewer(path):
    """A GET for the viewer vw through the proxy (JSON, or text)."""
    return expect(via_proxy("vw", path), 200, f"GET {path} for a viewer")


def as_editor(path):
    """A GET for the editor ed through the proxy (JSON, or text): forward search for any line is theirs."""
    return expect(via_proxy("ed", path), 200, f"GET {path} for the editor")


def shared_status(sh):
    """The shared document's status, as its owner gets it (GET status), or {} while there is no answer."""
    r = send("GET", "/status/main.pdf", [("Cookie", f"latex_live={sh['token']}")], quiet=True)
    return _quiet(lambda: json.loads(r[2])) or {} if r and r[0] == 200 else {}


def settled(sh):
    """Whether the shared copy's build has caught up with every edit of its sources: no run is on, and the
    last one began after the last edit (its status's "began"). (Note actions read the file list that a pass
    rewrites as it goes.)"""
    info = shared_status(sh)
    build = info.get("build") or {}
    newest = max(mtime_ns(os.path.join(sh["root"], name)) for name in SOURCES)
    return info.get("building") is False and isinstance(build.get("began"), int) and build["began"] >= newest


def line_of(text, needle):
    return text.count("\n", 0, text.index(needle)) + 1


def with_paragraph(text, i, new):
    """body.tex's text with its i-th one-line paragraph of greek words replaced (they follow NOTE_PARAGRAPHS)."""
    lines = text.split("\n")
    lines[line_of(text, "Walrus yak") - 1 + 2 * i] = new
    return "\n".join(lines)


def recorded(state):
    """The instances that a state folder lists."""
    try:
        return os.listdir(os.path.join(state, "instances"))
    except FileNotFoundError:
        return []


def note(sh, uid, req):
    """POST note as a user (through the proxy, or tk with its personal link), for the shared document."""
    body = {"pdf": "main.pdf", **req}
    if uid == "tk":
        return with_token(sh["users"]["tk"]["token"], "/note", "POST", body)
    return via_proxy(uid, "/note", "POST", body)


def note_ok(sh, uid, req, what):
    """A note action that must succeed, at once (a build in progress makes no difference: notes_while_rebuilding)."""
    return expect_dict(note(sh, uid, req), 200, what)


def commit_user(root, sha):
    """The user whose edits a commit holds, from its Latex-Live-Shared trailer (None without exactly one)."""
    trailer = re.findall(r"^Latex-Live-Shared: *(\S+) *$", git_out(root, "show", "-s", "--format=%B", sha), re.M)
    return trailer[0] if len(trailer) == 1 else None


def wait_commit(sh, uid, paths, what):
    """Wait for the background auto-commit of uid's edits to paths (once uid has been quiet for
    LATEX_LIVE_COMMIT_IDLE), with no one else's commit in between. Each user writes in a phase of their own,
    so that one user's commit can't hold another's edits of the same file."""
    root = sh["root"]

    def landed():
        unsaved = subprocess.run(["git", "-C", root, "diff", "--quiet", "HEAD", "--", *paths],
                                 env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"}).returncode
        # The commits are read after the files are found committed, so that they include the commit that did it: read
        # before, they could miss one that landed in between (uid's edits in two commits, the second just then), whose
        # sha would then not be the tip, and the next check would count it as a commit made after this.
        new = git_out(root, "rev-list", f"{sh['tip']}..shared").split()
        if any(commit_user(root, sha) != uid for sha in new):
            fail(f"a commit not of {uid}'s appeared while only {uid} wrote: {[(sha[:9], commit_user(root, sha)) for sha in new]}")
        return new if new and not unsaved else None

    sh["tip"] = wait_for(f"the auto-commit of {what}", landed, timeout=20)[0]


def save(uid, req, status, what):
    return expect_dict(via_proxy(uid, "/file", "POST", req), status, what)


def instance_containers(pid, run=None, every=False):
    """The running containers that tex-exec labelled for the shared instance in process pid (config.INSTANCE starts
    with its pid), or those of one kind of run ("main", "clean"; each run has a label of its own, main-12 say); with
    every, those in any state (created but never started too)."""
    return {f[0] for f in labelled_containers(every) if f[1].startswith(f"{pid}-") and run in (None, f[2])}


def labelled_containers(every=False):
    """(id, instance label, kind) of each container that tex-exec labelled for a shared instance (running ones, or
    with every, those in any state)."""
    r = subprocess.run(["podman", "ps", *(["-a"] if every else []), "--filter", "label=latex-live.instance", "--format",
                        '{{.ID}} {{index .Labels "latex-live.instance"}} {{index .Labels "latex-live.kind"}}'],
                       capture_output=True, text=True, timeout=60)
    return [f for f in (line.split() for line in r.stdout.splitlines()) if len(f) == 3]
