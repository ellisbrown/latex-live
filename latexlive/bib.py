"""Bibliography library and reference lookup (Papers panel): index .bib entries with live citation
counts across the paper's .tex files, look up arXiv IDs or DOIs (from hardcoded public endpoints only),
append entries safely to a .bib file, and verify bibliography entries against Crossref."""

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from .commits import commit_others, paused, remember_write, updating
from .latex import brace_group
from .sources import NOTE_LOCK, SourceChanged, editable_files, read_editable, write_source, wrote

BIB_ENTRY_RE = re.compile(r"@(\w+)\s*[{(]\s*([^,\s{}()]+)\s*,")
FIELD_RE = re.compile(r"\b([a-zA-Z][a-zA-Z0-9_-]*)\s*=\s*")
CITE_RE = re.compile(r"\\[a-zA-Z]*cite[a-zA-Z]*\*?(?:\[[^\]]*\]){0,2}\{([^{}]+)\}")
ARXIV_ID_RE = re.compile(
    r"(?:(?:https?://)?(?:www\.)?arxiv\.org/(?:abs|pdf|html)/|arXiv:)?(\d{4}\.\d{4,5}(?:v\d+)?|[a-z-]+/\d{7}(?:v\d+)?)(?:\.pdf)?",
    re.I,
)
DOI_RE = re.compile(r"(?:(?:https?://)?(?:dx\.)?doi\.org/|doi:\s*)?(10\.\d{4,9}/[^\s\"'<>]+)", re.I)

