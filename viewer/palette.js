// The palette (Cmd/Ctrl+K, or Go to… in the top bar): go to a source file, a heading of the PDF's outline, or a
// command; and, for editors, search the text of every source file (Cmd/Ctrl+Shift+F). Other parts of the viewer add
// their commands (registerCommands).

import { $, editorShown, el } from "./page.js";
import { editorAllowed, me } from "./role.js";
import { rebuild } from "./build.js";
import { cmView, ed, setSrcFiles, showEditor, srcFiles } from "./editor.js";
import { toggleFoldAll } from "./cm.js";
import { outlineHeadings } from "./outline.js";
import { showKeys } from "./toolbar.js";
import { pdfInvert, setPdfInvert, setTheme, setTint, theme, TINTS } from "./theme.js";
import { icon } from "./icons.js";

const IS_MAC = /^Mac|^iP/.test(navigator.platform);
$("paletteBtn").querySelector("kbd").textContent = IS_MAC ? "⌘K" : "Ctrl K";

// The commands: functions that return what they offer now, each [{title, run, sub?, icon?, group? ("Commands")}], in
// the order registered. A part of the viewer adds its own with registerCommands.
const commandSources = [];
export const registerCommands = (list) => commandSources.push(list);
registerCommands(() => [
  { title: theme === "dark" ? "Light theme" : "Dark theme", sub: "Appearance", icon: theme === "dark" ? "sun" : "moon",
    run: () => setTheme(theme === "dark" ? "light" : "dark") },
  { title: pdfInvert ? "Show the PDF's own colors" : "Invert the PDF's colors", sub: "Appearance", icon: "sun", run: () => setPdfInvert(!pdfInvert) },
  ...TINTS.map((t) => ({ title: `${t.label} surfaces`, sub: "Appearance", icon: "sun", run: () => setTint(t.id) })),
  ...(editorAllowed() && !$("layoutSwitch").hidden ? [
    { title: "Source alone", sub: "Layout", icon: "edit", run: () => showEditor(true, { mode: "source" }) },
    { title: "Source beside the PDF (Split)", sub: "Layout", icon: "edit", run: () => showEditor(true, { mode: "split" }) },
    { title: "PDF alone", sub: "Layout", icon: "pages", run: () => showEditor(false) }] : []),
  ...(!$("popoutBtn").hidden ? [{ title: "Pop out the PDF", sub: "Layout", icon: "popout", run: () => $("popoutBtn").click() }] : []),
  ...(editorShown() && ed && cmView ? [{ title: "Fold or unfold all headings and environments", sub: "Source", icon: "outline",
                                        run: () => toggleFoldAll(cmView) }] : []),
  ...(me.can.rebuild && !$("rebuild").hidden ? [{ title: "Rebuild the PDF from scratch", sub: "Build", icon: "rebuild", run: rebuild }] : []),
  { title: "Keyboard shortcuts", sub: "Help", icon: "more", run: () => showKeys(true) },
]);

// Go to: the source files (editors), the outline's headings and the commands whose names hold what was typed; after
// a >, only the commands.
function goItems(typed) {
  const commandsOnly = typed.startsWith(">"), words = (commandsOnly ? typed.slice(1) : typed).trim().toLowerCase();
  const matches = (...texts) => !words || texts.join(" ").toLowerCase().includes(words);
  const items = [];
  if (!commandsOnly) {
    if (editorAllowed()) {
      for (const f of srcFiles?.files || []) {
        if (matches(f.path)) items.push({ group: "Files", icon: "file", title: f.path.slice(f.path.lastIndexOf("/") + 1),
                                          sub: f.path.slice(0, f.path.lastIndexOf("/") + 1),
                                          run: () => showEditor(true, { path: f.path }) });
      }
    }
    for (const h of outlineHeadings()) {
      if (matches(h.title)) items.push({ group: "Headings", icon: "outline", title: h.title, sub: "in the PDF", run: h.go, depth: h.depth });
    }
  }
  for (const list of commandSources) {
    for (const c of list()) if (matches(c.title, c.sub || "")) items.push({ group: "Commands", ...c });
  }
  return items.slice(0, 80);
}

// Search: the lines of the source files that hold the text (live.py /search; literally, or as a regular expression
// outside shared mode), each opening its file at the match.
let searchSeq = 0, searchTimer = 0;
async function searchItems(typed) {
  const seq = ++searchSeq;
  if (!typed.trim()) return show([], "Type to search the text of every source file.");
  const query = new URLSearchParams({ q: typed, case: pressed("searchCase") ? "1" : "0", regex: pressed("searchRegex") ? "1" : "0" });
  const r = await fetch(`search?${query}`, { cache: "no-store" }).catch(() => null);
  const found = r ? await r.json().catch(() => ({})) : {};
  if (seq !== searchSeq || mode !== "search") return;
  if (!r?.ok) return show([], found.error || "The search did not answer.");
  show(found.matches.map((m) => ({ group: m.file, icon: "file", title: m.text, sub: `${m.line}`, mark: [m.col, m.len],
                                   run: () => showEditor(true, { path: m.file, line: m.line, column: m.col }) })),
       found.matches.length ? (found.truncated ? `The first ${found.matches.length} matches.` : "") : "No matches.");
}
const pressed = (id) => $(id).getAttribute("aria-pressed") === "true";

