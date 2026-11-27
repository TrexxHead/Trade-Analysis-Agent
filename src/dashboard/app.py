"""Kora Terminal - the trading hub.

Reads SQLite (trade history from ingest; live quotes/positions/account and
Kora's analyses from the Kora service, scripts/run_kora.py) and presents
the 11 Kora screens plus the per-instrument breakdown. Kora is an analyst:
nothing in this app places, modifies or closes an order.

Run via scripts/run_dashboard.py (binds 127.0.0.1). Set DASHBOARD_PASSWORD
in .env before exposing it anywhere else.
"""
from __future__ import annotations

import calendar as calendar_module
import json
import os
import secrets
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, Response, flash, jsonify, redirect, render_template, request, url_for

from src.analyst import llm, notify
from src.analyst.config import DEFAULT_SETTINGS, by_code, get_settings, instruments, load_watchlist
from src.analyst.fundamentals import sessions
from src.analysis.metrics import breakdown_by_hour, breakdown_by_weekday, compute_period_stats
from src.dashboard import data as D
from src.store.db import (
    analyses_between, get_flags, get_quotes, get_setup_stats, get_trade, get_trade_charts, get_trades, kora_connection,
    kv_get, kv_set, latest_analyses, list_decisions, list_plans, upsert_annotation,
)

ROOT = Path(__file__).resolve().parents[2]
BACKTESTS_DIR = ROOT / "backtests_out"
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD")

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET") or secrets.token_hex(16)

PAGES = {
    "dashboard": ("Dashboard", "Portfolio overview — live", "/", "layout-dashboard"),
    "charts": ("Charts", "Multi-timeframe workspace", "/charts", "candlestick-chart"),
    "kora": ("Kora", "Market analyst · setups & reasoning · never trades", "/kora", "bot"),
    "positions": ("Positions", "Open trades & history", "/positions", "layers"),
    "risk": ("Risk", "Exposure, limits & stress", "/risk", "shield"),
    "analytics": ("Analytics", "Quant performance review", "/analytics", "bar-chart-3"),
    "journal": ("Journal", "Auto-journaled trades", "/journal", "notebook-pen"),
    "scanner": ("Scanner", "Cross-market opportunities", "/scanner", "radar"),
    "calendar": ("Economic Calendar", "This week's releases", "/calendar", "calendar-clock"),
    "insights": ("AI Insights", "Ask Kora anything", "/insights", "sparkles"),
    "settings": ("Settings", "Data, alerts & Kora", "/settings", "settings"),
}
SUBPAGES = {
    "trades": ("Trades", "Full trade history"), "mistakes": ("Mistakes", "Rule violations"),
    "backtests": ("Backtests", "Replay Kora's setups on real history"), "pnl": ("P&L Calendar", "Daily P&L heatmap"),
    "report": ("Kora breakdown", "Full multi-timeframe analysis"),
}


@app.before_request
def _require_password():
    if not DASHBOARD_PASSWORD:
        return None
    auth = request.authorization
    if not auth or auth.password != DASHBOARD_PASSWORD:
        return Response("Password required.", 401, {"WWW-Authenticate": 'Basic realm="Kora Terminal"'})
    return None


def _conn():
    return kora_connection()


@app.context_processor
def _shell():
    conn = _conn()
    trades = get_trades(conn)
    feed = D.feed_status(conn)
    acct = D.account_state(conn, trades)
    settings = get_settings(conn)
    search = [{"label": m["code"], "sub": m["name"], "kind": "instrument", "href": f"/kora/{m['code']}"} for m in instruments()]
    search += [{"label": v[0].split(" — ")[0], "sub": v[1], "kind": "page", "href": v[2]} for v in PAGES.values()]
    search += [{"label": v[0], "sub": v[1], "kind": "page", "href": h} for h, v in
               (("/trades", SUBPAGES["trades"]), ("/mistakes", SUBPAGES["mistakes"]),
                ("/backtests", SUBPAGES["backtests"]), ("/pnl-calendar", SUBPAGES["pnl"]))]
    last_dec = list_decisions(conn, limit=1)
    return {
        "PAGES": PAGES, "SUBPAGES": SUBPAGES, "feed": feed, "acct": acct, "D": D,
        "settings": settings, "search_index": search, "n_positions": len(acct["positions"]),
        "mood": "content" if (acct["day_pl"] or 0) >= 0 else "concerned",
        "last_decision_id": last_dec[0]["id"] if last_dec else 0,
    }


