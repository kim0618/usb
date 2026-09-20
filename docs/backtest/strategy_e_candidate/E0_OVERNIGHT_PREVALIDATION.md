# Strategy E0 - Overnight / Closing Strength Pre-Validation

| | |
|---|---|
| Research id | `e0-overnight-v0` (`OVERNIGHT_CLOSING_STRENGTH_V0`) |
| Run id | `e0-a1a8b7eedd3d` |
| Rules | `e0_overnight_rules_v1.json`, canonical `078aa79231f075a4…`, frozen 2026-09-20 KST before any feature, label or statistic was computed |
| Daily snapshot | `USB-HIST-V1` (FROZEN), freeze `9ebd6c29c667…`, grid digest `830744f64a2d…` |
| Read-set digest | `7e790a8dcf1d…` at load and after the run (`inputs_unchanged: true`) |
| Provider calls | 0 |
| Artifacts | `data/runtime/strategy_e_candidate/runs/e0-a1a8b7eedd3d/` |
| Git HEAD at run | `4e84ec12a1fa176a19807f0d966f45925623763b` |
| **Verdict** | **FAIL** |

E0 is an independent research experiment, not a strategy. It answers one question: using only
information observable up to the regular-session close, can the probability and expected value of
the next session's open (or the first minutes after it) be raised meaningfully above the
unconditional base rate? Nothing in A, B, C, D, the Historical Store, the collector or
USB-HIST-V1/V2 was modified, no provider call was made, and no trading, broker, sizing or exit
code was written.

---

## A. Data Coverage

### Daily

```text
DAILY COVERAGE
start:    2024-09-17
end:      2026-09-16
sessions: 501 (grid), 480 used for evaluation (2024-10-15 .. 2026-09-15)
symbols:  6,339 in the CS panel, 3,507 actually used by the declared universe
```

Authority `MASSIVE_GROUPED_DAILY adjusted=false`, read through Strategy D's
`load_daily_history`, imported unmodified. Every frozen file is verified against the sha256 the
C raw freeze recorded before a row of it is used, and the grid passes D's G1-G8 checks. 8 CS
reference snapshots and 3,328 split records back the point-in-time universe and the split basis.
SPY is not a CS security, so the benchmark close is read from the same frozen grouped files in a
separate, equally verified pass.

### Minute

```text
MINUTE COVERAGE (two cohorts, both MASSIVE_TICKER_AGGREGATE adjusted=false)

M1_DEEP
  start:    2024-09-17
  end:      2026-09-16
  symbols:  30 (MINUTE_UNIVERSE_V1), 29 of them inside the declared universe
  sessions: 500 paired sessions, 14,868 rows -> 13,793 in the declared universe (92.8%)
  bars:     4,823,223 raw page bars + 5,663,972 legacy Parquet bars

M2_BROAD
  start:    2026-04-20
  end:      2026-09-16
  symbols:  1,694 collected, 1,532 inside the declared universe
  sessions: 103 paired sessions, 172,574 rows -> 145,619 in the declared universe (84.4%)
  bars:     55,234,991 raw page bars
```

No session was carried by both storage forms for any symbol (`overlap_sessions: 0`), so no volume
was double counted.

**M2 is alphabetically truncated.** The V2 broad minute collection was stopped part way through a
queue ordered by symbol, so the cohort covers roughly A..NEWT and almost nothing after it (270 A,
273 C, 149 M, 25 N, then 1-2 per letter for O..W). Alphabetical position is unrelated to return,
which makes this a truncated sample rather than a performance-selected one, but the truncation is
real and every M2 number below carries it.

### Minute tape against the daily authority

| Cohort | rows | median abs difference, minute close vs official close | correlation, minute-derived overnight vs official overnight |
|---|---|---|---|
| M1_DEEP | 13,793 | 0.021% | 0.9997 |
| M2_BROAD | 145,619 | 0.028% | 0.9991 |

The residual difference is the closing auction: the official close is the auction print, the
minute close is the last 15:59 bar. It is about 2-3 basis points at the median and does not move
any conclusion here.

---

## B. Universe

Point-in-time, declared before any read:

* in the latest CS reference snapshot dated on or before D (CS only, so ETF, fund, warrant, unit
  and test symbols are excluded by the reference itself);
