"""Notes written from the viewer (POST /note): select text in the PDF, and a note macro of the
author picked in the toolbar goes into the source right after the selected words; the viewer also
edits, resolves (comments out, under a "% RESOLVED <date> (<initials>): <reason>" line), replies to,
and deletes notes. Text that LATEX_LIVE_PROTECTED marks (config.PROTECTED_RE) stays as written: a new
note goes after the paragraph or caption it marks, and the marker itself can't be changed here."""

import bisect
import os
import re
import time
import unicodedata

from . import config
from .commits import commit_others, paused, remember_write, updating
from .files import fls_file, project_tex_files
from .latex import blank_comments, brace_group, read_tex
from .notes import escape_note, note_kinds
from .roles import at_least
from .sources import NOTE_LOCK, SourceChanged, read_source, write_source, wrote
from .synctex import inverse_search


# Characters pdflatex sets from UTF-8 without extra packages: ASCII, Latin-1, and the usual
# typographic ones. Others (✓, →, emoji) would stop the build, so notes refuse them.
TYPESETTABLE = set(map(chr, range(0x20, 0x7F))) | set(map(chr, range(0xA0, 0x100))) | set("‘’“”–—…•€")


CMD_RE = re.compile(r"\\([a-zA-Z@]+)\*?|\\.?", re.S)
# Commands whose (first) argument is not typeset as written: references, citations, labels,
# environment names, file names, colors, lengths.
NO_TEXT_ARG_RE = re.compile(r"[a-zA-Z]*ref|[a-zA-Z]*cite[a-zA-Z]*|label|begin|end|includegraphics|input|include|"
                            r"url|href|[hv]space|(?:text|page)?color|definecolor|setlength|addtolength")
HEADING_RE = re.compile(r"\\(?:part|chapter|(?:sub)*section|(?:sub)?paragraph|title)\*?\s*$")
PARAGRAPH_BREAK_RE = re.compile(r"\n[ \t]*\n")


def untypesettable(text):
    """An error message for the first character of text that pdflatex can't set, or None."""
    c = next((c for c in text if c not in TYPESETTABLE and not c.isspace()), None)
    return c and f"pdflatex can't set “{c}” (U+{ord(c):04X}); write it out in words"


def plain_key(text):
    """Letters and digits of text, lowercased and without accents: PDF text and source compare on these."""
    return "".join(c for c in unicodedata.normalize("NFKD", text).casefold() if c.isalnum())


def source_letters(raw, lo, hi):
    """(key, at): plain_key of raw[lo:hi] as it would typeset, roughly (without comments, command
    names, or the arguments of NO_TEXT_ARG_RE commands), with at[i] the index in raw of key[i]."""
    key, at, i = [], [], lo
    while i < hi:
        c = raw[i]
        if c == "%":  # to the end of the line
            i = raw.find("\n", i)
            i = hi if i < 0 else i
        elif c == "\\":
            m = CMD_RE.match(raw, i)
            i = m.end()
            if m.group(1) and NO_TEXT_ARG_RE.fullmatch(m.group(1)):
                braced = False
                while i < hi:
                    if raw[i].isspace():
                        i += 1
                    elif raw[i] == "[":
                        i = raw.find("]", i)
                        i = hi if i < 0 else i + 1
                    elif raw[i] == "{" and not braced:
                        i, braced = brace_group(raw, i)[1], True
                    else:
                        break
        else:
            for k in plain_key(c):
                key.append(k)
                at.append(i)
            i += 1
    return "".join(key), at


def token_end(raw, i):
    """Index after the rest of the token raw[i - 1] belongs to: on through punctuation, closing
    braces, and attached commands such as ~\\cite{...}, to the next space outside braces and math;
    never past a comment, an alignment tab &, or a row break \\\\."""
    depth = 0
    math = raw.count("$", raw.rfind("\n", 0, i) + 1, i) % 2 == 1
    while i < len(raw):
        c = raw[i]
        if c == "\\":
            if raw.startswith("\\\\", i):
                break
            i = CMD_RE.match(raw, i).end()
            continue
        if c in "%&" or (c.isspace() and depth == 0 and not math):
            break
        if c == "$":
            math = not math
        elif c == "{":
            depth += 1
        elif c == "}":
            depth = max(0, depth - 1)
        i += 1
    return i


