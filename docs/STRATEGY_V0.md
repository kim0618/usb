# USB Strategy V0

## Purpose and version

`strategy_v0` is the first mechanical V1 Catalyst Momentum lifecycle. Its fixed
values are research starting points, not optimized parameters. A meaningful
formula or threshold change requires a new strategy version. Risk remains
`risk_v0`, execution remains `execution_v0`, and variants remain
`shadow_variants_v0` until 300 eligible trades and 60 trading days.

## Common engine and lifecycle

Actual/Paper requires an imported GPT analysis and Human APPROVE. APPROVE only
opens the strategy path; it is not BUY. Shadow processes every Top-8 candidate,
ignores the Human gate, and can use deterministic defaults (NORMAL trailing and
UNKNOWN overnight suitability) without GPT. After that single gate both paths
call the same Strategy engine, Risk engine, and SimBroker path.

The validated lifecycle is RESEARCH_READY → HUMAN_APPROVED (actual only) →
PREMARKET_PASSED → OPENING_RANGE_BUILDING → WAITING_ENTRY → ENTRY_SIGNALLED →
POSITION_OPEN → optional PYRAMID_ADDED → OVERNIGHT_REVIEW → optional
OVERNIGHT_HELD → DAY2_ACTIVE → EXIT_SIGNALLED → EXITED. `EXIT_SIGNALLED` means
the SELL was requested; only a confirmed full Broker fill produces `EXITED`.
Rejected, unfilled, and no-next-bar orders remain signalled while a Broker
position exists. Human rejection, premarket rejection,
and deadline NO_TRADE are terminal. Invalid transitions raise an error.

Strategy state stores lifecycle facts (phase, entry/stop/high, add signal/count,
holding day, profiles and cutoff), not quantity, cash, cost basis, or account
value. Those remain Broker/Risk truth. `strategy_states` is persisted through a
repository/service transaction so stop/high/overnight state survives restart;
the pure engine imports neither SQLAlchemy nor broker code.

State identity is `(symbol, trading_date, book, variant)`. Actual uses the
canonical `(ACTUAL, ACTUAL)` discriminator; Shadow uses `(SHADOW, A..E)`, so
parallel variants cannot overwrite one another. Actual Risk reservations remain
in Stage 5 persistence. Shadow uses a variant-local simulation ledger with the
same pure RiskEngine formulas and never consumes Actual daily symbol/risk limits.

## Fixed StrategyConfig

- Premarket gap: +2% through +15%, inclusive.
- Premarket volume: premarket volume / historical average regular daily volume
  must be at least 5%. This placeholder depends on future provider coverage.
- Opening Range: 15 minutes; 09:30 through 09:44 bar-start timestamps.
- Entry evaluation starts at exchange market open plus `opening_range_minutes`
  (09:45 for the 15-minute default) through 10:30 ET, inclusive. Missing OR bars
  after the deadline produce `INSUFFICIENT_OPENING_RANGE`, not an infinite HOLD.
- One entry attempt per symbol/day; no re-entry.
- ATR: 14-period SMA of one-minute regular-session True Range.
- Trailing profiles: TIGHT 1.0, NORMAL/UNKNOWN 1.5, WIDE 2.0 ATR.
- Trailing activation: highest price reaches +1 initial R.
- One winner-only pyramid add.
- Close review: exchange close minus 10 minutes.
- Closing strength: current price in the top 30% of the session range.
- Maximum one overnight position and two XNYS trading days.

## Premarket and entry

Gap is `reference / previous_regular_close - 1`. Invalid/non-positive inputs,
low/high gap, volume below 5%, a new negative catalyst, or explicit research
block produce typed reasons. Strategy performs no web search.

Session VWAP begins at 09:30 and uses one-minute typical price
`(high + low + close) / 3`, weighted by volume. Zero total volume is invalid.
This bar VWAP deliberately differs from tick VWAP. Entry requires a regular
session reference close strictly above VWAP and strictly above OR High. The
09:45 evaluation bar is never part of the Opening Range. Premarket/postmarket
new entries are not allowed.

The initial long stop is OR Low. This simple structure-first rule avoids making
one-minute ATR the initial risk anchor. It must be strictly below entry and Risk
validates it again. There is no fixed take-profit.

