"""The unit checks of the agent hook (latexlive.agent), without an instance, with stand-in plugins: a plugin's
answer is taken only as one JSON object with a replacement (or its error passed on), within the bounds; a plugin
that runs too long, or prints too much, is stopped with everything it started, and so is what one that answered
left running; a big request to a plugin that reads none, and a lot of stderr, stall nothing; the command is never
run through a shell, and a malformed one fails cleanly; the plugin gets a minimal environment (no LATEX_LIVE_
settings or other secrets) in an empty folder; the hook is off without a plugin and in shared mode, refuses text
marked LATEX_LIVE_PROTECTED both ways (without running the plugin for a request), and runs one revision at a time;
python3 -m latexlive.agent --test tries a plugin, the example's too."""

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import threading
import time

from .base import ENV, HERE, fail, ok, wait_for
from .helpers import agent_example, put

PLUGINS = {
    "echo": "import json, sys\nr = json.load(sys.stdin)\nprint(json.dumps({'replacement': r['selection'].upper(),"
            " 'rationale': ' Louder. '}))\n",
    "say": "import sys\nsys.stdin.read()\nsys.stdout.write(sys.argv[1])\n",
    "fails": "import sys\nsys.stdin.read()\nsys.stderr.write('a first line\\nboom\\n')\nsys.exit(3)\n",
    "sleeper": "import os, subprocess, sys, time\nchild = subprocess.Popen(['sleep', '60'])\n"
               "open(os.path.join(sys.argv[1], 'pids'), 'w').write(f'{os.getpid()} {child.pid}')\ntime.sleep(60)\n",
    "leaves": "import json, os, subprocess, sys\nsys.stdin.read()\nchild = subprocess.Popen(['sleep', '60'], stdout=sys.stdout)\n"
              "open(os.path.join(sys.argv[1], 'pids'), 'w').write(str(child.pid))\nprint(json.dumps({'replacement': 'x'}))\n",
    "floods": "import sys, time\nsys.stdout.write('x' * (1 << 20))\nsys.stdout.flush()\ntime.sleep(60)\n",
    "deaf": "import json\nprint(json.dumps({'replacement': 'deaf'}))\n",
    "noisy": "import json, sys\nsys.stdin.read()\nsys.stderr.write('e' * (1 << 20))\nprint(json.dumps({'replacement': 'ok'}))\n",
    "env": "import json, os, sys\nsys.stdin.read()\nprint(json.dumps({'replacement': json.dumps({'env': sorted(os.environ),"
           " 'cwd': os.getcwd(), 'files': os.listdir('.')})}))\n",
    "marks": "import json, os, sys\nsys.stdin.read()\nopen(os.path.join(sys.argv[1], 'ran'), 'w').close()\n"
             "print(json.dumps({'replacement': sys.argv[2] if len(sys.argv) > 2 else 'fine'}))\n",
    "slow": "import json, sys, time\nsys.stdin.read()\ntime.sleep(1.5)\nprint(json.dumps({'replacement': 'slow'}))\n",
}
REQUEST = {"selection": "two  words", "paragraph": "Before. two  words. After.", "note": "", "file": "main.tex", "line": 3}


def agent_checks():
    from latexlive import agent, config
    saved = {name: getattr(config, name) for name in ("AGENT_CMD", "AGENT_ENV", "SHARED", "PROTECTED_RE")}
    with tempfile.TemporaryDirectory(prefix="latex-live-agent-check-") as tmp:
        for name, code in PLUGINS.items():
            put(os.path.join(tmp, f"{name}.py"), code)
        cmd = lambda name, *args: " ".join(shlex.quote(a) for a in (sys.executable, os.path.join(tmp, f"{name}.py"), *args))
        try:
            config.SHARED, config.AGENT_ENV = False, []
            config.PROTECTED_RE = re.compile(r"\\eb\{human-written\}")
            answer_checks(agent, cmd)
            stop_checks(agent, cmd, tmp)
            env_checks(agent, config, cmd)
            request_checks(agent, config, cmd, tmp)
        finally:
            for name, value in saved.items():
                setattr(config, name, value)
    test_checks()


