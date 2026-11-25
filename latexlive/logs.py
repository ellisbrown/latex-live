"""The build's log, for editors (GET /logs/<doc>.pdf): its errors, warnings and overfull or underfull boxes, each
with the paper's file and line where the log says, and the end of the log as it is, with the paper's folder as ./."""

import os
import re
import threading

from .build import build_status
from .files import job_file, open_output, redact_root, redactor, within
from .sources import editable_real

PARSE_LIMIT = 4 << 20  # (the most of the log's end that is read: the paper decides how much TeX writes)
RAW_TAIL = 64 << 10  # (the most of that sent as it is)
MAX_DIAG = 250  # (errors, warnings, and boxes, each)
RESOLVE_LIMIT = 2000  # (the most names of files one parse looks up: a paper reads far fewer)
STACK_LIMIT = 1000  # (the deepest file stack kept: TeX's own is far shallower)
PARSING = threading.Lock()  # (viewers ask together once a build ends: one parses, the others take its result)

ERROR_RE = re.compile(r"^([^:\s][^:]*):(\d{1,9}): (.*)$")  # (-file-line-error's file:line: message)
WARN_RE = re.compile(r"^(LaTeX(?: Font)? Warning|Package \S+ Warning|Class \S+ Warning|pdfTeX warning(?: \(ext4\))?)\s*:\s*(.+)$")
INPUT_LINE_RE = re.compile(r"on input line (\d{1,9})")
BOX_RE = re.compile(r"^(Overfull|Underfull) \\([hv]box) \(([^)]*)\) (?:in paragraph|in alignment|detected) at lines? (?:\d{1,9}--)?(\d{1,9})")
PAREN_RE = re.compile(r'\((?:"([^"()]*)"|([^\s()]*))|\)')  # (TeX quotes a name with spaces)


def parse_log(text, paper_root):
    """(errors, warnings) in a TeX log, each {severity, kind, file, line, msg}. An error counts only where its
    file:line names a source file of the paper (editable_real: not in the build folder or a dot folder, not
    outside the paper), so a timestamp or a package's message with :NN: is none. A warning or a box is in the
    file that TeX's log opened last and has not closed ("(path" ... ")"), if that is one of the paper's, and
    in none otherwise; a box's line is where its paragraph ends (where it began may be in another file). An error's
    or a warning's own lines open and close no file. The messages show the paper's folder as ./ (redact_root)."""
    root = os.path.abspath(paper_root)
    bases, redact = {root, os.path.realpath(root)}, redactor(root)
    found = {}  # (the names looked up: each once)

    def resolve(name):
        if name in found:
            return found[name]
        if len(found) >= RESOLVE_LIMIT:
            return None
        rel = name.strip('"')
        if os.path.isabs(rel):
            rel = next((os.path.relpath(rel, base) for base in bases if within(rel, base)), None)
        rel = os.path.normpath(rel) if rel else None
        found[name] = rel if rel and editable_real(root, rel) else None
        return found[name]

    def current():  # (the paper's file that the log opened last and has not closed, if it is one)
        return resolve(next((name for name in reversed(stack) if name), "")) if stack else None

    errors, warnings, seen, boxes = [], [], set(), 0
    stack, deeper, skipping = [], 0, False  # (deeper: names opened past STACK_LIMIT, still to close)
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if skipping:  # (a box's contents, up to a blank line: their parentheses are not files)
            skipping = bool(line.strip())
            continue
        error, box, warning = ERROR_RE.match(line), BOX_RE.match(line), WARN_RE.match(line)
        if error:  # (one of the paper's files, else another's: its message's parentheses are not files either way)
            path = len(errors) < MAX_DIAG and not error[3].lstrip().startswith("==> Fatal error") and resolve(error[1])
            msg = path and redact(error[3].strip())[:300]
            if path and ("e", path, error[2], msg) not in seen:
                seen.add(("e", path, error[2], msg))
                errors.append({"severity": "error", "kind": "error", "file": path, "line": int(error[2]), "msg": msg})
            continue
        if box:  # (counted apart from the other warnings: boxes)
            skipping = True
            if boxes < MAX_DIAG:
                before = len(warnings)
                add_warning(warnings, seen, "info" if box[1] == "Underfull" else "warning", box[1].lower(), current(),
                            int(box[4]), redact(f"{box[1]} \\{box[2]} ({box[3]})"))
                boxes += len(warnings) - before
            continue
        if warning:
            parts = [f"{warning[1]}: {warning[2].strip()}"]
            end = i  # (a package's next lines; the first few go in the message)
            while end < len(lines) and end - i < 50 and re.match(r"\(\S+\)\s", lines[end]):
                end += 1
            parts += [later.split(")", 1)[1].strip() for later in lines[i:end]]
            i = end
            if len(warnings) - boxes < MAX_DIAG:
                msg = redact(" ".join(parts[:6]))
                at = INPUT_LINE_RE.search(msg)
                add_warning(warnings, seen, "warning", "warning", current(), int(at[1]) if at else None, msg)
            continue
        for paren in PAREN_RE.finditer(line):
            if paren[0] == ")":
                if deeper:
                    deeper -= 1
                elif stack:
                    stack.pop()
            elif len(stack) >= STACK_LIMIT:
                deeper += 1
            else:
                name = paren[1] if paren[1] is not None else paren[2]
                stack.append(name if "." in name else None)  # (a name with an extension opens a file)
    return errors, warnings


