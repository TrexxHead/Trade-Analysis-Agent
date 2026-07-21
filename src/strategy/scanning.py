"""Shared per-instrument scan logic used by both scripts/run_scan.py and the
dashboard's Scanner page, so "what does the strategy currently see for this
symbol" is computed in one place rather than duplicated between a CLI script
and a web route.
"""
from src.ingest import deriv, metatrader
from src.strategy.signals import generate_idea


def merged_cfg(strategy_cfg: dict, instrument: dict) -> dict:
    """Global strategy.yaml settings (timeframe/lookback/trend/entry/exit),
    overridden per-instrument wherever an instrument declares its own block.
    A trend-pullback strategy tuned for XAUUSD's session/trend structure
    isn't necessarily valid for a synthetic index's constant-volatility
    random walk - backtest and override per instrument rather than assuming
    the shared defaults transfer.
    """
    return {
        "timeframe": instrument.get("timeframe", strategy_cfg["timeframe"]),
        "lookback_candles": instrument.get("lookback_candles", strategy_cfg["lookback_candles"]),
        "trend": {**strategy_cfg["trend"], **instrument.get("trend", {})},
        "entry": {**strategy_cfg["entry"], **instrument.get("entry", {})},
        "exit": {**strategy_cfg["exit"], **instrument.get("exit", {})},
    }


def candles_for(instrument: dict, timeframe: str, count: int) -> list[dict]:
    if instrument["platform"] == "mt4_mt5":
        return metatrader.fetch_candles(instrument["symbol"], timeframe, count)
    return deriv.fetch_candles(instrument["symbol"], timeframe, count)
