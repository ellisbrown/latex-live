"""The unit checks: the server's parts on their own, without an instance (made-up input, stand-ins for TeX), in the
order unit_checks runs them: the review copy's layout and matching, shared mode's (shared_units), the downloads'
bounds, failures and waits, the bibliography's SyncTeX lookups, tool timeouts, the warm standby's snapshot, the
timing lines, the restart prompt, the static files the server sends, the bibliography (bib_units), the Files tab
(files_units), the History tab (history_units) and the agent hook (agent_units)."""

import contextlib
import glob
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from .base import HERE, fail, ok, wait_for
from .helpers import fake_server, git, git_out, patched, put, put_bytes, read_bytes, tex_stand_in
from .static import link_modules
from .shared_units import baseline_failure_checks, build_link_checks, confinement_checks, shared_build_checks
from .bib_units import bib_checks
from .files_units import files_checks
from .history_units import history_checks
from .agent_units import agent_checks


def unit_checks():
    """The checks of the server's parts on their own, without an instance (made-up input, stand-ins for TeX)."""
    for check in (layout_checks, matching_checks, confinement_checks, build_link_checks, shared_build_checks, bounds_checks, failed_export_checks,
                  bib_span_checks, waiting_download_checks, tool_timeout_checks, standby_snapshot_checks, timing_checks,
                  code_updated_checks, static_file_checks, source_swap_checks, forward_cache_checks, forward_script_checks, link_checks, completion_checks, assets_checks, search_checks, logs_checks, presence_checks, word_column_checks, venue_checks, diff_retry_checks,
                  arxiv_strip_checks, graphics_synctex_checks, turned_page_checks, shown_synctex_checks, baseline_failure_checks, connection_cap_checks,
                  header_deadline_checks, in_flight_checks, note_limit_checks, bib_checks, files_checks, history_checks, agent_checks):
        check()


def timing_checks():
    """LATEX_LIVE_TIMING=1's lines (timing.Runs): a notes-free pass's events count only in the line of the run that
    started or kept it, so that a pass a newer run superseded neither adds to the newer run's line nor ends it before
    the newer run's own pass does; the lines don't show the passes' names."""
    from latexlive import timing
    runs, lines = timing.Runs(), []
    feed = lambda *events: lines.extend(runs.feed([(float(i), name, detail) for i, name, detail in events]))
    feed((0, "run", ""), (1, "pass", ""), (2, "clean-start", "a"), (3, "run-end", ""))
    feed((4, "run", ""), (5, "clean-tex", "a"), (6, "pass", ""), (7, "clean-published", "a"), (8, "clean-start", "b"),
         (9, "run-end", ""))
    early = len(lines)
    feed((10, "clean-tex", "b"), (11, "clean-published", "b"))
    feed((12, "run", ""), (13, "clean-early", "seed-1"), (14, "pass", ""), (15, "clean-kept", "seed-1"), (16, "run-end", ""),
         (17, "clean-tex", "seed-1"), (18, "clean-published", "seed-1"))
    names = [[name for _, name, _ in run] for run in lines]
    if early != 1 or names != [["run", "pass", "clean-start", "run-end"],
                               ["run", "pass", "clean-start", "run-end", "clean-tex", "clean-published"],
                               ["run", "clean-early", "pass", "clean-kept", "run-end", "clean-tex", "clean-published"]]:
        fail(f"the timing lines take a superseded notes-free pass's events, or end early: {names} (lines before the second"
             f" run's pass ended: {early})")
    if "(" in timing.line("main", lines[1]).split("): ", 1)[1]:
        fail(f"a timing line shows the notes-free pass's name: {timing.line('main', lines[1])}")
    ok("timing lines leave out a superseded notes-free pass's events, which no longer end the next run's line early")


def code_updated_checks():
    """The viewer asks for a restart when the code on disk changes (status.code_updated): every module the
    server imports is watched, and a module changed, added or removed counts, but a file only touched or
    changed back does not."""
    from latexlive import config, status
    __import__("latexlive.main")  # (and with it every module the server imports)
    watched = set(status.server_files())
    for name, module in sorted(sys.modules.items()):
        if (name == "latexlive" or name.startswith("latexlive.")) and os.path.realpath(module.__file__) not in watched:
            fail(f"code_updated does not watch {module.__file__}")
    for path in glob.glob(os.path.join(HERE, "latexlive", "*.py")):  # (and the scripts beside it that they import, maybe late)
        with open(path) as f:
            for name in re.findall(r"^\s*(?:import|from)\s+(\w+)", f.read(), re.M):
                script = os.path.join(HERE, f"{name}.py")
                if os.path.isfile(script) and script not in watched:
                    fail(f"code_updated does not watch {name}.py, which {os.path.relpath(path, HERE)} imports")
    with tempfile.TemporaryDirectory(prefix="latex-live-check-") as tmp, patched(config, HERE=tmp):
        for name in ("live.py", "VERSION", "latexlive/a.py", "latexlive/b.py"):
            put(os.path.join(tmp, name), f"{name}\n")
        started = {}
        b, c = os.path.join(tmp, "latexlive", "b.py"), os.path.join(tmp, "latexlive", "c.py")

        def updated(what, expected, stamps=iter(range(10, 100))):
            if os.path.exists(b):
                os.utime(b, ns=(next(stamps),) * 2)  # (each step a new time, however coarse the file system's)
            if status.code_updated(started) != expected:
                fail(f"code_updated after {what}: {not expected}")

        updated("starting", False)
        updated("touching a module", False)
        put(b, "changed\n")
        updated("changing a module", True)
        put(b, "latexlive/b.py\n")
        updated("changing it back", False)
        put(c, "new\n")
        updated("adding a module", True)
        os.remove(c)
        updated("removing it again", False)
        os.remove(b)
        updated("removing a module", True)
    ok("a restart is asked for when a module of the server changes, is added or is removed")


def static_file_checks():
    """The server sends the viewer's own files from the checkout (handler.static_file): viewer.html and the
    files in pdfjs/, codemirror/ and viewer/, never a folder, a file elsewhere in the checkout, a path through
    .., or a link out of the file's folder (a link within it is followed)."""
    from latexlive import config, handler
    with tempfile.TemporaryDirectory(prefix="latex-live-check-") as tmp, patched(config, HERE=tmp):
        for name in ("viewer.html", "live.py", "viewer/main.js", "viewer/viewer.css", "pdfjs/build/pdf.min.mjs", "codemirror/view.js"):
            put(os.path.join(tmp, name), "x\n")
        os.symlink(os.path.join(tmp, "live.py"), os.path.join(tmp, "viewer", "out.js"))
        os.symlink(os.path.join(tmp, "viewer", "main.js"), os.path.join(tmp, "viewer", "in.js"))
        os.symlink(os.path.join(tmp, "pdfjs"), os.path.join(tmp, "viewer", "pdfjs"))
        served = ("/viewer.html", "/viewer/main.js", "/viewer/viewer.css", "/viewer/in.js", "/pdfjs/build/pdf.min.mjs",
                  "/codemirror/view.js", "/viewer/nope.js")  # (a missing file: SimpleHTTPRequestHandler answers 404)
        refused = ("/live.py", "/viewer/", "/viewer", "/viewer/../live.py", "/viewer/out.js", "/viewer/pdfjs/build/pdf.min.mjs",
                   "/viewer/\0.js", "/viewer.html/", "/latexlive/config.py", "/pdfjsx/a.js")
        wrong = [p for p in served if not handler.static_file(p)] + [p for p in refused if handler.static_file(p)]
        if wrong:
            fail(f"static_file is wrong about {wrong}")
    ok("the server sends only the viewer's own files from the checkout, never through .. or a link out of their folder")


def bounds_checks():
    """The downloads read TeX's output within bounds (config.OUTPUT_LIMIT and config.SYNCTEX_LIMIT, made smaller here),
    without TeX: a log, an .aux or a .bbl past its bound (files.read_output), the probe pass's log (review.run_probe,
    with a stand-in for TeX) and a SyncTeX file uncompressed (review_anchors.synctex_pages) stop a download with a message
    that says so, before they are read whole; within them, the probe's records are read as before, and so is a log
    whose wrapped lines split a UTF-8 character."""
    from latexlive import config, files, review, review_anchors, review_layout
    with tempfile.TemporaryDirectory(prefix="latex-live-check-") as tmp, patched(config, OUTPUT_LIMIT=1 << 20, SYNCTEX_LIMIT=1 << 20):
        root = os.path.join(tmp, "paper")
        out = os.path.join(root, "build", "review", "main")
        os.makedirs(out)
        aux = os.path.join(out, "main-export.aux")
        put(aux, "\\relax\n" * 1000)
        if files.read_output(root, aux) != "\\relax\n" * 1000 or files.read_output(root, aux + ".gone") is not None:
            fail("files.read_output does not read a file within its bound as it is, or has a file that is gone")
        put_bytes(aux, b"Overfull \\hbox in caf\xc3\n\xa9\n")  # (TeX wraps its log at 79 bytes, splitting a UTF-8 character)
        try:
            if "Overfull" not in (files.read_output(root, aux) or ""):
                raise ValueError("nothing read")
        except ValueError as e:
            fail(f"files.read_output does not read a log whose wrapping split a UTF-8 character: {e}")
        put(aux, "\\relax\n" * 200_000)
        try:
            files.read_output(root, aux)
            fail("files.read_output read a file past its bound")
        except RuntimeError as e:
            if "build/review/main/main-export.aux is over 1 MB" not in str(e):
                fail(f"a file past its bound: {e}")

        stand_in = os.path.join(tmp, "tex")
        os.makedirs(stand_in)
        server = fake_server(root, tex_exec=tex_stand_in(stand_in))
        geo = review_layout.review_geometry({"W": 612, "H": 792, "columns": 1})
        notes = [{"id": 1, "macro": "eb", "body": "Check this", "call": "\\eb{Check this}"}]
        records = ("latex-live|pos|./body.tex|7|1|4000000|50000000|[EB: Check this]\n"
                   "latex-live|snippet|1|0|2|100.0pt|10.0pt|2.0pt\nlatex-live|snippet|1|1|3|90.0pt|8.0pt|1.0pt\n"
                   "latex-live|pages|1\n")
        put(os.path.join(stand_in, "log"), f"This is pdfTeX\n{'Overfull hbox in a paragraph' * 3}\n{records}"
                                           "Output written on main-export.pdf (3 pages, 1234 bytes).\n")
        put(os.path.join(stand_in, "pdf"), "%PDF-1.5\n")
        probe = review.run_probe(server, out, "main-export", "main", notes, geo)
        if (probe.get("pages"), [r[:3] for r in probe.get("pos", [])], sorted(probe.get("snippets", {}).get(1, {})),
                probe.get("pdf")) != (1, [["body.tex", 7, 1]], [0, 1], True) or not os.path.isfile(os.path.join(out, "probe.pdf")):
            fail(f"the probe's records, read from its log line by line, are not as written: {probe}")
        put(os.path.join(stand_in, "log"), "x" * 99 + "\n" + ("latex-live|seen|./body.tex|7|[EB: Check this]\n" * 30_000) + records)
        try:
            review.run_probe(server, out, "main-export", "main", notes, geo)
            fail("the probe's log was read past its bound")
        except RuntimeError as e:
            if "build/review/main/main-export.log is over 1 MB" not in str(e):
                fail(f"the probe's log past its bound: {e}")

        synctex = os.path.join(tmp, "export.synctex.gz")
        head = ("SyncTeX Version:1\nInput:1:/paper/body.tex\nContent:\n{1\n[1,7:4736286,4736286:30000000,40000000,0\n"
                "(1,7:4736286,9000000:30000000,655360,0\n")
        with gzip.open(synctex, "wt") as f:
            f.write(head + "h1,7:4736286,9000000:100000\n" * 10 + ")\n]\n}1\n")
        if list(review_anchors.synctex_pages(synctex)) != [1]:
            fail(f"review_anchors.synctex_pages does not read a small SyncTeX file: {review_anchors.synctex_pages(synctex)}")
        with gzip.open(synctex, "wt") as f:  # (a few KB compressed)
            f.write(head + "h1,7:4736286,9000000:100000\n" * 50_000 + ")\n]\n}1\n")
        try:
            review_anchors.synctex_pages(synctex)
            fail("a SyncTeX file was read past its bound")
        except RuntimeError as e:
            if "export.synctex.gz is over 1 MB uncompressed" not in str(e):
                fail(f"a SyncTeX file past its bound: {e}")
    ok("the downloads read TeX's logs, .aux, .bbl and SyncTeX files only up to a bound, past which they stop and say so;"
       " the probe's records are read from its log line by line; a log whose wrapping split a UTF-8 character reads")


