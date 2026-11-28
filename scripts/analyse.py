#!/usr/bin/env python
"""One-shot Kora breakdown for a single instrument, printed to the terminal
(the same analysis the service runs; nothing is saved).

    python scripts/analyse.py XAUUSD
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

from src.analyst.config import by_code, load_watchlist  # noqa: E402
from src.analyst.engine import analyse_instrument  # noqa: E402
from src.analyst.fundamentals import fetch_calendar, instrument_fundamentals  # noqa: E402
from src.live.sources import make_source  # noqa: E402
from src.store.db import get_setup_stats, kora_connection  # noqa: E402


async def main(code: str):
    meta = by_code().get(code.upper())
    if not meta:
        raise SystemExit(f"Unknown code {code}. Options: {', '.join(by_code())}")
    wl = load_watchlist()
    src = make_source([meta])
    if src.name == "sim":
        await src.start()
    candles = {tf: await src.candles(meta["symbol"], tf, n) for tf, n in wl["timeframes"].items()}
    rep = analyse_instrument(meta, candles, wl.get("execution_timeframe", "H1"),
                             instrument_fundamentals(meta, fetch_calendar(), {}),
                             get_setup_stats(kora_connection(), meta["code"]))
    d = rep["digits"]
    print(f"\n{rep['name']} ({rep['code']})  {rep['price']:,.{d}f}   bias {rep['bias']} {rep['confidence']}%  "
          f"regime {rep['regime']['primary']}  aligned {rep['alignment']}")
    for tf, r in rep["reads"].items():
        print(f"  {tf:<4} {r['direction']:<5} score {r['trend_score']}  RSI {r['rsi']}  ADX {r['adx']}  {r['structure']}  vol {r['volatility']}")
    print("\nFundamentals:", *rep["fundamentals"]["notes"], sep="\n  ")
    print("\nSetups:" if rep["candidates"] else "\nNo setups.")
    for c in rep["candidates"]:
        print(f"  [{c['grade']}] {c['title']}  {c['confidence']}%\n      {c['levels_text']}")
        print("      + " + "\n      + ".join(c["confluence"]))
        for a in c["against"]:
            print(f"      - {a}")
        print(f"      {c['calibration']['note']}\n      Invalidation: {c['invalidation']}")
    print(f"\nVerdict: {rep['verdict']}\n")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "XAUUSD"))
