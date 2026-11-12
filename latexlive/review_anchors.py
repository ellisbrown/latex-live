"""Where each note sits in the export, for the review copy: the probe's records matched to the
notes, trusted where the probe typeset a line exactly as the export did, else placed by source line."""

import gzip
import os
import re

from . import config
from .document import FLOAT_ENV_RE
from .files import open_output, too_big
from .latex import INPUT_RE, brace_group, read_tex, tex_to_text
from .synctex import on_page, page_turns, to_int


BP = 72 / 72.27  # TeX points to PDF points


def synctex_pages(synctex_gz):
    """{page: [(kind, file, line, x, y, width, box)]}: a build's SyncTeX records in document order, in
    points from the page's top left. box is the hbox (a line) a record is directly in, as [left,
    baseline, right, start, contents] (start: after an indent box; contents: its records but the x
    ones, for comparing two builds), or None. Raises RuntimeError past SYNCTEX_LIMIT."""
    inputs, pages, page, stack, size = {}, {}, 0, [], 0
    raw = open_output(synctex_gz)
    if raw is None:
        raise FileNotFoundError(2, "No such file", synctex_gz)
    with raw, gzip.open(raw, "rt", errors="replace") as f:
        while record := f.readline(1 << 16):  # (a line past that comes in parts)
            size += len(record)
            if size > config.SYNCTEX_LIMIT:
                raise RuntimeError(too_big(os.path.basename(synctex_gz), config.SYNCTEX_LIMIT, " uncompressed"))
            c = record[:1]
            if record.startswith("Input:"):
                tag, _, path = record[6:].rstrip("\n").partition(":")
                inputs[tag] = os.path.normpath(path)
            elif c == "{":
                page, stack = to_int(record[1:], page), []
                pages[page] = []
            elif c in ")]":
                stack = stack[:-1]
            elif (m := SYNCTEX_ANY_RE.match(record)) and page in pages:
                kind, tag, line, h, v, w = m.group(1), m.group(2), int(m.group(3)), int(m.group(4)), int(m.group(5)), m.group(6)
                x, y, w = h / 65536 * BP, v / 65536 * BP, int(w or 0) / 65536 * BP
                box = stack[-1] if stack else None
                if kind == "h" and box and abs(x - box[0]) < 0.01 and box[3] == box[0]:
                    box[3] = x + w  # (a paragraph's indent)
                pages[page].append((kind, inputs.get(tag), line, x, y, w, box))
                if box is not None and kind != "x":
                    box[4].append((kind, round(x, 2), round(y, 2), round(w, 2)))
                if kind in "([":
                    stack.append([x, y, x + w, x, []] if kind == "(" else None)
    return pages


SYNCTEX_ANY_RE = re.compile(r"([\[(hvxkg$])(\d+),(\d+):(-?\d+),(-?\d+)(?::(-?\d+))?")


def synctex_same(a, b):
    """The pages two builds (synctex_pages) typeset the same way: the same records at the same
    places, whatever lines they are tagged with; b may have extra x records (one marks where text
    stopped, and the probe's records add some)."""
    def key(r):
        return r[0], round(r[3], 2), round(r[4], 2), round(r[5], 2)
    same = set()
    for page, recs in a.items():
        other, j = b.get(page, []), 0
        for r in recs:
            while j < len(other) and key(other[j]) != key(r) and other[j][0] == "x":
                j += 1
            if j >= len(other) or key(other[j]) != key(r):
                break
            j += 1
        else:
            if all(r[0] == "x" for r in other[j:]):
                same.add(page)
    return same


def same_line(export, probe):
    """A test of the probe's spots: same_line(page, x, y) says whether the line (innermost hbox)
    at that spot of the probe is typeset as in the export: at the same place, with the same records
    (whole pages typeset the same pass at once). A note's spot depends only on its line."""
    whole, lines = synctex_same(export, probe), {}
    for page, recs in export.items():
        for r in recs:
            if r[6] is not None:
                lines[(page, round(r[6][0], 2), round(r[6][1], 2), round(r[6][2], 2))] = r[6][4]

    def test(page, x, y):
        if page in whole:
            return True
        boxes = [r[6] for r in probe.get(page, []) if r[6] is not None and abs(r[6][1] - y) < 0.05
                 and r[6][0] - 0.5 <= x <= r[6][2] + 0.5]
        box = min(boxes, key=lambda b: b[2] - b[0], default=None)
        return bool(box) and lines.get((page, round(box[0], 2), round(box[1], 2), round(box[2], 2))) == box[4]
    return test