* primary exchange XNAS / XNYS / XASE;
* `close(D) >= $5`;
* median dollar volume over the 20 sessions `[D-20, D-1]` `>= $5M`, with at least 15 of those 20
  sessions present. The window ends strictly **before** D so that the liquidity filter cannot
  select on the same volume spike that the RVOL feature measures;
* `open(D+1)` present and positive;
* 20 sessions of warmup, so D runs over grid indices 20..499.

| Counter | Cells |
|---|---|
| candidate cells (480 sessions x 6,339 tickers) | 3,042,720 |
| rejected: not a CS member as of D | 544,365 |
| rejected: price / finiteness | 784,336 |
| rejected: liquidity | 557,381 |
| rejected: no next open | 322 |
| rejected: corporate action in (D, D+1] | 77 |
| **rows kept** | **1,156,233** |

Prices are split-normalized with `F(t)`, the cumulative price factor of splits executed on or
before t; volume is `v/F`; dollar volume needs no adjustment. A split executing between the two
prices of the label is exactly `F(t+1) != F(t)`, so the 77 excluded rows are detected without a
separate date scan.

---

## C. Baseline Overnight Effect

`overnight_return = open(D+1) / close(D) - 1` over all 1,156,233 rows.

```text
N            1,156,233
mean            +4.77 bp
median          +0.00 bp
win rate        49.93 %
std              2.047 %
P5              -2.038 %
P25             -0.514 %
P75             +0.580 %
P95             +2.123 %

P(gap >= +1%)   14.81 %      P(gap <= -1%)   13.89 %
P(gap >= +2%)    5.51 %      P(gap <= -3%)    2.49 %
P(gap >= +3%)    2.78 %      P(gap <= -5%)    0.85 %

mean positive gap  +1.010 %
mean negative gap  -0.993 %
```

This is the base rate every later number is measured against. Three properties of it matter more
than its size:

1. **The drift is real but tiny and entirely tail-driven.** The mean is +4.77 bp while the median
   is exactly 0.00 bp and the win rate is 49.93%, i.e. below half. 4.07% of rows open exactly at
   the prior close. A positive mean with a zero median and a sub-50% win rate means the average is
   produced by the right tail, not by a majority of names drifting up.
2. **It is smaller than any plausible cost.** 4.77 bp is well inside the spread of the median
   $5-and-up, $5M-dollar-volume name, before any slippage or auction impact.
3. **It is not stable quarter to quarter** (section I).

The extreme rows were inspected for data integrity rather than trusted: the worst and best rows
are genuine biotech binary events (SAVA -84.8% on 2024-11-22, MLTX -88.8%, CAPR +371.7%,
QURE +188.1%, APLS +136.4%), not split or ticker-reuse artifacts.

---

## D. Single Feature Results

Fixed bucket edges, declared before any result was seen. Lift is candidate mean minus the
baseline mean, in basis points.

### `day_return`

| bucket | N | mean(bp) | median(bp) | win(%) | lift(bp) | P(>=2%) | P(<=-3%) |
|---|---|---|---|---|---|---|---|
| [-inf, 0) | 567,766 | +5.8 | +3.5 | 51.23 | +1.1 | 5.53% | 2.61% |
| [0, 2%) | 389,190 | +2.8 | +0.0 | 48.79 | -2.0 | 3.82% | 1.56% |
| [2%, 5%) | 149,538 | +5.2 | +0.0 | 48.81 | +0.4 | 7.07% | 2.81% |
| [5%, 10%) | 38,411 | +9.2 | +0.0 | 48.25 | +4.4 | 12.35% | 5.36% |
| [10%, +inf) | 11,328 | +0.6 | -14.9 | 44.37 | -4.2 | 18.31% | 14.80% |

Not monotone, and the direction is the opposite of the thesis at both ends that matter: a down day
beats the baseline on mean, median and win rate, while the biggest up days have the worst median
(-14.9 bp), the worst win rate (44.37%) and a 14.8% chance of a -3% gap.

### `CLV` = (close - low) / (high - low)

| bucket | N | mean(bp) | median(bp) | win(%) | lift(bp) | P(>=2%) | P(<=-3%) |
|---|---|---|---|---|---|---|---|
| [0, 0.2) | 256,253 | +9.4 | +7.0 | 53.02 | +4.6 | 5.58% | 2.44% |
| [0.2, 0.5) | 324,831 | +4.7 | +1.0 | 50.20 | -0.1 | 5.37% | 2.46% |
| [0.5, 0.8) | 325,256 | +4.4 | +0.0 | 48.84 | -0.4 | 5.48% | 2.37% |
| [0.8, 0.95) | 184,233 | +1.0 | +0.0 | 48.13 | -3.7 | 5.62% | 2.79% |
| [0.95, 1.0] | 65,655 | -0.4 | +0.0 | 47.00 | -5.2 | 5.69% | 2.58% |

