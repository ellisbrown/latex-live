// The keyboard shortcuts.

import { $, editorShown, isDiff, setStatus } from "./page.js";
import { canComment, editorAllowed, isCollaborator, me, notSetUp, ownerName } from "./role.js";
import { viewer } from "./pdfview.js";
import { find } from "./search.js";
import { hidePreview } from "./preview.js";
import { ahead, back, travel } from "./navigation.js";
import { ed, saveSource, showEditor } from "./editor.js";
import { revealControl, showKeys, showMore } from "./toolbar.js";
import { showDownloads } from "./downloads.js";
import { keepFocusIn, showWelcome } from "./welcome.js";
import { closeNotePop, startComment } from "./comments.js";
import { goToChange, toggleChanges } from "./changes.js";

window.addEventListener("keydown", (e) => {
  const mod = e.ctrlKey || e.metaKey;
  if (!$("welcome").hidden) { // (while the guide is open, only its own keys)
    if (e.key === "Escape") showWelcome(false);
    else if (e.key === "Tab" && !e.defaultPrevented) keepFocusIn($("welcome"), e); // (not a Tab the source pane took, to indent)
    return;
  }
  if (e.target.closest?.("#editorPane")) { // (keys typed in the source pane are the editor's: c, d, Cmd+F, ...)
    if (mod && e.key === "s" && !e.defaultPrevented) { e.preventDefault(); saveSource(); } // (e.g. after a click in its header)
    return;
  }
  if (mod && e.key === "s" && ed && editorShown()) { e.preventDefault(); saveSource(); }
  else if (mod && (e.key === "=" || e.key === "+")) { e.preventDefault(); viewer.increaseScale(); }
  else if (mod && e.key === "-") { e.preventDefault(); viewer.decreaseScale(); }
  else if (mod && e.key === "0") { e.preventDefault(); viewer.currentScaleValue = "page-width"; }
  else if (mod && e.key === "f") { e.preventDefault(); revealControl($("find")); $("find").focus(); $("find").select(); }
  else if (mod && e.key === "g") { e.preventDefault(); find(e.shiftKey); }
  else if (e.target.tagName === "INPUT") return; // keep word-wise cursor keys in the text boxes
  else if ((e.altKey && e.key === "ArrowLeft") || (e.metaKey && e.key === "[")) { e.preventDefault(); travel(back, ahead); }
  else if ((e.altKey && e.key === "ArrowRight") || (e.metaKey && e.key === "]")) { e.preventDefault(); travel(ahead, back); }
  else if (e.key === "Escape") { hidePreview(); $("comparePanel").hidden = true; closeNotePop(); showMore(false); showKeys(false); showDownloads(false); }
  else if (!mod && !e.altKey && e.key === "?") { e.preventDefault(); showKeys($("keys").hidden); }
  else if (!mod && !e.altKey && e.key === "c") {
    if (!$("commentBtn").hidden) { e.preventDefault(); startComment(); }
    else if (isCollaborator() && !isDiff && !canComment()) setStatus(notSetUp() || `Your access is read-only: ask ${ownerName()} if you want to comment`, "error");
  }
  else if (!mod && !e.altKey && e.key === "d") { e.preventDefault(); toggleChanges(); }
  else if (!mod && !e.altKey && (e.key === "." || e.key === ",")) { e.preventDefault(); goToChange(e.key === "." ? 1 : -1); }
  else if (!mod && !e.altKey && e.key === "e") {
    if (editorAllowed()) { e.preventDefault(); showEditor(!editorShown()); }
    else if (isCollaborator() && !isDiff) setStatus(`Editing the source isn't part of your access (${me.role}): ask ${ownerName()} if you need it`, "error");
  }
});
