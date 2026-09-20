# Strategy E1-H5 Confirmation

| | |
|---|---|
| Study id | `E1_H5_CONFIRMATION_V1` |
| Run id | `e1h5-d57b212da84c` |
| Rules | `e1_h5_confirmation_rules_v1.json`, canonical `d2a8b5f23a3c96c3…`, frozen 2026-09-20 after a bar-counting coverage audit and before any confirmation return was computed |
| Hypothesis | E1's H5, reproduced verbatim and evaluated by importing E1's own mask function |
| Upstream | E1 rules `951b973b1c848ef8…` (run `e1-32a530c8af9a`), E0 universe rules `078aa79231f075a4…` |
| Daily snapshot | `USB-HIST-V1` (FROZEN at 2026-09-16), read-set digest identical before and after the run |
| Provider calls | 0. Writer lock taken: no. B-minute collector: left running, untouched |
| Artifacts | `data/runtime/strategy_e_candidate/confirmation_runs/e1h5-d57b212da84c/` |
| **Verdict** | **INCONCLUSIVE** (does not promote H5 to Strategy E) |

---

## A. Frozen Rules

H5 is reproduced character for character from the E1 declaration and is not modified:

```text
premarket_gap > 0
AND premarket_rvol >= 3.0
AND position_in_premarket_range >= 0.8
AND return_0900_0925 > 0
```

Decision time 09:25 ET (last usable bar start 09:24). Primary horizon **R_5M**, fixed before this
study began; R_1M and R_15M are diagnostic only and could not have changed the primary had they
looked better. Feature definitions are not re-implemented here: the confirmation block is
processed by importing `strategy_e1_premarket` unchanged, and the test suite asserts both that
this declaration's H5 string equals the E1 declaration's H5 string and that the mask used here is
E1's own function.

Declared before any return: the matching method and its bucket edges, the cost grid and the
10 bp "realistic cost" assumption, the 2,000-row sample floor, the bootstrap seed and units, and
every gate threshold.

---

## B. New Data Coverage

```text
CURRENT PREMARKET COVERAGE (read-only audit, 2026-09-20)

start:                      2024-09-17
end:                        2026-09-16
sessions:                   501 on the grid, 480 used
unique symbols on the tape: 1,986 at audit time (1,902 when E1 was bound)
eligible symbol-sessions:   73,288 in the declared E1 universe across both blocks
09:00-09:25 available:      40.2% of tape symbol-sessions
09:30-09:35 available:      100% of sessions that have a 09:30 open bar
H5 eligible observations:   1,729 development + 75 confirmation = 1,804
```

**The new data is new symbols, not new sessions.** Between the E1 run and this audit the minute
store gained 93 symbols and every one of them begins with the letter P (PINE .. PZZA). The
B-minute collector advances alphabetically over a **fixed** scope window, so newly arrived data
covers the same 2026-04-20 .. 2026-09-16 sessions the development block already covered:
**0 of the 104 confirmation sessions are new.**

Read-sets are distinguished as the declaration requires. Development is bound to tape digest
`d12ff28a98cf9cc6…` (1,902 symbols, 2,152 files) recorded when E1 ran. Confirmation is bound to
digest `4f96e04261781ed6` (93 symbols, 99 files), frozen before any return was computed and
verified unchanged when this run finished.

---

## C. Development / Confirmation / Holdout Split

| block | definition | symbols | universe rows | H5 rows | session range |
|---|---|---|---|---|---|
| DEVELOPMENT | the symbols E1 was bound to | 1,902 | 70,738 | 1,729 | 2024-10-16 .. 2026-09-16 |
| CONFIRMATION | symbols absent from the E1 binding | 93 | 2,550 | 75 | 2026-04-20 .. 2026-09-16 |
| FINAL_HOLDOUT | - | - | - | - | **not constructible** |

**The final holdout could not be built, and that was declared in advance rather than discovered
afterwards.** A holdout has to be a chronological block of genuinely new data. Two independent
facts prevent one:

1. the collector adds symbols, not sessions, over a fixed window;
2. the daily snapshot `USB-HIST-V1` is frozen at 2026-09-16, and H5's universe reads its daily
   inputs from that snapshot at D-1, so a later session could not enter the universe even if its
   minutes existed.

The declaration forbids substituting a random or symbol-wise split and calling it a holdout, and
none was substituted. Under the declared rule, **no holdout means PASS is unreachable** in this
study regardless of what the numbers show.

---

## D. H5 Sample Counts

