# Strategy G0 - After-hours to Next Premarket Alpha Pre-Validation

| | |
|---|---|
| Research id | `g0-after-premarket-v1` (`STRATEGY_G_CANDIDATE`) |
| Rules | `g0_after_premarket_rules_v1.json`, canonical `4cb4e0eb9b9eade380421d5e549b31af12cee5c16329fc85089d59bc78a6ccaf`, file bytes `05177962d1bb646b…` |
| Frozen | 2026-09-27 14:54 KST, after the returns-free coverage audit and before any G0 feature value, label or statistic |
| Coverage audit | `data/runtime/strategy_g_candidate/coverage_audit.json` (counts, anchor availability and traded value only; `returns_computed: false`) |
| Code | `backend/app/backtest/strategy_g0_after_premarket/`, CLI `backend/app/dev/run_strategy_g0.py` |
| Tests | `backend/tests/strategy_g0/test_strategy_g0.py` (20 passed before the freeze) |
| Run | `g0-e9a66655d3fb` (`data/runtime/strategy_g_candidate/runs/g0-e9a66655d3fb/`), 316 s, daily inputs and tape unchanged at the end |
| Provider calls | 0. Writer lock taken: no |
| **Verdict** | **FAIL** |

G is a separate candidate. Strategy F is not present in this repository (no code, doc, rules or
runtime directory), so nothing from F can enter G. A and E rules, papers, cost contracts, the
Paper Gate and the server runtime are not touched; every G file is new and no tracked file was
modified.

---

## A. Repository Audit

```text
repo root:            /home/tjd618/usb
current branch:       main (HEAD cf03800, ahead of origin by 19; many unrelated user edits in the tree, untouched)
git status:           G0 adds only untracked files (package, CLI, tests, docs)

historical store:     Drive 1_US-B/market_data (Common Historical Store V1; snapshot USB-HIST-V1 FROZEN, freeze 9ebd6c29…)
minute store:         market_data/raw/massive/minute (3,889 symbols, provider pages + COMPLETE ledgers, window 04:00-20:00 ET)
                      market_data/normalized/minute/massive (legacy Parquet, 30 symbols)
                      data/runtime/common_hist/v2_staging (USB-HIST-V2 local staging, 174 symbols A..AMSWA, 2-year, all COMPLETE)
daily store:          USB-HIST-V1 grouped daily (2024-09-17..2026-09-16, 501 sessions), read via strategy_d_analog.source
reference store:      CS reference snapshots inside USB-HIST-V1 (membership + composite FIGI per snapshot date)
split/corp-action:    USB-HIST-V1 splits -> Panel.split_arrays (factor per session)
trading calendar:     app.market.calendar.MarketCalendar (exchange_calendars XNYS)
PIT universe:         app.backtest.strategy_e0_overnight.dataset.load_daily_rows (E0 PIT universe, imported unmodified)

E research implementation: app.backtest.strategy_e0_overnight, strategy_e1_premarket, strategy_e1_h5_confirm
matching infrastructure:   app.backtest.strategy_e1_h5_confirm.matching (session_demeaned / same_session)
bootstrap infrastructure:  app.backtest.strategy_e0_overnight.stats.session_bootstrap (session cluster)

F research location:  NOT FOUND
event classification: NOT FOUND for G (SEC EDGAR store exists only for Strategy C candidate CIKs)
ticker-change table:  NOT FOUND (FIGI per CS snapshot is used as the identity guard)
```

Reused unchanged, as infrastructure only: E0 minute loader (`minute.load_symbol_tape`,
`et_offsets`), E0 daily PIT loader and SPY benchmark reader, E0 `summarise` /
`symbol_concentration` / `bucket_index`, E1 `universe_volatility`, E1-H5 matching estimators. No
E threshold or rule is used. `market_data/forward/massive` (E-MAX forward evidence) is not read.

## B. Data Coverage (returns-free)

```text
AFTER -> NEXT PREMARKET COVERAGE
start session (T):              2024-10-15   (first 20 grid sessions are the liquidity warmup)
end session (T):                2026-09-15   (T+1 = 2026-09-16, last grid session)
source trading sessions:        480
after-hours available sessions: 480
next-premarket available:       480
paired sessions:                480
unique symbols on tape:         3,916 (3,865 paired)
after symbol-sessions:          410,176 (any bar 16:00-19:59)
paired symbol-sessions:         302,423 (73.7% of after)
paired + daily PIT eligible:    241,337 rows, 480 sessions, 2,912 symbols
symbols per paired session:     P5 121 / P25 135 / P50 239 / P75 267 / P95 2,332
```

