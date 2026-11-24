// CodeMirror, for the source pane: loaded on first use, with the pane's theme, highlighting, keys, and completion of
// labels in \ref and keys in \cite.

import { onThemeChange, theme as currentTheme } from "./theme.js";

export let CM = null, cmLoading = null, cmExtensions = null; // CodeMirror, once loaded (loadEditor)

// (save and edited: what Cmd/Ctrl+S and every change to the text call, saveSource and sourceEdited)
export function loadEditor({ save, edited }) {
  cmLoading ??= (async () => {
    const [state, view, commands, language, search, autocomplete, { stex }, { tags: t }] = await Promise.all([
      import("@codemirror/state"), import("@codemirror/view"), import("@codemirror/commands"), import("@codemirror/language"),
      import("@codemirror/search"), import("@codemirror/autocomplete"), import("@codemirror/legacy-modes/mode/stex"),
      import("@lezer/highlight")]);
    const { EditorView } = view;
    // The look follows the viewer's theme, in the editors open now and in those made later (a file opened anew).
    const look = new state.Compartment(), lookOf = (dark) => editorLook(EditorView, language, t, dark);
    const shown = new Set(), views = view.ViewPlugin.define((v) => (shown.add(v), { destroy: () => shown.delete(v) }));
    let shownLook = look.of(lookOf(currentTheme === "dark"));
    let shownDark = currentTheme === "dark";
    onThemeChange(({ theme }) => {
      if ((theme === "dark") === shownDark) return; // (a tint or the PDF's colors: the look's CSS variables follow)
      shownDark = theme === "dark";
      const next = lookOf(shownDark), was = shownLook;
      shownLook = look.of(next);
      cmExtensions = cmExtensions.map((x) => (x === was ? shownLook : x));
      for (const v of shown) v.dispatch({ effects: look.reconfigure(next) });
    });
    // Lines someone else just changed, highlighted for a moment (flashLines sets them, or none).
    const flashLines = state.StateEffect.define();
    const flashed = state.StateField.define({
      create: () => view.Decoration.none,
      update: (lines, tr) => tr.effects.reduce((all, e) => (e.is(flashLines) ? e.value : all), lines.map(tr.changes)),
      provide: (field) => EditorView.decorations.from(field),
    });
    cmExtensions = [
      state.EditorState.lineSeparator.of("\n"), // (only \n ends a line, as on the server: a lone \r stays a character)
      view.lineNumbers(), view.highlightActiveLineGutter(), view.highlightSpecialChars(), view.drawSelection(),
      view.highlightActiveLine(), commands.history(), language.bracketMatching(), autocomplete.closeBrackets(),
      autocomplete.autocompletion({ override: [completeRefs], icons: false }),
      search.search({ top: true }), language.StreamLanguage.define(stex), shownLook, views,
      EditorView.lineWrapping, EditorView.contentAttributes.of({ spellcheck: "true" }), flashed,
      view.keymap.of([{ key: "Mod-s", run: () => { save(); return true; }, preventDefault: true },
                      ...autocomplete.closeBracketsKeymap, ...autocomplete.completionKeymap, ...commands.defaultKeymap,
                      ...search.searchKeymap, ...commands.historyKeymap, commands.indentWithTab]),
      EditorView.updateListener.of(edited),
    ];
    CM = { EditorState: state.EditorState, ChangeSet: state.ChangeSet, Transaction: state.Transaction, EditorView,
           Decoration: view.Decoration, flashLines, remoteLine: view.Decoration.line({ class: "cm-remote" }) };
  })().catch((err) => { cmLoading = null; throw err; });
  return cmLoading;
}

