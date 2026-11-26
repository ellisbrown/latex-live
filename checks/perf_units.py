"""The unit checks of what the status poll and the viewer ask for over and over, without an instance (stand-ins for
SyncTeX and poppler): the bibliography's SyncTeX lookups, which merge while a build follows another; inverse search's
cache; the SyncTeX file read once per build for every lookup; the caches that requests share; the change baselines'
git calls, made only when a ref may have moved; the margin notes' points; LATEX_LIVE_ANON_TERMS; poppler's limits;
stopping the notes-free pass before the warm standbys, and telling when its process group has ended."""

import gzip
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import types

from .base import HERE, fail, ok, wait_for
from .helpers import fake_server, git, patched, put, put_bytes


def perf_checks():
    for check in (bib_span_checks, bib_merge_checks, inverse_cache_checks, synctex_text_checks, cache_race_checks,
                  ref_stamp_checks, margin_points_checks, anon_terms_checks, poppler_limit_checks, bounded_cache_checks,
                  stop_notes_free_checks, group_alive_checks):
        check()


SYNCTEX_STUB = """#!/bin/sh
# A stand-in for synctex view -i LINE:0:FILE -o PDF: a box for lines 1 and 7 of FILE, none for the others. Line 1 takes
# DELAY seconds (a file beside it), as a container's start would.
echo "$3" >> "$(dirname "$0")/calls"
line=${3%%:*}
[ "$line" = 1 ] && [ -f "$(dirname "$0")/delay" ] && sleep "$(cat "$(dirname "$0")/delay")"
echo "This is SyncTeX command line utility, version 1.5"
echo "SyncTeX result begin"
case $line in
  1) printf 'Output:%s\\nPage:3\\nx:1\\ny:2\\nh:10.5\\nv:200.0\\nW:50\\nH:12.0\\nd:3\\n' "$5" ;;
  7) printf 'Output:%s\\nPage:5\\nx:1\\ny:2\\nh:72.0\\nv:400.5\\nW:50\\nH:10.0\\nd:3\\n' "$5" ;;
esac
echo "SyncTeX result end"
"""
WANT = ({"page": 3, "x": 10.5, "y": 188.0}, {"page": 5, "x": 72.0, "y": 390.5})


class StubSyncTeX:
    """A paper folder (with a space in its path, which the batched runs must quote) with a build whose SyncTeX lookups
    go to SYNCTEX_STUB (on PATH), counting the tool runs."""

    def __enter__(self):
        from latexlive import synctex
        self.synctex, self.tmp, self.path, self.runs = synctex, tempfile.mkdtemp(prefix="latex-live check "), os.environ.get("PATH", ""), []
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        put(os.path.join(self.bin, "synctex"), SYNCTEX_STUB)
        os.chmod(os.path.join(self.bin, "synctex"), 0o755)
        os.environ["PATH"] = f"{self.bin}{os.pathsep}{self.path}"
        build = os.path.join(self.tmp, "build")
        self.files = [os.path.join(build, name) for name in ("main-live.synctex.gz", "main-live.bbl", "main-live.pdf")]
        put(self.files[0], "")
        put(self.files[1], "x\n" * 9)  # (10 lines: 1, then 10 down to 3 are looked up)
        run_tool = synctex.run_tool
        self.patch = patched(synctex, run_tool=lambda *a, **k: (self.runs.append(1), run_tool(*a, **k))[1], BIB_SPANS={})
        self.patch.__enter__()
        return self

    def calls(self):
        try:
            with open(os.path.join(self.bin, "calls")) as f:
                return f.read().splitlines()
        except FileNotFoundError:
            return []

    def __exit__(self, *_):
        self.patch.__exit__(None, None, None)
        os.environ["PATH"] = self.path
        shutil.rmtree(self.tmp, ignore_errors=True)


