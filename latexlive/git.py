"""The paper's git checkout, read only: its git directory and whether git is busy there, the last
pushed commit, and the recent commits for the viewer's compare menu."""

import os
import subprocess


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
