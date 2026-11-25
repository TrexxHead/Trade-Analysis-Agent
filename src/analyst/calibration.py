"""Kora's "training": replay every setup detector bar-by-bar over an
instrument's history and record what actually happened next.

For each historical bar i, the detectors see only candles[0..i] (plus the
last *completed* higher-timeframe bar), exactly as they would live. Each
plan is then simulated forward: limit entries must actually fill, and the
outcome is +RR at TP1, -1R at the stop (a bar touching both counts as a
loss - conservative), or marked-to-market in R at the time limit.

The result is a per (instrument, setup, direction) hit-rate and expectancy,
which live analysis uses to temper each setup's rule score. This is honest
calibration, not a promise: a few months of history is a small sample, and
the shrinkage toward break-even in `calibrate_confidence` is there so a
lucky 6-for-7 run doesn't masquerade as an edge.
"""
from __future__ import annotations

from src.analyst import ta
from src.analyst.setups import detect_all
from src.analyst.technical import Features, read_at

MAX_HOLD_BARS = 48
LIMIT_FILL_BARS = 6


def simulate(candles: list[dict], i: int, plan: dict) -> float | None:
    """R-multiple outcome of a plan generated at bar i, or None if a limit
    entry never filled."""
    sign = 1 if plan["direction"] == "long" else -1
    entry, stop, tp = plan["entry"], plan["stop"], plan["tp1"]
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    start = i + 1
    if plan["entry_type"] == "limit":
        fill = None
        for j in range(i + 1, min(i + 1 + LIMIT_FILL_BARS, len(candles))):
            c = candles[j]
            if (sign > 0 and c["low"] <= entry) or (sign < 0 and c["high"] >= entry):
                fill = j
                break
        if fill is None:
            return None
        start = fill
    end = min(start + MAX_HOLD_BARS, len(candles))
    for j in range(start, end):
        c = candles[j]
        hit_stop = c["low"] <= stop if sign > 0 else c["high"] >= stop
        hit_tp = c["high"] >= tp if sign > 0 else c["low"] <= tp
        if hit_stop:
            return -1.0
        # On a limit order's fill bar we can't know whether the target printed
        # before or after the fill, so only the stop counts there (conservative).
        if hit_tp and not (plan["entry_type"] == "limit" and j == start):
            return abs(tp - entry) / risk
    if end <= start:
        return None
    return sign * (candles[end - 1]["close"] - entry) / risk


def train_instrument(h1: list[dict], meta: dict, step: int = 1) -> list[dict]:
    """Returns stats rows [{setup, direction, n, wins, avg_r, expectancy}]."""
    f = Features(h1)
    h4 = ta.resample(h1, 4)
    f4 = Features(h4)
    # map each H1 bar to the last H4 bar that had fully closed by then
    h4_index, k = [], -1
    for c in h1:
        while k + 1 < len(h4) and h4[k + 1]["time"] <= c["time"]:
            k += 1
        h4_index.append(k - 1)  # k is the H4 bar still forming; k-1 is the last closed one

    outcomes: dict[tuple, list[float]] = {}
    last_signal: dict[tuple, int] = {}
    for i in range(210, len(h1) - 2, step):
        hk = h4_index[i]
        if hk < 60:
            continue
        htf = read_at(f4, hk, "H4")
        for cand in detect_all(f, i, htf, meta):
            key = (cand["setup"], cand["direction"])
            if i - last_signal.get(key, -999) < 6:  # one signal per setup per 6 bars - avoid counting the same move repeatedly
                continue
            last_signal[key] = i
            r = simulate(h1, i, cand)
            if r is not None:
                outcomes.setdefault(key, []).append(r)

    rows = []
    for (setup, direction), rs in outcomes.items():
        wins = sum(1 for r in rs if r > 0)
        rows.append({
            "setup": setup, "direction": direction, "n": len(rs), "wins": wins,
            "avg_r": round(sum(rs) / len(rs), 3),
            "expectancy": round(sum(rs) / len(rs), 3),
        })
    return sorted(rows, key=lambda r: (r["setup"], r["direction"]))


def calibrate_confidence(score: float, rr: float, stats: dict | None, prior_n: int = 12) -> dict:
    """Blend a candidate's rule score with its instrument's track record.

    Win rate is shrunk toward the break-even win rate for this RR
    (1 / (1 + RR)) with `prior_n` pseudo-trades, so small samples barely
    move the needle. Expectancy (in R) then scales the rule score by
    0.75x (≈ -0.5R) to 1.25x (≈ +0.5R).
    """
    breakeven = 1 / (1 + rr) if rr > 0 else 0.5
    if not stats or not stats.get("n"):
        return {"confidence": round(max(5, min(95, score * 0.9))), "hit_rate": None, "n": 0,
                "expectancy": None, "note": "Not yet calibrated on this instrument - run scripts/train_kora.py"}
    n, wins = stats["n"], stats["wins"]
    shrunk_wr = (wins + breakeven * prior_n) / (n + prior_n)
    shrunk_exp = stats["expectancy"] * n / (n + prior_n)
    edge = max(0.0, min(1.0, (shrunk_exp + 0.5)))  # -0.5R -> 0, +0.5R -> 1
    conf = score * (0.75 + 0.5 * edge)
    return {
        "confidence": round(max(5, min(95, conf))),
        "hit_rate": round(wins / n * 100),
        "shrunk_hit_rate": round(shrunk_wr * 100),
        "n": n,
        "expectancy": round(stats["expectancy"], 2),
        "note": (f"Historically {wins}/{n} hit TP1 ({wins / n:.0%}), avg {stats['expectancy']:+.2f}R"
                 + (" - small sample, treat as indicative" if n < 25 else "")),
    }
