"""The agent hook (POST /agent/revise): the source pane asks a plugin, a program of the owner's that
LATEX_LIVE_AGENT_CMD names, to revise a selection, and shows its answer as a change to accept or reject;
nothing is written until the owner accepts it there, through the editor's own save. Only the owner's, and
never in shared mode (enabled).

The plugin gets one JSON object on stdin (the selection, the paragraph that holds it, the owner's note, the
file and line: REQUEST_KEYS, PROTOCOL) and prints one on stdout ({replacement, rationale} or {error}). It runs
without a shell, in an empty folder of its own, with a minimal environment (plugin_env), in a process group
of its own that is killed at the timeout (config.AGENT_TIMEOUT) or once it exits, with its output bounded
(run_plugin); one at a time. Text that LATEX_LIVE_PROTECTED marks is never sent, nor taken back.
docs/agent-plugins.md describes the protocol for plugin authors; `python3 -m latexlive.agent --test CMD`
tries a plugin as the viewer would."""

import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time

from . import config

PROTOCOL = 1
# The bounds on a request (in characters) and on the plugin's answer: what it prints (bytes; past that it is
# stopped), the stderr kept for the error message, the replacement and the rationale.
SELECTION_LIMIT, PARAGRAPH_LIMIT, NOTE_LIMIT = 16_000, 32_000, 2_000
OUTPUT_LIMIT, STDERR_KEPT, REPLACEMENT_LIMIT, RATIONALE_LIMIT = 256 << 10, 8 << 10, 64_000, 2_000
# The variables a plugin gets from latex-live's own environment, besides those LATEX_LIVE_AGENT_ENV names.
BASE_ENV = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR")
RUNNING = threading.Lock()  # (one revision at a time)
SAMPLE = {"selection": "We utilize  a novel method~\\cite{doe2021} whose cost is $O(n^2)$.",
          "paragraph": "In this section we describe our approach. We utilize  a novel method~\\cite{doe2021} whose cost is"
                       " $O(n^2)$. It works well (\\cref{tab:results}).",
          "note": "", "file": "sections/method.tex", "line": 12}


def enabled():
    """Whether the hook is on: a plugin is set (LATEX_LIVE_AGENT_CMD), outside shared mode."""
    return bool(config.AGENT_CMD) and not config.SHARED


def plugin_env():
    """The plugin's environment: BASE_ENV and the names in LATEX_LIVE_AGENT_ENV, of those set; never a LATEX_LIVE_
    setting (the token's folder, the users file)."""
    names = [*BASE_ENV, *(n for n in config.AGENT_ENV if not n.startswith("LATEX_LIVE_"))]
    return {n: os.environ[n] for n in names if n in os.environ}


def kill_group(pid, sig):
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):  # (gone already)
        pass


