# H-V2 Facts Cache Compaction Repair V1

2026-10-18 KST. Data-cache correctness fix, allowed by the freeze policy (state document §0). No strategy
rule, threshold, ranking, E1/E2/E3, MethodJudgement, D3-D6, paper or forward code changed. No model call.
Code commit: `1e8d1bd` (`backend/app/strategies/h_auto/facts_cache.py` and tests only).

## 1. Failure

2026-10-16 15:40 KST, first gated `usb-h-evaluation` run: `intake-scan` refreshed 700 CIKs, then died
re-scanning 1,413 rows on `InputsNotReady: SEC_SUBMISSIONS_NOT_FOUND: 0000006201` (AAL), exit 1, before any
index, queue, ledger or paper write. Timer disabled again at 15:56 KST.

## 2. Ownership of the cache lifecycle

| Step | Owner | What it decides |
|---|---|---|
| fetch, body write, ledger write | `sec_store._page`, `xbrl_store.fetch_cik` (C-E0 / EQM stores, shared, not changed) | body through `.partial` then rename, ledger after the body |
| body existence for reuse | the same stores: a document is a cache hit only when **body and ledger** both exist; cache-only (`client=None`) it is otherwise NOT_FOUND / MISSING | |
| request freshness | `facts_cache.index` / `state_of` (primary submissions ledger `fetched_at`, 7 days) | |
| refetch requirement | `facts_cache.plan` (missing, then oldest fetch, then CIK; cap 700) | |
| load | `facts_cache.load` -> `inputs.fetch_sec(client=None)` | |
| eviction | `facts_cache.compact` (bodies of non-candidates; ledgers kept) and `prune` (dated roots beyond 6) | disk |

Root cause: `index` judged a copy reusable from the ledger alone, which is not the stores' own reuse rule.
`compact` kept the ledgers as documented, so 691 + 10 compacted CIKs stayed FRESH, `plan` never scheduled
them, and the cache-only `load` reported the missing body as SEC_SUBMISSIONS_NOT_FOUND. The executor's own
path (`fetch_sec` with a client, intake.py:283) already refetched a missing body; the SCAN path did not.

The fix therefore sits in the planner view (`index` / `state_of`), not in `compact`: it also covers a body
lost any other way, and it leaves the stores and the eviction policy as they were.

## 2a. Change

* `CacheEntry.usable`: every submissions page and the companyfacts document stored for the CIK in that
  root has both body and ledger (names only; `.partial` files ignored).
* `index` prefers a usable copy over a newer one that is not.
* `state_of` returns `EVICTED` for a copy that is not usable. `plan` is unchanged: `EVICTED` takes the
  existing non-missing class (oldest fetch first). Ranking it as MISSING would break ties by CIK and give
  the same lowest CIKs the whole cap every day, because `compact` evicts every scanned non-candidate.
* The SCAN already reports any non-FRESH state as `FUNDAMENTALS_FETCH_REQUIRED` and never scans it; the
  candidate filter already requires `facts_state == FRESH`. `summary` counts `EVICTED`.

Cache contract after the fix:

| Case | Result |
|---|---|
| A ledger fresh, bodies present | FRESH, REUSE, 0 requests |
| B ledger fresh, a body missing | EVICTED -> planned -> only the missing documents refetched (1 request each) |
| C body present but damaged | unchanged store contract: the reader raises, the SCAN stops (fail closed); never scanned |
| D ledger stale, bodies present | STALE, existing policy |
| E no ledger, no body | MISSING, planned first |

Partial failures (tests): 429/5xx are retried by the client and then stored; exhausted retries end the slice
(`SEC_DEGRADED`) with no fresh copy of that CIK; submissions stored but companyfacts exhausted reads back
`facts_fetched=False` (E1: FUNDAMENTALS_NOT_FETCHED, DATA_NOT_READY), as before; an empty 200 fails closed
now and on the next read; body without ledger, ledger without body, an older page without its body, and an
interrupted compact are all EVICTED and refetch exactly the missing documents.

Residual, unchanged: a process killed between one CIK's submissions and companyfacts downloads leaves a copy
without a companyfacts ledger, which reads as "facts not fetched" (DATA_NOT_READY for up to 7 days), not as
false data. A damaged-but-present body needs an operator to remove that CIK's ledger.

## 3. Verification (local)

* New `tests/strategy_h_v2/forward/test_h_facts_cache_lifecycle.py`, 23 tests on the real stores behind a
  fake HTTP transport (cases A-E, compact roundtrip, older pages, duplicate fetch, the 10-16 AAL shape,
  partial failures, interrupted compact, disk bound). 12 of them fail on the pre-fix code.
* Two fixtures in `test_h_production_intake.py` encoded the defect (fake refresh wrote ledgers without
  bodies; the compact test asserted `index()` unchanged after compaction) and now follow the store contract.
* `tests/strategy_h_v2`: 1,815 passed, 172 skipped, 26 failed; the same 26 fail on the pre-fix code (set
  difference 0). They are research-era tests that regenerate samples from a universe store this host does
  not have (`regenerate_sample_from_universe() == ()`), none imports `facts_cache` or `intake`.
* Production cache copy (`auto/sec`, read-only rsync of 2026-10-18, no SEC call): 1,413 cached CIKs. Old
  reading 1,413 FRESH. New reading 712 FRESH (700 in 2026-10-16, 12 kept candidates in 2026-10-15) and 701
  EVICTED (691 in 2026-10-15, 10 in 2026-10-14). The old cache-only load of the first 50 evicted CIKs:
  50/50 SEC_SUBMISSIONS_NOT_FOUND. AAL: old FRESH, new EVICTED.
* Complete-cache parity on that copy: for all 712 usable CIKs the index entry (day, fetched_at, has_facts)
  and the loaded inputs (submissions document, checksum, fetch time, facts flag, canonical facts) are
  identical old vs new. intake, E1/E2/E3, MethodJudgement and D3-D6 code is byte-identical, so a complete
  cache yields the same candidates in the same order.
* `index()` on the copy: 0.09 s old, 0.13 s new.

## 4. What the next SCAN will do

`plan` takes MISSING CIKs first (market-passing CIKs never cached; on 10-16 the whole 700 cap went to them),
then EVICTED by oldest fetch. So the first run after deployment most likely refetches 700 never-cached CIKs
(about 1,450 requests at 5/s, about 10 minutes, as on 10-16: 753 submissions pages + 698 companyfacts for 700
CIKs) and reports the 701 evicted ones as FUNDAMENTALS_FETCH_REQUIRED instead of crashing; they are fetched
on later runs. Rows scanned: the 712 usable plus the 700 fetched.

Steady state: `compact` evicts every scanned non-candidate each run, so about 700 CIKs (the cap) are usable
per run and rotate oldest-first; each issuer is rescanned every few runs instead of every run. This is the
honest consequence of keeping disk bounded (one full copy per market-eligible CIK is about 480 MB, and
trader-j has about 0.7 GB free), not a ranking change. Disk: at most one run's refetch (about 180 MB) before
that run's compact.
