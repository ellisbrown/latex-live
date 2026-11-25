// Who else has the paper open, for editors and the owner (live.py POST /presence, and the list in each status): their
// avatars in the top bar, a dot on the tab of each file someone has open, and a mark on the line each one is on in the
// open file. In a preview of your own, your other pages show there too.

import { $, editorShown, el, isDiff, isPopout } from "./page.js";
import { postJSON } from "./net.js";
import { EDITOR_ID } from "./drafts.js";
import { addEditorExtension } from "./cm.js";
import { cmView, ed, setTabPeers } from "./editor.js";

// This page tells live.py where it is when its file or line changes (once that has held for a tick, TICK ms) and every
// HEARTBEAT ms besides (moving or not), while it is shown; live.py lists a page until it has been quiet for 30 s. A
// pop-out window or a comparison is part of another page, and says nothing. Until live.py first answers (told), this
// page doesn't know its own key in the list (ownTab), so the list shows none of the user's own pages yet.
const TICK = 500, HEARTBEAT = 12000;
let started = false, listed = false, sending = false, told = false, sentAt = 0, sentKey = "", seenKey = "", ownTab = "";

const PALETTE = ["#2563eb", "#0d9488", "#d97706", "#7c3aed", "#db2777", "#16a34a", "#0284c7", "#dc2626"];
function peerColor(key) {
  let h = 7;
  for (const ch of String(key || "")) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return PALETTE[h % PALETTE.length];
}

function here() { // (the file open in the source pane, and the cursor's line)
  const open = editorShown() && ed && !ed.gone && cmView ? ed.path : null;
  return { open, line: open ? cmView.state.doc.lineAt(cmView.state.selection.main.head).number : null };
}
async function tell(where, key) {
  sending = true;
  [sentAt, sentKey] = [Date.now(), key];
  try {
    const r = await postJSON("presence", { tab: EDITOR_ID, ...where });
    const body = r.ok ? await r.json() : null;
    if (typeof body?.tab === "string") ownTab = body.tab;
  } catch {} // (the next heartbeat tries again)
  sending = false;
  told = true;
  render();
}
function tick() {
  if (document.hidden || sending || !listed) return;
  const where = here(), key = JSON.stringify(where);
  const due = Date.now() - sentAt > HEARTBEAT;
  if (key !== sentKey && key !== seenKey && !due) return void (seenKey = key); // (still moving: wait for it to settle)
  seenKey = key;
  if (key !== sentKey || due) tell(where, key);
}
function leave() { // (only once, and only while this page says where it is)
  if (!listed || !sentAt) return;
  sentAt = 0;
  postJSON("presence", { tab: EDITOR_ID, leave: true }, true).catch(() => {});
}
function start() {
  started = true;
  setInterval(tick, TICK);
  tick();
  document.addEventListener("visibilitychange", () => (document.hidden ? leave() : tick())); // (a page in the background is not there)
  addEventListener("pagehide", leave);
}

// The list as last shown (and its JSON but for this page, to tell a change), and who has each file open, other than
// this page: {path: [{user, name, line, color, you}]} (you: your own other page), each person once per line.
let list = [], shownKey = null, byFile = {};
const listeners = [];

// Each status answer's list (poll.js): from a live.py that gives editors and the owner one, which also means this page
// may say where it is (until an answer comes without it). Shown again only when it changed.
export function showPresence(next) {
  if (isDiff || isPopout) return;
  listed = Array.isArray(next);
  if (!listed && !started) return;
  if (!started) start();
  list = listed ? next : []; // (none: the role no longer sees it)
  render();
}
function render() {
  const others = list.filter((p) => p.tab !== ownTab && (told || !p.you)); // (this page's own moves change nothing shown)
  const key = JSON.stringify(others);
  if (key === shownKey) return;
  shownKey = key;
  byFile = {};
  for (const p of others) {
    if (!p.file) continue;
    const peer = { user: p.user, name: p.you ? "You (another page)" : p.name, line: p.line, color: peerColor(p.user), you: !!p.you };
    if (!(byFile[p.file] ||= []).some((q) => q.user === peer.user && q.line === peer.line)) byFile[p.file].push(peer);
  }
  showAvatars();
  setTabPeers(Object.fromEntries(Object.entries(byFile).map(([path, peers]) => // (a dot per person, not per page)
    [path, peers.filter((q, i) => peers.findIndex((r) => r.user === q.user) === i)])));
  showLineMarks();
  for (const fn of listeners) fn(byFile);
}

