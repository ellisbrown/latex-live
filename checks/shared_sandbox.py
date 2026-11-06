"""Shared mode's container sandbox (sandbox_checks): what a real container that tex-exec confines may do (as the
owner's own user, not root; a read-only root file system; podman's seccomp filter; no swap; outputs owned by the
owner), and that every TeX tool shared mode runs still works in it, font generation included."""

import os
import shutil
import subprocess

from .base import ENV, HERE, fail, ok
from .helpers import put, texlive_image


SANDBOX_PAPER = {
    "main.tex": "\\documentclass{article}\\usepackage{makeidx}\\makeindex\n\\begin{document}\nHello\\index{hello} \\cite{a}.\n"
                "\\bibliographystyle{plain}\\bibliography{refs}\\printindex\n\\end{document}\n",
    "bib.tex": "\\documentclass{article}\\usepackage[backend=biber]{biblatex}\\addbibresource{refs.bib}\n"
               "\\begin{document}\\cite{a}\\printbibliography\\end{document}\n",
    # (no font map: pdflatex needs a bitmap font at a size the image has none of, which mktexpk makes and caches)
    "pk.tex": "\\pdfmapfile{}\\documentclass{article}\\begin{document}\\font\\x=cmssq8 at 13.37pt \\x A new bitmap font.\\end{document}\n",
    "new.tex": "\\documentclass{article}\\begin{document}\nHello again.\n\\end{document}\n",
    "refs.bib": "@article{a, author={A. Author}, title={T}, journal={J}, year={2020}}\n",
}


def sandbox_checks(sh):
    """A real container of tex-exec with LATEX_LIVE_SANDBOX=1 runs as the owner's own uid (not root in it), with a
    read-only root file system and a sized /tmp, podman's seccomp filter on, no new privileges, no capabilities and
    no swap beyond its memory; what it writes in build/ belongs to the owner. pdflatex (making and caching a bitmap
    font), bibtex, biber, makeindex, SyncTeX and latexdiff all work there."""
    root = os.path.realpath(os.path.join(sh["tmp"], "sandbox-paper"))
    for name, text in SANDBOX_PAPER.items():
        put(os.path.join(root, name), text)
    build = os.path.join(root, "build")
    os.makedirs(build, exist_ok=True)
    image = texlive_image()[0]
    env = {**ENV, "LATEX_LIVE_SANDBOX": "1", "LATEX_LIVE_REQUIRE_IMAGE": "1", "LATEX_LIVE_INSTANCE": f"check-sandbox-{os.getpid()}"}

    def run(*args, cwd=root, **extra):
        r = subprocess.run([os.path.join(HERE, "tex-exec"), image, root, *args], cwd=cwd, env={**env, **extra},
                           capture_output=True, text=True, timeout=300)
        return r.returncode, r.stdout + r.stderr

    try:
        code, out = run("sh", "-c", "echo uid $(id -u); grep -E '^(Seccomp|NoNewPrivs|CapEff):' /proc/self/status;"
                        " if touch /rootfs-probe 2>/dev/null; then echo rootfs writable; else echo rootfs read-only; fi;"
                        " echo swap $(cat /sys/fs/cgroup/memory.swap.max); echo tmp $(df -k /tmp | awk 'NR == 2 { print $2 }');"
                        " echo texmfvar $TEXMFVAR; touch build/owned")
        seen = dict((line.split(None, 1) + [""])[:2] for line in out.splitlines() if line.strip())
        tmp_kb = int(seen["tmp"]) if seen.get("tmp", "").isdigit() else 0
        if (code or seen.get("uid") != str(os.getuid()) or seen.get("uid") == "0" or seen.get("Seccomp:") != "2"
                or seen.get("NoNewPrivs:") != "1" or seen.get("CapEff:", "1").strip("0") or seen.get("rootfs") != "read-only"
                or seen.get("swap") != "0" or not 0 < tmp_kb <= 512 * 1024 or seen.get("texmfvar") != "/tmp/texmf-var"):
            fail(f"a shared-mode container is not confined as tex-exec means it to be (exit {code}): {seen}")
        if os.stat(os.path.join(build, "owned")).st_uid != os.getuid():
            fail("a file that a shared-mode container wrote in build/ is not the owner's")
        ok("a shared-mode container runs as the owner's own uid, not root, on a read-only root file system with a sized /tmp,"
           " with podman's seccomp filter, no new privileges, no capabilities and no swap, and its files are the owner's")

        failed = []
        steps = [(("pdflatex", "-interaction=nonstopmode", "-synctex=1", "-output-directory=build", "main.tex"), root, "main.aux"),
                 (("bibtex", "main"), build, "main.bbl"), (("makeindex", "main.idx"), build, "main.ind"),
                 (("pdflatex", "-interaction=nonstopmode", "-synctex=1", "-output-directory=build", "main.tex"), root, "main.pdf"),
                 (("pdflatex", "-interaction=nonstopmode", "-output-directory=build", "bib.tex"), root, "bib.bcf"),
                 (("biber", "--input-directory=build", "--output-directory=build", "bib"), root, "bib.bbl"),
                 (("pdflatex", "-interaction=nonstopmode", "-output-directory=build", "pk.tex"), root, "pk.pdf")]
        for args, cwd, made in steps:
            code, out = run(*args, cwd=cwd, BIBINPUTS=f"{root}:")
            if code or not os.path.isfile(os.path.join(build, made)):
                failed.append(f"{' '.join(args[:1])} (exit {code}, no build/{made}): {out[-400:]!r}")
        code, out = run("synctex", "view", "-i", "3:0:main.tex", "-o", "build/main.pdf")
        if code or "Page:1" not in out:
            failed.append(f"synctex (exit {code}): {out[-400:]!r}")
        code, out = run("latexdiff", "main.tex", "new.tex")
        if code or "\\DIF" not in out:
            failed.append(f"latexdiff (exit {code}): {out[-400:]!r}")
        if failed:
            fail("in a shared-mode container, " + "; ".join(failed))
        ok("pdflatex (with a bitmap font made and cached in the container), bibtex, biber, makeindex, SyncTeX and latexdiff"
           " work in a shared-mode container")
    finally:
        shutil.rmtree(root, ignore_errors=True)
