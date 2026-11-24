"""The Refs panel's bibliography: GET /papers/index lists the entries of the paper's .bib files with how often its
.tex files cite each, and where; POST /bib/add appends entries to a .bib file, as a save from the browser editor;
POST /bib/lookup turns an arXiv ID, a DOI or pasted BibTeX into an entry to add; and POST /bib/verify checks the
bibliography in the background (keys and titles twice over, entries never cited, and against Crossref: years, and
published versions of preprints), which GET /bib/verify reports.

Looking up an arXiv ID or a DOI and checking against Crossref are the only requests latex-live sends out, and only
with LATEX_LIVE_LOOKUPS (config.LOOKUPS): over HTTPS to export.arxiv.org and api.crossref.org alone (redirects too),
at most two at once (NET_SLOTS), spaced per host (HOSTS), each under a deadline and read up to NET_READ bytes. What
they answer goes into BibTeX as plain text only (bib_text): it can't end a field or run a command."""

import bisect
import collections
import html
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from . import config
from .commits import paused, updating
from .editor import BIB_ENTRY_RE, COMPLETIONS_LIMIT, editor_text, save_file
from .latex import brace_group
from .sources import NOTE_LOCK, SourceChanged, editable_files, file_version, read_editable

FIELD_RE = re.compile(r"\b([a-zA-Z][a-zA-Z0-9_-]*)\s*=\s*")
CITE_RE = re.compile(r"\\[a-zA-Z]*cite[a-zA-Z]*\*?(?:\[[^\]]*\]){0,2}\{([^{}]+)\}")
ARXIV_ID_RE = re.compile(r"(?:(?:https?://)?(?:www\.)?arxiv\.org/(?:abs|pdf|html)/|arXiv:)?"
                         r"(\d{4}\.\d{4,5}(?:v\d+)?|[a-z-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?)(?:\.pdf)?", re.I)
# (a DOI as Crossref recommends matching them, which takes nearly all: no character that TeX or BibTeX treats specially)
DOI_RE = re.compile(r"(?:(?:https?://)?(?:dx\.)?doi\.org/|doi:\s*)?(10\.\d{4,9}/[-._;()/:A-Z0-9]{1,200})", re.I)
ENTRY_START_RE = re.compile(r"(?m)^[ \t]*(@)[A-Za-z]+\s*[{(]")  # (where an @string, @comment or @preamble may begin too)
ADD_LIMIT = 50_000  # (characters of BibTeX added at once)


def parse_bib_entries(text, rel=""):
    """[{"key", "entryType", "title", "author", "year", "venue", "doi", "arxiv", "url", "file", "line"}] of the entries
    in a .bib file's text (not @string, @comment or @preamble): a forgiving reading, each field as written with its
    braces dropped, cut short."""
    found = list(BIB_ENTRY_RE.finditer(text))
    starts = sorted({m.start() for m in found} | {m.start(1) for m in ENTRY_START_RE.finditer(text)} | {len(text)})
    entries, line, at = [], 1, 0
    for m in found:
        if m.group(1).lower() in ("comment", "string", "preamble"):
            continue
        line, at = line + text.count("\n", at, m.start()), m.start()
        fields = bib_fields(text[m.end():starts[bisect.bisect_right(starts, m.start())]])
        arxiv = fields.get("eprint", "")
        if not arxiv and (am := ARXIV_ID_RE.search(fields.get("url", ""))) and "arxiv" in fields.get("url", "").lower():
            arxiv = am.group(1)
        entries.append({"key": m.group(2), "entryType": m.group(1).lower(), "title": fields.get("title", "")[:240],
                        "author": fields.get("author", "")[:240], "year": fields.get("year", "")[:16],
                        "venue": (fields.get("booktitle") or fields.get("journal") or fields.get("publisher") or "")[:160],
                        "doi": fields.get("doi", "")[:120], "arxiv": arxiv[:40], "url": fields.get("url", "")[:240],
                        "file": rel, "line": line})
    return entries


