// The source pane: multi-file tabs, autosave with 3-way server merge & conflict resolution,
// live reload on disk changes, draft recovery, section outline rail, word count & cursor status,
// formatting shortcuts, and cursor helpers for files/papers/agent modules.

import { $, editorShown, el, hubBase, setStatus, SIGNED_OUT } from "./page.js";
import { fitsKeepalive, postJSON, readBody, signInPage, unreachable } from "./net.js";
import { lineHunks } from "./diff.js";
import { dropKept, EDITOR_ID, keptKey, newSaveId, readKept } from "./drafts.js";
import { editorAllowed, me, openTitles, ownerName } from "./role.js";
import { refit } from "./pdfview.js";
import { wrote } from "./build.js";
import { fitEditor, layoutMode, setLayoutMode, setToggle } from "./layout.js";
import {
  clearProposal, CM, cmExtensions, extractSections, loadEditor, registerView, setOpenHandler,
  toggleFoldAll, wrapSelection,
} from "./cm.js";

let saveId = EDITOR_ID;
export let cmView = null;
export let srcFiles = null; // live.py /files: {files: [{path, size}], assets: [...], main}
export function setSrcFiles(list) { srcFiles = list; }
export let ed = null;
let busy = null;
let saveTimer = null, retryTimer = null, openSeq = 0, lastSaveAt = 0;
let offline = null, offlineSince = 0, retryDelay = 2000, noticeTimer = null;
export let sourceEpoch = 0;
let conflict = null;

// Multi-file open tabs (persisted per paper).
const tabsKey = `latex-live:${hubBase}openTabs`;
let openTabs = [];
try { openTabs = JSON.parse(localStorage.getItem(tabsKey) || "[]").filter((p) => typeof p === "string"); } catch {}

const openListeners = new Set();
export function onFileOpen(fn) { openListeners.add(fn); return () => openListeners.delete(fn); }
function notifyFileOpen(path) { for (const fn of openListeners) fn(path); }

function saveTabs() {
  try { localStorage.setItem(tabsKey, JSON.stringify(openTabs.slice(0, 12))); } catch {}
}

export function renderTabs() {
  const bar = $("editorTabs");
  if (!bar) return;
  const rows = openTabs.map((path) => {
    const active = ed?.path === path;
    const base = path.split("/").pop();
    const tab = el("div", {
      className: `ed-tab${active ? " on" : ""}`,
      role: "tab",
      title: path,
      onclick: () => openFile(path),
    });
    tab.append(el("span", { className: "tname", textContent: base }));
    if (active && ed?.dirty) tab.append(el("span", { className: "dirty-dot", title: "Unsaved changes" }));
    if (openTabs.length > 1) {
      const x = el("button", {
        className: "tclose",
        textContent: "×",
        title: `Close ${base}`,
        onclick: (e) => { e.stopPropagation(); closeTab(path); },
      });
      tab.append(x);
    }
    return tab;
  });
  bar.replaceChildren(...rows);
  bar.querySelector(".ed-tab.on")?.scrollIntoView({ inline: "nearest", block: "nearest" });
}

export async function closeTab(path) {
  if (ed?.path === path && !(await flushSource())) return;
  openTabs = openTabs.filter((p) => p !== path);
  saveTabs();
  if (ed?.path === path) {
    if (openTabs.length) await openFile(openTabs[openTabs.length - 1]);
    else renderTabs();
  } else {
    renderTabs();
  }
}

export async function dropTab(path) {
  openTabs = openTabs.filter((p) => p !== path);
  saveTabs();
  if (ed?.path === path) {
    ed.dirty = false;
    ed.gone = true;
    dropKept(path);
    ed = null;
    if (openTabs.length) await openFile(openTabs[openTabs.length - 1]);
    else renderTabs();
  } else {
    renderTabs();
  }
}

export async function renameTab(oldPath, newPath) {
  openTabs = openTabs.map((p) => (p === oldPath ? newPath : p));
  saveTabs();
  if (ed?.path === oldPath) {
    ed.path = newPath;
    localStorage.setItem(`latex-live:${hubBase}editorFile`, newPath);
    renderTabs();
    $("filePick").title = `${newPath} — switch source file`;
    notifyFileOpen(newPath);
  } else {
    renderTabs();
  }
}

