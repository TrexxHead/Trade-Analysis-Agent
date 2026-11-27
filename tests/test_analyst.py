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


def test_brief_schedule_and_text(tmp_path):
    from src.analyst import briefing
    from src.store.db import kora_connection, kv_set
    conn = kora_connection(tmp_path / "t.db")
    kv_set(conn, "settings", {"brief": {"enabled": True, "times": ["07:00"], "weekdays_only": False}})
    at = datetime(2026, 11, 27, 7, 10).astimezone()
    assert briefing.due(conn, at) == "07:00"
    assert briefing.due(conn, at.replace(hour=8)) is None  # outside the 30-minute window
    briefing.mark_sent(conn, "07:00", at)
    assert briefing.due(conn, at) is None  # only once a day
    text = briefing.build(conn, at)
    assert text.startswith("KORA · Morning brief") and "TODAY'S FOCUS" in text and "APPROACHES FOR TODAY" in text


def test_sizing_small_account_and_rating():
    from src.analyst.sizing import rating, size_plan
    boom = {"code": "BOOM1000", "entry": 15800.0, "stop": 15780.0, "rr1": 1.5, "confidence": 70,
            "calibration": {"expectancy": 0.2}}
    spec = {"tick_size": 0.001, "tick_value": 0.001, "volume_min": 0.2, "volume_step": 0.01}
    tiny = size_plan(boom, spec, equity=1.51, risk_pct=1.0)
    assert tiny["lots"] == 0.2 and tiny["tradable"] is False and "Minimum lot" in tiny["reason"]  # 0.2 lots x 20pts = $4 > equity
    ok = size_plan(boom, spec, equity=1000, risk_pct=1.0, free_margin=900, margin_per_lot=16.0)
    assert ok["tradable"] and ok["lots"] == 0.5 and abs(ok["risk_usd"] - 10) < 1e-9 and ok["margin"] == 8.0
    assert size_plan(boom, None, 1000)["tradable"] is None
    assert 7 <= rating(boom) <= 8


def test_paper_engine_fills_and_exits(tmp_path, monkeypatch):
    import sqlite3
    from src.analyst import paper
    from src.store.db import kora_connection
    conn = kora_connection(tmp_path / "p.db")
    paper.reset(conn, 1000)
    cand = {"setup": "trend_pullback", "setup_name": "Trend pullback", "direction": "long", "entry_type": "limit",
            "entry": 100.0, "stop": 99.0, "tp1": 101.5, "tp2": 103.0}
    spec = {"contract_size": 1, "volume_min": 0.01, "volume_step": 0.01, "volume_max": 100, "tick_size": 0.01, "tick_value": 0.01}
    assert paper.open_for_plan(conn, 1, "XAUUSD", cand, spec, 1.0) == 3
    paper.process(conn, {"XAUUSD": {"bid": 100.4, "ask": 100.5}})        # not touched yet
    assert all(t["status"] == "pending" for t in paper.trades(conn))
    paper.process(conn, {"XAUUSD": {"bid": 99.9, "ask": 100.0}})         # limit fills
    assert all(t["status"] == "open" for t in paper.trades(conn))
    closed = paper.process(conn, {"XAUUSD": {"bid": 101.6, "ask": 101.7}})  # tp1 + quick1r win; tp2_be moves to BE
    assert {t["policy"] for t in closed} == {"tp1", "quick1r"}
    paper.process(conn, {"XAUUSD": {"bid": 99.95, "ask": 100.05}})       # back to entry -> breakeven
    by = {t["policy"]: t for t in paper.trades(conn)}
    assert by["tp1"]["status"] == "won" and abs(by["tp1"]["r"] - 1.5) < 1e-6
    assert by["tp2_be"]["status"] == "breakeven"
    assert paper.account(conn)["balance"] > 1000


def test_auto_tune_switches_off_losing_setup(tmp_path, monkeypatch):
    import sqlite3
    from datetime import datetime, timezone
    from src.analyst import paper, refine
    from src.store.db import kora_connection
    conn = kora_connection(tmp_path / "r.db")
    paper.reset(conn, 1000)
    now = datetime.now(timezone.utc).isoformat()
    for i in range(12):
        conn.execute("INSERT INTO paper_trades (code, setup, setup_name, direction, policy, status, created_at, closed_at, r, pnl) "
                     "VALUES ('V75', 'breakout_retest', 'Breakout & retest', 'long', 'tp1', 'lost', ?, ?, -1, -10)", (now, now))
    conn.commit()
    monkeypatch.setattr(refine, "add_decision", lambda *a, **k: None)
    assert refine.auto_tune(conn) == ["breakout_retest on V75"]
    from src.analyst.config import get_settings
    assert "breakout_retest" in get_settings(conn)["setups_off"]["V75"]
    assert refine.auto_tune(conn) == []   # idempotent
