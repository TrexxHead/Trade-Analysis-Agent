"""Indicator library, rule engine and research engine."""
import math
import random
from datetime import datetime, timedelta, timezone

from src.analyst import indicators as IND
from src.analyst import library as L
from src.analyst import research as RS
from src.analyst import rules as R


def candles(n=1200, seed=1, drift=0.0):
    rnd = random.Random(seed)
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    px, out = 100.0, []
    for i in range(n):
        o = px
        px = max(1, px * (1 + drift + rnd.gauss(0, 0.004)))
        hi, lo = max(o, px) * (1 + abs(rnd.gauss(0, 0.001))), min(o, px) * (1 - abs(rnd.gauss(0, 0.001)))
        out.append({"time": (t0 + timedelta(minutes=15 * i)).isoformat(), "open": o, "high": hi, "low": lo, "close": px, "volume": 1})
    return out


def test_every_indicator_is_causal_and_sized():
    c = candles(400)
    for key in IND.REGISTRY:
        full = IND.compute(key, c)
        cut = IND.compute(key, c[:300])
        for out, series in full.items():
            assert len(series) == len(c), (key, out)
            # value at bar 299 must not change when the future is removed (no lookahead)
            a, b = series[299], cut[out][299]
            assert (a is None and b is None) or math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9), (key, out, a, b)


def test_known_values():
    c = [{"time": f"2026-01-01T{h:02d}:00:00+00:00", "open": v, "high": v, "low": v, "close": v} for h, v in enumerate([1, 2, 3, 4, 5] * 4)]
    assert IND.compute("sma", c, {"period": 5})["value"][4] == 3
    d = IND.compute("donchian", c, {"period": 3})
    assert d["upper"][3] == 3 and d["lower"][3] == 1          # previous 3 candles only
    r = IND.compute("rsi", [{"close": float(i), "high": i, "low": i, "open": i, "time": "2026-01-01T00:00:00+00:00"} for i in range(30)], {"period": 14})
    assert r["value"][-1] == 100                              # only gains


def test_mirror_rules():
    m = R.mirror({"a": {"ind": "rsi", "params": {"period": 2}}, "op": "<", "b": {"value": 10}})
    assert m["op"] == ">" and m["b"]["value"] == 90
    m = R.mirror({"a": {"price": "low"}, "op": "<", "b": {"ind": "ema", "params": {"period": 20}}})
    assert m["a"]["price"] == "high" and m["op"] == ">"
    adx = {"a": {"ind": "adx", "params": {"period": 14}, "out": "adx"}, "op": ">", "b": {"value": 25}}
    assert R.mirror(adx) == adx                               # trend strength isn't directional
    m = R.mirror({"a": {"price": "close"}, "op": "crosses_above", "b": {"ind": "donchian", "out": "upper", "params": {"period": 20}}})
    assert m["op"] == "crosses_below" and m["b"]["out"] == "lower"


def test_rule_backtest_mechanics_and_costs():
    c = candles(1500, seed=4)
    params = {"entry_tf": "M15", "mirror": True, "long": [{"a": {"ind": "ema", "params": {"period": 10}}, "op": "crosses_above",
                                                             "b": {"ind": "ema", "params": {"period": 30}}}],
              "stop": {"type": "atr", "mult": 1.5}, "exit": "fixed_r", "tp_r": 2.0}
    free = R.run(c, params, {"code": "X"}, exits=["fixed_r"])[0]
    paid = R.run(c, params, {"code": "X"}, exits=["fixed_r"], cost=0.05)[0]
    assert free["all"]["n"] > 10 and free["all"]["n"] == paid["all"]["n"]
    assert paid["all"]["total_r"] < free["all"]["total_r"]   # spread is charged
    for t in free["trades"]:
        assert t["r"] >= -1.6 and t["r"] <= 2.0 + 1e-9         # stop/target bounded (gaps aside)
    # trades never overlap within one exit style
    ends = [(t["i"], t["i"] + t["bars"]) for t in free["trades"]]
    assert all(b[0] > a[1] for a, b in zip(ends, ends[1:]))
    bt = R.backtest(c, params, {"code": "X"})
    assert {g["exit"] for g in bt["grid"]} >= {"fixed_r", "fixed_1r", "ema_trail", "atr_trail", "opposite"}


def test_library_presets_all_run():
    c = candles(1500, seed=7)
    for p in L.PRESETS:
        for v in range(len(p["variants"])):
            g = R.run(c, L.preset_params(p["id"], v, "M15"), {"code": "X"}, exits=[R.norm(L.preset_params(p["id"], v, "M15"))["exit"]])
            assert g and "all" in g[0], p["id"]


def test_research_rejects_noise():
    """On a pure random walk, nothing should be promoted (that's the point of the haircut)."""
    hist = {"M15": candles(6000, seed=11), "H1": candles(3000, seed=12)}
    rows, fam = RS.evaluate("X", {"code": "X"}, hist, cost=0.01)
    assert not any(r["promoted"] for r in rows)
    assert all(r["status"] in ("tested", "rejected_is", "no_data") for r in rows)
    assert 0.0 <= RS.bootstrap_p([1, 2, 3]) < 0.05 and RS.bootstrap_p([-1, -2, -1]) > 0.95
    assert math.isclose(RS.sidak(0.05, 1), 0.05) and RS.sidak(0.05, 10) > 0.4


def test_ema_stoch_translates_exactly():
    from src.analyst import strategies as ST
    c = candles(3000, seed=21)
    st = ST.DEFAULT_STRATEGY
    meta = {"code": "CRASH1000"}
    a = next(g for g in ST.backtest(c, st["params"], meta)["grid"] if g["htf"] == "none" and g["exit"] == "fixed_1r")
    rp = {**ST.to_rules(st), "exit": "fixed_1r"}
    b = R.run(c, rp, meta, exits=["fixed_1r"], start=ST.DEFAULT_PARAMS["slow"] + ST.DEFAULT_PARAMS["zone_lookback"] // 2)[0]
    assert a["all"]["n"] == b["all"]["n"] and a["all"]["total_r"] == b["all"]["total_r"]
