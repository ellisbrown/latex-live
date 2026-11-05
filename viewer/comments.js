// Writing notes from the PDF (live.py /note): a comment on selected text, and the tools on every note.

import { $, docFile, el, hubBase, isDiff, setStatus, sleep } from "./page.js";
import { readBody, signInPage, unreachable } from "./net.js";
import { me, notSetUp, ownerName, who, whoLabel } from "./role.js";
import { container, shownVariant, viewer } from "./pdfview.js";
import { wrote } from "./build.js";
import { revealControl } from "./toolbar.js";

// Comments: select text in the PDF, then 💬 (or press C), and a note of the author picked in the
// toolbar goes into the source right after the selected words (live.py /note); latexmk rebuilds
// and the note appears. In the margin and the sidebar, notes get ✓ (resolve: commented out under a
// "% RESOLVED <date> (<initials>): <reason>" line), others' notes ↩ (reply), your own ✎ (edit), and
// the ones written from this tab ✕ (delete, as an undo; this tab remembers them by file, macro, and text).
const written = new Set(JSON.parse(sessionStorage.getItem(`latex-live:${hubBase}written`) || "[]"));
const writtenKey = (file, macro, tex) => JSON.stringify([file, macro, tex]);
function markWritten(key, on) {
  on ? written.add(key) : written.delete(key);
  sessionStorage.setItem(`latex-live:${hubBase}written`, JSON.stringify([...written]));
}

// The selection's end, for a new note: {page, x, y (PDF points from the page's top-left), text, rect}.
let selectionTarget = null;
function selectionInfo() {
  const sel = window.getSelection();
  if (!sel.rangeCount || sel.isCollapsed) return null;
  const range = sel.getRangeAt(0);
  const inText = (node) => (node.nodeType === Node.TEXT_NODE ? node.parentElement : node)?.closest?.(".textLayer");
  const text = sel.toString().replace(/\s+/g, " ").trim();
  // Its text's rectangles, in reading order; not a box much taller than a line (a container the
  // selection runs into, such as PDF.js's end-of-content block).
  let rects = [...range.getClientRects()].filter((r) => r.width > 0 && r.height > 0);
  const lineHeight = rects.map((r) => r.height).sort((a, b) => a - b)[rects.length >> 1];
  rects = rects.filter((r) => r.height <= 3 * lineHeight);
  if (!text || !rects.length || !inText(range.startContainer) || !inText(range.endContainer)) return null;
  const rect = rects.at(-1), cx = rect.right - 1, cy = (rect.top + rect.bottom) / 2;
  const pageDiv = [...document.querySelectorAll("#viewer .page")].find((p) => {
    const b = p.getBoundingClientRect();
    return cx >= b.left && cx <= b.right && cy >= b.top && cy <= b.bottom;
  });
  const pageView = pageDiv && viewer.getPageView(+pageDiv.dataset.pageNumber - 1);
  if (!pageView?.pdfPage) return null;
  const box = pageDiv.getBoundingClientRect();
  const [x, y] = pageView.viewport.convertToPdfPoint(cx - box.left - pageDiv.clientLeft, cy - box.top - pageDiv.clientTop);
  return { page: pageView.id, x, y: pageView.pdfPage.view[3] - y, text, rect };
}
function showCommentButton() {
  selectionTarget = isDiff || $("whoPick").hidden ? null : selectionInfo();
  const b = $("commentBtn");
  b.hidden = !selectionTarget || !$("notePop").hidden;
  if (b.hidden) return;
  const r = selectionTarget.rect;
  b.textContent = who ? `💬 ${whoLabel()}` : "💬 Comment";
  b.title = who ? "Add your note after the selected text (C)" : "Pick who you are in the toolbar first";
  Object.assign(b.style, { left: `${Math.min(r.right + 4, innerWidth - b.offsetWidth - 8)}px`, top: `${r.bottom + 4}px` });
}
container.addEventListener("mouseup", (e) => { if (e.button === 0) setTimeout(showCommentButton, 250); }); // (after a double-click's own handling)
container.addEventListener("scroll", () => { if (!$("commentBtn").hidden) showCommentButton(); }, { passive: true });
document.addEventListener("selectionchange", () => { if (window.getSelection().isCollapsed) $("commentBtn").hidden = true; });
$("commentBtn").onmousedown = (e) => e.preventDefault(); // keep the selection
$("commentBtn").onclick = () => startComment();
export function startComment() {
  const t = selectionTarget;
  if (!t) return;
  if (!who) {
    setStatus("pick who you are in the toolbar (💬 Comment as…) first", "error");
    return revealControl($("whoPick")), $("whoPick").focus();
  }
  $("commentBtn").hidden = true;
  const quote = t.text.length > 50 ? `…${t.text.slice(-50)}` : t.text;
  openNotePop({ head: `${whoLabel()}, after “${quote}”`, initial: "", rect: t.rect,
                save: (text) => postNote({ action: "add", pdf: docFile, macro: who, page: t.page, x: t.x, y: t.y,
                                           clean: shownVariant === "clean", selection: t.text, text }) });
}

