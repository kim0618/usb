# Strategy H — H-PV2C 2Y Growth Independent Issuer Confirmation Result V1

- result date: 2026-09-28
- preregistration: `304d38e`
- sample checksum: `6517b10e0fcec3a04a5e553a96e4b1c41052af94321434832533d1d6327fd729`
- result checksum: `47edac37c74cc7dc2f83f9b4a01845f83361e60a98da1e8ac84988992fe9307a`
- verdict: **NOT CONFIRMED**

## A. Repository State

PV2C began on `main` at `42726eb326e239b7cf31173fef149b840858d535`, ahead 11 and behind 0. Existing dirty files and all PV1/PV2 tracked files were preserved. No push occurred.

## B. Authoritative H State

PV1 Value remains `UNPROMISING / CLOSED`; PV2 Growth remains `INCONCLUSIVE`; official H1 remains `NOT AUTHORIZED`. PV2C does not rewrite either development result.

## C. Confirmation Purpose

Apply the identical frozen PV2 Growth hypothesis to a disjoint issuer group to test generalization and the prior concentration failure—not improve the model.

## D. Development Set Exclusion

All 37 PV2 primary CIKs and their stable FIGIs were excluded. Materialized confirmation overlap was exactly zero by CIK and zero by share-class/composite FIGI. Duplicate CIKs were forbidden.

## E. Confirmation Universe Selection

From the same 2024-10-25 dated CS snapshot, candidates required XNYS/XNAS/XASE, stable CIK/FIGI, one eligible security per CIK, >=451/501 local bars, and all 20 decision-date bars. Within exchange the frozen SHA-256 seed order selected fixed quotas without replacement or quota transfer.

## F. Confirmation Sample

120 securities: XNYS 60, XNAS 55, XASE 5. No market cap, return, fundamental outcome, or PV2 contributor determined selection. The committed sample is identified by checksum `6517b10e…d729`.

## G. SEC Ingestion

- requested CIKs: 120
- successful submissions: 120
- successful companyfacts: 119
- failed: CIK `0001371782` (companyfacts 404), not replaced
- HTTP requests: 241 (240×200, 1×404)
- downloaded bytes: 338,854,353
- issuers with canonical facts: 119
- canonical fact rows: 182,830

The existing identified/rate-limited SEC client was used and only selected CIKs were fetched.

## H. PIT / Comparable-period Audit

PV2's accession acceptance cutoff, after-open next-session rule, amendment versioning, exact comparable period families/tolerances, positive denominators, EPS $0.05 floor, YTD FCF matching, and transition exclusions were reused without changes. Per-row accession provenance is retained in the gitignored runtime artifact.

## I. Growth Feature Coverage

| Feature | Valid | Coverage | Median/date | Quality gate |
|---|---:|---:|---:|---|
| Revenue Growth | 2,142 | 89.3% | 107 | PASS |
| Operating Income Growth | 1,139 | 47.5% | 57.5 | PASS |
| EPS Growth | 1,300 | 54.2% | 65 | PASS |
| FCF Growth | 856 | 35.7% | 45 | FAIL |

The performance-blind rule therefore used Revenue, Operating Income and EPS, and excluded FCF. Composite coverage was 1,347/2,400 = 56.1%, above the frozen 50% gate.

## J. Growth Score Contract Equality vs PV2

Formulas, comparable-period rules, 2.5/97.5 winsorization, ascending percentile ranks, equal weights, minimum two valid inputs, decision dates, SPY benchmark and 10/20bp costs matched PV2. The same feature-quality rule—not return performance—caused FCF to miss confirmation inclusion.

## K. Decile Results

D1 through D10 mean 3M excess were −3.98%, +1.38%, −3.32%, −2.76%, −1.67%, −4.24%, +1.10%, +1.99%, −2.95%, and −0.89%. D10 net excess was −0.99%. D10−D1 net spread was +2.88%; 20bp stress spread was +2.68%. The positive spread came from D1 being more negative, not a positive D10.

## L. Top-N Results

Top 10% mean 3M net excess was −1.47%; Top 20% was −2.27%. Neither shortlist confirmed the development result.

## M. IC / Monotonicity

Mean monthly IC was −0.006, median +0.024, and positive IC fraction 65%. Empirical IC interval was [−0.257,+0.163]. Decile monotonicity was +0.297, just below the frozen +0.30 gate. D10 issuer-cluster bootstrap CI was [−5.71%,+3.36%].

