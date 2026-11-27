"""Kora's self-refinement loop (run by the service every few hours).

1. Retrain: re-calibrate every watchlist setup on fresh history once a week
   (or when you press "Retrain now" in the Lab).
2. Auto-tune: if a setup keeps losing in forward paper trading on an
   instrument (12+ trades, expectancy <= -0.25R), switch it off there and say so.
3. Management style: once each paper policy has 20+ trades, record which one
   is working best so the brief can recommend it.
Every change is written to the decision log with its evidence.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.analyst import paper
from src.analyst.calibration import train_instrument
from src.analyst.config import by_code, get_settings, watchlist_codes
from src.store.db import add_decision, kv_get, kv_set, save_setup_stats

MIN_TRADES_TUNE, CUTOFF_R, MIN_TRADES_POLICY = 12, -0.25, 20


def _now():
    return datetime.now(timezone.utc)


def retrain_due(conn) -> bool:
    if kv_get(conn, "retrain_now"):
        return True
    last = kv_get(conn, "trained_at")
    return not last or _now() - datetime.fromisoformat(last) > timedelta(days=7)


def retrain(conn, history_fn, days: int = 180) -> str:
    """history_fn(meta, days) -> H1 candles from the live source."""
    cat = by_code()
    notes = []
    for code in watchlist_codes(get_settings(conn)):
        meta = cat.get(code)
        if not meta:
            continue
        try:
            h1 = history_fn(meta, days)
            if len(h1) < 400:
                notes.append(f"{code}: only {len(h1)} candles, skipped")
                continue
            rows = train_instrument(h1, meta)
            save_setup_stats(conn, code, rows, "H1", days)
            best = max(rows, key=lambda r: r["expectancy"] if r["n"] >= 15 else -9, default=None)
            notes.append(f"{code}: {len(h1)} bars" + (f", best {best['setup']} {best['direction']} {best['expectancy']:+.2f}R" if best else ""))
        except Exception as e:
            notes.append(f"{code}: failed ({e})")
    kv_set(conn, "trained_at", _now().isoformat())
    conn.execute("DELETE FROM kv WHERE key = 'retrain_now'")
    conn.commit()
    summary = "; ".join(notes)
    add_decision(conn, "RETRAIN — Kora re-calibrated its setups on fresh history", "b",
                 f"Weekly self-calibration (no lookahead, conservative fills). {summary}")
    return summary


def auto_tune(conn) -> list[str]:
    settings = get_settings(conn)
    if not settings.get("auto_tune", True):
        return []
    done = [t for t in paper.trades(conn, limit=100000) if t["policy"] == "tp1" and t["status"] in ("won", "lost", "breakeven", "expired")]
    groups: dict = {}
    for t in done:
        groups.setdefault((t["code"], t["setup"]), []).append(t["r"] or 0)
    off = {c: list(v) for c, v in (settings.get("setups_off") or {}).items()}
    auto = kv_get(conn, "auto_disabled", {}) or {}
    changed = []
    for (code, setup), rs in groups.items():
        exp = sum(rs) / len(rs)
        if len(rs) >= MIN_TRADES_TUNE and exp <= CUTOFF_R and setup not in off.get(code, []):
            off.setdefault(code, []).append(setup)
            auto[f"{code}:{setup}"] = {"at": _now().isoformat(), "n": len(rs), "expectancy": round(exp, 2)}
            changed.append(f"{setup} on {code}")
            add_decision(conn, f"AUTO-TUNE — stopped looking for {setup.replace('_', ' ')} on {code}", "a",
                         f"Forward paper results: {len(rs)} trades averaging {exp:+.2f}R, below the {CUTOFF_R}R cut-off. "
                         f"Kora switched it off; turn it back on in Settings → Setups per instrument if you disagree.", code=code)
    if changed:
        cur = kv_get(conn, "settings", {}) or {}
        cur["setups_off"] = off
        kv_set(conn, "settings", cur)
        kv_set(conn, "auto_disabled", auto)
    return changed


def best_policy(conn) -> dict | None:
    stats = [s for s in paper.policy_stats(conn) if s["n"] >= MIN_TRADES_POLICY]
    if not stats:
        return None
    top = max(stats, key=lambda s: s["expectancy"])
    prev = kv_get(conn, "best_policy")
    if not prev or prev.get("policy") != top["policy"]:
        others = ", ".join(f"{s['name']} {s['expectancy']:+.2f}R" for s in stats if s is not top)
        add_decision(conn, f"INSIGHT — best trade management so far: {top['name']}", "b",
                     f"Across {top['n']} paper trades, {top['name']} averaged {top['expectancy']:+.2f}R per trade "
                     f"({top['win_rate']:.0f}% winners) vs {others}.")
    kv_set(conn, "best_policy", {k: top[k] for k in ("policy", "name", "n", "expectancy", "win_rate")})
    return top
