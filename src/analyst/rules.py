"""Rule-based strategy engine: the 'Build' half of the Strategy Lab.

A strategy is plain JSON, so it can be built in the UI, saved, duplicated,
tested, paper-traded and used live by Kora:

  params = {
    "entry_tf": "M15",
    "long":  [cond, ...],          # all must be true on a closed candle
    "short": [cond, ...],          # or "mirror": true to flip the long rules
    "exit_long": [cond, ...], "exit_short": [...],   # for exit = "cond"
    "stop": {"type": "atr" | "swing" | "pct", "mult": 1.5, "bars": 10, "pct": 0.5},
    "exit": "fixed_r" | "fixed_1r" | "fixed_2r" | "ema_trail" | "scale" | "atr_trail" | "opposite" | "cond" | "time_1r",
    "tp_r": 1.5, "trail": 14, "trail_atr": 3.0, "max_hold": 48,
    "session": [7, 17] | null,     # UTC hours when new entries are allowed
    "directions": "both" | "long" | "short" | "drift",
  }
  cond    = {"a": operand, "op": ">" | "<" | ">=" | "<=" | "crosses_above" | "crosses_below" | "rising" | "falling",
             "b": operand, "within": 1}
  operand = {"ind": "ema", "params": {"period": 50}, "out": "value", "shift": 0}
          | {"price": "close" | "open" | "high" | "low"} | {"value": 30}

Backtests are bar by bar with no lookahead, entry at the signal candle's close,
stop checked before target inside a candle, the spread charged on every
trade, and extra slippage when a Boom/Crash spike runs through the stop.
"""
from __future__ import annotations

from src.analyst import indicators as IND
from src.analyst import ta

EXITS = {
    "fixed_r": "Fixed target at your R multiple",
    "fixed_1r": "Take profit at 1R",
    "fixed_2r": "Take profit at 2R",
    "ema_trail": "Breakeven at 1R, then exit on a close back across the trail EMA",
    "scale": "Half off at 1R, rest trails the EMA (2 stacked positions)",
    "atr_trail": "Chandelier stop: trails 3 ATR behind the best close",
    "opposite": "Hold until the opposite signal",
    "cond": "Exit when your exit rules trigger",
    "time_1r": "1R target or out after 8 candles",
}
OPS = [">", "<", ">=", "<=", "crosses_above", "crosses_below", "rising", "falling"]
TF_MIN = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}

DEFAULT_PARAMS = {
    "entry_tf": "M15", "long": [], "short": [], "mirror": False, "exit_long": [], "exit_short": [],
    "stop": {"type": "atr", "mult": 1.5, "bars": 10, "pct": 0.5, "max_atr": 4.0},
    "exit": "fixed_r", "tp_r": 1.5, "trail": 14, "trail_atr": 3.0, "max_hold": 48,
    "session": None, "directions": "both",
}

# oscillators whose 'mirror' value for shorts is not just the same number
_MIRROR = {"rsi": lambda v: 100 - v, "stoch": lambda v: 100 - v, "stochrsi": lambda v: 100 - v, "zone": lambda v: 100 - v,
           "willr": lambda v: -100 - v, "cci": lambda v: -v, "roc": lambda v: -v, "macd": lambda v: -v,
           "bb": lambda v: 1 - v if 0 <= v <= 1 else v}
_FLIP = {">": "<", "<": ">", ">=": "<=", "<=": ">=", "crosses_above": "crosses_below", "crosses_below": "crosses_above",
         "rising": "falling", "falling": "rising"}


def norm(params: dict) -> dict:
    p = {**DEFAULT_PARAMS, **(params or {})}
    p["stop"] = {**DEFAULT_PARAMS["stop"], **(p.get("stop") or {})}
    if p.get("mirror"):
        p["short"] = [mirror(c) for c in p["long"]]
        p["exit_short"] = [mirror(c) for c in p["exit_long"]]
    return p


_NONDIR = {("adx", "adx"), ("atr", "value"), ("bb", "width"), ("hour", "value"), ("dow", "value"),
           ("candle", "body"), ("candle", "range"), ("candle", "body_pct")}


def _nondir(op: dict) -> bool:
    if "ind" not in op:
        return False
    out = op.get("out") or IND.REGISTRY.get(op["ind"], {}).get("outputs", ["value"])[0]
    return (op["ind"], out) in _NONDIR


