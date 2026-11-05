"""Auto-commit (shared mode, LATEX_LIVE_COMMIT=1): what the server writes for each user (their saves, their
notes) is committed to git as theirs, one commit per pause of theirs (LATEX_LIVE_COMMIT_IDLE seconds),
only the files the server wrote for them, with a "Latex-Live-Shared: <user id>" trailer; the owner
commits everything pending at once with POST /commit. A commit takes a whole file, so before the server
writes a file for one user, what it wrote there for others is committed first (commit_others): each
commit holds one person's edits. Commits run under NOTE_LOCK, as writes do, so no write lands in a file
while it is committed. They wait while git is busy (a merge, rebase or cherry-pick, or another git
command holding the index). Who wrote what, not committed yet, is kept in the state folder too
(save_pending), so that a restart, even after a crash, still commits it as theirs; edits found
uncommitted at the start that no one's writes account for are committed then, as the owner's
(UNATTRIBUTED), rather than in the next coauthor's commit of that file."""

import json
import math
import os
import re
import subprocess
import threading
import time
import traceback

from . import config
from .auth import ROLES, USER_ID_RE, users_owner
from .files import read_json
from .git import git_busy, git_operation, git_read_env
from .sources import NOTE_LOCK, editable_files


UNATTRIBUTED = "(unattributed)"  # (the pending entry of edits found uncommitted at the start; no user has this id: USER_ID_RE)


def remember_write(server, user, rel, kind):
    """Remember that the server wrote rel (relative to the paper root) for user (a principal), kind
    "edit" (a save) or "notes", to commit it as theirs once they pause (commit_loop)."""
    if not server.autocommit:
        return
    with server.pending_lock:
        server.write_seq += 1
        entry = server.pending.setdefault(user["id"], {"paths": {}})
        old = entry["paths"].get(rel)
        entry.update(user=user, due=time.monotonic() + config.COMMIT_IDLE)
        entry["paths"][rel] = ("edit" if kind == "edit" or (old and old[0] == "edit") else kind, server.write_seq)
        save_pending(server)


def commit_others(server, user, rel):
    """Before the server writes rel for user (holding NOTE_LOCK): commit what it wrote there for
    anyone else and hasn't committed yet, as theirs, so that the next commit of rel is user's alone.
    False if that can't be done within 2 s (git busy: another git command holds its index; or the
    commit failed, which commit_pending has said why): the caller then writes nothing (updating), so
    that no one's edits are ever committed as someone else's."""
    if not server.autocommit:
        return True
    end = time.monotonic() + 2
    while True:
        with server.pending_lock:
            others = [uid for uid, entry in server.pending.items() if uid != user["id"] and rel in entry["paths"]]
        if not others:
            return True
        commit_pending(server, others, only=rel)
        with server.pending_lock:
            if not any(uid in server.pending and rel in server.pending[uid]["paths"] for uid in others):
                return True
        if time.monotonic() > end:
            return False
        time.sleep(0.2)


def paused(server):
    """Why the shared copy takes no writes now: git in the middle of a merge, rebase, cherry-pick or
    revert there (git_operation), whose result would take the writes, or whose abort would lose them;
    else None. Only in shared mode: the owner's own previews write as they always did."""
    return git_operation(server) if config.SHARED else None


def updating():
    """The answer to a write that the shared copy can't take now (paused, commit_others): 503, which
    the viewer tries again, keeping the text meanwhile (the editor's, the note box's)."""
    return 503, {"error": "the shared copy is being updated; try again in a moment", "retry": True}


def save_pending(server):
    """Keep who wrote which files, not committed yet, in the state folder (server.pending_file), so
    that a restart, even after a crash, still commits them as theirs (load_pending). The caller holds
    pending_lock."""
    entries = {uid: {"user": {key: entry["user"].get(key) for key in ("id", "name", "email", "macro", "role")},
                     "paths": {rel: kind for rel, (kind, _) in entry["paths"].items()}}
               for uid, entry in server.pending.items() if entry["paths"]}
    path = server.pending_file
    try:
        if not entries:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            return
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
            json.dump({"root": server.paper_root, "pending": entries}, f)
        os.replace(tmp, path)
    except OSError as e:
        commit_notice(server, f"could not keep the list of uncommitted edits in {path} ({e})")


