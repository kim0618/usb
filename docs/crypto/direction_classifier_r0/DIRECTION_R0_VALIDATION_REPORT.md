# BTC DIRECTION CLASSIFIER R0 : VALIDATION report

Contract `CONTRACT_DIRECTION_R0_VALIDATION_V1.md` sha **7894bd4e…** (frozen before any VALIDATION row; before == after).
Output `data/research/crypto/direction_r0/validation_v1.json`, two runs **byte-identical** (RF `n_jobs = 1`).
Code `backend/app/crypto/research/direction_r0/{val_data, validation}.py`; tests 9 passed + 1 skipped in the venv
(the parameter test needs sklearn, which only the system interpreter has).
**HOLDOUT not read**: re-listing the read log gives 1,720 files, latest 2025-03-31, none dated 2025-04 or later; the
readers raise on any later file or row (tested). Binance only. LOCAL ONLY. Nothing committed.
(The output field `read_log_last_months` is a string sort of file-name suffixes and is not meaningful; the check above
replaces it.)

## A. Frozen TRAIN candidates

RF 4 h (threshold 0.55) and GB 4 h (threshold 0.60); features, parameters, label, costs as in TRAIN. One fit each
on 21,018 TRAIN hours (labels ending at least 1 day before 2023-07-01).

## B. Validation coverage

2023-07-01 .. 2025-03-31: 15,350 hourly 4 h labels, LONG base rate 51.5 %. Metrics rows 437,179 of 437,760 slots,
0 conflicting.

## C. RF validation (t = 0.55)

Accuracy 0.545, balanced accuracy **0.545** [0.536, 0.554], AUC **0.559** [0.548, 0.570], Brier 0.2474,
LONG precision 0.559, SHORT precision 0.530.
Confusion: LONG called 7,700 (4,306 right), SHORT called 7,650 (4,057 right).

## D. GB validation (t = 0.60)

Accuracy 0.539, balanced accuracy **0.538** [0.529, 0.546], AUC **0.555** [0.544, 0.566], Brier 0.2482,
LONG precision 0.549, SHORT precision 0.527.
Confusion: LONG called 8,385 (4,603 right), SHORT called 6,965 (3,669 right).

## E. Confidence subsets

| model | t | coverage | accuracy (95 % CI) | LONG acc. (calls) | SHORT acc. (calls) |
|---|---|---|---|---|---|
| RF | 0.55 | 32.6 % | **0.574** [0.558, 0.590] | 0.585 (2,724) | 0.560 (2,284) |
| GB | 0.60 | 19.2 % | **0.587** [0.565, 0.609] | 0.611 (1,794) | 0.550 (1,160) |

Other thresholds (report only): GB 0.55 coverage 51.9 % accuracy 0.564; GB 0.65 3.2 % / 0.594; RF 0.60 0.8 % / 0.619.

## F. LONG / SHORT

Accuracy holds on both sides, but the money does not: in the confident subsets LONG calls earn +9.6 (RF) and +15.9 bp
(GB) gross, while SHORT calls earn +0.2 and +0.3 bp gross, i.e. they are right more often than wrong but on smaller
moves. VALIDATION had a LONG base rate of 51.5 % and always-LONG earned +3.1 bp gross per 4 h.

## G. Calibration

GB reliability is close to the diagonal (bin 0.6: mean p 0.630, LONG rate 0.610; bin 0.3: 0.376 vs 0.454, i.e. short
calls are overconfident). RF probabilities stay compressed between 0.4 and 0.6 (as in TRAIN).

## H. Economic diagnostic (4 h, TRAIN cost contract, bp of price)

| | gross mean (95 % CI) | gross median | net mean (95 % CI) | net median | net > 0 | PF | MFE / MAE |
|---|---|---|---|---|---|---|---|
| RF all calls | +2.2 | +6.1 | -7.7 | | 46.7 % | | |
| RF confident | +5.3 [+1.2, +9.6] | +12.1 | **-4.6** [-8.8, -0.3] | +2.2 | 51.4 % | 0.88 | 81 / 86 |
| GB all calls | +1.9 | +5.4 | -8.1 | | 46.3 % | | |
| GB confident | +9.7 [+4.9, +14.9] | +14.1 | **-0.3** [-5.1, +4.9] | +3.9 | 52.6 % | 0.99 | 85 / 82 |

Mean round-trip cost 10.0 bp. Stress costs: RF -8.2, GB -3.8 bp. The confident gross is positive and its CI excludes
zero for both models, but it does not exceed the cost.

## I. Baselines (VALIDATION)

| baseline | AUC | balanced acc. | gross (all calls) | net |
|---|---|---|---|---|
| random / always LONG / always SHORT | 0.500 | 0.500 | +3.1 (LONG) / -3.1 (SHORT) | -7.3 / -12.7 |
| **price-only LR** | **0.551** | **0.543** | +1.8 | -8.2 |
| flow-only LR | 0.530 | 0.523 | +1.1 | -9.0 |
| OI-only LR | 0.493 | 0.497 | -1.6 | -11.8 |

