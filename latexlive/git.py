"""The paper's git checkout, read only: its git directory and whether git is busy there, the last
pushed commit, and the recent commits for the viewer's compare menu."""

import os
import re
import subprocess
import threading

from . import config

REV_RE = re.compile(r"^[0-9A-Za-z._^~/-]{1,80}$")
DIFF_LIMIT = 512_000  # bytes of unified diff sent to the viewer's History panel


def _visible_path(path, confined):
    """Whether a paper-relative git path is safe to list/diff (hides dotfiles and BUILD when confined)."""
    if not path or "\0" in path or os.path.isabs(path):
        return False
    norm = os.path.normpath(path)
    parts = norm.split(os.sep)
    if norm in (".", "..") or parts[0] == "..":
        return False
    if confined and (any(p.startswith(".") for p in parts) or norm == config.BUILD or norm.startswith(os.path.join(config.BUILD, ""))):
        return False
    return True


def _run_capped(argv, limit, env, timeout=30):
    """Stream stdout of argv up to limit + 1 bytes under a hard timeout, without stderr pipe deadlock."""
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
    timed_out = [False]

    def _abort():
        timed_out[0] = True
        try:
            proc.kill()
        except OSError:
            pass

    timer = threading.Timer(timeout, _abort)
    timer.daemon = True
    timer.start()
    chunks, total = [], 0
    truncated = False
    try:
        while True:
            data = proc.stdout.read(min(65536, limit + 1 - total))
            if not data:
                break
            chunks.append(data)
            total += len(data)
            if total > limit:
                truncated = True
                proc.terminate()
                break
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        return -1, b"", True
    finally:
        timer.cancel()
        if proc.stdout:
            proc.stdout.close()
    if timed_out[0]:
        return -1, b"", True
    raw = b"".join(chunks)[:limit]
    return proc.returncode, raw, truncated


def _strip_hidden_diff(diff_text):
    """Fail-closed filter dropping any diff section that does not cleanly name visible non-dotfile
    paths inside paper_root (prevents quotePath / mnemonicPrefix / noprefix leaks)."""
    sections = re.split(r"(?=^diff --git )", diff_text, flags=re.M)
    kept = []
    for sec in sections:
        if not sec.startswith("diff --git "):
            continue
        m = re.match(r"^diff --git a/(.+?) b/(.+)$", sec.splitlines()[0])
        if not m or m.group(1).startswith('"') or m.group(2).startswith('"'):
            continue
        if not _visible_path(m.group(1), True) or not _visible_path(m.group(2), True):
            continue
        kept.append(sec)
    return "".join(kept)


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


def valid_rev(paper_root, rev, any_rev=True):
    """Resolve rev to a full commit SHA inside paper_root, or None if invalid or (without any_rev)
    not an ancestor of HEAD."""
    if not isinstance(rev, str) or not rev or rev.startswith("-") or not REV_RE.fullmatch(rev):
        return None
    r = subprocess.run(["git", "-C", paper_root, "rev-parse", "--verify", f"{rev}^{{commit}}"],
                       capture_output=True, text=True, env=git_read_env(), timeout=15)
    if r.returncode != 0:
        return None
    sha = r.stdout.strip()
    if not any_rev:
        anc = subprocess.run(["git", "-C", paper_root, "merge-base", "--is-ancestor", sha, "HEAD"],
                             capture_output=True, env=git_read_env(), timeout=15)
        if anc.returncode != 0:
            return None
    return sha


