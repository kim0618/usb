# Strategy E-MAX - M3 Breadth / Session Strength Result V1

| | |
|---|---|
| Stage | `E-MAX-M3`, phase B, run once under protocol `7e240a8` (rules `2efbf457…`) |
| Run id | `em3-9aef0165c1f0` |
| Result | `strategy_e_max_m3_result_v1.json`, sha256 `245d013e19bd1bcf…` (byte-identical on a second, independent run) |
| Runtime | `data/runtime/strategy_e_max/m3_runs/em3-9aef0165c1f0/` (gitignored) |
| Evidence label | **E-MAX DEVELOPMENT** (reused development data; not OOS) |
| Exposure status | 1.5x is research-only normalized notional; financing NOT MODELED; not a live leverage approval |
| **Final gate** | **`E-MAX-M3 PASS — B2 SELECTED`**; **`M4 AUTHORIZED`** |

## A. Preflight / protocol freeze

- The start HEAD was `9dc80b1` (M2 result). M0, M1 (R1) and M2 (max 3 carried) are ancestors,
  and every checksum verified.
- The protocol was committed as `7e240a8` before any M3 number existed.
- The E-R3 prechecks were re-run and all passed.
- **B1 reproduces M1 R1 byte for byte** (trades `95d03874…`, daily `9d39d01b…`).
- **Invariants held.**
  - B1 and B2 have the same trade set; only weights differ.
  - Delta is 0 on every 1.0x session.
  - B2 is exactly 1.5 × B1 on every high-breadth session, in every scenario.
  - The funnel is identical: universe 71,119, H5 1,738, selected 501, valid entry 499, valid exit
    480, standard 480.

## B. Comparator B1

B1 is R1 with max 3 at a constant 1.0x. At 10 bp:

| metric | value |
|---|---|
| CAGR | 16.19% |
| cumulative | +33.09% |
| MDD | -15.45% |
| PF | 1.367 |
| session mean | +6.55 bp |

These equal M1 and M2's C1.

## C. B2 definition (frozen in M0)

B2 has the same trades as B1. Its exposure is 1.5x when the universe has at least 100 rows and
`h5_rate` >= 0.030741, and 1.0x otherwise. On a high-breadth session the weights become 1/2,
3/4 or 3/2 for 3, 2 or 1 executable positions. Returns, costs included, scale by 1.5.

## D. Breadth opportunity

| period | sessions | universe >= 100 | high-breadth | high-breadth active | high-breadth standard trades |
|---|---:|---:|---:|---:|---:|
| FULL | 480 | 104 | **26** | 26 | 73 |
| LEGACY_NARROW | 376 | 0 | 0 | 0 | 0 |
| BROAD_COVERAGE | 104 | 104 | 26 | 26 | 73 |

The 26 sessions match M0's structural count. They run from 2026-04-29 to 2026-09-14, and the
exposure map is in the result JSON.

## E. Data-coverage limitation

No legacy session reaches 100 universe rows, so **B2 can only act in the broad-coverage period,
chronological block 4.** The threshold itself was set, structurally and without returns, on those
same 104 sessions. B1 and B2 are identical over the first 376 sessions.

## F. Funnel identity

Every trade-level count is identical between B1 and B2 (see A). Trade-level statistics are
therefore also identical: 480 trades, trade mean +21.43 bp, PF 1.367, win rate 49.4%.

## G. Performance at 10 bp

| metric | B1 | B2 |
|---|---:|---:|
| cumulative | +33.09% | **+40.49%** |
| CAGR | 16.19% | **19.54%** |
| MDD | -15.45% | -15.45% |
| Calmar | 1.05 | 1.26 |
| Sharpe | 0.95 | 1.05 |
| Sortino | 1.48 | 1.70 |
| session mean | +6.55 bp | +7.76 bp |
| PF (trade) | 1.367 | 1.367 |
| win rate (trade) | 49.4% | 49.4% |
| worst session | -7.06% (2026-06-29) | -7.06% (2026-06-29) |
| worst week | -8.38% (2026-W27) | -8.38% (2026-W27) |
| worst month | -8.20% (2025-10) | -8.20% (2025-10) |
| worst quarter | -7.13% (2025Q4) | -7.13% (2025Q4) |
| recovery duration | 278 sessions (2025-03-31..2026-05-08) | 277 sessions (..2026-05-07) |
| longest losing run | 4 sessions | 4 sessions |

MDD and the worst periods do not change, because none of them falls on a high-breadth session.
The deepest drawdown happened in 2025, before the broad-coverage period.

Cost sensitivity, as CAGR:

| cost | 0 bp | 5 bp | 10 bp | 15 bp | 20 bp |
|---|---:|---:|---:|---:|---:|
| B1 | 31.56% | 23.64% | 16.19% | 9.19% | 2.60% |
| B2 | 36.27% | 27.63% | 19.54% | 11.96% | 4.85% |

