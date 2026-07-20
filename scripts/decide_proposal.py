#!/usr/bin/env python
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from src.store.db import get_connection, list_proposals
from src.strategy.execution import execute_proposal, reject_proposal

load_dotenv()


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="list pending proposals")
    group.add_argument("--approve", type=int, metavar="ID")
    group.add_argument("--reject", type=int, metavar="ID")
    parser.add_argument("--note", default=None, help="reason, recorded with a rejection")
    parser.add_argument("--live", action="store_true", help="allow execution against a non-demo account (danger)")
    args = parser.parse_args()

    conn = get_connection()

    if args.list:
        for p in list_proposals(conn, status="pending"):
            spec = json.loads(p["order_spec_json"])
            print(f"#{p['id']} [{p['platform']}] {p['symbol']} {p['direction']}")
            print(f"    {p['rationale']}")
            print(f"    order: {spec}")
        return

    if args.approve is not None:
        result = execute_proposal(conn, args.approve, require_demo=not args.live)
        print(f"Executed proposal #{args.approve}: {result}")
        return

    if args.reject is not None:
        reject_proposal(conn, args.reject, note=args.note)
        print(f"Rejected proposal #{args.reject}")


if __name__ == "__main__":
    main()
