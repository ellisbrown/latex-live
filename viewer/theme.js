// The look: the light or dark theme, the tint of its surfaces (Graphite, Paper, Sage, Mist), and whether the PDF's
// colors are inverted (for reading it dark, in either theme). Remembered across sessions, followed by every open
// window of the viewer, and told to whatever draws in the theme's colors itself (onThemeChange: the source pane).

export const TINTS = [
  { id: "graphite", label: "Graphite", swatch: "#e5e5e8", darkSwatch: "#202024" },
  { id: "paper", label: "Paper", swatch: "#f0ebe1", darkSwatch: "#25211c" },
  { id: "sage", label: "Sage", swatch: "#e6ece4", darkSwatch: "#1c231e" },
  { id: "mist", label: "Mist", swatch: "#e5ebf2", darkSwatch: "#1a2029" },
];

export let theme, tint, pdfInvert;
// The look as remembered. Before themes, "dark" both darkened the chrome and inverted the PDF: a browser that had
// it on starts with both (and from then on, the two are apart).
function readLook() {
  const saved = localStorage.getItem("latex-live:theme"), oldDark = !!localStorage.getItem("latex-live:dark");
  theme = saved === "dark" || saved === "light" ? saved : oldDark ? "dark" : "light";
  const savedTint = localStorage.getItem("latex-live:tint");
  tint = TINTS.some((t) => t.id === savedTint) ? savedTint : "graphite";
  const invert = localStorage.getItem("latex-live:pdfInvert");
  pdfInvert = invert === null ? oldDark : invert === "1";
}

const listeners = new Set();
export const onThemeChange = (fn) => listeners.add(fn);
function applyLook() {
  document.documentElement.dataset.theme = theme;
  document.documentElement.dataset.tint = tint;
  document.body.classList.toggle("pdf-invert", pdfInvert);
  for (const fn of listeners) fn({ theme, tint, pdfInvert });
}

export function setTheme(next) {
  theme = next === "dark" ? "dark" : "light";
  localStorage.setItem("latex-live:theme", theme);
  applyLook();
}
export function setTint(next) {
  if (!TINTS.some((t) => t.id === next)) return;
  tint = next;
  localStorage.setItem("latex-live:tint", tint);
  applyLook();
}
export function setPdfInvert(on) {
  pdfInvert = !!on;
  localStorage.setItem("latex-live:pdfInvert", pdfInvert ? "1" : "0");
  applyLook();
}

// (another window of the viewer, e.g. a pop-out, changed the look)
window.addEventListener("storage", (e) => {
  if (!["latex-live:theme", "latex-live:tint", "latex-live:pdfInvert"].includes(e.key)) return;
  readLook();
  applyLook();
});

readLook();
applyLook();
