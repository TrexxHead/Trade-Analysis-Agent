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
    latest_analyses, list_decisions, open_plans, prune_analyses, record_plan, resolve_plan_row, save_analysis,
    save_trade_chart, upsert_quotes,
)
from src.analyst.calibration import resolve_plan
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
        try:
            snap = self.source.snapshot()
        except Exception as e:
            # keep the heartbeat alive so the hub shows *why* data stopped, and retry next tick
            kv_set(self.conn, "feed_error", {"at": now().isoformat(), "error": str(e)})
            hb = kv_get(self.conn, "heartbeat") or {}
            kv_set(self.conn, "heartbeat", {**hb, "ts": now().isoformat(), "source": self.source.name,
                                            "feed_ok": False, "feed_error": str(e)})
            return
        if kv_get(self.conn, "feed_error"):
            self.conn.execute("DELETE FROM kv WHERE key = 'feed_error'")
            self.conn.commit()
        if snap.get("specs"):
            kv_set(self.conn, "symbol_specs", snap["specs"])
        kv_set(self.conn, "orders", snap.get("orders", []))
        q = snap["quotes"]
        for code, quote in q.items():
            if code in self.day_open and not quote.get("day_open"):
                quote["day_open"] = self.day_open[code]
        upsert_quotes(self.conn, q)
        self.paper_tick(q)
        kv_set(self.conn, "account", snap["account"])
        kv_set(self.conn, "positions", snap["positions"])
        self.track_equity(snap["account"])
        state = self.control()
        kv_set(self.conn, "heartbeat", {
            "ts": now().isoformat(), "source": self.source.name, "latency_ms": self.source.latency_ms,
            "started_at": self.started_at.isoformat(), "state": state["state"],
            "last_cycle": self.last_cycle.isoformat() if self.last_cycle else None,
            "next_cycle": self.next_cycle_at().isoformat() if state["state"] != "STOPPED" else None,
            "errors": self.source.errors, "pid": os.getpid(), "feed_ok": True,
            "broker_connected": (snap.get("health") or {}).get("broker", True),
        })

    def paper_tick(self, quotes: dict):
        from src.analyst import paper
        try:
            for t in paper.process(self.conn, quotes):
                if t["policy"] != "tp1":
                    continue
                row = self.conn.execute("SELECT status, r, pnl FROM paper_trades WHERE id = ?", (t["id"],)).fetchone()
                add_decision(self.conn, f"PAPER — {t['code']} {t['direction']} {t['setup_name']} {row['status']} ({row['r']:+.2f}R)",
                             "g" if row["status"] == "won" else "r" if row["status"] == "lost" else "b",
                             f"Kora's paper account closed this trade at {row['pnl']:+.2f} USD. Same plan is also being "
                             f"tested with breakeven-to-TP2 and quick-1R management - see the Lab.", code=t["code"])
        except Exception as e:
            print(f"paper tick failed: {e}", file=sys.stderr)

    def refine(self):
        """Self-refinement: weekly retrain, auto-tune losing setups, best management style."""
        from src.analyst import refine
        conn = kora_connection()  # runs in a worker thread: SQLite connections can't cross threads
        try:
            if refine.retrain_due(conn):
                refine.retrain(conn, self.history_for_training)
            refine.auto_tune(conn)
            refine.best_policy(conn)
        except Exception as e:
            print(f"refine failed: {e}", file=sys.stderr)

    def history_for_training(self, meta: dict, days: int) -> list[dict]:
        from src.live.sources import fetch_candles_sync
        if self.source.name == "mt5":
            from src.ingest.mt5_local import candle_history
            return candle_history(meta["symbol"], "H1", days)
        if self.source.name == "sim":
            from src.live.sim import gen_candles
            return gen_candles(meta["code"], "H1", days * 24)
        if self.source.name == "metaapi":
            from src.ingest.metatrader import fetch_candle_history
            return fetch_candle_history(meta["symbol"], "H1", days)
        return fetch_candles_sync(meta, "H1", min(days * 24, 5000), "deriv")

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

        from src.analyst import strategies as ST
        settings = get_settings(self.conn)
        jobs = []
        for m in active:
            tfs = dict(self.wl["timeframes"])
            st = ST.active_for(settings, m["code"])
            if st and ST.mode_for(settings, m["code"]) != "kora":
                tf = st["params"]["entry_tf"]
                tfs[tf] = max(tfs.get(tf, 0), st["params"]["zone_lookback"] + st["params"]["slow"] + 150)
            jobs += [one(m, tf, n) for tf, n in tfs.items()]
        await asyncio.gather(*jobs)
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
        setups_off = settings.get("setups_off") or {}
        for m in active:
            code = m["code"]
            m = {**m, "setups_off": setups_off.get(code, [])}
            try:
                fund = instrument_fundamentals(m, self.calendar, reads_by_code)
                live = (quotes.get(code) or {}).get("price")
                rep = analyse_instrument(m, candles.get(code, {}), exec_tf, fund, stats, live_price=live)
                self.apply_strategy(rep, m, candles.get(code, {}), settings, stats)
                self.size_candidates(rep, m, settings)
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
                    key = f"{code}:{best['setup']}:{best['direction']}:{round(best['entry'] / (rep['atr'] or 1))}"
                    if record_plan(self.conn, key, code, best, rep["digits"], aid):
                        print(f"{code}: logged {best['grade']}-grade plan for forward tracking")
                        from src.analyst import paper
                        from src.store.db import list_plans
                        pid = (list_plans(self.conn, code, 1) or [{}])[0].get("id")
                        spec = (kv_get(self.conn, "symbol_specs", {}) or {}).get(code)
                        paper.open_for_plan(self.conn, pid, code, best, spec, settings.get("risk_pct", 1.0))
                if state == "RUNNING" and best:
                    self.maybe_alert(rep, best, settings)
                self.source.errors.pop(code, None)
            except Exception as e:
                errors.append(f"{code}: {e}")
                self.source.errors[code] = str(e)
                traceback.print_exc()

        self.update_correlations(candles)
        await self.resolve_plans(candles)
        kv_set(self.conn, "sparklines", {c: [round(x["close"], 6) for x in (tfs.get("H1") or [])[-48:]]
                                         for c, tfs in candles.items()})
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

    def apply_strategy(self, rep: dict, meta: dict, candles: dict, settings: dict, stats: dict):
        """Your saved strategy for this instrument: 'kora' leaves Kora's plans,
        'mine' replaces them with your strategy's, 'both' ranks them together."""
        from src.analyst import strategies as ST
        mode = ST.mode_for(settings, meta["code"])
        for c in rep["candidates"]:
            c.setdefault("source", "kora")
        rep["mode"] = mode
        st = ST.active_for(settings, meta["code"])
        if mode == "kora" or not st:
            return
        tf = st["params"]["entry_tf"]
        c = candles.get(tf) or []
        try:
            rep["strategy"] = {"id": st["id"], "name": st["name"], "tf": tf, **ST.live_state(c, st)}
            mine = ST.live_candidates(c, st, meta, stats)
        except Exception as e:
            rep["strategy"] = {"id": st["id"], "name": st["name"], "tf": tf, "ok": False, "text": f"error: {e}"}
            mine = []
        digits = rep["digits"]
        for m in mine:
            m["grade"] = "A" if m["confidence"] >= 70 else "B" if m["confidence"] >= 55 else "C"
            m["timeframe"] = tf
            m["distance_atr"] = round(abs((rep["price"] or m["entry"]) - m["entry"]) / (rep["atr"] or 1), 2)
            m["title"] = f"{m['setup_name']} — {m['direction'].upper()} {meta['code']}"
            m["levels_text"] = (f"Entry {m['entry']:,.{digits}f} (market) · SL {m['stop']:,.{digits}f} · TP1 {m['tp1']:,.{digits}f} (1R) · "
                                f"TP2 {m['tp2']:,.{digits}f} (2R) · Exit: {m['exit_rule']}")
        cands = mine if mode == "mine" else rep["candidates"] + mine
        cands.sort(key=lambda x: -x["confidence"])
        rep["candidates"] = cands
        best = cands[0] if cands else None
        if best and best.get("source") == "mine":
            rep["headline"] = f"{best['setup_name']} {best['direction']} · {best['confidence']}%"
            rep["verdict"] = (f"{'Actionable' if best['confidence'] >= 55 else 'Marginal'}: your strategy ({st['name']}) signals "
                              f"{best['direction']} — {best['confidence']}% calibrated confidence. Exit plan: {best['exit_rule']}.")
            rep["verdict_tone"] = "g" if best["confidence"] >= 55 else "a"
        elif mode == "mine":
            rep["headline"] = f"{st['name']}: no signal"
            rep["verdict"] = f"Your strategy has no signal on {tf}. {rep['strategy'].get('text', '')}"
            rep["verdict_tone"] = "r"

    def size_candidates(self, rep: dict, meta: dict, settings: dict):
        """Attach lots / $ risk / margin / tradability and a 0-10 rating to every plan."""
        from src.analyst.sizing import rating, size_plan
        acct = kv_get(self.conn, "account", {}) or {}
        spec = (kv_get(self.conn, "symbol_specs", {}) or {}).get(rep["code"])
        for c in rep["candidates"]:
            mpl = None
            if hasattr(self.source, "margin_per_lot"):
                try:
                    mpl = self.source.margin_per_lot(meta["symbol"], c["direction"], c["entry"])
                except Exception:
                    mpl = None
            c["sizing"] = size_plan(c, spec, acct.get("equity"), settings.get("risk_pct", 1.0),
                                    acct.get("free_margin"), mpl, settings.get("max_risk_pct", 3.0))
            c["rating"] = rating(c)

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

    async def resolve_plans(self, candles):
        """Forward-test every open Kora plan against the H1 candles printed since
        (fetching history for instruments that have left the watchlist)."""
        from src.analyst.config import by_code
        cat = by_code()
        for p in open_plans(self.conn):
            h1 = (candles.get(p["code"]) or {}).get("H1")
            if not h1 and p["code"] in cat:
                try:
                    h1 = await self.source.candles(cat[p["code"]]["symbol"], "H1", 120)
                except Exception:
                    h1 = None
            if not h1:
                continue
            res = resolve_plan(h1, p["created_at"], p)
            if res:
                status, r = res
                resolve_plan_row(self.conn, p["id"], status, r)
                rr = f"{r:+.2f}R" if r is not None else "n/a"   # a limit that never filled has no R
                word = {"won": f"hit TP1 for {rr}", "lost": "stopped out (-1R)", "not_filled": "limit never filled",
                        "expired": f"expired after 48 bars at {rr}"}.get(status, status)
                add_decision(self.conn, f"RESULT — {p['code']} {p['direction']} {p['setup_name']} {word}",
                             "g" if status == "won" else "r" if status == "lost" else "b",
                             f"Plan logged {p['created_at'][:16]} UTC ({p['grade']}-grade, {p['confidence']:.0f}%): entry "
                             f"{p['entry']:.{p['digits']}f}, SL {p['stop']:.{p['digits']}f}, TP1 {p['tp1']:.{p['digits']}f}. "
                             f"Outcome: {word}. This feeds Kora's forward track record.", code=p["code"])

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

    def send_brief_if_due(self):
        from src.analyst import briefing
        slot = briefing.due(self.conn)
        if not slot or self.control()["state"] == "STOPPED":
            return
        text = briefing.build(self.conn)
        if notify.channels() and notify.send(text, title=text.splitlines()[0]):
            print(f"Sent the {slot} brief.")
        briefing.mark_sent(self.conn, slot)  # mark even with no channel, so it doesn't retry all window

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
    def purge_simulation(self):
        """Running on real data after a simulated session: drop everything the
        simulator wrote so no fake equity, plans or prices linger in the hub."""
        hb = kv_get(self.conn, "heartbeat") or {}
        trained_on = kv_get(self.conn, "trained_source")
        if self.source.name == "sim":
            return
        if hb.get("source") == "sim" or kv_get(self.conn, "account", {}).get("type") == "SIMULATED":
            for key in ("equity_history", "equity_peak", "equity_sod", "account", "positions", "sparklines",
                        "correlations", "last_cycle"):
                self.conn.execute("DELETE FROM kv WHERE key = ?", (key,))
            for table in ("analyses", "decisions", "quotes", "kora_plans", "alerts_sent"):
                try:
                    self.conn.execute(f"DELETE FROM {table}")
                except Exception:
                    pass
            self.conn.execute("DELETE FROM trades WHERE id LIKE 'sim:%'")
            self.conn.commit()
            print("Cleared data left over from simulation mode.")
        if trained_on == "sim":
            self.conn.execute("DELETE FROM setup_stats")
            self.conn.commit()
            print("Calibration was trained on simulated data - cleared. Run scripts/train_kora.py on real history.")

    def sync_history(self, days: int = 30):
        """Mirror closed trades from the MT5 terminal into the trades table."""
        if self.source.name != "mt5":
            return
        try:
            from src.ingest import mt5_local
            from src.store.db import upsert_trades
            trades = mt5_local.fetch_trades(now() - timedelta(days=days), now() + timedelta(days=1))
            if trades:
                upsert_trades(self.conn, trades)
            kv_set(self.conn, "history_sync", {"at": now().isoformat(), "trades": len(trades), "days": days})
        except Exception as e:
            print(f"history sync failed: {e}", file=sys.stderr)

    async def run(self):
        from src.live.sources import resolve_source_name
        print(f"Kora starting - data source: {self.source.name} (KORA_DATA_SOURCE={os.environ.get('KORA_DATA_SOURCE') or 'auto'}). "
              "Analysis only, never places orders.")
        if self.source.name == "deriv":
            print("  Using Deriv's free public feed: prices and candles only, no account data. Open your MT5 "
                  "terminal and restart Kora to mirror your account (run scripts/doctor.py if MT5 is open).")
        await self.source.start()
        self.purge_simulation()
        self.sync_history(days=365)
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
                        self.send_brief_if_due()
                    if tick % 150 == 0 and tick:  # ~every 5 min
                        self.sync_history(days=7)
                    if tick % 900 == 0:  # ~every 30 min
                        await self.snapshot_trades()
                    if tick % 10800 == 300 or kv_get(self.conn, "retrain_now"):  # ~every 6h (first ~10 min after start)
                        await asyncio.to_thread(self.refine)
                except Exception:
                    traceback.print_exc()
                tick += 1
                await asyncio.sleep(FEED_SECONDS)
        finally:
            await self.source.close()
