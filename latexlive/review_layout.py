"""The review copy's pages: the gutters beside the paper's columns, where each note goes in them,
the pages after the paper for the notes that don't fit, and the LaTeX that typesets it all."""

import itertools
import math

from .latex import tex_to_text
from .notes import escape_note
from .review_anchors import note_source


REVIEW_HEAD = r"""\documentclass{article}
\usepackage[T1]{fontenc}
\usepackage{lmodern}
\usepackage{graphicx}
\usepackage{tikz}
\pagestyle{empty}
\tikzset{x=1bp, y=1bp, latexlive tag/.style={rounded corners=1.3bp, inner sep=.9bp, minimum height=6.6bp,
  minimum width=6.6bp, text=white, font=\sffamily\bfseries\fontsize{5.2}{5.2}\selectfont},
  latexlive mark/.style={latexlive tag, rounded corners=1bp, inner sep=.5bp, minimum height=5.2bp, minimum width=5.2bp,
  font=\sffamily\bfseries\fontsize{4.3}{4.3}\selectfont}}
% \latexlivepage{width}{height}{x}{page}{drawing}: a page of the review copy, with a page of the paper
% (or nothing) placed at x points from its left edge on its bottom edge, by TeX itself so that it lands
% exactly there (TikZ's coordinates are a little off), and the drawing over it (TikZ, in points from
% the bottom left)
\newcommand\latexlivepage[5]{\pdfpagewidth=#1bp\relax\pdfpageheight=#2bp\relax\pdfhorigin=0pt\pdfvorigin=0pt
  \shipout\vbox to\pdfpageheight{\vss\hbox{\begin{tikzpicture}[overlay]#5\end{tikzpicture}\kern#3bp\relax#4}}}
\begin{document}
"""
REVIEW_INNER, REVIEW_TAG, REVIEW_OUTER = 7, 13, 9  # a gutter, from the page edge out: space, the number, the note, space
REVIEW_MARGIN, REVIEW_GAP = 24, 5  # (top and bottom of a gutter; between notes)


def review_geometry(area):
    """The review copy's page: the export's (W x H, in points) with a gutter right of it, and with two
    columns one left of it too; snippet is the width the notes are typeset at."""
    two = area.get("columns") == 2
    gutter = round(area["W"] * (0.31 if two else 0.38))
    return {"W": area["W"], "H": area["H"], "two": two, "gutter": gutter, "left": gutter if two else 0,
            "width": area["W"] + gutter * (2 if two else 1), "snippet": gutter - REVIEW_INNER - REVIEW_TAG - REVIEW_OUTER}


def place_notes(want, heights, lo, hi, gap):
    """Tops for boxes of these heights, in this order, without overlap (gap between them), within
    lo..hi, each as near its wanted top as the others allow (least squares). Needs them to fit."""
    offsets, total = [], 0
    for h in heights:
        offsets.append(total)
        total += h + gap
    upper = hi - (total - gap)  # (where the first box's top can go, at most, with all below it)
    blocks = []  # (pooled adjacent violators: a block's boxes move together)
    for target in (w - o for w, o in zip(want, offsets)):
        blocks.append([target, 1])
        while len(blocks) > 1 and blocks[-2][0] * blocks[-1][1] > blocks[-1][0] * blocks[-2][1]:
            s, c = blocks.pop()
            blocks[-1][0] += s
            blocks[-1][1] += c
    shifts = [min(max(s / c, lo), upper) for s, c in blocks for _ in range(c)]
    return [s + o for s, o in zip(shifts, offsets)]