def bib_span_checks():
    """Where the bibliography starts and ends (bib_span, with a stand-in for synctex): its lookups of the first line
    and the last eight run in one SyncTeX run (run_synctexes), each with the records it gets alone (run_synctex), once
    per build; without wait, refs_start answers PENDING at once while the lookup runs in a worker, then the start."""
    with StubSyncTeX() as stub:
        synctex, files = stub.synctex, stub.files
        runs = [("view", "-i", f"{line}:0:{files[1]}", "-o", files[2]) for line in (1, 2, 7)]
        alone = [synctex.run_synctex(stub.tmp, *args) for args in runs]
        if synctex.run_synctexes(stub.tmp, runs) != alone or [len(r) for r in alone] != [1, 0, 1] or alone[0][0].get("Page") != "3":
            fail(f"SyncTeX lookups in one run differ from each alone: {synctex.run_synctexes(stub.tmp, runs)} vs {alone}")
        os.remove(os.path.join(stub.bin, "calls"))
        del stub.runs[:]
        if synctex.bib_span(stub.tmp, files, cache={}) != WANT:
            fail(f"bib_span with the stand-in: {synctex.bib_span(stub.tmp, files, cache={})}")
        if stub.calls() != [f"{n}:0:{files[1]}" for n in (1, *range(10, 2, -1))] or len(stub.runs) != 1:
            fail(f"bib_span looks up other lines than the first and the last eight, or in more than one run: {stub.calls()},"
                 f" {len(stub.runs)} runs")
        put(files[1], "x\n")  # (2 lines: the second may have no box; never line 1 twice, nor line 0)
        if synctex.bib_span(stub.tmp, files, cache={}) != (WANT[0], None) or stub.calls()[-2:] != [f"{n}:0:{files[1]}" for n in (1, 2)]:
            fail(f"bib_span of a 2-line .bbl: {stub.calls()[-3:]}")
        put(files[1], "x\n" * 9)
        os.utime(files[0], ns=(1, 1_000_000_000))
        if synctex.refs_start(stub.tmp, "main.pdf", wait=False) is not synctex.PENDING:
            fail("refs_start without wait does not answer PENDING while it looks up")
        found = wait_for("refs_start's lookup in a worker", lambda: (lambda r: r if r is not synctex.PENDING else None)(
            synctex.refs_start(stub.tmp, "main.pdf", wait=False)), timeout=20)
        if found != WANT[0]:
            fail(f"refs_start without wait, once looked up: {found}")
        # Shared mode's notes-free pass reads its .bbl in a folder of its own (build/clean-<n>/), which SyncTeX records.
        read = os.path.join(stub.tmp, "build", "clean-3", "main-live.bbl")
        put_bytes(files[0], gzip.compress(f"SyncTeX Version:1\nInput:1:{stub.tmp}/main.tex\nInput:9:{read}\n".encode()))
        os.utime(files[0], ns=(2, 2_000_000_000))
        if synctex.bib_span(stub.tmp, files, cache={}) != WANT or stub.calls()[-9:] != [f"{n}:0:{read}" for n in (1, *range(10, 2, -1))]:
            fail(f"bib_span of a .bbl that TeX read elsewhere (shared mode's notes-free pass) asks SyncTeX about {stub.calls()[-9:]}")
        os.remove(files[1])
        if synctex.refs_start(stub.tmp, "main.pdf", wait=False) is not None or synctex.BIB_WORKERS:
            fail("refs_start without a .bbl waits for a lookup, or starts one")
    ok("bib_span looks up the bibliography's extent in one SyncTeX run (each lookup as alone), and refs_start without wait"
       " answers PENDING at once, then the result, and nothing without a .bbl; it asks about the .bbl by the name TeX read it as")


