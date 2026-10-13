# CONTRACT BTC DIRECTION CLASSIFIER R0 : VALIDATION V1

Parent: `CONTRACT_DIRECTION_R0_V1.md` (sha 4d380ba3…) and `DIRECTION_R0_REPORT.md` (TRAIN verdict C, candidates
RF 4 h and GB 4 h). Written after the TRAIN results, before any VALIDATION row is read.
VALIDATION = 2023-07-01T00:00Z .. 2025-04-01T00:00Z (exclusive). **HOLDOUT (from 2025-04-01) is not read**: the
VALIDATION readers refuse any monthly file 2025-04 or later, any daily metrics file 2025-04-01 or later, and any
row stamped at or after 2025-04-01T00:00Z. Binance only. LOCAL ONLY. Nothing committed.

## 1. Frozen from TRAIN (no change)

Feature set (20 features, 6 families, same code `direction_r0/features.py`), preprocessing (NaN native for RF/GB),
model family and parameters (RF 300 trees, depth 6, leaf 200, sqrt, seed 20261013; GB 200 iterations, lr 0.05,
depth 3, leaf 200, l2 1.0, no early stopping, seed 20261013), no extra calibration (raw predict_proba, as in TRAIN),
4 h label (sign of open[H + 1 min + 240 min] / open[H + 1 min] - 1, zero dropped), thresholds (**RF 0.55** = the only
RF threshold that passed in TRAIN; **GB 0.60** as named in the brief), cost contract of PTM R1 (fee 5 bp/side,
BASE spread 1.184e-6, actual Binance funding; stress 5.5 bp and 2.537e-4 reported).
Only change: RF `n_jobs = 1` for byte determinism (same trees for a fixed seed; meaning and parameters unchanged).

## 2. Fit and predict

One fit per model on all TRAIN hours whose label window ends at least 1 day before 2023-07-01 (purge + embargo,
as in the TRAIN folds). Predict every VALIDATION hour H (2023-07-01 .. 2025-03-31) whose 4 h label window ends
before 2025-04-01. Features near the start of VALIDATION use June 2023 data as lookback (allowed: past data).
Baselines (fit the same way on TRAIN): price-only LR, flow-only LR, OI-only LR, random 50/50, always LONG,
always SHORT. Incremental diagnostic: the same RF / GB refitted without families D and E.

## 3. Metrics

Balanced accuracy, accuracy, AUC, LONG / SHORT precision, confusion matrix, Brier, reliability bins, coverage and
accuracy at the frozen threshold (LONG and SHORT separately), economic diagnostic per call (gross, net, median,
net > 0 share, MFE, MAE over the 4 h window, via `r1_train.simulate` with no stop), all calls and confident calls.
Inference: 7-day moving block bootstrap over UTC days (2,000 reps, seed 20261013) for AUC, balanced accuracy,
confident accuracy, confident gross and net, and AUC minus price-only.

## 4. Gate (frozen now)

Per model, at its threshold t (RF 0.55, GB 0.60):

| id | condition |
|---|---|
| V1 | AUC > 0.53 and its block-bootstrap 95 % CI lower bound > 0.50 |
| V2 | balanced accuracy (t = 0.5) > 0.52 |
| V3 | confident accuracy > 0.55 |
| V4 | confident coverage >= 10 % of VALIDATION hours |
| V5 | confident mean gross > mean round-trip cost, i.e. confident mean net > 0 (BASE costs) |
| V6 | confident LONG accuracy > 0.50 and confident SHORT accuracy > 0.50 |
| V7 | AUC minus price-only LR AUC > 0, block-bootstrap CI lower bound > 0 |

- **PASS**: V1..V7 all hold.
- **FAIL**: AUC <= 0.51, or confident accuracy <= 0.52, or confident mean gross <= 0.
- **BORDERLINE**: neither PASS nor FAIL.

Verdict: **C. DIRECTION_VALIDATION_PASS** if at least one model PASSes (those, max 2, are HOLDOUT candidates);
**B. DIRECTION_VALIDATION_BORDERLINE** if none passes and at least one is BORDERLINE; **A. DIRECTION_VALIDATION_FAIL**
otherwise.

## 5. Post-hoc regime diagnostic (no filter, no gate effect)

Broad up / down: sign of close[H] / close[H - 30 days] - 1. High / low volatility: rv_24h above / below its VALIDATION
median. Per regime: N, accuracy, AUC, confident accuracy, confident gross.

## 6. Forbidden after results

Threshold, feature, parameter, horizon, direction restriction, regime filter. Any of these means a new lineage.

## 7. Output

`data/research/crypto/direction_r0/validation_v1.json` (two runs byte-identical), report
`docs/crypto/direction_classifier_r0/DIRECTION_R0_VALIDATION_REPORT.md`.
