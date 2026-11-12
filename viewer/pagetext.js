// The PDF's text, as the lines of each page's text area (from the build's log: area), for what measures or finds
// things in the pages: the page count, headings, margin notes, places to cut, and previews. Positions are on the page
// as shown (pageFrame), so that a turned page's lines read across, as on screen.

import { pageFrame } from "./pdfview.js";

// Text as lowercase letters and digits only, to find a title or a note's words in the PDF's text.
export const norm = (s) => s.toLowerCase().replace(/[^a-z0-9]/g, "");

// Page text, cached per document: {W, H, area (shownArea), turned, items {str, x, top (baseline, points from the page
// top), w, h}} without the submission line numbers (in the left margin, or beside both columns).
const textCache = new WeakMap();
export function pageText(d, n) {
  if (!textCache.has(d)) textCache.set(d, new Map());
  const cache = textCache.get(d);
  if (!cache.has(n)) cache.set(n, (async () => {
    const page = await d.getPage(n);
    const frame = pageFrame(page), { W, H } = frame, shown = shownArea(frame);
    const { items } = await page.getTextContent();
    return { W, H, area: shown, turned: frame.turned, items: items.filter((i) => i.str.trim())
      .map((i) => {
        const [x, top] = frame.fromUser(i.transform[4], i.transform[5]);
        return { str: i.str, x, top, w: i.width, h: i.height || Math.hypot(i.transform[2], i.transform[3]) };
      })
      .filter((i) => !lineNumber(i, W, shown)) };
  })());
  return cache.get(n);
}

// The page's text area, in points from the top, and its line spacing: from the build's log (live.py).
// Until a build reports it, 1in margins on US letter with 11pt lines.
export let area = { top: 72, bottom: 720, leading: 11, H: 792 };
export function setArea(layout) { area = layout; }
// The text area of a page as shown (frame: pageFrame): the build's, or on a turned page the build's turned with it, as
// one column (a landscape page's body).
export function shownArea(frame) {
  if (!area.width) return area;
  const [left, top, right, bottom] = frame.rect([area.left, area.top, area.left + area.width, area.bottom]);
  if (!frame.turned) return left === area.left ? area : { ...area, left }; // (a page whose box starts right of 0)
  return { ...area, left, top, bottom, width: right - left, colwidth: right - left, columns: 1, H: frame.H };
}
// The text columns of a text area (the build's, or a page's: shownArea), [left, right] in points (one, or two with
// columns = 2); null until a build reports them.
export function columnBoxes(a = area) {
  if (!a.width) return null;
  const { left, width, colwidth } = a;
  return a.columns === 2 ? [[left, left + colwidth], [left + width - colwidth, left + width]] : [[left, left + width]];
}
export const columnMid = (a = area) => (a.columns === 2 ? a.left + a.width / 2 : null);
export const columnAt = (x, a = area) => (columnMid(a) === null || x < columnMid(a) ? 1 : 2);
// A submission line number: a bare number outside the text columns (before a build reports them,
// in the left margin, as in ICLR's template).
function lineNumber(i, W, a) {
  if (!/^\d{1,4}$/.test(i.str.trim())) return false;
  const boxes = columnBoxes(a);
  return boxes ? !boxes.some(([l, r]) => i.x >= l - 2 && i.x + i.w <= r + 2) : i.x < W * 0.15;
}
// How far into a page's text a point is, from 0 to 1: by height, and with two columns, the left one first.
export function areaFraction(top, x, a = area) {
  const f = Math.min(1, Math.max(0, (top - a.top) / (a.bottom - a.top)));
  return columnMid(a) === null || x == null ? f : columnAt(x, a) === 1 ? f / 2 : 0.5 + f / 2;
}

// Text lines of a page (pieces joined by baseline), without the running header and footer, in
// reading order, with the page's text area (shownArea). Each line's col is 1 (or 2: the right column,
// with two columns), or 0 for a line across both columns (a title, a figure* caption); lines go column
// by column between those.
export async function textLines(d, pageNumber) {
  const { W, H, items, area: shown, turned } = await pageText(d, pageNumber);
  const mid = columnMid(shown);
  const colOf = (i) => (mid === null ? 1 : i.x < mid - 2 && i.x + i.w > mid + 2 ? 0 : i.x + i.w / 2 < mid ? 1 : 2);
  const lines = [];
  for (const col of mid === null ? [1] : [0, 1, 2]) {
    let last = null;
    for (const i of items.filter((i) => colOf(i) === col).sort((a, b) => a.top - b.top)) {
      if (last && i.top - last.top < 2) last.parts.push(i);
      else lines.push((last = { top: i.top, col, parts: [i] }));
    }
  }
  for (const l of lines) {
    l.parts.sort((a, b) => a.x - b.x);
    l.str = l.parts.map((p) => p.str.trim()).join(" ");
    l.left = l.parts[0].x;
    l.right = Math.max(...l.parts.map((p) => p.x + p.w));
  }
  const kept = lines.filter((l) => l.top > shown.top - 3 && l.top < shown.bottom + 12).sort((a, b) => a.top - b.top); // (the page number sits lower)
  if (mid === null) return { W, H, area: shown, turned, lines: kept };
  const ordered = [], left = kept.filter((l) => l.col === 1), right = kept.filter((l) => l.col === 2);
  for (const across of [...kept.filter((l) => l.col === 0), null]) {
    const until = across ? across.top : Infinity;
    while (left.length && left[0].top < until) ordered.push(left.shift());
    while (right.length && right[0].top < until) ordered.push(right.shift());
    if (across) ordered.push(across);
  }
  return { W, H, area: shown, turned, lines: ordered };
}
