"""The paper's git checkout, read only: its git directory and whether git is busy there, the last
pushed commit, the recent commits for the viewer's compare menu, and the History tab's commits, diffs and earlier
versions of a file."""

import os
import re
import subprocess
import threading

from .files import in_build


GIT_OPERATIONS = (("MERGE_HEAD", "a merge"), ("rebase-merge", "a rebase"), ("rebase-apply", "a rebase"),
                  ("CHERRY_PICK_HEAD", "a cherry-pick"), ("REVERT_HEAD", "a revert"))


def git_dir(server):
    """The paper's git directory (asked once), or None outside a git checkout."""
    if server.git_dir is None:
        try:
            r = subprocess.run(["git", "-C", server.paper_root, "rev-parse", "--absolute-git-dir"], capture_output=True,
                               encoding="utf-8", errors="surrogateescape", timeout=60)
        except OSError:  # (no git)
            server.git_dir = ""
        except subprocess.SubprocessError:  # (asked again next time)
            return None
        else:
            server.git_dir = r.stdout.strip() if r.returncode == 0 else ""
    return server.git_dir or None


def git_operation(server):
    """The operation git is in the middle of in the paper's checkout: "a merge", "a rebase", "a
    cherry-pick" or "a revert"; else None."""
    folder = git_dir(server)
    return next((what for name, what in GIT_OPERATIONS if folder and os.path.exists(os.path.join(folder, name))), None)


def git_busy(server):
    """Why git can't take a commit now: an operation in progress (git_operation), or another git
    command holding the index; else None."""
    folder = git_dir(server)
    if not folder:
        return "the paper is not in a git checkout"
    if what := git_operation(server):
        return f"git is in the middle of {what}; edits are committed once it is done"
    if os.path.exists(os.path.join(folder, "index.lock")):
        return "git is busy (another git command holds index.lock); edits are committed once it is done"
    return None


def git_read_env():
    """The environment for git commands that only read: without optional locks, so that they never hold
    the index while the server commits (commit_files)."""
    return {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}


def commits(paper_root):
    """Recent commits for the viewer's compare menu."""
    out = subprocess.run(["git", "-C", paper_root, "log", "-n", "40", "--date=format:%b %d %H:%M",
                          "--format=%h%x09%ad%x09%an%x09%s"], capture_output=True, text=True).stdout
    dirty = bool(subprocess.run(["git", "-C", paper_root, "status", "--porcelain", "--untracked-files=no"], capture_output=True,
                                text=True, env=git_read_env()).stdout.strip())
    return {"dirty": dirty, "commits": [dict(zip(("rev", "date", "author", "subject"), l.split("\t", 3)))
                                        for l in out.splitlines()]}


def pushed_commit(paper_root):
    """The last pushed commit that HEAD builds on, and the remote branch it went to: (sha, ref),
    else (None, None). The candidates are the branch's push and upstream branches and every
    remote's branch of the same name (for a branch never pushed, or a detached HEAD, each remote's
    default branch); the one whose merge base with HEAD is nearest to HEAD wins. The merge base,
    not the remote branch itself, so that others' work fetched since doesn't show as deletions.
    Remote-tracking branches move on push and fetch, so this needs no network."""
    def git(*args):
        r = subprocess.run(["git", "-C", paper_root, *args], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else ""

    branch = git("symbolic-ref", "-q", "--short", "HEAD")
    remotes = git("remote").split()
    refs = [ref for spec in ("@{push}", "@{upstream}") if (ref := git("rev-parse", "--abbrev-ref", spec))]
    refs += [f"{remote}/{branch}" for remote in remotes
             if branch and git("rev-parse", "-q", "--verify", f"refs/remotes/{remote}/{branch}")]
    if not refs:
        refs = [ref for remote in remotes if (ref := git("symbolic-ref", "-q", "--short", f"refs/remotes/{remote}/HEAD"))]
    best = None
    for ref in dict.fromkeys(refs):
        base = git("merge-base", "HEAD", ref)
        ahead = git("rev-list", "--count", f"{base}..HEAD") if base else ""
        if ahead.isdigit() and (best is None or int(ahead) < best[0]):
            best = (int(ahead), base, ref)
    return (best[1], best[2]) if best else (None, None)


# The History tab (history, commit_diff, file_at_commit): the commits that changed the paper's folder, what each changed,
# and a file as a commit left it. Only what is in the paper's folder (a paper in a folder of a bigger repository sees
# nothing of the rest), never a dot file or folder (.git, .env, ...) nor the build directory (shown_path), and only commits
# that changed something shown (not their messages either). A commit is named by its hexadecimal id, never anything
# git would read as an option or a revision expression; every git command has a time limit, and what it prints a bound.
SHA_RE = re.compile(r"[0-9a-f]{7,64}")
HISTORY_LIMIT, HISTORY_SCAN = 60, 1000  # (commits listed; commits read to find them, as some change nothing shown)
LOG_LIMIT, DIFF_LIMIT, DIFF_FILES, FILES_LISTED = 4_000_000, 512_000, 200, 100  # (bytes, bytes, files, files)
GIT_TIMEOUT = 20  # seconds


def shown_path(rel):
    """Whether rel, a path relative to the paper root as git names it (--relative), is one the History tab shows: in
    the paper root, with no part that starts with a dot, and not in the build directory."""
    if not isinstance(rel, str) or not rel or "\0" in rel or os.path.isabs(rel):
        return False
    norm = os.path.normpath(rel)
    parts = norm.split(os.sep)
    return norm != "." and parts[0] != ".." and not any(part.startswith(".") for part in parts) and not in_build(norm)


def git_capped(paper_root, args, limit, timeout=GIT_TIMEOUT):
    """(returncode, bytes, more) of git args in the paper's folder: what it printed, read as it comes, up to limit bytes
    (more: it printed more, and was stopped); returncode None if it ran past timeout seconds (killed), or there is no
    git. Paths are taken literally, and file names printed as they are."""
    argv = ["git", "--literal-pathspecs", "-c", "core.quotePath=false", "-C", paper_root, *args]
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                env=git_read_env())
    except OSError:
        return None, b"", False
    late = threading.Event()
    timer = threading.Timer(timeout, lambda: (late.set(), proc.kill()))
    timer.start()
    try:
        data = proc.stdout.read(limit + 1)
        if len(data) > limit:
            proc.kill()
        proc.stdout.close()
        code = proc.wait()
    finally:
        timer.cancel()
    return (None if late.is_set() else code), data[:limit], len(data) > limit


