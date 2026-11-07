"""The review copy (/review): the exact build without notes, its pages unchanged on wider pages,
with each note typeset in a gutter beside its column (left of the page for a left column), numbered at
its spot in the text and wired to it. For coauthors who don't run latex-live. Beside it, the same pages
at their own size with each note as a PDF comment at its spot (annotated), to read and reply to in a PDF
reader."""

import json
import os
import re
import shutil
import subprocess
import threading
import time

from . import config
from .document import layout
from .export import exact_export, links_in, waiting_for_build
from .files import fls_file, job_file, mtime, pdf_path, project_tex_files, read_output, too_big
from .latex import blank_comments, brace_group, false_blocks, read_tex
from .notes import NOTE_DEF_RE, escape_note, note_kinds, note_use_re
from .review_anchors import BP, note_source, review_anchors, squeeze, synctex_pages
from .review_layout import comments_tex, review_geometry, review_layout, review_tex
from .tools import failure, image_pulled, run_tool, tex_argv


# Where the notes go comes from a probe: the last pass of the export again, with the same inputs,
# where \todotxt records the spot of each note it typesets (\pdfsavepos, written at shipout) instead
# of nothing. So that TeX finds the same line breaks, glue and margin kerning, the spaces, kerns and
# penalties before a note in a paragraph come off and go back after the record (in math, a record
# changes no spacing). A record counts only where its line (the innermost hbox) is typeset exactly as
# in the export, by their SyncTeX; other notes, and notes between paragraphs, are placed from the
# export's SyncTeX by source line instead. A note the probe never meets isn't typeset (it is in a
# conditional that is off, say) and is left out. After the last page, the probe also typesets every
# note as the paper shows it (its macros, colors, references), at two sizes, each on a page of its
# own, which the review copy places in the gutters as they are.
REVIEW_PROBE = r"""% latex-live: the review copy's probe (build_review in latexlive/review.py), read before the document.
\ifdefined\ReadonlyShipoutCounter\else\endinput\fi
\makeatletter
\def\latexlive@log#1{\immediate\write-1{latex-live|#1}}
\def\latexlive@here{\ifdefined\CurrentFile\CurrentFilePath/\CurrentFile\fi|\the\inputlineno}
\def\latexlive@strip{\let\latexlive@next\relax
  \ifnum\lastnodetype=11 \edef\latexlive@tail{\hskip\the\lastskip\relax\unexpanded\expandafter{\latexlive@tail}}%
    \unskip\let\latexlive@next\latexlive@strip\fi
  \ifnum\lastnodetype=12 \edef\latexlive@tail{\kern\the\lastkern\relax\unexpanded\expandafter{\latexlive@tail}}%
    \unkern\let\latexlive@next\latexlive@strip\fi
  \ifnum\lastnodetype=13 \edef\latexlive@tail{\penalty\the\lastpenalty\relax\unexpanded\expandafter{\latexlive@tail}}%
    \unpenalty\let\latexlive@next\latexlive@strip\fi
  \latexlive@next}
% A note's spot (page, position), written at shipout.
\long\def\latexlive@record#1{\pdfsavepos\edef\latexlive@write{\write-1{latex-live|pos|\latexlive@here
  |\noexpand\the\ReadonlyShipoutCounter|\noexpand\the\pdflastxpos|\noexpand\the\pdflastypos|\detokenize{#1}}}%
  \latexlive@write}
% In a paragraph, the spaces, kerns and penalties before the note go back after the record, so the
% lines break where they did (elsewhere nothing breaks, and they stay before it, as in a cell's template).
% A record stops \ignorespaces (or TeX's look for an optional space), which in the export goes on past
% the vanished note, so the record skips the spaces after it where that is likely: after rigid or
% infinite glue (a template's, or \@esphack's after \label) and at the start of a paragraph (after a
% display), not after a word's space.
\protected\long\def\latexlive@mark#1{\begingroup\def\latexlive@tail{}\def\latexlive@then{}%
  \ifhmode
    \ifnum\lastnodetype<\z@\ifinner\else\def\latexlive@then{\ignorespaces}\fi\fi
    \ifnum\lastnodetype=11
      \ifnum\gluestretchorder\lastskip>\z@\def\latexlive@then{\ignorespaces}\fi
      \ifdim\gluestretch\lastskip=\z@\ifdim\glueshrink\lastskip=\z@\def\latexlive@then{\ignorespaces}\fi\fi
    \fi
    \ifinner\else\latexlive@strip\fi
  \fi
  \latexlive@record{#1}\latexlive@tail\expandafter\endgroup\latexlive@then}
\protected\long\def\latexlive@seen#1{\latexlive@log{seen|\latexlive@here|\detokenize{#1}}}
% At the start of a cell, whose template has begun (in a p column, in vertical mode); in the export,
% the look ahead for \omit skipped the spaces after the note.
\protected\long\def\latexlive@cell#1{\ifvmode\latexlive@seen{#1}\else\latexlive@record{#1}\expandafter\ignorespaces\fi}
% Typesetting, not writing to a file or a mark. Where an alignment looks ahead for \noalign or \omit,
% the probe must not start a row or a cell that the export doesn't: between rows, the note is logged
% from a \noalign of its own; at the start of a cell, from a protected macro, which starts the cell
% there, as the note's text does in the build with notes (so \omit can't follow it in a paper that
% builds).
\long\def\latexlive@todotxt#1{\ifx\protect\@typeset@protect
  \ifvmode\ifnum\currentgrouptype=6 \noalign{\latexlive@seen{#1}}\else\latexlive@seen{#1}\fi
  \else\ifnum\ifnum\currentgrouptype=6 \lastnodetype\else0\fi<0 \latexlive@cell{#1}\else\latexlive@mark{#1}\fi\fi\fi}
\ifcsname tex_shipout:D\endcsname\expandafter\let\expandafter\latexlive@shipout\csname tex_shipout:D\endcsname
\else\let\latexlive@shipout\shipout\fi
\newbox\latexlive@box \newdimen\latexlive@wide \newcount\latexlive@page
\def\latexlive@lines{\let\latexlive@next\relax
  \ifnum\lastnodetype=11 \unskip\let\latexlive@next\latexlive@lines\fi
  \ifnum\lastnodetype=12 \unkern\let\latexlive@next\latexlive@lines\fi
  \ifnum\lastnodetype=13 \unpenalty\let\latexlive@next\latexlive@lines\fi
  \ifx\latexlive@next\relax\setbox\tw@\lastbox\ifvoid\tw@\else
    \ifhbox\tw@\setbox\tw@\hbox{\unhbox\tw@}\fi
    \ifdim\wd\tw@>\latexlive@wide\global\latexlive@wide\wd\tw@\fi\let\latexlive@next\latexlive@lines\fi\fi
  \latexlive@next}
\long\def\latexlive@size#1#2#3#4{%
  \setbox\latexlive@box\vtop{\hsize=<WIDTH>bp\relax\linewidth\hsize\columnwidth\hsize\textwidth\hsize
    \normalfont#3\relax\raggedright\parindent\z@\parskip\z@\leftskip\z@\everypar{}%
    \ifdefined\nolinenumbers\nolinenumbers\fi\leavevmode#4\par}%
  \global\latexlive@wide\z@\setbox\z@\vbox{\unvcopy\latexlive@box\latexlive@lines}%
  \global\advance\latexlive@page\@ne
  \latexlive@log{snippet|#1|#2|\the\latexlive@page|\the\latexlive@wide|\the\ht\latexlive@box|\the\dp\latexlive@box}%
  \ifdim\latexlive@wide<\wd\latexlive@box\global\latexlive@wide\wd\latexlive@box\fi
  \begingroup\pdfpagewidth\latexlive@wide\pdfpageheight\dimexpr\ht\latexlive@box+\dp\latexlive@box+1sp\relax
  \pdfhorigin\z@\pdfvorigin\z@\latexlive@shipout\box\latexlive@box\endgroup}
% \latexlivecite{key}{label}: a citation only in notes, which the export's bibliography leaves out.
\def\latexlive@cite#1#2{\global\expandafter\def\csname b@\detokenize{#1}\ifdefined\@extra@binfo\@extra@binfo\fi\endcsname{#2}}
\long\def\latexlive@snippet#1#2{\latexlive@size{#1}0\footnotesize{#2}\latexlive@size{#1}1\scriptsize{#2}}
\def\latexlive@color#1#2#3{\ifdefined\convertcolorspec\begingroup
  \ifx\relax#2\relax\extractcolorspecs{#3}\latexlive@m\latexlive@s\else\def\latexlive@m{#2}\def\latexlive@s{#3}\fi
  \convertcolorspec\latexlive@m\latexlive@s{rgb}\latexlive@rgb\latexlive@log{color|#1|\latexlive@rgb}\endgroup\fi}
\def\latexlive@notes{\begingroup
  \latexlive@log{pages|\the\ReadonlyShipoutCounter}\global\latexlive@page\ReadonlyShipoutCounter
  \long\def\todotxt##1{##1}\ifdefined\showtodostrue\showtodostrue\fi
  \let\latexlivesnippet\latexlive@snippet\let\latexlivecolor\latexlive@color\let\latexlivecite\latexlive@cite
  \input{<NOTES>}\endgroup}
\AddToHook{enddocument/afterlastpage}{\latexlive@notes}
\AtBeginDocument{\ifdefined\todotxt\let\todotxt\latexlive@todotxt\fi\ifdefined\showtodosfalse\showtodosfalse\fi
  \ifdefined\xspaceaddexceptions\xspaceaddexceptions{\latexlive@mark}\fi}% (\xspace looks past a note it expands to nothing)
\makeatother
"""


