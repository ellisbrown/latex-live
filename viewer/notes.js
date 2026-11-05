// The notes: listed in the sidebar, and as bubbles in the margin of the notes-free view.

import { $, cleanFile, el, file, hubBase, isDiff } from "./page.js";
import { canOpenFile, opensAt, setWho, who } from "./role.js";
import { doc, eventBus, shownVariant, viewer } from "./pdfview.js";
import { GUTTER, setGutter } from "./layout.js";
import { columnAt, columnMid, norm, textLines } from "./pagetext.js";
import { openSource, showSource } from "./links.js";
import { noteTools } from "./comments.js";

// Notes (\todo and per-author note macros) from live.py, in reading order. Click one to see
// it in the PDF; click its file:line to open it in the editor.
const AUTHOR_COLORS = { TODO: "#ef5350", EB: "#26a69a", CH: "#4fc3f7", SX: "#bcaaa4", Hu: "#ce93d8", MR: "#f06292",
                        JV: "#9575cd", Shengyi: "#f8bbd0", Daniel: "#66bb6a" };
const authorColor = (a) => AUTHOR_COLORS[a] || `hsl(${[...a].reduce((h, c) => h * 31 + c.charCodeAt(0), 7) % 360} 60% 70%)`;
const hiddenAuthors = new Set(JSON.parse(localStorage.getItem(`latex-live:${hubBase}hiddenAuthors`) || "[]"));
let noteList = [];
export async function loadNotes() {
  noteList = await (await fetch(`notes/${encodeURIComponent(file)}`)).json();
  document.querySelector("#tabs [data-tab=notes] .n").textContent = noteList.length || "";
  renderNotes();
}
function renderNotes() {
  const panel = $("notes");
  const scroll = panel.scrollTop;
  const counts = {};
  for (const n of noteList) counts[n.author] = (counts[n.author] || 0) + 1;
  const filters = el("div", { className: "filters" });
  for (const [author, count] of Object.entries(counts)) {
    const chip = el("span", { className: `chip${hiddenAuthors.has(author) ? " off" : ""}`, textContent: `${author} ${count}`,
                              title: "Show or hide this author's notes" });
    chip.style.background = authorColor(author);
    chip.onclick = () => {
      hiddenAuthors.has(author) ? hiddenAuthors.delete(author) : hiddenAuthors.add(author);
      localStorage.setItem(`latex-live:${hubBase}hiddenAuthors`, JSON.stringify([...hiddenAuthors]));
      renderNotes();
      drawAllMargins();
    };
    filters.append(chip);
  }
  const items = noteList.filter((n) => !hiddenAuthors.has(n.author)).map((n) => {
    const who = el("span", { className: "chip who", textContent: n.author });
    who.style.background = authorColor(n.author);
    const where = opensAt(el("span", { className: "where", textContent: `${n.file.replace(/^.*\//, "")}:${n.line}` }), `${n.file}:${n.line}`);
    where.onclick = (e) => { e.stopPropagation(); openSource(n.file, n.line); };
    const row = el("div", { className: "note", title: "Show in the PDF" }, who, ...(canOpenFile(n.file) ? [where] : []),
                   el("div", { className: "text", textContent: n.text || "(empty)" }));
    const tools = noteTools(n);
    if (tools) who.after(tools);
    row.onclick = () => showSource(n.file, n.line);
    return row;
  });
  panel.replaceChildren(...(noteList.length ? [filters, ...items] : [el("div", { className: "empty", textContent: "No inline notes in the source." })]));
  panel.scrollTop = scroll;
}

// Margin notes: in the notes-free view, each note as a bubble in a gutter right of its page (with
// two columns, a left-column note's in a gutter left of it), wired to a caret where it sits in the
// text. live.py (/margin) gives, per note, where its source line landed (SyncTeX) and the typeset
// words around it; those words, found in the page text, put the caret between two words. Hover a
// bubble or caret to follow the wire; click to open the source.
export let marginOn = localStorage.getItem("latex-live:margin") !== "0";
let marginAnchors = new Map(); // page -> [{note, x, top}] (points from the page's top-left, baseline)
export function resetMargin() { marginAnchors = new Map(); } // (a new document: loadMargin anchors its notes)
const leftOfPage = (a) => columnMid() !== null && columnAt(a.x) === 1; // a note in the left column

