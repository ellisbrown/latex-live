// The source pane: the open file, saving it (live.py merges others' changes), its conflicts, reloading it when it changes
// on disk, the notice above the text, and the edits this browser keeps until they are saved.

import { $, editorShown, el, hubBase, setStatus, SIGNED_OUT } from "./page.js";
import { fitsKeepalive, postJSON, readBody, signInPage, unreachable } from "./net.js";
import { lineHunks } from "./diff.js";
import { dropKept, EDITOR_ID, keptKey, newSaveId, readKept } from "./drafts.js";
import { editorAllowed, me, openTitles, ownerName } from "./role.js";
import { refit } from "./pdfview.js";
import { wrote } from "./build.js";
import { fitEditor, setToggle } from "./layout.js";
import { CM, cmExtensions, loadEditor } from "./cm.js";

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

export let cmView = null; // the open file's editor (CodeMirror's view)

export let srcFiles = null; // live.py /files: {files: [{path, size}], main}
export function setSrcFiles(list) { srcFiles = list; }
// The open file: {path, base (its text as the server has it, at version baseVersion; null when that
// version is not known, so that the server merges from base), dirty (edits not saved yet), markers
// (conflict markers in the text), error ({short, why}: why the last save was refused), gone (the file
// is no longer one the server saves: its text can only be copied)}.
export let ed = null;
// A save or reload on its way: {kind, since (the edits typed since it left, a ChangeSet), done (a promise)}.
let busy = null;
let saveTimer = null, retryTimer = null, openSeq = 0, lastSaveAt = 0;
// Saves that failed but may work later: offline is then why ("unreachable": no answer, or the proxy
// or hub couldn't reach the preview; "signin": a page came back instead, e.g. a proxy's sign-in page;
// "updating": the owner is updating the shared copy, live.py's 503 {retry}; "server": another 5xx).
let offline = null, offlineSince = 0, retryDelay = 2000, noticeTimer = null;
export let sourceEpoch = 0; // (bumped whenever the open file's version changes: a status answer from before is stale)
let conflict = null; // a conflict of this session, not resolved yet: {by: who saved the other side, if live.py says}

// Show or hide the source pane; showing it opens path at line (and column, from 0), else the file open last time (in
// this paper) or the main file (with a hint on how to get to the source of a spot in the PDF).
export async function showEditor(on, { path = null, line = 0, column = 0, focus = true } = {}) {
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
    if (path) await openFile(path, line, column);
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
// Open a file (once the one open now is saved, or is gone from the paper) and go to a line (and column) in it.
export async function openFile(path, line = 0, column = 0) {
  if (ed?.path === path) return line && goToLine(line, column);
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
  if (line) goToLine(line, column);
  offerKept(f);
}
function goToLine(line, column = 0) {
  const doc = cmView.state.doc, to = doc.line(Math.min(Math.max(1, +line || 1), doc.lines));
  const at = to.from + Math.min(Math.max(0, +column || 0), to.length);
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
export async function flushSource() {
  for (let i = 0; i < 3 && ed && !ed.gone && (busy || ed.dirty) && !ed.markers && !(i && (ed.error || offline)); i++) {
    await (busy ? busy.done : saveSource());
  }
  return !ed || ed.gone || (!busy && !ed.dirty);
}
// The open file moved on, on disk (someone else saved, or a note went in): reload it in place; with
// edits not saved yet, save them now instead (the server merges). Also, once the server answers
// again, unsaved edits go at once.
export function sourceStatus(source, epoch) {
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

$("editBtn").onclick = () => showEditor(!editorShown());
$("editorClose").onclick = () => showEditor(false);
$("editorHint").querySelector(".close").onclick = () => { $("editorHint").hidden = true; };

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
