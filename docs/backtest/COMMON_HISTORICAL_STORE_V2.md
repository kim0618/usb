# US-B Historical Data Requirements + USB-HIST-V2

Status (2026-09-18): requirements FIXED, collection RUNNING for the kinds that pass the capacity
gate, **B minute BLOCKED by Drive capacity**, `USB-HIST-V2` **not frozen** (the freeze refuses while a
required kind is MISSING). `USB-HIST-V1` is unchanged. Uncommitted code:

* `backend/app/backtest/historical_store/requirements.py`: `STRATEGY_REQUIREMENTS_V2` (matrix, ranges, digest)
* `backend/app/backtest/historical_store/b_universe.py`: `B_FETCH_UNIVERSE_Q1` (PIT scope, fetch ranges)
* `backend/app/backtest/historical_store/reference_fetch.py`: daily CS reference snapshots (provider bytes)
* `raw_fetch.plan_requests(required=, chunk=)`, `raw_fetch.minute_page_cap`, `snapshot.build(extra_*)`
* CLIs `app.dev.historical_v2 plan|fetch`, `app.dev.build_historical_snapshot_v2 [--dry-run]`
* tests `backend/tests/test_historical_v2.py`

## 1. Requirements (from code and frozen rules, not assumed)

Grid = 501 XNYS sessions 2024-09-17..2026-09-16, index 0 = 2024-09-17.

| Data | A | B | C | D | Common raw | Authority |
|---|---|---|---|---|---|---|
| grouped daily | NOT_REQUIRED (universe rebuild only) | REQUIRED (scope, spec 3.2) | REQUIRED | REQUIRED | yes | MASSIVE_GROUPED_DAILY |
| per-symbol daily | REQUIRED (scanner, 29 + SPY) | REQUIRED (scope in code, D/D-1 audit) | NOT_REQUIRED | NOT_REQUIRED | yes | MASSIVE_TICKER_AGGREGATE |
| minute PM | REQUIRED | REQUIRED (`return_scope=EXTENDED_DAY`) | NOT_REQUIRED | NOT_REQUIRED | yes | ticker aggregate |
| minute REG | REQUIRED (STRICT) | REQUIRED (HYBRID-S) | NOT_REQUIRED | NOT_REQUIRED | yes | ticker aggregate |
| minute AFTER | NOT_REQUIRED | OPTIONAL | NOT_REQUIRED | NOT_REQUIRED | yes (same request) | ticker aggregate |
| CS reference | NOT_REQUIRED | REQUIRED (as of D-1; daily cadence = spec) | REQUIRED (quarterly) | REQUIRED (quarterly) | yes | reference tickers |
| ticker details | REQUIRED (market cap, static as_of 2025-09-12) | OPTIONAL (list_date flag) | NOT_REQUIRED | NOT_REQUIRED | no | ticker details |
| splits | NOT_REQUIRED (none applied) | REQUIRED | REQUIRED | REQUIRED | yes | splits |
| ticker events | NOT_REQUIRED | OPTIONAL (flag) | NOT_REQUIRED | NOT_REQUIRED | no | UNKNOWN |
| delisting | NOT_REQUIRED | OPTIONAL (flag) | NOT_REQUIRED (grouped disappearance) | NOT_REQUIRED | no | reference |
| benchmark | REQUIRED (SPY daily) | NOT_REQUIRED | REQUIRED (SPY row of grouped) | NOT_REQUIRED (universe median) | yes | as noted |
| SEC filings | NOT_REQUIRED | NOT_REQUIRED | PLANNED (C-E only) | NOT_REQUIRED | no (not Massive) | SEC EDGAR |
| quotes / spread | NOT_REQUIRED | UNAVAILABLE | NOT_REQUIRED | NOT_REQUIRED | no | none on Basic |
| halt feed | NOT_REQUIRED | UNAVAILABLE (inferred from gaps) | NOT_REQUIRED | NOT_REQUIRED | no | none on Basic |

Non-obvious facts behind the table:

* **A** builds its premarket-gate daily bars from REGULAR minutes (`replay/provider.py`), not from the
  daily Parquet; only the scanner reads per-symbol daily. A needs the **settlement session after the last
  entry** (minute, `SETTLEMENT_SESSIONS=1`), so eval end 2026-09-16 needs minute 2026-09-17. A applies no
  corporate action. A reads its minutes through the legacy manifest only; the 2024-09..2025-08 minutes of
  its 29 symbols sit in Common Raw (V1) and need the planned A adapter.
