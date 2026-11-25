"""The unit checks of the History tab's server side (latexlive.git, latexlive.editor), without an instance, on a paper in a
folder of a bigger repository: the commits listed (only those that changed the paper's shown files, a merge with what it
brought in, the limit counted after leaving the others out), the uncommitted changes (a rename from outside the paper's
folder names nothing outside it), a commit's diff (never a dot file or build/, however big, and the files shown first),
commits named only by their ids, and restoring a source: over the file the editor opens (only at the version given), a
removed one made again and committed as its restorer's, never over another file, a coauthor only from commits the branch
builds on; in a folder of the paper too."""

import json
import os
import shutil
import tempfile
import threading
import types

from .base import fail, ok
from .helpers import git, git_out, put, read_text
from .files_units import scratch_server


def history_checks():
    with tempfile.TemporaryDirectory(prefix="latex-live-history-") as repo, tempfile.TemporaryDirectory() as state:
        root = make_history_repo(repo)
        listing_checks(repo, root)
        diff_checks(repo, root)
        restore_checks(repo, root, state)


def commit(repo, message, *args):
    git(repo, "add", "-A", *args)
    git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", message)
    return git_out(repo, "rev-parse", "HEAD")


def make_history_repo(repo):
    """A repository with the paper in its folder paper/, and commits that change it, the rest of the repository only, its
    dot files and build/ only (with secret messages), a merge, and a branch that HEAD doesn't build on."""
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "Owner Example")
    git(repo, "config", "user.email", "owner@example.org")
    root = os.path.join(repo, "paper")
    put(os.path.join(root, "main.tex"), "\\documentclass{article}\n\\begin{document}\n\\input{sec}\nOne.\n\\end{document}\n")
    put(os.path.join(root, "sec.tex"), "Section one.\n")
    put(os.path.join(root, "gone.tex"), "Removed later.\n")
    put(os.path.join(root, "sections", "intro.tex"), "In a folder.\n")
    put(os.path.join(repo, "outside.tex"), "Outside the paper.\n")
    put(os.path.join(repo, "theirs.tex"), "Another project's file.\n")
    commit(repo, "start")
    put(os.path.join(repo, "outside.tex"), "Outside, changed.\n")
    commit(repo, "OUTSIDE-ONLY")
    put(os.path.join(root, "main.tex"), read_text(os.path.join(root, "main.tex")).replace("One.", "Two."))
    commit(repo, "second")
    git(repo, "checkout", "-q", "-b", "side")
    put(os.path.join(root, "sec.tex"), "Section one, from the side.\n")
    commit(repo, "on the side")
    git(repo, "checkout", "-q", "main")
    put(os.path.join(root, "main.tex"), read_text(os.path.join(root, "main.tex")).replace("Two.", "Three."))
    commit(repo, "third")
    git(repo, "-c", "commit.gpgsign=false", "merge", "-q", "--no-ff", "-m", "merge the side", "side")
    git(repo, "checkout", "-q", "-b", "elsewhere")
    put(os.path.join(root, "sec.tex"), "Never on main.\n")
    commit(repo, "ELSEWHERE")
    git(repo, "checkout", "-q", "main")
    os.remove(os.path.join(root, "gone.tex"))
    put(os.path.join(root, ".hidden", "big.tex"), "%" + "x" * 700_000 + "\n")  # (sorts first, and passes DIFF_LIMIT)
    put(os.path.join(root, "zlast.tex"), "Last.\n")
    commit(repo, "remove gone.tex")
    put(os.path.join(root, ".env"), "TOKEN=1\n")
    git(repo, "add", "-f", os.path.join("paper", ".env"))
    commit(repo, "DOT-SECRET")
    put(os.path.join(root, "build", "main.log"), "log\n")
    git(repo, "add", "-f", os.path.join("paper", "build", "main.log"))
    commit(repo, "BUILD-SECRET")
    for _ in range(3):  # (the newest commits change nothing shown: a limit counted before leaving them out lists less)
        put(os.path.join(root, ".env"), read_text(os.path.join(root, ".env")) + "x\n")
        commit(repo, "DOT-SECRET again")
    return root


