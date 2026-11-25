// Compare (latexdiff against a commit).

import { $, docFile, el, isDiff, setStatus } from "./page.js";
import { getJSON, sendJSON } from "./net.js";
import { me } from "./role.js";
import { flushSource } from "./editor.js";
import { placeItem, showMore } from "./toolbar.js";
import { registerCommands } from "./palette.js";

// Compare: latexdiff against a commit (live.py builds <doc>-diff.pdf), shown in its own tab.
$("compare").onclick = async () => {
  const panel = $("comparePanel");
  if (!panel.hidden) return (panel.hidden = true);
  const r = ($("moreMenu").contains($("compare")) ? $("more") : $("compare")).getBoundingClientRect();
  showMore(false);
  panel.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - Math.min(460, window.innerWidth - 24) - 12))}px`;
  panel.replaceChildren(el("div", { className: "head", textContent: "Loading commits…" }));
  panel.hidden = false;
  const got = await getJSON("commits");
  if (panel.hidden) return; // (closed meanwhile)
  if (!got.ok || !Array.isArray(got.body.commits)) {
    return panel.replaceChildren(el("div", { className: "head error", textContent: `The commits did not load: ${got.error || "no list in the answer"}` }));
  }
  const { dirty, commits } = got.body;
  const row = (rev, date, text, title, sha = rev) => {
    const div = el("div", { className: "commit", title }, el("span", { className: "rev", textContent: rev }),
                   el("span", { className: "date", textContent: date }), el("span", { textContent: text }));
    div.onclick = () => startDiff(sha);
    return div;
  };
  panel.replaceChildren(el("div", { className: "head", textContent: "Show the changes since… (the paper as it is now, with what changed since that commit marked; in a new tab)" }),
    ...(dirty ? [row("HEAD", "", "uncommitted edits only", "the last commit vs. the working tree")] : []),
    ...commits.map((c) => row(c.rev, c.date, `${c.subject} — ${c.author}`, `${c.rev} ${c.date} ${c.author}: ${c.subject}`, c.sha ?? c.rev)));
};
// Build the latexdiff against rev and show it in its own tab (also from the History tab).
export async function startDiff(rev) {
  $("comparePanel").hidden = true;
  await flushSource(); // (the diff is of the files on disk)
  const got = me.legacy ? await getJSON(`diff?${new URLSearchParams({ rev })}`) : await sendJSON("diff", { rev });
  if (!got.ok && got.status !== 409) return setStatus(`The comparison did not start: ${got.error}`, "error"); // (409: one is on its way)
  const url = `viewer.html?${new URLSearchParams({ file: docFile.replace(/\.pdf$/, "-diff.pdf") })}`;
  if (isDiff) return; // this tab already shows the diff; it reloads when the new one is built
  if (!window.open(url, "latex-live-diff")) location.href = url;
}
if (isDiff) { // (the diff tab's main control, so in the toolbar)
  placeItem($("compare"), true);
  $("compare").textContent = "Compare with…";
  $("compare").title = "Compare with another commit";
}
// The panel closes on a click elsewhere (and on Esc: keys.js).
document.addEventListener("mousedown", (e) => {
  const panel = $("comparePanel");
  if (!panel.hidden && !panel.contains(e.target) && !$("compare").contains(e.target)) panel.hidden = true;
});
registerCommands(() => ($("compare").hidden || isDiff ? [] : [
  { title: "Compare with an earlier version…", sub: "Changes", icon: "diff", run: () => $("compare").click() }]));

export function showDiffInfo(d) {
  if (!isDiff || !d?.sha) return;
  const info = $("diffInfo");
  const again = el("button", { textContent: "Rebuild", title: "Diff the same commit against the current files" });
  again.onclick = () => startDiff(d.rev);
  const paper = el("a", { textContent: "↩ paper", href: `viewer.html?${new URLSearchParams({ file: docFile })}` });
  info.replaceChildren(`changes since ${d.rev}${d.plainer ? " (plainer marks)" : ""} `, again, " ", paper);
  info.title = `latexdiff of commit ${d.sha} against the working tree`
    + (d.plainer ? "; with full marks the document didn't build, so changes in math, figures and citations aren't marked" : "");
  info.hidden = false;
}
