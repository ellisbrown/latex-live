"""The warm standby's checks (latexlive/standby.py: standby_run), in a thread of their own beside the instance
checks, with a paper, a state folder, a port and an instance of their own; and what a document's warm standbys
leave behind (standby_left), which the other checks look for too."""

import http.client
import json
import os
import re
import shutil
import signal
import subprocess
import time

from .base import ENV, HERE, STANDBY_PORT, _quiet, fail, ok, wait_for
from .helpers import (
    make_state, mtime_ns, pdf_pages_and_text, read_json_file, start, stop, texlive_image, text_point, write_text)


# The paper of the warm standby's checks (standby_run): its preamble reads macros.tex and typesets a box, which
# the body uses (SyncTeX then maps it to the preamble's line 4).
STANDBY_PAPER = r"""\documentclass{article}
\usepackage{hyperref}
\input{macros}
\newsavebox\stamp\sbox\stamp{\textbf{Boxed in the preamble}}
\begin{document}
\section{One}\label{one}
\usebox\stamp. \macro. See Section~\ref{one}.
\input{body}
\end{document}
"""
# The body's first line puts the error below (standby_run) far enough from where the log shows the .aux being read
# that latex_report.py doesn't take it for a cut-off .aux.
STANDBY_BODY = ("\\typeout{" + "A message in the log. " * 20 + "}\n"
                "Aardvark one.\n\nPlatypus two, on the fourth line.\n\nCapybara three.\n")


def standby_keepers(paper):
    """The pids of the warm standbys' keepers working in paper (on Linux, from /proc; elsewhere none are found)."""
    found = []
    for pid in filter(str.isdigit, os.listdir("/proc") if os.path.isdir("/proc") else []):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                argv = f.read().split(b"\0")
            if b"--keep" in argv and any(a.endswith(b"standby.py") for a in argv) and \
                    os.path.realpath(f"/proc/{pid}/cwd") == os.path.realpath(paper):
                found.append(int(pid))
        except OSError:
            pass
    return found


def standby_containers(paper, job="main-live"):
    """The ids of the containers of a document's warm standbys (none without podman)."""
    if not shutil.which("podman"):
        return []
    from latexlive.standby import label_of
    return subprocess.run(["podman", "ps", "-aq", "--filter", f"label=latex-live.standby={label_of(paper, 'build', job)}"],
                          capture_output=True, text=True, timeout=60).stdout.split()


def standby_left(paper, job="main-live"):
    """What a document's warm standbys left: keepers, containers and folders (none once they are all gone)."""
    folder = os.path.join(paper, "build", "standby")
    folders = [n for n in os.listdir(folder) if re.fullmatch(re.escape(job) + r"-[0-9a-f]+-[0-9]+", n)] if os.path.isdir(folder) else []
    return standby_keepers(paper) + standby_containers(paper, job) + folders


def standby_normal_pass(paper, image):
    """(Page count, text) of a normal pdflatex pass over a copy of the paper, with its build folder's job files."""
    copy = f"{paper}-normal-{time.time_ns()}"
    shutil.copytree(paper, copy, ignore=shutil.ignore_patterns("standby", "*.pdf"))
    with open(os.path.join(copy, "build", "main-live.log"), errors="replace") as f:
        source = next(line[2:] for line in f.read().splitlines() if line.startswith("**"))  # (the pass's own: $pre_tex_code)
    prefix = [os.path.join(HERE, "tex-exec"), texlive_image()[0], os.path.realpath(copy)] if image else []
    subprocess.run([*prefix, "pdflatex", "-synctex=1", "-file-line-error", "-halt-on-error", "-interaction=batchmode", "-recorder",
                    "-jobname=main-live", "-output-directory=build", source], cwd=copy, capture_output=True, timeout=120, env=ENV)
    return pdf_pages_and_text(os.path.join(copy, "build", "main-live.pdf"))


