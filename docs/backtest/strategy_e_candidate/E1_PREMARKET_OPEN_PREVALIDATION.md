# Strategy E1 - Premarket to Opening-Momentum Pre-Validation

| | |
|---|---|
| Research id | `e1-premarket-v0` (`PREMARKET_OPEN_MOMENTUM_V0`) |
| Run id | `e1-32a530c8af9a` |
| Rules | `e1_premarket_rules_v1.json`, canonical `951b973b1c848ef8…`, frozen 2026-09-20 after the coverage audit and before any return was computed |
| Daily universe | E0 declaration `078aa79231f075a4…`, snapshot `USB-HIST-V1` (FROZEN), freeze `9ebd6c29c667…` |
| Premarket tape | digest `d12ff28a98cf9cc6…`, 1,902 symbols, 2,152 files; identical at the end of the run |
| Read-set digest | `7e790a8dcf1d…` before and after (`daily_inputs_unchanged: true`) |
| Provider calls | 0. Writer lock taken: no |
| Artifacts | `data/runtime/strategy_e_candidate/runs/e1-32a530c8af9a/` |
| Git HEAD at run | `4e84ec12a1fa176a19807f0d966f45925623763b` |
| **Verdict** | **INCONCLUSIVE** |

E1 is a new question, not a retune of E0. E0 (`STRONG_CLOSE -> OVERNIGHT LONG`) is CLOSED as FAIL,
is not modified here, and none of its findings - its strong-close mean reversion included - enters
an E1 feature, filter or rule. E0's daily point-in-time universe loader and its statistics module
are imported unchanged, as infrastructure only. The B-minute collector was running throughout;
E1 took no lock, wrote nothing into the minute store, and bound itself to a tape digest that was
unchanged when the run finished.

---

## A. Data Coverage

```text
PREMARKET DATA COVERAGE (04:00-09:25 ET, MASSIVE_TICKER_AGGREGATE adjusted=false)

start_date:        2024-09-17
end_date:          2026-09-16
trading_sessions:  501 on the grid, 480 used
unique_symbols:    1,869 with a tape (1,902 directories bound)
symbol_sessions:   204,104 with any bar

regular_open coverage:      202,818 symbol-sessions have the 09:30 bar (99.4%)
09:30-09:45 coverage:       203,984 symbol-sessions have at least one bar (99.9%)
```

| Premarket window | bars | symbol-sessions | with a 09:30 open | share of tape |
|---|---|---|---|---|
| 04:00-09:25 (any) | 4,902,571 | 137,919 | 137,243 | 67.2% |
| 08:00-09:25 | 1,950,684 | 109,536 | 109,016 | 53.4% |
| 09:00-09:25 | 649,335 | 82,470 | 82,104 | 40.2% |
| 09:15-09:25 | 289,957 | 65,814 | 65,549 | 32.1% |

**Premarket minute bars exist only where a trade printed.** The median symbol-session with any
premarket activity has **4 bars** in five and a half hours (P25 2.4, P75 9.8, P90 32.3). Only 233
of 1,869 symbols have any 09:00-09:25 print on 90% or more of their sessions. This is the single
most important fact about E1 and it shapes every number below: a premarket "return" is often the
difference between two prints, not a path.

**The sample is heavily concentrated in the last five months.** The broad minute collection covers
2026-04-20 onward; before that only the 30-symbol `MINUTE_UNIVERSE_V1` has a tape. After the
declared universe is applied, 2024Q4 through 2026Q1 contribute 1,461 to 1,856 rows **per quarter**
while 2026Q2 and 2026Q3 contribute 28,710 and 31,636. 85% of the study lives in two quarters, and
the earlier six are a mega-cap universe answering a narrower question.

**Two label readings, because the declared bar is often absent.** Across the whole tape the exact
09:34 bar exists on 75.4% of sessions with an open bar and the 09:44 bar on 77.3%, and the absence
correlates with thinness. Every label is therefore computed twice: the primary reading takes the
last print at or before the declared minute (the price you would mark to), the strict reading takes
the exact declared bar. Inside the E1 universe, which requires premarket activity, the strict
reading is undefined on only 5.4% of rows, and **the two readings agree throughout** (H5: +16.54 bp
of lift primary, +17.77 bp strict). Nothing in this report turns on the choice.

