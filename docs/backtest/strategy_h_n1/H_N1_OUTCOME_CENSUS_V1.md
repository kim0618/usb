# H-N1 OUTCOME CENSUS V1

2026-10-19 KST, local research only. DEPLOY/PAPER/GPT/SERVER/PUSH = NO. Results not committed (awaiting user closeout approval).

## A. FINAL FROZEN CONTRACT

| item | value |
|---|---|
| contract state | FROZEN_BEFORE_OUTCOME (H_N1_CONTRACT_V1, PRE_OUTCOME_AMENDMENT_1, amendments A-E) |
| old checksum | e08f3d8cab90ac4917cfbf91b7e0f9fb5a2ade9826b1ed25007a68f5bd3545e7 -> `superseded_e08f3d8c/` (SUPERSEDED_BY_PRE_OUTCOME_AMENDMENT, bytes unchanged) |
| new canonical checksum | 5353017cdf4d59388a46c7882b4f95c78a4d6c88d2d33de7ad4f63c1d560d9f8 |
| JSON sha | e990dc1c151077397d50c60e0b49007050ce98be0ab01dd94cba117acc66fe71 |
| MD sha | b5a9c1b3c094a005f0da7f4f40ed4f8ac592c48f24b76c917b2fe3041f008a5c |
| freeze timestamp | 2026-10-19 15:40:35 KST (06:40:35Z) |
| outcome calculations before freeze | 0 |
| reaction q33/q67 before freeze | NOT CALCULATED |
| holdout outcomes before freeze | NOT INSPECTED |

Order proof (`data/runtime/strategy_h_n1/run_log.txt`): CONTRACT_FROZEN 06:40:35Z -> PHASE1_DONE 06:55:22Z -> PHASE2_DONE (cutoff) 06:55:38Z -> PHASE3_START (first forward price read) 06:55:43Z.
Phase 1 aborted twice on code errors (missing key, JSON bool) before writing any artifact; fixes were serialization/classification bugs, no rule or threshold changed.
Contract was not committed (provenance shown by the log + checksums; commit not required by repo rules).

## B. EVENT CONSTRUCTION FUNNEL

**PRIMARY DEV**

| step | input | remaining | excluded | step excl | cum retention |
|---|---:|---:|---:|---:|---:|
| All eligible 10-Q (signal >= snapshot) | 16,103 | 16,103 | 0 | 0.0% | 100.0% |
| PIT valid | 16,103 | 16,103 | 0 | 0.0% | 100.0% |
| universe valid | 16,103 | 15,713 | 390 | 2.4% | 97.6% |
| price >= $5 | 15,713 | 11,472 | 4,241 | 27.0% | 71.2% |
| ADV >= $5M | 11,472 | 8,023 | 3,449 | 30.1% | 49.8% |
| fundamental valid | 8,023 | 6,944 | 1,079 | 13.4% | 43.1% |
| F+ | 6,944 | 3,388 | 3,556 | 51.2% | 21.0% |
| reaction available | 3,388 | 3,388 | 0 | 0.0% | 21.0% |
| WEAK | 3,388 | 968 | 2,420 | 71.4% | 6.0% |
| entry available | 968 | 967 | 1 | 0.1% | 6.0% |
| 5D valid | 967 | 967 | 0 | 0.0% | 6.0% |
| 21D valid | 967 | 967 | 0 | 0.0% | 6.0% |
| 63D valid | 967 | 967 | 0 | 0.0% | 6.0% |

**PRIMARY HOLDOUT**

