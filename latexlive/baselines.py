"""Builds of the paper as committed: the baselines that the viewer's change highlights compare with
(HEAD and the last push), and the latexdiff against a commit."""

import contextlib
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time

from . import config
from .files import build_folder, copy_output, create_output, job_file, mtime, pdf_path, read_capped, too_big
from .git import POLL_TIMEOUT, SHA_RE, commit_sha, git_text, pushed_commit, ref_stamp, run_git
from .latex import first_error
from .tools import container_argv, failure, image_pulled, run_tool, tex_argv


def start_diff(server, rev, any_rev=True):
    """POST /diff {rev}: build <doc>-diff.pdf, a latexdiff of rev (a commit's hexadecimal id, or HEAD: never anything git
    would read as an option or a revision expression) against the working tree, in the background. Without any_rev (a
    coauthor), only a commit that HEAD builds on: other branches, tags and the stash may hold what isn't shared. (Its
    history is: the shared copy is a branch of the paper's, and coauthors compare with any commit it builds on.)"""
    if rev != "HEAD" and not (isinstance(rev, str) and SHA_RE.fullmatch(rev)):
        return 400, {"error": "bad rev (a commit's id, or HEAD)"}
    if server.diff.get("state") == "running":
        return 409, {"error": "a diff is already building"}
    root = server.paper_root
    sha = git_text(root, ["rev-parse", "--verify", "--quiet", "HEAD^{commit}"]) if rev == "HEAD" else commit_sha(root, rev)
    if not sha:
        return 404, {"error": f"unknown commit {rev}"}
    if not any_rev and not commit_sha(root, sha, any_rev=False):
        return 403, {"error": f"{rev} is not a commit this version of the paper builds on"}
    rev = rev if rev == "HEAD" else sha[:9]
    server.diff = {"rev": rev, "sha": sha[:9], "state": "running", "time": time.strftime("%H:%M:%S"), "msg": ""}
    threading.Thread(target=build_diff, args=(server, sha), daemon=True).start()
    return 200, server.diff


def tex_pathspec():
    """The git pathspec of the paper's .tex files that baselines and diffs copy into build/ (where the
    containers see them): all of them; in shared mode, none in a dot folder or named with a dot."""
    return [":(glob)**/*.tex", *([":(exclude,glob)**/.*", ":(exclude,glob)**/.*/**"] if config.SHARED else [])]


def extract_tex(root, sha, folder):
    """Put the paper's .tex files (tex_pathspec) as commit sha left them into folder (git archive, through a file in a
    temporary folder of the server's own, never in build/); RuntimeError if git or tar fails."""
    with tempfile.TemporaryDirectory(prefix="latex-live-archive-") as tmp:
        tar = os.path.join(tmp, "tex.tar")
        r = run_git(root, ["archive", "--format=tar", "-o", tar, sha, "--", *tex_pathspec()], timeout=60, literal=False)
        if r.code != 0:  # (the messages name the temporary folder as ., never where it is)
            why = (r.err.strip() or f"status {r.code}").splitlines()[-1].replace(tmp, ".")
            raise RuntimeError(f"{sha[:9]} has no .tex file here" if "did not match" in why else f"git archive failed: {why}")
        r = subprocess.run(["tar", "-x", "-f", "tex.tar", "-C", folder], cwd=tmp, capture_output=True, text=True, timeout=60)
        if r.returncode:
            why = (r.stderr.strip() or str(r.returncode)).splitlines()[-1].replace(tmp, ".")
            raise RuntimeError(f"tar could not unpack {sha[:9]}'s .tex files: {why}")


# latexdiff's marks, and the plainer ones a second try takes when the document with the first doesn't build (marks in
# math, around figures and in citations are the ones that break documents most often).
DIFF_MARKS = ["--math-markup=coarse"]
PLAINER_MARKS = ["--math-markup=off", "--graphics-markup=none", "--disable-citation-markup"]


