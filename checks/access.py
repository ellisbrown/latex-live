"""Who may reach the owner's own previews, and how (access_checks): the Host allowlist (loopback names on the
instance's and the hub's ports, LATEX_LIVE_HOST's name, LATEX_LIVE_ALLOWED_HOSTS'), the Origin rules for POSTs,
checked before the body is read, and the security headers."""

import http.client
import json
import time

from .base import PORT, fail, hub, ok
from .helpers import post_length


BRIDGE = "bridge.invalid"  # the instances' LATEX_LIVE_HOST (live_checks): a name the network gives this machine
EXTRA = "extra.invalid:8443"  # their LATEX_LIVE_ALLOWED_HOSTS: a reverse proxy's name, on its own port
FRAME = "frame-ancestors 'self' vscode-webview: vscode-file:"  # (VS Code's Simple Browser frames the owner's own)


def raw(port, method, path, headers, body=None):
    """(status, {header: value} in lower case, body) of one request to port, with exactly these headers (Host too)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        data = None if body is None else json.dumps(body).encode()
        for key, value in headers:
            conn.putheader(key, value)
        if data is not None:
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(len(data)))
        conn.endheaders(data)
        r = conn.getresponse()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
    finally:
        conn.close()


def access_checks(token, own):
    """The owner's preview (own: its port) and the hub (PORT), as a browser reaches them."""
    base, probe = hub["base"], {"path": "body.tex", "base": "0" * 20, "text": "x\n"}  # (a save on a version never seen: 409)
    cookie = ("Cookie", f"latex_live={token}")

    # Host: loopback names on the instance's and the hub's ports (or none), LATEX_LIVE_HOST's name on those ports,
    # LATEX_LIVE_ALLOWED_HOSTS' name on the port it names; nothing else, on either.
    for port, path in ((PORT, f"{base}main/me"), (own, "/me")):
        for host, want in ((f"localhost:{port}", 200), (f"127.0.0.1:{port}", 200), (f"[::1]:{port}", 200), ("localhost", 200),
                           (f"localhost:{own if port == PORT else PORT}", 200 if port == own else 403),
                           (f"{BRIDGE}:{port}", 200), (f"{BRIDGE.upper()}:{port}", 200), (EXTRA, 200),
                           ("localhost:5000", 403), (f"{BRIDGE}:5000", 403), (BRIDGE, 403), (f"other.invalid:{port}", 403),
                           (f"extra.invalid:{port}", 403), (f"{BRIDGE}.evil.example:{port}", 403), (f"localhost.evil.example:{port}", 403)):
            status = raw(port, "GET", path, [("Host", host), cookie])[0]
            if status != want:
                fail(f"GET {path} on port {port} with Host {host}: {status}, not {want}")
    status, _, data = raw(PORT, "GET", f"{base}main/me", [("Host", f"other.invalid:{PORT}"), cookie])
    if b"LATEX_LIVE_ALLOWED_HOSTS" not in data:
        fail(f"a Host that is not allowed is refused without saying how to allow it: {data[:200]!r}")
    ok("the hub and the previews answer to localhost, 127.0.0.1 and [::1] on their own and the hub's ports, to"
       " LATEX_LIVE_HOST's name there and to LATEX_LIVE_ALLOWED_HOSTS' on its port, and to no other name or port")

    # Origin, before the body is read: the viewer's own page only, under a name of the same kind as the Host.
    for port, path in ((PORT, f"{base}main/file"), (own, "/file")):
        for host, origin, allowed in (
                (f"localhost:{port}", f"http://localhost:{port}", True), (f"localhost:{port}", None, True),
                (f"127.0.0.1:{port}", f"http://127.0.0.1:{port}", True), (f"localhost:{port}", f"http://[::1]:{port}", True),
                (f"localhost:{port}", f"http://localhost:{PORT}", True), (f"{BRIDGE}:{port}", f"http://{BRIDGE}:{port}", True),
                (EXTRA, f"https://{EXTRA}", True), (EXTRA, f"http://{EXTRA}", True),
                (f"localhost:{port}", "http://localhost:5000", False), (f"localhost:{port}", "http://localhost", False),
                (f"localhost:{port}", f"http://{BRIDGE}:{port}", False), (f"{BRIDGE}:{port}", f"http://localhost:{port}", False),
                (f"localhost:{port}", "http://evil.example", False), (f"localhost:{port}", "null", False),
                (f"localhost:{port}", f"http://localhost.evil.example:{port}", False),
                (f"localhost:{port}", f"http://localhost:{port}/", False)):
            headers = [("Host", host), cookie] + ([("Origin", origin)] if origin else [])
            status = raw(port, "POST", path, headers, probe)[0]
            if status != (409 if allowed else 403):
                fail(f"a POST on port {port} with Host {host} and Origin {origin}: {status} (want {409 if allowed else 403})")
    for port, path in ((PORT, f"{base}main/note"), (own, "/note")):  # (90 000 bytes announced, none sent: answered at once)
        started = time.monotonic()
        status = post_length(port, path, 90_000, [("Host", f"localhost:{port}"), cookie, ("Origin", "http://evil.example")])
        if status != 403 or time.monotonic() - started > 5:
            fail(f"a POST from another site's page on port {port}: {status} after {time.monotonic() - started:.1f} s, not 403 at once")
    ok("POSTs count only from the viewer's own page (an Origin under the same kind of name as the Host, on the instance's"
       " or the hub's port), or with no Origin (a program with the token); a refusal comes before the body is read")

    # The security headers, as in shared mode, but VS Code's Simple Browser may frame the owner's own.
    for port, path in ((PORT, f"{base}main/viewer.html?file=main.pdf"), (own, "/viewer.html?file=main.pdf"),
                       (PORT, f"{base}main/status/main.pdf"), (PORT, "/papers")):
        _, headers, _ = raw(port, "GET", path, [("Host", f"localhost:{port}"), cookie])
        for name, want in (("x-content-type-options", "nosniff"), ("x-frame-options", "SAMEORIGIN"),
                           ("content-security-policy", FRAME), ("referrer-policy", "no-referrer")):
            if headers.get(name) != want:
                fail(f"GET {path} on port {port} is sent with {name}: {headers.get(name)!r}, not {want!r}")
    ok("the hub and the previews send nosniff, no referrer, and frame-ancestors (themselves and VS Code's Simple Browser"
       " only)")