def listing_checks(repo, root):
    """history: the paper's commits only, newest first, without those that changed nothing shown (nor their messages),
    a merge listing what it brought in, the limit counted after; the uncommitted changes in the paper's folder only."""
    from latexlive import git as history
    found = history.history(root)
    subjects = [c["subject"] for c in found["commits"]]
    if subjects != ["remove gone.tex", "merge the side", "third", "on the side", "second", "start"]:
        fail(f"the History tab's commits: {subjects}")
    text = json.dumps(found)
    for secret in ("OUTSIDE-ONLY", "DOT-SECRET", "BUILD-SECRET", "ELSEWHERE", ".env", ".hidden", "build/", "outside.tex", "theirs"):
        if secret in text:
            fail(f"the History tab's list shows {secret!r}: {text[:600]}")
    files = {c["subject"]: [(f["status"], f["path"]) for f in c["files"]] for c in found["commits"]}
    if files["merge the side"] != [("M", "sec.tex")] or files["remove gone.tex"] != [("D", "gone.tex"), ("A", "zlast.tex")]:
        fail(f"the files of the merge and of a removal: {files}")
    two = history.history(root, limit=2)["commits"]
    if [c["subject"] for c in two] != ["remove gone.tex", "merge the side"]:
        fail(f"history with a limit of 2 (counted after leaving out commits that change nothing shown): {[c['subject'] for c in two]}")
    git(repo, "mv", "theirs.tex", os.path.join("paper", "moved.tex"))  # (a rename from outside: git status names the source)
    put(os.path.join(root, "sec.tex"), "Uncommitted.\n")
    put(os.path.join(root, ".env"), "changed\n")
    dirty = history.history(root)["dirty"]
    if sorted((f["status"], f["path"]) for f in dirty) != [("A", "moved.tex"), ("M", "sec.tex")]:
        fail(f"the uncommitted changes, with a file moved in from outside the paper's folder: {dirty}")
    git(repo, "reset", "-q", "--hard")
    listed = history.commits(root)
    if [c["subject"] for c in listed["commits"]] != subjects or listed["dirty"] or any(
            c["sha"] not in json.dumps(found) or c["rev"] != c["sha"][:9] for c in listed["commits"]):
        fail(f"the compare menu's commits (GET /commits) are not the History tab's: {listed}")
    if any(secret in json.dumps(listed) for secret in ("OUTSIDE-ONLY", "DOT-SECRET", "BUILD-SECRET", "ELSEWHERE")):
        fail(f"the compare menu's commits show a commit of outside the paper's shown files: {listed}")
    put(os.path.join(repo, "outside.tex"), "Changed, outside the paper.\n")
    if history.commits(root)["dirty"]:
        fail("the compare menu counts an uncommitted change outside the paper's folder as the paper's")
    put(os.path.join(root, "sec.tex"), "Uncommitted.\n")
    if not history.commits(root)["dirty"] or len(history.commits(root, limit=2)["commits"]) != 2:
        fail("the compare menu misses an uncommitted change of the paper, or its limit")
    git(repo, "reset", "-q", "--hard")
    ok("history: only commits that changed the paper's files (not dot files or build/, nor their messages), a merge with"
       " what it brought in, the limit counted after; the uncommitted changes name nothing outside the paper's folder; the"
       " compare menu lists the same commits")