SYNCTEX_STUB = """#!/bin/sh
# A stand-in for synctex view -i LINE:0:FILE -o PDF: a box for lines 1 and 7 of FILE, none for the others.
echo "$3" >> "$(dirname "$0")/calls"
line=${3%%:*}
echo "This is SyncTeX command line utility, version 1.5"
echo "SyncTeX result begin"
case $line in
  1) printf 'Output:%s\\nPage:3\\nx:1\\ny:2\\nh:10.5\\nv:200.0\\nW:50\\nH:12.0\\nd:3\\n' "$5" ;;
  7) printf 'Output:%s\\nPage:5\\nx:1\\ny:2\\nh:72.0\\nv:400.5\\nW:50\\nH:10.0\\nd:3\\n' "$5" ;;
esac
echo "SyncTeX result end"
"""


def bib_span_checks():
    """Where the bibliography starts and ends (bib_span, with a stand-in for synctex): its SyncTeX lookups run
    batched (run_synctexes), each with the records it gets alone (run_synctex), on the same lines as before; without wait, bib_span
    answers PENDING at once while it looks up in a thread, then what a lookup that waits gives."""
    from latexlive import synctex
    tmp = tempfile.mkdtemp(prefix="latex-live-check-")
    path = os.environ.get("PATH", "")
    try:
        os.makedirs(os.path.join(tmp, "bin"))
        put(os.path.join(tmp, "bin", "synctex"), SYNCTEX_STUB)
        os.chmod(os.path.join(tmp, "bin", "synctex"), 0o755)
        os.environ["PATH"] = f"{os.path.join(tmp, 'bin')}{os.pathsep}{path}"
        files = [os.path.join(tmp, name) for name in ("main.synctex.gz", "my refs.bbl", "main.pdf")]
        put(files[0], "")
        put(files[1], "x\n" * 9)  # (10 lines: 1, then 10 down to 3 are looked up)
        runs = [("view", "-i", f"{line}:0:{files[1]}", "-o", files[2]) for line in (1, 2, 7)]
        alone = [synctex.run_synctex(tmp, *args) for args in runs]
        if synctex.run_synctexes(tmp, runs) != alone or [len(r) for r in alone] != [1, 0, 1] or alone[0][0].get("Page") != "3":
            fail(f"SyncTeX lookups in one run differ from each alone: {synctex.run_synctexes(tmp, runs)} vs {alone}")
        want = ({"page": 3, "x": 10.5, "y": 188.0}, {"page": 5, "x": 72.0, "y": 390.5})
        os.remove(os.path.join(tmp, "bin", "calls"))
        if synctex.bib_span(tmp, files, cache={}) != want:
            fail(f"bib_span with the stand-in: {synctex.bib_span(tmp, files, cache={})}")
        with open(os.path.join(tmp, "bin", "calls")) as f:
            if f.read().splitlines() != [f"{n}:0:{files[1]}" for n in (1, *range(10, 2, -1))]:
                fail("bib_span looks up other lines than the first and the last eight")
        cache = {}
        if synctex.bib_span(tmp, files, cache=cache, wait=False) is not synctex.PENDING:
            fail("bib_span without wait does not answer PENDING while it looks up")
        found = wait_for("bib_span's lookup in a thread", lambda: (lambda r: r if r is not synctex.PENDING else None)(
            synctex.bib_span(tmp, files, cache=cache, wait=False)), timeout=20)
        if found != want:
            fail(f"bib_span without wait, once looked up: {found}")
        ok("bib_span looks up the bibliography's extent in batched SyncTeX runs (each lookup as alone), and without wait answers"
           " PENDING at once, then the result")
    finally:
        os.environ["PATH"] = path
        shutil.rmtree(tmp, ignore_errors=True)


