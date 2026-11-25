"""The unit checks of the Files tab's server side (latexlive.sources, latexlive.editor, latexlive.commits), without an
instance: the names a new file may have, making, uploading, renaming and removing files (never over another, never
through a link, never into the build directory in any case, a source never uploaded, a kind never changed by a rename),
the upload limits, what a coauthor writes to the sources in a day and the size of the sources added (source_budget),
a coauthor's figures pinned to the document, the files added kept for a restart, and the commits of
what the Files tab changed (new files added to git, removed ones committed as removed)."""

import contextlib
import io
import os
import tempfile
import time

from .base import fail, ok
from .helpers import fake_server, git, git_out, patched, put, put_bytes, read_text


def files_checks():
    for check in (containment_checks, name_checks, change_checks, request_checks, source_budget_checks, commit_checks, csp_checks):
        check()


def containment_checks():
    """The path checks that confine requests to the paper (latexlive.files: within, plain_rel, in_paper, child_aux; and
    built on them, sources.paper_file, changed_path and git.shown_path): a path in a folder, never the folder itself nor
    a sibling that shares its name's start; never .. out of the paper, a dot part, or build/ in any case; in shared
    mode, never through a link out of the paper or into build/ (outside it, the owner's links count as before)."""
    from latexlive import config, files, git, sources
    for path, folder, want in (("/a/b", "/a", True), ("/a", "/a", False), ("/ab", "/a", False), ("/", "/", False),
                               ("/x", "/", True), ("/a/b/c", "/a/b", True)):
        if files.within(path, folder) is not want:
            fail(f"within({path!r}, {folder!r}) is not {want}")
    for rel, want in (("a/b.tex", "a/b.tex"), ("a/../b.tex", "b.tex"), ("./a.tex", "a.tex"), ("builds/x.tex", "builds/x.tex"),
                      ("../x.tex", None), ("a/../../x.tex", None), ("/etc/x.tex", None), ("", None), ("a\0.tex", None),
                      (".git/config", None), ("a/.env", None), ("build/x.tex", None), ("BUILD/x.tex", None), ("Build", None),
                      (".", None), (None, None)):
        if files.plain_rel(rel) != want or git.shown_path(rel) is not (want is not None):
            fail(f"plain_rel({rel!r}) is {files.plain_rel(rel)!r}, shown_path {git.shown_path(rel)}, not {want!r}")
    with tempfile.TemporaryDirectory(prefix="latex-live-confine-") as tmp:
        root, outside = os.path.join(tmp, "paper"), os.path.join(tmp, "outside.tex")
        for rel in ("main.tex", "sub/sec.tex", "build/gen.tex", "Build/x.tex", ".hidden/x.tex", "build2.tex"):
            put(os.path.join(root, rel), "x\n")
        put(outside, "x\n")
        put(os.path.join(tmp, "paper2", "x.tex"), "x\n")
        os.symlink(outside, os.path.join(root, "away.tex"))
        os.symlink(os.path.join(root, "build", "gen.tex"), os.path.join(root, "gen.tex"))
        os.symlink(os.path.join(root, "build"), os.path.join(root, "out"))
        os.symlink(os.path.join(root, "sub"), os.path.join(root, "linked"))
        cases = {"main.tex": (True, True), "sub/sec.tex": (True, True), "build2.tex": (True, True), "build/gen.tex": (False, False),
                 "Build/x.tex": (False, False), ".hidden/x.tex": (True, False), "away.tex": (True, False), "gen.tex": (True, False),
                 "out/gen.tex": (True, False), "linked/sec.tex": (True, True), "../paper2/x.tex": (False, False),
                 "sub/../../outside.tex": (False, False)}
        for rel, wants in cases.items():
            path = os.path.normpath(os.path.join(root, rel))
            for shared, want in zip((False, True), wants):
                with patched(config, SHARED=shared):
                    if files.in_paper(root, path) is not want:
                        fail(f"in_paper of {rel} ({'shared' if shared else 'not shared'}) is not {want}")
        editable = {"main.tex": True, "sub/sec.tex": True, "sub/../main.tex": True, "linked/sec.tex": True, "away.tex": False,
                    "gen.tex": False, "out/gen.tex": False, "build/gen.tex": False, "Build/x.tex": False, "BUILD/x.tex": False,
                    ".hidden/x.tex": False, "../outside.tex": False, outside: False, "build2.tex": True}
        for rel, want in editable.items():
            if bool(sources.editable_real(root, rel)) is not want:
                fail(f"editable_real({rel!r}) is not {want}")
        for rel, want in (("gone.tex", True), ("main.tex", True), ("Build/x.tex", False), ("../outside.tex", False),
                          ("away.tex", False), (".hidden/x.tex", False), ("sub/../main.tex", True)):
            if sources.changed_path(root, rel) is not want:
                fail(f"changed_path({rel!r}) is not {want}")
        aux = os.path.join(root, "build")
        put(os.path.join(aux, "ch", "a.aux"), "x")
        os.symlink(tmp, os.path.join(aux, "up"))
        for name, want in (("ch/a.aux", True), ("../main.aux", False), (os.path.join(aux, "ch", "a.aux"), False), ("up/x.aux", False)):
            if bool(files.child_aux(aux, name)) is not want:
                fail(f"child_aux({name!r}) is not {want}")
    ok("paths stay in the paper: in a folder (not the folder, nor a sibling sharing its name's start), no .., dot part"
       " or build/ in any case; in shared mode not through a link out of the paper or into build/; the editor's files,"
       " the Files tab's changes, the History tab's paths and the .aux files a build names alike")


