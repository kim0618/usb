# DIRECTION EXECUTION R1 (DISCOVERY) : report

TRAIN fit + VALIDATION signals (2023-07-01 .. 2025-03-31). HOLDOUT not read (readers raise on 2025-04+; latest file read
2025-03 klines). Binance only. LOCAL ONLY. No production change. Nothing committed. bp = 1/100 % of price.

## A. Contract

`CONTRACT_DIRECTION_EXECUTION_R1_V1.md` sha **ea67b572…** (before == after). Output
`data/research/crypto/direction_execution_r1/r1_v1.json` (two runs byte-identical) and `aggtrade_diag_v1.json`.
Code `backend/app/crypto/research/direction_execution_r1/` (execution, aggtrade_diag, r1). Tests
`tests/crypto/test_direction_execution_r1.py` 6 passed (taker = R0 simulate, limit prices, fill models, window,
unfilled, fallback, fees, funding from fill, buckets); R0 parity is asserted at run time.

## B. Frozen Direction R0

RF 4 h (threshold 0.55, n_jobs 1), GB 4 h (threshold 0.60), single TRAIN fit, features / label / horizon unchanged.
Price-only LR is a complexity control at 0.55 and 0.60, never a candidate.

## C. Execution data

Binance USD-M BTCUSDT 1 m klines and funding (TRAIN + VALIDATION). Signals: RF 5,008 (2,724 LONG), GB 2,954 (1,794 LONG),
LR 733 at 0.55 and 64 at 0.60. Costs: Binance standard VIP 0, taker 5 bp, maker 2 bp (no account tier on file),
taker spread leg 0.6e-6, actual funding. Exit always taker at the R0 exit time (entry + 4 h).

## D. Taker reproduction

| | signals | accuracy | gross | net | match to R0 |
|---|---|---|---|---|---|
| RF | 5,008 | 0.5737 | +5.3 | -4.6 | exact (1e-9) |
| GB | 2,954 | 0.5870 | +9.7 | -0.3 | exact (1e-9) |

## E. Maker assumptions (frozen)

M1 limit 1 tick passive, M2 limit at the reference price, M3 = M1 then taker at open[e + 5] if unfilled; window 5 min.
Fill on 1 m bars: OPTIMISTIC touch, BASE trade-through by 1 tick, CONSERVATIVE trade-through by 1 bp.

## F. aggTrades fill diagnostic (14 days, 2026-09-09 .. 22, synthetic quotes, no classifier)

| M1 (662 quotes) | fill rate | 4 h move after fill (signed) | 4 h move of unfilled quotes (signed, from reference) |
|---|---|---|---|
| touch | 92.0 % | -1.1 bp | **+12.3 bp** |
| through 1 tick (BASE) | 86.7 % | -1.9 bp | **+12.7 bp** |
| through 1 bp (CONSERVATIVE) | 79.3 % | -2.3 bp | **+9.0 bp** |

Touch without trade-through: 5.7 % of touches (median 0.88 BTC printed at the limit). BASE fills with less than 0.1 BTC
traded through the limit: 0 %. M2 behaves the same with one tick less room. Reading: passive fills at BTC's top of book
are frequent and rarely partial at small size, but **the quotes that do not fill are the ones price ran away from**,
which is classic adverse selection. The 1 m BASE rule shows the same pattern in VALIDATION (section Q), so the
diagnostic does not contradict it. Not extrapolated to 5 years.

## G. RF maker results (VALIDATION, bp)

| variant / fill | fill rate | gross | fee | net per filled | net median | net per signal | missed winners |
|---|---|---|---|---|---|---|---|
| TAKER | 100 % | +5.3 | 10.0 | -4.6 | +2.2 | -4.6 [-8.8, -0.3] | |
| M1 BASE | 90.6 % | +4.2 | 7.0 | -2.7 | +3.7 | -2.4 [-6.1, +1.6] | 66 % |
| M1 CONSERVATIVE | 84.8 % | +4.2 | 7.0 | -2.8 | +3.6 | -2.4 | 63 % |
| M2 BASE | 94.3 % | +4.6 | 7.0 | -2.4 | +4.6 | -2.2 | 65 % |
| M3 BASE | 100 % | +4.1 | 7.3 | -3.1 | +3.5 | -3.1 | |

## H. GB maker results (VALIDATION, bp)

