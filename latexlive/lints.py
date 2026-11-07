"""Consistency checks: generated figures older than their scripts, the bibliography's duplicates
and venue spellings, terms and reference styles written several ways, typos, and figures and tables
placed or referenced badly."""

import collections
import os
import re
import subprocess

from . import config
from .document import GRAPHIC_EXTS, floats, include_sites, layout
from .files import fls_file, in_paper, job_file, project_tex_files, read_capped, shown_synctex
from .latex import parse_bib, project_macros, read_tex, tex_to_text
from .notes import blank_notes, note_kinds
from .synctex import synctex_points
from .tools import pdf_pages_text


def git_times(paper_root):
    """When each file last changed: {relative path: unix time}, from git history, or the file's
    mtime when it has uncommitted edits or is untracked (checkouts reset mtimes, commits don't)."""
    git = ["git", "-C", paper_root]
    times = {}
    log = subprocess.run(git + ["log", "-n", "3000", "--format=@%ct", "--name-only", "--no-renames", "--relative"],
                         capture_output=True, text=True).stdout
    when = 0
    for line in log.splitlines():
        if line.startswith("@"):
            when = int(line[1:])
        elif line and line not in times:
            times[line] = when
    dirty = subprocess.run(git + ["ls-files", "-m", "-o", "--exclude-standard"], capture_output=True, text=True).stdout.split("\n")
    for path in filter(None, dirty):
        try:
            times[path] = os.path.getmtime(os.path.join(paper_root, path))
        except OSError:
            times.pop(path, None)  # deleted
    return times, set(dirty)


SCRIPT_EXTS = (".py", ".r", ".jl", ".m", ".ipynb")
DATA_RE = re.compile(r"""["']([^"'\s{}]+\.(?:json|jsonl|csv|tsv|parquet|npz|npy|pkl|pickle|yaml|yml|txt))["']""")


def stale_outputs(paper_root, pdf_name):
    """Generated figures and tables that are older than the script that makes them or its data:
    [{output, source, lag (s), dirty (the newer source has uncommitted edits), site (tex, line)}].

    A script makes an output if it mentions the output's name (e.g. save_figure(fig, "name")) or
    the output's name starts with the script's name. Data files are the string literals in the
    script that name existing data files.
    """
    sites = include_sites(paper_root, pdf_name)
    outputs = [p for p in sites if p.lower().endswith(GRAPHIC_EXTS) or p.startswith("tables" + os.sep)]
    if not outputs:
        return []
    skip = {".git", ".venv", "venv", "build", config.BUILD.split(os.sep)[0], "node_modules", "archive", "__pycache__"}
    scripts = {}
    for d, dirs, files in os.walk(paper_root):
        dirs[:] = [x for x in dirs if x not in skip and not x.startswith(".")]
        for f in files:
            if f.lower().endswith(SCRIPT_EXTS):
                path = os.path.join(d, f)
                try:
                    with open(path, errors="replace") as fh:
                        scripts[os.path.relpath(path, paper_root)] = fh.read()
                except OSError:
                    pass
    if not scripts:
        return []
    times, dirty = git_times(paper_root)
    data_files = {}
    for d, dirs, files in os.walk(paper_root):
        dirs[:] = [x for x in dirs if x not in skip and not x.startswith(".")]
        for f in files:
            data_files.setdefault(f, os.path.relpath(os.path.join(d, f), paper_root))
    stale = []
    for out in outputs:
        stem = os.path.splitext(os.path.basename(out))[0]
        mention = re.compile(r"(?<![\w-])%s(?![\w-])" % re.escape(stem))
        makers = [s_ for s_, text in scripts.items()
                  if mention.search(text) or stem.startswith(os.path.splitext(os.path.basename(s_))[0] + "_")]
        if not makers or out not in times:
            continue
        newest = None
        for script in makers:
            sources = [script] + [data_files[os.path.basename(lit)] for lit in DATA_RE.findall(scripts[script])
                                  if os.path.basename(lit) in data_files]
            for src in sources:
                if src in times and times[src] > times[out] + 120 and (not newest or times[src] > times[newest]):
                    newest = src
        if newest:
            stale.append({"output": out, "source": newest, "lag": times[newest] - times[out],
                          "dirty": newest in dirty, "site": sites[out]})
    return stale