def diff_checks(repo, root):
    """commit_diff: the files shown only, the visible ones whatever a big dot file's diff; a merge against its first parent;
    ids only (no option, no revision expression, no short or unknown id); a coauthor only commits HEAD builds on;
    file_at_commit: regular files shown only."""
    from latexlive import git as history
    shas = {c["subject"]: c["sha"] for c in history.history(root)["commits"]}
    code, got = history.commit_diff(root, shas["remove gone.tex"])
    if code != 200 or "b/zlast.tex" not in got["diff"] or "a/gone.tex" not in got["diff"] or ".hidden" in got["diff"]:
        fail(f"the diff of a commit with a big dot file in it: {code} {str(got)[:400]}")
    code, got = history.commit_diff(root, shas["merge the side"], "sec.tex")
    if code != 200 or "+Section one, from the side." not in got["diff"]:
        fail(f"the diff of a merge, against its first parent: {code} {str(got)[:400]}")
    head = git_out(repo, "rev-parse", "HEAD")
    elsewhere = git_out(repo, "rev-parse", "elsewhere")
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "written")
        for rev in (f"--output={out}", "HEAD", "main", "HEAD~1", head[:6], "a" * 40, f"{head}^", f"{head}:paper/main.tex", "",
                    None, head.upper()):
            if history.commit_diff(root, rev)[0] != 404:
                fail(f"commit_diff takes {rev!r} as a commit")
        if os.path.exists(out):
            fail("commit_diff passed a rev to git as an option")
    if history.commit_diff(root, head) != (200, {"rev": head, "files": [], "diff": "", "truncated": False}):
        fail(f"commit_diff of a commit that changes only a dot file: {history.commit_diff(root, head)}")
    for rev, path in ((shas["remove gone.tex"], ".hidden/big.tex"), (shas["start"], "../outside.tex"),
                      (shas["start"], "outside.tex"), (shas["second"], "sec.tex")):
        if history.commit_diff(root, rev, path)[0] != 404:
            fail(f"commit_diff shows {path} at {rev[:7]}")
    if history.commit_diff(root, elsewhere, any_rev=False)[0] != 404 or history.commit_diff(root, elsewhere)[0] != 200:
        fail("commit_diff of a commit HEAD doesn't build on: refused for a coauthor, shown for the owner")
    saved, history.LOG_LIMIT = history.LOG_LIMIT, 12  # (a list cut short at its limit: what came is used)
    try:
        cut = history.changed_files(root, shas["second"], shas["remove gone.tex"])
    finally:
        history.LOG_LIMIT = saved
    if cut is None or len(cut) > 1:
        fail(f"changed_files cut short at its limit: {cut}")
    if history.commit_diff(root, "WORKTREE")[0] != 200:
        fail("commit_diff of the uncommitted changes")
    os.symlink("main.tex", os.path.join(root, "link.tex"))
    linked = commit(repo, "a link")
    for rel in (".env", "build/main.log", "link.tex", "../outside.tex", "nothere.tex"):
        if history.file_at_commit(root, linked, rel, 1 << 20)[0] is not None:
            fail(f"file_at_commit reads {rel}")
    if history.file_at_commit(root, linked, "main.tex", 1 << 20)[0] != read_text(os.path.join(root, "main.tex")).encode() \
            or history.file_at_commit(root, linked, "main.tex", 10)[0] is not None:
        fail("file_at_commit of main.tex (and of it, over a limit)")
    git(repo, "reset", "-q", "--hard", "HEAD^")
    from latexlive import baselines
    saved, baselines.build_diff = baselines.build_diff, lambda server, sha: None  # (no latexdiff: what start_diff takes)
    try:
        def start(rev, any_rev=True):
            server = types.SimpleNamespace(paper_root=root, diff={})
            code, state = baselines.start_diff(server, rev, any_rev)
            return code, state.get("rev"), state.get("sha")
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "written")
            for rev in (f"--output={out}", "-x", "HEAD~1", "main", "elsewhere", head[:6], f"{head}^", "", "HEAD^{commit}"):
                if start(rev)[0] != 400:
                    fail(f"POST /diff takes {rev!r} as a commit")
            if os.path.exists(out):
                fail("POST /diff passed a rev to git as an option")
        if start("a" * 40)[0] != 404 or start(elsewhere, any_rev=False)[0] != 403 or start(elsewhere)[:2] != (200, elsewhere[:9]):
            fail("POST /diff: an unknown commit (404), one HEAD doesn't build on (403 for a coauthor, the owner's to compare)")
        if start("HEAD", any_rev=False) != (200, "HEAD", head[:9]) or start(head[:9], any_rev=False) != (200, head[:9], head[:9]):
            fail(f"POST /diff of HEAD and of a short id: {start('HEAD', any_rev=False)} {start(head[:9], any_rev=False)}")
    finally:
        baselines.build_diff = saved
    ok("a commit's diff: the paper's shown files only, first (whatever a dot file's size), a merge against its first parent;"
       " only ids name commits (also the latexdiff's, or HEAD); a coauthor sees only commits the branch builds on; a file read from a commit is a regular"
       " file shown")


