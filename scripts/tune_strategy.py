#!/usr/bin/env python
"""Backtests a grid of trend/entry/exit parameter combinations for one
instrument, against candles pulled fresh from MetaApi, and ranks the
results - the honest way to find per-instrument settings instead of
guessing at "reasonable-sounding" numbers.

This does NOT write anything back to config/strategy.yaml. Review the
results yourself before adding an override - a backtest ranking can overfit
to whatever history happened to be available, especially with the small
trade counts a few months of data produces here. Treat the top result as a
candidate to keep watching on demo, not a proven edge.
"""
import argparse
import itertools
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml
from dotenv import load_dotenv

from src.analysis.metrics import compute_period_stats
from src.backtest.engine import run_backtest
from src.ingest.metatrader import fetch_candle_history, get_symbol_spec
from src.strategy import scanning

load_dotenv()

STRATEGY_PATH = Path(__file__).resolve().parents[1] / "config" / "strategy.yaml"
RULES_PATH = Path(__file__).resolve().parents[1] / "config" / "rules.yaml"

# Deliberately modest - each combo re-runs the full O(n^2) backtest engine,
# so this is a tradeoff between grid coverage and runtime (roughly 0.5-1s
# per combo per 1500 candles). Widen it yourself if you want a finer sweep
# and don't mind the wait.
GRID = {
    "fast_ema": [10, 20, 50],
    "slow_ema": [50, 100, 200],
    "pullback_atr_multiple": [0.5, 1.0, 1.5],
    "rsi_thresholds": [(30, 70), (40, 60)],  # (oversold_recovery, overbought_recovery)
    "stop_atr_multiple": [1.0, 1.5, 2.0],
    "reward_risk_ratio": [1.5, 2.0, 3.0],
}


def _candidates(base_cfg: dict):
    for fast, slow, pullback, rsi_pair, stop_mult, rr in itertools.product(
        GRID["fast_ema"], GRID["slow_ema"], GRID["pullback_atr_multiple"],
        GRID["rsi_thresholds"], GRID["stop_atr_multiple"], GRID["reward_risk_ratio"],
    ):
        if fast >= slow:
            continue
        cfg = {
            "timeframe": base_cfg["timeframe"],
            "lookback_candles": base_cfg["lookback_candles"],
            "trend": {"fast_ema": fast, "slow_ema": slow},
            "entry": {
                **base_cfg["entry"],
                "pullback_atr_multiple": pullback,
                "rsi_oversold_recovery": rsi_pair[0],
                "rsi_overbought_recovery": rsi_pair[1],
            },
            "exit": {
                **base_cfg["exit"],
                "stop_atr_multiple": stop_mult,
                "reward_risk_ratio": rr,
            },
        }
        yield cfg


