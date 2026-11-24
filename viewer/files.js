// The Files tab (editors and the owner): the paper's sources and figures (live.py /files), in folders that fold, with a
// filter. A click opens a source in the source pane, or shows a figure's preview with a button that types its
// \includegraphics at the cursor (the source pane's card for an \includegraphics shows the preview too). New makes a
// source file, Upload adds a figure (sources are written in the editor, never uploaded), and each row renames or removes
// its file (live.py POST /file/create, /file/upload, /file/rename, /file/delete; in shared mode, committed as yours).
//
// The bar and the form (a new file's name, an upload's, a rename's) are made once, outside the tree: the tree is drawn
// again only when what it shows changes (the list, the open file, a folder folded), so that whatever is being typed keeps
// its text and the focus. A dot in a file's row for each other person who has it open (presence.js).

import { editorShown, el, isPopout, setStatus } from "./page.js";
import { postJSON, readBody } from "./net.js";
import { editorAllowed } from "./role.js";
import { assets as pdfAssets, pdfjsLib } from "./pdfview.js";
import { closeTab, ed, flushSource, insertText, onFileOpen, openFile, setSrcFiles, showEditor, srcFiles } from "./editor.js";
import { setFigurePreview } from "./cm.js";
import { onPresence } from "./presence.js";
import { addTab, showSide, showTab } from "./sidebar.js";
import { registerCommands } from "./palette.js";
import { icon } from "./icons.js";

const FIGURES = ".png,.jpg,.jpeg,.pdf,.svg,.eps,.webp,.gif"; // (live.py's ASSET_EXTS: what Upload takes)
const panel = addTab("files", { label: "Files", icon: "folder", title: "The paper's sources and figures: open, add, rename, remove",
                                refresh: () => loadFiles() });
const filter = el("input", { type: "search", placeholder: "Filter files" });
const newBtn = el("button", { title: "New source file (.tex, .bib, .sty, .cls)" }, icon("plus", 13));
const uploadBtn = el("button", { title: `Upload a figure (${FIGURES.replaceAll(",", " ")})` }, icon("upload", 13));
const picker = el("input", { type: "file", accept: FIGURES, hidden: true });
const formLabel = el("div", { className: "files-note" });
const nameInput = el("input", { type: "text", spellcheck: false, ariaLabel: "The file's name, with its folder" });
const formError = el("div", { className: "files-error" });
const okBtn = el("button", { className: "primary" });
const cancelBtn = el("button", { textContent: "Cancel" });
const form = el("div", { className: "files-form", hidden: true },
                formLabel, nameInput, formError, el("div", { className: "files-row" }, cancelBtn, okBtn));
const tree = el("div", { className: "files-tree" });
panel.classList.add("files");
panel.append(el("div", { className: "files-bar" }, filter, newBtn, uploadBtn, picker), form, tree);

let folded = new Set(), preview = null, drawn = "", peers = {}; // (folders folded; the figure previewed; what the tree shows)
let action = null; // the form's: {kind: "new" | "upload" | "rename", path?, file?}

// The list, again when the tab shows, after each build and each change made here, and every 10 s while it shows (a
// coauthor's upload builds nothing).
async function loadFiles() {
  if (!editorAllowed()) return;
  const r = await fetch("files", { cache: "no-store" }).catch(() => null);
  const list = r?.ok ? await r.json().catch(() => null) : null;
  if (!Array.isArray(list?.files)) return;
  setSrcFiles(list);
  drawTree();
}
setInterval(() => { if (!panel.hidden && !document.hidden) loadFiles(); }, 10000);
onFileOpen(() => drawTree());

// Who else has each file open (presence.js, on each change): a dot for each person in the file's row.
onPresence((byFile) => {
  peers = Object.fromEntries(Object.entries(byFile).map(([path, list]) =>
    [path, list.filter((p, i) => list.findIndex((q) => q.user === p.user) === i)]));
  drawTree();
});

