# PTM-R2 TRAIN Report: Recovery, Profit Protection, Leverage Feasibility

Run 2026-10-13 KST, LOCAL only. Output `data/research/crypto/ptm_r2/r2_train_v1.json`. Code
`backend/app/crypto/research/ptm_r1/r2_train.py` (reuses R1 execution/cost/bootstrap) + TRAIN-guarded
basis reader in `data.py`. Tests `backend/tests/crypto/test_ptm_r2_train.py` (15); all PTM tests 92.
Run 11.5 s, executed twice: byte-identical. No server access, no download, no API call; VALIDATION
and HOLDOUT not read; Q123 / Collector / Recorder / crypto-paper untouched; not committed.

Units: underlying returns in bp of entry notional; account results in bp of account equity.

**Verdict:** recovery-filter candidates 2 by the frozen gate (`R1:S8`, `R2:S8`, both "anchor reached
within 4 h of entry"), **flagged as probably mechanical** (section W); profit-protection candidates 0;
leverage profiles descriptive only (no method passed): 5x SAFE, 10x BALANCED, 20x and 50x INFEASIBLE.

## A. Contract sha

`docs/crypto/personal_trading_method_r2/CONTRACT_PTM_R2_V1.md` sha256
`12cacdf3a24dfcb5ceb87362cc284e1d9eae8cae9031cebe32404c7d1da62c58`, frozen before any R2 computation,
re-hashed after the run.

## B. Relation to R1

R1 is closed (F1 FAIL, F2/F3 WEAK, 0 candidates) and its families are not reused. R2 questions were
formed after seeing R1 TRAIN outcomes (giveback, 20x safety, recovery after -0.5 / -1 %), so R2 TRAIN is
discovery and must be re-tested on VALIDATION. R2 is a separate research family with its own Holm
families (Q1 27, Q2 9, Q3 4 tests).

## C. TRAIN coverage

1,267,200 minutes (missing 0); funding 2,640 settlements; mark/index basis NA on 24,485 minutes (1.9 %,
missing mark/index rows; those anchors are excluded from S6 only); parity 31,680 / 0. Files read: 168,
roles `train`, `train_funding`, `parity` only.

Population (4 h grid, direction = completed-day thesis): raw 5,262 (LONG 2,304, SHORT 2,958), dedup
(12:00 UTC) 877, 48 h overlap-adjusted 438. Base P0 (I1 or 48 h, no stop) net mean +9.0 bp, median
-37.7 bp, PF 1.07, median hold 32 h (descriptive).

## D/E/G. Adverse cohorts, recovery rates, time to recovery

| anchor | reached (share of entries) | recovered before I1/48 h (95 % CI) | LONG / SHORT | dedup / overlap | time to recovery median (p95) | further adverse median: recovered / not | ended by I1 unrecovered |
|---|---|---|---|---|---|---|---|
| R0 -0.5 % | 4,560 (86.7 %) | **81.4 %** (79.2 .. 83.5) | 81.6 / 81.4 | 81.6 / 82.6 | 55 min (11.0 h) | 83 / 284 bp | 17.1 % |
| R1 -1.0 % | 3,774 (71.7 %) | **65.1 %** (61.4 .. 68.5) | 64.7 / 65.3 | 64.4 / 67.6 | 130 min (16.3 h) | 153 / 316 bp | 31.4 % |
| R2 -1.5 % | 3,095 (58.8 %) | **53.2 %** (48.8 .. 57.3) | 51.9 / 54.1 | 49.8 / 54.3 | 203 min (19.4 h) | 215 / 352 bp | 40.6 % |

"Further adverse" is measured from the anchor to recovery (or to the end), from entry price.

## F. Recovery vs non-recovery states (difference in recovery rate, group 1 minus group 0)

| split | R0 | R1 | R2 |
|---|---|---|---|
| S1 W1 (4H against) | -1.9 pp | **-8.0** (Holm .024) | **-9.3** (Holm .049) |
| S2 1H against | -1.9 | -0.9 | -5.7 |
| S3 10M against | -3.3 | +0.8 | +1.1 |
| S4 alignment >= 2 | +3.9 | +6.1 | +9.2 |
| S5 funding paying | -4.0 | -6.8 | -8.0 |
| S6 basis favourable | **-6.4** (Holm .010; SHORT only) | **-9.6** (.027; SHORT only) | **-10.2** (.027; SHORT only) |
| S7 thesis age >= 24 h | +1.5 | +6.0 | +7.7 |
| S8 since entry <= 4 h | **+8.0** (Holm <.001) | **+11.6** (<.001) | **+12.6** (<.001) |
| S9 LONG | +0.2 | -0.6 | -2.2 |

Medians at the anchor (recovered / not): adverse duration R1 21 / 40 min, time since entry
206 / 344 min; alignment count 1 / 1 at every anchor.
Reading: 4H against at the anchor lowers recovery by 8 to 9 pp (significant, both directions, clustered
p < .01) but is below the pre-registered 10 pp effect size. Basis is significant but driven by SHORT
only. Only S8 passes every gate (R1, R2).

## H/I/J. Profit-protection cohorts, MFE / giveback, P0..P4

Recovered trades: R0 3,714, R1 2,456, R2 1,645. P4 = P0 under V3 (reported once).

| anchor | policy | net mean / median | MFE after recovery mean / median | giveback mean / median | giveback ratio median | fired | median hold | 20x safety rate | paired net vs P0 (95 % CI), Holm |
|---|---|---|---|---|---|---|---|---|---|
| R0 | P0 | 11.4 / -29.9 | 339 / 201 | 321 / 234 | 1.15 | - | 40 h | 55.7 % | - |
| R0 | P1 1H | -8.0 / -11.7 | 55 / 13 | 54 / 19 | 1.22 | 98.9 % | 4.9 h | 15.4 % | -19.4 (-54.3 .. 13.1), 1.0 |
| R0 | P2 1H+4H | 4.7 / -14.3 | 151 / 46 | 138 / 84 | 1.26 | 93.2 % | 9.0 h | 24.2 % | -6.7 (-37.2 .. 20.3), 1.0 |
| R0 | P3 4H x2 | 1.1 / -34.1 | 263 / 134 | 255 / 181 | 1.26 | 61.1 % | 20.0 h | 41.8 % | -10.3 (-30.5 .. 9.1), 1.0 |
| R1 | P0 | 10.7 / -31.8 | 345 / 209 | 329 / 237 | 1.17 | - | 44 h | 66.4 % | - |
| R1 | P1 | -3.9 / -13.6 | 78 / 24 | 73 / 41 | 1.29 | 98.2 % | 9.3 h | 37.1 % | -14.7 (-52.3 .. 21.6), 1.0 |
| R1 | P2 | 8.8 / -17.8 | 175 / 63 | 160 / 109 | 1.27 | 89.2 % | 14.6 h | 44.3 % | -2.0 (-32.4 .. 26.1), 1.0 |
| R1 | P3 | 4.4 / -37.6 | 283 / 158 | 273 / 197 | 1.26 | 53.6 % | 24.0 h | 57.2 % | -6.4 (-25.6 .. 12.3), 1.0 |
| R2 | P0 | 13.7 / -34.6 | 351 / 217 | 333 / 238 | 1.16 | - | 48 h | 83.3 % | - |
| R2 | P1 | -4.7 / -15.7 | 93 / 37 | 91 / 64 | 1.30 | 97.3 % | 13.0 h | 69.7 % | -18.4 (-62.1 .. 21.1), 1.0 |
| R2 | P2 | 8.0 / -20.1 | 199 / 87 | 185 / 132 | 1.26 | 84.9 % | 19.0 h | 73.1 % | -5.7 (-39.7 .. 24.9), 1.0 |
| R2 | P3 | 3.6 / -39.5 | 304 / 173 | 296 / 218 | 1.25 | 47.1 % | 32.0 h | 79.7 % | -10.2 (-27.9 .. 7.3), 1.0 |

Diagnostics (hypothetical exit at the first occurrence after recovery; not candidates): 10M against
net -9.6 to -12.8, giveback -272 to -286 vs P0; 4H against (W1) +2.7 to +6.5, -213 to -220;
W3 +3.0 to +6.2, -133 to -172. Paired net vs P0 negative in every case.

Answer to Q2-C: every protection state cuts mean giveback by 37 to 286 bp, but **none preserves net**:
P0's positive mean comes from a minority of large winners that the protection exits also cut.
P1 (1H against) sharply lowers the 20x safety-exit rate (55.7 % to 15.4 % at R0) because it exits early.

## K/L/M/N. Leverage (P0, raw population; isolated model, bracket 1, last-trade prices)

| lev | liq / safety distance | SAFETY_ON exits: safety / I1 / max hold | safety+liq rate | profile | R0: recovered before stop / stopped before recovery | R0 reaching P1 / P2 / P3 after recovery | LIQ_ONLY liquidations |
|---|---|---|---|---|---|---|---|
| 5x | 19.6 / 7.84 % | 234 / 3,090 / 1,938 | 4.4 % | **SAFE** | 81.3 / 1.4 % | 80.4 / 75.6 / 49.1 % | 12 (0.2 %) |
| 10x | 9.6 / 3.84 % | 1,186 / 2,483 / 1,593 | 22.5 % | **BALANCED** | 79.3 / 8.6 % | 78.1 / 71.9 / 43.4 % | 124 (2.4 %) |
| 20x | 4.6 / 1.84 % | 2,700 / 1,462 / 1,100 | 51.3 % | INFEASIBLE | 70.8 / 24.5 % | 68.3 / 58.6 / 29.9 % | 879 (16.7 %) |
| 50x | 1.6 / 0.64 % | 4,322 / 521 / 419 | 82.1 % | INFEASIBLE | 21.1 / 78.4 % | 18.6 / 12.6 / 4.1 % | 2,969 (56.4 %) |
| 100x (diag) | 0.6 / 0.24 % | 4,933 / 172 / 157 | 93.7 % | not profiled | 0 / 100 % | 0 % | 4,397 (83.6 %) |

No primary leverage falls in the AGGRESSIVE band (30 to 50 %): 20x is just over 50 %.

## O/P/Q/R. Account level (P0, trade level, bp of equity)

Scenario A FIXED_NOTIONAL (notional = 1.0 x equity) and B FIXED_MARGIN (margin = 10 % of equity).
At 10x both scenarios are the same position (notional 1.0, margin 10 %).

| lev | mode | scenario | notional | mean | median | p5 | worst trade | best trade | max adverse p5 | mean / abs p5 |
|---|---|---|---|---|---|---|---|---|---|---|
| 5x | SAFETY_ON | A | 1.0 | +6.8 | -38.7 | -630 | -819 | +2,714 | -748 | 0.011 |
| 5x | SAFETY_ON | B | 0.5 | +3.4 | -19.4 | -315 | -410 | +1,357 | -374 | 0.011 |
| 10x | SAFETY_ON | A = B | 1.0 | -0.1 | -56.6 | -395 | -420 | +2,714 | -384 | -0.000 |
| 20x | SAFETY_ON | A | 1.0 | -1.8 | -180.6 | -196 | -233 | +2,714 | -184 | -0.009 |
| 20x | SAFETY_ON | B | 2.0 | -3.5 | -361.3 | -393 | -467 | +5,427 | -368 | -0.009 |
| 50x | SAFETY_ON | A | 1.0 | -6.0 | -74.0 | -76 | -98 | +2,714 | -64 | -0.079 |
| 50x | SAFETY_ON | B | 5.0 | -30.0 | -369.9 | -378 | -490 | +13,568 | -320 | -0.079 |
| 5x | LIQ_ONLY | B | 0.5 | +4.2 | -18.8 | -283 | -1,004 | +1,357 | -374 | 0.015 |
| 10x | LIQ_ONLY | A = B | 1.0 | +6.0 | -38.6 | -578 | -1,029 | +2,714 | -748 | 0.010 |
| 20x | LIQ_ONLY | B | 2.0 | -6.6 | -93.9 | -1,012 | -1,061 | +5,427 | -920 | -0.007 |
| 50x | LIQ_ONLY | B | 5.0 | -105.6 | -1,005 | -1,036 | -1,222 | +13,568 | -800 | -0.102 |

Q3 tests (B, SAFETY_ON, mean != 0): 5x +3.4 (CI -13.1 .. 21.7), 10x -0.1 (-30.1 .. 32.3), 20x -3.5
(-47.7 .. 46.7), 50x -30.0 (-92.7 .. 34.4); Holm p = 1.0 for all four. 100x diagnostic: -94.9
(-149.6 .. -32.2).

Reading (no recommendation): with the same margin, higher leverage raises the best trade linearly but
turns the median trade into a near-full safety loss (20x median -361 bp of equity, 50x -370 bp) because
the safety layer, not the thesis, ends most trades. With fixed notional, leverage changes nothing but
where the trade is forced out.

## S. Liquidation / safety exits

SAFETY_ON liquidations (gap through liq): 0 at every leverage. LIQ_ONLY (actual isolated geometry,
no buffer): 0.2 / 2.4 / 16.7 / 56.4 % at 5 / 10 / 20 / 50x. The real account is CROSS margin, where
liquidation depends on wallet equity and comes later than this isolated model.

## T. Clustered inference

Primary 7-day moving-block bootstrap on entry days (2,000, seed 20261013); secondary thesis-episode
cluster bootstrap. All significant Q1 effects keep cluster p < .01 (S1 R1/R2, S6, S8).

## U. Multiple testing

Holm within Q1 (27), Q2 (9), Q3 (4). Q1 survivors (adjusted p < .05): S8 at R0/R1/R2, S6 at R0/R1/R2,
S1 at R1/R2. Q2 and Q3: none (all adjusted p = 1.0). R2 questions were formed after R1 outcomes:
discovery status.

## V. Validation candidates (frozen gate)

| family | candidates |
|---|---|
| Recovery filter | **R1:S8_since_entry_le4h** (+11.6 pp, Holm 1.5e-8), **R2:S8_since_entry_le4h** (+12.6 pp, Holm 2.6e-7). R0:S8 fails the 10 pp effect size (+8.0). S1 W1 fails effect size; S6 fails direction consistency. |
| Profit protection | **none** (all paired net differences negative, Holm 1.0) |
| Leverage profiles | descriptive only, because no method candidate passed: SAFE = 5x, BALANCED = 10x, AGGRESSIVE = none, INFEASIBLE = 20x, 50x |

## W. Limitations

1. **S8 is probably mechanical.** "Anchor reached within 4 h of entry" is trade age, not market
   state. An early drawdown leaves up to about 44 h of the 48 h window (and more time before I1) to
   recover; a late one leaves little. The gate was met as written, but before VALIDATION the user
   should decide whether S8 is tested as is, or with a fixed post-anchor window that removes the
   remaining-time effect. That variant was not pre-registered here and was not computed.
2. The entry population is a 4 h state sample, not the user's own entries; the thesis direction is the
   completed-day flow.
3. Isolated, bracket 1, no liquidation clearance fee, last-trade prices (conservative). Cross margin
   would liquidate later.
4. Account results are per trade, no compounding, no position overlap at the account level.
5. TRAIN only; 2021-02 .. 2023-06.

## X. Final verdict

- Q1: recovery after -0.5 / -1.0 / -1.5 % is common (81 / 65 / 53 %); states explain little. The only
  gate-passing filter (S8) is likely a remaining-time artifact. **2 formal candidates, flagged.**
- Q2: no state-based protection preserves net; giveback falls but winners are cut with it.
  **0 candidates.**
- Q3: 5x SAFE, 10x BALANCED, 20x and 50x INFEASIBLE for a 1D-thesis hold (the safety or liquidation
  layer ends 51 to 82 % of trades before the thesis acts). No leverage shows a positive account mean
  that survives correction. **Profiles descriptive only.**

VALIDATION not run. STOP.