def git_text(paper_root, args, limit=100_000):
    """What git args printed in the paper's folder, stripped, if it succeeded in time and within limit bytes; else
    None."""
    code, data, more = git_capped(paper_root, args, limit)
    return data.decode("utf-8", "replace").strip() if code == 0 and not more else None


def commit_sha(paper_root, rev, any_rev=True):
    """The full id of the commit rev names (a hexadecimal id, at least 7 digits), or None; without any_rev (a coauthor),
    only a commit that HEAD builds on."""
    if not isinstance(rev, str) or not SHA_RE.fullmatch(rev):
        return None
    sha = git_text(paper_root, ["rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"])
    if not sha or not SHA_RE.fullmatch(sha):
        return None
    if not any_rev and git_capped(paper_root, ["merge-base", "--is-ancestor", sha, "HEAD"], 1000)[0] != 0:
        return None
    return sha


def changed_files(paper_root, base, target=None):
    """[{"status", "path"}]: the files shown (shown_path) that changed from commit base to commit target (None: the
    working tree), paths relative to the paper root; None if git failed."""
    code, data, more = git_capped(paper_root, ["diff", "--name-status", "-z", "--no-renames", "--relative", "--no-color",
                                               base, *([target] if target else []), "--", "."], LOG_LIMIT)
    if code != 0:
        return None
    parts = data.decode("utf-8", "replace").split("\0")
    if more:
        parts = parts[:-1]  # (cut off)
    pairs = zip(parts[0::2], parts[1::2])
    return [{"status": status[:1], "path": path} for status, path in pairs if status and shown_path(path)]


def first_parent(paper_root, sha):
    """The commit that sha's changes are shown against: its first parent (for a merge, the branch it was merged into),
    or for a first commit the empty tree."""
    return (git_text(paper_root, ["rev-parse", "--verify", "--quiet", f"{sha}^1^{{commit}}"])
            or git_text(paper_root, ["hash-object", "-t", "tree", "/dev/null"]))


def history(paper_root, limit=HISTORY_LIMIT, cache={}):
    """GET /history: {"isGit", "branch", "dirty": [{"status", "path"}] (uncommitted changes), "commits": [{"sha", "parent",
    "author", "time", "subject", "files": [{"status", "path"}]}]}: the last commits that HEAD builds on and that changed a
    file shown (shown_path; a merge: what it brought into its first parent), newest first, at most limit of them after
    those that changed nothing shown are left out, each with at most FILES_LISTED files. The commits are read again only
    when HEAD moves."""
    head = git_text(paper_root, ["rev-parse", "--verify", "--quiet", "HEAD^{commit}"])
    if head is None:
        inside = git_text(paper_root, ["rev-parse", "--is-inside-work-tree"]) == "true"
        return {"isGit": inside, "branch": "", "dirty": [], "commits": []}
    branch = git_text(paper_root, ["symbolic-ref", "-q", "--short", "HEAD"]) or ""
    dirty = changed_files(paper_root, "HEAD") or []
    key, kept = (os.path.realpath(paper_root), head, limit), cache.get("kept")
    if not kept or kept[0] != key:
        kept = (key, read_commits(paper_root, limit))
        if kept[1] is not None:  # (a git that failed or ran out of time is asked again next time)
            cache["kept"] = kept
    return {"isGit": True, "branch": branch, "dirty": dirty[:FILES_LISTED], "commits": kept[1] or []}


