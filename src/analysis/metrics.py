from datetime import datetime


def compute_period_stats(trades: list[dict]) -> dict:
    if not trades:
        return {"total_trades": 0}

    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    breakeven = [p for p in pnls if p == 0]

    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for p in pnls:
        cumulative += p
        peak = max(peak, cumulative)
        max_drawdown = min(max_drawdown, cumulative - peak)

    longest_win_streak = longest_loss_streak = current_streak = 0
    current_sign = 0
    for p in pnls:
        sign = 1 if p > 0 else (-1 if p < 0 else 0)
        current_streak = current_streak + 1 if sign == current_sign and sign != 0 else 1
        current_sign = sign
        if sign == 1:
            longest_win_streak = max(longest_win_streak, current_streak)
        elif sign == -1:
            longest_loss_streak = max(longest_loss_streak, current_streak)

    gross_win = sum(wins)
    gross_loss = abs(sum(losses))

    return {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(breakeven),
        "win_rate": len(wins) / len(trades),
        "total_pnl": cumulative,
        "avg_win": gross_win / len(wins) if wins else 0.0,
        "avg_loss": gross_loss / len(losses) if losses else 0.0,
        "profit_factor": (gross_win / gross_loss) if gross_loss else None,
        "max_drawdown": max_drawdown,
        "longest_win_streak": longest_win_streak,
        "longest_loss_streak": longest_loss_streak,
    }


def compute_daily_progress(trades: list[dict]) -> dict[str, dict]:
    """Per-day running P&L against that day's starting balance. Only uses
    trades with balance_after populated (skips the rest, e.g. Deriv trades
    where it currently isn't set) - start_balance for a day is derived from
    the first such trade that closes that day (balance_after - pnl), so it
    doesn't depend on a separate account snapshot.
    """
    daily: dict[str, dict] = {}
    with_balance = sorted(
        (t for t in trades if t.get("balance_after") is not None),
        key=lambda t: t["close_time"],
    )
    for t in with_balance:
        day = t["close_time"][:10]
        if day not in daily:
            daily[day] = {"start_balance": t["balance_after"] - t["pnl"], "pnl": 0.0}
        daily[day]["pnl"] += t["pnl"]
    return daily


def compute_r_multiple_stats(trades: list[dict]) -> dict:
    """R-multiple stats using each trade's annotated risk_amount - trades
    without one (the majority, until annotated) are excluded rather than
    guessed at, so this only reflects trades where the risk is actually
    known.
    """
    r_values = [t["pnl"] / t["risk_amount"] for t in trades if t.get("risk_amount")]
    if not r_values:
        return {"annotated_trades": 0}

    wins = [r for r in r_values if r > 0]
    losses = [r for r in r_values if r < 0]
    win_rate = len(wins) / len(r_values)
    avg_win_r = sum(wins) / len(wins) if wins else 0.0
    avg_loss_r = sum(losses) / len(losses) if losses else 0.0
    expectancy_r = win_rate * avg_win_r + (1 - win_rate) * avg_loss_r

    return {
        "annotated_trades": len(r_values),
        "avg_r": sum(r_values) / len(r_values),
        "avg_win_r": avg_win_r,
        "avg_loss_r": avg_loss_r,
        "expectancy_r": expectancy_r,
    }


def _breakdown(trades: list[dict], key_fn) -> list[dict]:
    groups: dict = {}
    for t in trades:
        key = key_fn(t)
        if key is None:
            continue
        groups.setdefault(key, []).append(t)

    result = []
    for key, group_trades in groups.items():
        stats = compute_period_stats(group_trades)
        result.append({"key": key, **stats})
    return result


def breakdown_by_hour(trades: list[dict]) -> list[dict]:
    return sorted(_breakdown(trades, lambda t: datetime.fromisoformat(t["open_time"]).hour), key=lambda r: r["key"])


def breakdown_by_weekday(trades: list[dict]) -> list[dict]:
    names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    rows = _breakdown(trades, lambda t: datetime.fromisoformat(t["open_time"]).weekday())
    for row in rows:
        row["key"] = names[row["key"]]
    return sorted(rows, key=lambda r: names.index(r["key"]))


def breakdown_by_setup(trades: list[dict]) -> list[dict]:
    return sorted(_breakdown(trades, lambda t: t.get("setup")), key=lambda r: -r["total_pnl"])


def breakdown_by_emotion(trades: list[dict]) -> list[dict]:
    return sorted(_breakdown(trades, lambda t: t.get("emotion")), key=lambda r: -r["total_pnl"])
