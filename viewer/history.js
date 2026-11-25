// The History tab (editors and the owner): the commits that changed the paper's folder (live.py /history), newest first,
// under the changes not committed yet. A click on one lists the files it changed and shows what changed in one of them
// (live.py /history/diff); Compare shows the paper as it is now with what changed since that commit marked (compare.js),
// and Restore puts the file shown back as that commit left it, or, for a file the commit removed, as it was just before
// (live.py POST /history/restore; a removed file is made again; in shared mode, committed as yours).
//
// A file open in the source pane is saved first, then reloaded with the text restored (reloadSource, which waits for a
// save on its way, and merges edits typed meanwhile as a save would): no edit is dropped without the confirmation saying
// so. The tab is drawn again only when what it shows changes, so that its scroll stays.

import { $, editorShown, el, HOLD, isDiff, isPopout, panelError, plural, refreshButton, setStatus } from "./page.js";
import { getJSON, sendJSON } from "./net.js";
import { me } from "./role.js";
import { closeTab, ed, flushSource, openFile, reloadSource, showEditor, srcFiles } from "./editor.js";
import { startDiff } from "./compare.js";
import { loadFiles } from "./files.js";
import { addTab } from "./sidebar.js";
import { wrote } from "./build.js";
import { icon } from "./icons.js";

const SOURCES = /\.(tex|bib|sty|cls)$/i; // (live.py's EDITABLE_EXTS: what a restore puts back)
const STATUS = { A: "added", M: "changed", D: "removed", T: "changed type" };
const allowed = () => !!me.can?.history && !isDiff && !isPopout;
const panel = addTab("history", { label: "History", icon: "history", title: "The paper's commits: what each changed, and restoring a file",
                                  refresh: () => loadHistory(), onHead: true }); // (a new commit, e.g. shared mode's)
panel.classList.add("history");

// The history as live.py last listed it ({isGit, branch, dirty: [{status, path}], commits: [{sha, parent, author, time,
// subject, files}]}), the entry open (a commit's sha, or "WORKTREE") and the file shown in it, and the diffs fetched
// ("rev|path" -> {diff, truncated} or {error}; a commit's never change, the uncommitted changes' are fetched again).
let data = null, open = null, shownFile = null, drawn = "", loadSeq = 0, worktreeSeq = 0;
const diffs = new Map();

async function loadHistory() {
  if (!allowed()) return;
  const seq = ++loadSeq;
  const got = await getJSON("history"), body = got.body;
  if (seq !== loadSeq) return;
  if (!got.ok || !Array.isArray(body.commits)) {
    if (!data) panel.replaceChildren(panelError(`The history did not load: ${got.error || "no commits in the answer"}`, () => loadHistory()));
    return;
  }
  data = body;
  worktreeSeq++; // (the uncommitted changes' diffs are fetched again, and one on its way is dropped)
  for (const key of [...diffs.keys()]) if (key.startsWith("WORKTREE|")) diffs.delete(key);
  if (open === "WORKTREE" && !data.dirty.some((f) => f.path === shownFile)) {
    [open, shownFile] = data.dirty.length ? ["WORKTREE", data.dirty[0].path] : [null, null];
  }
  draw();
  if (open === "WORKTREE" && shownFile) fetchDiff(open, shownFile);
}

function draw() {
  if (!data) return;
  const key = JSON.stringify([data, open, shownFile, diffs.get(`${open}|${shownFile}`) || null]);
  if (key === drawn) return;
  drawn = key;
  if (!data.isGit) return panel.replaceChildren(el("div", { className: "empty", textContent: "The paper is not in a git repository." }));
  const scroll = panel.scrollTop;
  const top = el("div", { className: "panel-bar" },
                 ...(data.branch ? [el("span", { className: "hist-branch", textContent: data.branch, title: "The branch checked out" })] : []),
                 el("span", { className: "panel-note", textContent: plural(data.commits.length, "commit").replace(" ", data.commits.length >= 60 ? "+ " : " ") }),
                 refreshButton("Look again", () => loadHistory()));
  const entries = [];
  if (data.dirty.length) entries.push(entry({ rev: "WORKTREE", subject: "Not committed yet", files: data.dirty }));
  for (const c of data.commits) entries.push(entry({ rev: c.sha, ...c }));
  if (!entries.length) entries.push(el("div", { className: "empty", textContent: "No commits change the paper's files yet." }));
  panel.replaceChildren(top, ...entries);
  panel.scrollTop = scroll;
}

const ago = (t) => {
  const s = Date.now() / 1000 - t;
  return s < 60 ? "just now" : s < 3600 ? `${Math.floor(s / 60)} min ago` : s < 86400 ? `${Math.floor(s / 3600)} h ago`
       : s < 30 * 86400 ? `${Math.floor(s / 86400)} d ago` : new Date(t * 1000).toLocaleDateString();
};

