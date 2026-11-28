"""Builds Kora's full breakdown for one instrument.

The report is a plain dict (persisted as JSON in `analyses`) that every
hub screen reads: bias/confidence/regime for the agent page, per-timeframe
technicals and key levels for the breakdown page, ranked entry plans with
their reasoning, the fundamental context, and a short reasoning chain.
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.analyst.calibration import calibrate_confidence
from src.analyst.setups import detect_all
from src.analyst.technical import Features, alignment, mtf_score, read_at, regime

TF_ORDER = ["D1", "H4", "H1", "M15"]


def fmt(v: float | None, digits: int) -> str:
    return "—" if v is None else f"{v:,.{digits}f}"


def grade(conf: float) -> str:
    return "A" if conf >= 70 else "B" if conf >= 55 else "C"


def analyse_instrument(meta: dict, candles_by_tf: dict[str, list[dict]], exec_tf: str,
                       fundamentals: dict, stats: dict, live_price: float | None = None) -> dict:
    digits = meta.get("digits", 2)
    feats = {tf: Features(c) for tf, c in candles_by_tf.items() if c and len(c) >= 60}
    reads = {tf: read_at(f, f.n - 1, tf) for tf, f in feats.items()}
    if exec_tf not in feats:
        raise ValueError(f"not enough {exec_tf} candles to analyse {meta['code']}")
    fx = feats[exec_tf]
    i = fx.n - 1
    price = live_price or fx.close[i]
    htf = reads.get("H4") or reads.get("D1") or {}

    s = mtf_score(reads)
    agree, total = alignment(reads)
    bias = "Neutral" if s is None or abs(s) < 0.2 else ("Bullish" if s > 0 else "Bearish")
    bias_conf = 0 if s is None else round(min(95, 25 + 55 * abs(s) + 15 * (agree / max(total, 1))))
    reg = regime(reads)

    # --- candidates -------------------------------------------------------
    candidates = detect_all(fx, i, htf, meta)
    ev = fundamentals.get("event_risk")
    lean = fundamentals.get("lean", 0)
    for c in candidates:
        sign = 1 if c["direction"] == "long" else -1
        if ev:
            c["score"] = max(5, c["score"] - 15)
            c["against"].append(ev["text"])
        if abs(lean) >= 1:
            if sign * lean > 0:
                c["score"] = min(95, c["score"] + 5)
                c["confluence"].append(f"Fundamental tilt agrees — {fundamentals['lean_text']}")
            else:
                c["score"] = max(5, c["score"] - 8)
                c["against"].append(f"Fundamental tilt disagrees — {fundamentals['lean_text']}")
        cal = calibrate_confidence(c["score"], c["rr1"], stats.get((meta["code"], c["setup"], c["direction"])))
        c["calibration"] = cal
        c["confidence"] = cal["confidence"]
        c["grade"] = grade(c["confidence"])
        c["timeframe"] = exec_tf
        c["distance_atr"] = round(abs(price - c["entry"]) / (fx.atr[i] or 1), 2)
        c["title"] = f"{c['setup_name']} — {c['direction'].upper()} {meta['code']}"
        c["levels_text"] = (f"Entry {fmt(c['entry'], digits)} ({c['entry_type']}) · SL {fmt(c['stop'], digits)} · "
                            f"TP1 {fmt(c['tp1'], digits)} ({c['rr1']}R) · TP2 {fmt(c['tp2'], digits)} ({c['rr2']}R)")
    candidates.sort(key=lambda c: -c["confidence"])
    best = candidates[0] if candidates else None

    # --- key levels across timeframes ------------------------------------
    key_levels = []
    a = fx.atr[i] or 1
    for tf in ("D1", "H4", "H1"):
        for lv in (reads.get(tf) or {}).get("levels", []):
            if abs(lv["price"] - price) <= 6 * a:
                key_levels.append({"price": lv["price"], "touches": lv["touches"], "tf": tf,
                                   "kind": "resistance" if lv["price"] > price else "support",
                                   "distance_atr": round((lv["price"] - price) / a, 2)})
    key_levels.sort(key=lambda lv: -lv["price"])
    d1 = feats.get("D1")
    prev_day = None
    if d1 and d1.n >= 2:
        pdc = d1.c[-2]
        prev_day = {"high": pdc["high"], "low": pdc["low"], "close": pdc["close"]}

    # --- live signals (agent page) -----------------------------------------
    h1, h4, d1r, m15 = reads.get("H1", {}), reads.get("H4", {}), reads.get("D1", {}), reads.get("M15", {})

    def dir_tone(d):
        return "g" if d == "up" else "r" if d == "down" else "a"

    signals = [
        {"n": "HTF trend (H4)", "s": (h4.get("direction") or "n/a").title(), "c": dir_tone(h4.get("direction"))},
        {"n": "Daily trend (D1)", "s": (d1r.get("direction") or "n/a").title(), "c": dir_tone(d1r.get("direction"))},
        {"n": "Momentum (H1 MACD)", "s": h1.get("momentum") or "n/a",
         "c": "g" if (h1.get("momentum") or "").startswith("rising") else "r" if h1.get("momentum") else "a"},
        {"n": "RSI (H1)", "s": f"{h1.get('rsi')}" if h1.get("rsi") is not None else "n/a",
         "c": "a" if h1.get("rsi") and (h1["rsi"] > 70 or h1["rsi"] < 30) else "g"},
        {"n": "Volatility regime", "s": (h1.get("volatility") or "n/a").title(),
         "c": "a" if h1.get("volatility") in ("expanding", "compressed") else "g"},
        {"n": "VWAP position (M15)", "s": ("Above" if m15.get("vwap") and price > m15["vwap"] else "Below") if m15.get("vwap") else "n/a",
         "c": "g" if m15.get("vwap") and ((price > m15["vwap"]) == (bias != "Bearish")) else "a"},
        {"n": "Structure (H1)", "s": h1.get("structure") or "n/a",
         "c": "g" if h1.get("structure") in ("HH/HL", "LH/LL") else "a"},
        {"n": "News risk", "s": ("Clear" if not ev else f"{ev['currency']} in {ev['minutes']}m") if fundamentals.get("applicable") else "N/A (synthetic)",
         "c": "r" if ev else "g"},
    ]

    # --- reasoning chain ---------------------------------------------------
    chain = [
        {"t": "Regime scan", "d": f"{reg['primary']} · H1 ADX {h1.get('adx')} · volatility {h1.get('volatility')} "
                                    f"(ATR pct {h1.get('atr_pct')})", "state": "done"},
        {"t": "Trend alignment", "d": f"{bias} bias, {agree}/{total} timeframes agree (weighted score "
                                        f"{'n/a' if s is None else f'{s:+.2f}'})", "state": "done"},
        {"t": "Setup detection", "d": (f"{len(candidates)} setup(s): " + ", ".join(f"{c['setup_name']} {c['direction']}" for c in candidates))
                                      if candidates else "No setup currently meets its rules on " + exec_tf, "state": "done"},
        {"t": "Event & fundamental filter", "d": ev["text"] if ev else
            ("No high-impact event within 2h; tilt " + fundamentals.get("lean_text", "n/a")) if fundamentals.get("applicable")
            else "Synthetic - fundamentals not applicable", "state": "done"},
        {"t": "Calibration & ranking", "d": best["calibration"]["note"] if best else "Nothing to rank - waiting for price to reach a level",
         "state": "active" if best else "done"},
    ]

    # --- verdict -----------------------------------------------------------
    if best and best["confidence"] >= 55:
        verdict = (f"Actionable: {best['setup_name']} {best['direction']} — {best['grade']}-grade, "
                   f"{best['confidence']}% confidence, {best['rr1']}R to TP1.")
        tone = "g"
    elif best:
        verdict = f"Marginal: {best['setup_name']} {best['direction']} at {best['confidence']}% — below the 55% bar; watch only."
        tone = "a"
    else:
        near = min(key_levels, key=lambda lv: abs(lv["distance_atr"]), default=None)
        headline = (f"No setup · watching {fmt(near['price'], digits)} ({near['kind']}, {near['distance_atr']:+} ATR)"
                    if near else "No setup · no nearby levels")
        verdict = ("No trade: no setup on " + exec_tf + "." +
                   (f" Nearest level {fmt(near['price'], digits)} ({near['kind']}, {near['distance_atr']:+} ATR) is the one to watch." if near else ""))
        tone = "r"
    if best:
        headline = f"{best['setup_name']} {best['direction']} · {best['confidence']}%"
    opportunity = round(best["confidence"] if best else bias_conf * 0.45)

    expected_move = expected_rr = None
    if best:
        pts = best["tp1"] - price
        expected_move = f"{pts:+,.{digits}f} to {fmt(best['tp1'], digits)}"
        expected_rr = f"1 : {best['rr1']}"

    day_open = d1.c[-1]["open"] if d1 else None
    return {
        "code": meta["code"], "name": meta["name"], "kind": meta["kind"], "digits": digits,
        "generated_at": datetime.now(timezone.utc).isoformat(), "exec_tf": exec_tf,
        "price": price, "day_open": day_open,
        "day_change_pct": round((price / day_open - 1) * 100, 2) if day_open else None,
        "bias": bias, "bias_score": None if s is None else round(s, 2), "confidence": bias_conf,
        "alignment": f"{agree}/{total}", "regime": reg,
        "reads": {tf: {k: v for k, v in r.items() if k != "levels"} for tf, r in reads.items()},
        "key_levels": key_levels[:12], "prev_day": prev_day,
        "fundamentals": fundamentals, "candidates": candidates,
        "signals": signals, "chain": chain, "verdict": verdict, "headline": headline, "verdict_tone": tone,
        "opportunity": opportunity, "expected_move": expected_move, "expected_rr": expected_rr,
        "atr": fx.atr[i], "atr_pct": h1.get("atr_pct"), "adx": h1.get("adx"),
        "narrative": None,
    }


def decision_for(report: dict) -> dict:
    """One decision-log entry summarising the analysis in plain language."""
    best = report["candidates"][0] if report["candidates"] else None
    code = report["code"]
    if best and best["confidence"] >= 55:
        action = f"SETUP — {code} {best['direction']} ({best['setup_name']})"
        tone = "g"
        expl = (f"{best['levels_text']}. Why: {'; '.join(best['confluence'][:4])}."
                + (f" Against: {'; '.join(best['against'][:2])}." if best["against"] else "")
                + f" {best['calibration']['note']}. Invalidation: {best['invalidation']}")
        conf = best["confidence"]
    elif best:
        action = f"WATCH — {code} {best['direction']} ({best['setup_name']})"
        tone = "a"
        expl = (f"A setup formed but scored {best['confidence']}%, under the 55% bar. "
                f"Holding back because: {'; '.join(best['against'][:3]) or 'confluence is thin'}. {best['levels_text']}.")
        conf = best["confidence"]
    else:
        action = f"NO TRADE — {code} {report['bias'].lower()} bias"
        tone = "r"
        expl = f"{report['verdict']} Regime {report['regime']['primary']}, {report['alignment']} timeframes aligned."
        conf = report["confidence"]
    return {"action": action, "tone": tone, "explanation": expl, "confidence": conf}
