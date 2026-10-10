# H-V2 D7 Operations Activation V1

Strategy H's forward shadow launched on 2026-10-04 and then stopped moving. Its research is
finished, its launch is valid, its decisions are frozen, and its price store ends at 2026-10-02 -
the session that was the launch baseline. This step changes no strategy semantics. It connects the
updater that already exists to the schedule A and E already run on, and collects the sessions that
were missed in between.

**Result: FAILED, H-only rollback applied (2026-10-10 18:01-18:0x KST).** The price catch-up
succeeded and is kept; the timer was removed because the production host has no SEC submissions
cache, so every scheduled run would rewrite the refresh queue as a false `NO_SUBMISSIONS_CACHE`.
Sections A-C, E-G and J were written before the production change; D.2, H, I and K record what
actually happened. Section B.2 is the cause of the failure.

---

## A. Audit Finding

The read-only audit that preceded this step is the authority for the starting state:

```text
H-V2 CURRENT HEALTH   = PARTIALLY RUNNING
Strategy H build      = COMPLETE
D7 launch             = VALID        (2026-10-04T08:34:30Z, baseline 2026-10-02)
production            = DEPLOYED     (server /root/usb at the same HEAD as origin/main)
APPROVE 0 / WATCH 6 / REJECT 2
launch snapshots      = 8/8
forward ledger        = 8 LAUNCH_STATE rows, verify PASS
H D7 price store      = 2026-10-02 only / STALE
scheduler H           = NOT_RUNNING
1D                    = 0/8 matured
5D                    = PENDING
AEYE                  = REFRESH_DUE / not processed
sizing                = NOT_DEFINED / fail-closed
```

Reconfirmed independently here, and two of the audit's figures are worth restating precisely:

* the local and the server H store are **byte-identical** - all 14 files, same sha256 - so there is
  one state to catch up, not two that have to be reconciled;
* `refresh_queue.json` carries `generated_at = 2026-10-04T08:49:48Z`, fifteen minutes after the
  launch timestamp. Both numbers are from the one manual launch session. Nothing has run since.

## B. Root Cause

**There was no scheduler. That is the whole of it.**

`python -m app.dev.run_h_v2_d7 update` was only ever a hand-run command. It was run once, during the
launch session on 2026-10-04, when the newest settled session was 2026-10-02 (2026-10-03 and -04
were a weekend). It has not been run since, so the store still ends there.

Everything downstream of that follows mechanically, and none of it is a second bug:

* **1D = 0/8 is not a maturation bug.** 1D matures on 2026-10-05, and the store does not hold
  2026-10-05. `outcomes` reports `PENDING` and refuses to substitute the last available price, which
  is the behaviour the forward contract exists to guarantee. The audit was right that the *source*
  data exists - it is in Strategy A's grouped store, collected 2026-10-06 13:40 KST - but H keeps its
  own store and fetches it with its own Massive call, so A having a session does not give it to H.
* **5D = PENDING was correct at audit time.** 5D matures on 2026-10-09, which a provider publishes
  after midnight ET on 2026-10-10.
* **The refresh queue is launch-time only** because `refresh_scan()` runs inside `update()`.

So one missing timer explains the stale store, the unmatured 1D and the frozen queue together.

### B.1 The trap found while writing the unit

The updater resolves H's append-only store from the module's own path
(`store.REPO_ROOT = parents[4]`), so the store is correct from any working directory. But
`refresh_scan()` reaches the SEC submissions caches through **repository-relative** paths:

```text
data/runtime/strategy_h_v2/d1_1/sec_raw
data/runtime/strategy_h/h_pv2c/sec_raw
data/runtime/strategy_c/e0/raw
data/runtime/strategy_h/h0_5/sec_raw
```

Measured both ways, same CIK:

| working directory  | AEYE submission rows read | source |
|---|---|---|
| `/root/usb`         | 688 | `data/runtime/strategy_h_v2/d1_1/sec_raw/.../CIK0001362190.json.gz` |
| `/root/usb/backend` | 0   | `None` |

A unit copying `usb-grouped-daily.service`'s `WorkingDirectory=/root/usb/backend` would therefore
exit 0 while rewriting all eight issuers as `NO_SUBMISSIONS_CACHE` - turning AEYE's real
`REFRESH_DUE` into a false "no cache", and the seven qualified `..._AS_OF_CACHE` states into the
same. The queue would look answered when it had been erased. `WorkingDirectory=/root/usb` is
therefore load-bearing, and
`backend/tests/strategy_h_v2/forward/test_d7_operations_schedule.py` holds it there.

### B.2 What B.1 did not check, and what failed in production

