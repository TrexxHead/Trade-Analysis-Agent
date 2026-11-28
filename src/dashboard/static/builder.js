/* Kora strategy builder: renders and edits rule-based strategies.
   Shared by the Strategy Lab (Build & test) and the chart's "Build" panel. */
(function () {
  const K = window.KORA;
  const OPS = { '>': 'is above', '<': 'is below', '>=': 'is at or above', '<=': 'is at or below', crosses_above: 'crosses above', crosses_below: 'crosses below', rising: 'is rising over', falling: 'is falling over' };
  const PRICES = ['close', 'open', 'high', 'low'];
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

  class Builder {
    constructor(root, catalog, onChange) {
      this.root = root; this.cat = Object.fromEntries(catalog.map((c) => [c.key, c])); this.catalog = catalog;
      this.onChange = onChange || (() => {}); this.p = null; this.readonly = false;
    }
    set(params, readonly = false) { this.p = JSON.parse(JSON.stringify(params || {})); this.readonly = readonly; this.fill(); this.render(); }
    fill() {
      const p = this.p;
      p.long ||= []; p.short ||= []; p.exit_long ||= []; p.exit_short ||= [];
      p.stop = { type: 'atr', mult: 1.5, bars: 10, pct: 0.5, max_atr: 4, ...(p.stop || {}) };
      p.entry_tf ||= 'M15'; p.exit ||= 'fixed_r'; p.tp_r ??= 1.5; p.trail ??= 14; p.trail_atr ??= 3; p.max_hold ??= 48; p.directions ||= 'both';
      if (p.mirror === undefined) p.mirror = !p.short.length;
    }
    value() { return JSON.parse(JSON.stringify(this.p)); }
    describeOp(o) {
      if (!o) return '?';
      if ('value' in o) return String(o.value);
      let s;
      if ('price' in o) s = o.price;
      else { const c = this.cat[o.ind] || { name: o.ind, params: {}, outputs: [] }; const ps = Object.values({ ...c.params, ...(o.params || {}) }).join(',');
        s = `${c.name}${ps ? '(' + ps + ')' : ''}${c.outputs.length > 1 ? ' ' + (o.out || c.outputs[0]) : ''}`; }
      return o.shift ? `${s} [${o.shift} ago]` : s;
    }
    describe(c) { const b = c.op === 'rising' || c.op === 'falling' ? `${c.b?.value ?? 1} candles` : this.describeOp(c.b);
      return `${this.describeOp(c.a)} ${OPS[c.op] || c.op} ${b}${(c.within || 1) > 1 ? ` (within ${c.within} candles)` : ''}`; }

    operandHTML(o, path) {
      const kind = 'value' in o ? 'value' : 'price' in o ? 'price:' + o.price : 'ind:' + o.ind;
      const opts = ['<optgroup label="Price">' + PRICES.map((p) => `<option value="price:${p}" ${kind === 'price:' + p ? 'selected' : ''}>${p[0].toUpperCase() + p.slice(1)}</option>`).join('') + '</optgroup>',
        `<option value="value" ${kind === 'value' ? 'selected' : ''}>Number</option>`,
        '<optgroup label="Indicators">' + this.catalog.map((c) => `<option value="ind:${c.key}" ${kind === 'ind:' + c.key ? 'selected' : ''}>${esc(c.name)}</option>`).join('') + '</optgroup>'].join('');
      let extra = '';
      if ('value' in o) extra = `<input class="field bnum" data-path="${path}.value" type="number" step="any" value="${o.value}">`;
      else if (o.ind) {
        const c = this.cat[o.ind] || { params: {}, outputs: [] };
        extra = Object.entries({ ...c.params, ...(o.params || {}) }).map(([k, v]) => `<label class="bp">${k}<input class="field bnum" data-path="${path}.params.${k}" type="number" step="any" value="${v}"></label>`).join('');
        if (c.outputs.length > 1) extra += `<select class="field bsel" data-path="${path}.out">${c.outputs.map((x) => `<option ${x === (o.out || c.outputs[0]) ? 'selected' : ''}>${x}</option>`).join('')}</select>`;
      }
      if (!('value' in o)) extra += `<label class="bp adv" title="Use the value from N candles ago">ago<input class="field bnum" data-path="${path}.shift" type="number" min="0" value="${o.shift || 0}" style="width:46px"></label>`;
      return `<span class="bop"><select class="field bsel" data-kind="${path}">${opts}</select>${extra}</span>`;
    }
    condHTML(c, list, i) {
      const path = `${list}.${i}`, ro = this.readonly ? 'disabled' : '';
      const rf = c.op === 'rising' || c.op === 'falling';
      const adv = (c.within || 1) > 1 || (c.a && c.a.shift) || (c.b && c.b.shift);
      return `<div class="bcond ${adv ? 'open' : ''}" ${ro ? 'data-ro' : ''}>
        ${this.operandHTML(c.a || { price: 'close' }, path + '.a')}
        <select class="field bsel bopsel" data-path="${path}.op">${Object.entries(OPS).map(([k, l]) => `<option value="${k}" ${k === c.op ? 'selected' : ''}>${l}</option>`).join('')}</select>
        ${rf ? `<input class="field bnum" data-path="${path}.b.value" type="number" min="1" value="${c.b?.value ?? 1}"><span class="bp">candles</span>` : this.operandHTML(c.b || { value: 0 }, path + '.b')}
        <label class="bp adv" title="True on any of the last N candles">within<input class="field bnum" data-path="${path}.within" type="number" min="1" value="${c.within || 1}" style="width:46px"> candles</label>
        <button class="bmore" title="Timing options: 'N candles ago' and 'within N candles'" aria-label="Timing options">⋯</button>
        <button class="bx" data-del="${path}" title="Remove rule" aria-label="Remove rule">×</button>
      </div>`;
    }
    listHTML(key, title, hint) {
      const items = this.p[key] || [];
      return `<div class="bsec"><div class="bsec-h"><b>${title}</b><span class="note">${hint}</span></div>
        ${items.map((c, i) => this.condHTML(c, key, i)).join('') || '<div class="note" style="padding:6px 0">No rules yet.</div>'}
        ${this.readonly ? '' : `<button class="obtn" data-add="${key}" style="margin-top:6px">+ Add rule</button>`}</div>`;
    }
    render() {
      const p = this.p, ro = this.readonly;
      const tfs = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'];
      const exits = this.exits || {};
      this.root.innerHTML = `
        ${ro ? '<div class="info" style="margin-bottom:10px"><i data-lucide="lock"></i><span>Library strategy: read-only. Press <b>Copy to my strategies</b> to edit it.</span></div>' : ''}
        <fieldset ${ro ? 'disabled' : ''} style="border:0;padding:0;margin:0">
        ${this.listHTML('long', 'Buy when ALL of these are true', 'checked on each closed candle')}
        <label class="bchk"><input type="checkbox" data-path="mirror" ${p.mirror ? 'checked' : ''}> Sell rules = mirror of the buy rules (crosses above ↔ below, RSI 30 ↔ 70, low ↔ high)</label>
        ${p.mirror ? '' : this.listHTML('short', 'Sell when ALL of these are true', '')}
        <div class="bgrid">
          <div><label class="fl">Entry chart</label><select class="field" data-path="entry_tf">${tfs.map((t) => `<option ${t === p.entry_tf ? 'selected' : ''}>${t}</option>`).join('')}</select></div>
          <div><label class="fl">Directions</label><select class="field" data-path="directions">${[['both', 'Both'], ['long', 'Buys only'], ['short', 'Sells only'], ['drift', 'Drift only (Boom sells, Crash buys)']].map(([k, l]) => `<option value="${k}" ${k === p.directions ? 'selected' : ''}>${l}</option>`).join('')}</select></div>
          <div><label class="fl">Stop</label><select class="field" data-path="stop.type">${[['atr', 'ATR multiple'], ['swing', 'Beyond recent swing'], ['pct', 'Percent of price']].map(([k, l]) => `<option value="${k}" ${k === p.stop.type ? 'selected' : ''}>${l}</option>`).join('')}</select></div>
          ${p.stop.type === 'atr' ? `<div><label class="fl">Stop × ATR</label><input class="field" type="number" step="0.1" data-path="stop.mult" value="${p.stop.mult}"></div>` : ''}
          ${p.stop.type === 'swing' ? `<div><label class="fl">Swing candles</label><input class="field" type="number" data-path="stop.bars" value="${p.stop.bars}"></div>` : ''}
          ${p.stop.type === 'pct' ? `<div><label class="fl">Stop %</label><input class="field" type="number" step="0.05" data-path="stop.pct" value="${p.stop.pct}"></div>` : ''}
          <div><label class="fl">Skip if stop > N ATR</label><input class="field" type="number" step="0.5" data-path="stop.max_atr" value="${p.stop.max_atr}"></div>
          <div style="grid-column:span 2"><label class="fl">Exit</label><select class="field" data-path="exit">${Object.entries(exits).map(([k, l]) => `<option value="${k}" ${k === p.exit ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select></div>
          ${p.exit === 'fixed_r' ? `<div><label class="fl">Target (R)</label><input class="field" type="number" step="0.1" data-path="tp_r" value="${p.tp_r}"></div>` : ''}
          ${['ema_trail', 'scale'].includes(p.exit) ? `<div><label class="fl">Trail EMA</label><input class="field" type="number" data-path="trail" value="${p.trail}"></div>` : ''}
          ${p.exit === 'atr_trail' ? `<div><label class="fl">Trail × ATR</label><input class="field" type="number" step="0.1" data-path="trail_atr" value="${p.trail_atr}"></div>` : ''}
          <div><label class="fl">Max hold (candles)</label><input class="field" type="number" data-path="max_hold" value="${p.max_hold}"></div>
          <div><label class="fl">Only enter between (UTC)</label><div style="display:flex;gap:6px;align-items:center">
            <input class="field" type="number" min="0" max="24" data-session="0" value="${p.session ? p.session[0] : ''}" placeholder="any" style="width:70px">–
            <input class="field" type="number" min="0" max="24" data-session="1" value="${p.session ? p.session[1] : ''}" placeholder="any" style="width:70px"></div></div>
        </div>
        ${p.exit === 'cond' ? this.listHTML('exit_long', 'Exit a buy when ALL of these are true', p.mirror ? 'sells use the mirror' : '') + (p.mirror ? '' : this.listHTML('exit_short', 'Exit a sell when ALL of these are true', '')) : ''}
        </fieldset>
        <div class="bplain"><b>In plain English:</b> ${this.plain()}</div>`;
      this.bind();
      if (window.lucide) lucide.createIcons();
    }
    plain() {
      const p = this.p, j = (l) => l.map((c) => this.describe(c)).join(', and ');
      let s = p.long.length ? `Buy on ${p.entry_tf} when ${j(p.long)}.` : 'No buy rules yet.';
      s += p.mirror ? ' Sell on the mirror image.' : p.short.length ? ` Sell when ${j(p.short)}.` : '';
      const st = p.stop.type === 'atr' ? `${p.stop.mult} ATR away` : p.stop.type === 'swing' ? `beyond the last ${p.stop.bars} candles` : `${p.stop.pct}% away`;
      s += ` Stop ${st}. Exit rule: ${((this.exits || {})[p.exit] || p.exit).replace(/^Exit when /, 'when ')}${p.exit === 'fixed_r' ? ` (${p.tp_r}R)` : ''}.`;
      if (p.session) s += ` New trades only ${p.session[0]}:00–${p.session[1]}:00 UTC.`;
      return esc(s);
    }
    get(path) { return path.split('.').reduce((o, k) => (o == null ? o : o[k]), this.p); }
    put(path, val) { const ks = path.split('.'), last = ks.pop(); let o = this.p; for (const k of ks) { o[k] ??= {}; o = o[k]; } o[last] = val; }
    bind() {
      const R = this.root;
      R.querySelectorAll('[data-path]').forEach((el) => {
        el.onchange = () => {
          const path = el.dataset.path; let v = el.type === 'checkbox' ? el.checked : el.value;
          if (el.type === 'number') v = v === '' ? 0 : Number(v);
          if (path.endsWith('.shift') || path.endsWith('.within')) v = Math.max(path.endsWith('.within') ? 1 : 0, Math.round(v));
          this.put(path, v);
          if (path === 'mirror' && !v && !this.p.short.length) this.p.short = [];
          this.render(); this.onChange();
        };
      });
      R.querySelectorAll('[data-kind]').forEach((el) => {
        el.onchange = () => {
          const path = el.dataset.kind, v = el.value; let o;
          if (v === 'value') o = { value: 0 };
          else if (v.startsWith('price:')) o = { price: v.slice(6) };
          else { const c = this.cat[v.slice(4)]; o = { ind: c.key, params: { ...c.params } }; if (c.outputs.length > 1) o.out = c.outputs[0]; }
          this.put(path, o); this.render(); this.onChange();
        };
      });
      R.querySelectorAll('[data-session]').forEach((el) => {
        el.onchange = () => {
          const a = R.querySelector('[data-session="0"]').value, b = R.querySelector('[data-session="1"]').value;
          this.p.session = a === '' || b === '' ? null : [Number(a), Number(b)]; this.render(); this.onChange();
        };
      });
      R.querySelectorAll('.bmore').forEach((el) => el.onclick = () => el.closest('.bcond').classList.toggle('open'));
      R.querySelectorAll('[data-add]').forEach((el) => el.onclick = () => {
        this.p[el.dataset.add].push({ a: { price: 'close' }, op: 'crosses_above', b: { ind: 'ema', params: { period: 50 } } }); this.render(); this.onChange();
      });
      R.querySelectorAll('[data-del]').forEach((el) => el.onclick = () => {
        const [list, i] = el.dataset.del.split('.'); this.p[list].splice(Number(i), 1); this.render(); this.onChange();
      });
    }
  }
  K.Builder = Builder;

  // ---------------------------------------------------------- backtest result renderer
  K.renderBacktest = function (el, bt, summary, opts = {}) {
    if (!bt) { el.innerHTML = '<div class="empty">No backtest yet.</div>'; return; }
    const tone = (v) => (v == null ? 'w' : v > 0 ? 'g' : v < 0 ? 'r' : 'w');
    const R2 = (v) => (v == null ? '—' : (v > 0 ? '+' : '') + v.toFixed(2) + 'R');
    const rows = bt.grid.filter((g) => g.all.n).sort((a, b) => (b.all.expectancy ?? -9) - (a.all.expectancy ?? -9));
    const mine = opts.exit ? bt.grid.find((g) => g.exit === opts.exit && (g.htf === (opts.htf || 'none'))) : null;
    const best = bt.best ? bt.grid.find((g) => g.exit === bt.best.exit && g.htf === bt.best.htf) : null;
    const show = mine && mine.all.n ? mine : best || rows[0];
    const curve = (show && show.curve) || [];
    let svg = '';
    if (curve.length > 1) {
      const mn = Math.min(0, ...curve), mx = Math.max(0, ...curve), W = 600, H = 150, X = (i) => (i / (curve.length - 1)) * W, Y = (v) => H - 6 - ((v - mn) / ((mx - mn) || 1)) * (H - 12);
      const line = curve.map((v, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(''), col = curve[curve.length - 1] >= 0 ? '#9bd35a' : '#ff6b61';
      svg = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="width:100%;height:150px;display:block"><line x1="0" x2="${W}" y1="${Y(0)}" y2="${Y(0)}" stroke="rgba(255,255,255,.15)" stroke-dasharray="4 4"/><path d="${line}" fill="none" stroke="${col}" stroke-width="1.8" vector-effect="non-scaling-stroke"/></svg>`;
    }
    const a = show ? show.all : null;
    el.innerHTML = `
      <div class="info" style="margin-bottom:10px"><i data-lucide="sparkles"></i><span>${esc(summary || '')}</span></div>
      <div class="note" style="margin-bottom:8px">${esc(bt.code)} · ${esc(bt.tf)} · ${bt.bars} candles · ${(bt.from || '').slice(0, 10)} → ${(bt.to || '').slice(0, 10)} · spread ${bt.cost ? bt.cost.toPrecision(3) : '0'} charged per trade</div>
      ${a ? `<div class="bstats">
        <div><span>Trades</span><b>${a.n}</b></div><div><span>Win rate</span><b>${a.win_rate ?? '—'}%</b></div>
        <div><span>Per trade</span><b class="c-${tone(a.expectancy)}">${R2(a.expectancy)}</b></div><div><span>Total</span><b class="c-${tone(a.total_r)}">${R2(a.total_r)}</b></div>
        <div><span>Profit factor</span><b>${a.pf ?? '—'}</b></div><div><span>Worst drawdown</span><b>${a.max_dd_r}R</b></div><div><span>Loss streak</span><b>${a.max_losing_streak}</b></div></div>
        <div class="note" style="margin:8px 0 2px">Equity curve (R) · ${esc(show.exit_name)}${show.htf && show.htf !== 'none' ? ' · ' + show.htf + ' filter' : ''}</div>${svg}` : ''}
      <div style="overflow-x:auto;margin-top:10px"><table class="tbl"><thead><tr><th>Exit style</th>${bt.grid.some((g) => g.htf !== 'none') ? '<th>Filter</th>' : ''}<th>Trades</th><th>Win</th><th>Per trade</th><th>Total</th><th>PF</th><th>Drawdown</th><th>Buys</th><th>Sells</th></tr></thead><tbody>
      ${rows.map((g) => `<tr ${g === show ? 'style="background:rgba(90,200,250,.08)"' : ''}><td>${esc(g.exit_name)}${mine && g === mine ? ' <span class="tint b">yours</span>' : ''}${best && g === best ? ' <span class="tint g">best</span>' : ''}</td>
        ${bt.grid.some((x) => x.htf !== 'none') ? `<td>${g.htf}</td>` : ''}<td>${g.all.n}</td><td>${g.all.win_rate ?? '—'}%</td><td class="c-${tone(g.all.expectancy)}" style="font-weight:600">${R2(g.all.expectancy)}</td><td>${R2(g.all.total_r)}</td><td>${g.all.pf ?? '—'}</td><td>${g.all.max_dd_r}R</td>
        <td class="c-${tone(g.long.expectancy)}">${g.long.n ? R2(g.long.expectancy) + ' · ' + g.long.n : '—'}</td><td class="c-${tone(g.short.expectancy)}">${g.short.n ? R2(g.short.expectancy) + ' · ' + g.short.n : '—'}</td></tr>`).join('')}
      </tbody></table></div>
      ${show && show.trades && show.trades.length ? `<details style="margin-top:10px"><summary class="note" style="cursor:pointer">Last ${show.trades.length} trades (${esc(show.exit_name)})</summary>
        <table class="tbl" style="margin-top:6px"><thead><tr><th>Time</th><th>Side</th><th>Result</th><th>Held</th><th>Exit</th></tr></thead><tbody>
        ${show.trades.slice().reverse().map((t) => `<tr><td class="faint">${esc((t.time || '').replace('T', ' ').slice(0, 16))}</td><td><span class="dir ${t.direction}">${t.direction.toUpperCase()}</span></td><td class="c-${tone(t.r)}">${R2(t.r)}</td><td>${t.bars}</td><td class="muted">${esc(t.exit)}</td></tr>`).join('')}
        </tbody></table></details>` : ''}`;
    if (window.lucide) lucide.createIcons();
  };
})();