def review_layout(placed, probe, geo, pages):
    """Where everything goes on the review copy's pages: {pages, paper, width, height, left, W (the
    paper's page width), notes} (points from the top left), each note with its text, its number n,
    side, size (0 or 1: the smaller type),
    top (in its gutter), or continued (the page it continues on). Notes are numbered in reading order
    (page, column, top to bottom), and in each gutter they keep that order, each as near its spot as
    the others allow. When they don't fit, they are set smaller; when they still don't, the first
    ones that fit stay, and the rest continue on pages after the paper (continuation_pages), with a
    line in the gutter saying where."""
    W, H, snippet = geo["W"], geo["H"], geo["snippet"]
    for n in placed:
        n["side"] = "left" if geo["two"] and n["x"] < W / 2 else "right"
    placed.sort(key=lambda n: (n["page"], n["side"] == "right", n["y"], n["x"], n["id"]))
    snippets = probe.get("snippets", {}) if probe.get("pdf") else {}
    for number, n in enumerate(placed, 1):
        n["n"], sizes = number, snippets.get(n["id"], {})
        if 0 in sizes and 1 in sizes:
            n["sizes"] = []
            for page, text, ht, dp in (sizes[0], sizes[1]):
                w = max(text, snippet)  # (the page the probe shipped it on)
                scale = min(1, snippet / w)  # (a long word or URL sticks out: shrink it to fit)
                n["sizes"].append({"page": page, "w": round(w * scale, 2), "text": round(text * scale, 2),
                                   "ht": round(ht * scale, 2), "h": round((ht + dp) * scale, 2)})
        else:  # (the probe didn't typeset it: its text, roughly sized)
            lines = math.ceil((len(review_text(n)) + len(n["author"]) + 2) / (snippet / 3.7))
            n["sizes"] = [{"page": None, "w": snippet, "text": snippet, "ht": 6.0, "h": 8.6 * lines + 2} for _ in range(2)]
    lo, hi = REVIEW_MARGIN, H - REVIEW_MARGIN
    for _, group in itertools.groupby(placed, key=lambda n: (n["page"], n["side"])):
        group = list(group)
        for size in (0, 1):
            heights = [n["sizes"][size]["h"] for n in group]
            if sum(heights) + REVIEW_GAP * (len(group) - 1) <= hi - lo:
                keep = len(group)
                break
        else:  # (smaller type isn't enough: as many as fit, then a line saying where the rest are)
            keep = 0
            while keep < len(group) and sum(heights[:keep + 1]) + REVIEW_GAP * keep <= hi - lo - 12:
                keep += 1
        tops = place_notes([n["y"] - n["sizes"][size]["ht"] for n in group[:keep]], heights[:keep], lo,
                           hi - (12 if keep < len(group) else 0), REVIEW_GAP)
        for i, n in enumerate(group):
            n["size"], n["top"], n["continued"] = size, round(tops[i], 2) if i < keep else None, None
    extra = continuation_pages(placed, geo)
    for i, page in enumerate(extra):
        for n, *_ in page:
            n["continued"] = pages + 1 + i
    return {"pages": pages + len(extra), "paper": pages, "width": geo["width"], "height": H, "left": geo["left"], "W": W,
            "notes": [{**{k: v for k, v in n.items() if k not in ("call", "body", "typeset")}, "text": review_text(n)} for n in placed]}


