#!/usr/bin/env python3
"""Smoke test for latex-live: python3 check.py (a minute or two; needs TeX, as live.py does).

1. The Python compiles without warnings (and with Python 3.9, if there is one); the viewer's import
   map is valid JSON, comes before its module code, and maps every bare module name that the viewer
   (viewer/) and the vendored editor (codemirror/) import to a file here, and every module that the
   viewer imports by path is a file here; the viewer's modules parse (with Node, if there is one);
   instances starting at once agree on one token, never an empty one.
   The review copy's layout keeps notes in order, apart, and on their pages, and continues the ones
   that don't fit after the paper; its matching tells apart two authors' notes with the same text.
   In shared mode, the downloads built on request say plainly that the TeX Live image is missing, the
   review copy reads its notes from the files of the last good build (only the paper's own), and
   the inputs kept for its probe leave out symbolic links; shared mode's builder prints the page
   layout as latexmkrc does, runs biber again when a data source changes, and reads a chapter's .aux
   for bibtex. LATEX_LIVE_TIMING=1's lines leave out the events of a notes-free pass that a newer run superseded.
2. A scratch paper in a temporary directory (two columns, lineno line numbers, inline notes, a git
   repository with a pushed commit and a local one) gets a live.py instance, with the hub on port
   44180, or below the ports that outgoing connections take where 44180 is among them (Linux: 28180), or the first
   free block of 24 ports above (LATEX_LIVE_CHECK_PORT sets it), state of its own
   (not the user's) and none of the user's LATEX_LIVE_* settings:
   - the build publishes main.pdf, and the notes-free pass main-clean.pdf;
   - the hub redirects an old bookmark, needs the token or its cookie, and forwards every
     endpoint (POST /rebuild too); the hub and the previews refuse HEAD and bad or oversized POST
     bodies; SyncTeX finds a source line; /instances lists the instance;
   - /me reports the owner with every capability; the viewer's and the editor's modules and the
     viewer's style are served (as JavaScript, and CSS), the viewer's revalidated; SyncTeX answers
     as JSON without opening the editor, while GET /open, POST /open and plain /synctex still open
     it (a stand-in that logs its arguments); the hub takes a source file's save above the other
     actions' size limit, refuses bodies above its limits, and survives a non-ASCII token;
     /favicon.ico is answered, not refused;
   - the HEAD and last-push baselines build (they differ: one commit is unpushed), and the
     notes-free baseline's line numbers match the live notes-free build's (two passes with lineno);
   - the download without notes is an exact build (its line numbers match the notes-free view's),
     made once per build of the document, and the review copy places its pages on wider ones with
     every note in a gutter (none overlapping, the protected marker and the resolved note left out),
     and its last pass loads no package that the paper's TeX wrote; a document without notes gets a
     clear failure, not a file;
   - POST /switch moves the preview to another document and GET /switch back, and POST /diff and
     GET /diff build a latexdiff (the viewer's POST forms and the old GET ones, through the hub);
   - a second document runs in its own process behind the same port, and /papers links both; the
     hub, refused by a preview that a stale entry says is another paper's, looks again and doesn't
     misroute; after a switch, the old document's viewers still reach their preview;
   - a second instance for the same document says it is already running; --replace takes over,
     and so does any new instance when the running one no longer answers; the hub moves to
     another instance on the same port, and a hub that no longer answers is replaced;
   - stopping the instance removes it from /instances, and no instance's warm standby leaves a keeper, a
     container or a folder behind.
   Beside these, in a thread of its own, the warm standby (standby_run) on a paper and an instance of its own,
   whose preamble reads a file and typesets a box: once a body edit runs from the standby, its PDF has a normal
   pass's pages and text; both passes of an edit that adds a reference run from it (and with LATEX_LIVE_TIMING=1,
   its timing line names each phase in order, and /status gives the server's clock); a preamble edit runs normally
   (verifying a new standby beside it) and the next edits use the standby, and so does a change to the file the
   preamble reads; an error in the body is reported at its file and line; forward and inverse search work after a
   pass from the standby (also on the box); killing live.py (kill -9) stops its standbys, and so does stopping it,
   leaving no keeper, container or folder; LATEX_LIVE_STANDBY=0 builds without standbys.
   And in another thread, the early notes-free pass (early_run), on a paper with notes and line numbers and an
   instance of its own without the standby: a body edit's early pass is kept, with the PDF (but dates) and SyncTeX
   of the notes-free pass after the run; a two-pass edit keeps its second pass's; a failed run puts build/clean/ back.
   And in a third, the notes-free pass's warm standby (clean_standby_run), on a paper with notes and an instance of
   its own: a body edit's notes-free pass runs from it, with a normal notes-free pass's PDF (but dates), pages, text
   and SyncTeX, and forward and inverse search of a body line in the notes-free PDF work; a preamble edit that
   adds line numbers runs normally, and then both runs of a notes-free pass (lineno) come from standbys and match a
   normal pass; an early pass stopped while it runs from a standby (its run failed) leaves no keeper, container or
   folder of that standby and build/clean/ as it was; LATEX_LIVE_STANDBY=0 builds the notes-free PDF without one.
3. A shared-mode instance (LATEX_LIVE_USERS) on the hub's port + 11 serves a clone of the paper on branch
   "shared" to users of every role, behind a stand-in for an authenticating proxy (an identity
   header), with personal-link tokens, auto-commit, protected text and the editor stand-in. Shared
   mode builds only in the TeX Live image: where podman or the image is missing, all of this part
   but its first two checks (which need neither) is skipped, with a "skip:" line.
   - tex-exec and --host-tex refuse the host's TeX, and tex-exec's container options are hardened
     (the paper read-only, limits, labels, 10 minutes at most); an instance refuses the default
     state folder, and on Linux the identity header without LATEX_LIVE_PROXY_UID; it builds in the
     hardened image without latexmk (a \\bibdata name that is a shell command, which latexmk would
     run on the host, runs nothing; the paper's latexmkrc doesn't run; TeX can't read absolute
     paths), serves exactly its port and only loopback peers, prints its public URL and no token
     anywhere (nor does a second instance that finds it running), and stays out of the hub; edits
     it finds uncommitted at the start are committed as the owner's;
   - Host names, the identity header (none, two, empty, unknown, on another Host), tokens and
     cookies, a page that says what to do for a browser without a working link, users added (and a
     broken users file) without a restart, /me (whether the paper defines the user's note macro, and
     no document switch, for anyone) and every endpoint per role (the checks for editors only),
     the Origin rules for POSTs, POST body limits, non-owners pinned to the document, and the
     viewer's security headers;
   - the download without notes: a viewer may ask; several asking at once share one build, made once
     per build of the document, in the hardened image;
   - the copy with notes in the margin (/review), likewise: a viewer may ask, several asking at once
     share one build, made once per build of the document; another document's copy is refused; its
     probe pass can't read absolute paths either; both downloads start only with a POST from the
     viewer's own page (a GET, or another site's Origin, starts nothing); and the host's TeX never
     runs (a stand-in for it on PATH);
   - notes as oneself only: the editor's own macro yes, the owner's no, others' notes no,
     resolving anyone's yes, protected text kept, a macro the paper lacks explained; notes, the
     list of notes and the note macros keep working while the paper rebuilds;
   - the editor's files API: what it lists and refuses (../, absolute paths, %2e%2e, .git, build/,
     a symlink to the token, a new file, too big, not UTF-8), reading, saving, a stale save that
     merges, a conflict that writes nothing (and says who saved meanwhile), a stale save without and
     then with its base text, a save whose answer got lost followed by more typing on that line
     (from the same tab; another tab's conflicts), CRLF files; status ?open=; SyncTeX as JSON that
     never opens the editor (only the owner's POST open does), with a line past a file's end moved
     to its last line; diffs only against commits that HEAD builds on; editors' rebuilds at most
     every 20 s;
   - a rebuild stops the run in progress and its container (a pass kept busy for a minute); removing
     a file the build reads rebuilds it; a bibtex failure fails the build; a file a failed build missed
     builds it once it appears; a build that fills build/ past LATEX_LIVE_BUILD_LIMIT is stopped and
     build/ emptied; a container sees no dot entry of the paper (.git); the status ignores a status, a .building marker and a record of what was published forged in
     build/; a second instance asks the port in a forged lock file without its token;
   - auto-commit: each user's edits are committed as that user (by the owner) once they are
     quiet, or at the owner's POST commit, only the files the server wrote, on the shared branch
     only, past the paper's hooks and signing settings, and after a git lock goes away; a write to
     a file that holds another user's uncommitted edits waits for their commit (503 while git is
     busy), and every write waits out a merge in the shared copy (503); the HEAD baseline is not
     rebuilt for each commit; the edits build; a GET of the status builds no baseline, a viewer's
     POST baseline does;
   - stopping the instance leaves no container, running or created and never started; an edit still uncommitted when it stops
     (a merge in progress) is committed as its writer's at the next start; restarted with an https public URL, cookies are Secure on its
     Host; the identity header from a peer whose uid is not LATEX_LIVE_PROXY_UID is not trusted
     (Linux).
Exits non-zero on the first failure.
"""

import glob
import gzip
import hashlib
import http.client
import ipaddress
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import zlib

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))  # (checks/ next to this file, wherever it is run from)
from checks.base import (  # noqa: E402
    DECOY_PORT, ENV, HERE, PORT, SECRETS, SHARED_PORT, TOKEN, _quiet, fail, hub, in_thread, ok, prints_token, redact,
    wait_for)
from checks.clean_standby import clean_standby_run  # noqa: E402
from checks.early import early_run  # noqa: E402
from checks.helpers import (  # noqa: E402
    CAPS, JS_TYPE_RE, editor_calls, editor_stub, get, git, git_out, hub_get, make_state, mtime_ns, post_length, put,
    put_bytes, read_bytes, read_hub, read_json_file, read_text, request, start, stop, texlive_image, text_point,
    write_text)
from checks.papers import composed, make_paper  # noqa: E402
from checks.standby import standby_left, standby_run  # noqa: E402
from checks.static import static_checks  # noqa: E402
from checks.units import unit_checks  # noqa: E402


PUBLIC = "http://paper.invalid:8443"  # its LATEX_LIVE_PUBLIC_URL: the origin browsers would use
PROXY_HOST = "proxy.invalid"  # its LATEX_LIVE_PROXY_HOST: the Host of the proxy's requests (via_proxy)

# What each role can do in shared mode (no one switches documents: a shared preview serves one; everyone may download
# the paper without its notes), and commenting only with a note macro the paper defines (check_me).
ROLE_CAN = {"viewer": ("download",), "commenter": ("comment", "download"), "editor": ("comment", "edit", "rebuild", "compare", "download"),
            "owner": ("comment", "edit", "rebuild", "compare", "openHost", "download")}

SOURCES = ("main.tex", "body.tex", "people.tex", "generated.inc")  # what the shared copy's build reads (settled)

# A change to the shared copy that only the test makes, before the shared instance starts: no one's saves
# account for it, so the server commits it then, as the owner's (shared_start_checks). It also tells whether
# TeX can read an absolute path there (shared mode runs it with openin_any=p), and reads slow.tex, disk.tex and
# refer.tex while they exist (rebuild_stop_checks).
LOCAL_CHANGE = r"""\IfFileExists{/etc/passwd}{\typeout{latex-live-check: TeX read an absolute path}}
  {\typeout{latex-live-check: TeX refused an absolute path}}
\IfFileExists{slow.tex}{\input{slow.tex}}{}\IfFileExists{disk.tex}{\input{disk.tex}}{}\IfFileExists{refer.tex}{\input{refer.tex}}{}
"""
# A line for the shared copy's preamble whose \bibdata name is a shell command, which would create bibdata-ran in
# the paper: latexmk hands such names to a shell on the host (its kpsewhich), where it runs. The .bib file of that
# name is in the paper, with a \citation of it and a style, so that bibtex (and the downloads' bibtex) succeeds.
BIBDATA_NAME = "probe$(>bibdata-ran)"  # (no braces or spaces, which bibtex would not take)
BIBDATA_PROBE = ("\\makeatletter\\AtBeginDocument{" + "".join(f"\\immediate\\write\\@auxout{{\\string\\{line}}}" for line in
                 ("citation{probe}", "bibstyle{plain}", "bibdata{" + BIBDATA_NAME + "}"))
                 + "}\\AtEndDocument{\\typeout{No file \\jobname.bbl.}}\\makeatother\n")  # (latexmk then runs bibtex)
# A change that only the test makes once the shared instance runs (to refs.bib, which the build doesn't read):
# the server must not commit it while it runs (commit_checks), only as the owner's at its next start
# (shared_stop_checks).
LATER_CHANGE = "% A change made beside the server, which it did not write.\n"


def ln_records(path):
    with open(path, errors="replace") as f:
        return [line for line in f if "\\@LN" in line]