// The box to write a note in: save(text) returns an error message, or nothing once it is written.
let popSave = null;
function openNotePop({ head, initial, rect, save, latex = false, placeholder = "Your comment" }) {
  const pop = $("notePop"), field = pop.querySelector("textarea");
  pop.querySelector(".head").textContent = head;
  field.placeholder = placeholder;
  // (Plain-text notes are written on one line; only LaTeX keeps line breaks.)
  pop.querySelector(".hint").textContent = `Enter to save · ${latex ? "Shift+Enter for a new line · " : ""}Esc to cancel`;
  pop.querySelector(".error").textContent = "";
  pop.querySelector(".latex").hidden = !latex;
  pop.querySelector(".primary").disabled = false;
  field.value = initial;
  popSave = save;
  pop.hidden = false;
  const below = rect.bottom + 6 + pop.offsetHeight <= innerHeight;
  Object.assign(pop.style, { left: `${Math.max(8, Math.min(rect.left, innerWidth - pop.offsetWidth - 8))}px`,
                             top: `${below ? rect.bottom + 6 : Math.max(40, rect.top - pop.offsetHeight - 6)}px` });
  field.focus();
  field.setSelectionRange(field.value.length, field.value.length);
}
export function closeNotePop() { $("notePop").hidden = true; popSave = null; noteSeq++; }
async function submitNotePop() {
  const pop = $("notePop"), text = pop.querySelector("textarea").value, button = pop.querySelector(".primary");
  if (!text.trim() || !popSave || button.disabled) return;
  const save = popSave;
  button.disabled = true;
  const error = await save(text);
  if (popSave !== save) return; // (the box was closed meanwhile, or opened for another note)
  button.disabled = false;
  if (error) pop.querySelector(".error").textContent = error;
  else closeNotePop();
}
$("notePop").querySelector("textarea").addEventListener("keydown", (e) => {
  e.stopPropagation(); // (not the viewer's shortcuts)
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submitNotePop(); }
  else if (e.key === "Escape") { e.preventDefault(); closeNotePop(); }
});
$("notePop").querySelector(".primary").onclick = submitNotePop;
$("notePop").querySelector(".cancel").onclick = closeNotePop;

