"""Between the PDF and the sources, for the viewer: forward search for its lists (/forward),
inverse search (/synctex), and opening a source line in the owner's editor (/open)."""

import os
import re
import shutil
import subprocess
import threading
import time
import unicodedata

from . import config
from .files import shown_synctexes
from .notes import margin_notes, notes, shown_notes
from .roles import at_least
from .sources import editable_files
from .synctex import bib_source, forward_target, inverse_search, synctex_input, to_int
from .tools import synctex_unavailable


def shared_result(server, key, compute, miss_ttl=None):
    """compute()'s result for key, computed once: a request for the same key while it runs waits for it,
    and later ones reuse it (keys hold what the result depends on, e.g. the build's version). This keeps
    many identical requests, e.g. from several open viewers, to one container run. At most 512 kept.
    With miss_ttl, an empty result (None: a lookup that found nothing, or failed for now, e.g. while the
    image is missing or SyncTeX timed out) is reused for only that many seconds, then computed again."""
    with server.results_lock:
        entry = server.results.get(key)
        if entry is not None and entry.get("expires", float("inf")) < time.monotonic():
            entry = None
        mine = entry is None
        if mine:
            if len(server.results) >= 512:
                server.results.clear()
            entry = server.results[key] = {"done": threading.Event()}
    if not mine:
        entry["done"].wait()
        if "value" in entry:
            return entry["value"]
        return compute()  # (the first one failed)
    try:
        entry["value"] = compute()
        if miss_ttl is not None and not entry["value"]:
            entry["expires"] = time.monotonic() + miss_ttl
    except BaseException:
        with server.results_lock:
            if server.results.get(key) is entry:
                del server.results[key]
        raise
    finally:
        entry["done"].set()
    return entry["value"]


def forward_request(server, query, user=None):
    """/forward?file=<relative path>&line=N[&clean=1]: forward search for the viewer's lists. The same
    lookup is made once per build: requests for it while it runs wait for its answer (shared_result), and
    one that found nothing is made again after 5 s (it may have failed for now: a busy slot, the image).
    In shared mode, below editor, only for the notes the PDF shows them (shown_notes), whose rows are
    what they click: other lines would tell them which source files exist, and where."""
    paper_root = server.paper_root
    try:
        src, line = os.path.realpath(os.path.join(paper_root, query["file"][0])), int(query["line"][0])
    except (KeyError, ValueError):
        return 400, {"error": "expected file, line"}
    if not src.startswith(os.path.realpath(paper_root) + os.sep):
        return 404, {"error": "not a file in the paper"}
    if config.SHARED and user is not None and not at_least(user, "editor"):
        pdf = server.default_pdf
        shown = shown_notes(server, pdf, notes(paper_root, pdf) + margin_notes(paper_root, pdf))
        if not any(n.get("line") == line and os.path.realpath(os.path.join(paper_root, n.get("file") or "")) == src for n in shown):
            return 404, {"error": "that line is not in the PDF"}  # (as for any line not in it: no hint either way)
    clean = query.get("clean") == ["1"]
    builds = tuple(sorted(shown_synctexes(paper_root, clean)))
    found = shared_result(server, ("forward", src, line, clean, builds), lambda: forward_target(paper_root, src, line, clean),
                          miss_ttl=5)
    if found:
        return 200, found[1]
    if missing := synctex_unavailable():
        return 503, {"error": missing}
    return 404, {"error": "that line is not in the PDF"}


def open_source(paper_root, query):
    """Open a project file at a line in VS Code (build-error links in the viewer), and at a column in it when the
    query has one (an inverse search that found the double-clicked word)."""
    try:
        src, line = os.path.realpath(os.path.join(paper_root, query["file"][0])), int(query["line"][0])
        column = int(query.get("column", ["0"])[0])
    except (KeyError, ValueError):
        return 400, "expected file, line"
    if not src.startswith(os.path.realpath(paper_root) + os.sep) or not os.path.isfile(src):
        return 404, "not a file in the paper"
    return open_in_vscode(src, line, paper_root, column)


def editor_cli():
    """The editor's command-line tool: LATEX_LIVE_EDITOR (e.g. cursor), else `code` in a VS Code terminal, else None."""
    if editor := os.environ.get("LATEX_LIVE_EDITOR"):
        return shutil.which(editor)
    return shutil.which("code") if "VSCODE_IPC_HOOK_CLI" in os.environ else None


