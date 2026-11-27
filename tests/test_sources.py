"""Data-source adapters tested against fakes of the MT5 package and Deriv API."""
import asyncio
import sys
import types
from collections import namedtuple

import pytest

from src.analyst.config import instruments


def fake_mt5_module():
    m = types.ModuleType("MetaTrader5")
    for i, tf in enumerate(["M1", "M5", "M15", "M30", "H1", "H4", "D1"]):
        setattr(m, f"TIMEFRAME_{tf}", i)
    Tick = namedtuple("Tick", "time bid ask")
    Info = namedtuple("Info", "trade_tick_size trade_tick_value trade_tick_value_loss digits point volume_min volume_step trade_contract_size spread")
    Order = namedtuple("Order", "ticket type symbol volume_current price_open sl tp time_setup comment")
    Term = namedtuple("Term", "connected")
    Acc = namedtuple("Acc", "login trade_mode balance equity margin margin_free leverage currency server company name")
    Pos = namedtuple("Pos", "ticket time type volume price_open sl tp price_current swap profit symbol comment")
    Deal = namedtuple("Deal", "ticket time type entry position_id volume price commission swap profit symbol")
    m.initialize = lambda **kw: True
    m.terminal_info = lambda: Term(True)
    m.last_error = lambda: (1, "ok")
    m.symbol_select = lambda s, on: s != "MISSING"
    m.symbol_info_tick = lambda s: Tick(1764000000, 3342.4, 3342.6)
    m.symbol_info = lambda s: Info(0.01, 1.0, 1.0, 2, 0.01, 0.01, 0.01, 100, 20)
    m.orders_get = lambda: [Order(55, 2, "XAUUSD", 0.02, 3300.0, 3290.0, 3330.0, 1764000000, "")]
    rates = [{"time": 1764000000 + 3600 * k, "open": 1.0 + k, "high": 2.0 + k, "low": 0.5 + k, "close": 1.5 + k, "tick_volume": 10} for k in range(5)]
    m.copy_rates_from_pos = lambda s, tf, pos, n: rates[-n:]
    m.copy_rates_from = lambda s, tf, end, n: rates[-n:]
    m.copy_rates_range = lambda s, tf, a, b: rates
    m.account_info = lambda: Acc(123, 0, 1000.0, 1010.0, 50.0, 960.0, 500, "USD", "Deriv-Demo", "Deriv.com Limited", "Me")
    m.positions_get = lambda: [Pos(9, 1764000000, 0, 0.05, 3330.0, 3320.0, 3360.0, 3342.5, -0.5, 62.5, "XAUUSD", "")]
    m.history_deals_get = lambda a, b: [Deal(1, 1764000000, 0, 0, 77, 0.05, 3330.0, 0, 0, 0, "XAUUSD"),
                                        Deal(2, 1764003600, 1, 1, 77, 0.05, 3340.0, -0.2, 0, 50.0, "XAUUSD")]
    return m


@pytest.fixture
def mt5(monkeypatch):
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake_mt5_module())
    from src.ingest import mt5_local
    monkeypatch.setattr(mt5_local, "_mt5", None)
    return mt5_local


def test_mt5_snapshot_candles_and_trades(mt5):
    from src.live.sources import MT5Source
    src = MT5Source(instruments())
    asyncio.run(src.start())
    snap = src.snapshot()
    assert snap["account"]["type"] == "DEMO" and snap["account"]["equity"] == 1010.0
    p = snap["positions"][0]
    assert p["code"] == "XAUUSD" and p["direction"] == "buy"
    assert p["risk_amount"] == pytest.approx(10 / 0.01 * 1.0 * 0.05)  # $10 to stop * 100 ticks/$ * 0.05 lots
    assert snap["quotes"]["XAUUSD"]["price"] == pytest.approx(3342.4)  # bid, like MT5
    assert snap["quotes"]["XAUUSD"]["spread_points"] == 20
    assert snap["specs"]["XAUUSD"]["digits"] == 2 and snap["health"]["broker"] is True
    o = snap["orders"][0]
    assert o["direction"] == "buy" and o["order_type"] == "limit" and o["price"] == 3300.0
    c = asyncio.run(src.candles("XAUUSD", "H1", 3))
    assert len(c) == 3 and c[-1]["close"] == 5.5 and c[0]["time"].endswith("+00:00")
    trades = mt5.fetch_trades(None, None)
    assert len(trades) == 1 and trades[0]["pnl"] == pytest.approx(49.8) and trades[0]["direction"] == "buy"


def test_deriv_parses_ticks_and_candles(monkeypatch):
    from src.live.sources import DerivSource
    src = DerivSource(instruments())

    async def fake_call(payload, timeout=30):
        assert payload["granularity"] == 3600 and payload["ticks_history"] == "frxXAUUSD"
        return {"candles": [{"epoch": 1764000000, "open": 1, "high": 2, "low": 0.5, "close": 1.5}]}
    monkeypatch.setattr(src, "_call", fake_call)
    c = asyncio.run(src.candles("XAUUSD", "H1", 10))
    assert c == [{"time": "2025-11-24T16:00:00+00:00", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": None}]
    src.ticks["R_75"] = {"symbol": "R_75", "quote": 98000.5, "bid": 98000.0, "ask": 98001.0, "epoch": 1764000000}
    q = src.snapshot()["quotes"]["V75"]
    assert q["price"] == 98000.5 and q["ask"] == 98001.0


def test_auto_source_falls_back_to_deriv(monkeypatch):
    from src.live import sources
    monkeypatch.setenv("KORA_DATA_SOURCE", "auto")
    monkeypatch.delenv("METAAPI_TOKEN", raising=False)
    monkeypatch.setitem(sys.modules, "MetaTrader5", None)  # import fails, as on Linux/mac
    assert sources.resolve_source_name() == "deriv"
