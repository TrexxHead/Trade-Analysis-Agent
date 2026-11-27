"""Entry-setup detectors.

Each detector looks at the execution-timeframe Features at bar i (plus the
higher-timeframe trend) and returns a fully specified entry *plan* -
entry / stop / two targets / reward:risk / why / what invalidates it - or
None. Kora never places these; they're for you to assess.

Detectors only read data up to bar i, so the same functions are replayed
bar by bar over history in src/analyst/calibration.py to measure how each
setup has actually performed on each instrument ("training").
"""
from __future__ import annotations

from datetime import datetime

from src.analyst.technical import Features, trend_score

SETUP_NAMES = {
    "trend_pullback": "Trend pullback",
    "breakout_retest": "Breakout & retest",
    "range_reversion": "Range reversion",
    "liquidity_sweep": "Liquidity sweep",
    "session_breakout": "Asia-range breakout",
}

MIN_STOP_ATR, MAX_STOP_ATR = 0.6, 3.0


def _plan(f: Features, i: int, direction: str, entry: float, stop: float, entry_type: str) -> dict | None:
    a = f.atr[i]
    if not a:
        return None
    risk = abs(entry - stop)
    if risk < MIN_STOP_ATR * a:  # too tight to survive normal noise - widen to the minimum
        risk = MIN_STOP_ATR * a
        stop = entry - risk if direction == "long" else entry + risk
    if risk > MAX_STOP_ATR * a:
        return None
    sign = 1 if direction == "long" else -1
    levels = f.levels(i)
    opposing = sorted(
        (lv["price"] for lv in levels if sign * (lv["price"] - entry) > 0),
        key=lambda p: abs(p - entry),
    )
    # TP1: nearest opposing S/R if it pays at least 1.2R, else a fixed 1.5R.
    tp1 = entry + sign * 1.5 * risk
    for p in opposing:
        if abs(p - entry) >= 1.2 * risk:
            tp1 = p - sign * 0.1 * a  # just in front of the level
            break
    tp2 = entry + sign * max(2.5 * risk, abs(tp1 - entry) * 1.6)
    for p in opposing:
        if abs(p - entry) >= 2.0 * risk and abs(p - entry) > abs(tp1 - entry) + 0.3 * risk:
            tp2 = p - sign * 0.1 * a
            break
    return {
        "direction": direction,
        "entry_type": entry_type,
        "entry": entry,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "risk_atr": round(risk / a, 2),
        "rr1": round(abs(tp1 - entry) / risk, 2),
        "rr2": round(abs(tp2 - entry) / risk, 2),
    }


def _candidate(setup, plan, score, confluence, against, trigger, invalidation):
    if plan is None:
        return None
    return {
        "setup": setup,
        "setup_name": SETUP_NAMES[setup],
        **plan,
        "score": int(max(5, min(95, score))),
        "confluence": confluence,
        "against": against,
        "trigger": trigger,
        "invalidation": invalidation,
    }


