"""Turned pages (latexlive.synctex page_turns: pdflscape's landscape pages), in a thread of their own beside the instance
checks, with a paper, a state folder, a port and an instance of their own (landscape_run)."""

import http.client
import json
import os
import re
import shutil
import subprocess
import zlib

from .base import ENV, LANDSCAPE_PORT, fail, ok, wait_for
from .helpers import make_state, read_json_file, start, stop, texlive_image, write_text

# A portrait page with a note, a landscape page (a paragraph with a note, and a table) and a portrait page after it.
LANDSCAPE = r"""\documentclass{article}
\usepackage{xcolor}
\usepackage{pdflscape}
\newif\ifshowtodos \showtodostrue
\newcommand{\todotxt}[1]{\ifshowtodos\textcolor{red}{#1}\fi}
\newcommand{\eb}[1]{\todotxt{[EB: #1]}}
\begin{document}
\section{Portrait}
Portrait text, quokka narwhal axolotl. \eb{a portrait note}
\begin{landscape}
Landscape text, pangolin tapir gecko walrus. \eb{a landscape note}
\begin{table}[h]
\centering
\begin{tabular}{lrr}
Recipe & Alpha & Beta \\
Okapi & 1.0 & 2.0 \\
\end{tabular}
\caption{A table on a landscape page.}
\end{table}
\end{landscape}
Portrait again, zebu ibex yak.
\end{document}
"""
PORTRAIT_LINE, BODY_LINE, ROW_LINE = 9, 11, 16


