import { initBackground, PALETTE } from './scene.js';
import { initWalk } from './walk.js';

const HEX = PALETTE.map(n => '#' + n.toString(16).padStart(6, '0'));
const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const nf = new Intl.NumberFormat('de-DE');

let META = { bench_digits: 100000 };

const getJSON = async (u, opt) => (await fetch(u, opt)).json();

function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(t._t);
  t._t = setTimeout(() => t.classList.remove('show'), 2600);
}
async function copy(text, label = 'Kopiert!') {
  try { await navigator.clipboard.writeText(text); toast(label); }
  catch { toast('Kopieren ging nicht'); }
}
function clientId() {
  try {
    let id = localStorage.getItem('pi_cid');
    if (!id) {
      id = (window.crypto && crypto.randomUUID) ? crypto.randomUUID()
        : String(Math.random()).slice(2) + Date.now().toString(36);
      localStorage.setItem('pi_cid', id);
    }
    return id;
  } catch { return ''; }
}

const fmtMs = (s) => {
  const ms = s * 1000;
  if (ms < 1000) return Math.round(ms) + ' ms';
  return (ms / 1000).toFixed(2).replace('.', ',') + ' s';
};
const fmtDur = (s) => {
  s = Math.max(0, s);
  if (s < 10) return s.toFixed(2).replace('.', ',') + 's';
  if (s < 60) return Math.round(s) + 's';
  s = Math.floor(s); const m = Math.floor(s / 60), r = s % 60;
  if (m < 60) return `${m}m ${r}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
};

/* ───────── boot ───────── */
async function boot() {
  try { META = await getJSON('/api/meta'); } catch {}
  setupMeta();
  setupReveal();
  setupScrollProgress();
  setupStatus();

  const bg = initBackground($('#bg-canvas'));
  let head = { digits: '' };
  try { head = await getJSON('/api/digits?start=0&count=2400'); } catch {}
  if (head.digits) bg.setDigits(head.digits);

  setupCounters();
  setupBenchmark();
  await setupWalk();
  setupExplorer();
  setupCode();
  setupFooter(head.digits);
}

/* ───────── meta / links ───────── */
function setupMeta() {
  const repo = META.repo || 'https://github.com/ProfessorEngineergit/pi';
  $('#github-btn').href = repo;
  $('#code-github').href = repo + '/blob/main/web/worker.py';
  const fg = $('#footer-github'); fg.href = repo; fg.textContent = repo.replace('https://', '');
  if (META.cpu) $('#st-cpu').textContent = shortCpu(META.cpu);
  if (META.bench_digits) $('#bench-digits').textContent = nf.format(META.bench_digits);
  $('#footer-gmp').textContent = 'Spigot · pi.delivery';
}
function shortCpu(s) {
  return String(s).replace(/\(R\)|\(TM\)|CPU|Processor/gi, '').replace(/\s+/g, ' ').trim().slice(0, 22);
}

/* ───────── animated counters ───────── */
function setupCounters() {
  const io = new IntersectionObserver((entries) => {
    entries.forEach(e => {
      if (!e.isIntersecting) return;
      io.unobserve(e.target);
      const target = parseFloat(e.target.dataset.count || '0');
      const t0 = performance.now(), dur = 1500;
      const tick = (t) => {
        const p = Math.min((t - t0) / dur, 1);
        e.target.textContent = nf.format(Math.floor(target * (1 - Math.pow(1 - p, 3))));
        if (p < 1) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });
  }, { threshold: 0.5 });
  $$('.hstat-num[data-count]').forEach(el => io.observe(el));
}

/* ───────── live status (spigot ticker) ───────── */
const STATE_LABEL = {
  starting: 'startet…', streaming: 'rechnet Ziffern', resetting: 'Runde fertig, fängt neu an',
  error: 'Fehler', offline: 'gerade offline',
};
const STATE_CLASS = { resetting: 'idle', error: 'error', offline: 'offline' };

function setupStatus() {
  function render(s) {
    const state = s.state || 'offline';
    const stateEl = $('#st-state'), wrap = stateEl.closest('.status-state');
    stateEl.textContent = STATE_LABEL[state] || state;
    wrap.className = 'status-state ' + (STATE_CLASS[state] || '');

    const cur = s.current_digit || 0, lim = s.reset_limit || 0;
    $('#st-iter').textContent = 'Runde ' + nf.format(s.iteration || 1);
    $('#st-digit').textContent = nf.format(cur);
    const frac = lim ? cur / lim : 0;
    $('#st-bar').style.width = Math.min(100, Math.max(0, frac * 100)) + '%';
    $('#st-progress-label').textContent = lim
      ? `${Math.round(frac * 100)}% dieser Runde (bis ${nf.format(lim)} Stellen)` : '';

    const rec = s.recent || '';
    $('#st-stream').innerHTML = [...rec].map(c =>
      /\d/.test(c) ? `<span style="color:${HEX[+c]}">${c}</span>` : c).join('');

    $('#st-rate').textContent = s.rate_dps ? nf.format(s.rate_dps) : '—';
    $('#st-blocks').textContent = s.blocks_verified != null ? nf.format(s.blocks_verified) : '—';
    $('#st-text').textContent = s.status_text || '…';

    const v = s.verify_ok;
    $('#st-verify').innerHTML = v === true
      ? 'letzter Block <span style="color:#3ce6a0">✓ von pi.delivery bestätigt</span>'
      : v === false ? '<span style="color:var(--magenta)">Abweichung entdeckt!</span>'
      : 'Abgleich mit pi.delivery (gerade keine Antwort)';
    $('#st-round').textContent = lim ? `${nf.format(cur)} von ${nf.format(lim)} Stellen` : '—';

    $('#hero-digit').textContent = nf.format(cur);
    $('#hero-iter').textContent = nf.format(s.iteration || 1);
  }
  const poll = async () => { try { render(await getJSON('/api/status')); } catch {} };
  poll(); setInterval(poll, 1200);
}

/* ───────── benchmark + leaderboard ───────── */
function detectSpecs() {
  const ua = navigator.userAgent || '';
  let browser = 'Browser';
  if (/Edg\//.test(ua)) browser = 'Edge';
  else if (/OPR\//.test(ua)) browser = 'Opera';
  else if (/Firefox\//.test(ua)) browser = 'Firefox';
  else if (/Chrome\//.test(ua)) browser = 'Chrome';
  else if (/Safari\//.test(ua)) browser = 'Safari';
  let platform = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || '';
  let gpu = null;
  try {
    const c = document.createElement('canvas');
    const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
    const dbg = gl && gl.getExtension('WEBGL_debug_renderer_info');
    if (dbg) gpu = gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL);
  } catch {}
  return {
    cores: navigator.hardwareConcurrency || null,
    memory: navigator.deviceMemory || null,
    platform, browser, gpu,
  };
}
function specChips(s) {
  if (!s) return '';
  return [
    s.cores && `<span class="chip">${s.cores} Kerne</span>`,
    s.memory && `<span class="chip">${s.memory} GB RAM</span>`,
    s.gpu && `<span class="chip">${s.gpu}</span>`,
    s.browser && `<span class="chip">${s.browser}</span>`,
    s.platform && `<span class="chip">${s.platform}</span>`,
  ].filter(Boolean).join('');
}

async function loadLeaderboard(myName) {
  let data;
  try { data = await getJSON('/api/leaderboard?limit=100'); } catch { return; }
  const rows = data.entries || [];
  const el = $('#leaderboard');
  if (!rows.length) { el.innerHTML = '<span class="st-sub">Noch niemand hier — sei der oder die Erste!</span>'; return; }
  el.innerHTML = `<div class="lb-row lb-head"><span>#</span><span>Name</span><span>Maschine</span><span>Zeit</span></div>` +
    rows.map(e => {
      const mach = [e.specs?.cores && e.specs.cores + ' Kerne', e.specs?.gpu, e.specs?.browser].filter(Boolean).join(' · ') || '—';
      const me = myName && e.username === myName ? ' me' : '';
      return `<div class="lb-row${me}"><span class="lb-rank">${e.rank}</span>
        <span class="lb-name">${escapeHtml(e.username)}</span>
        <span class="lb-mach">${escapeHtml(mach)}</span>
        <span class="lb-time">${fmtMs(e.seconds)}</span></div>`;
    }).join('');
}
function escapeHtml(s) { return String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

function setupBenchmark() {
  const PHASES = [['bs', 'Reihe'], ['sqrt', 'Wurzel'], ['div', 'Division'], ['str', 'Konvert.']];
  const wrap = $('#bench-phases');
  wrap.style.gridTemplateColumns = `repeat(${PHASES.length}, 1fr)`;
  wrap.innerHTML = PHASES.map(([k, n]) =>
    `<div class="cphase" data-k="${k}"><span class="spinner"></span><div class="cphase-name">${n}</div><div class="cphase-time" data-t>—</div></div>`).join('');
  const phaseEl = (k) => wrap.querySelector(`.cphase[data-k="${k}"]`);
  const setActive = (k) => { $$('.cphase', wrap).forEach(c => c.classList.remove('active')); if (k) phaseEl(k)?.classList.add('active'); };

  const specs = detectSpecs();
  $('#bench-specs').innerHTML = specChips(specs);

  const btn = $('#bench-go'), name = $('#bench-name');
  try { name.value = localStorage.getItem('pi_bench_name') || ''; } catch {}
  let worker = null;

  btn.addEventListener('click', () => {
    const username = (name.value || '').trim();
    if (!username) { name.focus(); toast('Bitte erst einen Namen eintragen'); return; }
    try { localStorage.setItem('pi_bench_name', username); } catch {}
    if (worker) worker.terminate();
    $$('.cphase', wrap).forEach(c => { c.classList.remove('done', 'active'); c.querySelector('[data-t]').textContent = '—'; });
    $('#bench-time').textContent = '…'; $('#bench-rank').textContent = '—'; $('#bench-score').textContent = '—';
    btn.disabled = true; btn.textContent = '⏳ rechnet in deinem Browser…';
    setActive('bs');

    const digits = META.bench_digits || 100000;
    worker = new Worker('/js/pi-worker.js');
    worker.onmessage = async (ev) => {
      const m = ev.data;
      if (m.type === 'phase') {
        const el = phaseEl(m.name);
        if (el) { el.querySelector('[data-t]').textContent = m.seconds.toFixed(2) + 's'; el.classList.add('done'); el.classList.remove('active'); }
        setActive(PHASES[PHASES.findIndex(p => p[0] === m.name) + 1]?.[0]);
      } else if (m.type === 'done') {
        setActive(null);
        $('#bench-time').textContent = fmtMs(m.total_seconds);
        $('#bench-score').textContent = nf.format(Math.round(digits / m.total_seconds));
        btn.disabled = false; btn.textContent = '▶ nochmal';
        worker.terminate(); worker = null;
        try {
          const res = await getJSON('/api/benchmark', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, cid: clientId(), seconds: m.total_seconds, digits, specs }),
          });
          if (res.rank) $('#bench-rank').textContent = '#' + res.rank;
          toast(`${fmtMs(m.total_seconds)}, Platz #${res.rank} von ${res.total}`);
          loadLeaderboard(res.you?.username || username);
        } catch { toast('Ergebnis konnte nicht gesendet werden'); }
      } else if (m.type === 'error') {
        $('#bench-time').textContent = 'Fehler';
        btn.disabled = false; btn.textContent = '▶ Benchmark starten';
        worker.terminate(); worker = null;
      }
    };
    worker.onerror = () => { btn.disabled = false; btn.textContent = '▶ Benchmark starten'; if (worker) worker.terminate(); };
    worker.postMessage({ digits });
  });

  $('#board-refresh').addEventListener('click', () => loadLeaderboard());
  loadLeaderboard();
}

