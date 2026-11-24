// The icons of the viewer's chrome: line drawings on a 16-unit grid, drawn in the text's color (currentColor).

const NS = "http://www.w3.org/2000/svg";
const PATHS = {
  panelLeft: "M2.5 3.5h11a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1Zm3.5 0v9",
  back: "M10 3.5 5.5 8l4.5 4.5",
  forward: "M6 3.5 10.5 8 6 12.5",
  outline: "M3 4h10M5.5 8h7.5M5.5 12h7.5M3 8h.6M3 12h.6",
  pages: "M3 3h4v4.5H3V3Zm6 0h4v4.5H9V3ZM3 9h4v4H3V9Zm6 0h4v4H9V9Z",
  notes: "M3 3.5h10a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1H6.5L3.5 13.8V11.5H3a1 1 0 0 1-1-1v-6a1 1 0 0 1 1-1Z",
  checks: "M8 2.2 13 4.3v3.5c0 3-2.1 5.1-5 6-2.9-.9-5-3-5-6V4.3L8 2.2Zm-2 6 1.5 1.5 3-3.2",
  figures: "M2.5 3.5h11v9h-11v-9Zm0 3h11M6.5 6.5v6",
  download: "M8 2.5v7.5m-3-2.8L8 10.2l3-3M2.8 12.8h10.4",
  rebuild: "M12.8 8a4.8 4.8 0 1 1-1.2-3.2M12.8 2.8v2.6h-2.6",
  more: "M3.8 8a.8.8 0 1 0 0-.01M8 8a.8.8 0 1 0 0-.01M12.2 8a.8.8 0 1 0 0-.01",
  cut: "M4.5 4.5a1.6 1.6 0 1 0 0 3.2 1.6 1.6 0 0 0 0-3.2Zm0 4.6a1.6 1.6 0 1 0 0 3.2 1.6 1.6 0 0 0 0-3.2Zm1.4-2.1 7.6 4.5M5.9 9.7 13.5 5.2",
  diff: "M8 2.8 13.2 12.5H2.8L8 2.8Z",
  sun: "M8 5.2a2.8 2.8 0 1 0 0 5.6 2.8 2.8 0 0 0 0-5.6ZM8 1.8v1.4M8 12.8v1.4M1.8 8h1.4M12.8 8h1.4M3.6 3.6l1 1m6.8 6.8 1 1m0-8.8-1 1M4.6 11.4l-1 1",
  moon: "M12.4 9.8A4.8 4.8 0 0 1 6.2 3.6a5 5 0 1 0 6.2 6.2Z",
  popout: "M9.5 2.8h3.7v3.7M13 3 7.8 8.2M11.5 9.2v3.3a.8.8 0 0 1-.8.8H3.5a.8.8 0 0 1-.8-.8V5.3a.8.8 0 0 1 .8-.8h3.3",
  sync: "M8 2.5v2.2m0 6.6v2.2M2.5 8h2.2m6.6 0h2.2M8 5.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5Z",
  close: "M4.2 4.2 11.8 11.8M11.8 4.2 4.2 11.8",
  search: "M7.2 2.8a4.4 4.4 0 1 0 0 8.8 4.4 4.4 0 0 0 0-8.8Zm3.2 7.6 2.8 2.8",
  file: "M4 2.5h5.2L12.5 5.8v7.7a.5.5 0 0 1-.5.5H4a.5.5 0 0 1-.5-.5V3a.5.5 0 0 1 .5-.5Zm5 0v3.5h3.5",
  edit: "M10.8 2.8 13.2 5.2 5.6 12.8H3.2v-2.4l7.6-7.6Z",
};

// An icon as an <svg> element, size pixels square (decorative: the button it sits in has the label).
export function icon(name, size = 15) {
  const svg = document.createElementNS(NS, "svg");
  for (const [key, value] of Object.entries({ viewBox: "0 0 16 16", width: size, height: size, "aria-hidden": "true", class: "ic" })) {
    svg.setAttribute(key, String(value));
  }
  const path = document.createElementNS(NS, "path");
  for (const [key, value] of Object.entries({ d: PATHS[name], fill: "none", stroke: "currentColor", "stroke-width": "1.45",
                                              "stroke-linecap": "round", "stroke-linejoin": "round" })) {
    path.setAttribute(key, value);
  }
  svg.append(path);
  return svg;
}
