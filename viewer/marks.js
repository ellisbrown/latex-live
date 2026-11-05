// Marks over the pages: the changes, unresolved references, and places to cut that the modules finding them put in
// marks.

import { el } from "./page.js";
import { doc, eventBus, viewer } from "./pdfview.js";

// Persistent marks over pages: {page: [{rect (PDF coordinates), cls, title}]}. PDF.js empties a
// page's div when it re-renders it (zoom, reload), so they are redrawn on every "pagerendered".
export const marks = { changes: new Map(), unresolved: new Map(), cut: new Map() };
export let cutOn = !!localStorage.getItem("latex-live:cut"); // (the cut marks show: ✂ in the toolbar)
export function setCutOn(on) { cutOn = on; }
function drawMarks(pageNumber) {
  const pageView = viewer.getPageView(pageNumber - 1);
  if (!pageView?.div || !pageView.pdfPage) return;
  pageView.div.querySelector(".ll-marks")?.remove();
  const list = [...(marks.changes.get(pageNumber) || []), ...(marks.unresolved.get(pageNumber) || []),
                ...(cutOn ? marks.cut.get(pageNumber) || [] : [])];
  if (!list.length) return;
  const [vx, vy, vw, vh] = pageView.pdfPage.view;
  const W = vw - vx, H = vh - vy;
  const layer = el("div", { className: "ll-marks" });
  for (const m of list) {
    const [x0, y0, x1, y1] = m.rect;
    const div = el("div", { className: m.cls });
    if (m.label) div.dataset.label = m.label;
    Object.assign(div.style, { left: `${(x0 - vx) / W * 100}%`, top: `${(vh - y1) / H * 100}%`,
                               width: `${(x1 - x0) / W * 100}%`, height: `${(y1 - y0) / H * 100}%` });
    layer.append(div);
  }
  pageView.div.append(layer);
}
export const drawAllMarks = () => { for (let n = 1; n <= (doc?.numPages || 0); n++) drawMarks(n); };
eventBus.on("pagerendered", (e) => drawMarks(e.pageNumber));

// Unresolved references and citations render as "??": outline them in red.
export function showUnresolved(words) {
  marks.unresolved.clear();
  for (const w of words) {
    if (!w.s.includes("??")) continue;
    if (!marks.unresolved.has(w.page)) marks.unresolved.set(w.page, []);
    marks.unresolved.get(w.page).push({ rect: [w.rect[0] - 1, w.rect[1] - 1, w.rect[2] + 1, w.rect[3] + 1], cls: "unres" });
  }
}
