// Workspace appearance: Light / Dark theme, surface tint presets (Graphite, Paper, Sage, Mist),
// and independent PDF inversion, persisted in localStorage and broadcast to CodeMirror.

export const TINTS = [
  { id: "graphite", label: "Graphite", swatch: "#e5e5e8", darkSwatch: "#202024" },
  { id: "paper", label: "Paper", swatch: "#f0ebe1", darkSwatch: "#25211c" },
  { id: "sage", label: "Sage", swatch: "#e6ece4", darkSwatch: "#1c231e" },
  { id: "mist", label: "Mist", swatch: "#e5ebf2", darkSwatch: "#1a2029" },
];

const listeners = new Set();

// Migrate legacy latex-live:dark seamlessly while defaulting fresh browsers to crisp Light + Graphite.
const savedTheme = localStorage.getItem("latex-live:theme");
const legacyDark = localStorage.getItem("latex-live:dark");
export let theme = savedTheme === "dark" || savedTheme === "light" ? savedTheme : (legacyDark ? "dark" : "light");
export let tint = TINTS.some((t) => t.id === localStorage.getItem("latex-live:tint"))
  ? localStorage.getItem("latex-live:tint") : "graphite";
export let pdfInvert = localStorage.getItem("latex-live:pdfInvert") === "1"
  || (localStorage.getItem("latex-live:pdfInvert") === null && theme === "dark" && !!legacyDark);

export function applyTheme() {
  const root = document.documentElement;
  root.dataset.theme = theme;
  root.dataset.tint = tint;
  document.body.classList.toggle("dark", theme === "dark");
  document.body.classList.toggle("pdf-invert", pdfInvert);
  for (const fn of listeners) fn({ theme, tint, pdfInvert });
}

export function setTheme(next) {
  theme = next === "dark" ? "dark" : "light";
  localStorage.setItem("latex-live:theme", theme);
  localStorage.setItem("latex-live:dark", theme === "dark" ? "1" : "");
  applyTheme();
}

export function toggleTheme() {
  setTheme(theme === "dark" ? "light" : "dark");
}

export function setTint(next) {
  if (!TINTS.some((t) => t.id === next)) return;
  tint = next;
  localStorage.setItem("latex-live:tint", tint);
  applyTheme();
}

export function setPdfInvert(on) {
  pdfInvert = !!on;
  localStorage.setItem("latex-live:pdfInvert", pdfInvert ? "1" : "0");
  applyTheme();
}

export function onThemeChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

applyTheme();
