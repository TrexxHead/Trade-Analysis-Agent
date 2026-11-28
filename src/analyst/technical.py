"""Per-timeframe technical read and multi-timeframe synthesis.

`Features` precomputes every indicator once over a candle series; `read_at`
summarises the state at bar i (only using data up to i). Live analysis uses
i = last bar; calibration walks i forward through history.
"""
from __future__ import annotations

from src.analyst import ta

TF_WEIGHTS = {"D1": 0.2, "H4": 0.35, "H1": 0.3, "M15": 0.15}


class Features:
    def __init__(self, candles: list[dict]):
        self.c = candles
        self.n = len(candles)
        closes = [x["close"] for x in candles]
        self.close = closes
        self.ema20 = ta.ema(closes, 20)
        self.ema50 = ta.ema(closes, 50)
        self.ema200 = ta.ema(closes, 200)
        self.rsi = ta.rsi(closes, 14)
        self.atr = ta.atr(candles, 14)
        self.atr_pct = ta.rolling_percentile(self.atr, 100)
        _, _, self.macd_hist = ta.macd(closes)
        self.adx, self.pdi, self.mdi = ta.adx(candles, 14)
        self.bbw_pct = ta.rolling_percentile(ta.bollinger_width(closes, 20), 100)
        self.vwap = ta.session_vwap(candles)
        self.swings = ta.swings(candles, 3)

    # -- helpers -----------------------------------------------------------
    def known_swings(self, i: int, lookback: int = 150) -> list[dict]:
        return [s for s in self.swings if s["confirmed"] <= i and s["i"] >= i - lookback]

    def levels(self, i: int, lookback: int = 150) -> list[dict]:
        a = self.atr[i] or 0
        return ta.cluster_levels(self.known_swings(i, lookback), tolerance=0.5 * a) if a else []

    def structure(self, i: int) -> str:
        sw = self.known_swings(i, 120)
        highs = [s["price"] for s in sw if s["type"] == "high"][-2:]
        lows = [s["price"] for s in sw if s["type"] == "low"][-2:]
        if len(highs) < 2 or len(lows) < 2:
            return "unclear"
        hh, hl = highs[1] > highs[0], lows[1] > lows[0]
        if hh and hl:
            return "HH/HL"
        if not hh and not hl:
            return "LH/LL"
        return "mixed"


def trend_score(f: Features, i: int) -> float | None:
    """-1 (strong down) .. +1 (strong up) from price/EMA stacking + slope."""
    c, e20, e50, e200 = f.close[i], f.ema20[i], f.ema50[i], f.ema200[i]
    if e50 is None:
        return None
    pts = 0.0
    pts += 1 if c > e20 else -1 if e20 is not None else 0
    pts += 1 if c > e50 else -1
    if e200 is not None:
        pts += 1 if c > e200 else -1
        pts += 1 if e50 > e200 else -1
    if e20 is not None:
        pts += 1 if e20 > e50 else -1
    prev = f.ema50[i - 10] if i >= 10 else None
    a = f.atr[i]
    if prev is not None and a:
        slope = (e50 - prev) / a
        pts += max(-1.0, min(1.0, slope))
    return pts / 6.0


def read_at(f: Features, i: int, tf: str) -> dict:
    ts = trend_score(f, i)
    a, adx_v = f.atr[i], f.adx[i]
    struct = f.structure(i)
    direction = "flat" if ts is None or abs(ts) < 0.25 else ("up" if ts > 0 else "down")
    vol_p = f.atr_pct[i]
    vol_state = "unknown" if vol_p is None else "compressed" if vol_p < 25 else "expanding" if vol_p > 75 else "normal"
    levels = f.levels(i)
    price = f.close[i]
    sup = max((lv for lv in levels if lv["price"] < price), key=lambda lv: lv["price"], default=None)
    res = min((lv for lv in levels if lv["price"] > price), key=lambda lv: lv["price"], default=None)
    mh = f.macd_hist[i]
    mh_prev = f.macd_hist[i - 1] if i else None
    momentum = None
    if mh is not None and mh_prev is not None:
        momentum = ("rising" if mh > mh_prev else "falling") + (" (+)" if mh > 0 else " (-)")
    return {
        "tf": tf,
        "time": f.c[i]["time"],
        "close": price,
        "trend_score": round(ts, 2) if ts is not None else None,
        "direction": direction,
        "structure": struct,
        "ema20": f.ema20[i], "ema50": f.ema50[i], "ema200": f.ema200[i],
        "rsi": round(f.rsi[i], 1) if f.rsi[i] is not None else None,
        "adx": round(adx_v, 1) if adx_v is not None else None,
        "di_bias": None if f.pdi[i] is None else ("+DI" if f.pdi[i] > f.mdi[i] else "-DI"),
        "atr": a,
        "atr_pct": round(vol_p) if vol_p is not None else None,
        "volatility": vol_state,
        "bb_squeeze": f.bbw_pct[i] is not None and f.bbw_pct[i] < 15,
        "momentum": momentum,
        "vwap": f.vwap[i],
        "support": sup["price"] if sup else None,
        "support_touches": sup["touches"] if sup else None,
        "resistance": res["price"] if res else None,
        "resistance_touches": res["touches"] if res else None,
        "levels": [{"price": lv["price"], "touches": lv["touches"]} for lv in levels],
    }


def regime(reads: dict[str, dict]) -> dict:
    """Classify the market from the H1/H4 reads into the five regime chips
    the hub shows: Bullish / Bearish / Range / Volatile / Trending."""
    h1, h4 = reads.get("H1") or {}, reads.get("H4") or {}
    adx_h1, adx_h4 = h1.get("adx") or 0, h4.get("adx") or 0
    tags = []
    trending = max(adx_h1, adx_h4) >= 25 and h1.get("direction") == h4.get("direction") != "flat"
    ranging = max(adx_h1, adx_h4) < 20
    if trending:
        tags.append("Trending")
    elif ranging:
        tags.append("Range")
    if (h1.get("atr_pct") or 0) >= 80:
        tags.append("Volatile")
    score = mtf_score(reads)
    if score is not None and score >= 0.25:
        tags.append("Bullish")
    elif score is not None and score <= -0.25:
        tags.append("Bearish")
    primary = "Trending" if trending else "Range" if ranging else "Transitional"
    return {"tags": tags, "primary": primary}


def mtf_score(reads: dict[str, dict]) -> float | None:
    tot = wsum = 0.0
    for tf, w in TF_WEIGHTS.items():
        s = (reads.get(tf) or {}).get("trend_score")
        if s is not None:
            tot += s * w
            wsum += w
    return tot / wsum if wsum else None


def alignment(reads: dict[str, dict]) -> tuple[int, int]:
    """(# timeframes agreeing with the weighted bias, # timeframes read)."""
    s = mtf_score(reads)
    dirs = [r.get("direction") for r in reads.values() if r]
    if s is None or not dirs:
        return 0, len(dirs)
    want = "up" if s > 0 else "down"
    return sum(1 for d in dirs if d == want), len(dirs)
