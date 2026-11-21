"""Owner-only bring-your-own-agent revision hook (POST /agent/revise, GET /agent/job, GET /agent/info):
invokes a local coding-agent CLI (claude, codex, pi, or LATEX_LIVE_AGENT_CMD) non-interactively in a
tool-less / read-only mode inside an empty temporary directory to propose an inline replacement and
rationale for selected LaTeX prose or a margin note. Never writes files on disk — the viewer displays
an Accept / Reject diff card inside CodeMirror."""

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid

MAX_TEXT = 32_000
MAX_CONTEXT = 4_000
MAX_INSTRUCTION = 2_000
MAX_OUTPUT = 262_144
AGENT_TIMEOUT = 90


def detect_agents():
    """Return available local agent CLIs without executing them."""
    custom = os.environ.get("LATEX_LIVE_AGENT_CMD", "").strip()
    found = []
    if custom:
        found.append({"id": "custom", "label": "Custom ($LATEX_LIVE_AGENT_CMD)"})
    for name, label in (("claude", "Claude Code"), ("codex", "Codex"), ("pi", "Pi")):
        if shutil.which(name):
            found.append({"id": name, "label": label})
    return {"available": found, "default": found[0]["id"] if found else None, "configured": bool(found)}


def _build_prompt(mode, text, before, after, note_text, instruction):
    """Construct a tight prompt asking the agent for strict JSON {replacement, rationale, changes}."""
    task = {
        "polish": "Polish the selected LaTeX prose for clarity, concision, and academic flow while preserving every technical claim, citation, label, and LaTeX command.",
        "proofread": "Proofread the selected LaTeX prose, fixing grammar, spelling, punctuation, and awkward phrasing with minimal changes.",
        "note": f"Revise the selected LaTeX passage to address this reviewer/coauthor note: {note_text}",
        "custom": instruction or "Improve the selected LaTeX passage.",
    }.get(mode, "Improve the selected LaTeX passage.")
    if instruction and mode != "custom":
        task += f" Additional instruction: {instruction}"
    return (
        "You are assisting with a LaTeX academic paper edit. "
        f"{task}\n\n"
        "Rules:\n"
        "- Preserve all valid LaTeX syntax, citations (\\cite...), references (\\ref...), macros, and math.\n"
        "- Return ONLY a single valid JSON object with keys:\n"
        '  {"replacement": "<exact replacement LaTeX for the selected text>", '
        '"rationale": "<one concise sentence explaining why>", '
        '"changes": [{"kind": "<concision|grammar|clarity|fix>", "detail": "<short phrase>"}]}\n'
        "- Do not wrap in commentary outside the JSON object.\n\n"
        f"--- BEFORE CONTEXT ---\n{before}\n"
        f"--- SELECTED TEXT TO REPLACE ---\n{text}\n"
        f"--- AFTER CONTEXT ---\n{after}\n"
    )


def _extract_json(output):
    """Extract the JSON object from agent stdout (tolerating markdown ```json fences)."""
    raw = (output or "").strip()
    if not raw:
        return None
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S)
    if fence:
        raw = fence.group(1)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(raw[start:end + 1])
    except ValueError:
        return None
    if isinstance(obj, dict) and isinstance(obj.get("replacement"), str):
        return {
            "replacement": obj["replacement"],
            "rationale": str(obj.get("rationale") or "")[:400],
            "changes": obj.get("changes") if isinstance(obj.get("changes"), list) else [],
        }
    return None


def _agent_argv(agent_id):
    """Command argv for a detected agent CLI, with tools disabled / read-only sandbox enforced."""
    if agent_id == "custom":
        cmd = os.environ.get("LATEX_LIVE_AGENT_CMD", "").strip()
        return shlex.split(cmd) if cmd else None
    if agent_id == "claude" and shutil.which("claude"):
        return ["claude", "-p", "--output-format", "text", "--tools", ""]
    if agent_id == "codex" and shutil.which("codex"):
        return ["codex", "exec", "-s", "read-only", "-"]
    if agent_id == "pi" and shutil.which("pi"):
        return ["pi", "-p", "--no-tools", "--no-context-files"]
    return None


def _kill_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(3)
    except Exception:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            proc.wait(2)
        except Exception:
            pass


