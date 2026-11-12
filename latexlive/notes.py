"""The paper's inline notes: the note macros (the one-argument commands that go through \\todotxt),
the notes in the sources, where they landed in the notes-free PDF (for the margin bubbles), and which of
them the PDF shows (for viewers and commenters)."""

import collections
import os
import re
import unicodedata

from . import config
from .document import layout
from .files import fls_file, mtime, pdf_path, project_tex_files, shown_synctex
from .latex import brace_group, project_macros, read_tex, tex_to_text
from .synctex import page_turns, shown_point, synctex_points
from .tools import pdf_pages_text


def letters(text):
    """text's letters only, in lower case, ligatures taken apart (to find a note's text in a PDF's, whose line
    breaks, hyphens, line numbers and references differ)."""
    return re.sub(r"[\W\d_]+", "", unicodedata.normalize("NFKC", text).lower())


def shown_notes(server, pdf_name, found):
    """Of the notes found (notes, margin_notes), those that the published PDF shows, for viewers and
    commenters: a note counts if the start of its text (letters) is in the PDF's text (pdftotext), so that notes
    in a false branch (\\iffalse, a switch that is off), or all of them while \\todotxt hides them, stay
    with the editors, who read the sources anyway. Without poppler, none."""
    pdf = pdf_path(server.paper_root, pdf_name)
    key = (pdf, mtime(pdf))
    kept = getattr(server, "pdf_text", None)  # (one PDF's, the last asked for: (key, text))
    if kept and kept[0] == key:
        text = kept[1]
    else:
        text = letters(" ".join(pdf_pages_text(pdf))) if os.path.isfile(pdf) else ""
        server.pdf_text = (key, text)
    shown = lambda note: (start := letters(note.get("text") or "")[:20]) and start in text
    return [note for note in found if shown(note)]


NOTE_DEF_RE = re.compile(r"\\(?:re|provide)?newcommand\*?\s*\{?\\([a-zA-Z]+)\}?\s*\[1\]\s*(?=\{)")


def note_kinds(texts):
    """{note macro: author label} from the macro definitions in texts."""
    kinds = {}
    for text in texts:
        for m in NOTE_DEF_RE.finditer(text):
            body, _ = brace_group(text, m.end())
            if "\\todotxt" in body:
                label = re.search(r"\\textbf\{([^}:]+):?\}", body)
                kinds[m.group(1)] = label.group(1) if label else m.group(1)
    return kinds


def note_use_re(kinds):
    return re.compile(r"\\(%s)\s*(?=\{)" % "|".join(sorted(kinds, key=len, reverse=True)))


def blank_notes(text, kinds):
    """text with its notes (uses of the note macros in kinds) blanked (line numbers kept)."""
    for m in reversed(list(note_use_re(kinds).finditer(text)) if kinds else []):
        end = brace_group(text, m.end())[1]
        text = text[:m.start()] + re.sub(r"[^\n]", " ", text[m.start():end]) + text[end:]
    return text


def notes(paper_root, pdf_name):
    """The inline notes in the build's files, in reading order: [{author, text, file, line, ...}]
    (plus note_fields, for editing them from the viewer).

    Note macros are the one-argument commands whose body goes through \\todotxt (this
    project's show/hide switch), e.g. \\todo and the per-author \\eb, \\ch; the author label
    is the bold "XX:" in the body.
    """
    files = project_tex_files(paper_root, fls_file(paper_root, pdf_name))
    texts = {path: read_tex(path) for path in files}
    kinds = note_kinds(texts.values())
    if not kinds:
        return []
    macros = project_macros(paper_root, fls_file(paper_root, pdf_name))
    found = []
    for path, text in texts.items():
        for m in note_use_re(kinds).finditer(text):
            body, end = brace_group(text, m.end())
            words = " ".join(tex_to_text(body, macros).split())
            found.append({"author": kinds[m.group(1)], "text": words[:400], "file": os.path.relpath(path, paper_root),
                          "line": text.count("\n", 0, m.start()) + 1, **note_fields(text, m, end)})
    return found


