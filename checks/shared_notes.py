"""Notes in shared mode: each user writes as themselves, edits and deletes only their own notes, and may resolve
anyone's (note_checks); notes and their lists keep working while the paper rebuilds (notes_while_rebuilding)."""

import os
import re
import time

from .base import fail, ok, wait_for
from .helpers import read_text, text_point
from .shared_helpers import (
    as_editor, as_viewer, expect, expect_dict, line_of, note, note_ok, settled, via_proxy, wait_commit, with_token)


def note_checks(sh):
    """Notes in shared mode: each user writes as themselves (their own macro, never another's), edits and
    deletes only their own notes, writes LaTeX only as an editor, and may resolve anyone's note; protected
    text keeps its rules; a user whose note macro the paper lacks is told so. Each user's edits are
    committed in the background once they have been quiet for a while."""
    root = sh["root"]
    path = os.path.join(root, "body.tex")
    sh["tip"] = sh["base"]  # (wait_commit: the newest commit seen)
    wait_for("the shared copy's build to catch up", lambda: settled(sh), timeout=120)
    text = read_text(path)
    first, second = line_of(text, "Quokka narwhal"), line_of(text, "Walrus yak")
    at_first = text_point(as_editor, "body.tex", first)

    def add(point, selection, words, macro):
        return {"action": "add", "page": point[0], "x": point[1], "y": point[2], "selection": selection, "text": words, "macro": macro}

    owners = {"source": "body.tex", "line": first, "macro": "eb", "tex": "An owner note to resolve."}
    marker = {"source": "body.tex", "line": second, "macro": "eb", "tex": "as written"}
    for uid, req, what in (("vw", add(at_first, "axolotl pangolin", "A viewer's note", "vw"), "a viewer adding a note"),
                           ("ed", {"action": "edit", **owners, "text": "Rewritten"}, "the editor editing the owner's note"),
                           ("ed", {"action": "delete", **owners}, "the editor deleting the owner's note"),
                           ("cm", {"action": "edit", **owners, "latex": "\\textbf{Rewritten}"},
                            "a commenter writing LaTeX into the owner's note"),
                           ("cm", {"action": "resolve", **marker, "who": "cm", "text": "Done"},
                            "a commenter resolving the protected-text marker")):
        expect(note(sh, uid, req), 403, what)
    refused = str(expect(note(sh, "tk", add(at_first, "axolotl pangolin", "A note", "tk")), 403,
                         "a user whose note macro the paper lacks"))
    if "tk" not in refused or "defined" not in refused:
        fail(f"a user whose note macro the paper lacks is not told so: {refused[:300]}")
    if read_text(path) != text:
        fail("a refused note action changed body.tex")
    ok("notes: a viewer, the owner's note, LaTeX from a commenter, the protected marker and a missing macro are refused")

    one = note_ok(sh, "ed", add(at_first, "axolotl pangolin", "ED note one", "ed"), "the editor adding a note")
    if (one.get("macro"), one.get("source")) != ("ed", "body.tex") or "\\ed{ED note one}" not in read_text(path):
        fail(f"the editor's new note is not \\ed{{ED note one}} in body.tex: {one}")
    wait_commit(sh, "ed", ("body.tex",), "the editor's note")
    two = note_ok(sh, "cm", add(at_first, "tapir gecko", "CM note one", "cm"), "a commenter adding a note")
    if two.get("macro") != "cm" or "\\cm{CM note one}" not in read_text(path):
        fail(f"the commenter's new note is not \\cm{{CM note one}} in body.tex: {two}")
    kept = note_ok(sh, "cm", add(text_point(as_editor, "body.tex", second), "ibex okapi", "CM on protected", "cm"),
                   "a commenter's note on protected text")
    text = read_text(path)
    if kept.get("protected") is not True or not 0 <= text.find("\\eb{as written}") < text.find("\\cm{CM on protected}"):
        fail(f"a note on protected text did not go after the marker: {kept}")
    wait_commit(sh, "cm", ("body.tex",), "the commenter's notes")
    ok("an editor and a commenter add notes with their own macros (one on protected text goes after the marker),"
       " each committed once they are quiet")

    wait_for("the shared copy's build to catch up", lambda: settled(sh), timeout=120)
    text = read_text(path)
    eds = {"source": "body.tex", "line": one["line"], "macro": "ed", "tex": "ED note one"}
    cms = {"source": "body.tex", "line": two["line"], "macro": "cm", "tex": "CM note one"}
    for uid, req, what in (("cm", {"action": "edit", **eds, "text": "Hijacked"}, "a commenter editing the editor's note"),
                           ("ed", {"action": "edit", **cms, "text": "Hijacked"}, "the editor editing a commenter's note"),
                           ("ed", {"action": "delete", **cms}, "the editor deleting a commenter's note"),
                           ("cm", {"action": "edit", **cms, "latex": "CM \\emph{note}"}, "a commenter writing LaTeX into their own note"),
                           ("vw", {"action": "resolve", **eds, "who": "vw", "text": "No"}, "a viewer resolving a note")):
        expect(note(sh, uid, req), 403, what)
    if read_text(path) != text:
        fail("a refused note action changed body.tex")
    posing = note(sh, "ed", add(at_first, "Quokka narwhal", "Not as eb", "eb"))
    text = read_text(path)
    if ("\\eb{Not as eb}" in text or posing[0] not in (200, 400, 403)
            or (posing[0] == 200 and (expect_dict(posing, 200, "").get("macro") != "ed" or "\\ed{Not as eb}" not in text))):
        fail(f"the editor asking to write as \\eb: {posing[0]} {posing[2][:200]!r} (want it refused, or written as \\ed)")
    note_ok(sh, "ed", {"action": "edit", **eds, "latex": "ED note \\emph{one}"}, "the editor writing LaTeX into their own note")
    wait_commit(sh, "ed", ("body.tex",), "the editor's note edit")
    note_ok(sh, "cm", {"action": "edit", **cms, "text": "CM note edited"}, "a commenter editing their own note")
    reply = note_ok(sh, "cm", {"action": "reply", **eds, "tex": "ED note \\emph{one}", "who": "ed", "text": "CM reply"},
                    "a commenter replying (naming someone else as who)")
    note_ok(sh, "cm", {"action": "resolve", **owners, "who": "ed", "text": "Done by CM"}, "a commenter resolving the owner's note")
    wait_commit(sh, "cm", ("body.tex",), "the commenter's edit, reply and resolution")
    text = read_text(path)
    for want in ("\\ed{ED note \\emph{one}}", "\\cm{CM note edited}", "\\cm{CM reply}"):
        if want not in text:
            fail(f"body.tex lacks {want} after the edits")
    if reply.get("macro") != "cm" or "\\ed{CM reply}" in text:
        fail(f"a commenter's reply was written as someone else: {reply}")
    if (not re.search(r"^% RESOLVED \S+ \(cm\): Done by CM$", text, re.M)
            or re.search(r"^[^%\n]*\\eb\{An owner note to resolve\.\}", text, re.M)):
        fail(f"the owner's note is not resolved under the commenter's initials:\n{text[:700]}")
    ok("notes: no one edits or deletes others' notes or writes as someone else; editors may write LaTeX; anyone who comments resolves")
    notes_while_rebuilding(sh)


