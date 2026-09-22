# Strategy E-MAX - F1 Forward Data Collection V1

| | |
|---|---|
| Verdict (2026-09-22 14:00 KST) | **E-MAX-F1 PARTIAL — COLLECTION CONTINUES** |
| Contract | `strategy_e_max_forward_collection_rules_v1.json`, canonical `f8d17972…d731` |
| Upstream | F0 `91856b3` (forward rules `dd8ddf42…`), M6 `0a439a3` (V1 rules `b30a3e95…`) |
| Code | `app.strategy_e_max_forward.{storage,forward_daily,collect,collection_rules,readiness}`, CLI `app.dev.run_strategy_e_max_f1` (Phase A commit `6bdf151`) |
| Tests | `backend/tests/strategy_e_max/test_e_max_f1_collection.py` |
| Source | MASSIVE Stocks Basic · RECONSTRUCTED · SECONDARY (no LIVE, no Kiwoom) |

F1 collects data only. It computes no forward return, compares nothing and evaluates no
checkpoint. E-MAX V1 is unchanged.

## 1. Preflight

- Branch `main`, starting HEAD `91856b3`. M6 `0a439a3` and F0 `91856b3` are both ancestors of HEAD.
- The staged area was empty. The unrelated A/B/C/D dirty worktree was left untouched.
- The F0 preflight passes: V1 rules, M6 result and verdict, M6 code identity `e4c4a6e3…`
  recomputed equal, development tape `d12ff28a…`, E-BASE V1.1.

## 2. Storage architecture

**Raw forward market data** is strategy-independent. It lives on the workspace beside the frozen
snapshot tree, following the existing E1 forward layout:

```text
market_data/forward/massive/
  grouped_daily/<year>/<D>.json.gz (+ .validation.json)
  splits/splits_asof_<D>.json.gz (+ .validation.json)
  reference/CS_<as_of>.json.gz (+ .validation.json)
  minute/<DIR>/<SYM>_<a>_<b>.pNN.json.gz + .request.json + .validation.json
  rvol_context/<DIR>/...        pre-boundary minute history for RVOL only
  manifests/run_<run_id>.json   immutable, one per run
  quarantine/<run_id>/          failed or refused files, kept
```

**Strategy outputs** (seals, registry, sessions) stay under `data/runtime/strategy_e_max/forward/`.
Run logs, readiness files and manifest copies go to `data/runtime/strategy_e_max/forward/collection/`,
which is gitignored.

**Isolation rules:**

- Every write passes `require_forward_tree`, which refuses any path outside `market_data/forward`.
- Nothing is written to the frozen Common Raw tree. USB-HIST-V1 is frozen and new strategies may
  not collect into it.

## 3. Existing collector reuse (no modification)

| Input | Reused function | Wrapper |
|---|---|---|
| Grouped daily | `strategy_c_selection.raw_fetch.fetch_grouped` | stage → validate → move to `grouped_daily/<year>/<D>` |
| Splits as of D | `strategy_c_selection.raw_fetch.fetch_splits` | executions in [2026-09-01, D] → `splits_asof_<D>` |
| CS reference | `strategy_c_selection.raw_fetch.fetch_tickers` | quarterly cadence as in development; `CS_2026-07-01` still the latest <= D-1, next due 2026-10-01 |
| Minute | `historical_store.raw_fetch.fetch_request` | request subclass pointing at the forward tree and the `_<SYMBOL>` directory |

A provider refusal (403) is never published. For minute data, the ledger is quarantined so that a
later run can retry it.

## 4. Symbol path mapping

```text
symbol_to_storage("CON") = "_CON"      storage_to_symbol("_CON") = "CON"
symbol_to_storage("AAPL") = "AAPL"     (identity for every non-reserved symbol)
```

- The reserved names are the DOS device names (CON, PRN, AUX, NUL, CONIN$, CONOUT$, COM0-9,
  LPT0-9). The mapping round-trips, and no real ticker starts with `_`.
- File names, ledgers, sidecars and records all keep `CON`. Only the directory is `_CON`.
- Verified on real data: the RVOL history for `CON`, 2026-07-08 to 2026-09-16, is in
  `rvol_context/_CON/` (14,088 bars).

## 5. Collection results so far

| Input | Session | Rows | Validation |
|---|---|---:|---|
| Grouped daily | 2026-09-17 | 12,572 | PASS (0 duplicate, 0 invalid OHLC, 0 wrong-date rows) |
| Grouped daily | 2026-09-18 | 12,592 | PASS |
| Grouped daily | 2026-09-21 | 12,626 | PASS |
| splits_asof | 2026-09-17 | 104 | PASS (none executed after D) |
| splits_asof | 2026-09-18 | 109 | PASS |
| splits_asof | 2026-09-21 | 111 | PASS |
| SPY minute | 2026-09-17..21 | 2,684 bars | PASS |
| CON RVOL context | 2026-07-08..2026-09-16 | 14,088 bars | PASS |

