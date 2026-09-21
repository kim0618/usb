# Strategy E-MAX - M2 Capacity Result V1

| | |
|---|---|
| Stage | `E-MAX-M2`, phase B, run once under protocol `cf5a6cb` (rules `aabeddcd…`) |
| Run id | `em2-45dd8e457fce` |
| Result | `strategy_e_max_m2_result_v1.json`, sha256 `d5402bf9d42333c6…` (byte-identical on a second, independent run) |
| Runtime | `data/runtime/strategy_e_max/m2_runs/em2-45dd8e457fce/` (gitignored) |
| Evidence label | **E-MAX DEVELOPMENT** (reused development data; not OOS) |
| Risk status of C2 | RESEARCH ONLY; NOT LIVE AUTHORIZED; NOT COMMON-RISK COMPLIANT |
| **Final gate** | **`E-MAX-M2 NO_USEFUL_ENHANCEMENT`**; C1 (R1 + max 3) carries to M3 |

## A. Preflight / protocol freeze

- The start HEAD was `3485fdd`. That is an unrelated Strategy C commit made by another session
  after M1; it touches no Strategy E file.
- M0 `b05bbf6`, M1 protocol `058217f` and M1 result `d5bdc6b` are ancestors, and their checksums
  and the M1 winner R1 all verified.
- The protocol was committed as `cf5a6cb` before any M2 number existed.
- The E-R3 prechecks were re-run and all passed: dataset, V1 reproduction 70,738 / 1,729,
  attribution 71,119, and PIT poison.
- **C1 reproduces M1 R1 byte for byte** through `capacity.execute`: trades `95d03874…`, daily
  `9d39d01b…`.
- Invariants held: same H5 and universe for both variants, C1 <= 3, C2 <= 5, C2's selection
  extends C1's R1 prefix, and sessions with at most 3 candidates are identical.

## B. Comparator: R1 + max 3 (C1)

At 10 bp:

| metric | value |
|---|---|
| trades | 480 |
| session mean | +6.55 bp |
| PF | 1.367 |
| cumulative | +33.09% |
| CAGR | 16.19% |
| MDD | -15.45% |

These equal M1.

## C. Max 5 (C2)

C2 uses the R1 order (rvol descending, tie symbol ascending) and selects the first 5, with no
replacement. Normalized session gross exposure stays 1.0: 1/n per executable position, with n
up to 5. The only difference from C1 is how many symbols share the same capital.

## D. Capacity opportunity

| period | sessions with H5 | >= 4 candidates | >= 5 candidates | selection changed | added selected positions | added standard trades |
|---|---:|---:|---:|---:|---:|---:|
| FULL | 239 | 101 | 98 | 101 | 199 | 183 |
| LEGACY_NARROW | 138 | 4 | 3 | 4 | 7 | 7 |
| BROAD_COVERAGE | 101 | 97 | 95 | 97 | 192 | 176 |

## E. Funnel

| | universe | H5 | selected | capacity skipped | valid entry | ENTRY_INVALID | valid exit | UNRESOLVED | standard PnL | coverage |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| C1 | 71,119 | 1,738 | 501 | 1,237 | 499 | 2 (shortened) | 480 | 19 | 480 | 96.19% |
| C2 | 71,119 | 1,738 | **700** | 1,038 | 697 | 3 (1 missing 09:30, 2 shortened) | 663 | 34 | **663** | 95.12% |

C2 adds 199 selected positions and 183 standard trades. One added pick had no 09:30 bar. Its slot
stayed empty, and candidate #6 was not promoted.

## F. Performance at 10 bp

| metric | R1 max 3 (C1) | R1 max 5 (C2) |
|---|---:|---:|
| trades | 480 | 663 |
| active sessions | 237 | 237 |
| trade mean | +21.43 bp | +9.27 bp |
| session mean | +6.55 bp | +2.07 bp |
| active-session mean | +13.28 bp | +4.20 bp |
| median trade | -2.42 bp | -2.99 bp |
| PF | 1.367 | 1.150 |
| win rate | 49.4% | 49.2% |
| cumulative | +33.09% | +8.17% |
| CAGR | 16.19% | 4.21% |
| MDD | -15.45% | -15.16% |
| Calmar | 1.05 | 0.28 |
| Sharpe | 0.95 | 0.35 |
| Sortino | 1.48 | 0.51 |

Cost sensitivity, as session mean / CAGR:

| cost | C1 | C2 |
|---|---|---|
| 0 bp | +11.49 / 31.56% | +7.01 / 18.00% |
| 5 bp | +9.02 / 23.64% | +4.54 / 10.89% |
| 10 bp | +6.55 / 16.19% | +2.07 / 4.21% |
| 15 bp | +4.09 / 9.19% | -0.40 / -2.08% |
| 20 bp | +1.62 / 2.60% | -2.87 / -7.99% |

## G. Eligibility of C2 (M0 gate)

| condition | C2 | result |
|---|---|---|
| integrity | PASS | yes |
| coverage >= 95% | 95.12% | yes |
| session mean > 0 | +2.07 bp | yes |
| PF > 1 | 1.150 | yes |
| MDD >= -35% | -15.16% | yes |
| top1 share <= 73.38% | HIMS 54.6% | yes |

C2 is **eligible**.

## H. Improvement test (comparator C1)

| | value | required | result |
|---|---|---|---|
| cumulative delta (C2 − C1) | **-24.92 pp** | > 0 | FAIL |
| paired mean delta | **-4.48 bp** | > 0 | FAIL |
| 95% CI of delta | [-8.27, -0.88] bp | - | - |
| P(delta <= 0) | **0.994** | <= 0.10 | FAIL |

The bootstrap is IID over 480 sessions, 10,000 replicates, seed 20260921. Max 5 is worse than
max 3, and the 95% interval of the difference lies entirely below zero.

## I. Risk / reward (M0 stop B)

- CAGR ratio C2/C1 = **0.26**.
- MDD ratio |C2|/|C1| = **0.98**.

The drawdown barely improves while return falls by three quarters. Stop B is flagged, with the
MDD ratio above the CAGR ratio. It is diagnostic here, and C2 has already failed the improvement
gate.

## J. Concentration

| | unique symbols | top1 | top5 | top10 | HHI | largest winner | largest loser |
|---|---:|---|---:|---:|---:|---|---|
| C1 | 272 | HIMS 18.4% | 69.6% | 121.4% | 0.0099 | BAND +12.34% (04-30) | INBX -7.76% (05-11) |
| C2 | 389 | HIMS 54.6% | 191.8% | 296.4% | 0.0066 | BAND +12.34% (04-30) | BTDR -9.93% (08-10) |

By trade count (HHI), C2 is more spread out. But its *net contribution* is more concentrated,
because its total shrinks: top1 rises from 18% to 55%. Spreading across more symbols did not
spread the profit. It diluted it.

## K. Narrow versus broad coverage (diagnostic)

| | period | trades | session mean 10 bp | cumulative | PF | paired delta vs C1 |
|---|---|---:|---:|---:|---:|---:|
| C1 | LEGACY_NARROW | 199 | -0.35 bp | -2.69% | 1.022 | - |
| C1 | BROAD | 281 | +31.53 bp | +36.77% | 1.576 | - |
| C2 | LEGACY_NARROW | 206 | -0.54 bp | -3.36% | 1.014 | -0.19 bp |
| C2 | BROAD | 457 | +11.53 bp | +11.93% | 1.199 | **-20.00 bp** |

## L. Chronological blocks (10 bp)

| block | C1 trades | C1 mean | C1 compounded | C2 trades | C2 mean | C2 compounded |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 66 | +4.65 bp | +5.39% | 70 | +5.20 bp | +6.09% |
| 2 | 70 | -2.77 bp | -3.82% | 71 | -3.03 bp | -4.12% |
| 3 | 48 | -4.31 bp | -5.46% | 48 | -4.31 bp | -5.46% |
| 4 | 296 | +28.65 bp | +38.88% | 474 | +10.44 bp | +12.48% |

The whole capacity effect sits in block 4, the broad period, as the coverage limitation
predicted. There it is strongly negative: the 4th and 5th R1 candidates earn far less than the
top 3.

## M. Reproducibility

Run 1 (777 s) and run 2 (`repeat-2`, separate process) give the same result digest `d5402bf9…`; `REPEAT_CHECK.json` identical = true, artifacts_identical = true; all 5 artifacts byte-equal; both recorded HEAD `cf5a6cb`.

## N. Final gate

```text
E-MAX-M2 NO_USEFUL_ENHANCEMENT
```

Under R1 ordering, adding the 4th and 5th candidates at the same total exposure lowers return
without meaningfully lowering drawdown. **C1 (R1 + max 3) carries forward to M3.** No other
capacity is tested: max 4, max 6+ and dynamic capacity stay forbidden.
