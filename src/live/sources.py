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
import json
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


class MT5Source:
    """Your own MT5 terminal via the free MetaTrader5 package (Windows)."""
    name = "mt5"

    def __init__(self, instruments: list[dict]):
        self.instruments = instruments
        self.by_symbol = {m["symbol"]: m for m in instruments}
        self.latency_ms: float | None = None
        self.errors: dict[str, str] = {}

    async def start(self):
        from src.ingest import mt5_local
        mt5_local.connect()
        for m in self.instruments:
            try:
                mt5_local.ensure_symbol(m["symbol"])
            except Exception as e:
                self.errors[m["code"]] = str(e)

    def snapshot(self) -> dict:
        from src.ingest import mt5_local
        h = mt5_local.health()  # also re-initialises the link if the terminal was restarted
        if not h["terminal"]:
            raise RuntimeError(h.get("error") or "The MT5 terminal isn't responding")
        t0 = time.perf_counter()
        today = datetime.now(timezone.utc).date().isoformat()
        if getattr(self, "_specs_day", None) != today:  # refresh specs + day opens once a day (and at start)
            self.specs, self.day_opens = {}, {}
            for m in self.instruments:
                try:
                    mt5_local.ensure_symbol(m["symbol"])
                    self.specs[m["code"]] = mt5_local.symbol_spec(m["symbol"])
                    self.day_opens[m["code"]] = mt5_local.day_open(m["symbol"])
                    self.errors.pop(m["code"], None)
                except Exception as e:
                    self.errors[m["code"]] = str(e)
            self._specs_day = today
        quotes = {}
        for m in self.instruments:
            if m["code"] in self.errors and "not found" in self.errors[m["code"]]:
                continue
            q = mt5_local.tick(m["symbol"])
            if q:
                q["day_open"] = self.day_opens.get(m["code"])
                spec = self.specs.get(m["code"]) or {}
                if spec.get("point") and q.get("ask"):
                    q["spread_points"] = round((q["ask"] - q["bid"]) / spec["point"])
                quotes[m["code"]] = q
        self.latency_ms = (time.perf_counter() - t0) * 1000
        return {"quotes": quotes, "account": mt5_local.account(), "positions": mt5_local.positions(self.by_symbol),
                "orders": mt5_local.orders(self.by_symbol), "specs": self.specs, "health": h}

    async def candles(self, symbol: str, tf: str, count: int, end: datetime | None = None) -> list[dict]:
        from src.ingest import mt5_local
        return mt5_local.candles(symbol, tf, count, end)

    async def close(self):
        pass


# Deriv's current public endpoint needs no app_id or token; the legacy host
# (often HTTP 520 now) is kept as a fallback. Override with DERIV_WS_URL.
DERIV_WS_URLS = ["wss://api.derivws.com/trading/v1/options/ws/public",
                 "wss://ws.derivws.com/websockets/v3?app_id={app_id}"]
DERIV_GRAN = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}


