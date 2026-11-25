"""The unit checks of shared mode's parts, without an instance: the downloads' guards (confinement_checks), the
host's reads and copies of the build's files, which follow no link (build_link_checks), nor do its writes in the
build's folders (build_folder_checks), the builder (shared_build_checks), what the host reads of the files the paper's
TeX writes (shared_parsing_checks), and which notes viewers and commenters see (shown_notes_checks)."""

import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from .base import HERE, fail, ok
from .helpers import fake_server, git, git_out, patched, put, read_text, tex_stand_in


def confinement_checks():
    """Shared mode's guards around the downloads built on request (live.py), without TeX: with the TeX Live image
    missing, the export and the review copy fail with a message that says so (never the host's TeX); the review
    copy's notes come from the last good build's list of files (.fls.done, not the .fls a build is rewriting), and
    only from the paper's own files (not a link to a file elsewhere, nor a dot file or a file in a dot folder, which
    TeX doesn't read there, though that list names them: the paper's TeX can write it); the inputs kept for its
    probe leave out symbolic links; a failure names no folder of the server's (the paper's, latex-live's), nor the
    command line of a TeX run that took too long. Outside shared mode, links count as before, and so do failures."""
    from latexlive import config, export, review
    with tempfile.TemporaryDirectory(prefix="latex-live-check-") as tmp, patched(config, SHARED=True), \
            patched(export, image_pulled=lambda: False), patched(review, image_pulled=lambda: False):
        root = os.path.join(tmp, "paper")
        build = os.path.join(root, config.BUILD)
        os.makedirs(build)
        server = fake_server(root, tex_exec="false")
        try:
            export.exact_export(server, "main.pdf")
            fail("with the TeX Live image missing, the export in shared mode went ahead")
        except RuntimeError as e:
            if config.IMAGE_MISSING not in str(e):
                fail(f"with the TeX Live image missing, the export in shared mode says: {e}")
        server.review = {"pdf": "main.pdf", "source": "1", "state": "running"}
        review.build_review(server, "main.pdf", "1")
        if server.review.get("state") != "failed" or config.IMAGE_MISSING not in server.review.get("msg", ""):
            fail(f"with the TeX Live image missing, the review copy in shared mode: {server.review}")

        outside = os.path.join(tmp, "outside.tex")
        os.makedirs(os.path.join(root, ".hidden"))
        for path, text in ((os.path.join(root, "main.tex"), "\\newcommand{\\eb}[1]{\\todotxt{[EB: #1]}}\n\\eb{in main}\n"),
                           (os.path.join(root, "late.tex"), "\\eb{read late in the build}\n"),
                           (os.path.join(root, ".dot.tex"), "\\eb{in a dot file}\n"),
                           (os.path.join(root, ".hidden", "inside.tex"), "\\eb{in a dot folder}\n"),
                           (outside, "\\eb{in a file outside the paper}\n")):
            with open(path, "w") as f:
                f.write(text)
        os.symlink(outside, os.path.join(root, "away.tex"))
        fls = os.path.join(build, "main-live.fls")
        with open(fls, "w") as f:  # (as pdflatex rewrites it during a pass: only the files read so far)
            f.write(f"PWD {root}\nINPUT main.tex\n")
        with open(fls + ".done", "w") as f:  # (the last good build's)
            f.write(f"PWD {root}\nINPUT main.tex\nINPUT late.tex\nINPUT away.tex\nINPUT {outside}\nINPUT ./.dot.tex\n"
                    "INPUT ./.hidden/inside.tex\n")
        found = sorted(n["body"] for n in review.review_notes(root, "main.pdf"))
        if found != ["in main", "read late in the build"]:
            fail(f"the review copy's notes in shared mode are not the last good build's, in the paper's own files: {found}")

        out = os.path.join(tmp, "export")
        os.makedirs(out)
        with open(os.path.join(out, "main-export.aux"), "w") as f:
            f.write("\\relax\n")
        os.symlink(outside, os.path.join(out, "main-export.bbl"))
        export.keep_inputs(out)
        if sorted(os.listdir(os.path.join(out, "inputs"))) != ["main-export.aux"]:
            fail(f"the inputs kept for the probe in shared mode: {sorted(os.listdir(os.path.join(out, 'inputs')))} (no links wanted)")

        export.image_pulled = review.image_pulled = lambda: True  # (a stand-in for TeX runs instead)
        stand_in = os.path.join(tmp, "tex")
        os.makedirs(stand_in)
        put(os.path.join(stand_in, "log"), "This is pdfTeX\n")  # (and no SyncTeX, as with \synctex=0 in the export's job)
        put(os.path.join(stand_in, "pdf"), "%PDF-1.5\n")
        put(os.path.join(root, "main.pdf"), "%PDF-1.5\n")
        server.tex_exec, server.review = tex_stand_in(stand_in), {"pdf": "main.pdf", "source": "2", "state": "running"}
        review.build_review(server, "main.pdf", "2")
        msg = server.review.get("msg", "")
        if (server.review.get("state") != "failed" or "build/export/main/main-export.synctex.gz" not in msg or root in msg
                or config.HERE in msg):
            fail(f"a review copy that failed for a missing file, in shared mode, says: {server.review}")
        timeout = subprocess.TimeoutExpired(["sh", "-c", f"'{config.HERE}/tex-exec' image '{root}' pdflatex"], 600)
        if export.failure(server, timeout) != "a TeX run took more than 10 minutes":
            fail(f"a TeX run that took too long, in shared mode, says: {export.failure(server, timeout)}")
        config.SHARED = False
        if root not in export.failure(server, OSError(2, "No such file or directory", os.path.join(build, "x"))):
            fail("outside shared mode, a download's failure no longer names its file in full")
        export.keep_inputs(out)
        found = [n["body"] for n in review.review_notes(root, "main.pdf")]
        if (sorted(os.listdir(os.path.join(out, "inputs"))) != ["main-export.aux", "main-export.bbl"]
                or "in a file outside the paper" not in found):
            fail("outside shared mode, the review copy no longer follows links as it did")
    ok("in shared mode, the downloads built on request say plainly that the TeX Live image is missing, the review copy"
       " reads the last good build's own files (no dot files) and keeps no links for its probe, and failures name no"
       " folder of the server's")


