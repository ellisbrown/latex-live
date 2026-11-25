"""The unit checks of the Refs panel's bibliography (latexlive.bib), without an instance and never online: the index
and its citation counts, adding entries, reading what arXiv and Crossref answer (from stand-ins), and the lookups'
requests: off without LATEX_LIVE_LOOKUPS, their redirects, rate and concurrency."""

import contextlib
import email.message
import io
import json
import os
import tempfile
import threading
import time
import types
import urllib.error
import urllib.request
import urllib.response

from .base import fail, ok
from .helpers import fake_server, patched, put, read_text


def bib_checks():
    for check in (index_checks, add_checks, metadata_checks, lookups_off_checks, request_checks, verify_checks):
        check()


OWNER = {"id": "owner", "name": "Owner", "role": "owner", "macro": "", "via": "token"}
MAIN = ("\\documentclass{article}\n\\begin{document}\nAs in \\cite{alpha} and \\citep[p.~3]{beta, alpha}.\n"
        "% \\cite{gamma} is a comment\n\\input{sec}\n\\bibliography{refs}\n\\end{document}\n")
REFS = ("@string{conf = {Conf}}\n@article{alpha,\n  title = {{Alpha} Paper},\n  author = {Doe, Jane and Roe, Rick},\n"
        "  year = {2020},\n  journal = {J. Alpha}\n}\n\n@inproceedings{beta, title = \"Beta\", year = 2021, booktitle = {Proc. B}}\n"
        "@misc{gamma,\n  title = {Gamma},\n  eprint = {2312.00752}\n}\n@string{year = {1999}}\n")


@contextlib.contextmanager
def paper():
    with tempfile.TemporaryDirectory(prefix="latex-live-bib-") as root:
        put(os.path.join(root, "main.tex"), MAIN)
        put(os.path.join(root, "sec.tex"), "\\nocite{beta}\n\\citet{alpha}\nA line break\\\\% then \\cite{gamma} in a comment\n")
        put(os.path.join(root, "refs.bib"), REFS)
        put(os.path.join(root, "more", "other.bib"), "@book{Delta, title={Delta}}\n")
        yield root, fake_server(root)


def index_checks():
    """GET /papers/index (bib.papers_index): every entry of the paper's .bib files (not @string), its fields, how
    often the .tex files cite it outside comments and where (\\citep[...]{a, b}, \\nocite, \\citet), each file read
    again once it changed; and whether this preview looks references up online."""
    from latexlive import bib, config
    with paper() as (root, server):
        found = bib.papers_index(server)
        got = {e["key"]: (e["citeCount"], [(c["file"], c["line"]) for c in e["citedIn"]], e["file"], e["line"])
               for e in found["entries"]}
        want = {"alpha": (3, [("main.tex", 3), ("main.tex", 3), ("sec.tex", 2)], "refs.bib", 2),
                "beta": (2, [("main.tex", 3), ("sec.tex", 1)], "refs.bib", 9), "gamma": (0, [], "refs.bib", 10),
                "Delta": (0, [], "more/other.bib", 1)}
        alpha = next(e for e in found["entries"] if e["key"] == "alpha")
        if (got != want or sorted(found["bibFiles"]) != ["more/other.bib", "refs.bib"] or found["lookups"] is not config.LOOKUPS
                or (alpha["title"], alpha["author"], alpha["year"], alpha["venue"]) != ("Alpha Paper", "Doe, Jane and Roe, Rick", "2020", "J. Alpha")
                or [(e["arxiv"], e["year"]) for e in found["entries"] if e["key"] == "gamma"] != [("2312.00752", "")]):
            fail(f"the bibliography's index: {got}, {found['bibFiles']}, {alpha}")
        quoted = bib.parse_bib_entries('@misc{q, title = "A {"}quoted{"} B", year = 2000}\n@misc{r, title = "A } B", year = 2001}')
        if [(e["title"], e["year"]) for e in quoted] != [('A "quoted" B', "2000"), ("A B", "2001")]:
            fail(f"a quoted field with a quote in braces: {quoted}")
        put(os.path.join(root, "sec.tex"), "\\cite{gamma}\n")
        os.utime(os.path.join(root, "sec.tex"), ns=(1, 1))  # (a new mtime as well as a new size)
        counts = {e["key"]: e["citeCount"] for e in bib.papers_index(server)["entries"]}
        if counts != {"alpha": 2, "beta": 1, "gamma": 1, "Delta": 0}:
            fail(f"the bibliography's citation counts after a change to sec.tex: {counts}")
    ok("the Refs panel lists the .bib files' entries with how often and where the .tex files cite them (not in comments),"
       " and counts again after a change")


