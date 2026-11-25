"""The paper's inline notes: the note macros (the one-argument commands that go through \\todotxt),
the notes in the sources, where they landed in the notes-free PDF (for the margin bubbles), and which of
them the PDF shows (for viewers and commenters)."""

import collections
import hashlib
import os
import re

from . import config
from .document import layout
from .files import fls_file, pdf_path, project_tex_files, shown_synctex
from .latex import blank_comments, brace_group, project_macros, read_tex, tex_to_text
from .synctex import page_turns, shown_point, synctex_points


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


# Put before the document by shared mode's builds (shown_tex), for shown_notes: from \begin{document} on, \todotxt
# hands the note macro's text on with a record in front of it (in a group of its own, which soul's \hl takes as one
# piece), which writes a line into the log when its page is shipped out (a \write that isn't \immediate):
# latex-live|shown|<key>|<folder>|<file>|<line>|<MD5 of the text without its spaces>. So a note that isn't typeset,
# in \iffalse or a switch that is off, dropped by \todotxt (\showtodosfalse) or only measured (a box thrown away),
# records nothing. In what is written to files (moving arguments, a running head), \todotxt is the paper's as it
# was, and the record vanishes. Only a \todotxt of one argument (or a robust one: its inner command) gets the record,
# \protected if the paper's was; without one, no note counts. key, new for each run, keeps the paper's text from
# passing for a record (a commenter's note in a box TeX shows in the log, say).
SHOWN_TEX = (r"\makeatletter\protected\long\def\latexlive@record#1{\edef\latexlive@w{\write-1{latex-live|shown|<KEY>|"
             r"\ifdefined\CurrentFile\detokenize\expandafter{\CurrentFilePath}|\detokenize\expandafter{\CurrentFile}\else|\fi"
             r"|\the\inputlineno|\pdfmdfivesum{\expandafter\zap@space\detokenize{#1} \@empty}}}\latexlive@w}"
             r"\def\latexlive@shown{\ifx\protect\@typeset@protect\expandafter\latexlive@record\else\expandafter\@gobble\fi}"
             r"\long\def\latexlive@todotxt#1{\ifx\protect\@typeset@protect\expandafter\@firstoftwo\else\expandafter\@secondoftwo"
             r"\fi{\latexlive@orig@todotxt{{\latexlive@shown{#1}}#1}}{\latexlive@orig@todotxt{#1}}}"
             r"\def\latexlive@wrap#1{\expandafter\let\expandafter\latexlive@orig@todotxt\csname#1\endcsname"
             r"\ifnum\pdfmatch{^(\string\\protected)?(\string\\long)? ?macro:\string##1->}{\meaning\latexlive@orig@todotxt}=1 "
             r"\ifnum\pdfmatch{^\string\\protected}{\meaning\latexlive@orig@todotxt}=1 "
             r"\expandafter\protected\expandafter\long\expandafter\def\csname#1\endcsname##1{\latexlive@todotxt{##1}}"
             r"\else\expandafter\let\csname#1\endcsname\latexlive@todotxt\fi"
             r"\ifdefined\pdfstringdefDisableCommands\pdfstringdefDisableCommands{\let\latexlive@shown\@gobble}\fi\fi}"
             r"\def\latexlive@notes{\ifdefined\todotxt\ifdefined\pdfmdfivesum"
             r"\ifnum\pdfmatch{^macro:->\string\\protect \string\\todotxt }{\meaning\todotxt}=1 \latexlive@wrap{todotxt }"
             r"\else\latexlive@wrap{todotxt}\fi\fi\fi}"
             r"\ifdefined\AddToHook\AddToHook{begindocument/end}{\latexlive@notes}\else\AtBeginDocument{\latexlive@notes}\fi"
             r"\makeatother")


def shown_tex(key):
    """SHOWN_TEX with its records keyed key (letters and digits)."""
    return SHOWN_TEX.replace("<KEY>", key)


def note_records(log, key):
    """The notes a pass typeset, from its log (shown_tex(key)): [(file, line, digest)], file as TeX named it (relative
    to the paper root, or absolute), line where TeX was reading then."""
    pattern = r"^latex-live\|shown\|%s\|([^|\n]*)\|([^|\n]*)\|(\d+)\|([0-9A-F]{32})$" % re.escape(key)
    return [(os.path.normpath(os.path.join(m.group(1), m.group(2))) if m.group(2) else "", int(m.group(3)), m.group(4).lower())
            for m in re.finditer(pattern, log or "", re.M)] if key else []


def typeset_digest(tex):
    """The digest SHOWN_TEX records for tex, what a note macro hands \\todotxt, as written in the source (comments
    blanked by blank_comments): as TeX reads it (a blank line is \\par), without its spaces."""
    tex = re.sub(r"\n[ \t]*\n\s*", r"\\par ", tex).replace("\x7f", "")
    return hashlib.md5(re.sub(r"[ \t\n\r\f\v]+", "", tex).encode("utf-8")).hexdigest()


# A macro's definition, up to its body's brace: a note in one is typeset where the macro is used.
DEFINITION_RE = re.compile(r"\\(?:(?:(?:re|provide)?newcommand|DeclareRobustCommand)\*?\s*\{?\\[a-zA-Z@]+\}?(?:\s*\[\d\])?"
                           r"(?:\s*\[[^\]]*\])?|(?:New|Renew|Provide|Declare)DocumentCommand\s*\{?\\[a-zA-Z@]+\}?\s*\{[^{}]*\}"
                           r"|[gex]?def\s*\\[a-zA-Z@]+[^{%\n]*)\s*(?=\{)")


