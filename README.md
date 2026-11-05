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
  another root file, and every open viewer follows. The same menu lists the
  papers other instances on the machine preview and opens their viewers, all
  on one port; `/papers` lists them.
- **Live rebuild.** `latexmk -pvc` builds into `build/`. The PDF is published
  atomically, and only after a successful run. Errors appear as a clickable
  banner and as `file:line` diagnostics for the VS Code problem matcher. ↻ in
  the toolbar runs latexmk again from the top, e.g. after another build
  (`make`) replaced the PDF and SyncTeX no longer matched it.
- **SyncTeX both ways.** Double-click the PDF to open the source line in VS
  Code (`code -g`), or in the browser editor while that is open. `forward.py
  FILE LINE`, or Show in PDF in the browser editor, scrolls the viewer to a
  source line and highlights it.
- **Edit in the browser.** ✎ Source (or E) opens the LaTeX beside the PDF (in
  a shared preview, for editors and the owner). A file menu lists the paper's `.tex`, `.bib`, `.sty` and `.cls` files by
  folder, with a filter. Edits save by themselves a moment after you stop
  typing (Cmd/Ctrl+S saves at once), and the PDF rebuilds. If the file changed
  meanwhile (saved in another editor or tab, or a note from the viewer), your
  edits are merged with that change. Where both changed the same lines, the
  editor shows both versions between `<<<<<<<` and `>>>>>>>` lines and saves
  again once you have settled them; or you keep your version or the other
  whole. A file that changes on disk reloads in place. Edits that could not be
  saved yet stay in the browser and are offered back when you open the file
  again.
- **Notes on/off.** A second, notes-free build shows the paper as submitted.
  The notes-free view is the default. It puts each inline note in a margin
  bubble, wired to a caret in the text where the note sits. Hover a bubble to
  expand it; click it to open the source.
- **Comments from the viewer.** Pick who you are in the toolbar (one of the
  paper's note macros, e.g. `\ch`; in a shared preview, coauthors comment as
  themselves, so the menu shows only their own), select text in the PDF, and
  press 💬 (or C): your note goes into the source right after the selected
  words, and the rebuild shows it. Typed text is escaped, so `%`, `&` and `_`
  are safe. In their bubbles and in the sidebar, notes get ✓ (resolve),
  others' notes ↩ (reply: your note goes right after theirs), and your own ✎
  (edit). Resolving asks why and comments the note out instead of deleting it,
  on its own lines after the line that held it (a comment line doesn't break
  the paragraph):
  ```
  % RESOLVED 2026-10-19 (EB): defined the control in App. D.2.
  % \ch{The control is never defined ...}
  ```
  ✕ (delete) appears only on notes written from the same tab, as an undo.
  A note never lands inside a heading, another note, or text marked as
  protected (`LATEX_LIVE_PROTECTED`), and the note buttons can't change a
  protected marker (the browser editor edits any text). Characters pdflatex
  can't set (✓, →, emoji) are refused. If the file is open with unsaved
  changes in your editor, save it first: otherwise, saving later offers to
  overwrite the note. The browser editor merges the note in by itself.
- **Shared preview for coauthors.** A coauthor with no LaTeX setup of their
  own opens one link and gets the live preview: they read the PDF, comment as
  themselves (with their own note macro), and, if they are editors, edit the
  LaTeX in the browser. Each person has a role: viewer, commenter, editor or
  owner. Who they are comes from an authenticating reverse proxy, which names
  them in a header, or from a personal link. A badge in the toolbar shows each
  person their name and role. Coauthors see only what their role allows, on
  that one document; double-clicking the PDF opens the browser editor for
  editors and names the source line for others. The preview runs on a
  separate copy of the paper (a git worktree on its own branch), and can
  commit each person's edits to git as theirs, for you to merge. See
  [Shared mode](#shared-mode).
- **Page-limit badge.** Main-text pages against the venue limit, measured with
  and without notes. The limit comes from the venue style (ICLR, NeurIPS and
  COLM: 9 pages; ICML, CVPR and ICCV: 8) or `--page-limit`.
- **One or two columns.** LaTeX reports the column layout on each build. With
  two columns, the viewer reads the text column by column, ignores review line
  numbers beside either column, and measures page fill, space hints and note
  anchors per column. A hover preview shows the target's column (all of a
  `figure*`), and a note's bubble sits in a gutter beside its column, left of
  the page for the left column.
