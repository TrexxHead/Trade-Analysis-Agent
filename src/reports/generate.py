from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from src.analysis.metrics import compute_period_stats
from src.analysis.rules import evaluate_trades, load_rules
from src.store.db import get_connection, get_trades, set_trade_flags

RULES_PATH = Path(__file__).resolve().parents[2] / "config" / "rules.yaml"


def _day_start(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def period_bounds(period: str, reference_date: date) -> tuple[datetime, datetime]:
    if period == "daily":
        start = _day_start(reference_date)
        return start, start + timedelta(days=1)
    if period == "weekly":
        start = _day_start(reference_date - timedelta(days=reference_date.weekday()))
        return start, start + timedelta(days=7)
    if period == "monthly":
        start = _day_start(reference_date.replace(day=1))
        next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        return start, next_month
    raise ValueError(f"Unknown period: {period}")


def previous_period_bounds(period: str, start: datetime, end: datetime) -> tuple[datetime, datetime]:
    if period == "monthly":
        prev_end = start
        prev_start = (start.replace(day=1) - timedelta(days=1)).replace(day=1)
        return prev_start, prev_end
    length = end - start
    return start - length, start


def _flag_counts(flags_by_trade: dict[str, list[dict]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for flags in flags_by_trade.values():
        for f in flags:
            counts[f["rule_id"]] = counts.get(f["rule_id"], 0) + 1
    return counts


def build_report_data(period: str, reference_date: date | None = None) -> dict:
    reference_date = reference_date or datetime.now(timezone.utc).date()
    start, end = period_bounds(period, reference_date)
    prev_start, prev_end = previous_period_bounds(period, start, end)

    rules = load_rules(str(RULES_PATH))
    conn = get_connection()
    current_trades = get_trades(conn, start.isoformat(), end.isoformat())
    previous_trades = get_trades(conn, prev_start.isoformat(), prev_end.isoformat())

    current_flags = evaluate_trades(current_trades, rules)
    previous_flags = evaluate_trades(previous_trades, rules)

    for trade_id, flag_list in current_flags.items():
        set_trade_flags(conn, trade_id, flag_list)

    trades_by_id = {t["id"]: t for t in current_trades}

    return {
        "period": period,
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "previous_range": {"start": prev_start.isoformat(), "end": prev_end.isoformat()},
        "stats": compute_period_stats(current_trades),
        "previous_stats": compute_period_stats(previous_trades),
        "mistake_counts": _flag_counts(current_flags),
        "previous_mistake_counts": _flag_counts(previous_flags),
        "mistakes_detail": [
            {"trade_id": tid, "symbol": trades_by_id[tid]["symbol"], "close_time": trades_by_id[tid]["close_time"], "flags": flags}
            for tid, flags in current_flags.items() if flags
        ],
        "trades": current_trades,
    }
