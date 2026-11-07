"""The downloads in shared mode: the paper without its notes and the owner's arXiv package (export_checks), and with
its notes in the margin (review_shared_checks), started only by a POST from the viewer's own page, one build for
several asking at once, confined."""

import os
import threading
import time

from .base import SHARED_PORT, fail, ok, wait_for
from .helpers import editor_calls, read_bytes
from .papers import composed
from .shared_helpers import PROXY_HOST, PUBLIC, expect, expect_dict, header, send, settled, via_proxy


def export_checks(sh):
    """The download without notes in shared mode (POST /export): a viewer may ask for it (a copy of what they read),
    several asking at once share one build, it is built once per build of the document, in the hardened image (TeX
    can't read absolute paths there), and coauthors get that PDF."""
    root = sh["root"]
    wait_for("the shared copy's build to settle", lambda: settled(sh), timeout=120)
    asked, users = {}, ("vw", "cm", "ed", "vw")
    askers = [threading.Thread(target=lambda i=i, uid=uid: asked.update({i: send("POST", "/export", [
        ("Host", PROXY_HOST), ("X-Test-User", uid), ("Origin", PUBLIC)], {}, quiet=True)})) for i, uid in enumerate(users)]
    for thread in askers:
        thread.start()
    for thread in askers:
        thread.join()
    if sorted(asked) != list(range(len(users))) or None in asked.values():
        fail(f"of {len(users)} requests for the download without notes at once, some got no answer")
    answers = [expect_dict(asked[i], 200, f"POST export for {uid}, with others at once") for i, uid in enumerate(users)]
    if len({(a.get("source"), a.get("time")) for a in answers}) != 1 or any(a.get("state") not in ("running", "done") for a in answers):
        fail(f"requests for the download without notes at once did not share one build: {answers}")

    def exported():
        e = expect_dict(via_proxy("vw", "/export", "POST", {}), 200, "POST export for a viewer")
        if e.get("state") == "failed":
            fail(f"the shared copy's download without notes failed: {e.get('msg')}")
        return e if e.get("state") == "done" else None

    done = wait_for("the shared copy's download without notes", exported, timeout=240)
    pdf = via_proxy("vw", "/pdf/main-export.pdf")
    if pdf[0] != 200 or header(pdf, "Content-Type") != "application/pdf" or not pdf[2].startswith(b"%PDF"):
        fail(f"/pdf/main-export.pdf for a viewer: {pdf[0]} {header(pdf, 'Content-Type')}")
    with open(os.path.join(root, "build", "export", "main", "main-export.log"), errors="replace") as f:
        texlog = f.read()
    if "latex-live-check: TeX refused an absolute path" not in texlog:
        fail("the download without notes " + ("read /etc/passwd: its TeX runs without openin_any=p, not in the hardened image"
                                               if "TeX read an absolute path" in texlog else "logged nothing from the probe"))
    if expect_dict(via_proxy("cm", "/export", "POST", {}), 200, "POST export for a commenter").get("version") != done["version"]:
        fail("asking again rebuilt the shared copy's download without notes, though the document had not changed")
    ok("a viewer may download the shared paper without its notes: one build for several asking at once, once per build of"
       " the document, in the hardened image")

    def packaged():
        a = expect_dict(via_proxy("owner1", "/arxiv", "POST", {}), 200, "POST arxiv for the owner")
        if a.get("state") == "failed":
            fail(f"the shared copy's arXiv package failed: {a.get('msg')}")
        return a if a.get("state") == "done" else None

    made = wait_for("the shared copy's arXiv package", packaged, timeout=240)
    tarball = via_proxy("owner1", "/arxiv.tar.gz")
    if tarball[0] != 200 or header(tarball, "Content-Type") != "application/gzip" or not tarball[2].startswith(b"\x1f\x8b"):
        fail(f"/arxiv.tar.gz for the owner: {tarball[0]} {header(tarball, 'Content-Type')}")
    with open(os.path.join(root, "build", "arxiv", "main", "test", "main.log"), errors="replace") as f:
        texlog = f.read()
    if "latex-live-check: TeX refused an absolute path" not in texlog or not made["same"]:
        fail(f"the shared copy's arXiv package was not built alone in the hardened image, or differs from the export: {made}")
    ok("the owner makes the shared paper's arXiv package, built alone in the hardened image, as the export")


