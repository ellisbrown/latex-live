// The change highlights (Delta in the toolbar): the words and figures that changed since the previous build, the last
// commit, or the last push.

import { $, docFile, file, isDiff } from "./page.js";
import { diffWords } from "./diff.js";
import { assets, docSeq, pdfjsLib, shownVariant } from "./pdfview.js";
import { area, pageText } from "./pagetext.js";
import { reveal } from "./navigation.js";
import { drawAllMarks, marks } from "./marks.js";

// What changed since the previous build: a word-level diff of the two PDFs' text (robust to
// reflow), highlighted in green, with deletions as red bars. Replaced on every rebuild.
async function docWords(d) {
  const words = [];
  for (let n = 1; n <= d.numPages; n++) {
    const { H, items } = await pageText(d, n);
    for (const it of items) {
      if (it.top > area.bottom + 10 && /^[\divxlc]+$/i.test(it.str.trim())) continue; // page numbers shift on reflow
      const len = it.str.length || 1;
      let off = 0;
      for (const part of it.str.split(/(\s+)/)) {
        if (part.trim()) {
          const x0 = it.x + it.w * off / len, x1 = it.x + it.w * (off + part.length) / len, y = H - it.top;
          words.push({ s: part, page: n, rect: [x0, y - it.h * 0.25, x1, y + it.h * 0.85] });
        }
        off += part.length;
      }
    }
  }
  return words;
}

let changeList = [], changeIndex = -1;
function showChanges(oldWords, newWords, figures, against) {
  marks.changes.clear();
  changeList = [];
  changeIndex = -1;
  const result = oldWords && diffWords(oldWords.map((w) => w.s), newWords.map((w) => w.s));
  const add = (page, m) => { if (!marks.changes.has(page)) marks.changes.set(page, []); marks.changes.get(page).push(m); };
  if (result) textChanges(result, newWords, add);
  const texts = changeList.length;
  for (const f of figures) {
    const label = { head: "figure changed since the commit", push: "figure changed since the push" }[against] || "figure updated";
    add(f.page, { rect: f.rect, cls: "chg fig", label });
    changeList.push({ page: f.page, rect: f.rect });
  }
  changeList.sort((a, b) => a.page - b.page || b.rect[3] - a.rect[3]);
  const figs = figures.length ? `${figures.length} figure${figures.length === 1 ? "" : "s"}` : "";
  // In a git checkout the group stays, to switch baselines (and to say there are no changes).
  $("changes").hidden = !changeList.length && !headInfo;
  $("changeCount").textContent = [texts || !figs ? `${texts} change${texts === 1 ? "" : "s"}` : "", figs].filter(Boolean).join(" · ");
  drawAllMarks();
}