def live_checks(tmp):
    ENV["LATEX_LIVE_STATE"] = state = os.path.join(tmp, "state")  # (not the user's: their previews keep their list)
    ENV["LATEX_LIVE_EDITOR"], editor_log = editor_stub(tmp, "editor")  # (not the user's editor: owner_checks)
    paper = make_paper(tmp)
    for name in ("appendix", "extra"):
        with open(os.path.join(paper, f"{name}.tex"), "w") as f:
            f.write(f"\\documentclass{{article}}\n\\begin{{document}}\nThe {name}.\n\\end{{document}}\n")
    procs, sharing = [start(paper)], []  # (sharing: the shared-mode instance, shared_setup)
    standby = in_thread("warm standby checks", standby_run, tmp)  # (beside the rest, with an instance of its own)
    early = in_thread("early notes-free pass checks", early_run, tmp)  # (likewise)
    clean_standby = in_thread("notes-free standby checks", clean_standby_run, tmp)  # (likewise)
    try:
        sh = shared_setup(tmp, sharing)  # (it builds while the hub's checks run; None without podman or the image)
        build = os.path.join(paper, "build")
        wait_for("main.pdf", lambda: os.path.isfile(os.path.join(paper, "main.pdf")))
        ok("the build published main.pdf")
        clean_status = os.path.join(build, "clean", "main-clean.status.json")
        wait_for("the notes-free pass", lambda: os.path.isfile(clean_status))
        with open(clean_status) as f:
            if not json.load(f).get("ok"):
                fail(f"the notes-free pass failed: see {build}/clean/main-clean.log")
        if not os.path.isfile(os.path.join(build, "main-clean.pdf")):
            fail("no build/main-clean.pdf")
        ok("the notes-free pass published build/main-clean.pdf")

        with open(os.path.join(state, "token")) as f:
            token = TOKEN[0] = f.read().strip()
        wait_for("the hub", read_hub, timeout=20)
        if hub["port"] != PORT or hub["pid"] != procs[0].pid:
            fail(f"the hub is not on port {PORT} in the first instance: {hub}")
        with open(os.path.join(build, "main-live.lock")) as f:
            own = json.load(f)["port"]
        status, headers, _ = request("/viewer.html?file=main.pdf", token)  # (an old bookmark)
        if status != 302 or not re.fullmatch(r"/p/paper-[0-9a-f]{6}/main/viewer\.html\?file=main\.pdf", headers.get("Location", "")):
            fail(f"/viewer.html?file=main.pdf on the hub: {status} {headers.get('Location')}")
        hub["base"] = headers["Location"][: -len("main/viewer.html?file=main.pdf")]
        ok(f"the hub serves port {PORT} ({hub['base']}main/; the preview's own port is {own})")
        if request(f"{hub['base']}main/status/main.pdf")[0] != 403:
            fail("the hub answers without the token")
        if request(f"{hub['base']}main/status/main.pdf", cookie=token)[0] != 200:
            fail("the hub refuses its cookie")
        ok("the hub needs the token (or its cookie)")
        shared = shared_checks(sh, sharing, own, token) if sh else None  # (beside the rest, which doesn't touch its instance)
        wrong = http.client.HTTPConnection("127.0.0.1", own, timeout=10)  # (as from a hub that looked up a stale port)
        wrong.request("GET", "/docs", headers={"Cookie": f"latex_live={token}", "X-Latex-Live-Paper": "other-000000"})
        if wrong.getresponse().status != 421:
            fail("a preview answered a request the hub meant for another paper")
        wrong.close()
        ok("a preview refuses a request meant for another paper (421), so the hub looks again")
        if request(f"{hub['base']}main/status/main.pdf", method="HEAD")[0] != 405 or request("/VERSION", method="HEAD")[0] != 405:
            fail("HEAD is answered (it would skip the token)")
        big = http.client.HTTPConnection("127.0.0.1", hub["port"], timeout=10)
        big.putrequest("POST", f"{hub['base']}main/note")
        for key, value in (("Cookie", f"latex_live={token}"), ("Content-Type", "application/json"), ("Content-Length", "1000000000")):
            big.putheader(key, value)
        big.endheaders()
        if big.getresponse().status != 413:
            fail("the hub accepts a huge POST body")
        big.close()
        direct = http.client.HTTPConnection("127.0.0.1", own, timeout=10)
        direct.request("HEAD", "/status/main.pdf", headers={"Cookie": f"latex_live={token}"})
        if direct.getresponse().status != 405:
            fail("a preview answers HEAD on its own port")
        direct.close()
        for length in ("-1", "abc", "1000000000"):
            direct = http.client.HTTPConnection("127.0.0.1", own, timeout=10)
            direct.putrequest("POST", "/note")
            for key, value in (("Cookie", f"latex_live={token}"), ("Content-Type", "application/json"), ("Content-Length", length)):
                direct.putheader(key, value)
            direct.endheaders()
            if direct.getresponse().status not in (400, 413):
                fail(f"a preview accepts Content-Length: {length} on its own port")
            direct.close()
        ok("the hub and the previews refuse HEAD and bad or oversized POST bodies")

        for path in ("/status/main.pdf?base=push", "/outline/main.pdf", "/notes/main.pdf", "/authors/main.pdf",
                     "/check/main.pdf", "/graphics/main.pdf", "/margin/main.pdf", "/floats/main.pdf", "/docs",
                     "/commits", "/papers", "/viewer.html", "/viewer/main.js", "/viewer/viewer.css", "/pdfjs/build/pdf.min.mjs",
                     "/pdf/main.pdf"):
            try:
                get(path, token)
            except Exception as e:
                fail(f"{path} (through the hub): {e}")
        ok("the viewer's endpoints answer through the hub")
        with open(os.path.join(HERE, "VERSION")) as f:
            version = f.read().strip()
        reported = get("/status/main.pdf", token).get("latexLive")
        if reported != {"version": version, "updated": False}:
            fail(f"/status reports latexLive {reported}, not version {version}, not updated")
        ok(f"/status reports version {version}")
        notes = get("/notes/main.pdf", token)
        if not any("hiding it moves" in n.get("text", "") for n in notes):
            fail(f"/notes does not list the note: {str(notes)[:300]}")
        target = get("/forward?file=main.tex&line=9", token)
        if not target.get("page"):
            fail(f"SyncTeX found no target for main.tex:9: {target}")
        ok(f"SyncTeX: main.tex:9 is on page {target['page']}")
        listed = get("/instances", token)
        if not any(i["pid"] == procs[0].pid and hub["base"] == f"/p/{i['id']}/" for i in listed["instances"]):
            fail(f"/instances does not list the instance with its paper id: {listed}")
        ok("/instances lists the instance")
        owner_checks(token, own, editor_log)

        def baselines():
            info = get("/status/main.pdf?base=push", token)
            head, push = info.get("head") or {}, info.get("push") or {}
            failed = [b for b in (head, push) if b.get("state") == "failed"]
            if failed:
                fail(f"a baseline build failed: {failed}")
            return info if head.get("clean") and push.get("clean") else None

        info = wait_for("the HEAD and last-push baselines", baselines)
        if info["head"]["sha"] == info["push"]["sha"]:
            fail(f"the last-push baseline is HEAD, but a commit is unpushed: {info}")
        ok(f"baselines: HEAD {info['head']['sha']}, last push {info['push']['sha']} ({info['push']['ref']})")
        clean = ln_records(os.path.join(build, "clean", "main-clean.aux"))
        head_clean = ln_records(os.path.join(build, "head", "main", "main-head-clean.aux"))
        if not clean:
            fail("the notes-free .aux has no lineno records")
        if clean != head_clean:
            fail("the notes-free HEAD baseline's line numbers differ from the live notes-free build's")
        ok("the notes-free baseline's line numbers match the live notes-free build's")
        before = get("/status/main.pdf", token)
        status, _, data = request(f"{hub['base']}main/rebuild", method="POST", body={}, cookie=token)
        if status != 202:
            fail(f"POST /rebuild through the hub: {status} {data[:200]!r}")

        def rebuilt():  # (and its notes-free pass: the download below is of one build, which nothing replaces meanwhile)
            info = get("/status/main.pdf", token)
            return info["version"] != before["version"] and not info["building"] and info["clean"]["version"] != before["clean"]["version"]

        wait_for("the rebuild that POST /rebuild started", lambda: _quiet(rebuilt))
        ok("POST /rebuild works through the hub")
        exported = wait_for("the PDF without notes", lambda: (lambda e: e if e.get("state") in ("done", "failed") else None)(
            get("/export", token)), timeout=240)
        if exported["state"] != "done":
            fail(f"the build without notes failed: {exported.get('msg')}")
        pdf = get("/pdf/main-export.pdf", token)
        log = open(os.path.join(build, "export", "main", "main-export.log"), errors="replace").read()
        if not pdf.startswith(b"%PDF") or re.search(r"Rerun to get|may have changed\. Rerun", log):
            fail("the PDF without notes is missing, or its references had not settled")
        if get("/export", token).get("version") != exported["version"]:
            fail("asking again rebuilt the PDF without notes, though the document had not changed")
        status, _, data = request(f"{hub['base']}main/export", method="POST", body={}, cookie=token)  # (as the viewer asks)
        if status != 200 or json.loads(data).get("version") != exported["version"]:
            fail(f"POST /export through the hub: {status} {data[:200]!r}, not the same download")
        if ln_records(os.path.join(build, "export", "main", "main-export.aux")) != ln_records(os.path.join(build, "clean", "main-clean.aux")):
            fail("the download without notes has its line numbers in other places than the notes-free view")
        ok("the download without notes is an exact build (line numbers too), made once per build; the viewer's POST asks"
           " for the same one")
        review_checks(build, token)
        switch_checks(token)

        procs.append(start(paper, "appendix"))  # (a second document of the same paper, in its own process)
        # (switch_checks built appendix.pdf already, so wait for the hub to find this process, not just for the PDF)
        wait_for("the appendix's own process, through the hub",
                 lambda: _quiet(lambda: get("/instances", token, "appendix")["self"] == procs[1].pid))
        wait_for("appendix.pdf through the hub", lambda: _quiet(lambda: get("/status/appendix.pdf", token, "appendix")["version"]))
        routed = wait_for("the appendix route", lambda: _quiet(lambda: (get("/instances", token, "appendix")["self"],
                                                                        get("/docs", token, "appendix")["doc"])), timeout=20)
        if routed != (procs[1].pid, "appendix"):
            fail(f"/p/.../appendix/ reaches process {routed[0]} building {routed[1]}, not the appendix's {procs[1].pid}")
        page = request("/papers", token)[2].decode()
        if f"{hub['base']}main/viewer.html" not in page or f"{hub['base']}appendix/viewer.html" not in page:
            fail(f"/papers does not link both documents through the hub:\n{page[:600]}")
        ok("a second document gets its own process, behind the same port")
        failed = wait_for("the appendix's review copy", lambda: _quiet(lambda: (lambda e: e if e.get("state") in ("done", "failed")
                                                                          else None)(get("/review", token, "appendix"))), timeout=120)
        if failed.get("state") != "failed" or "no notes" not in failed.get("msg", "") or \
                os.path.exists(os.path.join(build, "appendix-review.pdf")):
            fail(f"a review copy of a document without notes: {failed}")
        ok("a document without notes gets no review copy, and says why")
        ghost = os.path.join(state, "instances", "ghost.json")  # (a stale entry: another paper's id, this preview's port)
        with open(ghost, "w") as f:
            json.dump({"port": own, "pid": procs[1].pid, "root": "/elsewhere", "doc": "main", "id": "ghost-000000"}, f)
        status, _, data = wait_for("the hub to see the stale entry", lambda: (lambda r: r if r[0] != 404 else None)(
            request("/p/ghost-000000/main/docs", cookie=token)), timeout=10)  # (it rescans at most every 0.5 s)
        os.remove(ghost)
        if status != 502:
            fail(f"the hub forwarded a request for another paper to this preview's port: {status} {data[:200]!r}")
        ok("the hub, refused by a preview that is another paper's, looks again and doesn't misroute")
        if get("/switch?doc=extra", token).get("doc") != "extra":
            fail("switching main's preview to extra failed")
        if wait_for("main's old route", lambda: _quiet(lambda: get("/docs", token)["doc"]), timeout=20) != "extra":
            fail("after a switch, the old document's viewers no longer reach their preview")
        if get("/switch?doc=main", token, "extra").get("doc") != "main":
            fail("switching back to main failed")
        wait_for("main again", lambda: _quiet(lambda: get("/docs", token)["doc"] == "main"), timeout=20)
        ok("after a switch (two documents running), the old document's viewers still reach their preview")

        second = subprocess.run([sys.executable, os.path.join(HERE, "live.py"), "main.tex", "--no-open", "--port", str(PORT)],
                                cwd=paper, capture_output=True, text=True, timeout=60, env=ENV)
        if "already running" not in second.stdout or f":{PORT}{hub['base']}main/" not in second.stdout or second.returncode:
            fail(f"a second instance did not report the first, with its hub URL:\n{second.stdout}{second.stderr}")
        ok("a second instance reports the running one (its hub URL) and exits")
        procs.append(start(paper, "main", "--replace"))
        try:
            procs[0].wait(30)
        except subprocess.TimeoutExpired:
            fail("--replace did not stop the first instance")
        wait_for("the hub to move to another instance", lambda: read_hub() and hub["pid"] != procs[0].pid, timeout=20)
        if hub["port"] != PORT:
            fail(f"the hub moved to port {hub['port']}")
        wait_for("the replacement, through the hub", lambda: _quiet(lambda: get("/instances", token)["self"] == procs[2].pid))
        listed = get("/instances", token)["instances"]
        if sorted(i["pid"] for i in listed if i["root"] == os.path.realpath(paper)) != sorted([procs[1].pid, procs[2].pid]):
            fail(f"/instances after --replace: {listed}")
        ok(f"--replace takes over; the hub stays on port {PORT} (now in process {hub['pid']})")

        main_proc = procs[2]
        os.kill(main_proc.pid, signal.SIGSTOP)  # (holds the lock, answers nothing)
        third = subprocess.Popen([sys.executable, os.path.join(HERE, "live.py"), "main.tex", "--no-open", "--port", str(PORT)],
                                 cwd=paper, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True,
                                 env=ENV)
        procs.append(third)
        try:
            first_line = third.stdout.readline()
            threading.Thread(target=third.stdout.read, daemon=True).start()  # (keep reading: a full pipe would stall it)
            main_proc.wait(30)
        except subprocess.TimeoutExpired:
            fail("an instance that does not answer was not replaced")
        if "not answering" not in first_line:
            fail(f"replacing an instance that does not answer: {first_line}")
        wait_for("the instance that replaced the one not answering", lambda: _quiet(lambda: get("/instances", token)["self"] == third.pid))
        ok("an instance that does not answer is replaced")

        wait_for("the hub, after that", lambda: read_hub() and hub["pid"] != main_proc.pid, timeout=20)
        stuck = hub["pid"]
        os.kill(stuck, signal.SIGSTOP)  # (serves the hub, answers nothing)
        procs.append(start(paper, "extra"))  # (another document: only the hub's check can stop the stuck one)
        holder = next(p for p in procs if p.pid == stuck)
        try:
            holder.wait(60)  # (three tries over about 10 s first)
        except subprocess.TimeoutExpired:
            fail("a new instance did not replace the one serving the hub without answering")
        with open(procs[-1].log) as f:
            if "serves the hub but is not answering" not in f.read():
                fail("the hub that did not answer was stopped some other way")
        wait_for("the hub to come back", lambda: read_hub() and hub["pid"] != stuck, timeout=20)
        if hub["port"] != PORT:
            fail(f"the hub came back on port {hub['port']}")
        ok("a hub that does not answer is replaced")
        # (another instance may have taken the hub while the new one still starts: an instance stopped before it has
        # started latexmk exits without removing itself from the list, so let it build first)
        wait_for("extra.pdf through the hub", lambda: _quiet(lambda: get("/status/extra.pdf", token, "extra")["version"]))
        if shared:
            shared.join()
            if shared.failed:
                sys.exit(1)
        for thread in (standby, early, clean_standby):
            thread.join()
            if thread.failed:
                sys.exit(1)
    finally:
        for proc in procs + sharing:
            stop(proc)
    left = os.listdir(os.path.join(state, "instances"))
    if left:
        fail(f"stopped instances are still listed: {left}")
    ok("stopped instances are no longer listed")
    left = [what for doc in ("main", "appendix", "extra") for job in ("live", "clean") for what in standby_left(paper, f"{doc}-{job}")]
    if left:
        fail(f"the stopped instances' warm standbys left keepers, containers or folders behind: {left}")
    ok("the stopped instances' warm standbys are all gone")


def written_pages(log):
    """The page count of a pdflatex run, from its log."""
    with open(log, errors="replace") as f:
        m = re.search(r"Output written on .*?\((\d+) pages?", f.read().replace("\n", ""))
    return int(m.group(1)) if m else None


def drawn_pages(pdf, left):
    """How many times a PDF's content streams draw an image or form unscaled with its bottom left at
    (left, 0): in the review copy of a two-column paper (a gutter on the left, so the paper's own
    figures don't count), once per page of the paper."""
    found = 0
    for m in re.finditer(rb"stream\r?\n", pdf):
        try:
            content = zlib.decompress(pdf[m.end():pdf.find(b"endstream", m.end())])
        except zlib.error:
            continue
        found += len(re.findall(rb"\b1 0 0 1 %s 0 cm\s*/\w+ Do" % f"{left:g}".encode(), content))
    return found


