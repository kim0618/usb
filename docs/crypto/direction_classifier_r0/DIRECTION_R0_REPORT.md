# BTC DIRECTION CLASSIFIER R0 : TRAIN report

Contract `CONTRACT_DIRECTION_R0_V1.md` sha **4d380ba3…** (before == after; one wording fix of the fold blocks
was made before any fit, recorded in the contract). Output `data/research/crypto/direction_r0/direction_r0_v1.json`.
Post-hoc diagnostics (outside the contract, verdict unchanged) `diagnostics_posthoc_v1.json`.
Code `backend/app/crypto/research/direction_r0/` (features, folds, metrics, models, study, diagnostics).
Tests `tests/crypto/test_direction_r0.py` 8 passed (leakage, timing, folds, metrics, guard).
TRAIN only; VALIDATION / HOLDOUT not read. Binance only. LOCAL ONLY. Nothing committed.
Runtime: system Python 3.12.3, numpy 1.26.4, scikit-learn 1.8.0 (already installed, nothing installed).

## Setup

- 21,047 hourly decisions; 7 walk-forward blocks (2021-11 .. 2023-06), purge + 1 day embargo; pooled
  out-of-sample (OOS) n = 14,563 (4 h) / 14,559 (1 h). LONG base rate 49.8 % / 50.2 %.
- 20 features in 6 families; TA / TP missing 36.6 % (2022), others < 3 %.
- Models: LR, RF, GB with the fixed settings of the contract.

## Results, primary horizon 4 h (OOS)

| model | balanced acc. (95 % CI) | AUC (95 % CI) | LONG prec. | SHORT prec. | Brier |
|---|---|---|---|---|---|
| random / always LONG / always SHORT | 0.500 | 0.500 | | | |
| price-only LR | 0.527 | 0.535 | | | 0.2496 |
| flow-only LR | 0.521 | 0.530 | | | 0.2494 |
| OI-only LR | 0.497 | 0.494 | | | 0.2502 |
| **LR (all)** | 0.525 [0.513, 0.535] | 0.531 [0.517, 0.544] | 0.522 | 0.528 | 0.2529 |
| **RF (all)** | **0.541 [0.532, 0.551]** | **0.556 [0.544, 0.568]** | 0.539 | 0.544 | 0.2478 |
| **GB (all)** | **0.539 [0.528, 0.549]** | **0.553 [0.541, 0.565]** | 0.534 | 0.544 | 0.2497 |

Confidence thresholds (4 h):

| model | t | coverage | accuracy (95 % CI) | LONG acc. (calls) | SHORT acc. (calls) |
|---|---|---|---|---|---|
| RF | 0.55 | 29.4 % | 0.569 [0.549, 0.589] | 0.562 (2,634) | 0.580 (1,644) |
| RF | 0.60 | 1.2 % | 0.494 [0.399, 0.607] | 0.488 (168) | 0.625 (8) |
| RF | 0.65 | 0 % | n/a | | |
| GB | 0.55 | 56.0 % | 0.558 [0.545, 0.571] | 0.550 (4,611) | 0.567 (3,549) |
| GB | 0.60 | 23.7 % | 0.573 [0.554, 0.593] | 0.567 (2,069) | 0.581 (1,380) |
| GB | 0.65 | 8.2 % | 0.565 [0.535, 0.598] | 0.558 (708) | 0.576 (484) |
| LR | 0.55 / 0.60 / 0.65 | 45.9 / 17.8 / 8.2 % | 0.531 / 0.538 / 0.545 | | |

Every one of the 7 walk-forward blocks has AUC between 0.538 and 0.588 for RF and GB; none falls to 0.5.

## Secondary horizon 1 h

RF balanced accuracy 0.546 [0.539, 0.552], AUC 0.560; GB 0.538, AUC 0.556; LR 0.534, AUC 0.543. GB at t = 0.60:
coverage 16.9 %, accuracy 0.589 [0.572, 0.605]. Price-only LR AUC 0.535, flow-only 0.539.

## Incremental information

| | RF 4 h | GB 4 h | RF 1 h | GB 1 h |
|---|---|---|---|---|
| AUC minus price-only LR (95 % CI) | +0.021 [0.008, 0.035] | +0.018 [0.002, 0.035] | +0.025 [0.018, 0.031] | +0.021 [0.012, 0.030] |
| AUC minus flow-only LR | +0.026 [0.013, 0.039] | +0.023 [0.008, 0.038] | +0.020 [0.014, 0.027] | +0.017 [0.008, 0.025] |
| AUC full minus without OI + positioning | +0.006 [0.000, 0.012] | +0.009 [-0.001, 0.018] | +0.003 [0.001, 0.006] | +0.002 [-0.004, 0.007] |

Permutation importance (AUC drop when a family is shuffled), 4 h: price 0.043 to 0.047, positioning 0.009 to 0.016,
flow 0.004, funding/basis 0.002 to 0.003, OI 0.001 to 0.004, volatility about 0. **The models are mostly a
nonlinear price model.** OI and positioning add at most about 0.01 AUC, significant only for RF.