| | development | confirmation |
|---|---|---|
| H5 rows | 1,729 | **75** |
| matched rows (primary) | 1,724 (99.9%) | 72 (96.0%) |
| declared floor | - | 2,000 |
| shortfall | - | **1,928 rows** |
| additional symbols required at the observed yield | - | **2,118** |

Yield is 0.909 H5 rows per symbol, measured on development. The collector's remaining queue for
this scope window holds roughly 1,900 symbols, so **exhausting the entire remaining collection
would produce about 1,730 confirmation rows and still miss the declared floor** - and all of it
would remain inside the same two quarters. The floor was not lowered.

---

## E. Raw H5 Performance

| block | set | N | mean(bp) | median(bp) | win(%) |
|---|---|---|---|---|---|
| development | H5 | 1,729 | +16.45 | +6.51 | 52.46 |
| development | non-H5 | 69,009 | -0.50 | +0.00 | 49.06 |
| development | **raw gap** | | **+16.95** | | **+3.40%p** |
| confirmation | H5 | 75 | +45.95 | +24.56 | 58.67 |
| confirmation | non-H5 | 2,475 | +1.84 | +0.00 | 48.40 |
| confirmation | **raw gap** | | **+44.11** | | **+10.27%p** |

Horizons, H5 against non-H5 in the same block:

| block | R_1M | R_5M | R_15M | MFE_5M | MAE_5M |
|---|---|---|---|---|---|
| development | +6.69 / +0.04 | **+16.45 / -0.50** | +5.14 / -0.70 | +132.34 / +88.03 | -108.86 / -86.96 |
| confirmation | +28.23 / +2.17 | **+45.95 / +1.84** | +29.47 / -4.55 | +168.59 / +104.47 | -112.33 / -98.88 |

The development block reproduces E1 exactly, as it must. The confirmation block points the same
way and larger, on a sample that section N shows cannot support the claim.

---

## F. Matched-Control Performance

Coarsened exact matching on price x previous-day dollar volume x premarket dollar volume x gap
size, deterministic, no fitted model. The primary estimator first removes each session's own
universe mean, so a strong morning lifts H5 and its controls together and cancels.

| block | estimator | matched | match rate | mean lift | median lift | win-rate lift | downside ratio |
|---|---|---|---|---|---|---|---|
| development | primary (session demeaned) | 1,724 | 99.9% | **+17.01 bp** | **+9.47 bp** | **+3.68%p** | **0.88** |
| development | secondary (same session) | 1,484 | 86.0% | +13.53 bp | +5.87 bp | - | - |
| confirmation | primary | 72 | 96.0% | +31.02 bp | +13.85 bp | +8.79%p | 0.50 |
| confirmation | secondary (same session) | 30 | 40.0% | **-4.35 bp** | +2.26 bp | - | - |

**This is the most important result in the study.** On development, matching does not shrink H5's
edge at all: the raw gap is +16.95 bp and the matched lift is +17.01 bp, against 37,915 control
rows spread over 75 cells at a 99.9% match rate. Whatever H5 is, **it is not a price, liquidity or
gap-size premium in disguise** - the confound E1 flagged as one of its two reasons for withholding
a PASS is rejected. The control rows in H5's own cells average -2.18 bp; H5 averages +16.44 bp.

The downside ratio of 0.88 says H5 is *less* likely than its matched controls to lose 1% in the
first five minutes, which is unusual for a momentum condition and is the opposite of the E0 RVOL
failure mode.

The stricter same-session estimator holds on development (+13.53 bp at an 86% match rate) and
**flips sign on confirmation** (-4.35 bp at a 40% match rate). With 30 matched rows that
disagreement carries no information, but it is recorded rather than omitted.

---

## G. Price Neutralization

Development, matched lift by reported price bucket (thin buckets merged by the declared 30-row
rule, applied mechanically):

| price bucket | H5 rows | matched | mean lift | median lift | win-rate lift |
|---|---|---|---|---|---|
| $3-10 | 115 | 115 (100%) | **+56.07 bp** | +14.45 | +6.32%p |
| $10-20 | 219 | 218 (99.5%) | +24.16 bp | +10.86 | +1.08%p |
| $20-50 | 410 | 410 (100%) | +14.26 bp | +10.42 | +3.29%p |
| $50-100 | 359 | 359 (100%) | +17.48 bp | +14.78 | +5.97%p |
| $100-200 | 281 | 281 (100%) | +3.82 bp | +3.89 | +4.00%p |
| $200+ | 341 | 341 (100%) | +13.13 bp | +2.24 | +2.23%p |

