"""The early notes-free pass's checks (latex_report.py run_early: early_run), in a thread of their own beside the
instance checks, with a paper, a state folder, a port and an instance of their own, without the warm standby."""

import gzip
import os
import shutil
import subprocess
import sys

from .base import EARLY_PORT, ENV, HERE, _quiet, fail, ok, wait_for
from .helpers import (
    make_state, mtime_ns, pdf_pages_and_text, read_bytes, read_json_file, start, stop, texlive_image, write_text)
from .papers import NOTE_PARAGRAPHS, PAPER, PEOPLE


def early_run(tmp):
    """The early notes-free pass (latex_report.py run_early), on a paper with notes and line numbers (two notes-free
    TeX runs), with an instance of its own, without the warm standby (each pass then rewrites the files the early
    pass starts from): a body edit's early pass is kept, and its PDF and SyncTeX are what the notes-free pass after
    the run makes; an edit that adds a reference keeps the early pass of its second pass; a failed run drops it and
    puts build/clean/ back as it was; the next good one is kept again."""
    paper, state = os.path.join(tmp, "early", "paper"), os.path.join(tmp, "early", "state")
    image = texlive_image()[1]
    if not image and not shutil.which("pdflatex"):
        return print("skip: no TeX Live image and no pdflatex, so the early notes-free pass is not checked", flush=True)
    write_text(paper, "main.tex", PAPER)
    write_text(paper, "people.tex", PEOPLE)
    words = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu".split()
    body = NOTE_PARAGRAPHS + "\n\n".join(" ".join(words[(i * 5 + j) % 12] for j in range(80)) for i in range(6)) + "\n"
    write_text(paper, "body.tex", body)
    make_state(state)
    env = {**ENV, "LATEX_LIVE_STATE": state, "LATEX_LIVE_STANDBY": "0"}
    build = os.path.join(paper, "build")
    outcomes, clean_pdf = os.path.join(build, "clean-early", "outcomes.json"), os.path.join(build, "main-clean.pdf")
    status_file = os.path.join(build, "main-live.status.json")
    proc = start(paper, "main", *(() if image else ("--host-tex",)), env=env, port=EARLY_PORT)

    def counts():
        return _quiet(lambda: read_json_file(outcomes)) or {}

    def clean_files():
        found = {}
        for folder, _, names in os.walk(os.path.join(build, "clean")):
            for name in names:
                if name != "pid":
                    found[os.path.relpath(os.path.join(folder, name), build)] = read_bytes(os.path.join(folder, name))
        return found

    def save(text, how):
        """Write body.tex and wait until its build and notes-free pass are done, the early one counted as how."""
        before, built = counts(), mtime_ns(status_file)
        write_text(paper, "body.tex", text)
        wait_for(f"the early pass to be {how}", lambda: counts().get(how, 0) > before.get(how, 0) and mtime_ns(status_file) != built,
                 timeout=180)
        return read_json_file(status_file)

    try:
        wait_for("the first notes-free pass", lambda: os.path.isfile(clean_pdf) and os.path.isfile(status_file), timeout=180)
        published = mtime_ns(clean_pdf)
        body1 = body.replace("alpha beta", "alpha tapir beta", 1)
        status = save(body1, "kept")
        wait_for("the kept early pass's PDF", lambda: mtime_ns(clean_pdf) != published, timeout=120)
        if not status.get("ok") or "tapir" not in pdf_pages_and_text(clean_pdf)[1]:
            fail(f"the PDF of a kept early pass: status {status}, text {pdf_pages_and_text(clean_pdf)[1][:200]!r}")
        copy = f"{paper}-normal"
        shutil.copytree(paper, copy)
        prefix = f"{os.path.join(HERE, 'tex-exec')} {texlive_image()[0]} {os.path.realpath(copy)}" if image else ""
        subprocess.run([sys.executable, "-c", "import latex_report as r; r.seed_notes_free('main-live', 'main'); r.notes_free_pass('main-live', 'main')"],
                       cwd=copy, env={**ENV, "PYTHONPATH": HERE, "LATEX_LIVE_EXEC": prefix, "max_print_line": "10000"}, timeout=300, check=True)
        sys.path.insert(0, os.path.join(HERE, "latexlive"))
        import standby as standby_module  # (masked: a PDF's bytes without its dates and IDs)
        synctex = lambda root: [line for line in gzip.decompress(read_bytes(os.path.join(root, "build", "clean", "main-clean.synctex.gz")))
                                .replace(os.path.realpath(root).encode(), b"ROOT").splitlines()
                                if not line.startswith(b"!")]  # (byte offsets, which the paper folder's name moves)
        if standby_module.masked(clean_pdf) != standby_module.masked(os.path.join(copy, "build", "main-clean.pdf")):
            fail("a kept early pass made another PDF than the notes-free pass after the run")
        if synctex(paper) != synctex(copy):
            fail("a kept early pass wrote other SyncTeX data than the notes-free pass after the run")
        ok("a body edit's early notes-free pass is kept: its PDF (but dates) and SyncTeX are the notes-free pass's after the run")

        body2 = body1.replace("alpha tapir", "alpha tapir \\label{early}see \\ref{early} and", 1)
        status = save(body2, "kept")
        resolved = lambda: "see 1 and" in " ".join(pdf_pages_and_text(clean_pdf)[1].split())
        if not status.get("ok") or not wait_for("the notes-free PDF of an edit that adds a reference", resolved, timeout=60):
            fail(f"an edit that adds a reference, early pass kept: {status}")
        ok("an edit that adds a reference keeps the early pass of its second pass, and the reference resolves")

        wait_for("its result", lambda: mtime_ns(os.path.join(build, "clean", "main-clean.status.json")) > mtime_ns(clean_pdf), timeout=30)
        files, published = clean_files(), read_bytes(clean_pdf)
        status = save(body2.replace("tapir", "tapir \\undefinedcontrolsequence", 1), "dropped")
        if status.get("ok") or clean_files() != files or read_bytes(clean_pdf) != published:
            fail(f"a failed run: status {status}, build/clean/ put back: {clean_files() == files}")
        ok("a failed run drops its early pass and puts build/clean/ back as it was, keeping the notes-free PDF")
        status = save(body2.replace("tapir", "okapi", 1), "kept")
        wait_for("the notes-free PDF after the fix", lambda: "okapi" in pdf_pages_and_text(clean_pdf)[1], timeout=60)
        ok("the next good run keeps its early pass again")
    finally:
        stop(proc)
