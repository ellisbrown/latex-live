// The sidebar's outline.

import { $, cleanFile, el, file } from "./page.js";
import { doc, linkService, shownVariant } from "./pdfview.js";
import { area, areaFraction, norm, textLines } from "./pagetext.js";
import { displayedResult } from "./pages.js";

// Outline, rebuilt on each reload: the PDF's bookmarks (hyperref), or else the contents entries
// from live.py (e.g. this ICLR draft, whose restored \addcontentsline skips hyperref). Headings show
// their numbers from the build (live.py), appendix letters included.
// Each heading shows how many pages its section takes (up to the end of the main text for the
// last main-text section).
let outlineRows = [];
export async function renderOutline() {
  outlineRows = [];
  const build = (items, depth) => {
    const ul = el("ul");
    for (const item of items) {
      const li = el("li");
      li.classList.toggle("minor", !!item.minor);
      const pp = el("span", { className: "pp" });
      const span = el("span", { title: item.title }, ...(item.num ? [el("span", { className: "num", textContent: item.num })] : []),
                      el("span", { className: "t", textContent: item.title }), pp);
      span.onclick = () => (item.dest ? linkService.goToDestination(item.dest) : goToHeading(item));
      outlineRows.push({ item, depth, pp });
      li.append(span);
      if (item.items.length) li.append(build(item.items, depth + 1));
      ul.append(li);
    }
    return ul;
  };
  const d = doc;
  const entries = await (await fetch(`outline/${encodeURIComponent(shownVariant === "clean" ? cleanFile : file)}`)).json().catch(() => []);
  let outline = await d.getOutline();
  if (d !== doc) return;
  if (outline?.length) numberBookmarks(outline, entries);
  else outline = nest(entries);
  const nav = $("outline");
  const scroll = nav.scrollTop;
  nav.replaceChildren(outline.length ? build(outline, 0) : el("div", { className: "empty", textContent: "No outline: the PDF has no bookmarks and the build no .toc." }));
  nav.scrollTop = scroll;
  fillSectionPages();
}
export async function fillSectionPages() {
  const d = doc, r = displayedResult(), rows = outlineRows;
  if (!r || !rows.length) return;
  const pos = []; // page index + fraction of the text area, found in reading order
  let from = 0;
  for (const { item } of rows) {
    if (item.minor) { pos.push(null); continue; } // (run-in paragraph titles: too generic to find reliably)
    const p = await headingPos(d, item, from).catch(() => null);
    if (p?.found) from = p.index;
    pos.push(p?.found ? p.index + areaFraction(p.top, p.x) : null);
  }
  if (d !== doc || rows !== outlineRows) return;
  const mainEnd = r.page - 1 + r.fill;
  rows.forEach((row, i) => {
    row.pp.textContent = "";
    if (row.item.minor || pos[i] == null) return;
    let j = i + 1; // the next heading at the same or a higher level
    while (j < rows.length && (rows[j].depth > row.depth || pos[j] == null)) j++;
    let end = j < rows.length ? pos[j] : d.numPages;
    const main = pos[i] < mainEnd;
    if (main && end > mainEnd) end = mainEnd;
    const pages = end - pos[i];
    if (pages < 0.05) return;
    row.pp.textContent = pages.toFixed(1);
    row.pp.title = `≈${pages.toFixed(1)} pages${main ? " of main text" : ""}`;
  });
}

// Bookmarks get their headings' numbers: matched by destination, else by title in reading order
// (unless the title already starts with it, as with hyperref's bookmarksnumbered).
function numberBookmarks(items, entries) {
  const byDest = new Map();
  for (const e of entries) if (e.dest && !byDest.has(e.dest)) byDest.set(e.dest, e); // (later entries without an anchor of their own reuse the last one)
  const key = (s) => s.replace(/\s+/g, " ").trim().toLowerCase();
  let from = 0;
  const walk = (list) => {
    for (const item of list) {
      let e = typeof item.dest === "string" ? byDest.get(item.dest) : null;
      if (!e) {
        const i = entries.findIndex((x, k) => k >= from && key(x.title) === key(item.title));
        if (i >= 0) [e, from] = [entries[i], i + 1];
      }
      if (e?.num && !item.title.startsWith(`${e.num} `)) item.num = e.num;
      walk(item.items || []);
    }
  };
  walk(items);
}

// Flat contents entries ({level, num, title, page, dest}) to a bookmark-style tree.
function nest(entries) {
  const root = { level: -1, items: [] }, stack = [root];
  for (const e of entries) {
    while (stack.at(-1).level >= e.level) stack.pop();
    const item = { ...e, minor: e.level >= 4, items: [] };
    stack.at(-1).items.push(item);
    stack.push(item);
  }
  return root.items;
}

// A .toc entry without an anchor: go to its page label, then to the line that holds its title.
async function goToHeading(entry) {
  const p = await headingPos(doc, entry);
  if (p) linkService.goToDestination([p.index, { name: "XYZ" }, null, p.H - p.top + 4, null]);
}
// Where a heading is: {index (page), top (points from the page top), H, found}: on its labeled
// page, or nearby (not before `from`, the previous heading's page) should the label be stale.
async function headingPos(d, entry, from = null) {
  if (entry.dest) {
    const dest = typeof entry.dest === "string" ? await d.getDestination(entry.dest) : entry.dest;
    if (!Array.isArray(dest)) return null;
    const index = typeof dest[0] === "object" && dest[0] ? await d.getPageIndex(dest[0]) : dest[0];
    const H = (await d.getPage(index + 1)).view[3];
    const xyz = dest[1]?.name === "XYZ";
    return { index, H, top: xyz && dest[3] != null ? H - dest[3] : area.top, x: xyz ? dest[2] : null, found: true };
  }
  const labels = await d.getPageLabels();
  const index = labels?.includes(entry.page) ? labels.indexOf(entry.page) : +entry.page - 1;
  if (!(index >= 0 && index < d.numPages)) return null;
  const [, titleNumber, text] = entry.title.match(/^(?:([A-Z]?[\d.]*\d|[A-Z])\s+)?(.*)$/);
  const number = entry.num || titleNumber;
  const key = norm(text).slice(-20); // the end of the title: its start may be a symbol or macro
  const pages = [index, index - 1, index + 1, index - 2];
  for (const i of pages.filter((i) => i >= (from ?? 0) && i < d.numPages)) {
    const { H, lines } = await textLines(d, i + 1);
    const line = (key && lines.find((l) => norm(l.str).includes(key))) ||
                 (number && lines.find((l) => l.str.startsWith(`${number} `)));
    if (line) return { index: i, H, top: line.top - 10, x: line.left, found: true };
  }
  return { index, H: (await d.getPage(index + 1)).view[3], top: 0, found: false };
}
