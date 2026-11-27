# Trade Analysis Agent — Kora Terminal

Two things in one repo:

1. **Kora, a market-analysis agent.** Kora watches your instruments (Deriv
   synthetic indices, gold, silver, EURUSD and GBPUSD on Deriv MT5) and, every
   cycle, writes a full breakdown of each one: multi-timeframe technicals
   (D1/H4/H1/M15), key levels, the fundamental picture (economic calendar,
   event risk, cross-asset context), and ranked **entry plans** with exact
   entry, stop and two targets, reward:risk, why the plan qualifies, what argues
   against it, and what invalidates it. Each setup's confidence is **calibrated
   against how that setup has actually performed on that instrument's
   history**. When something qualifies, Kora alerts you (Telegram/webhook).
   **Kora never places, modifies or closes a trade.** It assesses; you decide
   and execute.
2. **Kora Terminal, your trading hub.** A dark, data-dense web app with 11
   screens: Dashboard, Charts, Kora Agent, Positions, Risk, Analytics,
   Journal, Scanner, Economic Calendar, AI Insights (chat with Kora) and
   Settings. It's built on your real MT5 account data, trade history and
   Kora's analysis.

```
                 ┌──────────────── scripts/run_kora.py (one process) ────────────────┐
MetaApi (MT5) ──>│ streaming quotes/positions/account ──> SQLite (every 2s)          │
  candles  ─────>│ every N min: technicals + setups + calibration + fundamentals     │──> data/trades.db ──> Kora Terminal
ForexFactory ───>│ calendar · correlations · journal chart snapshots · alerts        │                       (scripts/run_dashboard.py)
                 └───────────────────────────────────────────────────────────────────┘
Deriv / MetaApi deal history ──> scripts/run_ingest.py ──> trades, rule checks, analytics, journal
```

### Real data — free

Kora works with two free data sources and picks one automatically
(`KORA_DATA_SOURCE=auto`):

1. **Your MT5 terminal (recommended).** Install and open the Deriv MT5 app
   on this PC and log in. Kora reads live prices, candles, your positions,
   balance and trade history from it through MetaQuotes' official
   `MetaTrader5` Python package. It's local, free, needs no API key, and is
   read-only, so Kora never sends orders. Windows only. Make sure every
   instrument in `config/instruments.yaml` shows in MT5's Market Watch (right
   click → Show All).
2. **Deriv's public feed (fallback).** Deriv's free WebSocket API gives live
   prices and candles for all 11 instruments with no account needed. Kora
   uses it automatically when MT5 isn't open. It can't see positions or
   balances.

MetaApi (`METAAPI_TOKEN`) is still supported but optional. The only paid
piece is the optional Claude chat/narratives (`ANTHROPIC_API_KEY`).
Telegram alerts are free.

```bash
python scripts/train_kora.py           # calibrate on real history from MT5 or Deriv
python scripts/run_kora.py             # live analysis
python scripts/run_dashboard.py        # http://127.0.0.1:5000
python scripts/run_ingest.py --source mt5 --days-back 90   # your closed trades for Journal/Analytics
```

