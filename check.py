#!/usr/bin/env python3
"""Smoke test for latex-live: python3 check.py (about a minute; needs TeX, as live.py does).

1. The Python compiles without warnings (and with Python 3.9, if there is one), and the viewer's
   script parses (with Node, if there is one).
2. A scratch paper in a temporary directory (two columns, lineno line numbers, an inline note, a
   git repository with a pushed commit and a local one) gets a live.py instance, with the hub on
   port 44180 and state of its own (not the user's):
   - the build publishes main.pdf, and the notes-free pass main-clean.pdf;
   - the hub redirects an old bookmark, needs the token or its cookie, and forwards every
     endpoint (POST /rebuild too); SyncTeX finds a source line; /instances lists the instance;
   - the HEAD and last-push baselines build (they differ: one commit is unpushed), and the
     notes-free baseline's line numbers match the live notes-free build's (two passes with lineno);
   - a second document runs in its own process behind the same port, and /papers links both;
   - a second instance for the same document says it is already running; --replace takes over,
     and so does any new instance when the running one no longer answers; the hub moves to
     another instance on the same port, and a hub that no longer answers is replaced;
   - stopping the instance removes it from /instances.
Exits non-zero on the first failure.
"""

import glob
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
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 44180  # the hub's; the previews' own ports follow

PAPER = r"""\documentclass[twocolumn]{article}
\usepackage{xcolor}
\usepackage[switch]{lineno}
\linenumbers
\newif\ifshowtodos \showtodostrue
\newcommand{\todotxt}[1]{\ifshowtodos\textcolor{red}{#1}\fi}
\newcommand{\eb}[1]{\todotxt{[EB: #1]}}
\begin{document}
\section{Introduction}
\eb{A note long enough that hiding it moves the text below it. A note long enough that hiding it
moves the text below it. A note long enough that hiding it moves the text below it.}
\input{body}
\section{Method}\label{sec:method}
See \ref{sec:method}.
\end{document}
"""


LOGS = []  # the instances' output, shown on failure
ENV = dict(os.environ)  # for the instances: their own token and instance list (live_checks)


def redact(text):
    """Never print the token: as a URL parameter, a cookie, or itself."""
    text = re.sub(r"(t=|latex_live=)[A-Za-z0-9_-]{8,}", r"\1REDACTED", str(text))
    return text.replace(TOKEN[0], "REDACTED") if TOKEN[0] else text


def fail(msg):
    for path in LOGS:
        with open(path, errors="replace") as f:
            print(f"--- {os.path.basename(path)} (last lines)\n{redact(''.join(f.readlines()[-25:]))}", flush=True)
    sys.exit(f"FAIL: {redact(msg)}")


def ok(msg):
    print(f"ok: {msg}", flush=True)


def wait_for(what, test, timeout=180):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if result := test():
            return result
        time.sleep(1)
    fail(f"timed out waiting for {what}")


def static_checks():
    for name in ("live.py", "latex_report.py", "forward.py", "check.py"):
        r = subprocess.run([sys.executable, "-W", "error", "-m", "py_compile", os.path.join(HERE, name)],
                           capture_output=True, text=True)
        if r.returncode:
            fail(f"{name} does not compile:\n{r.stderr}")
    ok("the Python compiles")
    oldest = shutil.which("python3.9")  # (the oldest supported, as macOS ships)
    for name in ("live.py", "latex_report.py", "forward.py", "check.py") if oldest else ():
        r = subprocess.run([oldest, "-c", "import sys; compile(open(sys.argv[1]).read(), sys.argv[1], 'exec')",
                            os.path.join(HERE, name)], capture_output=True, text=True)
        if r.returncode:
            fail(f"{name} does not compile with Python 3.9:\n{r.stderr}")
    if oldest:
        ok("it compiles with Python 3.9 too")
    node = shutil.which("node")
    if not node:
        return print("skip: no node, so the viewer's script is not checked", flush=True)
    with open(os.path.join(HERE, "viewer.html")) as f:
        scripts = re.findall(r'<script(?: type="module")?>(.*?)</script>', f.read(), re.S)
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False) as f:
        f.write("\n".join(scripts))
    r = subprocess.run([node, "--check", f.name], capture_output=True, text=True)
    os.remove(f.name)
    if r.returncode:
        fail(f"the viewer's script does not parse:\n{r.stderr}")
    ok("the viewer's script parses")


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=check", "-c", "user.email=check@example.com", *args], cwd=cwd, check=True,
                   capture_output=True)


def make_paper(tmp):
    subprocess.run(["git", "init", "-q", "--bare", os.path.join(tmp, "remote.git")], check=True)
    paper = os.path.join(tmp, "paper")
    os.makedirs(paper)
    with open(os.path.join(paper, "main.tex"), "w") as f:
        f.write(PAPER)
    words = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu".split()
    with open(os.path.join(paper, "body.tex"), "w") as f:
        f.write("\n\n".join(" ".join(words[(i * 7 + j) % 12] for j in range(90)) for i in range(24)) + "\n")
    git(paper, "init", "-q", "-b", "main")
    git(paper, "add", "-A")
    git(paper, "commit", "-qm", "first")
    git(paper, "remote", "add", "origin", os.path.join(tmp, "remote.git"))
    git(paper, "push", "-q", "-u", "origin", "main")
    with open(os.path.join(paper, "body.tex"), "a") as f:
        f.write("\nomega omega omega.\n")
    git(paper, "commit", "-qam", "unpushed")
    return paper


