# Contributing to latex-live

For people and coding agents changing latex-live. The [README](README.md)
describes what it does and indexes the user docs in [docs/](docs/):
[setup](docs/setup.md), [the viewer](docs/viewer.md) and
[shared mode](docs/shared-mode.md).

## Workflow

- `main` stays usable: people run their previews straight from a checkout of
  it. Work on a branch, in its own worktree
  (`git worktree add ../latex-live-<topic> -b <topic> origin/main`), and
  remove the worktree once the branch is merged.
- One pull request per change. Before you open it and again before you merge,
  run `python3 check.py`. It takes about a minute and needs TeX, as `live.py`
  does, and podman with the TeX Live image for its shared-mode part. When you
  change behavior that it doesn't cover, extend it.
- Merge with a rebase (`gh pr merge <n> --rebase`), which keeps the history
  linear. Then delete the branch, after checking that no open pull request is
  based on it.
- Pushing to `main` updates everyone who tracks it. A running preview keeps its
  version, and its viewer shows "restart to update" until its task restarts.

## Versions and releases

- `VERSION` holds the version (0.x for now: no stability promise).
  `live.py --version` prints it, and the viewer's ⋯ menu shows it.
- To release, bump `VERSION` in its own commit on `main`, then run
  `git tag v<VERSION> && git push origin v<VERSION>` and
  `gh release create v<VERSION> --generate-notes`. Edit the notes into a few
  user-facing lines. Release when a batch of changes is worth announcing, not
  for every merge.
- Bump the minor version for new features or changed behavior, and the patch
  version for fixes. People pin a version by checking out its tag.

## How it fits together

