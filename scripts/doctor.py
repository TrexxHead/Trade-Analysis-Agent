#!/usr/bin/env python
"""Checks which live data sources Kora can use on this machine and why not.

    python scripts/doctor.py

Paste the output to Claude if something isn't connecting.
"""
import asyncio
import os
import platform
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

from src.analyst.config import instruments, watchlist_codes  # noqa: E402


def ok(msg):
    print(f"  [ok]   {msg}")


def bad(msg):
    print(f"  [!!]   {msg}")


def main():
    print(f"Python {sys.version.split()[0]} ({platform.architecture()[0]}) on {platform.system()} {platform.release()}")
    print(f"KORA_DATA_SOURCE = {os.environ.get('KORA_DATA_SOURCE') or '(unset -> auto)'}")
    if (os.environ.get("KORA_DATA_SOURCE") or "").lower() == "sim":
        bad("KORA_DATA_SOURCE is 'sim' - remove it from .env / your shell to use real data")
    wl = watchlist_codes()
    cat = {m["code"]: m for m in instruments()}
    print(f"Watchlist: {', '.join(wl)}")

    print("\n1) MT5 terminal (free, full account mirror)")
    try:
        import MetaTrader5 as mt5  # noqa: F401
        ok(f"MetaTrader5 package installed ({getattr(mt5, '__version__', '?')})")
        from src.ingest import mt5_local
        mt5_local.connect()
        a = mt5_local.account()
        ok(f"Connected to terminal - account {a.get('login')} on {a.get('server')} ({a.get('type')}), "
           f"balance {a.get('balance')} {a.get('currency')}")
        h = mt5_local.health()
        if h.get("broker"):
            ok("Terminal is connected to the broker's trade server")
        else:
            bad("Terminal is open but NOT connected to the broker (bottom-right of MT5 shows 'No connection' / "
                "'Invalid account'?) - log in again in MT5: File > Login to Trade Account")
        for code in wl:
            m = cat.get(code)
            try:
                mt5_local.ensure_symbol(m["symbol"])
                t = mt5_local.tick(m["symbol"])
                ok(f"{code:<9} '{m['symbol']}' price {t['price'] if t else 'no tick (market closed?)'}")
            except Exception as e:
                bad(f"{code:<9} {e}")
    except ImportError:
        bad("MetaTrader5 package not installed - run: pip install MetaTrader5   (Windows only)")
    except Exception as e:
        bad(str(e))
        print("         Open the Deriv MT5 app, log in, then re-run. If it's open, set MT5_PATH in .env "
              "to the full path of terminal64.exe")

    print("\n2) Deriv public feed (free, prices + candles only)")
    try:
        from src.live.sources import fetch_candles_sync
        for code in wl:
            m = cat.get(code)
            if not m.get("deriv_symbol"):
                print(f"  [--]   {code:<9} no Deriv symbol configured")
                continue
            try:
                c = fetch_candles_sync(m, "H1", 3, "deriv")
                ok(f"{code:<9} {m['deriv_symbol']} last H1 close {c[-1]['close']}")
            except Exception as e:
                bad(f"{code:<9} {e}")
    except Exception as e:
        bad(f"Deriv feed unreachable: {e}")

    from src.live.sources import resolve_source_name
    print(f"\nKora will use: {resolve_source_name()}")


if __name__ == "__main__":
    main()
