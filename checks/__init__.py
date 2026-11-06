"""The smoke test for latex-live, which check.py runs (python3 check.py: a minute or two; needs TeX, as live.py
does).

1. The Python compiles without warnings (and with Python 3.9, if there is one); the viewer's import
   map is valid JSON, comes before its module code, and maps every bare module name that the viewer
   (viewer/) and the vendored editor (codemirror/) import to a file here, and every module that the
   viewer imports by path is a file here; the viewer's modules parse (with Node, if there is one);
   instances starting at once agree on one token, never an empty one.
   The review copy's layout keeps notes in order, apart, and on their pages, and continues the ones
   that don't fit after the paper; its matching tells apart two authors' notes with the same text.
   In shared mode, the downloads built on request say plainly that the TeX Live image is missing, the
   review copy reads its notes from the files of the last good build (only the paper's own), and
   the inputs kept for its probe leave out symbolic links; shared mode's builder prints the page
   layout as latexmkrc does, runs biber again when a data source changes, and reads a chapter's .aux
   for bibtex. LATEX_LIVE_TIMING=1's lines leave out the events of a notes-free pass that a newer run superseded.
   The editor and the notes never read or write a source through a folder or file swapped for a link after the
   check.
2. A scratch paper in a temporary directory (two columns, lineno line numbers, inline notes, a git
   repository with a pushed commit and a local one) gets a live.py instance, with the hub on port
   44180, or below the ports that outgoing connections take where 44180 is among them (Linux: 28180), or the first
   free block of 24 ports above (LATEX_LIVE_CHECK_PORT sets it), state of its own
   (not the user's) and none of the user's LATEX_LIVE_* settings:
   - the build publishes main.pdf, and the notes-free pass main-clean.pdf;
   - the hub redirects an old bookmark, needs the token or its cookie, and forwards every
     endpoint (POST /rebuild too); the hub and the previews refuse HEAD and bad or oversized POST
     bodies; SyncTeX finds a source line; /instances lists the instance;
   - /me reports the owner with every capability; the viewer's and the editor's modules and the
     viewer's style are served (as JavaScript, and CSS), the viewer's revalidated; SyncTeX answers
     as JSON without opening the editor, while GET /open, POST /open and plain /synctex still open
     it (a stand-in that logs its arguments); the hub takes a source file's save above the other
     actions' size limit, refuses bodies above its limits, and survives a non-ASCII token;
     /favicon.ico is answered, not refused;
   - the HEAD and last-push baselines build (they differ: one commit is unpushed), and the
     notes-free baseline's line numbers match the live notes-free build's (two passes with lineno);
   - the download without notes is an exact build (its line numbers match the notes-free view's),
     made once per build of the document, and the review copy places its pages on wider ones with
     every note in a gutter (none overlapping, the protected marker and the resolved note left out),
     and its last pass loads no package that the paper's TeX wrote; a document without notes gets a
     clear failure, not a file;
   - POST /switch moves the preview to another document and GET /switch back, and POST /diff and
     GET /diff build a latexdiff (the viewer's POST forms and the old GET ones, through the hub);
   - a second document runs in its own process behind the same port, and /papers links both; the
     hub, refused by a preview that a stale entry says is another paper's, looks again and doesn't
     misroute; after a switch, the old document's viewers still reach their preview;
   - a second instance for the same document says it is already running; --replace takes over,
     and so does any new instance when the running one no longer answers; the hub moves to
     another instance on the same port, and a hub that no longer answers is replaced;
   - stopping the instance removes it from /instances, and no instance's warm standby leaves a keeper, a
     container or a folder behind.
   Beside these, in a thread of its own, the warm standby (standby_run) on a paper and an instance of its own,
   whose preamble reads a file and typesets a box: once a body edit runs from the standby, its PDF has a normal
   pass's pages and text; both passes of an edit that adds a reference run from it (and with LATEX_LIVE_TIMING=1,
   its timing line names each phase in order, and /status gives the server's clock); a preamble edit runs normally
   (verifying a new standby beside it) and the next edits use the standby, and so does a change to the file the
   preamble reads; an error in the body is reported at its file and line; forward and inverse search work after a
   pass from the standby (also on the box); killing live.py (kill -9) stops its standbys, and so does stopping it,
   leaving no keeper, container or folder; LATEX_LIVE_STANDBY=0 builds without standbys.
   And in another thread, the early notes-free pass (early_run), on a paper with notes and line numbers and an
   instance of its own without the standby: a body edit's early pass is kept, with the PDF (but dates) and SyncTeX
   of the notes-free pass after the run; a two-pass edit keeps its second pass's; a failed run puts build/clean/ back.
   And in a third, the notes-free pass's warm standby (clean_standby_run), on a paper with notes and an instance of
   its own: a body edit's notes-free pass runs from it, with a normal notes-free pass's PDF (but dates), pages, text
   and SyncTeX, and forward and inverse search of a body line in the notes-free PDF work; a preamble edit that
   adds line numbers runs normally, and then both runs of a notes-free pass (lineno) come from standbys and match a
   normal pass; an early pass stopped while it runs from a standby (its run failed) leaves no keeper, container or
   folder of that standby and build/clean/ as it was; LATEX_LIVE_STANDBY=0 builds the notes-free PDF without one.
3. A shared-mode instance (LATEX_LIVE_USERS) on the hub's port + 11 serves a clone of the paper on branch
   "shared" to users of every role, behind a stand-in for an authenticating proxy (an identity
   header), with personal-link tokens, auto-commit, protected text and the editor stand-in. Shared
   mode builds only in the TeX Live image: where podman or the image is missing, all of this part
   but its first two checks (which need neither) is skipped, with a "skip:" line.
   - tex-exec and --host-tex refuse the host's TeX, and tex-exec's container options are hardened
     (the owner's uid, the paper and the root file system read-only, limits, labels, 10 minutes at
     most, seccomp left on); an instance refuses the default
     state folder, and on Linux the identity header without LATEX_LIVE_PROXY_UID; it builds in the
     hardened image without latexmk (a \\bibdata name that is a shell command, which latexmk would
     run on the host, runs nothing; the paper's latexmkrc doesn't run; TeX can't read absolute
     paths), serves exactly its port and only loopback peers, prints its public URL and no token
     anywhere (nor does a second instance that finds it running), and stays out of the hub; edits
     it finds uncommitted at the start are committed as the owner's;
   - Host names, the identity header (none, two, empty, unknown, on another Host), tokens and
     cookies, a page that says what to do for a browser without a working link, users added (and a
     broken users file) without a restart, /me (whether the paper defines the user's note macro, and
     no document switch, for anyone) and every endpoint per role (the checks for editors only),
     the Origin rules for POSTs, POST body limits, non-owners pinned to the document, and the
     viewer's security headers;
   - a real confined container runs as the owner's uid (not root), on a read-only root file system
     with a sized /tmp, with podman's seccomp filter, no new privileges, capabilities or swap, and
     leaves files the owner owns; pdflatex (making and caching a bitmap font), bibtex, biber,
     makeindex, SyncTeX and latexdiff work in it;
   - the download without notes: a viewer may ask; several asking at once share one build, made once
     per build of the document, in the hardened image;
   - the copy with notes in the margin (/review), likewise: a viewer may ask, several asking at once
     share one build, made once per build of the document; another document's copy is refused; its
     probe pass can't read absolute paths either; both downloads start only with a POST from the
     viewer's own page (a GET, or another site's Origin, starts nothing); and the host's TeX never
     runs (a stand-in for it on PATH);
   - notes as oneself only: the editor's own macro yes, the owner's no, others' notes no,
     resolving anyone's yes, protected text kept, a macro the paper lacks explained; notes, the
     list of notes and the note macros keep working while the paper rebuilds;
   - the editor's files API: what it lists and refuses (../, absolute paths, %2e%2e, .git, build/,
     a symlink to the token, a new file, too big, not UTF-8), reading, saving, a stale save that
     merges, a conflict that writes nothing (and says who saved meanwhile), a stale save without and
     then with its base text, a save whose answer got lost followed by more typing on that line
     (from the same tab; another tab's conflicts), CRLF files; status ?open=; SyncTeX as JSON that
     never opens the editor (only the owner's POST open does), with a line past a file's end moved
     to its last line; diffs only against commits that HEAD builds on; editors' rebuilds at most
     every 20 s;
   - a rebuild stops the run in progress and its container (a pass kept busy for a minute); removing
     a file the build reads rebuilds it; a bibtex failure fails the build; a file a failed build missed
     builds it once it appears; a build that fills build/ past LATEX_LIVE_BUILD_LIMIT is stopped and
     build/ emptied; a container sees no dot entry of the paper (.git); the status ignores a status, a .building marker and a record of what was published forged in
     build/; a second instance asks the port in a forged lock file without its token;
   - auto-commit: each user's edits are committed as that user (by the owner) once they are
     quiet, or at the owner's POST commit, only the files the server wrote, on the shared branch
     only, past the paper's hooks and signing settings, and after a git lock goes away; a write to
     a file that holds another user's uncommitted edits waits for their commit (503 while git is
     busy), and every write waits out a merge in the shared copy (503); the HEAD baseline is not
     rebuilt for each commit; the edits build; a GET of the status builds no baseline, a viewer's
     POST baseline does;
   - stopping the instance leaves no container, running or created and never started; an edit still uncommitted when it stops
     (a merge in progress) is committed as its writer's at the next start; restarted with an https public URL, cookies are Secure on its
     Host; the identity header from a peer whose uid is not LATEX_LIVE_PROXY_UID is not trusted
     (Linux).
Exits non-zero on the first failure.

Its modules, from the bottom up (each imports only from those below it): base (where the checks run, and how
they report: ok, fail, wait_for, in_thread), helpers (files, git, instances and requests to them, stand-ins, the TeX
Live image), papers (the scratch paper), static (the checkout as it is), shared_units and units (the server's parts
without an instance), standby, early and clean_standby (the threads beside the instance checks: the warm standby,
the early notes-free pass and its warm standby), shared_helpers, shared_setup, shared_access, shared_sandbox,
shared_downloads, shared_notes, shared_editor, shared_rebuild, shared_commits and shared (shared mode, the other thread), preview (the
instance checks, which start the threads), and main.
"""
