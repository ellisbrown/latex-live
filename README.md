# latex-live

Live LaTeX preview in the browser, in the spirit of LaTeX Workshop / Overleaf:
save a `.tex` file and the PDF rebuilds and reloads in place, keeping scroll
and zoom. Built on `latexmk -pvc`, SyncTeX, and PDF.js, with no editor extension.

## Status

This is the first implementation, written for one ICLR paper on a remote
Linux machine used through VS Code. It works day to day, but it still assumes
that setup and venue (see [Portability gaps](#portability-gaps)). The plan
below makes it a general tool.

## Features

- **Live rebuild.** `latexmk -pvc` builds into `build/`. The PDF is published
  atomically, and only after a successful run. Errors appear as a clickable
  banner and as `file:line` diagnostics for the VS Code problem matcher.
- **SyncTeX both ways.** Double-click the PDF to open the source line in VS
  Code (`code -g`). `forward.py FILE LINE` scrolls the viewer to a source line
  and highlights it.
- **Notes on/off.** A second, notes-free build shows the paper as submitted.
  The notes-free view is the default. It puts each inline note in a margin
  bubble, wired to a caret in the text where the note sits. Hover a bubble to
  expand it; click it to open the source.
- **Page-limit badge.** Main-text pages against the venue limit, measured with
  and without notes.
- **Change highlights.** A word-level diff of the text against the last
  commit (a PDF of `HEAD`'s `.tex` files, rebuilt when `HEAD` moves) or
  against the previous build, plus outlines on changed figures. The toolbar
  arrows (or `,` and `.`) step through them.
- **Space hints (✂).** Marks short last lines and large blank gaps.
- **Sidebar:**
  - an outline, with pages per section;
  - page thumbnails, with over-limit pages shaded;
  - notes grouped by author;
  - pre-submission checks: anonymity, draft switches, undefined references,
    placeholders, Type 3 fonts, image resolution, stale generated figures,
    term consistency, typos, and bib duplicates.
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
python3 live.py <doc> [--page-limit N] [--no-open] [--host-tex] [--build-dir DIR]
```

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
  - `LATEX_LIVE_HOST`: also print a direct viewer URL on this hostname, e.g.
    the name a tunnel exposes.
- **PDF.js:** vendored in `pdfjs/` (4.10.38 legacy build, Apache-2.0; see
  `pdfjs/LICENSE`).

## Portability gaps

- **Remote Linux machine.**
  - **Networking:** the token is always required, even on a laptop where only
    localhost can reach the port.
  - **TeX:** podman is assumed; `--host-tex` uses whatever TeX is on `PATH`,
    whose version may differ from Overleaf's.
  - **Linux only:** the child process gets its parent-death signal through
    `libc.so.6` (`prctl`), which fails on macOS.
- **ICLR and this paper.**
  - **Page layout:** 1-inch margins (`MARGIN = 72`), the last-baseline bound,
    and 11 pt line spacing (`LEADING = 11`).
  - **Venue rules:** the page-limit rules (references and unnumbered
    statements don't count) and `\iclrfinalcopy`.
  - **Anonymity terms:** set by hand (`LATEX_LIVE_ANON_TERMS`).
  - **Notes:** note macros are found as one-argument commands that go through
    `\todotxt`, and the notes-free build works by emptying `\todotxt`.
  - **Generated figures:** the staleness check assumes they come from
    `raw/*.py`.
- **Single column only.** The viewer groups text into lines by baseline across
  the whole page. In two-column templates (ICML, CVPR, ACL), both columns merge
  into one line. That breaks the change highlights, space hints, margin-note
  anchors and page-fill measure.

## Plan

1. **Package.** An installable CLI (`pipx` / `uv tool install`):
   - `latex-live [doc]` to run it;
   - `latex-live forward FILE LINE` for forward search;
   - `latex-live init` to write the editor task and keybinding into ignored
     files.
2. **macOS (TODO).**
   - Replace the Linux-only parent-death call (`prctl` through `libc.so.6`).
   - Use MacTeX on the host when podman isn't set up.
   - Default to the local network mode (below) on a laptop.
3. **Detect, then configure.** Automatic detection, with an optional
   `.latex-live.toml` for overrides:
   - **main document:** found automatically;
   - **TeX:** a recent host TeX Live, else Docker, else podman;
   - **text area:** measured from SyncTeX, or printed by LaTeX through the same
     command-line hook the notes-free build uses;
   - **note macros:** detected, with notes-off emptying each one;
   - **column count:** from where text sits across the page;
   - **overrides:** anonymity terms, generated-figure paths, page limit.
4. **Venue presets.** ICLR, NeurIPS, ICML, CVPR, ACL: page limit, what
   doesn't count, anonymity switch, number of columns.
5. **Two network modes.**
   - **local** (the laptop default): localhost only, no token, and it opens
     the browser;
   - **remote**: the token plus a configurable port range, for tunnels that
     forward only a few ports.

   Editor links for VS Code, Cursor, or a custom command.
6. **Two-column support** throughout the viewer.
7. **Test papers.** Plain article, ICLR, NeurIPS, and a two-column template,
   to keep the layout heuristics honest.
8. **Static review snapshot** (optional). One self-contained HTML file (PDF,
   note bubbles, checks) for coauthors who only use Overleaf.
