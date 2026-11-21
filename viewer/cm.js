// CodeMirror 6 for the source pane: dynamic Light/Dark themes & editorial syntax highlighting,
// LaTeX section & environment folding, rich completions (\ref, \cite, \includegraphics, \begin, commands),
// hover cards (\cite, \ref, \includegraphics, diagnostics), inline diagnostic squiggles & gutter dots,
// in-editor highlighted note spans, remote collaborator badges, and Lattice-style inline AI diff cards.

import { onThemeChange, theme as currentTheme } from "./theme.js";
import { lineHunks } from "./diff.js";

export let CM = null, cmLoading = null, cmExtensions = null;

let themeComp = null;
let openLinkFn = null; // (path, line) => void, set by editor.js
let papersByKey = new Map(); // populated by papers.js / completions for rich \cite hover cards
let activeDiagList = []; // diagnostics currently shown in open file (used by hoverTooltip)
let activeViews = new Set(); // live EditorView instances to reconfigure on theme switch

export function setOpenHandler(fn) { openLinkFn = fn; }
export function setPapersCache(entries) {
  papersByKey = new Map();
  for (const e of entries || []) if (e?.key) papersByKey.set(e.key, e);
}

// Common LaTeX environments & commands for autocomplete.
const ENVS = [
  "figure", "figure*", "table", "table*", "equation", "equation*", "align", "align*",
  "itemize", "enumerate", "description", "abstract", "theorem", "lemma", "proposition",
  "proof", "corollary", "remark", "quote", "center", "minipage", "tabular", "subtable",
  "subfigure", "algorithm", "algorithmic", "verbatim", "lstlisting", "frame", "cases",
];

const COMMANDS = [
  { label: "\\section", insert: "\\section{${1:Title}}\n\\label{sec:${2:label}}", detail: "Section heading" },
  { label: "\\subsection", insert: "\\subsection{${1:Title}}\n\\label{sec:${2:label}}", detail: "Subsection heading" },
  { label: "\\subsubsection", insert: "\\subsubsection{${1:Title}}", detail: "Subsubsection heading" },
  { label: "\\paragraph", insert: "\\paragraph{${1:Heading}.} ", detail: "Run-in paragraph" },
  { label: "\\textbf", insert: "\\textbf{${1:text}}", detail: "Bold text" },
  { label: "\\textit", insert: "\\textit{${1:text}}", detail: "Italic text" },
  { label: "\\emph", insert: "\\emph{${1:text}}", detail: "Emphasized text" },
  { label: "\\texttt", insert: "\\texttt{${1:code}}", detail: "Monospace text" },
  { label: "\\cite", insert: "\\cite{${1}}", detail: "Citation" },
  { label: "\\citep", insert: "\\citep{${1}}", detail: "Parenthetical citation" },
  { label: "\\citet", insert: "\\citet{${1}}", detail: "Textual citation" },
  { label: "\\ref", insert: "\\ref{${1}}", detail: "Cross-reference" },
  { label: "\\eqref", insert: "\\eqref{${1}}", detail: "Equation reference" },
  { label: "\\cref", insert: "\\cref{${1}}", detail: "Cleveref reference" },
  { label: "\\label", insert: "\\label{${1}}", detail: "Define label" },
  { label: "\\includegraphics", insert: "\\includegraphics[width=${1:\\linewidth}]{${2}}", detail: "Insert figure" },
  { label: "\\caption", insert: "\\caption{${1:Caption.}}", detail: "Float caption" },
  { label: "\\centering", insert: "\\centering", detail: "Center float" },
  { label: "\\footnote", insert: "\\footnote{${1}}", detail: "Footnote" },
  { label: "\\input", insert: "\\input{${1}}", detail: "Include .tex file" },
  { label: "\\frac", insert: "\\frac{${1:num}}{${2:den}}", detail: "Fraction" },
  { label: "\\sum", insert: "\\sum_{${1:i=1}}^{${2:n}}", detail: "Summation" },
  { label: "\\mathbb", insert: "\\mathbb{${1:R}}", detail: "Blackboard bold" },
  { label: "\\mathcal", insert: "\\mathcal{${1:L}}", detail: "Calligraphic math" },
  { label: "\\newcommand", insert: "\\newcommand{\\${1:cmd}}[${2:1}]{${3}}", detail: "Define macro" },
  { label: "\\usepackage", insert: "\\usepackage{${1}}", detail: "Load package" },
  { label: "\\begin", insert: "\\begin{${1:env}}\n  ${2}\n\\end{${1:env}}", detail: "Environment block" },
];

