# US-B CRYPTO — PAPER Experimental LONG/SHORT V1

Date: 2026-10-03. Historical data ends 2026-09-23. Decision: **STOP — no candidate selected**.

## Fixed candidate set

This was a five-candidate comparison, not a threshold sweep. All inputs are completed bars and
the existing five-minute-delayed OI grid. Entry is next-minute open, exit is the fixed 240-minute
open, concurrent positions are limited to one, and costs are Bybit VIP0 taker on both notionals,
the frozen BASE micro-cost, and actual funding settlements.

LONG keeps falling 60-minute OI in every case. `EL_PREMIUM_B2` changes only premium B1 to B1+B2;
`EL_VOL_MID` changes only HIGH volatility to MID/HIGH; `EL_LIMITED_COMBO` combines those two.
SHORT does not invert C1 or reuse the closed C2 blow-off/high-failure rule:
`ES_BREAKDOWN_OI_EXPANSION` is lower-tail 60-minute downside continuation with expanding 15-minute
OI, and `ES_FAILED_REBOUND` is a top-decile 15-minute rebound failure inside a negative 4-hour
trend with falling OI.

## Trade-level results

| Candidate | Trades | /week | mean net bp | median bp | PF | win | clustered 95% CI bp | top 5% removed bp | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| EL_PREMIUM_B2 | 940 | 3.29 | +3.82 | +1.64 | 1.06 | 50.5% | [-8.67, +15.81] | -20.61 | CLEARLY_NEGATIVE |
| EL_VOL_MID | 1,689 | 5.88 | +2.72 | -2.02 | 1.05 | 49.3% | [-4.55, +10.34] | -18.95 | CLEARLY_NEGATIVE |
| EL_LIMITED_COMBO | 2,595 | 9.03 | -3.40 | -5.11 | 0.94 | 47.9% | [-9.33, +2.58] | -24.79 | CLEARLY_NEGATIVE |
| ES_BREAKDOWN_OI_EXPANSION | 4,242 | 14.63 | -12.37 | -19.37 | 0.77 | 40.0% | [-16.19, -8.70] | -31.34 | CLEARLY_NEGATIVE |
| ES_FAILED_REBOUND | 3,796 | 13.10 | -13.78 | -15.90 | 0.73 | 41.3% | [-17.97, -9.51] | -30.96 | CLEARLY_NEGATIVE |

MFE/MAE mean bp respectively: EL_PREMIUM_B2 +166.08/-156.49; EL_VOL_MID
+132.01/-124.50; EL_LIMITED_COMBO +125.68/-122.07; ES_BREAKDOWN +106.79/-95.33;
ES_FAILED_REBOUND +102.34/-95.14. Full year and first/second-period tables, monthly frequency,
medians, and cost metadata are in `data/research/crypto/experimental_ls/results_v1.json`.

The two superficially positive LONG means are not acceptable for PAPER: both confidence
intervals include materially negative values and both depend on the largest 5% winners. The
combined relaxation also misses the 2–5/week objective. Both SHORT candidates are negative in
every calendar-year fold and both halves, with confidence intervals wholly below zero.

## Selection and implementation

- EXP LONG: none.
- EXP SHORT: none.
- C1 OFFICIAL: unchanged.
- PAPER origin/routing: unchanged.
- LIVE: unchanged; no EXP order path or display was added.
- UI: unchanged.
- Preview/Production: not created or deployed because no candidate passed.

This follows the requested terminal condition: when both directions are unsuitable, stop before
implementation.