def start_review(server):
    """/review: the review copy of the current build (build_review), for downloading as
    build/<doc>-review.pdf. Built once per build of the document; until then, its state."""
    pdf = server.default_pdf
    if not os.path.isfile(pdf_path(server.paper_root, pdf)):
        return 409, {"state": "failed", "msg": f"{pdf} has not built yet"}
    with server.download_lock:  # (from however many viewers at once: one build)
        r = server.review
        if waiting := waiting_for_build(server, pdf, r):
            return 200, waiting
        source = str(mtime(pdf_path(server.paper_root, pdf)))
        if r.get("pdf") == pdf and (r.get("state") == "running" or r.get("source") == source):  # (a failure too, until the next build)
            return 200, r
        server.review = {"pdf": pdf, "source": source, "state": "running", "step": "the exact build without notes",
                         "time": time.strftime("%H:%M:%S"), "msg": ""}
        threading.Thread(target=build_review, args=(server, pdf, source), daemon=True).start()
        return 200, server.review


def build_review(server, pdf, source):
    """build/<doc>-review.pdf (see REVIEW_PROBE), built in build/review/<doc>/: the exact export,
    the probe (one pdflatex pass), the layout of the gutters, and a document that places the
    export's pages and the typeset notes with TikZ (one more pass, in .copy/ there: a folder that
    holds only that document and the two PDFs it places). The layout also goes to <doc>-review.json
    there. Then build/<doc>-annotated.pdf, a document there that places the export's pages at their
    own size with the notes as PDF comments (comments_tex: one more pass). Published whole or not at all."""
    root, doc = server.paper_root, pdf[:-4]
    out = os.path.join(root, config.BUILD, "review", doc)
    job = f"{doc}-export"  # (the probe repeats the export's last pass, \jobname included)

    def step(text):
        with server.download_lock:  # (not over a newer review's state: start_review)
            if server.review.get("pdf") == pdf and server.review.get("source") == source:
                server.review = {**server.review, "step": text}

    def keep(export):  # the export's PDF and SyncTeX, and the inputs of its last pass, for the probe
        shutil.copyfile(os.path.join(root, config.BUILD, f"{job}.pdf"), os.path.join(out, "export.pdf"))
        shutil.copyfile(os.path.join(export, f"{job}.synctex.gz"), os.path.join(out, "export.synctex.gz"))
        shutil.copytree(os.path.join(export, "inputs"), out, dirs_exist_ok=True, ignore=links_in)

    with server.review_lock:  # (one at a time: they share build/review/<doc>/)
        try:
            if config.SHARED and not image_pulled():  # (said plainly, rather than as a pdflatex failure with no log)
                raise RuntimeError(f"{config.IMAGE_MISSING}; until then there is no copy with notes in the margin"
                                   " (never the host's TeX here)")
            notes = review_notes(root, pdf)
            if not notes:
                raise RuntimeError("the paper has no notes to show (resolved ones and protected markers don't count)")
            shutil.rmtree(out, ignore_errors=True)
            os.makedirs(out)
            exact_export(server, pdf, keep)
            step("placing the notes")
            area = layout(root, pdf) or {"W": 612, "H": 792, "left": 72, "top": 72, "bottom": 720, "width": 468, "columns": 1}
            geo = review_geometry(area)
            probe = run_probe(server, out, job, doc, notes, geo)
            placed = review_anchors(root, out, notes, probe, area)
            if not placed:
                raise RuntimeError("none of the notes is typeset (are they all in parts the document leaves out?)")
            pages = len(synctex_pages(os.path.join(out, "export.synctex.gz")))
            plan = review_layout(placed, probe, geo, pages)
            step("typesetting the review copy")
            with open(os.path.join(out, f"{doc}-review.json"), "w") as f:
                json.dump(plan, f, indent=1)
            # (TeX takes packages from its working folder first, and the paper's TeX writes into out: the last pass
            # runs in a folder made only now, with a name that starts with a dot, which no TeX run of the paper's can
            # write into either, as openout_any=p refuses it)
            copy = os.path.join(out, ".copy")
            os.makedirs(copy)
            for name in ("export.pdf", "probe.pdf"):
                if os.path.exists(os.path.join(out, name)):
                    os.replace(os.path.join(out, name), os.path.join(copy, name))

            def typeset(job, source, pages, what):
                with open(os.path.join(copy, f"{job}.tex"), "w", encoding="utf-8") as f:
                    f.write(source)
                code, _ = run_tool(tex_argv(server, "pdflatex", "-interaction=batchmode", "-halt-on-error", f"{job}.tex"), copy,
                                   kind="review")
                if code is None:
                    raise subprocess.TimeoutExpired("pdflatex", 600)
                log = read_output(root, os.path.join(copy, f"{job}.log")) or ""
                written = re.search(r"Output written on .*?\((\d+) pages?", log.replace("\n", ""))
                if code != 0 or not written or int(written.group(1)) != pages:
                    first = next((l for l in log.splitlines() if l.startswith("! ")), "")
                    raise RuntimeError(f"{what} didn't build ({first or f'see {os.path.relpath(copy, root)}/{job}.log'})")
            colors = probe.get("colors", {})
            typeset(f"{doc}-review", review_tex(plan, geo, colors, notes), plan["pages"], "the review copy")
            typeset(f"{doc}-annotated", comments_tex(plan, colors, time.strftime("%Y%m%d%H%M%S")), plan["paper"],
                    "the copy with the notes as comments")
            os.replace(os.path.join(copy, f"{doc}-annotated.pdf"), os.path.join(root, config.BUILD, f"{doc}-annotated.pdf"))
            os.replace(os.path.join(copy, f"{doc}-review.pdf"), os.path.join(root, config.BUILD, f"{doc}-review.pdf"))
            state = {"state": "done", "version": str(mtime(os.path.join(root, config.BUILD, f"{doc}-review.pdf"))),
                     "notes": len(plan["notes"]), "approximate": sum(n["how"] != "exact" for n in plan["notes"]),
                     "continued": sum(n["top"] is None for n in plan["notes"])}
        except Exception as e:  # (shown in the viewer)
            state = {"state": "failed", "msg": failure(server, e)}
    with server.download_lock:  # (not over a newer review's state: start_review)
        if server.review.get("pdf") == pdf and server.review.get("source") == source:
            server.review = {**server.review, **state}


