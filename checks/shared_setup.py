"""Shared mode's setup and start: the copy of the scratch paper that it serves (make_shared_paper) and the users
of its users file; the checks that need no instance (tex-exec's confinement, the refusals: --host-tex, the default
state folder, the identity header without LATEX_LIVE_PROXY_UID); the instance (shared_setup); and the checks of
its build and start (shared_start_checks)."""

import ipaddress
import os
import re
import secrets
import shlex
import socket
import subprocess
import sys

from .base import ENV, HERE, SECRETS, SHARED_PORT, fail, ok, prints_token, wait_for
from .helpers import editor_stub, git, git_out, make_state, put, read_json_file, start, stop, texlive_image, write_text
from .shared_helpers import (
    PROXY_HOST, PUBLIC, commit_user, expect, expect_dict, recorded, send, shared_status, with_token, write_users)


# A change to the shared copy that only the test makes, before the shared instance starts: no one's saves
# account for it, so the server commits it then, as the owner's (shared_start_checks). It also tells whether
# TeX can read an absolute path there (shared mode runs it with openin_any=p), and reads slow.tex, disk.tex and
# refer.tex while they exist (rebuild_stop_checks).
LOCAL_CHANGE = r"""\IfFileExists{/etc/passwd}{\typeout{latex-live-check: TeX read an absolute path}}
  {\typeout{latex-live-check: TeX refused an absolute path}}
\IfFileExists{slow.tex}{\input{slow.tex}}{}\IfFileExists{disk.tex}{\input{disk.tex}}{}\IfFileExists{refer.tex}{\input{refer.tex}}{}
"""
# A line for the shared copy's preamble whose \bibdata name is a shell command, which would create bibdata-ran in
# the paper: latexmk hands such names to a shell on the host (its kpsewhich), where it runs. The .bib file of that
# name is in the paper, with a \citation of it and a style, so that bibtex (and the downloads' bibtex) succeeds.
BIBDATA_NAME = "probe$(>bibdata-ran)"  # (no braces or spaces, which bibtex would not take)
BIBDATA_PROBE = ("\\makeatletter\\AtBeginDocument{" + "".join(f"\\immediate\\write\\@auxout{{\\string\\{line}}}" for line in
                 ("citation{probe}", "bibstyle{plain}", "bibdata{" + BIBDATA_NAME + "}"))
                 + "}\\AtEndDocument{\\typeout{No file \\jobname.bbl.}}\\makeatother\n")  # (latexmk then runs bibtex)
# A change that only the test makes once the shared instance runs (to refs.bib, which the build doesn't read):
# the server must not commit it while it runs (commit_checks), only as the owner's at its next start
# (shared_stop_checks).
LATER_CHANGE = "% A change made beside the server, which it did not write.\n"


def host_tex_stub(tmp):
    """Stand-ins for the host's pdflatex and bibtex that log their arguments and fail, for the front of the
    shared instance's PATH: shared mode must never run them (all its TeX runs go through tex-exec, into the
    image). (Their folder, the log, which editor_calls reads.)"""
    folder, log = os.path.join(tmp, "host-tex"), os.path.join(tmp, "host-tex.log")
    os.makedirs(folder)
    for tool in ("pdflatex", "bibtex"):
        with open(os.path.join(folder, tool), "w") as f:
            f.write(f"#!/bin/sh\nprintf '{tool} %s\\n' \"$*\" >> {shlex.quote(log)}\nexit 1\n")
        os.chmod(os.path.join(folder, tool), 0o755)
    return folder, log


# Shared mode: one instance (shared_setup) serves a copy of the paper to the users of a users file, who
# come through a trusted proxy (an identity header) or with a personal link (a token of their own).
def shared_users():
    """The users file's users: the owner, an editor (ed) and a commenter (cm) whose note macros the paper
    defines, a viewer, and an editor with only a personal link (tk), whose macro the paper lacks."""
    return {"owner1": {"name": "Owner Example", "email": "owner@example.org", "macro": "eb", "role": "owner"},
            "ed": {"name": "Eden Doyle", "email": "eden@example.org", "macro": "ed", "role": "editor"},
            "cm": {"name": "Casey Moss", "email": "casey@example.org", "macro": "cm", "role": "commenter"},
            "vw": {"name": "Val Wren", "email": "val@example.org", "macro": "vw", "role": "viewer"},
            "tk": {"name": "Toni Kay", "email": "toni@example.org", "macro": "tk", "role": "editor",
                   "token": secrets.token_urlsafe(18)}}


