"""Platform-agnostic trend-pullback idea generation.

Takes OHLC candles (source doesn't matter - MT4/5 and Deriv both normalize
to the same {open, high, low, close, time} shape) and the strategy config,
and returns a trade idea if one is present, otherwise None. Turning that
idea into an actual order (with a stop, a stake, a duration, whatever the
platform needs) is order_spec.py's job, not this module's.
"""
from src.strategy.indicators import atr, ema, rsi


def generate_idea(candles: list[dict], cfg: dict) -> dict | None:
    closes = [c["close"] for c in candles]

    fast = ema(closes, cfg["trend"]["fast_ema"])
    slow = ema(closes, cfg["trend"]["slow_ema"])
    if not fast or not slow:
        return None

    if fast[-1] > slow[-1]:
        trend = "up"
    elif fast[-1] < slow[-1]:
        trend = "down"
    else:
        return None

    atr_vals = atr(candles, cfg["exit"]["atr_period"])
    if not atr_vals:
        return None
    last_atr = atr_vals[-1]
    last_close = closes[-1]

    if abs(last_close - fast[-1]) > cfg["entry"]["pullback_atr_multiple"] * last_atr:
        return None  # price isn't currently pulled back to the fast EMA

    rsi_vals = rsi(closes, cfg["entry"]["rsi_period"])
    lookback = cfg["entry"]["rsi_lookback"]
    if len(rsi_vals) < lookback + 1:
        return None
    recent_rsi = rsi_vals[-(lookback + 1):]

    if trend == "up":
        threshold = cfg["entry"]["rsi_oversold_recovery"]
        if not (any(r < threshold for r in recent_rsi[:-1]) and recent_rsi[-1] >= threshold):
            return None
        direction = "long"
        rsi_note = f"dipped below {threshold} and is recovering"
    else:
        threshold = cfg["entry"]["rsi_overbought_recovery"]
        if not (any(r > threshold for r in recent_rsi[:-1]) and recent_rsi[-1] <= threshold):
            return None
        direction = "short"
        rsi_note = f"rose above {threshold} and is turning down"

    rationale = (
        f"Trend {trend} (EMA{cfg['trend']['fast_ema']}={fast[-1]:.5f} vs "
        f"EMA{cfg['trend']['slow_ema']}={slow[-1]:.5f}); price {last_close:.5f} is within "
        f"{cfg['entry']['pullback_atr_multiple']}x ATR{cfg['exit']['atr_period']} "
        f"({last_atr:.5f}) of the fast EMA; RSI{cfg['entry']['rsi_period']} {rsi_note} "
        f"(currently {recent_rsi[-1]:.1f})."
    )

    return {
        "direction": direction,
        "close": last_close,
        "atr": last_atr,
        "rsi": recent_rsi[-1],
        "rationale": rationale,
    }
