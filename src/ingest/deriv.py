"""Talks to Deriv's WebSocket API (api.deriv.com) - both to pull closed
trade history and, now, to fetch candles and place trades.

Deriv has no REST API - everything goes through a single WebSocket
JSON-RPC connection authorized with an API token.
"""
import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import websockets

DERIV_WS_URL = "wss://ws.derivws.com/websockets/v3?app_id={app_id}"

GRANULARITY_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}


def is_demo_account(loginid: str) -> bool:
    # Deriv virtual/demo account IDs start with VR (e.g. VRTC1234567);
    # real-money accounts start with CR, MF, MX, etc.
    return loginid.upper().startswith("VR")


def _epoch(dt: datetime) -> int:
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


def _iso(epoch_seconds: int) -> str:
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).isoformat()


def _parse_direction(shortcode: str) -> str:
    if "CALL" in shortcode:
        return "buy"
    if "PUT" in shortcode:
        return "sell"
    return "unknown"


def _parse_symbol(shortcode: str) -> str:
    # shortcode format is like CALL_R_100_.../PUT_FRXEURUSD_... - the symbol
    # sits after the contract type token. Falls back to the raw shortcode
    # when the format doesn't match, which is enough to keep going and can
    # be refined once real data is flowing.
    parts = shortcode.split("_")
    return parts[1] if len(parts) > 1 else shortcode


@asynccontextmanager
async def _authorized_connection(token: str, app_id: str):
    url = DERIV_WS_URL.format(app_id=app_id)
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"authorize": token}))
        auth_response = json.loads(await ws.recv())
        if "error" in auth_response:
            raise RuntimeError(f"Deriv authorization failed: {auth_response['error']['message']}")
        yield ws, auth_response["authorize"]


async def _request(ws, payload: dict) -> dict:
    await ws.send(json.dumps(payload))
    response = json.loads(await ws.recv())
    if "error" in response:
        raise RuntimeError(f"Deriv request failed: {response['error']['message']}")
    return response


async def _fetch_profit_table(token: str, app_id: str, date_from: datetime, date_to: datetime) -> dict:
    async with _authorized_connection(token, app_id) as (ws, account):
        trades = []
        offset = 0
        limit = 500
        while True:
            response = await _request(ws, {
                "profit_table": 1,
                "description": 1,
                "sort": "ASC",
                "date_from": _epoch(date_from),
                "date_to": _epoch(date_to),
                "limit": limit,
                "offset": offset,
            })
            batch = response["profit_table"]["transactions"]
            trades.extend(batch)
            if len(batch) < limit:
                break
            offset += limit
        return {"account_id": account["loginid"], "transactions": trades}


def normalize(account_id: str, transactions: list[dict]) -> list[dict]:
    normalized = []
    for tx in transactions:
        shortcode = tx.get("shortcode", "")
        buy_price = float(tx["buy_price"])
        sell_price = float(tx["sell_price"])
        normalized.append({
            "id": f"deriv:{tx['contract_id']}",
            "source": "deriv",
            "account_id": account_id,
            "symbol": _parse_symbol(shortcode),
            "direction": _parse_direction(shortcode),
            "volume": buy_price,
            "open_time": _iso(int(tx["purchase_time"])),
            "close_time": _iso(int(tx["sell_time"])),
            "open_price": buy_price,
            "close_price": sell_price,
            "pnl": sell_price - buy_price,
            "balance_after": None,  # requires joining with the `statement` call by transaction_id
            "raw": tx,
        })
    return normalized


def fetch_trades(date_from: datetime, date_to: datetime) -> list[dict]:
    token = os.environ["DERIV_API_TOKEN"]
    app_id = os.environ.get("DERIV_APP_ID", "1089")
    result = asyncio.run(_fetch_profit_table(token, app_id, date_from, date_to))
    return normalize(result["account_id"], result["transactions"])


async def _fetch_candles_async(symbol: str, timeframe: str, count: int) -> list[dict]:
    token = os.environ["DERIV_API_TOKEN"]
    app_id = os.environ.get("DERIV_APP_ID", "1089")
    async with _authorized_connection(token, app_id) as (ws, _account):
        response = await _request(ws, {
            "ticks_history": symbol,
            "adjust_start_time": 1,
            "count": count,
            "end": "latest",
            "style": "candles",
            "granularity": GRANULARITY_SECONDS[timeframe],
        })
        return [
            {"open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"], "time": _iso(c["epoch"])}
            for c in response["candles"]
        ]


def fetch_candles(symbol: str, timeframe: str, count: int) -> list[dict]:
    return asyncio.run(_fetch_candles_async(symbol, timeframe, count))


async def _get_account_async() -> dict:
    token = os.environ["DERIV_API_TOKEN"]
    app_id = os.environ.get("DERIV_APP_ID", "1089")
    async with _authorized_connection(token, app_id) as (_ws, account):
        return account


def get_account() -> dict:
    """Returns the authorize response - includes loginid and balance."""
    return asyncio.run(_get_account_async())


async def _place_order_async(payload: dict, require_demo: bool) -> dict:
    token = os.environ["DERIV_API_TOKEN"]
    app_id = os.environ.get("DERIV_APP_ID", "1089")
    async with _authorized_connection(token, app_id) as (ws, account):
        if require_demo and not is_demo_account(account["loginid"]):
            raise RuntimeError(
                f"Refusing to place a trade: {account['loginid']} is not a Deriv demo/virtual account. "
                "Pass require_demo=False explicitly once you're ready to trade live."
            )
        response = await _request(ws, payload)
        return response["buy"]


def place_multiplier_trade(symbol: str, direction: str, stake: float, multiplier: int,
                            stop_loss_amount: float, take_profit_amount: float,
                            require_demo: bool = True) -> dict:
    payload = {
        "buy": 1,
        "price": stake,
        "parameters": {
            "amount": stake,
            "basis": "stake",
            "contract_type": "MULTUP" if direction == "long" else "MULTDOWN",
            "currency": "USD",
            "symbol": symbol,
            "multiplier": multiplier,
            "limit_order": {"stop_loss": stop_loss_amount, "take_profit": take_profit_amount},
        },
    }
    return asyncio.run(_place_order_async(payload, require_demo))


def place_option_trade(symbol: str, contract_type: str, stake: float, duration: int, duration_unit: str,
                        require_demo: bool = True) -> dict:
    payload = {
        "buy": 1,
        "price": stake,
        "parameters": {
            "amount": stake,
            "basis": "stake",
            "contract_type": contract_type,
            "currency": "USD",
            "symbol": symbol,
            "duration": duration,
            "duration_unit": duration_unit,
        },
    }
    return asyncio.run(_place_order_async(payload, require_demo))