// For other parts of the viewer (a list of the files, say): fn(byFile), now and on each change.
export function onPresence(fn) {
  listeners.push(fn);
  fn(byFile);
}

// The avatars: one for each other user with the paper open, with their name and where they are.
const initials = (name) => {
  const words = (String(name || "").trim() || "?").split(/\s+/).map((w) => [...w]); // (by code point: an emoji is one)
  return (words.length > 1 ? words[0][0] + words.at(-1)[0] : words[0].slice(0, 2).join("")).toUpperCase();
};
function showAvatars() {
  const users = new Map();
  for (const p of list) {
    if (p.you) continue;
    const u = users.get(p.user) || { name: p.name, key: p.user, places: [] };
    u.places.push(p.file ? `${p.file}${p.line ? `:${p.line}` : ""}` : "the PDF");
    users.set(p.user, u);
  }
  const box = $("presence"), shown = [...users.values()];
  box.hidden = !shown.length;
  box.replaceChildren(...shown.slice(0, 5).map((u) => {
    const av = el("span", { className: "avatar", textContent: initials(u.name), title: `${u.name}: ${[...new Set(u.places)].join(", ")}` });
    av.style.background = peerColor(u.key);
    return av;
  }), ...(shown.length > 5 ? [el("span", { className: "avatar more", textContent: `+${shown.length - 5}`,
                                             title: shown.slice(5).map((u) => u.name).join(", ") })] : []));
  box.setAttribute("aria-label", shown.length ? `Also here: ${shown.map((u) => u.name).join(", ")}` : "");
}

// The marks in the open file: a bar beside each line someone else is on, and their name at its end, set as the list
// changes (only then) and as each file opens (the field's create), and moved with the edits made since.
const lineMarks = () => (ed && byFile[ed.path] || []).filter((p) => p.line);
let setMarks = null; // (the state effect that sets them: once CodeMirror has loaded)
function showLineMarks() {
  const marks = lineMarks(), key = JSON.stringify(marks);
  if (setMarks && cmView && cmView.state.field(markField, false)?.key !== key) cmView.dispatch({ effects: setMarks.of(marks) });
}
let markField = null;
addEditorExtension(({ state, view }) => {
  setMarks = state.StateEffect.define();
  class Badge extends view.WidgetType {
    constructor(name, color) { super(); this.name = name; this.color = color; }
    eq(other) { return other.name === this.name && other.color === this.color; }
    toDOM() {
      const badge = el("span", { className: "cm-peer-badge", textContent: this.name });
      badge.style.background = this.color;
      return badge;
    }
  }
  const marksOf = (doc, marks) => {
    const ranges = [];
    for (const p of [...marks].sort((a, b) => a.line - b.line)) {
      if (p.line > doc.lines) continue; // (the line is in their text, not yet in this page's)
      const line = doc.line(p.line);
      ranges.push(view.Decoration.line({ class: "cm-peer-line", attributes: { style: `--peer-color: ${p.color}` } }).range(line.from),
                  view.Decoration.widget({ widget: new Badge(p.name, p.color), side: 1 }).range(line.to));
    }
    return { key: JSON.stringify(marks), decos: view.Decoration.set(ranges, true) };
  };
  markField = state.StateField.define({
    create: (st) => marksOf(st.doc, lineMarks()),
    update: (value, tr) => {
      const set = tr.effects.reduce((last, e) => (e.is(setMarks) ? e : last), null);
      if (set) return marksOf(tr.state.doc, set.value);
      return tr.docChanged ? { key: value.key, decos: value.decos.map(tr.changes) } : value;
    },
    provide: (f) => view.EditorView.decorations.from(f, (value) => value.decos),
  });
  return markField;
});