---

## B. Universe

Point-in-time, declared before any return was computed. Daily eligibility is evaluated at **D-1**,
so every daily input to a 09:25 decision is known at the previous close; this is structural, not
checked afterwards, because E1 looks up E0's row for the previous grid session.

| Filter | Rows removed |
|---|---|
| premarket rows on the tape | 138,615 |
| fewer than 3 premarket bars in 04:00-09:24 | 45,483 |
| premarket dollar volume below $50,000 | 17,346 |
| session not on the XNYS grid | 28 |
| no E0 daily row at D-1 (CS membership, $5 price, $5M liquidity, no split in (D-1, D]) | 5,020 |
| **rows kept** | **70,738** (480 sessions, 1,612 tickers) |

Features that need a window keep their own coverage: `return_0900_0925` is undefined on 23,216 of
the 70,738 rows (32.8%), `return_0915_0925` on 35,234 (49.8%), `return_last30m` on 20,823 (29.4%),
`premarket_rvol` on 3,191 (4.5%). Those rows stay in the baseline and leave the affected buckets
and hypotheses, which is why each block below reports its own N.

---

## C. Baseline Opening Return

All 70,738 rows, measured from the **open of the 09:30 bar**.

| label | N | mean(bp) | median(bp) | win(%) | P5 | P95 |
|---|---|---|---|---|---|---|
| R_1M | 70,738 | +0.20 | +0.00 | 48.92 | -1.26% | +1.24% |
| **R_5M (primary)** | 70,738 | **-0.08** | **+0.00** | **49.14** | -1.99% | +2.01% |
| R_5M strict | 66,897 | -0.13 | +0.00 | 49.19 | -2.03% | +2.04% |
| R_15M | 70,738 | -0.56 | -0.82 | 49.19 | -2.64% | +2.65% |
| MFE_5M | 70,738 | +89.11 | +60.33 | 91.74 | 0.00% | +2.72% |
| MAE_5M | 70,738 | -87.50 | -59.38 | 0.00 | -2.69% | 0.00% |
| MFE_15M | 70,738 | +117.52 | +79.78 | 93.93 | 0.00% | +3.56% |
| MAE_15M | 70,738 | -116.03 | -79.51 | 0.00 | -3.52% | 0.00% |

Probabilities on R_5M: P(>= +0.5%) 28.17%, P(>= +1%) 15.40%, P(>= +2%) 5.05%, P(>= +3%) 1.92%;
P(<= -0.5%) 28.86%, P(<= -1%) 15.65%, P(<= -2%) 4.97%, P(<= -3%) 1.79%.

This baseline is **a coin flip with no drift**, which is a cleaner reference than E0's overnight
window: mean -0.08 bp, median exactly zero, win rate 49.14%, and the up and down tails matched to
within 0.7 points of probability at every threshold. Any lift measured against it is signal
rather than a share of a pre-existing drift.

The MFE and MAE columns matter as much as the R columns. In the first five minutes the average
stock travels +89 bp up and -88 bp down from its open. The edge sizes found below are 5 to 17 bp.
**The noise is five to twenty times the signal**, which is the dominant practical fact of section O.

Baseline by quarter, with the sample imbalance visible:

| quarter | N | mean(bp) | median(bp) | win(%) |
|---|---|---|---|---|
| 2024Q4 | 1,461 | -3.31 | -1.76 | 48.67 |
| 2025Q1 | 1,669 | +8.04 | +4.34 | 52.79 |
| 2025Q2 | 1,783 | +9.75 | +8.67 | 53.90 |
| 2025Q3 | 1,855 | -4.95 | -3.27 | 47.28 |
| 2025Q4 | 1,856 | -3.38 | -2.63 | 48.17 |
| 2026Q1 | 1,768 | +1.34 | +1.51 | 50.74 |
| 2026Q2 | 28,710 | -5.39 | -4.33 | 47.43 |
| 2026Q3 | 31,636 | +4.29 | +1.16 | 50.33 |

---

## D. Single Feature Results

Fixed bucket edges, declared before any result. Lift is against the R_5M baseline.

### `position_in_premarket_range` - the cleanest feature in the study

