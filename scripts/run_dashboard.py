#!/usr/bin/env python
"""Starts the local dashboard at http://127.0.0.1:<port> (default 5000).

Binds to 127.0.0.1 only, on purpose - this serves your trade history and
account P&L, so it isn't meant to be reachable from anywhere but your own
machine.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.dashboard.app import app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()

    print(f"Dashboard running at http://127.0.0.1:{args.port} (Ctrl+C to stop)")
    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    main()