| step | input | remaining | excluded | step excl | cum retention |
|---|---:|---:|---:|---:|---:|
| All eligible 10-Q (signal >= snapshot) | 7,835 | 7,835 | 0 | 0.0% | 100.0% |
| PIT valid | 7,835 | 7,835 | 0 | 0.0% | 100.0% |
| universe valid | 7,835 | 7,616 | 219 | 2.8% | 97.2% |
| price >= $5 | 7,616 | 5,828 | 1,788 | 23.5% | 74.4% |
| ADV >= $5M | 5,828 | 4,423 | 1,405 | 24.1% | 56.5% |
| fundamental valid | 4,423 | 3,615 | 808 | 18.3% | 46.1% |
| F+ | 3,615 | 2,022 | 1,593 | 44.1% | 25.8% |
| reaction available | 2,022 | 2,022 | 0 | 0.0% | 25.8% |
| WEAK | 2,022 | 619 | 1,403 | 69.4% | 7.9% |
| entry available | 619 | 619 | 0 | 0.0% | 7.9% |
| 5D valid | 619 | 619 | 0 | 0.0% | 7.9% |
| 21D valid | 619 | 616 | 3 | 0.5% | 7.9% |
| 63D valid | 616 | 341 | 275 | 44.6% | 4.4% |

**CONTRAST DEV**

| step | input | remaining | excluded | step excl | cum retention |
|---|---:|---:|---:|---:|---:|
| All eligible 10-Q (signal >= snapshot) | 16,103 | 16,103 | 0 | 0.0% | 100.0% |
| PIT valid | 16,103 | 16,103 | 0 | 0.0% | 100.0% |
| universe valid | 16,103 | 15,713 | 390 | 2.4% | 97.6% |
| price >= $5 | 15,713 | 11,472 | 4,241 | 27.0% | 71.2% |
| ADV >= $5M | 11,472 | 8,023 | 3,449 | 30.1% | 49.8% |
| fundamental valid | 8,023 | 6,944 | 1,079 | 13.4% | 43.1% |
| F- | 6,944 | 3,556 | 3,388 | 48.8% | 22.1% |
| reaction available | 3,556 | 3,556 | 0 | 0.0% | 22.1% |
| WEAK | 3,556 | 1,347 | 2,209 | 62.1% | 8.4% |
| entry available | 1,347 | 1,347 | 0 | 0.0% | 8.4% |
| 5D valid | 1,347 | 1,347 | 0 | 0.0% | 8.4% |
| 21D valid | 1,347 | 1,347 | 0 | 0.0% | 8.4% |
| 63D valid | 1,347 | 1,347 | 0 | 0.0% | 8.4% |

**CONTRAST HOLDOUT**

| step | input | remaining | excluded | step excl | cum retention |
|---|---:|---:|---:|---:|---:|
| All eligible 10-Q (signal >= snapshot) | 7,835 | 7,835 | 0 | 0.0% | 100.0% |
| PIT valid | 7,835 | 7,835 | 0 | 0.0% | 100.0% |
| universe valid | 7,835 | 7,616 | 219 | 2.8% | 97.2% |
| price >= $5 | 7,616 | 5,828 | 1,788 | 23.5% | 74.4% |
| ADV >= $5M | 5,828 | 4,423 | 1,405 | 24.1% | 56.5% |
| fundamental valid | 4,423 | 3,615 | 808 | 18.3% | 46.1% |
| F- | 3,615 | 1,593 | 2,022 | 55.9% | 20.3% |
| reaction available | 1,593 | 1,593 | 0 | 0.0% | 20.3% |
| WEAK | 1,593 | 609 | 984 | 61.8% | 7.8% |
| entry available | 609 | 609 | 0 | 0.0% | 7.8% |
| 5D valid | 609 | 609 | 0 | 0.0% | 7.8% |
| 21D valid | 609 | 607 | 2 | 0.3% | 7.7% |
| 63D valid | 607 | 364 | 243 | 40.0% | 4.6% |

Exclusion reasons (all events):

