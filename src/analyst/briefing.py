"""Kora's scheduled briefing: a plain-text read of your account and the board,
sent before the sessions you trade (default 07:00 and 13:00 your PC's local
time) to every configured channel (Telegram, ntfy, email, webhook).

    python scripts/send_brief.py        # send one now
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from src.analyst.config import by_code, get_settings, watchlist_codes
from src.store.db import get_trades, kv_get, latest_analyses, list_plans


def _money(v):
    return "—" if v is None else (f"${v:,.2f}" if v >= 0 else f"-${abs(v):,.2f}")


def _signed(v):
    return "—" if v is None else (f"+${v:,.2f}" if v >= 0 else f"-${abs(v):,.2f}")


HEAT_CAP_PCT = 6.0


def _session_open(kind: str, now_utc: datetime) -> bool:
    """Gold/silver/FX liquidity is concentrated in London + New York (07-17 UTC, weekdays)."""
    if kind not in ("Metals", "Forex"):
        return True
    return now_utc.weekday() < 5 and 7 <= now_utc.hour < 17


def focus_ranking(reps: dict, wl: list, cat: dict, now_utc: datetime) -> list[tuple[float, dict, list[str]]]:
    out = []
    for code in wl:
        r = reps.get(code)
        if not r:
            continue
        score, why = float(r.get("opportunity") or 0), []
        best = r["candidates"][0] if r.get("candidates") else None
        if best and best["confidence"] >= 55:
            score += 10
            why.append(f"{best['grade']}-grade {best['setup_name'].lower()} live")
        if best and (best.get("sizing") or {}).get("tradable") is False:
            score -= 15
            why.append("not tradable at your size")
        if not _session_open((cat.get(code) or {}).get("kind", ""), now_utc):
            score -= 15
            why.append("outside London/NY liquidity")
        if (r.get("fundamentals") or {}).get("event_risk"):
            score -= 10
            why.append("red-folder news soon")
        if r["regime"]["primary"] == "Trending":
            score += 5
            why.append("trending")
        out.append((score, r, why))
    return sorted(out, key=lambda x: -x[0])


def _entry_lines(r: dict, c: dict, d: int) -> list[str]:
    sz = c.get("sizing") or {}
    lines = [f"  {c['direction'].upper()} {c['setup_name']} — rated {c.get('rating', c['confidence'] / 10)}/10 ({c['grade']}-grade, {c['confidence']}%)",
             f"  Entry {c['entry']:,.{d}f} ({c['entry_type']}) · SL {c['stop']:,.{d}f} · TP1 {c['tp1']:,.{d}f} ({c['rr1']}R) · TP2 {c['tp2']:,.{d}f}"]
    if sz.get("lots") is not None:
        line = f"  Size: {sz['lots']:g} lots risks {_money(sz['risk_usd'])} ({sz['risk_pct']:.1f}%) to make {_money(sz['reward_usd'])} at TP1"
        if sz.get("margin"):
            line += f", margin {_money(sz['margin'])}"
        lines.append(line)
    if sz.get("reason"):
        lines.append(f"  {'⛔' if sz.get('tradable') is False else '⚠'} {sz['reason']}")
    if c.get("exit_rule"):
        lines.append(f"  Exit plan: {c['exit_rule']}")
    why = "; ".join(c["confluence"][:2])
    lines.append(f"  Why: {why}")
    if c["against"]:
        lines.append(f"  Watch out: {c['against'][0]}")
    return lines


def _approaches(entries: list[tuple[dict, dict]], corr: dict, equity: float | None, free_margin: float | None,
                specs: dict, risk_pct: float, max_risk_pct: float) -> list[str]:
    from src.analyst.sizing import size_plan
    tradable = [(r, c) for r, c in entries if (c.get("sizing") or {}).get("tradable") and c.get("rating", 0) >= 5.5]
    if not tradable:
        return ["• Stand aside: nothing rated 5.5+ that your account can size within your risk limits. "
                "Waiting is a position; Kora keeps watching and will alert you."]

    def plan(sel, rp):
        tot_r = tot_m = 0.0
        parts = []
        for r, c in sel:
            sz = size_plan(c, specs.get(r["code"]), equity, rp, free_margin, (c["sizing"].get("margin") or 0) / c["sizing"]["lots"]
                           if c["sizing"].get("lots") and c["sizing"].get("margin") else None, max_risk_pct)
            if not sz.get("lots"):
                continue
            tot_r += sz["risk_usd"]
            tot_m += sz.get("margin") or 0
            parts.append(f"{r['code']} {c['direction']} {sz['lots']:g} lots")
        pct = tot_r / equity * 100 if equity else 0
        return f"{', '.join(parts)} → risk {_money(tot_r)} ({pct:.1f}%){f', margin {_money(tot_m)}' if tot_m else ''}"

    def uncorrelated(sel):
        picked = []
        for r, c in sel:
            ok = True
            for r2, c2 in picked:
                v = ((corr.get("matrix") or {}).get(r["code"]) or {}).get(r2["code"])
                same = c["direction"] == c2["direction"]
                if v is not None and ((v >= 0.6 and same) or (v <= -0.6 and not same)):
                    ok = False
            if ok:
                picked.append((r, c))
        return picked

    out = [f"• Conservative: only the best entry at half risk: {plan(tradable[:1], risk_pct / 2)}"]
    bal = uncorrelated(tradable)[:2]
    if len(bal) > 1:
        out.append(f"• Balanced: the top two uncorrelated entries at {risk_pct:g}% each: {plan(bal, risk_pct)}")
    active, used = [], 0.0
    for rc in uncorrelated(tradable):
        if used + risk_pct <= HEAT_CAP_PCT:
            active.append(rc)
            used += risk_pct
    if len(active) > len(bal):
        out.append(f"• Active: every independent entry rated 5.5+ within the {HEAT_CAP_PCT:g}% heat cap: {plan(active, risk_pct)}")
    return out


def build(conn, now: datetime | None = None) -> str:
    now = now or datetime.now().astimezone()
    now_utc = now.astimezone(timezone.utc)
    settings = get_settings(conn)
    wl = watchlist_codes(settings)
    cat = by_code()
    reps = latest_analyses(conn)
    acct = kv_get(conn, "account", {}) or {}
    positions = kv_get(conn, "positions", []) or []
    specs = kv_get(conn, "symbol_specs", {}) or {}
    corr = kv_get(conn, "correlations", {}) or {}
    risk_pct, max_risk = settings.get("risk_pct", 1.0), settings.get("max_risk_pct", 3.0)
    equity, free_margin = acct.get("equity"), acct.get("free_margin")
    hour = now.hour
    title = "Morning brief" if hour < 12 else "Afternoon brief" if hour < 18 else "Evening brief"
    lines = [f"KORA · {title} · {now.strftime('%a %d %b, %H:%M')}"]

    if acct.get("login"):
        open_pl = sum(p.get("profit", 0) + p.get("swap", 0) for p in positions)
        lines.append(f"Account {acct['login']} ({(acct.get('type') or '').title()}): equity {_money(equity)}, free margin "
                     f"{_money(free_margin)}, {len(positions)} open ({_signed(open_pl)}). Sizing at {risk_pct:g}% risk "
                     f"({_money(equity * risk_pct / 100 if equity else None)} per trade).")
    else:
        lines.append("No MT5 account connected - entries are rated but not sized.")
    yday = (now_utc - timedelta(days=1)).date().isoformat()
    closed = [t for t in get_trades(conn) if t["close_time"][:10] in (yday, now_utc.date().isoformat())]
    if closed:
        lines.append(f"Last 24h: {len(closed)} closed, {sum(1 for t in closed if t['pnl'] > 0)} won, {_signed(sum(t['pnl'] for t in closed))}")

    ranked = focus_ranking(reps, wl, cat, now_utc)
    focus = ranked[:3]
    lines += ["", "TODAY'S FOCUS"]
    for i, (score, r, why) in enumerate(focus, 1):
        d = r.get("digits", 2)
        chg = f" ({r['day_change_pct']:+.2f}%)" if r.get("day_change_pct") is not None else ""
        lines.append(f"{i}. {r['code']} {r['price']:,.{d}f}{chg} — {r['bias']} {r['confidence']}%, {r['regime']['primary'].lower()}"
                     + (f" · {', '.join(why)}" if why else ""))
        rated = [c for c in r.get("candidates", []) if c["confidence"] >= 45]
        if rated:
            for c in rated[:2]:
                lines += _entry_lines(r, c, d)
        else:
            lines.append(f"  {r.get('headline') or r['verdict']}")
        ev = (r.get("fundamentals") or {}).get("event_risk")
        if ev:
            lines.append(f"  ⚠ {ev['text']}")

    entries = sorted([(r, r["candidates"][0]) for _, r, _ in ranked if r.get("candidates") and r["candidates"][0]["confidence"] >= 45],
                     key=lambda rc: -rc[1].get("rating", 0))
    if len(entries) > 1:
        lines += ["", "TAKING SEVERAL AT ONCE?"]
        tot = sum((c.get("sizing") or {}).get("risk_pct") or 0 for _, c in entries if (c.get("sizing") or {}).get("tradable"))
        for i, (r1, c1) in enumerate(entries):
            for r2, c2 in entries[i + 1:]:
                v = ((corr.get("matrix") or {}).get(r1["code"]) or {}).get(r2["code"])
                same = c1["direction"] == c2["direction"]
                if v is None:
                    verdict = "no correlation data yet"
                elif (v >= 0.6 and same) or (v <= -0.6 and not same):
                    verdict = f"correlated ({v:+.2f}) - effectively one bet; take the better-rated one or halve both"
                elif abs(v) < 0.3:
                    verdict = f"independent ({v:+.2f}) - fine to hold together"
                else:
                    verdict = f"partly related ({v:+.2f}) - ok together at reduced size"
                lines.append(f"• {r1['code']} {c1['direction']} + {r2['code']} {c2['direction']}: {verdict}")
        if any(r["code"].startswith(("BOOM", "CRASH")) for r, _ in entries):
            lines.append("• Boom/Crash run on separate generators, so they don't move together. Each can spike against you: "
                         "longs on Boom and shorts on Crash ride the spike; the opposite side can gap through a stop.")
        lines.append(f"• All tradable entries together risk {tot:.1f}% of equity (heat cap {HEAT_CAP_PCT:g}%)."
                     + (" Over the cap - pick from the approaches below." if tot > HEAT_CAP_PCT else ""))
        for r, c in entries[:3]:
            sz = c.get("sizing") or {}
            if sz.get("lots") and sz.get("tradable"):
                by_heat = int(HEAT_CAP_PCT // max(sz["risk_pct"], 1e-9))
                by_margin = int((free_margin or 0) * 0.5 // sz["margin"]) if sz.get("margin") else None
                cap = min(by_heat, by_margin) if by_margin is not None else by_heat
                lines.append(f"• {r['code']}: room for up to {max(cap, 0)} position(s) at this size"
                             f" ({'heat' if by_margin is None or by_heat <= by_margin else 'margin'}-limited). Kora suggests one per setup.")

    lines += ["", "APPROACHES FOR TODAY"]
    lines += _approaches(entries, corr, equity, free_margin, specs, risk_pct, max_risk)

    rest = [r for _, r, _ in ranked[3:]]
    if rest:
        lines += ["", "REST OF THE BOARD"]
        for r in rest:
            lines.append(f"• {r['code']}: {r['bias']} {r['confidence']}% — {r.get('headline') or r['verdict']}")
    missing = [c for c in wl if c not in reps]
    if missing:
        lines.append(f"• Not analysed yet: {', '.join(missing)} (is the Kora service running?)")

    ccys = {x for c in wl for x in (cat.get(c) or {}).get("currencies", [])}
    upcoming = []
    for e in kv_get(conn, "calendar", []) or []:
        try:
            t = datetime.fromisoformat(e["time"])
        except (KeyError, ValueError):
            continue
        if e.get("impact") == "High" and e.get("currency") in ccys and timedelta(0) <= t - now_utc <= timedelta(hours=18):
            upcoming.append(f"• {t.astimezone().strftime('%H:%M')} {e['currency']} {e['title']} (fcst {e.get('forecast') or '—'}, prev {e.get('previous') or '—'})")
    watch = [f"• {r['code']} [{ {'mine': 'your strategy only', 'both': 'yours + Kora'}.get(r.get('mode'), r.get('mode'))}] "
             f"{r['strategy']['name']} ({r['strategy']['tf']}): {r['strategy'].get('text', '')}"
             for code, r in reps.items() if r.get("strategy") and code in wl]
    if watch:
        lines += ["", "YOUR STRATEGY WATCH"] + watch
    lines += ["", "HIGH-IMPACT NEWS (next 18h)"] + (upcoming or ["• None for your instruments"])

    plans = list_plans(conn, limit=10000)
    done = [p for p in plans if p["status"] in ("won", "lost", "expired")]
    lab = kv_get(conn, "paper_account") or {}
    if done or lab:
        lines.append("")
        if done:
            rs = [p["result_r"] for p in done if p["result_r"] is not None]
            lines.append(f"Kora's record: {len(done)} plans resolved, {sum(1 for p in done if p['status'] == 'won') / len(done):.0%} hit TP1, "
                         f"{(sum(rs) / len(rs) if rs else 0):+.2f}R avg")
        if lab.get("balance") is not None:
            lines.append(f"Kora's paper account: {_money(lab['balance'])} (started {_money(lab.get('start'))})")
        best = kv_get(conn, "best_policy")
        if best:
            lines.append(f"Best management in paper tests: {best['name']} ({best['expectancy']:+.2f}R over {best['n']} trades)")

    if os.environ.get("DASHBOARD_PUBLIC_URL"):
        lines += ["", os.environ["DASHBOARD_PUBLIC_URL"]]
    lines.append("Analysis only — your decision, your execution.")
    return "\n".join(lines)


def due(conn, now: datetime | None = None) -> str | None:
    """Return the scheduled time ('HH:MM') whose brief should go out now, if any."""
    s = get_settings(conn)
    b = s.get("brief") or {}
    if not b.get("enabled", True):
        return None
    now = now or datetime.now().astimezone()
    sent = kv_get(conn, "brief_sent", {}) or {}
    today = now.date().isoformat()
    for t in b.get("times") or []:
        try:
            hh, mm = (int(x) for x in t.split(":"))
        except ValueError:
            continue
        slot = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if slot <= now <= slot + timedelta(minutes=30) and t not in sent.get(today, []):
            if b.get("weekdays_only") and now.weekday() >= 5:
                continue
            return t
    return None


def mark_sent(conn, slot: str, now: datetime | None = None) -> None:
    from src.store.db import kv_set
    now = now or datetime.now().astimezone()
    sent = kv_get(conn, "brief_sent", {}) or {}
    today = now.date().isoformat()
    kv_set(conn, "brief_sent", {today: sorted(set(sent.get(today, [])) | {slot})})