VENUES = {
    "NeurIPS": r"neurips|nips\b|neural information processing",
    "ICLR": r"iclr|international conference on learning representations",
    "ICML": r"icml|international conference on machine learning",
    "CVPR": r"cvpr|computer vision and pattern recognition",
    "ICCV": r"iccv|international conference on computer vision",
    "ECCV": r"eccv|european conference on computer vision",
    "ACL": r"^acl\b|annual meeting of the association for computational linguistics",
    "EMNLP": r"emnlp|empirical methods in natural language processing",
    "NAACL": r"naacl|north american chapter",
    "TMLR": r"tmlr|transactions on machine learning research",
    "COLM": r"colm|conference on language modeling",
    "AAAI": r"aaai",
    "TPAMI": r"pami|pattern analysis and machine intelligence",
}
ORDINALS = r"\b(?:\w+-)?(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth|\w+teenth|\w+tieth|\d+(?:st|nd|rd|th))\b"


def bib_consistency(paper_root, pdf_name):
    """Cited entries that are the same paper under two keys, and venues written several ways:
    (duplicates, venues), lists of check items."""
    aux, blg = read_capped(job_file(paper_root, pdf_name, ".aux")), read_capped(job_file(paper_root, pdf_name, ".blg"))
    if aux is None or blg is None:
        return [], []
    cited = {k.strip() for group in re.findall(r"\\citation\{([^}]*)\}", aux) for k in group.split(",")}
    bibs = re.findall(r"^Database file #\d+: (.+)$", blg, re.M)
    entries = []
    for bib in list(dict.fromkeys(bibs))[:100]:
        path = os.path.normpath(os.path.join(paper_root, bib.strip()))
        if not in_paper(paper_root, path):  # (the .blg names them, and the paper's TeX can write that file)
            continue
        entries += [{**e, "file": os.path.relpath(path, paper_root)} for e in parse_bib(path) if e["key"] in cited]
    plain = lambda s_: " ".join(re.sub(r"\\[a-zA-Z]+|[{}]", "", s_).split())
    groups = {}
    for e in entries:
        f = e["fields"]
        title = re.sub(r"[^a-z0-9]", "", plain(f.get("title", "")).lower())
        arxiv = re.search(r"(\d{4}\.\d{4,5})", " ".join(f.get(k, "") for k in ("eprint", "journal", "url", "note", "volume")))
        for ident in filter(None, [title and len(title) > 15 and "t:" + title, arxiv and "a:" + arxiv.group(1)]):
            groups.setdefault(ident, {})[e["key"]] = e
    duplicates, seen = [], set()
    for group in groups.values():
        keys = tuple(sorted(group))
        if len(keys) > 1 and keys not in seen:
            seen.add(keys)
            for k in keys:
                e = group[k]
                duplicates.append({"text": f"same paper under keys {', '.join(keys)}: {plain(e['fields'].get('title', ''))[:70]}",
                                   "file": e["file"], "line": e["line"]})
    forms = {}
    for e in entries:
        venue = plain(e["fields"].get("booktitle") or e["fields"].get("journal") or "")
        name = next((n for n, pat in VENUES.items() if re.search(pat, venue, re.I)), None)
        if not name:
            continue
        norm = re.sub(r"\s+", " ", re.sub(r"\b(?:19|20)\d\d\b|%s|\bproceedings of( the)?\b|\b(?:the|ieee|cvf|acm|pmlr)\b|[^\w\s]" % ORDINALS, " ",
                                          venue, flags=re.I)).strip().lower()
        forms.setdefault(name, {}).setdefault(norm, []).append(e)
    venues = []
    for name, variants in forms.items():
        if len(variants) > 1:
            for norm, es in sorted(variants.items(), key=lambda kv: -len(kv[1])):
                e = es[0]
                venues.append({"text": f"{name} as \"{(e['fields'].get('booktitle') or e['fields'].get('journal')).strip()}\""
                                       f" ({len(es)} entr{'y' if len(es) == 1 else 'ies'}, e.g. {e['key']})",
                               "file": e["file"], "line": e["line"]})
    return duplicates, venues


REF_WORDS = {"figure": ["Figure", "Fig.", "figure", "Figs."], "table": ["Table", "Tab.", "table"],
             "section": ["Section", "Sec.", "section", "§"], "appendix": ["Appendix", "App.", "appendix"],
             "equation": ["Equation", "Eq.", "Eqn.", "equation"]}