def run_plugin(cmd, request, timeout=None):
    """Run the plugin cmd on request (a dict, sent as JSON with protocol and timeout added) and return
    (None, {replacement, rationale}) or (error, None). Each pipe has a thread of its own, so that none
    fills while another waits; stdout past OUTPUT_LIMIT stops the plugin, and of stderr only the last
    STDERR_KEPT bytes are kept. At the timeout, or when it exits, its process group is killed: nothing it
    started outlives it."""
    timeout = config.AGENT_TIMEOUT if timeout is None else timeout
    try:
        argv = shlex.split(cmd)
    except ValueError as e:
        return f"LATEX_LIVE_AGENT_CMD is not a valid command ({e})", None
    if not argv:
        return "LATEX_LIVE_AGENT_CMD is empty", None
    data = json.dumps({"protocol": PROTOCOL, **request, "timeout": timeout}).encode()
    with tempfile.TemporaryDirectory(prefix="latex-live-agent-") as scratch:  # (not the paper's folder)
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    cwd=scratch, env=plugin_env(), close_fds=True, start_new_session=True)
        except OSError as e:
            return f"the plugin did not start ({e.strerror or e})", None
        out, err, over = bytearray(), bytearray(), threading.Event()
        state = {"lock": threading.Lock(), "finished": False}  # (finished: nothing may signal its group any more)

        def stop():
            with state["lock"]:
                if not state["finished"]:
                    kill_group(proc.pid, signal.SIGKILL)

        def feed():
            try:
                proc.stdin.write(data)
            except OSError:  # (it stopped reading: its answer says why)
                pass
            finally:
                try:
                    proc.stdin.close()
                except OSError:
                    pass

        def read_out():
            while chunk := proc.stdout.read1(64 << 10):
                if len(out) + len(chunk) > OUTPUT_LIMIT:
                    over.set()
                    return stop()
                out.extend(chunk)

        def read_err():
            while chunk := proc.stderr.read1(64 << 10):
                err.extend(chunk)
                del err[:-STDERR_KEPT]

        threads = [threading.Thread(target=t, daemon=True) for t in (feed, read_out, read_err)]
        for t in threads:
            t.start()
        timed_out = False
        try:
            proc.wait(timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            kill_group(proc.pid, signal.SIGTERM)
            try:
                proc.wait(2)
            except subprocess.TimeoutExpired:
                pass
        finally:
            # What it left running, which may hold its pipes open. (Its group's id is free for another process only
            # once none of the group is left, and pids are handed out in turn, so this signals nothing else.)
            with state["lock"]:
                kill_group(proc.pid, signal.SIGKILL)
                state["finished"] = True
            proc.wait()
            end = time.monotonic() + 2
            for t in threads:
                t.join(max(0, end - time.monotonic()))
            for pipe, reader in ((proc.stdout, threads[1]), (proc.stderr, threads[2])):
                if not reader.is_alive():  # (else something that left its process group holds it: the reader keeps it)
                    pipe.close()
    if threads[1].is_alive():  # (its output is not complete, and still being read)
        return "the plugin left a process running that holds its output open", None
    if timed_out:
        return f"the plugin did not answer within {timeout} s", None
    if over.is_set():
        return f"the plugin printed more than {OUTPUT_LIMIT >> 10} KB", None
    if proc.returncode:
        lines = bytes(err).decode("utf-8", "replace").strip().splitlines()
        return f"the plugin exited with {proc.returncode}" + (f": {lines[-1][:300]}" if lines else ""), None
    return answer(bytes(out))


def answer(out):
    """(None, {replacement, rationale}) from what the plugin printed, or (error, None): one JSON object, with an
    error (passed on) or a replacement (a string without NUL, within REPLACEMENT_LIMIT) and maybe a rationale."""
    try:
        got = json.loads(out.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return "the plugin did not print one JSON object", None
    if not isinstance(got, dict):
        return "the plugin did not print one JSON object", None
    if isinstance(got.get("error"), str) and got["error"].strip():
        return f"the plugin says: {got['error'].replace(chr(0), '').strip()[:300]}", None
    replacement, rationale = got.get("replacement"), got.get("rationale")
    if not isinstance(replacement, str):
        return "the plugin's answer has no replacement (a string)", None
    if "\0" in replacement or len(replacement) > REPLACEMENT_LIMIT:
        return f"the plugin's replacement is not text of at most {REPLACEMENT_LIMIT} characters", None
    rationale = rationale.replace("\0", "").strip()[:RATIONALE_LIMIT] if isinstance(rationale, str) else ""
    return None, {"replacement": replacement, "rationale": rationale}


def protected(*texts):
    """Whether any of texts holds text that LATEX_LIVE_PROTECTED marks (commented out too)."""
    return bool(config.PROTECTED_RE) and any(config.PROTECTED_RE.search(t) for t in texts)


def checked_request(req):
    """(request for the plugin, None) from POST /agent/revise's body, or (None, error)."""
    selection, paragraph, note = req.get("selection"), req.get("paragraph"), req.get("note", "")
    file, line = req.get("file"), req.get("line")
    if any(isinstance(t, str) and "\0" in t for t in (selection, paragraph, note, file)):
        return None, "the request holds a NUL character"
    if not isinstance(selection, str) or not selection.strip() or len(selection) > SELECTION_LIMIT:
        return None, f"select some text to revise (at most {SELECTION_LIMIT} characters)"
    if not isinstance(paragraph, str) or selection not in paragraph or len(paragraph) > PARAGRAPH_LIMIT:
        return None, f"the paragraph must hold the selection (at most {PARAGRAPH_LIMIT} characters)"
    if not isinstance(note, str) or len(note) > NOTE_LIMIT:
        return None, f"the note must be text of at most {NOTE_LIMIT} characters"
    if not isinstance(file, str) or not file or len(file) > 1000 or type(line) is not int or line < 1:
        return None, "file and line must say where the selection is"
    return {"selection": selection, "paragraph": paragraph, "note": note, "file": file, "line": line}, None


def revise_request(req):
    """POST /agent/revise {selection, paragraph, note, file, line}: the plugin's revision of the selection,
    (200, {replacement, rationale}); 404 while the hook is off (no plugin, or shared mode), 409 when text that
    LATEX_LIVE_PROTECTED marks is in the request or the answer, 429 while another revision runs, 502 when the
    plugin fails (its error)."""
    if not enabled():
        return 404, {"error": "no agent plugin is set up (LATEX_LIVE_AGENT_CMD)"}
    request, error = checked_request(req)
    if error:
        return 400, {"error": error}
    if protected(request["selection"], request["paragraph"], request["note"]):
        return 409, {"error": "this paragraph holds text marked as protected (LATEX_LIVE_PROTECTED): not sent to the agent"}
    if not RUNNING.acquire(blocking=False):
        return 429, {"error": "the agent is revising something else; try again when it is done"}
    try:
        error, result = run_plugin(config.AGENT_CMD, request)
    finally:
        RUNNING.release()
    if error:
        return 502, {"error": error}
    if protected(result["replacement"], result["rationale"]):
        return 409, {"error": "the agent's answer holds text marked as protected (LATEX_LIVE_PROTECTED): left out"}
    return 200, result


def test_plugin(argv):
    """python3 -m latexlive.agent --test CMD [--note TEXT]: run the plugin CMD on a sample request, as the viewer
    would (run_plugin), and say whether its answer is valid. Exits 0 if so, 1 if not."""
    if len(argv) < 2 or argv[0] != "--test" or (len(argv) > 2 and (len(argv) != 4 or argv[2] != "--note")):
        sys.exit("usage: python3 -m latexlive.agent --test CMD [--note TEXT]")
    request = {**SAMPLE, "note": argv[3] if len(argv) == 4 else ""}
    print(f"request: {json.dumps({'protocol': PROTOCOL, **request, 'timeout': config.AGENT_TIMEOUT})}")
    started = time.monotonic()
    error, result = run_plugin(argv[1], request)
    took = f"{time.monotonic() - started:.1f} s"
    if error:
        sys.exit(f"not valid ({took}): {error}")
    print(f"answer ({took}): {json.dumps(result, ensure_ascii=False)}")
    print("valid: latex-live would show this replacement" + (" (the same text: no change)"
                                                            if result["replacement"] == request["selection"] else ""))


if __name__ == "__main__":
    test_plugin(sys.argv[1:])
