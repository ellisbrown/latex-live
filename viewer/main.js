// The viewer's script (viewer.html loads it): the modules in this folder, imported from here, set up the page as they
// load; then the controls follow the role, and start starts the viewer.

import { $, isDiff, isPopout } from "./page.js";
import { latestKept } from "./drafts.js";
import { editorAllowed, isCollaborator, loadAuthors, loadMe, me, setWho } from "./role.js";
import { layoutMode, setLayoutMode, tellWindows } from "./layout.js";
import { showEditor } from "./editor.js";
import { showAccessEnded } from "./banner.js";
import { showWelcome, welcomeDue } from "./welcome.js";
import { showSide, showTabButton } from "./sidebar.js";
import { loadDocs } from "./docs.js";
import { poll } from "./poll.js";
import "./filemenu.js";
import "./keys.js";
import "./refs.js";
import "./files.js";
import "./history.js";
import "./agent.js";

// Controls follow the role (once /me has answered): a collaborator comments only as themselves,
// and sees no controls for what their role can't do (live.py refuses those anyway).
const ROLES = { viewer: "you can read the paper", commenter: "you can read the paper and comment on it",
                editor: "you can comment and edit the LaTeX source", owner: "this preview is yours" };
function applyRole() {
  if (isCollaborator()) setWho(me.macro || "");
  $("rebuild").hidden = isDiff || !me.can.rebuild;
  $("compare").hidden = !me.can.compare;
  $("layoutSwitch").hidden = !editorAllowed() || isPopout;
  setLayoutMode("pdf", false); // (the PDF alone until the source pane opens: start)
  $("me").hidden = !me.shared;
  $("me").textContent = `${me.name || me.id} · ${me.role}`;
  $("me").title = `Signed in as ${me.id}: ${ROLES[me.role] || me.role}`;
  $("welcomeBtn").hidden = $("keys").querySelector(".welcome").hidden = !me.shared; // (the guide is for coauthors)
  $("signOut").hidden = !me.can.signOut; // (not for a coauthor whom the proxy signs in)
  if (!isDiff) showTabButton("checks", me.can.checks !== false);
  if (!isDiff) showTabButton("refs", editorAllowed() && !isPopout);
  if (!isDiff) showTabButton("files", editorAllowed() && !isPopout);
  if (!isDiff) showTabButton("history", !!me.can.history && !isPopout);
}

// The layout as it was left, for start.
const leftLayout = layoutMode;

// The sidebar, as it was left (a pop-out window starts without it).
if (localStorage.getItem("latex-live:side") && !isPopout) showSide(true);

// Start: who is asking first (it decides the controls, and whose notes new ones are), then the
// documents (the owner's menu), the authors, the status poll, the source pane if it was open (or on
// a file with unsaved edits this browser kept, to restore), and the welcome guide when it is due.
// A main window then tells pop-out windows that it is there, for a source line one has for it (links.js).
async function start() {
  if (!(await loadMe())) return showAccessEnded();
  applyRole();
  if (me.can.switch) {
    loadDocs().catch(() => {});
    setInterval(() => { if (document.activeElement !== $("docPick")) loadDocs().catch(() => {}); }, 15000); // (papers opened since)
  }
  const authorsLoaded = isDiff ? null : loadAuthors().catch(() => {});
  poll();
  setInterval(poll, isCollaborator() ? 500 : 250); // (coauthors: half as often, as there may be many)
  const kept = editorAllowed() && !isPopout ? latestKept() : null;
  const mode = leftLayout === "pdf" ? null : leftLayout;
  const opened = kept || (editorAllowed() && mode) ? showEditor(true, { path: kept?.path, focus: false, mode }) : null;
  if (!isPopout) Promise.resolve(opened).then(() => tellWindows({ kind: "mainWindow" })); // (once its own file is open)
  await authorsLoaded; // (the guide names your initials)
  if (welcomeDue() && !isPopout) showWelcome(true, true);
}
start();