def note_site(raw, line, selection):
    """(index, dropped): where in raw a note on the selected PDF text goes, given the line SyncTeX
    put the selection's end on: after the token where the selection's last words end. Trailing
    words not found in the source (math, references) are dropped (dropped counts them); index is
    None if none are found."""
    words = [k for k in map(plain_key, selection.split()) if k]
    starts = [0] + [m.end() for m in re.finditer("\n", raw)]
    line = min(max(line, 1), len(starts))
    for radius in (3, 12, 40):  # SyncTeX's line is usually right; look further only if need be
        lo = starts[max(0, line - 1 - radius)]
        hi = starts[line + radius] if line + radius < len(starts) else len(raw)
        key, at = source_letters(raw, lo, hi)
        for end in range(len(words), 0, -1):
            for span in (4, 3, 2, 1):
                chunk = "".join(words[end - span:end]) if span <= end else ""
                # (one short word alone matches too easily, unless it is the whole selection)
                if len(chunk) < 3 or (span == 1 and len(words) > 1 and len(chunk) < 6):
                    continue
                hits = [m.start() for m in re.finditer(re.escape(chunk), key)]
                if hits:
                    best = min(hits, key=lambda h: abs(bisect.bisect_right(starts, at[h]) - line))
                    return token_end(raw, at[best + len(chunk) - 1] + 1), len(words) - end
    return None, 0


def enclosing_group(text, lo, pos):
    """Index of the innermost { still open at pos, scanning from lo, or None."""
    stack = []
    for j in range(lo, pos):
        if j and text[j - 1] == "\\":
            continue
        if text[j] == "{":
            stack.append(j)
        elif text[j] == "}" and stack:
            stack.pop()
    return stack[-1] if stack else None


def safe_site(raw, pos, note_macros=()):
    """(index, protected): where a note meant for raw[pos] goes. Out of a heading's argument or
    another note (after it), and out of text marked LATEX_LIVE_PROTECTED: after the caption or other
    group holding the marker, or after a marker that ends the paragraph or starts the next one."""
    text = blank_comments(raw)  # (a commented-out marker protects nothing)
    breaks = list(PARAGRAPH_BREAK_RE.finditer(text))
    start = max([b.end() for b in breaks if b.end() <= pos], default=0)
    following = [b for b in breaks if b.start() >= pos]
    end = following[0].start() if following else len(text)
    note_re = re.compile(r"\\(?:%s)\s*$" % "|".join(map(re.escape, note_macros))) if note_macros else None
    group, outside = enclosing_group(text, start, pos), None
    while group is not None:  # innermost to outermost
        if HEADING_RE.search(text, start, group) or (note_re and note_re.search(text, start, group)):
            outside = group
        group = enclosing_group(text, start, group)
    if outside is not None:
        pos = brace_group(text, outside)[1]
    if not config.PROTECTED_RE:
        return pos, False
    markers = list(config.PROTECTED_RE.finditer(text, start, end))
    if following:  # a marker opening the next paragraph protects this one
        m = config.PROTECTED_RE.match(text, re.compile(r"\s*").match(text, following[0].end()).end())
        markers += [m] if m else []
    sites = []
    for m in markers:
        group = enclosing_group(text, start, m.start())
        sites.append(brace_group(text, group)[1] if group is not None else m.end())
    site = min((s for s in sites if s >= pos), default=None)
    return (site, True) if site is not None else (pos, False)