Premarket anchor availability on T+1 (daily-PIT paired rows):

| anchor | exact bar | first print within 5m | within 15m | any earlier print |
|---|---|---|---|---|
| 04:00 | 26.8% | 36.7% | 42.2% | - |
| 07:00 | 30.3% | 37.7% | 46.8% | 66.1% |
| 08:00 | 25.5% | 35.4% | 47.0% | 81.5% |

09:25 exit: a print in [08:00, 09:24] exists on 79.0% of rows; the exact 09:24 bar on 17.9%.
Entry bar at 08:00 (first print in 15m): traded value P5 $1.9k / P25 $10.2k / P50 $43.7k /
P75 $289k / P95 $10.8M; zero prints in the prior 30 minutes on 28.1%.

**The 16:00 bar is the closing cross.** AAPL's median 16:00 volume is 672k shares against 5k at
16:01, and across paired rows the 16:00 bar alone carries a median $128k of traded value
against $56k for all of 16:01-19:59. G's after-hours window therefore starts at 16:01.

After-hours activity 16:01-19:59 on daily-PIT paired rows: >= 1 bar 190,311, >= 3 bars 132,123,
>= 5 bars 105,209; >= 3 bars and an 08:00 entry print 91,327.

**Sample concentration.** Paired daily-PIT rows per quarter: 24Q4 11,654 / 25Q1 13,811 /
25Q2 13,603 / 25Q3 12,240 / 25Q4 6,716 / 26Q1 6,725 / 26Q2 82,407 / 26Q3 94,181. The broad
minute collection starts 2026-04-20; before it only the 30-symbol V1 tape and the 174-symbol
A-prefix staging exist. 73% of the study lives in the last two quarters.

## C. Session Pairing

500 grid pairs, audit PASS: every T+1 is after T, equals `MarketCalendar.next_trading_day(T)`,
no duplicate pair, no source paired twice. Kinds: 388 normal overnight, 91 weekend, 21 holiday.

```text
Fri 2024-09-20 -> Mon 2024-09-23        weekend_gap
Wed 2024-11-27 -> Fri 2024-11-29        holiday_gap (Thanksgiving)
Tue 2024-12-31 -> Thu 2025-01-02        holiday_gap
Wed 2025-01-08 -> Fri 2025-01-10        holiday_gap (national day of mourning 2025-01-09)
early-close sources (excluded): 2024-11-29, 2024-12-24, 2025-07-03, 2025-11-28, 2025-12-24
```

## D. Data Integrity

```text
DATA ISSUE
type: duplicate timestamps across provider pages (overlapping requests)
count: 16,888 minutes; 33 source and 33 target paired symbol-days affected
affected symbols: 16 (ADBE, AMGN, APP, CAR, DJT, LLY, MELI, META, MRNA, NFLX, NOW, PANW, PLTR, SPOT, UNH, V)
handling: identical copies dropped to one; conflicting copies would exclude the day (0 found)
reason: the same minute fetched by two overlapping requests; identical values, so no information lost

DATA ISSUE
type: bars outside 04:00-20:00 ET
count: 28 bars on 16 symbols (e.g. ACHR, CCL, CFG)
handling: outside every G window, never read
reason: provider edge prints

DATA ISSUE
type: Drive vs staging disagreement on an overlapping session
count: 2 of 17,654 overlapping symbol-days
handling: the Drive copy is used; staging adds only sessions Drive lacks
reason: same authority collected at different times

out-of-order timestamps: 0   OHLC invalid: 0   non-positive price: 0   negative volume: 0
zero volume: 0   missing volume: 0   missing timestamp: 0
```

Session classification: after-hours = bar start 16:01-19:59 of T; premarket = 04:00-09:29 of
T+1 (features never read T+1; labels read only [entry bar, 09:24]).

Corporate actions: a split in (T, T+1] is excluded through the E0 split-factor test (77 such
cells on the whole daily universe); a FIGI change between the CS snapshots as of T and T+1
excludes the row. Both are counted in the run's `universe.json`. Listing / delisting boundaries:
CS membership at T, a daily open at T+1 and a T+1 entry print are required.

## E. Universe (point in time)