def build_link_checks():
    """In shared mode, where TeX writes build/ in containers, the host follows no link there when it reads or copies a
    file of the build's (a link to a file of the owner's elsewhere would put that file in a download, or before TeX in
    the next run): the downloads' reads (read_output), the export's head start, the diff's and the baselines' copies of
    the live build's .aux files, the copies the review copy and the arXiv package make (copy_output: never written
    through a link either), the recorder file kept after a pass, the notes-free pass's seed and the build's log
    (latex_report.py, which never follows one)."""
    from latexlive import baselines, config, export, files
    sys.path.insert(0, HERE)
    import latex_report
    latexdiff = lambda argv, cwd, **kwargs: (0, "\\documentclass{article}\\begin{document}y\\end{document}\n" if "latexdiff" in argv else "")
    cwd = os.getcwd()
    with tempfile.TemporaryDirectory(prefix="latex-live-check-") as tmp, patched(config, SHARED=True), \
            patched(export, image_pulled=lambda: True, run_tool=lambda *args, **kwargs: (1, "")), \
            patched(baselines, image_pulled=lambda: True, run_tool=latexdiff):  # (export: a TeX run that fails, after the head start)
        try:
            secret = os.path.join(tmp, "secret.txt")
            put(secret, "THE OWNER'S SECRET\n! an error line from it\n")
            root = os.path.join(tmp, "paper")
            build = os.path.join(root, config.BUILD)
            os.makedirs(build)
            put(os.path.join(root, "main.tex"), "\\documentclass{article}\\begin{document}x\\end{document}\n")
            put(os.path.join(root, "main.pdf"), "%PDF-1.5\n")
            git(root, "init", "-q")
            git(root, "add", "main.tex")
            git(root, "commit", "-q", "-m", "one")
            sha = git_out(root, "rev-parse", "HEAD")
            for name in ("main-live.aux", "main-live.bbl", "main-live.fls", "main-live.log", "x.log"):
                os.symlink(secret, os.path.join(build, name))
            os.chdir(root)
            leaks = []

            def leaked(what, path):
                try:
                    with open(path, errors="replace") as f:
                        if "SECRET" in f.read():
                            leaks.append(what)
                except OSError:
                    pass

            if "SECRET" in (files.read_output(root, os.path.join(build, "x.log")) or ""):
                leaks.append("read_output")
            server = fake_server(root, diff={}, bases={"head": {"pdf": "main.pdf"}})
            try:
                export.exact_export(server, "main.pdf")
            except RuntimeError:
                pass
            leaked("the export's head start", os.path.join(build, "export", "main", "main-export.aux"))
            baselines.build_diff(server, sha)
            leaked("the diff's copy", os.path.join(build, "diff", "main-diff.aux"))
            baselines.build_base(server, "head", sha, "main.pdf")
            leaked("the baseline's copy", os.path.join(build, "head", "main", "main-head.aux"))
            latex_report.keep_inputs("main-live")
            leaked("the recorder file kept", os.path.join(build, "main-live.fls.done"))
            latex_report.seed_notes_free("main-live", "main", os.path.join(build, "clean-1"))
            leaked("the notes-free pass's seed", os.path.join(build, "clean-1", "main-clean.aux"))
            status = latex_report.run_report("main-live", "fail", [])[1]
            if "SECRET" in json.dumps(status) or "an error line" in json.dumps(status):
                leaks.append("the build's log, read for its errors")
            target = os.path.join(tmp, "owners.txt")
            put(target, "kept\n")
            os.symlink(target, os.path.join(build, "planted.pdf"))
            put(os.path.join(build, "real.pdf"), "%PDF copied\n")
            if (files.copy_output(os.path.join(build, "main-live.aux"), os.path.join(build, "copy.aux"))
                    or not files.copy_output(os.path.join(build, "real.pdf"), os.path.join(build, "planted.pdf"))
                    or read_text(target) != "kept\n" or os.path.islink(os.path.join(build, "planted.pdf"))):
                leaks.append("copy_output (from a link, or written through one)")
            if leaks:
                fail(f"in shared mode, the host followed a link in build/ to a file elsewhere: {', '.join(leaks)}")
        finally:
            os.chdir(cwd)
    ok("in shared mode, the host's reads and copies of the build's files (the downloads, the diff, the baselines, the kept"
       " recorder file, the notes-free pass's seed, the build's log) follow no link, and no copy is written through one")


