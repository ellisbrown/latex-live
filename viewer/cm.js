// CodeMirror, for the source pane: loaded on first use, with the pane's theme, highlighting, keys, folding of sections
// and environments, completion (labels in \ref, keys in \cite, figures in \includegraphics, environments in \begin,
// and common commands), a card for the reference, citation or figure under the pointer, and the notes marked in the
// text. Other parts of the viewer add extensions of their own (addEditorExtension).

import { onThemeChange, theme as currentTheme } from "./theme.js";

export let CM = null, cmLoading = null, cmExtensions = null; // CodeMirror, once loaded (loadEditor)

// The editors on screen (each file opened makes a state of cmExtensions; the view stays), which a change of the theme
// or of the added extensions reconfigures in place.
const shown = new Set();
let parts = null; // ({look, added}: the compartments that hold those, once loaded)

// Extensions that other parts of the viewer add (e.g. marks of their own in the text, fed through a state effect of
// theirs): make, called once with CodeMirror's modules ({state, view, language, …}) when they are loaded, returns
// them. They go in the editors made later and in those on screen.
const added = []; // [{make, ext}]
let modules = null;
export function addEditorExtension(make) {
  added.push({ make, ext: modules ? make(modules) : null });
  if (parts) setPart("added", added.map((a) => a.ext));
}
function setPart(name, ext) {
  const was = cmExtensions.find((x) => x.compartment === parts[name]);
  const next = parts[name].of(ext);
  cmExtensions = cmExtensions.map((x) => (x === was ? next : x));
  for (const v of shown) v.dispatch({ effects: parts[name].reconfigure(ext) });
}

