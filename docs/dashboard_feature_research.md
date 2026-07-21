# Dashboard Feature Research

Research date: 2026-07-21. Looked at the categories of platform most
relevant to what this dashboard already does: dedicated trade journals
(closest comparison - same job as our Overview/Trades/Mistakes pages),
prop-firm account dashboards (closest comparison to risk/scaling
tracking), and general charting platforms (TradingView, for UI/workflow
philosophy). Sources linked inline.

## 1. Trade journals: TradeZella, Edgewonk, Tradervue

These three are the direct competitors to what our dashboard already
does - trade history, stats, calendar - so they're the most relevant
comparison, feature-for-feature ([TradeZella](https://www.tradezella.com/blog/best-trading-journal-software), [Tradervue](https://www.tradervue.com/blog/best-trading-journal)).

**Composite performance score.** TradeZella's "Zella Score" is a 0-100
metric combining profitability, risk management, consistency, and
discipline into one number, rather than just showing raw P&L ([TradeZella](https://www.tradezella.com/zella-ai)).
We have all four of those inputs already (`compute_period_stats`,
`config/rules.yaml` violations) but never combine them into a single
"how am I actually doing" number.

**Per-trade notes and screenshots.** Every trade gets a free-text note
and an attached screenshot/chart image ([TradeZella](https://www.tradezella.com/zella-ai)). **This is a real gap** -
our dashboard currently has zero notetaking capability anywhere. Trades
are just rows in a table with no way to record *why* you took one or
what you thought at the time.

**Tagging beyond automated rule checks.** Setup, emotion, and execution
quality are tagged per trade ([TradeZella](https://www.tradezella.com/zella-ai)). Our `trade_flags` table only
captures *mechanical* rule violations (overtrading, revenge trading,
etc.) computed automatically from timing/price data - there's no field
for *your own* read on a trade (which setup you thought it was, how you
felt executing it).

**Playbooks (strategy-level tracking).** You name a strategy/setup as a
"playbook" and the journal shows win rate, expectancy, and profit factor
*per playbook*, plus tracks trades that matched a playbook's criteria
but weren't taken ("missed") ([TradeZella](https://www.tradezella.com/zella-ai)). This maps almost exactly onto
our multi-strategy setup (the XAUUSD research's three strategies) - we
have no way today to see "how is Strategy 2 doing specifically" versus
Strategy 1, and our `rejected` proposals are already the "missed trades"
data, just not surfaced that way.

**Psychology/emotion tracking.** Edgewonk's "Tiltmeter" assigns a
numerical emotional-state rating (confidence, stress, patience,
impulsivity) to each trade and correlates it with performance over time,
operating on the thesis that most traders lose to psychology, not
strategy ([Edgewonk](https://edgewonk.com/trading-psychology)). This is the single biggest capability gap
relative to the project's original goal of "learn from mistakes" - right
now "mistakes" only means mechanical rule breaks, never how you actually
felt or what you were thinking.

**Structured session review.** Daily/weekly/monthly reflection prompts,
report cards, and explicit "what did I learn / what needs attention next"
tracking, not just raw stats ([Edgewonk](https://edgewonk.com/trading-psychology)). Our Calendar page shows *what*
happened per day; nothing captures *why* or *what to do differently*.

**Not pursuing (low fit):** trade replay (visual candle-by-candle
playback), broker/liquidity/commission reports - more relevant to
discretionary/manual multi-broker traders than a single-account,
semi-automated setup like this one.

## 2. Prop-firm dashboards: FTMO, MyFundedFX

Closest comparison to the risk/scaling side of this project (the
XAUUSD research doc's Phase 1/2/3 scaling framework is explicitly modeled
on FTMO's).

**Real-time daily-loss-limit progress bar, color-coded.** A visual bar
showing today's loss against the daily limit, green under ~70%, orange
70-90%, red past 90% - critically, warning *before* a breach, not
after ([search synthesis](https://ftmo.com/en/how-to-pass-ftmo-challenge/), [MQL5 community tools](https://www.mql5.com/en/code/70268)).
Common complaint about the *official* FTMO/MyFundedFX dashboards: they
update with delay and don't show how close you are in percentage terms -
third-party tools exist specifically to fix that gap.

This maps directly onto a real, currently-incomplete piece of our own
system: `config/rules.yaml`'s `max_daily_loss_pct` and
`daily_loss_limit_breached` rule were flagged from the start as
needing balance-before-trade tracking that wasn't wired up yet. Now that
real MetaApi balance data flows through ingestion, this is buildable
properly, not just a stub.

**Profit-target / scaling progress bar.** Visual progress toward the
next account-size milestone, plus a running count of qualifying trading
days ([search synthesis](https://academy.ftmo.com/lesson/maximum-daily-loss/)). Direct fit with the Phase 1/2/3 scaling plan
already written up in `docs/xauusd_strategy_research.md` - right now
that plan exists only as a document, with nothing tracking progress
through it.

## 3. TradingView: charting/workflow UI

Less directly transferable, since TradingView solves a different problem
(discovering and charting *any* symbol across markets) than this
dashboard (monitoring *one* account's activity). The useful takeaway is
philosophical rather than a feature to copy outright: a clean workspace
built around your single most common task, not a dashboard crammed with
every possible widget - one chart, one watchlist, alerts and layout
working together as a single workflow rather than isolated screens ([ChartWiseHub](https://chartwisehub.com/tradingview-layout-tutorial/), [FinancialTechWiz](https://www.financialtechwiz.com/post/tradingview-screener/)).
Worth keeping in mind as we add features below - resist turning this
into a cluttered multi-widget dashboard when the job is still "how is my
trading actually going."

## 4. General best practice: R-multiples and breakdown analysis

R-multiples (expressing every trade's result as a multiple of what was
risked, not a raw dollar figure) are described as the standard for
professional trade journaling, popularized by Van Tharp - expectancy is
computed as `(win rate x avg win in R) - (loss rate x avg loss in R)` ([CrossTrade](https://crosstrade.io/learn/performance-metrics/r-multiple), [P&L Ledger](https://www.pnlledger.com/expectancy-r-multiples-the-plain-english-guide/)).
The point of R-normalization: a $500 win means something different at
$1,000 risk-per-trade than at $10,000 risk-per-trade, and comparing raw
dollars across periods where position sizing changed (exactly what
happens as the Phase 1/2/3 scaling plan progresses) hides the real
picture. Our stats are currently 100% dollar-based - `pnl` only, no R
anywhere - which will only get more misleading as position sizing scales
up per the growth plan.

Standard breakdown cuts: performance by setup/strategy, by time-of-day,
by day-of-week, and by holding-time ([search synthesis](https://journalplus.co/learn/guides/weekly-monthly-review-guide/)) - the pattern named
specifically is that overall numbers can hide where the real edge is
(e.g. one setup profitable only during one session, losing at another) and
breaking results apart by these dimensions is how that gets found. We
have every input needed for this already (`open_time` has both time-of-day
and day-of-week; nothing currently slices by them).

## Prioritized recommendation

**Tier 1 - directly extends what's already built, no new fundamental
capability:**
1. Daily-loss-limit progress bar (color-coded, warns before breach) -
   completes an already-half-built rule, real data now exists to do it
   right.
2. R-multiple / expectancy stats alongside the existing dollar figures.
3. Time-of-day and day-of-week performance breakdown tables.
4. Per-strategy ("playbook") breakdown, using rejected proposals as the
   "missed trades" data we already have.

**Tier 2 - new capability, moderate build:**
5. Free-text notes per trade.
6. Custom tags (setup/emotion/mistake) on top of the automated rule
   flags, editable from the dashboard.
7. Scaling-plan progress bar (Phase 1/2/3 from the XAUUSD research doc).

**Tier 3 - bigger, more open-ended:**
8. Emotion/psychology rating per trade, correlated with performance
   over time (the "Tiltmeter" concept) - the single highest-value
   addition relative to the project's original "learn from mistakes"
   goal, but the most new design work (what scale, what to correlate
   against).
9. Structured session-review workflow (prompts, report cards, lesson
   tracking) - meaningfully changes the dashboard from a read-only report
   into something you write into regularly.
10. Screenshot attachments per trade - lowest priority; needs file
    storage/upload handling this project doesn't have any of yet, for a
    feature the psychology/notes fields (5, 6, 8) mostly substitute for.

Not pursuing: trade replay, commission/liquidity reports, a
TradingView-style multi-symbol screener - all solve problems this
single-account dashboard doesn't have.
