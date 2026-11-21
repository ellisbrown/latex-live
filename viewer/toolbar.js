// Top bar chrome: crisp SVG icon hydration, Light/Dark + tint appearance controls, PDF invert,
// pop-out PDF window, collaborator presence avatars, overflow ⋯ menu (fitBar), and shortcuts.

import { $, el, file, isDiff, setStatus } from "./page.js";
import { postJSON } from "./net.js";
import { canComment, editorAllowed, opensSources, openWhere } from "./role.js";
import { icon } from "./icons.js";
import { onThemeChange, pdfInvert, setPdfInvert, setTint, theme, tint, TINTS, toggleTheme } from "./theme.js";

// Hydrate crisp 1.5px stroke SVG icons into static shell buttons.
function hydrateIcons() {
  const put = (id, name, size = 15) => { const n = $(id); if (n) n.replaceChildren(icon(name, size)); };
  const slot = (id, name, size = 14) => { const n = $(id)?.querySelector(".ic-slot"); if (n) n.replaceChildren(icon(name, size)); };
  put("toggleSide", "panelLeft", 16);
  put("back", "back", 14);
  put("forward", "forward", 14);
  put("cut", "cut", 15);
  put("changesBtn", "diff", 15);
  put("more", "more", 15);
  put("marginBtn", "notes", 15);
  put("rebuild", "rebuild", 15);
  put("popoutBtn", "popout", 14);
  put("foldBtn", "fold", 14);
  put("showInPdf", "sync", 14);
  put("editorClose", "close", 14);
  put("logsClose", "close", 14);
  slot("editBtn", "edit", 14);
  slot("cmdTrigger", "search", 14);
  slot("downloadBtn", "download", 14);
  slot("polishBtn", "sparkle", 13);
  const findIc = $("findIc");
  if (findIc) findIc.replaceChildren(icon("search", 13));
  const palIc = $("paletteIc");
  if (palIc) palIc.replaceChildren(icon("search", 15));

  // Sidebar icon+label tabs.
  const tabMeta = [
    ["files", "files", "Files"],
    ["outline", "outline", "Outline"],
    ["thumbs", "pages", "Pages"],
    ["notes", "notes", "Notes"],
    ["checks", "checks", "Checks"],
    ["figures", "figures", "Figs"],
    ["history", "history", "History"],
    ["papers", "papers", "Refs"],
  ];
  for (const [key, ic, label] of tabMeta) {
    const b = document.querySelector(`#tabs [data-tab=${key}]`);
    if (!b) continue;
    b.replaceChildren(icon(ic, 14), el("span", { textContent: label }), el("span", { className: "n" }));
  }
}
hydrateIcons();

// Appearance controls (Light / Dark, Tint swatches, Invert PDF).
function syncThemeUI() {
  const isDark = theme === "dark";
  $("themeToggle").replaceChildren(icon(isDark ? "sun" : "moon", 15));
  $("themeToggle").title = isDark ? "Switch to light theme" : "Switch to dark theme";
  $("dark").classList.toggle("on", isDark);
  $("dark").textContent = isDark ? "✓ Dark theme" : "Dark theme";
  $("pdfInvertBtn").classList.toggle("on", pdfInvert);
  $("pdfInvertBtn").textContent = pdfInvert ? "✓ Invert PDF colors" : "Invert PDF colors";
  for (const sw of document.querySelectorAll("#tintRow .tint-swatch")) {
    sw.classList.toggle("on", sw.dataset.tint === tint);
    const meta = TINTS.find((t) => t.id === sw.dataset.tint);
    if (meta) sw.style.background = isDark ? meta.darkSwatch : meta.swatch;
  }
}

$("tintRow").replaceChildren(...TINTS.map((t) => el("button", {
  className: "tint-swatch",
  textContent: t.label,
  title: `${t.label} surface tint`,
  dataset: { tint: t.id },
  onclick: () => setTint(t.id),
})));

$("themeToggle").onclick = toggleTheme;
$("dark").onclick = toggleTheme;
$("pdfInvertBtn").onclick = () => setPdfInvert(!pdfInvert);
onThemeChange(syncThemeUI);
syncThemeUI();

