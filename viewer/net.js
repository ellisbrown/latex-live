// Requests to live.py: a POST of JSON, and reading an answer, which may also come from a proxy's sign-in page, or from a
// proxy or the hub that can't reach the preview just now.

import { SIGNED_OUT } from "./page.js";

const KEEPALIVE = 60000; // (a keepalive request outlives the page, for a body of up to 64 KB)
export const fitsKeepalive = (body) => new TextEncoder().encode(JSON.stringify(body)).length < KEEPALIVE;
export const postJSON = (path, body, keepalive = false, signal = undefined) => {
  const data = JSON.stringify(body);
  return fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: data, signal,
                       keepalive: keepalive && new TextEncoder().encode(data).length < KEEPALIVE });
};

// A response's body: its JSON, or {error} with a plain-text or HTML error's message (and html: true
// for a page, which may also be a proxy's sign-in page rather than the server's answer).
export async function readBody(r) {
  const text = await r.text();
  try { return JSON.parse(text); } catch { /* not JSON */ }
  const page = /^\s*</.test(text) && new DOMParser().parseFromString(text, "text/html");
  const message = page ? [...page.querySelectorAll("p")].map((p) => p.textContent).find((s) => s.startsWith("Message: "))?.slice(9) : text.trim();
  return { error: message || `failed (${r.status})`, ...(page ? { html: true } : {}) };
}
// Whether an answer came from something other than the server, as when a proxy whose sign-in has
// expired redirects to its sign-in page: a redirect, a 401 (live.py sends none), or a page that
// claims success.
export const signInPage = (r, body) => r.redirected || r.status === 401 || (r.ok && !!body.html);
// Whether the server, the proxy or the hub couldn't reach the preview just now (worth sending again
// as it is): 502-504, or the hub's 404 for a preview that is not running (e.g. restarting).
export const unreachable = (r, body) => (r.status >= 502 && r.status <= 504 && !body.retry) || (r.status === 404 && /^no preview of /.test(body.error || ""));

// A request that a panel makes, and its answer as {ok, status, body, error}: ok only for the server's own JSON answer
// of success, else error says why in words to show: a sign-in page (SIGNED_OUT), no answer or a preview that can't be
// reached just now, too many requests at once (429: the server takes only so many of one person's at a time), or the
// server's own message. signal: an AbortSignal that ends the wait (a timeout, a request cancelled), which counts as no
// answer.
export const getJSON = (path, { signal } = {}) => answer(fetch(path, { cache: "no-store", signal }));
export const sendJSON = (path, body, { signal } = {}) => answer(postJSON(path, body, false, signal));
const NO_ANSWER = "can't reach the preview server";
async function answer(sent) {
  let r;
  try { r = await sent; } catch { return { ok: false, status: 0, body: {}, error: NO_ANSWER }; }
  const body = (await readBody(r).catch(() => null)) ?? { error: `failed (${r.status})` };
  const fail = (error) => ({ ok: false, status: r.status, body, error });
  if (signInPage(r, body)) return fail(SIGNED_OUT);
  if (unreachable(r, body)) return fail(NO_ANSWER);
  if (r.status === 429) return fail(body.error && !body.html ? body.error : "The preview is busy: try again in a moment");
  if (!r.ok || !body || typeof body !== "object" || body.html) return fail(body?.error || `failed (${r.status})`);
  return { ok: true, status: r.status, body, error: "" };
}