**Positive in all six buckets.** The edge is far larger at the bottom (+56 bp under $10) and
thinner in the $100-200 band, but it does not vanish anywhere, including in $200+ names where a
small-cap variance story cannot apply. E1 could not separate these; this study can, and the answer
is that price explains part of the magnitude and none of the sign.

Confirmation, with only two merged buckets available: $3-50 gives **-29.91 bp** on 38 rows and
$50+ gives **+100.89 bp** on 34. One of two buckets positive is not a majority, so the criterion
records a failure - on 72 rows, which is what that number is worth.

---

## H. Liquidity Neutralization

Development, matched lift by previous-day dollar volume:

| bucket | H5 rows | matched | mean lift | median lift |
|---|---|---|---|---|
| $5-20M | 145 | 141 (97.2%) | **+63.04 bp** | +25.74 |
| $20-100M | 418 | 416 (99.5%) | +19.80 bp | +7.45 |
| $100-500M | 578 | 578 (100%) | +16.14 bp | +11.97 |
| $500M+ | 584 | 584 (100%) | +8.11 bp | +3.07 |

**Positive in all four buckets**, monotonically decreasing with size. The same reading as price:
liquidity scales the effect without explaining it. Confirmation is positive in both of its merged
buckets (+23.50 and +37.09 bp) on 38 and 34 rows.

---

## I. Extreme Removal

Matched mean lift after removing the largest matched differences:

| block | full | drop top 1 | drop top 5 | drop top 1% |
|---|---|---|---|---|
| development | +17.01 | +16.32 | +14.01 | **+8.76** |
| confirmation | +31.02 | +15.68 | **-18.50** | +15.68 |

Development survives comfortably; removing its top 17 rows leaves +8.76 bp. Confirmation collapses
when five rows are removed, which on 72 rows is the definition of a result driven by a handful of
observations.

---

## J. Concentration

| block | symbols | top 1 | top 5 | top 10 | Herfindahl |
|---|---|---|---|---|---|
| development | 831 | 5.9% | **20.9%** | 36.7% | 0.072 |
| confirmation | 39 | **50.1%** | **158.2%** | 218.4% | 1.389 |

Development is well spread: 831 distinct symbols, no symbol above 6%, and a Herfindahl of 0.072.
Confirmation is the opposite: POWL supplies 50.1% of the matched excess **from a single row**, the
top five supply 158% (so the remainder is net negative), and the Herfindahl of 1.389 is what you
get when a few single-row symbols carry everything.

---

## K. Time Consistency

Only the broad-universe period is treated as market evidence. Before 2026-04-20 the tape is 30
mega-caps, and those months are reported separately below rather than summed in.

Development, matched lift by month, broad-universe period:

| month | rows | mean lift | median lift | win(%) |
|---|---|---|---|---|
| 2026-04 | 70 | +45.78 | +14.29 | 54.29 |
| 2026-05 | 295 | +24.35 | +12.41 | 55.25 |
| 2026-06 | 236 | +32.74 | +13.80 | 55.08 |
| 2026-07 | 309 | +25.67 | +11.44 | 53.72 |
| 2026-08 | 438 | **+1.16** | +2.39 | 51.14 |
| 2026-09 | 181 | +9.01 | +8.83 | 55.25 |

Six months out of six positive, but the two most recent months are the two weakest (+1.16 and
+9.01 against +25 to +46 earlier). The same decay E1 saw quarter over quarter is visible here
month over month, and it is visible in the matched estimate, so it is not a composition effect.

The 18 months before 2026-04 hold 2 to 24 H5 rows each and swing from -58.72 to +92.49 bp. They
are reported in `development.json` and are not evidence about the broad market.

Confirmation by month: +58.27 (1 row), +168.16 (14), +55.73 (9), +25.73 (21), -56.34 (20),
-13.48 (7). Four of six positive on a monthly average of 12 rows.

---

## L. Regime Robustness

All regime labels use only information available at or before the decision time; SPY's same-day
regular-session outcome is never used.

| regime | development matched | mean lift | median lift | win-rate lift |
|---|---|---|---|---|
| previous day down | 765 | +23.13 bp | +8.16 | +3.21%p |
| previous day up | 958 | +9.95 bp | +6.53 | +3.23%p |
| SPY premarket negative | 580 | +19.47 bp | -0.63 | +1.44%p |
| SPY premarket positive | 1,143 | +16.22 bp | +11.72 | +4.70%p |

Positive in all four, with similar magnitude. H5 is not a single-regime artifact on the
development block. Confirmation splits into 28 and 41 matched rows (-6.40 and +35.95 bp); its SPY
premarket regimes hold no matched rows at all after cell matching.