## N. Monthly / Quarterly Stability

D10−D1 spread was positive in 14/20 months and 5/7 quarters. Stability of the spread alone did not rescue the negative D10 and IC. Quarterly spreads were positive in 2024-Q4, 2025-Q1/Q3, and 2026-Q1/Q2; negative in 2025-Q2/Q4.

## O. Individual Growth Diagnostics

Mean monthly ICs were Revenue −0.023, Operating Income −0.003, EPS −0.001, and FCF −0.011. All four were directionally nonpositive in confirmation.

## P. Extreme Return Robustness

After removing top 1% winners: D10 −3.63%, spread +0.25%. After top five: D10 −0.99%, spread +2.88%. After top ten contributing issuers: D10 −3.06%, spread +1.41%. D10 stayed negative in every audit, so C5 failed.

## Q. Issuer Concentration

Single issuer 22.3%, top five 63.3%, top ten 87.8%. All frozen limits passed. The larger universe solved the mechanical concentration problem, but the Growth return relation did not generalize.

## R. PV2 vs PV2C Comparison

| Metric | PV2 development | PV2C confirmation |
|---|---:|---:|
| Securities | 37 | 120 |
| Composite coverage | 86.4% | 56.1% |
| D10 net excess | +16.08% | −0.99% |
| D10−D1 | +14.21% | +2.88% |
| Mean IC | +0.125 | −0.006 |
| Median IC | +0.169 | +0.024 |
| Positive IC months | 80% | 65% |
| Monotonicity | +0.661 | +0.297 |
| Positive spread months | 70% | 70% |
| Positive quarters | 71.4% | 71.4% |
| Single issuer contribution | 47.5% | 22.3% |
| Top five contribution | 93.2% | 63.3% |
| Top-10-issuer removal spread | +3.42% | +1.41% |

The concentration finding improved, but the central alpha direction did not reproduce.

## S. Statistical / Sample Limitations

Both tracks share the same short period and overlapping labels, so this is issuer-disjoint rather than time-independent confirmation. The universe remains based on one recent snapshot; dividends and a survivorship-safe master are absent. Companyfacts failed for one issuer and FCF coverage failed its frozen gate.

## T. Tests

`101 passed, 4 warnings`. New tests cover development CIK/FIGI leakage, duplicate CIK rejection, deterministic quota selection, sample immutability, PV2 Growth formula/decision/cost equality, concentration thresholds and gate aggregation.

## U. Freeze Commit

`304d38e` — `research(strategy-h): freeze 2Y growth confirmation`; checksum `2e5b548cee80e6ab8c7336edb5033fa868a08a516d6affde531ce32c816fa31d`.

## V. Result Commit

Recorded after this document; final handoff supplies the full SHA.

## W. Confirmation Gates C1–C8

| Gate | Result |
|---|---|
| C1 D10 positive | FAIL |
| C2 spread positive | PASS |
| C3 mean IC positive | FAIL |
| C4 monotonicity >=0.30 | FAIL |
| C5 removals retain D10 and spread | FAIL |
| C6 concentration | PASS |
| C7 base cost retains both | FAIL |
| C8 coverage | PASS |

## X. Verdict

```text
H-PV2C = NOT CONFIRMED
```

C8 passed and only one of C1–C4 passed, satisfying the frozen NOT CONFIRMED rule.

## Y. Strategy H State

```text
Strategy H = CANDIDATE / PREVALIDATION
OFFICIAL H1 = NOT AUTHORIZED
```

## Z. Paid Long-History Recommendation

```text
NOT JUSTIFIED
```

PV2's positive development result did not reproduce in a larger disjoint issuer sample. This evidence does not justify paid full historical validation of the frozen Growth model.

## AA. Next Action

1. Close PV2C without retuning.
2. Do not combine Value and Growth.
3. Do not run PV3 automatically.
4. Preserve development and confirmation artifacts separately.
5. Reassess whether Strategy H should continue before any further data purchase.
6. Keep H1 unauthorized.

## Final declarations

PV1 modified: NO. PV2 modified: NO. Development issuers reused: NO. Growth model changed: NO. Value/Quality/GPT/paid history/official H1/post-hoc tuning: NO. Push: NO.
