"""The smoke test for latex-live, which check.py runs (python3 check.py: about two minutes; needs TeX, as live.py does,
and podman with the TeX Live image for shared mode's part; python3 check.py units, say, runs one part).

- static and units check the checkout as it is and the server's parts without an instance (what GitHub runs).
- preview starts a live.py instance on a scratch paper behind the hub and checks it, beside threads with instances of
  their own: the warm standby, the early notes-free pass and its standby, turned pages, and shared mode, whose checks
  serve a clone of the paper to users of every role.

Each module says what it checks, and each ok line what passed; it exits non-zero on the first failure. CONTRIBUTING.md
maps the modules. From the bottom up (each imports only from those before it): base, helpers, papers, access, browser,
static, shared_units, perf_units, bib_units, files_units, history_units, agent_units, units, standby, early,
clean_standby, landscape, shared_helpers, shared_setup, shared_access, shared_sessions, shared_sandbox,
shared_downloads, shared_notes, shared_editor, shared_rebuild, shared_commits, shared_files, shared_browser, shared,
preview, main.
"""
