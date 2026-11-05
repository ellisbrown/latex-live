# Setting up latex-live

This guide covers Linux and macOS, either the machine you work on or a remote
one you edit through VS Code Remote-SSH (a lab server or workstation, say). It
is written for a person or a coding agent. An agent
can do steps 1–4 and 6 on its own. Step 5 (VS Code) needs the person.

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
  TeX. macOS support is new and not yet tried on a Mac; please report
  problems.

## 2. Get the code and the TeX image

```bash
gh repo clone ellisbrown/latex-live ~/.local/share/latex-live   # or git clone over HTTPS
podman pull docker.io/minidocks/texlive:2024-full                # Linux: about 4.6 GB, once
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
NeurIPS, COLM, ICML, CVPR and ICCV styles; `--page-limit N` sets another.

Expected output, in order:

1. Two `=== latex-live: viewer ...` lines with URLs. The first run creates
   the token, `~/.config/latex-live/token` (mode 0600), which every checkout
   shares.
2. `=== latex-live <version>: building <doc>.tex`, naming the other root files.
3. `=== latex-live: TeX Live 2024 (podman)`, or on macOS
   `=== latex-live: TeX from the host (no podman)`
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
      "options": { "env": { "LATEX_LIVE_ANON_TERMS": "\\bNYU\\b|New York University", "LATEX_LIVE_PRIVATE_TERMS": "(?i)projectx" } },
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
of you, beside any other instance.

The problem matcher sends LaTeX errors and warnings to the Problems panel.
Author names for the anonymity check come from git's `user.name` and the
note-macro comments. Affiliations don't, so list them in
`LATEX_LIVE_ANON_TERMS`. The arXiv source check flags the codenames and other
internal names in `LATEX_LIVE_PRIVATE_TERMS`. Delete either variable (or the
whole `options` line) if you don't need it.

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

## 6. Updating

```bash
git -C ~/.local/share/latex-live pull
```

Then restart the task with Cmd+Shift+B; until then, the viewer shows "restart
to update". To stay on a release instead, check out its tag
(`git -C ~/.local/share/latex-live checkout v0.1.1`); the releases page lists
them. To propose a change, see [CONTRIBUTING.md](CONTRIBUTING.md).

## Sharing a preview with coauthors

A shared instance gives coauthors who don't run LaTeX themselves the live
preview of your paper, in their own browsers: they read it, comment as
themselves, and editors edit the LaTeX beside the PDF. Each can also download
the paper, as built, without its notes, or with its notes in the margin (a
copy to read the notes away from the preview). The README's
[Shared mode](README.md#shared-mode) describes the roles and the users file.
The instance answers only connections from this machine, so coauthors reach
it in one of two ways:

- through an authenticating reverse proxy on this machine, which names each
  signed-in user in a request header;
- with a personal link each, through a reverse proxy or tunnel on this
  machine that doesn't sign anyone in.

An agent can do these steps once the person has said who gets which role and
how coauthors reach the machine.

### 0. Check podman's limits

Every TeX run of a shared instance is a container with memory and CPU limits,
which rootless podman can set only with cgroup v2 and those controllers
delegated to your user. This prints `ok` where they work:

```bash
podman run --rm --pull=never --network=none --memory=4g --cpus=2 --entrypoint= \
  docker.io/minidocks/texlive:2024-full true && echo ok
