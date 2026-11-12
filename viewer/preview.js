// Hover previews of the PDF's internal links.

import { $, el } from "./page.js";
import { container, doc, linkService, pageFrame, pdfjsLib } from "./pdfview.js";
import { columnAt, columnBoxes, columnMid, shownArea, textLines } from "./pagetext.js";

// Hover previews: hovering an internal link (\ref, \cref, a citation) shows the part of the page
// it points to: a figure or table down to the end of its caption, a bibliography entry, the
// start of a section, or a few lines at an equation.
const preview = $("preview");
let hoverTimer = null, hideTimer = null, hoverLink = null, previewToken = 0;
container.addEventListener("mouseover", (e) => {
  const a = e.target.closest?.(".annotationLayer a[href^='#']");
  if (!a || a === hoverLink) return;
  hoverLink = a;
  clearTimeout(hoverTimer);
  clearTimeout(hideTimer);
  hoverTimer = setTimeout(() => showPreview(a).catch((err) => console.warn(err)), 250);
});
container.addEventListener("mouseout", (e) => {
  const a = e.target.closest?.(".annotationLayer a");
  if (!a || (e.relatedTarget && (a.contains(e.relatedTarget) || preview.contains(e.relatedTarget)))) return;
  hoverLink = null;
  clearTimeout(hoverTimer);
  hideTimer = setTimeout(hidePreview, 200);
});
preview.addEventListener("mouseenter", () => clearTimeout(hideTimer));
preview.addEventListener("mouseleave", () => { hideTimer = setTimeout(hidePreview, 200); });
container.addEventListener("scroll", () => hidePreview(), { passive: true });
export function hidePreview() {
  previewToken++;
  hoverLink = null;
  clearTimeout(hoverTimer);
  preview.hidden = true;
}
async function showPreview(a) {
  const token = ++previewToken;
  const hash = unescape(a.getAttribute("href").slice(1)); // PDFLinkService.getDestinationHash escapes
  let dest = null, name = null;
  if (hash.startsWith("[")) { try { dest = JSON.parse(hash); } catch { return; } }
  else { name = hash; dest = await doc.getDestination(hash); }
  if (!Array.isArray(dest)) return;
  const index = typeof dest[0] === "object" && dest[0] ? await doc.getPageIndex(dest[0]) : dest[0];
  if (!(index >= 0)) return;
  const page = await doc.getPage(index + 1);
  const frame = pageFrame(page), { W, H } = frame; // (the page as shown, as it renders)
  const kind = dest[1]?.name;
  const destY = kind === "XYZ" ? dest[3] : kind === "FitH" || kind === "FitBH" ? dest[2] : null;
  const destX = kind === "XYZ" ? dest[2] : null;
  const shown = shownArea(frame);
  let top = shown.top, x = null;
  if (destY != null && (destX != null || !frame.turned)) { // (a turned page's height is across: it takes both)
    const [X, Y] = frame.fromUser(destX ?? page.view[0], destY);
    [top, x] = [Y, destX == null ? null : X];
  }
  const [t0, t1, x0, x1] = await previewRegion(name?.split(".")[0] || "", index + 1, top, x, W, H);
  const span = columnMid(shown) === null ? x1 - x0 : Math.max(x1 - x0, shown.width + 16); // (a column at the scale of a full-width preview)
  const scale = Math.min(620 / span, (window.innerWidth - 40) / (x1 - x0), (window.innerHeight * 0.6) / (t1 - t0));
  const dpr = window.devicePixelRatio || 1;
  const viewport = page.getViewport({ scale: scale * dpr, offsetX: -x0 * scale * dpr, offsetY: -t0 * scale * dpr });
  const canvas = el("canvas", { width: Math.ceil((x1 - x0) * scale * dpr), height: Math.ceil((t1 - t0) * scale * dpr) });
  Object.assign(canvas.style, { width: `${(x1 - x0) * scale}px`, height: `${(t1 - t0) * scale}px` });
  await page.render({ canvasContext: canvas.getContext("2d"), viewport, annotationMode: pdfjsLib.AnnotationMode.DISABLE }).promise;
  if (token !== previewToken) return;
  preview.replaceChildren(canvas, el("div", { className: "hint", textContent: `page ${index + 1} · click to go there` }));
  preview.onclick = () => linkService.goToDestination(dest);
  preview.hidden = false;
  const r = a.getBoundingClientRect(), pw = preview.offsetWidth, ph = preview.offsetHeight;
  preview.style.left = `${Math.max(8, Math.min(window.innerWidth - pw - 8, r.left - 40))}px`;
  preview.style.top = `${r.bottom + 6 + ph < window.innerHeight ? r.bottom + 6 : Math.max(40, r.top - ph - 6)}px`;
}
// The region to show, [top, bottom, left, right] in points from the page's top-left. With two
// columns, a target in a column (by its destination's x) shows that column, measured by its lines
// alone; a full-width one (a figure* or table*, or a line across both columns) the whole width.
const CAPTION = /^(Figure|Fig\.|Table)\s*[A-Z]?\d+\s*[:.]/;
async function previewRegion(kind, pageNumber, top, x, W, H) {
  const { lines: found, area: shown } = await textLines(doc, pageNumber);
  const all = found.slice().sort((a, b) => a.top - b.top);
  const body = all.filter((l) => l.str.length > 20);
  let left = Math.max(0, Math.min(...body.map((l) => l.left), W * 0.2) - 8);
  let right = Math.min(W, Math.max(...body.map((l) => l.right), W * 0.8) + 8);
  let col = columnMid(shown) === null || x == null ? null : columnAt(x, shown); // null: the whole page, as with one column
  const across = (l) => l && (l.col === 0 || all.some((m) => m.col === 0 && Math.abs(m.top - l.top) < 2));
  const at = all.find((l) => l.top > top - 2); // the target's line
  if (col && at && at.top - top < 20 && at.col === 0) col = 0;
  if (col && (kind === "figure" || kind === "table")) {
    // A float anchors at its top left, so a figure* is in the left column by x: its caption (the
    // first below, in that column or across both) runs across both columns.
    if (across(all.find((l) => l.top > top - 2 && (l.col === col || l.col === 0) && CAPTION.test(l.str)))) col = 0;
  }
  if (col) [left, right] = [columnBoxes(shown)[col - 1][0] - 8, columnBoxes(shown)[col - 1][1] + 8];
  // (A full-width caption's short last line sits in the left column.)
  const lines = col === null ? all : col ? all.filter((l) => l.col === col) : all.filter((l) => l.col !== 2);
  const below = lines.filter((l) => l.top > top - 2);
  const paragraphEnd = (i, gap) => { // last line of the block starting at below[i]
    while (i + 1 < below.length && below[i + 1].top - below[i].top < gap) i++;
    return below[i].top;
  };
  let t0 = top - 4, t1 = top + 140;
  if (kind === "cite" && below.length) {
    // One entry: continuation lines are indented (hanging indent) until the next entry starts. (The
    // anchor sits about a line above the entry: level with the end of the one before, at tight spacing.)
    let i = Math.max(0, below.findIndex((l) => l.top > top + 2));
    const first = below[i];
    while (i + 1 < below.length && below[i + 1].left > first.left + 2 && below[i + 1].top - below[i].top < 20) i++;
    t0 = first.top - first.parts[0].h - 3;
    t1 = below[i].top + 4;
  } else if (kind === "figure" || kind === "table") {
    const i = below.findIndex((l) => CAPTION.test(l.str));
    t0 = top - 2;
    t1 = i >= 0 ? paragraphEnd(i, 16) + 4 : top + H * 0.45;
    if (kind === "table" && i >= 0 && below[i].top - top < 30) t1 = paragraphEnd(i, 19) + 4; // caption above the table
  } else if (kind === "equation" || kind === "AMS") {
    t0 = top - 6; t1 = top + 44;
  } else if (/section|paragraph|appendix|chapter|part/.test(kind)) {
    t0 = top - 2; t1 = top + 110;
  }
  const band = col ? all.find((l) => l.col === 0 && l.top > top + 2) : null; // (a column ends above a full-width float)
  if (band) t1 = Math.min(t1, band.top - band.parts[0].h - 2);
  t1 = Math.min(H, Math.max(t1, t0 + 20), t0 + H * 0.75);
  return [Math.max(0, t0), t1, left, right];
}
