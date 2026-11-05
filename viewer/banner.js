// The banner above the PDF: a failed build, another build that replaced the PDF, or the access ended.

import { $, ACCESS_ENDED, docFile, el, isDiff, setStatus } from "./page.js";
import { canOpenFile, me, opensAt, ownerName } from "./role.js";
import { rebuild } from "./build.js";
import { ed } from "./editor.js";
import { openSource } from "./links.js";

// Build-status banner: shows the first errors of a failed build, or else of a failed notes-free
// pass (the notes-free view and its page count are then out of date); click one to open it in the
// editor. Failing those, it says when another build (e.g. make) replaced the published PDF.
let dismissed = null;
const closeButton = (onclick) => {
  const b = el("button", { className: "close", textContent: "✕", title: "Close", onclick });
  b.setAttribute("aria-label", "Close");
  return b;
};
export function showBuild(build, notesFree, overwritten) {
  const banner = $("banner");
  accessClosed = false; // (the server answers again)
  if (!(build && !build.ok) && !isDiff && notesFree && !notesFree.ok) build = { ...notesFree, notesFree: true };
  if (!(build && !build.ok) && !isDiff && overwritten) build = { ok: false, time: overwritten, errors: [], overwritten: true };
  if (!build || build.ok || dismissed === build.time) return (banner.hidden = true);
  banner.replaceChildren();
  banner.dataset.kind = "build";
  const close = closeButton(() => { dismissed = build.time; banner.hidden = true; });
  banner.append(close, isDiff ? `Diff failed at ${build.time}.`
    : build.notesFree ? `The notes-free build failed at ${build.time}${build.msg ? ` (${build.msg})` : ""}; the notes-free view and its page count are out of date.`
    : build.overwritten ? `Another build (e.g. make) replaced ${docFile} at ${build.time}: the view with notes shows that PDF, and double-click may land on the wrong line.`
    : `Build failed at ${build.time}; showing the last good PDF.`);
  for (const err of build.errors.slice(0, 3)) {
    const line = el("div");
    if (err.file && canOpenFile(err.file)) {
      const a = opensAt(el("a", { textContent: `${err.file}:${err.line}` }), `${err.file}:${err.line}`);
      a.onclick = () => openSource(err.file, err.line);
      line.append(a, `  ${err.msg}`);
    } else {
      line.append(err.file ? `${err.file}:${err.line}  ${err.msg}` : err.msg);
    }
    banner.append(line);
  }
  if (build.errors.length > 3) banner.append(`(+${build.errors.length - 3} more${me.can.openHost ? " in the Problems panel" : ""})`);
  if (build.overwritten && me.can.rebuild) banner.append(" ", el("a", { textContent: "Rebuild now", onclick: rebuild }));
  banner.hidden = false;
}
// The access ended (403 for status or me): the same banner, with what to try and where unsaved edits
// are, until the server answers again (closed by hand, it stays closed until then).
let accessClosed = false;
export function showAccessEnded() {
  setStatus(ACCESS_ENDED, "error");
  const banner = $("banner");
  if (accessClosed || (banner.dataset.kind === "access" && !banner.hidden)) return;
  banner.replaceChildren(closeButton(() => { accessClosed = true; banner.hidden = true; }),
    `You no longer have access to this preview, or your sign-in or link expired. Reload the page; if that doesn't help, ask ${ownerName()}.`,
    ...(ed?.dirty ? [` Your unsaved edits to ${ed.path} are kept in this browser.`] : []));
  banner.dataset.kind = "access";
  banner.hidden = false;
}
