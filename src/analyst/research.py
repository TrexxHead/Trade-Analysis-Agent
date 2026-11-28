"""Kora's research engine: which strategies have actually worked on *your*
instruments, judged the way a sceptical quant would.

Non-negotiables (from the backtest-overfitting literature: Bailey & Lopez de
Prado; Park & Irwin on data snooping):
  1. Chronological split. Parameters, variant and timeframe are chosen on the
     first 60% of history only (in-sample). The last 40% is touched once.
  2. Costs. Every trade pays the spread.
  3. Enough trades: at least 20 in-sample and 25 out-of-sample.
  4. Multiple-testing haircut. Testing many strategies makes some look good
     by luck, so each out-of-sample p-value is Sidak-adjusted for the number
     of strategies tried on that instrument.
  5. Confidence = 1 - adjusted p, from a bootstrap of out-of-sample trades.
     A strategy is promoted to Kora only at >= 65% and a profit factor > 1.
Anything that passes is still re-tested every week on fresh data.
"""
from __future__ import annotations

import random
from datetime import datetime, timezone

from src.analyst import library as L
from src.analyst import rules as R

IS_FRAC = 0.6
MIN_IS, MIN_OOS = 20, 25
PROMOTE_AT = 0.65
TFS = ["M15", "H1"]
BARS = {"M15": 20000, "H1": 12000}

# Typical spreads in price units, used when the live broker spread isn't known.
DEFAULT_SPREAD = {"XAUUSD": 0.35, "XAGUSD": 0.035, "V75": 25.0, "BOOM1000": 0.35, "CRASH1000": 0.35,
                  "BOOM500": 0.3, "CRASH500": 0.3, "V100": 0.4, "EURUSD": 0.00012, "GBPUSD": 0.00015}


def spread_for(code: str, live: dict | None = None) -> float:
    q = (live or {}).get(code) or {}
    if q.get("bid") and q.get("ask") and q["ask"] > q["bid"]:
        return q["ask"] - q["bid"]
    return DEFAULT_SPREAD.get(code, 0.0)


def bootstrap_p(rs: list[float], n: int = 2000, seed: int = 7) -> float:
    """P(mean <= 0) under resampling of the trades: a one-sided p-value."""
    if len(rs) < 2:
        return 1.0
    rnd = random.Random(seed)
    m = len(rs)
    bad = 0
    for _ in range(n):
        s = 0.0
        for _ in range(m):
            s += rs[rnd.randrange(m)]
        if s <= 0:
            bad += 1
    return (bad + 1) / (n + 1)


def sidak(p: float, m: int) -> float:
    return 1 - (1 - p) ** max(1, m)


def _params(pr: dict, vi: int, tf: str) -> dict:
    import copy
    out = copy.deepcopy(pr["params"])
    out.update(copy.deepcopy(pr["variants"][vi]) if vi < len(pr["variants"]) else {})
    out["entry_tf"] = tf
    return out


def user_presets(settings: dict, code: str) -> list[dict]:
    """Your saved strategies for this instrument, as research entries (one
    variant, their own timeframe - nothing is tuned)."""
    from src.analyst import strategies as ST
    out = []
    for st in ST.saved(settings):
        on = (ST.active_for(settings, code) or {}).get("id") == st["id"]
        if not on and code not in (st.get("instruments") or []):
            continue
        params = ST.to_rules(st)
        out.append({"id": f"my:{st['id']}", "name": f"{st['name']} (yours)", "family": "Your strategies",
                    "evidence": "Your own rules, tested exactly as saved.", "params": params, "variants": [{}],
                    "tf_only": [params.get("entry_tf", "M15")], "mine": True})
    return out


