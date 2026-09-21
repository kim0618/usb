# Strategy E — E-D2 Entry / Execution Contract V1

## Status

- Stage: `E-D2_ENTRY_EXECUTION`
- Status: **FROZEN BEFORE ANY STRATEGY E TRADING BACKTEST**
- Machine-readable authority: `strategy_e_execution_rules_v1.json`
- Upstream E-D0 digest: `f1534f07688c801f2979491e447eefbb3e62b8045afb9c4ab302b90e4593d4b2`
- Signal version: `STRATEGY_E_H5_SIGNAL_V1`

This contract freezes candidate selection, execution eligibility, and entry price identity. It
does not calculate or compare a return and defines no exit, stop, size, cost, or PnL rule.

## 1. Data capability audit

The Strategy E loader reads `MASSIVE_TICKER_AGGREGATE adjusted=false`. Raw `t` and normalized
`timestamp_utc` identify the start of the aggregate window and are converted to an ET session and
minute. `SymbolTape` preserves symbol, ET session/minute, open, high, low, close, volume, and VWAP.

| Candidate field | Verdict | Meaning |
| --- | --- | --- |
| Official opening-auction fill | **NOT IDENTIFIABLE FROM CURRENT DATA** | no auction/imbalance/venue flag |
| 09:30 one-minute open | **AVAILABLE** | `open` of the aggregate starting 09:30 ET |
| 09:30 one-minute VWAP | **AVAILABLE** | aggregate `vwap`, not selected for entry |
| 09:30 one-minute close | **AVAILABLE** | aggregate `close`, not selected for entry |
| 09:31 one-minute open | **AVAILABLE WHEN BAR EXISTS** | not selected; absent minutes remain absent |

The provider schema contains OHLCV aggregates, not an auction execution feed. Halt state is also
**NOT IDENTIFIABLE FROM CURRENT DATA**; no inferred halt detector is introduced.

## 2. Frozen signal boundary

The decision remains 09:25 ET and Alpha is only the E-D0/E-D1 frozen H5 result. E-D2 receives
candidate identifiers and provenance, never H5 feature magnitudes. Entry availability may reject
a trade but never turns an H5 boolean false.

```text
H5 alpha_candidates
→ canonical selection
→ execution eligibility
→ entry records
```

## 3. Deterministic candidate selection

Common `RiskConfig(version="risk_v1")` freezes both `max_new_symbols_per_day` and
`max_open_positions` at 3. E-D2 therefore selects at most three H5 candidates by **canonical
symbol ascending**. This ordering is independent of market values and outcomes. Gap, RVOL,
premarket range position, premarket return, and any H5 strength score are forbidden ranking inputs.

Overflow candidates remain `alpha_candidate=true` and are recorded as `selected=false` with
`NOT_SELECTED_CAPACITY`. Portfolio state and final Risk eligibility remain later-stage concerns;
E-D2 does not size or reserve capital.

## 4. Primary entry model

```text
model:           FIRST_REGULAR_MINUTE_OPEN_PROXY_V1
decision time:   09:25 ET
entry bar:       one-minute aggregate starting 09:30 ET
entry field:     open
```

This is a historical first-regular-minute open proxy. It is **not** an official opening-auction
fill and is not a guaranteed live fill. Even if the provider publishes a completed aggregate
after 09:31, only its recorded `open` is used as the historical proxy. The bar's high, low, close,
volume, VWAP, every later bar, and every future label are forbidden entry-price inputs.

## 5. Execution eligibility and abnormal sessions

A selected candidate is executable only if exactly one matching symbol/session bar starts at
09:30 ET and its `open` is finite and positive. No forward-fill, backfill, interpolation, delayed
first-bar substitution, or alternate field is allowed.

| Condition | Result |
| --- | --- |
| missing 09:30 or first bar delayed | `NO_TRADE_MISSING_ENTRY_BAR` |
| NaN, zero, or negative open | `NO_TRADE_INVALID_ENTRY_OPEN` |
| duplicate 09:30 aggregate | `NO_TRADE_DUPLICATE_ENTRY_BAR` |
| same-symbol bars from another session only | `NO_TRADE_SESSION_MISMATCH` |
| holiday/non-session | `NO_TRADE_INVALID_SESSION` |
| shortened session | `NO_TRADE_SHORTENED_SESSION` |

The short-session rule resolves E-D0's pending abnormal-session behavior conservatively without
observing outcomes. A halt cannot be identified from the stored aggregates; missing 09:30 data
still fails closed under the missing-bar rule.

## 6. Entry record and provenance

Each immutable record carries strategy/signal/execution versions, session and symbol, 09:25
decision time, E-D1 signal and source digests, E-D0 rules digest, E-D2 rules digest, selection rank,
Alpha/selection/execution booleans, entry model/timestamp/price, and a skip reason. A canonical
batch SHA256 binds these fields.

Records contain no exit, future return, PnL, cost-adjusted PnL, stop, or position size.

## 7. Execution limitations

The one-minute open proxy does not model:

- bid/ask spread;
- queue position;
- auction imbalance or auction participation;
- market impact;
- latency;
- partial fills;
- trading halts.

These limitations are not silently estimated or folded into Alpha. Applicable execution/cost
stress belongs to the later preregistered stage.