## What the models learned (post-hoc)

Price-only LR fitted on all TRAIN: coefficients on the 4 h and 24 h returns are negative (4 h label: -0.075, -0.083;
1 h label: -0.109, -0.050), 72 h slightly positive. The signal is **short-term reversal** over 4 to 24 hours, with
a weak longer-term trend term. This is a different object from the 12 to 48 h trend-following that PTM tested.

## Leakage checks

- Unit tests: no feature changes when data after H is altered; metrics row = H - 5 min; funding calc_time <= H; the
  label starts at H + 1 min; folds purge every training label that ends within 1 day of the test block.
- **Post-hoc placebo:** the same pipeline asked to predict the 4 h return starting 24 h later gives AUC 0.503 (RF and
  GB), balanced accuracy 0.499. A pipeline leak would show here; none does.

## Economic size (post-hoc, gross, no costs; not a strategy)

Mean signed 4 h return of the predicted direction, in bp of price:

| model | all calls | t = 0.55 | t = 0.60 | t = 0.65 |
|---|---|---|---|---|
| GB | +5.6 (n 14,563) | +9.1 (8,160) | **+14.0** (3,449) | +14.4 (1,192) |
| RF | +4.1 | +5.5 (4,278) | -1.0 (176) | n/a |

The average 4 h move is about 80 bp; the classifier is right slightly more often than wrong, which is worth about
5 bp per call on average and 14 bp for GB's confident calls. The Binance round trip used elsewhere is about 10 bp
(5 bp/side taker plus spread and funding). **Gross minus cost is about 0 for all calls and +4 bp for the confident
GB calls.** At 1 h the confident GB calls give +4.8 bp gross: below cost.

## Gate (frozen)

| (model, horizon) | bal. acc. > 0.52, CI > 0.5, Holm <= 0.05 | confident acc. > 0.55, cov >= 10 %, CI > 0.5 | LONG and SHORT precision above base | AUC above price-only and flow-only, CI > 0 | pass |
|---|---|---|---|---|---|
| LR 4 h | yes | no | yes | no | no |
| **RF 4 h** | yes | yes (t 0.55) | yes | yes | **yes** |
| **GB 4 h** | yes | yes (t 0.55, 0.60) | yes | yes | **yes** |
| LR 1 h | yes | yes | yes | no | no |
| RF 1 h | yes | yes | yes | yes | yes |
| GB 1 h | yes | yes | yes | yes | yes |

Holm p is 0.003 for all six (the bootstrap floor: no replicate fell to 0.5), so the tie is broken by contract order
(primary 4 h first, then LR, RF, GB): **validation candidates RF 4 h and GB 4 h** (max 2).

## Output by model (4 h)

MODEL A, RF 4 h: accuracy 54.1 %, LONG precision 53.9 %, SHORT precision 54.4 %; at t 0.55 coverage 29 %,
accuracy 56.9 % (LONG 56.2 %, SHORT 58.0 %); above 0.60 it almost never calls (1.2 %). Importance: price 0.047,
positioning 0.009, flow 0.004.
MODEL B, GB 4 h: accuracy 53.9 %, LONG precision 53.4 %, SHORT 54.4 %; at t 0.60 coverage 24 %, accuracy 57.3 %
(LONG 56.7 %, SHORT 58.1 %); at t 0.65 coverage 8 %, 56.5 %. Importance: price 0.043, positioning 0.016,
flow 0.004, OI 0.004.

## Limitations

1. TRAIN is 2021-02 .. 2023-06 and has been read by eight earlier designs. This is the first design with a positive
   result, which is exactly the situation in which a validation failure is most likely.
2. The economic margin is thin: about +4 bp per confident GB call after a 10 bp round trip, gross of slippage beyond
   the BASE spread. Maker execution would change this materially; taker execution leaves almost nothing.
3. The learned effect is reversal in a period with a large bear market (2022). Reversal effects are regime-dependent.
4. Determinism: two runs agree on every metric and gate; one reliability-bin mean differs in the 16th decimal
   (RF with n_jobs = 4 sums trees in a non-fixed order). Not byte-identical as required; harmless for the verdict.
   A fix is `n_jobs = 1` in a later version.
5. Holm p values sit at the bootstrap floor (2,000 replicates); the true p is smaller, not larger.
6. The confidence thresholds behave differently by model: RF probabilities are compressed (almost nothing above 0.60),
   GB is better calibrated for thresholding.

## Final verdict

**C. DIRECTION_TRAIN_CANDIDATE** by the frozen gate: RF 4 h and GB 4 h.

Read with the economic note: the direction information is real in TRAIN (walk-forward OOS, leak placebo 0.503,
every block above 0.5), mostly short-term price reversal, with OI / positioning adding little. Its size is about
5 bp per call and 14 bp for GB's confident calls against a 10 bp round trip. VALIDATION is the next decision and
the user's to make; nothing was run on it.

STOP.