**This is the central result of the daily study, and it points the wrong way.** CLV is cleanly
monotone in mean, median and win rate, and it is monotone **downward**. Closing at the top of the
day's range predicts a *worse* next open, not a better one: the strongest closes (CLV >= 0.95)
have a negative mean, a 47.00% win rate and a -5.2 bp lift, while the weakest closes
(CLV < 0.2) have +9.4 bp, a 53.02% win rate and a +4.6 bp lift. The gap-tail probabilities barely
move across the whole range, so this is a shift of the centre of the distribution, not a tail
effect.

### `RVOL` = volume(D) / median volume over [D-20, D-1]

| bucket | N | mean(bp) | median(bp) | win(%) | lift(bp) | P(>=2%) | P(<=-3%) |
|---|---|---|---|---|---|---|---|
| [0, 1) | 585,043 | +3.8 | +0.0 | 49.59 | -0.9 | 4.57% | 1.85% |
| [1, 2) | 485,555 | +5.1 | +1.4 | 50.34 | +0.4 | 5.78% | 2.69% |
| [2, 3) | 57,412 | +7.5 | +0.0 | 49.99 | +2.8 | 9.90% | 5.79% |
| [3, 5) | 20,449 | +9.4 | +0.0 | 49.36 | +4.7 | 10.73% | 5.55% |
| [5, +inf) | 7,774 | +19.9 | +2.7 | 51.07 | +15.1 | 12.57% | 6.14% |

The one monotone increasing feature, and the one that is least useful. RVOL raises the mean
(+15.1 bp in the top bucket) while raising P(>= +2%) to 12.57% **and** P(<= -3%) to 6.14%, against
baseline 5.51% and 2.49%. The median barely moves (+2.7 bp) and the win rate stays at 51.07%. RVOL
is a volatility selector, not a direction selector: it widens the distribution roughly
symmetrically and the mean follows the fatter right tail.

### `dollar_volume`, `close_price`, `momentum_5d`, `relative_strength_5d`

| feature | shape | notable |
|---|---|---|
| dollar volume | flat | lifts span -0.8 to +2.3 bp across four buckets spanning 100x in size |
| close price | decreasing in mean, increasing in win rate | $5-10 names: +8.1 bp mean but 47.48% win rate; $200+: +4.4 bp mean, 51.08% win rate. The cheap-name mean is tail money |
| momentum_5d | U-shaped | both extremes beat the middle (+2.1 bp at < -5%, +3.2 bp at > +15%), interior negative |
| relative strength vs SPY | U-shaped | -5% and worse: +3.3 bp; +15% and better: +5.0 bp, but win rate 47.45% |

The U shapes are the same tail mechanism as RVOL: extreme recent moves of either sign predict a
wider next-open distribution, and the mean tracks the right tail while the win rate falls.

---

## E. Combined Hypotheses

The five hypotheses were declared before any of section D was computed. Their thresholds come only
from the declared bucket edges. No sweep, no optimizer, no threshold search was run.

| set | rule | N | mean(bp) | median(bp) | win(%) | mean lift(bp) | win lift(%p) |
|---|---|---|---|---|---|---|---|
| baseline | all rows | 1,156,233 | +4.77 | +0.00 | 49.93 | - | - |
| H1 | `day_return > 0 and clv >= 0.8` | 226,335 | +0.78 | +0.00 | 47.79 | **-3.99** | **-2.14** |
| H2 | `H1 and rvol >= 2.0` | 17,687 | +0.27 | -2.31 | 45.49 | **-4.50** | **-4.44** |
| H3 | `clv >= 0.8 and rvol >= 2.0 and rel_strength_5d > 0` | 13,563 | +8.66 | +0.00 | 46.94 | **+3.89** | **-2.99** |

Two of the three daily hypotheses are worse than doing nothing, on every statistic. H3, the only
one with a positive mean lift, buys that +3.89 bp while *lowering* the win rate by 2.99 points and
leaving the median unchanged at zero: it is a wider distribution, not a better one.

Minute hypotheses, evaluated inside each cohort against that cohort's own baseline:

| cohort | set | N | mean(bp) | median(bp) | win(%) | mean lift(bp) | win lift(%p) |
|---|---|---|---|---|---|---|---|
| M1_DEEP | cohort baseline | 13,793 | +11.10 | +5.67 | 52.48 | - | - |
| M1_DEEP | H2 in cohort | 200 | -30.11 | -15.76 | 44.50 | -41.20 | -7.98 |
| M1_DEEP | H4 = `H2 and last-30m return > 0` | 154 | -21.09 | -12.02 | 45.45 | -32.19 | -7.03 |
| M1_DEEP | H5 = `H2 and close within 0.5% of high and last-15m return > 0` | 93 | +15.72 | -8.01 | 45.16 | +4.63 | -7.32 |
| M2_BROAD | cohort baseline | 145,619 | +9.03 | +5.41 | 51.92 | - | - |
| M2_BROAD | H2 in cohort | 2,036 | -6.82 | -4.73 | 44.30 | -15.85 | -7.62 |
| M2_BROAD | H4 | 1,638 | -8.09 | -6.13 | 43.59 | -17.11 | -8.33 |
| M2_BROAD | H5 | 1,007 | -6.59 | -7.49 | 43.20 | -15.61 | -8.72 |

Every minute hypothesis falls below the declared minimum of 2,000 rows (154, 93, 1,638, 1,007), so
none of them can pass the gate on sample size alone. M1_DEEP H5's +15.72 bp mean rests on 93 rows
whose top five tickers account for 947% of the total excess, i.e. it is a handful of names netted
against the rest. It is noise, and it is reported as noise.

---

## F. Closing Strength Incremental Alpha

The question the minute cohorts exist to answer: does closing strength add anything to the daily
signal? Measured two ways, both negative.

**Incremental over H2, same universe, same period, one extra condition:**

| cohort | H2 alone | H4 = H2 + positive last 30 min | H5 = H2 + near high + positive last 15 min |
|---|---|---|---|
| M1_DEEP | 200 rows, -30.11 bp | 154 rows, -21.09 bp (delta +9.01 bp) | 93 rows, +15.72 bp (delta +45.83 bp) |
| M2_BROAD | 2,036 rows, -6.82 bp | 1,638 rows, -8.09 bp (delta -1.26 bp) | 1,007 rows, -6.59 bp (delta +0.24 bp) |

On the broad cohort, which has 10x the rows, the closing-strength filters move the mean by -1.26 bp
and +0.24 bp. That is no incremental information. The M1 deltas are large and disagree with each
other across two nested rules on 154 and 93 rows, which is what sampling noise looks like.

**Closing strength as a standalone feature**, against the cohort baseline:

`return_1530_to_close`, M2_BROAD (N = 145,619, baseline +9.03 bp / 51.92% win):

| bucket | N | mean(bp) | median(bp) | win(%) | lift(bp) |
|---|---|---|---|---|---|
| < -1% | 7,777 | +32.69 | +15.41 | 54.17 | **+23.67** |
| [-1%, -0.2%) | 42,440 | +12.85 | +9.19 | 53.52 | +3.82 |
| [-0.2%, 0) | 22,602 | +7.70 | +6.07 | 53.13 | -1.32 |
| [0, +0.2%) | 24,149 | +6.93 | +4.56 | 51.89 | -2.10 |
| [+0.2%, +1%) | 40,588 | +2.87 | +0.00 | 49.72 | -6.15 |
| > +1% | 8,052 | +7.03 | +0.00 | 49.16 | -2.00 |

`close_vs_session_vwap`, M2_BROAD:

| bucket | N | mean(bp) | median(bp) | win(%) | lift(bp) |
|---|---|---|---|---|---|
| < -1% | 14,415 | +31.11 | +19.91 | 56.06 | **+22.09** |
| [-1%, -0.2%) | 43,442 | +11.03 | +8.93 | 53.65 | +2.00 |
| [-0.2%, 0) | 19,095 | +5.86 | +4.71 | 52.29 | -3.17 |
| [0, +0.2%) | 18,407 | +5.73 | +2.35 | 50.87 | -3.29 |
| [+0.2%, +1%) | 37,542 | +4.26 | +0.00 | 49.81 | -4.77 |
| > +1% | 12,718 | +0.74 | +0.00 | 48.56 | **-8.29** |

