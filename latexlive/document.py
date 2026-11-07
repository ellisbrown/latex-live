"""The document as built: its page layout and page limit, its outline, its figures and tables, and
the graphics it includes, from the build's log, .aux and SyncTeX file."""

import collections
import os
import re
import subprocess

from . import config
from .files import (
    child_aux, clean_file, file_hash, fls_file, job_file, mtime, project_inputs, project_tex_files, read_capped, shown_synctex)
from .git import git_read_env, pushed_commit
from .latex import brace_group, project_macros, read_tex, tex_to_text
from .synctex import synctex_boxes


LAYOUT_RE = re.compile(r"^latex-live layout: (.+)$", re.M)


def layout(paper_root, pdf_name, cache={}):
    """The text area of the last build, as latexmkrc's pretex hook printed it to the log, in PDF points
    from the page's top-left: {W, H, left, top, bottom, width, colwidth, leading, columns}. None until a build has
    printed it. A build that stopped before \\begin{document} keeps the previous value."""
    log = job_file(paper_root, pdf_name, ".log")
    stamp = mtime(log)
    if cache.get(log, (None,))[0] != stamp:
        m = LAYOUT_RE.search(read_capped(log) or "")  # (the line comes once the preamble has run: near the start)
        value = cache.get(log, (None, None))[1]
        if m:
            try:  # TeX points (72.27 per inch) to PDF points (72); glue keeps only its natural size
                pw, ph, hoff, side, voff, top, head, sep, tw, th, cw, skip = (
                    float(re.match(r"-?[\d.]+", v.strip()).group()) * 72 / 72.27 for v in m.group(1).split(","))
                y = 72 + voff + top + head + sep  # TeX's origin is 1in from the top-left corner
                value = {"W": round(pw, 2), "H": round(ph, 2), "left": round(72 + hoff + side, 2), "top": round(y, 2),
                         "bottom": round(y + th, 2), "width": round(tw, 2), "colwidth": round(cw, 2), "leading": round(skip, 2),
                         "columns": 2 if cw < 0.75 * tw else 1}
            except (AttributeError, ValueError):
                pass
        cache[log] = (stamp, value)
    return cache[log][1]


# Main-text page limits of submission styles, for documents run without --page-limit.
# The main text's page limit of a venue's style (ACL's for long papers; ECCV's without the references), and the
# unnumbered sections, beyond the usual ones (pages.js UNCOUNTED), that a venue doesn't count either.
VENUE_PAGE_LIMITS = {r"iclr\d{4}_conference": 9, r"neurips_\d{4}": 9, r"colm\d{4}_conference": 9, r"icml\d{4}": 8,
                     r"cvpr": 8, r"iccv": 8, r"wacv": 8, r"acl(\d{4})?": 8, r"aaai\d{2,4}": 7, r"eccv": 14}
VENUE_UNCOUNTED = {r"acl(\d{4})?": ["limitations"]}
USEPACKAGE_RE = re.compile(r"\\usepackage\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")


def venue_style(server, pdf_name, table):
    """The value in table (VENUE_PAGE_LIMITS, VENUE_UNCOUNTED) of the venue style the document loads, else None."""
    packages = {p.strip() for m in USEPACKAGE_RE.finditer(read_tex(os.path.join(server.paper_root, pdf_name[:-4] + ".tex")))
                for p in m.group(1).split(",")}
    return next((v for pattern, v in table.items() if any(re.fullmatch(pattern, p) for p in packages)), None)


def page_limit(server, pdf_name):
    """--page-limit, else the limit of the venue style the document loads, else None."""
    return server.page_limit or venue_style(server, pdf_name, VENUE_PAGE_LIMITS)


def uncounted_sections(server, pdf_name):
    """The unnumbered sections that the venue style the document loads leaves out of the main text, beyond the usual
    ones (the viewer's UNCOUNTED): their headings' first words, lowercase."""
    return venue_style(server, pdf_name, VENUE_UNCOUNTED) or []


