# Strategy H — H-PV2 2Y Growth-Only Prevalidation Result V1

- result date: 2026-09-28
- preregistration: `79c3bc4`
- artifact SHA-256: `d95ed8617f8f39210dd2053b0f0b8bad1436a353341bfc632034876797fe7b47`
- H-PV2: **INCONCLUSIVE**
- H-PV1 Value: **UNPROMISING / CLOSED / UNCHANGED**

## A. Repository State

PV2 began on `main` at `ed16a664a382200241334db39bec8ba09fb17d7f`, ahead 9 and behind 0. Pre-existing dirty files and every PV1 file were preserved. No push occurred.

## B. Authoritative H State

H0 and H0.5 remain `INCONCLUSIVE`; H0.6 remains `FAIL` because Developer entitlement was inactive; H-PV1 Value remains `UNPROMISING / CLOSED`. Strategy H remains `CANDIDATE / PREVALIDATION`; official H1 remains `NOT AUTHORIZED`.

## C. PV2 Definition

PV2 is an independent Growth-only hypothesis. It uses no Value Score, Value+Growth composite, Quality, GPT, revisions, optimization, entries, targets, portfolios, or trading. It reuses PV1 infrastructure and identical market inputs.

## D. Research Window

The usable daily panel remains 501 sessions, 2024-09-17 through 2026-09-16. Decision dates remain the same 20 month-ends from 2024-10-31 through 2026-05-29. Primary and secondary endpoints remain exact 63 and 21 sessions.

## E. Universe

The fixed universe is unchanged: 43 dated-reference rows, six multi-class rows excluded, and 37 primary single-class securities. No CIK or ticker was added or replaced.

`LIMITED 2Y PREVALIDATION UNIVERSE — NOT SURVIVORSHIP-SAFE FULL HISTORICAL UNIVERSE`.

## F. PIT / Comparable-period Contract

Every current/prior fact was accepted by the decision session's regular open and retained accession/acceptance provenance in `provenance.json.gz`. Later amendments were not injected backward.

Revenue, operating income and EPS matched 70–110 day Q1/Q2/Q3 discrete quarters or 300–400 day FY periods. FCF matched OCF−capex on exact common periods: Q1, Q2 YTD, Q3 YTD, or FY. Current/prior required the same unit, canonical tag, fiscal period/family, 345–385 day end separation, and duration tolerance. Annual, discrete-quarter and YTD observations were never mixed.

## G. Growth Feature Definitions

- Revenue Growth = current comparable revenue / prior revenue − 1.
- Operating Income Growth = current / prior comparable operating income − 1.
- Diluted EPS Growth = current / prior comparable diluted EPS − 1.
- FCF Growth = current comparable (OCF−capex) / prior comparable FCF − 1.

Both values had to be positive; EPS prior also had to be at least $0.05. Transitions were excluded rather than assigned extreme growth.

## H. Feature Coverage

All four features passed the performance-blind 40%/median-10 quality gate and therefore remained in the frozen composite.

| Feature | Valid | Coverage | Median/date |
|---|---:|---:|---:|
| Revenue Growth | 720 | 97.3% | 36 |
| Operating Income Growth | 497 | 67.2% | 25 |
| EPS Growth | 562 | 75.9% | 28 |
| FCF Growth | 360 | 48.6% | 18 |

Composite evaluation retained 639/740 rows, or 86.4%.

## I. Transition / Invalid Cases

- Operating income: 22 loss→profit, 26 profit→loss, 36 nonpositive-prior, 159 missing comparable.
- EPS: 55 loss→profit, 39 profit→loss, 44 nonpositive/near-zero prior, 40 missing comparable.
- FCF: 24 negative→positive, 50 positive→negative, 77 nonpositive prior, 49 duration mismatches, 180 missing comparable.
- Revenue: 20 missing comparable; no invalid sign transitions.

No transition bonus was added.

## J. Growth Score

All four quality-passing features were winsorized at 2.5/97.5 percentiles and ascending percentile-ranked within date. GrowthScore was their equal-weight mean with at least two valid components. Performance did not change feature membership or weights.

## K. Forward Return / Benchmark / Cost

Labels, SPY benchmark and costs exactly match PV1: exact unadjusted close-to-close 63-session primary and 21-session secondary price returns; no endpoint fallback or dividend; 10bp base and 20bp stress round trip, with two legs for D10−D1.

## L. Decile Results

| Decile | Mean 3M excess |
|---:|---:|
| D1 | +1.77% |
| D2 | −2.54% |
| D3 | −4.43% |
| D4 | −3.73% |
| D5 | −3.56% |
| D6 | +0.62% |
| D7 | −0.87% |
| D8 | +2.27% |
| D9 | +5.24% |
| D10 | +16.18% |

D10 net excess was +16.08% at base cost and +15.98% at stress. D10−D1 net spread was +14.21% at base and +14.01% at stress.

## M. Top-N Results

- Top 10%: 78 observations, mean 3M net excess +13.57%.
- Top 20%: 138 observations, mean 3M net excess +9.78%.

## N. IC / Monotonicity