def _rank_key(stats: dict) -> float:
    if stats["profit_factor"] is not None:
        return stats["profit_factor"]
    return 999.0 if stats["wins"] > 0 else -999.0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True, help="must match an mt4_mt5 instrument in config/strategy.yaml")
    parser.add_argument("--days", type=int, default=180, help="how much history to pull")
    parser.add_argument("--starting-balance", type=float, default=10000.0)
    parser.add_argument("--risk-pct", type=float, default=None,
                         help="defaults to config/rules.yaml's max_risk_per_trade_pct")
    parser.add_argument("--min-trades", type=int, default=15,
                         help="drop candidates with fewer completed trades than this - too few to mean anything")
    parser.add_argument("--top", type=int, default=10, help="how many top candidates to print")
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
    base_cfg = scanning.merged_cfg(strategy_cfg, instrument_cfg)

    print(f"Fetching ~{args.days} days of {base_cfg['timeframe']} candles for {args.symbol} from MetaApi...")
    candles = fetch_candle_history(args.symbol, base_cfg["timeframe"], args.days)
    if not candles:
        print("No candles returned - check the symbol name against your MT5 Market Watch exactly.")
        return
    print(f"Got {len(candles)} candles: {candles[0]['time']} -> {candles[-1]['time']}")

    print("Fetching symbol spec from MetaApi...")
    symbol_spec = get_symbol_spec(args.symbol)

    candidates = list(_candidates(base_cfg))
    print(f"\nRunning {len(candidates)} parameter combinations "
          f"(min {args.min_trades} trades to count) - this can take a few minutes...")

    results = []
    start = time.time()
    for i, cfg in enumerate(candidates, start=1):
        outcome = run_backtest(candles, cfg, instrument_cfg, args.starting_balance, risk_pct, symbol_spec)
        stats = compute_period_stats(outcome["trades"])
        if stats["total_trades"] >= args.min_trades:
            results.append((cfg, stats))
        if i % 50 == 0 or i == len(candidates):
            elapsed = time.time() - start
            print(f"  {i}/{len(candidates)} combos done ({elapsed:.0f}s elapsed)")

    print(f"\n{len(results)}/{len(candidates)} combinations produced at least {args.min_trades} trades.")
    if not results:
        print("None did - try a shorter --min-trades, more --days of history, or a coarser GRID in this script.")
        return

    results.sort(key=lambda r: _rank_key(r[1]), reverse=True)

    # Baseline for comparison: the config as currently configured (no sweep override).
    baseline_outcome = run_backtest(candles, base_cfg, instrument_cfg, args.starting_balance, risk_pct, symbol_spec)
    baseline_stats = compute_period_stats(baseline_outcome["trades"])
    print(f"\nBaseline (current config/strategy.yaml settings): "
          f"{baseline_stats['total_trades']} trades, "
          f"win rate {baseline_stats['win_rate']:.1%}, "
          f"profit factor {baseline_stats['profit_factor']}, "
          f"P&L {baseline_stats['total_pnl']:+.2f}")

    print(f"\nTop {min(args.top, len(results))} candidates by profit factor:\n")
    header = f"{'rank':<5}{'trades':<8}{'win%':<8}{'PF':<8}{'P&L':<12}{'maxDD':<12}params"
    print(header)
    print("-" * len(header))
    for rank, (cfg, stats) in enumerate(results[:args.top], start=1):
        params = (f"EMA{cfg['trend']['fast_ema']}/{cfg['trend']['slow_ema']} "
                  f"pullback={cfg['entry']['pullback_atr_multiple']} "
                  f"RSI={cfg['entry']['rsi_oversold_recovery']}/{cfg['entry']['rsi_overbought_recovery']} "
                  f"stop={cfg['exit']['stop_atr_multiple']}xATR "
                  f"RR={cfg['exit']['reward_risk_ratio']}")
        pf = f"{stats['profit_factor']:.2f}" if stats["profit_factor"] is not None else "inf"
        win_pct = f"{stats['win_rate']:.1%}"
        pnl = f"{stats['total_pnl']:+.2f}"
        max_dd = f"{stats['max_drawdown']:+.2f}"
        print(f"{rank:<5}{stats['total_trades']:<8}{win_pct:<8}{pf:<8}{pnl:<12}{max_dd:<12}{params}")

    best_cfg, best_stats = results[0]
    print("\nBest candidate as a config/strategy.yaml override block "
          f"(paste under the '{args.symbol}' instrument entry - review before using, don't paste blindly):\n")
    print(f'    trend: {{fast_ema: {best_cfg["trend"]["fast_ema"]}, slow_ema: {best_cfg["trend"]["slow_ema"]}}}')
    print(f'    entry: {{pullback_atr_multiple: {best_cfg["entry"]["pullback_atr_multiple"]}, '
          f'rsi_oversold_recovery: {best_cfg["entry"]["rsi_oversold_recovery"]}, '
          f'rsi_overbought_recovery: {best_cfg["entry"]["rsi_overbought_recovery"]}}}')
    print(f'    exit: {{stop_atr_multiple: {best_cfg["exit"]["stop_atr_multiple"]}, '
          f'reward_risk_ratio: {best_cfg["exit"]["reward_risk_ratio"]}}}')
    print(f"\nThat candidate's numbers ({best_stats['total_trades']} trades, "
          f"PF {best_stats['profit_factor']}) - remember this was found by searching {len(candidates)} "
          "combinations against a few months of data, so some of this ranking is noise, not edge. "
          "Keep watching it on demo before trusting it live.")


if __name__ == "__main__":
    main()