export async function refreshFilesList() {
  const r = await fetch("files", { cache: "no-store" }).catch(() => null);
  if (!r?.ok) return srcFiles;
  const list = await readBody(r);
  if (Array.isArray(list.files)) srcFiles = list;
  return srcFiles;
}

// Live word count (strips preamble, comments, and LaTeX command names for an accurate prose count).
function countProseWords(text) {
  if (!text) return 0;
  let body = text;
  const bd = body.indexOf("\\begin{document}");
  if (bd >= 0) body = body.slice(bd + 16);
  const edIdx = body.indexOf("\\end{document}");
  if (edIdx >= 0) body = body.slice(0, edIdx);
  body = body
    .replace(/(?<!\\)%.*$/gm, " ")
    .replace(/\\(?:cite|citep|citet|ref|eqref|cref|label|includegraphics|input|usepackage|bibliography)\*?(?:\[[^\]]*\])?\{[^{}]*\}/g, " ")
    .replace(/\\[a-zA-Z]+\*?/g, " ")
    .replace(/[{}[\]$_^~\\]/g, " ");
  const m = body.match(/[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*/g);
  return m ? m.length : 0;
}

let wordTimer = null;
function updateEditorFoot(update = null) {
  if (!cmView) return;
  const state = cmView.state;
  const sel = state.selection.main;
  const line = state.doc.lineAt(sel.head);
  const col = sel.head - line.from + 1;
  $("cursorPos").textContent = `Ln ${line.number}, Col ${col}`;
  updateRailActive(line.number);
  if (!update || update.docChanged || sel.from !== sel.to) {
    clearTimeout(wordTimer);
    wordTimer = setTimeout(() => {
      if (!cmView) return;
      const total = countProseWords(cmView.state.doc.toString());
      const curSel = cmView.state.selection.main;
      if (curSel.to > curSel.from) {
        const selW = countProseWords(cmView.state.sliceDoc(curSel.from, curSel.to));
        $("wordCount").textContent = `${selW.toLocaleString()} / ${total.toLocaleString()} words`;
      } else {
        $("wordCount").textContent = `${total.toLocaleString()} words`;
      }
    }, update?.docChanged ? 220 : 40);
  }
}

// Section Outline Rail on right edge of editor.
let railSections = [];
function renderOutlineRail() {
  const rail = $("outlineRail");
  if (!rail || !cmView) return;
  railSections = extractSections(cmView.state.doc);
  rail.hidden = railSections.length < 2;
  if (rail.hidden) return;
  rail.replaceChildren(...railSections.map((s) => el("div", {
    className: `rail-tick lvl${s.level}`,
    title: `${s.title} (line ${s.line})`,
    dataset: { line: String(s.line) },
    onclick: () => goToLine(s.line),
  })));
  const curLine = cmView.state.doc.lineAt(cmView.state.selection.main.head).number;
  updateRailActive(curLine);
}

function updateRailActive(lineNum) {
  const rail = $("outlineRail");
  if (!rail || rail.hidden || !railSections.length) return;
  let active = railSections[0]?.line;
  for (const s of railSections) if (s.line <= lineNum) active = s.line;
  for (const node of rail.children) node.classList.toggle("active", +node.dataset.line === active);
}

export function cursorLine() {
  if (!cmView || !ed) return 0;
  return cmView.state.doc.lineAt(cmView.state.selection.main.head).number;
}

export function insertAtCursor(text) {
  if (!cmView || !ed) return false;
  const { from, to } = cmView.state.selection.main;
  cmView.dispatch({ changes: { from, to, insert: text }, selection: { anchor: from + text.length } });
  cmView.focus();
  return true;
}

export function getSelectionContext() {
  if (!cmView || !ed) return null;
  const doc = cmView.state.doc;
  let { from, to } = cmView.state.selection.main;
  if (from === to) {
    // Expand to the current non-empty paragraph when nothing is selected.
    const cur = doc.lineAt(from);
    if (!cur.text.trim()) return null;
    let first = cur.number, last = cur.number;
    while (first > 1 && doc.line(first - 1).text.trim()) first--;
    while (last < doc.lines && doc.line(last + 1).text.trim()) last++;
    from = doc.line(first).from;
    to = doc.line(last).to;
  }
  const text = doc.sliceString(from, to);
  const before = doc.sliceString(Math.max(0, from - 1200), from);
  const after = doc.sliceString(to, Math.min(doc.length, to + 1200));
  return { file: ed.path, from, to, text, before, after };
}

