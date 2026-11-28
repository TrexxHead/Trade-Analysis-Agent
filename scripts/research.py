"""Run Kora's strategy research on your instruments and save the results.

    py -3.13 scripts/research.py                 # watchlist, from the best data source
    py -3.13 scripts/research.py XAUUSD V75      # specific instruments
    KORA_DATA_SOURCE=deriv py -3.13 scripts/research.py

Results appear in the hub under Strategy Lab -> Research. Strategies that pass
the out-of-sample test at >= 65% confidence are used by Kora automatically.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analyst import research  # noqa: E402
from src.analyst.config import get_settings, watchlist_codes  # noqa: E402
from src.analyst.strategies import history  # noqa: E402
from src.live.sources import resolve_source_name  # noqa: E402
from src.store.db import add_decision, get_quotes, kora_connection, kv_set  # noqa: E402


def main():
    conn = kora_connection()
    codes = [a.upper() for a in sys.argv[1:]] or watchlist_codes(get_settings(conn))
    src = resolve_source_name()
    quotes = get_quotes(conn)
    print(f"Researching {', '.join(codes)} on {src} data ({len(research.L.PRESETS)} strategies x {research.TFS})...")
    t0 = time.time()
    out = research.run_all(codes, lambda m, tf, n: history(m, tf, n, src), lambda c: research.spread_for(c, quotes),
                           progress=lambda c, p: print(f"  {c}: {p}", flush=True), settings=get_settings(conn))
    out["source"] = src
    kv_set(conn, "research", out)
    passed = research.promoted(out)
    add_decision(conn, f"RESEARCH — {len(passed)} strateg{'y' if len(passed) == 1 else 'ies'} passed out-of-sample testing", "b",
                 "Kora tested its strategy library on your instruments (60/40 chronological split, spread included, "
                 "multiple-testing adjusted). Passed: " + (", ".join(f"{r['name']} on {r['code']} {r['tf']} ({r['confidence']:.0f}%)" for r in passed) or "none"))
    print(f"\nDone in {time.time() - t0:.0f}s.")
    for code, d in out["instruments"].items():
        print(f"\n{code}  (spread {d['cost']:g}, bars {d['bars']})")
        for r in d["rows"]:
            o = r.get("oos_stats") or {}
            print(f"  {'PASS' if r['promoted'] else '    '} {r['name']:<36} {r.get('tf', ''):<4} "
                  f"IS {(r.get('is_stats') or {}).get('expectancy')}  OOS {o.get('expectancy')} n={o.get('n')} "
                  f"conf={r.get('confidence')}  - {r.get('note', '')}")
        for fam, f in d.get("families", {}).items():
            print(f"  family {fam:<18} members={f['members']} days={f['days']} exp/day={f.get('expectancy')} conf={f.get('confidence')} {'PASS' if f['promoted'] else ''}")
    for code, e in out["errors"].items():
        print(f"  {code}: ERROR {e}")


if __name__ == "__main__":
    main()
