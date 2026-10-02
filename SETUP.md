# Setting up latex-live

This guide covers a Linux machine, either the one you work on or a remote one
you edit through VS Code Remote-SSH (a lab server or workstation, say). macOS
isn't supported yet. It is written for a person or a coding agent. An agent
can do steps 1–4 and 6 on its own. Step 5 (VS Code) needs the person.

## Rules for an agent

- **Never print the token** or a URL containing `?t=`. It grants access to the
  viewer. When a command's output may contain it, redact it first, e.g.
  `sed -E 's/t=[A-Za-z0-9_-]{8,}/t=REDACTED/g'`.
- **Don't modify the paper's sources.** latex-live only reads them and writes
  into `build/` plus the published `<doc>.pdf`. Only the `.vscode/tasks.json`
  added in step 4 goes in the paper checkout, and it stays out of git.
- **Stop any instance you start for testing** before handing over. Only one
  instance runs per document. A leftover one makes the person's VS Code task
  exit, reporting "already running".

## 1. Check the prerequisites

```bash
python3 --version             # 3.9 or newer; standard library only
latexmk -v | sed -n 2p         # latexmk and perl on the host
pdftotext -v 2>&1 | head -1    # poppler-utils: pdftotext, pdfinfo, pdffonts, pdfimages
podman --version               # rootless podman runs the TeX tools
git --version
```

Install anything missing with your package manager, e.g.
`sudo apt install latexmk poppler-utils podman git` or the same with `dnf`.
Without root, ask the machine's admin for these.

Linux only for now. On macOS, the call that stops latexmk together with the
server fails.

## 2. Get the code and the TeX image

```bash
gh repo clone ellisbrown/latex-live ~/.local/share/latex-live   # or git clone over HTTPS
podman pull docker.io/minidocks/texlive:2024-full                # about 4.6 GB, once
```

The path `~/.local/share/latex-live` matches the task file in step 4. Any
other path works if the task file points to it.

pdflatex, bibtex, synctex and latexdiff run in that TeX Live 2024 image, which
is close to Overleaf. latexmk runs on the host. latex-live checks for the
image on each call. While it is missing, builds use the TeX installed on the
host, if there is one, and the viewer shows ⚠ TeX image missing. Once the image
is pulled again, latex-live uses it with no restart.

## 3. First run from a terminal

From the paper's root folder (the one with the main `.tex` file):

```bash
python3 ~/.local/share/latex-live/live.py --no-open
```

It builds the root `.tex` file whose sources were edited last; the viewer's
toolbar switches to another. To start with a given one, name it, e.g.
`live.py iclr2027 --no-open`. The page badge uses the venue's limit for ICLR,
NeurIPS and COLM styles; `--page-limit N` sets another.

Expected output, in order:

1. Two `=== latex-live: viewer ...` lines with URLs. The first run creates
   `~/.local/share/latex-live/token`, with mode 0600 and ignored by git.
2. `=== latex-live: building <doc>.tex`, naming the other root files.
3. `=== latex-live: TeX Live 2024 (podman)`
4. latexmk output, ending with `=== Watching for updated files`. The first build
   takes about a minute.

Check it without printing the token:

```bash
PORT=$(cat build/<doc>-live.lock)
TOKEN=$(cat ~/.local/share/latex-live/token)
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
      // Optional: affiliations the anonymity check should flag (a regular expression).
      "options": { "env": { "LATEX_LIVE_ANON_TERMS": "\\bNYU\\b|New York University" } },
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

The problem matcher sends LaTeX errors and warnings to the Problems panel.
Author names for the anonymity check come from git's `user.name` and the
note-macro comments. Affiliations don't, so list them in
`LATEX_LIVE_ANON_TERMS`, or delete that `options` line.

## 5. In VS Code (the person)

1. **Allow the automatic task.** Open the paper folder. VS Code asks whether to
   allow tasks that run on folder open; allow it. To start the task by hand,
   press Cmd+Shift+B (Ctrl+Shift+B on Linux and Windows).
2. **Remote machine only: keep port numbers.** Open *Preferences: Open Remote
   Settings (JSON)* and add:

   ```json
   "remote.portsAttributes": {
     "44100-44109": { "label": "latex-live viewer", "requireLocalPort": true, "onAutoForward": "silent" }
   }
   ```

   This keeps the laptop's port the same as the remote machine's, so saved URLs
   keep working.
3. **Open the viewer.**
   - On a remote machine, forward the port from the task's URL in the Ports
     panel first. VS Code often forwards it by itself.
   - Open that `http://localhost:PORT/?t=...` URL in any browser, or in VS
     Code (*Simple Browser: Show*).
   - The token becomes a cookie, so later visits to `http://localhost:PORT/`
     work without it.
4. **Optional: a key for forward search.** Add this to your
   `keybindings.json` (use `ctrl+alt+j` on Linux and Windows):

   ```json
   { "key": "cmd+alt+j", "command": "workbench.action.tasks.runTask", "args": "LaTeX: show line in PDF", "when": "resourceExtname == .tex" }
   ```

Check that:

- saving a `.tex` file rebuilds, and the page reloads in place;
- double-clicking the PDF jumps to the source line;
- the forward-search key in a `.tex` file highlights that line in the viewer.

## 6. Updating

```bash
git -C ~/.local/share/latex-live pull
```

Then restart the task with Cmd+Shift+B. To propose a change, push a branch and
open a pull request rather than pushing to `main`.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| ⚠ TeX image missing in the viewer, `is not pulled` in the task output, or double-click does nothing | Pull the image (step 2). No restart is needed. If it keeps disappearing, a cleanup job is probably pruning unused podman images (`podman system prune -a`). See [Keeping the image](#keeping-the-image). |
| `already running for <doc>.tex` | Another instance holds the lock, e.g. another VS Code window. It prints that instance's URL. |
| 403 in the browser | Open the full URL with `?t=` once. The token is in `~/.local/share/latex-live/token`. |
| Stopping the task leaves latexmk running | Your `python3` may be a wrapper that doesn't pass signals on. Use the interpreter itself, e.g. `/usr/bin/python3`. |
| Double-click does nothing | The task must run in a VS Code terminal so that `code` is on `PATH`. |
| `no free port in 44100-44109` | Stop old instances (`pgrep -af latex-live/live.py`), or start elsewhere with `--port`. |
| Viewer shows an old PDF with a red banner | The last build failed and the previous PDF was kept. Click the error to open it. |

### Keeping the image

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