| split | reason | N |
|---|---|---:|
| PRE_SNAPSHOT | EXCLUDED_PRE_UNIVERSE_SNAPSHOT | 177 |
| DEV | NONE | 6,944 |
| DEV | PRICE_LT_5 | 4,241 |
| DEV | ADV_LT_5M_OR_LT_20_SESSIONS | 3,449 |
| DEV | FUND_REVENUE_MISSING | 688 |
| DEV | TICKER_UNRESOLVED_AT_SIGNAL | 390 |
| DEV | FUND_XBRL_MISSING | 319 |
| DEV | FUND_NET_INCOME_MISSING | 47 |
| DEV | FUND_INVALID_PERIOD | 12 |
| DEV | FUND_REVENUE_PRIOR_MISSING | 11 |
| DEV | FUND_INVALID_FILING_MAPPING | 2 |
| HOLDOUT | NONE | 3,615 |
| HOLDOUT | PRICE_LT_5 | 1,788 |
| HOLDOUT | ADV_LT_5M_OR_LT_20_SESSIONS | 1,405 |
| HOLDOUT | FUND_REVENUE_MISSING | 454 |
| HOLDOUT | TICKER_UNRESOLVED_AT_SIGNAL | 219 |
| HOLDOUT | FUND_XBRL_MISSING | 185 |
| HOLDOUT | FUND_INVALID_FILING_MAPPING | 149 |
| HOLDOUT | FUND_NET_INCOME_MISSING | 11 |
| HOLDOUT | FUND_REVENUE_PRIOR_MISSING | 8 |
| HOLDOUT | FUND_INVALID_PERIOD | 1 |

Eligible-cell counts (frozen cutoff): {"DEV": {"F+|MIDDLE": 1138, "F+|STRONG": 1282, "F+|WEAK": 968, "F-|WEAK": 1347, "F-|STRONG": 1033, "F-|MIDDLE": 1176}, "HOLDOUT": {"F-|WEAK": 609, "F-|MIDDLE": 546, "F+|STRONG": 720, "F+|MIDDLE": 683, "F+|WEAK": 619, "F-|STRONG": 438}} 

Anchor: {"DEV": {"8k_anchor": 6760, "10q_anchor": 184, "submissions_truncated": 19}, "HOLDOUT": {"8k_anchor": 3506, "10q_anchor": 109, "submissions_truncated": 3}} 

## D. PRIMARY DEV

| h | N | mean 0 | median 0 | win 0 | mean 10 | median 10 | win 10 | mean 25 | median 25 | win 25 | CI95 @10 (ISO week, 10k) | blocks | delisted N | obs mean 10 | cons mean 10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| 1D (diag) | 967 | -0.02% | -0.09% | 48.2% | -0.12% | -0.19% | 46.5% | -0.27% | -0.34% | 43.1% | [-0.31%, +0.11%] | 50 | 0 | -0.12% | -0.12% |
| 5D | 967 | -0.27% | -0.41% | 46.9% | -0.37% | -0.51% | 45.9% | -0.52% | -0.66% | 44.0% | [-1.18%, +0.43%] | 50 | 0 | -0.37% | -0.37% |
| 21D | 967 | +0.81% | +0.02% | 50.3% | +0.71% | -0.08% | 49.8% | +0.56% | -0.23% | 49.2% | [-0.59%, +1.73%] | 50 | 0 | +0.71% | +0.71% |
| 63D | 967 | -1.22% | -3.71% | 41.6% | -1.32% | -3.81% | 41.2% | -1.47% | -3.96% | 40.6% | [-3.38%, +0.29%] | 50 | 1 | -1.32% | -1.44% |

## E. PRIMARY HOLDOUT

| h | N | mean 0 | median 0 | win 0 | mean 10 | median 10 | win 10 | mean 25 | median 25 | win 25 | CI95 @10 (ISO week, 10k) | blocks | delisted N | obs mean 10 | cons mean 10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| 1D (diag) | 619 | -0.04% | -0.14% | 47.3% | -0.14% | -0.24% | 46.0% | -0.29% | -0.39% | 44.6% | [-0.45%, +0.12%] | 35 | 0 | -0.14% | -0.14% |
| 5D | 619 | -0.31% | -0.73% | 43.1% | -0.41% | -0.83% | 42.3% | -0.56% | -0.98% | 41.7% | [-1.57%, +0.75%] | 35 | 0 | -0.41% | -0.41% |
| 21D | 616 | -0.32% | -1.92% | 43.0% | -0.42% | -2.02% | 42.4% | -0.57% | -2.17% | 41.7% | [-1.91%, +1.11%] | 33 | 0 | -0.42% | -0.42% |
| 63D | 341 | +3.44% | +0.06% | 50.1% | +3.34% | -0.04% | 49.9% | +3.19% | -0.19% | 49.9% | [+1.60%, +7.80%] | 24 | 0 | +3.34% | +3.34% |