def start(paper, doc="main", *extra):
    LOGS.append(os.path.join(paper, "..", f"live-{len(LOGS)}.log"))
    log = open(LOGS[-1], "w")
    return subprocess.Popen([sys.executable, os.path.join(HERE, "live.py"), f"{doc}.tex", "--no-open", "--port", str(PORT), *extra],
                            cwd=paper, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, env=ENV)


def request(path, token=None, method="GET", body=None, cookie=None):
    """(status, headers, body) of a request to the hub, without following redirects."""
    conn = http.client.HTTPConnection("127.0.0.1", hub["port"], timeout=30)
    if token:
        path += ("&" if "?" in path else "?") + f"t={token}"
    headers = {"Cookie": f"latex_live={cookie}"} if cookie else {}
    if body is not None:
        headers["Content-Type"] = "application/json"
    conn.request(method, path, json.dumps(body) if body is not None else None, headers)
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r.status, dict(r.getheaders()), data


def get(path, token, doc="main"):
    """A preview's endpoint, through the hub."""
    status, headers, data = request(f"{hub['base']}{doc}{path}", token)
    if status != 200:
        raise OSError(f"{status} {data[:200]!r}")
    kind = headers.get("Content-Type", "")
    return json.loads(data) if kind.startswith("application/json") else data.decode() if kind.startswith("text/") else data


def read_hub():
    """The hub's port and pid (HUB_FILE), if it answers."""
    try:
        with open(os.path.join(ENV["LATEX_LIVE_STATE"], "hub.json")) as f:
            hub.update(json.load(f))
        return request("/hub/ping", TOKEN[0])[0] == 200
    except (OSError, ValueError, KeyError):
        return False


hub, TOKEN = {}, [None]


def ln_records(path):
    with open(path, errors="replace") as f:
        return [line for line in f if "\\@LN" in line]


def stop(proc):
    try:
        os.killpg(proc.pid, signal.SIGCONT)
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(20)
    except ProcessLookupError:
        pass
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)


def live_checks(tmp):
    ENV["LATEX_LIVE_STATE"] = state = os.path.join(tmp, "state")  # (not the user's: their previews keep their list)
    paper = make_paper(tmp)
    for name in ("appendix", "extra"):
        with open(os.path.join(paper, f"{name}.tex"), "w") as f:
            f.write(f"\\documentclass{{article}}\n\\begin{{document}}\nThe {name}.\n\\end{{document}}\n")
    procs = [start(paper)]
    try:
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
        ok("the hub refuses HEAD and oversized POST bodies")

        for path in ("/status/main.pdf?base=push", "/outline/main.pdf", "/notes/main.pdf", "/authors/main.pdf",
                     "/check/main.pdf", "/graphics/main.pdf", "/margin/main.pdf", "/floats/main.pdf", "/docs",
                     "/commits", "/papers", "/viewer.html", "/pdfjs/build/pdf.min.mjs", "/pdf/main.pdf"):
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
        status, _, data = request(f"{hub['base']}main/rebuild", method="POST", body={}, cookie=token)
        if status != 202:
            fail(f"POST /rebuild through the hub: {status} {data[:200]!r}")
        ok("POST /rebuild works through the hub")

        procs.append(start(paper, "appendix"))  # (a second document of the same paper, in its own process)
        wait_for("appendix.pdf through the hub", lambda: _quiet(lambda: get("/status/appendix.pdf", token, "appendix")["version"]))
        routed = get("/instances", token, "appendix")["self"], get("/docs", token, "appendix")["doc"]
        if routed != (procs[1].pid, "appendix"):
            fail(f"/p/.../appendix/ reaches process {routed[0]} building {routed[1]}, not the appendix's {procs[1].pid}")
        page = request("/papers", token)[2].decode()
        if f"{hub['base']}main/viewer.html" not in page or f"{hub['base']}appendix/viewer.html" not in page:
            fail(f"/papers does not link both documents through the hub:\n{page[:600]}")
        ok("a second document gets its own process, behind the same port")

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
        with open(LOGS[-1]) as f:
            if "serves the hub but is not answering" not in f.read():
                fail("the hub that did not answer was stopped some other way")
        wait_for("the hub to come back", lambda: read_hub() and hub["pid"] != stuck, timeout=20)
        if hub["port"] != PORT:
            fail(f"the hub came back on port {hub['port']}")
        ok("a hub that does not answer is replaced")
    finally:
        for proc in procs:
            stop(proc)
    left = os.listdir(os.path.join(state, "instances"))
    if left:
        fail(f"stopped instances are still listed: {left}")
    ok("stopped instances are no longer listed")


def _quiet(test):
    try:
        return test()
    except (OSError, ValueError, KeyError, TypeError):
        return None


def main():
    static_checks()
    with tempfile.TemporaryDirectory(prefix="latex-live-check-") as tmp:
        live_checks(tmp)
    print("all checks passed")


if __name__ == "__main__":
    main()
