// Who is asking (live.py /me), and what their role lets them do here: comment, and as whom (who, among the paper's note
// authors), edit the source, and open source lines (in VS Code, or in the source pane).

import { $, docFile, editorShown, el, hubBase, isDiff, setStatus, SIGNED_OUT, sleep, UNREACHABLE } from "./page.js";
import { readBody, signInPage, unreachable } from "./net.js";

// Who is asking (live.py /me). In shared mode (LATEX_LIVE_USERS) a collaborator has a role and
// comments as themselves; otherwise it is the owner, who may do everything. A server older than
// this viewer has no /me: the owner then, as before, without the browser editor (and with the GET
// forms of switch, diff and open, the only ones it knows).
const ALL_CAN = { comment: true, edit: true, rebuild: true, compare: true, switch: true, openHost: true, download: true };
export let me = { id: "", name: "", macro: "", role: "owner", shared: false, owner: "", legacy: true, can: ALL_CAN };
export const isCollaborator = () => me.shared && me.role !== "owner";
export const ownerName = () => me.owner || "the owner";

// Where this user can open a source line (links.js openSource): in the source pane, or in VS Code on the host.
export const editable = (path) => /\.(tex|bib|sty|cls)$/i.test(path); // (what live.py /files lists)
export const opensInBrowser = () => editorAllowed() && (editorShown() || (me.shared && !me.can.openHost));
export const canOpenFile = (path) => me.can.openHost || (editorAllowed() && editable(path));
export const opensSources = () => me.can.openHost || editorAllowed();
export const openWhere = () => (opensInBrowser() ? "the source pane" : "VS Code");
// A link to a source line says where it opens; openTitles updates that when the pane opens or closes.
export function opensAt(node, where) {
  node.dataset.opens = where;
  node.title = `Open ${where} in ${openWhere()}`;
  return node;
}
export const openTitles = () => { for (const node of document.querySelectorAll("[data-opens]")) node.title = `Open ${node.dataset.opens} in ${openWhere()}`; };

// Why a collaborator whose role may comment can't, or null: the paper defines no note macro of theirs
// (/me macroDefined; from an older server, the note macros /authors lists, when it lists any).
export function notSetUp() {
  if (!isCollaborator() || me.role === "viewer") return null;
  const listed = authors.some((a) => a.macro === me.macro);
  if (me.macro && (listed || (me.macroDefined ?? !authors.length))) return null;
  return me.macro ? `Commenting isn't set up for you yet: the paper has no \\${me.macro} note macro. Ask ${ownerName()} to add it.`
                  : `Commenting isn't set up for you yet: you have no note macro. Ask ${ownerName()} to give you one.`;
}
// Whether this user can write notes here (and so sees the controls for it).
export const canComment = () => !isDiff && !!me.can.comment && !notSetUp() && (isCollaborator() ? !!who : authors.length > 0);
// /me, asked once at the start. Until the server answers, ask again; a 404 is a server older than
// this viewer; a 403 means the access ended (returns false then: start says so).
export async function loadMe() {
  for (let attempt = 1, wait = 1000; ; attempt++, wait = Math.min(2 * wait, 10000)) {
    let answered = false; // (the server answers, just not /me)
    try {
      const r = await fetch("me", { cache: "no-store" }).catch(() => { throw new Error(UNREACHABLE); });
      const body = await readBody(r);
      if (r.status === 403) return false;
      if (signInPage(r, body)) throw new Error(SIGNED_OUT);
      if (unreachable(r, body)) throw new Error(UNREACHABLE);
      if (r.status === 404) return true;
      answered = true;
      if (!r.ok || typeof body.role !== "string") throw new Error(`who you are: ${r.status}`);
      me = { ...me, ...body, legacy: false, can: { ...(body.shared ? {} : ALL_CAN), ...body.can } };
      return true;
    } catch (err) {
      if (answered && attempt >= 3) return true; // (the owner, as before)
      const message = `${err.message || err}`;
      setStatus([UNREACHABLE, SIGNED_OUT].includes(message) ? message : `${message} (retrying)`, "error");
    }
    await sleep(wait);
  }
}

// Whether this user may edit the sources, in the source pane (editor.js).
export const editorAllowed = () => !isDiff && !me.legacy && !!me.can.edit;

// Whose notes this user writes (the note macro picked in the toolbar; a collaborator's own: applyRole), and the
// paper's note authors (/authors).
export let who = localStorage.getItem(`latex-live:${hubBase}who`) || "", authors = [];

export function setWho(macro) { who = macro; }
// How the user's notes are labeled, in the toolbar and on the comment button alike: as /authors labels
// their note macro (the initials it prints), else, for a collaborator, by their name.
export const whoLabel = () => authors.find((a) => a.macro === who)?.author || (isCollaborator() ? me.name || me.id : who);
export async function loadAuthors() {
  authors = await (await fetch(`authors/${encodeURIComponent(docFile)}`)).json();
  if (isCollaborator() && me.macroDefined === false && me.role !== "viewer" && authors.some((a) => a.macro === me.macro)) {
    me.macroDefined = true; // (the paper has defined it since /me answered; live.py checks again for each note)
    me.can.comment = true;
  }
  const pick = $("whoPick");
  if (isCollaborator()) { // (who you are comes from the server: a label, not a choice)
    pick.replaceChildren(el("option", { value: who, textContent: `Commenting as ${whoLabel()}`, selected: true }));
    pick.disabled = true;
    pick.title = `Your notes are signed \\${who} (${me.name || me.id}): select text in the PDF, then press C or click the 💬 button that appears beside it`;
    pick.hidden = !canComment();
    return;
  }
  pick.replaceChildren(el("option", { value: "", textContent: "💬 Comment as…" }),
                       ...authors.map((a) => el("option", { value: a.macro, textContent: `💬 ${a.author}`, selected: a.macro === who })));
  pick.hidden = isDiff || !authors.length;
}