### Quick start

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # nothing required: open MT5, or Kora falls back to Deriv's free feed
python scripts/train_kora.py    # calibrate setups on 180 days of history (a few minutes)
python scripts/run_kora.py      # Kora: live feed + analysis every 15 min + alerts
python scripts/run_dashboard.py # hub at http://127.0.0.1:5000
```

No broker credentials yet? Preview everything on simulated data (it's
clearly labelled SIMULATED on every screen):

```bash
KORA_DATA_SOURCE=sim python scripts/run_kora.py
KORA_DATA_SOURCE=sim python scripts/run_dashboard.py
```

Single breakdown in the terminal: `python scripts/analyse.py XAUUSD`.

## Kora — how the analysis works

Kora runs this every cycle for every instrument in `config/instruments.yaml`:

| Layer | What Kora does | Code |
|---|---|---|
| Technicals | Per timeframe: EMA 20/50/200 stack and slope, RSI, MACD momentum, ADX/DI, ATR and its percentile (volatility regime), Bollinger squeeze, session VWAP, swing structure (HH/HL vs LH/LL), clustered support/resistance with touch counts. Weighted multi-timeframe bias plus a regime classification (Trending / Range / Volatile / Bullish / Bearish). | `src/analyst/ta.py`, `technical.py` |
| Setups | Five detectors on the execution timeframe (H1) with the H4 trend as a filter: **trend pullback**, **breakout & retest**, **range reversion**, **liquidity sweep**, **Asia-range (London) breakout** (gold/FX only). Each produces a complete plan: entry (market/limit), structure-based stop, TP1 at the next opposing level (≥1.2R) and TP2, plus confluence, objections, trigger and invalidation. Boom/Crash plans against the spike direction are penalised. | `src/analyst/setups.py` |
| Calibration ("training") | `train_kora.py` replays every detector bar by bar over history with no lookahead (swings count only once confirmed; limit orders must actually fill; a bar that hits both stop and target counts as a loss). That gives a hit rate and expectancy per instrument, setup and direction. Live confidence = rule score × track record, with small samples shrunk toward break-even. | `src/analyst/calibration.py` |
| Fundamentals | ForexFactory calendar: high-impact releases within 2h for the instrument's currencies flag **event risk** and cut confidence. Consensus vs prior is read per indicator type (inflation, jobs, growth, PMIs, rates…) into a currency tilt, then translated to the instrument (USD strength is a gold/silver headwind). Cross-asset context: EURUSD as an inverse-USD proxy for metals, plus whether the peer instrument confirms the move (gold↔silver, EURUSD↔GBPUSD). Synthetics are explicitly marked "no fundamentals" because Deriv's RNG drives them. | `src/analyst/fundamentals.py` |
| Output | A report per instrument (bias, conviction, regime, per-TF table, key levels, fundamentals, ranked plans graded A ≥70% / B 55–69% / watch-only <55%, reasoning chain, verdict), an explainable **decision-log** entry, alerts, and optionally a written "Kora's take" from Claude. | `src/analyst/engine.py`, `service.py`, `llm.py`, `notify.py` |

**Honest limits.** The five setups are well-known discretionary patterns
encoded as rules, not a proven edge. Calibration tells you how each one
*has* behaved on your instruments; it can't promise how it will behave.
The calendar feed has no "actual" figures, so Kora reads expectations and
event timing, not post-release surprises. Treat A-grades as "worth your
attention", never as instructions.

### Controlling Kora

The **Kora Agent** page has Run / Pause / Stop (Run = analyse + alert,
Pause = analyse with alerts muted, Stop = live feed only) plus **Analyse
now**. **Settings** controls the interval, alert threshold, which
instruments are analysed, which alerts fire, and whether Claude writes
narratives. The service reads all of these live, with no restart needed.

### Alerts

Set `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` and/or `KORA_WEBHOOK_URL`
(Discord/Slack-style) in `.env`. Kora sends: setups at or above your
threshold (with entry, SL, TPs, reasons, track record and invalidation),
optional watch-list setups, high-impact news 15 min out, and positions with
no stop-loss. Alerts are de-duplicated. Use **Settings → Send test alert**
to check the wiring.

### Ask Kora (AI Insights)

The chat sends Claude a context snapshot: your account, open positions and
risk model, performance stats, rule violations, recent trades, the latest
analysis for every instrument, and recent decisions. You can ask "what's the
best setup on the board?", "full gold breakdown", "what was my biggest
mistake this week?". Needs `ANTHROPIC_API_KEY`.

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

## Legacy: trading proposals (propose -> approve -> execute)

> Superseded by Kora's analysis-only entry plans. The dashboard no longer has
> approve/execute buttons; the CLI below remains for anyone who still wants
> it, and it is still demo-gated.


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

### Scanning multiple instruments

`config/strategy.yaml`'s `instruments` list can hold more than one symbol —
`run_scan.py` checks all of them every run, independently (one instrument
erroring out, e.g. a mistyped symbol name, doesn't stop the rest from being
scanned). Each instrument inherits the shared `trend`/`entry`/`exit`/
`timeframe` defaults but can override any of them, since a strategy tuned for
one instrument's behavior isn't automatically valid for another — a
synthetic index (Volatility 10/75, Step Index) trades 24/7 on a constant
statistical volatility model with no macro trend/news structure the way
XAUUSD does, so treat the shared defaults as **unvalidated** for those until
you've backtested them specifically (`scripts/run_backtest.py`) and added an
override block. See the comments in `config/strategy.yaml` for the override
syntax.

Every scan writes a per-instrument result (a setup, "no setup", or an error)
to `data/trades.db`, which the dashboard's **Scanner** page reads — that page
never calls MetaApi directly itself, since a live network round-trip per
watched instrument on every page load doesn't scale (MetaApi's default
per-request timeout is 60 seconds). Run `scripts/run_scan_loop.py`
continuously to keep the Scanner page current.

**What's not done yet, on purpose:**
- The strategy is a conservative starting point, not a validated edge. Expect
  to tune `config/strategy.yaml` after watching it run on demo for a while —
  especially for any instrument beyond XAUUSD, which hasn't been backtested.
- There's no correlation check across instruments yet — if XAUUSD and a
  synthetic index both fire at once, each is sized independently against
  your risk %, without accounting for combined account risk.

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

### Backtesting straight from MetaApi (no CSV export needed)

If you have a live MetaApi connection, `scripts/backtest_live.py` pulls
historical candles directly instead of needing a manual History Center
export - useful for an instrument you haven't exported before, like the
synthetic indices:

```bash
python scripts/backtest_live.py --symbol "Volatility 10 Index" --days 180
python scripts/backtest_live.py --symbol "Volatility 75 Index" --days 180
python scripts/backtest_live.py --symbol "Step Index" --days 180
python scripts/backtest_live.py --symbol XAUUSD --days 180
```

It looks the symbol up in `config/strategy.yaml` (so it uses that
instrument's own overrides, if you've added any), paginates
`get_historical_candles` to cover `--days` of history (MetaApi caps a single
call at 1000 candles), and fetches `tick_size`/`tick_value`/`volume_step`
from MetaApi's symbol spec automatically instead of you having to supply
them by hand. Actual history coverage depends on your broker's retention -
check the printed candle range against what you asked for. Writes the same
`backtests_out/<symbol>_<timestamp>.json` report as `run_backtest.py`, so
results show up in the dashboard's Backtests page either way.

### Tuning per-instrument settings

`config/strategy.yaml`'s shared EMA/RSI/ATR settings were arrived at for
XAUUSD - there's no reason to assume they suit a different instrument's
price behavior. `scripts/tune_strategy.py` backtests a grid of
trend/entry/exit combinations for one instrument (candles pulled fresh from
MetaApi, same as `backtest_live.py`) and ranks them by profit factor,
instead of guessing at "reasonable-sounding" numbers:

```bash
python scripts/tune_strategy.py --symbol "Volatility 10 Index" --days 180
```

Prints a baseline (today's shared settings) alongside the top candidates,
plus a ready-to-review override block for `config/strategy.yaml`. **This
does not edit any config file for you and the ranking can overfit** - a
few hundred backtests against a few months of data is enough to find
*something* that looks good by chance, especially for candidates near
`--min-trades` (default 15). Treat the top result as "worth watching on
demo," not "proven" - and re-run periodically as more real history
accumulates rather than tuning once and trusting it forever. Runs ~400+
backtests per invocation, so expect it to take a few minutes; `--top` and
`--min-trades` control how much is printed, not how long it takes.

## Kora Terminal (the hub)

```bash
python scripts/run_dashboard.py            # http://127.0.0.1:5000
```

| Screen | What's on it |
|---|---|
| Dashboard | Equity, daily P/L (vs start-of-day equity), open P/L, free margin, max drawdown, win rate; equity curve; Kora status and top opportunity; live sessions; open positions; limits & quality |
| Charts | Canvas candlesticks for every instrument and timeframe with VWAP, EMA 20, Kora's S/R levels, your positions' entry/SL/TP and **Kora's current plan** (entry/SL/TP1/TP2) overlaid; crosshair; live last candle; watchlist |
| Kora Agent | Run/Pause/Stop, analyse-now, confidence gauge, regime, live signals, reasoning chain, instrument brief cards, expandable decision log |
| Kora breakdown (`/kora/<CODE>`) | The full report for one instrument: verdict, Kora's take, every entry plan with levels / why / against / trigger / invalidation / track record, technical table, key levels, fundamentals and upcoming events, setup track record |
| Positions | Live MT5 positions (read-only) with risk %, $ to stop/target, duration and how each sits against Kora's analysis; recently closed |
| Risk | Composite risk gauge and account health; exposure by asset class; risk per position; historical VaR; bootstrapped expected drawdown; stress tests; correlation-adjusted heat; daily-loss and trailing-drawdown buffers; live correlation heatmap |
| Analytics | Sharpe, Sortino, Calmar, PF, expectancy, risk of ruin (Monte Carlo), etc.; monthly returns; win/loss; P/L distribution (in R once trades carry a risk amount); strategy comparison by setup tag; hour/weekday breakdowns. Links to the P&L calendar, rule violations and backtests |
| Journal | Card per closed trade with an auto-captured candle snapshot, tags, review status, **Kora's note** (was the entry aligned with Kora's analysis at the time?) and your lesson; click to tag and review |
| Scanner | Every instrument ranked by Kora's opportunity score, with live price, change, ATR, volatility percentile, trend strength and Kora's read |
| Economic Calendar | This week's (and next week's, once published) releases with impact filter and live countdowns |
| AI Insights | Ask Kora (chat grounded in your live context) |
| Settings | Data connections, masked API keys, Kora settings, notifications |

Topbar search (press `/`) jumps to any instrument breakdown or page. The
hub reads only from SQLite, so pages load instantly. Live values refresh
every 2s from what the Kora service writes. If the service isn't running,
every screen says so instead of showing stale or made-up numbers.
**There are no order buttons anywhere.** The old proposal approve/execute
flow has been removed from the web UI. (`decide_proposal.py` still exists
as a deliberate, demo-gated CLI tool, but Kora itself never uses it.)

### Legacy: automated proposal scanning

Superseded by Kora (`scripts/run_kora.py`), which analyses without filing
executable proposals. Kept for reference:


```bash
python scripts/run_scan_loop.py                       # scans every 20 minutes
python scripts/run_scan_loop.py --interval-minutes 15
```

Runs `run_scan.py`'s logic on a repeating interval so proposals show up in
the dashboard on their own. It only ever *proposes* - execution still
requires an explicit approval, either here or via `decide_proposal.py`. A
failed scan (a network blip, etc.) logs the error and retries next
interval rather than killing the loop; meant to run continuously in its
own terminal (or as a background process) alongside the dashboard.

### Running everything in the background (no terminal needed)

Both the dashboard and the scan loop are meant to run continuously, but
neither needs a terminal window open once it's started - and you shouldn't
need to stop either one just to "check progress": the dashboard itself
*is* the progress view. The **Scanner** page shows the last scan per
instrument, **Proposals** shows anything new waiting for a decision, and
both update from the same `data/trades.db` the background scan loop
writes to - refreshing the page is enough, no restart required (see
"Dashboard" above).

To actually stop needing a terminal at all, run both as silent background
processes launched at Windows logon, via Task Scheduler:

```bash
cd ~/Trade-Analysis-Agent
git pull origin main
WINPATH=$(pwd -W)
MSYS_NO_PATHCONV=1 schtasks /create /tn "TradeAgentDashboard" /tr "\"$WINPATH/run_dashboard.bat\"" /sc onlogon /rl highest /f
MSYS_NO_PATHCONV=1 schtasks /create /tn "KoraAgent" /tr "\"$WINPATH/run_kora.bat\"" /sc onlogon /rl highest /f
```

`run_kora.bat` replaces the old scan loop: Kora does the scanning now, and
it only analyses. If you registered `TradeAgentScanLoop` before, remove it
so the old proposal scanner isn't running alongside Kora:
`MSYS_NO_PATHCONV=1 schtasks /delete /tn "TradeAgentScanLoop" /f`.
Kora logs to `kora.log`.

`MSYS_NO_PATHCONV=1` is required here specifically because you're running
`schtasks` (a native Windows tool) from Git Bash - its MSYS layer otherwise
rewrites `/create`, `/tn`, etc. into bogus Windows paths since they look
like Unix-style absolute paths. Any other `schtasks` command below needs
the same prefix.

This registers two scheduled tasks that start automatically the moment you
log into Windows and keep running in the background (via `pythonw`, so no
console window pops up) - `run_dashboard.bat` and `run_kora.bat` (both
at the project root) each `cd` to the project folder first, so they work
regardless of what directory Task Scheduler launches them from.

To check on them without ever opening a terminal: visit
`http://127.0.0.1:5000` anytime, or check Task Manager for `pythonw.exe`
processes. If you do want to look under the hood, output is redirected to
`dashboard.log` and `kora.log` in the project root (both gitignored)
rather than a console, since `pythonw` has none.

