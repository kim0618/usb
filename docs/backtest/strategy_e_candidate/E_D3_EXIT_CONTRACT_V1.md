# Strategy E — E-D3 Exit Contract V1

## Status

- Stage: `E-D3_EXIT_EXECUTION`
- Status: **FROZEN BEFORE ANY STRATEGY E TRADING BACKTEST**
- Machine-readable authority: `strategy_e_exit_rules_v1.json`
- Exit version: `STRATEGY_E_EXIT_V1`
- Exit model: `FIXED_FIVE_MINUTE_CLOSE_PROXY_V1`

This contract implements the Trading V1 meaning frozen in E-D0 and clarified by
`E_D3_HORIZON_SEMANTICS_RESOLUTION_V1.md`. It does not reproduce Research `R_5m` and computes no
return or PnL.

## 1. Exact fixed-time exit

```text
entry = exact 09:30 ET aggregate open
exit  = exact 09:34 ET aggregate close
```

The 09:34 timestamp is the start of the one-minute aggregate covering 09:34:00 through the final
observation before 09:35:00 ET. Only `close` is read for the exit price. High, low, volume, VWAP,
09:35+ bars, later sessions, and Research labels are not inputs.

No fallback exists. The resolver never uses 09:33/09:32 close, last known price, Research
last-existing-bar semantics, 09:35 open, session close, forward-fill, backfill, interpolation, or
nearest-bar substitution.

## 2. Layer boundary

```text
H5 Signal → E-D2 selection/entry records → E-D3 exit records
```

The exit layer returns new immutable records. It does not modify the H5 mask, selection rank,
entry eligibility, entry timestamp, entry price, or the E-D2 batch.

An entry that is unselected, skipped, invalid, or otherwise not execution-eligible produces
`NO_EXIT_NO_VALID_ENTRY` without a valid exit or price lookup result.

## 3. Fail-closed rules

For an eligible exact-09:30 entry, a valid exit requires one and only one matching symbol/session
09:34 aggregate and a finite positive close.

| Condition | Reason |
| --- | --- |
| no valid entry | `NO_EXIT_NO_VALID_ENTRY` |
| exact 09:34 missing | `NO_TRADE_MISSING_EXIT_BAR` |
| duplicate exact 09:34 | `NO_TRADE_DUPLICATE_EXIT_BAR` |
| NaN/zero/negative close | `NO_TRADE_INVALID_EXIT_CLOSE` |
| session mismatch | `NO_TRADE_SESSION_MISMATCH` |
| symbol mismatch | `NO_TRADE_SYMBOL_MISMATCH` |
| non-session | `NO_TRADE_INVALID_SESSION` |
| shortened session | `NO_TRADE_SHORTENED_SESSION` |

E-D2 prohibits shortened-session entry. The E-D3 check is a defensive invariant for malformed
upstream input.

The data has no halt identifier. `HALT_STATUS = UNKNOWN`; a missing 09:34 aggregate is recorded as
missing without guessing whether halt, liquidity, or vendor coverage caused it.

## 4. Determinism and provenance

Input entry records are canonicalized by symbol. Each exit record carries:

- E-D0 rules digest and E-D1 signal/source identity;
- E-D2 execution version, rules digest, and batch digest;
- E-D3 horizon-semantics digest;
- E-D3 exit version and rules digest;
- unchanged entry identity and the exact exit identity/status.

The exit batch digest hashes only this canonical output and its upstream execution identity.
Changing row order, post-09:34 bars, or unused 09:34 fields cannot change the output. Changing the
exact 09:34 close changes the exit identity as intended.

Exit records intentionally contain no return, gross/net PnL, fees, slippage, size, or portfolio
metric.
