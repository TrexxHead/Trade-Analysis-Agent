"""Pulls closed trade history from MT4/MT5 via MetaApi.cloud.

MetaTrader itself has no public API - MetaApi.cloud is a broker-agnostic
bridge that connects to your MT4/5 account and exposes deal history over
a Python SDK. MT4/5 report trades as separate "deals" (an opening deal and
one or more closing deals sharing a positionId), so this groups them back
into single trades before handing them to the rest of the pipeline.
"""
import asyncio
import os
from datetime import datetime, timezone

from metaapi_cloud_sdk import MetaApi

OPEN_ENTRY_TYPES = {"DEAL_ENTRY_IN"}
CLOSE_ENTRY_TYPES = {"DEAL_ENTRY_OUT", "DEAL_ENTRY_OUT_BY"}
TRADE_DEAL_TYPES = {"DEAL_TYPE_BUY", "DEAL_TYPE_SELL"}


def _iso(mt_time) -> str:
    if isinstance(mt_time, str):
        return mt_time
    return mt_time.replace(tzinfo=timezone.utc).isoformat()


def _direction(deal_type: str) -> str:
    return "buy" if deal_type == "DEAL_TYPE_BUY" else "sell"


def group_into_trades(account_id: str, deals: list[dict]) -> list[dict]:
    by_position: dict[str, list[dict]] = {}
    for deal in deals:
        if deal.get("type") not in TRADE_DEAL_TYPES:
            continue
        position_id = deal.get("positionId")
        if not position_id:
            continue
        by_position.setdefault(position_id, []).append(deal)

    trades = []
    for position_id, position_deals in by_position.items():
        opens = [d for d in position_deals if d.get("entryType") in OPEN_ENTRY_TYPES]
        closes = [d for d in position_deals if d.get("entryType") in CLOSE_ENTRY_TYPES]
        if not opens or not closes:
            continue  # position not fully closed within this window yet

        open_deal = opens[0]
        last_close = max(closes, key=lambda d: d["time"])
        pnl = sum(d.get("profit", 0) + d.get("commission", 0) + d.get("swap", 0) for d in closes)

        trades.append({
            "id": f"mt:{position_id}",
            "source": "mt4_mt5",
            "account_id": account_id,
            "symbol": open_deal["symbol"],
            "direction": _direction(open_deal["type"]),
            "volume": open_deal.get("volume"),
            "open_time": _iso(open_deal["time"]),
            "close_time": _iso(last_close["time"]),
            "open_price": open_deal.get("price"),
            "close_price": last_close.get("price"),
            "pnl": pnl,
            "balance_after": None,  # not exposed per-deal; derive from the account equity curve if needed later
            "raw": {"deals": position_deals},
        })
    return trades


async def _connected_rpc(token: str, account_id: str):
    api = MetaApi(token)
    account = await api.metatrader_account_api.get_account(account_id)
    await account.wait_connected()

    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()
    return connection


async def _fetch_deals(token: str, account_id: str, date_from: datetime, date_to: datetime) -> list[dict]:
    connection = await _connected_rpc(token, account_id)
    result = await connection.get_deals_by_time_range(date_from, date_to)
    # Confirmed against a live account: this returns {"deals": [...], "synchronizing": bool},
    # not a bare list - the wrapping isn't documented, so don't assume it back to a list shape.
    return result["deals"]


def fetch_trades(date_from: datetime, date_to: datetime) -> list[dict]:
    token = os.environ["METAAPI_TOKEN"]
    account_id = os.environ["METAAPI_ACCOUNT_ID"]
    deals = asyncio.run(_fetch_deals(token, account_id, date_from, date_to))
    return group_into_trades(account_id, deals)


# --- Everything below here (candles, symbol specs, account info, order
# placement) was written from MetaApi's documented API shape and has now
# been cross-checked against the installed SDK's actual source
# (metaapi_cloud_sdk==29.1.1). Order placement (create_market_buy_order /
# create_market_sell_order) still hasn't been exercised against a live
# account - verify before trusting it for real orders.

# MetaApi uses lowercase timeframe strings ("1h", "4h"), not this project's
# platform-agnostic "H1"/"H4" convention used elsewhere (config/strategy.yaml,
# Deriv's granularity mapping) - translate here so the config stays
# platform-agnostic.
TIMEFRAME_MAP = {"M1": "1m", "M5": "5m", "M15": "15m", "M30": "30m", "H1": "1h", "H4": "4h", "D1": "1d"}


