# POSITIONING DISCOVERY R0 : TRAIN report

TRAIN only (2021-02-01 .. 2023-06-30). VALIDATION / HOLDOUT not read. LOCAL ONLY, no server, no download,
no API, no Q123, no order. Nothing committed. bp = 1/100 % of entry price per trade, unlevered.

## A. Contract

`CONTRACT_POSITIONING_R0_V1.md`: pre-registration sha 77397bc8… (before any data), after the section 12
CANDIDATE FREEZE sha **d4862941…** (Stage 1 before == after). Outputs `data/research/crypto/positioning_r0/`
`stage0_v1.json` (states only, outcome-key scanner passed; rerun identical apart from the contract sha field)
and `stage1_v1.json` (two runs byte-identical). Code `backend/app/crypto/research/positioning_r0/`
(data, states, stage0, stage1). Tests `tests/crypto/test_positioning_r0.py` 13 passed.

## B. Binance source lineage

| input | venue | file | timestamp semantics |
|---|---|---|---|
| OI, G, TA, TP (taker ratio reported only) | Binance USD-M | `d5_2/archive/binance/futures/um/daily/metrics/BTCUSDT` (CHECKSUM verified) | label T, used as known at T + 5 min |
| price (entry, exit, high, low, MFE, MAE) | Binance USD-M 1 m | PTM reader, monthly klines | bar close |
| flow (comparison only) | Binance taker_buy_quote | same | minute close |
| funding | Binance | monthly fundingRate | settlement |
| fee | Binance 5 bp/side (measured) + BASE 1.184e-6 | PTM R1 | |

No Bybit file is opened (test enforced). Entry = open of the 1 m bar at H + 1 min; positioning row stamped
H - 5 min, so the latest value used is at least 6 minutes old at entry.

## C. Data quality (TRAIN)

| | value |
|---|---|
| expected 5 m slots | 253,440 |
| unique rows / missing slots | 252,989 / 451 (all in 2021) |
| identical duplicates / conflicting | 31,241 removed / 0 |
| OI undefined | 131 slots |
| G undefined | 5,759 slots |
| **TA and TP undefined** | **92,153 slots (36 %): almost all of 2022-01 .. 2022-12 is blank in the archive** |
| longest gap | 63 slots (5 h 15 m), 2021-02-19 |
| OI outliers | min 2,772 BTC vs p1 28,204: isolated bad prints, not cleaned (contract: positive values only) |

## D. OI distributions (hourly, %)

| window | p1 | p5 | median | p95 | p99 |
|---|---|---|---|---|---|
| O1 5 m | -0.78 | -0.31 | +0.01 | +0.29 | +0.60 |
| O2 1 h | -3.13 | -1.51 | +0.05 | +1.45 | +2.90 |
| O3 4 h | -6.49 | -3.39 | +0.10 | +3.55 | +6.33 |
| O4 24 h | -12.5 | -6.93 | +0.28 | +8.08 | +13.1 |

## E. Positioning distributions and states

| ratio (5 m) | p1 | median | p99 | LONG share (hourly) | SHORT share | flips / day | median run |
|---|---|---|---|---|---|---|---|
| G global account | 0.71 | 1.69 | 4.20 | 82.6 % | 15.1 % | 0.30 | 9.5 h |
| TA top account | 0.73 | 1.64 | 4.11 | 55.3 % | 8.2 % (36 % undefined) | 0.20 | 6 h |
| TP top position | **1.009** | 1.15 | 1.44 | 63.3 % | **0.25 %** | 0.04 | 4.5 h |

TP is above 1 in practically every defined slot: "top-trader position SHORT" almost never happens. Without
a dead band (forbidden in R0), TP is close to a constant. G/TA/TP 8-state: GL_TAL_TPL 53.6 %, GS_TAS_TPL 7.6 %,
GS_TAL_TPL 1.5 %, every TP-short state < 0.3 %, undefined 36.5 %.

## F. Price / OI regimes (hourly)

A (up + OI up) 26.9 %, B (up + OI down) 23.3 %, C (down + OI up) 26.3 %, D (down + OI down) 23.1 %, undefined 0.4 %.
About 4.2 to 4.7 onsets per day each; median run 1 h, p90 2 h. The transition matrix is close to uniform
(every row 0.19 .. 0.31): the hour's price/OI state says almost nothing about the next hour's state. Mirror
pairs (A vs C, B vs D) are symmetric in frequency and persistence.