// The editor's look in the light or dark theme: its chrome in the theme's colors (viewer.css), and the highlighting of
// the token kinds of CodeMirror's stex mode.
function editorLook(EditorView, language, t, dark) {
  const blue = (alpha) => (dark ? `rgba(96, 165, 250, ${alpha + 0.1})` : `rgba(37, 99, 235, ${alpha})`);
  const ui = EditorView.theme({
    "&": { height: "100%", color: "var(--ink)", backgroundColor: "var(--bg-surface)", fontSize: "13.5px" },
    ".cm-scroller": { fontFamily: "var(--font-mono)", lineHeight: "1.65" },
    ".cm-content": { caretColor: "var(--ink)", padding: "8px 12px 40px 4px" },
    ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--accent)", borderLeftWidth: "2px" },
    "&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection":
      { backgroundColor: blue(0.18) },
    ".cm-gutters": { color: "var(--ink-3)", backgroundColor: "var(--bg-surface)", border: "none", padding: "0 4px" },
    ".cm-lineNumbers .cm-gutterElement": { minWidth: "34px", fontSize: "11.5px", paddingRight: "8px" },
    ".cm-activeLine": { backgroundColor: dark ? "rgba(255, 255, 255, 0.035)" : "rgba(24, 24, 27, 0.03)" },
    ".cm-activeLineGutter": { color: "var(--ink)", backgroundColor: "transparent", fontWeight: "600" },
    "&.cm-focused .cm-matchingBracket": { backgroundColor: blue(0.15), outline: "1px solid var(--accent)", borderRadius: "2px" },
    "&.cm-focused .cm-nonmatchingBracket": { backgroundColor: "var(--err-bg)", outline: "1px solid var(--err)" },
    ".cm-panels": { color: "var(--ink)", backgroundColor: "var(--bg-panel)" },
    ".cm-panels.cm-panels-top": { borderBottom: "1px solid var(--border)" },
    ".cm-search": { padding: "6px 10px", fontSize: "12.5px", fontFamily: "var(--font-ui)" },
    ".cm-textfield": { color: "var(--ink)", backgroundColor: "var(--bg-input)", border: "1px solid var(--border-strong)",
                       borderRadius: "5px", padding: "3px 7px" },
    ".cm-button": { color: "var(--ink)", backgroundImage: "none", backgroundColor: "var(--bg-surface-2)",
                    border: "1px solid var(--border)", borderRadius: "5px", padding: "3px 8px", cursor: "pointer" },
    ".cm-searchMatch": { backgroundColor: "var(--warn-bg)", outline: "1px solid var(--warn-border)", borderRadius: "2px" },
    ".cm-searchMatch.cm-searchMatch-selected": { backgroundColor: "rgba(217, 119, 6, 0.35)" },
    ".cm-tooltip": { color: "var(--ink)", backgroundColor: "var(--bg-surface)", border: "1px solid var(--border-strong)",
                     borderRadius: "8px", boxShadow: "var(--shadow-pop)", fontFamily: "var(--font-ui)", fontSize: "12.5px" },
    ".cm-tooltip.cm-tooltip-autocomplete > ul": { padding: "4px", maxHeight: "240px" },
    ".cm-tooltip.cm-tooltip-autocomplete > ul > li": { padding: "4px 8px", borderRadius: "5px" },
    ".cm-tooltip.cm-tooltip-autocomplete > ul > li[aria-selected]": { color: "var(--ink)", backgroundColor: "var(--accent-soft)" },
    ".cm-completionDetail": { color: "var(--ink-3)", marginLeft: "1em", fontSize: "11.5px" },
    ".cm-remote": { backgroundColor: "var(--accent-soft)" }, // (lines someone else just changed: flashLines)
  }, { dark });
  // [comment, \commands, $ \[ \( (math), environment names, labels and citation keys, numbers and strings, letters in
  // math, other names, brackets, errors]
  const palette = dark ? ["#7c8491", "#7dd3fc", "#fbbf24", "#c4b5fd", "#86efac", "#fcd34d", "#67e8f9", "#94a3b8", "#f87171"]
                       : ["#71717a", "#7c3f00", "#b45309", "#6d28d9", "#15803d", "#9a3412", "#0e7490", "#64748b", "#dc2626"];
  const kinds = [t.comment, t.tagName, t.keyword, t.atom, [t.number, t.string], t.special(t.variableName),
                 t.standard(t.variableName), t.bracket, t.invalid];
  const colors = language.HighlightStyle.define(kinds.map((tag, i) => ({ tag, color: palette[i], ...(i ? {} : { fontStyle: "italic" }) })));
  return [ui, language.syntaxHighlighting(colors)];
}

// Completion in the argument of \ref (and \eqref, \cref, \autoref…), from the labels of the paper's files and of the
// text in the pane, and of \cite (\citet, \citep…), from the keys of its .bib files (live.py /completions, asked again
// when what it said is more than 5 s old), each entry of a list of several completed on its own.
const REF_ARG = /\\(?!href)[a-zA-Z]*ref\*?\{([^{}]*)$/, CITE_ARG = /\\[a-zA-Z]*cite[a-zA-Z]*\*?(?:\[[^\]]*\]){0,2}\{([^{}]*)$/;
let known = null; // ({at, promise}: the last answer of /completions)
function completionData() {
  if (!known || performance.now() - known.at > 5000) {
    known = { at: performance.now(), promise: fetch("completions", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : { labels: [], keys: [] })).catch(() => ({ labels: [], keys: [] })) };
  }
  return known.promise;
}
async function completeRefs(context) {
  const line = context.state.doc.lineAt(context.pos);
  const before = line.text.slice(0, context.pos - line.from);
  const ref = before.match(REF_ARG), cite = !ref && before.match(CITE_ARG);
  if (!ref && !cite) return null;
  const typed = (ref || cite)[1].split(",").pop().trimStart();
  const data = await completionData();
  let options;
  if (cite) {
    options = data.keys.map((k) => ({ label: k.key, detail: k.title }));
  } else {
    const text = context.state.doc.toString().replace(/(?<!\\)%.*$/gm, ""); // (outside comments, as live.py's)
    const here = [...text.matchAll(/\\label\s*\{([^{}\n]+)\}/g)].map((m) => m[1]);
    options = [...new Set([...here, ...data.labels.map((l) => l.label)])].map((label) => ({ label }));
  }
  return { from: context.pos - typed.length, options, validFor: /^[^,{}\s]*$/ };
}
