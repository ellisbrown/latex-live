"""Settings: where latex-live and its state are, the build directory, and shared mode's settings,
from the environment (LATEX_LIVE_*), plus the bounds on reading what TeX writes.

Other modules read these as config.NAME at the time of use, never through `from .config import`:
main changes BUILD and PUBLISH (--build-dir) after the import, and check.py changes SHARED, INSTANCE and
the bounds to test with."""

import os
import re
import secrets
import sys
import urllib.parse


# The checkout (live.py, latexmkrc, tex-exec, the viewer), whatever path latex-live runs through (a symbolic link too).
HERE = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
TEXLIVE_IMAGE = "docker.io/minidocks/texlive:2024-full"
IMAGE_MISSING = f"{TEXLIVE_IMAGE} is not pulled (to fix: podman pull {TEXLIVE_IMAGE})"
# Build directory, relative to the paper root (--build-dir; LATEX_LIVE_BUILD for latexmkrc,
# latex_report.py, and forward.py). With a non-default one, <doc>.pdf is published there too,
# so a second instance can run beside the usual build without touching its files.
BUILD = os.environ.get("LATEX_LIVE_BUILD", "build")
PUBLISH = "" if BUILD == "build" else BUILD
# Per-user state, shared by every checkout (a pinned version and a development one see the same
# token and the same instances): the token, and one <pid>.json per running instance (record_port).
# (Absolute: main changes into the paper root.)
DEFAULT_STATE = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "latex-live")
STATE = os.path.abspath(os.environ.get("LATEX_LIVE_STATE") or DEFAULT_STATE)
TOKEN_FILE = os.path.join(STATE, "token")
INSTANCES = os.path.join(STATE, "instances")
OLD_INSTANCES = os.path.join(HERE, "instances")  # (where instances of 0.1.0 list themselves)
HUB_LOCK, HUB_FILE = os.path.join(STATE, "hub.lock"), os.path.join(STATE, "hub.json")  # (hub_watch)
with open(os.path.join(HERE, "VERSION")) as f:
    VERSION = f.read().strip()  # (tagged v<VERSION> on release)


def default_port():
    """The hub's port when --port isn't given: $LATEX_LIVE_PORT, else STATE/port, else 44100. A
    setting every preview reads (e.g. when another program holds 44100), so that whichever one
    serves the hub binds the same port."""
    for value in (os.environ.get("LATEX_LIVE_PORT"), read_text(os.path.join(STATE, "port"))):
        if value and value.strip().isdigit():
            return int(value)
    return 44100


