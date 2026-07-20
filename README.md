# Trade Analysis Agent

Pulls your trade history from Deriv and MetaTrader (via MetaApi.cloud), checks
every trade against your own trading-plan rules, and builds the data behind
daily/weekly/monthly reports (mistakes made, progress trends, stats).

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

1. Log in at https://app.deriv.com
2. Go to Settings -> API token
3. Create a token with **Read** scope (Trading history doesn't need Trade/Payments scopes)
4. Also grab your app_id from https://developers.deriv.com/docs (or use the shared demo app_id `1089` to start)

### 3. Set up MetaApi.cloud (for MT4/MT5)

MetaTrader has no official API, so MT4/5 access goes through MetaApi.cloud,
a broker-agnostic bridge:

1. Sign up at https://metaapi.cloud
2. Add your MT4/5 account (they ask for your broker server + login, or an
   investor/read-only password — never your live trading password if you
   can avoid it)
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

## Scheduling

Once ingestion is verified working end-to-end, the daily/weekly/monthly
cadence can run as a recurring Routine in Claude Code that: runs
`run_ingest.py` + `run_report.py`, then generates and delivers the report
(chat dashboard + docx + email, per your preference). That step comes after
credentials are wired up and a first manual run looks correct.