# =================================================================== screens
@app.route("/")
def dashboard():
    conn = _conn()
    trades = get_trades(conn)
    acct = D.account_state(conn, trades)
    q = D.quant_stats(trades, acct["balance"])
    hist = kv_get(conn, "equity_history", []) or []
    if len(hist) >= 10:
        curve = [v for _, v in hist]
        curve_label = f"Equity · {D.duration(hist[0][0])} of snapshots"
    elif trades and q.get("start_balance"):
        curve = [q["start_balance"]] + [b for _, b in D.balance_curve(trades, q["start_balance"])]
        curve_label = f"Closed-trade balance · {len(trades)} trades"
    elif trades:
        run, curve = 0.0, [0.0]
        for t in trades:
            run += t["pnl"]
            curve.append(run)
        curve_label = f"Cumulative P&L · {len(trades)} trades"
    else:
        curve, curve_label = [], ""
    reps = D.watched_analyses(conn)
    top = max(reps.values(), key=lambda r: r.get("opportunity") or 0, default=None)
    positions = D.enrich_positions(conn, acct["positions"], acct["equity"])
    risk = D.risk_model(conn, acct, positions)
    board = sorted(((r, r["candidates"][0]) for r in reps.values() if r.get("candidates")),
                   key=lambda rc: -rc[1]["confidence"])[:5]
    sparks = kv_get(conn, "sparklines", {}) or {}
    s2c = D.symbol_to_code()
    recent = [{**t, "code": s2c.get(t["symbol"], t["symbol"])} for t in list(reversed(trades))[:8]]
    return render_template("dashboard.html", active="dashboard", acct=acct, q=q, curve=curve, board=board, recent=recent,
                           sparks=sparks, rows=D.instrument_rows(conn), pstats=D.plan_stats(list_plans(conn)),
                           curve_label=curve_label, top=top, reps=reps, positions=positions, risk=risk,
                           sessions=sessions(), decisions=list_decisions(conn, 1), n_trades=len(trades))


@app.route("/charts")
def charts():
    rows = D.instrument_rows(_conn())
    code = (request.args.get("code") or (rows[0]["code"] if rows else "")).upper()
    tf = request.args.get("tf", "M15").upper()
    return render_template("charts.html", active="charts", rows=rows, code=code, tf=tf,
                           meta=by_code().get(code))


@app.route("/kora")
def kora():
    conn = _conn()
    reps = D.watched_analyses(conn)
    rows = [r for r in D.instrument_rows(conn) if r["watched"]]
    code = (request.args.get("code") or "").upper()
    if code not in reps:
        code = max(reps, key=lambda c: reps[c].get("opportunity") or 0) if reps else None
    focus = reps.get(code) if code else None
    today = datetime.now(timezone.utc).date().isoformat()
    n_analyses = conn.execute("SELECT COUNT(*) FROM analyses WHERE created_at >= ?", (today,)).fetchone()[0]
    return render_template("kora.html", active="kora", focus=focus, code=code, rows=rows,
                           decisions=list_decisions(conn, 60), last_cycle=kv_get(conn, "last_cycle"),
                           n_analyses=n_analyses, n_trained=len(get_setup_stats(conn)),
                           plans=list_plans(conn, limit=40), pstats=D.plan_stats(list_plans(conn, limit=10000)),
                           control=kv_get(conn, "agent_control") or {"state": "RUNNING"})


@app.route("/kora/<code>")
def kora_report(code):
    conn = _conn()
    code = code.upper()
    rep = latest_analyses(conn).get(code)
    meta = by_code().get(code)
    if not meta:
        flash(f"Unknown instrument {code}.", "r")
        return redirect(url_for("kora"))
    stats = {k[1:]: v for k, v in get_setup_stats(conn, code).items()}
    decs = [d for d in list_decisions(conn, 200) if d["code"] == code][:12]
    return render_template("kora_report.html", active="kora", sub="report", rep=rep, meta=meta,
                           stats=stats, decisions=decs, llm_ok=llm.available(),
                           plans=list_plans(conn, code, 20), pstats=D.plan_stats(list_plans(conn, code, 10000)))