function buildThemeBundle(EditorView, language, t, dark) {
  const ui = EditorView.theme({
    "&": {
      height: "100%",
      color: "var(--ink)",
      backgroundColor: "var(--bg-surface)",
      fontSize: "13.5px",
    },
    ".cm-scroller": {
      fontFamily: "var(--font-mono)",
      lineHeight: "1.65",
      padding: "6px 0",
    },
    ".cm-content": { caretColor: "var(--ink)", padding: "4px 12px 40px 4px" },
    ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--accent)", borderLeftWidth: "2px" },
    "&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection":
      { backgroundColor: dark ? "rgba(96, 165, 250, 0.28)" : "rgba(37, 99, 235, 0.18)" },
    ".cm-gutters": {
      color: "var(--ink-3)",
      backgroundColor: "var(--bg-surface)",
      border: "none",
      paddingLeft: "4px",
      paddingRight: "4px",
    },
    ".cm-lineNumbers .cm-gutterElement": { minWidth: "34px", fontSize: "11.5px", paddingRight: "8px" },
    ".cm-foldGutter .cm-gutterElement": { width: "14px", cursor: "pointer", opacity: "0.55", fontSize: "11px" },
    ".cm-foldGutter .cm-gutterElement:hover": { opacity: "1", color: "var(--ink)" },
    ".cm-activeLine": { backgroundColor: dark ? "rgba(255, 255, 255, 0.035)" : "rgba(24, 24, 27, 0.03)" },
    ".cm-activeLineGutter": { color: "var(--ink)", backgroundColor: "transparent", fontWeight: "600" },
    "&.cm-focused .cm-matchingBracket": {
      backgroundColor: dark ? "rgba(96, 165, 250, 0.24)" : "rgba(37, 99, 235, 0.15)",
      outline: "1px solid var(--accent)",
      borderRadius: "2px",
    },
    "&.cm-focused .cm-nonmatchingBracket": { backgroundColor: "var(--err-bg)", outline: "1px solid var(--err)" },
    ".cm-panels": { color: "var(--ink)", backgroundColor: "var(--bg-panel)" },
    ".cm-panels.cm-panels-top": { borderBottom: "1px solid var(--border)" },
    ".cm-search": { padding: "6px 10px", fontSize: "12.5px", fontFamily: "var(--font-ui)" },
    ".cm-textfield": {
      color: "var(--ink)", backgroundColor: "var(--bg-input)",
      border: "1px solid var(--border-strong)", borderRadius: "5px", padding: "3px 7px",
    },
    ".cm-button": {
      color: "var(--ink)", backgroundImage: "none", backgroundColor: "var(--bg-surface-2)",
      border: "1px solid var(--border)", borderRadius: "5px", padding: "3px 8px", cursor: "pointer",
    },
    ".cm-searchMatch": { backgroundColor: "var(--warn-bg)", outline: "1px solid var(--warn-border)", borderRadius: "2px" },
    ".cm-searchMatch.cm-searchMatch-selected": { backgroundColor: "rgba(217, 119, 6, 0.35)" },
    ".cm-tooltip": {
      color: "var(--ink)", backgroundColor: "var(--bg-surface)",
      border: "1px solid var(--border-strong)", borderRadius: "8px",
      boxShadow: "var(--shadow-pop)", fontFamily: "var(--font-ui)", fontSize: "12.5px",
    },
    ".cm-tooltip.cm-tooltip-autocomplete > ul": { padding: "4px", maxHeight: "240px" },
    ".cm-tooltip.cm-tooltip-autocomplete > ul > li": { padding: "4px 8px", borderRadius: "5px" },
    ".cm-tooltip.cm-tooltip-autocomplete > ul > li[aria-selected]": {
      color: "var(--ink)", backgroundColor: "var(--accent-soft)",
    },
    ".cm-completionDetail": { color: "var(--ink-3)", marginLeft: "1em", fontSize: "11.5px" },
    ".cm-remote": { backgroundColor: "var(--accent-soft)" },
    ".cm-foldPlaceholder": {
      backgroundColor: "var(--bg-surface-2)", border: "1px solid var(--border)",
      color: "var(--ink-3)", borderRadius: "4px", padding: "0 5px", margin: "0 4px",
    },
  }, { dark });

  const colors = language.HighlightStyle.define(dark ? [
    { tag: t.comment, color: "#7c8491", fontStyle: "italic" },
    { tag: t.tagName, color: "#7dd3fc" },
    { tag: t.keyword, color: "#fbbf24" },
    { tag: t.atom, color: "#c4b5fd" },
    { tag: [t.number, t.string], color: "#86efac" },
    { tag: t.special(t.variableName), color: "#fcd34d" },
    { tag: t.standard(t.variableName), color: "#67e8f9" },
    { tag: t.bracket, color: "#94a3b8" },
    { tag: t.invalid, color: "#f87171" },
  ] : [
    { tag: t.comment, color: "#71717a", fontStyle: "italic" },
    { tag: t.tagName, color: "#7c3f00" },                 // warm editorial command brown/indigo
    { tag: t.keyword, color: "#b45309" },                 // math delimiters
    { tag: t.atom, color: "#6d28d9" },                    // environments, labels, cite keys
    { tag: [t.number, t.string], color: "#15803d" },
    { tag: t.special(t.variableName), color: "#9a3412" }, // math vars
    { tag: t.standard(t.variableName), color: "#0e7490" },
    { tag: t.bracket, color: "#64748b" },
    { tag: t.invalid, color: "#dc2626" },
  ]);
  return [ui, language.syntaxHighlighting(colors)];
}