export async function showEditor(on, { path = null, line = 0, column = 0, focus = true, mode = null } = {}) {
  if (on && !editorAllowed()) return;
  if (!on) {
    $("fileMenu").hidden = true;
    saveSource();
  }
  const nextMode = mode || (on ? (layoutMode === "source" ? "source" : "split") : "pdf");
  if (editorShown() !== on || layoutMode !== nextMode) {
    $("editorPane").hidden = !on;
    $("editBtn").classList.toggle("on", on);
    setToggle("editor", on);
    setLayoutMode(nextMode);
    fitEditor(on);
    refit();
    openTitles();
  }
  if (!on) return;
  try {
    setOpenHandler((p, l) => openFile(p, l));
    await loadEditor({ save: saveSource, edited: sourceEdited, onCursor: updateEditorFoot });
    let hint = false;
    if (!path && !ed) [path, hint] = await firstFile();
    if (path) await openFile(path, line, column);
    if (hint && cmView) $("editorHint").hidden = false;
    if (!cmView) $("editorHost").replaceChildren(el("div", {
      className: "msg",
      textContent: path ? `${path} did not open (the status line says why): pick a file above.` : "No source files to edit.",
    }));
    if (focus && editorShown()) cmView?.focus();
  } catch (err) {
    console.warn(err);
    if (cmView) setStatus(`${err.message || err}`, "error");
    else $("editorHost").replaceChildren(el("div", { className: "msg", textContent: `The editor did not load: ${err.message || err}` }));
  }
}

async function firstFile() {
  const r = await fetch("files", { cache: "no-store" });
  const list = await readBody(r);
  if (!r.ok || !Array.isArray(list.files)) throw new Error(list.error || `the list of files did not load (${r.status})`);
  srcFiles = list;
  const paths = srcFiles.files.map((f) => f.path);
  openTabs = openTabs.filter((p) => paths.includes(p));
  const last = localStorage.getItem(`latex-live:${hubBase}editorFile`);
  return paths.includes(last) ? [last, false] : [srcFiles.main || paths[0] || null, true];
}

export async function openFile(path, line = 0, column = 0) {
  if (ed?.path === path) {
    if (line) goToLine(line, column);
    return;
  }
  const seq = ++openSeq;
  if (ed && !(await flushSource())) return setStatus(`${ed.path} is not saved yet, so ${path} did not open`, "error");
  const r = await fetch(`file?${new URLSearchParams({ path })}`, { cache: "no-store" }).catch(() => null);
  if (seq !== openSeq) return;
  const f = r ? await readBody(r) : {};
  if (!r?.ok || typeof f.text !== "string" || typeof f.version !== "string") {
    const why = !r || unreachable(r, f) ? "can't reach the preview server" : signInPage(r, f) ? "your sign-in may have expired"
      : r.status === 404 ? "not a source file that can be edited here" : r.status === 415 ? "not UTF-8 text" : f.error;
    const kept = r?.status === 404 && !unreachable(r, f) && readKept(path);
    if (kept) showGone(path, kept.text, kept.key);
    return setStatus(`${path}: ${why}`, "error");
  }
  while (busy) await busy.done;
  if (seq !== openSeq || (ed?.dirty && !ed.gone)) return;
  ed = { path: f.path, base: f.text, baseVersion: f.version, dirty: false, markers: false, error: null, gone: false };
  sourceEpoch++;
  const state = CM.EditorState.create({ doc: f.text, extensions: cmExtensions });
  if (cmView) {
    cmView.setState(state);
  } else {
    $("editorHost").replaceChildren();
    cmView = new CM.EditorView({ state, parent: $("editorHost") });
    registerView(cmView);
  }
  clearProposal(cmView);
  if (!openTabs.includes(f.path)) openTabs.push(f.path);
  saveTabs();
  renderTabs();
  $("filePick").textContent = "▾";
  $("filePick").title = `${f.path} — switch source file`;
  $("editorHint").hidden = true;
  localStorage.setItem(`latex-live:${hubBase}editorFile`, f.path);
  showSaveState();
  renderOutlineRail();
  updateEditorFoot();
  notifyFileOpen(f.path);
  if (line) goToLine(line, column);
  offerKept(f);
}

