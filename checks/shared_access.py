"""Who is asking in shared mode, and what each may see and do: Host names, the identity header, tokens and cookies,
GET me per role (identity_checks); every endpoint per role, non-owners pinned to the document, the Origin rules
for POSTs, POST body limits, the viewer's security headers, and SyncTeX as JSON (role_checks). Extend role_checks
with each new endpoint."""

import gzip
import json
import os
import re
import secrets
import urllib.parse

from .base import HERE, SHARED_PORT, fail, ok, redact, wait_for
from .helpers import CAPS, JS_TYPE_RE, editor_calls, post_length, put_bytes, read_bytes, read_text, text_point
from .shared_helpers import (
    PROXY_HOST, PUBLIC, as_editor, as_viewer, expect, expect_dict, header, line_of, send, settled, via_proxy,
    with_token, write_users)


# What each role can do in shared mode (no one switches documents: a shared preview serves one; everyone may download
# the paper without its notes), and commenting only with a note macro the paper defines (check_me).
ROLE_CAN = {"viewer": ("download",), "commenter": ("comment", "download"), "editor": ("comment", "edit", "rebuild", "compare", "download"),
            "owner": ("comment", "edit", "rebuild", "compare", "openHost", "download")}


def check_me(me, what, role, defined=True, **fields):
    """GET me's answer: shared mode, the owner's name, the role and its capabilities (ROLE_CAN; commenting,
    but for the owner, only if the paper defines the user's note macro: defined, its macroDefined), and
    the given fields."""
    want = {"role": role, "shared": True, "owner": "Owner Example", "macroDefined": defined, **fields}
    got = {key: me.get(key) for key in want}
    can = {c: c in ROLE_CAN[role] and (c != "comment" or defined or role == "owner") for c in CAPS}
    got_can = {c: (me.get("can") if isinstance(me.get("can"), dict) else {}).get(c) for c in CAPS}
    if got != want or got_can != can:
        fail(f"GET me for {what}: {got}, can {got_can}; not {want}, can {can}")