```

If it fails over cgroups, ask an administrator to delegate the `memory` and
`cpu` controllers to user sessions (systemd's `Delegate=` for `user@.service`).

### 1. Make a separate copy of the paper

From your paper's checkout, add a git worktree on a branch of its own:

```bash
git worktree add -b shared ../paper-shared
```

Run the shared instance there, never in your own checkout:

- coauthors' saves and notes land in the copy, so they never touch your
  working tree or your uncommitted work;
- the LaTeX that editors write can read any file in the copy (see
  [Security notes](#security-notes)), and a worktree holds only the branch's
  files: git's own folder, with your remotes' settings, stays outside it;
- with `LATEX_LIVE_COMMIT=1`, each person's edits become commits on `shared`,
  authored by them, which you merge like any other branch (step 6).

While it runs, leave the copy's files to latex-live: a commit takes whole
files, so a change you made there by hand would go in as a coauthor's. Edit
in your own checkout and merge (step 6), or in the shared viewer as the
owner.

### 2. Write the users file

Put it outside the copy, e.g. `~/.config/latex-live-shared/users.json` (after
`mkdir -m 700 ~/.config/latex-live-shared`), in the format of the README's
[Shared mode](README.md#shared-mode), and keep it private (`chmod 600`;
latex-live warns while others can read it).

- **Names:** each key is the name the proxy signs that person in as, exactly.
  With personal links only, any short name will do.
- **Yourself:** add yourself with role `owner`. Through the proxy you are then
  the owner, and your name and email commit coauthors' edits.
- **Roles:** give each person the least role they need, and treat editors as
  trusted coauthors (see [Security notes](#security-notes)).
- **Note macros:** `macro` must be a note macro that the paper defines, the
  way it defines the others, e.g. `\newcommand{\cy}[1]{\todotxt{[CY: #1]}}`.
  Add the missing ones to the paper, in the copy too, before you start the
  instance. Without one, a coauthor can read but is told "your note macro \cy
  is not defined in the paper" when commenting.
- **Personal links:** give each such person a `token`. This puts one in the
  file without printing it:

  ```bash
  python3 - ~/.config/latex-live-shared/users.json cy <<'EOF'
  import json, secrets, sys
  path, user = sys.argv[1:]
  with open(path) as f:
      data = json.load(f)
  data["users"][user]["token"] = secrets.token_urlsafe(18)
  with open(path, "w") as f:
      json.dump(data, f, indent=2)
  EOF
  ```

  Their link is the public URL followed by `/?t=` and their token. Send it
  privately: anyone with the link is that person. Deleting the token ends that
  access at once.

latex-live reads the file again whenever it changes, so changes need no
restart. While an edit leaves it broken, the last good version stays, and the
output says why.

### 3. Run it

From the copy's paper folder (the same subfolder as in your checkout, if the
paper isn't at the top of the repository), on a free port outside the ones
your own previews use (44100–44110, or the same block from the number in
`~/.config/latex-live/port`, if you moved them), e.g. 44120. Always give it
with `--port`: without it, the instance would take the hub's port (44100, or
`$LATEX_LIVE_PORT`).

Behind an authenticating proxy that names the user in a header:

```bash
LATEX_LIVE_USERS=~/.config/latex-live-shared/users.json \
LATEX_LIVE_STATE=~/.config/latex-live-shared \
LATEX_LIVE_PUBLIC_URL=https://paper.example.org \
LATEX_LIVE_IDENTITY_HEADER=x-remote-user \
LATEX_LIVE_PROXY_HOST=proxy.internal \
LATEX_LIVE_PROXY_UID=0 \
LATEX_LIVE_COMMIT=1 \
python3 ~/.local/share/latex-live/live.py --port 44120 --no-open
```

- Point the proxy at `http://127.0.0.1:44120` (or `[::1]:44120`), and have it
  replace any copy of the header that a browser sends.
- `LATEX_LIVE_PUBLIC_URL` is the address coauthors open, as their browsers
  send it: scheme, host, and the port unless it is the default one.
- `LATEX_LIVE_PROXY_HOST` is the `Host` header the proxy sends to latex-live,
  often a backend name rather than the public one.
- `LATEX_LIVE_PROXY_UID` is the uid the proxy runs as (`ps -o uid= -p <its
  pid>`; 0 for root).

With personal links only, leave out the three proxy settings:

```bash
LATEX_LIVE_USERS=~/.config/latex-live-shared/users.json \
LATEX_LIVE_STATE=~/.config/latex-live-shared \
LATEX_LIVE_PUBLIC_URL=https://paper.example.org \
LATEX_LIVE_COMMIT=1 \
python3 ~/.local/share/latex-live/live.py --port 44120 --no-open
```

The reverse proxy or tunnel must run on this machine too, and pass the
browser's `Host` header on; if it sends another one, set
`LATEX_LIVE_PROXY_HOST` to that.

`LATEX_LIVE_STATE` gives the instance a state folder of its own: its own
owner token (the `token` file there), and no place on the hub of your own
previews. It is required: without it (or with your previews' folder), the
instance refuses to start. So is `LATEX_LIVE_PROXY_UID`, whenever
`LATEX_LIVE_IDENTITY_HEADER` is set; the identity header works on Linux only.

Expected output, in order:

1. Warnings, if any, about weak settings, e.g. a users file that others can
   read. Fix what they name.
2. `=== latex-live: shared with the users in <users file>: <public URL>/ ...`,
   the link for coauthors.
3. `=== latex-live: on this machine: http://localhost:44120/ ...`: your own
   link on this machine, without its token, which is in the `token` file it
   names (step 4 uses it).
4. `=== latex-live: each user's edits are committed as theirs after 60 s of quiet (LATEX_LIVE_COMMIT)`
5. `=== latex-live <version>: building <doc>.tex`
6. `=== latex-live: TeX Live 2024 (podman), confined, each run in a container of its own`
7. `=== live-reload: published <doc>.pdf at <time>` once the first build is
   done, and again after each later one (no latexmk output: a shared instance
   runs the TeX tools itself).

### 4. Check it

Without printing a token. As the owner, with this instance's token:

```bash
TOKEN=$(cat ~/.config/latex-live-shared/token)
curl -s -b "latex_live=$TOKEN" http://127.0.0.1:44120/me | grep -o '"role": "[a-z]*"'   # "role": "owner"
```

As a coauthor, the way the proxy asks:

```bash
curl -s -H 'Host: proxy.internal' -H 'x-remote-user: ben' http://127.0.0.1:44120/me | grep -o '"role": "[a-z]*"'   # "role": "editor"
```

With `LATEX_LIVE_PROXY_UID` set, the second prints nothing (it gets a 403)
unless you run it as the proxy's user: that setting keeps other users of the
machine from signing in as anyone. Then open the public URL in a browser, as
a coauthor would: the toolbar badge shows their name and role. Use the public
URL for your own visits too: your browser keeps one latex-live cookie per host
name, whatever the port, so the local link would replace the cookie your own
previews use on `localhost`.

### 5. Keep it running

A systemd user service keeps it running, restarting it whenever it exits. Put
this in `~/.config/systemd/user/latex-live-shared.service`, with your paths
and the settings of step 3 (`%h` is your home folder; systemd doesn't expand
`~`):

```ini
[Unit]
Description=latex-live shared preview

[Service]
WorkingDirectory=%h/papers/paper-shared
Environment=LATEX_LIVE_USERS=%h/.config/latex-live-shared/users.json
Environment=LATEX_LIVE_STATE=%h/.config/latex-live-shared
Environment=LATEX_LIVE_PUBLIC_URL=https://paper.example.org
Environment=LATEX_LIVE_IDENTITY_HEADER=x-remote-user
Environment=LATEX_LIVE_PROXY_HOST=proxy.internal
Environment=LATEX_LIVE_PROXY_UID=0
Environment=LATEX_LIVE_COMMIT=1
ExecStart=/usr/bin/python3 %h/.local/share/latex-live/live.py --port 44120 --no-open
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
```

Then run `systemctl --user daemon-reload && systemctl --user enable --now
latex-live-shared`.

- If `podman` is outside the `PATH` that `systemctl --user show-environment`
  shows, add an `Environment=PATH=...` line.
- With lingering on (see [Keeping the image](#keeping-the-image)), it keeps
  running after you log out, and starts at boot.
- `journalctl --user -u latex-live-shared` shows its output. It prints no
  token, but names the users file and the state folder.
- `systemctl --user stop latex-live-shared` commits pending edits before the
  instance stops. After updating latex-live, use `restart` instead of `stop`.

### 6. Bring the changes back

Coauthors' edits collect on the `shared` branch: with `LATEX_LIVE_COMMIT=1`,
as commits by them, once each one pauses, and when the instance stops. In
your own checkout, review and merge them like any other branch:

```bash
git log --format='%an: %s' ..shared   # what coauthors changed
git merge shared
```

Without `LATEX_LIVE_COMMIT`, commit their edits in the copy yourself first.

To bring your newer work into the copy, stop the instance, merge your branch
(`main` here) into the copy, and start it again:

```bash
systemctl --user stop latex-live-shared   # commits pending edits first
git -C ~/papers/paper-shared merge main
systemctl --user start latex-live-shared
```

Open viewers wait meanwhile and reconnect. Edits that weren't saved yet are
saved then, merged with your changes.

### Security notes

- **This machine only.** A shared instance drops connections from other
  machines without answering. Coauthors reach it only through the proxy or
  tunnel that runs here.
- **The identity header counts only from the proxy.** Any program on this
  machine can connect to the port and send that header, so latex-live trusts
  it only as exactly one header, with the `Host` in `LATEX_LIVE_PROXY_HOST`,
  and from a process of the user in `LATEX_LIVE_PROXY_UID` (which only Linux
  can check: elsewhere the instance refuses the header): set both. Requests
  that carry it ignore tokens and cookies.
- **Host and Origin.** The instance answers only to loopback names, the
  public URL's host and the proxy's `Host`, so a web page can't reach it under
  another name (DNS rebinding). Notes, saves and other actions count only when
  the browser's `Origin` is `LATEX_LIVE_PUBLIC_URL` (or a loopback page, for a
  loopback address), so another site can't act through a coauthor's browser.
  That goes for the two downloads that start a build (without notes, with
  notes in the margin) too: the viewer asks for them that way, and each builds
  at most once per build of the paper.
- **Editors are trusted coauthors.** An editor can change the paper's `.tex`,
  `.bib`, `.sty` and `.cls` files, in effect any such file in the copy (an
  `\input` adds one to the paper). The LaTeX they write can read any file
  below the copy's paper folder and typeset it. Keep nothing in the copy that
  they shouldn't see. Commenters write plain text only. Viewers and
  commenters see the current PDF and what it shows: the notes it shows, not
  the build's error messages, the earlier versions (baselines, diff) or
  where in the sources a figure is (see the README's Shared mode). Editors can also
  compare with any commit that the copy's branch builds on, so its whole
  history is visible to them: share a branch whose history you are happy to
  show (e.g. one squashed commit on top of what you would publish).
- **TeX is confined, and nothing on the host reads its output as code.** No
  latexmk runs (it passes names from the `.aux` to a shell on the host):
  live.py runs pdflatex, bibtex, biber and makeindex itself, with fixed
  arguments, each in a container of its own from the TeX Live image, never
  with the host's TeX. The downloads without notes and with notes in the
  margin, the change-highlight baselines, latexdiff and SyncTeX run there too.
  A container has no network, no capabilities, no new privileges, at most
  4 GB of memory, 2 CPUs, 512 processes, files of 1 GB, and 10 minutes (or
  its caller's shorter deadline), no shell escape, and the paper folder
  read-only, with only the build folder writable and the paper's dot entries,
  at any depth (`.git` and its config, `.env`, ...), hidden. At most three
  run at once, plus the live build. Past `LATEX_LIVE_BUILD_LIMIT` (2 GB unless
  set), the build is stopped and the build folder emptied. TeX opens files only below the paper folder and in its own tree
  (`openin_any=p`), and writes only into the build folder (`openout_any=p`).
  A rebuild stops the run in progress, containers included, and coauthors can
  ask for one at most every 20 seconds; saves in a row make one build, and
  builds start at least 2 seconds apart, for everyone.
- **The build folder is the coauthors'.** TeX can write any file there, so
  the instance keeps the build's state, its errors and what it published in
  memory rather than reading them back, serves the PDF without following
  links, reads the files there only within the folder and up to a bound, and
  never sends its token to a port that a file there names.
- **Host tools.** The pre-submission checks read the PDF with poppler's tools
  on the host (the image has none), under time and memory limits; only
  editors and the owner can run them.
- **Links and tokens.** Anyone with a personal link is that person: send links
  privately, and delete a token to end that access. The instance's owner token
  (in `$LATEX_LIVE_STATE`) opens everything: keep it to yourself.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| ⚠ TeX image missing in the viewer, `is not pulled` in the task output, or double-click does nothing | Pull the image (step 2). No restart is needed. If it keeps disappearing, a cleanup job is probably pruning unused podman images (`podman system prune -a`). See [Keeping the image](#keeping-the-image). |
| `already running for <doc>.tex` | Another instance builds that document, e.g. from another VS Code window, and still answers. It prints that instance's URL; `--replace` stops it and runs here instead. An instance that no longer answers is replaced without asking. `http://localhost:PORT/papers` lists every running preview. |
| "restart to update" in the viewer | latex-live changed on disk (e.g. after a pull) since the task started. Restart the task, then reload. |
| 403 in the browser | Open the full URL with `?t=` once. The token is in `~/.config/latex-live/token`. |
| Stopping the task leaves latexmk running | Your `python3` may be a wrapper that doesn't pass signals on. Use the interpreter itself, e.g. `/usr/bin/python3`. |
| Double-click does nothing | The task must run in a VS Code terminal so that `code` is on `PATH`. |
| `port 44100 is taken, so the hub waits for it` | A preview from before 0.2 (one port per paper) holds 44100: restart every preview task once. Another program holds it: move latex-live for good with `echo 44200 > ~/.config/latex-live/port` (every preview reads it; the previews' own ports follow, from 44201), restart the preview tasks, and update the ports in step 5. Meanwhile each viewer works on its preview's own port, which the task prints; forward that port by hand (the settings above don't forward 44101–44110 on their own). |
| Every `localhost:44100/p/...` viewer hangs | The preview serving the hub is paused (Ctrl+Z in its terminal: run `fg` there) or stuck. Starting a preview of another document (or with `--replace`) replaces a stuck one after about 10 s. |
| `no free port in 44101-44110` | Too many previews: stop old ones (`pgrep -af latex-live/live.py`). |
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
