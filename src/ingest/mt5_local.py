"""Free, direct access to your own MetaTrader 5 terminal via MetaQuotes'
official `MetaTrader5` Python package (Windows only).

The package talks to the MT5 terminal running on the same PC over local
IPC - no cloud bridge, no API key, no cost. Requirements:
  - the Deriv MT5 terminal installed, open and logged in to your account
  - Tools -> Options -> Expert Advisors -> "Allow algorithmic trading" is NOT
    required: Kora only reads data, it never sends orders.
Optional .env keys if the terminal isn't auto-detected or you want it to log
in for you: MT5_PATH (path to terminal64.exe), MT5_LOGIN, MT5_PASSWORD,
MT5_SERVER.

Bar/deal times from MT5 are the broker server's clock expressed as epoch
seconds; Deriv's MT5 servers run on GMT, so they're treated as UTC.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

TF = {"M1": "TIMEFRAME_M1", "M5": "TIMEFRAME_M5", "M15": "TIMEFRAME_M15", "M30": "TIMEFRAME_M30",
      "H1": "TIMEFRAME_H1", "H4": "TIMEFRAME_H4", "D1": "TIMEFRAME_D1"}

_mt5 = None


def _iso(epoch: int | float) -> str:
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat()


def available() -> bool:
    try:
        import MetaTrader5  # noqa: F401
        return True
    except ImportError:
        return False


def connect():
    """Initialise (once) and return the MetaTrader5 module, or raise with a
    readable reason."""
    global _mt5
    if _mt5 is not None and _mt5.terminal_info() is not None:
        return _mt5
    try:
        import MetaTrader5 as mt5
    except ImportError as e:
        raise RuntimeError("The MetaTrader5 package isn't installed (Windows only): pip install MetaTrader5") from e
    kwargs = {}
    if os.environ.get("MT5_PATH"):
        kwargs["path"] = os.environ["MT5_PATH"]
    if os.environ.get("MT5_LOGIN"):
        kwargs.update(login=int(os.environ["MT5_LOGIN"]), password=os.environ.get("MT5_PASSWORD", ""),
                      server=os.environ.get("MT5_SERVER", ""))
    if not mt5.initialize(**kwargs):
        code, msg = mt5.last_error()
        raise RuntimeError(f"Couldn't connect to the MT5 terminal ({code}: {msg}). Is it open and logged in?")
    _mt5 = mt5
    return mt5


def ensure_symbol(symbol: str) -> None:
    mt5 = connect()
    if not mt5.symbol_select(symbol, True):
        raise RuntimeError(f"Symbol '{symbol}' not found in MT5 Market Watch - check config/instruments.yaml")


def candles(symbol: str, tf: str, count: int, end: datetime | None = None) -> list[dict]:
    mt5 = connect()
    ensure_symbol(symbol)
    frame = getattr(mt5, TF[tf])
    if end is None:
        rates = mt5.copy_rates_from_pos(symbol, frame, 0, count)
    else:
        rates = mt5.copy_rates_from(symbol, frame, end, count)
    if rates is None:
        code, msg = mt5.last_error()
        raise RuntimeError(f"No {tf} history for {symbol} ({code}: {msg})")
    return [{"time": _iso(r["time"]), "open": float(r["open"]), "high": float(r["high"]), "low": float(r["low"]),
             "close": float(r["close"]), "volume": int(r["tick_volume"])} for r in rates]


def candle_history(symbol: str, tf: str, days: int) -> list[dict]:
    from datetime import timedelta
    mt5 = connect()
    ensure_symbol(symbol)
    now = datetime.now(timezone.utc)
    rates = mt5.copy_rates_range(symbol, getattr(mt5, TF[tf]), now - timedelta(days=days), now)
    if rates is None:
        code, msg = mt5.last_error()
        raise RuntimeError(f"No {tf} history for {symbol} ({code}: {msg})")
    return [{"time": _iso(r["time"]), "open": float(r["open"]), "high": float(r["high"]), "low": float(r["low"]),
             "close": float(r["close"]), "volume": int(r["tick_volume"])} for r in rates]


def tick(symbol: str) -> dict | None:
    mt5 = connect()
    t = mt5.symbol_info_tick(symbol)
    if t is None or not t.bid:
        return None
    return {"bid": t.bid, "ask": t.ask, "price": (t.bid + t.ask) / 2, "time": _iso(t.time)}


def account() -> dict:
    mt5 = connect()
    a = mt5.account_info()
    if a is None:
        return {}
    mode = {0: "DEMO", 1: "CONTEST", 2: "REAL"}.get(a.trade_mode, str(a.trade_mode))
    return {"login": a.login, "server": a.server, "broker": a.company, "currency": a.currency,
            "balance": a.balance, "equity": a.equity, "margin": a.margin, "free_margin": a.margin_free,
            "leverage": a.leverage, "type": mode, "name": a.name}


def positions(by_symbol: dict[str, dict]) -> list[dict]:
    mt5 = connect()
    out = []
    for p in mt5.positions_get() or []:
        info = mt5.symbol_info(p.symbol)
        risk = None
        if p.sl and info and info.trade_tick_size:
            tick_value = getattr(info, "trade_tick_value_loss", 0) or info.trade_tick_value
            risk = abs(p.price_open - p.sl) / info.trade_tick_size * tick_value * p.volume
        meta = by_symbol.get(p.symbol, {})
        out.append({
            "id": str(p.ticket), "symbol": p.symbol, "code": meta.get("code", p.symbol),
            "kind": meta.get("kind", "Other"), "digits": meta.get("digits", info.digits if info else 2),
            "direction": "buy" if p.type == 0 else "sell", "volume": p.volume,
            "open_price": p.price_open, "current_price": p.price_current,
            "stop_loss": p.sl or None, "take_profit": p.tp or None,
            "profit": p.profit, "swap": p.swap, "commission": 0.0,
            "open_time": _iso(p.time), "comment": p.comment or None, "risk_amount": risk,
        })
    return out


def fetch_trades(date_from: datetime, date_to: datetime) -> list[dict]:
    """Closed trades from the terminal's deal history, grouped into one row
    per position (same shape as the MetaApi ingest)."""
    from src.ingest.metatrader import group_into_trades
    mt5 = connect()
    deals = mt5.history_deals_get(date_from, date_to) or []
    entry = {0: "DEAL_ENTRY_IN", 1: "DEAL_ENTRY_OUT", 2: "DEAL_ENTRY_INOUT", 3: "DEAL_ENTRY_OUT_BY"}
    dtype = {0: "DEAL_TYPE_BUY", 1: "DEAL_TYPE_SELL"}
    mapped = [{
        "id": str(d.ticket), "type": dtype.get(d.type, f"DEAL_TYPE_{d.type}"), "entryType": entry.get(d.entry),
        "positionId": str(d.position_id) if d.position_id else None, "time": _iso(d.time),
        "symbol": d.symbol, "volume": d.volume, "price": d.price, "profit": d.profit,
        "commission": d.commission, "swap": d.swap,
    } for d in deals]
    acct = account()
    return group_into_trades(str(acct.get("login", "mt5")), mapped)