@app.route("/positions")
def positions():
    conn = _conn()
    trades = get_trades(conn)
    acct = D.account_state(conn, trades)
    pos = D.enrich_positions(conn, acct["positions"], acct["equity"])
    risk = D.risk_model(conn, acct, pos)
    s2c = D.symbol_to_code()
    tab = request.args.get("tab", "open")
    hsym, hdays = request.args.get("sym", ""), request.args.get("days", 30, type=int)
    since = (datetime.now(timezone.utc) - timedelta(days=hdays)).isoformat() if hdays else ""
    hist = [{**t, "code": s2c.get(t["symbol"], t["symbol"]),
             "dur": D.duration(t["open_time"], D.parse_dt(t["close_time"]))}
            for t in reversed(trades) if (not hsym or t["symbol"] == hsym) and t["close_time"] >= since]
    by_sym: dict = {}
    for t in trades:
        b = by_sym.setdefault(t["symbol"], {"symbol": t["symbol"], "code": s2c.get(t["symbol"], t["symbol"]), "trades": [], "open": 0, "open_pl": 0.0})
        b["trades"].append(t)
    for p in pos:
        b = by_sym.setdefault(p["symbol"], {"symbol": p["symbol"], "code": p["code"], "trades": [], "open": 0, "open_pl": 0.0})
        b["open"] += 1
        b["open_pl"] += p["pnl"]
    sym_rows = []
    for b in by_sym.values():
        st = D.quant_stats(b.pop("trades"), None) if b.get("trades") is not None else {}
        sym_rows.append({**b, **{k: st.get(k) for k in ("trades", "win_rate", "net", "pf", "avg_win", "avg_loss")}})
    sym_rows.sort(key=lambda r: -((r["net"] or 0) + r["open_pl"]))
    a = acct["account"]
    margin_level = (acct["equity"] / acct["margin"] * 100) if acct.get("margin") and acct.get("equity") else None
    return render_template("positions.html", active="positions", acct=acct, pos=pos, risk=risk, tab=tab, hist=hist[:300],
                           hist_stats=D.quant_stats(list(reversed(hist)), None) if hist else {}, hsym=hsym, hdays=hdays,
                           symbols=sorted({t["symbol"] for t in trades}), sym_rows=sym_rows, margin_level=margin_level,
                           sync=kv_get(conn, "history_sync"), a=a)


@app.route("/risk")
def risk():
    conn = _conn()
    trades = get_trades(conn)
    acct = D.account_state(conn, trades)
    pos = D.enrich_positions(conn, acct["positions"], acct["equity"])
    rm = D.risk_model(conn, acct, pos)
    pnls = [t["pnl"] for t in trades]
    return render_template("risk.html", active="risk", acct=acct, pos=pos, rm=rm,
                           var95=D.historical_var(trades), exp_dd=D.bootstrap_drawdown(pnls),
                           trade_days=len(D.daily_pnl(trades)))


@app.route("/analytics")
def analytics():
    conn = _conn()
    trades = get_trades(conn)
    acct = D.account_state(conn, trades)
    q = D.quant_stats(trades, acct["balance"])
    return render_template("analytics.html", active="analytics", q=q, n=len(trades),
                           months=D.monthly_returns(trades, q.get("start_balance")) if trades else [],
                           hist=D.histogram(trades, q.get("r_values", [])) if trades else None,
                           strats=D.strategy_rows(trades), by_hour=breakdown_by_hour(trades),
                           by_weekday=breakdown_by_weekday(trades))


@app.route("/journal")
def journal():
    conn = _conn()
    flt = request.args.get("f", "all")
    fsym, fsetup, falign = request.args.get("sym", ""), request.args.get("setup", ""), request.args.get("align", "")
    fq, fstart, fend = request.args.get("q", "").strip().lower(), request.args.get("start", ""), request.args.get("end", "")
    all_trades = list(reversed(get_trades(conn)))
    trades = [t for t in all_trades if (not fsym or t["symbol"] == fsym) and (not fsetup or (t.get("setup") or "Untagged") == fsetup)
              and (not fstart or t["close_time"][:10] >= fstart) and (not fend or t["close_time"][:10] <= fend)
              and (not fq or fq in ((t.get("notes") or "") + " " + (t.get("setup") or "") + " " + t["symbol"]).lower())]
    flags = defaultdict(list)
    for f in get_flags(conn):
        flags[f["trade_id"]].append(f)
    charts = get_trade_charts(conn)
    s2c = D.symbol_to_code()
    entries = []
    for t in trades:
        fl = flags.get(t["id"], [])
        if flt == "wins" and t["pnl"] <= 0 or flt == "losses" and t["pnl"] >= 0 or flt == "flagged" and not fl:
            continue
        if flt == "unreviewed" and t.get("notes"):
            continue
        code = s2c.get(t["symbol"])
        note, align = None, None
        if code:
            opened = D.parse_dt(t["open_time"])
            near = analyses_between(conn, code, (opened - timedelta(hours=6)).isoformat(), opened.isoformat())
            if near:
                rep = near[0]
                want = "long" if t["direction"] == "buy" else "short"
                cand = next((c for c in rep["candidates"] if c["direction"] == want), None)
                if cand:
                    align = "aligned"
                    note = f"Aligned with Kora: {cand['setup_name']} {want} was live at entry ({cand['confidence']}% confidence, {cand['levels_text']})."
                elif rep["bias"] != "Neutral" and (rep["bias"] == "Bullish") != (want == "long"):
                    align = "against"
                    note = f"Against Kora's {rep['bias'].lower()} bias ({rep['confidence']}%) at entry — no matching setup was active."
                else:
                    align = "discretionary"
                    note = f"Discretionary — Kora had no {want} setup at entry (bias {rep['bias'].lower()}, regime {rep['regime']['primary'].lower()})."
        if falign and align != falign:
            continue
        if fl:
            note = (note + " " if note else "") + "Flagged: " + "; ".join(f["detail"] or f["rule_id"] for f in fl) + "."
        r = t["pnl"] / t["risk_amount"] if t.get("risk_amount") else None
        entries.append({**t, "code": code or t["symbol"], "flags": fl, "chart": charts.get(t["id"]), "kora_note": note, "r": r,
                        "dur": D.duration(t["open_time"], D.parse_dt(t["close_time"])),
                        "reviewed": bool(t.get("notes"))})
    shown = entries[:120]
    summary = D.quant_stats([e for e in reversed(entries)], None) if entries else {}
    return render_template("journal.html", active="journal", entries=shown, n_match=len(entries), f=flt, summary=summary,
                           symbols=sorted({t["symbol"] for t in all_trades}),
                           setups=sorted({t.get("setup") or "Untagged" for t in all_trades}),
                           args={"sym": fsym, "setup": fsetup, "align": falign, "q": request.args.get("q", ""),
                                 "start": fstart, "end": fend})


