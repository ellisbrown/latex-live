"""The checks of the notes-free pass's warm standby (latex_report.py notes_free_pass, through latexlive/standby.py
run_tex: clean_standby_run), in a thread of their own beside the instance checks, with a paper, a state folder, a
port and an instance of their own."""

import gzip
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import time

from .base import CLEAN_PORT, ENV, HERE, _quiet, fail, ok, wait_for
from .helpers import (
    make_state, mtime_ns, pdf_pages_and_text, read_bytes, read_json_file, read_text, start, stop, texlive_image,
    write_text)
from .standby import standby_keepers, standby_left


# The paper of the notes-free standby's checks (clean_standby_run): notes (their macros in a file the preamble reads), a
# cross-reference, and a line that a preamble edit turns into line numbers (lineno: two notes-free runs).
CLEAN_PAPER = r"""\documentclass{article}
\usepackage{xcolor}
\usepackage{hyperref}
\input{macros}
% (line numbers go here)
\begin{document}
\section{One}\label{one}
\eb{A note long enough that hiding it moves the text below it, a note long enough that hiding it moves the text.}
See Section~\ref{one}.
\input{body}
\end{document}
"""
CLEAN_MACROS = r"""\newif\ifshowtodos \showtodostrue
\newcommand{\todotxt}[1]{\ifshowtodos\textcolor{red}{#1}\fi}
\newcommand{\eb}[1]{\todotxt{[EB: #1]}}
"""
# Spins TeX for #1 million rounds (about a third of a second each), the longer when notes are off, then fails: the
# notes-free pass is then still running from its standby when the live build's run ends (clean_standby_run). (The
# message keeps the error away from where the log shows the .aux being read: latex_report.py damaged_aux.)
CLEAN_SLOW = (r"\def\spin#1{\count255=0 \loop\advance\count255 1 \ifnum\count255<#1000000 \repeat}"
              r"\ifshowtodos\spin{10}\else\spin{300}\fi\typeout{" + "A message in the log. " * 20 + "}" r"\undefinedcontrolsequence" "\n")


def synctex_records(root):
    """The records of a notes-free pass's SyncTeX file (build/clean/main-clean.synctex.gz), each naming its input file
    rather than its number, and the input files: what forward and inverse search find in it. (A pass from a standby
    numbers the root file twice, as the copy of its preamble and as itself, so the numbers after it differ; and the
    byte offsets move with the paper folder's name.)"""
    data = gzip.decompress(read_bytes(os.path.join(root, "build", "clean", "main-clean.synctex.gz")))
    names, records = {}, []
    for line in data.replace(os.path.realpath(root).encode(), b"ROOT").splitlines():
        if line.startswith(b"Input:"):
            tag, _, path = line[6:].partition(b":")
            names[tag] = os.path.normpath(path)
        elif not line.startswith(b"!"):
            m = re.match(rb"([\[(a-z$])(\d+),(.*)", line)
            records.append(m[1] + names.get(m[2], b"?") + b"," + m[3] if m else line)
    return records, sorted(set(names.values()))


