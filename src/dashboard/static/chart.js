/* Kora chart engine: candlesticks + overlay/pane indicators, zoom/pan,
   crosshair legend, trade-history markers and Kora plan overlays. */
(function () {
  const K = window.KORA;
  const C = { g: '#9bd35a', r: '#ff6b61', grid: 'rgba(255,255,255,.05)', axis: 'rgba(235,235,245,.42)', text: 'rgba(235,235,245,.72)' };
  const FONT = '11px -apple-system,"SF Pro Text",Inter,system-ui,sans-serif';

  // ------------------------------------------------------------ indicator math
  const sma = (a, n) => a.map((_, i) => (i < n - 1 ? null : a.slice(i - n + 1, i + 1).reduce((s, v) => s + v, 0) / n));
  const ema = (a, n) => { const k = 2 / (n + 1); let e = null; return a.map((v, i) => { if (v == null) return null; e = e == null ? v : v * k + e * (1 - k); return i < n - 1 ? null : e; }); };
  const stdev = (a, n) => a.map((_, i) => { if (i < n - 1) return null; const w = a.slice(i - n + 1, i + 1), m = w.reduce((s, v) => s + v, 0) / n; return Math.sqrt(w.reduce((s, v) => s + (v - m) ** 2, 0) / n); });
  function rsi(a, n) {
    const out = a.map(() => null); let g = 0, l = 0;
    for (let i = 1; i < a.length; i++) {
      const d = a[i] - a[i - 1], up = Math.max(d, 0), dn = Math.max(-d, 0);
      if (i <= n) { g += up; l += dn; if (i === n) { g /= n; l /= n; out[i] = l ? 100 - 100 / (1 + g / l) : 100; } }
      else { g = (g * (n - 1) + up) / n; l = (l * (n - 1) + dn) / n; out[i] = l ? 100 - 100 / (1 + g / l) : 100; }
    }
    return out;
  }
  function atr(c, n) {
    const tr = c.map((k, i) => (i ? Math.max(k.high - k.low, Math.abs(k.high - c[i - 1].close), Math.abs(k.low - c[i - 1].close)) : k.high - k.low));
    const out = c.map(() => null); let a = null;
    tr.forEach((t, i) => { if (i < n) { if (i === n - 1) a = tr.slice(0, n).reduce((s, v) => s + v, 0) / n, out[i] = a; } else { a = (a * (n - 1) + t) / n; out[i] = a; } });
    return out;
  }

  const TYPES = {
    sma: { name: 'SMA', pane: false, params: { period: 50 }, color: '#ffc34d', calc: (c, p) => [{ v: sma(c.map((k) => k.close), p.period) }] },
    ema: { name: 'EMA', pane: false, params: { period: 20 }, color: '#5ac8fa', calc: (c, p) => [{ v: ema(c.map((k) => k.close), p.period) }] },
    bb: { name: 'Bollinger', pane: false, params: { period: 20, mult: 2 }, color: '#bf9cff', calc: (c, p) => {
      const cl = c.map((k) => k.close), m = sma(cl, p.period), sd = stdev(cl, p.period);
      return [{ v: m.map((x, i) => (x == null ? null : x + p.mult * sd[i])), dash: true }, { v: m, alpha: 0.6 }, { v: m.map((x, i) => (x == null ? null : x - p.mult * sd[i])), dash: true }];
    } },
    vwap: { name: 'VWAP', pane: false, params: {}, color: '#5ac8fa', calc: (c) => {
      let pv = 0, vv = 0, day = null;
      return [{ v: c.map((k) => { const d = k.time.slice(0, 10); if (d !== day) { day = d; pv = 0; vv = 0; } const w = k.volume || Math.max(k.high - k.low, 1e-12); pv += ((k.high + k.low + k.close) / 3) * w; vv += w; return pv / vv; }) }];
    } },
    donchian: { name: 'Donchian', pane: false, params: { period: 20 }, color: '#64d2ff', calc: (c, p) => {
      const hi = c.map((_, i) => (i < p.period - 1 ? null : Math.max(...c.slice(i - p.period + 1, i + 1).map((k) => k.high))));
      const lo = c.map((_, i) => (i < p.period - 1 ? null : Math.min(...c.slice(i - p.period + 1, i + 1).map((k) => k.low))));
      return [{ v: hi, alpha: 0.7 }, { v: lo, alpha: 0.7 }];
    } },
    rsi: { name: 'RSI', pane: true, params: { period: 14 }, color: '#bf9cff', range: [0, 100], levels: [30, 70], calc: (c, p) => [{ v: rsi(c.map((k) => k.close), p.period) }] },
    macd: { name: 'MACD', pane: true, params: { fast: 12, slow: 26, signal: 9 }, color: '#5ac8fa', calc: (c, p) => {
      const cl = c.map((k) => k.close), f = ema(cl, p.fast), s = ema(cl, p.slow);
      const line = f.map((x, i) => (x == null || s[i] == null ? null : x - s[i]));
      const first = line.findIndex((x) => x != null), sigRaw = ema(line.slice(Math.max(first, 0)).map((x) => x ?? 0), p.signal);
      const sig = line.map((_, i) => (i < first ? null : sigRaw[i - first]));
      return [{ v: line.map((x, i) => (x == null || sig[i] == null ? null : x - sig[i])), hist: true }, { v: line }, { v: sig, color: '#ffc34d' }];
    } },
    atr: { name: 'ATR', pane: true, params: { period: 14 }, color: '#ffc34d', calc: (c, p) => [{ v: atr(c, p.period) }] },
    stoch: { name: 'Stochastic', pane: true, params: { k: 14, d: 3 }, color: '#5ac8fa', range: [0, 100], levels: [20, 80], calc: (c, p) => {
      const kk = c.map((_, i) => { if (i < p.k - 1) return null; const w = c.slice(i - p.k + 1, i + 1), hi = Math.max(...w.map((x) => x.high)), lo = Math.min(...w.map((x) => x.low)); return hi === lo ? 50 : ((c[i].close - lo) / (hi - lo)) * 100; });
      return [{ v: kk }, { v: sma(kk.map((x) => x ?? 0), p.d).map((x, i) => (kk[i] == null ? null : x)), color: '#ffc34d' }];
    } },
    volume: { name: 'Volume', pane: true, params: {}, color: 'rgba(235,235,245,.35)', calc: (c) => [{ v: c.map((k) => k.volume ?? 0), bars: true }] },
  };
  K.INDICATOR_TYPES = TYPES;

  // ------------------------------------------------------------------- chart
  class Chart {
    constructor(canvas, opts = {}) {
      this.c = canvas; this.opts = opts; this.data = null; this.inds = []; this.show = { levels: true, kora: true, trades: true };
      this.view = null; this.mouse = null; this.drag = null;
      canvas.addEventListener('mousemove', (e) => {
        const r = canvas.getBoundingClientRect(); this.mouse = { x: e.clientX - r.left, y: e.clientY - r.top };
        if (this.drag) { const dx = this.mouse.x - this.drag.x, cw = this._cw || 6; this.view.end = Math.min(this.data.candles.length, Math.max(this.view.count, this.drag.end - Math.round(dx / cw))); }
        this.draw();
      });
      canvas.addEventListener('mouseleave', () => { this.mouse = null; this.drag = null; this.draw(); });
      canvas.addEventListener('mousedown', (e) => { this.drag = { x: e.offsetX, end: this.view.end }; canvas.style.cursor = 'grabbing'; });
      window.addEventListener('mouseup', () => { this.drag = null; canvas.style.cursor = 'crosshair'; });
      canvas.addEventListener('wheel', (e) => { e.preventDefault(); if (!this.view) return; const n = this.data.candles.length;
        this.view.count = Math.round(Math.min(n, Math.max(30, this.view.count * (e.deltaY > 0 ? 1.12 : 0.89)))); this.view.end = Math.max(this.view.count, this.view.end); this.draw(); }, { passive: false });
      canvas.addEventListener('dblclick', () => { this.resetView(); this.draw(); });
      window.addEventListener('resize', () => this.draw());
    }
    resetView() { const n = this.data ? this.data.candles.length : 0; this.view = { end: n, count: Math.min(n, 130) }; }
    set(data) { const first = !this.data || this.data.code !== data.code || this.data.tf !== data.tf; this.data = data; if (first || !this.view) this.resetView(); this.compute(); this.draw(); }
    setIndicators(list) { this.inds = list.map((x) => ({ ...x })); this.compute(); this.draw(); }
    compute() { if (!this.data) return; for (const ind of this.inds) { const T = TYPES[ind.type]; ind.series = T ? T.calc(this.data.candles, { ...T.params, ...ind.params }) : []; } }
    live(price) {
      if (!this.data || !price) return;
      const k = this.data.candles[this.data.candles.length - 1];
      k.close = price; k.high = Math.max(k.high, price); k.low = Math.min(k.low, price);
      this.compute(); this.draw();
    }
    draw() {
      const c = this.c, d = this.data; if (!c || !d || !d.candles.length) return;
      const dpr = window.devicePixelRatio || 1, W = c.clientWidth || 900;
      const panes = this.inds.filter((i) => TYPES[i.type] && TYPES[i.type].pane);
      const paneH = 96, H = (this.opts.height || 520) + panes.length * paneH;
      c.style.height = H + 'px';
      if (c.width !== Math.round(W * dpr) || c.height !== Math.round(H * dpr)) { c.width = Math.round(W * dpr); c.height = Math.round(H * dpr); }
      const x = c.getContext('2d'); x.setTransform(dpr, 0, 0, dpr, 0, 0); x.clearRect(0, 0, W, H);
      const all = d.candles, s = Math.max(0, this.view.end - this.view.count), e = this.view.end, data = all.slice(s, e), n = data.length;
      const dd = d.digits > 4 ? 5 : d.digits, padR = 82, padB = 24, mainH = H - padB - panes.length * paneH;
      const cw = (W - padR) / n; this._cw = cw;
      const X = (i) => (i - s) * cw + cw / 2;
      // main scale
      let mn = Infinity, mx = -Infinity;
      data.forEach((k) => { mn = Math.min(mn, k.low); mx = Math.max(mx, k.high); });
      const span0 = mx - mn;
      for (const ind of this.inds) if (!TYPES[ind.type]?.pane) for (const sr of ind.series || []) for (let i = s; i < e; i++) { const v = sr.v[i]; if (v != null && Math.abs(v - (mn + mx) / 2) < span0 * 1.5) { mn = Math.min(mn, v); mx = Math.max(mx, v); } }
      const ovs = (d.overlays || []).filter((o) => this.show.kora || !o.kora);
      ovs.forEach((o) => { if (Math.abs(o.price - (mn + mx) / 2) < span0 * 2.5) { mn = Math.min(mn, o.price); mx = Math.max(mx, o.price); } });
      const rg = mx - mn || 1; mn -= rg * 0.05; mx += rg * 0.05;
      const Y = (v) => 8 + (mainH - 16) * (1 - (v - mn) / (mx - mn));
      x.font = FONT; x.textAlign = 'left';
      for (let i = 0; i <= 6; i++) { const v = mn + ((mx - mn) * i) / 6, y = Y(v); x.strokeStyle = C.grid; x.beginPath(); x.moveTo(0, y); x.lineTo(W - padR, y); x.stroke(); x.fillStyle = C.axis; x.fillText(K.fm(v, dd), W - padR + 8, y + 4); }
      // time axis
      x.fillStyle = C.axis; x.textAlign = 'center';
      const step = Math.max(1, Math.round(n / 7)), p2 = (v) => String(v).padStart(2, '0');
      for (let i = Math.floor(step / 2); i < n; i += step) {
        const t = new Date(data[i].time);
        const lab = d.tf === 'D1' ? `${t.getDate()}/${t.getMonth() + 1}` : (d.tf === 'H4' || d.tf === 'H1') ? `${t.getDate()}/${t.getMonth() + 1} ${p2(t.getHours())}:00` : `${p2(t.getHours())}:${p2(t.getMinutes())}`;
        x.fillText(lab, i * cw + cw / 2, H - 7);
      }
      x.textAlign = 'left';
      // S/R levels
      if (this.show.levels) (d.levels || []).forEach((lv) => { const y = Y(lv.price); if (y < 0 || y > mainH) return;
        x.fillStyle = lv.kind === 'support' ? 'rgba(155,211,90,.08)' : 'rgba(255,107,97,.08)'; x.fillRect(0, y - 3, W - padR, 6);
        x.fillStyle = C.axis; x.fillText(`${lv.tf} ${lv.kind === 'support' ? 'S' : 'R'}`, W - padR - 44, y - 6); });
      // candles
      data.forEach((k, j) => { const cx = j * cw + cw / 2, up = k.close >= k.open, col = up ? C.g : C.r;
        x.strokeStyle = col; x.beginPath(); x.moveTo(cx, Y(k.high)); x.lineTo(cx, Y(k.low)); x.stroke();
        const bw = Math.max(1.5, cw * 0.62), y1 = Y(Math.max(k.open, k.close)), y2 = Y(Math.min(k.open, k.close));
        x.fillStyle = col; x.fillRect(cx - bw / 2, y1, bw, Math.max(1, y2 - y1)); });
      // overlay indicators
      const line = (vals, color, Yf, dash, alpha) => { x.save(); x.strokeStyle = color; x.globalAlpha = alpha ?? 1; x.lineWidth = 1.5; if (dash) x.setLineDash([4, 4]); x.beginPath(); let on = false;
        for (let i = s; i < e; i++) { const v = vals[i]; if (v == null) { on = false; continue; } const px = X(i), py = Yf(v); on ? x.lineTo(px, py) : x.moveTo(px, py); on = true; } x.stroke(); x.restore(); };
      for (const ind of this.inds) { if (TYPES[ind.type]?.pane) continue; for (const sr of ind.series || []) line(sr.v, sr.color || ind.color, Y, sr.dash, sr.alpha); }
      // trade history markers
      if (this.show.trades && d.trades) {
        const idx = (t) => { let lo = 0, hi = all.length - 1; if (t < all[0].time) return -1; while (lo < hi) { const m = (lo + hi + 1) >> 1; if (all[m].time <= t) lo = m; else hi = m - 1; } return lo; };
        for (const tr of d.trades) {
          const io = idx(tr.open_time.slice(0, 19)), ic = idx(tr.close_time.slice(0, 19)); if (ic < s || io >= e || io < 0) continue;
          const buy = tr.direction === 'buy', yo = Y(tr.open_price), yc = Y(tr.close_price), win = tr.pnl >= 0;
          x.save(); x.strokeStyle = win ? 'rgba(155,211,90,.55)' : 'rgba(255,107,97,.55)'; x.setLineDash([3, 3]); x.beginPath(); x.moveTo(X(io), yo); x.lineTo(X(ic), yc); x.stroke(); x.restore();
          x.fillStyle = buy ? C.g : C.r; x.beginPath();
          if (buy) { x.moveTo(X(io), yo + 3); x.lineTo(X(io) - 5, yo + 11); x.lineTo(X(io) + 5, yo + 11); } else { x.moveTo(X(io), yo - 3); x.lineTo(X(io) - 5, yo - 11); x.lineTo(X(io) + 5, yo - 11); }
          x.fill(); x.beginPath(); x.arc(X(ic), yc, 4, 0, Math.PI * 2); x.fillStyle = win ? C.g : C.r; x.fill();
          x.fillStyle = C.text; x.fillText((tr.pnl >= 0 ? '+' : '') + tr.pnl.toFixed(2), X(ic) + 7, yc + 4);
        }
      }
      // kora plan / positions
      const tags = [];
      ovs.forEach((o) => { const y = Y(o.price); if (y < -2 || y > mainH + 2) return;
        x.save(); x.strokeStyle = o.color; x.setLineDash(o.dash ? [6, 4] : []); x.globalAlpha = 0.7; x.beginPath(); x.moveTo(0, y); x.lineTo(W - padR, y); x.stroke(); x.restore();
        tags.push({ y, o }); });
      // right-aligned label tags, nudged apart so close levels never overlap
      tags.sort((a, b) => a.y - b.y); let lastY = -Infinity;
      for (const t of tags) { t.ty = Math.max(t.y, lastY + 17); lastY = t.ty; }
      for (const t of tags) { const label = `${t.o.label} ${K.fm(t.o.price, dd)}`, w = x.measureText(label).width + 14, lx = W - padR - w - 6;
        x.save(); x.globalAlpha = 0.92; x.fillStyle = '#151817'; x.strokeStyle = t.o.color; x.beginPath(); x.roundRect(lx, t.ty - 8, w, 16, 5); x.fill(); x.stroke(); x.restore();
        x.fillStyle = t.o.color; x.fillText(label, lx + 7, t.ty + 4); }
      // last price
      const last = all[all.length - 1].close, ly = Y(last);
      if (ly > 0 && ly < mainH) { x.save(); x.strokeStyle = 'rgba(255,195,77,.6)'; x.setLineDash([3, 4]); x.beginPath(); x.moveTo(0, ly); x.lineTo(W - padR, ly); x.stroke(); x.restore();
        x.fillStyle = '#ffc34d'; x.beginPath(); x.roundRect(W - padR + 2, ly - 10, padR - 6, 20, 6); x.fill(); x.fillStyle = '#0b0d0c'; x.fillText(K.fm(last, dd), W - padR + 8, ly + 4); }
      // panes
      panes.forEach((ind, pi) => {
        const T = TYPES[ind.type], top = mainH + pi * paneH, h = paneH - 10;
        x.strokeStyle = 'rgba(255,255,255,.07)'; x.beginPath(); x.moveTo(0, top + 4); x.lineTo(W, top + 4); x.stroke();
        let lo = Infinity, hi = -Infinity;
        if (T.range) [lo, hi] = T.range; else for (const sr of ind.series) for (let i = s; i < e; i++) { const v = sr.v[i]; if (v != null) { lo = Math.min(lo, v); hi = Math.max(hi, v); } }
        if (!isFinite(lo)) return; if (T.name === 'Volume' || ind.series.some((sr) => sr.hist)) lo = Math.min(lo, 0);
        const PY = (v) => top + 10 + (h - 10) * (1 - (v - lo) / ((hi - lo) || 1));
        (T.levels || []).forEach((lv) => { x.save(); x.strokeStyle = 'rgba(255,255,255,.12)'; x.setLineDash([3, 4]); x.beginPath(); x.moveTo(0, PY(lv)); x.lineTo(W - padR, PY(lv)); x.stroke(); x.restore(); x.fillStyle = C.axis; x.fillText(lv, W - padR + 8, PY(lv) + 4); });
        x.fillStyle = C.axis; x.fillText(K.fm(hi, hi > 100 ? 0 : 2), W - padR + 8, top + 18);
        for (const sr of ind.series) {
          if (sr.hist || sr.bars) { for (let i = s; i < e; i++) { const v = sr.v[i]; if (v == null) continue; const y0 = PY(0), y1 = PY(v);
              x.fillStyle = sr.bars ? (all[i].close >= all[i].open ? 'rgba(155,211,90,.35)' : 'rgba(255,107,97,.35)') : (v >= 0 ? 'rgba(155,211,90,.55)' : 'rgba(255,107,97,.55)');
              x.fillRect(X(i) - Math.max(1, cw * 0.32), Math.min(y0, y1), Math.max(1.5, cw * 0.64), Math.abs(y1 - y0)); } }
          else line(sr.v, sr.color || ind.color, PY);
        }
      });
      // crosshair + legend
      let hi = all.length - 1;
      if (this.mouse && this.mouse.x < W - padR && this.mouse.y < H - padB) {
        const { x: mx2, y: my } = this.mouse; hi = Math.min(e - 1, s + Math.max(0, Math.floor(mx2 / cw)));
        x.save(); x.strokeStyle = 'rgba(255,255,255,.25)'; x.setLineDash([3, 3]); x.beginPath(); x.moveTo(mx2, 0); x.lineTo(mx2, H - padB); x.stroke();
        if (my < mainH) { x.beginPath(); x.moveTo(0, my); x.lineTo(W - padR, my); x.stroke(); x.restore();
          const pv = mn + (mx - mn) * (1 - (my - 8) / (mainH - 16)); x.fillStyle = '#2c302f'; x.beginPath(); x.roundRect(W - padR + 2, my - 10, padR - 6, 20, 6); x.fill(); x.fillStyle = '#f5f5f7'; x.fillText(K.fm(pv, dd), W - padR + 8, my + 4); }
        else x.restore();
      }
      x.font = FONT; let lx = 10;
      for (const ind of this.inds) { if (TYPES[ind.type]?.pane) continue; const v = ind.series?.[0]?.v[hi]; const label = `${TYPES[ind.type].name}${Object.values(ind.params || {}).length ? ' ' + Object.values(ind.params).join(',') : ''} ${v != null ? K.fm(v, dd) : ''}`;
        x.fillStyle = ind.color; x.fillText(label, lx, 18); lx += x.measureText(label).width + 16; }
      panes.forEach((ind, pi) => { const top = mainH + pi * paneH; const vals = ind.series.map((sr) => sr.v[hi]).filter((v) => v != null).map((v) => K.fm(v, Math.abs(v) > 100 ? 0 : 2)).join(' · ');
        x.fillStyle = ind.color; x.fillText(`${TYPES[ind.type].name} ${Object.values(ind.params || {}).join(',')}  ${vals}`, 10, top + 20); });
      if (this.opts.onHover) this.opts.onHover(all[hi]);
    }
  }
  K.Chart = Chart;
})();
