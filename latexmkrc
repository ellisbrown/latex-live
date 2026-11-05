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
# writes build/%R.status.json at the end of the run. After each successful pass, `--pass`
# publishes the PDF already, so the viewer need not wait for a second pass (bibtex, references).
# %P is the source with $pre_tex_code before it: once the preamble has run, it prints the page
# layout (live.py reads the text area and line spacing from the log, so the viewer needs no
# per-template constants).
$pre_tex_code = '\\def\\latexlivelayout{\\typeout{latex-live layout:'
    . ' \\the\\paperwidth,\\the\\paperheight,\\the\\hoffset,\\the\\oddsidemargin,\\the\\voffset,\\the\\topmargin,'
    . '\\the\\headheight,\\the\\headsep,\\the\\textwidth,\\the\\textheight,\\the\\columnwidth,\\the\\baselineskip}}'
    . '\\ifdefined\\AddToHook\\AddToHook{begindocument/end}{\\latexlivelayout}\\else\\AtBeginDocument{\\latexlivelayout}\\fi';
# With LATEX_LIVE_STANDBY_OWNER (live.py's pid; LATEX_LIVE_STANDBY=0 turns it off), each pass goes through
# latexlive/standby.py, which runs it in a warm standby (a pdflatex that has read the preamble already)
# when it can, else as before, with the same arguments and $exec (which it reads from the environment).
my $pass = $ENV{LATEX_LIVE_STANDBY_OWNER} ? "python3 -I '$here/latexlive/standby.py'" : $exec;
$pdflatex = "touch $build/%R.building; $pass pdflatex -synctex=1 -file-line-error -halt-on-error %O %P && python3 '$here/latex_report.py' --pass %R";
# LATEX_LIVE_TIMING=1 (live.py; latexlive/timing.py): note when each run and pass begins, when a pass's command
# returns and latexmk has read what it wrote, when the run ends, and when bibtex starts, in build/<job>.timing.
# The hooks need latexmk 4.80 or later; with an older one, only the other processes' notes are there.
my $timing = ($ENV{LATEX_LIVE_TIMING} // '') eq '1';
require Time::HiRes;
if ($timing && defined &add_hook) {
    for my $hook (['compile_begin', 'run'], ['before_xlatex', 'pass'], ['after_xlatex', 'pass-end'],
                  ['after_xlatex_analysis', 'analyzed'], ['compile_end', 'run-end']) {
        my $event = $hook->[1];
        add_hook($hook->[0], sub {
            if (open(my $f, '>>', "$build/$jobname.timing")) { printf $f "%.6f %s\n", Time::HiRes::time(), $event; close $f; }
            return 0;
        });
    }
}
# Before each pass of live.py's build: copy the files of the main build that the notes-free pass reads (SEED_EXTS in
# latex_report.py) as this pass is about to read them, and start latex_report.py --early beside the pass, which runs
# the notes-free pass from them; the end-of-run report keeps it if the run ends with those files (see EARLY there).
# (Detached, as latex_report.py starts the notes-free pass: latexmk doesn't wait for it.)
if ($ENV{LATEX_LIVE_DOC} && defined &add_hook) {
    require File::Copy;
    require POSIX;
    add_hook('before_xlatex', sub {
        my $doc = $ENV{LATEX_LIVE_DOC};
        my $began = int(Time::HiRes::time() * 1e6);
        my $seed = "seed-$began-$$";
        mkdir "$build/clean-early";
        mkdir "$build/clean-early/$seed" or return 0;
        for my $ext ('.aux', '.bbl', '.brf', '.out', '.toc') {
            File::Copy::copy("$build/$jobname$ext", "$build/clean-early/$seed/$doc-clean$ext") if -e "$build/$jobname$ext";
        }
        my $owner = $$;
        my $pid = fork();
        if (defined $pid && $pid == 0) {  # (the grandchild leads a process group of its own: stop_early signals it by pid)
            POSIX::_exit(0) if fork();
            POSIX::setsid();
            open(STDIN, '<', '/dev/null'); open(STDOUT, '>', '/dev/null'); open(STDERR, '>', '/dev/null');
            exec('python3', "$here/latex_report.py", '--early', $jobname, $doc, $seed, $began, $owner) or POSIX::_exit(1);
        }
        waitpid($pid, 0) if $pid;
        return 0;
    });
}
require File::Spec;  # (bibtex runs in the build folder: an absolute path)
$bibtex = ($timing ? "perl -MTime::HiRes=time -e 'printf qq(%.6f bibtex\\n), time' >> '" . File::Spec->rel2abs($build) . "/'%R.timing; " : '')
    . "$exec bibtex %O %S";
$out_dir = $build;
$view = 'none';     # live.py's viewer reloads itself when the PDF changes
$sleep_time = 0.25; # watch poll interval (s; latexmk 4.x takes fractions)
$silent = 1;        # keep the terminal readable; full log is in build/<doc>.log

# Watch-mode hooks; latexmk expands %R to the root name. $warning_cmd fires
# instead of $success_cmd when refs/citations are undefined.
$success_cmd = "python3 '$here/latex_report.py' %R ok";
$warning_cmd = $success_cmd;
$failure_cmd = "python3 '$here/latex_report.py' %R fail";
