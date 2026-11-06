"""Shared mode's builder, without latexmk.

latexmk is Perl that runs on the host, and it parses the files that the paper's TeX writes (.aux, .bcf,
.log): it hands \\bibdata names from the .aux to a shell command (its kpsewhich), so coauthors who write
the paper could run commands on this machine through it. Instead, latex-live watches the paper's files
itself and runs pdflatex, bibtex, biber and makeindex as argument lists, each in a container of its own
(tex-exec, confined, with the paper read-only and only build/ writable), and it treats what they write
in build/ as written by someone else: the run's state, its status and what it published stay in memory
(server.shared)."""

import glob
import hashlib
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback

from . import config
from .files import aux_tree, fls_file, pdf_path, project_tex_files, read_build, read_capped
from .latex import RERUN_RE
from .shared_watch import shared_deps, shared_inputs, shared_missing
from .timing import note
from .tools import LIVE_SPAWN, RUN_SEQ, SPAWNS, container_argv, image_pulled, stop_containers


# The same layout line as latexmkrc's $pre_tex_code (check.py checks that they agree).
LAYOUT_TEX = (r"\def\latexlivelayout{\typeout{latex-live layout:"
              r" \the\paperwidth,\the\paperheight,\the\hoffset,\the\oddsidemargin,\the\voffset,\the\topmargin,"
              r"\the\headheight,\the\headsep,\the\textwidth,\the\textheight,\the\columnwidth,\the\baselineskip}}"
              r"\ifdefined\AddToHook\AddToHook{begindocument/end}{\latexlivelayout}\else\AtBeginDocument{\latexlivelayout}\fi")


BIBDATA_RE = re.compile(r"^\\(?:citation|bibdata|bibstyle)\{.*\}$", re.M)


def start_shared_build(server):
    """Shared mode: build the document now, and again whenever its files change (shared_build)."""
    os.environ["LATEX_LIVE_DOC"] = server.default_pdf[:-4]  # (latex_report.run_report names the main file after it)
    threading.Thread(target=shared_build, args=(server,), daemon=True).start()


QUIET, MIN_GAP, MOST_WAIT = 1.0, 2.0, 10.0  # (s: shared_build's debounce, and the least time between two runs' starts)


def shared_build(server):
    """Shared mode's builder, a thread of its own: a run (shared_run) at the start, then whenever one of
    the files it depends on changes, goes or appears (shared_inputs, looked at twice a second; a change
    during a run makes another), or POST /rebuild asks for one (shared_rebuild_request), or the TeX Live
    image appears after a run found it missing. Never more than one run at a time, and for everyone
    alike: a change starts one once the files have been quiet for QUIET s (at most MOST_WAIT s after the
    first change), and no run starts within MIN_GAP s of the last one's start, so that saves in a row
    make one build, and no one can keep the machine building."""
    built, last_start, image_checked = None, -math.inf, time.monotonic()
    while not server.stopping:
        asked = server.shared["wanted"].wait(0.5)
        if not asked and server.shared["no_image"] and time.monotonic() - image_checked > 5:
            image_checked = time.monotonic()
            asked = image_pulled()
        now = shared_inputs(server)
        if not asked and now == built:  # (a file gone, or new, counts as a change too)
            continue
        if not asked:  # (a change: wait until the files are quiet)
            first = time.monotonic()
            while not server.stopping and time.monotonic() - first < MOST_WAIT:
                time.sleep(QUIET)
                again = shared_inputs(server)
                if again == now or server.shared["wanted"].is_set():
                    break
                now = again
        if (wait := last_start + MIN_GAP - time.monotonic()) > 0:
            time.sleep(wait)
        if server.stopping:
            break
        now = shared_inputs(server)
        server.shared["wanted"].clear()
        server.shared["force"] = False
        last_start = time.monotonic()
        newest = max(((st[0], path) for path, st in now.items() if st), default=None)  # (the save, for the run's timing)
        if newest:
            note(server, "saved", os.path.relpath(newest[1], server.paper_root), when=newest[0] / 1e9)
        try:
            shared_run(server)
        except Exception:  # (said, and the next change tries again)
            print(f"=== latex-live: the build failed:\n{traceback.format_exc().rstrip()}", flush=True)
            server.shared.update(building=False, status={"ok": False, "time": time.strftime("%H:%M:%S"),
                                                         "errors": [{"file": None, "line": None, "msg": "the build failed (see the server's log)"}]})
        # The files the run depends on, as they were when it started (files it read for the first time: as they are now).
        built = {path: now.get(path, stat) for path, stat in shared_inputs(server).items()}