def _swap_price(op: dict) -> dict:
    if op.get("price") in ("low", "high"):
        return {**op, "price": "high" if op["price"] == "low" else "low"}
    if op.get("ind") == "donchian" and op.get("out") in ("upper", "lower"):
        return {**op, "out": "lower" if op["out"] == "upper" else "upper"}
    if op.get("ind") in ("bb", "keltner") and op.get("out") in ("upper", "lower"):
        return {**op, "out": "lower" if op["out"] == "upper" else "upper"}
    if op.get("ind") == "adx" and op.get("out") in ("plus", "minus"):
        return op  # +DI > -DI flips by the operator
    if op.get("ind") in ("highest", "lowest"):
        return {**op, "ind": "lowest" if op["ind"] == "highest" else "highest"}
    return op


def mirror(cond: dict) -> dict:
    """Long rule -> the equivalent short rule (crosses above -> below, RSI 30 -> 70,
    low -> high, upper band -> lower band). Non-directional measures (ADX, ATR,
    band width, time) are left as they are."""
    a, b = cond.get("a") or {}, cond.get("b") or {}
    if (_nondir(a) and ("value" in b or _nondir(b))) or (_nondir(b) and "value" in a):
        return dict(cond)
    a, b = _swap_price(a), _swap_price(b)
    ref = a.get("ind") or b.get("ind")
    out = (a.get("out") if a.get("ind") else b.get("out")) or "value"

    def flip_val(op):
        if "value" in op and ref in _MIRROR and not (ref == "bb" and out != "pctb"):
            return {"value": round(_MIRROR[ref](float(op["value"])), 6)}
        return op
    rising_like = cond.get("op") in ("rising", "falling")
    return {**cond, "op": _FLIP.get(cond.get("op"), cond.get("op")), "a": flip_val(a),
            "b": b if rising_like else flip_val(b)}


def describe_operand(op: dict) -> str:
    if not op:
        return "?"
    if "value" in op:
        return f"{op['value']:g}" if isinstance(op["value"], (int, float)) else str(op["value"])
    if "price" in op:
        s = op["price"]
    else:
        spec = IND.REGISTRY.get(op.get("ind"), {})
        params = {**spec.get("params", {}), **(op.get("params") or {})}
        ps = ",".join(f"{v:g}" if isinstance(v, (int, float)) else str(v) for v in params.values())
        s = f"{spec.get('name', op.get('ind'))}{'(' + ps + ')' if ps else ''}"
        if len(spec.get("outputs", [])) > 1:
            s += f" {op.get('out', spec['outputs'][0])}"
    if op.get("shift"):
        s += f" [{op['shift']} ago]"
    return s


def describe(cond: dict) -> str:
    op = cond.get("op")
    words = {"crosses_above": "crosses above", "crosses_below": "crosses below", "rising": "rising over", "falling": "falling over"}
    b = describe_operand(cond.get("b"))
    if op in ("rising", "falling"):
        b = f"{b} candles"
    s = f"{describe_operand(cond.get('a'))} {words.get(op, op)} {b}"
    if (cond.get("within") or 1) > 1:
        s += f" (within {cond['within']} candles)"
    return s


class Ctx:
    """Indicator cache over one candle series."""

    def __init__(self, candles: list[dict]):
        self.c = candles
        self.n = len(candles)
        self.cache: dict = {}
        self.atr = ta.atr(candles, 14)

    def series(self, op: dict) -> list | None:
        if "price" in op:
            key = ("price", op["price"])
            if key not in self.cache:
                self.cache[key] = [x[op["price"]] for x in self.c]
            return self.cache[key]
        if "ind" in op:
            ind = op["ind"]
            if ind not in IND.REGISTRY:
                return None
            params = op.get("params") or {}
            key = (ind, tuple(sorted(params.items())))
            if key not in self.cache:
                self.cache[key] = IND.compute(ind, self.c, params)
            outs = self.cache[key]
            return outs.get(op.get("out") or IND.REGISTRY[ind]["outputs"][0])
        return None

    def val(self, op: dict, i: int):
        if "value" in op:
            return float(op["value"])
        s = self.series(op)
        j = i - int(op.get("shift") or 0)
        if s is None or j < 0 or j >= self.n:
            return None
        return s[j]

    def cond_at(self, cond: dict, i: int) -> bool:
        a, b, op = cond.get("a") or {}, cond.get("b") or {}, cond.get("op")
        if op in ("rising", "falling"):
            n = int(float(b.get("value", 1)))
            x, y = self.val(a, i), self.val(a, i - n)
            return x is not None and y is not None and (x > y if op == "rising" else x < y)
        x, y = self.val(a, i), self.val(b, i)
        if x is None or y is None:
            return False
        if op == ">":
            return x > y
        if op == "<":
            return x < y
        if op == ">=":
            return x >= y
        if op == "<=":
            return x <= y
        px, py = self.val(a, i - 1), self.val(b, i - 1)
        if px is None or py is None:
            return False
        if op == "crosses_above":
            return px <= py and x > y
        if op == "crosses_below":
            return px >= py and x < y
        return False

    def all_true(self, conds: list[dict], i: int) -> bool:
        if not conds:
            return False
        for c in conds:
            w = max(1, int(c.get("within") or 1))
            if not any(self.cond_at(c, j) for j in range(max(0, i - w + 1), i + 1)):
                return False
        return True