@app.route("/scanner")
def scanner():
    conn = _conn()
    rows = sorted(D.instrument_rows(conn), key=lambda r: -(r["score"] or -1))
    return render_template("scanner.html", active="scanner", rows=rows, sparks=kv_get(conn, "sparklines", {}) or {})


@app.route("/calendar")
def econ_calendar():
    conn = _conn()
    imp = request.args.get("imp", "all")
    cal, fetched = kv_get(conn, "calendar", []) or [], None
    if not cal:  # service not running yet - fetch once directly so the page isn't empty
        try:
            from src.analyst.fundamentals import fetch_calendar
            cal = fetch_calendar()
            kv_set(conn, "calendar", cal)
        except Exception as e:
            print(f"calendar fetch failed: {e}", file=sys.stderr)
    watched = {c for m in instruments() for c in m.get("currencies", [])}
    now = datetime.now(timezone.utc)
    days: dict[str, list] = defaultdict(list)
    for e in cal:
        t = D.parse_dt(e["time"])
        if t < now - timedelta(hours=12) or (imp != "all" and e["impact"] != imp):
            continue
        if imp == "all" and e["impact"] in ("Low", "Non-Economic", "Holiday") and e["currency"] not in watched:
            continue
        affects = [m["code"] for m in instruments() if e["currency"] in m.get("currencies", [])]
        days[t.date().isoformat()].append({**e, "t": t, "affects": affects})
    groups = []
    for d, evs in sorted(days.items()):
        dd = date.fromisoformat(d)
        label = ("Today — " if dd == now.date() else "Tomorrow — " if dd == now.date() + timedelta(days=1) else "") + dd.strftime("%a %d %b")
        groups.append({"day": label, "events": sorted(evs, key=lambda e: e["t"])})
    return render_template("econ_calendar.html", active="calendar", groups=groups, imp=imp, watched=watched)


@app.route("/insights")
def insights():
    return render_template("insights.html", active="insights", llm_ok=llm.available(),
                           n_trades=len(get_trades(_conn())))


@app.route("/settings")
def settings_page():
    conn = _conn()
    keys = []
    for env, label, desc in (("METAAPI_TOKEN", "MetaApi token", "MT5 bridge - live data & history"),
                             ("ANTHROPIC_API_KEY", "Anthropic API key", "Ask Kora chat & narratives"),
                             ("TELEGRAM_BOT_TOKEN", "Telegram bot token", "Push alerts"),
                             ("DERIV_API_TOKEN", "Deriv API token", "Deriv-native history")):
        v = os.environ.get(env) or ""
        keys.append({"env": env, "label": label, "desc": desc, "set": bool(v),
                     "masked": ("•" * 16 + v[-4:]) if len(v) > 8 else ("set" if v else "not set")})
    from src.analyst.config import watchlist_codes
    return render_template("settings.html", active="settings", keys=keys, channels=notify.channels(),
                           watched=watchlist_codes(get_settings(conn)),
                           instruments=instruments(), defaults=DEFAULT_SETTINGS, llm_ok=llm.available(),
                           deriv_ok=bool(os.environ.get("DERIV_API_TOKEN")),
                           mt5_pkg=__import__("src.ingest.mt5_local", fromlist=["available"]).available(),
                           metaapi_ok=bool(os.environ.get("METAAPI_TOKEN") and os.environ.get("METAAPI_ACCOUNT_ID")))


