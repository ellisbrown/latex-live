// Sidebar Papers / References tab: searchable & sortable .bib library with live citation counts,
// one-click \cite{} insertion, Add by arXiv ID / DOI / BibTeX, and Crossref reference verification.

import { $, editorShown, el, setStatus } from "./page.js";
import { postJSON, readBody } from "./net.js";
import { editorAllowed, me } from "./role.js";
import { insertAtCursor, openFile, showEditor } from "./editor.js";
import { setPapersCache } from "./cm.js";
import { icon } from "./icons.js";

let indexData = null; // {entries, bibFiles, totalCitations}
let filterText = "";
let sortBy = "cited"; // "cited" | "title" | "year" | "uncited"
let showAddForm = false;
let lookupPreview = null;
let lookupQuery = "";
let verifyData = null;
let verifyTimer = null;

export function getPapers() { return indexData?.entries || []; }

export async function loadPapers() {
  if (!editorAllowed()) return;
  const r = await fetch("papers/index", { cache: "no-store" }).catch(() => null);
  if (!r?.ok) return;
  indexData = await r.json();
  setPapersCache(indexData.entries || []);
  const badge = document.querySelector("#tabs [data-tab=papers] .n");
  if (badge) badge.textContent = indexData.entries?.length || "";
  if (!$("papers").hidden) renderPapers();
}

async function runVerify() {
  const r = await postJSON("bib/verify", {}).catch(() => null);
  if (!r?.ok) return setStatus("Could not start reference verification", "error");
  verifyData = await r.json();
  renderPapers();
  clearTimeout(verifyTimer);
  const pollVerify = async () => {
    const vr = await fetch("bib/verify", { cache: "no-store" }).catch(() => null);
    if (!vr?.ok) return;
    verifyData = await vr.json();
    renderPapers();
    if (verifyData.state === "running") verifyTimer = setTimeout(pollVerify, 1200);
  };
  verifyTimer = setTimeout(pollVerify, 800);
}

function renderAddBox(container) {
  const form = el("div", { className: "inline-form" });
  const input = el("input", {
    type: "text",
    placeholder: "arXiv ID (2312.00752), DOI (10.xxxx/...), or @article{…",
    value: lookupQuery,
    oninput: (e) => { lookupQuery = e.target.value; },
  });
  const err = el("div", { className: "form-err" });
  const lookupBtn = el("button", { className: "panel-btn primary", textContent: "Look up" });
  const cancelBtn = el("button", {
    className: "panel-btn",
    textContent: "Close",
    onclick: () => { showAddForm = false; lookupPreview = null; renderPapers(); },
  });
  const doLookup = async () => {
    const q = input.value.trim();
    if (!q) return;
    lookupQuery = q;
    lookupBtn.disabled = true;
    err.textContent = "";
    const r = await postJSON("bib/lookup", { query: q }).catch(() => null);
    const res = r ? await readBody(r) : {};
    lookupBtn.disabled = false;
    if (!r?.ok) { err.textContent = res.error || "Lookup failed"; return; }
    lookupPreview = res;
    renderPapers();
  };
  lookupBtn.onclick = doLookup;
  input.onkeydown = (e) => { if (e.key === "Enter") doLookup(); };
  form.append(input, err, el("div", { className: "form-row" }, cancelBtn, lookupBtn));

  if (lookupPreview) {
    const ta = el("textarea", { value: lookupPreview.bibtex || "" });
    const bibSel = el("select");
    const bibs = indexData?.bibFiles || [];
    for (const b of bibs) bibSel.append(el("option", { value: b, textContent: b }));
    const addBtn = el("button", {
      className: "panel-btn primary",
      textContent: bibs.length ? "Add to .bib" : "No .bib file",
      disabled: !bibs.length,
      onclick: async () => {
        addBtn.disabled = true;
        err.textContent = "";
        const r = await postJSON("bib/add", { bibtex: ta.value, file: bibSel.value || undefined }).catch(() => null);
        const res = r ? await readBody(r) : {};
        addBtn.disabled = false;
        if (!r?.ok) { err.textContent = res.error || "Could not add entry"; return; }
        setStatus(`Added @${res.key} to ${res.file}`, "fresh");
        showAddForm = false;
        lookupPreview = null;
        lookupQuery = "";
        await loadPapers();
      },
    });
    form.append(ta, el("div", { className: "form-row" }, ...(bibs.length > 1 ? [bibSel] : []), addBtn));
  }
  container.append(form);
}