def build_folder_checks():
    """In shared mode, the host writes nothing through a link that TeX in a container could leave in build/: a folder
    there that is a link (build/diff, build/export, build/head, build/review, build/arxiv, build/clean, a notes-free
    pass's build/clean-<n>) is replaced by a folder of the build's own (files.build_folder) before the export, the diff,
    the baselines and the others write in it, and nothing where it leads is removed or written; the push baseline
    never links or copies a link to the HEAD baseline, nor what it leads to; a status file is never written through a
    link at its temporary name (latex_report.write_json); a folder that can't be emptied is not taken as emptied."""
    from latexlive import baselines, config, export, files
    sys.path.insert(0, HERE)
    import latex_report
    latexdiff = lambda argv, cwd, **kwargs: (0, "\\documentclass{article}\\begin{document}y\\end{document}\n" if "latexdiff" in argv else "")
    cwd = os.getcwd()
    with tempfile.TemporaryDirectory(prefix="latex-live-check-") as tmp, patched(config, SHARED=True), \
            patched(export, image_pulled=lambda: True, run_tool=lambda *args, **kwargs: (1, "")), \
            patched(baselines, image_pulled=lambda: True, run_tool=latexdiff):
        try:
            root = os.path.join(tmp, "paper")
            build = os.path.join(root, config.BUILD)
            os.makedirs(build)
            put(os.path.join(root, "main.tex"), "\\documentclass{article}\\begin{document}x\\end{document}\n")
            git(root, "init", "-q")
            git(root, "add", "main.tex")
            git(root, "commit", "-q", "-m", "one")
            sha = git_out(root, "rev-parse", "HEAD")
            put(os.path.join(build, "main-live.aux"), "\\relax\n")
            outside = {}
            for name in ("diff", "export", "head", "review", "arxiv", "clean", "clean-1"):
                folder = os.path.join(tmp, f"owners-{name}")
                for rel in ("kept.txt", os.path.join("old", "kept.txt"), os.path.join("main", "kept.txt")):
                    put(os.path.join(folder, rel), "the owner's\n")
                os.symlink(folder, os.path.join(build, name))
                outside[name] = folder

            def listing(folder):
                return sorted((os.path.relpath(os.path.join(d, f), folder), read_text(os.path.join(d, f)))
                              for d, _, names in os.walk(folder) for f in names)

            before = {name: listing(folder) for name, folder in outside.items()}
            os.chdir(root)
            server = fake_server(root, diff={}, bases={"head": {"pdf": "main.pdf"}})
            try:
                export.exact_export(server, "main.pdf")
            except RuntimeError:
                pass
            baselines.build_diff(server, sha)
            baselines.build_base(server, "head", sha, "main.pdf")
            for rel in (os.path.join("review", "main"), os.path.join("arxiv", "main"), "clean", "clean-1"):
                made = files.build_folder(root, rel, fresh=True)
                with files.create_output(os.path.join(made, "x.tex")) as f:
                    f.write(b"written\n")
            put(os.path.join(outside["diff"], "inner", "kept.txt"), "the owner's\n")  # (a link one folder further in)
            before["diff"] = listing(outside["diff"])
            shutil.rmtree(os.path.join(build, "diff", "old"))
            os.symlink(os.path.join(outside["diff"], "inner"), os.path.join(build, "diff", "old"))
            files.build_folder(root, os.path.join("diff", "old"), fresh=True)
            shutil.rmtree(os.path.join(build, "export", "main", "inputs"))
            os.symlink(outside["export"], os.path.join(build, "export", "main", "inputs"))
            export.keep_inputs(os.path.join(build, "export", "main"))
            touched = [name for name, folder in outside.items() if listing(folder) != before[name]]
            linked = [name for name in outside if os.path.islink(os.path.join(build, name))]
            if touched or linked:
                fail(f"in shared mode, the host wrote through a folder in build/ that is a link: {', '.join(touched)} changed"
                     f" outside build/, {', '.join(linked) or 'none'} still links")
            if not os.path.isfile(os.path.join(build, "diff", "main-diff.tex")):
                fail("with build/diff a link, the diff wrote no main-diff.tex in a folder of its own")
            stuck = os.path.join(build, "stuck")
            put(os.path.join(stuck, "x.tex"), "x\n")
            os.chmod(stuck, 0o555)  # (a folder whose files can't be removed: not taken as emptied)
            try:
                files.build_folder(root, "stuck", fresh=True)
                fail("build_folder took a folder it could not empty as emptied")
            except OSError:
                pass
            finally:
                os.chmod(stuck, 0o755)
            for rel in ("..", os.path.join("diff", ".."), os.path.join("diff", "..", "..", "away"), ""):
                try:
                    files.build_folder(root, rel)
                    fail(f"build_folder took {rel!r}, which is not a folder in build/")
                except ValueError:
                    pass
            secret = os.path.join(tmp, "secret.pdf")
            put(secret, "THE OWNER'S SECRET\n")
            os.symlink(secret, os.path.join(build, "main-head.pdf"))
            baselines.share_head_base(server, "main.pdf")
            push = os.path.join(build, "main-push.pdf")
            if os.path.lexists(push) and (os.path.islink(push) or "SECRET" in read_text(push)):
                fail("the push baseline links or copies a link that build/ holds for the HEAD baseline")
            os.remove(os.path.join(build, "main-head.pdf"))
            put(os.path.join(build, "main-head.pdf"), "%PDF head\n")
            baselines.share_head_base(server, "main.pdf")
            if read_text(push) != "%PDF head\n" or os.path.islink(push):
                fail("the push baseline is not the HEAD baseline's PDF when nothing is unpushed")
            status = os.path.join(build, "clean", "main-clean.status.json")
            target = os.path.join(tmp, "owners.json")
            put(target, "kept\n")
            os.symlink(target, f"{status}.{os.getpid()}-{threading.get_ident()}.tmp")
            latex_report.write_json(status, {"ok": True})
            if read_text(target) != "kept\n" or json.loads(read_text(status)) != {"ok": True}:
                fail("a status file was written through a link at its temporary name")
        finally:
            os.chdir(cwd)
    ok("in shared mode, a folder in build/ that is a link is made a folder of the build's own before the host writes in"
       " it (the export, the diff, the baselines, the review copy, the arXiv package, the notes-free pass), the push"
       " baseline never takes a link, and no status file is written through one")


