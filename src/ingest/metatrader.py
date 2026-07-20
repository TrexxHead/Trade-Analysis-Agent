"""Pulls closed trade history from MT4/MT5 via MetaApi.cloud.

MetaTrader itself has no public API - MetaApi.cloud is a broker-agnostic
bridge that connects to your MT4/5 account and exposes deal history over
a Python SDK. MT4/5 report trades as separate "deals" (an opening deal and
one or more closing deals sharing a positionId), so this groups them back
into single trades before handing them to the rest of the pipeline.
"""
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


async def _fetch_deals(token: str, account_id: str, date_from: datetime, date_to: datetime) -> list[dict]:
    api = MetaApi(token)
    account = await api.metatrader_account_api.get_account(account_id)
    await account.wait_connected()

    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()

    return await connection.get_deals_by_time_range(date_from, date_to)


def fetch_trades(date_from: datetime, date_to: datetime) -> list[dict]:
    token = os.environ["METAAPI_TOKEN"]
    account_id = os.environ["METAAPI_ACCOUNT_ID"]
    import asyncio
    deals = asyncio.run(_fetch_deals(token, account_id, date_from, date_to))
    return group_into_trades(account_id, deals)
