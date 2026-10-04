# Liquidity Map V1.2 follow-up gate: writer collision, continuity decisions, volatile book

Date: 2026-10-04 (KST).
Continuity rule **`lm-continuity.v2`**, sha256 `596339b66cc170b4bd1550b93e44f63e35f81c13e28f833dc9a446e67b691576`,
superseding `lm-continuity.v1` (`d68a26ce…`).
`lm-wall.v2` unchanged (`deaa9db8…`). `btc-ms.v0.1` unchanged (`9eed3862…`).

**Status: isolated preview only. Not deployed, not committed, not linked from navigation.**
No direction, no rating, no order path, no AUTO change.

---

## A. Writer collision: root cause

Reproduced in three cases before anything was changed, so the cause is measured rather than
argued. `scratchpad/repro.py`, against the shipped `Store`:

| case | second writer | first writer's `close()` | files left in the root |
|---|---|---|---|
| lock file intact | **refused** (`StoreLocked`) | clean | one session's |
| lock file unlinked | **acquired** | `FileNotFoundError` from `_seal` | **both sessions'** |
| whole root `rm -rf`'d | **acquired** | `FileNotFoundError` from `_seal` | second session's only |

### The mechanism

**`flock` locks an inode, not a path.** While `<root>/.writer.lock` exists, a second `Store.open`
is refused and always was. Remove that file, on its own or with the directory around it, and the
next `open(..., O_CREAT)` creates a **different inode** which locks cleanly. Both processes then
hold a valid exclusive lock, on two different files, and neither can tell from `flock` alone.

The damage then follows in order, and every step of it is in the shipped code doing what it was
told:

1. the second writer's `recover_orphans` finds the first writer's live `.open` files and
   **renames them**, because from its side they are orphans left by a dead process;
2. the first writer keeps appending through file descriptors that now point at renamed or
   unlinked inodes, so its rows land in a file the sealed name no longer describes;
3. at exit the first writer's `_seal` calls `os.replace` on a path that no longer exists and
   raises `FileNotFoundError` **from the shutdown path of an otherwise complete run**;
4. any file the first writer opened *after* the removal is left `.open` in the recreated
   directory, which is the stray `storage_stats` file with a foreign session id that started
   this audit.

### What actually triggered it

Not the collector. The measurement harness was launched twice against one root:

```
cd backend && SP=/tmp/.../lmv12 && rm -rf $SP/data && ... setsid nohup python drive.py & echo $! > $SP/drive.pid
```

Everything before the `&` runs in a **background subshell**, so `SP` was set there and nowhere
else. The foreground `echo` ran with `SP` empty, failed with `/drive.pid: Permission denied`, and
the visible output said `cat: /drive.log: No such file or directory` - which reads exactly like
"nothing started". The background job had in fact started correctly. The second launch then ran
`rm -rf $SP/data` **on a live root**, and both collectors wrote into it for the next three
minutes. One log file, opened `>` by both, is why the log showed a single `START`, the second
process's `END`, and the first process's traceback.

So: an operator mistake rather than a collector bug, and a contract that could not survive it.

---

## B. The fix

The contract asked for is **one authority writer per `MS_V0_ROOT`, second writer fails closed**.
It is enforced from two sides, and only one of the two can be complete. Both are stated here,
including the limit, because a guarantee with an unstated hole is worse than a smaller guarantee.

### The incumbent fails closed. This half is complete.

| change | what it does |
|---|---|
| `WriterLock` remembers `(st_dev, st_ino)` | the identity of the inode it actually locked |
| `WriterLock.verify()` | the path under the root must still resolve to that inode |
| `acquire()` re-checks after `flock` | a file replaced *between* `open` and `flock` becomes a refusal, not a second writer |
| `Store.check_authority()` on every flush tick, seal and state write | one `stat` a second; raises `StoreAuthorityLost`, and the loss is **sticky** |
| `Store.write_state()` refuses without authority | the one artefact that claims to be "the current state" is not published by a process that cannot prove it owns the root |
| `Store.close()` **abandons** instead of sealing | buffer dropped, `.open` left exactly as `recover_orphans` expects to find it; no `os.replace` onto a path somebody else may own, and no exception from a shutdown path |
| `Runner._sampler` catches it | sets `_stop`, and the run ends with a named `stop_reason` |

### The newcomer is refused on any evidence that survives. This half is a heuristic.

The lock file holds `{pid, session_id, started_ms, lock_version}`, so a refusal names the holder
instead of only reporting that somebody is there. And when the lock is *free* but the compact
state file is younger than 3,000 ms and names a live pid, the root is handed straight back:

```
StoreLocked: the writer lock for <root> was free, but a collector is still writing here
({'pid': 234075, 'session_id': 'bccd2769-…', 'state_age_ms': 1000, …}).
The lock file was removed or replaced under a running process.
```