def evaluate(code: str, meta: dict, history: dict[str, list[dict]], cost: float, presets: list[dict] | None = None,
             progress=None) -> list[dict]:
    """history: {tf: candles}. One row per preset."""
    rows = []
    for pr in presets or L.PRESETS:
        best = None
        tried = 0
        for tf in (pr.get("tf_only") or TFS):
            c = history.get(tf) or []
            if len(c) < 1500:
                continue
            cut = int(len(c) * IS_FRAC)
            for vi in range(len(pr["variants"])):
                params = _params(pr, vi, tf)
                ex = R.norm(params)["exit"]
                g = R.run(c, params, meta, exits=[ex], cost=cost, start=210, end=cut)[0]
                tried += 1
                st = g["all"]
                if st["n"] >= MIN_IS and (best is None or st["expectancy"] > best["is"]["expectancy"]):
                    best = {"tf": tf, "variant": vi, "is": st, "cut": cut, "params": params}
        row = {"code": code, "preset": pr["id"], "name": pr["name"], "family": pr["family"], "evidence": pr["evidence"],
               "trials": tried, "mine": bool(pr.get("mine"))}
        if not best:
            row.update(status="no_data", note="Not enough trades in-sample to judge")
        elif best["is"]["expectancy"] <= 0:
            row.update(status="rejected_is", tf=best["tf"], variant=best["variant"], is_stats=best["is"],
                       note=f"Lost money in-sample ({best['is']['expectancy']:+.2f}R per trade), so it was never tested out of sample")
            c = history[best["tf"]]
            g = R.run(c, best["params"], meta, exits=[R.norm(best["params"])["exit"]], cost=cost, start=best["cut"])[0]
            row["_oos"] = [(t["time"][:10], t["r"]) for t in g["trades"]]
        else:
            c = history[best["tf"]]
            g = R.run(c, best["params"], meta, exits=[R.norm(best["params"])["exit"]], cost=cost, start=best["cut"])[0]
            rs = [t["r"] for t in g["trades"]]
            row["_oos"] = [(t["time"][:10], t["r"]) for t in g["trades"]]
            half = len(rs) // 2
            row.update(status="tested", tf=best["tf"], variant=best["variant"], is_stats=best["is"], oos_stats=g["all"],
                       oos_halves=[R.stats(rs[:half])["expectancy"], R.stats(rs[half:])["expectancy"]],
                       p_raw=round(bootstrap_p(rs), 4), oos_from=c[best["cut"]]["time"], oos_to=c[-1]["time"],
                       curve=R._curve(rs, 80))
        rows.append(row)
        if progress:
            progress(code, pr["id"])
    # multiple-testing haircut across the strategies that reached the out-of-sample stage
    tested = [r for r in rows if r["status"] == "tested"]
    m = max(1, len(tested))
    for r in tested:
        p_adj = sidak(r["p_raw"], m)
        o = r["oos_stats"]
        r["confidence"] = round((1 - p_adj) * 100, 1)
        enough = o["n"] >= MIN_OOS
        r["promoted"] = bool(enough and (1 - p_adj) >= PROMOTE_AT and (o["pf"] or 0) > 1 and o["expectancy"] > 0)
        if not enough:
            r["note"] = f"Only {o['n']} out-of-sample trades - not enough to trust"
        elif r["promoted"]:
            r["note"] = (f"Passed: {o['expectancy']:+.2f}R per trade on unseen data ({o['n']} trades, PF {o['pf']}), "
                         f"{r['confidence']:.0f}% confidence after adjusting for {m} strategies tested")
        elif o["expectancy"] <= 0:
            r["note"] = f"Worked in-sample, then lost on unseen data ({o['expectancy']:+.2f}R): likely overfit or regime change"
        else:
            r["note"] = (f"Positive on unseen data ({o['expectancy']:+.2f}R, {o['n']} trades) but only {r['confidence']:.0f}% "
                         f"confidence after the multiple-testing haircut - watch, don't trust yet")
    families = family_tests(rows)
    for r in rows:
        r.setdefault("promoted", False)
        r.setdefault("confidence", None)
        f = families.get(r["family"])
        if f and f["promoted"] and not r["promoted"] and r["status"] == "tested" and \
                r["oos_stats"]["expectancy"] > 0 and r["oos_stats"]["n"] >= 15:
            r["promoted"] = True
            r["via_family"] = True
            r["confidence"] = f["confidence"]
            r["note"] = (f"Passed as part of the {r['family'].lower()} family: the family's combined out-of-sample portfolio "
                         f"scored {f['confidence']:.0f}% confidence ({f['expectancy']:+.2f}R per active day). On its own: "
                         f"{r['oos_stats']['expectancy']:+.2f}R over {r['oos_stats']['n']} unseen trades.")
        r.pop("_oos", None)
    fam = families
    rows.sort(key=lambda r: (-(r["promoted"]), -(r["confidence"] or -1), -((r.get("oos_stats") or {}).get("expectancy") or -9)))
    return rows, fam