## G. Trader divergence

D1 G vs TP disagree: 1,986 of 13,414 defined hours, but 1,940 are "G short, TP long" and only 46 "G long, TP short".
D2 TA vs TP: 1,776 of 13,413, of which 1,724 "TA short, TP long" and 52 the reverse. Both divergences are one-sided
by construction (TP almost always > 1).

## H. OI / flow alignment (hourly)

Flow BUY + OI up 5,532; BUY + OI down 4,603; SELL + OI up 5,715; SELL + OI down 5,198 (21,048 defined). The 60 m
flow sign agrees with the 1 h price sign 80 % of the time.

## I. Candidate generation (rule fixed before Stage 0)

| candidate | raw onsets | 4 h dedup | 48 h N | viability | selected |
|---|---|---|---|---|---|
| P1 NEW_POSITIONS_WITH_MOVE (A -> LONG, C -> SHORT) | 8,174 | 5,854 | 424 | LIMITED | yes |
| P2 TOP_VS_CROWD (G vs TP, follow TP) | 113 | 107 (92 long) | 51 | TOO_RARE | no |
| P3 FLOW_WITHOUT_OI (flow with OI down, fade the flow) | 7,320 | 5,419 | 422 | LIMITED | yes |
| P4 TOP_ACCOUNT_VS_POSITION (TA vs TP, follow TP) | 104 | 93 (78 long) | 46 | TOO_RARE | no |

Frozen: P1, P3. The two pure trader-ratio candidates are too rare because TP is almost never below 1 and
TA/TP are blank for most of 2022.

## J. Sample sizes

P1: 5,854 events (2,951 LONG / 2,903 SHORT), 48 h N 424. P3: 5,419 (2,794 / 2,625), 48 h N 422.

## K. Candidate 1: P1 NEW_POSITIONS_WITH_MOVE

| horizon | N | gross | net | median | net > 0 | PF | MFE | MAE | CI95 (net) |
|---|---|---|---|---|---|---|---|---|---|
| 15 m | 5,854 | +0.8 | -9.2 | -10.9 | 30 % | 0.47 | 27 | 23 | [-10.1, -8.2] |
| **1 h** | 5,853 | +0.2 | **-9.8** | -12.0 | 38 % | 0.65 | 52 | 48 | [-11.6, -8.0] |
| **4 h** | 5,852 | +0.1 | **-9.9** | -11.9 | 43 % | 0.81 | 105 | 102 | [-12.6, -7.2] |
| 12 h | 5,851 | -0.2 | -10.3 | -12.9 | 46 % | 0.88 | 187 | 185 | [-13.6, -6.9] |
| 24 h | 5,848 | +0.4 | -9.6 | -12.3 | 47 % | 0.93 | 268 | 267 | [-14.3, -4.3] |

Gross is between -0.2 and +0.8 bp at every horizon; the net is the cost (about 10 bp).

## L. Candidate 2: P3 FLOW_WITHOUT_OI

| horizon | N | gross | net | median | net > 0 | PF | MFE | MAE | CI95 (net) |
|---|---|---|---|---|---|---|---|---|---|
| 15 m | 5,419 | -0.3 | -10.3 | -8.8 | 34 % | 0.42 | 25 | 27 | [-11.3, -9.4] |
| **1 h** | 5,419 | -0.4 | **-10.4** | -8.3 | 41 % | 0.63 | 48 | 52 | [-12.1, -8.7] |
| **4 h** | 5,418 | -0.4 | **-10.5** | -7.7 | 45 % | 0.80 | 101 | 106 | [-13.3, -7.8] |
| 12 h | 5,416 | -2.4 | -12.6 | -11.2 | 47 % | 0.86 | 180 | 187 | [-16.1, -9.0] |
| 24 h | 5,413 | -1.7 | -12.1 | -8.3 | 48 % | 0.91 | 263 | 269 | [-17.4, -7.0] |

## M. Candidate 3

None (only two candidates passed the Stage 0 rule).

## N. Controls (primary horizons, net bp)

