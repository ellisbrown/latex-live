// PDF.js: the viewer, the document on screen (doc, shownVariant), its scroll and zoom (kept across reloads), and the
// toolbar's page and zoom controls. Loading the library waits at the top, so the modules that import this one wait too.

import * as pdfjsLib from "../pdfjs/build/pdf.min.mjs";
import { $, file, hubBase } from "./page.js";
import { me } from "./role.js";

globalThis.pdfjsLib = pdfjsLib; // pdf_viewer.mjs reads the library from the global
export { pdfjsLib }; // (and so do the modules that open or render PDFs of their own)
pdfjsLib.GlobalWorkerOptions.workerSrc = "./pdfjs/build/pdf.worker.min.mjs";
export const { EventBus, PDFViewer, PDFLinkService, PDFFindController, FindState, LinkTarget } =
  await import("../pdfjs/web/pdf_viewer.mjs");

export let shownVariant = null; // the build on screen: "notes" (the live build) or "clean" (poll.js picks it)
const viewKey = `latex-live:${hubBase}${file}`;

export const container = $("viewerContainer");
export const eventBus = new EventBus();
export const linkService = new PDFLinkService({ eventBus, externalLinkTarget: LinkTarget.BLANK });
const findController = new PDFFindController({ eventBus, linkService });
export const viewer = new PDFViewer({ container, eventBus, linkService, findController });
linkService.setViewer(viewer);

// Scroll/zoom state survives PDF reloads, refreshing this tab, and restarting the browser.
let restore = JSON.parse(localStorage.getItem(viewKey) || "null");
const saveView = () => localStorage.setItem(viewKey, JSON.stringify(
  { scale: viewer.currentScaleValue, top: container.scrollTop, left: container.scrollLeft }));
container.addEventListener("scroll", saveView, { passive: true });

export let pagesReady = false;
let pagesLoaded = false; // (every page view has its page: a rebuild can move them to the next document, swapDocument)
eventBus.on("pagesinit", () => {
  viewer.currentScaleValue = restore?.scale ?? "page-width";
  if (restore) { container.scrollTop = restore.top; container.scrollLeft = restore.left; }
  pagesReady = true;
});
eventBus.on("pagesloaded", () => { pagesLoaded = true; });
// Before the next document replaces the one on screen: its view, to restore, and its pages not ready.
export function leaveDocument() {
  if (doc) restore = { scale: viewer.currentScaleValue, top: container.scrollTop, left: container.scrollLeft };
  pagesReady = pagesLoaded = false;
}
eventBus.on("pagechanging", (e) => { $("page").value = e.pageNumber; });
eventBus.on("scalechanging", (e) => { showZoom(e); saveView(); });

// The zoom menu shows the fit in use with its percentage, a listed percentage, or the zoom as it is.
const ZOOM_FITS = { "page-width": "Fit width", "page-fit": "Fit page" };
function showZoom({ scale, presetValue }) {
  const options = [...$("zoomPick").options], pct = `${Math.round(scale * 100)}%`;
  for (const o of options) if (ZOOM_FITS[o.value]) o.textContent = ZOOM_FITS[o.value];
  const shown = options.find((o) => ZOOM_FITS[o.value] && o.value === presetValue) || options.find((o) => Math.abs(o.value - scale) < 1e-3);
  if (shown && ZOOM_FITS[shown.value]) shown.textContent = `${ZOOM_FITS[shown.value]} (${pct})`;
  options.at(-1).textContent = pct;
  $("zoomPick").value = shown ? shown.value : "custom";
}

export const refit = () => {
  const v = viewer.currentScaleValue;
  if (isNaN(+v)) viewer.currentScaleValue = v; // re-fit named scales to the new size
};

$("prev").onclick = () => { viewer.currentPageNumber = Math.max(1, viewer.currentPageNumber - 1); };
$("next").onclick = () => { viewer.currentPageNumber = Math.min(viewer.pagesCount, viewer.currentPageNumber + 1); };
$("page").onchange = (e) => { viewer.currentPageNumber = Math.min(viewer.pagesCount, Math.max(1, +e.target.value || 1)); };
$("zoomIn").onclick = () => viewer.increaseScale();
$("zoomOut").onclick = () => viewer.decreaseScale();
$("zoomPick").onchange = (e) => { viewer.currentScaleValue = e.target.value; e.target.blur(); };
window.addEventListener("resize", refit);