def bib_fields(body):
    """{name in lower case: value, braces dropped and spaces collapsed} of the fields in one entry's body."""
    fields, i, n = {}, 0, len(body)
    while (m := FIELD_RE.search(body, i)) and m.end() < n:
        j = m.end()
        if body[j] == "{":
            value, i = brace_group(body, j)
        elif body[j] == '"':  # (it ends at a quote outside braces)
            k, depth = j + 1, 0
            while k < n and (body[k] != '"' or depth):
                depth += {"{": 1, "}": -1}.get(body[k], 0)
                k += 1
            value, i = body[j + 1:k], k + 1
        else:
            k = j
            while k < n and body[k] not in ",}\r\n":
                k += 1
            value, i = body[j:k].strip(), k + 1
        fields.setdefault(m.group(1).lower(), " ".join(value.replace("{", "").replace("}", "").split()))
    return fields


def balanced(text):
    """Whether text's braces pair up as BibTeX counts them (a backslash before one doesn't escape it there)."""
    depth = 0
    for ch in text:
        depth += {"{": 1, "}": -1}.get(ch, 0)
        if depth < 0:
            return False
    return depth == 0


def bib_sources(server, cache={}):
    """{rel: (kind, found)} for the paper's .bib files (kind "bib", found its entries: parse_bib_entries) and .tex
    files (kind "tex", found [(key, line)] of their citations outside comments), each read again only once it changed."""
    out = {}
    for rel, real in editable_files(server).items():
        if not rel.endswith((".bib", ".tex")):
            continue
        try:
            st = os.stat(real)
            entry = cache.get(real)
            if not entry or entry[0] != (st.st_mtime_ns, st.st_size, rel):
                text = read_editable(real).decode("utf-8", errors="replace")
                entry = cache[real] = ((st.st_mtime_ns, st.st_size, rel),
                                       parse_bib_entries(text, rel) if rel.endswith(".bib") else citations(text))
        except (OSError, SourceChanged):
            continue
        out[rel] = ("bib" if rel.endswith(".bib") else "tex", entry[1])
    if len(cache) > 256:
        cache.clear()
    return out


def citations(text):
    """[(key, line)] of each key in the \\cite commands (\\citep, \\citet, \\nocite…) outside comments of a .tex file."""
    bare, found, line, at = re.sub(r"(?<!\\)(?:\\\\)*%.*", "", text), [], 1, 0
    for m in CITE_RE.finditer(bare):
        line, at = line + bare.count("\n", at, m.start()), m.start()
        found += [(key.strip(), line) for key in m.group(1).split(",") if key.strip()]
    return found


def papers_index(server):
    """GET /papers/index: {"entries": [entry (parse_bib_entries) plus "citeCount" and "citedIn" (the first 10 places
    it is cited, [{"file", "line"}])], at most COMPLETIONS_LIMIT; "bibFiles" (bib_order); "lookups": whether this preview looks
    references up online (config.LOOKUPS)}."""
    entries, bib_files, cited = [], [], collections.defaultdict(list)
    for rel, (kind, found) in bib_sources(server).items():
        if kind == "bib":
            bib_files.append(rel)
            entries += found[:COMPLETIONS_LIMIT - len(entries)]
        else:
            for key, line in found:
                cited[key].append({"file": rel, "line": line})
    return {"entries": [{**e, "citeCount": len(cited.get(e["key"], ())), "citedIn": cited.get(e["key"], [])[:10]}
                        for e in entries],
            "bibFiles": sorted(bib_files, key=bib_order), "lookups": config.LOOKUPS}


def bib_order(rel):
    """The order of the .bib files, the first of which an entry is added to by default: those in the paper root first."""
    return os.sep in rel, rel