def build_diff(server, sha):
    """latexdiff --flatten (TeX Live image) of <doc>.tex at sha vs now, then two pdflatex passes; if
    latexdiff fails, or the marked document makes no PDF or has errors, once more with plainer marks
    (PLAINER_MARKS), and the PDF with fewer errors is published (with the plainer marks, the diff's
    state says "plainer").

    Both sides are copies of just the .tex files (old: `git archive`; new: the working
    tree), so latexdiff doesn't inline a stale <doc>.bbl from the paper root; figures,
    styles, and the bibliography are the current ones. Files that \\input each other in a
    loop, on either side, stop it first (input_loop: latexdiff would flatten them forever).
    Runs in build/diff/; the PDF is published to build/<doc>-diff.pdf.
    """
    root = server.paper_root
    doc = server.default_pdf[:-4]
    out = os.path.join(root, config.BUILD, "diff")
    old, new = os.path.join(out, "old"), os.path.join(out, "new")
    job = f"{doc}-diff"

    def attempt(marks):
        """The diff with these marks, as out/<job>.pdf: how many errors its log has. Raises RuntimeError with the reason
        it made none, or TimeoutError."""
        code, text = run_tool([*exec_, "latexdiff", "--flatten", "--type=UNDERLINE", *marks,
                               "--config", "PICTUREENV=(?:picture|DIFnomarkup|tikzpicture|NiceTabular|tabular)[\\w\\d*@]*",
                               os.path.join(old, f"{doc}.tex"), os.path.join(new, f"{doc}.tex")], root, timeout=300, kind="diff",
                              capture=config.OUTPUT_LIMIT + 1)
        if code is None:
            raise TimeoutError("latexdiff took more than 5 minutes")
        if code != 0 or not text.strip():
            raise RuntimeError("latexdiff failed")
        if len(text.encode()) > config.OUTPUT_LIMIT:
            raise RuntimeError(too_big("latexdiff's output", config.OUTPUT_LIMIT))
        build_folder(root, "diff")  # (again after latexdiff's container: never a link now either)
        with create_output(os.path.join(out, f"{job}.tex")) as fh:
            fh.write(text.encode())
        for ext in (".aux", ".bbl", ".brf"):  # current references, so the diff needs no bibtex run
            copy_output(job_file(root, server.default_pdf, ext), os.path.join(out, job + ext))
        with contextlib.suppress(FileNotFoundError):  # (a link too: removed, not followed)
            os.remove(pdf)
        tex = [*exec_, "pdflatex", "-interaction=nonstopmode", f"-output-directory={out}", f"-jobname={job}",
               os.path.relpath(os.path.join(out, f"{job}.tex"), root)]  # (relative: shared mode's TeX opens no absolute path)
        for _ in range(2):
            if run_tool(tex, root, timeout=300, kind="diff", env={**os.environ, "max_print_line": "10000"})[0] is None:
                raise TimeoutError("a pdflatex pass took more than 5 minutes")
        log = read_capped(os.path.join(out, f"{job}.log")) or ""
        if not os.path.isfile(pdf):
            raise RuntimeError(first_error(log) or "pdflatex produced no PDF")
        return sum(line.startswith("! ") for line in log.splitlines())

    pdf, full = os.path.join(out, f"{job}.pdf"), os.path.join(out, f"{job}-full.pdf")
    try:
        for side in ("old", "new"):  # (never through a link in build/: build_folder)
            build_folder(root, os.path.join("diff", side), fresh=True)
        extract_tex(root, sha, old)
        tracked = run_git(root, ["ls-files", "-z", "-co", "--exclude-standard", "--", *tex_pathspec()], literal=False)
        if tracked.code != 0 or tracked.more:
            raise RuntimeError(f"git could not list the .tex files: {tracked.err.strip() or tracked.code}")
        for rel_path in filter(None, tracked.text.split("\0")):
            if os.path.isfile(os.path.join(root, rel_path)):
                os.makedirs(os.path.dirname(os.path.join(new, rel_path)), exist_ok=True)
                shutil.copyfile(os.path.join(root, rel_path), os.path.join(new, rel_path))
        for side, folder in (("the current version", new), ("that commit", old)):
            if loop := input_loop(folder, f"{doc}.tex"):
                raise RuntimeError(f"in {side}, {' → '.join(loop)} \\input each other, so latexdiff would never finish")
        pulled = image_pulled()
        if config.SHARED and not pulled:  # (shared mode never runs the host's TeX tools)
            raise RuntimeError(f"{config.IMAGE_MISSING}; meanwhile there is no diff")
        exec_ = container_argv(root) if pulled else []  # else the host's, if it has latexdiff
        with contextlib.suppress(FileNotFoundError):
            os.remove(full)
        try:
            errors = attempt(DIFF_MARKS)
        except RuntimeError:
            errors = None
        plainer = False
        if errors != 0:  # (no PDF, or one with errors: plainer marks, and the PDF with fewer errors)
            if errors is not None:
                os.replace(pdf, full)
            try:
                fewer = attempt(PLAINER_MARKS) < (float("inf") if errors is None else errors)
            except (RuntimeError, TimeoutError):  # (with a PDF from the full marks, that one)
                if errors is None:
                    raise
                fewer = False
            if fewer:
                plainer = True
            else:
                os.replace(full, pdf)
        os.replace(pdf, os.path.join(root, config.BUILD, f"{job}.pdf"))
        server.diff = {**server.diff, "state": "done", "time": time.strftime("%H:%M:%S"), "plainer": plainer}
    except Exception as e:  # reported in the diff viewer's banner
        server.diff = {**server.diff, "state": "failed", "time": time.strftime("%H:%M:%S"), "msg": failure(server, e)}