def standby_run(tmp):
    paper, state = os.path.join(tmp, "standby", "paper"), os.path.join(tmp, "standby", "state")
    image = texlive_image()[1]
    if not image and not shutil.which("pdflatex"):
        return print("skip: no TeX Live image and no pdflatex, so the warm standby is not checked", flush=True)
    extra = () if image else ("--host-tex",)  # (a standby never runs the host's TeX in place of the image)
    write_text(paper, "main.tex", STANDBY_PAPER)
    write_text(paper, "macros.tex", "\\newcommand\\macro{Macro version one}\n")
    write_text(paper, "body.tex", STANDBY_BODY)
    token = make_state(state)
    env = {**ENV, "LATEX_LIVE_STATE": state, "LATEX_LIVE_TIMING": "1"}  # (and each build's timing line: timing_check)
    build = os.path.join(paper, "build")
    status_file, records = os.path.join(build, "main-live.status.json"), os.path.join(build, "standby", "main-live.json")
    procs = [start(paper, "main", *extra, env=env, port=STANDBY_PORT)]

    def passes():  # (how many passes ran from a standby, verified one, or ran normally)
        return _quiet(lambda: read_json_file(records).get("passes")) or {}

    def idle():
        return mtime_ns(status_file) > mtime_ns(os.path.join(build, "main-live.building"))

    def waiting():  # (a standby whose TeX run has started, which no pass has taken)
        folder = os.path.join(build, "standby")
        return any(os.path.isfile(os.path.join(folder, n, "keeper.json")) and not os.path.exists(os.path.join(folder, n, "claimed"))
                   for n in (os.listdir(folder) if os.path.isdir(folder) else ()) if n.startswith("main-live-"))

    def save(name, text, ready=True):
        """Write a source and wait for its build: (how its passes ran, {kind: count}; its status)."""
        if ready:
            wait_for("a waiting standby", waiting, timeout=60)
        before, built = passes(), mtime_ns(status_file)
        write_text(paper, name, text)
        wait_for(f"the build after a change to {name}", lambda: mtime_ns(status_file) != built and idle(), timeout=120)
        after = passes()
        return {k: n - before.get(k, 0) for k, n in after.items() if n != before.get(k, 0)}, read_json_file(status_file)

    def text():
        return pdf_pages_and_text(os.path.join(paper, "main.pdf"))[1]

    def until_standby(what, body, tries=4):
        """Body edits until one runs from the standby, at most tries; its passes."""
        for i in range(tries):
            ran, _ = save("body.tex", body.replace("Capybara three", f"Capybara three ({what} {i})"))
            if ran.get("standby"):
                return ran
        fail(f"no pass ran from the warm standby after {what}: {read_json_file(records)}")

    try:
        wait_for("main.pdf of the warm standby's paper", lambda: os.path.isfile(os.path.join(paper, "main.pdf")) and idle())
        until_standby("start", STANDBY_BODY)
        with open(os.path.join(build, "main-live.lock")) as f:
            own = json.load(f)["port"]
        if standby_normal_pass(paper, image) != pdf_pages_and_text(os.path.join(paper, "main.pdf")):
            fail("a pass from the warm standby made a PDF with other pages or text than a normal pass")
        ok("a body edit runs from the warm standby, and its PDF has a normal pass's pages and text")

        body = STANDBY_BODY + "See Section~\\ref{two}.\n\n\\section{Two}\\label{two}\nKoala four.\n"
        ran, _ = save("body.tex", body)
        if ran.get("standby", 0) < 2 or set(ran) != {"standby"} or "See Section 2." not in text():
            fail(f"an edit that adds a reference: passes {ran}, text {text()[:300]!r}")
        ok("both passes of an edit that adds a reference run from the warm standby, and the reference resolves")
        timing_check(procs[0].log, own, token)

        ran, _ = save("main.tex", STANDBY_PAPER.replace("Boxed in the preamble", "Boxed again"))
        if ran.get("standby") or not ran.get("verify") or "Boxed again" not in text():
            fail(f"a preamble edit: passes {ran}, text {text()[:200]!r}")
        until_standby("preamble", body, tries=2)
        ok("a preamble edit runs normally (and verifies a new standby beside it), and the next edits use the standby")

        ran, _ = save("macros.tex", "\\newcommand\\macro{Macro version two}\n")
        if ran.get("standby") or not ran.get("verify") or "Macro version two" not in text():
            fail(f"a change to a file the preamble reads: passes {ran}, text {text()[:200]!r}")
        until_standby("macros", body, tries=2)
        ok("a change to a file the preamble reads runs normally (and verifies a new standby), and the next edits use it")

        ran, status = save("body.tex", body.replace("Platypus two", "Platypus two \\undefinedcontrolsequence"))
        errors = status.get("errors") or [{}]
        if ran != {"standby": 1} or status.get("ok") or (errors[0].get("file"), errors[0].get("line")) != ("body.tex", 4):
            fail(f"an error in the body, from the warm standby: passes {ran}, status {status}")
        ran, status = save("body.tex", body, ready=False)
        if not status.get("ok"):
            fail(f"the build once the error was fixed: {status}")
        ok("an error in the body, from the warm standby, is reported at body.tex:4, as from a normal pass")

        def lookup(path):  # (this preview's own port)
            conn = http.client.HTTPConnection("127.0.0.1", own, timeout=30)
            conn.request("GET", path, headers={"Cookie": f"latex_live={token}"})
            data = conn.getresponse().read()
            conn.close()
            return json.loads(data)

        until_standby("synctex", body)
        text_point(lookup, "body.tex", 4)
        words = subprocess.run(["pdftotext", "-bbox", os.path.join(paper, "main.pdf"), "-"], capture_output=True, text=True).stdout
        m = re.search(r'xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">Boxed<', words)
        found = m and lookup(f"/synctex?file=main.pdf&page=1&x={(float(m[1]) + float(m[3])) / 2:.2f}"
                             f"&y={(float(m[2]) + float(m[4])) / 2:.2f}&json=1")
        if not found or (found.get("file"), found.get("line")) != ("main.tex", 4):
            fail(f"inverse search on a box that the preamble made, after a pass from the warm standby: {found}")
        ok("SyncTeX after a pass from the warm standby: forward and inverse search, also of a box the preamble made")

        os.kill(procs[0].pid, signal.SIGKILL)
        procs[0].wait()  # (as its parent would)
        wait_for("the standbys to stop once live.py is killed (kill -9)",
                 lambda: not standby_keepers(paper) and not standby_containers(paper), timeout=30)
        procs.append(start(paper, "main", *extra, env=env, port=STANDBY_PORT))
        wait_for("the restarted instance", lambda: _quiet(lambda: read_json_file(os.path.join(build, "main-live.lock"))["pid"]
                                                          == procs[-1].pid) and idle())
        save("body.tex", body + "% Restarted.\n", ready=False)  # (its latexmk found nothing to do: a pass starts a standby)
        wait_for("a standby of the restarted instance", waiting, timeout=60)
        stop(procs[-1])
        left = standby_left(paper)
        if left:
            fail(f"the warm standbys left keepers, containers or folders behind: {left}")
        ok("no standby keeper, container or folder outlives live.py, killed (kill -9) or stopped")

        before = passes()
        procs.append(start(paper, "main", *extra, env={**env, "LATEX_LIVE_STANDBY": "0"}, port=STANDBY_PORT))
        wait_for("the instance without standbys", lambda: _quiet(lambda: read_json_file(os.path.join(build, "main-live.lock"))["pid"]
                                                                 == procs[-1].pid) and idle())
        ran, status = save("body.tex", body.replace("Koala", "Wombat"), ready=False)
        if not status.get("ok") or "Wombat" not in text() or passes() != before or standby_left(paper):
            fail(f"with LATEX_LIVE_STANDBY=0: status {status}, passes {passes()} (before: {before}), left {standby_left(paper)}")
        ok("LATEX_LIVE_STANDBY=0 builds without standbys")
    finally:
        for proc in procs:
            stop(proc)