// (save, edited and open: what Cmd/Ctrl+S, every change to the text, and a card's link call: saveSource, sourceEdited
// and openFile)
export function loadEditor({ save, edited, open }) {
  cmLoading ??= (async () => {
    const [state, view, commands, language, search, autocomplete, { stex }, { tags: t }] = await Promise.all([
      import("@codemirror/state"), import("@codemirror/view"), import("@codemirror/commands"), import("@codemirror/language"),
      import("@codemirror/search"), import("@codemirror/autocomplete"), import("@codemirror/legacy-modes/mode/stex"),
      import("@lezer/highlight")]);
    const { EditorView } = view;
    modules = { state, view, commands, language, search, autocomplete };
    for (const a of added) a.ext ??= a.make(modules);
    // The look follows the viewer's theme, in the editors open now and in those made later (a file opened anew).
    parts = { look: new state.Compartment(), added: new state.Compartment() };
    const lookOf = (dark) => editorLook(EditorView, language, t, dark);
    let shownDark = currentTheme === "dark";
    onThemeChange(({ theme }) => {
      if ((theme === "dark") === shownDark) return; // (a tint or the PDF's colors: the look's CSS variables follow)
      shownDark = theme === "dark";
      setPart("look", lookOf(shownDark));
    });
    const views = view.ViewPlugin.define((v) => (shown.add(v), { destroy: () => shown.delete(v) }));
    // Lines someone else just changed, highlighted for a moment (flashLines sets them, or none).
    const flashLines = state.StateEffect.define();
    const flashed = state.StateField.define({
      create: () => view.Decoration.none,
      update: (lines, tr) => tr.effects.reduce((all, e) => (e.is(flashLines) ? e.value : all), lines.map(tr.changes)),
      provide: (field) => EditorView.decorations.from(field),
    });
    const wrap = (before) => (v) => wrapSelection(v, state, before, "}");
    cmExtensions = [
      state.EditorState.lineSeparator.of("\n"), // (only \n ends a line, as on the server: a lone \r stays a character)
      view.lineNumbers(), language.codeFolding(), language.foldGutter(), language.foldService.of(latexFold),
      view.highlightActiveLineGutter(), view.highlightSpecialChars(), view.drawSelection(),
      view.highlightActiveLine(), commands.history(), language.bracketMatching(), autocomplete.closeBrackets(),
      autocomplete.autocompletion({ override: [(context) => completeLatex(context, autocomplete)], icons: false }),
      search.search({ top: true }), search.highlightSelectionMatches(), language.StreamLanguage.define(stex), views,
      EditorView.lineWrapping, EditorView.contentAttributes.of({ spellcheck: "true" }), flashed,
      noteMarks(state, view), hoverCards(view, open),
      view.keymap.of([{ key: "Mod-s", run: () => { save(); return true; }, preventDefault: true },
                      { key: "Mod-b", run: wrap("\\textbf{"), preventDefault: true },
                      { key: "Mod-i", run: wrap("\\textit{"), preventDefault: true },
                      ...autocomplete.closeBracketsKeymap, ...autocomplete.completionKeymap, ...language.foldKeymap,
                      ...commands.defaultKeymap, ...search.searchKeymap, ...commands.historyKeymap, commands.indentWithTab]),
      EditorView.updateListener.of(edited),
      parts.look.of(lookOf(shownDark)), parts.added.of(added.map((a) => a.ext)),
    ];
    CM = { EditorState: state.EditorState, ChangeSet: state.ChangeSet, Transaction: state.Transaction, EditorView,
           Decoration: view.Decoration, flashLines, remoteLine: view.Decoration.line({ class: "cm-remote" }),
           foldAll: language.foldAll, unfoldAll: language.unfoldAll, foldedRanges: language.foldedRanges };
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
    ".cm-foldGutter .cm-gutterElement": { width: "14px", cursor: "pointer", opacity: "0.55", fontSize: "11px" },
    ".cm-foldGutter .cm-gutterElement:hover": { opacity: "1", color: "var(--ink)" },
    ".cm-foldPlaceholder": { color: "var(--ink-3)", backgroundColor: "var(--bg-surface-2)", border: "1px solid var(--border)",
                             borderRadius: "4px", padding: "0 5px", margin: "0 4px" },
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

// Cmd/Ctrl+B and +I: the selection (each, with several) in \textbf{…} or \textit{…}, still selected; with none, the
// command with the cursor in its braces.
function wrapSelection(view, state, before, after) {
  view.dispatch(view.state.changeByRange((range) => ({
    changes: [{ from: range.from, insert: before }, { from: range.to, insert: after }],
    range: state.EditorSelection.range(range.from + before.length, range.to + before.length),
  })));
  return true;
}

// Folding: a heading folds up to the next heading of its level or above (or \end{document}), and \begin{env} up to its
// own \end{env} (the next one at the same depth, past those of the same environment inside it), leaving the \end line
// shown. Comments don't count.
const SECTION_LEVELS = { part: 0, chapter: 1, section: 2, subsection: 3, subsubsection: 4, paragraph: 5 };
const SECTION_RE = /^\s*\\(part|chapter|section|subsection|subsubsection|paragraph)\*?(?:\[[^\]]*\])?\{/;
const BEGIN_RE = /\\begin\{([^{}]+)\}/;
const uncommented = (text) => text.replace(/(?<!\\)%.*$/, "");
function latexFold(state, lineStart, lineEnd) {
  const doc = state.doc, line = doc.lineAt(lineStart), text = uncommented(line.text);
  const heading = text.match(SECTION_RE);
  if (heading) {
    const level = SECTION_LEVELS[heading[1]];
    let last = line.number;
    for (let n = line.number + 1; n <= doc.lines; n++) {
      const next = uncommented(doc.line(n).text);
      const m = next.match(SECTION_RE);
      if ((m && SECTION_LEVELS[m[1]] <= level) || /^\s*\\end\{document\}/.test(next)) break;
      last = n;
    }
    while (last > line.number && !doc.line(last).text.trim()) last--; // (the blank lines before the next heading stay)
    if (last > line.number) return { from: lineEnd, to: doc.line(last).to };
  }
  const begin = text.match(BEGIN_RE);
  if (!begin || begin[1] === "document") return null;
  const name = begin[1].replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const token = new RegExp(`\\\\(begin|end)\\{${name}\\}`, "g");
  let depth = 0;
  for (let n = line.number; n <= doc.lines; n++) {
    const here = n === line.number ? text.slice(begin.index) : uncommented(doc.line(n).text);
    for (const m of here.matchAll(token)) {
      depth += m[1] === "begin" ? 1 : -1;
      if (depth) continue;
      if (n === line.number) return null; // (it ends on its own line)
      const end = doc.line(n), at = end.from + m.index;
      const to = end.text.slice(0, m.index).trim() ? at : doc.line(n - 1).to; // (\end alone on its line stays a line)
      return to > lineEnd ? { from: lineEnd, to } : null;
    }
  }
  return null;
}

// The source's headings (\section, \subsection, \subsubsection outside comments), for the pane's outline rail:
// [{line, level (1–3), title}].
export function sectionHeadings(doc) {
  const found = [], re = /^\s*\\(section|subsection|subsubsection)\*?(?:\[[^\]]*\])?\{([^{}\n]*(?:\{[^{}\n]*\}[^{}\n]*)*)\}/;
  for (let n = 1; n <= doc.lines; n++) {
    const m = uncommented(doc.line(n).text).match(re);
    if (m) found.push({ line: n, level: ["section", "subsection", "subsubsection"].indexOf(m[1]) + 1,
                        title: m[2].replace(/\\[a-zA-Z]+\*?\s*/g, "").replace(/[{}]/g, "").trim() || "(untitled)" });
  }
  return found;
}

// Fold every heading and environment, or unfold them all if any is folded.
export function toggleFoldAll(view) {
  if (!view || !CM) return;
  if (CM.foldedRanges(view.state).size) CM.unfoldAll(view);
  else CM.foldAll(view);
  view.focus();
}

// The notes in the text: each use of a note macro (setNoteMacros, e.g. \todo{…} or an author's \eb{…}), its
// argument included, tinted in its author's color, outside comments. Only the lines on screen are looked at.
let noteMacros = { re: null, colors: new Map(), key: "" };
let noteMacrosSet = null; // (the state effect that tells the editors on screen)
export function setNoteMacros(list) { // ([{macro, author, color}])
  const key = JSON.stringify(list.map((n) => [n.macro, n.color]).sort());
  if (key === noteMacros.key) return;
  const names = list.map((n) => n.macro.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  noteMacros = { key, re: names.length ? new RegExp(`\\\\(${names.join("|")})\\s*\\{`, "g") : null,
                 colors: new Map(list.map((n) => [n.macro, n])) };
  for (const v of shown) v.dispatch({ effects: noteMacrosSet.of(null) });
}
function noteMarks(state, view) {
  noteMacrosSet = state.StateEffect.define();
  const marks = (v) => {
    const ranges = [];
    if (!noteMacros.re) return view.Decoration.none;
    for (const { from, to } of v.visibleRanges) {
      const start = v.state.doc.lineAt(Math.max(0, from - 2000)).from, text = v.state.sliceDoc(start, Math.min(v.state.doc.length, to + 2000));
      for (const m of text.matchAll(noteMacros.re)) {
        const at = start + m.index;
        if (at > to || ranges.some((r) => at < r.to)) continue;
        const line = v.state.doc.lineAt(at);
        if (/(?<!\\)%/.test(line.text.slice(0, at - line.from))) continue; // (commented out: a resolved note)
        const end = closingBrace(text, m.index + m[0].length);
        if (end < 0 || start + end < from) continue;
        const note = noteMacros.colors.get(m[1]);
        ranges.push(view.Decoration.mark({ class: "cm-note-span", attributes: { style: `--note-col: ${note.color}`,
          title: `${note.author}: ${text.slice(m.index + m[0].length, end - 1).replace(/\s+/g, " ").slice(0, 300)}` } })
          .range(at, start + end));
      }
    }
    return view.Decoration.set(ranges, true);
  };
  return view.ViewPlugin.fromClass(class {
    constructor(v) { this.decorations = marks(v); }
    update(u) {
      if (u.docChanged || u.viewportChanged || u.transactions.some((tr) => tr.effects.some((e) => e.is(noteMacrosSet)))) {
        this.decorations = marks(u.view);
      }
    }
  }, { decorations: (p) => p.decorations });
}
// The end of a brace group whose { is just before from (the offset after its }), or -1 within 2000 characters.
function closingBrace(text, from) {
  let depth = 1;
  for (let i = from; i < text.length && i < from + 2000; i++) {
    if (text[i] === "\\") i++;
    else if (text[i] === "{") depth++;
    else if (text[i] === "}" && !--depth) return i + 1;
  }
  return -1;
}

// What the source pane completes and shows in its cards: live.py /completions ({labels, labelContext, keys, figures}),
// asked again when what it said is more than 5 s old.
let known = null; // ({at, promise}: the last answer)
export function completionData() {
  if (!known || performance.now() - known.at > 5000) {
    const none = { labels: [], labelContext: {}, keys: [], figures: [] };
    known = { at: performance.now(), promise: fetch("completions", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : none)).then((d) => ({ ...none, ...d })).catch(() => none) };
  }
  return known.promise;
}

// Completion in the argument of \ref (and \eqref, \cref, \autoref…), from the labels of the paper's files and of the
// text in the pane; of \cite (\citet, \citep…), from the keys of its .bib files, each entry of a list of several
// completed on its own; of \includegraphics, from the paper's figures; of \begin, from common environments (with
// their \end); and of a command just begun with \, from common ones (as snippets: Tab goes to the next field).
const REF_ARG = /\\(?!href)[a-zA-Z]*ref\*?\{([^{}]*)$/, CITE_ARG = /\\[a-zA-Z]*cite[a-zA-Z]*\*?(?:\[[^\]]*\]){0,2}\{([^{}]*)$/;
const GRAPHICS_ARG = /\\includegraphics\*?(?:\[[^\]]*\])?\{([^{}]*)$/, BEGIN_ARG = /\\begin\{([a-zA-Z*]*)$/, COMMAND = /\\[a-zA-Z]*$/;
const ENVIRONMENTS = ["figure", "figure*", "table", "table*", "equation", "equation*", "align", "align*", "itemize",
                      "enumerate", "description", "abstract", "theorem", "lemma", "proposition", "proof", "corollary",
                      "definition", "remark", "quote", "center", "minipage", "tabular", "subfigure", "algorithm",
                      "algorithmic", "verbatim", "lstlisting", "cases"];
const COMMANDS = [
  ["\\section", "\\section{${Title}}", "heading"], ["\\subsection", "\\subsection{${Title}}", "heading"],
  ["\\subsubsection", "\\subsubsection{${Title}}", "heading"], ["\\paragraph", "\\paragraph{${Heading}} ", "run-in heading"],
  ["\\textbf", "\\textbf{${}}", "bold"], ["\\textit", "\\textit{${}}", "italic"], ["\\emph", "\\emph{${}}", "emphasis"],
  ["\\texttt", "\\texttt{${}}", "monospace"], ["\\cite", "\\cite{${}}", "citation"], ["\\citep", "\\citep{${}}", "citation"],
  ["\\citet", "\\citet{${}}", "citation"], ["\\ref", "\\ref{${}}", "reference"], ["\\eqref", "\\eqref{${}}", "reference"],
  ["\\cref", "\\cref{${}}", "reference"], ["\\label", "\\label{${}}", "label"],
  ["\\includegraphics", "\\includegraphics[width=${\\linewidth}]{${}}", "figure"], ["\\caption", "\\caption{${}}", "caption"],
  ["\\centering", "\\centering", ""], ["\\footnote", "\\footnote{${}}", "footnote"], ["\\input", "\\input{${}}", "file"],
  ["\\frac", "\\frac{${}}{${}}", "math"], ["\\mathbb", "\\mathbb{${}}", "math"], ["\\mathcal", "\\mathcal{${}}", "math"],
  ["\\usepackage", "\\usepackage{${}}", "package"], ["\\begin", "\\begin{${env}}\n\t${}\n\\end{${env}}", "environment"],
];
async function completeLatex(context, autocomplete) {
  const line = context.state.doc.lineAt(context.pos);
  const before = line.text.slice(0, context.pos - line.from);
  const ref = before.match(REF_ARG), cite = !ref && before.match(CITE_ARG);
  if (ref || cite) {
    const typed = (ref || cite)[1].split(",").pop().trimStart();
    const data = await completionData();
    let options;
    if (cite) {
      options = data.keys.map((k) => ({ label: k.key, detail: k.title }));
    } else {
      const text = context.state.doc.toString().replace(/(?<!\\)%.*$/gm, ""); // (outside comments, as live.py's)
      const here = [...text.matchAll(/\\label\s*\{([^{}\n]+)\}/g)].map((m) => m[1]);
      options = [...new Set([...here, ...data.labels.map((l) => l.label)])]
        .map((label) => ({ label, detail: data.labelContext[label]?.slice(0, 60) || "" }));
    }
    return { from: context.pos - typed.length, options, validFor: /^[^,{}\s]*$/ };
  }
  const graphics = before.match(GRAPHICS_ARG);
  if (graphics) {
    const data = await completionData();
    return { from: context.pos - graphics[1].length, options: data.figures.map((path) => ({ label: path })), validFor: /^[^{}\s]*$/ };
  }
  const begin = before.match(BEGIN_ARG);
  if (begin) { // (the snippet takes the } that closeBrackets typed after the cursor, if it is there)
    const options = ENVIRONMENTS.map((env) => {
      const insert = autocomplete.snippet(`${env}}\n\t\${}\n\\end{${env}}`);
      return { label: env, apply: (view, c, from, to) => insert(view, c, from, view.state.sliceDoc(to, to + 1) === "}" ? to + 1 : to) };
    });
    return { from: context.pos - begin[1].length, options, validFor: /^[a-zA-Z*]*$/ };
  }
  const command = before.match(COMMAND);
  if (command && (command[0].length > 1 || context.explicit)) {
    const options = COMMANDS.map(([label, template, detail]) => autocomplete.snippetCompletion(template, { label, detail }));
    return { from: context.pos - command[0].length, options, validFor: /^\\[a-zA-Z]*$/ };
  }
  return null;
}

// The card for what the pointer rests on: a citation key (its title, and a link to its entry), a reference (where its
// label is, the line around it, and a link there), or a figure (its file). open(path, line) follows a link.
function hoverCards(view, open) {
  const card = (kind, ...rows) => {
    const dom = document.createElement("div");
    dom.className = "cm-hover-card";
    for (const [cls, text, onclick] of [["hc-kind", kind], ...rows.filter(Boolean)]) {
      if (!text) continue;
      const row = document.createElement(onclick ? "button" : "div");
      row.className = cls;
      row.textContent = text;
      if (onclick) row.onclick = onclick;
      dom.append(row);
    }
    return { dom };
  };
  const at = (m, col) => col >= m.index && col <= m.index + m[0].length;
  return view.hoverTooltip(async (v, pos) => {
    const line = v.state.doc.lineAt(pos), col = pos - line.from, text = line.text;
    for (const m of text.matchAll(/\\[a-zA-Z]*cite[a-zA-Z]*\*?(?:\[[^\]]*\]){0,2}\{([^{}]*)\}/g)) {
      if (!at(m, col)) continue;
      let from = m.index + m[0].length - 1 - m[1].length; // (the keys' start, after the {)
      for (const part of m[1].split(",")) {
        const key = part.trim(), start = from + part.indexOf(key);
        from += part.length + 1;
        if (!key || col < start || col > start + key.length) continue;
        const entry = (await completionData()).keys.find((k) => k.key === key);
        return { pos: line.from + start, end: line.from + start + key.length, above: true,
                 create: () => card(`Citation · ${key}`, ["hc-title", entry ? entry.title || "(no title)" : "Not in the paper's .bib files"],
                                    entry && ["hc-link", `Open ${entry.file}:${entry.line}`, () => open(entry.file, entry.line)]) };
      }
      return null;
    }
    for (const m of text.matchAll(/\\(?!href)[a-zA-Z]*ref\*?\{([^{}]*)\}/g)) {
      if (!at(m, col)) continue;
      const label = m[1].split(",")[0].trim(), data = await completionData();
      const target = data.labels.find((l) => l.label === label);
      return { pos: line.from + m.index, end: line.from + m.index + m[0].length, above: true,
               create: () => (target ? card(`Reference · ${target.file}:${target.line}`, ["hc-code", data.labelContext[label]],
                                            ["hc-link", "Go to the label", () => open(target.file, target.line)])
                                     : card(`Reference · ${label}`, ["hc-meta", "No such label in the paper's files"])) };
    }
    for (const m of text.matchAll(/\\includegraphics\*?(?:\[[^\]]*\])?\{([^{}]*)\}/g)) {
      if (!at(m, col)) continue;
      const name = m[1].trim(), figures = (await completionData()).figures;
      const found = figures.find((f) => f === name) || figures.find((f) => f.replace(/\.[^./]+$/, "") === name);
      return { pos: line.from + m.index, end: line.from + m.index + m[0].length, above: true,
               create: () => card(`Figure · ${found || name}`, ["hc-meta", found ? "" : "No such figure in the paper"]) };
    }
    return null;
  }, { hoverTime: 300 });
}