def answer_checks(agent, cmd):
    """What a plugin prints: one JSON object, with a replacement or an error."""
    def run(name, *args):
        return agent.run_plugin(cmd(name, *args), REQUEST, timeout=20)
    if run("echo") != (None, {"replacement": "TWO  WORDS", "rationale": "Louder."}):
        fail(f"a plugin's answer: {run('echo')}")
    bad = {"hello": "did not print one JSON object", '{"a": 1} {"b": 2}': "did not print one JSON object",
           "[1]": "did not print one JSON object", '{"replacement": 3}': "has no replacement",
           '{"replacement": "a\\u0000b"}': "is not text", json.dumps({"replacement": "x" * 64_001}): "is not text",
           '{"error": "model unavailable", "replacement": "x"}': "the plugin says: model unavailable"}
    for printed, want in bad.items():
        error, result = run("say", printed)
        if result is not None or want not in (error or ""):
            fail(f"a plugin that prints {printed[:40]!r}: {error!r} {result}, not an error with {want!r}")
    for printed, replacement in (('{"replacement": ""}', ""), ('{"replacement": "y", "rationale": 5}', "y")):
        if run("say", printed) != (None, {"replacement": replacement, "rationale": ""}):
            fail(f"a plugin that prints {printed}: {run('say', printed)}")
    error, _ = run("fails")
    if error != "the plugin exited with 3: boom":
        fail(f"a plugin that exits with 3: {error!r}, not its status and stderr's last line")
    for command, want in (("'unclosed", "not a valid command"), ("", "empty"), ("/nonexistent/plugin --x", "did not start"),
                          ("echo $HOME; touch pwned", "did not print one JSON object")):
        error, result = agent.run_plugin(command, REQUEST, timeout=20)
        if result is not None or want not in (error or ""):
            fail(f"LATEX_LIVE_AGENT_CMD={command!r}: {error!r} {result}, not an error with {want!r}")
    ok("an agent plugin's answer: one JSON object with a replacement (text, at most 64,000 characters, no NUL; empty"
       " too) or its error, passed on; else what was wrong, its exit status and stderr's last line; a malformed or missing"
       " command fails cleanly, and none runs through a shell")


def gone(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    try:  # (a zombie that its new parent has not reaped yet is gone too)
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] == "Z"
    except OSError:
        return False


def stop_checks(agent, cmd, tmp):
    """A plugin that runs past the timeout, or prints too much, is stopped with what it started; what an answered one
    left running too; big input and stderr stall nothing."""
    started = time.monotonic()
    error, _ = agent.run_plugin(cmd("sleeper", tmp), REQUEST, timeout=1)
    took = time.monotonic() - started
    pids = [int(p) for p in open(os.path.join(tmp, "pids")).read().split()]
    if error != "the plugin did not answer within 1 s" or took > 5:
        fail(f"a plugin past its timeout: {error!r} after {took:.1f} s")
    wait_for("the timed-out plugin and its child to be gone", lambda: all(gone(p) for p in pids), timeout=5)
    os.remove(os.path.join(tmp, "pids"))
    error, result = agent.run_plugin(cmd("leaves", tmp), REQUEST, timeout=20)
    child = int(open(os.path.join(tmp, "pids")).read())
    if error or result["replacement"] != "x":
        fail(f"a plugin that answers and leaves a child running (on its stdout): {error!r} {result}")
    wait_for("the child a plugin left running to be gone", lambda: gone(child), timeout=5)
    started = time.monotonic()
    error, _ = agent.run_plugin(cmd("floods"), REQUEST, timeout=20)
    if error != f"the plugin printed more than {agent.OUTPUT_LIMIT >> 10} KB" or time.monotonic() - started > 5:
        fail(f"a plugin that prints 1 MB: {error!r} after {time.monotonic() - started:.1f} s")
    big = {**REQUEST, "selection": "é" * 16_000, "paragraph": "ü" * 32_000}  # (more than a pipe holds)
    for name, want in (("deaf", "deaf"), ("noisy", "ok")):
        started = time.monotonic()
        error, result = agent.run_plugin(cmd(name), big, timeout=20)
        if error or result["replacement"] != want or time.monotonic() - started > 5:
            fail(f"the {name} plugin on a big request: {error!r} {result} after {time.monotonic() - started:.1f} s")
    ok("an agent plugin past its timeout is stopped with the processes it started, and so is what an answered one left"
       " running; one that prints too much is stopped at once; a big request to a plugin that reads none, and 1 MB of"
       " stderr, stall nothing")


def env_checks(agent, config, cmd):
    """The plugin's environment: the minimal one and LATEX_LIVE_AGENT_ENV's names, never a LATEX_LIVE_ setting; its
    folder: an empty one of its own."""
    planted = {"SECRET_TOKEN": "s3cret", "LATEX_LIVE_STATE": "/elsewhere", "LATEX_LIVE_USERS": "/elsewhere/users.json",
               "AGENT_CHECK_EXTRA": "yes"}
    before = {name: os.environ.get(name) for name in planted}
    os.environ.update(planted)
    config.AGENT_ENV = ["AGENT_CHECK_EXTRA", "LATEX_LIVE_USERS"]
    try:
        error, result = agent.run_plugin(cmd("env"), REQUEST, timeout=20)
    finally:
        config.AGENT_ENV = []
        for name, value in before.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    seen = json.loads(result["replacement"]) if result else {}
    extra = set(seen.get("env", [])) - set(agent.BASE_ENV) - {"AGENT_CHECK_EXTRA"}
    extra -= {"LC_CTYPE", "__CF_USER_TEXT_ENCODING"}  # (that Python or macOS add in the child itself)
    if error or extra or "AGENT_CHECK_EXTRA" not in seen["env"] or "PATH" not in seen["env"]:
        fail(f"an agent plugin's environment: {error!r} {seen.get('env')} (more than the minimal one: {sorted(extra)})")
    if seen["files"] or os.path.realpath(seen["cwd"]) == os.path.realpath(os.getcwd()) or "latex-live-agent-" not in seen["cwd"]:
        fail(f"an agent plugin's folder: {seen['cwd']} holding {seen['files']}, not an empty one of its own")
    ok("an agent plugin gets PATH, HOME and the locale, and the variables LATEX_LIVE_AGENT_ENV names, never another"
       " secret or a LATEX_LIVE_ setting, and runs in an empty folder of its own")


