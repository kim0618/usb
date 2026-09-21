# Strategy E — E-D3 Horizon Semantics Resolution V1

## Resolution

This artifact separates two already-existing meanings. It changes neither one.

```text
Research primary R_5m
IS NOT
the literal Trading Exit Contract.
```

Research `R_5m` is a flexible observational label used during Alpha discovery. Trading V1 turns
the observed five-minute opening horizon into a deterministic fixed-time contract. Trading V1
therefore requires the exact 09:34 close.

No return, PnL, or comparison between `R_5m` and `R_5m_strict` was computed for this resolution.

## 1. Authoritative Research semantics

Implementation: `backend/app/backtest/strategy_e1_premarket/premarket.py`, functions
`opening_block` and `labels`.

```text
R_5m
= open anchor: exact 09:30 bar.open
+ observation window: bar starts 09:30 through 09:34 inclusive
+ observation field: last existing bar.close in that window
```

When the exact 09:34 bar is absent, `R_5m` uses the close of the last existing bar in the window.
The existing regression test demonstrates a 09:32 fallback without creating a new calculation.

```text
R_5m_strict
= open anchor: exact 09:30 bar.open
+ observation field: exact 09:34 bar.close
+ missing exact 09:34: NaN
```

The implementation and the existing tests are unchanged by this resolution.

## 2. Frozen Trading semantics

```text
model: FIXED_FIVE_MINUTE_CLOSE_PROXY_V1
entry: exact 09:30 bar.open
exit:  exact 09:34 bar.close
missing exact 09:34: exit_valid=false
fallback: FORBIDDEN
```

Forbidden substitutes include 09:33 close, 09:32 close, any last-existing-bar fallback, 09:35
open, session close, forward-fill, and backfill.

Exact 09:34 close is available from the current minute schema when that aggregate exists. Its
absence remains explicit; the Trading layer does not synthesize a value.

## 3. Relationship

Trading V1 exact-09:34 field semantics are structurally closer to `R_5m_strict` than to `R_5m`.
That statement concerns timestamp and field identity only. It makes no claim that either label has
better or worse performance.

The distinction is intentional:

- Research may retain an observation on a sparse tape using its preregistered flexible label.
- Trading requires one reproducible fixed-time exit field and marks the execution invalid when it
  is unavailable.

This is not Alpha retuning. Exact 09:34 was already frozen in E-D0 before any Strategy E trading
backtest or PnL evaluation. The blocker exposed an imprecise claim that the literal semantics were
identical; this artifact corrects that provenance description without changing a rule.

## 4. Frozen provenance chain

| Stage | Authority |
| --- | --- |
| E-D0 | commit `89f27c6…`, rules `f1534f07688c…` |
| E-D1 | commit `c210f6f…`, signal `STRATEGY_E_H5_SIGNAL_V1` |
| E-D2 | commit `1ba07734…`, execution rules `d204b1dac9dd…` |

The E-D0, E-D1, E-D2, Research, and Confirmation artifacts are not modified. The machine-readable
authority for this clarification is `strategy_e_horizon_semantics_v1.json` and its canonical
checksum file.
