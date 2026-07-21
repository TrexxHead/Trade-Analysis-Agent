"""Shared "run a backtest, print a summary, write the JSON report" logic,
used by both scripts/run_backtest.py (CSV-sourced candles) and
scripts/backtest_live.py (MetaApi-sourced candles) so the two entry points
differ only in where candles come from - the JSON report shape is what the
dashboard's Backtests page reads, so it needs to stay identical regardless
of the source.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from src.analysis.metrics import compute_period_stats
from src.analysis.rules import evaluate_trades
from src.backtest.engine import run_backtest

OUT_DIR = Path(__file__).resolve().parents[2] / "backtests_out"


def run_and_report(candles: list[dict], strategy_cfg: dict, instrument_cfg: dict, rules_cfg: dict,
                    starting_balance: float, risk_pct: float, symbol_spec: dict) -> Path:
    symbol = instrument_cfg["symbol"]
    result = run_backtest(candles, strategy_cfg, instrument_cfg, starting_balance, risk_pct, symbol_spec)
    trades = result["trades"]

    stats = compute_period_stats(trades)
    mistake_counts: dict = {}
    if trades:
        flags = evaluate_trades(trades, rules_cfg)
        for flag_list in flags.values():
            for f in flag_list:
                mistake_counts[f["rule_id"]] = mistake_counts.get(f["rule_id"], 0) + 1

    print(f"\n{len(trades)} completed trades, starting balance {starting_balance:.2f} -> "
          f"ending {result['ending_balance']:.2f}")
    print("stats:", json.dumps(stats, indent=2))
    print("mistake_counts (against config/rules.yaml):", mistake_counts)
    if result["open_at_end"]:
        print("\nNote: a position was still open when the data ran out (not counted above):", result["open_at_end"])

    OUT_DIR.mkdir(exist_ok=True)
    safe_symbol = symbol.replace(" ", "_")
    out_path = OUT_DIR / f"{safe_symbol}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.json"
    out_path.write_text(json.dumps({
        "symbol": symbol,
        "candle_range": {"start": candles[0]["time"], "end": candles[-1]["time"], "count": len(candles)},
        "starting_balance": starting_balance,
        "ending_balance": result["ending_balance"],
        "stats": stats,
        "mistake_counts": mistake_counts,
        "trades": trades,
    }, indent=2, default=str))
    print(f"\nWrote {out_path}")
    return out_path
