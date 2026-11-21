// Command Palette (Cmd/Ctrl+K) & Cross-file Project Search (Cmd/Ctrl+Shift+F):
// jump to project files, outline headings, citations, search text across all sources, or run
// workspace commands (themes, tints, layouts, rebuild, logs, checks, polish).

import { $, editorShown, el } from "./page.js";
import { editorAllowed, me } from "./role.js";
import { rebuild } from "./build.js";
import { openFile, refreshFilesList, showEditor, srcFiles } from "./editor.js";
import { toggleLogs } from "./logs.js";
import { getPapers } from "./papers.js";
import { agentReady, reviseSelection } from "./agent.js";
import { pdfInvert, setPdfInvert, setTheme, setTint, theme, TINTS } from "./theme.js";
import { icon } from "./icons.js";

let mode = "jump"; // "jump" | "search"
let items = [];
let selIndex = 0;
let searchTimer = null;
let searchSeq = 0;

function workspaceCommands() {
  const cmds = [
    { group: "Commands", ic: "sun", title: `Switch to ${theme === "dark" ? "Light" : "Dark"} theme`, sub: "Appearance", run: () => setTheme(theme === "dark" ? "light" : "dark") },
    { group: "Commands", ic: "invert", title: `${pdfInvert ? "Disable" : "Enable"} PDF color inversion`, sub: "Appearance", run: () => setPdfInvert(!pdfInvert) },
    ...TINTS.map((t) => ({ group: "Commands", ic: "sun", title: `Surface tint: ${t.label}`, sub: "Appearance", run: () => setTint(t.id) })),
    { group: "Commands", ic: "popout", title: "Pop out PDF in a synchronized window", sub: "Layout", run: () => $("popoutBtn").click() },
  ];
  if (editorAllowed()) {
    cmds.push(
      { group: "Commands", ic: "split", title: "Layout: Split (Source + PDF)", sub: "Layout", run: () => showEditor(true, { mode: "split" }) },
      { group: "Commands", ic: "edit", title: "Layout: Source focus", sub: "Layout", run: () => showEditor(true, { mode: "source" }) },
      { group: "Commands", ic: "pages", title: "Layout: PDF only", sub: "Layout", run: () => showEditor(false, { mode: "pdf" }) },
    );
  }
  if (me.can.logs) cmds.push({ group: "Commands", ic: "logs", title: "Toggle Build Logs & Warnings drawer", sub: "⌘⇧L", run: () => { if (!editorShown()) showEditor(true); toggleLogs(); } });
  if (me.can.rebuild) cmds.push({ group: "Commands", ic: "rebuild", title: "Rebuild PDF from scratch", sub: "Build", run: rebuild });
  if (agentReady()) {
    cmds.push(
      { group: "Commands", ic: "sparkle", title: "AI Polish selected paragraph", sub: "Agent", run: () => reviseSelection("polish") },
      { group: "Commands", ic: "sparkle", title: "AI Proofread selected paragraph", sub: "Agent", run: () => reviseSelection("proofread") },
    );
  }
  return cmds;
}

function buildJumpItems(raw) {
  const onlyCmds = raw.startsWith(">");
  const q = (onlyCmds ? raw.slice(1) : raw).trim().toLowerCase();
  const match = (s) => !q || s.toLowerCase().includes(q);
  const out = [];

  if (!onlyCmds) {
    if (editorAllowed()) {
      for (const f of srcFiles?.files || []) {
        if (match(f.path)) {
          out.push({
            group: "Files", ic: "file", title: f.path.split("/").pop(), sub: f.path,
            run: () => (editorShown() ? openFile(f.path) : showEditor(true, { path: f.path })),
          });
        }
      }
    }
    // PDF outline headings are available to every role (including viewers and commenters).
    for (const span of document.querySelectorAll("#outline li > span")) {
      const t = span.querySelector(".t")?.textContent || "";
      const num = span.querySelector(".num")?.textContent || "";
      const full = `${num} ${t}`.trim();
      if (full && match(full)) {
        out.push({ group: "Sections", ic: "outline", title: full, sub: "PDF outline", run: () => span.click() });
      }
    }
    if (editorAllowed()) {
      for (const p of getPapers()) {
        if (match(`${p.key} ${p.title} ${p.author} ${p.year}`)) {
          out.push({
            group: "References", ic: "papers", title: p.title || p.key, sub: `${p.key}${p.year ? ` · ${p.year}` : ""}`,
            run: () => (editorShown() ? openFile(p.file, p.line) : showEditor(true, { path: p.file, line: p.line })),
          });
        }
      }
    }
  }
  for (const c of workspaceCommands()) {
    if (match(`${c.title} ${c.sub}`)) out.push(c);
  }
  return out.slice(0, 60);
}