def load_pending(server):
    """The writes the last run left uncommitted (save_pending), pending again, to be committed as
    theirs right away: those of valid users (as load_users checks them) to files that are editable
    (editable_files; the state folder could have been changed meanwhile)."""
    root = os.path.realpath(server.paper_root)
    editable = set(editable_files(server).values())
    data = read_json(server.pending_file)
    saved = data.get("pending") if isinstance(data, dict) else None
    with server.pending_lock:
        for uid, entry in (saved or {}).items() if isinstance(saved, dict) else ():
            user, paths = (entry.get("user"), entry.get("paths")) if isinstance(entry, dict) else (None, None)
            if not (isinstance(user, dict) and user.get("id") == uid and user.get("role") in ROLES and isinstance(paths, dict)
                    and (uid == UNATTRIBUTED or USER_ID_RE.fullmatch(uid))):
                continue
            fields = {key: user.get(key) if isinstance(user.get(key), str) else None for key in ("id", "name", "email", "macro", "role")}
            if any(value and (len(value) > 200 or re.search(r"[\x00-\x1f\x7f<>]", value)) for value in fields.values()):
                continue  # (as load_users checks the users file: they go into the commit's author and subject)
            kept = {}
            for rel, kind in paths.items():
                if isinstance(rel, str) and kind in ("edit", "notes") and not os.path.isabs(rel) \
                        and os.path.realpath(os.path.join(root, rel)) in editable:
                    server.write_seq += 1
                    kept[os.path.relpath(os.path.realpath(os.path.join(root, rel)), root)] = (kind, server.write_seq)
            if kept:
                user = fields
                server.pending[uid] = {"user": {**user, "via": "restart"}, "paths": kept, "due": time.monotonic()}
        if server.pending:
            print("=== latex-live: the last run left edits uncommitted; committing them as theirs: "
                  + "; ".join(f"{e['user']['name'] or uid}: {', '.join(sorted(e['paths']))}" for uid, e in server.pending.items()),
                  flush=True)


def claim_leftovers(server):
    """At the start: the editable files that git tracks and that hold uncommitted edits no pending write
    accounts for (made before a crash, or outside the server), as one pending entry (UNATTRIBUTED),
    committed as the owner's before anyone writes those files again (commit_others)."""
    root = os.path.realpath(server.paper_root)
    with server.pending_lock:
        claimed = {rel for entry in server.pending.values() for rel in entry["paths"]}
    files = sorted({os.path.relpath(real, root) for real in editable_files(server).values()} - claimed)
    if not files:
        return
    git = ["git", "-C", server.paper_root, "--literal-pathspecs"]
    run = lambda args: subprocess.run(git + args, capture_output=True, encoding="utf-8", errors="surrogateescape",
                                      env=git_read_env(), timeout=60)
    r = run(["diff", "--name-only", "-z", "--relative", "HEAD", "--", *files])
    if r.returncode:  # (no commit yet: nothing to compare with)
        return
    changed, tracked = set(r.stdout.split("\0")), set(run(["ls-files", "-z", "--", *files]).stdout.split("\0"))
    left = [rel for rel in files if rel in changed and rel in tracked]
    if not left:
        return
    with server.pending_lock:
        entry = server.pending.setdefault(UNATTRIBUTED, {"paths": {}})
        entry.update(user={"id": UNATTRIBUTED, "name": None, "email": None, "macro": None, "role": "owner", "via": "start"},
                     due=time.monotonic())
        for rel in left:
            server.write_seq += 1
            entry["paths"][rel] = ("edit", server.write_seq)
        save_pending(server)
    print(f"=== latex-live: no one's saves account for the uncommitted edits in {', '.join(left)}; committing them as the"
          " owner's (Latex-Live-Shared: unattributed)", flush=True)


