// Requests to live.py: a POST of JSON, and reading an answer, which may also come from a proxy's sign-in page, or from a
// proxy or the hub that can't reach the preview just now.

const KEEPALIVE = 60000; // (a keepalive request outlives the page, for a body of up to 64 KB)
export const fitsKeepalive = (body) => new TextEncoder().encode(JSON.stringify(body)).length < KEEPALIVE;
export const postJSON = (path, body, keepalive = false) => {
  const data = JSON.stringify(body);
  return fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: data,
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