def name_checks():
    """new_path: letters, digits, - _ + and dots, folders below the paper root (at most ASSETS_DEPTH), never the build
    directory (in any case), a dot part, .., a PDF in the paper root, another kind's extension, or a folder that is a
    link; paper_file (the editor's files) refuses the build directory in any case too."""
    from latexlive import sources
    with tempfile.TemporaryDirectory(prefix="latex-live-names-") as root, tempfile.TemporaryDirectory() as elsewhere:
        os.symlink(elsewhere, os.path.join(root, "linked"))
        put(os.path.join(root, "afile"), "x")
        good = {"figures/plot.pdf": sources.ASSET_EXTS, "a/b/c/d/x.png": sources.ASSET_EXTS, "sec_2+v1.tex": sources.EDITABLE_EXTS,
                " figures/./x.PNG ": sources.ASSET_EXTS}
        for rel, exts in good.items():
            try:
                sources.new_path(root, rel, exts)
            except ValueError as e:
                fail(f"new_path refuses {rel!r}: {e}")
        bad = ["sec\nevil.png", "a:b.png", "a,b.png", "a b.png", "../x.png", "/tmp/x.png", ".hidden.png", "figs/.x.png",
               "build/x.png", "Build/x.png", "BUILD/sub/x.png", "x.pdf", "a/b/c/d/e/x.png", "x.tex", "linked/x.png",
               "afile/x.png", "x<y.png", "", "x.png\0"]
        for rel in bad:
            try:
                sources.new_path(root, rel, sources.ASSET_EXTS)
                fail(f"new_path takes {rel!r} as a new figure's name")
            except ValueError:
                pass
        put(os.path.join(root, "Build", "export", "main.tex"), "x")  # (the build directory, where case is ignored)
        put(os.path.join(root, "Builds.tex"), "x")
        if sources.editable_real(root, "Build/export/main.tex") or not sources.editable_real(root, "Builds.tex"):
            fail("the editor opens a file in the build directory named in another case (or refuses one beside it)")
        if sources.in_build("Build/x") is not True or sources.in_build("builder/x") is not False:
            fail("in_build does not ignore case, or takes builder/ for build/")
    ok("a new file's name: letters, digits, - _ + and dots, in folders below the paper (not too deep), never build/ in"
       " any case, a dot part, .., a PDF in the paper root, another kind's extension or a folder that is a link")


