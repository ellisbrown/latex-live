"""latex-live: LaTeX Workshop-style live preview without the extension.

live.py runs main from a paper's root folder: `latexmk -pvc` rebuilds the document into build/ whenever one of its
files changes, latex_report.py publishes <doc>.pdf after each successful pass, and the server sends the PDF.js viewer
(viewer.html, viewer/) and the JSON it asks for, behind the hub's one port for every preview on the machine. With
LATEX_LIVE_USERS, an instance runs in shared mode instead: coauthors open it in their roles, and it builds without
latexmk, each TeX run in a confined container. README.md and docs/ say what users see; CONTRIBUTING.md maps the code.

Its modules, from the bottom up (each imports only from those before it; CONTRIBUTING.md says what each is for):
config, standby, auth, roles, files, tools, git, latex, synctex, document, notes, sources, commits, note_writer, editor,
file_actions, bib, agent, navigation, instances, build, baselines, status, logs, timing, shared_watch, shared_build,
export, review_anchors, review_layout, review, lints, arxiv, checks, web, hub, handler, main.
"""

DESCRIPTION = __doc__.splitlines()[0]  # (live.py --help)