function renderVerifyBox(container) {
  if (!verifyData || verifyData.state === "idle") return;
  const box = el("div", { className: "verify-box" });
  if (verifyData.state === "running") {
    box.textContent = "Checking bibliography against Crossref…";
    container.append(box);
    return;
  }
  if (verifyData.state === "failed") {
    box.textContent = `Check failed: ${verifyData.error || "unknown error"}`;
    container.append(box);
    return;
  }
  const findings = verifyData.findings || [];
  const head = el("div", { style: "display:flex;align-items:center;gap:6px;margin-bottom:4px;font-weight:600" },
    el("span", { textContent: findings.length ? `${findings.length} finding${findings.length === 1 ? "" : "s"} (${verifyData.checked} verified online)` : `All ${verifyData.total} references look good` }),
    el("button", {
      className: "panel-btn",
      style: "margin-left:auto;height:20px;padding:0 6px;font-size:11px",
      textContent: "×",
      onclick: () => { verifyData = null; renderPapers(); },
    }),
  );
  box.append(head);
  if (verifyData.offline) {
    box.append(el("div", { className: "paper-meta", textContent: "Crossref unreachable (offline) — showing local checks only." }));
  }
  for (const f of findings.slice(0, 20)) {
    const badgeCol = f.severity === "fail" ? "var(--err)" : f.severity === "warn" ? "var(--warn)" : "var(--ink-3)";
    const row = el("div", {
      className: "verify-row",
      onclick: async () => {
        if (f.file) {
          if (!editorShown()) await showEditor(true, { path: f.file, line: f.line || 1 });
          else await openFile(f.file, f.line || 1);
        }
      },
    },
      el("span", { style: `color:${badgeCol};font-weight:700;font-size:11px;flex:none`, textContent: f.severity === "fail" ? "✗" : f.severity === "warn" ? "⚠" : "i" }),
      el("span", { textContent: `${f.key}: ${f.msg}` }),
    );
    box.append(row);
  }
  container.append(box);
}

export function renderPapers() {
  const panel = $("papers");
  if (!panel) return;
  const scroll = panel.scrollTop;
  const frag = document.createDocumentFragment();

  const bar = el("div", { className: "panel-bar" });
  const search = el("input", {
    type: "search",
    placeholder: "Search or add papers…",
    value: filterText,
    oninput: (e) => { filterText = e.target.value; renderPapers(); },
  });
  const sortSel = el("select", {
    style: "flex:0 0 auto;width:78px",
    title: "Sort papers",
    onchange: (e) => { sortBy = e.target.value; renderPapers(); },
  },
    el("option", { value: "cited", textContent: "Cited", selected: sortBy === "cited" }),
    el("option", { value: "year", textContent: "Year", selected: sortBy === "year" }),
    el("option", { value: "title", textContent: "Title", selected: sortBy === "title" }),
    el("option", { value: "uncited", textContent: "Uncited", selected: sortBy === "uncited" }),
  );
  bar.append(search, sortSel);
  if (me.can.bib) {
    bar.append(
      el("button", { title: "Add paper by arXiv ID, DOI, or BibTeX", onclick: () => { showAddForm = !showAddForm; renderPapers(); } }, icon("plus", 13)),
      el("button", { title: "Check references against Crossref", onclick: runVerify }, icon("checks", 13)),
    );
  }
  frag.append(bar);

  if (showAddForm) renderAddBox(frag);
  renderVerifyBox(frag);

  let list = [...(indexData?.entries || [])];
  const q = filterText.trim().toLowerCase();
  if (q) {
    list = list.filter((e) => `${e.key} ${e.title} ${e.author} ${e.venue} ${e.year}`.toLowerCase().includes(q));
  }
  if (sortBy === "uncited") list = list.filter((e) => e.citeCount === 0);
  list.sort((a, b) => {
    if (sortBy === "cited") return (b.citeCount - a.citeCount) || a.key.localeCompare(b.key);
    if (sortBy === "year") return (parseInt(b.year, 10) || 0) - (parseInt(a.year, 10) || 0) || a.title.localeCompare(b.title);
    return (a.title || a.key).localeCompare(b.title || b.key);
  });

  if (!list.length) {
    frag.append(el("div", { className: "empty", textContent: indexData ? "No matching bibliography entries." : "Loading references…" }));
  } else {
    for (const e of list.slice(0, 200)) {
      const firstAuthor = (e.author || "").split(" and ")[0]?.trim() || "";
      const etAl = e.author?.includes(" and ") ? `${firstAuthor} et al.` : firstAuthor;
      const meta = [etAl, e.venue, e.year].filter(Boolean).join(" · ");
      const badge = el("span", {
        className: `cite-badge${e.citeCount ? " used" : ""}`,
        textContent: `${e.citeCount}×`,
        title: e.citeCount ? `Cited ${e.citeCount} time(s) — click to jump to first citation` : "Not cited in the paper",
        onclick: async () => {
          const loc = e.citedIn?.[0];
          if (!loc) return;
          if (!editorShown()) await showEditor(true, { path: loc.file, line: loc.line });
          else await openFile(loc.file, loc.line);
        },
      });
      const card = el("div", { className: "paper-card" },
        el("div", { className: "paper-top" }, el("div", { className: "paper-title", textContent: e.title || e.key }), badge),
        ...(meta ? [el("div", { className: "paper-meta", textContent: meta })] : []),
        el("div", { className: "paper-acts" },
          el("code", { textContent: e.key }),
          el("button", {
            textContent: "Cite",
            title: `Insert \\cite{${e.key}} at cursor`,
            onclick: async () => {
              const snippet = `\\cite{${e.key}}`;
              if (editorShown() && insertAtCursor(snippet)) return;
              navigator.clipboard?.writeText(snippet).catch(() => {});
              setStatus(`Copied ${snippet}`);
            },
          }),
          el("button", {
            textContent: ".bib",
            title: `Open ${e.file}:${e.line}`,
            onclick: async () => {
              if (!editorShown()) await showEditor(true, { path: e.file, line: e.line });
              else await openFile(e.file, e.line);
            },
          }),
        ),
      );
      frag.append(card);
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