INCLUDE_RE = re.compile(r"\\(?:input|include|subfile)\s*\{([^{}]*)\}"
                        r"|\\(?:sub)?(?:import|inputfrom|includefrom)\s*\{([^{}]*)\}\s*\{([^{}]*)\}")


def input_loop(folder, doc):
    """Files in folder (from doc) that \\input each other in a loop, as latexdiff --flatten follows them: the loop
    (["a.tex", "b.tex", "a.tex"]), or None. latexdiff pastes each \\input, \\include, \\subfile and
    \\import into the file that names it, again and again, without checking for a file it is already inside. It looks
    names up from the folder of the file that began the flattening (the root, or the file an \\import or \\subfile
    opened), adds .tex to a name without a three- or four-letter extension, and leaves a missing file alone. (As
    lattice's changes/input_loop.rs finds them.)"""
    def name(base, written):
        written = written.strip()
        path = os.path.normpath(os.path.join(base, written if re.search(r"\.\w{3,4}$", written) else written + ".tex"))
        return path if path.startswith(os.path.join(folder, "")) and os.path.isfile(path) else None

    done = set()  # (files read to the end without a loop, each with the folder its names resolve in: no loop from them)

    def walk(path, base, chain):
        if (path, base) in chain:  # (a file again, resolving names in the same folder: latexdiff goes round forever)
            return [os.path.relpath(p, folder) for p, _ in chain[chain.index((path, base)):] + [(path, base)]]
        if (path, base) in done:
            return None
        text = re.sub(r"(?<!\\)%.*", "", read_capped(path) or "")
        for m in INCLUDE_RE.finditer(text):
            if m.group(1) is not None:  # (\input, \include, \subfile: from base; a \subfile's own from its folder)
                found = name(base, m.group(1))
                inner = os.path.dirname(found) if found and m.group(0).startswith("\\subfile") else base
            else:  # (\import{folder}{file} and the like: from that folder)
                inner = os.path.normpath(os.path.join(base, m.group(2).strip()))
                found = name(inner, m.group(3))
            if found and (loop := walk(found, inner, chain + [(path, base)])):
                return loop
        done.add((path, base))
        return None
    root = os.path.join(folder, doc)
    return walk(root, folder, []) if os.path.isfile(root) else None