def trend_pullback(f: Features, i: int, htf: dict, meta: dict) -> dict | None:
    if i < 60 or f.ema50[i] is None or f.ema20[i] is None or f.rsi[i] is None or not f.atr[i]:
        return None
    ts = trend_score(f, i)
    if ts is None or abs(ts) < 0.3:
        return None
    direction = "long" if ts > 0 else "short"
    sign = 1 if direction == "long" else -1
    if htf.get("direction") not in ("up" if sign > 0 else "down",):
        return None
    a, c = f.atr[i], f.close[i]
    recent = f.c[i - 3:i + 1]
    touch20 = any(abs((x["low"] if sign > 0 else x["high"]) - f.ema20[i]) <= 0.3 * a or
                  sign * ((x["low"] if sign > 0 else x["high"]) - f.ema20[i]) < 0 for x in recent)
    touch50 = any(sign * ((x["low"] if sign > 0 else x["high"]) - f.ema50[i]) <= 0.3 * a for x in recent)
    if not (touch20 or touch50):
        return None
    if sign * (c - f.ema50[i]) < 0:  # closed through the deeper EMA - pullback is becoming a reversal
        return None
    rsis = [r for r in f.rsi[i - 6:i + 1] if r is not None]
    if len(rsis) < 7:
        return None
    if sign > 0 and not (min(rsis[:-1]) < 48 and rsis[-1] > rsis[-2] and rsis[-1] < 66):
        return None
    if sign < 0 and not (max(rsis[:-1]) > 52 and rsis[-1] < rsis[-2] and rsis[-1] > 34):
        return None
    lows_highs = [x["low"] for x in f.c[i - 5:i + 1]] if sign > 0 else [x["high"] for x in f.c[i - 5:i + 1]]
    stop = (min(lows_highs) - 0.25 * a) if sign > 0 else (max(lows_highs) + 0.25 * a)
    plan = _plan(f, i, direction, c, stop, "market")
    score, conf, against = 50, [f"Trend {('up' if sign > 0 else 'down')} on execution TF (score {ts:+.2f})",
                                f"Higher timeframe agrees ({htf.get('tf', 'HTF')} {htf.get('direction')})"], []
    if abs(htf.get("trend_score") or 0) > 0.5:
        score += 8
        conf.append("Higher-timeframe trend is strong")
    adx_v = f.adx[i] or 0
    if adx_v >= 25:
        score += 8
        conf.append(f"ADX {adx_v:.0f} - trend has strength")
    elif adx_v < 18:
        score -= 8
        against.append(f"ADX {adx_v:.0f} - weak trend, pullbacks may not resume")
    st = f.structure(i)
    if st == ("HH/HL" if sign > 0 else "LH/LL"):
        score += 8
        conf.append(f"Market structure {st}")
    elif st != "unclear":
        score -= 6
        against.append(f"Structure {st} - not cleanly trending")
    if touch50:
        score += 6
        conf.append("Pulled back to the EMA50 (deeper, better-priced entry)")
    else:
        conf.append("Pulled back to the EMA20")
    if f.vwap[i] and sign * (c - f.vwap[i]) > 0:
        score += 4
        conf.append("Price on the trend side of session VWAP")
    conf.append(f"RSI reset to {min(rsis[:-1]) if sign > 0 else max(rsis[:-1]):.0f} and turning")
    return _candidate("trend_pullback", plan, score, conf, against,
                      trigger="Entry at market on the current close; better if the next candle confirms in trend direction.",
                      invalidation=f"A close {'below' if sign > 0 else 'above'} the EMA50 "
                                   f"({f.ema50[i]:.5g}) or the stop level.")


def breakout_retest(f: Features, i: int, htf: dict, meta: dict) -> dict | None:
    if i < 60 or not f.atr[i]:
        return None
    a, c, bar = f.atr[i], f.close[i], f.c[i]
    prior_levels = [lv for lv in f.levels(i - 8) if lv["touches"] >= 2]
    for lv in prior_levels:
        L = lv["price"]
        for sign, direction in ((1, "long"), (-1, "short")):
            window = range(i - 8, i)
            broke = any(sign * (f.close[j] - L) > 0.2 * a and sign * (f.close[j - 1] - L) <= 0.2 * a for j in window)
            if not broke:
                continue
            probe = bar["low"] if sign > 0 else bar["high"]
            if not (sign * (probe - L) <= 0.35 * a and sign * (c - L) > 0):
                continue
            stop = L - sign * 0.8 * a
            plan = _plan(f, i, direction, L + sign * 0.05 * a, stop, "limit")
            score = 52 + min(lv["touches"], 5) * 4
            conf = [f"Broke and held {'above' if sign > 0 else 'below'} a {lv['touches']}-touch level at {L:.5g}",
                    "Now retesting the broken level from the other side"]
            against = []
            if htf.get("direction") == ("up" if sign > 0 else "down"):
                score += 10
                conf.append(f"Breakout is in the higher-timeframe direction ({htf.get('direction')})")
            elif htf.get("direction") not in (None, "flat"):
                score -= 10
                against.append("Breakout is against the higher-timeframe trend")
            if (f.atr_pct[i] or 50) > 85:
                score -= 6
                against.append("Volatility very elevated - false breaks more common")
            return _candidate("breakout_retest", plan, score, conf, against,
                              trigger=f"Limit order at the retest of {L:.5g}; or wait for a rejection candle there.",
                              invalidation=f"A close back {'below' if sign > 0 else 'above'} {L:.5g} - the breakout failed.")
    return None


