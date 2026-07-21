# XAUUSD Strategy Research

Research date: 2026-07-21. Sources are linked inline; claims without a link
are arithmetic derived from sourced facts (lot/pip math, risk-of-ruin
formulas), not independent claims.

## 0. Read this before the strategies below

A large fraction of "gold strategy" content online is unverifiable
marketing copy - fabricated win rates, cherry-picked screenshots, no
disclosed methodology. Two data points from this research make that
concrete rather than just asserted:

- [QuantifiedStrategies](https://www.quantifiedstrategies.com/gold-trading-strategies/)
  backtested opening-range-breakout on gold futures and found it does not
  produce tradeable results in its basic form - the same negative result
  they got on Nasdaq, silver, and oil. Their conclusion: ORB alone is not
  an edge; it needs "smart, logical filters" to have a chance.
- A backtest of the popular EMA 9/21 crossover on XAUUSD daily bars
  produced a 32.4% win rate and 0.96 profit factor - negative expectancy,
  meaning it loses money net of costs ([Quant Signals](https://quant-signals.com/xauusd-trading-strategies/)).

Both of those are the two most commonly recommended "gold strategies" on
the internet. Neither holds up. That's the bar this document is trying to
clear: every strategy below is built to survive that kind of scrutiny, and
none of them are presented as proven - "proof" only happens once we
backtest each against your actual data with the engine already built in
this repo. This document produces hypotheses with sourced rationale, not
verified edges.

## 1. Why gold doesn't trade like a currency pair

- **Volatility is structurally larger.** Gold routinely moves 1-2% in a
  single session, several times a typical major FX pair's daily range, and
  on news days (NFP/CPI/FOMC) the range can exceed 1,000 pips vs. a normal
  200-500 pip day ([NordFX](https://nordfx.com/en/traders-guide/best-time-to-trade-gold-xauusd-sessions-volatility-news), [Vantage](https://www.vantagemarkets.com/en/academy/news-trading-gold/)).
- **Peak liquidity/volatility window: 13:00-17:00 UTC** (London/New York
  overlap) - both major hubs active simultaneously, concentrating the
  day's scheduled data releases and institutional flow into a four-hour
  window. Asian hours are comparatively calm and range-bound ([NordFX](https://nordfx.com/en/traders-guide/best-time-to-trade-gold-xauusd-sessions-volatility-news), [FXNX](https://fxnx.com/en/blog/london-ny-overlap-goldmine-strategy-xau-usd)).
- **Two dominant macro drivers**, both mechanically explainable rather
  than mystical: (1) the US Dollar Index (DXY) - inverse correlation
  typically -0.5 to -0.8, though the relationship is regime-dependent, not
  constant ([LBMA Alchemist](https://www.lbma.org.uk/alchemist/issue-90/an-update-on-gold-real-interest-rates-and-the-dollar), [ResearchGate](https://www.researchgate.net/publication/378173204_A_Review_of_Gold_Pricing_Real_Interest_Rate_and_US_Dollar_Index)); and (2) real (inflation-adjusted)
  yields - gold pays no interest, so rising real yields raise the
  opportunity cost of holding it and pressure price down, independent of
  nominal dollar strength.
- **Scheduled-event behavior has a known shape.** NFP and CPI releases
  frequently produce an initial spike, a sharp reversal (a "stop hunt" /
  liquidity sweep through resting stops), and only then a cleaner
  directional move - the first move after the print is often the least
  reliable one ([FXNX](https://fxnx.com/en/blog/mastering-xauusd-news-15-minute-rule-cpi-nfp), [Vantage](https://www.vantagemarkets.com/en/academy/news-trading-gold/)).

## 2. Three strategies

Each is specified precisely enough to encode mechanically - no
"use your judgment" steps - because vague rules can't be backtested or
falsified.

### Strategy 1 - Filtered session-volatility breakout

**Thesis:** trade the expansion out of the quiet Asian range when London
opens, but only when specific filters are present - because the *naive*
version of this (plain box breakout, no filters) is the one
QuantifiedStrategies already showed doesn't work.

**Rules:**
1. Mark the high/low of the Asian session range, 00:00-07:00 UTC, on the
   15m chart.
2. At 07:00 UTC (London open), only arm the setup if: (a) the Asian range
   is narrower than its 20-day average (a tight range implies more
   potential energy for expansion - trading every range regardless of
   width is one of the things that makes naive ORB fail), and (b) H1 ATR(14)
   is not itself already elevated versus its 20-day average (avoids
   chasing a move that's already extended).
3. Enter on a confirmed close (not just a wick) beyond the range by
   0.25x H1 ATR, in the direction of the break, only if that direction
   agrees with the H4 trend (price vs. H4 EMA(50)).
4. Stop: opposite side of the Asian range, or 1.5x H1 ATR from entry,
   whichever is tighter.
5. Target: 1.5-2x the Asian range width, trailed by a 1x ATR chandelier
   stop once price reaches 1x the initial risk in profit.
6. Skip the setup entirely on days with a scheduled high-impact release
   (NFP/CPI/FOMC) before 12:00 UTC - Strategy 3 owns that behavior instead
   of this one.

**Sourced rationale for the filters:** the range-width and ATR-percentile
filters exist specifically because unfiltered breakout is a documented
failure mode ([QuantifiedStrategies](https://www.quantifiedstrategies.com/opening-range-breakout-strategy/)); the H4 trend
filter and confirmed-close requirement target the two most commonly cited
failure modes for breakout systems generally (counter-trend breaks and
wick-only fakeouts) ([Dominion Markets](https://www.dominionmarkets.com/gold-breakout-strategy-xau-usd/)).

**Honest risk:** this is the least evidence-backed of the three - it's a
reasoned repair of a strategy shown not to work, not a strategy shown to
work. Treat it as the first one to falsify in backtesting.

### Strategy 2 - Multi-EMA confirmation pullback (ATR-scaled asymmetric R:R)

**Thesis:** trend-following with a pullback entry, but survive scrutiny by
using multiple confirming EMAs (not one crossover, which failed at 32.4%
WR/0.96 PF on its own) and an asymmetric reward:risk large enough that a
sub-50% win rate can still be profitable.

This is directly adapted from a public, inspectable implementation
([ilahuerta-IA/backtrader-pullback-window-xauusd](https://github.com/ilahuerta-IA/backtrader-pullback-window-xauusd)) claiming
55.4% win rate, 1.64 profit factor, 5.8% max drawdown, +44.75% over 5
years on 5-minute gold (175 trades, ~$100k notional, 2020-2025). That
repo's costs/slippage assumptions aren't fully disclosed and the sample is
modest (175 trades) - treat the claimed numbers as **unverified until we
run it against your data**, not as proof. What's genuinely useful here is
the structure, which is well-specified enough to reimplement exactly:

**Rules (state machine):**
1. **SCANNING:** watch for the fastest EMA to cross a basket of three
   slower EMAs (e.g. 14/18/24) in the same direction, with a minimum EMA
   "angle" (rate of change) confirming real momentum, not chop.
2. **ARMED:** once crossed, wait for a pullback of 1-3 counter-trend
   candles - price pulling back toward the EMA basket without breaking the
   larger trend structure.
3. **WINDOW_OPEN:** on the pullback candle(s), mark a breakout level just
   beyond the pullback's high/low.
4. **ENTRY:** only fires on a confirmed break of that level in the trend
   direction. Any opposing signal during ARMED/WINDOW_OPEN invalidates the
   setup and resets to SCANNING.
5. **Stop:** 2.5x ATR(14) from entry.
6. **Target:** 12x ATR(14) - a ~4.8:1 reward:risk, which is why the system
   can tolerate a sub-60% win rate and still be profitable (55.4% x 4.8 -
   44.6% x 1 = expectancy of roughly +2.2R per trade if the claimed win
   rate holds).
7. Fixed 1% risk per trade.

**Relationship to what's already built:** this is the same family as
`config/strategy.yaml`'s current baseline (EMA trend + RSI pullback), made
stricter (multi-EMA basket + momentum-angle filter instead of a single
fast/slow pair) and re-scaled to a much wider, asymmetric R:R. It's the
most straightforward of the three to extend our existing
`src/strategy/signals.py` for.

### Strategy 3 - News-driven liquidity-sweep reversal

**Thesis:** trade the well-documented NFP/CPI/FOMC pattern directly,
rather than avoiding news (which Strategies 1 and 2 both do) - since the
initial spike-then-reversal shape around high-impact releases is one of
the more consistently observed behaviors in gold specifically, not a
generic pattern borrowed from equities.

**Rules:**
1. Only active in a 30-minute window around scheduled NFP/CPI/FOMC
   releases (requires an economic calendar feed - see the gap noted
   below).
2. Wait out the first 15 minutes post-release entirely - this is the
   "15-minute rule": the first move is disproportionately likely to be the
   liquidity sweep/stop hunt itself, not the real move ([FXNX](https://fxnx.com/en/blog/mastering-xauusd-news-15-minute-rule-cpi-nfp)).
3. Identify the sweep: price spikes through the pre-release 1H swing
   high/low, then closes back on the other side of that level within 2-3
   candles (1-minute or 5-minute chart) - this close-back-through is what
   distinguishes a genuine stop hunt from a real breakout ([Daily Price Action](https://dailypriceaction.com/blog/liquidity-sweep-reversals/), [EBC](https://www.ebc.com/forex/liquidity-sweep-in-forex-how-to-trade-the-trap)).
4. Enter in the reversal direction on that confirming close, or on a
   retest of the swept level showing rejection (wick/engulfing candle).
5. Stop: just beyond the sweep's extreme wick - if price takes that out,
   the read was wrong.
6. Target: the opposing side of the pre-release range, or 2x risk,
   whichever is closer (news volatility means targets are hit fast or not
   at all - this isn't a trade to hold for hours).

**Known gap:** this strategy needs release timestamps (NFP/CPI/FOMC
calendar) as an input the current backtest engine doesn't have wired up -
`src/backtest/engine.py` only knows about price candles. Backtesting this
one specifically requires either a historical economic calendar feed or
manually flagging release timestamps in the test data. Flagging this now
rather than pretending it can be tested with what already exists.

## 3. Position sizing: small equity -> scaling

**The core risk-of-ruin math** (three inputs: win rate, reward:risk, %
risked per trade) is unambiguous on this point: risking 1-2% per trade
keeps ruin probability near zero for any strategy with real positive
expectancy, while a 55%-win-rate strategy risking 10% per trade carries
roughly 15% ruin probability *despite being profitable on paper* - sizing,
not edge, is what typically kills accounts ([tradicted](https://www.tradicted.com/learn/risk-of-ruin-in-trading/), [Medium/Ildi Veliu](https://medium.com/@ildiveliu/risk-before-returns-position-sizing-frameworks-fixed-fractional-atr-based-kelly-lite-4513f770a82a)).
This project's existing `config/rules.yaml` already defaults to 1%,
consistent with that.

**Gold-specific complication for genuinely small accounts:** unlike FX
majors, gold's lot increments are coarse in dollar terms. Standard lot =
100oz = $1.00/pip; mini = 10oz = $0.10/pip; micro = 1oz (0.01 lot) =
$0.01/pip ([Myfxbook](https://www.myfxbook.com/forex-calculators/pip-calculator/XAUUSD), [DefcoFX](https://www.defcofx.com/xauusd-pips-and-lot-size/)). With
a typical H1 ATR-based stop in the hundreds of pips, even the smallest
tradeable size (0.01 lot, 1oz) can risk more than 1-2% of a truly small
account:

| Account size | 1% risk budget | 0.01-lot risk at a 1000-pip stop |
|---|---|---|
| $200 | $2 | $10 (5% of account - can't hit 1% target) |
| $1,000 | $10 | $10 (exactly matches) |
| $5,000 | $50 | $10 (well within budget, size can scale up) |

This means: **below roughly $1,000, gold's minimum lot size itself becomes
the binding constraint**, not your risk appetite - a real, arithmetic
constraint, not a rule of thumb. (Deriv's minimum deposit is $5 and it
does offer gold CFDs/multipliers with fine-grained stake sizing, which
sidesteps this specific problem if trading gold through Deriv directly
rather than an MT4/5 lot-based broker - worth factoring into which
platform gets used for the small-equity phase ([Deriv](https://deriv.com/markets/commodities/precious-metals/gold)).)

**Scaling framework**, modeled on FTMO's published scaling plan structure
(not because FTMO is being used, but because it's a real, checkable
model for "prove it before you size it up"): FTMO scales a funded account
+25% after 10% profit in four consecutive months, capped and re-verified
along the way rather than granted all at once ([PickMyTrade](https://pickmytrade.io/prop-firm-faq/ftmo/), [EdgeFlo](https://www.edgeflo.com/blog/ftmo-rules)).
Adapted for personal capital:

1. **Phase 1 (prove the strategy):** trade minimum size (0.01 lot or
   Deriv's minimum stake) regardless of account size, for a minimum of 20
   completed trades or 2 months, whichever is longer. Judge on the same
   stats this repo's reporting engine already produces - win rate, profit
   factor, max drawdown - not on a handful of lucky trades.
2. **Phase 2 (fixed-fractional scaling):** once Phase 1's numbers hold up,
   move to strict 1% fixed-fractional sizing recomputed every trade off
   current balance (already how `src/strategy/order_spec.py` sizes MT4/5
   trades) - position size grows and shrinks automatically with the
   account.
3. **Phase 3 (deliberate step-ups):** only increase risk-per-trade (e.g.
   1% -> 1.5%) after a full quarter of consistent results, mirroring
   FTMO's "consecutive period" requirement rather than reacting to any
   single good month.

## 4. Validation plan - what "proof" actually means here

None of the above is proof yet. Once your chart data arrives:

1. **Strategy 2** is the most straightforward to validate immediately -
   it extends the existing `src/strategy/signals.py` EMA/RSI framework
   with a multi-EMA basket, an angle/momentum filter, and the wider
   ATR-scaled R:R. Can run through the existing `run_backtest.py` pipeline
   with modest changes.
2. **Strategy 1** needs the backtest engine to track a rolling ATR/range
   percentile (to implement the "narrower than 20-day average" filter) -
   a moderate but contained extension.
3. **Strategy 3** needs release-timestamp data the engine doesn't
   currently ingest - the honest gap noted above. Testing it properly
   means sourcing a historical NFP/CPI/FOMC calendar, which is a separate
   piece of work from what exists today.

Recommended order once data lands: backtest Strategy 2 first (cheapest to
stand up, most directly comparable to a real published implementation),
then Strategy 1, then decide whether Strategy 3 is worth the calendar-data
effort based on how much conviction the first two build.
