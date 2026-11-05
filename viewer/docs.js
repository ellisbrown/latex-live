// The document menu.

import { $, docFile, el, hubBase, isDiff, setStatus } from "./page.js";
import { postJSON } from "./net.js";
import { me } from "./role.js";
import { ed, flushSource } from "./editor.js";

// Main document: the paper's root .tex files. Picking one makes the server build it instead, and
// every open viewer follows (poll), once the source pane has saved.
let leaving = false;
export async function openDoc(pdf) {
  if (leaving) return;
  leaving = true;
  if (!(await flushSource())) { // (edits the pane can't save, e.g. an unsettled conflict: the page stays, and asks again soon)
    setStatus(`${ed.path} is not saved yet${ed.markers ? " (settle its conflicts first)" : ""}, so this page stays on ` +
              `${docFile.slice(0, -4)}.tex, though ${pdf.slice(0, -4)}.tex is the one built now`, "error");
    setTimeout(() => (leaving = false), 3000);
    return;
  }
  setTimeout(() => (leaving = false), 30000); // (should the page stay anyway)
  const dir = hubBase ? `${hubBase}${encodeURIComponent(pdf.slice(0, -4))}/` : ""; // (under the hub, the document's own path)
  location.href = `${dir}viewer.html?${new URLSearchParams({ file: pdf })}`;
}
// The document menu: this paper's root files (switching what this instance builds), the other
// papers that latex-live instances on this machine preview (their viewers: through the hub, else
// on their own ports), and a page listing them all.
let otherPreviews = [];
function viewerOf(i) {
  const query = new URLSearchParams({ file: `${i.doc}.pdf` });
  return hubBase && i.id ? `/p/${encodeURIComponent(i.id)}/${encodeURIComponent(i.doc)}/viewer.html?${query}`
                         : `${location.protocol}//${location.hostname}:${i.port}/viewer.html?${query}`;
}
export async function loadDocs() {
  const { doc: building, docs } = await (await fetch("docs", { signal: AbortSignal.timeout(10000) })).json();
  const { self, instances } = await (await fetch("instances", { signal: AbortSignal.timeout(10000) })).json().catch(() => ({ instances: [] }));
  const others = otherPreviews = instances.filter((i) => i.pid !== self);
  const pick = $("docPick");
  pick.replaceChildren(...docs.map((d) => el("option", { value: d, textContent: `${d}.tex`, selected: `${d}.pdf` === docFile })));
  if (others.length) {
    const group = el("optgroup", { label: "Other previews" });
    group.append(...others.map((i, n) => el("option", { value: `open:${n}`, title: i.root,
                                                     textContent: `${i.root.split("/").pop()}: ${i.doc}.tex` })));
    pick.append(group, el("option", { value: "papers", textContent: "All previews…" }));
  }
  pick.hidden = isDiff || docs.length + others.length < 2 || `${building}.pdf` !== docFile;
}
$("docPick").onchange = async (e) => {
  const value = e.target.value;
  e.target.value = docFile.slice(0, -4);
  if (value === "papers") return window.open(hubBase ? "/papers" : "papers", "latex-live-papers");
  if (!(await flushSource())) { // (the source pane saves first; with edits it can't save, nothing switches)
    return setStatus(`${ed.path} is not saved yet${ed.markers ? " (settle its conflicts first)" : ""}, so nothing switched`, "error");
  }
  if (value.startsWith("open:")) return (location.href = viewerOf(otherPreviews[+value.slice(5)]));
  setStatus(`switching to ${value}.tex…`, "busy");
  const r = me.legacy ? await fetch(`switch?${new URLSearchParams({ doc: value })}`) : await postJSON("switch", { doc: value });
  const body = await r.json().catch(() => ({ error: `switch failed (${r.status})` }));
  if (r.ok) return openDoc(`${value}.pdf`);
  if (body.port) { // (another instance builds it: its viewer, through the hub unless it predates it)
    const other = otherPreviews.find((i) => i.port === body.port);
    const id = other ? other.id : hubBase && decodeURIComponent(hubBase.split("/")[2]); // (not listed yet: the same paper, so this one's id)
    return (location.href = viewerOf({ port: body.port, doc: value, id }));
  }
  setStatus(body.error, "error");
};
