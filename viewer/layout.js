// How the window is shared out: the sidebar (open or not, and its tab), the source pane's width, and the gutters for the
// margin bubbles ("Fit width" fits the page in what they leave), with the toggles remembered across sessions.

import { $, editorShown, isDiff, setStatus } from "./page.js";
import { container, refit } from "./pdfview.js";

// Toggles remembered across sessions: dark mode, the sidebar and the source pane. The sidebar's tab is remembered too.
export function setToggle(name, on) {
  document.body.classList.toggle(name, on);
  localStorage.setItem(`latex-live:${name}`, on ? "1" : "");
}

// The sidebar's tab (a comparison has no notes, checks or figures).
export let tab = localStorage.getItem("latex-live:tab") || "outline";
if (isDiff && (tab === "notes" || tab === "checks" || tab === "figures")) tab = "outline";

export function setSide(on) {
  $("side").hidden = !on;
  $("toggleSide").classList.toggle("on", on);
  setToggle("side", on);
  fitEditor(); // (the source beside the PDF leaves the PDF some room)
  refit();
}
export function setTab(name) {
  tab = name;
  localStorage.setItem("latex-live:tab", name);
}

export const sideOpen = (name) => !$("side").hidden && tab === name;

// The pane's width: as dragged last (else about a third of the window), but leaving the PDF readable
// beside it: room for the margin bubbles' gutters as shown, and about 500 px for the page, which "Fit
// width" then shows at half its size or more. Where opening the pane leaves it less than its least
// width, the sidebar makes way (and the status line says so).
let editorWidth = +localStorage.getItem("latex-live:editorWidth") || 0;
const PDF_ROOM = 500, EDITOR_MIN = 280;
export function fitEditor(opening = false) {
  const gutters = GUTTER * (+container.classList.contains("gutter") + +container.classList.contains("gutter-left"));
  const room = () => innerWidth - ($("side").hidden ? 0 : 290) - gutters - PDF_ROOM;
  if (opening && room() < EDITOR_MIN && !$("side").hidden) {
    setSide(false);
    setStatus("closed the sidebar to make room for the source");
  }
  const width = Math.round(Math.max(EDITOR_MIN, Math.min(room(), editorWidth || innerWidth * 0.35)));
  document.body.style.setProperty("--editor-w", `${width}px`);
  return width;
}
$("editorGrip").addEventListener("pointerdown", (e) => {
  if (e.button !== 0) return;
  e.preventDefault();
  const grip = e.currentTarget;
  grip.setPointerCapture(e.pointerId);
  document.body.classList.add("resizing");
  let frame = 0;
  const move = (ev) => {
    editorWidth = innerWidth - ev.clientX;
    fitEditor();
    frame ||= requestAnimationFrame(() => { frame = 0; refit(); });
  };
  grip.addEventListener("pointermove", move);
  grip.addEventListener("lostpointercapture", () => {
    grip.removeEventListener("pointermove", move);
    document.body.classList.remove("resizing");
    editorWidth = fitEditor();
    localStorage.setItem("latex-live:editorWidth", editorWidth);
    refit();
  }, { once: true });
});
window.addEventListener("resize", () => { if (editorShown()) { fitEditor(); refit(); } });

// The width of a gutter for the margin bubbles (notes.js), beside the page.
export const GUTTER = 250;

// Fitting the page width leaves room for the gutters: PDF.js measures the container's clientWidth.
const clientWidthOf = Object.getOwnPropertyDescriptor(Element.prototype, "clientWidth").get;
Object.defineProperty(container, "clientWidth", { get() {
  return clientWidthOf.call(this) - (this.classList.contains("gutter") ? GUTTER : 0) - (this.classList.contains("gutter-left") ? GUTTER : 0);
} });
export function setGutter(on, left = false) {
  if (container.classList.contains("gutter") === on && container.classList.contains("gutter-left") === left) return;
  container.classList.toggle("gutter", on);
  container.classList.toggle("gutter-left", left);
  if (editorShown()) fitEditor(); // (the source pane leaves the gutters room too)
  refit();
}
