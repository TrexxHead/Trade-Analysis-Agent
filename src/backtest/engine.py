"""Simulates the baseline strategy against historical candles.

Scoped to mt4_mt5-style instruments only: MT4/5 history exports are plain
price data, which maps directly onto price-based stop-loss/take-profit
simulation. Deriv Multipliers (monetary stop/take-profit) and Options
(fixed-duration, no stop) would need different fill logic that isn't
implemented here - don't reuse this for those platforms without adding it.

To avoid lookahead bias: a signal is only allowed to use candles up to and
including the one it's evaluated on, and the resulting trade "opens" at the
NEXT candle's open, since the signal candle's own close wouldn't have been
known as final until that candle finished.
"""
from src.strategy.order_spec import build_order_spec
from src.strategy.signals import generate_idea


def _pnl(position: dict, exit_price: float, symbol_spec: dict) -> float:
    direction_mult = 1 if position["direction"] == "long" else -1
    price_diff = (exit_price - position["entry"]) * direction_mult
    pip_value_per_unit = symbol_spec["tick_value"] / symbol_spec["tick_size"]
    return price_diff * position["volume"] * pip_value_per_unit


def run_backtest(candles: list[dict], strategy_cfg: dict, instrument_cfg: dict,
                  starting_balance: float, risk_pct: float, symbol_spec: dict) -> dict:
    if instrument_cfg["platform"] != "mt4_mt5":
        raise ValueError(
            "Backtesting only supports mt4_mt5-style price-based stop/target simulation for now; "
            f"got platform={instrument_cfg['platform']!r}."
        )
    if not symbol_spec:
        raise ValueError("symbol_spec (tick_size, tick_value) is required to size positions and compute P&L.")

    # generate_idea already returns None gracefully until each indicator has
    # enough data, so the loop can just start at 0 rather than pre-computing
    # a warm-up offset.
    balance = starting_balance
    trades: list[dict] = []
    open_position: dict | None = None

    i = 0
    while i < len(candles):
        if open_position is None:
            idea = generate_idea(candles[:i + 1], strategy_cfg)
            if idea is not None and i + 1 < len(candles):
                order_spec = build_order_spec(idea, instrument_cfg, strategy_cfg, balance, risk_pct, symbol_spec)
                entry_candle = candles[i + 1]
                open_position = {
                    "direction": idea["direction"],
                    "entry": entry_candle["open"],
                    "entry_time": entry_candle["time"],
                    "stop": order_spec["stop"],
                    "target": order_spec["target"],
                    "volume": order_spec["volume"],
                }
                i += 1
                continue
        else:
            candle = candles[i]
            direction = open_position["direction"]
            hit_stop = (
                (direction == "long" and candle["low"] <= open_position["stop"])
                or (direction == "short" and candle["high"] >= open_position["stop"])
            )
            hit_target = (
                (direction == "long" and candle["high"] >= open_position["target"])
                or (direction == "short" and candle["low"] <= open_position["target"])
            )
            if hit_stop or hit_target:
                # if a single candle's range spans both levels, assume the stop
                # was hit first - the standard conservative assumption when
                # intra-candle order isn't known from OHLC alone
                exit_price = open_position["stop"] if hit_stop else open_position["target"]
                pnl = _pnl(open_position, exit_price, symbol_spec)
                balance += pnl
                trades.append({
                    "id": f"backtest:{len(trades)}",
                    "source": "backtest",
                    "account_id": "backtest",
                    "symbol": instrument_cfg["symbol"],
                    "direction": "buy" if direction == "long" else "sell",
                    "volume": open_position["volume"],
                    "open_time": open_position["entry_time"],
                    "close_time": candle["time"],
                    "open_price": open_position["entry"],
                    "close_price": exit_price,
                    "pnl": pnl,
                    "balance_after": balance,
                    "raw": {},
                })
                open_position = None
        i += 1

    return {"trades": trades, "ending_balance": balance, "open_at_end": open_position}