def add_request(server, req, user):
    """POST /bib/add {bibtex, file?}: append BibTeX entries to one of the paper's .bib files (file, else the first), as
    user (a principal), through the browser editor's save (save_file: LATEX_LIVE_COMMIT commits it as theirs, and the
    file stays within the editor's 4 MB). Refused unless the text holds entries, its braces pair up, and none of its
    keys is there twice or already in a .bib file of the paper (as BibTeX compares keys, ignoring case). 200 {"file",
    "keys"}."""
    bibtex, target = req.get("bibtex"), req.get("file")
    if not isinstance(bibtex, str) or not isinstance(target, (str, type(None))):
        return 400, {"error": "expected bibtex (and maybe file)"}
    bibtex = bibtex.replace("\r\n", "\n").strip()
    if not bibtex or len(bibtex) > ADD_LIMIT:
        return 400, {"error": f"paste a BibTeX entry (at most {ADD_LIMIT // 1000} KB)"}
    if not balanced(bibtex):
        return 400, {"error": "the BibTeX's braces don't pair up"}
    keys = [e["key"] for e in parse_bib_entries(bibtex)]
    if not keys:
        return 400, {"error": "no BibTeX entry found (it starts with @article{key, or the like)"}
    twice = [key for key, n in collections.Counter(k.lower() for k in keys).items() if n > 1]
    if twice:
        return 400, {"error": f"the key {twice[0]} is there twice"}
    files = editable_files(server)
    bibs = sorted((rel for rel in files if rel.endswith(".bib")), key=bib_order)
    target = os.path.normpath(target) if target else (bibs[0] if bibs else None)
    if target not in bibs:
        return 404, {"error": "no such .bib file in this paper" if bibs else "this paper has no .bib file yet"}
    for _ in range(3):  # (again when another program writes the file between our read and write)
        try:
            with NOTE_LOCK:
                if paused(server):
                    return updating()
                for rel, (kind, found) in bib_sources(server).items():
                    if kind == "bib" and rel != target and (clash := taken_key(keys, found)):
                        return 409, {"error": f"{clash} is already in {rel}"}
                data = read_editable(files[target])
                text = editor_text(data.decode("utf-8"))[0]
                if clash := taken_key(keys, parse_bib_entries(text)):
                    return 409, {"error": f"{clash} is already in {target}"}
                gap = "" if not text or text.endswith("\n\n") else "\n" if text.endswith("\n") else "\n\n"
                status, answer = save_file(server, user, target, files[target], f"{text}{gap}{bibtex}\n",
                                           file_version(data), text, False)
        except SourceChanged:
            continue
        except UnicodeDecodeError:
            return 415, {"error": f"{target} is not UTF-8, so it can't be added to"}
        except OSError as e:
            return 500, {"error": f"could not write {target}: {e.strerror or e}"}
        if status == 413:
            return 413, {"error": f"{target} would grow past the editor's 4 MB"}
        return (200, {"file": target, "keys": keys}) if status == 200 else (status, answer)
    return 409, {"error": f"{target} keeps changing on disk; try again"}


def taken_key(keys, entries):
    """The first of keys that one of entries has already, ignoring case, or None."""
    have = {e["key"].lower() for e in entries}
    return next((key for key in keys if key.lower() in have), None)


# The lookups' requests: the hosts they may reach, each with the seconds between two requests to it (arXiv's terms ask
# for 3); at most NET_SLOTS at once, NET_TIMEOUT seconds each, NET_READ bytes of an answer. A lookup someone waits for
# is refused (LookupBusy: 429) when no slot is free, or when it would wait more than NET_WAIT seconds for its host's turn
# (holding its slot meanwhile); the check against Crossref, in the background, waits.
HOSTS = {"export.arxiv.org": 3.0, "api.crossref.org": 1.0}
NET_SLOTS = threading.BoundedSemaphore(2)
NET_TIMEOUT, NET_READ, NET_WAIT = 8, 1 << 20, 3.0
net_lock, net_next = threading.Lock(), {}  # (host: when its next request may go, time.monotonic)


class LookupBusy(Exception):
    """Too many lookups at once, or too soon after the last ones."""


class LookupsOff(Exception):
    """This preview doesn't look references up online (config.LOOKUPS)."""


