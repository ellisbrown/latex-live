"""SyncTeX: running it (synctex view and edit) and reading a build's SyncTeX file, for forward
search (a source line to a spot in the PDF; forward.py too) and inverse search (a spot to a source line)."""

import gzip
import os
import re
import shlex
import shutil
import threading
import zlib

from . import config
from .files import clean_file, job_file, mtime, open_output, read_capped, shown_synctex, shown_synctexes
from .tools import container_argv, run_tool, synctex_unavailable


def run_synctex(paper_root, *args):
    """Run `synctex <args>` and return its result records (dicts of the Key:value lines).

    Without a synctex on the host, it runs in the TeX Live image (~0.2 s); with neither, there are
    no records (synctex_unavailable says why). In shared mode always in the image, confined: SyncTeX
    parses the build's files, which the paper's TeX writes. At most four at once (SPAWNS), each for
    up to a minute.
    """
    return run_synctexes(paper_root, [args])[0]


RUN_MARK = "latex-live synctex run"  # (run_synctexes: what starts each run's output)


def run_synctexes(paper_root, runs):
    """run_synctex for each argument list of runs, in one tool run (one container, rather than one each: a
    container takes longer to start than SyncTeX to answer): a list of their records, one list per run."""
    if not runs:
        return []
    cmd = ["synctex", *runs[0]] if len(runs) == 1 else [
        "sh", "-c", f'for n; do echo "{RUN_MARK}"; eval "set -- $n"; synctex "$@"; done', "sh",
        *(" ".join(shlex.quote(str(a)) for a in args) for args in runs)]
    if config.SHARED or not shutil.which("synctex"):
        if not shutil.which("podman"):
            return [[] for _ in runs]
        cmd = [*container_argv(paper_root), *cmd]
    try:
        code, out = run_tool(cmd, paper_root, timeout=60, kind="synctex", capture=4 << 20, group=True)  # (a batch: a shell)
    except OSError:
        return [[] for _ in runs]
    found = [[]] if len(runs) == 1 else []
    for line in out.splitlines():
        if line == RUN_MARK and len(runs) > 1:
            found.append([])
            continue
        key, sep, value = line.partition(":")
        if not found:
            continue
        records = found[-1]
        if key == "Output":  # each result record starts with Output:
            records.append({})
        if sep and records:
            records[-1][key] = value
    return (found + [[] for _ in runs])[:len(runs)]


def synctex_records(synctex_gz):
    """The lines of a build's SyncTeX file, decompressed as they are read, up to SYNCTEX_LIMIT bytes and
    RECORD_LIMIT lines in all (a file the paper's TeX made could hold far more); none if there is no file."""
    f = open_output(synctex_gz)
    if f is None:
        return
    size = 0
    with f, gzip.open(f, "rt", errors="replace") as text:
        for n in range(config.RECORD_LIMIT):
            try:
                record = text.readline(1 << 16)  # (a line past that comes in parts)
            except (OSError, EOFError, zlib.error):  # (not gzip, or cut off)
                return
            size += len(record)
            if not record or size > config.SYNCTEX_LIMIT:
                return
            yield record


def to_int(text, default=None):
    """int(text), or default for text that isn't a number (SyncTeX's output comes from files the paper's TeX wrote)."""
    try:
        return int(text)
    except (TypeError, ValueError):
        return default


BIB_SPAN_LOCK = threading.Lock()  # (bib_span: one lookup per build, however many viewers poll at once)
PENDING = "pending"  # (bib_span without wait: the lookup for the newest build runs meanwhile)
BIB_SPAN_RUNNING, RUNNING_LOCK = set(), threading.Lock()  # (the lookups started without wait that have yet to end: (synctex_gz, mtime))


