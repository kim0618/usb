# USB Trading & Risk Foundation — Stage 5

## Purpose and architecture

Stage 5 defines the common, broker-independent decision and capital-survival
domain used by later trading strategies. It deliberately stops at `OrderIntent`:

```text
Market State → StrategyEngine → RiskEngine → OrderIntent → future adapter
```

`StrategyEngine` decides trade quality (`ENTER`, `HOLD`, `ADD`, `EXIT`,
`OVERNIGHT_HOLD`, or `NO_TRADE`). `RiskEngine` never decides whether a stock is
good; it determines whether an already-proposed action is allowed and its maximum
size. Neither module imports a broker, repository, SQLAlchemy model, or GPT model.
An `OrderIntent` is an immutable request description, not a submitted order,
acknowledgement, or fill. It therefore contains no venue, TIF, broker order type,
or broker tick/lot rounding.

`StrategyDecision.market_as_of` is the market-information cutoff used by the
decision. Optional `created_at` is audit time only. Risk calculation receives its
audit `created_at` explicitly, so a wall clock cannot change sizing.

## Human approval and safe mode boundary

Human `APPROVE` means that Strategy execution is eligible; it does not create an
`ENTER` decision or an intent. A base intent requires all three independent gates:

1. human approval;
2. a Strategy `ENTER` decision;
3. Risk approval.

`TradingEligibility.safe_mode` blocks `ENTER` and `ADD`. Stage 5 does not manage
Safe Mode itself. Premarket and Opening gates are also intentionally absent.

## Money and currency policy

Bulk market bars and scanner analytics retain `float`. Money, account values,
prices at the order boundary, FX, quantities, notionals, and risk use `Decimal`.
The boundary helper converts floats through `Decimal(str(value))`, never
`Decimal(value)`. Exact reservations are stored as decimal strings in SQLite.

Stage 5 does not quantize to cents, ticks, or integer shares. Calculations retain
Decimal precision and permit fractional internal quantity. A future execution
adapter must apply measured broker capabilities without changing Strategy/Risk.

Currencies are explicitly `USD` or `KRW`. Same-currency sizing uses multiplier 1.
Cross-currency sizing requires one directed `FxRate`:

```text
base_currency = instrument currency
quote_currency = account currency
quote_per_base = account-currency units for one instrument-currency unit
```

For example, USD/KRW `quote_per_base=1350` means 1 USD = 1,350 KRW. A missing or
wrongly directed rate rejects the calculation. Stage 5 provides no FX provider,
conversion graph, spread, or FX cost engine. All caps are calculated in account
currency.

## Risk V0 and position sizing

`RiskConfig(version="risk_v0")` is the single source of truth:

- per-trade planned risk: equity × 0.5% (`1R`);
- daily planned initial-risk limit: `2R`;
- base capacity: at most 80% of equity;
- pyramid reserve: separate 20% of equity;
- total symbol exposure safety cap: at most 60% of equity;
- at most two newly issued symbol entry intents per trading day;
- one entry intent per symbol and trading day;
- at most one pyramid add;
- overnight stress gap: 20%;
- maximum overnight stress loss: 5% of equity;
- at most one overnight position.

For a long base entry, Strategy supplies the structure-derived entry and stop;
Risk does not calculate either price. Risk requires `entry > stop > 0` and uses:

```text
1R = account equity × 0.005
per-share risk (instrument currency) = entry − stop
per-share risk (account currency) = per-share risk × FX multiplier
risk quantity = 1R / per-share risk (account currency)
risk notional = risk quantity × entry × FX multiplier

final account notional = min(
    risk notional,
    remaining base capacity,
    remaining symbol capacity,
    cash,
)
final quantity = final account notional / (entry × FX multiplier)
```

There is no leverage, margin, short sizing, or forced allocation. A cap may reduce
planned risk below 1R. The base path cannot consume pyramid reserve. Cash and
exposure must remain within equity. `1R` is planned initial stop loss, not a loss
guarantee: slippage and especially overnight gaps can exceed it.

Example, USD account: equity 100,000, entry 100, stop 98 gives 1R=500,
per-share risk=2, quantity=250, and notional=25,000.

Example, KRW account: equity 1,000,000, USD entry 100, stop 98, and 1 USD=1,350
KRW gives 1R=5,000, per-share risk=2,700 KRW, and quantity
`5000 / 2700 = 1.851851…`. Notional and all caps are then compared in KRW.

## Pyramiding foundation

Stage 5 implements only enforceable capital invariants, not an ADD market trigger
or complete ADD sizing strategy. An `ADD` requires an existing profitable position
(`current_price > average_price`), approval, non-Safe Mode, reserve capacity, cash,
symbol capacity, and `add_count < 1`. This makes averaging down forbidden and keeps
the 20% reserve unavailable to base entries. Stage 7 supplies the actual momentum,
VWAP, confirmation, and risk-reduction rules.

## Overnight stress foundation

For any explicit positive gap scenario:

```text
estimated loss = proposed overnight notional × abs(gap scenario)
account loss pct = estimated loss / equity
maximum notional = equity × 5% / abs(gap scenario)
```

At the default 20% scenario, the maximum is 25% of equity. Proposed notional above
that is outside the stress limit. The calculation and maximum-one-position query
are foundations only; no overnight Strategy decision or transition intent exists.

## Rejections and deterministic result

Expected denials are `RiskEvaluation(approved=False)` values with a typed reason,
not routine exceptions. Reasons cover eligibility/Safe Mode, invalid prices or
account/portfolio state, FX mismatch, daily risk and symbol limits, duplicate
attempt, exhausted base/pyramid/symbol/cash capacity, pyramid limit, losing ADD,
and missing position. Invalid domain construction remains a `ValueError`.

Given identical account, portfolio, decision, eligibility, prices, FX, config,
daily state, market time, and explicit audit timestamp, Risk returns the same
result. `RiskMetrics` exposes 1R, actual planned risk, requested/final quantities
and notionals, per-share risk, and remaining capacities for audit and tests.

## Persistence and atomicity

`daily_symbol_states` is a minimal planned-state table with a unique
`(trading_date, symbol)` key. It records only:

- the time a final entry intent was issued;
- planned initial risk reserved;
- base and pyramid notional reserved;
- ADD count;
- risk and strategy versions;
- audit timestamps.

“Entry attempt” means Risk approved and `RiskService` atomically persisted the
final `BASE_ENTRY` intent reservation. A Strategy `ENTER` rejected by Risk is not
an attempt. No `entered` or fill field exists because Stage 5 has no broker.

`RiskEngine` is pure calculation. `RiskService` loads daily state, evaluates,
flushes the unique reservation, and commits as one transaction; any error rolls
back. The unique key prevents two same-symbol/day reservations even under a race.
Reloading the database reconstructs attempted symbols, daily planned risk, base
and pyramid reservation, and ADD counts. SQLite/single-worker V1 needs no
distributed lock. Current broker positions will later remain a separate source of
truth rather than expanding this table into a premature position ledger.

## Known limitations and next stages

Stage 5 has no concrete Premarket, Opening Range, VWAP, stop-computation, trailing,
pyramid-trigger, exit, overnight-selection, or Day 2 strategy. It has no broker,
order transmission, fills, commission, spread, slippage, or broker rounding.
OrderIntent is not persisted; only its restart-critical risk reservation is.

Stage 6 adds SimBroker, order/fill/cost models, and Shadow execution. Stage 7 adds
the common concrete Strategy lifecycle and uses these Risk foundations. Broker
capability measurement and Kiwoom remain deferred to Stage 10/11.