def shared_build_checks():
    """Shared mode's builder (shared_build.shared_run) prints the page layout as latexmk does: shared_build.LAYOUT_TEX is
    latexmkrc's $pre_tex_code."""
    from latexlive import config, files, shared_build
    with open(os.path.join(HERE, "latexmkrc")) as f:
        rc = re.search(r"^\$pre_tex_code = (.*?);$", f.read(), re.S | re.M).group(1)
    code = "".join(re.sub(r"\\([\\'])", r"\1", part) for part in re.findall(r"'((?:[^'\\]|\\.)*)'", rc))
    if code != shared_build.LAYOUT_TEX:
        fail(f"shared_build.LAYOUT_TEX is not latexmkrc's $pre_tex_code:\n{shared_build.LAYOUT_TEX}\n{code}")
    with tempfile.TemporaryDirectory(prefix="latex-live-check-") as root:
        server = fake_server(root)
        bcf = '<bcf:datasource type="file" datatype="bibtex" glob="false">refs.bib</bcf:datasource>'
        put(os.path.join(root, "refs.bib"), "@misc{a}\n")
        before = shared_build.biber_state(server, bcf)
        put(os.path.join(root, "refs.bib"), "@misc{a, title = {A}}\n")
        if shared_build.biber_state(server, bcf) == before:
            fail("shared mode's builder would not run biber again after a change to a .bib file the .bcf names")
    with tempfile.TemporaryDirectory(prefix="latex-live-check-") as root:
        cwd = os.getcwd()
        os.chdir(root)
        try:
            for name, text in (("main-live.aux", "\\relax\n\\@input{chapter.aux}\n\\@input{../outside.aux}\n"),
                               ("chapter.aux", "\\relax\n\\citation{late}\n"), ("../outside.aux", "\\citation{outside}\n")):
                os.makedirs(os.path.dirname(os.path.join(config.BUILD, name)) or config.BUILD, exist_ok=True)
                put(os.path.join(config.BUILD, name), text)
            tree = files.aux_tree(read_text(os.path.join(config.BUILD, "main-live.aux")))
        finally:
            os.chdir(cwd)
        if "\\citation{late}" not in tree or "outside" in tree.replace("../outside.aux", ""):
            fail(f"shared mode's builder doesn't read a chapter's .aux for bibtex (or reads one outside build/): {tree!r}")
    ok("shared mode's builder prints the page layout as latexmkrc does, runs biber again when a data source changes, and"
       " bibtex when a chapter's .aux changes")
    shared_parsing_checks()


