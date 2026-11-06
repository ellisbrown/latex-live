"""Shared mode's auto-commit (commit_checks) and its stop and restart (shared_stop_checks): edits build, baselines
only on a POST, no container left, an edit uncommitted at the stop committed at the next start, Secure cookies on
an https public URL, the proxy's uid."""

import glob
import os
import re
import subprocess
import sys
import time

from .base import SHARED_PORT, fail, ok, prints_token, redact, wait_for
from .helpers import editor_calls, git, git_out, read_json_file, read_text, start, stop, texlive_image
from .shared_helpers import (
    commit_user, expect, expect_dict, header, instance_containers, recorded, save, send, settled, shared_status,
    via_proxy, with_paragraph, with_token)


def commit_checks(sh):
    """Auto-commit: the owner's POST commit commits what is pending; every commit since the start is one
    user's edits, authored as that user from the users file and committed as the owner, with only files the
    server wrote; only the shared branch moved; the paper's hooks and signing settings did not apply; the
    HEAD baseline was not rebuilt for each commit."""
    root, users = sh["root"], sh["users"]
    flushed = expect_dict(with_token(sh["token"], "/commit", "POST", {}), 200, "the owner's POST commit")
    if not isinstance(flushed.get("commits"), list):
        fail(f"POST commit's answer has no commits list: {flushed}")
    if os.path.exists(os.path.join(sh["tmp"], "hook-ran")):
        fail("the paper's git hooks ran for a commit (the server commits with core.hooksPath=/dev/null)")
    shas = git_out(root, "rev-list", "--reverse", f"{sh['base']}..shared").split()
    if not shas:
        fail("nothing was committed on the shared branch")
    wrote = {}
    for sha in shas:
        name, email, cname, cemail, parents, subject = git_out(
            root, "show", "-s", "--format=%an%x1f%ae%x1f%cn%x1f%ce%x1f%P%x1f%s", sha).split("\x1f", 5)
        files = git_out(root, "diff-tree", "--no-commit-id", "--name-only", "-r", sha).split()
        uid = commit_user(root, sha)
        user = users.get(uid) or {}
        if (not user or (name, email) != (user["name"], user["email"]) or (cname, cemail) != ("Owner Example", "owner@example.org")
                or len(parents.split()) != 1 or not subject.startswith(f"{user['name']}: ") or not files
                or not all(f in subject for f in files)):
            fail(f"commit {sha[:9]}: author {name} <{email}>, committer {cname} <{cemail}>, {subject!r}, user {uid}, files {files}"
                 " (want the user's name and email from the users file, the owner's as committer, '<Name>: ...' naming the files,"
                 " a Latex-Live-Shared trailer)")
        wrote.setdefault(uid, set()).update(files)
    if wrote != {"ed": {"body.tex", "crlf.bib"}, "cm": {"body.tex"}}:
        fail(f"the commits hold other files, or other users' edits, than the server wrote for each: {wrote}")
    if git_out(root, "symbolic-ref", "HEAD") != "refs/heads/shared":
        fail("HEAD is no longer the shared branch")
    others = [line for line in git_out(root, "for-each-ref", "--format=%(refname) %(objectname)").splitlines()
              if not line.startswith("refs/heads/shared ")]
    if others != [line for line in sh["refs"].splitlines() if not line.startswith("refs/heads/shared ")]:
        fail(f"auto-commit moved refs other than the shared branch: {others}")
    if subprocess.run(["git", "-C", root, "merge-base", "--is-ancestor", sh["base"], "shared"]).returncode:
        fail("the shared branch no longer builds on where it started")
    status = git_out(root, "status", "--porcelain", "--untracked-files=normal").splitlines()
    changed = [line for line in status if not line.startswith("??")]
    untracked = sorted(line[3:] for line in status if line.startswith("??"))
    if changed != [" M refs.bib"]:
        fail(f"after POST commit, the tracked changes are {changed}, not just the test's own change to refs.bib (LATER_CHANGE)")
    if untracked != sorted([".latexmkrc", ".private.bib", "appendix.tex", "big.bib", "build/", "latexmkrc", "latin.bib", "leak.bib",
                            "main.pdf", "reviews.pdf"]):
        fail(f"the shared copy has unexpected untracked files: {untracked}")
    if os.path.isdir(os.path.join(root, "build", "head")):
        fail("shared mode built the HEAD baseline though no viewer asked for base=head (each commit moves HEAD)")
    ok(f"auto-commit: {len(shas)} commits, each one user's (author from the users file, committed by the owner), only files"
       " the server wrote, on the shared branch only")


