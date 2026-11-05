// The page itself: its elements ($, el), the PDF it shows (?file=: a document, or a comparison of it: isDiff), and the
// status line. Every other module may use this one.

export const $ = (id) => document.getElementById(id);
// Under the hub (one port for every preview): "/p/<paper id>/". Settings tied to a paper are kept
// per paper then, since every paper shares the hub's origin.
export const hubBase = location.pathname.match(/^\/p\/[^/]+\//)?.[0] || "";
// Someone new to latex-live in this browser: nothing remembered yet (read before anything is written).
export const firstVisit = !Array.from({ length: localStorage.length }, (_, i) => localStorage.key(i)).some((k) => k?.startsWith("latex-live:"));
export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
export const el = (tag, props = {}, ...children) => { const e = Object.assign(document.createElement(tag), props); e.append(...children); return e; };

// What the status line says when the server can't be asked: it refused (403: the access ended), a
// page came back instead of an answer (a proxy's sign-in page), or nothing came back (offline, or the
// server, proxy or hub can't reach the preview just now).
export const ACCESS_ENDED = "Access ended — see the message";
export const SIGNED_OUT = "Your sign-in may have expired — reload the page";
export const UNREACHABLE = "Can't reach the preview server — retrying…";
export const file = new URLSearchParams(location.search).get("file");
export const isDiff = file.endsWith("-diff.pdf");
export const docFile = file.replace(/-(clean|diff)\.pdf$/, ".pdf"); // the document a derived PDF belongs to
export const cleanFile = docFile.replace(/\.pdf$/, "-clean.pdf");

document.title = isDiff ? `diff: ${docFile}` : file;

// The downloads that the server is building for this tab (downloadBuilt): the status line is theirs meanwhile.
export const building = new Set();

// The live build's state on the status line (poll, load), unless a download that the server builds is in
// progress: that has the line meanwhile (downloadBuilt), rather than the two taking turns.
export function liveStatus(text, cls = "", title = text) {
  if (building.size) return;
  setStatus(text, cls);
  $("status").title = title;
}
export function setStatus(text, cls = "") {
  $("status").textContent = $("status").title = text; // (the title: the whole text when it is cut off)
  $("status").className = cls;
}

export const editorShown = () => !$("editorPane").hidden; // (the source pane: editor.js)
