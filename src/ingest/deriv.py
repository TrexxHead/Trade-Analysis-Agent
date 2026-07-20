"""Pulls closed trade history from Deriv's WebSocket API (api.deriv.com).

Deriv has no REST trade-history endpoint - everything goes through a
WebSocket JSON-RPC connection authorized with an API token.
"""
import asyncio
import json
import os
from datetime import datetime, timezone

import websockets

DERIV_WS_URL = "wss://ws.derivws.com/websockets/v3?app_id={app_id}"


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


async def _fetch_profit_table(token: str, app_id: str, date_from: datetime, date_to: datetime) -> dict:
    url = DERIV_WS_URL.format(app_id=app_id)
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"authorize": token}))
        auth_response = json.loads(await ws.recv())
        if "error" in auth_response:
            raise RuntimeError(f"Deriv authorization failed: {auth_response['error']['message']}")
        account_id = auth_response["authorize"]["loginid"]

        trades = []
        offset = 0
        limit = 500
        while True:
            await ws.send(json.dumps({
                "profit_table": 1,
                "description": 1,
                "sort": "ASC",
                "date_from": _epoch(date_from),
                "date_to": _epoch(date_to),
                "limit": limit,
                "offset": offset,
            }))
            response = json.loads(await ws.recv())
            if "error" in response:
                raise RuntimeError(f"Deriv profit_table request failed: {response['error']['message']}")
            batch = response["profit_table"]["transactions"]
            trades.extend(batch)
            if len(batch) < limit:
                break
            offset += limit
        return {"account_id": account_id, "transactions": trades}


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
