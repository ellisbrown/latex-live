#!/usr/bin/env python3
"""Smoke test for latex-live: python3 check.py (a minute or two; needs TeX, as live.py does).

This file only starts it: the checks are in checks/, next to it (its __init__ says what they check, and lists its
modules from the bottom up). Exits non-zero on the first failure.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))  # (checks/ next to this file, wherever it is run from)
from checks.main import main  # noqa: E402


if __name__ == "__main__":
    main()