def bib_merge_checks():
    """While builds follow each other faster than the bibliography's lookups, refs_start without wait runs one worker
    per document and variant, whose lookups take the build on show when they start: far fewer lookups than builds, none
    left once the builds stop, and the newest build's result kept. A lookup that waited for the lock while another
    looked up a newer build never replaces that build's result (the cause of the endless lookups before)."""
    with StubSyncTeX() as stub:
        synctex, files = stub.synctex, stub.files
        put(os.path.join(stub.bin, "delay"), "0.3")
        workers, version, began = 0, 10, time.monotonic()
        for _ in range(30):  # (a new build every 20 ms, each polled at once)
            version += 1
            os.utime(files[0], ns=(1, version * 1_000_000_000))
            if synctex.refs_start(stub.tmp, "main.pdf", wait=False) is not synctex.PENDING:
                fail("refs_start answered a build it had not looked up yet")
            workers = max(workers, len(synctex.BIB_WORKERS))
            time.sleep(0.02)
        took = time.monotonic() - began  # (longer on a slow machine, with more lookups meanwhile; never half the builds)
        found = wait_for("the merged lookups to end", lambda: not synctex.BIB_WORKERS and (lambda r: r if r is not synctex.PENDING else None)(
            synctex.refs_start(stub.tmp, "main.pdf", wait=False)), timeout=30)
        if found != WANT[0] or workers != 1 or len(stub.runs) > min(15, max(5, took / 0.3 + 2)) or synctex.BIB_SPANS[files[0]][0][0] != version * 1_000_000_000:
            fail(f"30 builds polled in {took:.1f} s while each lookup takes 0.3 s: {len(stub.runs)} lookups, {workers} workers at most, found"
                 f" {found}, kept for {synctex.BIB_SPANS[files[0]][0]}")

        os.remove(os.path.join(stub.bin, "delay"))
        del stub.runs[:]
        cache, older = {}, synctex.stamp(files[0])
        synctex.BIB_SPAN_LOCK.acquire()
        try:
            late = threading.Thread(target=lambda: cache.setdefault("late", synctex.bib_span(stub.tmp, files, cache)))
            late.start()  # (it reads the build's version, then waits for the lock)
            time.sleep(0.2)
            os.utime(files[0], ns=(1, (version + 1) * 1_000_000_000))
            cache[files[0]] = (synctex.stamp(files[0]), "the newer build's")  # (as a lookup meanwhile left it)
        finally:
            synctex.BIB_SPAN_LOCK.release()
        late.join(10)
        if cache.get("late") != "the newer build's" or cache[files[0]][1] != "the newer build's" or stub.runs or older == cache[files[0]][0]:
            fail(f"a lookup that waited for the lock replaced a newer build's result: {cache}, {len(stub.runs)} runs")
    ok("the bibliography's lookups merge while builds follow each other (one worker per variant, a few lookups for 30"
       " builds, none after), and a lookup that waited never replaces a newer build's result")


def inverse_cache_checks():
    """Inverse search (navigation.cached_input, as /synctex asks it): a spot is looked up once per build, also a spot
    within half a point of it, and again for a new build; a lookup that found no source line is made again after a
    few seconds; a PDF name that isn't one is refused without a lookup; the owner's non-JSON form goes through it too."""
    from latexlive import navigation
    calls, answers = [], []

    def synctex_input(paper_root, name, page, x, y, clean):
        calls.append((page, x, y))
        return answers.pop(0) if answers else (os.path.join(paper_root, "main.tex"), 3)
    with tempfile.TemporaryDirectory(prefix="latex-live-check-") as tmp, patched(navigation, synctex_input=synctex_input,
                                                                                  editor_cli=lambda: None):
        root = os.path.join(tmp, "paper")
        put(os.path.join(root, "main.tex"), "text\n" * 5)
        synctex_gz = os.path.join(root, "build", "main-live.synctex.gz")
        put(synctex_gz, "")
        server = fake_server(root)
        first = navigation.cached_input(server, "main.pdf", 2, 100.1, 200.2, False)
        again = navigation.cached_input(server, "main.pdf", 2, 100.2, 199.9, False)
        other = navigation.cached_input(server, "main.pdf", 2, 140.0, 200.2, False)
        if first != again or len(calls) != 2 or calls[0] != (2, 100.0, 200.0):
            fail(f"inverse search looked the same spot up again in the same build: {calls}")
        os.utime(synctex_gz, ns=(1, 1))
        navigation.cached_input(server, "main.pdf", 2, 100.1, 200.2, False)
        answers.append((404, "no source location here"))
        miss = [navigation.cached_input(server, "main.pdf", 3, 50, 50, False) for _ in range(2)]
        for entry in server.results.entries.values():  # (as if the few seconds had passed)
            if "expires" in entry:
                entry["expires"] = 0
        found = navigation.cached_input(server, "main.pdf", 3, 50, 50, False)
        bad = navigation.cached_input(server, "../main.pdf", 3, 50, 50, False)
        nan = [navigation.cached_input(server, "main.pdf", 3, x, 50, False)[0] for x in (float("nan"), float("inf"), 1e308)]
        status, text = navigation.synctex_edit(server, {"file": ["main.pdf"], "page": ["2"], "x": ["100.1"], "y": ["200.2"]})
        if (len(calls) != 5 or miss != [(404, "no source location here")] * 2 or found != (os.path.join(root, "main.tex"), 3)
                or bad[0] != 400 or other != first or "main.tex:3" not in text or nan != [400] * 3):
            fail(f"inverse search's cache: {len(calls)} lookups ({calls}), misses {miss}, then {found}, a bad name {bad},"
                 f" the owner's form {status} {text}, spots that are no numbers {nan}")
    ok("inverse search looks a spot up once per build (half-point grain), again a few seconds after it found nothing,"
       " and refuses a bad PDF name without a lookup")


