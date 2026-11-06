"""Shared mode's builds (rebuild_stop_checks): a rebuild stops the run and its container, the status ignores files
forged in build/, a removed or failed input rebuilds, saves in a row make one build, build/'s size limit, no dot
entry in a container, a tool's deadline; and a forged lock file (lock_spoof_checks)."""

import glob
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time

from .base import DECOY_PORT, ENV, HERE, SHARED_PORT, fail, ok, prints_token, wait_for
from .helpers import mtime_ns, put, put_bytes, read_bytes, read_text, texlive_image
from .shared_helpers import expect, instance_containers, labelled_containers, settled, shared_status, with_token
from .shared_setup import BIBDATA_NAME


def rebuild_stop_checks(sh):
    """A rebuild stops the run in progress, its container too (a pass that slow.tex keeps busy for a minute; the
    shared copy's people.tex reads it while it exists), not only the podman client; and the build's state comes
    from the server, not from files in build/, where the paper's TeX writes (a status, a .building marker and a
    record of what was published, all forged)."""
    root, owner, pid = sh["root"], sh["token"], sh["proc"].pid
    slow = os.path.join(root, "slow.tex")
    put(slow, "\\loop\\ifnum\\pdfelapsedtime<3932160 \\repeat\n")  # (a minute of each pass)
    try:
        expect(with_token(owner, "/rebuild", "POST", {}), 202, "the owner rebuilding")
        wait_for("a container of the rebuild", lambda: instance_containers(pid, "main"), timeout=60, step=0.2)
        time.sleep(3)  # (a pass without slow.tex is over by now)
        busy = wait_for("a pass that slow.tex keeps busy", lambda: instance_containers(pid, "main"), timeout=30, step=0.2)
        sh["instance"] = next((f[1] for f in labelled_containers() if f[0] in busy), None)  # (its config.INSTANCE: shared_stop_checks)
        expect(with_token(owner, "/rebuild", "POST", {}), 202, "the owner rebuilding during that pass")
        wait_for("the busy pass's container to be gone", lambda: not busy & instance_containers(pid), timeout=20, step=0.2)
    finally:
        os.remove(slow)
    expect(with_token(owner, "/rebuild", "POST", {}), 202, "the owner rebuilding without slow.tex")
    wait_for("the shared copy's build to settle", lambda: settled(sh), timeout=120)
    ok("a rebuild stops the run in progress, and its container")

    build = os.path.join(root, "build")
    forged = {"main-status.status.json": json.dumps({"ok": False, "time": "00:00:00", "errors": [{"msg": "forged"}]}),
              "main-live.building": "", "main-live.published.json": json.dumps({"mtime": 1, "size": 1})}
    for name, text in forged.items():
        put(os.path.join(build, name), text)
    future = time.time_ns() + 3_600_000_000_000
    os.utime(os.path.join(build, "main-live.building"), ns=(future, future))
    info = shared_status(sh)
    for name in forged:
        os.remove(os.path.join(build, name))
    if (info.get("building") is not False or "forged" in json.dumps(info.get("build")) or not (info.get("build") or {}).get("ok")
            or "overwritten" in info):
        fail(f"the shared copy's status follows files forged in build/: building {info.get('building')}, build"
             f" {info.get('build')}, overwritten {info.get('overwritten')}")
    ok("the shared copy's status ignores a status, a .building marker and a record of what was published forged in build/")

    inc = os.path.join(root, "generated.inc")  # (a file the build reads that the editor doesn't offer)
    kept = read_bytes(inc)
    os.remove(inc)
    try:
        gone = wait_for("a build without generated.inc", lambda: (shared_status(sh).get("build") or {}).get("ok") is False
                        and shared_status(sh)["build"], timeout=60, step=0.5)
    finally:
        put_bytes(inc, kept)
    wait_for("the shared copy's build to settle", lambda: settled(sh) and (shared_status(sh).get("build") or {}).get("ok"),
             timeout=120)
    ok(f"removing a file the build reads rebuilds the shared copy (which then fails: {str(gone.get('errors'))[:60]}), and"
       " restoring it builds again")

    bib = os.path.join(root, BIBDATA_NAME + ".bib")
    kept = read_bytes(bib)
    put(bib, "@misc{probe, title = {Probe}\n")  # (no closing brace: bibtex fails)
    try:
        broken = wait_for("a build whose bibtex fails", lambda: "bibtex failed" in json.dumps((shared_status(sh).get("build") or {})
                                                                                           .get("errors")) and shared_status(sh)["build"],
                          timeout=60, step=0.5)
    finally:
        put_bytes(bib, kept)
    wait_for("the shared copy's build to settle", lambda: settled(sh) and (shared_status(sh).get("build") or {}).get("ok"),
             timeout=120)
    if broken.get("ok") is not False or "the end of its log" not in json.dumps(broken.get("errors")):
        fail(f"a build whose bibtex failed reports success, or not what bibtex said: {broken}")
    ok("a build whose bibtex fails reports it, with the end of its log, and builds again once the .bib is fixed")

    # A tool that failed runs again on the next run even when nothing it reads changed: here bibtex can't read the
    # .bib (no permission, which changes no mtime or size, so nothing is rebuilt for it), then can, and the next run,
    # for an unrelated change (main.tex touched), must run bibtex again rather than keep its failure.
    put(bib, read_text(bib) + "% changed\n")
    os.chmod(bib, 0)
    try:
        wait_for("a build whose bibtex can't read the .bib", lambda: "bibtex failed" in json.dumps(
            (shared_status(sh).get("build") or {}).get("errors")) and not shared_status(sh).get("building"), timeout=60, step=0.5)
    finally:
        os.chmod(bib, 0o644)
    main = os.path.join(root, "main.tex")
    os.utime(main)
    wait_for("a build after the .bib is readable again (main.tex touched)", lambda: (b := shared_status(sh).get("build") or {})
             .get("ok") and b.get("began", 0) >= mtime_ns(main) and settled(sh), timeout=120, step=0.5)
    put_bytes(bib, kept)
    wait_for("the shared copy's build to settle", lambda: settled(sh) and (shared_status(sh).get("build") or {}).get("ok"),
             timeout=120)
    ok("a failed bibtex runs again on the next run, though nothing it reads changed")

    with open(sh["log"], errors="replace") as f:
        runs = len(re.findall(r"=== live-reload: (?:published|BUILD FAILED)", f.read()))
    began = time.monotonic()
    while time.monotonic() - began < 3:  # (saves in a row: one build, once they stop)
        os.utime(main)
        time.sleep(0.25)
    wait_for("the shared copy's build to settle", lambda: settled(sh) and (shared_status(sh).get("build") or {}).get("ok"),
             timeout=120)
    with open(sh["log"], errors="replace") as f:
        made = len(re.findall(r"=== live-reload: (?:published|BUILD FAILED)", f.read())) - runs
    if not 1 <= made <= 2:
        fail(f"12 changes to main.tex within 3 s made {made} builds, not one (or two)")
    ok(f"changes in a row make one build once they stop ({made})")

    refer, later = os.path.join(root, "refer.tex"), os.path.join(root, "later.tex")
    put(refer, "\\input{later}\n")  # (a file that doesn't exist yet, as an edit that refers to a figure still to come)
    expect(with_token(owner, "/rebuild", "POST", {}), 202, "the owner rebuilding with refer.tex")
    wait_for("a build that misses later.tex", lambda: (b := shared_status(sh).get("build") or {}).get("ok") is False
             and b.get("began", 0) >= mtime_ns(refer) and not shared_status(sh).get("building"), timeout=60, step=0.5)
    put(later, "% It came later.\n")
    try:
        wait_for("a build once later.tex exists (no rebuild asked for)",
                 lambda: (b := shared_status(sh).get("build") or {}).get("ok") and b.get("began", 0) >= mtime_ns(later),
                 timeout=60, step=0.5)
    finally:
        os.remove(refer)
        os.remove(later)
    expect(with_token(owner, "/rebuild", "POST", {}), 202, "the owner rebuilding without refer.tex")
    wait_for("the shared copy's build to settle", lambda: settled(sh) and (shared_status(sh).get("build") or {}).get("ok"),
             timeout=120)
    ok("a file that a failed build missed builds the paper once it appears")

    put(os.path.join(root, "disk.tex"), "\\newcount\\diskn \\newwrite\\diskf \\def\\diska{xxxxxxxxxx}"
        "\\edef\\diskb{\\diska\\diska\\diska\\diska\\diska\\diska\\diska\\diska\\diska\\diska}"
        "\\edef\\diskc{\\diskb\\diskb\\diskb\\diskb\\diskb\\diskb\\diskb\\diskb\\diskb\\diskb}\n"
        "\\loop\\advance\\diskn by 1 \\immediate\\openout\\diskf=junk\\the\\diskn.txt\n"  # (1 MB in each of up to 500 files)
        "{\\count255=0 \\loop\\immediate\\write\\diskf{\\diskc}\\advance\\count255 by 1 \\ifnum\\count255<1000 \\repeat}"
        "\\immediate\\closeout\\diskf \\ifnum\\diskn<500 \\repeat\n")
    try:
        expect(with_token(owner, "/rebuild", "POST", {}), 202, "the owner rebuilding with disk.tex")
        full = wait_for("a build stopped for filling build/", lambda: "LATEX_LIVE_BUILD_LIMIT" in json.dumps(
            (shared_status(sh).get("build") or {}).get("errors")) and shared_status(sh)["build"], timeout=120, step=0.5)
        wait_for("build/ emptied and the run's container gone", lambda: not glob.glob(os.path.join(root, "build", "junk*.txt"))
                 and not instance_containers(pid, "main"), timeout=10, step=0.5)
    finally:
        os.remove(os.path.join(root, "disk.tex"))
    expect(with_token(owner, "/rebuild", "POST", {}), 202, "the owner rebuilding without disk.tex")
    wait_for("the shared copy's build to settle", lambda: settled(sh) and (shared_status(sh).get("build") or {}).get("ok"),
             timeout=120)
    ok(f"a build that fills build/ past LATEX_LIVE_BUILD_LIMIT is stopped, build/ emptied, and it says so"
       f" ({str(full.get('errors'))[:70]}); the next one builds")

    lock = os.path.join(root, "build", "main-live.lock")
    held = os.stat(lock).st_ino
    with open(os.path.join(root, "build", "flood.lock"), "wb") as f:  # (TeX could write it: only the instance's own lock stays)
        f.truncate(0)
        f.write(b"\0" * (70 << 20))
    wait_for("build/flood.lock removed", lambda: not os.path.exists(os.path.join(root, "build", "flood.lock")), timeout=30, step=0.5)
    if not os.path.exists(lock) or os.stat(lock).st_ino != held:
        fail("emptying build/ removed the instance's own lock file")
    asked = time.time_ns()
    expect(with_token(owner, "/rebuild", "POST", {}), 202, "the owner rebuilding after build/ was emptied")
    wait_for("the rebuild after build/ was emptied", lambda: (b := shared_status(sh).get("build") or {}).get("ok")
             and b.get("began", 0) >= asked and not shared_status(sh).get("building"), timeout=180, step=0.5)
    ok("a large file in build/ named like a lock file is removed with the rest when build/ is emptied; the instance's lock stays")

    marker = secrets.token_hex(8)
    put(os.path.join(root, ".git", "latex-live-check-marker"), marker)
    with open(os.path.join(root, ".git", "config"), "a") as f:  # (where a remote's credentials would be)
        f.write(f"[latexlivecheck]\n\tmarker = {marker}\n")
    nested = (os.path.join("sub", ".nested.bib"), os.path.join("sub", ".private", "refs.bib"))
    for name in nested:
        put(os.path.join(root, name), f"@misc{{{marker}}}\n")
    image = texlive_image()[0]
    try:
        seen = subprocess.run([os.path.join(HERE, "tex-exec"), image, root, "sh", "-c",
                               f"cat .git/config .git/latex-live-check-marker .private.bib {' '.join(nested)}; ls -a .git sub/.private"],
                              cwd=root, capture_output=True, text=True, timeout=120,
                              env={**ENV, "LATEX_LIVE_SANDBOX": "1", "LATEX_LIVE_REQUIRE_IMAGE": "1",
                                   "LATEX_LIVE_INSTANCE": f"{pid}-dotcheck"})
    finally:
        os.remove(os.path.join(root, nested[0]))
        shutil.rmtree(os.path.join(root, "sub", ".private"))
    # (a container that a build started meanwhile masks them too; podman's mask skips a path that is gone, rather
    # than making an empty file or folder in its place, as a mount over it would)
    wait_for("the instance's containers to finish", lambda: not instance_containers(pid), timeout=120, step=0.5)
    if os.path.lexists(os.path.join(root, nested[0])) or os.path.lexists(os.path.join(root, "sub", ".private")):
        fail("a dot entry deleted while a container hid it came back in the paper")
    if marker in seen.stdout + seen.stderr or "@misc{five}" in seen.stdout:
        fail("a shared-mode container can read the paper's .git (or another dot entry, at any depth): biber, which ignores"
             " openin_any, would too")
    ok("a shared-mode container sees no .git or other dot entry of the paper, at any depth (empty in their place)")

    from latexlive import config, tools
    saved = config.SHARED, config.INSTANCE
    config.SHARED, config.INSTANCE = True, f"{pid}-timeoutcheck"
    try:
        free = tools.SPAWNS._value
        code, _ = tools.run_tool([os.path.join(HERE, "tex-exec"), image, root, "sleep", "120"], root, timeout=8, kind="synctex",
                                env={**ENV, "LATEX_LIVE_SANDBOX": "1", "LATEX_LIVE_REQUIRE_IMAGE": "1",
                                     "LATEX_LIVE_INSTANCE": config.INSTANCE})
        left = subprocess.run(["podman", "ps", "-q", "--filter", f"label=latex-live.instance={config.INSTANCE}"], capture_output=True,
                              text=True, timeout=60).stdout.split()
    finally:
        config.SHARED, config.INSTANCE = saved
    if code == 0 or left or tools.SPAWNS._value != free:  # (None: stopped by run_tool; else by podman's own, equal, deadline)
        fail(f"a tool that ran out of time: exit {code}, {len(left)} container(s) left, {tools.SPAWNS._value} of {free} slots free")
    ok("a tool that runs out of time has its container stopped before its slot is free again")
    lock_spoof_checks(sh)