def bib_span(paper_root, files, cache={}, wait=True):
    """Where the bibliography starts and ends: ({"page", "y"}, {"page", "y"}) (y in points
    from the page top), or None. Maps the first and last lines of the .bbl to the PDF, cached
    per build (each viewer's status poll asks, and a lookup may take several SyncTeX runs).
    files = (synctex_gz, bbl, pdf). Without wait, while the build's lookup has yet to be made,
    PENDING, with the lookup started in a thread (the status poll must not wait for it: a new
    PDF shows at once, and its page count once the lookup ends).
    """
    synctex_gz, bbl, pdf = files
    try:
        mtime = os.stat(synctex_gz).st_mtime_ns
        text = read_capped(bbl)
        if text is None:
            return None
        last = text.count("\n") + 1
    except OSError:
        return None
    if cache.get(synctex_gz, (None,))[0] != mtime and not wait:
        with RUNNING_LOCK:
            if (synctex_gz, mtime) not in BIB_SPAN_RUNNING:
                BIB_SPAN_RUNNING.add((synctex_gz, mtime))

                def look_up():
                    try:
                        bib_span(paper_root, files, cache)
                    finally:
                        with RUNNING_LOCK:
                            BIB_SPAN_RUNNING.discard((synctex_gz, mtime))
                threading.Thread(target=look_up, daemon=True).start()
        return PENDING
    if cache.get(synctex_gz, (None,))[0] != mtime:
        with BIB_SPAN_LOCK:
            if cache.get(synctex_gz, (None,))[0] != mtime:  # (another request may have looked it up meanwhile)
                def where(records):
                    rec = next((r for r in records if "Page" in r), None)
                    try:
                        return rec and {"page": int(rec["Page"]), "x": float(rec["h"]), "y": float(rec["v"]) - float(rec["H"])}
                    except (KeyError, ValueError):  # (a record that isn't what SyncTeX writes)
                        return None

                def look_up(lines):
                    return [where(r) for r in run_synctexes(paper_root, [("view", "-i", f"{n}:0:{bbl}", "-o", pdf) for n in lines])]
                # The first line and the last; the last lines (\end{thebibliography}, blank ones) may have no boxes:
                # then walk back to one that does, within seven more.
                start, end = look_up([1, last]) if last > 1 else (*look_up([1]), None)
                if start and not end:
                    end = next(filter(None, look_up(range(last - 1, max(1, last - 8), -1))), None)
                end = end if start else None
                cache[synctex_gz] = (mtime, start and (start, end))
    return cache[synctex_gz][1]


def refs_start(paper_root, pdf_name, clean=False, wait=True):
    """Where the bibliography starts, in the live build or the notes-free one (without wait, PENDING
    while it is being looked up: bib_span)."""
    f, synctex_gz = clean_file if clean else job_file, shown_synctex(paper_root, pdf_name, clean)
    span = bib_span(paper_root, (synctex_gz, f(paper_root, pdf_name, ".bbl"), synctex_gz[:-len(".synctex.gz")] + ".pdf"),
                    wait=wait)
    return span if span is PENDING else span and span[0]


SYNCTEX_BOX_RE = re.compile(r"([\[(h])(\d+),(\d+):(-?\d+),(-?\d+):(-?\d+),(-?\d+),(-?\d+)")  # vboxes, hboxes


def synctex_boxes(synctex_gz, cache={}):
    """{(file, line): [(page, left, top, right, bottom)]}: the boxes each source line made (points
    from the page's top-left), from a build's SyncTeX file."""
    key = (synctex_gz, mtime(synctex_gz))
    if key not in cache:
        inputs, boxes, page = {}, {}, 0
        pt = 72 / 72.27 / 65536  # scaled points to PDF points
        for record in synctex_records(synctex_gz):
            if record.startswith("Input:"):
                tag, _, path = record[6:].rstrip("\n").partition(":")
                inputs[tag] = os.path.normpath(path)
            elif record.startswith("{"):
                page = to_int(record[1:], page)
            elif m := SYNCTEX_BOX_RE.match(record):
                x, y, w, h, d = (int(v) * pt for v in m.groups()[3:])
                boxes.setdefault((inputs.get(m.group(2)), int(m.group(3))), []).append(
                    (page, round(x, 1), round(y - h, 1), round(x + w, 1), round(y + d, 1)))
        cache.clear()
        cache[key] = boxes
    return cache[key]