# ============================================================ legacy pages
@app.route("/trades")
def trades_view():
    source = request.args.get("source") or None
    trades = list(reversed(get_trades(_conn(), source=source)))
    return render_template("trades.html", active="journal", sub="trades", trades=trades, source=source)


@app.route("/trades/<trade_id>", methods=["GET", "POST"])
def trade_detail(trade_id):
    conn = _conn()
    if request.method == "POST":
        upsert_annotation(conn, trade_id, setup=request.form.get("setup") or None,
                          emotion=request.form.get("emotion") or None, notes=request.form.get("notes") or None,
                          risk_amount=float(request.form["risk_amount"]) if request.form.get("risk_amount") else None)
        flash("Saved.", "g")
        return redirect(url_for("trade_detail", trade_id=trade_id))
    trade = get_trade(conn, trade_id)
    if trade is None:
        flash(f"No trade with id {trade_id}.", "r")
        return redirect(url_for("journal"))
    return render_template("trade_detail.html", active="journal", sub="trades", trade=trade,
                           chart=get_trade_charts(conn).get(trade_id),
                           flags=[f for f in get_flags(conn) if f["trade_id"] == trade_id])


@app.route("/mistakes")
def mistakes_view():
    flags = list(reversed(get_flags(_conn())))
    counts: dict[str, int] = defaultdict(int)
    for f in flags:
        counts[f["rule_id"]] += 1
    return render_template("mistakes.html", active="analytics", sub="mistakes", flags=flags,
                           counts=sorted(counts.items(), key=lambda kv: -kv[1]))


@app.route("/backtests")
def backtests_view():
    runs = []
    if BACKTESTS_DIR.exists():
        for path in sorted(BACKTESTS_DIR.glob("*.json"), reverse=True):
            try:
                d = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            runs.append({"filename": path.name, "symbol": d.get("code") or d.get("symbol"), "range": d.get("candle_range", {}),
                         "stats": d.get("stats", {}), "starting_balance": d.get("starting_balance"),
                         "ending_balance": d.get("ending_balance"), "kind": d.get("kind", "csv"), "tf": d.get("tf"),
                         "days": d.get("days")})
    from src.analyst.setups import SETUP_NAMES
    return render_template("backtests.html", active="analytics", sub="backtests", runs=runs, catalog=instruments(),
                           setup_names=SETUP_NAMES, watched=__import__("src.analyst.config", fromlist=["x"]).watchlist_codes())


@app.route("/backtests/<filename>")
def backtest_detail(filename):
    path = BACKTESTS_DIR / Path(filename).name
    d = json.loads(path.read_text()) if path.exists() else {}
    if d.get("kind") == "kora":
        return render_template("backtest_kora.html", active="analytics", sub="backtests", filename=filename, d=d,
                               curve=D.svg_path([0.0] + d.get("curve", [])), meta=by_code().get(d.get("code"), {}))
    return render_template("backtest_detail.html", active="analytics", sub="backtests", filename=filename, data=d)


@app.route("/api/backtest", methods=["POST"])
def api_backtest():
    from src.analyst import backtest
    body = request.get_json(force=True) or {}
    meta = by_code().get((body.get("code") or "").upper())
    if not meta:
        return jsonify({"ok": False, "message": "Pick an instrument."}), 400
    try:
        res = backtest.run(meta, tf=body.get("tf", "H1"), days=int(body.get("days", 180)),
                           setups=body.get("setups") or None, risk_usd=float(body.get("risk_usd", 100)))
    except Exception as e:
        print(f"backtest failed: {e}", file=sys.stderr)
        return jsonify({"ok": False, "message": str(e)}), 500
    return jsonify({"ok": True, "file": backtest.save(res), "trades": res["stats"]["trades"]})