## F. CONTRAST (F- ∩ WEAK) vs PRIMARY, mean excess 0 bp, OBSERVED

| split | h | PRIMARY N | PRIMARY mean | CONTRAST N | CONTRAST mean | P - C | CONTRAST median 0 | CONTRAST mean 25 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| DEV | 5D | 967 | -0.27% | 1347 | +0.03% | -0.30% | -0.19% | -0.22% |
| DEV | 21D | 967 | +0.81% | 1347 | +1.58% | -0.77% | +0.26% | +1.33% |
| DEV | 63D | 967 | -1.22% | 1347 | -0.13% | -1.09% | -3.51% | -0.38% |
| HOLDOUT | 5D | 619 | -0.31% | 609 | -0.88% | +0.57% | -1.13% | -1.13% |
| HOLDOUT | 21D | 616 | -0.32% | 607 | -1.26% | +0.94% | -2.52% | -1.51% |
| HOLDOUT | 63D | 341 | +3.44% | 364 | +1.83% | +1.61% | -1.81% | +1.58% |

## G. COST SENSITIVITY (PRIMARY mean net excess, OBSERVED)

| split | h | 0 bp | 10 bp | 25 bp |
|---|---|---:|---:|---:|
| DEV | 5D | -0.27% | -0.37% | -0.52% |
| DEV | 21D | +0.81% | +0.71% | +0.56% |
| DEV | 63D | -1.22% | -1.32% | -1.47% |
| HOLDOUT | 5D | -0.31% | -0.41% | -0.56% |
| HOLDOUT | 21D | -0.32% | -0.42% | -0.57% |
| HOLDOUT | 63D | +3.44% | +3.34% | +3.19% |

## H. DELISTING SENSITIVITY (TRUE_DELISTING = CIK gone under every successor ticker)

| cohort | split | h | N | true delisting N | ticker-changed N | exit-gap N | obs mean 10 | cons (-100%) mean 10 | gate verdict obs / cons |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| PRIMARY | DEV | 5D | 967 | 0 | 0 | 0 | -0.37% | -0.37% | FAIL / FAIL |
| PRIMARY | DEV | 21D | 967 | 0 | 1 | 0 | +0.71% | +0.71% | FAIL / FAIL |
| PRIMARY | DEV | 63D | 967 | 1 | 3 | 0 | -1.32% | -1.44% | FAIL / FAIL |
| PRIMARY | HOLDOUT | 5D | 619 | 0 | 0 | 0 | -0.41% | -0.41% | FAIL / FAIL |
| PRIMARY | HOLDOUT | 21D | 616 | 0 | 1 | 0 | -0.42% | -0.42% | FAIL / FAIL |
| PRIMARY | HOLDOUT | 63D | 341 | 0 | 1 | 0 | +3.34% | +3.34% | FAIL / FAIL |
| CONTRAST | DEV | 5D | 1347 | 1 | 0 | 0 | -0.07% | -0.14% |  |
| CONTRAST | DEV | 21D | 1347 | 2 | 0 | 0 | +1.48% | +1.33% |  |
| CONTRAST | DEV | 63D | 1347 | 5 | 3 | 0 | -0.23% | -0.64% |  |
| CONTRAST | HOLDOUT | 5D | 609 | 0 | 0 | 0 | -0.98% | -0.98% |  |
| CONTRAST | HOLDOUT | 21D | 607 | 2 | 1 | 0 | -1.36% | -1.69% |  |
| CONTRAST | HOLDOUT | 63D | 364 | 2 | 3 | 0 | +1.73% | +1.18% |  |

## I. CONCENTRATION (PRIMARY, net 10 bp, OBSERVED)

