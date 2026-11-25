"""The Kora service: one long-running async process that
  - keeps live quotes / positions / account state flowing into SQLite (every ~2s),
  - re-analyses the whole watchlist every `interval_minutes`,
  - logs explainable decisions, refreshes the economic calendar and the
    cross-instrument correlation matrix, snapshots candles for closed trades
    (the journal's chart thumbnails), and pushes alerts.

Kora never places, modifies or closes an order. Control state (RUNNING /
PAUSED / STOPPED) is set from the hub's Kora page and read here each loop:
  RUNNING - analyse + alert;  PAUSED - analyse, alerts muted;  STOPPED - feed only.
"""
from __future__ import annotations

import asyncio
import os
import sys
import traceback
import uuid
from datetime import datetime, timedelta, timezone

from src.analyst import llm, notify
from src.analyst.config import get_settings, instruments, load_watchlist
from src.analyst.engine import analyse_instrument, decision_for
from src.analyst.fundamentals import fetch_calendar, instrument_fundamentals
from src.analyst import ta
from src.store.db import (
    add_decision, get_quotes, get_setup_stats, get_trade_charts, get_trades, kora_connection, kv_get, kv_set,
    latest_analyses, list_decisions, prune_analyses, save_analysis, save_trade_chart, upsert_quotes,
)
from src.live.sources import make_source

FEED_SECONDS = 2.0
CALENDAR_REFRESH = timedelta(hours=1)


def now() -> datetime:
    return datetime.now(timezone.utc)


