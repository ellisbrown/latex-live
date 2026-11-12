// Where to cut (✂ in the toolbar).

import { $, el, isDiff } from "./page.js";
import { doc, pdfjsLib } from "./pdfview.js";
import { area, columnMid, textLines } from "./pagetext.js";
import { reveal } from "./navigation.js";
import { cutOn, drawAllMarks, marks, setCutOn } from "./marks.js";
import { displayedResult } from "./pages.js";

// Where to cut: paragraphs whose last line holds only a word or two, and large empty bands in the
// text column (around figures and tables, or a page that ends early), in the main text of the PDF
// as shown. Each spot estimates the lines it would give back.
let cutList = [], cutIndex = -1;
const cutCache = new WeakMap();
export function cutSpots(d, r) {
  if (!cutCache.has(d)) cutCache.set(d, findCutSpots(d, r));
  return cutCache.get(d);
}
async function findCutSpots(d, r) {
  const spots = [];
  for (let p = r.first; p <= r.page; p++) {
    const { lines: all, turned } = await textLines(d, p);
    if (turned) continue; // (a landscape page holds a table or a figure, not the text columns)
    let ctx = null, canvas = null; // the rendered page, for the empty-band scan (once per page)
    for (const col of columnMid() === null ? [1] : [1, 2]) { // each text column on its own
      if (p === r.page && col > (r.endCol || 1)) break;
      const end = p === r.page && col === (r.endCol || 1) ? r.endTop : area.bottom + 12;
      const lines = all.filter((l) => l.col === col);
      // A full line of body text spans most of the column in body-size type, unlike a title (before a
      // build reports the column's width: over 60 characters).
      const full = (l) => (area.colwidth ? l.right - l.left > area.colwidth * 0.85 && Math.max(...l.parts.map((q) => q.h)) <= area.leading
                                         : l.str.length > 60);
      const body = lines.filter(full);
      if (body.length < 3) continue;
      const lefts = {};
      for (const l of body) lefts[Math.round(l.left)] = (lefts[Math.round(l.left)] || 0) + 1;
      const colLeft = +Object.entries(lefts).sort((a, b) => b[1] - a[1])[0][0];
      const aligned = body.filter((l) => Math.abs(l.left - colLeft) < 2);
      const colRight = Math.max(...aligned.map((l) => l.right));
      for (let i = 1; i < lines.length; i++) {
        const l = lines[i], prev = lines[i - 1], next = lines[i + 1];
        if (l.top > end) break;
        const short = l.str.split(/\s+/).length <= 2 || l.right - l.left < (colRight - colLeft) * 0.12;
        if (!short || Math.abs(l.left - colLeft) > 1.5 || /^[\d\s.,%()–-]*$/.test(l.str)) continue;
        // after a full justified line of the same paragraph, and not followed by more of it
        if (prev.right < colRight - 3 || !full(prev) || prev.left < colLeft - 1.5 || l.top - prev.top > area.leading + 3) continue;
        if (next && next.top - l.top < area.leading + 2 && Math.abs(next.left - colLeft) < 1.5) continue;
        const h = Math.max(...l.parts.map((q) => q.h));
        spots.push({ page: p, col, top: l.top, lines: 1, label: "1 line", text: `“${l.str}” alone on a paragraph's last line`,
                     rect: [l.left - 1, l.top - h * 0.9, colRight, l.top + h * 0.3] });
      }
      // Empty bands: rows of the text column with nothing drawn in them, well beyond a paragraph break.
      if (!ctx) {
        const page = await d.getPage(p);
        const viewport = page.getViewport({ scale: 1 });
        canvas = el("canvas", { width: Math.ceil(viewport.width), height: Math.ceil(viewport.height) });
        ctx = canvas.getContext("2d", { willReadFrequently: true });
        await page.render({ canvasContext: ctx, viewport, annotationMode: pdfjsLib.AnnotationMode.DISABLE }).promise;
      }
      const x0 = Math.max(0, Math.floor(colLeft - 2)), x1 = Math.min(canvas.width, Math.ceil(colRight + 2));
      // (On the first page, only between its first and last full lines: the spacing of a title block,
      // and of a title page that ends after the abstract, is by design.)
      const start = p === r.first ? Math.max(area.top, body[0].top - 12) : area.top;
      const stop = p === r.first ? Math.min(end, aligned.at(-1).top + area.leading) : end;
      const y0 = Math.floor(start), y1 = Math.min(canvas.height, Math.ceil(stop));
      if (y1 - y0 < 30) continue;
      const { data } = ctx.getImageData(x0, y0, x1 - x0, y1 - y0);
      const w = x1 - x0;
      let run = null;
      for (let y = 0; y <= y1 - y0; y++) {
        let blank = y < y1 - y0;
        for (let x = 0; blank && x < w; x++) {
          const k = (y * w + x) * 4;
          if (data[k + 3] > 0 && (data[k] < 235 || data[k + 1] < 235 || data[k + 2] < 235)) blank = false;
        }
        if (blank) { run ??= y; continue; }
        const gap = run === null ? 0 : y - run;
        const n = Math.round((gap - 10) / area.leading); // a paragraph break leaves ~10pt
        if (gap > 28 && n >= 1) {
          const top = y0 + run, bottom = y0 + y, atEnd = y === y1 - y0 && p < r.page;
          spots.push({ page: p, col, top, lines: n, label: `≈${n} line${n === 1 ? "" : "s"}`,
                       text: `${Math.round(gap)} pt of empty space ${atEnd ? "at the bottom of the column" : "in the text column"}`,
                       rect: [colLeft, top, colRight, bottom] });
        }
        run = null;
      }
    }
  }
  return spots.sort((a, b) => a.page - b.page || (a.col || 1) - (b.col || 1) || a.top - b.top);
}
export async function updateCut() {
  $("cut").classList.toggle("on", cutOn);
  const d = doc, r = displayedResult();
  marks.cut.clear();
  cutList = [];
  cutIndex = -1;
  if (cutOn && d && r) {
    const spots = await cutSpots(d, r);
    if (d !== doc || !cutOn) return;
    cutList = spots;
    for (const s of spots) {
      if (!marks.cut.has(s.page)) marks.cut.set(s.page, []);
      marks.cut.get(s.page).push({ rect: s.rect, cls: "cut", label: s.label });
    }
    const lines = spots.reduce((n, s) => n + s.lines, 0);
    $("cutCount").textContent = spots.length ? `${spots.length} spot${spots.length === 1 ? "" : "s"} · ≈${lines} line${lines === 1 ? "" : "s"}` : "nothing to cut";
  }
  $("cutNav").hidden = !cutOn || !r;
  drawAllMarks();
}
function goToCut(step) {
  if (!cutList.length) return;
  cutIndex = (cutIndex + step + cutList.length) % cutList.length;
  const s = cutList[cutIndex];
  $("cutCount").textContent = `${cutIndex + 1} / ${cutList.length}: ${s.label}`;
  reveal(s.page, s.rect);
}
$("cut").onclick = () => { setCutOn(!cutOn); localStorage.setItem("latex-live:cut", cutOn ? "1" : ""); updateCut(); };
$("nextCut").onclick = () => goToCut(1);
$("prevCut").onclick = () => goToCut(-1);
$("cut").hidden = isDiff;
