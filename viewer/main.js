// The viewer's script (viewer.html loads it), as far as it is not in the modules it imports yet.

import {
  $, ACCESS_ENDED, building, cleanFile, docFile, editorShown, el, file, firstVisit, hubBase, isDiff, liveStatus, setStatus,
  SIGNED_OUT, sleep, UNREACHABLE,
} from "./page.js";
import { fitsKeepalive, postJSON, readBody, signInPage, unreachable } from "./net.js";
import { diffWords, lineHunks } from "./diff.js";
import { dropKept, EDITOR_ID, keptKey, latestKept, newSaveId, readKept } from "./drafts.js";
import {
  authors, canComment, canOpenFile, editable, editorAllowed, isCollaborator, loadAuthors, loadMe, me, notSetUp, opensAt,
  opensInBrowser, opensSources, openTitles, openWhere, ownerName, setWho, who, whoLabel,
} from "./role.js";
import {
  assets, container, doc, docSeq, eventBus, leaveDocument, linkService, pagesReady, pdfjsLib, refit, showDocument, shownVariant,
  viewer,
} from "./pdfview.js";
import { find, resetFind } from "./search.js";
import {
  behind, buildFailed, buildVersion, rebuild, rebuildAt, seeBuild, setBuildVersion, setShown, shown, wrote,
} from "./build.js";
import { fitEditor, GUTTER, setGutter, setSide, setTab, setToggle, sideOpen, tab } from "./layout.js";
import { area, areaFraction, columnAt, columnMid, norm, pageText, setArea, textLines } from "./pagetext.js";
import { hidePreview } from "./preview.js";
import { ahead, back, flash, goToPage, reveal, travel } from "./navigation.js";
import { cutOn, drawAllMarks, marks, setCutOn, showUnresolved } from "./marks.js";

// Which build to show: "notes" (the live build) or "clean" (the notes-free build, when there is one).
let view = localStorage.getItem("latex-live:view") === "notes" ? "notes" : "clean"; // notes-free unless chosen otherwise

// The ⋯ menu: dark mode and Compare, plus the toolbar's controls that don't fit (fitBar).
function showMore(open) {
  $("moreMenu").hidden = !open;
  $("more").classList.toggle("on", open);
}
const revealControl = (node) => { if ($("moreMenu").contains(node)) showMore(true); }; // (before focusing a control there)
$("more").onclick = () => showMore($("moreMenu").hidden);
// The shortcuts list (?), with only the rows for what this user can do.
function showKeys(open) {
  if (open) {
    $("keysComment").hidden = !canComment();
    $("keysEdit").hidden = !editorAllowed();
    $("keysDblclickRow").hidden = isDiff || !opensSources();
    $("keysDblclick").textContent = `open the source line in ${openWhere()}`;
  }
  $("keys").hidden = !open;
}
$("keysBtn").onclick = () => { showMore(false); showKeys(true); };
$("keys").querySelector(".close").onclick = () => showKeys(false);
document.addEventListener("mousedown", (e) => { // (not the welcome guide: it closes with its own buttons, or Esc)
  if (!$("moreMenu").hidden && !$("moreMenu").contains(e.target) && !$("more").contains(e.target)) showMore(false);
  if (!$("keys").hidden && !$("keys").contains(e.target)) showKeys(false);
  if (!$("fileMenu").hidden && !$("fileMenu").contains(e.target) && !$("filePick").contains(e.target)) $("fileMenu").hidden = true;
});