def base_status(server, pdf_name, kind, build=True):
    """A baseline the viewer diffs against to mark changes: PDFs of the document as committed at
    HEAD ("head") or at the last push ("push", pushed_commit), rebuilt whenever that commit moves
    (build_base); with build=False, only the commit is reported (the push baseline is built only
    once a viewer shows it). When the last push is HEAD, the push baseline reuses HEAD's PDFs
    (share_head_base). The commit is checked every few seconds. The versions appear once a
    build has finished; None outside git (or with nothing pushed), and for a document the server
    is not building. git runs only when a ref may have moved (ref_stamp), or a minute has passed."""
    root = server.paper_root
    if pdf_name != server.default_pdf:
        return None
    lock = server.base_locks[kind]
    due = time.monotonic() - server.base_checked[kind] > 3
    if (due or build and server.bases[kind].get("state") == "waiting") and lock.acquire(blocking=False):
        try:
            b = server.bases[kind]
            stamp, known = (ref_stamp(server), server.base_stamps.get(kind)) if due else (None, None)
            if due:
                server.base_checked[kind] = time.monotonic()
                due = not known or known[0] != stamp or time.monotonic() - known[1] > 60
            if due:
                if kind == "head":
                    sha, ref = git_text(root, ["rev-parse", "--verify", "--quiet", "HEAD^{commit}"], timeout=POLL_TIMEOUT), None
                else:
                    sha, ref = pushed_commit(root)
                if not sha:
                    server.bases[kind] = b = {}
                elif (sha, pdf_name) != (b.get("sha"), b.get("pdf")) and b.get("state") != "running":
                    server.bases[kind] = b = {"sha": sha, "ref": ref, "pdf": pdf_name, "state": "waiting", "msg": ""}
                if not sha or (sha, pdf_name) == (b.get("sha"), b.get("pdf")):  # (not while a build of another commit runs)
                    server.base_stamps[kind] = (stamp, time.monotonic())
            sha = b.get("sha")
            if b.get("state") == "waiting" and build and os.path.isfile(job_file(root, pdf_name, ".aux")):
                head = server.bases["head"]
                # (in shared mode HEAD's baseline is built only when a viewer asks for it: then it isn't waited for)
                head_coming = not config.SHARED or head.get("built") == sha or head.get("state") in ("running", "failed")
                if kind == "push" and (head.get("sha"), head.get("pdf")) == (sha, pdf_name) and head_coming:
                    if head.get("built") == sha:  # (else wait for the HEAD build)
                        share_head_base(server, pdf_name)
                        b.update(state="done", built=sha)
                    elif head.get("state") == "failed":
                        b.update(state="failed", msg=head.get("msg", ""))
                else:
                    b["state"] = "running"
                    threading.Thread(target=build_base, args=(server, kind, sha, pdf_name), daemon=True).start()
        finally:
            lock.release()
    b = server.bases[kind]
    if b.get("pdf") != pdf_name:
        return None
    info = {"sha": b["sha"][:9], "ref": b["ref"], "state": b["state"], "msg": b.get("msg", "")}
    if b.get("built"):
        for key, name in (("version", f"-{kind}.pdf"), ("clean", f"-{kind}-clean.pdf")):
            path = pdf_path(root, pdf_name[:-4] + name)
            if os.path.isfile(path):
                info[key] = str(mtime(path))
    return info


def share_head_base(server, pdf_name):
    """The push baseline when nothing is unpushed: build/<doc>-push[-clean].pdf as hard links to the
    HEAD baseline's PDFs, which a rebuild of that baseline replaces rather than rewrites (or copies, copy_output), only
    of a regular file: never of a link there, nor to where it leads."""
    for suffix in ("", "-clean"):
        head, push = (pdf_path(server.paper_root, f"{pdf_name[:-4]}-{kind}{suffix}.pdf") for kind in ("head", "push"))
        try:
            os.remove(push)
        except FileNotFoundError:
            pass
        try:
            if not stat.S_ISREG(os.lstat(head).st_mode):  # (a link that TeX in a container left there is not linked to)
                continue
            os.link(head, push, follow_symlinks=False)
        except FileNotFoundError:
            pass
        except OSError:
            copy_output(head, push)


def link_missing(source, target, skip=()):
    """Link each entry of the folder source into target that target lacks (but those named in skip, and
    dot entries, at any depth: .git, .env, which the build never needs), and do the same inside each
    folder that both have: git archive made those in target for the .tex files in them, and the rest of
    their files (fonts, images) must resolve there too."""
    for name in os.listdir(source):
        here, there = os.path.join(source, name), os.path.join(target, name)
        if name in skip or name.startswith("."):
            continue
        if not os.path.lexists(there):
            os.symlink(here, there)
        elif os.path.isdir(here) and not os.path.islink(here) and os.path.isdir(there) and not os.path.islink(there):
            link_missing(here, there)