// Section & \begin{...}...\end{...} fold service for LaTeX.
const SEC_LEVEL = { part: 0, chapter: 1, section: 2, subsection: 3, subsubsection: 4, paragraph: 5 };
const SEC_RE = /^\\(part|chapter|section|subsection|subsubsection|paragraph)\*?(?:\[[^\]]*\])?\{/;
const BEGIN_RE = /\\begin\{([^}]+)\}/;

function latexFold(state, lineStart, lineEnd) {
  const doc = state.doc;
  const line = doc.lineAt(lineStart);
  const text = line.text.trimStart();
  const sm = text.match(SEC_RE);
  if (sm) {
    const level = SEC_LEVEL[sm[1]];
    let endLine = line.number;
    for (let n = line.number + 1; n <= doc.lines; n++) {
      const t = doc.line(n).text.trimStart();
      if (/^\\end\{document\}/.test(t)) break;
      const nm = t.match(SEC_RE);
      if (nm && SEC_LEVEL[nm[1]] <= level) break;
      endLine = n;
    }
    while (endLine > line.number && !doc.line(endLine).text.trim()) endLine--;
    if (endLine > line.number) return { from: lineEnd, to: doc.line(endLine).to };
  }
  const bm = line.text.match(BEGIN_RE);
  if (bm && bm[1] !== "document") {
    const env = bm[1];
    let depth = 1;
    for (let n = line.number + 1; n <= doc.lines; n++) {
      const t = doc.line(n).text;
      if (t.includes(`\\begin{${env}}`)) depth++;
      if (t.includes(`\\end{${env}}`)) depth--;
      if (depth === 0) return { from: lineEnd, to: doc.line(n).to };
    }
  }
  return null;
}

// Extract section headings from a CodeMirror doc for the right-edge Section Outline Rail.
export function extractSections(doc) {
  const out = [];
  if (!doc) return out;
  const re = /^\s*\\(section|subsection|subsubsection)\*?(?:\[[^\]]*\])?\{([^{}\n]+)\}/;
  for (let n = 1; n <= doc.lines; n++) {
    const m = doc.line(n).text.match(re);
    if (m) {
      const level = m[1] === "section" ? 1 : m[1] === "subsection" ? 2 : 3;
      out.push({ line: n, level, title: m[2].replace(/\\[a-zA-Z]+\s*/g, "").replace(/[{}]/g, "").trim() });
    }
  }
  return out;
}

// Build word-level HTML diff elements for the inline AI revision proposal card.
function buildWordDiff(oldText, newText) {
  const tok = (s) => s.match(/\s+|[^\s]+/g) || [];
  const A = tok(oldText), B = tok(newText);
  const hunks = lineHunks(A, B);
  const frag = document.createDocumentFragment();
  let cur = 0;
  for (const [a0, a1, b0, b1] of hunks) {
    if (a0 > cur) frag.append(document.createTextNode(A.slice(cur, a0).join("")));
    if (a1 > a0) {
      const del = document.createElement("del");
      del.textContent = A.slice(a0, a1).join("");
      frag.append(del);
    }
    if (b1 > b0) {
      const ins = document.createElement("ins");
      ins.textContent = B.slice(b0, b1).join("");
      frag.append(ins);
    }
    cur = a1;
  }
  if (cur < A.length) frag.append(document.createTextNode(A.slice(cur).join("")));
  return frag;
}

let diagEffect, diagGutterEffect, noteEffect, peerEffect, proposalEffect;

