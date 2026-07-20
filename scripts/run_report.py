#!/usr/bin/env python
import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.reports.generate import build_report_data

OUT_DIR = Path(__file__).resolve().parents[1] / "reports_out"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", choices=["daily", "weekly", "monthly"], required=True)
    parser.add_argument("--date", type=date.fromisoformat, default=None, help="YYYY-MM-DD, defaults to today (UTC)")
    args = parser.parse_args()

    report = build_report_data(args.period, args.date)

    OUT_DIR.mkdir(exist_ok=True)
    out_path = OUT_DIR / f"{args.period}_{report['range']['start'][:10]}.json"
    out_path.write_text(json.dumps(report, indent=2, default=str))
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
