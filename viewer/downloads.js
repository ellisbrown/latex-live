// The toolbar's download menu.

import { $, building, docFile, el, file, isDiff, setStatus, SIGNED_OUT, sleep } from "./page.js";
import { postJSON, readBody, signInPage, unreachable } from "./net.js";
import { behind, buildFailed, buildVersion } from "./build.js";
import { showMore } from "./toolbar.js";

// Downloads: the PDF as built (notes included); without notes, from an exact build that the server
// makes on request (live.py start_export; once per build of the document); or that build with the
// notes in its margins (live.py start_review), for co-authors who don't run latex-live, or with the notes
// as PDF comments, which the same request builds beside it (for a PDF reader's comments). In shared mode
// the last two are for whoever's role may (me.can.download): it is the shared document, as every
// coauthor reads it. And the arXiv package (live.py start_arxiv: the sources the export read, without
// their comments and notes, checked by building it alone), the owner's (me.can.arxiv). They are asked
// for with a POST, whose Origin the server checks there (it builds).
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
async function downloadBuilt(endpoint, built, saveAs, what, done) { // (built: a PDF in build/, or the URL of another download)
  showMore(false);
  showDownloads(false);
  if (building.has(endpoint)) return;
  building.add(endpoint);
  try {
    while (behind()) {
      setStatus(`waiting for the build in progress, then ${what}…`, "busy");
      await new Promise((r) => setTimeout(r, 250));
    }
    for (;;) {
      const r = await postJSON(endpoint, {}).catch(() => null);
      const e = r ? await readBody(r) : {};
      if (!r || unreachable(r, e)) return setStatus("download failed: can't reach the preview server", "error");
      if (signInPage(r, e)) return setStatus(SIGNED_OUT, "error"); // (a proxy's sign-in page, not the server's answer)
      if (!r.ok && !e.state) return setStatus(`download failed: ${e.error}`, "error"); // (refused: e.g. the access ended)
      if (e.state === "done") {
        saveFile(`${built.endsWith(".pdf") ? `pdf/${encodeURIComponent(built)}` : built}?v=${e.version}`, saveAs);
        return setStatus(`saved ${saveAs} (${done(e)}${buildFailed ? "; of the last build that worked" : ""})`,
                         e.same === false ? "error" : "fresh"); // (an arXiv package that builds differently: said in red)
      }
      if (e.state === "failed") return setStatus(`couldn't build ${what}: ${e.msg}`, "error");
      setStatus(`building ${what}${e.step ? `: ${e.step}` : ""}…`, "busy");
      await sleep(1000);
    }
  } catch (err) {
    setStatus(`download failed: ${err.message || err}`, "error");
  } finally {
    building.delete(endpoint);
  }
}
$("downloadClean").onclick = () => downloadBuilt("export", `${docName()}-export.pdf`, `${docName()}.pdf`, "the PDF without notes",
                                                 () => "without notes");
const reviewed = (where) => (e) => [`${e.notes} note${e.notes === 1 ? "" : "s"} ${where}`,
  e.approximate && `${e.approximate} placed by source line`, where === "in the margin" && e.continued && `${e.continued} continued after the paper`]
  .filter(Boolean).join(", ");
$("downloadReview").onclick = () => downloadBuilt("review", `${docName()}-review.pdf`, `${docName()}-with-margin-notes.pdf`,
  "the PDF with notes in the margin", reviewed("in the margin"));
$("downloadAnnotated").onclick = () => downloadBuilt("review", `${docName()}-annotated.pdf`, `${docName()}-with-comments.pdf`,
  "the PDF with notes as comments", reviewed("as comments"));
const plural = (n, what) => `${n} ${what}${n === 1 ? "" : "s"}`;
const packaged = (e) => [`${plural(e.files, "file")}, ${(e.size / 1e6).toFixed(1)} MB`,
  (e.comments || e.hidden || e.notes) && `${[e.comments && plural(e.comments, "comment"), e.hidden && plural(e.hidden, "hidden block"),
    e.notes && plural(e.notes, "note")].filter(Boolean).join(", ")} taken out`,
  e.same ? "its own build matches the PDF without notes" : `its own build differs from the PDF without notes: ${e.differs}`]
  .filter(Boolean).join("; ");
$("downloadArxiv").onclick = () => downloadBuilt("arxiv", "arxiv.tar.gz", `${docName()}-arxiv.tar.gz`, "the arXiv package", packaged);
