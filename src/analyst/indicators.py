"""Indicator library shared by the strategy builder, research engine and charts.

Every indicator is causal: the value at bar i uses bars 0..i only, so a
strategy built from them can be backtested without lookahead. The same keys,
parameters and output names exist in static/chart.js, so what you see on the
chart is exactly what the builder tests.

compute(key, candles, params) -> {output_name: [float | None, ...]}
"""
from __future__ import annotations

import math
from datetime import datetime

from src.analyst import ta

Series = list  # list[float | None]


def _closes(c):
    return [x["close"] for x in c]


def wma(v: list, n: int) -> Series:
    out = [None] * len(v)
    w = n * (n + 1) / 2
    for i in range(n - 1, len(v)):
        win = v[i - n + 1:i + 1]
        if any(x is None for x in win):
            continue
        out[i] = sum(x * (k + 1) for k, x in enumerate(win)) / w
    return out


def hma(v: list, n: int) -> Series:
    a, b = wma(v, max(1, n // 2)), wma(v, n)
    diff = [None if x is None or y is None else 2 * x - y for x, y in zip(a, b)]
    first = next((i for i, x in enumerate(diff) if x is not None), len(diff))
    tail = wma(diff[first:], max(1, int(math.sqrt(n))))
    return [None] * first + tail


def ema_series(v: list, n: int) -> Series:
    """EMA that tolerates leading Nones."""
    first = next((i for i, x in enumerate(v) if x is not None), len(v))
    return [None] * first + ta.ema(v[first:], n)


def rolling(v: list, n: int, fn) -> Series:
    out = [None] * len(v)
    for i in range(n - 1, len(v)):
        win = v[i - n + 1:i + 1]
        if any(x is None for x in win):
            continue
        out[i] = fn(win)
    return out


def stdev(v: list, n: int) -> Series:
    def sd(w):
        m = sum(w) / len(w)
        return math.sqrt(sum((x - m) ** 2 for x in w) / len(w))
    return rolling(v, n, sd)


def stoch_k(c: list[dict], n: int) -> Series:
    out = [None] * len(c)
    for i in range(n - 1, len(c)):
        w = c[i - n + 1:i + 1]
        hi, lo = max(x["high"] for x in w), min(x["low"] for x in w)
        out[i] = 50.0 if hi == lo else (c[i]["close"] - lo) / (hi - lo) * 100
    return out


def sma_n(v, n):
    return rolling(v, n, lambda w: sum(w) / len(w))


def _bb(c, p):
    cl = _closes(c)
    mid, sd = sma_n(cl, p["period"]), stdev(cl, p["period"])
    up = [None if m is None else m + p["mult"] * s for m, s in zip(mid, sd)]
    lo = [None if m is None else m - p["mult"] * s for m, s in zip(mid, sd)]
    width = [None if m in (None, 0) else (u - l) / m * 100 for u, l, m in zip(up, lo, mid)]
    pctb = [None if u is None or u == l else (x - l) / (u - l) for x, u, l in zip(cl, up, lo)]
    return {"upper": up, "mid": mid, "lower": lo, "width": width, "pctb": pctb}


def _keltner(c, p):
    mid = ta.ema(_closes(c), p["period"])
    a = ta.atr(c, p["atr"])
    up = [None if m is None or x is None else m + p["mult"] * x for m, x in zip(mid, a)]
    lo = [None if m is None or x is None else m - p["mult"] * x for m, x in zip(mid, a)]
    return {"upper": up, "mid": mid, "lower": lo}


def _donchian(c, p):
    """Channel of the *previous* N candles, so 'close above upper' is a breakout."""
    n = p["period"]
    up, lo = [None] * len(c), [None] * len(c)
    for i in range(n, len(c)):
        w = c[i - n:i]
        up[i], lo[i] = max(x["high"] for x in w), min(x["low"] for x in w)
    mid = [None if u is None else (u + l) / 2 for u, l in zip(up, lo)]
    return {"upper": up, "lower": lo, "mid": mid}


def _supertrend(c, p):
    a = ta.atr(c, p["period"])
    m = p["mult"]
    line, d = [None] * len(c), [None] * len(c)
    fu = fl = None
    trend = 1
    for i, k in enumerate(c):
        if a[i] is None:
            continue
        hl2 = (k["high"] + k["low"]) / 2
        bu, bl = hl2 + m * a[i], hl2 - m * a[i]
        pc = c[i - 1]["close"] if i else k["close"]
        fu = bu if fu is None or bu < fu or pc > fu else fu
        fl = bl if fl is None or bl > fl or pc < fl else fl
        if trend == 1 and k["close"] < fl:
            trend = -1
        elif trend == -1 and k["close"] > fu:
            trend = 1
        line[i], d[i] = (fl if trend == 1 else fu), trend
    return {"line": line, "dir": d}


def _psar(c, p):
    step, mx = p["step"], p["max"]
    out, d = [None] * len(c), [None] * len(c)
    if len(c) < 3:
        return {"value": out, "dir": d}
    up = c[1]["close"] >= c[0]["close"]
    sar = c[0]["low"] if up else c[0]["high"]
    ep = c[1]["high"] if up else c[1]["low"]
    af = step
    for i in range(1, len(c)):
        k = c[i]
        sar = sar + af * (ep - sar)
        if up:
            sar = min(sar, c[i - 1]["low"], c[i - 2]["low"] if i > 1 else c[i - 1]["low"])
            if k["low"] < sar:
                up, sar, ep, af = False, ep, k["low"], step
            elif k["high"] > ep:
                ep, af = k["high"], min(mx, af + step)
        else:
            sar = max(sar, c[i - 1]["high"], c[i - 2]["high"] if i > 1 else c[i - 1]["high"])
            if k["high"] > sar:
                up, sar, ep, af = True, ep, k["high"], step
            elif k["low"] < ep:
                ep, af = k["low"], min(mx, af + step)
        out[i], d[i] = sar, 1 if up else -1
    return {"value": out, "dir": d}


def _ichimoku(c, p):
    def mid(n):
        o = [None] * len(c)
        for i in range(n - 1, len(c)):
            w = c[i - n + 1:i + 1]
            o[i] = (max(x["high"] for x in w) + min(x["low"] for x in w)) / 2
        return o
    t, k, b = mid(p["tenkan"]), mid(p["kijun"]), mid(p["senkou"])
    sh = p["kijun"]
    # the cloud you see at bar i was computed `kijun` bars earlier - causal
    a = [None] * len(c)
    s = [None] * len(c)
    for i in range(sh, len(c)):
        j = i - sh
        if t[j] is not None and k[j] is not None:
            a[i] = (t[j] + k[j]) / 2
        s[i] = b[j]
    return {"tenkan": t, "kijun": k, "span_a": a, "span_b": s}


def _macd(c, p):
    cl = _closes(c)
    f, s = ta.ema(cl, p["fast"]), ta.ema(cl, p["slow"])
    line = [None if x is None or y is None else x - y for x, y in zip(f, s)]
    sig = ema_series(line, p["signal"])
    hist = [None if x is None or y is None else x - y for x, y in zip(line, sig)]
    return {"macd": line, "signal": sig, "hist": hist}


def _stoch(c, p):
    k = stoch_k(c, p["k"])
    ks = sma_n(k, p["smooth"]) if p["smooth"] > 1 else k
    d = sma_n(ks, p["d"]) if p["d"] > 1 else ks
    return {"k": ks, "d": d}


def _stochrsi(c, p):
    r = ta.rsi(_closes(c), p["rsi"])
    n = p["stoch"]
    raw = [None] * len(r)
    for i in range(len(r)):
        w = r[max(0, i - n + 1):i + 1]
        if len(w) < n or any(x is None for x in w):
            continue
        hi, lo = max(w), min(w)
        raw[i] = 50.0 if hi == lo else (r[i] - lo) / (hi - lo) * 100
    k = sma_n(raw, p["k"])
    return {"k": k, "d": sma_n(k, p["d"])}


def _adx(c, p):
    a, plus, minus = ta.adx(c, p["period"])
    return {"adx": a, "plus": plus, "minus": minus}


def _cci(c, p):
    n = p["period"]
    tp = [(x["high"] + x["low"] + x["close"]) / 3 for x in c]
    out = [None] * len(c)
    for i in range(n - 1, len(c)):
        w = tp[i - n + 1:i + 1]
        m = sum(w) / n
        md = sum(abs(x - m) for x in w) / n
        out[i] = 0.0 if md == 0 else (tp[i] - m) / (0.015 * md)
    return {"value": out}


def _willr(c, p):
    k = stoch_k(c, p["period"])
    return {"value": [None if x is None else x - 100 for x in k]}


def _roc(c, p):
    cl, n = _closes(c), p["period"]
    return {"value": [None if i < n or not cl[i - n] else (cl[i] / cl[i - n] - 1) * 100 for i in range(len(cl))]}


def _zone(c, p):
    from src.analyst.strategies import zone_series
    return {"value": zone_series(_closes(c), p["ema"], p["lookback"])}


def _hour(c, p):
    return {"value": [datetime.fromisoformat(x["time"]).hour + datetime.fromisoformat(x["time"]).minute / 60 for x in c]}


def _dow(c, p):
    return {"value": [datetime.fromisoformat(x["time"]).weekday() for x in c]}


def _candle(c, p):
    body = [abs(x["close"] - x["open"]) for x in c]
    rng = [x["high"] - x["low"] for x in c]
    return {"body": body, "range": rng, "body_pct": [b / r * 100 if r else 0 for b, r in zip(body, rng)],
            "dir": [1 if x["close"] > x["open"] else -1 if x["close"] < x["open"] else 0 for x in c]}


def _highest(c, p):
    n = p["period"]
    return {"value": [None if i < n - 1 else max(x["high"] for x in c[i - n + 1:i + 1]) for i in range(len(c))]}


def _lowest(c, p):
    n = p["period"]
    return {"value": [None if i < n - 1 else min(x["low"] for x in c[i - n + 1:i + 1]) for i in range(len(c))]}


# key: (label, params, outputs, pane, fn)
REGISTRY: dict[str, dict] = {
    "sma": {"name": "SMA", "params": {"period": 50}, "outputs": ["value"], "pane": False,
            "fn": lambda c, p: {"value": sma_n(_closes(c), p["period"])}},
    "ema": {"name": "EMA", "params": {"period": 20}, "outputs": ["value"], "pane": False,
            "fn": lambda c, p: {"value": ta.ema(_closes(c), p["period"])}},
    "wma": {"name": "WMA", "params": {"period": 20}, "outputs": ["value"], "pane": False,
            "fn": lambda c, p: {"value": wma(_closes(c), p["period"])}},
    "hma": {"name": "Hull MA", "params": {"period": 21}, "outputs": ["value"], "pane": False,
            "fn": lambda c, p: {"value": hma(_closes(c), p["period"])}},
    "vwap": {"name": "VWAP (session)", "params": {}, "outputs": ["value"], "pane": False,
             "fn": lambda c, p: {"value": ta.session_vwap(c)}},
    "bb": {"name": "Bollinger Bands", "params": {"period": 20, "mult": 2.0}, "outputs": ["upper", "mid", "lower", "width", "pctb"], "pane": False, "fn": _bb},
    "keltner": {"name": "Keltner Channel", "params": {"period": 20, "atr": 10, "mult": 2.0}, "outputs": ["upper", "mid", "lower"], "pane": False, "fn": _keltner},
    "donchian": {"name": "Donchian Channel", "params": {"period": 20}, "outputs": ["upper", "lower", "mid"], "pane": False, "fn": _donchian},
    "supertrend": {"name": "Supertrend", "params": {"period": 10, "mult": 3.0}, "outputs": ["line", "dir"], "pane": False, "fn": _supertrend},
    "psar": {"name": "Parabolic SAR", "params": {"step": 0.02, "max": 0.2}, "outputs": ["value", "dir"], "pane": False, "fn": _psar},
    "ichimoku": {"name": "Ichimoku", "params": {"tenkan": 9, "kijun": 26, "senkou": 52}, "outputs": ["tenkan", "kijun", "span_a", "span_b"], "pane": False, "fn": _ichimoku},
    "highest": {"name": "Highest high", "params": {"period": 20}, "outputs": ["value"], "pane": False, "fn": _highest},
    "lowest": {"name": "Lowest low", "params": {"period": 20}, "outputs": ["value"], "pane": False, "fn": _lowest},
    "rsi": {"name": "RSI", "params": {"period": 14}, "outputs": ["value"], "pane": True,
            "fn": lambda c, p: {"value": ta.rsi(_closes(c), p["period"])}},
    "stoch": {"name": "Stochastic", "params": {"k": 14, "d": 3, "smooth": 3}, "outputs": ["k", "d"], "pane": True, "fn": _stoch},
    "stochrsi": {"name": "Stoch RSI", "params": {"rsi": 14, "stoch": 14, "k": 3, "d": 3}, "outputs": ["k", "d"], "pane": True, "fn": _stochrsi},
    "macd": {"name": "MACD", "params": {"fast": 12, "slow": 26, "signal": 9}, "outputs": ["macd", "signal", "hist"], "pane": True, "fn": _macd},
    "adx": {"name": "ADX / DMI", "params": {"period": 14}, "outputs": ["adx", "plus", "minus"], "pane": True, "fn": _adx},
    "cci": {"name": "CCI", "params": {"period": 20}, "outputs": ["value"], "pane": True, "fn": _cci},
    "willr": {"name": "Williams %R", "params": {"period": 14}, "outputs": ["value"], "pane": True, "fn": _willr},
    "roc": {"name": "Rate of change", "params": {"period": 12}, "outputs": ["value"], "pane": True, "fn": _roc},
    "atr": {"name": "ATR", "params": {"period": 14}, "outputs": ["value"], "pane": True,
            "fn": lambda c, p: {"value": ta.atr(c, p["period"])}},
    "zone": {"name": "Stoch zone (EMA)", "params": {"ema": 10, "lookback": 200}, "outputs": ["value"], "pane": True, "fn": _zone},
    "candle": {"name": "Candle", "params": {}, "outputs": ["body", "range", "body_pct", "dir"], "pane": True, "fn": _candle},
    "hour": {"name": "Hour (UTC)", "params": {}, "outputs": ["value"], "pane": True, "fn": _hour},
    "dow": {"name": "Weekday (0=Mon)", "params": {}, "outputs": ["value"], "pane": True, "fn": _dow},
}


def compute(key: str, candles: list[dict], params: dict | None = None) -> dict[str, Series]:
    spec = REGISTRY[key]
    p = {**spec["params"], **(params or {})}
    for k, v in p.items():  # integer-ish params come from forms as floats
        if isinstance(spec["params"].get(k), int):
            p[k] = int(v)
    return spec["fn"](candles, p)


def catalog() -> list[dict]:
    """For the builder UI."""
    return [{"key": k, "name": v["name"], "params": v["params"], "outputs": v["outputs"], "pane": v["pane"]}
            for k, v in REGISTRY.items()]