def change_checks():
    """create_source, add_file, move_file and remove_file: never over a file that is there (also one that comes while
    an upload is written), no temporary file left, a source stays a source and a figure a figure, nothing through a
    link, and figures in the paper root's folders only."""
    from latexlive import sources
    with tempfile.TemporaryDirectory(prefix="latex-live-changes-") as root, tempfile.TemporaryDirectory() as elsewhere:
        if sources.create_source(root, "sections/intro.tex", "Hi\n") != "sections/intro.tex" \
                or read_text(os.path.join(root, "sections", "intro.tex")) != "Hi\n":
            fail("create_source did not make sections/intro.tex")
        for call in (lambda: sources.create_source(root, "sections/intro.tex", "Again\n"),
                     lambda: sources.add_file(root, "sections/../sections/intro.tex", b"x")):
            with contextlib.suppress(FileExistsError, ValueError):
                call()
                fail("a new file was written over sections/intro.tex")
        if read_text(os.path.join(root, "sections", "intro.tex")) != "Hi\n":
            fail("sections/intro.tex changed")
        sources.add_file(root, "figures/plot.png", b"one")
        try:
            sources.add_file(root, "figures/plot.png", b"two")
            fail("add_file wrote over figures/plot.png")
        except FileExistsError:
            pass
        link = os.link  # (a file that comes while the upload is written: the link to its name fails, nothing written over)
        sources.os.link = lambda src, dst, **kw: (put_bytes(os.path.join(root, "figures", "late.png"), b"theirs"), link(src, dst, **kw))
        try:
            sources.add_file(root, "figures/late.png", b"mine")
            fail("add_file wrote over a file that came meanwhile")
        except FileExistsError:
            pass
        finally:
            sources.os.link = link
        if open(os.path.join(root, "figures", "plot.png"), "rb").read() != b"one" \
                or open(os.path.join(root, "figures", "late.png"), "rb").read() != b"theirs":
            fail("an upload changed a file that was there")
        if [n for n in os.listdir(os.path.join(root, "figures")) if n.startswith(".")]:
            fail(f"an upload left a temporary file: {os.listdir(os.path.join(root, 'figures'))}")
        for new in ("figures/plot.tex", "plot.pdf", "build/plot.png", "Build/plot.png", "figures/late.png"):
            try:
                sources.move_file(root, "figures/plot.png", new)
                fail(f"move_file renamed figures/plot.png to {new}")
            except (ValueError, FileExistsError):
                pass
        if sources.move_file(root, "figures/plot.png", "figs/sub/plot2.png") != "figs/sub/plot2.png" \
                or os.path.exists(os.path.join(root, "figures", "plot.png")) or not os.path.isfile(os.path.join(root, "figs", "sub", "plot2.png")):
            fail("move_file did not move figures/plot.png to figs/sub/plot2.png")
        try:
            sources.move_file(root, "sections/intro.tex", "intro.png")
            fail("move_file made a source a figure")
        except ValueError:
            pass
        put_bytes(os.path.join(elsewhere, "secret.png"), b"s")
        os.symlink(os.path.join(elsewhere, "secret.png"), os.path.join(root, "figs", "out.png"))
        os.symlink(elsewhere, os.path.join(root, "away"))
        put_bytes(os.path.join(root, "main.pdf"), b"%PDF")
        for call, what in ((lambda: sources.remove_file(root, "figs/out.png"), "remove a link"),
                           (lambda: sources.move_file(root, "figs/out.png", "figs/in.png"), "rename a link"),
                           (lambda: sources.remove_file(root, "away/secret.png"), "remove through a linked folder"),
                           (lambda: sources.remove_file(root, "main.pdf"), "remove the document's PDF"),
                           (lambda: sources.remove_file(root, "../x.png"), "remove a file outside the paper")):
            try:
                call()
                fail(f"the Files tab could {what}")
            except (FileNotFoundError, ValueError):
                pass
        try:
            sources.add_file(root, "away/new.png", b"x")
            fail("add_file wrote through a linked folder")
        except ValueError:
            pass
        if os.listdir(elsewhere) != ["secret.png"] or not os.path.exists(os.path.join(root, "main.pdf")):
            fail("the Files tab changed a file outside the paper, or the document's PDF")
        if sources.remove_file(root, "figs/sub/plot2.png") != "figs/sub/plot2.png" or os.path.exists(os.path.join(root, "figs", "sub", "plot2.png")):
            fail("remove_file did not remove figs/sub/plot2.png")
    ok("making, uploading, renaming and removing files: never over another (also one that comes meanwhile), no temporary"
       " file left, a source stays a source and a figure a figure, nothing through a link or into build/, no PDF in the paper root")


