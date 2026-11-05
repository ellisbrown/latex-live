// The toolbar's search box.

import { $ } from "./page.js";
import { eventBus, FindState } from "./pdfview.js";

// Search (PDF.js's find controller: searches every page, not just the rendered ones).
let lastQuery = null;
export function resetFind() {
  lastQuery = null;
  $("findCount").textContent = "";
}
export function find(findPrevious = false) {
  const query = $("find").value;
  if (!query) return clearFind();
  eventBus.dispatch("find", { source: window, type: query === lastQuery ? "again" : "", query, findPrevious,
                              caseSensitive: false, entireWord: false, highlightAll: true, matchDiacritics: false });
  lastQuery = query;
}
function clearFind() {
  $("find").value = "";
  $("findCount").textContent = "";
  lastQuery = null;
  eventBus.dispatch("findbarclose", { source: window });
}
function showFindCount({ current, total }) {
  $("findCount").textContent = total ? `${current} / ${total}` : "";
}
eventBus.on("updatefindmatchescount", (e) => showFindCount(e.matchesCount));
eventBus.on("updatefindcontrolstate", (e) => {
  $("findCount").classList.toggle("none", e.state === FindState.NOT_FOUND);
  if (e.state === FindState.NOT_FOUND) $("findCount").textContent = "no match";
  else showFindCount(e.matchesCount);
});
$("find").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); find(e.shiftKey); }
  else if (e.key === "Escape") { clearFind(); $("find").blur(); }
});