def shared_setup(tmp, sharing):
    """Check that shared mode refuses the host's TeX, then start the shared-mode instance (it builds while
    the hub's checks run; sharing holds it, to be stopped). Returns what the shared checks need, or None
    where shared mode can't run: it builds only in the TeX Live image, with podman."""
    state = os.path.join(tmp, "shared-state")
    sh = {"tmp": tmp, "state": state, "token": make_state(state), "users": shared_users(), "users_file": os.path.join(tmp, "users.json")}
    SECRETS.extend([sh["token"]] + [user["token"] for user in sh["users"].values() if "token" in user])
    sh["root"] = make_shared_paper(tmp, state)
    stub, sh["editor_log"] = editor_stub(tmp, "shared-editor")
    host_tex, sh["host_tex_log"] = host_tex_stub(tmp)
    sh["env"] = {**ENV, "LATEX_LIVE_STATE": state, "LATEX_LIVE_USERS": sh["users_file"], "LATEX_LIVE_IDENTITY_HEADER": "x-test-user",
                 "LATEX_LIVE_PROXY_HOST": PROXY_HOST, "LATEX_LIVE_PUBLIC_URL": PUBLIC, "LATEX_LIVE_COMMIT": "1",
                 "LATEX_LIVE_COMMIT_IDLE": "2", "LATEX_LIVE_PROTECTED": r"\\eb\{as written\}", "LATEX_LIVE_EDITOR": stub,
                 "LATEX_LIVE_BUILD_LIMIT": "64",
                 "PATH": host_tex + os.pathsep + ENV.get("PATH", os.defpath)}
    if sys.platform.startswith("linux"):  # (the server looks the peer's uid up in /proc/net)
        sh["env"]["LATEX_LIVE_PROXY_UID"] = str(os.getuid())
    write_users(sh)
    tex_exec_check(tmp)
    host_tex_check(sh)
    image, pulled = texlive_image()
    if not pulled:
        print(f"skip: no podman or no {image} here, so shared mode, which builds only in that image, is not checked", flush=True)
        return None
    refusal_checks(sh)
    sh["refs"] = git_out(sh["root"], "for-each-ref", "--format=%(refname) %(objectname)")
    sh["base"] = git_out(sh["root"], "rev-parse", "shared")
    sh["proc"] = start(sh["root"], env=sh["env"], port=SHARED_PORT)
    sharing.append(sh["proc"])
    sh["log"] = sh["proc"].log
    return sh


