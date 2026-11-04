#!/usr/bin/env python3
"""Smoke test for latex-live: python3 check.py (about a minute; needs TeX, as live.py does).

1. The Python compiles without warnings, and the viewer's script parses (with Node, if there is one).
2. A scratch paper in a temporary directory (two columns, lineno line numbers, an inline note, a
   git repository with a pushed commit and a local one) gets a live.py instance on a spare port:
   - the build publishes main.pdf, and the notes-free pass main-clean.pdf;
   - the viewer's endpoints answer, SyncTeX finds a source line, /instances lists the instance;
   - the HEAD and last-push baselines build (they differ: one commit is unpushed), and the
     notes-free baseline's line numbers match the live notes-free build's (two passes with lineno);
   - a second instance for the same document says it is already running; --replace takes over,
     and so does any new instance when the running one no longer answers;
   - stopping the instance removes it from /instances.
Exits non-zero on the first failure.
"""

import glob
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 44180
port = PORT  # the instance's (live_checks)

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


def fail(msg):
    for path in LOGS:
        with open(path, errors="replace") as f:
            print(f"--- {os.path.basename(path)} (last lines)\n" + "".join(f.readlines()[-25:]), flush=True)
    sys.exit(f"FAIL: {msg}")


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


def start(paper, *extra):
    LOGS.append(os.path.join(paper, "..", f"live-{len(LOGS)}.log"))
    log = open(LOGS[-1], "w")
    return subprocess.Popen([sys.executable, os.path.join(HERE, "live.py"), "main.tex", "--no-open", "--port", str(PORT), *extra],
                            cwd=paper, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)


def get(path, token):
    sep = "&" if "?" in path else "?"
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}{sep}t={token}", timeout=30) as r:
        body = r.read()
        return json.loads(body) if r.headers.get_content_type() == "application/json" else body.decode()


def ln_records(path):
    with open(path, errors="replace") as f:
        return [line for line in f if "\\@LN" in line]


def live_checks(tmp):
    paper = make_paper(tmp)
    proc = start(paper)
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

        with open(os.path.join(HERE, "token")) as f:
            token = f.read().strip()
        read_port(build)
        for path in ("/status/main.pdf?base=push", "/outline/main.pdf", "/notes/main.pdf", "/authors/main.pdf",
                     "/check/main.pdf", "/graphics/main.pdf", "/margin/main.pdf", "/floats/main.pdf", "/docs",
                     "/commits", "/papers", "/viewer.html"):
            try:
                get(path, token)
            except Exception as e:
                fail(f"{path}: {e}")
        ok("the viewer's endpoints answer")
        notes = get("/notes/main.pdf", token)
        if not any("hiding it moves" in n.get("text", "") for n in (notes if isinstance(notes, list) else notes.get("notes", []))):
            fail(f"/notes does not list the note: {str(notes)[:300]}")
        target = get("/forward?file=main.tex&line=9", token)
        if not target.get("page"):
            fail(f"SyncTeX found no target for main.tex:9: {target}")
        ok(f"SyncTeX: main.tex:9 is on page {target['page']}")
        listed = get("/instances", token)
        if not any(i["pid"] == proc.pid for i in listed["instances"]):
            fail(f"/instances does not list the instance: {listed}")
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

        second = subprocess.run([sys.executable, os.path.join(HERE, "live.py"), "main.tex", "--no-open", "--port", str(PORT)],
                                cwd=paper, capture_output=True, text=True, timeout=60)
        if "already running" not in second.stdout or second.returncode:
            fail(f"a second instance did not report the first:\n{second.stdout}{second.stderr}")
        ok("a second instance reports the running one and exits")
        replacement = start(paper, "--replace")
        try:
            proc.wait(30)
        except subprocess.TimeoutExpired:
            fail("--replace did not stop the first instance")
        proc = replacement
        wait_for("the replacement to answer", lambda: _answers(token, build))
        listed = get("/instances", token)["instances"]
        if [i["pid"] for i in listed if i["root"] == os.path.realpath(paper)] != [proc.pid]:
            fail(f"/instances after --replace: {listed}")
        ok("--replace takes over")
        os.kill(proc.pid, signal.SIGSTOP)  # (holds the lock, answers nothing)
        third = subprocess.Popen([sys.executable, os.path.join(HERE, "live.py"), "main.tex", "--no-open", "--port", str(PORT)],
                                 cwd=paper, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
        try:
            first_line = third.stdout.readline()
            proc.wait(30)
        except subprocess.TimeoutExpired:
            third.kill()
            fail("an instance that does not answer was not replaced")
        if "not answering" not in first_line:
            fail(f"replacing an instance that does not answer: {first_line}")
        proc = third
        wait_for("the instance that replaced the one not answering", lambda: _answers(token, build))
        ok("an instance that does not answer is replaced")
    finally:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(20)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
    if os.path.exists(os.path.join(HERE, "instances", f"{proc.pid}.json")):
        fail("the stopped instance is still listed")
    ok("a stopped instance is no longer listed")


def read_port(build):
    """The instance's port, from its lock file (the first free one from PORT)."""
    global port
    with open(os.path.join(build, "main-live.lock")) as f:
        port = json.load(f)["port"]


def _answers(token, build):
    read_port(build)
    try:
        return get("/docs", token)
    except OSError:
        return None


def main():
    static_checks()
    with tempfile.TemporaryDirectory(prefix="latex-live-check-") as tmp:
        live_checks(tmp)
    print("all checks passed")


if __name__ == "__main__":
    main()