// Write a note change (live.py /note); returns an error message, or nothing. While the owner updates
// the shared copy (503 with retry), it waits and sends it again, for a minute at most, unless the
// note box is closed meanwhile.
let noteSeq = 0; // (bumped when the note box closes: a note still waiting to go is dropped then)
async function postNote(body) {
  const seq = noteSeq, kept = "(your text is still here)";
  try {
    let r, res;
    for (let attempt = 1; ; attempt++) {
      r = await fetch("note", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).catch(() => null);
      if (!r) return `Can't reach the preview server: try again in a moment ${kept}`;
      res = await readBody(r);
      if (!(r.status === 503 && res.retry) || attempt >= 30) break;
      if (!$("notePop").hidden) $("notePop").querySelector(".error").textContent = "Owner is updating the copy — retrying…";
      setStatus("Owner is updating the copy — retrying…", "busy");
      await sleep(2000);
      if (seq !== noteSeq) return "not sent: the note box was closed";
    }
    if (signInPage(r, res)) return `The preview answered with a web page: your sign-in may have expired. Copy your text, then reload the page ${kept}`;
    if (r.status === 503 && res.retry) return `${ownerName()} is still updating the shared copy: try again in a minute ${kept}`;
    if (unreachable(r, res)) return `Can't reach the preview server: try again in a moment ${kept}`;
    if (!r.ok || typeof res.source !== "string") return res.error || `failed (${r.status})`;
    wrote(); // (a build comes: behind)
    const old = writtenKey(body.source, body.macro, body.tex);
    if (body.action === "add" || body.action === "reply") markWritten(writtenKey(res.source, res.macro, res.tex), true);
    else if (written.has(old)) {
      markWritten(old, false);
      if (body.action === "edit") markWritten(writtenKey(res.source, body.macro, res.tex), true);
    }
    const verb = { add: "note added", reply: "reply added", edit: "note updated", resolve: "note resolved",
                   delete: "note deleted" }[body.action];
    const dropped = res.dropped ? `, before the last ${res.dropped} selected word${res.dropped === 1 ? "" : "s"} (not in the source)` : "";
    setStatus(`${verb} in ${res.source.replace(/^.*\//, "")}:${res.line}${res.protected ? ", after the protected text" : ""}`
              + `${dropped}; rebuilding…`, "fresh");
    if (body.action === "add") window.getSelection().removeAllRanges();
    return null;
  } catch (err) {
    return `${err.message || err}`;
  }
}

// ✓ on every note, ↩ on others', ✎ on your own, ✕ on the ones written from this tab (none on a
// marker of protected text).
export function noteTools(n) {
  if (isDiff || !who || n.protected || !me.can.comment || notSetUp()) return null;
  const target = { pdf: docFile, source: n.file, line: n.line, macro: n.macro, tex: n.tex };
  const where = `${n.file.replace(/^.*\//, "")}:${n.line}`, buttons = [];
  const button = (textContent, title, onclick) => {
    const b = el("button", { textContent, title });
    b.onclick = (e) => { e.stopPropagation(); onclick(e.target.getBoundingClientRect()); };
    buttons.push(b);
  };
  if (n.macro !== who)
    button("↩", "Reply to this note", (rect) => openNotePop({
      head: `${whoLabel()}, replying to ${n.author} (${where})`, initial: "", rect, placeholder: "Your reply",
      save: (text) => postNote({ action: "reply", ...target, who, text }) }));
  else if (n.plain != null || me.can.edit) // (a note with LaTeX in it is edited as LaTeX: editors only)
    button("✎", "Edit this note", (rect) => {
      const latex = n.plain == null; // (TeX beyond escaped characters: edited as LaTeX)
      openNotePop({ head: `Edit ${n.author} note (${where})`, initial: latex ? n.tex : n.plain, latex, rect,
                    save: (text) => postNote({ action: "edit", ...target, ...(latex ? { latex: text } : { text }) }) });
    });
  button("✓", "Resolve: comment it out under a RESOLVED line with your reason", (rect) => openNotePop({
    head: `Resolve ${n.author} note (${where}) as ${whoLabel()}: why?`, initial: "", rect,
    placeholder: "What addressed it (one line)", save: (text) => postNote({ action: "resolve", ...target, who, text }) }));
  if (written.has(writtenKey(n.file, n.macro, n.tex)))
    button("✕", "Delete this note (written from this tab)", async () => {
      if (!confirm(`Delete this ${n.author} note?\n\n${n.text}`)) return;
      const error = await postNote({ action: "delete", ...target });
      if (error) setStatus(error, "error");
    });
  return el("span", { className: "tools" }, ...buttons);
}