def add_warning(warnings, seen, severity, kind, path, line, msg):
    """Add a warning, unless one with the same place and message is there already."""
    msg, line = msg[:300], line if path else None
    if ("w", path, line, msg) not in seen:
        seen.add(("w", path, line, msg))
        warnings.append({"severity": severity, "kind": kind, "file": path, "line": line, "msg": msg})


def read_log(server, path):
    """The parsed end of the log at path: {errors, warnings, raw, truncated}, at most PARSE_LIMIT bytes of it read,
    without following a link in shared mode (open_output). The last one is kept for as long as the log's size and
    modification time stay the same (each viewer of the paper asks after every build)."""
    f = open_output(path)
    if f is None:
        return {"errors": [], "warnings": [], "raw": "", "truncated": False}
    with f, PARSING:
        st = os.fstat(f.fileno())
        key = (path, st.st_ino, st.st_mtime_ns, st.st_size)
        cached = getattr(server, "parsed_log", None)
        if cached and cached[0] == key:
            return cached[1]
        start = max(0, st.st_size - PARSE_LIMIT)
        f.seek(start)
        text = f.read(PARSE_LIMIT).decode("utf-8", errors="replace")
        if start and "\n" in text[:-1]:  # (from the first whole line)
            text = text.partition("\n")[2]
        errors, warnings = parse_log(text, server.paper_root)
        tail = text[-RAW_TAIL:]
        if len(text) > RAW_TAIL and "\n" in tail[:-1]:
            tail = tail.partition("\n")[2]
        parsed = {"errors": errors, "warnings": warnings, "raw": redact_root(tail, server.paper_root),
                  "truncated": bool(start) or len(text) > RAW_TAIL}
        server.parsed_log = (key, parsed)
        return parsed


def build_logs(server, pdf_name):
    """GET /logs/<doc>.pdf: the last build's result (ok, time, durationMs), the errors and warnings of its log, with
    the build's own errors that the log names no source file for (e.g. one in a package, or a bibtex failure)
    after them, and the end of the log."""
    status = build_status(server, pdf_name) or {}
    parsed = read_log(server, job_file(server.paper_root, pdf_name, ".log"))
    errors = list(parsed["errors"])
    seen = {(e["line"], e["msg"]) for e in errors}
    for e in status.get("errors") or []:
        if not isinstance(e, dict):
            continue
        line = e.get("line") if type(e.get("line")) is int and e["line"] > 0 else None
        msg = redact_root(str(e.get("msg") or "").strip(), server.paper_root)[:300]
        if (line, msg) not in seen and len(errors) < MAX_DIAG:
            seen.add((line, msg))
            path = redact_root(e["file"], server.paper_root) if isinstance(e.get("file"), str) else None
            path = path[2:] if path and path.startswith("./") else path  # (as the log's own errors name them)
            errors.append({"severity": "error", "kind": "error", "file": path, "line": line, "msg": msg})
    return {"ok": status.get("ok"), "time": status.get("time"), "durationMs": status.get("durationMs"),
            "errors": errors, "warnings": parsed["warnings"], "raw": parsed["raw"], "truncated": parsed["truncated"]}
