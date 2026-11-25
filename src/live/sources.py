"""Market-data sources for the Kora service (scripts/run_kora.py).

MetaApiSource keeps one MetaApi *streaming* connection open for the whole
session - live quotes, positions and account state arrive by push and are
read from MetaApi's local terminal-state cache, so a 2-second refresh costs
no API calls. Candles for analysis come from MetaApi's REST history
endpoint. SimSource produces deterministic fake data for demos/dev and is
always labelled SIMULATED in the hub.

Both expose the same async interface:
    start(), snapshot() -> dict, candles(symbol, tf, count, end=None), close()
"""
from __future__ import annotations

import asyncio
import os
import random
import time
from datetime import datetime, timedelta, timezone

from src.ingest.metatrader import TIMEFRAME_MAP
from src.live import sim


def _iso(t) -> str:
    if isinstance(t, str):
        return t
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc).isoformat()


class MetaApiSource:
    name = "metaapi"

    def __init__(self, instruments: list[dict]):
        self.instruments = instruments
        self.by_symbol = {m["symbol"]: m for m in instruments}
        self.latency_ms: float | None = None
        self.errors: dict[str, str] = {}

    async def start(self):
        from metaapi_cloud_sdk import MetaApi
        self.api = MetaApi(os.environ["METAAPI_TOKEN"])
        self.account = await self.api.metatrader_account_api.get_account(os.environ["METAAPI_ACCOUNT_ID"])
        await self.account.wait_connected()
        self.conn = self.account.get_streaming_connection()
        await self.conn.connect()
        await self.conn.wait_synchronized()
        for m in self.instruments:
            try:
                await self.conn.subscribe_to_market_data(m["symbol"], [{"type": "quotes"}], timeout_in_seconds=20)
            except Exception as e:  # a mis-named symbol shouldn't stop the rest of the watchlist
                self.errors[m["code"]] = f"subscribe failed: {e}"

    def snapshot(self) -> dict:
        ts = self.conn.terminal_state
        quotes = {}
        for m in self.instruments:
            p = ts.price(m["symbol"])
            if p:
                quotes[m["code"]] = {"bid": p["bid"], "ask": p["ask"], "price": (p["bid"] + p["ask"]) / 2,
                                     "time": _iso(p["time"]) if p.get("time") else None}
        info = ts.account_information or {}
        positions = []
        for p in ts.positions or []:
            spec = ts.specification(p["symbol"]) or {}
            price = ts.price(p["symbol"]) or {}
            risk = None
            if p.get("stopLoss") and spec.get("tickSize") and price.get("lossTickValue"):
                risk = abs(p["openPrice"] - p["stopLoss"]) / spec["tickSize"] * price["lossTickValue"] * p["volume"]
            meta = self.by_symbol.get(p["symbol"], {})
            positions.append({
                "id": str(p["id"]), "symbol": p["symbol"], "code": meta.get("code", p["symbol"]),
                "kind": meta.get("kind", "Other"), "digits": meta.get("digits", spec.get("digits", 2)),
                "direction": "buy" if p["type"] == "POSITION_TYPE_BUY" else "sell",
                "volume": p["volume"], "open_price": p["openPrice"], "current_price": p.get("currentPrice"),
                "stop_loss": p.get("stopLoss"), "take_profit": p.get("takeProfit"),
                "profit": p.get("profit", 0.0), "swap": p.get("swap", 0.0), "commission": p.get("commission", 0.0),
                "open_time": _iso(p["time"]), "comment": p.get("comment"), "risk_amount": risk,
            })
        account = {
            "login": info.get("login"), "server": info.get("server"), "broker": info.get("broker"),
            "currency": info.get("currency"), "balance": info.get("balance"), "equity": info.get("equity"),
            "margin": info.get("margin"), "free_margin": info.get("freeMargin"), "leverage": info.get("leverage"),
            "type": info.get("type"), "name": info.get("name"),
        }
        return {"quotes": quotes, "account": account, "positions": positions}

    async def candles(self, symbol: str, tf: str, count: int, end: datetime | None = None) -> list[dict]:
        t0 = time.perf_counter()
        raw = await self.account.get_historical_candles(symbol, TIMEFRAME_MAP.get(tf, tf.lower()),
                                                        start_time=end, limit=min(count, 1000))
        self.latency_ms = (time.perf_counter() - t0) * 1000
        out = [{"time": _iso(c["time"]), "open": c["open"], "high": c["high"], "low": c["low"],
                "close": c["close"], "volume": c.get("tickVolume")} for c in raw or []]
        out.sort(key=lambda c: c["time"])
        return out

    async def close(self):
        try:
            await self.conn.close()
        except Exception:
            pass