// The baseline for the change highlights: the last commit ("head": build/<doc>-head[-clean].pdf,
// built by the server whenever HEAD moves), the last push ("push": build/<doc>-push[-clean].pdf,
// the commit the branch's remote branch is at, built once a viewer asks for it in poll), or the
// previous build ("build"). Until the commit's baseline exists (or outside git), the previous build.
// The last push by default, which covers several local commits; the last commit when nothing
// was pushed anywhere (baseKind).
const BASES = ["push", "head", "build"];
let changeBase = BASES.find((b) => b === localStorage.getItem("latex-live:changesAgainst")) || "push";
export const baseKind = () => (changeBase === "push" && !pushInfo && headInfo ? "head" : changeBase);
// Change highlights are off unless turned on with the toolbar's Delta button (remembered).
export let changesOn = localStorage.getItem("latex-live:changesOn") === "1";
$("changesBtn").classList.toggle("on", changesOn);
let headInfo = null;       // status().head
let pushInfo = null;       // status().push: null without a remote-tracking branch to compare with (or from an older server)
export function setBaselines(head, push) { headInfo = head; pushInfo = push; }
let prevBuildWords = null; // words of the build shown before this one (same variant)
let figuresPromise = null; // {build, head, push}: changed figures of the shown PDF
export let wordsPromise = null;   // words of the shown PDF
// The document just shown (oldWords: those of the one before, when it was of the same variant): its words
// and its changed figures, to compare.
export function trackDocument(next, variant, oldWords) {
  prevBuildWords = oldWords || null;
  wordsPromise = docWords(next);
  figuresPromise = isDiff ? null : changedFigures(next, variant).catch((e) => { console.warn(e); return { build: [], head: [], push: [] }; });
}
let changesKey = null, baseCache = null;
function commitBase() {
  const kind = baseKind();
  const info = { head: headInfo, push: pushInfo }[kind];
  const version = shownVariant === "clean" ? info?.clean : info?.version;
  if (!version) return null;
  const name = docFile.replace(/\.pdf$/, `-${kind}${shownVariant === "clean" ? "-clean" : ""}.pdf`);
  return { key: `${kind}:${name}:${version}`, kind, name, version };
}
function baseWords(commit) {
  if (baseCache?.key !== commit.key) {
    baseCache = { key: commit.key, promise: pdfjsLib.getDocument({ url: `pdf/${encodeURIComponent(commit.name)}?v=${commit.version}`, ...assets }).promise
      .then(async (d) => { try { return await docWords(d); } finally { d.destroy(); } }) };
  }
  return baseCache.promise;
}
export async function updateChanges() {
  if (isDiff || !wordsPromise) return;
  if (!changesOn) {
    changesKey = null;
    if (marks.changes.size || changeList.length) { marks.changes.clear(); changeList = []; changeIndex = -1; drawAllMarks(); }
    $("changes").hidden = true;
    return;
  }
  const commit = commitBase();
  const key = `${commit ? commit.key : "build"}|${docSeq}`;
  showBase(commit);
  if (key === changesKey) return;
  changesKey = key;
  try {
    const [words, figs, base] = await Promise.all([wordsPromise, figuresPromise, commit ? baseWords(commit) : prevBuildWords]);
    if (key === changesKey) showChanges(base, words, figs[commit ? commit.kind : "build"], commit?.kind);
  } catch (e) {
    console.warn(e);
    if (key === changesKey) changesKey = null; // try again on the next poll
  }
}
function showBase(commit) {
  const kind = baseKind();
  const b = $("changeBase");
  b.hidden = !headInfo;
  if (!headInfo) return;
  const pending = kind !== "build" && !commit;
  b.textContent = { head: "vs HEAD", push: "vs last push", build: "vs last build" }[kind] + (pending ? "…" : "");
  const fallback = "showing changes since the last build";
  b.title = (kind === "head"
    ? (pending ? (headInfo.state === "failed" ? `The HEAD build failed (${headInfo.msg}); ${fallback}.`
                                              : `Building the HEAD (${headInfo.sha}) baseline; ${fallback} meanwhile.`)
               : `Changes since the last commit (${headInfo.sha}).`)
    : kind === "push"
    ? (!pushInfo ? `No pushed commit to compare with (no remote-tracking branch); ${fallback}.`
       : pending ? (pushInfo.state === "failed" ? `The last-push build failed (${pushInfo.msg}); ${fallback}.`
                                                : `Building the last-push (${pushInfo.sha}, ${pushInfo.ref}) baseline; ${fallback} meanwhile.`)
       : `Changes since the last push (${pushInfo.sha}, ${pushInfo.ref}), committed or not.`)
    : "Changes since the previous build.") + " Click to switch.";
}
export function toggleChanges() {
  changesOn = !changesOn;
  localStorage.setItem("latex-live:changesOn", changesOn ? "1" : "");
  $("changesBtn").classList.toggle("on", changesOn);
  updateChanges();
}
$("changesBtn").onclick = toggleChanges;
$("changeBase").onclick = () => { // push (when there is a pushed commit), head, build
  do changeBase = BASES[(BASES.indexOf(baseKind()) + 1) % BASES.length];
  while (changeBase === "push" && !pushInfo);
  localStorage.setItem("latex-live:changesAgainst", changeBase);
  updateChanges();
};
function textChanges(result, newWords, add) {
  // Group inserted words into runs (small gaps allowed), each run one change; deletions next to a
  // run belong to it, others are changes of their own.
  const runs = [];
  for (const i of result.inserted) {
    const last = runs.at(-1);
    if (last && i - last.at(-1) <= 3) last.push(i); else runs.push([i]);
  }
  const changes = runs.map((r) => ({ start: r[0], end: r.at(-1), words: r, dels: [] }));
  for (const at of result.deletedAt) {
    const c = changes.find((c) => at >= c.start - 1 && at <= c.end + 2);
    if (c) c.dels.push(at); else changes.push({ start: at, end: at, words: [], dels: [at] });
  }
  changes.sort((a, b) => a.start - b.start);
  for (const c of changes) {
    const lines = []; // merge each change's word boxes line by line
    for (const i of c.words) {
      const w = newWords[i], last = lines.at(-1);
      if (last && last.page === w.page && Math.abs(last.rect[1] - w.rect[1]) < 2) {
        last.rect = [Math.min(last.rect[0], w.rect[0]), Math.min(last.rect[1], w.rect[1]), Math.max(last.rect[2], w.rect[2]), Math.max(last.rect[3], w.rect[3])];
      } else {
        lines.push({ page: w.page, rect: [...w.rect] });
      }
    }
    for (const l of lines) add(l.page, { rect: l.rect, cls: "chg" });
    for (const at of c.dels) {
      if (c.words.includes(at)) continue; // a replacement: the new words already show it
      const w = newWords[Math.min(at, newWords.length - 1)];
      if (!w) continue;
      const x = at < newWords.length ? w.rect[0] - 1.5 : w.rect[2] + 0.5;
      lines.push({ page: w.page, rect: [x, w.rect[1], x + 2, w.rect[3]] });
      add(w.page, { rect: [x, w.rect[1], x + 2, w.rect[3]], cls: "del" });
    }
    if (lines.length) changeList.push(lines[0]);
  }
}