def read_text(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


# Shared mode (LATEX_LIVE_USERS, a users file: load_users): this instance serves one paper to its
# coauthors, each with a role (ROLES), who reach it through an authenticating reverse proxy on this
# machine that names them in LATEX_LIVE_IDENTITY_HEADER, or with a personal link (their token). It
# answers on this machine only (LoopbackServer), takes no part in the hub, serves exactly --port, and
# builds without latexmk, each TeX run in a container of the TeX Live image (shared_build, tex-exec with
# LATEX_LIVE_SANDBOX). It needs a state folder of its own (LATEX_LIVE_STATE), and on Linux, with the
# identity header, the proxy's uid. Optional settings:
# - LATEX_LIVE_PUBLIC_URL: the origin browsers use, e.g. https://paper.example.org or
#   http://host.example:8443; POSTs must come from it, its host is allowed, and it is printed;
# - LATEX_LIVE_PROXY_HOST: the exact Host the proxy sends (host or host:port); identity-header
#   requests must carry it;
# - LATEX_LIVE_PROXY_UID: trust the identity header only on connections from a socket of this uid;
# - LATEX_LIVE_COMMIT=1: commit each user's saves and notes to git as theirs (commit_loop), once
#   they have been quiet for LATEX_LIVE_COMMIT_IDLE seconds (60 by default).
USERS_FILE = os.path.abspath(os.environ["LATEX_LIVE_USERS"]) if os.environ.get("LATEX_LIVE_USERS") else None  # (main changes into the paper root)
SHARED = bool(USERS_FILE)
PUBLIC_URL = os.environ.get("LATEX_LIVE_PUBLIC_URL", "").strip()
IDENTITY_HEADER = os.environ.get("LATEX_LIVE_IDENTITY_HEADER", "").strip()
PROXY_HOST = os.environ.get("LATEX_LIVE_PROXY_HOST", "").strip()
HOST_RE = re.compile(r"(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9._-]+)(?::[0-9]{1,5})?")  # a Host header's value


def public_origin(url):
    """(origin, host name) of LATEX_LIVE_PUBLIC_URL, the origin as browsers send it (scheme://host,
    plus :port unless it is the scheme's default); ValueError unless url is a bare http(s) origin."""
    try:
        parts = urllib.parse.urlsplit(url[:-1] if url.endswith("/") else url)
        port = parts.port
    except ValueError:
        parts = None
    if (not parts or parts.scheme not in ("http", "https") or not parts.hostname or "@" in parts.netloc
            or parts.path or parts.query or parts.fragment):
        raise ValueError(f"LATEX_LIVE_PUBLIC_URL must be an origin such as https://paper.example.org, not {url!r}")
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    default = {"http": 80, "https": 443}[parts.scheme]
    return f"{parts.scheme}://{host}" + (f":{port}" if port and port != default else ""), parts.hostname


SHARED_PROBLEMS = []  # (main stops on these in shared mode; forward.py imports this module, so not here)
try:
    PUBLIC_ORIGIN, PUBLIC_HOST = public_origin(PUBLIC_URL) if PUBLIC_URL else (None, None)
except ValueError as e:
    PUBLIC_ORIGIN = PUBLIC_HOST = None
    SHARED_PROBLEMS.append(str(e))
PROXY_UID = os.environ.get("LATEX_LIVE_PROXY_UID", "").strip() or None
if PROXY_UID is not None and re.fullmatch(r"[0-9]{1,10}", PROXY_UID):
    PROXY_UID = int(PROXY_UID)
elif PROXY_UID is not None:
    SHARED_PROBLEMS.append(f"LATEX_LIVE_PROXY_UID must be the number of the user the proxy runs as, not {PROXY_UID!r}")
    PROXY_UID = None
if PROXY_HOST and not HOST_RE.fullmatch(PROXY_HOST):
    SHARED_PROBLEMS.append(f"LATEX_LIVE_PROXY_HOST must be a Host header's value (host or host:port), not {PROXY_HOST!r}")
if IDENTITY_HEADER and not re.fullmatch(r"[A-Za-z0-9_-]+", IDENTITY_HEADER):
    SHARED_PROBLEMS.append(f"LATEX_LIVE_IDENTITY_HEADER must be a header name, e.g. x-remote-user, not {IDENTITY_HEADER!r}")
COMMIT = SHARED and os.environ.get("LATEX_LIVE_COMMIT", "").strip() == "1"
if os.environ.get("LATEX_LIVE_COMMIT", "").strip() not in ("", "0", "1"):
    SHARED_PROBLEMS.append(f"LATEX_LIVE_COMMIT must be 1 (or 0), not {os.environ['LATEX_LIVE_COMMIT']!r}")
COMMIT_IDLE = os.environ.get("LATEX_LIVE_COMMIT_IDLE", "").strip() or "60"
if re.fullmatch(r"[0-9]{1,6}(\.[0-9]+)?", COMMIT_IDLE):
    COMMIT_IDLE = float(COMMIT_IDLE)
else:
    SHARED_PROBLEMS.append(f"LATEX_LIVE_COMMIT_IDLE must be a number of seconds, not {COMMIT_IDLE!r}")
    COMMIT_IDLE = 60.0
BUILD_LIMIT = os.environ.get("LATEX_LIVE_BUILD_LIMIT", "").strip() or "2048"  # (MB: watch_build_size)
if re.fullmatch(r"[0-9]{1,7}", BUILD_LIMIT) and int(BUILD_LIMIT) > 0:
    BUILD_LIMIT = int(BUILD_LIMIT) << 20
else:
    SHARED_PROBLEMS.append(f"LATEX_LIVE_BUILD_LIMIT must be a number of MB, not {BUILD_LIMIT!r}")
    BUILD_LIMIT = 2048 << 20
INSTANCE = f"{os.getpid()}-{secrets.token_hex(4)}"  # (tex-exec labels a shared instance's containers with it)


# The warm standby (standby.py): a pdflatex run that has read the preamble already and waits for the next pass
# of the live build. On unless LATEX_LIVE_STANDBY=0; never in shared mode, which builds without latexmk.
STANDBY = os.environ.get("LATEX_LIVE_STANDBY", "").strip() != "0"
# And a second one for the pass without notes (latex_report.py notes_free_pass): on with STANDBY, unless
# LATEX_LIVE_CLEAN_STANDBY=0.
CLEAN_STANDBY = STANDBY and os.environ.get("LATEX_LIVE_CLEAN_STANDBY", "").strip() != "0"

# LATEX_LIVE_TIMING=1: print when each phase of each build happened, from the save to the published PDFs, and have
# the viewer log when it noticed, fetched, parsed and drew each new PDF in the browser's console (timing).
TIMING = os.environ.get("LATEX_LIVE_TIMING", "").strip() == "1"


# LATEX_LIVE_PROTECTED, a regular expression, marks text that must stay as written, e.g.
# r"\\ch\{human-written\}" at the end of a paragraph or inside a caption: a note written from the viewer
# goes after the paragraph or caption it marks, and the marker itself can't be changed there (note_writer).
try:
    PROTECTED_RE = re.compile(os.environ["LATEX_LIVE_PROTECTED"]) if os.environ.get("LATEX_LIVE_PROTECTED") else None
except re.error as e:  # (warn, not exit: forward.py imports this module too)
    print(f"=== latex-live: ignoring LATEX_LIVE_PROTECTED, not a regular expression ({e})", file=sys.stderr, flush=True)
    PROTECTED_RE = None


# What the server reads of a file that TeX (or BibTeX, Biber) wrote: the paper's TeX decides how big it is,
# so every read is bounded. READ_LIMIT for one file (an .aux, an .fls), AUX_BUDGET for the .aux files one
# reads (\@input), JSON_LIMIT for a small JSON file, RECORD_LIMIT for the records parsed from one.
READ_LIMIT, AUX_BUDGET, JSON_LIMIT, RECORD_LIMIT = 16 << 20, 64 << 20, 1 << 20, 2_000_000
# The most of TeX's output that the downloads read: of a log, an .aux or a .bbl, which they read whole, and of a
# SyncTeX file, uncompressed (synctex_pages, whose records take about ten times that in memory). The paper decides
# how much TeX writes (in shared mode, an editor's paper): past these, a download fails and says so, rather than
# the server's memory growing with it. A 50-page paper's log is a few hundred KB, and its SyncTeX 3 MB.
OUTPUT_LIMIT, SYNCTEX_LIMIT = 64 << 20, 32 << 20