* **B**: only the scanner is implemented (`RESEARCH_SCANNER_ONLY`); setups, entry, exit and risk are PLANNED.
  RVOL reads the 20 sessions before D **whether or not the symbol was in scope**, and the loader reads a
  covered session without bars as zero volume, so each symbol's minute range must be contiguous.
* **C** reads `T,o,h,l,c,v` of grouped daily only (`vw`, `n` unused). Primary eval ends at N-11 (code is
  exclusive at N-10); the rules file pins `range_end` 2026-09-16 (checksum `c769aea5`).
* **D** (rules only) pins its grid to the FREEZE_V1 501 sessions; extending the grid needs a new rules version.
* The reference endpoint is **not** limited to the rolling window (a 2023 date answered 200 on 2026-09-18).
  Aggregates are: a range that starts before the window is **truncated silently** (no 403), so the
  fetcher clips every request to the window just before sending it.

## 2. Data range vs evaluation range

| | Raw data | Warmup start | Eval | Forward end | Limitation |
|---|---|---|---|---|---|
| A | 2024-09-17..2026-09-17 (settlement) | 2024-09-17 | 2024-10-23..2026-09-16 (idx 26..500; 2026-09-15 without the settlement minute) | 2026-09-17 | 20 minute + 25 daily sessions before 2024-09-17 UNAVAILABLE |
| B | 2024-09-17..2026-09-16 | 2024-09-17 | 2024-10-02..2026-09-16 (PARTIAL RVOL); FULL from 2024-10-15 | 2026-09-16 | first scope day follows the first CS snapshot (2024-10-01) under Q1 |
| C | 2024-09-17..2026-09-16 | 2024-09-17 | primary 2025-09-18..2026-09-01 (idx 251..490); secondary from 2024-12-11 | 2026-09-16 | none |
| D | 2024-09-17..2026-09-16 | 2024-09-17 | 2025-10-01..2026-08-18 (idx 260..480) | 2026-09-16 | none |

## 3. B PIT fetch universe (`B_FETCH_UNIVERSE_Q1`, digest `33c194f3…`)

S(D) = CS in the latest quarterly snapshot dated < D, exchange XNAS/XNYS/XASE, not a test symbol, grouped
close(D-1) >= 1, median grouped close x volume over the 20 sessions before D >= 1M with >= 5 present.
Fetch range per symbol = [first S(D) - 20, last S(D)] clipped to the grid (a superset of B's reads;
B filters to S(D) at read time, spec 3.5).

* 4,953 symbols, 490 scope sessions from 2024-10-02, per day min 3,036 / median 3,308.5 / max 3,488.
* 1,605,793 in-scope symbol-days; 1,816,236 symbol-sessions in the contiguous ranges; 2,655 symbols
  need the whole grid; 1,281 symbols leave and re-enter scope (the range covers the gap).
* Quarterly cadence misses about 0.86% of symbol-days after 2024-10-01 (707 symbols, 169 never in Q1
  scope, mostly IPOs between snapshots; estimated with the next snapshot as a proxy). They sit mid-window,
  so they can be added later from the daily reference (`B_FETCH_UNIVERSE_D1`) without loss.

## 4. Coverage and missing (plan of 2026-09-18)

| Kind | Required | Existing | Missing | Requests / calls | Size | Gate |
|---|---|---|---|---|---|---|
| grouped daily | 501 | 501 | 0 | - | - | - |
| CS quarterly / splits | 8 / 1 | 8 / 1 | 0 | - | - | - |
| A minute + daily (30 x 501) | 15,030 | 15,030 | 0 | - | - | - |
| B per-symbol daily | 1,816,236 | 34,008 | 1,782,228 | 4,995 / 4,995 | ~73 MB | PASS |
| B minute | 1,816,236 | 14,433 | 1,801,803 | 4,925 / ~13,015 | ~10.3 GB | Drive **BLOCKED** -> LOCAL_STAGING PASS (877 GB free) |
| A settlement minute 2026-09-17 | 29 | 0 | 29 | 29 / 29 | ~0.6 MB | PASS (T-1 from ET 2026-09-18) |
| B daily reference (as of D-1) | 501 | 8 | 493 | 493 / ~2,958 | ~220 MB | PASS |