def word_boxes(pdf, page):
    """{word: [xMin, yMin, xMax, yMax]} of a page of a PDF as it is shown (pdftotext -bbox: turned by the page's
    /Rotate), each word's first box."""
    out = subprocess.run(["pdftotext", "-bbox", "-f", str(page), "-l", str(page), pdf, "-"], capture_output=True, text=True).stdout
    found = {}
    for m in re.finditer(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>', out):
        found.setdefault(m[5], [float(v) for v in m.groups()[:4]])
    return found


def page_sizes(pdf):
    """{page: (width, height, rotation)} of a PDF (pdfinfo)."""
    info = subprocess.run(["pdfinfo", "-f", "1", "-l", "100", pdf], capture_output=True, text=True).stdout
    sizes = {int(n): [float(w), float(h)] for n, w, h in re.findall(r"^Page\s+(\d+) size:\s+([\d.]+) x ([\d.]+)", info, re.M)}
    for n, r in re.findall(r"^Page\s+(\d+) rot:\s+(\d+)", info, re.M):
        sizes[int(n)].append(int(r))
    return {n: tuple(s) for n, s in sizes.items()}


def stored(box, height):
    """A box on a page with /Rotate 90 as shown (pdftotext's) on the page as stored, height points high: SyncTeX's
    frame, and latex-live's."""
    x0, y0, x1, y1 = box
    return [y0, height - x1, y1, height - x0]


def holds(outer, inner, slack=1.5):
    return outer[0] <= inner[0] + slack and outer[1] <= inner[1] + slack and outer[2] >= inner[2] - slack and outer[3] >= inner[3] - slack


def comment_rects(pdf):
    """{note number: [x0, y0, x1, y1]}: where the copy with the notes as comments puts each note's annotation (PDF points
    from the bottom left), from its objects or object streams."""
    chunks = [pdf]
    for m in re.finditer(rb"stream\r?\n", pdf):
        try:
            chunks.append(zlib.decompress(pdf[m.end():pdf.find(b"endstream", m.end())]))
        except zlib.error:
            continue
    return {int(m[1]): [float(v) for v in m[2].split()] for chunk in chunks
            for m in re.finditer(rb"/NM\(latex-live-note-(\d+)\).*?/Rect\s*\[([^\]]*)\]", chunk, re.S)}


def landscape_run(tmp):
    """A paper with a pdflscape landscape page (/Rotate 90, its body rotated), with an instance of its own: forward search
    finds a table row and a line of the landscape page where they are on the page as stored (what pdftotext shows,
    turned back), and inverse search finds them from there; the landscape note's margin note is on its page, at its
    line; the table's box in the Figures tab holds it; the review copy places the landscape page as it is shown, at its
    own size with the gutter beside it, the note wired to its spot there, and the copy with the notes as comments puts
    the page at its shown size with the note's comment at its spot; the portrait pages around it are as before."""
    if not shutil.which("pdftotext") or not shutil.which("pdfinfo"):
        return print("skip: no poppler (pdftotext, pdfinfo), so turned pages are not checked", flush=True)
    image = texlive_image()[1]
    if not image and not shutil.which("pdflatex"):
        return print("skip: no TeX Live image and no pdflatex, so turned pages are not checked", flush=True)
    paper, state = os.path.join(tmp, "landscape", "paper"), os.path.join(tmp, "landscape", "state")
    write_text(paper, "main.tex", LANDSCAPE)
    token = make_state(state)
    build = os.path.join(paper, "build")
    proc = start(paper, "main", *(() if image else ("--host-tex",)), env={**ENV, "LATEX_LIVE_STATE": state}, port=LANDSCAPE_PORT)

    def ask(path, method="GET"):  # (this preview's own port; with the token as the cookie, a POST needs no Origin)
        conn = http.client.HTTPConnection("127.0.0.1", LANDSCAPE_PORT + 1, timeout=60)
        conn.request(method, path, "{}" if method == "POST" else None,
                     {"Cookie": f"latex_live={token}", **({"Content-Type": "application/json"} if method == "POST" else {})})
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return json.loads(data) if r.status == 200 and r.getheader("Content-Type", "").startswith("application/json") else None

    try:
        wait_for("the landscape paper's notes-free pass", lambda: os.path.isfile(os.path.join(build, "main-clean.pdf"))
                 and os.path.isfile(os.path.join(paper, "main.pdf")), timeout=240)
        pdf = os.path.join(paper, "main.pdf")
        sizes = page_sizes(pdf)
        if sizes.get(2) != (612, 792, 90) or sizes.get(1) != (612, 792, 0):
            fail(f"the landscape paper's pages: {sizes} (want page 2 turned: /Rotate 90)")
        H = sizes[2][1]
        words = {n: word_boxes(pdf, n) for n in (1, 2, 3)}
        row, line, portrait = stored(words[2]["Okapi"], H), stored(words[2]["Landscape"], H), words[1]["quokka"]

        forward = lambda n: wait_for(f"forward search of line {n}", lambda: ask(f"/forward?file=main.tex&line={n}"), timeout=60)
        rect = lambda t: [t["x"], t["y"], t["x"] + t["w"], t["y"] + t["h"]]
        found = {n: forward(n) for n in (ROW_LINE, BODY_LINE, PORTRAIT_LINE)}
        if found[ROW_LINE]["page"] != 2 or not holds(rect(found[ROW_LINE]), row) or rect(found[ROW_LINE])[2] > 612:
            fail(f"forward search of a table row on the landscape page: {found[ROW_LINE]}, not around {row}")
        if found[BODY_LINE]["page"] != 2 or not holds(rect(found[BODY_LINE]), line) or rect(found[BODY_LINE])[2] > 612:
            fail(f"forward search of a line on the landscape page: {found[BODY_LINE]}, not around {line}")
        if found[PORTRAIT_LINE]["page"] != 1 or not holds(rect(found[PORTRAIT_LINE]), portrait):
            fail(f"forward search of a line on a portrait page: {found[PORTRAIT_LINE]}, not around {portrait}")
        center = lambda b: ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
        spots = {n: ask(f"/synctex?file=main.pdf&page={page}&x={x:.2f}&y={y:.2f}&json=1") for n, page, (x, y) in (
            (ROW_LINE, 2, center(row)), (BODY_LINE, 2, center(line)), (PORTRAIT_LINE, 1, center(portrait)))}
        if any((spot or {}).get("line") != n for n, spot in spots.items()):
            fail(f"inverse search on the landscape page (and a portrait one), from where the text is: {spots}")
        ok("on a landscape page (pdflscape: /Rotate 90, its body rotated), forward search finds a table row and a line"
           " where they are, and inverse search finds them from there; on a portrait page as before")

        margin = wait_for("the margin notes", lambda: ask("/margin/main-clean.pdf"), timeout=60)
        note = next((n for n in margin if n["text"] == "a landscape note"), None)
        end = stored(word_boxes(os.path.join(build, "main-clean.pdf"), 2)["walrus."], H)  # (the note comes right after it)
        if not note or note["side"] != "line" or {p[0] for p in note["points"]} != {2} or not any(
                abs(p[1] - end[2]) < 3 and abs(p[2] - end[1]) < 3 for p in note["points"]):
            fail(f"the landscape note's margin note: {note}, not at its line, after {end}")
        floats = ask("/floats/main.pdf") or []
        if len(floats) != 1 or floats[0]["page"] != 2 or not holds(floats[0]["box"], row) or floats[0]["box"][2] > 612:
            fail(f"the table on the landscape page, in the Figures tab: {floats}, not around {row}")
        ok("the landscape page's note is on its page at its line in the margin, and its table's box holds it")

        reviewed = wait_for("the review copy", lambda: (lambda e: e if e and e.get("state") in ("done", "failed") else None)(
            ask("/review", "POST")), timeout=240)
        if reviewed["state"] != "done":
            fail(f"the landscape paper's review copy failed: {reviewed.get('msg')}")
        plan = read_json_file(os.path.join(build, "review", "main", "main-review.json"))
        review, export = page_sizes(os.path.join(build, "main-review.pdf")), os.path.join(build, "main-export.pdf")
        gutter = plan["width"] - plan["W"]
        if plan["sizes"] != {"2": [792, 612]} or review.get(2) != (792 + gutter, 612, 0) or review.get(1) != (612 + gutter, 792, 0) \
                or review.get(3) != review[1]:
            fail(f"the review copy's pages: {review} (gutter {gutter}), its plan's turned ones {plan['sizes']}")
        shown, placed = word_boxes(export, 2), word_boxes(os.path.join(build, "main-review.pdf"), 2)
        if any(abs(a - b) > 0.6 for a, b in zip(center(placed.get("Okapi", [0] * 4)), center(shown["Okapi"]))):
            fail(f"the review copy's landscape page is not the export's as shown: {placed.get('Okapi')}, not {shown['Okapi']}")
        mine = {n["text"]: n for n in plan["notes"]}
        here, after = mine.get("a landscape note"), shown["walrus."]
        if not here or here["page"] != 2 or here["how"] != "exact" or abs(here["x"] - after[2]) > 3 or not after[1] < here["y"] < after[3] + 1:
            fail(f"the review copy's landscape note: {here}, not right after {after} on the page as shown")
        start_of = word_boxes(export, 1)["axolotl."]
        if abs(mine["a portrait note"]["x"] - start_of[2]) > 3 or not start_of[1] < mine["a portrait note"]["y"] < start_of[3] + 1:
            fail(f"the review copy's portrait note: {mine['a portrait note']}, not right after {start_of}")
        with open(os.path.join(build, "main-annotated.pdf"), "rb") as f:
            rects = comment_rects(f.read())
        annotated = page_sizes(os.path.join(build, "main-annotated.pdf"))
        spot = rects.get(here["n"], [0] * 4)
        if annotated.get(2) != (792, 612, 0) or annotated.get(1) != (612, 792, 0) or abs(spot[0] - after[2]) > 3 or \
                not 0 < spot[1] - (612 - after[3]) < 14:
            fail(f"the copy with the notes as comments: pages {annotated}, the landscape note's comment at {spot}, not just"
                 f" above the end of {after}")
        ok("the review copy places the landscape page as it is shown, with the gutter beside it and its note at its spot;"
           " the copy with the notes as comments puts it at its shown size with the note's comment at its spot")
    finally:
        stop(proc)