def make_synctex(pages=3):
    """A SyncTeX file's text with an input that appears inside a page, boxes, points and kerns on each page."""
    out = ["SyncTeX Version:1", "Input:1:/paper/main.tex", "Output:pdf", "Content:"]
    for page in range(1, pages + 1):
        out += [f"{{{page}", "[1,1:4736286,4736286:30000000,40000000,0", f"(1,{page + 4}:4736286,9000000:20000000,655360,0",
                f"x1,{page + 4}:4736286,9000000", f"g1,{page + 4}:5736286,9000000", f"k1,{page + 4}:6736286,9000000:65536",
                ")"]
        if page == 2:  # (an input read on the page, and a { line that starts no page)
            out += ["Input:2:/paper/sec.tex", "{x", "(2,3:4736286,19000000:20000000,655360,0", "x2,3:4736286,19000000", ")"]
        out += ["]", f"}}{page}"]
    return "\n".join(out + ["Postamble:", "Count:30", "Post scriptum:"]) + "\n"


def synctex_text_checks():
    """Every lookup in a build's SyncTeX file (synctex_boxes, synctex_points, page_nodes) reads it once per build, also
    when requests ask at once, and finds what reading it record by record finds; a new build is read anew."""
    from latexlive import synctex
    with tempfile.TemporaryDirectory(prefix="latex-live-check-") as tmp:
        synctex_gz = os.path.join(tmp, "main-live.synctex.gz")
        with gzip.open(synctex_gz, "wt") as f:
            f.write(make_synctex())
        reads, read = [], synctex.read_synctex
        with patched(synctex, read_synctex=lambda path: (reads.append(path), time.sleep(0.2), read(path))[2],
                     SYNCTEX_TEXTS=synctex.Kept(4), BOXES=synctex.Kept(4), POINTS=synctex.Kept(4), NODES=synctex.Kept(8)):
            found = {}
            threads = [threading.Thread(target=lambda what=what: found.setdefault(what, getattr(synctex, what)(synctex_gz)))
                       for what in ("synctex_boxes", "synctex_points", "synctex_boxes")]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(10)
            nodes = [synctex.page_nodes(synctex_gz, page) for page in (1, 2, 3, 4)]
            # What reading the file record by record finds (as before the text was kept).
            inputs, boxes, points, page = {}, {}, {}, 0
            for record in gzip.open(synctex_gz, "rt", errors="replace"):
                if record.startswith("Input:"):
                    tag, _, path = record[6:].rstrip("\n").partition(":")
                    inputs[tag] = os.path.normpath(path)
                elif record.startswith("{"):
                    page = synctex.to_int(record[1:], page)
                elif record[0] in "([h" and (m := synctex.SYNCTEX_BOX_RE.match(record)):
                    x, y, w, h, d = (int(v) * synctex.PT for v in m.groups()[3:])
                    boxes.setdefault((inputs.get(m[2]), int(m[3])), []).append((page, *(round(c, 1) for c in (x, y - h, x + w, y + d))))
                elif record[0] in "gkx$" and (m := synctex.SYNCTEX_POINT_RE.match(record)):
                    points.setdefault((inputs.get(m[2]), int(m[3])), []).append((page, round(int(m[4]) * synctex.PT, 1), round(int(m[5]) * synctex.PT, 1)))
            packed = next(iter(found.get("synctex_points", {}).values()), None)
            if packed is None or packed == None or packed[-1] != list(packed)[-1] or packed[:1] != list(packed)[:1]:  # noqa: E711
                fail(f"a source line's packed points don't read as their list: {packed!r}")
            if len(reads) != 1 or found.get("synctex_boxes") != boxes or found.get("synctex_points") != points or not boxes:
                fail(f"the SyncTeX file's lookups read it {len(reads)} times at once, or find other boxes or points: {found}")
            kinds = [[n[0] for n in page_nodes] for _, page_nodes in nodes]
            if kinds != [["[", "(", "x", "g", "k", ")", "]"], ["[", "(", "x", "g", "k", ")", "(", "x", ")", "]"],
                         ["[", "(", "x", "g", "k", ")", "]"], []] or nodes[1][0].get("2") != "/paper/sec.tex":
                fail(f"page_nodes from the kept text: {kinds}, inputs {nodes[1][0]}")
            os.utime(synctex_gz, ns=(1, 1))
            synctex.synctex_points(synctex_gz)
            if len(reads) != 2:
                fail(f"a new build of the SyncTeX file is not read anew: {len(reads)} reads")
    ok("a build's SyncTeX file is read once for every lookup in it (also asked at once), with what reading it record by"
       " record finds, and read anew for a new build")