TOC_LEVELS = {"part": 0, "chapter": 0, "section": 1, "subsection": 2, "subsubsection": 3, "paragraph": 4}


def contents_lines(text):
    """The \\contentsline{type}{title}{page label}{anchor} entries in text, as argument lists."""
    found, i = [], 0
    while (i := text.find("\\contentsline", i)) >= 0:
        args, i = [], i + len("\\contentsline")
        while len(args) < 4 and (j := text.find("{", i)) >= 0 and not text[i:j].strip():
            arg, i = brace_group(text, j)
            args.append(arg)
        if len(args) == 4:
            found.append(args)
    return found


WRITTEN_RE = re.compile(r"\\@writefile\{(toc|lof|lot)\}(?=\{)|\\@input\{([^}]+\.aux)\}")


def written_contents(aux, ext, depth=0, budget=None):
    """The contents lines a build wrote for its .toc, .lof or .lot (ext) through its .aux, and the
    .aux files that one inputs (\\include). Every build writes them, whether or not the document
    prints that list. The paper's TeX writes those names: only .aux files in the same folder or below
    it are read (child_aux), AUX_BUDGET bytes in all."""
    budget = [config.AUX_BUDGET, 200] if budget is None else budget
    text = read_capped(aux, min(config.READ_LIMIT, budget[0])) or ""
    budget[0] -= len(text)
    found = []
    for m in WRITTEN_RE.finditer(text):
        if m.group(1) == ext:
            found += contents_lines(brace_group(text, m.end())[0])
        elif m.group(2) and depth < 3 and budget[0] > 0 and budget[1] > 0 and (child := child_aux(os.path.dirname(aux), m.group(2))):
            budget[1] -= 1
            found += written_contents(child, ext, depth + 1, budget)
    return found


def numbered(arg, macros):
    """(number, text) of a contents-line title: \\numberline{2.1}Title -> ("2.1", "Title")."""
    m = re.match(r"\s*\\numberline\s*\{([^{}]*)\}", arg)
    text = tex_to_text(arg[m.end():] if m else arg, macros)
    return (m.group(1).strip() or None) if m else None, re.sub(r" ([:;,.?!)])", r"\1", " ".join(text.split()))


def toc_outline(paper_root, pdf_name, clean=False):
    """Outline entries [{level, num, title, page, dest}] (of the notes-free build with clean=True,
    whose pages differ). The viewer numbers the PDF's bookmarks with them, and shows them as the
    outline of a PDF without bookmarks.

    From the contents lines the build wrote to its .aux, else its .toc. The anchor (a named
    destination) is empty when hyperref doesn't handle the entry; the viewer then finds the title
    on the page.
    """
    f = clean_file if clean else job_file
    lines = written_contents(f(paper_root, pdf_name, ".aux"), "toc")
    if not lines:
        text = read_capped(f(paper_root, pdf_name, ".toc"))
        if text is None:
            return []
        lines = contents_lines(text)
    macros = project_macros(paper_root, fls_file(paper_root, pdf_name))
    entries = []
    for kind, title, page, dest in lines:
        if kind in TOC_LEVELS:
            num, title = numbered(title, macros)
            entries.append({"level": TOC_LEVELS[kind], "num": num, "title": title, "page": page, "dest": dest or None})
    return entries


FLOAT_ENV_RE = re.compile(r"\\(begin|end)\s*\{((?:sideways|wrap)?(?:figure|table))\*?\}")
CAPTION_RE = re.compile(r"\\caption\s*(?=[\[{])")  # (not \captionof or \subcaption)
LABEL_RE = re.compile(r"\\label\s*\{([^{}]*)\}")
NEWLABEL_RE = re.compile(r"\\newlabel\{([^{}]*)\}\{\{([^{}]*)\}")


SCALED_RE = re.compile(r"\\(?:resizebox|scalebox|rotatebox|reflectbox|adjustbox)\b|\\begin\{adjustbox\}")
ARG_RE = re.compile(r"\s*([\[{])")