@app.route("/pnl-calendar")
def pnl_calendar():
    today = date.today()
    year = request.args.get("year", type=int, default=today.year)
    month = request.args.get("month", type=int, default=today.month)
    trades = get_trades(_conn())
    pnl, cnt = defaultdict(float), defaultdict(int)
    for t in trades:
        pnl[t["close_time"][:10]] += t["pnl"]
        cnt[t["close_time"][:10]] += 1
    mx = max([abs(v) for v in pnl.values()] + [1.0])
    grid = [[{"date": d, "in_month": d.month == month, "pnl": pnl.get(d.isoformat()), "n": cnt.get(d.isoformat(), 0),
              "i": abs(pnl.get(d.isoformat(), 0)) / mx} for d in w]
            for w in calendar_module.Calendar(0).monthdatescalendar(year, month)]
    total = sum(v for k, v in pnl.items() if k[:7] == f"{year:04d}-{month:02d}")
    pm, py = (month - 1 or 12), (year - 1 if month == 1 else year)
    nm, ny = (month % 12 + 1), (year + 1 if month == 12 else year)
    return render_template("pnl_calendar.html", active="analytics", sub="pnl", grid=grid, total=total,
                           month_name=calendar_module.month_name[month], year=year, pm=pm, py=py, nm=nm, ny=ny)


@app.route("/proposals")
def proposals_redirect():
    flash("Kora is analysis-only now - trade ideas live on the Kora page as entry plans.", "b")
    return redirect(url_for("kora"))


# ===================================================================== APIs
@app.route("/api/live")
def api_live():
    conn = _conn()
    trades = get_trades(conn)
    feed = D.feed_status(conn)
    acct = D.account_state(conn, trades)
    vals: dict[str, list] = {}

    def put(k, text, t=None):
        vals[k] = [text, t]

    put("equity", D.money(acct["equity"]))
    put("balance", "Balance " + D.money(acct["balance"]))
    put("openpl", D.smoney(acct["open_pl"]), D.tone(acct["open_pl"]))
    put("openpl-side", D.smoney(acct["open_pl"]) + " open", D.tone(acct["open_pl"]))
    put("daypl", D.smoney(acct["day_pl"]), D.tone(acct["day_pl"]))
    put("dd", D.pct(acct["drawdown_pct"]) if acct["drawdown_pct"] is not None else "—")
    put("freemargin", D.money(acct["free_margin"]))
    quotes = {}
    for r in D.instrument_rows(conn):
        put(f"q:{r['code']}:price", r["price_s"])
        put(f"q:{r['code']}:chg", r["chg_s"], r["chg_t"])
        if r["price"]:
            quotes[r["code"]] = r["price"]
    for p in D.enrich_positions(conn, acct["positions"], acct["equity"]):
        put(f"pos:{p['id']}:cur", p["cur_s"])
        put(f"pos:{p['id']}:pnl", p["pnl_s"], p["pnl_t"])
    hb = feed.get("hb") or {}
    last = list_decisions(conn, 1)
    return jsonify({
        "values": vals, "quotes": quotes, "feed": {"online": feed["online"], "source": feed["source"],
                                                   "latency": feed.get("latency"), "state": feed["state"]},
        "positions": len(acct["positions"]), "mood": "content" if (acct["day_pl"] or 0) >= 0 else "concerned",
        "last_decision": last[0] if last else None, "next_cycle": hb.get("next_cycle"),
    })


@app.route("/api/chart/<code>")
def api_chart(code):
    code = code.upper()
    meta = by_code().get(code)
    if not meta:
        return jsonify({"error": "unknown instrument"}), 404
    tf = request.args.get("tf", "M15").upper()
    conn = _conn()
    feed = D.feed_status(conn)
    try:
        from src.live.sources import fetch_candles_sync
        count = max(60, min(request.args.get("count", 500, type=int), 1000))
        candles = fetch_candles_sync(meta, tf, count, feed["source"] if feed["online"] else None)
    except Exception as e:
        print(f"chart candles failed for {code}: {e}", file=sys.stderr)
        return jsonify({"error": f"Couldn't fetch candles: {e}"}), 502
    q = get_quotes(conn).get(code) or {}
    if q.get("price") and candles and feed["sim"]:
        scale = q["price"] / candles[-1]["close"]
        for c in candles:
            for k in ("open", "high", "low", "close"):
                c[k] *= scale
    overlays = []
    for p in kv_get(conn, "positions", []) or []:
        if p["code"] == code:
            overlays.append({"price": p["open_price"], "label": f"ENTRY {p['direction'].upper()}", "color": "#93c93f" if p["direction"] == "buy" else "#e0655f", "dash": False})
            if p.get("stop_loss"):
                overlays.append({"price": p["stop_loss"], "label": "SL", "color": "#e0655f", "dash": True})
            if p.get("take_profit"):
                overlays.append({"price": p["take_profit"], "label": "TP", "color": "#93c93f", "dash": True})
    rep = latest_analyses(conn).get(code)
    plan, levels = None, []
    if rep:
        if rep["candidates"]:
            c = rep["candidates"][0]
            plan = {"title": c["title"], "confidence": c["confidence"], "grade": c["grade"], "levels": c["levels_text"]}
            for v, lab, col in ((c["entry"], "KORA ENTRY", "#29abe2"), (c["stop"], "KORA SL", "#e0655f"),
                                (c["tp1"], "KORA TP1", "#93c93f"), (c["tp2"], "KORA TP2", "#93c93f")):
                overlays.append({"price": v, "label": lab, "color": col, "dash": True, "kora": True})
        levels = [{"price": lv["price"], "kind": lv["kind"], "tf": lv["tf"]} for lv in rep["key_levels"][:8]]
    trades = [{k: t[k] for k in ("open_time", "close_time", "open_price", "close_price", "direction", "pnl")}
              for t in get_trades(conn) if t["symbol"] in (meta["symbol"], code)
              and t.get("open_price") is not None and t.get("close_price") is not None]
    return jsonify({"code": code, "name": meta["name"], "digits": meta["digits"], "tf": tf, "trades": trades,
                    "candles": candles, "overlays": overlays, "levels": levels, "plan": plan,
                    "bias": rep["bias"] if rep else None})


