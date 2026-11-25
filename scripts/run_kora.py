#!/usr/bin/env python
"""Runs Kora, the market-analysis agent: live feed + scheduled multi-timeframe
analysis of the watchlist + alerts. Kora never places orders.

    python scripts/run_kora.py                    # real data via MetaApi (needs .env)
    KORA_DATA_SOURCE=sim python scripts/run_kora.py   # simulated data, for demos
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

from src.analyst.service import KoraService  # noqa: E402

if __name__ == "__main__":
    try:
        asyncio.run(KoraService().run())
    except KeyboardInterrupt:
        print("\nKora stopped.")
