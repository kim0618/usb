# Context Collector V1.1 - retention amendment: active session is never pruned

    identity: ctx-retention.v1.1 (amends ctx-retention.v1, RETENTION_CTX_V1_1.md sha256 bbca1f65...)
    implementation: backend/app/crypto/context_collector_v1/retention.py
    frozen: 2026-10-11, before the fixed planner was run on any fixture or on the production journal

This amendment changes one rule of `RETENTION_CTX_V1_1.md` and nothing else. The retention period
(7 days), the two-clock rule, the root check, the dry-run default and everything section 4 of
that contract never deletes stay exactly as they are. No collector code, record shape, threshold,
`lm-wall.v2`, `lm-continuity.v5`, journal writer or unit file changes.

---

## 1. Defect being fixed

`ctx-retention.v1` protects only the `session` header of the session the writer lock names
(`retention.py`, the `kind == SESSION_KIND and session8 == protected_session8` test). Every other
sealed file of the running session (`context`, `wall_v2`, `wall_r0`) is aged like any other file.
Once a collector session has run for more than 7 days, its first hourly segments become deletion
candidates while the session is still writing.

A reader that rebuilds a session from its first segment then cannot: the resting wall set at the
session start lives only in those segments. The Q123 forward recorder refuses such a session
(`stream.UnrecoverableStart`: "starts at segment N; the resting set before it is unknown"), so a
lost checkpoint/cursor after day 7 would make full reconstruction impossible.

## 2. Active session: identity

The **active session** is the session id named by the writer lock `<root>/.writer.lock`, the JSON
the V0 `WriterLock.acquire` writes (`session_id`), cut to the 8 characters that appear in file
names. This is the same source `ctx-retention.v1` already uses to protect the session header. No
new state file is created.

It names the running session while a collector runs, and the last session after the collector
stops, until a new collector takes the lock with a new session. A stopped collector's last
session is therefore kept until the next session starts. That is deliberate: it is the session a
restart-free reader would still be reconstructing.

Corroboration: when the compact state file `<root>/state/collector_state.json` is readable and
names a `session.session_id`, its first 8 characters must equal the lock's. The state file is
only read, never written or locked.

## 3. Fail closed

If the active session cannot be established, **nothing is deleted** in that run (the full
candidate list is reported as kept under `identity_unknown`). This is the case when:

* the lock file is missing, unreadable, not JSON, or names no `session_id`;
* the lock's session id does not yield 8 lowercase hex characters;
* the state file names a different session than the lock.

Whether a process is alive is **not** part of the rule. A live process does not make every file
active, and a dead one does not make its last session closed.

## 4. Rule

For every file that `ctx-retention.v1` would list as a candidate:

* if its kind is one of `session`, `context`, `wall_v2`, `wall_r0` **and** the session8 in its
  name is the active session's: **KEEP**, at any age (`kept.active_session`);
* otherwise: the existing rule, unchanged (both clocks older than the 7-day cut-off).

Files never listed by `ctx-retention.v1` (`.jsonl.open` being written, `.tmp`, the state cache,
the lock, links, foreign names, kinds outside the retained set) stay never listed.

### Why these four kinds and not `telemetry` / `storage_stats`

Reconstruction readers read only these: `reader.seconds` merges `context`, `wall_v2`, `wall_r0`
of a session; the Q123 stream reads `context` and `wall_r0` from segment 1; the viewer describes
the running collector from its `session` header. No reader rebuilds anything from `telemetry` or
`storage_stats`; they are operating measurements and keep the 7-day rule, so the amendment does
not grow beyond what reconstruction needs.

## 5. Closed sessions

A file whose session8 is not the active session's is a closed-session file and is aged exactly as
before: deleted iff its seal mtime is older than `now - 7 days` and the UTC date in its name is
earlier than the cut-off's UTC date.

## 6. Storage consequence (intended)

Active-session files are no longer bounded by 7 days. Disk for the active session grows by the
measured daily rate of its four kinds for as long as the session lasts. This is an intentional
increase in exchange for recoverability, not a regression. It ends at the next collector start,
when the previous session becomes closed and is aged out by the 7-day rule.

## 7. Required evidence before production

1. active session at 8 and 30 days: 0 candidates; closed session at 8 days: candidate; at 6 days:
   kept; file being written: kept; active `wall_r0`/`context`/`wall_v2`: kept; unknown identity:
   0 candidates; closed old `wall_r0`: candidate;
2. non-collector roots (crypto-paper run, liqfwd, any other runtime path): refused, exit 2;
3. dry-run list == apply list; second apply deletes nothing;
4. Q123 recorder on an active session older than 8 days, pruned with this planner: incremental
   from checkpoint, full rebuild without checkpoint, rebuild with a corrupt cursor;
5. production dry-run against the live journal: 0 active-session candidates, read-only.