| bucket | N | mean(bp) | median(bp) | win(%) | lift(bp) | P(>=1%) | P(<=-1%) |
|---|---|---|---|---|---|---|---|
| [0, 0.2) | 19,101 | -2.01 | -1.62 | 48.34 | -1.93 | 14.61% | 16.02% |
| [0.2, 0.5) | 17,142 | -2.11 | -0.54 | 48.89 | -2.03 | 15.87% | 16.68% |
| [0.5, 0.8) | 16,857 | +0.14 | -0.29 | 48.94 | +0.23 | 15.59% | 15.73% |
| [0.8, 0.95) | 7,923 | +1.89 | +0.00 | 49.74 | +1.98 | 16.41% | 16.17% |
| [0.95, 1.0] | 9,616 | +5.23 | +2.43 | 51.05 | **+5.31** | 14.98% | **12.65%** |

Monotone in mean, median **and** win rate, and - the part that separates it from a volatility
selector - the up tail is flat across the range while the **down tail falls** from 16.02% to
12.65%. A stock sitting at the top of its premarket range opens with the same chance of a 1% pop
and a materially smaller chance of a 1% drop. This is a distribution shift, not a widening.

### `premarket_gap` - not monotone, and the big gaps reverse

| bucket | N | mean(bp) | median(bp) | win(%) | lift(bp) | P(>=1%) | P(<=-1%) |
|---|---|---|---|---|---|---|---|
| < 0 | 30,993 | -0.45 | +0.00 | 49.00 | -0.37 | 15.43% | 15.96% |
| 0 to 1% | 23,609 | -0.72 | +0.00 | 48.97 | -0.64 | 11.39% | 11.94% |
| 1 to 2% | 8,555 | +1.72 | +0.00 | 49.76 | +1.81 | 17.55% | 16.76% |
| 2 to 5% | 6,011 | +3.05 | +0.00 | 49.84 | +3.13 | 23.79% | 22.54% |
| 5 to 10% | 1,210 | -7.39 | **-11.70** | 48.02 | **-7.30** | 29.59% | 32.40% |
| 10%+ | 360 | +2.90 | -8.01 | 49.44 | +2.99 | 37.50% | 35.56% |

The gap on its own is close to worthless and it turns against you where it is largest: a 5-10% gap
has a median opening five-minute return of **-11.70 bp** and a 32.40% chance of losing another 1%.
Whatever E1 finds, it is not "buy the gap".

### `premarket_rvol` - the E0 failure mode, restated

| bucket | N | mean(bp) | median(bp) | win(%) | lift(bp) | P(>=1%) | P(<=-1%) |
|---|---|---|---|---|---|---|---|
| < 1 | 25,247 | -1.56 | -1.69 | 48.57 | -1.48 | 13.60% | 14.22% |
| 1 to 2 | 17,226 | +0.88 | +0.00 | 49.63 | +0.97 | 15.59% | 15.23% |
| 2 to 3 | 7,600 | +0.71 | +0.00 | 49.47 | +0.80 | 15.83% | 16.08% |
| 3 to 5 | 6,517 | +4.16 | +0.82 | 50.21 | +4.24 | 16.54% | 15.07% |
| 5+ | 10,957 | +0.54 | +0.00 | 49.66 | +0.63 | 18.45% | 19.05% |

Non-monotone. The top bucket widens both tails (18.45% / 19.05% against a 15.40% / 15.65% baseline)
and gives back almost all of the mean. The `[3, 5)` bucket is the only one that lifts the mean and
the median together without a symmetric tail, which is why the declared `rvol >= 3` threshold is
the useful part of RVOL and `rvol >= 5` is not.

### Others

| feature | shape | notable |
|---|---|---|
| `distance_to_premarket_high` | U-shaped | far below the high +8.25 bp but with 32% tails on both sides; within 0.2% of the high +2.34 bp with the **lowest** tails in the table (11.85% / 11.00%) |
| `relative_strength_vs_spy` | weak, U-shaped | +1.14 bp just above zero, +4.96 bp at the negative extreme with 25% tails; not a clean signal |
| `premarket_dollar_volume` | slightly decreasing | the smallest bucket ($50k-250k) is the best at +2.03 bp; large premarket value predicts nothing |
| `previous_day_dollar_volume` | decreasing | $5-20M +7.51 bp, $500M+ -0.94 bp |
| `close_price` | decreasing | $5-10 +6.50 bp with 21.7% / 20.0% tails; $200+ -3.28 bp |