// The gutters the notes need: right, and left too for notes in a left column.
function fitGutters() {
  const all = [...marginAnchors.values()].flat();
  setGutter(marginOn && all.some((a) => !leftOfPage(a)), marginOn && all.some(leftOfPage));
}
export function showMarginButton() {
  const b = $("marginBtn");
  b.hidden = isDiff || shownVariant !== "clean";
  b.classList.toggle("on", marginOn);
  b.title = marginOn ? "Showing the notes as bubbles in the margin; click to hide them"
                     : "Show the notes as bubbles in the margin, wired to where they sit";
}
$("marginBtn").onclick = () => {
  marginOn = !marginOn;
  localStorage.setItem("latex-live:margin", marginOn ? "1" : "0");
  showMarginButton();
  fitGutters();
  drawAllMargins();
};

export async function loadMargin(d) {
  const notes = await (await fetch(`margin/${encodeURIComponent(cleanFile)}`)).json();
  const anchors = new Map();
  for (const n of notes) {
    const a = await anchorNote(d, n);
    if (d !== doc) return; // a newer build arrived meanwhile
    if (!anchors.has(a.page)) anchors.set(a.page, []);
    anchors.get(a.page).push({ note: n, ...a });
  }
  marginAnchors = anchors;
  fitGutters();
  drawAllMargins();
}

// Where a note's caret goes: right after its preceding words (or before its following ones) in
// the page text near where SyncTeX put its line, else on the whole page; failing both, at the end
// (or start) of that line.
async function anchorNote(d, n) {
  const pages = [...new Set(n.points.map((p) => p[0]))];
  if (n.side !== "after") pages.reverse(); // a note after its line's text: the last page it reaches
  const before = norm(n.before.normalize("NFKC")), after = norm(n.after.normalize("NFKC"));
  const trailing = n.before.trimEnd().match(/[^\p{L}\p{N}\s]*$/u)[0].length; // the caret goes after "word)." too
  // Longest context first; shorter keys only near the note's own lines, where a short match is unlikely to be wrong.
  for (const [len, bands] of [[30, [true, false]], [14, [true]]]) {
    const b = before.slice(-len), a = after.slice(0, len);
    for (const band of bands) {
      // A float's caption can land a page away from where SyncTeX puts a note next to it.
      const spill = [Math.min(...pages) - 1, Math.max(...pages) + 1].filter((p) => p >= 1 && p <= d.numPages);
      for (const page of band ? pages : len === 30 ? [...pages, ...spill] : []) {
        const ys = n.points.filter((p) => p[0] === page).map((p) => p[2]);
        const col = columnAt((n.points.find((p) => p[0] === page) || n.points[0])[1]);
        const { lines: all } = await textLines(d, page);
        const lines = all.filter((l) => !l.col || l.col === col || !band); // (with two columns: the note's own)
        const near = band ? lines.filter((l) => l.top > Math.min(...ys) - 14 && l.top < Math.max(...ys) + 14) : lines;
        let text = "";
        const at = []; // per character of text: [x0, x1, top]
        for (const l of near) {
          for (const p of l.parts) {
            const chars = [...p.str];
            chars.forEach((c, i) => {
              const x0 = p.x + p.w * i / chars.length, x1 = p.x + p.w * (i + 1) / chars.length, keys = norm(c.normalize("NFKC"));
              for (const k of keys) { text += k; at.push([x0, x1, l.top, []]); }
              if (!keys && c.trim() && at.at(-1)?.[2] === l.top) at.at(-1)[3].push(x1); // punctuation right after it
            });
          }
        }
        let i;
        if (b.length >= 6 && (i = text.lastIndexOf(b)) >= 0) {
          const [, x1, top, punct] = at[i + b.length - 1];
          return { page, x: (trailing ? punct[trailing - 1] ?? punct.at(-1) ?? x1 : x1) + 1, top };
        }
        if (a.length >= 6 && (i = text.indexOf(a)) >= 0) {
          const [x0, , top] = at[i];
          return { page, x: x0 - 1.5, top };
        }
        if (band || !pages.includes(page)) continue;
        // The context runs across a page break: the page ends with the start of the text after the note,
        // or starts with the end of the text before it.
        for (let k = Math.min(a.length, text.length); k >= 8; k--) {
          if (text.endsWith(a.slice(0, k))) { const [x0, , top] = at[text.length - k]; return { page, x: x0 - 1.5, top }; }
        }
        for (let k = Math.min(b.length, text.length); k >= 8; k--) {
          if (text.startsWith(b.slice(-k))) { const [, x1, top] = at[k - 1]; return { page, x: x1 + 1, top }; }
        }
      }
    }
  }
  const p = n.side === "after" || n.side === "near" ? n.points[0] : n.points.at(-1);
  const { lines } = await textLines(d, p[0]);
  const line = lines.find((l) => Math.abs(l.top - p[2]) < 2 && (!l.col || l.col === columnAt(p[1])));
  const x = !line ? p[1] : n.side === "after" || n.side === "near" ? line.left - 1.5 : n.side === "before" ? line.right + 1 : p[1];
  return { page: p[0], x, top: p[2] };
}