CS in the latest reference snapshot on or before T; XNAS/XNYS/XASE; close(T) >= $5; median
close*volume over [T-20, T-1] >= $5M with >= 15 sessions; open at T+1; no split in (T, T+1];
same FIGI at T and T+1; source session not an early close; >= 3 after-hours prints 16:01-19:59
with >= $10k traded value; an entry print on T+1 in [08:00, 08:14] (label-existence filter,
reported per hypothesis as the fill rate).

## F. Frozen Rules

```text
G0 RULES FROZEN

rules file:   docs/backtest/strategy_g_candidate/g0_after_premarket_rules_v1.json
sha256 file:  docs/backtest/strategy_g_candidate/g0_after_premarket_rules_v1.sha256
sha256:       4cb4e0eb9b9eade380421d5e549b31af12cee5c16329fc85089d59bc78a6ccaf (canonical)

Primary Entry: 08:00 ET  (open of the first T+1 print in [08:00, 08:14])
Primary Exit:  09:25 ET  (close of the last print in [entry bar, 09:24])

H1: after_return_1600_2000 > 0 and distance_to_after_high >= -0.5%
H2: after_return_1600_2000 > 0 and after_rvol >= 3.0
H3: after_return_1600_2000 > 0 and position_in_after_range >= 0.8 and last30m_after_return > 0
H4: relative_strength_vs_spy > 0 and after_return_1600_2000 > 0 and after_peak_retention >= 0.8
H5: after_return_1600_2000 >= 2% and after_rvol >= 3.0 and distance_to_after_high >= -1% and last30m_after_return > 0
```

Why 08:00 was kept: coverage within 15 minutes is 47.0% at 08:00, 46.8% at 07:00 and 42.2% at
04:00, so no earlier time buys availability; a 15-minute entry window because the exact 08:00
bar exists on only a quarter of rows. Secondary diagnostics (08:00->09:00, 08:00->09:15,
07:00->09:25, exact-08:00 strict entry) cannot produce a PASS.

Gate (per hypothesis, all twelve for PASS): >= 1,000 rows / 100 sessions / 200 symbols; mean
lift >= +20 bp; candidate median >= -10 bp; P(>=2%) lift >= 2pp or P(>=3%) >= 1.5pp or
P(MFE>=3%) >= 3pp; mean lift after top-1% removal >= +10 bp; top-1 ticker <= 15% and top-5 <= 40%
of excess; P(<=-2%) ratio <= 1.5; weaker of session/symbol cluster bootstrap 95% CI low > 0;
>= 65% positive months among >= 8 months with >= 20 rows; matched lift >= +10 bp with match rate
>= 70%; break-even cost >= 50 bp round trip; median entry-bar traded value >= $10k and fill rate
>= 60%. Statistical 1-11 with 12 failing is INCONCLUSIVE.

Pre-freeze tests: pairing (Friday->Monday, holiday eve, 2025-01-09 closure, grid hole), after /
premarket classification (16:00 bar excluded, 09:25+ never read), feature cutoff invariance,
synthetic future-bar leakage rejection (a leaky builder must FAIL the audit), split in (T, T+1]
exclusion, FIGI change exclusion, early close exclusion, labels from T+1 only, duplicate handling,
gate logic, bootstrap determinism, rules hash and sha file.

---

## G. Baseline Premarket Return (08:00 -> 09:25)

Universe after every filter: 135,252 signal rows (475 sessions, 2,676 tickers after the early-close
exclusion), of which 90,318 have an entry print in [08:00, 08:14] (fill rate 66.8%) and are
evaluated. Exclusion counters: FIGI changed between T and T+1 4, conflicting duplicates 0,
after-hours activity below the floor 212,830, early-close sources 11,988, no tape 795,978
(daily-PIT rows outside the minute collection), no T+1 tape 181.

| N | mean | median | win | std | P5 | P25 | P75 | P95 |
|---|---|---|---|---|---|---|---|---|
| 90,318 | **-8.0 bp** | -3.3 bp | 43.55% | 126 bp | -172 bp | -48 bp | +32 bp | +147 bp |

P(>= +0.5%) 18.46%, P(>= +1%) 8.81%, P(>= +2%) 2.92%, P(>= +3%) 1.28%, P(>= +5%) 0.40%;
P(<= -0.5%) 24.31%, P(<= -1%) 12.13%, P(<= -2%) 3.66%, P(<= -3%) 1.40%.
MFE mean +73.6 bp (P >= 1% 21.9%, >= 3% 3.76%), MAE mean -79.3 bp.

