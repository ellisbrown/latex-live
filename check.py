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

import http.client
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import zlib

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))  # (checks/ next to this file, wherever it is run from)
from checks.base import ENV, HERE, PORT, TOKEN, _quiet, fail, hub, in_thread, ok, wait_for  # noqa: E402
from checks.clean_standby import clean_standby_run  # noqa: E402
from checks.early import early_run  # noqa: E402
from checks.helpers import (  # noqa: E402
    CAPS, JS_TYPE_RE, editor_calls, editor_stub, get, hub_get, post_length, read_bytes, read_hub, request, start, stop,
    texlive_image, text_point)
from checks.papers import composed, make_paper  # noqa: E402
from checks.shared import shared_checks  # noqa: E402
from checks.shared_setup import shared_setup  # noqa: E402
from checks.standby import standby_left, standby_run  # noqa: E402
from checks.static import static_checks  # noqa: E402
from checks.units import unit_checks  # noqa: E402


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


def main():
    static_checks()
    unit_checks()
    with tempfile.TemporaryDirectory(prefix="latex-live-check-") as tmp:
        live_checks(tmp)
    print("all checks passed")



if __name__ == "__main__":
    main()
