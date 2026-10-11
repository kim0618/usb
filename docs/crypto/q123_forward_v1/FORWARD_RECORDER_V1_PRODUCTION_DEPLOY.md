# Q1/Q2/Q3 Forward Recorder V1: production deploy record

Deployed 2026-10-11 15:57:45 KST on trader-j. Source: main `20cba2c` (Q123 code identical to `264e719`).
Verdict: **PRODUCTION_Q123_FORWARD_RECORDER_PASS**. Research recorder only: no order, account, key or
signal. Contract `CONTRACT_Q123_FORWARD_V1.md` sha256 `9d855ad7…` unchanged.

## 1. Release

`deploy/crypto_q123_forward/stage_release.sh 20cba2c`: tarball `q123-forward-20cba2c.tgz` sha256
`27aed3f1…`, 64 files, `MANIFEST.sha256` sha256 `defd42a1…`. All 64 files equal `git show 20cba2c:<path>`.
The manifest is byte-identical to the pre-merge evidence release `q123-forward-ebc78db.tgz` (`21e11e1c…`).
The tarball sha differs only through tar/gzip metadata.

## 2. Installed

    /root/usb_runtime/q123_forward/
      releases/q123-forward-20cba2c/   (root:root, go-w, MANIFEST verified on host, no .pyc)
      src -> releases/q123-forward-20cba2c
      config/q123_forward.env          (600, from the release; no credential)
      store/ (700)  runtime/ (700)  logs/
    /etc/systemd/system/usb-crypto-q123-forward.service   (from the release, enabled)

Only `daemon-reload` and `enable --now` of this unit were run. No other unit was started, stopped or
restarted.

## 3. Startup and reconstruction

The recorder started with `restored_from: SESSION_START` and replayed collector session `31cc8177` from
its first segment. That is about 18.4 h of samples (66,243 samples). It took 14 minutes at the 15%
CPU quota (throttled) and reached `RUNNING` at 16:11:42 KST with a lag of 11 s. During catch-up the
state was `WAITING_FOR_DATA`, the defined state while the newest consumed sample is older than 120 s.

| | value |
|---|---|
| peak RSS (catch-up) | 119.7 MB (VmHWM 122.6 MB); cgroup peak 147.7 MB of 180 MB, of which ~36 MB inactive file cache |
| steady RSS | ~119 MB (the allocator keeps the catch-up heap; local live-only measurement was 87-92 MB) |
| memory.events | max 0, oom 0, oom_kill 0; swap 0 |
| CPU | quota-bound at 15% during catch-up; 3.9% steady (90 s window) |
| host PSI memory full avg60 | 2.94 before, 0.06 after |

## 4. Store at 16:12 KST (coverage 2026-10-10 21:47:23 to 2026-10-11 16:11 KST)

approaches 132, events 82, outcomes 260 (MEASURED 258, MISSING 2: `GAP_OVER_5S_AT_TARGET` at 30 min),
quality 19, conflicts 0, unrecoverable sessions 0. Duplicate keys 0 in every file.

| | Q1 | Q2 | Q3A | Q3B |
|---|---|---|---|---|
| approached | 25 | 25 | 41 | 41 |
| admitted | 12 | 12 | 29 | 29 |
| valid | 9 | 9 | 26 | 26 |
| invalid (`GENERATION_CHANGED_IN_W`) | 3 | 3 | 3 | 3 |
| fired | 2 | 0 | 8 | 0 |
| stage (firing with 60 m outcome) | INSUFFICIENT (2) | INSUFFICIENT (0) | INSUFFICIENT (8) | INSUFFICIENT (0) |

Non-admitted reasons: `OUTSIDE_JOURNAL` 13/13/11/11, `NO_WALL_IN_BAND` 0/0/1/1. `automatic_promotion`
is `PROHIBITED`. The only LONG/SHORT strings in the store are `detector.lean`, the contract's fixed
per-question constant (Q1 SHORT, Q2 LONG, Q3A LONG, Q3B SHORT). No state, verdict or WAIT exists.

## 5. Detector parity (batch `run.compute` on a read-only copy of the same journal)

Store snapshot taken first, then the journal copied, so the batch input covers everything the store saw.
Clean source `git archive 20cba2c`.