@app.route("/api/chart-templates", methods=["GET", "POST"])
def api_chart_templates():
    conn = _conn()
    store = kv_get(conn, "chart_templates", {"templates": {}, "default": None}) or {"templates": {}, "default": None}
    if request.method == "POST":
        body = request.get_json(force=True) or {}
        name = (body.get("name") or "").strip()[:40]
        if body.get("action") == "save" and name:
            store["templates"][name] = [{"type": i.get("type"), "params": i.get("params") or {}, "color": i.get("color") or "#9bd35a"}
                                        for i in (body.get("indicators") or [])[:12]]
        elif body.get("action") == "delete":
            store["templates"].pop(name, None)
            if store.get("default") == name:
                store["default"] = None
        elif body.get("action") == "default" and name in store["templates"]:
            store["default"] = name
        kv_set(conn, "chart_templates", store)
    return jsonify(store)


def _chat_context(conn) -> dict:
    trades = get_trades(conn)
    acct = D.account_state(conn, trades)
    q = D.quant_stats(trades, acct["balance"])
    pos = D.enrich_positions(conn, acct["positions"], acct["equity"])
    rm = D.risk_model(conn, acct, pos)
    reps = D.watched_analyses(conn)
    flags = get_flags(conn)
    fc: dict[str, int] = defaultdict(int)
    for f in flags:
        fc[f["rule_id"]] += 1
    return {
        "now_utc": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "account": {k: acct.get(k) for k in ("equity", "balance", "open_pl", "day_pl", "drawdown_pct", "free_margin")},
        "risk": {"open_risk_usd": rm["total_risk"], "heat_pct": rm["heat"], "corr_adjusted_heat_pct": rm["heat_adj"],
                 "risk_score": rm["score"], "positions_without_stop": [p["code"] for p in rm["uncapped"]],
                 "daily_loss_limit_usd": rm["loss_limit"], "daily_loss_used_usd": rm["loss_used"]},
        "open_positions": [{k: p.get(k) for k in ("code", "direction", "volume", "open_price", "current_price",
                                                   "stop_loss", "take_profit", "pnl", "risk_pct", "dur")} | {"kora_view": (p.get("kora") or {}).get("text")}
                           for p in pos],
        "performance": {k: q.get(k) for k in ("trades", "win_rate", "pf", "avg_win", "avg_loss", "expectancy_r",
                                               "expectancy_usd", "max_dd_pct", "sharpe", "sortino", "avg_hold", "net")},
        "strategies_by_tag": D.strategy_rows(trades)[:6],
        "rule_violations": dict(fc),
        "recent_trades": [{k: t.get(k) for k in ("symbol", "direction", "open_time", "close_time", "pnl", "setup", "emotion", "notes")}
                          for t in trades[-10:]],
        "kora_analysis": {code: {"price": r["price"], "bias": r["bias"], "confidence": r["confidence"],
                                 "regime": r["regime"]["primary"], "verdict": r["verdict"],
                                 "setups": [{k: c[k] for k in ("title", "confidence", "grade", "levels_text", "confluence", "against", "invalidation")}
                                            | {"track_record": c["calibration"]["note"]} for c in r["candidates"][:2]],
                                 "event_risk": (r["fundamentals"].get("event_risk") or {}).get("text"),
                                 "fundamental_notes": r["fundamentals"].get("notes", [])[:3],
                                 "analysed_at": r["_created_at"]}
                          for code, r in reps.items()},
        "recent_decisions": [f"{d['created_at'][:16]} {d['action']}: {d['explanation'][:220]}" for d in list_decisions(conn, 8)],
    }