def family_tests(rows: list[dict]) -> dict:
    """Related strategies (e.g. all trend followers) tested as one equal-weight
    portfolio. Trades are summed per day, so overlapping trades on the same
    move count once, then the daily series is bootstrapped. Every member is
    included whatever its in-sample result (no cherry-picking), and the
    p-value is Sidak-adjusted for the number of families."""
    fams: dict = {}
    for r in rows:
        if "_oos" in r and not r.get("mine"):
            fams.setdefault(r["family"], []).append(r)
    m = max(1, len(fams))
    out = {}
    for fam, members in fams.items():
        k = len(members)
        days: dict = {}
        for r in members:
            for d, x in r["_oos"]:
                days[d] = days.get(d, 0.0) + x / k
        series = [days[d] for d in sorted(days)]
        if len(series) < 30:
            out[fam] = {"members": k, "days": len(series), "promoted": False, "confidence": None, "expectancy": None,
                        "note": "Too few active days to judge"}
            continue
        p = sidak(bootstrap_p(series), m)
        exp = sum(series) / len(series)
        pos = sum(1 for r in members if sum(x for _, x in r["_oos"]) > 0)
        conf = round((1 - p) * 100, 1)
        out[fam] = {"members": k, "positive_members": pos, "days": len(series), "expectancy": round(exp, 3),
                    "confidence": conf, "promoted": bool(conf >= PROMOTE_AT * 100 and exp > 0),
                    "curve": R._curve(series, 80)}
    return out


def run_all(codes: list[str], fetch, cost_fn, presets=None, progress=None, settings: dict | None = None) -> dict:
    """fetch(meta, tf, bars) -> candles; cost_fn(code) -> spread."""
    from src.analyst.config import by_code
    cat = by_code()
    out = {"ran_at": datetime.now(timezone.utc).isoformat(), "instruments": {}, "errors": {},
           "method": {"is_frac": IS_FRAC, "min_is": MIN_IS, "min_oos": MIN_OOS, "promote_at": PROMOTE_AT, "tfs": TFS}}
    for code in codes:
        meta = cat.get(code)
        if not meta:
            continue
        try:
            mine = user_presets(settings, code) if settings else []
            tfs = sorted(set(TFS) | {tf for pr in mine for tf in pr["tf_only"]})
            hist = {tf: fetch(meta, tf, BARS.get(tf, 20000)) for tf in tfs}
            rows, fam = evaluate(code, meta, hist, cost_fn(code), (presets or L.PRESETS) + mine, progress)
            out["instruments"][code] = {"rows": rows, "families": fam,
                                        "bars": {tf: len(v) for tf, v in hist.items()},
                                        "span": {tf: [v[0]["time"], v[-1]["time"]] if v else None for tf, v in hist.items()},
                                        "cost": cost_fn(code)}
        except Exception as e:
            out["errors"][code] = str(e)
    return out


def promoted(store: dict | None, code: str | None = None) -> list[dict]:
    rows = []
    for c, d in ((store or {}).get("instruments") or {}).items():
        if code and c != code:
            continue
        rows += [r for r in d.get("rows", []) if r.get("promoted")]
    return rows


def research_candidates(candles_by_tf: dict, code: str, meta: dict, store: dict) -> list[dict]:
    """Live plans from strategies that passed research for this instrument."""
    from src.analyst.calibration import calibrate_confidence
    out = []
    for r in promoted(store, code):
        if r.get("mine"):
            continue  # your own strategies run through Kora/Mine/Both instead
        c = candles_by_tf.get(r["tf"]) or []
        if len(c) < 260:
            continue
        params = L.preset_params(r["preset"], r["variant"], r["tf"])
        s = R.live_signal(c, params, meta)
        if not s:
            continue
        p = R.norm(params)
        sign, entry, risk = s["sign"], s["entry"], s["risk"]
        o = r["oos_stats"]
        cal = calibrate_confidence(62, float(p.get("tp_r", 1.5)), {"n": o["n"], "wins": o["wins"], "expectancy": o["expectancy"],
                                                                   "avg_r": o["expectancy"]})
        tp_r = float(p.get("tp_r", 1.5)) if p["exit"] == "fixed_r" else 1.5
        out.append({
            "setup": f"res:{r['preset']}", "setup_name": f"Research · {r['name']}", "source": "research",
            "direction": s["direction"], "entry_type": "market", "entry": entry, "stop": s["stop"],
            "tp1": entry + sign * risk * min(tp_r, 1.5), "tp2": entry + sign * risk * max(2.0, tp_r),
            "rr1": round(min(tp_r, 1.5), 2), "rr2": round(max(2.0, tp_r), 2), "risk_atr": None, "score": 62,
            "confluence": [f"Passed Kora's out-of-sample research on {code} {r['tf']}: {o['expectancy']:+.2f}R per trade over {o['n']} unseen trades",
                           f"{r['confidence']:.0f}% confidence after costs and the multiple-testing haircut"] +
                          [R.describe(x) for x in (p["long"] if sign > 0 else p["short"])][:3],
            "against": [] if (r.get("oos_halves") or [1, 1])[1] and (r.get("oos_halves") or [1, 1])[1] > 0 else
                       ["Most recent half of the test period was weaker"],
            "trigger": "Market entry on the signal candle's close",
            "invalidation": "The stop, or the strategy's exit rule",
            "exit_rule": R.exit_name(p["exit"], p), "calibration": cal, "confidence": cal["confidence"],
            "timeframe": r["tf"], "rule_params": p, "signal_time": c[s["i"]]["time"],
        })
    return out


