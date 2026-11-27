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


def build(conn, now: datetime | None = None) -> str:
    now = now or datetime.now().astimezone()
    settings = get_settings(conn)
    wl = watchlist_codes(settings)
    cat = by_code()
    reps = latest_analyses(conn)
    acct = kv_get(conn, "account", {}) or {}
    positions = kv_get(conn, "positions", []) or []
    hour = now.hour
    title = "Morning brief" if hour < 12 else "Afternoon brief" if hour < 18 else "Evening brief"
    lines = [f"KORA · {title} · {now.strftime('%a %d %b, %H:%M')}"]

    # account
    if acct.get("login"):
        open_pl = sum(p.get("profit", 0) + p.get("swap", 0) for p in positions)
        lines.append(f"Account {acct['login']} ({(acct.get('type') or '').title()}): equity {_money(acct.get('equity'))}, "
                     f"balance {_money(acct.get('balance'))}, {len(positions)} open ({_signed(open_pl)})")
    yday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    today = datetime.now(timezone.utc).date().isoformat()
    closed = [t for t in get_trades(conn) if t["close_time"][:10] in (yday, today)]
    if closed:
        pnl = sum(t["pnl"] for t in closed)
        wins = sum(1 for t in closed if t["pnl"] > 0)
        lines.append(f"Last 24h closed: {len(closed)} trades, {wins} won, {_signed(pnl)}")

    # board
    lines.append("")
    lines.append("THE BOARD")
    ranked = sorted((reps[c] for c in wl if c in reps), key=lambda r: -(r.get("opportunity") or 0))
    for r in ranked:
        d = r.get("digits", 2)
        c = r["candidates"][0] if r.get("candidates") else None
        price = f"{r['price']:,.{d}f}" if r.get("price") else "—"
        chg = f" ({r['day_change_pct']:+.2f}%)" if r.get("day_change_pct") is not None else ""
        lines.append(f"• {r['code']} {price}{chg} — {r['bias']} {r['confidence']}%, {r['regime']['primary'].lower()}")
        if c and c["confidence"] >= 55:
            lines.append(f"   {c['grade']}-grade {c['direction'].upper()} {c['setup_name']} ({c['confidence']}%): "
                         f"entry {c['entry']:,.{d}f} {c['entry_type']}, SL {c['stop']:,.{d}f}, TP1 {c['tp1']:,.{d}f} ({c['rr1']}R)")
        else:
            lines.append(f"   {r.get('headline') or r['verdict']}")
        ev = (r.get("fundamentals") or {}).get("event_risk")
        if ev:
            lines.append(f"   ⚠ {ev['text']}")
    missing = [c for c in wl if c not in reps]
    if missing:
        lines.append(f"• Not analysed yet: {', '.join(missing)} (is the Kora service running?)")

    # today's red-folder events for watched currencies
    ccys = {x for c in wl for x in (cat.get(c) or {}).get("currencies", [])}
    cal = kv_get(conn, "calendar", []) or []
    upcoming = []
    for e in cal:
        try:
            t = datetime.fromisoformat(e["time"])
        except (KeyError, ValueError):
            continue
        if e.get("impact") == "High" and e.get("currency") in ccys and timedelta(0) <= t - datetime.now(timezone.utc) <= timedelta(hours=18):
            upcoming.append(f"• {t.astimezone().strftime('%H:%M')} {e['currency']} {e['title']} (fcst {e.get('forecast') or '—'}, prev {e.get('previous') or '—'})")
    lines.append("")
    lines.append("HIGH-IMPACT NEWS (next 18h)")
    lines.extend(upcoming or ["• None for your instruments"])

    # forward record
    plans = list_plans(conn, limit=10000)
    done = [p for p in plans if p["status"] in ("won", "lost", "expired")]
    if done:
        rs = [p["result_r"] for p in done if p["result_r"] is not None]
        wins = sum(1 for p in done if p["status"] == "won")
        lines.append("")
        lines.append(f"Kora's record: {len(done)} plans resolved, {wins / len(done):.0%} hit TP1, "
                     f"{(sum(rs) / len(rs) if rs else 0):+.2f}R avg · {sum(1 for p in plans if p['status'] == 'open')} live")

    link = os.environ.get("DASHBOARD_PUBLIC_URL")
    if link:
        lines.append("")
        lines.append(link)
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
