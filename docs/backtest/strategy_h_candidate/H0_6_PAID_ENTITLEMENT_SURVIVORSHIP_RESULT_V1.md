# Strategy H — H0.6 Paid Entitlement + Survivorship Coverage Audit Result V1

- audit date: 2026-09-27
- frozen contract: `e7d1b62`
- H0.6: **FAIL**
- H0 data/PIT feasibility: **INCONCLUSIVE**
- H1: **NOT AUTHORIZED**
- returns/alpha: **NOT COMPUTED**

## A. Repository State

Audit began on `main` at `96c034add6a6bd56939e2a738d4ee7a298c604e1`, ahead 5 and behind 0 relative to `origin/main`. The pre-existing modified/untracked worktree was preserved and excluded from both H0.6 commits. No push occurred.

## B. H0/H0.5 Authoritative State

H0 is `INCONCLUSIVE` (`a5c683c` freeze, `f3ac1fe` result). H0.5 is `INCONCLUSIVE` (`20328a6` freeze, `96c034a` result). H1 remains `NOT AUTHORIZED`. H0.6 does not amend either prior contract.

## C. Developer Entitlement Verification

The active credential was tested after freeze with one AAPL `adjusted=false` daily request on a fixed XNYS session in each year 2017–2026. Results were 2/10 successful:

| Year | Session | Result |
|---:|---|---|
| 2017 | 2017-06-30 | `PLAN_TIMEFRAME_NOT_INCLUDED` |
| 2018 | 2018-06-29 | `PLAN_TIMEFRAME_NOT_INCLUDED` |
| 2019 | 2019-06-28 | `PLAN_TIMEFRAME_NOT_INCLUDED` |
| 2020 | 2020-06-30 | `PLAN_TIMEFRAME_NOT_INCLUDED` |
| 2021 | 2021-06-30 | `PLAN_TIMEFRAME_NOT_INCLUDED` |
| 2022 | 2022-06-30 | `PLAN_TIMEFRAME_NOT_INCLUDED` |
| 2023 | 2023-06-30 | `PLAN_TIMEFRAME_NOT_INCLUDED` |
| 2024 | 2024-06-28 | `PLAN_TIMEFRAME_NOT_INCLUDED` |
| 2025 | 2025-06-30 | OK, close 205.17 |
| 2026 | 2026-06-30 | OK, close 289.36 |