The 08:00 -> 09:25 premarket window **drifts down** for the average after-hours-active stock:
mean -8 bp, median -3 bp, win 43.6%, and 16 of 24 months negative. Every lift below is measured
against this drift. Secondary baselines: 08:00->09:00 -4.6 bp, 08:00->09:15 -7.1 bp,
07:00->09:25 -9.9 bp, strict exact-08:00 entry -7.2 bp.

## H. Single Feature Results (fixed buckets, primary label)

No after-hours feature produces a monotone mean. The shape that repeats across features is a
**U in dispersion with a flat or falling mean**: the further a feature is from "quiet", the wider
both tails, and the mean stays at or below the baseline.

| feature | shape | notable |
|---|---|---|
| after_return_1600_2000 | U-shaped dispersion, non-monotone mean | +0.5..2% -9.6 bp, +2..5% -25.0 bp, +5..10% -29.2 bp; only >= +10% is positive (306 rows, +70.5 bp, median +46.6 bp) |
| after_rvol | flat then wider | 3..5 -12.1 bp, 5..10 -15.3 bp, >= 10 -3.0 bp with P(<=-2%) 9.75% vs 3.66% |
| position_in_after_range | flat, weak U | 0.95..1.0 -5.7 bp, 0.2..0.5 -10.9 bp; no monotone gradient |
| distance_to_after_high | best near the high, but still negative | within 0.5% of high -5.8 bp; > 5% below -20.3 bp |
| last30m_after_return | inverted | >= +0.5% -16.4 bp; exactly 0 (no print) -5.6 bp |
| after_peak_retention | flat | 0.8..1.0 -6.4 bp vs baseline -8.0 |
| after_high_return | falling then a thin positive tail | 2..5% -15.4, 5..10% -28.8, >= 10% +24.6 bp (575 rows) |
| after_dollar_volume | U, small | $10-50k -4.3, $250k-1M -11.2, $25M+ -4.7 bp |
| regular_day_return | falling at the top | >= +5% **-26.1 bp** (median -18.8) |
| close_vs_regular_vwap | falling at the top | >= +2% **-30.7 bp** (median -24.3) |
| relative_strength_vs_spy | falling at the top | >= +3% -20.1 bp |
| close_price / regular_dollar_volume | cheaper and thinner are worse | $5-10 -16.1 bp, $500M+ volume -4.8 bp |

Tail shift is real but two-sided: every "strong" bucket raises P(>=2%) *and* P(<=-2%) together.

## I. H1-H5 (primary label E0800:0925)

| set | N | mean | median | win | mean lift | median lift | P(>=1%) | P(>=2%) | P(>=3%) | P(>=5%) | P(<=-2%) ratio |
|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 90,318 | -8.0 | -3.3 | 43.55% | - | - | 8.81% | 2.92% | 1.28% | 0.40% | 1.00 |
| H1 | 35,766 | -6.9 | -2.9 | 43.74% | **+1.1** | +0.5 | 7.01% | 1.99% | 0.84% | 0.28% | 0.71 |
| H2 | 5,242 | -7.4 | -2.6 | 44.01% | +0.6 | +0.8 | 13.37% | 5.88% | 3.15% | 1.20% | **1.91** |
| H3 | 12,948 | -8.9 | -4.8 | 44.05% | -0.9 | -1.5 | 8.93% | 2.86% | 1.17% | 0.38% | 1.01 |
| H4 | 9,302 | -8.1 | -2.2 | 43.85% | -0.1 | +1.1 | 9.17% | 2.84% | 1.33% | 0.47% | 1.10 |
| H5 | 373 | **-23.4** | -12.6 | 43.97% | **-15.4** | -9.3 | 20.11% | 9.65% | 4.56% | 1.34% | **3.37** |

(bp unless marked.) No hypothesis has a positive gross mean. H1 and H4 lower both tails (quieter
names); H2 and H5 raise both tails and the downside more than the upside. H5 has 373 rows from
253 tickers on 160 sessions and fails the sample criterion outright.

## J. Aggressive Tail Analysis

| set | P(>=2%) lift | ratio | P(>=3%) lift | ratio | P(>=5%) ratio | P(MFE>=3%) | lift |
|---|---|---|---|---|---|---|---|
| H1 | -0.93pp | 0.68 | -0.45pp | 0.65 | 0.71 | 2.54% | -1.21pp |
| H2 | +2.95pp | 2.01 | +1.86pp | 2.45 | 3.03 | 8.20% | +4.45pp |
| H3 | -0.07pp | 0.98 | -0.11pp | 0.91 | 0.96 | 3.91% | +0.15pp |
| H4 | -0.08pp | 0.97 | +0.05pp | 1.04 | 1.19 | 3.66% | -0.10pp |
| H5 | +6.73pp | 3.30 | +3.27pp | 3.55 | 3.38 | 15.82% | +12.06pp |