USER_AGENT = "latex-live/0.5 (https://github.com/ellisbrown/latex-live)"
NET_TIMEOUT = 8
ALLOWED_NET_HOSTS = ("export.arxiv.org", "api.crossref.org")


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Allow HTTP redirects only to the hardcoded HTTPS metadata hosts (blocks redirect SSRF)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_NET_HOSTS or parsed.port not in (None, 443):
            raise urllib.error.URLError(f"blocked redirect to disallowed host: {parsed.hostname}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_NET_OPENER = urllib.request.build_opener(_SafeRedirectHandler)


def _clean_field(val):
    """Strip outer BibTeX braces/quotes and collapse whitespace."""
    return " ".join(val.replace("{", "").replace("}", "").split())


def _parse_fields(body):
    """Parse key = value fields from the body of one BibTeX entry."""
    fields = {}
    i, n = 0, len(body)
    while i < n:
        m = FIELD_RE.search(body, i)
        if not m:
            break
        name = m.group(1).lower()
        j = m.end()
        if j >= n:
            break
        if body[j] == "{":
            val, end = brace_group(body, j)
            i = end
        elif body[j] == '"':
            k = j + 1
            while k < n and not (body[k] == '"' and body[k - 1] != "\\"):
                k += 1
            val, i = body[j + 1:k], k + 1
        else:
            k = j
            while k < n and body[k] not in ",}\r\n":
                k += 1
            val, i = body[j:k].strip(), k + 1
        if name not in fields:
            fields[name] = _clean_field(val)
    return fields


def parse_bib_entries(text, rel_path=""):
    """Parse BibTeX entries from text into a list of dicts with line numbers."""
    matches = [m for m in BIB_ENTRY_RE.finditer(text) if m.group(1).lower() not in ("comment", "string", "preamble")]
    entries = []
    line, at = 1, 0
    for idx, m in enumerate(matches):
        line += text.count("\n", at, m.start())
        at = m.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        chunk = text[m.start():end].strip()
        body = text[m.end():end]
        fields = _parse_fields(body)
        venue = fields.get("booktitle") or fields.get("journal") or fields.get("publisher") or ""
        arxiv = fields.get("eprint") or ""
        if not arxiv and "arxiv" in (fields.get("url") or "").lower():
            am = ARXIV_ID_RE.search(fields["url"])
            if am:
                arxiv = am.group(1)
        entries.append({
            "key": m.group(2),
            "entryType": m.group(1).lower(),
            "title": fields.get("title", "")[:240],
            "author": fields.get("author", "")[:240],
            "year": fields.get("year", "")[:16],
            "venue": venue[:160],
            "doi": fields.get("doi", "")[:120],
            "arxiv": arxiv[:40],
            "url": fields.get("url", "")[:240],
            "file": rel_path,
            "line": line,
        })
    return entries


def papers_index(server):
    """GET /papers/index: all .bib entries with live citation counts and locations from .tex files."""
    files = editable_files(server)
    entries, bib_files = [], []
    cite_locs = {}
    for rel, real in files.items():
        if not rel.endswith((".bib", ".tex")):
            continue
        try:
            text = read_editable(real).decode("utf-8", errors="replace")
        except (OSError, SourceChanged):
            continue
        if rel.endswith(".bib"):
            bib_files.append(rel)
            entries.extend(parse_bib_entries(text, rel))
        else:
            clean = re.sub(r"(?<!\\)%.*", "", text)
            line, at = 1, 0
            for m in CITE_RE.finditer(clean):
                line += clean.count("\n", at, m.start())
                at = m.start()
                for raw_key in m.group(1).split(","):
                    k = raw_key.strip()
                    if k:
                        cite_locs.setdefault(k, []).append({"file": rel, "line": line})
    total = 0
    for e in entries:
        locs = cite_locs.get(e["key"], [])
        e["citeCount"] = len(locs)
        e["citedIn"] = locs[:10]
        total += len(locs)
    return {"entries": entries, "bibFiles": bib_files, "totalCitations": total}


def _make_key(author, year, title):
    """Build a readable BibTeX cite key like gu2023mamba."""
    first = (author.split(" and ")[0] if author else "ref").strip()
    surname = (first.split(",")[0] if "," in first else first.split()[-1]) if first else "ref"
    surname = re.sub(r"[^a-zA-Z]", "", surname).lower() or "ref"
    yr = re.sub(r"[^0-9]", "", str(year or ""))[:4]
    stop = {"a", "an", "the", "of", "for", "in", "on", "to", "with", "via", "and", "from", "towards", "toward"}
    words = [w.lower() for w in re.findall(r"[A-Za-z0-9]+", title or "") if w.lower() not in stop]
    word = words[0] if words else "paper"
    return f"{surname}{yr}{word}"


def _fetch_url(url, accept="application/json"):
    """Fetch a hardcoded public metadata URL with strict host-allowlisted redirects and timeout."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_NET_HOSTS or parsed.port not in (None, 443):
        raise ValueError(f"disallowed host: {parsed.hostname}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    with _NET_OPENER.open(req, timeout=NET_TIMEOUT) as r:
        return r.read(1_000_000)


def lookup_arxiv(arxiv_id):
    """Fetch metadata for a validated arXiv ID from export.arxiv.org."""
    url = f"https://export.arxiv.org/api/query?id_list={urllib.parse.quote(arxiv_id, safe='/.')}"
    xml_bytes = _fetch_url(url, accept="application/atom+xml")
    root = ET.fromstring(xml_bytes)
    ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    entry = root.find("a:entry", ns)
    if entry is None:
        return None
    title_el = entry.find("a:title", ns)
    title = " ".join((title_el.text or "").split()) if title_el is not None else ""
    if not title or title.lower() == "error":
        return None
    authors = [el.text.strip() for el in entry.findall("a:author/a:name", ns) if el.text]
    pub = (entry.findtext("a:published", "", ns) or "")[:4]
    doi = (entry.findtext("arxiv:doi", "", ns) or "").strip()
    cat_el = entry.find("arxiv:primary_category", ns)
    primary = cat_el.attrib.get("term", "") if cat_el is not None else ""
    author_str = " and ".join(authors)
    key = _make_key(author_str, pub, title)
    lines = [
        f"@article{{{key},",
        f"  title={{{{{title}}}}},",
        f"  author={{{author_str}}},",
        f"  journal={{arXiv preprint arXiv:{arxiv_id}}},",
        f"  year={{{pub}}},",
        f"  eprint={{{arxiv_id}}},",
        "  archivePrefix={arXiv},",
    ]
    if primary:
        lines.append(f"  primaryClass={{{primary}}},")
    if doi:
        lines.append(f"  doi={{{doi}}},")
    lines.append("}")
    return {
        "source": "arxiv",
        "key": key,
        "entryType": "article",
        "title": title,
        "author": author_str,
        "year": pub,
        "venue": f"arXiv:{arxiv_id}",
        "doi": doi,
        "arxiv": arxiv_id,
        "bibtex": "\n".join(lines),
    }


def lookup_doi(doi):
    """Fetch metadata for a validated DOI from api.crossref.org."""
    url = f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='')}"
    data = json.loads(_fetch_url(url).decode("utf-8", errors="replace"))
    msg = data.get("message") or {}
    title = " ".join(((msg.get("title") or [""])[0] or "").split())
    if not title:
        return None
    authors = []
    for a in msg.get("author") or []:
        fam, giv = a.get("family", ""), a.get("given", "")
        if fam and giv:
            authors.append(f"{giv} {fam}")
        elif fam:
            authors.append(fam)
    author_str = " and ".join(authors)
    year = ""
    for k in ("published-print", "published-online", "issued", "created"):
        parts = ((msg.get(k) or {}).get("date-parts") or [[]])[0]
        if parts and parts[0]:
            year = str(parts[0])
            break
    venue = ((msg.get("container-title") or [""])[0] or "").strip()
    kind = msg.get("type", "")
    entry_type = "inproceedings" if "proceedings" in kind else "article"
    venue_field = "booktitle" if entry_type == "inproceedings" else "journal"
    key = _make_key(author_str, year, title)
    lines = [
        f"@{entry_type}{{{key},",
        f"  title={{{{{title}}}}},",
        f"  author={{{author_str}}},",
    ]
    if venue:
        lines.append(f"  {venue_field}={{{venue}}},")
    if year:
        lines.append(f"  year={{{year}}},")
    lines.append(f"  doi={{{doi}}},")
    lines.append("}")
    return {
        "source": "crossref",
        "key": key,
        "entryType": entry_type,
        "title": title,
        "author": author_str,
        "year": year,
        "venue": venue,
        "doi": doi,
        "arxiv": "",
        "bibtex": "\n".join(lines),
    }


def lookup_request(req):
    """POST /bib/lookup {query}: resolve an arXiv ID/URL, DOI, or raw BibTeX into a preview entry."""
    query = (req.get("query") or "").strip() if isinstance(req, dict) else ""
    if not query or len(query) > 20_000:
        return 400, {"error": "enter an arXiv ID, DOI, or BibTeX entry"}
    if query.startswith("@"):
        parsed = parse_bib_entries(query)
        if not parsed:
            return 400, {"error": "could not parse BibTeX entry"}
        return 200, {**parsed[0], "source": "bibtex", "bibtex": query}
    am = ARXIV_ID_RE.fullmatch(query)
    dm = DOI_RE.fullmatch(query.rstrip("."))
    try:
        if am:
            res = lookup_arxiv(am.group(1))
            return (200, res) if res else (404, {"error": f"arXiv entry {am.group(1)} not found"})
        if dm:
            res = lookup_doi(dm.group(1))
            return (200, res) if res else (404, {"error": f"DOI {dm.group(1)} not found on Crossref"})
    except (urllib.error.URLError, TimeoutError, OSError, ET.ParseError, ValueError) as e:
        return 502, {"error": f"lookup failed ({e})"}
    return 400, {"error": "unrecognized input: paste an arXiv ID (e.g. 2312.00752), a DOI (10.xxxx/...), or raw BibTeX"}


def add_reference_request(server, req, user):
    """POST /bib/add {bibtex, file?}: append a BibTeX entry to a .bib file safely under NOTE_LOCK."""
    if not isinstance(req, dict):
        return 400, {"error": "expected JSON object"}
    bibtex = (req.get("bibtex") or "").strip()
    if not bibtex or len(bibtex) > 50_000:
        return 400, {"error": "invalid BibTeX entry"}
    parsed = parse_bib_entries(bibtex)
    if not parsed:
        return 400, {"error": "could not parse BibTeX entry"}
    new_key = parsed[0]["key"]
    files = editable_files(server)
    bib_files = [rel for rel in files if rel.endswith(".bib")]
    target = req.get("file") or (bib_files[0] if bib_files else None)
    if not target or target not in files or not target.endswith(".bib"):
        return 404, {"error": "no .bib file found in this paper (create one first, e.g. references.bib)"}
    real = files[target]
    with NOTE_LOCK:
        if paused(server):
            return updating()
        try:
            raw = read_editable(real).decode("utf-8")
        except (OSError, SourceChanged, UnicodeDecodeError) as e:
            return 500, {"error": f"could not read {target}: {e}"}
        existing_keys = {e["key"] for e in parse_bib_entries(raw, target)}
        if new_key in existing_keys:
            return 409, {"error": f"@{new_key} already exists in {target}"}
        sep = "\n\n" if raw and not raw.endswith("\n\n") else ("\n" if raw and not raw.endswith("\n") else "")
        updated = raw + sep + bibtex + "\n"
        if not commit_others(server, user, target):
            return updating()
        try:
            write_source(real, raw, updated)
        except (OSError, SourceChanged) as e:
            return 409, {"error": f"could not write {target}: {e}"}
        remember_write(server, user, target, "edit")
        wrote(server, user, target)
    return 200, {"ok": True, "file": target, "key": new_key, "entry": parsed[0]}


def _norm_title(title):
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def _crossref_query_title(title):
    """Query Crossref for the closest published work matching title."""
    q = urllib.parse.urlencode({"query.title": title[:200], "rows": "2"})
    url = f"https://api.crossref.org/works?{q}"
    data = json.loads(_fetch_url(url).decode("utf-8", errors="replace"))
    return (data.get("message") or {}).get("items") or []


def _run_verify(server, job):
    """Background Crossref and local bibliography verification."""
    try:
        idx = papers_index(server)
        entries = idx["entries"]
        findings = []
        # 1. Local checks: duplicate keys, duplicate normalized titles, uncited entries.
        by_key, by_title = {}, {}
        for e in entries:
            if e["key"] in by_key:
                findings.append({"severity": "fail", "kind": "duplicate-key", "key": e["key"],
                                 "file": e["file"], "line": e["line"],
                                 "msg": f"Duplicate key '{e['key']}' (also in {by_key[e['key']]['file']}:{by_key[e['key']]['line']})"})
            else:
                by_key[e["key"]] = e
            nt = _norm_title(e["title"])
            if len(nt) >= 16:
                if nt in by_title and by_title[nt]["key"] != e["key"]:
                    findings.append({"severity": "warn", "kind": "duplicate-title", "key": e["key"],
                                     "file": e["file"], "line": e["line"],
                                     "msg": f"Same title as '{by_title[nt]['key']}'"})
                else:
                    by_title[nt] = e
            if e["citeCount"] == 0:
                findings.append({"severity": "info", "kind": "uncited", "key": e["key"],
                                 "file": e["file"], "line": e["line"], "msg": "Entry is never cited in the paper"})

        # 2. Crossref audit on up to 30 cited entries (preprints with published versions, year/title checks).
        to_check = [e for e in entries if e["citeCount"] > 0][:30] or entries[:20]
        checked, net_errors = 0, 0
        for e in to_check:
            if not e["title"] and not e["doi"]:
                continue
            try:
                doi_m = DOI_RE.fullmatch((e["doi"] or "").strip().rstrip(".")) if e["doi"] else None
                if doi_m:
                    ref = lookup_doi(doi_m.group(1))
                    if ref:
                        checked += 1
                        if e["year"] and ref["year"] and e["year"] != ref["year"]:
                            findings.append({"severity": "warn", "kind": "year-mismatch", "key": e["key"],
                                             "file": e["file"], "line": e["line"],
                                             "msg": f"Year is {e['year']} in .bib, Crossref has {ref['year']} for DOI {e['doi']}"})
                elif e["title"]:
                    items = _crossref_query_title(e["title"])
                    checked += 1
                    nt = _norm_title(e["title"])
                    for item in items:
                        it_title = _norm_title(((item.get("title") or [""])[0] or ""))
                        if it_title and (it_title == nt or (len(nt) > 20 and (nt in it_title or it_title in nt))):
                            venue = ((item.get("container-title") or [""])[0] or "").strip()
                            doi = item.get("DOI", "")
                            parts = ((item.get("issued") or {}).get("date-parts") or [[]])[0]
                            yr = str(parts[0]) if parts and parts[0] else ""
                            is_preprint = "arxiv" in (e["venue"] or "").lower() or bool(e["arxiv"])
                            if is_preprint and venue and "arxiv" not in venue.lower():
                                findings.append({"severity": "warn", "kind": "published-version", "key": e["key"],
                                                 "file": e["file"], "line": e["line"], "doi": doi,
                                                 "msg": f"Preprint has published version in {venue}" + (f" ({yr})" if yr else "")})
                            elif e["year"] and yr and e["year"] != yr and abs(int(e["year"]) - int(yr)) >= 1 if (e["year"].isdigit() and yr.isdigit()) else False:
                                findings.append({"severity": "info", "kind": "year-diff", "key": e["key"],
                                                 "file": e["file"], "line": e["line"],
                                                 "msg": f"Year is {e['year']}, Crossref lists {yr}" + (f" ({venue})" if venue else "")})
                            break
            except Exception:
                net_errors += 1
                if net_errors >= 3 and checked == 0:
                    break
        with server.bib_lock:
            job.update(state="done", findings=findings, checked=checked, total=len(entries),
                       offline=(net_errors > 0 and checked == 0), finished=time.strftime("%H:%M:%S"))
    except Exception as e:
        with server.bib_lock:
            job.update(state="failed", error=str(e))


def start_verify(server):
    """POST /bib/verify: run bibliography audit against local rules + Crossref in the background."""
    with server.bib_lock:
        cur = server.bib_verify
        if cur.get("state") == "running":
            return 202, dict(cur)
        job = {"state": "running", "started": time.strftime("%H:%M:%S"), "findings": [], "checked": 0, "total": 0}
        server.bib_verify = job
    threading.Thread(target=_run_verify, args=(server, job), daemon=True).start()
    return 202, dict(job)


def verify_status(server):
    """GET /bib/verify: current bibliography verification state and findings."""
    with server.bib_lock:
        return dict(server.bib_verify or {"state": "idle", "findings": []})
