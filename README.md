# latex-live

A live LaTeX preview in the browser, in the spirit of LaTeX Workshop and
Overleaf: save a `.tex` file and the PDF rebuilds and reloads in place,
keeping scroll and zoom. It runs where the paper is, on your own machine or a
remote Linux machine you reach through VS Code Remote-SSH, and serves every
preview on one port. It is built on `latexmk -pvc`, SyncTeX and PDF.js, builds
in a TeX Live 2024 image close to Overleaf's, and needs no editor extension and
nothing beyond Python's standard library.

## Features

- Rebuild on save, with errors as a clickable banner and in VS Code's Problems panel.
- SyncTeX both ways: double-click the PDF to open the source line, and a key
  in VS Code highlights the current line in the PDF.
- Notes: a notes-free view with each note in a margin bubble, and notes
  written, replied to, resolved and edited from the viewer.
- An editor in the browser, beside the PDF, that merges with changes saved elsewhere.
- Change highlights against the last push, commit or build, and latexdiff against any commit.
- A page-limit badge, space hints, and pre-submission checks: anonymity,
  references, fonts, figure placement, what the arXiv source would expose, and more.
- Downloads: as built, without notes (an exact build), or with the notes in the margin.
- Shared mode: coauthors without a LaTeX setup open one link and read,
  comment or edit, each in their role, with every TeX run confined to a container.

## Quick start

On Linux, with Python 3.9+, latexmk, poppler-utils, git and rootless podman:

```bash
git clone https://github.com/ellisbrown/latex-live ~/.local/share/latex-live
podman pull docker.io/minidocks/texlive:2024-full    # about 4.6 GB, once
cd <paper> && python3 ~/.local/share/latex-live/live.py --no-open
```

Open the `http://localhost:44100/p/...?t=...` URL that it prints (on a remote
machine, forward port 44100 first). [docs/setup.md](docs/setup.md) has the
full steps, for a person or a coding agent: prerequisites (macOS too), the VS
Code task that starts the preview when you open the paper, port forwarding,
and troubleshooting.

## Documentation

- [Setup](docs/setup.md): installing, the VS Code tasks, ports and the token,
  options and environment variables, updating, keeping the TeX image, and
  [troubleshooting](docs/setup.md#troubleshooting).
- [The viewer](docs/viewer.md): the toolbar and ⋯ menu, notes and the
  notes-free view, the source pane, change highlights and comparing, downloads
  and the review copy, the sidebar, page limits and checks, and keyboard
  shortcuts.
- [Shared mode](docs/shared-mode.md): roles and what each sees, setting it up
  behind a reverse proxy or with personal links, the security model, and
  running it as a service.
- [Contributing](CONTRIBUTING.md): the workflow, how the pieces fit together,
  and the rules for changing latex-live.

## Status

latex-live is at 0.x (see `VERSION`): no stability promise yet. Releases are
tagged `v<VERSION>`; track `main` to follow development. It is used on Linux;
macOS with MacTeX should work but has not been tried. Known limits:

- Notes are the paper's note macros that go through `\todotxt`.
- Page limits are built in for ICLR, NeurIPS, COLM, ICML, CVPR and ICCV;
  pass `--page-limit` for other venues.
- The token is required even on a laptop, where only localhost reaches the port.

Planned work is in the [issues](https://github.com/ellisbrown/latex-live/issues).

PDF.js (Apache-2.0, in `pdfjs/`) and CodeMirror 6 (MIT, in `codemirror/`) are
vendored as published; see their `LICENSE` and `VERSION` files.
