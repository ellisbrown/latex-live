"""HTTP plumbing shared by the instances and the hub: the servers, request body limits, the pages
for a browser without access, and BaseHandler, which checks who is asking and sends answers."""

import html
import http.cookies
import http.server
import json
import os
import re
import shutil
import socket
import sys
import threading
import time

from . import config
from .auth import (
    ORIGIN_RE, USER_ID_RE, b64, host_kinds, is_loopback, new_session, owner_name, owner_principal, peer_uid, principal,
    same_secret, session_mac, session_subject, unb64, users)
from .files import open_output
from .instances import is_live_instance


POST_LIMIT, POST_LIMITS = 100_000, {"/file": 8_000_000}  # bytes of a POST body (the browser editor saves whole files)


def content_length(headers, limit):
    """(n, None) for a request body of n bytes (its Content-Length; none: 0), else (None, the status to
    answer): 400 unless it is a single plain number, 413 if it is over limit."""
    values = headers.get_all("Content-Length") or ["0"]
    if len({v.strip() for v in values}) != 1 or not re.fullmatch(r"[0-9]{1,15}", values[0].strip()):
        return None, 400
    n = int(values[0].strip())
    return (n, None) if n <= limit else (None, 413)


class DualStackServer(http.server.ThreadingHTTPServer):
    """Every server's (an instance's, the hub's): on every interface, IPv4 too, with at most CONNECTIONS connections
    at once (each holds a thread until it ends or times out): past that, it drops new ones, which anything that
    reaches the port (a tunnel, a page in a browser) could otherwise open without end. A viewer takes a few at a
    time (a poll, a PDF, a module)."""
    address_family = socket.AF_INET6
    CONNECTIONS = 64

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)  # accept IPv4 too
        super().server_bind()

    def server_activate(self):
        self.slots, self.holding, self.slots_lock = threading.BoundedSemaphore(self.CONNECTIONS), set(), threading.Lock()
        super().server_activate()

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            return self.shutdown_request(request)
        with self.slots_lock:
            self.holding.add(request)
        super().process_request(request, client_address)

    def shutdown_request(self, request):
        super().shutdown_request(request)
        with self.slots_lock:
            held = request in self.holding
            self.holding.discard(request)
        if held:
            self.slots.release()

    def handle_error(self, request, client_address):
        """Print what went wrong in a request, unless the client just left mid-answer (a reload cuts a
        download short): that is no error, and its traceback would only clutter the log."""
        if not isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
            super().handle_error(request, client_address)


class LoopbackServer(DualStackServer):
    """A shared instance's server: on every interface, like the others (so 127.0.0.1 and [::1] both
    work), but it drops a connection from another machine before reading anything: coauthors come
    through the proxy, which runs here."""

    def verify_request(self, request, client_address):
        return is_loopback(client_address[0])


def ephemeral_ports():
    """The ports this machine gives outgoing connections, as (first, last, the ranges in it that it never gives).
    A connection holds its port while it lasts, so a server that needs one exact port does best outside them:
    on a busy machine, one of the many connections may hold it just when the server starts. Linux's are in
    /proc (ip_local_port_range, less ip_local_reserved_ports); elsewhere, the IANA range that macOS uses."""
    def read(name):
        try:
            with open(f"/proc/sys/net/ipv4/{name}") as f:
                return f.read()
        except OSError:
            return ""
    bounds = re.findall(r"\d+", read("ip_local_port_range"))
    first, last = (int(bounds[0]), int(bounds[1])) if len(bounds) == 2 else (49152, 65535)
    reserved = [(int(a), int(b or a)) for a, b in re.findall(r"(\d+)(?:-(\d+))?", read("ip_local_reserved_ports"))]
    return first, last, reserved


def ephemeral(port):
    """Whether an outgoing connection may hold port (ephemeral_ports)."""
    first, last, reserved = ephemeral_ports()
    return first <= port <= last and not any(a <= port <= b for a, b in reserved)


