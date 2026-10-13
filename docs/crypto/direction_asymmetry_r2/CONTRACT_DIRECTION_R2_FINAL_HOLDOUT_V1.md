# CONTRACT DIRECTION ASYMMETRY R2 : FINAL HOLDOUT TEST V1

Lineage: Direction R0 TRAIN (4d380ba3…) -> R0 VALIDATION (7894bd4e…, BORDERLINE) -> Execution R1 (ea67b572…,
ASYMMETRY_FOUND). This contract is written before any HOLDOUT row is read. HOLDOUT is opened once. No rule may change
after the result; a rerun is allowed only as a byte-identical verification with the same code and inputs.
Binance USD-M only. LOCAL ONLY. No production change. Nothing committed.

## 1. HOLDOUT window

2025-04-01T00:00Z .. 2026-09-01T00:00Z (exclusive): every hour H whose 4 h label window ends before 2026-09-01.
**2026-09-01 .. 09-22 is excluded**: Binance funding for September 2026 is not in the local archive, Bybit is
forbidden and downloads are out of scope. The reader refuses any monthly file after 2026-08, any daily metrics file
after 2026-08-31, and any row at or after 2026-09-01T00:00Z.

## 2. Frozen (unchanged)

GB 4 h (primary, threshold 0.60), RF 4 h (diagnostic, threshold 0.55), price-only LR (complexity control, 0.60 and 0.55):
single fit on TRAIN with the 1 day purge, as in R0 VALIDATION (RF n_jobs 1). Features, preprocessing, label, entry at
H + 1 min, exit at entry + 240 min, taker execution with fee 5 bp/side, BASE spread 1.184e-6, actual Binance funding
(`r1_train.simulate`, no stop). No maker, no filter, no new threshold, no primary swap.
Before HOLDOUT is opened the run writes a provenance file with sha256 of: this contract, the code files, the feature
list, the cost constants, the pickled fitted models, and the VALIDATION predictions; the VALIDATION confident results
must match R0 VALIDATION to 1e-9 or the run stops before reading HOLDOUT.

## 3. Primary questions and tests (GB)

Confidence c = max(p, 1 - p) of the predicted side, on every HOLDOUT hour. Fixed buckets [0.50, 0.55), [0.55, 0.60),
[0.60, 0.65), [0.65, 0.70), [0.70, 1]; a bucket with N < 100 is marked INSUFFICIENT (never merged).

- T1 accuracy trend: Spearman(c, correct) > 0.
- T2 magnitude trend: Spearman(c, |4 h move|) > 0.
- T3 gross trend: Spearman(c, signed gross of the predicted side) > 0.
- T4 asymmetry: in the GB >= 0.60 set, mean gross(LONG calls) - mean gross(SHORT calls) > 0.
- T5 economics: in the GB >= 0.60 set, taker mean net > 0.

Each test: 7-day moving block bootstrap over UTC days (2,000 reps, seed 20261013), one-sided p = share of replicates
<= 0 (smoothed); Holm over T1..T5. "Significant" = Holm p <= 0.05.
Predictability check: GB AUC on all HOLDOUT hours > 0.52 with block CI lower bound > 0.50 (PRED_OK).
Ordered-bucket trend reported descriptively (bucket means and number of increasing adjacent pairs).

## 4. Labels and verdict

- Asymmetry: INSUFFICIENT if LONG or SHORT calls < 100; ASYMMETRY_PERSISTS if T4 significant; ASYMMETRY_REVERSES if the
  difference < 0 with block CI upper bound < 0; ASYMMETRY_DISAPPEARS otherwise.
- HIGH_CONFIDENCE_EFFECT_CONFIRMED (diagnostic label): buckets 0.65-0.70 and 0.70+ each N >= 100 and each taker net > 0
  with block CI lower bound > 0. Never a trading rule.
- NOT_TRADABLE_AS_IS if the GB >= 0.60 taker mean net <= 0.

Primary verdict:
- **A. DIRECTION_EDGE_REJECTED** if not PRED_OK, or T1 not significant.
- **C. DIRECTION_HIGH_CONFIDENCE_EFFECT_CONFIRMED** if PRED_OK and T1, T2, T3 and T5 are all significant.
- **B. DIRECTION_EDGE_REAL_BUT_NOT_TRADABLE** otherwise (predictability and the accuracy trend hold, economics do not).
Additional flag: **D. DIRECTION_ASYMMETRY_CONFIRMED** if ASYMMETRY_PERSISTS (may accompany A, B or C).

## 5. Diagnostics (no gate effect)

Market drift: BTC return over the window, share of hours with a positive 30-day return, LONG / SHORT share of GB calls.
Price-only LR and RF: AUC, accuracy, Spearman trends, buckets, gross / net at their thresholds; GB minus LR paired AUC
and paired net per hour (block bootstrap). TRAIN / VALIDATION / HOLDOUT comparison table.

## 6. Output

`data/research/crypto/direction_asymmetry_r2/{provenance_v1.json, holdout_v1.json}` (verification rerun byte-identical),
report `docs/crypto/direction_asymmetry_r2/R2_FINAL_HOLDOUT_REPORT.md`.
