/* Kora Terminal client runtime: icons, clock, live polling, search, avatar
   moods, toasts, decision toggles, and the canvas candlestick renderer. */
(function () {
  const K = (window.KORA = window.KORA || {});
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const COLORS = { g: '#93c93f', r: '#e0655f', a: '#fdb813', b: '#29abe2', m: 'rgba(230,237,233,.55)' };
  K.$ = $; K.$$ = $$; K.COLORS = COLORS;

  K.icons = () => window.lucide && window.lucide.createIcons();
  K.icons();

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
        ft.textContent = d.feed.online ? ((d.feed.source === 'sim' ? 'Simulated' : 'MetaApi') + (d.feed.latency ? ` · ${Math.round(d.feed.latency)} ms` : '')) : 'Feed offline';
      }
      const ac = $('#agent-chip'), at = $('#agent-chip-text');
      if (ac && at) {
        ac.className = 'chipx ' + ({ RUNNING: 'g', PAUSED: 'a', STOPPED: 'r' }[d.feed.state] || 'm');
        at.textContent = 'Kora ' + d.feed.state;
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

  class KoraChart {
    constructor(canvas, opts = {}) {
      this.c = canvas; this.opts = opts; this.data = null; this.ind = { vwap: true, ema: true, levels: true, kora: true };
      this.mouse = null;
      canvas.addEventListener('mousemove', (e) => { const r = canvas.getBoundingClientRect(); this.mouse = { x: e.clientX - r.left, y: e.clientY - r.top }; this.draw(); });
      canvas.addEventListener('mouseleave', () => { this.mouse = null; this.draw(); });
      window.addEventListener('resize', () => this.draw());
    }
    set(data) { this.data = data; this.draw(); }
    live(price) {
      if (!this.data || !price) return;
      const k = this.data.candles[this.data.candles.length - 1];
      k.close = price; k.high = Math.max(k.high, price); k.low = Math.min(k.low, price);
      this.draw();
    }
    draw() {
      const c = this.c, d = this.data; if (!c || !d || !d.candles.length) return;
      const dpr = window.devicePixelRatio || 1, W = c.clientWidth || 900, H = c.clientHeight || 520;
      if (c.width !== Math.round(W * dpr) || c.height !== Math.round(H * dpr)) { c.width = Math.round(W * dpr); c.height = Math.round(H * dpr); }
      const x = c.getContext('2d'); x.setTransform(dpr, 0, 0, dpr, 0, 0); x.clearRect(0, 0, W, H);
      const data = d.candles.slice(-130), n = data.length, dd = d.digits > 4 ? 5 : d.digits;
      const padR = 78, padB = 20;
      let mn = Infinity, mx = -Infinity;
      data.forEach((k) => { mn = Math.min(mn, k.low); mx = Math.max(mx, k.high); });
      const ovs = (d.overlays || []).filter((o) => this.ind.kora || !o.kora);
      const span0 = mx - mn;
      ovs.forEach((o) => { if (Math.abs(o.price - (mn + mx) / 2) < span0 * 2.5) { mn = Math.min(mn, o.price); mx = Math.max(mx, o.price); } });
      const rg = mx - mn || 1; mn -= rg * 0.04; mx += rg * 0.04;
      const cw = (W - padR) / n, Y = (v) => (H - padB) * (1 - (v - mn) / (mx - mn));
      x.font = '10px "IBM Plex Mono",monospace'; x.textAlign = 'left';
      for (let i = 0; i <= 5; i++) {
        const v = mn + (mx - mn) * i / 5, y = Y(v);
        x.strokeStyle = 'rgba(255,255,255,.055)'; x.beginPath(); x.moveTo(0, y); x.lineTo(W - padR, y); x.stroke();
        x.fillStyle = 'rgba(230,237,233,.45)'; x.fillText(K.fm(v, dd), W - padR + 6, y + 3);
      }
      x.fillStyle = 'rgba(230,237,233,.38)'; x.textAlign = 'center';
      const p2 = (v) => String(v).padStart(2, '0');
      for (let i = 10; i < n; i += 24) {
        const t = new Date(data[i].time);
        const lab = (d.tf === 'D1' || d.tf === 'H4') ? `${t.getDate()}/${t.getMonth() + 1}`
          : d.tf === 'H1' ? `${t.getDate()}/${t.getMonth() + 1} ${p2(t.getHours())}h` : `${p2(t.getHours())}:${p2(t.getMinutes())}`;
        x.fillText(lab, i * cw + cw / 2, H - 6);
      }
      x.textAlign = 'left';
      // key levels (faint bands)
      if (this.ind.levels) (d.levels || []).forEach((lv) => {
        const y = Y(lv.price); if (y < 0 || y > H - padB) return;
        x.strokeStyle = lv.kind === 'support' ? 'rgba(147,201,63,.16)' : 'rgba(224,101,95,.16)';
        x.lineWidth = 3; x.beginPath(); x.moveTo(0, y); x.lineTo(W - padR, y); x.stroke(); x.lineWidth = 1;
        x.fillStyle = 'rgba(230,237,233,.35)'; x.font = '9px "IBM Plex Mono",monospace';
        x.fillText(`${lv.tf} ${lv.kind === 'support' ? 'S' : 'R'}`, W - padR - 40, y - 5); x.font = '10px "IBM Plex Mono",monospace';
      });
      data.forEach((k, i) => {
        const cx = i * cw + cw / 2, up = k.close >= k.open, col = up ? '#93c93f' : '#e0655f';
        x.strokeStyle = col; x.beginPath(); x.moveTo(cx, Y(k.high)); x.lineTo(cx, Y(k.low)); x.stroke();
        const bw = Math.max(2, cw * 0.6), y1 = Y(Math.max(k.open, k.close)), y2 = Y(Math.min(k.open, k.close));
        x.fillStyle = col; x.fillRect(cx - bw / 2, y1, bw, Math.max(1, y2 - y1));
      });
      if (this.ind.vwap) {
        x.strokeStyle = '#29abe2'; x.lineWidth = 1.4; x.beginPath();
        let pv = 0, vv = 0, day = null;
        data.forEach((k, i) => {
          const dday = k.time.slice(0, 10); if (dday !== day) { day = dday; pv = 0; vv = 0; }
          const w = k.volume || Math.max(k.high - k.low, 1e-12); pv += (k.high + k.low + k.close) / 3 * w; vv += w;
          const px = i * cw + cw / 2, v = pv / vv; i ? x.lineTo(px, Y(v)) : x.moveTo(px, Y(v));
        });
        x.stroke(); x.lineWidth = 1;
      }
      if (this.ind.ema) {
        const all = d.candles; let e = all[0].close; const k2 = 2 / 21; const emas = all.map((k) => (e = k.close * k2 + e * (1 - k2)));
        const off = all.length - n; x.strokeStyle = '#fdb813'; x.lineWidth = 1.4; x.beginPath();
        for (let i = 0; i < n; i++) { const px = i * cw + cw / 2, v = emas[off + i]; i ? x.lineTo(px, Y(v)) : x.moveTo(px, Y(v)); }
        x.stroke(); x.lineWidth = 1;
      }
      ovs.forEach((o) => {
        const y = Y(o.price); if (y < -2 || y > H - padB + 2) return;
        x.strokeStyle = o.color; x.setLineDash(o.dash ? [5, 4] : []); x.globalAlpha = 0.8;
        x.beginPath(); x.moveTo(0, y); x.lineTo(W - padR, y); x.stroke(); x.setLineDash([]); x.globalAlpha = 1;
        x.fillStyle = o.color; x.font = '9px "IBM Plex Mono",monospace';
        x.fillText(`${o.label} ${K.fm(o.price, dd)}`, o.kora ? 120 : 8, y - 4); x.font = '10px "IBM Plex Mono",monospace';
      });
      const last = data[n - 1].close, ly = Y(last);
      x.strokeStyle = 'rgba(253,184,19,.7)'; x.setLineDash([4, 4]); x.beginPath(); x.moveTo(0, ly); x.lineTo(W - padR, ly); x.stroke(); x.setLineDash([]);
      x.fillStyle = '#fdb813'; x.fillRect(W - padR, ly - 9, padR - 2, 18);
      x.fillStyle = '#14201c'; x.fillText(K.fm(last, dd), W - padR + 6, ly + 3);
      if (this.mouse && this.mouse.x < W - padR) {
        const { x: mx2, y: my } = this.mouse;
        x.strokeStyle = 'rgba(255,255,255,.28)'; x.setLineDash([3, 3]);
        x.beginPath(); x.moveTo(mx2, 0); x.lineTo(mx2, H - padB); x.stroke();
        x.beginPath(); x.moveTo(0, my); x.lineTo(W - padR, my); x.stroke(); x.setLineDash([]);
        const pv = mn + (mx - mn) * (1 - my / (H - padB));
        x.fillStyle = '#2a3a33'; x.fillRect(W - padR, my - 9, padR - 2, 18);
        x.fillStyle = '#e6ede9'; x.fillText(K.fm(pv, dd), W - padR + 6, my + 3);
        const idx = Math.floor(mx2 / cw);
        if (data[idx] && this.opts.onHover) this.opts.onHover(data[idx]);
      } else if (this.opts.onHover) this.opts.onHover(data[n - 1]);
    }
  }
  K.KoraChart = KoraChart;

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
      const col = k.close >= k.open ? '#93c93f' : '#e0655f', cx = i * cw + cw / 2;
      html += `<line x1="${cx}" x2="${cx}" y1="${Y(k.high)}" y2="${Y(k.low)}" stroke="${col}" stroke-width=".8"/>`;
      const y1 = Y(Math.max(k.open, k.close)), y2 = Y(Math.min(k.open, k.close));
      html += `<rect x="${cx - cw * 0.32}" y="${y1}" width="${Math.max(1, cw * 0.64)}" height="${Math.max(0.8, y2 - y1)}" fill="${col}"/>`;
    });
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`); svg.setAttribute('preserveAspectRatio', 'none'); svg.innerHTML = html;
  }
})();