def shared_parsing_checks():
    """What the host reads of the files the paper's TeX writes stays within the build and within bounds (live.py, no
    TeX): an .aux's \\@input of an absolute path, of .. or through a link reads nothing (aux_tree, and written_contents
    for the outline); a JSON file, a recorder file, a SyncTeX file (records, size, a line without an end, numbers that
    aren't, a file that isn't gzip) are read only up to their bounds, quickly; and the bibliographies and styles that the
    .aux and .bcf name, at any depth and even before they exist, are what bibtex's and biber's states depend on and
    what the builder watches."""
    from latexlive import config, document, files, shared_build, shared_watch, synctex
    cwd = os.getcwd()
    with tempfile.TemporaryDirectory(prefix="latex-live-check-") as tmp, patched(config, SHARED=True):
        try:
            root = os.path.join(tmp, "paper")
            build = os.path.join(root, config.BUILD)
            os.makedirs(build)
            os.chdir(root)
            secret = os.path.join(tmp, "other.aux")
            put(secret, "\\citation{secretkey}\n\\@writefile{toc}{\\contentsline {section}{\\numberline {1}Secret heading}{1}}\n")
            os.symlink(secret, os.path.join(build, "link.aux"))
            put(os.path.join(build, "main-live.aux"), f"\\relax\n\\@input{{{secret}}}\n\\@input{{../../other.aux}}\n\\@input{{link.aux}}\n"
                "\\@input{chapter.aux}\n")
            put(os.path.join(build, "chapter.aux"), "\\citation{chapterkey}\n")
            tree = files.aux_tree(read_text(os.path.join(build, "main-live.aux")))
            heads = document.written_contents(os.path.join(build, "main-live.aux"), "toc")
            if "secretkey" in tree or "chapterkey" not in tree or "Secret heading" in json.dumps(heads):
                fail(f"an .aux's \\@input of an absolute path, of .. or through a link was read: {tree!r} {heads!r}")

            put(os.path.join(build, "big.json"), "[" + "1," * (1 << 20) + "1]")
            began = time.monotonic()
            if files.read_json(os.path.join(build, "big.json")) is not None or time.monotonic() - began > 2:
                fail("a JSON file past config.JSON_LIMIT was parsed")
            put(os.path.join(build, "main-live.fls"), "INPUT main.tex\n" * 5000)
            with patched(config, RECORD_LIMIT=1000):
                if len(files.fls_inputs(os.path.join(build, "main-live.fls"))) != 1000:
                    fail("a recorder file was read past config.RECORD_LIMIT lines")
            synctex_gz = os.path.join(build, "main-live.synctex.gz")
            head = "SyncTeX Version:1\nInput:1:/paper/main.tex\nContent:\n{x\n{1\n[1,7:4736286,4736286:30000000,40000000,0\n"
            for body in ("h1,7:4736286,9000000:100000\n" * 400_000, "x" * (20 << 20), "h1,seven:a,b:c\n" * 10):
                with gzip.open(synctex_gz, "wt") as f:  # (a bomb: a few KB compressed)
                    f.write(head + body)
                began = time.monotonic()
                try:
                    with patched(config, RECORD_LIMIT=2_000_000, SYNCTEX_LIMIT=1 << 20):
                        points = synctex.synctex_points(synctex_gz)
                        synctex.synctex_boxes(synctex_gz)
                except Exception as e:
                    fail(f"a SyncTeX file the paper's TeX could write raised {type(e).__name__}: {e}")
                if time.monotonic() - began > 10 or sum(len(v) for v in points.values()) > 1 << 20:
                    fail("a SyncTeX file was read past config.SYNCTEX_LIMIT")
            put(synctex_gz, "not gzip at all")
            if synctex.synctex_points(synctex_gz) or list(synctex.synctex_records(synctex_gz)):
                fail("a SyncTeX file that isn't gzip gave records")

            server = fake_server(root)
            for name in ("deep/er/refs.bib", "styles/local.bst"):
                put(os.path.join(root, name), "% x\n")
            auxes = "\\bibdata{deep/er/refs,later/refs,../outside,/etc/passwd}\n\\bibstyle{styles/local}\n"
            deps = sorted(os.path.relpath(p, os.path.realpath(root)) for p in shared_watch.shared_deps(server, auxes, ""))
            if deps != ["deep/er/refs.bib", "later/refs.bib", "styles/local.bst"]:
                fail(f"the bibliographies and styles an .aux names, for the builder to watch: {deps}")
            before = shared_build.bib_state(server, auxes)
            put(os.path.join(root, "styles/local.bst"), "% changed\n")
            if shared_build.bib_state(server, auxes) == before:
                fail("shared mode's builder would not run bibtex again after a change to the .bst the .aux names")
            before = shared_build.bib_state(server, auxes)
            put(os.path.join(root, "later/refs.bib"), "@misc{x}\n")
            if shared_build.bib_state(server, auxes) == before:
                fail("shared mode's builder would not run bibtex again once a .bib the .aux names appears")
        finally:
            os.chdir(cwd)
    ok("the host reads no .aux outside build/ (absolute, .., links), and JSON, recorder and SyncTeX files only within"
       " bounds; bibtex's state and the builder's watch cover the .bib and .bst files an .aux names, at any depth, even"
       " before they exist")


