"""Who is asking: the instance's token, shared mode's users file and its personal links, the
principals that requests come from, and the checks on a request's Host and peer."""

import hmac
import ipaddress
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import threading

from . import config


def load_token():
    """Stable per-user secret, so the viewer URL can be bookmarked. All checkouts share it: the
    browser keeps one cookie per host, whatever the port. A token from before (./token next to
    live.py) carries over, so its URLs and cookies keep working."""
    token = (config.read_text(config.TOKEN_FILE) or "").strip()
    if token:
        return token
    if os.path.exists(config.TOKEN_FILE):  # (never an empty token: it would let every request in)
        token = (config.read_text(config.TOKEN_FILE) or "").strip()  # (another instance may have linked it in just now)
        if token:
            return token
        sys.exit(f"latex-live: {config.TOKEN_FILE} is empty; delete it and start again")
    os.makedirs(config.STATE, mode=0o700, exist_ok=True)
    token = (config.read_text(os.path.join(config.HERE, "token")) or "").strip() or secrets.token_urlsafe(16)
    # Written whole to a file of its own, then linked into place: another instance starting at the
    # same time reads either no token or all of it, and whichever links first wins.
    fd, tmp = tempfile.mkstemp(dir=config.STATE, prefix=".token-")
    with os.fdopen(fd, "w") as f:
        f.write(token)
    try:
        os.link(tmp, config.TOKEN_FILE)
    except FileExistsError:
        pass
    finally:
        os.remove(tmp)
    return load_token()


def same_secret(given, secret):
    """Whether given is the secret, in constant time; False for an empty secret, and for anything
    hmac.compare_digest can't compare (it raises on non-ASCII text)."""
    try:
        return bool(secret) and hmac.compare_digest(given.encode("utf-8", "surrogatepass"), secret.encode("utf-8", "surrogatepass"))
    except (AttributeError, TypeError, UnicodeError):
        return False


LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1"}
LOOPBACK_ORIGIN_RE = re.compile(r"http://(?:localhost|127\.0\.0\.1|\[::1\])(?::[0-9]{1,5})?")  # (any port: forwards remap it)
ROLES = ("viewer", "commenter", "editor", "owner")  # each may do what the ones before it may (least_role)
USER_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]{0,63}")  # (the proxy's name for a user)
USER_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{16,128}")  # (a personal link's secret, e.g. secrets.token_urlsafe(18))