/* ───────── explorer (search + all-digits viewer) ───────── */
function setupExplorer() {
  $('#digit-palette').innerHTML = HEX.map((c, i) => `<i style="background:${c}">${i}</i>`).join('');

  const input = $('#search-input'), res = $('#search-result');
  $('#quick-tags').innerHTML = ['141592', '271828', '123456789', '42', '00000', '999999']
    .map(t => `<span class="tag" data-q="${t}">${t}</span>`).join('');
  $$('#quick-tags .tag').forEach(t => t.addEventListener('click', () => { input.value = t.dataset.q; doSearch(); }));

  async function doSearch() {
    const q = input.value.trim();
    if (!/^\d+$/.test(q)) { res.innerHTML = '<span style="color:var(--magenta)">Bitte nur Ziffern.</span>'; return; }
    res.innerHTML = '<span class="st-sub">suche…</span>';
    try {
      const r = await getJSON('/api/search?q=' + encodeURIComponent(q));
      if (r.found) {
        const ctx = (r.context || '').replace(q, `<mark>${q}</mark>`);
        res.innerHTML = `Gefunden an Position <span class="hit">${nf.format(r.position + 1)}</span>
          <div class="ctx">…${ctx}…</div>
          <button class="btn btn-ghost btn-sm" id="goto-hit" style="margin-top:.8rem">im Explorer zeigen ▸</button>`;
        $('#goto-hit').addEventListener('click', () => jumpTo(Math.max(0, r.position)));
      } else {
        res.innerHTML = `<span class="st-sub">„${q}" steckt nicht in den ersten ${nf.format(r.total)} Stellen.</span>`;
      }
    } catch { res.innerHTML = '<span style="color:var(--magenta)">Suche gerade nicht möglich.</span>'; }
  }
  $('#search-btn').addEventListener('click', doSearch);
  input.addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(); });

  const jump = $('#jump-input'), grid = $('#digit-grid');
  let count = 300, curStart = 0, lastTotal = META.total_digits || 0;
  let following = false, followTimer = null;

  async function currentTotal() {
    try { return (await getJSON('/api/digits?start=0&count=1')).total || lastTotal; } catch { return lastTotal; }
  }
  async function render(start) {
    start = Math.max(0, Math.floor(start));
    let resp;
    try { resp = await getJSON(`/api/digits?start=${start}&count=${count}`); }
    catch { resp = { digits: '', total: lastTotal, start }; }
    const total = resp.total ?? lastTotal; lastTotal = total;
    if (!resp.digits && total > 0 && start > 0) return render(Math.max(0, total - count));
    curStart = resp.start ?? start; jump.value = curStart;
    const d = resp.digits || '';
    grid.innerHTML = [...d].map((ch, i) => `<span style="color:${HEX[+ch]};animation-delay:${i * 2}ms">${ch}</span>`).join('');
    $('#digit-range').textContent = total
      ? `Stelle ${nf.format(curStart + 1)} bis ${nf.format(curStart + d.length)} von ${nf.format(total)}`
      : 'noch kein Datensatz da';
    // in the live view, keep the newest digits in sight at the bottom
    if (following && grid.classList.contains('expanded')) grid.scrollTop = grid.scrollHeight;
  }
  function stopFollow() { following = false; if (followTimer) clearInterval(followTimer); followTimer = null; $('#jump-follow').classList.remove('following'); }
  async function toLast() { const t = await currentTotal(); await render(Math.max(0, t - count)); }
  async function startFollow() {
    following = true; $('#jump-follow').classList.add('following');
    if (!grid.classList.contains('expanded')) {        // open up so you see a long tail
      grid.classList.add('expanded'); count = 2400;
      $('#jump-expand').textContent = '⤡ einklappen';
    }
    await toLast();
    followTimer = setInterval(async () => { const t = await currentTotal(); if (t !== lastTotal) await render(Math.max(0, t - count)); }, 2500);
  }

  $('#jump-first').addEventListener('click', () => { stopFollow(); render(0); });
  $('#jump-last').addEventListener('click', () => { stopFollow(); toLast(); });
  $$('#explorer [data-jump]').forEach(b => b.addEventListener('click', () => { stopFollow(); render(curStart + (+b.dataset.jump) * count); }));
  $('#jump-rand').addEventListener('click', async () => { stopFollow(); const t = await currentTotal(); render(Math.floor(Math.random() * Math.max(1, t - count))); });
  $('#jump-follow').addEventListener('click', () => following ? stopFollow() : startFollow());
  $('#jump-expand').addEventListener('click', (e) => {
    const expanded = grid.classList.toggle('expanded');
    count = expanded ? 2400 : 300;
    e.target.textContent = expanded ? '⤡ einklappen' : '⤢ aufklappen';
    render(curStart);
  });
  jump.addEventListener('change', () => { stopFollow(); render(+jump.value || 0); });
  render(0);

  function jumpTo(s) { stopFollow(); render(s); document.getElementById('explorer').scrollIntoView({ behavior: 'smooth' }); }
  setupExplorer.jumpTo = jumpTo;
  window._jumpTo = jumpTo;
}
function jumpTo(s) { (setupExplorer.jumpTo || (() => {}))(s); }