export function loadEditor({ save, edited, onCursor }) {
  cmLoading ??= (async () => {
    const [state, view, commands, language, search, autocomplete, { stex }, { tags: t }] = await Promise.all([
      import("@codemirror/state"), import("@codemirror/view"), import("@codemirror/commands"), import("@codemirror/language"),
      import("@codemirror/search"), import("@codemirror/autocomplete"), import("@codemirror/legacy-modes/mode/stex"),
      import("@lezer/highlight"),
    ]);
    const { EditorView, Decoration, WidgetType, GutterMarker } = view;
    themeComp = new state.Compartment();

    class DiagMarker extends GutterMarker {
      constructor(severity, msg) { super(); this.severity = severity; this.msg = msg; }
      toDOM() {
        const dot = document.createElement("span");
        dot.className = `cm-diag-dot ${this.severity}`;
        dot.title = this.msg;
        return dot;
      }
    }

    class PeerBadgeWidget extends WidgetType {
      constructor(name, color) { super(); this.name = name; this.color = color; }
      eq(other) { return other.name === this.name && other.color === this.color; }
      toDOM() {
        const s = document.createElement("span");
        s.className = "cm-peer-pill";
        s.style.background = this.color;
        s.textContent = this.name;
        return s;
      }
    }

    class ProposalCardWidget extends WidgetType {
      constructor(prop) { super(); this.prop = prop; }
      eq(other) { return other.prop === this.prop; }
      ignoreEvent() { return true; }
      toDOM() {
        const { prop } = this;
        const card = document.createElement("div");
        card.className = "cm-proposal-card";
        const head = document.createElement("div");
        head.className = "cm-proposal-head";
        head.textContent = prop.title || "Proposed revision";
        card.append(head);

        if (prop.loading) {
          const msg = document.createElement("div");
          msg.className = "cm-proposal-why";
          msg.textContent = prop.loading;
          card.append(msg);
          const acts = document.createElement("div");
          acts.className = "cm-proposal-acts";
          const cancel = document.createElement("button");
          cancel.textContent = "Cancel";
          cancel.onclick = () => prop.onReject?.();
          acts.append(cancel);
          card.append(acts);
          return card;
        }

        if (prop.error) {
          const msg = document.createElement("div");
          msg.className = "cm-proposal-why";
          msg.style.color = "var(--err)";
          msg.textContent = prop.error;
          card.append(msg);
          const acts = document.createElement("div");
          acts.className = "cm-proposal-acts";
          const dismiss = document.createElement("button");
          dismiss.textContent = "Dismiss";
          dismiss.onclick = () => prop.onReject?.();
          acts.append(dismiss);
          card.append(acts);
          return card;
        }

        const diff = document.createElement("div");
        diff.className = "cm-proposal-diff";
        diff.append(buildWordDiff(prop.original, prop.replacement));
        card.append(diff);

        if (prop.rationale) {
          const why = document.createElement("div");
          why.className = "cm-proposal-why";
          why.textContent = `${prop.agent ? `${prop.agent}: ` : ""}${prop.rationale}`;
          card.append(why);
        }
        if (prop.changes?.length) {
          const tags = document.createElement("div");
          tags.className = "cm-proposal-tags";
          for (const c of prop.changes.slice(0, 5)) {
            const sp = document.createElement("span");
            sp.textContent = c.kind ? `${c.kind}: ${c.detail || ""}`.trim() : String(c.detail || c);
            tags.append(sp);
          }
          card.append(tags);
        }

        const acts = document.createElement("div");
        acts.className = "cm-proposal-acts";
        const accept = document.createElement("button");
        accept.className = "accept";
        accept.textContent = "Accept ⌘↩";
        accept.onclick = () => prop.onAccept?.();
        const reject = document.createElement("button");
        reject.textContent = "Reject";
        reject.onclick = () => prop.onReject?.();
        acts.append(accept, reject);
        if (prop.onRetry) {
          const retry = document.createElement("button");
          retry.textContent = "Try again…";
          retry.onclick = () => prop.onRetry?.();
          acts.append(retry);
        }
        card.append(acts);
        return card;
      }
    }

    const flashLines = state.StateEffect.define();
    const flashed = state.StateField.define({
      create: () => Decoration.none,
      update: (lines, tr) => tr.effects.reduce((all, e) => (e.is(flashLines) ? e.value : all), lines.map(tr.changes)),
      provide: (f) => EditorView.decorations.from(f),
    });

    diagEffect = state.StateEffect.define();
    const diagField = state.StateField.define({
      create: () => Decoration.none,
      update: (v, tr) => tr.effects.reduce((a, e) => (e.is(diagEffect) ? e.value : a), v.map(tr.changes)),
      provide: (f) => EditorView.decorations.from(f),
    });

    diagGutterEffect = state.StateEffect.define();
    const diagGutterField = state.StateField.define({
      create: () => state.RangeSet.empty,
      update: (v, tr) => tr.effects.reduce((a, e) => (e.is(diagGutterEffect) ? e.value : a), v.map(tr.changes)),
    });
    const diagGutter = view.gutter({
      class: "cm-diag-gutter",
      markers: (v) => v.state.field(diagGutterField),
      initialSpacer: () => new DiagMarker("warning", ""),
    });

    noteEffect = state.StateEffect.define();
    const noteField = state.StateField.define({
      create: () => Decoration.none,
      update: (v, tr) => tr.effects.reduce((a, e) => (e.is(noteEffect) ? e.value : a), v.map(tr.changes)),
      provide: (f) => EditorView.decorations.from(f),
    });

    peerEffect = state.StateEffect.define();
    const peerField = state.StateField.define({
      create: () => Decoration.none,
      update: (v, tr) => tr.effects.reduce((a, e) => (e.is(peerEffect) ? e.value : a), v.map(tr.changes)),
      provide: (f) => EditorView.decorations.from(f),
    });

    proposalEffect = state.StateEffect.define();
    const proposalField = state.StateField.define({
      create: () => Decoration.none,
      update: (v, tr) => tr.effects.reduce((a, e) => (e.is(proposalEffect) ? e.value : a), v.map(tr.changes)),
      provide: (f) => EditorView.decorations.from(f),
    });

    const stexLang = language.StreamLanguage.define({
      ...stex,
      languageData: { commentTokens: { line: "%" } },
    });

    // Hover cards for \cite{key}, \ref{label}, \includegraphics{path}, and diagnostics.
    const hoverExt = view.hoverTooltip(async (edView, pos) => {
      const line = edView.state.doc.lineAt(pos);
      const col = pos - line.from;
      // 1. Diagnostic on this line.
      const lineDiags = activeDiagList.filter((d) => d.line === line.number);
      if (lineDiags.length) {
        return {
          pos: line.from,
          above: true,
          create() {
            const dom = document.createElement("div");
            dom.className = "cm-hover-card";
            for (const d of lineDiags.slice(0, 3)) {
              const kind = document.createElement("div");
              kind.className = "hc-kind";
              kind.style.color = d.severity === "error" ? "var(--err)" : "var(--warn)";
              kind.textContent = d.severity === "error" ? "LaTeX Error" : "LaTeX Warning";
              const msg = document.createElement("div");
              msg.className = "hc-meta";
              msg.textContent = d.msg;
              dom.append(kind, msg);
            }
            return { dom };
          },
        };
      }
      // 2. \cite{...} key under pointer.
      const citeRe = /\\[a-zA-Z]*cite[a-zA-Z]*\*?(?:\[[^\]]*\]){0,2}\{([^{}]+)\}/g;
      for (const m of line.text.matchAll(citeRe)) {
        const innerStart = m.index + m[0].indexOf("{") + 1;
        const innerEnd = m.index + m[0].length - 1;
        if (col >= m.index && col <= innerEnd) {
          let offset = innerStart;
          for (const part of m[1].split(",")) {
            const key = part.trim();
            const start = line.text.indexOf(key, offset);
            if (key && col >= start && col <= start + key.length) {
              const entry = papersByKey.get(key) || (await completionData()).keys.find((k) => k.key === key);
              if (!entry) return null;
              return {
                pos: line.from + start,
                end: line.from + start + key.length,
                above: true,
                create() {
                  const dom = document.createElement("div");
                  dom.className = "cm-hover-card";
                  const kEl = document.createElement("div");
                  kEl.className = "hc-kind";
                  kEl.textContent = `Citation · ${entry.key}`;
                  const title = document.createElement("div");
                  title.className = "hc-title";
                  title.textContent = entry.title || "(no title)";
                  dom.append(kEl, title);
                  const meta = [entry.author, entry.venue, entry.year].filter(Boolean).join(" · ");
                  if (meta) {
                    const mEl = document.createElement("div");
                    mEl.className = "hc-meta";
                    mEl.textContent = meta;
                    dom.append(mEl);
                  }
                  if (entry.file && openLinkFn) {
                    const btn = document.createElement("button");
                    btn.className = "hc-link";
                    btn.textContent = `Open in ${entry.file}:${entry.line || 1}`;
                    btn.onclick = () => openLinkFn(entry.file, entry.line || 1);
                    dom.append(btn);
                  }
                  return { dom };
                },
              };
            }
            offset += part.length + 1;
          }
        }
      }
      // 3. \ref{...} / \cref{...} / \eqref{...}.
      const refRe = /\\(?!href)[a-zA-Z]*ref\*?\{([^{}]+)\}/g;
      for (const m of line.text.matchAll(refRe)) {
        const innerStart = m.index + m[0].indexOf("{") + 1;
        const innerEnd = m.index + m[0].length - 1;
        if (col >= m.index && col <= innerEnd) {
          const label = m[1].split(",")[0].trim();
          const data = await completionData();
          const target = data.labels.find((l) => l.label === label);
          if (!target) return null;
          const ctx = data.labelContext?.[label] || "";
          return {
            pos: line.from + innerStart,
            end: line.from + innerEnd,
            above: true,
            create() {
              const dom = document.createElement("div");
              dom.className = "cm-hover-card";
              const kEl = document.createElement("div");
              kEl.className = "hc-kind";
              kEl.textContent = `Reference · ${target.file}:${target.line}`;
              dom.append(kEl);
              if (ctx) {
                const code = document.createElement("div");
                code.className = "hc-code";
                code.textContent = ctx;
                dom.append(code);
              }
              if (openLinkFn) {
                const btn = document.createElement("button");
                btn.className = "hc-link";
                btn.textContent = "Jump to definition";
                btn.onclick = () => openLinkFn(target.file, target.line);
                dom.append(btn);
              }
              return { dom };
            },
          };
        }
      }
      // 4. \includegraphics[...]{path}.
      const imgRe = /\\includegraphics(?:\[[^\]]*\])?\{([^{}]+)\}/g;
      for (const m of line.text.matchAll(imgRe)) {
        if (col >= m.index && col <= m.index + m[0].length) {
          const raw = m[1].trim();
          const data = await completionData();
          const match = (data.figures || []).find((f) => f === raw || f.replace(/\.[^.]+$/, "") === raw);
          if (!match) return null;
          const isImg = /\.(png|jpe?g|gif|webp|svg)$/i.test(match);
          return {
            pos: line.from + m.index,
            end: line.from + m.index + m[0].length,
            above: true,
            create() {
              const dom = document.createElement("div");
              dom.className = "cm-hover-card";
              const kEl = document.createElement("div");
              kEl.className = "hc-kind";
              kEl.textContent = `Figure · ${match}`;
              dom.append(kEl);
              if (isImg) {
                const img = document.createElement("img");
                img.src = `asset?${new URLSearchParams({ path: match })}`;
                img.alt = match;
                dom.append(img);
              }
              return { dom };
            },
          };
        }
      }
      return null;
    }, { hoverTime: 260 });

    const completeAll = (ctx) => completeLatex(ctx, autocomplete);

    cmExtensions = [
      state.EditorState.lineSeparator.of("\n"),
      diagGutter,
      view.lineNumbers(),
      language.codeFolding(),
      language.foldGutter(),
      language.foldService.of(latexFold),
      view.highlightActiveLineGutter(),
      view.highlightSpecialChars(),
      view.drawSelection(),
      view.highlightActiveLine(),
      commands.history(),
      language.bracketMatching(),
      autocomplete.closeBrackets(),
      autocomplete.autocompletion({ override: [completeAll], icons: false }),
      search.search({ top: true }),
      search.highlightSelectionMatches(),
      stexLang,
      themeComp.of(buildThemeBundle(EditorView, language, t, currentTheme === "dark")),
      flashed,
      diagField,
      diagGutterField,
      noteField,
      peerField,
      proposalField,
      hoverExt,
      EditorView.lineWrapping,
      EditorView.contentAttributes.of({ spellcheck: "true" }),
      view.keymap.of([
        { key: "Mod-s", run: () => { save(); return true; }, preventDefault: true },
        { key: "Mod-b", run: (v) => { wrapSelection(v, "\\textbf{", "}"); return true; }, preventDefault: true },
        { key: "Mod-i", run: (v) => { wrapSelection(v, "\\textit{", "}"); return true; }, preventDefault: true },
        { key: "Mod-/", run: commands.toggleComment, preventDefault: true },
        ...autocomplete.closeBracketsKeymap,
        ...autocomplete.completionKeymap,
        ...language.foldKeymap,
        ...commands.defaultKeymap,
        ...search.searchKeymap,
        ...commands.historyKeymap,
        commands.indentWithTab,
      ]),
      EditorView.updateListener.of((update) => {
        edited(update);
        if (update.selectionSet || update.docChanged) onCursor?.(update);
      }),
    ];

    CM = {
      EditorState: state.EditorState,
      ChangeSet: state.ChangeSet,
      Transaction: state.Transaction,
      EditorView,
      Decoration,
      RangeSetBuilder: state.RangeSetBuilder,
      DiagMarker,
      PeerBadgeWidget,
      ProposalCardWidget,
      flashLines,
      remoteLine: Decoration.line({ class: "cm-remote" }),
      foldAll: language.foldAll,
      unfoldAll: language.unfoldAll,
      buildTheme: (dark) => buildThemeBundle(EditorView, language, t, dark),
    };

    onThemeChange(({ theme }) => reconfigureEditorTheme(theme === "dark"));
  })().catch((err) => { cmLoading = null; throw err; });
  return cmLoading;
}