// Downloads: the PDF as built (notes included); without notes, from an exact build that the server
// makes on request (live.py start_export; once per build of the document); or that build with the
// notes in its margins (live.py start_review), for co-authors who don't run latex-live. In shared mode
// the last two are for whoever's role may (me.can.download): it is the shared document, as every
// coauthor reads it. They are asked for with a POST, whose Origin the server checks there (it builds).
function saveFile(url, name) {
  const a = el("a", { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
}
const docName = () => docFile.replace(/\.pdf$/, "");
// The toolbar's download button opens its menu on hover (and on click, for keyboards and touch).
function showDownloads(open) {
  const menu = $("downloadMenu"), button = $("downloadBtn");
  if (open) {
    const r = button.getBoundingClientRect();
    menu.style.left = `${Math.max(4, Math.min(r.left, window.innerWidth - 270))}px`;
    menu.style.top = `${r.bottom + 2}px`;
  }
  menu.hidden = !open;
  button.setAttribute("aria-expanded", String(open));
  button.classList.toggle("on", open);
}
let downloadsTimer = 0;
for (const node of [$("downloadBtn"), $("downloadMenu")]) {
  node.addEventListener("mouseenter", () => { clearTimeout(downloadsTimer); showDownloads(true); });
  node.addEventListener("mouseleave", () => { downloadsTimer = setTimeout(() => showDownloads(false), 300); }); // (time to reach the menu)
}
$("downloadBtn").onclick = () => showDownloads(true);
document.addEventListener("mousedown", (e) => {
  if (!$("downloadMenu").hidden && !$("downloadMenu").contains(e.target) && !$("downloadBtn").contains(e.target)) showDownloads(false);
});
$("downloadPdf").onclick = () => {
  showMore(false);
  showDownloads(false);
  const shown = isDiff ? file : docFile; // (a comparison tab saves the comparison)
  // ("-with-notes" when the paper has notes, so a version without them too: the Notes switch shows then)
  saveFile(`pdf/${encodeURIComponent(shown)}?v=${buildVersion}`, isDiff ? file : `${docName()}${$("notesView").hidden ? "" : "-with-notes"}.pdf`);
};

// The server builds them from the PDF as last built (and waits for a build in progress first): ask until it is
// done, showing its progress (poll leaves the status line to it meanwhile: liveStatus), then save. Right after
// this tab writes a note, wait for the build that the write starts, which the server can't know of yet.
async function downloadBuilt(endpoint, built, saveAs, what, done) {
  showMore(false);
  showDownloads(false);
  if (building.has(endpoint)) return;
  building.add(endpoint);
  try {
    while (behind()) {
      setStatus(`waiting for the build in progress, then the PDF ${what}…`, "busy");
      await new Promise((r) => setTimeout(r, 250));
    }
    for (;;) {
      const r = await postJSON(endpoint, {}).catch(() => null);
      const e = r ? await readBody(r) : {};
      if (!r || unreachable(r, e)) return setStatus("download failed: can't reach the preview server", "error");
      if (signInPage(r, e)) return setStatus(SIGNED_OUT, "error"); // (a proxy's sign-in page, not the server's answer)
      if (!r.ok && !e.state) return setStatus(`download failed: ${e.error}`, "error"); // (refused: e.g. the access ended)
      if (e.state === "done") {
        saveFile(`pdf/${encodeURIComponent(built)}?v=${e.version}`, saveAs);
        return setStatus(`saved ${saveAs} (${done(e)}${buildFailed ? "; of the last build that worked" : ""})`, "fresh");
      }
      if (e.state === "failed") return setStatus(`couldn't build the PDF ${what}: ${e.msg}`, "error");
      setStatus(`building the PDF ${what}${e.step ? `: ${e.step}` : ""}…`, "busy");
      await sleep(1000);
    }
  } catch (err) {
    setStatus(`download failed: ${err.message || err}`, "error");
  } finally {
    building.delete(endpoint);
  }
}
$("downloadClean").onclick = () => downloadBuilt("export", `${docName()}-export.pdf`, `${docName()}.pdf`, "without notes",
                                                 () => "without notes");
$("downloadReview").onclick = () => downloadBuilt("review", `${docName()}-review.pdf`, `${docName()}-with-margin-notes.pdf`,
  "with notes in the margin", (e) => [`${e.notes} note${e.notes === 1 ? "" : "s"} in the margin`,
    e.approximate && `${e.approximate} placed by source line`, e.continued && `${e.continued} continued after the paper`].filter(Boolean).join(", "));

// The welcome guide: what this is, and how to comment, edit and find your way, for the role at hand.
// Whether it shows by itself is kept in latex-live:welcome: unset, "later" (it showed by itself and
// hasn't been put away), or "hidden" (closed with "Don't show this again" checked). It shows by
// itself while "later", and while unset to collaborators in shared mode and to someone new to
// latex-live, though not to an owner who has used it before; ? and the ⋯ menu bring it back.
let welcomeFocus = null; // (what had the focus before the guide opened: it gets it back)
function showWelcome(open, auto = false) {
  const w = $("welcome");
  if (!open) {
    if (w.hidden) return;
    localStorage.setItem("latex-live:welcome", w.querySelector("input").checked ? "hidden" : "later");
    w.hidden = true;
    welcomeFocus?.focus?.();
    welcomeFocus = null;
    return;
  }
  const part = (name, on) => { for (const e of w.querySelectorAll(`[data-part=${name}]`)) e.hidden = !on; };
  const missing = notSetUp();
  part("comment", me.can.comment || !!missing);
  part("pick", !isCollaborator());
  part("locked", isCollaborator());
  part("edit", editorAllowed());
  part("download", !!me.can.download);
  part("shared", isCollaborator() && (me.can.comment || me.can.edit));
  w.querySelector(".how").hidden = !!missing;
  w.querySelector(".missing").hidden = !missing;
  w.querySelector(".missing").textContent = missing || "";
  const initials = isCollaborator() && authors.find((a) => a.macro === me.macro)?.author;
  w.querySelector(".initials").textContent = initials ? ` (${initials})` : "";
  w.querySelector(".changes").textContent = me.can.edit ? "Your edits and notes are" : "Your notes are";
  w.querySelector(".owner").textContent = ownerName();
  w.querySelector("input").checked = true;
  if (auto) localStorage.setItem("latex-live:welcome", "later");
  showMore(false);
  showKeys(false);
  showDownloads(false);
  if (w.hidden) welcomeFocus = document.activeElement;
  w.hidden = false;
  w.querySelector(".ok").focus();
}
function welcomeDue() {
  const state = localStorage.getItem("latex-live:welcome");
  return !isDiff && me.shared && (state === "later" || (state === null && (isCollaborator() || firstVisit)));
}
// Tab and Shift+Tab stay within the guide while it is open.
function keepFocusIn(box, e) {
  const stops = [...box.querySelectorAll("button, input, a[href], textarea")].filter((n) => !n.disabled && n.getClientRects().length);
  const i = stops.indexOf(document.activeElement);
  if (i >= 0 && !(e.shiftKey ? i === 0 : i === stops.length - 1)) return; // (the browser moves on within it)
  e.preventDefault(); // (from its last stop, its first; from its first, back to its last; from outside it, in)
  stops.at(e.shiftKey ? -1 : 0)?.focus();
}
$("welcome").querySelector(".close").onclick = () => showWelcome(false);
$("welcome").querySelector(".ok").onclick = () => showWelcome(false);
$("welcomeBtn").onclick = () => showWelcome(true);
$("keys").querySelector(".welcome").onclick = () => showWelcome(true);

// The toolbar keeps to one row: when it overflows, its controls move into the ⋯ menu, lowest
// data-priority first, and come back once there is room again. It refits when the window
// resizes, when a control appears or disappears, and when the status or the page count changes.
const barItems = [...document.querySelectorAll("[data-priority]")]; // (in toolbar order)
function placeItem(item, inBar) {
  const where = inBar ? $("bar") : $("moreItems");
  const next = barItems.slice(barItems.indexOf(item) + 1).find((i) => i.parentElement === where);
  where.insertBefore(item, next || (inBar ? $("spacer") : null));
}
function fitBar() {
  const bar = $("bar"), overflows = () => bar.scrollWidth > bar.clientWidth + 1;
  bar.classList.add("measuring"); // (everything at full width, packed to the left)
  const room = () => bar.getBoundingClientRect().left + bar.clientWidth - parseFloat(getComputedStyle(bar).paddingRight) -
    Math.max(...[...bar.children].filter((c) => c.offsetWidth).map((c) => c.getBoundingClientRect().right));
  const toMenu = (item) => { item.dataset.width = item.offsetWidth; placeItem(item, false); };
  const byPriority = (a, b) => b.dataset.priority - a.dataset.priority;
  for (const item of barItems.filter((i) => i.parentElement === $("moreItems")).sort(byPriority)) {
    if (+item.dataset.width + 8 > room()) break; // (no moving back and forth while it can't fit)
    placeItem(item, true);
    if (overflows()) { toMenu(item); break; }
  }
  for (const item of barItems.filter((i) => i.parentElement === bar).sort(byPriority).reverse()) {
    if (!overflows()) break;
    toMenu(item);
  }
  bar.classList.remove("measuring");
}
new ResizeObserver(fitBar).observe($("bar"));
const shownChanged = new MutationObserver((records) => {
  if (records.some((r) => r.target !== $("moreMenu") && (r.oldValue === null) !== !r.target.hasAttribute("hidden"))) fitBar();
});
for (const node of [$("bar"), $("moreMenu")]) {
  shownChanged.observe(node, { subtree: true, attributes: true, attributeFilter: ["hidden"], attributeOldValue: true });
}
const textChanged = new MutationObserver(fitBar);
for (const node of [$("status"), $("pages")]) textChanged.observe(node, { childList: true, characterData: true, subtree: true });
document.fonts?.ready.then(fitBar);

// Dark mode (remembered: setToggle).
$("dark").onclick = () => { setToggle("dark", !document.body.classList.contains("dark")); $("dark").classList.toggle("on"); };
if (localStorage.getItem("latex-live:dark")) { document.body.classList.add("dark"); $("dark").classList.add("on"); }

for (const name of ["notes", "checks", "figures"]) document.querySelector(`#tabs [data-tab=${name}]`).hidden = isDiff;
$("toggleSide").onclick = () => showSide($("side").hidden);
for (const b of document.querySelectorAll("#tabs button")) b.onclick = () => showTab(b.dataset.tab);

function showSide(on) {
  setSide(on);
  if (on) showTab(tab);
}
function showTab(name) {
  setTab(name);
  for (const b of document.querySelectorAll("#tabs button")) b.classList.toggle("on", b.dataset.tab === name);
  for (const p of document.querySelectorAll(".panel")) p.hidden = p.id !== name;
  refreshTab();
}

function refreshTab() {
  if (!doc) return;
  if (sideOpen("thumbs")) renderThumbs();
  if (sideOpen("figures")) renderFloats().catch((e) => console.warn(e));
  if (sideOpen("checks") && checksVersion !== `${buildVersion}/${shown}`) runChecks();
}

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

// SyncTeX inverse search: double-click a spot on a page to open its source line in VS Code, or in
// the source pane beside the PDF when that is open (a collaborator who may edit, or an owner whose
// preview has no VS Code to open: always there). For someone who can open neither, a double-click
// just selects a word, as anywhere else (e.g. to comment on it).
container.addEventListener("dblclick", async (e) => {
  const pageDiv = e.target.closest(".page");
  if (!pageDiv || e.target.closest(".ll-margin") || !opensSources()) return;
  const pageView = viewer.getPageView(+pageDiv.dataset.pageNumber - 1);
  const rect = pageDiv.getBoundingClientRect();
  const [x, y] = pageView.viewport.convertToPdfPoint(
    e.clientX - rect.left - pageDiv.clientLeft, e.clientY - rect.top - pageDiv.clientTop);
  window.getSelection().removeAllRanges(); // undo the word selection from the double-click
  flash(pageView.id, [x - 6, y - 6, x + 6, y + 6], "dot");
  if (isDiff) return setStatus("inverse search is off in the diff view", "error");
  const top = pageView.pdfPage.view[3] - y; // synctex measures y down from the page top
  const q = new URLSearchParams({ file, page: pageView.id, x: x.toFixed(1), y: top.toFixed(1), ...cleanParam() });
  try {
    let why = null; // (VS Code's failure to open, when the source pane opens instead)
    if (me.legacy || (!me.shared && me.can.openHost && !opensInBrowser())) { // (the server opens VS Code itself, as it always has)
      const r = await fetch(`synctex?${q}`);
      const text = await r.text();
      // 409: found, but VS Code did not open (e.g. not run in a VS Code terminal); the pane, then
      if (r.status !== 409 || !editorAllowed()) return setStatus(r.ok ? `→ ${text}` : text, r.ok ? "fresh" : "error");
      why = text;
    }
    q.set("json", "1"); // (just where it is; the viewer opens it)
    const r = await fetch(`synctex?${q}`);
    const found = await readBody(r);
    if (!r.ok) return setStatus(found.error || `no source line here (${r.status})`, "error");
    if (!found.file) return setStatus(found.reason || "no source file here", "error");
    if (why === null) {
      setStatus(`→ ${found.file}:${found.line}`, "fresh");
      return canOpenFile(found.file) && openSource(found.file, found.line);
    }
    if (!editable(found.file)) return setStatus(why, "error");
    setStatus(`${found.file}:${found.line} is in the source pane (${why})`);
    showEditor(true, { path: found.file, line: found.line });
  } catch (err) {
    setStatus(`${err.message || err}`, "error");
  }
});

// SyncTeX forward search (forward.py, or a note or check in the sidebar): scroll to the source
// line's boxes ({page, x, y, w, h}, points from the page's top-left) and highlight them; one that
// arrives while the document is loading, once its pages are ready.
let pendingForward = null;
eventBus.on("pagesinit", () => {
  if (pendingForward) showForward(pendingForward); // arrived while this document was loading
  pendingForward = null;
});
function showForward(t) {
  if (!pagesReady) return (pendingForward = t);
  const pageView = viewer.getPageView(t.page - 1);
  if (!pageView) return;
  const H = pageView.pdfPage.view[3];
  reveal(t.page, [t.x - 2, H - t.y - t.h - 2, t.x + t.w + 2, H - t.y + 2]);
  setStatus(`← page ${t.page}`, "fresh");
}
// Show a source line in the PDF; sources with no PDF location (e.g. a .bib entry) open in the editor.
const cleanParam = () => (shownVariant === "clean" ? { clean: 1 } : {}); // SyncTeX of the notes-free build
async function showSource(file, line, openIfMissing = false) {
  const r = await fetch(`forward?${new URLSearchParams({ file, line, ...cleanParam() })}`);
  const body = await r.json().catch(() => ({ error: `${file}:${line} is not in the PDF (${r.status})` }));
  if (r.ok) return showForward(body);
  if (openIfMissing && canOpenFile(file)) return openSource(file, line);
  setStatus(body.error, "error");
}

// Open a source line: in the source pane beside the PDF when that is open (for a collaborator who
// may edit, or the owner of a shared preview without VS Code: always), else in the host's editor
// (the owner's VS Code, live.py /open), or the pane when that does not open.
async function openSource(file, line) {
  if (opensInBrowser() && editable(file)) return showEditor(true, { path: file, line });
  if (!me.can.openHost) return setStatus(`${file} can't be opened here`, "error");
  try {
    const r = me.legacy ? await fetch(`open?${new URLSearchParams({ file, line })}`) : await postJSON("open", { file, line });
    if (r.ok) return;
    const why = (await readBody(r)).error; // e.g. latex-live is not running in a VS Code terminal
    if (!editorAllowed() || !editable(file)) return setStatus(why, "error");
    setStatus(`${file}:${line} is in the source pane (VS Code did not open: ${why})`);
    showEditor(true, { path: file, line });
  } catch (err) {
    setStatus(`${err.message || err}`, "error");
  }
}

// The LaTeX source beside the PDF (✎ Source, or E), for editors and the owner: live.py /files and
// /file, in CodeMirror (codemirror/, through the import map), loaded on first use. Edits save by
// themselves 1.5 s after the last change, and at once on Cmd/Ctrl+S, a file switch, closing the
// pane, and leaving the page. live.py merges a save with changes made to the file meanwhile (by
// someone else, or a note written from the viewer); edits typed while a save is on its way are
// rebased onto its answer, unless they touch the lines it changed (then the server merges them
// too, on the next save). A file that changes on disk reloads in place (status ?open=). Until a
// save goes through, this browser keeps the unsaved text (keepUnsaved), so that it survives a crash
// or a reload.

// The name this page's saves go by (EDITOR_ID), so that live.py knows which saves are its own (save_file). A
// restored draft starts a new name for the saves (restoreKept): it is not built on this page's last save, so that
// save must count as anyone's.
let saveId = EDITOR_ID;

// Build-status banner: shows the first errors of a failed build, or else of a failed notes-free
// pass (the notes-free view and its page count are then out of date); click one to open it in the
// editor. Failing those, it says when another build (e.g. make) replaced the published PDF.
let dismissed = null;
const closeButton = (onclick) => {
  const b = el("button", { className: "close", textContent: "✕", title: "Close", onclick });
  b.setAttribute("aria-label", "Close");
  return b;
};
function showBuild(build, notesFree, overwritten) {
  const banner = $("banner");
  accessClosed = false; // (the server answers again)
  if (!(build && !build.ok) && !isDiff && notesFree && !notesFree.ok) build = { ...notesFree, notesFree: true };
  if (!(build && !build.ok) && !isDiff && overwritten) build = { ok: false, time: overwritten, errors: [], overwritten: true };
  if (!build || build.ok || dismissed === build.time) return (banner.hidden = true);
  banner.replaceChildren();
  banner.dataset.kind = "build";
  const close = closeButton(() => { dismissed = build.time; banner.hidden = true; });
  banner.append(close, isDiff ? `Diff failed at ${build.time}.`
    : build.notesFree ? `The notes-free build failed at ${build.time}${build.msg ? ` (${build.msg})` : ""}; the notes-free view and its page count are out of date.`
    : build.overwritten ? `Another build (e.g. make) replaced ${docFile} at ${build.time}: the view with notes shows that PDF, and double-click may land on the wrong line.`
    : `Build failed at ${build.time}; showing the last good PDF.`);
  for (const err of build.errors.slice(0, 3)) {
    const line = el("div");
    if (err.file && canOpenFile(err.file)) {
      const a = opensAt(el("a", { textContent: `${err.file}:${err.line}` }), `${err.file}:${err.line}`);
      a.onclick = () => openSource(err.file, err.line);
      line.append(a, `  ${err.msg}`);
    } else {
      line.append(err.file ? `${err.file}:${err.line}  ${err.msg}` : err.msg);
    }
    banner.append(line);
  }
  if (build.errors.length > 3) banner.append(`(+${build.errors.length - 3} more${me.can.openHost ? " in the Problems panel" : ""})`);
  if (build.overwritten && me.can.rebuild) banner.append(" ", el("a", { textContent: "Rebuild now", onclick: rebuild }));
  banner.hidden = false;
}
// The access ended (403 for status or me): the same banner, with what to try and where unsaved edits
// are, until the server answers again (closed by hand, it stays closed until then).
let accessClosed = false;
function showAccessEnded() {
  setStatus(ACCESS_ENDED, "error");
  const banner = $("banner");
  if (accessClosed || (banner.dataset.kind === "access" && !banner.hidden)) return;
  banner.replaceChildren(closeButton(() => { accessClosed = true; banner.hidden = true; }),
    `You no longer have access to this preview, or your sign-in or link expired. Reload the page; if that doesn't help, ask ${ownerName()}.`,
    ...(ed?.dirty ? [` Your unsaved edits to ${ed.path} are kept in this browser.`] : []));
  banner.dataset.kind = "access";
  banner.hidden = false;
}

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

let cmView = null; // the open file's editor (CodeMirror's view)
let CM = null, cmLoading = null, cmExtensions = null; // CodeMirror, once loaded (loadEditor)
let srcFiles = null; // live.py /files: {files: [{path, size}], main}
function setSrcFiles(list) { srcFiles = list; }
// The open file: {path, base (its text as the server has it, at version baseVersion; null when that
// version is not known, so that the server merges from base), dirty (edits not saved yet), markers
// (conflict markers in the text), error ({short, why}: why the last save was refused), gone (the file
// is no longer one the server saves: its text can only be copied)}.
let ed = null;
// A save or reload on its way: {kind, since (the edits typed since it left, a ChangeSet), done (a promise)}.
let busy = null;
let saveTimer = null, retryTimer = null, openSeq = 0, lastSaveAt = 0;
// Saves that failed but may work later: offline is then why ("unreachable": no answer, or the proxy
// or hub couldn't reach the preview; "signin": a page came back instead, e.g. a proxy's sign-in page;
// "updating": the owner is updating the shared copy, live.py's 503 {retry}; "server": another 5xx).
let offline = null, offlineSince = 0, retryDelay = 2000, noticeTimer = null;
let sourceEpoch = 0; // (bumped whenever the open file's version changes: a status answer from before is stale)
let conflict = null; // a conflict of this session, not resolved yet: {by: who saved the other side, if live.py says}

// (save and edited: what Cmd/Ctrl+S and every change to the text call, saveSource and sourceEdited)
function loadEditor({ save, edited }) {
  cmLoading ??= (async () => {
    const [state, view, commands, language, search, autocomplete, { stex }, { tags: t }] = await Promise.all([
      import("@codemirror/state"), import("@codemirror/view"), import("@codemirror/commands"), import("@codemirror/language"),
      import("@codemirror/search"), import("@codemirror/autocomplete"), import("@codemirror/legacy-modes/mode/stex"),
      import("@lezer/highlight")]);
    const { EditorView } = view;
    const theme = EditorView.theme({ // (dark, as the rest of the chrome: panels #2a2d2f, bars #323639)
      "&": { height: "100%", color: "#ddd", backgroundColor: "#2a2d2f", fontSize: "12.5px" },
      ".cm-scroller": { fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace", lineHeight: "1.5" },
      ".cm-content": { caretColor: "#fff" },
      ".cm-cursor, .cm-dropCursor": { borderLeftColor: "#fff" },
      "&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection":
        { backgroundColor: "#2f5f8f99" },
      ".cm-gutters": { color: "#6e7478", backgroundColor: "#26292b", border: "none", borderRight: "1px solid #33373a" },
      ".cm-activeLine": { backgroundColor: "#ffffff0a" },
      ".cm-activeLineGutter": { color: "#bbb", backgroundColor: "#ffffff0d" },
      "&.cm-focused .cm-matchingBracket": { backgroundColor: "#4a4f52", outline: "1px solid #888" },
      "&.cm-focused .cm-nonmatchingBracket": { backgroundColor: "#6a2424" },
      ".cm-panels": { color: "#eee", backgroundColor: "#323639" },
      ".cm-panels.cm-panels-top": { borderBottom: "1px solid #1d1f20" },
      ".cm-textfield": { color: "#eee", backgroundColor: "#202124", border: "1px solid #555", borderRadius: "3px" },
      ".cm-button": { color: "#eee", backgroundImage: "none", backgroundColor: "#4a4f52", border: "0", borderRadius: "3px" },
      ".cm-searchMatch": { backgroundColor: "#e0a04033", outline: "1px solid #e0a04099" },
      ".cm-searchMatch.cm-searchMatch-selected": { backgroundColor: "#e0a04080" },
      ".cm-tooltip": { color: "#ddd", backgroundColor: "#2a2d2f", border: "1px solid #444" },
      ".cm-remote": { backgroundColor: "#2f6fb033" }, // (lines someone else just changed: flashLines)
    }, { dark: true });
    const colors = language.HighlightStyle.define([ // (the token kinds of CodeMirror's stex mode)
      { tag: t.comment, color: "#9aa3a7", fontStyle: "italic" },
      { tag: t.tagName, color: "#79b8ff" },                 // \commands
      { tag: t.keyword, color: "#f0a35e" },                 // $, \[, \( (math)
      { tag: t.atom, color: "#c5a5ff" },                    // environment names, labels, citation keys
      { tag: [t.number, t.string], color: "#b5cea8" },
      { tag: t.special(t.variableName), color: "#e5c07b" }, // letters in math
      { tag: t.standard(t.variableName), color: "#56b6c2" },
      { tag: t.bracket, color: "#9aa0a6" },
      { tag: t.invalid, color: "#ff7b72" },
    ]);
    // Lines someone else just changed, highlighted for a moment (flashLines sets them, or none).
    const flashLines = state.StateEffect.define();
    const flashed = state.StateField.define({
      create: () => view.Decoration.none,
      update: (lines, tr) => tr.effects.reduce((all, e) => (e.is(flashLines) ? e.value : all), lines.map(tr.changes)),
      provide: (field) => EditorView.decorations.from(field),
    });
    cmExtensions = [
      state.EditorState.lineSeparator.of("\n"), // (only \n ends a line, as on the server: a lone \r stays a character)
      view.lineNumbers(), view.highlightActiveLineGutter(), view.highlightSpecialChars(), view.drawSelection(),
      view.highlightActiveLine(), commands.history(), language.bracketMatching(), autocomplete.closeBrackets(),
      search.search({ top: true }), language.StreamLanguage.define(stex), language.syntaxHighlighting(colors),
      EditorView.lineWrapping, EditorView.contentAttributes.of({ spellcheck: "true" }), theme, flashed,
      view.keymap.of([{ key: "Mod-s", run: () => { save(); return true; }, preventDefault: true },
                      ...autocomplete.closeBracketsKeymap, ...commands.defaultKeymap, ...search.searchKeymap,
                      ...commands.historyKeymap, commands.indentWithTab]),
      EditorView.updateListener.of(edited),
    ];
    CM = { EditorState: state.EditorState, ChangeSet: state.ChangeSet, Transaction: state.Transaction, EditorView,
           Decoration: view.Decoration, flashLines, remoteLine: view.Decoration.line({ class: "cm-remote" }) };
  })().catch((err) => { cmLoading = null; throw err; });
  return cmLoading;
}

// Show or hide the source pane; showing it opens path at line, else the file open last time (in
// this paper) or the main file (with a hint on how to get to the source of a spot in the PDF).
async function showEditor(on, { path = null, line = 0, focus = true } = {}) {
  if (on && !editorAllowed()) return;
  if (!on) {
    $("fileMenu").hidden = true;
    saveSource();
  }
  if (editorShown() !== on) {
    $("editorPane").hidden = !on;
    $("editBtn").classList.toggle("on", on);
    setToggle("editor", on);
    fitEditor(on);
    refit();
    openTitles();
  }
  if (!on) return;
  try {
    await loadEditor({ save: saveSource, edited: sourceEdited });
    let hint = false;
    if (!path && !ed) [path, hint] = await firstFile();
    if (path) await openFile(path, line);
    if (hint && cmView) $("editorHint").hidden = false;
    if (!cmView) $("editorHost").replaceChildren(el("div", { className: "msg",
      textContent: path ? `${path} did not open (the status line says why): pick a file above.` : "No source files to edit." }));
    if (focus && editorShown()) cmView?.focus();
  } catch (err) {
    console.warn(err);
    if (cmView) setStatus(`${err.message || err}`, "error");
    else $("editorHost").replaceChildren(el("div", { className: "msg", textContent: `The editor did not load: ${err.message || err}` }));
  }
}
// The file to open first: [path, whether that is just the main file (nothing remembered)].
async function firstFile() {
  const r = await fetch("files", { cache: "no-store" });
  const list = await readBody(r);
  if (!r.ok || !Array.isArray(list.files)) throw new Error(list.error || `the list of files did not load (${r.status})`);
  srcFiles = list;
  const paths = srcFiles.files.map((f) => f.path), last = localStorage.getItem(`latex-live:${hubBase}editorFile`);
  return paths.includes(last) ? [last, false] : [srcFiles.main || paths[0] || null, true];
}
// Open a file (once the one open now is saved, or is gone from the paper) and go to a line in it.
async function openFile(path, line = 0) {
  if (ed?.path === path) return line && goToLine(line);
  const seq = ++openSeq;
  if (ed && !(await flushSource())) return setStatus(`${ed.path} is not saved yet, so ${path} did not open`, "error");
  const r = await fetch(`file?${new URLSearchParams({ path })}`, { cache: "no-store" }).catch(() => null);
  if (seq !== openSeq) return;
  const f = r ? await readBody(r) : {};
  if (!r?.ok || typeof f.text !== "string" || typeof f.version !== "string") {
    const why = !r || unreachable(r, f) ? "can't reach the preview server" : signInPage(r, f) ? "your sign-in may have expired"
              : r.status === 404 ? "not a source file that can be edited here" : r.status === 415 ? "not UTF-8 text" : f.error;
    const kept = r?.status === 404 && !unreachable(r, f) && readKept(path);
    if (kept) showGone(path, kept.text, kept.key); // (edits kept from earlier, to a file the paper no longer has)
    return setStatus(`${path}: ${why}`, "error");
  }
  while (busy) await busy.done; // (e.g. a reload of the file open now)
  if (seq !== openSeq || (ed?.dirty && !ed.gone)) return; // (another file was asked for, or new edits came in meanwhile)
  ed = { path: f.path, base: f.text, baseVersion: f.version, dirty: false, markers: false, error: null, gone: false };
  sourceEpoch++;
  const state = CM.EditorState.create({ doc: f.text, extensions: cmExtensions });
  if (cmView) {
    cmView.setState(state);
  } else {
    $("editorHost").replaceChildren();
    cmView = new CM.EditorView({ state, parent: $("editorHost") });
  }
  $("filePick").textContent = `${f.path} ▾`;
  $("editorHint").hidden = true;
  localStorage.setItem(`latex-live:${hubBase}editorFile`, f.path);
  showSaveState();
  if (line) goToLine(line);
  offerKept(f);
}
function goToLine(line) {
  const doc = cmView.state.doc, at = doc.line(Math.min(Math.max(1, +line || 1), doc.lines)).from;
  cmView.dispatch({ selection: { anchor: at }, effects: CM.EditorView.scrollIntoView(at, { y: "center" }) });
  cmView.focus();
}

// Every change to the text: the user's own make it unsaved (and start the autosave's countdown).
// While a save or reload is on its way, they are collected, to rebase onto its answer.
function sourceEdited(update) {
  if (!update.docChanged || !ed) return;
  let own = false;
  for (const tr of update.transactions) {
    if (!tr.docChanged) continue;
    if (busy) busy.since = busy.since.compose(tr.changes);
    if (!tr.annotation(CM.Transaction.remote)) own = true;
  }
  if (!own) return;
  ed.dirty = true;
  if (!ed.gone) ed.error = null;
  if (ed.markers) ed.markers = hasConflictMarks(update.state.doc.toString());
  scheduleSave();
  showSaveState();
  keepUnsaved();
}
function scheduleSave(delay = 1500) {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => saveSource(), delay);
}
// Whether a text has git's conflict markers in it, so that it must not be saved: a line that starts
// with <<<<<<< or >>>>>>>, and while a conflict of this session is open, a ======= line more than the
// file has on disk (the markers' middle line).
const MARK_ENDS = /^(<{7}|>{7})( |$)/m, MARK_MIDDLE = /^={7}$/gm;
function hasConflictMarks(text) {
  return MARK_ENDS.test(text) || (!!conflict && (text.match(MARK_MIDDLE) || []).length > ((ed?.base || "").match(MARK_MIDDLE) || []).length);
}
// The save state, beside the file's name: "Not saved" says why in its tooltip, and a click on it
// tries again (on "Conflict", it shows the choices again).
const OFFLINE_LABELS = { unreachable: "Not saved — offline", signin: "Not saved — signed out?", server: "Not saved — retrying",
                         updating: "Owner is updating the copy — retrying" };
const OFFLINE_TITLES = {
  unreachable: "Can't reach the preview server. Your edits are kept in this browser and save by themselves once it answers; click to try now.",
  signin: "The preview answered with a web page instead of saving: your sign-in may have expired. Your edits are kept in this browser; reload the page to sign in again.",
  server: "The server could not save just now. Your edits are kept in this browser, and it tries again by itself; click to try now.",
  updating: "The owner is updating the shared copy of the paper. Your edits are kept in this browser and save by themselves in a moment.",
};
function showSaveState() {
  const label = $("saveState");
  const [text, cls, title] = !ed ? ["", "", ""] : busy?.kind === "save" ? ["Saving…", "busy", ""]
    : ed.markers ? ["Conflict", conflict ? "error conflict" : "error",
                    `There are conflict markers in the text (<<<<<<<, ======= and >>>>>>> lines): it saves again once they are gone${conflict ? ". Click for the choices." : "."}`]
    : ed.error ? [`Not saved — ${ed.error.short}`, "error retry", `Not saved: ${ed.error.why.replace(/\.$/, "")}. Click to try again.`]
    : offline && ed.dirty ? [OFFLINE_LABELS[offline], "error retry", OFFLINE_TITLES[offline]]
    : ed.dirty ? ["Unsaved", "dirty", ""] : ["Saved", "", ""];
  label.textContent = text;
  label.className = cls;
  label.title = title;
}
$("saveState").onclick = () => {
  if (!ed) return;
  if (ed.markers) return void (conflict && showConflict());
  if (!ed.error && !(offline && ed.dirty)) return;
  ed.gone = false;
  ed.error = null;
  saveSource();
};

// Save the open file (one save at a time): {path, base: the version the edits started from, text},
// and, should the server no longer know that version, its text too. The server answers with what is
// now on disk: the text sent, or that merged with changes made meanwhile, which is applied here as
// someone else's change; or a conflict (409), whose merged text, with git's markers, goes in the
// editor for the user to resolve. A save counts only once the server's own answer says so (a
// version): anything else (no answer, a page from a proxy, the owner updating the copy) keeps the
// edits unsaved, to send again; a refusal says why.
async function saveSource({ keepalive = false } = {}) {
  clearTimeout(saveTimer);
  if (!ed || !cmView || ed.gone) return;
  if (busy) {
    await busy.done;
    return saveSource({ keepalive });
  }
  clearTimeout(retryTimer);
  const target = ed, text = cmView.state.doc.toString();
  if (!ed.dirty) return showSaveState();
  // Nothing to save (e.g. a conflict resolved to the file as it is, or an undo back to it), unless a save
  // may have reached the server unanswered: the file may then hold that save's text, so this goes too.
  if (text === ed.base && !ed.uncertain) {
    ed.dirty = false;
    if (!hasConflictMarks(text)) endConflict();
    keepUnsaved(true);
    return showSaveState();
  }
  if (hasConflictMarks(text)) {
    ed.markers = true;
    return showSaveState();
  }
  let done;
  busy = { kind: "save", since: CM.ChangeSet.empty(text.length), done: new Promise((resolve) => (done = resolve)) };
  lastSaveAt = Date.now();
  showSaveState();
  target.uncertain = true; // (until the server's answer says what the file holds)
  try {
    let body = { path: target.path, base: target.baseVersion, text, editor: saveId };
    if (keepalive && fitsKeepalive({ ...body, base_text: target.base })) body = { ...body, base_text: target.base }; // (no second try once the page is gone)
    let r = await postJSON("file", body, keepalive);
    let res = await readBody(r);
    if (r.status === 409 && res.stale && !("base_text" in body)) { // (the server no longer has the text these edits started from)
      r = await postJSON("file", { ...body, base_text: target.base }, keepalive);
      res = await readBody(r);
    }
    const trouble = signInPage(r, res) || (r.ok && typeof res.version !== "string") ? "signin" : r.status === 503 && res.retry ? "updating"
                  : unreachable(r, res) ? "unreachable" : r.status >= 500 ? "server" : null;
    if (trouble) throw Object.assign(new Error(res.error || `save failed (${r.status})`), { trouble });
    if (r.status === 404 && (await stillListed(target.path))) throw Object.assign(new Error(res.error), { trouble: "server" }); // (not gone after all)
    backOnline();
    const { since } = busy;
    busy = null;
    if (r.ok) wrote(); // (a build comes: behind)
    if (ed !== target) return;
    if (r.ok) {
      const now = typeof res.text === "string" ? res.text : text;
      if (now === text || applyRemote(text, now, since)) { // (merged with changes made meanwhile)
        Object.assign(ed, { base: now, baseVersion: res.version, dirty: !since.empty, markers: false, error: null, uncertain: false });
      } else { // (edits typed meanwhile touch the lines merged in: the server merges them, on the next save)
        Object.assign(ed, { base: text, baseVersion: null, dirty: true, markers: false, error: null, uncertain: false });
      }
      sourceEpoch++;
      endConflict();
      hideNotice("refused");
    } else if (r.status === 409 && res.conflict && typeof res.merged === "string") {
      if (applyRemote(text, res.merged, since)) {
        conflict = { by: typeof res.by === "string" && res.by ? res.by : null };
        Object.assign(ed, { base: res.text, baseVersion: res.version, dirty: true, markers: true, error: null, uncertain: false });
        sourceEpoch++;
        if (!editorShown()) showEditor(true, { focus: false }); // (e.g. the save on closing the pane: show what happened)
        showConflict();
      } else { // (typed meanwhile in the lines that conflict: asked again, with those edits)
        ed.dirty = true;
      }
    } else {
      refuseSave(r.status, res.error || `failed (${r.status})`);
    }
  } catch (err) {
    console.warn(err);
    if (ed === target) goOffline(err.trouble || "unreachable");
  } finally {
    if (busy?.kind === "save") busy = null;
    done();
    showSaveState();
    keepUnsaved(true);
    if (ed?.dirty && !ed.markers && !ed.error && !offline) scheduleSave();
  }
}
// Whether the server still lists a file whose save it answered with 404: then it is not gone from the
// paper, it just wasn't saved this once (e.g. a build was rewriting the list just then).
async function stillListed(path) {
  const r = await fetch("files", { cache: "no-store" }).catch(() => null);
  const list = r?.ok ? await readBody(r) : null;
  return !Array.isArray(list?.files) || list.files.some((f) => f.path === path);
}
// A save that failed but may work later: sent again (soon while the owner updates the copy, else
// backing off), and from the next status poll once the server answers again. After 30 s of that, the
// notice above the text says what is going on, with the text to copy.
function goOffline(why) {
  if (!offline) offlineSince = Date.now();
  offline = why;
  retryTimer = setTimeout(() => saveSource(), why === "updating" ? 2000 : retryDelay);
  if (why !== "updating") retryDelay = Math.min(2 * retryDelay, 30000);
  if (why === "signin") setStatus(SIGNED_OUT, "error");
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => {
    if (offline && ed?.dirty && !ed.gone) showNotice("unsaved", OFFLINE_NOTICES[offline](ed.path), { copy: cmView.state.doc.toString() });
  }, Math.max(0, offlineSince + 30000 - Date.now()));
}
const OFFLINE_NOTICES = {
  unreachable: (path) => `Still can't reach the preview server, so your edits to ${path} are not saved. They save by themselves once it ` +
    "answers, and this browser keeps them meanwhile. If you were signed out, copy them from below before you reload the page:",
  signin: (path) => `The preview keeps answering with a web page instead of saving ${path}: your sign-in may have expired. This browser ` +
    "keeps your edits, and offers to restore them after you reload the page to sign in again; to be safe, copy them from below first:",
  server: (path) => `The server could not save ${path} for a while; it keeps trying. This browser keeps your edits meanwhile, and they are below to copy:`,
  updating: (path) => `${ownerName()} is updating the shared copy of the paper; your edits to ${path} save by themselves once that is done. ` +
    "This browser keeps them meanwhile, and they are below to copy:",
};
function backOnline() {
  offline = null;
  retryDelay = 2000;
  clearTimeout(noticeTimer);
  hideNotice("unsaved");
}
// A save that the server refused (sending it again as it is won't help): the label says why, and the
// text stays, unsaved. Without access (403), or with the file gone from the paper (404), the notice
// above the text also gives it to copy.
const REFUSALS = { 403: "no access", 404: "file not in the paper", 413: "file too large", 415: "not UTF-8 text", 422: "couldn't merge", 409: "the file keeps changing" };
function refuseSave(status, why) {
  ed.error = { short: REFUSALS[status] || "refused", why };
  setStatus(`${ed.path} not saved: ${why}`, "error");
  if (status === 404) {
    ed.gone = true;
    showGone(ed.path, cmView.state.doc.toString());
  } else if (status === 403) {
    showNotice("refused", `The preview refused to save ${ed.path} (${why}). This browser keeps your edits; copy them from below before you ` +
                          `leave, and ask ${ownerName()} if you should still have access:`, { copy: cmView.state.doc.toString() });
  }
}
function showGone(path, text, key = keptKey(path)) {
  showNotice("gone", `${path} is no longer part of the paper (it was renamed or removed, or is no longer \\input), so your edits to it ` +
                     "can't be saved there. Copy them from below; you can open another file meanwhile.", { copy: text, onClose: () => dropKept(path, key) });
}
// Save now, and wait for it (before a file or document switch): whether all is saved then (a file
// gone from the paper counts: its text is in the notice, to copy).
async function flushSource() {
  for (let i = 0; i < 3 && ed && !ed.gone && (busy || ed.dirty) && !ed.markers && !(i && (ed.error || offline)); i++) {
    await (busy ? busy.done : saveSource());
  }
  return !ed || ed.gone || (!busy && !ed.dirty);
}
// The open file moved on, on disk (someone else saved, or a note went in): reload it in place; with
// edits not saved yet, save them now instead (the server merges). Also, once the server answers
// again, unsaved edits go at once.
function sourceStatus(source, epoch) {
  if (!ed || busy || ed.gone) return;
  if (ed.dirty && (offline === "unreachable" || offline === "signin")) { // (at most every 2 s, should saves keep failing still)
    if (Date.now() - lastSaveAt > 2000) saveSource();
    return;
  }
  if (source?.path !== ed.path || source.version === ed.baseVersion || epoch !== sourceEpoch) return;
  if (!ed.dirty) reloadSource();
  else if (!ed.markers && !ed.error && !offline) saveSource();
}
async function reloadSource() {
  const target = ed, from = cmView.state.doc.toString();
  let done;
  busy = { kind: "load", since: CM.ChangeSet.empty(from.length), done: new Promise((resolve) => (done = resolve)) };
  try {
    const r = await fetch(`file?${new URLSearchParams({ path: target.path })}`, { cache: "no-store" });
    const f = await readBody(r);
    if (!r.ok || typeof f.text !== "string" || typeof f.version !== "string") throw new Error(f.error || `reload failed (${r.status})`);
    const { since } = busy;
    busy = null;
    if (ed !== target) return;
    if (applyRemote(from, f.text, since)) { // (else edits typed meanwhile touch the lines that changed: they save against the old base, merged there)
      Object.assign(ed, { base: f.text, baseVersion: f.version, dirty: !since.empty });
      sourceEpoch++;
    }
  } catch (err) {
    console.warn(err); // (the next status poll tries again)
  } finally {
    if (busy?.kind === "load") busy = null;
    done();
    showSaveState();
    keepUnsaved(true);
    if (ed?.dirty && !ed.markers && !ed.error) scheduleSave();
  }
}
// Text from the server (to), computed from the text from, goes into the editor as someone else's
// change: mapped over the edits typed since (since), not undoable, not unsaved; the cursor stays,
// and the lines it changed light up for a moment. Unless those edits touch lines it changes, or the
// diff was too long to line up (one block of changes then): it is not applied then (false), and the
// server merges them instead.
function applyRemote(from, to, since) {
  const { changes, rough } = lineChanges(from, to);
  if (!since.empty) {
    let touches = rough;
    since.iterChangedRanges((fromA, toA) => { touches ||= !!changes.touchesRange(fromA, toA); });
    if (touches) return false;
  }
  const mapped = changes.map(since, true); // (lines inserted where the user typed go before what they typed)
  if (mapped.empty) return true;
  cmView.dispatch({ changes: mapped, annotations: [CM.Transaction.remote.of(true), CM.Transaction.addToHistory.of(false)] });
  flashChanged(mapped);
  return true;
}
// Light up, for a couple of seconds, the lines a change (from someone else) made or changed.
let flashSeq = 0;
function flashChanged(changes) {
  const doc = cmView.state.doc, lines = [];
  changes.iterChangedRanges((fromA, toA, fromB, toB) => {
    const last = doc.lineAt(Math.max(fromB, toB - 1)).number; // (an inserted line ends where the next one starts)
    for (let n = doc.lineAt(fromB).number; n <= last && lines.length < 500; n++) lines.push(CM.remoteLine.range(doc.line(n).from));
  });
  cmView.dispatch({ effects: CM.flashLines.of(CM.Decoration.set(lines, true)) });
  const seq = ++flashSeq;
  setTimeout(() => { if (seq === flashSeq) cmView.dispatch({ effects: CM.flashLines.of(CM.Decoration.none) }); }, 2000);
}
// The text becomes text, as the user's own edit (unsaved, and undoable).
function replaceSource(text) {
  cmView.dispatch({ changes: lineChanges(cmView.state.doc.toString(), text).changes });
}
// The changes from text a to text b, line by line: {changes (a CodeMirror ChangeSet over a), rough
// (whether lineHunks gave up lining up a long run of changes, and replaces it whole)}.
function lineChanges(a, b) {
  const lines = (s) => s.match(/[^\n]*\n|[^\n]+$/g) || []; // (each with its line end; the last may have none)
  const A = lines(a), B = lines(b), at = [0];
  for (const l of A) at.push(at.at(-1) + l.length);
  const hunks = lineHunks(A, B);
  return { changes: CM.ChangeSet.of(hunks.map(([a0, a1, b0, b1]) => ({ from: at[a0], to: at[a1], insert: B.slice(b0, b1).join("") })), a.length, "\n"),
           rough: !!hunks.rough };
}

// The notice above the text: a conflict and the two ways out of it, the lines that "Keep theirs" left
// out, edits that can't be saved (to copy), or edits kept from an earlier visit (to restore). One at
// a time; one that holds text found nowhere else (left out, or of a file gone from the paper) gives
// way only to a conflict. kind: "conflict", "theirs", "gone", "refused", "unsaved" or "restore".
let notice = null; // {kind, path, onClose} of the notice shown
function showNotice(kind, what, { buttons = [], copy = null, onClose = null } = {}) {
  const box = $("notice");
  if (notice && ["theirs", "gone"].includes(notice.kind) && notice.kind !== kind && kind !== "conflict") return;
  notice = { kind, path: ed?.path, onClose };
  box.querySelector(".what").replaceChildren(...[].concat(what));
  box.querySelector(".row").replaceChildren(...buttons.map(([textContent, title, onclick]) => el("button", { textContent, title, onclick })));
  box.querySelector(".copy").hidden = copy === null;
  box.querySelector("textarea").value = copy ?? "";
  box.hidden = false;
}
function hideNotice(kind = null) {
  if (!notice || (kind && notice.kind !== kind)) return;
  const { onClose } = notice;
  notice = null;
  $("notice").hidden = true;
  onClose?.();
}
$("notice").querySelector(".close").onclick = () => hideNotice();
$("notice").querySelector("textarea").onfocus = (e) => e.target.select();
$("notice").addEventListener("keydown", (e) => { if (e.key === "Escape") hideNotice(); });

// A conflict: the server could not merge (the same lines changed on both sides). The editor shows
// git's merge with both versions marked: resolve it by hand, or keep one side of every conflict.
function showConflict() {
  const code = (textContent) => el("code", { textContent });
  const own = !!conflict.by && [me.name, me.id].includes(conflict.by); // (who saved the other side is this user: a note of theirs, or another tab)
  const lead = own ? `You changed the same lines of ${ed.path} elsewhere (with a note, or in another tab) while editing it here.`
                   : `${conflict.by || "Someone else"} changed the same lines of ${ed.path} while you were editing it.`;
  showNotice("conflict", [`${lead} Both versions are in the text now: ${own ? "the other one" : "theirs"} after `,
                          code("<<<<<<< saved meanwhile"), ", yours after ", code("======="), ", up to ", code(">>>>>>> your edit"),
                          ". Keep what you want and delete all three marker lines; it saves again once they are gone. Or decide every conflict at once:"],
             { buttons: [["Keep mine", "In each conflict, keep your lines; everything else in the file stays as it is", () => keepSide("mine")],
                         ["Keep theirs", "In each conflict, keep their lines; your other edits stay (your lines from the conflicts are shown to copy)", () => keepSide("theirs")]] });
  const first = cmView.state.doc.toString().search(/^<{7}/m); // (the first conflict in view)
  if (first >= 0) cmView.dispatch({ effects: CM.EditorView.scrollIntoView(first, { y: "nearest" }) });
  setStatus(`${ed.path}: edits conflict, see the source pane`, "error");
}
function endConflict() { // (resolved: the markers are gone, and the text saved)
  if (!conflict) return;
  conflict = null;
  hideNotice("conflict");
}
// The conflicts git's merge left in a text: <<<<<<< saved meanwhile, their lines, (||||||| base and
// its lines, in git's diff3 style), =======, the user's lines, >>>>>>> your edit. Each as {from, to}
// (character offsets of the whole block) and its two sides' lines; a block whose marker lines were
// edited is not one any more (it stays for the user to resolve by hand).
function conflictBlocks(text) {
  const blocks = [];
  let block = null, at = 0;
  for (const line of text.match(/[^\n]*\n|[^\n]+$/g) || []) {
    const bare = line.replace(/\n$/, "");
    if (/^<{7}( |$)/.test(bare)) block = { from: at, part: "theirs", theirs: "", mine: "" }; // (and an unfinished one before it is dropped)
    else if (block?.part === "theirs" && /^\|{7}( |$)/.test(bare)) block.part = "base";
    else if (block && block.part !== "mine" && bare === "=======") block.part = "mine";
    else if (block?.part === "mine" && /^>{7}( |$)/.test(bare)) { blocks.push({ ...block, to: at + line.length }); block = null; }
    else if (block && block.part !== "base") block[block.part] += line;
    at += line.length;
  }
  return blocks;
}
// Keep one side of every conflict ("mine" or "theirs") in the text as it is now: everything else in
// it, merged from the others or typed since, stays. Then it saves as usual (against the version the
// conflict came from: the server merges anything saved since). The lines "theirs" leaves out are
// shown, to copy.
async function keepSide(side) {
  if (!conflict || !ed) return;
  while (busy) await busy.done;
  if (!conflict || !ed?.markers) return;
  const path = ed.path, blocks = conflictBlocks(cmView.state.doc.toString());
  if (!blocks.length) return setStatus(`${path}: no complete conflict left to decide; resolve the rest by hand`, "error");
  cmView.dispatch({ changes: blocks.map((b) => ({ from: b.from, to: b.to, insert: b[side] })) });
  const left = hasConflictMarks(cmView.state.doc.toString()); // (blocks whose marker lines were edited)
  if (side === "theirs") {
    showNotice("theirs", `${path}: in each conflict, their lines are kept; your other edits stay. Your lines from the conflicts are below, to copy what you need:`,
               { copy: blocks.map((b) => b.mine).join("\n") });
  } else if (!left) {
    hideNotice("conflict");
  }
  setStatus(left ? `${path}: some conflicts have edited marker lines; resolve those by hand` : `${path}: kept ${side === "mine" ? "your" : "their"} lines in each conflict`,
            left ? "error" : "");
  await saveSource();
}

let keepTimer = null;
function keepUnsaved(now = false) {
  clearTimeout(keepTimer);
  if (!now) return void (keepTimer = setTimeout(() => keepUnsaved(true), 400)); // (while typing: at most this often)
  if (!ed || !cmView) return;
  const text = cmView.state.doc.toString();
  try {
    if (ed.dirty) localStorage.setItem(keptKey(ed.path), JSON.stringify({ path: ed.path, base: ed.base, baseVersion: ed.baseVersion, text,
                                                                          time: Date.now(), ...(conflict ? { conflict } : {}) }));
    else dropKept(ed.path);
  } catch (err) {
    console.warn(err); // (past the storage quota: the text is still in the editor, and in the notice to copy)
  }
  const area = $("notice").querySelector("textarea");
  if (["unsaved", "refused", "gone"].includes(notice?.kind) && notice.path === ed.path && document.activeElement !== area) area.value = text;
}
// A file just opened, with edits kept from an earlier visit that it doesn't have: offer them back.
function offerKept(f) {
  const kept = readKept(f.path);
  if (!kept) return;
  if (kept.text === f.text) return dropKept(f.path, kept.key); // (they were saved after all, e.g. by the save on leaving the page)
  const when = new Date(kept.time).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  showNotice("restore", `This browser kept edits to ${f.path} from ${when} that were not saved. Restore them? They are merged with what was saved since.`,
             { buttons: [["Restore", "Put them back in the text, and save them merged with the changes saved since", () => restoreKept(kept)],
                         ["Discard", "Forget them", () => { dropKept(kept.path, kept.key); hideNotice("restore"); }]] });
}
async function restoreKept(kept) {
  if (ed?.path !== kept.path) return;
  if (!(await flushSource())) return setStatus(`${kept.path}: the newer edits are not saved yet; restore once they are`, "error");
  while (busy) await busy.done;
  if (ed?.path !== kept.path) return;
  hideNotice("restore");
  conflict = kept.conflict && MARK_ENDS.test(kept.text) ? { by: kept.conflict.by || null } : null;
  // The edits, against what they started from, saved under a new name: the newer edits just saved are
  // someone else's change to them (merged, or a conflict where they overlap), not a save of theirs whose
  // answer got lost (save_file would then take the draft as typed on from it, and overwrite them).
  saveId = newSaveId();
  Object.assign(ed, { base: kept.base, baseVersion: kept.baseVersion ?? null });
  sourceEpoch++;
  replaceSource(kept.text);
  keepUnsaved(true); // (kept as this page's edits now, before the earlier page's copy goes)
  if (kept.key !== keptKey(kept.path)) dropKept(kept.path, kept.key);
  if (conflict) {
    ed.markers = true;
    showConflict();
  }
  saveSource();
}

// The file picker: the editable sources (live.py /files), grouped by folder, with a filter.
async function showFileMenu(open) {
  $("fileMenu").hidden = !open;
  if (!open) return;
  $("fileFilter").value = "";
  renderFileList();
  $("fileFilter").focus();
  const r = await fetch("files", { cache: "no-store" }).catch(() => null);
  if (r?.ok) setSrcFiles(await r.json());
  if (!$("fileMenu").hidden) renderFileList(r?.ok ? null : "The list of files did not load.");
}
function renderFileList(problem = null) {
  const query = $("fileFilter").value.trim().toLowerCase();
  const files = (srcFiles?.files || []).filter((f) => f.path.toLowerCase().includes(query));
  const dirOf = (path) => path.slice(0, path.lastIndexOf("/") + 1);
  const rows = [];
  for (const dir of [...new Set(files.map((f) => dirOf(f.path)))].sort()) { // (the paper's root first, its main file on top)
    if (dir) rows.push(el("div", { className: "dir", textContent: dir }));
    for (const f of files.filter((g) => dirOf(g.path) === dir)) {
      const row = el("div", { className: `f${dir ? " sub" : ""}${f.path === ed?.path ? " current" : ""}`, textContent: f.path.slice(dir.length),
                              title: `${f.path} (${Math.max(1, Math.round(f.size / 1024))} KB)` });
      row.onclick = async () => { showFileMenu(false); await openFile(f.path); cmView?.focus(); };
      rows.push(row);
    }
  }
  rows.find((r) => r.classList.contains("f"))?.classList.add("sel");
  const empty = problem || (srcFiles ? "No file matches." : "Loading…");
  $("fileList").replaceChildren(...(rows.length ? rows : [el("div", { className: "empty", textContent: empty })]));
}
$("fileFilter").oninput = () => renderFileList();
$("fileFilter").addEventListener("keydown", (e) => {
  const rows = [...$("fileList").querySelectorAll(".f")], i = rows.findIndex((r) => r.classList.contains("sel"));
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    const next = rows[Math.min(rows.length - 1, Math.max(0, i + (e.key === "ArrowDown" ? 1 : -1)))];
    rows[i]?.classList.remove("sel");
    next?.classList.add("sel");
    next?.scrollIntoView({ block: "nearest" });
  } else if (e.key === "Enter") {
    e.preventDefault();
    rows[Math.max(0, i)]?.click();
  } else if (e.key === "Escape") {
    e.preventDefault();
    showFileMenu(false);
    cmView?.focus();
  }
});
$("filePick").onclick = () => showFileMenu($("fileMenu").hidden);
$("editBtn").onclick = () => showEditor(!editorShown());
$("editorClose").onclick = () => showEditor(false);
$("editorHint").querySelector(".close").onclick = () => { $("editorHint").hidden = true; };
$("showInPdf").onclick = () => {
  if (ed && cmView) showSource(ed.path, cmView.state.doc.lineAt(cmView.state.selection.main.head).number);
};
// Edits not saved yet: the browser asks before the page goes, and hiding or leaving it saves them
// (and keeps them in this browser at once, should that save not go through).
window.addEventListener("beforeunload", (e) => {
  if (ed && (ed.dirty || busy?.kind === "save")) { e.preventDefault(); e.returnValue = ""; }
});
window.addEventListener("pagehide", () => {
  if (!ed?.dirty) return;
  keepUnsaved(true);
  if (!busy) saveSource({ keepalive: true });
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden || !ed?.dirty) return;
  keepUnsaved(true);
  saveSource();
});

