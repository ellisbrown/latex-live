// The top bar and the PDF's bar: their icons, the ⋯ menus with the controls that move into them when a bar runs out of
// room, the look (the theme, the tint, inverting the PDF), the pop-out window, and the list of shortcuts.

import { $, el, file, isDiff, isPopout, setStatus } from "./page.js";
import { postJSON } from "./net.js";
import { canComment, editorAllowed, opensSources, openWhere } from "./role.js";
import { icon } from "./icons.js";
import { onThemeChange, pdfInvert, setPdfInvert, setTheme, setTint, theme, tint, TINTS } from "./theme.js";

// The chrome's icons: [button, icon] (into a button's .ic-slot, before its text, when it has one).
for (const [id, name, size] of [["toggleSide", "panelLeft", 16], ["back", "back", 14], ["forward", "forward", 14],
  ["popoutBtn", "popout", 14], ["cut", "cut"], ["changesBtn", "diff"], ["downloadBtn", "download", 14], ["rebuild", "rebuild"],
  ["more", "more"], ["pdfMore", "more"], ["marginBtn", "notes"], ["showInPdf", "sync", 14], ["editorClose", "close", 14]]) {
  ($(id).querySelector(".ic-slot") || $(id)).replaceChildren(icon(name, size));
}

// The look: the theme (the top bar's sun or moon, or the ⋯ menu), the tint of the surfaces, and the PDF's colors.
$("tintRow").replaceChildren(...TINTS.map((t) => el("button", { className: "tint", textContent: t.label, title: `${t.label} surfaces`,
                                                             dataset: { tint: t.id }, onclick: () => setTint(t.id) })));
function showLook() {
  const dark = theme === "dark";
  $("themeToggle").replaceChildren(icon(dark ? "sun" : "moon", 15));
  $("themeToggle").title = dark ? "Light theme" : "Dark theme";
  $("dark").classList.toggle("on", dark);
  $("pdfInvert").classList.toggle("on", pdfInvert);
  for (const b of $("tintRow").children) {
    const t = TINTS.find((x) => x.id === b.dataset.tint);
    b.classList.toggle("on", t.id === tint);
    b.style.background = dark ? t.darkSwatch : t.swatch;
  }
}
$("themeToggle").onclick = $("dark").onclick = () => setTheme(theme === "dark" ? "light" : "dark");
$("pdfInvert").onclick = () => setPdfInvert(!pdfInvert);
onThemeChange(showLook);
showLook();

// The pop-out window: the PDF alone, which follows this window's source (layout.js, links.js). One per document.
$("popoutBtn").onclick = () => {
  const url = new URL(location.href);
  url.searchParams.set("mode", "pdf");
  window.open(url, `latex-live-pdf:${file}`, "width=900,height=1100");
};
$("popoutBtn").hidden = isPopout || isDiff;

// The ⋯ menus: the top bar's (the look, Compare and the rest, plus the top bar's controls that don't fit) and the PDF
// bar's (its controls that don't fit), each under its button.
const menus = [{ menu: $("moreMenu"), button: $("more") }, { menu: $("pdfMenu"), button: $("pdfMore") }];
function showMenu({ menu, button }, open) {
  if (open && menu === $("pdfMenu")) { // (the top bar's sits at the window's right edge: CSS)
    const r = button.getBoundingClientRect();
    Object.assign(menu.style, { top: `${r.bottom + 4}px`, right: `${Math.max(8, innerWidth - r.right)}px` });
  }
  menu.hidden = !open;
  button.classList.toggle("on", open);
}
export const showMore = (open) => showMenu(menus[0], open);
for (const m of menus) m.button.onclick = () => showMenu(m, m.menu.hidden);
export function revealControl(node) { // (before focusing a control that may be in a menu)
  for (const m of menus) if (m.menu.contains(node)) showMenu(m, true);
}
// The shortcuts list (?), with only the rows for what this user can do.
export function showKeys(open) {
  if (open) {
    $("keysComment").hidden = !canComment();
    $("keysEdit").hidden = !editorAllowed() || isPopout;
    $("keysDblclickRow").hidden = isDiff || !opensSources();
    $("keysDblclick").textContent = `open the source line in ${openWhere()}`;
  }
  $("keys").hidden = !open;
}
$("keysBtn").onclick = () => { showMore(false); showKeys(true); };
// Sign out (⋯ menu): live.py forgets this browser's session, which is the address's (whatever the port), so every
// preview there needs its link again; the page then says so (?signed-out=1).
$("signOut").onclick = async () => {
  showMore(false);
  if (!confirm(`Sign out of latex-live in this browser? Every preview at ${location.hostname} will need its link (with ?t=) again.`)) return;
  const r = await postJSON("logout", {}).catch(() => null);
  if (!r || !r.ok) return setStatus(`couldn't sign out (${r ? r.status : "no answer"})`, "error");
  location.replace(`viewer.html?${new URLSearchParams({ file, "signed-out": "1" })}`);
};
$("keys").querySelector(".close").onclick = () => showKeys(false);
document.addEventListener("mousedown", (e) => { // (not the welcome guide: it closes with its own buttons, or Esc)
  for (const m of menus) if (!m.menu.hidden && !m.menu.contains(e.target) && !m.button.contains(e.target)) showMenu(m, false);
  if (!$("keys").hidden && !$("keys").contains(e.target)) showKeys(false);
  if (!$("fileMenu").hidden && !$("fileMenu").contains(e.target) && !$("filePick").contains(e.target)) $("fileMenu").hidden = true;
});

