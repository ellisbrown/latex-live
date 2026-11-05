// Myers' diff: of lines, for the source pane's merges (editor.js), and of words, for the change highlights (changes.js).

// Myers' diff of two lists of lines, as hunks [a0, a1, b0, b1]: A[a0..a1) became B[b0..b1). Past
// 1000 edits (between the common start and end), one hunk for everything in between (marked rough).
export function lineHunks(A, B) {
  let s = 0, ea = A.length, eb = B.length;
  while (s < ea && s < eb && A[s] === B[s]) s++;
  while (ea > s && eb > s && A[ea - 1] === B[eb - 1]) { ea--; eb--; }
  const N = ea - s, M = eb - s;
  if (!N || !M) return N || M ? [[s, ea, s, eb]] : [];
  const ids = new Map(), id = (line) => ids.get(line) ?? ids.set(line, ids.size).get(line); // (lines as numbers)
  const a = Int32Array.from(A.slice(s, ea), id), b = Int32Array.from(B.slice(s, eb), id);
  const LIMIT = 1000, off = LIMIT + 2, v = new Int32Array(2 * off + 1), trace = [];
  let D = -1;
  outer: for (let d = 0; d <= LIMIT; d++) {
    trace.push(v.slice(off - d - 1, off + d + 2)); // v[k] for k in [-d-1, d+1], before step d
    for (let k = -d; k <= d; k += 2) {
      let x = k === -d || (k !== d && v[off + k - 1] < v[off + k + 1]) ? v[off + k + 1] : v[off + k - 1] + 1;
      let y = x - k;
      while (x < N && y < M && a[x] === b[y]) { x++; y++; }
      v[off + k] = x;
      if (x >= N && y >= M) { D = d; break outer; }
    }
  }
  if (D < 0) return Object.assign([[s, ea, s, eb]], { rough: true });
  const edits = []; // [x, y, inserted]: B[y] went in before A[x], or A[x] went out (before B[y])
  for (let d = D, x = N, y = M; d > 0; d--) {
    const t = trace[d], at = (k) => t[k + d + 1], k = x - y;
    const prevK = k === -d || (k !== d && at(k - 1) < at(k + 1)) ? k + 1 : k - 1;
    const prevX = at(prevK), prevY = prevX - prevK;
    while (x > prevX && y > prevY) { x--; y--; }
    edits.push([prevX, prevY, x === prevX]);
    x = prevX; y = prevY;
  }
  const hunks = [];
  for (const [x, y, inserted] of edits.reverse()) {
    const last = hunks.at(-1);
    if (last && last[1] === s + x && last[3] === s + y) last[inserted ? 3 : 1]++;
    else hunks.push([s + x, s + x + (inserted ? 0 : 1), s + y, s + y + (inserted ? 1 : 0)]);
  }
  return hunks;
}

// Myers diff of string arrays: {inserted: indices into b, deletedAt: indices into b before which
// words of a were removed}, or null when there are too many changes to show usefully.
// Words: {inserted: indices into b, deletedAt: indices into b before which words of a went}.
// Myers' diff; one too large for it (many edits against HEAD) is split at anchors (words that
// occur once in each, in the same order) and diffed piece by piece.
export function diffWords(a, b, depth = 0) {
  let s = 0;
  while (s < a.length && s < b.length && a[s] === b[s]) s++;
  let ea = a.length, eb = b.length;
  while (ea > s && eb > s && a[ea - 1] === b[eb - 1]) { ea--; eb--; }
  const A = a.slice(s, ea), B = b.slice(s, eb);
  const r = myers(A, B) || (depth < 3 && byAnchors(A, B, depth)) || replaced(A, B);
  return { inserted: r.inserted.map((j) => s + j), deletedAt: [...new Set(r.deletedAt)].map((j) => s + j) };
}
function replaced(A, B) { // no alignment found: all of B is new
  return { inserted: B.map((_, j) => j), deletedAt: A.length ? [0] : [] };
}
function byAnchors(A, B, depth) {
  const count = (xs) => xs.reduce((m, x) => m.set(x, (m.get(x) || 0) + 1), new Map());
  const ca = count(A), cb = count(B), inB = new Map();
  B.forEach((x, j) => { if (ca.get(x) === 1 && cb.get(x) === 1) inB.set(x, j); });
  const pairs = [];
  A.forEach((x, i) => { if (inB.has(x)) pairs.push([i, inB.get(x)]); });
  // The longest run of pairs increasing in B too (patience sorting).
  const tails = [], prev = [];
  pairs.forEach(([, j], p) => {
    let lo = 0, hi = tails.length;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (pairs[tails[mid]][1] < j) lo = mid + 1; else hi = mid; }
    prev[p] = lo ? tails[lo - 1] : -1;
    tails[lo] = p;
  });
  if (!tails.length) return null;
  const anchors = [];
  for (let p = tails.at(-1); p >= 0; p = prev[p]) anchors.push(pairs[p]);
  anchors.reverse().push([A.length, B.length]);
  const inserted = [], deletedAt = [];
  let i0 = 0, j0 = 0;
  for (const [i, j] of anchors) {
    const r = diffWords(A.slice(i0, i), B.slice(j0, j), depth + 1);
    inserted.push(...r.inserted.map((k) => j0 + k));
    deletedAt.push(...r.deletedAt.map((k) => j0 + k));
    i0 = i + 1; j0 = j + 1;
  }
  return { inserted, deletedAt };
}
function myers(A, B) {
  const N = A.length, M = B.length;
  const inserted = [], deletedAt = [];
  if (!N || !M) return replaced(A, B);
  const LIMIT = 3000, off = LIMIT + 2, trace = [];
  const v = new Int32Array(2 * off + 1);
  let D = -1;
  outer: for (let d = 0; d <= LIMIT; d++) {
    trace.push(v.slice(off - d - 1, off + d + 2)); // v[k] for k in [-d-1, d+1], before step d
    for (let k = -d; k <= d; k += 2) {
      let x = k === -d || (k !== d && v[off + k - 1] < v[off + k + 1]) ? v[off + k + 1] : v[off + k - 1] + 1;
      let y = x - k;
      while (x < N && y < M && A[x] === B[y]) { x++; y++; }
      v[off + k] = x;
      if (x >= N && y >= M) { D = d; break outer; }
    }
  }
  if (D < 0) return null;
  let x = N, y = M;
  for (let d = D; d > 0; d--) {
    const t = trace[d], at = (k) => t[k + d + 1];
    const k = x - y;
    const prevK = k === -d || (k !== d && at(k - 1) < at(k + 1)) ? k + 1 : k - 1;
    const prevX = at(prevK), prevY = prevX - prevK;
    while (x > prevX && y > prevY) { x--; y--; }
    if (x === prevX) inserted.push(prevY); // B[prevY] was inserted
    else deletedAt.push(y);                // A[prevX] was deleted before B[y]
    x = prevX; y = prevY;
  }
  return { inserted: inserted.reverse(), deletedAt: [...new Set(deletedAt)].reverse() };
}
