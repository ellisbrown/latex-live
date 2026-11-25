// How the window is shared out: the sidebar (open or not, its tab and its width), the layout (the source alone, the
// source beside the PDF, or the PDF alone) and the source pane's width, and the gutters for the margin bubbles ("Fit
// width" fits the page in what they leave), with the toggles remembered across sessions. And the channel between the
// windows of a document: a pop-out window (?mode=pdf) shows the PDF alone, beside the main window with the source.

import { $, docFile, editorShown, HOLD, hubBase, isDiff, isPopout, setStatus } from "./page.js";
import { container, refit, viewer } from "./pdfview.js";

// Toggles remembered across sessions: the sidebar, say. A pop-out window leaves the main window's settings alone.
function setToggle(name, on) {
  document.body.classList.toggle(name, on);
  if (!isPopout) localStorage.setItem(`latex-live:${name}`, on ? "1" : "");
}

// The sidebar's tab (a comparison has no notes, checks or figures: sidebar.js shows the outline then).
export let tab = localStorage.getItem("latex-live:tab") || "outline";
export function setTab(name) {
  tab = name;
  if (!isPopout && !isDiff) localStorage.setItem("latex-live:tab", name); // (nor does a comparison, which has fewer tabs)
}
export const sideOpen = (name) => !$("side").hidden && tab === name;

// The sidebar's width: as dragged last, between its least and most, and leaving the rest of the window some room.
let sideWidth = +localStorage.getItem("latex-live:sideWidth") || 276;
const SIDE_MIN = 210, SIDE_MAX = 440;
function fitSide() {
  const most = Math.max(SIDE_MIN, Math.min(SIDE_MAX, innerWidth - 420));
  const width = Math.round(Math.max(SIDE_MIN, Math.min(most, sideWidth)));
  document.documentElement.style.setProperty("--side-w", `${width}px`);
  return width;
}
fitSide();
export function setSide(on) {
  $("side").hidden = !on;
  $("toggleSide").classList.toggle("on", on);
  setToggle("side", on);
  fitEditor(); // (the source beside the PDF leaves the PDF some room)
  fitGutters();
  refit();
}

// The layout: "pdf" (the PDF alone), "split" (the source beside it) or "source" (the source alone, e.g. with the PDF
// in a pop-out window on another screen). The source pane shows in the last two (editor.js showEditor); alone, it
// hides the PDF, which stays laid out (viewer.css), so that its place survives the rebuilds meanwhile. A browser from
// before the layouts remembered only whether the pane was open.
const LAYOUTS = ["pdf", "split", "source"];
export let layoutMode = isPopout ? "pdf" : localStorage.getItem("latex-live:layout") || (localStorage.getItem("latex-live:editor") ? "split" : "pdf");
if (!LAYOUTS.includes(layoutMode)) layoutMode = "pdf";
export function setLayoutMode(mode, remember = true) {
  if (!LAYOUTS.includes(mode)) mode = "pdf";
  const besidePdf = mode === "split" && layoutMode !== "split"; // (the pane comes beside the PDF: make room for it)
  layoutMode = mode;
  if (remember && !isPopout) localStorage.setItem("latex-live:layout", mode);
  for (const m of ["pdf", "split", "source"]) document.body.classList.toggle(`layout-${m}`, m === mode);
  for (const b of $("layoutSwitch").querySelectorAll("button")) b.classList.toggle("on", b.dataset.mode === mode);
  fitEditor(besidePdf);
  fitGutters();
  refit();
}

// The pane's width beside the PDF: as dragged last (else about half of what the sidebar leaves), but leaving the PDF
// readable beside it: room for the gutters the margin bubbles ask for, and about 420 px for the page, which "Fit width"
// then shows at half its size or more. Where opening the pane leaves it less than its least width even without the
// gutters (which fold where there is no room for them: fitGutters), the sidebar makes way, and the status line says so.
// Alone (Source), the pane takes the whole width.
let editorWidth = +localStorage.getItem("latex-live:editorWidth") || 0;
const PDF_ROOM = 420, EDITOR_MIN = 300, FRAME = 18; // (FRAME: the workspace's padding and the source pane's grip)
function fitEditor(opening = false) {
  if (layoutMode === "source") return editorWidth;
  const gutters = GUTTER * (+wantGutter.right + +wantGutter.left); // (asked for, not shown: so no loop with fitGutters)
  const side = () => ($("side").hidden ? 0 : fitSide() + 6);
  const room = (withGutters = true) => innerWidth - FRAME - side() - (withGutters ? gutters : 0) - PDF_ROOM;
  if (opening && room(false) < EDITOR_MIN && !$("side").hidden) {
    setSide(false);
    setStatus("Closed the sidebar to make room for the source beside the PDF", "", HOLD);
  }
  // (a width dragged to may take the gutters' room: they fold then)
  const width = Math.round(Math.max(EDITOR_MIN, Math.min(room(!editorWidth), editorWidth || (innerWidth - side()) * 0.48)));
  document.documentElement.style.setProperty("--editor-w", `${width}px`);
  return width;
}