// Each bar keeps to one row: when it overflows, its controls (those with a data-priority) move into its menu, lowest
// data-priority first, and come back once there is room again. A control comes back to where it was, on its side of
// the spacer, whatever else the bar holds by then: a marker (an empty comment) keeps its place. A bar refits when it
// resizes, when a control appears or disappears, and when a text in it that changes its width changes (watch).
// It returns how to move one of its controls (place), also those that start in the menu (extra: they come into the
// bar left of the spacer).
function fitRow(bar, menu, more, { extra = [], watch = [] } = {}) {
  const items = [...bar.querySelectorAll(":scope > [data-priority]"), ...extra]; // (in the bar's order)
  const marks = new Map(items.map((item) => {
    const mark = document.createComment(item.id);
    (extra.includes(item) ? bar.querySelector(":scope > .spacer") : item).before(mark);
    return [item, mark];
  }));
  const place = (item, inBar) => {
    if (inBar) return marks.get(item).after(item);
    const next = items.slice(items.indexOf(item) + 1).find((i) => i.parentElement === menu);
    menu.insertBefore(item, next || null);
  };
  const fit = () => {
    const overflows = () => bar.scrollWidth > bar.clientWidth + 1;
    bar.classList.add("measuring"); // (everything at full width, packed to the left)
    const room = () => bar.getBoundingClientRect().left + bar.clientWidth - parseFloat(getComputedStyle(bar).paddingRight) -
      Math.max(...[...bar.children].filter((c) => c.offsetWidth).map((c) => c.getBoundingClientRect().right));
    const toMenu = (item) => { item.dataset.width = item.offsetWidth; place(item, false); };
    const byPriority = (a, b) => b.dataset.priority - a.dataset.priority;
    for (const item of items.filter((i) => i.parentElement === menu).sort(byPriority)) {
      if (+item.dataset.width + 8 > room()) break; // (no moving back and forth while it can't fit)
      place(item, true);
      if (overflows()) { toMenu(item); break; }
    }
    for (const item of items.filter((i) => i.parentElement === bar).sort(byPriority).reverse()) {
      if (!overflows()) break;
      toMenu(item);
    }
    if (more) more.hidden = !items.some((i) => i.parentElement === menu && !i.hidden); // (a menu only for the overflow)
    bar.classList.remove("measuring");
  };
  new ResizeObserver(fit).observe(bar);
  const shownChanged = new MutationObserver((records) => {
    if (records.some((r) => r.target !== menu && (r.oldValue === null) !== !r.target.hasAttribute("hidden"))) fit();
  });
  for (const node of [bar, menu]) shownChanged.observe(node, { subtree: true, attributes: true, attributeFilter: ["hidden"], attributeOldValue: true });
  const textChanged = new MutationObserver(fit);
  for (const node of watch) textChanged.observe(node, { childList: true, characterData: true, subtree: true });
  document.fonts?.ready.then(fit);
  return place;
}
export const placeItem = fitRow($("bar"), $("moreItems"), null, { extra: [$("compare")], watch: [$("status"), $("pages")] });
fitRow($("pdfHead"), $("pdfMenu"), $("pdfMore"), { watch: [$("findCount")] });
