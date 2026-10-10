# H-V2 D7 Operations Activation V1

Strategy H's forward shadow launched on 2026-10-04 and then stopped moving. Its research is
finished, its launch is valid, its decisions are frozen, and its price store ends at 2026-10-02 -
the session that was the launch baseline. This step changes no strategy semantics. It connects the
updater that already exists to the schedule A and E already run on, and collects the sessions that
were missed in between.

Status of this document: sections A-C, E-G and J were established before any production change.
Sections D, H, I and K record the activation itself and are marked with what had and had not been
applied when they were written.

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

### D.2 Execution

*(Recorded at activation - see section K for whether it had been applied.)*

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

## H. UI/API Smoke

*(Recorded at activation.)*

## I. Timer Verification

*(Recorded at activation.)*

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

*(Recorded at activation.)*
