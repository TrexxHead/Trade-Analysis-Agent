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

1. **Auto-journal.** Attach the candle context (a small OHLC snapshot
   around entry/exit) to each closed trade automatically, so trade
   review doesn't depend on remembering what the chart looked like.
   Needs: store a compact candle window per trade at close time
   (`fetch_candles` already exists); a small `trade_charts` table.
2. **Canvas/candlestick chart page.** A real chart for the active
   symbol (XAUUSD to start), using `fetch_candles` for OHLC data and
   plotting proposal/trade markers on it. This is the biggest visible
   gap versus the Kora brief's "Charts" screen and versus TradingView-
   style platforms generally. Recommend a lightweight canvas-based
   renderer (e.g. lightweight-charts) rather than building an SVG
   candlestick renderer from scratch.
3. **Correlation/session structure notes.** Nothing to build yet -
   this folds into the chart page once it exists (session highlight
   bands using the strategy research's session windows).
4. **Composite performance score.** A single 0-100ish number combining
   win rate, profit factor, and rule-violation frequency (from
   `config/dashboard_feature_research.md`'s "Zella Score" finding) -
   pure computation over data we already have, no new ingestion.

## Phase 2 - Multi-instrument

The strategy engine (`src/strategy/`) and order-spec builder are
already platform/symbol-agnostic; `config/strategy.yaml` was
deliberately scoped down to just XAUUSD when the user confirmed
MT5-only trading, not because the code can't handle more.

1. Add additional MT5 symbols to `config/strategy.yaml` (e.g. other
   pairs/metals available on the same Deriv MT5 account) and confirm
   `run_scan.py` produces sane proposals for each via backtesting first.
2. A **Scanner** page: one row per watched symbol, current signal state
   (trending/pullback/no-setup), last scan time - a multi-symbol view
   of what `run_scan.py` already computes per-symbol today.
3. Re-evaluate Deriv-native synthetic indices/Multipliers/Options
   (`src/ingest/deriv.py`, already built and unit-tested but unused)
   only if the user actually starts trading Deriv-native products
   again - don't wire it into the live dashboard on spec.

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
