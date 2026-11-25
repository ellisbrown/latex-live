// The Refs tab (editors and the owner): the paper's bibliography (live.py /papers/index), each .bib entry with how
// often the paper cites it (3×, 0×; a click goes to the first citation), a button that types \cite{key} at the source
// pane's cursor and one that opens the entry; adding entries by arXiv ID, DOI or pasted BibTeX (/bib/lookup, /bib/add);
// and checking the bibliography (/bib/verify). An ID's lookup and the check against Crossref go online, which a
// preview does only with LATEX_LIVE_LOOKUPS (index.lookups); pasted BibTeX works either way.
//
// The panel's controls (the search, the add box) are made once: a build or an added entry redraws only the list and the
// check's findings, so whatever is being typed in them keeps its text and the focus.

import { editorShown, el, isPopout, panelError, setStatus } from "./page.js";
import { getJSON, sendJSON } from "./net.js";
import { editorAllowed } from "./role.js";
import { insertText, showEditor } from "./editor.js";
import { addTab, showSide, showTab } from "./sidebar.js";
import { wrote } from "./build.js";
import { registerCommands } from "./palette.js";
import { icon } from "./icons.js";

let index = null, loadError = ""; // (/papers/index: {entries, bibFiles, lookups}; why it last failed to load)
let verify = null, verifyTimer = 0, verifyMisses = 0; // (/bib/verify's last answer; the poll while it runs, and its misses)

const panel = addTab("refs", { label: "Refs", icon: "refs", title: "The bibliography: citations, adding and checking entries",
                               refresh: () => loadRefs() });
const search = el("input", { type: "search", placeholder: "Filter by key, title, author, venue, year" });
const sort = el("select", { title: "Order" }, ...[["cited", "Most cited"], ["year", "Newest"], ["title", "Title"],
                                                    ["uncited", "Not cited"]].map(([value, textContent]) => el("option", { value, textContent })));
const addBtn = el("button", { title: "Add an entry by arXiv ID, DOI or BibTeX" }, icon("plus", 13));
const checkBtn = el("button", { title: "" }, icon("checks", 13));
const query = el("textarea", { className: "refs-query", rows: 1, spellcheck: false, ariaLabel: "An arXiv ID, a DOI or BibTeX" }); // (BibTeX keeps its lines)
const lookupBtn = el("button", { className: "primary", textContent: "Look up" });
const offNote = el("div", { className: "refs-note" });
const addError = el("div", { className: "refs-error" });
const preview = el("textarea", { className: "refs-preview", spellcheck: false, hidden: true, ariaLabel: "The entry to add, as BibTeX" });
const target = el("select", { hidden: true, title: "The .bib file to add it to" });
const saveBtn = el("button", { className: "primary", textContent: "Add to the .bib", hidden: true });
const addBox = el("div", { className: "refs-add", hidden: true },
                  query, offNote, addError, preview, el("div", { className: "refs-row" }, target, lookupBtn, saveBtn));
const verifyBox = el("div", { className: "refs-verify", hidden: true });
const list = el("div", { className: "refs-list" });
panel.classList.add("refs");
panel.append(el("div", { className: "refs-bar" }, search, sort, addBtn, checkBtn), addBox, verifyBox, list);

// The list, and what depends on whether this preview goes online; again after each build and each added entry. When it
// doesn't load, the list says why (over the one loaded before, if any).
async function loadRefs() {
  if (!editorAllowed()) return;
  const got = await getJSON("papers/index");
  loadError = got.ok && Array.isArray(got.body.entries) ? "" : `The bibliography did not load: ${got.error || "no entries in the answer"}`;
  if (loadError) return renderList();
  index = got.body;
  const n = document.querySelector("#tabs [data-tab=refs] .n");
  if (n) n.textContent = index.entries.length || "";
  showLookups();
  renderList();
}

function showLookups() {
  const online = !!index?.lookups;
  query.placeholder = online ? "arXiv ID (2312.00752), DOI (10.1000/xyz) or @article{…" : "Paste a BibTeX entry: @article{…";
  lookupBtn.textContent = online ? "Look up" : "Read";
  offNote.textContent = online ? "" : "Online lookups are off in this preview, so arXiv IDs and DOIs can't be looked up and"
                                      + " the check against Crossref is off: paste the BibTeX instead.";
  offNote.hidden = online;
  checkBtn.disabled = !online;
  checkBtn.title = online ? "Check the bibliography: keys and titles in it twice, entries not cited, and the cited ones against"
                            + " Crossref (which gets their titles and DOIs)" : "Checking against Crossref is off in this preview";
}

