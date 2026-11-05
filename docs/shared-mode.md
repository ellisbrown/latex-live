# Shared mode

Shared mode gives coauthors who don't run LaTeX themselves the live preview of
your paper, in their own browsers. They open one link and read the PDF, write
notes as themselves (with their own note macro), and, if they are editors,
edit the LaTeX beside the PDF. Each can download the paper as built, without
its notes, or with its notes in the margin. The preview runs on a separate copy
of the paper (a git worktree on its own branch) and can commit each person's
edits to git as theirs, for you to merge.

A shared instance is a `live.py` run with `LATEX_LIVE_USERS` set to a users
file. It needs rootless podman and the TeX Live image, so set up
[your own previews](setup.md) first.

- [Roles](#roles)
- [How coauthors reach it](#how-coauthors-reach-it)
- [Setting it up](#setting-it-up)
- [The users file](#the-users-file)
- [Settings](#settings)
- [Security model](#security-model)
- [Known gaps](#known-gaps)

## Roles

Each person in the users file has one role, and each role may do what the ones
before it may:

- **viewer:** reads the PDF, the notes it shows and the outline, and downloads
  the PDF: with its notes, without them, or with them in the margin;
- **commenter:** also writes notes, as their own note macro and in plain text;
  edits and deletes only their own notes, and replies to and resolves anyone's;
- **editor:** also edits the sources in the browser (notes too, as LaTeX),
  rebuilds (the coauthors together at most every 20 seconds), sees the
  pre-submission checks and the build's error messages, and compares with
  earlier commits of the copy's branch (the diff, and the change highlights
  against the last commit or push);
- **owner:** everything a preview of your own allows, except switching to
  another document: a shared instance serves one.

Viewers and commenters see the current PDF and what it shows, nothing more:

- the notes the PDF shows (not those in `\iffalse` or a switch that is off,
  nor any while `\todotxt` hides them);
- whether the build failed and how many errors it had, not the messages, which
  quote the sources;
- the figures without their labels and source lines, and not the list of
  figure files, which marks changed figures;
- forward search only to the notes they see, and SyncTeX's lines without the
  names of files the source pane doesn't open.

The earlier versions (the baselines and the diff) are for editors and the
owner. The download with notes in the margin shows every note outside a false
branch, even while `\todotxt` hides them in the PDF.

A badge in the toolbar shows each person their name and role. Double-clicking
the PDF opens the source pane for editors and names the source line for
others. The first time a coauthor opens the preview, a short welcome guide for
their role explains the live PDF, commenting, editing, finding your way, the
downloads, and where their changes go; "Welcome guide" in the ⋯ menu reopens
it.

## How coauthors reach it

The instance answers only connections from this machine, at most 64 at a
time. Coauthors reach it in one of two ways:

- through an authenticating reverse proxy on this machine, which names each
  signed-in user in a request header (Linux only);
- with a personal link each, through a reverse proxy or tunnel on this machine
  that doesn't sign anyone in.

## Setting it up

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
  [Security model](#security-model)), and a worktree holds only the branch's
  files: git's own folder, with your remotes' settings, stays outside it;
- with `LATEX_LIVE_COMMIT=1`, each person's edits become commits on `shared`,
  authored by them, which you merge like any other branch (step 6).

Editors can compare with any commit that the copy's branch builds on, so its
whole history is part of what you share. Share a branch whose history you are
happy to show (e.g. one squashed commit on top of what you would publish).

While it runs, leave the copy's files to latex-live: a commit takes whole
files, so a change you made there by hand would go in as a coauthor's. Edit
in your own checkout and merge (step 6), or in the shared viewer as the
owner.

### 2. Write the users file

Put it outside the copy, e.g. `~/.config/latex-live-shared/users.json` (after
`mkdir -m 700 ~/.config/latex-live-shared`), in the format of
[The users file](#the-users-file), and keep it private (`chmod 600`;
latex-live warns while others can read it).

- **Names:** each key is the name the proxy signs that person in as, exactly.
  With personal links only, any short name will do.
- **Yourself:** add yourself with role `owner`. Through the proxy you are then
  the owner, and your name and email commit coauthors' edits.
- **Roles:** give each person the least role they need, and treat editors as
  trusted coauthors (see [Security model](#security-model)).
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

### 3. Run it

From the copy's paper folder (the same subfolder as in your checkout, if the
paper isn't at the top of the repository), on a free port outside the ones
your own previews use (44100–44110, or the same block from the number in
`~/.config/latex-live/port`, if you moved them), e.g. 44120. `--port` is
required: the instance serves exactly that port, never the hub.

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

The instance refuses to start without a state folder of its own
(`LATEX_LIVE_STATE`, not your previews' one), with `LATEX_LIVE_IDENTITY_HEADER`
but without `LATEX_LIVE_PROXY_UID` or off Linux, with `--host-tex`, or without
podman.

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
- With lingering on (see [Keeping the image](setup.md#keeping-the-image)), it
  keeps running after you log out, and starts at boot.
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

## The users file

The users file is JSON, readable only by you (mode 0600). It is read again
whenever it changes, so adding or removing someone needs no restart; while an
edit leaves it broken, the last good version stays, and the output says why.

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

Two people can't share a macro or a token.

## Settings

A shared instance reads these environment variables, beside those in
[setup.md](setup.md#environment-variables) (`LATEX_LIVE_HOST` doesn't apply:
it answers this machine only).

- `LATEX_LIVE_USERS`: the users file, which turns on shared mode, e.g.
  `~/.config/latex-live-shared/users.json`.
- `LATEX_LIVE_STATE`: a state folder of its own, e.g.
  `~/.config/latex-live-shared`, for its own owner token (the `token` file
  there) and no place on the hub of your own previews. Required; the default
  folder is refused, since there it would share your previews' token.
- `LATEX_LIVE_PUBLIC_URL`: the origin that coauthors' browsers use (scheme,
  host and port, no path), e.g. `https://paper.example.org`. Notes, saves and
  other actions are accepted only from pages there, and it is the link the
  instance prints. Without it, coauthors who come through a proxy can read but
  not comment or edit.
- `LATEX_LIVE_IDENTITY_HEADER`: the header in which an authenticating reverse
  proxy names the signed-in user, e.g. `x-remote-user`. Without it, coauthors
  use personal links.
- `LATEX_LIVE_PROXY_HOST`: the exact `Host` header that the proxy sends (`host`
  or `host:port`), e.g. `proxy.internal`. The identity header counts only on
  requests with this Host.
- `LATEX_LIVE_PROXY_UID`: the user id the proxy runs as, e.g. `0`. The identity
  header then counts only on connections from that user's processes. Required
  with `LATEX_LIVE_IDENTITY_HEADER`.
- `LATEX_LIVE_COMMIT`: `1` commits each person's saves and notes to git as
  theirs (with the owner as committer) once they pause. It has no effect
  outside shared mode.
- `LATEX_LIVE_COMMIT_IDLE`: the seconds of quiet after a person's last edit
  before it is committed (default 60).
- `LATEX_LIVE_BUILD_LIMIT`: the most disk space the build folder may take, in
  MB (default 2048). Past it, the build is stopped and the build folder
  emptied, and the viewer says so.

## Security model

Coauthors who may edit write the LaTeX that this machine compiles, so a shared
instance treats the paper and everything TeX writes as theirs.

- **This machine only.** It drops connections from other machines without
  answering, and serves exactly `--port`, never the hub of your own previews.
  Its pages can't be framed by another site.
- **The identity header counts only from the proxy.** Any program on this
  machine can connect to the port and send that header, so latex-live trusts
  it only as exactly one header, with the `Host` in `LATEX_LIVE_PROXY_HOST`,
  and from a process of the user in `LATEX_LIVE_PROXY_UID` (which only Linux
  can check: elsewhere the instance refuses the header). Requests that carry it
  ignore tokens and cookies.
- **Host and Origin.** The instance answers only to loopback names, the public
  URL's host and the proxy's `Host`, so a web page can't reach it under another
  name (DNS rebinding). Every POST counts only when the browser's `Origin` is
  `LATEX_LIVE_PUBLIC_URL` (or a loopback page, for a loopback address), so
  another site can't act through a coauthor's browser. Nothing acts or starts a
  build on a GET: the downloads built on request and the change-highlight
  baselines start only with such a POST, each at most once per build of the
  paper and one at a time, however many ask.
- **Editors are trusted coauthors.** An editor can change the paper's `.tex`,
  `.bib`, `.sty` and `.cls` files, in effect any such file in the copy (an
  `\input` adds one to the paper). The LaTeX they write can read any file below
  the copy's paper folder and typeset it. Keep nothing in the copy that they
  shouldn't see. Commenters write plain text only.
- **No latexmk.** latexmk is a Perl program on the host that reads the files
  TeX writes, and it passes the bibliography names in the `.aux` to a shell, so
  a crafted name would run a command here. live.py itself watches the paper's
  files and runs pdflatex, and bibtex, biber or makeindex where the paper uses
  them, with fixed arguments, as latexmk would (up to six passes, until the
  references settle). A tool that failed runs again on every build until it
  succeeds, and its log's last lines are in the build's errors. It also watches
  the bibliographies and styles that the `.aux` or `.bcf` names (in any
  folder), and files a build missed or that an `\input` or `\include` names
  before they exist. Saves in a row make one build: one starts once the files
  have been quiet for a second (at most 10 seconds after the first change), and
  never within 2 seconds of the last. Other tools that a paper's own
  `latexmkrc` would add (makeglossaries, say) don't run, and a `latexmkrc` in
  the paper is not read.
- **Every TeX run is confined.** Each run is a container of its own from the
  TeX Live image, never the host's TeX: pdflatex, bibtex, biber, makeindex, the
  downloads, the baselines, latexdiff and SyncTeX. A container has no network,
  no capabilities, no new privileges, no shell escape, at most 4 GB of memory,
  2 CPUs, 512 processes, files of 1 GB, and 10 minutes (or its caller's shorter
  deadline). The paper folder is mounted read-only, with only the build folder
  writable and the paper's dot entries at any depth (`.git`, whose config may
  hold a remote's credentials, `.env`, `chapters/.private/`, ...) hidden. A
  paper with a dot entry whose name holds a newline, `:` or `,`, or with more
  than 1000 dot entries, doesn't build. TeX opens files only below the paper
  folder and in its own tree (`openin_any=p`), and writes only into the build
  folder (`openout_any=p`). Builds and downloads share three container slots,
  and the live build has one of its own. A rebuild stops the run in progress,
  its containers too, and so does stopping the instance.
- **The build folder is the coauthors'.** TeX can write any file there, so the
  instance keeps the build's state, its errors and what it published in memory
  rather than reading them back, serves the PDF without following links, reads
  the files there (the `.aux` files and the ones they name, file lists,
  SyncTeX, logs, JSON) only within the folder, without links, and up to a
  bound, and never sends its token to a port that a file there names. The build
  folder may grow to `LATEX_LIVE_BUILD_LIMIT`, checked every second (with
  `--no-build` too); past it, it is emptied but for the instance's own lock
  file, and the build tried again a few times.
- **Host tools.** The pre-submission checks read the PDF with poppler's tools
  on the host (the image has none), under time and memory limits, so only
  editors and the owner can run them.
- **Links and tokens.** Anyone with a personal link is that person: send links
  privately, and delete a token to end that access. The instance's owner token
  (in `$LATEX_LIVE_STATE`) opens everything: keep it to yourself. The instance
  prints no token.

## Known gaps

- **No podman, no shared mode.** It needs rootless podman and the TeX Live
  image, and never falls back to the host's TeX, so it doesn't run where there
  is no podman (e.g. a Mac with MacTeX only). Its containers' memory and CPU
  limits need cgroup v2 with those controllers delegated to your user
  ([step 0](#0-check-podmans-limits)).
- **The identity header is Linux only.** `LATEX_LIVE_PROXY_UID` looks the
  connection's user up in Linux's `/proc/net`; elsewhere, coauthors use
  personal links.
- **A killed server** (`kill -9`) can't stop its containers; each still stops
  itself after 10 minutes.
- **No real-time co-editing.** Edits reach others when they are saved, a
  moment after typing stops; each save is merged with what others saved
  meanwhile, and edits to the same lines become a conflict to settle.
