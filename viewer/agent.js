// The agent hook (live.py POST /agent/revise; docs/agent-plugins.md), for the owner when a plugin is set up (me.can.agent):
// the palette's "Polish with the agent" sends the selection in the source pane (or the paragraph at the cursor), with
// the paragraph around it and maybe a note, and a card under the text shows the plugin's revision as a word diff with
// its reason. Accepting it (Cmd/Ctrl+Enter) edits the text as typing would: the source pane saves it as usual, and
// Cmd/Ctrl+Z takes it back. Esc dismisses the card, whatever it shows; asking again, cancelling or opening another file
// drops an answer still on its way.

import { editorShown, el, setStatus } from "./page.js";
import { readBody } from "./net.js";
import { me } from "./role.js";
import { addEditorExtension } from "./cm.js";
import { cmView, ed } from "./editor.js";
import { lineHunks } from "./diff.js";
import { paletteOpen, registerCommands } from "./palette.js";

const SELECTION_LIMIT = 16000, PARAGRAPH_LIMIT = 32000, NOTE_LIMIT = 2000; // (live.py's, in characters)
const IS_MAC = /^Mac|^iP/.test(navigator.platform);
const STRUCTURE = /^\s*(%|\\(begin|end|documentclass|usepackage|part|chapter|(sub)*section|(sub)?paragraph)\b)/;

// The card, in the open file's editor state (gone with it when another file opens): {id (its request), file, from, to
// (the text it is about, moved with the edits made since), original (that text when asked), note, kind ("note": asking
// for one; "loading", "error", "result"), error, replacement, rationale}.
let setCard = null, cardField = null; // (the state effect that sets it, or null, and its field: once CodeMirror has loaded)
let seq = 0, aborter = null; // (the latest request, and how to cancel it)
const card = () => (cardField && cmView ? cmView.state.field(cardField, false) || null : null);
const show = (value) => cmView?.dispatch({ effects: setCard.of(value) });
const allowed = () => !!me.can.agent && editorShown() && !!cmView && !!ed && !ed.gone;

addEditorExtension(({ state, view }) => {
  setCard = state.StateEffect.define();
  class Card extends view.WidgetType {
    constructor(value) { super(); this.value = value; }
    eq(other) { return other.value === this.value; }
    ignoreEvent() { return true; } // (its buttons and note box are its own, not the editor's)
    toDOM() { return cardElement(this.value); }
  }
  const decorations = (doc, v) => {
    const marks = v.to > v.from ? [view.Decoration.mark({ class: "cm-agent-text" }).range(v.from, v.to)] : [];
    const below = doc.lineAt(v.to > v.from ? v.to - 1 : v.to).to; // (under the last line of the text, not the next one)
    return view.Decoration.set([...marks, view.Decoration.widget({ widget: new Card(v), block: true, side: 1 }).range(below)], true);
  };
  cardField = state.StateField.define({
    create: () => null,
    update: (value, tr) => {
      const set = tr.effects.reduce((last, e) => (e.is(setCard) ? e : last), null);
      if (set) return set.value && { ...set.value, decos: decorations(tr.state.doc, set.value) };
      if (!value || !tr.docChanged) return value;
      const from = tr.changes.mapPos(value.from, 1), to = Math.max(from, tr.changes.mapPos(value.to, -1));
      return { ...value, from, to, decos: value.decos.map(tr.changes) };
    },
    provide: (f) => view.EditorView.decorations.from(f, (value) => (value ? value.decos : view.Decoration.none)),
  });
  const keys = state.Prec.highest(view.keymap.of([{ key: "Mod-Enter", run: () => accept() }, { key: "Escape", run: () => dismiss() }]));
  return [cardField, keys];
});

