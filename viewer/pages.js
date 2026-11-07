// The main text's length against the page limit (the toolbar's badge), and the sidebar's page thumbnails.

import { $, cleanFile, el, file } from "./page.js";
import { assets, doc, eventBus, pdfjsLib, shownVariant, viewer } from "./pdfview.js";
import { sideOpen } from "./layout.js";
import { area, areaFraction, columnAt, textLines } from "./pagetext.js";
import { goToPage } from "./navigation.js";

// Main-text length, against the page limit when there is one. Main text runs from the page labeled
// "1" (a draft ToC may come first) up to the references or an earlier unnumbered statement (AI
// use, ethics, reproducibility, impact, acknowledgments), which venues such as ICLR and ICML do not
// count; without references (refs null), to the end of the document.
const UNCOUNTED = /^(ai use|llm usage|use of large language models|ethics statement|reproducibility statement|impact statement|acknowledge?ments?)\b/i;
// More that the venue's style leaves out (live.py's status says), e.g. ACL's Limitations: a heading of just those words,
// as a line of prose may start with them too.
let venueUncounted = null;
export function setUncounted(words) {
  const source = words?.length ? `^(${words.map((w) => w.replace(/[^\w ]/g, "")).join("|")})[.:]?$` : null;
  if (source !== (venueUncounted?.source ?? null)) venueUncounted = source && new RegExp(source, "i");
}
const uncounted = (s) => UNCOUNTED.test(s) || !!venueUncounted?.test(s);
export async function checkPages(d, refs, limit) {
  const labels = await d.getPageLabels();
  const first = Math.max(0, labels?.indexOf("1") ?? 0) + 1;
  let end = refs ? { page: refs.page, top: refs.y, x: refs.x } : { page: d.numPages + 1, top: area.top }; // (then: to the last line)
  for (let p = Math.max(first, end.page - 2); refs && p <= refs.page; p++) {
    const { lines } = await textLines(d, p);
    const heading = lines.find((l) => l.str.length < 60 && uncounted(l.str) && (p < refs.page || l.top < refs.y));
    if (heading) { end = { page: p, top: heading.top - 10, x: heading.left }; break; }
  }
  let page = end.page, fill, endTop, endCol;
  if (areaFraction(end.top, end.x) * (area.bottom - area.top) > 15) { // main text ends partway into this page
    [endTop, endCol] = [end.top, columnAt(end.x)];
    fill = areaFraction(end.top, end.x);
  } else { // it ends on the previous page: measure down to its last text line (in its last column)
    page -= 1;
    const { lines } = await textLines(d, page);
    endCol = Math.max(1, ...lines.map((l) => l.col));
    const last = lines.filter((l) => (l.col || 1) === endCol);
    endTop = Math.max(area.top, ...last.map((l) => l.top)) + 3;
    fill = areaFraction(endTop - 3, last[0]?.left);
  }
  fill = Math.min(1, Math.max(0, fill));
  const used = page - first + 1;
  const lastAllowed = limit ? first + limit - 1 : null;
  return { first, page, fill, endTop, endCol, used, limit, value: used - 1 + fill, lastAllowed, lastAllowedLabel: labels?.[lastAllowed - 1] ?? lastAllowed,
           cls: !limit ? "" : used > limit ? "over" : used === limit && fill > 0.9 ? "tight" : "ok",
           range: `page ${labels?.[first - 1] ?? first} to ${labels?.[page - 1] ?? page} (${Math.round(fill * 100)}% of the last page)` };
}

