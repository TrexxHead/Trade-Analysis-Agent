"""Deterministic simulated market data, for developing and demoing the hub
without broker credentials (KORA_DATA_SOURCE=sim). Every screen shows a
SIMULATED badge while this is active - nothing here is real market data.
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

BASE_PRICES = {
    "STEP": (9234.5, 0.0009), "V10": (6450.0, 0.0011), "V25": (2145.32, 0.0028), "V75": (98432.11, 0.0085),
    "V100": (1624.08, 0.011), "BOOM500": (7821.4, 0.0016), "CRASH500": (5213.9, 0.0016),
    "BOOM1000": (14250.5, 0.0012), "CRASH1000": (6421.3, 0.0012),
    "XAUUSD": (3342.5, 0.0032), "XAGUSD": (38.42, 0.0045), "EURUSD": (1.0862, 0.0011), "GBPUSD": (1.2718, 0.0013),
}
TF_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}


ANCHOR = datetime(2026, 1, 1, tzinfo=timezone.utc)


def gen_candles(code: str, tf: str, count: int, end: datetime | None = None, seed_extra: int = 0) -> list[dict]:
    """One continuous simulated series per (code, tf), anchored at a fixed
    epoch, so the market genuinely evolves bar by bar over time."""
    base, vol = BASE_PRICES.get(code, (100.0, 0.003))
    minutes = TF_MINUTES[tf]
    vol *= math.sqrt(minutes / 60)
    end = end or datetime.now(timezone.utc)
    total = int((end - ANCHOR).total_seconds() // (minutes * 60)) + 1
    first = max(0, total - count)
    rnd = random.Random(f"{code}:{tf}:{seed_extra}")
    p, drift, out = base, 0.0, []
    for k in range(total):
        if k % 60 == 0:
            drift = rnd.gauss(0, vol * 0.35)
        o = p
        c = o * (1 + drift + rnd.gauss(0, vol))
        if code.startswith("BOOM") and rnd.random() < 0.01:
            c = o * (1 + vol * 8)
        if code.startswith("CRASH") and rnd.random() < 0.01:
            c = o * (1 - vol * 8)
        hw, lw, v = abs(rnd.gauss(0, vol * 0.5)), abs(rnd.gauss(0, vol * 0.5)), rnd.randint(50, 500)
        if k >= first:
            t = ANCHOR + timedelta(minutes=minutes * k)
            out.append({"time": t.isoformat(), "open": o, "high": max(o, c) * (1 + hw),
                        "low": min(o, c) * (1 - lw), "close": c, "volume": v})
        p = c
    scale = base / out[-1]["close"]
    for x in out:
        for key in ("open", "high", "low", "close"):
            x[key] *= scale
    return out
