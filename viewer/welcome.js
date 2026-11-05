// The welcome guide.

import { $, firstVisit, isDiff } from "./page.js";
import { authors, editorAllowed, isCollaborator, me, notSetUp, ownerName } from "./role.js";
import { showKeys, showMore } from "./toolbar.js";
import { showDownloads } from "./downloads.js";

// The welcome guide: what this is, and how to comment, edit and find your way, for the role at hand.
// Whether it shows by itself is kept in latex-live:welcome: unset, "later" (it showed by itself and
// hasn't been put away), or "hidden" (closed with "Don't show this again" checked). It shows by
// itself while "later", and while unset to collaborators in shared mode and to someone new to
// latex-live, though not to an owner who has used it before; ? and the ⋯ menu bring it back.
let welcomeFocus = null; // (what had the focus before the guide opened: it gets it back)
export function showWelcome(open, auto = false) {
  const w = $("welcome");
  if (!open) {
    if (w.hidden) return;
    localStorage.setItem("latex-live:welcome", w.querySelector("input").checked ? "hidden" : "later");
    w.hidden = true;
    welcomeFocus?.focus?.();
    welcomeFocus = null;
    return;
  }
  const part = (name, on) => { for (const e of w.querySelectorAll(`[data-part=${name}]`)) e.hidden = !on; };
  const missing = notSetUp();
  part("comment", me.can.comment || !!missing);
  part("pick", !isCollaborator());
  part("locked", isCollaborator());
  part("edit", editorAllowed());
  part("download", !!me.can.download);
  part("shared", isCollaborator() && (me.can.comment || me.can.edit));
  w.querySelector(".how").hidden = !!missing;
  w.querySelector(".missing").hidden = !missing;
  w.querySelector(".missing").textContent = missing || "";
  const initials = isCollaborator() && authors.find((a) => a.macro === me.macro)?.author;
  w.querySelector(".initials").textContent = initials ? ` (${initials})` : "";
  w.querySelector(".changes").textContent = me.can.edit ? "Your edits and notes are" : "Your notes are";
  w.querySelector(".owner").textContent = ownerName();
  w.querySelector("input").checked = true;
  if (auto) localStorage.setItem("latex-live:welcome", "later");
  showMore(false);
  showKeys(false);
  showDownloads(false);
  if (w.hidden) welcomeFocus = document.activeElement;
  w.hidden = false;
  w.querySelector(".ok").focus();
}
export function welcomeDue() {
  const state = localStorage.getItem("latex-live:welcome");
  return !isDiff && me.shared && (state === "later" || (state === null && (isCollaborator() || firstVisit)));
}
// Tab and Shift+Tab stay within the guide while it is open.
export function keepFocusIn(box, e) {
  const stops = [...box.querySelectorAll("button, input, a[href], textarea")].filter((n) => !n.disabled && n.getClientRects().length);
  const i = stops.indexOf(document.activeElement);
  if (i >= 0 && !(e.shiftKey ? i === 0 : i === stops.length - 1)) return; // (the browser moves on within it)
  e.preventDefault(); // (from its last stop, its first; from its first, back to its last; from outside it, in)
  stops.at(e.shiftKey ? -1 : 0)?.focus();
}
$("welcome").querySelector(".close").onclick = () => showWelcome(false);
$("welcome").querySelector(".ok").onclick = () => showWelcome(false);
$("welcomeBtn").onclick = () => showWelcome(true);
$("keys").querySelector(".welcome").onclick = () => showWelcome(true);