The cheap-and-small effects come with wide tails and are the usual small-cap variance premium
rather than a premarket finding.

---

## E. H1~H5 Results

All five declared before any of section D was computed. Thresholds come from the declared bucket
edges only. No sweep, no optimizer.

| set | rule | N | mean(bp) | median(bp) | win(%) | mean lift(bp) | win lift(%p) |
|---|---|---|---|---|---|---|---|
| baseline | all rows | 70,738 | -0.08 | +0.00 | 49.14 | - | - |
| H1 | gap > 0 and 09:00-09:25 return > 0 | 13,842 | -0.86 | -0.30 | 49.01 | -0.77 | -0.13 |
| H2 | gap > 0 and RVOL >= 3 | 9,864 | +3.24 | +0.50 | 50.07 | +3.33 | +0.93 |
| H3 | position >= 0.8 and last-30m return > 0 | 8,657 | +4.80 | +2.07 | 50.79 | +4.88 | +1.65 |
| H4 | gap > 0 and rel. strength > 0 and $1M premarket | 14,744 | -0.68 | -1.94 | 48.87 | -0.59 | -0.27 |
| **H5** | **gap > 0 and RVOL >= 3 and position >= 0.8 and 09:00-09:25 return > 0** | **1,729** | **+16.45** | **+6.51** | **52.46** | **+16.54** | **+3.32** |

H1 and H4 are worse than doing nothing. H2 and H3 are mildly positive but well short of the
declared +15 bp. H5 - all four conditions stacked - is the only candidate that clears the declared
effect size, and it clears it on mean, median, win rate and bootstrap together.

H5 detail: P(>= +1%) 23.83% against a 15.40% baseline, P(<= -1%) 17.87% against 15.65%. Both tails
rise, but the up tail rises 8.4 points against the down tail's 2.2, and the median rises - so it is
**not** flagged as a volatility selector under the declared test.

---

## F. Open -> 1m / 5m / 15m

| set | R_1M lift | R_5M lift | R_15M lift | R_15M median |
|---|---|---|---|---|
| gap only | +2.39 | +0.36 | -1.40 | -2.65 bp |
| gap + volume (H2) | +1.76 | +3.33 | -1.22 | -1.64 bp |
| gap + closing strength | +5.35 | +4.53 | +3.51 | +0.00 bp |
| H1 | +2.81 | -0.77 | -4.62 | -5.24 bp |
| H5 | +6.49 | **+16.54** | +5.70 | **-3.29 bp** |

**Continuation peaks at five minutes and is gone by fifteen.** H5 earns +16.54 bp by 09:35 and
gives two thirds of it back by 09:45, where its median is already negative. H1 is positive at one
minute and negative at five and fifteen - short-horizon premarket momentum reverses almost
immediately. Only `gap + closing strength` holds a positive lift across all three horizons, and it
does so at a modest +3.5 to +5.4 bp.

The declared question "how far does continuation extend" therefore has a clear answer: **to about
five minutes, and not to fifteen.**

---

## G. MFE / MAE

| set | MFE_5M | MAE_5M | MFE_15M | MAE_15M |
|---|---|---|---|---|
| baseline | +89.11 | -87.50 | +117.52 | -116.03 |
| gap + closing strength | +89.73 (+0.62) | **-78.73 (+8.77)** | +116.02 (-1.49) | **-106.72 (+9.32)** |
| H2 | +108.77 (+19.66) | -100.48 (-12.99) | +139.97 (+22.45) | -134.34 (-18.30) |
| H5 | +132.34 (+43.23) | -108.86 (-21.36) | +165.08 (+47.57) | -145.23 (-29.19) |

Two different mechanisms are visible. `gap + closing strength` leaves the upside excursion
untouched and **shrinks the downside** by 9 bp at both horizons: it is selecting stocks that fall
less. H2 and H5 widen both excursions, H5 by +43 bp up against -21 bp down. H5's edge is therefore
a favourably skewed widening, not a quiet drift, and an exit rule would decide most of whether it
survives - which is precisely the work this phase is not allowed to do.