def review_checks(build, token):
    """The review copy of the scratch paper: the export's pages on wider ones, every note beside its
    column, numbered and apart, without the protected marker or the resolved note. (Two of its notes
    are there for shared mode's checks, at the start of body.tex: NOTE_PARAGRAPHS; outside shared mode,
    "as written" is a note like any other.)"""
    reviewed = wait_for("the review copy", lambda: (lambda e: e if e.get("state") in ("done", "failed") else None)(
        get("/review", token)), timeout=240)
    if reviewed["state"] != "done":
        fail(f"the review copy failed: {reviewed.get('msg')}")
    review = get("/pdf/main-review.pdf", token)
    if not review.startswith(b"%PDF"):
        fail("no review copy to download")
    with open(os.path.join(build, "review", "main", "main-review.json")) as f:
        plan = json.load(f)
    pages = written_pages(os.path.join(build, "export", "main", "main-export.log"))
    if not pages or plan["paper"] != pages or plan["pages"] != pages or \
            written_pages(os.path.join(build, "review", "main", ".copy", "main-review.log")) != pages:
        fail(f"the review copy has {plan['pages']} pages ({plan['paper']} of the paper), the export {pages}")
    if plan["width"] <= plan["W"]:
        fail(f"the review copy's pages are no wider than the paper's ({plan['width']} <= {plan['W']})")
    if drawn_pages(review, plan["left"]) != pages:
        fail(f"the review copy should place each page of the paper unscaled, {plan['left']} points from its left edge:"
             f" {drawn_pages(review, plan['left'])} of {pages} are")
    texts = [n["text"] for n in plan["notes"]]
    want = ["hiding it moves", "An owner note to resolve", "as written", "a note in a paragraph", "a note in a footnote"]
    if len(texts) != len(want) or any(sum(w in t for t in texts) != 1 for w in want):
        fail(f"the review copy's notes are not the five expected (no protected marker, resolved note, or note in a branch"
             f" that is off): {texts}")
    sides = {n["side"] for n in plan["notes"]}
    if sides != {"left", "right"}:
        fail(f"notes of both columns should go to both gutters: {sides}")
    exact = [n["text"][:20] for n in plan["notes"] if n["how"] != "exact" and "hiding it moves" not in n["text"]]
    if exact:
        fail(f"notes in a paragraph or a footnote placed only by source line: {exact}")
    placed = [n for n in plan["notes"] if n["top"] is not None]
    for a in placed:
        for b in placed:
            if a is not b and (a["page"], a["side"]) == (b["page"], b["side"]) and a["top"] <= b["top"] < a["top"] + a["sizes"][a["size"]]["h"]:
                fail(f"notes {a['n']} and {b['n']} overlap")
    composed(build)
    if reviewed.get("version") != get("/review", token).get("version"):
        fail("asking again rebuilt the review copy, though the document had not changed")
    status, _, data = request(f"{hub['base']}main/review", method="POST", body={}, cookie=token)  # (as the viewer asks)
    if status != 200 or json.loads(data).get("version") != reviewed["version"]:
        fail(f"POST /review through the hub: {status} {data[:200]!r}, not the same review copy")
    ok(f"the review copy: {pages} pages as exported, unscaled on wider ones, with {len(texts)} notes in both gutters, apart"
       " (the protected marker, the resolved note, and the one in a branch that is off left out); its last pass loads no"
       " package that the paper's TeX wrote")


def owner_checks(token, own, editor_log):
    """The owner's own preview (not shared): GET me, the editor's modules, SyncTeX as JSON, the editor
    stand-in through GET /open (kept outside shared mode), POST /open and plain /synctex, and the POST
    body limits of the hub and of the preview's own port (own; a source file's save may be bigger than
    other actions)."""
    me = hub_get("/me", token)
    can = me.get("can") if isinstance(me, dict) and isinstance(me.get("can"), dict) else {}
    if not isinstance(me, dict) or me.get("shared") is not False or me.get("role") != "owner" or not all(can.get(c) is True for c in CAPS):
        fail(f"GET me outside shared mode is not the owner with every capability: {me}")
    status, headers, data = request(f"{hub['base']}main/codemirror/view.js", token)
    kind = next((value for key, value in headers.items() if key.lower() == "content-type"), "")  # (static files send Content-type)
    with open(os.path.join(HERE, "codemirror", "view.js"), "rb") as f:
        if status != 200 or data != f.read() or not JS_TYPE_RE.match(kind):
            fail(f"/codemirror/view.js through the hub: {status} {kind}")
    for name, kind_re in (("viewer/main.js", JS_TYPE_RE), ("viewer/viewer.css", re.compile(r"text/css\b"))):
        status, headers, data = request(f"{hub['base']}main/{name}", token)
        kind, cache = (next((value for key, value in headers.items() if key.lower() == h), "") for h in ("content-type", "cache-control"))
        if status != 200 or data != read_bytes(os.path.join(HERE, name)) or not kind_re.match(kind) or "no-cache" not in cache:
            fail(f"/{name} through the hub: {status} {kind} ({cache or 'no Cache-Control'})")
    if (status := request(f"{hub['base']}main/favicon.ico", token)[0]) not in (200, 204):  # (browsers ask on every page)
        fail(f"/favicon.ico through the hub: {status}, not answered")
    ok("GET me reports the owner with every capability; the viewer's and the editor's modules are served as JavaScript, the"
       " viewer's style as CSS, the viewer's own revalidated on each load; /favicon.ico is answered")

    page, x, y = text_point(lambda path: hub_get(path, token), "main.tex", 9)
    if editor_calls(editor_log):
        fail(f"synctex with json=1 opened the editor: {editor_calls(editor_log)}")
    for path, method, body in ((f"{hub['base']}main/open?file=main.tex&line=9", "GET", None),
                               (f"{hub['base']}main/open", "POST", {"file": "main.tex", "line": 10}),
                               (f"{hub['base']}main/synctex?file=main.pdf&page={page}&x={x:.2f}&y={y:.2f}", "GET", None)):
        status, _, data = request(path, method=method, body=body, cookie=token)
        if status != 200:
            fail(f"{method} {path} (not shared): {status} {data[:200]!r}")
    calls = wait_for("the editor stand-in, three times", lambda: len(editor_calls(editor_log)) >= 3 and editor_calls(editor_log),
                     timeout=10, step=0.2)
    if (len(calls) != 3 or not all(c.startswith("-g ") and re.search(r"/main\.tex:\d+$", c) for c in calls)
            or not any(c.endswith("main.tex:9") for c in calls) or not any(c.endswith("main.tex:10") for c in calls)):
        fail(f"the editor stand-in got {calls}, not -g .../main.tex:9, :10 and the double-clicked line")
    ok("SyncTeX answers JSON without opening the editor; GET /open, POST /open and plain /synctex open it")

    status, _, data = request(f"{hub['base']}main/file", method="POST", cookie=token,
                              body={"path": "body.tex", "base": "0" * 20, "text": "x" * (1 << 20)})
    if status != 409 or not _quiet(lambda: json.loads(data)["stale"]):
        fail(f"a 1 MiB save through the hub: {status} {data[:200]!r} (want 409 stale: within the 8 MB limit for files)")
    for path, length in (("file", 9 << 20), ("note", 100_001)):
        if (status := post_length(hub["port"], f"{hub['base']}main/{path}", length, [("Cookie", f"latex_live={token}")])) != 413:
            fail(f"a {length}-byte POST /{path} through the hub: {status}, not 413")
    for length, wanted in ((100_001, (413,)), ("-1", (400, 411, 413)), ("many", (400, 411))):
        if (status := post_length(own, "/note", length, [("Cookie", f"latex_live={token}")])) not in wanted:
            fail(f"a POST /note announcing {length!r} bytes on the preview's own port: {status or 'no answer within 10 s'}")
    for path, headers in (("status/main.pdf?t=%C3%A9", {}), ("status/main.pdf", {"Cookie": "latex_live=\u00e9"})):
        try:
            status = request(f"{hub['base']}main/{path}", headers=headers)[0]
        except (OSError, http.client.HTTPException) as e:
            fail(f"a non-ASCII token makes the hub drop the connection ({type(e).__name__}: {e})")
        if status != 403:
            fail(f"a non-ASCII token through the hub: {status}, not 403")
    ok("the hub takes a 1 MiB save of a source file, refuses bigger bodies (as the preview does a bad Content-Length),"
       " and refuses a non-ASCII token")


def switch_checks(token):
    """Outside shared mode, through the hub: POST /switch (as the viewer sends it) builds another document and
    GET /switch (as older viewers send it) goes back; POST /diff and GET /diff each build a latexdiff against
    HEAD."""
    status, _, data = request(f"{hub['base']}main/switch", method="POST", body={"doc": "appendix"}, cookie=token)
    if status != 200 or _quiet(lambda: json.loads(data)["doc"]) != "appendix":
        fail(f"POST /switch {{doc: appendix}} through the hub: {status} {data[:200]!r}")
    wait_for("appendix.pdf, after POST /switch", lambda: _quiet(lambda: get("/status/appendix.pdf", token, "appendix")["version"]))
    status, _, data = request(f"{hub['base']}appendix/switch?doc=main", token)
    if status != 200 or _quiet(lambda: json.loads(data)["doc"]) != "main" or hub_get("/docs", token)["doc"] != "main":
        fail(f"GET /switch?doc=main through the hub: {status} {data[:200]!r}")
    ok("POST /switch builds another document, and GET /switch goes back (outside shared mode, through the hub)")
    if not texlive_image()[1] and not shutil.which("latexdiff"):
        return print("skip: no TeX Live image and no latexdiff here, so POST /diff and GET /diff are not checked", flush=True)

    def diffed():
        diff = hub_get("/status/main-diff.pdf", token).get("diff") or {}
        if diff.get("state") == "failed":
            fail(f"the latexdiff against HEAD failed: {diff.get('msg')}")
        return diff.get("state") == "done"

    for method, path, body in (("POST", "diff", {"rev": "HEAD"}), ("GET", "diff?rev=HEAD", None)):
        status, _, data = request(f"{hub['base']}main/{path}", method=method, body=body, cookie=token)
        if status != 200 or _quiet(lambda: json.loads(data)["state"]) != "running":
            fail(f"{method} /{path} through the hub: {status} {data[:200]!r}")
        wait_for(f"the latexdiff that {method} /{path} started", diffed, timeout=120, step=0.2)
    status, headers, _ = request(f"{hub['base']}main/pdf/main-diff.pdf", token)
    if status != 200 or headers.get("Content-Type") != "application/pdf":
        fail(f"/pdf/main-diff.pdf through the hub: {status} {headers.get('Content-Type')}")
    ok("POST /diff and GET /diff build a latexdiff against HEAD (outside shared mode, through the hub)")


def host_tex_stub(tmp):
    """Stand-ins for the host's pdflatex and bibtex that log their arguments and fail, for the front of the
    shared instance's PATH: shared mode must never run them (all its TeX runs go through tex-exec, into the
    image). (Their folder, the log, which editor_calls reads.)"""
    folder, log = os.path.join(tmp, "host-tex"), os.path.join(tmp, "host-tex.log")
    os.makedirs(folder)
    for tool in ("pdflatex", "bibtex"):
        with open(os.path.join(folder, tool), "w") as f:
            f.write(f"#!/bin/sh\nprintf '{tool} %s\\n' \"$*\" >> {shlex.quote(log)}\nexit 1\n")
        os.chmod(os.path.join(folder, tool), 0o755)
    return folder, log


# Shared mode: one instance (shared_setup) serves a copy of the paper to the users of a users file, who
# come through a trusted proxy (an identity header) or with a personal link (a token of their own).
def shared_users():
    """The users file's users: the owner, an editor (ed) and a commenter (cm) whose note macros the paper
    defines, a viewer, and an editor with only a personal link (tk), whose macro the paper lacks."""
    return {"owner1": {"name": "Owner Example", "email": "owner@example.org", "macro": "eb", "role": "owner"},
            "ed": {"name": "Eden Doyle", "email": "eden@example.org", "macro": "ed", "role": "editor"},
            "cm": {"name": "Casey Moss", "email": "casey@example.org", "macro": "cm", "role": "commenter"},
            "vw": {"name": "Val Wren", "email": "val@example.org", "macro": "vw", "role": "viewer"},
            "tk": {"name": "Toni Kay", "email": "toni@example.org", "macro": "tk", "role": "editor",
                   "token": secrets.token_urlsafe(18)}}


def shared_setup(tmp, sharing):
    """Check that shared mode refuses the host's TeX, then start the shared-mode instance (it builds while
    the hub's checks run; sharing holds it, to be stopped). Returns what the shared checks need, or None
    where shared mode can't run: it builds only in the TeX Live image, with podman."""
    state = os.path.join(tmp, "shared-state")
    sh = {"tmp": tmp, "state": state, "token": make_state(state), "users": shared_users(), "users_file": os.path.join(tmp, "users.json")}
    SECRETS.extend([sh["token"]] + [user["token"] for user in sh["users"].values() if "token" in user])
    sh["root"] = make_shared_paper(tmp, state)
    stub, sh["editor_log"] = editor_stub(tmp, "shared-editor")
    host_tex, sh["host_tex_log"] = host_tex_stub(tmp)
    sh["env"] = {**ENV, "LATEX_LIVE_STATE": state, "LATEX_LIVE_USERS": sh["users_file"], "LATEX_LIVE_IDENTITY_HEADER": "x-test-user",
                 "LATEX_LIVE_PROXY_HOST": PROXY_HOST, "LATEX_LIVE_PUBLIC_URL": PUBLIC, "LATEX_LIVE_COMMIT": "1",
                 "LATEX_LIVE_COMMIT_IDLE": "2", "LATEX_LIVE_PROTECTED": r"\\eb\{as written\}", "LATEX_LIVE_EDITOR": stub,
                 "LATEX_LIVE_BUILD_LIMIT": "64",
                 "PATH": host_tex + os.pathsep + ENV.get("PATH", os.defpath)}
    if sys.platform.startswith("linux"):  # (the server looks the peer's uid up in /proc/net)
        sh["env"]["LATEX_LIVE_PROXY_UID"] = str(os.getuid())
    write_users(sh)
    tex_exec_check(tmp)
    host_tex_check(sh)
    image, pulled = texlive_image()
    if not pulled:
        print(f"skip: no podman or no {image} here, so shared mode, which builds only in that image, is not checked", flush=True)
        return None
    refusal_checks(sh)
    sh["refs"] = git_out(sh["root"], "for-each-ref", "--format=%(refname) %(objectname)")
    sh["base"] = git_out(sh["root"], "rev-parse", "shared")
    sh["proc"] = start(sh["root"], env=sh["env"], port=SHARED_PORT)
    sharing.append(sh["proc"])
    sh["log"] = sh["proc"].log
    return sh


def shared_checks(sh, sharing, own, token):
    """Start the shared-mode checks, which take the longest, in a thread of their own: they have an instance and a
    paper of their own, so they run beside the others. The first ones run here, before the takeover checks replace
    the owner's preview, which they reach. Returns the thread; its .failed says whether a check failed."""
    shared_start_checks(sh, own, token)
    return in_thread("shared-mode checks", shared_run, sh, sharing)


def shared_run(sh, sharing):
    """The shared-mode checks after the first ones, in order, ending with the instance's stop."""
    for check in (identity_checks, role_checks, export_checks, review_shared_checks, note_checks, editor_checks,
                  rebuild_stop_checks, commit_checks):
        check(sh)
    shared_stop_checks(sh, sharing)