| File | Role |
|---|---|
| `live.py` | The entry point, run from a paper root, by its path or through a link to the checkout: it puts `latexlive/` (next to its real path) on Python's path and runs `latexlive.main`. |
| `latexlive/` | The server, a package of modules by responsibility; its `__init__` says what latex-live does and lists them from the bottom up, and each imports only from those below it. It runs `latexmk -pvc` (`build`), whose pdflatex passes take a warm standby when they can (`standby`: a pdflatex that has read the preamble and waits; latexmkrc runs it as a script, so it imports nothing from the package, and its docstring explains how it stays exact), and serves the viewer, the PDFs, and JSON endpoints (`handler`, on `web.BaseHandler`): who the user is (`/me`), status (`status`), outline, figures and graphics (`document`), notes (`notes`), checks (`checks`, `lints`, `arxiv`), SyncTeX (`synctex`, `navigation`), diffs and the change baselines (`baselines`), and the browser editor's files (`/files`, `/file`: `editor`). It also writes notes into the sources (`POST /note`: `note_writer`), saves the browser editor's files, merged with `git merge-file` when they changed meanwhile (`POST /file`; every write goes through `sources`), rebuilds (`POST /rebuild`), and builds the downloads without notes (`/export`: `export`, an exact build in `build/export/<doc>/`) and with notes in the margin (`/review`: `review`, `review_layout`, `review_anchors`; in `build/review/<doc>/`: the export's pages placed on wider ones, with the notes typeset where a probe pass of the export records them). TeX tools, SyncTeX and poppler run through `tools`, each in a slot and under a deadline. One instance per document, with a lock in `build/<doc>-live.lock`; running instances list themselves in `~/.config/latex-live/instances/` (`instances`), beside the token. Each instance has its own port (from the hub's port + 1, 44101); the one holding `hub.lock` also serves the hub on 44100 (or `$LATEX_LIVE_PORT`, or the number in `~/.config/latex-live/port`: `config.default_port`), which forwards `/p/<paper>/<doc>/…` to the right instance (`hub.HubHandler`), so users forward one port. With `LATEX_LIVE_USERS`, an instance runs in shared mode instead: users and roles from a users file (`auth`: `BaseHandler.authorize`, then `Handler.permitted`, with the roles in `roles.least_role`), an `Origin` check on every POST (`Handler.origin_allowed`), the only way to act or to start a build there (the downloads and baselines too), exactly `--port`, loopback peers only (`web.LoopbackServer`), no hub, and commits as each user (`commits.commit_loop`). It builds without latexmk (`shared_watch`, `shared_build`): pdflatex, bibtex, biber and makeindex as argument lists, each in its own confined container (`tools.container_argv`, through `tex-exec`), stopped by a rebuild or a stop (`tools.stop_containers`); the build's state lives in `server.shared`, never read back from `build/`. Settings are in `config`, read as `config.NAME` at the time of use (never `from .config import`): `main` sets `BUILD` and `PUBLISH` after the import, and `check.py` changes some to test with. |
| `latexmkrc` | The latexmk configuration: builds into `build/` as job `<doc>-live`, prints the page layout into the log, runs each pdflatex pass through `latexlive/standby.py` when live.py asks for standbys (`LATEX_LIVE_STANDBY_OWNER`), and runs `latex_report.py` after each pass and run. |
| `latex_report.py` | The post-build hook. It publishes `<doc>.pdf` atomically, only from successful passes (each pass, so a second one doesn't hold up the first), writes `status.json` and `file:line` diagnostics, and starts the background notes-free pass (`build/<doc>-clean.pdf`). |
| `tex-exec` | Runs a TeX tool in the TeX Live image (rootless podman), or on the host while the image is missing. In shared mode, only in the image (`LATEX_LIVE_REQUIRE_IMAGE`), confined (`LATEX_LIVE_SANDBOX`: the paper read-only and only the build folder writable, resource limits, labels that let the server stop the container). A warm standby's container gets labels and a deadline too, and never falls back to the host's TeX (`LATEX_LIVE_STANDBY_LABEL`). |
| `forward.py` | Forward search: maps a source line to a spot in the PDF (`latexlive.synctex`) and writes `build/<job>.forward.json` for the viewer. |
| `viewer.html` | The viewer's page: its elements, the import map, and the module script `viewer/main.js` (PDF.js from `pdfjs/`, and the browser editor's CodeMirror from `codemirror/`). |
| `viewer/` | The viewer's style (`viewer.css`) and script: plain ES modules, one for each part of the viewer, each saying at its top what it is for. `main.js` imports the rest and starts; `page.js`, `net.js`, `role.js` and `pdfview.js` are what the others stand on, and `poll.js` polls `/status` four times a second (twice for a coauthor in shared mode; the owner's every 50 ms while a new PDF is on its way) and loads each new PDF. Each module imports only from modules below it (no cycles), and only the module that declares a binding assigns it: the others call a function of its (`setTab`, `showDocument`, …). Module paths are relative to the importing module, `fetch` URLs relative to the page (so both work under the hub's `/p/<paper>/<doc>/`). |
| `codemirror/` | CodeMirror 6 for the browser editor: each package's published ES module, unmodified, which the viewer loads through the import map in `viewer.html`. `codemirror/VERSION` lists the packages and versions. Update it as `pdfjs/`, by replacing the files with newer published ones (`npm pack`), and keep the import map in step (`check.py` checks it). |
| `check.py` | The smoke test: the Python compiles, the viewer's import map and modules are valid, an instance on a scratch paper works behind the hub, and so does a shared-mode instance on a copy of it, for users of every role. It patches a function where the code that calls it looks it up (e.g. `export.run_tool`). |

`viewer.html` and `viewer/` are served from the checkout on each request (the
browser revalidates them), so a viewer change shows on reload.
`latex_report.py` and `forward.py` run anew each time. Changes to `live.py`
and `latexlive/` take effect only once a preview restarts; its viewer then
shows "restart to update".

## Rules

- **Python's standard library only**, and no build step: the viewer's modules
  load in the browser as they are, and PDF.js and CodeMirror stay vendored.
- **Start latexmk only from the main thread.** Its parent-death signal fires
  when the thread that started it exits. Request handlers hand such work to the
  main loop (`on_main_thread`).
- **Never print the token**, a URL containing `?t=`, or a personal link's
  token from a users file. Every request needs one, and anything that reaches
  the port can send requests.
- **Keep coauthors to their role in shared mode.** An endpoint is the owner's
  until `least_role` grants it to a lesser role, every POST goes through the
  `Origin` check, and nothing acts or starts a build on a GET there. TeX tools
  run only in the image, through `tex-exec` with `LATEX_LIVE_SANDBOX`, each
  run in a `SPAWNS` slot (the live build: `LIVE_SPAWN`, its own) and under a
  timeout, with `build/` watched for size (`watch_build_size`), and no host program parses what
  TeX wrote as commands (so no latexmk there). Treat every file in `build/` as
  written by a coauthor. Extend `check.py`'s role checks with each new
  endpoint.
- **Test on scratch papers** in a temporary directory, as `check.py` does.
  Never edit a real paper's sources, and never commit a build of one.
  `check.py`'s shared-mode checks run in a thread of their own, beside the
  others (`shared_checks`): give each side its own instances, and wait for a
  condition (`wait_for`) rather than for a fixed time where you can.
- **Leave other people's previews running.** Before you kill a process, check
  its command line and working directory. Match processes with anchored
  patterns: `pgrep -f "^python3 /path/to/live.py"`. A bare `pgrep -f live.py`
  also matches the shell that runs it.
- **Keep it general.** No employer-, cluster-, or paper-specific names, paths,
  or hostnames in the code or docs. Site-specific settings go in environment
  variables (`LATEX_LIVE_*`), which are set in each user's own VS Code tasks
  (or, for a shared instance, its service).
- **Python 3.9+**, which is what macOS ships. Compile with `python3 -W error`
  (`check.py` does): an invalid escape in a docstring is a warning today and
  an error in a later Python.

## Style

- Write comments and docstrings in full sentences that say what something is
  for, not how it works line by line. A parenthetical comment is fine for a
  short reason, e.g. `# (fonts are not looked up through TEXINPUTS)`.
- Match the surrounding code's naming and idiom. Prefer a small function in
  the file that uses it over a new module.
- In the README and `docs/`, describe what a user sees, not how it is
  implemented. Keep the README short; details go in the doc they belong to.
  When you change behavior, update the doc that describes it.
- Commit subjects are imperative sentences that say what changed, e.g. "Keep
  the toolbar to one row". Use the body for why, and for anything a reviewer
  should check.
