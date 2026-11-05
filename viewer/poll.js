// The status poll, and loading each new PDF in place; the notes on/off switch picks which build it shows.

import {
  $, ACCESS_ENDED, cleanFile, docFile, editorShown, file, isDiff, liveStatus, setStatus, SIGNED_OUT, UNREACHABLE,
} from "./page.js";
import { postJSON, readBody, unreachable } from "./net.js";
import { isCollaborator, loadAuthors, me } from "./role.js";
import { assets, leaveDocument, pdfjsLib, showDocument, shownVariant } from "./pdfview.js";
import { resetFind } from "./search.js";
import { rebuildAt, seeBuild, setBuildVersion, setShown, shown } from "./build.js";
import { setGutter } from "./layout.js";
import { area, setArea } from "./pagetext.js";
import { hidePreview } from "./preview.js";
import { marks, showUnresolved } from "./marks.js";
import { ed, sourceEpoch, sourceStatus } from "./editor.js";
import { showForward } from "./links.js";
import { showAccessEnded, showBuild } from "./banner.js";
import { checkPages, cleanShown, measureVariant, measuring, notesShown, renderPages, setResult } from "./pages.js";
import { fillSectionPages, renderOutline } from "./outline.js";
import { loadMargin, loadNotes, marginOn, resetMargin, showMarginButton } from "./notes.js";
import { updateCut } from "./cut.js";
import { baseKind, changesOn, setBaselines, trackDocument, updateChanges, wordsPromise } from "./changes.js";
import { refreshTab } from "./sidebar.js";
import { showDiffInfo } from "./compare.js";
import { openDoc } from "./docs.js";

// Which build to show: "notes" (the live build) or "clean" (the notes-free build, when there is one).
let view = localStorage.getItem("latex-live:view") === "notes" ? "notes" : "clean"; // notes-free unless chosen otherwise

// With/without the inline notes: switches the view to the notes-free build (what would be submitted).
function showNotesButton() {
  const b = $("notesView");
  b.textContent = view === "notes" ? "Notes: on" : "Notes: off";
  b.classList.toggle("on", view === "notes");
  b.title = view === "notes" ? "Showing the inline notes; click to show the notes-free build (what you would submit)"
                             : "Showing the notes-free build; click to show the inline notes";
}
$("notesView").onclick = () => {
  view = view === "notes" ? "clean" : "notes";
  localStorage.setItem("latex-live:view", view);
  showNotesButton();
  poll();
};
showNotesButton();

let loading = false;
let baselineAsked = 0; // (when this tab last asked for a baseline: POST /baseline)
let forwardSeq = null;

async function load(name, version, variant, info) {
  // Fetch and parse the new PDF before swapping, so the old one stays visible meanwhile.
  const next = await pdfjsLib.getDocument({ url: `pdf/${encodeURIComponent(name)}?v=${version}`, ...assets }).promise;
  // Text changes only within one build (the notes-free one differs from the live one everywhere).
  const oldWords = variant === shownVariant && wordsPromise && await wordsPromise.catch(() => null);
  leaveDocument();
  resetMargin();
  if (variant !== "clean") setGutter(false);
  else if (marginOn && !isDiff) setGutter(true, area.columns === 2); // (dropped again if no notes need them)
  hidePreview();
  showDocument(next, variant);
  showMarginButton();
  marks.cut.clear();
  resetFind(); // the find controller starts over with the new document
  $("count").textContent = next.numPages;
  const time = new Date().toLocaleTimeString();
  liveStatus(`✓ ${time.replace(/\s*[AP]M$/i, "")}`, "fresh", `Updated at ${time}`);
  setTimeout(() => $("status").classList.remove("fresh"), 1500);
  trackDocument(next, variant, oldWords);
  wordsPromise.then(showUnresolved).catch((e) => console.warn(e));
  updateChanges();
  renderOutline().catch(() => {});
  if (!isDiff) loadNotes().catch(() => {});
  if (!isDiff) loadAuthors().catch(() => {});
  if (!isDiff && variant === "clean") loadMargin(next).catch((e) => { console.warn(e); setGutter(false); });
  const refs = variant === "clean" ? info.clean.refs : info.refs;
  if (!isDiff) { // (the main text's extent: the page badge, space hints and section lengths)
    checkPages(next, refs || null, info.pageLimit).then((r) => {
      setResult(variant, version, r);
      renderPages(); refreshTab(); fillSectionPages(); updateCut();
    }).catch((e) => console.warn(e));
  } else {
    setResult(variant, version, null);
    renderPages();
    refreshTab();
    updateCut();
  }
}

