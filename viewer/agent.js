// Owner-only BYO-Agent revision workflow: Polish / Proofread / Custom instruction on selected
// LaTeX text, or Propose fix for a margin note, rendering an inline Accept / Reject diff card
// right inside CodeMirror (Lattice style). Never mutates files without explicit Accept.

import { $, editorShown, setStatus } from "./page.js";
import { postJSON, readBody } from "./net.js";
import { me } from "./role.js";
import { cmView, ed, getSelectionContext, openFile, showEditor } from "./editor.js";
import { clearProposal, showProposal } from "./cm.js";

let agentInfo = null; // {available: [{id, label}], default, configured}
let activeProposal = null;
let pollTimer = null;
const readyListeners = new Set();

export function agentReady() { return !!(me.can.agent && agentInfo?.configured); }
export function onAgentReady(fn) { readyListeners.add(fn); return () => readyListeners.delete(fn); }

export async function loadAgentInfo() {
  if (!me.can.agent) return;
  try {
    const r = await fetch("agent/info", { cache: "no-store" });
    if (!r.ok) return;
    agentInfo = await r.json();
    $("polishBtn").hidden = !agentInfo.configured;
    $("polishAgentMeta").textContent = agentInfo.configured
      ? `Local agent: ${agentInfo.available.map((a) => a.label).join(", ")}`
      : "No local agent CLI found (claude, codex, pi, or $LATEX_LIVE_AGENT_CMD)";
    if (agentInfo.configured) for (const fn of readyListeners) fn();
  } catch {}
}

function dismissProposal() {
  clearTimeout(pollTimer);
  activeProposal = null;
  clearProposal(cmView);
}

function acceptProposal() {
  if (!activeProposal || !cmView || ed?.path !== activeProposal.file) return dismissProposal();
  const doc = cmView.state.doc;
  let { from, to, original, replacement } = activeProposal;
  const inBounds = from >= 0 && to <= doc.length && from <= to;
  if (!inBounds || doc.sliceString(from, to) !== original) {
    // Re-locate exact original passage if intermediate edits shifted offsets.
    const full = doc.toString();
    const first = full.indexOf(original);
    const second = first >= 0 ? full.indexOf(original, first + 1) : -1;
    if (first >= 0 && second < 0) {
      from = first;
      to = first + original.length;
    } else {
      dismissProposal();
      return setStatus("Passage changed since proposal was generated; select again to retry", "error");
    }
  }
  dismissProposal();
  cmView.dispatch({
    changes: { from, to, insert: replacement },
    selection: { anchor: from + replacement.length },
  });
  cmView.focus();
  setStatus("Applied revision (Cmd/Ctrl+Z to undo)", "fresh");
}

async function runRevision({ mode, ctx, note = "", instruction = "", title = "Polish proposal" }) {
  if (!ctx || !cmView) return setStatus("Place your cursor on a paragraph or select text first", "error");
  clearTimeout(pollTimer);
  const baseProp = {
    file: ctx.file,
    from: ctx.from,
    to: ctx.to,
    original: ctx.text,
    title,
    loading: `Asking ${agentInfo?.default || "local agent"}…`,
    onReject: dismissProposal,
  };
  activeProposal = baseProp;
  showProposal(cmView, baseProp);

  const r = await postJSON("agent/revise", {
    mode,
    text: ctx.text,
    before: ctx.before,
    after: ctx.after,
    note,
    instruction,
    agent: agentInfo?.default || undefined,
  }).catch(() => null);
  const res = r ? await readBody(r) : {};
  if (!r?.ok || !res.id) {
    activeProposal = { ...baseProp, loading: null, error: res.error || "Could not start agent revision" };
    showProposal(cmView, activeProposal);
    return;
  }

  const jobId = res.id;
  const poll = async () => {
    if (!activeProposal) return;
    const jr = await fetch(`agent/job?${new URLSearchParams({ id: jobId })}`, { cache: "no-store" }).catch(() => null);
    const job = jr ? await readBody(jr) : {};
    if (!jr?.ok) {
      activeProposal = { ...baseProp, loading: null, error: job.error || "Lost connection to agent job" };
      showProposal(cmView, activeProposal);
      return;
    }
    if (job.state === "running") {
      pollTimer = setTimeout(poll, 700);
      return;
    }
    if (job.state === "failed") {
      activeProposal = { ...baseProp, loading: null, error: job.error || "Agent revision failed" };
      showProposal(cmView, activeProposal);
      return;
    }
    if (ed?.path !== ctx.file) return dismissProposal();
    activeProposal = {
      ...baseProp,
      loading: null,
      replacement: job.replacement,
      rationale: job.rationale,
      changes: job.changes,
      agent: job.agent,
      onAccept: acceptProposal,
      onReject: dismissProposal,
      onRetry: () => {
        const extra = prompt("Feedback for the next attempt (optional):", instruction) ?? null;
        if (extra === null) return;
        runRevision({ mode, ctx, note, instruction: extra, title });
      },
    };
    showProposal(cmView, activeProposal);
  };
  pollTimer = setTimeout(poll, 500);
}

export async function reviseSelection(mode = "polish") {
  $("polishMenu").hidden = true;
  if (!agentReady()) return setStatus("No local agent CLI configured (install claude, codex, or pi)", "error");
  if (!editorShown()) await showEditor(true);
  const ctx = getSelectionContext();
  if (!ctx) return setStatus("Select text or place cursor inside a paragraph to polish", "error");
  let instruction = "";
  if (mode === "custom") {
    instruction = prompt("Instruction for the local agent:") || "";
    if (!instruction.trim()) return;
  }
  const title = mode === "proofread" ? "Proofread proposal" : mode === "custom" ? "Agent revision" : "Polish proposal";
  runRevision({ mode, ctx, instruction, title });
}

export async function proposeNoteFix(note) {
  if (!agentReady()) return setStatus("No local agent CLI configured", "error");
  if (!editorShown()) await showEditor(true, { path: note.file, line: note.line });
  else await openFile(note.file, note.line);
  // Let CodeMirror mount the target line, then select the surrounding paragraph.
  await new Promise((r) => setTimeout(r, 40));
  const ctx = getSelectionContext();
  if (!ctx) return setStatus(`Could not read paragraph at ${note.file}:${note.line}`, "error");
  runRevision({ mode: "note", ctx, note: `${note.author}: ${note.text}`, title: `Proposal for ${note.author}'s note` });
}

// Wire Polish dropdown & Accept shortcut (Cmd/Ctrl+Enter while a proposal is shown).
$("polishBtn").onclick = () => { $("polishMenu").hidden = !$("polishMenu").hidden; };
for (const b of document.querySelectorAll("#polishMenu button[data-mode]")) {
  b.onclick = () => reviseSelection(b.dataset.mode);
}

// Capture phase intercepts Cmd/Ctrl+Enter before CodeMirror's defaultKeymap (insertBlankLine).
window.addEventListener("keydown", (e) => {
  if (!activeProposal || activeProposal.loading || activeProposal.error) return;
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
    e.preventDefault();
    e.stopImmediatePropagation();
    acceptProposal();
  } else if (e.key === "Escape") {
    e.preventDefault();
    e.stopImmediatePropagation();
    dismissProposal();
  }
}, { capture: true });