def failed_export_checks():
    """An export that failed fails again at once for the same build of the document (export.exact_export, with a
    stand-in for TeX), as the review copy asks for it after /export did, and so does one that timed out; the
    next build is exported again."""
    from latexlive import config, export
    tmp = tempfile.mkdtemp(prefix="latex-live-check-")
    try:
        root = os.path.join(tmp, "paper")
        os.makedirs(os.path.join(root, config.BUILD))
        published = os.path.join(root, "main.pdf")
        put(published, "%PDF-1.5\n")
        stand_in = os.path.join(tmp, "tex")
        os.makedirs(stand_in)
        put(os.path.join(stand_in, "log"), "This is pdfTeX\n./main.tex:3: Undefined control sequence.\n")
        put(os.path.join(stand_in, "exit"), "1")
        server = fake_server(root, tex_exec=tex_stand_in(stand_in))

        def runs(what, error="./main.tex:3: Undefined control sequence."):
            try:
                export.exact_export(server, "main.pdf")
                fail(f"{what} went ahead, though TeX failed")
            except RuntimeError as e:
                if not str(e).startswith(error):
                    fail(f"{what} failed with: {e}")
            with open(os.path.join(stand_in, "calls")) as f:
                return len(f.read().splitlines())

        def new_build():
            st = os.stat(published)
            os.utime(published, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

        if (runs("the export"), runs("the export again, for the same build")) != (1, 1):
            fail("an export that failed was built again for the same build of the document")
        new_build()
        if runs("the export of a new build") != 2:
            fail("an export that failed was not built again for a new build of the document")
        new_build()

        def too_long(*args, **kwargs):  # (run_tool's answer for a run it stopped at its deadline)
            with open(os.path.join(stand_in, "calls"), "a") as f:
                f.write("timed out\n")
            return None, ""

        with patched(export, run_tool=too_long):
            if (runs("an export that times out", "a pass took over 10 minutes"),
                    runs("it again", "a pass took over 10 minutes")) != (3, 3):
                fail("an export that timed out was built again for the same build of the document")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    ok("an export that failed or timed out fails again at once for the same build (no second build for the review"
       " copy), and is built again for the next one")


def waiting_download_checks():
    """/export and /review wait while the document builds (export.start_export, review.start_review): a pass may publish
    the PDF before the run ends, and a download made then would lack the run's last pass; once the run ends, they
    start (a build of theirs that runs is reported as it is). With --no-build, a stale marker holds nothing up."""
    from latexlive import config, export, files, review
    tmp = tempfile.mkdtemp(prefix="latex-live-check-")
    try:
        root = os.path.join(tmp, "paper")
        os.makedirs(os.path.join(root, config.BUILD))
        put(os.path.join(root, "main.pdf"), "%PDF-1.5\n")
        status, building = files.job_file(root, "main.pdf", ".status.json"), files.job_file(root, "main.pdf", ".building")
        put(status, "{}")
        put(building, "")
        os.utime(building, (time.time() + 5, time.time() + 5))  # (a run that started after the last one ended)
        server = fake_server(root, no_build=True)
        if export.waiting_for_build(server, "main.pdf", {}) is not None:
            fail("with --no-build, the downloads wait for a build that a stopped one left marked as running")
        server.no_build = False
        for name, start in (("/export", export.start_export), ("/review", review.start_review)):
            code, state = start(server)
            if (code, state.get("state"), state.get("step")) != (200, "running", "waiting for the build in progress") or server.export or server.review:
                fail(f"{name} during a build: {code} {state}, {server.export or server.review}")
        server.export = {"pdf": "main.pdf", "state": "running", "step": "its own"}
        if export.start_export(server)[1].get("step") != "its own":
            fail("/export during a build did not report its own build that runs")
        os.utime(status, (time.time() + 10, time.time() + 10))  # (the run ended)
        if export.waiting_for_build(server, "main.pdf", {}) is not None:
            fail("/export still waits after the build ended")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    ok("the downloads wait while the document builds, and start once the run ends (with --no-build, at once)")


def layout_checks():
    """The review copy's gutters (review_layout.place_notes, review_layout.review_layout), on made-up notes, and the notes'
    text, with the paper's own macros spelled out, in the copy with the notes as comments (comments_tex)."""
    import random
    from latexlive import review_layout
    rng = random.Random(7)
    for _ in range(200):
        n = rng.randint(1, 12)
        heights = [rng.uniform(5, 60) for _ in range(n)]
        want = sorted(rng.uniform(0, 800) for _ in range(n))
        if sum(heights) + 5 * (n - 1) > 700:
            continue
        tops = review_layout.place_notes(want, heights, 40, 740, 5)
        if any(tops[i] + heights[i] + 5 > tops[i + 1] + 1e-6 for i in range(n - 1)) or tops[0] < 40 - 1e-6 \
                or tops[-1] + heights[-1] > 740 + 1e-6:
            fail(f"place_notes overlaps or leaves the gutter: {want} {heights} -> {tops}")
    geo = review_layout.review_geometry({"W": 612, "H": 792, "columns": 2})
    notes = [{"id": i, "page": 1 + i // 40, "x": 100 + 300 * (i % 2), "y": 60 + (i * 17) % 700, "author": "EB", "body": "x",
              "macro": "eb", "file": "a.tex", "line": i, "how": "exact"} for i in range(80)]
    sizes = {i: {0: [100 + 2 * i, 150, 6, 40], 1: [101 + 2 * i, 150, 5, 30]} for i in range(80)}
    notes[0]["body"] = "compare with \\method"  # (a macro of the paper's, which the comments' text spells out)
    plan = review_layout.review_layout(notes, {"pdf": True, "snippets": sizes}, geo, 2, {"method": "BaselineNet"})
    if sorted(n["id"] for n in plan["notes"]) != list(range(80)) or [n["n"] for n in plan["notes"]] != list(range(1, 81)):
        fail("review_layout lost or renumbered notes")
    first = next(n for n in plan["notes"] if n["id"] == 0)
    if first["text"] != "compare with BaselineNet" or review_layout.pdf_text(first["text"]).replace("\n", "") not in \
            review_layout.comments_tex(plan, {}, "20261107000000").replace("\n", ""):
        fail(f"a note's text for the comments, with the paper's own macro: {first['text']!r}")
    gutters = {}
    for n in plan["notes"]:
        gutters.setdefault((n["page"], n["side"]), []).append(n)
    for (page, side), group in gutters.items():
        placed = [n for n in group if n["top"] is not None]
        if [n["n"] for n in placed] != sorted(n["n"] for n in placed) or any(n["continued"] for n in placed):
            fail(f"page {page}, {side} gutter: notes out of order")
        for a, b in zip(placed, placed[1:]):
            if a["top"] + a["sizes"][a["size"]]["h"] > b["top"] + 1e-6:
                fail(f"page {page}, {side} gutter: notes {a['n']} and {b['n']} overlap")
        if any(n["top"] is None and not n["continued"] for n in group) or not all(n["size"] == 1 for n in group):
            fail(f"page {page}, {side} gutter: crowded notes not set smaller and continued")
    if plan["pages"] <= plan["paper"]:
        fail("crowded notes did not continue on pages after the paper")
    ok("the review copy's gutters keep notes in order and apart, set crowded ones smaller, then continue them")


def matching_checks():
    """The review copy's matching of the probe's records to notes (review_anchors.match_records): two authors'
    notes with the same text, on one line, each get their own record; a protected marker (no note, but
    recorded as its macro typesets it) whose text holds a note's, in another file or below the note in
    its own, gives that note no record, while a note whose typeset text no record has (an unusual macro)
    still gets the record that holds its text."""
    from latexlive import review_anchors
    templates = {"eb": r"{\color{Emerald}[\textbf{EB:} #1]}", "ab": r"{\color{Blue}[\textbf{AB:} #1]}"}
    notes = [{"id": i, "macro": macro, "file": "a.tex", "line": 5, "last": 5, "body": "Check this",
              "typeset": review_anchors.squeeze(templates[macro].replace("#1", "Check this"))} for i, macro in ((1, "eb"), (2, "ab"))]
    records = [("pos", "a.tex", 5, 1, 100.0, 200.0, r"{\color {Emerald}[\textbf {EB:} Check this]}"),
               ("pos", "a.tex", 5, 1, 300.0, 200.0, r"{\color {Blue}[\textbf {AB:} Check this]}")]
    matched = {k: [r[4] for r in v] for k, v in review_anchors.match_records(notes, records).items()}
    if matched != {1: [100.0], 2: [300.0]}:
        fail(f"two authors' notes with the same text got each other's records: {matched}")
    note = {"id": 3, "macro": "eb", "file": "b.tex", "line": 43, "last": 44, "body": "human-written",
            "typeset": review_anchors.squeeze(templates["eb"].replace("#1", "human-written"))}
    own = ("pos", "b.tex", 44, 8, 415.0, 207.0, r"{\color {Emerald}[\textbf {EB:} human-written]}")
    for where in ("a.tex", "b.tex"):  # (the marker's record comes first, as its page does)
        marker = ("pos", where, 213, 5, 170.0, 222.0, r"{\color {Cyan}{[\textbf {CH:} human-written]}}")
        matched = {k: [r[3] for r in v] for k, v in review_anchors.match_records([note], [marker, own]).items()}
        if matched != {3: [8]}:
            fail(f"a protected marker in {where} (page 5) and a note with its text in b.tex (page 8): the note got pages {matched}")
    odd = {**note, "typeset": "[EB:human-written]"}  # (as review_notes would make it of a macro it reads otherwise than TeX)
    if review_anchors.match_records([odd], [own]) != {3: [own]}:
        fail("a note whose typeset text no record has lost the record that holds its text")
    ok("the review copy tells apart two authors' notes with the same text, and gives no note a protected marker's record")


def tool_timeout_checks():
    """A tool run in a process group of its own (latexlive.tools.run_tool with group: a shell running tools in
    turn, as the batched SyncTeX lookups do) is stopped whole past its timeout, rather than the shell alone,
    whose child would hold the output (and the slot) until it ended."""
    from latexlive import tools
    began = time.monotonic()
    code, _ = tools.run_tool(["sh", "-c", "for n in 1 2; do sleep 20; done"], tempfile.gettempdir(), timeout=1, kind="check",
                             capture=1000, group=True)
    if code is not None or time.monotonic() - began > 5:
        fail(f"a shell's tool run past its timeout: exit {code} after {time.monotonic() - began:.1f} s (not stopped whole)")
    ok("a tool run in a process group of its own is stopped whole past its timeout")


def standby_snapshot_checks():
    """The warm standby notices a name that appears in a folder the paper links to (latexlive/standby.snapshot and
    new_name, which look at the working directory: run in a process of their own), as TeX would find it
    (\\IfFileExists of a file that was missing), and a link back up doesn't loop."""
    tmp = tempfile.mkdtemp(prefix="latex-live-check-")
    try:
        os.makedirs(os.path.join(tmp, "paper", "build"))
        os.makedirs(os.path.join(tmp, "assets"))
        os.symlink(os.path.join(tmp, "assets"), os.path.join(tmp, "paper", "assets"))
        os.symlink(".", os.path.join(tmp, "paper", "loop"))
        script = ("import os, sys, time\n"
                  f"sys.path.insert(0, {HERE!r})\n"
                  "from latexlive import standby\n"
                  "snap = standby.snapshot('build', 'main-live')\n"
                  "before = None if snap is None else standby.new_name(snap, 'build', 'main-live')\n"
                  "time.sleep(0.01)\n"
                  "open(os.path.join('..', 'assets', 'optional.tex'), 'w').close()\n"
                  "print(snap is not None, before, standby.new_name(snap, 'build', 'main-live') if snap else None)\n"
                  "os.makedirs(os.path.join('build', 'clean'), exist_ok=True)\n"
                  "clean = standby.snapshot('build', 'main-clean', os.path.join('build', 'clean'))\n"
                  "time.sleep(0.01)\n"
                  "open(os.path.join('build', 'pdflatex4242.fls'), 'w').close()\n"
                  "transient = standby.new_name(clean, os.path.join('build', 'clean'), 'main-clean')\n"
                  "open(os.path.join('build', 'generated.tex'), 'w').close()\n"
                  "print(transient, standby.new_name(clean, os.path.join('build', 'clean'), 'main-clean'))\n")
        r = subprocess.run([sys.executable, "-c", script], cwd=os.path.join(tmp, "paper"), capture_output=True, text=True, timeout=60)
        if r.stdout.split() != ["True", "None", os.path.join("assets", "optional.tex"), "None", os.path.join("build", "generated.tex")]:
            fail(f"a file added in a folder the paper links to went unnoticed by the warm standby's snapshot: {r.stdout}{r.stderr}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    ok("the warm standby notices a file added in a folder the paper links to (and a link back up doesn't loop), and a"
       " notes-free standby one added to build/ (but not pdflatex's passing recorder file)")


def source_swap_checks():
    """The browser editor and the notes read and write a source only as the file that was checked (sources.open_source,
    write_source): when a folder on its path or the file itself becomes a symbolic link after the check (to a file
    outside the paper, or to /dev/zero), a read or a save follows no link, writes nothing outside the paper and leaves
    no temporary file, and the editor answers 404 for the read and 409 for the save; a file that is just as it was
    reads and saves as before, keeping its mode."""
    from latexlive import editor, sources
    tmp = tempfile.mkdtemp(prefix="latex-live-check-")
    try:
        root, outside = os.path.join(tmp, "paper"), os.path.join(tmp, "outside")
        put(os.path.join(root, "main.tex"), "\\documentclass{article}\\begin{document}\\input{sub/ch}\\end{document}\n")
        put(os.path.join(root, "sub", "ch.tex"), "chapter\n")
        put(os.path.join(outside, "ch.tex"), "secret\n")
        os.chmod(os.path.join(root, "sub", "ch.tex"), 0o640)
        real = sources.editable_real(root, os.path.join("sub", "ch.tex"))
        server = fake_server(root, git_name="Owner")
        listed = {"main.tex": sources.editable_real(root, "main.tex"), os.path.join("sub", "ch.tex"): real}
        with patched(editor, editable_files=lambda server, fresh=True: listed):  # (as checked before the swap)
            status, body = editor.file_request(server, {"path": [os.path.join("sub", "ch.tex")]})
            if status != 200 or body.get("text") != "chapter\n":
                fail(f"the editor no longer reads a source: {status} {body}")
            status, saved_body = editor.save_request(server, {"path": os.path.join("sub", "ch.tex"), "base": body["version"],
                                                              "text": "chapter, saved\n"}, {"id": "owner", "name": None, "role": "owner"})
            if (status != 200 or sources.read_source(real) != "chapter, saved\n"
                    or os.stat(real).st_mode & 0o777 != 0o640):
                fail(f"the editor no longer saves a source as before (keeping its mode): {status} {saved_body}")
            os.rename(os.path.join(root, "sub"), os.path.join(root, "sub.was"))
            os.symlink(outside, os.path.join(root, "sub"))  # (a folder on the checked path, now a link out of the paper)
            read = editor.file_request(server, {"path": [os.path.join("sub", "ch.tex")]})
            save = editor.save_request(server, {"path": os.path.join("sub", "ch.tex"), "base": None, "text": "pwned\n",
                                                "force": True}, {"id": "owner", "name": None, "role": "owner"})
            try:
                sources.write_source(real, "secret\n", "pwned\n")
                wrote_through = True
            except sources.SourceChanged:
                wrote_through = False
            os.remove(os.path.join(root, "sub"))
            os.rename(os.path.join(root, "sub.was"), os.path.join(root, "sub"))
            os.remove(real)
            os.symlink("/dev/zero", real)  # (the file itself, now a link to something endless)
            try:
                sources.read_source(real)
                endless = True
            except sources.SourceChanged:
                endless = False
            final = editor.file_request(server, {"path": [os.path.join("sub", "ch.tex")]})
        with open(os.path.join(outside, "ch.tex")) as f:
            if f.read() != "secret\n" or wrote_through or os.listdir(outside) != ["ch.tex"]:
                fail("a save followed a folder swapped for a link out of the paper after the check")
        if read[0] != 404 or "secret" in str(read[1]) or save[0] != 409 or final[0] != 404 or endless:
            fail(f"after a swap for a link, the editor's read says {read}, its save {save}, and a read of a link to"
                 f" /dev/zero {final}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    ok("the editor and the notes read and write a source only as the file that was checked: a folder or the file swapped"
       " for a link since is never followed")


def forward_cache_checks():
    """Forward search (navigation.forward_request) makes a lookup once per build, but one that found nothing (it may
    have failed for now: a busy slot, the TeX Live image missing) is made again a few seconds later rather than kept
    for the build; requests for it meanwhile share it."""
    from latexlive import navigation
    calls, answers = [], [None, None]

    def target(*args):
        calls.append(args)
        return answers.pop(0) if answers else ("main.pdf", {"page": 1, "x": 0, "y": 0, "w": 1, "h": 1})
    with tempfile.TemporaryDirectory(prefix="latex-live-check-") as tmp, patched(navigation, forward_target=target,
                                                                                  synctex_unavailable=lambda: None):
        root = os.path.join(tmp, "paper")
        put(os.path.join(root, "main.tex"), "text\n")
        server = fake_server(root)
        query = {"file": ["main.tex"], "line": ["1"]}
        first, again = navigation.forward_request(server, query), navigation.forward_request(server, query)
        for entry in server.results.values():  # (as if the few seconds had passed)
            entry["expires"] = 0
        later = navigation.forward_request(server, query)
        for entry in server.results.values():
            entry["expires"] = 0
        found = navigation.forward_request(server, query)
        if any("expires" in entry for entry in server.results.values()):  # (a lookup that found the line is kept for the build)
            fail("forward search keeps what it found for only a few seconds")
        cached = navigation.forward_request(server, query)
    if first[0] != 404 or again[0] != 404 or later[0] != 404 or found[0] != 200 or cached[0] != 200 or len(calls) != 3:
        fail(f"forward search's misses are kept for the build, or not shared for a moment: {first[0]}, {again[0]}, {later[0]},"
             f" {found[0]}, {cached[0]} after {len(calls)} lookups")
    ok("forward search looks a line up again a few seconds after it found nothing, and keeps what it finds for the build")


def link_checks():
    """static's link of the viewer's modules (link_modules, with Node) finds what would blank the viewer, each in the
    module that has it: a name imported that its module doesn't export, a module that doesn't parse, an import of no
    file, a bare name the import map doesn't map, a name re-exported that its module doesn't export; and it follows the
    import map's names (also a folder's, name/), links a cycle across folders, and runs nothing (a module that throws)."""
    node = shutil.which("node")
    if not node:
        return print("skip: no node, so the check of the viewer's modules' link is not checked itself", flush=True)
    with tempfile.TemporaryDirectory(prefix="latex-live-check-") as root:
        for name, text in {"lib/a.js": "export const a = 1;\n", "lib/deep/b.js": "export const b = 2;\n",
                           "good.js": 'import { a } from "lib-a";\nimport { b } from "lib/deep/b.js";\nexport const c = a + b;\n',
                           "missing.js": 'import { c } from "./good.js";\nimport { d } from "./lib/a.js";\n',
                           "user.js": 'import "./missing.js";\n', "broken.js": "export const e = ;\n",
                           "nofile.js": 'import "./gone.js";\n', "unmapped.js": 'import "elsewhere";\n',
                           "lib/cycle.js": 'import { a } from "./a.js";\nimport { other } from "../other/cycle.js";\nexport const lib = a;\n',
                           "other/cycle.js": 'import { lib } from "../lib/cycle.js";\nimport { b } from "./b.js";\nexport const other = b;\n',
                           "other/b.js": 'export * from "../lib/deep/b.js";\nthrow new Error("run");\n',
                           "reexport.js": 'export { b, nope } from "./lib/deep/b.js";\n'}.items():
            put(os.path.join(root, name), text)
        found = link_modules(node, root, {"lib-a": "./lib/a.js?v=1", "lib/": "./lib/"},
                             ["good.js", "user.js", "missing.js", "broken.js", "nofile.js", "unmapped.js", "other/cycle.js", "reexport.js"])
    want = ["missing.js: The requested module './lib/a.js' does not provide an export named 'd'", "broken.js does not parse",
            "gone.js is not a file here", "unmapped.js imports elsewhere, which the import map does not map",
            "reexport.js: The requested module './lib/deep/b.js' does not provide an export named 'nope'"]
    if found["modules"] != 11 or len(found["problems"]) != len(want) or not all(
            any(p.startswith(w) for p in found["problems"]) for w in want):
        fail(f"the link of ES modules (link_modules) found {found}, not each of {want} once")
    ok("the check of the viewer's modules' link finds a missing export (also re-exported), a syntax error, a missing file"
       " and an unmapped name, each once, in the module that has it; it links a cycle, and runs nothing")


def forward_script_checks():
    """forward.py (the editor's "show line in PDF") says why when SyncTeX can't run (no synctex, and no podman to run
    it in), rather than that the line is in no build."""
    with tempfile.TemporaryDirectory(prefix="latex-live-check-") as root:
        put(os.path.join(root, "main.tex"), "text\n")
        put_bytes(os.path.join(root, "build", "main-live.synctex.gz"), gzip.compress(b""))  # (a build to find the paper by)
        r = subprocess.run([sys.executable, os.path.join(HERE, "forward.py"), "main.tex", "1"], cwd=root, capture_output=True,
                           text=True, timeout=60, env={"PATH": os.path.join(root, "nothing"), "HOME": root})
    if r.returncode != 1 or "no synctex" not in r.stderr:
        fail(f"forward.py without synctex or podman: exit {r.returncode}, {r.stderr.strip()!r} (not why SyncTeX can't run)")
    ok("forward.py says why SyncTeX can't run, rather than that the line is in no build")


# Lines of a source file, and double-clicked words in them as the viewer sends them, with the column each lands on
# (None: not there, or its places can't be told apart).
WORD_LINES = ["Quokka caf\\'e alpha beta alpha gamma \\cite[p.~2]{alpha} delta % alpha in a comment",
              "na\\\"{\\i}ve {\\em hy\\-phen} x\U0001d465 omega omega omega"]
WORD_CASES = [((1, {"word": "Café"}), 7), ((1, {"word": "alpha", "before": "beta", "after": "gamma"}), 25),
              ((1, {"word": "alpha", "after": "beta"}), 14), ((1, {"word": "alpha", "occurrence": "1", "count": "2"}), 25),
              ((1, {"word": "alpha", "occurrence": "1", "count": "3"}), None), ((1, {"word": "delta"}), 56),
              ((2, {"word": "NAÏVE"}), 0), ((2, {"word": "hyphen"}), 16), ((2, {"word": "omega", "before": "omega",
              "after": "omega"}), 36), ((1, {"word": "zebra"}), None), ((3, {"word": "alpha"}), None)]


def word_column_checks():
    """Inverse search finds the double-clicked word in its source line (navigation.word_column): by its neighbours, by
    its place among the line's same words when the PDF's line holds as many, through accents and markup, and not in a
    citation's key or a comment; a column in UTF-16 units (after a character outside the BMP)."""
    from latexlive import navigation
    with tempfile.TemporaryDirectory(prefix="latex-live-check-") as tmp:
        put(os.path.join(tmp, "main.tex"), "\n".join(WORD_LINES) + "\n")
        got = [navigation.word_column(os.path.join(tmp, "main.tex"), line, navigation.clicked_word({k: [v] for k, v in q.items()}))
               for (line, q), _ in WORD_CASES]
    wrong = [(case, column) for (case, want), column in zip(WORD_CASES, got) if column != want]
    if wrong:
        fail(f"inverse search puts the double-clicked word at the wrong column: {wrong}")
    ok("inverse search finds the double-clicked word in its line, by its neighbours or its place, through accents and markup")


def shown_synctex_checks():
    """Each publish keeps a copy of the pass's SyncTeX file in build/shown/ (latex_report.keep_synctex), which the
    lookups read once a failed pass has removed the build's own (files.shown_synctex); the copy is made only from a
    regular file (a FIFO doesn't hold it up) and never through a link planted in build/shown/."""
    sys.path.insert(0, HERE)
    import latex_report
    from latexlive import files
    with tempfile.TemporaryDirectory(prefix="latex-live-check-") as tmp, patched(latex_report, BUILD=os.path.join(tmp, "build")):
        build = latex_report.BUILD
        own, kept, outside = (os.path.join(build, "main-live.synctex.gz"), os.path.join(build, "shown", "main-live.synctex.gz"),
                              os.path.join(tmp, "outside"))
        put_bytes(own, b"first pass")
        latex_report.keep_synctex(own)
        if read_bytes(kept) != b"first pass":
            fail(f"keep_synctex did not copy the SyncTeX file to build/shown/: {os.listdir(build)}")
        os.remove(own)  # (as a failed pass does)
        if files.shown_synctex(tmp, "main.pdf") != kept:
            fail(f"after a failed pass, the lookups read {files.shown_synctex(tmp, 'main.pdf')}, not the copy kept")
        put_bytes(outside, b"not the server's to write")
        os.symlink(outside, os.path.join(build, "shown", ".main-live.synctex.gz.tmp"))
        put_bytes(own, b"second pass")
        latex_report.keep_synctex(own)
        if read_bytes(outside) != b"not the server's to write" or read_bytes(kept) != b"second pass":
            fail("keep_synctex wrote through a link in build/shown/, or kept no copy beside it")
        os.mkfifo(os.path.join(build, "fifo-live.synctex.gz"))
        copier = threading.Thread(target=latex_report.keep_synctex, args=(os.path.join(build, "fifo-live.synctex.gz"),),
                                  daemon=True)
        copier.start()
        copier.join(5)
        if copier.is_alive() or os.path.exists(os.path.join(build, "shown", "fifo-live.synctex.gz")):
            fail("keep_synctex waits on a FIFO, or copies from it")
    ok("a publish keeps a copy of its SyncTeX file, which the lookups read after a failed pass; only from a regular file,"
       " never through a link")


# A SyncTeX file of one page, as pdfTeX writes it for \includegraphics[width=0.3\linewidth] of a 320x240 bp figure
# (line 14) and the same with trim and clip of a 220x140 bp one (line 15): each graphic is a box at its scaled size
# around an \rlap (a box with no width) around one at its original size, and the clipped one draws a form that SyncTeX
# records without a line.
GRAPHICS_SYNCTEX = """SyncTeX Version:1
Input:1:/paper/main.tex
Output:pdf
Magnification:1000
Unit:1
X Offset:0
Y Offset:0
Content:
!100
{1
[1,10:4736286,46220574:26673152,41484288,0
(1,14:10998001,17010672:6783045,5087313,0
(1,14:10998001,17010672:0,5087326,0
(1,14:10998001,17010672:21050163,15787622,0
)
)
)
(1,15:17999499,17010672:6783045,4316487,0
(1,15:17999499,17010672:0,4316487,0
(1,15:17999499,17010672:14471936,9209407,0
f19:17999499,17010672
)
)
)
]
}1
Input:2:/paper/after.tex
"""
GRAPHICS_BBL = "\\begin{thebibliography}{2}\n\n\\bibitem[Doe(2021)]{doe2021}\nJane Doe.\n\\newblock Another.\n\n" \
               "\\bibitem[{Smith et~al.(2020)Smith, Roe, and\n  Poe}]{smith2020}\nJohn Smith.\n\n\\end{thebibliography}\n"
GRAPHICS_BIB = "@article{smith2020,\n  title={A},\n}\n\n@inproceedings{ doe2021 ,\n  title={B},\n}\n"


def graphics_synctex_checks():
    """SyncTeX around graphics and the bibliography (synctex.laid_out, line_at, bib_source): forward search measures a
    scaled graphic's boxes at the size TeX laid them out (synctex view grows them to the original's size); inverse search
    on a clipped graphic, where synctex edit answers line 0, finds the line of the boxes around the point; and an entry
    of the bibliography (a line of the .bbl) goes to its .bib entry."""
    from latexlive import synctex
    with tempfile.TemporaryDirectory(prefix="latex-live-check-") as tmp:
        synctex_gz = os.path.join(tmp, "main-live.synctex.gz")
        with gzip.open(synctex_gz, "wt") as f:
            f.write(GRAPHICS_SYNCTEX)
        found = {tuple(round(c, 2) for c in at): tuple(round(c, 2) for c in to) for at, to in synctex.laid_out(synctex_gz, 1)}
        want = {(167.19, 258.59, 320.0, 240.0): (167.19, 258.59, 103.11, 77.34),
                (273.62, 258.59, 220.0, 140.0): (273.62, 258.59, 103.11, 65.62)}
        if any(found.get(at) != to for at, to in want.items()):
            fail(f"forward search's boxes around scaled graphics, as laid out: {found}")
        llap, column = (59.39, 134.76, 12.61, 3.27), (72.0, 672.75, 228.64, 547.95)  # (lineno's number: moved, not grown)
        graphic = synctex.laid_box((167.19, 258.59, 320.0, 240.0), synctex.laid_out(synctex_gz, 1))
        if synctex.laid_box(llap, [(llap, column)]) != llap or tuple(round(c, 2) for c in graphic) != (167.19, 258.59, 103.11, 77.34):
            fail(f"forward search measures again a box that synctex did not grow, or not one it did: {graphic}")
        spots = [synctex.line_at(synctex_gz, 1, x, y) for x, y in ((323, 220), (218, 220), (600, 750))]
        if spots != [("/paper/main.tex", 15), ("/paper/main.tex", 14), ("/paper/main.tex", 10)]:
            fail(f"inverse search where synctex edit answers line 0 (a clipped graphic; far from any box): {spots}")
        put(os.path.join(tmp, "build", "main-live.bbl"), GRAPHICS_BBL)
        put(os.path.join(tmp, "refs.bib"), GRAPHICS_BIB)
        entries = [synctex.bib_source(tmp, os.path.join(tmp, "build", "main-live.bbl"), n) for n in (1, 4, 5, 10, 11)]
        bib = os.path.join(tmp, "refs.bib")
        if entries != [None, (bib, 5), (bib, 5), (bib, 1), (bib, 1)]:
            fail(f"inverse search on the bibliography's entries (lines of the .bbl): {entries}")
    ok("SyncTeX measures scaled graphics as laid out, finds a clipped graphic's line, and takes a bibliography entry to"
       " its .bib entry")


def turned_synctex():
    """A SyncTeX file of four pages (612 x 792 points), as pdflscape's pages record their bodies: a portrait page; a page
    whose body graphicx rotates by 90 degrees around (477.48, 672.75), from a box with no width there (/Rotate 90); one
    whose body it rotates by -90 degrees around (134, 124.8) (/Rotate 270); and a page with /Rotate 90 and nothing
    rotated on it."""
    sp = lambda *bp: ",".join(str(round(v * 65781.76)) for v in bp)
    block = lambda h, v: [f"(1,5:{sp(h, v)}:0,{sp(343.7)},0", f"[1,5:{sp(h, v)}:{sp(548, 343.7)},0",
                          f"(1,6:{sp(h, v - 200)}:{sp(548, 7)},{sp(2)}", f"x1,6:{sp(h + 30, v - 200)}",
                          f"(1,7:{sp(h, v - 100)}:{sp(600, 7)},{sp(2)}", f"x1,7:{sp(h + 590, v - 100)}", ")", ")", "]", ")"]
    body = lambda h, v: [f"(1,3:{sp(134, 672.75)}:{sp(343.7, 548)},0", *block(h, v), ")", f"x1,8:{sp(306, 720)}"]
    lines = ["SyncTeX Version:1", "Input:1:/paper/main.tex", "Output:pdf", "Magnification:1000", "Unit:1",
             "X Offset:0", "Y Offset:0", "Content:",
             "{1", f"[1,2:{sp(134, 672.75)}:{sp(343.7, 548)},0", f"x1,2:{sp(150, 200)}", "]", "}1",
             "{2", *body(477.48, 672.75), "}2", "{3", *body(134, 124.8), "}3",
             "{4", f"[1,9:{sp(134, 672.75)}:{sp(343.7, 548)},0", f"x1,9:{sp(150, 200)}", "]", "}4", "Postamble:"]
    return "\n".join(lines) + "\n"


def turned_page_checks():
    """Turned pages (synctex.page_turns, with a stand-in for pdfinfo): a block rotated by 90 degrees (pdflscape's, on a
    page with /Rotate 90) or by -90 (/Rotate 270) is found, and nothing on a portrait page or on a page with /Rotate but
    nothing rotated; SyncTeX's records in a block go where they are drawn, an overfull line's too, and back (inverse
    search), while the records outside it stay; the boxes and points of each line follow; without poppler, nothing
    changes; and where those pages are shown turned."""
    from latexlive import synctex
    info = "".join(f"Page    {n} size: 612 x 792 pts (letter)\nPage    {n} rot:  {r}\n" for n, r in ((1, 0), (2, 90), (3, 270), (4, 90)))
    pdfinfo = lambda argv, timeout=60: info if argv[0] == "pdfinfo" and argv[-1].endswith("main-live.pdf") else ""  # (the stand-in)
    with tempfile.TemporaryDirectory(prefix="latex-live-check-") as tmp, patched(synctex, pdf_tool=pdfinfo):
        synctex_gz = os.path.join(tmp, "main-live.synctex.gz")
        with gzip.open(synctex_gz, "wt") as f:
            f.write(turned_synctex())
        put_bytes(os.path.join(tmp, "main-live.pdf"), b"%PDF-1.5\n")  # (what pdfinfo reads: the stand-in)
        turns = synctex.page_turns(synctex_gz)
        angles = {page: [b[2] for b in t["blocks"]] for page, t in turns.items()}
        if angles != {2: [90], 3: [270], 4: []}:
            fail(f"the rotated blocks of turned pages: {angles} (want 90 on page 2, 270 on page 3, none on page 4)")
        close = lambda a, b: len(a) == len(b) and all(abs(x - y) < 0.06 for p, q in zip(a, b) for x, y in zip(p, q))
        spots = [synctex.on_page(turns.get(page), x, y) for page, x, y in (
            (1, 150, 200), (2, 507.48, 472.75), (2, 1067.48, 572.75), (2, 306, 720), (3, 164, -75.2), (4, 150, 200))]
        if not close(spots, [(150, 200), (277.48, 642.75), (377.48, 82.75), (306, 720), (334, 154.8), (150, 200)]):
            fail(f"SyncTeX's records where they are drawn on turned pages: {spots}")
        back = [synctex.as_recorded(turns.get(page), *xy) for page, xy in ((2, (277.48, 642.75)), (3, (334, 154.8)), (2, (306, 720)))]
        if not close(back, [(507.48, 472.75), (164, -75.2), (306, 720)]):
            fail(f"inverse search's points on turned pages, where SyncTeX records them: {back}")
        boxes, points = synctex.synctex_boxes(synctex_gz), synctex.synctex_points(synctex_gz)
        line = boxes.get(("/paper/main.tex", 6), [])
        if not close(line, [(2, 270.48, 124.75, 279.48, 672.75), (3, 332.0, 124.8, 341.0, 672.8)]) or not close(
                points.get(("/paper/main.tex", 6), []), [(2, 277.48, 642.75), (3, 334.0, 154.8)]) or not close(
                points.get(("/paper/main.tex", 8), []), [(2, 306.0, 720.0), (3, 306.0, 720.0)]):
            fail(f"a turned page's boxes and points where they are drawn: {line}, {points.get(('/paper/main.tex', 6))},"
                 f" {points.get(('/paper/main.tex', 8))}")
        shown = [synctex.shown_point(turns[page], 277.48, 642.75) + synctex.shown_size(turns[page]) for page in (2, 3)]
        if not close(shown, [(149.25, 277.48, 792, 612), (642.75, 334.52, 792, 612)]):
            fail(f"where a turned page's points are shown, and its size: {shown}")
        synctex.pdf_tool = lambda argv, timeout=60: ""
        for path in (synctex_gz, os.path.join(tmp, "main-live.pdf")):  # (a new build: looked up again)
            os.utime(path, ns=(1, 1))
        if synctex.page_turns(synctex_gz) or not close(synctex.synctex_points(synctex_gz).get(("/paper/main.tex", 6), []),
                                                       [(2, 507.48, 472.75), (3, 164.0, -75.2)]):
            fail("without poppler, SyncTeX's records should stay as recorded")
    ok("turned pages: pdflscape's rotated body (90 or -90 degrees) is found, its records go where they are drawn and"
       " back, the rest stays, nothing changes without poppler, and the pages are shown turned")


def diff_retry_checks():
    """The latexdiff against a commit (baselines.build_diff, with stand-ins for latexdiff and pdflatex): when the
    marked document makes no PDF or has errors, it is built once more with plainer marks, and the PDF with fewer errors
    is published (saying so when it has the plainer marks); a pass that timed out is not tried again; and files that
    \\input each other in a loop, on either side, stop it before latexdiff runs (input_loop, which follows \\input,
    \\include and \\import as latexdiff does, and skips comments)."""
    from latexlive import baselines
    calls, outcome, TIMEOUT = [], {}, 99  # (outcome: a pass's errors with each marks, -1 for no PDF, or TIMEOUT)

    def run_tool(argv, cwd, **kwargs):  # (latexdiff writes a document; pdflatex makes a PDF of it, or not)
        if argv[0] == "latexdiff":
            calls.append("latexdiff plainer" if "--math-markup=off" in argv else "latexdiff full")
            return 0, "\\documentclass{article}\\begin{document}x\\end{document}\n"
        marks = next(c for c in reversed(calls) if c.startswith("latexdiff")).split()[1]
        what = outcome[marks]
        calls.append("pdflatex")
        put(os.path.join(root, "build", "diff", "main-diff.log"), "! Undefined control sequence.\n" * max(0, int(what)))
        if what >= 0:
            put(os.path.join(root, "build", "diff", "main-diff.pdf"), f"%PDF-1.5 {marks}\n")
        return (None if what == TIMEOUT else 0), ""
    stand_ins = {"run_tool": run_tool, "image_pulled": lambda: True, "container_argv": lambda root: []}
    with tempfile.TemporaryDirectory(prefix="latex-live-check-") as tmp, patched(baselines, **stand_ins):
        root = os.path.join(tmp, "paper")
        put(os.path.join(root, "main.tex"), "\\documentclass{article}\\begin{document}\\input{a}\\end{document}\n")
        put(os.path.join(root, "a.tex"), "% \\input{main}\nText.\n")
        for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "x"]):
            git(root, *args)
        sha = git_out(root, "rev-parse", "HEAD")
        results = []
        for full, plainer in ((0, 0), (-1, 0), (2, 0), (1, 2), (-1, -1), (TIMEOUT, 0), (2, TIMEOUT)):
            outcome.update(full=full, plainer=plainer)
            calls.clear()
            server = fake_server(root, diff={"rev": "HEAD"})
            baselines.build_diff(server, sha)
            published = os.path.join(root, "build", "main-diff.pdf")
            results.append((server.diff.get("state"), server.diff.get("plainer"), sum(c.startswith("latexdiff") for c in calls),
                            server.diff.get("state") == "done" and read_bytes(published).split()[-1].decode()))
        put(os.path.join(root, "a.tex"), "\\import{sub/}{c}\n")  # (names in what an \\import reads resolve from its folder)
        put(os.path.join(root, "sub", "c.tex"), "\\input{d}\n")
        put(os.path.join(root, "sub", "d.tex"), "\\include{c}\n")
        loop = baselines.input_loop(root, "main.tex")
        put(os.path.join(tmp, "subfile", "main.tex"), "\\input{sub/a}\n")  # (a file again, but resolving in another folder)
        put(os.path.join(tmp, "subfile", "sub", "a.tex"), "\\input{x}\n")
        put(os.path.join(tmp, "subfile", "x.tex"), "\\subfile{sub/a}\n")
        put(os.path.join(tmp, "subfile", "sub", "x.tex"), "Text.\n")
        no_loop = baselines.input_loop(os.path.join(tmp, "subfile"), "main.tex")
        server = fake_server(root, diff={"rev": "HEAD"})
        calls.clear()
        baselines.build_diff(server, sha)
    want = [("done", False, 1, "full"), ("done", True, 2, "plainer"), ("done", True, 2, "plainer"), ("done", False, 2, "full"),
            ("failed", None, 2, False), ("failed", None, 1, False), ("done", False, 2, "full")]
    if results != want:
        fail(f"the diff's second try with plainer marks: (state, plainer, latexdiff runs, the PDF published) {results}")
    if loop != ["sub/c.tex", "sub/d.tex", "sub/c.tex"] or no_loop or server.diff.get("state") != "failed" or calls or \
            "sub/c.tex → sub/d.tex → sub/c.tex" not in server.diff.get("msg", ""):
        fail(f"files that \\input each other in a loop: {loop} (and a file read again from another folder: {no_loop}); the"
             f" diff {server.diff} after {calls}")
    ok("a diff whose marked document doesn't build is built again with plainer marks (not after a timeout), and"
       " files that \\input each other in a loop stop it before latexdiff runs")