**What it cannot catch, stated as a test rather than a footnote**
(`test_a_removed_root_is_the_case_the_newcomer_cannot_see`): a root that was removed outright
takes that evidence with it. The newcomer then has nothing left to read and opens the root, which
is correct - nothing distinguishes it from a fresh start. That case is covered only by the
incumbent stopping, which it now does within one flush tick.

The 3-second window is also what keeps a restart working: a killed collector's state file ages
out, and its pid is dead either way, so `kill -9` recovery is unaffected.

### Verified live, not only in tests

Real collector, real sockets, lock file removed under it at t+12 s:

* the newcomer was refused and **named the holder's pid and session**;
* the incumbent stopped at **t+22.06 s** (the shutdown sequence is ~10 s, mostly the websocket
  close handshake) and printed
  `stop_reason: writer_authority_lost: the writer lock under … is no longer the file this
  process locked`;
* it left **10 `.open` files**, which the next `Store.open` recovered;
* `dropped_records 0`, and the state file froze at the instant of the loss.

**One defect found in the fix itself, by that live run.** The first version only raised from
`Store.tick()`. All unit tests passed, but live the exception was swallowed by the sampler task:
the collector stopped *writing* (journal flat, state file frozen - so the data was safe) and did
not stop *running* for 20+ seconds, buffering into memory and reporting nothing. Catching it at
the call site and stopping the run is what made it a fail-closed rather than a quiet stall.

### Tests

`test_ms_v0_writer_collision.py`, 21 cases, covering every item the gate listed: A/B collectors
started together (including four real processes racing, exactly one winner), different session
ids on one root, different roots, `kill -9` and restart recovery, stale lock, stale state file,
dead pid, a session that recorded its own end, seal recovery and the `-r1` non-overwrite rule,
child/orphan processes not inheriting the lock, and the removed-root limit.
**Full regression: backend 448 passed, frontend 848 passed (52 files).**

---

## C. The continuity decisions, and the 300 ms result

All four decisions are now in the frozen rule, which moved to **`lm-continuity.v2`** with a
changelog recording what changed and why. A version that moved without saying what moved would
be a silent edit with extra steps.

| decision | state |
|---|---|
| journal recovery carry = forbidden | **written into the rule** as its own clause, and tested: a set rebuilt from `wall` transitions reports every wall `NEW` with `COLLECTOR_PUBLISHES_NO_LEDGER`. Rebuilding the carry from the `wall_continuity` telemetry stream is explicitly refused |
| session boundary = HARD | unchanged, already enforced and tested (`test_a_restart_cannot_inherit_a_carry_from_the_process_that_died`) |
| SOFT bounded window = 300 ms | `SOFT_WINDOW_MAX_MS` 1000 → **300** |
| `lm-wall.v2` thresholds | **unchanged**, hash re-verified `deaa9db8…` |
| display filter 500k | **unchanged** |

**300 ms against measured latency.** Every REST round trip observed so far: 87, 99, 102, 105,
105, 119, 143, 162 ms. All eight are inside 300 ms, so the tighter ceiling refuses nothing that
the measurements say should pass, and it removes the order of magnitude of slack v1 carried.

**Honestly: 300 ms is not yet exercised by a live SOFT carry.** The volatile run below produced
no voluntary install at all, so the only live evidence for the new ceiling is that the round
trips sit well under it. The earlier forced-refresh sessions, which did carry, ran at 87-143 ms
and would have passed the 300 ms gate unchanged.

---

## D. Volatile book: what the 23 minutes actually showed

A live collector, **no forced refreshes** - the point was to let the policy's own coverage-edge
trigger fire. Preview API alongside, sampled once a second from the collector's state file.

### The window

| | this run | V1.1 quiet baseline |
|---|---|---|
| duration | 23.0 min | 27 min |
| mid range | **10.64 bps** (85,048.35 - 85,138.95) | - |
| total path travelled | **52.9 bps** | 2.3 bps |
| per minute | **2.3 bps/min** | 0.085 bps/min |

About **27x** the movement of the quiet baseline. It is an honestly moving book, and it is
**not** a violent one: no funding print, no liquidation cascade, max per-second move 4.03 bps.
Call it *moving, not violent*, and read everything below with that bound.

### The headline, which is not about continuity

| | |
|---|---|
| coverage margin below the 1.0 bp trigger | **12.0%** of samples |
| coverage margin **negative** (the ±0.1% COMPLETE promise broken) | **3.7%** of samples - 50 consecutive seconds, worst **-0.72 bp** |
| coverage-edge refreshes requested | **2** |
| refreshes installed | **0** |
| refreshes rejected (`NOT_NEWER_THAN_LIVE_BOOK`) | **2** |
| generations (resyncs) | **1** - the startup snapshot, nothing else |
| SOFT / HARD transitions | **0 / 0** |
| walls carried / ended / unknown | **0 / 0 / 0** |

