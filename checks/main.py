"""The smoke test from start to end, which check.py runs; or only some of its parts, by module (python3 check.py
units, say, while you work on what those check)."""

import sys
import tempfile

from .base import ENV
from .clean_standby import clean_standby_run
from .early import early_run
from .helpers import editor_stub
from .preview import live_checks
from .standby import standby_run
from .static import static_checks
from .units import unit_checks


def scratch(run):
    """A part that needs a scratch folder: run(tmp), in one of its own."""
    def part():
        with tempfile.TemporaryDirectory(prefix="latex-live-check-") as tmp:
            run(tmp)
    return part


def alone(run):
    """One of the threads beside the instance checks, on its own: with the editor stand-in, as live_checks starts it."""
    def thread(tmp):
        ENV["LATEX_LIVE_EDITOR"] = editor_stub(tmp, "editor")[0]
        run(tmp)
    return scratch(thread)


# The parts, by module: a full run is static, units and preview (whose instance checks start the threads beside them:
# shared mode, the warm standby, the early notes-free pass and its warm standby). Those threads but shared mode, which
# reaches the owner's preview, also run alone.
PARTS = {"static": static_checks, "units": unit_checks, "preview": scratch(live_checks), "standby": alone(standby_run),
         "early": alone(early_run), "clean_standby": alone(clean_standby_run)}
FULL = ("static", "units", "preview")


def main():
    names = tuple(sys.argv[1:]) or FULL
    unknown = [name for name in names if name not in PARTS]
    if unknown:
        sys.exit(f"check: no part {', '.join(unknown)}: the parts are {', '.join(FULL)} (together, every check), and"
                 f" {', '.join(name for name in PARTS if name not in FULL)} (which preview runs too)")
    for name in names:
        PARTS[name]()
    print("all checks passed" if names == FULL else f"checks passed: {', '.join(names)}")
