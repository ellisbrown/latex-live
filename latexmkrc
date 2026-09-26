# latex-live build config (LaTeX Workshop replacement); used by live.py.
# Manual use from a paper root:  latexmk -r ~/.local/share/latex-live/latexmkrc -pvc <doc>
#
# Builds into build/ in the paper root. live.py adds -jobname=<doc>-live, so
# TeX and bibtex never pick up stale <doc>.aux/.bbl left in the root by
# in-place builds (`make`). After each run, latex_report.py publishes <doc>.pdf
# to the paper root (successful runs only, atomic rename, so the viewer never
# reloads a half-written or broken file) and prints file:line diagnostics for
# the VS Code Problems panel.
#
# LATEX_LIVE_EXEC (set by live.py) prefixes the TeX tools, e.g. with
# `podman exec` into a TeX Live 2024 container; latexmk itself stays on the host.

my $here = $ENV{LATEX_LIVE_HOME} || "$ENV{HOME}/.local/share/latex-live";

my $exec = $ENV{LATEX_LIVE_EXEC} // '';
my $build = $ENV{LATEX_LIVE_BUILD} || 'build';  # live.py --build-dir

$pdf_mode = 1;
# -halt-on-error: stop at the first error (fast failure, no cascade of follow-on errors).
# Touching build/%R.building first lets the viewer show "building..." until latex_report.py
# writes build/%R.status.json at the end of the run.
$pdflatex = "touch $build/%R.building; $exec pdflatex -synctex=1 -file-line-error -halt-on-error %O %S";
$bibtex = "$exec bibtex %O %S";
$out_dir = $build;
$view = 'none';     # live.py's viewer reloads itself when the PDF changes
$sleep_time = 1;    # watch poll interval (s)
$silent = 1;        # keep the terminal readable; full log is in build/<doc>.log

# Watch-mode hooks; latexmk expands %R to the root name. $warning_cmd fires
# instead of $success_cmd when refs/citations are undefined.
$success_cmd = "python3 '$here/latex_report.py' %R ok";
$warning_cmd = $success_cmd;
$failure_cmd = "python3 '$here/latex_report.py' %R fail";
