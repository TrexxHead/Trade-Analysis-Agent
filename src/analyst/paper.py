"""Kora's paper-trading account: forward-tests every A/B plan on live prices
without touching any real account.

Each plan is opened under three management policies so Kora can learn which
style actually works on your instruments:
  tp1     - exit at TP1, or the stop
  tp2_be  - at TP1 move the stop to breakeven, then run to TP2
  quick1r - take profit at 1R, or the stop
Fills use the live bid/ask (so spread counts), limit entries must be touched
within 6 hours, and anything still open after 48 hours is closed at market.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.analyst.sizing import size_plan
from src.store.db import kv_get, kv_set

POLICIES = {"tp1": "Exit at TP1", "tp2_be": "Breakeven at TP1, run to TP2", "quick1r": "Quick 1R", "rule": "Strategy's own exit"}
FILL_HOURS, MAX_HOURS = 6, 48
SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER, code TEXT NOT NULL, setup TEXT, setup_name TEXT, direction TEXT NOT NULL, policy TEXT NOT NULL,
    entry_type TEXT, entry REAL, stop REAL, tp1 REAL, tp2 REAL, lots REAL, risk_usd REAL,
    status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL, opened_at TEXT, closed_at TEXT,
    fill REAL, cur_stop REAL, exit REAL, r REAL, pnl REAL, note TEXT
);
CREATE INDEX IF NOT EXISTS idx_paper_status ON paper_trades(status);
"""
_MIGRATED = set()


def _schema(conn):
    conn.executescript(SCHEMA)
    if id(conn) in _MIGRATED:
        return
    cols = {r[1] for r in conn.execute("PRAGMA table_info(paper_trades)")}
    for col, typ in (("rule_json", "TEXT"), ("signal_time", "TEXT"), ("tf", "TEXT")):
        if col not in cols:
            conn.execute(f"ALTER TABLE paper_trades ADD COLUMN {col} {typ}")
    conn.commit()
    _MIGRATED.add(id(conn))


def _now():
    return datetime.now(timezone.utc)


def account(conn) -> dict:
    acct = kv_get(conn, "paper_account")
    if not acct:
        acct = {"start": 1000.0, "balance": 1000.0, "since": _now().isoformat()}
        kv_set(conn, "paper_account", acct)
    return acct


def reset(conn, start: float) -> None:
    _schema(conn)
    conn.execute("DELETE FROM paper_trades")
    conn.commit()
    kv_set(conn, "paper_account", {"start": float(start), "balance": float(start), "since": _now().isoformat()})


def open_for_plan(conn, plan_id: int | None, code: str, cand: dict, spec: dict | None, risk_pct: float) -> int:
    _schema(conn)
    acct = account(conn)
    sz = size_plan(cand, spec, acct["balance"], risk_pct)
    lots = sz.get("lots")
    risk_usd = sz.get("risk_usd") or acct["balance"] * risk_pct / 100
    n = 0
    for policy in POLICIES:
        if policy == "rule":
            continue
        conn.execute(
            "INSERT INTO paper_trades (plan_id, code, setup, setup_name, direction, policy, entry_type, entry, stop, tp1, tp2, "
            "lots, risk_usd, created_at, cur_stop) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (plan_id, code, cand["setup"], cand["setup_name"], cand["direction"], policy, cand["entry_type"], cand["entry"],
             cand["stop"], cand["tp1"], cand["tp2"], lots, risk_usd, _now().isoformat(), cand["stop"]))
        n += 1
    if cand.get("rule_params") and cand.get("signal_time"):
        import json
        conn.execute(
            "INSERT INTO paper_trades (plan_id, code, setup, setup_name, direction, policy, entry_type, entry, stop, tp1, tp2, "
            "lots, risk_usd, created_at, opened_at, fill, cur_stop, status, rule_json, signal_time, tf) "
            "VALUES (?, ?, ?, ?, ?, 'rule', 'market', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)",
            (plan_id, code, cand["setup"], cand["setup_name"], cand["direction"], cand["entry"], cand["stop"], cand["tp1"],
             cand["tp2"], lots, risk_usd, _now().isoformat(), _now().isoformat(), cand["entry"], cand["stop"],
             json.dumps(cand["rule_params"]), cand["signal_time"], cand.get("timeframe")))
        n += 1
    conn.commit()
    return n


def _close(conn, t: dict, exit_px: float, status: str, note: str) -> None:
    sign = 1 if t["direction"] == "long" else -1
    risk_dist = abs(t["fill"] - t["stop"]) or 1e-12
    r = sign * (exit_px - t["fill"]) / risk_dist
    pnl = r * t["risk_usd"]
    conn.execute("UPDATE paper_trades SET status = ?, closed_at = ?, exit = ?, r = ?, pnl = ?, note = ? WHERE id = ?",
                 (status, _now().isoformat(), exit_px, round(r, 3), round(pnl, 2), note, t["id"]))
    if t["policy"] == "tp1":  # the account balance follows the baseline policy so it reads like one real account
        acct = account(conn)
        acct["balance"] = round(acct["balance"] + pnl, 2)
        kv_set(conn, "paper_account", acct)


