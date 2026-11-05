// The viewer's script (viewer.html loads it), as far as it is not in the modules it imports yet.

import {
  $, ACCESS_ENDED, cleanFile, docFile, editorShown, el, file, hubBase, isDiff, liveStatus, setStatus, SIGNED_OUT, UNREACHABLE,
} from "./page.js";
import { postJSON, readBody, unreachable } from "./net.js";
import { latestKept } from "./drafts.js";
import { canComment, editorAllowed, isCollaborator, loadAuthors, loadMe, me, notSetUp, ownerName, setWho } from "./role.js";
import { assets, leaveDocument, pdfjsLib, showDocument, shownVariant, viewer } from "./pdfview.js";
import { find, resetFind } from "./search.js";
import { rebuildAt, seeBuild, setBuildVersion, setShown, shown } from "./build.js";
import { setGutter, tab } from "./layout.js";
import { area, setArea } from "./pagetext.js";
import { hidePreview } from "./preview.js";
import { ahead, back, travel } from "./navigation.js";
import { marks, showUnresolved } from "./marks.js";
import { ed, flushSource, saveSource, showEditor, sourceEpoch, sourceStatus } from "./editor.js";
import { showForward } from "./links.js";
import { showAccessEnded, showBuild } from "./banner.js";
import { placeItem, revealControl, showKeys, showMore } from "./toolbar.js";
import { showDownloads } from "./downloads.js";
import { keepFocusIn, showWelcome, welcomeDue } from "./welcome.js";
import { checkPages, cleanShown, measureVariant, measuring, notesShown, renderPages, setResult } from "./pages.js";
import { fillSectionPages, renderOutline } from "./outline.js";
import { closeNotePop, startComment } from "./comments.js";
import { loadMargin, loadNotes, marginOn, resetMargin, showMarginButton } from "./notes.js";
import { updateCut } from "./cut.js";
import {
  baseKind, changesOn, goToChange, setBaselines, toggleChanges, trackDocument, updateChanges, wordsPromise,
} from "./changes.js";
import { refreshTab, showSide, showTab } from "./sidebar.js";
import "./filemenu.js";

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

window.addEventListener("keydown", (e) => {
  const mod = e.ctrlKey || e.metaKey;
  if (!$("welcome").hidden) { // (while the guide is open, only its own keys)
    if (e.key === "Escape") showWelcome(false);
    else if (e.key === "Tab" && !e.defaultPrevented) keepFocusIn($("welcome"), e); // (not a Tab the source pane took, to indent)
    return;
  }
  if (e.target.closest?.("#editorPane")) { // (keys typed in the source pane are the editor's: c, d, Cmd+F, ...)
    if (mod && e.key === "s" && !e.defaultPrevented) { e.preventDefault(); saveSource(); } // (e.g. after a click in its header)
    return;
  }
  if (mod && e.key === "s" && ed && editorShown()) { e.preventDefault(); saveSource(); }
  else if (mod && (e.key === "=" || e.key === "+")) { e.preventDefault(); viewer.increaseScale(); }
  else if (mod && e.key === "-") { e.preventDefault(); viewer.decreaseScale(); }
  else if (mod && e.key === "0") { e.preventDefault(); viewer.currentScaleValue = "page-width"; }
  else if (mod && e.key === "f") { e.preventDefault(); revealControl($("find")); $("find").focus(); $("find").select(); }
  else if (mod && e.key === "g") { e.preventDefault(); find(e.shiftKey); }
  else if (e.target.tagName === "INPUT") return; // keep word-wise cursor keys in the text boxes
  else if ((e.altKey && e.key === "ArrowLeft") || (e.metaKey && e.key === "[")) { e.preventDefault(); travel(back, ahead); }
  else if ((e.altKey && e.key === "ArrowRight") || (e.metaKey && e.key === "]")) { e.preventDefault(); travel(ahead, back); }
  else if (e.key === "Escape") { hidePreview(); $("comparePanel").hidden = true; closeNotePop(); showMore(false); showKeys(false); showDownloads(false); }
  else if (!mod && !e.altKey && e.key === "?") { e.preventDefault(); showKeys($("keys").hidden); }
  else if (!mod && !e.altKey && e.key === "c") {
    if (!$("commentBtn").hidden) { e.preventDefault(); startComment(); }
    else if (isCollaborator() && !isDiff && !canComment()) setStatus(notSetUp() || `Your access is read-only: ask ${ownerName()} if you want to comment`, "error");
  }
  else if (!mod && !e.altKey && e.key === "d") { e.preventDefault(); toggleChanges(); }
  else if (!mod && !e.altKey && (e.key === "." || e.key === ",")) { e.preventDefault(); goToChange(e.key === "." ? 1 : -1); }
  else if (!mod && !e.altKey && e.key === "e") {
    if (editorAllowed()) { e.preventDefault(); showEditor(!editorShown()); }
    else if (isCollaborator() && !isDiff) setStatus(`Editing the source isn't part of your access (${me.role}): ask ${ownerName()} if you need it`, "error");
  }
});