DEFINITION_RE = re.compile(r"\\(?:(?:re|provide)?newcommand|DeclareRobustCommand|newenvironment|def|let)\b")


def prose_lines(paper_root, pdf_name):
    """(file, line, text) for the prose of the project's .tex files: comments, notes, math, and the
    arguments of labels, references, citations and includes removed; project macros expanded."""
    fls = fls_file(paper_root, pdf_name)
    macros = project_macros(paper_root, fls)
    texts = {path: read_tex(path) for path in project_tex_files(paper_root, fls)}
    kinds = note_kinds(texts.values())
    blank = lambda m: re.sub(r"[^\n]", " ", m.group(0))
    out = []
    for path, text in texts.items():
        begin = text.find("\\begin{document}")
        if begin > 0:
            text = "\n" * text.count("\n", 0, begin) + text[begin:]
        text = blank_notes(text, kinds)
        text = re.sub(r"\\(?:[a-zA-Z]*ref|label|cite[a-z]*|include\w*|input|url|href|begin|end|usepackage|graphicspath|"
                      r"hypersetup|bibliography\w*|newcommand|renewcommand|def|vspace|hspace|setlength|addtolength)\*?"
                      r"\s*(?:\[[^\]]*\]\s*)*\{[^{}]*\}", blank, text)
        text = re.sub(r"\$\\Delta\$", "Δ", text)  # keep names like $\Delta$Fusion
        text = re.sub(r"\$\$.*?\$\$|\$[^$]*\$|\\\(.*?\\\)|\\\[.*?\\\]", blank, text, flags=re.S)
        rel = os.path.relpath(path, paper_root)
        for n, line in enumerate(text.split("\n"), 1):
            if line.strip() and not DEFINITION_RE.search(line):
                out.append((rel, n, tex_to_text(re.sub(r"\\\\|&", " ", line), macros, typeset=True).replace("$", "")))
    return out


