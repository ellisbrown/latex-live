# latex-live

Live LaTeX preview in the browser, in the spirit of LaTeX Workshop / Overleaf:
save a `.tex` file and the PDF rebuilds and reloads in place, keeping scroll
and zoom. Built on `latexmk -pvc`, SyncTeX, and PDF.js, with no editor extension.

## Status

This is the first implementation, written for an ICLR paper and its arXiv
version on a remote Linux machine used through VS Code. It also runs on macOS
with MacTeX (not yet tried on a Mac). It reads the page layout from LaTeX, so
it is not tied to one template, but it still assumes that setup (see
[Portability gaps](#portability-gaps)).
The plan below makes it a general tool.

## Features

- **Main document.** With no document named, latex-live builds the root
  `.tex` file whose sources you edited last. A menu in the toolbar switches to
  another root file, and every open viewer follows.
- **Live rebuild.** `latexmk -pvc` builds into `build/`. The PDF is published
  atomically, and only after a successful run. Errors appear as a clickable
  banner and as `file:line` diagnostics for the VS Code problem matcher. ↻ in
  the toolbar runs latexmk again from the top, e.g. after another build
  (`make`) replaced the PDF and SyncTeX no longer matched it.
- **SyncTeX both ways.** Double-click the PDF to open the source line in VS
  Code (`code -g`). `forward.py FILE LINE` scrolls the viewer to a source line
  and highlights it.
- **Notes on/off.** A second, notes-free build shows the paper as submitted.
  The notes-free view is the default. It puts each inline note in a margin
  bubble, wired to a caret in the text where the note sits. Hover a bubble to
  expand it; click it to open the source.
- **Comments from the viewer.** Pick who you are in the toolbar (one of the
  paper's note macros, e.g. `\ch`), select text in the PDF, and press 💬 (or
  C): your note goes into the source right after the selected words, and the
  rebuild shows it. Typed text is escaped, so `%`, `&` and `_` are safe. In
  their bubbles and in the sidebar, notes get ✓ (resolve), others' notes ↩
  (reply: your note goes right after theirs), and your own ✎ (edit). Resolving
  asks why and comments the note out instead of deleting it, on its own lines
  after the line that held it (a comment line doesn't break the paragraph):
  ```
  % RESOLVED 2026-10-19 (EB): defined the control in App. D.2.
  % \ch{The control is never defined ...}
  ```
  ✕ (delete) appears only on notes written from the same tab, as an undo.
  A note never lands inside a heading, another note, or text marked as
  protected (`LATEX_LIVE_PROTECTED`), and a protected marker can't be changed
  from the viewer. Characters pdflatex can't set (✓, →, emoji) are refused.
  If the file is open with unsaved changes in your editor, save it first:
  otherwise, saving later offers to overwrite the note.
- **Page-limit badge.** Main-text pages against the venue limit, measured with
  and without notes. The limit comes from the venue style (ICLR, NeurIPS and
  COLM: 9 pages; ICML, CVPR and ICCV: 8) or `--page-limit`.
- **One or two columns.** LaTeX reports the column layout on each build. With
  two columns, the viewer reads the text column by column, ignores review line
  numbers beside either column, and measures page fill, space hints and note
  anchors per column. A hover preview shows the target's column (all of a
  `figure*`), and a note's bubble sits in a gutter beside its column, left of
  the page for the left column.
- **Change highlights.** A word-level diff of the text against the last
  commit (a PDF of `HEAD`'s `.tex` files, rebuilt when `HEAD` moves) or
  against the previous build, plus outlines on changed figures. The toolbar
  arrows (or `,` and `.`) step through them.
- **Space hints (✂).** Marks short last lines and large blank gaps.
- **Sidebar:**
  - an outline, numbered as in the paper (appendix letters too), with pages per
    section;
  - every figure and table, cropped from its page, with its caption; click
    one to go to it;
  - page thumbnails, with over-limit pages shaded;
  - notes grouped by author;
  - pre-submission checks: anonymity, draft switches, undefined references,
    placeholders, Type 3 fonts, image resolution, figure and table placement
    (off the text area, pages from their first reference, never referenced),
    what the arXiv source would expose, stale generated figures, term
    consistency, typos, and bib duplicates.
- **arXiv source check.** arXiv publishes the source, so the checks also read
  the paper root's arXiv package (the newest `.tar.gz`, `.tgz` or `.zip` file,
  or folder with a root `.tex`, with `arxiv` in its name) and list what it
  would expose: `%` comments in `.tex` files, notes (hidden ones too),
  `\iffalse` blocks and `comment` environments, private terms
  (`LATEX_LIVE_PRIVATE_TERMS`), personal paths (also in figure PDFs), files
  the build doesn't read, files over 10 MB, leftover build files, and a
  missing `.bbl`. Private terms, personal paths and a missing `.bbl` fail the
  check; the rest warn. It says when the package is older than the sources.
  Without a package it reads the files the build reads, i.e. what uploading
  the sources as they are would expose, and only informs (private terms and
  paths warn). A finding in a package file opens the project source when that
  file is the same, or its line is found there (e.g. in a package that inlines
  every `\input`).
- **Compare.** latexdiff of the working tree against any git commit.
- **Viewer comforts:**
  - search;
  - back/forward through links;
  - hover previews of reference targets;
  - dark mode;
  - trackpad pinch zoom that zooms the PDF, not the page.

## Usage (current)

For a step-by-step setup (prerequisites, VS Code tasks, port forwarding,
troubleshooting), see [SETUP.md](SETUP.md). It also works as instructions for
a coding agent. From a paper root:

```bash
python3 live.py [doc] [--page-limit N] [--no-open] [--host-tex] [--build-dir DIR]
```

- **Document:** a root `.tex` file, with or without the extension. Without one,
  latex-live picks the root file (one with a `\documentclass`) whose sources
  were edited last; on a tie, the last one it built. Switch in the viewer.
  Given a path instead, e.g. the editor's current file, it runs in that file's
  paper (its folder, or the nearest one above with a root file) and builds the
  file if it is a root one, so one editor task serves several papers.

- **Second instance:** `--build-dir build/next` (or any other directory) keeps
  every output there, including `<doc>.pdf`. A second instance, such as a
  development version, can then run beside the usual one without touching its
  files.

- **Requirements:** Python 3.9+ (standard library only), `latexmk` on the host,
  and either rootless podman with `docker.io/minidocks/texlive:2024-full`
  pulled, or a host TeX Live with `--host-tex`.
- **Viewer address:** the first free port from 44100 (`--port` sets the
  start). Every request needs the token in `./token`, which is created on
  first run and gitignored.
- **Environment variables:**
  - `LATEX_LIVE_ANON_TERMS`: a regular expression of affiliations and other
    terms that identify the authors, for the anonymity check, e.g.
    `\bNYU\b|New York University`. Author names are found automatically
    (git `user.name` and the note-macro comments).
  - `LATEX_LIVE_PRIVATE_TERMS`: a regular expression of codenames and other
    internal names that must never appear in the arXiv source, e.g.
    `(?i)projectx|cluster-a`, for the arXiv source check.
  - `LATEX_LIVE_HOST`: also print a direct viewer URL on this hostname, e.g.
    the name a tunnel exposes.
  - `LATEX_LIVE_EDITOR`: the editor command that double-click opens the
    source in (`<editor> -g FILE:LINE`), e.g. `cursor`. Without it, latex-live
    uses `code`, and only from a VS Code terminal. On a Mac, Cursor's local
    terminals don't have the variable VS Code terminals set, so set this to
    the `cursor` symlink to the app (`/usr/local/bin/cursor`); Cursor's bundled
    `code` script doesn't reach the open window.
  - `LATEX_LIVE_PROTECTED`: a regular expression for a marker of text that must
    stay as written, e.g. `\\ch\{human-written\}` at the end of a paragraph
    or inside a caption. Notes written from the viewer (replies too) go after
    the paragraph or caption it marks, and the marker itself can't be edited,
    resolved, or deleted there.
- **PDF.js:** vendored in `pdfjs/` (4.10.38 legacy build, Apache-2.0; see
  `pdfjs/LICENSE`).

## Portability gaps

- **Remote machine.**
  - **Networking:** the token is always required, even on a laptop where only
    localhost can reach the port.
  - **TeX:** the TeX Live 2024 image through podman, else (`--host-tex`, or no
    podman) whatever TeX is on `PATH`, whose version may differ from
    Overleaf's.
  - **Stopping:** on Linux, latexmk stops when the server dies, however it
    dies. On macOS it stops with the server's signal handlers, so a killed
    (`kill -9`) server leaves it running.
- **ICLR and this paper.**
  - **Venue rules:** references and unnumbered statements (ethics,
    reproducibility, acknowledgments) don't count toward the page limit. Page
    limits are known for six styles only. The anonymity check runs when page
    1 says "Anonymous", as blind-review styles print it.
  - **Anonymity terms:** set by hand (`LATEX_LIVE_ANON_TERMS`).
  - **Notes:** note macros are found as one-argument commands that go through
    `\todotxt`, and the notes-free build works by emptying `\todotxt`. Notes
    written from the viewer go after the token where the selection ends, found
    by matching the selected words near SyncTeX's line; words it can't find in
    the source (math, references) are dropped from the end of the selection.
  - **Generated figures:** the staleness check assumes they come from
    `raw/*.py`.
  - **Figure placement:** references are the commands ending in `ref`
    (`\ref`, `\cref`, `\autoref`, macros like `\figref`) and `\hyperref[...]`.
    Pages that hold only floats don't count toward a float's distance from its
    first reference, and floats in the back matter count only references from
    there. Rotated (`sideways`) floats aren't checked against the text area.

## Plan

1. **Package.** An installable CLI (`pipx` / `uv tool install`):
   - `latex-live [doc]` to run it;
   - `latex-live forward FILE LINE` for forward search;
   - `latex-live init` to write the editor task and keybinding into ignored
     files.
2. **macOS.** Runs with MacTeX on the host when there is no podman; not yet
   tried on a Mac. Still to do: default to the local network mode (below) on a
   laptop.
3. **Detect, then configure.** Automatic detection, with an optional
   `.latex-live.toml` for overrides. The main document and the text area are
   detected already (the text area is printed by LaTeX on each build). Still to
   do:
   - **TeX:** a recent host TeX Live, else Docker, else podman;
   - **note macros:** detected, with notes-off emptying each one;
   - **column count:** from where text sits across the page;
   - **overrides:** anonymity terms, generated-figure paths, page limit.
4. **Venue presets.** ICLR, NeurIPS, COLM, ICML, CVPR and ICCV page limits
   exist (submission limits; pass `--page-limit` for a camera-ready extra
   page). Still to do: ACL, and per venue what doesn't count.
5. **Two network modes.**
   - **local** (the laptop default): localhost only, no token, and it opens
     the browser;
   - **remote**: the token plus a configurable port range, for tunnels that
     forward only a few ports.

   Editor links for VS Code, Cursor, or a custom command.
6. **Test papers.** Plain article, ICLR, NeurIPS, and a two-column template,
   to keep the layout heuristics honest.
7. **Static review snapshot** (optional). One self-contained HTML file (PDF,
   note bubbles, checks) for coauthors who only use Overleaf.
