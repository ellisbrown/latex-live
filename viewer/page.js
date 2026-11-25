// The page itself: its elements ($, el), the PDF it shows (?file=: a document, or a comparison of it: isDiff), and the
// status line. Every other module may use this one.

import { icon } from "./icons.js";

export const $ = (id) => document.getElementById(id);
// Under the hub (one port for every preview): "/p/<paper id>/". Settings tied to a paper are kept
// per paper then, since every paper shares the hub's origin.
export const hubBase = location.pathname.match(/^\/p\/[^/]+\//)?.[0] || "";
// Someone new to latex-live in this browser: nothing remembered yet (read before anything is written).
export const firstVisit = !Array.from({ length: localStorage.length }, (_, i) => localStorage.key(i)).some((k) => k?.startsWith("latex-live:"));
export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
export const IS_MAC = /^Mac|^iP/.test(navigator.platform); // (Cmd there, Ctrl elsewhere)
export const plural = (n, word, many = `${word}s`) => `${n} ${n === 1 ? word : many}`;
export const el = (tag, { dataset, ...props } = {}, ...children) => {
  const e = Object.assign(document.createElement(tag), props);
  Object.assign(e.dataset, dataset); // (dataset is read-only: its entries are set one by one)
  e.append(...children);
  return e;
};

// What the status line says when the server can't be asked: it refused (403: the access ended), a
// page came back instead of an answer (a proxy's sign-in page), or nothing came back (offline, or the
// server, proxy or hub can't reach the preview just now).
export const ACCESS_ENDED = "Access ended — see the message";
export const SIGNED_OUT = "Your sign-in may have expired — reload the page";
export const UNREACHABLE = "Can't reach the preview server — retrying…";
export const file = new URLSearchParams(location.search).get("file");
export const isDiff = file.endsWith("-diff.pdf");
// A pop-out window (?mode=pdf): the PDF alone, e.g. on a second screen, beside a main window with the source.
export const isPopout = new URLSearchParams(location.search).get("mode") === "pdf";
export const docFile = file.replace(/-(clean|diff)\.pdf$/, ".pdf"); // the document a derived PDF belongs to
export const cleanFile = docFile.replace(/\.pdf$/, "-clean.pdf");

document.title = isDiff ? `diff: ${docFile}` : file;

// The downloads that the server is building for this tab (downloadBuilt): the status line is theirs meanwhile.
export const building = new Set();

// The live build's state on the status line (poll, load), unless a download that the server builds is in
// progress: that has the line meanwhile (downloadBuilt), rather than the two taking turns. Nor does it replace a
// message held on the line (an error, say): it shows once the hold ends.
let held = null, lastLive = null; // ({timer, live: the live state that came meanwhile}) while a message holds the line
export function liveStatus(text, cls = "", title = text) {
  lastLive = [text, cls, title];
  if (held) return void (held.live = lastLive);
  if (building.size) return;
  showStatus(text, cls);
  $("status").title = title;
}
// A message on the status line. An error stays there for a while (HOLD ms, longer for a long one; another message
// may hold the line too), so that the next build's state doesn't replace it within a second; a click on it ends the
// hold. Where the line cuts a held message short, a note under the top bar shows it whole meanwhile.
export const HOLD = 8000;
export function setStatus(text, cls = "", hold = cls === "error" ? HOLD : 0) {
  if (held) { clearTimeout(held.timer); held = null; }
  showStatus(text, cls);
  if (!hold || !text) return;
  const ms = hold + 40 * text.length;
  held = { timer: setTimeout(() => release(false), ms), live: null };
  const line = $("status");
  if (line.scrollWidth > line.clientWidth + 1) showWhole(text, cls, line);
}
function showStatus(text, cls) {
  $("status").textContent = $("status").title = text; // (the title: the whole text when it is cut off)
  $("status").className = cls;
  whole?.remove();
  whole = null;
}
// The hold's end: the live state that came meanwhile, if any; dismissed, the last one (an error left on its own stays
// until the next one, as before holds).
function release(dismissed = true) {
  const live = held?.live || (dismissed ? lastLive || ["", ""] : null);
  clearTimeout(held?.timer); // (dismissed before its time: the timer would end a later message's hold)
  held = null;
  whole?.remove();
  whole = null;
  if (live) liveStatus(...live);
}
let whole = null; // (the note with a held message whole)
function showWhole(text, cls, line) {
  const box = line.getBoundingClientRect();
  whole = el("div", { className: `status-whole ${cls}`, role: "status", textContent: text, title: "Close", onclick: () => release() });
  whole.style.top = `${box.bottom + 6}px`;
  whole.style.right = `${Math.max(8, innerWidth - box.right)}px`;
  document.body.append(whole);
}
$("status").addEventListener("click", () => { if (held) release(); });

// A panel's part that didn't load: why, and a button that asks again.
export const panelError = (text, retry) => el("div", { className: "empty panel-error" }, el("div", { textContent: text }),
                                              el("button", { textContent: "Try again", onclick: retry }));
// A panel's refresh button, for the end of its top bar (sidebar.js): an icon, the same in every panel.
export const refreshButton = (title, run) => el("button", { className: "panel-refresh", title, ariaLabel: title, onclick: () => run() },
                                                icon("rebuild", 12));

export const editorShown = () => !$("editorPane").hidden; // (the source pane: editor.js)

// The keyboard. A dialog keeps Tab and Shift+Tab within it while it is open (keys.js: the welcome guide, the shortcuts,
// the palette).
export function keepFocusIn(box, e) {
  const stops = [...box.querySelectorAll("button, input, select, textarea, a[href], [tabindex]:not([tabindex='-1'])")]
    .filter((n) => !n.disabled && n.tabIndex >= 0 && n.getClientRects().length);
  const i = stops.indexOf(document.activeElement);
  if (i >= 0 && !(e.shiftKey ? i === 0 : i === stops.length - 1)) return; // (the browser moves on within it)
  e.preventDefault(); // (from its last stop, its first; from its first, back to its last; from outside it, in)
  stops.at(e.shiftKey ? -1 : 0)?.focus();
}
// Something that isn't a button but acts on a click (a row of a list): it takes the focus, and Enter or Space on it
// (not on a button inside it) does what its click does.
export function activatable(node, run) {
  node.tabIndex = 0;
  node.addEventListener("keydown", (e) => {
    if (e.target !== node || (e.key !== "Enter" && e.key !== " ") || e.repeat) return;
    e.preventDefault(); // (Space would scroll)
    run(e);
  });
  return node;
}
// A strip of tabs (role=tablist, its tabs role=tab with aria-selected): one stop in the Tab order, the selected tab's
// (rovingTabs, again whenever the selection or the tabs shown change), and the arrow keys, Home and End move the focus
// along it. Enter or Space then opens the tab focused (a button's own click; activatable for another element).
export function rovingTabs(list) {
  const shown = [...list.querySelectorAll("[role=tab]")].filter((t) => !t.hidden);
  const stop = shown.find((t) => t.getAttribute("aria-selected") === "true") || shown[0];
  for (const t of shown) t.tabIndex = t === stop ? 0 : -1;
}
export function tabKeys(list) {
  list.addEventListener("keydown", (e) => {
    const shown = [...list.querySelectorAll("[role=tab]")].filter((t) => !t.hidden), i = shown.indexOf(e.target);
    const to = i < 0 ? -1 : { ArrowLeft: i - 1, ArrowRight: i + 1, Home: 0, End: shown.length - 1 }[e.key];
    if (to === undefined || i < 0 || e.altKey || e.ctrlKey || e.metaKey) return;
    e.preventDefault();
    const next = shown[(to + shown.length) % shown.length];
    for (const t of shown) t.tabIndex = t === next ? 0 : -1;
    next.focus();
  });
}
