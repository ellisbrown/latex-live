"""The unit checks: the server's parts on their own, without an instance (made-up input, stand-ins for TeX), in the
order unit_checks runs them: the review copy's layout and matching, shared mode's (shared_units), the downloads'
bounds, failures and waits, the bibliography's SyncTeX lookups, tool timeouts, the warm standby's snapshot, the
timing lines, the restart prompt, and the static files the server sends."""

import collections
import glob
import gzip
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types

from .base import HERE, fail, ok, wait_for
from .helpers import put, put_bytes, read_bytes, tex_stand_in
from .shared_units import baseline_failure_checks, confinement_checks, shared_build_checks


def unit_checks():
    """The checks of the server's parts on their own, without an instance (made-up input, stand-ins for TeX)."""
    for check in (layout_checks, matching_checks, confinement_checks, shared_build_checks, bounds_checks, failed_export_checks,
                  bib_span_checks, waiting_download_checks, tool_timeout_checks, standby_snapshot_checks, timing_checks,
                  code_updated_checks, static_file_checks, source_swap_checks, forward_cache_checks, completion_checks, word_column_checks, venue_checks, diff_retry_checks,
                  graphics_synctex_checks, shown_synctex_checks, baseline_failure_checks, connection_cap_checks):
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
    saved, tmp = config.HERE, tempfile.mkdtemp(prefix="latex-live-check-")
    try:
        config.HERE = tmp
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
    finally:
        config.HERE = saved
        shutil.rmtree(tmp)
    ok("a restart is asked for when a module of the server changes, is added or is removed")


def static_file_checks():
    """The server sends the viewer's own files from the checkout (handler.static_file): viewer.html and the
    files in pdfjs/, codemirror/ and viewer/, never a folder, a file elsewhere in the checkout, a path through
    .., or a link out of the file's folder (a link within it is followed)."""
    from latexlive import config, handler
    saved, tmp = config.HERE, tempfile.mkdtemp(prefix="latex-live-check-")
    try:
        config.HERE = tmp
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
    finally:
        config.HERE = saved
        shutil.rmtree(tmp)
    ok("the server sends only the viewer's own files from the checkout, never through .. or a link out of their folder")


def bounds_checks():
    """The downloads read TeX's output within bounds (config.OUTPUT_LIMIT and config.SYNCTEX_LIMIT, made smaller here),
    without TeX: a log, an .aux or a .bbl past its bound (files.read_output), the probe pass's log (review.run_probe,
    with a stand-in for TeX) and a SyncTeX file uncompressed (review_anchors.synctex_pages) stop a download with a message
    that says so, before they are read whole; within them, the probe's records are read as before, and so is a log
    whose wrapped lines split a UTF-8 character."""
    from latexlive import config, files, review, review_anchors, review_layout
    saved, tmp = (config.OUTPUT_LIMIT, config.SYNCTEX_LIMIT), tempfile.mkdtemp(prefix="latex-live-check-")
    try:
        config.OUTPUT_LIMIT = config.SYNCTEX_LIMIT = 1 << 20
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
        server = types.SimpleNamespace(paper_root=root, tex_exec=tex_stand_in(stand_in))
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
    finally:
        config.OUTPUT_LIMIT, config.SYNCTEX_LIMIT = saved
        shutil.rmtree(tmp, ignore_errors=True)
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
        server = types.SimpleNamespace(paper_root=root, tex_exec=tex_stand_in(stand_in), export={}, exported={},
                                       export_lock=threading.Lock(), download_lock=threading.Lock())

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

        saved, export.run_tool = export.run_tool, too_long
        try:
            if (runs("an export that times out", "a pass took over 10 minutes"),
                    runs("it again", "a pass took over 10 minutes")) != (3, 3):
                fail("an export that timed out was built again for the same build of the document")
        finally:
            export.run_tool = saved
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
        server = types.SimpleNamespace(paper_root=root, default_pdf="main.pdf", export={}, review={}, no_build=True,
                                       download_lock=threading.Lock())
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
        server = types.SimpleNamespace(paper_root=root, default_pdf="main.pdf", versions=collections.OrderedDict(),
                                       versions_lock=threading.Lock(), last_saves=collections.OrderedDict(), last_writer={},
                                       autocommit=False, pending={}, pending_lock=threading.Lock(), git_name="Owner")
        listed = {"main.tex": sources.editable_real(root, "main.tex"), os.path.join("sub", "ch.tex"): real}
        saved = editor.editable_files
        editor.editable_files = lambda server, fresh=True: listed  # (as checked before the swap)
        try:
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
        finally:
            editor.editable_files = saved
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
    tmp = tempfile.mkdtemp(prefix="latex-live-check-")
    calls, answers = [], [None, None]
    saved = navigation.forward_target, navigation.synctex_unavailable
    try:
        root = os.path.join(tmp, "paper")
        put(os.path.join(root, "main.tex"), "text\n")
        server = types.SimpleNamespace(paper_root=root, default_pdf="main.pdf", results={}, results_lock=threading.Lock())
        navigation.synctex_unavailable = lambda: None

        def target(*args):
            calls.append(args)
            return answers.pop(0) if answers else ("main.pdf", {"page": 1, "x": 0, "y": 0, "w": 1, "h": 1})
        navigation.forward_target = target
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
    finally:
        navigation.forward_target, navigation.synctex_unavailable = saved
        shutil.rmtree(tmp, ignore_errors=True)
    if first[0] != 404 or again[0] != 404 or later[0] != 404 or found[0] != 200 or cached[0] != 200 or len(calls) != 3:
        fail(f"forward search's misses are kept for the build, or not shared for a moment: {first[0]}, {again[0]}, {later[0]},"
             f" {found[0]}, {cached[0]} after {len(calls)} lookups")
    ok("forward search looks a line up again a few seconds after it found nothing, and keeps what it finds for the build")


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
    saved = latex_report.BUILD
    with tempfile.TemporaryDirectory(prefix="latex-live-check-") as tmp:
        build = latex_report.BUILD = os.path.join(tmp, "build")
        try:
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
        finally:
            latex_report.BUILD = saved
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


