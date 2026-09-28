# Strategy H — H-PV1 2Y Value Prevalidation Result V1

- result date: 2026-09-28
- preregistration: `85a3266`
- result artifact SHA-256: `b8622d4fb63e5aa4db93f4972cbb334abd84832cf4026d39e73a0727a45f1752`
- H-PV1 verdict: **UNPROMISING**
- official H1: **NOT RUN / NOT AUTHORIZED**

## A. Repository State

The run began on `main` at `f7fc630e7bd46451fb9643ce61c3816ad4e572f8`, ahead 7 and behind 0 relative to `origin/main`. Pre-existing modified and untracked files were preserved and excluded. No push occurred.

## B. Authoritative H State

H0 remains `INCONCLUSIVE`, H0.5 remains `INCONCLUSIVE`, and H0.6 remains `FAIL` because Developer entitlement was not active. This H-PV track does not alter those states. Strategy H remains `CANDIDATE / PREVALIDATION`.

## C. 2Y Prevalidation Definition

This is a limited, local-data Value screen, not official H1. It tests only three frozen annual PIT yields against 1M/3M price returns. Growth, Quality, GPT analysis, analyst revisions, optimization, entries, valuation targets, portfolios, and trading are absent.

## D. Actual Research Window

The directory has 502 gzip files, but 2024-09-16 is a retained `NOT_AUTHORIZED` error artifact without a grouped body. The usable market panel is therefore 501 complete XNYS sessions from 2024-09-17 through 2026-09-16, containing 5,760,100 rows. There were zero duplicate symbol/session rows and zero invalid/non-finite OHLCV rows. The preregistration's preliminary “502 complete” statement is corrected here; no decision date depends on 2024-09-16.

Monthly decisions span 2024-10-31 through 2026-05-29. Twenty dates have an exact 63-session future endpoint. Later month-ends were truncated rather than assigned a last-available price.

## E. Universe

The fixed intersection contains 43 dated-reference securities from the existing H0 40-CIK store. Six rows from three multi-class CIKs—BF.A/BF.B, BIO/BIO.B, and UHAL/UHAL.B—are `MULTI_CLASS_UNRESOLVED` and excluded. The primary universe has 37 single-class securities.

This is a **LIMITED 2Y PREVALIDATION UNIVERSE, NOT A SURVIVORSHIP-SAFE FULL HISTORICAL UNIVERSE**. Its 2024-10-25 snapshot creates recent-universe bias and cannot establish complete delisting coverage.

## F. PIT Fundamental Coverage

All facts were joined by accession to SEC submissions `acceptanceDateTime`. The feature cutoff was the decision session's regular open; future filings and current backfill were rejected. After-hours filings became usable on the next regular session. Amendments retained knowledge-time ordering.

PIT shares resolved for 671/740 single-class security-date candidates (90.7%); 69 were missing, stale beyond 135 days, or split-inconsistent. No current shares fallback occurred.

## G. Value Feature Contract

- Earnings Yield = positive latest PIT annual net income / PIT market cap.
- FCF Yield = positive (annual operating cash flow − annual capex) / PIT market cap.
- Sales Yield = positive annual revenue / PIT market cap.

Annual facts were 10-K/10-K-A durations of 300–400 days. Negative earnings/FCF, nonpositive revenue/cap, missing fields, and invalid denominators became missing. EV/EBITDA and EV/Sales were not introduced after results.

## H. Value Feature Coverage

| Feature | Valid rows | Coverage | Median | 2.5%–97.5% raw range |
|---|---:|---:|---:|---:|
| Earnings Yield | 598 | 80.8% | 5.12% | 0.46%–28.31% |
| FCF Yield | 418 | 56.5% | 7.28% | 0.16%–24.26% |
| Sales Yield | 671 | 90.7% | 0.628 | 0.081–6.229 |

## I. Value Score

Each feature was winsorized within date at fixed 2.5/97.5 percentiles and percentile-ranked with higher yield more attractive. `ValueScore` was the equal-weight mean of at least two available ranks. No weights, cutoffs, or features changed after the result.

## J. Forward Return Contract

Primary was exact 63-session unadjusted close-to-close price return; secondary was exact 21-session return. Labels missing either endpoint were invalid. Returns exclude dividends, so they are price returns rather than total returns. No intraday execution model was used.

## K. Benchmark / Cost

SPY same-session price return was the sole benchmark. Net excess deducts 10bp round trip from long groups and 20bp from D10-D1's two legs. The 20bp stress deducts 20bp and 40bp respectively.

## L. Decile Results

| Decile | Mean 3M excess |
|---:|---:|
| D1, least attractive | +12.73% |
| D2 | +9.83% |
| D3 | +0.03% |
| D4 | +1.98% |
| D5 | −3.37% |
| D6 | +0.61% |
| D7 | −3.60% |
| D8 | −2.63% |
| D9 | +1.32% |
| D10, most attractive | −3.88% |