A few things worth knowing:
- **Run the two `schtasks /create` commands only once** - re-running them
  (with `/f`) just re-registers the same task, harmless but unnecessary.
- **To trigger a task immediately** instead of waiting for your next
  logon: `MSYS_NO_PATHCONV=1 schtasks /run /tn "TradeAgentDashboard"`.
- **To stop everything**: `MSYS_NO_PATHCONV=1 schtasks /end /tn "TradeAgentDashboard"`
  and the same for `KoraAgent` (stops the running process; they'll
  start again at your next logon unless you also disable them).
- **To remove the scheduled tasks entirely**:
  `MSYS_NO_PATHCONV=1 schtasks /delete /tn "TradeAgentDashboard" /f` and the
  same for `KoraAgent`.
- Since this is a Task Scheduler entry rather than a shell you launched
  yourself, it won't have your interactive shell's exported environment
  variables - `.env` is loaded explicitly by both entry-point scripts now,
  specifically so this works without that.

### Remote access

**Set a password before exposing this beyond your own machine.** There's
no authentication by default - fine for pure `127.0.0.1` use, not once
something else can reach the page, since whoever has the link could see
your trade history and approve/reject real orders. Add to `.env`:

```
DASHBOARD_PASSWORD=choose-something-here
```

The dashboard will then require it (HTTP Basic Auth - any username, that
password) on every page.

Simplest way to actually reach it from another device (phone, another
computer) without deploying anything or opening router ports: a tunnel
like [ngrok](https://ngrok.com/). It runs alongside the dashboard on your
machine - your credentials and data never leave it - and gives you a
public HTTPS URL that forwards to your local port:

```bash
ngrok http 5000
```

Ngrok prints a URL like `https://random-string.ngrok-free.app` - open that
from any device, log in with the password above, and it's the same
dashboard. On the free tier that URL changes each time you restart ngrok;
a paid plan gets you a stable one if that matters. Keep both the
dashboard and the ngrok process running in their own terminals for as
long as you want it reachable.

## Scheduling

Once ingestion is verified working end-to-end, the daily/weekly/monthly
cadence can run as a recurring Routine in Claude Code that: runs
`run_ingest.py` + `run_report.py`, then generates and delivers the report
(chat dashboard + docx + email, per your preference). That step comes after
credentials are wired up and a first manual run looks correct.
