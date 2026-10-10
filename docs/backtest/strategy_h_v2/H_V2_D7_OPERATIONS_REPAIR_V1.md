# H-V2 D7 Operations Repair V1

**Status: READY FOR PRODUCTION, not applied (2026-10-10).** Code, tests and local verification are
done. Production deploy, the SEC bootstrap and timer re-activation wait for the operator's approval.
This is an operations change. No strategy research, no threshold, valuation or decision change, no
model call.

Follows `H_V2_D7_OPERATIONS_ACTIVATION_V1.md` §B.2: the first activation was rolled back because the
production host had no SEC submissions cache and `refresh_scan` rewrote all eight issuers as
`NO_SUBMISSIONS_CACHE`, which destroyed AEYE's real `REFRESH_DUE`.

## A. Root cause (code audit)

`update()` ran `collect_prices()` → `VW.forward()` → `refresh_scan()` in one failure domain:

1. `refresh_scan()` wrote `refresh_queue.json` unconditionally, with a plain `write_text`. A missing
   cache was not a refusal. It was written into the queue as the issuer's state (`NO_SUBMISSIONS_CACHE`),
   on top of the existing research state.
2. The caches it read were repository-relative paths owned by other steps (D1.1, PV2C, C-E0, H0.5).
   None of them existed on the production host, and none of them was refreshed by anything.
3. Prices were already written before the scan, so a scan failure did not roll prices back. But any
   exception in the scan failed the whole unit and hid the price success. A missing cache did not
   raise at all: it exited 0.

The research state (the issuer has a filing the thesis has not seen) and the infrastructure state
(we cannot currently see SEC) were stored in the same field.

## B. Design

| Step | Failure domain | On failure |
|---|---|---|
| 1. `collect_prices` + `VW.forward` + `verify` | forward shadow | `verify` FAIL → exit 2 (unit failure) |
| 2. `sec_refresh`: today's submissions for the 8 cohort CIKs | material refresh | `REFRESH_DEGRADED`, exit 0, queue untouched |
| 3. `refresh_scan` if the cache is ready | material refresh | `REFRESH_DEGRADED`, exit 0, queue untouched |

* **New module** `app/strategies/h_forward/sec_refresh.py`. It reuses `strategy_c_e0.sec_store`
  unchanged (`SecClient`, `fetch_cik`, the 5 req/s limiter, retries, raw-bytes-plus-ledger
  immutability) with the D1.1 `USER_AGENT`. It adds no HTTP logic.
* **Cache location** `data/runtime/strategy_h_v2/d7/sec_submissions/<ET date>/submissions/CIK…/`.
  It resolves from the store root, which is module-relative, so it no longer depends on the working
  directory. `sec_store` never overwrites a file, so freshness comes from a new dated root per day.
  A same-day rerun reuses that day's root and makes no request. The 10 newest roots are kept
  (about 180 KB each). Pruning runs only after a complete fetch and only inside this directory.
* **Scope.** One primary submissions page per launch-snapshot CIK, which is 8 requests a day. The
  decision session lies inside SEC's `recent` block, so no older page is needed. There is no
  companyfacts request, no filing body and no other issuer.
* **Readiness** (`REFRESH_DATA_NOT_READY` otherwise). The scan uses the newest dated root that holds
  every cohort CIK with a readable page and a request ledger. That root's `fetched_at` (from the
  ledger, never the file mtime) must be at most `MAX_CACHE_AGE_DAYS = 4` old. In `update`, an
  incomplete fetch today skips the scan instead of falling back to an older root.
* **Guarded replacement.** `SR.validate` refuses a candidate queue in any of these cases:
  * its cohort differs from the launch snapshot;
  * any issuer is `NO_SUBMISSIONS_CACHE`;
  * a filing already in the queue disappears (`KNOWN_FILING_LOST`);
  * a `REFRESH_DUE` is cleared by a scan (`REFRESH_DUE_CLEARED_BY_SCAN`). Only a D3→D6
    re-evaluation can clear a `REFRESH_DUE`.

  An accepted queue is written as temp file → fsync → read-back check → `os.replace`.