// Outline, rebuilt on each reload: the PDF's bookmarks (hyperref), or else the contents entries
// from live.py (e.g. this ICLR draft, whose restored \addcontentsline skips hyperref). Headings show
// their numbers from the build (live.py), appendix letters included.
// Each heading shows how many pages its section takes (up to the end of the main text for the
// last main-text section).
let outlineRows = [];
async function renderOutline() {
  outlineRows = [];
  const build = (items, depth) => {
    const ul = el("ul");
    for (const item of items) {
      const li = el("li");
      li.classList.toggle("minor", !!item.minor);
      const pp = el("span", { className: "pp" });
      const span = el("span", { title: item.title }, ...(item.num ? [el("span", { className: "num", textContent: item.num })] : []),
                      el("span", { className: "t", textContent: item.title }), pp);
      span.onclick = () => (item.dest ? linkService.goToDestination(item.dest) : goToHeading(item));
      outlineRows.push({ item, depth, pp });
      li.append(span);
      if (item.items.length) li.append(build(item.items, depth + 1));
      ul.append(li);
    }
    return ul;
  };
  const d = doc;
  const entries = await (await fetch(`outline/${encodeURIComponent(shownVariant === "clean" ? cleanFile : file)}`)).json().catch(() => []);
  let outline = await d.getOutline();
  if (d !== doc) return;
  if (outline?.length) numberBookmarks(outline, entries);
  else outline = nest(entries);
  const nav = $("outline");
  const scroll = nav.scrollTop;
  nav.replaceChildren(outline.length ? build(outline, 0) : el("div", { className: "empty", textContent: "No outline: the PDF has no bookmarks and the build no .toc." }));
  nav.scrollTop = scroll;
  fillSectionPages();
}
async function fillSectionPages() {
  const d = doc, r = displayedResult(), rows = outlineRows;
  if (!r || !rows.length) return;
  const pos = []; // page index + fraction of the text area, found in reading order
  let from = 0;
  for (const { item } of rows) {
    if (item.minor) { pos.push(null); continue; } // (run-in paragraph titles: too generic to find reliably)
    const p = await headingPos(d, item, from).catch(() => null);
    if (p?.found) from = p.index;
    pos.push(p?.found ? p.index + areaFraction(p.top, p.x) : null);
  }
  if (d !== doc || rows !== outlineRows) return;
  const mainEnd = r.page - 1 + r.fill;
  rows.forEach((row, i) => {
    row.pp.textContent = "";
    if (row.item.minor || pos[i] == null) return;
    let j = i + 1; // the next heading at the same or a higher level
    while (j < rows.length && (rows[j].depth > row.depth || pos[j] == null)) j++;
    let end = j < rows.length ? pos[j] : d.numPages;
    const main = pos[i] < mainEnd;
    if (main && end > mainEnd) end = mainEnd;
    const pages = end - pos[i];
    if (pages < 0.05) return;
    row.pp.textContent = pages.toFixed(1);
    row.pp.title = `≈${pages.toFixed(1)} pages${main ? " of main text" : ""}`;
  });
}