| variant / fill | fill rate | gross | fee | net per filled (95 % CI) | net median | net per signal | missed winners | adverse sel. |
|---|---|---|---|---|---|---|---|---|
| TAKER | 100 % | +9.7 | 10.0 | -0.3 [-5.1, +4.9] | +3.9 | -0.3 | | |
| M1 OPTIMISTIC | 94.6 % | +8.8 | 7.0 | +1.8 | +6.2 | +1.7 | 67 % | -17.2 |
| **M1 BASE** | 91.0 % | +8.1 | 7.0 | **+1.1** [-3.9, +6.5] | +4.7 | +1.0 | 70 % | -18.6 |
| M1 CONSERVATIVE | 85.1 % | +8.0 | 7.0 | +1.0 | +4.7 | +0.8 | 66 % | -11.9 |
| M2 BASE | 94.6 % | +8.8 | 7.0 | +1.8 [-3.0, +7.1] | +6.2 | +1.7 | 67 % | -17.2 |
| M2 CONSERVATIVE | 85.2 % | +8.1 | 7.0 | +1.1 | +4.7 | +0.9 | 66 % | -11.4 |
| M3 BASE | 100 % | +8.6 | 7.3 | +1.3 [-3.5, +6.3] | +4.7 | +1.3 | | |
| M3 CONSERVATIVE | 100 % | +7.8 | 7.5 | +0.3 | +4.2 | +0.3 | | |

Adverse selection = TAKER gross of filled minus unfilled signals. Maker saves 3 bp of fees, but the 5 to 15 % of
signals it misses are mostly winners (66 to 70 %), so about 1.5 of the 3 bp comes back. Stress costs: GB M1 -0.8,
M2 -0.1, M3 -0.8 bp per signal.

## I. LONG payoff (confident calls, TAKER gross, bp)

| | N | accuracy | gross mean / median | MFE / MAE | avg winner / loser | payoff ratio | move right / wrong |
|---|---|---|---|---|---|---|---|
| RF LONG | 2,724 | 0.585 | +9.6 / +15.2 | 86 / 92 | 75 / 83 | 0.91 | 75 / 83 |
| GB LONG | 1,794 | 0.611 | **+15.9** / +17.4 | 87 / 82 | 75 / 76 | 0.98 | 75 / 76 |

## J. SHORT payoff

| | N | accuracy | gross mean / median | MFE / MAE | avg winner / loser | payoff ratio | move right / wrong |
|---|---|---|---|---|---|---|---|
| RF SHORT | 2,284 | 0.560 | +0.2 / +9.1 | 76 / 79 | 63 / 79 | **0.79** | 63 / 79 |
| GB SHORT | 1,160 | 0.550 | +0.3 / +8.3 | 82 / 83 | 68 / 83 | **0.83** | 68 / 83 |

## K. Direction asymmetry: why SHORT is right but earns nothing

SHORT calls are right 55 to 56 % of the time, but when right the 4 h drop averages 63 to 68 bp, and when wrong the rally
averages 79 to 83 bp. The wins are about 17 % smaller than the losses, which cancels the hit rate. LONG calls have a
payoff ratio close to 1 (0.91 to 0.98), so their hit rate turns into money.
This is not model-specific. Price-only LR shows the same pattern on all its calls (LONG +4.6, SHORT -1.4 bp; SHORT payoff
0.85), and so do RF / GB on all calls (SHORT -0.9 / -1.4). VALIDATION had upward drift (LONG base rate 51.5 %,
always-LONG +3.1 bp per 4 h, BTC roughly 30k to 85k): adverse moves against a SHORT are larger than the drops it catches.
Part of the asymmetry is therefore the period's drift and upside skew, not a property of the classifier.

## L. Confidence vs accuracy (all VALIDATION hours, predicted-side probability)

| bucket | GB N / accuracy | RF N / accuracy | LR N / accuracy |
|---|---|---|---|
| 0.50-0.55 | 7,379 / 0.512 | 10,342 / 0.531 | 14,617 / 0.543 |
| 0.55-0.60 | 5,017 / 0.551 | 4,882 / 0.573 | 669 / 0.546 |
| 0.60-0.65 | 2,461 / 0.586 | 126 / 0.619 | 54 / 0.593 |
| 0.65-0.70 | 444 / 0.581 | empty | 10 / 0.600 |
| 0.70+ | 49 / 0.714 | empty | empty |

Accuracy rises with confidence (GB 51 % to 59 %, then 71 % on 49 hours).

## M. Confidence vs magnitude

GB mean |4 h move|: 62, 68, 73, 83, 125 bp across the buckets; RF 63, 74, 82. Higher confidence coincides with bigger
moves, i.e. with higher volatility. The LONG share also rises with confidence (GB 0.52, 0.55, 0.58, 0.77), so high
confidence partly means "LONG in a volatile hour".

