# H-V2 D7 Production Activation V1

**Result: HEALTHY (2026-10-10 18:45-18:51 KST).** Repair 81f06c8 is deployed and the 8-CIK SEC
cache is bootstrapped. The material scan replaced the queue. The timer is enabled and a manual
service run succeeded. Prices, outcomes, ledger, decisions and A/E are all unchanged.

Preceded by `H_V2_D7_OPERATIONS_ACTIVATION_V1.md` (failed, rolled back) and
`H_V2_D7_OPERATIONS_REPAIR_V1.md` (the design). Neither is edited. Approval: the operator's
instruction approved Phase 2 conditional on the Phase 1 sync passing. Phase 1 passed with
checkpoint c44dc79.

## A. Provenance

| File | Source commit | sha256 (local = server) |
|---|---|---|
| `backend/app/dev/run_h_v2_d7.py` | 81f06c8 | `d28534bf…337f86` |
| `backend/app/strategies/h_forward/sec_refresh.py` | 81f06c8 (new) | `55a0cec7…2ec8` |
| `/etc/systemd/system/usb-h-forward.service` | 81f06c8 | `fd7d81a5…0937` |
| `/etc/systemd/system/usb-h-forward.timer` | 75c42c0 (unchanged by 81f06c8) | `111678d0…1a91` |

c44dc79 is docs-only. `git diff 81f06c8 c44dc79 -- backend deploy` is empty, so the runtime source
is 81f06c8. The server git HEAD is still `db41f04`. The two code files were installed over it as
files (`run_h_v2_d7.py` shows as modified, `sec_refresh.py` as untracked). No other Python or
frontend file was deployed, and no service was restarted: `app.main` imports neither file.

## B. Backup

`/root/h_d7_backup_pre_repair_20261010-184543.tar.gz` (sha256 `6b494a9a…3c24`, `gzip -t` OK). It
holds `data/runtime/strategy_h_v2/d7/`, `backend/app/dev/run_h_v2_d7.py` and
`backend/app/strategies/h_forward/`. The working evidence is in `/root/h_deploy_20261010-184543/`
(status before/after, A/E manifests, SEC report, dry-run, scan, journal).

## C. SEC bootstrap (8/8)

`sec-refresh` made 8 HTTP requests, all 200, with 0 retries. ET day 2026-10-10. Root:
`data/runtime/strategy_h_v2/d7/sec_submissions/2026-10-10/` (260 KB in total).

| Ticker | CIK | Rows | Latest filing (any form) |
|---|---|---|---|
| AEYE | 0001362190 | 694 | 2026-10-02 |
| COLL | 0001267565 | 865 | 2026-10-08 |
| DORM | 0000868780 | 1000 | 2026-09-16 |
| FG | 0001934850 | 347 | 2026-10-05 |
| IDCC | 0001405495 | 1003 | 2026-10-06 |
| SCCO | 0001001838 | 1000 | 2026-09-25 |
| TG | 0000850429 | 1003 | 2026-10-02 |
| VRRM | 0001682745 | 657 | 2026-10-05 |

The hard gate passed. AEYE has rows > 0, and its 8-K of 2026-09-18 (`0001104659-26-108940`, items
1.01/2.03/9.01) is visible.

## D. Refresh scan

The dry-run reported `problems=[]`, NO_SUBMISSIONS_CACHE 0, and the queue file unchanged. It
showed two new REFRESH_DUE issuers. Both are WATCH issuers whose new filings came after their old
caches (9/28 and 9/21), so the old caches could not see them:

| Ticker | Before | After | Filing |
|---|---|---|---|
| AEYE | REFRESH_DUE | REFRESH_DUE | 8-K 2026-09-18 (1.01, 2.03, 9.01) |
| FG | NO_NEW_…_AS_OF_CACHE | **REFRESH_DUE** | 8-K 2026-10-05 `0001934850-26-000095` (2.02 results of operations, 7.01) |
| VRRM | NO_NEW_…_AS_OF_CACHE | **REFRESH_DUE** | 8-K 2026-10-02 `0001193125-26-412497` (5.02 officer/director change, 7.01, 9.01) |
| COLL, DORM, IDCC, SCCO, TG | NO_NEW_…_AS_OF_CACHE | NO_NEW_MATERIAL_EVIDENCE | none after 2026-09-16 (cache fetched today) |

No known filing was lost, AEYE was kept, and the queue did not shrink. The actual scan replaced the
queue atomically: `43123c04…` → `48f51f97…`, with no temp file left behind.