**Split PIT limit.** `/v3/reference/splits` filters by execution date only and offers no
publication-time as-of view. So `splits_asof_<D>` means "executions <= D, as retrieved at
retrieved_at". That is admissible for RECONSTRUCTED under the E-R1 contract. It is not claimed to
be a LIVE 09:25 list.

**D-1 daily-eligible universe** (E0 rule through `universe.daily_eligibility`, forward-extended
panel):

| D | Eligible |
|---|---:|
| 2026-09-17 | 2,550 |
| 2026-09-18 | 2,557 |
| 2026-09-21 | 2,553 |

The same code reproduces the development eligibility exactly for 2026-09-16, 2026-05-01 and
2025-11-03 (parity check: identical sets of 2,554, 2,482 and 2,448 symbols).

**Minute collection** (in progress):

- 2,565 requests: the union of the three universes plus SPY, one request per symbol covering
  2026-09-17..2026-09-21.
- 13 s spacing, which is the client's `BASIC_CALLS_PER_MINUTE = 5` with a margin, as in
  historical_v2. That is about 9.3 h.
- Started 13:57 KST as a detached process (pid 17759). At 14:00 KST, 14 of 2,565 were done with
  0 failures and 0 retries.

## 6. Initial forward sessions (readiness at 13:59 KST)

| Session | Daily | Split | Minute | RVOL | SPY | Status |
|---|---|---|---|---|---|---|
| 2026-09-17 | OK | OK | 4 / 2,550 | OK (CON via `_CON`) | OK | NOT_READY (MISSING_MINUTE) |
| 2026-09-18 | OK | OK | 4 / 2,557 | 4 / 2,557 | OK | NOT_READY (MISSING_MINUTE, MISSING_RVOL_HISTORY) |
| 2026-09-21 | OK | OK | 4 / 2,553 | 4 / 2,553 | OK | NOT_READY (MISSING_MINUTE, MISSING_RVOL_HISTORY) |
| 2026-09-22 | - | - | - | - | - | NOT_READY (SESSION_NOT_CLOSED, plus inputs not yet due) |

- For 09-18 and 09-21, the RVOL history gap is the forward sessions after 2026-09-16, and the
  running minute requests fill it.
- Every NOT_READY session is FEATURE_CONTEXT_INCOMPLETE. None is turned into H5 = False.
- 2026-09-22 has not closed, so nothing is written for it.

## 7. Failures, retries, manifests

- **Failures and retries:** none so far. There were 0 quarantined files and 0 transient retries.
- **Rate limiting:** a 429 goes first to the reused fetchers' 120 s cooldown, then to the
  collector's 60/120/300/600/900 s backoff. NOT_AUTHORIZED stops the run.
- **Locks:** the process lock is `collector.lock`. The shared workspace writer lock is held per
  batch of 20 requests, as in historical_v2. No other collector was running at start.
- **Manifest** `run_20260922T045210Z-5ce2be.json`: status PARTIAL_MAX_MINUTE, 8 verified files.
  The running collector writes its own manifest when it ends.
- **Per-file sidecars:** sha256, rows and validation for every file. `run_strategy_e_max_f1
  verify` re-hashes every sidecar and page.

## 8. Development separation and M6 identity

- The development manifest contains no forward path (test).
- Forward writes outside `market_data/forward` are refused (test).
- The M6 code identity recomputed equals the committed `e4c4a6e3…`. No file was added to
  `app.strategy_e_max` or `app.backtest.strategy_e_max` (test).

## 9. How to continue

```bash
PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_e_max_f1 collect     # resume; verified files cost 0 calls
PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_e_max_f1 verify
PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_e_max_f1 readiness
```

Each new closed session adds 1 grouped, 1 splits_asof and about 2,555 minute requests. Collecting
in weekly batches keeps it at about one request per symbol per week (up to 50 sessions per
request).

## 10. Gate

```text
E-MAX-F1 PARTIAL — COLLECTION CONTINUES
```

The collector and every source dependency work. Daily data, splits, the universe, SPY and CON
context are complete for all closed sessions. What remains is whole-universe minute data, and it
is being collected.

**F2 (forward feature build)** can start once `readiness` shows 2026-09-17, 2026-09-18 and
2026-09-21 as READY. Until then, F2 should not be run on this data.
