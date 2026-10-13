# DIRECTION ASYMMETRY R2 : FINAL HOLDOUT report

HOLDOUT opened once. Binance only. LOCAL ONLY. No production change. Nothing committed. bp = 1/100 % of price.

## A. Frozen lineage

R0 TRAIN (contract 4d380ba3…, verdict C) -> R0 VALIDATION (7894bd4e…, B BORDERLINE) -> Execution R1 (ea67b572…,
C ASYMMETRY_FOUND) -> this test, contract `CONTRACT_DIRECTION_R2_FINAL_HOLDOUT_V1.md` sha **ad49b70c…** (before == after).
Primary GB 4 h at 0.60; RF (0.55) and price-only LR (0.60) diagnostic. Single TRAIN fit, taker execution, R0 costs.

## B. HOLDOUT protection proof

`provenance_v1.json` was written in phase 1, before any HOLDOUT file was opened. It records:
- code sha256 for 9 files, the feature-config sha (d457ccec…) and the cost-config sha (9aae27f7…);
- pickled-model shas (GB 0947f0ad…, RF 83be3bb2…, LR 4df640bb…) and the VALIDATION prediction shas;
- VALIDATION parity GB and RF exact (1e-9).

Phase 1 read 1,720 files, latest `BTCUSDT-fundingRate-2025-03`. Phase 2 reused the same fitted objects. Every
pre-HOLDOUT feature row was identical after HOLDOUT data was appended (causality check passed). Phase 2 read 2,306 files,
latest monthly 2026-08 and latest metrics 2026-08-31; nothing from September 2026. The verification rerun (same code and
inputs) produced byte-identical `provenance_v1.json` and `holdout_v1.json`. Tests: 1 passed + 1 skipped in the venv, the
skipped one (Spearman) checked under the system interpreter.

## C. HOLDOUT coverage

2025-04-01 .. 2026-08-31 (label windows ending before 2026-09-01): 12,428 hourly 4 h labels. September 2026 is excluded
(no local Binance funding; contract section 1).

## D. GB direction accuracy

AUC **0.520** [0.506, 0.535], accuracy 0.519, balanced accuracy 0.518, LONG precision 0.519, SHORT precision 0.519,
Brier 0.2541 (worse than a constant 0.25). The contract's predictability check (AUC > 0.52 and CI > 0.5) fails by a hair:
the interval still excludes 0.5, but the effect fell from 0.555 (VALIDATION) to 0.520.

## E. Confidence bucket table (GB, all HOLDOUT hours)

| bucket | N | coverage | accuracy | bal. acc. | mean abs move | gross | gross median | net (95 % CI) | MFE / MAE | LONG share |
|---|---|---|---|---|---|---|---|---|---|---|
| 0.50-0.55 | 5,441 | 43.8 % | 0.513 | 0.513 | 56.7 | +2.0 | +1.7 | -8.0 [-10.1, -5.3] | 62 / 59 | 0.53 |
| 0.55-0.60 | 3,888 | 31.3 % | 0.521 | 0.521 | 59.0 | +2.9 | +2.7 | -7.1 [-10.3, -3.8] | 64 / 65 | 0.61 |
| 0.60-0.65 | 2,303 | 18.5 % | 0.522 | 0.521 | 64.0 | -1.5 | +3.7 | -11.5 [-16.4, -6.8] | 64 / 72 | 0.68 |
| 0.65-0.70 | 624 | 5.0 % | 0.534 | 0.510 | 61.8 | +0.6 | +4.3 | -9.4 [-18.6, -1.0] | 64 / 69 | 0.92 |
| 0.70+ | 172 | 1.4 % | 0.529 | 0.518 | 57.7 | +1.2 | +6.1 | -8.7 [-23.5, +11.2] | 64 / 64 | 0.98 |

All buckets have N >= 100.

## F. Confidence monotonicity

| test (GB) | Spearman | 95 % CI | Holm p | VALIDATION buckets for comparison |
|---|---|---|---|---|
| T1 confidence vs correct | +0.008 | [-0.014, +0.027] | 1.0 | accuracy 0.51, 0.55, 0.59, 0.58, 0.71 |
| T2 confidence vs abs move | +0.060 | [+0.032, +0.087] | **0.002** | abs move 62, 68, 73, 83, 125 bp |
| T3 confidence vs gross | +0.004 | [-0.021, +0.024] | 1.0 | gross -1.0, +1.6, +6.4, +21.7, +70.7 bp |

Ordered buckets: accuracy rises in 3 of 4 steps but only from 51.3 % to 53.4 %; gross shows no trend.

## G. Move magnitude relationship

The only relationship that survives: higher confidence still selects somewhat bigger moves (57 to 64 bp in the middle
buckets; Spearman +0.06). The 0.70+ bucket no longer stands out (58 bp vs 125 bp in VALIDATION).

## H. Gross relationship

Gone. Gross per bucket +2.0, +2.9, -1.5, +0.6, +1.2 bp; no bucket's net is positive.

## I. LONG results (GB >= 0.60)

N 2,312, accuracy 0.522, move when right 60.5 bp, when wrong 68.8 bp, gross -1.3, net -11.4, MFE 65, MAE 73.

## J. SHORT results

