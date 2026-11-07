"""The instance checks (live_checks): a live.py instance on the scratch paper, behind the hub, with the threads that
run beside it (the warm standbys, the early notes-free pass, shared mode); the hub, the endpoints, SyncTeX, the
baselines, a rebuild, the downloads (review_checks, arxiv_checks), the owner's own preview (owner_checks), switching documents
(switch_checks), a second document, a second instance, --replace, and instances and hubs that no longer answer."""

import http.client
import io
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tarfile
import threading
import urllib.parse
import zlib

from latexlive.navigation import source_words  # (check.py puts the checkout on the path)

from .access import BRIDGE, EXTRA, access_checks
from .base import ENV, HERE, PORT, TOKEN, _quiet, fail, hub, in_thread, ok, wait_for
from .clean_standby import clean_standby_run
from .early import early_run
from .helpers import (
    CAPS, JS_TYPE_RE, editor_calls, editor_stub, get, hub_get, post, post_length, read_bytes, read_hub, request, start, stop,
    read_text, texlive_image, text_point)
from .papers import composed, make_paper
from .shared import shared_checks
from .shared_setup import shared_setup
from .standby import standby_left, standby_run


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
    os.makedirs(state, mode=0o700)  # (a token file that others can read: the instance makes it private, access_checks)
    fd = os.open(os.path.join(state, "token"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(secrets.token_urlsafe(16))
    os.chmod(os.path.join(state, "token"), 0o644)
    named = {**ENV, "LATEX_LIVE_HOST": BRIDGE, "LATEX_LIVE_ALLOWED_HOSTS": EXTRA}  # (the hub's and its preview's other names)
    procs, sharing = [start(paper, env=named)], []  # (sharing: the shared-mode instance, shared_setup)
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
        owner_checks(token, own, editor_log, paper)
        access_checks(token, own, state, procs[0].log)

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
            post("/export", token)), timeout=240)
        if exported["state"] != "done":
            fail(f"the build without notes failed: {exported.get('msg')}")
        pdf = get("/pdf/main-export.pdf", token)
        log = open(os.path.join(build, "export", "main", "main-export.log"), errors="replace").read()
        if not pdf.startswith(b"%PDF") or re.search(r"Rerun to get|may have changed\. Rerun", log):
            fail("the PDF without notes is missing, or its references had not settled")
        if post("/export", token).get("version") != exported["version"]:
            fail("asking again rebuilt the PDF without notes, though the document had not changed")
        status, _, data = request(f"{hub['base']}main/export", cookie=token)  # (as a viewer from before asked)
        if status != 405 or "reload the page" not in _quiet(lambda: json.loads(data)["error"]):
            fail(f"GET /export through the hub: {status} {data[:200]!r}, not 405 with a message for the viewer")
        if ln_records(os.path.join(build, "export", "main", "main-export.aux")) != ln_records(os.path.join(build, "clean", "main-clean.aux")):
            fail("the download without notes has its line numbers in other places than the notes-free view")
        ok("the download without notes is an exact build (line numbers too), made once per build; a GET of it is 405")
        review_checks(build, token)
        arxiv_checks(build, token)
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
                                                                          else None)(post("/review", token, "appendix"))), timeout=120)
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
        if post("/switch", token, body={"doc": "extra"}).get("doc") != "extra":
            fail("switching main's preview to extra failed")
        if wait_for("main's old route", lambda: _quiet(lambda: get("/docs", token)["doc"]), timeout=20) != "extra":
            fail("after a switch, the old document's viewers no longer reach their preview")
        if post("/switch", token, "extra", {"doc": "main"}).get("doc") != "main":
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


def pdf_comments(pdf):
    """The text annotations (sticky notes) of a PDF that pdfTeX wrote, in its objects or object streams: [(their
    /T, their /Contents)], each as text (pdfTeX writes what comments_tex gives it: UTF-16BE in hex)."""
    chunks = [pdf]
    for m in re.finditer(rb"stream\r?\n", pdf):
        try:
            chunks.append(zlib.decompress(pdf[m.end():pdf.find(b"endstream", m.end())]))
        except zlib.error:
            continue
    text = lambda hexes: bytes.fromhex(re.sub(rb"\s", b"", hexes).decode()).decode("utf-16")
    return [(text(m.group(1)), text(m.group(2))) for chunk in chunks
            for m in re.finditer(rb"/Subtype\s*/Text\b.*?/T\s*<([0-9A-Fa-f\s]*)>\s*/Contents\s*<([0-9A-Fa-f\s]*)>", chunk, re.S)]