B.1 was measured **on the development machine only**. The production host was never asked whether
the caches exist at all. They do not: on `/root/usb` all four submission roots are missing, and
`data/runtime/strategy_h_v2/` holds nothing but `d7/` (locally the same roots hold 773 MB + 68 MB).

So the launch and its refresh scan ran locally and only `d7/` was copied to the server. The valid
queue production served from 2026-10-04 was a **copy**, byte-identical to the local one - which is
exactly why the audit's local/server equivalence check passed and hid that production cannot
regenerate it. The first scan actually run on the server (the catch-up, cwd `/root/usb`, correct)
wrote all eight issuers as `NO_SUBMISSIONS_CACHE` and an empty `refresh_due`, erasing AEYE's real
`REFRESH_DUE`. The working-directory fix was right and insufficient: the right directory with no
data in it fails the same way as the wrong directory.

## C. Scheduler Design

Two files, in the pattern the repository already uses, with no new framework:

```text
deploy/systemd/usb-h-forward.service    Type=oneshot, ExecStart ... run_h_v2_d7 update
deploy/systemd/usb-h-forward.timer      OnCalendar=Mon..Fri 01:10 America/New_York
```

**Schedule.** 01:10 ET, Mon..Fri. Chosen against measured evidence rather than a remembered hour:

* a session is published after midnight ET on its next calendar day. Corroborated here: the grouped
  store's files for 2026-10-05..08 were each written at 00:40 ET the following morning, so by 01:10
  ET the session is certainly there;
* the Massive key is shared and the Basic plan allows 5 calls per minute, while each client paces
  only itself. `usb-e-universe` runs 00:20 ET, `usb-grouped-daily` 00:40 ET, `usb-splits-refresh`
  00:55 ET. 01:10 ET is 15 minutes behind the last of the three, so the key is idle;
* 01:10 ET is 2h35m ahead of the 03:45 ET paper start that attaches A;
* the host runs KST, and the updater's upper bound is `date.today()` in **host-local** time. 01:10 ET
  is 14:10 KST the *same* calendar day, so the bound cannot run a day ahead of the sessions being
  asked for. Verified with `systemd-analyze calendar`: next elapse Mon 2026-10-12 14:10 KST.
  No KST hour is written in the unit; `America/New_York` keeps it DST-safe;
* `Mon..Fri` matches the grouped-daily cadence H shadows. A Friday session is collected Monday; a
  holiday Monday re-asks for Friday as a no-op; weekends make no external call.

**Ordering.** `After=usb-grouped-daily.service`, and deliberately **no** `Wants=`. H does not read
A's grouped store - it makes its own `grouped_daily` call - so there is no data dependency to
express, and `After=` only matters in the unlikely case the two are queued in one transaction.
`Wants=` would be actively wrong: it would make an H run execute a Strategy A collection, which is a
side effect on A that H is not permitted to have.

**A grouped-daily failure cannot give H a wrong price.** The two do not share a store. And on H's own
side a failed fetch stores nothing: the session stays a gap, and `outcomes` reports `PENDING` or
`INCOMPLETE` across a gap rather than substituting a price. Fail-closed by construction.

**Bounds, measured not guessed.** One update peaks at 132 MB resident and 0.92 s wall;
`MemoryMax=300M` matches grouped-daily with better than 2x headroom on a host that has been OOM-hit
before. `TimeoutStartSec=20min` covers flock's wait plus a full provider backoff.

**Idempotency** is already the updater's own property, so nothing was reimplemented:

| risk | what prevents it |
|---|---|
| duplicate ledger row | `update()` never appends to the ledger; only `launch()` does |
| duplicate outcome | outcomes are derived on read, never stored |
| launch snapshot overwrite | `store.append` raises `AppendOnlyViolation` on a repeated identity |
| price session rewritten | `write_session` compares bodies and raises on a differing rewrite |
| thesis version change | requires a D3->D6 re-evaluation, which `update()` cannot perform |
| concurrent writers | systemd never overlaps a oneshot with itself; `flock -w 300` on `/run/usb-h-forward.lock` also covers a hand-run colliding with a timer firing. The lock file is never unlinked, because flock locks an inode and not a path |

`refresh_queue.json` is a regenerated view, not a ledger: a second run rewrites it wholesale and only
`generated_at` differs. That is the intended behaviour.

**Failure isolation.** `Type=oneshot`, no `Restart=`, `StartLimitIntervalSec=1800` /
`StartLimitBurst=3` so a once-a-day job cannot crash-loop between timers. Nothing in A, E,
`usb-backend` or `usb-frontend` depends on this unit, so an H failure is one visible H failure. The
CLI prints the whole run as JSON on stdout, which is what lands in the journal - start, finish,
sessions written, maturity counts, refresh-due list - so no logging framework was added.

