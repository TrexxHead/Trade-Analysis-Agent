#!/usr/bin/env python
"""Kora's "training": replays every setup detector bar-by-bar over each
instrument's H1 history (no lookahead) and records how each setup actually
played out - hit rate to TP1 and expectancy in R - per instrument and
direction. Live analysis then tempers each setup's confidence with that
track record (see src/analyst/calibration.py).

    python scripts/train_kora.py                  # every instrument, 180 days
    python scripts/train_kora.py --code XAUUSD --days 365
    KORA_DATA_SOURCE=sim python scripts/train_kora.py   # simulated history (demo only)

Re-run monthly-ish as history accumulates. Small samples are shrunk toward
break-even on purpose - a handful of lucky wins shouldn't read as an edge.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os

from dotenv import load_dotenv

load_dotenv()

from src.analyst.calibration import train_instrument  # noqa: E402
from src.analyst.config import instruments  # noqa: E402
from src.store.db import add_decision, kora_connection, save_setup_stats  # noqa: E402


def history(meta: dict, days: int) -> list[dict]:
    from src.live.sources import fetch_candles_sync, resolve_source_name
    name = resolve_source_name()
    if name == "sim":
        from src.live.sim import gen_candles
        return gen_candles(meta["code"], "H1", days * 24)
    if name == "mt5":
        from src.ingest.mt5_local import candle_history
        return candle_history(meta["symbol"], "H1", days)
    if name == "metaapi":
        from src.ingest.metatrader import fetch_candle_history
        return fetch_candle_history(meta["symbol"], "H1", days)
    return fetch_candles_sync(meta, "H1", min(days * 24, 5000), "deriv")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", help="instrument code from config/instruments.yaml (default: all)")
    ap.add_argument("--days", type=int, default=180)
    args = ap.parse_args()
    conn = kora_connection()
    from src.live.sources import resolve_source_name
    print(f"History source: {resolve_source_name()}")
    todo = [m for m in instruments() if not args.code or m["code"] == args.code.upper()]
    if not todo:
        raise SystemExit(f"Unknown code {args.code}")
    summary = []
    for m in todo:
        try:
            h1 = history(m, args.days)
            print(f"{m['code']}: {len(h1)} H1 candles ({h1[0]['time'][:10]} → {h1[-1]['time'][:10]})" if h1 else f"{m['code']}: no data")
            if len(h1) < 400:
                print("  not enough history - skipped")
                continue
            rows = train_instrument(h1, m)
            save_setup_stats(conn, m["code"], rows, "H1", args.days)
            for r in rows:
                print(f"  {r['setup']:<17} {r['direction']:<5} n={r['n']:<4} hit={r['wins'] / r['n']:.0%}  exp={r['expectancy']:+.2f}R")
            best = max(rows, key=lambda r: r["expectancy"] if r["n"] >= 15 else -9, default=None)
            if best:
                summary.append(f"{m['code']} best: {best['setup']} {best['direction']} ({best['expectancy']:+.2f}R, n={best['n']})")
        except Exception as e:
            print(f"  {m['code']} failed: {e}", file=sys.stderr)
    from src.store.db import kv_set
    kv_set(conn, "trained_source", resolve_source_name())
    add_decision(conn, f"TRAIN — calibrated {len(todo)} instrument(s) on {args.days}d of H1", "b",
                 "Replayed every setup detector bar-by-bar with no lookahead and recorded TP1/stop outcomes. "
                 + ("; ".join(summary) if summary else "No instrument had enough history."))


if __name__ == "__main__":
    main()