def scaled_lines(env, first):
    """Lines of a float's environment (env, from line first) that end content drawn scaled or rotated
    (\\resizebox, \\scalebox, adjustbox...), whose SyncTeX boxes have its size before scaling."""
    lines = set()
    for m in SCALED_RE.finditer(env):
        i = m.end()
        if m.group().startswith("\\begin"):
            i = max(env.find("\\end{adjustbox}", i), i)
        else:  # to the end of its arguments
            while a := ARG_RE.match(env, i):
                i = brace_group(env, a.end(1) - 1)[1] if a.group(1) == "{" else (env.find("]", a.end()) + 1 or len(env))
        lines.update(range(first + env.count("\n", 0, m.start()), first + env.count("\n", 0, i) + 1))
    return lines


def floats(paper_root, pdf_name, clean=False):
    """The figures and tables that were typeset, in reading order, for the viewer's Figures tab and
    the placement check:
    [{kind, num, caption, page, label, dest, box, extent, keys, file, line, last}]. page is the PDF
    page, label its printed number; box is [left, top, right, bottom] in points from the page's
    top-left, cut at the page's bottom edge; extent is the box as typeset (not cut, nor widened by
    content drawn scaled), if known; keys are its \\label names; line and last are the lines of
    its environment in file.

    The environments come from the source; SyncTeX tells which were typeset and where (the boxes
    their lines made). Numbers come from their \\label, else the order of their captions; captions
    and anchors from the list of figures and tables the build wrote to its .aux.
    """
    f, synctex_gz = clean_file if clean else job_file, shown_synctex(paper_root, pdf_name, clean)
    if not os.path.exists(synctex_gz):
        return []
    boxes = synctex_boxes(synctex_gz)
    macros = project_macros(paper_root, fls_file(paper_root, pdf_name))
    area = layout(paper_root, pdf_name) or {"W": 612, "H": 792, "top": 72, "bottom": 720}
    tall = area["bottom"] - area["top"] - 1  # (no float is as tall as the text block)
    listed = {}
    for kind, ext in (("figure", "lof"), ("table", "lot")):
        listed[kind] = []
        for typ, title, page, dest in written_contents(f(paper_root, pdf_name, ".aux"), ext):
            if typ == kind:
                num, caption = numbered(title, macros)
                listed[kind].append({"num": num, "caption": caption, "label": page, "dest": dest or None})
    label_nums = dict(NEWLABEL_RE.findall(read_capped(f(paper_root, pdf_name, ".aux")) or ""))
    found, taken = [], {"figure": 0, "table": 0}
    for path in project_tex_files(paper_root, fls_file(paper_root, pdf_name)):
        text, open_at = read_tex(path), []
        for m in FLOAT_ENV_RE.finditer(text):
            if m.group(1) == "begin":
                open_at.append(m.start())
                continue
            if not open_at:
                continue
            start = open_at.pop()
            if open_at:  # (a float inside another one: part of it)
                continue
            first, last = text.count("\n", 0, start) + 1, text.count("\n", 0, m.end()) + 1
            made = [b for line in range(first, last + 1) for b in boxes.get((path, line), [])
                    # Not the page, its text block, header and footer (a page shipped out while one of these
                    # lines was current), nor an unscaled image or an empty box. A float may start low and
                    # run off the page.
                    if b[1] >= 1 and b[3] <= area["W"] and area["top"] - 15 <= b[2] <= area["bottom"] + 15
                    and 0 < b[4] - b[2] < tall and b[3] > b[1]]
            if not made:  # not typeset (e.g. in an \\iffalse block)
                continue
            kind = "table" if m.group(2).endswith("table") else "figure"
            env = text[start:m.end()]
            captions = len(CAPTION_RE.findall(env))
            entry = listed[kind][taken[kind]] if captions and taken[kind] < len(listed[kind]) else {}
            taken[kind] += captions
            nums = [label_nums[name] for name in LABEL_RE.findall(env) if name in label_nums]
            if nums and nums[0] != entry.get("num"):  # the label knows better than the order
                entry = next((e for e in listed[kind] if e["num"] == nums[0]), {"num": nums[0]})
            page = collections.Counter(b[0] for b in made).most_common(1)[0][0]
            on = [b for b in made if b[0] == page]
            skip = scaled_lines(env, first)
            sized = [] if m.group(2).startswith("sideways") else [  # (rotated: SyncTeX has the boxes unrotated)
                b for line in range(first, last + 1) if line not in skip for b in boxes.get((path, line), []) if b in on]
            found.append({"kind": kind, "num": entry.get("num"), "caption": entry.get("caption", ""), "page": page,
                          "label": entry.get("label"), "dest": entry.get("dest"),
                          "box": [min(b[1] for b in on), min(b[2] for b in on), max(b[3] for b in on),
                                  min(area["H"], max(b[4] for b in on))],  # (a float can run off the page)
                          "extent": sized and [min(b[1] for b in sized), min(b[2] for b in sized), max(b[3] for b in sized),
                                               max(b[4] for b in sized)] or None, "keys": LABEL_RE.findall(env),
                          "file": os.path.relpath(path, paper_root), "line": first, "last": last})
    return sorted(found, key=lambda x: (x["page"], x["box"][1]))