def notes_while_rebuilding(sh):
    """Notes keep working while the shared copy rebuilds, and so do the lists of notes and of note macros: the
    server reads the paper's files from its last successful build, not from the list of the files read (the
    .fls) that pdflatex rewrites as each pass goes. Each note edit (the editor's, of their own note) is sent as
    soon as a pass has begun, while that list lacks body.tex, and makes the build go another pass."""
    root = sh["root"]
    fls = os.path.join(root, "build", "main-live.fls")
    state = {"edits": 0, "asked": 0.0}  # (note edits so far; when a rebuild was last asked for)

    def mid_pass():
        try:
            with open(fls, errors="replace") as f:
                return "body.tex" not in f.read()
        except OSError:
            return True

    def until(test, what):
        """Wait for test(), asking for a rebuild whenever the build has settled for a while (a pass missed)."""
        while not test():
            if time.monotonic() > end:
                fail(f"timed out waiting for {what}, after {state['edits']} note edits while the shared copy rebuilds")
            if time.monotonic() - state["asked"] > 5 and settled(sh):
                expect(with_token(sh["token"], "/rebuild", "POST", {}), 202, "the owner rebuilding")  # (no 20 s limit)
                state["asked"] = time.monotonic()
            time.sleep(0.002)

    wait_for("the shared copy's build to catch up", lambda: settled(sh), timeout=120)
    listed = expect(via_proxy("ed", "/notes/main.pdf"), 200, "GET notes for the editor")
    mine = [n for n in listed if isinstance(n, dict) and n.get("macro") == "ed" and n.get("file") == "body.tex"]
    if not mine:
        fail(f"/notes lists no note of the editor's in body.tex: {listed}")
    req = {"action": "edit", "source": "body.tex", "line": mine[0]["line"], "macro": "ed", "tex": mine[0]["tex"]}
    during, end = 0, time.monotonic() + 60
    while state["edits"] < 6:
        until(mid_pass, "a pass of the build to begin")
        state["edits"] += 1
        text = f"ED note, edited during pass {state['edits']}"
        note_ok(sh, "ed", {**req, "text": text}, f"the editor editing their own note as a pass of the build begins ({state['edits']})")
        req["tex"] = text
        during += mid_pass()  # (pdflatex still had not read body.tex: the note was written mid-pass)
        now = expect(via_proxy("ed", "/notes/main.pdf"), 200, "GET notes for the editor")
        authors = {a.get("macro") for a in as_viewer("/authors/main.pdf") if isinstance(a, dict)}
        if len(now) != len(listed) or not {"eb", "ed", "cm"} <= authors:
            fail(f"while the shared copy rebuilds, /notes lists {len(now)} notes, not {len(listed)}, and /authors {sorted(authors)}")
        until(lambda: not mid_pass(), "the pass to read body.tex")
    if not during:
        fail("no note edit was answered before pdflatex read body.tex, so this check met no pass in progress")
    wait_commit(sh, "ed", ("body.tex",), "the editor's note edits during the rebuilds")
    ok(f"notes, /notes and /authors keep working while the paper rebuilds ({during} of {state['edits']} note edits were"
       " answered mid-pass, before pdflatex read body.tex again)")