class SimSource:
    name = "sim"

    def __init__(self, instruments: list[dict]):
        self.instruments = instruments
        self.latency_ms = None
        self.errors: dict[str, str] = {}
        self.prices: dict[str, float] = {}
        self.day_open: dict[str, float] = {}

    async def start(self):
        for m in self.instruments:
            c = sim.gen_candles(m["code"], "H1", 30)
            self.prices[m["code"]] = c[-1]["close"]
            self.day_open[m["code"]] = c[-1]["close"] * (1 - random.Random(m["code"]).uniform(-0.012, 0.012))
        self.balance = 26148.72
        self.positions = []
        for pid, code, d, vol, sl_m, tp_m in (("90001", "V75", "sell", 0.5, 0.003, -0.0075),
                                               ("90002", "XAUUSD", "buy", 0.05, -0.0052, 0.0105),
                                               ("90003", "STEP", "buy", 2.0, -0.0025, 0.0049)):
            m = next((x for x in self.instruments if x["code"] == code), None)
            if m is None:
                continue
            entry = self.prices[code] * (1.0012 if d == "sell" else 0.9992)
            self.positions.append({"id": pid, "symbol": m["symbol"], "code": code, "kind": m["kind"],
                                   "digits": m["digits"], "direction": d, "volume": vol, "open_price": entry,
                                   "stop_loss": entry * (1 + sl_m), "take_profit": entry * (1 + tp_m),
                                   "swap": -0.8, "commission": -0.7,
                                   "open_time": (datetime.now(timezone.utc) - timedelta(hours=2, minutes=12)).isoformat(),
                                   "mult": {"V75": 1.0, "XAUUSD": 100.0, "STEP": 10.0}[code]})

    def snapshot(self) -> dict:
        quotes = {}
        for m in self.instruments:
            code = m["code"]
            vol = sim.BASE_PRICES.get(code, (1, 0.003))[1] / 25
            self.prices[code] *= 1 + random.gauss(0, vol)
            p = self.prices[code]
            spread = p * 0.00004
            quotes[code] = {"bid": p - spread, "ask": p + spread, "price": p, "day_open": self.day_open[code],
                            "time": datetime.now(timezone.utc).isoformat()}
        positions, upl = [], 0.0
        for pos in self.positions:
            cur = self.prices[pos["code"]]
            sign = 1 if pos["direction"] == "buy" else -1
            profit = sign * (cur - pos["open_price"]) * pos["volume"] * pos["mult"]
            upl += profit
            risk = abs(pos["open_price"] - pos["stop_loss"]) * pos["volume"] * pos["mult"]
            positions.append({**{k: v for k, v in pos.items() if k != "mult"}, "current_price": cur,
                              "profit": profit, "risk_amount": risk})
        account = {"login": "SIM-0001", "server": "Simulated", "broker": "Simulated", "currency": "USD",
                   "balance": self.balance, "equity": self.balance + upl, "margin": 1240.5,
                   "free_margin": self.balance + upl - 1240.5, "leverage": 500, "type": "SIMULATED", "name": "Simulation"}
        return {"quotes": quotes, "account": account, "positions": positions}

    async def candles(self, symbol: str, tf: str, count: int, end: datetime | None = None) -> list[dict]:
        code = next((m["code"] for m in self.instruments if m["symbol"] == symbol), symbol)
        await asyncio.sleep(0)
        out = sim.gen_candles(code, tf, count, end=end)
        if end is None and code in self.prices:  # stitch the live sim price onto the last bar
            scale = self.prices[code] / out[-1]["close"]
            for c in out:
                for k in ("open", "high", "low", "close"):
                    c[k] *= scale
        return out

    async def close(self):
        pass


def make_source(instruments: list[dict]):
    if os.environ.get("KORA_DATA_SOURCE", "").lower() == "sim":
        return SimSource(instruments)
    if not (os.environ.get("METAAPI_TOKEN") and os.environ.get("METAAPI_ACCOUNT_ID")):
        raise SystemExit("METAAPI_TOKEN / METAAPI_ACCOUNT_ID not set. Fill in .env, or run with "
                         "KORA_DATA_SOURCE=sim to demo the hub on simulated data.")
    return MetaApiSource(instruments)