| check | result |
|---|---|
| events | 82 / 82 byte-identical detector records, recorder-only 0, batch-only 0 |
| outcomes | MEASURED 258 / 258 identical, MISSING 2 / 2 agree |
| exclusion census | identical for all four questions |

The batch run needed 5.16 GB RSS for 18.4 h of journal, which is why it cannot run on the host.

## 6. Idempotency (isolated copies, deployed release code, offline klines)

| run | read | new lines | skipped existing | conflicts | store bytes |
|---|---|---|---|---|---|
| fresh store | 66,272 samples | 82 ev / 260 oc / 132 ap | | 0 | equal to the production snapshot (all 4 files, every key, every line) |
| production snapshot, fresh runtime, full reprocess | 7,915 buffered after skipping to the earliest unfinished fact | 0 | 3,516 | 0 | unchanged |
| fresh store again, fresh runtime, full reprocess | same | 0 | 3,516 | 0 | unchanged |
| same store, reused checkpoints | 7,915 | 0 | 116 | 0 | unchanged |

Pitfall: with an existing store and a fresh runtime, `--until-idle` stops after two cycles that buffer
nothing, which happens while skipping to the earliest unfinished fact. Those runs read 0 samples and
prove nothing. The full reprocess above used `--max-cycles 300 --poll-s 0` instead. Production runs
the polling loop and is not affected.

## 7. Session boundary

The production journal holds one collector session (`31cc8177`), and all 82 events belong to it. No
outcome crossed a session. The four boundary tests (session end MISSING never bridged, trigger in a gap
excluded, decision across a session end excluded, missing first segment refused) pass on `20cba2c`.

## 8. Isolation

* Inside the unit's mount namespace, the collector journal and `crypto_paper/run` are on a read-only
  mount. Only `/root/usb_runtime/q123_forward` is read-write.
* Journal: no new name in the journal beyond the collector's own segments, and no recorder fd under the
  journal in write mode.
* Environment: 0 key / secret / token / pass / binance / kiwoom / api variables. The same grep finds 3
  in usb-crypto-paper.
* Imports on host: 46 `app.*` modules, 0 under paper / live / binance / services / terminal / exit_guard
  / c1. The only network library import is `frames.py` (GET `https://fapi.binance.com/fapi/v1/klines`).
  A 90 s socket sample caught no open connection: the kline poll is a short request once a minute
  (`kline_last_open_ms` advances and `kline_error` is null).

## 9. No side effects (before 15:55 / after 16:15 / final 16:20 KST)

usb-crypto-context 1582777, usb-crypto-paper 1669431, usb-crypto-liqfwd 3250885, usb-frontend 1672252,
usb-backend 1556760, nginx 707, usb-e-rvol 2515148: same MainPID, NRestarts 0. Nine usb timers enabled
and active, unchanged. Collector writer lock still pid 1582777, session 31cc8177.
BTCUSDT LONG 0.108 @ 83913.1, 20x CROSSED, ONE_WAY, update_time_ms 1791339778021 unchanged. fills 17,
newest id 8151740768 unchanged. armed false, arm_count 0. exit-guard OFF. c1-auto enabled with
has_position (unchanged). REST trade_requests 0, errors 0. Five paper ledgers have the same sha and
line counts. Order, CLOSE, leverage, AUTO and account mutation: 0.

## 10. Storage

Store 514 KB after 18.4 h with 82 events, i.e. ~107 events/day and ~6.2 KB per event including outcomes
and approaches. Projection (no retention): ~0.67 MB/day, 30 d ≈ 20 MB, 60 d ≈ 40 MB, 90 d ≈ 60 MB.
Runtime 7.2 MB (kline cache 6.5 MB, about +0.2 MB/day). Disk / 2.2 G free.

## 11. First 24 hours

Not run. `first_24h_summary.sh` is allowed from 2026-10-11 21:47:23 KST. The 24H result is a count
and says nothing about direction.

## 12. Rollback (Q123 only)

```bash
systemctl disable --now usb-crypto-q123-forward
rm /etc/systemd/system/usb-crypto-q123-forward.service
systemctl daemon-reload
# keep /root/usb_runtime/q123_forward/store; never restart collector, paper, frontend, nginx, A/H
```

Evidence (local): `~/usb-q123-evidence/production-deploy-20261011/` (SHA256SUMS).