This is the response pattern of the existing two-year entitlement, not a verified Developer entitlement. Pricing text says Developer includes ten years, but it is not evidence that this credential has it: [Massive pricing](https://massive.com/pricing).

## D. Historical Universe Enumeration

Not executed. The frozen contract requires nine complete dated CS snapshots, but continuing into bounded full-reference enumeration after the prerequisite credential failed would not test the claimed paid configuration. No current ticker list was substituted and no partial snapshot was represented as a historical universe.

## E. Active / Inactive / Delisted Coverage

No new 40-security results were generated. H0.5 evidence remains informative but not an H0.6 pass: FB/META identity continuity and historical-before/post-event behavior for TWTR and BBBY were previously demonstrated. H0.6's requirements for nine-date enumeration and at least two later-inactive members were not measured under Developer access.

## F. Security Identity

The frozen identity rule remains share-class FIGI, then composite FIGI, otherwise `UNKNOWN`; CIK is the company identifier and ticker is effective-dated. No new identity results were used. FB/META's prior shared FIGI remains prior evidence only.

## G. 10Y Daily Coverage

`FAIL` for the credential under test: eight required historical requests were rejected. No 40-security daily download followed. This does not prove Massive's advertised Developer product lacks ten-year data; it proves that Developer access was not active on the supplied credential.

## H. Corporate Actions

Not re-audited because the entitlement prerequisite failed. H0.5's isolated AAPL split success cannot satisfy the frozen sample-wide split/dividend/ticker-event condition. The gate remains unmeasured.

## I. PIT Shares Coverage

Not measured across the preregistered 40-security sample. The existing one-issuer AAPL result remains valid prior evidence but cannot satisfy the newly frozen >=70% single-class issuer-date threshold. Future/current/weighted-average fallback remained prohibited.

## J. Multi-class Audit

GOOG/GOOGL and BRK.B were preregistered but not fetched. No class allocation was inferred. Their status for this audit is `MULTI_CLASS_UNRESOLVED`.

## K. Historical Market Cap Reconstruction

No new market caps were calculated. The prerequisites for the frozen formula—Developer historical unadjusted close and valid PIT raw shares—were not jointly available for the sample. Current close, current shares, and current market cap were not used as fallback.

## L. Market Cap Lane Reconstruction

Not executed. Every H0.6 sample/date lane is unmeasured rather than backfilled. No return or performance comparison was calculated.

## M. Survivorship Safety Assessment

The fail-closed behavior worked: absence of paid history stopped the audit before a present-day list, current shares, or partial prices could leak backward. Survivorship-safe feasibility remains unproven, not disproven as a general provider capability.

## N. Tests

`77 passed, 4 warnings` across Strategy H0, Strategy C-E0, and Strategy EQM-V0. New tests cover entitlement failure, missing downstream measurements, exact frozen thresholds, inactive-member minimum, identity/actions failure, and deterministic UNKNOWN policy. Existing tests retain inactive historical inclusion, post-delisting exclusion, ticker mutation, unadjusted market-cap inputs, future/stale shares rejection, filing availability, split consistency, missing inputs, multi-class rejection, current fallback rejection, and lane boundaries.

## O. Storage / Bulk Acquisition Estimate

The provider's Stocks Day Aggregates flat-file listing totals approximately 265 MB compressed for 2022–2026 and 475 MB for 2017–2026 based on the published per-year sizes. Reference/actions, ledgers, normalization, and indexes are additional and remain unmeasured. Existing SEC evidence estimates roughly 2.6 GB compressed for a cumulative 10,000-CIK companyfacts snapshot; PIT/normalized tables remain `UNKNOWN` until schemas and sample coverage are proven.

For a future approved full backfill, daily Flat Files are preferable to thousands of REST calls: they are compressed daily cross-sectional CSVs, unadjusted, and fit the project's provider-byte + ledger + checksum convention under a versioned `raw/massive/day_aggs/` tree. REST remains preferable for bounded reference/action verification. No flat file was downloaded here. [Stocks Day Aggregates](https://massive.com/docs/flat-files/stocks/day-aggregates), [Flat Files quickstart](https://massive.com/docs/flat-files/quickstart), [Stocks Flat Files overview](https://massive.com/docs/flat-files/stocks/overview).

## P. H0.6 Freeze Commit

`e7d1b62` — `research(strategy-h): freeze H0.6 entitlement audit contract`

Canonical checksum: `9467110af5655149f44096856ad497c96d2d567d189f18711edfa60016b95ace`.

## Q. Implementation / Result Commit

Recorded after this document and regression verification; the final handoff supplies the full SHA.

## R. Blockers

1. Supplied Massive credential does not have verified Developer ten-year access.
2. Nine dated reference snapshots were therefore not enumerated under the target entitlement.
3. Deterministic 40-security sample was not materialized.
4. Daily completeness, actions, shares, and market-cap coverage thresholds remain unmeasured.
5. Multi-class allocation remains unresolved.
6. Historical panel storage beyond published flat-file sizes is not empirically measured.

## S. Verdict

```text
H0.6 = FAIL
H0 DATA/PIT FEASIBILITY = INCONCLUSIVE
```

H0.6 fails its declared paid-entitlement audit because only 2/10 annual probes succeeded. H0 remains inconclusive: the failed credential does not establish that a genuinely active Developer entitlement or another source cannot supply the missing data.

## T. H1 Authorization

```text
NOT AUTHORIZED
```

## U. Next Action

1. Confirm billing/account and key association without sharing the secret.
2. Activate or replace the credential with actual Stocks Developer access.
3. Re-run only the frozen ten-request entitlement probe first.
4. Continue the existing frozen H0.6 contract only if all 10 probes succeed; otherwise stop again.
5. Then enumerate nine dated snapshots and materialize the frozen 40-security sample.
6. Measure the frozen daily/shares/cap thresholds without changing them.
7. Write a separate bulk-ingestion contract only after H0.6 passes.

## Final accounting

- changed existing files: none
- new tracked implementation/result files: `backend/app/backtest/strategy_h0/h0_6.py`, `backend/tests/strategy_h0/test_h0_6.py`, this result document
- freeze files: contract Markdown, canonical JSON, SHA-256 sidecar
- pre-existing dirty files: preserved, not staged
- tests: 77 passed, 4 third-party deprecation warnings
- push: NO
- Massive API calls: 10
- provider response bytes: 2,035
- other downloads: 0
- Developer entitlement actually verified: **NO**
