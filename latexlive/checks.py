"""GET /check/: the pre-submission checks (anonymity, notes, references, fonts, images, size, the
arXiv source, the bibliography, generated figures, placement, consistency, overfull lines)."""

import glob
import os
import re
import subprocess
import sys
import threading
import time

from . import config
from .arxiv import ago, arxiv_source
from .build import build_status
from .files import clean_file, fls_file, job_file, mtime, pdf_path, project_tex_files, read_capped
from .latex import read_tex
from .lints import bib_consistency, float_placement, lint, stale_outputs
from .notes import notes
from .synctex import bib_span
from .tools import pdf_pages_text, pdf_tool


def find_bib_entry(paper_root, key):
    """(relative .bib path, line) of @type{key, in the paper's .bib files, or None."""
    pat = re.compile(r"@\w+\s*\{\s*%s\s*," % re.escape(key))
    for bib in sorted(glob.glob(os.path.join(paper_root, "*.bib")) + glob.glob(os.path.join(paper_root, "*", "*.bib"))):
        try:
            with open(bib, errors="replace") as f:
                for n, line in enumerate(f, 1):
                    if pat.search(line):
                        return os.path.relpath(bib, paper_root), n
        except OSError:
            pass
    return None


def root_flag(text, name):
    """The last uncommented \\<name>true / \\<name>false in the root file: True, False, or None."""
    found = re.findall(r"\\%s(true|false)\b" % name, text)
    return found[-1] == "true" if found else None


