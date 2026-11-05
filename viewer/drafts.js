// This page's name, and the unsaved edits this browser keeps (editor.js keeps and offers them; main.js reopens the newest).

import { hubBase } from "./page.js";

// This page's own name (for the edits it keeps: keptKey), which its saves go by too (editor.js saveId).
export const newSaveId = () => `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
export const EDITOR_ID = newSaveId();

// Unsaved edits survive the page (a crash, or a reload while offline or signed out): the open file's
// text, with the version and text the edits started from, is kept in localStorage (per paper, file
// and page: two tabs on one file keep theirs apart) until a save goes through. Opening that file
// again offers to restore the newest, through the usual merging save.
const keptPrefix = (path) => `latex-live:${hubBase}unsaved:${path}#`;
export const keptKey = (path, page = EDITOR_ID) => keptPrefix(path) + page;
function parseKept(key) {
  try {
    const kept = JSON.parse(localStorage.getItem(key) || "null");
    return kept && typeof kept.path === "string" && typeof kept.text === "string" && typeof kept.base === "string" ? { ...kept, key } : null;
  } catch {
    return null;
  }
}
// The kept edits whose keys start with prefix, newest first ({..., key}).
const allKept = (prefix) => Array.from({ length: localStorage.length }, (_, i) => localStorage.key(i))
  .filter((k) => k?.startsWith(prefix)).map(parseKept).filter(Boolean).sort((a, b) => b.time - a.time);
export const readKept = (path) => allKept(keptPrefix(path)).find((kept) => kept.path === path) || null;
export const dropKept = (path, key = keptKey(path)) => localStorage.removeItem(key); // (this page's, unless key says whose)
export const latestKept = () => allKept(`latex-live:${hubBase}unsaved:`)[0] || null;