def request_checks():
    """The Files tab's requests: a source is made, not uploaded; the main file is neither renamed nor removed; the
    upload limits (the paper's figures in count and bytes, a coauthor's bytes a day: not the owner's); in shared mode, a
    coauthor's figures are the document's (those its build read and those added), the owner's every one; the files
    added are listed (the editor opens a source added, though no \\input names it) and kept for a restart; each change
    is remembered for its commit, a rename's two paths too."""
    from latexlive import config, editor, file_actions, sources
    owner = {"id": "owner", "name": "Owner", "role": "owner", "macro": "", "email": None, "via": "token"}
    ed = {"id": "ed", "name": "Eden", "role": "editor", "macro": "ed", "email": None, "via": "proxy"}
    limits = {"UPLOAD_TOTAL": file_actions.UPLOAD_TOTAL, "UPLOAD_RATE": file_actions.UPLOAD_RATE, "ASSETS_LIMIT": file_actions.ASSETS_LIMIT}  # (as they are)
    with tempfile.TemporaryDirectory(prefix="latex-live-requests-") as root, tempfile.TemporaryDirectory() as state, \
            patched(config, STATE=state, SHARED=config.SHARED), patched(file_actions, paused=lambda s: None, **limits):
        put(os.path.join(root, "main.tex"), "\\documentclass{article}\n\\begin{document}\nx\n\\end{document}\n")
        put_bytes(os.path.join(root, "figures", "old.png"), b"x" * 10)
        put_bytes(os.path.join(root, "other", "theirs.png"), b"x" * 10)
        put(os.path.join(root, "build", "main-live.fls"), f"PWD {root}\nINPUT {root}/figures/old.png\n")
        server = fake_server(root, state)
        b64 = lambda data: __import__("base64").b64encode(data).decode()
        want = lambda result, status, what: result[0] == status or fail(f"{what}: {result}, not {status}")
        want(file_actions.upload_request(server, {"path": "sec.tex", "data": b64(b"x")}, ed), 400, "uploading a source")
        want(file_actions.create_request(server, {"path": "sec/new.tex", "text": "New\n"}, ed), 200, "making sec/new.tex")
        want(file_actions.create_request(server, {"path": "sec/new.tex"}, ed), 409, "making sec/new.tex again")
        if "sec/new.tex" not in sources.editable_files(server):
            fail("the editor does not open a source made in the Files tab (no \\input names it)")
        for path in ("main.tex", "./main.tex"):
            want(file_actions.rename_request(server, {"from": path, "to": "paper.tex"}, owner), 404, f"renaming {path}")
            want(file_actions.delete_request(server, {"path": path}, owner), 404, f"removing {path}")
        want(file_actions.rename_request(server, {"from": "figures/old.png", "to": "figures/old.tex"}, owner), 400, "a figure renamed to a source")
        want(file_actions.upload_request(server, {"path": "figures/new.png", "data": "not base64!"}, ed), 400, "data that is not base64")
        want(file_actions.upload_request(server, {"path": "figures/old.png", "data": b64(b"y")}, ed), 409, "an upload over a figure")
        file_actions.UPLOAD_TOTAL = 25
        want(file_actions.upload_request(server, {"path": "figures/big.png", "data": b64(b"y" * 6)}, ed), 413, "past the paper's bytes")
        file_actions.UPLOAD_TOTAL, file_actions.ASSETS_LIMIT = 10 ** 9, 2
        want(file_actions.upload_request(server, {"path": "figures/many.png", "data": b64(b"y")}, ed), 413, "past the paper's figures")
        file_actions.ASSETS_LIMIT, file_actions.UPLOAD_RATE, config.SHARED = 400, 8, True
        want(file_actions.upload_request(server, {"path": "figures/a.png", "data": b64(b"y" * 5)}, ed), 200, "a coauthor's first upload")
        want(file_actions.upload_request(server, {"path": "figures/b.png", "data": b64(b"y" * 5)}, ed), 429, "past a coauthor's bytes a day")
        want(file_actions.upload_request(server, {"path": "figures/c.png", "data": b64(b"y" * 5)}, owner), 200, "the owner, past that")
        for user, want_paths in ((ed, ["figures/a.png", "figures/c.png", "figures/old.png"]),
                                 (owner, ["figures/a.png", "figures/c.png", "figures/old.png", "other/theirs.png"])):
            found = [a["path"] for a in editor.files_listing(server, user)["assets"]]
            if found != want_paths:
                fail(f"in shared mode, the {user['role']}'s Files tab lists {found}, not {want_paths}")
        want(file_actions.asset_request(server, {"path": ["other/theirs.png"]}, ed), 404, "a coauthor reading a figure the document doesn't use")
        want(file_actions.delete_request(server, {"path": "other/theirs.png"}, ed), 404, "a coauthor removing a figure the document doesn't use")
        if file_actions.asset_request(server, {"path": ["figures/old.png"]}, ed)[:2] != (200, "image/png"):
            fail("GET asset of the document's figure, for a coauthor")
        server.autocommit = True
        want(file_actions.rename_request(server, {"from": "figures/a.png", "to": "figures/renamed.png"}, ed), 200, "renaming a figure")
        want(file_actions.delete_request(server, {"path": "sec/new.tex"}, ed), 200, "removing sec/new.tex")
        remembered = {rel: kind for rel, (kind, _) in server.pending["ed"]["paths"].items()}
        if remembered != {"figures/a.png": "files", "figures/renamed.png": "files", "sec/new.tex": "files"}:
            fail(f"the Files tab's changes are remembered for their commit as {remembered}")
        restarted = fake_server(root, state)
        sources.load_added(restarted)
        if restarted.added != {"figures/renamed.png", "figures/c.png"}:
            fail(f"the files added, as a restart reads them back: {restarted.added}")
    ok("the Files tab's requests: a source made, never uploaded; the main file kept; the limits on uploads (a coauthor's"
       " bytes a day, not the owner's); a coauthor's figures are the document's; the files added listed and kept for a"
       " restart; every change remembered for its commit")


