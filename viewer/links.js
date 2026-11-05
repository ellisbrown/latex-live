// Between the PDF and the sources (SyncTeX): from a spot in the PDF to its source line, and from a source line to the
// PDF; opening a source line, in VS Code or in the source pane.

import { $, file, isDiff, setStatus } from "./page.js";
import { postJSON, readBody } from "./net.js";
import { canOpenFile, editable, editorAllowed, me, opensInBrowser, opensSources } from "./role.js";
import { container, eventBus, pagesReady, shownVariant, viewer } from "./pdfview.js";
import { flash, reveal } from "./navigation.js";
import { cmView, ed, showEditor } from "./editor.js";

// SyncTeX inverse search: double-click a spot on a page to open its source line in VS Code, or in
// the source pane beside the PDF when that is open (a collaborator who may edit, or an owner whose
// preview has no VS Code to open: always there). For someone who can open neither, a double-click
// just selects a word, as anywhere else (e.g. to comment on it).
container.addEventListener("dblclick", async (e) => {
  const pageDiv = e.target.closest(".page");
  if (!pageDiv || e.target.closest(".ll-margin") || !opensSources()) return;
  const pageView = viewer.getPageView(+pageDiv.dataset.pageNumber - 1);
  const rect = pageDiv.getBoundingClientRect();
  const [x, y] = pageView.viewport.convertToPdfPoint(
    e.clientX - rect.left - pageDiv.clientLeft, e.clientY - rect.top - pageDiv.clientTop);
  window.getSelection().removeAllRanges(); // undo the word selection from the double-click
  flash(pageView.id, [x - 6, y - 6, x + 6, y + 6], "dot");
  if (isDiff) return setStatus("inverse search is off in the diff view", "error");
  const top = pageView.pdfPage.view[3] - y; // synctex measures y down from the page top
  const q = new URLSearchParams({ file, page: pageView.id, x: x.toFixed(1), y: top.toFixed(1), ...cleanParam() });
  try {
    let why = null; // (VS Code's failure to open, when the source pane opens instead)
    if (me.legacy || (!me.shared && me.can.openHost && !opensInBrowser())) { // (the server opens VS Code itself, as it always has)
      const r = await fetch(`synctex?${q}`);
      const text = await r.text();
      // 409: found, but VS Code did not open (e.g. not run in a VS Code terminal); the pane, then
      if (r.status !== 409 || !editorAllowed()) return setStatus(r.ok ? `→ ${text}` : text, r.ok ? "fresh" : "error");
      why = text;
    }
    q.set("json", "1"); // (just where it is; the viewer opens it)
    const r = await fetch(`synctex?${q}`);
    const found = await readBody(r);
    if (!r.ok) return setStatus(found.error || `no source line here (${r.status})`, "error");
    if (!found.file) return setStatus(found.reason || "no source file here", "error");
    if (why === null) {
      setStatus(`→ ${found.file}:${found.line}`, "fresh");
      return canOpenFile(found.file) && openSource(found.file, found.line);
    }
    if (!editable(found.file)) return setStatus(why, "error");
    setStatus(`${found.file}:${found.line} is in the source pane (${why})`);
    showEditor(true, { path: found.file, line: found.line });
  } catch (err) {
    setStatus(`${err.message || err}`, "error");
  }
});

// SyncTeX forward search (forward.py, or a note or check in the sidebar): scroll to the source
// line's boxes ({page, x, y, w, h}, points from the page's top-left) and highlight them; one that
// arrives while the document is loading, once its pages are ready.
let pendingForward = null;
eventBus.on("pagesinit", () => {
  if (pendingForward) showForward(pendingForward); // arrived while this document was loading
  pendingForward = null;
});
export function showForward(t) {
  if (!pagesReady) return (pendingForward = t);
  const pageView = viewer.getPageView(t.page - 1);
  if (!pageView) return;
  const H = pageView.pdfPage.view[3];
  reveal(t.page, [t.x - 2, H - t.y - t.h - 2, t.x + t.w + 2, H - t.y + 2]);
  setStatus(`← page ${t.page}`, "fresh");
}
// Show a source line in the PDF; sources with no PDF location (e.g. a .bib entry) open in the editor.
const cleanParam = () => (shownVariant === "clean" ? { clean: 1 } : {}); // SyncTeX of the notes-free build
export async function showSource(file, line, openIfMissing = false) {
  const r = await fetch(`forward?${new URLSearchParams({ file, line, ...cleanParam() })}`);
  const body = await r.json().catch(() => ({ error: `${file}:${line} is not in the PDF (${r.status})` }));
  if (r.ok) return showForward(body);
  if (openIfMissing && canOpenFile(file)) return openSource(file, line);
  setStatus(body.error, "error");
}

// Open a source line: in the source pane beside the PDF when that is open (for a collaborator who
// may edit, or the owner of a shared preview without VS Code: always), else in the host's editor
// (the owner's VS Code, live.py /open), or the pane when that does not open.
export async function openSource(file, line) {
  if (opensInBrowser() && editable(file)) return showEditor(true, { path: file, line });
  if (!me.can.openHost) return setStatus(`${file} can't be opened here`, "error");
  try {
    const r = me.legacy ? await fetch(`open?${new URLSearchParams({ file, line })}`) : await postJSON("open", { file, line });
    if (r.ok) return;
    const why = (await readBody(r)).error; // e.g. latex-live is not running in a VS Code terminal
    if (!editorAllowed() || !editable(file)) return setStatus(why, "error");
    setStatus(`${file}:${line} is in the source pane (VS Code did not open: ${why})`);
    showEditor(true, { path: file, line });
  } catch (err) {
    setStatus(`${err.message || err}`, "error");
  }
}

$("showInPdf").onclick = () => {
  if (ed && cmView) showSource(ed.path, cmView.state.doc.lineAt(cmView.state.selection.main.head).number);
};