// Changed figures: the text diff can't see them, so compare the included graphics files' hashes
// between builds, and ask git which differ from HEAD and from the last push. Outlined, placed by
// SyncTeX at their \includegraphics: {build, head, push}, one list per baseline.
let graphicsHashes = null;
async function changedFigures(d, variant) {
  const list = await (await fetch(`graphics/${encodeURIComponent(file)}`)).json();
  const before = graphicsHashes;
  graphicsHashes = new Map(list.map((g) => [g.file, g.hash]));
  const build = before ? list.filter((g) => before.get(g.file) !== g.hash) : [];
  const head = list.filter((g) => g.dirty), push = list.filter((g) => g.unpushed);
  const found = new Map();
  for (const g of new Set([...build, ...head, ...push])) {
    const r = await fetch(`forward?${new URLSearchParams({ file: g.src, line: g.line, ...(variant === "clean" ? { clean: 1 } : {}) })}`);
    if (!r.ok) continue;
    const t = await r.json();
    const [, , W, H] = (await d.getPage(t.page)).view;
    found.set(g, { page: t.page, file: g.file, rect: [Math.max(0, t.x), H - t.y - t.h, Math.min(W, t.x + t.w), H - t.y] });
  }
  const placed = (gs) => gs.map((g) => found.get(g)).filter(Boolean);
  return { build: placed(build), head: placed(head), push: placed(push) };
}
export function goToChange(step) {
  if (!changeList.length) return;
  changeIndex = (changeIndex + step + changeList.length) % changeList.length;
  const c = changeList[changeIndex];
  $("changeCount").textContent = `${changeIndex + 1} / ${changeList.length} changes`;
  reveal(c.page, [c.rect[0] - 2, c.rect[1] - 2, c.rect[2] + 2, c.rect[3] + 2]);
}
$("nextChange").onclick = () => goToChange(1);
$("prevChange").onclick = () => goToChange(-1);
$("clearChanges").onclick = () => { marks.changes.clear(); changeList = []; $("changes").hidden = true; drawAllMarks(); };
