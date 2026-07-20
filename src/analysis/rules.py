"""Checks closed trades against config/rules.yaml.

oversize_risk and daily_loss_limit_breached aren't evaluated yet - both need
the account balance immediately *before* each trade, which neither ingestion
module populates yet (balance_after is currently None on every trade). Wire
that up once real account data is flowing, then extend evaluate_trades below.
"""
from datetime import datetime

import yaml


def load_rules(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def evaluate_trades(trades: list[dict], rules: dict) -> dict[str, list[dict]]:
    flags: dict[str, list[dict]] = {t["id"]: [] for t in trades}
    if not trades:
        return flags

    behavior = rules["behavior"]
    risk_cfg = rules["risk"]
    sorted_trades = sorted(trades, key=lambda t: t["open_time"])

    for prev, cur in zip(sorted_trades, sorted_trades[1:]):
        if prev["pnl"] < 0:
            gap_minutes = (_parse(cur["open_time"]) - _parse(prev["close_time"])).total_seconds() / 60
            if 0 <= gap_minutes < behavior["min_minutes_between_trades_after_loss"]:
                flags[cur["id"]].append({
                    "rule_id": "revenge_trade",
                    "detail": f"Opened {gap_minutes:.1f} min after a loss on {prev['symbol']}",
                })

    for t in sorted_trades:
        hour = _parse(t["open_time"]).hour
        if not any(start <= hour < end for start, end in behavior["allowed_sessions_utc"]):
            flags[t["id"]].append({"rule_id": "outside_session", "detail": f"Opened at {hour:02d}:00 UTC"})

    by_day: dict[str, list[dict]] = {}
    for t in sorted_trades:
        day = _parse(t["open_time"]).date().isoformat()
        by_day.setdefault(day, []).append(t)
    for day, day_trades in by_day.items():
        if len(day_trades) > behavior["max_trades_per_day"]:
            for idx, t in enumerate(day_trades[behavior["max_trades_per_day"]:], start=behavior["max_trades_per_day"] + 1):
                flags[t["id"]].append({
                    "rule_id": "overtrading",
                    "detail": f"Trade #{idx} of {len(day_trades)} on {day}",
                })

    events = []
    for t in sorted_trades:
        events.append((_parse(t["open_time"]), 1, t))
        events.append((_parse(t["close_time"]), -1, t))
    events.sort(key=lambda e: (e[0], e[1]))  # closes before opens at the same instant

    open_count = 0
    for _, delta, t in events:
        if delta == 1:
            open_count += 1
            if open_count > risk_cfg["max_open_positions"]:
                flags[t["id"]].append({
                    "rule_id": "too_many_open_positions",
                    "detail": f"{open_count} positions open simultaneously",
                })
        else:
            open_count -= 1

    return flags