`close_to_day_high` and the high-break flags, M2_BROAD:

| condition | N | mean(bp) | win(%) | lift(bp) |
|---|---|---|---|---|
| close more than 2% below the day high | 49,269 | +15.22 | 52.95 | +6.20 |
| close within 0.2% of the day high | 11,158 | -0.34 | 48.92 | **-9.36** |
| made a new day high in the last 30 min | 20,690 | +3.81 | 49.81 | -5.22 |
| made a new day high in the last 15 min | 16,947 | +2.37 | 49.01 | -6.66 |

The two cohorts do not say the same thing, and the difference is worth stating precisely.

**M2_BROAD (145,619 rows, 1,532 names, 5 months) is monotone decreasing** on all four measures, in
mean, median and win rate together: weak closes beat the baseline, strong closes lose to it. The
only break in the monotonicity is the top `return_1530_to_close` bucket (-2.0 bp against the
-6.2 bp below it), which is a 8,052-row bucket of names that moved more than 1% in the last half
hour.

**M1_DEEP (13,793 rows, 29 mega-caps, 2 years) is U-shaped** on all four: the weak-close side beats
the baseline, which agrees with M2, but so does the extreme strong-close side (+22.2 bp for a
last-30-minute return above +1%, +12.5 bp for a close more than 1% above VWAP, +4.1 bp for a close
within 0.2% of the high). The two late-high-break flags point the opposite way from M2 outright:
+6.0 and +6.6 bp in M1 against -5.2 and -6.7 bp in M2. On 30 mega-caps the extreme closing-strength
buckets look like a volatility selector, not a direction selector, which is the same pattern RVOL
shows in the daily table.

What the two cohorts do agree on is the part that decides the hypotheses: **the ordinary strong
close never outperforms.** In both cohorts the populated interior runs downward, and in both the
declared H4 and H5 conditions - which select the positive side of these features without reaching
the extreme tail - land below their cohort baseline. That is why they fail, and it is not a
threshold problem: on a feature whose interior is monotone decreasing, no threshold on the
increasing direction pays.

---

## G. Next Open / 1m / 5m / 15m Comparison

M2_BROAD, all 145,619 cohort rows (minute-close basis):

| horizon | N | mean(bp) | median(bp) | win(%) |
|---|---|---|---|---|
| close -> next open | 144,663 | +9.01 | +6.07 | 52.88 |
| close -> next 09:30 bar close (1m) | 144,663 | +11.72 | +10.29 | 54.27 |
| close -> next 09:34 bar close (5m) | 145,409 | +10.66 | +9.09 | 53.21 |
| close -> next 09:44 bar close (15m) | 145,553 | +10.69 | +8.30 | 52.60 |
| next 5m MFE | 145,409 | +81.66 | +61.42 | 72.78 |
| next 5m MAE | 145,409 | -59.50 | -39.94 | 34.03 |
| next 15m MFE | 145,553 | +108.09 | +81.81 | 77.99 |
| next 15m MAE | 145,553 | -85.90 | -61.95 | 27.73 |

M1_DEEP is the same shape: +11.32 / +11.03 / +12.01 / +12.21 bp across the four horizons.

**There is no momentum continuation after the open.** The whole overnight move is present at the
first print; holding another fifteen minutes adds about +1.7 bp on M2 and +0.9 bp on M1, and the
win rate *declines* from 54.27% at one minute to 52.60% at fifteen. Meanwhile MFE and MAE both
widen to about +108 / -86 bp at 15 minutes, so the extra time buys a great deal of noise for
nothing. If a later study wants an exit, this says the honest comparison is "the open" versus "the
first minute", not "hold for momentum".

For the candidate sets the picture is worse: H2 in M1_DEEP goes -28.77 bp at the open to -42.24 bp
at 15 minutes, and its 15-minute MAE is -169.01 bp against the cohort's -83.90 bp. Selecting strong
closes selects names that keep falling into the next morning.

---

## H. Downside Tail

| set | P(<= -1%) | P(<= -3%) | P(<= -5%) | ratio vs baseline at -5% | P5 | worst |
|---|---|---|---|---|---|---|
| baseline | 13.89% | 2.49% | 0.85% | 1.00 | -2.04% | -93.1% |
| H1 | 14.54% | 2.69% | 0.94% | 1.11 | -2.12% | -88.8% |
| H2 | 22.11% | 6.84% | 2.83% | **3.33** | -3.66% | -88.8% |
| H3 | 20.74% | 6.22% | 2.74% | **3.22** | -3.44% | -88.8% |
| M1_DEEP H4 | - | - | - | **3.61** | - | - |
| M2_BROAD H4 | - | - | - | **2.96** | - | - |
| M2_BROAD H5 | - | - | - | **2.40** | - | - |