// What to send for the text from..to of the editor's state (else the selection, or the paragraph at the cursor):
// {from, to, selection, paragraph (the lines around it up to blank ones; past the limit, a window around it), line},
// or why there is nothing to send.
function context(st, range = null) {
  const doc = st.doc;
  let { from, to } = range || st.selection.main;
  const blank = (n) => !doc.line(n).text.trim();
  if (from === to && !range) { // (the lines of prose around the cursor: not a heading, an environment's ends, or a comment)
    const prose = (n) => !blank(n) && !STRUCTURE.test(doc.line(n).text);
    const at = doc.lineAt(from);
    if (!prose(at.number)) return "Select some text, or put the cursor in a paragraph, to polish it";
    let first = at.number, last = at.number;
    while (first > 1 && prose(first - 1)) first--;
    while (last < doc.lines && prose(last + 1)) last++;
    [from, to] = [doc.line(first).from, doc.line(last).to];
  }
  const selection = doc.sliceString(from, to);
  if (!selection.trim()) return "Select some text to polish";
  if (selection.length > SELECTION_LIMIT) return `Select at most ${SELECTION_LIMIT} characters to polish`;
  let first = doc.lineAt(from).number, last = doc.lineAt(to > from ? to - 1 : to).number;
  while (first > 1 && !blank(first - 1)) first--;
  while (last < doc.lines && !blank(last + 1)) last++;
  let start = doc.line(first).from, end = doc.line(last).to;
  if (end - start > PARAGRAPH_LIMIT) {
    start = Math.max(start, from - Math.floor((PARAGRAPH_LIMIT - selection.length) / 2));
    end = Math.min(end, start + PARAGRAPH_LIMIT);
    start = Math.max(doc.line(first).from, end - PARAGRAPH_LIMIT);
  }
  return { from, to, selection, paragraph: doc.sliceString(start, end), line: doc.lineAt(from).number };
}

// Ask the plugin about the text (range: the card's, to ask again; else the selection) with a note.
async function ask(range = null, note = "") {
  if (!allowed()) return;
  const ctx = context(cmView.state, range);
  if (typeof ctx === "string") return setStatus(ctx, "error");
  const id = ++seq;
  aborter?.abort();
  aborter = new AbortController();
  show({ id, file: ed.path, from: ctx.from, to: ctx.to, original: ctx.selection, note, kind: "loading" });
  cmView.focus(); // (for Esc, also after the palette)
  let r = null, body = {};
  try {
    r = await fetch("agent/revise", { method: "POST", headers: { "Content-Type": "application/json" }, signal: aborter.signal,
                                      body: JSON.stringify({ selection: ctx.selection, paragraph: ctx.paragraph, note,
                                                             file: ed.path, line: ctx.line }) });
    body = await readBody(r);
  } catch { /* cancelled, or no answer */ }
  const now = card();
  if (id !== seq || now?.id !== id) return; // (cancelled, asked again, or another file opened meanwhile)
  if (r?.ok && typeof body.replacement === "string") {
    show({ ...now, kind: "result", replacement: body.replacement, rationale: typeof body.rationale === "string" ? body.rationale : "" });
  } else {
    show({ ...now, kind: "error", error: body.error || (r ? `the agent did not answer (${r.status})` : "can't reach the preview server") });
  }
}
// Ask with a note: the card first asks for it.
function askWithNote() {
  if (!allowed()) return;
  const ctx = context(cmView.state);
  if (typeof ctx === "string") return setStatus(ctx, "error");
  aborter?.abort();
  show({ id: ++seq, file: ed.path, from: ctx.from, to: ctx.to, original: ctx.selection, note: "", kind: "note" });
}

// Put the revision in place of the text, if the text is still as it was when asked (Cmd/Ctrl+Enter, or Accept).
function accept() {
  const v = card();
  if (!v || v.kind !== "result" || paletteOpen()) return false;
  if (v.replacement === v.original) return true; // (nothing to take: not a blank line either)
  if (ed?.path !== v.file || ed.gone || cmView.state.doc.sliceString(v.from, v.to) !== v.original) {
    show({ ...v, kind: "error", error: "The text changed since it was sent: ask again" });
    return true;
  }
  seq++;
  cmView.dispatch({ changes: { from: v.from, to: v.to, insert: v.replacement }, selection: { anchor: v.from + v.replacement.length },
                    effects: setCard.of(null), userEvent: "input", scrollIntoView: true });
  cmView.focus();
  setStatus(`Took the agent's revision (${IS_MAC ? "Cmd" : "Ctrl"}+Z undoes it)`, "fresh");
  return true;
}
// Dismiss the card, whatever it shows, and drop its answer if it is still on its way (Esc, Reject, Cancel).
function dismiss() {
  if (!card() || paletteOpen()) return false;
  seq++;
  aborter?.abort();
  show(null);
  cmView.focus();
  return true;
}

