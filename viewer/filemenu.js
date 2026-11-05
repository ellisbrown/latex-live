// The source pane's file picker.

import { $, el } from "./page.js";
import { cmView, ed, openFile, setSrcFiles, srcFiles } from "./editor.js";

// The file picker: the editable sources (live.py /files), grouped by folder, with a filter.
async function showFileMenu(open) {
  $("fileMenu").hidden = !open;
  if (!open) return;
  $("fileFilter").value = "";
  renderFileList();
  $("fileFilter").focus();
  const r = await fetch("files", { cache: "no-store" }).catch(() => null);
  if (r?.ok) setSrcFiles(await r.json());
  if (!$("fileMenu").hidden) renderFileList(r?.ok ? null : "The list of files did not load.");
}
function renderFileList(problem = null) {
  const query = $("fileFilter").value.trim().toLowerCase();
  const files = (srcFiles?.files || []).filter((f) => f.path.toLowerCase().includes(query));
  const dirOf = (path) => path.slice(0, path.lastIndexOf("/") + 1);
  const rows = [];
  for (const dir of [...new Set(files.map((f) => dirOf(f.path)))].sort()) { // (the paper's root first, its main file on top)
    if (dir) rows.push(el("div", { className: "dir", textContent: dir }));
    for (const f of files.filter((g) => dirOf(g.path) === dir)) {
      const row = el("div", { className: `f${dir ? " sub" : ""}${f.path === ed?.path ? " current" : ""}`, textContent: f.path.slice(dir.length),
                              title: `${f.path} (${Math.max(1, Math.round(f.size / 1024))} KB)` });
      row.onclick = async () => { showFileMenu(false); await openFile(f.path); cmView?.focus(); };
      rows.push(row);
    }
  }
  rows.find((r) => r.classList.contains("f"))?.classList.add("sel");
  const empty = problem || (srcFiles ? "No file matches." : "Loading…");
  $("fileList").replaceChildren(...(rows.length ? rows : [el("div", { className: "empty", textContent: empty })]));
}
$("fileFilter").oninput = () => renderFileList();
$("fileFilter").addEventListener("keydown", (e) => {
  const rows = [...$("fileList").querySelectorAll(".f")], i = rows.findIndex((r) => r.classList.contains("sel"));
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    const next = rows[Math.min(rows.length - 1, Math.max(0, i + (e.key === "ArrowDown" ? 1 : -1)))];
    rows[i]?.classList.remove("sel");
    next?.classList.add("sel");
    next?.scrollIntoView({ block: "nearest" });
  } else if (e.key === "Enter") {
    e.preventDefault();
    rows[Math.max(0, i)]?.click();
  } else if (e.key === "Escape") {
    e.preventDefault();
    showFileMenu(false);
    cmView?.focus();
  }
});
$("filePick").onclick = () => showFileMenu($("fileMenu").hidden);
