"""Library of well-known strategies, written in the builder's rule format.

These are the methods with a public track record or a long practitioner
history. None of them is assumed to work on your instruments: the research
engine (research.py) tests each one out of sample and only promotes what
clears the bar. Open any of them in the builder to read or change the rules.
"""
from __future__ import annotations


def ind(key, out=None, shift=0, **params):
    op = {"ind": key, "params": params}
    if out:
        op["out"] = out
    if shift:
        op["shift"] = shift
    return op


def px(field="close", shift=0):
    return {"price": field, **({"shift": shift} if shift else {})}


def v(x):
    return {"value": x}


def c(a, op, b, within=1):
    d = {"a": a, "op": op, "b": b}
    if within > 1:
        d["within"] = within
    return d


PRESETS: list[dict] = [
    {
        "id": "ema_trend", "name": "EMA trend cross", "family": "Trend following",
        "evidence": "Time-series momentum (Moskowitz, Ooi & Pedersen 2012) is one of the best-documented effects in futures, gold included; strongest on longer horizons.",
        "params": {"long": [c(ind("ema", period=20), "crosses_above", ind("ema", period=50)), c(px(), ">", ind("ema", period=200))],
                   "mirror": True, "stop": {"type": "atr", "mult": 2.0}, "exit": "atr_trail", "trail_atr": 3.0},
        "variants": [{}, {"long": [c(ind("ema", period=50), "crosses_above", ind("ema", period=200))]}],
    },
    {
        "id": "donchian_breakout", "name": "Donchian breakout (Turtle)", "family": "Breakout",
        "evidence": "The Turtle channel breakout: the classic trend-following entry, long used by CTAs on metals.",
        "params": {"long": [c(px(), "crosses_above", ind("donchian", "upper", period=20))], "mirror": True,
                   "stop": {"type": "atr", "mult": 2.0}, "exit": "atr_trail", "trail_atr": 3.0},
        "variants": [{}, {"long": [c(px(), "crosses_above", ind("donchian", "upper", period=55))]}],
    },
    {
        "id": "supertrend", "name": "Supertrend flip", "family": "Trend following",
        "evidence": "ATR-band trend filter popular with practitioners; a volatility-adjusted version of trend following.",
        "params": {"long": [c(ind("supertrend", "dir", period=10, mult=3.0), "crosses_above", v(0))], "mirror": True,
                   "stop": {"type": "atr", "mult": 2.0}, "exit": "opposite"},
        "variants": [{}, {"long": [c(ind("supertrend", "dir", period=10, mult=2.0), "crosses_above", v(0))]}],
    },
    {
        "id": "rsi2", "name": "RSI(2) pullback in trend (Connors)", "family": "Mean reversion",
        "evidence": "Connors & Alvarez (2008): buy short-term oversold dips inside a long-term uptrend; well documented on equity indices, less so on metals.",
        "params": {"long": [c(px(), ">", ind("ema", period=200)), c(ind("rsi", period=2), "<", v(10))],
                   "exit_long": [c(px(), ">", ind("sma", period=5))], "mirror": True,
                   "stop": {"type": "atr", "mult": 2.5}, "exit": "cond", "max_hold": 20},
        "variants": [{}, {"long": [c(px(), ">", ind("ema", period=200)), c(ind("rsi", period=2), "<", v(5))]}],
    },
    {
        "id": "bb_reversion", "name": "Bollinger band fade", "family": "Mean reversion",
        "evidence": "Fading moves outside 2 standard deviations back to the mean; works in ranges, fails in trends - hence the ADX < 20 filter.",
        "params": {"long": [c(px(), "<", ind("bb", "lower", period=20, mult=2.0)), c(ind("adx", "adx", period=14), "<", v(20))],
                   "exit_long": [c(px(), ">", ind("bb", "mid", period=20, mult=2.0))], "mirror": True,
                   "stop": {"type": "atr", "mult": 1.5}, "exit": "cond", "max_hold": 30},
        "variants": [{}, {"long": [c(px(), "<", ind("bb", "lower", period=20, mult=2.5)), c(ind("adx", "adx", period=14), "<", v(25))]}],
    },
    {
        "id": "squeeze", "name": "Volatility squeeze breakout", "family": "Breakout",
        "evidence": "Bollinger inside Keltner = compressed volatility; expansion tends to follow (volatility clustering is well documented).",
        "params": {"long": [c(ind("bb", "upper", period=20, mult=2.0), "<", ind("keltner", "upper", period=20, atr=10, mult=1.5), within=6),
                            c(px(), "crosses_above", ind("bb", "upper", period=20, mult=2.0))], "mirror": True,
                   "stop": {"type": "atr", "mult": 1.5}, "exit": "atr_trail", "trail_atr": 2.5},
        "variants": [{}, {"exit": "fixed_2r"}],
    },
    {
        "id": "macd_trend", "name": "MACD cross with 200 EMA", "family": "Trend following",
        "evidence": "Classic momentum confirmation: MACD crosses its signal below zero, in the direction of the 200 EMA.",
        "params": {"long": [c(ind("macd", "macd", fast=12, slow=26, signal=9), "crosses_above", ind("macd", "signal", fast=12, slow=26, signal=9)),
                            c(ind("macd", "macd", fast=12, slow=26, signal=9), "<", v(0)), c(px(), ">", ind("ema", period=200))],
                   "mirror": True, "stop": {"type": "atr", "mult": 2.0}, "exit": "fixed_r", "tp_r": 2.0},
        "variants": [{}, {"exit": "atr_trail", "trail_atr": 3.0}],
    },
    {
        "id": "adx_pullback", "name": "ADX trend pullback", "family": "Trend following",
        "evidence": "Buy dips to the 20 EMA only when ADX confirms a trend: a common professional refinement of trend following.",
        "params": {"long": [c(ind("adx", "adx", period=14), ">", v(25)), c(ind("adx", "plus", period=14), ">", ind("adx", "minus", period=14)),
                            c(px("low"), "<", ind("ema", period=20)), c(px(), ">", ind("ema", period=20)), c(px(), ">", ind("ema", period=50))],
                   "mirror": True, "stop": {"type": "swing", "bars": 8}, "exit": "fixed_r", "tp_r": 2.0},
        "variants": [{}, {"exit": "ema_trail", "trail": 20}],
    },
    {
        "id": "us_open_breakout", "name": "US-session range breakout", "family": "Session",
        "evidence": "Gold's biggest hourly ranges come around the US cash open (13:30-16:00 UTC), per multi-year H1 range studies; breakouts need volatility.",
        "params": {"long": [c(px(), "crosses_above", ind("donchian", "upper", period=12))], "mirror": True, "session": [13, 17],
                   "stop": {"type": "atr", "mult": 1.5}, "exit": "fixed_r", "tp_r": 1.5, "max_hold": 24},
        "variants": [{}, {"session": [7, 11]}],
        "tf_only": ["M15"],
    },
    {
        "id": "ichimoku", "name": "Ichimoku cloud trend", "family": "Trend following",
        "evidence": "Price above the cloud with a Kijun cross: a complete trend system used widely on gold and FX.",
        "params": {"long": [c(px(), "crosses_above", ind("ichimoku", "kijun", tenkan=9, kijun=26, senkou=52)),
                            c(px(), ">", ind("ichimoku", "span_a", tenkan=9, kijun=26, senkou=52)),
                            c(px(), ">", ind("ichimoku", "span_b", tenkan=9, kijun=26, senkou=52))],
                   "mirror": True, "stop": {"type": "atr", "mult": 2.0}, "exit": "opposite"},
        "variants": [{}, {"exit": "atr_trail", "trail_atr": 3.0}],
    },
    {
        "id": "stoch_trend", "name": "Stochastic dip in trend", "family": "Mean reversion",
        "evidence": "Stochastic crossing up through 20 while above the 200 EMA: buying pullbacks in an established trend.",
        "params": {"long": [c(ind("stoch", "k", k=14, d=3, smooth=3), "crosses_above", v(20)), c(px(), ">", ind("ema", period=200))],
                   "mirror": True, "stop": {"type": "swing", "bars": 10}, "exit": "fixed_r", "tp_r": 1.5},
        "variants": [{}, {"exit": "ema_trail", "trail": 20}],
    },
]

BY_ID = {p["id"]: p for p in PRESETS}


def preset_params(pid: str, variant: int = 0, tf: str = "M15") -> dict:
    import copy
    p = BY_ID[pid]
    out = copy.deepcopy(p["params"])
    out.update(copy.deepcopy(p["variants"][variant]) if variant < len(p["variants"]) else {})
    out["entry_tf"] = tf
    return out


def as_strategy(pid: str, variant: int = 0, tf: str = "M15", instruments: list | None = None, sid: str | None = None) -> dict:
    p = BY_ID[pid]
    return {"id": sid or f"lib_{pid}_{variant}_{tf.lower()}", "name": f"{p['name']} · {tf}", "template": "rules",
            "instruments": instruments or [], "params": preset_params(pid, variant, tf),
            "notes": f"{p['family']}. {p['evidence']}", "source": "library", "preset": pid}
