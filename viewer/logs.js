// The build's log, for editors (live.py /logs, once per build): its errors and warnings marked in the source (a wavy
// underline, a mark in the gutter, a card on hover), the build's state in the source pane's status bar, and the drawer
// under the source (Cmd/Ctrl+Shift+L, or that status) that lists them, or shows the end of the log itself.

import { $, docFile, editorShown, el, isDiff, isPopout } from "./page.js";
import { editable, editorAllowed, me } from "./role.js";
import { addEditorExtension } from "./cm.js";
import { cmView, ed, openFile, showEditor } from "./editor.js";
import { registerCommands } from "./palette.js";
import { icon } from "./icons.js";

const allowed = () => !isDiff && !isPopout && !!me.can.logs && editorAllowed();

// The build as the status poll last saw it ({building, ok, durationMs, errors: how many, key: which build}), and the
// log of the build key names, once it came ({errors, warnings, raw, truncated}).
let build = null, logs = null, logsKey = "", logsAsked = "", logsSeq = 0;

// The status poll's news of the build (poll.js, on every answer): the build's state in the status bar, and the log
// of each build that ended, asked for once.
export function noteBuild(info) {
  if (!allowed()) return;
  const b = info.build || {};
  build = { building: !!info.building, ok: b.ok, durationMs: b.durationMs, errors: (b.errors || []).length,
            key: JSON.stringify([b.time, b.began, b.ok, b.durationMs, (b.errors || []).length]) };
  if (!build.building && b.time && build.key !== logsAsked) loadLogs(build.key);
  showBuildState();
}

async function loadLogs(key) {
  logsAsked = key;
  const seq = ++logsSeq;
  try {
    const r = await fetch(`logs/${encodeURIComponent(docFile)}`, { cache: "no-store" });
    const body = r.ok ? await r.json() : null;
    if (seq !== logsSeq || !body || !Array.isArray(body.errors) || !Array.isArray(body.warnings)) return;
    [logs, logsKey] = [body, key];
  } catch {
    return; // (the next build asks again; opening the drawer too)
  }
  showBuildState();
  showMarks();
  if (!$("logsDrawer").hidden) showLogs();
}