def lint(paper_root, pdf_name, pages, in_bib):
    """Consistency lints: (terms, refs, typos) check items."""
    lines = prose_lines(paper_root, pdf_name)
    sites = include_sites(paper_root, pdf_name)
    # Text inside the included figures (e.g. labels in a diagram), located at their \includegraphics.
    for path, (src, line) in sites.items():
        if path.lower().endswith(".pdf"):
            text = " ".join(pdf_pages_text(os.path.join(paper_root, path)))
            if text.strip():
                lines.append((src, line, text, path))
    key = lambda w: w.lower().replace("-", "").replace("δ", "delta").replace("∆", "delta")
    word_re = re.compile(r"[A-Za-zΔ∆][A-Za-zΔ∆0-9]*(?:-[A-Za-zΔ∆0-9]+)*")
    forms, spaced, hyphen_next, surface = {}, {}, {}, {}
    for entry in lines:
        words = word_re.findall(entry[2])
        for i, w in enumerate(words):
            nxt = words[i + 1].lower() if i + 1 < len(words) else None
            if len(key(w)) >= 5 and len(w) >= 3:
                v = w.lower().replace("∆", "δ")
                forms.setdefault(key(w), {}).setdefault(v, []).append(entry)
                surface.setdefault(v, collections.Counter())[w.replace("∆", "Δ")] += 1
                if "-" in w:
                    hyphen_next.setdefault(key(w), set()).add(nxt)
            if nxt:
                spaced.setdefault(key(w + nxt), []).append((f"{w} {nxt}".lower(), words[i + 2].lower() if i + 2 < len(words) else None, entry))
                surface.setdefault(f"{w} {nxt}".lower(), collections.Counter())[f"{w} {words[i + 1]}"] += 1
    shown = lambda v: surface[v].most_common(1)[0][0]  # as most often written, not lowercased
    terms = []
    for k, variants in forms.items():
        # "encoder free models" next to "encoder-free models" (not "trained from scratch" next to
        # "from-scratch encoder": hyphenating a modifier before its noun is right).
        for v, nxt, entry in spaced.get(k, []):
            if nxt and nxt in hyphen_next.get(k, ()):
                variants.setdefault(v, []).append(entry)
        if len(variants) < 2:  # (case is ignored, so the variants differ in hyphens, spaces, or Δ/Delta)
            continue
        ranked = sorted(variants.items(), key=lambda kv: -len(kv[1]))
        top, top_entries = ranked[0]
        for v, entries in ranked[1:]:
            figs = sorted({e[3] for e in entries if len(e) > 3})
            e = next((e for e in entries if len(e) <= 3), entries[0])
            terms.append({"text": f"\"{shown(v)}\" ×{len(entries)} vs \"{shown(top)}\" ×{len(top_entries)}"
                                  + (f" (incl. in {', '.join(os.path.basename(f) for f in figs)})" if figs else ""),
                          "file": e[0], "line": e[1]})
    # Reference styles in the rendered text (what cleveref and friends actually printed).
    refs = []
    for kind, words in REF_WORDS.items():
        seen = {}
        for n, text in enumerate(pages, 1):
            if in_bib(n):
                continue
            flat = " ".join(text.split())
            for w in words:
                for m in re.finditer(r"(?<![\w.])%s\s?(?:[A-Z]\.)?\d+(?!\s*:)" % re.escape(w), flat):
                    if w[0].islower() and m.start() >= 2 and flat[m.start() - 2] in ".?!":
                        continue
                    seen.setdefault(w, []).append(n)
        if len(seen) > 1:
            for w, ps in sorted(seen.items(), key=lambda kv: -len(kv[1])):
                refs.append({"text": f"{kind} references as \"{w} N\" ×{len(ps)} (pages {', '.join(map(str, sorted(set(ps))[:6]))})",
                             "page": ps[0]})
    # Doubled words and references that can break across lines.
    typos = []
    for path in project_tex_files(paper_root, fls_file(paper_root, pdf_name)):
        text = read_tex(path)
        rel = os.path.relpath(path, paper_root)
        for m in re.finditer(r"(?<![\\\w])([A-Za-z]{2,})\s+\1\b", text, re.I):
            if m.group(1).lower() not in ("that", "had", "is"):
                typos.append({"text": f"doubled word: \"{m.group(0).split()[0]} {m.group(0).split()[-1]}\"", "file": rel,
                              "line": text.count("\n", 0, m.start()) + 1})
        for m in re.finditer(r"\b(Figures?|Figs?\.|Tables?|Tab\.|Sections?|Sec\.|Appendix|App\.|Eq\.|Equation|Algorithm|Line)"
                             r"(\s+)\\(ref|eqref)\{", text):
            typos.append({"text": f"\"{m.group(1)} \\{m.group(3)}\": use ~ so the number can't wrap to the next line",
                          "file": rel, "line": text.count("\n", 0, m.start()) + 1})
    return terms, refs, typos


# References to labels: \ref, \cref, \Cref, \autoref, \pageref, project macros like \figref, ranges, \hyperref[...].
REF_RE = re.compile(r"\\[a-zA-Z]*ref\*?\s*\{([^{}]*)\}|\\[a-zA-Z]*refrange\*?\s*\{([^{}]*)\}\s*\{([^{}]*)\}|\\hyperref\s*\[([^\]]*)\]")
BACK_MATTER_RE = re.compile(r"\\(?:appendix|bibliography|printbibliography)\b|\\begin\{thebibliography\}")