def review_shared_checks(sh):
    """The copy with notes in the margin in shared mode (POST /review), as for the export: both start only with a POST
    from the viewer's own page (a GET, or a POST with another site's Origin, starts nothing), a viewer may ask,
    several asking at once share one build, made once per build of the document, and coauthors get that PDF; another
    document's copy is refused. It is confined: its probe pass can't read an absolute path (openin_any=p), its last
    pass loads no package that the paper's TeX wrote, and the host's TeX never runs."""
    root = sh["root"]
    wait_for("the shared copy's build to settle", lambda: settled(sh), timeout=120)
    proxy, owner = [("Host", PROXY_HOST), ("X-Test-User", "vw")], [("Cookie", f"latex_live={sh['token']}")]
    for path in ("/review", "/export"):  # (a GET through the proxy with neither header: all another site's page sends over http)
        for headers, what in ((proxy, "through the proxy"), (proxy + [("Sec-Fetch-Site", "same-origin")], "Sec-Fetch-Site: same-origin"),
                              (owner, "with the owner's token")):
            expect(send("GET", path, headers), 405, f"GET {path} {what}")
        for headers, what in ((proxy, "no Origin, through the proxy"),
                              (proxy + [("Origin", "http://evil.example")], "another site's Origin"),
                              (proxy + [("Origin", "null")], "Origin: null"),
                              (proxy + [("Origin", PUBLIC), ("Origin", PUBLIC)], "two Origin headers"),
                              (proxy + [("Origin", f"http://localhost:{SHARED_PORT}")], "a loopback Origin through the proxy"),
                              (owner + [("Origin", "http://evil.example")], "another site's Origin, with the owner's token")):
            expect(send("POST", path, headers, {}), 403, f"POST {path} with {what}")
    time.sleep(1)  # (a review copy that had started would have its folder by now)
    if os.path.exists(os.path.join(root, "build", "review")):
        fail("a GET of the copy with notes in the margin, or a POST from another site's page, started a build")
    for headers, what in ((proxy + [("Origin", PUBLIC)], "the public Origin through the proxy"),
                          (owner + [("Origin", f"http://localhost:{SHARED_PORT}")], "a loopback Origin with the owner's token"),
                          (owner, "the owner's token and no Origin, on a loopback Host (curl)")):
        expect_dict(send("POST", "/export", headers, {}), 200, f"POST /export with {what}")
    ok("the downloads built on request start only with a POST from the viewer's own page: a GET of /review or /export"
       " (even through the proxy with neither Origin nor Sec-Fetch-Site), or a POST from another site's page, starts nothing")

    asked, askers = {}, ("vw", "cm", "ed", "vw")
    threads = [threading.Thread(target=lambda i=i, uid=uid: asked.update({i: send(
        "POST", "/review", [("Host", PROXY_HOST), ("X-Test-User", uid), ("Origin", PUBLIC)], {}, quiet=True)}))
        for i, uid in enumerate(askers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if sorted(asked) != list(range(len(askers))) or None in asked.values():
        fail(f"of {len(askers)} requests for the copy with notes in the margin at once, some got no answer")
    answers = [expect_dict(asked[i], 200, f"POST review for {uid}, with others at once") for i, uid in enumerate(askers)]
    if len({(a.get("source"), a.get("time")) for a in answers}) != 1 or any(a.get("state") not in ("running", "done") for a in answers):
        fail(f"requests for the copy with notes in the margin at once did not share one build: {answers}")

    def reviewed():
        r = expect_dict(via_proxy("vw", "/review", "POST", {}), 200, "POST review for a viewer")
        if r.get("state") == "failed":
            fail(f"the shared copy's copy with notes in the margin failed: {r.get('msg')}")
        return r if r.get("state") == "done" else None

    done = wait_for("the shared copy's copy with notes in the margin", reviewed, timeout=240)
    # (one build for all of them: for a few seconds, nothing builds the copy again, which would empty its folder first
    # and change its state's step, then its version)
    plan, end = os.path.join(root, "build", "review", "main", "main-review.json"), time.monotonic() + 4
    while time.monotonic() < end:
        again = expect_dict(via_proxy("vw", "/review", "POST", {}), 200, "POST review for a viewer, once it is done")
        if (again.get("version"), again.get("step")) != (done.get("version"), done.get("step")) or not os.path.isfile(plan):
            fail(f"the copy with notes in the margin was built again for the same build of the document: {again}")
        time.sleep(0.2)
    pdf = via_proxy("vw", "/pdf/main-review.pdf")
    if pdf[0] != 200 or header(pdf, "Content-Type") != "application/pdf" or not pdf[2].startswith(b"%PDF"):
        fail(f"/pdf/main-review.pdf for a viewer: {pdf[0]} {header(pdf, 'Content-Type')}")
    if pdf[2] != read_bytes(os.path.join(root, "build", "main-review.pdf")) or not done.get("notes"):
        fail(f"the viewer's copy with notes in the margin is not the one built, or has no notes: {done}")
    expect(via_proxy("vw", "/pdf/appendix-review.pdf"), 403, "GET /pdf/appendix-review.pdf for a viewer (another document's)")
    annotated = via_proxy("vw", "/pdf/main-annotated.pdf")
    if annotated[0] != 200 or annotated[2] != read_bytes(os.path.join(root, "build", "main-annotated.pdf")):
        fail(f"/pdf/main-annotated.pdf (the notes as comments) for a viewer: {annotated[0]}")
    expect(via_proxy("vw", "/pdf/appendix-annotated.pdf"), 403, "GET /pdf/appendix-annotated.pdf for a viewer (another document's)")
    if editor_calls(sh["host_tex_log"]):
        fail(f"shared mode ran the host's TeX: {editor_calls(sh['host_tex_log'])}")
    try:
        with open(os.path.join(root, "build", "review", "main", "main-export.log"), errors="replace") as f:
            probed = f.read()
    except FileNotFoundError:  # (the probe pass did not run)
        probed = ""
    if "latex-live-check: TeX refused an absolute path" not in probed or "latex-live|pages|" not in probed:
        fail("the review copy's probe pass " + ("read /etc/passwd: its TeX runs without openin_any=p, not in the hardened image"
                                                if "TeX read an absolute path" in probed
                                                else "did not get through people.tex and the notes"))
    composed(os.path.join(root, "build"))
    if expect_dict(via_proxy("cm", "/review", "POST", {}), 200, "POST review for a commenter").get("version") != done["version"]:
        fail("asking again rebuilt the shared copy's copy with notes in the margin, though the document had not changed")
    ok(f"a viewer may download the shared paper with its {done['notes']} notes in the margin: one build for several asking at"
       " once, once per build of the document, confined (the probe can't read absolute paths; never the host's TeX; no"
       " package of the paper's in its last pass); another document's copy is refused")