def diff_retry_checks():
    """The latexdiff against a commit (baselines.build_diff, with stand-ins for latexdiff and pdflatex): when the
    marked document makes no PDF or has errors, it is built once more with plainer marks, and the PDF with fewer errors
    is published (saying so when it has the plainer marks); a pass that timed out is not tried again; and files that
    \\input each other in a loop, on either side, stop it before latexdiff runs (input_loop, which follows \\input,
    \\include and \\import as latexdiff does, and skips comments)."""
    from latexlive import baselines
    saved = baselines.run_tool, baselines.image_pulled, baselines.container_argv
    tmp = tempfile.mkdtemp(prefix="latex-live-check-")
    try:
        root = os.path.join(tmp, "paper")
        put(os.path.join(root, "main.tex"), "\\documentclass{article}\\begin{document}\\input{a}\\end{document}\n")
        put(os.path.join(root, "a.tex"), "% \\input{main}\nText.\n")
        for args in (["init", "-q"], ["add", "-A"], ["-c", "user.name=check", "-c", "user.email=check@example.com", "commit", "-qm", "x"]):
            subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True).stdout.strip()
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
        baselines.run_tool, baselines.image_pulled, baselines.container_argv = run_tool, lambda: True, lambda root: []
        results = []
        for full, plainer in ((0, 0), (-1, 0), (2, 0), (1, 2), (-1, -1), (TIMEOUT, 0), (2, TIMEOUT)):
            outcome.update(full=full, plainer=plainer)
            calls.clear()
            server = types.SimpleNamespace(paper_root=root, default_pdf="main.pdf", tex_exec="", diff={"rev": "HEAD"})
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
        server = types.SimpleNamespace(paper_root=root, default_pdf="main.pdf", tex_exec="", diff={"rev": "HEAD"})
        calls.clear()
        baselines.build_diff(server, sha)
    finally:
        baselines.run_tool, baselines.image_pulled, baselines.container_argv = saved
        shutil.rmtree(tmp, ignore_errors=True)
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
            server = types.SimpleNamespace(paper_root=tmp, page_limit=limit)
            found.append((document.page_limit(server, "main.pdf"), document.uncounted_sections(server, "main.pdf")))
    if found != [(9, []), (8, ["limitations"]), (7, []), (14, []), (None, []), (9, ["limitations"])]:
        fail(f"page limits and sections left out by venue style: {found}")
    ok("the venue style sets the page limit (ICLR, ACL, AAAI, ECCV), and ACL's leaves out its Limitations")


def completion_checks():
    """What the source pane completes (editor.label_lines, bib_completions): labels outside comments with their lines,
    and a .bib file's keys with their titles, braced or quoted, without @string and the like; 15,000 entries in well
    under a second."""
    from latexlive import editor
    labels = editor.label_lines("a\n% \\label{gone}\n\\label{one} % \\label{two}\nb\n\\label{three}\n")
    keys = editor.bib_completions('@string{x="y"}\n@misc{a, title = "Quoted {T}itle", year=1}\n@book{ b ,\n author={Z}}\n'
                                  '@article(c, title={A {Nested} Title},\n)\n')
    if labels != [("one", 3), ("three", 5)] or keys != [{"key": "a", "title": "Quoted Title"}, {"key": "b", "title": ""},
                                                       {"key": "c", "title": "A Nested Title"}]:
        fail(f"the source pane's completions: labels {labels}, keys {keys}")
    big = "".join(f"@article{{key{i},\n  title={{Title {i}}},\n  year={{2020}}\n}}\n" for i in range(15000))
    began = time.monotonic()
    if len(editor.bib_completions(big)) != 15000 or time.monotonic() - began > 1:
        fail(f"a .bib file of 15,000 entries took {time.monotonic() - began:.1f} s to read for completion")
    ok("the source pane completes labels outside comments and a .bib file's keys with their titles, quickly")


def connection_cap_checks():
    """Every server (an instance's, a shared one's, the hub's) takes at most DualStackServer.CONNECTIONS connections
    at once: past that, it closes a new one at once, and takes new ones again as the others end."""
    import http.server
    import socket
    from latexlive import hub, web
    if not (issubclass(hub.HubServer, web.DualStackServer) and issubclass(web.LoopbackServer, web.DualStackServer)
            and web.DualStackServer.CONNECTIONS == 64):
        fail("not every server of latex-live keeps to the connection cap")

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
