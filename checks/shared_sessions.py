"""Shared mode's sessions (session_checks): a personal link's session cookie lasts only as long as its token, signing
out forgets a browser's session (and offers nothing to those whom the proxy signs in), and the instance's token file
is made private at the start."""

import os
import re

from .base import fail, ok
from .shared_helpers import PUBLIC, expect_dict, header, send, via_proxy, with_token, write_users


def session_checks(sh):
    """A personal link's session works until its token changes in the users file, then no longer (nor does the
    old link); the owner's token and the link's give a new session each time. Sign-out is offered to those who came
    with a token, not through the proxy; it expires the cookie only from the viewer's page; the proxy's users have
    nothing to forget. The instance's token file, readable by others before the start, is the owner's only now."""
    user = sh["users"]["tk"]
    public = "paper.invalid:8443"
    sessions = []
    for _ in range(2):
        first = send("GET", f"/me?t={user['token']}", [("Host", public)])
        m = re.match(r"latex_live=(s1\.[^;]+)", header(first, "Set-Cookie") or "")
        if first[0] != 200 or not m:
            fail(f"a personal link on the public Host: {first[0]}, no session cookie")
        sessions.append(m.group(1))
    if sessions[0] == sessions[1] or user["token"] in "".join(sessions):
        fail("a personal link gives the same session twice, or one that holds its token")
    for value in sessions:
        expect_dict(with_token(value, "/me", host=public), 200, "a personal link's session cookie")
    me = expect_dict(with_token(sessions[0], "/me", host=public), 200, "GET me with a personal link's session")
    proxied = expect_dict(via_proxy("ed", "/me"), 200, "GET me through the proxy")
    if me.get("can", {}).get("signOut") is not True or proxied.get("can", {}).get("signOut") is not False:
        fail(f"sign-out is offered to {me.get('can', {}).get('signOut')} (personal link) and {proxied.get('can', {}).get('signOut')}"
             " (the proxy's user): want True and False")

    old, user["token"] = user["token"], user["token"][::-1]
    write_users(sh)
    try:
        if (with_token(sessions[0], "/me", host=public)[0] != 403 or with_token(old, "/me", host=public)[0] != 403
                or with_token(user["token"], "/me", host=public)[0] != 200):
            fail("a personal link's session (or the link) still works after its token changed, or the new link doesn't")
    finally:
        user["token"] = old
        write_users(sh)
    expect_dict(with_token(sessions[1], "/me", host=public), 200, "a session of a link whose token is back as it was")
    ok("a personal link's session cookie is new on each visit, never the token, and works only while its token does")

    refused = with_token(sessions[1], "/logout", "POST", {}, host=public, origin="http://evil.example")
    out = with_token(sessions[1], "/logout", "POST", {}, host=public, origin=PUBLIC)
    cleared = header(out, "Set-Cookie") or ""
    if refused[0] != 403 or out[0] != 200 or not cleared.startswith("latex_live=;") or "max-age=0" not in cleared.lower():
        fail(f"sign-out with a personal link: {refused[0]} from another site, {out[0]} {cleared!r} from the viewer's page"
             " (want 403, then 200 with a cookie that expires at once)")
    proxy_out = via_proxy("ed", "/logout", "POST", {})  # (via_proxy fails if it sets a cookie)
    if expect_dict(proxy_out, 200, "sign-out through the proxy").get("proxy") is not True:
        fail(f"sign-out through the proxy: {proxy_out[2][:200]!r}")
    ok("signing out expires a personal link's cookie, only from the viewer's page; through the proxy there is nothing to forget")

    mode = os.stat(os.path.join(sh["state"], "token")).st_mode & 0o777
    with open(sh["log"], errors="replace") as f:
        said = "readable by you only now" in f.read()
    if mode != 0o600 or not said:
        fail(f"the shared instance's token file, readable by others before the start, is {oct(mode)} now"
             f" ({'said' if said else 'not said'}), not 600")
    ok("the shared instance makes its token file private at the start, saying so")
