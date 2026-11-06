"""The browser editor in shared mode (editor_checks): the files API, merges and conflicts, status ?open=, writes
waiting out a git lock and a merge, diffs, rebuilds, and the owner's POST open."""

import glob
import hashlib
import os
import time
import urllib.parse

from .base import HERE, fail, ok, wait_for
from .helpers import editor_calls, git, git_out, put, read_bytes, read_text
from .shared_helpers import expect, expect_dict, note, note_ok, save, via_proxy, wait_commit, with_paragraph, with_token


def version_of(data):
    """A file version as the editor API gives it: the first 20 hex digits of the sha256 of its bytes."""
    return hashlib.sha256(data).hexdigest()[:20]


def editor_checks(sh):
    """The browser editor's files API (for the editor ed), status ?open=, auto-commit waiting out a git lock
    (and another user's write of the same file waiting for that commit), writes waiting out a merge in the
    shared copy, comparing (diff) only with commits that HEAD builds on, rebuilding, and the owner's POST open
    (the editor stand-in's only call)."""
    root, owner = sh["root"], sh["token"]
    path, crlf_path, token_file = os.path.join(root, "body.tex"), os.path.join(root, "crlf.bib"), os.path.join(sh["state"], "token")
    listing = expect_dict(via_proxy("ed", "/files"), 200, "GET files for the editor")
    files = [f for f in listing.get("files") or [] if isinstance(f, dict)]
    paths = [f.get("path") for f in files]
    if listing.get("main") != "main.tex" or paths[:1] != ["main.tex"] or paths[1:] != sorted(paths[1:]):
        fail(f"GET files does not list main.tex first, then the rest by path: {listing}")
    missing = [p for p in ("body.tex", "people.tex", "refs.bib", "sub/refs.bib", "crlf.bib") if p not in paths]
    offered = [p for p in paths if p.startswith(("build/", ".git/")) or any(part.startswith(".") for part in p.split("/"))
               or p in ("leak.bib", "big.bib", "appendix.tex", "generated.inc", "side.tex", "reviews.pdf", "latexmkrc")]
    sizes = {f.get("path"): f.get("size") for f in files}
    if missing or offered or sizes.get("body.tex") != os.path.getsize(path):
        fail(f"GET files: missing {missing}, offering {offered}, body.tex size {sizes.get('body.tex')}: {paths}")
    ok("GET files lists the paper's sources and .bib files (main.tex first), not build/, .git, dot files, links out, big files"
       " or other documents")

    disk = read_bytes(path)
    got = expect_dict(via_proxy("ed", "/file?path=body.tex"), 200, "GET file body.tex")
    want = {"path": "body.tex", "text": disk.decode(), "version": version_of(disk), "eol": "\n"}
    if {key: got.get(key) for key in want} != want:
        fail(f"GET file body.tex: {str(got)[:300]}, not its text, version {version_of(disk)} and eol \\n")
    if expect_dict(via_proxy("ed", "/file?path=sub/refs.bib"), 200, "GET file sub/refs.bib").get("text") != "@misc{two, title = {Two}}\n":
        fail("GET file sub/refs.bib does not give its text")
    new = with_paragraph(got["text"], 3, "Paragraph three, saved by the editor.")
    saved = save("ed", {"path": "body.tex", "base": got["version"], "text": new}, 200, "a save on the current version")
    disk = read_bytes(path)
    if disk.decode() != new or saved.get("version") != version_of(disk) or saved.get("merged") or saved.get("text", new) != new:
        fail(f"a save on the current version: {str(saved)[:300]}")
    ok("GET file gives the text and its version; a save on the current version writes it")

    base = expect_dict(via_proxy("ed", "/file?path=body.tex"), 200, "GET file body.tex")
    put(path, with_paragraph(base["text"], 24, "Paragraph twenty-four, changed on disk meanwhile."))
    older = with_paragraph(base["text"], 1, "Paragraph one, saved from an older copy.")
    merged = save("ed", {"path": "body.tex", "base": base["version"], "text": older}, 200, "a save on an older version")
    disk = read_bytes(path)
    if (merged.get("merged") is not True or merged.get("text") != disk.decode() or merged.get("version") != version_of(disk)
            or "Paragraph twenty-four, changed on disk meanwhile." not in disk.decode()
            or "Paragraph one, saved from an older copy." not in disk.decode()):
        fail(f"a save on an older version did not merge with the change on disk: {str(merged)[:300]}")
    base = expect_dict(via_proxy("ed", "/file?path=body.tex"), 200, "GET file body.tex")
    theirs = with_paragraph(base["text"], 1, "Paragraph one, changed on disk (theirs).")
    mine = with_paragraph(base["text"], 1, "Paragraph one, changed in the browser (mine).")
    put(path, theirs)
    clash = save("ed", {"path": "body.tex", "base": base["version"], "text": mine}, 409, "a save that conflicts with the file on disk")
    disk = read_bytes(path)
    if disk.decode() != theirs or b"<<<<<<<" in disk or b">>>>>>>" in disk:
        fail("a conflicting save changed the file on disk")
    marked = str(clash.get("merged"))
    if (clash.get("conflict") is not True or clash.get("version") != version_of(disk) or clash.get("text") != theirs
            or "<<<<<<< " not in marked or ">>>>>>> " not in marked or "|||||||" in marked):
        fail(f"a conflicting save's answer: {str(clash)[:300]} (want the file's version and text, and git's merge with two-sided"
             " conflict markers)")
    save("ed", {"path": "body.tex", "base": clash["version"], "text": mine, "force": True}, 200,
         "the same save with force (use my version)")
    if read_text(path) != mine:
        fail("a save with force did not write the text")
    copy = with_paragraph(read_text(path), 20, "Paragraph twenty, changed on disk before the copy.")
    put(path, copy)  # (a version the server never served or wrote)
    put(path, with_paragraph(copy, 22, "Paragraph twenty-two, changed on disk after the copy."))
    edit = with_paragraph(copy, 3, "Paragraph three, saved again from that copy.")
    stale = save("ed", {"path": "body.tex", "base": version_of(copy.encode()), "text": edit}, 409,
                 "a save on a version the server never saw")
    disk = read_bytes(path)
    if stale.get("stale") is not True or stale.get("version") != version_of(disk) or stale.get("text") != disk.decode():
        fail(f"a save on an unknown version: {str(stale)[:300]}")
    again = save("ed", {"path": "body.tex", "base": version_of(copy.encode()), "base_text": copy, "text": edit}, 200,
                 "the same save with its base text")
    text = read_text(path)
    if again.get("merged") is not True or any(s not in text for s in ("Paragraph twenty, changed", "Paragraph twenty-two, changed",
                                                                      "Paragraph three, saved again")):
        fail(f"a save with its base text did not merge: {str(again)[:300]}")
    ok("a stale save merges; a conflict writes nothing and answers both texts; force writes; an unknown base asks for its text")

    base = expect_dict(via_proxy("ed", "/file?path=body.tex"), 200, "GET file body.tex")
    once = with_paragraph(base["text"], 7, "Paragraph seven, saved once.")
    save("ed", {"path": "body.tex", "base": base["version"], "text": once, "editor": "tab-a"}, 200,
         "a save (whose answer the editor never gets)")
    more = with_paragraph(base["text"], 7, "Paragraph seven, saved once, then typed on.")
    save("ed", {"path": "body.tex", "base": base["version"], "text": more, "editor": "tab-a"}, 200,
         "the next save, typed on in the same line, from the version before the save whose answer got lost")
    if read_text(path) != more:
        fail("the save after one whose answer got lost did not write its text")
    tab = with_paragraph(base["text"], 7, "Paragraph seven, from the same user's other tab.")
    clash = save("ed", {"path": "body.tex", "base": base["version"], "text": tab, "editor": "tab-b"}, 409,
                 "the same user's save of that line from another tab, from the same older version")
    if clash.get("conflict") is not True or read_text(path) != more:
        fail("a save of the same line from the same user's other tab overwrote the first tab's instead of conflicting")
    undo = expect_dict(via_proxy("ed", "/file?path=body.tex"), 200, "GET file body.tex")
    save("ed", {"path": "body.tex", "base": undo["version"], "text": with_paragraph(undo["text"], 9, "Paragraph nine, then undone."),
                "editor": "tab-a"}, 200, "a save (whose answer the editor never gets)")
    save("ed", {"path": "body.tex", "base": undo["version"], "text": undo["text"], "editor": "tab-a"}, 200,
         "the same tab's save after undoing that edit")
    if read_text(path) != undo["text"]:
        fail("an edit undone after a save whose answer got lost stayed in the file")
    theirs = with_paragraph(base["text"], 7, "Paragraph seven, as another editor changed it in an older copy.")
    clash = expect_dict(with_token(sh["users"]["tk"]["token"], "/file", "POST",
                                   {"path": "body.tex", "base": base["version"], "text": theirs, "editor": "tab-a"}),
                        409, "another editor's save of that line from the same older version")
    if clash.get("conflict") is not True or clash.get("by") != sh["users"]["ed"]["name"] or read_text(path) != more:
        fail(f"another editor's save of that line from the same older version: {str(clash)[:300]} (want a conflict that says"
             f" by: {sh['users']['ed']['name']}, who saved meanwhile, and nothing written)")
    ok("a save whose answer got lost doesn't conflict with the same tab's next save of the same line (and an undo of it"
       " undoes it in the file); another tab's or another user's does, and says who saved meanwhile")

    # A draft restored while the tab had saved newer edits (the viewer's restoreKept): sent from the draft's old base,
    # under the new save name that restoreKept takes, the tab's acknowledged save counts as a change by someone else,
    # so overlapping edits conflict rather than the draft overwriting them.
    draft_base = expect_dict(via_proxy("ed", "/file?path=body.tex"), 200, "GET file body.tex")
    newer = with_paragraph(draft_base["text"], 13, "Paragraph thirteen, saved after the draft was kept.")
    save("ed", {"path": "body.tex", "base": draft_base["version"], "text": newer, "editor": "tab-c"}, 200,
         "the tab's newer save, answered")
    draft = with_paragraph(draft_base["text"], 13, "Paragraph thirteen, as the kept draft had it.")
    clash = save("ed", {"path": "body.tex", "base": draft_base["version"], "base_text": draft_base["text"], "text": draft,
                        "editor": "tab-c-restored"}, 409, "the restored draft's save, under a new save name")
    if clash.get("conflict") is not True or read_text(path) != newer:
        fail(f"a restored draft overwrote the newer saved edits of the same line instead of conflicting: {str(clash)[:200]}")
    page = "\n".join(read_text(p) for p in [os.path.join(HERE, "viewer.html"), *sorted(glob.glob(os.path.join(HERE, "viewer", "*.js")))])
    restore = page[page.index("async function restoreKept"):]
    restore = restore[:restore.index("\n}\n")]
    if "editor: saveId" not in page or "saveId = newSaveId()" not in restore:
        fail("the viewer's restoreKept no longer saves a restored draft under a new save name")
    ok("a restored draft is saved under a new save name, so the tab's newer saved edits of the same lines conflict with it"
       " instead of being overwritten")

    crlf = expect_dict(via_proxy("ed", "/file?path=crlf.bib"), 200, "GET file crlf.bib")
    raw = read_bytes(crlf_path)
    if crlf.get("eol") != "\r\n" or crlf.get("text") != raw.decode().replace("\r\n", "\n") or crlf.get("version") != version_of(raw):
        fail(f"GET file of a CRLF file: {crlf}")
    edited = crlf["text"].replace("Three", "Four")
    done = save("ed", {"path": "crlf.bib", "base": crlf["version"], "text": edited}, 200, "a save of a CRLF file")
    raw = read_bytes(crlf_path)
    if raw != edited.replace("\n", "\r\n").encode() or done.get("version") != version_of(raw):
        fail(f"a save of a CRLF file did not keep its line endings: {raw!r}")
    if os.stat(crlf_path).st_mode & 0o777 != 0o640:
        fail(f"a save did not keep the file's mode (0640, now {os.stat(crlf_path).st_mode & 0o777:o})")
    expect(via_proxy("ed", "/file?path=latin.bib"), 415, "GET file of a file that is not UTF-8")
    for name in ("../shared-state/token", token_file, ".git/config", "build/main-live.log", "build/stray.bib", "leak.bib", "big.bib",
                 ".private.bib", "appendix.tex", "generated.inc", "nope.tex"):
        expect(via_proxy("ed", "/file?path=" + urllib.parse.quote(name, safe="")), 404, f"GET file {name!r} (not editable)")
    for query in ("%2e%2e%2fshared-state%2ftoken", "%2e%2e/shared-state/token", "%252e%252e%252fshared-state%252ftoken"):
        expect(via_proxy("ed", "/file?path=" + query), 404, f"GET file?path={query}")
    expect(via_proxy("ed", "/file?path="), (400, 404), "GET file without a path")
    before = {p: read_bytes(p) for p in (token_file, os.path.join(root, ".git", "config"), os.path.join(root, "build", "stray.bib"))}
    for name in ("../shared-state/token", token_file, "leak.bib", ".git/config", "build/stray.bib", "big.bib", "new.tex", "sub/new.bib"):
        expect(via_proxy("ed", "/file", "POST", {"path": name, "base": "0" * 20, "text": "Overwritten.\n", "force": True}), (403, 404),
               f"POST file {name!r} (not editable)")
    if (any(read_bytes(p) != data for p, data in before.items()) or os.path.exists(os.path.join(root, "new.tex"))
            or os.path.exists(os.path.join(root, "sub", "new.bib")) or os.path.getsize(os.path.join(root, "big.bib")) != (5 << 20) + 2):
        fail("a refused save changed or made a file")
    ok("CRLF files keep their line endings (and saves the mode); files that are not UTF-8 get 415; paths outside the editable"
       " set get 404")
    wait_commit(sh, "ed", ("body.tex", "crlf.bib"), "the editor's saves")

    def opened(name):
        source = expect_dict(via_proxy("ed", "/status/main.pdf?open=" + urllib.parse.quote(name, safe="")), 200,
                             f"status ?open={name}").get("source")
        return (source.get("path"), source.get("version")) if isinstance(source, dict) else (None, None)

    if opened("body.tex") != ("body.tex", version_of(read_bytes(path))):
        fail(f"status ?open=body.tex: {opened('body.tex')}, not its version")
    put(path, with_paragraph(read_text(path), 5, "Paragraph five, changed on disk while open."))
    if (opened("body.tex") != ("body.tex", version_of(read_bytes(path)))
            or opened("crlf.bib") != ("crlf.bib", version_of(read_bytes(crlf_path)))):
        fail("status ?open= does not give a file's version as on disk (after a change, or for a CRLF file)")
    for name in ("../shared-state/token", "leak.bib", "nope.tex"):
        if opened(name)[1]:
            fail(f"status ?open={name} gives a version (not an editable file)")
    lock = os.path.join(root, ".git", "index.lock")  # (as while someone runs git in the shared copy)
    open(lock, "w").close()
    current = expect_dict(via_proxy("ed", "/file?path=body.tex"), 200, "GET file body.tex")
    save("ed", {"path": "body.tex", "base": current["version"], "text": current["text"].replace("Walrus yak zebu", "Walrus yak and zebu")},
         200, "an editor's save inside protected text (no server-side check, by design)")
    saved = time.monotonic()
    if "Walrus yak and zebu" not in read_text(path):
        fail("an editor's save inside protected text was not written")
    # Before it writes the commenter's note in body.tex, the server commits the editor's edit there (a commit holds
    # one person's edits), which it can't while git is busy: so the note waits a moment, then is refused (503, to be
    # tried again), with nothing written.
    cms = [n for n in expect(via_proxy("ed", "/notes/main.pdf"), 200, "GET notes for the editor") if isinstance(n, dict) and n.get("macro") == "cm" and n.get("tex") == "CM note edited"]
    if not cms:
        fail("/notes does not list the commenter's note CM note edited")
    later = {"action": "edit", "source": "body.tex", "line": cms[0]["line"], "macro": "cm", "tex": "CM note edited",
             "text": "CM note, edited once git was free"}
    before = read_bytes(path)
    if (expect_dict(note(sh, "cm", later), 503, "a commenter's note in body.tex while the editor's edit there can't be committed")
            .get("retry") is not True or read_bytes(path) != before):
        fail("a commenter's note, refused while the editor's edit in the same file can't be committed, is not to be tried again,"
             " or changed body.tex")
    time.sleep(max(0.0, saved + 4 - time.monotonic()))  # (two quiet periods since the save)
    if git_out(root, "rev-list", f"{sh['tip']}..shared"):
        fail("auto-commit committed while .git/index.lock existed")
    os.remove(lock)
    wait_commit(sh, "ed", ("body.tex",), "the editor's save, once .git/index.lock is gone")
    note_ok(sh, "cm", later, "the commenter's note, once the editor's edit is committed")
    wait_commit(sh, "cm", ("body.tex",), "the commenter's note")
    ok("status ?open= follows the open file; editors may change protected text; edits wait out a git lock, then commit,"
       " and others' writes to the same file wait for that (503 meanwhile)")

    # During a merge in the shared copy (as when the owner merges the paper's main branch into it), writes are
    # refused (503, to be tried again): the merge's commit would take them, or its abort lose them. After it, they
    # go through.
    current = expect_dict(via_proxy("ed", "/file?path=body.tex"), 200, "GET file body.tex")
    nine = {"path": "body.tex", "base": current["version"],
            "text": with_paragraph(current["text"], 9, "Paragraph nine, saved once the merge was over.")}
    after = {**later, "tex": later["text"], "text": "CM note, edited once the merge was over"}
    git(root, "-c", "core.hooksPath=/dev/null", "merge", "--no-ff", "--no-commit", "side")
    if not os.path.isfile(os.path.join(root, ".git", "MERGE_HEAD")):
        fail("git merge --no-commit side left no merge in progress in the shared copy")
    before = read_bytes(path)
    for result, what in ((via_proxy("ed", "/file", "POST", nine), "the editor's save"), (note(sh, "cm", after), "a commenter's note")):
        if expect_dict(result, 503, f"{what} during a merge in the shared copy").get("retry") is not True:
            fail(f"{what} during a merge in the shared copy is refused, but not to be tried again: {result[2][:200]!r}")
    if read_bytes(path) != before:
        fail("a write during a merge in the shared copy changed body.tex")
    git(root, "-c", "core.hooksPath=/dev/null", "merge", "--abort")
    save("ed", nine, 200, "the editor's save, once the merge is over")
    wait_commit(sh, "ed", ("body.tex",), "the editor's save after the merge")
    note_ok(sh, "cm", after, "the commenter's note, once the merge is over")
    wait_commit(sh, "cm", ("body.tex",), "the commenter's note after the merge")
    ok("during a merge in the shared copy, saves and notes are refused (503, to be tried again) and write nothing;"
       " after it, they go through")

    expect(via_proxy("ed", "/diff", "POST", {"rev": "side"}), 403, "the editor comparing with a commit that HEAD does not build on")
    expect(via_proxy("ed", "/diff", "POST", {"rev": "--output=x"}), (400, 403, 404), "the editor comparing with a bad revision")
    expect(via_proxy("ed", "/diff", "POST", {"rev": "main"}), 200, "the editor comparing with a commit that HEAD builds on")
    diff = wait_for("the latexdiff build", lambda: (d := (expect_dict(via_proxy("ed", "/status/main-diff.pdf"), 200, "the diff's"
                                                                       " status").get("diff") or {})).get("state") in ("done", "failed")
                    and d, timeout=120, step=0.5)
    if diff.get("state") != "done":
        fail(f"the latexdiff build failed in the hardened image (the paper read-only): {diff}")
    expect(with_token(owner, "/diff", "POST", {"rev": "side"}), (200, 409), "the owner comparing with any commit")
    if not isinstance(expect_dict(via_proxy("ed", "/commits"), 200, "GET commits for the editor").get("commits"), list):
        fail("GET commits for the editor has no commits list")
    expect(via_proxy("ed", "/rebuild", "POST", {}), 202, "the editor rebuilding")
    expect(via_proxy("ed", "/rebuild", "POST", {}), 429, "the editor rebuilding again at once (at most every 20 s)")
    if editor_calls(sh["editor_log"]):
        fail(f"the editor stand-in ran for someone other than the owner: {editor_calls(sh['editor_log'])}")
    expect(with_token(owner, "/open", "POST", {"file": "main.tex", "line": 9}), 200, "the owner's POST open")
    calls = wait_for("the owner's POST open to run the editor stand-in", lambda: editor_calls(sh["editor_log"]), timeout=10, step=0.2)
    if len(calls) != 1 or not calls[0].startswith("-g ") or not calls[0].endswith("/main.tex:9"):
        fail(f"the editor stand-in got {calls}, not one -g .../main.tex:9")
    ok("editors compare only with commits HEAD builds on (the owner with any) and rebuild, at most every 20 s; only the owner's"
       " POST open runs the editor")