/* ───────── random walk ───────── */
async function setupWalk() {
  const walk = initWalk($('#walk-canvas'));
  $('#walk-legend').innerHTML = HEX.map((c, i) => `<span class="legend-dot"><i style="background:${c}"></i>${i}</span>`).join('');
  const steps = $('#walk-steps'), start = $('#walk-start');
  start.max = Math.max(0, (META.total_digits || 1000000) - 40000);
  const sync = () => { $('#walk-steps-val').textContent = nf.format(+steps.value); $('#walk-start-val').textContent = nf.format(+start.value); };
  steps.addEventListener('input', sync); start.addEventListener('input', sync); sync();
  async function draw() {
    let d = '';
    try { d = (await getJSON(`/api/digits?start=${+start.value}&count=${+steps.value}`)).digits; } catch {}
    if (d) await walk.setWalk(d);
  }
  $('#walk-regen').addEventListener('click', draw);
  await draw();
}

/* ───────── code viewer ───────── */
function setupCode() {
  const block = $('#code-block');
  let src = '';
  (async () => {
    try { src = await (await fetch('/api/source')).text(); }
    catch { src = '# Quellcode konnte nicht geladen werden.'; }
    block.textContent = src;
    if (window.hljs) window.hljs.highlightElement(block);
  })();
  $('#copy-code').addEventListener('click', () => copy(src, 'Code kopiert!'));
}

/* ───────── footer + reveal + scroll ───────── */
function setupFooter(digits) { if (digits) $('#footer-pi').textContent = '3,' + digits.slice(0, 1200); }
function setupReveal() {
  $$('.section-head, .card').forEach(el => el.classList.add('reveal'));
  const io = new IntersectionObserver((es) => es.forEach(e => { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } }), { threshold: 0.12 });
  $$('.reveal').forEach(el => io.observe(el));
}
function setupScrollProgress() {
  const bar = $('#scroll-progress');
  const upd = () => { const h = document.documentElement; bar.style.width = (h.scrollTop / (h.scrollHeight - h.clientHeight || 1) * 100) + '%'; };
  window.addEventListener('scroll', upd, { passive: true }); upd();
}

boot();