export function registerView(view) {
  if (view) activeViews.add(view);
}

export function reconfigureEditorTheme(dark) {
  if (!CM || !themeComp) return;
  const bundle = CM.buildTheme(dark);
  for (const v of activeViews) {
    try { v.dispatch({ effects: themeComp.reconfigure(bundle) }); } catch {}
  }
}

export function wrapSelection(view, before, after) {
  if (!view) return;
  const { from, to } = view.state.selection.main;
  const selected = view.state.sliceDoc(from, to);
  view.dispatch({
    changes: { from, to, insert: `${before}${selected}${after}` },
    selection: { anchor: from + before.length, head: from + before.length + selected.length },
  });
  view.focus();
}

let allFolded = false;
export function toggleFoldAll(view) {
  if (!view || !CM) return false;
  allFolded = !allFolded;
  if (allFolded) CM.foldAll(view);
  else CM.unfoldAll(view);
  return allFolded;
}

// Push inline diagnostic squiggles & gutter dots for the currently open file.
export function setDiagnostics(view, diags) {
  activeDiagList = diags || [];
  if (!view || !CM || !diagEffect) return;
  const doc = view.state.doc;
  const marks = [], gutterRanges = [];
  const seenLine = new Map();
  for (const d of activeDiagList) {
    if (!d.line || d.line < 1 || d.line > doc.lines) continue;
    const prev = seenLine.get(d.line);
    if (!prev || (d.severity === "error" && prev.severity !== "error")) seenLine.set(d.line, d);
  }
  const sorted = [...seenLine.entries()].sort((a, b) => a[0] - b[0]);
  for (const [lno, d] of sorted) {
    const line = doc.line(lno);
    if (line.length > 0) {
      const trimmed = line.text.search(/\S/);
      const from = trimmed >= 0 ? line.from + trimmed : line.from;
      marks.push(CM.Decoration.mark({ class: d.severity === "error" ? "cm-diag-error" : "cm-diag-warning" }).range(from, line.to));
    }
    gutterRanges.push(new CM.DiagMarker(d.severity === "error" ? "error" : "warning", d.msg).range(line.from));
  }
  const builder = new CM.RangeSetBuilder();
  for (const r of gutterRanges) builder.add(r.from, r.to, r.value);
  view.dispatch({
    effects: [diagEffect.of(CM.Decoration.set(marks, true)), diagGutterEffect.of(builder.finish())],
  });
}

