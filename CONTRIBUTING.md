# Contributing to latex-live

For people and coding agents changing latex-live. The [README](README.md)
describes what it does and indexes the user docs in [docs/](docs/):
[setup](docs/setup.md), [the viewer](docs/viewer.md),
[agent plugins](docs/agent-plugins.md) and [shared mode](docs/shared-mode.md).

## Workflow

- `main` stays usable: people run their previews straight from a checkout of
  it. Work on a branch, in its own worktree
  (`git worktree add ../latex-live-<topic> -b <topic> origin/main`), and
  remove the worktree once the branch is merged.
- One pull request per change. Before you open it and again before you merge,
  run `python3 check.py`. It takes about two minutes and needs TeX, as `live.py`
  does, and podman with the TeX Live image for its shared-mode part. When you
  change behavior that it doesn't cover, extend it. While you work, it can
  run only some of its parts: `python3 check.py units`, say (the parts are
  modules of `checks/`). GitHub runs its `static` and `units` parts on Linux
  and macOS for every push and pull request, and all of it on Linux
  (`.github/workflows/check.yml`), but with Debian's TeX Live in a small image
  rather than the full one, so it doesn't replace your run. With Playwright
  (`pip install playwright`, then `playwright install chromium`, or
  `LATEX_LIVE_CHECK_CHROMIUM` set to a Chromium), `preview` also checks the
  viewer in headless Chromium; without it, it says so and skips that.
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
| `latexlive/` | The server, a package of modules by responsibility: [the server's map](#the-server-latexlive) below. |
| `latexmkrc` | The latexmk configuration: builds into `build/` as job `<doc>-live`, prints the page layout into the log, runs the Python helpers with live.py's own interpreter (`LATEX_LIVE_PYTHON`, never a `python3` launcher from the `PATH`), runs each pdflatex pass through `latexlive/standby.py` when live.py asks for standbys (`LATEX_LIVE_STANDBY_OWNER`), starts the notes-free pass beside each pass (`latex_report.py --early`, from a copy of the files the pass is about to read), and runs `latex_report.py` after each pass and run. |
| `latex_report.py` | The post-build hook. It publishes `<doc>.pdf` atomically, only from successful passes (each pass, so a second one doesn't hold up the first), with a copy of the pass's SyncTeX file in `build/shown/` (a failed pass removes its own; the lookups read the newer of the two: `files.shown_synctex`), writes `status.json` and `file:line` diagnostics, and starts the background notes-free pass (`build/<doc>-clean.pdf`), or rather keeps the one started beside the run's last pass, when the run ended with the files it started from and nothing else it reads changed (`keep_early`). The notes-free pass's TeX runs take warm standbys of their own when they can (`standby.run_tex`). |
| `tex-exec` | Runs a TeX tool in the TeX Live image (rootless podman), or on the host while the image is missing. In shared mode, only in the image (`LATEX_LIVE_REQUIRE_IMAGE`), confined (`LATEX_LIVE_SANDBOX`: as the caller's own uid rather than root, on a read-only root file system with a small `/tmp` for TeX's caches, the paper read-only and only the build folder writable, resource limits, labels that let the server stop the container). A warm standby's container gets labels and a deadline too, and never falls back to the host's TeX (`LATEX_LIVE_STANDBY_LABEL`). |
| `forward.py` | Forward search: maps a source line to a spot in the PDF (`latexlive.synctex`) and writes `build/<job>.forward.json` for the viewer. |
| `viewer.html` | The viewer's page: its elements, the import map, and the module script `viewer/main.js` (PDF.js from `pdfjs/`, and the browser editor's CodeMirror from `codemirror/`). Every page is sent with a policy that runs only the server's own scripts, and this one's import map by its hash (`web.page_policy`): no other inline script, no `on…=` attribute, no `eval`. |
| `viewer/` | The viewer's style (`viewer.css`) and script: plain ES modules, one for each part of the viewer, each saying at its top what it is for. `main.js` imports the rest and starts; `page.js`, `net.js`, `role.js` and `pdfview.js` are what the others stand on (the [shared helpers](#shared-helpers) name theirs), and `poll.js` polls `/status` four times a second (twice for a coauthor in shared mode; the owner's every 50 ms while a new PDF is on its way) and loads each new PDF. Each module imports only from modules below it (no cycles), and only the module that declares a binding assigns it: the others call a function of its (`setTab`, `showDocument`, …). Module paths are relative to the importing module, `fetch` URLs relative to the page (so both work under the hub's `/p/<paper>/<doc>/`). |
| `codemirror/` | CodeMirror 6 for the browser editor: each package's published ES module, unmodified, which the viewer loads through the import map in `viewer.html`. `codemirror/VERSION` lists the packages and versions. Update it as `pdfjs/`, by replacing the files with newer published ones (`npm pack`), and keep the import map in step (`check.py` checks it). |
| `check.py` | The smoke test's entry point: it puts `checks/` (next to its real path) on Python's path and runs `checks.main`, everything or the parts given (`python3 check.py units`). |
| `checks/` | The smoke test, a package of modules as `latexlive/` is: [the checks' map](#the-checks-checks) below. |

Positions on a page travel between the server and the viewer in PDF points from
the top-left corner of the page as the PDF stores it, before its `/Rotate`:
SyncTeX's frame, `\pdfsavepos`'s, and PDF.js's user space with y down. On a
turned page (pdflscape's landscape pages), SyncTeX records the rotated body
as laid out before the rotation; `synctex.page_turns` says where it is drawn
(`on_page`) and where SyncTeX has a point (`as_recorded`), and every lookup
goes through it. The viewer reads text and draws on each page as it is shown,
turned by its `/Rotate`, and converts with `pdfview.pageFrame`; the review
copy places a turned page as shown (`synctex.shown_point`).

`viewer.html` and `viewer/` are served from the checkout on each request (the
browser revalidates them), so a viewer change shows on reload.
`forward.py` runs anew each time, and so does `latex_report.py` as latexmk's
hook. Changes to `live.py` and `latexlive/` take effect only once a preview
restarts, and so do those to `latex_report.py` where the server imports it
(the downloads, the baselines, the checks, shared mode's builder); its viewer
then shows "restart to update".

### The server (`latexlive/`)

`live.py` runs `latexlive.main`. Each module imports only from those above it
in this table (the order of the list in `latexlive/__init__.py`), and says at
its top what it is for.

| Subsystem | Module | For |
|---|---|---|
| Settings | `config` | Every setting (`LATEX_LIVE_*`), read as `config.NAME` at the time of use, and the bounds on reading what TeX writes |
| Warm standby | `standby` | A pdflatex that has read the preamble and waits; latexmkrc runs it as a script and `latex_report.py` loads it, so it imports nothing from the package |
| Who is asking | `auth` | The token, sessions, the users file and personal links, the Host and peer checks |
| | `roles` | What each role may do in shared mode (`least_role`) and see |
| Files and processes | `files` | Where the paper's and the build's files are, path containment, reading and copying `build/` without following links |
| | `tools` | Running TeX tools (in the image, confined in shared mode), SyncTeX and poppler, each in a slot and under a deadline |
| | `git` | Every git command, bounded; the compare menu's and the History tab's commits and diffs |
| | `latex` | Reading LaTeX: groups, comments, `\iffalse`, macros, the root documents and their inputs, BibTeX |
| The document as built | `synctex` | Running and reading SyncTeX, turned pages, and the compute-once cache (`Kept`) |
| | `document` | The page layout and limit, the outline, figures and tables, graphics |
| | `notes` | The note macros, the notes, the margin notes, and which notes the PDF shows (for viewers) |
| Writing the sources | `sources` | The editable files, the lock every write takes, writing a file in one step, who wrote which version |
| | `commits` | Auto-commit in shared mode (`LATEX_LIVE_COMMIT`), and each coauthor's daily budget of writes to the sources |
| | `note_writer` | Notes written, replied to, resolved and edited from the viewer (`POST /note`) |
| | `editor` | The browser editor's files, saves (merged with `git merge-file`), completions and search |
| | `file_actions` | The Files tab's actions and figures (`/asset`), and History's restore |
| | `bib` | The Refs panel (`/bib/*`): index, adding entries, the only outgoing requests (`LATEX_LIVE_LOOKUPS`) |
| | `agent` | The agent plugin hook (`POST /agent/revise`; never in shared mode) |
| The owner's previews | `navigation` | Forward and inverse search, and opening a line in the owner's editor |
| | `instances` | One instance per document (`build/<doc>-live.lock`), the running instances' list, stopping one |
| | `build` | `latexmk -pvc`, started on the main thread; rebuilds and document switches |
| | `baselines` | The change highlights' baselines (HEAD, last push) and the latexdiff |
| | `status` | `/status/`, and who has the paper open (`POST /presence`) |
| | `logs` | The build's log for editors (`/logs/`) |
| | `timing` | `LATEX_LIVE_TIMING=1`'s line per build |
| Shared mode's builder | `shared_watch` | What a shared build depends on, and the guard on the size of `build/` |
| | `shared_build` | Builds without latexmk: each TeX tool in its own confined container |
| Downloads | `export` | The exact build without notes (`build/export/<doc>/`) |
| | `review_anchors` | Where each note sits in the export |
| | `review_layout` | The review copy's pages and gutters, and the copy with PDF comments |
| | `review` | The review copy (`build/review/<doc>/`) |
| Checks | `lints` | Consistency checks: stale figures, the bibliography, terms, typos, float placement |
| | `arxiv` | The arXiv source check and package (`build/arxiv/<doc>/`) |
| | `checks` | `/check/`: the pre-submission checks |
| Serving | `web` | The servers (64 connections, a 10 s header deadline), body limits, the page policy, `BaseHandler` (who is asking, the `Origin` check) |
| | `hub` | One port for every preview: forwards `/p/<paper>/<doc>/…` to the instance |
| | `handler` | An instance's requests: GETs, and the actions (`POSTS`), each as the role allows, with shared mode's per-user caps |
| | `main` | The command line, the checks before shared mode starts, the main loop |

How the pieces meet:

- **Instances and the hub.** One instance per document, with a lock in
  `build/<doc>-live.lock`; running instances list themselves in the state
  folder's `instances/` (`config.STATE`). Each has its own port, from the hub's
  port + 1; the one holding `hub.lock` also serves the hub (44100, or
  `config.default_port`), so users forward one port.
- **Every request.** A server answers only to its own host names
  (`auth.host_kinds`), exchanges a link's token for a signed session cookie
  (`auth.new_session`, checked in `BaseHandler.authorize`), and acts only on a
  POST whose `Origin` is the viewer's own page (`BaseHandler.origin_allowed`,
  before the body is read). Every page carries a policy that runs only the
  server's own scripts (`web.page_policy`).
- **Shared mode** (`LATEX_LIVE_USERS`): roles from the users file
  (`Handler.permitted`, `roles.least_role`), exactly `--port`, loopback peers
  only (`web.LoopbackServer`), no hub, per-user request caps
  (`handler.in_flight`) and write budget (`commits`), auto-commit as each user,
  and builds without latexmk (`shared_watch`, `shared_build`), each TeX tool in
  its own container (`tools.container_argv`, through `tex-exec`). The build's
  state lives in `server.shared`, never read back from `build/`.
- **Settings** are read as `config.NAME` at the time of use (never
  `from .config import`): `main` sets `BUILD` and `PUBLISH` after the import,
  and `check.py` changes some to test with.

### Shared helpers

Use these rather than writing another:

- `tools.run_bounded`: run a program with a deadline, bounded output and its
  process group stopped (`tools.run_tool`, for TeX tools, is built on it).
- `git.run_git`: every git command (`git_capped`, `git_text`).
- `files.within` and `files.plain_rel`: whether a path stays in a folder, and
  in the paper (no `..`, dot file or `build/`).
- `files.open_output`, `read_output`, `create_output`, `copy_output` and
  `build_folder`: read, write and copy in `build/`, and make folders there,
  never through a link.
- `latex.bib_entries` and `latex.parsed_file`: the one BibTeX reading, kept per
  file and version.
- `synctex.Kept`: a value computed once per version of a file.
- `handler.POSTS`: the actions; a new one also needs a role in
  `roles.least_role` and a case in `checks/shared_access.py`.
- `config.flag`: an on/off setting (1 on, 0 off, unset its default).
- `standby.command_line` and `standby.remove_containers`: a process's command
  line, and removing containers by label.
- Viewer: `net.js` `getJSON` and `sendJSON` (a request and its answer as
  `{ok, status, body, error}`, with sign-in pages, unreachable previews and
  429s handled); `page.js` `panelError` and `refreshButton` (a panel's failure
  with Try again, and its ↻), `activatable`, `rovingTabs`/`tabKeys`,
  `keepFocusIn`, `IS_MAC` and `plural`; `sidebar.js` `addTab(name, {label, icon,
  refresh, every, onHead, early, inDiff})`, which decorates a tab that
  `viewer.html` declares and says when its panel refreshes.

### Settings that only latex-live sets

The user-facing `LATEX_LIVE_*` settings are in [setup](docs/setup.md#environment-variables)
and [shared mode](docs/shared-mode.md#settings). These others pass
information between latex-live's own processes:

- `LATEX_LIVE_BUILD`, `LATEX_LIVE_PUBLISH`: the build folder (`--build-dir`)
  and where `<doc>.pdf` is published, for latexmkrc, `latex_report.py` and
  `forward.py`.
- `LATEX_LIVE_DOC`: the document's name, for `latex_report.py`.
- `LATEX_LIVE_HOME`: the checkout, for latexmkrc.
- `LATEX_LIVE_PYTHON`: live.py's own interpreter, which latexmkrc runs the
  Python helpers with.
- `LATEX_LIVE_EXEC`: how TeX runs (`tex-exec` and its arguments); live.py drops
  one it inherited.
- `LATEX_LIVE_TIMEOUT`, `LATEX_LIVE_RUN`: a tool's deadline and its
  container's label, for `tex-exec`.
- `LATEX_LIVE_INSTANCE`: a shared instance's label for its containers.
- `LATEX_LIVE_SANDBOX`, `LATEX_LIVE_REQUIRE_IMAGE`: shared mode's confinement,
  and never the host's TeX, for `tex-exec`.
- `LATEX_LIVE_STANDBY_OWNER`: the pid of the live.py that asks for warm
  standbys; `LATEX_LIVE_STANDBY_LABEL`: a standby's container label.
- `LATEX_LIVE_EARLY_LABEL`, `LATEX_LIVE_CLEAN_PASS`: what ties a notes-free
  pass's runs to it.
- `LATEX_LIVE_CHECK_PORT` (the first of the 25 ports `check.py` takes) and
  `LATEX_LIVE_CHECK_CHROMIUM` (a Chromium for the browser checks): for
  `check.py` only.

`check.py`'s `static` part checks that every `LATEX_LIVE_*` name in the code is
in the user docs or in this list.

### The checks (`checks/`)

`check.py` runs `checks.main`: every part, or those given. Each module imports
only from those above it here (the list in `checks/__init__.py`), and says at
its top what it checks; each `ok` line says what passed.

| Part | Modules |
|---|---|
| Foundation | `base` (where the checks run: the checkout, 25 ports, the instances' environment; `ok`, `fail`, `wait_for`, `in_thread`), `helpers` (files, git, instances and requests to them, `fake_server`, `patched`, stand-ins, the image probe), `papers` (the scratch paper) |
| Building blocks | `access` (who reaches the owner's preview), `browser` (the viewer in headless Chromium, with Playwright) |
| The checkout as it is | `static` (the checkout as it is: the Python compiles, also with 3.9; the import map; the viewer's modules parse and link, through `link.mjs` in Node; every setting documented) |
| The server's parts | `shared_units`, `perf_units`, `bib_units`, `files_units`, `history_units`, `agent_units`, and `units`, which runs them (the server's parts without an instance; also that every route has a role case and `CAPS` is complete) |
| Threads beside the instance checks | `standby`, `early`, `clean_standby`, `landscape` |
| Shared mode | `shared_helpers`, `shared_setup`, `shared_access`, `shared_sessions`, `shared_sandbox`, `shared_downloads`, `shared_notes`, `shared_editor`, `shared_rebuild`, `shared_commits`, `shared_files`, `shared_browser`, and `shared`, which runs them in two threads of their own |
| The instance checks | `preview` (an instance on the scratch paper behind the hub; it starts the threads), then `main` |

A check patches a function where the code that calls it looks it up (e.g.
`export.run_tool`).

## Rules

- **Python's standard library only**, and no build step: the viewer's modules
  load in the browser as they are, and PDF.js and CodeMirror stay vendored.
- **Start latexmk only from the main thread.** Its parent-death signal fires
  when the thread that started it exits. Request handlers hand such work to the
  main loop (`on_main_thread`).
- **Never print the token**, a URL containing `?t=`, a personal link's token
  from a users file, or a session cookie. Every request needs one, and anything
  that reaches the port can send requests.
- **Nothing acts on a GET, in any mode.** Actions and builds made on request
  are POSTs (`handler.POSTS`), which go through the `Origin` check and take a
  JSON object.
- **Keep coauthors to their role in shared mode.** An endpoint is the owner's
  until `least_role` grants it to a lesser role. TeX tools
  run only in the image, through `tex-exec` with `LATEX_LIVE_SANDBOX`, each
  run in a `SPAWNS` slot (the live build: `LIVE_SPAWN`, its own) and under a
  timeout, with `build/` watched for size (`watch_build_size`), and no host program parses what
  TeX wrote as commands (so no latexmk there). Treat every file in `build/` as
  written by a coauthor: read and copy them with `files.open_output` and `copy_output`, write
  them with `create_output`, in folders there made with `files.build_folder`. Extend the role checks (`checks/shared_access.py`)
  with each new endpoint (`units` fails on a route without one).
- **Test on scratch papers** in a temporary directory, as `check.py` does.
  Never edit a real paper's sources, and never commit a build of one.
  The shared-mode checks run in threads of their own, beside the others
  (`checks/shared.py`), and so do the warm standbys' and the early
  notes-free pass's: give each side its own instances, and wait for a
  condition (`wait_for`) rather than for a fixed time where you can. A new
  area of shared-mode checks goes in a `checks/shared_*` module of its own,
  in `shared_run`'s list.
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
