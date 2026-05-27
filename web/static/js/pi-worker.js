/* Client-side pi computation — runs in the VISITOR'S browser, never on the
   server. Chudnovsky + binary splitting with native BigInt.
   Messages out: {type:'phase'|'progress'|'done'|'error', ...} */

const C3_OVER_24 = 640320n ** 3n / 24n;
const A = 13591409n;
const B = 545140134n;
const HARD_CAP = 300000;          // safety cap for the in-browser demo

function isqrt(n) {
  if (n < 2n) return n < 0n ? 0n : n;
  const bits = n.toString(16).length * 4;
  let x = 1n << BigInt((bits + 2) >> 1);
  while (true) {
    const y = (x + n / x) >> 1n;
    if (y >= x) return x;
    x = y;
  }
}

// binary splitting of terms [a, b)  ->  [P, Q, T]
function bs(a, b) {
  if (b - a === 1n) {
    let P, Q;
    if (a === 0n) { P = 1n; Q = 1n; }
    else {
      P = (6n * a - 5n) * (2n * a - 1n) * (6n * a - 1n);
      Q = a * a * a * C3_OVER_24;
    }
    let T = P * (A + B * a);
    if (a & 1n) T = -T;
    return [P, Q, T];
  }
  const m = (a + b) >> 1n;
  const [P1, Q1, T1] = bs(a, m);
  const [P2, Q2, T2] = bs(m, b);
  return [P1 * P2, Q1 * Q2, Q2 * T1 + P1 * T2];
}

function combine(L, R) {
  return [L[0] * R[0], L[1] * R[1], R[1] * L[2] + L[0] * R[2]];
}

function compute(digits) {
  const t0 = performance.now();
  const N = Math.floor(digits / 14.1816474627 + 1);

  // ---- series (binary splitting), reported in segments for a progress bar ----
  const SEG = Math.min(64, Math.max(1, N));
  let acc = null;
  for (let s = 0; s < SEG; s++) {
    const lo = BigInt(Math.floor((N * s) / SEG));
    const hi = BigInt(Math.floor((N * (s + 1)) / SEG));
    if (hi <= lo) continue;
    const r = bs(lo, hi);
    acc = acc ? combine(acc, r) : r;
    self.postMessage({ type: 'progress', phase: 'bs', frac: (s + 1) / SEG });
  }
  const [, Q, T] = acc;
  const tSeries = performance.now();
  self.postMessage({ type: 'phase', name: 'bs', seconds: (tSeries - t0) / 1000 });

  // ---- square root ----
  const one = 10n ** BigInt(digits);
  const sqrtC = isqrt(10005n * one * one);
  const tSqrt = performance.now();
  self.postMessage({ type: 'phase', name: 'sqrt', seconds: (tSqrt - tSeries) / 1000 });

  // ---- division ----
  const piInt = (Q * 426880n * sqrtC) / T;
  const tDiv = performance.now();
  self.postMessage({ type: 'phase', name: 'div', seconds: (tDiv - tSqrt) / 1000 });

  // ---- string ----
  const raw = piInt.toString();
  const full = raw[0] + '.' + raw.slice(1, digits + 1);
  const tStr = performance.now();
  self.postMessage({ type: 'phase', name: 'str', seconds: (tStr - tDiv) / 1000 });

  self.postMessage({
    type: 'done',
    digits,
    total_seconds: (tStr - t0) / 1000,
    preview: full.slice(0, 1002),
    full,
  });
}

self.onmessage = (e) => {
  try {
    let digits = Math.max(1, Math.floor(e.data.digits || 1000));
    if (digits > HARD_CAP) digits = HARD_CAP;
    compute(digits);
  } catch (err) {
    self.postMessage({ type: 'error', message: String(err && err.message || err) });
  }
};