def make_shared_paper(tmp, state):
    """The copy of the scratch paper that shared mode serves: a clone of its remote on branch "shared", with
    the files that the editor checks need (committed there), a branch that HEAD does not build on ("side"),
    git settings and hooks that the server's commits must override, files that the server must never list,
    serve, write or commit, latexmkrc files that latexmk must not read, and a change only the test makes."""
    root = os.path.join(tmp, "shared")
    subprocess.run(["git", "clone", "-q", "-b", "main", os.path.join(tmp, "remote.git"), root], check=True, capture_output=True)
    git(root, "checkout", "-q", "-b", "side")
    write_text(root, "side.tex", "A commit that the shared branch does not build on.\n")
    git(root, "add", "side.tex")
    git(root, "commit", "-qm", "side")
    git(root, "checkout", "-q", "-b", "shared", "main")
    for name, text in (("generated.inc", "Generated quetzal sentence.\n\n"), ("refs.bib", "@misc{one, title = {One}}\n"),
                       (os.path.join("sub", "refs.bib"), "@misc{two, title = {Two}}\n"),
                       ("crlf.bib", "@misc{three,\r\n  title = {Three},\r\n}\r\n"),
                       (BIBDATA_NAME + ".bib", "@misc{probe, title = {Probe}}\n")):
        write_text(root, name, text)
    with open(os.path.join(root, "body.tex"), "a") as f:
        f.write("\n\\input{generated.inc}\n\\iffalse\\eb{Quagga private draft note}\\fi\n")  # (typeset text from a file the
        # editor does not offer, and a note that is not: role_checks)
    git(root, "add", "-A")
    git(root, "commit", "-qm", "shared fixtures")
    os.chmod(os.path.join(root, "crlf.bib"), 0o640)  # (a save keeps the mode; git does not track this one)
    for key, value in (("commit.gpgsign", "true"), ("gpg.program", "false"), ("user.name", "Not The Owner"),
                       ("user.email", "not-the-owner@example.invalid")):
        subprocess.run(["git", "-C", root, "config", key, value], check=True)
    hooks = os.path.join(root, ".git", "hooks")
    os.makedirs(hooks, exist_ok=True)
    for hook in ("pre-commit", "prepare-commit-msg", "commit-msg", "post-commit"):
        with open(os.path.join(hooks, hook), "w") as f:
            f.write(f"#!/bin/sh\n: > {shlex.quote(os.path.join(tmp, 'hook-ran'))}\nexit 1\n")
        os.chmod(os.path.join(hooks, hook), 0o755)
    write_text(root, "appendix.tex", "\\documentclass{article}\n\\begin{document}\nThe appendix.\n\\end{document}\n")
    write_text(root, "reviews.pdf", "%PDF-1.4\n% another PDF in the paper root\n")
    os.symlink(os.path.join(state, "token"), os.path.join(root, "leak.bib"))
    write_text(root, "big.bib", "%" + " " * (5 << 20) + "\n")  # (over the editor's 4 MB limit)
    with open(os.path.join(root, "latin.bib"), "wb") as f:
        f.write("@misc{four, title = {Caf\u00e9}}\n".encode("latin-1"))
    write_text(root, ".private.bib", "@misc{five}\n")
    write_text(root, os.path.join("build", "stray.bib"), "@misc{six}\n")
    ran = os.path.join(tmp, "latexmkrc-ran").replace("\\", "\\\\").replace("'", "\\'")
    for name in ("latexmkrc", ".latexmkrc"):
        write_text(root, name, f"open(my $f, '>', '{ran}'); close($f);\n")
    with open(os.path.join(root, "people.tex"), "a") as f:
        f.write(LOCAL_CHANGE + BIBDATA_PROBE)
    return root


def write_users(sh, text=None):
    """(Re)write the users file (mode 0600) with a later mtime, so that the server reads it again."""
    path = sh["users_file"]
    fd = os.open(path + ".new", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps({"users": sh["users"]}, indent=1) if text is None else text)
    os.replace(path + ".new", path)
    sh["users_mtime"] = stamp = max(sh.get("users_mtime", 0) + 2_000_000_000, time.time_ns())
    os.utime(path, ns=(stamp, stamp))


def tex_exec_check(tmp):
    """tex-exec with LATEX_LIVE_REQUIRE_IMAGE=1 (shared mode) refuses to run a tool on the host while the
    image is missing; without it, it falls back to the host as before. With LATEX_LIVE_SANDBOX=1 (shared
    mode), it confines the container, as a stand-in for podman shows: no capabilities, no new privileges,
    at most 512 processes, 4 GB, 2 CPUs, 1 GB files and the caller's deadline (LATEX_LIVE_TIMEOUT, at most
    10 minutes), labels that name the instance, the run and its kind (for stopping it), the paper read-only
    (its dot entries at any depth, .git among them, hidden) and only build/ writable, no shell escape, and TeX's files
    only below its folder; tools.container_argv in shared mode goes through tex-exec too."""
    cmd = [os.path.join(HERE, "tex-exec"), "localhost/latex-live-check-missing:none", tmp, "true"]
    fallback = subprocess.run(cmd, cwd=tmp, env=ENV, capture_output=True, text=True, timeout=60)
    required = subprocess.run(cmd, cwd=tmp, env={**ENV, "LATEX_LIVE_REQUIRE_IMAGE": "1"}, capture_output=True, text=True, timeout=60)
    if fallback.returncode:
        fail(f"tex-exec without the image no longer falls back to the host's tool: {fallback.stderr}")
    if not required.returncode or not required.stderr.strip():
        fail(f"tex-exec with LATEX_LIVE_REQUIRE_IMAGE=1 and no image ran the host's tool or said nothing (exit {required.returncode})")
    stub = os.path.join(tmp, "podman-stub")
    os.makedirs(stub)
    with open(os.path.join(stub, "podman"), "w") as f:  # (the image exists; a run prints its arguments, one per line)
        f.write('#!/bin/sh\n[ "$1" = image ] && exit 0\nprintf \'%s\\n\' "$@"\n')
    os.chmod(os.path.join(stub, "podman"), 0o755)
    root = os.path.join(tmp, "sandboxed")  # (a paper root with dot entries, which the container must not see)
    os.makedirs(os.path.join(root, ".git"))
    put(os.path.join(root, ".env"), "")
    put(os.path.join(root, "sub", ".secret.bib"), "")  # (and below the top: tex-exec hides them at any depth)
    os.makedirs(os.path.join(root, "sub", "deeper", ".private"))
    confined = subprocess.run([os.path.join(HERE, "tex-exec"), "localhost/latex-live-check:none", root, "pdflatex", "x.tex"], cwd=root,
                              env={**ENV, "LATEX_LIVE_SANDBOX": "1", "LATEX_LIVE_BUILD": "out", "LATEX_LIVE_INSTANCE": "check-1",
                                   "LATEX_LIVE_RUN": "main-7", "LATEX_LIVE_TIMEOUT": "60",
                                   "PATH": stub + os.pathsep + ENV.get("PATH", os.defpath)},
                              capture_output=True, text=True, timeout=60)
    from latexlive import config, tools
    shared, config.SHARED = config.SHARED, True
    started = tools.container_argv(tmp)
    config.SHARED = shared
    want = ("--cap-drop=all", "--security-opt=no-new-privileges", "--pids-limit=512", "--memory=4g", "--cpus=2",
            "fsize=1073741824:1073741824", "--timeout=60", "latex-live.instance=check-1", "latex-live.run=main-7",
            "latex-live.kind=main", f"{root}:{root}:ro", f"{root}/out:{root}/out", "openin_any=p", "openout_any=p",
            "shell_escape=f")
    hidden = {f"{root}/.git", f"{root}/.env", f"{root}/sub/.secret.bib", f"{root}/sub/deeper/.private"}  # (podman's mask)
    args = confined.stdout.splitlines()
    masks = [set(a[len("mask="):].split(":")) for a in args if a.startswith("mask=")]
    if masks != [hidden]:
        fail(f"tex-exec with LATEX_LIVE_SANDBOX=1 masks {masks}, not the paper's dot entries {hidden}")
    if (confined.returncode or not all(w in args for w in want) or f"{root}:{root}" in args
            or args[-3:] != ["localhost/latex-live-check:none", "pdflatex", "x.tex"]):
        fail(f"tex-exec with LATEX_LIVE_SANDBOX=1 runs podman with {args} (exit {confined.returncode}), not with all of {want}"
             " (and the paper only read-only)")
    if started[:1] != [os.path.join(HERE, "tex-exec")]:
        fail(f"tools.container_argv in shared mode is {started}, not through tex-exec")
    ok("tex-exec refuses the host's TeX when shared mode requires the image, and confines the container there (the paper"
       " read-only, limits, labels, 10 minutes at most)")


def refusal_checks(sh):
    """A shared instance refuses to start with the owner's own state folder (it would share their previews' token)
    and, on Linux, with the identity header but no LATEX_LIVE_PROXY_UID (any local program could send it)."""
    config = os.path.join(sh["tmp"], "config")  # (XDG_CONFIG_HOME: the default state folder, here a scratch one)
    no_state = {k: v for k, v in sh["env"].items() if k != "LATEX_LIVE_STATE"}
    no_uid = {k: v for k, v in sh["env"].items() if k != "LATEX_LIVE_PROXY_UID"}
    cases = [({**no_state, "XDG_CONFIG_HOME": config}, "LATEX_LIVE_STATE", "the default state folder")]
    if sys.platform.startswith("linux"):
        cases.append((no_uid, "LATEX_LIVE_PROXY_UID", "the identity header without LATEX_LIVE_PROXY_UID"))
    for env, word, what in cases:
        r = subprocess.run([sys.executable, os.path.join(HERE, "live.py"), "main.tex", "--no-build", "--no-open", "--port",
                            str(SHARED_PORT)], cwd=sh["root"], env=env, capture_output=True, text=True, timeout=60)
        if not r.returncode or word not in r.stdout + r.stderr:
            fail(f"a shared instance with {what} exited {r.returncode} without refusing (naming {word}):\n{r.stdout + r.stderr}")
    if os.path.exists(os.path.join(config, "latex-live")):
        fail("a shared instance refused for the default state folder still wrote there")
    ok("a shared instance refuses the default state folder" + (", and the identity header without LATEX_LIVE_PROXY_UID"
                                                                if len(cases) > 1 else ""))