def cache_race_checks():
    """The caches that request threads share (synctex.Kept) answer every request while others replace and drop
    entries at once, which a cache that clears itself and then reads its own entry back did not (a KeyError now and
    then, on Python 3.9)."""
    from latexlive import synctex
    with tempfile.TemporaryDirectory(prefix="latex-live-check-") as tmp, patched(synctex, NODES=synctex.Kept(8)):
        synctex_gz = os.path.join(tmp, "a.synctex.gz")
        with gzip.open(synctex_gz, "wt") as f:
            f.write(make_synctex(20))
        errors, interval = [], sys.getswitchinterval()

        def work(i):
            for n in range(200):
                try:
                    if synctex.page_nodes(synctex_gz, 1 + (i * 7 + n) % 20)[1] == []:
                        errors.append("no nodes")
                except Exception as e:
                    errors.append(repr(e))
        sys.setswitchinterval(1e-6)
        try:
            threads = [threading.Thread(target=work, args=(i,)) for i in range(16)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(60)
        finally:
            sys.setswitchinterval(interval)
    if errors:
        fail(f"the shared caches failed requests while others changed them: {errors[:3]} ({len(errors)} in all)")
    ok("the caches that request threads share answer every request while others replace and drop entries")


def ref_stamp_checks():
    """The change baselines check git only when a ref may have moved (git.ref_stamp: a commit, a new branch, a push,
    a fetch, a gc, a checkout change it; nothing else does), or a minute has passed: base_status runs no git command
    while the stamp stays the same, and a commit that came while a baseline was building is taken once it is done."""
    from latexlive import baselines, git as git_module
    with tempfile.TemporaryDirectory(prefix="latex-live-check-") as tmp:
        subprocess.run(["git", "init", "-q", "--bare", os.path.join(tmp, "remote.git")], check=True)
        root = os.path.join(tmp, "paper")
        put(os.path.join(root, "main.tex"), "one\n")
        git(root, "init", "-q", "-b", "main")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "first")
        server = fake_server(root, git_dir=None)
        stamps = [git_module.ref_stamp(server)]
        same = git_module.ref_stamp(server) == stamps[0]
        put(os.path.join(root, "main.tex"), "two\n")
        moves = (("commit", "-qam", "second"), ("branch", "side"), ("remote", "add", "origin", os.path.join(tmp, "remote.git")),
                 ("push", "-q", "-u", "origin", "main"), ("pack-refs", "--all"), ("checkout", "-q", "side"))
        changed = []
        for move in moves:
            time.sleep(0.01)  # (a file system's mtimes may be coarse)
            git(root, *move)
            stamps.append(git_module.ref_stamp(server))
            changed.append(stamps[-1] != stamps[-2])
        put(os.path.join(root, "main.tex"), "three\n")  # (an edit moves no ref)
        quiet = git_module.ref_stamp(server) == stamps[-1]
        if not same or not all(changed) or not quiet or git_module.ref_stamp(fake_server(tmp, git_dir=None)) is not None:
            fail(f"the ref stamp: the same twice {same}, changed by {dict(zip((m[0] for m in moves), changed))}, by an edit"
                 f" {not quiet}")
        calls = []
        text = git_module.git_text
        server = fake_server(root, git_dir=None, default_pdf="main.pdf", bases={"head": {}, "push": {}}, base_checked={"head": 0, "push": 0},
                             base_stamps={}, base_locks={"head": threading.Lock(), "push": threading.Lock()})
        with patched(baselines, git_text=lambda *a, **k: (calls.append(a[1]), text(*a, **k))[1]):
            first = baselines.base_status(server, "main.pdf", "head", build=False)
            for _ in range(3):
                server.base_checked["head"] = 0  # (as if the few seconds had passed)
                baselines.base_status(server, "main.pdf", "head", build=False)
            idle = len(calls)
            git(root, "commit", "-q", "--allow-empty", "-m", "third")
            server.base_checked["head"] = 0
            moved = baselines.base_status(server, "main.pdf", "head", build=False)
            server.bases["head"]["state"] = "running"  # (a build of that commit runs; a newer one comes meanwhile)
            git(root, "commit", "-q", "--allow-empty", "-m", "fourth")
            server.base_checked["head"] = 0
            baselines.base_status(server, "main.pdf", "head", build=False)
            server.bases["head"]["state"] = "done"
            server.base_checked["head"] = 0
            later = baselines.base_status(server, "main.pdf", "head", build=False)
        if idle != 1 or len(calls) != 4 or not first or moved["sha"] == first["sha"] or later["sha"] == moved["sha"]:
            fail(f"base_status ran git {idle} times while no ref moved, {len(calls)} in all, and reports {first}, {moved}, then"
                 f" after a build that ran {later}")
    ok("the change baselines run git only once a ref may have moved (a commit, a branch, a push, a fetch, a gc, a"
       " checkout), not on every few seconds' check")


