#!/usr/bin/env python
"""Backtests the baseline strategy against an MT4/5 History Center CSV export.

Only supports mt4_mt5-style price-based stop/target simulation for now - see
src/backtest/engine.py for why Deriv Multipliers/Options aren't included.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml

from src.backtest.data import load_candles_csv
from src.backtest.report import run_and_report

STRATEGY_PATH = Path(__file__).resolve().parents[1] / "config" / "strategy.yaml"
RULES_PATH = Path(__file__).resolve().parents[1] / "config" / "rules.yaml"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True, help="path to an MT4/5 History Center CSV export")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--tick-size", type=float, required=True, help="e.g. 0.0001 for a 5-digit FX pair")
    parser.add_argument("--tick-value", type=float, required=True, help="account-currency value of one tick, per lot")
    parser.add_argument("--volume-step", type=float, default=0.01)
    parser.add_argument("--starting-balance", type=float, default=10000.0)
    parser.add_argument("--risk-pct", type=float, default=None, help="defaults to config/rules.yaml's max_risk_per_trade_pct")
    args = parser.parse_args()

    strategy_cfg = yaml.safe_load(STRATEGY_PATH.read_text())
    rules_cfg = yaml.safe_load(RULES_PATH.read_text())
    risk_pct = args.risk_pct if args.risk_pct is not None else rules_cfg["risk"]["max_risk_per_trade_pct"]

    candles = load_candles_csv(args.csv)
    if not candles:
        print("No candles parsed from the CSV - check the file format.")
        return
    print(f"Loaded {len(candles)} candles: {candles[0]['time']} -> {candles[-1]['time']}")

    instrument_cfg = {"symbol": args.symbol, "platform": "mt4_mt5"}
    symbol_spec = {"tick_size": args.tick_size, "tick_value": args.tick_value, "volume_step": args.volume_step}

    run_and_report(candles, strategy_cfg, instrument_cfg, rules_cfg, args.starting_balance, risk_pct, symbol_spec)


if __name__ == "__main__":
    main()
