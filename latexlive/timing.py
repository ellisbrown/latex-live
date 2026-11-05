"""LATEX_LIVE_TIMING=1: one line per build saying when each of its phases happened, counted from the save that
started it, so that time spent between a save and the page in the viewer can be measured rather than guessed.

The processes of a build note their phases in build/<job>.timing, a line each, "<time> <event> [<detail>]":
latexmkrc (from latexmk's hooks: a run begins, a pass begins, its command returns, latexmk has read what it wrote,
the run ends; and bibtex), standby.py (how a pass ran, and when a standby was released, its TeX run ended and its
output was moved), and latex_report.py (each pass's publish, the end-of-run report, the newest source's time, and the
notes-free pass). In shared mode, shared_build notes them in memory instead (note), as it reads nothing back from
build/. A thread (watch_timing) prints a run's line once the run and the notes-free pass it started have ended. The
viewer logs its own part (noticing, fetching, parsing and drawing each new PDF) in the browser's console."""

import os
import threading
import time

from . import config

# The events that end a run's line: the notes-free pass's end when the run started one, else the run's end.
CLEAN_END = ("clean-published", "clean-failed")


def note(server, event, detail="", when=None):
    """Note an event of shared mode's build (its builder runs in this process), now or at time when."""
    if config.TIMING:
        with server.timing_lock:
            server.timing_events.append((time.time() if when is None else when, event, detail))


def parse(text):
    """The events of a .timing file's lines: (time, event, detail)."""
    events = []
    for line in text.splitlines():
        when, _, rest = line.partition(" ")
        event, _, detail = rest.partition(" ")
        try:
            events.append((float(when), event, detail))
        except ValueError:
            pass
    return events


def line(doc, events):
    """A run's line: each event in time order, in ms from the save (the newest source the run read), or from the
    run's start when it noted no save (a rebuild, say)."""
    saved = [e for e in events if e[1] == "saved"]
    start = saved[-1][0] if saved else events[0][0]
    since = f"the save of {saved[-1][2]}" if saved else "the run began"
    shown = sorted((e for e in events if e[1] != "saved"), key=lambda e: e[0])
    parts = [f"{event}{'(' + detail + ')' if detail and event not in ('published', 'clean-published') else ''}"
             f" {round((when - start) * 1000)}" for when, event, detail in shown]
    return f"=== latex-live timing: {doc} (ms after {since}): {', '.join(parts)}"


class Runs:
    """Gathers events into runs, and gives each run's line once it has ended."""

    def __init__(self):
        self.events = []

    def feed(self, events):
        lines = []
        for e in events:
            if e[1] == "run" and any(n == "run" for _, n, _ in self.events):  # (a run began before the last one's line: print what it had)
                lines.append(self.events)
                self.events = []
            self.events.append(e)
            names = {n for _, n, _ in self.events}
            cleaned = "clean-start" in names or "clean-kept" in names  # (an early pass kept may publish before the run's end)
            if "run-end" in names and (not cleaned or names & set(CLEAN_END)):
                lines.append(self.events)
                self.events = []
        return lines


def watch_timing(server):
    """Print each run's timing line (a thread, with LATEX_LIVE_TIMING=1): from build/<job>.timing, or in shared
    mode from what shared_build noted."""
    runs, offset, job_path = Runs(), 0, None
    while not server.stopping:
        time.sleep(0.2)
        doc = server.default_pdf[:-4]
        if config.SHARED:
            with server.timing_lock:
                events, server.timing_events = server.timing_events, []
        else:
            path = os.path.join(server.paper_root, config.BUILD, f"{doc}-live.timing")
            if path != job_path:  # (another document now: start on its file)
                job_path, offset, runs = path, 0, Runs()
            try:
                with open(path, "rb") as f:
                    if os.fstat(f.fileno()).st_size < offset:
                        offset = 0
                    f.seek(offset)
                    data = f.read(1 << 20)
            except OSError:
                continue
            end = data.rfind(b"\n") + 1  # (a line still being written waits for the next look)
            offset += end
            events = parse(data[:end].decode(errors="replace"))
        for run in runs.feed(events):
            print(line(doc, run), flush=True)


def start_timing(server):
    """With LATEX_LIVE_TIMING=1, print a line per build (watch_timing), starting from an empty file."""
    server.timing_events, server.timing_lock = [], threading.Lock()
    if not config.TIMING:
        return
    if not config.SHARED:
        try:
            os.remove(os.path.join(server.paper_root, config.BUILD, f"{server.default_pdf[:-4]}-live.timing"))
        except OSError:
            pass
    threading.Thread(target=watch_timing, args=(server,), daemon=True).start()