## D. Catch-up

### D.1 Dry audit, before any fetch

```text
H latest stored session   = 2026-10-02
expected (2026-09-16, today]
  = 09-17 09-18 09-21 09-22 09-23 09-24 09-25 09-28 09-29 09-30 10-01 10-02
    10-05 10-06 10-07 10-08 10-09
GAPS = 2026-10-05, 2026-10-06, 2026-10-07, 2026-10-08, 2026-10-09   (5 sessions)
```

Source availability, read out of Strategy A's grouped store rather than assumed - all nine symbols
(the eight-issuer cohort plus the SPY benchmark) present in every one:

| session | grouped file written | cohort symbols found |
|---|---|---|
| 2026-10-05 | 2026-10-06 13:40 KST | 9/9 |
| 2026-10-06 | 2026-10-07 13:40 KST | 9/9 |
| 2026-10-07 | 2026-10-08 13:40 KST | 9/9 |
| 2026-10-08 | 2026-10-09 13:40 KST | 9/9 |
| 2026-10-09 | not yet collected - publishes after 00:00 ET 2026-10-10 | - |

Cross-source check on the one session both stores already hold: H's stored 2026-10-02 closes equal
the grouped store's for all nine symbols exactly (AEYE 6.92, COLL 22.02, DORM 123.06, FG 21.92,
IDCC 335.13, SCCO 205.54, SPY 769.64, TG 7.03, VRRM 2.81). The two sources are the same endpoint,
so the catch-up's numbers are predictable in advance and were not substituted by hand.

One expected difference, not a defect: sessions collected *before* launch carry 14 symbols (the
wider D4 universe, which is what `cohort_symbols()` falls back to before a launch exists), and
sessions collected after it carry 9 (the launch cohort plus SPY). Outcomes read only the cohort and
the benchmark.

### D.2 Execution (2026-10-10, applied)