B minute estimate basis: 20 B-scope symbols x 21 sessions measured (2026-08-03..31): rows per session by
grouped trade count (64 below 1k trades .. 936 above 128k), 15-26 gzip bytes per row (median 20).
517M rows; zstd-19 would save only ~24% (measured on V1 pages), not enough to pass.

Capacity gate: Drive free 9.07 GB (15 GB account, shared with Gmail/Photos). B minute needs 10.3 x 1.5 =
15.5 GB. Everything else together needs ~0.3 GB x 1.5.

## 5. Collection (emergency preservation, 2026-09-18 13:10 KST)

The binding resource is the Basic rolling window, not Drive space: every ET date drops the oldest
session of minute and per-symbol daily. So B minute moved to local staging and runs first.

`app.dev.historical_v2 fetch` (collector `historical_v2/2026-09-18.b3`):

| Priority | Kind | Store | Order |
|---|---|---|---|
| P0 | B minute | LOCAL_STAGING `data/runtime/common_hist/v2_staging/market_data/raw/massive/minute/<SYM>/` | request start, first scope session, symbol |
| P1 | B per-symbol daily | Drive Common Raw | oldest start first |
| P2 | A settlement minute 2026-09-17 (29) | Drive Common Raw | skipped at dequeue while not T-1 |
| P3 | B daily reference (D-1) | Drive | by date (not windowed) |

- Staging uses the Common Raw layout and ledger contract (provider bytes, gzip mtime 0, sha256 of file and
  body, ledger last). Capacity gate: local free >= 2 x estimate; the Drive 1.5 x gate applies to Drive kinds only.
- Every aggregate request is clipped to `window_start()` just before it is sent (and again on each retry).
  The ledger keeps `requested_start/end`, `clipped_request_start/end`, `window_start_at_request`,
  `unavailable_rolling_window`, `coverage_status` (COMPLETE / COMPLETE_CLIPPED), `first_session`/`last_session`
  from the actual bars, `pagination_pages`, `file_bytes`, `endpoint`, `collector_version`, `storage_tier`.
- Lost sessions are merged into `data/runtime/common_hist/v2/unavailable_rolling_window.json` (only grows).
  2024-09-17 left the window at ET 2026-09-18 00:00 - 30 min, before any B minute request.
- Fetch refuses overlapping pages and bars outside the request (no ledger, so nothing counts as covered).
- NETWORK_ERROR / PROVIDER_TIMEOUT / PROVIDER_ERROR / RATE_LIMITED retry the same item after 60/120/300/600/900 s;
  NOT_AUTHORIZED and SECRET_IN_BODY stop the run. 13 s spacing unchanged.
- One collector at a time (`collector.lock`, flock). The Drive writer lock is taken only for batches that
  write Drive (B minute batches do not lock out backtests).
- Progress: `fetch_progress.json` (per kind: planned/done/failed/skipped/rows/bytes/http/unavailable),
  `minute_coverage.json` (request-count ETA), `fetch.log` (earlier runs: `fetch.part1.log`, `fetch.part2.log`).
- Validation (read-only): `app.dev.validate_historical_v2 minute|daily`, B HYBRID-S smoke (in memory,
  B's own `_validated_table` + `validate_sparse_session`): `app.dev.smoke_b_hybrid_s_v2 --symbols ...`.

Loss budget: the minute queue is ~13,000 calls (~47 h), then daily ~4,800 (~17 h). The window moves one
session per US weekday. B's first scope session is 2024-10-02, so every session lost during this run is
RVOL warmup (2024-09-17..10-01), not a scope day.

## 6. Freeze

Staging files are members with `storage_tier = LOCAL_STAGING` and a `local_staging/` path prefix; the
snapshot records `storage.drive` and `storage.local_staging` and embeds `unavailable_rolling_window.json`.

`app.dev.build_historical_snapshot_v2` writes `snapshot.json` with raw_data_range, evaluation_ranges,
strategy_requirements_digest, b_fetch_universe_digest, coverage_digest, manifest_digest, limitations and
the V1 members plus new members. It refuses while a required kind is MISSING unless the user names it in
`--accept-missing` (it becomes a limitation). Sessions lost to the window are UNAVAILABLE, not MISSING.
