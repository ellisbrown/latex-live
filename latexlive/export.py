"""The download without notes (/export): an exact build of the document without its notes, made
once per build of the document, which the review copy builds on too."""

import os
import re
import shutil
import subprocess
import sys
import threading
import time

from . import config
from .build import build_running
from .files import aux_tree, job_file, mtime, pdf_path, read_output
from .latex import RERUN_RE
from .tools import image_pulled, run_tool, tex_argv


def waiting_for_build(server, pdf, state):
    """The state that /export and /review report while the document builds (a download made then would
    be of the build before, or of a pass before the last), or None when it doesn't, or when state (theirs)
    is a build of theirs that is running. Not with --no-build: no latexmk runs then, so a .building marker
    is one that a build stopped part way left behind."""
    if server.no_build or state.get("pdf") == pdf and state.get("state") == "running" or not build_running(server, pdf):
        return None
    return {"pdf": pdf, "state": "running", "step": "waiting for the build in progress", "time": time.strftime("%H:%M:%S"), "msg": ""}


def start_export(server):
    """/export: the document without its notes, built exactly (build_export), for downloading as
    build/<doc>-export.pdf. Built once per build of the document; until then, its state."""
    pdf = server.default_pdf
    if not os.path.isfile(pdf_path(server.paper_root, pdf)):
        return 409, {"state": "failed", "msg": f"{pdf} has not built yet"}
    with server.download_lock:  # (from however many viewers at once: one build)
        e = server.export
        if waiting := waiting_for_build(server, pdf, e):
            return 200, waiting
        source = str(mtime(pdf_path(server.paper_root, pdf)))  # (the build it reflects)
        if e.get("pdf") == pdf and (e.get("state") == "running" or e.get("source") == source):  # (a failure too, until the next build)
            return 200, e
        server.export = {"pdf": pdf, "source": source, "state": "running", "time": time.strftime("%H:%M:%S"), "msg": ""}
        threading.Thread(target=build_export, args=(server, pdf, source), daemon=True).start()
        return 200, server.export


def export_tools(run, root, out, job):
    """After the export's first pass (exact_export), the tools the paper uses, as the live build runs them
    (shared_run): bibtex for \\bibdata (in its .aux or a chapter's) or biber for a .bcf, and makeindex for
    an .idx. Whether one ran; RuntimeError if one failed, rather than an export missing its bibliography
    or index."""
    ran, where = False, os.path.relpath(out, root)
    if "\\bibdata" in aux_tree(read_output(root, os.path.join(out, f"{job}.aux")) or "", out):
        if not run(["bibtex", job], out, {"BIBINPUTS": f"{root}:", "BSTINPUTS": f"{root}:"}):
            raise RuntimeError(f"bibtex failed; see {where}/{job}.blg")
        ran = True
    elif os.path.exists(os.path.join(out, f"{job}.bcf")):
        if not run(["biber", f"--output-directory={out}", job], root):
            raise RuntimeError(f"biber failed; see {where}/{job}.blg")
        ran = True
    if os.path.exists(os.path.join(out, f"{job}.idx")):
        if not run(["makeindex", f"{job}.idx"], out):
            raise RuntimeError(f"makeindex failed; see {where}/{job}.ilg")
        ran = True
    return ran


def build_export(server, pdf, source):
    """The /export thread: the exact build without notes (exact_export), its state for the viewer."""
    try:
        exact_export(server, pdf)
        state = {"state": "done", "version": str(mtime(os.path.join(server.paper_root, config.BUILD, f"{pdf[:-4]}-export.pdf")))}
    except Exception as e:  # (shown in the viewer)
        state = {"state": "failed", "msg": failure(server, e)}
    with server.download_lock:  # (not over a newer export's state: start_export)
        if server.export.get("pdf") == pdf and server.export.get("source") == source:
            server.export = {**server.export, **state}


def failure(server, e):
    """What a download that failed says in the viewer (build_export, build_review), in 300 characters at most. In
    shared mode, where every coauthor reads it, without the server's folders (TeX names the paper's files relative
    to it, but Python's errors name them in full) or the command line of a TeX run that took too long."""
    if config.SHARED and isinstance(e, subprocess.TimeoutExpired):
        return f"a TeX run took more than {e.timeout / 60:g} minutes"
    text = str(e)
    for folder, name in ((os.path.realpath(server.paper_root), "."), (server.paper_root, "."), (config.HERE, "latex-live")) if config.SHARED else ():
        text = text.replace(folder + os.sep, "" if name == "." else name + os.sep).replace(folder, name)
    return text[:300]