## Position management

After +1R, ATR variants set `highest_since_entry - multiplier × ATR` and active
stop is `max(previous active stop, candidate stop)`, so it never loosens or falls
below initial stop. +1R is calculated from the actual SimBroker average entry
fill and the initial stop, never from the signal reference price. Actual uses the
GPT TIGHT/NORMAL/WIDE profile (UNKNOWN maps to NORMAL); Shadow A-D uses its frozen
variant multiplier and E remains structure-only.

For each regular-session OHLC bar, the stop that existed before the bar is tested
against the low first. Only if it survives does the completed bar high update the
high-water mark and next-bar trailing stop. A newly raised stop is never applied
retroactively to the same bar. Premarket and postmarket bars do not alter the
normal high-water mark, ATR, stop, or stop-hit path. A market bar whose low
crosses the pre-existing active stop emits EXIT_SIGNALLED;
SimBroker, not Strategy, supplies the conservative next executable fill. If the
OHLC ordering can genuinely change an outcome, WORST_CASE selects the adverse
path and the lifecycle forwards that count to the TradeResult/Shadow record; a
pre-existing stop hit is deterministic, not automatically ambiguous. A
gap-through stop is therefore filled from the next bar
open with execution costs, never optimistically at the stop price.

ADD requires price above average price and VWAP, at least +1R, and a new high.
The engine issues it once; Risk controls its quantity and the existing 20%
pyramid reserve. Averaging down and multiple adds remain forbidden.
`add_count` increases only after a SimBroker BUY fill; rejection/unfilled does
not masquerade as a filled add.

## Closing, overnight, and Day 2

The calendar supplies actual close, including early closes. MEDIUM/HIGH GPT
overnight suitability is necessary but not sufficient: price must exceed VWAP,
closing strength must be at least 0.70, catalyst must remain valid, active stop
must be valid, the one-position limit must pass, and Risk gap stress must pass.
UNKNOWN/LOW exits on Day 1. Risk V0's -20%/5% rule yields HOLD_FULL,
REDUCE_AND_HOLD, or EXIT_ALL; for example 40% notional is reduced to 25%.
REDUCE_AND_HOLD is a partial SELL OrderIntent through SimBroker and transitions
to OVERNIGHT_HELD only after its fill.

Day 2 is the next XNYS trading session, not the next calendar date. Weekend and
holiday gaps are skipped. A new negative catalyst can create an emergency exit
candidate; no new GPT call or second breakout is required. The active trailing
stop never loosens. Every remaining position exits by Day 2 close; Day 3 is
impossible.

## Shadow variants

- A: intraday only, ATR 1.5.
- B: Day 2 allowed, ATR 1.0.
- C: Day 2 allowed, ATR 1.5; Paper/Live control.
- D: Day 2 allowed, ATR 2.0.
- E: intraday only; retains the non-decreasing OR structure stop and disables
  ATR trailing.

All behavior is data in `VariantConfig`; there are no separate strategy files.

## Point-in-time and limitations

Every indicator filters `timestamp <= market_as_of` and
`available_at <= market_as_of`. Replay additionally passes only the current bar
prefix and evaluates at `available_at` (minute timestamps are bar starts).
Intraday ATR resets at each regular trading session, excluding overnight gaps.
Closing strength is computed from regular bars visible at the review cutoff;
callers need not inject a future full-day high/low. Opening Range, VWAP, ATR,
high-water mark, and decisions cannot see a future bar. Market lifecycle time is
America/New_York and accepts aware UTC inputs by conversion. Identical inputs
produce identical traces except database audit IDs.

`StrategyLifecycleRunner` is the minimal one-candidate common orchestrator. It
connects Strategy decisions → Risk evaluation/reservation → OrderIntent →
SimBroker next-bar execution → fill acknowledgement for base entry, one ADD,
overnight full/partial exit, XNYS Day 2 activation, and mandatory final exit.
Quantity, cash, cost basis, and realized PnL remain Broker truth rather than
being copied into StrategyState.

V0 parameters have not been verified against Kiwoom coverage or real fills.
There is no external market API, broker, GPT API, optimizer, ML, UI, re-entry,
multi-step pyramiding, Day 3, MOC type, tick VWAP, or large backtest framework.