class KoraService:
    def __init__(self):
        self.conn = kora_connection()
        self.wl = load_watchlist()
        self.all_instruments = instruments()
        self.source = make_source(self.all_instruments)
        self.started_at = now()
        self.last_cycle: datetime | None = None
        self.last_calendar: datetime | None = None
        self.calendar: list[dict] = kv_get(self.conn, "calendar", []) or []
        self.day_open: dict[str, float] = {}

    # ------------------------------------------------------------------ feed
    def write_feed(self):
        snap = self.source.snapshot()
        q = snap["quotes"]
        for code, quote in q.items():
            if code in self.day_open and not quote.get("day_open"):
                quote["day_open"] = self.day_open[code]
        upsert_quotes(self.conn, q)
        kv_set(self.conn, "account", snap["account"])
        kv_set(self.conn, "positions", snap["positions"])
        self.track_equity(snap["account"])
        state = self.control()
        kv_set(self.conn, "heartbeat", {
            "ts": now().isoformat(), "source": self.source.name, "latency_ms": self.source.latency_ms,
            "started_at": self.started_at.isoformat(), "state": state["state"],
            "last_cycle": self.last_cycle.isoformat() if self.last_cycle else None,
            "next_cycle": self.next_cycle_at().isoformat() if state["state"] != "STOPPED" else None,
            "errors": self.source.errors, "pid": os.getpid(),
        })

    def track_equity(self, account: dict):
        eq = account.get("equity")
        if eq is None:
            return
        peak = kv_get(self.conn, "equity_peak")
        if peak is None or eq > peak:
            kv_set(self.conn, "equity_peak", eq)
        hist = kv_get(self.conn, "equity_history", []) or []
        if not hist or now() - datetime.fromisoformat(hist[-1][0]) >= timedelta(minutes=5):
            hist.append([now().isoformat(), round(eq, 2)])
            kv_set(self.conn, "equity_history", hist[-600:])
        # start-of-day equity (UTC) for the daily P/L figure
        sod = kv_get(self.conn, "equity_sod") or {}
        today = now().date().isoformat()
        if sod.get("date") != today:
            kv_set(self.conn, "equity_sod", {"date": today, "equity": eq})

    def control(self) -> dict:
        return kv_get(self.conn, "agent_control", None) or {"state": "RUNNING", "run_now": False}

    def next_cycle_at(self) -> datetime:
        mins = get_settings(self.conn)["interval_minutes"]
        return (self.last_cycle or self.started_at - timedelta(days=1)) + timedelta(minutes=mins)

    # -------------------------------------------------------------- analysis
    async def fetch_all(self, active: list[dict]) -> dict[str, dict[str, list[dict]]]:
        sem = asyncio.Semaphore(4)
        out: dict[str, dict[str, list[dict]]] = {m["code"]: {} for m in active}

        async def one(m, tf, n):
            async with sem:
                try:
                    out[m["code"]][tf] = await self.source.candles(m["symbol"], tf, n)
                except Exception as e:
                    self.source.errors[m["code"]] = f"{tf} candles failed: {e}"

        await asyncio.gather(*(one(m, tf, n) for m in active for tf, n in self.wl["timeframes"].items()))
        return out

    async def cycle(self, state: str):
        settings = get_settings(self.conn)
        active = instruments(include_disabled=False, settings=settings)
        cycle_id = uuid.uuid4().hex[:10]
        t0 = now()
        candles = await self.fetch_all(active)
        quotes = get_quotes(self.conn)
        stats = get_setup_stats(self.conn)
        exec_tf = self.wl.get("execution_timeframe", "H1")

        # first pass: technical reads, so fundamentals can use cross-asset context
        from src.analyst.technical import Features, read_at
        reads_by_code = {}
        for m in active:
            c = candles.get(m["code"], {})
            reads_by_code[m["code"]] = {tf: read_at(Features(v), len(v) - 1, tf) for tf, v in c.items() if v and len(v) >= 60}

        previous = latest_analyses(self.conn)
        last_dec = {d["code"]: d for d in reversed(list_decisions(self.conn, limit=200)) if d["code"]}
        reports, n_setups, errors = {}, 0, []
        for m in active:
            code = m["code"]
            try:
                fund = instrument_fundamentals(m, self.calendar, reads_by_code)
                live = (quotes.get(code) or {}).get("price")
                rep = analyse_instrument(m, candles.get(code, {}), exec_tf, fund, stats, live_price=live)
                if rep.get("day_open"):
                    self.day_open[code] = rep["day_open"]
                best = rep["candidates"][0] if rep["candidates"] else None
                prev_best = (previous.get(code) or {}).get("candidates", [None])
                prev_best = prev_best[0] if prev_best else None
                changed = bool(best) and (not prev_best or prev_best["setup"] != best["setup"]
                                          or prev_best["direction"] != best["direction"])
                if settings.get("llm_narratives") and llm.available() and (changed or not (previous.get(code) or {}).get("narrative")):
                    rep["narrative"] = await asyncio.to_thread(llm.narrative, rep)
                elif previous.get(code, {}).get("narrative") and not changed:
                    rep["narrative"] = previous[code]["narrative"]
                aid = save_analysis(self.conn, code, cycle_id, rep)
                reports[code] = rep
                d = decision_for(rep)
                prev = last_dec.get(code)
                if not prev or prev["action"] != d["action"] or (now() - datetime.fromisoformat(prev["created_at"])) > timedelta(hours=4):
                    add_decision(self.conn, d["action"], d["tone"], d["explanation"], code=code,
                                 confidence=d["confidence"], analysis_id=aid)
                if best and best["confidence"] >= 55:
                    n_setups += 1
                if state == "RUNNING" and best:
                    self.maybe_alert(rep, best, settings)
                self.source.errors.pop(code, None)
            except Exception as e:
                errors.append(f"{code}: {e}")
                self.source.errors[code] = str(e)
                traceback.print_exc()

        self.update_correlations(candles)
        secs = (now() - t0).total_seconds()
        add_decision(self.conn, f"CYCLE — {len(reports)} instruments analysed", "b",
                     f"Full multi-timeframe pass ({', '.join(self.wl['timeframes'])}) in {secs:.1f}s. "
                     f"{n_setups} actionable setup(s) at ≥55% confidence." +
                     (f" Errors: {'; '.join(errors[:3])}" if errors else "") +
                     ("" if state == "RUNNING" else " Alerts muted (Kora paused)."))
        kv_set(self.conn, "last_cycle", {"id": cycle_id, "at": now().isoformat(), "seconds": secs,
                                         "instruments": len(reports), "setups": n_setups, "errors": errors})
        self.last_cycle = now()
        prune_analyses(self.conn)

    def maybe_alert(self, rep: dict, best: dict, settings: dict):
        thr = settings["alert_threshold"]
        n = settings["notify"]
        if best["confidence"] >= thr and n.get("setups"):
            key = f"setup:{rep['code']}:{best['setup']}:{best['direction']}:{round(best['entry'] / (rep['atr'] or 1))}"
            notify.notify_once(self.conn, key, notify.setup_message(rep, best))
        elif 55 <= best["confidence"] < thr and n.get("watch"):
            key = f"watch:{rep['code']}:{best['setup']}:{best['direction']}"
            notify.notify_once(self.conn, key, f"KORA · watch · {rep['code']} {best['direction']} "
                                               f"({best['setup_name']}, {best['confidence']}%) — {best['levels_text']}")

    def update_correlations(self, candles):
        series = {}
        for code, tfs in candles.items():
            h1 = tfs.get("H1") or []
            if len(h1) > 60:
                series[code] = {c["time"]: c["close"] for c in h1[-220:]}
        codes = list(series)
        common = sorted(set.intersection(*(set(s) for s in series.values()))) if series else []
        rets = {c: ta.returns([series[c][t] for t in common]) for c in codes}
        matrix = {a: {b: ta.correlation(rets[a], rets[b]) for b in codes} for a in codes}
        kv_set(self.conn, "correlations", {"codes": codes, "matrix": matrix, "bars": len(common), "tf": "H1"})

    # ---------------------------------------------------------- housekeeping
    async def refresh_calendar(self):
        try:
            cal = await asyncio.to_thread(fetch_calendar)
            if cal:
                self.calendar = cal
                kv_set(self.conn, "calendar", cal)
            self.last_calendar = now()
        except Exception as e:
            print(f"calendar refresh failed: {e}", file=sys.stderr)

    def news_alerts(self):
        settings = get_settings(self.conn)
        if not settings["notify"].get("news") or self.control()["state"] != "RUNNING":
            return
        ccys = {c for m in instruments(False, settings) for c in m.get("currencies", [])}
        for e in self.calendar:
            if e["impact"] != "High" or e["currency"] not in ccys:
                continue
            mins = (datetime.fromisoformat(e["time"]) - now()).total_seconds() / 60
            if 0 < mins <= 15:
                notify.notify_once(self.conn, f"news:{e['currency']}:{e['title']}:{e['time']}",
                                   f"KORA · high-impact news in {int(mins)} min: {e['currency']} {e['title']} "
                                   f"(fcst {e['forecast'] or '—'}, prev {e['previous'] or '—'}). "
                                   f"Expect a volatility spike on gold/silver/FX.", within_hours=24)

    def risk_alerts(self):
        settings = get_settings(self.conn)
        if not settings["notify"].get("risk"):
            return
        for p in kv_get(self.conn, "positions", []) or []:
            if not p.get("stop_loss"):
                notify.notify_once(self.conn, f"nosl:{p['id']}",
                                   f"KORA · risk · {p['code']} {p['direction']} {p['volume']} has NO stop-loss - "
                                   f"its downside is uncapped.", within_hours=24)

    async def snapshot_trades(self, limit: int = 5):
        """Store an H1 candle window around each closed trade (journal thumbnails)."""
        have = get_trade_charts(self.conn)
        sym_ok = {m["symbol"] for m in self.all_instruments} | {m["code"] for m in self.all_instruments}
        todo = [t for t in reversed(get_trades(self.conn)) if t["id"] not in have and t["symbol"] in sym_ok][:limit]
        for t in todo:
            try:
                close = datetime.fromisoformat(t["close_time"])
                if close.tzinfo is None:
                    close = close.replace(tzinfo=timezone.utc)
                end = close + timedelta(hours=8)
                if end > now():
                    continue  # wait until the after-trade window has printed
                open_ = datetime.fromisoformat(t["open_time"])
                if open_.tzinfo is None:
                    open_ = open_.replace(tzinfo=timezone.utc)
                bars = int(max(24, min(200, (end - open_).total_seconds() / 3600 + 24)))
                c = await self.source.candles(t["symbol"], "H1", bars, end=end)
                save_trade_chart(self.conn, t["id"], "H1", c)
            except Exception as e:
                print(f"trade snapshot failed for {t['id']}: {e}", file=sys.stderr)

    # ------------------------------------------------------------------ main
    async def run(self):
        print(f"Kora starting ({self.source.name}) - analysis only, never places orders.")
        await self.source.start()
        if not kv_get(self.conn, "agent_control"):
            kv_set(self.conn, "agent_control", {"state": "RUNNING", "run_now": False})
        add_decision(self.conn, f"START — Kora service online ({self.source.name})", "b",
                     f"Watching {len(self.all_instruments)} instruments. Live feed every {FEED_SECONDS:.0f}s; "
                     f"analysis every {get_settings(self.conn)['interval_minutes']} min. Analysis only - no orders are ever placed.")
        tick = 0
        try:
            while True:
                try:
                    self.write_feed()
                    ctl = self.control()
                    if not self.last_calendar or now() - self.last_calendar > CALENDAR_REFRESH:
                        await self.refresh_calendar()
                    due = now() >= self.next_cycle_at()
                    if ctl["state"] != "STOPPED" and (due or ctl.get("run_now")):
                        if ctl.get("run_now"):
                            kv_set(self.conn, "agent_control", {**ctl, "run_now": False})
                        await self.cycle(ctl["state"])
                    if tick % 30 == 0:  # ~every minute
                        self.news_alerts()
                        self.risk_alerts()
                    if tick % 900 == 0:  # ~every 30 min
                        await self.snapshot_trades()
                except Exception:
                    traceback.print_exc()
                tick += 1
                await asyncio.sleep(FEED_SECONDS)
        finally:
            await self.source.close()
