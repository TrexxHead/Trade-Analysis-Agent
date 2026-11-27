"""Data shaping for the Kora Terminal hub. Everything here reads SQLite (written
by ingest + the Kora service) and computes - no network calls, so every
page renders fast. Numbers that can't be computed honestly yet (too little
history, feed offline) come back as None and the templates say so.
"""
from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

from src.analyst.config import by_code, get_settings, instruments
from src.store.db import get_quotes, kv_get, latest_analyses

ROOT = Path(__file__).resolve().parents[2]
RULES = yaml.safe_load((ROOT / "config" / "rules.yaml").read_text())
HEAT_CAP_PCT = RULES["risk"].get("max_portfolio_heat_pct", 6.0)
FEED_STALE_SECONDS = 20


# ------------------------------------------------------------------ format
def money(v, dp=2):
    return "—" if v is None else f"${abs(v):,.{dp}f}" if v >= 0 else f"-${abs(v):,.{dp}f}"


def smoney(v, dp=2):
    return "—" if v is None else (f"+${v:,.{dp}f}" if v >= 0 else f"-${abs(v):,.{dp}f}")


def price(v, d=2):
    return "—" if v is None else f"{v:,.{d}f}"


def pct(v, dp=1, signed=False):
    if v is None:
        return "—"
    return f"{v:+.{dp}f}%" if signed else f"{v:.{dp}f}%"


def tone(v):
    return "g" if (v or 0) >= 0 else "r"


def utcnow():
    return datetime.now(timezone.utc)


