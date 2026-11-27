# Kora Terminal — Improvement Checklist

Snapshot date: 27 Nov 2026 · branch `claude/nifty-cerf-oqqv2t`
Priority: **P1** = do next (correctness, daily use) · **P2** = high value · **P3** = polish / later.
Tick items off as they land. Screens as of this date: `docs/snapshots/2026-11-27/`.

## Where things stand
- [x] Live data from your Deriv MT5 terminal (free, read-only): prices, candles, positions, balance, history
- [x] Kora analysis on D1/H4/H1/M15, five setup detectors, calibration on history, fundamentals and event risk
- [x] Forward track record of every A/B plan Kora issues
- [x] 11 screens plus breakdowns, backtests, chart indicators and templates, editable watchlist, MT5 history sync
- [x] Telegram/webhook alerts, Ask Kora (Claude), `doctor.py` diagnostics

---

## 1. Data and connectivity
- [ ] **P1** Read price digits, tick size and tick value from MT5 `symbol_info` instead of `instruments.yaml`, so prices display with the broker's exact precision (XAGUSD and Boom/Crash currently show extra decimals)
- [ ] **P1** Show the last traded **bid** (or bid/ask) rather than the bid/ask midpoint, matching what MT5 shows
- [ ] **P1** Fix the Deriv public feed fallback (HTTP 520 from `ws.derivws.com` with the shared app_id 1089): register your own free app_id at api.deriv.com and/or update to Deriv's current endpoint
- [ ] **P1** Auto-reconnect to MT5 if the terminal restarts or loses connection, and show a "MT5 disconnected" banner rather than silently stale data
- [ ] **P2** Show live spread per instrument (bid/ask) on the Scanner and in setup filters (skip setups when spread > N× normal)
- [ ] **P2** Detect the MT5 server timezone offset and normalise bar and deal times to UTC (assumed GMT today)
- [ ] **P2** Cache candles locally (SQLite) so charts and backtests load instantly and use fewer terminal calls
- [ ] **P3** Support several MT5 accounts (demo + real) with an account switcher

## 2. Kora analyst quality
- [ ] **P1** Run `train_kora.py` on real MT5 history for all five focus instruments; review per-setup expectancy and disable setups that are negative on an instrument
- [ ] **P1** Per-instrument setup on/off switches in Settings (e.g. no range reversion on Boom/Crash)
- [ ] **P2** Boom/Crash-specific logic: spike detection, ticks-since-last-spike, spike-direction-only plans
- [ ] **P2** Multi-timeframe triggers: an H1 plan waits for an M15 confirmation candle and alerts again when it triggers
- [ ] **P2** Walk-forward calibration (train on older data, validate on recent) with a warning when live results drift from the backtest
- [ ] **P2** Position-size suggestion on every plan: lots for a chosen % risk from MT5 tick value (read-only, display only)
- [ ] **P2** Session filters per instrument (e.g. gold plans only London/NY)
- [ ] **P3** Additional setups: order-block/FVG retest, double top/bottom, trend-line break
- [ ] **P3** Post-release macro surprises (needs a calendar source with *actual* figures)

## 3. Charts
- [ ] **P1** Drawing tools: horizontal line, trend line, rectangle zone, saved per symbol
- [ ] **P1** Shade Kora's plan as an entry→TP and entry→SL zone, not just lines
- [ ] **P2** Live-updating last candle from MT5 ticks (today: live close only, new bars on refresh)
- [ ] **P2** Load more history when panning left (infinite scroll)
- [ ] **P2** Multi-chart layout (2×2) for the focus watchlist
- [ ] **P2** More indicators: Ichimoku, Supertrend, Parabolic SAR, pivots, ADX pane, previous day H/L
- [ ] **P3** Heikin-Ashi and line chart types; log scale
- [ ] **P3** Screenshot/export chart image into the Journal

## 4. Dashboard (live MT5 mirror)
- [ ] **P1** Equity curve from real account snapshots plus closed-trade balance, with range tabs (1D, 1W, 1M, All)
- [ ] **P1** Today's realised P/L, trade count and win rate tile
- [ ] **P2** Balance-operations awareness (deposits/withdrawals excluded from P/L and drawdown)
- [ ] **P2** Customisable tile order / hide panels
- [ ] **P3** Greeting and a daily "Kora summary" card at the top