def _hour(t: str) -> float:
    return int(t[11:13]) + int(t[14:16]) / 60


def spike_side(meta: dict) -> str | None:
    code = (meta.get("code") or "").upper()
    return "down" if code.startswith("CRASH") else "up" if code.startswith("BOOM") else None


def allowed_dirs(p: dict, meta: dict) -> set:
    d = p.get("directions", "both")
    if d == "drift":
        sp = spike_side(meta)
        return {"long"} if sp == "down" else {"short"} if sp == "up" else {"long", "short"}
    return {"long", "short"} if d == "both" else {d}


def signal_at(ctx: Ctx, i: int, p: dict, dirs: set) -> dict | None:
    if i < 1 or ctx.atr[i] is None:
        return None
    if p.get("session"):
        lo, hi = p["session"]
        h = _hour(ctx.c[i]["time"])
        if not (lo <= h < hi if lo <= hi else (h >= lo or h < hi)):
            return None
    for direction, conds in (("long", p["long"]), ("short", p["short"])):
        if direction not in dirs or not ctx.all_true(conds, i):
            continue
        sign = 1 if direction == "long" else -1
        entry, a = ctx.c[i]["close"], ctx.atr[i]
        st = p["stop"]
        if st["type"] == "swing":
            w = ctx.c[max(0, i - int(st.get("bars", 10))):i + 1]
            stop = (min(x["low"] for x in w) - 0.1 * a) if sign > 0 else (max(x["high"] for x in w) + 0.1 * a)
        elif st["type"] == "pct":
            stop = entry * (1 - sign * float(st.get("pct", 0.5)) / 100)
        else:
            stop = entry - sign * float(st.get("mult", 1.5)) * a
        risk = abs(entry - stop)
        if risk < 0.5 * a:
            risk = 0.5 * a
            stop = entry - sign * risk
        if risk > float(st.get("max_atr", 4.0)) * a:   # stop too far away: skip rather than oversize the risk
            continue
        return {"i": i, "direction": direction, "sign": sign, "entry": entry, "stop": stop, "risk": risk}
    return None