---

## H. Downside Tail

| set | P(<=-0.5%) | P(<=-1%) | P(<=-2%) | ratio at -1% | P5 |
|---|---|---|---|---|---|
| baseline | 28.34% | 15.65% | 5.07% | 1.00 | -1.99% |
| H1 | 29.90% | 16.80% | 5.87% | 1.07 | -2.19% |
| H2 | 28.85% | 16.88% | 6.72% | 1.08 | -2.37% |
| H3 | 28.37% | 15.25% | 4.99% | **0.97** | -2.00% |
| H4 | 30.67% | 17.91% | 6.61% | 1.14 | -2.31% |
| H5 | 28.74% | 17.87% | 7.40% | 1.14 | -2.59% |
| gap + closing strength | 27.07% | 14.24% | 4.34% | **0.91** | -1.87% |

No candidate approaches the declared 1.5x limit. H3 and `gap + closing strength` actually *reduce*
the chance of a 1% loss while lifting the mean, which is the most attractive property found
anywhere in E1 even though their effect sizes are small.

---

## I. Time Consistency

Mean lift by quarter, against each quarter's own baseline:

| set | 24Q4 | 25Q1 | 25Q2 | 25Q3 | 25Q4 | 26Q1 | 26Q2 | 26Q3 | positive |
|---|---|---|---|---|---|---|---|---|---|
| H1 | +10.6 | -5.1 | -13.8 | +6.7 | +5.2 | -1.2 | -1.8 | -0.4 | 3 / 8 |
| H2 | -4.5 | +22.2 | -18.6 | +10.2 | -7.5 | -26.5 | +8.9 | -0.4 | 3 / 8 |
| H3 | +2.9 | +3.4 | -4.3 | +10.8 | +12.9 | +3.7 | +9.1 | +0.9 | **7 / 8** |
| H4 | +3.5 | +4.0 | -13.9 | +5.8 | +2.9 | -2.7 | -0.3 | -1.4 | 4 / 8 |
| H5 | +12.8 | +29.8 | -17.8 | +36.5 | -21.3 | +11.5 | +29.3 | +8.9 | **6 / 8** |
| gap + closing strength | +9.1 | +9.8 | -4.8 | +13.6 | +13.5 | +7.4 | +7.9 | +0.4 | **7 / 8** |

**The quarterly counts make six of these eight columns nearly meaningless for H5.** Its rows per
quarter are 43, 21, 43, 35, 24, 29, 603, 931: 88.7% of H5 sits in the last two quarters. Its
"6 of 8" is decided by quarters holding two or three dozen rows each. The honest reading of H5's
time consistency is not "six quarters out of eight" but "two well-populated quarters, both
positive (+29.3 and +8.9), and six quarters too small to say anything".

Restricted to the well-populated period (2026Q2 and 2026Q3, 1,534 of 1,729 rows), H5 averages
+17.4 bp against a -0.3 bp baseline, so the effect does not depend on the thin early quarters. But
it **decays in the most recent quarter**, from +29.3 bp to +8.9 bp - and the same decay appears in
H3 (+9.1 to +0.9) and in `gap + closing strength` (+7.9 to +0.4). The most recent quarter is the
one a live study would have traded.

First half / second half (split 2026-06-22): H5 +25.4 bp then +8.5 bp; H3 +10.2 then -0.4;
`gap + closing strength` +7.9 then -0.4. The same fade, seen a second way.

### Regime

| regime | baseline mean | H3 lift | H5 lift | H5 N |
|---|---|---|---|---|
| SPY premarket negative | +3.10 bp | +6.63 | +14.37 | 583 |
| SPY premarket positive | -2.43 bp | +4.87 | +18.37 | 1,146 |
| trailing vol high | -2.95 bp | +4.34 | +16.13 | 835 |
| trailing vol low | +2.86 bp | +5.04 | +16.64 | 894 |

H5 is positive in all four regimes with similar magnitude, which is the strongest thing that can be
said for it. Both regime labels are knowable before the decision time; SPY's same-day return was
deliberately not used as a regime label because it is not.

### Decision-time robustness