| split | h | net total | positive sum | top1 % of net | top5 % of net | top10% % of net | top5 % of positive | top1% removed (k) mean | largest winner | largest loser |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| DEV | 5D | -3.549 | 19.271 | n/a | n/a | n/a | 7.7% | (10) -0.63% | COIN 2024-10-31 +37.74% | UNH 2025-05-08 -33.10% |
| DEV | 21D | +6.876 | 41.215 | 12.9% | 47.2% | 319.6% | 7.9% | (10) +0.15% | NVCR 2024-10-31 +88.93% | JANX 2025-11-07 -41.48% |
| DEV | 63D | -12.767 | 78.770 | n/a | n/a | n/a | 8.2% | (10) -2.48% | FLNC 2025-08-12 +189.99% | CNC 2025-04-28 -72.71% |
| HOLDOUT | 5D | -2.512 | 13.277 | n/a | n/a | n/a | 10.9% | (7) -0.72% | SEDG 2026-05-08 +49.13% | DV 2026-05-07 -21.63% |
| HOLDOUT | 21D | -2.563 | 25.486 | n/a | n/a | n/a | 13.6% | (7) -1.10% | MRNA 2026-08-03 +171.26% | ALHC 2026-05-01 -38.16% |
| HOLDOUT | 63D | +11.396 | 37.034 | 15.0% | 50.0% | 173.3% | 15.4% | (4) +1.92% | MU 2026-03-20 +171.15% | INV 2026-05-15 -84.13% |

net total <= 0 -> share of net is undefined (n/a) and gate 5 fails by contract.

## J. SEASON ROBUSTNESS (PRIMARY, mean net 10 bp, OBSERVED, DEV+HOLDOUT pooled)

**5D**: 2024Q4 N=202 +0.17% +, 2025Q1 N=40 -1.45% -, 2025Q2 N=162 -0.04% -, 2025Q3 N=277 +0.19% +, 2025Q4 N=286 -1.32% -, 2026Q1 N=60 +1.16% +, 2026Q2 N=276 -1.89% -, 2026Q3 N=283 +0.71% +  -> eligible 8, positive 4, ratio 50.0%

**21D**: 2024Q4 N=202 +0.91% +, 2025Q1 N=40 -3.66% -, 2025Q2 N=162 -0.99% -, 2025Q3 N=277 +2.14% +, 2025Q4 N=286 +0.76% +, 2026Q1 N=60 +1.96% +, 2026Q2 N=276 -1.84% -, 2026Q3 N=280 +0.48% +  -> eligible 8, positive 5, ratio 62.5%

**63D**: 2024Q4 N=202 -3.02% -, 2025Q1 N=40 -4.93% -, 2025Q2 N=162 -3.64% -, 2025Q3 N=277 -0.18% -, 2025Q4 N=286 +0.60% +, 2026Q1 N=60 +10.61% +, 2026Q2 N=276 +1.94% +, 2026Q3 N=5 -6.63% - (N<30, out)  -> eligible 7, positive 3, ratio 42.9%

## K. 63D OVERLAP (diagnostic only)

| split | 63D N | overlap N | overlap % | non-overlap N | event-censored mean 0 | non-overlap mean 0 |
|---|---:|---:|---:|---:|---:|---:|
| DEV | 967 | 263 | 27.2% | 704 | -1.19% | -1.73% |
| HOLDOUT | 341 | 266 | 78.0% | 75 | +3.23% | +8.52% |

## L. 8-GATE MATRIX (OBSERVED evaluation; CONSERVATIVE evaluation gives the same PASS/FAIL per gate)

**5D**

| gate | result | evidence |
|---|---|---|
| G1 mean 25bp > 0 both | FAIL | mean25 dev -0.5170% hold -0.5558% |
| G2 median 10bp > 0 both | FAIL | median10 dev -0.5096% hold -0.8302% |
| G3 DEV CI lo 10bp > 0 | FAIL | dev CI10 [-1.1769%, +0.4347%] |
| G4 conservative mean 10bp > 0 both | FAIL | conservative mean10 dev -0.3670% hold -0.4058% |
| G5 concentration | FAIL | dev net -3.549 top5 nan% top1%rm -0.6316%; holdout net -2.512 top5 nan% top1%rm -0.7160% |
| G6 seasons >= 60% | FAIL | 4/8 eligible seasons positive |
| G7 PRIMARY > CONTRAST 0bp both | FAIL | dev P -0.2670% vs C +0.0341%; hold P -0.3058% vs C -0.8768% |
| G8 shorter-horizon consistency | N/A | N/A (5D) |
| SAMPLE N >= 300 both | PASS | N dev 967 hold 619 |
| delisting consistency | N/A | obs FAIL / cons FAIL |

