// The sidebar and its tabs: a tab's panel is filled when it shows, and again after each build.

import { $, isDiff } from "./page.js";
import { doc } from "./pdfview.js";
import { buildVersion, shown } from "./build.js";
import { setSide, setTab, sideOpen, tab } from "./layout.js";
import { renderThumbs } from "./pages.js";
import { renderFloats } from "./floats.js";
import { checksVersion, runChecks } from "./checks.js";
import { loadFilesTab } from "./files.js";
import { loadHistory } from "./history.js";
import { loadPapers } from "./papers.js";

for (const name of ["files", "notes", "checks", "figures", "history", "papers"]) {
  const btn = document.querySelector(`#tabs [data-tab=${name}]`);
  if (btn && isDiff) btn.hidden = true;
}
$("toggleSide").onclick = () => showSide($("side").hidden);
for (const b of document.querySelectorAll("#tabs button")) b.onclick = () => showTab(b.dataset.tab);

export function showSide(on) {
  setSide(on);
  if (on) showTab(tab);
}
export function showTab(name) {
  setTab(name);
  for (const b of document.querySelectorAll("#tabs button")) b.classList.toggle("on", b.dataset.tab === name);
  for (const p of document.querySelectorAll(".panel")) p.hidden = p.id !== name;
  refreshTab();
}

export function refreshTab() {
  if (sideOpen("files")) loadFilesTab().catch(() => {});
  if (sideOpen("history")) loadHistory().catch(() => {});
  if (sideOpen("papers")) loadPapers().catch(() => {});
  if (!doc) return;
  if (sideOpen("thumbs")) renderThumbs();
  if (sideOpen("figures")) renderFloats().catch((e) => console.warn(e));
  if (sideOpen("checks") && checksVersion !== `${buildVersion}/${shown}`) runChecks();
}
