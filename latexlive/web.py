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

from . import config
from .auth import (
    USER_ID_RE, host_kinds, is_loopback, owner_name, owner_principal, peer_uid, principal, same_secret, users)
from .files import open_output


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
    address_family = socket.AF_INET6

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)  # accept IPv4 too
        super().server_bind()

    def handle_error(self, request, client_address):
        """Print what went wrong in a request, unless the client just left mid-answer (a reload cuts a
        download short): that is no error, and its traceback would only clutter the log."""
        if not isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
            super().handle_error(request, client_address)


class LoopbackServer(DualStackServer):
    """A shared instance's server: on every interface, like the others (so 127.0.0.1 and [::1] both
    work), but it drops a connection from another machine before reading anything: coauthors come
    through the proxy, which runs here. At most CONNECTIONS connections at once (each holds a thread
    until it ends or times out): past that, it drops new ones, which a public tunnel could otherwise
    open without end."""
    CONNECTIONS = 64

    def server_activate(self):
        self.slots, self.holding, self.slots_lock = threading.BoundedSemaphore(self.CONNECTIONS), set(), threading.Lock()
        super().server_activate()

    def verify_request(self, request, client_address):
        return is_loopback(client_address[0])

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


class BaseHandler(http.server.SimpleHTTPRequestHandler):
    """What every request of an instance and of the hub goes through: who is asking (authorize), and
    sending the answer. Without a route of its own (Handler, HubHandler), it answers nothing."""
    timeout = 60  # (a client that stops sending mid-request frees its thread)
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map, ".mjs": "text/javascript", ".js": "text/javascript",
                      ".css": "text/css"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=config.HERE, **kwargs)

    def authorize(self, query):
        """Who is asking (a principal), or None, with self.denial saying why (deny). Every client needs
        ?t=TOKEN once, then the cookie (forwarded traffic arrives via loopback too). In shared mode, a
        user's own token works the same way, or the trusted proxy names the user (proxy_user); and the
        Host must be one of this instance's names (host_kinds)."""
        self.set_cookie, self.hosts = None, set()
        self.denial = (403, "open your link again (the one with ?t= in it)", None)
        if config.SHARED:  # (a browser with an old or revoked personal link, or none, gets a page that says what to do)
            self.denial = (*self.denial[:2], lambda: bad_link_page(owner_name(self.server)))
            hosts = self.headers.get_all("Host") or []
            self.hosts = host_kinds(hosts[0]) if len(hosts) == 1 else set()
            if not self.hosts:
                self.denial = (403, "this preview does not answer to that host name", None)
                return None
            if config.IDENTITY_HEADER and self.headers.get_all(config.IDENTITY_HEADER) is not None:
                return self.proxy_user()  # (never by token or cookie: they stay out of the proxy's traffic)
        cookie = http.cookies.SimpleCookie(self.headers.get("Cookie", "")).get("latex_live")
        cookie = cookie.value if cookie else ""
        for value in (cookie, query.get("t", [""])[0]):
            user = self.token_user(value)
            if user:
                if value != cookie:
                    self.set_cookie = value
                return user
        return None

    def token_user(self, value):
        """The principal a token is for: the owner (the instance's token), or in shared mode the user
        whose personal link it is; else None."""
        if not value:
            return None
        if same_secret(value, self.server.token):  # (never with an empty token: same_secret refuses one, as load_token does)
            return owner_principal("owner-token")
        for entry in users().values() if config.SHARED else ():
            if entry["token"] and same_secret(value, entry["token"]):
                return principal(entry, "token")
        return None

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
        (made only now, if it is a function)."""
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
        if getattr(self, "set_cookie", None):  # (Secure where the browser reaches it over https: shared mode's public URL)
            secure = "; Secure" if config.SHARED and (config.PUBLIC_ORIGIN or "").startswith("https:") and "loopback" not in getattr(self, "hosts", ()) else ""
            self.send_header("Set-Cookie", f"latex_live={self.set_cookie}; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000{secure}")
        if self.path.startswith(("/viewer.html", "/viewer/")):  # revalidate, so an update shows on the next reload
            self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        if config.SHARED:  # (no other site may frame the viewer, nor learn its URL; VS Code's Simple Browser frames an owner's own)
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
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

    def send_pdf(self, pdf):
        """A PDF, streamed from its file (open_output: in shared mode never through a link), in parts of 256 KB."""
        f = open_output(pdf)
        if f is None:
            return self.send_error(404)
        with f:
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
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


def bad_link_page(owner):
    """The page for a browser in shared mode without a link that works: an old or revoked personal link,
    or none (it says nothing about the link itself, nor where anything is)."""
    return f"""<!doctype html><meta charset="utf-8"><title>latex-live: this link doesn't work</title>
{PAGE_STYLE}
<h2>This link doesn't work</h2>
<p>It may be old, or your access may have ended. Ask {html.escape(owner)} for a new one.</p>"""
