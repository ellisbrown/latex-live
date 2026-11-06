"""The scratch paper that the instance checks build (make_paper: two columns, lineno line numbers, inline notes, a
git repository with a pushed commit and a local one), which the early notes-free pass's checks and shared mode's
copy start from too, and what the package its review copy's probe pass plants must not do (composed)."""

import os
import subprocess

from .base import fail
from .helpers import git


PAPER = r"""\documentclass[twocolumn]{article}
\usepackage{xcolor}
\usepackage[switch]{lineno}
\linenumbers
\newif\ifshowtodos \showtodostrue
\newcommand{\todotxt}[1]{\ifshowtodos\textcolor{red}{#1}\fi}
\newcommand{\eb}[1]{\todotxt{[EB: #1]}}\input{people}
\begin{document}
\section{Introduction}
\eb{A note long enough that hiding it moves the text below it. A note long enough that hiding it
moves the text below it. A note long enough that hiding it moves the text below it.}
\input{body}
\section{Method}\label{sec:method}
See \ref{sec:method}. A sentence with a note \eb{a note in a paragraph, about \ref{sec:method}} and more
words after it.\footnote{A footnote \eb{a note in a footnote}.} \ifnum0=1 \eb{a note in a branch that is off}\fi
% \eb{a resolved note}
\eb{human-written}
% In the review copy's probe pass only: a package that TeX writes into that pass's folder, which the
% copy's last pass uses and must not load from there.
\makeatletter\ifdefined\latexlive@record\newwrite\planted\immediate\openout\planted=lmodern.sty
\immediate\write\planted{\noexpand\typeout{latex-live-check: a package that TeX wrote ran}}\immediate\closeout\planted\fi\makeatother
\end{document}
"""

# The coauthors' note macros (the users of shared_setup), in a file of their own so that main.tex
# keeps its line numbers.
PEOPLE = r"""\newcommand{\ed}[1]{\todotxt{[ED: #1]}}
\newcommand{\cm}[1]{\todotxt{[CM: #1]}}
"""
# The start of body.tex, for the notes in shared mode (note_checks), near the top of page 1: a paragraph
# with a note of the owner's, and one that the owner's protected-text marker ends (LATEX_LIVE_PROTECTED).
NOTE_PARAGRAPHS = r"""
Quokka narwhal axolotl pangolin tapir gecko. \eb{An owner note to resolve.}

Walrus yak zebu ibex okapi. \eb{as written}

"""


def make_paper(tmp):
    subprocess.run(["git", "init", "-q", "--bare", os.path.join(tmp, "remote.git")], check=True)
    paper = os.path.join(tmp, "paper")
    os.makedirs(paper)
    with open(os.path.join(paper, "main.tex"), "w") as f:
        f.write(PAPER)
    with open(os.path.join(paper, "people.tex"), "w") as f:
        f.write(PEOPLE)
    words = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu".split()
    with open(os.path.join(paper, "body.tex"), "w") as f:
        f.write(NOTE_PARAGRAPHS + "\n\n".join(" ".join(words[(i * 7 + j) % 12] for j in range(90)) for i in range(24)) + "\n")
    git(paper, "init", "-q", "-b", "main")
    git(paper, "add", "-A")
    git(paper, "commit", "-qm", "first")
    git(paper, "remote", "add", "origin", os.path.join(tmp, "remote.git"))
    git(paper, "push", "-q", "-u", "origin", "main")
    with open(os.path.join(paper, "body.tex"), "a") as f:
        f.write("\nomega omega omega.\n")
    git(paper, "commit", "-qam", "unpushed")
    return paper


def composed(build):
    """The review copy's last pass (in build/review/main/.copy/) loaded no package that the paper's TeX wrote into the
    probe pass's folder (main.tex has the probe pass write lmodern.sty there, a package that the last pass uses)."""
    with open(os.path.join(build, "review", "main", ".copy", "main-review.log"), errors="replace") as f:
        log = f.read()
    if not os.path.isfile(os.path.join(build, "review", "main", "lmodern.sty")):
        fail("the probe pass wrote no lmodern.sty into its folder (the scratch paper has it write one)")
    if "latex-live-check: a package that TeX wrote ran" in log:
        fail("the review copy's last pass loaded lmodern.sty from the folder the paper's TeX writes into")