function renderList() {
  const words = search.value.trim().toLowerCase();
  let shown = (index?.entries || []).filter((e) => !words || `${e.key} ${e.title} ${e.author} ${e.venue} ${e.year}`.toLowerCase().includes(words));
  if (sort.value === "uncited") shown = shown.filter((e) => !e.citeCount);
  const byKey = (a, b) => a.key.localeCompare(b.key);
  shown.sort(sort.value === "cited" ? (a, b) => b.citeCount - a.citeCount || byKey(a, b)
             : sort.value === "year" ? (a, b) => (parseInt(b.year, 10) || 0) - (parseInt(a.year, 10) || 0) || byKey(a, b)
             : (a, b) => (a.title || a.key).localeCompare(b.title || b.key));
  const cards = shown.slice(0, 300).map(card);
  if (loadError) {
    cards.unshift(panelError(loadError, () => loadRefs()));
  } else if (!cards.length) {
    cards.push(el("div", { className: "empty", textContent: !index ? "Loading the bibliography…"
                                                           : index.bibFiles.length ? "No entry matches." : "This paper has no .bib file." }));
  } else if (shown.length > 300) {
    cards.push(el("div", { className: "empty", textContent: `The first 300 of ${shown.length}: filter to see the others.` }));
  }
  list.replaceChildren(...cards);
}

function card(e) {
  const authors = (e.author || "").split(" and ");
  const meta = [authors.length > 1 ? `${authors[0]} et al.` : authors[0], e.venue, e.year].filter(Boolean).join(" · ");
  const count = el("button", { className: `refs-count${e.citeCount ? " used" : ""}`, textContent: `${e.citeCount}×`,
                               title: e.citeCount ? `Cited ${e.citeCount} time${e.citeCount === 1 ? "" : "s"}: go to the first` : "Not cited",
                               disabled: !e.citeCount,
                               onclick: () => e.citedIn[0] && showEditor(true, { path: e.citedIn[0].file, line: e.citedIn[0].line }) });
  return el("div", { className: "refs-card" },
            el("div", { className: "refs-top" }, el("div", { className: "refs-title", textContent: e.title || e.key }), count),
            ...(meta ? [el("div", { className: "refs-meta", textContent: meta })] : []),
            el("div", { className: "refs-acts" }, el("code", { textContent: e.key }),
               el("button", { textContent: "Cite", title: `Type \\cite{${e.key}} at the cursor`, onclick: () => cite(e.key) }),
               el("button", { textContent: ".bib", title: `Open ${e.file}:${e.line}`,
                              onclick: () => showEditor(true, { path: e.file, line: e.line }) })));
}

// \cite{key} at the source pane's cursor; without a file open there, onto the clipboard.
function cite(key) {
  const text = `\\cite{${key}}`;
  if (editorShown() && insertText(text)) return;
  (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject())
    .then(() => setStatus(`Copied ${text}: open the source to paste it`), () => setStatus(`Open the source to cite ${key}`));
}

search.oninput = renderList;
sort.onchange = renderList;
search.onkeydown = (e) => { // (Enter cites the first entry listed)
  const first = list.querySelector(".refs-card code")?.textContent;
  if (e.key === "Enter" && first) cite(first);
};

