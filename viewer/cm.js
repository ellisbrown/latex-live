// CodeMirror, for the source pane: loaded on first use, with the pane's theme, highlighting, keys, and completion of
// labels in \ref and keys in \cite.

export let CM = null, cmLoading = null, cmExtensions = null; // CodeMirror, once loaded (loadEditor)

// (save and edited: what Cmd/Ctrl+S and every change to the text call, saveSource and sourceEdited)
export function loadEditor({ save, edited }) {
  cmLoading ??= (async () => {
    const [state, view, commands, language, search, autocomplete, { stex }, { tags: t }] = await Promise.all([
      import("@codemirror/state"), import("@codemirror/view"), import("@codemirror/commands"), import("@codemirror/language"),
      import("@codemirror/search"), import("@codemirror/autocomplete"), import("@codemirror/legacy-modes/mode/stex"),
      import("@lezer/highlight")]);
    const { EditorView } = view;
    const theme = EditorView.theme({ // (dark, as the rest of the chrome: panels #2a2d2f, bars #323639)
      "&": { height: "100%", color: "#ddd", backgroundColor: "#2a2d2f", fontSize: "12.5px" },
      ".cm-scroller": { fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace", lineHeight: "1.5" },
      ".cm-content": { caretColor: "#fff" },
      ".cm-cursor, .cm-dropCursor": { borderLeftColor: "#fff" },
      "&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection":
        { backgroundColor: "#2f5f8f99" },
      ".cm-gutters": { color: "#6e7478", backgroundColor: "#26292b", border: "none", borderRight: "1px solid #33373a" },
      ".cm-activeLine": { backgroundColor: "#ffffff0a" },
      ".cm-activeLineGutter": { color: "#bbb", backgroundColor: "#ffffff0d" },
      "&.cm-focused .cm-matchingBracket": { backgroundColor: "#4a4f52", outline: "1px solid #888" },
      "&.cm-focused .cm-nonmatchingBracket": { backgroundColor: "#6a2424" },
      ".cm-panels": { color: "#eee", backgroundColor: "#323639" },
      ".cm-panels.cm-panels-top": { borderBottom: "1px solid #1d1f20" },
      ".cm-textfield": { color: "#eee", backgroundColor: "#202124", border: "1px solid #555", borderRadius: "3px" },
      ".cm-button": { color: "#eee", backgroundImage: "none", backgroundColor: "#4a4f52", border: "0", borderRadius: "3px" },
      ".cm-searchMatch": { backgroundColor: "#e0a04033", outline: "1px solid #e0a04099" },
      ".cm-searchMatch.cm-searchMatch-selected": { backgroundColor: "#e0a04080" },
      ".cm-tooltip": { color: "#ddd", backgroundColor: "#2a2d2f", border: "1px solid #444" },
      ".cm-tooltip.cm-tooltip-autocomplete > ul > li[aria-selected]": { color: "#fff", backgroundColor: "#2f5f8f" },
      ".cm-completionDetail": { color: "#9aa3a7", marginLeft: "1em" },
      ".cm-remote": { backgroundColor: "#2f6fb033" }, // (lines someone else just changed: flashLines)
    }, { dark: true });
    const colors = language.HighlightStyle.define([ // (the token kinds of CodeMirror's stex mode)
      { tag: t.comment, color: "#9aa3a7", fontStyle: "italic" },
      { tag: t.tagName, color: "#79b8ff" },                 // \commands
      { tag: t.keyword, color: "#f0a35e" },                 // $, \[, \( (math)
      { tag: t.atom, color: "#c5a5ff" },                    // environment names, labels, citation keys
      { tag: [t.number, t.string], color: "#b5cea8" },
      { tag: t.special(t.variableName), color: "#e5c07b" }, // letters in math
      { tag: t.standard(t.variableName), color: "#56b6c2" },
      { tag: t.bracket, color: "#9aa0a6" },
      { tag: t.invalid, color: "#ff7b72" },
    ]);
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
      search.search({ top: true }), language.StreamLanguage.define(stex), language.syntaxHighlighting(colors),
      EditorView.lineWrapping, EditorView.contentAttributes.of({ spellcheck: "true" }), theme, flashed,
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
    const here = [...context.state.doc.toString().matchAll(/\\label\s*\{([^{}\n]+)\}/g)].map((m) => m[1]);
    options = [...new Set([...here, ...data.labels.map((l) => l.label)])].map((label) => ({ label }));
  }
  return { from: context.pos - typed.length, options, validFor: /^[^,{}\s]*$/ };
}
