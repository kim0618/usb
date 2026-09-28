# Strategy H — H-PV3 2Y Quality-only Prevalidation Result V1

## A. Repository State

Branch `main`; pre-work HEAD `5def980875b8818605f4b8b732ecbdac481ae730`; origin ahead 13/behind 0. The pre-existing dirty worktree was preserved and excluded from both PV3 commits.

## B. Authoritative H State

H0/H0.5 INCONCLUSIVE; H0.6 FAIL; PV1 Value UNPROMISING/CLOSED; PV2 Growth INCONCLUSIVE; PV2C Growth NOT CONFIRMED/CLOSED. Strategy H remains CANDIDATE / PREVALIDATION; official H1 is not authorized.

## C. PV3 Purpose

Test the independent Quality-level hypothesis, without Value, Growth, composites, GPT, overlays, paid data, entry/exit, portfolio or broker logic.

## D. Research Window

501 grouped sessions, 2024-09-17–2026-09-16; 20 monthly decisions, 2024-10-31–2026-05-29; exact 63-session primary and 21-session secondary horizons.

## E. Universe

The frozen PV2C 120 disjoint issuers were reused unchanged (checksum `6517b10e0fcec3a04a5e553a96e4b1c41052af94321434832533d1d6327fd729`). This is a **LIMITED 2Y PREVALIDATION UNIVERSE; NOT SURVIVORSHIP-SAFE FULL HISTORICAL UNIVERSE**.

## F. PIT Contract

Only facts accepted by the decision-session open were eligible. After-open facts become eligible next session. Accession/acceptance/period/tag provenance is retained in the runtime artifact; no future/current fallback or amendment backfill was used.

## G. Quality Feature Selection Contract

Candidates were selected for PIT and mapping stability before returns. ROE, ROIC and debt-derived leverage were excluded ex ante for denominator/tax/debt normalization concerns. Entry gate: >=40% of 2,400 rows and median >=10 names/date.

## H. Quality Feature Definitions

Operating Margin = OI/revenue on identical discrete Q/FY periods; FCF Margin = (OCF−CapEx)/revenue on identical YTD/FY periods; ROA = annualized net income / average boundary assets; Cash Conversion = OCF/net income with net income >$1m; Cash/Assets uses identical instants. All five are higher-is-better levels.

## I. Feature Coverage

| Factor | Valid | Coverage | Median/date | Gate |
|---|---:|---:|---:|---|
| Operating Margin | 1,893 | 78.88% | 95 | PASS |
| FCF Margin | 1,813 | 75.54% | 90 | PASS |
| ROA | 2,365 | 98.54% | 119 | PASS |
| Cash Conversion | 1,470 | 61.25% | 74 | PASS |
| Cash/Assets | 2,354 | 98.08% | 118 | PASS |

## J. Invalid / Edge Cases

Operating Margin: 471 missing/period mismatches and 36 invalid revenues. FCF Margin: 559 missing/duration mismatches and 28 invalid revenues. ROA: 26 missing durations and 9 missing average assets. Cash Conversion: 904 nonpositive/near-zero earnings and 26 missing/duration mismatches. Cash/Assets: 46 missing/instant mismatches. Invalid ratios were UNKNOWN before winsorization.

## K. Quality Score

All five gated in. Date-wise 2.5/97.5% winsorization, ascending average-tie percentile ranks, equal-weight mean, minimum two factors. No post-result removal or weighting occurred.

## L. Forward Return / Benchmark / Cost

Unadjusted close-to-close exact endpoints, SPY-relative, dividends omitted, no endpoint fallback. Base 10bp and stress 20bp round trip; long-short spread pays two legs.

## M. Decile Results

| D1 | D2 | D3 | D4 | D5 | D6 | D7 | D8 | D9 | D10 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 75.39% | 64.30% | 42.12% | 12.66% | −1.98% | −3.23% | −1.59% | −2.32% | −1.71% | −1.90% |

D10 net is −2.00%; D10−D1 net is −77.49%. At 20bp these are −2.10% and −77.69%.

## N. Top-N Results

Top 10%: 240 rows, mean net excess −1.96%. Top 20%: 480 rows, −1.74%.

## O. IC / Monotonicity

Mean monthly IC +0.0540, median +0.0870, positive fraction 60%, empirical monthly interval [−0.1144,+0.2253]. Decile monotonicity is −0.7091. D10 issuer-cluster bootstrap 95% CI is [−6.30%,+1.63%].

## P. Monthly / Quarterly Stability

Positive-spread months: 35%. Positive quarters: 28.6% (2/7). Quarterly spreads: 2024-Q4 +13.25%; 2025-Q1 −6.73%, Q2 −114.95%, Q3 −50.43%, Q4 +10.17%; 2026-Q1 −120.99%, Q2 −370.38%. The complete monthly table is retained in the checksummed result JSON.