def venue_checks():
    """The page limit and the sections left out of the main text, from the venue style the document loads
    (document.page_limit, uncounted_sections), and --page-limit over it."""
    from latexlive import document
    with tempfile.TemporaryDirectory(prefix="latex-live-check-") as tmp:
        found = []
        for style, limit in (("{iclr2027_conference}", None), ("[review]{acl}", None), ("[submission]{aaai2026}", None),
                             ("[review,year=2026,ID=1]{eccv}", None), ("{times}", None), ("[review]{acl}", 9)):
            put(os.path.join(tmp, "main.tex"), f"\\documentclass{{article}}\\usepackage{style}\n")
            server = fake_server(tmp, page_limit=limit)
            found.append((document.page_limit(server, "main.pdf"), document.uncounted_sections(server, "main.pdf")))
    if found != [(9, []), (8, ["limitations"]), (7, []), (14, []), (None, []), (9, ["limitations"])]:
        fail(f"page limits and sections left out by venue style: {found}")
    ok("the venue style sets the page limit (ICLR, ACL, AAAI, ECCV), and ACL's leaves out its Limitations")


def arxiv_strip_checks():
    """What the arXiv package takes out of a .tex file (arxiv.strip_source): comments (a trailing one keeps its %),
    \\iffalse blocks and comment environments that take whole lines, and the notes' text; not what TeX reads verbatim,
    an escaped %, a % in \\url's argument, inline blocks, or a comment environment the sources define. And how it
    compares PDFs (arxiv.pdf_difference: dates and IDs aside), and that the arXiv check reads the package made."""
    from latexlive import arxiv
    source = ("\\documentclass{article}\r\n% a whole-line comment\r\n\\usepackage{url}% loads url\r\n"
              "50\\% and a break\\\\% then a comment\n\\url{http://x.org/a%20b} and \\verb|50%| stay % but this goes\n"
              "\\foo{%\n  bar}\n%\n\\begin{verbatim}\n% kept \\eb{kept}\n\\end{verbatim}\n\\iffalse\nhidden \\iftrue\\fi\n  \\fi\n"
              "inline \\iffalse x\\fi{} stays\n\\begin{comment}\ngone\n\\end{comment}\n"
              "A note \\eb{secret\nover two lines} and \\eb{} and \\eb{#1}, \\verb|\\eb{shown}| \\eb{fix \\#42}.\n"
              "\\iffalse\nunclosed\n")
    want = ("\\documentclass{article}\r\n\\usepackage{url}%\r\n50\\% and a break\\\\%\n"
            "\\url{http://x.org/a%20b} and \\verb|50%| stay %\n\\foo{%\n  bar}\n%\n\\begin{verbatim}\n% kept \\eb{kept}\n"
            "\\end{verbatim}\ninline \\iffalse x\\fi{} stays\nA note \\eb{} and \\eb{} and \\eb{#1}, \\verb|\\eb{shown}| \\eb{}.\n"
            "\\iffalse\nunclosed\n")
    text, found = arxiv.strip_source(source, {"eb": "EB"})
    if text != want or found != {"comments": 4, "hidden": 2, "notes": 2}:
        fail(f"the arXiv package's sources: {text!r}, {dict(found)}")
    own = arxiv.strip_source("a\n\\begin{comment}\nshown\n\\end{comment}\n", comment_env=False)[0]
    closed = arxiv.strip_source("a\n\\begin{comment}\ngone\n\\end{comment}\nb\n")[0]
    if own != "a\n\\begin{comment}\nshown\n\\end{comment}\n" or closed != "a\nb\n":
        fail(f"comment environments in the arXiv package: the sources' own {own!r}, the comment package's {closed!r}")
    with tempfile.TemporaryDirectory(prefix="latex-live-check-") as tmp:
        pdf = b"%%PDF-1.5\n1 0 obj << /CreationDate (D:%s) >> endobj\ntrailer << /ID [<%s> <%s>] >>\n"
        put_bytes(os.path.join(tmp, "a.pdf"), pdf % (b"20261107120000Z", b"ab" * 16, b"cd" * 16))
        put_bytes(os.path.join(tmp, "b.pdf"), pdf % (b"20250101000000Z", b"12" * 16, b"34" * 16))
        put_bytes(os.path.join(tmp, "c.pdf"), pdf.replace(b"1 0 obj", b"2 0 obj") % (b"20250101000000Z", b"12" * 16, b"34" * 16))
        put(os.path.join(tmp, "main.tex"), "\\documentclass{article}\n")
        os.makedirs(os.path.join(tmp, "build"))
        old = arxiv.arxiv_package(tmp, "main")
        put_bytes(os.path.join(tmp, "build", "main-arxiv.tar.gz"), b"")
        made = arxiv.arxiv_package(tmp, "main")
        same, other = (arxiv.pdf_difference(os.path.join(tmp, "a.pdf"), os.path.join(tmp, n)) for n in ("b.pdf", "c.pdf"))
    if same is not None or not other or old is not None or not made or not made[2].endswith("build/main-arxiv.tar.gz"):
        fail(f"comparing the arXiv package's PDF: dates and IDs apart {same!r}, otherwise {other!r};"
             f" the package the viewer made: {old}, {made}")
    ok("the arXiv package's sources lose their comments (a trailing one keeps its %), whole-line hidden blocks and their notes'"
       " text, not what TeX reads verbatim; its PDF is compared dates and IDs aside; the arXiv check reads the package made")


