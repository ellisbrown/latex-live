// Sidebar History tab: interactive git timeline with expandable syntax-colored inline diffs,
// one-click file restore (owner), and latexdiff PDF comparison trigger.

import { $, editorShown, el, setStatus } from "./page.js";
import { postJSON, readBody } from "./net.js";
import { me } from "./role.js";
import { ed, openFile, reloadSource, showEditor } from "./editor.js";
import { startCompare } from "./compare.js";
import { icon } from "./icons.js";

let historyData = null;
let expandedRev = null;
let expandedFile = null;
let diffCache = new Map();

export async function loadHistory() {
  if (!me.can.history) return;
  const panel = $("history");
  if (!historyData) panel.replaceChildren(el("div", { className: "empty", textContent: "Loading git history…" }));
  const r = await fetch("history", { cache: "no-store" }).catch(() => null);
  if (!r?.ok) {
    panel.replaceChildren(el("div", { className: "empty", textContent: "Could not load git history." }));
    return;
  }
  historyData = await r.json();
  diffCache.clear();
  renderHistory();
}

function renderDiffPre(diffText) {
  const pre = el("pre", { className: "diff-pre" });
  if (!diffText || !diffText.trim()) {
    pre.textContent = "(no text diff)";
    return pre;
  }
  const frag = document.createDocumentFragment();
  for (const line of diffText.split("\n")) {
    if (line.startsWith("diff --git ") || line.startsWith("index ") || line.startsWith("--- ") || line.startsWith("+++ ")) continue;
    const cls = line.startsWith("@@") ? "d-hunk"
      : line.startsWith("+") ? "d-add"
      : line.startsWith("-") ? "d-del" : "d-ctx";
    frag.append(el("span", { className: cls, textContent: line || " " }));
  }
  pre.append(frag);
  return pre;
}

async function fetchDiff(rev, file = null) {
  const key = `${rev}|${file || ""}`;
  if (diffCache.has(key)) return diffCache.get(key);
  const q = new URLSearchParams({ rev });
  if (file) q.set("file", file);
  const r = await fetch(`history/diff?${q}`, { cache: "no-store" }).catch(() => null);
  const data = r?.ok ? await r.json() : { diff: "", error: "Could not load diff" };
  diffCache.set(key, data);
  return data;
}

async function doRestore(rev, path) {
  if (!confirm(`Restore ${path} to its contents in commit ${rev}? Current uncommitted edits to ${path} will be overwritten.`)) return;
  setStatus(`Restoring ${path} from ${rev}…`, "busy");
  const r = await postJSON("history/restore", { rev, path }).catch(() => null);
  const res = r ? await readBody(r) : {};
  if (!r?.ok) return setStatus(res.error || "Restore failed", "error");
  setStatus(`Restored ${path} from ${rev}`, "fresh");
  if (ed?.path === path) await reloadSource();
  else if (editorShown()) await openFile(path);
  else await showEditor(true, { path });
  loadHistory();
}

function buildEntry(item, { isWorktree = false } = {}) {
  const rev = isWorktree ? "WORKTREE" : item.rev;
  const open = expandedRev === rev;
  const card = el("div", { className: "hist-item" });
  const head = el("div", {
    className: "hist-head",
    onclick: async () => {
      if (expandedRev === rev) { expandedRev = null; expandedFile = null; renderHistory(); return; }
      expandedRev = rev;
      expandedFile = item.files?.[0]?.path || null;
      renderHistory();
      await fetchDiff(rev, expandedFile);
      renderHistory();
    },
  },
    el("div", { className: "hist-title", textContent: isWorktree ? "Uncommitted changes" : (item.subject || "(no message)") }),
    el("div", { className: "hist-meta" },
      el("code", { textContent: isWorktree ? "working tree" : item.rev }),
      ...(item.author ? [el("span", { textContent: item.author })] : []),
      el("span", { textContent: isWorktree ? `${item.files.length} file${item.files.length === 1 ? "" : "s"}` : (item.relDate || item.date) }),
    ),
  );
  card.append(head);

  if (open) {
    const wrap = el("div", { className: "hist-diff-wrap" });
    const files = item.files || [];
    if (files.length) {
      const pills = el("div", { className: "hist-files" });
      for (const f of files.slice(0, 12)) {
        const pill = el("button", {
          className: `hist-file-pill${expandedFile === f.path ? " on" : ""}`,
          textContent: `${f.status} ${f.path.split("/").pop()}`,
          title: f.path,
          onclick: async () => {
            expandedFile = expandedFile === f.path ? null : f.path;
            renderHistory();
            await fetchDiff(rev, expandedFile);
            renderHistory();
          },
        });
        pills.append(pill);
      }
      wrap.append(pills);
    }
    const key = `${rev}|${expandedFile || ""}`;
    const cached = diffCache.get(key);
    if (!cached) {
      wrap.append(el("div", { className: "empty", textContent: "Loading diff…" }));
    } else {
      wrap.append(renderDiffPre(cached.diff));
      const acts = el("div", { className: "hist-actions" });
      if (!isWorktree && me.can.compare) {
        acts.append(el("button", {
          className: "panel-btn",
          textContent: "Compare PDF (latexdiff)",
          onclick: () => startCompare(item.rev),
        }));
      }
      if (!isWorktree && me.can.restore && expandedFile && /\.(tex|bib|sty|cls)$/i.test(expandedFile)) {
        acts.append(el("button", {
          className: "panel-btn",
          onclick: () => doRestore(item.rev, expandedFile),
        }, icon("restore", 13), el("span", { textContent: `Restore ${expandedFile.split("/").pop()}` })));
      }
      if (acts.childNodes.length) wrap.append(acts);
    }
    card.append(wrap);
  }
  return card;
}

export function renderHistory() {
  const panel = $("history");
  if (!panel) return;
  if (!historyData) return;
  if (!historyData.isGit) {
    panel.replaceChildren(el("div", { className: "empty", textContent: "This paper folder is not a git repository." }));
    return;
  }
  const scroll = panel.scrollTop;
  const frag = document.createDocumentFragment();
  const top = el("div", { className: "hist-branch" },
    el("span", { className: "badge", textContent: historyData.branch || "HEAD" }),
    el("span", { textContent: `${historyData.commits.length} commits` }),
    el("button", { className: "panel-btn", style: "margin-left:auto", title: "Refresh history", onclick: loadHistory }, icon("rebuild", 12)),
  );
  frag.append(top);
  if (historyData.dirty && historyData.dirtyFiles?.length) {
    frag.append(buildEntry({ files: historyData.dirtyFiles }, { isWorktree: true }));
  }
  for (const c of historyData.commits) frag.append(buildEntry(c));
  panel.replaceChildren(frag);
  panel.scrollTop = scroll;
}