def open_in_vscode(src, line, paper_root, column=0):
    """Open src at line in the editor (editor_cli), at a column from 0 when there is one (as VS Code counts: UTF-16
    units). Without an editor, say so rather than fail quietly."""
    where = f"{os.path.relpath(src, paper_root)}:{line}"
    code, editor = editor_cli(), os.environ.get("LATEX_LIVE_EDITOR")
    if not code:
        why = (f"LATEX_LIVE_EDITOR ({editor}) is not a command here" if editor else
               "latex-live is not running in a VS Code terminal (start it with the VS Code task), and LATEX_LIVE_EDITOR is not set")
        return 409, f"{where}, not opened: {why}"
    target = f"{src}:{line}:{column + 1}" if column > 0 else f"{src}:{line}"
    subprocess.Popen([code, "-g", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return 200, where


# Inverse search lands on the double-clicked word, not just its line. SyncTeX answers with a line, and a paragraph is
# often one long source line, so the viewer sends the word from the PDF's text (as a key: letters and digits, lowercase,
# without accents), the words beside it, and which of its line's same words it is; the word is then looked for among
# the words that the source line prints. The idea is lattice's (synctex-word.ts), written anew here.
IN_WORD_SYMBOLS = "'\"`^~=.-"  # (control symbols that change a letter, such as \'e, or split one: hy\-phen)
IN_WORD_COMMANDS = set("uvHcdbtrk")  # (accents spelled as letters: Dvo\v{r}\'ak is one word)
# Commands whose arguments are keys or names, never text on the page (what they print, a citation's number or a
# reference's label, is not in the source).
KEY_COMMAND_RE = re.compile(r"[A-Za-z]*cite[A-Za-z]*|[A-Za-z]*ref|label|begin|end|includegraphics|input|include|"
                            r"usepackage|documentclass|[hv]space|bibliography|bibliographystyle|url|color|textcolor")


def word_key(text):
    """A word as the viewer sends it: letters and digits, without accents, lowercase, ligatures spelled out."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).lower()


def word_char(c):
    return c.isalnum() or unicodedata.category(c).startswith("M")


def skip_arguments(text, i):
    """Past a key command's star, optional arguments and first argument, from just after its name."""
    while i < len(text):
        if text[i] in " *":
            i += 1
        elif text[i] in "[{":
            depth, close = 0, "]" if text[i] == "[" else "}"
            opening = text[i]
            while i < len(text):
                if text[i] == "\\":
                    i += 1
                elif text[i] == opening:
                    depth += 1
                elif text[i] == close:
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            i += 1
            if close == "}":
                return i
        else:
            return i
    return i


def source_words(text):
    """The words that a source line prints, as (key, column from 0): without the comment, command names, braces and
    key arguments; accents and \\- stay inside their word."""
    words, word, start, i = [], "", 0, 0

    def end():
        nonlocal word
        if key := word_key(word):
            words.append((key, start))
        word = ""
    while i < len(text):
        c, at = text[i], i
        if c == "%":
            break
        if c == "\\":
            name = re.match(r"[A-Za-z@]*", text[i + 1:]).group()
            i += 1 + max(len(name), 1)  # (past the command, or the control symbol)
            if name in ("i", "j"):  # (the dotless letters that an accent sits on: na\"{\i}ve)
                start = start if word else at
                word += name
            elif name:
                if name not in IN_WORD_COMMANDS:
                    end()
                if KEY_COMMAND_RE.fullmatch(name):
                    i = skip_arguments(text, i)
            elif not (text[at + 1:at + 2] and text[at + 1] in IN_WORD_SYMBOLS):  # (\%, \& and \\ separate words)
                end()
            continue
        if c not in "{}":  # (braces only group: \'{e} and {\em word} read as letters)
            if word_char(c):
                start = start if word else at
                word += c
            else:
                end()
        i += 1
    end()
    return words


def clicked_word(query):
    """The double-clicked word that the viewer sends with an inverse search ({"word", "before", "after", "occurrence",
    "count"}), or None."""
    get = lambda key: query.get(key, [""])[0][:100]
    word = word_key(get("word"))
    if not word:
        return None
    return {"word": word, "before": word_key(get("before")), "after": word_key(get("after")),
            "occurrence": to_int(get("occurrence"), -1), "count": to_int(get("count"), 0)}


def word_column(path, line, word):
    """Where the clicked word starts in line (from 1) of path: a column from 0, in UTF-16 units (as VS Code and the
    browser's editor count), or None when the word is not there or its places can't be told apart. Its neighbours tell
    places apart first; then, when the PDF's line holds the word as often as the source line, its place among them."""
    if not word:
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for n, text in enumerate(f, 1):
                if n == line:
                    break
            else:
                return None
    except OSError:
        return None
    if len(text) > 100_000:
        return None
    words = source_words(text.rstrip("\r\n"))
    places = [i for i, (key, _) in enumerate(words) if key == word["word"]]
    if not places:
        return None

    def score(i):
        return ((word["before"] != "" and i > 0 and words[i - 1][0] == word["before"])
                + (word["after"] != "" and i + 1 < len(words) and words[i + 1][0] == word["after"]))
    best = max(map(score, places))
    top = [i for i in places if score(i) == best]
    if len(places) == 1 or (best > 0 and len(top) == 1):
        place = top[0]
    elif len(places) == word["count"] and 0 <= word["occurrence"] < len(places) and score(places[word["occurrence"]]) == best:
        place = places[word["occurrence"]]
    else:
        return None
    return len(text[:words[place][1]].encode("utf-16-le")) // 2


def synctex_lookup(server, query, user=None):
    """/synctex?file&page&x&y[&clean=1]&json=1: inverse search for the browser editor, which opens
    nothing here. Returns (status, {"file": the source's path in the editor's list (editable_files),
    "line"}, with "column" when the query names the double-clicked word (word, before, after, occurrence,
    count: clicked_word) and the line has it), or {"file": None, "reason"} (200) for a spot whose source
    the editor doesn't open: a class file of TeX's, generated text in the build directory (for anyone but
    the owner, without the file's name: SyncTeX knows every file the build read)."""
    found = synctex_spot(server, query)
    if config.SHARED and user and user["role"] != "owner" and found[1].get("file") is None and "reason" in found[1]:
        return 200, {"file": None, "reason": "no source line the editor opens here"}
    return found


def synctex_spot(server, query):
    """synctex_lookup's answer, with the reason naming the file, and the column of the double-clicked word in the line
    when the query names it and the line has it (word_column)."""
    try:
        name = query["file"][0]
        page, x, y = int(query["page"][0]), float(query["x"][0]), float(query["y"][0])
    except (KeyError, ValueError):
        return 400, {"error": "expected file, page, x, y"}
    found = synctex_input(server.paper_root, name, page, x, y, query.get("clean") == ["1"])
    if isinstance(found[0], int):
        return found[0], {"error": found[1]}
    src, line = found
    root = os.path.realpath(server.paper_root)
    path = os.path.realpath(os.path.join(root, src))
    if path.endswith(".bbl") and (entry := bib_source(server.paper_root, path, line)):  # (an entry of the bibliography)
        src, line = entry
        path = os.path.realpath(src)
    if path.startswith(os.path.realpath(os.path.join(root, config.BUILD)) + os.sep):
        return 200, {"file": None, "reason": f"generated text ({os.path.basename(src)}); no source line"}
    if not path.startswith(root + os.sep):
        return 200, {"file": None, "reason": f"{os.path.basename(src)} is not one of the paper's files"}
    rel = next((rel for rel, real in editable_files(server, fresh=False).items() if real == path), None)
    if not rel:
        return 200, {"file": None, "reason": f"{os.path.basename(src)} is not one of the files the editor opens"}
    try:
        with open(path, "rb") as f:
            data = f.read()
        lines = max(1, data.count(b"\n") + (not data.endswith(b"\n")))
    except OSError:
        lines = line
    if line > lines:  # (SyncTeX names lines past the end now and then, e.g. in a table built by macros)
        return 200, {"file": rel, "line": lines, "note": f"SyncTeX said line {line} of {rel}, which has {lines}: its last line instead"}
    column = word_column(path, line, clicked_word(query))
    return 200, {"file": rel, "line": line, **({"column": column} if column is not None else {})}


def synctex_edit(paper_root, query):
    """Inverse search: PDF position -> source file:line, opened in VS Code. Returns (status, text).

    query: file (published PDF name), page, and x, y in PDF points from the page's top-left;
    clean=1 for a position in the notes-free PDF; the double-clicked word (clicked_word), to open at.
    """
    try:
        name = query["file"][0]
        page, x, y = int(query["page"][0]), float(query["x"][0]), float(query["y"][0])
    except (KeyError, ValueError):
        return 400, "expected file, page, x, y"
    found = inverse_search(paper_root, name, page, x, y, query.get("clean") == ["1"], bib=True)
    if isinstance(found[0], int):
        return found
    src, line = found
    return open_in_vscode(src, line, paper_root, word_column(src, line, clicked_word(query)) or 0)


def open_in_editor(pdf):
    """Open the PDF in a VS Code tab once it exists (the first build may still be running)."""
    code = editor_cli()
    if not code:
        return print("=== latex-live: not in a VS Code terminal and no LATEX_LIVE_EDITOR; skipping editor tab", flush=True)
    while not os.path.isfile(pdf):
        time.sleep(1)
    subprocess.run([code, "-r", pdf], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