def baseline_failure_checks():
    """In shared mode, a diff or a change-highlight baseline that fails says why without the server's folders
    (failure, as the downloads do): an error that names a file in full names it relative to the paper; outside shared
    mode, the owner's own preview still names it in full."""
    from latexlive import baselines, config
    def run_tool(*args, **kwargs):
        raise OSError(2, "No such file or directory", missing)
    with tempfile.TemporaryDirectory(prefix="latex-live-check-") as tmp, patched(baselines, run_tool=run_tool, image_pulled=lambda: True):
        root = os.path.join(tmp, "paper")
        put(os.path.join(root, "main.tex"), "\\documentclass{article}\\begin{document}x\\end{document}\n")
        for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "x"]):
            git(root, *args)
        sha = git_out(root, "rev-parse", "HEAD")
        missing = os.path.join(root, config.BUILD, "diff", "gone.tex")
        said = {}
        for shared in (True, False):
            server = fake_server(root, diff={"rev": "HEAD"}, bases={"head": {"pdf": "main.pdf"}})
            with patched(config, SHARED=shared):
                baselines.build_diff(server, sha)
                baselines.build_base(server, "head", sha, "main.pdf")
            said[shared] = (server.diff.get("msg", ""), server.bases["head"].get("msg", ""))
    if any(root in msg or tmp in msg or "diff/gone.tex" not in msg for msg in said[True]):
        fail(f"in shared mode, a failed diff or baseline names the server's folders, or not the file: {said[True]}")
    if not all(root in msg for msg in said[False]):
        fail(f"outside shared mode, a failed diff or baseline no longer names the file in full: {said[False]}")
    ok("in shared mode, a failed diff or change-highlight baseline names no folder of the server's")


# A paper for shown_notes_checks: notes that the PDF shows, and notes that it doesn't (one only measured, one with the
# text of a note typeset again in the contents), whose words it has elsewhere.
SHOWN_PAPER = r"""\documentclass{article}
\usepackage{xcolor,hyperref}
\newif\ifshowtodos \showtodostrue
\newcommand{\todotxt}[1]{\ifshowtodos\textcolor{red}{#1}\fi}
\newcommand{\eb}[1]{\todotxt{[EB: #1]}}\input{defs}\newlength\notewidth
\begin{document}\tableofcontents
\section{Start \eb{in a title}}
Hello world, this is a scratch paper. Why not? \eb{shown note}
\iffalse\eb{Hello world, this is a scratch paper -- PRIVATE: reviewer two}\eb{in a title}\fi
\ifnum0=1 \eb{why?}\fi
\eb{one} \iffalse\eb{two}\fi
\eb{a note on
% a comment
three lines}
\begin{figure}\caption{A caption \eb{in a caption}
}\end{figure}
\fromdefs \settowidth{\notewidth}{\eb{only measured}}
\showtodosfalse \eb{while todotxt hides notes}
\end{document}
"""
SHOWN_DEFS = "\\newcommand{\\fromdefs}{\\eb{from a macro}}\n"
# (each record: the line TeX was reading, and what \todotxt typeset, without its spaces; the contents may add one, at
# line 1 of the .toc, which shows no note)
SHOWN_RECORDS = ((7, "[EB:inatitle]"), (8, "[EB:shownnote]"), (11, "[EB:one]"), (14, "[EB:anoteonthreelines]"),
                 (16, "[EB:inacaption]"), (17, "[EB:fromamacro]"))
SHOWN_TEXTS = {"in a title", "shown note", "one", "a note on three lines", "in a caption", "from a macro"}