**21D**

| gate | result | evidence |
|---|---|---|
| G1 mean 25bp > 0 both | FAIL | mean25 dev +0.5611% hold -0.5662% |
| G2 median 10bp > 0 both | FAIL | median10 dev -0.0834% hold -2.0186% |
| G3 DEV CI lo 10bp > 0 | FAIL | dev CI10 [-0.5876%, +1.7322%] |
| G4 conservative mean 10bp > 0 both | FAIL | conservative mean10 dev +0.7111% hold -0.4162% |
| G5 concentration | FAIL | dev net +6.876 top5 47.2% top1%rm +0.1539%; holdout net -2.563 top5 nan% top1%rm -1.1025% |
| G6 seasons >= 60% | PASS | 5/8 eligible seasons positive |
| G7 PRIMARY > CONTRAST 0bp both | FAIL | dev P +0.8111% vs C +1.5799%; hold P -0.3162% vs C -1.2604% |
| G8 shorter-horizon consistency | FAIL | 5D dev -0.2670%, 5D holdout -0.3058% |
| SAMPLE N >= 300 both | PASS | N dev 967 hold 616 |
| delisting consistency | CONSISTENT (both FAIL, no PASS possible) | obs FAIL / cons FAIL |

**63D**

| gate | result | evidence |
|---|---|---|
| G1 mean 25bp > 0 both | FAIL | mean25 dev -1.4702% hold +3.1920% |
| G2 median 10bp > 0 both | FAIL | median10 dev -3.8097% hold -0.0411% |
| G3 DEV CI lo 10bp > 0 | FAIL | dev CI10 [-3.3837%, +0.2890%] |
| G4 conservative mean 10bp > 0 both | FAIL | conservative mean10 dev -1.4432% hold +3.3420% |
| G5 concentration | FAIL | dev net -12.767 top5 nan% top1%rm -2.4826%; holdout net +11.396 top5 50.0% top1%rm +1.9202% |
| G6 seasons >= 60% | FAIL | 3/7 eligible seasons positive |
| G7 PRIMARY > CONTRAST 0bp both | FAIL | dev P -1.2202% vs C -0.1327%; hold P +3.4420% vs C +1.8343% |
| G8 shorter-horizon consistency | FAIL | 5D dev -0.2670%, 5D holdout -0.3058%, 21D dev +0.8111%, 21D holdout -0.3162% |
| SAMPLE N >= 300 both | PASS | N dev 967 hold 341 |
| delisting consistency | CONSISTENT (both FAIL, no PASS possible) | obs FAIL / cons FAIL |

## M. FINAL H-N1 VERDICT

**FAIL** (contract verdict order: INSUFFICIENT no -> FAIL_SIGNAL no (21D DEV gross +0.81%) -> FAIL_COST_BOUND no (21D DEV 25bp +0.56%) -> H_N1_PASS no -> FAIL_HOLDOUT no (DEV side fails median 10bp and CI) -> BORDERLINE no (21D HOLDOUT 25bp -0.57%, 63D DEV 25bp -1.47%) -> FAIL).

## N. INTERPRETATION

**FACT**
- PRIMARY N: DEV 967 at every horizon, HOLDOUT 619 / 616 / 341 (5/21/63D). Sample is OK; the result is not a sample problem.
- 5D gross mean excess is negative in both splits (DEV -0.27%, HOLDOUT -0.31%).
- 21D: DEV +0.81% gross, but median -0.08% at 10bp, CI [-0.59%, +1.73%]; HOLDOUT -0.32% gross, median -2.02%.
- 63D: DEV -1.22% gross (median -3.71%); HOLDOUT +3.44% gross, median +0.06%, top 5 events = 50.0% of net, 78% of windows overlap the next 10-Q.
- CONTRAST (F- ∩ WEAK) beats PRIMARY in DEV at 5D/21D/63D (21D: +1.58% vs +0.81%). PRIMARY beats CONTRAST only in HOLDOUT.
- True delistings: 0-1 PRIMARY events per horizon; OBSERVED and CONSERVATIVE give the same gate outcomes. Delisting is not a factor in the verdict.
- 0 of 8 gates pass at 5D; 1 of 8 at 21D (seasons 5/8); 0 at 63D.