// Trackpad pinch (Chromium reports it as ctrl+wheel) and ctrl/cmd+scroll zoom the PDF around the
// cursor instead of zooming the whole page. updateScale rounds to 0.01, so accumulate small
// pinch steps until they change the scale (as PDF.js's own viewer does).
let pinchFactor = 1;
container.addEventListener("wheel", (e) => {
  if (!(e.ctrlKey || e.metaKey)) return;
  e.preventDefault();
  const px = e.deltaMode === WheelEvent.DOM_DELTA_LINE ? e.deltaY * 30 : e.deltaY;
  pinchFactor *= Math.exp(-Math.max(-25, Math.min(25, px)) / 100); // clamp mouse-wheel notches
  const current = viewer.currentScale;
  const next = Math.round(current * pinchFactor * 100) / 100;
  if (next === current) return;
  viewer.updateScale({ scaleFactor: next / current, drawingDelay: 400, origin: [e.clientX, e.clientY] });
  // Keep the rounding remainder, unless the scale hit its min/max limit.
  pinchFactor = viewer.currentScale === next ? (current * pinchFactor) / next : 1;
}, { passive: false });

export const assets = {
  // Absolute URLs: the worker would resolve relative ones against pdfjs/build/.
  cMapUrl: new URL("pdfjs/cmaps/", location.href).href,
  cMapPacked: true,
  standardFontDataUrl: new URL("pdfjs/standard_fonts/", location.href).href,
  get isEvalSupported() { return !me.shared; }, // (in shared mode, no code compiled from a PDF that coauthors' TeX made)
};
export let doc = null; // the document on screen
export let docSeq = 0;
// The next document on screen (variant: "notes" or "clean"), each with a number of its own (docSeq).
export function showDocument(next, variant) {
  viewer.setDocument(next);
  linkService.setDocument(next);
  doc?.destroy();
  doc = next;
  shownVariant = variant;
  docSeq++;
}

// A rebuild of the document on screen, shown in place: the viewer keeps its pages, which move to the new document,
// and each drawn page keeps its old drawing (and the marks and bubbles over it) until the new one is drawn, so nothing
// blanks and the scroll and zoom stay as they are. Only where every page is set up and keeps its size, and the page
// count is the same; false otherwise (or on any error), and the caller shows the document anew (showDocument).
// (This moves PDF.js's page views to another document as its own zoom redraws them: pdf_viewer.mjs, 4.10.)
const DRAWN = 3; // (PDF.js's RenderingStates.FINISHED)
const KEPT = ":scope > .ll-marks, :scope > .ll-margin, :scope > .sync-flash"; // (redrawn on "pagerendered", or gone soon)
export async function swapDocument(next) {
  if (!doc || !pagesReady || !pagesLoaded || next.numPages !== doc.numPages || viewer.pagesCount !== next.numPages) return false;
  const views = Array.from({ length: viewer.pagesCount }, (_, i) => viewer.getPageView(i));
  if (views.some((v) => !v?.pdfPage)) return false;
  let pages;
  try {
    pages = await Promise.all(views.map((v) => next.getPage(v.id)));
  } catch (e) {
    console.warn(e);
    return false;
  }
  const sameBox = (a, b) => a.rotate === b.rotate && a.view.every((v, i) => v === b.view[i]);
  if (next.numPages !== viewer.pagesCount || views.some((v, i) => v !== viewer.getPageView(i) || !sameBox(v.pdfPage, pages[i]))) return false;
  // From here on, nothing waits: no page may draw from one document while the viewer has the other.
  try {
    moveTo(next, views, pages);
  } catch (e) { // (showDocument sets the viewer up anew)
    console.warn(e);
    return false;
  }
  doc.destroy();
  doc = next;
  docSeq++;
  return true;
}
function moveTo(next, views, pages) {
  const contents = next.getOptionalContentConfig({ intent: "display" });
  viewer.pdfDocument = next; // (where the viewer gets pages, and the layers their links and storage)
  viewer._optionalContentConfigPromise = contents;
  linkService.setDocument(next);
  findController.setDocument(next); // (a search starts over, from the page on screen)
  views.forEach((v, i) => {
    v._optionalContentConfigPromise = contents;
    if (v.renderingState !== DRAWN) return v.setPdfPage(pages[i]); // (nothing drawn to keep: drawn anew when shown)
    const kept = [...v.div.querySelectorAll(KEPT)];
    v.pdfPage = pages[i];
    v.viewport = pages[i].getViewport({ scale: v.viewport.scale, rotation: v.viewport.rotation });
    v.reset({ keepCanvasWrapper: true }); // (the new canvas replaces the old one once it is drawn)
    v.div.append(...kept);
  });
  viewer.update(); // (draws the pages in view)
}
