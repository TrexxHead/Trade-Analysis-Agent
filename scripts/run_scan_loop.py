#!/usr/bin/env python
"""Runs run_scan.py's scan on a repeating interval, so trade proposals show
up in the dashboard automatically instead of needing a manual run each
time. Meant to run continuously in its own terminal (or as a background
process) alongside the dashboard - approvals still happen there, or via
decide_proposal.py; this script only ever proposes, never executes.
"""
import argparse
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run_scan


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval-minutes", type=float, default=20.0,
                         help="how often to scan, in minutes (default 20)")
    args = parser.parse_args()

    print(f"Scanning every {args.interval_minutes} minute(s). Ctrl+C to stop.")
    try:
        while True:
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            print(f"\n=== scan at {timestamp} ===")
            try:
                run_scan.main()
            except Exception:
                print("Scan failed, will retry next interval:")
                traceback.print_exc()
            time.sleep(args.interval_minutes * 60)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
