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