function entry(c) {
  const worktree = c.rev === "WORKTREE", isOpen = open === c.rev;
  const head = el("button", { className: "hist-head", ariaExpanded: String(isOpen), onclick: () => toggle(c) },
                  el("span", { className: "hist-subject", textContent: c.subject || "(no message)" }),
                  el("span", { className: "hist-meta" },
                     ...(worktree ? [] : [el("code", { textContent: c.sha.slice(0, 7) }), el("span", { textContent: c.author }),
                                          el("span", { textContent: ago(c.time), title: new Date(c.time * 1000).toLocaleString() })]),
                     el("span", { textContent: plural(c.files.length, "file").replace(" ", c.more ? "+ " : " ") })));
  const card = el("div", { className: `hist-item${isOpen ? " open" : ""}` }, head);
  if (!isOpen) return card;
  const files = el("div", { className: "hist-files" }, ...c.files.map((f) => el("button", {
    className: `hist-file${f.path === shownFile ? " on" : ""}`, title: `${f.path}: ${STATUS[f.status] || f.status}`,
    onclick: () => showFile(c.rev, f.path) }, el("span", { className: `hist-st st-${f.status}`, textContent: f.status }),
    el("span", { textContent: f.path }))));
  if (c.more) files.append(el("span", { className: "hist-note", textContent: `and ${c.more} more` }));
  card.append(files);
  const got = diffs.get(`${c.rev}|${shownFile}`);
  if (shownFile) card.append(got ? diffView(got) : el("div", { className: "hist-note", textContent: "Loading…" }));
  const f = c.files.find((x) => x.path === shownFile);
  const acts = el("div", { className: "hist-acts" });
  if (!worktree && me.can.compare) {
    acts.append(el("button", { textContent: "Compare", title: "Show the paper as it is now, with what changed since this commit marked (in a new tab)",
                               onclick: () => startDiff(c.sha) }));
  }
  if (!worktree && me.can.restore && f && SOURCES.test(f.path) && (f.status !== "D" || c.parent)) {
    const before = f.status === "D", name = f.path.split("/").pop();
    const when = before ? `just before commit ${c.sha.slice(0, 7)}, which removed it` : `in commit ${c.sha.slice(0, 7)}`;
    acts.append(el("button", { title: `Put ${f.path} back as it was ${when}`, onclick: () => restore(before ? c.parent : c.sha, f.path, when, before) },
                   icon("restore", 12), el("span", { textContent: `Restore ${name}` })));
  }
  if (acts.childNodes.length) card.append(acts);
  return card;
}

function toggle(c) {
  if (open === c.rev) [open, shownFile] = [null, null];
  else { // (the file open in the source pane, if the commit changed it; else its first source, as a figure shows no text)
    const first = c.files.find((f) => f.path === ed?.path) || c.files.find((f) => SOURCES.test(f.path)) || c.files[0];
    [open, shownFile] = [c.rev, first?.path || null];
  }
  draw();
  if (shownFile) fetchDiff(open, shownFile);
}
function showFile(rev, path) {
  shownFile = path;
  draw();
  fetchDiff(rev, path);
}
async function fetchDiff(rev, path) {
  const key = `${rev}|${path}`, seq = worktreeSeq;
  if (diffs.has(key) && !diffs.get(key).error) return; // (one that failed is asked again)
  const got = await getJSON(`history/diff?${new URLSearchParams({ rev, path })}`), body = got.body;
  if (rev === "WORKTREE" && seq !== worktreeSeq) return;
  diffs.set(key, got.ok && typeof body.diff === "string" ? body : { error: `The changes did not load: ${got.error || "no diff in the answer"}` });
  if (diffs.size > 60) diffs.delete(diffs.keys().next().value); // (the oldest goes)
  draw();
}

// A file's diff, its lines colored: added, removed, and the hunks' headers (git's own header lines, before the first
// hunk, left out).
function diffView(got) {
  if (got.error) return el("div", { className: "hist-note", textContent: got.error });
  let inHunk = false;
  const lines = got.diff.split("\n").filter((l) => l && ((inHunk ||= l.startsWith("@@")) || l.startsWith("Binary files ")));
  const pre = el("pre", { className: "hist-diff" }, ...lines.map((l) => el("span", {
    className: l.startsWith("@@") ? "d-hunk" : l.startsWith("+") ? "d-add" : l.startsWith("-") ? "d-del" : "", textContent: l })));
  if (!lines.length) pre.textContent = "(no changes to show as text)";
  return got.truncated ? el("div", {}, pre, el("div", { className: "hist-note", textContent: "Cut short: too long to show here." })) : pre;
}

// Restore: the file open in the source pane is saved first (should that fail, nothing is restored), and live.py writes
// the text only if the file is still as that save left it; then it reloads in place. A file not open opens.
async function restore(rev, path, when, removed) {
  const wasOpen = ed?.path === path && !ed.gone;
  const unsaved = wasOpen && ed.dirty ? " Your edits not saved yet are saved first, so they are replaced too." : "";
  const gone = srcFiles ? !srcFiles.files.some((f) => f.path === path) : removed; // (the list not loaded yet: as the commit left it)
  const what = gone ? "It is not in the paper now, so it comes back under that name." : "This replaces its text as it is now.";
  if (!confirm(`Put ${path} back as it was ${when}? ${what}${unsaved}`)) return;
  if (wasOpen && !(await flushSource())) return setStatus(`${path} has edits that are not saved, so it was not restored`, "error");
  const version = ed?.path === path && !ed.gone && !ed.dirty ? ed.baseVersion : undefined;
  setStatus(`Restoring ${path}…`, "busy");
  const got = await sendJSON("history/restore", { rev, path, ...(version ? { version } : {}) }), done = got.body;
  if (!got.ok) return setStatus(`${path} was not restored: ${got.error}`, "error");
  wrote(); // (a build comes: behind)
  if (done.created) await loadFiles();
  if (ed?.path === done.path && ed.gone) await closeTab(done.path, { discard: true }); // (its tab said it was gone: it opens anew)
  const sideWas = !$("side").hidden;
  if (ed?.path === done.path) await reloadSource();
  else if (editorShown()) await openFile(done.path);
  else await showEditor(true, { path: done.path });
  if (sideWas && $("side").hidden) { // (the source pane took its room: said with the restore, which it would replace)
    setStatus(`Restored ${done.path} as it was ${when}; the sidebar closed to make room for the source beside the PDF`, "fresh", HOLD);
  } else {
    setStatus(`Restored ${done.path} as it was ${when}`, "fresh");
  }
  loadHistory();
}
