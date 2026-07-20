"""Pure-Python indicators - no pandas/numpy dependency.

Each function returns a list aligned to the *end* of the input (i.e. the
last element always corresponds to the most recent candle), even though
warm-up periods mean the lists have different lengths and starting offsets.
Always index from the end (e.g. `ema(...)[-1]`) rather than trying to
line series up by absolute position.
"""


def ema(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        return []
    multiplier = 2 / (period + 1)
    result = [sum(values[:period]) / period]
    for price in values[period:]:
        result.append((price - result[-1]) * multiplier + result[-1])
    return result


def rsi(values: list[float], period: int) -> list[float]:
    if len(values) < period + 1:
        return []
    deltas = [values[i + 1] - values[i] for i in range(len(values) - 1)]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    result = [100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)]

    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        result.append(100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss))

    return result


def atr(candles: list[dict], period: int) -> list[float]:
    true_ranges = []
    for i in range(1, len(candles)):
        high, low, prev_close = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))

    if len(true_ranges) < period:
        return []
    result = [sum(true_ranges[:period]) / period]
    for tr in true_ranges[period:]:
        result.append((result[-1] * (period - 1) + tr) / period)
    return result