---

## M. Cost Stress

```text
break_even_cost_bp = 17.01     (development, matched)
break_even_cost_bp = 31.02     (confirmation, matched, 72 rows)
```

Development, net matched lift by assumed round-trip cost:

| assumed cost | 0 bp | 5 bp | 10 bp | 15 bp | 20 bp | 25 bp | 30 bp |
|---|---|---|---|---|---|---|---|
| net matched lift | +17.0 | +12.0 | **+7.0** | +2.0 | **-3.0** | -8.0 | -13.0 |

This is the study's second substantive finding and it is a warning. **H5's entire matched edge is
17 basis points round trip.** It clears the declared 10 bp assumption with +7.0 bp to spare, is
marginal at 15 bp, and is gone at 20 bp. Stocks Basic carries no quote, so no spread is measured
anywhere here and the 10 bp figure is a declared assumption rather than an estimate. For the
sub-$20 names that carry the largest part of the effect, a 20 bp round trip is not a pessimistic
assumption.

The practical consequence: H5's viability is decided by execution quality, not by the signal. A
study that cannot measure spread cannot settle it.

---

## N. Bootstrap

10,000 resamples, seed 20260920. The session-cluster interval is the one the gate reads.

| block | unit | mean lift | 95% CI | P(lift <= 0) |
|---|---|---|---|---|
| development | session cluster | +17.01 bp | **[+6.51, +27.75]** | 0.0005 |
| development | symbol-session iid | +16.95 bp | [+8.00, +25.78] | 0.0000 |
| confirmation | session cluster | +31.23 bp | **[-27.52, +89.08]** | 0.1397 |
| confirmation | symbol-session iid | +31.86 bp | [-37.94, +99.74] | 0.1796 |

The development interval excludes zero under both units, and the cluster interval is wider than
the iid one as it should be. The confirmation interval spans more than a full percentage point and
contains zero comfortably. 72 rows cannot resolve a 30 bp effect.

---

## O. Final Holdout

**Not constructible.** See section C. Under the declared holdout rule this study cannot return a
PASS, and the best outcome available to it was `CONFIRMATION_PASS_BUT_HOLDOUT_INSUFFICIENT`, which
would still not have promoted H5. The confirmation block did not reach even that.

Building one requires a new frozen daily snapshot extending past 2026-09-16 **and** minute data for
the sessions after it. Neither exists today.

---

## P. PIT Audit

E1's two audits bind this study unchanged, because the confirmation block is processed by the same
imported code: the decision-time boundary audit (every bar from 09:25 ET onward replaced with
positive, finite, missing-preserving noise; no premarket feature moved, 41 symbols / 2,607
sessions) and the previous-session join audit (every row's daily input verified to be the previous
XNYS grid session, 70,738 rows, 0 wrong). Both passed in run `e1-32a530c8af9a`.

This study adds no feature and therefore no new look-ahead surface. Its own point-in-time
properties are structural: the matching variables are all E1 features already proven to respect
the 09:24 boundary, the session demeaning uses only same-session rows, and the control pool is
drawn from the same block and the same session-relative information as the treated rows.

Input immutability for this run: daily read-set digest identical before and after
(`7e790a8dcf1d…`), confirmation tape digest identical before and after (`4f96e04261781ed6`),
provider calls 0, writer lock not taken, B-minute collector untouched.

---

## Q. Regression

| suite | result |
|---|---|
| E0 (`backend/tests/strategy_e0`) | 32 passed |
| E1 (`backend/tests/strategy_e1`) | 28 passed |
| E1-H5 confirmation (`backend/tests/strategy_e1_h5`) | 25 passed |
| full suite (`backend/tests`) | see the run log accompanying this report |

A defect found and fixed during this study is worth recording, because its first version produced
a report-shaped result that was entirely an artifact. The subset analyses (price buckets, liquidity
buckets, regimes) passed a NaN-masked value array into the matcher; `_session_means` was not
NaN-aware, so one masked row turned its whole session's mean into NaN and every subset estimate
came back `matched 0 / nan`. The gate then read those empty tables and recorded both neutralization
criteria as failures. The fix was two-part and both parts matter: `_session_means` now ignores
missing values, and subset analyses pass an explicit `subset` mask so that the demeaning still uses
**all** universe rows of the session, which is what the declaration says. Four regression tests now
cover it, including one asserting that a bucket with no controls reports a zero match rate rather
than a number. The primary matched estimates were never affected - `R_5M` has no missing values in
either block - but the neutralization tables in section G and H are only meaningful after the fix.

