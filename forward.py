#!/usr/bin/env python3
"""latex-live forward search: show a source line in the browser viewer (SyncTeX).

Usage: forward.py FILE LINE   (run by the "LaTeX: show line in PDF" VS Code task)

Finds the paper root (nearest ancestor of FILE with build/*-live.synctex.gz),
maps FILE:LINE to boxes in the most recently built job that contains it, and
writes build/<job>.forward.json. The viewer picks that up on its next poll,
scrolls to the spot, and highlights it (in the notes-free PDF, the "clean" target).
"""

import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from live import forward_target  # noqa: E402


def main():
    src, line = os.path.realpath(sys.argv[1]), int(sys.argv[2])
    root = os.path.dirname(src)
    while not glob.glob(os.path.join(root, os.environ.get("LATEX_LIVE_BUILD", "build"), "*-live.synctex.gz")):
        if root == os.path.dirname(root):
            sys.exit(f"latex-live: no latex-live build above {src}")
        root = os.path.dirname(root)
    found = forward_target(root, src, line)
    if not found:
        sys.exit(f"latex-live: {os.path.relpath(src, root)}:{line} is not in any latex-live build")
    pdf, target = found
    clean = forward_target(root, src, line, clean=True)  # the same spot in the notes-free PDF, if built
    if clean:
        target["clean"] = clean[1]
    out = pdf[: -len(".pdf")] + ".forward.json"
    with open(out + ".tmp", "w") as f:
        json.dump({"seq": time.time(), **target}, f)
    os.replace(out + ".tmp", out)
    doc = os.path.basename(pdf)[: -len("-live.pdf")]
    print(f"latex-live: {os.path.relpath(src, root)}:{line} -> {doc}.pdf page {target['page']}")


if __name__ == "__main__":
    main()