def history(paper_root, limit=60, confined=False):
    """Structured git timeline scoped strictly to paper_root (--relative -- .): commits with date,
    relative date, author, subject, and changed file list, plus branch and dirty files."""
    env = git_read_env()
    try:
        r = subprocess.run(["git", "-C", paper_root, "rev-parse", "--is-inside-work-tree"],
                           capture_output=True, text=True, env=env, timeout=15)
        if r.returncode != 0 or r.stdout.strip() != "true":
            return {"isGit": False, "branch": "", "dirty": False, "dirtyFiles": [], "commits": []}
        branch = subprocess.run(["git", "-C", paper_root, "symbolic-ref", "-q", "--short", "HEAD"],
                                capture_output=True, text=True, env=env, timeout=15).stdout.strip()
        st = subprocess.run(["git", "-C", paper_root, "-c", "core.quotePath=false", "--literal-pathspecs", "status", "--porcelain",
                             "--untracked-files=no", "--", "."],
                            capture_output=True, text=True, env=env, timeout=15).stdout
        prefix = subprocess.run(["git", "-C", paper_root, "rev-parse", "--show-prefix"],
                                capture_output=True, text=True, env=env, timeout=15).stdout.strip()
        dirty_files = []
        for l in st.splitlines():
            if len(l) <= 3:
                continue
            raw_p = l[3:].strip().strip('"')
            paths_in_line = [seg.strip().strip('"') for seg in raw_p.split(" -> ")]
            cleaned = [seg[len(prefix):] if prefix and seg.startswith(prefix) else seg for seg in paths_in_line]
            if all(_visible_path(seg, confined) for seg in cleaned):
                dirty_files.append({"status": l[:2].strip() or "M", "path": cleaned[-1]})
        sep = "\x1e"
        log = subprocess.run(
            ["git", "-C", paper_root, "-c", "core.quotePath=false", "--literal-pathspecs", "log", f"-n{min(max(1, limit), 100)}",
             "--relative", "--date=format:%b %d %H:%M",
             f"--format={sep}%H%x09%h%x09%ad%x09%ar%x09%an%x09%s", "--name-status", "--", "."],
            capture_output=True, text=True, errors="replace", env=env, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return {"isGit": False, "branch": "", "dirty": False, "dirtyFiles": [], "commits": []}
    entries = []
    for block in log.split(sep):
        block = block.strip("\n")
        if not block:
            continue
        lines = block.splitlines()
        parts = lines[0].split("\t", 5)
        if len(parts) < 6:
            continue
        files = []
        for fl in lines[1:]:
            if not fl.strip():
                continue
            fp = fl.split("\t")
            if len(fp) >= 2 and _visible_path(fp[-1], confined):
                files.append({"status": fp[0][0], "path": fp[-1]})
        entries.append({"full": parts[0], "rev": parts[1], "date": parts[2], "relDate": parts[3],
                        "author": parts[4], "subject": parts[5], "files": files[:40]})
    return {"isGit": True, "branch": branch, "dirty": bool(dirty_files), "dirtyFiles": dirty_files[:40], "commits": entries}


def commit_diff(paper_root, rev, file_path=None, any_rev=True, confined=False):
    """Unified diff for a commit (scoped to paper_root via --relative --literal-pathspecs, or
    working tree when rev == 'WORKTREE'), streamed and capped at DIFF_LIMIT bytes."""
    env = git_read_env()
    if file_path:
        if (not isinstance(file_path, str) or file_path.startswith(("-", "/", ":")) or "\0" in file_path
                or not _visible_path(file_path, confined)):
            return 400, {"error": "invalid file path"}
        path_args = ["--", os.path.normpath(file_path)]
    else:
        path_args = ["--", "."]
    diff_flags = ["-c", "core.quotePath=false", "-c", "diff.noprefix=false", "-c", "diff.mnemonicPrefix=false",
                  "--literal-pathspecs"]
    try:
        if rev == "WORKTREE":
            _, raw, truncated = _run_capped(
                ["git", "-C", paper_root, *diff_flags, "diff", "--no-color", "--no-ext-diff", "--no-textconv",
                 "--relative", "--src-prefix=a/", "--dst-prefix=b/", "HEAD", *path_args],
                DIFF_LIMIT, env,
            )
            diff_text = raw.decode("utf-8", errors="replace")
            if confined:
                diff_text = _strip_hidden_diff(diff_text)
            return 200, {"rev": "WORKTREE", "subject": "Uncommitted working tree changes", "author": "", "date": "Now",
                         "diff": diff_text, "truncated": truncated}
        sha = valid_rev(paper_root, rev, any_rev=any_rev)
        if not sha:
            return 404, {"error": "commit not found"}
        meta = subprocess.run(["git", "-C", paper_root, "show", "-s", "--date=format:%b %d %H:%M",
                               "--format=%h%x09%ad%x09%an%x09%s", sha],
                              capture_output=True, text=True, errors="replace", env=env, timeout=15).stdout.strip().split("\t", 3)
        _, raw, truncated = _run_capped(
            ["git", "-C", paper_root, *diff_flags, "show", "--no-color", "--no-ext-diff", "--no-textconv",
             "--relative", "--src-prefix=a/", "--dst-prefix=b/", "--format=", "--root", sha, *path_args],
            DIFF_LIMIT, env,
        )
    except (OSError, subprocess.SubprocessError):
        return 500, {"error": "git diff failed"}
    diff_text = raw.decode("utf-8", errors="replace")
    if confined:
        diff_text = _strip_hidden_diff(diff_text)
    return 200, {"rev": meta[0] if meta else sha[:7], "date": meta[1] if len(meta) > 1 else "",
                 "author": meta[2] if len(meta) > 2 else "", "subject": meta[3] if len(meta) > 3 else "",
                 "diff": diff_text, "truncated": truncated}


def file_at_commit(paper_root, rev, rel, any_rev=True, limit=4_000_000):
    """UTF-8 text of paper-relative rel at commit rev, streamed up to limit bytes, or (None, error)."""
    sha = valid_rev(paper_root, rev, any_rev=any_rev)
    if not sha:
        return None, "commit not found"
    if not isinstance(rel, str) or not rel or rel.startswith(("-", "/", ":")) or "\0" in rel or not _visible_path(rel, True):
        return None, "invalid file path"
    env = git_read_env()
    try:
        prefix = subprocess.run(["git", "-C", paper_root, "rev-parse", "--show-prefix"],
                                capture_output=True, text=True, env=env, timeout=15).stdout.strip()
        git_rel = f"{prefix}{os.path.normpath(rel)}"
        rc, raw, truncated = _run_capped(["git", "-C", paper_root, "show", f"{sha}:{git_rel}"], limit, env, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None, "git show failed"
    if truncated:
        return None, "file is too large to restore"
    if rc != 0:
        return None, f"{rel} was not in commit {sha[:7]}"
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, "file is not UTF-8 text"


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