// Highlight active inline note macros (\ch{...}, \eb{...}, etc.) inside CodeMirror.
export function setNoteHighlights(view, notes, colorOf) {
  if (!view || !CM || !noteEffect) return;
  const doc = view.state.doc;
  const marks = [];
  for (const n of notes || []) {
    if (!n.line || n.line < 1 || n.line > doc.lines || !n.macro) continue;
    const line = doc.line(n.line);
    const needle = `\\${n.macro}{`;
    const idx = line.text.indexOf(needle);
    if (idx < 0) continue;
    // Find matching closing brace on same or next lines (capped at 800 chars).
    const absStart = line.from + idx;
    const slice = doc.sliceString(absStart, Math.min(doc.length, absStart + 800));
    let depth = 0, endRel = slice.length;
    for (let i = needle.length - 1; i < slice.length; i++) {
      if (slice[i] === "\\" && i + 1 < slice.length) { i++; continue; }
      if (slice[i] === "{") depth++;
      else if (slice[i] === "}") { depth--; if (depth === 0) { endRel = i + 1; break; } }
    }
    const color = colorOf ? colorOf(n.author) : "#2563eb";
    marks.push(CM.Decoration.mark({
      class: "cm-note-span",
      attributes: { style: `--note-col: ${color}`, title: `${n.author}: ${n.text || ""}` },
    }).range(absStart, Math.min(doc.length, absStart + endRel)));
  }
  view.dispatch({ effects: noteEffect.of(CM.Decoration.set(marks, true)) });
}