## Q. Individual Quality Factor Diagnostics

| Factor | Mean IC | Median IC | Months |
|---|---:|---:|---:|
| Operating Margin | +0.0939 | +0.1027 | 20 |
| FCF Margin | +0.0911 | +0.0998 | 20 |
| ROA | +0.0712 | +0.0904 | 20 |
| Cash Conversion | −0.0530 | −0.0489 | 20 |
| Cash/Assets | −0.0446 | −0.0227 | 20 |

These diagnostics did not alter the composite.

## R. Market Cap Lane Diagnostic

Mean net excess/count: MICRO +55.69%/741, H-SMALL +0.85%/428, H-MID −3.79%/526, H-UPPER-MID −3.85%/299, H-LARGE +0.31%/139, UNKNOWN +20.35%/241. This secondary diagnostic did not change the universe.

## S. Extreme Return Robustness

| Removal | D10 net | D10−D1 net |
|---|---:|---:|
| Top 1% observations | −2.00% | +4.85% |
| Top 5 observations | −2.00% | −45.21% |
| Top 10 issuers | −2.00% | +1.28% |

D10 stays negative in every removal, so P5 fails. The very large raw D1 return is extreme-observation sensitive, but removing it does not make high Quality profitable.

## T. Issuer Concentration

Positive D10 contribution shares: single 19.21%, top five 65.34%, top ten 87.96%; all frozen limits pass.

## U. Comparison vs PV1 / PV2 / PV2C

| Metric | PV1 Value | PV2 Growth Dev | PV2C Growth Confirm | PV3 Quality |
|---|---:|---:|---:|---:|
| D10 net | −3.98% | +16.08% | −0.99% | −2.00% |
| D10−D1 | −16.81% | +14.21% | +2.88% | −77.49% |
| Mean IC | −0.2098 | +0.1252 | −0.0062 | +0.0540 |
| Median IC | not recorded | +0.1694 | +0.0239 | +0.0870 |
| Monotonicity | −0.6970 | +0.6606 | +0.2970 | −0.7091 |
| Positive months | not recorded | 70% | 70% | 35% |
| Positive quarters | 14.3% | 71.4% | 71.4% | 28.6% |
| Extreme robustness | FAIL | PASS | FAIL (D10) | FAIL |
| Concentration | FAIL | FAIL | PASS | PASS |

## V. Statistical / Sample Limitations

Only 20 overlapping 63-session labels exist. The snapshot universe is not survivorship-safe, dividends are absent, and unadjusted prices make split/extreme-return sensitivity material. Issuer-cluster bootstrap crosses zero. This is limited prevalidation, not investable evidence.

## W. Tests

`331 passed, 7 warnings` across Strategy H0/PV, Strategy C, Strategy D, Strategy E0 and EQM-V0. The six PV3 tests cover formulas, period/duration and instant matching, average/invalid assets, near-zero earnings, coverage gates, winsor/ranking/composite/minimum count, deciles, IC, concentration and verdict aggregation.

## X. Freeze Commit

`af0baf7` — `research(strategy-h): freeze 2Y quality prevalidation`; contract checksum `f794ef1ac3add1cdc7ac9482c4d921197fa0a817625987fd4160d3ec94eba809`.

## Y. Result Commit

Recorded after this report; final handoff supplies the full SHA. Runtime result checksum: `e2a003018fcfc2dfd428e81976f5b313d64d568eaa5b955b8cebaaf7bc148dd0`.

## Z. Gate Results P1-P8

| Gate | Result |
|---|---|
| P1 D10 positive | FAIL |
| P2 spread positive | FAIL |
| P3 mean IC positive | PASS |
| P4 monotonicity >=0.30 | FAIL |
| P5 all removals directionally positive | FAIL |
| P6 concentration limits | PASS |
| P7 base cost preserves signal | FAIL |
| P8 coverage | PASS |

## AA. Verdict

**H-PV3 2Y QUALITY = UNPROMISING.** Coverage is adequate; the limitation is SIGNAL, not DATA.

## AB. Strategy H Continuation Assessment

**RECOMMEND CLOSE QUANT CORE.** Do not automatically proceed to Value+Quality, Growth+Quality, three-factor composites, GPT/Future Business/Catalyst overlays, or PV3C.

## AC. Paid Long-History Recommendation

**NOT JUSTIFIED.** PV1 failed, PV2 did not confirm on PV2C, and PV3 is unpromising despite adequate coverage.

## AD. Next Action

1. Close the current Strategy H Quant Fundamental Core track.
2. Preserve contracts, checksums and runtime provenance as audit evidence.
3. Do not purchase long-history data or authorize H1 from these results.
4. Treat any future Quality-change or other hypothesis as a separately preregistered project, not a retune.