These are research-queue entries only. No D3→D6 rerun happened, and the decisions stay
0 / 6 / 2.

## E. Timer and service smoke

The timer was installed and checked with `systemd-analyze verify` (OK), then `daemon-reload` and
`enable --now`. Results:

* Timer: enabled, active (waiting); next trigger Mon 2026-10-12 14:10 KST (01:10 ET). `LAST` still
  shows 18:01:23, a stamp left by the first activation; the timer itself has not fired since.
* Service: `WorkingDirectory=/root/usb`.

Manual `systemctl start usb-h-forward.service` (18:47:44-18:47:46) gave Result=success with exit 0.
The JSON output showed:

* prices: requested 0, written 0 (no gap, latest session 2026-10-09);
* integrity PASS;
* 0/6/2;
* 1D 8/8 and 5D 8/8 matured;
* refresh `OK / READY` with 0 SEC requests (the same day's cache was reused);
* queue → `b7dc6482…`. It differs from `48f51f97…` only in `generated_at`.

`refresh_status.json` sha256 is `a3658e40…`, with `research_refresh_due = [AEYE, FG, VRRM]`.

## F. Invariants

* Price files: all 17 sha256 identical before and after the smoke run.
* `status` before and after: identical (every row, every horizon).
* `verify`: PASS, `problems=[]`. Launch snapshot `dba04d4b…` and ledger `6cd6009d…` unchanged.
* Decisions: APPROVE 0 / WATCH 6 / REJECT 2. Decision mutations: 0.
* A/E: the sha256 manifest of the 114 files under `data/` outside H and crypto (including the paper
  SQLite DB) is identical before and after, and 0 files outside H/crypto are newer than the deploy
  marker. A/E timers, usb-backend and usb-frontend all keep their pre-deploy start times.

## G. API and UI

* API on the production backend (127.0.0.1:8000), all HTTP 200: `/api/v1/strategies`, `/cards`,
  `/performance`, `/STRATEGY_H_V2/forward`, `/STRATEGY_H_V2/forward/VRRM`, `/STRATEGY_H_V2/status`.
  * The forward payload says 0/6/2, 1D/5D 8/8 matured, and 21D (2026-11-02) and 63D (2027-01-04)
    with 8 pending each.
  * `position_rule.sizing_contract = NOT_DEFINED`, `approve_creates_position = false`.
  * VRRM: RECENT_6M, `upside_to_tp1 = 0.248`, Bear null, `bear_na_reason = NEGATIVE_IMPLIED_EQUITY`.
* UI rendered in headless Chromium. The deployed frontend bundle (port 3000) was loaded over an
  SSH tunnel, and its `usb.jptcalc.kr` GETs were rerouted to the production backend. Non-GET
  requests were blocked; there were 0 of them. 18 API calls, all 200.
  * `/dashboard`: H WATCH 6, REJECT 2, APPROVE 0, positions 0, "1D 8/8 · 5D 8/8 · 21D 0/8 · 63D 0/8".
  * `/strategy-h`: horizon table 1D 8/0, 5D 8/0, 21D 0/8, 63D 0/8. VRRM shows RECENT_6M, Bear N/A,
    "range 불완전".
  * `/strategy-compare`: tabs ALL/A/E/H, H decisions 0/6/2, H excluded from Combined.

## H. Result

```text
H-V2 D7 PRODUCTION      = HEALTHY
price/outcome           = HEALTHY
SEC submissions refresh = HEALTHY (8/8)
material scan           = HEALTHY
timer                   = ENABLED / ACTIVE
```

Degraded-path behaviour is not exercised in production. It was proven locally against the real
fixtures (`test_d7_refresh_independence.py`): with SEC down, price/outcome continues, the queue is
preserved and REFRESH_DEGRADED is recorded.

## I. Rollback (not needed)

The procedure is in `docs/operations/H_FORWARD_OPERATIONS_RUNBOOK_V1.md` §7, using the backup in B.

## J. Remaining

1. AEYE, FG and VRRM are REFRESH_DUE. A D3→D6 re-evaluation needs operator approval (model cost).
   Their decisions do not change until then.
2. sizing = NOT_DEFINED. A future APPROVE becomes an entry candidate only
   (`SIZING_CONTRACT_REQUIRED`).
3. The first timer-driven run is Mon 2026-10-12 01:10 ET. It is the first unattended proof, and
   its journal should be checked.
4. Server git HEAD `db41f04` with 2 files installed over it. 49502bc, 81f06c8 and c44dc79 are
   local only (not pushed).