def completion_checks():
    """What the source pane completes (editor.label_lines, bib_completions): labels outside comments with their lines
    and the line around them, and a .bib file's keys with their titles, braced or quoted, and lines, without @string
    and the like; 15,000 entries in well under a second."""
    from latexlive import editor
    labels, around = editor.label_lines("a\n% \\label{gone}\n\\label{one} % \\label{two}\n\\caption{A plot.}\n\\label{three}\n", True)
    keys = editor.bib_completions('@string{x="y"}\n@misc{a, title = "Quoted {T}itle", year=1}\n@book{ b ,\n author={Z}}\n'
                                  '@article(c, title={A {Nested} Title},\n)\n')
    if (labels != [("one", 3), ("three", 5)] or around != {"one": "\\label{one}", "three": "\\caption{A plot.}"}
            or keys != [{"key": "a", "title": "Quoted Title", "line": 2}, {"key": "b", "title": "", "line": 3},
                        {"key": "c", "title": "A Nested Title", "line": 5}]):
        fail(f"the source pane's completions: labels {labels} {around}, keys {keys}")
    big = "".join(f"@article{{key{i},\n  title={{Title {i}}},\n  year={{2020}}\n}}\n" for i in range(15000))
    began = time.monotonic()
    if len(editor.bib_completions(big)) != 15000 or time.monotonic() - began > 1:
        fail(f"a .bib file of 15,000 entries took {time.monotonic() - began:.1f} s to read for completion")
    ok("the source pane completes labels outside comments and a .bib file's keys with their titles, quickly")


