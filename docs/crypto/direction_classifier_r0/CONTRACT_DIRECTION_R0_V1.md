# CONTRACT BTC DIRECTION CLASSIFIER R0 V1 (TRAIN only)

Direction only: LONG / SHORT / NO_TRADE. No holding, stop, take-profit, protection, carry or wall rule.
Binance USD-M only. TRAIN 2021-02-01 .. 2023-06-30; VALIDATION and HOLDOUT not read (code guard: the
PTM and Positioning readers refuse any non-TRAIN file). LOCAL ONLY. No server, no download, no API.
Runtime: system Python 3.12.3, numpy 1.26.4, scikit-learn 1.8.0 (already installed; nothing installed).

## 0. History (declared)

Direction from Binance price / flow / OI / positioning was already examined on this TRAIN window:
PTM R1/R2/D1 (flow states), HOT-EDGE, Positioning R0 (no incremental information), and BTC-P2 on the Bybit grid
(AUC < 0.5). This study pools all families in standard classifiers. No feature, model setting, threshold or gate
may change after results.

## 1. Samples and labels

Decision boundaries H: every UTC hour in TRAIN (5 m rows are not used as samples). Entry reference = open of
the 1 m bar at H + 1 min. Label for horizon h (primary 4 h, secondary 1 h): sign of open[entry + h] / open[entry] - 1;
exactly 0 is dropped. Samples whose label window leaves TRAIN are dropped.

## 2. Features (20, six families, fixed; all known at H)

Price minute closes up to the minute ending at H; metrics row stamped H - 5 min (known at H); funding = last
settlement with calc_time <= H; mark / index closes of the minute ending at H.

| family | features |
|---|---|
| A price | log return 1 h, 4 h, 24 h, 72 h |
| B volatility | realized vol of 1 m log returns over 4 h and 24 h; (24 h high - 24 h low) / close |
| C taker flow | (2 x taker_buy_quote - quote) / quote over 1 h, 4 h, 24 h |
| D OI | OI (BTC) change 1 h, 4 h, 24 h |
| E positioning | log G, log TA, log TP, 24 h change of log G |
| F funding / basis | last funding rate; mark / index - 1 at H; mean of mark / index - 1 over the last 4 h |

Missing values stay NaN. TA / TP are blank for most of 2022 (known from Positioning R0).

## 3. Models (3, fixed settings, seed 20261013)

- **LR**: median imputation with missing indicators (fit on the training fold only), standardization,
  L2 logistic regression C = 1.0.
- **RF**: random forest, 300 trees, max_depth 6, min_samples_leaf 200, max_features "sqrt", NaN handled natively.
- **GB**: histogram gradient boosting, 200 iterations, learning_rate 0.05, max_depth 3, min_samples_leaf 200,
  l2_regularization 1.0, NaN native, no early stopping.

## 4. Validation scheme inside TRAIN

Walk-forward, expanding window. Test blocks are 3-month blocks starting 2021-11-01: 2021-11..2022-01,
2022-02..04, 2022-05..07, 2022-08..10, 2022-11..2023-01, 2023-02..04, and a final 2-month block 2023-05..06
(corrected before any fit: the first draft said both "every 3 months" and "last block 2023-04 .. 06"). Training = all samples whose label window ends at least
24 h before the test block starts (purge + 1 day embargo). Out-of-sample (OOS) predictions of all blocks are
pooled. No shuffling.

## 5. Baselines (same OOS hours)

Random 50/50 (expected 0.5), always LONG, always SHORT, and LR trained on one family only: price-only (A),
flow-only (C), OI-only (D). LR on each other single family is reported for the family contribution table.

## 6. Metrics

Balanced accuracy, accuracy, LONG precision, SHORT precision, ROC-AUC, Brier score, reliability (10 bins),
confusion matrix. Confidence thresholds **0.55, 0.60, 0.65** only: predict LONG if p >= t, SHORT if p <= 1 - t,
else NO_TRADE; report coverage, accuracy, LONG accuracy, SHORT accuracy.
Inference: 7-day moving block bootstrap over UTC days (2,000 reps, seed 20261013).

## 7. Feature importance and incremental information

Per model: permutation importance by family on the OOS set (permute all features of a family together within
each test block, 5 repeats, drop in AUC). Incremental OI / positioning: the same model class refitted without
families D and E; paired block-bootstrap AUC difference (full minus reduced). LR coefficients reported.

## 8. Usefulness gate (fixed now)

A (model, horizon) is a TRAIN candidate when all hold on the pooled OOS set:
1. balanced accuracy > 0.52, block-bootstrap 95 % CI lower bound > 0.50, and Holm-adjusted one-sided p <= 0.05
   over the 6 (model, horizon) tests;
2. at one or more of the three thresholds with coverage >= 10 % of OOS hours: accuracy > 0.55 with CI lower bound
   > 0.50;
3. LONG precision > LONG base rate and SHORT precision > SHORT base rate (both, at threshold 0.5);
4. AUC above the price-only and the flow-only LR baselines, both paired block-bootstrap CI lower bounds > 0.

At most 2 candidates (smallest Holm p first). Verdict: **C. DIRECTION_TRAIN_CANDIDATE** if one or more pass;
**B. DIRECTION_WEAK** if any (model, horizon) has balanced-accuracy CI lower bound > 0.50 but fails the gate;
**A. DIRECTION_NO_EDGE** otherwise.

## 9. Leakage tests

Feature timing (no value after H), metrics stamp <= H - 5 min, funding calc_time <= H, label starts after H,
purge / embargo in folds, TRAIN guard, deterministic rerun.

## 10. Output

`data/research/crypto/direction_r0/direction_r0_v1.json` (two runs byte-identical), report
`docs/crypto/direction_classifier_r0/DIRECTION_R0_REPORT.md`. Nothing committed.