def range_reversion(f: Features, i: int, htf: dict, meta: dict) -> dict | None:
    if i < 60 or not f.atr[i] or f.rsi[i] is None or f.rsi[i - 1] is None:
        return None
    if (f.adx[i] or 99) >= 20:
        return None
    a, c = f.atr[i], f.close[i]
    levels = [lv for lv in f.levels(i) if lv["touches"] >= 2]
    sup = max((lv for lv in levels if lv["price"] <= c + 0.4 * a), key=lambda lv: lv["price"], default=None)
    res = min((lv for lv in levels if lv["price"] >= c - 0.4 * a), key=lambda lv: lv["price"], default=None)
    r, rp = f.rsi[i], f.rsi[i - 1]
    if sup and abs(c - sup["price"]) <= 0.4 * a and rp < 38 and r > rp:
        direction, sign, L = "long", 1, sup
    elif res and abs(c - res["price"]) <= 0.4 * a and rp > 62 and r < rp:
        direction, sign, L = "short", -1, res
    else:
        return None
    stop = L["price"] - sign * 0.7 * a
    plan = _plan(f, i, direction, c, stop, "market")
    score = 50 + min(L["touches"], 5) * 3
    conf = [f"ADX {f.adx[i]:.0f} - range-bound conditions",
            f"At a {L['touches']}-touch {'support' if sign > 0 else 'resistance'} ({L['price']:.5g})",
            f"RSI stretched ({rp:.0f}) and turning back"]
    against = []
    if htf.get("direction") == ("down" if sign > 0 else "up"):
        score -= 12
        against.append(f"Fading into a higher-timeframe {'down' if sign > 0 else 'up'}trend")
    if f.bbw_pct[i] is not None and f.bbw_pct[i] < 15:
        score -= 8
        against.append("Bollinger squeeze - range may be about to break rather than hold")
    return _candidate("range_reversion", plan, score, conf, against,
                      trigger="Entry at market; scale in only if the level holds on the next close.",
                      invalidation=f"A decisive close {'below' if sign > 0 else 'above'} {L['price']:.5g}.")


def liquidity_sweep(f: Features, i: int, htf: dict, meta: dict) -> dict | None:
    if i < 60 or not f.atr[i]:
        return None
    a, bar = f.atr[i], f.c[i]
    rng = bar["high"] - bar["low"]
    if rng <= 0:
        return None
    sw = [s for s in f.known_swings(i - 1, 40) if s["i"] < i - 3]
    for s in reversed(sw):
        if s["type"] == "low" and bar["low"] < s["price"] < bar["close"]:
            wick = min(bar["open"], bar["close"]) - bar["low"]
            if wick / rng < 0.5:
                continue
            direction, sign = "long", 1
        elif s["type"] == "high" and bar["high"] > s["price"] > bar["close"]:
            wick = bar["high"] - max(bar["open"], bar["close"])
            if wick / rng < 0.5:
                continue
            direction, sign = "short", -1
        else:
            continue
        stop = (bar["low"] - 0.2 * a) if sign > 0 else (bar["high"] + 0.2 * a)
        plan = _plan(f, i, direction, bar["close"], stop, "market")
        score = 54
        conf = [f"Swept the swing {'low' if sign > 0 else 'high'} at {s['price']:.5g} and closed back inside",
                f"Rejection wick is {wick / rng:.0%} of the candle"]
        against = []
        if htf.get("direction") == ("up" if sign > 0 else "down"):
            score += 10
            conf.append("Sweep is in the higher-timeframe trend direction (stop-hunt before continuation)")
        elif htf.get("direction") not in (None, "flat"):
            score -= 8
            against.append("Counter to the higher-timeframe trend")
        r = f.rsi[i]
        if r is not None and ((sign > 0 and r < 40) or (sign < 0 and r > 60)):
            score += 6
            conf.append(f"RSI {r:.0f} - stretched into the sweep")
        return _candidate("liquidity_sweep", plan, score, conf, against,
                          trigger="Entry on the sweep candle close; aggressive traders can enter on a 50% wick retrace.",
                          invalidation=f"Price trading back through the sweep extreme ({bar['low' if sign > 0 else 'high']:.5g}).")
    return None