def note_request(server, req, user):
    """POST /note, from user (a principal): add a note at a selection in the PDF, or edit, resolve, reply
    to, or delete one. Returns (status, JSON).

    Every request names pdf (the published PDF), whose last build lists the paper's .tex files.
    add: page, x, y (PDF points from the page's top-left, at the selection's end), clean (the
    notes-free PDF), selection (its text), macro, and text (plain; escaped here).
    edit / resolve / reply / delete: source (a .tex path in the paper), line, macro, and tex (the
    note's argument as /notes and /margin gave it); edit also text (plain) or latex (written as is);
    resolve also who (the resolver's note macro, for the initials) and text (the reason); reply also
    who (the replier's note macro) and text (plain).

    A coauthor in shared mode (user, a principal other than the owner) writes as their own note
    macro whatever macro or who the request names, edits and deletes only their own notes, writes
    LaTeX (latex) only as an editor, and only in the shared document; anyone who may comment may
    resolve anyone's note. While the shared copy can't take a write (a git merge in progress, or
    others' edits there can't be committed first), 503 (updating).
    """
    root = server.paper_root
    try:
        action, pdf = req.get("action", "add"), req["pdf"]
        if not isinstance(pdf, str) or not pdf.endswith(".pdf") or os.path.basename(pdf) != pdf:
            return 400, {"error": "bad pdf"}
        coauthor = user["role"] != "owner"
        if coauthor and not at_least(user, "commenter"):
            return 403, {"error": "viewers can read the notes but not write them"}
        if coauthor and pdf != server.default_pdf:
            return 403, {"error": f"this preview shares {server.default_pdf} only"}
        if coauthor and "latex" in req and user["role"] == "commenter":
            return 403, {"error": "commenters write notes as plain text (LaTeX takes the editor role)"}
        files = {os.path.realpath(p): p for p in project_tex_files(root, fls_file(root, pdf))}
        kinds = note_kinds(read_tex(p) for p in files.values())
        if coauthor and user["macro"] not in kinds:
            return 403, {"error": f"your note macro \\{user['macro']} is not defined in the paper" if user["macro"]
                         else "the users file gives you no note macro, so you can't write notes"}
        if coauthor and action in ("add", "resolve", "reply"):  # (as themselves, whoever the request says)
            req = {**req, ("macro" if action == "add" else "who"): user["macro"]}
        macro = req["macro"]
        if macro not in kinds:
            return 400, {"error": f"\\{macro} is not a note macro of this paper"}
        if coauthor and action in ("edit", "delete") and macro != user["macro"]:
            return 403, {"error": "you can edit or delete only your own notes"}
        if action == "add":
            found = inverse_search(root, pdf, int(req["page"]), float(req["x"]), float(req["y"]), bool(req.get("clean")))
            if isinstance(found[0], int):
                return found[0], {"error": found[1]}
            src, line = found
        elif action in ("edit", "resolve", "reply", "delete"):
            src, line = os.path.join(root, req["source"]), int(req["line"])
            if action in ("resolve", "reply") and req["who"] not in kinds:
                return 400, {"error": f"\\{req['who']} is not a note macro of this paper"}
        else:
            return 400, {"error": f"unknown action {action}"}
        src = os.path.realpath(src)
        rel = os.path.relpath(src, root)
        if src not in files:
            return 403, {"error": f"{rel} is not one of the paper's .tex files"}
        with NOTE_LOCK:
            if paused(server) or not commit_others(server, user, rel):  # (LATEX_LIVE_COMMIT: others' edits there are theirs)
                return updating()
            raw = read_source(src)
            if action == "add":
                result = add_note(src, rel, raw, line, macro, kinds[macro], req, list(kinds))
            else:
                result = change_note(src, rel, raw, line, macro, action, req, kinds)
            if result[0] == 200:
                remember_write(server, user, rel, "notes")  # (to be committed as theirs: LATEX_LIVE_COMMIT)
                wrote(server, user, rel)
        return result
    except UnicodeDecodeError:
        return 415, {"error": "the source file is not UTF-8"}
    except (KeyError, TypeError, ValueError) as e:
        return 400, {"error": f"bad request ({type(e).__name__}: {e})"}
    except SourceChanged:
        return 409, {"error": "the source file changed meanwhile (saved in an editor?); try again"}


def add_note(src, rel, raw, line, macro, author, req, note_macros):
    """Write \\<macro>{text} after the selected words near line of raw (the text of src)."""
    typed = unicodedata.normalize("NFC", str(req["text"]))
    if error := untypesettable(typed):
        return 400, {"error": error}
    text = escape_note(typed)
    if not text:
        return 400, {"error": "empty note"}
    pos, dropped = note_site(raw, line, str(req.get("selection", "")))
    if pos is None:
        return 422, {"error": f"couldn't find the selected words near {rel}:{line}; select a few words of plain text"}
    site, protected = safe_site(raw, pos, note_macros)
    write_source(src, raw, raw[:site] + f" \\{macro}{{{text}}}" + raw[site:])
    return 200, {"source": rel, "line": raw.count("\n", 0, site) + 1, "author": author, "protected": protected,
                 "dropped": dropped, "macro": macro, "tex": text}