def source_budget_checks():
    """In shared mode, a coauthor's writes to the sources (a save, an entry added in the Refs tab, a file made, a restore)
    count against commits.SOURCE_RATE bytes a day: the bytes that changed (changed_bytes), or with auto-commit the whole
    file when it is not among their writes still to commit (a note too); past that, 429 with a message, nothing written; the owner's
    and anyone's outside shared mode are not counted, nor a save that changes nothing. A source added through the Files
    tab may not grow once the added sources pass ADDED_SOURCES_TOTAL (413, for everyone), but may shrink."""
    from latexlive import bib, commits, config, editor, file_actions, note_writer, sources
    owner = {"id": "owner", "name": "Owner", "role": "owner", "macro": "", "email": None, "via": "token"}
    ed = {"id": "ed", "name": "Eden", "role": "editor", "macro": "ed", "email": None, "via": "proxy"}
    for old, new, want in (("abc", "abXc", 1), ("aaaa", "aaaaa", 1), ("", "xyz", 3), ("xyz", "", 0), ("abab", "ab", 0),
                           ("same", "same", 0), ("one two", "one three two", 6)):
        if commits.changed_bytes(old.encode(), new.encode()) != want:
            fail(f"changed_bytes({old!r}, {new!r}) is {commits.changed_bytes(old.encode(), new.encode())}, not {want}")
    with tempfile.TemporaryDirectory(prefix="latex-live-budget-") as root, tempfile.TemporaryDirectory() as state, \
            patched(config, STATE=state, SHARED=True), patched(editor, paused=lambda s: None), patched(file_actions, paused=lambda s: None), patched(commits, SOURCE_RATE=1000, ADDED_SOURCES_TOTAL=3000), \
            patched(bib, paused=lambda s: None), patched(note_writer, paused=lambda s: None):
        main = os.path.join(root, "main.tex")
        put(main, "\\documentclass{article}\n\\begin{document}\nx\n\\end{document}\n")
        put(os.path.join(root, "refs.bib"), "@misc{a,\n  title = {A},\n}\n")
        for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "x"]):
            git(root, *args)
        sha = git_out(root, "rev-parse", "HEAD")
        server = fake_server(root, state)
        want = lambda result, status, what: result[0] == status or fail(f"{what}: {result}, not {status}")
        spent = lambda: sum(n for _, n in server.writes.get("ed", []))

        def save(user, text, path="main.tex"):
            version = sources.file_version(open(os.path.join(root, path), "rb").read())
            return editor.save_request(server, {"path": path, "base": version, "text": text}, user)

        text = read_text(main)
        want(save(ed, text.replace("x\n", "xy\n")), 200, "a coauthor's small edit")
        want(save(ed, text.replace("x\n", "xy\n")), 200, "a save that changes nothing")
        if spent() != 1:
            fail(f"a coauthor's edit of one byte (and a save that changed nothing) cost {spent()} bytes of their day")
        big = text.replace("x\n", "z" * 1200 + "\n")
        refused = save(ed, big)
        if refused[0] != 429 or "a day" not in refused[1].get("error", "") or "z" * 10 in read_text(main):
            fail(f"a coauthor's save past {commits.SOURCE_RATE} bytes in a day: {refused}")
        want(save(owner, big), 200, "the owner's save of as much")
        with patched(config, SHARED=False):
            want(save(ed, big.replace("z", "w")), 200, "a save of as much outside shared mode")
        added = bib.add_request(server, {"bibtex": "@misc{b,\n  title = {" + "t" * 1200 + "},\n}"}, ed)
        if added[0] != 429 or "a day" not in added[1].get("error", ""):
            fail(f"an entry added in the Refs tab past a coauthor's day: {added}")
        want(file_actions.create_request(server, {"path": "made.tex", "text": "m" * 1200}, ed), 429, "a coauthor's new file past their day")
        git(root, "commit", "-qam", "big")
        want(file_actions.restore_request(server, {"rev": sha, "path": "main.tex"}, owner), 200, "the owner's restore of the small file")
        want(file_actions.restore_request(server, {"rev": git_out(root, "rev-parse", "HEAD"), "path": "main.tex"}, ed), 429, "a coauthor's restore past their day")
        server.writes.clear()
        server.autocommit = True
        want(save(ed, read_text(main) + "%first\n"), 200, "a coauthor's edit with auto-commit")
        first, size = spent(), len(open(main, "rb").read())
        want(save(ed, read_text(main) + "%next\n"), 200, "another, before their commit")
        if first != size or spent() - first != 6:
            fail(f"with auto-commit, a coauthor's first write of a file cost {first} bytes (the file has {size}), the next"
                 f" one {spent() - first} (6 changed)")
        server.autocommit = False
        want(file_actions.create_request(server, {"path": "one.tex", "text": "a" * 2000}, owner), 200, "making a source of 2000 bytes")
        refused = file_actions.create_request(server, {"path": "two.tex", "text": "b" * 1500}, owner)
        if refused[0] != 413 or "added" not in refused[1].get("error", "") or os.path.exists(os.path.join(root, "two.tex")):
            fail(f"a new source past the added sources' {commits.ADDED_SOURCES_TOTAL} bytes: {refused}")
        want(file_actions.create_request(server, {"path": "two.tex", "text": "b" * 900}, owner), 200, "a new source within them")
        want(save(owner, "a" * 2200, "one.tex"), 413, "an added source grown past the added sources' total")
        want(save(owner, "a" * 1000, "one.tex"), 200, "an added source shrunk")
        put(main, "\\newcommand{\\ed}[1]{\\todotxt{#1}}\n\\begin{document}\nSome words. \\ed{first note}\n" + "%" * 900 + "\n\\end{document}\n")
        put(os.path.join(root, "build", "main-live.fls"), f"PWD {root}\nINPUT main.tex\n")
        server.autocommit, server.pending = True, {}
        server.writes["ed"] = [(time.monotonic(), 500)]
        note = {"action": "edit", "pdf": "main.pdf", "source": "main.tex", "line": 3, "macro": "ed", "tex": "first note", "text": "y"}
        refused = note_writer.note_request(server, note, ed)
        if refused[0] != 429 or "first note" not in read_text(main):
            fail(f"a coauthor's note in a file not among their writes to commit, past what they may write today: {refused}")
        server.writes["ed"] = []
        want(note_writer.note_request(server, note, ed), 200, "a coauthor's note within their day")
        server.autocommit, server.pending, server.paid, server.writes = False, {}, {}, {}
        want(note_writer.note_request(server, {**note, "tex": "y", "action": "reply", "text": "r" * 300}, ed), 200, "a coauthor's reply")
        if not 300 <= spent() < 400:
            fail(f"a coauthor's reply of 300 characters cost {spent()} bytes of their day")
        server.autocommit, server.writes = True, {}
        want(file_actions.rename_request(server, {"from": "two.tex", "to": "moved.tex"}, ed), 200, "a coauthor's rename")
        want(save(ed, read_text(os.path.join(root, "moved.tex")) + "x", "moved.tex"), 200, "a coauthor's edit of a file they renamed")
        if spent() != 901:
            fail(f"with auto-commit, a coauthor's first edit of a file they renamed cost {spent()} bytes, not its 901 (its commit"
                 " stores it whole)")
        want(file_actions.delete_request(server, {"path": "moved.tex"}, ed), 200, "a coauthor's removal of it")
        want(file_actions.rename_request(server, {"from": "one.tex", "to": "moved.tex"}, ed), 200, "another file renamed to its name")
        server.writes = {}
        want(save(ed, read_text(os.path.join(root, "moved.tex"))[:-1] + "y", "moved.tex"), 200, "an edit of that file")
        if spent() != 1000:
            fail(f"with auto-commit, the first edit of a file renamed to a name paid for already cost {spent()} bytes, not its 1000")
        server.autocommit = False
        want(file_actions.create_request(server, {"path": "more.bib", "text": "@misc{c,\n  title = {C},\n}\n"}, owner), 200, "making more.bib")
        added = bib.add_request(server, {"bibtex": "@misc{d,\n  title = {" + "t" * 2500 + "},\n}", "file": "more.bib"}, owner)
        if added[0] != 413 or "added" not in added[1].get("error", ""):
            fail(f"an entry added in the Refs tab past the added sources' total: {added}")
    ok(f"in shared mode, a coauthor writes at most {commits.SOURCE_RATE // 1_000_000} MB to the sources a day (saves, the Refs"
       " tab, new files, restores, notes; what changed, or with auto-commit what their commit stores), the owner and anyone outside"
       f" shared mode without limit; the sources added hold at most {commits.ADDED_SOURCES_TOTAL // 1_000_000} MB")