// Render subtle remote collaborator badges on lines where coauthors are editing this file.
export function setRemotePeers(view, peers, colorOf) {
  if (!view || !CM || !peerEffect) return;
  const doc = view.state.doc;
  const decos = [];
  for (const p of peers || []) {
    if (!p.line || p.line < 1 || p.line > doc.lines) continue;
    const line = doc.line(p.line);
    const color = colorOf ? colorOf(p.id || p.name) : "#2563eb";
    decos.push(CM.Decoration.line({ class: "cm-peer-line", attributes: { style: `--peer-col: ${color}` } }).range(line.from));
    decos.push(CM.Decoration.widget({ widget: new CM.PeerBadgeWidget(p.name || p.id, color), side: 1 }).range(line.to));
  }
  view.dispatch({ effects: peerEffect.of(CM.Decoration.set(decos, true)) });
}

// Show or clear an inline Lattice-style AI revision proposal card below [from, to].
export function showProposal(view, prop) {
  if (!view || !CM || !proposalEffect) return;
  if (!prop) return clearProposal(view);
  const doc = view.state.doc;
  const from = Math.max(0, Math.min(prop.from, doc.length));
  const to = Math.max(from, Math.min(prop.to, doc.length));
  const lineEnd = doc.lineAt(to).to;
  const decos = [];
  if (to > from) decos.push(CM.Decoration.mark({ class: "cm-proposal-hl" }).range(from, to));
  decos.push(CM.Decoration.widget({ widget: new CM.ProposalCardWidget(prop), block: true, side: 1 }).range(lineEnd));
  view.dispatch({
    effects: [proposalEffect.of(CM.Decoration.set(decos, true)), CM.EditorView.scrollIntoView(lineEnd, { y: "center" })],
  });
}