// Controls follow the role (once /me has answered): a collaborator comments only as themselves,
// and sees no controls for what their role can't do (live.py refuses those anyway).
const ROLES = { viewer: "you can read the paper", commenter: "you can read the paper and comment on it",
                editor: "you can comment and edit the LaTeX source", owner: "this preview is yours" };
function applyRole() {
  if (isCollaborator()) setWho(me.macro || "");
  $("rebuild").hidden = isDiff || !me.can.rebuild;
  $("compare").hidden = !me.can.compare;
  $("editBtn").hidden = !editorAllowed();
  $("me").hidden = !me.shared;
  $("me").textContent = `${me.name || me.id} · ${me.role}`;
  $("me").title = `Signed in as ${me.id}: ${ROLES[me.role] || me.role}`;
  $("welcomeBtn").hidden = $("keys").querySelector(".welcome").hidden = !me.shared; // (the guide is for coauthors)
  document.querySelector("#tabs [data-tab=checks]").hidden = isDiff || me.can.checks === false;
  if (me.can.checks === false && tab === "checks") showTab("outline");
}

// Compare: latexdiff against a commit (live.py builds <doc>-diff.pdf), shown in its own tab.
$("compare").onclick = async () => {
  const panel = $("comparePanel");
  if (!panel.hidden) return (panel.hidden = true);
  const r = ($("moreMenu").contains($("compare")) ? $("more") : $("compare")).getBoundingClientRect();
  showMore(false);
  panel.style.left = `${Math.min(r.left, window.innerWidth - 450)}px`;
  panel.replaceChildren(el("div", { className: "head", textContent: "Loading commits…" }));
  panel.hidden = false;
  const { dirty, commits } = await (await fetch("commits")).json();
  const row = (rev, date, text, title) => {
    const div = el("div", { className: "commit", title }, el("span", { className: "rev", textContent: rev }),
                   el("span", { className: "date", textContent: date }), el("span", { textContent: text }));
    div.onclick = () => startDiff(rev);
    return div;
  };
  panel.replaceChildren(el("div", { className: "head", textContent: "Show the changes since… (the paper as it is now, with what changed since that commit marked; in a new tab)" }),
    ...(dirty ? [row("HEAD", "", "uncommitted edits only", "the last commit vs. the working tree")] : []),
    ...commits.map((c) => row(c.rev, c.date, `${c.subject} — ${c.author}`, `${c.rev} ${c.date} ${c.author}: ${c.subject}`)));
};
async function startDiff(rev) {
  $("comparePanel").hidden = true;
  await flushSource(); // (the diff is of the files on disk)
  const r = me.legacy ? await fetch(`diff?${new URLSearchParams({ rev })}`) : await postJSON("diff", { rev });
  if (!r.ok && r.status !== 409) return setStatus((await readBody(r)).error, "error");
  const url = `viewer.html?${new URLSearchParams({ file: docFile.replace(/\.pdf$/, "-diff.pdf") })}`;
  if (isDiff) return; // this tab already shows the diff; it reloads when the new one is built
  if (!window.open(url, "latex-live-diff")) location.href = url;
}
if (isDiff) { // (the diff tab's main control, so in the toolbar)
  placeItem($("compare"), true);
  $("compare").textContent = "Compare with…";
  $("compare").title = "Compare with another commit";
}
function showDiffInfo(d) {
  if (!isDiff || !d?.sha) return;
  const info = $("diffInfo");
  const again = el("button", { textContent: "Rebuild", title: "Diff the same commit against the current files" });
  again.onclick = () => startDiff(d.rev);
  const paper = el("a", { textContent: "↩ paper", href: `viewer.html?${new URLSearchParams({ file: docFile })}` });
  info.replaceChildren(`changes since ${d.rev} `, again, " ", paper);
  info.title = `latexdiff of commit ${d.sha} against the working tree`;
  info.hidden = false;
}

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
async function poll() {
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
// The sidebar, as it was left.
if (localStorage.getItem("latex-live:side")) showSide(true);
// Main document: the paper's root .tex files. Picking one makes the server build it instead, and
// every open viewer follows (poll), once the source pane has saved.
let leaving = false;
async function openDoc(pdf) {
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
async function loadDocs() {
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

// Start: who is asking first (it decides the controls, and whose notes new ones are), then the
// documents (the owner's menu), the authors, the status poll, the source pane if it was open (or on
// a file with unsaved edits this browser kept, to restore), and the welcome guide when it is due.
async function start() {
  if (!(await loadMe())) return showAccessEnded();
  applyRole();
  if (me.can.switch) {
    loadDocs().catch(() => {});
    setInterval(() => { if (document.activeElement !== $("docPick")) loadDocs().catch(() => {}); }, 15000); // (papers opened since)
  }
  const authorsLoaded = isDiff ? null : loadAuthors().catch(() => {});
  poll();
  setInterval(poll, isCollaborator() ? 500 : 250); // (coauthors: half as often, as there may be many)
  const kept = editorAllowed() ? latestKept() : null;
  if (kept || (editorAllowed() && localStorage.getItem("latex-live:editor"))) showEditor(true, { path: kept?.path, focus: false });
  await authorsLoaded; // (the guide names your initials)
  if (welcomeDue()) showWelcome(true, true);
}
start();
