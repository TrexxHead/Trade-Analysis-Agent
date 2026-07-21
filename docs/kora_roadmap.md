# Kora Terminal Roadmap

The Kora Terminal design brief (a design-tool export covering an 11-screen
Next.js command center) is the long-term direction for this dashboard: a
dark, data-dense trading terminal with a reactive avatar, an LLM chat
assistant, multi-instrument scanning, and eventually a fully autonomous
execution mode. This dashboard has been reskinned to Kora's dark visual
language (`src/dashboard/templates/base.html`) and now serves real trade
history, R-multiple analytics, and live open positions - but most of the
brief is still aspirational. This doc lays out what's built, what isn't,
and a sequenced plan for closing the gap, so "build toward the full
vision" has a concrete order of operations rather than staying a slogan.

Nothing here should ship as fake UI ahead of the real capability behind
it - each phase below only adds a nav item, chip, or page once the data
or logic it shows is real. That's the same rule already applied to the
shell reskin (no fake latency/agent-status chips, no search box that
doesn't search).

## What's real today

- Trade history, P&L calendar, R-multiple/expectancy stats, mistake
  rules, backtesting - all reading from `data/trades.db`, sourced from
  MetaApi (MT4/5) ingestion.
- Trade tagging (setup/emotion/notes/risk) and breakdowns by hour,
  weekday, setup, and emotion.
- Live open positions (`/positions`), fetched directly from MetaApi's
  RPC connection rather than cached, since positions aren't "trades"
  until they close.
- Propose -> approve -> execute workflow, always demo-gated from the
  dashboard; going live is a deliberate CLI flag, not a UI toggle.
- Single dark theme, sidebar/topbar shell, static (non-animated) Kora
  avatar.

## Phase 1 - Depth on the single-instrument (XAUUSD/MT5) path

Lowest risk, highest immediate value - extends what already works
without introducing new infra.

**Done:**
- **Candlestick chart page** (`/charts`) - lightweight-charts (CDN)
  rendering OHLC from a new `/api/candles/<symbol>` endpoint, with
  trade entry/exit markers overlaid from stored trades. Works for any
  configured `mt4_mt5` instrument, not just XAUUSD. Note: this
  couldn't be visually verified end-to-end in the sandboxed dev
  environment this was built in (the CDN script and MetaApi calls are
  both network-restricted there) - verify it renders correctly on a
  real machine with normal internet access before relying on it.
- **Composite performance score** (`compute_composite_score` in
  `src/analysis/metrics.py`, shown on Overview) - a simple, transparent
  0-100 blend of profit factor, win rate, and discipline (share of
  trades with no rule violation). Explicitly documented as not a
  validated edge metric, just an at-a-glance number.

**Still open:**
1. **Auto-journal.** Attach the candle context (a small OHLC snapshot
   around entry/exit) to each closed trade automatically, so trade
   review doesn't depend on remembering what the chart looked like -
   the new `/charts` page covers this manually (pick the symbol, find
   the trade markers) but doesn't yet attach a saved snapshot per trade.
   Needs: store a compact candle window per trade at close time
   (`fetch_candles` already exists); a small `trade_charts` table.
2. **Correlation/session structure notes** on the chart page - session
   highlight bands using the strategy research's session windows.

## Phase 2 - Multi-instrument

The strategy engine (`src/strategy/`) and order-spec builder are
already platform/symbol-agnostic; `config/strategy.yaml` was
deliberately scoped down to just XAUUSD when the user confirmed
MT5-only trading, not because the code can't handle more.

**Done:** `config/strategy.yaml` now also watches Volatility 10 Index,
Volatility 75 Index, and Step Index (Deriv synthetic indices, traded
via the same MT5 account - not the Deriv-native WebSocket API).
`src/strategy/scanning.py` merges per-instrument overrides over the
shared defaults, `run_scan.py` scans every instrument independently
(one failing symbol doesn't stop the rest) and persists a per-instrument
`scan_status` row either way, and the dashboard's **Scanner** page reads
that cached status rather than calling MetaApi live - a live round-trip
per watched instrument on every page load doesn't scale past one symbol
given MetaApi's default 60s per-request timeout.

**Still open:**
1. **Backtest each new instrument before trusting its proposals.** The
   synthetic indices inherit the XAUUSD-tuned trend-pullback defaults
   (EMA50/200, RSI14) untested - they trade 24/7 on a constant
   statistical volatility model with no session/news structure, so
   those defaults are a guess, not a validated setting. Run
   `scripts/run_backtest.py` per instrument and add a `trend`/`entry`/
   `exit` override block in `config/strategy.yaml` once tuned.
2. **Confirm exact MT5 symbol strings** against the live account's
   Market Watch - a mismatched name currently just shows up as a
   per-instrument scan error on the Scanner page rather than crashing
   anything, but it means that instrument silently never gets scanned
   until the name is fixed.
3. **Cross-instrument risk awareness.** Right now each instrument is
   risk-sized independently (`risk_pct` of balance per trade) with no
   check for combined exposure if multiple instruments signal at once -
   worth a portfolio-level risk cap once more than one instrument is
   live-scanning regularly.
4. Re-evaluate Deriv-native synthetic indices/Multipliers/Options
   (`src/ingest/deriv.py`, already built and unit-tested but unused)
   only if the user actually starts trading Deriv-native products
   directly (not via MT5) - don't wire it into the live dashboard on spec.

## Phase 3 - Risk analytics

1. **VaR / stress testing.** Historical-simulation VaR over the
   existing trade/equity history (no new data source - `compute_period_stats`
   already tracks the equity curve this would be built on).
2. **Correlation heatmap** across watched symbols, once Phase 2's
   multi-symbol candle fetching exists.
3. **Economic calendar.** This is the one item here that needs a new
   external data source (no news/calendar API is wired up anywhere in
   this project yet) - lowest priority of this phase since it adds an
   external dependency for comparatively low value versus the
   analytics above.

## Phase 4 - Kora agent (chat + reasoning surface)

The Kora brief's "Kora Agent" screen (chat assistant, reasoning chains,
decision logs) is the most speculative part of the vision - it's a
genuinely new capability (an LLM in the loop), not a restyle of
something that already exists.

1. **Decision log, non-LLM first.** Before any chat UI, make the
   *existing* automated decisions legible: every scan run and every
   rule evaluation already happens in `run_scan.py`/`analysis/rules.py`
   - log each one (symbol, inputs, output, timestamp) to a table and
   surface it as a real "what did the system just do and why" feed.
   This alone covers most of the value of a "reasoning log" without
   an LLM.
2. **Chat assistant**, scoped narrowly at first: answer questions over
   the user's own stored trade data ("how did I do on gold last week",
   "what's my win rate after 2pm") - a constrained retrieval-style
   assistant, not a free-form agent with tool access to place trades.
