# USB SimBroker & Shadow Foundation — Stage 6

## Purpose and boundary

Stage 6 completes the execution path `OrderIntent → SimBroker → SimOrder →
SimFill → SimPosition → TradeResult`. Strategy and Risk remain broker-independent;
SimBroker consumes only an intent, validated market bars, and `ExecutionConfig`.
It imports no scanner, GPT, research ORM, or risk repository. There is no Kiwoom,
external API, real order, PaperBroker, concrete entry/exit strategy, or frontend.

## Broker contract and state

The execution-only contract contains submit/cancel, order/open-order, position,
fill, and reset methods. Authentication, account transport, token, WebSocket, and
market-data capabilities are deliberately separate. `SimBroker` owns in-memory
domain state for deterministic single-process replay. SQLite rows are immutable
research/result copies; `ExecutionRepository` commits order, fills, and optional
shadow result in one transaction. The broker never changes Stage 5 risk
reservations; a later orchestration/reconciliation service consumes its outcome.

The account is long-only, same-currency, cash-only, and has no leverage. BUY
debits execution notional plus commission/FX charge; SELL credits execution
notional less those charges. Mark-to-market equity is cash plus position quantity
times an explicitly supplied latest price. Closed positions are removed while
their `TradeResult` remains available.

## `execution_v0`

Defaults are conservative research assumptions, not actual broker fees:

- spread 10 bps; slippage 5 bps; commission 10 bps; FX cost 0 bps;
- `fill_delay_bars=1` and fixed deterministic partial-fill ratio 0.5 when enabled;
- ambiguous policy `WORST_CASE`.

Minute-bar timestamps mean bar-open instants. An intent based on market data at T
can fill only on the first matching symbol bar with timestamp greater than T.
The bar open is the raw price. A BUY fill adds spread and slippage; a SELL fill
subtracts them. No later bar means `NO_NEXT_BAR`; the last known price is never
used. The fill records its session and whether the last signal-side bar and fill
bar crossed a session boundary; Stage 6 does not impose a strategy session rule.

Fixed-ratio partial fills were selected instead of bar volume because it is
deterministic, supports fractional Stage 5 quantity, and avoids claiming a volume
unit/participation model not yet validated. Random fills and market impact are
absent.

## Decimal cost and PnL policy

Bars remain float. Conversion uses `Decimal(str(value))`. Spread, slippage,
commission, FX cost, order/fill money, cash, PnL, and R are Decimal. Fill price
contains the adverse spread/slippage movement. Their exact opportunity cost is
also recorded for attribution; cash is not debited a second time for those two
items. Commission and FX cost are explicit cash charges.

Position average price and cost basis use fill execution notional only; fees stay
separate. Multiple BUYs use notional-weighted average. Partial SELL realizes
`(sell fill price − average entry price) × quantity`. A trade's gross PnL uses
execution prices; net PnL is gross minus all recorded entry/exit costs. Gross and
net R divide by the immutable initial planned risk. Zero initial planned risk is
invalid for a new trade. Pyramiding does not change this denominator.

## Ambiguity and shadow metadata

Stage 6 can record synthetic ambiguity counts. `WORST_CASE` is frozen so Stage 7
can resolve a bar containing both favorable and stop exits in favor of the stop
for a long. Threshold detection itself belongs to Stage 7.

Every Top-8 candidate fans out to variants A–E regardless of APPROVE, REJECT, or
missing Human decision. A is Intraday/ATR 1.5x, B is 2-Day/ATR 1.0x, C is
2-Day/ATR 1.5x, D is 2-Day/ATR 2.0x, and E is Intraday/Structure Stop. These are
IDs only in Stage 6; no ATR or exit behavior exists yet. Version is
`shadow_variants_v0`, C is the control, and the freeze/evaluation guard is at
least 300 eligible trades and 60 trading days. `NO_TRADE`, `OPEN`, `CLOSED`,
`UNFILLED`, and `REJECTED` are persistable research outcomes.

Sequential IDs are resettable, fixed-ratio fills contain no randomness, and all
times come from the input intent/bars. Thus identical replay data, intent,
configuration, and starting cash produce identical execution outcomes. Stage 7
will supply the common Premarket/Opening/ATR/Trailing lifecycle without creating
a separate Shadow strategy.
