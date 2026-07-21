#!/usr/bin/env python
"""Backtests the configured strategy against candles pulled fresh from
MetaApi - no manual MT4/5 History Center CSV export needed, unlike
run_backtest.py. Useful for instruments you don't already have a saved
export for, e.g. the synthetic indices added alongside XAUUSD.

Only supports mt4_mt5-style instruments (see src/backtest/engine.py for why
Deriv Multipliers/Options aren't backtestable here).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml
from dotenv import load_dotenv

from src.backtest.report import run_and_report
from src.ingest.metatrader import fetch_candle_history, get_symbol_spec
from src.strategy import scanning

load_dotenv()

STRATEGY_PATH = Path(__file__).resolve().parents[1] / "config" / "strategy.yaml"
RULES_PATH = Path(__file__).resolve().parents[1] / "config" / "rules.yaml"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True, help="must match an mt4_mt5 instrument in config/strategy.yaml")
    parser.add_argument("--days", type=int, default=180,
                         help="how much history to request (actual coverage is capped by your broker's history depth)")
    parser.add_argument("--starting-balance", type=float, default=10000.0)
    parser.add_argument("--risk-pct", type=float, default=None,
                         help="defaults to config/rules.yaml's max_risk_per_trade_pct")
    args = parser.parse_args()

    strategy_cfg = yaml.safe_load(STRATEGY_PATH.read_text())
    rules_cfg = yaml.safe_load(RULES_PATH.read_text())
    risk_pct = args.risk_pct if args.risk_pct is not None else rules_cfg["risk"]["max_risk_per_trade_pct"]

    instrument_cfg = next(
        (i for i in strategy_cfg["instruments"] if i["symbol"] == args.symbol and i["platform"] == "mt4_mt5"),
        None,
    )
    if instrument_cfg is None:
        print(f"'{args.symbol}' isn't an mt4_mt5 instrument in config/strategy.yaml - add it there first.")
        return
    cfg = scanning.merged_cfg(strategy_cfg, instrument_cfg)

    print(f"Fetching ~{args.days} days of {cfg['timeframe']} candles for {args.symbol} from MetaApi...")
    candles = fetch_candle_history(args.symbol, cfg["timeframe"], args.days)
    if not candles:
        print("No candles returned - check the symbol name against your MT5 Market Watch exactly.")
        return
    print(f"Got {len(candles)} candles: {candles[0]['time']} -> {candles[-1]['time']}")

    print("Fetching symbol spec (tick size/value, volume step) from MetaApi...")
    symbol_spec = get_symbol_spec(args.symbol)
    print(f"symbol_spec: {symbol_spec}")

    run_and_report(candles, cfg, instrument_cfg, rules_cfg, args.starting_balance, risk_pct, symbol_spec)


if __name__ == "__main__":
    main()
