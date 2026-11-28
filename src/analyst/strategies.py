"""Your own saved strategies, run by Kora alongside (or instead of) its setups.

Template "ema_stoch" - the EMA cross + Stochastic-zone method for Boom/Crash:
  1. Direction: fast EMA (14) crosses the slow EMA (50) on the entry chart (M15 or M5).
  2. Zone: the 10 EMA drawn in the Stochastic window must have been in the buy
     zone (<= 20) for a long, or the sell zone (>= 80) for a short, shortly
     before the cross.
  3. Confirmation: the 2nd candle after the cross closes beyond both EMAs.
  4. Optional higher-timeframe filter: M30 / H1 fast EMA on the same side.
  5. "Missed it" rule: no entry if the confirmation is more than N candles old.

About the zone line: an MT5/TradingView EMA dropped into the Stochastic window
is an EMA of *price*, scaled to the price range visible on screen - so where
it sits against 20/80 depends on how far you're zoomed out. Kora reproduces it
reproducibly as the 10 EMA's position within its range over the last
`zone_lookback` candles (200 M15 candles is about the 2 days a phone chart
shows), so it means the same thing every time.

Every exit style below is backtested bar by bar (no lookahead, stop checked
before target inside a candle, extra slippage when a Boom/Crash spike hits
the stop), so you can see which one stops winners turning into losers.
"""
from __future__ import annotations

from datetime import datetime

from src.analyst import ta

TEMPLATES = {"ema_stoch": "EMA cross + Stochastic zones", "rules": "Rule builder"}

DEFAULT_PARAMS = {
    "entry_tf": "M15",       # M15 or M5
    "fast": 14, "slow": 50,  # EMAs on close
    "zone_ema": 10, "zone_lookback": 200, "buy_zone": 20, "sell_zone": 80,
    "zone_within": 12,       # zone must have been touched within this many candles before the cross
    "confirm_bars": 2,       # the Nth candle after the cross must close beyond both EMAs
    "max_late_bars": 1,      # skip if the confirmation is older than this (the "I missed it" rule)
    "htf": "none",           # none | M30 | H1 - trade only with the higher-timeframe EMAs (backtest both)
    "directions": "both",    # both | long | short | drift (Boom: sells, Crash: buys - with the drift between spikes)
    "stop": "swing",         # swing (beyond the last 10 candles' extreme) | atr
    "stop_atr": 1.5,
    "exit": "ema_trail",     # default exit for live plans - see EXITS
    "max_hold": 32,          # candles before a time exit (32 x M15 = 8h)
}

EXITS = {
    "fixed_1r": "Take profit at 1R",
    "fixed_2r": "Take profit at 2R",
    "zone": "Exit at the opposite zone (long out at 80, short out at 20)",
    "ema_trail": "Breakeven at 1R, then exit on a close back across the 14 EMA",
    "time_1r": "1R target or out after 8 candles",
    "scale": "Half off at 1R, rest trails the 14 EMA (2 stacked positions)",
}
HTF_VARIANTS = ["none", "M30", "H1"]
TF_MIN = {"M5": 5, "M15": 15, "M30": 30, "H1": 60}

DEFAULT_STRATEGY = {
    "id": "ema_stoch_bc",
    "name": "EMA cross + Stoch zones",
    "template": "ema_stoch",
    "instruments": ["BOOM1000", "CRASH1000"],
    "params": dict(DEFAULT_PARAMS),
    "notes": "Your Boom/Crash method as you trade it: EMA 14/50 cross, Stochastic(1,1,1) with a 10 EMA at 20/80, "
             "confirmation on the 2nd candle after the cross, both directions. Enter M5/M15, read the chart on M30/H1.",
}
DRIFT_STRATEGY = {
    "id": "ema_stoch_drift_m5",
    "name": "EMA/Stoch · drift only (M5)",
    "template": "ema_stoch",
    "instruments": [],
    "params": {**DEFAULT_PARAMS, "entry_tf": "M5", "directions": "drift", "exit": "scale"},
    "notes": "Kora's variant of your method from the first backtest: same entry rules on M5, but only with the drift "
             "(sell Boom, buy Crash), half off at 1R and the rest trailing the 14 EMA. Switch an instrument to it to compare.",
}