def source_digests(paper_root, pdf_name):
    """{path: SHA-256} of the build's .tex files (those its last successful pass read)."""
    found = {}
    for path in project_tex_files(paper_root, fls_file(paper_root, pdf_name)):
        try:
            with open(path, "rb") as f:
                found[path] = hashlib.sha256(f.read()).hexdigest()
        except OSError:
            pass
    return found


def recorded_notes(paper_root, pdf_name, records, sources):
    """The notes of the build's files that records (note_records) are of, as {(file, line, macro, tex)} (note
    fields: tex squeezed): notes that hand \\todotxt exactly what a record recorded. A note is recorded at its last
    line, or later in an argument read whole (a caption), so a record is of the nearest such note above it in its
    file; failing that (a macro's use), of one in a macro's definition elsewhere, one per record. Another record (a
    list of contents, a running head) shows no note, so that a note in a false branch with the text of one shown
    stays hidden. None unless those files are as the pass read them (sources: source_digests), as what the notes
    say (their text, a macro in it, the words around them) may not be what the PDF shows otherwise."""
    texts = {}
    for path in project_tex_files(paper_root, fls_file(paper_root, pdf_name)):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            return None
        if not sources or sources.get(path) != hashlib.sha256(data).hexdigest():
            return None
        texts[path] = blank_comments(data.decode("utf-8", errors="replace"))
    kinds = note_kinds(texts.values())
    templates = {}  # (note macro: the argument it gives \todotxt, with #1 in it; the last definition, as note_kinds)
    for text in texts.values():
        for m in NOTE_DEF_RE.finditer(text):
            at = re.search(r"\\todotxt\s*(?=\{)", body := brace_group(text, m.end())[0])
            if m.group(1) in kinds and at:
                templates[m.group(1)] = brace_group(body, at.end())[0]
    by_digest = {}  # (digest: [(note, its last line, whether it is in a macro's definition)])
    for path, text in texts.items() if kinds else ():
        rel = os.path.relpath(path, paper_root)
        defined = [(m.end(), brace_group(text, m.end())[1]) for m in DEFINITION_RE.finditer(text)]
        for m in note_use_re(kinds).finditer(text):
            if m.group(1) not in templates:
                continue
            body, end = brace_group(text, m.end())
            note = (rel, text.count("\n", 0, m.start()) + 1, m.group(1), " ".join(body.replace("\x7f", " ").split()))
            by_digest.setdefault(typeset_digest(templates[m.group(1)].replace("#1", body)), []).append(
                (note, text.count("\n", 0, end) + 1, any(start < m.start() < stop for start, stop in defined)))
    shown, elsewhere = set(), []
    for path, line, digest in records:
        path = os.path.relpath(path, paper_root) if os.path.isabs(path) else path
        above = [(last, note) for note, last, _ in by_digest.get(digest, []) if note[0] == path and last <= line]
        if above:
            shown.add(max(above)[1])
        else:
            elsewhere.append(digest)
    for digest in elsewhere:
        note = next((note for note, _, in_macro in by_digest.get(digest, []) if in_macro and note not in shown), None)
        if note:
            shown.add(note)
    return shown


def shown_notes(server, pdf_name, found):
    """Of the notes found (notes, margin_notes), those that the published PDF shows, for viewers and commenters in
    shared mode: the notes its pass recorded typesetting (shown_tex; shared_build.shared_publish keeps the records
    with what it published), so that notes in a false branch (\\iffalse, a switch that is off), or all of them while
    \\todotxt hides them, stay with the editors, who read the sources anyway. None without records of the PDF on
    show (before the first build of a restarted instance, say), or while a source differs from what its pass read."""
    shared = getattr(server, "shared", None) or {}
    kept = shared.get("shown")
    try:
        st = os.stat(pdf_path(server.paper_root, pdf_name))
    except OSError:
        return []
    if pdf_name != server.default_pdf or not kept or kept["published"] != shared.get("published") \
            or kept["published"] != {"mtime": st.st_mtime_ns, "size": st.st_size}:
        return []
    shown = recorded_notes(server.paper_root, pdf_name, kept["records"], kept["sources"]) or set()
    return [n for n in found if (n.get("file"), n.get("line"), n.get("macro"),
                                 " ".join((n.get("tex") or "").replace("\x7f", " ").split())) in shown]


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
                          "points": spread(dict.fromkeys(where), turns), "side": side,
                          "before": before, "after": after, **note_fields(text, m, end)})
    return found


def spread(points, turns):
    """What the viewer reads of a note's points (margin_notes; viewer/notes.js anchorNote): its pages, the lines it
    spans on each (y on the page as shown, as the viewer turns it), the first point on each page, and the first and
    last points. So, in order, the first point of each page and y as shown, and the last point: a few per line of
    the paragraph rather than one per word and space."""
    points, seen, kept = list(points), set(), []
    for i, p in enumerate(points):
        line = (p[0], shown_point(turns.get(p[0]), p[1], p[2])[1])
        if line not in seen or i == len(points) - 1:
            seen.add(line)
            kept.append(list(p))
    return kept


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