def float_placement(paper_root, pdf_name, clean=False):
    """Figures and tables placed or referenced badly: (off, far, unreferenced, unlabeled) check items.

    off: it runs past the text area by more than 3 pt (perhaps off the page). far: a page of text
    (not only floats) lies between it and the first page that references one of its labels from
    outside it. unreferenced: none of its labels is referenced. unlabeled: it has a number but no
    label. A float in the back matter (after the bibliography or \\appendix) only counts references
    there: one referenced only from the main text is a pointer to the appendix, wherever it is.
    """
    found = floats(paper_root, pdf_name, clean)
    fls = fls_file(paper_root, pdf_name)
    points = synctex_points(shown_synctex(paper_root, pdf_name, clean))
    area = layout(paper_root, pdf_name) or {"H": 792, "left": 72, "width": 468, "top": 72, "bottom": 720}
    texts = {path: read_tex(path) for path in project_tex_files(paper_root, fls)}
    kinds = note_kinds(texts.values())
    sites = include_sites(paper_root, pdf_name)

    def at(path, line):
        """Reading-order key of a source line: the lines of the \\input chain down to it."""
        rel, chain = os.path.relpath(path, paper_root), (line,)
        while rel in sites and len(chain) < 10:
            rel, n = sites[rel]
            chain = (n, *chain)
        return chain

    def placed(p):
        """Whether a SyncTeX point is in the text area (not the header, footer, or line numbers)."""
        return area["left"] - 0.5 <= p[1] <= area["left"] + area["width"] + 0.5 and area["top"] - 0.5 <= p[2] <= area["bottom"] + 0.5

    def page_of(path, line):
        """The page a source line was typeset on: its float's, else where its words (or the next
        lines', e.g. a reference at the end of a line) landed."""
        x = next((x for x in found if x["path"] == path and x["line"] <= line <= x["last"]), None)
        if x:
            return x["page"]
        return next((p[0] for n in (line, line + 1, line + 2, line - 1) for p in points.get((path, n), []) if placed(p)), None)

    by_page = {}
    for x in found:
        x["path"] = os.path.join(paper_root, x["file"])
        by_page.setdefault(x["page"], []).append(x["box"])
    # Pages with text outside their floats (a line's worth of words and spaces; a page of floats has a
    # few points of glue): only those put distance between a float and its reference.
    outside = collections.Counter(p[0] for pts in points.values() for p in pts if placed(p) and not any(
        l - 2 <= p[1] <= r + 2 and t - 2 <= p[2] <= b + 2 for l, t, r, b in by_page.get(p[0], ())))
    text_pages = {page for page, n in outside.items() if n >= 10}
    refs, ends = {}, []  # {label: [(reading-order key, path, line)]}; where the main text may end
    begin = None
    for path, text in texts.items():
        # (notes and \iffalse blocks are not in the submitted PDF)
        text = blank_notes(re.sub(r"\\iffalse\b.*?\\fi\b", lambda m: re.sub(r"[^\n]", " ", m.group()), text, flags=re.S), kinds)
        line_at = lambda i: text.count("\n", 0, i) + 1
        if begin is None and "\\begin{document}" in text:
            begin = at(path, line_at(text.find("\\begin{document}")))
        ends += [at(path, line_at(m.start())) for m in BACK_MATTER_RE.finditer(text)]
        for m in REF_RE.finditer(text):
            line = line_at(m.start())
            for label in ",".join(filter(None, m.groups())).split(","):
                refs.setdefault(label.strip(), []).append((at(path, line), path, line))
    back = min((key for key in ends if begin and key > begin), default=None)  # (not definitions in the preamble)
    off, far, unreferenced, unlabeled = [], [], [], []
    for x in found:
        name = f"{x['kind'].title()} {x['num']}" if x["num"] else f"Unnumbered {x['kind']}"
        item = {"file": x["file"], "line": x["line"], "page": x["page"]}
        if x["extent"]:
            left, top, right, bottom = x["extent"]
            past = [f"{d:.0f} pt {where}" for where, d in (
                ("above the text area", area["top"] - top), ("below the text area", bottom - area["bottom"]),
                ("into the left margin", area["left"] - left), ("into the right margin", right - area["left"] - area["width"]))
                if d > 3]
            if past:
                off.append({"text": f"{name} runs {', '.join(past)}" + (", off the page" if bottom > area["H"] else ""), **item})
        if not x["keys"]:
            if x["num"]:
                unlabeled.append({"text": f"{name} has no \\label, so the text can't reference it", **item})
            continue
        cited = [r for k in x["keys"] for r in refs.get(k, []) if not (r[1] == x["path"] and x["line"] <= r[2] <= x["last"])]
        if not cited:
            unreferenced.append({"text": f"{name} ({', '.join(x['keys'])}) is never referenced", **item})
            continue
        if back and at(x["path"], x["line"]) > back:
            cited = [r for r in cited if r[0] > back]
        first = min(filter(None, (page_of(r[1], r[2]) for r in cited)), default=None)
        if first and any(p in text_pages for p in range(min(x["page"], first) + 1, max(x["page"], first))):
            far.append({"text": f"{name} is {abs(x['page'] - first)} pages {'after' if x['page'] > first else 'before'} "
                                f"its first reference (page {first})", **item})
    return off, far, unreferenced, unlabeled