GRAPHIC_EXTS = (".pdf", ".png", ".jpg", ".jpeg", ".eps", ".svg")
INCLUDE_RE = re.compile(r"\\(includegraphics|includesvg)\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}|\\(input|include)\s*\{([^}]*)\}")


def include_sites(paper_root, pdf_name):
    """Where each included graphic or \\input file is included: {relative path: (tex file, line)}."""
    fls = fls_file(paper_root, pdf_name)
    inputs = project_inputs(paper_root, fls)
    stems = {os.path.splitext(p)[0]: p for p in inputs}
    sites = {}
    for path in project_tex_files(paper_root, fls):
        text = read_tex(path)
        for m in INCLUDE_RE.finditer(text):
            arg = os.path.normpath((m.group(2) or m.group(4)).strip())
            arg = os.path.splitext(arg)[0] if arg.lower().endswith(GRAPHIC_EXTS + (".tex",)) else arg
            # The argument may be relative to a \graphicspath directory: match it as a path suffix.
            target = stems.get(arg) or next((p for s_, p in stems.items() if s_.endswith("/" + arg)), None)
            if target and target not in sites:
                sites[target] = (os.path.relpath(path, paper_root), text.count("\n", 0, m.start()) + 1)
    return sites


def graphics(paper_root, pdf_name):
    """The graphics the build included: [{file, hash, dirty, unpushed, src, line}] (src:line is the
    \\includegraphics; dirty: changed since HEAD, or untracked; unpushed: likewise since the last
    push). The viewer compares hashes across rebuilds to mark figures whose files changed, or marks
    the dirty (unpushed) ones against HEAD (the last push)."""
    git = ["git", "-C", paper_root]
    changed = lambda rev: set(subprocess.run(git + ["diff", "--name-only", "--relative", rev], capture_output=True, text=True,
                                             env=git_read_env()).stdout.splitlines())
    untracked = set(subprocess.run(git + ["ls-files", "-o", "--exclude-standard"], capture_output=True, text=True).stdout.splitlines())
    dirty = changed("HEAD") | untracked
    pushed = pushed_commit(paper_root)[0]
    unpushed = changed(pushed) | untracked if pushed else set()
    found = []
    for path, (src, line) in include_sites(paper_root, pdf_name).items():
        if path.lower().endswith(GRAPHIC_EXTS):
            try:
                found.append({"file": path, "hash": file_hash(os.path.join(paper_root, path)), "dirty": path in dirty,
                              "unpushed": path in unpushed, "src": src, "line": line})
            except OSError:
                pass
    return found
