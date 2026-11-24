// The sidebar and its tabs: a tab's panel is filled when it shows, and again after each build.

import { $, el, isDiff } from "./page.js";
import { doc } from "./pdfview.js";
import { buildVersion, shown } from "./build.js";
import { setSide, setTab, tab } from "./layout.js";
import { icon } from "./icons.js";
import { renderThumbs } from "./pages.js";
import { renderFloats } from "./floats.js";
import { checksVersion, runChecks } from "./checks.js";

// The tabs, in order: each a button in #tabs (with its icon, its label and a count, .n) and a panel with its name as
// id, which refresh fills (when the tab shows, and after each build). A part of the viewer adds its own tab with
// addTab; viewer.html has the buttons and panels of the ones here, which their modules fill as they load. A
// comparison shows only the tabs for it (inDiff).
const tabs = new Map();
export function addTab(name, { label, icon: iconName, title = label, refresh = null, inDiff = false }) {
  let button = $("tabs").querySelector(`[data-tab=${name}]`);
  if (!button) {
    button = el("button", { title, dataset: { tab: name } });
    $("tabs").append(button);
    $("side").append(el("div", { id: name, className: "panel", hidden: true }));
  }
  button.replaceChildren(icon(iconName, 14), el("span", { className: "label", textContent: label }), el("span", { className: "n" }));
  button.setAttribute("role", "tab");
  button.onclick = () => showTab(name);
  tabs.set(name, { button, refresh });
  if (isDiff && !inDiff) showTabButton(name, false);
}
addTab("outline", { label: "Outline", icon: "outline", inDiff: true });
addTab("thumbs", { label: "Pages", icon: "pages", inDiff: true, refresh: () => doc && renderThumbs() });
addTab("notes", { label: "Notes", icon: "notes" });
addTab("checks", { label: "Checks", icon: "checks", refresh: () => doc && checksVersion !== `${buildVersion}/${shown}` && runChecks() });
addTab("figures", { label: "Figures", icon: "figures", refresh: () => doc && renderFloats().catch((e) => console.warn(e)) });

// Show or hide a tab's button (e.g. for what a role can't see); the outline shows instead of a tab hidden while open.
export function showTabButton(name, on) {
  const button = tabs.get(name)?.button;
  if (!button) return;
  button.hidden = !on;
  if (!on && tab === name) showTab("outline");
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
  refreshTab();
}
export function refreshTab() {
  if (!$("side").hidden) tabs.get(tab)?.refresh?.();
}