def _run_job(server, job, argv, prompt):
    t0 = time.monotonic()
    try:
        # Run in an isolated empty directory so prompt content can never read or modify paper files.
        with tempfile.TemporaryDirectory(prefix="latex-live-agent-") as scratch:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=scratch,
                start_new_session=True,
            )
            timer = threading.Timer(AGENT_TIMEOUT, _kill_group, args=(proc,))
            timer.daemon = True
            timer.start()
            timed_out = False
            try:
                if proc.stdin:
                    try:
                        proc.stdin.write(prompt.encode("utf-8"))
                    except BrokenPipeError:
                        pass
                    try:
                        proc.stdin.close()
                    except OSError:
                        pass
                out_chunks, out_len = [], 0
                while True:
                    chunk = proc.stdout.read(min(16384, MAX_OUTPUT - out_len))
                    if not chunk:
                        break
                    out_chunks.append(chunk)
                    out_len += len(chunk)
                    if out_len >= MAX_OUTPUT:
                        _kill_group(proc)
                        break
                err_bytes = proc.stderr.read(32768) if proc.stderr else b""
                rc = proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_group(proc)
                rc, out_chunks, err_bytes = -1, [], b""
            finally:
                timer.cancel()
                for s in (proc.stdout, proc.stderr):
                    if s:
                        try:
                            s.close()
                        except OSError:
                            pass
        duration_ms = round((time.monotonic() - t0) * 1000)
        if timed_out or (duration_ms >= AGENT_TIMEOUT * 1000 and rc != 0):
            with server.agent_lock:
                job.update(state="failed", error=f"agent timed out after {AGENT_TIMEOUT}s", durationMs=duration_ms)
            return
        stdout = b"".join(out_chunks).decode("utf-8", errors="replace")
        stderr = err_bytes.decode("utf-8", errors="replace")
        if rc != 0:
            err = (stderr or stdout or f"exit {rc}").strip().splitlines()[-1][:240]
            with server.agent_lock:
                job.update(state="failed", error=f"agent exited with {rc}: {err}", durationMs=duration_ms)
            return
        parsed = _extract_json(stdout)
        if not parsed:
            with server.agent_lock:
                job.update(state="failed", error="agent did not return a JSON replacement", durationMs=duration_ms)
            return
        with server.agent_lock:
            job.update(state="done", durationMs=duration_ms, **parsed)
    except OSError as e:
        with server.agent_lock:
            job.update(state="failed", error=f"could not start agent: {e}")


def start_revise(server, req):
    """POST /agent/revise: launch a bounded non-interactive agent revision job."""
    if not isinstance(req, dict):
        return 400, {"error": "expected JSON object"}
    mode = req.get("mode") or "polish"
    if mode not in ("polish", "proofread", "note", "custom"):
        return 400, {"error": "mode must be polish, proofread, note, or custom"}
    text = req.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        return 400, {"error": "select text (up to 32 KB) to revise"}
    protected = os.environ.get("LATEX_LIVE_PROTECTED", "").strip()
    if protected and not req.get("allowProtected"):
        try:
            if re.search(protected, text):
                return 409, {"error": "selection contains protected text (LATEX_LIVE_PROTECTED); not revising automatically"}
        except re.error:
            pass
    info = detect_agents()
    agent_id = req.get("agent") or info["default"]
    argv = _agent_argv(agent_id) if agent_id else None
    if not argv:
        return 503, {"error": "no local agent CLI found (install claude, codex, or pi, or set LATEX_LIVE_AGENT_CMD)"}
    before = str(req.get("before") or "")[-MAX_CONTEXT:]
    after = str(req.get("after") or "")[:MAX_CONTEXT]
    note_text = str(req.get("note") or "")[:MAX_INSTRUCTION]
    instruction = str(req.get("instruction") or "")[:MAX_INSTRUCTION]
    prompt = _build_prompt(mode, text, before, after, note_text, instruction)

    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "state": "running", "agent": agent_id, "mode": mode, "started": time.time()}
    with server.agent_lock:
        running = sum(1 for j in server.agent_jobs.values() if j.get("state") == "running")
        if running >= 2:
            return 429, {"error": "two agent revisions are already running; wait for one to finish"}
        if len(server.agent_jobs) >= 16:
            oldest = min(server.agent_jobs, key=lambda k: server.agent_jobs[k].get("started", 0))
            del server.agent_jobs[oldest]
        server.agent_jobs[job_id] = job
    threading.Thread(target=_run_job, args=(server, job, argv, prompt), daemon=True).start()
    return 202, {"id": job_id, "state": "running", "agent": agent_id}


def job_status(server, job_id):
    """GET /agent/job?id=<id>: poll the state and result of an agent revision job."""
    if not isinstance(job_id, str) or not job_id:
        return 400, {"error": "missing job id"}
    with server.agent_lock:
        job = server.agent_jobs.get(job_id)
        if not job:
            return 404, {"error": "revision job not found"}
        return 200, dict(job)