// Bookmarks get their headings' numbers: matched by destination, else by title in reading order
// (unless the title already starts with it, as with hyperref's bookmarksnumbered).
function numberBookmarks(items, entries) {
  const byDest = new Map();
  for (const e of entries) if (e.dest && !byDest.has(e.dest)) byDest.set(e.dest, e); // (later entries without an anchor of their own reuse the last one)
  const key = (s) => s.replace(/\s+/g, " ").trim().toLowerCase();
  let from = 0;
  const walk = (list) => {
    for (const item of list) {
      let e = typeof item.dest === "string" ? byDest.get(item.dest) : null;
      if (!e) {
        const i = entries.findIndex((x, k) => k >= from && key(x.title) === key(item.title));
        if (i >= 0) [e, from] = [entries[i], i + 1];
      }
      if (e?.num && !item.title.startsWith(`${e.num} `)) item.num = e.num;
      walk(item.items || []);
    }
  };
  walk(items);
}

// Flat contents entries ({level, num, title, page, dest}) to a bookmark-style tree.
function nest(entries) {
  const root = { level: -1, items: [] }, stack = [root];
  for (const e of entries) {
    while (stack.at(-1).level >= e.level) stack.pop();
    const item = { ...e, minor: e.level >= 4, items: [] };
    stack.at(-1).items.push(item);
    stack.push(item);
  }
  return root.items;
}