def simulate(ctx: Ctx, s: dict, exit_style: str, p: dict, meta: dict, cost: float = 0.0) -> tuple[float, int, str] | None:
    """R multiple after costs, candles held, exit reason."""
    i, sign, entry, risk = s["i"], s["sign"], s["entry"], s["risk"]
    stop = s["stop"]
    n = ctx.n
    tp_r = {"fixed_r": float(p.get("tp_r", 1.5)), "fixed_1r": 1.0, "fixed_2r": 2.0, "time_1r": 1.0, "scale": 1.0}.get(exit_style)
    tp = entry + sign * tp_r * risk if tp_r else None
    hold = 8 if exit_style == "time_1r" else int(p.get("max_hold", 48))
    if exit_style in ("opposite", "cond", "atr_trail"):
        hold = max(hold, 400)
    trail_ema = ctx.series({"ind": "ema", "params": {"period": int(p.get("trail", 14))}}) if exit_style in ("ema_trail", "scale") else None
    spike = spike_side(meta)
    dirs = {s["direction"]}
    exit_conds = p["exit_long"] if sign > 0 else p["exit_short"]
    banked, trailing, best = 0.0, False, entry
    cr = cost / risk if risk else 0.0

    def done(r, j, why):
        return round(r - cr, 3), j - i, why

    for j in range(i + 1, min(n, i + 1 + hold)):
        c = ctx.c[j]
        adverse, favour = (c["low"], c["high"]) if sign > 0 else (c["high"], c["low"])
        if sign * (adverse - stop) <= 0:
            fill = stop
            if ((spike == "down" and sign > 0) or (spike == "up" and sign < 0)) and not trailing:
                fill = stop - sign * abs(adverse - stop) * 0.5
            if sign * (c["open"] - stop) <= 0:
                fill = c["open"]
            r = sign * (fill - entry) / risk
            return done(banked + (0.5 * r if banked else r), j, "stopped at breakeven" if trailing and abs(stop - entry) < 1e-12 else "stop")
        if tp is not None and sign * (favour - tp) >= 0:
            if exit_style == "scale" and not banked:
                banked, tp, trailing, stop = 0.5 * tp_r, None, True, entry
            else:
                return done(sign * (tp - entry) / risk, j, "target")
        if exit_style == "ema_trail" and not trailing and sign * (favour - (entry + sign * risk)) >= 0:
            trailing, stop = True, entry
        if trail_ema is not None and trailing and trail_ema[j] is not None and sign * (c["close"] - trail_ema[j]) < 0:
            r = sign * (c["close"] - entry) / risk
            return done(banked + (0.5 * r if banked else r), j, "closed back across the trail EMA")
        if exit_style == "atr_trail" and ctx.atr[j]:
            best = max(best, c["close"]) if sign > 0 else min(best, c["close"])
            new = best - sign * float(p.get("trail_atr", 3.0)) * ctx.atr[j]
            if sign * (new - stop) > 0:
                stop, trailing = new, True
        if exit_style == "opposite":
            opp = signal_at(ctx, j, p, {"short" if sign > 0 else "long"})
            if opp:
                return done(sign * (c["close"] - entry) / risk, j, "opposite signal")
        if exit_style == "cond" and exit_conds and ctx.all_true(exit_conds, j):
            return done(sign * (c["close"] - entry) / risk, j, "exit rule")
    last = min(n - 1, i + hold)
    if last <= i:
        return None
    if last < i + hold and exit_style not in ("ema_trail", "scale", "opposite", "cond", "atr_trail"):
        return None
    r = sign * (ctx.c[last]["close"] - entry) / risk
    return done(banked + (0.5 * r if banked else r), last, "time exit")


def stats(rs: list[float]) -> dict:
    if not rs:
        return {"n": 0, "wins": 0, "win_rate": None, "expectancy": None, "total_r": 0, "max_dd_r": 0,
                "max_losing_streak": 0, "pf": None}
    eq = peak = dd = 0.0
    streak = worst = 0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
        streak = streak + 1 if r <= 0 else 0
        worst = max(worst, streak)
    wins = [r for r in rs if r > 0]
    losses = -sum(r for r in rs if r < 0)
    return {"n": len(rs), "wins": len(wins), "win_rate": round(len(wins) / len(rs) * 100, 1),
            "expectancy": round(sum(rs) / len(rs), 3), "total_r": round(sum(rs), 2), "max_dd_r": round(dd, 2),
            "max_losing_streak": worst, "pf": round(sum(wins) / losses, 2) if losses else None}


def signals(ctx: Ctx, p: dict, meta: dict, start: int = 0) -> list[dict]:
    dirs = allowed_dirs(p, meta)
    out = []
    prev = {"long": False, "short": False}
    for i in range(max(start, 1), ctx.n - 1):
        s = signal_at(ctx, i, p, dirs)
        # a rule that stays true for many candles is one signal, not one per candle
        cur = {"long": bool(s and s["direction"] == "long"), "short": bool(s and s["direction"] == "short")}
        if s and not prev[s["direction"]]:
            out.append(s)
        prev = cur
    return out


