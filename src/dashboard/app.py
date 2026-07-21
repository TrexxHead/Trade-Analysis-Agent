"""Local-only dashboard: overview stats, a P&L calendar, trade/mistake/
backtest/proposal tables. Reads directly from data/trades.db and the
reports_out/backtests_out JSON files - it doesn't compute anything the
rest of this repo doesn't already compute, it just presents it.

Run via scripts/run_dashboard.py. Binds to 127.0.0.1 only - this holds
account balances and trade history, so it's not meant to be exposed
beyond your own machine.
"""
import calendar as calendar_module
import json
from datetime import date, datetime, timezone
from pathlib import Path

from flask import Flask, render_template, request

from src.analysis.metrics import compute_period_stats
from src.store.db import get_connection, get_flags, get_trades, list_proposals

ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = ROOT / "reports_out"
BACKTESTS_DIR = ROOT / "backtests_out"

app = Flask(__name__)


def _parse_time(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def _conn():
    return get_connection()


def _build_equity_svg(equity_points: list[tuple[datetime, float]], width: int = 760, height: int = 180) -> dict:
    if not equity_points:
        return {"points": "", "hover": [], "min_val": 0, "max_val": 0, "width": width, "height": height}

    values = [0.0] + [v for _, v in equity_points]
    min_val, max_val = min(values), max(values)
    span = (max_val - min_val) or 1.0
    pad = 10
    n = len(equity_points)

    def coords(i, v):
        x = pad + (i / max(n - 1, 1)) * (width - 2 * pad)
        y = height - pad - ((v - min_val) / span) * (height - 2 * pad)
        return x, y

    points = []
    hover = []
    for i, (ts, v) in enumerate(equity_points):
        x, y = coords(i, v)
        points.append(f"{x:.1f},{y:.1f}")
        hover.append({"x": round(x, 1), "y": round(y, 1), "label": f"{ts.strftime('%Y-%m-%d %H:%M')}  {v:+.2f}"})

    return {"points": " ".join(points), "hover": hover, "min_val": min_val, "max_val": max_val,
            "width": width, "height": height}


@app.route("/")
def index():
    conn = _conn()
    trades = get_trades(conn)
    stats = compute_period_stats(trades)
    flags = get_flags(conn)

    mistake_counts: dict[str, int] = {}
    for f in flags:
        mistake_counts[f["rule_id"]] = mistake_counts.get(f["rule_id"], 0) + 1

    equity_points = []
    running = 0.0
    for t in trades:
        running += t["pnl"]
        equity_points.append((_parse_time(t["close_time"]), running))

    pending_proposals = len(list_proposals(conn, status="pending"))

    return render_template(
        "index.html",
        active="index",
        stats=stats,
        mistake_counts=sorted(mistake_counts.items(), key=lambda kv: -kv[1])[:5],
        equity=_build_equity_svg(equity_points),
        pending_proposals=pending_proposals,
        trade_count=len(trades),
    )


@app.route("/calendar")
def calendar_view():
    today = date.today()
    year = request.args.get("year", type=int, default=today.year)
    month = request.args.get("month", type=int, default=today.month)

    conn = _conn()
    trades = get_trades(conn)

    daily_pnl: dict[str, float] = {}
    daily_count: dict[str, int] = {}
    for t in trades:
        day = t["close_time"][:10]
        daily_pnl[day] = daily_pnl.get(day, 0.0) + t["pnl"]
        daily_count[day] = daily_count.get(day, 0) + 1

    max_abs = max([abs(v) for v in daily_pnl.values()] + [1.0])

    weeks = calendar_module.Calendar(firstweekday=0).monthdatescalendar(year, month)
    grid = []
    for week in weeks:
        row = []
        for day in week:
            key = day.isoformat()
            pnl = daily_pnl.get(key)
            row.append({
                "date": day,
                "in_month": day.month == month,
                "pnl": pnl,
                "trade_count": daily_count.get(key, 0),
                "intensity": (abs(pnl) / max_abs) if pnl else 0,
            })
        grid.append(row)

    month_total = sum(v for k, v in daily_pnl.items() if k[:7] == f"{year:04d}-{month:02d}")

    prev_month = month - 1 or 12
    prev_year = year - 1 if month == 1 else year
    next_month = month % 12 + 1
    next_year = year + 1 if month == 12 else year

    return render_template(
        "calendar.html",
        active="calendar",
        grid=grid,
        month_name=calendar_module.month_name[month],
        year=year,
        month_total=month_total,
        prev_year=prev_year, prev_month=prev_month,
        next_year=next_year, next_month=next_month,
    )


@app.route("/trades")
def trades_view():
    conn = _conn()
    source = request.args.get("source") or None
    trades = list(reversed(get_trades(conn, source=source)))
    return render_template("trades.html", active="trades", trades=trades, source=source)


@app.route("/mistakes")
def mistakes_view():
    conn = _conn()
    flags = list(reversed(get_flags(conn)))
    counts: dict[str, int] = {}
    for f in flags:
        counts[f["rule_id"]] = counts.get(f["rule_id"], 0) + 1
    return render_template("mistakes.html", active="mistakes", flags=flags,
                           counts=sorted(counts.items(), key=lambda kv: -kv[1]))


@app.route("/backtests")
def backtests_view():
    runs = []
    if BACKTESTS_DIR.exists():
        for path in sorted(BACKTESTS_DIR.glob("*.json"), reverse=True):
            try:
                data = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            runs.append({
                "filename": path.name,
                "symbol": data.get("symbol"),
                "range": data.get("candle_range", {}),
                "stats": data.get("stats", {}),
                "starting_balance": data.get("starting_balance"),
                "ending_balance": data.get("ending_balance"),
            })
    return render_template("backtests.html", active="backtests", runs=runs)


@app.route("/backtests/<filename>")
def backtest_detail(filename):
    path = BACKTESTS_DIR / filename
    data = json.loads(path.read_text()) if path.exists() else {}
    return render_template("backtest_detail.html", active="backtests", filename=filename, data=data)


@app.route("/proposals")
def proposals_view():
    conn = _conn()
    status = request.args.get("status") or None
    proposals = list(reversed(list_proposals(conn, status=status)))
    for p in proposals:
        p["order_spec"] = json.loads(p["order_spec_json"])
    return render_template("proposals.html", active="proposals", proposals=proposals, status=status)
