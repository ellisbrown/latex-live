"""Who may reach the owner's own previews, and how (access_checks): the Host allowlist (loopback names on the
instance's and the hub's ports, LATEX_LIVE_HOST's name, LATEX_LIVE_ALLOWED_HOSTS'), the Origin rules for POSTs,
checked before the body is read, the security headers, and the sessions: the cookie a link's token is exchanged
for, renewed, refused once forged or expired, and forgotten on sign-out; the token file made private at the start."""

import base64
import hashlib
import hmac
import http.client
import json
import os
import re
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


def session(token, issued, subject="o"):
    """A session cookie as live.py signs one (auth.new_session), issued at a given time."""
    payload = f"s1.{int(issued)}.checknonce.{subject}"
    mac = hmac.new(token.encode(), b"latex-live session\0" + payload.encode(), hashlib.sha256).digest()[:24]
    return f"{payload}.{base64.urlsafe_b64encode(mac).rstrip(b'=').decode()}"


def cookie_of(headers):
    """The latex_live cookie's value in a Set-Cookie header, or None."""
    m = re.match(r"latex_live=([^;]*)", headers.get("set-cookie", ""))
    return m and m.group(1)


def access_checks(token, own, state, log):
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

    # Sessions: a link's token becomes a signed cookie (never the token), new on each link, working on the hub and the
    # preview alike; one older than a day is renewed; a forged, expired or other instance's one is refused.
    host = [("Host", f"localhost:{PORT}")]
    first = raw(PORT, "GET", f"{base}main/me?t={token}", host)
    second = raw(PORT, "GET", f"{base}main/me?t={token}", host + [("Cookie", f"latex_live={cookie_of(first[1])}")])
    sessions = [cookie_of(r[1]) for r in (first, second)]
    attrs = first[1].get("set-cookie", "").lower()
    if (not all(s and s.startswith("s1.") and token not in s for s in sessions) or sessions[0] == sessions[1]
            or "httponly" not in attrs or "samesite=strict" not in attrs or "max-age=31536000" not in attrs or "secure" in attrs):
        fail(f"a link's token sets the cookies {[s and s[:12] for s in sessions]} ({attrs.replace(token, 'TOKEN')!r}): want a new"
             " signed session each time, never the token, HttpOnly, SameSite=Strict, for a year, not Secure (http)")
    for value in sessions:
        for port, path in ((PORT, f"{base}main/me"), (own, "/me")):
            r = raw(port, "GET", path, [("Host", f"localhost:{port}"), ("Cookie", f"latex_live={value}")])
            if r[0] != 200 or "set-cookie" in r[1]:
                fail(f"a session cookie on port {port}: {r[0]} (want 200, and no new cookie for a fresh one)")
    legacy = raw(PORT, "GET", f"{base}main/me", host + [cookie])
    if legacy[0] != 200 or not (cookie_of(legacy[1]) or "").startswith("s1."):
        fail(f"a cookie from before sessions (the token): {legacy[0]}, not 200 with a session cookie in its place")
    old = session(token, time.time() - 2 * 86400)
    renewed = raw(own, "GET", "/me", [("Host", f"localhost:{own}"), ("Cookie", f"latex_live={old}")])
    if renewed[0] != 200 or not (cookie_of(renewed[1]) or "").startswith("s1.") or cookie_of(renewed[1]) == old:
        fail(f"a session two days old: {renewed[0]}, not 200 with a new session cookie")
    good = sessions[0]
    for value, what in ((session(token, time.time() - 400 * 86400), "a session older than a year"),
                        (session(token, time.time() + 86400), "a session issued tomorrow"),
                        (session("another-instance-token", time.time()), "a session signed with another token"),
                        (good[:-2] + ("AA" if not good.endswith("AA") else "BB"), "a session with its signature changed"),
                        (good.replace("s1.", "s1.1", 1), "a session with its time changed"),
                        (session(token, time.time(), "u" + base64.urlsafe_b64encode(b"owner").decode().rstrip("=")),
                         "a user's session, outside shared mode")):
        if raw(PORT, "GET", f"{base}main/me", host + [("Cookie", f"latex_live={value}")])[0] != 403:
            fail(f"{what} is accepted")
    me = json.loads(raw(PORT, "GET", f"{base}main/me", host + [("Cookie", f"latex_live={good}")])[2])
    if me.get("can", {}).get("signOut") is not True:
        fail(f"GET me for the owner signed in with a token does not offer to sign out: {me.get('can')}")
    ok("a link's token becomes a signed session cookie (a new one each time, never the token) that the hub and the previews"
       " take; a cookie from before sessions is replaced; one older than a day is renewed; forged, expired or other"
       " instances' ones are refused")

    # Signing out: a POST from the viewer's page, through the hub or on the preview's own port, expires the cookie;
    # the page then says so.
    for port, path in ((PORT, f"{base}main/logout"), (own, "/logout")):
        refused = raw(port, "POST", path, [("Host", f"localhost:{port}"), ("Cookie", f"latex_live={good}"),
                                           ("Origin", "http://evil.example")], {})
        out = raw(port, "POST", path, [("Host", f"localhost:{port}"), ("Cookie", f"latex_live={good}"),
                                       ("Origin", f"http://localhost:{port}")], {})
        if refused[0] != 403 or "set-cookie" in refused[1]:
            fail(f"a sign-out from another site's page on port {port}: {refused[0]}")
        if out[0] != 200 or cookie_of(out[1]) != "" or "max-age=0" not in out[1].get("set-cookie", "").lower():
            fail(f"POST logout on port {port}: {out[0]} {out[1].get('set-cookie')!r}, not 200 with a cookie that expires at once")
    page = raw(PORT, "GET", f"{base}main/viewer.html?file=main.pdf&signed-out=1", host + [("Accept", "text/html")])
    if page[0] != 403 or b"Signed out" not in page[2]:
        fail(f"the page after signing out: {page[0]} {page[2][:200]!r}")
    ok("signing out (a POST from the viewer's page, through the hub or not) expires the cookie, and the page says so")

    mode = os.stat(os.path.join(state, "token")).st_mode & 0o777
    with open(log, errors="replace") as f:
        said = "readable by you only now" in f.read()
    if mode != 0o600 or not said:
        fail(f"a token file that others could read is {oct(mode)} after the start ({'said' if said else 'not said'}), not 600")
    ok("a token file that others could read is made private at the start, saying so")