# Where a note's surrounding prose stops: paragraph breaks, environments, list items, headings.
PROSE_BREAK_RE = re.compile(r"\n[ \t]*\n|\\(?:begin|end)\{[^}]*\}|\\item\b|\\\\|"
                            r"\\(?:sub)*section\*?\{[^{}]*\}|\\paragraph\*?\{[^{}]*\}|\\caption\{")
# Rendered unpredictably (math, citations, cross-references): context stops there too.
OPAQUE_RE = re.compile(r"\$[^$]*\$|\\\(.*?\\\)|\\[a-zA-Z]*(?:cite|ref)[a-zA-Z]*\*?(?:\[[^\]]*\])*\{[^}]*\}")


def margin_notes(paper_root, pdf_name):
    """The inline notes placed in the notes-free PDF, for the viewer's margin bubbles:
    [{author, text, file, line, points: [[page, x, y], ...], side, before, after, ...}] (plus note_fields).

    points are where the note's source line (or, if that line typeset nothing without its
    notes, the nearest line of the same paragraph) landed; side says which of those it is
    ("line", "before", "after"). before/after are the few typeset words around the note, which
    the viewer finds in the page text to put the note's caret between two words.
    """
    synctex_gz = shown_synctex(paper_root, pdf_name, clean=True)
    fls = fls_file(paper_root, pdf_name)
    if not os.path.exists(synctex_gz):
        return []
    points, turns = synctex_points(synctex_gz), page_turns(synctex_gz)
    texts = {path: read_tex(path) for path in project_tex_files(paper_root, fls)}
    kinds = note_kinds(texts.values())
    if not kinds:
        return []
    macros = project_macros(paper_root, fls)
    use = note_use_re(kinds)

    area = layout(paper_root, pdf_name) or {"left": 72, "top": 72, "bottom": 720}  # (1in margins until a build says)

    def at(path, line):
        """The line's points in the text area, without columns: the page frame, header, and
        line numbers are shipped out while some line is current; the left-margin glue repeats on
        each line of a paragraph (at one place across, as the lines run: a turned page's rotated
        block runs down the page as stored)."""
        found = [p for p in points.get((path, line), [])
                 if p[1] >= area["left"] - 0.5 and area["top"] - 0.5 <= p[2] <= area["bottom"] + 0.5]
        def across(p):
            turn = turns.get(p[0])
            return p[0], shown_point(turn, p[1], p[2])[0] if turn and turn["blocks"] else p[1]
        column = collections.Counter(across(p) for p in found)
        return [p for p in found if column[across(p)] < 5] or None

    def prose(tex, keep):
        """Typeset words of tex without notes, cut at its last (keep="end") or first opaque part."""
        while m := use.search(tex):
            tex = tex[:m.start()] + tex[brace_group(tex, m.end())[1]:]
        tex = OPAQUE_RE.sub(" \ue000 ", re.sub(r"\\label\{[^}]*\}|\x7f", "", tex))
        words = " ".join(tex_to_text(tex, macros, typeset=True).split())
        return words.rsplit("\ue000", 1)[-1].strip() if keep == "end" else words.split("\ue000", 1)[0].strip()

    found = []
    for path, text in texts.items():
        with open(path, errors="replace") as f:
            raw = f.read().split("\n")
        # A commented-out line is not a paragraph break (\x7f keeps it from reading as blank), and
        # notes in \iffalse blocks are not typeset.
        text = "\n".join("\x7f" if r.lstrip().startswith("%") else t for r, t in zip(raw, text.split("\n")))
        text = re.sub(r"\\iffalse\b.*?\\fi\b", lambda m: re.sub(r"[^\n]", " ", m.group()), text, flags=re.S)
        for m in use.finditer(text):
            body, end = brace_group(text, m.end())
            line = text.count("\n", 0, m.start()) + 1
            head = text[max(0, m.start() - 400):m.start()]
            cut = [b for b in PROSE_BREAK_RE.finditer(head)][-1:]
            head = head[cut[0].end() if cut else 0:]
            if cut and re.fullmatch(r"\\end\{(?:figure|table)\*?\}", cut[0].group()) and not prose(head, "end"):
                # a note right after a float: anchor it to the end of the float's caption
                float_end = m.start() - len(head)
                begin = max(text.rfind("\\begin{figure", 0, float_end), text.rfind("\\begin{table", 0, float_end))
                caption = text.rfind("\\caption{", max(begin, 0), float_end)
                if begin >= 0 and caption >= 0:
                    head = brace_group(text, caption + len("\\caption{") - 1)[0]
            tail = text[end:end + 400]
            tail = tail[:next((b.start() for b in PROSE_BREAK_RE.finditer(tail)), len(tail))]
            before, after = " ".join(prose(head, "end").split()[-6:]), " ".join(prose(tail, "start").split()[:6])
            # The note's line, else the nearest line of its paragraph that typeset something, else
            # ("near") any line close by: a caption's words are tagged with a line after it.
            last = text.count("\n", 0, end) + 1
            where, side = at(path, line), "line"
            for start, step, name in ((line - 1, -1, "before"), (last + 1, 1, "after")):
                n = start
                while not where and 0 < n <= len(raw) and raw[n - 1].strip() and abs(n - line) <= 8:
                    where, side = at(path, n), name
                    n += step
            for n in sorted(range(max(1, line - 30), last + 31), key=lambda n: (abs(n - line), n < line)):
                if where:
                    break
                where, side = at(path, n), "near"
            if not where:
                continue
            found.append({"author": kinds[m.group(1)], "text": " ".join(tex_to_text(body, macros).split())[:600],
                          "file": os.path.relpath(path, paper_root), "line": line,
                          "points": [list(p) for p in dict.fromkeys(where)], "side": side,
                          "before": before, "after": after, **note_fields(text, m, end)})
    return found