# --------------------------------------------------------------------------- settings helpers
def saved(settings: dict) -> list[dict]:
    out = settings.get("strategies")
    if out is None:
        out = [DEFAULT_STRATEGY, DRIFT_STRATEGY]
    res = []
    for s in out:
        if s.get("template", "ema_stoch") == "rules":
            from src.analyst import rules as R
            res.append({**s, "params": R.norm(s.get("params") or {})})
        else:
            res.append({**s, "template": "ema_stoch", "params": {**DEFAULT_PARAMS, **(s.get("params") or {})}})
    return res


def is_rules(strategy: dict) -> bool:
    return strategy.get("template") == "rules"


def bars_needed(strategy: dict) -> int:
    """Candles the live cycle should fetch for this strategy's entry timeframe."""
    if is_rules(strategy):
        return 600
    p = strategy["params"]
    return p["zone_lookback"] + p["slow"] + 150


def run_backtest(candles: list[dict], strategy: dict, meta: dict, cost: float = 0.0) -> dict:
    if is_rules(strategy):
        from src.analyst import rules as R
        return R.backtest(candles, strategy["params"], meta, cost)
    return backtest(candles, strategy["params"], meta, cost)


def by_id(settings: dict) -> dict[str, dict]:
    return {s["id"]: s for s in saved(settings)}


def mode_for(settings: dict, code: str) -> str:
    """kora | mine | both. Instruments a strategy is assigned to default to 'both'."""
    modes = settings.get("modes") or {}
    if code in modes:
        return modes[code]
    return "both" if active_for(settings, code) else "kora"


def active_for(settings: dict, code: str) -> dict | None:
    """The strategy switched on for an instrument (settings.active_strategy[code]),
    else the first saved strategy that lists the instrument."""
    strats = by_id(settings)
    sid = (settings.get("active_strategy") or {}).get(code)
    if sid:
        return strats.get(sid)
    if sid == "":
        return None
    return next((s for s in strats.values() if code in (s.get("instruments") or [])), None)


# --------------------------------------------------------------------------- indicators
def _ts(t) -> float:
    return datetime.fromisoformat(t).timestamp() if isinstance(t, str) else float(t)


def zone_series(closes: list[float], ema_period: int, lookback: int) -> list[float | None]:
    e = ta.ema(closes, ema_period)
    out: list[float | None] = []
    for i, v in enumerate(e):
        win = [x for x in e[max(0, i - lookback + 1): i + 1] if x is not None]
        if v is None or len(win) < min(lookback, 30):
            out.append(None)
            continue
        lo, hi = min(win), max(win)
        out.append(50.0 if hi == lo else (v - lo) / (hi - lo) * 100)
    return out


