// 3-pane card workspace layout: [Sidebar | Editor | PDF], resizable splitters for sidebar and
// editor, layout modes ("pdf" | "split" | "source"), pop-out PDF window sync (?mode=pdf), and
// margin-bubble gutter measurement for PDF.js "Fit width".

import { $, editorShown, hubBase, isDiff, setStatus } from "./page.js";
import { container, refit } from "./pdfview.js";

export const isPopout = new URLSearchParams(location.search).get("mode") === "pdf";

// Toggles remembered across sessions.
export function setToggle(name, on) {
  document.body.classList.toggle(name, on);
  localStorage.setItem(`latex-live:${name}`, on ? "1" : "");
}

// Sidebar tab (remembered across sessions; comparisons hide unsupported tabs).
export let tab = localStorage.getItem("latex-live:tab") || "outline";
if (isDiff && ["files", "notes", "checks", "figures", "history", "papers"].includes(tab)) tab = "outline";

let sideWidth = +localStorage.getItem("latex-live:sideWidth") || 276;
const SIDE_MIN = 210, SIDE_MAX = 440;

export function applySideWidth() {
  const maxW = Math.min(SIDE_MAX, Math.max(SIDE_MIN, innerWidth - 420));
  const w = Math.round(Math.max(SIDE_MIN, Math.min(maxW, sideWidth)));
  document.documentElement.style.setProperty("--side-w", `${w}px`);
  return w;
}
applySideWidth();

export function setSide(on) {
  $("side").hidden = !on;
  $("toggleSide").classList.toggle("on", on);
  setToggle("side", on);
  fitEditor();
  refit();
}

export function setTab(name) {
  tab = name;
  localStorage.setItem("latex-live:tab", name);
}

export const sideOpen = (name) => !$("side").hidden && tab === name;

// Workspace layout mode: "pdf" (PDF only), "split" (Editor + PDF), "source" (Editor only).
export let layoutMode = isPopout ? "pdf" : (localStorage.getItem("latex-live:layout") || "pdf");
export function setLayoutMode(mode) {
  if (!["pdf", "split", "source"].includes(mode)) mode = "pdf";
  layoutMode = mode;
  if (!isPopout) localStorage.setItem("latex-live:layout", mode);
  document.body.classList.toggle("layout-source", mode === "source");
  for (const b of document.querySelectorAll("#layoutSwitch button")) {
    b.classList.toggle("on", b.dataset.mode === mode);
  }
  if (mode !== "source") refit();
}

// Editor pane width (Center pane between Sidebar and PDF): draggable from its right edge.
let editorWidth = +localStorage.getItem("latex-live:editorWidth") || 0;
const PDF_ROOM = 420, EDITOR_MIN = 300;
export function fitEditor(opening = false) {
  if (layoutMode === "source") return innerWidth;
  const sideW = $("side").hidden ? 0 : (applySideWidth() + 6);
  const gutters = GUTTER * (+container.classList.contains("gutter") + +container.classList.contains("gutter-left"));
  const avail = () => innerWidth - 18 - sideW - gutters - PDF_ROOM;
  if (opening && avail() < EDITOR_MIN && !$("side").hidden) {
    setSide(false);
    setStatus("Closed the sidebar to make room for the source");
  }
  const roomNow = innerWidth - 18 - ($("side").hidden ? 0 : applySideWidth() + 6) - gutters - PDF_ROOM;
  const target = editorWidth || Math.round((innerWidth - ($("side").hidden ? 0 : applySideWidth())) * 0.48);
  const width = Math.round(Math.max(EDITOR_MIN, Math.min(Math.max(EDITOR_MIN, roomNow), target)));
  document.documentElement.style.setProperty("--editor-w", `${width}px`);
  return width;
}

// Drag right edge of #side to resize sidebar.
$("sideGrip").addEventListener("pointerdown", (e) => {
  if (e.button !== 0) return;
  e.preventDefault();
  const grip = e.currentTarget;
  grip.setPointerCapture(e.pointerId);
  document.body.classList.add("resizing-side");
  let frame = 0;
  const move = (ev) => {
    sideWidth = ev.clientX - 6;
    applySideWidth();
    if (editorShown()) fitEditor();
    frame ||= requestAnimationFrame(() => { frame = 0; refit(); });
  };
  grip.addEventListener("pointermove", move);
  grip.addEventListener("lostpointercapture", () => {
    grip.removeEventListener("pointermove", move);
    document.body.classList.remove("resizing-side");
    sideWidth = applySideWidth();
    localStorage.setItem("latex-live:sideWidth", sideWidth);
    if (editorShown()) fitEditor();
    refit();
  }, { once: true });
});

// Drag right edge of #editorPane to resize the split between Editor and PDF.
$("editorGrip").addEventListener("pointerdown", (e) => {
  if (e.button !== 0 || layoutMode === "source") return;
  e.preventDefault();
  const grip = e.currentTarget;
  grip.setPointerCapture(e.pointerId);
  document.body.classList.add("resizing");
  let frame = 0;
  const leftEdge = () => $("editorPane").getBoundingClientRect().left;
  const move = (ev) => {
    editorWidth = ev.clientX - leftEdge();
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

window.addEventListener("resize", () => {
  if (!$("side").hidden) applySideWidth();
  if (editorShown()) { fitEditor(); refit(); }
});

// Margin bubble gutter width beside the PDF page.
export const GUTTER = 250;

const clientWidthOf = Object.getOwnPropertyDescriptor(Element.prototype, "clientWidth").get;
Object.defineProperty(container, "clientWidth", {
  get() {
    return clientWidthOf.call(this)
      - (this.classList.contains("gutter") ? GUTTER : 0)
      - (this.classList.contains("gutter-left") ? GUTTER : 0);
  },
});

export function setGutter(on, left = false) {
  if (container.classList.contains("gutter") === on && container.classList.contains("gutter-left") === left) return;
  container.classList.toggle("gutter", on);
  container.classList.toggle("gutter-left", left);
  if (editorShown()) fitEditor();
  refit();
}

// Synchronized pop-out PDF BroadcastChannel (Editor window <-> second-screen PDF window).
export const syncChannel = typeof BroadcastChannel !== "undefined"
  ? new BroadcastChannel(`latex-live:${hubBase || location.pathname}`) : null;

export function broadcastSync(msg) {
  try { syncChannel?.postMessage(msg); } catch {}
}