N 787, accuracy 0.532, move when right 56.0 bp, when wrong 63.5 bp, gross +0.1, net -9.6, MFE 63, MAE 66.

## K. Asymmetry

LONG minus SHORT gross -1.4 bp [-12.2, +9.1], Holm p 1.0. VALIDATION: LONG +15.9 vs SHORT +0.3. **ASYMMETRY_DISAPPEARS**.
Both sides now have wins about 12 % smaller than losses.

## L. Market drift diagnostic

BTC over the window -4.8 %; 53 % of hours with a positive 30-day return; LONG base rate 50.4 %; always-LONG gross 0.0 bp
per 4 h (VALIDATION +3.1). GB still leans LONG: 61 % of all calls, 75 % of confident calls, 92 to 98 % in the top two
buckets. The VALIDATION LONG advantage matched a strong uptrend; in a flat-to-down period the LONG lean earns nothing.

## M. Taker economics (GB >= 0.60)

N 3,099 (coverage 24.9 %), accuracy 0.525, gross -0.9 [-5.9, +3.7], median +4.2, **net -10.9 [-15.9, -6.2]**, median -5.7,
PF 0.71, win rate 45.8 %, average winner 57.2, average loser 68.4. **NOT_TRADABLE_AS_IS.**

## N. Price-only LR

AUC 0.525 [0.511, 0.540], accuracy 0.523; its confidence is mostly below 0.55 (97 %); at 0.60 only 25 calls (+10.9 bp net,
no CI). GB minus LR: AUC -0.005 [-0.019, +0.009]; net per hour (both at 0.60) -2.7 bp [-4.0, -1.6]. The 20-feature model
is no better than four returns and costs more because it trades more.

## O. RF diagnostic

AUC 0.526 [0.513, 0.541], balanced accuracy 0.522; Spearman accuracy +0.018 (p 0.04 unadjusted), magnitude +0.081, gross
+0.017; at 0.55 N 4,199, gross +1.3, net -8.7 [-13.2, -4.7], LONG/SHORT gross +1.0 / +1.6 (no asymmetry). Not primary
(no swap).

## P. Clustered inference

All intervals are 7-day moving block bootstrap over UTC days (2,000 reps); overlapping 4 h labels are inside day blocks.
Holm over T1..T5: T1 1.0, T2 0.002, T3 1.0, T4 1.0, T5 1.0.

## Q. TRAIN / VALIDATION / HOLDOUT

| GB 4 h | TRAIN (walk-forward OOS) | VALIDATION | HOLDOUT |
|---|---|---|---|
| AUC | 0.553 | 0.555 | **0.520** |
| balanced accuracy | 0.539 | 0.538 | 0.518 |
| confident (0.60) accuracy / coverage | 0.573 / 23.7 % | 0.587 / 19.2 % | 0.525 / 24.9 % |
| confident gross / taker net | +14.0 / about +4 | +9.7 / -0.3 | -0.9 / **-10.9** |
| LONG minus SHORT gross (confident) | | +15.6 | -1.4 |
| top bucket (0.70+) accuracy / gross | | 0.714 / +70.7 (N 49) | 0.529 / +1.2 (N 172) |
| price-only LR AUC | 0.535 | 0.551 | 0.525 |
| BTC drift (always-LONG gross per 4 h) | | +3.1 | 0.0 |

## R. Final gate

| check | result |
|---|---|
| PRED_OK (AUC > 0.52 and CI > 0.5) | no (0.520) |
| T1 accuracy trend | not significant |
| T2 magnitude trend | significant |
| T3 gross trend | not significant |
| T4 asymmetry | not significant (ASYMMETRY_DISAPPEARS) |
| T5 net > 0 | no (-10.9) |
| HIGH_CONFIDENCE_EFFECT_CONFIRMED | no (0.65-0.70 net -9.4, 0.70+ net -8.7) |

## S. Final verdict

**A. DIRECTION_EDGE_REJECTED.** D (asymmetry) not confirmed. NOT_TRADABLE_AS_IS.

In a new period the classifier keeps a faint direction signal (AUC 0.520, accuracy 51.9 %), but the confidence-accuracy and
confidence-gross relationships vanish, the LONG/SHORT asymmetry vanishes with the uptrend, and taker economics are clearly
negative (-10.9 bp per confident call, CI below zero). Only "higher confidence goes with somewhat bigger moves" survives, which
is a volatility statement, not a direction one.

## T. Forward-test implications

The Direction track ends here on historical data: TRAIN, VALIDATION and HOLDOUT have all been used. Any further idea
(LONG-only, a volatility filter, maker entry, another threshold or horizon) is a post-HOLDOUT hypothesis with no independent
historical data left and could only be judged by a forward test started from today. Nothing in this lineage justifies running
one: the out-of-sample edge is about 2 % accuracy above chance, which is smaller than the 10 bp round trip.

## U. Limitations

1. September 2026 (22 days) excluded for lack of local Binance funding.
2. HOLDOUT is 17 months of a single flat-to-down regime; it tests the frozen rule, not every market.
3. Single fit on 2021-2023 data; no refit through 2025 was allowed by the freeze. A refitted model is a different model and
   would need its own forward test.
4. The PRED_OK miss is marginal (0.5199 vs 0.52); the verdict also rests on T1 failing, which is not marginal (Spearman 0.008).

STOP. No further historical tuning.