def timing_check(log, port, token):
    """With LATEX_LIVE_TIMING=1, each build prints a line of its phases, counted from the save (here the last
    one: two passes from the warm standby), and /status gives the server's clock for the viewer's own part."""
    def lines():
        with open(log, errors="replace") as f:
            return [l for l in f.read().splitlines() if l.startswith("=== latex-live timing: main (ms after the save of body.tex): ")]
    end = time.monotonic() + 10  # (the line comes a moment after the build: its last event is noted after the status)
    while not (line := next((l for l in lines()[::-1] if l.count(", pass ") == 2 and "released" in l), None)) and time.monotonic() < end:
        time.sleep(0.1)
    if not line:
        fail(f"no timing line of a build of two passes; the last: {(lines() or [None])[-1]}")
    events = re.findall(r"([a-z-]+(?:\([^)]*\))?) (-?\d+)(?:,|$)", line.split("): ", 1)[1])
    names = [name for name, _ in events]
    want = ["run", "pass", "standby(verified)", "released", "tex-end", "moved", "report", "published", "pass-end", "analyzed"]
    if names[:len(want)] != want or names.count("published") != 2 or names[-2:] != ["status", "run-end"] \
            or [int(ms) for _, ms in events] != sorted(int(ms) for _, ms in events) or int(events[0][1]) < 0:
        fail(f"the timing line of a build of two passes from the warm standby: {line}")
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    conn.request("GET", "/status/main.pdf", headers={"Cookie": f"latex_live={token}"})
    now = json.loads(conn.getresponse().read()).get("timing", {}).get("now")
    conn.close()
    if not isinstance(now, float) or abs(now - time.time()) > 5:
        fail(f"/status with LATEX_LIVE_TIMING=1 gives no server clock: {now}")
    ok("LATEX_LIVE_TIMING=1 prints each build's phases from the save (two passes from the warm standby), and /status the clock")
