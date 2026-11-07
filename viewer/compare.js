// Compare (latexdiff against a commit).

import { $, docFile, el, isDiff, setStatus } from "./page.js";
import { postJSON, readBody } from "./net.js";
import { me } from "./role.js";
import { flushSource } from "./editor.js";
import { placeItem, showMore } from "./toolbar.js";

// Compare: latexdiff against a commit (live.py builds <doc>-diff.pdf), shown in its own tab.
$("compare").onclick = async () => {
  const panel = $("comparePanel");
  if (!panel.hidden) return (panel.hidden = true);
  const r = ($("moreMenu").contains($("compare")) ? $("more") : $("compare")).getBoundingClientRect();
  showMore(false);
  panel.style.left = `${Math.min(r.left, window.innerWidth - 450)}px`;
  panel.replaceChildren(el("div", { className: "head", textContent: "Loading commits…" }));
  panel.hidden = false;
  const { dirty, commits } = await (await fetch("commits")).json();
  const row = (rev, date, text, title) => {
    const div = el("div", { className: "commit", title }, el("span", { className: "rev", textContent: rev }),
                   el("span", { className: "date", textContent: date }), el("span", { textContent: text }));
    div.onclick = () => startDiff(rev);
    return div;
  };
  panel.replaceChildren(el("div", { className: "head", textContent: "Show the changes since… (the paper as it is now, with what changed since that commit marked; in a new tab)" }),
    ...(dirty ? [row("HEAD", "", "uncommitted edits only", "the last commit vs. the working tree")] : []),
    ...commits.map((c) => row(c.rev, c.date, `${c.subject} — ${c.author}`, `${c.rev} ${c.date} ${c.author}: ${c.subject}`)));
};
async function startDiff(rev) {
  $("comparePanel").hidden = true;
  await flushSource(); // (the diff is of the files on disk)
  const r = me.legacy ? await fetch(`diff?${new URLSearchParams({ rev })}`) : await postJSON("diff", { rev });
  if (!r.ok && r.status !== 409) return setStatus((await readBody(r)).error, "error");
  const url = `viewer.html?${new URLSearchParams({ file: docFile.replace(/\.pdf$/, "-diff.pdf") })}`;
  if (isDiff) return; // this tab already shows the diff; it reloads when the new one is built
  if (!window.open(url, "latex-live-diff")) location.href = url;
}
if (isDiff) { // (the diff tab's main control, so in the toolbar)
  placeItem($("compare"), true);
  $("compare").textContent = "Compare with…";
  $("compare").title = "Compare with another commit";
}
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
