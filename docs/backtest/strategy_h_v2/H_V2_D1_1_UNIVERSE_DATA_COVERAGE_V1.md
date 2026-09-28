# Strategy H-V2 - D1.1 Universe Data Coverage V1

- declared: 2026-09-28
- status: **DATA COVERAGE / PIPELINE CORRECTNESS - NOT AN ALPHA RESULT**
- stage: **H-V2-D1.1**
- parent: `docs/backtest/strategy_h_v2/H_V2_D1_UNIVERSE_CHANGE_ENGINE_V1.md`
- implementation: `backend/app/backtest/strategy_h_v2/eligibility.py` (state-model fix),
  `backend/app/backtest/strategy_h_v2/acquisition.py` (new), `backend/app/dev/
  acquire_strategy_h_v2_fundamentals.py` (new), `backend/app/dev/run_strategy_h_v2_d1.py` (updated)
- tests: 144 passing (`backend/tests/strategy_h_v2/`, 74 tests) alongside the 63-test Strategy H0/PV
  regression suite, 0 failures
- acquisition run: `data/runtime/strategy_h_v2/d1_1/D1_1-20260928T044641Z.manifest.json`
  (gitignored runtime artifact)
- pipeline rerun: `data/runtime/strategy_h_v2/d1/D1-20260928T054937Z/` (gitignored runtime artifact)

