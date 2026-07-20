"""Turns a platform-agnostic trade idea into the concrete order a given
platform actually needs. This is where the products stop looking alike:

- mt4_mt5: real price-level stop-loss/take-profit; position size is derived
  from risk_amount and the instrument's pip value so a fixed % of balance
  is at stake regardless of the symbol.
- deriv_multipliers: stop-loss/take-profit are monetary amounts, not price
  levels. Deriv Multipliers already cap max loss at the stake by design, so
  using risk_amount as the stake directly makes "risk %" and "max loss" the
  same number - that's the whole simplification this product offers.
- deriv_options (Rise/Fall-style): no stop-loss concept exists. Max loss is
  the stake, full stop. "Exit" is just contract duration, fixed up front.
"""


def build_order_spec(idea: dict, instrument_cfg: dict, strategy_cfg: dict,
                      account_balance: float, risk_pct: float, symbol_spec: dict | None = None) -> dict:
    platform = instrument_cfg["platform"]
    symbol = instrument_cfg["symbol"]
    direction = idea["direction"]
    risk_amount = account_balance * risk_pct / 100

    if platform == "mt4_mt5":
        stop_distance = strategy_cfg["exit"]["stop_atr_multiple"] * idea["atr"]
        reward_ratio = strategy_cfg["exit"]["reward_risk_ratio"]
        if direction == "long":
            stop = idea["close"] - stop_distance
            target = idea["close"] + reward_ratio * stop_distance
        else:
            stop = idea["close"] + stop_distance
            target = idea["close"] - reward_ratio * stop_distance

        volume = None
        if symbol_spec:
            pip_value_per_unit = symbol_spec["tick_value"] / symbol_spec["tick_size"]
            raw_volume = risk_amount / (stop_distance * pip_value_per_unit)
            step = symbol_spec.get("volume_step", 0.01)
            volume = max(step, round(raw_volume / step) * step)

        return {
            "platform": "mt4_mt5",
            "symbol": symbol,
            "direction": direction,
            "entry": idea["close"],
            "stop": stop,
            "target": target,
            "volume": volume,
            "risk_amount": risk_amount,
            "needs_symbol_spec": symbol_spec is None,
        }

    if platform == "deriv_multipliers":
        return {
            "platform": "deriv_multipliers",
            "symbol": symbol,
            "direction": direction,
            "stake": round(risk_amount, 2),
            "multiplier": instrument_cfg["multiplier"],
            "stop_loss_amount": round(risk_amount, 2),
            "take_profit_amount": round(risk_amount * strategy_cfg["exit"]["reward_risk_ratio"], 2),
        }

    if platform == "deriv_options":
        return {
            "platform": "deriv_options",
            "symbol": symbol,
            "contract_type": "CALL" if direction == "long" else "PUT",
            "stake": round(risk_amount, 2),
            "duration": instrument_cfg["duration"],
            "duration_unit": instrument_cfg["duration_unit"],
        }

    raise ValueError(f"Unknown platform: {platform}")