def forward_target(paper_root, src, line, clean=False):
    """Forward search: where source line src:line (absolute path) is in the most recent build that
    has it (or in the notes-free build), as published (shown_synctex). Returns (pdf, {"page", "x", "y",
    "w", "h"}) in points from the page's top-left, or None.
    """
    for synctex_gz, _ in shown_synctexes(paper_root, clean):
        job = os.path.basename(synctex_gz)[: -len(".synctex.gz")]
        pdf = os.path.join(paper_root, config.BUILD, *(["clean"] if clean else []), f"{job}.pdf")  # (the job's, as named)
        records = [r for r in run_synctex(paper_root, "view", "-i", f"{line}:0:{src}", "-o", synctex_gz[: -len(".synctex.gz")] + ".pdf")
                   if "Page" in r]
        if not records:
            continue
        # A long source line can span several output lines: cover all of them on the first page.
        page = records[0]["Page"]
        try:
            boxes = [(float(r["h"]), float(r["v"]) - float(r["H"]), abs(float(r["W"])), float(r["H"]))
                     for r in records if r["Page"] == page]
            int(page)
        except (KeyError, ValueError):  # (a record that isn't what SyncTeX writes)
            continue
        x, y = min(b[0] for b in boxes), min(b[1] for b in boxes)
        return pdf, {"page": int(page), "x": x, "y": y,
                     "w": max(b[0] + b[2] for b in boxes) - x, "h": max(b[1] + b[3] for b in boxes) - y}
    return None


SYNCTEX_POINT_RE = re.compile(r"([gkx$])(\d+),(\d+):(-?\d+),(-?\d+)")  # glue, kerns, points, math


def synctex_points(synctex_gz, cache={}):
    """{(file, line): [(page, x, y)]}: where each source line's words, spaces, and math
    landed (points from the page's top-left, y at the baseline), from a build's SyncTeX file."""
    key = (synctex_gz, mtime(synctex_gz))
    if key not in cache:
        inputs, points, page = {}, {}, 0
        for record in synctex_records(synctex_gz):
            if record.startswith("Input:"):
                tag, _, path = record[6:].rstrip("\n").partition(":")
                inputs[tag] = os.path.normpath(path)
            elif record.startswith("{"):
                page = to_int(record[1:], page)
            elif m := SYNCTEX_POINT_RE.match(record):
                pt = 72 / 72.27 / 65536  # scaled points to PDF points
                points.setdefault((inputs.get(m.group(2)), int(m.group(3))), []).append(
                    (page, round(int(m.group(4)) * pt, 1), round(int(m.group(5)) * pt, 1)))
        cache.clear()
        cache[key] = points
    return cache[key]


def synctex_input(paper_root, name, page, x, y, clean):
    """PDF position -> (the input file as SyncTeX recorded it, line), or (status, message) when there
    is none. name: the published PDF; x, y in PDF points from the page's top-left; clean for a
    position in the notes-free PDF."""
    if not name.endswith(".pdf") or os.path.basename(name) != name:
        return 400, "bad file"
    synctex_gz = shown_synctex(paper_root, name, clean)  # (the notes-free PDF itself moves to build/)
    pdf = synctex_gz[:-len(".synctex.gz")] + ".pdf"
    if not os.path.isfile(synctex_gz):
        return 404, f"no {os.path.relpath((clean_file if clean else job_file)(paper_root, name, '.pdf'), paper_root)} yet"
    result = next((r for r in run_synctex(paper_root, "edit", "-o", f"{page}:{x:.2f}:{y:.2f}:{pdf}") if "Input" in r), {})
    if "Input" not in result:
        missing = synctex_unavailable()
        return (503, missing) if missing else (404, "no source location here")
    return os.path.normpath(result["Input"]), max(1, to_int(result.get("Line"), 1))


def inverse_search(paper_root, name, page, x, y, clean):
    """PDF position -> (source path, line), or (status, message) when there is none (synctex_input)."""
    found = synctex_input(paper_root, name, page, x, y, clean)
    if isinstance(found[0], int):
        return found
    src, line = found
    if src.startswith(os.path.join(paper_root, config.BUILD) + os.sep):  # generated, e.g. the table of contents
        return 404, f"generated text ({os.path.basename(src)}); no source line"
    return src, line