def review_notes(paper_root, pdf_name):
    """The notes the review copy shows, in source order: [{id, macro, author, file, line, col, last,
    call, body, typeset}]: not resolved notes (commented out), notes in \\iffalse blocks, notes inside
    another note (they show in it), or LATEX_LIVE_PROTECTED markers. call is the note as written and
    body its argument, as Latin-1 (the file's bytes as they are); typeset is what its macro hands to
    \\todotxt, squeezed (to tell apart notes of two authors with the same text), or None."""
    files = project_tex_files(paper_root, fls_file(paper_root, pdf_name))
    texts = [read_tex(p) for p in files]
    kinds = note_kinds(texts)
    if not kinds:
        return []
    templates = {}  # (note macro: the argument it gives \todotxt, with #1 in it)
    for text in texts:
        for m in NOTE_DEF_RE.finditer(text):
            body = brace_group(text, m.end())[0]
            at = re.search(r"\\todotxt\s*(?=\{)", body)
            if at:
                templates[m.group(1)] = brace_group(body, at.end())[0]
    use, found = note_use_re(kinds), []
    for path in files:
        try:
            with open(path, "rb") as f:
                raw = f.read().decode("latin-1")
        except OSError:
            continue
        text = blank_comments(raw)
        for start, end in false_blocks(text):
            text = text[:start] + re.sub(r"[^\n]", " ", text[start:end]) + text[end:]
        inside = 0
        for m in use.finditer(text):
            if m.start() < inside:
                continue
            inside = end = brace_group(text, m.end())[1]
            if config.PROTECTED_RE and config.PROTECTED_RE.search(text[m.start():end]):
                continue
            body = raw[m.end() + 1:end - 1]
            template = templates.get(m.group(1))
            found.append({"id": len(found) + 1, "macro": m.group(1), "author": kinds[m.group(1)],
                          "file": os.path.relpath(path, paper_root), "line": text.count("\n", 0, m.start()) + 1,
                          "col": m.start() - text.rfind("\n", 0, m.start()) - 1, "last": text.count("\n", 0, end) + 1,
                          "call": raw[m.start():end], "body": body,
                          "typeset": squeeze(note_source(template.replace("#1", body))) if template is not None else None})
    return found