NOTE_ESCAPES = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "%": r"\%", "&": r"\&", "#": r"\#", "_": r"\_",
                "$": r"\$", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


def escape_note(text):
    """Plain text as a note macro's argument: one line, TeX's special characters escaped."""
    return "".join(NOTE_ESCAPES.get(c, c) for c in " ".join(text.split()))


def plain_note(tex):
    """The plain text that escape_note turns into tex, or None if tex has other TeX in it."""
    out, i = [], 0
    while i < len(tex):
        if tex[i] == "\\":
            c = next((c for c, e in NOTE_ESCAPES.items() if tex.startswith(e, i)), None)
            if c is None:
                return None
            out.append(c)
            i += len(NOTE_ESCAPES[c])
        elif tex[i] in NOTE_ESCAPES:
            return None
        else:
            out.append(tex[i])
            i += 1
    return " ".join("".join(out).split())


def note_fields(text, m, end):
    """What the viewer needs to edit or delete the note macro use m (body up to end) in text."""
    body = text[m.end() + 1:end - 1]
    return {"macro": m.group(1), "tex": " ".join(body.split()), "plain": plain_note(body),
            "protected": bool(config.PROTECTED_RE and config.PROTECTED_RE.search(text[m.start():end]))}


def paper_note_kinds(paper_root, pdf_name):
    """{note macro: author label} of the document's note macros (note_kinds), from the files its last
    successful build read."""
    return note_kinds(read_tex(p) for p in project_tex_files(paper_root, fls_file(paper_root, pdf_name)))

def note_authors(paper_root, pdf_name):
    """[{macro, author}]: the note macros the viewer can write, from their definitions."""
    return [{"macro": macro, "author": author} for macro, author in paper_note_kinds(paper_root, pdf_name).items()]