def shown_notes_checks():
    """Which notes viewers and commenters see (notes.shown_notes): those whose typesetting the published PDF's pass
    recorded (notes.shown_tex), not one in \\iffalse or a switch that is off, nor while \\todotxt hides them, however
    much of their text the PDF has elsewhere; none for a PDF without records (a build before a restart, another PDF
    on show) or while a source differs from what its pass read, nor for records of another key (in the paper's text). With the host's pdflatex, shown_tex records just
    those and changes nothing else that the build writes."""
    from latexlive import config, notes
    with tempfile.TemporaryDirectory(prefix="latex-live-check-") as tmp:
        root = os.path.join(tmp, "paper")
        put(os.path.join(root, "main.tex"), SHOWN_PAPER)
        put(os.path.join(root, "defs.tex"), SHOWN_DEFS)
        put(os.path.join(root, config.BUILD, "main-live.fls"), f"PWD {root}\nINPUT main.tex\nINPUT defs.tex\n")
        put(os.path.join(root, "main.pdf"), "%PDF-1.4\n")
        st = os.stat(os.path.join(root, "main.pdf"))
        published = {"mtime": st.st_mtime_ns, "size": st.st_size}
        digest = {text: hashlib.md5(text.encode()).hexdigest().upper() for _, text in SHOWN_RECORDS}
        log = "".join(f"latex-live|shown|k3y||main.tex|{line}|{digest[text]}\n" for line, text in SHOWN_RECORDS)
        log += f"latex-live|shown|k3y||main.tex|1|{digest['[EB:inatitle]']}\nlatex-live|shown|k3y||other.tex|40|{digest['[EB:inatitle]']}\n"
        log += "latex-live|shown|k3y||main.tex|9|0123\nlatex-live|shown|k3y|x\n  latex-live|shown|k3y||main.tex|9|" + "A" * 32 + "\n"
        records = notes.note_records(log, "k3y")
        if notes.note_records(log.replace("k3y", "other"), "k3y") or notes.note_records(log, ""):
            fail("note_records takes records of another key, or of none")
        sources = notes.source_digests(root, "main.pdf")
        server = fake_server(root, shared={"published": published, "shown": {"published": published, "records": records, "sources": sources}})
        found = notes.notes(root, "main.pdf")
        if len(found) != 12:
            fail(f"the check's paper has 12 notes, notes() found {len(found)}: {found}")
        seen = {n["text"] for n in notes.shown_notes(server, "main.pdf", found)}
        if seen != SHOWN_TEXTS:
            fail(f"viewers would see {sorted(seen)}, not the notes the PDF shows: {sorted(SHOWN_TEXTS)}")
        margin = [{**n, "tex": n["tex"].replace("on three", "on \x7f three")} for n in found]  # (margin_notes keeps a \x7f)
        if {n["text"] for n in notes.shown_notes(server, "main.pdf", margin)} != SHOWN_TEXTS:
            fail("a margin note across a commented-out line is not matched to its record")
        for what, stale in (("before the first build (no records)", {"published": published, "shown": None}),
                            ("after another publish", {"published": {**published, "size": 1}, "shown": server.shared["shown"]}),
                            ("for records of another publish", {"published": published,
                                                                "shown": {**server.shared["shown"], "published": {**published, "mtime": 1}}})):
            if notes.shown_notes(fake_server(root, shared=stale), "main.pdf", found):
                fail(f"viewers would see notes {what}")
        if notes.shown_notes(fake_server(root, default_pdf="other.pdf", shared=server.shared), "main.pdf", found):
            fail("viewers would see notes of a PDF that isn't the shared document")
        put(os.path.join(root, "defs.tex"), SHOWN_DEFS.replace("from a macro", "a private draft"))
        if notes.shown_notes(server, "main.pdf", found):
            fail("viewers would see notes while a source differs from what the PDF's pass read (a macro in them, say)")
        put(os.path.join(root, "defs.tex"), SHOWN_DEFS)
        for what, unsure in (("for a pass during which a source changed", None), ("for a source the pass didn't read", {})):
            if notes.shown_notes(fake_server(root, shared={**server.shared, "shown": {**server.shared["shown"], "sources": unsure}}),
                                 "main.pdf", found):
                fail(f"viewers would see notes {what}")
        put(os.path.join(root, "main.pdf"), "%PDF-1.4\n% replaced\n")  # (by something other than shared_publish)
        if notes.shown_notes(server, "main.pdf", found):
            fail("viewers would see notes of a PDF replaced since its records")
        st = os.stat(os.path.join(root, "main.pdf"))
        published = {"mtime": st.st_mtime_ns, "size": st.st_size}
        parsed = notes.note_records(f"latex-live|shown|k|sub|x.tex|3|{'B' * 32}\nlatex-live|shown|k|/abs/dir|y.tex|4|{'C' * 32}\n"
                                    f"latex-live|shown|k|||5|{'D' * 32}\n", "k")
        if parsed != [("sub/x.tex", 3, "b" * 32), ("/abs/dir/y.tex", 4, "c" * 32), ("", 5, "d" * 32)]:
            fail(f"note_records: {parsed}")
        tex_checks(root, published)
    ok("viewers and commenters see the notes whose typesetting the PDF's pass recorded: not those in \\iffalse or a switch"
       " that is off, nor a short one whose letters the PDF has elsewhere; none for a PDF without records")


