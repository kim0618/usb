# Strategy E — E-D5 Risk / Position Sizing Contract V1

## Status

- Stage: `E-D5_RISK_SIZING`
- Status: **FROZEN BEFORE ANY STRATEGY E TRADING BACKTEST**
- Machine-readable authority: `strategy_e_risk_rules_v1.json`
- Risk version: `STRATEGY_E_RISK_V1`
- Sizing model: `EQUAL_WEIGHT_EXECUTABLE_V1`

This contract defines dimensionless Strategy E backtest normalization. It does not calculate
historical returns, choose a stop, recommend capital, or create an order quantity.

## 1. Common Risk V1 audit and boundary

`RiskConfig(version="risk_v1")` freezes:

| Constraint | Common value |
| --- | ---: |
| new symbols per day | 3 |
| concurrent open symbols | 3 |
| base capacity | 80% of account equity |
| symbol exposure cap | 60% of account equity |
| planned risk per trade | 0.5% of equity |
| daily planned initial-risk limit | 3R |
| leverage/margin | none |
| sector limit | none |

Common Risk sizing requires a Strategy-supplied structure stop and caps real account notional by
cash, base capacity and symbol capacity. Strategy E V1 has no stop and therefore does not invoke or
replace that real-account sizing path. Its normalized gross-notional unit of `1` is dimensionless;
it does not mean 100% account investment, leverage, buying power, or live-dollar allocation.

E-D5 reuses the common count limit of three. Real-money mapping requires a later deployment
contract consistent with Common Risk V1.

## 2. Frozen selection and sizing

```text
candidate priority = canonical symbol ascending
maximum selected = 3
selection first → execution validation second
replacement = NONE
```

The frozen top three are never backfilled from candidate four when one fails E-D2 or E-D3. Alpha
feature magnitude and signal strength never enter sizing.

For the records eligible for standard PnL, one normalized gross-notional unit is divided equally:

```text
1 executable → 1
2 executable → 1/2 each
3 executable → 1/3 each
0 executable → daily exposure 0
```

Invalid records receive zero. Remaining executable records are re-normalized to sum exactly to one.
Weights use exact rational numerator/denominator representation, so one-third is not rounded and
the total remains exactly one. Input row order cannot alter ordering, weights, or digest.

E-D3 exit validity is known only after the fixed horizon completes. Consequently this standard-PnL
eligibility normalization is an audit/backtest accounting contract, not a claim that a live 09:30
order could foresee a later missing 09:34 bar.

## 3. Exit risk

- Intratrade stop: `NONE`
- Profit target: `NONE`
- Partial exit: `NONE`
- Normal exit: the complete position at exact 09:34 under E-D3
- Overnight position: `NONE`

A valid entry without a valid exact-09:34 exit remains `UNRESOLVED_EXIT / INVALID_FOR_STANDARD_PNL`.
No session-close, last-bar, or synthetic liquidation price is created. E-D6 must report the
frequency as a risk/data-quality metric. The invalid record receives zero standard-PnL weight but
is retained in the sizing audit output.

## 4. Sector, volatility, and capacity limitations

The Strategy E historical chain contains no reliable sector classification. Sector concentration
control is therefore `NOT ENFORCED`; no sector is inferred or filtered. E-D6 may report sector
metadata only if it exists with appropriate provenance.

No additional opening-volatility gate exists. Post-09:25 high, low, or realized volatility cannot
affect entry or sizing, and no new pre-09:25 Alpha field is introduced. Later diagnostic buckets
must not rewrite the frozen decision.

Market impact remains `NONE / NOT YET EXPLICITLY MODELED`. A real-dollar deployment must validate
position notional against liquidity before treating normalized weights as capacity evidence.

## 5. Determinism and provenance

The sizing primitive accepts an E-D3 batch, preserves every selected/unselected record, sorts by
symbol, assigns exact weights, and emits an immutable batch digest. Each record retains signal,
execution, exit and E-D5 rules identity. The rules chain references E-D0 through E-D4 and Common
Risk `risk_v1`; none of those frozen artifacts is modified.
