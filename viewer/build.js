// The live build, as this tab sees it: the version on screen, whether a build runs or failed, whether one is still to
// come for a change written from here (behind), and Rebuild.

import { $, setStatus } from "./page.js";
import { readBody } from "./net.js";

// Rebuild (live.py /rebuild): latexmk again from the top, e.g. when another build replaced the PDF
// and with it the match to SyncTeX, so double-click and forward search work again.
export let rebuildAt = 0; // (poll leaves "rebuilding…" up until latexmk reports the build)
export async function rebuild() {
  rebuildAt = Date.now();
  setStatus("rebuilding…", "busy");
  const r = await fetch("rebuild", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).catch(() => null);
  if (!r?.ok) setStatus(r ? (await readBody(r)).error : "rebuild failed: can't reach the preview server", "error");
}
$("rebuild").onclick = rebuild;

export let shown = null; // the build on screen: its version (clean:<version> for the notes-free build)
export function setShown(want) { shown = want; }

export let buildVersion = null; // the live build's version, as /status last reported it
export function setBuildVersion(version) { buildVersion = version; }

// Whether a build is adding a change that this tab wrote (a note: wroteAt) and hasn't started yet, for
// downloadBuilt: until a build starts, for 2 seconds at most (latexmk looks for changes four times a second).
export let buildRunning = false, buildStarted = 0, buildFailed = false, wroteAt = 0;
export function seeBuild(running, failed) {
  if (running && !buildRunning) buildStarted = Date.now();
  buildRunning = running;
  buildFailed = failed; // (the last build that finished: the PDF is then the one before)
}
export const wrote = () => { wroteAt = Date.now(); }; // (this tab wrote a change: a note, or a save)
export const behind = () => wroteAt > buildStarted && Date.now() - wroteAt < 2000;