def clean_standby_run(tmp):
    """The notes-free pass's warm standby (latex_report.py notes_free_pass, through latexlive/standby.py run_tex), on
    a paper with notes and an instance of its own (with the live build's standby, as by default)."""
    paper, state = os.path.join(tmp, "clean-standby", "paper"), os.path.join(tmp, "clean-standby", "state")
    image = texlive_image()[1]
    if not image and not shutil.which("pdflatex"):
        return print("skip: no TeX Live image and no pdflatex, so the notes-free pass's standby is not checked", flush=True)
    extra = () if image else ("--host-tex",)
    words = "quokka narwhal axolotl pangolin tapir gecko walrus yak zebu ibex okapi lemur".split()
    body = "Aardvark one.\n\nNarwhal two, on the third line.\n\n" + "\n\n".join(
        " ".join(words[(i * 5 + j) % 12] for j in range(60)) for i in range(4)) + "\n"
    write_text(paper, "main.tex", CLEAN_PAPER)
    write_text(paper, "macros.tex", CLEAN_MACROS)
    write_text(paper, "body.tex", body)
    token = make_state(state)
    env = {**ENV, "LATEX_LIVE_STATE": state}
    build = os.path.join(paper, "build")
    records, folder = os.path.join(build, "standby", "main-clean.json"), os.path.join(build, "standby")
    status_file, clean_status = os.path.join(build, "main-live.status.json"), os.path.join(build, "clean", "main-clean.status.json")
    clean_pdf, outcomes = os.path.join(build, "main-clean.pdf"), os.path.join(build, "clean-early", "outcomes.json")
    procs = [start(paper, "main", *extra, env=env, port=CLEAN_PORT)]
    sys.path.insert(0, os.path.join(HERE, "latexlive"))
    import standby as standby_module  # (masked: a PDF's bytes without its dates and IDs)

    def passes():  # (how the notes-free pass's runs went: from a standby, verifying one, or normally)
        return _quiet(lambda: read_json_file(records).get("passes")) or {}

    def done():  # (the build and the notes-free pass after it have ended)
        return mtime_ns(clean_status) > mtime_ns(status_file) > mtime_ns(os.path.join(build, "main-live.building"))

    def save(name, text):
        """Write a source and wait for its build and notes-free pass: how that pass's runs went, {kind: count}."""
        before, built = passes(), mtime_ns(status_file)
        write_text(paper, name, text)
        wait_for(f"the build and notes-free pass after a change to {name}", lambda: mtime_ns(status_file) != built and done(),
                 timeout=180)
        return {k: n - before.get(k, 0) for k, n in passes().items() if n != before.get(k, 0)}

    def until_standby(what, text, runs):
        """Body edits until the notes-free pass's runs (runs of them) all come from standbys, at most four."""
        for i in range(4):
            ran = save("body.tex", text.replace("Aardvark one", f"Aardvark one ({what} {i})"))
            if ran == {"standby": runs}:
                return ran
        fail(f"no notes-free pass ran from the warm standby after {what}: {read_json_file(records)}")

    def matches_normal(what):
        """The notes-free PDF (but dates and IDs), its pages, text and SyncTeX are those of a normal notes-free pass
        (as without standbys) over a copy of the paper and its build."""
        copy = f"{paper}-normal-{time.time_ns()}"
        shutil.copytree(paper, copy, ignore=shutil.ignore_patterns("standby", "clean-early"))
        prefix = f"{os.path.join(HERE, 'tex-exec')} {texlive_image()[0]} {os.path.realpath(copy)}" if image else ""
        subprocess.run([sys.executable, "-c", "import latex_report as r; r.seed_notes_free('main-live', 'main'); r.notes_free_pass('main-live', 'main')"],
                       cwd=copy, env={**ENV, "PYTHONPATH": HERE, "LATEX_LIVE_EXEC": prefix, "max_print_line": "10000"}, timeout=300, check=True)
        theirs = os.path.join(copy, "build", "main-clean.pdf")
        if pdf_pages_and_text(clean_pdf) != pdf_pages_and_text(theirs):
            fail(f"{what}: the notes-free PDF from the standby has other pages or text than a normal notes-free pass's")
        if standby_module.masked(clean_pdf) != standby_module.masked(theirs):
            fail(f"{what}: the notes-free PDF from the standby differs from a normal notes-free pass's (but dates and IDs)")
        if synctex_records(paper) != synctex_records(copy):
            fail(f"{what}: the notes-free SyncTeX data from the standby differs from a normal notes-free pass's")
        shutil.rmtree(copy, ignore_errors=True)

    def lookup(path):  # (this preview's own port)
        with open(os.path.join(build, "main-live.lock")) as f:
            own = json.load(f)["port"]
        conn = http.client.HTTPConnection("127.0.0.1", own, timeout=30)
        conn.request("GET", path, headers={"Cookie": f"latex_live={token}"})
        data = conn.getresponse().read()
        conn.close()
        return json.loads(data)

    def taken_by_early():  # ((folder, pid) of a notes-free standby that an early pass has taken, or None)
        for name in os.listdir(folder) if os.path.isdir(folder) else ():
            who = _quiet(lambda: read_text(os.path.join(folder, name, "claimed")).strip()) or ""
            if name.startswith("main-clean-") and who.isdigit() and b"--early" in (_quiet(lambda: read_bytes(f"/proc/{who}/cmdline")) or b""):
                return name, int(who)
        return None

    def waiting():  # (notes-free standbys that wait: no pass has taken them)
        return [n for n in os.listdir(folder) if n.startswith("main-clean-") and not os.path.exists(os.path.join(folder, n, "claimed"))]

    try:
        wait_for("the first notes-free PDF of the standby's paper", lambda: os.path.isfile(clean_pdf) and done(), timeout=180)
        until_standby("start", body, 1)
        matches_normal("a body edit")
        box = lookup("/forward?file=body.tex&line=3&clean=1")
        page, x, y = box["page"], box["x"] + box["w"] / 2, box["y"] + box["h"] / 2
        found = lookup(f"/synctex?file=main.pdf&page={page}&x={x:.2f}&y={y:.2f}&clean=1&json=1")
        if (found.get("file"), found.get("line")) != ("body.tex", 3):
            fail(f"inverse search in the notes-free PDF from the standby, of a forward search of body.tex:3: {found}")
        ok("a body edit's notes-free pass runs from its warm standby: a normal one's PDF (but dates), pages, text and SyncTeX")

        ran = save("main.tex", CLEAN_PAPER.replace("% (line numbers go here)", "\\usepackage[switch]{lineno}\\linenumbers"))
        if ran.get("standby", 0) > 1 or not ran.get("verify"):  # (its second run may take a standby that its first verified)
            fail(f"a preamble edit's notes-free pass: runs {ran} (the first verifying a new standby)")
        until_standby("lineno", body, 2)
        matches_normal("both runs of a notes-free pass with line numbers")
        ok("a preamble edit's notes-free pass runs normally; then both runs of one with line numbers come from standbys, as normal")

        files = {n: read_bytes(os.path.join(build, "clean", n)) for n in os.listdir(os.path.join(build, "clean")) if n != "pid"}
        published, dropped = read_bytes(clean_pdf), (_quiet(lambda: read_json_file(outcomes)) or {}).get("dropped", 0)
        write_text(paper, "body.tex", body + CLEAN_SLOW)
        taken = wait_for("an early notes-free pass to take a standby", taken_by_early, timeout=60)
        wait_for("the early pass to be dropped", lambda: (_quiet(lambda: read_json_file(outcomes)) or {}).get("dropped", 0) > dropped, timeout=60)
        left = [what for what, there in (("its folder", os.path.isdir(os.path.join(folder, taken[0]))), ("the pass", os.path.exists(f"/proc/{taken[1]}")),
                ("its keeper", any(taken[0].encode() in (_quiet(lambda: read_bytes(f"/proc/{k}/cmdline")) or b"") for k in standby_keepers(paper))),
                ("its container", shutil.which("podman") and subprocess.run(["podman", "ps", "-aq", "--filter", f"label=latex-live.run={taken[0]}"],
                                                                            capture_output=True, text=True, timeout=60).stdout.strip())) if there]
        now = {n: read_bytes(os.path.join(build, "clean", n)) for n in os.listdir(os.path.join(build, "clean")) if n != "pid"}
        if left or now != files or read_bytes(clean_pdf) != published or len(waiting()) > 1:
            fail(f"an early pass stopped while it ran from a standby left {left}; build/clean/ as it was: {now == files}; "
                 f"standbys waiting: {waiting()}")
        ok("an early notes-free pass stopped while it runs from a standby leaves no keeper, container or folder, and build/clean/ as it was")
        save("body.tex", body)

        stop(procs[0])
        if standby_left(paper, "main-clean"):
            fail(f"the notes-free pass's standbys outlive live.py: {standby_left(paper, 'main-clean')}")
        before = passes()
        procs.append(start(paper, "main", *extra, env={**env, "LATEX_LIVE_STANDBY": "0"}, port=CLEAN_PORT))
        wait_for("the instance without standbys", lambda: _quiet(lambda: read_json_file(os.path.join(build, "main-live.lock"))["pid"]
                                                                 == procs[-1].pid) and done())
        save("body.tex", body.replace("Narwhal two", "Wombat two"))
        if "Wombat" not in pdf_pages_and_text(clean_pdf)[1] or passes() != before or standby_left(paper, "main-clean"):
            fail(f"the notes-free pass with LATEX_LIVE_STANDBY=0: runs {passes()} (before: {before}), left {standby_left(paper, 'main-clean')}")
        ok("the notes-free pass's standbys stop with live.py, and LATEX_LIVE_STANDBY=0 builds the notes-free PDF without one")
    finally:
        for proc in procs:
            stop(proc)
