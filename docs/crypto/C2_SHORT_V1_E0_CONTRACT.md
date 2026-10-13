# US-B CRYPTO C2 SHORT V1 — E0 selection and E1 preregistration

Frozen before any forward-return or trade result for this candidate was computed.

## E0 candidate audit

| Candidate | Market logic | PIT-safe inputs | Historical/live coverage | Cost | Prior overlap | Decision |
|---|---|---|---|---|---|---|
| A Perp Premium Exhaustion | leveraged demand lifts perp over spot; rising OI confirms new leverage; enter only after the rise fails | completed Bybit/spot closes, delayed Bybit OI, trailing windows excluding future bars | D5.2 historical archives and C1 live readers provide the same three sources | VIP0 taker round trip plus observed funding | unlike C1: premium high, OI rising, SHORT, and failure confirmation | **SELECT** |
| B Leveraged Blow-off Failure | sharp rise plus OI growth fails at a new high | Bybit price/OI | full D2 history and live | same | materially overlaps D5.4 C2 and D5.5 A/B/C shock/failure work | REJECT_OVERLAP |
| C Crowded Long Unwind | funding/basis/OI/volatility crowding then failure | Bybit funding, basis, OI, volatility | available, but funding changes only at settlement and conjunction is sparse | same | mostly A plus two extra filters; high multiple-testing/coverage risk | REJECT_COMPLEXITY |

## Selected hypothesis

`C2-PREMIUM-EXHAUSTION-240M-SHORT`:

1. `premium = ln(Bybit BTCUSDT perpetual close / Binance BTCUSDT spot close)`.
2. `premium_z30 >= 3.0`, where the robust z-score uses the prior 30 complete UTC days only.
3. `oi_change_60m_z30 >= 2.0`, using OI known after the existing five-minute availability delay.
4. A prior 30-minute high exists and the current close is at least 25 bp below it.
5. Five-minute log return is negative.

The first bar satisfying all conditions starts one event. Consecutive true bars are one event and
new events are suppressed for 240 minutes. The condition is not C1 inverted: C1 requires a low
spot basis, falling OI and high volatility; C2 requires high premium, rising OI and an observed
price failure.

## Frozen execution and evaluation

- Decision at the close of bar `t`; entry at `open[t+1]`.
- SHORT only.
- Official exit: `open[t+1+240]`; no stop, target, dynamic exit, or best-horizon selection.
- VIP0 taker fee on entry and exit, historical funding settlements while held, and the frozen
  D5 BASE micro-cost are deducted.
- One event is one trade. Overlapping trades are not opened during the 240-minute hold/cooldown.
- Research window: 2022-01-01 through 2026-09-23, using the existing F1–F9 half-year folds.
- Report trades, mean/median net, PF, win rate, seven-day clustered bootstrap CI, fold/year
  stability, top-5%-winner removal, and MFE/MAE.
- Bootstrap: 2,000 iterations, seed 20261003.

## Frozen verdict

- `CLEARLY_NEGATIVE`: mean net <= 0 and CI upper <= 0; or PF < 0.8 with mean net <= 0 and net
  positive in fewer than 3 of 9 folds.
- `POSITIVE_PREVALIDATION`: mean net > 0, CI lower > 0, PF > 1, at least 5/9 positive folds,
  and top-5%-winner removal remains positive.
- Otherwise `INCONCLUSIVE_BUT_PLAUSIBLE` only when mean net > 0, PF > 1, at least 4/9 positive
  folds, and top-5%-winner removal is not negative. All other results are `CLEARLY_NEGATIVE`.

No threshold sweep or alternative exit is authorized by this contract.