// Pop-out synchronized PDF window for a second screen.
$("popoutBtn").onclick = () => {
  const u = new URL(location.href);
  u.searchParams.set("mode", "pdf");
  window.open(u.toString(), `latex-live-pdf:${file}`, "noopener,width=900,height=1100");
};

// Collaborator presence avatar stack in top bar.
const PEER_PALETTE = ["#2563eb", "#0d9488", "#d97706", "#7c3aed", "#db2777", "#16a34a", "#0284c7", "#dc2626"];
export function peerColor(id) {
  let h = 7;
  for (const ch of String(id || "")) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return PEER_PALETTE[h % PEER_PALETTE.length];
}

export function renderPresence(list) {
  const box = $("presence");
  if (!box) return;
  const peers = Array.isArray(list) ? list : [];
  // Deduplicate by user id, preferring non-self or entries with an active file.
  const byUser = new Map();
  for (const p of peers) {
    const prev = byUser.get(p.id);
    if (!prev || (!prev.file && p.file) || (prev.self && !p.self)) byUser.set(p.id, p);
  }
  const unique = [...byUser.values()];
  // Hide when alone in non-shared mode so the bar stays calm.
  box.hidden = unique.length <= 1;
  if (box.hidden) return;
  box.replaceChildren(...unique.slice(0, 6).map((p) => {
    const where = p.file ? ` · ${p.file}${p.line ? `:${p.line}` : ""}` : "";
    const av = el("span", {
      className: `avatar${p.self ? " self" : ""}`,
      textContent: p.initials || "?",
      title: `${p.name || p.id} (${p.role})${where}${p.self ? " — you" : ""}`,
    });
    av.style.background = peerColor(p.id || p.name);
    return av;
  }));
}

// ⋯ More menu & keyboard shortcuts dialog.
export function showMore(open) {
  $("moreMenu").hidden = !open;
  $("more").classList.toggle("on", open);
}
export const revealControl = (node) => { if ($("moreMenu").contains(node)) showMore(true); };
$("more").onclick = () => showMore($("moreMenu").hidden);

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

$("signOut").onclick = async () => {
  showMore(false);
  if (!confirm(`Sign out of latex-live in this browser? Every preview at ${location.hostname} will need its link (with ?t=) again.`)) return;
  const r = await postJSON("logout", {}).catch(() => null);
  if (!r || !r.ok) return setStatus(`couldn't sign out (${r ? r.status : "no answer"})`, "error");
  location.replace(`viewer.html?${new URLSearchParams({ file, "signed-out": "1" })}`);
};
$("keys").querySelector(".close").onclick = () => showKeys(false);

document.addEventListener("mousedown", (e) => {
  if (!$("moreMenu").hidden && !$("moreMenu").contains(e.target) && !$("more").contains(e.target)) showMore(false);
  if (!$("keys").hidden && !$("keys").contains(e.target)) showKeys(false);
  if (!$("fileMenu").hidden && !$("fileMenu").contains(e.target) && !$("filePick").contains(e.target)) $("fileMenu").hidden = true;
  if (!$("polishMenu").hidden && !$("polishMenu").contains(e.target) && !$("polishBtn").contains(e.target)) $("polishMenu").hidden = true;
});

// Single-row overflow manager for top bar items with [data-priority].
const barItems = [...document.querySelectorAll("#bar [data-priority]")];
export function placeItem(item, inBar) {
  const where = inBar ? $("bar") : $("moreItems");
  const next = barItems.slice(barItems.indexOf(item) + 1).find((i) => i.parentElement === where);
  where.insertBefore(item, next || (inBar ? $("spacer") : null));
}

function fitBar() {
  const bar = $("bar"), overflows = () => bar.scrollWidth > bar.clientWidth + 1;
  bar.classList.add("measuring");
  const room = () => bar.getBoundingClientRect().left + bar.clientWidth - parseFloat(getComputedStyle(bar).paddingRight)
    - Math.max(...[...bar.children].filter((c) => c.offsetWidth).map((c) => c.getBoundingClientRect().right));
  const toMenu = (item) => { item.dataset.width = item.offsetWidth; placeItem(item, false); };
  const byPriority = (a, b) => b.dataset.priority - a.dataset.priority;
  for (const item of barItems.filter((i) => i.parentElement === $("moreItems")).sort(byPriority)) {
    if (+item.dataset.width + 8 > room()) break;
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