def cut_note(raw, start, end):
    """(new, after): raw without the note at raw[start:end], and the index in new where the line
    after the note's begins. A note alone on its line(s) takes the line with it; otherwise the
    space before it goes too, as add_note wrote it."""
    bol, eol = raw.rfind("\n", 0, start) + 1, raw.find("\n", end)
    eol = len(raw) if eol < 0 else eol
    if not raw[bol:start].strip() and not raw[end:eol].strip():  # the note's own line: remove the line
        return (raw[:bol] + raw[eol + 1:], bol) if eol < len(raw) else (raw[:max(0, bol - 1)], max(0, bol - 1))
    spaced = start > 0 and raw[start - 1] in " \t" and (end == len(raw) or raw[end].isspace())
    new = raw[:start - spaced] + raw[end:]
    return new, new.find("\n", start - spaced) + 1 or len(new)


def change_note(src, rel, raw, line, macro, action, req, kinds):
    """Edit, resolve, reply to, or delete the \\<macro>{tex} use nearest line of raw (the text of src);
    kinds is {note macro: author label}."""
    text = re.sub(r"(?<!\\)%.*", lambda m: " " * len(m.group()), raw)  # (notes in comments don't count)
    want = " ".join(str(req["tex"]).split())
    uses = []
    for m in re.finditer(r"\\(%s)\s*(?=\{)" % re.escape(macro), text):
        end = brace_group(text, m.end())[1]
        if " ".join(text[m.end() + 1:end - 1].split()) == want:
            uses.append((abs(text.count("\n", 0, m.start()) + 1 - line), m.start(), m.end(), end))
    if not uses:
        return 409, {"error": f"this note is no longer in {rel} as shown; wait for the rebuild and try again"}
    _, start, brace, end = min(uses)  # the one nearest its line (lines move as the file is edited)
    if config.PROTECTED_RE and config.PROTECTED_RE.search(raw[start:end]):
        return 403, {"error": "this note marks protected text; edit it in the source if you mean to"}
    out = {"source": rel, "line": raw.count("\n", 0, start) + 1}
    if action == "delete":
        new = cut_note(raw, start, end)[0]
    elif action == "edit":
        typed = unicodedata.normalize("NFC", str(req["latex"] if "latex" in req else req["text"]))
        if error := untypesettable(typed):
            return 400, {"error": error}
        body = typed if "latex" in req else escape_note(typed)
        if not body.strip():
            return 400, {"error": "empty note (delete it instead)"}
        if brace_group("{" + body + "}", 0)[1] != len(body) + 2 or re.search(r"(?<!\\)%|\n[ \t]*\n", body):
            return 400, {"error": "unbalanced braces, a % comment, or a blank line in the note's LaTeX"}
        new = raw[:brace + 1] + body + raw[end - 1:]
        out["tex"] = " ".join(body.split())
    else:  # resolve, reply: typed text, on one line
        typed = " ".join(unicodedata.normalize("NFC", str(req["text"])).split())
        if error := untypesettable(typed):
            return 400, {"error": error}
        if not typed:
            return 400, {"error": "say why it is resolved" if action == "resolve" else "empty reply"}
        who = req["who"]
        if action == "reply":  # right after the note (or after the protected text it sits in)
            site, out["protected"] = safe_site(raw, end, list(kinds))
            out.update(line=raw.count("\n", 0, site) + 1, author=kinds[who], macro=who, tex=escape_note(typed))
            new = raw[:site] + f" \\{who}{{{out['tex']}}}" + raw[site:]
        else:  # commented out on its own lines after the note's line, every line with %, with the
            # line's indentation; a comment-only line doesn't end a paragraph
            bol = raw.rfind("\n", 0, start) + 1
            indent = re.match(r"[ \t]*", raw[bol:]).group()
            nl = "\r\n" if "\r\n" in raw[bol:raw.find("\n", end) + 1 or len(raw)] else "\n"
            lines = [f"RESOLVED {time.strftime('%Y-%m-%d')} ({kinds[who]}): {typed}"] + raw[start:end].split("\n")
            block = "".join(f"{indent}% {row.strip()}".rstrip() + nl for row in lines)
            new, after = cut_note(raw, start, end)
            new = new[:after] + ("" if after == 0 or new[after - 1] == "\n" else nl) + block + new[after:]
    write_source(src, raw, new)
    return 200, out
