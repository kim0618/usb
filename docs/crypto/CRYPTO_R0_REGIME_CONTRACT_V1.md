# US-B CRYPTO R0 — BTC Market Regime Contract V1

Status: **FROZEN BEFORE R0 RESULT COMPUTATION**  
Frozen at: 2026-10-03 (Asia/Seoul)  
Scope: research only; C1/C1x, PAPER, LIVE, deployment, and production paths are out of scope.

## 1. Question and unit of observation

R0 asks which side, if any, has relative forward advantage after the market state is known. It does
not define an entry signal. Every eligible BTCUSDT one-minute decision bar is classified exactly once.
The state uses information available by that bar's close. Forward benchmarks enter at the next
one-minute open and exit at the open 30, 60, 120, 240, or 480 minutes later.

The five labels are mutually exclusive. The fixed priority is R4, R3, R1, R2, R5. Priority is part of
the definition and must not be changed after results are seen.

## 2. PIT-safe inputs

For decision bar `t` (open timestamp `ts[t]`, information time `ts[t] + 1 minute`):

- `r4h = ln(close[t] / close[t-240])`
- `r24h = ln(close[t] / close[t-1440])`
- `trend = EWMA_12h(log close) - EWMA_72h(log close)`, adjusted=False and updated through `t`
- `vol24h = std(log(close[i]/close[i-1]), i=t-1439..t, ddof=0)`
- `oi4h = ln(OI_asof[t] / OI_asof[t-240])`; OI is already conservatively available at source
  timestamp +5 minutes under the D5 dataset contract
- `basis = ln(mark_close[t] / index_close[t])`
- `funding = latest settled funding rate with settlement timestamp <= information time`

`move_q75[t]` and `vol_q75[t]` are recalculated once per UTC day using finite observations from the
strictly preceding 30 UTC days `[D-30d, D)`. The move distribution is `abs(r4h)`; the volatility
distribution is `vol24h`. At least 15 days of minute observations are required. No current-day or
future observation enters either cutoff.

A row is eligible only when all inputs and both PIT cutoffs are finite. Missing input means unclassified,
not R5. No backward fill is allowed.

## 3. Frozen regime definitions

The derivatives confirmation count is the number of true conditions among the three listed items.

1. **R4 — Deleveraging / Panic**: `r4h < 0`, `vol24h >= vol_q75`, and `oi4h < 0`.
2. **R3 — Leveraged Expansion / Overheat**: not R4, `abs(r4h) >= move_q75`,
   `vol24h >= vol_q75`, `oi4h > 0`, and at least one of `sign(basis) == sign(r4h)` or
   `sign(funding) == sign(r4h)`.
3. **R1 — Uptrend / Risk-on**: not R4/R3, `r24h > 0`, `trend > 0`, and at least one of
   `oi4h > 0`, `basis > 0`, or `funding > 0`.
4. **R2 — Downtrend / Risk-off**: not R4/R3/R1, `r24h < 0`, `trend < 0`, and at least one of
   `oi4h < 0`, `basis < 0`, or `funding < 0`.
5. **R5 — Range / Neutral**: every other eligible row.

Interpretation: R1/R2 require price direction plus a slower trend and at least one derivatives
confirmation. R3 requires simultaneous large movement, high volatility, and expanding leverage in
the move's direction. R4 requires a falling, high-volatility market with contracting leverage. R5 is
the explicit residual state and is not assumed to have zero edge before measurement.

## 4. Forward economics

For horizon `h`, entry is `open[t+1]` and exit is `open[t+1+h]`:

- LONG gross = `exit / entry - 1`
- SHORT gross = `entry / exit - 1`
- fees = Binance/Bybit-compatible VIP0 taker `0.00055 * (1 + exit/entry)`
- micro cost = `0.00000118` per round trip (D5 VIP0_BASE convention)
- funding uses settlements in `[entry time, exit time)` and the contemporaneous mark/entry-notional
  ratio; paid by LONG when the funding rate is positive and received by SHORT
- LONG net = LONG gross - fees - micro cost - funding cash flow
- SHORT net = SHORT gross - fees - micro cost + funding cash flow
- directional edge = LONG net - SHORT net; NO TRADE return = 0

All horizons are benchmarks, not time-based strategy exits. No overlapping observations are removed
from the primary state-level estimate; dependence is addressed with UTC-day clustered bootstrap CIs.

## 5. Reporting and verdicts

Report coverage, eligible/unclassified counts, state frequency, contiguous episode duration and
transition matrix; gross/net mean, median, win rate, profit factor, sample count, clustered 95% CI,
and directional edge for both sides at every horizon; calendar-year and fixed chronological-fold
stability; and transition forward benchmarks.

Transition analysis uses only the first minute of each newly entered contiguous regime. The named
R1→R2, R2→R1, R3→R2, and R4→R1 transitions are mandatory. Sparse cells are reported, never pooled
or silently dropped.

Regime verdict at the primary 4h benchmark:

- `LONG_DIRECTIONAL`: directional edge > 0, LONG net > 0, and at least 60% of eligible calendar
  years have directional edge > 0.
- `SHORT_DIRECTIONAL`: directional edge < 0, SHORT net > 0, and at least 60% of eligible calendar
  years have directional edge < 0.
- `MIXED`: neither directional rule passes, but either side is net positive or the directional-edge
  clustered CI excludes zero.
- `NO_EDGE`: both side net means <= 0 and directional-edge CI includes zero.

The CI lower bound is deliberately not a mandatory directional criterion. These labels are research
triage, not production promotion gates.

## 6. Existing-research joins

- C1 is reconstructed from its frozen D5.2/operational contract and reported both minute-weighted and
  event-start-weighted by regime. C1 itself is not changed.
- Existing C2 SHORT E1 trades are joined to the regime known at their frozen signal timestamp. Losing
  trades (`net_return <= 0`) and all trades are both reported. No C2 rule is changed or re-run with a
  modified threshold.

## 7. Prohibitions

No new entry strategy, parameter sweep, C1/C1x modification, PAPER/LIVE change, deployment change,
production change, commit, or push belongs to R0. Results end with a research plan and STOP.
