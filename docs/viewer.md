# The viewer

The viewer is a PDF.js page that latex-live serves for each preview. Open the
URL that the preview prints (see [setup.md](setup.md#3-first-run-from-a-terminal));
after the first visit, `http://localhost:44100/` opens the only preview or
lists them all. In a shared preview, coauthors see only what their role
allows: see [shared-mode.md](shared-mode.md#roles).

- [The toolbar](#the-toolbar)
- [Building and errors](#building-and-errors)
- [Source and PDF: SyncTeX](#source-and-pdf-synctex)
- [Notes](#notes)
- [The source pane](#the-source-pane)
- [Changes and comparing](#changes-and-comparing)
- [Downloads and the review copy](#downloads-and-the-review-copy)
- [The sidebar](#the-sidebar)
- [Page limit and space](#page-limit-and-space)
- [Pre-submission checks](#pre-submission-checks)
- [Keyboard shortcuts](#keyboard-shortcuts)

## The toolbar

From left to right:

| Control | What it does |
| --- | --- |
| ☰ | Opens the [sidebar](#the-sidebar). |
| Document menu | Switches to another root `.tex` file of the paper; every open viewer follows. It also lists the papers that other previews on the machine show, and opens their viewers. |
| ← → | Back and forward after following a link, a reference or a forward search. |
| Page, zoom, search | Page number, zoom (fit width, fit page, or a percentage; trackpad pinch zooms the PDF), and search. |
| Notes: off / on | Switches between the notes-free build and the build with the notes in the text ([Notes](#notes)). |
| 💬 | Shows or hides the notes as bubbles in the margin of the notes-free view. |
| 💬 Comment as… | Who you are, for writing notes. |
| ✂ | [Space hints](#page-limit-and-space). |
| Δ | [Change highlights](#changes-and-comparing), with the baseline ("vs …") and arrows to step through the changes. |
| Download | Its menu (on hover or click) saves the PDF three ways ([Downloads](#downloads-and-the-review-copy)). |
| ✎ Source | Opens the [source pane](#the-source-pane). |
| Name badge | In a shared preview, your name and role. |
| ⚠ TeX image missing | The TeX Live image is not pulled ([setup](setup.md#2-get-the-code-and-the-tex-image)). |
| ⟳ restart to update | latex-live changed on disk since this preview started; restart its task, then reload. |
| ⋯ | Dark mode (it inverts the PDF), Compare with an earlier version, Keyboard shortcuts, the Welcome guide, Sign out (the browser forgets its sign-in, for every preview at that address, until you open the link with `?t=` again), and the version, which links to the repository. On a narrow window, the toolbar stays one row: the controls that don't fit move into this menu, least used first. |
| ↻ | Rebuilds from scratch, e.g. after another build (`make`) replaced the PDF and SyncTeX no longer matches it. |
| Pages, status | The [page badge](#page-limit-and-space) and the build's status. |

Hovering over a reference shows a preview of its target (in a two-column paper,
the target's column; all of a `figure*`). Scroll position and zoom survive
rebuilds, reloads and browser restarts.

## Building and errors

latexmk rebuilds the paper whenever one of its files changes. The viewer
reloads in place: while a rebuild with the same pages is drawn, each page
keeps showing its previous version, so nothing blanks. The PDF is replaced
only after a successful run, so a failed build keeps the previous PDF with a
red banner; click an error to open its line. The same errors go to VS Code's Problems panel as `file:line`
diagnostics, through the task's problem matcher.

Beside the live build, a second build without the notes runs in the
background. It gives the notes-free view, and the page count without notes.
It starts beside each pass of the live build, so the notes-free view updates
about when the live build ends; when the live build ends with other
cross-references or citations than that pass started from, the build without
notes runs again after it, and the view updates a little later.

## Source and PDF: SyncTeX

- **PDF to source:** double-click the PDF. The source line opens in VS Code
  (`code -g`, or `LATEX_LIVE_EDITOR`), or in the source pane while it is open
  or when VS Code can't be reached.
- **Source to PDF:** the forward-search key in VS Code (setup step 5), `python3
  forward.py FILE LINE`, or Show in PDF in the source pane scrolls the viewer
  to the line and highlights it.

## Notes

Notes are the paper's note macros: one-argument commands that go through
`\todotxt`, such as `\newcommand{\ch}[1]{\todotxt{[CH: #1]}}`.

- **Notes-free view.** Notes: off (the default) shows the notes-free build, the
  paper as submitted, with each note in a margin bubble wired to a caret where
  it sits in the text. Hover a bubble to expand it; click it to open the source.
  In two columns, a bubble sits beside its column (left of the page for the left
  column). Notes: on shows the live build, notes in the text.
- **Writing a note.** Pick who you are in 💬 Comment as… (in a shared preview,
  you comment as yourself), select text in the PDF, and press 💬 or `c`. Your
  note goes into the source right after the selected words, and the rebuild
  shows it. Typed text is escaped, so `%`, `&` and `_` are safe; characters
  pdflatex can't set (✓, →, emoji) are refused.
- **Reply, resolve, edit, delete.** In the bubbles and the sidebar, notes get
  ✓ (resolve), others' notes ↩ (reply: your note goes right after theirs), and
  your own ✎ (edit). ✕ (delete) appears only on notes written from the same
  tab, as an undo. Resolving asks why, and comments the note out on its own
  lines after the line that held it (a comment line doesn't break the
  paragraph):

  ```
  % RESOLVED 2026-10-19 (EB): defined the control in App. D.2.
  % \ch{The control is never defined ...}
  ```

- **Where notes don't go.** A note never lands inside a heading, another note,
  or text marked as protected (`LATEX_LIVE_PROTECTED`,
  see [setup](setup.md#environment-variables)). Words of the selection that
  can't be found in the source (math, references) are dropped from its end.
- **Unsaved editors.** If the file is open with unsaved changes in your editor,
  save it first; otherwise, saving later offers to overwrite the note. The
  source pane merges the note in by itself.

## The source pane

✎ Source (or `e`) opens the LaTeX beside the PDF; drag its edge to resize it.

- The file menu lists the paper's `.tex`, `.bib`, `.sty` and `.cls` files by
  folder, with a filter.
- Edits save by themselves a moment after you stop typing (Cmd/Ctrl+S saves at
  once), and the PDF rebuilds.
- If the file changed meanwhile (saved in another editor or tab, or a note from
  the viewer), your edits are merged with that change. Where both changed the
  same lines, the pane shows both versions between `<<<<<<<` and `>>>>>>>`
  lines and saves again once you have settled them; or you keep your version or
  the other one whole.
- A file that changes on disk reloads in place. Edits that could not be saved
  yet stay in the browser and are offered back when you open the file again.

## Changes and comparing

- **Change highlights** (Δ or `d`; off by default): a word-level diff of the
  text against a baseline, plus outlines on changed figures. The "vs" button
  switches the baseline: the last push (the commit that the branch's
  remote-tracking branch is at, so no network; the default), the last commit
  (`HEAD`, rebuilt when it moves; the default when nothing was pushed), or the
  previous build. `,` and `.` (or the toolbar arrows) step through the changes.
- **Compare with an earlier version** (in the ⋯ menu): latexdiff of the working
  tree against any git commit, in a new tab.

## Downloads and the review copy

The download button's menu saves:

- **PDF with notes:** the PDF as built (`<doc>-with-notes.pdf` when the paper
  has notes).
- **PDF without notes (as submitted):** an exact build, as you would submit it,
  saved as `<doc>.pdf`: its own bibtex run (a citation only in a note drops
  out) and as many passes as the references need, made once per build of the
  document.
- **PDF with notes in the margin** (`<doc>-with-margin-notes.pdf`), a copy to
  read the notes away from the preview: the pages without notes, unchanged, on
  wider pages, with each note in a margin beside its column (left of the page
  for the left column of two). A note is typeset as the paper shows it (math,
  references, its author's label and color), numbered at its spot in the text
  and wired to it. In a crowded margin the notes keep their order and are set
  smaller; those that still don't fit continue on pages after the paper, under
  the same numbers. Resolved notes, protected markers, and notes the document
  leaves out (in a conditional that is off, say) are left out. A note that
  can't be pinned to its exact spot (one between paragraphs, say) is placed by
  its source line instead; the status line says how many were. A citation only
  in notes shows its key (with natbib, its authors and year). The pages' links
  don't work in this copy.

In a shared preview, every coauthor can download all three, viewers too.

## The sidebar

- **Outline:** numbered as in the paper (appendix letters too), with pages per
  section.
- **Pages:** thumbnails, with pages over the limit shaded.
- **Notes:** grouped by author.
- **Checks:** the [pre-submission checks](#pre-submission-checks).
- **Figures:** every figure and table, cropped from its page, with its caption;
  click one to go to it.

## Page limit and space

- **Page badge:** main-text pages against the venue limit, with and without
  notes. The main text ends at the references or at an earlier unnumbered
  statement (AI use, ethics, reproducibility, impact, acknowledgments). The
  limit comes from the venue style (ICLR, NeurIPS and COLM: 9 pages; ICML, CVPR
  and ICCV: 8) or `--page-limit`.
- **Space hints (✂):** marks short last lines of paragraphs and large blank
  gaps in the main text, with arrows to step through them.
- **Two columns:** LaTeX reports the page layout on each build. With two
  columns, the viewer reads the text column by column, ignores review line
  numbers beside either column, and measures page fill, space hints and note
  anchors per column.

## Pre-submission checks

The Checks tab of the sidebar lists, each as ok, info, warning or failure:

- **Anonymity:** author names (git `user.name` and the note-macro comments)
  and `LATEX_LIVE_ANON_TERMS` in the PDF; checked when page 1 says
  "Anonymous", as blind-review styles print it.
- **Draft notes and switches**, **undefined references and citations**,
  **placeholder text**.
- **Fonts** (unembedded and Type 3), **raster image resolution**, and **file
  size** (warning past 40 MB, failure past 50 MB).
- **arXiv source:** arXiv publishes the source, so this reads the paper root's
  arXiv package (the newest `.tar.gz`, `.tgz` or `.zip` file, or folder with a
  root `.tex`, with `arxiv` in its name) and lists what it would expose: `%`
  comments in `.tex` files, notes (hidden ones too), `\iffalse` blocks and
  `comment` environments, private terms (`LATEX_LIVE_PRIVATE_TERMS`), personal
  paths (also in figure PDFs), files the build doesn't read, files over 10 MB,
  leftover build files, and a missing `.bbl`. Private terms, personal paths and
  a missing `.bbl` fail the check; the rest warn. It says when the package is
  older than the sources. Without a package, it reads the files the build reads
  (what uploading the sources as they are would expose) and only informs
  (private terms and personal paths warn). A
  finding in a package file opens the project source when that file is the
  same, or when its line is found there.
- **Bibliography entries** (bibtex warnings) and **bibliography consistency**
  (duplicates, venue names).
- **Generated figures and tables** older than the script that makes them
  (`.py`, `.r`, `.jl`, `.m` or `.ipynb`, anywhere in the paper) or its data.
- **Figure and table placement:** floats off the text area, far from their
  first reference, or never referenced. References are the commands ending in
  `ref` (`\ref`, `\cref`, `\autoref`, macros like `\figref`) and
  `\hyperref[...]`. Pages that hold only floats don't count toward the distance,
  floats in the back matter count only references from there, and rotated
  (`sideways`) floats aren't checked against the text area.
- **Consistent terms**, **typos**, and **overfull lines**.

## Keyboard shortcuts

`?` in the viewer lists the ones your role can use.

| Key | Does |
| --- | --- |
| Cmd/Ctrl F | Search; Enter / Shift Enter for the next / previous match, Esc to clear |
| Ctrl + / Ctrl − | Zoom in / out |
| Ctrl 0 | Fit width |
| Alt ← / Alt → (or Cmd [ / Cmd ]) | Back / forward after a link |
| `d` | Change highlights on / off |
| `,` / `.` | Previous / next change |
| `c` | Comment on the selected text |
| `e` | The source pane (Cmd/Ctrl S there saves at once) |
| Double-click | Open the source line |
| `?` | The list of shortcuts |
