# CONTRACT DIRECTION EXECUTION R1 V1 (DISCOVERY; TRAIN + VALIDATION; HOLDOUT sealed)

Parent: Direction Classifier R0 (`CONTRACT_DIRECTION_R0_V1.md` 4d380ba3…, `CONTRACT_DIRECTION_R0_VALIDATION_V1.md`
7894bd4e…, verdict B BORDERLINE). This is a DISCOVERY study on data already opened by R0. Its single surviving
execution rule, if any, is confirmed only by one HOLDOUT run after user approval; any change after HOLDOUT = FAIL.
Binance USD-M only. LOCAL ONLY. No production change, no order, no commit.

## 1. Frozen (unchanged from R0)

Features, label, 4 h horizon, RF / GB parameters, preprocessing, probability (raw predict_proba), thresholds
RF 0.55 and GB 0.60, single fit on TRAIN with the 1 day purge, RF n_jobs = 1. Signals = VALIDATION hours where the
model's probability passes its threshold (2023-07-01 .. 2025-03-31). No new classifier, feature or threshold.
Price-only LR (fit the same way) is a complexity control only, evaluated at the GB threshold 0.60 and at RF's 0.55.

## 2. Questions (only these)

Q1 maker execution after costs; Q2 LONG vs SHORT payoff structure; Q3 confidence vs accuracy, move magnitude and gross.

## 3. Costs (Binance USD-M standard schedule, VIP 0; no account tier on file)

Taker 5.0 bp per side (equals the measured rate used since PTM R1), maker 2.0 bp per side, BASE spread 1.184e-6 per
round trip charged on taker legs only (half per leg), actual Binance funding settlements between fill and exit
(as `r1_train.simulate`). Stress: taker 5.5 bp, maker 2.2 bp, spread 2.537e-4 on taker legs. No fee is lowered.

## 4. Execution variants (entry only; exit is unchanged)

Reference minute e = H + 1 min (the R0 entry). Exit for every variant: taker at open[e + 240] (the R0 exit time;
the horizon is not changed). Tick = 0.1 USDT. Order attempt window W = 5 minutes (minutes e .. e + 4), fixed.

- TAKER: buy / sell at open[e] (R0 reproduction).
- M1 best-bid / best-ask passive: LONG limit = open[e] - 1 tick, SHORT limit = open[e] + 1 tick.
- M2 join the reference price: LONG / SHORT limit = open[e].
- M3 maker then taker fallback: M1 for W; if not filled, taker at open[e + W].

Fill models on 1 m bars (frozen; a LONG limit L, SHORT mirrored with highs):
- OPTIMISTIC: filled at L in the first window minute whose low <= L.
- BASE: filled at L in the first window minute whose low <= L - 1 tick (traded through).
- CONSERVATIVE: filled at L in the first window minute whose low <= L x (1 - 1 bp).
A 1 m bar cannot show partial fills; partial-fill risk is measured only in the aggTrades diagnostic (section 5).
Unfilled maker signals produce no trade (net 0 per signal; counted as missed).

## 5. aggTrades microstructure diagnostic (14 days, explicitly allowed by the brief)

Binance aggTrades BTCUSDT 2026-09-09 .. 2026-09-22 (`runtime/crypto/BTCUSDT/historical/aggtrades_daily`). This window
lies in the HOLDOUT period, so **no classifier is applied there**: quotes are synthetic at every UTC hour and both
directions. Measured: share of windows touched, traded through by 1 tick, traded through by 1 bp; for touched-only
windows the volume traded exactly at L (queue proxy); volume traded through L versus an order size of 0.1 BTC
(partial-fill proxy); adverse selection = signed 5 min and 4 h move after the fill time versus the unfilled windows;
agreement of the three 1 m fill rules with trade-level truth (bars built from the trades). Not extrapolated to 5 years.

## 6. Metrics

Per model (RF, GB, LR control) x variant x fill model: signals, filled, fill rate, unfilled, gross, fees, net, net per
signal, net per filled trade, net median, missed-winner rate and missed-loser rate (sign of the TAKER gross of the
unfilled signals), adverse selection (TAKER gross of filled minus unfilled signals; and filled maker gross minus the
same signals' TAKER gross).
Q2: per direction: accuracy, gross mean / median, MFE, MAE, average winner and loser, payoff ratio; move magnitude
when right and when wrong; compared with price-only LR and with the VALIDATION drift (always-LONG gross).
Q3: buckets of the predicted-side probability max(p, 1 - p): [0.50, 0.55), [0.55, 0.60), [0.60, 0.65), [0.65, 0.70),
[0.70, 1]; per bucket N, accuracy, gross mean / median, mean |4 h move|, MFE, MAE, average winner / loser (TAKER).
Random-direction baseline: the same signal timestamps with a coin-flip direction (expected value = mean of LONG and
SHORT outcomes at each timestamp).
Inference: 7-day moving block bootstrap over UTC days (2,000 reps, seed 20261013), paired by timestamp.

## 7. Economic gate (frozen)

Candidates = 2 models x {TAKER, M1, M2, M3} = 8 rules, evaluated under the BASE fill model:
- G-A confident accuracy of the filled trades > 0.55;
- G-B mean net per filled trade > 0;
- G-C median net per filled trade >= -5 bp;
- G-D net per signal > 0 under BASE **and** CONSERVATIVE fill;
- G-E paired net per signal above price-only LR with the same variant and above the random-direction baseline;
- G-F the rule as a whole (both directions) has mean net > 0 and neither direction's mean net per filled trade is
  below -5 bp;
- G-G block-bootstrap one-sided p for mean net per filled trade > 0, Holm-adjusted over the 8 rules, <= 0.05.

**D. DIRECTION_EXECUTION_READY_FOR_HOLDOUT**: at least one rule passes all; the one with the smallest Holm p is the
single candidate. Otherwise **C. DIRECTION_ASYMMETRY_FOUND** if, for some rule under BASE, the LONG side has mean net
per filled trade > 0 with block CI lower bound > 0 while the SHORT side's mean net <= 0. Otherwise
**B. DIRECTION_EXECUTION_WEAK** if some rule has mean net per filled trade > 0 under BASE. Otherwise
**A. DIRECTION_EDGE_NOT_TRADABLE**.
Maker label: MAKER_POTENTIALLY_VIABLE if a maker rule (M1..M3) is net-positive per signal under BASE and CONSERVATIVE;
MAKER_DATA_INSUFFICIENT if positive only under OPTIMISTIC or if the aggTrades diagnostic contradicts the BASE rule;
MAKER_NOT_ENOUGH otherwise. LONG-only is never adopted here.

## 8. Reproduction stop

The TAKER rows must reproduce R0 VALIDATION (confident N, accuracy, gross mean, net mean) to 1e-9; otherwise STOP.

## 9. Output

`data/research/crypto/direction_execution_r1/r1_v1.json` (two runs byte-identical) and
`docs/crypto/direction_execution_r1/R1_REPORT.md`.