**INTERPRETATION**
- The data does not support the fundamental-change thesis (revenue and net income up + weak reaction = underreaction): the F- group did as well or better in DEV, and the signs flip between DEV and HOLDOUT at 21D and 63D.
- The HOLDOUT 63D positive mean is a mean without a median (median about 0, top-5 concentrated, mostly 2026Q1 N=60 at +10.6%), and DEV 63D is negative; it is not a reproducible effect.

**LIMITATION**
- DEV is 14 months (2024-10-25..2025-12-31, 50 ISO-week blocks), HOLDOUT 63D covers essentially one filing season (signals up to about 2026-07); about 8 seasons in total.
- Price return only (no dividends) for stock and SPY; unadjusted grouped daily with split/stock-dividend factors.
- Revenue tag coverage excludes banks/insurers that do not report the four fixed tags (FUND_REVENUE_MISSING 688 DEV / 454 HOLDOUT); companyfacts fetched 2026-09-28, so 10-Qs filed after that are INVALID_FILING_MAPPING (149 HOLDOUT), not F-.
- 63D windows overlap the next quarterly filing (DEV 27%, HOLDOUT 78%); the 63D result is partly the next quarter's information.
- Universe is a single 2024-10-25 snapshot (no later IPOs).
None of these limitations is grounds for a rescue; they are stated only as the scope of the FAIL.

## O. NEXT ACTION

FAIL -> H-N1 closeout. No GPT rescue, feature mining, reaction-threshold change, small-cap/sector subset, horizon change, runtime or Paper.
Any new idea = separate version + preregistration on new data.

## Artifacts

| artifact | sha256 |
|---|---|
| docs h_n1_contract_v1.json | e990dc1c151077397d50c60e0b49007050ce98be0ab01dd94cba117acc66fe71 |
| docs H_N1_CONTRACT_V1.md | b5a9c1b3c094a005f0da7f4f40ed4f8ac592c48f24b76c917b2fe3041f008a5c |
| runtime h_n1_events_phase1.json.gz (24,115 events, no forward columns) | 6fb84cfb34fc8fe0252a8f179e813e1c500d6491adb0b78a15c72e429fa57f17 |
| runtime h_n1_dev_reaction_cutoff_v1.json (canonical digest dd182483...37d6) | 507bfeafdd7a3ec9552c8b076e15e22eeddd40cd9d3648d3c4aaa14e38550c4b |
| runtime h_n1_outcomes_phase3.json.gz (PRIMARY+CONTRAST rows) | cfa1ce4ffe3cdfe17bb5b352a3293b59eef9f9ba7a21d908f1e3b42e0d76714e |
| runtime h_n1_funnel_v1.json | 7eb8020438d7b2e67277d065de5419472e704c00304f71d253308e6692aa3b05 |
| runtime h_n1_outcome_summary_v1.json (stats, seasons, gates, overlap, verdict) | d0b98e39353117005a747a21d34fff977d85ddd2c5c5c7ac32bfbf945b9a6181 |

Code: `backend/app/backtest/strategy_h_n1/` (common, events, cutoff, outcomes, run, report), tests `backend/tests/test_strategy_h_n1.py` (20 passed: HOLDOUT/ineligible rows refused by the quantile function, invalid fundamentals never F-, pre-snapshot split, forward-column guard, frozen bucket reuse, verdict precedence, 5D cannot pass alone, shorter-horizon and delisting consistency).
Spot check: SPY and stock 21D returns recomputed from raw grouped files and splits for 4 events (incl. one 1.05 stock dividend) match exactly.

**H-N1 = FAIL**