Mean monthly IC was +0.125, median +0.169, and 80% of monthly ICs were positive. Monthly IC's empirical 2.5–97.5% interval was [−0.233, +0.335]. Decile monotonicity was +0.661. D10 issuer-cluster bootstrap 95% CI was [+1.80%, +25.22%].

## O. Monthly / Quarterly Stability

D10−D1 was positive in 14/20 months (70%) and 5/7 quarters (71.4%). Negative quarters were 2024-Q4 and 2026-Q2. The result is not uniformly positive but is materially more stable than PV1.

## P. Individual Growth Factor Diagnostics

| Feature | Mean monthly IC | Median IC |
|---|---:|---:|
| Revenue Growth | +0.145 | +0.170 |
| Operating Income Growth | +0.154 | +0.149 |
| EPS Growth | +0.070 | +0.092 |
| FCF Growth | +0.028 | +0.055 |

These diagnostics did not alter the composite.

## Q. Market Cap Lane Diagnostic

| Lane | Rows | Mean 3M net excess |
|---|---:|---:|
| MICRO | 12 | −1.96% |
| H-SMALL | 45 | −4.31% |
| H-MID | 238 | +0.54% |
| H-UPPER-MID | 160 | −0.97% |
| H-LARGE | 144 | +5.56% |
| UNKNOWN | 40 | −0.02% |

This was secondary; no lane was selected for the primary result.

## R. Concentration

Issuer-level positive D10 contribution shares were:

- single issuer: 47.5% versus <=35% limit;
- top five: 93.2% versus <=75% limit;
- top ten: 100% versus <=90% limit.

P6 therefore failed. This concentration prevents PROMISING despite otherwise positive evidence.

## S. Extreme Return Removal

| Audit | D10 net excess | D10−D1 net spread |
|---|---:|---:|
| Remove top 1% winners | +7.12% | +5.25% |
| Remove top 5 winners | +9.31% | +7.43% |
| Remove top 10 contributing issuers | +1.83% | +3.42% |

All remained directionally positive, so P5 passed. The last audit retained only 14 D10 observations and is not enough to waive concentration risk.

## T. Comparison vs PV1 Value

| Metric | PV1 Value | PV2 Growth |
|---|---:|---:|
| D10 net excess | −3.98% | +16.08% |
| D10−D1 net spread | −16.81% | +14.21% |
| Mean IC | −0.210 | +0.125 |
| Monotonicity | −0.697 | +0.661 |
| Positive months | 10% | 70% |
| Verdict | UNPROMISING | INCONCLUSIVE |

Growth shows independent information in this sample. Value was not used or combined with it.

## U. Statistical / Sample Limitations

There are only 20 overlapping month-end observations and 37 securities, one recent dated universe snapshot, no dividends, limited regimes, no survivorship-safe master, and no independent confirmation set. Concentration is high, and later 2026 months weakened. These prevent a PROMISING or full-validation conclusion.

## V. Tests

`94 passed, 4 warnings` across Strategy H0/PV, C-E0 and EQM-V0. PV2 tests cover fiscal-period matching, duration/annual-quarter contamination, future filing rejection, after-hours availability through inherited H tests, YTD FCF matching, sign/transition handling, growth arithmetic, deterministic winsor/rank, coverage gate, minimum composite inputs, deciles, labels, benchmark, costs, IC, monotonicity, extreme removal and contribution.

## W. Freeze Commit

`79c3bc4` — `research(strategy-h): freeze 2Y growth prevalidation`

Checksum: `17464dd446601d960863c79692efb133f659355263d0ee591f9745f304040eeb`.

## X. Result Commit

Recorded after this document; final handoff supplies the full SHA.

## Y. Gate Results

| Gate | Result | Evidence |
|---|---|---|
| P1 | PASS | D10 net +16.08% |
| P2 | PASS | spread +14.21% |
| P3 | PASS | mean IC +0.125 |
| P4 | PASS | monotonicity +0.661 |
| P5 | PASS | every removal remained positive |
| P6 | FAIL | issuer concentration exceeded every limit |
| P7 | PASS | base cost preserved both signals |
| P8 | PASS | 20 dates, median >30 names, 86.4% coverage |

## Z. Verdict

```text
H-PV2 2Y GROWTH = INCONCLUSIVE
Strategy H overall = CANDIDATE / PREVALIDATION
OFFICIAL H1 = NOT AUTHORIZED
```

The Growth signal is directionally encouraging but fails the frozen concentration gate. PROMISING required all eight gates.

## AA. Paid Long-History Recommendation

```text
POTENTIALLY JUSTIFIED
```

Growth provides enough positive evidence to reconsider a longer-history validation, but concentration and the lack of an independent sample make immediate full validation purchase unjustified. This is not Strategy H PASS.

## AB. Next Action

1. Stop PV2 without retuning features, thresholds, or weights.
2. Do not construct Value+Growth or run PV3 automatically.
3. Preserve issuer-level provenance and concentration evidence.
4. If approved, first seek a low-cost independent Growth confirmation or actual Developer entitlement probe.
5. Buy long history only under a separate budget/data contract.
6. Keep official H1 unauthorized.

## Final declarations

```text
PV1 modified? NO
Value Score used? NO
GPT used? NO
paid historical data used? NO
official H1 run? NO
post-hoc tuning? NO
push = NO
```
