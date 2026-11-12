// Moving around the PDF: back and forward, going to a page, and revealing a spot (scrolling to it, and flashing it).

import { $, el } from "./page.js";
import { container, eventBus, linkService, pageFrame, viewer } from "./pdfview.js";
import { hidePreview } from "./preview.js";

// Back/forward after following a link (a \ref, a citation, the outline) or a forward search.
// Positions are PDF coordinates, so they survive zoom changes and reloads.
export const back = [], ahead = [];
let here = null;
eventBus.on("updateviewarea", (e) => { here = e.location; });
const position = () => here && { page: here.pageNumber, left: here.left, top: here.top };
function remember() {
  if (!here) return;
  back.push(position());
  ahead.length = 0;
  updateHistory();
}
export function travel(from, to) {
  if (!from.length) return;
  to.push(position());
  const p = from.pop();
  viewer.scrollPageIntoView({ pageNumber: p.page, destArray: [null, { name: "XYZ" }, p.left, p.top, null] });
  updateHistory();
}
// Go to a page (back returns from it).
export const goToPage = (n) => { remember(); viewer.currentPageNumber = Math.min(viewer.pagesCount, Math.max(1, n)); };
function updateHistory() { $("back").disabled = !back.length; $("forward").disabled = !ahead.length; }
const goToDestination = linkService.goToDestination.bind(linkService);
linkService.goToDestination = (dest) => { remember(); hidePreview(); return goToDestination(dest); };
$("back").onclick = () => travel(back, ahead);
$("forward").onclick = () => travel(ahead, back);
container.addEventListener("mouseup", (e) => { // mouse side buttons
  if (e.button === 3) travel(back, ahead);
  else if (e.button === 4) travel(ahead, back);
});

// Briefly highlight a rectangle [left, top, right, bottom] on a page (as shown: pageFrame).
export function flash(pageNumber, rect, cls = "") {
  const pageView = viewer.getPageView(pageNumber - 1);
  if (!pageView?.pdfPage) return;
  const { W, H } = pageFrame(pageView.pdfPage), [x0, y0, x1, y1] = rect;
  const div = el("div", { className: `sync-flash ${cls}` });
  Object.assign(div.style, { left: `${x0 / W * 100}%`, top: `${y0 / H * 100}%`,
                             width: `${(x1 - x0) / W * 100}%`, height: `${(y1 - y0) / H * 100}%` });
  pageView.div.append(div);
  setTimeout(() => div.remove(), 2600);
}

// Scroll so that a rectangle (on the page as shown) is a third of the way down the view, and flash it.
export function reveal(pageNumber, rect) {
  const pageView = viewer.getPageView(pageNumber - 1);
  if (!pageView?.pdfPage) return;
  remember();
  const [x, y] = pageFrame(pageView.pdfPage).toUser(0, rect[1]); // (the page's left edge level with the top: x too, on a turned page)
  viewer.scrollPageIntoView({ pageNumber, destArray: [null, { name: "XYZ" }, x, y, null] });
  container.scrollTop -= container.clientHeight / 3;
  flash(pageNumber, rect);
}
