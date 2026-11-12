// The sidebar's figures and tables.

import { $, cleanFile, el, file } from "./page.js";
import { canOpenFile, opensAt } from "./role.js";
import { doc, docSeq, pageFrame, pdfjsLib, shownVariant, viewer } from "./pdfview.js";
import { reveal } from "./navigation.js";
import { openSource } from "./links.js";

// Figures and tables (live.py /floats), each cropped from its page as it scrolls into view; click one
// to go there. The chips show only figures or only tables.
let floatsKey = null, floatObserver = null, floatKind = localStorage.getItem("latex-live:floatKind") || "all";
export async function renderFloats() {
  const key = `${docSeq}/${floatKind}`;
  if (floatsKey === key) return;
  floatsKey = key;
  const d = doc, panel = $("figures");
  const list = await (await fetch(`floats/${encodeURIComponent(shownVariant === "clean" ? cleanFile : file)}`)).json();
  if (d !== doc || floatsKey !== key) return;
  floatObserver?.disconnect();
  floatObserver = new IntersectionObserver((seen) => {
    for (const e of seen) {
      if (!e.isIntersecting || e.target.dataset.done) continue;
      e.target.dataset.done = "1";
      drawFloat(d, e.target, list[+e.target.dataset.i]).catch((err) => console.warn(err));
    }
  }, { root: panel, rootMargin: "400px" });
  const count = (kind) => list.filter((f) => f.kind === kind).length;
  const filters = el("div", { className: "filters" });
  for (const [kind, text, color] of [["all", `All ${list.length}`, "#ccc"], ["figure", `Figures ${count("figure")}`, "#9cf"],
                                     ["table", `Tables ${count("table")}`, "#fc9"]]) {
    const chip = el("span", { className: `chip${floatKind === kind ? "" : " off"}`, textContent: text });
    chip.style.background = color;
    chip.onclick = () => { floatKind = kind; localStorage.setItem("latex-live:floatKind", kind); renderFloats(); };
    filters.append(chip);
  }
  const scroll = panel.scrollTop;
  const cards = [];
  list.forEach((f, i) => {
    if (floatKind !== "all" && f.kind !== floatKind) return;
    const name = `${f.kind === "table" ? "Table" : "Figure"} ${f.num ?? "?"}`;
    const where = opensAt(el("span", { className: "where", textContent: `${f.file.replace(/^.*\//, "")}:${f.line}` }), `${f.file}:${f.line}`);
    where.onclick = (e) => { e.stopPropagation(); openSource(f.file, f.line); };
    const crop = el("div", { className: "crop" });
    const shownBox = () => { // (on the page as shown: a turned page's turned)
      const page = viewer.getPageView(f.page - 1)?.pdfPage;
      return page ? pageFrame(page).rect(f.box) : f.box;
    };
    const [x0, y0, x1, y1] = shownBox();
    crop.style.aspectRatio = `${x1 - x0 + 8} / ${y1 - y0 + 8}`;
    const card = el("div", { className: "float", title: `${name}: show in the PDF` },
                    el("div", { className: "head" }, el("b", { textContent: name }), ...(canOpenFile(f.file) ? [where] : []),
                       el("span", { className: "pg", textContent: `p. ${f.label ?? f.page}` })),
                    crop, el("div", { className: "cap", textContent: f.caption }));
    card.dataset.i = i;
    card.onclick = () => {
      const [left, top, right, bottom] = shownBox();
      reveal(f.page, [left - 2, top - 2, right + 2, bottom + 2]);
    };
    cards.push(card);
    floatObserver.observe(card);
  });
  panel.replaceChildren(filters, ...(cards.length ? cards : [el("div", { className: "empty", textContent: list.length ? "None of this kind." : "No figures or tables found in the build." })]));
  panel.scrollTop = scroll;
}
async function drawFloat(d, card, f) {
  const page = await d.getPage(f.page);
  const [x0, y0, x1, y1] = pageFrame(page).rect(f.box), pad = 4; // (the page renders as shown)
  const crop = card.querySelector(".crop");
  crop.style.aspectRatio = `${x1 - x0 + 2 * pad} / ${y1 - y0 + 2 * pad}`;
  const scale = crop.clientWidth * (window.devicePixelRatio || 1) / (x1 - x0 + 2 * pad);
  const viewport = page.getViewport({ scale, offsetX: -(x0 - pad) * scale, offsetY: -(y0 - pad) * scale });
  const canvas = el("canvas", { width: Math.ceil((x1 - x0 + 2 * pad) * scale), height: Math.ceil((y1 - y0 + 2 * pad) * scale) });
  await page.render({ canvasContext: canvas.getContext("2d"), viewport, annotationMode: pdfjsLib.AnnotationMode.DISABLE }).promise;
  crop.replaceChildren(canvas);
}
