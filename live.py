#!/usr/bin/env python3
"""latex-live: LaTeX Workshop-style live preview without the extension.

Usage (from a paper root):  live.py [<doc>[.tex] | PATH] [--port 44100] [--page-limit N] [--build-dir DIR] [--no-build] [--no-open] [--host-tex]

This file only starts latex-live: the code is in latexlive/, next to it (its __init__ says what
latex-live does). The VS Code tasks run this file by its path, also through a symbolic link to the
checkout.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))  # (latexlive/ next to this file, wherever it is run from)
from latexlive.main import main  # noqa: E402


if __name__ == "__main__":
    main()