def port_below_ephemeral(port):
    """A port like port (the same last three digits) a few thousand below the ephemeral ports, or None when
    they start too low for one."""
    base = (ephemeral_ports()[0] // 1000 - 4) * 1000
    return base + port % 1000 if base >= 2000 else None


def taken_port_advice(port):
    """For a message that port is taken: when an outgoing connection may hold it, a sentence that says so and
    which port to choose instead; else ""."""
    if not ephemeral(port):
        return ""
    first, last, _ = ephemeral_ports()
    instead = port_below_ephemeral(port)
    return (f" Port {port} is among the ports this machine gives outgoing connections ({first}-{last}), so any of them may hold"
            f" it for a while: choose a port below {first}" + (f", e.g. {instead}." if instead else "."))


FRAME_ANCESTORS = "frame-ancestors 'self' vscode-webview: vscode-file:"  # (the owner's previews: VS Code's Simple Browser)


def serving_hub_port(cache={}):
    """The port of the hub serving now (HUB_FILE, if its process is alive), looked up at most every 2 s."""
    if time.monotonic() - cache.get("at", -9) > 2:
        try:
            with open(config.HUB_FILE) as f:
                hub = json.load(f)
        except (OSError, ValueError):
            hub = {}
        port = hub.get("port") if isinstance(hub, dict) else None
        cache["port"] = port if isinstance(port, int) and is_live_instance(hub.get("pid")) else None
        cache["at"] = time.monotonic()
    return cache["port"]


class BaseHandler(http.server.SimpleHTTPRequestHandler):
    """What every request of an instance and of the hub goes through: who is asking (authorize), and
    sending the answer. Without a route of its own (Handler, HubHandler), it answers nothing."""
    timeout = 60  # (a client that stops sending mid-request frees its thread)
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map, ".mjs": "text/javascript", ".js": "text/javascript",
                      ".css": "text/css"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=config.HERE, **kwargs)

    def allowed_ports(self):
        """The ports that a loopback name in Host or Origin may have here (host_kinds): this server's own, and the
        hub's (server.hub_port: the port the hub forwards from, for an instance; the hub is its own), plus the port
        of the hub serving now, should another preview hold it on another port."""
        ports = {self.server.server_address[1]}
        if getattr(self.server, "hub_port", None):
            ports.add(self.server.hub_port)
            ports.add(serving_hub_port())
        return ports - {None}

    def authorize(self, query):
        """Who is asking (a principal), or None, with self.denial saying why (deny). The Host must be one of this
        server's names (host_kinds: loopback ones on its ports, LATEX_LIVE_HOST's and LATEX_LIVE_ALLOWED_HOSTS', and
        in shared mode the public and the proxy's). Every client needs ?t=TOKEN once, which is exchanged for a session
        cookie (new_session; forwarded traffic arrives via loopback too). In shared mode, a user's own token works the
        same way, or the trusted proxy names the user (proxy_user)."""
        self.set_cookie, self.hosts = None, set()
        self.denial = (403, f"open the URL with ?t=<token> from {config.TOKEN_FILE}", None)
        if config.SHARED:  # (a browser with an old or revoked personal link, or none, gets a page that says what to do)
            self.denial = (403, "open your link again (the one with ?t= in it)", lambda: bad_link_page(owner_name(self.server)))
        if "signed-out" in query:  # (the viewer, after signing out: say so, rather than that the link is bad)
            self.denial = (403, "signed out: open your link again (the one with ?t= in it) to sign in", signed_out_page)
        hosts = self.headers.get_all("Host") or []
        self.hosts = host_kinds(hosts[0], self.allowed_ports()) if len(hosts) == 1 else set()
        if not self.hosts:
            name = hosts[0].strip() if len(hosts) == 1 and config.HOST_RE.fullmatch(hosts[0].strip()) else "that host name"
            self.denial = (403, f"this preview does not answer to {name}" + ("" if config.SHARED else
                           " (LATEX_LIVE_HOST or LATEX_LIVE_ALLOWED_HOSTS adds a name; see latex-live's setup guide)"), None)
            return None
        if config.SHARED and config.IDENTITY_HEADER and self.headers.get_all(config.IDENTITY_HEADER) is not None:
            return self.proxy_user()  # (never by token or cookie: they stay out of the proxy's traffic)
        cookie = http.cookies.SimpleCookie(self.headers.get("Cookie", "")).get("latex_live")
        cookie = cookie.value if cookie else ""
        presented = self.token_user(query.get("t", [""])[0])  # (a link: always a new session, whatever the cookie)
        if presented:
            user, key, subject = presented
            self.set_cookie = new_session(key, subject)
            return user
        if session := self.session_user(cookie):
            user, key, subject, issued = session
            if time.time() - issued > config.SESSION_RENEW:  # (a browser in use keeps its sign-in)
                self.set_cookie = new_session(key, subject)
            return user
        if presented := self.token_user(cookie):  # (the token itself as the cookie: from before sessions, or from the hub)
            user, key, subject = presented
            if not self.headers.get("X-Latex-Live-Paper"):  # (the hub keeps its own cookie, and drops a preview's)
                self.set_cookie = new_session(key, subject)
            return user
        return None

    def token_user(self, value):
        """(principal, token, session subject) for a token: the owner (the instance's token), or in shared mode the
        user whose personal link it is; else None."""
        if not value:
            return None
        if same_secret(value, self.server.token):  # (never with an empty token: same_secret refuses one, as load_token does)
            return owner_principal("owner-token"), self.server.token, "o"
        for entry in users().values() if config.SHARED else ():
            if entry["token"] and same_secret(value, entry["token"]):
                return principal(entry, "token"), entry["token"], "u" + b64(entry["id"].encode())
        return None

    def session_user(self, value):
        """(principal, token, subject, when it was issued) for a session cookie (new_session) that this server
        issued for a token that still works, within SESSION_AGE; else None."""
        found = session_subject(value) if value else None
        if not found:
            return None
        issued, subject, payload, mac = found
        if not -300 < time.time() - issued < config.SESSION_AGE:  # (a few minutes' grace for the clock)
            return None
        if subject == "o":
            user, key = owner_principal("owner-token"), self.server.token
        else:
            entry = users().get(unb64(subject[1:]) or "") if config.SHARED and subject.startswith("u") else None
            if not entry or not entry["token"]:
                return None
            user, key = principal(entry, "token"), entry["token"]
        return (user, key, subject, issued) if same_secret(mac, session_mac(key, payload)) else None

    def origin_allowed(self):
        """Whether a POST comes from the viewer's own page, so that no other site can make a browser act: the
        Origin is the public one (shared mode) when the Host is the public or the proxy's, else one of this server's
        own names of the same kind as the Host (host_kinds: a loopback one on its port or the hub's when the Host is
        loopback, a LATEX_LIVE_HOST or LATEX_LIVE_ALLOWED_HOSTS name when the Host is one). Without Origin (browsers
        send one with every POST), only from a program with a token (curl, check.py), at one of its own names."""
        origins = self.headers.get_all("Origin") or []
        if not origins:
            return bool(self.hosts & {"loopback", "named"}) and self.principal["via"] != "proxy"
        origin = origins[0].strip() if len(origins) == 1 else ""
        if self.hosts & {"public", "proxy"} and config.PUBLIC_ORIGIN and origin == config.PUBLIC_ORIGIN:
            return True
        m = ORIGIN_RE.fullmatch(origin)
        return bool(m and host_kinds(m.group(2), self.allowed_ports(), m.group(1)) & self.hosts & {"loopback", "named"})

    def logout(self):
        """POST logout: forget this browser's session, by a cookie that expires at once. The cookie is the host's,
        whatever the port, so every preview on this host name forgets it; the link's token still works. Signed in
        by the proxy, there is nothing to forget here."""
        if self.principal["via"] == "proxy":
            return self.send_json(200, {"proxy": True})
        self.set_cookie = ""
        self.send_json(200, {"signedOut": True})

    def proxy_user(self):
        """The user the trusted proxy names in IDENTITY_HEADER (exactly once), or None. Any program on
        this machine can connect and send that header, so it counts only with the proxy's Host
        (LATEX_LIVE_PROXY_HOST) and from a socket of the proxy's uid (LATEX_LIVE_PROXY_UID), where set."""
        values = self.headers.get_all(config.IDENTITY_HEADER)
        name = values[0].strip() if len(values) == 1 else ""
        entry = None
        if not USER_ID_RE.fullmatch(name):
            self.denial = (403, f"expected one user name in {config.IDENTITY_HEADER}", None)
        elif config.PROXY_HOST and "proxy" not in self.hosts:
            self.denial = (403, f"{config.IDENTITY_HEADER} counts only from the proxy (its Host)", None)
        elif config.PROXY_UID is not None and peer_uid(self.client_address, self.connection.getsockname()) != config.PROXY_UID:
            self.denial = (403, f"{config.IDENTITY_HEADER} counts only from the proxy (its uid)", None)
        elif not (entry := users().get(name)):
            owner = owner_name(self.server)
            text = f"You are signed in as {name}, but this preview isn't shared with you. Ask {owner} to add you."
            self.denial = (403, text, unshared_page(name, owner))
        return entry and principal(entry, "proxy")

    def deny(self):
        """Answer a request that authorize turned down: 403, as a page for a browser where there is one
        (made only now, if it is a function), else as text."""
        status, text, page = self.denial
        page = page() if callable(page) else page
        if page and "text/html" in self.headers.get("Accept", ""):
            return self.send_html(page, status)
        if page:
            return self.send_text(status, text)
        self.send_error(status, text)

    def do_HEAD(self):  # (not served: SimpleHTTPRequestHandler's would skip the token)
        self.send_error(405)

    def end_headers(self):
        if getattr(self, "set_cookie", None) is not None:  # (Secure where the browser reaches it over https: shared mode's public URL)
            secure = "; Secure" if config.SHARED and (config.PUBLIC_ORIGIN or "").startswith("https:") and "loopback" not in getattr(self, "hosts", ()) else ""
            age = config.SESSION_AGE if self.set_cookie else 0  # ("": the logout's, which expires at once)
            self.send_header("Set-Cookie", f"latex_live={self.set_cookie}; Path=/; HttpOnly; SameSite=Strict; Max-Age={age}{secure}")
        if self.path.startswith(("/viewer.html", "/viewer/")):  # revalidate, so an update shows on the next reload
            self.send_header("Cache-Control", "no-cache")
        # No other site may frame the viewer, nor learn its URL. Outside shared mode, VS Code's Simple Browser (a
        # webview) may frame the owner's own: modern browsers follow frame-ancestors and ignore X-Frame-Options then.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY" if config.SHARED else "SAMEORIGIN")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'" if config.SHARED else FRAME_ANCESTORS)
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def send_json(self, status, value):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.write_body(body)

    def send_pdf(self, pdf, content_type="application/pdf"):
        """A PDF (or another download: the arXiv package), streamed from its file (open_output: in shared mode
        never through a link), in parts of 256 KB."""
        f = open_output(pdf)
        if f is None:
            return self.send_error(404)
        with f:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(os.fstat(f.fileno()).st_size))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                shutil.copyfileobj(f, self.wfile, 1 << 18)
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True

    def write_body(self, body):
        """Write a response body in parts of 256 KB: the socket timeout bounds each part's write, not a
        whole PDF or source file on a slow link. A client that leaves meanwhile (a reload cuts a PDF's
        download short) just stops it."""
        view = memoryview(body)
        try:
            for i in range(0, len(body), 1 << 18):
                self.wfile.write(view[i:i + (1 << 18)])
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def send_html(self, text, status=200):
        body = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.write_body(body)

    def send_text(self, status, text):
        body = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.write_body(body)

    def log_message(self, *args):  # the viewer polls twice a second; keep the terminal clean
        pass