// A .toc entry without an anchor: go to its page label, then to the line that holds its title.
async function goToHeading(entry) {
  const p = await headingPos(doc, entry);
  if (p) linkService.goToDestination([p.index, { name: "XYZ" }, null, p.H - p.top + 4, null]);
}
// Where a heading is: {index (page), top (points from the page top), H, found}: on its labeled
// page, or nearby (not before `from`, the previous heading's page) should the label be stale.
async function headingPos(d, entry, from = null) {
  if (entry.dest) {
    const dest = typeof entry.dest === "string" ? await d.getDestination(entry.dest) : entry.dest;
    if (!Array.isArray(dest)) return null;
    const index = typeof dest[0] === "object" && dest[0] ? await d.getPageIndex(dest[0]) : dest[0];
    const H = (await d.getPage(index + 1)).view[3];
    const xyz = dest[1]?.name === "XYZ";
    return { index, H, top: xyz && dest[3] != null ? H - dest[3] : area.top, x: xyz ? dest[2] : null, found: true };
  }
  const labels = await d.getPageLabels();
  const index = labels?.includes(entry.page) ? labels.indexOf(entry.page) : +entry.page - 1;
  if (!(index >= 0 && index < d.numPages)) return null;
  const [, titleNumber, text] = entry.title.match(/^(?:([A-Z]?[\d.]*\d|[A-Z])\s+)?(.*)$/);
  const number = entry.num || titleNumber;
  const key = norm(text).slice(-20); // the end of the title: its start may be a symbol or macro
  const pages = [index, index - 1, index + 1, index - 2];
  for (const i of pages.filter((i) => i >= (from ?? 0) && i < d.numPages)) {
    const { H, lines } = await textLines(d, i + 1);
    const line = (key && lines.find((l) => norm(l.str).includes(key))) ||
                 (number && lines.find((l) => l.str.startsWith(`${number} `)));
    if (line) return { index: i, H, top: line.top - 10, x: line.left, found: true };
  }
  return { index, H: (await d.getPage(index + 1)).view[3], top: 0, found: false };
}