const SVG = "http://www.w3.org/2000/svg";
function drawMargin(pageNumber) {
  const pageView = viewer.getPageView(pageNumber - 1);
  if (!pageView?.div || !pageView.pdfPage) return;
  pageView.div.querySelector(".ll-margin")?.remove();
  const list = (marginOn && shownVariant === "clean" && marginAnchors.get(pageNumber) || [])
    .filter((a) => !hiddenAuthors.has(a.note.author));
  if (!list.length) return;
  const [vx, vy, vw] = pageView.pdfPage.view;
  const cw = pageView.div.clientWidth, ch = pageView.div.clientHeight, s = cw / (vw - vx);
  const layer = el("div", { className: "ll-margin" });
  const svg = document.createElementNS(SVG, "svg"); // (wires to a left gutter overflow it)
  svg.setAttribute("width", cw + GUTTER);
  svg.setAttribute("height", ch);
  layer.append(svg);
  pageView.div.append(layer);
  const items = list.map((a) => {
    const n = a.note, color = authorColor(n.author), left = leftOfPage(a);
    const bubble = el("div", { className: `ll-bubble${left ? " left" : ""}`, title: `${n.file}:${n.line}` },
                      el("div", { className: "text" }, el("b", { textContent: n.author }), n.text || "(empty)"));
    if (canOpenFile(n.file)) opensAt(bubble, `${n.file}:${n.line}`);
    bubble.style[left ? "borderRightColor" : "borderLeftColor"] = color;
    bubble.querySelector("b").style.color = color;
    const tools = noteTools(n);
    if (tools) bubble.prepend(tools);
    const caret = el("div", { className: "caret", title: `${n.author}: ${n.text}` });
    const x = (a.x - vx) * s, y = (a.top - vy) * s;
    Object.assign(caret.style, { left: `${x}px`, top: `${y - 8.5 * s}px`, height: `${11 * s}px`, background: color });
    const path = document.createElementNS(SVG, "path");
    path.setAttribute("stroke", color);
    svg.append(path);
    layer.append(caret, bubble);
    const hot = (on) => { for (const e of [bubble, caret, path]) e.classList.toggle("hot", on); };
    for (const e of [bubble, caret]) {
      e.onmouseenter = () => hot(true);
      e.onmouseleave = () => hot(false);
      e.onclick = (ev) => { ev.stopPropagation(); if (canOpenFile(n.file)) openSource(n.file, n.line); };
    }
    return { bubble, path, x, y, s, left };
  }).sort((a, b) => a.y - b.y || a.x - b.x);
  // In each gutter, stack the bubbles in reading order, each as close to its line as the ones
  // above allow; pull them up from the page bottom if they run past it; then undo any overlap that left.
  for (const side of [items.filter((it) => !it.left), items.filter((it) => it.left)]) {
    let y = 0;
    for (const it of side) { it.h = it.bubble.offsetHeight; it.top = Math.max(it.y - 12, y); y = it.top + it.h + 4; }
    let limit = ch;
    for (const it of [...side].reverse()) { it.top = Math.max(0, Math.min(it.top, limit - it.h)); limit = it.top - 4; }
    y = 0;
    for (const it of side) { it.top = Math.max(it.top, y); y = it.top + it.h + 4; }
  }
  for (const it of items) {
    it.bubble.style.top = `${it.top}px`;
    const base = it.y + 2.5 * it.s, by = it.top + Math.min(12, it.h / 2);
    it.path.setAttribute("d", it.left ? `M${it.x},${base} H0 C-10,${base} -6,${by} -16,${by}`
                                      : `M${it.x},${base} H${cw} C${cw + 10},${base} ${cw + 6},${by} ${cw + 16},${by}`);
  }
}
const drawAllMargins = () => { for (let n = 1; n <= (doc?.numPages || 0); n++) drawMargin(n); };
eventBus.on("pagerendered", (e) => drawMargin(e.pageNumber));

$("whoPick").onchange = (e) => {
  setWho(e.target.value);
  localStorage.setItem(`latex-live:${hubBase}who`, who);
  renderNotes();
  drawAllMargins();
};