def lock_spoof_checks(sh):
    """The lock file (build/main-live.lock) names the running instance's port, but the paper's TeX can rewrite it:
    a second instance that finds the document locked asks that port whether it answers without the token (a decoy
    there records what it gets), and prints no token."""
    path, got = os.path.join(sh["root"], "build", "main-live.lock"), []
    with socket.socket(socket.AF_INET6) as decoy:
        decoy.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        decoy.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        try:
            decoy.bind(("::", DECOY_PORT))
        except OSError:
            return print(f"skip: port {DECOY_PORT} is taken, so the forged lock file is not checked", flush=True)
        decoy.listen(8)
        decoy.settimeout(0.5)

        def serve():
            while not done.is_set():
                try:
                    conn, _ = decoy.accept()
                except OSError:
                    continue
                with conn:
                    conn.settimeout(5)
                    try:
                        got.append(conn.recv(65536).decode(errors="replace"))
                        conn.sendall(b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n{}")
                    except OSError:
                        pass

        done, server = threading.Event(), threading.Thread(target=serve, daemon=True)
        server.start()
        with open(path, "r+") as f:  # (in place, as TeX's \\openout would: the running instance keeps its lock)
            real = f.read()
            f.seek(0)
            f.truncate()
            f.write(json.dumps({"port": DECOY_PORT, "pid": sh["proc"].pid}))
        try:
            again = subprocess.run([sys.executable, os.path.join(HERE, "live.py"), "main.tex", "--no-build", "--no-open", "--port",
                                    str(SHARED_PORT)], cwd=sh["root"], env=sh["env"], capture_output=True, text=True, timeout=60)
        finally:
            with open(path, "r+") as f:
                f.truncate()
                f.write(real)
            done.set()
            server.join(5)
    out = again.stdout + again.stderr
    if not got:
        fail(f"a second instance never asked the port in the forged lock file whether it answers (so this check proves nothing):\n{out}")
    if any(prints_token(text) or "latex_live=" in text for text in got):
        fail("a second instance sent a token to the port in a forged lock file")
    if prints_token(out):
        fail("a second instance that found a forged lock file printed a token")
    ok("a second instance asks the port in a forged lock file without its token, and prints none")