def add_checks():
    """POST /bib/add (bib.add_request): entries appended after a blank line through the browser editor's save (who wrote
    it is remembered, a \\r\\n file keeps its line ends); refused, writing nothing: no entry, braces that don't pair up,
    a key twice in what was sent (not only the first entry's), a key already in the file or in another .bib file
    (ignoring case), a file that is not one of the paper's .bib files, and a file that would grow past 4 MB."""
    from latexlive import bib, sources
    with paper() as (root, server):
        refs = os.path.join(root, "refs.bib")
        code, answer = bib.add_request(server, {"bibtex": "@misc{eps, title={Eps}}\n\n@misc{zeta, title={Zeta}}"}, OWNER)
        if (code, answer) != (200, {"file": "refs.bib", "keys": ["eps", "zeta"]}) or read_text(refs) != REFS + "\n@misc{eps, title={Eps}}\n\n@misc{zeta, title={Zeta}}\n":
            fail(f"adding two entries to refs.bib: {code} {answer}, the file ends {read_text(refs)[-80:]!r}")
        if server.last_writer.get("refs.bib", (None, None))[1] != "Owner":
            fail("an entry added to refs.bib did not go through the editor's save (who wrote it is not remembered)")
        before = read_text(refs)
        for body, status, why in (({"bibtex": "just text"}, 400, "no entry"),
                                  ({"bibtex": "@misc{x, title={Open}"}, 400, "an unclosed brace"),
                                  ({"bibtex": "@misc{x, title={a}}} @misc{y, title={b}"}, 400, "a brace closed too soon"),
                                  ({"bibtex": "@misc{new1, title={A}}\n@misc{NEW1, title={B}}"}, 400, "the same key twice"),
                                  ({"bibtex": "@misc{new2, title={A}}\n@misc{Alpha, title={B}}"}, 409, "a key in the file, as the second entry"),
                                  ({"bibtex": "@misc{delta, title={A}}"}, 409, "a key in another .bib file"),
                                  ({"bibtex": "@misc{new3, title={A}}", "file": "main.tex"}, 404, "a .tex file"),
                                  ({"bibtex": "@misc{new3, title={A}}", "file": "../refs.bib"}, 404, "a path out of the paper"),
                                  ({"bibtex": ["@misc{new3}"]}, 400, "not text")):
            code, answer = bib.add_request(server, body, OWNER)
            if code != status:
                fail(f"adding BibTeX with {why}: {code} {answer}, not {status}")
        if read_text(refs) != before:
            fail("a refused addition changed refs.bib")
        big = os.path.join(root, "big.bib")
        put(big, "@misc{filler, title={F}}\n%" + "x" * (sources.EDITABLE_SIZE - 40) + "\n")
        server.editable = None
        code, answer = bib.add_request(server, {"bibtex": "@misc{last, title={" + "y" * 100 + "}}", "file": "big.bib"}, OWNER)
        if code != 413 or os.path.getsize(big) > sources.EDITABLE_SIZE:
            fail(f"adding to a .bib file just under 4 MB: {code} {answer}, the file is {os.path.getsize(big)} bytes")
        with open(os.path.join(root, "crlf.bib"), "w", newline="") as f:
            f.write("@misc{one,\r\n  title={One}\r\n}\r\n")
        server.editable = None
        code, answer = bib.add_request(server, {"bibtex": "@misc{two,\n  title={Two}\n}", "file": "crlf.bib"}, OWNER)
        with open(os.path.join(root, "crlf.bib"), newline="") as f:
            if code != 200 or f.read() != "@misc{one,\r\n  title={One}\r\n}\r\n\r\n@misc{two,\r\n  title={Two}\r\n}\r\n":
                fail(f"adding to a .bib file with \\r\\n line ends: {code} {answer}")
    ok("an entry added from the Refs panel goes through the editor's save after a blank line; one with a key the paper"
       " has (any entry's, any .bib file's, ignoring case), unpaired braces, or past the 4 MB limit is refused")


ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <title>Mamba: Linear-Time {Sequence}} Modeling
      with $\\input{secret}$ 50% &amp; #1_x</title>
    <published>2023-12-01T18:01:34Z</published>
    <author><name>Albert Gu</name></author><author><name>Tri Dao}</name></author>
    <arxiv:doi>10.1000/a^^5cinput^^7bx^^7d</arxiv:doi>
    <arxiv:primary_category term="cs.LG"/>
  </entry>
