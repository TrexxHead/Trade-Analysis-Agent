/* Kora chart engine v2
   - Free-flowing movement: sub-candle panning with inertia, cursor-anchored
     wheel/pinch zoom, drag the price axis to scale, drag the time axis to zoom,
     right-side whitespace, keyboard control, older history loads as you scroll back.
   - Drawing toolkit: trend, ray, extended line, horizontal line/ray, vertical,
     rectangle, Fibonacci, measure, text, long/short position. Select, drag,
     re-shape by handles, recolour, delete; magnet snaps to OHLC.
   - 24 indicators identical to the strategy builder's (same keys/params).
   - Overlays: Kora's plan, S/R levels, your trades, and any strategy's signals. */
(function () {
  const K = window.KORA;
  const C = { g: '#9bd35a', r: '#ff6b61', b: '#5ac8fa', a: '#ffc34d', grid: 'rgba(255,255,255,.045)', axis: 'rgba(235,235,245,.45)', text: 'rgba(235,235,245,.78)', bg: '#0f1211' };
  const FONT = '11px -apple-system,"SF Pro Text",Inter,system-ui,sans-serif';
  const TF_MS = { M1: 6e4, M5: 3e5, M15: 9e5, M30: 18e5, H1: 36e5, H4: 144e5, D1: 864e5 };

  // ------------------------------------------------------------ indicator math (mirrors src/analyst/indicators.py)
  const nz = (a) => a.map((v) => (v == null || Number.isNaN(v) ? null : v));
  const sma = (a, n) => a.map((_, i) => { if (i < n - 1) return null; let s = 0; for (let j = i - n + 1; j <= i; j++) { if (a[j] == null) return null; s += a[j]; } return s / n; });
  const ema = (a, n) => { const out = a.map(() => null); let first = a.findIndex((v) => v != null); if (first < 0 || a.length - first < n) return out;
    let e = 0; for (let j = first; j < first + n; j++) e += a[j]; e /= n; out[first + n - 1] = e; const k = 2 / (n + 1);
    for (let i = first + n; i < a.length; i++) { if (a[i] == null) continue; e = (a[i] - e) * k + e; out[i] = e; } return out; };
  const wma = (a, n) => { const w = (n * (n + 1)) / 2; return a.map((_, i) => { if (i < n - 1) return null; let s = 0; for (let k = 0; k < n; k++) { const v = a[i - n + 1 + k]; if (v == null) return null; s += v * (k + 1); } return s / w; }); };
  const hma = (a, n) => { const x = wma(a, Math.max(1, Math.floor(n / 2))), y = wma(a, n); const d = x.map((v, i) => (v == null || y[i] == null ? null : 2 * v - y[i]));
    const f = d.findIndex((v) => v != null); if (f < 0) return d; const t = wma(d.slice(f), Math.max(1, Math.floor(Math.sqrt(n)))); return d.map((_, i) => (i < f ? null : t[i - f])); };
  const stdev = (a, n) => a.map((_, i) => { if (i < n - 1) return null; const w = a.slice(i - n + 1, i + 1); if (w.some((v) => v == null)) return null; const m = w.reduce((s, v) => s + v, 0) / n; return Math.sqrt(w.reduce((s, v) => s + (v - m) ** 2, 0) / n); });
  function rsi(a, n) {
    const out = a.map(() => null); let g = 0, l = 0;
    for (let i = 1; i < a.length; i++) { const d = a[i] - a[i - 1], up = Math.max(d, 0), dn = Math.max(-d, 0);
      if (i <= n) { g += up; l += dn; if (i === n) { g /= n; l /= n; out[i] = l ? 100 - 100 / (1 + g / l) : 100; } }
      else { g = (g * (n - 1) + up) / n; l = (l * (n - 1) + dn) / n; out[i] = l ? 100 - 100 / (1 + g / l) : 100; } }
    return out;
  }
  const trueRange = (c) => c.map((k, i) => (i ? Math.max(k.high - k.low, Math.abs(k.high - c[i - 1].close), Math.abs(k.low - c[i - 1].close)) : k.high - k.low));
  function wilder(v, n, start = 0) { const out = v.map(() => null); if (v.length - start < n) return out; let a = 0; for (let j = start; j < start + n; j++) a += v[j]; a /= n; out[start + n - 1] = a; for (let i = start + n; i < v.length; i++) { a = (a * (n - 1) + v[i]) / n; out[i] = a; } return out; }
  const atr = (c, n) => wilder(trueRange(c), n);
  const rollMax = (c, n, key, prev) => c.map((_, i) => { const lo = prev ? i - n : i - n + 1, hi = prev ? i - 1 : i; if (lo < 0) return null; let m = -Infinity; for (let j = lo; j <= hi; j++) m = Math.max(m, c[j][key]); return m; });
  const rollMin = (c, n, key, prev) => c.map((_, i) => { const lo = prev ? i - n : i - n + 1, hi = prev ? i - 1 : i; if (lo < 0) return null; let m = Infinity; for (let j = lo; j <= hi; j++) m = Math.min(m, c[j][key]); return m; });
  const stochK = (c, n) => c.map((_, i) => { if (i < n - 1) return null; let hi = -Infinity, lo = Infinity; for (let j = i - n + 1; j <= i; j++) { hi = Math.max(hi, c[j].high); lo = Math.min(lo, c[j].low); } return hi === lo ? 50 : ((c[i].close - lo) / (hi - lo)) * 100; });
  function adx(c, n) {
    const L = c.length, pdm = Array(L).fill(0), mdm = Array(L).fill(0);
    for (let i = 1; i < L; i++) { const up = c[i].high - c[i - 1].high, dn = c[i - 1].low - c[i].low; pdm[i] = up > dn && up > 0 ? up : 0; mdm[i] = dn > up && dn > 0 ? dn : 0; }
    const tr = trueRange(c), st = wilder(tr, n, 1), sp = wilder(pdm, n, 1), sm = wilder(mdm, n, 1), pdi = Array(L).fill(null), mdi = Array(L).fill(null), dx = Array(L).fill(0); let first = null;
    for (let i = 0; i < L; i++) if (st[i]) { pdi[i] = (100 * sp[i]) / st[i]; mdi[i] = (100 * sm[i]) / st[i]; const t = pdi[i] + mdi[i]; dx[i] = t ? (100 * Math.abs(pdi[i] - mdi[i])) / t : 0; if (first == null) first = i; }
    return [first == null ? Array(L).fill(null) : wilder(dx, n, first), pdi, mdi];
  }
  function supertrend(c, n, m) {
    const a = atr(c, n), line = c.map(() => null), dir = c.map(() => null); let fu = null, fl = null, t = 1;
    c.forEach((k, i) => { if (a[i] == null) return; const hl = (k.high + k.low) / 2, bu = hl + m * a[i], bl = hl - m * a[i], pc = i ? c[i - 1].close : k.close;
      fu = fu == null || bu < fu || pc > fu ? bu : fu; fl = fl == null || bl > fl || pc < fl ? bl : fl;
      if (t === 1 && k.close < fl) t = -1; else if (t === -1 && k.close > fu) t = 1; line[i] = t === 1 ? fl : fu; dir[i] = t; });
    return [line, dir];
  }
  function psar(c, step, mx) {
    const out = c.map(() => null), dir = c.map(() => null); if (c.length < 3) return [out, dir];
    let up = c[1].close >= c[0].close, sar = up ? c[0].low : c[0].high, ep = up ? c[1].high : c[1].low, af = step;
    for (let i = 1; i < c.length; i++) { const k = c[i]; sar += af * (ep - sar);
      if (up) { sar = Math.min(sar, c[i - 1].low, i > 1 ? c[i - 2].low : c[i - 1].low); if (k.low < sar) { up = false; sar = ep; ep = k.low; af = step; } else if (k.high > ep) { ep = k.high; af = Math.min(mx, af + step); } }
      else { sar = Math.max(sar, c[i - 1].high, i > 1 ? c[i - 2].high : c[i - 1].high); if (k.high > sar) { up = true; sar = ep; ep = k.high; af = step; } else if (k.low < ep) { ep = k.low; af = Math.min(mx, af + step); } }
      out[i] = sar; dir[i] = up ? 1 : -1; }
    return [out, dir];
  }
  const midHL = (c, n) => c.map((_, i) => { if (i < n - 1) return null; let hi = -Infinity, lo = Infinity; for (let j = i - n + 1; j <= i; j++) { hi = Math.max(hi, c[j].high); lo = Math.min(lo, c[j].low); } return (hi + lo) / 2; });
  const split = (v, dir) => [v.map((x, i) => (dir[i] === 1 || (dir[i + 1] === 1 && dir[i] === -1) ? x : null)), v.map((x, i) => (dir[i] === -1 || (dir[i + 1] === -1 && dir[i] === 1) ? x : null))];
  const cl = (c) => c.map((k) => k.close);

  const TYPES = {
    sma: { name: 'SMA', pane: false, params: { period: 50 }, color: '#ffc34d', calc: (c, p) => [{ v: sma(cl(c), p.period) }] },
    ema: { name: 'EMA', pane: false, params: { period: 20 }, color: '#5ac8fa', calc: (c, p) => [{ v: ema(cl(c), p.period) }] },
    wma: { name: 'WMA', pane: false, params: { period: 20 }, color: '#64d2ff', calc: (c, p) => [{ v: wma(cl(c), p.period) }] },
    hma: { name: 'Hull MA', pane: false, params: { period: 21 }, color: '#ff9f0a', calc: (c, p) => [{ v: hma(cl(c), p.period) }] },
    vwap: { name: 'VWAP', pane: false, params: {}, color: '#bf9cff', calc: (c) => { let pv = 0, vv = 0, day = null;
      return [{ v: c.map((k) => { const d = k.time.slice(0, 10); if (d !== day) { day = d; pv = 0; vv = 0; } const w = k.volume || Math.max(k.high - k.low, 1e-12); pv += ((k.high + k.low + k.close) / 3) * w; vv += w; return pv / vv; }) }]; } },
    bb: { name: 'Bollinger', pane: false, params: { period: 20, mult: 2 }, color: '#bf9cff', calc: (c, p) => { const m = sma(cl(c), p.period), sd = stdev(cl(c), p.period);
      const up = m.map((x, i) => (x == null ? null : x + p.mult * sd[i])), lo = m.map((x, i) => (x == null ? null : x - p.mult * sd[i]));
      return [{ v: up, fill: 2, alpha: 0.85 }, { v: m, alpha: 0.5, dash: true }, { v: lo, alpha: 0.85 }]; } },
    keltner: { name: 'Keltner', pane: false, params: { period: 20, atr: 10, mult: 2 }, color: '#30d158', calc: (c, p) => { const m = ema(cl(c), p.period), a = atr(c, p.atr);
      return [{ v: m.map((x, i) => (x == null || a[i] == null ? null : x + p.mult * a[i])), alpha: 0.8 }, { v: m, alpha: 0.45, dash: true }, { v: m.map((x, i) => (x == null || a[i] == null ? null : x - p.mult * a[i])), alpha: 0.8 }]; } },
    donchian: { name: 'Donchian', pane: false, params: { period: 20 }, color: '#64d2ff', calc: (c, p) => [{ v: rollMax(c, p.period, 'high', true), fill: 1, alpha: 0.8 }, { v: rollMin(c, p.period, 'low', true), alpha: 0.8 }] },
    supertrend: { name: 'Supertrend', pane: false, params: { period: 10, mult: 3 }, color: '#9bd35a', calc: (c, p) => { const [l, d] = supertrend(c, p.period, p.mult); const [u, w] = split(l, d); return [{ v: u, color: C.g, width: 2 }, { v: w, color: C.r, width: 2 }]; } },
    psar: { name: 'Parabolic SAR', pane: false, params: { step: 0.02, max: 0.2 }, color: '#ffc34d', calc: (c, p) => { const [v] = psar(c, p.step, p.max); return [{ v, dots: true }]; } },
    ichimoku: { name: 'Ichimoku', pane: false, params: { tenkan: 9, kijun: 26, senkou: 52 }, color: '#ff6b61', calc: (c, p) => { const t = midHL(c, p.tenkan), k = midHL(c, p.kijun), b = midHL(c, p.senkou), sh = p.kijun;
      const A = c.map((_, i) => (i < sh || t[i - sh] == null || k[i - sh] == null ? null : (t[i - sh] + k[i - sh]) / 2)), Bs = c.map((_, i) => (i < sh ? null : b[i - sh]));
      return [{ v: t, color: '#5ac8fa', alpha: 0.9 }, { v: k, color: '#ff6b61', alpha: 0.9 }, { v: A, color: '#30d158', alpha: 0.55, fill: 3, fillColor: 'cloud' }, { v: Bs, color: '#ff453a', alpha: 0.55 }]; } },
    highest: { name: 'Highest high', pane: false, params: { period: 20 }, color: '#30d158', calc: (c, p) => [{ v: rollMax(c, p.period, 'high', false), dash: true }] },
    lowest: { name: 'Lowest low', pane: false, params: { period: 20 }, color: '#ff453a', calc: (c, p) => [{ v: rollMin(c, p.period, 'low', false), dash: true }] },
    rsi: { name: 'RSI', pane: true, params: { period: 14 }, color: '#bf9cff', range: [0, 100], levels: [30, 70], calc: (c, p) => [{ v: rsi(cl(c), p.period) }] },
    stoch: { name: 'Stochastic', pane: true, params: { k: 14, d: 3, smooth: 3 }, color: '#5ac8fa', range: [0, 100], levels: [20, 80], calc: (c, p) => {
      const k0 = stochK(c, p.k), k = p.smooth > 1 ? sma(k0, p.smooth) : k0, d = p.d > 1 ? sma(k, p.d) : k; return [{ v: k }, { v: d, color: '#ffc34d' }]; } },
    stochrsi: { name: 'Stoch RSI', pane: true, params: { rsi: 14, stoch: 14, k: 3, d: 3 }, color: '#64d2ff', range: [0, 100], levels: [20, 80], calc: (c, p) => {
      const r = rsi(cl(c), p.rsi), raw = r.map((_, i) => { if (i < p.stoch - 1) return null; const w = r.slice(i - p.stoch + 1, i + 1); if (w.some((x) => x == null)) return null; const hi = Math.max(...w), lo = Math.min(...w); return hi === lo ? 50 : ((r[i] - lo) / (hi - lo)) * 100; });
      const k = sma(raw, p.k); return [{ v: k }, { v: sma(k, p.d), color: '#ffc34d' }]; } },
    macd: { name: 'MACD', pane: true, params: { fast: 12, slow: 26, signal: 9 }, color: '#5ac8fa', calc: (c, p) => { const f = ema(cl(c), p.fast), s = ema(cl(c), p.slow);
      const line = f.map((x, i) => (x == null || s[i] == null ? null : x - s[i])), sig = ema(line, p.signal);
      return [{ v: line.map((x, i) => (x == null || sig[i] == null ? null : x - sig[i])), hist: true }, { v: line }, { v: sig, color: '#ffc34d' }]; } },
    adx: { name: 'ADX / DMI', pane: true, params: { period: 14 }, color: '#ffc34d', levels: [20, 25], calc: (c, p) => { const [a, pl, mi] = adx(c, p.period); return [{ v: a, width: 2 }, { v: pl, color: C.g, alpha: 0.7 }, { v: mi, color: C.r, alpha: 0.7 }]; } },
    cci: { name: 'CCI', pane: true, params: { period: 20 }, color: '#64d2ff', levels: [-100, 100], calc: (c, p) => { const tp = c.map((k) => (k.high + k.low + k.close) / 3), n = p.period;
      return [{ v: tp.map((_, i) => { if (i < n - 1) return null; const w = tp.slice(i - n + 1, i + 1), m = w.reduce((s, v) => s + v, 0) / n, md = w.reduce((s, v) => s + Math.abs(v - m), 0) / n; return md ? (tp[i] - m) / (0.015 * md) : 0; }) }]; } },
    willr: { name: 'Williams %R', pane: true, params: { period: 14 }, color: '#ff9f0a', range: [-100, 0], levels: [-80, -20], calc: (c, p) => [{ v: stochK(c, p.period).map((x) => (x == null ? null : x - 100)) }] },
    roc: { name: 'Rate of change', pane: true, params: { period: 12 }, color: '#30d158', levels: [0], calc: (c, p) => [{ v: c.map((k, i) => (i < p.period ? null : ((k.close / c[i - p.period].close) - 1) * 100)) }] },
    atr: { name: 'ATR', pane: true, params: { period: 14 }, color: '#ffc34d', calc: (c, p) => [{ v: atr(c, p.period) }] },
    zone: { name: 'Stoch zone (EMA)', pane: true, params: { ema: 10, lookback: 200 }, color: '#ff6b4a', range: [0, 100], levels: [20, 80], calc: (c, p) => {
      const e = ema(cl(c), p.ema); return [{ v: e.map((v, i) => { if (v == null) return null; let lo = Infinity, hi = -Infinity, n = 0; for (let j = Math.max(0, i - p.lookback + 1); j <= i; j++) if (e[j] != null) { lo = Math.min(lo, e[j]); hi = Math.max(hi, e[j]); n++; }
        if (n < Math.min(p.lookback, 30)) return null; return hi === lo ? 50 : ((v - lo) / (hi - lo)) * 100; }) }]; } },
    volume: { name: 'Volume', pane: true, params: {}, color: 'rgba(235,235,245,.35)', calc: (c) => [{ v: c.map((k) => k.volume ?? 0), bars: true }] },
  };
  K.INDICATOR_TYPES = TYPES;

  // ------------------------------------------------------------------ drawing tools
  const TOOLS = {
    cursor: { name: 'Cursor', pts: 0, icon: 'mouse-pointer-2' },
    seg: { name: 'Trend line', pts: 2, icon: 'trending-up' },
    ray: { name: 'Ray', pts: 2, icon: 'move-up-right' },
    ext: { name: 'Extended line', pts: 2, icon: 'move-diagonal' },
    h: { name: 'Horizontal line', pts: 1, icon: 'minus' },
    hray: { name: 'Horizontal ray', pts: 1, icon: 'arrow-right-to-line' },
    v: { name: 'Vertical line', pts: 1, icon: 'separator-vertical' },
    rect: { name: 'Rectangle', pts: 2, icon: 'square' },
    fib: { name: 'Fib retracement', pts: 2, icon: 'align-vertical-space-around' },
    measure: { name: 'Measure', pts: 2, icon: 'ruler' },
    text: { name: 'Text note', pts: 1, icon: 'type' },
    long: { name: 'Long position', pts: 2, icon: 'arrow-up-circle' },
    short: { name: 'Short position', pts: 2, icon: 'arrow-down-circle' },
  };
  K.CHART_TOOLS = TOOLS;
  const FIBS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1, 1.618];
  const DEFAULT_COLOR = { seg: '#5ac8fa', ray: '#5ac8fa', ext: '#64d2ff', h: '#ffc34d', hray: '#ffc34d', v: '#bf9cff', rect: '#5ac8fa', fib: '#ffc34d', measure: '#5ac8fa', text: '#f5f5f7', long: '#9bd35a', short: '#ff6b61' };

  // ------------------------------------------------------------------- chart
  class Chart {
    constructor(canvas, opts = {}) {
      this.c = canvas; this.opts = opts; this.data = null; this.inds = []; this.drawings = []; this.marks = null;
      this.show = { levels: true, kora: true, trades: true, drawings: true, marks: true };
      this.view = null; this.y = { auto: true, min: 0, max: 1 }; this.tool = 'cursor'; this.pending = null; this.sel = -1; this.magnet = true;
      this.mouse = null; this.ptrs = new Map(); this.drag = null; this.vel = 0; this.raf = 0; this.loadingMore = false;
      canvas.style.touchAction = 'none'; canvas.tabIndex = 0; canvas.style.outline = 'none';
      canvas.addEventListener('pointerdown', (e) => this.onDown(e));
      canvas.addEventListener('pointermove', (e) => this.onMove(e));
      canvas.addEventListener('pointerup', (e) => this.onUp(e));
      canvas.addEventListener('pointercancel', (e) => this.onUp(e));
      canvas.addEventListener('pointerleave', () => { if (!this.ptrs.size) { this.mouse = null; this.req(); } });
      canvas.addEventListener('wheel', (e) => this.onWheel(e), { passive: false });
      canvas.addEventListener('dblclick', (e) => this.onDbl(e));
      canvas.addEventListener('keydown', (e) => this.onKey(e));
      canvas.addEventListener('contextmenu', (e) => { e.preventDefault(); this.setTool('cursor'); this.pending = null; this.req(); });
      window.addEventListener('resize', () => this.req());
    }
    // ---------------------------------------------------------------- data
    set(data) {
      const first = !this.data || this.data.code !== data.code || this.data.tf !== data.tf;
      const prevN = this.data ? this.data.candles.length : 0;
      this.data = data; this.tfms = TF_MS[data.tf] || 9e5;
      if (first || !this.view) this.resetView();
      else if (data.candles.length > prevN && this.view) { /* history prepended: keep the same candles in view */
        const lastOld = this.data.candles.length - prevN; if (this._prepending) this.view.end += lastOld; }
      this._prepending = false;
      this.compute(); this.req();
    }
    resetView() { const n = this.data ? this.data.candles.length : 0; const cnt = Math.min(n, Math.max(60, Math.round((this.c.clientWidth || 900) / 8))); this.view = { end: n + cnt * 0.06, count: cnt }; this.y.auto = true; }
    setIndicators(list) { this.inds = list.map((x) => ({ ...x })); this.compute(); this.req(); }
    setMarks(m) { this.marks = m; this.req(); }
    compute() { if (!this.data) return; for (const ind of this.inds) { const T = TYPES[ind.type]; try { ind.series = T ? T.calc(this.data.candles, { ...T.params, ...ind.params }) : []; } catch (e) { ind.series = []; } } }
    live(price) {
      if (!this.data || !price) return; const k = this.data.candles[this.data.candles.length - 1];
      k.close = price; k.high = Math.max(k.high, price); k.low = Math.min(k.low, price); this.compute(); this.req();
    }
    setTool(t) { this.tool = TOOLS[t] ? t : 'cursor'; this.pending = null; this.c.style.cursor = this.tool === 'cursor' ? 'default' : 'crosshair'; if (this.opts.onTool) this.opts.onTool(this.tool); }
    changed() { if (this.opts.onDrawings) this.opts.onDrawings(this.drawings); }
    req() { if (this.raf) return; this.raf = requestAnimationFrame(() => { this.raf = 0; this.draw(); }); }

    // ------------------------------------------------------------- geometry
    get L() { return this._L || {}; }
    idxToX(i) { const L = this.L; return (i - L.start) * L.cw + L.cw / 2; }
    xToIdx(x) { const L = this.L; return L.start + (x - L.cw / 2) / L.cw; }
    priceToY(p) { const L = this.L; return L.top + (L.mainH - L.top - 8) * (1 - (p - L.mn) / ((L.mx - L.mn) || 1)); }
    yToPrice(y) { const L = this.L; return L.mn + (L.mx - L.mn) * (1 - (y - L.top) / ((L.mainH - L.top - 8) || 1)); }
    timeToIdx(t) {
      const a = this.data.candles, n = a.length; if (!n) return 0;
      if (t >= a[n - 1].time) return n - 1 + (Date.parse(t) - Date.parse(a[n - 1].time)) / this.tfms;
      if (t <= a[0].time) return (Date.parse(t) - Date.parse(a[0].time)) / this.tfms;
      let lo = 0, hi = n - 1; while (lo < hi) { const m = (lo + hi + 1) >> 1; if (a[m].time <= t) lo = m; else hi = m - 1; }
      const frac = lo < n - 1 ? (Date.parse(t) - Date.parse(a[lo].time)) / Math.max(1, Date.parse(a[lo + 1].time) - Date.parse(a[lo].time)) : 0;
      return lo + Math.max(0, Math.min(1, frac));
    }
    idxToTime(i) {
      const a = this.data.candles, n = a.length, r = Math.round(i);
      if (r >= 0 && r < n) return a[r].time;
      const base = r < 0 ? a[0] : a[n - 1], off = r < 0 ? r : r - (n - 1);
      return new Date(Date.parse(base.time) + off * this.tfms).toISOString().slice(0, 19) + '+00:00';
    }
    pointAt(x, y, snap = true) {
      let i = this.xToIdx(x), p = this.yToPrice(y);
      const ri = Math.round(i), k = this.data.candles[ri];
      if (snap && this.magnet && k) { const opts = [k.open, k.high, k.low, k.close]; let best = null, bd = 12;
        for (const v of opts) { const d = Math.abs(this.priceToY(v) - y); if (d < bd) { bd = d; best = v; } } if (best != null) p = best; }
      return { t: this.idxToTime(ri), p };
    }
    zoneAt(x, y) { const L = this.L; if (x > L.plotW) return y < L.mainH ? 'price' : 'none'; if (y > L.H - L.padB) return 'time'; if (y > L.mainH) return 'pane'; return 'plot'; }
    clampView() {
      const n = this.data.candles.length, v = this.view;
      v.count = Math.max(8, Math.min(Math.max(n * 1.3, 60), v.count));
      v.end = Math.max(Math.min(v.count * 0.25, n), Math.min(n + v.count * 0.5, v.end));
      if (this.opts.onNeedMore && v.end - v.count < 3 && !this.loadingMore) { this.loadingMore = true; this._prepending = true; this.opts.onNeedMore(() => { this.loadingMore = false; }); }
    }

    // --------------------------------------------------------------- events
    pos(e) { const r = this.c.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; }
    onDown(e) {
      if (!this.data) return; this.c.focus({ preventScroll: true }); this.c.setPointerCapture(e.pointerId);
      const p = this.pos(e); this.ptrs.set(e.pointerId, p); this.vel = 0; cancelAnimationFrame(this._inertia || 0);
      if (this.ptrs.size === 2) { const [a, b] = [...this.ptrs.values()]; this.drag = { mode: 'pinch', d0: Math.hypot(a.x - b.x, a.y - b.y), count: this.view.count, mid: (a.x + b.x) / 2, idx: this.xToIdx((a.x + b.x) / 2) }; return; }
      const zone = this.zoneAt(p.x, p.y);
      if (zone === 'price') { this.drag = { mode: 'yscale', y: p.y, mn: this.L.mn, mx: this.L.mx }; this.y.auto = false; this.y.min = this.L.mn; this.y.max = this.L.mx; return; }
      if (zone === 'time') { this.drag = { mode: 'xzoom', x: p.x, count: this.view.count, end: this.view.end }; return; }
      if (zone === 'plot' && this.tool !== 'cursor') { this.placePoint(p); return; }
      if (zone === 'plot' && this.show.drawings) {
        const hit = this.hitTest(p.x, p.y);
        if (hit) { this.sel = hit.i; const d = this.drawings[hit.i]; this.drag = { mode: hit.handle != null ? 'handle' : 'move', i: hit.i, h: hit.handle, start: p, orig: JSON.parse(JSON.stringify(d)), moved: false };
          this.opts.onSelect && this.opts.onSelect(d); this.req(); return; }
        if (this.sel >= 0) { this.sel = -1; this.opts.onSelect && this.opts.onSelect(null); }
      }
      this.drag = { mode: 'pan', x: p.x, y: p.y, end: this.view.end, mn: this.L.mn, mx: this.L.mx, t: performance.now(), lx: p.x, lt: performance.now() };
      this.c.style.cursor = 'grabbing';
    }
    onMove(e) {
      if (!this.data) return; const p = this.pos(e); this.mouse = p;
      if (this.ptrs.has(e.pointerId)) this.ptrs.set(e.pointerId, p);
      const d = this.drag;
      if (d && d.mode === 'pinch' && this.ptrs.size === 2) { const [a, b] = [...this.ptrs.values()], dist = Math.hypot(a.x - b.x, a.y - b.y) || 1, mid = (a.x + b.x) / 2;
        const cnt = d.count * (d.d0 / dist), L = this.L; this.view.count = cnt; const cwN = L.plotW / cnt; this.view.end = d.idx + (L.plotW - mid) / cwN + 0.5; this.clampView(); this.req(); return; }
      if (d && d.mode === 'pan') {
        const L = this.L; this.view.end = d.end - (p.x - d.x) / L.cw;
        if (!this.y.auto) { const dp = ((p.y - d.y) / (L.mainH - L.top - 8)) * (d.mx - d.mn); this.y.min = d.mn + dp; this.y.max = d.mx + dp; }
        const now = performance.now(); if (now - d.lt > 0) { this.vel = (p.x - d.lx) / (now - d.lt); d.lx = p.x; d.lt = now; }
        this.clampView(); this.req(); return; }
      if (d && d.mode === 'yscale') { const f = Math.exp((p.y - d.y) / 160), mid = (d.mn + d.mx) / 2, half = ((d.mx - d.mn) / 2) * f; this.y.min = mid - half; this.y.max = mid + half; this.req(); return; }
      if (d && d.mode === 'xzoom') { const f = Math.exp(-(p.x - d.x) / 200); this.view.count = d.count * f; this.view.end = d.end; this.clampView(); this.req(); return; }
      if (d && (d.mode === 'move' || d.mode === 'handle')) { this.dragDrawing(d, p); return; }
      // hover cursor
      if (!d) { const z = this.zoneAt(p.x, p.y); this.c.style.cursor = z === 'price' ? 'ns-resize' : z === 'time' ? 'ew-resize' : this.tool !== 'cursor' ? 'crosshair' : (this.show.drawings && this.hitTest(p.x, p.y) ? 'move' : 'default'); }
      this.req();
    }
    onUp(e) {
      this.ptrs.delete(e.pointerId); const d = this.drag; this.drag = null; this.c.style.cursor = this.tool === 'cursor' ? 'default' : 'crosshair';
      if (d && (d.mode === 'move' || d.mode === 'handle') && d.moved) this.changed();
      if (d && d.mode === 'pan' && Math.abs(this.vel) > 0.15 && performance.now() - d.lt < 60) this.coast();
      if (this.ptrs.size === 1) { const [q] = [...this.ptrs.values()]; this.drag = { mode: 'pan', x: q.x, y: q.y, end: this.view.end, mn: this.L.mn, mx: this.L.mx, lx: q.x, lt: performance.now() }; }
    }
    coast() { let v = this.vel * 16; const step = () => { v *= 0.93; if (Math.abs(v) < 0.4) return; this.view.end -= v / this.L.cw; this.clampView(); this.draw(); this._inertia = requestAnimationFrame(step); }; this._inertia = requestAnimationFrame(step); }
    onWheel(e) {
      if (!this.data) return; e.preventDefault(); const p = this.pos(e), L = this.L;
      if (Math.abs(e.deltaX) > Math.abs(e.deltaY) && !e.ctrlKey) { this.view.end += e.deltaX / L.cw; this.clampView(); this.req(); return; }
      if (this.zoneAt(p.x, p.y) === 'price') { this.y.auto = false; const f = Math.exp(e.deltaY / 400), mid = (L.mn + L.mx) / 2, half = ((L.mx - L.mn) / 2) * f; this.y.min = mid - half; this.y.max = mid + half; this.req(); return; }
      const f = Math.exp(Math.max(-120, Math.min(120, e.ctrlKey ? e.deltaY * 3 : e.deltaY)) / 650), idx = this.xToIdx(Math.min(p.x, L.plotW));
      const cnt = this.view.count * f; this.view.count = cnt; this.clampView();
      const cwN = L.plotW / this.view.count; this.view.end = idx + (L.plotW - Math.min(p.x, L.plotW)) / cwN + 0.5; this.clampView(); this.req();
    }
    onDbl(e) { const p = this.pos(e), z = this.zoneAt(p.x, p.y);
      if (z === 'price') { this.y.auto = true; this.req(); return; }
      if (z === 'plot' && this.tool === 'cursor') { const h = this.hitTest(p.x, p.y); if (h && this.drawings[h.i].type === 'text') { const d = this.drawings[h.i]; const t = prompt('Edit note', d.text); if (t != null) { d.text = t; this.changed(); this.req(); } return; } }
      this.resetView(); this.req(); }
    onKey(e) {
      if (!this.data) return; const v = this.view;
      if (e.key === 'ArrowLeft') { v.end -= Math.max(1, v.count * 0.08); }
      else if (e.key === 'ArrowRight') { v.end += Math.max(1, v.count * 0.08); }
      else if (e.key === '+' || e.key === '=' || e.key === 'ArrowUp') { v.count *= 0.85; }
      else if (e.key === '-' || e.key === 'ArrowDown') { v.count *= 1.18; }
      else if (e.key === 'Home') { this.resetView(); }
      else if (e.key === 'Escape') { this.setTool('cursor'); this.pending = null; this.sel = -1; this.opts.onSelect && this.opts.onSelect(null); }
      else if ((e.key === 'Delete' || e.key === 'Backspace') && this.sel >= 0) { this.drawings.splice(this.sel, 1); this.sel = -1; this.changed(); this.opts.onSelect && this.opts.onSelect(null); }
      else if (e.key.toLowerCase() === 'z' && (e.ctrlKey || e.metaKey) && this.drawings.length) { this.drawings.pop(); this.sel = -1; this.changed(); }
      else return;
      e.preventDefault(); this.clampView(); this.req();
    }

    // ------------------------------------------------------------- drawings
    placePoint(p) {
      const T = TOOLS[this.tool], pt = this.pointAt(p.x, p.y);
      if (T.pts === 1) {
        const d = { type: this.tool, pts: [pt], color: DEFAULT_COLOR[this.tool] };
        if (this.tool === 'text') { const t = prompt('Note text'); if (!t) return; d.text = t; }
        this.drawings.push(d); this.sel = this.drawings.length - 1; this.changed(); this.setTool('cursor'); this.req(); return;
      }
      if (!this.pending) { this.pending = { type: this.tool, pts: [pt], color: DEFAULT_COLOR[this.tool] }; this.req(); return; }
      const d = this.pending; d.pts.push(pt);
      if (d.type === 'long' || d.type === 'short') { const e0 = d.pts[0].p, s0 = d.pts[1].p; d.tp = e0 + 2 * (e0 - s0); d.pts[1] = { t: d.pts[1].t, p: s0 }; }
      this.drawings.push(d); this.pending = null; this.sel = this.drawings.length - 1; this.changed(); this.setTool('cursor'); this.req();
    }
    dragDrawing(d, p) {
      const dr = this.drawings[d.i]; if (!dr) return; d.moved = true;
      const di = this.xToIdx(p.x) - this.xToIdx(d.start.x), dp = this.yToPrice(p.y) - this.yToPrice(d.start.y);
      if (d.mode === 'move') { dr.pts = d.orig.pts.map((q) => ({ t: this.idxToTime(this.timeToIdx(q.t) + di), p: q.p + dp })); if (d.orig.tp != null) dr.tp = d.orig.tp + dp; }
      else if (d.h === 'tp') dr.tp = this.pointAt(p.x, p.y).p;
      else { const q = this.pointAt(p.x, p.y); dr.pts[d.h] = dr.type === 'h' ? { t: dr.pts[0].t, p: q.p } : q; }
      this.req();
    }
    shape(dr) { /* screen geometry for hit-testing and drawing */
      const L = this.L, P = dr.pts.map((q) => ({ x: this.idxToX(this.timeToIdx(q.t)), y: this.priceToY(q.p) }));
      return { P, L };
    }
    hitTest(x, y) {
      const near = (ax, ay, bx, by) => { const dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy || 1, t = Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / l2)); return Math.hypot(x - (ax + t * dx), y - (ay + t * dy)); };
      for (let i = this.drawings.length - 1; i >= 0; i--) {
        const dr = this.drawings[i]; if (!dr.pts) continue; const { P, L } = this.shape(dr), tol = 6;
        if (i === this.sel) { for (let k = 0; k < P.length; k++) if (Math.hypot(P[k].x - x, P[k].y - y) < 8) return { i, handle: k };
          if ((dr.type === 'long' || dr.type === 'short') && dr.tp != null) { const ty = this.priceToY(dr.tp); if (Math.abs(ty - y) < 7 && x > P[0].x && x < P[1].x + 60) return { i, handle: 'tp' }; } }
        const t = dr.type;
        if (t === 'h' && Math.abs(P[0].y - y) < tol) return { i };
        if (t === 'hray' && Math.abs(P[0].y - y) < tol && x >= P[0].x - tol) return { i };
        if (t === 'v' && Math.abs(P[0].x - x) < tol) return { i };
        if (t === 'text' && x >= P[0].x - 4 && x <= P[0].x + 8 + 7 * (dr.text || '').length && Math.abs(y - P[0].y) < 12) return { i };
        if (P.length < 2) continue;
        const [a, b] = P;
        if (t === 'seg' && near(a.x, a.y, b.x, b.y) < tol) return { i };
        if (t === 'ray' || t === 't' || t === 'ext') { const sl = (b.y - a.y) / ((b.x - a.x) || 1e-9), x0 = t === 'ext' ? 0 : a.x, x1 = L.plotW; if (near(x0, a.y + sl * (x0 - a.x), x1, a.y + sl * (x1 - a.x)) < tol) return { i }; }
        if (t === 'rect' || t === 'measure' || t === 'fib' || t === 'long' || t === 'short') {
          let x0 = Math.min(a.x, b.x), x1 = Math.max(a.x, b.x), y0 = Math.min(a.y, b.y), y1 = Math.max(a.y, b.y);
          if (t === 'fib') { x1 = Math.max(x1, L.plotW * 0.98); }
          if (t === 'long' || t === 'short') { const ty = this.priceToY(dr.tp); y0 = Math.min(y0, ty); y1 = Math.max(y1, ty); x1 = Math.max(x1, x0 + 60); }
          if (x >= x0 - tol && x <= x1 + tol && y >= y0 - tol && y <= y1 + tol) return { i };
        }
      }
      return null;
    }
    drawDrawing(x, dr, sel, dd) {
      const { P, L } = this.shape(dr), col = dr.color || '#5ac8fa', t = dr.type;
      x.save(); x.strokeStyle = col; x.fillStyle = col; x.lineWidth = sel ? 2 : 1.5;
      const lbl = (txt, px, py, c = col, align = 'left') => { x.font = FONT; const w = x.measureText(txt).width + 10; const lx = align === 'right' ? px - w : px; x.save(); x.globalAlpha = 0.9; x.fillStyle = '#151817'; x.beginPath(); x.roundRect(lx, py - 9, w, 18, 5); x.fill(); x.restore(); x.fillStyle = c; x.fillText(txt, lx + 5, py + 4); };
      if (t === 'h') { x.beginPath(); x.moveTo(0, P[0].y); x.lineTo(L.plotW, P[0].y); x.stroke(); lbl(K.fm(dr.pts[0].p, dd), L.plotW - 4, P[0].y, col, 'right'); }
      else if (t === 'hray') { x.beginPath(); x.moveTo(P[0].x, P[0].y); x.lineTo(L.plotW, P[0].y); x.stroke(); lbl(K.fm(dr.pts[0].p, dd), L.plotW - 4, P[0].y, col, 'right'); }
      else if (t === 'v') { x.beginPath(); x.moveTo(P[0].x, 0); x.lineTo(P[0].x, L.mainH); x.stroke(); }
      else if (t === 'text') { x.font = '600 12.5px -apple-system,Inter,sans-serif'; x.fillText(dr.text || '', P[0].x + 4, P[0].y + 4); }
      else if (P.length >= 2) {
        const [a, b] = P;
        if (t === 'seg') { x.beginPath(); x.moveTo(a.x, a.y); x.lineTo(b.x, b.y); x.stroke(); }
        else if (t === 'ray' || t === 't' || t === 'ext') { const sl = (b.y - a.y) / ((b.x - a.x) || 1e-9), x0 = t === 'ext' ? 0 : a.x, x1 = L.plotW * 1.01; x.beginPath(); x.moveTo(x0, a.y + sl * (x0 - a.x)); x.lineTo(x1, a.y + sl * (x1 - a.x)); x.stroke(); }
        else if (t === 'rect') { x.globalAlpha = 0.12; x.fillRect(Math.min(a.x, b.x), Math.min(a.y, b.y), Math.abs(b.x - a.x), Math.abs(b.y - a.y)); x.globalAlpha = 1; x.strokeRect(Math.min(a.x, b.x), Math.min(a.y, b.y), Math.abs(b.x - a.x), Math.abs(b.y - a.y)); }
        else if (t === 'fib') {
          const p0 = dr.pts[0].p, p1 = dr.pts[1].p, x0 = Math.min(a.x, b.x), x1 = L.plotW;
          FIBS.forEach((f, k) => { const pr = p1 + (p0 - p1) * f, y = this.priceToY(pr); x.globalAlpha = f === 0.5 || f === 0.618 ? 0.95 : 0.6;
            x.beginPath(); x.moveTo(x0, y); x.lineTo(x1, y); x.stroke(); x.globalAlpha = 1; x.font = FONT; x.fillText(`${f} (${K.fm(pr, dd)})`, x0 + 4, y - 3);
            if (k < FIBS.length - 1) { const y2 = this.priceToY(p1 + (p0 - p1) * FIBS[k + 1]); x.globalAlpha = 0.04 + (k % 2) * 0.03; x.fillRect(x0, Math.min(y, y2), x1 - x0, Math.abs(y2 - y)); x.globalAlpha = 1; } });
          x.setLineDash([3, 3]); x.beginPath(); x.moveTo(a.x, a.y); x.lineTo(b.x, b.y); x.stroke(); x.setLineDash([]);
        }
        else if (t === 'measure') {
          const p0 = dr.pts[0].p, p1 = dr.pts[1].p, up = p1 >= p0, c2 = up ? C.g : C.r; x.fillStyle = c2; x.strokeStyle = c2;
          x.globalAlpha = 0.13; x.fillRect(Math.min(a.x, b.x), Math.min(a.y, b.y), Math.abs(b.x - a.x), Math.abs(b.y - a.y)); x.globalAlpha = 1;
          x.beginPath(); x.moveTo(a.x, a.y); x.lineTo(b.x, b.y); x.stroke();
          const bars = Math.round(this.timeToIdx(dr.pts[1].t) - this.timeToIdx(dr.pts[0].t)), ms = Math.abs(Date.parse(dr.pts[1].t) - Date.parse(dr.pts[0].t)), hrs = ms / 36e5;
          lbl(`${up ? '+' : ''}${K.fm(p1 - p0, dd)} (${(((p1 - p0) / p0) * 100).toFixed(2)}%) · ${bars} bars · ${hrs >= 48 ? (hrs / 24).toFixed(1) + 'd' : hrs.toFixed(1) + 'h'}`, (a.x + b.x) / 2 - 80, Math.min(a.y, b.y) - 14, c2);
        }
        else if (t === 'long' || t === 'short') {
          const e0 = dr.pts[0].p, s0 = dr.pts[1].p, tp = dr.tp ?? e0 + 2 * (e0 - s0), x0 = a.x, x1 = Math.max(b.x, a.x + 60), ye = this.priceToY(e0), ys = this.priceToY(s0), yt = this.priceToY(tp);
          x.globalAlpha = 0.16; x.fillStyle = C.g; x.fillRect(x0, Math.min(ye, yt), x1 - x0, Math.abs(yt - ye)); x.fillStyle = C.r; x.fillRect(x0, Math.min(ye, ys), x1 - x0, Math.abs(ys - ye)); x.globalAlpha = 1;
          x.strokeStyle = 'rgba(235,235,245,.6)'; x.beginPath(); x.moveTo(x0, ye); x.lineTo(x1, ye); x.stroke();
          const rr = Math.abs(tp - e0) / (Math.abs(e0 - s0) || 1);
          lbl(`Target ${K.fm(tp, dd)} · ${rr.toFixed(2)}R · ${(((tp - e0) / e0) * 100).toFixed(2)}%`, x0 + 4, yt + (yt < ye ? -12 : 12), C.g);
          lbl(`Stop ${K.fm(s0, dd)} · ${(((s0 - e0) / e0) * 100).toFixed(2)}%`, x0 + 4, ys + (ys > ye ? 12 : -12), C.r);
          const el = `${t === 'long' ? 'Long' : 'Short'} ${K.fm(e0, dd)}`; if (x1 + 110 < L.plotW) lbl(el, x1 + 4, ye, '#f5f5f7'); else lbl(el, x0 - 4, ye, '#f5f5f7', 'right');
          if (sel) { x.fillStyle = C.g; x.beginPath(); x.arc((x0 + x1) / 2, yt, 4.5, 0, Math.PI * 2); x.fill(); }
        }
      }
      if (sel) { x.fillStyle = '#0f1211'; x.strokeStyle = col; x.lineWidth = 1.5; for (const q of P) { x.beginPath(); x.arc(q.x, q.y, 4.5, 0, Math.PI * 2); x.fill(); x.stroke(); } }
      x.restore();
    }

    // ----------------------------------------------------------------- draw
    draw() {
      const c = this.c, d = this.data; if (!c || !d || !d.candles.length) return;
      const dpr = window.devicePixelRatio || 1, W = c.clientWidth || 900;
      const panes = this.inds.filter((i) => TYPES[i.type] && TYPES[i.type].pane);
      const paneH = 104, H = (this.opts.height || 540) + panes.length * paneH, padR = 82, padB = 26, plotW = W - padR, mainH = H - padB - panes.length * paneH;
      if (c.style.height !== H + 'px') c.style.height = H + 'px';
      if (c.width !== Math.round(W * dpr) || c.height !== Math.round(H * dpr)) { c.width = Math.round(W * dpr); c.height = Math.round(H * dpr); }
      const x = c.getContext('2d'); x.setTransform(dpr, 0, 0, dpr, 0, 0); x.clearRect(0, 0, W, H);
      const all = d.candles, n = all.length, v = this.view, start = v.end - v.count, cw = plotW / v.count;
      const s = Math.max(0, Math.floor(start)), e = Math.min(n, Math.ceil(v.end) + 1), dd = d.digits > 4 ? 5 : d.digits;
      // y range
      let mn = Infinity, mx = -Infinity;
      for (let i = s; i < e; i++) { mn = Math.min(mn, all[i].low); mx = Math.max(mx, all[i].high); }
      if (!isFinite(mn)) { mn = all[n - 1].low; mx = all[n - 1].high; }
      const span0 = mx - mn || Math.abs(mx) * 0.01 || 1;
      for (const ind of this.inds) if (!TYPES[ind.type]?.pane) for (const sr of ind.series || []) for (let i = s; i < e; i++) { const val = sr.v[i]; if (val != null && Math.abs(val - (mn + mx) / 2) < span0 * 1.2) { mn = Math.min(mn, val); mx = Math.max(mx, val); } }
      const rg = mx - mn || span0; mn -= rg * 0.06; mx += rg * 0.06;
      if (!this.y.auto) { mn = this.y.min; mx = this.y.max; }
      this._L = { W, H, padR, padB, plotW, mainH, cw, start, mn, mx, top: 8 };
      const X = (i) => (i - start) * cw + cw / 2, Y = (p) => this.priceToY(p);
      x.font = FONT;
      // grid + price axis
      const niceStep = (r, target) => { const raw = r / target, mag = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / mag; return (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * mag; };
      const ps = niceStep(mx - mn, Math.max(3, Math.round(mainH / 70)));
      x.textAlign = 'left';
      for (let pv = Math.ceil(mn / ps) * ps; pv <= mx; pv += ps) { const y = Y(pv); if (y < 4 || y > mainH - 4) continue; x.strokeStyle = C.grid; x.beginPath(); x.moveTo(0, y); x.lineTo(plotW, y); x.stroke(); x.fillStyle = C.axis; x.fillText(K.fm(pv, dd), plotW + 8, y + 4); }
      // time axis
      const p2 = (q) => String(q).padStart(2, '0'), minLabel = 90, stepI = Math.max(1, Math.ceil(minLabel / cw));
      const nice = [1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256, 384, 512, 768, 1024].find((q) => q >= stepI) || stepI;
      x.fillStyle = C.axis; x.textAlign = 'center';
      for (let i = Math.ceil(start / nice) * nice; i < v.end; i += nice) {
        const tt = new Date(Date.parse(this.idxToTime(i))), px = X(i); if (px < 20 || px > plotW - 20) continue;
        x.strokeStyle = C.grid; x.beginPath(); x.moveTo(px, 0); x.lineTo(px, mainH); x.stroke();
        const lab = d.tf === 'D1' ? `${tt.getDate()} ${tt.toLocaleString('en', { month: 'short' })}` : (tt.getHours() === 0 && tt.getMinutes() === 0) || nice * this.tfms >= 864e5 ? `${tt.getDate()} ${tt.toLocaleString('en', { month: 'short' })}` : `${p2(tt.getHours())}:${p2(tt.getMinutes())}`;
        x.fillText(lab, px, H - 8);
      }
      x.textAlign = 'left';
      x.save(); x.beginPath(); x.rect(0, 0, plotW, mainH); x.clip();
      // S/R levels
      if (this.show.levels) (d.levels || []).forEach((lv) => { const y = Y(lv.price); if (y < 0 || y > mainH) return;
        x.fillStyle = lv.kind === 'support' ? 'rgba(155,211,90,.07)' : 'rgba(255,107,97,.07)'; x.fillRect(0, y - 3, plotW, 6); x.fillStyle = C.axis; x.fillText(`${lv.tf} ${lv.kind === 'support' ? 'S' : 'R'}`, plotW - 46, y - 6); });
      // Kora plan zone
      if (this.show.kora && d.plan && d.plan.entry != null) { const zx = Math.min(plotW * 0.78, X(n - 1) + 6), zw = plotW - zx, ye = Y(d.plan.entry), yt = Y(d.plan.tp1), ys = Y(d.plan.stop);
        x.fillStyle = 'rgba(155,211,90,.10)'; x.fillRect(zx, Math.min(ye, yt), zw, Math.abs(yt - ye)); x.fillStyle = 'rgba(255,107,97,.10)'; x.fillRect(zx, Math.min(ye, ys), zw, Math.abs(ys - ye));
        x.fillStyle = 'rgba(235,235,245,.6)'; x.fillText(`Kora ${d.plan.rr1}R`, zx + 6, Math.min(ye, yt) + 14); }
      // overlay indicators (fills first)
      const line = (vals, color, Yf, o = {}) => { x.save(); x.strokeStyle = color; x.globalAlpha = o.alpha ?? 1; x.lineWidth = o.width || 1.5; if (o.dash) x.setLineDash([4, 4]); x.beginPath(); let on = false;
        for (let i = s; i < e; i++) { const val = vals[i]; if (val == null) { on = false; continue; } const px = X(i), py = Yf(val); on ? x.lineTo(px, py) : x.moveTo(px, py); on = true; } x.stroke(); x.restore(); };
      for (const ind of this.inds) { if (TYPES[ind.type]?.pane) continue; const ser = ind.series || [];
        ser.forEach((sr) => { if (sr.fill == null || !ser[sr.fill]) return; const other = ser[sr.fill].v; x.save();
          for (let i = Math.max(s, 1); i < e; i++) { const a0 = sr.v[i - 1], a1 = sr.v[i], b0 = other[i - 1], b1 = other[i]; if ([a0, a1, b0, b1].some((q) => q == null)) continue;
            x.fillStyle = sr.fillColor === 'cloud' ? (a1 >= b1 ? 'rgba(48,209,88,.10)' : 'rgba(255,69,58,.10)') : (ind.color + '14');
            x.beginPath(); x.moveTo(X(i - 1), Y(a0)); x.lineTo(X(i), Y(a1)); x.lineTo(X(i), Y(b1)); x.lineTo(X(i - 1), Y(b0)); x.closePath(); x.fill(); }
          x.restore(); }); }
      // candles
      const bw = Math.max(1, Math.min(cw * 0.7, cw - 1));
      for (let i = s; i < e; i++) { const k = all[i], cx = X(i), up = k.close >= k.open, col = up ? C.g : C.r;
        x.strokeStyle = col; x.lineWidth = 1; x.beginPath(); x.moveTo(Math.round(cx) + 0.5, Y(k.high)); x.lineTo(Math.round(cx) + 0.5, Y(k.low)); x.stroke();
        const y1 = Y(Math.max(k.open, k.close)), y2 = Y(Math.min(k.open, k.close)); x.fillStyle = col; x.fillRect(cx - bw / 2, y1, bw, Math.max(1, y2 - y1)); }
      for (const ind of this.inds) { if (TYPES[ind.type]?.pane) continue; for (const sr of ind.series || []) {
        if (sr.dots) { x.fillStyle = sr.color || ind.color; for (let i = s; i < e; i++) { const val = sr.v[i]; if (val == null) continue; x.beginPath(); x.arc(X(i), Y(val), Math.max(1.2, Math.min(2.4, cw * 0.18)), 0, Math.PI * 2); x.fill(); } }
        else line(sr.v, sr.color || ind.color, Y, sr); } }
      // your trades
      if (this.show.trades && d.trades) for (const tr of d.trades) {
        const io = this.timeToIdx(tr.open_time.slice(0, 19)), ic = this.timeToIdx(tr.close_time.slice(0, 19)); if (ic < s - 1 || io > e + 1) continue;
        const buy = tr.direction === 'buy', yo = Y(tr.open_price), yc = Y(tr.close_price), win = tr.pnl >= 0;
        x.save(); x.strokeStyle = win ? 'rgba(155,211,90,.6)' : 'rgba(255,107,97,.6)'; x.setLineDash([3, 3]); x.beginPath(); x.moveTo(X(io), yo); x.lineTo(X(ic), yc); x.stroke(); x.restore();
        x.fillStyle = buy ? C.g : C.r; x.beginPath(); if (buy) { x.moveTo(X(io), yo + 3); x.lineTo(X(io) - 5, yo + 11); x.lineTo(X(io) + 5, yo + 11); } else { x.moveTo(X(io), yo - 3); x.lineTo(X(io) - 5, yo - 11); x.lineTo(X(io) + 5, yo - 11); } x.fill();
        x.beginPath(); x.arc(X(ic), yc, 4, 0, Math.PI * 2); x.fillStyle = win ? C.g : C.r; x.fill(); x.fillStyle = C.text; x.fillText((tr.pnl >= 0 ? '+' : '') + tr.pnl.toFixed(2), X(ic) + 7, yc + 4); }
      // strategy signals
      if (this.show.marks && this.marks && this.marks.marks) for (const m of this.marks.marks) {
        const i0 = this.timeToIdx(m.t), i1 = this.timeToIdx(m.te); if (i1 < s - 1 || i0 > e + 1) continue;
        const long = m.dir === 'long', y0 = Y(m.entry), ys = Y(m.stop), win = m.r > 0, col = win ? C.g : m.r < 0 ? C.r : C.axis;
        x.save(); x.globalAlpha = 0.09; x.fillStyle = C.r; x.fillRect(X(i0), Math.min(y0, ys), Math.max(2, X(i1) - X(i0)), Math.abs(ys - y0)); x.restore();
        x.save(); x.strokeStyle = col; x.lineWidth = 1.4; x.setLineDash([2, 3]); x.beginPath(); x.moveTo(X(i0), y0); x.lineTo(X(i1), y0); x.stroke(); x.restore();
        const ax = X(i0), ay = long ? Y(all[Math.round(Math.min(n - 1, i0))].low) + 8 : Y(all[Math.round(Math.min(n - 1, i0))].high) - 8;
        x.fillStyle = long ? C.g : C.r; x.beginPath(); if (long) { x.moveTo(ax, ay); x.lineTo(ax - 6, ay + 10); x.lineTo(ax + 6, ay + 10); } else { x.moveTo(ax, ay); x.lineTo(ax - 6, ay - 10); x.lineTo(ax + 6, ay - 10); } x.fill();
        if (cw > 2.5) { x.fillStyle = col; x.font = '600 10.5px -apple-system,Inter,sans-serif'; x.fillText(`${m.r > 0 ? '+' : ''}${m.r.toFixed(2)}R`, X(i1) + 4, y0 - 4); x.font = FONT; } }
      // position overlays (entry / SL / TP / Kora lines)
      const ovs = (d.overlays || []).filter((o) => this.show.kora || !o.kora), tags = [];
      ovs.forEach((o) => { const y = Y(o.price); if (y < -2 || y > mainH + 2) return; x.save(); x.strokeStyle = o.color; x.setLineDash(o.dash ? [6, 4] : []); x.globalAlpha = 0.7; x.beginPath(); x.moveTo(0, y); x.lineTo(plotW, y); x.stroke(); x.restore(); tags.push({ y, o }); });
      tags.sort((a, b) => a.y - b.y); let lastY = -Infinity; for (const t of tags) { t.ty = Math.max(t.y, lastY + 17); lastY = t.ty; }
      for (const t of tags) { const label = `${t.o.label} ${K.fm(t.o.price, dd)}`, w = x.measureText(label).width + 14, lx = plotW - w - 6;
        x.save(); x.globalAlpha = 0.92; x.fillStyle = '#151817'; x.strokeStyle = t.o.color; x.beginPath(); x.roundRect(lx, t.ty - 8, w, 16, 5); x.fill(); x.stroke(); x.restore(); x.fillStyle = t.o.color; x.fillText(label, lx + 7, t.ty + 4); }
      // drawings
      if (this.show.drawings) { this.drawings.forEach((dr, i) => { if (dr.type === 't' && dr.t1) { dr.pts = [{ t: dr.t1, p: dr.p1 }, { t: dr.t2, p: dr.p2 }]; dr.type = 'ray'; }
          if (dr.type === 'h' && !dr.pts) dr.pts = [{ t: all[n - 1].time, p: dr.price }]; if (dr.pts) this.drawDrawing(x, dr, i === this.sel, dd); });
        if (this.pending && this.mouse) { const pv = { ...this.pending, pts: [...this.pending.pts, this.pointAt(this.mouse.x, this.mouse.y)] };
          if (pv.type === 'long' || pv.type === 'short') pv.tp = pv.pts[0].p + 2 * (pv.pts[0].p - pv.pts[1].p); x.save(); x.globalAlpha = 0.8; this.drawDrawing(x, pv, false, dd); x.restore(); } }
      x.restore(); // end plot clip
      // last price tag
      const last = all[n - 1].close, ly = Y(last), upLast = last >= all[n - 1].open;
      if (ly > 0 && ly < mainH) { x.save(); x.strokeStyle = upLast ? 'rgba(155,211,90,.55)' : 'rgba(255,107,97,.55)'; x.setLineDash([2, 3]); x.beginPath(); x.moveTo(0, ly); x.lineTo(plotW, ly); x.stroke(); x.restore();
        x.fillStyle = upLast ? C.g : C.r; x.beginPath(); x.roundRect(plotW + 2, ly - 10, padR - 6, 20, 6); x.fill(); x.fillStyle = '#0b0d0c'; x.fillText(K.fm(last, dd), plotW + 8, ly + 4); }
      if (!this.y.auto) { x.fillStyle = C.b; x.fillText('manual', plotW + 8, 14); }
      // panes
      panes.forEach((ind, pi) => {
        const T = TYPES[ind.type], top = mainH + pi * paneH, h = paneH - 12;
        x.strokeStyle = 'rgba(255,255,255,.08)'; x.beginPath(); x.moveTo(0, top + 4); x.lineTo(W, top + 4); x.stroke();
        let lo = Infinity, hi = -Infinity;
        if (T.range) [lo, hi] = T.range; else for (const sr of ind.series || []) for (let i = s; i < e; i++) { const val = sr.v[i]; if (val != null) { lo = Math.min(lo, val); hi = Math.max(hi, val); } }
        if (!isFinite(lo)) return; if (T.name === 'Volume' || (ind.series || []).some((sr) => sr.hist)) lo = Math.min(lo, 0);
        const PY = (val) => top + 12 + (h - 12) * (1 - (val - lo) / ((hi - lo) || 1));
        (T.levels || []).forEach((lv) => { if (lv < lo || lv > hi) return; x.save(); x.strokeStyle = 'rgba(255,255,255,.13)'; x.setLineDash([3, 4]); x.beginPath(); x.moveTo(0, PY(lv)); x.lineTo(plotW, PY(lv)); x.stroke(); x.restore(); x.fillStyle = C.axis; x.fillText(lv, plotW + 8, PY(lv) + 4); });
        x.fillStyle = C.axis; x.fillText(K.fm(hi, Math.abs(hi) > 100 ? 0 : 2), plotW + 8, top + 20);
        x.save(); x.beginPath(); x.rect(0, top, plotW, paneH); x.clip();
        for (const sr of ind.series || []) {
          if (sr.hist || sr.bars) { for (let i = s; i < e; i++) { const val = sr.v[i]; if (val == null) continue; const y0 = PY(0), y1 = PY(val);
              x.fillStyle = sr.bars ? (all[i].close >= all[i].open ? 'rgba(155,211,90,.35)' : 'rgba(255,107,97,.35)') : (val >= 0 ? 'rgba(155,211,90,.55)' : 'rgba(255,107,97,.55)');
              x.fillRect(X(i) - Math.max(0.5, cw * 0.32), Math.min(y0, y1), Math.max(1, cw * 0.64), Math.abs(y1 - y0)); } }
          else line(sr.v, sr.color || ind.color, PY, sr);
        }
        x.restore();
      });
      // crosshair
      let hi = n - 1;
      if (this.mouse && this.mouse.x < plotW && this.mouse.y < H - padB && !(this.drag && this.drag.mode === 'pan')) {
        const { x: mx2, y: my } = this.mouse; hi = Math.max(0, Math.min(n - 1, Math.round(this.xToIdx(mx2)))); const sx = X(Math.round(this.xToIdx(mx2)));
        x.save(); x.strokeStyle = 'rgba(255,255,255,.28)'; x.setLineDash([3, 3]); x.beginPath(); x.moveTo(sx, 0); x.lineTo(sx, H - padB); x.stroke();
        if (my < mainH) { x.beginPath(); x.moveTo(0, my); x.lineTo(plotW, my); x.stroke(); }
        x.restore();
        if (my < mainH) { const pv = this.yToPrice(my); x.fillStyle = '#2c302f'; x.beginPath(); x.roundRect(plotW + 2, my - 10, padR - 6, 20, 6); x.fill(); x.fillStyle = '#f5f5f7'; x.fillText(K.fm(pv, dd), plotW + 8, my + 4); }
        const tl = new Date(Date.parse(this.idxToTime(Math.round(this.xToIdx(mx2))))), tlab = `${tl.toLocaleDateString('en', { day: 'numeric', month: 'short' })} ${p2(tl.getHours())}:${p2(tl.getMinutes())}`;
        x.font = FONT; const tw = x.measureText(tlab).width + 14; x.fillStyle = '#2c302f'; x.beginPath(); x.roundRect(Math.min(plotW - tw, Math.max(0, sx - tw / 2)), H - padB + 3, tw, 19, 6); x.fill(); x.fillStyle = '#f5f5f7'; x.textAlign = 'center'; x.fillText(tlab, Math.min(plotW - tw / 2, Math.max(tw / 2, sx)), H - 9); x.textAlign = 'left';
      }
      // legends
      x.font = FONT; let lx = 10;
      for (const ind of this.inds) { if (TYPES[ind.type]?.pane) continue; const val = ind.series?.[0]?.v[hi]; const label = `${TYPES[ind.type].name}${Object.values(ind.params || {}).length ? ' ' + Object.values(ind.params).join(',') : ''} ${val != null ? K.fm(val, dd) : ''}`;
        x.fillStyle = ind.color; x.fillText(label, lx, 18); lx += x.measureText(label).width + 16; }
      panes.forEach((ind, pi) => { const top = mainH + pi * paneH; const vals = (ind.series || []).map((sr) => sr.v[hi]).filter((q) => q != null).map((q) => K.fm(q, Math.abs(q) > 100 ? 0 : 2)).join(' · ');
        x.fillStyle = ind.color; x.fillText(`${TYPES[ind.type].name} ${Object.values(ind.params || {}).join(',')}  ${vals}`, 10, top + 22); });
      if (this.opts.onHover) this.opts.onHover(all[hi], hi);
    }
  }
  K.Chart = Chart;
})();