def continuation_pages(notes, geo):
    """The notes that don't fit in their gutter (top None), on pages after the paper, in number order:
    [[(note, x, top, scale, heading)]], in columns as wide as a gutter's notes, top to bottom, under
    the number of the page they belong to (heading: whether it goes above this note); a note taller
    than a page is scaled to fit."""
    width = REVIEW_TAG + geo["snippet"]
    columns = max(1, int((geo["width"] - 2 * 36 + 18) // (width + 18)))
    top, bottom = 50, geo["H"] - 36
    pages, page, column, y, last = [], [], 0, top, None
    for n in sorted((n for n in notes if n["top"] is None), key=lambda n: n["n"]):
        heading = n["page"] != last or y == top
        h = (12 if heading else 0) + n["sizes"][0]["h"]
        scale = min(1, (bottom - top) / h)
        if y + h * scale > bottom and y > top:
            column, y = column + 1, top
            if column == columns:
                pages.append(page)
                page, column = [], 0
            if not heading:
                heading, h = True, h + 12
                scale = min(1, (bottom - top) / h)
        page.append((n, 36 + column * (width + 18), y, scale, heading))
        y, last = y + h * scale + 2 * REVIEW_GAP, n["page"]
    return pages + ([page] if page else [])


def review_text(n):
    """A note as plain text, for when the probe couldn't typeset it."""
    body = n["body"].encode("latin-1").decode("utf-8", errors="replace")
    return " ".join(tex_to_text(note_source(body), {}).split())


def review_tex(plan, geo, colors, notes):
    """The LaTeX source of the review copy (plan: review_layout; notes: the same notes, with their
    text, for any the probe couldn't typeset)."""
    W, H, left = geo["W"], geo["H"], geo["left"]
    text = {n["id"]: n for n in notes}
    out = [REVIEW_HEAD]
    for macro in sorted({n["macro"] for n in plan["notes"]}):
        r, g, b = colors.get(macro, (0.35, 0.35, 0.35))
        out.append(f"\\definecolor{{latexlive{macro}}}{{rgb}}{{{r},{g},{b}}}\n")

    def f(v):
        return f"{v:.2f}".rstrip("0").rstrip(".")

    def note(n, x, top, size, scale=1.0, tag_right=False):
        """The note with its top left at (x, top), and its number left of its first line (or right of
        its widest line, in a left gutter). Returns the TikZ code, and the number's center."""
        s, color = n["sizes"][size], f"latexlive{n['macro']}"
        w, ht = s["w"] * scale, s["ht"] * scale
        tag = (x + s["text"] * scale + REVIEW_TAG / 2 if tag_right else x - REVIEW_TAG / 2, H - top - ht + 2.4)
        if s["page"]:
            body = f"\\includegraphics[page={s['page']},width={f(w)}bp]{{probe.pdf}}"
        else:
            body = (f"\\parbox[t]{{{f(w)}bp}}{{\\raggedright\\sffamily\\fontsize{{7}}{{8.6}}\\selectfont\\textcolor{{{color}}}"
                    f"{{\\textbf{{{escape_note(n['author'])}:}}}} {escape_note(review_text(text[n['id']]))}}}")
        return (f"\\node[anchor=north west,inner sep=0pt,outer sep=0pt] at ({f(x)},{f(H - top)}) {{{body}}};\n"
                f"\\node[latexlive tag,fill={color}] at ({f(tag[0])},{f(tag[1])}) {{{n['n']}}};\n"), tag

    by_page = {}
    for n in plan["notes"]:
        by_page.setdefault(n["page"], []).append(n)
    for p in range(1, plan["paper"] + 1):
        wires, marks, boxes = [], [], []
        here = by_page.get(p, [])
        spread, run = {}, []  # (notes at one spot: their numbers side by side)
        for n in sorted(here, key=lambda n: (n["y"], n["x"], n["n"])) + [None]:
            if run and (n is None or abs(n["y"] - run[0]["y"]) > 1.5 or abs(n["x"] - run[0]["x"]) > 3):
                for i, m in enumerate(run):
                    spread[m["n"]] = (i - (len(run) - 1) / 2) * 6
                run = []
            run.append(n)
        for n in here:
            color, right = f"latexlive{n['macro']}", n["side"] == "right"
            ax, base = left + n["x"], H - n["y"]
            marks.append(f"\\draw[{color},line width=.7bp] ({f(ax)},{f(base - 2.2)}) -- ({f(ax)},{f(base + 6.4)});\n"
                         f"\\node[latexlive mark,fill={color}] at ({f(ax + spread[n['n']])},{f(base + 9.2)}) {{{n['n']}}};\n")
            if n["top"] is None:
                continue
            edge = left + W if right else left
            s = n["sizes"][n["size"]]
            x = edge + REVIEW_INNER + REVIEW_TAG if right else edge - REVIEW_INNER - REVIEW_TAG - s["text"]
            code, (tx, ty) = note(n, x, n["top"], n["size"], tag_right=not right)
            boxes.append(code)
            end = tx - 3.6 if right else tx + 3.6
            bend = max(ax, edge - 10) if right else min(ax, edge + 10)  # (the bend starts in the page's own margin)
            mid, y = (bend + end) / 2, base - 2.2
            wires.append(f"\\draw[{color},line width=.4bp,dash pattern=on 1.2bp off 1.1bp] ({f(ax)},{f(y)}) -- ({f(bend)},{f(y)});\n"
                         f"\\draw[{color},line width=.4bp] ({f(bend)},{f(y)}) .. controls ({f(mid)},{f(y)}) and ({f(mid)},{f(ty)})"
                         f" .. ({f(end)},{f(ty)});\n")
        for side in ("left", "right"):
            rest = [n for n in here if n["side"] == side and n["top"] is None]
            if rest:
                where = f"{rest[0]['n']}" + (f"--{rest[-1]['n']}" if len(rest) > 1 else "")
                x = left + W + REVIEW_INNER if side == "right" else REVIEW_OUTER
                boxes.append(f"\\node[anchor=south west,inner sep=0pt,font=\\sffamily\\itshape\\fontsize{{6.5}}{{8}}\\selectfont,"
                             f"text=black!65] at ({f(x)},{f(REVIEW_MARGIN)}) {{Notes {where} continue on page {rest[0]['continued']}.}};\n")
        # (gutters light gray, so the page's edges show; the page white under the paper's, which may have no background)
        paper = (f"\\fill[black!7] (0,0) rectangle ({f(plan['width'])},{f(H)});\n"
                 f"\\fill[white] ({f(left)},0) rectangle ({f(left + W)},{f(H)});\n")
        out.append(f"\\latexlivepage{{{f(plan['width'])}}}{{{f(H)}}}{{{f(left)}}}{{\\includegraphics[page={p}]{{export.pdf}}}}"
                   f"{{%\n{paper}{''.join(wires + marks + boxes)}}}\n")
    for i, page in enumerate(continuation_pages(plan["notes"], geo)):
        draw = [f"\\node[anchor=north west,inner sep=0pt,font=\\sffamily\\bfseries\\fontsize{{9}}{{11}}\\selectfont] at (36,{f(H - 24)})"
                f" {{Notes that didn't fit in the margin{' (continued)' if i else ''}}};\n"]
        for n, x, top, scale, heading in page:
            if heading:
                draw.append(f"\\node[anchor=north west,inner sep=0pt,font=\\sffamily\\bfseries\\fontsize{{6.5}}{{8}}\\selectfont,"
                            f"text=black!65] at ({f(x)},{f(H - top)}) {{Page {n['page']}}};\n")
            draw.append(note(n, x + REVIEW_TAG, top + (12 if heading else 0) * scale, 0, scale)[0])
        out.append(f"\\latexlivepage{{{f(plan['width'])}}}{{{f(H)}}}{{0}}{{}}{{%\n{''.join(draw)}}}\n")
    out.append("\\end{document}\n")
    return "".join(out)
