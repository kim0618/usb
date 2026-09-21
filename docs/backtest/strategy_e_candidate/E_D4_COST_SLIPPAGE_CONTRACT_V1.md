# Strategy E — E-D4 Cost / Slippage Contract V1

## Status and purpose

- Stage: `E-D4_COST_SLIPPAGE`
- Status: **FROZEN BEFORE ANY STRATEGY E TRADING BACKTEST**
- Machine-readable authority: `strategy_e_cost_rules_v1.json`
- Cost version: `STRATEGY_E_COST_V1`
- Cost model: `TOTAL_ROUND_TRIP_SUBTRACTION_V1`

This is a pre-registered execution-friction stress model. It is not an observed spread, fill,
slippage, fee, or market-impact model. No historical Strategy E trade table or performance metric
was produced to select it.

## 1. Data capability audit

The current Strategy E minute-bar contract contains symbol, session, bar start, OHLC, volume and
optional aggregate VWAP. Premarket features also contain frozen dollar-volume derivations. It does
not contain quote, auction, venue, halt, or trade-count fields. A 09:30 one-minute aggregate is not
identified as the official opening-auction print.

| Item | Classification | Repository-grounded meaning |
| --- | --- | --- |
| bid | `NOT AVAILABLE` | no Strategy E quote field |
| ask | `NOT AVAILABLE` | no Strategy E quote field |
| spread | `NOT IDENTIFIABLE` | bid/ask absent; bar movement is not spread |
| NBBO | `NOT AVAILABLE` | no quote feed |
| trade count | `NOT AVAILABLE` | absent from the Strategy E bar contract |
| volume | `AVAILABLE` | minute aggregate field |
| VWAP | `AVAILABLE` | optional minute aggregate field |
| OHLC | `AVAILABLE` | minute aggregate fields |
| opening auction price | `NOT IDENTIFIABLE` | 09:30 aggregate open is not auction-labelled |
| opening auction volume | `NOT IDENTIFIABLE` | 09:30 aggregate volume is not auction-labelled |
| venue | `NOT AVAILABLE` | absent from Strategy E records |
| halt | `NOT IDENTIFIABLE` | missing bars do not identify their cause |
| market cap | `NOT AVAILABLE` | no PIT market-cap series for this universe |
| D-1 dollar volume | `DERIVABLE` | D-1 daily price × volume / frozen prior-day feature |
| premarket dollar volume | `DERIVABLE` | sum of aggregate VWAP × volume through the cutoff |
| regular-session volume | `DERIVABLE` | sum of available regular-session minute volume |

## 2. Frozen all-in round-trip scenarios

| Scenario | Total round-trip deduction |
| --- | ---: |
| `COST_05BP` | 5 bp = 0.0005 |
| `COST_10BP` | 10 bp = 0.0010 |
| `COST_15BP` | 15 bp = 0.0015 |
| `COST_20BP` | 20 bp = 0.0020 |

Each number covers the complete entry-plus-exit trade. It is not a per-side number and is not
applied twice. No amount is allocated to either leg. `GROSS_0BP` may be reported later only as a
diagnostic gross baseline, never as a realistic execution scenario.

```text
gross_return = exit_price / entry_price - 1
round_trip_cost_decimal = round_trip_cost_bp / 10000
net_return = gross_return - round_trip_cost_decimal
```

This simple total subtraction is the E-D0 frozen Strategy E convention. Strategy B's per-side
fill-price and cash-fee contract is strategy-specific and does not replace E-D0. Costs always move
a long trade adversely. Price- and liquidity-dependent multipliers are disabled.

The primitive converts source price text to `Decimal`, retains full calculation precision, and
does not round intermediate results. Rounding is a presentation concern only.

## 3. Taxonomy and limitations

- Broker commission, SEC/TAF and other explicit fees: `NOT YET MODELED SEPARATELY`.
- Observed spread: `UNKNOWN`; high-low, open-close, and VWAP-open are forbidden substitutes.
- Actual order slippage: `UNKNOWN`; one-minute OHLCV cannot identify it.
- Market impact: `NOT YET EXPLICITLY MODELED`; E-D5 size does not yet exist.
- Latency, queue position and auction uncertainty: `NOT IDENTIFIABLE`.

The grid is an all-in generic friction stress, so a future version that adds a separate broker fee
or execution component must explicitly reconcile or replace the overlapping amount. It must not
silently double count it.

The same four scenarios apply to every valid trade. The 5-dollar universe floor does not establish
that 5–20 bp is sufficient for lower-priced names. That possible understatement is a limitation,
not permission to introduce a post-result price multiplier. A distinct model requires quote or
paper/live fill evidence and a newly preregistered version.

## 4. Record and provenance

Only a valid E-D3 entry/exit record can produce an E-D4 record. Invalid entry or exit means no cost
result; it is never converted to a zero return. The immutable output records entry and exit prices,
gross return, scenario identity, total cost, net return, all upstream identities, and a canonical
digest. Repeated identical inputs produce identical numeric values and digest.

The rules reference the frozen E-D0 rules, E-D1 signal version, E-D2 execution contract, E-D3
horizon clarification, and E-D3 exit contract. E-D4 changes none of those artifacts.