// Badge: the notes-free build's count (what would be submitted) when there is one.
export let pageResult = null, cleanResult = null;
export function renderPages() {
  const badge = $("pages");
  const main = pageResult, clean = cleanResult;
  if (!main) return (badge.textContent = "");
  const shownResult = clean || main;
  const of = main.limit ? ` / ${main.limit}` : "";
  badge.textContent = `${shownResult.value.toFixed(1)}${of} pp`; // (the notes-free length when there is one: what would be submitted)
  badge.className = shownResult.cls;
  const shownTag = (v) => (shownVariant === v ? " (as shown)" : "");
  badge.title = (clean ? `Main text ${clean.value.toFixed(1)}${of} pp without the inline notes${shownTag("clean")}: ${clean.range}.\n` +
                         `${main.value.toFixed(1)} pp with notes${shownTag("notes")}: ${main.range}.`
                       : `Main text ${main.value.toFixed(1)}${of} pp: ${main.range}.`) +
                "\nEnds at the references or at an unnumbered statement (AI use, ethics, reproducibility, impact, acknowledgments).";
  if (sideOpen("thumbs")) markThumbLimit();
}
// The build that is not on screen is measured from its own copy of the PDF.
export let cleanShown = null, notesShown = null, measuring = false;
export const displayedResult = () => (shownVariant === "clean" ? cleanResult : pageResult);
export function setResult(variant, version, r) {
  if (variant === "clean") { cleanResult = r; cleanShown = version; } else { pageResult = r; notesShown = version; }
}
export async function measureVariant(variant, info) {
  measuring = true;
  try {
    const [name, version, refs] = variant === "clean" ? [cleanFile, info.clean.version, info.clean.refs] : [file, info.version, info.refs];
    const d = await pdfjsLib.getDocument({ url: `pdf/${encodeURIComponent(name)}?v=${version}`, ...assets }).promise;
    const r = await checkPages(d, refs, info.pageLimit);
    d.destroy();
    setResult(variant, version, r);
    renderPages();
  } catch (e) {
    console.warn(e);
  } finally {
    measuring = false;
  }
}

// Page thumbnails (rendered as they scroll into view), with the page limit marked.
let thumbsDoc = null, thumbObserver = null;
export async function renderThumbs() {
  if (thumbsDoc === doc) return markThumbLimit();
  thumbsDoc = doc;
  const d = doc, panel = $("thumbs");
  thumbObserver?.disconnect();
  thumbObserver = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting || e.target.dataset.done) continue;
      e.target.dataset.done = "1";
      drawThumb(d, e.target);
    }
  }, { root: panel, rootMargin: "300px" });
  const labels = await d.getPageLabels();
  const first = await d.getPage(1);
  const ratio = first.view[3] / first.view[2];
  panel.replaceChildren();
  for (let n = 1; n <= d.numPages; n++) {
    const img = el("div", { className: "img" });
    img.style.aspectRatio = `${1 / ratio}`;
    const thumb = el("div", { className: "thumb", title: `Page ${n}` }, img, el("div", { className: "label", textContent: labels?.[n - 1] ?? n }));
    thumb.dataset.page = n;
    thumb.onclick = () => goToPage(n);
    panel.append(thumb);
    thumbObserver.observe(thumb);
  }
  markCurrentThumb(viewer.currentPageNumber);
  markThumbLimit();
}
async function drawThumb(d, thumb) {
  const page = await d.getPage(+thumb.dataset.page);
  const width = thumb.clientWidth * (window.devicePixelRatio || 1);
  const viewport = page.getViewport({ scale: width / page.view[2] });
  const canvas = el("canvas", { width: viewport.width, height: viewport.height });
  await page.render({ canvasContext: canvas.getContext("2d"), viewport, annotationMode: pdfjsLib.AnnotationMode.DISABLE }).promise;
  thumb.querySelector(".img").prepend(canvas);
}
function markCurrentThumb(n) {
  if (!sideOpen("thumbs")) return;
  for (const t of document.querySelectorAll(".thumb.current")) t.classList.remove("current");
  const t = document.querySelector(`.thumb[data-page="${n}"]`);
  t?.classList.add("current");
  t?.scrollIntoView({ block: "nearest" });
}
eventBus.on("pagechanging", (e) => markCurrentThumb(e.pageNumber));
// Pages past the limit are shaded red, down to where the main text ends; the last allowed page
// gets a red underline.
function markThumbLimit() {
  for (const t of document.querySelectorAll(".thumb")) {
    t.classList.remove("last-allowed");
    t.querySelector(".over")?.remove();
    t.querySelector(".tag")?.remove();
  }
  const r = displayedResult();
  if (!r?.limit) return;
  const { lastAllowed } = r;
  document.querySelector(`.thumb[data-page="${lastAllowed}"]`)?.classList.add("last-allowed");
  for (let p = lastAllowed + 1; p <= r.page; p++) {
    const t = document.querySelector(`.thumb[data-page="${p}"]`);
    if (!t) continue;
    const over = el("div", { className: "over" });
    over.style.height = p < r.page ? "100%" : `${(area.top + r.fill * (area.bottom - area.top)) / area.H * 100}%`;
    t.querySelector(".img").append(over);
    t.querySelector(".label").append(el("span", { className: "tag", textContent: " over limit" }));
  }
}