| control | P1 1 h | P1 4 h | P3 1 h | P3 4 h |
|---|---|---|---|---|
| candidate | -9.8 | -9.9 | -10.4 | -10.5 |
| random hour, same direction (mean of 200) / share >= observed | -10.0 / 0.41 | -10.2 / 0.43 | -10.1 / 0.68 | -10.0 / 0.60 |
| time shift +1 d / +3 d | -11.6 / -12.2 | -12.5 / -10.3 | -8.7 / -9.6 | -9.2 / -9.7 |
| opposite direction / paired diff (p) | -10.2 / +0.4 (0.84) | -10.1 / +0.2 (0.96) | -9.6 / -0.9 (0.62) | -9.5 / -1.0 (0.72) |
| price-only matched | -9.4 | -9.7 | -11.0 | -9.3 |
| flow-only matched | -10.4 | -10.0 | -11.4 | -10.6 |

Every control lands at the cost line. Trading the opposite direction gives the same result.

## O. Incremental information (candidate minus matched control, paired, bp)

| | vs price-only: mean, CI95, p | vs flow-only: mean, CI95, p |
|---|---|---|
| P1 1 h | -0.4 [-2.3, +1.5] 0.68 | +0.6 [-1.3, +2.4] 0.56 |
| P1 4 h | -0.2 [-3.2, +2.7] 0.87 | +0.1 [-2.7, +3.2] 0.97 |
| P3 1 h | +0.5 [-1.2, +2.2] 0.57 | +1.0 [-0.8, +2.8] 0.27 |
| P3 4 h | -1.2 [-4.4, +2.1] 0.47 | +0.1 [-3.2, +3.4] 0.97 |

Adding OI to the price move or to the taker flow changes the next 1 h / 4 h by less than 1.2 bp, and the
confidence intervals are about ±3 bp around zero. Positioning carries no incremental information here.

## P. LONG / SHORT

P1 1 h: LONG -10.3, SHORT -9.4; 4 h: -10.0 / -9.9. P3 1 h: -10.8 / -10.0; 4 h: -10.8 / -10.2. Incremental by
direction within ±2.8 bp. Nothing is hidden in one direction.

## Q. Costs

Base round trip about 10 bp (fee 5 bp/side plus funding). Stress (5.5 bp/side, 2.5 bp spread): P1 -13.3 / -13.5,
P3 -14.0 / -14.0. Break-even cost equals the gross, which is 0 to 1 bp.

## R. Clustered inference

7-day block bootstrap and 48 h cluster bootstrap agree (all p < 0.001 for the negative net mean, which is the
cost). The 48 h overlap-adjusted subsets (N 424 / 422) give nets of -7.8 to -12.4 bp at the primary horizons.

## S. Multiple testing

4 primary tests (2 candidates x 2 horizons). Holm on the net mean: all < 1e-12 (negative side, i.e. the cost).
Holm on the incremental test: 1.0 for all four.

## T. Validation gate (frozen in the contract)

Every (candidate, horizon) fails mean net > 0, PF > 1, the Holm gate and the LONG/SHORT balance gate; P1 4 h and
P3 4 h also fail the tail gate (2.9 % and 3.1 % of events reach a 3.84 % adverse move within 4 h). Only P3 1 h has
both incremental point estimates above zero (+0.5 / +1.0), with p 0.57 / 0.27. The WEAK rule (p <= 0.10) is not met.

## U. Limitations

- TA and TP are blank for nearly all of 2022 in TRAIN; P2 / P4 were not testable as designed, and TP without a
  dead band is almost constant. A dead band or a ratio-change definition would be a new pre-registration.
- Hourly decision grid, fixed horizons, no stops: this is an information test, not a method.
- `create_time` semantics unverified; values are used one interval late, which costs at most 5 minutes of
  freshness and cannot create the null result seen at 4 h to 24 h.
- OI outlier prints left in (contract); they affect a handful of hourly signs.
- TRAIN has been read by eight earlier designs; positioning itself was new.

## V. Validation candidates

None.

## W. Final verdict

**A. POSITIONING_NO_EDGE.**

On Binance BTC, the 1 h price/OI state (new positions behind the move) and OI-confirmed vs OI-contradicted taker
flow carry no information about the next 15 minutes to 24 hours. Gross is 0 ± 2 bp everywhere, the result equals
random hours, the opposite direction and the price-only / flow-only matched controls. The trader-ratio divergences
could not be tested because the archive lacks 2022 top-trader data and TP is almost never below 1.

STOP. VALIDATION and HOLDOUT not run.