def make_shared_paper(tmp, state):
    """The copy of the scratch paper that shared mode serves: a clone of its remote on branch "shared", with
    the files that the editor checks need (committed there), a branch that HEAD does not build on ("side"),
    git settings and hooks that the server's commits must override, files that the server must never list,
    serve, write or commit, latexmkrc files that latexmk must not read, and a change only the test makes."""
    root = os.path.join(tmp, "shared")
    subprocess.run(["git", "clone", "-q", "-b", "main", os.path.join(tmp, "remote.git"), root], check=True, capture_output=True)
    git(root, "checkout", "-q", "-b", "side")
    write_text(root, "side.tex", "A commit that the shared branch does not build on.\n")
    git(root, "add", "side.tex")
    git(root, "commit", "-qm", "side")
    git(root, "checkout", "-q", "-b", "shared", "main")
    for name, text in (("generated.inc", "Generated quetzal sentence.\n\n"), ("refs.bib", "@misc{one, title = {One}}\n"),
                       (os.path.join("sub", "refs.bib"), "@misc{two, title = {Two}}\n"),
                       ("crlf.bib", "@misc{three,\r\n  title = {Three},\r\n}\r\n"),
                       (BIBDATA_NAME + ".bib", "@misc{probe, title = {Probe}}\n")):
        write_text(root, name, text)
    with open(os.path.join(root, "body.tex"), "a") as f:
        f.write("\n\\input{generated.inc}\n\\iffalse\\eb{Quagga private draft note}\\fi\n")  # (typeset text from a file the
        # editor does not offer, and a note that is not: role_checks)
    git(root, "add", "-A")
    git(root, "commit", "-qm", "shared fixtures")
    os.chmod(os.path.join(root, "crlf.bib"), 0o640)  # (a save keeps the mode; git does not track this one)
    for key, value in (("commit.gpgsign", "true"), ("gpg.program", "false"), ("user.name", "Not The Owner"),
                       ("user.email", "not-the-owner@example.invalid")):
        subprocess.run(["git", "-C", root, "config", key, value], check=True)
    hooks = os.path.join(root, ".git", "hooks")
    os.makedirs(hooks, exist_ok=True)
    for hook in ("pre-commit", "prepare-commit-msg", "commit-msg", "post-commit"):
        with open(os.path.join(hooks, hook), "w") as f:
            f.write(f"#!/bin/sh\n: > {shlex.quote(os.path.join(tmp, 'hook-ran'))}\nexit 1\n")
        os.chmod(os.path.join(hooks, hook), 0o755)
    write_text(root, "appendix.tex", "\\documentclass{article}\n\\begin{document}\nThe appendix.\n\\end{document}\n")
    write_text(root, "reviews.pdf", "%PDF-1.4\n% another PDF in the paper root\n")
    os.symlink(os.path.join(state, "token"), os.path.join(root, "leak.bib"))
    write_text(root, "big.bib", "%" + " " * (5 << 20) + "\n")  # (over the editor's 4 MB limit)
    with open(os.path.join(root, "latin.bib"), "wb") as f:
        f.write("@misc{four, title = {Caf\u00e9}}\n".encode("latin-1"))
    write_text(root, ".private.bib", "@misc{five}\n")
    write_text(root, os.path.join("build", "stray.bib"), "@misc{six}\n")
    ran = os.path.join(tmp, "latexmkrc-ran").replace("\\", "\\\\").replace("'", "\\'")
    for name in ("latexmkrc", ".latexmkrc"):
        write_text(root, name, f"open(my $f, '>', '{ran}'); close($f);\n")
    with open(os.path.join(root, "people.tex"), "a") as f:
        f.write(LOCAL_CHANGE + BIBDATA_PROBE)
    return root


def tex_exec_check(tmp):
    """tex-exec with LATEX_LIVE_REQUIRE_IMAGE=1 (shared mode) refuses to run a tool on the host while the
    image is missing; without it, it falls back to the host as before. With LATEX_LIVE_SANDBOX=1 (shared
    mode), it confines the container, as a stand-in for podman shows: no capabilities, no new privileges,
    at most 512 processes, 4 GB, 2 CPUs, 1 GB files and the caller's deadline (LATEX_LIVE_TIMEOUT, at most
    10 minutes), labels that name the instance, the run and its kind (for stopping it), the paper read-only
    (its dot entries at any depth, .git among them, hidden) and only build/ writable, no shell escape, and TeX's files
    only below its folder; tools.container_argv in shared mode goes through tex-exec too."""
    cmd = [os.path.join(HERE, "tex-exec"), "localhost/latex-live-check-missing:none", tmp, "true"]
    fallback = subprocess.run(cmd, cwd=tmp, env=ENV, capture_output=True, text=True, timeout=60)
    required = subprocess.run(cmd, cwd=tmp, env={**ENV, "LATEX_LIVE_REQUIRE_IMAGE": "1"}, capture_output=True, text=True, timeout=60)
    if fallback.returncode:
        fail(f"tex-exec without the image no longer falls back to the host's tool: {fallback.stderr}")
    if not required.returncode or not required.stderr.strip():
        fail(f"tex-exec with LATEX_LIVE_REQUIRE_IMAGE=1 and no image ran the host's tool or said nothing (exit {required.returncode})")
    stub = os.path.join(tmp, "podman-stub")
    os.makedirs(stub)
    with open(os.path.join(stub, "podman"), "w") as f:  # (the image exists; a run prints its arguments, one per line)
        f.write('#!/bin/sh\n[ "$1" = image ] && exit 0\nprintf \'%s\\n\' "$@"\n')
    os.chmod(os.path.join(stub, "podman"), 0o755)
    root = os.path.join(tmp, "sandboxed")  # (a paper root with dot entries, which the container must not see)
    os.makedirs(os.path.join(root, ".git"))
    put(os.path.join(root, ".env"), "")
    put(os.path.join(root, "sub", ".secret.bib"), "")  # (and below the top: tex-exec hides them at any depth)
    os.makedirs(os.path.join(root, "sub", "deeper", ".private"))
    confined = subprocess.run([os.path.join(HERE, "tex-exec"), "localhost/latex-live-check:none", root, "pdflatex", "x.tex"], cwd=root,
                              env={**ENV, "LATEX_LIVE_SANDBOX": "1", "LATEX_LIVE_BUILD": "out", "LATEX_LIVE_INSTANCE": "check-1",
                                   "LATEX_LIVE_RUN": "main-7", "LATEX_LIVE_TIMEOUT": "60",
                                   "PATH": stub + os.pathsep + ENV.get("PATH", os.defpath)},
                              capture_output=True, text=True, timeout=60)
    from latexlive import config, tools
    shared, config.SHARED = config.SHARED, True
    started = tools.container_argv(tmp)
    config.SHARED = shared
    want = ("--cap-drop=all", "--security-opt=no-new-privileges", "--pids-limit=512", "--memory=4g", "--cpus=2",
            "fsize=1073741824:1073741824", "--timeout=60", "latex-live.instance=check-1", "latex-live.run=main-7",
            "latex-live.kind=main", f"{root}:{root}:ro", f"{root}/out:{root}/out", "openin_any=p", "openout_any=p",
            "shell_escape=f")
    hidden = {f"{root}/.git", f"{root}/.env", f"{root}/sub/.secret.bib", f"{root}/sub/deeper/.private"}  # (podman's mask)
    args = confined.stdout.splitlines()
    masks = [set(a[len("mask="):].split(":")) for a in args if a.startswith("mask=")]
    if masks != [hidden]:
        fail(f"tex-exec with LATEX_LIVE_SANDBOX=1 masks {masks}, not the paper's dot entries {hidden}")
    if (confined.returncode or not all(w in args for w in want) or f"{root}:{root}" in args
            or args[-3:] != ["localhost/latex-live-check:none", "pdflatex", "x.tex"]):
        fail(f"tex-exec with LATEX_LIVE_SANDBOX=1 runs podman with {args} (exit {confined.returncode}), not with all of {want}"
             " (and the paper only read-only)")
    if started[:1] != [os.path.join(HERE, "tex-exec")]:
        fail(f"tools.container_argv in shared mode is {started}, not through tex-exec")
    ok("tex-exec refuses the host's TeX when shared mode requires the image, and confines the container there (the paper"
       " read-only, limits, labels, 10 minutes at most)")