def identity_checks(sh):
    """Who is asking: the Host allowlist, the identity header and its edge cases, tokens and cookies, GET me
    for each kind of user, and users added (or a broken users file) without a restart."""
    owner, tk = sh["token"], sh["users"]["tk"]["token"]
    cookie, proxy = [("Cookie", f"latex_live={owner}")], [("Host", PROXY_HOST)]
    for host in ("evil.example", f"evil.example:{SHARED_PORT}", "paper.invalid.evil.example:8443", "localhost:5000",
                 f"127.0.0.1:{SHARED_PORT + 1}"):
        expect(send("GET", "/me", [("Host", host)] + cookie), 403, f"the owner's cookie with Host {host} (DNS rebinding)")
    for host in (f"localhost:{SHARED_PORT}", "localhost", f"127.0.0.1:{SHARED_PORT}", f"[::1]:{SHARED_PORT}"):
        expect(send("GET", "/me", [("Host", host)] + cookie), 200, f"the owner's cookie with Host {host}")
    expect(send("GET", "/me", [("Host", "paper.invalid:8443"), ("Cookie", f"latex_live={tk}")]), 200,
           "a personal link's cookie with the public Host")
    ok("the shared instance answers loopback names on its own port, and the public and the proxy's Host names, only")

    expect(send("GET", "/me", proxy), 403, "the proxy's Host without the identity header or a token")
    for values in (("ed", "ed"), ("ed", "cm"), ("",)):
        expect(send("GET", "/me", proxy + [("X-Test-User", v) for v in values]), 403, f"identity headers {values}")
    for name in ("ED", "ed,cm", "ed cm", "../ed", "\u00e9"):
        expect(send("GET", "/me", proxy + [("x-test-user", name)]), 403, f"the identity {name!r}, which no user has")
    unknown = send("GET", "/viewer.html?file=main.pdf", proxy + [("X-Test-User", "stranger"), ("Accept", "text/html")])
    page = unknown[2].decode(errors="replace")
    if unknown[0] != 403 or "stranger" not in page or "Owner Example" not in page:
        fail(f"a signed-in user who is not in the users file: {unknown[0]}, not 403 naming them and the owner ({page[:300]!r})")
    for host in ("paper.invalid:8443", f"{PROXY_HOST}:80", f"localhost:{SHARED_PORT}"):
        expect(send("GET", "/me", [("Host", host), ("X-Test-User", "ed")] + cookie), 403,
               f"the identity header with Host {host}, not the proxy's (even with the owner's cookie)")
    me = expect_dict(via_proxy("ed", f"/me?t={owner}"), 200, "ed through the proxy, with the owner's ?t=")
    other = expect_dict(via_proxy("vw", "/me", headers=cookie), 200, "vw through the proxy, with the owner's cookie")
    spaced = expect_dict(send("GET", "/me", proxy + [("X-Test-User", " ed ")]), 200, "the identity ' ed '")
    if (me.get("id"), other.get("id"), spaced.get("id")) != ("ed", "vw", "ed"):
        fail(f"through the proxy, a token or spaces changed who is asking: {me.get('id')}, {other.get('id')}, {spaced.get('id')}")
    ok("the identity header counts only alone, non-empty, a known user, with the proxy's Host; it beats tokens (no cookie set)")

    users = sh["users"]
    for uid in ("owner1", "ed", "cm", "vw"):  # (the paper defines note macros for all but the viewer)
        user = users[uid]
        check_me(expect_dict(via_proxy(uid, "/me"), 200, f"GET me for {uid}"), f"{uid} through the proxy", user["role"],
                 defined=uid != "vw", id=uid, name=user["name"], macro=user["macro"])
    for token, what, role, fields in ((tk, "the personal link", "editor",
                                       {"id": "tk", "name": "Toni Kay", "macro": "tk", "defined": False}),
                                      (owner, "the owner's token", "owner", {})):
        first = send("GET", f"/me?t={token}")
        check_me(expect_dict(first, 200, f"{what} as ?t="), f"{what} as ?t=", role, **fields)
        set_cookie = header(first, "Set-Cookie") or ""
        if (not set_cookie.startswith(f"latex_live={token}") or "httponly" not in set_cookie.lower()
                or "samesite=strict" not in set_cookie.lower() or re.search(r";\s*secure\b", set_cookie, re.I)):
            fail(f"{what} as ?t= sets the cookie {redact(set_cookie)!r}: want latex_live, HttpOnly, SameSite=Strict, not Secure (http)")
        check_me(expect_dict(with_token(token, "/me"), 200, f"{what} as a cookie"), f"{what} as a cookie", role, **fields)
    for path, headers, what in (("/me?t=%C3%A9", [], "?t= with a non-ASCII character"),
                                ("/me", [("Cookie", "latex_live=\u00e9")], "a cookie with a non-ASCII character"),
                                ("/me", [("Cookie", "latex_live=" + "x" * 22)], "a wrong token")):
        r = send("GET", path, headers, quiet=True)
        if r is None:
            fail(f"{what} drops the connection (hmac.compare_digest raised?)")
        if r[0] != 403 or (header(r, "Content-Type") or "").startswith("text/html"):
            fail(f"{what}: {r[0]} {header(r, 'Content-Type')}, not 403 (and no page: the client did not ask for HTML)")
    ok("GET me answers for each role, through the proxy or a personal link (its cookie: HttpOnly, SameSite=Strict),"
       " and says whether the paper defines the user's note macro (commenting needs it); no one switches documents")

    for path, what in ((f"/viewer.html?file=main.pdf&t={secrets.token_urlsafe(18)}", "a personal link that is not (or no longer) valid"),
                       ("/viewer.html?file=main.pdf", "no link at all")):
        r = send("GET", path, [("Accept", "text/html,application/xhtml+xml")])
        page = r[2].decode(errors="replace")
        if (r[0] != 403 or not (header(r, "Content-Type") or "").startswith("text/html") or "Owner Example" not in page
                or sh["state"] in page or header(r, "Set-Cookie")):
            fail(f"a browser with {what}: {r[0]} {header(r, 'Content-Type')}, not a 403 page that names the owner to ask"
                 f" (and says nothing about the state folder): {page[:300]!r}")
    ok("a browser without a working link gets a page that says whom to ask")

    users["nw"] = {"name": "Nia West", "email": "nia@example.org", "macro": "nw", "role": "viewer"}
    write_users(sh)
    wait_for("a user added to the users file to get in (no restart)", lambda: via_proxy("nw", "/me")[0] == 200, timeout=10, step=0.2)
    write_users(sh, "{ not JSON")
    for uid in ("ed", "nw"):
        expect(via_proxy(uid, "/me"), 200, f"{uid} once the users file is broken (the last good one stays)")
    with open(sh["log"], errors="replace") as f:
        if "still using the users as they were" not in f.read():
            fail("the shared-mode instance did not read the broken users file (or said nothing about it)")
    write_users(sh)
    ok("the users file is read again when it changes; a broken one leaves the last good one in place")