def arxiv_checks(build, token):
    """The arXiv package of the scratch paper: the files its build reads, without their comments and notes, with
    the notes switched off; its own build matches the export; made once per build; the arXiv check reads it."""
    made = wait_for("the arXiv package", lambda: (lambda e: e if e.get("state") in ("done", "failed") else None)(
        post("/arxiv", token)), timeout=240)
    if made["state"] != "done":
        fail(f"the arXiv package failed: {made.get('msg')}")
    with tarfile.open(fileobj=io.BytesIO(get(f"/arxiv.tar.gz?v={made['version']}", token))) as t:
        files = {m.name: t.extractfile(m).read().decode() for m in t if m.isfile()}
    hidden = ("A note long enough", "a note in a footnote", "a resolved note", "In the review copy's probe pass")
    if sorted(files) != ["body.tex", "main.tex", "people.tex"] or any(h in text for h in hidden for text in files.values()) \
            or "\\eb{}" not in files["main.tex"] or "\\renewcommand\\todotxt[1]{}" not in files["main.tex"].split("\\documentclass")[0]:
        fail(f"the arXiv package holds {sorted(files)}, with its notes or comments, or without the notes switched off:\n"
             f"{files.get('main.tex', '')[:600]}")
    if not made["same"] or not made["comments"] or not made["notes"]:
        fail(f"the arXiv package's own build differs from the export, or it took nothing out: {made}")
    if post("/arxiv", token).get("version") != made["version"]:
        fail("asking again remade the arXiv package, though the document had not changed")
    status, _, data = request(f"{hub['base']}main/arxiv", cookie=token)
    if status != 405:
        fail(f"GET /arxiv through the hub: {status} {data[:200]!r}, not 405")
    arxiv_check = lambda: next((c for c in get("/check/main.pdf", token) if c["title"] == "arXiv source"), {})
    source = wait_for("the arXiv check of the package (the checks are kept for 10 s)",
                      lambda: (lambda c: c if c.get("detail", "").startswith("main-arxiv.tar.gz") else None)(arxiv_check()), timeout=20)
    if not source["detail"].startswith("main-arxiv.tar.gz, 3 files") or any("note" in i["text"] for i in source["items"]):
        fail(f"the arXiv check doesn't read the package the viewer made, or finds notes in it: {source}")
    ok(f"the arXiv package: the 3 files the build reads, {made['comments']} comments and {made['notes']} notes taken out, the notes"
       " switched off; its own build matches the export; made once per build; the arXiv check reads it")


def review_checks(build, token):
    """The review copy of the scratch paper: the export's pages on wider ones, every note beside its
    column, numbered and apart, without the protected marker or the resolved note. (Two of its notes
    are there for shared mode's checks, at the start of body.tex: NOTE_PARAGRAPHS; outside shared mode,
    "as written" is a note like any other.)"""
    reviewed = wait_for("the review copy", lambda: (lambda e: e if e.get("state") in ("done", "failed") else None)(
        post("/review", token)), timeout=240)
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
    annotated = get("/pdf/main-annotated.pdf", token)
    comments = pdf_comments(annotated)
    if written_pages(os.path.join(build, "review", "main", ".copy", "main-annotated.log")) != pages or \
            drawn_pages(annotated, 0) < pages or sorted(text for _, text in comments) != sorted(texts) or \
            {author for author, _ in comments} != {n["author"] for n in plan["notes"]}:
        fail(f"the copy with the notes as comments should place the {pages} pages at their own size, with each note as"
             f" a comment of its author's: {written_pages(os.path.join(build, 'review', 'main', '.copy', 'main-annotated.log'))}"
             f" pages, {drawn_pages(annotated, 0)} placed, comments {comments}")
    if reviewed.get("version") != post("/review", token).get("version"):
        fail("asking again rebuilt the review copy, though the document had not changed")
    status, _, data = request(f"{hub['base']}main/review", cookie=token)  # (as a viewer from before asked)
    if status != 405 or "reload the page" not in _quiet(lambda: json.loads(data)["error"]):
        fail(f"GET /review through the hub: {status} {data[:200]!r}, not 405 with a message for the viewer")
    ok(f"the review copy: {pages} pages as exported, unscaled on wider ones, with {len(texts)} notes in both gutters, apart"
       " (the protected marker, the resolved note, and the one in a branch that is off left out); its last pass loads no"
       " package that the paper's TeX wrote; beside it, the same pages at their own size with the notes as PDF comments")