def refusal_checks(sh):
    """A shared instance refuses to start with the owner's own state folder (it would share their previews' token)
    and, on Linux, with the identity header but no LATEX_LIVE_PROXY_UID (any local program could send it)."""
    config = os.path.join(sh["tmp"], "config")  # (XDG_CONFIG_HOME: the default state folder, here a scratch one)
    no_state = {k: v for k, v in sh["env"].items() if k != "LATEX_LIVE_STATE"}
    no_uid = {k: v for k, v in sh["env"].items() if k != "LATEX_LIVE_PROXY_UID"}
    cases = [({**no_state, "XDG_CONFIG_HOME": config}, "LATEX_LIVE_STATE", "the default state folder")]
    if sys.platform.startswith("linux"):
        cases.append((no_uid, "LATEX_LIVE_PROXY_UID", "the identity header without LATEX_LIVE_PROXY_UID"))
    for env, word, what in cases:
        r = subprocess.run([sys.executable, os.path.join(HERE, "live.py"), "main.tex", "--no-build", "--no-open", "--port",
                            str(SHARED_PORT)], cwd=sh["root"], env=env, capture_output=True, text=True, timeout=60)
        if not r.returncode or word not in r.stdout + r.stderr:
            fail(f"a shared instance with {what} exited {r.returncode} without refusing (naming {word}):\n{r.stdout + r.stderr}")
    if os.path.exists(os.path.join(config, "latex-live")):
        fail("a shared instance refused for the default state folder still wrote there")
    ok("a shared instance refuses the default state folder" + (", and the identity header without LATEX_LIVE_PROXY_UID"
                                                                if len(cases) > 1 else ""))


