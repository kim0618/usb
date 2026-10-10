# 24-hour isolated trial: runbook and readiness, V1.5

The file keeps its V1.4 name so the V1.4 report and the status document keep resolving; the
content below is V1.5.

Isolated means: read-only public Binance endpoints, its own output root, its own process, no
production deploy, nothing shared with the paper or live trading runtime.

## Before starting

1. **One writer per root.** `flock` locks an inode, not a path, so a deleted lock file lets a
   second writer in silently (`feedback_flock_locks_inode_not_path`). Pick a root that no other
   process is using and never start a second harness on it. Confirm with
   `pgrep -af market_structure_v0` before launching.
2. **Disk.** Measured projections below. Reserve at least 3x the projection.
3. **The process must outlive this shell.** Use `setsid ... &` with stdin from `/dev/null`;
   a plain `nohup` dies with the session on this machine
   (`feedback_session_bg_process_dies`).

## Start

`$REPO` is this checkout, `$PY` its interpreter (`$REPO/.venv/bin/python3` on the workstation,
`/root/usb/.venv/bin/python` on the server).

```
cd "$REPO/backend"
setsid "$PY" -m app.crypto.market_structure_v0.collector \
  --root <TRIAL_ROOT> --duration 86400 > <TRIAL_ROOT>.log 2>&1 < /dev/null &
```

The preview reads the same root without writing to it:

```
MS_V0_ROOT=<TRIAL_ROOT> "$PY" -m app.crypto.liquidity_map 8080
```

## Storage, measured not estimated

From a 5-minute session with **9 generation transitions**, which is roughly 100x the natural
refresh rate, so this is a ceiling and not a forecast:

| kind | GB/day |
|---|---|
| wall | 3.32 |
| raw_depth | 1.64 |
| derived | 0.33 |
| trade | 0.30 |
| raw_trade | 0.23 |
| snapshot, telemetry, checkpoint, storage_stats, session | 0.33 |
| **total** | **6.14** |

`wall` dominates and scales with the number of transitions, so a natural 24 h session should
land well below this. Peak RSS 42.3 MiB with no drift; queue backlog max 568 of 8,192; dropped
records 0; state-file writes 300 with 0 failures.

Free space on the workstation at the time of writing: 863 GB. 6.14 GB/day is 0.7% of it.

## What the trial is for

The four things V1.4 could not settle, in order of what a day of wall-clock is most likely to
produce. V1.5 removed a fifth - a late snapshot installing onto a healthy book - by giving every
request an owner, so the trial now measures that the discard path stays quiet rather than
watching for the install:

1. **A real live HARD.** Still never observed outside forced conditions. A day should contain at
   least one reconnect or stale socket, and the question is whether `carried_lost` and the three
   counts behave on a HARD that nobody arranged.
2. **The safety refresh firing on its own.** It needs 3,600 s on one snapshot; coverage-edge
   refreshes keep resetting that clock, so it has never fired. A quiet overnight stretch is the
   only thing likely to produce one.
3. **The natural exempt rate.** V1.4's whole subject. In 30 minutes of natural triggers the
   round trips were inside the ceiling; the exemption's natural frequency is unknown and a day
   is the first sample that could estimate it.
4. **Coverage over a full session cycle**, including the Asia/Europe/US handovers, where the
   V1.2 measurement found the margin negative 3.7% of the time.

## What to check afterwards

Run the analyzer against the sealed root. It reads the journal, not the collector:

```
python3 docs/crypto/liquidity_map_v1/trial_analyze.py <TRIAL_ROOT>
```

It prints, per transition: refresh type, the gate reason, the window and its verdict, whether
the chain was preserved, and whether `carried + ended + unknown` equals the candidates held
before. Plus the rollback check straight from the checkpoint stream.

Acceptance, stated before the run:

* `carried + ended + unknown == candidates_before` on **every** transition
* `last_update_id` decreases: **0**
* every `EXEMPT_REPLAYED_CHAIN` transition has `chain_preserved: true` and all five gates true
* no transition with `window_exempt: true` and `chain_preserved: false` - that combination is
  impossible by construction and finding one means the construction is wrong
* `refresh_storm` count and every `refresh_rejected` reason accounted for
* dropped records 0

V1.5 adds four, and the analyzer prints each of them:

* every `snapshot` record's `response_disposition` accounted for, and **every** response whose
  disposition is not `APPLIED` has no `resync` and no generation change beside it
* **installs behind the chain: 0.** The analyzer's own check, and the signature of the V1.4
  defect: a `resync` whose `snapshot_update_id` is below the chain position already reached
* between two installed `coverage_edge` refreshes, at least **10 s**
* the +-0.1% band's COMPLETE ratio over the whole session, with the negative-margin runs. The
  pre-synchronisation samples at session start are UNKNOWN rather than COMPLETE and are counted
  separately: a book that does not exist yet is not a broken promise

## Readiness

**Ready to start.** The trial needs nothing that does not exist: the collector CLI takes
`--duration 86400`, the storage projection fits with two orders of magnitude of headroom, the
preview reads the live root without writing to it, and the acceptance criteria above are
written down before the run rather than after it.

**The V1.4 risk is closed.** It read: if a REST read ever exceeds 1,000 ms, the staged attempt
is abandoned and the late snapshot is then installed by the recovery path onto a healthy book
without a newer-check, which rolled the book back about 141,000 ids and produced a
`GAP_FIRST_DELTA` under forced conditions. V1.5 gives every request an owner and discards a
response whose attempt has ended, and the two forced sessions of 2026-10-04 measured 24 late
responses - 17 at a 1,400 ms delay and 7 at 2,500 ms - with **0 installs, 0 rollbacks, 0 gaps**
and an avoided rollback of 61,800 to 153,754 ids per response.

What to watch instead, in a 24 h session, is the opposite failure: a late response being
discarded when the book genuinely needed a recovery. The journal shows it as a
`snapshot_discarded` followed by a `snapshot_request` whose purpose is `HARD_RECOVERY`, and the
cost is one extra round trip of UNSYNCED time. If that sequence appears with the book UNSYNCED
for more than a second or two, the recovery request is not being issued and that is a defect.