def owner_checks(token, own, editor_log, paper):
    """The owner's own preview (not shared): GET me, the editor's modules, SyncTeX as JSON, the editor
    stand-in through POST /open and plain /synctex (GET /open is 405, as in shared mode), and the POST
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

    completions = hub_get("/completions", token)
    if {"label": "sec:method", "file": "main.tex", "line": 13} not in completions.get("labels", []):
        fail(f"/completions does not offer main.tex's label sec:method for \\ref: {completions}")
    page, x, y = text_point(lambda path: hub_get(path, token), "main.tex", 9)
    spot = f"file=main.pdf&page={page}&x={x:.2f}&y={y:.2f}"
    line = hub_get(f"/synctex?{spot}&json=1", token)["line"]
    key, column = source_words(read_text(os.path.join(paper, "main.tex")).split("\n")[line - 1])[0]  # (a word of the line)
    word = f"word={urllib.parse.quote(key.upper())}&occurrence=0&count=1"
    if (found := hub_get(f"/synctex?{spot}&{word}&json=1", token)).get("column") != column:
        fail(f"synctex with json=1 and the double-clicked word {key!r} (at {column} in main.tex:{line}): {found}")
    if editor_calls(editor_log):
        fail(f"synctex with json=1 opened the editor: {editor_calls(editor_log)}")
    status, _, data = request(f"{hub['base']}main/open?file=main.tex&line=9", cookie=token)
    if status != 405:
        fail(f"GET /open (not shared): {status} {data[:200]!r}, not 405")
    for path, method, body in ((f"{hub['base']}main/open", "POST", {"file": "main.tex", "line": 10}),
                               (f"{hub['base']}main/open", "POST", {"file": "main.tex", "line": 11, "column": 4}),
                               (f"{hub['base']}main/synctex?{spot}", "GET", None),
                               (f"{hub['base']}main/synctex?{spot}&{word}", "GET", None)):
        status, _, data = request(path, method=method, body=body, cookie=token)
        if status != 200:
            fail(f"{method} {path} (not shared): {status} {data[:200]!r}")
    calls = wait_for("the editor stand-in, four times", lambda: len(editor_calls(editor_log)) >= 4 and editor_calls(editor_log),
                     timeout=10, step=0.2)
    if (len(calls) != 4 or not all(c.startswith("-g ") and re.search(r"/main\.tex:\d+(:\d+)?$", c) for c in calls)
            or sorted(c.rpartition("/")[2] for c in calls) != sorted(["main.tex:10", "main.tex:11:5", f"main.tex:{line}",
                                                                      f"main.tex:{line}:{column + 1}"])):
        fail(f"the editor stand-in got {calls}, not -g .../main.tex:10, main.tex:11:5, and the double-clicked line"
             f" ({line}) without and with the double-clicked word's column ({column + 1})")
    ok("SyncTeX answers JSON without opening the editor, with the column of a double-clicked word; POST /open and plain"
       " /synctex open it, at that column with one, and GET /open is 405")

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
    """Outside shared mode, through the hub: POST /switch (as the viewer sends it) builds another document, and back;
    GET /switch (as older viewers sent it) is 405; POST /diff builds a latexdiff against HEAD, and GET /diff is 405."""
    status, _, data = request(f"{hub['base']}main/switch", method="POST", body={"doc": "appendix"}, cookie=token)
    if status != 200 or _quiet(lambda: json.loads(data)["doc"]) != "appendix":
        fail(f"POST /switch {{doc: appendix}} through the hub: {status} {data[:200]!r}")
    wait_for("appendix.pdf, after POST /switch", lambda: _quiet(lambda: get("/status/appendix.pdf", token, "appendix")["version"]))
    status, _, data = request(f"{hub['base']}appendix/switch?doc=main", token)
    if status != 405 or hub_get("/docs", token)["doc"] != "appendix":
        fail(f"GET /switch?doc=main through the hub: {status} {data[:200]!r}, not 405 (it switches only by POST)")
    status, _, data = request(f"{hub['base']}appendix/switch", method="POST", body={"doc": "main"}, cookie=token)
    if status != 200 or _quiet(lambda: json.loads(data)["doc"]) != "main" or hub_get("/docs", token)["doc"] != "main":
        fail(f"POST /switch {{doc: main}} through the hub: {status} {data[:200]!r}")
    ok("POST /switch builds another document, and back; GET /switch is 405 (outside shared mode, through the hub)")
    if not texlive_image()[1] and not shutil.which("latexdiff"):
        return print("skip: no TeX Live image and no latexdiff here, so POST /diff is not checked", flush=True)

    def diffed():
        diff = hub_get("/status/main-diff.pdf", token).get("diff") or {}
        if diff.get("state") == "failed":
            fail(f"the latexdiff against HEAD failed: {diff.get('msg')}")
        return diff.get("state") == "done"

    status, _, data = request(f"{hub['base']}main/diff?rev=HEAD", cookie=token)
    if status != 405:
        fail(f"GET /diff?rev=HEAD through the hub: {status} {data[:200]!r}, not 405 (it builds only by POST)")
    status, _, data = request(f"{hub['base']}main/diff", method="POST", body={"rev": "HEAD"}, cookie=token)
    if status != 200 or _quiet(lambda: json.loads(data)["state"]) != "running":
        fail(f"POST /diff through the hub: {status} {data[:200]!r}")
    wait_for("the latexdiff that POST /diff started", diffed, timeout=120, step=0.2)
    status, headers, _ = request(f"{hub['base']}main/pdf/main-diff.pdf", token)
    if status != 200 or headers.get("Content-Type") != "application/pdf":
        fail(f"/pdf/main-diff.pdf through the hub: {status} {headers.get('Content-Type')}")
    ok("POST /diff builds a latexdiff against HEAD, and GET /diff is 405 (outside shared mode, through the hub)")
