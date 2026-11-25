"""Indicator library for Kora's analyst - pure Python, no numpy/pandas.

Unlike src/strategy/indicators.py (end-aligned lists of varying length),
every function here returns a list the SAME length as its input, with None
during the warm-up period. That lets one precomputed Features object serve
both live analysis (look at index -1) and historical calibration (walk
index i forward bar by bar) without any lookahead: value[i] only ever
depends on candles[0..i].
"""
from __future__ import annotations

from datetime import datetime


def sma(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= period:
            s -= values[i - period]
        if i >= period - 1:
            out[i] = s / period
    return out


def ema(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = (values[i] - prev) * k + prev
        out[i] = prev
    return out


def rsi(values: list[float], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = values[i] - values[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    ag, al = gains / period, losses / period
    out[period] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(period + 1, len(values)):
        d = values[i] - values[i - 1]
        ag = (ag * (period - 1) + max(d, 0)) / period
        al = (al * (period - 1) + max(-d, 0)) / period
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def true_range(candles: list[dict]) -> list[float]:
    tr = [candles[0]["high"] - candles[0]["low"]] if candles else []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
    return tr


def wilder(values: list[float], period: int, start: int = 0) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) - start < period:
        return out
    prev = sum(values[start:start + period]) / period
    out[start + period - 1] = prev
    for i in range(start + period, len(values)):
        prev = (prev * (period - 1) + values[i]) / period
        out[i] = prev
    return out


def atr(candles: list[dict], period: int = 14) -> list[float | None]:
    return wilder(true_range(candles), period, start=1) if len(candles) > period else [None] * len(candles)


def macd(values: list[float], fast=12, slow=26, signal=9):
    ef, es = ema(values, fast), ema(values, slow)
    line = [f - s if f is not None and s is not None else None for f, s in zip(ef, es)]
    first = next((i for i, v in enumerate(line) if v is not None), None)
    sig: list[float | None] = [None] * len(values)
    if first is not None:
        sub = ema([v for v in line[first:]], signal)
        for j, v in enumerate(sub):
            sig[first + j] = v
    hist = [l - s if l is not None and s is not None else None for l, s in zip(line, sig)]
    return line, sig, hist


def adx(candles: list[dict], period: int = 14):
    """Returns (adx, +DI, -DI), each full length."""
    n = len(candles)
    plus_dm, minus_dm = [0.0] * n, [0.0] * n
    for i in range(1, n):
        up = candles[i]["high"] - candles[i - 1]["high"]
        dn = candles[i - 1]["low"] - candles[i]["low"]
        plus_dm[i] = up if up > dn and up > 0 else 0.0
        minus_dm[i] = dn if dn > up and dn > 0 else 0.0
    tr = true_range(candles)
    s_tr, s_p, s_m = wilder(tr, period, 1), wilder(plus_dm, period, 1), wilder(minus_dm, period, 1)
    pdi: list[float | None] = [None] * n
    mdi: list[float | None] = [None] * n
    dx = [0.0] * n
    first = None
    for i in range(n):
        if s_tr[i]:
            pdi[i] = 100 * s_p[i] / s_tr[i]
            mdi[i] = 100 * s_m[i] / s_tr[i]
            tot = pdi[i] + mdi[i]
            dx[i] = 100 * abs(pdi[i] - mdi[i]) / tot if tot else 0.0
            first = i if first is None else first
    out: list[float | None] = [None] * n
    if first is not None:
        sub = wilder(dx, period, first)
        for i in range(n):
            out[i] = sub[i]
    return out, pdi, mdi


def bollinger_width(values: list[float], period: int = 20, mult: float = 2.0) -> list[float | None]:
    mid = sma(values, period)
    out: list[float | None] = [None] * len(values)
    for i in range(period - 1, len(values)):
        win = values[i - period + 1:i + 1]
        m = mid[i]
        sd = (sum((v - m) ** 2 for v in win) / period) ** 0.5
        out[i] = (2 * mult * sd) / m if m else None
    return out


def rolling_percentile(values: list[float | None], window: int = 100) -> list[float | None]:
    """Percentile rank (0-100) of each value within its trailing window."""
    out: list[float | None] = [None] * len(values)
    for i, v in enumerate(values):
        if v is None:
            continue
        win = [x for x in values[max(0, i - window + 1):i + 1] if x is not None]
        if len(win) < 20:
            continue
        out[i] = 100.0 * sum(1 for x in win if x <= v) / len(win)
    return out


def session_vwap(candles: list[dict]) -> list[float | None]:
    """VWAP reset at each UTC day boundary, weighted by tick volume when the
    feed provides it (MetaApi's tickVolume), else by bar range as a proxy."""
    out: list[float | None] = [None] * len(candles)
    day, pv, vol = None, 0.0, 0.0
    for i, c in enumerate(candles):
        d = c["time"][:10]
        if d != day:
            day, pv, vol = d, 0.0, 0.0
        w = c.get("volume") or max(c["high"] - c["low"], 1e-12)
        tp = (c["high"] + c["low"] + c["close"]) / 3
        pv += tp * w
        vol += w
        out[i] = pv / vol if vol else None
    return out


def swings(candles: list[dict], k: int = 3) -> list[dict]:
    """Fractal swing points. A swing at bar j is only *known* once k more
    bars have printed, so each swing records `confirmed` = j + k - callers
    must filter on confirmed <= i to avoid lookahead."""
    out = []
    for j in range(k, len(candles) - k):
        h, l = candles[j]["high"], candles[j]["low"]
        if all(h > candles[j + d]["high"] and h >= candles[j - d]["high"] for d in range(1, k + 1)):
            out.append({"i": j, "confirmed": j + k, "type": "high", "price": h})
        if all(l < candles[j + d]["low"] and l <= candles[j - d]["low"] for d in range(1, k + 1)):
            out.append({"i": j, "confirmed": j + k, "type": "low", "price": l})
    return out


def cluster_levels(points: list[dict], tolerance: float, max_levels: int = 8) -> list[dict]:
    """Merge swing prices within `tolerance` of each other into S/R levels,
    strength = number of touches (recent touches weigh a little more)."""
    levels: list[dict] = []
    for p in sorted(points, key=lambda x: x["price"]):
        if levels and abs(p["price"] - levels[-1]["price"]) <= tolerance:
            lv = levels[-1]
            lv["touches"] += 1
            lv["prices"].append(p["price"])
            lv["price"] = sum(lv["prices"]) / len(lv["prices"])
            lv["last_i"] = max(lv["last_i"], p["i"])
        else:
            levels.append({"price": p["price"], "prices": [p["price"]], "touches": 1, "last_i": p["i"]})
    for lv in levels:
        lv.pop("prices")
    levels.sort(key=lambda lv: (-lv["touches"], -lv["last_i"]))
    return sorted(levels[:max_levels], key=lambda lv: lv["price"])


def resample(candles: list[dict], hours: int) -> list[dict]:
    """Aggregate candles into `hours`-hour buckets aligned to UTC midnight
    (H1 -> H4 for calibration, where fetching a separate H4 history would
    double the API calls)."""
    out: list[dict] = []
    key = None
    for c in candles:
        t = datetime.fromisoformat(c["time"])
        k = (t.date(), t.hour // hours)
        if k != key:
            key = k
            out.append({**c, "time": t.replace(hour=(t.hour // hours) * hours, minute=0, second=0, microsecond=0).isoformat()})
        else:
            b = out[-1]
            b["high"], b["low"], b["close"] = max(b["high"], c["high"]), min(b["low"], c["low"]), c["close"]
            b["volume"] = (b.get("volume") or 0) + (c.get("volume") or 0)
    return out


def returns(closes: list[float]) -> list[float]:
    return [(closes[i] / closes[i - 1] - 1) if closes[i - 1] else 0.0 for i in range(1, len(closes))]


def correlation(a: list[float], b: list[float]) -> float | None:
    n = min(len(a), len(b))
    if n < 20:
        return None
    a, b = a[-n:], b[-n:]
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    return cov / (va * vb) ** 0.5 if va and vb else None
