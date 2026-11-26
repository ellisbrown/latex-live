// The sidebar and its tabs: a tab's panel is filled when it shows, and again after each build.

import { $, el, isDiff, rovingTabs, tabKeys } from "./page.js";
import { doc } from "./pdfview.js";
import { buildVersion, shown } from "./build.js";
import { setSide, setTab, sideOpen, tab } from "./layout.js";
import { icon } from "./icons.js";
import { renderThumbs } from "./pages.js";
import { renderFloats } from "./floats.js";
import { checksVersion, runChecks } from "./checks.js";
import { registerCommands } from "./palette.js";

// The tabs, in viewer.html's order: each a button in #tabs (with its icon, its label and a count, .n) and a panel with
// its name as id, which the module named there fills. addTab gives a tab its looks and how its panel refreshes, and
// returns the panel. Every panel refreshes the same way: refresh runs when the tab shows and after each build; with
// every (ms), also that often while it shows (e.g. for what changes without a build); with onHead, also when the
// paper's last commit changes while it shows (noteHead); with early, once when its button first shows, to fill its
// count before it is opened. A panel whose content can change meanwhile has a refresh button in its top bar
// (page.js refreshButton). A comparison shows only the tabs for it (inDiff).
const tabs = new Map();
export function addTab(name, { label, icon: iconName, title = label, refresh = null, inDiff = false, every = 0, onHead = false,
                               early = false }) {
  const button = $("tabs").querySelector(`[data-tab=${name}]`);
  button.replaceChildren(icon(iconName, 14), el("span", { className: "label", textContent: label }), el("span", { className: "n" }));
  button.title = title;
  button.dataset.icon = iconName;
  button.setAttribute("aria-label", label);
  button.onclick = () => showTab(name);
  tabs.set(name, { button, refresh, onHead, early: early && refresh });
  if (every && refresh) setInterval(() => { if (sideOpen(name) && !document.hidden) refresh(); }, every);
  if (isDiff && !inDiff) showTabButton(name, false);
  return $(name);
}
addTab("outline", { label: "Outline", icon: "outline", title: "The outline, numbered as in the paper", inDiff: true });
addTab("thumbs", { label: "Pages", icon: "pages", title: "The pages", inDiff: true, refresh: () => doc && renderThumbs().catch((e) => console.warn(e)) });
addTab("notes", { label: "Notes", icon: "notes", title: "The notes, by author" });
addTab("checks", { label: "Checks", icon: "checks", title: "Pre-submission checks",
                   refresh: () => doc && checksVersion !== `${buildVersion}/${shown}` && runChecks() });
addTab("figures", { label: "Figures", icon: "figures", title: "Figures and tables",
                    refresh: () => doc && renderFloats().catch((e) => console.warn(e)) });
tabKeys($("tabs"));

// Show or hide a tab's button (e.g. for what a role can't see); the outline shows instead of a tab hidden while open.
export function showTabButton(name, on) {
  const t = tabs.get(name);
  if (!t) return;
  t.button.hidden = !on;
  if (!on && tab === name) showTab("outline");
  if (on && t.early) { // (once)
    t.early = null;
    t.refresh();
  }
  rovingTabs($("tabs"));
  fitTabs();
}

$("toggleSide").onclick = () => showSide($("side").hidden);
export function showSide(on) {
  setSide(on);
  if (on) showTab(tabs.get(tab) && !tabs.get(tab).button.hidden ? tab : "outline");
}
export function showTab(name) {
  setTab(name);
  for (const [key, t] of tabs) {
    t.button.classList.toggle("on", key === name);
    t.button.setAttribute("aria-selected", String(key === name));
    $(key).hidden = key !== name;
  }
  rovingTabs($("tabs"));
  refreshTab();
}
export function refreshTab() {
  if (!$("side").hidden) tabs.get(tab)?.refresh?.();
}

// The paper's last commit, from each status (poll.js): when it changes (an edit committed, in shared mode a minute after
// it), the tab shown refreshes if it follows the commits.
let head = null;
export function noteHead(sha) {
  if (!sha || sha === head) return;
  const moved = head !== null; // (not the first one seen)
  head = sha;
  const t = tabs.get(tab);
  if (moved && t?.onHead && sideOpen(tab)) t.refresh();
}

// The strip stays one row: where the tabs shown don't fit with their labels, they show their icons alone (their names
// in their tooltips, and to screen readers).
const LABELED = 46; // (px: about what a tab needs with its label)
function fitTabs() {
  const width = $("tabs").clientWidth;
  if (!width) return; // (the sidebar is closed: measured when it opens)
  const shownTabs = [...tabs.values()].filter((t) => !t.button.hidden).length;
  $("tabs").classList.toggle("compact", shownTabs * LABELED > width - 12);
}
new ResizeObserver(fitTabs).observe($("tabs"));

// The palette's commands: the sidebar, and each of its tabs shown.
registerCommands(() => [
  { title: $("side").hidden ? "Show the sidebar" : "Hide the sidebar", sub: "Sidebar", icon: "panelLeft", run: () => showSide($("side").hidden) },
  ...[...tabs].filter(([, t]) => !t.button.hidden).map(([name, t]) => ({
    title: t.button.querySelector(".label").textContent, sub: "Sidebar", icon: t.button.dataset.icon || "outline",
    run: () => { showSide(true); showTab(name); t.button.focus(); } })),
]);