def restore_checks(repo, root, state):
    """restore_request: over the file the editor opens, at the version given only; a removed file made again, listed, and
    committed as its restorer's; never over another file; sources only; a coauthor only from commits HEAD builds on."""
    from latexlive import commits, editor, git as history, sources
    server = scratch_server(root, state)
    server.versions, server.versions_lock, server.last_saves = {}, threading.Lock(), {}
    os.makedirs(os.path.join(state, "added"), exist_ok=True)
    sources.load_added(server)
    owner = {"id": "owner", "name": "Owner Example", "role": "owner", "macro": "", "via": "token"}
    ed = {"id": "ed", "name": "Eden Doyle", "email": "eden@example.org", "role": "editor", "macro": "ed", "via": "proxy"}
    shas = {c["subject"]: c["sha"] for c in history.history(root)["commits"]}
    want = lambda result, status, what: result[0] == status or fail(f"{what}: {result}, not {status}")
    sec = os.path.join(root, "sec.tex")
    current = sources.file_version(read_text(sec).encode())
    want(editor.restore_request(server, {"rev": shas["start"], "path": "sec.tex", "version": "0" * 20}, ed), 409,
         "a restore over a version other than the one given")
    want(editor.restore_request(server, {"rev": shas["start"], "path": "sec.tex", "version": current}, ed), 200, "restoring sec.tex")
    if read_text(sec) != "Section one.\n":
        fail(f"sec.tex, restored: {read_text(sec)!r}")
    elsewhere = git_out(repo, "rev-parse", "elsewhere")
    want(editor.restore_request(server, {"rev": elsewhere, "path": "sec.tex"}, ed), 404, "a coauthor restoring from another branch")
    want(editor.restore_request(server, {"rev": elsewhere, "path": "sec.tex"}, owner), 200, "the owner restoring from another branch")
    for rev, rel, status in (("HEAD", "sec.tex", 404), (shas["start"][:6], "sec.tex", 404), (shas["start"], ".env", 400),
                             (shas["remove gone.tex"], ".hidden/big.tex", 404), (shas["start"], "../outside.tex", 404),
                             (shas["start"], "nothere.tex", 404), (shas["start"], "build/main.tex", 404)):
        want(editor.restore_request(server, {"rev": rev, "path": rel}, owner), status, f"restoring {rel} from {rev[:9]}")
    put(os.path.join(root, "zlast.tex"), "Not the document's: the editor doesn't open it.\n")
    want(editor.restore_request(server, {"rev": shas["remove gone.tex"], "path": "zlast.tex"}, owner), 409,
         "a restore over a file the editor doesn't open")
    if read_text(os.path.join(root, "zlast.tex")) != "Not the document's: the editor doesn't open it.\n":
        fail("a restore wrote over a file the editor doesn't open")
    server.autocommit = True
    want(editor.restore_request(server, {"rev": shas["start"], "path": "sec.tex"}, ed), 200, "restoring sec.tex again")
    result = editor.restore_request(server, {"rev": shas["merge the side"], "path": "gone.tex"}, ed)
    if result[:1] != (200,) or result[1].get("created") is not True or read_text(os.path.join(root, "gone.tex")) != "Removed later.\n":
        fail(f"restoring gone.tex, which a commit removed: {result}")
    if "gone.tex" not in server.added or "gone.tex" not in sources.editable_files(server):
        fail("a file restored after it was removed is not listed for the editor")
    paths = server.pending["ed"]["paths"]
    if paths.get("gone.tex", ("",))[0] != "files" or paths.get("sec.tex", ("",))[0] != "edit":
        fail(f"the restores, remembered for their commit: {paths}")
    commits.commit_files(server, ed, dict(paths))
    if git_out(repo, "show", "-s", "--format=%an", "HEAD") != "Eden Doyle" or "paper/gone.tex" not in git_out(repo, "ls-files"):
        fail("a file restored after it was removed is not committed as its restorer's")
    want(editor.restore_request(server, {"rev": shas["merge the side"], "path": "gone.tex"}, ed), 200, "restoring it again (as it is)")
    put(sec, "% grown by notes\n" + "%" * sources.EDITABLE_SIZE + "\n")  # (past what the editor opens)
    if "sec.tex" in sources.editable_files(server):
        fail("the editor opens a source past 4 MB")
    want(editor.restore_request(server, {"rev": shas["start"], "path": "sec.tex", "version": "0" * 20}, ed), 409,
         "a restore over a source past 4 MB, at another version")
    want(editor.restore_request(server, {"rev": shas["start"], "path": "sec.tex"}, ed), 200, "restoring sec.tex, grown past 4 MB")
    if read_text(sec) != "Section one.\n" or "sec.tex" not in sources.editable_files(server):
        fail(f"sec.tex, grown past 4 MB and restored: {read_text(sec)[:80]!r}")
    put(os.path.join(root, "zlast.tex"), "%" * (sources.EDITABLE_SIZE + 10) + "\n")
    want(editor.restore_request(server, {"rev": shas["remove gone.tex"], "path": "zlast.tex"}, owner), 409,
         "a restore over a file past 4 MB that is not the document's")
    shutil.rmtree(os.path.join(root, "sections"))
    result = editor.restore_request(server, {"rev": shas["start"], "path": "sections/intro.tex"}, ed)
    if result[:1] != (200,) or read_text(os.path.join(root, "sections", "intro.tex")) != "In a folder.\n":
        fail(f"restoring sections/intro.tex, whose folder is gone too: {result}")
    ok("restore: over the file the editor opens (at the version given), or a source of the document past 4 MB, a removed file made again, listed and committed as"
       " its restorer's, never over another file, sources only; a coauthor only from commits the branch builds on")
