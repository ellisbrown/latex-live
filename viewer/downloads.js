// The toolbar's download menu.

import { $, building, docFile, el, file, isDiff, setStatus, SIGNED_OUT, sleep } from "./page.js";
import { postJSON, readBody, signInPage, unreachable } from "./net.js";
import { behind, buildFailed, buildVersion } from "./build.js";
import { showMore } from "./toolbar.js";

// Downloads: the PDF as built (notes included); without notes, from an exact build that the server
// makes on request (live.py start_export; once per build of the document); or that build with the
// notes in its margins (live.py start_review), for co-authors who don't run latex-live. In shared mode
// the last two are for whoever's role may (me.can.download): it is the shared document, as every
// coauthor reads it. They are asked for with a POST, whose Origin the server checks there (it builds).
function saveFile(url, name) {
  const a = el("a", { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
}
const docName = () => docFile.replace(/\.pdf$/, "");
// The toolbar's download button opens its menu on hover (and on click, for keyboards and touch).
export function showDownloads(open) {
  const menu = $("downloadMenu"), button = $("downloadBtn");
  if (open) {
    const r = button.getBoundingClientRect();
    menu.style.left = `${Math.max(4, Math.min(r.left, window.innerWidth - 270))}px`;
    menu.style.top = `${r.bottom + 2}px`;
  }
  menu.hidden = !open;
  button.setAttribute("aria-expanded", String(open));
  button.classList.toggle("on", open);
}
let downloadsTimer = 0;
for (const node of [$("downloadBtn"), $("downloadMenu")]) {
  node.addEventListener("mouseenter", () => { clearTimeout(downloadsTimer); showDownloads(true); });
  node.addEventListener("mouseleave", () => { downloadsTimer = setTimeout(() => showDownloads(false), 300); }); // (time to reach the menu)
}
$("downloadBtn").onclick = () => showDownloads(true);
document.addEventListener("mousedown", (e) => {
  if (!$("downloadMenu").hidden && !$("downloadMenu").contains(e.target) && !$("downloadBtn").contains(e.target)) showDownloads(false);
});
$("downloadPdf").onclick = () => {
  showMore(false);
  showDownloads(false);
  const shown = isDiff ? file : docFile; // (a comparison tab saves the comparison)
  // ("-with-notes" when the paper has notes, so a version without them too: the Notes switch shows then)
  saveFile(`pdf/${encodeURIComponent(shown)}?v=${buildVersion}`, isDiff ? file : `${docName()}${$("notesView").hidden ? "" : "-with-notes"}.pdf`);
};

// The server builds them from the PDF as last built (and waits for a build in progress first): ask until it is
// done, showing its progress (poll leaves the status line to it meanwhile: liveStatus), then save. Right after
// this tab writes a note, wait for the build that the write starts, which the server can't know of yet.
async function downloadBuilt(endpoint, built, saveAs, what, done) {
  showMore(false);
  showDownloads(false);
  if (building.has(endpoint)) return;
  building.add(endpoint);
  try {
    while (behind()) {
      setStatus(`waiting for the build in progress, then the PDF ${what}…`, "busy");
      await new Promise((r) => setTimeout(r, 250));
    }
    for (;;) {
      const r = await postJSON(endpoint, {}).catch(() => null);
      const e = r ? await readBody(r) : {};
      if (!r || unreachable(r, e)) return setStatus("download failed: can't reach the preview server", "error");
      if (signInPage(r, e)) return setStatus(SIGNED_OUT, "error"); // (a proxy's sign-in page, not the server's answer)
      if (!r.ok && !e.state) return setStatus(`download failed: ${e.error}`, "error"); // (refused: e.g. the access ended)
      if (e.state === "done") {
        saveFile(`pdf/${encodeURIComponent(built)}?v=${e.version}`, saveAs);
        return setStatus(`saved ${saveAs} (${done(e)}${buildFailed ? "; of the last build that worked" : ""})`, "fresh");
      }
      if (e.state === "failed") return setStatus(`couldn't build the PDF ${what}: ${e.msg}`, "error");
      setStatus(`building the PDF ${what}${e.step ? `: ${e.step}` : ""}…`, "busy");
      await sleep(1000);
    }
  } catch (err) {
    setStatus(`download failed: ${err.message || err}`, "error");
  } finally {
    building.delete(endpoint);
  }
}
$("downloadClean").onclick = () => downloadBuilt("export", `${docName()}-export.pdf`, `${docName()}.pdf`, "without notes",
                                                 () => "without notes");
$("downloadReview").onclick = () => downloadBuilt("review", `${docName()}-review.pdf`, `${docName()}-with-margin-notes.pdf`,
  "with notes in the margin", (e) => [`${e.notes} note${e.notes === 1 ? "" : "s"} in the margin`,
    e.approximate && `${e.approximate} placed by source line`, e.continued && `${e.continued} continued after the paper`].filter(Boolean).join(", "));