def exact_export(server, pdf, keep=None):
    """build/<doc>-export.pdf: the document without notes, as it would be submitted. Unlike the
    quick notes-free pass (latex_report.notes_free_pass), which reuses the cross-references of the
    build with notes, this one runs bibtex for its own citations (a \\cite only in a note drops out
    of the bibliography) and pdflatex until the references settle, so page numbers in the contents,
    references and back-references are exact.

    Built in build/export/<doc>/ once per build of the document (the published PDF's mtime), one
    export at a time; with SyncTeX, and with the files its last pass read kept in inputs/, so that
    build_review can repeat that pass. keep(out), if given, runs before another export can replace
    this one (to copy what it needs). Raises RuntimeError when it fails, and at once when it failed
    for this build already (it would again: for the review copy after /export, say)."""
    root, doc = server.paper_root, pdf[:-4]
    out = os.path.join(root, config.BUILD, "export", doc)  # (per document: another instance may export another)
    job = f"{doc}-export"
    sys.path.insert(0, config.HERE)
    import latex_report

    def run(argv, cwd, env=None):
        code, _ = run_tool(tex_argv(server, *argv), cwd, kind="export", env={**os.environ, **(env or {})})
        if code is None:
            raise subprocess.TimeoutExpired(argv, 600)
        return code == 0

    def log():
        return read_output(root, os.path.join(out, f"{job}.log")) or ""

    with server.export_lock:
        source = str(mtime(pdf_path(root, pdf)))  # (the build it reflects)
        built, why = server.exported.get(pdf, (None, None))  # (the build last exported, and what failed then)
        if built == source and why:
            raise RuntimeError(why)
        if built != source or not os.path.isfile(os.path.join(root, config.BUILD, f"{job}.pdf")):
            if config.SHARED and not image_pulled():  # (said plainly, rather than as a pdflatex failure with no log)
                raise RuntimeError(f"{config.IMAGE_MISSING}; until then there is no download without notes (never the host's TeX here)")
            server.exported.pop(pdf, None)
            try:
                shutil.rmtree(out, ignore_errors=True)
                os.makedirs(out)
                for ext in (".aux", ".bbl", ".brf", ".out", ".toc"):  # (a head start; bibtex and the passes correct them)
                    if os.path.exists(job_file(root, pdf, ext)):
                        shutil.copyfile(job_file(root, pdf, ext), os.path.join(out, job + ext))
                tex = ["pdflatex", "-interaction=batchmode", "-halt-on-error", "-file-line-error", "-synctex=1",
                       f"-output-directory={out}", f"-jobname={job}", f"{latex_report.NOTES_OFF}\\input{{{doc}}}"]
                for passes in range(1, 7):
                    keep_inputs(out)
                    if not run(tex, root):
                        first = next((l for l in log().splitlines() if l.startswith("! ") or re.match(r"^\S+:\d+: ", l)), "")
                        raise RuntimeError(first or f"pdflatex failed; see {config.BUILD}/export/{doc}/{job}.log")
                    if passes == 1 and export_tools(run, root, out, job):  # (bibtex or biber, makeindex: then the passes again)
                        continue
                    settled = not RERUN_RE.search(log())
                    # (lineno places its numbers from the previous pass's .aux without asking for a rerun, so
                    # a first pass seeded with the build-with-notes .aux needs a second: latex_report.uses_lineno)
                    if settled and (passes >= 2 or not latex_report.uses_lineno(os.path.join(out, f"{job}.aux"))):
                        break
                else:  # (not exact, so not published)
                    raise RuntimeError(f"the references had not settled after {passes} passes; see {config.BUILD}/export/{doc}/{job}.log")
            except subprocess.TimeoutExpired:  # (remembered with the build, as every failure: it would fail again)
                server.exported[pdf] = (source, f"a pass took over 10 minutes; see {config.BUILD}/export/{doc}/{job}.log")
                raise RuntimeError(server.exported[pdf][1]) from None
            except RuntimeError as e:
                server.exported[pdf] = (source, str(e))
                raise
            os.replace(os.path.join(out, f"{job}.pdf"), os.path.join(root, config.BUILD, f"{job}.pdf"))
            server.exported[pdf] = (source, None)
            with server.download_lock:  # (built for the review copy: the download without notes needs no second build)
                e = server.export
                if not (e.get("pdf") == pdf and (e.get("state") == "running" or e.get("source") == source)):
                    server.export = {"pdf": pdf, "source": source, "state": "done", "time": time.strftime("%H:%M:%S"), "msg": "",
                                     "version": str(mtime(os.path.join(root, config.BUILD, f"{job}.pdf")))}
        if keep:
            keep(out)
    return out


AUX_SKIP = (".pdf", ".log", ".synctex.gz", ".blg", ".fls")  # (what a pass writes but doesn't read)


def keep_inputs(out):
    """Copy the files the next pass in out will read (.aux, .bbl, .toc..., an \\include's .aux) into
    out/inputs/."""
    inputs = os.path.join(out, "inputs")
    shutil.rmtree(inputs, ignore_errors=True)
    shutil.copytree(out, inputs, ignore=lambda folder, names: [n for n in names if n.endswith(AUX_SKIP)
                                                                or (folder == out and n == "inputs")] + links_in(folder, names))


def links_in(folder, names):
    """For shutil.copytree in shared mode, which copies what a link points to: the names in folder
    that are symbolic links, to leave out. TeX writes none, and a copy of TeX's output (which editors
    control) must bring in no file from elsewhere for the next pass to read, or the host to open."""
    return [n for n in names if config.SHARED and os.path.islink(os.path.join(folder, n))]