// The card's element: what it shows, and its buttons.
function cardElement(v) {
  const button = (text, onclick, cls = "") => el("button", { textContent: text, className: cls, onclick });
  const noteBox = (placeholder) => el("input", { className: "cm-agent-note", value: v.note || "", placeholder, maxLength: NOTE_LIMIT,
                                                 spellcheck: false, title: "A note for the agent" });
  const again = (box) => ask({ from: v.from, to: v.to }, box.value.trim());
  const parts = [el("div", { className: "cm-agent-head", textContent: { note: "Polish with the agent", loading: "Asking the agent…",
                                                                         error: "The agent's revision", result: "The agent's revision" }[v.kind] })];
  let box = null;
  if (v.kind === "note") {
    box = noteBox("A note for the agent, e.g. shorter (optional)");
    parts.push(box, el("div", { className: "cm-agent-acts" }, button("Ask", () => again(box), "primary"), button("Cancel", dismiss)));
  } else if (v.kind === "loading") {
    parts.push(el("div", { className: "cm-agent-acts" }, button("Cancel (Esc)", dismiss)));
  } else if (v.kind === "error") {
    box = noteBox("A note for another try (optional)");
    parts.push(el("div", { className: "cm-agent-why error", textContent: v.error }), box,
               el("div", { className: "cm-agent-acts" }, button("Ask again", () => again(box)), button("Dismiss (Esc)", dismiss)));
  } else {
    const same = v.replacement === v.original;
    box = noteBox("A note for another try (optional)");
    parts.push(el("div", { className: "cm-agent-diff" }, ...(same ? [el("span", { textContent: "No change." })] : wordDiff(v.original, v.replacement))));
    if (v.rationale) parts.push(el("div", { className: "cm-agent-why", textContent: v.rationale }));
    parts.push(box, el("div", { className: "cm-agent-acts" },
                       ...(same ? [] : [button(`Accept (${IS_MAC ? "⌘" : "Ctrl+"}↩)`, accept, "primary")]),
                       button("Reject (Esc)", dismiss), button("Ask again", () => again(box))));
  }
  const node = el("div", { className: "cm-agent-card" }, ...parts);
  node.addEventListener("keydown", (e) => { // (the editor ignores keys typed here)
    if (e.key === "Escape") { e.preventDefault(); dismiss(); }
    else if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); accept(); }
    else if (e.key === "Enter" && e.target === box) { e.preventDefault(); again(box); }
    else return;
    e.stopPropagation();
  });
  if (v.kind === "note") requestAnimationFrame(() => box.isConnected && box.focus());
  return node;
}
// The two texts' word diff: the words in common as they are, the others in del and ins.
function wordDiff(a, b) {
  const words = (s) => s.match(/\s+|\S+/g) || [];
  const A = words(a), B = words(b), out = [];
  let at = 0;
  for (const [a0, a1, b0, b1] of lineHunks(A, B)) {
    if (a0 > at) out.push(A.slice(at, a0).join(""));
    if (a1 > a0) out.push(el("del", { textContent: A.slice(a0, a1).join("") }));
    if (b1 > b0) out.push(el("ins", { textContent: B.slice(b0, b1).join("") }));
    at = a1;
  }
  if (at < A.length) out.push(A.slice(at).join(""));
  return out;
}

registerCommands(() => (allowed() ? [
  { title: "Polish with the agent", sub: "Source: the selection, or the paragraph", icon: "edit", run: () => ask() },
  { title: "Polish with the agent, with a note…", sub: "Source", icon: "edit", run: askWithNote }] : []));