def margin_points_checks():
    """The margin notes' points (notes.spread) keep what the viewer reads of them: the pages, the lines (each y as the
    page is shown, so a turned page's too), the first point on each page and the first and last points; not the rest."""
    from latexlive import notes
    line = [(1, 100.0 + i, 300.0) for i in range(40)] + [(1, 72.0, 312.0)] + [(1, 80.0 + i, 312.0) for i in range(40)]
    turn = {"rotate": 90, "W": 612.0, "H": 792.0, "blocks": []}
    turned = [(2, 300.0, 100.0 + i) for i in range(5)] + [(2, 310.0, 100.0)]  # (shown: y is x as stored)
    kept = notes.spread(dict.fromkeys(line + turned + [(3, 90.0, 80.0), (3, 95.0, 80.0)]), {2: turn})
    want = [[1, 100.0, 300.0], [1, 72.0, 312.0], [2, 300.0, 100.0], [2, 310.0, 100.0], [3, 90.0, 80.0], [3, 95.0, 80.0]]
    if kept != want:
        fail(f"the margin notes' points: {kept}, not {want}")
    ok("the margin notes send one point per line as shown, with the first on each page and the last")


def anon_terms_checks():
    """LATEX_LIVE_ANON_TERMS: one that is not a regular expression (or not after the authors' names) is ignored with a
    warning, and the anonymity check says why instead of failing; a valid one is looked for in the PDF."""
    from latexlive import checks, config
    env = {**os.environ, "LATEX_LIVE_ANON_TERMS": "(unclosed|a(?i)b"}
    r = subprocess.run([sys.executable, "-c", "from latexlive import config; print(config.ANON_TERMS_PROBLEM, repr(config.ANON_TERMS))"],
                       cwd=HERE, env=env, capture_output=True, text=True, timeout=60)
    if "not a regular expression" not in r.stdout or "''" not in r.stdout or "LATEX_LIVE_ANON_TERMS" not in r.stderr:
        fail(f"an invalid LATEX_LIVE_ANON_TERMS: {r.stdout!r} {r.stderr[-300:]!r}")
    with tempfile.TemporaryDirectory(prefix="latex-live-check-") as tmp:
        put(os.path.join(tmp, "main.tex"), "\\documentclass{article}\n\\begin{document}\nx\n\\end{document}\n")
        pages = lambda pdf: ["Anonymous Authors\nWe thank Example University for its support."]
        found = {}
        for terms, problem in ((r"\bExample University\b", None), ("", "LATEX_LIVE_ANON_TERMS is not a regular expression (x)")):
            with patched(checks, pdf_pages_text=pages, pdf_tool=lambda argv, timeout=60: ""), \
                    patched(config, ANON_TERMS=terms, ANON_TERMS_PROBLEM=problem):
                result = next(r for r in checks.checks(fake_server(tmp), "main.pdf") if r["title"] == "Anonymity")
            found[bool(problem)] = [item["text"] for item in result["items"]]
    if not any("Example University" in t for t in found[False]) or not any("not a regular expression" in t for t in found[True]):
        fail(f"the anonymity check with LATEX_LIVE_ANON_TERMS: {found}")
    ok("an invalid LATEX_LIVE_ANON_TERMS is ignored with a warning and named in the anonymity check, a valid one looked for")