async def _fetch_candles_async(symbol: str, timeframe: str, count: int) -> list[dict]:
    token = os.environ["METAAPI_TOKEN"]
    account_id = os.environ["METAAPI_ACCOUNT_ID"]
    api = MetaApi(token)
    account = await api.metatrader_account_api.get_account(account_id)
    mt_timeframe = TIMEFRAME_MAP.get(timeframe, timeframe.lower())
    # get_historical_candles is a REST call (account.get_historical_candles),
    # not part of the RPC connection - confirmed via source, no RPC connect/
    # synchronize needed. Sort ascending defensively since the docs don't
    # guarantee return order.
    candles = await account.get_historical_candles(symbol, mt_timeframe, limit=count)
    result = [
        {"open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"], "time": _iso(c["time"])}
        for c in candles
    ]
    result.sort(key=lambda c: c["time"])
    return result


def fetch_candles(symbol: str, timeframe: str, count: int) -> list[dict]:
    return asyncio.run(_fetch_candles_async(symbol, timeframe, count))


async def _get_positions_async() -> list[dict]:
    token = os.environ["METAAPI_TOKEN"]
    account_id = os.environ["METAAPI_ACCOUNT_ID"]
    connection = await _connected_rpc(token, account_id)
    # Unlike get_deals_by_time_range, get_positions resolves directly to a
    # bare list of MetatraderPosition dicts (confirmed via SDK source:
    # rpc_metaapi_connection_instance.py) - no {"positions": [...]} wrapper.
    positions = await connection.get_positions()
    return [
        {
            "id": p["id"],
            "symbol": p["symbol"],
            "direction": "buy" if p["type"] == "POSITION_TYPE_BUY" else "sell",
            "volume": p["volume"],
            "open_price": p["openPrice"],
            "current_price": p["currentPrice"],
            "stop_loss": p.get("stopLoss"),
            "take_profit": p.get("takeProfit"),
            "profit": p["profit"],
            "swap": p.get("swap", 0.0),
            "open_time": _iso(p["time"]),
        }
        for p in positions
    ]


def get_positions() -> list[dict]:
    """Live open positions on the configured MetaApi account. Unlike everything
    else in this module, this can't be served from data/trades.db - open
    positions aren't "trades" until they close, so this always hits MetaApi
    directly rather than caching in SQLite.
    """
    return asyncio.run(_get_positions_async())


async def _get_symbol_spec_async(symbol: str) -> dict:
    token = os.environ["METAAPI_TOKEN"]
    account_id = os.environ["METAAPI_ACCOUNT_ID"]
    connection = await _connected_rpc(token, account_id)
    spec = await connection.get_symbol_specification(symbol)
    # MetatraderSymbolSpecification has no tickValue field (confirmed via
    # source) - only tickSize and contractSize. tick_value (currency per tick
    # per lot) = tickSize * contractSize, exact when account currency ==
    # quote currency (true for a USD account trading XAUUSD).
    return {
        "tick_size": spec["tickSize"],
        "tick_value": spec["tickSize"] * spec["contractSize"],
        "volume_step": spec.get("volumeStep", 0.01),
    }


def get_symbol_spec(symbol: str) -> dict:
    return asyncio.run(_get_symbol_spec_async(symbol))


async def _get_account_info_async() -> dict:
    token = os.environ["METAAPI_TOKEN"]
    account_id = os.environ["METAAPI_ACCOUNT_ID"]
    connection = await _connected_rpc(token, account_id)
    return await connection.get_account_information()


def get_account_info() -> dict:
    """Returns MetaApi's accountInformation payload - includes balance and trade mode."""
    return asyncio.run(_get_account_info_async())


def is_demo_account(account_info: dict) -> bool:
    # MetaApi surfaces the MT trade mode (e.g. ACCOUNT_TRADE_MODE_DEMO vs
    # _REAL) somewhere in accountInformation - the exact key has not been
    # confirmed against a live response. Check both common spellings and
    # fail closed (treat as NOT demo, i.e. block execution) if neither matches,
    # rather than silently assuming it's safe.
    value = str(account_info.get("type") or account_info.get("tradeMode") or "").upper()
    return "DEMO" in value


async def _place_order_async(symbol: str, direction: str, volume: float,
                              stop: float | None, target: float | None, require_demo: bool) -> dict:
    token = os.environ["METAAPI_TOKEN"]
    account_id = os.environ["METAAPI_ACCOUNT_ID"]
    connection = await _connected_rpc(token, account_id)

    if require_demo:
        info = await connection.get_account_information()
        if not is_demo_account(info):
            raise RuntimeError(
                f"Refusing to place a trade: MetaApi account {account_id} did not report as demo/virtual. "
                "Pass require_demo=False explicitly once you're ready to trade live."
            )

    if direction == "long":
        return await connection.create_market_buy_order(symbol, volume, stop_loss=stop, take_profit=target)
    return await connection.create_market_sell_order(symbol, volume, stop_loss=stop, take_profit=target)


def place_trade(symbol: str, direction: str, volume: float,
                 stop: float | None = None, target: float | None = None, require_demo: bool = True) -> dict:
    return asyncio.run(_place_order_async(symbol, direction, volume, stop, target, require_demo))