def assets_checks():
    """The paper's figures, which the source pane completes in \\includegraphics (sources.project_assets): images and
    PDFs in the paper's folders, not in the paper root (a PDF there), build/, dot folders, or through a link out of the
    paper; at most ASSETS_LIMIT, the walk stopping there; a list a few seconds old does until invalidate_assets."""
    from latexlive import sources
    with tempfile.TemporaryDirectory(prefix="latex-live-assets-") as root, tempfile.TemporaryDirectory() as elsewhere:
        for rel in ("figures/plot.pdf", "figures/sub/photo.PNG", "logo.png", "main.pdf", "build/figures/x.png",
                    ".hidden/y.png", "figures/.z.png", "figures/notes.txt", "a/b/c/d/deep.png", "a/b/c/d/e/deeper.png"):
            put_bytes(os.path.join(root, rel), b"x")
        put_bytes(os.path.join(elsewhere, "secret.png"), b"x")
        os.symlink(elsewhere, os.path.join(root, "linked"))
        os.symlink(os.path.join(elsewhere, "secret.png"), os.path.join(root, "figures", "out.png"))
        server = fake_server(root)
        found = [a["path"] for a in sources.project_assets(server)]
        want = ["a/b/c/d/deep.png", "figures/plot.pdf", "figures/sub/photo.PNG", "logo.png"]
        if found != want:
            fail(f"the paper's figures: {found}, not {want}")
        put_bytes(os.path.join(root, "figures", "new.png"), b"x")
        if "figures/new.png" in [a["path"] for a in sources.project_assets(server)]:
            fail("the paper's figures were looked for again within their few seconds")
        sources.invalidate_assets(server)
        if "figures/new.png" not in [a["path"] for a in sources.project_assets(server)]:
            fail("invalidate_assets did not make the figures be looked for again")
        for i in range(sources.ASSETS_LIMIT + 50):
            put_bytes(os.path.join(root, "many", f"{i:03}.png"), b"x")
        put_bytes(os.path.join(root, "past", "it.png"), b"x")  # (a folder after the one where the limit is reached)
        walked, walk = [], os.walk
        sources.os.walk = lambda *a, **k: (walked.append(step) or step for step in walk(*a, **k))
        try:
            sources.invalidate_assets(server)
            many = sources.project_assets(server)
        finally:
            sources.os.walk = walk
        if len(many) != sources.ASSETS_LIMIT or any(step[0].endswith(os.sep + "past") for step in walked):
            fail(f"past ASSETS_LIMIT figures: {len(many)} listed, the walk went on to {[w[0] for w in walked][-3:]}")
    ok("the source pane completes the paper's figures (not a root PDF, build/, dot folders or links out), at most"
       f" {sources.ASSETS_LIMIT}, the walk stopping there; the list is kept a few seconds, or until invalidated")


def search_checks():
    """Search in the sources (editor.search_request): the first match of each line (shown from a little before it on
    a long line), case-insensitive unless asked, as a regular expression only outside shared mode (with $ at the line's
    end, before a \\r), at most SEARCH_LIMIT matches; places in UTF-16 units, as the editor counts."""
    from latexlive import config, editor
    with tempfile.TemporaryDirectory(prefix="latex-live-search-") as root:
        put(os.path.join(root, "main.tex"), "\\documentclass{article}\r\n\\begin{document}\r\nAlpha beta. alpha\r\n\\input{sec}\r\n\\end{document}\r\n")
        put(os.path.join(root, "sec.tex"), "axb and a.b\n\U0001d6fc astral\n" + "word " * 80 + "needle\n" + "line\n" * 300)
        server = fake_server(root)
        search = lambda **q: editor.search_request(server, {k: [v] for k, v in q.items()})
        code, found = search(q="alpha")
        if code != 200 or found["matches"] != [{"file": "main.tex", "line": 3, "col": 0, "len": 5, "text": "Alpha beta. alpha", "at": 0}]:
            fail(f"search for alpha: {code} {found}")
        if search(q="alpha", case="1")[1]["matches"][0]["col"] != 12:
            fail(f"a case-sensitive search: {search(q='alpha', case='1')}")
        for shared, want in ((False, ["a b", "axb"]), (True, ["a.b"])):
            with patched(config, SHARED=shared):
                hits = [m["text"][m["at"]:m["at"] + m["len"]] for m in search(q="a.b", regex="1")[1]["matches"]]
            if hits != want:
                fail(f"search for a.b as a regular expression ({'shared' if shared else 'not shared'}): {hits}")
        if [(m["col"], m["at"], m["len"]) for m in search(q="astral")[1]["matches"]] != [(3, 3, 6)]:
            fail(f"search after a character outside the BMP (two UTF-16 units): {search(q='astral')}")
        far = search(q="needle")[1]["matches"]
        if [(m["col"], m["text"][:1], m["text"][m["at"]:m["at"] + m["len"]]) for m in far] != [(400, "…", "needle")]:
            fail(f"search for a word far into a long line: {far}")
        if [(m["file"], m["line"]) for m in search(q="article}$", regex="1")[1]["matches"]] != [("main.tex", 1)]:
            fail(f"search for article}}$ in a file with \\r\\n line ends: {search(q='article}$', regex='1')}")
        code, found = search(q="line")
        if len(found["matches"]) != editor.SEARCH_LIMIT or not found["truncated"] or search(q="(", regex="1")[0] != 400:
            fail(f"search for a word on 300 lines: {len(found['matches'])} matches (truncated: {found['truncated']})")
    ok("search in the sources finds each line's first match, case-insensitive unless asked, a regular expression only"
       f" outside shared mode, at most {editor.SEARCH_LIMIT} matches")


