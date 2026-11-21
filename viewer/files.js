// Sidebar Files tab: collapsible project tree of sources and figure assets, create/upload/rename/delete
// actions (role-gated), figure preview modal, insert \includegraphics at cursor, and collaborator dots.

import { $, editorShown, el, setStatus } from "./page.js";
import { postJSON, readBody } from "./net.js";
import { editorAllowed, me } from "./role.js";
import { dropTab, ed, flushSource, insertAtCursor, onFileOpen, openFile, refreshFilesList, renameTab, showEditor, srcFiles } from "./editor.js";
import { icon } from "./icons.js";
import { peerColor } from "./toolbar.js";

let filterText = "";
let collapsedDirs = new Set();
let activePeers = [];
let showCreateForm = false;
let assetModalPath = null;

export function setFilePeers(list) {
  activePeers = Array.isArray(list) ? list.filter((p) => !p.self && p.file) : [];
  if (!$("files").hidden) renderFilesTree();
}

export async function loadFilesTab() {
  if (!editorAllowed()) return;
  await refreshFilesList();
  renderFilesTree();
}

function formatSize(bytes) {
  if (!Number.isFinite(bytes)) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function openAssetPreview(path) {
  assetModalPath = path;
  $("assetTitle").textContent = path;
  const body = $("assetBody");
  const isImg = /\.(png|jpe?g|gif|webp|svg)$/i.test(path);
  if (isImg) {
    body.replaceChildren(el("img", { src: `asset?${new URLSearchParams({ path })}`, alt: path }));
  } else {
    body.replaceChildren(el("div", { className: "empty", textContent: `${path} (PDF/EPS figure — click below to insert \\includegraphics)` }));
  }
  $("assetInsert").hidden = !editorAllowed();
  $("assetModal").hidden = false;
}

$("assetModal").querySelector(".close").onclick = () => { $("assetModal").hidden = true; };
$("assetInsert").onclick = async () => {
  if (!assetModalPath) return;
  const path = assetModalPath;
  $("assetModal").hidden = true;
  if (!editorShown()) await showEditor(true);
  const snippet = `\\includegraphics[width=\\linewidth]{${path}}`;
  if (!insertAtCursor(snippet)) {
    navigator.clipboard?.writeText(snippet).catch(() => {});
    setStatus(`Copied ${snippet}`);
  }
};

function renderCreateForm(container) {
  const form = el("div", { className: "inline-form" });
  const input = el("input", { type: "text", placeholder: "e.g. sections/discussion.tex or refs.bib" });
  const err = el("div", { className: "form-err" });
  const createBtn = el("button", { className: "panel-btn primary", textContent: "Create" });
  const cancelBtn = el("button", { className: "panel-btn", textContent: "Cancel", onclick: () => { showCreateForm = false; renderFilesTree(); } });
  const submit = async () => {
    const path = input.value.trim();
    if (!path) return;
    createBtn.disabled = true;
    err.textContent = "";
    const r = await postJSON("file/create", { path, text: path.endsWith(".tex") ? "% New section\n" : "" }).catch(() => null);
    const res = r ? await readBody(r) : {};
    createBtn.disabled = false;
    if (!r?.ok) { err.textContent = res.error || "Could not create file"; return; }
    showCreateForm = false;
    await refreshFilesList();
    renderFilesTree();
    await showEditor(true, { path: res.path });
  };
  createBtn.onclick = submit;
  input.onkeydown = (e) => { if (e.key === "Enter") submit(); else if (e.key === "Escape") { showCreateForm = false; renderFilesTree(); } };
  form.append(input, err, el("div", { className: "form-row" }, cancelBtn, createBtn));
  container.append(form);
  requestAnimationFrame(() => input.focus());
}

async function handleUpload(fileObj) {
  if (!fileObj) return;
  const folder = prompt("Save uploaded file as (relative path):", /\.(png|jpe?g|pdf|svg|webp|eps)$/i.test(fileObj.name) ? `figures/${fileObj.name}` : fileObj.name);
  if (!folder || !folder.trim()) return;
  const buf = await fileObj.arrayBuffer();
  const bytes = new Uint8Array(buf);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  const b64 = btoa(bin);
  setStatus(`Uploading ${folder.trim()}…`, "busy");
  const r = await postJSON("file/upload", { path: folder.trim(), data: b64, overwrite: false }).catch(() => null);
  const res = r ? await readBody(r) : {};
  if (r?.status === 409 && confirm(`${folder.trim()} already exists. Overwrite it?`)) {
    const r2 = await postJSON("file/upload", { path: folder.trim(), data: b64, overwrite: true }).catch(() => null);
    const res2 = r2 ? await readBody(r2) : {};
    if (!r2?.ok) return setStatus(res2.error || "Upload failed", "error");
    setStatus(`Uploaded ${res2.path}`, "fresh");
  } else if (!r?.ok) {
    return setStatus(res.error || "Upload failed", "error");
  } else {
    setStatus(`Uploaded ${res.path}`, "fresh");
  }
  await refreshFilesList();
  renderFilesTree();
}

async function doRename(path) {
  const next = prompt(`Rename ${path} to:`, path);
  if (!next || next.trim() === path) return;
  if (ed?.path === path && !(await flushSource())) return setStatus(`Save ${path} before renaming`, "error");
  const r = await postJSON("file/rename", { from: path, to: next.trim() }).catch(() => null);
  const res = r ? await readBody(r) : {};
  if (!r?.ok) return setStatus(res.error || "Rename failed", "error");
  setStatus(`Renamed to ${res.path}`, "fresh");
  await refreshFilesList();
  await renameTab(path, res.path);
  renderFilesTree();
}

async function doDelete(path) {
  if (!confirm(`Delete ${path} from disk?`)) return;
  const r = await postJSON("file/delete", { path }).catch(() => null);
  const res = r ? await readBody(r) : {};
  if (!r?.ok) return setStatus(res.error || "Delete failed", "error");
  setStatus(`Deleted ${path}`);
  await dropTab(path);
  await refreshFilesList();
  renderFilesTree();
}

function buildSection(container, items, { isAsset = false } = {}) {
  const q = filterText.trim().toLowerCase();
  const list = items.filter((f) => !q || f.path.toLowerCase().includes(q));
  if (!list.length) return;
  const dirs = [...new Set(list.map((f) => f.path.includes("/") ? f.path.slice(0, f.path.lastIndexOf("/") + 1) : ""))].sort();
  for (const dir of dirs) {
    const dirKey = `${isAsset ? "a:" : "s:"}${dir}`;
    const closed = dir && collapsedDirs.has(dirKey) && !q;
    if (dir) {
      const drow = el("div", {
        className: "tree-dir",
        onclick: () => {
          collapsedDirs.has(dirKey) ? collapsedDirs.delete(dirKey) : collapsedDirs.add(dirKey);
          renderFilesTree();
        },
      }, icon(closed ? "chevronRight" : "chevronDown", 13), icon("folder", 14), el("span", { textContent: dir.replace(/\/$/, "") }));
      container.append(drow);
    }
    if (closed) continue;
    for (const f of list.filter((x) => (x.path.includes("/") ? x.path.slice(0, x.path.lastIndexOf("/") + 1) : "") === dir)) {
      const name = dir ? f.path.slice(dir.length) : f.path;
      const active = !isAsset && ed?.path === f.path;
      const peersHere = activePeers.filter((p) => p.file === f.path);
      const row = el("div", {
        className: `tree-row${active ? " current" : ""}`,
        title: `${f.path} (${formatSize(f.size)})`,
        onclick: async () => {
          if (isAsset) return openAssetPreview(f.path);
          if (!editorShown()) await showEditor(true, { path: f.path });
          else await openFile(f.path);
        },
      });
      if (dir) row.style.paddingLeft = "24px";
      row.append(icon(isAsset ? "image" : "file", 14), el("span", { className: "fname", textContent: name }));
      for (const p of peersHere.slice(0, 3)) {
        const dot = el("span", { className: "peer-dot", title: `${p.name} is editing this file` });
        dot.style.background = peerColor(p.id || p.name);
        row.append(dot);
      }
      row.append(el("span", { className: "fsize", textContent: formatSize(f.size) }));
      const acts = el("span", { className: "row-acts" });
      if (isAsset) {
        acts.append(el("button", {
          title: "Insert \\includegraphics at cursor",
          onclick: async (e) => {
            e.stopPropagation();
            if (!editorShown()) await showEditor(true);
            insertAtCursor(`\\includegraphics[width=\\linewidth]{${f.path}}`);
          },
        }, icon("plus", 12)));
      }
      if (me.can.manageFiles && f.path !== srcFiles?.main) {
        acts.append(
          el("button", { title: "Rename file", onclick: (e) => { e.stopPropagation(); doRename(f.path); } }, icon("rename", 12)),
          el("button", { title: "Delete file", onclick: (e) => { e.stopPropagation(); doDelete(f.path); } }, icon("trash", 12)),
        );
      }
      if (acts.childNodes.length) row.append(acts);
      container.append(row);
    }
  }
}

export function renderFilesTree() {
  const panel = $("files");
  if (!panel) return;
  const scroll = panel.scrollTop;
  const frag = document.createDocumentFragment();

  const bar = el("div", { className: "panel-bar" });
  const search = el("input", {
    type: "search",
    placeholder: "Filter files…",
    value: filterText,
    oninput: (e) => { filterText = e.target.value; renderFilesTree(); },
  });
  bar.append(search);
  if (me.can.createFile) {
    bar.append(el("button", {
      title: "New source file (.tex, .bib, .sty)",
      onclick: () => { showCreateForm = !showCreateForm; renderFilesTree(); },
    }, icon("plus", 13)));
    const fileIn = el("input", {
      type: "file",
      hidden: true,
      onchange: (e) => { handleUpload(e.target.files?.[0]); e.target.value = ""; },
    });
    bar.append(fileIn, el("button", {
      title: "Upload figure or source file",
      onclick: () => fileIn.click(),
    }, icon("upload", 13)));
  }
  frag.append(bar);

  if (showCreateForm) renderCreateForm(frag);

  const files = srcFiles?.files || [];
  const assets = srcFiles?.assets || [];
  if (!files.length && !assets.length) {
    frag.append(el("div", { className: "empty", textContent: srcFiles ? "No project files found." : "Loading project files…" }));
  } else {
    if (files.length) {
      frag.append(el("div", { className: "tree-group-head" }, el("span", { textContent: "Sources" }), el("span", { textContent: String(files.length) })));
      buildSection(frag, files, { isAsset: false });
    }
    if (assets.length) {
      frag.append(el("div", { className: "tree-group-head" }, el("span", { textContent: "Figures & Assets" }), el("span", { textContent: String(assets.length) })));
      buildSection(frag, assets, { isAsset: true });
    }
  }

  const curSearch = panel.querySelector("input[type=search]");
  const hadFocus = Boolean(curSearch && document.activeElement === curSearch);
  const selPos = hadFocus ? curSearch.selectionStart : null;
  panel.replaceChildren(frag);
  panel.scrollTop = scroll;
  if (hadFocus) {
    const inp = panel.querySelector("input[type=search]");
    if (inp) {
      inp.focus();
      const pos = selPos ?? filterText.length;
      inp.setSelectionRange(pos, pos);
    }
  }
}

onFileOpen(() => { if (!$("files").hidden) renderFilesTree(); });
