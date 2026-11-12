// Between the PDF and the sources (SyncTeX): from a spot in the PDF to its source line, and from a source line to the
// PDF; opening a source line, in VS Code or in the source pane.

import { $, file, isDiff, setStatus } from "./page.js";
import { postJSON, readBody } from "./net.js";
import { canOpenFile, editable, editorAllowed, me, opensInBrowser, opensSources } from "./role.js";
import { container, eventBus, pageFrame, pagesReady, shownVariant, viewer } from "./pdfview.js";
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
  const [sx, sy] = pageFrame(pageView.pdfPage).fromUser(x, y);
  flash(pageView.id, [sx - 6, sy - 6, sx + 6, sy + 6], "dot");
  if (isDiff) return setStatus("inverse search is off in the diff view", "error");
  const top = pageView.pdfPage.view[3] - y; // synctex measures y down from the page top (as stored, before its /Rotate)
  const q = new URLSearchParams({ file, page: pageView.id, x: x.toFixed(1), y: top.toFixed(1), ...cleanParam(),
                                  ...clickedWord(pageDiv, e.clientX, e.clientY) });
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
      return canOpenFile(found.file) && openSource(found.file, found.line, found.column);
    }
    if (!editable(found.file)) return setStatus(why, "error");
    setStatus(`${found.file}:${found.line} is in the source pane (${why})`);
    showEditor(true, { path: found.file, line: found.line, column: found.column });
  } catch (err) {
    setStatus(`${err.message || err}`, "error");
  }
});

// The double-clicked word, for inverse search to land on it rather than at the start of its line (live.py looks for it
// in the line SyncTeX names): as letters and digits, lowercase and without accents, with the words beside it on its
// line, and which of the line's same words it is ({word, before, after, occurrence, count}); {} off the text. The line
// is put together from the text layer's pieces level with the clicked one, in its column.
const WORD = /[\p{L}\p{N}\p{M}]+/gu;
const wordKey = (s) => s.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase();
function clickedWord(pageDiv, x, y) {
  const inside = (b) => x >= b.left && x <= b.right && y >= b.top && y <= b.bottom;
  const spans = [...pageDiv.querySelectorAll(".textLayer span")].filter((s) => s.firstChild instanceof Text)
    .map((s) => ({ s, box: s.getBoundingClientRect() })).filter(({ box }) => box.width > 0);
  const hit = spans.find(({ box }) => inside(box));
  if (!hit) return {};
  const node = hit.s.firstChild, range = document.createRange();
  let offset = -1; // (the clicked character, measured glyph by glyph)
  for (let i = 0; i < node.length && offset < 0; i++) {
    range.setStart(node, i);
    range.setEnd(node, i + 1);
    const b = range.getBoundingClientRect();
    if (b.width > 0 && x >= b.left && x <= b.right) offset = i;
  }
  if (offset < 0) return {};
  const height = hit.box.height, middle = (b) => (b.top + b.bottom) / 2;
  const level = spans.filter(({ box }) => middle(box) >= hit.box.top && middle(box) <= hit.box.bottom)
    .sort((a, b) => a.box.left - b.box.left);
  const parts = [[]]; // (split where a gap wider than the line is high leaves one column for the next)
  level.forEach((it, i) => {
    if (i && it.box.left - level[i - 1].box.right > height) parts.push([]);
    parts.at(-1).push(it);
  });
  let text = "", start = 0, previous = null;
  for (const { s, box } of parts.find((part) => part.includes(hit))) {
    if (previous && box.left - previous.right > 0.15 * height && !/\s$/.test(text)) text += " ";
    if (s === hit.s) start = text.length;
    text += s.textContent;
    previous = box;
  }
  const words = [...text.matchAll(WORD)].map((m) => ({ key: wordKey(m[0]), from: m.index, to: m.index + m[0].length }))
    .filter((w) => w.key);
  const i = words.findIndex((w) => start + offset >= w.from && start + offset < w.to);
  if (i < 0) return {};
  const same = words.filter((w) => w.key === words[i].key);
  return { word: words[i].key, before: words[i - 1]?.key || "", after: words[i + 1]?.key || "",
           occurrence: same.indexOf(words[i]), count: same.length };
}

// SyncTeX forward search (forward.py, or a note or check in the sidebar): scroll to the source
// line's boxes ({page, x, y, w, h}, points from the page's top-left as stored: pageFrame) and highlight
// them; one that arrives while the document is loading, once its pages are ready.
let pendingForward = null;
eventBus.on("pagesinit", () => {
  if (pendingForward) showForward(pendingForward); // arrived while this document was loading
  pendingForward = null;
});
export function showForward(t) {
  if (!pagesReady) return (pendingForward = t);
  const pageView = viewer.getPageView(t.page - 1);
  if (!pageView?.pdfPage) return;
  const [x0, y0, x1, y1] = pageFrame(pageView.pdfPage).rect([t.x, t.y, t.x + t.w, t.y + t.h]);
  reveal(t.page, [x0 - 2, y0 - 2, x1 + 2, y1 + 2]);
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
export async function openSource(file, line, column = 0) {
  if (opensInBrowser() && editable(file)) return showEditor(true, { path: file, line, column });
  if (!me.can.openHost) return setStatus(`${file} can't be opened here`, "error");
  try {
    const r = me.legacy ? await fetch(`open?${new URLSearchParams({ file, line })}`)
                        : await postJSON("open", { file, line, ...(column ? { column } : {}) });
    if (r.ok) return;
    const why = (await readBody(r)).error; // e.g. latex-live is not running in a VS Code terminal
    if (!editorAllowed() || !editable(file)) return setStatus(why, "error");
    setStatus(`${file}:${line} is in the source pane (VS Code did not open: ${why})`);
    showEditor(true, { path: file, line, column });
  } catch (err) {
    setStatus(`${err.message || err}`, "error");
  }
}

$("showInPdf").onclick = () => {
  if (ed && cmView) showSource(ed.path, cmView.state.doc.lineAt(cmView.state.selection.main.head).number);
};
