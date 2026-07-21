# Trade Analysis Agent

Pulls your trade history from Deriv and MetaTrader (via MetaApi.cloud), checks
every trade against your own trading-plan rules, and builds the data behind
daily/weekly/monthly reports (mistakes made, progress trends, stats). It can
also scan for a baseline trend-following setup and propose trades — but it
never places one without your explicit approval, and it refuses to execute
anything on a non-demo account unless you override that on purpose. See
[Trading proposals](#trading-proposals-propose--approve--execute) below.

TradingView has no API for pulling executed trade history on standard
accounts, so it isn't an ingestion source here — it stays your charting/alert
tool. If you want planned setups tracked too, TradingView alert webhooks can
be added later as a separate "planned vs. actual" comparison.

## How it fits together

```
Deriv API  ─┐
            ├─> ingest ─> SQLite (data/trades.db) ─> analysis (stats + rule checks) ─> report data (JSON)
MetaApi.cloud┘                                                                              │
(MT4/MT5)                                                                                   v
                                                                        Claude turns this into your
                                                                        actual daily/weekly/monthly
                                                                        report (chat dashboard, docx, pdf)
```

The scripts in this repo only fetch and crunch numbers. Generating the
polished report itself (the docx/pdf/dashboard) is done by Claude reading
`reports_out/<period>.json` — that keeps the report writing flexible instead
of baking a rigid template into code.

## Setup

### 1. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Get a Deriv API token

1. Log in at https://app.deriv.com — **use your demo/virtual account**, not your real-money one, until the strategy has a track record
2. Go to Settings -> API token
3. Create a token with **Read** and **Trade** scopes (Trade is required to place Multipliers/options orders, not just to read history)
4. Also grab your app_id from https://developers.deriv.com/docs (or use the shared demo app_id `1089` to start)

### 3. Set up MetaApi.cloud (for MT4/MT5)

MetaTrader has no official API, so MT4/5 access goes through MetaApi.cloud,
a broker-agnostic bridge:

1. Sign up at https://metaapi.cloud
2. Add your MT4/5 **demo/practice account** first (they ask for your broker
   server + login and password — a real investor-only password won't work
   here since this needs to place trades, not just read)
3. Generate an API token from the dashboard
4. Copy the generated `accountId` for the account you added

### 4. Configure environment

```bash
cp .env.example .env
```

Fill in `DERIV_API_TOKEN`, `DERIV_APP_ID`, `METAAPI_TOKEN`, `METAAPI_ACCOUNT_ID`.

### 5. Define your trading plan rules

Edit `config/rules.yaml` — this is what "mistakes" get measured against. It
ships with a starter set (max risk per trade, revenge trading, overtrading,
session filters, daily loss limit). Nothing here is generic advice; adjust
the numbers to match your actual plan.

### 6. Pull trades and generate a report

```bash
python scripts/run_ingest.py --source all
python scripts/run_report.py --period daily
```

This writes `reports_out/daily_<date>.json`. Hand that file to Claude and
ask for the report — it has everything needed (stats, rule violations,
trend vs. the previous period) to write the narrative and format it as a
chat artifact, Word doc, or PDF.

## Trading proposals (propose -> approve -> execute)

`config/strategy.yaml` defines a baseline trend-following-pullback strategy
(price above/below both EMAs for trend, pulled back near the fast EMA,
RSI recovering from an extreme) and which platform each instrument trades
on. Platform matters because risk works completely differently per product:

| Platform | Stop/target | How risk is capped |
|---|---|---|
| `mt4_mt5` | real price levels | position size sized from `risk %` and stop distance |
| `deriv_multipliers` | monetary stop-loss/take-profit | stake = risk amount directly (Multipliers cap max loss at stake by design) |
| `deriv_options` (Rise/Fall-style) | none — fixed duration instead | stake IS the risk, full stop; no stop-loss concept exists for these contracts |

Run a scan, then review and decide on whatever it finds — nothing executes
without an explicit decision:

```bash
python scripts/run_scan.py                    # checks every instrument in strategy.yaml, files proposals
python scripts/decide_proposal.py --list       # see pending proposals + rationale
python scripts/decide_proposal.py --approve 3  # executes proposal #3
python scripts/decide_proposal.py --reject 3 --note "not liking the setup"
```

**The demo-only guard is enforced at execution time, not just in config.**
`decide_proposal.py --approve` checks the *actual connected account* — Deriv's
loginid prefix (`VR...` = virtual) or MetaApi's reported account type — and
refuses to place the order if it doesn't resolve to demo/virtual. Going live
requires passing `--live` explicitly; there's no config flag that quietly
flips this, on purpose, since that's the kind of thing that shouldn't be
possible to trigger by accident.

**What's not done yet, on purpose:**
- The MetaApi candle/symbol-spec/order methods (`get_candles`,
  `get_symbol_specification`, `create_market_buy_order`, etc.) are written
  against MetaApi's documented API shape but haven't been verified against a
  live account or your installed SDK version — check `src/ingest/metatrader.py`
  against a real demo run before trusting it.
- The strategy is a conservative starting point, not a validated edge. Expect
  to tune `config/strategy.yaml` after watching it run on demo for a while.
- There's no backtest yet — proposals are only checked against live-fetched
  candles going forward from whenever you start running scans.

## Backtesting (no live connection needed)

Since live Deriv/MetaApi connections require an environment with normal
outbound network access, `scripts/run_backtest.py` lets the baseline
strategy be validated against historical data instead - useful while that's
being sorted out, and worth doing before going live either way.

Export candles from either MT4/5 or TradingView:

- **MT4**: Tools -> History Center, pick the symbol + a timeframe matching
  `config/strategy.yaml`'s `timeframe` (H1 by default), Export to CSV.
- **MT5**: `F2` (View -> History Center), same idea, Export Bars.
- **TradingView**: right-click the chart -> Export chart data (Pro plans
  and above). Set the chart to the matching timeframe first - the export
  only grabs whatever's currently loaded on the chart, which caps how much
  history you get depending on your plan.

Then:

```bash
python scripts/run_backtest.py \
  --csv path/to/export.csv \
  --symbol EURUSD \
  --tick-size 0.0001 \
  --tick-value 1.0 \
  --starting-balance 10000
```

`--tick-size`/`--tick-value` describe the instrument's pip value for your
account currency (check your broker/MT4 "Contract Specification" for the
symbol) - they're what turn a price move into a dollar P&L, same as the live
position-sizing math in `src/strategy/order_spec.py`.

**Scope, on purpose:** this only simulates `mt4_mt5`-style trades (real
price-level stop-loss/take-profit), since that's what MT4/5 history export
naturally gives you. Deriv Multipliers (monetary stop/take-profit) and
Options (fixed duration, no stop) would need different fill simulation logic
that isn't built yet - see `src/backtest/engine.py`.

Results are written to `backtests_out/<symbol>_<timestamp>.json` and reuse
the same stats/rule-checking code as the live reporting side, so a
backtest's numbers are directly comparable to what a real report would show.

## Scheduling

Once ingestion is verified working end-to-end, the daily/weekly/monthly
cadence can run as a recurring Routine in Claude Code that: runs
`run_ingest.py` + `run_report.py`, then generates and delivers the report
(chat dashboard + docx + email, per your preference). That step comes after
credentials are wired up and a first manual run looks correct.
