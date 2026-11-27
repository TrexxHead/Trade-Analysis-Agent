"""Core checks for Kora's analyst. Run: python -m pytest tests -q"""
from datetime import datetime, timedelta, timezone

import pytest

from src.analyst import ta
from src.analyst.calibration import calibrate_confidence, simulate, train_instrument
from src.analyst.config import by_code, load_watchlist
from src.analyst.engine import analyse_instrument, decision_for
from src.analyst.fundamentals import expectation, instrument_fundamentals, sessions
from src.analyst.technical import Features, read_at
from src.live.sim import gen_candles


def bars(closes, start=datetime(2026, 1, 5, tzinfo=timezone.utc)):
    return [{"time": (start + timedelta(hours=i)).isoformat(), "open": c, "high": c + 1, "low": c - 1, "close": c}
            for i, c in enumerate(closes)]


def test_indicators_are_full_length_and_causal():
    closes = [100 + i * 0.5 for i in range(300)]
    c = bars(closes)
    for series in (ta.ema(closes, 20), ta.rsi(closes, 14), ta.atr(c, 14), ta.adx(c, 14)[0]):
        assert len(series) == len(closes)
    # value at i must not change when future bars are appended (no lookahead)
    short, long = Features(c[:200]), Features(c)
    assert short.ema50[150] == pytest.approx(long.ema50[150])
    assert short.rsi[150] == pytest.approx(long.rsi[150])
    assert all(s["confirmed"] <= 199 for s in short.swings)


def test_trend_read_on_a_clean_uptrend():
    f = Features(bars([100 + i for i in range(260)]))
    r = read_at(f, f.n - 1, "H1")
    assert r["direction"] == "up" and r["trend_score"] > 0.5


def test_simulate_long_hits_target_and_stop():
    base = bars([100] * 5)
    win = base + bars([101, 103, 106], start=datetime(2026, 1, 5, 5, tzinfo=timezone.utc))
    plan = {"direction": "long", "entry_type": "market", "entry": 100, "stop": 98, "tp1": 104}
    assert simulate(win, 4, plan) == pytest.approx(2.0)
    lose = base + bars([99, 96], start=datetime(2026, 1, 5, 5, tzinfo=timezone.utc))
    assert simulate(lose, 4, plan) == -1.0


def test_limit_fill_bar_never_counts_target():
    c = bars([100] * 5) + [{"time": "2026-01-05T05:00:00+00:00", "open": 103, "high": 106, "low": 99.5, "close": 100}]
    plan = {"direction": "long", "entry_type": "limit", "entry": 100, "stop": 98, "tp1": 104}
    assert simulate(c, 4, plan) != pytest.approx(2.0)  # fill bar printed TP too, but order is unknowable


def test_calibration_shrinks_small_samples():
    lucky = calibrate_confidence(60, 1.5, {"n": 5, "wins": 5, "expectancy": 1.5})
    solid = calibrate_confidence(60, 1.5, {"n": 200, "wins": 120, "expectancy": 0.5})
    assert lucky["confidence"] < solid["confidence"]
    assert calibrate_confidence(60, 1.5, None)["n"] == 0


def test_engine_end_to_end_on_sim_data():
    wl = load_watchlist()
    for code in ("XAUUSD", "V75", "EURUSD", "BOOM500"):
        meta = by_code()[code]
        candles = {tf: gen_candles(code, tf, n) for tf, n in wl["timeframes"].items()}
        rep = analyse_instrument(meta, candles, "H1", instrument_fundamentals(meta, [], {}), {})
        assert rep["bias"] in ("Bullish", "Bearish", "Neutral")
        assert set(rep["reads"]) == {"D1", "H4", "H1", "M15"}
        for c in rep["candidates"]:
            sign = 1 if c["direction"] == "long" else -1
            assert sign * (c["tp1"] - c["entry"]) > 0 and sign * (c["entry"] - c["stop"]) > 0
            assert 5 <= c["confidence"] <= 95
        d = decision_for(rep)
        assert d["tone"] in "gar" and d["explanation"]


def test_training_runs_and_reports_per_setup():
    rows = train_instrument(gen_candles("XAUUSD", "H1", 1500), by_code()["XAUUSD"])
    assert rows and all(r["n"] > 0 and 0 <= r["wins"] <= r["n"] for r in rows)


def test_fundamentals_rules():
    assert expectation({"title": "CPI y/y", "currency": "USD", "forecast": "3.1%", "previous": "2.9%"})["lean"] == 1
    assert expectation({"title": "Unemployment Rate", "currency": "USD", "forecast": "4.3%", "previous": "4.1%"})["lean"] == -1
    soon = (datetime.now(timezone.utc) + timedelta(minutes=45)).isoformat()
    cal = [{"title": "Non-Farm Employment Change", "currency": "USD", "time": soon, "impact": "High",
            "forecast": "180K", "previous": "150K"}]
    f = instrument_fundamentals(by_code()["XAUUSD"], cal, {})
    assert f["event_risk"] and f["lean"] < 0  # strong USD jobs expected -> headwind for gold
    assert not instrument_fundamentals(by_code()["V75"], cal, {})["applicable"]
    assert len(sessions()) == 4


def test_resolve_plan_forward():
    from src.analyst.calibration import resolve_plan
    t0 = "2026-01-05T04:00:00+00:00"
    c = bars([100, 101, 103, 105], start=datetime(2026, 1, 5, 5, tzinfo=timezone.utc))
    plan = {"direction": "long", "entry_type": "market", "entry": 100, "stop": 98, "tp1": 104}
    assert resolve_plan(c, t0, plan) == ("won", 2.0)
    assert resolve_plan(c[:1], t0, plan) is None  # still live
    lim = {**plan, "entry_type": "limit", "entry": 90, "stop": 88}
    assert resolve_plan(bars([100] * 6, start=datetime(2026, 1, 5, 5, tzinfo=timezone.utc)), t0, lim) == ("not_filled", None)