D10 had 60 observations, median excess −1.21%, hit rate 41.7%, and 10bp net excess −3.98%. D1 had 72 observations, median +8.06%, hit rate 70.8%. D10−D1 net spread was −16.81% at base cost and −17.01% at stress cost.

## M. Top-N Results

- Top 10%: 72 observations, mean 3M net excess −2.89%.
- Top 20%: 132 observations, mean 3M net excess −1.85%.

Neither frozen shortlist was positive.

## N. IC / Monotonicity

Mean monthly Spearman IC was −0.210; only 15% of monthly ICs were positive. Spearman correlation between decile number and decile mean excess was −0.697. The relationship ran opposite the hypothesis rather than merely lacking monotonicity.

D10 issuer-cluster bootstrap 95% CI for net excess was [−8.89%, +5.31%]. The short sample remains statistically uncertain, but its central direction is negative.

## O. Monthly / Quarterly Stability

Only 2/20 monthly D10−D1 spreads were positive (10%). Only 2026-Q2 was positive among seven represented quarters (14.3%). Six earlier quarters were negative, including −36.49% in 2026-Q1. This is not repeated positive directionality.

## P. Market Cap Lane Diagnostic

This is secondary and coverage-conditional.

| Lane | Rows | Mean 3M net excess |
|---|---:|---:|
| MICRO | 12 | −1.96% |
| H-SMALL | 46 | −3.65% |
| H-MID | 258 | +1.07% |
| H-UPPER-MID | 154 | +0.44% |
| H-LARGE | 150 | +5.32% |

Lane differences are descriptive only and did not drive the primary verdict.

## Q. Sector / Concentration

No PIT-reliable historical sector panel was available, so sector concentration is `INSUFFICIENT` rather than current-sector backfill. Within positive D10 contributions, the largest security contributed 33.6%, top five 92.3%, and top ten 100%. The frozen top-five/top-ten limits failed.

## R. Extreme Return Removal

| Audit | D10 net excess | D10−D1 net spread |
|---|---:|---:|
| Remove top 1% winners | −3.98% | −12.06% |
| Remove top 5 observations | −3.98% | −12.90% |
| Remove top 10 contributing securities | −4.07% | −5.31% |

Every audit remained negative. The original hypothesis was not modified.

## S. Statistical / Sample Limitations

The panel has only 20 overlapping monthly observations, 37 primary securities, a single recent-universe snapshot, no dividend return, no reliable PIT sector history, and limited regimes. Monthly 3M labels overlap strongly. Cross-sectional rows are not independent, and the bootstrap interval crosses zero. These limitations prevent a long-run strategy conclusion, but they do not reverse the frozen directional gate failures.

## T. Tests

`87 passed, 4 warnings` across Strategy H0/PV, Strategy C-E0, and EQM-V0. Tests cover PIT cutoff and amendment versioning, future filing rejection, after-hours availability, exact horizon truncation, invalid ratios, deterministic ranking/deciles, benchmark-compatible labels, cost application, missing values, extreme contribution arithmetic, shares/splits, multi-class rejection, and lane boundaries.

## U. Freeze Commit

`85a3266` — `research(strategy-h): freeze 2Y value prevalidation`

Contract checksum: `3c87c84f65fc5d09a9da503ce70a343234e9e3e509026521c9e30d10cfead23a`.

## V. Result Commit

Recorded after this document; the final handoff supplies the full SHA.

## W. Gate Results

| Gate | Result | Evidence |
|---|---|---|
| P1 | FAIL | D10 3M net excess −3.98% |
| P2 | FAIL | D10−D1 net spread −16.81% |
| P3 | FAIL | mean IC −0.210 |
| P4 | FAIL | monotonicity −0.697 |
| P5 | FAIL | all three robustness spreads negative |
| P6 | FAIL | top-5/top-10 contribution limits exceeded |
| P7 | FAIL | 10bp cost did not preserve positive signal |
| P8 | PASS | 20 dates, median ~31 names/date, 83.8% coverage |

## X. Verdict

```text
H-PV1 2Y VALUE = UNPROMISING
Strategy H overall = CANDIDATE / PREVALIDATION
```

This is not a Strategy H alpha FAIL and does not alter H0/H0.5/H0.6. It says the frozen simple Value hypothesis did not justify itself in the available two-year sample.

## Y. Paid Long-History Recommendation

```text
NOT YET JUSTIFIED
```

The Value-only prevalidation does not currently justify buying 5–10 years of data for full validation. This is a data-purchase priority decision, not an investment-strategy PASS/FAIL.

## Z. Next Action

1. Stop this frozen Value PV1 track without retuning it.
2. Do not run PV2 automatically.
3. Preserve the artifacts and limitations for future comparison.
4. If Strategy H continues, request explicit approval for a separately preregistered non-Value hypothesis.
5. Do not purchase long-history data on this PV1 evidence alone.
6. Keep official H1 unauthorized.

## Final declarations

```text
official H1 run? NO
GPT used? NO
paid historical data used? NO
post-hoc tuning? NO
push = NO
```