def start_commits(server):
    """Shared mode with LATEX_LIVE_COMMIT=1: commit the users' writes from now on (commit_loop), if
    the paper is in a git checkout. First, before any write is served (NOTE_LOCK), what the last run
    left uncommitted: its users' writes as theirs (load_pending), the rest as the owner's
    (claim_leftovers)."""
    if not config.COMMIT:
        return
    try:
        r = subprocess.run(["git", "-C", server.paper_root, "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True)
    except OSError:
        return print("=== latex-live: git is not installed, so edits are not committed (LATEX_LIVE_COMMIT)", flush=True)
    if r.stdout.strip() != "true":
        return print("=== latex-live: the paper is not in a git checkout, so edits are not committed (LATEX_LIVE_COMMIT)", flush=True)
    server.autocommit = True
    with NOTE_LOCK:
        try:
            load_pending(server)
            claim_leftovers(server)
            if server.pending:
                commit_pending(server)
        except Exception:  # (the server runs on; commit_loop tries again)
            commit_notice(server, f"could not commit what the last run left:\n{traceback.format_exc().rstrip()}")
    threading.Thread(target=commit_loop, args=(server,), daemon=True).start()
    print(f"=== latex-live: each user's edits are committed as theirs after {config.COMMIT_IDLE:g} s of quiet (LATEX_LIVE_COMMIT)",
          flush=True)


def commit_loop(server):
    """The thread that commits each user's pending writes once they have been quiet for
    LATEX_LIVE_COMMIT_IDLE seconds (commit_pending). Whatever goes wrong, it says so once (with where,
    for a bug) and keeps going after a pause."""
    while True:
        with server.pending_lock:  # (it wakes when the first one is due, and at least every second for new writes)
            first = min((entry.get("due", math.inf) for entry in server.pending.values()), default=math.inf)
        time.sleep(min(1, max(0.05, first - time.monotonic())))
        try:
            with server.pending_lock:
                due = [uid for uid, entry in server.pending.items() if entry["due"] <= time.monotonic()]
            if due:
                with NOTE_LOCK:
                    commit_pending(server, due)
        except Exception:
            commit_notice(server, f"commits failed; trying again in a while:\n{traceback.format_exc().rstrip()}")
            time.sleep(10)


def commit_request(server):
    """POST /commit (the owner): commit every pending write now. {"commits": [{user, commit, subject,
    files}]}, with "error" if some could not be."""
    if not server.autocommit:
        return 409, {"error": "edits are not committed here (shared mode does that with LATEX_LIVE_COMMIT=1)"}
    try:
        with NOTE_LOCK:
            made, problem = commit_pending(server)
    except Exception as e:  # (answered, rather than a dropped connection)
        commit_notice(server, f"could not commit:\n{traceback.format_exc().rstrip()}")
        return 500, {"error": f"could not commit ({e})", "commits": []}
    if problem and not made:
        return 409, {"error": problem, "commits": []}
    return 200, {"commits": made, **({"error": problem} if problem else {})}


def commit_at_exit(server):
    """At shutdown: commit what is still pending, as theirs, waiting up to 5 s while another git command
    holds the index. What stays pending (git in the middle of a merge, say) is in the state folder,
    for the next start (load_pending)."""
    if not server.autocommit:
        return
    end = time.monotonic() + 5
    try:
        while server.pending:
            with NOTE_LOCK:
                commit_pending(server)
            if not server.pending or git_operation(server) or not git_busy(server) or time.monotonic() > end:
                break
            time.sleep(0.2)
    except Exception:
        print(f"=== latex-live: could not commit the pending edits:\n{traceback.format_exc().rstrip()}", flush=True)
    if server.pending:
        print(f"=== latex-live: edits stay uncommitted for now; the next start commits them as theirs ({server.pending_file})",
              flush=True)


def commit_pending(server, ids=None, only=None):
    """Commit the pending writes of the users in ids (default: everyone's; of the file only, if
    given), one commit per user (commit_files). The caller holds NOTE_LOCK. While git is busy
    (git_busy), nothing, and they are tried again later; a user's commit that fails (even through a
    bug here) is tried again in a while. Returns ([{user, commit, subject, files}], what went wrong
    or None)."""
    if busy := git_busy(server):
        commit_notice(server, busy)
        with server.pending_lock:  # (commit_loop looks again in a few seconds)
            for entry in server.pending.values():
                entry["due"] = max(entry["due"], time.monotonic() + 3)
        return [], busy
    made, problem = [], None
    with server.pending_lock:
        ids = [uid for uid in (list(server.pending) if ids is None else ids) if uid in server.pending]
    for uid in ids:
        with server.pending_lock:
            user, paths = server.pending[uid]["user"], dict(server.pending[uid]["paths"])
        paths = {rel: value for rel, value in paths.items() if only is None or rel == only}
        who = user["name"] or user["id"]
        try:
            done, failed = commit_files(server, user, paths), None
        except (OSError, RuntimeError, subprocess.SubprocessError) as e:
            done, failed = None, f"could not commit {who}'s edits: {e}"
        except Exception:  # (a bug: said once, with where; the others' commits go on)
            done, failed = None, f"could not commit {who}'s edits:\n{traceback.format_exc().rstrip()}"
        with server.pending_lock:
            entry = server.pending[uid]
            if failed:
                entry["due"] = time.monotonic() + max(10, min(config.COMMIT_IDLE, 60))  # (again in a while)
            else:  # (what was written again meanwhile stays pending)
                for rel, value in paths.items():
                    if entry["paths"].get(rel) == value:
                        del entry["paths"][rel]
                if not entry["paths"]:
                    del server.pending[uid]
                save_pending(server)
        if failed:
            problem = problem or failed
            commit_notice(server, failed)
        if done:
            made.append(done)
    if made and not problem:
        server.commit_said = None
    return made, problem


def commit_notice(server, message):
    """Print why commits wait or fail, once until the reason changes."""
    if message != server.commit_said:
        server.commit_said = message
        print(f"=== latex-live: {message}", flush=True)


def commit_files(server, user, paths):
    """Commit paths ({path relative to the paper root: (kind, sequence number)}) as user, as they are
    in the working tree and nothing else (git commit --only; the committer is the owner), leaving
    out what git doesn't track, what is gone, and what is as committed. Returns {user, commit,
    subject, files}, or None if nothing is left; RuntimeError if git refuses."""
    root = server.paper_root
    git = ["git", "-C", root, "--literal-pathspecs"]
    run = lambda args, env: subprocess.run(git + args, capture_output=True, encoding="utf-8", errors="surrogateescape", env=env,
                                           timeout=60)  # (NOTE_LOCK is held; file names as Python has them, even if not UTF-8)
    files = sorted(rel for rel in paths if os.path.isfile(os.path.join(root, rel)))
    if files:
        tracked = set(run(["ls-files", "-z", "--", *files], git_read_env()).stdout.split("\0"))
        r = run(["diff", "--name-only", "-z", "--relative", "HEAD", "--", *files], git_read_env())
        changed = set(r.stdout.split("\0")) if r.returncode == 0 else set(files)  # (no HEAD yet: all of them)
        files = [rel for rel in files if rel in tracked and rel in changed]
    if not files:
        return None
    owner, owner_email = committer(server)
    name = user["name"] or owner
    email = user["email"] or (owner_email if user["role"] == "owner" else "")
    listed = ", ".join(re.sub(r"[\x00-\x1f\x7f]", "?", rel) for rel in files[:3]) + (f" and {len(files) - 3} more" if len(files) > 3 else "")
    if user["id"] == UNATTRIBUTED:
        subject, trailer = f"Edits found uncommitted at the start: {listed}", "unattributed"
    else:
        verb = "edit" if any(paths[rel][0] == "edit" for rel in files) else "notes in"
        subject, trailer = f"{name}: {verb} {listed}", user["id"]
    env = {key: value for key, value in os.environ.items() if key != "GIT_OPTIONAL_LOCKS"}  # (a commit takes the index)
    r = run(["-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", "-c", "gc.auto=0", "-c", f"user.name={owner}",
             "-c", f"user.email={owner_email}", "commit", "--only", f"--author={name} <{email}>",
             "-m", f"{subject}\n\nLatex-Live-Shared: {trailer}", "--", *files], env)
    if r.returncode:
        raise RuntimeError((r.stderr.strip() or r.stdout.strip() or f"git commit failed ({r.returncode})").splitlines()[-1])
    sha = run(["rev-parse", "--short", "HEAD"], git_read_env()).stdout.strip()
    print(f"=== latex-live: committed {sha}: {subject}", flush=True)
    return {"user": trailer, "commit": sha, "subject": subject, "files": files}


def committer(server):
    """(name, email) that commits coauthors' edits: the users file's owner's, else git's user.name and
    user.email (when they hold no control character, < or >, as the users file's), else latex-live."""
    entry, found = users_owner() or {}, []
    for key in ("name", "email"):
        if not (value := entry.get(key)):
            value = subprocess.run(["git", "-C", server.paper_root, "config", f"user.{key}"], capture_output=True, text=True,
                                   timeout=60).stdout.strip()
            value = "" if re.search(r"[\x00-\x1f\x7f<>]", value) or len(value) > 200 else value
        found.append(value or "latex-live")
    return tuple(found)
