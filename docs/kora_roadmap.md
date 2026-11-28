# Kora Terminal Roadmap

**Direction (Nov 2026):** Kora is a market-**analysis** agent, not an
execution agent. It assesses the watchlist (synthetic indices, gold, silver,
EURUSD, GBPUSD) for entries, technicals and fundamentals, and gives you a
detailed breakdown to decide on yourself. The earlier "Phase 5: full
autonomy" plan is dropped. Nothing in this repo should ever place, modify
or close an order on Kora's behalf.

The hub implements the Kora Terminal design handoff (11 screens, dark Kingston
palette, Montserrat/Jost/IBM Plex Mono, animated mood avatar) on the
existing Flask backend. The old "no Next.js rewrite" decision still stands.
The rule against fabricated UI also still stands: every chip, gauge and
number is backed by real data. Where real data doesn't exist yet, the
screen says so. Simulated data exists only behind `KORA_DATA_SOURCE=sim`
and is labelled on every screen.

## Built

**Kora analyst (`src/analyst/`)**
- Causal indicator library: EMA, RSI, ATR plus percentile, MACD, ADX/DI,
  Bollinger width, session VWAP, confirmed fractal swings, S/R clustering,
  resampling, correlations.
- Per-timeframe technical read (D1/H4/H1/M15), weighted multi-timeframe
  bias, alignment count, regime classification.
- Five setup detectors that output full plans (entry/SL/TP1/TP2/RR,
  confluence, objections, trigger, invalidation). Boom/Crash
  spike-direction penalty.
- Calibration ("training"): no-lookahead bar-by-bar replay with
  conservative fills, hit rate and expectancy per instrument/setup/direction,
  and Bayesian-style shrinkage into live confidence.
- Fundamentals: ForexFactory calendar, event-risk windows, consensus-vs-prior
  tilt per indicator type, USD-proxy and peer confirmation, live sessions.
- Engine: report, verdict, headline, reasoning chain, live signals,
  decision-log entry.
- Service (`scripts/run_kora.py`): MetaApi streaming feed into SQLite every
  2s, scheduled cycles, Run/Pause/Stop/Analyse-now control, equity
  history/peak/start-of-day tracking, calendar refresh, H1 correlation
  matrix, journal chart snapshots, de-duplicated Telegram/webhook alerts
  (setups, watch, news, no-stop-loss).
- Claude: Ask Kora chat grounded in a full context snapshot, plus optional
  "Kora's take" narratives.
- Tests: `tests/test_analyst.py` covers causality, simulator fills,
  shrinkage, engine invariants, training and fundamentals rules.

**Hub (`src/dashboard/`)**: all 11 handoff screens plus the per-instrument
breakdown, P&L calendar, trades, trade review, rule violations and
backtests. Live polling binds to `data-live` elements. Includes topbar
search, mood avatar, toasts, canvas chart with Kora-plan overlays, and a
settings API.

## Next improvements (in rough priority order)

1. **Verify on the live account.** Confirm MT5 symbol names, the MetaApi
   streaming subscription, and the `lossTickValue`-based risk figures
   against the terminal. Run `train_kora.py` on real history and sanity-check
   the hit rates.
2. **Outcome tracking for Kora's own plans.** Record each A/B plan and
   resolve it later (TP1/SL/expired) against real candles. Show Kora's live
   forward track record next to the backtested one. This is the honest
   scorecard.
3. **Walk-forward calibration.** Train on older history and validate on the
   most recent slice. Flag setups whose live hit rate diverges from their
   training estimate.
4. **Post-release surprises.** Add a source with *actual* figures (e.g. a
   paid calendar API) so Kora can react to the release, not only the
   expectation.
5. **Multi-timeframe triggers.** Let an H1 plan wait for an M15
   confirmation candle and notify again when it triggers.
6. **Daily digest.** A pre-London / pre-NY summary alert of the board: top
   plans, event risk, open-risk snapshot.
7. **Journal ↔ Kora loop.** Track which Kora grades you take and how they
   do versus discretionary trades, and surface that in Analytics.
8. **Mobile layout** for the hub (the design is desktop-first). An alert
   link already opens the breakdown page.
9. TradingView alert webhooks as an extra input. It was listed in the
   handoff's Settings and isn't built yet, so it isn't shown in the UI.

## Explicitly not planned

- Any order placement, modification or closing by Kora or from the hub.
- Fabricated telemetry or decorative UI with no data behind it.
- A Next.js/React rewrite.