def parse_dt(s: str) -> datetime:
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def duration(start: str, end: datetime | None = None) -> str:
    secs = ((end or utcnow()) - parse_dt(start)).total_seconds()
    h, m = int(secs // 3600), int(secs % 3600 // 60)
    return f"{h // 24}d {h % 24:02d}h" if h >= 48 else f"{h}h {m:02d}m"


def symbol_to_code() -> dict:
    out = {}
    for m in instruments():
        out[m["symbol"]] = m["code"]
        out[m["code"]] = m["code"]
    return out


# ------------------------------------------------------------ feed/account
def feed_status(conn) -> dict:
    hb, _ = kv_get(conn, "heartbeat"), None
    if not hb:
        return {"online": False, "source": None, "state": "OFFLINE", "hb": None, "sim": False}
    age = (utcnow() - parse_dt(hb["ts"])).total_seconds()
    online = age < FEED_STALE_SECONDS
    ctl = kv_get(conn, "agent_control") or {"state": "RUNNING"}
    return {"online": online, "source": hb.get("source"), "state": ctl["state"] if online else "OFFLINE",
            "hb": hb, "age": age, "sim": hb.get("source") == "sim", "latency": hb.get("latency_ms")}


def account_state(conn, trades: list[dict] | None = None) -> dict:
    acc = kv_get(conn, "account") or {}
    positions = kv_get(conn, "positions") or []
    eq, bal = acc.get("equity"), acc.get("balance")
    open_pl = sum(p.get("profit", 0) + p.get("swap", 0) for p in positions) if positions else 0.0
    sod = kv_get(conn, "equity_sod") or {}
    day_pl = None
    if eq is not None and sod.get("date") == utcnow().date().isoformat():
        day_pl = eq - sod["equity"]
    elif trades is not None:
        today = utcnow().date().isoformat()
        day_pl = sum(t["pnl"] for t in trades if t["close_time"][:10] == today) + open_pl
    peak = kv_get(conn, "equity_peak")
    dd = ((eq - peak) / peak * 100) if eq is not None and peak else None
    if bal is None and trades:
        bal = next((t["balance_after"] for t in reversed(trades) if t.get("balance_after") is not None), None)
    return {"account": acc, "positions": positions, "equity": eq, "balance": bal, "open_pl": open_pl,
            "day_pl": day_pl, "drawdown_pct": dd, "peak": peak, "sod": sod.get("equity"),
            "margin": acc.get("margin"), "free_margin": acc.get("free_margin")}


def enrich_positions(conn, positions: list[dict], equity: float | None) -> list[dict]:
    reps = latest_analyses(conn)
    out = []
    for p in positions:
        d = p.get("digits", 2)
        rep = reps.get(p["code"])
        want = "long" if p["direction"] == "buy" else "short"
        kora = None
        if rep:
            cand = next((c for c in rep["candidates"] if c["direction"] == want), None)
            if cand:
                kora = {"conf": cand["confidence"], "text": f"{cand['setup_name']} {want} active", "tone": "g"}
            elif rep["bias"] != "Neutral":
                agrees = (rep["bias"] == "Bullish") == (want == "long")
                kora = {"conf": rep["confidence"] if agrees else 100 - rep["confidence"],
                        "text": f"{'With' if agrees else 'Against'} Kora's {rep['bias'].lower()} bias", "tone": "g" if agrees else "r"}
            else:
                kora = {"conf": 50, "text": "Kora is neutral here", "tone": "a"}
        cur = p.get("current_price")
        to_stop = to_target = None
        if p.get("risk_amount") and p.get("stop_loss") and cur is not None and p["open_price"] != p["stop_loss"]:
            per_unit = p["risk_amount"] / abs(p["open_price"] - p["stop_loss"])
            to_stop = -abs(cur - p["stop_loss"]) * per_unit
            if p.get("take_profit"):
                to_target = abs(p["take_profit"] - cur) * per_unit
        pnl = p.get("profit", 0) + p.get("swap", 0)
        out.append({**p, "pnl": pnl, "pnl_s": smoney(pnl), "pnl_t": tone(pnl),
                    "entry_s": price(p["open_price"], d), "cur_s": price(cur, d),
                    "sl_s": price(p.get("stop_loss"), d) if p.get("stop_loss") else "none",
                    "tp_s": price(p.get("take_profit"), d) if p.get("take_profit") else "none",
                    "risk_pct": (p["risk_amount"] / equity * 100) if p.get("risk_amount") and equity else None,
                    "to_stop": to_stop, "to_target": to_target, "dur": duration(p["open_time"]), "kora": kora,
                    "strategy": (p.get("comment") or "Manual")})
    return out


# ---------------------------------------------------------- trade analytics
def daily_pnl(trades: list[dict]) -> dict[str, float]:
    d: dict[str, float] = defaultdict(float)
    for t in trades:
        d[t["close_time"][:10]] += t["pnl"]
    return dict(sorted(d.items()))


def start_balance(trades: list[dict], balance_now: float | None) -> float | None:
    if balance_now is None or not trades:
        return None
    return balance_now - sum(t["pnl"] for t in trades)


def balance_curve(trades, start):
    b, out = start, []
    for t in trades:
        b += t["pnl"]
        out.append((t["close_time"], b))
    return out


def quant_stats(trades: list[dict], balance_now: float | None) -> dict:
    if not trades:
        return {}
    pnls = [t["pnl"] for t in trades]
    wins, losses = [p for p in pnls if p > 0], [p for p in pnls if p < 0]
    gw, gl = sum(wins), abs(sum(losses))
    out = {
        "trades": len(trades), "wins": len(wins), "losses": len(losses),
        "win_rate": len(wins) / len(trades) * 100, "pf": gw / gl if gl else None,
        "avg_win": gw / len(wins) if wins else None, "avg_loss": -gl / len(losses) if losses else None,
        "expectancy_usd": sum(pnls) / len(pnls), "net": sum(pnls),
    }
    holds = [(parse_dt(t["close_time"]) - parse_dt(t["open_time"])).total_seconds() for t in trades]
    out["avg_hold"] = duration((utcnow() - timedelta(seconds=sum(holds) / len(holds))).isoformat())
    rs = [t["pnl"] / t["risk_amount"] for t in trades if t.get("risk_amount")]
    out["expectancy_r"] = sum(rs) / len(rs) if rs else None
    out["r_values"] = rs
    if out["avg_win"] and out["avg_loss"]:
        out["avg_rr"] = out["avg_win"] / abs(out["avg_loss"])
    sb = start_balance(trades, balance_now)
    out["start_balance"] = sb
    if sb and sb > 0:
        curve = balance_curve(trades, sb)
        peak, mdd = sb, 0.0
        for _, b in curve:
            peak = max(peak, b)
            mdd = min(mdd, (b - peak) / peak * 100)
        out["max_dd_pct"] = mdd
        days = daily_pnl(trades)
        bal, rets = sb, []
        for _, p in days.items():
            rets.append(p / bal if bal else 0)
            bal += p
        if len(rets) >= 10:
            mu, sd = statistics.mean(rets), statistics.pstdev(rets)
            down = [r for r in rets if r < 0]
            dsd = (sum(r * r for r in down) / len(rets)) ** 0.5 if down else 0
            out["sharpe"] = mu / sd * math.sqrt(252) if sd else None
            out["sortino"] = mu / dsd * math.sqrt(252) if dsd else None
            span = max(1, (parse_dt(trades[-1]["close_time"]) - parse_dt(trades[0]["close_time"])).days)
            end = sb + sum(pnls)
            ann = (end / sb) ** (365 / span) - 1 if end > 0 else -1
            out["calmar"] = ann * 100 / abs(mdd) if mdd else None
        out["ruin_pct"] = risk_of_ruin([p / sb for p in pnls])
    return out


def risk_of_ruin(fracs: list[float], runs=2000, horizon=200, ruin=0.5) -> float | None:
    """Monte Carlo: share of bootstrapped 200-trade paths that lose 50% of equity."""
    if len(fracs) < 20:
        return None
    rnd = random.Random(7)
    hit = 0
    for _ in range(runs):
        eq = 1.0
        for _ in range(horizon):
            eq *= 1 + rnd.choice(fracs)
            if eq <= 1 - ruin:
                hit += 1
                break
    return hit / runs * 100


def bootstrap_drawdown(pnls: list[float], n=20, runs=2000) -> float | None:
    if len(pnls) < 15:
        return None
    rnd = random.Random(11)
    dds = []
    for _ in range(runs):
        cum = peak = mdd = 0.0
        for _ in range(n):
            cum += rnd.choice(pnls)
            peak = max(peak, cum)
            mdd = min(mdd, cum - peak)
        dds.append(mdd)
    return statistics.median(dds)


def historical_var(trades, conf=0.95) -> float | None:
    days = list(daily_pnl(trades).values())
    if len(days) < 20:
        return None
    s = sorted(days)
    return s[int((1 - conf) * len(s))]


def monthly_returns(trades, start):
    by: dict[str, float] = defaultdict(float)
    for t in trades:
        by[t["close_time"][:7]] += t["pnl"]
    out, bal = [], start
    for m, p in sorted(by.items()):
        pc = (p / bal * 100) if bal else None
        out.append({"m": datetime.strptime(m, "%Y-%m").strftime("%b %y"), "pnl": p, "pct": pc})
        if bal:
            bal += p
    return out[-8:]


def histogram(trades, rs):
    if len(rs) >= 10:
        edges = [-2, -1.5, -1, -0.5, 0, 0.5, 1, 1.5, 2, 3]
        labels = ["≤-2R", "-1.5R", "-1R", "-0.5R", "0", "+0.5R", "+1R", "+1.5R", "+2R", "≥3R"]
        counts = [0] * len(edges)
        for r in rs:
            k = min(range(len(edges)), key=lambda j: abs(edges[j] - r))
            counts[k] += 1
        return {"unit": "R", "bins": [{"l": l, "n": c, "t": "r" if e < 0 else "m" if e == 0 else "g"}
                                      for l, c, e in zip(labels, counts, edges)]}
    pnls = [t["pnl"] for t in trades]
    if len(pnls) < 5:
        return None
    lo, hi = min(pnls), max(pnls)
    w = (hi - lo) / 10 or 1
    counts = [0] * 10
    for p in pnls:
        counts[min(9, int((p - lo) / w))] += 1
    return {"unit": "$", "bins": [{"l": f"{lo + w * (k + .5):+.0f}", "n": c, "t": "r" if lo + w * (k + .5) < 0 else "g"}
                                  for k, c in enumerate(counts)]}


def strategy_rows(trades):
    groups: dict[str, list] = defaultdict(list)
    for t in trades:
        groups[t.get("setup") or "Untagged"].append(t)
    rows = []
    for k, ts in groups.items():
        p = [t["pnl"] for t in ts]
        gw, gl = sum(x for x in p if x > 0), abs(sum(x for x in p if x < 0))
        rows.append({"n": k, "t": len(ts), "wr": sum(1 for x in p if x > 0) / len(ts) * 100,
                     "pf": gw / gl if gl else None, "net": sum(p)})
    return sorted(rows, key=lambda r: -r["net"])


# --------------------------------------------------------------------- risk
def risk_model(conn, acct: dict, positions: list[dict]) -> dict:
    eq = acct["equity"]
    corr = kv_get(conn, "correlations") or {}
    matrix = corr.get("matrix", {})
    capped = [p for p in positions if p.get("risk_amount")]
    uncapped = [p for p in positions if not p.get("risk_amount")]
    total_risk = sum(p["risk_amount"] for p in capped)
    heat = total_risk / eq * 100 if eq else None
    # correlation-adjusted heat: sqrt(w' C w) using signed risk weights
    adj = None
    if eq and capped:
        acc = 0.0
        for a in capped:
            for b in capped:
                c = 1.0 if a is b else (matrix.get(a["code"], {}).get(b["code"]) or 0.0)
                sa = 1 if a["direction"] == "buy" else -1
                sb = 1 if b["direction"] == "buy" else -1
                acc += a["risk_amount"] * b["risk_amount"] * c * sa * sb
        adj = math.sqrt(max(acc, 0)) / eq * 100
    by_kind: dict[str, float] = defaultdict(float)
    for p in capped:
        by_kind[p.get("kind", "Other")] += p["risk_amount"]
    expo = [{"l": k, "v": v / total_risk * 100 if total_risk else 0} for k, v in sorted(by_kind.items(), key=lambda kv: -kv[1])]
    max_daily = RULES["risk"]["max_daily_loss_pct"]
    sod = acct.get("sod") or eq
    loss_limit = sod * max_daily / 100 if sod else None
    loss_used = max(0.0, -(acct.get("day_pl") or 0))
    dd_pct = RULES["risk"].get("max_drawdown_pct", 10.0)
    dd_buffer = acct["peak"] * dd_pct / 100 if acct.get("peak") else None
    dd_used = (acct["peak"] - eq) / dd_buffer * 100 if dd_buffer and eq is not None else None
    # composite risk score (0 = calm, 100 = at every limit)
    parts = [
        0.40 * min(1.0, (heat or 0) / HEAT_CAP_PCT),
        0.25 * min(1.0, loss_used / loss_limit) if loss_limit else 0,
        0.20 * min(1.0, (dd_used or 0) / 100),
        0.15 * (1.0 if uncapped else 0.0),
    ]
    score = round(sum(parts) * 100)
    label = "Low" if score < 35 else "Moderate" if score < 65 else "High"
    return {"total_risk": total_risk, "heat": heat, "heat_adj": adj, "uncapped": uncapped, "expo": expo,
            "loss_limit": loss_limit, "loss_used": loss_used, "dd_buffer": dd_buffer, "dd_used": dd_used,
            "score": score, "label": label, "label_tone": "g" if score < 35 else "a" if score < 65 else "r",
            "health": max(0, min(100, round(100 - 0.6 * score - (15 if uncapped else 0)))),
            "corr": corr, "heat_cap": HEAT_CAP_PCT, "max_daily_pct": max_daily, "max_dd_pct": dd_pct}


# --------------------------------------------------------------- scanner etc
def instrument_rows(conn) -> list[dict]:
    reps = latest_analyses(conn)
    quotes = get_quotes(conn)
    settings = get_settings(conn)
    rows = []
    for m in instruments():
        rep = reps.get(m["code"])
        q = quotes.get(m["code"]) or {}
        px = q.get("price") or (rep or {}).get("price")
        dopen = q.get("day_open") or (rep or {}).get("day_open")
        chg = (px / dopen - 1) * 100 if px and dopen else None
        best = (rep or {}).get("candidates", [None])
        best = best[0] if best else None
        rows.append({
            **m, "rep": rep, "price": px, "price_s": price(px, m["digits"]), "chg": chg,
            "chg_s": pct(chg, 2, True) if chg is not None else "—", "chg_t": tone(chg),
            "spread": (q["ask"] - q["bid"]) if q.get("ask") and q.get("bid") else None,
            "atr": (rep or {}).get("atr"), "vol": (rep or {}).get("atr_pct"),
            "trend": min(100, (rep or {}).get("adx") or 0) if rep else None,
            "score": (rep or {}).get("opportunity"), "best": best,
            "disabled": m["code"] in settings.get("disabled", []),
        })
    return rows


def svg_path(values: list[float], w=600, h=180, pad=8) -> dict:
    """Line + area path for an equity-style SVG chart (viewBox 0 0 w h+10)."""
    if len(values) < 2:
        return {"line": "", "area": "", "hi": None, "lo": None}
    lo, hi = min(values), max(values)
    sx = w / (len(values) - 1)
    sy = (h - pad * 2) / ((hi - lo) or 1)
    pts = [f"{'L' if i else 'M'}{i * sx:.1f} {h - pad - (v - lo) * sy + 5:.1f}" for i, v in enumerate(values)]
    line = " ".join(pts)
    return {"line": line, "area": f"{line} L{w} {h + 10} L0 {h + 10} Z", "hi": hi, "lo": lo}


def plan_stats(plans: list[dict]) -> dict:
    done = [p for p in plans if p["status"] in ("won", "lost", "expired")]
    rs = [p["result_r"] for p in done if p["result_r"] is not None]
    wins = sum(1 for p in done if p["status"] == "won" or (p["status"] == "expired" and (p["result_r"] or 0) > 0))
    return {"total": len(plans), "open": sum(1 for p in plans if p["status"] == "open"),
            "resolved": len(done), "not_filled": sum(1 for p in plans if p["status"] == "not_filled"),
            "wins": wins, "win_rate": wins / len(done) * 100 if done else None,
            "avg_r": sum(rs) / len(rs) if rs else None, "total_r": sum(rs) if rs else 0.0}


def spark_path(values: list[float], w: int = 120, h: int = 28) -> str:
    if not values or len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    sx, sy = w / (len(values) - 1), (h - 4) / ((hi - lo) or 1)
    return " ".join(f"{'L' if i else 'M'}{i * sx:.1f} {h - 2 - (v - lo) * sy:.1f}" for i, v in enumerate(values))