const folderOf = (path) => (path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "");
const size = (n) => (n < 1024 ? `${n} B` : n < 1 << 20 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / (1 << 20)).toFixed(1)} MB`);

function drawTree() {
  if (panel.hidden && drawn) return; // (drawn when it shows again: refresh)
  const q = filter.value.trim().toLowerCase();
  const files = srcFiles?.files || [], figures = srcFiles?.assets || [];
  const peersOf = (path) => (peers[path] || []).map((p) => [p.name, p.color]);
  const key = JSON.stringify([q, files, figures, srcFiles?.main, ed?.path, [...folded], preview,
                              [...files, ...figures].map((f) => peersOf(f.path))]);
  if (key === drawn) return;
  drawn = key;
  const scroll = tree.scrollTop;
  const rows = [];
  for (const [label, list, figure] of [["Sources", files, false], ["Figures", figures, true]]) {
    const shown = list.filter((f) => !q || f.path.toLowerCase().includes(q));
    if (!shown.length) continue;
    rows.push(el("div", { className: "files-head" }, el("span", { textContent: label }), el("span", { textContent: String(shown.length) })));
    const folders = [...new Set(shown.map((f) => folderOf(f.path)))].sort();
    for (const folder of folders) {
      const id = `${figure ? "f" : "s"}:${folder}`, closed = folder && folded.has(id) && !q;
      if (folder) {
        rows.push(el("button", { className: "files-folder", title: closed ? "Show its files" : "Hide its files",
                                 onclick: () => { folded.has(id) ? folded.delete(id) : folded.add(id); drawTree(); } },
                     icon(closed ? "chevronRight" : "chevronDown", 12), icon("folder", 14), el("span", { textContent: folder })));
      }
      if (closed) continue;
      for (const f of shown.filter((f) => folderOf(f.path) === folder)) rows.push(...fileRow(f, figure, folder, peersOf(f.path)));
    }
  }
  if (!rows.length) {
    rows.push(el("div", { className: "files-note empty", textContent: !srcFiles ? "Loading…" : q ? "No file matches." : "No files." }));
  }
  tree.replaceChildren(...rows);
  tree.scrollTop = scroll;
}

function fileRow(f, figure, folder, here) {
  const name = folder ? f.path.slice(folder.length + 1) : f.path, main = !figure && f.path === srcFiles?.main;
  const act = (iconName, title, run) => el("button", { title, ariaLabel: title, onclick: (e) => { e.stopPropagation(); run(); } }, icon(iconName, 12));
  const acts = el("span", { className: "files-acts" },
                  ...(figure ? [act("plus", "Type its \\includegraphics at the cursor", () => includeFigure(f.path))] : []),
                  ...(main ? [] : [act("edit", "Rename or move", () => openForm({ kind: "rename", path: f.path })),
                                   act("trash", "Remove", () => removeFile(f.path))]));
  const row = el("div", { className: `files-file${!figure && ed?.path === f.path ? " on" : ""}${folder ? " nested" : ""}`,
                          title: `${f.path} (${size(f.size)})${main ? ", the main file" : ""}`,
                          onclick: () => (figure ? showPreview(f.path) : openSource(f.path)) },
                 icon(figure ? "figures" : "file", 14), el("span", { className: "name", textContent: name }),
                 ...here.slice(0, 3).map(([who, color]) => {
                   const dot = el("span", { className: "files-peer", title: `${who} has it open` });
                   dot.style.background = color;
                   return dot;
                 }),
                 el("span", { className: "size", textContent: size(f.size) }), acts);
  if (figure && preview === f.path) {
    return [row, el("div", { className: "files-preview" }, figureThumb(f.path),
                    el("button", { textContent: "Type its \\includegraphics at the cursor", onclick: () => includeFigure(f.path) }))];
  }
  return [row];
}
filter.oninput = () => drawTree();

async function openSource(path) {
  if (!editorShown()) await showEditor(true, { path });
  else await openFile(path);
}
function showPreview(path) {
  preview = preview === path ? null : path;
  drawTree();
}
async function includeFigure(path) {
  if (!editorShown()) await showEditor(true, { focus: false });
  if (!insertText(`\\includegraphics[width=\\linewidth]{${path}}`)) setStatus("Open a source file first, to type the figure in it", "error");
}

// A figure's preview, for the tree and the source pane's card: the image, or a PDF's first page (PDF.js, in this
// browser: nothing is drawn on the server), kept for 30 s. An EPS file has none.
const thumbs = new Map(); // path -> {at, url (a promise)}
export function figureThumb(path) {
  const box = el("div", { className: "fig-thumb" });
  if (/\.eps$/i.test(path)) {
    box.append(el("span", { textContent: "No preview of an EPS file here" }));
    return box;
  }
  let kept = thumbs.get(path);
  if (!kept || performance.now() - kept.at > 30000) {
    thumbs.get(path)?.url.then(forget);
    thumbs.delete(path);
    kept = { at: performance.now(), url: thumbURL(path) };
    thumbs.set(path, kept);
    if (thumbs.size > 24) { // (the oldest goes)
      const [old, t] = thumbs.entries().next().value;
      thumbs.delete(old);
      t.url.then(forget);
    }
  }
  kept.url.then((url) => box.replaceChildren(url ? el("img", { src: url, alt: path }) : el("span", { textContent: "No preview" })));
  return box;
}
const forget = (url) => url?.startsWith("blob:") && URL.revokeObjectURL(url);
async function thumbURL(path) {
  try {
    const r = await fetch(`asset?${new URLSearchParams({ path })}`, { cache: "no-store" });
    if (!r.ok) return null;
    if (!/\.pdf$/i.test(path)) return URL.createObjectURL(await r.blob());
    const pdf = await pdfjsLib.getDocument({ data: new Uint8Array(await r.arrayBuffer()), ...pdfAssets }).promise;
    try {
      const page = await pdf.getPage(1), unit = page.getViewport({ scale: 1 });
      const viewport = page.getViewport({ scale: Math.min(2, 480 / Math.max(unit.width, unit.height)) });
      const canvas = el("canvas", { width: Math.ceil(viewport.width), height: Math.ceil(viewport.height) });
      await page.render({ canvasContext: canvas.getContext("2d"), viewport }).promise;
      return canvas.toDataURL("image/png");
    } finally {
      pdf.destroy();
    }
  } catch (e) {
    console.warn(e);
    return null;
  }
}
setFigurePreview(figureThumb);

// The form above the tree: a new source file's name, an uploaded figure's (from the file picked), or a file's new name.
newBtn.onclick = () => openForm({ kind: "new" });
uploadBtn.onclick = () => picker.click();
picker.onchange = () => {
  const file = picker.files?.[0];
  picker.value = "";
  if (!file) return;
  if (srcFiles?.uploadSize && file.size > srcFiles.uploadSize) {
    return setStatus(`${file.name} is larger than ${Math.round(srcFiles.uploadSize / 1e6)} MB, the most a figure uploads`, "error");
  }
  const clean = file.name.replace(/[^A-Za-z0-9_+.-]+/g, "-").replace(/^[.-]+/, "") || "figure";
  const folder = (srcFiles?.assets || []).some((a) => a.path.startsWith("figures/")) ? "figures/" : "";
  openForm({ kind: "upload", file }, folder + clean);
};
function openForm(next, value = "") {
  action = next;
  const { kind, path } = next;
  formLabel.textContent = kind === "new" ? "New source file, in a folder if you like (sections/intro.tex)"
                        : kind === "upload" ? `Upload ${next.file.name} (${size(next.file.size)}) as`
                        : `Rename or move ${path}`;
  nameInput.value = kind === "rename" ? path : value;
  nameInput.placeholder = kind === "new" ? "sections/intro.tex" : "figures/plot.pdf";
  okBtn.textContent = { new: "Create", upload: "Upload", rename: "Rename" }[kind];
  formError.textContent = "";
  okBtn.disabled = false;
  form.hidden = false;
  showSide(true);
  showTab("files");
  nameInput.focus();
  const dot = nameInput.value.lastIndexOf("."), slash = nameInput.value.lastIndexOf("/") + 1;
  nameInput.setSelectionRange(kind === "new" ? nameInput.value.length : slash, dot > slash ? dot : nameInput.value.length);
}
function closeForm() {
  action = null;
  form.hidden = true;
}
cancelBtn.onclick = closeForm;
nameInput.onkeydown = (e) => {
  if (e.key === "Enter") okBtn.click();
  else if (e.key === "Escape") closeForm();
};
okBtn.onclick = async () => {
  const name = nameInput.value.trim(), now = action;
  if (!name || !now || okBtn.disabled) return;
  okBtn.disabled = true;
  formError.textContent = "";
  try {
    const error = await { new: createFile, upload: uploadFigure, rename: renameFile }[now.kind](name, now);
    if (action !== now) return;
    if (error) formError.textContent = error;
    else closeForm();
  } finally {
    okBtn.disabled = false;
  }
};

// Each action, once live.py has done it: the list again, the source pane following (the file made opens; a renamed
// file's tab goes, and it opens under its new name if it was open; a removed one's goes, with its edits). Each returns
// what went wrong, for the form, or nothing.
async function send(path, body) {
  const r = await postJSON(path, body).catch(() => null);
  const done = r ? await readBody(r) : {};
  return r?.ok ? [done, null] : [done, done.error || (r ? `refused (${r.status})` : "can't reach the preview server")];
}
async function createFile(name) {
  const [done, error] = await send("file/create", { path: name, text: "" });
  if (error) return error;
  setStatus(`Made ${done.path}`, "fresh");
  await loadFiles();
  await openSource(done.path);
}
async function uploadFigure(name, { file }) {
  const data = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).replace(/^data:[^,]*,/, ""));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  }).catch(() => null);
  if (data === null) return `${file.name} could not be read`;
  setStatus(`Uploading ${name}…`, "busy");
  const [done, error] = await send("file/upload", { path: name, data });
  if (error) {
    setStatus(`${name} was not uploaded`, "error");
    return error;
  }
  setStatus(`Uploaded ${done.path}`, "fresh");
  preview = done.path;
  await loadFiles();
}
async function renameFile(name, { path }) {
  const open = ed?.path === path;
  if (open && !(await flushSource())) return `${path} is not saved yet, so it was not renamed`;
  const [done, error] = await send("file/rename", { from: path, to: name });
  if (error) return error;
  setStatus(`Renamed ${path} to ${done.path}`, "fresh");
  if (preview === path) preview = done.path;
  await loadFiles();
  if (open) await openFile(done.path);
  await closeTab(path, { discard: true });
}
async function removeFile(path) {
  const unsaved = ed?.path === path && ed.dirty ? " Its edits not saved yet go too." : "";
  if (!confirm(`Remove ${path} from the paper?${unsaved}`)) return;
  const [, error] = await send("file/delete", { path });
  if (error) return setStatus(`${path}: ${error}`, "error");
  setStatus(`Removed ${path}`);
  if (preview === path) preview = null;
  await loadFiles();
  await closeTab(path, { discard: true });
}

// The palette's commands: a new file, an upload.
registerCommands(() => (editorAllowed() && !isPopout ? [
  { title: "New source file", sub: "Files", icon: "plus", run: () => openForm({ kind: "new" }) },
  { title: "Upload a figure", sub: "Files", icon: "upload", run: () => picker.click() }] : []));