def load_users(path):
    """The users file, checked: {id: {id, name, email, macro, role, token}}; ValueError (or OSError)
    says what is wrong. The file is {"users": {"<id>": {"name", "email", "macro", "role", "token"}}}:
    <id> is the user's name at the proxy, role one of ROLES (the only field needed), macro their note
    macro without the backslash (to comment), token the secret of their personal link, and email the
    one their edits are committed under."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    entries = data.get("users") if isinstance(data, dict) else None
    if not isinstance(entries, dict):
        raise ValueError('expected {"users": {"<id>": {"role": "editor", ...}, ...}}')
    found, seen = {}, {"macro": {}, "token": {}}
    for id_, entry in entries.items():
        where = f"user {id_!r}"
        if not USER_ID_RE.fullmatch(id_):
            raise ValueError(f"{where}: an id is up to 64 letters, digits, '.', '_', '@' or '-'")
        if not isinstance(entry, dict) or entry.get("role") not in ROLES:
            raise ValueError(f"{where}: role must be one of {', '.join(ROLES)}")
        fields = {key: entry.get(key) for key in ("name", "email", "macro", "token")}
        for key in ("name", "email"):
            value = fields[key]
            if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 200
                                      or re.search(r"[\x00-\x1f\x7f<>]", value)):
                raise ValueError(f"{where}: {key} must be one line of text, without < or >")
        if fields["macro"] is not None and not (isinstance(fields["macro"], str) and re.fullmatch(r"\\?[a-zA-Z]{1,40}", fields["macro"])):
            raise ValueError(f'{where}: macro is the name of their note macro, e.g. "ab" for \\ab')
        if fields["token"] is not None and not (isinstance(fields["token"], str) and USER_TOKEN_RE.fullmatch(fields["token"])):
            raise ValueError(f"{where}: token must be 16 to 128 letters, digits, '-' or '_' (e.g. secrets.token_urlsafe(18))")
        macro = fields["macro"] and fields["macro"].lstrip("\\")
        for key, value in (("macro", macro), ("token", fields["token"])):
            if value and value in seen[key]:
                raise ValueError(f"{where} has the same {key} as user {seen[key][value]!r}")
            if value:
                seen[key][value] = id_
        found[id_] = {"id": id_, "name": (fields["name"] or id_).strip(), "email": fields["email"] and fields["email"].strip(),
                      "macro": macro, "role": entry["role"], "token": fields["token"]}
    return found


USERS = {"tried": None, "users": {}}  # the users file as last read, and the stat of the last try (users)
USERS_LOCK = threading.Lock()


def users():
    """The users file's entries (load_users), read again whenever it changes, so adding someone needs
    no restart. While it is broken or missing, the last good version stays (with one warning)."""
    try:
        st = os.stat(config.USERS_FILE)
        stamp = (st.st_mtime_ns, st.st_size, st.st_ino)
    except OSError as e:
        stamp = str(e)
    with USERS_LOCK:
        if stamp != USERS["tried"]:
            USERS["tried"] = stamp
            try:
                USERS["users"] = load_users(config.USERS_FILE)
                if os.stat(config.USERS_FILE).st_mode & 0o077:
                    print(f"=== latex-live: other users can read {config.USERS_FILE} (it holds personal links' tokens): chmod 600 it",
                          file=sys.stderr, flush=True)
            except (OSError, ValueError) as e:
                print(f"=== latex-live: {config.USERS_FILE}: {e}; still using the users as they were", file=sys.stderr, flush=True)
        return USERS["users"]


def principal(entry, via):
    """Who a request comes from: a users-file entry without its token, plus via: how it was checked
    ("owner-token", "token": a personal link, or "proxy": the trusted proxy's identity header)."""
    return {**{key: value for key, value in entry.items() if key != "token"}, "via": via}


def users_owner():
    """The users file's entry with role owner (shared mode), else None."""
    return next((u for u in users().values() if u["role"] == "owner"), None) if config.SHARED else None


def owner_principal(via):
    """The owner (whoever has the instance's token): the users file's entry with role owner, if there
    is one, else a nameless stand-in (owner_name names it)."""
    return principal(users_owner() or {"id": "owner", "name": None, "email": None, "macro": None, "role": "owner"}, via)


def owner_name(server):
    """The owner's name, for coauthors: from the users file, else git's user.name, else "the owner"."""
    if entry := users_owner():
        return entry["name"]
    if server.git_name is None:
        r = subprocess.run(["git", "-C", server.paper_root, "config", "user.name"], capture_output=True, text=True)
        server.git_name = r.stdout.strip()
    return server.git_name or "the owner"


def host_kinds(host):
    """What a Host header's value names, of "loopback", "public" (LATEX_LIVE_PUBLIC_URL's host, any
    port) and "proxy" (exactly LATEX_LIVE_PROXY_HOST): an empty set for any other name, which a
    shared instance refuses (a page on a DNS-rebinding name must not reach it)."""
    m = config.HOST_RE.fullmatch(host.strip())
    if not m:
        return set()
    name = m.group(1).lower().strip("[]")
    kinds = {"loopback"} if name in LOOPBACK_NAMES else set()
    if config.PUBLIC_HOST and name == config.PUBLIC_HOST:
        kinds.add("public")
    if config.PROXY_HOST and host.strip().lower() == config.PROXY_HOST.lower():
        kinds.add("proxy")
    return kinds


def is_loopback(address):
    """Whether an IP address is on this machine, IPv4-mapped ones too (::ffff:127.0.0.1, which older
    Pythons don't count)."""
    try:
        ip = ipaddress.ip_address(address.split("%")[0])
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    return ip.is_loopback or bool(mapped and mapped.is_loopback)


def proc_net_address(ip, port):
    """An address and port as Linux's /proc/net/tcp and tcp6 write them: hex, each 32-bit word of the
    address in host byte order."""
    raw = ip.packed
    return "".join("%08X" % int.from_bytes(raw[i:i + 4], sys.byteorder) for i in range(0, len(raw), 4)) + ":%04X" % port


def peer_uid(client, server):
    """The uid of the local socket at the client's (address, port) that is connected to this server's
    (address, port), from Linux's /proc/net/tcp6 and /proc/net/tcp; None if there is none (not Linux,
    or the client is gone: a closed socket is no longer its owner's, and shows as root's). An IPv4
    client (::ffff:a.b.c.d here) has an IPv4 socket, or an IPv6 one with mapped addresses."""
    try:
        (cip, cport), (sip, sport) = [(ipaddress.ip_address(pair[0].split("%")[0]), pair[1]) for pair in (client, server)]
    except ValueError:
        return None
    tables = [("/proc/net/tcp6", cip, sip)]
    if getattr(cip, "ipv4_mapped", None) and getattr(sip, "ipv4_mapped", None):
        tables.insert(0, ("/proc/net/tcp", cip.ipv4_mapped, sip.ipv4_mapped))
    for table, local, remote in tables:
        try:
            with open(table) as f:
                text = f.read()
        except OSError:
            continue
        key = f" {proc_net_address(local, cport)} {proc_net_address(remote, sport)} "
        i = text.find(key)
        while i >= 0:
            cols = text[i:text.find("\n", i)].split()  # local, remote, state, queues, timer, retransmits, uid, timeout, inode
            if len(cols) >= 9 and cols[8] != "0":
                return int(cols[6])
            i = text.find(key, i + 1)
    return None