def read_commits(paper_root, limit):
    """history's commits (see there), or None if git failed."""
    code, data, more = git_capped(paper_root, [
        "log", f"-n{HISTORY_SCAN}", "-z", "--no-renames", "--relative", "--name-status", "--no-show-signature",
        "--no-color", "--format=%x1e%H%x1f%P%x1f%an%x1f%at%x1f%s", "--", "."], LOG_LIMIT, timeout=30)
    if code is None or code != 0 and not data:
        return None
    records = data.decode("utf-8", "replace").split("\x1e")[1:]
    if more:
        records = records[:-1]  # (cut off)
    found = []
    for record in records:
        head, _, rest = record.partition("\0")
        fields = head.split("\x1f")
        if len(fields) != 5 or not SHA_RE.fullmatch(fields[0]):
            continue
        sha, parents, author, when, subject = fields
        parents = parents.split()
        if len(parents) > 1:  # (git log lists no files for a merge)
            files = changed_files(paper_root, parents[0], sha) or []
        else:
            parts = rest.lstrip("\n").split("\0")
            files = [{"status": status[:1], "path": path} for status, path in zip(parts[0::2], parts[1::2])
                     if status and shown_path(path)]
        if not files:
            continue
        found.append({"sha": sha, "parent": parents[0] if parents else None, "author": author,
                      "time": int(when) if when.isdigit() else 0, "subject": subject, "files": files[:FILES_LISTED],
                      "more": max(0, len(files) - FILES_LISTED)})
        if len(found) >= limit:
            break
    return found


def commit_diff(paper_root, rev, path=None, any_rev=True):
    """GET /history/diff?rev=&path=: (status, {"rev", "files", "diff", "truncated"}): what commit rev (a hexadecimal id;
    without any_rev, one that HEAD builds on; "WORKTREE": the uncommitted changes) changed in the files shown
    (shown_path), against its first parent, as a unified diff of at most DIFF_LIMIT bytes: of path alone, if given (one
    of them), else of the first DIFF_FILES of them."""
    if rev == "WORKTREE":
        base, target = "HEAD", None
    elif sha := commit_sha(paper_root, rev, any_rev):
        base, target = first_parent(paper_root, sha), sha
    else:
        return 404, {"error": "no such commit here"}
    files = changed_files(paper_root, base, target) if base else None
    if files is None:
        return 500, {"error": "git could not list the commit's changes"}
    paths = [f["path"] for f in files]
    if path is not None:
        if path not in paths:
            return 404, {"error": "the commit changed no such file here"}
        paths = [path]
    if not paths:
        return 200, {"rev": target or rev, "files": files, "diff": "", "truncated": False}
    code, data, more = git_capped(paper_root, [
        "diff", "--no-color", "--no-ext-diff", "--no-textconv", "--no-renames", "--relative", "--src-prefix=a/",
        "--dst-prefix=b/", base, *([target] if target else []), "--", *paths[:DIFF_FILES]], DIFF_LIMIT)
    if code is None or code != 0 and not data:
        return 500, {"error": "git could not show the changes"}
    text = data.decode("utf-8", "replace")
    if more:
        text = text[:text.rfind("\n") + 1]
    return 200, {"rev": target or rev, "files": files, "diff": text, "truncated": more or len(paths) > DIFF_FILES}


def file_at_commit(paper_root, sha, rel, limit):
    """(bytes, None) of rel (relative to the paper root, shown_path) as commit sha (a full id: commit_sha) left it, if it
    was a regular file there of at most limit bytes; else (None, why)."""
    if not shown_path(rel):
        return None, "not a file that history shows"
    rel = os.path.normpath(rel)
    entry = git_text(paper_root, ["ls-tree", "-z", sha, "--", rel], limit=10_000)
    meta, _, name = (entry or "").rstrip("\0").partition("\t")
    mode, kind, blob = (meta.split() + ["", "", ""])[:3]
    if name != rel.replace(os.sep, "/") or kind != "blob" or mode not in ("100644", "100755") or not SHA_RE.fullmatch(blob):
        return None, f"{rel} is not a file in that commit"
    code, data, more = git_capped(paper_root, ["cat-file", "blob", blob], limit)
    if more:
        return None, f"{rel} was too big there"
    if code != 0:
        return None, f"git could not read {rel} there"
    return data, None