H2 and H5 do select an upside tail, and their tail-lift intervals exclude zero (H5 P(>=2%) lift
95% CI [+3.7, +9.9]pp session / [+3.8, +9.9]pp symbol; P(MFE>=3%) [+8.0, +16.5]pp). **But the
downside tail grows faster**: H5 P(<=-2%) 12.33% vs 3.66% (x3.37), P(<=-3%) 6.97% vs 1.40%. This
is a volatility selector, not a directional one.

## K. MFE / MAE

| set | MFE mean | MAE mean | P(MFE>=1/2/3/5%) | MAE lift |
|---|---|---|---|---|
| baseline | +73.6 | -79.3 | 21.9 / 8.0 / 3.8 / 1.2% | - |
| H1 | +63.4 | -69.7 | 18.1 / 5.9 / 2.5 / 0.8% | +9.7 |
| H2 | +109.0 | -114.5 | 29.8 / 14.3 / 8.2 / 3.5% | -35.2 |
| H3 | +79.9 | -88.2 | 23.8 / 8.7 / 3.9 / 1.4% | -8.9 |
| H4 | +77.5 | -86.8 | 22.9 / 8.2 / 3.7 / 1.3% | -7.5 |
| H5 | +215.5 | -194.5 | 51.5 / 26.3 / 15.8 / 6.4% | -115.2 |

Excursions widen symmetrically with after-hours intensity. Nothing here shows an asymmetric
path an exit rule could harvest without a model of which side comes first, and this phase does
not build one.

## L. Gap Decomposition (after close -> entry, entry -> exit)

| set | N | after close -> 08:00 entry | entry -> 09:25 | after close -> 09:25 | corr (Pearson / Spearman) |
|---|---|---|---|---|---|
| baseline | 90,318 | +13.5 (median +8.8) | -8.0 | +5.4 | -0.01 / -0.02 |
| H1 | 35,766 | -4.2 (0.0) | -6.9 | -11.1 | -0.01 / -0.02 |
| H2 | 5,242 | -2.6 (-0.1) | -7.4 | -9.1 | +0.13 / -0.05 |
| H3 | 12,948 | -2.8 (-0.7) | -8.9 | -11.7 | +0.02 / +0.01 |
| H4 | 9,302 | -10.2 (-7.9) | -8.1 | -18.3 | +0.01 / -0.02 |
| H5 | 373 | **-54.4 (-27.6)** | -23.4 | **-76.3** | +0.12 / -0.09 |

The after-hours strength is **not** already priced into a higher 08:00 entry; it is partly
**given back overnight**. H5's rows lose a median 28 bp between the 20:00 print and the 08:00
entry and a further 23 bp by 09:25. On the baseline, the 08:00 -> 09:25 return falls with the
size of the overnight gap in either direction beyond +0.5% (+0.5..2% -8.8, +2..5% -16.7, >= +5%
-23.3 bp); large gaps up fade, the familiar premarket pattern, independent of G's features.

## M. Price Neutralization / N. Liquidity Neutralization

Coarsened exact matching on close price x regular dollar volume x after-hours dollar volume x
T+1 pre-entry premarket dollar volume (E1-H5 estimators, unchanged):

| set | raw lift | matched lift (session-demeaned) | matched N | match rate | same-session lift (N, rate) | matched 95% CI |
|---|---|---|---|---|---|---|
| H1 | +1.1 | +0.1 | 35,683 | 1.00 | +0.5 (26,650, 0.75) | [-1.5, +1.6] |
| H2 | +0.6 | +0.4 | 5,226 | 1.00 | +0.6 (3,805, 0.73) | [-5.8, +6.4] |
| H3 | -0.9 | -1.0 | 12,925 | 1.00 | -1.4 (10,786, 0.83) | [-3.6, +1.5] |
| H4 | -0.1 | -1.5 | 9,269 | 1.00 | -3.0 (7,481, 0.81) | [-5.3, +2.4] |
| H5 | -15.4 | -10.9 | 372 | 1.00 | -6.9 (282, 0.76) | [-33.2, +10.7] |