The declared limit was 1.5x. Every hypothesis that adds RVOL more than triples the chance of a -5%
gap while adding at most +3.89 bp of mean. That trade is the whole content of the RVOL bucket
table restated: the filters buy variance, and rather more of it on the left than the right.

---

## I. Quarterly Consistency

Baseline by quarter (this is why each candidate is compared with its own quarter, not the whole
period):

| quarter | N | mean(bp) | median(bp) | win(%) |
|---|---|---|---|---|
| 2024Q4 | 122,763 | +13.99 | +3.64 | 51.57 |
| 2025Q1 | 137,718 | -3.04 | +0.00 | 48.35 |
| 2025Q2 | 141,458 | -6.32 | +0.00 | 47.60 |
| 2025Q3 | 152,084 | +10.17 | +6.44 | 53.58 |
| 2025Q4 | 157,064 | +10.75 | +3.02 | 51.30 |
| 2026Q1 | 151,707 | -5.24 | +0.00 | 46.24 |
| 2026Q2 | 156,467 | +11.51 | +0.00 | 49.92 |
| 2026Q3 | 136,972 | +6.33 | +3.32 | 50.94 |

The base rate itself flips sign four times across eight quarters. Mean lift by quarter:

| set | 24Q4 | 25Q1 | 25Q2 | 25Q3 | 25Q4 | 26Q1 | 26Q2 | 26Q3 | positive quarters |
|---|---|---|---|---|---|---|---|---|---|
| H1 | +3.9 | +4.6 | -20.2 | -2.7 | -3.9 | -1.0 | -4.9 | -1.7 | **2 / 8** |
| H2 | -0.2 | +14.2 | -52.5 | +22.0 | +3.2 | +21.3 | -9.4 | -28.7 | **4 / 8** |
| H3 | +7.3 | +11.0 | +1.0 | +16.3 | -1.1 | +22.7 | -8.5 | -30.7 | **5 / 8** |

Declared minimum was 6 of 8. H3, the best set, is positive in five quarters and then loses 30.7 bp
in the most recent one, which is the quarter a live study would have been trading.

---

## J. Extreme / Concentration Tests

Mean lift after removing the largest positive outcomes:

| set | full | drop top 1 | drop top 5 | drop top 1% |
|---|---|---|---|---|
| H1 | -3.99 bp | -4.06 bp | -4.22 bp | -13.79 bp |
| H2 | -4.50 bp | -5.02 bp | -6.54 bp | -25.80 bp |
| H3 | **+3.89 bp** | +3.39 bp | +1.93 bp | **-17.01 bp** |

H3's entire edge is 136 rows. Removing the top 1% of its 13,563 rows turns +3.89 bp into -17.01 bp,
against a declared floor of +0.5 bp.

Ticker concentration for H3: 2,917 distinct tickers, and the top five contribute **61.1%** of the
total excess return, against a declared limit of 40%. The largest single contributor, HOLO, supplies
16.5% of the edge from **two rows**; CRVS supplies 13.5% from four. After removing those five names
the mean lift falls to +1.52 bp.

One gate-design note, recorded rather than acted on: `max_top5_ticker_share_of_excess` is reported
as OK for H1, H2, M1_DEEP H4, M2_BROAD H4 and M2_BROAD H5, but only because their total excess
return is negative, which makes the share negative and therefore trivially below 0.40. The
criterion is only meaningful for a candidate whose excess is positive. It is left exactly as
declared; no candidate's verdict turns on it.

---

## K. Bootstrap

10,000 resamples, seed 20260920, resampling **whole sessions** rather than rows. Overnight returns
within one session share the entire market's move, so a row bootstrap would treat ~2,400
correlated rows as 2,400 independent draws and report an interval several times too narrow. The
candidate and the baseline are recomputed on the same drawn sessions, so a draw of unusually
strong days moves both sides and cancels out of the lift.