LOG = r"""This is pdfTeX, Version 3.141592653-2.6-1.40.25 (TeX Live 2024) (preloaded format=pdflatex)  14 APR 2026 12:00
entering extended mode
({root}/main.tex
LaTeX2e <2023-11-01>
(/usr/share/texlive/texmf-dist/tex/latex/base/article.cls
Document Class: article 2023/05/17 v1.4n Standard LaTeX document class
Package foo Warning: a package's own warning on input line 99.
(/usr/share/texlive/texmf-dist/tex/latex/base/size10.clo))
2026-04-14 12:00: a timestamp
bar.sty:12: a package's message
main.tex.bak:3: not a source file
(./sec/intro.tex
LaTeX Warning: Reference `x' on page 1 undefined on input line 7.
Overfull \hbox (12.0pt too wide) in paragraph at lines 3--9
[]\OT1/cmr/m/n/10 text (with an unbalanced parenthesis (and another

./sec/intro.tex:11: Undefined control sequence.
l.11 \foo
./sec/intro.tex:12: Missing ) inserted.
)
("./sec/related work.tex"
LaTeX Warning: Citation `y' on page 1 undefined on input line 4.
)
Package hyperref Warning: Token not allowed in a PDF string (Unicode):
(hyperref)                removing `math shift' on input line 20.
Package biblatex Warning: Conflicting options (see the manual,
(biblatex)                section 3.1) on input line 42.
main.tex:99999999999999999999: a line number far too long

build/main-live.aux:3: in the build folder
.hidden.tex:2: a dot file
../outside.tex:1: outside the paper
Underfull \hbox (badness 10000) in paragraph at lines 21--21
[]

)
Output written on build/main-live.pdf (1 page). See {real}/build and {root}.
"""


def logs_checks():
    """The build's log for editors (logs.read_log, build_logs): errors only where their file:line names a source file
    of the paper (not a timestamp, a package's message, a file in build/, a dot file or one outside the paper),
    warnings and boxes in the file TeX's log opened last (none in a package), a package's wrapped warning as one; the
    paper's folder as ./ in the raw log, also once links are resolved; the build's own errors after the log's; only
    the log's end is read, and a log parsed once until it changes."""
    from latexlive import config, logs
    with tempfile.TemporaryDirectory(prefix="latex-live-logs-") as tmp:
        real = os.path.join(tmp, "real")
        root = os.path.join(tmp, "paper")  # (a link to real: the log may name either)
        for name in ("main.tex", "sec/intro.tex", "sec/related work.tex", ".hidden.tex", "build/main-live.aux", "main.tex.bak"):
            put(os.path.join(real, name), "x\n")
        put(os.path.join(tmp, "outside.tex"), "x\n")
        os.symlink(real, root)
        server = fake_server(root, shared={"status": None})
        log = os.path.join(real, config.BUILD, "main-live.log")
        put(log, LOG.format(root=root, real=real))
        put(os.path.join(real, config.BUILD, "main-live.status.json"),
            '{"ok": false, "time": "12:00:00", "durationMs": 1234, "errors": [{"file": "sec/intro.tex", "line": 11,'
            ' "msg": "Undefined control sequence."}, {"file": null, "line": null, "msg": "a bibtex failure"}]}')
        found = logs.build_logs(server, "main.pdf")
        place = lambda d: (d["severity"], d["kind"], d["file"], d["line"])
        if [place(e) + (e["msg"],) for e in found["errors"]] != [("error", "error", "sec/intro.tex", 11, "Undefined control sequence."),
                                                                  ("error", "error", "sec/intro.tex", 12, "Missing ) inserted."),
                                                                  ("error", "error", None, None, "a bibtex failure")]:
            fail(f"the log's errors: {found['errors']}")
        want = [("warning", "warning", None, None), ("warning", "warning", "sec/intro.tex", 7),
                ("warning", "overfull", "sec/intro.tex", 9), ("warning", "warning", "sec/related work.tex", 4), ("warning", "warning", "main.tex", 20), ("warning", "warning", "main.tex", 42),
                ("info", "underfull", "main.tex", 21)]
        if [place(w) for w in found["warnings"]] != want or "removing `math shift'" not in found["warnings"][4]["msg"]:
            fail(f"the log's warnings: {[place(w) + (w['msg'][:40],) for w in found['warnings']]}")
        if root in found["raw"] or real in found["raw"] or "See ./build and ." not in found["raw"] or "(./main.tex" not in found["raw"]:
            fail(f"the raw log shows the paper's folder: {found['raw'][-200:]!r}")
        if (found["ok"], found["durationMs"], found["truncated"]) != (False, 1234, False):
            fail(f"the log's build: {found['ok']} {found['durationMs']} {found['truncated']}")
        parse, calls = logs.parse_log, []
        with patched(logs, parse_log=lambda *a: (calls.append(1), parse(*a))[1]):
            again = logs.build_logs(server, "main.pdf")
            if calls or again["warnings"] != found["warnings"]:
                fail("the log is parsed again while it is unchanged")
            filler = "".join(f"(./sec/intro.tex [{i}] line {i} of a long log)\n" for i in range(200_000))
            put(log, LOG.format(root=root, real=real) + filler + "./main.tex:5: Missing $ inserted.\n")
            t0 = time.monotonic()
            big = logs.build_logs(server, "main.pdf")
            took = time.monotonic() - t0
        if not calls or os.path.getsize(log) < 2 * logs.PARSE_LIMIT or not big["truncated"] or len(big["raw"]) > logs.RAW_TAIL:
            fail(f"a changed log is parsed again, and only its end: {len(calls)} parses, {os.path.getsize(log)} bytes,"
                 f" truncated {big['truncated']}, {len(big['raw'])} characters of it sent")
        if [place(e) for e in big["errors"]][:1] != [("error", "error", "main.tex", 5)] or took > 5:
            fail(f"the end of a long log: {big['errors'][:2]} in {took:.1f} s")
        put(log, "x" * (logs.RAW_TAIL + 10) + "\n" + "y" * (logs.RAW_TAIL + 10) + "\n")
        if not logs.build_logs(server, "main.pdf")["raw"].startswith("y"):
            fail("the end of a log whose last line is longer than what is sent is sent empty")
        sys.path.insert(0, HERE)
        import latex_report
        with patched(latex_report, BUILD=os.path.join(real, config.BUILD)):
            began = os.path.join(latex_report.BUILD, "main-live.began")
            put(began, "run\n")
            os.utime(began, (time.time() - 3, time.time() - 3))
            took = latex_report.run_duration("main-live")
            again = latex_report.run_duration("main-live")
        with open(os.path.join(HERE, "latexmkrc")) as f:
            first_pass = "[ -s $build/%R.began ] || echo run > $build/%R.began;" in f.read()
        if not (took and 3000 <= took < 6000) or again is not None or os.path.getsize(began) or not first_pass:
            fail(f"a run's time, from its first pass on: {took} ms, then {again}; latexmkrc marks the first pass: {first_pass}")
    ok("the build's log: errors only in the paper's source files, warnings and boxes in the file they are in, the paper's"
       " folder as ./, only the end of a long log read, and a log parsed once until it changes; a build is timed from its"
       " first pass")


def presence_checks():
    """Who has the paper open (status.presence_request, presence): a page is listed with the file it has open only if
    the editor opens that file, and its line clamped; it drops out after PRESENCE_TTL, or as it leaves (which looks nothing else up); a user has at
    most PRESENCE_TABS pages listed and the list at most PRESENCE_LIMIT, without crowding out pages already there; the
    list names users and pages by opaque keys (no ids or roles), says which are the asker's, and reading it changes
    nothing."""
    from latexlive import status
    with tempfile.TemporaryDirectory(prefix="latex-live-presence-") as root:
        put(os.path.join(root, "main.tex"), "\\documentclass{article}\\begin{document}\\input{sec}\\end{document}\n")
        put(os.path.join(root, "sec.tex"), "x\n")
        put(os.path.join(root, "notes.txt"), "x\n")
        server = fake_server(root, git_name="Owner Example")
        ed = {"id": "ed", "name": "Eden Doyle", "role": "editor"}
        tk = {"id": "tk", "name": "Toni Kay", "role": "editor"}
        owner = {"id": "owner", "name": None, "role": "owner"}
        say = lambda user, **req: status.presence_request(server, req, user)
        code, answer = say(ed, tab="a1", open="sec.tex", line=7)
        if code != 200 or not isinstance(answer.get("tab"), str) or answer["tab"] in ("a1", "ed"):
            fail(f"POST presence: {code} {answer}")
        for req, want in (({"open": "notes.txt", "line": 3}, (None, None)), ({"open": "../main.tex", "line": 3}, (None, None)),
                          ({"open": "sec.tex", "line": 0}, ("sec.tex", 1)), ({"open": "sec.tex", "line": 10**12}, ("sec.tex", status.PRESENCE_LINE)),
                          ({"open": "sec.tex", "line": "7"}, ("sec.tex", None)), ({"open": "sec.tex", "line": True}, ("sec.tex", None)),
                          ({"open": None, "line": 5}, (None, None)), ({"open": ["sec.tex"]}, (None, None))):
            say(tk, tab="b1", **req)
            got = next((p["file"], p["line"]) for p in status.presence(server, ed) if p["name"] == "Toni Kay")
            if got != want:
                fail(f"POST presence with {req}: listed at {got}, not {want}")
        for tab in ("", "x" * 65, "a b", "a/b", None, 7):
            if say(tk, tab=tab)[0] != 400:
                fail(f"POST presence with the page name {tab!r} was taken")
        listed = status.presence(server, ed)
        if any(set(p) != {"user", "tab", "name", "file", "line", "you"} or {p["user"], p["tab"]} & {"ed", "tk", "a1", "b1"}
               for p in listed) or "editor" in json.dumps(listed):
            fail(f"the presence list shows more than names, keys and places (an id or a role?): {listed}")
        if [p["you"] for p in listed] != [True, False] or [p["you"] for p in status.presence(server, tk)] != [False, True]:
            fail(f"the presence list doesn't say which pages are the asker's: {listed}")
        say(owner, tab="o1")
        if not any(p["name"] == "Owner Example" for p in status.presence(server, ed)):
            fail("the owner of a preview without a users file is not listed by their git name")
        before = {k: dict(v) for k, v in server.presence.items()}
        for entry in server.presence.values():
            entry["seen"] -= status.PRESENCE_TTL + 1  # (all quiet for longer than the TTL)
        aged = {k: dict(v) for k, v in server.presence.items()}
        if status.presence(server, ed) or {k: dict(v) for k, v in server.presence.items()} != aged or len(aged) != len(before):
            fail("reading the presence list changed the table, or listed pages that went quiet")
        say(ed, tab="a1", open="sec.tex", line=2)
        if [p["name"] for p in status.presence(server, tk)] != ["Eden Doyle"] or len(server.presence) != 1:
            fail(f"pages that went quiet stay in the table after a POST: {server.presence}")
        with patched(status, editable_files=lambda *a, **k: fail("POST presence (leaving) looked up the editor's files")):
            say(ed, tab="a1", leave=True, open="sec.tex", line=3)
        if status.presence(server, tk) or server.presence:
            fail("a page that left is still listed")
        for i in range(status.PRESENCE_TABS):
            if say(ed, tab=f"t{i}")[0] != 200:
                fail(f"page {i + 1} of a user was not listed")
        code, answer = say(ed, tab="extra")
        if code != 429 or "of each user" not in answer.get("error", "") or say(ed, tab="t0", open="sec.tex", line=4)[0] != 200 \
                or say(tk, tab="b1")[0] != 200:
            fail(f"a user past {status.PRESENCE_TABS} pages: another of theirs is listed ({code} {answer}), or one already listed"
                 " or someone else is not")
        if len(server.presence) != status.PRESENCE_TABS + 1:
            fail(f"the presence table after a user's pages reached the cap: {len(server.presence)} entries")
        with patched(status, PRESENCE_LIMIT=len(server.presence)):
            code, answer = say(owner, tab="o2")
            if code != 429 or "of each user" in answer.get("error", "") or say(tk, tab="b1", open="sec.tex", line=1)[0] != 200:
                fail(f"a full presence table takes a new page, or says why not as if the user had too many ({code} {answer}),"
                     " or refuses one already listed")
    ok(f"presence: a page lists only a file the editor opens, its line clamped; it drops out when quiet (reading the list"
       f" changes nothing) or as it leaves; at most {status.PRESENCE_TABS} pages per user, a capped list, nothing crowded out;"
       f" names and opaque keys, no ids or roles")