## N. Confidence vs gross

GB gross per call: -1.0, +1.6, +6.4, +21.7, +70.7 bp; RF +0.7, +4.4, +38.6. Gross rises faster than accuracy because both
the hit rate and the move size rise. The top two GB buckets hold 493 of 15,350 hours (3 %).

## O. Price-only LR control

Per VALIDATION hour (rule net, 0 when no signal), paired: GB minus LR@0.60 +0.12 to +0.26 bp per hour for M1/M2/M3
(CIs include 0), TAKER -0.12. RF minus LR@0.55 -0.8 to -1.5 bp per hour (RF+TAKER CI below 0). LR@0.60 makes only 64 calls
(+16 to +19 bp net, CI about ±45 bp). The complex models add no execution value over a 4-return linear model that is
statistically visible.

## P. Costs

Taker round trip 10.0 bp, maker entry + taker exit 7.0 bp, M3 mix 7.3 bp. Funding over 4 h averages under 0.5 bp.
Maker fee assumption is the public VIP 0 rate; the account's real tier is not on file.

## Q. Fill-adjusted economics

The best rule, GB + M2 BASE, makes +1.7 bp per signal [-2.8, +6.7]. All GB maker rules lie between +0.3 and +1.8 bp per
signal; all RF rules are negative. Fill-model sensitivity is small for M1 and M2 (BASE to CONSERVATIVE costs about 0.2 bp)
because the miss pattern is the same: missed signals are winners.

## R. Clustered inference

7-day moving block bootstrap over UTC days, paired by timestamp. Every rule's net-per-filled CI contains 0 except
RF + TAKER (negative). LONG-side GB maker nets: M1 +6.4 [+0.6, +13.2], M2 +7.2 [+1.6, +13.7], M3 +7.0 [+1.5, +13.5];
SHORT-side -6.5 to -7.6.

## S. Multiple testing

8 rules (2 models x 4 variants), Holm on the one-sided p of net per filled trade: 1.0 for all.

## T. Economic gate

| rule | failed gates |
|---|---|
| GB + M1 / M2 / M3 | G-F (SHORT side -6.5 to -7.6 bp), G-G (Holm 1.0) |
| GB + TAKER | G-B, G-D, G-E, G-F, G-G |
| RF + any | G-B, G-D, G-E, G-F, G-G |

GB maker rules pass G-A (filled accuracy 0.58), G-B (+1.1 to +1.8), G-C (median +4.7 to +6.2), G-D (positive under BASE and
CONSERVATIVE) and G-E (above LR and random by point estimate) but fail on the SHORT side and on significance.

## U. Final execution candidate

None.

## V. HOLDOUT readiness

Not ready. HOLDOUT stays sealed.

## W. Limitations

1. Discovery on data already opened by R0; every number here is in-sample for the execution choice.
2. 1 m bars cannot see the order book; fill rules are proxies. The 14-day aggTrades diagnostic supports the fill rates and
   the adverse-selection direction but is a different period (2026-09) and small.
3. Maker fee = public VIP 0; a BNB discount or tier would change costs by about 0.5 to 1 bp.
4. The LONG/SHORT asymmetry was noticed in R0 VALIDATION before this contract, so it is a post-hoc question here, and the
   VALIDATION period was a strong uptrend.
5. Overlapping 4 h windows on consecutive hourly signals; handled by day-block inference, not by thinning.

## X. Final verdict

**C. DIRECTION_ASYMMETRY_FOUND.** Maker label (contract rule): **MAKER_POTENTIALLY_VIABLE**, read with care.

- Q1 maker: for GB, maker entry turns -0.3 bp into +1.0 to +1.8 bp per signal, positive under BASE and CONSERVATIVE fill
  but statistically indistinguishable from zero (Holm 1.0), and about half of the fee saving is lost to adverse selection
  (missed signals are 66 to 70 % winners). RF stays negative under every execution.
- Q2 payoff: SHORT calls are right more often than not but their wins are about 17 % smaller than their losses; LONG calls
  carry all the money (GB LONG maker +6.4 to +7.2 bp per trade, CI above 0). The same asymmetry appears in the price-only
  model and coincides with the VALIDATION uptrend.
- Q3 confidence: accuracy, move size and gross all rise with confidence; gross rises fastest because confidence also
  selects volatile, LONG-leaning hours.

LONG-only is not adopted. The next step, if wanted, is a separate **DIRECTION_ASYMMETRY_R2** lineage with its own frozen
contract, whose only confirmation would be the single HOLDOUT run. STOP.