// The status bar's account of the build, from the status poll's answers alone (so that it changes only when the
// build does), with the warnings once its log has come: building, how long it took, or how many errors it had.
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const warningCount = () => logs.warnings.filter((w) => w.severity !== "info").length;
function showBuildState() {
  const pill = $("buildState");
  if (!build) return void (pill.hidden = true);
  const current = logs && logsKey === build.key;
  const warnings = current && warningCount() ? ` · ${plural(warningCount(), "warning")}` : "";
  let text = "", cls = "";
  if (build.building) [text, cls] = ["Building…", "busy"];
  else if (build.ok === false) [text, cls] = [`✗ ${plural(Math.max(1, current ? logs.errors.length : build.errors), "error")}${warnings}`, "error"];
  else if (build.ok) {
    const ms = build.durationMs;
    [text, cls] = [`✓ ${typeof ms !== "number" ? "built" : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`}${warnings}`, warnings ? "warn" : "ok"];
  }
  if (pill.textContent !== text) pill.textContent = text;
  if (pill.className !== cls) pill.className = cls;
  pill.hidden = !text;
}

// The drawer: every error and warning, the errors, the warnings (boxes too), or the end of the log as it is. A row
// with a source file the editor opens goes to its line.
let filter = "all";
export function toggleLogs(open = $("logsDrawer").hidden) {
  if (open && !allowed()) return;
  $("logsDrawer").hidden = !open;
  $("buildState").setAttribute("aria-expanded", String(open));
  if (open) {
    if (build && !build.building && logsKey !== build.key) loadLogs(build.key); // (also when asking for it failed)
    else if (!logs && !build) loadLogs("");
    showLogs();
  }
}
function showLogs() {
  const body = $("logsBody");
  $("logsErrCount").textContent = logs?.errors.length || "";
  $("logsWarnCount").textContent = logs?.warnings.length || "";
  if (!logs) return body.replaceChildren(el("div", { className: "empty", textContent: "No build log yet." }));
  if (filter === "raw") {
    body.replaceChildren(el("pre", { className: "log-raw", textContent: `${logs.truncated ? "(the end of the log)\n" : ""}${logs.raw || "(the log is empty)"}` }));
    return void (body.scrollTop = body.scrollHeight);
  }
  const items = [...(filter !== "warning" ? logs.errors : []), ...(filter !== "error" ? logs.warnings : [])];
  if (!items.length) return body.replaceChildren(el("div", { className: "empty", textContent: filter === "error" ? "No errors." : filter === "warning" ? "No warnings." : "No errors or warnings." }));
  body.replaceChildren(...items.map((d) => {
    const opens = d.file && d.line && editable(d.file);
    return el("div", { className: `log-row${opens ? " opens" : ""}`, title: opens ? `Open ${d.file}:${d.line}` : "",
                       onclick: opens ? () => openFile(d.file, d.line) : null },
              el("span", { className: `log-sev ${d.severity}`, textContent: d.severity === "info" ? "box" : d.severity }),
              ...(d.file ? [el("span", { className: "log-loc", textContent: d.line ? `${d.file}:${d.line}` : d.file })] : []),
              el("span", { className: "log-msg", textContent: d.msg }));
  }));
}
for (const b of $("logsTabs").children) {
  b.onclick = () => {
    filter = b.dataset.filter;
    for (const other of $("logsTabs").children) other.classList.toggle("on", other === b);
    showLogs();
  };
}
$("logsClose").replaceChildren(icon("close", 13));
$("logsClose").onclick = () => toggleLogs(false);
$("buildState").onclick = () => toggleLogs();

// Cmd/Ctrl+Shift+L (keys.js), and the palette: the drawer, with the source pane open.
export function showLogsKey() {
  if (!allowed()) return false;
  if (editorShown()) return void toggleLogs();
  showEditor(true, { focus: false });
  toggleLogs(true);
}
registerCommands(() => (allowed() ? [{ title: "Build log and warnings", sub: "Build", icon: "log", run: () => {
  if (!editorShown()) showEditor(true, { focus: false });
  toggleLogs(true);
} }] : []));

// The marks in the source: those of the open file's errors and warnings (not underfull boxes), set when its log
// comes and as each file opens (the field's create), and moved with the edits made since.
const fileMarks = () => (logs && ed ? [...logs.errors, ...logs.warnings].filter((d) => d.file === ed.path && d.line && d.severity !== "info") : []);
let setMarks = null; // (the state effect that sets them: once CodeMirror has loaded)
function showMarks() {
  if (setMarks && cmView) cmView.dispatch({ effects: setMarks.of(fileMarks()) });
}
addEditorExtension(({ state, view }) => {
  setMarks = state.StateEffect.define();
  class Dot extends view.GutterMarker { // (with the diagnostics of its line, for the card)
    constructor(severity, diags) { super(); this.severity = severity; this.diags = diags; }
    eq(other) { return other.severity === this.severity && other.diags === this.diags; }
    toDOM() { return el("span", { className: `cm-diag-dot ${this.severity}`, title: this.diags.map((d) => d.msg).join("\n") }); }
  }
  const marksOf = (doc, list) => { // ({marks: the underlines, dots: the gutter's marks})
    const lines = new Map();
    for (const d of list) {
      if (!(d.line >= 1 && d.line <= doc.lines)) continue;
      let n = d.line; // (a box's paragraph ends at a blank line: marked on the text above it)
      while (d.kind === "overfull" && n > 1 && d.line - n < 3 && !doc.line(n).text.trim()) n--;
      lines.set(n, [...(lines.get(n) || []), d]);
    }
    const marks = [], dots = [];
    for (const [number, diags] of [...lines].sort((a, b) => a[0] - b[0])) {
      const line = doc.line(number), severity = diags.some((d) => d.severity === "error") ? "error" : "warning";
      const start = line.from + Math.max(0, line.text.search(/\S/));
      if (start < line.to) marks.push(view.Decoration.mark({ class: `cm-diag-${severity}` }).range(start, line.to));
      dots.push(new Dot(severity, diags).range(line.from));
    }
    return { marks: view.Decoration.set(marks), dots: state.RangeSet.of(dots) };
  };
  const field = state.StateField.define({
    create: (st) => marksOf(st.doc, fileMarks()),
    update: (value, tr) => {
      const set = tr.effects.reduce((last, e) => (e.is(setMarks) ? e : last), null);
      if (set) return marksOf(tr.state.doc, set.value);
      return tr.docChanged ? { marks: value.marks.map(tr.changes), dots: value.dots.map(tr.changes) } : value;
    },
    provide: (f) => view.EditorView.decorations.from(f, (value) => value.marks),
  });
  // The card for a marked line under the pointer: what the log says about it.
  const card = view.hoverTooltip((v, pos) => {
    const line = v.state.doc.lineAt(pos);
    let dot = null;
    v.state.field(field).dots.between(line.from, line.to, (from, to, value) => { dot ??= value; }); // (after lines were joined: inside it)
    if (!dot) return null;
    return { pos: line.from + Math.max(0, line.text.search(/\S/)), end: line.to, above: true, create: () => {
      const dom = el("div", { className: "cm-hover-card" });
      for (const d of dot.diags.slice(0, 4)) {
        const kind = d.severity === "error" ? "LaTeX error" : d.kind === "overfull" ? "Overfull box" : "Warning";
        dom.append(el("div", { className: `hc-kind ${d.severity}`, textContent: kind }), el("div", { className: "hc-meta", textContent: d.msg }));
      }
      if (dot.diags.length > 4) dom.append(el("div", { className: "hc-meta", textContent: `and ${dot.diags.length - 4} more` }));
      dom.append(el("button", { className: "hc-link", textContent: "Open the build log", onclick: () => toggleLogs(true) }));
      return { dom };
    } };
  }, { hoverTime: 300 });
  const gutter = view.gutter({ class: "cm-diag-gutter", markers: (v) => v.state.field(field).dots,
                               initialSpacer: () => new Dot("spacer", []) });
  return [field, gutter, card];
});