Balance tables are in `results.json` (candidate vs control bucket shares per cell variable). After
neutralization every lift is within +-1.5 bp of zero except H5, which stays negative. There is
no raw effect for price or liquidity to explain away. Baseline gradients alone (section H): $5-10
names -16.1 bp vs $200+ -3.8 bp; $5-20M regular volume -16.7 bp vs $500M+ -4.8 bp.

## O. Extreme Removal

| set | original mean lift | drop top 1 | drop top 5 | drop top 1% | P(>=2%) lift after top 1% |
|---|---|---|---|---|---|
| H1 | +1.1 | +1.0 | +0.8 | **-3.8** | -1.92pp |
| H2 | +0.6 | -0.2 | -1.9 | **-9.4** | +2.01pp |
| H3 | -0.9 | -1.1 | -1.5 | -6.3 | -1.04pp |
| H4 | -0.1 | -0.5 | -1.2 | -6.6 | -1.07pp |
| H5 | -15.4 | -18.3 | -26.3 | -24.9 | +5.75pp |

H1's and H2's slightly positive lifts are made by their top 1% of trades.

## P. Symbol Concentration

| set | unique symbols | top-1 / top-5 / top-10 share of excess | HHI (positive excess) | leaders |
|---|---|---|---|---|
| H1 | 1,637 | 12.3% / 45.9% / 78.2% | 0.0039 | SOFI (214 rows), AVGO (207), ALAB (159), WEAV (3), VRT (144) |
| H2 | 1,343 | 148% / 479% / 741% | 0.0051 | ALMS (8), WEAV (1), AMLX (6), ABM (4), W (1) |
| H3 | 1,172 | n/m (total excess negative) | 0.0053 | ALAB, SOFI, AMPX, AVGO, MARA |
| H4 | 1,283 | n/m (total excess negative) | 0.0052 | WEAV, VRT, SOFI, QS, ACHR |
| H5 | 253 | n/m (total excess negative) | 0.0205 | AAOI, FRMI, AMPX, HNRG, ORCL |

Shares above 100% or negative mean the total excess is near zero or negative, so the few
leaders explain more than all of it (H2: five names produce 4.8x the net excess). H1's top 10
names hold 78% of its small excess.

## Q. Time Consistency

Months with >= 20 candidate rows and a positive mean lift: H1 12/24, H2 14/24, H3 8/24,
H4 14/24, H5 1/5 (gate: >= 65% of >= 8 months). Quarterly mean lift (bp):

| set | 24Q4 | 25Q1 | 25Q2 | 25Q3 | 25Q4 | 26Q1 | 26Q2 | 26Q3 |
|---|---|---|---|---|---|---|---|---|
| H1 | -1.9 | +3.0 | +5.0 | -3.4 | -0.9 | -2.1 | +1.8 | +0.1 |
| H2 | -11.2 | +11.9 | +0.8 | -3.4 | +3.2 | +18.0 | -4.1 | +5.2 |
| H3 | +0.8 | +1.7 | +3.7 | -3.8 | -0.0 | -0.6 | -3.0 | -1.4 |
| H4 | +2.0 | +8.3 | +8.5 | -1.2 | +7.2 | -7.4 | -8.0 | +0.2 |
| H5 | -1.8 | -49.0 | -34.9 | +9.4 | -24.7 | +56.9 | -19.4 | -15.5 |

Halves (split 2026-05-04) and tape era (before / from 2026-04-20) agree: no hypothesis holds a
material positive lift in both. Baseline months: 16 of 24 negative, the broad-tape months
2026-05 and 2026-06 the worst (-14.7, -23.0 bp).

## R. Regime (diagnostic only)

| regime | baseline mean | H1 | H2 | H3 | H4 | H5 (N) |
|---|---|---|---|---|---|---|
| SPY(T) up | -14.0 | +3.0 | -3.1 | -2.4 | -2.2 | -25.2 (226) |
| SPY(T) down | -1.2 | -0.5 | +6.0 | +2.4 | +4.5 | +2.2 (147) |
| volatility high | -11.3 | +2.3 | +1.6 | -1.0 | -1.1 | -20.0 (263) |
| volatility low | -2.7 | -1.0 | -0.5 | -1.0 | +1.6 | -1.5 (110) |
| risk-on (breadth >= 50%) | -9.4 | +2.1 | -0.6 | -1.6 | -1.3 | -21.7 (187) |
| risk-off | -6.6 | +0.2 | +1.8 | 0.0 | +1.8 | -9.0 (186) |

(mean lift, bp.) No regime turns a hypothesis into a +20 bp effect; none is added to a rule.

