"""The unit checks of shared mode's parts, without an instance: the downloads' guards (confinement_checks),
the builder (shared_build_checks), and what the host reads of the files the paper's TeX writes
(shared_parsing_checks)."""

import gzip
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import types

from .base import HERE, fail, ok
from .helpers import put, read_text, tex_stand_in


def confinement_checks():
    """Shared mode's guards around the downloads built on request (live.py), without TeX: with the TeX Live image
    missing, the export and the review copy fail with a message that says so (never the host's TeX); the review
    copy's notes come from the last good build's list of files (.fls.done, not the .fls a build is rewriting), and
    only from the paper's own files (not a link to a file elsewhere, nor a dot file or a file in a dot folder, which
    TeX doesn't read there, though that list names them: the paper's TeX can write it); the inputs kept for its
    probe leave out symbolic links; a failure names no folder of the server's (the paper's, latex-live's), nor the
    command line of a TeX run that took too long. Outside shared mode, links count as before, and so do failures."""
    from latexlive import config, export, review
    saved, tmp = (config.SHARED, export.image_pulled, review.image_pulled), tempfile.mkdtemp(prefix="latex-live-check-")
    try:
        config.SHARED, export.image_pulled, review.image_pulled = True, lambda: False, lambda: False
        root = os.path.join(tmp, "paper")
        build = os.path.join(root, config.BUILD)
        os.makedirs(build)
        server = types.SimpleNamespace(paper_root=root, default_pdf="main.pdf", tex_exec="false", export={}, review={},
                                       exported={}, export_lock=threading.Lock(), review_lock=threading.Lock(),
                                       download_lock=threading.Lock())
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
    finally:
        config.SHARED, export.image_pulled, review.image_pulled = saved
        shutil.rmtree(tmp, ignore_errors=True)
    ok("in shared mode, the downloads built on request say plainly that the TeX Live image is missing, the review copy"
       " reads the last good build's own files (no dot files) and keeps no links for its probe, and failures name no"
       " folder of the server's")


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
        server = types.SimpleNamespace(paper_root=root)
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
    saved = (config.SHARED, config.SYNCTEX_LIMIT, config.RECORD_LIMIT)
    tmp = tempfile.mkdtemp(prefix="latex-live-check-")
    cwd = os.getcwd()
    try:
        config.SHARED = True
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
        config.RECORD_LIMIT = 1000
        put(os.path.join(build, "main-live.fls"), "INPUT main.tex\n" * 5000)
        if len(files.fls_inputs(os.path.join(build, "main-live.fls"))) != 1000:
            fail("a recorder file was read past config.RECORD_LIMIT lines")
        config.RECORD_LIMIT, config.SYNCTEX_LIMIT = 2_000_000, 1 << 20
        synctex_gz = os.path.join(build, "main-live.synctex.gz")
        head = "SyncTeX Version:1\nInput:1:/paper/main.tex\nContent:\n{x\n{1\n[1,7:4736286,4736286:30000000,40000000,0\n"
        for body in ("h1,7:4736286,9000000:100000\n" * 400_000, "x" * (20 << 20), "h1,seven:a,b:c\n" * 10):
            with gzip.open(synctex_gz, "wt") as f:  # (a bomb: a few KB compressed)
                f.write(head + body)
            began = time.monotonic()
            try:
                points = synctex.synctex_points(synctex_gz)
                synctex.synctex_boxes(synctex_gz)
            except Exception as e:
                fail(f"a SyncTeX file the paper's TeX could write raised {type(e).__name__}: {e}")
            if time.monotonic() - began > 10 or sum(len(v) for v in points.values()) > 1 << 20:
                fail("a SyncTeX file was read past config.SYNCTEX_LIMIT")
        put(synctex_gz, "not gzip at all")
        if synctex.synctex_points(synctex_gz) or list(synctex.synctex_records(synctex_gz)):
            fail("a SyncTeX file that isn't gzip gave records")

        server = types.SimpleNamespace(paper_root=root)
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
        config.SHARED, config.SYNCTEX_LIMIT, config.RECORD_LIMIT = saved
        shutil.rmtree(tmp, ignore_errors=True)
    ok("the host reads no .aux outside build/ (absolute, .., links), and JSON, recorder and SyncTeX files only within"
       " bounds; bibtex's state and the builder's watch cover the .bib and .bst files an .aux names, at any depth, even"
       " before they exist")