def run_probe(server, out, job, doc, notes, geo):
    """The probe pass (REVIEW_PROBE) in out/, from the inputs of the export's last pass. Returns what
    its log records: {pages, pos: [[file, line, page, x, y, tex]], seen: [[file, line, tex]],
    snippets: {id: {size: [page, width, height, depth]}}, colors: {macro: [r, g, b]}}, with x, y in
    points from the page's top left, and sizes in points (width: of the widest line, which may be less
    than the page's, or more than the width set); {} if it didn't get as far as the notes. Raises
    RuntimeError for a log past OUTPUT_LIMIT."""
    rel = os.path.relpath(out, server.paper_root)
    with open(os.path.join(out, "probe.tex"), "w") as f:
        f.write(REVIEW_PROBE.replace("<WIDTH>", f"{geo['snippet']:.2f}").replace("<NOTES>", f"{rel}/notes"))
    with open(os.path.join(out, "notes.tex"), "w", encoding="latin-1") as f:
        for macro, (model, spec) in note_colors(server.paper_root, f"{doc}.pdf", {n["macro"] for n in notes}).items():
            f.write(f"\\latexlivecolor{{{macro}}}{{{model}}}{{{spec}}}\n")
        for key, label in note_citations(server.paper_root, f"{doc}.pdf", os.path.join(out, f"{job}.bbl"), notes).items():
            f.write(f"\\latexlivecite{{{key}}}{{{label}}}\n")
        for n in notes:
            f.write(f"\\latexlivesnippet{{{n['id']}}}{{{n['call']}}}\n")
    code, _ = run_tool(tex_argv(server, "pdflatex", "-interaction=batchmode", "-file-line-error", "-synctex=1",
                                f"-output-directory={out}", f"-jobname={job}", f"\\input{{{rel}/probe}}\\input{{{doc}}}"),
                       server.paper_root, kind="probe", env={**os.environ, "max_print_line": "1000000"})
    if code is None:
        return {}
    probe = {"pos": [], "seen": [], "snippets": {}, "colors": {}, "synctex": os.path.join(out, f"{job}.synctex.gz")}
    log = os.path.join(out, f"{job}.log")
    try:
        if os.path.getsize(log) > config.OUTPUT_LIMIT:
            raise RuntimeError(too_big(os.path.relpath(log, server.paper_root), config.OUTPUT_LIMIT))
        with open(log, "rb") as f:  # (only its records, and the line that says how many pages it wrote)
            lines = [line.decode("latin-1").rstrip("\r\n") for line in f if line.startswith((b"latex-live|", b"Output written on "))]
    except OSError:
        return {}
    for line in lines:
        if not line.startswith("latex-live|"):
            continue
        kind, *fields = line[len("latex-live|"):].split("|")
        try:
            if kind == "pos":
                path, ln, page, x, y = fields[:5]
                probe["pos"].append([os.path.normpath(path.lstrip("/")), int(ln), int(page), int(x) / 65536 * BP,
                                     geo["H"] - int(y) / 65536 * BP, "|".join(fields[5:])])
            elif kind == "seen":
                probe["seen"].append([os.path.normpath(fields[0].lstrip("/")), int(fields[1]), "|".join(fields[2:])])
            elif kind == "snippet":
                size = [float(v[:-2]) * BP for v in fields[3:6]]
                probe["snippets"].setdefault(int(fields[0]), {})[int(fields[1])] = [int(fields[2]), *size]
            elif kind == "color":
                probe["colors"][fields[0]] = [round(float(v), 4) for v in fields[1].split(",")]
            elif kind == "pages":
                probe["pages"] = int(fields[0])
        except (ValueError, IndexError):
            continue
    if "pages" not in probe:  # (it stopped before the notes: nothing it recorded can be trusted to be complete)
        return {}
    written = next(filter(None, (re.match(r"Output written on .*?\((\d+) pages?", line) for line in lines)), None)
    probe["pdf"] = os.path.isfile(os.path.join(out, f"{job}.pdf")) and bool(written) and int(written.group(1)) == (
        probe["pages"] + sum(len(s) for s in probe["snippets"].values()))
    if probe["pdf"]:
        os.replace(os.path.join(out, f"{job}.pdf"), os.path.join(out, "probe.pdf"))
    return probe