</feed>"""
CROSSREF = {"message": {"title": ["Deep <i>Residual</i> Learning {for\n Image} Recognition &amp; More"],
                        "author": [{"given": "Kaiming", "family": "He"}, {"given": "X", "family": "Zhang\\par"}],
                        "issued": {"date-parts": [[2016, 6]]}, "container-title": ["Proc. CVPR}"],
                        "type": "proceedings-article"}}


def metadata_checks():
    """What arXiv and Crossref answer (bib.lookup_arxiv, lookup_doi, from stand-ins for bib.fetch) goes into BibTeX as
    plain text on one line: braces that would end a field, backslashes, Crossref's markup and entities, and newlines
    gone, TeX's special characters escaped; a DOI that is not one is left out; the key is letters and digits; an Atom
    feed with a DOCTYPE is refused. Pasted BibTeX reads without asking anyone (lookup_request)."""
    from latexlive import bib
    answers = []
    stand_in = lambda url, accept="application/json", background=False: answers.append(url) or (
        ATOM.encode() if "arxiv" in url else json.dumps(CROSSREF).encode())
    with patched(bib, fetch=stand_in):
        arxiv = bib.lookup_arxiv("2312.00752")
        doi = bib.lookup_doi("10.1109/cvpr.2016.90")
        bib.fetch = lambda *a, **k: b'<!DOCTYPE x [<!ENTITY a "aaaa">]><feed/>'
        try:
            bib.lookup_arxiv("2312.00752")
            fail("an Atom answer with a DOCTYPE was parsed")
        except ValueError:
            pass
        bib.fetch = lambda url, *a, **k: (_ for _ in ()).throw(urllib.error.HTTPError(url, 404, "Not Found", {}, None))
        unknown = bib.lookup_request({"query": "10.1000/unknown"})
        if unknown[0] != 404 or "doesn't know" not in unknown[1].get("error", ""):
            fail(f"looking up a DOI Crossref doesn't know: {unknown}")
    for found in (arxiv, doi):
        text = found["bibtex"]
        if (not bib.balanced(text) or "\\input" in text or "\\par" in text or "<i>" in text or "&amp;" in text
                or not found["key"].isalnum() or len(bib.parse_bib_entries(text)) != 1 or text.count("\n@")):
            fail(f"an entry made from what arXiv or Crossref said is not safe BibTeX:\n{text}")
    if (arxiv["key"], arxiv["title"]) != ("gu2023mamba", "Mamba: Linear-Time Sequence Modeling with \\$inputsecret\\$ 50\\% \\& \\#1\\_x") \
            or "doi=" in arxiv["bibtex"] or "primaryClass={cs.LG}" not in arxiv["bibtex"] or arxiv["author"] != "Albert Gu and Tri Dao":
        fail(f"the entry made from arXiv's answer: {arxiv}")
    if (doi["key"], doi["title"], doi["year"], doi["venue"]) != ("he2016deep", "Deep Residual Learning for Image Recognition \\& More", "2016", "Proc. CVPR") \
            or not doi["bibtex"].startswith("@inproceedings{he2016deep,") or "Zhangpar" not in doi["author"]:
        fail(f"the entry made from Crossref's answer: {doi}")
    if answers != ["https://export.arxiv.org/api/query?id_list=2312.00752", "https://api.crossref.org/works/10.1109%2Fcvpr.2016.90"]:
        fail(f"the lookups asked for {answers}")
    code, found = bib.lookup_request({"query": "@article{k1, title={T}, author={A}}"})
    if code != 200 or (found["key"], found["source"]) != ("k1", "bibtex"):
        fail(f"looking up pasted BibTeX: {code} {found}")
    for query, status in (("@article{k1, title={T}", 400), ("hello", 400), ("", 400), ("10.1000/a^^5cinput", 400),
                          ("10.1000/a{b}", 400)):
        if bib.lookup_request({"query": query})[0] != status:
            fail(f"looking up {query!r}: {bib.lookup_request({'query': query})}, not {status}")
    ok("entries made from arXiv's and Crossref's answers are plain text on one line in BibTeX (no braces, backslashes,"
       " markup or newlines from them; TeX's special characters escaped); pasted BibTeX reads offline")


def lookups_off_checks():
    """Without LATEX_LIVE_LOOKUPS (config.LOOKUPS false, shared mode's default), an arXiv ID's or a DOI's lookup and the
    check against Crossref are refused, saying why, and nothing is requested; pasted BibTeX, the index and adding still
    work. The setting's default: on in a preview of one's own, off in shared mode."""
    import subprocess
    import sys
    from latexlive import bib, config
    asked = []
    opener = types.SimpleNamespace(open=lambda *a, **k: asked.append(a) or fail("a lookup went out with lookups off"))
    with patched(config, LOOKUPS=False), patched(bib, OPENER=opener), paper() as (root, server):
        for query in ("2312.00752", "arXiv:2312.00752", "https://arxiv.org/abs/2312.00752v2", "10.1109/cvpr.2016.90",
                      "https://doi.org/10.1109/cvpr.2016.90"):
            code, answer = bib.lookup_request({"query": query})
            if code != 403 or not answer.get("lookupsOff") or "off" not in answer.get("error", ""):
                fail(f"looking up {query} with lookups off: {code} {answer}")
        code, answer = bib.start_verify(server)
        if code != 403 or not answer.get("lookupsOff") or server.bib_verify:
            fail(f"the check against Crossref with lookups off: {code} {answer}")
        if (bib.papers_index(server)["lookups"] is not False or bib.lookup_request({"query": "@misc{k, title={T}}"})[0] != 200
                or bib.add_request(server, {"bibtex": "@misc{k, title={T}}"}, OWNER)[0] != 200):
            fail("with lookups off, the index, pasted BibTeX or adding stopped working")
    if asked:
        fail(f"requests went out with lookups off: {asked}")
    probe = "import sys; sys.path.insert(0, '.'); from latexlive import config; print(config.LOOKUPS)"
    env = {k: v for k, v in os.environ.items() if not k.startswith("LATEX_LIVE_")}
    with tempfile.TemporaryDirectory() as tmp:
        users = os.path.join(tmp, "users.json")
        put(users, "{}")
        got = [subprocess.run([sys.executable, "-c", probe], env={**env, **extra}, capture_output=True, text=True,
                              cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))).stdout.strip()
               for extra in ({}, {"LATEX_LIVE_USERS": users}, {"LATEX_LIVE_USERS": users, "LATEX_LIVE_LOOKUPS": "1"},
                             {"LATEX_LIVE_LOOKUPS": "0"}, {"LATEX_LIVE_LOOKUPS": "yes"})]
    if got != ["True", "False", "True", "False", "False"]:
        fail(f"LATEX_LIVE_LOOKUPS (unset, shared, shared with 1, 0, yes): {got}")
    ok("without LATEX_LIVE_LOOKUPS (off by default in shared mode only), lookups by ID and the check against Crossref are"
       " refused, saying so, and nothing goes out; pasted BibTeX, the index and adding still work")


class StandIn(urllib.request.HTTPSHandler):
    """HTTPS for the checks, never online: answers from pages ({url: (status, headers, body)}), each request logged;
    while gate is set, a request waits for it to clear."""
    pages, log, gate = {}, [], None

    def https_open(self, req):
        StandIn.log.append((req.full_url, req.get_header("User-agent")))
        if StandIn.gate is not None:
            StandIn.gate.wait(10)
        status, headers, body = StandIn.pages.get(req.full_url, (404, {}, b"not here"))
        message = email.message.Message()
        for key, value in headers.items():
            message[key] = value
        response = urllib.response.addinfourl(io.BytesIO(body), message, req.full_url, status)
        response.msg = "stand-in"
        return response


def request_checks():
    """The lookups' requests (bib.fetch, through bib.OPENER with a stand-in for HTTPS): only https to the allowed hosts,
    with latex-live's version in the User-Agent; a redirect followed to an allowed host, refused to another host or to
    http (bib.SafeRedirect, also on its own), at most 3 of them; an answer over NET_READ refused; a lookup that
    would wait more than NET_WAIT for its host's turn, or past NET_SLOTS at once, refused at once (LookupBusy), the
    background check waiting."""
    from latexlive import bib, config
    a, b = "https://api.crossref.org/works/a", "https://api.crossref.org/works/b"
    opener = urllib.request.build_opener(bib.SafeRedirect, StandIn)
    try:
        with patched(config, LOOKUPS=True), patched(bib, OPENER=opener, HOSTS={host: 0 for host in bib.HOSTS}):
            bib.net_next.clear()
            StandIn.pages = {a: (302, {"Location": b}, b""), b: (200, {}, b"found"),
                             "https://api.crossref.org/works/evil": (302, {"Location": "https://evil.example/x"}, b""),
                             "https://api.crossref.org/works/plain": (302, {"Location": "http://api.crossref.org/works/b"}, b""),
                             "https://api.crossref.org/works/port": (302, {"Location": "https://api.crossref.org:8443/works/b"}, b""),
                             **{f"https://api.crossref.org/works/hop{i}": (302, {"Location": f"https://api.crossref.org/works/hop{i + 1}"}, b"")
                                for i in range(9)},
                             "https://api.crossref.org/works/big": (200, {}, b"x" * 2000)}
            StandIn.log = []
            if bib.fetch(a) != b"found" or [url for url, _ in StandIn.log] != [a, b] or f"latex-live/{config.VERSION}" not in StandIn.log[0][1]:
                fail(f"a lookup redirected within Crossref: {StandIn.log}")
            for name in ("evil", "plain", "port", "hop0"):
                StandIn.log = []
                try:
                    bib.fetch(f"https://api.crossref.org/works/{name}")
                    fail(f"a lookup followed a redirect it should refuse ({name}): {StandIn.log}")
                except urllib.error.URLError:
                    pass
                if any(not url.startswith("https://api.crossref.org/") for url, _ in StandIn.log) or len(StandIn.log) > 4:
                    fail(f"a refused redirect ({name}) was requested anyway: {StandIn.log}")
            handler, req = bib.SafeRedirect(), urllib.request.Request(a)
            if handler.redirect_request(req, None, 302, "Found", {}, b).full_url != b:
                fail("SafeRedirect refused a redirect to Crossref")
            for where in ("https://evil.example/", "http://api.crossref.org/", "https://user@api.crossref.org/", "ftp://export.arxiv.org/"):
                try:
                    handler.redirect_request(req, None, 302, "Found", {}, where)
                    fail(f"SafeRedirect allowed a redirect to {where}")
                except urllib.error.URLError:
                    pass
            for url in ("http://api.crossref.org/works/a", "https://evil.example/", "https://api.crossref.org:444/works/a"):
                try:
                    bib.fetch(url)
                    fail(f"a lookup requested {url}")
                except ValueError:
                    pass
            try:
                with patched(bib, NET_READ=1000):
                    bib.fetch("https://api.crossref.org/works/big")
                fail("a lookup read an answer past NET_READ")
            except ValueError:
                pass

            bib.HOSTS["api.crossref.org"] = 10  # (the rate: one request to a host per 10 s here)
            bib.net_next.clear()
            StandIn.log = []
            bib.fetch(b)
            began = time.monotonic()
            try:
                bib.fetch(b)
                fail("a second lookup to the same host right away went out")
            except bib.LookupBusy:
                pass
            if time.monotonic() - began > 1 or len(StandIn.log) != 1:
                fail(f"a lookup too soon after another was not refused at once: {StandIn.log}")
            bib.HOSTS["api.crossref.org"] = 0.5
            bib.net_next.clear()
            bib.fetch(b)
            began = time.monotonic()
            bib.fetch(b, background=True)  # (the background check waits for its turn)
            if not 0.4 < time.monotonic() - began < 3:
                fail(f"the background check did not wait its turn: {time.monotonic() - began:.2f} s")

            bib.HOSTS["api.crossref.org"] = 0  # (the cap: two at once)
            bib.net_next.clear()
            StandIn.gate, StandIn.log, results = threading.Event(), [], []
            threads = [threading.Thread(target=lambda: results.append(bib.fetch(b))) for _ in range(2)]
            for t in threads:
                t.start()
            deadline = time.monotonic() + 5
            while len(StandIn.log) < 2 and time.monotonic() < deadline:
                time.sleep(0.02)
            try:
                bib.fetch(b)
                fail("a third lookup went out while two were on their way")
            except bib.LookupBusy:
                pass
            StandIn.gate.set()
            for t in threads:
                t.join(10)
            StandIn.gate = None
            if results != [b"found", b"found"] or bib.fetch(b) != b"found":
                fail(f"lookups after the two on their way ended: {results}")
    finally:
        StandIn.gate = None
        bib.net_next.clear()
    ok("lookups go over https to arXiv and Crossref only, redirects too (others refused, before any request), with the"
       " version in the User-Agent; a large answer is refused; one that would wait over 3 s for its host's turn, or past two"
       " at once, is refused at once, while the background check waits its turn")


def verify_checks():
    """The check of the bibliography (bib.start_verify, run_verify, verify_status; Crossref from a stand-in for
    bib.fetch): keys twice (ignoring case), titles twice, entries never cited; for cited ones, Crossref's year for a
    DOI (or that it doesn't know the DOI), and a preprint's published version found by its title."""
    from latexlive import bib, config

    def crossref(url, accept="application/json", background=False):
        if not background:
            fail(f"the check asked {url} as a lookup someone waits for, not in the background")
        if "/works/10.1000%2Fmissing" in url:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        if "/works/10." in url:
            return json.dumps({"message": {"title": ["Alpha"], "issued": {"date-parts": [[2019]]}}}).encode()
        return json.dumps({"message": {"items": [{"title": ["Gamma"], "container-title": ["Journal of G"],
                                                  "issued": {"date-parts": [[2024]]}}]}}).encode()
    with patched(config, LOOKUPS=True), patched(bib, fetch=crossref):
        with paper() as (root, server):
            put(os.path.join(root, "refs.bib"), REFS.replace("journal = {J. Alpha}", "journal = {J. Alpha}, doi = {10.1000/a}")
                + "@misc{ALPHA, title={Another}}\n@misc{copy, title={Beta}}\n@misc{long1, title={A Title Long Enough To Count}}\n"
                  "@misc{long2, title={A title long enough to count}}\n@misc{lost, title={Lost}, doi={10.1000/missing}}\n")
            put(os.path.join(root, "sec.tex"), "\\cite{gamma,lost}\n")
            code, state = bib.start_verify(server)
            deadline = time.monotonic() + 10
            while bib.verify_status(server)["state"] == "running" and time.monotonic() < deadline:
                time.sleep(0.05)
            done = bib.verify_status(server)
        kinds = sorted((f["kind"], f["key"]) for f in done.get("findings", []))
        want = [("duplicate-key", "ALPHA"), ("duplicate-title", "long2"), ("published-version", "gamma"),
                ("uncited", "ALPHA"), ("uncited", "Delta"), ("uncited", "copy"), ("uncited", "long1"), ("uncited", "long2"),
                ("unknown-doi", "lost"), ("year-mismatch", "alpha")]
        if code != 202 or done.get("state") != "done" or kinds != want or done.get("checked") != 4 or done.get("offline"):
            fail(f"the check of the bibliography: {code} {state}, then {done.get('state')} {kinds} (checked {done.get('checked')})")
        found = []
        bib.compare_crossref({"key": "k", "title": "A Long Title: An Introduction to Things", "arxiv": "1", "venue": "",
                              "year": "2020", "file": "refs.bib", "line": 1},
                             [{"title": ["Introduction"], "container-title": ["Some Journal"]}], lambda *a: found.append(a))
        if found:
            fail(f"a short title on Crossref was taken for a longer one: {found}")
    ok("the check of the bibliography finds keys and titles twice, entries never cited, a year Crossref disagrees with,"
       " a DOI it doesn't know, and a preprint published since")
