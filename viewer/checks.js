// The sidebar's pre-submission check.

import { $, el, file } from "./page.js";
import { canOpenFile, opensAt } from "./role.js";
import { doc, shownVariant } from "./pdfview.js";
import { buildVersion, shown } from "./build.js";
import { goToPage, reveal } from "./navigation.js";
import { openSource, showSource } from "./links.js";
import { cleanResult, displayedResult, pageResult } from "./pages.js";
import { cutSpots } from "./cut.js";

// Pre-submission check (live.py), plus the page-limit result from this viewer. Re-runs after each
// rebuild while the tab is open.
export let checksVersion = null, checksRunning = false;
const ICONS = { ok: "✓", info: "ℹ", warn: "⚠", fail: "✗" };
export async function runChecks() {
  if (checksRunning) return;
  checksRunning = true;
  checksVersion = `${buildVersion}/${shown}`;
  const panel = $("checks");
  if (!panel.querySelector(".check")) panel.replaceChildren(el("div", { className: "empty", textContent: "Checking…" }));
  try {
    const results = await (await fetch(`check/${encodeURIComponent(file)}`)).json();
    const r = cleanResult || pageResult;
    if (r?.limit) results.unshift({ title: "Page limit", level: r.cls === "over" ? "fail" : r.cls === "tight" ? "warn" : "ok",
                             detail: `main text ${r.value.toFixed(1)} of ${r.limit} pages${cleanResult ? " without the notes" : ""}: ${r.range}`,
                             items: r.cls === "over" ? [{ text: `the main text should end on page ${r.lastAllowedLabel}`, page: r.lastAllowed }] : [] });
    const shownR = displayedResult();
    if (shownR) {
      const spots = await cutSpots(doc, shownR);
      const lines = spots.reduce((n, s) => n + s.lines, 0);
      results.splice(r?.limit ? 1 : 0, 0, { title: "Space to reclaim", level: spots.length ? "info" : "ok",
        detail: spots.length ? `≈${lines} line(s) in ${spots.length} spot(s) of the main text (${shownVariant === "clean" ? "notes-free build" : "with notes"}); ✂ in the toolbar marks them`
                             : "no short last lines or large gaps in the main text",
        items: spots.map((s) => ({ text: `${s.label}: ${s.text}`, page: s.page, rect: s.rect })) });
    }
    renderChecks(results);
  } catch (e) {
    panel.replaceChildren(el("div", { className: "empty", textContent: `Check failed: ${e.message || e}` }));
  } finally {
    checksRunning = false;
  }
}
function renderChecks(results) {
  const panel = $("checks");
  const open = new Set([...panel.querySelectorAll("details[open]")].map((d) => d.dataset.title));
  const rerun = el("button", { textContent: "Run again" });
  rerun.onclick = runChecks;
  const bad = results.filter((r) => r.level === "fail" || r.level === "warn").length;
  document.querySelector("#tabs [data-tab=checks] .n").textContent = bad || "";
  const rows = results.map((r) => {
    const details = el("details", { className: "check" });
    details.dataset.title = r.title;
    details.open = open.has(r.title) || (r.level === "fail" && r.items.length > 0 && !open.size);
    details.append(el("summary", {}, el("span", { className: `icon ${r.level}`, textContent: ICONS[r.level] }),
                      el("span", { textContent: r.title }), el("span", { className: "detail", textContent: r.detail })));
    if (r.items.length) {
      details.append(el("ul", {}, ...r.items.map((it) => {
        const li = el("li", { textContent: it.text });
        if (it.page) {
          const page = el("span", { className: "where", textContent: `  PDF page ${it.page}`, title: "Go to the page" });
          page.onclick = (e) => { e.stopPropagation(); it.rect ? reveal(it.page, it.rect) : goToPage(it.page); };
          li.append(page);
          li.onclick = page.onclick;
        }
        if (it.file) {
          const where = opensAt(el("span", { className: "where", textContent: `  ${it.file.replace(/^.*\//, "")}:${it.line}` }), `${it.file}:${it.line}`);
          where.onclick = (e) => { e.stopPropagation(); openSource(it.file, it.line); };
          if (canOpenFile(it.file)) li.append(where);
          li.onclick = () => showSource(it.file, it.line, true);
        }
        if (it.open && canOpenFile(it.open)) { // a related file, e.g. the script that makes a figure
          const script = opensAt(el("span", { className: "where", textContent: `  ↗ ${it.open}` }), it.open);
          script.onclick = (e) => { e.stopPropagation(); openSource(it.open, 1); };
          li.append(script);
        }
        return li;
      })));
    }
    return details;
  });
  panel.replaceChildren(el("div", { className: "toolbar" }, rerun, el("span", { className: "when", textContent: `checked ${new Date().toLocaleTimeString()}` })), ...rows);
}