def session_breakout(f: Features, i: int, htf: dict, meta: dict) -> dict | None:
    """London breakout of the Asia range (00:00-07:00 UTC) - only for
    session-driven markets (gold/silver/FX), never the 24/7 synthetics."""
    if not meta.get("currencies") or i < 30 or not f.atr[i]:
        return None
    t = datetime.fromisoformat(f.c[i]["time"])
    if not 7 <= t.hour < 12:
        return None
    day = f.c[i]["time"][:10]
    asia = [x for x in f.c[max(0, i - 40):i + 1] if x["time"][:10] == day and datetime.fromisoformat(x["time"]).hour < 7]
    if len(asia) < 3:
        return None
    hi, lo = max(x["high"] for x in asia), min(x["low"] for x in asia)
    a, c = f.atr[i], f.close[i]
    if hi - lo > 2.5 * a or hi - lo < 0.3 * a:
        return None
    since = [j for j in range(i - 5, i + 1) if datetime.fromisoformat(f.c[j]["time"]).hour >= 7 and f.c[j]["time"][:10] == day]
    for sign, direction, edge in ((1, "long", hi), (-1, "short", lo)):
        if not any(sign * (f.close[j] - edge) > 0.1 * a for j in since):
            continue
        if sign * (c - edge) > 0.8 * a:  # already ran - don't chase
            return None
        mid = (hi + lo) / 2
        plan = _plan(f, i, direction, edge, mid, "limit")
        score = 55
        conf = [f"London broke the Asia range ({lo:.5g} - {hi:.5g}) to the {'upside' if sign > 0 else 'downside'}",
                "Price still close enough to the range edge to enter on a retest"]
        against = []
        if htf.get("direction") == ("up" if sign > 0 else "down"):
            score += 10
            conf.append("Break aligns with the higher-timeframe trend")
        elif htf.get("direction") not in (None, "flat"):
            score -= 10
            against.append("Break is against the higher-timeframe trend")
        return _candidate("session_breakout", plan, score, conf, against,
                          trigger=f"Limit at the range edge {edge:.5g} (retest); skip if price never comes back.",
                          invalidation="Price closing back inside the Asia range midpoint.")
    return None


DETECTORS = [trend_pullback, breakout_retest, range_reversion, liquidity_sweep, session_breakout]


def detect_all(f: Features, i: int, htf: dict, meta: dict) -> list[dict]:
    out = []
    off = set(meta.get("setups_off") or ())
    for det in DETECTORS:
        if det.__name__ in off:
            continue
        try:
            cand = det(f, i, htf, meta)
        except (TypeError, ZeroDivisionError, IndexError):
            cand = None
        if cand:
            spikes = meta.get("spikes")
            if spikes and ((spikes == "up" and cand["direction"] == "short") or
                           (spikes == "down" and cand["direction"] == "long")):
                cand["score"] = max(5, cand["score"] - 15)
                cand["against"].append(
                    f"{meta.get('name')} spikes {'up' if spikes == 'up' else 'down'} unpredictably - "
                    f"a {cand['direction']} here sits on the wrong side of spike risk")
            out.append(cand)
    return out