## 5. Positions and account
- [ ] **P1** Pending orders tab (limit/stop orders from MT5 `orders_get`)
- [ ] **P2** Per-position mini chart with entry/SL/TP
- [ ] **P2** Exposure per instrument in lots and notional, and correlation warning across open positions
- [ ] **P2** Export history to CSV
- [ ] **P3** Monthly statement view (P/L, commissions, swaps)

## 6. Journal
- [ ] **P1** Edit setup, emotion, lesson inline on the card (no page change)
- [ ] **P1** Auto-suggest the setup tag from Kora's matching plan at entry
- [ ] **P2** Screenshot upload or auto chart snapshot at M15 around entry
- [ ] **P2** Weekly review page: best and worst trades, rule breaks, Kora-aligned vs discretionary results
- [ ] **P3** Tags beyond setup/emotion (mistake type, market condition), custom tag sets

## 7. Analytics and risk
- [ ] **P1** "Kora aligned vs discretionary" performance split
- [ ] **P2** Performance by instrument, session and day-of-week heatmap
- [ ] **P2** R-multiple distribution once risk is known per trade (from SL at entry via MT5 deal/order data)
- [ ] **P2** Risk: daily loss limit and max-drawdown alerts tied to *real* account equity
- [ ] **P3** Monte Carlo of the next 100 trades from your own history

## 8. Scanner
- [ ] **P2** Live-updating rows (score/regime changes highlight)
- [ ] **P2** Column chooser and saved scanner views
- [ ] **P3** Custom screener rules (e.g. RSI < 30 and ADX > 25 on H1)

## 9. Calendar and fundamentals
- [ ] **P2** "My currencies only" toggle (default on)
- [ ] **P2** Per-event history (previous surprises) and gold impact notes
- [ ] **P3** News headlines feed for gold/silver/USD

## 10. Alerts and Kora on the go
- [ ] **P1** Set up Telegram (free) and test from Settings
- [ ] **P2** Alert when a live plan hits TP1 or its stop, and when a limit entry fills
- [ ] **P2** Pre-London and pre-New York board summary
- [ ] **P2** Ask Kora via Telegram (reply to an alert to ask about it)
- [ ] **P3** Quiet hours and per-instrument alert levels

## 11. Backtesting
- [ ] **P2** Compare two runs side by side; parameter sweep (stop ATR, RR, setups)
- [ ] **P2** Include spread/commission in results
- [ ] **P2** Plot backtest trades on the chart
- [ ] **P3** Monthly breakdown and longest losing streak

## 12. Design and UX
- [ ] **P1** Phone and tablet layout: bottom tab bar, single-column cards, touch-friendly chart (pinch zoom)
- [ ] **P2** Light theme option (system-aware)
- [ ] **P2** Loading skeletons and empty states for every panel
- [ ] **P2** Keyboard shortcuts (`g d` dashboard, `g c` charts, `/` search, `[`/`]` switch symbol)
- [ ] **P3** Command palette (⌘K) and onboarding tour
- [ ] **P3** Installable app (PWA) with home-screen icon

## 13. Access, reliability and security
- [ ] **P1** Remote access from any device: Tailscale or Cloudflare Tunnel (see `docs/remote_access.md`)
- [ ] **P1** Set `DASHBOARD_PASSWORD` before any remote access; proper login page with a session cookie (not just the browser popup)
- [ ] **P1** Auto-start Kora + hub at Windows logon (Task Scheduler: `run_kora.bat`, `run_dashboard.bat`) and keep the PC from sleeping while MT5 runs
- [ ] **P2** Health page: service uptime, last cycle, MT5 connection, errors, log tail
- [ ] **P2** Daily backup of `data/trades.db`
- [ ] **P3** Optional always-on Windows VPS (paid) so Kora runs when the laptop is off

## 14. Code quality
- [ ] **P2** Tests for the dashboard routes and APIs (Flask test client)
- [ ] **P2** GitHub Actions CI running `pytest` on every push
- [ ] **P3** Split `app.py` into blueprints per screen
