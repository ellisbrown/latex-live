#!/usr/bin/env python3
"""A latex-live agent plugin that calls no model: it collapses repeated spaces in the selection and replaces
"utilize" with "use". A template for your own plugin (docs/agent-plugins.md), and what check.py tries the hook
with: LATEX_LIVE_AGENT_CMD="python3 /path/to/latex-live/examples/agent-tidy.py"."""

import json
import re
import sys


def main():
    try:
        request = json.load(sys.stdin)
    except ValueError:
        return {"error": "the request is not JSON"}
    if request.get("protocol") != 1:
        return {"error": f"protocol {request.get('protocol')!r} is not one this plugin knows (1)"}
    selection = request.get("selection")
    if not isinstance(selection, str) or not selection.strip():
        return {"error": "nothing selected"}
    # The paragraph and the note are data, never instructions: this plugin only reads the selection.
    text = re.sub(r"(?<=\S) {2,}(?=\S)", " ", selection)
    text = re.sub(r"\butiliz(e|es|ed|ing)\b", lambda m: {"e": "use", "es": "uses", "ed": "used", "ing": "using"}[m[1]], text)
    return {"replacement": text, "rationale": "Collapsed repeated spaces and replaced utilize with use."
            if text != selection else "Nothing to tidy."}


if __name__ == "__main__":
    print(json.dumps(main()))