def process(conn, quotes: dict[str, dict]) -> list[dict]:
    """Advance every pending/open paper trade against the latest quotes.
    Returns trades that closed this tick."""
    _schema(conn)
    closed = []
    rows = [dict(r) for r in conn.execute("SELECT * FROM paper_trades WHERE status IN ('pending', 'open')")]
    for t in rows:
        if t["policy"] == "rule":
            continue  # managed candle by candle in manage_rules()
        q = quotes.get(t["code"]) or {}
        bid, ask = q.get("bid") or q.get("price"), q.get("ask") or q.get("price")
        if not bid or not ask:
            continue
        sign = 1 if t["direction"] == "long" else -1
        age = _now() - datetime.fromisoformat(t["created_at"])
        if t["status"] == "pending":
            px = ask if sign > 0 else bid
            hit = t["entry_type"] == "market" or (sign > 0 and ask <= t["entry"]) or (sign < 0 and bid >= t["entry"])
            if hit:
                fill = px if t["entry_type"] == "market" else t["entry"]
                conn.execute("UPDATE paper_trades SET status = 'open', opened_at = ?, fill = ? WHERE id = ?",
                             (_now().isoformat(), fill, t["id"]))
            elif age > timedelta(hours=FILL_HOURS):
                conn.execute("UPDATE paper_trades SET status = 'cancelled', closed_at = ?, note = 'limit never filled' WHERE id = ?",
                             (_now().isoformat(), t["id"]))
            continue
        exit_px = bid if sign > 0 else ask          # a long closes at the bid, a short at the ask
        risk_dist = abs(t["fill"] - t["stop"])
        target = {"tp1": t["tp1"], "tp2_be": t["tp2"], "quick1r": t["fill"] + sign * risk_dist}[t["policy"]]
        stop = t["cur_stop"] if t["cur_stop"] is not None else t["stop"]
        if sign * (exit_px - stop) <= 0:
            be = abs(stop - t["fill"]) < 1e-12
            _close(conn, t, stop, "breakeven" if be else "lost", "stopped at breakeven" if be else "stopped out")
            closed.append(t)
        elif sign * (exit_px - target) >= 0:
            _close(conn, t, target, "won", "target hit")
            closed.append(t)
        elif t["policy"] == "tp2_be" and abs(stop - t["stop"]) < 1e-12 and sign * (exit_px - t["tp1"]) >= 0:
            conn.execute("UPDATE paper_trades SET cur_stop = ? WHERE id = ?", (t["fill"], t["id"]))
        elif t["opened_at"] and _now() - datetime.fromisoformat(t["opened_at"]) > timedelta(hours=MAX_HOURS):
            _close(conn, t, exit_px, "expired", f"closed at market after {MAX_HOURS}h")
            closed.append(t)
    conn.commit()
    return closed


def trades(conn, status: str | None = None, limit: int = 500) -> list[dict]:
    _schema(conn)
    q, p = "SELECT * FROM paper_trades", []
    if status:
        q, p = q + " WHERE status = ?", [status]
    return [dict(r) for r in conn.execute(q + " ORDER BY id DESC LIMIT ?", (*p, limit))]


def policy_stats(conn) -> list[dict]:
    out = []
    done = [t for t in trades(conn, limit=100000) if t["status"] in ("won", "lost", "breakeven", "expired")]
    for pol, name in POLICIES.items():
        rs = [t["r"] for t in done if t["policy"] == pol and t["r"] is not None]
        out.append({"policy": pol, "name": name, "n": len(rs), "win_rate": sum(1 for r in rs if r > 0) / len(rs) * 100 if rs else None,
                    "expectancy": sum(rs) / len(rs) if rs else None, "total_r": sum(rs),
                    "pnl": sum(t["pnl"] or 0 for t in done if t["policy"] == pol)})
    return out


def manage_rules(conn, candles: dict, metas: dict, cost_fn=None) -> list[dict]:
    """Advance 'strategy's own exit' paper trades by replaying the strategy's exit
    logic on the latest closed candles - exactly what the backtest does.
    candles: {code: {tf: [...]}}."""
    import json
    from src.analyst import rules as R
    _schema(conn)
    closed = []
    for t in [dict(r) for r in conn.execute("SELECT * FROM paper_trades WHERE status = 'open' AND policy = 'rule'")]:
        c = (candles.get(t["code"]) or {}).get(t["tf"] or "") or []
        if len(c) < 60:
            continue
        idx = next((k for k in range(len(c) - 1, -1, -1) if c[k]["time"] == t["signal_time"]), None)
        params = R.norm(json.loads(t["rule_json"] or "{}"))
        meta = metas.get(t["code"]) or {"code": t["code"]}
        if idx is None:
            if c[0]["time"] > (t["signal_time"] or ""):   # entry candle scrolled out of the window: close at market
                last = c[-2]["close"]
                sign = 1 if t["direction"] == "long" else -1
                _close(conn, t, last, "expired", "closed at market (beyond the candle window)")
                closed.append(t)
            continue
        ctx = R.Ctx(c[:-1])   # the last candle is still forming
        sign = 1 if t["direction"] == "long" else -1
        risk = abs(t["fill"] - t["stop"]) or 1e-12
        s = {"i": idx, "sign": sign, "direction": t["direction"], "entry": t["fill"], "stop": t["stop"], "risk": risk}
        res = R.simulate(ctx, s, params["exit"], params, meta, cost_fn(t["code"]) if cost_fn else 0.0)
        if res is None:
            continue
        r, bars, why = res
        if idx + bars >= ctx.n - 1 and why == "time exit" and params["exit"] in ("ema_trail", "scale", "opposite", "cond", "atr_trail"):
            continue  # still running: the 'time exit' is just the end of the data
        exit_px = t["fill"] + sign * r * risk
        status = "won" if r > 0.02 else "lost" if r < -0.02 else "breakeven"
        _close(conn, t, exit_px, status, why)
        closed.append(t)
    conn.commit()
    return closed