| set | mean lift | 95% CI | P(mean lift <= 0) | median lift | 95% CI |
|---|---|---|---|---|---|
| H1 | -3.97 bp | [-11.42, +2.93] | 0.867 | -1.14 bp | [-5.00, +0.00] |
| H2 | -3.99 bp | [-28.86, +13.03] | 0.600 | -4.65 bp | [-16.00, +0.00] |
| H3 | +3.94 bp | [-5.79, +13.63] | **0.214** | -1.70 bp | [-6.00, +0.00] |
| M1_DEEP H4 | - | CI low -102.6 bp | - | - | - |
| M2_BROAD H4 | - | CI low -31.8 bp | - | - | - |
| M2_BROAD H5 | - | CI low -36.2 bp | - | - | - |

No candidate's mean-lift interval excludes zero. H3, the best of them, has a 21.4% chance of a
non-positive lift under the session bootstrap, and its median-lift interval sits almost entirely
below zero.

---

## L. PIT Audit

Method: replace every OHLCV value after a cut session with large, structurally different noise,
rebuild the entire row table, and require the rows that must not have moved to be identical bit for
bit. A feature of session t may read sessions <= t, so every row with **t < cut** must match,
including t = cut-1, which is the only row a feature that illegally read t+1 would move. The label
legitimately reads t+1, so labels are compared over t+1 < cut only.

The poison is large but deliberately **positive, finite, and missing-preserving**: the universe
filter reads open(t+1) and asks only "present and positive", so it answers identically on the
poisoned panel and the selected row set is unchanged, which the audit asserts rather than assumes.
An earlier version of the audit replaced missing cells with valid numbers, which admitted one row
at cut = 300 that the clean build had rejected for a missing next open. That was an artifact of the
poison, not a look-ahead, and it was fixed by keeping NaN as NaN. The audit is checked in the test
suite against a deliberately planted t+1 leak, which it detects.

| cut | cut session | feature rows compared | label rows compared | row set identical | all features identical | labels identical | verdict |
|---|---|---|---|---|---|---|---|
| 300 | 2025-11-26 | 652,146 | 649,691 | yes | yes (8/8) | yes | **PASS** |
| 450 | 2026-07-07 | 1,027,163 | 1,024,526 | yes | yes (8/8) | yes | **PASS** |

Supporting point-in-time properties, structural rather than checked after the fact:

* the daily panel is loaded by D's loader, which verifies every file against the freeze sha256 and
  refuses a grid with an internal gap;
* CS membership at D uses the latest snapshot dated on or before D;
* the liquidity window ends at D-1, so it cannot select on D's own volume;
* minute features read no bar starting at or after 16:00 ET of D;
* minute pairing walks the XNYS grid, not the tape, so a missing session cannot silently become a
  two-day overnight return;
* `read_set_digest` is identical before and after the run (`inputs_unchanged: true`), so nothing
  under the read set moved while the study ran, even though the minute collector writes into the
  same workspace.

---

## M. Limitations

1. **The official open is not an executable fill.** Every number here is measured against the
   consolidated open print, gross of spread, slippage, auction imbalance and capacity. The baseline
   drift of +4.77 bp is smaller than the spread of a typical name in this universe, so nothing in
   section C should be read as an attainable return.
2. **Two years, one regime.** 480 evaluation sessions, 2024-10-15 to 2026-09-15. The base rate
   itself flips sign four times inside it.
3. **M2_BROAD is alphabetically truncated** (roughly A..NEWT) and only 103 paired sessions long
   (2026-04-20 .. 2026-09-15). M1_DEEP spans 500 paired sessions but is 30 mega-cap names, and so
   answers a narrower question than the daily study.
4. **No premarket data is used.** E0 reads the close and the next open. Overnight news, premarket
   volume and premarket price action, which is where most of the gap is actually formed, are
   outside the declared feature set.
5. **The minute close is the 15:59 bar close, not the auction print** (median difference 2-3 bp).
   Minute-derived and official overnight returns correlate 0.999+, but the two are not the same
   number.
6. **Early-close sessions are dropped from the minute features** by construction: the declared
   15:30-15:59 windows do not exist on a 13:00 ET close.
7. **No cost, borrow, or short-side feasibility was modelled**, which matters because the
   observation in section F points at a reversal, and the tradeable side of a reversal after a
   strong close is the short side.
8. **Quarterly attribution is calendar-based**, so a quarter mixes regimes.

---

## N. E0 Verdict

## **FAIL**

All five declared hypotheses fail the declared gate. The failures are not marginal:

| criterion (declared) | H1 | H2 | H3 | M1 H4 | M1 H5 | M2 H4 | M2 H5 |
|---|---|---|---|---|---|---|---|
| N >= 2,000 | ok | ok | ok | **154** | **93** | **1,638** | **1,007** |
| mean lift >= +15 bp | -3.99 | -4.50 | +3.89 | -32.2 | +4.6 | -17.1 | -15.6 |
| median lift >= +5 bp | 0.00 | -2.31 | 0.00 | -17.7 | -13.7 | -11.5 | -12.9 |
| win rate lift >= +2%p | -2.14 | -4.44 | -2.99 | -7.03 | -7.32 | -8.33 | -8.72 |
| positive quarters >= 6 of 8 | 2 | 4 | 5 | 1 | 5 | 0 | 0 |
| mean lift after top 1% removed >= +5 bp | -13.8 | -25.8 | -17.0 | -47.3 | -7.8 | -33.9 | -32.3 |
| top-5 ticker share <= 40% | n/m | n/m | **61.1%** | n/m | **947%** | n/m | n/m |
| bootstrap CI low > 0 | -11.4 | -28.9 | -5.8 | -102.6 | -52.9 | -31.8 | -36.2 |
| downside ratio <= 1.5 | 1.11 | **3.33** | **3.22** | **3.61** | **2.00** | **2.96** | **2.40** |

("n/m" = not meaningful, see the note in section J.)

The PIT audit passed at both cuts, so this is a finding about the market, not about the pipeline.
The rules were not edited, relaxed or reinterpreted to produce a different outcome, and the
inverse relationship found in sections D and F is **not** being claimed as a PASS in disguise: it
was read off the same data the hypotheses were tested on, and it has not been pre-registered,
tested out of sample, or costed.

Answering the question E0 was built to answer, in one line: **no.** Conditioning on price, volume
and closing-strength information observable up to the close does not meaningfully raise the
probability or expected value of a positive next open. Where the conditioning moves the mean at
all, it does so by widening the distribution - raising the chance of a -5% gap by 2.4x to 3.6x for
at most +3.89 bp of mean - and the sign of the closing-strength relationship is the opposite of
the one the strategy thesis assumed.

---

## O. If PASS - Proposed Strategy E Thesis

Not applicable. E0 is FAIL and E is not promoted.

Recorded for a future declaration, as an observation and not as a result: **strength into the close
is followed by relative weakness at the next open.** The daily CLV table is the strongest form of
it, because it is the largest and longest sample here - 1,156,233 rows over two years, monotone in
mean, median and win rate together, from +4.6 bp of lift at CLV < 0.2 to -5.2 bp at CLV >= 0.95,
with the gap-tail probabilities barely moving across the range. M2_BROAD reproduces it on four
minute measures (+22 to +24 bp of lift on the weak-close side, -8 to -9 bp on the strong-close
side), but only over 5 months and an alphabetically truncated universe. M1_DEEP does **not**
reproduce it at the extremes: on 30 mega-caps the relationship is U-shaped and the late-high-break
flags reverse sign, so the effect as stated is a broad-universe, non-extreme phenomenon at best.

All of that is post-hoc, read off the same data the hypotheses were tested on, gross of costs, and
its tradeable side is the one that requires shorting. It would need its own pre-registration and
its own out-of-sample window before it counts as anything.

---

## P. Next Step

E is not promoted, so there is no D0/E1 list. What this run leaves behind for whoever picks the
question up next:

1. The E0 machinery (`backend/app/backtest/strategy_e0_overnight/`) is reusable as-is for any
   next-open study: PIT universe, split basis, session bootstrap, minute cohort builder and the
   poisoning audit are all independent of the hypotheses that failed.
2. The minute cohort cache (`data/runtime/strategy_e_candidate/minute_cache/`) holds 60M parsed
   bars reduced to per-session closing-strength rows, so a follow-up study does not re-read 1.1 GB
   of provider pages.
3. If the reversal observation in section O is ever taken up, it needs: a fresh declaration, a
   holdout window not used here, premarket data (the actual gap formation window, absent from E0),
   an explicit cost model, short-side feasibility because that is the side the observation points
   at, and a resolution of the M1 / M2 disagreement at the extremes - the two cohorts differ in
   universe, length and truncation all at once, so which of the three explains the difference is
   currently unknown.

No strategy directory, broker, sizing, paper trading or LLM work was created, per the E0 scope.