## J. Price-only incremental comparison

| | AUC diff (95 % CI) | balanced acc. diff | gross diff (all calls) |
|---|---|---|---|
| RF minus price-only LR | +0.008 [-0.001, +0.017] | +0.002 | 0.0 |
| GB minus price-only LR | +0.004 [-0.007, +0.015] | -0.005 | 0.0 |

In TRAIN both models beat price-only LR by about 0.02 AUC with CIs above zero; in VALIDATION the gap is within noise.
The four price returns in a linear model carry essentially all of the information the 20-feature models carry.

## K. OI / positioning incremental value

RF full minus without D and E: +0.004 AUC [+0.0005, +0.0077]; GB +0.004 [-0.004, +0.009]. Same size as in TRAIN
(+0.006 / +0.009): small, significant only for RF, and economically nil.

## L. Regime diagnostic (post-hoc, no filter)

| regime | RF N / acc. / AUC | RF confident acc. / gross | GB confident acc. / gross |
|---|---|---|---|
| broad up (30 d) | 9,269 / 0.546 / 0.561 | 0.576 / +5.8 | 0.587 / +9.2 |
| broad down (30 d) | 6,081 / 0.544 / 0.557 | 0.570 / +4.5 | 0.587 / +10.8 |
| high vol | 7,675 / 0.538 / 0.557 | 0.575 / +9.6 | 0.588 / +14.6 |
| low vol | 7,675 / 0.552 / 0.560 | 0.571 / +0.1 | 0.586 / +3.7 |

Accuracy is stable across regimes. Gross per call scales with volatility (the same hit rate on bigger moves). Not
applied as a filter (forbidden; would be a new lineage).

## M. Clustered CI

All intervals are 7-day moving block bootstrap over UTC days (2,000 reps): see C, D, E, H, J, K.

## N. Train vs validation decay

| | TRAIN (walk-forward OOS) | VALIDATION |
|---|---|---|
| RF AUC / balanced acc. | 0.556 / 0.541 | 0.559 / 0.545 |
| RF confident acc. / coverage | 0.569 / 29.4 % | 0.574 / 32.6 % |
| GB AUC / balanced acc. | 0.553 / 0.539 | 0.555 / 0.538 |
| GB confident acc. / coverage | 0.573 / 23.7 % | 0.587 / 19.2 % |
| GB confident gross | +14.0 (post-hoc) | +9.7 |
| price-only LR AUC | 0.535 | **0.551** |

No decay in direction accuracy. The decay is in the advantage over the simple price baseline (which itself improved)
and in gross per confident call (14.0 to 9.7 bp for GB).

## O. Gate

| | V1 AUC > 0.53, CI > 0.5 | V2 bal. > 0.52 | V3 conf. acc. > 0.55 | V4 coverage >= 10 % | V5 conf. net > 0 | V6 LONG and SHORT > 0.5 | V7 beats price-only, CI > 0 | status |
|---|---|---|---|---|---|---|---|---|
| RF | yes | yes | yes | yes | **no** (-4.6) | yes | **no** | BORDERLINE |
| GB | yes | yes | yes | yes | **no** (-0.3) | yes | **no** | BORDERLINE |

FAIL conditions (AUC <= 0.51, confident accuracy <= 0.52, confident gross <= 0) are not met by either model.

## P. Holdout candidates

None (no model PASSes). HOLDOUT stays sealed.

## Q. Limitations

1. Taker cost of about 10 bp per round trip is the binding constraint; the direction signal is worth 5 to 10 bp per
   confident call. Maker execution, a lower fee tier or a longer holding horizon are different studies (new lineage).
2. The useful information is short-term price reversal, which a 4-feature linear model already captures in VALIDATION.
3. The SHORT side is right more often than not but earns about zero gross; the money sits on the LONG side in a
   period with a LONG base rate of 51.5 %. Restricting to LONG is forbidden here and would be a new lineage.
4. Single fit on TRAIN, no refitting through VALIDATION (as frozen).

## R. Final verdict

**B. DIRECTION_VALIDATION_BORDERLINE.**

What survived out of sample: the direction information itself. AUC 0.555 to 0.559 and confident accuracy 57 to 59 % in
2023-07 .. 2025-03, with no decay from TRAIN, in every regime. What did not: (1) the gain over a 4-feature price-only
linear model, which is now inside noise; (2) the economics, since confident calls earn +5 to +10 bp gross against a
10 bp round trip (net -4.6 / -0.3 bp). No HOLDOUT candidate. Any change that might fix the economics (maker fills,
LONG-only, a volatility filter, another horizon or threshold) is a new research lineage, not part of R0.

STOP. HOLDOUT not run.