export function goToLine(line, column = 0) {
  if (!cmView) return;
  const doc = cmView.state.doc, to = doc.line(Math.min(Math.max(1, +line || 1), doc.lines));
  const at = to.from + Math.min(Math.max(0, +column || 0), to.length);
  cmView.dispatch({ selection: { anchor: at }, effects: CM.EditorView.scrollIntoView(at, { y: "center" }) });
  cmView.focus();
}

let railTimer = null;
function sourceEdited(update) {
  if (!update.docChanged || !ed) return;
  let own = false;
  for (const tr of update.transactions) {
    if (!tr.docChanged) continue;
    if (busy) busy.since = busy.since.compose(tr.changes);
    if (!tr.annotation(CM.Transaction.remote)) own = true;
  }
  clearTimeout(railTimer);
  railTimer = setTimeout(renderOutlineRail, 350);
  if (!own) return;
  ed.dirty = true;
  if (!ed.gone) ed.error = null;
  if (ed.markers) ed.markers = hasConflictMarks(update.state.doc.toString());
  scheduleSave();
  showSaveState();
  renderTabs();
  keepUnsaved();
}

function scheduleSave(delay = 1500) {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => saveSource(), delay);
}

const MARK_ENDS = /^(<{7}|>{7})( |$)/m, MARK_MIDDLE = /^={7}$/gm;
function hasConflictMarks(text) {
  return MARK_ENDS.test(text) || (!!conflict && (text.match(MARK_MIDDLE) || []).length > ((ed?.base || "").match(MARK_MIDDLE) || []).length);
}

const OFFLINE_LABELS = {
  unreachable: "Not saved — offline", signin: "Not saved — signed out?",
  server: "Not saved — retrying", updating: "Owner is updating — retrying",
};
const OFFLINE_TITLES = {
  unreachable: "Can't reach the preview server. Your edits are kept in this browser and save once it answers; click to retry.",
  signin: "Your sign-in may have expired. Your edits are kept in this browser; reload to sign in again.",
  server: "The server could not save just now. Your edits are kept in this browser and retry automatically; click to retry.",
  updating: "The owner is updating the shared copy. Your edits save automatically in a moment.",
};