// The list: items under their group's heading, the chosen one marked; a click or Enter runs it.
let mode = "go", items = [], chosen = 0, before = null;
function show(list, note = "") {
  items = list;
  chosen = 0;
  const rows = [];
  let group = null;
  list.forEach((it, i) => {
    if (it.group !== group) rows.push(el("div", { className: "pal-group", textContent: (group = it.group) }));
    const main = el("span", { className: "pal-main" });
    if (it.mark) {
      const [from, length] = it.mark;
      main.append(it.title.slice(0, from), el("mark", { textContent: it.title.slice(from, from + length) }), it.title.slice(from + length));
    } else {
      main.textContent = it.title;
    }
    if (it.depth) main.style.paddingLeft = `${Math.min(it.depth, 3) * 12}px`;
    const row = el("div", { className: "pal-row", onclick: () => run(it), onmousemove: () => choose(i) },
                   icon(it.icon || "file", 14), main, el("span", { className: "pal-sub", textContent: it.sub || "" }));
    row.setAttribute("role", "option");
    rows.push(row);
  });
  if (note || !list.length) rows.push(el("div", { className: "empty", textContent: note || "Nothing matches." }));
  $("paletteResults").replaceChildren(...rows);
  choose(0);
}
function choose(i) {
  chosen = i;
  $("paletteResults").querySelectorAll(".pal-row").forEach((row, j) => {
    row.classList.toggle("sel", j === i);
    row.setAttribute("aria-selected", String(j === i));
    if (j === i) row.scrollIntoView({ block: "nearest" });
  });
}
function run(it) {
  closePalette(false);
  it.run();
}
function refresh() {
  clearTimeout(searchTimer);
  if (mode === "go") show(goItems($("paletteInput").value));
  else searchTimer = setTimeout(() => searchItems($("paletteInput").value), 150);
}
function setMode(next) {
  mode = next === "search" && editorAllowed() ? "search" : "go";
  for (const b of $("palette").querySelectorAll(".palette-modes button")) b.classList.toggle("on", b.dataset.mode === mode);
  $("searchOptions").hidden = mode !== "search";
  $("searchRegex").hidden = me.shared; // (shared mode searches for the text as typed)
  $("paletteInput").placeholder = mode === "search" ? "Search the text of every source file" : "Go to a file, a heading or a command (> for commands)";
  refresh();
}

export const paletteOpen = () => !$("paletteBackdrop").hidden;
export async function openPalette(want = "go") {
  if (!paletteOpen()) before = document.activeElement;
  $("paletteBackdrop").hidden = false;
  $("palette").querySelector(".palette-modes").hidden = !editorAllowed();
  setMode(want);
  $("paletteInput").select();
  $("paletteInput").focus();
  if (editorAllowed() && !srcFiles) { // (the source pane did not list them yet)
    const r = await fetch("files", { cache: "no-store" }).catch(() => null);
    const list = r?.ok ? await r.json().catch(() => null) : null;
    if (Array.isArray(list?.files)) setSrcFiles(list);
    if (paletteOpen() && mode === "go") refresh();
  }
}
// Close it; refocus (cancelled, not run) what had the focus before.
export function closePalette(refocus = true) {
  $("paletteBackdrop").hidden = true;
  if (refocus && before?.isConnected) before.focus();
  before = null;
}

$("paletteBtn").onclick = () => openPalette("go");
$("paletteBackdrop").addEventListener("mousedown", (e) => { if (e.target === $("paletteBackdrop")) closePalette(); });
$("paletteInput").oninput = refresh;
for (const b of $("palette").querySelectorAll(".palette-modes button")) b.onclick = () => { setMode(b.dataset.mode); $("paletteInput").focus(); };
for (const id of ["searchCase", "searchRegex"]) {
  $(id).onclick = () => {
    $(id).setAttribute("aria-pressed", String(!pressed(id)));
    $(id).classList.toggle("on", pressed(id));
    refresh();
    $("paletteInput").focus();
  };
}
$("paletteInput").addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    if (items.length) choose((chosen + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length);
  } else if (e.key === "Enter" || e.key === "Escape") {
    e.preventDefault();
    e.stopPropagation(); // (the viewer's Esc would close its other popups too)
    if (e.key === "Escape") closePalette();
    else if (items[chosen]) run(items[chosen]);
  }
});