// Main-text length, against the page limit when there is one. Main text runs from the page labeled
// "1" (a draft ToC may come first) up to the references or an earlier unnumbered statement (AI
// use, ethics, reproducibility, impact, acknowledgments), which venues such as ICLR and ICML do not
// count; without references (refs null), to the end of the document.
const UNCOUNTED = /^(ai use|llm usage|use of large language models|ethics statement|reproducibility statement|impact statement|acknowledge?ments?)\b/i;
async function checkPages(d, refs, limit) {
  const labels = await d.getPageLabels();
  const first = Math.max(0, labels?.indexOf("1") ?? 0) + 1;
  let end = refs ? { page: refs.page, top: refs.y, x: refs.x } : { page: d.numPages + 1, top: area.top }; // (then: to the last line)
  for (let p = Math.max(first, end.page - 2); refs && p <= refs.page; p++) {
    const { lines } = await textLines(d, p);
    const heading = lines.find((l) => l.str.length < 60 && UNCOUNTED.test(l.str) && (p < refs.page || l.top < refs.y));
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
let pageResult = null, cleanResult = null;
function renderPages() {
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
let cleanShown = null, notesShown = null, measuring = false;
const displayedResult = () => (shownVariant === "clean" ? cleanResult : pageResult);
function setResult(variant, version, r) {
  if (variant === "clean") { cleanResult = r; cleanShown = version; } else { pageResult = r; notesShown = version; }
}
async function measureVariant(variant, info) {
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
async function renderThumbs() {
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

// Notes (\todo and per-author note macros) from live.py, in reading order. Click one to see
// it in the PDF; click its file:line to open it in the editor.
const AUTHOR_COLORS = { TODO: "#ef5350", EB: "#26a69a", CH: "#4fc3f7", SX: "#bcaaa4", Hu: "#ce93d8", MR: "#f06292",
                        JV: "#9575cd", Shengyi: "#f8bbd0", Daniel: "#66bb6a" };
const authorColor = (a) => AUTHOR_COLORS[a] || `hsl(${[...a].reduce((h, c) => h * 31 + c.charCodeAt(0), 7) % 360} 60% 70%)`;
const hiddenAuthors = new Set(JSON.parse(localStorage.getItem(`latex-live:${hubBase}hiddenAuthors`) || "[]"));
let noteList = [];
async function loadNotes() {
  noteList = await (await fetch(`notes/${encodeURIComponent(file)}`)).json();
  document.querySelector("#tabs [data-tab=notes] .n").textContent = noteList.length || "";
  renderNotes();
}
function renderNotes() {
  const panel = $("notes");
  const scroll = panel.scrollTop;
  const counts = {};
  for (const n of noteList) counts[n.author] = (counts[n.author] || 0) + 1;
  const filters = el("div", { className: "filters" });
  for (const [author, count] of Object.entries(counts)) {
    const chip = el("span", { className: `chip${hiddenAuthors.has(author) ? " off" : ""}`, textContent: `${author} ${count}`,
                              title: "Show or hide this author's notes" });
    chip.style.background = authorColor(author);
    chip.onclick = () => {
      hiddenAuthors.has(author) ? hiddenAuthors.delete(author) : hiddenAuthors.add(author);
      localStorage.setItem(`latex-live:${hubBase}hiddenAuthors`, JSON.stringify([...hiddenAuthors]));
      renderNotes();
      drawAllMargins();
    };
    filters.append(chip);
  }
  const items = noteList.filter((n) => !hiddenAuthors.has(n.author)).map((n) => {
    const who = el("span", { className: "chip who", textContent: n.author });
    who.style.background = authorColor(n.author);
    const where = opensAt(el("span", { className: "where", textContent: `${n.file.replace(/^.*\//, "")}:${n.line}` }), `${n.file}:${n.line}`);
    where.onclick = (e) => { e.stopPropagation(); openSource(n.file, n.line); };
    const row = el("div", { className: "note", title: "Show in the PDF" }, who, ...(canOpenFile(n.file) ? [where] : []),
                   el("div", { className: "text", textContent: n.text || "(empty)" }));
    const tools = noteTools(n);
    if (tools) who.after(tools);
    row.onclick = () => showSource(n.file, n.line);
    return row;
  });
  panel.replaceChildren(...(noteList.length ? [filters, ...items] : [el("div", { className: "empty", textContent: "No inline notes in the source." })]));
  panel.scrollTop = scroll;
}

// Figures and tables (live.py /floats), each cropped from its page as it scrolls into view; click one
// to go there. The chips show only figures or only tables.
let floatsKey = null, floatObserver = null, floatKind = localStorage.getItem("latex-live:floatKind") || "all";
async function renderFloats() {
  const key = `${docSeq}/${floatKind}`;
  if (floatsKey === key) return;
  floatsKey = key;
  const d = doc, panel = $("figures");
  const list = await (await fetch(`floats/${encodeURIComponent(shownVariant === "clean" ? cleanFile : file)}`)).json();
  if (d !== doc || floatsKey !== key) return;
  floatObserver?.disconnect();
  floatObserver = new IntersectionObserver((seen) => {
    for (const e of seen) {
      if (!e.isIntersecting || e.target.dataset.done) continue;
      e.target.dataset.done = "1";
      drawFloat(d, e.target, list[+e.target.dataset.i]).catch((err) => console.warn(err));
    }
  }, { root: panel, rootMargin: "400px" });
  const count = (kind) => list.filter((f) => f.kind === kind).length;
  const filters = el("div", { className: "filters" });
  for (const [kind, text, color] of [["all", `All ${list.length}`, "#ccc"], ["figure", `Figures ${count("figure")}`, "#9cf"],
                                     ["table", `Tables ${count("table")}`, "#fc9"]]) {
    const chip = el("span", { className: `chip${floatKind === kind ? "" : " off"}`, textContent: text });
    chip.style.background = color;
    chip.onclick = () => { floatKind = kind; localStorage.setItem("latex-live:floatKind", kind); renderFloats(); };
    filters.append(chip);
  }
  const scroll = panel.scrollTop;
  const cards = [];
  list.forEach((f, i) => {
    if (floatKind !== "all" && f.kind !== floatKind) return;
    const name = `${f.kind === "table" ? "Table" : "Figure"} ${f.num ?? "?"}`;
    const where = opensAt(el("span", { className: "where", textContent: `${f.file.replace(/^.*\//, "")}:${f.line}` }), `${f.file}:${f.line}`);
    where.onclick = (e) => { e.stopPropagation(); openSource(f.file, f.line); };
    const crop = el("div", { className: "crop" });
    const [x0, y0, x1, y1] = f.box;
    crop.style.aspectRatio = `${x1 - x0 + 8} / ${y1 - y0 + 8}`;
    const card = el("div", { className: "float", title: `${name}: show in the PDF` },
                    el("div", { className: "head" }, el("b", { textContent: name }), ...(canOpenFile(f.file) ? [where] : []),
                       el("span", { className: "pg", textContent: `p. ${f.label ?? f.page}` })),
                    crop, el("div", { className: "cap", textContent: f.caption }));
    card.dataset.i = i;
    card.onclick = () => {
      const H = viewer.getPageView(f.page - 1)?.pdfPage?.view[3] ?? area.H;
      reveal(f.page, [x0 - 2, H - y1 - 2, x1 + 2, H - y0 + 2]);
    };
    cards.push(card);
    floatObserver.observe(card);
  });
  panel.replaceChildren(filters, ...(cards.length ? cards : [el("div", { className: "empty", textContent: list.length ? "None of this kind." : "No figures or tables found in the build." })]));
  panel.scrollTop = scroll;
}
async function drawFloat(d, card, f) {
  const page = await d.getPage(f.page);
  const [x0, y0, x1, y1] = f.box, pad = 4;
  const crop = card.querySelector(".crop");
  const scale = crop.clientWidth * (window.devicePixelRatio || 1) / (x1 - x0 + 2 * pad);
  const viewport = page.getViewport({ scale, offsetX: -(x0 - pad) * scale, offsetY: -(y0 - pad) * scale });
  const canvas = el("canvas", { width: Math.ceil((x1 - x0 + 2 * pad) * scale), height: Math.ceil((y1 - y0 + 2 * pad) * scale) });
  await page.render({ canvasContext: canvas.getContext("2d"), viewport, annotationMode: pdfjsLib.AnnotationMode.DISABLE }).promise;
  crop.replaceChildren(canvas);
}

// Margin notes: in the notes-free view, each note as a bubble in a gutter right of its page (with
// two columns, a left-column note's in a gutter left of it), wired to a caret where it sits in the
// text. live.py (/margin) gives, per note, where its source line landed (SyncTeX) and the typeset
// words around it; those words, found in the page text, put the caret between two words. Hover a
// bubble or caret to follow the wire; click to open the source.
let marginOn = localStorage.getItem("latex-live:margin") !== "0";
let marginAnchors = new Map(); // page -> [{note, x, top}] (points from the page's top-left, baseline)
function resetMargin() { marginAnchors = new Map(); } // (a new document: loadMargin anchors its notes)
const leftOfPage = (a) => columnMid() !== null && columnAt(a.x) === 1; // a note in the left column

// The gutters the notes need: right, and left too for notes in a left column.
function fitGutters() {
  const all = [...marginAnchors.values()].flat();
  setGutter(marginOn && all.some((a) => !leftOfPage(a)), marginOn && all.some(leftOfPage));
}
function showMarginButton() {
  const b = $("marginBtn");
  b.hidden = isDiff || shownVariant !== "clean";
  b.classList.toggle("on", marginOn);
  b.title = marginOn ? "Showing the notes as bubbles in the margin; click to hide them"
                     : "Show the notes as bubbles in the margin, wired to where they sit";
}
$("marginBtn").onclick = () => {
  marginOn = !marginOn;
  localStorage.setItem("latex-live:margin", marginOn ? "1" : "0");
  showMarginButton();
  fitGutters();
  drawAllMargins();
};

async function loadMargin(d) {
  const notes = await (await fetch(`margin/${encodeURIComponent(cleanFile)}`)).json();
  const anchors = new Map();
  for (const n of notes) {
    const a = await anchorNote(d, n);
    if (d !== doc) return; // a newer build arrived meanwhile
    if (!anchors.has(a.page)) anchors.set(a.page, []);
    anchors.get(a.page).push({ note: n, ...a });
  }
  marginAnchors = anchors;
  fitGutters();
  drawAllMargins();
}

// Where a note's caret goes: right after its preceding words (or before its following ones) in
// the page text near where SyncTeX put its line, else on the whole page; failing both, at the end
// (or start) of that line.
async function anchorNote(d, n) {
  const pages = [...new Set(n.points.map((p) => p[0]))];
  if (n.side !== "after") pages.reverse(); // a note after its line's text: the last page it reaches
  const before = norm(n.before.normalize("NFKC")), after = norm(n.after.normalize("NFKC"));
  const trailing = n.before.trimEnd().match(/[^\p{L}\p{N}\s]*$/u)[0].length; // the caret goes after "word)." too
  // Longest context first; shorter keys only near the note's own lines, where a short match is unlikely to be wrong.
  for (const [len, bands] of [[30, [true, false]], [14, [true]]]) {
    const b = before.slice(-len), a = after.slice(0, len);
    for (const band of bands) {
      // A float's caption can land a page away from where SyncTeX puts a note next to it.
      const spill = [Math.min(...pages) - 1, Math.max(...pages) + 1].filter((p) => p >= 1 && p <= d.numPages);
      for (const page of band ? pages : len === 30 ? [...pages, ...spill] : []) {
        const ys = n.points.filter((p) => p[0] === page).map((p) => p[2]);
        const col = columnAt((n.points.find((p) => p[0] === page) || n.points[0])[1]);
        const { lines: all } = await textLines(d, page);
        const lines = all.filter((l) => !l.col || l.col === col || !band); // (with two columns: the note's own)
        const near = band ? lines.filter((l) => l.top > Math.min(...ys) - 14 && l.top < Math.max(...ys) + 14) : lines;
        let text = "";
        const at = []; // per character of text: [x0, x1, top]
        for (const l of near) {
          for (const p of l.parts) {
            const chars = [...p.str];
            chars.forEach((c, i) => {
              const x0 = p.x + p.w * i / chars.length, x1 = p.x + p.w * (i + 1) / chars.length, keys = norm(c.normalize("NFKC"));
              for (const k of keys) { text += k; at.push([x0, x1, l.top, []]); }
              if (!keys && c.trim() && at.at(-1)?.[2] === l.top) at.at(-1)[3].push(x1); // punctuation right after it
            });
          }
        }
        let i;
        if (b.length >= 6 && (i = text.lastIndexOf(b)) >= 0) {
          const [, x1, top, punct] = at[i + b.length - 1];
          return { page, x: (trailing ? punct[trailing - 1] ?? punct.at(-1) ?? x1 : x1) + 1, top };
        }
        if (a.length >= 6 && (i = text.indexOf(a)) >= 0) {
          const [x0, , top] = at[i];
          return { page, x: x0 - 1.5, top };
        }
        if (band || !pages.includes(page)) continue;
        // The context runs across a page break: the page ends with the start of the text after the note,
        // or starts with the end of the text before it.
        for (let k = Math.min(a.length, text.length); k >= 8; k--) {
          if (text.endsWith(a.slice(0, k))) { const [x0, , top] = at[text.length - k]; return { page, x: x0 - 1.5, top }; }
        }
        for (let k = Math.min(b.length, text.length); k >= 8; k--) {
          if (text.startsWith(b.slice(-k))) { const [, x1, top] = at[k - 1]; return { page, x: x1 + 1, top }; }
        }
      }
    }
  }
  const p = n.side === "after" || n.side === "near" ? n.points[0] : n.points.at(-1);
  const { lines } = await textLines(d, p[0]);
  const line = lines.find((l) => Math.abs(l.top - p[2]) < 2 && (!l.col || l.col === columnAt(p[1])));
  const x = !line ? p[1] : n.side === "after" || n.side === "near" ? line.left - 1.5 : n.side === "before" ? line.right + 1 : p[1];
  return { page: p[0], x, top: p[2] };
}

const SVG = "http://www.w3.org/2000/svg";
function drawMargin(pageNumber) {
  const pageView = viewer.getPageView(pageNumber - 1);
  if (!pageView?.div || !pageView.pdfPage) return;
  pageView.div.querySelector(".ll-margin")?.remove();
  const list = (marginOn && shownVariant === "clean" && marginAnchors.get(pageNumber) || [])
    .filter((a) => !hiddenAuthors.has(a.note.author));
  if (!list.length) return;
  const [vx, vy, vw] = pageView.pdfPage.view;
  const cw = pageView.div.clientWidth, ch = pageView.div.clientHeight, s = cw / (vw - vx);
  const layer = el("div", { className: "ll-margin" });
  const svg = document.createElementNS(SVG, "svg"); // (wires to a left gutter overflow it)
  svg.setAttribute("width", cw + GUTTER);
  svg.setAttribute("height", ch);
  layer.append(svg);
  pageView.div.append(layer);
  const items = list.map((a) => {
    const n = a.note, color = authorColor(n.author), left = leftOfPage(a);
    const bubble = el("div", { className: `ll-bubble${left ? " left" : ""}`, title: `${n.file}:${n.line}` },
                      el("div", { className: "text" }, el("b", { textContent: n.author }), n.text || "(empty)"));
    if (canOpenFile(n.file)) opensAt(bubble, `${n.file}:${n.line}`);
    bubble.style[left ? "borderRightColor" : "borderLeftColor"] = color;
    bubble.querySelector("b").style.color = color;
    const tools = noteTools(n);
    if (tools) bubble.prepend(tools);
    const caret = el("div", { className: "caret", title: `${n.author}: ${n.text}` });
    const x = (a.x - vx) * s, y = (a.top - vy) * s;
    Object.assign(caret.style, { left: `${x}px`, top: `${y - 8.5 * s}px`, height: `${11 * s}px`, background: color });
    const path = document.createElementNS(SVG, "path");
    path.setAttribute("stroke", color);
    svg.append(path);
    layer.append(caret, bubble);
    const hot = (on) => { for (const e of [bubble, caret, path]) e.classList.toggle("hot", on); };
    for (const e of [bubble, caret]) {
      e.onmouseenter = () => hot(true);
      e.onmouseleave = () => hot(false);
      e.onclick = (ev) => { ev.stopPropagation(); if (canOpenFile(n.file)) openSource(n.file, n.line); };
    }
    return { bubble, path, x, y, s, left };
  }).sort((a, b) => a.y - b.y || a.x - b.x);
  // In each gutter, stack the bubbles in reading order, each as close to its line as the ones
  // above allow; pull them up from the page bottom if they run past it; then undo any overlap that left.
  for (const side of [items.filter((it) => !it.left), items.filter((it) => it.left)]) {
    let y = 0;
    for (const it of side) { it.h = it.bubble.offsetHeight; it.top = Math.max(it.y - 12, y); y = it.top + it.h + 4; }
    let limit = ch;
    for (const it of [...side].reverse()) { it.top = Math.max(0, Math.min(it.top, limit - it.h)); limit = it.top - 4; }
    y = 0;
    for (const it of side) { it.top = Math.max(it.top, y); y = it.top + it.h + 4; }
  }
  for (const it of items) {
    it.bubble.style.top = `${it.top}px`;
    const base = it.y + 2.5 * it.s, by = it.top + Math.min(12, it.h / 2);
    it.path.setAttribute("d", it.left ? `M${it.x},${base} H0 C-10,${base} -6,${by} -16,${by}`
                                      : `M${it.x},${base} H${cw} C${cw + 10},${base} ${cw + 6},${by} ${cw + 16},${by}`);
  }
}
const drawAllMargins = () => { for (let n = 1; n <= (doc?.numPages || 0); n++) drawMargin(n); };
eventBus.on("pagerendered", (e) => drawMargin(e.pageNumber));

// Comments: select text in the PDF, then 💬 (or press C), and a note of the author picked in the
// toolbar goes into the source right after the selected words (live.py /note); latexmk rebuilds
// and the note appears. In the margin and the sidebar, notes get ✓ (resolve: commented out under a
// "% RESOLVED <date> (<initials>): <reason>" line), others' notes ↩ (reply), your own ✎ (edit), and
// the ones written from this tab ✕ (delete, as an undo; this tab remembers them by file, macro, and text).
const written = new Set(JSON.parse(sessionStorage.getItem(`latex-live:${hubBase}written`) || "[]"));
const writtenKey = (file, macro, tex) => JSON.stringify([file, macro, tex]);
function markWritten(key, on) {
  on ? written.add(key) : written.delete(key);
  sessionStorage.setItem(`latex-live:${hubBase}written`, JSON.stringify([...written]));
}

$("whoPick").onchange = (e) => {
  setWho(e.target.value);
  localStorage.setItem(`latex-live:${hubBase}who`, who);
  renderNotes();
  drawAllMargins();
};

// The selection's end, for a new note: {page, x, y (PDF points from the page's top-left), text, rect}.
let selectionTarget = null;
function selectionInfo() {
  const sel = window.getSelection();
  if (!sel.rangeCount || sel.isCollapsed) return null;
  const range = sel.getRangeAt(0);
  const inText = (node) => (node.nodeType === Node.TEXT_NODE ? node.parentElement : node)?.closest?.(".textLayer");
  const text = sel.toString().replace(/\s+/g, " ").trim();
  // Its text's rectangles, in reading order; not a box much taller than a line (a container the
  // selection runs into, such as PDF.js's end-of-content block).
  let rects = [...range.getClientRects()].filter((r) => r.width > 0 && r.height > 0);
  const lineHeight = rects.map((r) => r.height).sort((a, b) => a - b)[rects.length >> 1];
  rects = rects.filter((r) => r.height <= 3 * lineHeight);
  if (!text || !rects.length || !inText(range.startContainer) || !inText(range.endContainer)) return null;
  const rect = rects.at(-1), cx = rect.right - 1, cy = (rect.top + rect.bottom) / 2;
  const pageDiv = [...document.querySelectorAll("#viewer .page")].find((p) => {
    const b = p.getBoundingClientRect();
    return cx >= b.left && cx <= b.right && cy >= b.top && cy <= b.bottom;
  });
  const pageView = pageDiv && viewer.getPageView(+pageDiv.dataset.pageNumber - 1);
  if (!pageView?.pdfPage) return null;
  const box = pageDiv.getBoundingClientRect();
  const [x, y] = pageView.viewport.convertToPdfPoint(cx - box.left - pageDiv.clientLeft, cy - box.top - pageDiv.clientTop);
  return { page: pageView.id, x, y: pageView.pdfPage.view[3] - y, text, rect };
}
function showCommentButton() {
  selectionTarget = isDiff || $("whoPick").hidden ? null : selectionInfo();
  const b = $("commentBtn");
  b.hidden = !selectionTarget || !$("notePop").hidden;
  if (b.hidden) return;
  const r = selectionTarget.rect;
  b.textContent = who ? `💬 ${whoLabel()}` : "💬 Comment";
  b.title = who ? "Add your note after the selected text (C)" : "Pick who you are in the toolbar first";
  Object.assign(b.style, { left: `${Math.min(r.right + 4, innerWidth - b.offsetWidth - 8)}px`, top: `${r.bottom + 4}px` });
}
container.addEventListener("mouseup", (e) => { if (e.button === 0) setTimeout(showCommentButton, 250); }); // (after a double-click's own handling)
container.addEventListener("scroll", () => { if (!$("commentBtn").hidden) showCommentButton(); }, { passive: true });
document.addEventListener("selectionchange", () => { if (window.getSelection().isCollapsed) $("commentBtn").hidden = true; });
$("commentBtn").onmousedown = (e) => e.preventDefault(); // keep the selection
$("commentBtn").onclick = () => startComment();
function startComment() {
  const t = selectionTarget;
  if (!t) return;
  if (!who) {
    setStatus("pick who you are in the toolbar (💬 Comment as…) first", "error");
    return revealControl($("whoPick")), $("whoPick").focus();
  }
  $("commentBtn").hidden = true;
  const quote = t.text.length > 50 ? `…${t.text.slice(-50)}` : t.text;
  openNotePop({ head: `${whoLabel()}, after “${quote}”`, initial: "", rect: t.rect,
                save: (text) => postNote({ action: "add", pdf: docFile, macro: who, page: t.page, x: t.x, y: t.y,
                                           clean: shownVariant === "clean", selection: t.text, text }) });
}

// The box to write a note in: save(text) returns an error message, or nothing once it is written.
let popSave = null;
function openNotePop({ head, initial, rect, save, latex = false, placeholder = "Your comment" }) {
  const pop = $("notePop"), field = pop.querySelector("textarea");
  pop.querySelector(".head").textContent = head;
  field.placeholder = placeholder;
  // (Plain-text notes are written on one line; only LaTeX keeps line breaks.)
  pop.querySelector(".hint").textContent = `Enter to save · ${latex ? "Shift+Enter for a new line · " : ""}Esc to cancel`;
  pop.querySelector(".error").textContent = "";
  pop.querySelector(".latex").hidden = !latex;
  pop.querySelector(".primary").disabled = false;
  field.value = initial;
  popSave = save;
  pop.hidden = false;
  const below = rect.bottom + 6 + pop.offsetHeight <= innerHeight;
  Object.assign(pop.style, { left: `${Math.max(8, Math.min(rect.left, innerWidth - pop.offsetWidth - 8))}px`,
                             top: `${below ? rect.bottom + 6 : Math.max(40, rect.top - pop.offsetHeight - 6)}px` });
  field.focus();
  field.setSelectionRange(field.value.length, field.value.length);
}
function closeNotePop() { $("notePop").hidden = true; popSave = null; noteSeq++; }
async function submitNotePop() {
  const pop = $("notePop"), text = pop.querySelector("textarea").value, button = pop.querySelector(".primary");
  if (!text.trim() || !popSave || button.disabled) return;
  const save = popSave;
  button.disabled = true;
  const error = await save(text);
  if (popSave !== save) return; // (the box was closed meanwhile, or opened for another note)
  button.disabled = false;
  if (error) pop.querySelector(".error").textContent = error;
  else closeNotePop();
}
$("notePop").querySelector("textarea").addEventListener("keydown", (e) => {
  e.stopPropagation(); // (not the viewer's shortcuts)
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submitNotePop(); }
  else if (e.key === "Escape") { e.preventDefault(); closeNotePop(); }
});
$("notePop").querySelector(".primary").onclick = submitNotePop;
$("notePop").querySelector(".cancel").onclick = closeNotePop;

// Write a note change (live.py /note); returns an error message, or nothing. While the owner updates
// the shared copy (503 with retry), it waits and sends it again, for a minute at most, unless the
// note box is closed meanwhile.
let noteSeq = 0; // (bumped when the note box closes: a note still waiting to go is dropped then)
async function postNote(body) {
  const seq = noteSeq, kept = "(your text is still here)";
  try {
    let r, res;
    for (let attempt = 1; ; attempt++) {
      r = await fetch("note", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).catch(() => null);
      if (!r) return `Can't reach the preview server: try again in a moment ${kept}`;
      res = await readBody(r);
      if (!(r.status === 503 && res.retry) || attempt >= 30) break;
      if (!$("notePop").hidden) $("notePop").querySelector(".error").textContent = "Owner is updating the copy — retrying…";
      setStatus("Owner is updating the copy — retrying…", "busy");
      await sleep(2000);
      if (seq !== noteSeq) return "not sent: the note box was closed";
    }
    if (signInPage(r, res)) return `The preview answered with a web page: your sign-in may have expired. Copy your text, then reload the page ${kept}`;
    if (r.status === 503 && res.retry) return `${ownerName()} is still updating the shared copy: try again in a minute ${kept}`;
    if (unreachable(r, res)) return `Can't reach the preview server: try again in a moment ${kept}`;
    if (!r.ok || typeof res.source !== "string") return res.error || `failed (${r.status})`;
    wrote(); // (a build comes: behind)
    const old = writtenKey(body.source, body.macro, body.tex);
    if (body.action === "add" || body.action === "reply") markWritten(writtenKey(res.source, res.macro, res.tex), true);
    else if (written.has(old)) {
      markWritten(old, false);
      if (body.action === "edit") markWritten(writtenKey(res.source, body.macro, res.tex), true);
    }
    const verb = { add: "note added", reply: "reply added", edit: "note updated", resolve: "note resolved",
                   delete: "note deleted" }[body.action];
    const dropped = res.dropped ? `, before the last ${res.dropped} selected word${res.dropped === 1 ? "" : "s"} (not in the source)` : "";
    setStatus(`${verb} in ${res.source.replace(/^.*\//, "")}:${res.line}${res.protected ? ", after the protected text" : ""}`
              + `${dropped}; rebuilding…`, "fresh");
    if (body.action === "add") window.getSelection().removeAllRanges();
    return null;
  } catch (err) {
    return `${err.message || err}`;
  }
}

// ✓ on every note, ↩ on others', ✎ on your own, ✕ on the ones written from this tab (none on a
// marker of protected text).
function noteTools(n) {
  if (isDiff || !who || n.protected || !me.can.comment || notSetUp()) return null;
  const target = { pdf: docFile, source: n.file, line: n.line, macro: n.macro, tex: n.tex };
  const where = `${n.file.replace(/^.*\//, "")}:${n.line}`, buttons = [];
  const button = (textContent, title, onclick) => {
    const b = el("button", { textContent, title });
    b.onclick = (e) => { e.stopPropagation(); onclick(e.target.getBoundingClientRect()); };
    buttons.push(b);
  };
  if (n.macro !== who)
    button("↩", "Reply to this note", (rect) => openNotePop({
      head: `${whoLabel()}, replying to ${n.author} (${where})`, initial: "", rect, placeholder: "Your reply",
      save: (text) => postNote({ action: "reply", ...target, who, text }) }));
  else if (n.plain != null || me.can.edit) // (a note with LaTeX in it is edited as LaTeX: editors only)
    button("✎", "Edit this note", (rect) => {
      const latex = n.plain == null; // (TeX beyond escaped characters: edited as LaTeX)
      openNotePop({ head: `Edit ${n.author} note (${where})`, initial: latex ? n.tex : n.plain, latex, rect,
                    save: (text) => postNote({ action: "edit", ...target, ...(latex ? { latex: text } : { text }) }) });
    });
  button("✓", "Resolve: comment it out under a RESOLVED line with your reason", (rect) => openNotePop({
    head: `Resolve ${n.author} note (${where}) as ${whoLabel()}: why?`, initial: "", rect,
    placeholder: "What addressed it (one line)", save: (text) => postNote({ action: "resolve", ...target, who, text }) }));
  if (written.has(writtenKey(n.file, n.macro, n.tex)))
    button("✕", "Delete this note (written from this tab)", async () => {
      if (!confirm(`Delete this ${n.author} note?\n\n${n.text}`)) return;
      const error = await postNote({ action: "delete", ...target });
      if (error) setStatus(error, "error");
    });
  return el("span", { className: "tools" }, ...buttons);
}

// Pre-submission check (live.py), plus the page-limit result from this viewer. Re-runs after each
// rebuild while the tab is open.
let checksVersion = null, checksRunning = false;
const ICONS = { ok: "✓", info: "ℹ", warn: "⚠", fail: "✗" };
async function runChecks() {
  if (checksRunning) return;
  checksRunning = true;
  checksVersion = `${buildVersion}/${shown}`;
  const panel = $("checks");
  if (!panel.querySelector(".check")) panel.replaceChildren(el("div", { className: "empty", textContent: "Checking…" }));
  try {
    const results = await (await fetch(`check/${encodeURIComponent(file)}`)).json();
    const r = cleanResult || pageResult;
    if (r?.limit) results.unshift({ title: "Page limit", level: r.cls === "over" ? "fail" : r.cls === "tight" ? "warn" : "ok",
                             detail: `main text ${r.value.toFixed(1)} of ${r.limit} pages${cleanResult ? " without the notes" : ""}: ${r.range}`,
                             items: r.cls === "over" ? [{ text: `the main text should end on page ${r.lastAllowedLabel}`, page: r.lastAllowed }] : [] });
    const shownR = displayedResult();
    if (shownR) {
      const spots = await cutSpots(doc, shownR);
      const lines = spots.reduce((n, s) => n + s.lines, 0);
      results.splice(r?.limit ? 1 : 0, 0, { title: "Space to reclaim", level: spots.length ? "info" : "ok",
        detail: spots.length ? `≈${lines} line(s) in ${spots.length} spot(s) of the main text (${shownVariant === "clean" ? "notes-free build" : "with notes"}); ✂ in the toolbar marks them`
                             : "no short last lines or large gaps in the main text",
        items: spots.map((s) => ({ text: `${s.label}: ${s.text}`, page: s.page, rect: s.rect })) });
    }
    renderChecks(results);
  } catch (e) {
    panel.replaceChildren(el("div", { className: "empty", textContent: `Check failed: ${e.message || e}` }));
  } finally {
    checksRunning = false;
  }
}
function renderChecks(results) {
  const panel = $("checks");
  const open = new Set([...panel.querySelectorAll("details[open]")].map((d) => d.dataset.title));
  const rerun = el("button", { textContent: "Run again" });
  rerun.onclick = runChecks;
  const bad = results.filter((r) => r.level === "fail" || r.level === "warn").length;
  document.querySelector("#tabs [data-tab=checks] .n").textContent = bad || "";
  const rows = results.map((r) => {
    const details = el("details", { className: "check" });
    details.dataset.title = r.title;
    details.open = open.has(r.title) || (r.level === "fail" && r.items.length > 0 && !open.size);
    details.append(el("summary", {}, el("span", { className: `icon ${r.level}`, textContent: ICONS[r.level] }),
                      el("span", { textContent: r.title }), el("span", { className: "detail", textContent: r.detail })));
    if (r.items.length) {
      details.append(el("ul", {}, ...r.items.map((it) => {
        const li = el("li", { textContent: it.text });
        if (it.page) {
          const page = el("span", { className: "where", textContent: `  PDF page ${it.page}`, title: "Go to the page" });
          page.onclick = (e) => { e.stopPropagation(); it.rect ? reveal(it.page, it.rect) : goToPage(it.page); };
          li.append(page);
          li.onclick = page.onclick;
        }
        if (it.file) {
          const where = opensAt(el("span", { className: "where", textContent: `  ${it.file.replace(/^.*\//, "")}:${it.line}` }), `${it.file}:${it.line}`);
          where.onclick = (e) => { e.stopPropagation(); openSource(it.file, it.line); };
          if (canOpenFile(it.file)) li.append(where);
          li.onclick = () => showSource(it.file, it.line, true);
        }
        if (it.open && canOpenFile(it.open)) { // a related file, e.g. the script that makes a figure
          const script = opensAt(el("span", { className: "where", textContent: `  ↗ ${it.open}` }), it.open);
          script.onclick = (e) => { e.stopPropagation(); openSource(it.open, 1); };
          li.append(script);
        }
        return li;
      })));
    }
    return details;
  });
  panel.replaceChildren(el("div", { className: "toolbar" }, rerun, el("span", { className: "when", textContent: `checked ${new Date().toLocaleTimeString()}` })), ...rows);
}

// What changed since the previous build: a word-level diff of the two PDFs' text (robust to
// reflow), highlighted in green, with deletions as red bars. Replaced on every rebuild.
async function docWords(d) {
  const words = [];
  for (let n = 1; n <= d.numPages; n++) {
    const { H, items } = await pageText(d, n);
    for (const it of items) {
      if (it.top > area.bottom + 10 && /^[\divxlc]+$/i.test(it.str.trim())) continue; // page numbers shift on reflow
      const len = it.str.length || 1;
      let off = 0;
      for (const part of it.str.split(/(\s+)/)) {
        if (part.trim()) {
          const x0 = it.x + it.w * off / len, x1 = it.x + it.w * (off + part.length) / len, y = H - it.top;
          words.push({ s: part, page: n, rect: [x0, y - it.h * 0.25, x1, y + it.h * 0.85] });
        }
        off += part.length;
      }
    }
  }
  return words;
}

let changeList = [], changeIndex = -1;
function showChanges(oldWords, newWords, figures, against) {
  marks.changes.clear();
  changeList = [];
  changeIndex = -1;
  const result = oldWords && diffWords(oldWords.map((w) => w.s), newWords.map((w) => w.s));
  const add = (page, m) => { if (!marks.changes.has(page)) marks.changes.set(page, []); marks.changes.get(page).push(m); };
  if (result) textChanges(result, newWords, add);
  const texts = changeList.length;
  for (const f of figures) {
    const label = { head: "figure changed since the commit", push: "figure changed since the push" }[against] || "figure updated";
    add(f.page, { rect: f.rect, cls: "chg fig", label });
    changeList.push({ page: f.page, rect: f.rect });
  }
  changeList.sort((a, b) => a.page - b.page || b.rect[3] - a.rect[3]);
  const figs = figures.length ? `${figures.length} figure${figures.length === 1 ? "" : "s"}` : "";
  // In a git checkout the group stays, to switch baselines (and to say there are no changes).
  $("changes").hidden = !changeList.length && !headInfo;
  $("changeCount").textContent = [texts || !figs ? `${texts} change${texts === 1 ? "" : "s"}` : "", figs].filter(Boolean).join(" · ");
  drawAllMarks();
}

// The baseline for the change highlights: the last commit ("head": build/<doc>-head[-clean].pdf,
// built by the server whenever HEAD moves), the last push ("push": build/<doc>-push[-clean].pdf,
// the commit the branch's remote branch is at, built once a viewer asks for it in poll), or the
// previous build ("build"). Until the commit's baseline exists (or outside git), the previous build.
// The last push by default, which covers several local commits; the last commit when nothing
// was pushed anywhere (baseKind).
const BASES = ["push", "head", "build"];
let changeBase = BASES.find((b) => b === localStorage.getItem("latex-live:changesAgainst")) || "push";
const baseKind = () => (changeBase === "push" && !pushInfo && headInfo ? "head" : changeBase);
// Change highlights are off unless turned on with the toolbar's Delta button (remembered).
let changesOn = localStorage.getItem("latex-live:changesOn") === "1";
$("changesBtn").classList.toggle("on", changesOn);
let headInfo = null;       // status().head
let pushInfo = null;       // status().push: null without a remote-tracking branch to compare with (or from an older server)
function setBaselines(head, push) { headInfo = head; pushInfo = push; }
let prevBuildWords = null; // words of the build shown before this one (same variant)
let figuresPromise = null; // {build, head, push}: changed figures of the shown PDF
let wordsPromise = null;   // words of the shown PDF
// The document just shown (oldWords: those of the one before, when it was of the same variant): its words
// and its changed figures, to compare.
function trackDocument(next, variant, oldWords) {
  prevBuildWords = oldWords || null;
  wordsPromise = docWords(next);
  figuresPromise = isDiff ? null : changedFigures(next, variant).catch((e) => { console.warn(e); return { build: [], head: [], push: [] }; });
}
let changesKey = null, baseCache = null;
function commitBase() {
  const kind = baseKind();
  const info = { head: headInfo, push: pushInfo }[kind];
  const version = shownVariant === "clean" ? info?.clean : info?.version;
  if (!version) return null;
  const name = docFile.replace(/\.pdf$/, `-${kind}${shownVariant === "clean" ? "-clean" : ""}.pdf`);
  return { key: `${kind}:${name}:${version}`, kind, name, version };
}
function baseWords(commit) {
  if (baseCache?.key !== commit.key) {
    baseCache = { key: commit.key, promise: pdfjsLib.getDocument({ url: `pdf/${encodeURIComponent(commit.name)}?v=${commit.version}`, ...assets }).promise
      .then(async (d) => { try { return await docWords(d); } finally { d.destroy(); } }) };
  }
  return baseCache.promise;
}
async function updateChanges() {
  if (isDiff || !wordsPromise) return;
  if (!changesOn) {
    changesKey = null;
    if (marks.changes.size || changeList.length) { marks.changes.clear(); changeList = []; changeIndex = -1; drawAllMarks(); }
    $("changes").hidden = true;
    return;
  }
  const commit = commitBase();
  const key = `${commit ? commit.key : "build"}|${docSeq}`;
  showBase(commit);
  if (key === changesKey) return;
  changesKey = key;
  try {
    const [words, figs, base] = await Promise.all([wordsPromise, figuresPromise, commit ? baseWords(commit) : prevBuildWords]);
    if (key === changesKey) showChanges(base, words, figs[commit ? commit.kind : "build"], commit?.kind);
  } catch (e) {
    console.warn(e);
    if (key === changesKey) changesKey = null; // try again on the next poll
  }
}
function showBase(commit) {
  const kind = baseKind();
  const b = $("changeBase");
  b.hidden = !headInfo;
  if (!headInfo) return;
  const pending = kind !== "build" && !commit;
  b.textContent = { head: "vs HEAD", push: "vs last push", build: "vs last build" }[kind] + (pending ? "…" : "");
  const fallback = "showing changes since the last build";
  b.title = (kind === "head"
    ? (pending ? (headInfo.state === "failed" ? `The HEAD build failed (${headInfo.msg}); ${fallback}.`
                                              : `Building the HEAD (${headInfo.sha}) baseline; ${fallback} meanwhile.`)
               : `Changes since the last commit (${headInfo.sha}).`)
    : kind === "push"
    ? (!pushInfo ? `No pushed commit to compare with (no remote-tracking branch); ${fallback}.`
       : pending ? (pushInfo.state === "failed" ? `The last-push build failed (${pushInfo.msg}); ${fallback}.`
                                                : `Building the last-push (${pushInfo.sha}, ${pushInfo.ref}) baseline; ${fallback} meanwhile.`)
       : `Changes since the last push (${pushInfo.sha}, ${pushInfo.ref}), committed or not.`)
    : "Changes since the previous build.") + " Click to switch.";
}
function toggleChanges() {
  changesOn = !changesOn;
  localStorage.setItem("latex-live:changesOn", changesOn ? "1" : "");
  $("changesBtn").classList.toggle("on", changesOn);
  updateChanges();
}
$("changesBtn").onclick = toggleChanges;
$("changeBase").onclick = () => { // push (when there is a pushed commit), head, build
  do changeBase = BASES[(BASES.indexOf(baseKind()) + 1) % BASES.length];
  while (changeBase === "push" && !pushInfo);
  localStorage.setItem("latex-live:changesAgainst", changeBase);
  updateChanges();
};
function textChanges(result, newWords, add) {
  // Group inserted words into runs (small gaps allowed), each run one change; deletions next to a
  // run belong to it, others are changes of their own.
  const runs = [];
  for (const i of result.inserted) {
    const last = runs.at(-1);
    if (last && i - last.at(-1) <= 3) last.push(i); else runs.push([i]);
  }
  const changes = runs.map((r) => ({ start: r[0], end: r.at(-1), words: r, dels: [] }));
  for (const at of result.deletedAt) {
    const c = changes.find((c) => at >= c.start - 1 && at <= c.end + 2);
    if (c) c.dels.push(at); else changes.push({ start: at, end: at, words: [], dels: [at] });
  }
  changes.sort((a, b) => a.start - b.start);
  for (const c of changes) {
    const lines = []; // merge each change's word boxes line by line
    for (const i of c.words) {
      const w = newWords[i], last = lines.at(-1);
      if (last && last.page === w.page && Math.abs(last.rect[1] - w.rect[1]) < 2) {
        last.rect = [Math.min(last.rect[0], w.rect[0]), Math.min(last.rect[1], w.rect[1]), Math.max(last.rect[2], w.rect[2]), Math.max(last.rect[3], w.rect[3])];
      } else {
        lines.push({ page: w.page, rect: [...w.rect] });
      }
    }
    for (const l of lines) add(l.page, { rect: l.rect, cls: "chg" });
    for (const at of c.dels) {
      if (c.words.includes(at)) continue; // a replacement: the new words already show it
      const w = newWords[Math.min(at, newWords.length - 1)];
      if (!w) continue;
      const x = at < newWords.length ? w.rect[0] - 1.5 : w.rect[2] + 0.5;
      lines.push({ page: w.page, rect: [x, w.rect[1], x + 2, w.rect[3]] });
      add(w.page, { rect: [x, w.rect[1], x + 2, w.rect[3]], cls: "del" });
    }
    if (lines.length) changeList.push(lines[0]);
  }
}

// Changed figures: the text diff can't see them, so compare the included graphics files' hashes
// between builds, and ask git which differ from HEAD and from the last push. Outlined, placed by
// SyncTeX at their \includegraphics: {build, head, push}, one list per baseline.
let graphicsHashes = null;
async function changedFigures(d, variant) {
  const list = await (await fetch(`graphics/${encodeURIComponent(file)}`)).json();
  const before = graphicsHashes;
  graphicsHashes = new Map(list.map((g) => [g.file, g.hash]));
  const build = before ? list.filter((g) => before.get(g.file) !== g.hash) : [];
  const head = list.filter((g) => g.dirty), push = list.filter((g) => g.unpushed);
  const found = new Map();
  for (const g of new Set([...build, ...head, ...push])) {
    const r = await fetch(`forward?${new URLSearchParams({ file: g.src, line: g.line, ...(variant === "clean" ? { clean: 1 } : {}) })}`);
    if (!r.ok) continue;
    const t = await r.json();
    const [, , W, H] = (await d.getPage(t.page)).view;
    found.set(g, { page: t.page, file: g.file, rect: [Math.max(0, t.x), H - t.y - t.h, Math.min(W, t.x + t.w), H - t.y] });
  }
  const placed = (gs) => gs.map((g) => found.get(g)).filter(Boolean);
  return { build: placed(build), head: placed(head), push: placed(push) };
}
function goToChange(step) {
  if (!changeList.length) return;
  changeIndex = (changeIndex + step + changeList.length) % changeList.length;
  const c = changeList[changeIndex];
  $("changeCount").textContent = `${changeIndex + 1} / ${changeList.length} changes`;
  reveal(c.page, [c.rect[0] - 2, c.rect[1] - 2, c.rect[2] + 2, c.rect[3] + 2]);
}
$("nextChange").onclick = () => goToChange(1);
$("prevChange").onclick = () => goToChange(-1);
$("clearChanges").onclick = () => { marks.changes.clear(); changeList = []; $("changes").hidden = true; drawAllMarks(); };

// Where to cut: paragraphs whose last line holds only a word or two, and large empty bands in the
// text column (around figures and tables, or a page that ends early), in the main text of the PDF
// as shown. Each spot estimates the lines it would give back.
let cutList = [], cutIndex = -1;
const cutCache = new WeakMap();
function cutSpots(d, r) {
  if (!cutCache.has(d)) cutCache.set(d, findCutSpots(d, r));
  return cutCache.get(d);
}
async function findCutSpots(d, r) {
  const spots = [];
  for (let p = r.first; p <= r.page; p++) {
    const { H, lines: all } = await textLines(d, p);
    let ctx = null, canvas = null; // the rendered page, for the empty-band scan (once per page)
    for (const col of columnMid() === null ? [1] : [1, 2]) { // each text column on its own
      if (p === r.page && col > (r.endCol || 1)) break;
      const end = p === r.page && col === (r.endCol || 1) ? r.endTop : area.bottom + 12;
      const lines = all.filter((l) => l.col === col);
      // A full line of body text spans most of the column in body-size type, unlike a title (before a
      // build reports the column's width: over 60 characters).
      const full = (l) => (area.colwidth ? l.right - l.left > area.colwidth * 0.85 && Math.max(...l.parts.map((q) => q.h)) <= area.leading
                                         : l.str.length > 60);
      const body = lines.filter(full);
      if (body.length < 3) continue;
      const lefts = {};
      for (const l of body) lefts[Math.round(l.left)] = (lefts[Math.round(l.left)] || 0) + 1;
      const colLeft = +Object.entries(lefts).sort((a, b) => b[1] - a[1])[0][0];
      const aligned = body.filter((l) => Math.abs(l.left - colLeft) < 2);
      const colRight = Math.max(...aligned.map((l) => l.right));
      for (let i = 1; i < lines.length; i++) {
        const l = lines[i], prev = lines[i - 1], next = lines[i + 1];
        if (l.top > end) break;
        const short = l.str.split(/\s+/).length <= 2 || l.right - l.left < (colRight - colLeft) * 0.12;
        if (!short || Math.abs(l.left - colLeft) > 1.5 || /^[\d\s.,%()–-]*$/.test(l.str)) continue;
        // after a full justified line of the same paragraph, and not followed by more of it
        if (prev.right < colRight - 3 || !full(prev) || prev.left < colLeft - 1.5 || l.top - prev.top > area.leading + 3) continue;
        if (next && next.top - l.top < area.leading + 2 && Math.abs(next.left - colLeft) < 1.5) continue;
        const h = Math.max(...l.parts.map((q) => q.h));
        spots.push({ page: p, col, top: l.top, lines: 1, label: "1 line", text: `“${l.str}” alone on a paragraph's last line`,
                     rect: [l.left - 1, H - l.top - h * 0.3, colRight, H - l.top + h * 0.9] });
      }
      // Empty bands: rows of the text column with nothing drawn in them, well beyond a paragraph break.
      if (!ctx) {
        const page = await d.getPage(p);
        const viewport = page.getViewport({ scale: 1 });
        canvas = el("canvas", { width: Math.ceil(viewport.width), height: Math.ceil(viewport.height) });
        ctx = canvas.getContext("2d", { willReadFrequently: true });
        await page.render({ canvasContext: ctx, viewport, annotationMode: pdfjsLib.AnnotationMode.DISABLE }).promise;
      }
      const x0 = Math.max(0, Math.floor(colLeft - 2)), x1 = Math.min(canvas.width, Math.ceil(colRight + 2));
      // (On the first page, only between its first and last full lines: the spacing of a title block,
      // and of a title page that ends after the abstract, is by design.)
      const start = p === r.first ? Math.max(area.top, body[0].top - 12) : area.top;
      const stop = p === r.first ? Math.min(end, aligned.at(-1).top + area.leading) : end;
      const y0 = Math.floor(start), y1 = Math.min(canvas.height, Math.ceil(stop));
      if (y1 - y0 < 30) continue;
      const { data } = ctx.getImageData(x0, y0, x1 - x0, y1 - y0);
      const w = x1 - x0;
      let run = null;
      for (let y = 0; y <= y1 - y0; y++) {
        let blank = y < y1 - y0;
        for (let x = 0; blank && x < w; x++) {
          const k = (y * w + x) * 4;
          if (data[k + 3] > 0 && (data[k] < 235 || data[k + 1] < 235 || data[k + 2] < 235)) blank = false;
        }
        if (blank) { run ??= y; continue; }
        const gap = run === null ? 0 : y - run;
        const n = Math.round((gap - 10) / area.leading); // a paragraph break leaves ~10pt
        if (gap > 28 && n >= 1) {
          const top = y0 + run, bottom = y0 + y, atEnd = y === y1 - y0 && p < r.page;
          spots.push({ page: p, col, top, lines: n, label: `≈${n} line${n === 1 ? "" : "s"}`,
                       text: `${Math.round(gap)} pt of empty space ${atEnd ? "at the bottom of the column" : "in the text column"}`,
                       rect: [colLeft, H - bottom, colRight, H - top] });
        }
        run = null;
      }
    }
  }
  return spots.sort((a, b) => a.page - b.page || (a.col || 1) - (b.col || 1) || a.top - b.top);
}
async function updateCut() {
  $("cut").classList.toggle("on", cutOn);
  const d = doc, r = displayedResult();
  marks.cut.clear();
  cutList = [];
  cutIndex = -1;
  if (cutOn && d && r) {
    const spots = await cutSpots(d, r);
    if (d !== doc || !cutOn) return;
    cutList = spots;
    for (const s of spots) {
      if (!marks.cut.has(s.page)) marks.cut.set(s.page, []);
      marks.cut.get(s.page).push({ rect: s.rect, cls: "cut", label: s.label });
    }
    const lines = spots.reduce((n, s) => n + s.lines, 0);
    $("cutCount").textContent = spots.length ? `${spots.length} spot${spots.length === 1 ? "" : "s"} · ≈${lines} line${lines === 1 ? "" : "s"}` : "nothing to cut";
  }
  $("cutNav").hidden = !cutOn || !r;
  drawAllMarks();
}
function goToCut(step) {
  if (!cutList.length) return;
  cutIndex = (cutIndex + step + cutList.length) % cutList.length;
  const s = cutList[cutIndex];
  $("cutCount").textContent = `${cutIndex + 1} / ${cutList.length}: ${s.label}`;
  reveal(s.page, s.rect);
}
$("cut").onclick = () => { setCutOn(!cutOn); localStorage.setItem("latex-live:cut", cutOn ? "1" : ""); updateCut(); };
$("nextCut").onclick = () => goToCut(1);
$("prevCut").onclick = () => goToCut(-1);
$("cut").hidden = isDiff;

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