CITE_RE = re.compile(r"\\[a-zA-Z]*cite[a-zA-Z]*\*?\s*(?:\[[^\]]*\]\s*){0,2}\{([^{}]*)\}")
BIBITEM_RE = re.compile(r"\\bibitem\s*(?:\[(?:[^\[\]]|\[[^\]]*\])*\])?\s*\{([^{}]*)\}")


def note_citations(paper_root, pdf_name, bbl, notes):
    """{key: label} for the citations in notes that the export's bibliography (bbl) leaves out, being
    cited only in notes: the label they have in the build with notes, as natbib's authors and year
    (their number, or a numeric label, would point to another entry of the export's bibliography),
    else their key. As Latin-1, like the notes."""
    bibliography = read_output(paper_root, bbl, "latin-1")
    if bibliography is None:  # (no bibliography from BibTeX: \bibitem in the document, notes or not)
        return {}
    listed = set(BIBITEM_RE.findall(bibliography))
    keys = {k.strip() for n in notes for m in CITE_RE.finditer(n["body"]) for k in m.group(1).split(",")} - listed - {""}
    aux = read_output(paper_root, job_file(paper_root, pdf_name, ".aux"), "latin-1") or ""
    labels = {}
    for m in re.finditer(r"\\bibcite\{([^{}]*)\}(?=\{)", aux):
        labels[m.group(1)] = brace_group(aux, m.end())[0]
    found = {}
    for key in sorted(keys):
        label, name = labels.get(key, ""), escape_note(key)
        if label.startswith("{") and brace_group(label, 0)[1] < len(label):  # (natbib: {number}{year}{{authors}}{{full}})
            found[key] = "{" + name + "}" + label[brace_group(label, 0)[1]:]
        else:
            found[key] = label if label and not label.strip().isdigit() else name
    return found


def note_colors(paper_root, pdf_name, macros):
    """{note macro: (model, color)}: the first color its definition sets (\\color or \\textcolor),
    e.g. ("", "Emerald") or ("rgb", "0.1,0.5,0.2"), for its number and wire."""
    found = {}
    for path in project_tex_files(paper_root, fls_file(paper_root, pdf_name)):
        text = read_tex(path)
        for m in NOTE_DEF_RE.finditer(text):
            color = re.search(r"\\(?:text)?color\s*(?:\[([^\]]*)\])?\s*\{([^{}]*)\}", brace_group(text, m.end())[0])
            if m.group(1) in macros and m.group(1) not in found and color:
                found[m.group(1)] = (color.group(1) or "", color.group(2))
    return found