def allowed_url(url):
    """Whether a lookup may request url: https on a host of HOSTS, on its usual port."""
    try:
        parts = urllib.parse.urlsplit(url)
        return parts.scheme == "https" and parts.hostname in HOSTS and parts.port in (None, 443) and "@" not in parts.netloc
    except ValueError:
        return False


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Redirects followed only to where a lookup may go (allowed_url), at most 3 of them."""
    max_redirections = 3

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed_url(newurl):
            raise urllib.error.URLError(f"refused a redirect to {urllib.parse.urlsplit(newurl).hostname or newurl!r}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


OPENER = urllib.request.build_opener(SafeRedirect)


def fetch(url, accept="application/json", background=False):
    """The body of a GET of url (allowed_url), as a lookup makes it: only with config.LOOKUPS (else LookupsOff),
    in its host's turn and a slot of NET_SLOTS (else LookupBusy, unless background, which waits), within NET_TIMEOUT
    seconds in all, and of at most NET_READ bytes (else ValueError)."""
    if not config.LOOKUPS:
        raise LookupsOff()
    if not allowed_url(url):
        raise ValueError(f"not a host lookups may reach: {url}")
    host = urllib.parse.urlsplit(url).hostname
    if not NET_SLOTS.acquire(timeout=60 if background else 0):
        raise LookupBusy()
    try:
        with net_lock:
            now = time.monotonic()
            turn = max(now, net_next.get(host, 0))
            if turn - now > (60 if background else NET_WAIT):
                raise LookupBusy()
            net_next[host] = turn + HOSTS[host]
        time.sleep(turn - now)
        agent = f"latex-live/{config.VERSION} (+https://github.com/ellisbrown/latex-live)"
        req = urllib.request.Request(url, headers={"User-Agent": agent, "Accept": accept})
        deadline = time.monotonic() + NET_TIMEOUT
        with OPENER.open(req, timeout=NET_TIMEOUT) as r:
            data = b""
            while len(data) <= NET_READ and (chunk := r.read1(1 << 16)):
                if time.monotonic() > deadline:
                    raise TimeoutError("the answer took too long")
                data += chunk
    finally:
        NET_SLOTS.release()
    if len(data) > NET_READ:
        raise ValueError("the answer is too large")
    return data


def bib_text(value):
    """Text that arXiv or Crossref sent, as it goes into a BibTeX field: on one line, without Crossref's markup (<i>)
    and entities (&amp;), braces and backslashes, and with TeX's special characters escaped, so that it can neither
    end the field nor run a command."""
    text = " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", html.unescape(re.sub(r"<[^<>]{0,200}>", "", value or ""))).split())
    special = {"{": "", "}": "", "\\": "", "&": "\\&", "%": "\\%", "#": "\\#", "_": "\\_", "$": "\\$", "^": "\\^{}",
               "~": "\\~{}"}
    return "".join(special.get(ch, ch) for ch in text)


def make_key(author, year, title):
    """A cite key such as gu2023mamba: the first author's surname, the year and the title's first word that counts."""
    first = author.split(" and ")[0].strip()
    surname = re.sub(r"[^a-zA-Z]", "", first.split(",")[0] if "," in first else (first.split() or ["ref"])[-1]).lower()
    stop = {"a", "an", "the", "of", "for", "in", "on", "to", "with", "via", "and", "from", "towards", "toward"}
    words = [w for w in re.findall(r"[a-z0-9]+", title.lower()) if w not in stop]
    return f"{surname or 'ref'}{re.sub(r'[^0-9]', '', year)[:4]}{words[0] if words else 'paper'}"


def bib_entry(kind, fields):
    """(key, BibTeX) of an entry: @kind{key (make_key), then fields ([(name, value)], values already safe: bib_text,
    those empty left out), a title in double braces (its capitals kept)."""
    named = dict(fields)
    key = make_key(named["author"], named.get("year", ""), named["title"])
    lines = [f"  {name}={{{{{value}}}}}," if name == "title" else f"  {name}={{{value}}}," for name, value in fields if value]
    return key, "\n".join([f"@{kind}{{{key},", *lines, "}"])


def lookup_arxiv(arxiv_id):
    """An entry for an arXiv ID, from export.arxiv.org: {"source", "key", "title", "author", "year", "venue", "doi",
    "arxiv", "bibtex"}, or None if arXiv has no such paper."""
    data = fetch(f"https://export.arxiv.org/api/query?id_list={urllib.parse.quote(arxiv_id, safe='')}",
                 "application/atom+xml")
    text = data.decode("utf-8")  # (UnicodeDecodeError is a ValueError)
    if "<!DOCTYPE" in text or "<!ENTITY" in text:  # (an Atom feed has none; they could make the parser expand a lot)
        raise ValueError("unexpected answer from arXiv")
    ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    entry = ET.fromstring(re.sub(r"^\s*<\?xml[^>]*\?>", "", text)).find("a:entry", ns)
    title = bib_text(entry.findtext("a:title", "", ns)) if entry is not None else ""
    if not title or title.lower() == "error":
        return None
    author = " and ".join(bib_text(a.text) for a in entry.findall("a:author/a:name", ns) if a.text)
    year = re.sub(r"[^0-9]", "", entry.findtext("a:published", "", ns))[:4]
    doi = DOI_RE.fullmatch(entry.findtext("arxiv:doi", "", ns).strip())
    category = entry.find("arxiv:primary_category", ns)
    primary = category.get("term", "") if category is not None else ""
    fields = [("title", title), ("author", author), ("journal", f"arXiv preprint arXiv:{arxiv_id}"), ("year", year),
              ("eprint", arxiv_id), ("archivePrefix", "arXiv"),
              ("primaryClass", primary if re.fullmatch(r"[A-Za-z.-]{1,40}", primary) else ""),
              ("doi", doi.group(1) if doi else "")]
    key, bibtex = bib_entry("article", fields)
    return {"source": "arxiv", "key": key, "title": title, "author": author, "year": year,
            "venue": f"arXiv:{arxiv_id}", "doi": doi.group(1) if doi else "", "arxiv": arxiv_id, "bibtex": bibtex}


def lookup_doi(doi, background=False):
    """An entry for a DOI, from api.crossref.org (as lookup_arxiv's), or None if Crossref has no title for it."""
    msg = json.loads(fetch(f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='')}",
                           background=background)).get("message") or {}
    title = bib_text((msg.get("title") or [""])[0])
    if not title:
        return None
    author = " and ".join(bib_text(f"{a.get('given', '')} {a.get('family', '')}") for a in msg.get("author") or []
                          if isinstance(a, dict) and a.get("family"))
    year = crossref_year(msg)
    venue = bib_text((msg.get("container-title") or [""])[0])
    kind = "inproceedings" if "proceedings" in str(msg.get("type", "")) else "article"
    key, bibtex = bib_entry(kind, [("title", title), ("author", author),
                              ("booktitle" if kind == "inproceedings" else "journal", venue), ("year", year), ("doi", doi)])
    return {"source": "crossref", "key": key, "title": title,
            "author": author, "year": year, "venue": venue, "doi": doi, "arxiv": "", "bibtex": bibtex}


