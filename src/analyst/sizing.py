"""Position sizing for Kora's plans - display only, never an order.

Uses the broker's own contract details (from MT5: tick size, tick value,
minimum lot, lot step) and the account's equity/free margin to answer:
how many lots for my risk %, what that risks in dollars, what margin it
ties up, and whether the trade is even possible at the minimum lot.
"""
from __future__ import annotations

import math


def size_plan(cand: dict, spec: dict | None, equity: float | None, risk_pct: float = 1.0,
              free_margin: float | None = None, margin_per_lot: float | None = None,
              max_risk_pct: float = 3.0) -> dict:
    out = {"risk_pct_target": risk_pct}
    if not spec or not equity or equity <= 0:
        out.update(tradable=None, reason="Needs account equity and broker contract details (connect MT5).")
        return out
    tick_size, tick_value = spec.get("tick_size") or spec.get("point"), spec.get("tick_value")
    vmin, vstep = spec.get("volume_min") or 0.01, spec.get("volume_step") or spec.get("volume_min") or 0.01
    stop_dist = abs(cand["entry"] - cand["stop"])
    if not tick_size or not tick_value or stop_dist <= 0:
        out.update(tradable=None, reason="Broker contract details incomplete for this symbol.")
        return out
    risk_per_lot = stop_dist / tick_size * tick_value          # $ lost at the stop per 1.0 lot
    target_usd = equity * risk_pct / 100
    lots = math.floor(target_usd / risk_per_lot / vstep) * vstep
    min_lot_risk = vmin * risk_per_lot
    out.update(risk_per_lot=risk_per_lot, volume_min=vmin, min_lot_risk=min_lot_risk,
               min_lot_risk_pct=min_lot_risk / equity * 100)
    if lots < vmin:
        lots = vmin
        tradable = min_lot_risk / equity * 100 <= max_risk_pct
        reason = (f"Minimum lot {vmin:g} risks ${min_lot_risk:,.2f} ({min_lot_risk / equity * 100:.0f}% of equity)"
                  + ("" if tradable else f" - above your {max_risk_pct:g}% ceiling, so skip or wait for a tighter stop"))
    else:
        tradable, reason = True, None
    risk_usd = lots * risk_per_lot
    margin = (margin_per_lot or 0) * lots if margin_per_lot else None
    if margin and free_margin is not None and margin > free_margin:
        tradable, reason = False, f"Needs ${margin:,.2f} margin but only ${free_margin:,.2f} is free"
    out.update(lots=round(lots, 3), risk_usd=risk_usd, risk_pct=risk_usd / equity * 100, margin=margin,
               reward_usd=risk_usd * cand.get("rr1", 0), tradable=tradable, reason=reason)
    return out


def rating(cand: dict) -> float:
    """0-10 score blending calibrated confidence, reward:risk and the track record."""
    conf = cand.get("confidence", 0) / 10                       # 0-10
    rr_bonus = max(-1.0, min(1.0, (cand.get("rr1", 1.5) - 1.5)))  # +-1 for unusually good/poor RR
    exp = (cand.get("calibration") or {}).get("expectancy")
    exp_bonus = 0 if exp is None else max(-1.5, min(1.5, exp * 3))
    return round(max(0.0, min(10.0, conf + rr_bonus * 0.5 + exp_bonus * 0.5)), 1)