def shared_stop_checks(sh, sharing):
    """The edits build. An edit still uncommitted when the instance stops (git in the middle of a merge, which
    the stop doesn't wait out) is kept in its state folder for the next start; stopping removes the instance
    from its list. Restarted without a build on an https public URL, it commits that edit as its writer's,
    and the test's change beside it (LATER_CHANGE) as the owner's; a personal link's cookie is Secure on the
    public Host only, and with a proxy uid that is not the peer's (Linux), the identity header counts for
    nothing. Neither run printed a token or a traceback."""
    root, path, pending = sh["root"], os.path.join(sh["root"], "body.tex"), os.path.join(sh["state"], "pending", "*.json")

    def built():
        if not settled(sh):
            return None
        info = expect_dict(via_proxy("vw", "/status/main.pdf"), 200, "the shared copy's status")
        result = info.get("build") or {}
        if result.get("ok") is False:
            fail(f"the shared copy no longer builds after the edits: {result.get('errors')}")
        return result.get("ok") and not info.get("building") and info.get("version") != sh["version0"]

    wait_for("the shared copy to build the edits", built, timeout=120)
    ok("the shared copy builds with everyone's edits and notes")

    for _ in range(2):  # (the second after the commit check's interval: a GET still builds nothing)
        head = (expect_dict(via_proxy("ed", "/status/main.pdf?base=head"), 200, "the status, asking for base=head") or {}).get("head")
        if not isinstance(head, dict) or head.get("state") != "waiting" or os.path.isdir(os.path.join(root, "build", "head")):
            fail(f"a GET of the status started the HEAD baseline's build (shared mode builds only on a POST): {head}")
        time.sleep(3.5)
    asked = expect_dict(via_proxy("ed", "/baseline", "POST", {"kind": "head"}), 200, "the editor asking for the HEAD baseline")
    if asked.get("state") not in ("running", "done"):
        fail(f"POST baseline for the HEAD baseline: {asked}")
    head = wait_for("the HEAD baseline", lambda: (shared_status(sh).get("head") or {}).get("state") in ("done", "failed")
                    and shared_status(sh)["head"], timeout=120, step=0.5)
    if head.get("state") != "done" or not head.get("version"):
        fail(f"the HEAD baseline, asked for with POST baseline, did not build: {head}")
    ok("a GET of the status builds no baseline; an editor's POST baseline does")

    tip = git_out(root, "rev-parse", "shared")
    current = expect_dict(via_proxy("ed", "/file?path=body.tex"), 200, "GET file body.tex")
    last = with_paragraph(current["text"], 11, "Paragraph eleven, saved just before the stop.")
    save("ed", {"path": "body.tex", "base": current["version"], "text": last}, 200, "a save just before the stop")
    git(root, "-c", "core.hooksPath=/dev/null", "merge", "--no-ff", "--no-commit", "side")  # (before the save's quiet period ends)
    # A container as a podman client leaves it when it is stopped between creating the container and starting it (as
    # stopping the instance's process group may do to a pass's): created, never started. The stop removes it too.
    if sh.get("instance"):
        subprocess.run(["podman", "create", "--pull=never", "--entrypoint=", "--label", f"latex-live.instance={sh['instance']}",
                        "--label", "latex-live.run=clean-0", "--label", "latex-live.kind=clean", texlive_image()[0], "true"],
                       capture_output=True, timeout=60, check=True)
    stop(sh["proc"])
    sharing.remove(sh["proc"])
    if recorded(sh["state"]):
        fail(f"the stopped shared-mode instance is still listed: {recorded(sh['state'])}")
    if left := instance_containers(sh["proc"].pid, every=True):
        subprocess.run(["podman", "rm", "-f", "-t", "0", *left], capture_output=True, timeout=60)
        fail(f"the stopped shared-mode instance left {len(left)} containers (running, or created and never started)")
    ok("stopping the shared-mode instance removes its containers, also one created and never started")
    kept = [read_json_file(p) or {} for p in glob.glob(pending)]
    if (git_out(root, "rev-parse", "shared") != tip
            or [k.get("pending", {}).get("ed", {}).get("paths") for k in kept] != [{"body.tex": "edit"}]):
        fail(f"the editor's save just before a stop during a merge is committed, or not kept for the next start: {kept}")
    quiet_log(sh["log"], "the shared-mode instance")
    git(root, "-c", "core.hooksPath=/dev/null", "merge", "--abort")
    if read_text(path) != last:
        fail("aborting the merge undid the editor's save (a mistake in this check)")
    linux = sys.platform.startswith("linux")
    env = {**sh["env"], "LATEX_LIVE_PUBLIC_URL": "https://paper.invalid"}
    if linux:
        env["LATEX_LIVE_PROXY_UID"] = str(os.getuid() + 1)
    proc = start(root, "main", "--no-build", env=env, port=SHARED_PORT)
    sharing.append(proc)
    log = proc.log

    def answering():
        if proc.poll() is not None:
            fail(f"the restarted shared-mode instance exited (status {proc.returncode})")
        r = send("GET", "/me", [("Cookie", f"latex_live={sh['token']}")], quiet=True)
        return r is not None and r[0] == 200

    wait_for("the shared-mode instance, restarted", answering, timeout=60)
    end = time.monotonic() + 20  # (it commits what the last run left before it serves a write)
    while glob.glob(pending) or git_out(root, "status", "--porcelain", "--untracked-files=no"):
        if time.monotonic() > end:
            fail(f"the restarted instance left edits uncommitted ({git_out(root, 'status', '--porcelain', '--untracked-files=no')!r}),"
                 f" or kept for later: {[read_json_file(p) for p in glob.glob(pending)]}")
        time.sleep(0.2)
    shas = git_out(root, "rev-list", "--reverse", f"{tip}..shared").split()
    found = {commit_user(root, sha): (git_out(root, "show", "-s", "--format=%an <%ae>", sha),
                                      git_out(root, "diff-tree", "--no-commit-id", "--name-only", "-r", sha).split()) for sha in shas}
    want = {"ed": ("Eden Doyle <eden@example.org>", ["body.tex"]), "unattributed": ("Owner Example <owner@example.org>", ["refs.bib"])}
    if len(shas) != 2 or found != want:
        fail(f"at the restart, the commits are {found}: not the editor's save before the stop as theirs, and the change made"
             " beside the server (refs.bib) as the owner's")
    ok("an edit uncommitted when the instance stopped (a merge in progress) is committed as its writer's at the next start,"
       " and the change made beside the server as the owner's")

    tk = sh["users"]["tk"]["token"]
    for host, secure in (("paper.invalid", True), (None, False)):
        first = send("GET", f"/me?t={tk}", [("Host", host)] if host else [])
        set_cookie = header(first, "Set-Cookie") or ""
        if first[0] != 200 or bool(re.search(r";\s*secure\b", set_cookie, re.I)) != secure:
            fail(f"a personal link on {host or 'a loopback Host'} with an https public URL: {first[0]}, cookie {redact(set_cookie)!r}"
                 f" ({'' if secure else 'not '}Secure wanted)")
    if linux:
        expect(via_proxy("ed", "/me"), 403, "the identity header from a peer whose uid is not LATEX_LIVE_PROXY_UID")
    stop(proc)
    sharing.remove(proc)
    if recorded(sh["state"]):
        fail(f"the stopped shared-mode instance is still listed: {recorded(sh['state'])}")
    quiet_log(log, "the restarted shared-mode instance")
    if editor_calls(sh["host_tex_log"]):
        fail(f"shared mode ran the host's TeX: {editor_calls(sh['host_tex_log'])}")
    if not linux:
        print("skip: LATEX_LIVE_PROXY_UID is not checked here (it needs Linux's /proc/net)", flush=True)
    ok("with an https public URL, cookies are Secure on its Host only"
       + ("; the identity header counts only from a peer with LATEX_LIVE_PROXY_UID" if linux else "")
       + "; stopped shared instances are no longer listed")


def quiet_log(path, what):
    """Check an instance's output, once it has stopped: no token (its log may be kept, and read by others), and
    no traceback (an error the server did not expect)."""
    with open(path, errors="replace") as f:
        printed = f.read()
    if prints_token(printed):
        fail(f"{what} printed a token")
    if "Traceback (most recent call last)" in printed:
        fail(f"{what} printed a traceback:\n{printed[printed.index('Traceback (most recent call last)'):][:2000]}")