def squeeze(tex):
    """tex without its spaces, for comparing a note's source with the probe's \\detokenize of it."""
    tex = re.sub(r"\^\^([0-9a-f]{2})", lambda m: chr(int(m.group(1), 16)), tex)
    return re.sub(r"\s+", "", tex.replace("##", "#"))


def review_anchors(paper_root, out, notes, probe, area):
    """The notes that are typeset, each with its spot in the export: [{**note, page, x, y, how}],
    x, y in points from the page's top left (y at the baseline). how is "exact" (the probe's
    record, on a line typeset as in the export) or "line" (approximate: the export's SyncTeX, by
    source line, or else the probe's record on a line it typeset a little differently). On a turned
    page, where it is drawn (on_page: the probe's \\pdfsavepos, like SyncTeX, ignores a rotation)."""
    export = synctex_pages(os.path.join(out, "export.synctex.gz"))
    turns = page_turns(os.path.join(out, "export.synctex.gz"))
    drawn = lambda page, x, y: on_page(turns.get(page), x, y)
    trusted = same_line(export, synctex_pages(probe["synctex"])) if probe and os.path.isfile(probe["synctex"]) else None
    records = [("pos", *r) for r in probe.get("pos", [])] + [("seen", r[0], r[1], None, None, None, r[2]) for r in probe.get("seen", [])]
    matched = match_records(notes, records)
    lines = {}
    for page, recs in export.items():
        for r in recs:
            x, y = drawn(page, r[3], r[4])
            if r[0] in "hvxkg$" and r[1] and area["top"] - 15 <= y <= area["bottom"] + 15 and 0 <= x <= area["W"]:
                lines.setdefault((os.path.relpath(r[1], paper_root), r[2]), []).append((page, *r))
    placed = []
    for n in notes:
        recs = matched.get(n["id"], [])
        exact = [r for r in recs if r[0] == "pos" and trusted and trusted(r[3], r[4], r[5])]
        if exact:
            inside = [r for r in exact if area["top"] - 15 <= drawn(*r[3:6])[1] <= area["bottom"] + 15]
            r = (inside or exact)[0]
            x, y = drawn(*r[3:6])
            placed.append({**n, "page": r[3], "x": round(x, 2), "y": round(y, 2), "how": "exact"})
            continue
        if not recs and ("pages" in probe or not any((n["file"], ln) in lines for ln in range(n["line"] - 3, n["last"] + 4))):
            continue  # (not typeset: the probe went through and never met it; or, without a probe, nothing near it shows)
        probed = [r for r in recs if r[0] == "pos"]  # (on a line the probe set a little differently)
        spot = None
        if not probed or any(r[1] == n["file"] and n["line"] <= r[2] <= n["last"] for r in probed):
            spot = line_anchor(paper_root, n, lines, vertical=any(r[0] == "seen" for r in recs))
        if not spot and probed:  # (read elsewhere, as in a macro defined in another file: the probe's spot, near enough)
            spot = probed[0][3:6]
        if spot:
            x, y = drawn(*spot)
            placed.append({**n, "page": spot[0], "x": round(x, 2), "y": round(y, 2), "how": "line"})
    return placed