3. Only after (1) and (2) are solid: consider whether a chat-driven
   "propose a trade" flow adds anything over the existing
   scan -> approve UI, rather than building it because the brief shows it.

## Phase 5 - Full autonomy (explicitly deferred, explicitly gated)

Per an explicit decision made when this reskin started: full autonomy
(execution without a human approving each trade) should eventually be
a *real* mode, not just a styled-around-real-state mock - but it is
**not being built now**, and shouldn't be built casually later either.
Recommended gating before it's even attempted:

1. A minimum track record on the propose/approve workflow: a fixed
   number of consecutive weeks (e.g. 8-12) of demo-account approved
   trades meeting the strategy research's target win-rate/expectancy
   bounds, computed from real `trade_proposals` + `trades` data - not
   asserted.
2. An explicit, separate opt-in per symbol/strategy combination (not a
   single global toggle), defaulting off, requiring re-confirmation if
   disabled and re-enabled.
3. Hard risk backstops independent of the strategy logic itself: a
   circuit breaker that force-disables autonomy the moment
   `daily_loss_limit_breached` fires, regardless of what the strategy
   code wants to do next.
4. Even once built, autonomy should start demo-only (same
   `require_demo=True` default used everywhere else in this project)
   before a further, separate decision to allow live-account autonomy.

This phase is intentionally last and intentionally the most
conservatively gated item in this roadmap - the cost of an autonomous
execution bug is categorically different from a dashboard bug.

## Explicitly not planned

- Fabricated telemetry (fake latency/agent-status chips, decorative
  search boxes that don't search) - anything in the Kora brief that's
  purely cosmetic and has no real data behind it stays out until the
  real thing exists.
- A full Next.js/React rewrite - the reskin decision was to keep the
  tested Flask backend and restyle it, not migrate stacks.
