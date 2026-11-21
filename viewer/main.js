// The viewer's script (viewer.html loads it): the modules in this folder, imported from here, set up the page as they
// load; then the controls follow the role, and start starts the viewer.

import { $, isDiff } from "./page.js";
import { latestKept } from "./drafts.js";
import { editorAllowed, isCollaborator, loadAuthors, loadMe, me, setWho } from "./role.js";
import { isPopout, layoutMode, setLayoutMode, tab } from "./layout.js";
import { showEditor } from "./editor.js";
import { showAccessEnded } from "./banner.js";
import { showWelcome, welcomeDue } from "./welcome.js";
import { showSide, showTab } from "./sidebar.js";
import { loadDocs } from "./docs.js";
import { poll } from "./poll.js";
import { loadAgentInfo } from "./agent.js";
import { loadPapers } from "./papers.js";
import "./filemenu.js";
import "./keys.js";
import "./palette.js";

// Controls follow the role (once /me has answered): a collaborator comments only as themselves,
// and sees no controls for what their role can't do (live.py refuses those anyway).
const ROLES = { viewer: "you can read the paper", commenter: "you can read the paper and comment on it",
                editor: "you can comment and edit the LaTeX source", owner: "this preview is yours" };
function applyRole() {
  if (isCollaborator()) setWho(me.macro || "");
  const canEdit = editorAllowed() && !isPopout;
  $("rebuild").hidden = isDiff || !me.can.rebuild;
  $("compare").hidden = !me.can.compare;
  $("editBtn").hidden = true; // superseded by segmented layoutSwitch when editor is allowed
  $("layoutSwitch").hidden = !canEdit;
  $("popoutBtn").hidden = isPopout || isDiff;
  $("me").hidden = !me.shared;
  $("me").textContent = `${me.name || me.id} · ${me.role}`;
  $("me").title = `Signed in as ${me.id}: ${ROLES[me.role] || me.role}`;
  $("welcomeBtn").hidden = $("keys").querySelector(".welcome").hidden = !me.shared;
  $("signOut").hidden = !me.can.signOut;
  const hideTab = (name, hidden) => {
    const b = document.querySelector(`#tabs [data-tab=${name}]`);
    if (b) b.hidden = hidden;
  };
  hideTab("files", isDiff || !canEdit);
  hideTab("checks", isDiff || me.can.checks === false);
  hideTab("history", isDiff || !me.can.history);
  hideTab("papers", isDiff || !canEdit);
  const curTabBtn = document.querySelector(`#tabs [data-tab=${tab}]`);
  if (curTabBtn?.hidden) showTab("outline");
  setLayoutMode(canEdit ? layoutMode : "pdf");
}

// The sidebar, as it was left (kept closed in pop-out PDF windows).
if (!isPopout && localStorage.getItem("latex-live:side")) showSide(true);

async function start() {
  if (!(await loadMe())) return showAccessEnded();
  applyRole();
  if (me.can.switch) {
    loadDocs().catch(() => {});
    setInterval(() => { if (document.activeElement !== $("docPick")) loadDocs().catch(() => {}); }, 15000);
  }
  const authorsLoaded = isDiff ? null : loadAuthors().catch(() => {});
  if (!isDiff && editorAllowed()) {
    loadPapers().catch(() => {});
    loadAgentInfo().catch(() => {});
  }
  poll();
  setInterval(poll, isCollaborator() ? 500 : 250);
  const kept = editorAllowed() && !isPopout ? latestKept() : null;
  const wantEditor = !isPopout && editorAllowed() && (kept || layoutMode !== "pdf" || localStorage.getItem("latex-live:editor"));
  if (wantEditor) showEditor(true, { path: kept?.path, focus: false, mode: layoutMode === "source" ? "source" : "split" });
  await authorsLoaded;
  if (!isPopout && welcomeDue()) showWelcome(true, true);
}
start();