def request_checks(agent, config, cmd, tmp):
    """POST /agent/revise: off without a plugin and in shared mode; the request's checks; protected text both ways; one
    revision at a time."""
    ran = os.path.join(tmp, "ran")
    config.AGENT_CMD = ""
    if agent.revise_request(REQUEST)[0] != 404 or agent.enabled():
        fail("POST /agent/revise without LATEX_LIVE_AGENT_CMD is not 404")
    config.AGENT_CMD, config.SHARED = cmd("marks", tmp), True
    if agent.revise_request(REQUEST)[0] != 404 or agent.enabled() or os.path.exists(ran):
        fail("POST /agent/revise in shared mode, with LATEX_LIVE_AGENT_CMD set, is not 404 (or ran the plugin)")
    config.SHARED = False
    marked = "Kept as written.\\eb{human-written}"
    bad = [{**REQUEST, "line": True}, {**REQUEST, "line": 0}, {**REQUEST, "file": ""}, {**REQUEST, "selection": "  "},
           {**REQUEST, "paragraph": "elsewhere"}, {**REQUEST, "note": "a\0b"}, {**REQUEST, "note": "n" * 2001},
           {**REQUEST, "selection": "s" * 16_001, "paragraph": "s" * 16_001}, {k: v for k, v in REQUEST.items() if k != "file"}]
    for req in bad:
        if agent.revise_request(req)[0] != 400:
            fail(f"POST /agent/revise takes {str(req)[:120]}")
    for req in ({**REQUEST, "paragraph": f"{marked}\ntwo  words"}, {**REQUEST, "note": "write \\eb{human-written}"},
                {**REQUEST, "selection": marked, "paragraph": marked}):
        code, body = agent.revise_request(req)
        if code != 409 or "protected" not in body.get("error", "") or os.path.exists(ran):
            fail(f"POST /agent/revise with protected text in {str(req)[:100]}: {code} {body} (the plugin ran: {os.path.exists(ran)})")
    config.AGENT_CMD = cmd("marks", tmp, "two \\eb{human-written}")
    code, body = agent.revise_request(REQUEST)
    if code != 409 or "answer holds" not in body.get("error", ""):
        fail(f"POST /agent/revise whose plugin answers protected text: {code} {body}")
    for replacement in ("two  words", ""):
        config.AGENT_CMD = cmd("marks", tmp, replacement)
        if agent.revise_request(REQUEST) != (200, {"replacement": replacement, "rationale": ""}):
            fail(f"POST /agent/revise whose plugin answers {replacement!r}: {agent.revise_request(REQUEST)}")
    config.AGENT_CMD = cmd("fails")
    if agent.revise_request(REQUEST) != (502, {"error": "the plugin exited with 3: boom"}):
        fail(f"POST /agent/revise whose plugin fails: {agent.revise_request(REQUEST)}")
    config.AGENT_CMD = cmd("slow")
    first = []
    thread = threading.Thread(target=lambda: first.append(agent.revise_request(REQUEST)))
    thread.start()
    wait_for("the first revision to start", lambda: agent.RUNNING.locked(), timeout=5, step=0.01)
    second = agent.revise_request(REQUEST)
    thread.join()
    if second[0] != 429 or first != [(200, {"replacement": "slow", "rationale": ""})] or agent.revise_request(REQUEST)[0] != 200:
        fail(f"two revisions at once: {first} and {second}, not the first and 429 (and then the next)")
    ok("POST /agent/revise is off (404) without LATEX_LIVE_AGENT_CMD and in shared mode; checks its request; refuses text"
       " marked LATEX_LIVE_PROTECTED in the selection, the paragraph or the note (without running the plugin) and in the"
       " answer; passes a failure on (502); runs one revision at a time (429)")


def test_checks():
    """python3 -m latexlive.agent --test CMD: the example plugin passes, a broken one doesn't."""
    tidy = agent_example()
    run = lambda command: subprocess.run([sys.executable, "-m", "latexlive.agent", "--test", command], cwd=HERE, env=ENV,
                                         capture_output=True, text=True, timeout=60)
    good, broken = run(tidy), run("/nonexistent/plugin")
    if good.returncode or "valid" not in good.stdout or "We use a novel method" not in good.stdout:
        fail(f"python3 -m latexlive.agent --test on the example plugin:\n{good.stdout}{good.stderr}")
    if broken.returncode != 1 or "not valid" not in broken.stderr:
        fail(f"python3 -m latexlive.agent --test on a missing plugin: {broken.returncode}\n{broken.stdout}{broken.stderr}")
    ok("python3 -m latexlive.agent --test passes the example plugin (examples/agent-tidy.py) and fails a missing one")