def poppler_limit_checks():
    """poppler's limits in shared mode (tools.pdf_tool): a limit the system refuses (as macOS refuses an address
    space limit) is left out, and the tool still runs, rather than the request failing."""
    import resource
    from latexlive import config, tools
    refuse = lambda kind, limits: (_ for _ in ()).throw(ValueError("not allowed here"))
    with patched(config, SHARED=True), patched(resource, setrlimit=refuse):
        try:
            out = tools.pdf_tool([sys.executable, "-c", "print('ran')"])
        except Exception as e:
            out = f"{type(e).__name__}: {e}"
    if out.strip() != "ran":
        fail(f"poppler's run when the system refuses a limit: {out!r}")
    ok("poppler runs when the system refuses one of its limits in shared mode")


def bounded_cache_checks():
    """file_hash and the arXiv check's PDF text keep a bounded number of entries; the server puts its checkout on
    Python's path once (config), not on each build or download."""
    import re
    from latexlive import arxiv, files
    with tempfile.TemporaryDirectory(prefix="latex-live-check-") as tmp, patched(arxiv, pdf_tool=lambda argv, timeout=60: "text"):
        for n in range(1100):
            put(os.path.join(tmp, f"{n}.txt"), str(n))
            files.file_hash(os.path.join(tmp, f"{n}.txt"))
        for n in range(20):
            arxiv.pdf_strings(b"%PDF-1.5 " + str(n).encode())
        hashes, strings = len(files.file_hash.__defaults__[0]), len(arxiv.pdf_strings.__defaults__[0])
        files.file_hash.__defaults__[0].clear()
        arxiv.pdf_strings.__defaults__[0].clear()
    package = os.path.join(HERE, "latexlive")
    inserts = []
    for name in sorted(os.listdir(package)):
        if name.endswith(".py") and name not in ("config.py", "standby.py"):
            with open(os.path.join(package, name)) as f:
                inserts += [name] if re.search(r"sys\.path\.insert", f.read()) else []
    if hashes > 1024 or strings > 8 or inserts:
        fail(f"unbounded caches or path entries: file_hash keeps {hashes}, pdf_strings {strings}; sys.path.insert in {inserts}")
    ok("file_hash and the arXiv check's PDF text are bounded, and the checkout goes on Python's path once")