| decision | rows | H2 lift | H3 lift | H4 lift | H5 lift | H5 N |
|---|---|---|---|---|---|---|
| 09:25 (primary) | 70,738 | +3.33 | +4.88 | -0.59 | +16.54 | 1,729 |
| 09:15 | 68,728 | +2.53 (vol. selector) | +5.57 | -0.97 | +14.78 | 1,221 |
| 09:00 | 65,864 | +2.66 (vol. selector) | +4.69 | -1.01 | n/a | 0 |

H5 and H3 hold their sign and rough size ten minutes earlier, which argues the effect is not an
artifact of the last few minutes before the bell. H2 is flagged as a volatility selector at both
earlier times. H1 and H5 cannot be evaluated at 09:00 at all: at 09:00 the last closed bar starts
at 08:59, so `return_0900_0925` has no value by construction and the rules were not bent to give
it one.

---

## J. Liquidity / Price Robustness

H5's own mean, by previous-day dollar volume and by price (bucket counts are small, so these are
gradients rather than estimates):

| previous-day dollar volume | N | H5 mean(bp) | | price | N | H5 mean(bp) |
|---|---|---|---|---|---|---|
| $5-20M | 145 | **+57.85** | | $5-10 | 116 | **+61.70** |
| $20-100M | 418 | +13.97 | | $10-20 | 222 | +34.78 |
| $100-500M | 578 | +15.63 | | $20-50 | 410 | +11.23 |
| $500M+ | 584 | +8.67 | | $50-200 | 640 | +10.59 |
| | | | | $200+ | 341 | +6.42 |

**H5's edge is strongly concentrated in small and cheap names.** The $5-10 bucket averages +61.70 bp
on 116 rows while the $200+ bucket averages +6.42 bp on 341, and the same gradient runs through
previous-day dollar volume. Section D shows the universe-wide opening five-minute mean already
falls with size (+6.50 bp at $5-10, -3.28 bp at $200+) and with previous-day volume (+7.51 bp at
$5-20M, -0.94 bp at $500M+), both with wider tails.

So part of H5's +16.5 bp is the small-cap variance premium that is present in the baseline too, and
E1 cannot say how much. Deconfounding "premarket momentum" from "small and cheap" would need
either a size-neutral comparison or a sample large enough to run H5 inside each bucket - neither of
which 1,729 rows supports. This is a second, independent reason the verdict is not PASS.

---

## K. Extreme Removal

Mean lift after dropping the largest positive outcomes:

| set | full | drop top 1 | drop top 5 | drop top 1% |
|---|---|---|---|---|
| H1 | -0.77 | -0.86 | -1.20 | -6.50 |
| H2 | +3.33 | +3.20 | +2.72 | -3.79 |
| H3 | +4.88 | +4.74 | +4.23 | -0.94 |
| H4 | -0.59 | -0.68 | -1.00 | -6.68 |
| **H5** | **+16.54** | **+15.83** | **+13.38** | **+8.29** |
| gap only | +0.36 | +0.32 | +0.20 | -5.05 |
| gap + closing strength | +4.53 | +4.45 | +4.15 | -0.77 |

This is where H5 separates itself. Every other candidate, including `gap only` and
`gap + closing strength`, turns negative once the top 1% of its rows is removed: their means are
tail artifacts. H5 keeps **+8.29 bp** after the same surgery, against a declared floor of +0.5 bp.
Seventeen removed rows do not explain it.

---

## L. Symbol Concentration

| set | distinct tickers | top-5 share of excess | after removing top 5 | largest contributor |
|---|---|---|---|---|
| gap only | 1,540 | 143.9% | -0.16 bp | INTC (260 rows, 38.8%) |
| H2 | 1,494 | 29.2% | +2.37 bp | BAND (13 rows, 7.5%) |
| H3 | 1,122 | 30.0% | +3.51 bp | SOFI (76 rows, 8.0%) |
| **H5** | **834** | **22.7%** | **+12.88 bp** | ABSI (2 rows, 7.1%) |
| gap + closing strength | 1,425 | 20.5% | +3.69 bp | RKLB (66 rows, 5.0%) |

H5's 1,729 rows come from 834 distinct tickers and its top five explain 22.7% of the excess;
removing them leaves +12.88 bp. `gap only` is the counter-example that shows what concentration
failure looks like: its top five explain more than the whole excess (the rest is net negative), so
its +0.36 bp is five names.