READS = ("/viewer.html?file=main.pdf", "/pdf/main.pdf", "/pdf/main-clean.pdf", "/status/main.pdf",
         "/outline/main.pdf", "/notes/main.pdf", "/authors/main.pdf", "/graphics/main.pdf", "/margin/main.pdf", "/floats/main.pdf",
         "/docs", "/me", "/pdfjs/build/pdf.min.mjs", "/viewer/main.js", "/viewer/viewer.css")


def role_checks(sh):
    """What each role may do (reads, the role table, owner-only actions), the pinning of non-owners to the
    shared document, the old GET actions gone, CSRF (the Origin rules, no CORS), POST body limits, and
    SyncTeX as JSON that never opens the editor."""
    root, owner = sh["root"], sh["token"]
    for path in READS:
        as_viewer(path)
    authors = as_viewer("/authors/main.pdf")
    if not isinstance(authors, list) or not {"eb", "ed", "cm"} <= {a.get("macro") for a in authors if isinstance(a, dict)}:
        fail(f"/authors does not list every note macro for a viewer: {authors}")
    hidden = ("Quagga private draft note", "a note in a branch that is off")  # (\iffalse, and \ifnum0=1: not in the PDF)
    seen = {uid: json.dumps(expect(via_proxy(uid, "/notes/main.pdf"), 200, f"GET notes for {uid}")) for uid in ("vw", "cm", "ed")}
    if any(h in seen[uid] for h in hidden for uid in ("vw", "cm")) or not all(h in seen["ed"] for h in hidden) \
            or not all("An owner note to resolve" in seen[uid] for uid in seen):
        fail(f"/notes for a viewer or commenter lists notes the PDF doesn't show, or misses one it does (or the editor's"
             f" misses a hidden one): {seen}")
    for uid in ("vw", "cm"):
        info = expect_dict(via_proxy(uid, "/status/main.pdf?base=push"), 200, f"GET status for {uid}")
        if {"head", "push", "diff", "source"} & set(info) or not info.get("version"):
            fail(f"/status for {uid} has more than the current PDF's state: {sorted(info)}")
        floats = expect(via_proxy(uid, "/floats/main.pdf"), 200, f"GET floats for {uid}")
        if any({"keys", "file", "line", "last"} & set(f) for f in floats):
            fail(f"/floats for {uid} names labels or source lines: {floats[:2]}")
        for name in ("main-diff.pdf", "main-head.pdf", "main-push.pdf", "main-head-clean.pdf"):
            for kind in ("pdf", "status"):
                expect(via_proxy(uid, f"/{kind}/{name}"), 403, f"GET {kind} {name} for {uid} (an earlier version: editors only)")
        expect(via_proxy(uid, "/baseline", "POST", {"kind": "head"}), 403, f"POST baseline for {uid} (editors only)")
        if expect(via_proxy(uid, "/graphics/main.pdf"), 200, f"GET graphics for {uid}") != []:
            fail(f"/graphics for {uid} lists the figure files and where the sources include them")
        note = next(n for n in json.loads(seen[uid]) if "An owner note to resolve" in n.get("text", ""))
        expect(via_proxy(uid, f"/forward?file={urllib.parse.quote(note['file'])}&line={note['line']}"), 200,
               f"forward search for {uid} to a note the PDF shows (the notes list's rows)")
        for query in ("file=main.tex&line=9", f"file={urllib.parse.quote(note['file'])}&line={note['line'] + 1}", "file=nope.tex&line=1"):
            refused = via_proxy(uid, f"/forward?{query}")
            if refused[0] != 404 or b"that line is not in the PDF" not in refused[2]:
                fail(f"forward search for {uid} to a line that is no note's ({query}): {refused[0]} {refused[2][:120]!r}")
    expect(via_proxy("ed", "/status/main-diff.pdf"), 200, "GET status main-diff.pdf for the editor")
    as_editor("/forward?file=main.tex&line=9")
    ok("viewers and commenters see the current PDF and what it shows: not notes in false branches, the baselines and diff,"
       " the build's error messages, the figures' labels and source lines, nor the figure files; their forward search"
       " goes only to the notes they see; editors see all of it")
    home = via_proxy("vw", "/")
    location = header(home, "Location") or ""
    if home[0] != 302 or not location.endswith("viewer.html?file=main.pdf") or "/p/" in location:
        fail(f"GET / in shared mode: {home[0]} to {header(home, 'Location')}, not to viewer.html?file=main.pdf without a hub prefix")
    script = via_proxy("ed", "/codemirror/view.js")
    if (script[0] != 200 or script[2] != read_bytes(os.path.join(HERE, "codemirror", "view.js"))
            or not JS_TYPE_RE.match(header(script, "Content-Type") or "")):
        fail(f"/codemirror/view.js for the editor: {script[0]} {header(script, 'Content-Type')}")
    for path in ("/codemirror/", "/codemirror/../live.py", "/codemirror/%2e%2e/live.py", "/codemirror/nope.js",
                 "/viewer/", "/viewer/../live.py", "/viewer/%2e%2e/live.py", "/viewer/nope.js", "/viewer/%00.js"):
        expect(via_proxy("ed", path), 404, f"GET {path}")
    for path in ("/live.py", "/latexlive/config.py", "/VERSION", "/check.py"):  # (the checkout's other files: not even the owner's to read)
        expect(with_token(owner, path), 404, f"GET {path} for the owner")
    expect(via_proxy("vw", "/favicon.ico"), (200, 204), "GET /favicon.ico for a viewer (browsers ask on every page)")
    for uid in ("vw", "cm"):  # (the checks run poppler on the host: editors only)
        expect(via_proxy(uid, "/check/main.pdf"), 403, f"GET check for {sh['users'][uid]['role']} {uid}")
    expect(via_proxy("ed", "/check/main.pdf"), 200, "GET check for the editor")
    page = via_proxy("vw", "/viewer.html?file=main.pdf")
    for name, want in (("X-Content-Type-Options", "nosniff"), ("X-Frame-Options", "DENY"), ("Referrer-Policy", "no-referrer"),
                       ("Content-Security-Policy", "frame-ancestors 'none'")):
        if want not in (header(page, name) or ""):
            fail(f"the shared viewer's page is sent without {name}: {want} (it has {header(page, name)!r})")
    ok("a viewer reads everything about the shared document but the checks (editors only); the viewer's and the editor's"
       " modules are served, nothing else; /favicon.ico is answered; the viewer's page can't be framed or sniffed, and sends no"
       " referrer")

    for path in ("/status/appendix.pdf", "/notes/appendix.pdf", "/outline/appendix.pdf", "/authors/appendix.pdf", "/graphics/appendix.pdf",
                 "/margin/appendix.pdf", "/floats/appendix.pdf", "/check/appendix.pdf", "/pdf/reviews.pdf", "/pdf/appendix.pdf",
                 "/pdf/appendix-export.pdf", "/synctex?file=appendix.pdf&page=1&x=100&y=100&json=1"):
        expect(via_proxy("vw", path), (400, 403, 404), f"GET {path} for a viewer (only the shared document's names)")
    expect(with_token(owner, "/pdf/reviews.pdf"), 200, "the owner reading another PDF of the paper")
    expect(via_proxy("ed", "/note", "POST", {"action": "edit", "pdf": "appendix.pdf", "source": "appendix.tex", "line": 3, "macro": "ed",
                                             "tex": "x", "text": "y"}), (400, 403, 404), "a note for another document")
    ok("non-owners are pinned to the shared document")

    probe = {"path": "refs.bib", "base": "0" * 20, "text": "@misc{one}\n"}  # (a save on a version never seen: 409, writes nothing)
    owner_only = (("GET", "/instances", None), ("GET", "/papers", None), ("POST", "/switch", {"doc": "main"}),
                  ("POST", "/open", {"file": "main.tex", "line": 9}), ("POST", "/commit", {}))
    editor_up = (("GET", "/files", None), ("GET", "/file?path=body.tex", None), ("POST", "/file", probe), ("POST", "/rebuild", {}),
                 ("GET", "/commits", None), ("POST", "/diff", {"rev": "HEAD"}))
    for uid in ("vw", "cm", "ed"):
        for method, path, body in owner_only + (() if uid == "ed" else editor_up):
            expect(via_proxy(uid, path, method, body), 403, f"{method} {path} for {sh['users'][uid]['role']} {uid}")
    for result, who in ((with_token(owner, "/switch", "POST", {"doc": "appendix"}), "with the owner's token"),
                        (via_proxy("owner1", "/switch", "POST", {"doc": "appendix"}), "for the owner, through the proxy")):
        expect(result, 403, f"POST switch {who} (a shared preview serves one document)")
    for method, path, body, status in (("GET", "/files", None, 200), ("GET", "/file?path=body.tex", None, 200),
                                       ("GET", "/commits", None, 200), ("POST", "/file", probe, 409)):
        expect(via_proxy("ed", path, method, body), status, f"{method} {path} for the editor")
    listing = expect_dict(with_token(owner, "/instances"), 200, "GET instances for the owner")
    if listing.get("self") != sh["proc"].pid:
        fail(f"GET instances for the owner: {listing}")
    for path in ("/papers", "/files", "/commits"):
        expect(with_token(owner, path), 200, f"GET {path} for the owner")
    for path in ("/switch?doc=main", "/diff?rev=HEAD", "/open?file=main.tex&line=9"):
        if (r := with_token(owner, path))[0] == 200:
            fail(f"GET {path} still acts in shared mode (only POST may: it carries an Origin) ({r[2][:200]!r})")
    preflight = send("OPTIONS", "/note", [("Host", PROXY_HOST), ("X-Test-User", "ed"), ("Origin", "http://evil.example"),
                                          ("Access-Control-Request-Method", "POST"), ("Access-Control-Request-Headers", "content-type")])
    if any(key.lower().startswith("access-control-") for key, _ in preflight[1]):
        fail(f"OPTIONS is answered with CORS headers: {preflight[1]}")
    if editor_calls(sh["editor_log"]):
        fail(f"the editor stand-in ran: {editor_calls(sh['editor_log'])}")
    ok("roles: viewers read, commenters also comment, editors also edit, rebuild and compare, only the owner does the rest,"
       " but for switching documents, which no one does; the old GET actions are gone; no CORS")

    proxy, owners = [("Host", PROXY_HOST), ("X-Test-User", "ed")], [("Cookie", f"latex_live={owner}")]
    public = [("Host", "paper.invalid:8443"), ("Cookie", f"latex_live={sh['users']['tk']['token']}")]
    for headers, allowed, what in (
            (proxy + [("Origin", PUBLIC)], True, "the public origin, through the proxy"),
            (proxy, False, "no Origin, through the proxy"),
            (proxy + [("Origin", "null")], False, "Origin null, through the proxy"),
            (proxy + [("Origin", PUBLIC + "/")], False, "the public origin and a slash"),
            (proxy + [("Origin", "https://paper.invalid:8443")], False, "the public origin's host on https"),
            (proxy + [("Origin", "http://paper.invalid")], False, "the public origin's host on another port"),
            (proxy + [("Origin", "http://other.paper.invalid:8443")], False, "a sibling of the public host"),
            (proxy + [("Origin", f"http://localhost:{SHARED_PORT}")], False, "a loopback origin, through the proxy"),
            (owners, True, "a token and no Origin on a loopback Host (curl, check.py)"),
            (owners + [("Origin", "http://localhost:5000")], False, "a loopback origin on another port"),
            (owners + [("Origin", "http://localhost")], False, "a loopback origin on port 80"),
            (owners + [("Origin", f"http://localhost:{SHARED_PORT}")], True, "the localhost origin on its own port"),
            (owners + [("Origin", f"http://127.0.0.1:{SHARED_PORT}")], True, "the 127.0.0.1 origin"),
            (owners + [("Origin", f"http://[::1]:{SHARED_PORT}")], True, "the [::1] origin"),
            (owners + [("Origin", PUBLIC)], False, "the public origin on a loopback Host"),
            (owners + [("Origin", "null")], False, "Origin null with a token"),
            (owners + [("Origin", f"http://localhost.evil.example:{SHARED_PORT}")], False, "a look-alike of localhost"),
            (public + [("Origin", PUBLIC)], True, "a personal link with the public origin on the public Host"),
            (public, False, "a personal link without Origin on the public Host"),
            (public + [("Origin", f"http://localhost:{SHARED_PORT}")], False, "a loopback origin on the public Host")):
        result = send("POST", "/file", headers, probe)
        if result[0] != (409 if allowed else 403):
            fail(f"a POST with {what}: {result[0]} {result[2][:200]!r} (want {'409, past the Origin check' if allowed else '403'})")
    expect(send("POST", "/note", proxy + [("Origin", PUBLIC), ("Content-Type", "text/plain")], b"{}"), 415, "a POST that is not JSON")
    ok("POSTs need the public origin through the proxy and on the public Host, a loopback one on the instance's own port (or"
       " none, with a token) on loopback")

    ed = proxy + [("Origin", PUBLIC)]
    for path, length, wanted, what in (("/note", 100_001, (413,), "a note over 100 000 bytes"),
                                       ("/file", 9 << 20, (413,), "a file over 8 MB"),
                                       ("/note", "-1", (400, 411, 413), "a negative Content-Length"),
                                       ("/note", "many", (400, 411), "a Content-Length that is not a number")):
        if (status := post_length(SHARED_PORT, path, length, ed)) not in wanted:
            fail(f"{what}: {status or 'no answer within 10 s'}, not {' or '.join(map(str, wanted))}")
    expect(send("POST", "/file", ed, {**probe, "text": "x" * (1 << 20)}), 409, "a 1 MiB save (within the 8 MB limit for files)")
    ok("POST bodies: 100 000 bytes for actions, 8 MB for a file's save; a bad Content-Length is refused before reading")

    text = read_text(os.path.join(root, "body.tex"))
    page, x, y = text_point(as_editor, "body.tex", line_of(text, "Quokka narwhal"))
    query = f"/synctex?file=main.pdf&page={page}&x={x:.2f}&y={y:.2f}"
    for result, who in ((via_proxy("vw", query), "a viewer"), (via_proxy("ed", query), "the editor"),
                        (with_token(owner, query), "the owner")):
        if expect_dict(result, 200, f"GET synctex without json=1 for {who} (JSON in shared mode)").get("file") != "body.tex":
            fail(f"GET synctex without json=1 for {who}: {result[2][:200]!r}")
    box = as_editor("/forward?file=generated.inc&line=1")
    for share in (0.5, 0.3, 0.7):
        found = expect_dict(via_proxy("vw", f"/synctex?file=main.pdf&page={box['page']}&x={box['x'] + box['w'] * share:.2f}"
                                            f"&y={box['y'] + box['h'] / 2:.2f}&json=1"), 200, "synctex for generated text")
        if "file" in found and found["file"] is None:
            if not found.get("reason"):
                fail(f"synctex for text from generated.inc (not editable) gives no reason: {found}")
            break
    else:
        fail(f"synctex for text from generated.inc (not editable) does not answer file: null: {found}")
    if editor_calls(sh["editor_log"]):
        fail(f"SyncTeX opened the editor on the owner's machine in shared mode: {editor_calls(sh['editor_log'])}")

    # A line past the end of its file, as SyncTeX gives now and then (e.g. in a table that macros build), goes to
    # the file's last line, with a note saying so: for a moment, the shared copy's SyncTeX data says so of every
    # line of body.tex (the build has settled, so nothing rewrites it meanwhile).
    wait_for("the shared copy's build to settle", lambda: settled(sh), timeout=120)
    synctex = os.path.join(root, "build", "main-live.synctex.gz")
    data, st = read_bytes(synctex), os.stat(synctex)
    records = gzip.decompress(data).decode("latin-1")
    tags = re.findall(r"^Input:(\d+):.*/body\.tex$", records, re.M)
    moved = re.sub(r"^([\[(vhxkg$](?:%s)),\d+" % "|".join(tags), r"\1,99999", records, flags=re.M) if tags else records
    if moved == records:
        fail(f"no SyncTeX records of body.tex in {synctex}")
    try:
        put_bytes(synctex, gzip.compress(moved.encode("latin-1")))
        clamped = expect_dict(via_proxy("vw", query + "&json=1"), 200, "synctex for a line past the end of body.tex")
    finally:
        put_bytes(synctex, data)
        os.utime(synctex, ns=(st.st_atime_ns, st.st_mtime_ns))  # (as it was: what was computed from it still holds)
    last = text.count("\n") + (not text.endswith("\n"))
    if clamped.get("file") != "body.tex" or clamped.get("line") != last or not clamped.get("note"):
        fail(f"synctex for line 99999 of body.tex, which has {last}: {clamped}, not its last line, with a note")
    ok("SyncTeX answers JSON for everyone in shared mode (file: null where the editor can't go, the last line for one past"
       " the end), and opens nothing")