def json_query(req, *keys):
    """A JSON body's keys as parse_qs gives a query string's (strings and integers), for the handlers
    that GET requests share."""
    return {key: [str(req[key])] for key in keys if isinstance(req.get(key), (str, int)) and not isinstance(req.get(key), bool)}


PAGE_STYLE = "<style>body { font: 15px system-ui, sans-serif; background: #323639; color: #eee; margin: 3em; max-width: 40em; }</style>"


def unshared_page(name, owner):
    """The page for someone the proxy signed in whom the users file doesn't list."""
    return f"""<!doctype html><meta charset="utf-8"><title>latex-live: not shared with you</title>
{PAGE_STYLE}
<h2>This preview isn't shared with you</h2>
<p>You are signed in as <b>{html.escape(name)}</b>, but this preview isn't shared with you. Ask {html.escape(owner)} to add you.</p>"""


def signed_out_page():
    """The page for a browser that signed out (the viewer reloads with ?signed-out=1)."""
    return f"""<!doctype html><meta charset="utf-8"><title>latex-live: signed out</title>
{PAGE_STYLE}
<h2>Signed out</h2>
<p>This browser no longer opens the previews on this address. To sign in again, open your link (the one with
<code>?t=</code> in it).</p>"""


def bad_link_page(owner):
    """The page for a browser in shared mode without a link that works: an old or revoked personal link,
    or none (it says nothing about the link itself, nor where anything is)."""
    return f"""<!doctype html><meta charset="utf-8"><title>latex-live: this link doesn't work</title>
{PAGE_STYLE}
<h2>This link doesn't work</h2>
<p>It may be old, or your access may have ended. Ask {html.escape(owner)} for a new one.</p>"""