export function clearProposal(view) {
  if (!view || !CM || !proposalEffect) return;
  view.dispatch({ effects: proposalEffect.of(CM.Decoration.none) });
}

// Rich autocompletion: \ref, \cite, \includegraphics, \begin{...}, and LaTeX commands.
const REF_ARG = /\\(?!href)[a-zA-Z]*ref\*?\{([^{}]*)$/;
const CITE_ARG = /\\[a-zA-Z]*cite[a-zA-Z]*\*?(?:\[[^\]]*\]){0,2}\{([^{}]*)$/;
const IMG_ARG = /\\includegraphics(?:\[[^\]]*\])?\{([^{}]*)$/;
const BEGIN_ARG = /\\begin\{([^{}]*)$/;
const CMD_ARG = /\\([a-zA-Z]*)$/;

let known = null;
export function completionData(force = false) {
  if (force || !known || performance.now() - known.at > 5000) {
    known = {
      at: performance.now(),
      promise: fetch("completions", { cache: "no-store" })
        .then((r) => (r.ok ? r.json() : { labels: [], keys: [], figures: [] }))
        .catch(() => ({ labels: [], keys: [], figures: [] })),
    };
  }
  return known.promise;
}

async function completeLatex(context, autocomplete) {
  const line = context.state.doc.lineAt(context.pos);
  const before = line.text.slice(0, context.pos - line.from);

  const ref = before.match(REF_ARG);
  if (ref) {
    const typed = ref[1].split(",").pop().trimStart();
    const data = await completionData();
    const text = context.state.doc.toString().replace(/(?<!\\)%.*$/gm, "");
    const here = [...text.matchAll(/\\label\s*\{([^{}\n]+)\}/g)].map((m) => m[1]);
    const options = [...new Set([...here, ...data.labels.map((l) => l.label)])].map((label) => ({
      label,
      detail: data.labelContext?.[label]?.slice(0, 50) || "",
    }));
    return { from: context.pos - typed.length, options, validFor: /^[^,{}\s]*$/ };
  }

  const cite = before.match(CITE_ARG);
  if (cite) {
    const typed = cite[1].split(",").pop().trimStart();
    const data = await completionData();
    const options = data.keys.map((k) => {
      const rich = papersByKey.get(k.key);
      const yr = rich?.year ? ` (${rich.year})` : "";
      return { label: k.key, detail: `${k.title || rich?.title || ""}${yr}` };
    });
    return { from: context.pos - typed.length, options, validFor: /^[^,{}\s]*$/ };
  }

  const img = before.match(IMG_ARG);
  if (img) {
    const typed = img[1].trimStart();
    const data = await completionData();
    const options = (data.figures || []).map((f) => ({ label: f, detail: "figure" }));
    return { from: context.pos - typed.length, options, validFor: /^[^{}\s]*$/ };
  }

  const beg = before.match(BEGIN_ARG);
  if (beg) {
    const typed = beg[1];
    const from = context.pos - typed.length;
    const afterText = line.text.slice(context.pos - line.from);
    const hasCloseBrace = afterText.startsWith("}");
    const options = ENVS.map((env) => autocomplete.snippetCompletion(
      hasCloseBrace ? `${env}}\n  \${1}\n\\end{${env}}` : `${env}}\n  \${1}\n\\end{${env}}`,
      { label: env, detail: "environment" },
    ));
    return { from, to: hasCloseBrace ? context.pos + 1 : context.pos, options, validFor: /^[a-zA-Z*]*$/ };
  }

  const cmd = before.match(CMD_ARG);
  if (cmd && (cmd[1].length >= 1 || context.explicit)) {
    const from = context.pos - cmd[0].length;
    const options = COMMANDS.map((c) => autocomplete.snippetCompletion(c.insert, { label: c.label, detail: c.detail }));
    return { from, options, validFor: /^\\[a-zA-Z]*$/ };
  }

  return null;
}