* **Separate infrastructure state.** `refresh_status.json` is rewritten on every run (atomically). It
  holds `refresh_run` (OK / REFRESH_DEGRADED), `refresh_data` (READY / REFRESH_DATA_NOT_READY), the
  reasons, the per-CIK fetch report, `queue_replaced`, `queue_sha256` and `research_refresh_due`. AEYE
  can therefore read `REFRESH_DUE` (research) while the infrastructure reads `REFRESH_DATA_NOT_READY`.
* **CLI.** Added `sec-refresh` and `refresh-scan --dry-run`. A degraded run prints `REFRESH_DEGRADED:
  …` to stderr (journal). An integrity failure prints `H_FORWARD_INTEGRITY_FAILURE` and exits 2.
* **Unit.** Schedule (`Mon..Fri 01:10 America/New_York`), `WorkingDirectory=/root/usb`, flock,
  `ExecStart … update`, limits: all unchanged. Only the Description and the comments changed.

Nothing here reads or writes A or E. The UI and API do not read the refresh queue (grep: 0
consumers), so no frontend or backend API change is needed.

## C. Local verification (2026-10-10)

* `backend/tests/strategy_h_v2/forward`: 68 passed. The new file is
  `test_d7_refresh_independence.py`, and its fixtures are the real 2026-10-04 launch snapshot, the
  real queue (sha256 `43123c04…`) and AEYE's real 2026-09-28 submissions page (the 2026-09-18 8-K
  `0001104659-26-108940`).
* `strategy_h_v2` + `strategy_e_max` + `strategy_e_trading` + `strategy_a_mover_live`: 2690 passed,
  1 skipped.
* Real local store, no H cache (the incident scenario): `refresh-scan` and `refresh-scan --dry-run`
  both return `scan_performed=false`, `REFRESH_DATA_NOT_READY`, `refresh_due=[AEYE]`, and the queue
  sha256 stays `43123c04…`.
* `systemd-analyze verify` on both units: no finding for them. The only output is a host
  permission notice about an unrelated netplan unit.
* SEC live calls in the local phase: 0. Every test uses `httpx.MockTransport`, and the real client
  factory is replaced by one that fails the test.

## D. Production plan (requires approval)

1. Backup: `tar czf /root/h_d7_backup_pre_repair_<ts>.tar.gz data/runtime/strategy_h_v2/d7 backend/app/dev/run_h_v2_d7.py`.
2. Copy `backend/app/dev/run_h_v2_d7.py` (the server copy is byte-identical to the pre-change base,
   `86fc6a24…`) and the new `backend/app/strategies/h_forward/sec_refresh.py`. No service restart:
   `app.main` does not import either file.
3. Bootstrap: `cd /root/usb && PYTHONPATH=backend .venv/bin/python -m app.dev.run_h_v2_d7 sec-refresh`.
   This makes 8 SEC requests (plus retries only on 429/5xx) and costs $0.
4. Read-only check: `refresh-scan --dry-run`. Expected: AEYE `REFRESH_DUE`, 0 `NO_SUBMISSIONS_CACHE`,
   queue sha256 unchanged.
5. Write the queue: `refresh-scan`.
6. Install `usb-h-forward.{service,timer}` in `/etc/systemd/system`, then `daemon-reload` and
   `enable --now usb-h-forward.timer`. Optionally run the service once by hand and inspect the journal.
7. Rollback: `disable --now` the timer, remove both units, `daemon-reload`, restore the backed-up
   `run_h_v2_d7.py`, remove `sec_refresh.py` and the `sec_submissions/` directory, and restore
   `refresh_queue.json` from the backup.

## E. Known limitations (not D7 health failures)

* AEYE `REFRESH_DUE` → a D3→D6 re-evaluation needs operator approval (model cost). This step only
  keeps it in the queue.
* sizing = NOT_DEFINED. An APPROVE would be fail-closed (`SIZING_CONTRACT_REQUIRED`).
* 21D/63D are PENDING until their own maturity sessions.
* A new filing found by the daily scan reaches the research queue only. No D3→D6 rerun happens here.