class DerivSource:
    """Deriv's free public WebSocket API: live ticks and candles for every
    watchlist instrument, no account or token needed. It can't see MT5
    positions or balances - run your MT5 terminal for those."""
    name = "deriv"

    def __init__(self, instruments: list[dict]):
        self.instruments = [m for m in instruments if m.get("deriv_symbol")]
        self.by_deriv = {m["deriv_symbol"]: m for m in self.instruments}
        self.by_symbol = {m["symbol"]: m for m in self.instruments}
        self.latency_ms: float | None = None
        self.errors: dict[str, str] = {}
        self.ticks: dict[str, dict] = {}
        self.ws = None
        self.pending: dict[int, asyncio.Future] = {}
        self.req = 0
        self.reader = None

    async def start(self):
        await self._connect()

    async def _connect(self):
        import websockets
        urls = [os.environ["DERIV_WS_URL"]] if os.environ.get("DERIV_WS_URL") else DERIV_WS_URLS
        last = None
        for url in urls:
            try:
                self.ws = await websockets.connect(url.format(app_id=os.environ.get("DERIV_APP_ID") or "1089"),
                                                   open_timeout=20, ping_interval=25, max_size=2 ** 23)
                break
            except Exception as e:
                last = e
        else:
            raise RuntimeError(f"Couldn't reach Deriv's public feed: {last}")
        self.reader = asyncio.create_task(self._read())
        for m in self.instruments:
            await self.ws.send(json.dumps({"ticks": m["deriv_symbol"], "subscribe": 1}))

    async def _read(self):
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                rid = msg.get("req_id")
                if rid in self.pending:
                    self.pending.pop(rid).set_result(msg)
                elif msg.get("msg_type") == "tick" and "tick" in msg:
                    t = msg["tick"]
                    self.ticks[t["symbol"]] = t
                elif msg.get("error") and msg.get("echo_req", {}).get("ticks"):
                    sym = msg["echo_req"]["ticks"]
                    m = self.by_deriv.get(sym)
                    if m:
                        self.errors[m["code"]] = f"Deriv: {msg['error'].get('message')}"
        except Exception as e:
            for f in self.pending.values():
                if not f.done():
                    f.set_exception(e)
            self.pending.clear()

    async def _ensure(self):
        if self.ws is None or self.reader is None or self.reader.done():
            await self._connect()

    async def _call(self, payload: dict, timeout: float = 30) -> dict:
        await self._ensure()
        self.req += 1
        payload = {**payload, "req_id": self.req}
        fut = asyncio.get_running_loop().create_future()
        self.pending[self.req] = fut
        t0 = time.perf_counter()
        await self.ws.send(json.dumps(payload))
        msg = await asyncio.wait_for(fut, timeout)
        self.latency_ms = (time.perf_counter() - t0) * 1000
        if msg.get("error"):
            raise RuntimeError(f"Deriv: {msg['error'].get('message')}")
        return msg

    def snapshot(self) -> dict:
        quotes = {}
        for sym, t in self.ticks.items():
            m = self.by_deriv.get(sym)
            if not m:
                continue
            bid, ask = t.get("bid") or t["quote"], t.get("ask") or t["quote"]
            quotes[m["code"]] = {"bid": bid, "ask": ask, "price": t["quote"],
                                 "time": datetime.fromtimestamp(t["epoch"], tz=timezone.utc).isoformat()}
        return {"quotes": quotes, "account": {}, "positions": []}

    async def candles(self, symbol: str, tf: str, count: int, end: datetime | None = None) -> list[dict]:
        m = self.by_symbol.get(symbol) or self.by_deriv.get(symbol)
        if not m:
            raise RuntimeError(f"No Deriv symbol configured for {symbol}")
        msg = await self._call({"ticks_history": m["deriv_symbol"], "style": "candles", "granularity": DERIV_GRAN[tf],
                                "count": min(count, 5000), "end": str(int(end.timestamp())) if end else "latest"})
        return [{"time": datetime.fromtimestamp(c["epoch"], tz=timezone.utc).isoformat(), "open": float(c["open"]),
                 "high": float(c["high"]), "low": float(c["low"]), "close": float(c["close"]), "volume": None}
                for c in msg.get("candles", [])]

    async def close(self):
        try:
            await self.ws.close()
        except Exception:
            pass


def resolve_source_name() -> str:
    """KORA_DATA_SOURCE = sim | mt5 | deriv | metaapi | auto (default).
    auto: your MT5 terminal if it's running, else MetaApi if configured,
    else Deriv's free public feed."""
    choice = (os.environ.get("KORA_DATA_SOURCE") or "auto").lower()
    if choice != "auto":
        return choice
    try:
        from src.ingest import mt5_local
        if mt5_local.available():
            mt5_local.connect()
            return "mt5"
    except Exception:
        pass
    if os.environ.get("METAAPI_TOKEN") and os.environ.get("METAAPI_ACCOUNT_ID"):
        return "metaapi"
    return "deriv"


def make_source(instruments: list[dict]):
    name = resolve_source_name()
    if name == "sim":
        return SimSource(instruments)
    if name == "mt5":
        return MT5Source(instruments)
    if name == "deriv":
        return DerivSource(instruments)
    if name == "metaapi":
        if not (os.environ.get("METAAPI_TOKEN") and os.environ.get("METAAPI_ACCOUNT_ID")):
            raise SystemExit("KORA_DATA_SOURCE=metaapi but METAAPI_TOKEN / METAAPI_ACCOUNT_ID aren't set in .env")
        return MetaApiSource(instruments)
    raise SystemExit(f"Unknown KORA_DATA_SOURCE '{name}' - use auto, mt5, deriv, metaapi or sim")


def fetch_candles_sync(meta: dict, tf: str, count: int, source_name: str | None = None) -> list[dict]:
    """One-off candle fetch for the hub's chart endpoint, from whichever
    source the Kora service is using (or the best available)."""
    name = source_name or resolve_source_name()
    if name == "sim":
        return sim.gen_candles(meta["code"], tf, count)
    if name == "mt5":
        from src.ingest import mt5_local
        return mt5_local.candles(meta["symbol"], tf, count)
    if name == "metaapi":
        from src.ingest.metatrader import fetch_candles
        return fetch_candles(meta["symbol"], tf, count)

    async def via_deriv():
        src = DerivSource([meta])
        src.instruments = []  # no tick subscriptions for a one-off fetch
        await src._connect()
        try:
            return await src.candles(meta["symbol"], tf, count)
        finally:
            await src.close()
    return asyncio.run(via_deriv())