A note on the criterion, recorded rather than acted on: `max_top5_ticker_share_of_excess` reports
OK for H1 and H4 only because their total excess is negative, which makes the share negative and
trivially below 0.40. As in E0, the criterion is meaningful only for a candidate with positive
excess. It is left exactly as declared and no verdict turns on it.

---

## M. Bootstrap

10,000 resamples, seed 20260920, resampling whole **sessions** so that rows sharing a morning move
together.

| set | mean lift | 95% CI | P(lift <= 0) | median lift | 95% CI |
|---|---|---|---|---|---|
| H1 | -0.72 bp | [-5.21, +3.74] | 0.620 | -0.69 bp | [-4.00, +2.00] |
| H2 | +3.34 bp | [-1.26, +8.18] | 0.083 | +1.26 bp | [-1.00, +4.00] |
| H3 | +4.97 bp | [-0.97, +10.70] | 0.051 | +2.54 bp | [-1.00, +7.00] |
| H4 | -0.57 bp | [-5.35, +3.97] | 0.589 | -1.90 bp | [-5.00, +1.00] |
| **H5** | **+16.63 bp** | **[+5.42, +27.57]** | **0.0024** | **+6.78 bp** | **[+0.00, +15.00]** |
| gap only | +0.38 bp | [-2.59, +3.25] | 0.394 | +0.18 bp | [-2.00, +2.00] |
| gap + closing strength | +4.60 bp | [-0.25, +9.38] | 0.032 | +2.46 bp | [+0.00, +6.00] |

H5 is the only candidate whose mean-lift interval excludes zero, and it does so comfortably.
H3 and `gap + closing strength` sit just on the boundary.

---

## N. PIT Audit

| check | method | scope | result |
|---|---|---|---|
| decision-time boundary 09:24 | every bar from 09:25 ET onward replaced with positive, finite, missing-preserving noise; premarket block rebuilt and compared feature by feature | 41 symbols, 2,607 sessions | **PASS** (no mismatched feature) |
| daily input is the previous session | the daily row each E1 row actually used is read back and required to be the previous XNYS grid session of D | 70,738 rows | **PASS** (0 wrong) |

Supporting properties, structural rather than checked afterwards: the premarket slice is taken at
bars with start <= 09:24 before any arithmetic happens; labels read 09:30 onward and are never an
input to a feature or a filter; `premarket_rvol` divides by a median over strictly prior sessions;
the join walks the XNYS grid, so a holiday cannot turn the previous calendar day into the previous
session; E0's own universe filter supplied CS membership, price, liquidity and the split test as of
D-1. The audit is checked in the test suite against a deliberately planted look-ahead (a premarket
feature that reads the 09:30 open), which it detects.

Input immutability: the daily read-set digest is identical before and after the run, and the
premarket tape digest is identical too - 2,152 files both times - even though the B-minute
collector held the writer lock throughout.

---

## O. Execution Limitations

1. **No quotes, therefore no spread.** Stocks Basic carries no bid/ask. Nothing in E1 nets a
   spread, and at these effect sizes the spread is the whole question: H5's +16.5 bp is roughly one
   tick on a $30 stock and less than the likely quoted spread on the $5-20M names that dominate the
   favourable buckets.
2. **The 09:30 bar open is a print, not a fill.** It is the first consolidated trade of the
   session, not a price a market order would have received, and the opening auction that sets it is
   invisible here.
3. **The noise dwarfs the signal.** Average MFE and MAE over the first five minutes are +89 bp and
   -88 bp against edges of 5 to 17 bp. Whether any of this survives depends almost entirely on
   entry and exit mechanics that E1 is not permitted to model.
4. **No imbalance, halt or auction data**, so the sessions where an opening move is mechanical
   rather than informational cannot be separated out.
5. **Premarket prints are sparse and uneven** (median 4 bars), so a "premarket range" is often two
   or three prints and `position_in_premarket_range` is a coarser quantity than its name suggests.
6. **The sample is concentrated in five months** and in a universe truncated alphabetically at
   roughly NEWT, because that is how far the broad minute collection has reached.