def htf_side(candles: list[dict], minutes: int, tf_min: int, fast: int, slow: int) -> list[int]:
    """Per entry candle: +1 / -1 / 0 = fast EMA above / below slow EMA on the last
    *completed* higher-timeframe candle (built from the entry candles - causal)."""
    if minutes <= tf_min:
        return [0] * len(candles)
    span = minutes * 60
    closes, ends = [], []
    side_at = []
    bucket, last_close = None, None
    for c in candles:
        b = int(_ts(c["time"]) // span)
        if bucket is not None and b != bucket:
            closes.append(last_close)
            ends.append(bucket)
        bucket, last_close = b, c["close"]
        side_at.append(len(closes))  # number of completed HTF candles before this entry candle
    ef, es = ta.ema(closes, fast), ta.ema(closes, slow)
    out = []
    for k in side_at:
        j = k - 1
        if j < 0 or ef[j] is None or es[j] is None:
            out.append(0)
        else:
            out.append(1 if ef[j] > es[j] else -1 if ef[j] < es[j] else 0)
    return out


class Ind:
    def __init__(self, candles: list[dict], p: dict):
        self.c = candles
        self.close = [x["close"] for x in candles]
        self.fast = ta.ema(self.close, p["fast"])
        self.slow = ta.ema(self.close, p["slow"])
        self.zone = zone_series(self.close, p["zone_ema"], p["zone_lookback"])
        self.atr = ta.atr(candles, 14)
        tfm = TF_MIN.get(p["entry_tf"], 15)
        self.htf = {v: htf_side(candles, TF_MIN[v], tfm, p["fast"], p["slow"]) for v in ("M30", "H1")}


# --------------------------------------------------------------------------- signals
def signal_at(ind: Ind, i: int, p: dict, htf: str | None = None) -> dict | None:
    """A confirmed entry whose confirmation candle is bar i - (0..max_late_bars),
    evaluated with data up to and including bar i only."""
    htf = p["htf"] if htf is None else htf
    cb = p["confirm_bars"]
    for late in range(0, p["max_late_bars"] + 1):
        k = i - late                     # confirmation candle
        x = k - cb                       # cross candle
        if x < 1 or ind.slow[x - 1] is None or ind.atr[i] is None:
            continue
        up = ind.fast[x - 1] <= ind.slow[x - 1] and ind.fast[x] > ind.slow[x]
        dn = ind.fast[x - 1] >= ind.slow[x - 1] and ind.fast[x] < ind.slow[x]
        if not (up or dn):
            continue
        sign = 1 if up else -1
        direction = "long" if up else "short"
        if p["directions"] in ("long", "short") and p["directions"] != direction:
            continue
        # still on the right side of the cross through confirmation, and confirmation closes beyond both EMAs
        if any(sign * (ind.fast[j] - ind.slow[j]) <= 0 for j in range(x, i + 1)):
            continue
        cc = ind.close[k]
        if (sign > 0 and cc <= max(ind.fast[k], ind.slow[k])) or (sign < 0 and cc >= min(ind.fast[k], ind.slow[k])):
            continue
        zw = [z for z in ind.zone[max(0, x - p["zone_within"]): x + 1] if z is not None]
        hit_zone = bool(zw) and (min(zw) <= p["buy_zone"] if up else max(zw) >= p["sell_zone"])
        if not hit_zone:
            continue
        if htf != "none" and ind.htf[htf][i] != sign:
            continue
        entry = ind.close[i]
        a = ind.atr[i]
        if p["stop"] == "atr":
            stop = entry - sign * p["stop_atr"] * a
        else:
            lows = [ind.c[j]["low" if up else "high"] for j in range(max(0, i - 10), i + 1)]
            stop = (min(lows) if up else max(lows)) - sign * 0.1 * a
        risk = abs(entry - stop)
        if risk < 0.6 * a:
            risk = 0.6 * a
            stop = entry - sign * risk
        if risk > 4 * a:
            continue
        return {"i": i, "cross": x, "confirm": k, "late": late, "direction": direction, "sign": sign,
                "entry": entry, "stop": stop, "risk": risk, "zone_min": round(min(zw), 1), "zone_max": round(max(zw), 1)}
    return None


# --------------------------------------------------------------------------- exits
def simulate(ind: Ind, s: dict, exit_style: str, p: dict, spike: str | None) -> tuple[float, int, str] | None:
    """R multiple, candles held, reason. Entry at the signal candle's close."""
    i, sign, entry, risk = s["i"], s["sign"], s["entry"], s["risk"]
    stop = s["stop"]
    n = len(ind.c)
    tp = {"fixed_1r": entry + sign * risk, "fixed_2r": entry + sign * 2 * risk, "time_1r": entry + sign * risk,
          "scale": entry + sign * risk, "ema_trail": None, "zone": None}[exit_style]
    hold = 8 if exit_style == "time_1r" else p["max_hold"]
    banked, trailing = 0.0, False
    for j in range(i + 1, min(n, i + 1 + hold)):
        c = ind.c[j]
        adverse, favour = (c["low"], c["high"]) if sign > 0 else (c["high"], c["low"])
        if sign * (adverse - stop) <= 0:     # stop first (conservative)
            fill = stop
            spike_hit = (spike == "down" and sign > 0) or (spike == "up" and sign < 0)
            if spike_hit and not trailing:   # Boom/Crash spike through the stop: assume half the spike as slippage
                fill = stop - sign * abs(adverse - stop) * 0.5
            if sign * (c["open"] - stop) <= 0:
                fill = c["open"]
            r = sign * (fill - entry) / risk
            r = banked + (0.5 * r if exit_style == "scale" and banked else r)
            return round(r, 3), j - i, "stopped at breakeven" if trailing and abs(stop - entry) < 1e-12 else "stop"
        if tp is not None and sign * (favour - tp) >= 0:
            if exit_style == "scale" and not banked:
                banked, tp, trailing, stop = 0.5, None, True, entry   # half off at 1R, rest to breakeven + trail
            else:
                return round(sign * (tp - entry) / risk, 3), j - i, "target"
        if exit_style == "ema_trail" and not trailing and sign * (favour - (entry + sign * risk)) >= 0:
            trailing, stop = True, entry
        if exit_style in ("ema_trail", "scale") and trailing and ind.fast[j] is not None and sign * (c["close"] - ind.fast[j]) < 0:
            r = sign * (c["close"] - entry) / risk
            return round(banked + (0.5 * r if exit_style == "scale" else r), 3), j - i, "closed back across the 14 EMA"
        if exit_style == "zone" and ind.zone[j] is not None and (
                (sign > 0 and ind.zone[j] >= p["sell_zone"]) or (sign < 0 and ind.zone[j] <= p["buy_zone"])):
            return round(sign * (c["close"] - entry) / risk, 3), j - i, "reached the opposite zone"
    last = min(n - 1, i + hold)
    if last <= i:
        return None
    if last < i + hold and exit_style not in ("ema_trail", "zone", "scale"):
        return None  # not enough data yet to know
    r = sign * (ind.c[last]["close"] - entry) / risk
    return round(banked + (0.5 * r if exit_style == "scale" and banked else r), 3), last - i, "time exit"


def spike_side(meta: dict) -> str | None:
    code = (meta.get("code") or "").upper()
    return "down" if code.startswith("CRASH") else "up" if code.startswith("BOOM") else None


def _stats(rs: list[float]) -> dict:
    if not rs:
        return {"n": 0, "wins": 0, "win_rate": None, "expectancy": None, "total_r": 0, "max_dd_r": 0, "max_losing_streak": 0}
    eq = peak = dd = 0.0
    streak = worst = 0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
        streak = streak + 1 if r <= 0 else 0
        worst = max(worst, streak)
    wins = sum(1 for r in rs if r > 0)
    return {"n": len(rs), "wins": wins, "win_rate": round(wins / len(rs) * 100, 1), "expectancy": round(sum(rs) / len(rs), 3),
            "total_r": round(sum(rs), 2), "max_dd_r": round(dd, 2), "max_losing_streak": worst}


def resolve(params: dict, meta: dict) -> dict:
    p = {**DEFAULT_PARAMS, **params}
    if p["directions"] == "drift":
        sp = spike_side(meta)
        p["directions"] = "long" if sp == "down" else "short" if sp == "up" else "both"
    return p


def backtest(candles: list[dict], params: dict, meta: dict, cost: float = 0.0) -> dict:
    """Every exit style x higher-timeframe filter, per direction. No overlapping
    trades within one variant (a new signal is ignored while one is open)."""
    p = resolve(params, meta)
    ind = Ind(candles, p)
    spike = spike_side(meta)
    warm = p["slow"] + p["zone_lookback"] // 2
    grid = []
    for htf in HTF_VARIANTS:
        sigs = []
        last_cross = None
        for i in range(warm, len(candles) - 1):
            s = signal_at(ind, i, {**p, "max_late_bars": 0}, htf)
            if s and s["cross"] != last_cross:
                sigs.append(s)
                last_cross = s["cross"]
        for ex in EXITS:
            busy_until = -1
            res = {"long": [], "short": []}
            trades = []
            for s in sigs:
                if s["i"] <= busy_until:
                    continue
                out = simulate(ind, s, ex, p, spike)
                if out is None:
                    continue
                r, bars, why = out
                r = round(r - (cost / s["risk"] if s["risk"] else 0), 3)
                busy_until = s["i"] + bars
                res[s["direction"]].append(r)
                trades.append({"time": candles[s["i"]]["time"], "direction": s["direction"], "r": r, "bars": bars, "exit": why})
            both = [t["r"] for t in trades]
            from src.analyst.rules import _curve
            grid.append({"htf": htf, "exit": ex, "exit_name": EXITS[ex], "all": _stats(both), "curve": _curve(both),
                         "long": _stats(res["long"]), "short": _stats(res["short"]), "trades": trades[-40:]})
    ranked = [g for g in grid if g["all"]["n"] >= 8]
    best = max(ranked, key=lambda g: g["all"]["expectancy"], default=None)
    t0, t1 = (candles[0]["time"], candles[-1]["time"]) if candles else (None, None)
    return {"code": meta.get("code"), "tf": p["entry_tf"], "bars": len(candles), "from": t0, "to": t1, "cost": cost,
            "grid": grid, "best": {k: best[k] for k in ("htf", "exit", "exit_name", "all")} if best else None,
            "ran_at": datetime.now().astimezone().isoformat()}


def stats_rows(bt: dict, strategy: dict) -> list[dict]:
    """Rows for setup_stats so live plans get calibrated confidence: uses the
    variant matching the strategy's own HTF filter with the 1R exit, which is
    what live TP1 / paper trading measure."""
    p = strategy["params"]
    if is_rules(strategy):
        g = next((g for g in bt["grid"] if g["exit"] == p["exit"]), None)
    else:
        g = next((g for g in bt["grid"] if g["htf"] == p["htf"] and g["exit"] == "fixed_1r"), None)
    rows = []
    for d in ("long", "short"):
        st = (g or {}).get(d) or {}
        if st.get("n"):
            rows.append({"setup": f"my:{strategy['id']}", "direction": d, "n": st["n"], "wins": st["wins"],
                         "avg_r": st["expectancy"], "expectancy": st["expectancy"]})
    return rows


# --------------------------------------------------------------------------- history
def history(meta: dict, tf: str, bars: int, source_name: str | None = None) -> list[dict]:
    """Long candle history for backtests: MT5 by date range, Deriv paged back
    2,000 candles at a time, else whatever the source gives in one call."""
    from datetime import timedelta, timezone
    from src.live.sources import fetch_candles_sync, resolve_source_name
    name = source_name or resolve_source_name()
    if name == "mt5":
        from src.ingest.mt5_local import candle_history
        # Boom/Crash/V75 trade 24/7, forex/metals ~5 days a week: ask for enough calendar days either way
        return candle_history(meta["symbol"], tf, max(2, int(bars * TF_MIN.get(tf, 15) / 1440 * 1.45) + 1))[-bars:]
    if name != "deriv":
        return fetch_candles_sync(meta, tf, bars, name)
    import asyncio
    from src.live.sources import DerivSource

    async def paged():
        src = DerivSource([meta])
        src.instruments = []
        await src._connect()
        out: list[dict] = []
        end = None
        try:
            while len(out) < bars:
                chunk = await src.candles(meta["symbol"], tf, 2000, end)
                chunk = [c for c in chunk if not out or c["time"] < out[0]["time"]]
                if not chunk:
                    break
                out = chunk + out
                end = datetime.fromisoformat(chunk[0]["time"]).astimezone(timezone.utc) - timedelta(seconds=1)
        finally:
            await src.close()
        return out[-bars:]
    return asyncio.run(paged())


# --------------------------------------------------------------------------- live
def _rules_state(candles: list[dict], strategy: dict, meta: dict | None = None) -> dict:
    from src.analyst import rules as R
    p = R.norm(strategy["params"])
    ctx = R.Ctx(candles)
    if ctx.n < 60:
        return {"ok": False, "text": "Not enough candles yet"}
    dirs = R.allowed_dirs(p, meta or {})
    last = None
    for i in range(ctx.n - 2, max(1, ctx.n - 300), -1):
        s = R.signal_at(ctx, i, p, dirs)
        if s:
            last = (ctx.n - 2 - i, s["direction"])
            break
    if last is None:
        return {"ok": True, "text": "No signal in the last 300 candles: waiting for the rules to line up"}
    ago, d = last
    return {"ok": True, "cross_ago": ago, "text": ("Signal on the last closed candle: " + d) if ago == 0 else
            f"Last signal {ago} candle{'s' if ago != 1 else ''} ago ({d}); waiting for the next one"}


def live_state(candles: list[dict], strategy: dict, meta: dict | None = None) -> dict:
    if is_rules(strategy):
        return _rules_state(candles, strategy, meta)
    p = {**DEFAULT_PARAMS, **strategy["params"]}
    ind = Ind(candles, p)
    i = len(candles) - 1
    if i < p["slow"] + 5 or ind.slow[i] is None:
        return {"ok": False, "text": "Not enough candles yet"}
    side = 1 if ind.fast[i] > ind.slow[i] else -1
    x = next((j for j in range(i, 0, -1) if ind.slow[j - 1] is not None and
              (ind.fast[j] - ind.slow[j]) * (ind.fast[j - 1] - ind.slow[j - 1]) <= 0), None)
    z = ind.zone[i]
    htf = ind.htf.get(p["htf"], [0])[i] if p["htf"] != "none" else None
    ago = i - x if x is not None else None
    text = (f"{p['fast']} EMA {'above' if side > 0 else 'below'} {p['slow']} EMA"
            + (f" since {ago} candle{'s' if ago != 1 else ''} ago" if ago is not None else "")
            + (f" · zone {z:.0f}" if z is not None else "")
            + (f" · {p['htf']} {'up' if htf > 0 else 'down' if htf < 0 else 'flat'}" if htf is not None else ""))
    if ago is not None and ago > p["confirm_bars"] + p["max_late_bars"]:
        text += " · move already running: wait for the next cross"
    return {"ok": True, "side": side, "cross_ago": ago, "zone": None if z is None else round(z, 1), "htf": htf, "text": text}


def live_candidates(candles: list[dict], strategy: dict, meta: dict, stats: dict) -> list[dict]:
    from src.analyst.calibration import calibrate_confidence
    if is_rules(strategy):
        return _rules_candidates(candles, strategy, meta, stats)
    p = resolve(strategy["params"], meta)
    if len(candles) < p["slow"] + 30:
        return []
    ind = Ind(candles, p)
    i = len(candles) - 1
    # the last candle in a live feed is still forming: evaluate on the last closed candle
    s = signal_at(ind, i - 1, p)
    if not s:
        return []
    sign, entry, risk = s["sign"], s["entry"], s["risk"]
    setup = f"my:{strategy['id']}"
    a = ind.atr[s["i"]] or risk
    score = 60 + (10 if s["late"] == 0 else 0) + (5 if p["htf"] != "none" else 0)
    st = stats.get((meta["code"], setup, s["direction"]))
    cal = calibrate_confidence(score, 1.0, st)
    zone_txt = (f"zone dipped to {s['zone_min']:.0f} (buy zone ≤{p['buy_zone']})" if sign > 0
                else f"zone reached {s['zone_max']:.0f} (sell zone ≥{p['sell_zone']})")
    against = []
    sp = spike_side(meta)
    if (sp == "down" and sign > 0) or (sp == "up" and sign < 0):
        against.append(f"Trading against the {'drop' if sp == 'down' else 'spike'} direction: a spike can jump the stop")
    if s["late"]:
        against.append(f"Confirmation was {s['late']} candle ago: entry is later than ideal")
    tp1, tp2 = entry + sign * risk, entry + sign * 2 * risk
    return [{
        "setup": setup, "setup_name": f"{strategy['name']} (yours)", "source": "mine", "strategy_id": strategy["id"],
        "direction": s["direction"], "entry_type": "market", "entry": entry, "stop": s["stop"], "tp1": tp1, "tp2": tp2,
        "risk_atr": round(risk / a, 2), "rr1": 1.0, "rr2": 2.0, "score": score,
        "confluence": [f"{p['fast']}/{p['slow']} EMA cross {'up' if sign > 0 else 'down'} on {p['entry_tf']}",
                       zone_txt, f"Candle {p['confirm_bars']} after the cross closed {'above' if sign > 0 else 'below'} both EMAs"]
                      + ([f"{p['htf']} EMAs agree"] if p["htf"] != "none" else []),
        "against": against,
        "trigger": "Market entry on the confirmation close",
        "invalidation": f"A close back {'below' if sign > 0 else 'above'} the {p['slow']} EMA, or the stop",
        "exit_rule": EXITS.get(p["exit"], p["exit"]),
        "calibration": cal, "confidence": cal["confidence"],
        "rule_params": to_rules(strategy), "signal_time": candles[s["i"]]["time"], "timeframe": p["entry_tf"],
    }]


def bars_for_backtest(tf: str) -> int:
    return {"M5": 20000, "M15": 20000}.get(tf, 12000)


def compact(bt: dict) -> dict:
    """Backtest result trimmed for storage (kv) - trades only for the best variant."""
    best = bt.get("best") or {}
    grid = [{**g, "trades": g["trades"] if (g["htf"], g["exit"]) == (best.get("htf"), best.get("exit")) else []} for g in bt["grid"]]
    return {**bt, "grid": grid}


def _lc(t: str) -> str:
    return t[:1].lower() + t[1:]


def summary(bt: dict, strategy: dict) -> str:
    """Plain-English read of a backtest."""
    if not bt or not bt.get("grid"):
        return "No backtest yet."
    p = strategy["params"]
    mine = next((g for g in bt["grid"] if g["htf"] == p.get("htf", "none") and g["exit"] == p["exit"]), None)
    best = bt.get("best")
    parts = []
    if mine and mine["all"]["n"]:
        a = mine["all"]
        parts.append(f"As set up ({_lc(mine['exit_name'])}): {a['n']} trades, {a['win_rate']:.0f}% winners, "
                     f"{a['expectancy']:+.2f}R per trade, worst run {a['max_losing_streak']} losses in a row.")
        for d in ("long", "short"):
            st = mine[d]
            if st["n"] >= 5:
                parts.append(f"{'Buys' if d == 'long' else 'Sells'}: {st['expectancy']:+.2f}R over {st['n']}.")
    elif mine:
        parts.append("Your exact settings produced no trades in this history.")
    if best and (not mine or (best["htf"], best["exit"]) != (mine["htf"], mine["exit"])):
        parts.append(f"Best variant: {_lc(best['exit_name'])}" + (f" with the {best['htf']} filter" if best["htf"] != "none" else "")
                     + f" ({best['all']['expectancy']:+.2f}R over {best['all']['n']}).")
    n = (mine or {}).get("all", {}).get("n", 0)
    if n and n < 30:
        parts.append("Small sample: treat as a hint, not proof.")
    return " ".join(parts)


def _rules_candidates(candles: list[dict], strategy: dict, meta: dict, stats: dict) -> list[dict]:
    from src.analyst import rules as R
    from src.analyst.calibration import calibrate_confidence
    p = R.norm(strategy["params"])
    s = R.live_signal(candles, p, meta)
    if not s:
        return []
    sign, entry, risk = s["sign"], s["entry"], s["risk"]
    tp_r = float(p.get("tp_r", 1.5)) if p["exit"] == "fixed_r" else 1.0
    setup = f"my:{strategy['id']}"
    st = stats.get((meta["code"], setup, s["direction"]))
    cal = calibrate_confidence(65, tp_r, st)
    sp = R.spike_side(meta)
    against = []
    if (sp == "down" and sign > 0) or (sp == "up" and sign < 0):
        against.append(f"Against the {'drop' if sp == 'down' else 'spike'} direction: a spike can jump the stop")
    return [{
        "setup": setup, "setup_name": f"{strategy['name']} (yours)", "source": "mine", "strategy_id": strategy["id"],
        "direction": s["direction"], "entry_type": "market", "entry": entry, "stop": s["stop"],
        "tp1": entry + sign * risk * tp_r, "tp2": entry + sign * risk * max(2.0, tp_r * 1.6),
        "risk_atr": None, "rr1": round(tp_r, 2), "rr2": round(max(2.0, tp_r * 1.6), 2), "score": 65,
        "confluence": [R.describe(x) for x in (p["long"] if sign > 0 else p["short"])][:4],
        "against": against, "trigger": "Market entry on the signal candle's close",
        "invalidation": "The stop, or your exit rule", "exit_rule": R.exit_name(p["exit"], p),
        "calibration": cal, "confidence": cal["confidence"],
        "rule_params": p, "signal_time": candles[s["i"]]["time"], "timeframe": p["entry_tf"],
    }]


def to_rules(strategy: dict) -> dict:
    """Your EMA/Stoch strategy expressed in the rule builder's format (same
    entry logic, used by research and editable in the builder)."""
    if is_rules(strategy):
        return dict(strategy["params"])
    p = {**DEFAULT_PARAMS, **strategy["params"]}
    cb = int(p["confirm_bars"])
    f = {"ind": "ema", "params": {"period": p["fast"]}}
    sl = {"ind": "ema", "params": {"period": p["slow"]}}
    zone = {"ind": "zone", "params": {"ema": p["zone_ema"], "lookback": p["zone_lookback"]}, "shift": cb}
    long = [{"a": {**f, "shift": cb}, "op": "crosses_above", "b": {**sl, "shift": cb}},
            *[{"a": {**f, "shift": k} if k else f, "op": ">", "b": {**sl, "shift": k} if k else sl} for k in range(cb)],
            {"a": {"price": "close"}, "op": ">", "b": f}, {"a": {"price": "close"}, "op": ">", "b": sl},
            {"a": zone, "op": "<=", "b": {"value": p["buy_zone"]}, "within": int(p["zone_within"]) + 1}]
    ex = {"ema_trail": "ema_trail", "scale": "scale", "fixed_1r": "fixed_1r", "fixed_2r": "fixed_2r",
          "time_1r": "time_1r", "zone": "cond"}.get(p["exit"], "fixed_1r")
    out = {"entry_tf": p["entry_tf"], "long": long, "mirror": True, "directions": p["directions"],
           "stop": {"type": "swing" if p["stop"] == "swing" else "atr", "bars": 10, "mult": p["stop_atr"], "max_atr": 4.0},
           "exit": ex, "trail": p["fast"], "max_hold": p["max_hold"]}
    if ex == "cond":
        out["exit_long"] = [{"a": {"ind": "zone", "params": {"ema": p["zone_ema"], "lookback": p["zone_lookback"]}},
                             "op": ">=", "b": {"value": p["sell_zone"]}}]
    return out
