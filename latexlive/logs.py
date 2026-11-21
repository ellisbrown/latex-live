"""Structured build logs and diagnostics (GET /logs/<doc>.pdf): errors, LaTeX/package/undefined
warnings, overfull boxes with file and line numbers, and the capped raw log tail."""

import os
import re

from . import config
from .build import build_status
from .files import job_file, read_capped

RAW_TAIL = 65_536
MAX_DIAG = 250

ERROR_RE = re.compile(r"^(.+?):(\d+):\s*(.+)$")
WARN_RE = re.compile(
    r"^(LaTeX(?:\s+Font)?\s+Warning|Package\s+\S+\s+Warning|Class\s+\S+\s+Warning|pdfTeX\s+warning)\s*:\s*(.+)$"
)
INPUT_LINE_RE = re.compile(r"on input line (\d+)")
BOX_RE = re.compile(r"^(Overfull|Underfull) \\([hv]box) \(([^)]+)\) in paragraph at lines (\d+)--(\d+)")
FILE_OPEN_RE = re.compile(r"\(([^()\s]+\.[a-zA-Z0-9]+)")


def _clean_path(raw_path, paper_root):
    """Normalize a path from TeX's log to a paper-relative path, or None if outside paper_root."""
    p = raw_path.strip().strip('"')
    if p.startswith("./"):
        p = p[2:]
    root = os.path.realpath(paper_root)
    cand = os.path.realpath(os.path.join(root, p)) if not os.path.isabs(p) else os.path.realpath(p)
    if cand == root or cand.startswith(os.path.join(root, "")):
        return os.path.relpath(cand, root)
    return None


def parse_log(log_text, paper_root, default_file=""):
    """Extract structured warnings and errors with paper-relative file and line numbers from a TeX log."""
    errors, warnings = [], []
    seen_err, seen_warn = set(), set()
    file_stack = []
    lines = log_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        # Track active input file from TeX's balanced (...) log stack.
        pos = 0
        while pos < len(line):
            ch = line[pos]
            if ch == "(":
                m = FILE_OPEN_RE.match(line, pos)
                if m:
                    cleaned = _clean_path(m.group(1), paper_root)
                    file_stack.append(cleaned)
                    pos = m.end()
                    continue
                file_stack.append(None)
            elif ch == ")" and file_stack:
                file_stack.pop()
            pos += 1

        cur_file = next((f for f in reversed(file_stack) if f), default_file or None)

        em = ERROR_RE.match(line)
        if em and not em.group(3).lstrip().startswith("==> Fatal error"):
            fpath = _clean_path(em.group(1), paper_root) or cur_file
            lno = int(em.group(2))
            msg = em.group(3).strip()
            key = (fpath, lno, msg)
            if key not in seen_err and len(errors) < MAX_DIAG:
                seen_err.add(key)
                errors.append({"severity": "error", "kind": "error", "file": fpath, "line": lno, "msg": msg})
            i += 1
            continue

        bm = BOX_RE.match(line)
        if bm and len(warnings) < MAX_DIAG:
            kind = bm.group(1).lower()
            lno = int(bm.group(4))
            msg = f"{bm.group(1)} \\{bm.group(2)} ({bm.group(3)}) at lines {bm.group(4)}--{bm.group(5)}"
            key = (cur_file, lno, msg)
            if key not in seen_warn:
                seen_warn.add(key)
                warnings.append({"severity": "info" if kind == "underfull" else "warning",
                                 "kind": kind, "file": cur_file, "line": lno, "msg": msg})
            i += 1
            continue

        wm = WARN_RE.match(line)
        if wm and len(warnings) < MAX_DIAG:
            msg_parts = [f"{wm.group(1)}: {wm.group(2).strip()}"]
            # Consume wrapped continuation lines (indented or matching package continuation).
            j = i + 1
            while j < len(lines) and j < i + 5 and lines[j] and (lines[j].startswith(" ") or lines[j].startswith("(")):
                if lines[j].startswith("(") and FILE_OPEN_RE.match(lines[j]):
                    break
                msg_parts.append(lines[j].strip())
                j += 1
            full_msg = " ".join(msg_parts)
            lm = INPUT_LINE_RE.search(full_msg)
            lno = int(lm.group(1)) if lm else None
            key = (cur_file, lno, full_msg)
            if key not in seen_warn:
                seen_warn.add(key)
                warnings.append({"severity": "warning", "kind": "warning", "file": cur_file,
                                 "line": lno, "msg": full_msg[:300]})
            i = j
            continue

        i += 1
    return errors, warnings


def _redact_root(text, paper_root):
    """Replace absolute paths to the server's paper folder with relative paths in the raw log."""
    root = os.path.realpath(paper_root).rstrip(os.sep) + os.sep
    return text.replace(root, "./")


def build_logs(server, pdf_name):
    """GET /logs/<doc>.pdf: structured diagnostics plus the capped raw log tail."""
    root = server.paper_root
    doc_file = f"{pdf_name[:-4]}.tex"
    status = build_status(server, pdf_name) or {}
    log_path = job_file(root, pdf_name, ".log")
    raw_text = read_capped(log_path, config.OUTPUT_LIMIT)
    log_text = raw_text if isinstance(raw_text, str) else (raw_text.decode("utf-8", errors="replace") if raw_text else "")
    errors, warnings = parse_log(log_text, root, default_file=doc_file)
    # Merge status.json errors & undefined refs (which locate exact source lines via latex_report).
    seen_e = {(e.get("file"), e.get("line"), e.get("msg")) for e in errors}
    for e in status.get("errors") or []:
        key = (e.get("file"), e.get("line"), e.get("msg"))
        if key not in seen_e:
            seen_e.add(key)
            errors.insert(0, {"severity": "error", "kind": "error", "file": e.get("file"),
                              "line": e.get("line"), "msg": e.get("msg") or ""})
    seen_u = {(w.get("file"), w.get("line"), w.get("msg")) for w in warnings}
    for u in status.get("undefined") or []:
        msg = f"Undefined {u.get('kind', 'reference')} '{u.get('key', '')}'"
        key = (u.get("file"), u.get("line"), msg)
        if key not in seen_u:
            seen_u.add(key)
            warnings.insert(0, {"severity": "warning", "kind": "undefined", "file": u.get("file"),
                                "line": u.get("line"), "msg": msg})
    tail = _redact_root(log_text[-RAW_TAIL:], root)
    return {
        "ok": status.get("ok"),
        "time": status.get("time"),
        "durationMs": status.get("durationMs"),
        "errors": errors[:MAX_DIAG],
        "warnings": warnings[:MAX_DIAG],
        "raw": tail,
        "truncated": len(log_text) > RAW_TAIL,
    }
