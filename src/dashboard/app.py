"""Dashboard: overview stats, a P&L calendar, trade/mistake/backtest/proposal
tables, with approve/reject actions on pending proposals. Reads directly
from data/trades.db and the reports_out/backtests_out JSON files - it
doesn't compute anything the rest of this repo doesn't already compute, it
just presents it (and now, executes approve/reject decisions directly).

Run via scripts/run_dashboard.py, which binds to 127.0.0.1 only - if you're
exposing this beyond your own machine (e.g. via a tunnel like ngrok), set
DASHBOARD_PASSWORD in .env first. Without it, anyone who reaches this page
can see your trade history and approve/reject real orders.
"""
import calendar as calendar_module
import json
import os
import secrets
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import yaml
from flask import Flask, Response, flash, redirect, render_template, request, url_for

from src.analysis.metrics import (
    breakdown_by_emotion,
    breakdown_by_hour,
    breakdown_by_setup,
    breakdown_by_weekday,
    compute_daily_progress,
    compute_period_stats,
    compute_r_multiple_stats,
)
from src.store.db import get_connection, get_flags, get_trade, get_trades, list_proposals, upsert_annotation
from src.strategy.execution import execute_proposal, reject_proposal

ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = ROOT / "reports_out"
BACKTESTS_DIR = ROOT / "backtests_out"
RULES_PATH = ROOT / "config" / "rules.yaml"

DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD")

app = Flask(__name__)
app.secret_key = secrets.token_hex(16)  # only used for one-request flash messages, not real sessions


@app.before_request
def _require_password():
    if not DASHBOARD_PASSWORD:
        return None  # no password configured - fine for pure localhost use, not for exposing this remotely
    auth = request.authorization
    if not auth or auth.password != DASHBOARD_PASSWORD:
        return Response(
            "Password required.", 401, {"WWW-Authenticate": 'Basic realm="Trade Analysis Agent"'}
        )
    return None


@app.context_processor
def _shell_context():
    """Data every page's shell (sidebar account card, topbar stat pair) needs.
    Deliberately only ever real, locally-known data - no live API call here
    (that would add a network dependency to every single page load), and no
    placeholder for things we can't actually know yet (session latency,
    whether a background scan process is running) rather than faking them.
    """
    conn = _conn()
    trades = get_trades(conn)
    last_trade = trades[-1] if trades else None
    daily_progress = compute_daily_progress(trades)
    today = date.today().isoformat()
    today_pnl = daily_progress.get(today, {}).get("pnl", 0.0)
    return {
        "shell_account": {
            "account_id": last_trade["account_id"] if last_trade else None,
            "source": last_trade["source"] if last_trade else None,
            "balance": last_trade["balance_after"] if last_trade else None,
        },
        "shell_daily_pnl": today_pnl,
        "shell_max_drawdown": compute_period_stats(trades).get("max_drawdown", 0.0) if trades else 0.0,
    }


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


def _today_loss_status(trades: list[dict]) -> dict | None:
    """Today's cumulative loss against config/rules.yaml's max_daily_loss_pct,
    color-coded the same way prop-firm dashboards do (green under 70% of the
    limit, orange 70-90%, red past 90%) - only available once a trade with
    balance_after has closed today, since that's what anchors the day's
    starting balance.
    """
    rules_cfg = yaml.safe_load(RULES_PATH.read_text())
    max_pct = rules_cfg["risk"]["max_daily_loss_pct"]
    today = date.today().isoformat()
    day = compute_daily_progress(trades).get(today)
    if not day or not day["start_balance"]:
        return None

    loss_pct = max(0.0, -day["pnl"] / day["start_balance"] * 100)
    pct_of_limit = min(loss_pct / max_pct * 100, 100) if max_pct else 0
    status = "critical" if pct_of_limit >= 90 else "warning" if pct_of_limit >= 70 else "good"
    return {"pnl": day["pnl"], "loss_pct": loss_pct, "max_pct": max_pct,
            "pct_of_limit": pct_of_limit, "status": status}


@app.route("/")
def index():
    conn = _conn()
    trades = get_trades(conn)
    stats = compute_period_stats(trades)
    r_stats = compute_r_multiple_stats(trades)
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
        r_stats=r_stats,
        daily_loss=_today_loss_status(trades),
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


@app.route("/trades/<trade_id>", methods=["GET", "POST"])
def trade_detail(trade_id):
    conn = _conn()
    if request.method == "POST":
        upsert_annotation(
            conn, trade_id,
            setup=request.form.get("setup") or None,
            emotion=request.form.get("emotion") or None,
            notes=request.form.get("notes") or None,
            risk_amount=float(request.form["risk_amount"]) if request.form.get("risk_amount") else None,
        )
        flash("Saved.", "good")
        return redirect(url_for("trade_detail", trade_id=trade_id))

    trade = get_trade(conn, trade_id)
    if trade is None:
        flash(f"No trade with id {trade_id}.", "critical")
        return redirect(url_for("trades_view"))
    return render_template("trade_detail.html", active="trades", trade=trade)


@app.route("/positions")
def positions_view():
    positions = []
    error = None
    if not (os.environ.get("METAAPI_TOKEN") and os.environ.get("METAAPI_ACCOUNT_ID")):
        error = "METAAPI_TOKEN / METAAPI_ACCOUNT_ID not configured - see .env.example."
    else:
        try:
            from src.ingest.metatrader import get_positions
            positions = get_positions()
        except Exception as e:
            print(f"Failed to fetch open positions: {e}", file=sys.stderr)
            error = f"Couldn't reach MetaApi: {e}"
    return render_template("positions.html", active="positions", positions=positions, error=error)


@app.route("/insights")
def insights_view():
    conn = _conn()
    trades = get_trades(conn)
    return render_template(
        "insights.html",
        active="insights",
        r_stats=compute_r_multiple_stats(trades),
        by_hour=breakdown_by_hour(trades),
        by_weekday=breakdown_by_weekday(trades),
        by_setup=breakdown_by_setup(trades),
        by_emotion=breakdown_by_emotion(trades),
        trade_count=len(trades),
    )


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


@app.route("/proposals/<int:proposal_id>/approve", methods=["POST"])
def approve_proposal_route(proposal_id):
    conn = _conn()
    try:
        # Always demo-gated from the dashboard, on purpose - going live is a
        # deliberate CLI action (decide_proposal.py --live), not a button
        # that's one click away in a web UI.
        result = execute_proposal(conn, proposal_id, require_demo=True)
        order_id = result.get("orderId") or result.get("contract_id") or result.get("positionId")
        flash(f"Proposal #{proposal_id} executed (order {order_id}).", "good")
    except Exception as e:
        print(f"Failed to execute proposal #{proposal_id}: {e}", file=sys.stderr)
        flash(f"Proposal #{proposal_id} failed to execute: {e}", "critical")
    return redirect(url_for("proposals_view"))


@app.route("/proposals/<int:proposal_id>/reject", methods=["POST"])
def reject_proposal_route(proposal_id):
    conn = _conn()
    note = request.form.get("note") or None
    try:
        reject_proposal(conn, proposal_id, note=note)
        flash(f"Proposal #{proposal_id} rejected.", "muted")
    except Exception as e:
        print(f"Failed to reject proposal #{proposal_id}: {e}", file=sys.stderr)
        flash(f"Proposal #{proposal_id} failed to reject: {e}", "critical")
    return redirect(url_for("proposals_view"))