function showSaveState() {
  const label = $("saveState");
  const [text, cls, title] = !ed ? ["", "", ""] : busy?.kind === "save" ? ["Saving…", "busy", ""]
    : ed.markers ? ["Conflict", conflict ? "error conflict" : "error",
      `There are conflict markers in the text${conflict ? ". Click for the choices." : "."}`]
    : ed.error ? [`Not saved — ${ed.error.short}`, "error retry", `Not saved: ${ed.error.why.replace(/\.$/, "")}. Click to try again.`]
    : offline && ed.dirty ? [OFFLINE_LABELS[offline], "error retry", OFFLINE_TITLES[offline]]
    : ed.dirty ? ["Unsaved", "dirty", "Autosaves shortly (Cmd/Ctrl+S to save now)"] : ["Saved", "", ""];
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

export async function saveSource({ keepalive = false } = {}) {
  clearTimeout(saveTimer);
  if (!ed || !cmView || ed.gone) return;
  if (busy) {
    await busy.done;
    return saveSource({ keepalive });
  }
  clearTimeout(retryTimer);
  const target = ed, text = cmView.state.doc.toString();
  if (!ed.dirty) return showSaveState();
  if (text === ed.base && !ed.uncertain) {
    ed.dirty = false;
    if (!hasConflictMarks(text)) endConflict();
    keepUnsaved(true);
    renderTabs();
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
  target.uncertain = true;
  try {
    let body = { path: target.path, base: target.baseVersion, text, editor: saveId };
    if (keepalive && fitsKeepalive({ ...body, base_text: target.base })) body = { ...body, base_text: target.base };
    let r = await postJSON("file", body, keepalive);
    let res = await readBody(r);
    if (r.status === 409 && res.stale && !("base_text" in body)) {
      r = await postJSON("file", { ...body, base_text: target.base }, keepalive);
      res = await readBody(r);
    }
    const trouble = signInPage(r, res) || (r.ok && typeof res.version !== "string") ? "signin"
      : r.status === 503 && res.retry ? "updating" : unreachable(r, res) ? "unreachable" : r.status >= 500 ? "server" : null;
    if (trouble) throw Object.assign(new Error(res.error || `save failed (${r.status})`), { trouble });
    if (r.status === 404 && (await stillListed(target.path))) throw Object.assign(new Error(res.error), { trouble: "server" });
    backOnline();
    const { since } = busy;
    busy = null;
    if (r.ok) wrote();
    if (ed !== target) return;
    if (r.ok) {
      const now = typeof res.text === "string" ? res.text : text;
      if (now === text || applyRemote(text, now, since)) {
        Object.assign(ed, { base: now, baseVersion: res.version, dirty: !since.empty, markers: false, error: null, uncertain: false });
      } else {
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
        if (!editorShown()) showEditor(true, { focus: false });
        showConflict();
      } else {
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
    renderTabs();
    keepUnsaved(true);
    if (ed?.dirty && !ed.markers && !ed.error && !offline) scheduleSave();
  }
}

async function stillListed(path) {
  const r = await fetch("files", { cache: "no-store" }).catch(() => null);
  const list = r?.ok ? await readBody(r) : null;
  return !Array.isArray(list?.files) || list.files.some((f) => f.path === path);
}

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
  unreachable: (path) => `Still can't reach the preview server, so your edits to ${path} are not saved. They save automatically once it answers, and this browser keeps them meanwhile:`,
  signin: (path) => `Your sign-in may have expired while saving ${path}. Copy your edits from below before reloading:`,
  server: (path) => `The server could not save ${path} for a while; retrying automatically. Copy below if needed:`,
  updating: (path) => `${ownerName()} is updating the shared copy; your edits to ${path} save automatically once that finishes:`,
};

function backOnline() {
  offline = null;
  retryDelay = 2000;
  clearTimeout(noticeTimer);
  hideNotice("unsaved");
}

const REFUSALS = { 403: "no access", 404: "file not in the paper", 413: "file too large", 415: "not UTF-8 text", 422: "couldn't merge", 409: "the file keeps changing" };
function refuseSave(status, why) {
  ed.error = { short: REFUSALS[status] || "refused", why };
  setStatus(`${ed.path} not saved: ${why}`, "error");
  if (status === 404) {
    ed.gone = true;
    showGone(ed.path, cmView.state.doc.toString());
  } else if (status === 403) {
    showNotice("refused", `The preview refused to save ${ed.path} (${why}). Copy your edits below and ask ${ownerName()}:`,
      { copy: cmView.state.doc.toString() });
  }
}

function showGone(path, text, key = keptKey(path)) {
  showNotice("gone", `${path} is no longer part of the paper, so your edits can't be saved there. Copy them below:`,
    { copy: text, onClose: () => dropKept(path, key) });
}

export async function flushSource() {
  for (let i = 0; i < 3 && ed && !ed.gone && (busy || ed.dirty) && !ed.markers && !(i && (ed.error || offline)); i++) {
    await (busy ? busy.done : saveSource());
  }
  return !ed || ed.gone || (!busy && !ed.dirty);
}

export function sourceStatus(source, epoch) {
  if (!ed || busy || ed.gone) return;
  if (ed.dirty && (offline === "unreachable" || offline === "signin")) {
    if (Date.now() - lastSaveAt > 2000) saveSource();
    return;
  }
  if (source?.path !== ed.path || source.version === ed.baseVersion || epoch !== sourceEpoch) return;
  if (!ed.dirty) reloadSource();
  else if (!ed.markers && !ed.error && !offline) saveSource();
}

export async function reloadSource() {
  if (!ed || !cmView) return;
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
    if (applyRemote(from, f.text, since)) {
      Object.assign(ed, { base: f.text, baseVersion: f.version, dirty: !since.empty });
      sourceEpoch++;
    }
  } catch (err) {
    console.warn(err);
  } finally {
    if (busy?.kind === "load") busy = null;
    done();
    showSaveState();
    renderTabs();
    keepUnsaved(true);
    if (ed?.dirty && !ed.markers && !ed.error) scheduleSave();
  }
}

function applyRemote(from, to, since) {
  const { changes, rough } = lineChanges(from, to);
  if (!since.empty) {
    let touches = rough;
    since.iterChangedRanges((fromA, toA) => { touches ||= !!changes.touchesRange(fromA, toA); });
    if (touches) return false;
  }
  const mapped = changes.map(since, true);
  if (mapped.empty) return true;
  cmView.dispatch({ changes: mapped, annotations: [CM.Transaction.remote.of(true), CM.Transaction.addToHistory.of(false)] });
  flashChanged(mapped);
  return true;
}

let flashSeq = 0;
function flashChanged(changes) {
  const doc = cmView.state.doc, lines = [];
  changes.iterChangedRanges((fromA, toA, fromB, toB) => {
    const last = doc.lineAt(Math.max(fromB, toB - 1)).number;
    for (let n = doc.lineAt(fromB).number; n <= last && lines.length < 500; n++) lines.push(CM.remoteLine.range(doc.line(n).from));
  });
  cmView.dispatch({ effects: CM.flashLines.of(CM.Decoration.set(lines, true)) });
  const seq = ++flashSeq;
  setTimeout(() => { if (seq === flashSeq) cmView.dispatch({ effects: CM.flashLines.of(CM.Decoration.none) }); }, 2000);
}

function replaceSource(text) {
  cmView.dispatch({ changes: lineChanges(cmView.state.doc.toString(), text).changes });
}

function lineChanges(a, b) {
  const lines = (s) => s.match(/[^\n]*\n|[^\n]+$/g) || [];
  const A = lines(a), B = lines(b), at = [0];
  for (const l of A) at.push(at.at(-1) + l.length);
  const hunks = lineHunks(A, B);
  return {
    changes: CM.ChangeSet.of(hunks.map(([a0, a1, b0, b1]) => ({ from: at[a0], to: at[a1], insert: B.slice(b0, b1).join("") })), a.length, "\n"),
    rough: !!hunks.rough,
  };
}

let notice = null;
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

function showConflict() {
  const code = (textContent) => el("code", { textContent });
  const own = !!conflict.by && [me.name, me.id].includes(conflict.by);
  const lead = own ? `You changed the same lines of ${ed.path} elsewhere while editing it here.`
    : `${conflict.by || "Someone else"} changed the same lines of ${ed.path} while you were editing it.`;
  showNotice("conflict", [`${lead} Both versions are shown between `,
    code("<<<<<<< saved meanwhile"), ", ", code("======="), ", and ", code(">>>>>>> your edit"),
    ". Resolve by hand or pick one side:"], {
    buttons: [
      ["Keep mine", "Keep your lines in each conflict", () => keepSide("mine")],
      ["Keep theirs", "Keep their lines in each conflict (yours shown below to copy)", () => keepSide("theirs")],
    ],
  });
  const first = cmView.state.doc.toString().search(/^<{7}/m);
  if (first >= 0) cmView.dispatch({ effects: CM.EditorView.scrollIntoView(first, { y: "nearest" }) });
  setStatus(`${ed.path}: edits conflict, see the source pane`, "error");
}
function endConflict() {
  if (!conflict) return;
  conflict = null;
  hideNotice("conflict");
}

function conflictBlocks(text) {
  const blocks = [];
  let block = null, at = 0;
  for (const line of text.match(/[^\n]*\n|[^\n]+$/g) || []) {
    const bare = line.replace(/\n$/, "");
    if (/^<{7}( |$)/.test(bare)) block = { from: at, part: "theirs", theirs: "", mine: "" };
    else if (block?.part === "theirs" && /^\|{7}( |$)/.test(bare)) block.part = "base";
    else if (block && block.part !== "mine" && bare === "=======") block.part = "mine";
    else if (block?.part === "mine" && /^>{7}( |$)/.test(bare)) { blocks.push({ ...block, to: at + line.length }); block = null; }
    else if (block && block.part !== "base") block[block.part] += line;
    at += line.length;
  }
  return blocks;
}

async function keepSide(side) {
  if (!conflict || !ed) return;
  while (busy) await busy.done;
  if (!conflict || !ed?.markers) return;
  const path = ed.path, blocks = conflictBlocks(cmView.state.doc.toString());
  if (!blocks.length) return setStatus(`${path}: no complete conflict left to decide; resolve the rest by hand`, "error");
  cmView.dispatch({ changes: blocks.map((b) => ({ from: b.from, to: b.to, insert: b[side] })) });
  const left = hasConflictMarks(cmView.state.doc.toString());
  if (side === "theirs") {
    showNotice("theirs", `${path}: kept their lines in each conflict. Your replaced lines are below:`,
      { copy: blocks.map((b) => b.mine).join("\n") });
  } else if (!left) {
    hideNotice("conflict");
  }
  setStatus(left ? `${path}: some conflicts have edited marker lines` : `${path}: kept ${side === "mine" ? "your" : "their"} lines`,
    left ? "error" : "");
  await saveSource();
}

let keepTimer = null;
function keepUnsaved(now = false) {
  clearTimeout(keepTimer);
  if (!now) return void (keepTimer = setTimeout(() => keepUnsaved(true), 400));
  if (!ed || !cmView) return;
  const text = cmView.state.doc.toString();
  try {
    if (ed.dirty) {
      localStorage.setItem(keptKey(ed.path), JSON.stringify({
        path: ed.path, base: ed.base, baseVersion: ed.baseVersion, text,
        time: Date.now(), ...(conflict ? { conflict } : {}),
      }));
    } else {
      dropKept(ed.path);
    }
  } catch (err) {
    console.warn(err);
  }
  const area = $("notice").querySelector("textarea");
  if (["unsaved", "refused", "gone"].includes(notice?.kind) && notice.path === ed.path && document.activeElement !== area) area.value = text;
}

function offerKept(f) {
  const kept = readKept(f.path);
  if (!kept) return;
  if (kept.text === f.text) return dropKept(f.path, kept.key);
  const when = new Date(kept.time).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  showNotice("restore", `This browser kept unsaved edits to ${f.path} from ${when}. Restore them?`, {
    buttons: [
      ["Restore", "Put them back and merge with disk changes", () => restoreKept(kept)],
      ["Discard", "Forget the kept draft", () => { dropKept(kept.path, kept.key); hideNotice("restore"); }],
    ],
  });
}

async function restoreKept(kept) {
  if (ed?.path !== kept.path) return;
  if (!(await flushSource())) return setStatus(`${kept.path}: newer edits are not saved yet`, "error");
  while (busy) await busy.done;
  if (ed?.path !== kept.path) return;
  hideNotice("restore");
  conflict = kept.conflict && MARK_ENDS.test(kept.text) ? { by: kept.conflict.by || null } : null;
  saveId = newSaveId();
  Object.assign(ed, { base: kept.base, baseVersion: kept.baseVersion ?? null });
  sourceEpoch++;
  replaceSource(kept.text);
  keepUnsaved(true);
  if (kept.key !== keptKey(kept.path)) dropKept(kept.path, kept.key);
  if (conflict) {
    ed.markers = true;
    showConflict();
  }
  saveSource();
}

// Wire toolbar & formatting controls.
$("editBtn").onclick = () => showEditor(!editorShown());
$("editorClose").onclick = () => showEditor(false);
$("editorHint").querySelector(".close").onclick = () => { $("editorHint").hidden = true; };
$("fmtBold").onclick = () => wrapSelection(cmView, "\\textbf{", "}");
$("fmtItalic").onclick = () => wrapSelection(cmView, "\\textit{", "}");
$("foldBtn").onclick = () => toggleFoldAll(cmView);
for (const b of document.querySelectorAll("#layoutSwitch button")) {
  b.onclick = () => {
    const mode = b.dataset.mode;
    if (mode === "pdf") showEditor(false, { mode: "pdf" });
    else showEditor(true, { mode });
  };
}

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
