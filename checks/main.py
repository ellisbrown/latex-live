"""The smoke test from start to end, which check.py runs."""

import tempfile

from .preview import live_checks
from .static import static_checks
from .units import unit_checks


def main():
    static_checks()
    unit_checks()
    with tempfile.TemporaryDirectory(prefix="latex-live-check-") as tmp:
        live_checks(tmp)
    print("all checks passed")
