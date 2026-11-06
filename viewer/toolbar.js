// The toolbar: the ⋯ menu, the controls that move into it when the toolbar runs out of room, the list of shortcuts, and
// dark mode.

import { $, file, isDiff, setStatus } from "./page.js";
import { postJSON } from "./net.js";
import { canComment, editorAllowed, opensSources, openWhere } from "./role.js";
import { setToggle } from "./layout.js";

// The ⋯ menu: dark mode and Compare, plus the toolbar's controls that don't fit (fitBar).
export function showMore(open) {
  $("moreMenu").hidden = !open;
  $("more").classList.toggle("on", open);
}
export const revealControl = (node) => { if ($("moreMenu").contains(node)) showMore(true); }; // (before focusing a control there)
$("more").onclick = () => showMore($("moreMenu").hidden);
// The shortcuts list (?), with only the rows for what this user can do.
export function showKeys(open) {
  if (open) {
    $("keysComment").hidden = !canComment();
    $("keysEdit").hidden = !editorAllowed();
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
  if (!$("moreMenu").hidden && !$("moreMenu").contains(e.target) && !$("more").contains(e.target)) showMore(false);
  if (!$("keys").hidden && !$("keys").contains(e.target)) showKeys(false);
  if (!$("fileMenu").hidden && !$("fileMenu").contains(e.target) && !$("filePick").contains(e.target)) $("fileMenu").hidden = true;
});

// The toolbar keeps to one row: when it overflows, its controls move into the ⋯ menu, lowest
// data-priority first, and come back once there is room again. It refits when the window
// resizes, when a control appears or disappears, and when the status or the page count changes.
const barItems = [...document.querySelectorAll("[data-priority]")]; // (in toolbar order)
export function placeItem(item, inBar) {
  const where = inBar ? $("bar") : $("moreItems");
  const next = barItems.slice(barItems.indexOf(item) + 1).find((i) => i.parentElement === where);
  where.insertBefore(item, next || (inBar ? $("spacer") : null));
}
function fitBar() {
  const bar = $("bar"), overflows = () => bar.scrollWidth > bar.clientWidth + 1;
  bar.classList.add("measuring"); // (everything at full width, packed to the left)
  const room = () => bar.getBoundingClientRect().left + bar.clientWidth - parseFloat(getComputedStyle(bar).paddingRight) -
    Math.max(...[...bar.children].filter((c) => c.offsetWidth).map((c) => c.getBoundingClientRect().right));
  const toMenu = (item) => { item.dataset.width = item.offsetWidth; placeItem(item, false); };
  const byPriority = (a, b) => b.dataset.priority - a.dataset.priority;
  for (const item of barItems.filter((i) => i.parentElement === $("moreItems")).sort(byPriority)) {
    if (+item.dataset.width + 8 > room()) break; // (no moving back and forth while it can't fit)
    placeItem(item, true);
    if (overflows()) { toMenu(item); break; }
  }
  for (const item of barItems.filter((i) => i.parentElement === bar).sort(byPriority).reverse()) {
    if (!overflows()) break;
    toMenu(item);
  }
  bar.classList.remove("measuring");
}
new ResizeObserver(fitBar).observe($("bar"));
const shownChanged = new MutationObserver((records) => {
  if (records.some((r) => r.target !== $("moreMenu") && (r.oldValue === null) !== !r.target.hasAttribute("hidden"))) fitBar();
});
for (const node of [$("bar"), $("moreMenu")]) {
  shownChanged.observe(node, { subtree: true, attributes: true, attributeFilter: ["hidden"], attributeOldValue: true });
}
const textChanged = new MutationObserver(fitBar);
for (const node of [$("status"), $("pages")]) textChanged.observe(node, { childList: true, characterData: true, subtree: true });
document.fonts?.ready.then(fitBar);

// Dark mode (remembered: setToggle).
$("dark").onclick = () => { setToggle("dark", !document.body.classList.contains("dark")); $("dark").classList.toggle("on"); };
if (localStorage.getItem("latex-live:dark")) { document.body.classList.add("dark"); $("dark").classList.add("on"); }