def tex_checks(root, published):
    """shown_notes_checks with the host's pdflatex, if there is one: shown_tex's records of SHOWN_PAPER are just those
    of SHOWN_RECORDS (and the contents' one) and show just those notes, also when the paper's \\todotxt is \\protected
    or robust, none for one with two arguments, and the PDF's text, the log but for the records, and the .aux, .toc
    and .out are as without shown_tex. One that writes its argument into the contents writes none of shown_tex's
    commands there (but for a group: {}), and one that highlights its argument with soul still builds."""
    from latexlive import config, notes
    if not shutil.which("pdflatex"):
        print("skip: no pdflatex here, so notes.shown_tex is not run", flush=True)
        return
    build = os.path.join(root, config.BUILD)

    def run(job, pre):
        for _ in range(2):
            subprocess.run(["pdflatex", "-interaction=batchmode", f"-output-directory={build}", f"-jobname={job}",
                            f"{pre}\\input{{main}}"], cwd=root, env={**os.environ, "max_print_line": "10000"},
                           capture_output=True, timeout=120)
        with open(os.path.join(build, f"{job}.log"), errors="replace") as f:
            return f.read()

    plain, key = "\\newcommand{\\todotxt}[1]{\\ifshowtodos\\textcolor{red}{#1}\\fi}", "0123456789abcdef"
    want, again = sorted(SHOWN_RECORDS), sorted([*SHOWN_RECORDS, (1, "[EB:inatitle]")])  # (the contents: line 1 of the .toc)
    for name, todotxt, want in (
            ("one argument", plain, want),
            ("\\protected", "\\protected\\long\\def\\todotxt#1{\\ifshowtodos\\textcolor{red}{#1}\\fi}", again),
            ("robust", "\\DeclareRobustCommand{\\todotxt}[1]{\\ifshowtodos\\textcolor{red}{#1}\\fi}", again),
            ("two arguments", "\\newcommand{\\todotxt}[2][red]{\\ifshowtodos\\textcolor{#1}{#2}\\fi}", [])):
        put(os.path.join(root, "main.tex"), SHOWN_PAPER.replace(plain, todotxt))
        for ext in (".aux", ".toc", ".out", ".log", ".pdf"):
            for job in ("main-live", "main-plain"):
                if os.path.exists(os.path.join(build, job + ext)):
                    os.remove(os.path.join(build, job + ext))
        log, before = run("main-live", notes.shown_tex(key)), run("main-plain", "")
        records = notes.note_records(log, key)
        texts = {hashlib.md5(text.encode()).hexdigest(): text for _, text in SHOWN_RECORDS}
        got = sorted((line, texts.get(digest, digest)) for _, line, digest in records)
        server = fake_server(root, shared={"published": published, "shown": {"published": published, "records": records,
                                                                             "sources": notes.source_digests(root, "main.pdf")}})
        seen = {n["text"] for n in notes.shown_notes(server, "main.pdf", notes.notes(root, "main.pdf"))}
        if got != want or seen != (SHOWN_TEXTS if want else set()):
            fail(f"shown_tex with a {name} \\todotxt recorded {got}, not {want}; viewers would see {sorted(seen)}")
        if not os.path.isfile(os.path.join(build, "main-live.pdf")):
            fail(f"the build with shown_tex and a {name} \\todotxt made no PDF: {log[-1500:]}")
        mine = [line for line in log.splitlines()[1:] if not line.startswith(("latex-live|shown|", "**"))]
        theirs = [line for line in before.replace("main-plain", "main-live").splitlines()[1:] if not line.startswith("**")]
        if [line for line in mine if "out of" not in line] != [line for line in theirs if "out of" not in line]:
            fail(f"shown_tex changes the log of a paper with a {name} \\todotxt: {set(mine) ^ set(theirs)}")
        for ext in (".aux", ".toc", ".out"):
            if read_text(os.path.join(build, "main-live" + ext)) != read_text(os.path.join(build, "main-plain" + ext)).replace("main-plain", "main-live"):
                fail(f"shown_tex changes the {ext} of a paper with a {name} \\todotxt")
        if shutil.which("pdftotext"):
            text = [subprocess.run(["pdftotext", os.path.join(build, f"{job}.pdf"), "-"], capture_output=True).stdout
                    for job in ("main-live", "main-plain")]
            if text[0] != text[1]:
                fail(f"shown_tex changes the PDF's text with a {name} \\todotxt")
    put(os.path.join(root, "main.tex"), "\\documentclass{article}\\usepackage{xcolor}\n"
        "\\newcommand{\\todotxt}[1]{\\addcontentsline{toc}{subsection}{#1}\\textcolor{red}{#1}}\n"
        "\\newcommand{\\eb}[1]{\\todotxt{[EB: #1]}}\n\\begin{document}\\tableofcontents\nWords \\eb{one note} more.\n\\end{document}\n")
    log = run("main-live", notes.shown_tex(key))
    written = read_text(os.path.join(build, "main-live.aux")) + read_text(os.path.join(build, "main-live.toc"))
    if [(line, digest) for _, line, digest in notes.note_records(log, key)] != [(5, hashlib.md5(b"[EB:onenote]").hexdigest())] \
            or "latexlive" in written or "[EB: one note]" not in written:
        fail(f"shown_tex with a \\todotxt that writes its argument into the contents: {written} {log[-1500:]}")
    if subprocess.run(["kpsewhich", "soul.sty"], capture_output=True).returncode == 0:  # (\\hl takes a group as one piece)
        put(os.path.join(root, "main.tex"), "\\documentclass{article}\\usepackage{xcolor,soul}\\newcommand{\\todotxt}[1]{\\hl{#1}}\n"
            "\\newcommand{\\eb}[1]{\\todotxt{[EB: #1]}}\n\\begin{document}\nWords \\eb{one note} more.\\iffalse\\eb{hidden}\\fi\n\\end{document}\n")
        log = run("main-live", notes.shown_tex(key))
        if [(line, digest) for _, line, digest in notes.note_records(log, key)] != [(4, hashlib.md5(b"[EB:onenote]").hexdigest())]:
            fail(f"shown_tex with a \\todotxt that highlights with soul: {log[-1500:]}")