// A grip right of a pane resizes it: start() takes its width as the drag starts, move(dx) how far the pointer went
// since, and done() the end.
function grip(node, start, move, done) {
  node.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    start();
    node.setPointerCapture(e.pointerId);
    document.body.classList.add("resizing");
    let frame = 0;
    const onMove = (ev) => {
      move(ev.clientX - e.clientX);
      frame ||= requestAnimationFrame(() => { frame = 0; refit(); });
    };
    node.addEventListener("pointermove", onMove);
    node.addEventListener("lostpointercapture", () => {
      node.removeEventListener("pointermove", onMove);
      document.body.classList.remove("resizing");
      done();
      refit();
    }, { once: true });
  });
}
let dragFrom = 0;
grip($("sideGrip"), () => (dragFrom = $("side").offsetWidth), (dx) => {
  sideWidth = dragFrom + dx;
  fitSide();
  if (editorShown()) fitEditor();
  fitGutters();
}, () => {
  sideWidth = fitSide();
  if (!isPopout) localStorage.setItem("latex-live:sideWidth", sideWidth);
  if (editorShown()) fitEditor();
  fitGutters();
});
grip($("editorGrip"), () => (dragFrom = $("editorPane").offsetWidth), (dx) => {
  editorWidth = dragFrom + dx;
  fitEditor();
  fitGutters();
}, () => {
  editorWidth = fitEditor();
  localStorage.setItem("latex-live:editorWidth", editorWidth);
  fitGutters();
});
window.addEventListener("resize", () => {
  fitSide();
  if (editorShown()) fitEditor();
  fitGutters();
  if (editorShown() || !$("side").hidden) refit(); // (after the panes' widths follow the window's)
});

// The width of a gutter for the margin bubbles (notes.js), beside the page.
export const GUTTER = 250;

// Fitting the page width leaves room for the gutters: PDF.js measures the container's clientWidth. Never less than a
// narrow page, though: in a pane narrower than its gutters, a fit to nothing would be no zoom at all. Which pages are
// in view, PDF.js reckons with the whole width (else, with a gutter left of the page, none would seem to be).
const clientWidthOf = Object.getOwnPropertyDescriptor(Element.prototype, "clientWidth").get;
let wholeWidth = false;
Object.defineProperty(container, "clientWidth", { get() {
  if (wholeWidth) return clientWidthOf.call(this);
  const gutters = (this.classList.contains("gutter") ? GUTTER : 0) + (this.classList.contains("gutter-left") ? GUTTER : 0);
  return Math.max(120, clientWidthOf.call(this) - gutters);
} });
const visiblePages = viewer._getVisiblePages;
viewer._getVisiblePages = function () {
  wholeWidth = true;
  try { return visiblePages.call(this); } finally { wholeWidth = false; }
};

// The gutters the notes ask for (setGutter: right of the page, and left of it for the notes of a left column), and
// those shown (gutters): where the PDF's pane leaves the page less than GUTTER_ROOM beside them, a left column's notes
// go to the right gutter too, and where there isn't room for that either, there are none, and the notes show only
// their carets (notes.js redraws them: onGutters). Asked again whenever the pane's width may change.
const GUTTER_ROOM = 360;
let wantGutter = { right: false, left: false }, guttersChanged = () => {};
export let gutters = { right: false, left: false };
export const onGutters = (fn) => { guttersChanged = fn; };
export function setGutter(on, left = false) {
  wantGutter = { right: on, left };
  if (editorShown()) fitEditor(); // (the source pane leaves the gutters room too, where it can)
  fitGutters();
}
function fitGutters() {
  const width = clientWidthOf.call(container), asked = +wantGutter.right + +wantGutter.left;
  const room = (n) => width - n * GUTTER >= GUTTER_ROOM;
  const next = asked && room(asked) ? { ...wantGutter } : asked && room(1) ? { right: true, left: false } : { right: false, left: false };
  if (next.right === gutters.right && next.left === gutters.left) return;
  gutters = next;
  container.classList.toggle("gutter", next.right);
  container.classList.toggle("gutter-left", next.left);
  refit();
  guttersChanged();
}

// The windows showing this document (not those of another document, or of another paper under the hub) talk over a
// channel of their own: a pop-out follows the main window's source, and the main window opens the source lines
// double-clicked in a pop-out (links.js). A window answers a message that asks for something (askWindows) once it
// takes it on; whoever asked then knows that some window did.
const channel = !isDiff && typeof BroadcastChannel !== "undefined" ? new BroadcastChannel(`latex-live:${hubBase}${docFile}`) : null;
const handlers = new Map(), asked = new Map();
const windowId = Math.random().toString(36).slice(2);
let asks = 0;
// What a message of a kind does here: fn(message) returns whether this window takes it on.
export const onWindowMessage = (kind, fn) => handlers.set(kind, fn);
export function tellWindows(message) {
  try { channel?.postMessage(message); } catch {} // (a closed channel: the page is going away)
}
// Ask the other windows: whether one of them took it on within wait ms.
export function askWindows(message, wait = 400) {
  if (!channel) return Promise.resolve(false);
  const id = `${windowId}:${++asks}`;
  return new Promise((resolve) => {
    const timer = setTimeout(() => { asked.delete(id); resolve(false); }, wait);
    asked.set(id, () => { clearTimeout(timer); asked.delete(id); resolve(true); });
    tellWindows({ ...message, id });
  });
}
if (channel) channel.onmessage = ({ data: message }) => {
  if (!message || typeof message !== "object") return;
  if (message.kind === "taken") return asked.get(message.id)?.();
  if (handlers.get(message.kind)?.(message) && message.id) tellWindows({ kind: "taken", id: message.id });
};
