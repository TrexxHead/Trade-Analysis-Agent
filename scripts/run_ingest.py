#!/usr/bin/env python
import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from src.store.db import get_connection, upsert_trades

load_dotenv()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["deriv", "mt4", "all"], default="all")
    parser.add_argument("--days-back", type=int, default=30, help="how far back to pull on this run")
    args = parser.parse_args()

    date_to = datetime.now(timezone.utc)
    date_from = date_to - timedelta(days=args.days_back)

    conn = get_connection()
    total = 0

    if args.source in ("deriv", "all"):
        from src.ingest.deriv import fetch_trades as fetch_deriv
        trades = fetch_deriv(date_from, date_to)
        total += upsert_trades(conn, trades)
        print(f"Deriv: {len(trades)} trades fetched")

    if args.source in ("mt4", "all"):
        from src.ingest.metatrader import fetch_trades as fetch_mt
        trades = fetch_mt(date_from, date_to)
        total += upsert_trades(conn, trades)
        print(f"MT4/5: {len(trades)} trades fetched")

    print(f"Stored/updated {total} trade rows")


if __name__ == "__main__":
    main()
