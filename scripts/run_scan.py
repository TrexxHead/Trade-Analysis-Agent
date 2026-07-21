#!/usr/bin/env python
"""Scans every configured instrument for a trade-plan-baseline setup and
files any hits as pending proposals. Nothing gets executed here - that's
scripts/decide_proposal.py's job, on purpose, so a human always signs off
before an order is placed.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml
from dotenv import load_dotenv

from src.ingest import deriv, metatrader
from src.store.db import create_proposal, get_connection, has_pending_proposal, upsert_scan_status
from src.strategy import scanning
from src.strategy.order_spec import build_order_spec

load_dotenv()

STRATEGY_PATH = Path(__file__).resolve().parents[1] / "config" / "strategy.yaml"
RULES_PATH = Path(__file__).resolve().parents[1] / "config" / "rules.yaml"


def _balance_for_platform(platform: str, cache: dict) -> float:
    if platform not in cache:
        if platform == "mt4_mt5":
            cache[platform] = metatrader.get_account_info()["balance"]
        else:
            cache[platform] = deriv.get_account()["balance"]
    return cache[platform]


def main():
    strategy_cfg = yaml.safe_load(STRATEGY_PATH.read_text())
    rules_cfg = yaml.safe_load(RULES_PATH.read_text())
    risk_pct = rules_cfg["risk"]["max_risk_per_trade_pct"]

    conn = get_connection()
    balance_cache: dict = {}
    new_proposals = []

    for instrument in strategy_cfg["instruments"]:
        symbol = instrument["symbol"]
        cfg = scanning.merged_cfg(strategy_cfg, instrument)

        # A broken/unreachable/misnamed instrument shouldn't stop the rest of
        # the watchlist from being scanned - record the failure and move on.
        try:
            if has_pending_proposal(conn, symbol):
                print(f"{symbol}: skipping, already has a pending proposal")
                continue

            candles = scanning.candles_for(instrument, cfg["timeframe"], cfg["lookback_candles"])
            idea = scanning.generate_idea(candles, cfg)
            if idea is None:
                print(f"{symbol}: no setup")
                upsert_scan_status(conn, symbol, instrument["platform"], cfg["timeframe"])
                continue

            symbol_spec = metatrader.get_symbol_spec(symbol) if instrument["platform"] == "mt4_mt5" else None
            balance = _balance_for_platform(instrument["platform"], balance_cache)
            order_spec = build_order_spec(idea, instrument, cfg, balance, risk_pct, symbol_spec)

            proposal_id = create_proposal(conn, instrument["platform"], symbol, idea["direction"], order_spec, idea["rationale"])
            new_proposals.append(proposal_id)
            upsert_scan_status(conn, symbol, instrument["platform"], cfg["timeframe"],
                                direction=idea["direction"], rationale=idea["rationale"])
            print(f"{symbol}: NEW PROPOSAL #{proposal_id} ({idea['direction']}) - {idea['rationale']}")
        except Exception as e:
            print(f"{symbol}: scan failed - {e}", file=sys.stderr)
            upsert_scan_status(conn, symbol, instrument["platform"], cfg["timeframe"], error=str(e))

    print(f"\n{len(new_proposals)} new proposal(s): {new_proposals}")


if __name__ == "__main__":
    main()
