# Strategy E — E-D0 Trading Preregistration V1

- Strategy: `STRATEGY_E`
- Name: `PREMARKET_OPEN_MOMENTUM_V1`
- Stage: `TRADING_DEVELOPMENT`
- Status: **PREREGISTERED before any Strategy E trading backtest**
- Machine-readable authority: `strategy_e_trading_rules_v1.json`

If this document conflicts with the JSON, the JSON is authoritative. Its canonical checksum uses
`json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)` and SHA256.

## 1. Boundary between Research Alpha and Trading Rules

The only Strategy E V1 Alpha is the frozen E1-H5 rule, reproduced verbatim:

```text
premarket_gap > 0
AND premarket_rvol >= 3.0
AND position_in_premarket_range >= 0.8
AND return_0900_0925 > 0
```

Research H5 and Trading H5 are identical. No fifth field, score, ranking input, execution property,
or risk property may enter this predicate. Candidate generation ends after the eligible row is
evaluated at 09:25 ET. Everything after that boundary is Execution/Risk and may reject a trade but
may not redefine Alpha eligibility.

Canonical statement: `premarket_gap > 0 and premarket_rvol >= 3.0 and position_in_premarket_range >= 0.8 and return_0900_0925 > 0`

Signal cutoff is **09:25 ET**. The final usable one-minute bar starts at 09:24 ET; no later
information may create or change a signal. A valid decision seal is required.

## 2. Eligible universe and PIT

The universe is imported unchanged from `e1_premarket_rules_v1.json`: evaluate the declared E0 PIT
universe at D-1 using the latest CS reference snapshot dated no later than D-1; allow primary
exchange XNAS/XNYS/XASE; require close(D-1) >= $5, 20-session median dollar volume over
[D-21,D-2] >= $5M with at least 15 sessions, and no split in (D-1,D]. At 09:25 require at least
three premarket bars in [04:00,09:24] and at least $50,000 premarket dollar volume. The research
dataset also requires a 09:30 bar, explicitly an after-cutoff data-availability limitation rather
than a signal input.

`premarket_gap`, `premarket_rvol`, `position_in_premarket_range`, and `return_0900_0925` retain the
E1 definitions. Daily inputs stop at D-1; intraday features stop at the 09:24 bar. Missing or
non-finite H5 inputs exclude the row. The forward seal records the eligible rows, four H5 inputs,
mask, sources, rules digest, and decision digest before labels may be attached; an existing seal is
not silently replaced.

## 3. Candidate and position-count policy

At 09:25, every eligible symbol satisfying H5 becomes a candidate. Common `risk_v1` limits apply:
at most three new symbols per day and at most three concurrent positions including pending base
entries. The deterministic priority when more than three H5 candidates occur in one session is:

```text
TBD — MUST BE FROZEN BEFORE E-D2 BACKTEST
```

No return-driven or Alpha-strength ranking may fill this gap.

## 4. Entry and exit

One entry proxy is admitted to the E-D2 contract scope:

```text
E0_OFFICIAL_OPEN_PROXY = open of the 09:30 ET one-minute aggregate bar
```

The dataset supports this field, but it is a research proxy—not a guaranteed executable fill.
Stocks Basic contains no bid/ask, opening-auction imbalance, auction fill, impact, or order-book
data. Therefore no first-tradable-minute fill or auction-fill model is registered.

The primary exit is fixed at **Open + 5 minutes**, using the exact 09:34 bar close. This preserves
the research primary horizon. The exact bar is required; the research fallback to the last print
does not become a trading execution. No secondary exit is registered and exit optimization is
forbidden.

## 5. Costs

Round-trip total execution-cost stress must report **5, 10, 15, and 20 bp** separately. Every
result must distinguish gross return, commission/fees, spread proxy, slippage proxy, total
execution cost, and net return. Actual spread is `UNKNOWN` because the Basic dataset has no quote.
Unknown commission or slippage components must not be presented as measured values. The observed
research gross edge of approximately 17 bp is context only and is not a model parameter.

## 6. Risk and sizing

The common Risk V1 contract is reused where applicable: planned per-position risk 0.5% of equity,
daily planned initial risk at most 3R, base capacity at most 80% of equity, symbol exposure at most
60%, at most three new symbols daily, and at most three concurrent positions. These are caps, not
Alpha filters.

Strategy E has no frozen structure stop at E-D0, so common stop-based 1R sizing cannot yet produce
a quantity. Position sizing must be deterministic, must not use Alpha strength, and must respect
Risk V1, but its policy is `TBD — MUST BE FROZEN BEFORE E-D2 BACKTEST`. Sector concentration and
opening-volatility handling are also TBD on the same deadline. The $5 Research universe floor is
not retuned; any stricter low-price/liquidity execution exclusion must remain separately labelled.

## 7. Fail-closed and session behavior

| Condition | Behavior |
| --- | --- |
| H5 feature missing/non-finite | `NO_TRADE` |
| 09:25 seal incomplete/invalid | `NO_TRADE` |
| entry price unavailable | `NO_TRADE` |
| exact exit price unavailable after entry | `INVALID_EXECUTION`, exclude from return and count |
| holiday / no session | `NO_TRADE` |
| shortened session | `NO_TRADE` until a separate policy is frozen |
| halt at entry or exit | invalid execution; count explicitly |
| no candidates | `NO_TRADE` |
| duplicate symbol-session | deterministic dedupe to one candidate |

Abnormal sessions fail closed unless a behavior is frozen before E-D2. Missing executions must
never be silently imputed.

## 8. No retuning

The H5 Alpha Contract must not change in response to Trading Backtest, Paper Trading, or Forward
Validation results. Any Alpha change is a separate new research hypothesis/version, not a change
to Strategy E V1. E0 remains closed and is not reused.

## 9. Historical runtime provenance limitation

Historical confirmation runtime artifact `e1h5-d57b212da84c` is not present in the current
workspace. Its generated trade/event payload therefore cannot be independently replay-audited.

This directory is a runtime-only artifact, not a canonical repository source: `.gitignore`
excludes all `data/runtime/*` content except `.gitkeep`, the confirmation runner writes its
generated payload only below `data/runtime/strategy_e_candidate/confirmation_runs/`, and the
repository tracks no runtime payload. The run ID remains recorded in the frozen confirmation
report, forward protocol, and the commit that introduced and froze the confirmation study.

The frozen research/trading rule provenance, canonical rule checksums, H5 identity, cutoff,
universe, and PIT contracts remain independently verifiable from tracked sources. This limitation
does not modify or re-estimate the frozen Alpha Contract. No missing payload was regenerated and
no existing research artifact was edited.

```text
Alpha = FROZEN
Trading Contract = PREREGISTERED
Trading Backtest = NOT YET
Paper = NOT YET
Live = NOT APPROVED
```
