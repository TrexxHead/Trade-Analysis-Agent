"""Kora setup backtests, run on demand from the hub (Backtests page).

Pulls history for one instrument from the live data source (your MT5
terminal or Deriv's free feed), replays Kora's setup detectors bar by bar
with no lookahead, and reports every simulated trade, an equity curve (in R,
and in $ at a fixed risk per trade) plus per-setup stats. Results are saved
to backtests_out/ alongside the older CSV-based backtests.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.analyst.calibration import replay
from src.analyst.setups import SETUP_NAMES

OUT = Path(__file__).resolve().parents[2] / "backtests_out"
HTF_HOURS = {"M15": 1, "M30": 2, "H1": 4, "H4": 24, "D1": 0}
BARS_PER_DAY = {"M15": 96, "M30": 48, "H1": 24, "H4": 6, "D1": 1}


def history(meta: dict, tf: str, days: int) -> list[dict]:
    from src.live.sources import fetch_candles_sync, resolve_source_name
    name = resolve_source_name()
    if name == "mt5":
        from src.ingest.mt5_local import candle_history
        return candle_history(meta["symbol"], tf, days)
    if name == "metaapi":
        from src.ingest.metatrader import fetch_candle_history
        return fetch_candle_history(meta["symbol"], tf, days)
    return fetch_candles_sync(meta, tf, min(days * BARS_PER_DAY[tf], 5000), name)


def run(meta: dict, tf: str = "H1", days: int = 180, setups: list[str] | None = None,
        risk_usd: float = 100.0, candles: list[dict] | None = None) -> dict:
    if tf not in HTF_HOURS:
        raise ValueError(f"Backtests support {', '.join(HTF_HOURS)}")
    candles = candles if candles is not None else history(meta, tf, days)
    if len(candles) < 260:
        raise ValueError(f"Only {len(candles)} {tf} candles available - need at least 260. Try more days or a lower timeframe.")
    trades = replay(candles, meta, htf_hours=HTF_HOURS[tf], setups=set(setups) if setups else None)
    cum = peak = mdd = 0.0
    curve = []
    for t in trades:
        cum += t["r"]
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)
        curve.append(round(cum, 3))
    rs = [t["r"] for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    by_setup = {}
    for t in trades:
        b = by_setup.setdefault(t["setup"], {"setup": t["setup"], "name": SETUP_NAMES.get(t["setup"], t["setup"]), "rs": []})
        b["rs"].append(t["r"])
    setup_rows = []
    for b in by_setup.values():
        r = b.pop("rs")
        w = [x for x in r if x > 0]
        setup_rows.append({**b, "n": len(r), "win_rate": len(w) / len(r) * 100, "expectancy": sum(r) / len(r), "total_r": sum(r)})
    stats = {
        "trades": len(trades), "win_rate": len(wins) / len(rs) * 100 if rs else None,
        "expectancy_r": sum(rs) / len(rs) if rs else None, "total_r": sum(rs),
        "profit_factor": (sum(wins) / abs(sum(losses))) if losses and sum(losses) else None,
        "max_dd_r": mdd, "net_usd": sum(rs) * risk_usd, "max_dd_usd": mdd * risk_usd,
    }
    return {
        "kind": "kora", "code": meta["code"], "symbol": meta["symbol"], "tf": tf, "days": days,
        "setups": setups or sorted(SETUP_NAMES), "risk_usd": risk_usd,
        "candle_range": {"start": candles[0]["time"], "end": candles[-1]["time"], "count": len(candles)},
        "ran_at": datetime.now(timezone.utc).isoformat(), "stats": stats,
        "by_setup": sorted(setup_rows, key=lambda r: -r["total_r"]), "curve": curve, "trades": trades,
    }


def save(result: dict) -> str:
    OUT.mkdir(exist_ok=True)
    name = f"kora_{result['code']}_{result['tf']}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    (OUT / name).write_text(json.dumps(result, default=str))
    return name
