# Manual Trade Snapshot Recorder V1: predeploy finalization (2026-10-12)

Verdict: **READY_FOR_MANUAL_RECORDER_PRODUCTION_APPLY.** Nothing is installed on the host: no
unit, no timer, no runtime directory. This document is for the approval; the deploy itself waits
for the user.

## 1. Git

| commit | |
|---|---|
| d6349e7 | contract |
| 7072933 | implementation |
| bb9e88c | report |
| (this commit) | trigger OFF in the shipped env, two regression tests, this document |

All on `main`. origin/main = 31b6f2d, so these are not pushed. Push is the user's:
`cd ~/usb && git push origin main`.

## 2. Defect found and fixed: the 5-minute forced refresh was ON in the shipped env

The code default was off (`--trigger-url` default `""`, `recorder.py:262,274`), but
`deploy/manual_trade_research/manual_trade_research.env:14` set
`MTR_TRIGGER_URL=http://127.0.0.1:8100/api/crypto/binance/performance?symbol=BTCUSDT`, and the
report said "on by default". That GET makes the terminal call `performance.refresh()`
(`terminal/live_routes.py:1044-1058` → `live/performance.py:149-164`), which sends signed Binance
reads (`userTrades`, `income`, `query_order` for unclassified orders) and rewrites S1. Installed as
shipped, the recorder would have added about 288 private queries a day and broken the "S1 sha
unchanged" isolation check.

Fix: `MTR_TRIGGER_URL=` (empty). With systemd's `--trigger-url=${MTR_TRIGGER_URL}` this is
`--trigger-url=`, which `main()` turns into `trigger=None`. Tests:
- `test_shipped_env_leaves_the_s1_trigger_off`: the env file value is empty (control: `MTR_S1` is read by the same parse).
- `test_empty_trigger_url_means_no_trigger`: `--trigger-url=` hands `run_once` no trigger (control: a non-empty value does).

Turning the trigger on is a separate approval.

## 3. Read-only and mutation audit (static, plus import graph measured)

| check | result | evidence |
|---|---|---|
| S1 `manual_performance.json` | read only | `sources.py:53` `read_text()`; no write, rename or unlink of it |
| LIVE mirror `binance_live_events.jsonl` | read only | `sources.py:151,163,176` `open(path, "rb")` |
| Context Collector journal | read only | `collector.py:21-22` `gzip.open/open(..., "rb")`; state and latest files are not referenced |
| writes | own store only | `store.py:22-23,31,59,63,96,106-109`, `sources.py:179,185`, `market.py:108-111`; systemd `ProtectSystem=strict` + `ReadWritePaths=.../store` |
| order path import | 0 | importing the package under `unshare -rn` adds only `app`, `app.crypto` and the 8 package modules to `sys.modules` |
| network | public GET only | `market.py:23,45-55`: klines, mark/index klines, fundingRate, openInterestHist, aggTrades. No key, no signature, no order/account/positionRisk/leverage/userTrades |
| account, AUTO, ExitGuard, leverage, Safe MAX | 0 code paths | no import, no POST/PUT/DELETE |
| private Binance query added by the recorder | 0 (after the fix) | the trigger was the only path, now off |

Tests: `backend/tests/crypto/test_manual_snapshot_v1.py`, network blocked (`unshare -rn`):
**26 passed** (24 before, plus the 2 above).

Offline run on production copies of S1 and the mirror tail, network blocked, `--trigger-url=`:
the run reached the public market fetch and failed there on DNS (expected without network). The
trigger was not built. RSS 26 MB, 0.19 s up to that point.

## 4. Production resource preflight (read only, 2026-10-12 08:52 KST)

| unit | PID | NRestarts | MemoryCurrent | MemoryMax | swap |
|---|---|---|---|---|---|
| usb-crypto-paper | 1669431 | 0 | 186.4 MB | 200 MB | 36.1 MB |
| usb-crypto-context | 1582777 | 0 | 48.4 MB | 200 MB | 9.4 MB |
| usb-crypto-q123-forward | 1684303 | 0 | 81.9 MB | 180 MB | 35.1 MB |
| usb-btc-context-ui | 1701557 | 0 | 3.2 MB | 96 MB | 34.5 MB |
| usb-crypto-liqfwd | 3250885 | 0 | 22.1 MB | 100 MB | |
| usb-frontend | 1700688 | 0 | 47.8 MB | none | |
| usb-backend | 1556760 | 0 | 23.5 MB | none | |
| nginx | 707 | 0 | 13.0 MB | none | |

Host: total 961 MB, available 272 MB, swap used 541 of 2047 MB. PSI memory some avg60 1.01,
full avg60 0.57. crypto-paper `memory.events`: max 177, oom 0 (98 on 2026-10-11 18:38).
No `usb-crypto-manual-research` unit or timer exists.

## 5. Will the recorder worsen crypto-paper's memory pressure?

crypto-paper's pressure is its own cgroup: 186 of 200 MB, and the `max` count grows by itself
(98 → 177 overnight, with no recorder). The recorder runs in a separate cgroup
(`MemoryHigh=64M`, `MemoryMax=96M`), so it cannot charge crypto-paper's limit. It could only affect
crypto-paper through host-level reclaim:

- steady state: 38 MB for ≤ 2 s once a minute, so about 3% of the time; host available 272 → about
  234 MB during a run. The BTC context deploy ran with available 170-197 MB and no OOM;
- first runs (backfill, 500-snapshot chunks): 28 MB per chunk; the 60 MB / 41 s single-shot
  figure applies only without the chunk limit, which the unit sets (`--max-snapshots 500`).

Conclusion: no measurable effect on crypto-paper's cgroup limit is expected. The rising `max`
count of crypto-paper is a separate, pre-existing item (C1 runtime full rescan, chart cache;
see the BTC context UI memory notes) and should be watched in the deploy's isolation check
rather than attributed to the recorder.

## 6. Points for the deploy itself

1. On the host, confirm the installed env reads `MTR_TRIGGER_URL=` and the first run prints `"trigger": null`.
2. Confirm the S2 fingerprint directory: `.../live/487ea08e/binance_live_events.jsonl` exists
   (it did on 2026-10-12, 8.9 MB). A wrong path makes every `usbm-` fill `USBM_UNVERIFIED`, which
   would merge ExitGuard closes into manual ones.
3. Isolation check as in report section O step 6. With the trigger off, S1 may change only by the
   terminal's own refresh (the UI's GET).
4. A venue or DNS failure ends the oneshot with exit 1 (seen offline). Nothing is half-written:
   rows are appended one fsynced line at a time under keys, and the next minute retries. Expect
   `failed` runs in the journal during a Binance outage; they are not a recorder fault.
5. The open BTC position (LONG 0.108 @ 83,913.1, filled 2026-10-07 11:22 KST) predates the
   Context Collector (production since 2026-10-10 21:47). Its entry snapshot will have
   collector `NOT_AVAILABLE`; market (OI, funding, basis) comes from public history.