def shared_tool(server, argv, kind="main", cwd=None, env=None):
    """Run a TeX tool (argv) in its own container (tex-exec, from the paper root or cwd), for up to 10 minutes,
    in the live build's slot (LIVE_SPAWN; another kind of run's: a SPAWNS slot); whether it succeeded. The
    container is labelled with a run of its own (e.g. main-12) and its kind, so that a rebuild stops the
    main run's (stop_containers); when the tool fails or runs out of time, its container is stopped before
    the slot is free again."""
    label = f"{kind}-{next(RUN_SEQ)}"
    with LIVE_SPAWN if kind == "main" else SPAWNS:
        proc = subprocess.Popen([*container_argv(server.paper_root), *argv], cwd=cwd or server.paper_root,
                                env={**server.env, **(env or {}), "LATEX_LIVE_RUN": label, "LATEX_LIVE_TIMEOUT": "600"},
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if kind == "main":
            server.shared["proc"] = proc
        try:
            proc.wait(timeout=600)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        finally:
            if proc.returncode != 0:
                stop_containers(label)
            if kind == "main":
                server.shared["proc"] = None
        return proc.returncode == 0


def dep_stats(paths):
    """The stat (mtime, size) of each of paths that exists, to tell whether a tool needs to run again."""
    stats = []
    for path in paths:
        try:
            st = os.stat(path)
            stats.append((path, st.st_mtime_ns, st.st_size))
        except OSError:
            stats.append((path, None))
    return stats


def bib_state(server, aux):
    """What a bibtex run depends on: the citation, bibliography and style lines of the .aux files (aux:
    aux_tree's), and the .bib and .bst files they name in the paper (shared_deps), to tell whether bibtex
    needs to run again."""
    return hashlib.sha256(repr((BIBDATA_RE.findall(aux), dep_stats(shared_deps(server, aux, "")))).encode()).hexdigest()


def biber_state(server, bcf):
    """What a biber run depends on: the control file (.bcf: the citations, the options) and the data sources
    it names in the paper (shared_deps), to tell whether biber needs to run again."""
    return hashlib.sha256(repr((bcf, dep_stats(shared_deps(server, "", bcf)))).encode()).hexdigest()


def tool_failure(name, path):
    """What a failed bibtex, biber or makeindex says in the build's status: its name and the last lines of
    its log (path: .blg or .ilg), which coauthors can't open otherwise (at most 10 lines, 2000 characters)."""
    tail = "\n".join((read_capped(path, 1 << 20) or "").rstrip().splitlines()[-10:])[-2000:]
    return f"{name} failed" + (f"; the end of its log:\n{tail}" if tail else " (it wrote no log)")


def shared_run(server):
    """One build of the shared document (shared_build), as latexmk would make it: pdflatex in passes, with
    bibtex, biber or makeindex between them where the paper uses them, until the references settle (at
    most six passes). Each successful pass publishes the PDF (shared_publish) and keeps its list of files
    (build/<job>.fls.done); the run's status, from the log (latex_report.run_report, with the paper's own
    files only, and what bibtex, biber or makeindex said when they failed), stays in memory; and a
    successful run starts the notes-free pass (shared_notes_free). A rebuild stops a run part way (its
    containers: stop_containers) and starts it again; a tool's state is kept only when it succeeded, and
    only if no rebuild (server.shared["gen"]) came meanwhile, so that it runs again until it does."""
    sys.path.insert(0, config.HERE)
    import latex_report
    root, pdf = server.paper_root, server.default_pdf
    doc, job = pdf[:-4], f"{pdf[:-4]}-live"
    out = lambda ext: os.path.join(config.BUILD, job + ext)  # (relative to the paper root, as the tools see it)
    server.shared["building"] = True
    note(server, "run")
    began, gen = time.time_ns(), server.shared["gen"]  # (in its status: the run reflects the files as they were then)
    failed = {}  # (bibtex, biber or makeindex: why it failed, in this run)
    try:
        stop_containers(kind="main")  # (one that a rebuild's stop missed: its client had not started it yet)
        server.shared["no_image"] = not image_pulled()
        if server.shared["no_image"]:
            raise RuntimeError(f"{config.IMAGE_MISSING}; until then nothing builds (never the host's TeX here)")
        tex = ["pdflatex", "-recorder", "-synctex=1", "-file-line-error", "-halt-on-error", "-interaction=batchmode",
               f"-output-directory={config.BUILD}", f"-jobname={job}", f"{LAYOUT_TEX}\\input{{{doc}}}"]
        ok = False

        def tool(name, state_key, state, argv, log_ext, **where):
            note(server, name)
            done = shared_tool(server, argv, kind=name, **where)
            failed[name] = None if done else tool_failure(name, out(log_ext))
            if done and server.shared["gen"] == gen:  # (failed: it runs again on the next pass and the next run)
                server.shared[state_key] = state
            return done

        for passes in range(1, 7):
            before = read_build(out(".aux"))
            note(server, "pass")
            ok = shared_tool(server, tex)
            note(server, "pass-end")
            if server.shared["force"]:  # (a rebuild stopped it: shared_build starts again)
                return
            if not ok:
                break
            latex_report.keep_inputs(job)
            shared_publish(server, job)
            note(server, "published")
            aux, log, bcf = read_build(out(".aux")), read_build(out(".log")), read_build(out(".bcf"))
            auxes, ran = aux_tree(aux), False
            server.shared["deps"] = shared_deps(server, auxes, bcf)
            if "\\bibdata" in auxes:
                if (state := bib_state(server, auxes)) != server.shared["bib"]:
                    paths = f"{os.path.realpath(root)}{os.pathsep}"  # (bibtex runs in build/: the paper's .bib and .bst files)
                    ran = tool("bibtex", "bib", state, ["bibtex", job], ".blg", cwd=os.path.join(root, config.BUILD),
                               env={"BIBINPUTS": paths, "BSTINPUTS": paths})
            elif bcf:
                if (state := biber_state(server, bcf)) != server.shared["biber"] or re.search(r"Please \(?re\)?run Biber", log):
                    ran = tool("biber", "biber", state, ["biber", f"--output-directory={config.BUILD}", job], ".blg")
            idx = read_build(out(".idx"))
            if idx and (state := hashlib.sha256(idx.encode()).hexdigest()) != server.shared["idx"]:
                ran = tool("makeindex", "idx", state, ["makeindex", "-o", out(".ind"), out(".idx")], ".ilg") or ran
            if server.shared["force"]:
                return
            if not ran and not RERUN_RE.search(log) and aux == before:
                break
        if server.shared["overflow"] > began:  # (watch_build_size stopped it: tried again a few times, further apart)
            server.shared["overflows"] += 1
            if server.shared["overflows"] <= 3:
                retry = threading.Timer(5 * 2 ** server.shared["overflows"], server.shared["wanted"].set)
                retry.daemon = True
                retry.start()
            raise RuntimeError(f"the build wrote more than {config.BUILD_LIMIT >> 20} MB into {config.BUILD}/ (LATEX_LIVE_BUILD_LIMIT), so it"
                               f" was stopped and {config.BUILD}/ emptied")
        files = [os.path.relpath(p, root) for p in project_tex_files(root, fls_file(root, pdf))]
        ok, status, log, _ = latex_report.run_report(job, "ok" if ok else "fail", files)
        missing = shared_missing(server, log) + server.shared["missing"]  # (and those an earlier run missed, until they appear)
        server.shared["missing"] = [path for path in dict.fromkeys(missing) if not os.path.exists(path)][:400]
        if tools := [msg for msg in failed.values() if msg]:  # (a stale bibliography or index, with what the tool said)
            ok, status = False, {**status, "ok": False,
                                 "errors": [*status.get("errors", []), *({"file": None, "line": None, "msg": msg} for msg in tools)]}
        if ok:
            server.shared["overflows"] = 0
        if not ok and latex_report.damaged_aux(log, job) and time.monotonic() - server.shared["retried"] > 60 \
                and server.shared["gen"] == gen:
            server.shared["retried"] = time.monotonic()  # (a run stopped part way cut off an .aux: once more without them)
            for ext in latex_report.AUX_EXTS:
                try:
                    os.remove(out(ext))
                except OSError:
                    pass
            server.shared["wanted"].set()
        server.shared["status"] = {**status, "began": began}
        note(server, "status")
        stamp = status["time"]
        print(f"=== live-reload: published {pdf} at {stamp}" if ok else f"=== live-reload: BUILD FAILED at {stamp}; kept the previous {pdf}",
              flush=True)
        if ok and latex_report.has_notes(files):
            shared_notes_free(server, job, doc)
    except RuntimeError as e:
        server.shared["status"] = {"ok": False, "time": time.strftime("%H:%M:%S"), "began": began,
                                   "errors": [{"file": None, "line": None, "msg": str(e)}]}
    finally:
        server.shared["building"] = False
        note(server, "run-end")


def shared_publish(server, job):
    """Publish build/<job>.pdf as the shared document's PDF, in one step, and remember what was published
    (status's "overwritten" compares the PDF with it). Never through a symbolic link: build/ is written in
    the containers."""
    dest = pdf_path(server.paper_root, server.default_pdf)
    try:
        fd = os.open(os.path.join(server.paper_root, config.BUILD, f"{job}.pdf"), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return
    tmp = os.path.join(os.path.dirname(dest), f".{os.path.basename(dest)}.tmp")
    with open(fd, "rb") as src, open(tmp, "wb") as f:
        shutil.copyfileobj(src, f)
    os.replace(tmp, dest)
    st = os.stat(dest)
    server.shared["published"] = {"mtime": st.st_mtime_ns, "size": st.st_size}


def shared_notes_free(server, job, doc):
    """The notes-free pass after a successful run (latex_report.notes_free_pass), in a thread, in a SPAWNS
    slot and a container labelled "clean", in a folder of its own (build/clean-<n>/): a newer run's pass
    stops an older one's, and only the newest publishes, under clean_lock (its PDF to build/, its SyncTeX
    and .aux to build/clean/, where the viewer's lookups read them)."""
    sys.path.insert(0, config.HERE)
    import latex_report
    stop_containers(kind="clean")
    with server.shared["clean_lock"]:
        server.shared["clean_seq"] += 1
        seq = server.shared["clean_seq"]
    note(server, "clean-start", str(seq))  # (the pass's events name it: timing.NAMING)
    out = os.path.join(config.BUILD, f"clean-{seq}")
    for old in glob.glob(os.path.join(glob.escape(config.BUILD), "clean-*")):
        shutil.rmtree(old, ignore_errors=True)
    latex_report.seed_notes_free(job, doc, out)
    clean = os.path.join(config.BUILD, "clean")

    def publish(pdf):
        with server.shared["clean_lock"]:
            if seq != server.shared["clean_seq"]:  # (a newer run's pass came meanwhile)
                return False
            os.makedirs(clean, exist_ok=True)
            for ext in (".synctex.gz", ".aux", ".bbl", ".log"):
                if os.path.lexists(src := os.path.join(out, f"{doc}-clean{ext}")):
                    os.replace(src, os.path.join(clean, f"{doc}-clean{ext}"))
            os.replace(pdf, os.path.join(config.BUILD, f"{doc}-clean.pdf"))
            return True

    def run():
        with SPAWNS:  # (one of the slots that downloads share: the live build has its own)
            if seq != server.shared["clean_seq"]:
                return
            try:
                status = latex_report.notes_free_pass(job, doc, env={**server.env, "LATEX_LIVE_RUN": f"clean-{seq}"}, out=out,
                                                      publish=publish)
            except OSError as e:  # (its folder emptied under it: a newer pass's, or watch_build_size)
                status = {"ok": False, "time": time.strftime("%H:%M:%S"),
                          "errors": [{"file": None, "line": None, "msg": f"its files went missing ({e.strerror})"}]}
            if status.get("timeout"):
                stop_containers(f"clean-{seq}")
        with server.shared["clean_lock"]:
            if seq == server.shared["clean_seq"] and not status.get("superseded"):
                server.shared["clean"] = status
                note(server, "clean-published" if status.get("ok") else "clean-failed", str(seq))
        shutil.rmtree(out, ignore_errors=True)

    threading.Thread(target=run, daemon=True).start()


def shared_rebuild_request(server, user):
    """POST /rebuild in shared mode: stop the run in progress (its containers too) and build again, with
    bibtex, biber and makeindex too (a new generation: what a run in progress learns about them is not
    kept). At most once every 20 s for the coauthors together (429; the owner, any time; for everyone,
    shared_build starts runs at least MIN_GAP s apart), so that they can't keep the machine busy."""
    with server.shared["admit"]:  # (two requests at once: one rebuild, and the other is refused)
        if user["role"] != "owner":
            if time.monotonic() - server.shared["rebuilt"] < 20:
                return 429, {"error": "it was just rebuilt; try again in a few seconds"}
            server.shared["rebuilt"] = time.monotonic()
        server.shared["gen"] += 1
        server.shared.update(force=True, bib=None, biber=None, idx=None)
        if proc := server.shared["proc"]:
            proc.terminate()
        stop_containers(kind="main")
        server.shared["wanted"].set()
    print(f"=== latex-live: rebuilding {server.default_pdf[:-4]}.tex (asked by {user['name'] or user['id']})", flush=True)
    return 202, {"doc": server.default_pdf[:-4]}