---

## R. Verdict

## **INCONCLUSIVE**

Evaluated on the confirmation block, primary horizon R_5M, matched-control primary estimator:

| criterion | required | observed | |
|---|---|---|---|
| confirmation rows | >= 2,000 | **72** | FAIL |
| matched mean lift | >= +15 bp | +31.02 bp | ok |
| matched median lift | >= +5 bp | +13.85 bp | ok |
| matched win-rate lift | >= +2%p | +8.79%p | ok |
| matched mean lift after top 1% removed | >= +0.5 bp | +15.68 bp | ok |
| top-5 symbol share of matched excess | <= 40% | **158.2%** | FAIL |
| bootstrap session-cluster CI low | > 0 | **-27.52 bp** | FAIL |
| downside ratio vs control | <= 1.5 | 0.50 | ok |
| price neutralized positive | majority of buckets | **1 of 2** | FAIL |
| liquidity neutralized positive | majority of buckets | 2 of 2 | ok |
| time direction consistent | majority of months | 4 of 6 | ok |
| net positive at 10 bp | > 0 | +21.02 bp | ok |
| final holdout | required for PASS | **not constructible** | blocks PASS |

All four failures are the same failure wearing different clothes: 72 observations. The verdict is
INCONCLUSIVE rather than FAIL because the matched edge is positive and large in the confirmation
block, and rather than PASS because the sample cannot support any claim and no holdout exists.

Answering the question this study was built to answer - does the Open->5m excess reproduce under a
new sample, matched controls and a cost stress, with H5 untouched?

* **Matched controls: yes, and decisively.** On 1,724 matched development rows the edge is
  unchanged by matching (+17.01 bp against a +16.95 bp raw gap), positive in all six price buckets
  including $200+, positive in all four liquidity buckets, positive in all four regimes, positive
  in all six broad-universe months, and it survives removing its top 1%. The price and liquidity
  confounding that E1 could not rule out is now ruled out.
* **Cost: barely, and only under a generous assumption.** Break-even is 17 bp round trip. Net
  positive at 10 bp, marginal at 15, negative at 20.
* **New sample: unanswerable.** 75 H5 rows. Directionally positive, statistically empty, and
  dominated by single-row symbols.
* **Holdout: impossible today.** The collector adds symbols, not sessions, and the daily snapshot
  is frozen at 2026-09-16.

H5 is not promoted. Nothing about H5 was changed, relaxed or re-thresholded to reach this.

---

## S. If PASS

Not applicable: the verdict is INCONCLUSIVE and H5 is not promoted to Strategy E.

Recorded for the declaration that would finally test it: on the development sample H5's opening
five-minute excess is +17 bp after matching on price, liquidity and gap size, it is present at
every price level rather than only in cheap names, it reduces rather than raises the chance of a
1% loss, and it decays to near zero over the final two months of the sample. Its break-even
round-trip cost is 17 bp. None of this is approved, and all of it is in-sample.

---

## T. Next Step

H5 is not promoted, so this is not a Strategy E work list. What has to be true before the question
can be closed, in the order it should be settled:

1. **A frozen daily snapshot past 2026-09-16.** Without it no chronological holdout can exist, and
   without a holdout the declared gate cannot return a PASS however good the numbers look. This is
   the binding constraint, and it is infrastructure rather than research.
2. **Confirmation sample.** 2,118 more symbols at the observed yield. The remaining collection
   queue (~1,900 symbols) does not reach it, so either the scope widens or the window lengthens.
3. **A spread estimate.** At a 17 bp break-even, the difference between a 10 bp and a 20 bp round
   trip is the difference between a strategy and nothing. Stocks Basic cannot supply it; a quote
   source or a measured fill study can.
4. **The decay.** The matched lift falls from +25 to +46 bp in 2026-04..07 to +1.16 and +9.01 bp in
   2026-08..09. If that is regime, it needs a regime statement; if it is decay, H5 may already be
   gone.
5. **An exit rule, declared before it is measured.** H5's MFE and MAE over five minutes are +132
   and -109 bp against a 17 bp edge, so the exit decides the result.

The confirmation machinery (`backend/app/backtest/strategy_e1_h5_confirm/`) is reusable for all of
these as soon as a real holdout exists: the same declaration, the same matching, the same cost
stress, pointed at a new block.

No Strategy E directory, trading backtester, entry rule, exit optimization, position sizing,
broker, paper trading or LLM work was created, per the study scope.
