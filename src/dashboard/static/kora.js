/* Kora Terminal client runtime: icons, clock, live polling, search, avatar
   moods, toasts, decision toggles, and the canvas candlestick renderer. */
(function () {
  const K = (window.KORA = window.KORA || {});
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const COLORS = { g: '#9bd35a', r: '#ff6b61', a: '#ffc34d', b: '#5ac8fa', m: 'rgba(235,235,245,.55)' };
  K.$ = $; K.$$ = $$; K.COLORS = COLORS;

  K.icons = () => window.lucide && window.lucide.createIcons();
  K.icons();

  // ---- phone menu (sidebar as a drawer)
  const sb = $('.sidebar'), scrim = $('#scrim');
  const menu = (on) => { if (sb) sb.classList.toggle('open', on); if (scrim) scrim.classList.toggle('open', on); };
  ['#menu-btn', '#tab-more'].forEach((s) => { const b = $(s); if (b) b.onclick = () => menu(!sb.classList.contains('open')); });
  if (scrim) scrim.onclick = () => menu(false);

  // ---- clock
  const clock = $('#clock');
  const tickClock = () => clock && (clock.textContent = new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' }));
  tickClock(); setInterval(tickClock, 1000);

  // ---- toast
  let toastT;
  K.toast = (msg) => {
    const t = $('#toast'); if (!t) return;
    t.textContent = msg; t.classList.remove('on'); void t.offsetWidth; t.classList.add('on');
    clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove('on'), 2600);
  };

  // ---- avatar moods (mouth path + eyebrow offset), overrides last 3.2s
  const MOODS = {
    content: { d: 'M18.5 31.5 Q24 35 29.5 31.5', f: 'none', b: '0px' },
    joy: { d: 'M17.5 31 Q24 39 30.5 31 Q24 33.5 17.5 31', f: '#5b2c2c', b: '-1.5px' },
    neutral: { d: 'M19 32.5 L29 32.5', f: 'none', b: '0px' },
    concerned: { d: 'M18.5 33.5 Q24 30.8 29.5 33.5', f: 'none', b: '1px' },
    sad: { d: 'M18.5 34.5 Q24 30.5 29.5 34.5', f: 'none', b: '1.4px' },
  };
  let baseMood = K.mood || 'content', moodT;
  const applyMood = (m) => {
    const av = $('#kora-avatar'); if (!av) return;
    const s = MOODS[m] || MOODS.content;
    const mouth = $('.av-mouth', av), brows = $('.av-brows', av);
    mouth.setAttribute('d', s.d); mouth.setAttribute('fill', s.f);
    brows.style.transform = `translateY(${s.b})`;
    av.classList.toggle('joy', m === 'joy');
  };
  K.setMood = (m) => { applyMood(m); clearTimeout(moodT); moodT = setTimeout(() => applyMood(baseMood), 3200); };
  applyMood(baseMood);

  // ---- JSON POST helper
  K.post = async (url, body) => {
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
    return r.json();
  };

  // ---- live polling: every element with data-live="key" is refreshed
  K.quotes = {};
  K.onLive = [];
  async function poll() {
    try {
      const r = await fetch('/api/live', { cache: 'no-store' });
      if (!r.ok) return;
      const d = await r.json();
      for (const el of $$('[data-live]')) {
        const v = d.values[el.dataset.live];
        if (!v) continue;
        if (el.textContent !== v[0]) el.textContent = v[0];
        if (v[1]) { el.classList.remove('c-g', 'c-r', 'c-a'); el.classList.add('c-' + v[1]); }
      }
      K.quotes = d.quotes || {};
      const badge = $('[data-live-count="positions"]'); if (badge) badge.textContent = d.positions;
      // feed + agent chips
      const fc = $('#feed-chip'), ft = $('#feed-chip-text');
      if (fc && ft) {
        fc.className = 'chipx ' + (d.feed.online ? 'g' : 'm');
        fc.querySelector('.dot').className = 'dot ' + (d.feed.online ? 'bg-g pulse' : 'bg-m');
        ft.textContent = d.feed.online ? (({sim: 'Simulated', mt5: 'MT5 terminal', deriv: 'Deriv feed', metaapi: 'MetaApi'}[d.feed.source] || d.feed.source) + (d.feed.latency ? ` · ${Math.round(d.feed.latency)} ms` : '')) : 'Feed offline';
      }
      const ac = $('#agent-chip'), at = $('#agent-chip-text');
      if (ac && at) {
        ac.className = 'chipx ' + ({ RUNNING: 'g', PAUSED: 'a', STOPPED: 'r' }[d.feed.state] || 'm');
        at.textContent = 'Kora · ' + d.feed.state.charAt(0) + d.feed.state.slice(1).toLowerCase();
      }
      if (d.mood !== baseMood) { baseMood = d.mood; applyMood(baseMood); }
      // new Kora decisions -> bell count, joy on fresh high-confidence setups
      if (d.last_decision && d.last_decision.id > K.lastDecisionId) {
        const ld = d.last_decision;
        const n = $('#bell-n');
        if (n) { n.textContent = Number(n.textContent || 0) + 1; n.style.display = 'block'; }
        if (ld.tone === 'g') { K.setMood('joy'); K.toast(ld.action); }
        K.lastDecisionId = ld.id;
      }
      K.onLive.forEach((fn) => fn(d));
    } catch (e) { /* offline - keep last values */ }
  }
  setInterval(poll, 2000);
  setTimeout(poll, 300);

  // ---- search (instruments + pages), keyboard navigable, "/" focuses
  const si = $('#search-input'), sr = $('#search-results');
  let sel = 0, hits = [];
  const renderSearch = () => {
    const q = si.value.trim().toLowerCase();
    if (!q) { sr.classList.remove('open'); return; }
    hits = K.search.filter((x) => (x.label + ' ' + x.sub).toLowerCase().includes(q)).slice(0, 8);
    sel = Math.min(sel, Math.max(0, hits.length - 1));
    sr.innerHTML = hits.length ? hits.map((h, i) => `<a href="${h.href}" class="${i === sel ? 'sel' : ''}"><b class="mono">${h.label}</b><span class="muted">${h.sub}</span><span class="k">${h.kind}</span></a>`).join('')
      : '<div class="empty" style="padding:10px">No match</div>';
    sr.classList.add('open');
  };
  if (si) {
    si.addEventListener('input', () => { sel = 0; renderSearch(); });
    si.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') { sel = Math.min(sel + 1, hits.length - 1); renderSearch(); e.preventDefault(); }
      if (e.key === 'ArrowUp') { sel = Math.max(sel - 1, 0); renderSearch(); e.preventDefault(); }
      if (e.key === 'Enter' && hits[sel]) location.href = hits[sel].href;
      if (e.key === 'Escape') { si.value = ''; renderSearch(); si.blur(); }
    });
    si.addEventListener('blur', () => setTimeout(() => sr.classList.remove('open'), 150));
    document.addEventListener('keydown', (e) => { if (e.key === '/' && document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'TEXTAREA') { e.preventDefault(); si.focus(); } });
  }

  // ---- expandable rows (decision log, positions)
  document.addEventListener('click', (e) => {
    const t = e.target.closest('[data-toggle]');
    if (!t) return;
    e.preventDefault();
    const box = t.closest(t.dataset.toggle);
    if (!box) return;
    box.classList.toggle('open');
    const chev = t.querySelector('.chev'); if (chev) chev.textContent = box.classList.contains('open') ? '▾' : '▸';
  });

  // ---- local time rendering for ISO timestamps
  for (const el of $$('[data-localtime]')) {
    const d = new Date(el.dataset.localtime);
    const fmt = el.dataset.fmt || 'time';
    el.textContent = fmt === 'datetime' ? d.toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })
      : d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
  }
  // ---- live countdowns ("in 2h 08m")
  const tickCountdowns = () => {
    for (const el of $$('[data-countdown]')) {
      const ms = new Date(el.dataset.countdown) - Date.now();
      if (ms <= 0) { el.textContent = ms > -3600e3 ? 'just released' : ''; continue; }
      const m = Math.round(ms / 60000);
      el.textContent = m >= 1440 ? '' : `in ${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
    }
  };
  tickCountdowns(); setInterval(tickCountdowns, 15000);

  // =================================================================== chart
  K.fm = (n, d) => Number(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });


  // tiny inline candle sparkline (journal thumbnails) from <svg data-candles='[...]'>
  for (const svg of $$('svg[data-candles]')) {
    const cs = JSON.parse(svg.dataset.candles || '[]'); if (!cs.length) continue;
    const W = 300, H = 74; let mn = Infinity, mx = -Infinity;
    cs.forEach((k) => { mn = Math.min(mn, k.low); mx = Math.max(mx, k.high); });
    const Y = (v) => 4 + (H - 8) * (1 - (v - mn) / ((mx - mn) || 1)), cw = W / cs.length;
    const o = parseInt(svg.dataset.openIdx || '-1'), cl = parseInt(svg.dataset.closeIdx || '-1');
    let html = '';
    if (o >= 0 && cl >= o) html += `<rect x="${o * cw}" y="0" width="${(cl - o + 1) * cw}" height="${H}" fill="rgba(41,171,226,.09)"/>`;
    cs.forEach((k, i) => {
      const col = k.close >= k.open ? '#9bd35a' : '#ff6b61', cx = i * cw + cw / 2;
      html += `<line x1="${cx}" x2="${cx}" y1="${Y(k.high)}" y2="${Y(k.low)}" stroke="${col}" stroke-width=".8"/>`;
      const y1 = Y(Math.max(k.open, k.close)), y2 = Y(Math.min(k.open, k.close));
      html += `<rect x="${cx - cw * 0.32}" y="${y1}" width="${Math.max(1, cw * 0.64)}" height="${Math.max(0.8, y2 - y1)}" fill="${col}"/>`;
    });
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`); svg.setAttribute('preserveAspectRatio', 'none'); svg.innerHTML = html;
  }
})();