**The continuity rule never engaged, because the voluntary refresh never installed.** V1.2 is
untested in this window, and not because the carry failed: the transition it attaches to did not
happen. And the protection the refresh exists to provide did not happen either - the band the
preview is allowed to call COMPLETE left the snapshot's known interval for 50 seconds, and the
policy's response was requested twice and refused twice, with a 300 s cooldown burnt by each
failed attempt (the cooldown is set when a refresh is *requested*, not when one succeeds).

### Why the refusals happen, measured rather than assumed

The code comment says a refresh is refused when "the REST read is not newer than the deltas
already applied", which reads as *Binance served a stale book*. **That is wrong, and an
independent probe says so.** Ten REST reads interleaved with the live diff stream:

| | |
|---|---|
| reads where the snapshot was **newer** than the newest stream frame at request time | **10 / 10** |
| how old the snapshot's book was | **32 - 178 ms** |
| ids the snapshot was ahead by, at request | 2,176 - 21,682 |

So the snapshot is ahead when it is asked for. The refusal happens because `_refresh_refusal`
compares it against the live book **at install time**, after the stream has advanced for the
whole round trip. In the two rejections the live book was ahead of the arriving snapshot by
**1,695** and **1,789** ids after round trips of **105 ms** - entirely accounted for by the
stream advancing during the read.

The refusal itself is correct: installing a snapshot behind the applied deltas would move
`last_update_id` backwards, fail the first-delta rule on the next frame, and cost a gap, a resync
and every wall candidate. What is wrong is that the voluntary refresh has **no way to use a
snapshot it just raced past**, so on a busy book it is a race it mostly loses.

**The fix exists and is the collector's own initial-sync procedure**: buffer the diff stream
during the REST read, install the snapshot, then replay the buffered frames with
`u > lastUpdateId`, which is exactly what `apply_snapshot` already does at startup. The voluntary
path cannot do it today because the book stays `SYNCED` during the read and frames are applied
directly instead of buffered. That is a redesign of the refresh path, it is **out of this gate's
scope**, and it is the single most valuable next piece of work here.

### Health over the same 23 minutes

| | |
|---|---|
| gaps / reconnects / crossed / overflows | **0 / 0 / 0 / 0** |
| dropped records | **0** |
| persistence queue backlog max | **304** of 8,192 |
| RSS | 39.6 → 41.5 MiB (**+1.0 MiB** over 23 min) |
| depth state | `SYNCED` in **1343 / 1343** samples |
| depth age | median 52 ms, max 425 ms |
| preview API latency | median **13.5 ms**, p90 17.7 ms, max 47.8 ms, **0 errors** |
| preview API body | ~47.8 KB |
| state file writes | 1,320, **0 failed** |
| active candidates | 254 - 308, median 283 |
| journal rate | 4.79 GB/day projected (quiet-book V0 measurement was 4.51) |

Nothing in the resource picture is a concern. The queue, the memory and the API are all where
V1.1 measured them, under a book moving 27x faster.

---

## E. Production readiness

**No**, and the reason is now sharper and sits below V1.2 rather than inside it.

1. **The coverage-edge refresh does not fire on a moving book.** Two attempts, two refusals, and
   the ±0.1% COMPLETE promise broke for 50 seconds while the policy had nothing it could do. This
   is a **V1.1 defect that V1.2's work sits on top of**, and it makes the SOFT carry moot in
   exactly the regime where wall history matters most. The mechanism is understood and the fix is
   known. It needs its own gate.
2. **The safety refresh has still never fired on its own** (no uninterrupted hour), and no live
   HARD resync has ever been observed (**gaps 0** again here, as in every V0 run).
3. **300 ms is compatible with every latency measured but has not yet carried a wall live.**
4. **The carry rates of 86-98% remain quiet-book numbers.** This window produced no transition at
   all, so there is still no volatile-market carry rate.
5. The preview still renders inside the app shell, which a concurrent session is editing.

### Decisions for the user

* **A.** Redesign the voluntary refresh to buffer-and-replay (the known fix in D) as its own
  gate, or accept that coverage-edge protection is best-effort on a busy book and say so on the
  screen.
* **B.** Whether a *failed* voluntary refresh should burn the 300 s cooldown. It does today, so
  two refusals cost ten minutes of unprotected band. Changing it is a policy change and was left
  alone here.
* **C.** Whether a longer or genuinely violent window (funding, cascade) is required before the
  carry rate is considered measured.
* **D.** V1.1's open decisions A (operational UI placement) and C (R3's 1 bp blind spot) are
  unaffected.