# What the published evidence says, cross-referenced (see docs/research.md for the full notes).
EVIDENCE = {
    "XAUUSD": {
        "verdict": "Trend-following has the strongest outside evidence on gold, but mainly over weeks to months. Intraday, only longer-parameter moving-average rules showed some predictive power, and mean returns by session are not reliably different.",
        "points": [
            ("Time-series momentum is profitable across commodity futures including gold since 1985 (monthly horizon).", "Moskowitz, Ooi & Pedersen (2012), Journal of Financial Economics", "https://fairmodel.econ.yale.edu/ec439/hurst.pdf"),
            ("Intraday MA rules: no predictive power at standard settings; some with longer parameters in gold.", "Batten et al. (2018), J. Int. Financial Markets, Institutions & Money", "https://eprints.soton.ac.uk/411993/"),
            ("Gold's largest hourly ranges cluster around the US open (16:00 GMT) and the London morning - volatility, not direction.", "Multi-year XAUUSD H1 range study (practitioner)", "https://www.mql5.com/en/blogs/post/773690"),
            ("Testing many rules inflates the best backtest; correct for the number of trials.", "Bailey & Lopez de Prado, Deflated Sharpe Ratio (2014)", "https://papers.ssrn.com/abstract=2460551"),
        ]},
    "XAGUSD": {
        "verdict": "Silver shows no reliable intraday trend predictability in the academic work; long-run gold/silver ratio mean reversion is documented by an industry study. Short-term mean reversion is the more plausible family - and it is what Kora's out-of-sample test found.",
        "points": [
            ("Intraday technical (MA) rules offer no significant predictability in silver.", "Batten et al. (2018)", "https://eprints.soton.ac.uk/411993/"),
            ("The gold/silver ratio is not a random walk and tends to revert after ~20% deviations (1970-2026).", "Silver Institute (industry-commissioned, treat with care)", "https://silverinstitute.org/wp-content/uploads/2026/07/Is-the-Gold-Silver-Ratio-Relevant-Today.pdf"),
            ("5-minute gold & silver: profitable high-frequency technical trading is 'possible but not easy' after controlling for data snooping.", "Urquhart, Batten, Lucey et al. (2015)", "https://www.cxoadvisory.com/technical-trading/high-frequency-technical-trading-of-gold-and-silver/"),
        ]},
    "SYNTHETIC": {
        "verdict": "Volatility, Boom and Crash indices are generated by Deriv's random-number algorithm with fixed volatility / average spike frequency. There is no economic reason for a lasting edge, so Kora treats any backtest profit on them as luck until it survives unseen data - and so far none has. Size small, trade the plan, and expect variance.",
        "points": [
            ("Volatility 75's volatility is fixed at 75% by algorithm; it is not linked to any real market.", "Deriv", "https://blog.deriv.com/blog/what-is-volatility-75-index-how-to-trade"),
            ("Boom/Crash 1000: a spike on average every 1,000 ticks - an average, not a schedule.", "Deriv", "https://deriv.com/derived-indices/crash-boom-indices"),
            ("Generator and per-tick spike odds are not published by Deriv; third-party descriptions are unverified.", "Deriv / FXNX (unverified)", "https://fxnx.com/en/blog/boom-crash-spike-sizes-90-day-distribution-study"),
        ]},
}


def evidence_for(code: str) -> dict:
    if code in EVIDENCE:
        return EVIDENCE[code]
    if code.startswith(("BOOM", "CRASH", "V", "JUMP", "STEP")):
        return EVIDENCE["SYNTHETIC"]
    return {"verdict": "No instrument-specific research notes yet.", "points": []}