def connection_cap_checks():
    """Every server (an instance's, a shared one's, the hub's) takes at most DualStackServer.CONNECTIONS connections
    at once: past that, it closes a new one at once, and takes new ones again as the others end; as many may wait to be
    taken (a burst of them is not reset)."""
    import http.server
    import socket
    from latexlive import hub, web
    if not (issubclass(hub.HubServer, web.DualStackServer) and issubclass(web.LoopbackServer, web.DualStackServer)
            and web.DualStackServer.CONNECTIONS == 64):
        fail("not every server of latex-live keeps to the connection cap")
    if web.DualStackServer.request_queue_size < web.DualStackServer.CONNECTIONS:  # (where a full queue resets: a burst)
        fail(f"a server queues {web.DualStackServer.request_queue_size} connections to be taken, fewer than it takes at once")

    class Quiet(http.server.BaseHTTPRequestHandler):
        timeout = 10

        def do_GET(self):
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = type("Capped", (web.DualStackServer,), {"CONNECTIONS": 2, "daemon_threads": True})(("::", 0), Quiet)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port, held = server.server_address[1], []
    try:
        for _ in range(2):  # (two idle connections hold both slots)
            held.append(socket.create_connection(("127.0.0.1", port), timeout=5))
        time.sleep(0.3)
        third = socket.create_connection(("127.0.0.1", port), timeout=5)
        third.sendall(b"GET / HTTP/1.0\r\n\r\n")
        dropped = third.recv(100) == b""
        third.close()
        for s in held:
            s.close()
        wait_for("the slots to free", lambda: server.slots._value == 2, timeout=10, step=0.1)
        fourth = socket.create_connection(("127.0.0.1", port), timeout=5)
        fourth.sendall(b"GET / HTTP/1.0\r\n\r\n")
        answered = fourth.recv(100).startswith(b"HTTP/1.0 204")
        fourth.close()
    finally:
        server.shutdown()
        server.server_close()
    if not dropped or not answered:
        fail(f"a server at its connection cap: the next connection {'was dropped' if dropped else 'was served'}, and once"
             f" the others ended, a new one {'was' if answered else 'was not'} served")
    ok("every server takes at most 64 connections at once, dropping new ones past that, and takes them again as others end")


def header_deadline_checks():
    """Every server reads a request's line and headers within BaseHandler.HEADER_DEADLINE seconds, however slowly they
    trickle in (a byte now and then would otherwise hold a connection for ever), then closes the connection without an
    answer; a request sent at once is answered, and a body may take longer than the deadline (only the socket's own
    timeout bounds it)."""
    import socket
    from latexlive import web
    if web.BaseHandler.HEADER_DEADLINE != 10:
        fail(f"BaseHandler.HEADER_DEADLINE is {web.BaseHandler.HEADER_DEADLINE}, not 10 s")

    class Quick(web.BaseHandler):
        HEADER_DEADLINE = 1

        def do_GET(self):
            self.send_json(204 if self.path == "/" else 404, {})

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_json(200, {"read": len(body)})

    server = type("Deadline", (web.DualStackServer,), {"daemon_threads": True})(("::", 0), Quick)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as slow:
            began, closed = time.monotonic(), False
            try:
                for byte in b"GET / HTTP/1.0\r\nX-Slow: " + b"y" * 40:
                    slow.sendall(bytes([byte]))
                    time.sleep(0.2)
            except OSError:
                closed = True  # (reset: closed by the server)
            slow.settimeout(5)
            try:
                closed = closed or slow.recv(100) == b""
            except OSError:
                closed = True
            took = time.monotonic() - began
        with socket.create_connection(("127.0.0.1", port), timeout=5) as quick:
            quick.sendall(b"GET / HTTP/1.0\r\n\r\n")
            answered = quick.recv(100).startswith(b"HTTP/1.0 204")
        with socket.create_connection(("127.0.0.1", port), timeout=10) as body:
            body.sendall(b"POST /x HTTP/1.0\r\nContent-Length: 8\r\n\r\n")
            for byte in b"12345678":
                body.sendall(bytes([byte]))
                time.sleep(0.25)
            reply = body.recv(1000)
    finally:
        server.shutdown()
        server.server_close()
    if not closed or took > 4:
        fail(f"a request whose headers trickle in past the deadline (1 s here) was {'closed after' if closed else 'still open after'}"
             f" {took:.1f} s")
    if not answered or b'{"read": 8}' not in reply:
        fail(f"with the header deadline, a request sent at once was {'' if answered else 'not '}answered, and a body slower"
             f" than the deadline got {reply[:200]!r}")
    ok("every server takes a request's line and headers within 10 s however slowly they come, while a body may take longer")


def in_flight_checks():
    """In shared mode, a coauthor has at most handler.USER_REQUESTS requests in progress at once (the viewer's own files
    and /favicon.ico aside) and at most USER_SPAWNS of those that may start a process (SyncTeX, forward search, the
    commits and a commit's diff, a note): past that, refused; another user and the owner are not counted, nor anyone outside shared mode;
    a request that ends, also with an error, frees its place."""
    from latexlive import config, handler
    viewer = {"id": "vw", "role": "viewer"}
    with patched(config, SHARED=True):
        with contextlib.ExitStack() as held:
            spawns = [held.enter_context(handler.in_flight(viewer, "GET", "/synctex")) for _ in range(3)]
            if spawns != [True, True, False]:
                fail(f"a viewer's SyncTeX lookups at once, {handler.USER_SPAWNS} allowed: {spawns}")
            for method, path in (("GET", "/forward"), ("GET", "/history"), ("GET", "/history/diff"), ("GET", "/commits"),
                                 ("POST", "/note")):
                with handler.in_flight(viewer, method, path) as admitted:
                    if admitted:
                        fail(f"{method} {path} is not counted with the requests that may start a process")
            with handler.in_flight({"id": "ed", "role": "editor"}, "GET", "/synctex") as admitted:
                if not admitted:
                    fail("another user's SyncTeX lookup was refused for the viewer's")
            others = [held.enter_context(handler.in_flight(viewer, "GET", "/status/main.pdf"))
                      for _ in range(handler.USER_REQUESTS)]
            if others.count(True) != handler.USER_REQUESTS - 2 or others[-1]:
                fail(f"a viewer's requests at once, {handler.USER_REQUESTS} allowed: {others.count(True)} admitted with 2 lookups")
            for method, path in (("GET", "/viewer/main.js"), ("GET", "/pdfjs/pdf.mjs"), ("GET", "/viewer.html"),
                                 ("GET", "/favicon.ico")):
                with handler.in_flight(viewer, method, path) as admitted:
                    if not admitted:
                        fail(f"{method} {path}, one of the viewer's own files, was counted")
            with handler.in_flight(viewer, "GET", "/viewer/../live.py") as admitted:
                if admitted:
                    fail("a path that is not one of the viewer's files was not counted")
            with handler.in_flight({"id": "o", "role": "owner"}, "GET", "/synctex") as admitted:
                if not admitted:
                    fail("the owner's request was counted")
        try:
            with handler.in_flight(viewer, "GET", "/synctex"):
                raise ValueError("failed")
        except ValueError:
            pass
        if handler.IN_FLIGHT:
            fail(f"requests that ended still hold places: {dict(handler.IN_FLIGHT)}")
    with patched(config, SHARED=False), contextlib.ExitStack() as held:
        if not all(held.enter_context(handler.in_flight(viewer, "GET", "/synctex")) for _ in range(5)):
            fail("requests were counted outside shared mode")
    ok(f"in shared mode, a coauthor has at most {handler.USER_REQUESTS} requests in progress (the viewer's own files aside)"
       f" and {handler.USER_SPAWNS} that may start a process; others and the owner are not counted, nor anyone outside shared"
       " mode")


def note_limit_checks():
    """A note's text, a reply's and a resolve's reason take at most note_writer.NOTE_LIMIT characters as written into
    the source (a backslash escaped is 16), and an edit's LaTeX as much; no note takes a file past sources.EDITABLE_SIZE,
    which the editor no longer opens (write_source refuses any such write); a file that isn't the paper's is named in
    the refusal as asked, never by where its link leads."""
    from latexlive import config, note_writer, sources
    with tempfile.TemporaryDirectory(prefix="latex-live-check-") as tmp, patched(config, SHARED=config.SHARED):
        root = os.path.join(tmp, "paper")
        main = os.path.join(root, "main.tex")
        put(main, "\\newcommand{\\eb}[1]{\\todotxt{\\textbf{EB:} #1}}\n\\begin{document}\nSome words here. \\eb{first note}\n"
                  "\\end{document}\n")
        put(os.path.join(tmp, "outside", "secret.tex"), "x\n")
        os.symlink(os.path.join(tmp, "outside", "secret.tex"), os.path.join(root, "away.tex"))
        put(os.path.join(root, "build", "main-live.fls"), f"PWD {root}\nINPUT main.tex\nINPUT away.tex\n")
        server = fake_server(root, git_name="Owner")
        owner = {"id": "owner", "name": None, "role": "owner", "macro": "", "via": "token"}
        note = {"pdf": "main.pdf", "source": "main.tex", "line": 3, "macro": "eb", "tex": "first note", "who": "eb"}
        before = read_bytes(main)
        long = "x" * (note_writer.NOTE_LIMIT + 1)
        for what, req in (("a reply of backslashes", {**note, "action": "reply", "text": "\\" * 200}),
                          ("a long reply", {**note, "action": "reply", "text": long}),
                          ("a long reason to resolve", {**note, "action": "resolve", "text": long}),
                          ("a long edit", {**note, "action": "edit", "text": long}),
                          ("long LaTeX in an edit", {**note, "action": "edit", "latex": long})):
            if note_writer.note_request(server, req, owner)[0] != 413:
                fail(f"{what} past {note_writer.NOTE_LIMIT} characters as written was taken")
        added = note_writer.add_note(main, "main.tex", before.decode(), 3, "eb", "EB", {"text": "\\" * 200, "selection": "words here"}, ["eb"])
        if added[0] != 413 or read_bytes(main) != before:
            fail(f"a note of 200 backslashes (3200 characters escaped) was added: {added}")
        if note_writer.note_request(server, {**note, "action": "reply", "text": "y" * note_writer.NOTE_LIMIT}, owner)[0] != 200:
            fail(f"a reply of {note_writer.NOTE_LIMIT} characters was refused")
        grown = read_bytes(main).decode()
        padded = grown.replace("\\end{document}", "%" + "p" * (sources.EDITABLE_SIZE - len(grown) - 200) + "\n\\end{document}")
        put(main, padded)
        result = note_writer.note_request(server, {**note, "action": "reply", "text": "z" * 300}, owner)
        if result[0] != 413 or read_bytes(main).decode() != padded:
            fail(f"a reply that takes the file past {sources.EDITABLE_SIZE} bytes: {result}")
        try:
            sources.write_source(main, padded, padded + "q" * 300)
            fail("write_source took a file past EDITABLE_SIZE")
        except sources.SourceTooBig:
            pass
        config.SHARED = True
        result = note_writer.note_request(server, {**note, "action": "edit", "source": "away.tex", "line": 1, "text": "y"}, owner)
        if result[0] != 403 or "away.tex" not in result[1].get("error", "") or "secret" in str(result) or ".." in str(result):
            fail(f"a note in a link to a file outside the paper: {result} (want it named as asked)")
    ok(f"notes, replies and reasons take at most {note_writer.NOTE_LIMIT} characters as written; no write takes a source past"
       " 4 MB; a refusal names a file as asked, not where its link leads")