7. **Size and price are confounded with the signal** inside H5's 1,729 rows (section J).
8. **No borrow, no capacity, no cost model.**

---

## P. E1 Verdict

## **INCONCLUSIVE**

| criterion (declared) | H1 | H2 | H3 | H4 | H5 |
|---|---|---|---|---|---|
| N >= 2,000 | 13,842 | 9,864 | 8,657 | 14,744 | **1,729** |
| mean lift >= +15 bp | -0.77 | +3.33 | +4.88 | -0.59 | **+16.54** |
| median lift >= +5 bp | -0.30 | +0.50 | +2.07 | -1.94 | **+6.51** |
| win rate lift >= +2%p | -0.13 | +0.93 | +1.65 | -0.27 | **+3.32** |
| positive quarters >= 6 of 8 | 3 | 3 | 7 | 4 | **6** |
| mean lift after top 1% removed >= +0.5 bp | -6.50 | -3.79 | -0.94 | -6.68 | **+8.29** |
| top-5 ticker share <= 40% | n/m | 29.2% | 30.0% | n/m | **22.7%** |
| bootstrap CI low > 0 | -5.21 | -1.26 | -0.97 | -5.35 | **+5.42** |
| downside ratio <= 1.5 | 1.07 | 1.08 | 0.97 | 1.14 | **1.14** |
| volatility selector | no | no | no | no | no |
| **verdict** | FAIL | FAIL | FAIL | FAIL | **INCONCLUSIVE** |

Four of the five declared hypotheses fail outright. **H5 meets eight of the nine declared criteria
and fails only the sample-size floor**, with 1,729 rows against a declared 2,000. That is what
INCONCLUSIVE was declared for, and the floor is not being lowered to reach a PASS.

Answering the question E1 was built to answer: **not yet demonstrated, but not refuted either.**
The premarket gap on its own carries no information and inverts where it is largest. Premarket
volume and premarket closing strength each carry a little. Stacking all four declared conditions
produces a candidate with a +16.5 bp five-minute lift that survives extreme removal, is not
concentrated in a few names, is positive in all four regimes and ten minutes earlier in the
morning, and whose bootstrap interval excludes zero - on a sample too small to promote, drawn
overwhelmingly from two quarters, and decaying in the most recent one.

---

## Q. If PASS - Strategy E Thesis

Not applicable: E1 is INCONCLUSIVE and E is not promoted.

Recorded for the declaration that would test it: a stock that gaps up, has traded three or more
times its usual premarket value, sits in the top 20% of its premarket range, and is still rising
in the final half hour before the bell, continues upward for about five minutes after the open and
gives most of it back by fifteen. The effect is a favourably skewed widening (MFE +43 bp against
MAE -21 bp), not a quiet drift, so an exit rule decides whether it exists net of costs. Nothing in
this paragraph is approved; it is the hypothesis a larger sample would have to confirm.

---

## R. Next Step

E is not promoted, so this is not a D0/E1 work list. What would have to be true before the question
is worth reopening, in the order it should be settled:

1. **Sample.** H5 needs roughly 3x its current rows. The binding constraint is broad-universe
   premarket minute coverage, which today reaches about A..NEWT over five months. Extending the
   collection completes the sample without any new research.
2. **A holdout.** Every number here was read on the same rows. H5 needs a window this study never
   touched, declared before it is opened.
3. **Costs.** At 16.5 bp of edge, a spread estimate is not a refinement, it is the experiment.
   Stocks Basic cannot supply one.
4. **Deconfounding size and price.** H5 averages +61.70 bp in $5-10 names and +6.42 bp above $200
   (section J), and the baseline carries the same gradient, so a size-neutral test is required
   before any of the edge is attributed to the premarket conditions.
5. **The five-minute decay.** The edge is gone by fifteen minutes and its median is negative there,
   so any follow-up has to state an exit before it states a return.

The E1 machinery (`backend/app/backtest/strategy_e1_premarket/`) and the premarket cache
(`data/runtime/strategy_e_candidate/e1_cache/`, three decision times over 1,902 symbols) are
reusable as-is for all five.

No Strategy E directory, entry rule, exit optimization, position sizing, broker, paper trading or
LLM work was created, per the E1 scope.