def checks(server, pdf_name):
    """Pre-submission checks: [{title, level (ok|info|warn|fail), detail, items: [{text, file?, line?, page?}]}].

    Looks at the notes-free PDF when there is one (what would be submitted), else the live one.
    The viewer adds the page-limit result itself.
    """
    root = server.paper_root
    doc = pdf_name[:-4]
    clean = os.path.isfile(pdf_path(root, f"{doc}-clean.pdf"))
    pdf = pdf_path(root, f"{doc}-clean.pdf") if clean else pdf_path(root, pdf_name)
    root_tex = read_tex(os.path.join(root, f"{doc}.tex"))
    pages = pdf_pages_text(pdf)
    f = clean_file if clean else job_file
    span = bib_span(root, (f(root, pdf_name, ".synctex.gz"), f(root, pdf_name, ".bbl"), f(root, pdf_name, ".pdf")))
    in_bib = (lambda p: span[0]["page"] <= p <= (span[1] or span[0])["page"]) if span else (lambda p: False)
    results = []

    def add(title, level, detail, items=()):
        results.append({"title": title, "level": level, "detail": detail, "items": list(items)[:50]})

    def hits(pattern, skip_bib=False, flags=re.I):
        """[{text, page}] for regex matches in the page text, with some context."""
        found = []
        for n, text in enumerate(pages, 1):
            if skip_bib and in_bib(n):
                continue
            flat = " ".join(text.split())
            for m in re.finditer(pattern, flat, flags):
                found.append({"text": "…" + flat[max(0, m.start() - 40):m.end() + 40] + "…", "page": n})
        return found

    # Anonymity, for documents that print an anonymous author block (as blind-review styles do).
    final = re.search(r"^[ \t]*(?:\\iclrfinalcopy\b|\\usepackage\s*\[[^\]]*\b(?:accepted|preprint)\b[^\]]*\]\s*\{icml\d{4}\})",
                      root_tex, re.M)
    if pages and re.search(r"\banonymous\b", pages[0], re.I):
        names = set()  # people named in the note-macro comments, e.g. "\eb ... % Ellis Brown"
        for path in project_tex_files(root, fls_file(root, pdf_name)):
            with open(path, errors="replace") as fh:
                names |= {m.strip() for m in re.findall(r"\\todotxt.*%\s*([A-Z][a-z]+(?: [A-Z][a-z]+)+)\s*$", fh.read(), re.M)}
        git_name = subprocess.run(["git", "-C", root, "config", "user.name"], capture_output=True, text=True).stdout.strip()
        if git_name:
            names.add(git_name)
        # Affiliations and other terms that identify the authors: LATEX_LIVE_ANON_TERMS, a regular
        # expression (alternatives separated by |), e.g. r"\bNYU\b|New York University".
        anon_terms = os.environ.get("LATEX_LIVE_ANON_TERMS", "").strip()
        ident = [r"\b%s\b" % re.escape(n) for n in sorted(names)] + ([f"(?:{anon_terms})"] if anon_terms else []) + [
            r"github\.com/\S+", r"huggingface\.co/\S+", r"\bwandb\b"]
        anon = hits("|".join(ident), skip_bib=True, flags=0)
        anon += hits(r"\b(?:our|my) (?:prior|previous|earlier|recent) (?:work|paper|study)|\bwe (?:previously|earlier) (?:showed|proposed|introduced|found)")
        info = pdf_tool(["pdfinfo", pdf])
        # (ICML's submission style sets the metadata author to "Anonymous Authors".)
        meta_author = re.search(r"^Author:[ \t]*(?!anonymous\b)(\S.*)$", info, re.M | re.I)
        problems = [{"text": f"PDF metadata names an author: {meta_author.group(1).strip()}"}] if meta_author else []
        problems += [{**h, "text": "acknowledgments: " + h["text"]} for h in hits(r"\bAcknowledge?ments?\b", flags=0)]
        add("Anonymity", "fail" if problems else "warn" if anon else "ok",
            f"{len(problems)} problem(s), {len(anon)} identifying phrase(s) outside the references" if problems or anon
            else "anonymous author block; no names, affiliations, or links to identifying pages found",
            problems + anon)
    else:
        add("Anonymity", "info", "not checked: page 1 has no anonymous author block"
            + (f" ({final.group(0).strip()} is on)" if final else ""),
            [{"text": f"{final.group(0).strip()} prints the author names", "file": f"{doc}.tex",
              "line": root_tex.count("\n", 0, final.start()) + 1}] if final else [])

    # Notes and draft switches.
    note_list = notes(root, pdf_name)
    shown = root_flag(root_tex, "showtodos")
    toc = [m for m in re.finditer(r"\\(\w*showtoc)true\b", root_tex)]
    items = []
    if shown:
        items.append({"text": "\\showtodostrue: notes are rendered (use \\showtodosfalse to submit)", "file": f"{doc}.tex",
                      "line": root_tex.count("\n", 0, root_tex.rfind("\\showtodostrue")) + 1})
    if toc:
        items.append({"text": f"\\{toc[-1].group(1)}true: the draft table of contents is on", "file": f"{doc}.tex",
                      "line": root_tex.count("\n", 0, toc[-1].start()) + 1})
    add("Draft notes and switches", "warn" if items else "info" if note_list else "ok",
        f"{len(note_list)} inline note(s) in the source" + ("; switch them off before submitting" if shown else "; hidden"),
        items)

    # Undefined references and citations, placeholders.
    build = build_status(server, pdf_name) or {}
    undefined = build.get("undefined") or []
    marks = hits(r"\?\?")
    add("Undefined references and citations", "fail" if undefined or marks else "ok",
        f"{len(undefined)} undefined in the log, {len(marks)} '??' in the PDF" if undefined or marks else "none",
        [{"text": f"{u['kind']} '{u['key']}'", "file": u["file"], "line": u["line"]} for u in undefined] + marks)
    placeholders = hits(r"\b(?:TODO|TBD|TBA|FIXME|XXX)\b|lorem ipsum|\[citation needed\]", flags=0)
    add("Placeholder text", "warn" if placeholders else "ok",
        f"{len(placeholders)} in the {'notes-free ' if clean else ''}PDF" if placeholders else "none", placeholders)

    # Fonts.
    fonts = pdf_tool(["pdffonts", pdf]).splitlines()[2:]
    type3 = []
    if any(" Type 3 " in l for l in fonts):  # which pages (usually matplotlib figures; in shared mode, of the first 200)
        for n in range(1, min(len(pages), 200 if config.SHARED else len(pages)) + 1):
            out = pdf_tool(["pdffonts", "-f", str(n), "-l", str(n), pdf])
            if " Type 3 " in out:
                type3.append(n)
    unembedded = [l.split()[0] for l in fonts if len(l.split()) >= 5 and re.search(r"\s(no)\s+(yes|no)\s+(yes|no)\s+\d+\s+\d+\s*$", l)]
    add("Fonts", "fail" if unembedded else "warn" if type3 else "ok",
        f"{len(fonts)} fonts; {len(unembedded)} not embedded; Type 3 fonts on {len(type3)} page(s)" if unembedded or type3
        else f"all {len(fonts)} fonts embedded; no Type 3 fonts",
        [{"text": f"not embedded: {n}"} for n in unembedded] +
        [{"text": "Type 3 font (often a matplotlib figure; some venues ask for Type 1/TrueType only)", "page": n} for n in type3])

    # Images.
    low = []
    for l in pdf_tool(["pdfimages", "-list", pdf]).splitlines()[2:]:
        c = l.split()
        if len(c) >= 14 and c[2] in ("image", "stencil") and c[12].isdigit() and int(c[3]) * int(c[4]) > 10000:
            ppi = min(int(c[12]), int(c[13]))
            if ppi < 150:
                low.append({"text": f"{ppi} ppi raster image ({c[3]}×{c[4]} px)", "page": int(c[0])})
    add("Raster image resolution", "warn" if low else "ok",
        f"{len(low)} image(s) below 150 ppi at print size" if low else "no raster images below 150 ppi", low)

    # File size.
    published = pdf_path(root, pdf_name)
    size = os.path.getsize(published) if os.path.isfile(published) else 0
    add("File size", "fail" if size > 50e6 else "warn" if size > 40e6 else "ok",
        f"{size / 1e6:.1f} MB (OpenReview's limit is typically 50 MB)")

    # What the arXiv upload would publish (the source).
    add("arXiv source", *arxiv_source(root, pdf_name))

    # Bibliography.
    warnings = [l.strip()[9:] for l in (read_capped(job_file(root, pdf_name, ".blg")) or "").splitlines() if l.startswith("Warning--")]
    bib_items = []
    for w in dict.fromkeys(warnings):
        key = re.search(r" in (\S+)$", w)
        loc = key and find_bib_entry(root, key.group(1))
        bib_items.append({"text": w, **({"file": loc[0], "line": loc[1]} if loc else {})})
    add("Bibliography entries", "warn" if bib_items else "ok",
        f"{len(bib_items)} bibtex warning(s), e.g. missing fields" if bib_items else "no bibtex warnings", bib_items)

    # Generated figures and tables older than their scripts or data.
    run = "uv run python" if os.path.isfile(os.path.join(root, "pyproject.toml")) else "python"
    stale = sorted(stale_outputs(root, pdf_name), key=lambda x: (not x["dirty"], x["output"]))
    add("Generated figures and tables", "warn" if any(x["dirty"] for x in stale) else "info" if stale else "ok",
        f"{len(stale)} may be stale (a script or data file changed after them)" if stale
        else "no figure or table is older than the script or data that makes it",
        [{"text": f"{x['output']}: {x['source']} changed {ago(x['lag'])} later"
                  + (" (uncommitted)" if x["dirty"] else "")
                  + (f"; rerun {run} {x['source']}" if x["source"].endswith(".py") else ""),
          "file": x["site"][0], "line": x["site"][1], "open": x["source"]} for x in stale])

    # Where figures and tables landed, and whether the text references them.
    off, far, unreferenced, unlabeled = float_placement(root, pdf_name, clean)
    counts = [(off, "off the text area"), (unreferenced, "never referenced"),
              (far, "pages away from their first reference"), (unlabeled, "without a \\label")]
    add("Figure and table placement", "warn" if off or unreferenced else "info" if far or unlabeled else "ok",
        "; ".join(f"{len(xs)} {what}" for xs, what in counts if xs)
        or "all inside the text area, referenced, and near their first reference",
        off + unreferenced + far + unlabeled)

    # Consistency lints.
    terms, refs, typos = lint(root, pdf_name, pages, in_bib)
    add("Consistent terms", "info" if terms or refs else "ok",
        f"{len(terms)} term(s) spelled more than one way, {len(refs)} reference style(s) mixed" if terms or refs
        else "hyphenation, Δ/Delta names, and Figure/Section references are consistent", terms + refs)
    add("Typos", "warn" if typos else "ok",
        f"{len(typos)} doubled word(s) or breakable references" if typos else "no doubled words; references use ~",
        typos)
    duplicates, venues = bib_consistency(root, pdf_name)
    add("Bibliography consistency", "warn" if duplicates else "info" if venues else "ok",
        f"{len(duplicates)} entr(ies) cited twice under different keys; {len(venues)} venue spelling(s) differ"
        if duplicates or venues else "no duplicate entries; each venue is written one way", duplicates + venues)

    # Overfull boxes.
    sys.path.insert(0, config.HERE)
    import latex_report
    boxes = []
    if (log := read_capped(job_file(root, pdf_name, ".log"), config.OUTPUT_LIMIT)) is not None:
        cwd = os.getcwd()
        os.chdir(root)  # overfull_boxes checks project paths relative to the paper root
        try:
            boxes = latex_report.overfull_boxes(log)
        finally:
            os.chdir(cwd)
    add("Overfull lines", "info" if boxes else "ok", f"{len(boxes)} line(s) stick into the margin" if boxes else "none",
        [{"text": m, "file": p, "line": n} for p, n, m in boxes])
    return results