D1.1 does not change what E1/E2/E3 mean (D0's contract is unchanged) and does not retune any
threshold to produce more or fewer candidates. It fixes a state-semantics defect discovered after
D1's real-universe run, acquires the local SEC data that defect had been masking the need for, and
reruns the frozen D1 pipeline once against that expanded data. No forward return, factor ranking,
GPT research, valuation, or decision logic is touched anywhere in this stage.

## A. Problem Discovered

D1's real-universe run (`H_V2_D1_UNIVERSE_CHANGE_ENGINE_V1.md` §8) found 5,069 of 5,192 securities
`INELIGIBLE`, with `INSUFFICIENT_FUNDAMENTALS` present on 5,030 of them. D1's own §11.1 already
flagged the cause: local SEC companyfacts coverage was 158 CIKs out of 5,088 with a CIK, because D1
deliberately did no new SEC ingestion. The defect is that D1's eligibility model had only two
outcomes for "not enough data" - `INELIGIBLE` or, in one narrow case, `UNKNOWN` - so a security we
had simply never fetched came back indistinguishable from a security we had *proven* to be a penny
stock or a distressed filer. `MISSING LOCAL DATA != COMPANY INELIGIBLE`, and D1's schema could not
say so.

## B. State Semantic Fix

`eligibility.py` now has four statuses, not three:

```text
CandidateStatus:
  ELIGIBLE        - current available data clears every E1 check
  INELIGIBLE      - a proven fact about the security/market rules it out
  DATA_NOT_READY  - a primary input (identity, price history, or fundamentals) has not been
                    fetched or does not yet cover enough history - a fact about our cache, not
                    about the company
  UNKNOWN         - data exists but its meaning/identity cannot be stably determined
```

Reasons are now evaluated in four priority tiers (`evaluate_eligibility`'s docstring carries the
same text): `hard` facts win outright (`INELIGIBLE`); `unknown_ambiguous` outranks a plain data gap
(`UNKNOWN`); `data_not_ready` a cache gap that outranks nothing except those two (`DATA_NOT_READY`);
`soft` caveats never block an otherwise-clear candidate (`ELIGIBLE`, with the caveat recorded).

Mapping from D1's original hard/blocking-unknown/soft-unknown model:

| D1.1 status | Reasons that produce it |
|---|---|
| `INELIGIBLE` | `NOT_COMMON_STOCK`, `UNSUPPORTED_EXCHANGE`, `MISSING_CIK`, `EXTREME_LOW_PRICE`, confirmed `LOW_LIQUIDITY`, confirmed `DISTRESS_FLAG` (negative equity resolved OK), confirmed `EXTREME_DILUTION_RISK` (dilution resolved OK) |
| `DATA_NOT_READY` | `REFERENCE_DATA_NOT_READY` (missing FIGI / unresolved security type), `REQUIRED_MARKET_DATA_NOT_AVAILABLE` (no/short local price history), `FUNDAMENTALS_NOT_FETCHED` (no local companyfacts at all), `INSUFFICIENT_REPORTING_HISTORY` (fetched, but company too young - recent IPO), `INSUFFICIENT_COMPARABLE_PERIODS` (fetched, still too few resolved fields) |
| `UNKNOWN` | `SECURITY_TYPE_AMBIGUOUS`, `CIK_CONFLICT`, `CORPORATE_ACTION_UNRESOLVED`, `FUNDAMENTAL_FACT_AMBIGUOUS` - all defined, **none currently triggered** (§H) |
| `ELIGIBLE` (with caveat) | `DISTRESS_UNRESOLVED` / `DILUTION_UNRESOLVED` when the secondary check itself lacks data |

The IPO-vs-not-fetched distinction (D1.1 brief §14) is `FundamentalsCoverage.facts_fetched`
(explicitly passed by the caller, never inferred from an empty facts list) plus
`earliest_fact_age_days` (age of the oldest known fact): `facts_fetched=False` is always
`FUNDAMENTALS_NOT_FETCHED`; `facts_fetched=True` with the oldest fact younger than
`MIN_REPORTING_HISTORY_DAYS` (730 days) is `INSUFFICIENT_REPORTING_HISTORY`; otherwise it is
`INSUFFICIENT_COMPARABLE_PERIODS`. None of the three implies a company-quality judgment.

## D. Target Universe / CIK

Target is D1's own dated CS/XNYS/XNAS/XASE snapshot (`CS_2024-10-25.json.gz`), not the full SEC
filer population - **5,192 rows, 5,089 unique CIKs** (6 rows have no CIK and are excluded from any
CIK-keyed fetch; `eligibility.py` already reports `MISSING_CIK` for them independently).
`acquisition.build_acquisition_queue` collapses duplicate CIKs (79 CIKs have more than one ticker,
e.g. GOOG/GOOGL) into one queue item each, deterministically ordered by CIK, so a multi-class issuer
is fetched once.

## E. Existing SEC Cache (audited before any fetch)

| | Submissions | Companyfacts |
|---|---:|---:|
| Already cached (across H0, PV2C, H0.5, and the shared C-E0 store) | 2,253 / 5,089 | 159 / 5,089 |
| Needed | 2,836 | 4,930 |

Existing valid caches were never re-downloaded - `acquisition.py` only queues a source that is
missing, and the reused fetch primitives (`sec_store._page`, `xbrl_store.fetch_cik`) independently
short-circuit on an existing file plus its ledger before this script even runs (verified offline,
`test_sec_reuse_contract.py::test_cached_companyfacts_is_never_refetched`).

## F. Acquisition Method

Compared, per the brief's own criteria, before choosing:

| | REST (existing client) | Bulk ZIP (`companyfacts.zip`-style) |
|---|---|---|
| Reliability | Already audited and live-tested in this exact repository (H0, PV2C) | Unvalidated in this repository; would need new, unaudited ZIP-parsing code |
| API policy | Already SEC-compliant: identified User-Agent, 5 req/s (half the stated 10/s ceiling), bounded retry/backoff, typed 404 handling | Also SEC-sanctioned, but a new code path with its own policy surface to get right |
| Download size | ~10.9GB for exactly the ~4,930 CIKs actually needed (measured, §G) | A full bulk file contains every US filer (H0's own crude estimate: ~2.6GB-plus for a 10,000-CIK companyfacts snapshot, likely more current), most of it outside this target universe |
| Implementation complexity | Zero new HTTP logic; only new code is queue-building (`acquisition.py`) | New ZIP-member extraction, integrity checking, and a mapping from bulk records back to canonical facts |
| Cacheability | Identical raw-bytes-plus-ledger convention as every other H0/C-E0 store, so it composes with existing readers unmodified | Would need its own cache/versioning convention |

**Decision: reuse the existing REST client unmodified.** The brief's own instruction ("이미
repository에 지원 코드가 있으면 우선 재사용") and the fact that the target is a bounded ~4,930-CIK
list rather than the full filer population both favor it; bulk ZIP remains a reasonable option for a
future full-market historical build, not for this bounded, already-solved-shape acquisition.

## G. SEC Fetch Results

Run `D1_1-20260928T044641Z`, user agent `USB Research tjd6189@gmail.com` (the same identifier
already used by H0/C-E0 in this repository), 5 req/s (unchanged from `sec_store.REQUESTS_PER_SECOND`),
submissions required back to 2022-01-01 (~2 years of comparable quarters is what E2 actually needs).

```text
target unique CIKs:        5,089
already fully cached:        159
requested:                 4,930
fetched:                   4,930   (100%)
partial:                       0
failed:                        0
HTTP requests:              7,867  (200: 7,843 - 404: 24, a fact about those filers, not an error)
retries:                        0
download bytes:     10,872,674,386 (~10.9 GB)
wall time:              ~54 minutes
```

Zero failures and zero retries against a real, external, rate-limited government API - no aggressive
retry was used, no 429/403 was seen. New raw data lives under
`data/runtime/strategy_h_v2/d1_1/sec_raw/` (gitignored), a fresh store; H0's and PV2C's original
caches were opened read-only and never modified.

## H. Canonical Normalization

Reused H0's unmodified `strategy_h0.facts.extract_companyfacts`/`resolve_fact`/`canonical_coverage`
- the same 12-field canonical mapping D1 already used. No canonical tag mapping changed in D1.1.

**A real normalization-scale defect was found and fixed in D1.1's own new code, not in H0's
primitives.** D1.1 added a `FUNDAMENTAL_FACT_AMBIGUOUS` -> `UNKNOWN` check (>=3 of 12 fields
resolving to `FactStatus.AMBIGUOUS`). Run once against the full acquired universe, it fired on 2,143
of 5,192 securities (41%) - far more than the rare case D1 had observed. Sampling 200 ambiguous
instances directly showed the cause: **every one was a single 10-Q filing legitimately reporting the
same line item under two different durations for the same period-end** (a discrete quarter, e.g.
2026-04-01..2026-06-30, and the year-to-date figure, e.g. 2026-01-01..2026-06-30) - normal, correct
XBRL practice, not conflicting data. `facts.py::resolve_fact`'s snapshot-level narrowing (by tag,
acceptance, and accession) does not also narrow by duration family, so it correctly reports these as
"AMBIGUOUS" in the sense the function actually promises (conflicting values sharing the same winning
provenance under its own narrowing rules) while that is not the same as a genuine data-identity
conflict. **The check was removed before being wired into the final pipeline run** (§B's UNKNOWN row
lists it as defined but inactive); `ambiguous_field_count` is retained as a diagnostic field in the
evidence stub's `data_quality`, never as a status gate. This is an honest example of exactly the
mistake this whole D1.1 stage exists to prevent - a plausible-looking check that fires on missing
context rather than a real signal - caught by testing at full scale before being finalized, not
after.

## I. Fundamental Coverage

| | D1 (before) | D1.1 (after) |
|---|---:|---:|
| Unique CIKs with local companyfacts fetched | 159 | 5,065 |
| Unique CIKs with >=1 resolved canonical field | not separately measured | 4,416 |
| Universe rows with fetched companyfacts | 162 | 5,160 |

## J. E1 Re-evaluation

Before/after, same frozen E1 logic, only the input data changed:

| Metric | D1 (159 CIKs) | D1.1 (5,065 CIKs) |
|---|---:|---:|
| Universe | 5,192 | 5,192 |
| ELIGIBLE | 123 | **2,716** |
| INELIGIBLE | 5,069 | **1,952** |
| DATA_NOT_READY | n/a (status did not exist) | **524** |
| UNKNOWN | 0 | 0 |

`INELIGIBLE` reasons that depend only on market data (not fundamentals) are, correctly,
**unchanged between the two runs** - a strong internal-consistency check that the pipeline logic
itself did not drift, only the fundamentals input did:

| Reason | D1 | D1.1 |
|---|---:|---:|
| `LOW_LIQUIDITY` | 1,383 | 1,383 |
| `EXTREME_LOW_PRICE` | 470 | 470 |
| `MISSING_CIK` | 6 | 6 |
| `UNSUPPORTED_EXCHANGE` | 1 | 1 |

The reasons that *do* change are exactly the ones that depend on fundamentals, and they now mean
what their names say:

| Reason | D1 (masked by missing data) | D1.1 (real, confirmed facts) |
|---|---:|---:|
| `DISTRESS_FLAG` (confirmed negative equity) | 5,041 (mostly "equity unresolved," not confirmed) | 564 |
| `EXTREME_DILUTION_RISK` (confirmed) | 5,044 (mostly unresolved) | 355 |
| `INSUFFICIENT_FUNDAMENTALS` (was hard-ineligible) | 5,030 | retired - see below |

## K. Decomposition of the Original 5,030 `INSUFFICIENT_FUNDAMENTALS`

This is the central question D1.1 exists to answer. Of the 5,030 securities D1 marked `INELIGIBLE`
for `INSUFFICIENT_FUNDAMENTALS`, after acquiring their local SEC data:

```text
became genuinely ELIGIBLE (fundamentals resolved fine once fetched)      ~2,590
became genuinely INELIGIBLE for a real, now-confirmed reason
  (distress, dilution, or a market fact already known in D1)             ~1,940
remain DATA_NOT_READY - fetched, but still structurally insufficient
  (recent IPO, thin/absent XBRL, or delisted/inactive filer)                524
```

(The three figures are derived from the eligibility-count deltas in §J and do not sum to exactly
5,030 because a small number of securities also carry an independent market-based `INELIGIBLE`
reason that was already present in D1 regardless of fundamentals coverage.) The overwhelming
majority of the original 5,030 were, as suspected, companies our local cache had simply never
reached - not companies proven unfit for research.

## L. E2 Coverage

E2 now runs for all 2,716 `ELIGIBLE` candidates (up from 123). Change-state totals across every
metric moved from D1's small-sample counts (tens of observations per state) to hundreds - e.g.
`operating_income` `UNKNOWN` (too few periods to judge) is now 1,266 of 2,716 eligible candidates,
which is itself useful, honest information about how much of the *eligible* universe still lacks a
clean multi-period trend, distinct from the `DATA_NOT_READY` population that never reached E2 at
all. Full per-metric distributions are in the manifest
(`data/runtime/strategy_h_v2/d1/D1-20260928T054937Z/manifest.json`).

## M. E3 Results

| Priority | D1 (123 eligible) | D1.1 (2,716 eligible) |
|---|---:|---:|
| P1_HIGH | 11 | 200 |
| P2_MEDIUM | 81 | 1,810 |
| P3_LOW | 31 | 691 |
| HOLD | 0 | 15 |
| **Candidate stubs written (P1+P2)** | **92** | **2,010** |

No threshold in `research_priority.py` changed between the two runs; the entire difference is the
larger, now-correctly-classified `ELIGIBLE` population E3 had to rank. `HOLD` (completeness < 34%)
appearing 15 times in D1.1 and never in D1 is itself informative: it means 15 `ELIGIBLE` candidates
have thin-but-passable fundamentals coverage, a case D1's tiny 123-name sample never happened to
produce.

## N. Before / After Summary

```text
Metric                     D1 Before        D1.1 After

Universe                       5,192             5,192
Eligible                         123             2,716
Ineligible                     5,069             1,952
Data Not Ready               (n/a)                 524
Unknown                            0                 0
E2 analyzable                    123             2,716
P1                                 11               200
P2                                 81             1,810
P3                                 31               691
HOLD                                0                15
Candidate stubs                    92             2,010
```

## O. Artifact / Cache Layout

```text
data/runtime/strategy_h_v2/d1_1/
  sec_raw/submissions/CIK{cik}/CIK{cik}.json.gz(.request.json)   (new, D1.1's own store)
  sec_raw/companyfacts/CIK{cik}.json.gz(.request.json)           (new, D1.1's own store)
  D1_1-{run_id}.manifest.json / .sha256                           (acquisition run record)

data/runtime/strategy_h_v2/d1/{run_id}/                           (unchanged D1 artifact shape)
  manifest.json / .sha256
  candidates/{ticker}.json
```

H0's (`data/runtime/strategy_h/h0/raw`) and PV2C's (`.../h_pv2c/sec_raw`) original stores are
untouched, read-only inputs. `data/runtime/` is gitignored repository-wide.

## P. Tests

74 tests in `backend/tests/strategy_h_v2/` (up from 58 after D1): `test_eligibility.py` now covers
all four statuses and their priority ordering, including the core regression
(`test_missing_local_fundamental_is_not_ineligible`) and the ambiguity-detector-removal regression
(`test_ambiguous_field_count_is_diagnostic_only_and_never_changes_status`);
`test_acquisition.py` (queue determinism, duplicate-CIK collapse, cached-CIK skip);
`test_sec_reuse_contract.py` (offline proof that a cached CIK is never re-requested and a missing
one is reported honestly, not fabricated); `test_pipeline.py` extended for the
`facts_fetched`/`DATA_NOT_READY` wiring and rerun idempotence. Every test in this suite runs offline
(no network); the one live-network step is the acquisition script itself, run once, manually,
outside pytest. Full suite alongside the existing Strategy H0/PV regression: **144 passed, 0
failed**.

## Q. Remaining Limitations

1. **524 `DATA_NOT_READY` candidates are a real residual, not a bug.** `INSUFFICIENT_COMPARABLE_PERIODS`
   (366) and `REFERENCE_DATA_NOT_READY` (338, overlapping) mean either the filer's history is
   genuinely too thin even after fetching, or the reference snapshot itself lacks an identifier for
   that row - neither is fixable by fetching more of the same source again.
2. **The local daily OHLCV window is still ~2 years** (unchanged from H0.6's finding); D1.1 did not
   touch market data acquisition, only SEC fundamentals.
3. **`UNKNOWN` has no active detector** after removing `FUNDAMENTAL_FACT_AMBIGUOUS` (§H). The
   reason codes remain defined for `SECURITY_TYPE_AMBIGUOUS`/`CIK_CONFLICT`/
   `CORPORATE_ACTION_UNRESOLVED`, but none is currently backed by a validated check. A future stage
   should either build one carefully (with the same full-universe validation discipline used here)
   or accept that `UNKNOWN` stays dormant.
4. **Submissions were only required back to 2022-01-01.** E2's 4-period trend needs roughly that
   much; a future stage wanting a longer trend window would need to re-run acquisition with an
   earlier `SUBMISSIONS_REQUIRED_FROM`, which the existing cache-aware queue would handle
   incrementally (only the newly-required older pages would be fetched).
5. **`DISTRESS_UNRESOLVED`/`DILUTION_UNRESOLVED` still appear on 309 + 20 otherwise-`ELIGIBLE`
   candidates.** These are honest caveats (shares-outstanding or equity history too short to judge),
   not defects; a future acquisition pass targeting specifically these CIKs' older shares history
   could close some of them.

## R. D2 Readiness

`D2 Evidence Collector` formalization (per the D0 roadmap, D0 §Z) can now proceed against a universe
where `ELIGIBLE` means something real: 2,716 candidates with actual resolved fundamentals, not a
123-name sample that was 97% an artifact of missing local data. D2's job - turning the pipeline's
already-assembled `CandidateEvidenceStub` into the full D0 Evidence Bundle contract - does not
depend on resolving §Q's remaining limitations first; those are legitimate, separately-trackable
data-coverage backlog items, not blockers to formalizing the evidence contract itself.

## Verdict

```text
H-V2-D1.1 = PASS
```

Missing local cache is no longer treated as company ineligibility (§B, §K). The target universe now
has broad, real fundamental coverage (5,065 of 5,089 unique CIKs, up from 159) sufficient for E1/E2
evaluation at the scale the D0 architecture was designed for. Acquisition was reproducible,
cache-aware, and fully logged (§G) with zero failures. E1/E2/E3 reran under the exact frozen D1
thresholds with no retuning (§J-§M). No forward return, price target, or performance figure was read
or computed anywhere in this stage. A real defect in D1.1's own new ambiguity check was found and
removed through the same full-scale validation discipline, rather than shipped uncaught (§H).

```text
D2 Evidence Collector = READY
```

## S. Next Action (max 7)

1. User reviews this document and the before/after numbers before D2 begins.
2. If approved, begin D2 (Evidence Collector) formalizing the full D0 Evidence Bundle contract on
   top of the now-broad `ELIGIBLE` population.
3. Track the 524 `DATA_NOT_READY` and ~330 soft-caveat candidates as a data-coverage backlog, not as
   a pipeline defect - revisit only if a future stage specifically needs them.
4. If a longer E2 trend window is ever wanted, rerun acquisition with an earlier
   `SUBMISSIONS_REQUIRED_FROM`; the cache-aware queue will fetch only the incremental older pages.
5. Leave `UNKNOWN`'s reason codes defined but dormant until a specific, full-scale-validated
   detector is proposed for one of them.
6. Do not resume PV-track factor ranking or any Value/Growth/Quality composite on this expanded
   universe - that role remains retired per `H_V2_D0` §X.
7. Do not begin GPT research, valuation, or paper trading until D2 and the later D-stages in the
   roadmap are complete and reviewed.

## Final Declarations

```text
future return used?                     NO
selection threshold retuned?            NO
Value/Growth/Quality alpha ranking used? NO
GPT Research executed?                  NO
Expectation Gap evaluated?              NO
investment decision generated?          NO
paid data used?                         NO
existing dirty files modified?          NO
push?                                   NO
```