@app.route("/api/chat", methods=["POST"])
def api_chat():
    body = request.get_json(force=True) or {}
    history = body.get("history") or []
    if not llm.available():
        return jsonify({"text": "Ask Kora needs an Anthropic API key - add ANTHROPIC_API_KEY to .env and restart the dashboard."})
    return jsonify({"text": llm.chat(_chat_context(_conn()), history)})


@app.route("/api/settings", methods=["POST"])
def api_settings():
    conn = _conn()
    body = request.get_json(force=True) or {}
    cur = kv_get(conn, "settings", {}) or {}
    allowed = set(DEFAULT_SETTINGS)
    for k, v in body.items():
        if k not in allowed:
            continue
        if k == "notify" and isinstance(v, dict):
            cur.setdefault("notify", {}).update({kk: bool(vv) for kk, vv in v.items()})
        elif k in ("interval_minutes", "alert_threshold"):
            cur[k] = max(1 if k == "interval_minutes" else 0, min(240 if k == "interval_minutes" else 100, int(v)))
        elif k == "watchlist":
            cur[k] = [c for c in v if c in by_code()]
        else:
            cur[k] = v
    kv_set(conn, "settings", cur)
    return jsonify({"ok": True, "settings": get_settings(conn)})


@app.route("/api/watchlist", methods=["POST"])
def api_watchlist():
    """add / remove a code, reorder, or add / delete a custom instrument."""
    from src.analyst.config import watchlist_codes
    conn = _conn()
    body = request.get_json(force=True) or {}
    cur = kv_get(conn, "settings", {}) or {}
    s = get_settings(conn)
    wl = watchlist_codes(s)
    act, code = body.get("action"), (body.get("code") or "").upper().strip()
    if act == "add_custom":
        sym = (body.get("symbol") or "").strip()
        if not code or not sym:
            return jsonify({"ok": False, "message": "Give the instrument a short code and its exact MT5 symbol name."}), 400
        if code in by_code():
            return jsonify({"ok": False, "message": f"{code} already exists."}), 400
        custom = [c for c in s.get("custom_instruments", []) if c.get("code") != code]
        custom.append({"code": code, "symbol": sym, "name": body.get("name") or sym,
                       "deriv_symbol": (body.get("deriv_symbol") or "").strip() or None,
                       "kind": body.get("kind") or "Custom", "digits": int(body.get("digits") or 2),
                       "currencies": [x.strip().upper() for x in (body.get("currencies") or "").split(",") if x.strip()]})
        cur["custom_instruments"] = custom
        act = "add"
    elif act == "delete_custom":
        cur["custom_instruments"] = [c for c in s.get("custom_instruments", []) if c.get("code") != code]
        act = "remove"
    if act == "add" and code not in wl:
        wl.append(code)
    elif act == "remove":
        wl = [c for c in wl if c != code]
    elif act == "set" and isinstance(body.get("codes"), list):
        wl = [c for c in body["codes"] if c in by_code()]
    cur["watchlist"] = wl
    kv_set(conn, "settings", cur)
    return jsonify({"ok": True, "watchlist": wl})


@app.route("/api/agent", methods=["POST"])
def api_agent():
    conn = _conn()
    body = request.get_json(force=True) or {}
    ctl = kv_get(conn, "agent_control") or {"state": "RUNNING", "run_now": False}
    from src.store.db import add_decision
    if body.get("state") in ("RUNNING", "PAUSED", "STOPPED") and body["state"] != ctl["state"]:
        ctl["state"] = body["state"]
        expl = {"RUNNING": "Kora resumed: analysing every cycle and sending alerts.",
                "PAUSED": "Kora keeps analysing on schedule, but alerts are muted.",
                "STOPPED": "Analysis halted. The live price feed keeps running; no new breakdowns until resumed."}[ctl["state"]]
        add_decision(conn, f"{ {'RUNNING': 'RESUME', 'PAUSED': 'PAUSE', 'STOPPED': 'STOP'}[ctl['state']] } — manual override", "b",
                     "Operator command from the control center. " + expl)
    if body.get("run_now"):
        ctl["run_now"] = True
    kv_set(conn, "agent_control", ctl)
    return jsonify({"ok": True, "control": ctl, "service_online": D.feed_status(conn)["online"]})


@app.route("/api/notify-test", methods=["POST"])
def api_notify_test():
    if not notify.channels():
        return jsonify({"ok": False, "message": "No channel configured - set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID or KORA_WEBHOOK_URL in .env."})
    ok = notify.send("KORA · test alert — notifications are wired up. (Analysis only; Kora never trades.)")
    return jsonify({"ok": ok, "message": "Test alert sent." if ok else "Send failed - check the dashboard log."})