def commit_checks():
    """commit_files with the Files tab's changes: a new file is added to git (unless git ignores it), a removed one is
    committed as removed, a rename as both; a file made and removed before its commit, nothing; an edit after a file
    was made keeps it a new file (remember_write); a commit that fails leaves nothing in the index."""
    from latexlive import commits
    from latexlive.git import GitRun
    with tempfile.TemporaryDirectory(prefix="latex-live-commits-") as root, tempfile.TemporaryDirectory() as state:
        git(root, "init", "-q")
        git(root, "config", "user.name", "Owner Example")
        git(root, "config", "user.email", "owner@example.org")
        put(os.path.join(root, ".gitignore"), "*.eps\n")
        put(os.path.join(root, "main.tex"), "x\n")
        put_bytes(os.path.join(root, "figures", "old.png"), b"old")
        git(root, "add", "-A")
        git(root, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "start")
        server = fake_server(root, state)
        server.autocommit = True
        user = {"id": "ed", "name": "Eden Doyle", "email": "eden@example.org", "role": "editor", "macro": "ed", "via": "proxy"}
        os.rename(os.path.join(root, "figures", "old.png"), os.path.join(root, "figures", "moved.png"))
        put_bytes(os.path.join(root, "figures", "new.png"), b"new")
        put_bytes(os.path.join(root, "figures", "ignored.eps"), b"eps")
        put(os.path.join(root, "sec.tex"), "made\n")
        for rel in ("figures/old.png", "figures/moved.png", "figures/new.png", "figures/ignored.eps", "gone.tex", "sec.tex"):
            commits.remember_write(server, user, rel, "files")
        commits.remember_write(server, user, "sec.tex", "edit")
        paths = server.pending["ed"]["paths"]
        if paths["sec.tex"][0] != "files":
            fail(f"an edit of a file the Files tab made makes it an edit only: {paths['sec.tex']}")
        run = commits.run_git  # (a commit that fails: the new files it added leave the index again)
        refuse = lambda root, args, **kw: GitRun(1, b"", "refused", False) if "--only" in args else run(root, args, **kw)
        try:
            with patched(commits, run_git=refuse):
                commits.commit_files(server, user, dict(paths))
            fail("commit_files did not say that git refused the commit")
        except RuntimeError:
            pass
        if git_out(root, "diff", "--cached", "--name-only"):
            fail(f"a failed commit left new files in the index: {git_out(root, 'diff', '--cached', '--name-only')}")
        with contextlib.redirect_stdout(io.StringIO()):  # (its "committed" line)
            made = commits.commit_files(server, user, dict(paths))
        changes = sorted(git_out(root, "show", "--name-status", "--format=", "-M", "HEAD").splitlines())
        want = sorted(["A\tfigures/new.png", "A\tsec.tex", "R100\tfigures/old.png\tfigures/moved.png"])
        if not made or changes != want or git_out(root, "show", "-s", "--format=%an", "HEAD") != "Eden Doyle":
            fail(f"the Files tab's changes, committed: {made}, {changes} (want {want}, as Eden Doyle)")
        if "figures/ignored.eps" in git_out(root, "ls-files"):
            fail("a file that git ignores was committed")
    ok("the Files tab's changes are committed as their writer's: new files added to git (not ignored ones), renames and"
       " removals; an edit of a file made there keeps it new")


def csp_checks():
    """A figure's own Content-Security-Policy (GET /asset: nothing runs in it) is that response's only: the next one on
    the connection has the usual one (BaseHandler.end_headers forgets it)."""
    from latexlive import handler
    h = object.__new__(handler.Handler)
    h.path, h.request_version, h.wfile, h._headers_buffer = "/asset?path=x.svg", "HTTP/1.1", io.BytesIO(), []
    h.csp_override = handler.ASSET_CSP
    h.end_headers()
    h.path = "/me"
    h.end_headers()
    found = [line for line in h.wfile.getvalue().decode().splitlines() if line.startswith("Content-Security-Policy:")]
    if len(found) != 2 or "sandbox" not in found[0] or "sandbox" in found[1]:
        fail(f"the figure's CSP, then the next response's on the connection: {found}")
    ok("a figure's sandboxing CSP is its response's only, not the next one's on the connection")