Pre-change state re-recorded first, because the server had moved since the plan was approved: HEAD
`98feb31` -> `db41f04` (another session's Strategy A deploy, server-local, not on origin) and
`usb-backend`/`usb-frontend` had been restarted by it. That commit touches no H module, no Massive
client and no calendar; the 50 `app.*` modules the update path loads intersect its changed modules
in nothing; the H store was still byte-identical to local (aggregate `55de6fbb...`).

Backup of the whole H store first: `/root/h_d7_backup_pre_activation_20261010-180116.tar.gz`.
Units installed (sha256 `1b43b52f...` service, `111678d0...` timer, equal to local commit `75c42c0`),
`daemon-reload`, `enable --now usb-h-forward.timer`. The timer did not fire on enable.

The catch-up was run once as `systemctl start usb-h-forward.service`: the same ExecStart as the
approved command (same flock, interpreter and arguments, cwd `/root/usb`), with `.env` and
`.env.massive` loaded through the unit's own EnvironmentFile contract, and with the journal and
exit status as evidence. 18:01:50 -> 18:02:47 KST, `Result=success`, exit 0, 2.13 s CPU.

```text
Massive grouped_daily calls  5
requested   2026-10-05 2026-10-06 2026-10-07 2026-10-08 2026-10-09
written     2026-10-05 2026-10-06 2026-10-07 2026-10-08 2026-10-09
no_result   []      failed  {}
price sessions  12 (09-17..10-02)  ->  17 (09-17..10-09); 6 on or after the baseline
```

| session | symbols | SPY close | vs A's grouped store (close/high/low/volume) |
|---|---|---|---|
| 2026-10-05 | 9 | 774.83 | identical, 9/9 |
| 2026-10-06 | 9 | 779.09 | identical, 9/9 |
| 2026-10-07 | 9 | 777.22 | identical, 9/9 |
| 2026-10-08 | 9 | 773.93 | identical, 9/9 |
| 2026-10-09 | 9 | 778.57 | A collects it Monday; H fetched it directly |

Nothing was entered by hand. `launch_snapshot.jsonl` and `forward_ledger.jsonl` kept their sha256
(`dba04d4b...`, `6cd6009d...`), the ledger stayed at 8 rows, `verify` returned `problems = []`.

## E. Outcome Maturation

Horizon endpoints, by `MarketCalendar` from the 2026-10-02 baseline:

| horizon | maturity session | status before catch-up |
|---|---|---|
| 1D  | 2026-10-05 | PENDING - endpoint not stored |
| 5D  | 2026-10-09 | PENDING - endpoint not published at audit time |
| 21D | 2026-11-02 | PENDING - correctly in the future |
| 63D | 2027-01-04 | PENDING - correctly in the future |

1D is the one that was wrong only in the sense that its endpoint existed at source and nobody
fetched it. 21D and 63D will not mature before 2026-11-02 and 2027-01-04 and no amount of catch-up
changes that.

5D is left to mature on its own. If 2026-10-09 is not in the source store yet, `5D = PENDING` is the
correct answer and the next update collects it. Forcing the latest available price into the endpoint
is the single easiest way to turn a forward observation into a backtest, and `outcomes` does not
implement it at all.

## F. Refresh Queue

`update()` calls `refresh_scan()`, so the queue is regenerated on every run once H is launched. The
scan reads only the local SEC caches and never fetches - the acquisition layer
(`app.dev.acquire_strategy_h_v2_fundamentals`) owns fetching - and it is careful about what that
licenses it to claim:

* `REFRESH_DUE` - a cached filing is newer than the thesis's decision session;
* `NO_NEW_MATERIAL_EVIDENCE_AS_OF_CACHE` - nothing new *as of the cache date*, a weaker claim than
  "nothing new", which is why every row carries `evidence_known_through`;
* `NO_NEW_MATERIAL_EVIDENCE` - only when the cache was refreshed today.

State at audit time, which a schedule does not change because it is a function of cache **content**,
not of when the scan ran:

```text
AEYE  REFRESH_DUE                          cache 2026-09-28, 8-K 2026-09-18, 1 new filing
COLL DORM FG IDCC SCCO TG VRRM             NO_NEW_MATERIAL_EVIDENCE_AS_OF_CACHE
```

**Detecting genuinely new SEC material is outside `update()`'s range.** `update()` regenerates the
queue from caches dated 2026-09-21 and 2026-09-28; it cannot see a filing made since. Making the
queue current would require an acquisition run, which is a separate step and was not added here.
Stated rather than papered over: this schedule keeps the queue *regenerated*, not *current*.

## G. A/E Isolation

H writes only under `data/runtime/strategy_h_v2/d7/` and reads A's and E's records nowhere.
Server-authoritative A/E state was captured before any change (section H has the comparison):

```text
A  session 2026-10-09  equity 7509.398546173644750679827318  initial 7428.92
   total_pnl 80.478546173644750679827318  open 0  trades 3
E  session 2026-10-09  equity 10190.38592  initial 10000  total_pnl 190.38592
   open 0  trades 28  closed_today 3  last_signal 2026-10-09T09:30:12-04:00
```

Local regression, with no Python source changed at all:

* `tests/strategy_h_v2/forward` - 35 passed before, 47 after (12 new unit/timer tests);
* `tests/strategy_h_v2` - 27 failed / 1585 passed / 138 skipped before, 27 failed / 1597 passed /
  138 skipped after. The 27 are a pre-existing baseline: they assert frozen research samples
  regenerate from universe runtime data that is gitignored and absent locally
  (`regenerate_sample_from_universe()` returns empty). None is in `forward/`, and none is touched by
  this step;
* `tests/strategy_e_max` - 383 passed, the A/E shared read layer.

`systemd-analyze verify` on both new units: exit 0.

### E.1 After the catch-up

1D matured 8/8 on 2026-10-05 and 5D 8/8 on 2026-10-09 (published by then: the run was at 05:01 ET
on 2026-10-10). 21D and 63D are PENDING. Recorded as observations; nothing here feeds any decision.

| ticker | decision | 1D security | 1D SPY | 1D excess | 5D security | 5D SPY | 5D excess |
|---|---|---|---|---|---|---|---|
| COLL | WATCH  | -0.681% | +0.674% | -1.356% | +0.318%  | +1.160% | -0.842%  |
| DORM | WATCH  | +0.309% | +0.674% | -0.366% | -2.162%  | +1.160% | -3.322%  |
| FG   | WATCH  | +0.411% | +0.674% | -0.264% | -12.135% | +1.160% | -13.295% |
| SCCO | WATCH  | -0.122% | +0.674% | -0.796% | +1.786%  | +1.160% | +0.625%  |
| TG   | WATCH  | -0.427% | +0.674% | -1.101% | +0.000%  | +1.160% | -1.160%  |
| VRRM | WATCH  | +2.135% | +0.674% | +1.461% | +4.626%  | +1.160% | +3.466%  |
| AEYE | REJECT | -2.746% | +0.674% | -3.420% | +0.145%  | +1.160% | -1.016%  |
| IDCC | REJECT | +0.913% | +0.674% | +0.239% | -1.140%  | +1.160% | -2.300%  |

Decisions: APPROVE 0 / WATCH 6 / REJECT 2, `decisions_changed = 0`. VRRM: window `RECENT_6M`, frozen
`upside_to_tp1` +24.80% (snapshot unchanged), Bear N/A (`NEGATIVE_IMPLIED_EQUITY`); `tp1_distance`
moved +57.2% -> +50.3% with the price 2.81 -> 2.94, as J.3 said it would.

## H. UI/API Smoke

API on the production backend, after the rollback: `/api/v1/strategies`, `/cards`, `/performance`,
`/STRATEGY_H_V2/forward`, `/STRATEGY_H_V2/forward/VRRM`, `/STRATEGY_H_V2/status` all HTTP 200. H
reports `session 2026-10-09`, `price_sessions_observed 6`, `FORWARD_SHADOW_RUNNING`.
`last_update` still reads 2026-10-04T08:34:30Z: it is the newest *decision* time, not a price
time, so a price catch-up correctly leaves it alone.

UI `/dashboard`, `/strategy-h`, `/strategy-compare`: HTTP 200. **Not rendered in a browser**, so the
selector and the per-horizon display were not visually confirmed.

A/E, compared with the values recorded before the change (trades hashed as well as counted):

```text
A  session 2026-10-09  equity 7509.398546173644750679827318  init 7428.92  pnl 80.4785...  open 0  trades 3   sha b218acca  unchanged
E  session 2026-10-09  equity 10190.38592  init 10000  pnl 190.38592  open 0  trades 28  sha 30a75b11  unchanged
```

MainPIDs of usb-backend, usb-frontend, usb-crypto-paper, usb-crypto-liqfwd and usb-e-rvol identical
before and after; all 20 other usb unit files identical by sha256; server HEAD and dirty set
unchanged. No other unit was started, stopped or restarted.

## I. Timer Verification

While installed: `enabled`, `active (waiting)`, trigger Mon 2026-10-12 14:10 KST, no last trigger,
service `static`/inactive, enable symlink in `timers.target.wants` (the reboot-survival mechanism).
The one service run: `Result=success`, exit 0, journal shows Starting / Finished / Deactivated
successfully and the full JSON report.

**Then removed** (section K). Now: no unit file, no wants symlink, absent from `list-timers`.

## J. Remaining Limitations

1. **Sizing is still `NOT_DEFINED`.** Unchanged by design. `APPROVE = 0`, so it is not a launch
   blocker; the first APPROVE needs a frozen sizing contract written as its own step, and until then
   an APPROVE is fail-closed rather than a position.
2. **The refresh queue is regenerated, not current** (section F). AEYE stays `REFRESH_DUE / awaiting
   research approval`; no D3->D6 re-evaluation was run.
3. **Two upside numbers, two denominators.** VRRM's frozen `upside_to_tp1 = +24.80%` is measured
   against the decision-session close (3.54) and is immutable in the launch snapshot.
   `tp1_distance` is measured against the *current* price and **moves with every price update** -
   +57.2% at the 2.81 close. A changed `tp1_distance` after a catch-up is the correct behaviour, not
   thesis drift.
4. **Mon..Fri leaves a weekend gap in the store**, by choice. Friday's session is collected Monday.
   The intervening state is an honest gap, not a substituted price.
5. **Horizon endpoints of 21D and 63D are months out** (2026-11-02, 2027-01-04). A timer does not
   accelerate them, and the pre-registered sample is unreached until they land.

## K. Final Health

```text
H-V2 D7 OPERATIONS = FAILED, H-only rollback applied
                     (price/outcome catch-up succeeded and is kept)
```

Rollback, as approved: `disable --now` the timer, remove both unit files, `daemon-reload`. In
addition, `refresh_queue.json` alone was restored from the pre-activation backup (sha256
`43123c04...`, identical to before) because the degraded version was a false statement being served;
the degraded copy is kept at `/root/h_d7_refresh_queue_DEGRADED_20261010-1802.json`. The five price
sessions were **not** rolled back: they are correct, append-only, verified field by field against
an independent store, and removing them would only re-hide 1D/5D results that exist.

To activate for real, production needs the SEC submissions for the eight cohort CIKs at a path the
scan reads, which is a data deployment or a code change - neither was in this approval:

1. copy the eight `CIK*.json.gz` submission files (raw, read-only, no fetch) into one of the
   `SUBMISSION_ROOTS` on the server, then reinstall the same two units unchanged; or
2. make `refresh_scan` fail closed when no cache root exists (refuse to rewrite the queue rather
   than write `NO_SUBMISSIONS_CACHE`), which would let prices run daily now while the queue waits.

Either way the queue would only be *regenerated*, not *current* (section F).