- **Change highlights (Δ, or `d`; off by default).** A word-level diff of the
  text against the last push (a PDF of the `.tex` files at the commit the
  branch's remote branch is at, from the remote-tracking branches, so no
  network; the default), the last commit (the same for `HEAD`, rebuilt when
  `HEAD` moves; the default when nothing was pushed), or the previous build,
  plus outlines on changed figures. The "vs" button switches the baseline; the
  toolbar arrows (or `,` and `.`) step through the changes.
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
- **Compare.** latexdiff of the working tree against any git commit (in the
  ⋯ menu).
- **Download** (the toolbar's download button; its menu opens on hover or
  click): the PDF as built, notes included; without notes; or with notes in
  the margin. The version without notes is an exact build, as you would
  submit it: its own bibtex run (a citation only in a note drops out) and as
  many passes as the references need, made once per build of the document.
  In a shared preview, every coauthor can download all three, viewers too.
- **Notes in the margin, for co-authors** (the download menu's "PDF with notes
  in the margin", saved as `<doc>-with-margin-notes.pdf`): the pages without
  notes, unchanged, on wider pages, with each note in a margin beside its
  column (left of the page for the left column of two). A note is typeset as
  the paper shows it (math, references, its author's label and color),
  numbered at its spot in the text and wired to it. In a crowded margin the
  notes keep their order and are set smaller; those that still don't fit
  continue on pages after the paper, under the same numbers, and the margin
  says where. Resolved notes, protected markers, and notes the document
  leaves out (in a conditional that is off, say) are left out. A note that
  can't be pinned to its exact spot (one between paragraphs, say) is placed by
  its source line instead; the viewer says how many were. A citation only in
  notes, which the bibliography leaves out, shows its key (with natbib, its
  authors and year). The pages' links don't work in this copy.
- **Welcome guide.** When a coauthor opens a shared preview, a short guide for
  their role explains the live PDF, commenting, editing, finding your way, the
  downloads, and where their changes go. Closed with "Don't show this again"
  checked (the default), it stays closed; "Welcome guide" in the ⋯ menu or in
  the `?` list reopens it.
- **Viewer comforts:**
  - a one-row toolbar: on a narrow window, the controls that don't fit move
    into the ⋯ menu, least used first;
  - search;
  - back/forward through links;
  - hover previews of reference targets;
  - dark mode (in the ⋯ menu);
  - `?` lists the keyboard shortcuts;
  - trackpad pinch zoom that zooms the PDF, not the page.

## Usage (current)

For a step-by-step setup (prerequisites, VS Code tasks, port forwarding,
troubleshooting), see [SETUP.md](SETUP.md). It also works as instructions for
a coding agent. From a paper root:

```bash
python3 live.py [doc] [--page-limit N] [--no-open] [--host-tex] [--build-dir DIR] [--replace]
```

- **Document:** a root `.tex` file, with or without the extension. Without one,
  latex-live picks the root file (one with a `\documentclass`) whose sources
  were edited last; on a tie, the last one it built. Switch in the viewer.
  Given a path instead, e.g. the editor's current file, it runs in that file's
  paper (its folder, or the nearest one above with a root file) and builds the
  file if it is a root one, so one editor task serves several papers.

- **One instance per document:** another one for the same document prints the
  running viewer's URL and exits; `--replace` stops the running one and takes
  over instead. An instance that no longer answers is replaced without asking.

- **Second instance:** `--build-dir build/next` (or any other directory) keeps
  every output there, including `<doc>.pdf`. A second instance, such as a
  development version, can then run beside the usual one without touching its
  files.

- **Requirements:** Python 3.9+ (standard library only), `latexmk` on the host,
  and either rootless podman with `docker.io/minidocks/texlive:2024-full`
  pulled, or a host TeX Live with `--host-tex`. Shared mode needs podman.
- **Viewer address:** one port for every preview on the machine, 44100
  (`--port`, `$LATEX_LIVE_PORT`, or the number in `~/.config/latex-live/port`): a paper's viewer is at `/p/<folder>-<hash>/<doc>/`, and `/`
  opens the only preview or lists them all. Each preview still runs in its
  own process (its own terminal, latexmk and version) on its own port from
  44101; whichever runs the hub forwards to the others, and another takes over
  within a second when it stops (once nothing else holds the port). Every request needs the token in `~/.config/latex-live/token`
  (`$LATEX_LIVE_STATE/token` if set), which is created on first run and
  shared by every checkout, as is the list of running instances.
- **Environment variables:**
  - `LATEX_LIVE_ANON_TERMS`: a regular expression of affiliations and other
    terms that identify the authors, for the anonymity check, e.g.
    `\bNYU\b|New York University`. Author names are found automatically
    (git `user.name` and the note-macro comments).
  - `LATEX_LIVE_PRIVATE_TERMS`: a regular expression of codenames and other
    internal names that must never appear in the arXiv source, e.g.
    `(?i)projectx|cluster-a`, for the arXiv source check.
  - `LATEX_LIVE_HOST`: also print a direct viewer URL on this hostname, e.g.
    the name a tunnel exposes (not in shared mode, which answers this machine
    only).
  - `LATEX_LIVE_EDITOR`: the editor command that double-click opens the
    source in while the browser editor is closed (`<editor> -g FILE:LINE`),
    e.g. `cursor`. Without it, latex-live uses `code`, and only from a VS Code
    terminal (elsewhere, the browser editor opens instead). On a Mac, Cursor's local
    terminals don't have the variable VS Code terminals set, so set this to
    the `cursor` symlink to the app (`/usr/local/bin/cursor`); Cursor's bundled
    `code` script doesn't reach the open window.
  - `LATEX_LIVE_PROTECTED`: a regular expression for a marker of text that must
    stay as written, e.g. `\\ch\{human-written\}` at the end of a paragraph
    or inside a caption. Notes written from the viewer (replies too) go after
    the paragraph or caption it marks, and the marker itself can't be edited,
    resolved, or deleted there. The browser editor doesn't guard it: whoever
    may edit can change it there like any other text.
  - `LATEX_LIVE_USERS`: a users file, which turns on
    [shared mode](#shared-mode) for this instance, e.g.
    `~/.config/latex-live-shared/users.json`.
  - `LATEX_LIVE_STATE`: the folder that holds the token and the list of
    running instances (default `~/.config/latex-live`). A shared instance
    needs one of its own, e.g. `~/.config/latex-live-shared`, and refuses the
    default: there it would share your previews' token.
  - `LATEX_LIVE_PUBLIC_URL`: in shared mode, the origin that coauthors'
    browsers use (scheme, host and port, no path), e.g.
    `https://paper.example.org`. Notes, saves and other actions are accepted
    only from pages there, and it is the link the instance prints. Without it,
    coauthors who come through a proxy can read but not comment or edit.
  - `LATEX_LIVE_IDENTITY_HEADER`: in shared mode, the header in which an
    authenticating reverse proxy names the signed-in user, e.g.
    `x-remote-user`. Without it, coauthors use personal links.
  - `LATEX_LIVE_PROXY_HOST`: the exact `Host` header that the authenticating
    proxy sends (`host` or `host:port`), e.g. `proxy.internal`. The identity
    header counts only on requests with this Host.
  - `LATEX_LIVE_PROXY_UID`: the user id the proxy runs as, e.g. `0`. The
    identity header then counts only on connections from that user's
    processes, so other users of the machine can't send it. Required with
    `LATEX_LIVE_IDENTITY_HEADER`, which works on Linux only (elsewhere this
    can't be checked, so the instance refuses to start; use personal links).
  - `LATEX_LIVE_COMMIT`: in shared mode, `1` commits each person's saves and
    notes to git as theirs (with the owner as committer) once they pause.
  - `LATEX_LIVE_COMMIT_IDLE`: the seconds of quiet after a person's last edit
    before it is committed (default 60), e.g. `30`.
  - `LATEX_LIVE_BUILD_LIMIT`: in shared mode, the most disk space the build
    folder may take, in MB (default 2048). Past it, the build is stopped and
    the build folder emptied, and the viewer says so.
- **Versions:** releases are tagged `v<VERSION>` (see `VERSION`; `live.py
  --version` prints it), with notes on the GitHub releases page. To pin one,
  check out its tag; to follow development, track `main`. After the checkout
  changes, a running preview keeps its version and the viewer shows "restart
  to update" until the task is restarted.
- **Contributing:** see [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow,
  how the pieces fit together, and the rules.
- **Smoke test:** `python3 check.py` (a minute or two, with TeX as above; its
  shared-mode part needs podman and the image) compiles the Python, checks the
  viewer's import map and parses its script (with Node), and runs an instance
  on a scratch two-column paper with git history: build, notes-free pass,
  endpoints, SyncTeX, both change baselines, the download without notes, the
  copy with notes in the margin, and taking over an instance. A shared-mode
  instance on a copy of the paper, beside it, then covers the confinement (a
  bibliography name that is a shell command runs nothing), the roles, the
  proxy's identity header, personal links, both downloads built on request,
  the browser editor's saves and merges, rebuilds that stop their containers,
  build state forged in `build/`, and the commits. Run it before pushing.
- **PDF.js:** vendored in `pdfjs/` (4.10.38 legacy build, Apache-2.0; see
  `pdfjs/LICENSE`).
- **CodeMirror:** vendored in `codemirror/` for the browser editor (CodeMirror
  6 and the packages it uses, as published on npm, MIT; see
  `codemirror/VERSION` and `codemirror/LICENSE`).

### Shared mode

With `LATEX_LIVE_USERS` set, an instance serves its paper to the people in a
users file, each with a role:

- **viewer:** reads the PDF, the notes it shows and the outline, and
  downloads the PDF: with its notes, without them, or with them in the
  margin;
- **commenter:** also writes notes, as their own note macro and in plain
  text; edits and deletes only their own notes, and replies to and resolves
  anyone's;
- **editor:** also edits the sources in the browser (notes too, as LaTeX),
  rebuilds (the coauthors together at most every 20 seconds), sees the
  pre-submission checks and the build's error messages, and compares with
  earlier commits of the copy's branch (the diff, and the change highlights
  against the last commit or push);
- **owner:** everything, as without shared mode.

Viewers and commenters see the current PDF and what it shows, nothing more:
the notes the PDF shows (not those in `\iffalse` or a switch that is off, nor
any while `\todotxt` hides them), whether the build failed and how many errors
it had (not the messages, which quote the sources), the figures without their
labels and source lines (and not the list of figure files, which marks changed
figures), forward search only to the notes they see, and SyncTeX's lines
without the names of files the editor doesn't open. The earlier versions (the baselines and the diff) are for
editors and the owner. The download with notes in the margin shows every note
outside a false branch, as it always did, even while `\todotxt` hides them in
the PDF.

Coauthors who may edit write the LaTeX that this machine compiles, so such an
instance treats the paper and everything TeX writes as theirs:

- It builds without latexmk. latexmk is a Perl program on the host that
  reads the files TeX writes, and it passes the bibliography names in the
  `.aux` to a shell, so a crafted name would run a command here. live.py
  itself watches the paper's files and runs pdflatex, and bibtex, biber or
  makeindex where the paper uses them, as latexmk would (up to six passes,
  until the references settle). A tool that failed runs again on every
  build until it succeeds, and its log's last lines are in the build's
  errors. It also watches the bibliographies and styles that the `.aux` or
  `.bcf` names (in any folder), and files a build missed or that an
  `\input` or `\include` names before they exist. Saves in a row make one
  build: one starts once the files have been quiet for a second (at most 10
  seconds after the first change), and never within 2 seconds of the last. Other tools that a paper's own `latexmkrc`
  would add (makeglossaries, say) don't run, and a `latexmkrc` in the paper
  is not read.
- Every TeX run is a container of its own from the TeX Live image, never the
  host's TeX (it refuses `--host-tex`, and a machine without podman): no
  network, no capabilities, no shell escape, at most 4 GB of memory, 2 CPUs,
  512 processes, files of 1 GB, and 10 minutes (or the caller's shorter
  deadline: a container that runs out of time is stopped before its slot is
  free again), and the build folder may grow to `LATEX_LIVE_BUILD_LIMIT`
  (checked every second, with `--no-build` too; past it, the build folder is
  emptied but for the instance's own lock file, and the build tried again a
  few times). The paper folder is mounted read-only and only its build folder
  writable, with its dot entries at any depth (`.git`, whose config may hold a
  remote's credentials, `.env`, `chapters/.private/`, ...) hidden; a paper
  with a dot entry whose name holds a newline, `:` or `,`, which can't be
  hidden, doesn't build. TeX opens files only below its folder and in the TeX
  tree. A rebuild stops the run in progress, its containers too, and so does
  stopping the instance. Builds and downloads share three container slots,
  and the live build has one of its own. The memory and CPU
  limits need cgroup v2 with those controllers delegated to your user (see
  [SETUP.md](SETUP.md#sharing-a-preview-with-coauthors)).
- What TeX writes in the build folder counts as written by the coauthors:
  the build's state, its errors and what it published stay in the server's
  memory, the PDF is copied and served without following links, and what the
  server reads there (the `.aux` files and the ones they name, file lists,
  SyncTeX, logs, JSON) it reads only within the build folder, without links,
  and only up to a bound. The
  downloads built on request (without notes, and with notes in the margin)
  and the change-highlight baselines build in the same containers, each at
  most once per build of the paper and one at a time, however many ask. They
  start only with a POST from the viewer's own page: nothing builds on a GET,
  and another site's page can't start a build.
- The pre-submission checks read the PDF with poppler's tools, which run on
  the host, so they are for editors and the owner, under time and memory
  limits.
- Editors compare only with commits that the copy's branch builds on: its
  history is part of what you share. Share a branch whose history you are
  happy to show (a squashed one, say).
- It serves exactly `--port` (give it one outside your own previews' ports),
  never the hub of your own previews, and answers connections from this
  machine only, at most 64 at a time: coauthors reach it through a reverse
  proxy or tunnel that runs here. Its pages can't be framed by another site.
- It needs its own state folder (`LATEX_LIVE_STATE`), and on Linux, with an
  identity header, `LATEX_LIVE_PROXY_UID`; it refuses to start without them.
  It prints the link for coauthors (`LATEX_LIVE_PUBLIC_URL`, with no token in
  it), then your own.

The users file is JSON, readable only by you (mode 0600). It is read again
whenever it changes, so adding someone needs no restart; while an edit leaves
it broken, the last good version stays.

```json
{
  "users": {
    "ada": {"name": "Ada Example", "email": "ada@example.org", "macro": "ad", "role": "owner"},
    "ben": {"name": "Ben Example", "email": "ben@example.org", "macro": "be", "role": "editor"},
    "cy": {"name": "Cy Example", "email": "cy@example.org", "macro": "cy", "role": "commenter", "token": "<personal-link secret>"},
    "dee": {"role": "viewer"}
  }
}
```

- **Key:** the name the proxy signs the person in as (up to 64 letters,
  digits, `.`, `_`, `@` and `-`), matched exactly.
- **`role`:** `viewer`, `commenter`, `editor` or `owner`; the only field
  required.
- **`macro`:** their note macro, without the backslash. It must be one of the
  paper's note macros; without one, they can read but not comment.
- **`name`, `email`:** how the viewer names them, and the author of their
  commits (`LATEX_LIVE_COMMIT`).
- **`token`:** the secret of a personal link, for someone who doesn't come
  through an authenticating proxy: 16 to 128 letters, digits, `-` and `_`,
  e.g. from Python's `secrets.token_urlsafe(18)`. Their link is the public URL
  followed by `/?t=` and the token.

[SETUP.md](SETUP.md#sharing-a-preview-with-coauthors) has the steps: the copy,
the users file, the proxy's settings, a service that keeps it running, and
merging the edits back.

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
- **Shared mode.**
  - **TeX:** it needs rootless podman and the TeX Live image, and never falls
    back to the host's TeX, so it doesn't run where there is no podman (e.g. a
    Mac with MacTeX only). Its containers' memory and CPU limits need cgroup
    v2 with those controllers delegated to your user.
  - **Proxy check:** `LATEX_LIVE_PROXY_UID` looks the connection's user up in
    Linux's `/proc/net`, so the identity header works on Linux only;
    elsewhere, coauthors use personal links.
  - **Host tools:** the pre-submission checks run poppler's tools on the host
    (the image has none), on a PDF that editors' LaTeX made: that is why they
    are for editors and the owner only, under limits.
  - **A killed server:** a server killed outright (`kill -9`) can't stop its
    containers; each still stops itself after 10 minutes.
  - **Editing together:** no real-time co-editing. Edits reach others when
    they are saved, a moment after typing stops; each save is merged with what
    others saved meanwhile, and edits to the same lines become a conflict to
    settle.
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
5. **Network modes.** Shared mode serves a paper to coauthors (see
   [Shared mode](#shared-mode)): each one signed in by a proxy or a personal
   link, with a role, on one fixed port that answers this machine only. Still
   to do:
   - **local** (the laptop default): localhost only, no token, and it opens
     the browser;
   - **remote**: the token plus a configurable port range, for tunnels that
     forward only a few ports.

   Editor links for VS Code, Cursor, or a custom command.
6. **Test papers.** Plain article, ICLR, NeurIPS, and a two-column template,
   to keep the layout heuristics honest.
7. **Static review snapshot** (optional). Coauthors who only use Overleaf can
   now use a shared preview, or the PDF with notes in the margin. Still to do:
   one self-contained HTML file (PDF, note bubbles, checks) for a reader who
   can't reach the machine.