def crossref_year(item):
    """The year a Crossref work came out, as text ("" if it doesn't say)."""
    for name in ("published-print", "published-online", "issued", "created"):
        parts = ((item.get(name) or {}).get("date-parts") or [[]])[0]
        if parts and isinstance(parts[0], int):
            return str(parts[0])
    return ""


def lookup_request(req):
    """POST /bib/lookup {query}: an entry to add (lookup_arxiv's form) for an arXiv ID or link, a DOI or link (online:
    403 without config.LOOKUPS, 429 when busy, 502 when it fails), or pasted BibTeX (here, always)."""
    query = req.get("query").strip() if isinstance(req.get("query"), str) else ""
    if not query or len(query) > ADD_LIMIT:
        return 400, {"error": "enter an arXiv ID, a DOI or a BibTeX entry"}
    if query.startswith("@"):
        parsed = parse_bib_entries(query)
        if not parsed or not balanced(query):
            return 400, {"error": "that BibTeX doesn't read as an entry (or its braces don't pair up)"}
        return 200, {**parsed[0], "source": "bibtex", "bibtex": query}
    arxiv, doi = ARXIV_ID_RE.fullmatch(query), DOI_RE.fullmatch(query.rstrip("."))
    if not arxiv and not doi:
        return 400, {"error": "enter an arXiv ID (2312.00752), a DOI (10.1000/xyz) or a BibTeX entry"}
    try:
        found = lookup_arxiv(arxiv.group(1)) if arxiv else lookup_doi(doi.group(1))
    except LookupsOff:
        return 403, {"error": "online lookups are off in this preview: paste the BibTeX instead", "lookupsOff": True}
    except LookupBusy:
        return 429, {"error": "too many lookups just now; try again in a few seconds"}
    except (OSError, ValueError, ET.ParseError, AttributeError, TypeError) as e:  # (URLError and timeouts are OSErrors)
        return 502, {"error": f"the lookup failed ({e})"}
    if not found:
        return 404, {"error": f"arXiv has no paper {arxiv.group(1)}" if arxiv else f"Crossref doesn't know {doi.group(1)}"}
    return 200, found


def start_verify(server):
    """POST /bib/verify: check the bibliography in the background (run_verify), one check at a time; 403 without
    config.LOOKUPS (it sends the titles of the cited entries to Crossref). 202 with its state (verify_status's)."""
    if not config.LOOKUPS:
        return 403, {"error": "online lookups are off in this preview, and with them the check against Crossref",
                     "lookupsOff": True}
    with server.bib_lock:
        if server.bib_verify.get("state") == "running":
            return 202, dict(server.bib_verify)
        job = server.bib_verify = {"state": "running", "findings": [], "checked": 0, "total": 0}
    threading.Thread(target=run_verify, args=(server, job), daemon=True).start()
    return 202, dict(job)