## S. Weekend / Holiday

| pair kind | baseline N / mean | H1 | H2 | H3 | H4 | H5 (N) |
|---|---|---|---|---|---|---|
| normal overnight | 70,806 / -7.9 | +1.7 | +1.4 | +0.5 | +1.0 | -11.7 (338) |
| weekend gap | 15,954 / -9.3 | -1.2 | -5.1 | -7.9 | -6.9 | -44.6 (28) |
| holiday gap | 3,558 / -5.1 | 0.0 | +7.4 | -6.3 | +1.8 | -78.2 (7) |

Weekends are worse for every hypothesis; excluding them (not allowed after the fact) would still
leave H1-H4 below +2 bp.

## T. Event Limitation

```text
EVENT_CLASSIFICATION = UNAVAILABLE
```

The only event store in the repository is the SEC EDGAR raw store of Strategy C-E0, which covers
the C-M0 candidate CIKs only. No earnings, guidance, offering, FDA, M&A or news classification
exists for the G universe, and none was fetched. After-hours moves are dominated by earnings
releases; G0 cannot separate an earnings reaction from any other after-hours move.

## U. Cost Stress (round trip, bp of entry notional, once per trade)

| set | gross | @10 | @20 | @30 | @50 | @75 | @100 | break_even_cost_bp |
|---|---|---|---|---|---|---|---|---|
| baseline | -8.0 | -18.0 | -28.0 | -38.0 | -58.0 | -83.0 | -108.0 | **-8.0** |
| H1 | -6.9 | -16.9 | -26.9 | -36.9 | -56.9 | -81.9 | -106.9 | **-6.9** |
| H2 | -7.4 | -17.4 | -27.4 | -37.4 | -57.4 | -82.4 | -107.4 | **-7.4** |
| H3 | -8.9 | -18.9 | -28.9 | -38.9 | -58.9 | -83.9 | -108.9 | **-8.9** |
| H4 | -8.1 | -18.1 | -28.1 | -38.1 | -58.1 | -83.1 | -108.1 | **-8.1** |
| H5 | -23.4 | -33.4 | -43.4 | -53.4 | -73.4 | -98.4 | -123.4 | **-23.4** |

No hypothesis breaks even at zero cost.

## V. Execution Liquidity (primary entry bar)

| set | signal rows | fill rate | entry-bar $ P5 / P25 / P50 / P75 / P95 | exact 08:00 | no print in prior 30m | entry bar < $1k |
|---|---|---|---|---|---|---|
| baseline | 135,252 | 66.8% | 2.1k / 12.7k / 63.8k / 471k / 14.9M | 60.0% | 18.9% | 1.3% |
| H1 | 52,422 | 68.2% | 2.4k / 15.6k / 77.4k / 554k / 16.5M | 61.7% | 19.2% | 1.0% |
| H2 | 8,862 | **59.2%** | 2.0k / 12.0k / 54.5k / 381k / 17.7M | 58.3% | 19.6% | 1.3% |
| H3 | 16,499 | 78.5% | 2.3k / 15.9k / 99.0k / 879k / 24.7M | 65.3% | 12.8% | 1.3% |
| H4 | 14,754 | 63.0% | 2.3k / 14.6k / 66.3k / 479k / 19.0M | 61.5% | 22.0% | 1.3% |
| H5 | 463 | 80.6% | 2.0k / 18.7k / 95.7k / 2.1M / 88.1M | 72.7% | 7.5% | 1.6% |

Entry delay: median 0 minutes, P75 3, P95 11. Execution is not what fails G: medians of $55-99k
per entry bar are thin but usable, and H1/H3/H4/H5 clear the 60% fill rate. H2 misses it (59.2%).

## W. PIT Audit

```text
after_feature_cutoff_1959_and_future_poison   PASS  40 symbols, 120 cuts, 8,701 symbol-days, 0 mismatched fields
row_next_session_is_calendar_next             PASS  135,252 rows, 0 wrong
synthetic future-bar leakage (test suite)     a builder that reads T+1 premarket into p2000 FAILS the audit (as required)
daily inputs unchanged after the run          true (read-set 7e790a8d...)
tape unchanged after the run                  true
```

The cutoff audit replaces every bar after 19:59 of a cut day (T+1 premarket and all labels
included) with noise and requires every source field, derived feature and H1-H5 mask up to the
cut to be bit-identical, RVOL denominator included.

## X. Regression

