// Build Logs & Inline Diagnostics: polls GET /logs/<doc>.pdf after builds, pushes red/amber
// squiggles & gutter markers into CodeMirror, updates the Lattice build-time pill, and renders
// the collapsible Logs & Warnings drawer inside #editorPane.

import { $, docFile, editorShown, el, isDiff } from "./page.js";
import { canOpenFile, editorAllowed, me } from "./role.js";
import { cmView, ed, onFileOpen, openFile, showEditor } from "./editor.js";
import { setDiagnostics } from "./cm.js";

let latestLogs = null; // {ok, time, durationMs, errors, warnings, raw, truncated}
let activeFilter = "all";
let lastFetchedKey = "";

export function toggleLogs(force = null) {
  const drawer = $("logsDrawer");
  if (!drawer) return;
  const open = force === null ? drawer.hidden : !!force;
  drawer.hidden = !open;
  if (open && !latestLogs) loadLogs(true);
  else if (open) renderLogs();
}

export function applyFileDiagnostics() {
  if (!cmView || !ed || !latestLogs) return;
  const all = [...(latestLogs.errors || []), ...(latestLogs.warnings || [])];
  const mine = all.filter((d) => d.file && d.file === ed.path);
  setDiagnostics(cmView, mine);
}

export function updateBuildPill({ building = false, build = null } = {}) {
  const pill = $("editorBuildPill");
  if (!pill) return;
  if (isDiff || !me.can.logs) { pill.hidden = true; return; }
  pill.hidden = false;
  if (building) {
    pill.className = "build-pill busy";
    pill.textContent = "Building…";
    return;
  }
  const b = build || latestLogs;
  if (!b) { pill.hidden = true; return; }
  const errs = (b.errors || []).length;
  if (b.ok === false || errs > 0) {
    pill.className = "build-pill error";
    pill.textContent = `${errs || 1} error${errs === 1 ? "" : "s"}`;
    return;
  }
  const ms = b.durationMs;
  const dur = typeof ms === "number" ? (ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`) : "Built";
  pill.className = "build-pill";
  pill.textContent = `✓ ${dur}`;
}

function updateDiagFooter() {
  const btn = $("editorDiagBtn");
  if (!btn || !latestLogs) return;
  const nErr = (latestLogs.errors || []).length;
  const nWarn = (latestLogs.warnings || []).length;
  btn.hidden = false;
  btn.className = `foot-btn${nErr ? " has-err" : nWarn ? " has-warn" : ""}`;
  btn.textContent = nErr
    ? `${nErr} error${nErr === 1 ? "" : "s"}${nWarn ? ` · ${nWarn} warn` : ""}`
    : nWarn ? `${nWarn} warning${nWarn === 1 ? "" : "s"}` : "0 issues";
  $("logsErrCount").textContent = nErr || "";
  $("logsWarnCount").textContent = nWarn || "";
}

export async function loadLogs(force = false, cacheKey = "") {
  if (isDiff || !me.can.logs) return;
  if (!force && cacheKey && cacheKey === lastFetchedKey) return;
  lastFetchedKey = cacheKey || String(Date.now());
  try {
    const r = await fetch(`logs/${encodeURIComponent(docFile)}`, { cache: "no-store" });
    if (!r.ok) return;
    latestLogs = await r.json();
    updateDiagFooter();
    updateBuildPill({ building: false, build: latestLogs });
    applyFileDiagnostics();
    if (!$("logsDrawer").hidden) renderLogs();
  } catch {}
}

function renderLogs() {
  const body = $("logsBody");
  if (!body) return;
  if (!latestLogs) {
    body.replaceChildren(el("div", { className: "empty", textContent: "No build log yet." }));
    return;
  }
  if (activeFilter === "raw") {
    body.replaceChildren(el("pre", { className: "log-raw", textContent: latestLogs.raw || "(empty log)" }));
    body.scrollTop = body.scrollHeight;
    return;
  }
  const items = [
    ...(latestLogs.errors || []).map((e) => ({ ...e, severity: "error" })),
    ...(latestLogs.warnings || []),
  ].filter((d) => activeFilter === "all" || (activeFilter === "error" ? d.severity === "error" : d.severity !== "error"));

  if (!items.length) {
    body.replaceChildren(el("div", { className: "empty", textContent: activeFilter === "error" ? "No errors." : "No warnings or errors." }));
    return;
  }
  const rows = items.map((d) => {
    const loc = d.file ? `${d.file}${d.line ? `:${d.line}` : ""}` : "";
    const row = el("div", {
      className: "log-row",
      onclick: async () => {
        if (d.file && editorAllowed() && canOpenFile(d.file)) {
          if (!editorShown()) await showEditor(true, { path: d.file, line: d.line || 1 });
          else await openFile(d.file, d.line || 1);
        }
      },
    },
      el("span", { className: `log-sev ${d.severity || "warning"}`, textContent: d.severity || "warn" }),
      ...(loc ? [el("span", { className: "log-loc", textContent: loc })] : []),
      el("span", { className: "log-msg", textContent: d.msg || "" }),
    );
    return row;
  });
  body.replaceChildren(...rows);
}

for (const b of document.querySelectorAll("#logsHead .logs-tabs button")) {
  b.onclick = () => {
    activeFilter = b.dataset.filter;
    for (const x of document.querySelectorAll("#logsHead .logs-tabs button")) x.classList.toggle("on", x === b);
    renderLogs();
  };
}
$("logsClose").onclick = () => toggleLogs(false);
$("editorDiagBtn").onclick = () => toggleLogs();
$("editorBuildPill").onclick = () => toggleLogs();

onFileOpen(() => applyFileDiagnostics());
