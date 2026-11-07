"""Reading the paper's LaTeX: balanced groups, comments, \\iffalse blocks, macro definitions, rough
plain text, and the root documents and the files they \\input."""

import glob
import os
import re
import subprocess

from . import config
from .files import fls_inputs, in_paper


DOCCLASS_RE = re.compile(r"^[^%\n]*\\documentclass\b", re.M)


def root_docs(paper_root):
    """The root documents in the paper root: the .tex files with a \\documentclass that is not commented out."""
    docs = []
    for name in sorted(os.listdir(paper_root)) if os.path.isdir(paper_root) else ():
        if name.endswith(".tex"):
            try:
                with open(os.path.join(paper_root, name), errors="replace") as f:
                    if DOCCLASS_RE.search(f.read(65536)):
                        docs.append(name[:-4])
            except OSError:
                pass
    return docs


def paper_root_of(path):
    """The paper root for a file, e.g. the editor's current one: its folder if that holds a root
    document, else the nearest folder above that does (not past the top of its git checkout, or
    outside one, more than three folders up)."""
    folder = os.path.dirname(os.path.abspath(path))
    while not os.path.isdir(folder):
        folder = os.path.dirname(folder)
    r = subprocess.run(["git", "-C", folder, "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    top = os.path.realpath(r.stdout.strip()) if r.returncode == 0 else None
    for _ in range(100 if top else 4):
        if root_docs(folder):
            return folder
        if os.path.realpath(folder) == top or folder == os.path.dirname(folder):
            break
        folder = os.path.dirname(folder)
    return None


def source_tree(paper_root, doc):
    """The .tex files that doc reads through \\input and \\include, from the source (no build needed); in
    shared mode, only the paper's own (in_paper), at most 500."""
    seen, todo = set(), [os.path.join(paper_root, f"{doc}.tex")]
    while todo and len(seen) < 500:
        path = os.path.normpath(todo.pop())
        if path in seen or not os.path.isfile(path) or config.SHARED and not in_paper(paper_root, path):
            continue
        seen.add(path)
        for m in INPUT_RE.finditer(read_tex(path)):
            name = m.group(1).strip()
            todo.append(os.path.join(paper_root, name if name.endswith(".tex") else name + ".tex"))
    return seen


def pick_doc(paper_root, docs):
    """The root document whose sources were edited last. On a tie (the newest file is shared, e.g.
    a preamble), the one last built here."""
    try:
        with open(os.path.join(paper_root, config.BUILD, "latex-live-doc")) as f:
            last = f.read().strip()
    except OSError:
        last = None

    def edited(doc):
        return max((os.path.getmtime(path) for path in source_tree(paper_root, doc)), default=0)

    return max(docs, key=lambda doc: (edited(doc), doc == last))


TEX_SYMBOLS = {"Delta": "Δ", "alpha": "α", "beta": "β", "gamma": "γ", "times": "×", "&": "&", "%": "%", "_": "_", "xspace": " ",
               "ldots": "…", "dots": "…"}
MACRO_DEF_RE = re.compile(r"\\(?:(?:re|provide)?newcommand\*?|DeclareRobustCommand\*?)\s*\{?\\([a-zA-Z@]+)\}?\s*(?=\{)|\\def\\([a-zA-Z@]+)(?=\{)")
INPUT_RE = re.compile(r"\\(?:input|include|subfile)\s*\{([^{}]+)\}")


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


def parse_bib(path, text=None):
    """Entries of a .bib file: [{key, type, line, fields: {name: value}}] (a forgiving parser); from its text when
    given."""
    if text is None:
        try:
            with open(path, errors="replace") as f:
                text = f.read()
        except OSError:
            return []
    entries = []
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", text):
        if m.group(1).lower() in ("comment", "string", "preamble"):
            continue
        body, _ = brace_group(text, text.index("{", m.start()))
        fields, i = {}, body.index(",") + 1 if "," in body else len(body)
        for fm in re.finditer(r"(\w+)\s*=\s*", body[i:]):
            j = i + fm.end()
            if j >= len(body):
                break
            if body[j] == "{":
                value, _ = brace_group(body, j)
            elif body[j] == '"':
                k = body.find('"', j + 1)
                value = body[j + 1:k]
            else:
                value = re.match(r"[^,\s}]*", body[j:]).group(0)
            fields.setdefault(fm.group(1).lower(), value)
        entries.append({"key": m.group(2), "type": m.group(1).lower(), "line": text.count("\n", 0, m.start()) + 1,
                        "fields": fields})
    return entries


def brace_group(text, i):
    """The balanced {...} group starting at text[i] == "{": (content, index after it)."""
    depth = 0
    for j in range(i, len(text)):
        if text[j - 1] != "\\":
            depth += {"{": 1, "}": -1}.get(text[j], 0)
        if depth == 0:
            return text[i + 1:j], j + 1
    return "", len(text)


def project_macros(paper_root, fls):
    """Zero-argument macro definitions from the project's .tex files (those the build read).

    Definitions are read in TeX's order, from the root file (the first one read) down through
    \\input/\\include, so a redefinition wins as in TeX, e.g. one in the root after \\input{preamble}.
    Files not reached that way only add macros that are still undefined.
    """
    macros = {}
    paths = [os.path.normpath(os.path.join(paper_root, p)) for p in fls_inputs(fls) if p.endswith(".tex")]
    paths = [p for p in dict.fromkeys(paths) if in_paper(paper_root, p)]
    done = set()

    def read(path, define):
        done.add(path)
        try:
            with open(path, errors="replace") as f:
                text = re.sub(r"(?<!\\)%.*\n?[ \t]*", "", f.read())  # (a comment also eats the line break)
        except OSError:
            return
        pos = 0
        for m in [*INPUT_RE.finditer(text), None]:
            for d in MACRO_DEF_RE.finditer(text, pos, m.start() if m else len(text)):
                body, _ = brace_group(text, d.end())
                if "#" not in body:
                    define(d.group(1) or d.group(2), body)
            if m:
                pos = m.end()
                child = os.path.normpath(os.path.join(paper_root, m.group(1).strip()))
                child = next((c for c in (child, child + ".tex") if c in paths and c not in done), None)
                if child:
                    read(child, define)

    if paths:
        read(paths[0], macros.__setitem__)
    for path in paths:
        if path not in done:
            read(path, macros.setdefault)
    return macros


def tex_to_text(tex, macros, depth=0, typeset=False):
    """Rough plain text for a TeX snippet: expand project macros, keep a few symbols, drop other commands.
    \texorpdfstring gives its bookmark form, or with typeset=True the typeset one."""
    while (i := tex.find("\\texorpdfstring")) >= 0:
        typeset_text, k = brace_group(tex, tex.find("{", i))
        pdf_text, k = brace_group(tex, tex.find("{", k))
        tex = tex[:i] + (typeset_text if typeset else pdf_text) + tex[k:]
    if typeset:  # conditionals: the \else branch (e.g. a glyph's non-bold form)
        tex = re.sub(r"\\if\w*(?:[^\\]|\\(?!else|fi\b))*?\\else((?:[^\\]|\\(?!fi\b))*)\\fi\b", lambda m: m.group(1).strip(), tex)

    def macro(m):
        name = m.group(1)
        if name in macros and depth < 6:
            return tex_to_text(macros[name], macros, depth + 1, typeset)
        return TEX_SYMBOLS.get(name, "")
    return re.sub(r"\\([a-zA-Z@]+|.)\s*", macro, tex).replace("{", "").replace("}", "")


def read_tex(path):
    """File text with comments blanked (line numbers kept)."""
    try:
        with open(path, errors="replace") as f:
            return re.sub(r"(?<!\\)%.*", "", f.read())
    except OSError:
        return ""


def blank_comments(raw):
    """raw with its comments blanked, at the same length (indexes carry over). A line that is all
    comment keeps a \x7f: it doesn't end a paragraph in TeX."""
    def blank(m):
        whole = not raw[raw.rfind("\n", 0, m.start()) + 1:m.start()].strip()
        return ("\x7f" if whole else " ") + " " * (len(m.group()) - 1)
    return re.sub(r"(?<!\\)%.*", blank, raw)


IF_RE = re.compile(r"\\newif\s*\\if[a-zA-Z@]*|\\ifthenelse\b|\\(if[a-zA-Z@]*|fi)(?![a-zA-Z@])")


def false_blocks(text):
    """(start, end) of each \\iffalse ... \\fi block in comment-blanked text (to the end if unclosed)."""
    blocks, depth, start = [], 0, None
    for m in IF_RE.finditer(text):
        if not m.group(1):
            continue
        if start is None:
            if m.group(1) == "iffalse":
                start, depth = m.start(), 1
        else:
            depth += -1 if m.group(1) == "fi" else 1
            if depth == 0:
                blocks.append((start, m.end()))
                start = None
    return blocks + ([(start, len(text))] if start is not None else [])


RERUN_RE = re.compile(r"Rerun to get|may have changed\. Rerun|Please rerun|rerunfilecheck Warning")