def verify_status(server):
    """GET /bib/verify: {"state" ("idle", "running", "done" or "failed"), "findings": [{"severity" (fail, warn, info),
    "kind", "key", "file", "line", "msg"}], "checked" (entries checked against Crossref), "total", "offline" (Crossref
    didn't answer), "error"}."""
    with server.bib_lock:
        return dict(server.bib_verify or {"state": "idle", "findings": []})


VERIFY_LIMIT = 30  # (entries checked against Crossref, the cited ones first)


def run_verify(server, job):
    """start_verify's check: keys and titles that are in the bibliography twice and entries never cited; then, for up
    to VERIFY_LIMIT entries (the cited ones), Crossref's year for its DOI, or for its title, whether a preprint has come
    out since, and Crossref's year; and a DOI that Crossref doesn't know. When a few requests fail and none went
    through, Crossref is offline, and the check ends there."""
    try:
        entries = papers_index(server)["entries"]
        findings, by_key, by_title = [], {}, {}
        note = lambda severity, kind, e, msg: findings.append({"severity": severity, "kind": kind, "key": e["key"],
                                                               "file": e["file"], "line": e["line"], "msg": msg})
        for e in entries:
            if (other := by_key.setdefault(e["key"].lower(), e)) is not e:
                note("fail", "duplicate-key", e, f"Key {e['key']} also at {other['file']}:{other['line']}")
            title = plain_title(e["title"])
            if len(title) >= 16 and (other := by_title.setdefault(title, e)) is not e:
                note("warn", "duplicate-title", e, f"Same title as {other['key']}")
            if not e["citeCount"]:
                note("info", "uncited", e, "Never cited in the paper")
        checked = failed = 0
        for e in [e for e in entries if e["citeCount"]][:VERIFY_LIMIT]:
            try:
                if doi := DOI_RE.fullmatch(e["doi"].strip().rstrip(".")):
                    try:
                        found = lookup_doi(doi.group(1), background=True)
                    except urllib.error.HTTPError as http:  # (Crossref answered, but not with the work)
                        if http.code != 404:
                            raise
                        found = None
                        note("warn", "unknown-doi", e, f"Crossref doesn't know its DOI {doi.group(1)}")
                    checked += 1
                    if found and e["year"] and found["year"] and e["year"] != found["year"]:
                        note("warn", "year-mismatch", e, f"Year {e['year']}; Crossref has {found['year']} for its DOI")
                elif e["title"]:
                    query = urllib.parse.urlencode({"query.title": e["title"][:200], "rows": "2"})
                    items = (json.loads(fetch(f"https://api.crossref.org/works?{query}", background=True))
                             .get("message") or {}).get("items") or []
                    checked += 1
                    compare_crossref(e, items, note)
            except (OSError, ValueError, LookupBusy, AttributeError, TypeError):
                failed += 1
                if failed >= 3 and not checked:
                    break
        with server.bib_lock:
            job.update(state="done", findings=findings, checked=checked, total=len(entries), offline=failed > 0 and not checked)
    except Exception as e:  # (the viewer polls for an end: it must come)
        with server.bib_lock:
            job.update(state="failed", error=str(e))


def plain_title(title):
    """A title as compared with others: lower case, letters and digits only."""
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def compare_crossref(e, items, note):
    """Note what Crossref's first work with e's title (items, from a search by title) says otherwise: a published
    version of a preprint, or another year."""
    title = plain_title(e["title"])
    for item in items:
        theirs = plain_title(str((item.get("title") or [""])[0]))
        if theirs and (theirs == title or len(title) > 20 and (title in theirs or theirs in title)):
            venue, year = " ".join(str((item.get("container-title") or [""])[0]).split()), crossref_year(item)
            if (e["arxiv"] or "arxiv" in e["venue"].lower()) and venue and "arxiv" not in venue.lower():
                note("warn", "published-version", e, f"Published since, in {venue}" + (f" ({year})" if year else ""))
            elif e["year"].isdigit() and year and e["year"] != year:
                note("info", "year-diff", e, f"Year {e['year']}; Crossref has {year}" + (f" ({venue})" if venue else ""))
            return