// Adding: Look up (or Read, offline) shows the entry as BibTeX, to change before it is added to the chosen .bib file.
addBtn.onclick = () => showAdd(addBox.hidden);
function showAdd(on) {
  addBox.hidden = !on;
  addBtn.classList.toggle("on", on);
  if (on) query.focus();
}
function resetAdd() {
  query.value = preview.value = addError.textContent = "";
  preview.hidden = saveBtn.hidden = target.hidden = true;
  lookupBtn.hidden = false;
}
query.onkeydown = (e) => { // (Enter looks up; Shift+Enter starts a new line)
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    lookUp();
  }
};
query.oninput = () => { if (!preview.hidden && !query.value.trim()) resetAdd(); };
lookupBtn.onclick = lookUp;
async function lookUp() {
  const text = query.value.trim();
  if (!text) return;
  lookupBtn.disabled = true;
  addError.textContent = "";
  const got = await sendJSON("bib/lookup", { query: text }), found = got.body;
  lookupBtn.disabled = false;
  if (!got.ok) {
    if (found.lookupsOff && index) { index.lookups = false; showLookups(); }
    addError.textContent = got.error || "The lookup failed.";
    return;
  }
  preview.value = found.bibtex || "";
  const files = index?.bibFiles || [];
  target.replaceChildren(...files.map((f) => el("option", { value: f, textContent: f })));
  target.hidden = files.length < 2;
  preview.hidden = saveBtn.hidden = false;
  saveBtn.disabled = !files.length;
  saveBtn.title = files.length ? "" : "This paper has no .bib file to add it to";
}
saveBtn.onclick = async () => {
  saveBtn.disabled = true;
  addError.textContent = "";
  const got = await sendJSON("bib/add", { bibtex: preview.value, file: target.value || null }), done = got.body;
  saveBtn.disabled = false;
  if (!got.ok || !Array.isArray(done.keys)) {
    addError.textContent = got.error || "The entry was not added.";
    return;
  }
  wrote(); // (a build comes: a download waits for it)
  setStatus(`Added ${done.keys.join(", ")} to ${done.file}`, "fresh");
  resetAdd();
  showAdd(false);
  loadRefs();
};

// The check: it runs on the server (a minute at most), and its findings stay until closed.
checkBtn.onclick = async () => {
  const got = await sendJSON("bib/verify", {});
  if (!got.ok) {
    verify = { state: "failed", error: `it did not start: ${got.error}` };
    return renderVerify();
  }
  verify = got.body;
  verifyMisses = 0;
  renderVerify();
  pollVerify();
};
// Its state every 1.2 s while it runs; after 5 polls in a row without an answer, the box says so and the poll stops.
function pollVerify() {
  clearTimeout(verifyTimer);
  verifyTimer = setTimeout(async () => {
    const got = await getJSON("bib/verify"), state = got.ok ? got.body : null;
    if (!verify) return; // (closed meanwhile)
    verifyMisses = state ? 0 : verifyMisses + 1;
    if (state) verify = state;
    else if (verifyMisses >= 5) verify = { state: "failed", error: "the preview stopped answering" };
    renderVerify();
    if (verify.state === "running") pollVerify();
  }, 1200);
}
function renderVerify() {
  verifyBox.hidden = !verify || verify.state === "idle";
  if (verifyBox.hidden) return;
  const close = el("button", { title: "Close (a check on its way goes on)", onclick: () => { verify = null; clearTimeout(verifyTimer); renderVerify(); } },
                  icon("close", 12));
  if (verify.state !== "done") {
    verifyBox.replaceChildren(el("div", { className: "refs-row" }, el("span", {
      textContent: verify.state === "running" ? "Checking the bibliography against Crossref…" : `The check failed: ${verify.error || "no reason given"}` }),
      close));
    return;
  }
  const findings = verify.findings || [];
  const head = findings.length ? `${findings.length} finding${findings.length === 1 ? "" : "s"}` : `No findings in ${verify.total} entries`;
  verifyBox.replaceChildren(
    el("div", { className: "refs-row" }, el("strong", { textContent: `${head} (${verify.checked} checked against Crossref)` }), close),
    ...(verify.offline ? [el("div", { className: "refs-note", textContent: "Crossref didn't answer: only the checks here." })] : []),
    ...findings.slice(0, 50).map((f) => el("button", { className: `refs-finding ${f.severity}`, title: `Open ${f.file}:${f.line}`,
                                                      onclick: () => showEditor(true, { path: f.file, line: f.line }) },
                                           el("span", { className: "sev", textContent: { fail: "✗", warn: "!", info: "i" }[f.severity] || "i" }),
                                           el("span", { textContent: `${f.key}: ${f.msg}` }))));
}

// The palette's commands: the Refs tab, ready to filter (and cite with Enter) or to add an entry.
function openRefs(add) {
  showSide(true);
  showTab("refs");
  if (add) showAdd(true);
  else search.focus();
}
registerCommands(() => (editorAllowed() && !isPopout ? [
  { title: "Insert a citation", sub: "Refs", icon: "refs", run: () => openRefs(false) },
  { title: "Add a reference by arXiv ID, DOI or BibTeX", sub: "Refs", icon: "refs", run: () => openRefs(true) }] : []));