// One poll at a time, and none waits more than 10 s: a connection that died without closing (one the browser
// kept from before the preview restarted, say) would otherwise hold its request, and every later poll would
// queue behind the few connections the browser opens to this port, which every viewer on it shares.
let polling = false, pollFailed = false; // (the status line says why the last poll failed)
export async function poll() {
  if (loading || polling) return;
  polling = true;
  const epoch = sourceEpoch;
  try {
    const query = new URLSearchParams();
    const against = changesOn ? baseKind() : null; // (the server builds the push baseline only when asked)
    if (against === "push") query.set("base", against);
    if (ed && editorShown()) query.set("open", ed.path); // (and the open source file's version on disk)
    const r = await fetch(`status/${encodeURIComponent(file)}${query.toString() ? `?${query}` : ""}`,
                          { cache: "no-store", signal: AbortSignal.timeout(10000) })
      .catch((e) => { throw ["TimeoutError", "AbortError"].includes(e.name) ? e : new Error(UNREACHABLE); });
    if (r.status === 403) throw new Error(ACCESS_ENDED);
    if (r.redirected || r.status === 401 || (r.ok && !/json/.test(r.headers.get("Content-Type") || ""))) throw new Error(SIGNED_OUT);
    if (!r.ok) {
      const body = await readBody(r);
      throw new Error(unreachable(r, body) || r.status >= 500 ? UNREACHABLE
                      : r.status === 404 ? "server is older than this viewer: restart the LaTeX build task" : `status ${r.status}`);
    }
    const info = await r.json();
    if (pollFailed) setStatus(""); // (it answers again)
    pollFailed = false;
    sourceStatus(info.source, epoch);
    if (!isDiff) seeBuild(!!info.building, info.build?.ok === false);
    if (!isDiff && info.doc && info.doc !== docFile) return openDoc(info.doc); // the server builds another document now
    if (info.layout) setArea(info.layout);
    showBuild(info.building ? null : info.build, info.building ? null : info.cleanBuild, info.building ? null : info.overwritten);
    showDiffInfo(info.diff);
    $("texImage").hidden = !info.texImage; // builds fall back to the host's TeX; SyncTeX may not work
    $("texImage").title = info.texImage || "";
    $("texImage").textContent = info.texImage?.startsWith("no synctex") ? "⚠ no SyncTeX" : "⚠ TeX image missing";
    $("updated").hidden = !info.latexLive?.updated || isCollaborator(); // (only the owner can restart the preview)
    $("versionInfo").textContent = info.latexLive ? `latex-live ${info.latexLive.version}` : "";
    if (info.building) liveStatus(isDiff ? "building diff…" : "building…", "busy");
    else if ($("status").className === "busy" && Date.now() - rebuildAt > 4000) liveStatus(info.build?.ok === false ? "build failed" : "", info.build?.ok === false ? "error" : "");
    if (!info.version) return info.building || liveStatus(`waiting for ${file}…`);
    setBuildVersion(info.version);
    setBaselines(info.head || null, info.push || null);
    if (me.shared && (against === "head" || against === "push") && info[against]?.state === "waiting" && Date.now() - baselineAsked > 3000) {
      baselineAsked = Date.now(); // (in shared mode a GET builds nothing: the baseline is asked for with a POST)
      postJSON("baseline", { kind: against }).catch(() => {});
    }
    $("notesView").hidden = isDiff || !info.clean?.version;
    // (only a paper with notes has a version without them, or notes for the margin; and only a role that may download them sees them)
    $("downloadClean").hidden = $("downloadReview").hidden = isDiff || !info.clean?.version || !me.can.download;
    $("downloadPdf").textContent = isDiff ? "This comparison" : $("notesView").hidden ? "PDF" : "PDF with notes";
    const clean = !isDiff && view === "clean" && info.clean?.version ? info.clean : null;
    const want = clean ? `clean:${clean.version}` : info.version;
    if (want !== shown) {
      loading = true;
      await load(clean ? cleanFile : file, clean ? clean.version : info.version, clean ? "clean" : "notes", info);
      setShown(want);
    }
    updateChanges();
    if (!isDiff && !measuring) { // the page count of the build that is not on screen
      if (shownVariant === "notes" && info.clean?.refs && info.clean.version !== cleanShown) measureVariant("clean", info);
      else if (shownVariant === "clean" && info.refs && info.version !== notesShown) measureVariant("notes", info);
    }
    const fwd = info.forward;
    if (fwd && fwd.seq !== forwardSeq) {
      // On first load, only honor a request from the last few seconds (not a stale one).
      const target = shownVariant === "clean" ? fwd.clean : fwd;
      if (target && (forwardSeq !== null || Date.now() / 1000 - fwd.seq < 5)) showForward(target);
      forwardSeq = fwd.seq;
    } else if (forwardSeq === null) {
      forwardSeq = 0;
    }
  } catch (err) {
    const message = ["TimeoutError", "AbortError"].includes(err.name) ? "no answer from the preview" : `${err.message || err}`;
    pollFailed = true;
    if (message === ACCESS_ENDED) showAccessEnded();
    else setStatus([UNREACHABLE, SIGNED_OUT].includes(message) ? message : `${message} (retrying)`, "error");
  } finally {
    polling = loading = false;
  }
}