def match_records(notes, records):
    """{note id: [record]}: which note each of the probe's records (kind, file, line, ..., tex) is
    about. A note is recorded when TeX typesets it, at the line TeX is reading then: its own last
    line, or later in an argument read whole (a caption). So a record goes to the note above it in
    its file whose text it holds (the longest such text, then the nearest note), else to a note in
    another file whose text it holds (one in a macro used here), preferring notes whose macro typesets
    exactly that (so two authors' notes with the same text don't swap); records of one line and text
    go in order. A note whose typeset text is recorded somewhere takes no record of another text,
    which is another macro's: a protected marker's, say (no note), whose text may hold the note's."""
    by_file, taken, found = {}, set(), {}
    for n in notes:
        by_file.setdefault(n["file"], []).append(n)
    keyed = {}
    for r in records:
        keyed.setdefault((r[1], r[2], squeeze(r[-1])), []).append(r)
    recorded = {tex for _, _, tex in keyed}

    def holds(n, tex):
        return squeeze(note_source(n["body"])) in tex and (n.get("typeset") == tex or n.get("typeset") not in recorded)

    for (path, line, tex), group in keyed.items():
        candidates = [n for n in (by_file.get(path, []) if path else notes) if n["last"] <= line and holds(n, tex)]
        if not candidates:  # (a note in a macro defined in another file, recorded where the macro is used)
            candidates = [n for n in notes if n["file"] != path and holds(n, tex)]
        candidates = [n for n in candidates if n.get("typeset") == tex] or candidates  # (its own author's)
        free = [n for n in candidates if n["id"] not in taken] or candidates
        chosen = sorted(free, key=lambda n: (len(squeeze(note_source(n["body"]))), n["last"], n["id"]))[-len(group):]
        for i, r in enumerate(group):
            if chosen:
                n = chosen[min(i, len(chosen) - 1)]
                taken.add(n["id"])
                found.setdefault(n["id"], []).append(r)
    return found


def note_source(body):
    """A note's argument as TeX reads it: without comments, blank lines as \\par."""
    body = re.sub(r"(?<!\\)%[^\n]*\n?[ \t]*", "", body)
    return re.sub(r"\n[ \t]*\n\s*", r"\\par ", body)


def line_anchor(paper_root, n, lines, vertical):
    """A note's spot from the export's SyncTeX, by source line, when the probe has none: after the
    words before it on its line (a note in a paragraph), else at the start of the next typeset line
    (a note before a paragraph or between two; in a file \\input there, if that comes first), else
    after the last typeset words before it; not in a float the note isn't in (floats move). (page, x,
    y) or None; lines is {(file, line): [(page, kind, file, line, x, y, width, box)]}."""
    path = os.path.join(paper_root, n["file"])
    if not vertical:
        with open(path, "rb") as f:
            raw = f.read().decode("latin-1").split("\n")
        before = re.sub(r"(?<!\\)%.*", "", raw[n["line"] - 1][:n["col"]] if n["line"] <= len(raw) else "")
        while m := re.search(r"\\(?:footnote|footnotetext|marginpar)\s*(?=\{)", before):  # (typeset elsewhere)
            before = before[:m.start()] + before[brace_group(before, m.end())[1]:]
        words = len(tex_to_text(re.sub(r"\$[^$]*(\$|$)", " ", before), {}).split())
        ends = [r for r in lines.get((n["file"], n["line"]), []) if r[1] == "x"]
        if words and len(ends) >= words:
            r = ends[words - 1]
            return r[0], r[4], r[5]
    text, floats = read_tex(path), []
    for m in FLOAT_ENV_RE.finditer(text):
        line = text.count("\n", 0, m.start()) + 1
        if m.group(1) == "begin":
            floats.append([line, line])
        elif floats:
            floats[-1][1] = line
    moved = {ln for a, b in floats if not a <= n["line"] <= b for ln in range(a, b + 1)}
    source = text.split("\n")
    for ln in range(n["last"], n["last"] + 40):
        recs = None if ln in moved else lines.get((n["file"], ln))
        for m in INPUT_RE.finditer(source[ln - 1] if not recs and ln <= len(source) else ""):
            name = os.path.normpath(m.group(1).strip())
            child = next((f for f in (name, name + ".tex") if any(k[0] == f for k in lines)), None)
            if child:
                recs = lines[min(k for k in lines if k[0] == child)]
                break
        if recs:  # (its first words: a line's first records can belong to the row or line before it)
            page, _, _, _, x, y, _, box = next((r for r in recs if r[1] == "x"), recs[0])
            return (page, box[3], box[1]) if box else (page, x, y)
    for ln in range(n["line"], max(0, n["line"] - 40), -1):
        if ln not in moved and (recs := lines.get((n["file"], ln))):
            return recs[-1][0], recs[-1][4], recs[-1][5]
    return None
