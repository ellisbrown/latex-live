"""latex-live: LaTeX Workshop-style live preview without the extension.

- Without <doc>, it builds the root .tex file (one with a \\documentclass) whose sources
  were edited last; the viewer switches to another root file.
- `latexmk -pvc` rebuilds into build/ whenever any input file changes (latexmkrc),
  as job <doc>-live so stale <doc>.aux/.bbl from in-place builds are never read.
  Each pdflatex pass takes a warm standby when it can: a pdflatex that has read the
  preamble already and waits, so a save waits only for the body (standby); so does
  each TeX run of the pass without notes (latex_report.py).
- pdflatex/bibtex run in the TeX Live 2024 image (close to Overleaf) via one
  short-lived rootless podman container per call (~0.2 s each); --host-tex
  uses the host's TeX installation instead. latexmk itself stays on the host.
- latex_report.py publishes <doc>.pdf after successful runs only and prints
  file:line diagnostics (picked up by the VS Code task's problem matcher).
- <doc>.pdf opens in a VS Code editor tab (via `code`, when run from a VS Code
  terminal); the vscode-pdf extension (mathematic.vscode-pdf) reloads it on each
  publish, staying on the same page.
- A PDF.js viewer (viewer.html) is also served: it reloads in place keeping exact
  scroll and zoom, and double-clicking the PDF jumps VS Code to the matching
  source line (SyncTeX inverse search, via `code -g`); forward.py does the
  reverse (source line -> highlighted spot in the viewer). The viewer also shows
  build status (click an error to open it), a page-limit check (also without
  the inline notes, from a background build), search, a numbered outline, page
  thumbnails, the notes, the figures and tables, and a pre-submission check
  (including stale generated figures, consistency lints, and what the arXiv
  source would expose) in a sidebar, hover previews of references, highlights
  of what changed since the last build (text and figures), hints on where to
  save space, a notes-free view, and a latexdiff
  against any git commit. Notes can be written from the viewer too: select text,
  and the note macro of the author picked in the toolbar goes into the source
  after it (also edit, resolve, reply, and delete). On a remote machine, forward the port (e.g. in VS Code's Ports panel)
  and open the localhost URL; with LATEX_LIVE_HOST set, a direct URL on that host
  is printed too (e.g. a name a tunnel exposes). Anything that reaches the port
  can send requests, so every request needs the token (in the state folder;
  ?t=TOKEN once, then a SameSite=Strict cookie).
- One port for every preview (the hub, 44100): each instance runs on its own
  port from 44101, and the one that holds the hub's lock forwards
  /p/<paper>/<doc>/ to the right one; / lists them.
- One instance per document: a second one (e.g. another VS Code window opening
  the same folder) prints the running viewer's URL and exits, or with --replace
  stops the first and runs instead; one that no longer answers is replaced. /papers
  lists every running instance on the machine.
- Shared mode (LATEX_LIVE_USERS=users.json): coauthors open this instance through an
  authenticating reverse proxy on this machine, or with personal links, each with
  the role the users file gives them (viewer, commenter, editor, owner). It serves
  exactly --port, to this machine only, and stays out of the hub. It builds without
  latexmk (which parses what TeX writes on the host): live.py runs pdflatex, bibtex,
  biber and makeindex itself, each in a confined container of the TeX Live image.
- The LaTeX can also be edited in the viewer, beside the PDF (the browser editor): a save
  is merged with what was saved to the file meanwhile (git merge-file), and in shared mode
  with LATEX_LIVE_COMMIT=1 each coauthor's edits and notes are committed to git as theirs.

The server is this package, which live.py starts (main). Its modules, from the bottom up: config
(settings), standby (the warm standbys of the live build and of its notes-free pass, which latexmkrc runs as a
script and latex_report.py loads), auth and
roles (who is asking, and what each role may do), files, tools, git and latex (the
paper's files and the build's, running TeX tools, git, reading LaTeX), synctex and document (the
document as built), notes, sources, note_writer, commits and editor (reading and writing the
sources), navigation, instances, build, baselines, status and timing (LATEX_LIVE_TIMING=1: each build's phases), shared_watch and shared_build (shared
mode's builder), export, review_anchors, review_layout and review (the downloads), lints, arxiv and
checks (the pre-submission checks), and web, hub, handler and main (serving it all).
"""

DESCRIPTION = __doc__.splitlines()[0]  # (live.py --help)