def run(candles: list[dict], params: dict, meta: dict, exits: list[str] | None = None, cost: float = 0.0,
        start: int = 0, end: int | None = None) -> list[dict]:
    """One grid row per exit style: stats + trades. Signals in [start, end)."""
    p = norm(params)
    ctx = Ctx(candles)
    sigs = [s for s in signals(ctx, p, meta, start) if end is None or s["i"] < end]
    exits = exits or variant_exits(p)
    grid = []
    for ex in exits:
        busy_until = -1
        trades = []
        for s in sigs:
            if s["i"] <= busy_until:
                continue
            res = simulate(ctx, s, ex, p, meta, cost)
            if res is None:
                continue
            r, bars, why = res
            busy_until = s["i"] + bars
            trades.append({"time": candles[s["i"]]["time"], "i": s["i"], "direction": s["direction"], "entry": s["entry"],
                           "stop": s["stop"], "r": r, "bars": bars, "exit": why})
        rs = [t["r"] for t in trades]
        grid.append({"htf": "none", "exit": ex, "exit_name": exit_name(ex, p), "all": stats(rs),
                     "long": stats([t["r"] for t in trades if t["direction"] == "long"]),
                     "short": stats([t["r"] for t in trades if t["direction"] == "short"]), "trades": trades})
    return grid


def exit_name(ex: str, p: dict) -> str:
    if ex == "fixed_r":
        return f"Take profit at {float(p.get('tp_r', 1.5)):g}R"
    if ex in ("ema_trail", "scale"):
        return EXITS[ex].replace("the trail EMA", f"the {int(p.get('trail', 14))} EMA").replace("the EMA", f"the {int(p.get('trail', 14))} EMA")
    if ex == "atr_trail":
        return f"Chandelier stop: trails {float(p.get('trail_atr', 3)):g} ATR behind the best close"
    return EXITS.get(ex, ex)


def variant_exits(p: dict) -> list[str]:
    ex = ["fixed_r", "fixed_1r", "fixed_2r", "ema_trail", "scale", "atr_trail", "opposite", "time_1r"]
    if p.get("exit_long") or p.get("exit_short"):
        ex.append("cond")
    if p["exit"] not in ex:
        ex.insert(0, p["exit"])
    return ex


def backtest(candles: list[dict], params: dict, meta: dict, cost: float = 0.0) -> dict:
    from datetime import datetime
    p = norm(params)
    grid = run(candles, p, meta, cost=cost)
    for g in grid:
        g["curve"] = _curve([t["r"] for t in g["trades"]])
        g["trades"] = g["trades"][-60:]
    ranked = [g for g in grid if g["all"]["n"] >= 8]
    best = max(ranked, key=lambda g: g["all"]["expectancy"], default=None)
    return {"code": meta.get("code"), "tf": p["entry_tf"], "bars": len(candles),
            "from": candles[0]["time"] if candles else None, "to": candles[-1]["time"] if candles else None,
            "cost": cost, "grid": grid, "best": {k: best[k] for k in ("htf", "exit", "exit_name", "all")} if best else None,
            "ran_at": datetime.now().astimezone().isoformat()}


def _curve(rs: list[float], points: int = 120) -> list[float]:
    eq, out = 0.0, [0.0]
    for r in rs:
        eq += r
        out.append(round(eq, 3))
    if len(out) > points:
        step = len(out) / points
        out = [out[int(k * step)] for k in range(points)] + [out[-1]]
    return out


def live_signal(candles: list[dict], params: dict, meta: dict) -> dict | None:
    """Signal on the last *closed* candle (the final candle in a live feed is still forming)."""
    p = norm(params)
    ctx = Ctx(candles)
    i = ctx.n - 2
    if i < 50:
        return None
    s = signal_at(ctx, i, p, allowed_dirs(p, meta))
    if not s:
        return None
    # don't re-fire a rule that was already true on the previous candle
    if signal_at(ctx, i - 1, p, {s["direction"]}):
        return None
    return s


def signals_for_chart(candles: list[dict], params: dict, meta: dict, cost: float = 0.0) -> dict:
    """Entries/exits for drawing on the chart, with the strategy's own exit style."""
    p = norm(params)
    g = run(candles, p, meta, exits=[p["exit"]], cost=cost)[0]
    marks = []
    for t in g["trades"]:
        j = min(len(candles) - 1, t["i"] + t["bars"])
        marks.append({"t": candles[t["i"]]["time"], "te": candles[j]["time"], "dir": t["direction"], "entry": t["entry"],
                      "stop": t["stop"], "r": t["r"], "exit": t["exit"]})
    return {"marks": marks, "stats": g["all"], "exit_name": g["exit_name"]}