def host_tex_check(sh):
    """Shared mode refuses --host-tex: collaborators' LaTeX must not run in the host's TeX, which can read
    the owner's files."""
    proc = subprocess.Popen([sys.executable, os.path.join(HERE, "live.py"), "appendix.tex", "--host-tex", "--no-open", "--port",
                             str(SHARED_PORT)], cwd=sh["root"], env=sh["env"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, start_new_session=True)
    try:
        out = proc.communicate(timeout=30)[0]
    except subprocess.TimeoutExpired:
        stop(proc)
        fail("shared mode with --host-tex kept running (it must refuse to build with the host's TeX)")
    if not proc.returncode or not out.strip():
        fail(f"shared mode with --host-tex exited {proc.returncode} without refusing:\n{out}")
    if f"{proc.pid}.json" in recorded(sh["state"]):
        fail("the instance that refused --host-tex is still listed")
    ok("shared mode refuses --host-tex")


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


def version_of(data):
    """A file version as the editor API gives it: the first 20 hex digits of the sha256 of its bytes."""
    return hashlib.sha256(data).hexdigest()[:20]


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


def outside_address():
    """This machine's address on its network (not loopback), or None."""
    for family, probe in ((socket.AF_INET, "192.0.2.1"), (socket.AF_INET6, "2001:db8::1")):
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as s:
                s.connect((probe, 9))  # (sends nothing: it only picks the address that traffic would leave from)
                address = s.getsockname()[0]
        except OSError:
            continue
        if not ipaddress.ip_address(address.split("%")[0]).is_loopback:
            return address
    return None


def shared_start_checks(sh, own, hub_token):
    """The shared-mode instance's build and start: hardened, alone on exactly its port (none other when it
    is taken), loopback peers only, its public URL printed and no token anywhere (nor when another instance
    finds it running), no part in the hub, and the edits it found uncommitted committed as the owner's."""
    root, cookie = sh["root"], [("Cookie", f"latex_live={sh['token']}")]

    def published():
        if sh["proc"].poll() is not None:  # (e.g. its port is taken, perhaps as another program's ephemeral port)
            fail(f"the shared-mode instance exited (status {sh['proc'].returncode}) before its first build")
        info = shared_status(sh).get("build") or {}
        if info.get("ok") is False:
            fail(f"the shared copy does not build in the hardened image: {info.get('errors')}")
        return info.get("ok") is True and os.path.isfile(os.path.join(root, "main.pdf"))

    wait_for("the shared copy's main.pdf", published)
    clean = wait_for("the shared copy's notes-free pass", lambda: shared_status(sh).get("cleanBuild"))
    if not clean.get("ok"):
        fail(f"the shared copy's notes-free pass failed in the hardened image: {clean}")

    def answering():
        r = send("GET", "/me", cookie, quiet=True)
        if r and r[0] != 200:
            fail(f"GET me with the shared instance's owner cookie: {r[0]} {r[2][:200]!r}")
        return r

    wait_for("the shared-mode instance", answering, timeout=60)
    sh["version0"] = expect_dict(with_token(sh["token"], "/status/main.pdf"), 200, "the shared copy's status")["version"]
    with open(sh["log"], errors="replace") as f:
        printed = f.read()
    if PUBLIC not in printed:
        fail(f"the shared-mode instance does not print its public URL ({PUBLIC})")
    if prints_token(printed):
        fail("the shared-mode instance prints a token (its own, a personal link's, or a ?t= link), which its log keeps")
    if re.search(r"/p/[^/\s]+/[^/\s]+/", printed) or os.path.exists(os.path.join(sh["state"], "hub.lock")):
        fail("the shared-mode instance takes part in the hub (a hub URL, or hub.lock in its state)")
    entry = read_json_file(os.path.join(sh["state"], "instances", f"{sh['proc'].pid}.json")) or {}
    lock = read_json_file(os.path.join(root, "build", "main-live.lock")) or {}
    if entry.get("port") != SHARED_PORT or lock.get("port") != SHARED_PORT:
        fail(f"the shared-mode instance is not recorded on port {SHARED_PORT}: instance {entry}, lock {lock}")
    if os.path.exists(os.path.join(sh["tmp"], "latexmkrc-ran")):
        fail("the paper's own latexmkrc ran (shared mode builds without latexmk)")
    with open(os.path.join(root, "build", "main-live.aux"), errors="replace") as f:
        if "\\bibdata{" + BIBDATA_NAME + "}" not in f.read():
            fail("the \\bibdata probe did not reach the shared copy's .aux, so it proves nothing")
    if any(os.path.exists(os.path.join(where, "bibdata-ran")) for where in (root, os.path.join(root, "build"))):
        fail("a \\bibdata name in the shared copy's .aux ran as a shell command on this machine")
    with open(os.path.join(root, "build", "main-live.log"), errors="replace") as f:
        texlog = f.read()
    if "latex-live-check: TeX refused an absolute path" not in texlog:
        fail("the shared copy's build " + ("read /etc/passwd: TeX runs without openin_any=p" if "TeX read an absolute path" in texlog
                                           else "logged nothing from the probe in people.tex"))
    ok(f"shared mode builds in the hardened image (no latexmk, so a \\bibdata name runs nothing; no absolute paths), on port"
       f" {SHARED_PORT}, out of the hub,"
       " printing its public URL and no token")

    # The test's change to people.tex (LOCAL_CHANGE), uncommitted when the instance started: no one's writes account
    # for it, so it is committed then, as the owner's, rather than in the next coauthor's commit of that file.
    shas = wait_for("the commit of the edits found uncommitted at the start",
                    lambda: git_out(root, "rev-list", f"{sh['base']}..shared").split(), timeout=20, step=0.2)
    found = [(git_out(root, "show", "-s", "--format=%an <%ae>|%cn <%ce>|%s", sha).split("|"), commit_user(root, sha),
              git_out(root, "diff-tree", "--no-commit-id", "--name-only", "-r", sha).split()) for sha in shas]
    owner = "Owner Example <owner@example.org>"
    if (len(found) != 1 or found[0][0][:2] != [owner, owner] or "people.tex" not in found[0][0][2]
            or found[0][1:] != ("unattributed", ["people.tex"])):
        fail(f"the edit found uncommitted at the start is not one commit of people.tex, as the owner's (Latex-Live-Shared:"
             f" unattributed): {found}")
    sh["base"] = shas[0]  # (the users' commits come after it: commit_checks)
    with open(os.path.join(root, "refs.bib"), "a") as f:
        f.write(LATER_CHANGE)
    ok("the edit found uncommitted at the start is committed then, as the owner's, alone")

    second = subprocess.Popen([sys.executable, os.path.join(HERE, "live.py"), "appendix.tex", "--no-build", "--no-open", "--port",
                               str(SHARED_PORT)], cwd=root, env=sh["env"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                              start_new_session=True)
    try:
        out = second.communicate(timeout=30)[0]
    except subprocess.TimeoutExpired:
        stop(second)
        fail(f"a second shared-mode instance kept running though port {SHARED_PORT} is taken (shared mode uses no other port)")
    if not second.returncode or str(SHARED_PORT) not in out:
        fail(f"a second shared-mode instance on the taken port {SHARED_PORT} exited {second.returncode} without saying so:\n{out}")
    if f"{second.pid}.json" in recorded(sh["state"]):
        fail("the instance that found its port taken is still listed")
    ok("a shared-mode instance whose port is taken says so and exits")
    portless = subprocess.run([sys.executable, os.path.join(HERE, "live.py"), "appendix.tex", "--no-build", "--no-open"],
                              cwd=root, env=sh["env"], capture_output=True, text=True, timeout=60)
    if not portless.returncode or "--port" not in portless.stdout + portless.stderr:
        fail(f"a shared-mode instance without --port exited {portless.returncode} without asking for it:\n"
             f"{portless.stdout + portless.stderr}")
    ok("a shared-mode instance needs --port (it never takes the hub's)")
    again = subprocess.run([sys.executable, os.path.join(HERE, "live.py"), "main.tex", "--no-build", "--no-open", "--port",
                            str(SHARED_PORT)], cwd=root, env=sh["env"], capture_output=True, text=True, timeout=60)
    out = again.stdout + again.stderr
    if again.returncode or "already running" not in out or PUBLIC not in out or prints_token(out):
        fail(f"a second shared-mode instance for main.tex exited {again.returncode} without saying it is already running, with"
             f" the public URL and no token:\n{out}")
    ok("a second shared-mode instance for the same document says it is already running, with the public URL and no token")

    expect(send("GET", "/me", cookie, addr="::1"), 200, "the owner's cookie over IPv6 loopback ([::1])")
    address = outside_address()
    if not address:
        return print("skip: no network address here, so the loopback-only check is not made", flush=True)
    control = send("GET", "/docs", [("Host", f"localhost:{own}"), ("Cookie", f"latex_live={hub_token}")], port=own, addr=address,
                   timeout=5, quiet=True)
    answer = send("GET", "/me", [("Host", f"localhost:{SHARED_PORT}")] + cookie, addr=address, timeout=5, quiet=True)
    if answer is not None:
        fail(f"the shared-mode instance answered a peer at {address}, which is not loopback ({answer[0]})")
    if control is None:
        return print(f"skip: {address} does not reach this machine's own ports, so the loopback-only check proves little", flush=True)
    ok(f"the shared-mode instance answers over loopback (127.0.0.1, [::1]) but not from {address}")


def identity_checks(sh):
    """Who is asking: the Host allowlist, the identity header and its edge cases, tokens and cookies, GET me
    for each kind of user, and users added (or a broken users file) without a restart."""
    owner, tk = sh["token"], sh["users"]["tk"]["token"]
    cookie, proxy = [("Cookie", f"latex_live={owner}")], [("Host", PROXY_HOST)]
    for host in ("evil.example", f"evil.example:{SHARED_PORT}", "paper.invalid.evil.example:8443"):
        expect(send("GET", "/me", [("Host", host)] + cookie), 403, f"the owner's cookie with Host {host} (DNS rebinding)")
    for host in (f"localhost:{SHARED_PORT}", "localhost", f"127.0.0.1:{SHARED_PORT}", f"[::1]:{SHARED_PORT}"):
        expect(send("GET", "/me", [("Host", host)] + cookie), 200, f"the owner's cookie with Host {host}")
    expect(send("GET", "/me", [("Host", "paper.invalid:8443"), ("Cookie", f"latex_live={tk}")]), 200,
           "a personal link's cookie with the public Host")
    ok("the shared instance answers loopback, public and proxy Host names only")

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
            (owners + [("Origin", "http://localhost:5000")], True, "a loopback origin on another port"),
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
    ok("POSTs need the public origin through the proxy and on the public Host, a loopback one (or none, with a token) on loopback")

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


def note_checks(sh):
    """Notes in shared mode: each user writes as themselves (their own macro, never another's), edits and
    deletes only their own notes, writes LaTeX only as an editor, and may resolve anyone's note; protected
    text keeps its rules; a user whose note macro the paper lacks is told so. Each user's edits are
    committed in the background once they have been quiet for a while."""
    root = sh["root"]
    path = os.path.join(root, "body.tex")
    sh["tip"] = sh["base"]  # (wait_commit: the newest commit seen)
    wait_for("the shared copy's build to catch up", lambda: settled(sh), timeout=120)
    text = read_text(path)
    first, second = line_of(text, "Quokka narwhal"), line_of(text, "Walrus yak")
    at_first = text_point(as_editor, "body.tex", first)

    def add(point, selection, words, macro):
        return {"action": "add", "page": point[0], "x": point[1], "y": point[2], "selection": selection, "text": words, "macro": macro}

    owners = {"source": "body.tex", "line": first, "macro": "eb", "tex": "An owner note to resolve."}
    marker = {"source": "body.tex", "line": second, "macro": "eb", "tex": "as written"}
    for uid, req, what in (("vw", add(at_first, "axolotl pangolin", "A viewer's note", "vw"), "a viewer adding a note"),
                           ("ed", {"action": "edit", **owners, "text": "Rewritten"}, "the editor editing the owner's note"),
                           ("ed", {"action": "delete", **owners}, "the editor deleting the owner's note"),
                           ("cm", {"action": "edit", **owners, "latex": "\\textbf{Rewritten}"},
                            "a commenter writing LaTeX into the owner's note"),
                           ("cm", {"action": "resolve", **marker, "who": "cm", "text": "Done"},
                            "a commenter resolving the protected-text marker")):
        expect(note(sh, uid, req), 403, what)
    refused = str(expect(note(sh, "tk", add(at_first, "axolotl pangolin", "A note", "tk")), 403,
                         "a user whose note macro the paper lacks"))
    if "tk" not in refused or "defined" not in refused:
        fail(f"a user whose note macro the paper lacks is not told so: {refused[:300]}")
    if read_text(path) != text:
        fail("a refused note action changed body.tex")
    ok("notes: a viewer, the owner's note, LaTeX from a commenter, the protected marker and a missing macro are refused")

    one = note_ok(sh, "ed", add(at_first, "axolotl pangolin", "ED note one", "ed"), "the editor adding a note")
    if (one.get("macro"), one.get("source")) != ("ed", "body.tex") or "\\ed{ED note one}" not in read_text(path):
        fail(f"the editor's new note is not \\ed{{ED note one}} in body.tex: {one}")
    wait_commit(sh, "ed", ("body.tex",), "the editor's note")
    two = note_ok(sh, "cm", add(at_first, "tapir gecko", "CM note one", "cm"), "a commenter adding a note")
    if two.get("macro") != "cm" or "\\cm{CM note one}" not in read_text(path):
        fail(f"the commenter's new note is not \\cm{{CM note one}} in body.tex: {two}")
    kept = note_ok(sh, "cm", add(text_point(as_editor, "body.tex", second), "ibex okapi", "CM on protected", "cm"),
                   "a commenter's note on protected text")
    text = read_text(path)
    if kept.get("protected") is not True or not 0 <= text.find("\\eb{as written}") < text.find("\\cm{CM on protected}"):
        fail(f"a note on protected text did not go after the marker: {kept}")
    wait_commit(sh, "cm", ("body.tex",), "the commenter's notes")
    ok("an editor and a commenter add notes with their own macros (one on protected text goes after the marker),"
       " each committed once they are quiet")

    wait_for("the shared copy's build to catch up", lambda: settled(sh), timeout=120)
    text = read_text(path)
    eds = {"source": "body.tex", "line": one["line"], "macro": "ed", "tex": "ED note one"}
    cms = {"source": "body.tex", "line": two["line"], "macro": "cm", "tex": "CM note one"}
    for uid, req, what in (("cm", {"action": "edit", **eds, "text": "Hijacked"}, "a commenter editing the editor's note"),
                           ("ed", {"action": "edit", **cms, "text": "Hijacked"}, "the editor editing a commenter's note"),
                           ("ed", {"action": "delete", **cms}, "the editor deleting a commenter's note"),
                           ("cm", {"action": "edit", **cms, "latex": "CM \\emph{note}"}, "a commenter writing LaTeX into their own note"),
                           ("vw", {"action": "resolve", **eds, "who": "vw", "text": "No"}, "a viewer resolving a note")):
        expect(note(sh, uid, req), 403, what)
    if read_text(path) != text:
        fail("a refused note action changed body.tex")
    posing = note(sh, "ed", add(at_first, "Quokka narwhal", "Not as eb", "eb"))
    text = read_text(path)
    if ("\\eb{Not as eb}" in text or posing[0] not in (200, 400, 403)
            or (posing[0] == 200 and (expect_dict(posing, 200, "").get("macro") != "ed" or "\\ed{Not as eb}" not in text))):
        fail(f"the editor asking to write as \\eb: {posing[0]} {posing[2][:200]!r} (want it refused, or written as \\ed)")
    note_ok(sh, "ed", {"action": "edit", **eds, "latex": "ED note \\emph{one}"}, "the editor writing LaTeX into their own note")
    wait_commit(sh, "ed", ("body.tex",), "the editor's note edit")
    note_ok(sh, "cm", {"action": "edit", **cms, "text": "CM note edited"}, "a commenter editing their own note")
    reply = note_ok(sh, "cm", {"action": "reply", **eds, "tex": "ED note \\emph{one}", "who": "ed", "text": "CM reply"},
                    "a commenter replying (naming someone else as who)")
    note_ok(sh, "cm", {"action": "resolve", **owners, "who": "ed", "text": "Done by CM"}, "a commenter resolving the owner's note")
    wait_commit(sh, "cm", ("body.tex",), "the commenter's edit, reply and resolution")
    text = read_text(path)
    for want in ("\\ed{ED note \\emph{one}}", "\\cm{CM note edited}", "\\cm{CM reply}"):
        if want not in text:
            fail(f"body.tex lacks {want} after the edits")
    if reply.get("macro") != "cm" or "\\ed{CM reply}" in text:
        fail(f"a commenter's reply was written as someone else: {reply}")
    if (not re.search(r"^% RESOLVED \S+ \(cm\): Done by CM$", text, re.M)
            or re.search(r"^[^%\n]*\\eb\{An owner note to resolve\.\}", text, re.M)):
        fail(f"the owner's note is not resolved under the commenter's initials:\n{text[:700]}")
    ok("notes: no one edits or deletes others' notes or writes as someone else; editors may write LaTeX; anyone who comments resolves")
    notes_while_rebuilding(sh)


def notes_while_rebuilding(sh):
    """Notes keep working while the shared copy rebuilds, and so do the lists of notes and of note macros: the
    server reads the paper's files from its last successful build, not from the list of the files read (the
    .fls) that pdflatex rewrites as each pass goes. Each note edit (the editor's, of their own note) is sent as
    soon as a pass has begun, while that list lacks body.tex, and makes the build go another pass."""
    root = sh["root"]
    fls = os.path.join(root, "build", "main-live.fls")
    state = {"edits": 0, "asked": 0.0}  # (note edits so far; when a rebuild was last asked for)

    def mid_pass():
        try:
            with open(fls, errors="replace") as f:
                return "body.tex" not in f.read()
        except OSError:
            return True

    def until(test, what):
        """Wait for test(), asking for a rebuild whenever the build has settled for a while (a pass missed)."""
        while not test():
            if time.monotonic() > end:
                fail(f"timed out waiting for {what}, after {state['edits']} note edits while the shared copy rebuilds")
            if time.monotonic() - state["asked"] > 5 and settled(sh):
                expect(with_token(sh["token"], "/rebuild", "POST", {}), 202, "the owner rebuilding")  # (no 20 s limit)
                state["asked"] = time.monotonic()
            time.sleep(0.002)

    wait_for("the shared copy's build to catch up", lambda: settled(sh), timeout=120)
    listed = expect(via_proxy("ed", "/notes/main.pdf"), 200, "GET notes for the editor")
    mine = [n for n in listed if isinstance(n, dict) and n.get("macro") == "ed" and n.get("file") == "body.tex"]
    if not mine:
        fail(f"/notes lists no note of the editor's in body.tex: {listed}")
    req = {"action": "edit", "source": "body.tex", "line": mine[0]["line"], "macro": "ed", "tex": mine[0]["tex"]}
    during, end = 0, time.monotonic() + 60
    while state["edits"] < 6:
        until(mid_pass, "a pass of the build to begin")
        state["edits"] += 1
        text = f"ED note, edited during pass {state['edits']}"
        note_ok(sh, "ed", {**req, "text": text}, f"the editor editing their own note as a pass of the build begins ({state['edits']})")
        req["tex"] = text
        during += mid_pass()  # (pdflatex still had not read body.tex: the note was written mid-pass)
        now = expect(via_proxy("ed", "/notes/main.pdf"), 200, "GET notes for the editor")
        authors = {a.get("macro") for a in as_viewer("/authors/main.pdf") if isinstance(a, dict)}
        if len(now) != len(listed) or not {"eb", "ed", "cm"} <= authors:
            fail(f"while the shared copy rebuilds, /notes lists {len(now)} notes, not {len(listed)}, and /authors {sorted(authors)}")
        until(lambda: not mid_pass(), "the pass to read body.tex")
    if not during:
        fail("no note edit was answered before pdflatex read body.tex, so this check met no pass in progress")
    wait_commit(sh, "ed", ("body.tex",), "the editor's note edits during the rebuilds")
    ok(f"notes, /notes and /authors keep working while the paper rebuilds ({during} of {state['edits']} note edits were"
       " answered mid-pass, before pdflatex read body.tex again)")


def save(uid, req, status, what):
    return expect_dict(via_proxy(uid, "/file", "POST", req), status, what)


def editor_checks(sh):
    """The browser editor's files API (for the editor ed), status ?open=, auto-commit waiting out a git lock
    (and another user's write of the same file waiting for that commit), writes waiting out a merge in the
    shared copy, comparing (diff) only with commits that HEAD builds on, rebuilding, and the owner's POST open
    (the editor stand-in's only call)."""
    root, owner = sh["root"], sh["token"]
    path, crlf_path, token_file = os.path.join(root, "body.tex"), os.path.join(root, "crlf.bib"), os.path.join(sh["state"], "token")
    listing = expect_dict(via_proxy("ed", "/files"), 200, "GET files for the editor")
    files = [f for f in listing.get("files") or [] if isinstance(f, dict)]
    paths = [f.get("path") for f in files]
    if listing.get("main") != "main.tex" or paths[:1] != ["main.tex"] or paths[1:] != sorted(paths[1:]):
        fail(f"GET files does not list main.tex first, then the rest by path: {listing}")
    missing = [p for p in ("body.tex", "people.tex", "refs.bib", "sub/refs.bib", "crlf.bib") if p not in paths]
    offered = [p for p in paths if p.startswith(("build/", ".git/")) or any(part.startswith(".") for part in p.split("/"))
               or p in ("leak.bib", "big.bib", "appendix.tex", "generated.inc", "side.tex", "reviews.pdf", "latexmkrc")]
    sizes = {f.get("path"): f.get("size") for f in files}
    if missing or offered or sizes.get("body.tex") != os.path.getsize(path):
        fail(f"GET files: missing {missing}, offering {offered}, body.tex size {sizes.get('body.tex')}: {paths}")
    ok("GET files lists the paper's sources and .bib files (main.tex first), not build/, .git, dot files, links out, big files"
       " or other documents")

    disk = read_bytes(path)
    got = expect_dict(via_proxy("ed", "/file?path=body.tex"), 200, "GET file body.tex")
    want = {"path": "body.tex", "text": disk.decode(), "version": version_of(disk), "eol": "\n"}
    if {key: got.get(key) for key in want} != want:
        fail(f"GET file body.tex: {str(got)[:300]}, not its text, version {version_of(disk)} and eol \\n")
    if expect_dict(via_proxy("ed", "/file?path=sub/refs.bib"), 200, "GET file sub/refs.bib").get("text") != "@misc{two, title = {Two}}\n":
        fail("GET file sub/refs.bib does not give its text")
    new = with_paragraph(got["text"], 3, "Paragraph three, saved by the editor.")
    saved = save("ed", {"path": "body.tex", "base": got["version"], "text": new}, 200, "a save on the current version")
    disk = read_bytes(path)
    if disk.decode() != new or saved.get("version") != version_of(disk) or saved.get("merged") or saved.get("text", new) != new:
        fail(f"a save on the current version: {str(saved)[:300]}")
    ok("GET file gives the text and its version; a save on the current version writes it")

    base = expect_dict(via_proxy("ed", "/file?path=body.tex"), 200, "GET file body.tex")
    put(path, with_paragraph(base["text"], 24, "Paragraph twenty-four, changed on disk meanwhile."))
    older = with_paragraph(base["text"], 1, "Paragraph one, saved from an older copy.")
    merged = save("ed", {"path": "body.tex", "base": base["version"], "text": older}, 200, "a save on an older version")
    disk = read_bytes(path)
    if (merged.get("merged") is not True or merged.get("text") != disk.decode() or merged.get("version") != version_of(disk)
            or "Paragraph twenty-four, changed on disk meanwhile." not in disk.decode()
            or "Paragraph one, saved from an older copy." not in disk.decode()):
        fail(f"a save on an older version did not merge with the change on disk: {str(merged)[:300]}")
    base = expect_dict(via_proxy("ed", "/file?path=body.tex"), 200, "GET file body.tex")
    theirs = with_paragraph(base["text"], 1, "Paragraph one, changed on disk (theirs).")
    mine = with_paragraph(base["text"], 1, "Paragraph one, changed in the browser (mine).")
    put(path, theirs)
    clash = save("ed", {"path": "body.tex", "base": base["version"], "text": mine}, 409, "a save that conflicts with the file on disk")
    disk = read_bytes(path)
    if disk.decode() != theirs or b"<<<<<<<" in disk or b">>>>>>>" in disk:
        fail("a conflicting save changed the file on disk")
    marked = str(clash.get("merged"))
    if (clash.get("conflict") is not True or clash.get("version") != version_of(disk) or clash.get("text") != theirs
            or "<<<<<<< " not in marked or ">>>>>>> " not in marked or "|||||||" in marked):
        fail(f"a conflicting save's answer: {str(clash)[:300]} (want the file's version and text, and git's merge with two-sided"
             " conflict markers)")
    save("ed", {"path": "body.tex", "base": clash["version"], "text": mine, "force": True}, 200,
         "the same save with force (use my version)")
    if read_text(path) != mine:
        fail("a save with force did not write the text")
    copy = with_paragraph(read_text(path), 20, "Paragraph twenty, changed on disk before the copy.")
    put(path, copy)  # (a version the server never served or wrote)
    put(path, with_paragraph(copy, 22, "Paragraph twenty-two, changed on disk after the copy."))
    edit = with_paragraph(copy, 3, "Paragraph three, saved again from that copy.")
    stale = save("ed", {"path": "body.tex", "base": version_of(copy.encode()), "text": edit}, 409,
                 "a save on a version the server never saw")
    disk = read_bytes(path)
    if stale.get("stale") is not True or stale.get("version") != version_of(disk) or stale.get("text") != disk.decode():
        fail(f"a save on an unknown version: {str(stale)[:300]}")
    again = save("ed", {"path": "body.tex", "base": version_of(copy.encode()), "base_text": copy, "text": edit}, 200,
                 "the same save with its base text")
    text = read_text(path)
    if again.get("merged") is not True or any(s not in text for s in ("Paragraph twenty, changed", "Paragraph twenty-two, changed",
                                                                      "Paragraph three, saved again")):
        fail(f"a save with its base text did not merge: {str(again)[:300]}")
    ok("a stale save merges; a conflict writes nothing and answers both texts; force writes; an unknown base asks for its text")

    base = expect_dict(via_proxy("ed", "/file?path=body.tex"), 200, "GET file body.tex")
    once = with_paragraph(base["text"], 7, "Paragraph seven, saved once.")
    save("ed", {"path": "body.tex", "base": base["version"], "text": once, "editor": "tab-a"}, 200,
         "a save (whose answer the editor never gets)")
    more = with_paragraph(base["text"], 7, "Paragraph seven, saved once, then typed on.")
    save("ed", {"path": "body.tex", "base": base["version"], "text": more, "editor": "tab-a"}, 200,
         "the next save, typed on in the same line, from the version before the save whose answer got lost")
    if read_text(path) != more:
        fail("the save after one whose answer got lost did not write its text")
    tab = with_paragraph(base["text"], 7, "Paragraph seven, from the same user's other tab.")
    clash = save("ed", {"path": "body.tex", "base": base["version"], "text": tab, "editor": "tab-b"}, 409,
                 "the same user's save of that line from another tab, from the same older version")
    if clash.get("conflict") is not True or read_text(path) != more:
        fail("a save of the same line from the same user's other tab overwrote the first tab's instead of conflicting")
    undo = expect_dict(via_proxy("ed", "/file?path=body.tex"), 200, "GET file body.tex")
    save("ed", {"path": "body.tex", "base": undo["version"], "text": with_paragraph(undo["text"], 9, "Paragraph nine, then undone."),
                "editor": "tab-a"}, 200, "a save (whose answer the editor never gets)")
    save("ed", {"path": "body.tex", "base": undo["version"], "text": undo["text"], "editor": "tab-a"}, 200,
         "the same tab's save after undoing that edit")
    if read_text(path) != undo["text"]:
        fail("an edit undone after a save whose answer got lost stayed in the file")
    theirs = with_paragraph(base["text"], 7, "Paragraph seven, as another editor changed it in an older copy.")
    clash = expect_dict(with_token(sh["users"]["tk"]["token"], "/file", "POST",
                                   {"path": "body.tex", "base": base["version"], "text": theirs, "editor": "tab-a"}),
                        409, "another editor's save of that line from the same older version")
    if clash.get("conflict") is not True or clash.get("by") != sh["users"]["ed"]["name"] or read_text(path) != more:
        fail(f"another editor's save of that line from the same older version: {str(clash)[:300]} (want a conflict that says"
             f" by: {sh['users']['ed']['name']}, who saved meanwhile, and nothing written)")
    ok("a save whose answer got lost doesn't conflict with the same tab's next save of the same line (and an undo of it"
       " undoes it in the file); another tab's or another user's does, and says who saved meanwhile")

    # A draft restored while the tab had saved newer edits (the viewer's restoreKept): sent from the draft's old base,
    # under the new save name that restoreKept takes, the tab's acknowledged save counts as a change by someone else,
    # so overlapping edits conflict rather than the draft overwriting them.
    draft_base = expect_dict(via_proxy("ed", "/file?path=body.tex"), 200, "GET file body.tex")
    newer = with_paragraph(draft_base["text"], 13, "Paragraph thirteen, saved after the draft was kept.")
    save("ed", {"path": "body.tex", "base": draft_base["version"], "text": newer, "editor": "tab-c"}, 200,
         "the tab's newer save, answered")
    draft = with_paragraph(draft_base["text"], 13, "Paragraph thirteen, as the kept draft had it.")
    clash = save("ed", {"path": "body.tex", "base": draft_base["version"], "base_text": draft_base["text"], "text": draft,
                        "editor": "tab-c-restored"}, 409, "the restored draft's save, under a new save name")
    if clash.get("conflict") is not True or read_text(path) != newer:
        fail(f"a restored draft overwrote the newer saved edits of the same line instead of conflicting: {str(clash)[:200]}")
    page = "\n".join(read_text(p) for p in [os.path.join(HERE, "viewer.html"), *sorted(glob.glob(os.path.join(HERE, "viewer", "*.js")))])
    restore = page[page.index("async function restoreKept"):]
    restore = restore[:restore.index("\n}\n")]
    if "editor: saveId" not in page or "saveId = newSaveId()" not in restore:
        fail("the viewer's restoreKept no longer saves a restored draft under a new save name")
    ok("a restored draft is saved under a new save name, so the tab's newer saved edits of the same lines conflict with it"
       " instead of being overwritten")

    crlf = expect_dict(via_proxy("ed", "/file?path=crlf.bib"), 200, "GET file crlf.bib")
    raw = read_bytes(crlf_path)
    if crlf.get("eol") != "\r\n" or crlf.get("text") != raw.decode().replace("\r\n", "\n") or crlf.get("version") != version_of(raw):
        fail(f"GET file of a CRLF file: {crlf}")
    edited = crlf["text"].replace("Three", "Four")
    done = save("ed", {"path": "crlf.bib", "base": crlf["version"], "text": edited}, 200, "a save of a CRLF file")
    raw = read_bytes(crlf_path)
    if raw != edited.replace("\n", "\r\n").encode() or done.get("version") != version_of(raw):
        fail(f"a save of a CRLF file did not keep its line endings: {raw!r}")
    if os.stat(crlf_path).st_mode & 0o777 != 0o640:
        fail(f"a save did not keep the file's mode (0640, now {os.stat(crlf_path).st_mode & 0o777:o})")
    expect(via_proxy("ed", "/file?path=latin.bib"), 415, "GET file of a file that is not UTF-8")
    for name in ("../shared-state/token", token_file, ".git/config", "build/main-live.log", "build/stray.bib", "leak.bib", "big.bib",
                 ".private.bib", "appendix.tex", "generated.inc", "nope.tex"):
        expect(via_proxy("ed", "/file?path=" + urllib.parse.quote(name, safe="")), 404, f"GET file {name!r} (not editable)")
    for query in ("%2e%2e%2fshared-state%2ftoken", "%2e%2e/shared-state/token", "%252e%252e%252fshared-state%252ftoken"):
        expect(via_proxy("ed", "/file?path=" + query), 404, f"GET file?path={query}")
    expect(via_proxy("ed", "/file?path="), (400, 404), "GET file without a path")
    before = {p: read_bytes(p) for p in (token_file, os.path.join(root, ".git", "config"), os.path.join(root, "build", "stray.bib"))}
    for name in ("../shared-state/token", token_file, "leak.bib", ".git/config", "build/stray.bib", "big.bib", "new.tex", "sub/new.bib"):
        expect(via_proxy("ed", "/file", "POST", {"path": name, "base": "0" * 20, "text": "Overwritten.\n", "force": True}), (403, 404),
               f"POST file {name!r} (not editable)")
    if (any(read_bytes(p) != data for p, data in before.items()) or os.path.exists(os.path.join(root, "new.tex"))
            or os.path.exists(os.path.join(root, "sub", "new.bib")) or os.path.getsize(os.path.join(root, "big.bib")) != (5 << 20) + 2):
        fail("a refused save changed or made a file")
    ok("CRLF files keep their line endings (and saves the mode); files that are not UTF-8 get 415; paths outside the editable"
       " set get 404")
    wait_commit(sh, "ed", ("body.tex", "crlf.bib"), "the editor's saves")

    def opened(name):
        source = expect_dict(via_proxy("ed", "/status/main.pdf?open=" + urllib.parse.quote(name, safe="")), 200,
                             f"status ?open={name}").get("source")
        return (source.get("path"), source.get("version")) if isinstance(source, dict) else (None, None)

    if opened("body.tex") != ("body.tex", version_of(read_bytes(path))):
        fail(f"status ?open=body.tex: {opened('body.tex')}, not its version")
    put(path, with_paragraph(read_text(path), 5, "Paragraph five, changed on disk while open."))
    if (opened("body.tex") != ("body.tex", version_of(read_bytes(path)))
            or opened("crlf.bib") != ("crlf.bib", version_of(read_bytes(crlf_path)))):
        fail("status ?open= does not give a file's version as on disk (after a change, or for a CRLF file)")
    for name in ("../shared-state/token", "leak.bib", "nope.tex"):
        if opened(name)[1]:
            fail(f"status ?open={name} gives a version (not an editable file)")
    lock = os.path.join(root, ".git", "index.lock")  # (as while someone runs git in the shared copy)
    open(lock, "w").close()
    current = expect_dict(via_proxy("ed", "/file?path=body.tex"), 200, "GET file body.tex")
    save("ed", {"path": "body.tex", "base": current["version"], "text": current["text"].replace("Walrus yak zebu", "Walrus yak and zebu")},
         200, "an editor's save inside protected text (no server-side check, by design)")
    saved = time.monotonic()
    if "Walrus yak and zebu" not in read_text(path):
        fail("an editor's save inside protected text was not written")
    # Before it writes the commenter's note in body.tex, the server commits the editor's edit there (a commit holds
    # one person's edits), which it can't while git is busy: so the note waits a moment, then is refused (503, to be
    # tried again), with nothing written.
    cms = [n for n in expect(via_proxy("ed", "/notes/main.pdf"), 200, "GET notes for the editor") if isinstance(n, dict) and n.get("macro") == "cm" and n.get("tex") == "CM note edited"]
    if not cms:
        fail("/notes does not list the commenter's note CM note edited")
    later = {"action": "edit", "source": "body.tex", "line": cms[0]["line"], "macro": "cm", "tex": "CM note edited",
             "text": "CM note, edited once git was free"}
    before = read_bytes(path)
    if (expect_dict(note(sh, "cm", later), 503, "a commenter's note in body.tex while the editor's edit there can't be committed")
            .get("retry") is not True or read_bytes(path) != before):
        fail("a commenter's note, refused while the editor's edit in the same file can't be committed, is not to be tried again,"
             " or changed body.tex")
    time.sleep(max(0.0, saved + 4 - time.monotonic()))  # (two quiet periods since the save)
    if git_out(root, "rev-list", f"{sh['tip']}..shared"):
        fail("auto-commit committed while .git/index.lock existed")
    os.remove(lock)
    wait_commit(sh, "ed", ("body.tex",), "the editor's save, once .git/index.lock is gone")
    note_ok(sh, "cm", later, "the commenter's note, once the editor's edit is committed")
    wait_commit(sh, "cm", ("body.tex",), "the commenter's note")
    ok("status ?open= follows the open file; editors may change protected text; edits wait out a git lock, then commit,"
       " and others' writes to the same file wait for that (503 meanwhile)")

    # During a merge in the shared copy (as when the owner merges the paper's main branch into it), writes are
    # refused (503, to be tried again): the merge's commit would take them, or its abort lose them. After it, they
    # go through.
    current = expect_dict(via_proxy("ed", "/file?path=body.tex"), 200, "GET file body.tex")
    nine = {"path": "body.tex", "base": current["version"],
            "text": with_paragraph(current["text"], 9, "Paragraph nine, saved once the merge was over.")}
    after = {**later, "tex": later["text"], "text": "CM note, edited once the merge was over"}
    git(root, "-c", "core.hooksPath=/dev/null", "merge", "--no-ff", "--no-commit", "side")
    if not os.path.isfile(os.path.join(root, ".git", "MERGE_HEAD")):
        fail("git merge --no-commit side left no merge in progress in the shared copy")
    before = read_bytes(path)
    for result, what in ((via_proxy("ed", "/file", "POST", nine), "the editor's save"), (note(sh, "cm", after), "a commenter's note")):
        if expect_dict(result, 503, f"{what} during a merge in the shared copy").get("retry") is not True:
            fail(f"{what} during a merge in the shared copy is refused, but not to be tried again: {result[2][:200]!r}")
    if read_bytes(path) != before:
        fail("a write during a merge in the shared copy changed body.tex")
    git(root, "-c", "core.hooksPath=/dev/null", "merge", "--abort")
    save("ed", nine, 200, "the editor's save, once the merge is over")
    wait_commit(sh, "ed", ("body.tex",), "the editor's save after the merge")
    note_ok(sh, "cm", after, "the commenter's note, once the merge is over")
    wait_commit(sh, "cm", ("body.tex",), "the commenter's note after the merge")
    ok("during a merge in the shared copy, saves and notes are refused (503, to be tried again) and write nothing;"
       " after it, they go through")

    expect(via_proxy("ed", "/diff", "POST", {"rev": "side"}), 403, "the editor comparing with a commit that HEAD does not build on")
    expect(via_proxy("ed", "/diff", "POST", {"rev": "--output=x"}), (400, 403, 404), "the editor comparing with a bad revision")
    expect(via_proxy("ed", "/diff", "POST", {"rev": "main"}), 200, "the editor comparing with a commit that HEAD builds on")
    diff = wait_for("the latexdiff build", lambda: (d := (expect_dict(via_proxy("ed", "/status/main-diff.pdf"), 200, "the diff's"
                                                                       " status").get("diff") or {})).get("state") in ("done", "failed")
                    and d, timeout=120, step=0.5)
    if diff.get("state") != "done":
        fail(f"the latexdiff build failed in the hardened image (the paper read-only): {diff}")
    expect(with_token(owner, "/diff", "POST", {"rev": "side"}), (200, 409), "the owner comparing with any commit")
    if not isinstance(expect_dict(via_proxy("ed", "/commits"), 200, "GET commits for the editor").get("commits"), list):
        fail("GET commits for the editor has no commits list")
    expect(via_proxy("ed", "/rebuild", "POST", {}), 202, "the editor rebuilding")
    expect(via_proxy("ed", "/rebuild", "POST", {}), 429, "the editor rebuilding again at once (at most every 20 s)")
    if editor_calls(sh["editor_log"]):
        fail(f"the editor stand-in ran for someone other than the owner: {editor_calls(sh['editor_log'])}")
    expect(with_token(owner, "/open", "POST", {"file": "main.tex", "line": 9}), 200, "the owner's POST open")
    calls = wait_for("the owner's POST open to run the editor stand-in", lambda: editor_calls(sh["editor_log"]), timeout=10, step=0.2)
    if len(calls) != 1 or not calls[0].startswith("-g ") or not calls[0].endswith("/main.tex:9"):
        fail(f"the editor stand-in got {calls}, not one -g .../main.tex:9")
    ok("editors compare only with commits HEAD builds on (the owner with any) and rebuild, at most every 20 s; only the owner's"
       " POST open runs the editor")


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


def rebuild_stop_checks(sh):
    """A rebuild stops the run in progress, its container too (a pass that slow.tex keeps busy for a minute; the
    shared copy's people.tex reads it while it exists), not only the podman client; and the build's state comes
    from the server, not from files in build/, where the paper's TeX writes (a status, a .building marker and a
    record of what was published, all forged)."""
    root, owner, pid = sh["root"], sh["token"], sh["proc"].pid
    slow = os.path.join(root, "slow.tex")
    put(slow, "\\loop\\ifnum\\pdfelapsedtime<3932160 \\repeat\n")  # (a minute of each pass)
    try:
        expect(with_token(owner, "/rebuild", "POST", {}), 202, "the owner rebuilding")
        wait_for("a container of the rebuild", lambda: instance_containers(pid, "main"), timeout=60, step=0.2)
        time.sleep(3)  # (a pass without slow.tex is over by now)
        busy = wait_for("a pass that slow.tex keeps busy", lambda: instance_containers(pid, "main"), timeout=30, step=0.2)
        sh["instance"] = next((f[1] for f in labelled_containers() if f[0] in busy), None)  # (its config.INSTANCE: shared_stop_checks)
        expect(with_token(owner, "/rebuild", "POST", {}), 202, "the owner rebuilding during that pass")
        wait_for("the busy pass's container to be gone", lambda: not busy & instance_containers(pid), timeout=20, step=0.2)
    finally:
        os.remove(slow)
    expect(with_token(owner, "/rebuild", "POST", {}), 202, "the owner rebuilding without slow.tex")
    wait_for("the shared copy's build to settle", lambda: settled(sh), timeout=120)
    ok("a rebuild stops the run in progress, and its container")

    build = os.path.join(root, "build")
    forged = {"main-status.status.json": json.dumps({"ok": False, "time": "00:00:00", "errors": [{"msg": "forged"}]}),
              "main-live.building": "", "main-live.published.json": json.dumps({"mtime": 1, "size": 1})}
    for name, text in forged.items():
        put(os.path.join(build, name), text)
    future = time.time_ns() + 3_600_000_000_000
    os.utime(os.path.join(build, "main-live.building"), ns=(future, future))
    info = shared_status(sh)
    for name in forged:
        os.remove(os.path.join(build, name))
    if (info.get("building") is not False or "forged" in json.dumps(info.get("build")) or not (info.get("build") or {}).get("ok")
            or "overwritten" in info):
        fail(f"the shared copy's status follows files forged in build/: building {info.get('building')}, build"
             f" {info.get('build')}, overwritten {info.get('overwritten')}")
    ok("the shared copy's status ignores a status, a .building marker and a record of what was published forged in build/")

    inc = os.path.join(root, "generated.inc")  # (a file the build reads that the editor doesn't offer)
    kept = read_bytes(inc)
    os.remove(inc)
    try:
        gone = wait_for("a build without generated.inc", lambda: (shared_status(sh).get("build") or {}).get("ok") is False
                        and shared_status(sh)["build"], timeout=60, step=0.5)
    finally:
        put_bytes(inc, kept)
    wait_for("the shared copy's build to settle", lambda: settled(sh) and (shared_status(sh).get("build") or {}).get("ok"),
             timeout=120)
    ok(f"removing a file the build reads rebuilds the shared copy (which then fails: {str(gone.get('errors'))[:60]}), and"
       " restoring it builds again")

    bib = os.path.join(root, BIBDATA_NAME + ".bib")
    kept = read_bytes(bib)
    put(bib, "@misc{probe, title = {Probe}\n")  # (no closing brace: bibtex fails)
    try:
        broken = wait_for("a build whose bibtex fails", lambda: "bibtex failed" in json.dumps((shared_status(sh).get("build") or {})
                                                                                           .get("errors")) and shared_status(sh)["build"],
                          timeout=60, step=0.5)
    finally:
        put_bytes(bib, kept)
    wait_for("the shared copy's build to settle", lambda: settled(sh) and (shared_status(sh).get("build") or {}).get("ok"),
             timeout=120)
    if broken.get("ok") is not False or "the end of its log" not in json.dumps(broken.get("errors")):
        fail(f"a build whose bibtex failed reports success, or not what bibtex said: {broken}")
    ok("a build whose bibtex fails reports it, with the end of its log, and builds again once the .bib is fixed")

    # A tool that failed runs again on the next run even when nothing it reads changed: here bibtex can't read the
    # .bib (no permission, which changes no mtime or size, so nothing is rebuilt for it), then can, and the next run,
    # for an unrelated change (main.tex touched), must run bibtex again rather than keep its failure.
    put(bib, read_text(bib) + "% changed\n")
    os.chmod(bib, 0)
    try:
        wait_for("a build whose bibtex can't read the .bib", lambda: "bibtex failed" in json.dumps(
            (shared_status(sh).get("build") or {}).get("errors")) and not shared_status(sh).get("building"), timeout=60, step=0.5)
    finally:
        os.chmod(bib, 0o644)
    main = os.path.join(root, "main.tex")
    os.utime(main)
    wait_for("a build after the .bib is readable again (main.tex touched)", lambda: (b := shared_status(sh).get("build") or {})
             .get("ok") and b.get("began", 0) >= mtime_ns(main) and settled(sh), timeout=120, step=0.5)
    put_bytes(bib, kept)
    wait_for("the shared copy's build to settle", lambda: settled(sh) and (shared_status(sh).get("build") or {}).get("ok"),
             timeout=120)
    ok("a failed bibtex runs again on the next run, though nothing it reads changed")

    with open(sh["log"], errors="replace") as f:
        runs = len(re.findall(r"=== live-reload: (?:published|BUILD FAILED)", f.read()))
    began = time.monotonic()
    while time.monotonic() - began < 3:  # (saves in a row: one build, once they stop)
        os.utime(main)
        time.sleep(0.25)
    wait_for("the shared copy's build to settle", lambda: settled(sh) and (shared_status(sh).get("build") or {}).get("ok"),
             timeout=120)
    with open(sh["log"], errors="replace") as f:
        made = len(re.findall(r"=== live-reload: (?:published|BUILD FAILED)", f.read())) - runs
    if not 1 <= made <= 2:
        fail(f"12 changes to main.tex within 3 s made {made} builds, not one (or two)")
    ok(f"changes in a row make one build once they stop ({made})")

    refer, later = os.path.join(root, "refer.tex"), os.path.join(root, "later.tex")
    put(refer, "\\input{later}\n")  # (a file that doesn't exist yet, as an edit that refers to a figure still to come)
    expect(with_token(owner, "/rebuild", "POST", {}), 202, "the owner rebuilding with refer.tex")
    wait_for("a build that misses later.tex", lambda: (b := shared_status(sh).get("build") or {}).get("ok") is False
             and b.get("began", 0) >= mtime_ns(refer) and not shared_status(sh).get("building"), timeout=60, step=0.5)
    put(later, "% It came later.\n")
    try:
        wait_for("a build once later.tex exists (no rebuild asked for)",
                 lambda: (b := shared_status(sh).get("build") or {}).get("ok") and b.get("began", 0) >= mtime_ns(later),
                 timeout=60, step=0.5)
    finally:
        os.remove(refer)
        os.remove(later)
    expect(with_token(owner, "/rebuild", "POST", {}), 202, "the owner rebuilding without refer.tex")
    wait_for("the shared copy's build to settle", lambda: settled(sh) and (shared_status(sh).get("build") or {}).get("ok"),
             timeout=120)
    ok("a file that a failed build missed builds the paper once it appears")

    put(os.path.join(root, "disk.tex"), "\\newcount\\diskn \\newwrite\\diskf \\def\\diska{xxxxxxxxxx}"
        "\\edef\\diskb{\\diska\\diska\\diska\\diska\\diska\\diska\\diska\\diska\\diska\\diska}"
        "\\edef\\diskc{\\diskb\\diskb\\diskb\\diskb\\diskb\\diskb\\diskb\\diskb\\diskb\\diskb}\n"
        "\\loop\\advance\\diskn by 1 \\immediate\\openout\\diskf=junk\\the\\diskn.txt\n"  # (1 MB in each of up to 500 files)
        "{\\count255=0 \\loop\\immediate\\write\\diskf{\\diskc}\\advance\\count255 by 1 \\ifnum\\count255<1000 \\repeat}"
        "\\immediate\\closeout\\diskf \\ifnum\\diskn<500 \\repeat\n")
    try:
        expect(with_token(owner, "/rebuild", "POST", {}), 202, "the owner rebuilding with disk.tex")
        full = wait_for("a build stopped for filling build/", lambda: "LATEX_LIVE_BUILD_LIMIT" in json.dumps(
            (shared_status(sh).get("build") or {}).get("errors")) and shared_status(sh)["build"], timeout=120, step=0.5)
        wait_for("build/ emptied and the run's container gone", lambda: not glob.glob(os.path.join(root, "build", "junk*.txt"))
                 and not instance_containers(pid, "main"), timeout=10, step=0.5)
    finally:
        os.remove(os.path.join(root, "disk.tex"))
    expect(with_token(owner, "/rebuild", "POST", {}), 202, "the owner rebuilding without disk.tex")
    wait_for("the shared copy's build to settle", lambda: settled(sh) and (shared_status(sh).get("build") or {}).get("ok"),
             timeout=120)
    ok(f"a build that fills build/ past LATEX_LIVE_BUILD_LIMIT is stopped, build/ emptied, and it says so"
       f" ({str(full.get('errors'))[:70]}); the next one builds")

    lock = os.path.join(root, "build", "main-live.lock")
    held = os.stat(lock).st_ino
    with open(os.path.join(root, "build", "flood.lock"), "wb") as f:  # (TeX could write it: only the instance's own lock stays)
        f.truncate(0)
        f.write(b"\0" * (70 << 20))
    wait_for("build/flood.lock removed", lambda: not os.path.exists(os.path.join(root, "build", "flood.lock")), timeout=30, step=0.5)
    if not os.path.exists(lock) or os.stat(lock).st_ino != held:
        fail("emptying build/ removed the instance's own lock file")
    asked = time.time_ns()
    expect(with_token(owner, "/rebuild", "POST", {}), 202, "the owner rebuilding after build/ was emptied")
    wait_for("the rebuild after build/ was emptied", lambda: (b := shared_status(sh).get("build") or {}).get("ok")
             and b.get("began", 0) >= asked and not shared_status(sh).get("building"), timeout=180, step=0.5)
    ok("a large file in build/ named like a lock file is removed with the rest when build/ is emptied; the instance's lock stays")

    marker = secrets.token_hex(8)
    put(os.path.join(root, ".git", "latex-live-check-marker"), marker)
    with open(os.path.join(root, ".git", "config"), "a") as f:  # (where a remote's credentials would be)
        f.write(f"[latexlivecheck]\n\tmarker = {marker}\n")
    nested = (os.path.join("sub", ".nested.bib"), os.path.join("sub", ".private", "refs.bib"))
    for name in nested:
        put(os.path.join(root, name), f"@misc{{{marker}}}\n")
    image = texlive_image()[0]
    try:
        seen = subprocess.run([os.path.join(HERE, "tex-exec"), image, root, "sh", "-c",
                               f"cat .git/config .git/latex-live-check-marker .private.bib {' '.join(nested)}; ls -a .git sub/.private"],
                              cwd=root, capture_output=True, text=True, timeout=120,
                              env={**ENV, "LATEX_LIVE_SANDBOX": "1", "LATEX_LIVE_REQUIRE_IMAGE": "1",
                                   "LATEX_LIVE_INSTANCE": f"{pid}-dotcheck"})
    finally:
        os.remove(os.path.join(root, nested[0]))
        shutil.rmtree(os.path.join(root, "sub", ".private"))
    # (a container that a build started meanwhile masks them too; podman's mask skips a path that is gone, rather
    # than making an empty file or folder in its place, as a mount over it would)
    wait_for("the instance's containers to finish", lambda: not instance_containers(pid), timeout=120, step=0.5)
    if os.path.lexists(os.path.join(root, nested[0])) or os.path.lexists(os.path.join(root, "sub", ".private")):
        fail("a dot entry deleted while a container hid it came back in the paper")
    if marker in seen.stdout + seen.stderr or "@misc{five}" in seen.stdout:
        fail("a shared-mode container can read the paper's .git (or another dot entry, at any depth): biber, which ignores"
             " openin_any, would too")
    ok("a shared-mode container sees no .git or other dot entry of the paper, at any depth (empty in their place)")

    from latexlive import config, tools
    saved = config.SHARED, config.INSTANCE
    config.SHARED, config.INSTANCE = True, f"{pid}-timeoutcheck"
    try:
        free = tools.SPAWNS._value
        code, _ = tools.run_tool([os.path.join(HERE, "tex-exec"), image, root, "sleep", "120"], root, timeout=8, kind="synctex",
                                env={**ENV, "LATEX_LIVE_SANDBOX": "1", "LATEX_LIVE_REQUIRE_IMAGE": "1",
                                     "LATEX_LIVE_INSTANCE": config.INSTANCE})
        left = subprocess.run(["podman", "ps", "-q", "--filter", f"label=latex-live.instance={config.INSTANCE}"], capture_output=True,
                              text=True, timeout=60).stdout.split()
    finally:
        config.SHARED, config.INSTANCE = saved
    if code == 0 or left or tools.SPAWNS._value != free:  # (None: stopped by run_tool; else by podman's own, equal, deadline)
        fail(f"a tool that ran out of time: exit {code}, {len(left)} container(s) left, {tools.SPAWNS._value} of {free} slots free")
    ok("a tool that runs out of time has its container stopped before its slot is free again")
    lock_spoof_checks(sh)


def lock_spoof_checks(sh):
    """The lock file (build/main-live.lock) names the running instance's port, but the paper's TeX can rewrite it:
    a second instance that finds the document locked asks that port whether it answers without the token (a decoy
    there records what it gets), and prints no token."""
    path, got = os.path.join(sh["root"], "build", "main-live.lock"), []
    with socket.socket(socket.AF_INET6) as decoy:
        decoy.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        decoy.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        try:
            decoy.bind(("::", DECOY_PORT))
        except OSError:
            return print(f"skip: port {DECOY_PORT} is taken, so the forged lock file is not checked", flush=True)
        decoy.listen(8)
        decoy.settimeout(0.5)

        def serve():
            while not done.is_set():
                try:
                    conn, _ = decoy.accept()
                except OSError:
                    continue
                with conn:
                    conn.settimeout(5)
                    try:
                        got.append(conn.recv(65536).decode(errors="replace"))
                        conn.sendall(b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n{}")
                    except OSError:
                        pass

        done, server = threading.Event(), threading.Thread(target=serve, daemon=True)
        server.start()
        with open(path, "r+") as f:  # (in place, as TeX's \\openout would: the running instance keeps its lock)
            real = f.read()
            f.seek(0)
            f.truncate()
            f.write(json.dumps({"port": DECOY_PORT, "pid": sh["proc"].pid}))
        try:
            again = subprocess.run([sys.executable, os.path.join(HERE, "live.py"), "main.tex", "--no-build", "--no-open", "--port",
                                    str(SHARED_PORT)], cwd=sh["root"], env=sh["env"], capture_output=True, text=True, timeout=60)
        finally:
            with open(path, "r+") as f:
                f.truncate()
                f.write(real)
            done.set()
            server.join(5)
    out = again.stdout + again.stderr
    if not got:
        fail(f"a second instance never asked the port in the forged lock file whether it answers (so this check proves nothing):\n{out}")
    if any(prints_token(text) or "latex_live=" in text for text in got):
        fail("a second instance sent a token to the port in a forged lock file")
    if prints_token(out):
        fail("a second instance that found a forged lock file printed a token")
    ok("a second instance asks the port in a forged lock file without its token, and prints none")


def commit_checks(sh):
    """Auto-commit: the owner's POST commit commits what is pending; every commit since the start is one
    user's edits, authored as that user from the users file and committed as the owner, with only files the
    server wrote; only the shared branch moved; the paper's hooks and signing settings did not apply; the
    HEAD baseline was not rebuilt for each commit."""
    root, users = sh["root"], sh["users"]
    flushed = expect_dict(with_token(sh["token"], "/commit", "POST", {}), 200, "the owner's POST commit")
    if not isinstance(flushed.get("commits"), list):
        fail(f"POST commit's answer has no commits list: {flushed}")
    if os.path.exists(os.path.join(sh["tmp"], "hook-ran")):
        fail("the paper's git hooks ran for a commit (the server commits with core.hooksPath=/dev/null)")
    shas = git_out(root, "rev-list", "--reverse", f"{sh['base']}..shared").split()
    if not shas:
        fail("nothing was committed on the shared branch")
    wrote = {}
    for sha in shas:
        name, email, cname, cemail, parents, subject = git_out(
            root, "show", "-s", "--format=%an%x1f%ae%x1f%cn%x1f%ce%x1f%P%x1f%s", sha).split("\x1f", 5)
        files = git_out(root, "diff-tree", "--no-commit-id", "--name-only", "-r", sha).split()
        uid = commit_user(root, sha)
        user = users.get(uid) or {}
        if (not user or (name, email) != (user["name"], user["email"]) or (cname, cemail) != ("Owner Example", "owner@example.org")
                or len(parents.split()) != 1 or not subject.startswith(f"{user['name']}: ") or not files
                or not all(f in subject for f in files)):
            fail(f"commit {sha[:9]}: author {name} <{email}>, committer {cname} <{cemail}>, {subject!r}, user {uid}, files {files}"
                 " (want the user's name and email from the users file, the owner's as committer, '<Name>: ...' naming the files,"
                 " a Latex-Live-Shared trailer)")
        wrote.setdefault(uid, set()).update(files)
    if wrote != {"ed": {"body.tex", "crlf.bib"}, "cm": {"body.tex"}}:
        fail(f"the commits hold other files, or other users' edits, than the server wrote for each: {wrote}")
    if git_out(root, "symbolic-ref", "HEAD") != "refs/heads/shared":
        fail("HEAD is no longer the shared branch")
    others = [line for line in git_out(root, "for-each-ref", "--format=%(refname) %(objectname)").splitlines()
              if not line.startswith("refs/heads/shared ")]
    if others != [line for line in sh["refs"].splitlines() if not line.startswith("refs/heads/shared ")]:
        fail(f"auto-commit moved refs other than the shared branch: {others}")
    if subprocess.run(["git", "-C", root, "merge-base", "--is-ancestor", sh["base"], "shared"]).returncode:
        fail("the shared branch no longer builds on where it started")
    status = git_out(root, "status", "--porcelain", "--untracked-files=normal").splitlines()
    changed = [line for line in status if not line.startswith("??")]
    untracked = sorted(line[3:] for line in status if line.startswith("??"))
    if changed != [" M refs.bib"]:
        fail(f"after POST commit, the tracked changes are {changed}, not just the test's own change to refs.bib (LATER_CHANGE)")
    if untracked != sorted([".latexmkrc", ".private.bib", "appendix.tex", "big.bib", "build/", "latexmkrc", "latin.bib", "leak.bib",
                            "main.pdf", "reviews.pdf"]):
        fail(f"the shared copy has unexpected untracked files: {untracked}")
    if os.path.isdir(os.path.join(root, "build", "head")):
        fail("shared mode built the HEAD baseline though no viewer asked for base=head (each commit moves HEAD)")
    ok(f"auto-commit: {len(shas)} commits, each one user's (author from the users file, committed by the owner), only files"
       " the server wrote, on the shared branch only")


def shared_stop_checks(sh, sharing):
    """The edits build. An edit still uncommitted when the instance stops (git in the middle of a merge, which
    the stop doesn't wait out) is kept in its state folder for the next start; stopping removes the instance
    from its list. Restarted without a build on an https public URL, it commits that edit as its writer's,
    and the test's change beside it (LATER_CHANGE) as the owner's; a personal link's cookie is Secure on the
    public Host only, and with a proxy uid that is not the peer's (Linux), the identity header counts for
    nothing. Neither run printed a token or a traceback."""
    root, path, pending = sh["root"], os.path.join(sh["root"], "body.tex"), os.path.join(sh["state"], "pending", "*.json")

    def built():
        if not settled(sh):
            return None
        info = expect_dict(via_proxy("vw", "/status/main.pdf"), 200, "the shared copy's status")
        result = info.get("build") or {}
        if result.get("ok") is False:
            fail(f"the shared copy no longer builds after the edits: {result.get('errors')}")
        return result.get("ok") and not info.get("building") and info.get("version") != sh["version0"]

    wait_for("the shared copy to build the edits", built, timeout=120)
    ok("the shared copy builds with everyone's edits and notes")

    for _ in range(2):  # (the second after the commit check's interval: a GET still builds nothing)
        head = (expect_dict(via_proxy("ed", "/status/main.pdf?base=head"), 200, "the status, asking for base=head") or {}).get("head")
        if not isinstance(head, dict) or head.get("state") != "waiting" or os.path.isdir(os.path.join(root, "build", "head")):
            fail(f"a GET of the status started the HEAD baseline's build (shared mode builds only on a POST): {head}")
        time.sleep(3.5)
    asked = expect_dict(via_proxy("ed", "/baseline", "POST", {"kind": "head"}), 200, "the editor asking for the HEAD baseline")
    if asked.get("state") not in ("running", "done"):
        fail(f"POST baseline for the HEAD baseline: {asked}")
    head = wait_for("the HEAD baseline", lambda: (shared_status(sh).get("head") or {}).get("state") in ("done", "failed")
                    and shared_status(sh)["head"], timeout=120, step=0.5)
    if head.get("state") != "done" or not head.get("version"):
        fail(f"the HEAD baseline, asked for with POST baseline, did not build: {head}")
    ok("a GET of the status builds no baseline; an editor's POST baseline does")

    tip = git_out(root, "rev-parse", "shared")
    current = expect_dict(via_proxy("ed", "/file?path=body.tex"), 200, "GET file body.tex")
    last = with_paragraph(current["text"], 11, "Paragraph eleven, saved just before the stop.")
    save("ed", {"path": "body.tex", "base": current["version"], "text": last}, 200, "a save just before the stop")
    git(root, "-c", "core.hooksPath=/dev/null", "merge", "--no-ff", "--no-commit", "side")  # (before the save's quiet period ends)
    # A container as a podman client leaves it when it is stopped between creating the container and starting it (as
    # stopping the instance's process group may do to a pass's): created, never started. The stop removes it too.
    if sh.get("instance"):
        subprocess.run(["podman", "create", "--pull=never", "--entrypoint=", "--label", f"latex-live.instance={sh['instance']}",
                        "--label", "latex-live.run=clean-0", "--label", "latex-live.kind=clean", texlive_image()[0], "true"],
                       capture_output=True, timeout=60, check=True)
    stop(sh["proc"])
    sharing.remove(sh["proc"])
    if recorded(sh["state"]):
        fail(f"the stopped shared-mode instance is still listed: {recorded(sh['state'])}")
    if left := instance_containers(sh["proc"].pid, every=True):
        subprocess.run(["podman", "rm", "-f", "-t", "0", *left], capture_output=True, timeout=60)
        fail(f"the stopped shared-mode instance left {len(left)} containers (running, or created and never started)")
    ok("stopping the shared-mode instance removes its containers, also one created and never started")
    kept = [read_json_file(p) or {} for p in glob.glob(pending)]
    if (git_out(root, "rev-parse", "shared") != tip
            or [k.get("pending", {}).get("ed", {}).get("paths") for k in kept] != [{"body.tex": "edit"}]):
        fail(f"the editor's save just before a stop during a merge is committed, or not kept for the next start: {kept}")
    quiet_log(sh["log"], "the shared-mode instance")
    git(root, "-c", "core.hooksPath=/dev/null", "merge", "--abort")
    if read_text(path) != last:
        fail("aborting the merge undid the editor's save (a mistake in this check)")
    linux = sys.platform.startswith("linux")
    env = {**sh["env"], "LATEX_LIVE_PUBLIC_URL": "https://paper.invalid"}
    if linux:
        env["LATEX_LIVE_PROXY_UID"] = str(os.getuid() + 1)
    proc = start(root, "main", "--no-build", env=env, port=SHARED_PORT)
    sharing.append(proc)
    log = proc.log

    def answering():
        if proc.poll() is not None:
            fail(f"the restarted shared-mode instance exited (status {proc.returncode})")
        r = send("GET", "/me", [("Cookie", f"latex_live={sh['token']}")], quiet=True)
        return r is not None and r[0] == 200

    wait_for("the shared-mode instance, restarted", answering, timeout=60)
    end = time.monotonic() + 20  # (it commits what the last run left before it serves a write)
    while glob.glob(pending) or git_out(root, "status", "--porcelain", "--untracked-files=no"):
        if time.monotonic() > end:
            fail(f"the restarted instance left edits uncommitted ({git_out(root, 'status', '--porcelain', '--untracked-files=no')!r}),"
                 f" or kept for later: {[read_json_file(p) for p in glob.glob(pending)]}")
        time.sleep(0.2)
    shas = git_out(root, "rev-list", "--reverse", f"{tip}..shared").split()
    found = {commit_user(root, sha): (git_out(root, "show", "-s", "--format=%an <%ae>", sha),
                                      git_out(root, "diff-tree", "--no-commit-id", "--name-only", "-r", sha).split()) for sha in shas}
    want = {"ed": ("Eden Doyle <eden@example.org>", ["body.tex"]), "unattributed": ("Owner Example <owner@example.org>", ["refs.bib"])}
    if len(shas) != 2 or found != want:
        fail(f"at the restart, the commits are {found}: not the editor's save before the stop as theirs, and the change made"
             " beside the server (refs.bib) as the owner's")
    ok("an edit uncommitted when the instance stopped (a merge in progress) is committed as its writer's at the next start,"
       " and the change made beside the server as the owner's")

    tk = sh["users"]["tk"]["token"]
    for host, secure in (("paper.invalid", True), (None, False)):
        first = send("GET", f"/me?t={tk}", [("Host", host)] if host else [])
        set_cookie = header(first, "Set-Cookie") or ""
        if first[0] != 200 or bool(re.search(r";\s*secure\b", set_cookie, re.I)) != secure:
            fail(f"a personal link on {host or 'a loopback Host'} with an https public URL: {first[0]}, cookie {redact(set_cookie)!r}"
                 f" ({'' if secure else 'not '}Secure wanted)")
    if linux:
        expect(via_proxy("ed", "/me"), 403, "the identity header from a peer whose uid is not LATEX_LIVE_PROXY_UID")
    stop(proc)
    sharing.remove(proc)
    if recorded(sh["state"]):
        fail(f"the stopped shared-mode instance is still listed: {recorded(sh['state'])}")
    quiet_log(log, "the restarted shared-mode instance")
    if editor_calls(sh["host_tex_log"]):
        fail(f"shared mode ran the host's TeX: {editor_calls(sh['host_tex_log'])}")
    if not linux:
        print("skip: LATEX_LIVE_PROXY_UID is not checked here (it needs Linux's /proc/net)", flush=True)
    ok("with an https public URL, cookies are Secure on its Host only"
       + ("; the identity header counts only from a peer with LATEX_LIVE_PROXY_UID" if linux else "")
       + "; stopped shared instances are no longer listed")


def quiet_log(path, what):
    """Check an instance's output, once it has stopped: no token (its log may be kept, and read by others), and
    no traceback (an error the server did not expect)."""
    with open(path, errors="replace") as f:
        printed = f.read()
    if prints_token(printed):
        fail(f"{what} printed a token")
    if "Traceback (most recent call last)" in printed:
        fail(f"{what} printed a traceback:\n{printed[printed.index('Traceback (most recent call last)'):][:2000]}")


def main():
    static_checks()
    unit_checks()
    with tempfile.TemporaryDirectory(prefix="latex-live-check-") as tmp:
        live_checks(tmp)
    print("all checks passed")



if __name__ == "__main__":
    main()