## H. Eligibility of B2 (M0 gate)

B2 passes every condition:

| condition | observed |
|---|---|
| integrity | PASS |
| coverage | 96.19% |
| session mean | +7.76 bp |
| PF | 1.367 |
| MDD | -15.45% |
| top1 share | HIMS 15.6% |

B1 is also eligible, which bears on M4 authorization.

## I. Improvement test (comparator B1)

| | value | required | result |
|---|---|---|---|
| cumulative delta | **+7.41 pp** | > 0 | PASS |
| paired mean delta | **+1.21 bp** | > 0 | PASS |
| 95% CI of delta | [-0.26, +3.01] bp | - | - |
| P(delta <= 0) | **0.0605** | <= 0.10 | PASS |

## J. Changed-session diagnostics (the sample caveat)

- **Exposure changed on 26 sessions**, all in block 4. The other 454 sessions have delta exactly 0.
- Of the 26, **14 have a positive delta, 12 negative and 0 zero**, which is close to a coin flip.
- The total delta is +580.9 bp-sessions. **Three sessions supply 561 bp of it (96%):**

  | session | delta | driving trade |
  |---|---:|---|
  | 2026-08-04 | +250.8 bp | BLZE +11.6% |
  | 2026-07-28 | +214.1 bp | ITRI +9.6% |
  | 2026-08-13 | +95.7 bp | OMER +6.6% |

- Without the top two sessions, the sum falls to +116 bp. The largest negative is 2026-05-11
  at -140.7 bp.
- **Implication.** The PASS rests on 26 exposure-changed sessions and, within them, on two or three
  right-tail trades. The IID bootstrap resamples 480 sessions, 454 of which are identical zeros, so
  its p-value (0.0605) overstates the information in a 26-session effect. B2 is a
  *leverage-on-block-4* result. It does not show that breadth predicts returns in general. B1's
  mean on the 26 high-breadth sessions was +44.7 bp, and B2 amplifies exactly that.

## K. Risk / reward (M0 stop B)

The CAGR ratio B2/B1 is 1.21 and the MDD ratio is 1.00. **Stop B is not flagged**, because the
drawdown is unchanged.

## L. Concentration (10 bp net contribution)

| | unique symbols | top1 | top5 | top10 | HHI | top 5 symbols |
|---|---:|---|---:|---:|---:|---|
| B1 | 272 | HIMS 18.4% | 69.6% | 121.4% | 0.0099 | HIMS, SOFI, BAND, ABSI, UBER |
| B2 | 272 | HIMS 15.6% | 66.1% | 115.4% | 0.0099 | HIMS, BLZE, ITRI, SOFI, FVRR |

The largest winner (BAND +12.34%, 04-30) and loser (INBX -7.76%, 05-11) are the same trades in
both. BLZE and ITRI move into B2's top five because they sit on high-breadth sessions.

## M. Chronological blocks (10 bp)

| block | trades | B1 mean | B1 compounded | B2 mean | B2 compounded | changed sessions |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 66 | +4.65 bp | +5.39% | +4.65 bp | +5.39% | 0 |
| 2 | 70 | -2.77 bp | -3.82% | -2.77 bp | -3.82% | 0 |
| 3 | 48 | -4.31 bp | -5.46% | -4.31 bp | -5.46% | 0 |
| 4 | 296 | +28.65 bp | +38.88% | +33.49 bp | +46.61% | 26 |

**All of the improvement is in block 4.** Blocks 1-3 are unchanged by construction. Under the E-D6
convention both variants have 2 of 4 positive blocks, and B2 still reads `INCONCLUSIVE —
STATISTICAL` there (CI [-2.55, +18.49] bp). M3's own gate does not use that label.

## N. Reproducibility

Run 1 (723 s) and run 2 (`repeat-2`, separate process) give the same result digest `245d013e…`. `REPEAT_CHECK.json` records `identical` and `artifacts_identical` as true. All 6 artifacts, including the exposure map, are byte-equal, and both runs recorded HEAD `7e240a8`.

## Q. Gate

```text
E-MAX-M3 PASS — B2 SELECTED
M4 AUTHORIZED
```

Under the frozen M0 rules, B2 passes eligibility and improvement and is carried forward:
R1 + max 3, exposure 1.5x on sessions with `h5_rate` >= 0.030741 and at least 100 universe rows.
M4 is authorized because the carried configuration (B2) passes every M0 eligibility condition.

**Interpretation limits.** The result comes from 26 block-4 sessions, is dominated by two or three
trades, reuses development data, and assumes 1.5x notional without financing. It is exploratory
E-MAX DEVELOPMENT evidence, not a validation and not a leverage approval. No new breadth
hypothesis may be added.