def stop_notes_free_checks():
    """live.py stops a document's notes-free pass before its warm standbys (latex_report.stop_notes_free): the pass
    runs in a session of its own, and was left to start a standby after them. Only that document's pass is stopped.
    An early pass that was kept while its TeX run goes on has that run's container removed too (as stop_early does:
    the podman client stopped may leave it), and one whose TeX runs have ended has none to remove."""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import latex_report

    def start(doc, how="--notes-free"):  # (as latex_report.py starts it: in a session of its own, its parent gone)
        spawn = ("import subprocess, sys; p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)',"
                 f" '{how}', '{doc}'], start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,"
                 " stderr=subprocess.DEVNULL); print(p.pid)")
        return int(subprocess.run([sys.executable, "-c", spawn], capture_output=True, text=True, timeout=30).stdout)

    def running(pid):
        try:
            with open(f"/proc/{pid}/stat") as f:
                return f.read().rsplit(")", 1)[1].split()[0] != "Z"
        except OSError:
            if os.path.isdir("/proc"):
                return False
            try:
                os.kill(pid, 0)  # (elsewhere: a zombie counts, but its parent is gone, so it is soon reaped)
                return True
            except OSError:
                return False
    passes = {doc: start(doc) for doc in ("docmainx", "docotherx")}
    cwd, removed, early_ok = os.getcwd(), {}, False
    with tempfile.TemporaryDirectory(prefix="latex-live-check-") as tmp:
        try:
            os.chdir(tmp)
            os.makedirs(os.path.join("build", "clean"))
            with open(os.path.join("build", "clean", "pid"), "w") as f:
                f.write(str(passes["docotherx"]))
            latex_report.stop_notes_free("docmainx")
            other_runs = running(passes["docotherx"])
            with open(os.path.join("build", "clean", "pid"), "w") as f:
                f.write(str(passes["docmainx"]))
            began = time.monotonic()
            latex_report.stop_notes_free("docmainx")
            took = time.monotonic() - began
            stopped = wait_for("the notes-free pass to end", lambda: not running(passes["docmainx"]), timeout=5)
            stand_in = types.SimpleNamespace(command_line=latex_report.standby_module().command_line,
                                             remove_containers=lambda label: removed.setdefault(texdone, []).append(label))
            for texdone in (False, True):
                passes[f"early{texdone}"] = pid = start("docmainx", "--early")
                os.makedirs(os.path.join("build", "clean-early"), exist_ok=True)
                with open(os.path.join("build", "clean-early", "early.json"), "w") as f:
                    json.dump({"pid": pid, "label": f"{pid}-1", "state": "kept", "seed": "s", "started": 1,
                               **({"texDone": True} if texdone else {})}, f)
                with open(os.path.join("build", "clean", "pid"), "w") as f:
                    f.write(str(pid))
                with patched(latex_report, standby_module=lambda: stand_in):
                    latex_report.stop_notes_free("docmainx")
            early_ok = removed == {False: [f"latex-live.early={passes['earlyFalse']}-1"]} and wait_for(
                "the kept early passes to end", lambda: not running(passes["earlyFalse"]) and not running(passes["earlyTrue"]), timeout=5)
        finally:
            os.chdir(cwd)
            for pid in passes.values():
                try:
                    os.killpg(pid, signal.SIGKILL)
                except OSError:
                    pass
    if not other_runs or not stopped or took > 10:
        fail(f"stop_notes_free: another document's pass left running {other_runs}, this one's stopped {stopped} in {took:.1f} s")
    if not early_ok:
        fail(f"stop_notes_free of a kept early pass: containers removed {removed} (want only the one whose TeX run goes on)")
    ok("live.py stops a document's notes-free pass (only that document's) before its warm standbys, and a kept early"
       " pass's TeX run with it")


def group_alive_checks():
    """A process group counts as ended once its members are all zombies (latex_report.group_alive): a stopped notes-free
    pass's leader, whose parent is gone, stays one where nothing reaps orphans, and live.py's stop waited out both of
    its waits for it. A group with a member that runs counts until that member ends; without /proc, any member counts."""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import latex_report

    def state(pid):
        return (latex_report.process_fields(pid) or ["gone"])[0]
    if not os.path.isdir("/proc"):
        return print("skip: no /proc, so a process group of zombies is not checked", flush=True)
    procs, member = [], None
    try:
        # A group whose only member is a zombie: a session leader that has exited, which this process (its parent) doesn't
        # reap yet.
        zombie = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
        procs.append(zombie)
        # A group whose leader is a zombie but another member runs: the leader starts a sleeper (pid on its output), exits.
        leader = subprocess.Popen([sys.executable, "-c", "import subprocess, sys; print(subprocess.Popen(['sleep', '60']).pid)"],
                                  start_new_session=True, stdout=subprocess.PIPE, text=True)
        procs.append(leader)
        member = int(leader.stdout.readline())
        running = subprocess.Popen(["sleep", "60"], start_new_session=True)
        procs.append(running)
        wait_for("the leaders to exit", lambda: state(zombie.pid) == "Z" and state(leader.pid) == "Z", timeout=10)
        with patched(latex_report, PROC=os.path.join(HERE, "no-such-folder")):
            fallback = latex_report.group_alive(zombie.pid)
        found = [latex_report.group_alive(pid) for pid in (zombie.pid, leader.pid, running.pid, 0, -1)]
        os.kill(member, signal.SIGKILL)  # (the sleeper this check started: its exact pid)
        wait_for("the group's last running member to end", lambda: not latex_report.group_alive(leader.pid), timeout=10)
    finally:
        for proc in procs:
            proc.kill()
            proc.wait()
            if proc.stdout:
                proc.stdout.close()
        try:
            os.kill(member, signal.SIGKILL)  # (the sleeper this check started: its exact pid)
        except (OSError, TypeError):
            pass
    if found != [False, True, True, False, False] or not fallback:
        fail(f"group_alive: zombies only, a zombie leader with a running member, running, 0, -1: {found}; without /proc,"
             f" zombies only: {fallback}")
    ok("a process group of zombies counts as ended (live.py's stop doesn't wait for it), one with a running member doesn't")