def host_tex_check(sh):
    """Shared mode refuses --host-tex: collaborators' LaTeX must not run in the host's TeX, which can read
    the owner's files."""
    proc = subprocess.Popen([sys.executable, os.path.join(HERE, "live.py"), "appendix.tex", "--host-tex", "--no-open", "--port",
                             str(SHARED_PORT)], cwd=sh["root"], env=sh["env"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, start_new_session=True)
    try:
        out = proc.communicate(timeout=30)[0]
    except subprocess.TimeoutExpired:
        stop(proc)
        fail("shared mode with --host-tex kept running (it must refuse to build with the host's TeX)")
    if not proc.returncode or not out.strip():
        fail(f"shared mode with --host-tex exited {proc.returncode} without refusing:\n{out}")
    if f"{proc.pid}.json" in recorded(sh["state"]):
        fail("the instance that refused --host-tex is still listed")
    ok("shared mode refuses --host-tex")


def outside_address():
    """This machine's address on its network (not loopback), or None."""
    for family, probe in ((socket.AF_INET, "192.0.2.1"), (socket.AF_INET6, "2001:db8::1")):
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as s:
                s.connect((probe, 9))  # (sends nothing: it only picks the address that traffic would leave from)
                address = s.getsockname()[0]
        except OSError:
            continue
        if not ipaddress.ip_address(address.split("%")[0]).is_loopback:
            return address
    return None


def shared_start_checks(sh, own, hub_token):
    """The shared-mode instance's build and start: hardened, alone on exactly its port (none other when it
    is taken), loopback peers only, its public URL printed and no token anywhere (nor when another instance
    finds it running), no part in the hub, and the edits it found uncommitted committed as the owner's."""
    root, cookie = sh["root"], [("Cookie", f"latex_live={sh['token']}")]

    def published():
        if sh["proc"].poll() is not None:  # (e.g. its port is taken, perhaps as another program's ephemeral port)
            fail(f"the shared-mode instance exited (status {sh['proc'].returncode}) before its first build")
        info = shared_status(sh).get("build") or {}
        if info.get("ok") is False:
            fail(f"the shared copy does not build in the hardened image: {info.get('errors')}")
        return info.get("ok") is True and os.path.isfile(os.path.join(root, "main.pdf"))

    wait_for("the shared copy's main.pdf", published)
    clean = wait_for("the shared copy's notes-free pass", lambda: shared_status(sh).get("cleanBuild"))
    if not clean.get("ok"):
        fail(f"the shared copy's notes-free pass failed in the hardened image: {clean}")

    def answering():
        r = send("GET", "/me", cookie, quiet=True)
        if r and r[0] != 200:
            fail(f"GET me with the shared instance's owner cookie: {r[0]} {r[2][:200]!r}")
        return r

    wait_for("the shared-mode instance", answering, timeout=60)
    sh["version0"] = expect_dict(with_token(sh["token"], "/status/main.pdf"), 200, "the shared copy's status")["version"]
    with open(sh["log"], errors="replace") as f:
        printed = f.read()
    if PUBLIC not in printed:
        fail(f"the shared-mode instance does not print its public URL ({PUBLIC})")
    if prints_token(printed):
        fail("the shared-mode instance prints a token (its own, a personal link's, or a ?t= link), which its log keeps")
    if re.search(r"/p/[^/\s]+/[^/\s]+/", printed) or os.path.exists(os.path.join(sh["state"], "hub.lock")):
        fail("the shared-mode instance takes part in the hub (a hub URL, or hub.lock in its state)")
    entry = read_json_file(os.path.join(sh["state"], "instances", f"{sh['proc'].pid}.json")) or {}
    lock = read_json_file(os.path.join(root, "build", "main-live.lock")) or {}
    if entry.get("port") != SHARED_PORT or lock.get("port") != SHARED_PORT:
        fail(f"the shared-mode instance is not recorded on port {SHARED_PORT}: instance {entry}, lock {lock}")
    if os.path.exists(os.path.join(sh["tmp"], "latexmkrc-ran")):
        fail("the paper's own latexmkrc ran (shared mode builds without latexmk)")
    with open(os.path.join(root, "build", "main-live.aux"), errors="replace") as f:
        if "\\bibdata{" + BIBDATA_NAME + "}" not in f.read():
            fail("the \\bibdata probe did not reach the shared copy's .aux, so it proves nothing")
    if any(os.path.exists(os.path.join(where, "bibdata-ran")) for where in (root, os.path.join(root, "build"))):
        fail("a \\bibdata name in the shared copy's .aux ran as a shell command on this machine")
    with open(os.path.join(root, "build", "main-live.log"), errors="replace") as f:
        texlog = f.read()
    if "latex-live-check: TeX refused an absolute path" not in texlog:
        fail("the shared copy's build " + ("read /etc/passwd: TeX runs without openin_any=p" if "TeX read an absolute path" in texlog
                                           else "logged nothing from the probe in people.tex"))
    ok(f"shared mode builds in the hardened image (no latexmk, so a \\bibdata name runs nothing; no absolute paths), on port"
       f" {SHARED_PORT}, out of the hub,"
       " printing its public URL and no token")

    # The test's change to people.tex (LOCAL_CHANGE), uncommitted when the instance started: no one's writes account
    # for it, so it is committed then, as the owner's, rather than in the next coauthor's commit of that file.
    shas = wait_for("the commit of the edits found uncommitted at the start",
                    lambda: git_out(root, "rev-list", f"{sh['base']}..shared").split(), timeout=20, step=0.2)
    found = [(git_out(root, "show", "-s", "--format=%an <%ae>|%cn <%ce>|%s", sha).split("|"), commit_user(root, sha),
              git_out(root, "diff-tree", "--no-commit-id", "--name-only", "-r", sha).split()) for sha in shas]
    owner = "Owner Example <owner@example.org>"
    if (len(found) != 1 or found[0][0][:2] != [owner, owner] or "people.tex" not in found[0][0][2]
            or found[0][1:] != ("unattributed", ["people.tex"])):
        fail(f"the edit found uncommitted at the start is not one commit of people.tex, as the owner's (Latex-Live-Shared:"
             f" unattributed): {found}")
    sh["base"] = shas[0]  # (the users' commits come after it: commit_checks)
    with open(os.path.join(root, "refs.bib"), "a") as f:
        f.write(LATER_CHANGE)
    ok("the edit found uncommitted at the start is committed then, as the owner's, alone")

    second = subprocess.Popen([sys.executable, os.path.join(HERE, "live.py"), "appendix.tex", "--no-build", "--no-open", "--port",
                               str(SHARED_PORT)], cwd=root, env=sh["env"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                              start_new_session=True)
    try:
        out = second.communicate(timeout=30)[0]
    except subprocess.TimeoutExpired:
        stop(second)
        fail(f"a second shared-mode instance kept running though port {SHARED_PORT} is taken (shared mode uses no other port)")
    if not second.returncode or str(SHARED_PORT) not in out:
        fail(f"a second shared-mode instance on the taken port {SHARED_PORT} exited {second.returncode} without saying so:\n{out}")
    if f"{second.pid}.json" in recorded(sh["state"]):
        fail("the instance that found its port taken is still listed")
    ok("a shared-mode instance whose port is taken says so and exits")
    portless = subprocess.run([sys.executable, os.path.join(HERE, "live.py"), "appendix.tex", "--no-build", "--no-open"],
                              cwd=root, env=sh["env"], capture_output=True, text=True, timeout=60)
    if not portless.returncode or "--port" not in portless.stdout + portless.stderr:
        fail(f"a shared-mode instance without --port exited {portless.returncode} without asking for it:\n"
             f"{portless.stdout + portless.stderr}")
    ok("a shared-mode instance needs --port (it never takes the hub's)")
    again = subprocess.run([sys.executable, os.path.join(HERE, "live.py"), "main.tex", "--no-build", "--no-open", "--port",
                            str(SHARED_PORT)], cwd=root, env=sh["env"], capture_output=True, text=True, timeout=60)
    out = again.stdout + again.stderr
    if again.returncode or "already running" not in out or PUBLIC not in out or prints_token(out):
        fail(f"a second shared-mode instance for main.tex exited {again.returncode} without saying it is already running, with"
             f" the public URL and no token:\n{out}")
    ok("a second shared-mode instance for the same document says it is already running, with the public URL and no token")

    expect(send("GET", "/me", cookie, addr="::1"), 200, "the owner's cookie over IPv6 loopback ([::1])")
    address = outside_address()
    if not address:
        return print("skip: no network address here, so the loopback-only check is not made", flush=True)
    control = send("GET", "/docs", [("Host", f"localhost:{own}"), ("Cookie", f"latex_live={hub_token}")], port=own, addr=address,
                   timeout=5, quiet=True)
    answer = send("GET", "/me", [("Host", f"localhost:{SHARED_PORT}")] + cookie, addr=address, timeout=5, quiet=True)
    if answer is not None:
        fail(f"the shared-mode instance answered a peer at {address}, which is not loopback ({answer[0]})")
    if control is None:
        return print(f"skip: {address} does not reach this machine's own ports, so the loopback-only check proves little", flush=True)
    ok(f"the shared-mode instance answers over loopback (127.0.0.1, [::1]) but not from {address}")