def build_base(server, kind, sha, pdf_name):
    """The document as committed at sha, for the kind of baseline (base_status): build/<doc>-<kind>.pdf,
    plus build/<doc>-<kind>-clean.pdf without notes (when the live build has a notes-free PDF).

    Like build_diff, only the .tex files come from git (`git archive` into build/<kind>/<doc>/src/).
    Figures, styles, fonts and the bibliography resolve to the working tree's (through TEXINPUTS,
    and links for everything else, such as a class's font directory: link_missing), so
    the diff shows text edits; the viewer marks changed figures itself. The live build's .aux/.bbl
    seed the references: two passes with notes, then one without (two with line numbers,
    latex_report.uses_lineno).
    """
    root = server.paper_root
    doc = pdf_name[:-4]
    out = os.path.join(root, config.BUILD, kind, doc)
    src = os.path.join(out, "src")
    job = f"{doc}-{kind}"
    import latex_report

    def tex(jobname, source):
        code, _ = run_tool(tex_argv(server, "pdflatex", "-interaction=batchmode", f"-output-directory={out}", f"-jobname={jobname}",
                                    source), src, timeout=300, kind=f"base-{kind}", env={**os.environ, "TEXINPUTS": f"{src}:{root}:"})
        if code is None:
            raise RuntimeError("a pass took more than 5 minutes")
        return os.path.join(out, f"{jobname}.pdf")

    try:
        build_folder(root, os.path.join(kind, doc), fresh=True)  # (never through a link in build/)
        os.makedirs(src)
        if config.SHARED and not image_pulled():
            raise RuntimeError(f"{config.IMAGE_MISSING}; meanwhile there is no baseline")
        extract_tex(root, sha, src)
        if not os.path.isfile(os.path.join(src, f"{doc}.tex")):
            raise RuntimeError(f"{doc}.tex is not in {sha[:9]}")
        link_missing(root, src, skip=(".git", config.BUILD.split(os.sep)[0]))  # (fonts are not looked up through TEXINPUTS)
        for ext in (".aux", ".bbl", ".brf"):  # (.brf: hyperref's back-references)
            copy_output(job_file(root, pdf_name, ext), os.path.join(out, job + ext))
        for _ in range(2):
            pdf = tex(job, doc)
        if not os.path.isfile(pdf):
            raise RuntimeError(f"pdflatex produced no PDF; see {config.BUILD}/{kind}/{doc}/{job}.log")
        clean_pdf = None
        if os.path.isfile(pdf_path(root, f"{doc}-clean.pdf")):
            for ext in (".aux", ".bbl", ".brf", ".out", ".toc"):
                copy_output(os.path.join(out, job + ext), os.path.join(out, f"{job}-clean{ext}"))
            clean_pdf = tex(f"{job}-clean", f"{latex_report.NOTES_OFF}\\input{{{doc}}}")
            if latex_report.uses_lineno(os.path.join(out, f"{job}-clean.aux")):
                clean_pdf = tex(f"{job}-clean", f"{latex_report.NOTES_OFF}\\input{{{doc}}}")
        os.replace(pdf, os.path.join(root, config.BUILD, f"{job}.pdf"))
        if clean_pdf and os.path.isfile(clean_pdf):
            os.replace(clean_pdf, os.path.join(root, config.BUILD, f"{job}-clean.pdf"))
        state = {"state": "done", "built": sha}
    except Exception as e:  # reported in the viewer's tooltip; the previous baseline stays
        state = {"state": "failed", "msg": failure(server, e)}
    if server.bases[kind].get("pdf") == pdf_name:  # (not after a switch to another document)
        server.bases[kind] = {**server.bases[kind], **state}


def baseline_request(server, kind):
    """POST /baseline {kind: "head" or "push"} in shared mode: build that baseline for the change
    highlights, once per commit (base_status), where status, a GET, only reports it."""
    if kind not in ("head", "push"):
        return 400, {"error": "kind is head or push"}
    return 200, base_status(server, server.default_pdf, kind) or {}
