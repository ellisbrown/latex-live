# Setting up latex-live

This guide installs latex-live for your own previews, on Linux or macOS,
either on the machine you work on or on a remote one you edit through VS Code
Remote-SSH (a lab server or workstation, say). It is written for a person or a
coding agent. An agent can do steps 1–4 and 6 on its own; step 5 (VS Code)
needs the person. To share a preview with coauthors, set this up first, then
follow [shared-mode.md](shared-mode.md).

## Rules for an agent

- **Never print the token**, a URL containing `?t=`, or a shared preview's
  users file (it holds personal links' tokens). They grant access to the
  viewer. When a command's output may contain a token, redact it first, e.g.
  `sed -E 's/t=[A-Za-z0-9_-]{8,}/t=REDACTED/g'`.
- **Don't modify the paper's sources.** latex-live writes into `build/` plus
  the published `<doc>.pdf`, and into the sources only when the person adds,
  edits, or deletes a note from the viewer, or saves in the browser editor.
  Only the `.vscode/tasks.json` added in step 4 goes in the paper checkout, and
  it stays out of git.
- **Stop any instance you start for testing** before handing over. Only one
  instance runs per document. A leftover one makes the person's VS Code task
  exit, reporting "already running".

## 1. Check the prerequisites

```bash
python3 --version             # 3.9 or newer; standard library only
latexmk -v | sed -n 2p         # latexmk and perl on the host
pdftotext -v 2>&1 | head -1    # poppler-utils: pdftotext, pdfinfo, pdffonts, pdfimages
podman --version               # Linux: rootless podman runs the TeX tools
pdflatex --version | head -1   # macOS: TeX on the host instead
git --version
```

- **Linux:** install anything missing with your package manager, e.g.
  `sudo apt install latexmk poppler-utils podman git` or the same with `dnf`.
  Without root, ask the machine's admin for these.
- **macOS:** `xcode-select --install` (python3 and git),
  `brew install --cask mactex-no-gui` (TeX Live with latexmk and synctex), and
  `brew install poppler`. Skip podman: without it, latex-live uses the host's
  TeX. macOS is not yet tried; please report problems.

## 2. Get the code and the TeX image

```bash
git clone https://github.com/ellisbrown/latex-live ~/.local/share/latex-live
podman pull docker.io/minidocks/texlive:2024-full    # Linux: about 4.6 GB, once
```

The path `~/.local/share/latex-live` matches the task file in step 4. Any
other path works if the task file points to it.

pdflatex, bibtex and latexdiff run in that TeX Live 2024 image, which is close
to Overleaf, and so does synctex unless the host has one; latexmk runs on the
host. latex-live checks for the
image on each call. While it is missing, builds use the TeX installed on the
host, if there is one, and the viewer shows ⚠ TeX image missing. Once the image
is pulled again, latex-live uses it with no restart. `--host-tex` uses the
host's TeX on purpose; its version may differ from Overleaf's.

## 3. First run from a terminal

From the paper's root folder (the one with the main `.tex` file):

```bash
python3 ~/.local/share/latex-live/live.py --no-open
```

It builds the root `.tex` file (one with a `\documentclass`) whose sources
were edited last; the viewer's toolbar switches to another. To start with a
given one, name it, e.g. `live.py main --no-open`. Without `--no-open`, it also
opens the PDF in a VS Code editor tab.

Expected output, in order:

1. `=== latex-live: viewer (on a remote machine, forward port 44100 first): http://localhost:44100/p/...?t=...`
   (a second, direct URL follows with `LATEX_LIVE_HOST`). The first run
   creates the token, `~/.config/latex-live/token` (mode 0600), which every
   checkout shares.
2. `=== latex-live <version>: building <doc>.tex`, naming the other root files.
3. `=== latex-live: TeX Live 2024 (podman)`, or without podman
   `=== latex-live: TeX from the host (no podman)`.
4. latexmk output, ending with `=== Watching for updated files`. The first build
   takes about a minute.

Check it without printing the token:

```bash
PORT=$(python3 -c 'import json; print(json.load(open("build/<doc>-live.lock"))["port"])')
TOKEN=$(cat ~/.config/latex-live/token)
curl -s "http://127.0.0.1:$PORT/status/<doc>.pdf?t=$TOKEN" | python3 -m json.tool | grep '"ok"'   # "ok": true
```

Then stop it with Ctrl+C, or `kill` the `live.py` process if you started it in
the background.

## 4. Add the VS Code tasks

Create `<paper>/.vscode/tasks.json` and keep it out of git:

```bash
echo .vscode/tasks.json >> .git/info/exclude
```

Contents:

```jsonc
{
  // latex-live: starts when this folder opens, or with Cmd+Shift+B.
  "version": "2.0.0",
  "tasks": [
    {
      "label": "LaTeX: live preview",
      "type": "shell",
      "command": "python3 ${env:HOME}/.local/share/latex-live/live.py --no-open",
      // Optional (regular expressions): affiliations the anonymity check should flag, and
      // codenames that must never reach the arXiv source.
      "options": { "env": { "LATEX_LIVE_ANON_TERMS": "\\bExample University\\b", "LATEX_LIVE_PRIVATE_TERMS": "(?i)projectx" } },
      "isBackground": true,
      "group": { "kind": "build", "isDefault": true },
      "runOptions": { "runOn": "folderOpen" },
      "presentation": { "reveal": "always", "panel": "dedicated", "clear": true },
      "problemMatcher": {
        "owner": "latex-live",
        "source": "LaTeX",
        "fileLocation": "absolute",
        "pattern": { "regexp": "^(/.+?):(\\d+): (error|warning|info): (.*)$", "file": 1, "line": 2, "severity": 3, "message": 4 },
        "background": { "activeBegin": true, "beginsPattern": "^Latexmk: Run number|^Rc files read", "endsPattern": "^=== Watching for updated files" }
      }
    },
    {
      "label": "LaTeX: show line in PDF",
      "type": "process",
      "command": "python3",
      "args": ["${env:HOME}/.local/share/latex-live/forward.py", "${file}", "${lineNumber}"],
      "presentation": { "reveal": "never", "echo": false, "focus": false, "panel": "shared", "showReuseMessage": false, "close": true },
      "problemMatcher": []
    }
  ]
}
```

For a folder with several papers (say, a notes repository with each paper in a
subfolder), add a copy of the first task whose command passes the current file,
`live.py "${file}" --no-open`, with a label such as `LaTeX: live preview
(current file)` and without `runOn`. It previews the paper of the file in front
of you (its folder, or the nearest one above with a root file), beside any
other instance.

The problem matcher sends LaTeX errors and warnings to the Problems panel. The
[environment variables](#environment-variables) below describe the two in
`options`; delete either (or the whole `options` line) if you don't need it.

## 5. In VS Code (the person)

1. **Allow the automatic task.** Open the paper folder. VS Code asks whether to
   allow tasks that run on folder open; allow it. To start the task by hand,
   press Cmd+Shift+B (Ctrl+Shift+B on Linux and Windows).
2. **Remote machine only: one port.** Every preview is served on port 44100.
   Open *Preferences: Open Remote Settings (JSON)* and add:

   ```json
   "remote.portsAttributes": {
     "44100": { "label": "latex-live", "requireLocalPort": true, "onAutoForward": "silent" },
     "44101-44110": { "label": "latex-live (behind 44100)", "onAutoForward": "ignore" }
   }
   ```

   The first line keeps the laptop's port the same as the remote machine's, so
   saved URLs keep working. The second stops VS Code from offering to forward
   each preview's own port, which you never need.
3. **Open the viewer.**
   - On a remote machine, forward port 44100 in the Ports panel (once; VS Code
     often does it by itself).
   - Open the task's `http://localhost:44100/p/.../?t=...` URL in any browser,
     or in VS Code (*Simple Browser: Show*).
   - The token becomes a cookie, so later visits work without it, and
     `http://localhost:44100/` lists every running preview.
4. **Optional: a key for forward search.** Add this to your
   `keybindings.json` (use `ctrl+alt+j` on Linux and Windows):

   ```json
   { "key": "cmd+alt+j", "command": "workbench.action.tasks.runTask", "args": "LaTeX: show line in PDF", "when": "resourceExtname == .tex" }
   ```

Check that:

- saving a `.tex` file rebuilds, and the page reloads in place;
- double-clicking the PDF jumps to the source line;
- the forward-search key in a `.tex` file highlights that line in the viewer.

[viewer.md](viewer.md) describes the rest of the viewer.

## 6. Updating

```bash
git -C ~/.local/share/latex-live pull
```

Then restart the task with Cmd+Shift+B; until then, the viewer shows "restart
to update". Releases are tagged `v<VERSION>` (`live.py --version` prints the
version, and the viewer's ⋯ menu shows it), with notes on the GitHub releases
page. To stay on a release, check out its tag, e.g.
`git -C ~/.local/share/latex-live checkout v0.4.1`. To propose a change, see
[CONTRIBUTING.md](../CONTRIBUTING.md).

## Ports and the token

- **One port for every preview.** Each preview runs in its own process (its
  own terminal, latexmk and version) on its own port, from 44101 to 44110.
  Whichever preview holds the hub also serves port 44100, and forwards
  `/p/<folder>-<hash>/<doc>/` to the right preview; when it stops, another
  takes over within a second. `/` opens the only preview or lists them all,
  and `/papers` lists them too.
- **Moving the ports.** The hub's port is `--port`, else `$LATEX_LIVE_PORT`,
  else the number in the `port` file of the state folder
  (`~/.config/latex-live/port`), else 44100; the previews take the ten ports
  after it. Use the file to move every preview at once, e.g.
  `echo 44200 > ~/.config/latex-live/port`, then restart the preview tasks and
  update the ports in step 5.
- **The token.** Every request needs the token in the state folder's `token`
  file: `?t=` once, after which it is a cookie. Anything that reaches the port
  can send requests, so the token is required even on a laptop. Your browser
  keeps one cookie per host name, whatever the port.
- **The state folder** is `~/.config/latex-live` (`$XDG_CONFIG_HOME/latex-live`
  if that is set, or `$LATEX_LIVE_STATE`). It holds the token, the `port` file,
  the hub's lock and the list of running instances, shared by every checkout of
  latex-live.

## Options

```
live.py [doc | PATH] [--port N] [--page-limit N] [--build-dir DIR] [--no-build] [--no-open] [--host-tex] [--replace]
```

- **`doc`:** a root `.tex` file, with or without the extension. Without one,
  latex-live picks the root file whose sources were edited last; on a tie, the
  last one it built. Given a path instead, e.g. the editor's current file, it
  runs in that file's paper and builds the file if it is a root one.
- **`--page-limit N`:** the main-text page limit for the page badge. Without
  it, the limit comes from the venue style: ICLR, NeurIPS and COLM 9 pages;
  ICML, CVPR and ICCV 8. Pass it for other venues and for a camera-ready extra
  page.
- **`--replace`:** one instance runs per document. Another one for the same
  document prints the running viewer's URL and exits; `--replace` stops the
  running one and takes over. An instance that no longer answers is replaced
  without asking.
- **`--build-dir DIR`:** keeps every output in `DIR` (relative to the paper
  root), including `<doc>.pdf`. A second instance, such as a development
  version, can then run beside the usual one without touching its files.
- **`--host-tex`:** use the host's TeX rather than the image.
- **`--no-open`:** don't open the PDF in a VS Code editor tab.
- **`--no-build`:** serve the viewer only; don't run latexmk.
- **`--port N`:** the hub's port (see above). A shared instance needs it.
- **`--version`:** print the version.

## Environment variables

Set these in the VS Code task's `options.env` (step 4). Those for shared mode
are in [shared-mode.md](shared-mode.md#settings).

- `LATEX_LIVE_ANON_TERMS`: a regular expression of affiliations and other
  terms that identify the authors, for the anonymity check, e.g.
  `\bExample University\b`. Author names are found automatically (git
  `user.name` and the note-macro comments).
- `LATEX_LIVE_PRIVATE_TERMS`: a regular expression of codenames and other
  internal names that must never appear in the arXiv source, e.g.
  `(?i)projectx|cluster-a`, for the arXiv source check.
- `LATEX_LIVE_PROTECTED`: a regular expression for a marker of text that must
  stay as written, e.g. `\\ch\{human-written\}` at the end of a paragraph or
  inside a caption. Notes written from the viewer (replies too) go after the
  paragraph or caption it marks, and the note buttons can't edit, resolve, or
  delete the marker. The browser editor doesn't guard it: whoever may edit can
  change it there like any other text.
- `LATEX_LIVE_EDITOR`: the editor command that double-click opens the source
  in while the browser editor is closed (`<editor> -g FILE:LINE`), e.g.
  `cursor`. Without it, latex-live uses `code`, and only from a VS Code
  terminal; elsewhere, double-click opens the browser editor instead. On a
  Mac, Cursor's local terminals don't set the variable VS Code terminals set,
  so set this to the `cursor` symlink to the app (`/usr/local/bin/cursor`);
  Cursor's bundled `code` script doesn't reach the open window.
- `LATEX_LIVE_STANDBY=0`: turn off the warm standby. By default each preview
  keeps a pdflatex run waiting that has read the paper's preamble already, so
  a save waits only for the body: about a third less time for a typical
  paper. The PDF, the errors and SyncTeX are what a normal build gives. After
  a change to the preamble, or to a file it reads, the next pass runs
  normally, and the one after it is checked against a normal pass before the
  standby is trusted. A pass that can't use the standby runs normally, and
  the task's output says why once (`=== latex-live: ... warm standby ...`).
  A waiting standby holds about 120 MB of memory (pdflatex and its podman
  client). It runs only in the TeX Live image (or with `--host-tex` on the
  host's TeX), and not in shared mode.
- `LATEX_LIVE_HOST`: also print a direct viewer URL on this host name, e.g.
  the name a tunnel exposes.
- `LATEX_LIVE_PORT`: the hub's port (see [Ports and the token](#ports-and-the-token)).
- `LATEX_LIVE_STATE`: the state folder (default `~/.config/latex-live`).

## Keeping the image

Some shared machines run a daily `podman system prune -a`, which removes every
image no container is using. A container that only sleeps keeps the TeX Live
image in use. With systemd and lingering on (`loginctl show-user $USER -p
Linger`), put this in `~/.config/containers/systemd/latex-live-texlive.container`:

```ini
[Container]
Image=docker.io/minidocks/texlive:2024-full
ContainerName=latex-live-texlive
Exec=sleep infinity
Network=none
RunInit=true
Pull=missing

[Service]
Restart=always
RestartSec=60

[Install]
WantedBy=default.target
```

Then run `systemctl --user daemon-reload && systemctl --user start
latex-live-texlive`. It starts again at boot and pulls the image if it is
missing.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| ⚠ TeX image missing in the viewer, or `is not pulled` in the task output | Pull the image (step 2). No restart is needed. If it keeps disappearing, a cleanup job is probably pruning unused podman images (`podman system prune -a`). See [Keeping the image](#keeping-the-image). |
| `already running for <doc>.tex` | Another instance builds that document, e.g. from another VS Code window, and still answers. It prints that instance's URL; `--replace` stops it and runs here instead. `http://localhost:44100/papers` lists every running preview. |
| "restart to update" in the viewer | latex-live changed on disk (e.g. after a pull) since the task started. Restart the task, then reload. |
| 403 in the browser | Open the full URL with `?t=` once. The token is in `~/.config/latex-live/token`. |
| Stopping the task leaves latexmk running | Your `python3` may be a wrapper that doesn't pass signals on. Use the interpreter itself, e.g. `/usr/bin/python3`. On macOS, a server killed with `kill -9` leaves latexmk running; stop it by hand. |
| Double-click opens the browser editor, not VS Code | latex-live runs outside a VS Code terminal, so `code` can't reach the window. Start it with the VS Code task, or set `LATEX_LIVE_EDITOR`. |
| Double-click does nothing, or says the image is not pulled | Without a `synctex` on the host, SyncTeX runs in the image: pull it (step 2). |
| `port 44100 is taken, so the hub waits for it` | Another program holds 44100. Move latex-live for good with `echo 44200 > ~/.config/latex-live/port` (every preview reads it; the previews' own ports follow, from 44201), restart the preview tasks, and update the ports in step 5. Meanwhile each viewer works on its preview's own port, which the task prints; forward that port by hand. |
| Every `localhost:44100/p/...` viewer hangs | The preview serving the hub is paused (Ctrl+Z in its terminal: run `fg` there) or stuck. Starting a preview of another document (or with `--replace`) replaces a stuck one after about 10 s. |
| `no free port in 44101-44110` | Too many previews: stop old ones (`pgrep -af latex-live/live.py`). |
| Viewer shows an old PDF with a red banner | The last build failed and the previous PDF was kept. Click the error to open it. |
| `=== latex-live: not using the warm standby for this pass: ...` (or `no warm standby ...`) | Nothing to fix: that pass ran normally, as without the standby, and the line says why. Typical reasons are a change to the preamble or to a file it reads (the standby is then rebuilt and checked) and a new file in the paper. Set `LATEX_LIVE_STANDBY=0` to turn the standby off. |