async function runProjectSearch(q) {
  const seq = ++searchSeq;
  const box = $("paletteResults");
  if (!q.trim()) {
    items = [];
    box.replaceChildren(el("div", { className: "empty", textContent: "Type to search across all .tex, .bib, .sty, and .cls files…" }));
    return;
  }
  box.replaceChildren(el("div", { className: "empty", textContent: "Searching project files…" }));
  const r = await fetch(`search?${new URLSearchParams({ q: q.trim() })}`, { cache: "no-store" }).catch(() => null);
  if (seq !== searchSeq) return;
  if (!r?.ok) {
    items = [];
    box.replaceChildren(el("div", { className: "empty", textContent: "Search is available to editors." }));
    return;
  }
  const data = await r.json();
  items = (data.matches || []).map((m) => ({
    group: m.file,
    ic: "search",
    title: m.text.trim(),
    sub: `${m.file}:${m.line}`,
    col: m.col,
    len: m.len,
    run: () => (editorShown() ? openFile(m.file, m.line, m.col) : showEditor(true, { path: m.file, line: m.line, column: m.col })),
  }));
  selIndex = 0;
  renderResults(q.trim());
}

function renderResults(highlight = "") {
  const box = $("paletteResults");
  if (!items.length) {
    box.replaceChildren(el("div", { className: "empty", textContent: "No matches." }));
    return;
  }
  const frag = document.createDocumentFragment();
  let lastGroup = null;
  items.forEach((it, idx) => {
    if (it.group !== lastGroup) {
      lastGroup = it.group;
      frag.append(el("div", { className: "pal-group", textContent: lastGroup }));
    }
    const main = el("span", { className: "pal-main" });
    if (highlight && mode === "search") {
      const lower = it.title.toLowerCase(), h = highlight.toLowerCase(), at = lower.indexOf(h);
      if (at >= 0) {
        main.append(
          document.createTextNode(it.title.slice(0, at)),
          el("mark", { textContent: it.title.slice(at, at + h.length) }),
          document.createTextNode(it.title.slice(at + h.length)),
        );
      } else {
        main.textContent = it.title;
      }
    } else {
      main.textContent = it.title;
    }
    const row = el("div", {
      className: `pal-row${idx === selIndex ? " sel" : ""}`,
      onclick: () => { closePalette(); it.run(); },
    }, icon(it.ic || "file", 14), main, el("span", { className: "pal-sub", textContent: it.sub || "" }));
    frag.append(row);
  });
  box.replaceChildren(frag);
  box.querySelector(".pal-row.sel")?.scrollIntoView({ block: "nearest" });
}

function setMode(next) {
  mode = next === "search" ? "search" : "jump";
  for (const b of document.querySelectorAll("#palette .palette-modes button")) {
    b.classList.toggle("on", b.dataset.pmode === mode);
  }
  $("paletteInput").placeholder = mode === "search"
    ? "Search text across all project files…"
    : "Jump to file, section, citation, or type > for commands…";
  onInput();
}

function onInput() {
  const val = $("paletteInput").value;
  clearTimeout(searchTimer);
  if (mode === "search") {
    searchTimer = setTimeout(() => runProjectSearch(val), 150);
  } else {
    items = buildJumpItems(val);
    selIndex = 0;
    renderResults();
  }
}

export async function openPalette(initialMode = "jump") {
  $("paletteBackdrop").hidden = false;
  $("paletteInput").value = "";
  const searchBtn = document.querySelector("#palette [data-pmode=search]");
  if (searchBtn) searchBtn.hidden = !editorAllowed();
  setMode(initialMode === "search" && editorAllowed() ? "search" : "jump");
  $("paletteInput").focus();
  if (editorAllowed() && !srcFiles) {
    await refreshFilesList();
    if (!$("paletteBackdrop").hidden && mode === "jump") onInput();
  }
}

export function closePalette() {
  $("paletteBackdrop").hidden = true;
}

export const paletteOpen = () => !$("paletteBackdrop").hidden;

$("cmdTrigger").onclick = () => openPalette("jump");
$("paletteBackdrop").addEventListener("mousedown", (e) => { if (e.target === $("paletteBackdrop")) closePalette(); });
$("paletteInput").addEventListener("input", onInput);
for (const b of document.querySelectorAll("#palette .palette-modes button")) {
  b.onclick = () => { setMode(b.dataset.pmode); $("paletteInput").focus(); };
}
$("paletteInput").addEventListener("keydown", (e) => {
  if (e.key === "Escape") { e.preventDefault(); closePalette(); }
  else if (e.key === "ArrowDown") { e.preventDefault(); if (items.length) { selIndex = (selIndex + 1) % items.length; renderResults($("paletteInput").value.trim()); } }
  else if (e.key === "ArrowUp") { e.preventDefault(); if (items.length) { selIndex = (selIndex - 1 + items.length) % items.length; renderResults($("paletteInput").value.trim()); } }
  else if (e.key === "Enter") { e.preventDefault(); const pick = items[selIndex]; if (pick) { closePalette(); pick.run(); } }
});