```text
backend/tests/strategy_g0                                   20 passed
strategy_g0 + strategy_e0 + strategy_e1 + strategy_e1_h5    105 passed   (reused infrastructure)
full backend/tests                                          4305 passed, 1 skipped (20m56s)
  ignored: strategy_b/test_strategy_b_scanner.py, test_strategy_b_historical_scanner.py
  (pre-existing collection errors from untracked Strategy B work, not touched)
```

No tracked file was modified; A/E paper runtime, server and cost contracts are untouched.

## Y. Verdict

**FAIL.**

* No hypothesis has a positive gross mean on the primary label: H1 -6.9, H2 -7.4, H3 -8.9,
  H4 -8.1, H5 -23.4 bp against a -8.0 bp baseline. Break-even cost is negative for all five
  (gate: >= +50 bp).
* The largest mean lift is H1's +1.1 bp (gate +20 bp); its weaker bootstrap interval is
  [-1.7, +3.9] bp (session) and it turns to -3.8 bp once the top 1% of trades is removed.
* The only real tail effect (H2, H5: P(>=2%) x2.0 / x3.3, P(MFE>=3%) +4.5 / +12.1pp, intervals
  above zero) is two-sided: P(<=-2%) x1.9 / x3.4. It is a volatility selector.
* After price and liquidity matching every lift is within +-1.5 bp of zero (H5 -10.9 bp).
* Positive months: 12/24, 14/24, 8/24, 14/24, 1/5.
* Strong after-hours closes give back part of the move before 08:00 (H5 median -28 bp from the
  20:00 print to the entry), so waiting for 08:00 buys the reversal, not the continuation.

Failed criteria: H1 {mean, tail, extreme removal, concentration, bootstrap, time,
neutralization, cost}; H2 {mean, extreme removal, concentration, downside, bootstrap, time,
neutralization, cost, execution}; H3 and H4 {mean, tail, extreme removal, bootstrap, time,
neutralization, cost}; H5 {sample, mean, median, extreme removal, downside, bootstrap, time,
neutralization, cost}.

G0 ends here. Per the fail policy nothing is re-tuned on this data.

## Z. If PASS

Not applicable.

## NEW CANDIDATE IDEA (recorded only, not tested, not a rescue of G)

Observed post hoc on the same data, so neither is evidence; each would need its own
declaration and data that G0 has not seen.

```text
Status of both items: OBSERVATIONAL ONLY / POST-HOC / NOT EVIDENCE /
NOT PART OF STRATEGY G / NOT AUTHORIZED FOR DEVELOPMENT (no candidate number assigned)
```

1. **Extreme after-hours event continuation.** The single after_return bucket >= +10% (306
   rows) averages +70.5 bp (median +46.6 bp, win 56%) from 08:00 to 09:25, the only after-hours
   bucket with a positive mean. This is almost certainly an earnings/news population, so it
   first needs the event classification G0 lacks.
2. **Premarket fade of regular-session extremes (short side).** Regular day return >= +5%
   (-26.1 bp), close >= 2% above the regular VWAP (-30.7 bp) and an overnight gap >= +5% into
   08:00 (-23.3 bp) all drift down from 08:00 to 09:25. A short-side question is outside G's
   declared long thesis.

## AA. Next Step

1. Record Strategy G as **G0 FAIL / CLOSED**; no G-D0..G-D6 work.
2. Do not re-tune G on this data (RVOL, range cut, entry or exit minute).
3. If idea 1 is pursued, the prerequisite is an event (earnings calendar) source for the whole
   universe, declared and collected before any return is read.
4. Any successor must be evaluated on sessions G0 has not used (after 2026-09-16) or on a new
   historical purchase, not on this window.
5. Commit of the G0 files is the user's decision; A/E runtime was not touched.

---

## Closeout

```text
FINAL VERDICT: FAIL

Strategy G Status:
G0 FAIL / CLOSED

Reason:
After-hours information alone did not provide robust evidence
for selecting next-session Premarket upside opportunities.

H1~H5 all failed the preregistered gate.

No same-data retuning is permitted.

G-D0 and later development stages are not authorized.
```

Closed 2026-09-27 on run `g0-e9a66655d3fb`, rules canonical
`4cb4e0eb9b9eade380421d5e549b31af12cee5c16329fc85089d59bc78a6ccaf` (verified unchanged at closeout).
Run artifacts stay under the git-ignored `data/runtime/strategy_g_candidate/` (repository convention
`data/runtime/*`); every number quoted here comes from that run.