def cached_checks(server, pdf_name):
    """GET /check/: checks, which take seconds of the machine's time on a long paper, reused while the
    published PDF and its notes-free one stay the same, for up to 10 s (in shared mode, where editors
    may ask, 30 s; builds start at most every MIN_GAP s there). One computation
    per document at a time, without holding checks_lock meanwhile: requests that come while it runs wait
    for its result, and other documents' checks don't wait for it."""
    root = server.paper_root
    key = (mtime(pdf_path(root, pdf_name)), mtime(pdf_path(root, f"{pdf_name[:-4]}-clean.pdf")))

    def fresh(kept):
        age = time.monotonic() - kept["at"]
        return kept["key"] == key and age < (30 if config.SHARED else 10)

    with server.checks_lock:
        kept = server.checks.get(pdf_name)
        if kept and fresh(kept):
            return kept["results"]
        running = server.checks_running.get(pdf_name)
        mine = running is None
        if mine:
            running = server.checks_running[pdf_name] = threading.Event()
    if not mine:
        running.wait()
        with server.checks_lock:
            kept = server.checks.get(pdf_name)
        return kept["results"] if kept else checks(server, pdf_name)
    try:
        results = checks(server, pdf_name)
        with server.checks_lock:
            server.checks[pdf_name] = {"key": key, "at": time.monotonic(), "results": results}
        return results
    finally:
        with server.checks_lock:
            server.checks_running.pop(pdf_name, None)
        running.set()
