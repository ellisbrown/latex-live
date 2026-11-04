# Contributing to latex-live

For people and coding agents changing latex-live. The [README](README.md)
describes what it does, and [SETUP.md](SETUP.md) describes how to install it.

## Workflow

- `main` stays usable: people run their previews straight from a checkout of
  it. Work on a branch, in its own worktree
  (`git worktree add ../latex-live-<topic> -b <topic> origin/main`), and
  remove the worktree once the branch is merged.
- One pull request per change. Before you open it and again before you merge,
  run `python3 check.py`. It takes a minute or two and needs TeX, as `live.py`
  does, and podman for its shared-mode part. When you change behavior that it
  doesn't cover, extend it.
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
| `live.py` | The server, run from a paper root. It runs `latexmk -pvc` and serves the viewer, the PDFs, and JSON endpoints: who the user is (`/me`), status, outline, notes, checks, figures, SyncTeX, diffs, the change baselines, and the browser editor's files (`/files`, `/file`). It also writes notes into the sources (`POST /note`), saves the browser editor's files, merged with `git merge-file` when they changed meanwhile (`POST /file`), rebuilds (`POST /rebuild`), and builds the downloads without notes (`/export`: an exact build in `build/export/<doc>/`) and with notes in the margin (`/review`, in `build/review/<doc>/`: the export's pages placed on wider ones, with the notes typeset where a probe pass of the export records them), each once per build. One instance per document, with a lock in `build/<doc>-live.lock`; running instances list themselves in `~/.config/latex-live/instances/`, beside the token. Each instance has its own port (from the hub's port + 1, 44101); the one holding `hub.lock` also serves the hub on 44100 (or `$LATEX_LIVE_PORT`, or the number in `~/.config/latex-live/port`: `default_port`), which forwards `/p/<paper>/<doc>/…` to the right instance (`HubHandler`), so users forward one port. With `LATEX_LIVE_USERS`, an instance runs in shared mode instead: users and roles from a users file (`authorize`, then `permitted`, with the roles in `least_role`), an `Origin` check on every POST (`origin_allowed`) and on the GETs that start a build (`cross_site`), exactly `--port`, loopback peers only (`LoopbackServer`), no hub, TeX only in the confined image (`sandbox_args`), and commits as each user (`commit_loop`). |
| `latexmkrc` | The latexmk configuration: builds into `build/` as job `<doc>-live`, prints the page layout into the log, and runs `latex_report.py` after each run. |
| `latex_report.py` | The post-build hook. It publishes `<doc>.pdf` atomically and only after a successful run, writes `status.json` and `file:line` diagnostics, and starts the background notes-free pass (`build/<doc>-clean.pdf`). |
| `tex-exec` | Runs a TeX tool in the TeX Live image (rootless podman), or on the host while the image is missing. In shared mode, only in the image, confined (`LATEX_LIVE_REQUIRE_IMAGE`, `LATEX_LIVE_SANDBOX`). |
| `forward.py` | Forward search: maps a source line to a spot in the PDF and writes `build/<job>.forward.json` for the viewer. |
| `viewer.html` | The whole viewer in one file (PDF.js from `pdfjs/`, and the browser editor's CodeMirror from `codemirror/`). It polls `/status` twice a second. |
| `codemirror/` | CodeMirror 6 for the browser editor: each package's published ES module, unmodified, which `viewer.html` loads through its import map. `codemirror/VERSION` lists the packages and versions. Update it as `pdfjs/`, by replacing the files with newer published ones (`npm pack`), and keep the import map in step (`check.py` checks it). |
| `check.py` | The smoke test: the Python compiles, the viewer's import map and script are valid, an instance on a scratch paper works behind the hub, and so does a shared-mode instance on a copy of it, for users of every role. |

`viewer.html` is served from the checkout on each request, so a viewer change
shows on reload. `latex_report.py` and `forward.py` run anew each time.
`live.py` changes take effect only once a preview restarts.

## Rules

- **Python's standard library only**, and no build step. The viewer stays one
  file of our own code. PDF.js (`pdfjs/`) and CodeMirror (`codemirror/`) stay
  vendored as published, unmodified, and load straight from the checkout.
- **Start latexmk only from the main thread.** Its parent-death signal fires
  when the thread that started it exits. Request handlers hand such work to the
  main loop (`on_main_thread`).
- **Never print the token**, a URL containing `?t=`, or a personal link's
  token from a users file. Every request needs one, and anything that reaches
  the port can send requests.
- **Keep coauthors to their role in shared mode.** An endpoint is the owner's
  until `least_role` grants it to a lesser role, every POST goes through the
  `Origin` check (and a GET that starts a build through `cross_site`), and TeX
  runs only in the image, with `sandbox_args` (in `tex-exec`,
  `LATEX_LIVE_SANDBOX`), each run in a `SPAWNS` slot and under a timeout.
  Extend `check.py`'s role checks with each new endpoint.
- **Test on scratch papers** in a temporary directory, as `check.py` does.
  Never edit a real paper's sources, and never commit a build of one.
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
- In the README, describe what a user sees, not how it is implemented.
- Commit subjects are imperative sentences that say what changed, e.g. "Keep
  the toolbar to one row". Use the body for why, and for anything a reviewer
  should check.
