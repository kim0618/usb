# Context Collector V1.1 - local retention contract

    identity: ctx-retention.v1
    applies to: ctx-collector.v1.1 (CONTRACT_CTX_V1_1.md, sha256 a9f8becf...)
    implementation: backend/app/crypto/context_collector_v1/retention.py
    frozen: 2026-10-10, before the first production start

This contract fixes how long a production context collector keeps its persistent journal on the
host, and what a deletion may touch. It adds nothing to `CONTRACT_CTX_V1_1.md` and changes no
record shape, threshold or value.

---

## 1. Retention period

**7 days.**

| Basis | Value |
|---|---|
| Measured disk occupancy (V1.1 report L, tmpfs cache) | 55.3 MB/day |
| 7 days | about 387 MB |
| 14 days (not chosen) | about 774 MB |
| Plus the hour being written (plain, before seal and gzip) | up to about 24 MB |

14 days is not used for now because the production host's free disk was last measured at about
1.1 GB, and 774 MB of it for one read-only collector leaves too little for the trading services.
The period can be raised later by changing `CTX_V1_RETENTION_DAYS`; doing so is a new decision,
not an edit of this contract.

A file is deleted only when both its seal time and the UTC date in its name are past the cut-off
(section 3), so a file is kept for at least 7 days and at most about 8. The planning bound is
therefore 8 x 55.3 = **about 443 MB**, plus the open hour.

## 2. What may be deleted

Only **sealed persistent context files**: regular files directly inside `<root>/<kind>/` for
`kind` in `session`, `telemetry`, `storage_stats`, `context`, `wall_v2`, `wall_r0`, whose name is
exactly `<kind>-YYYYMMDD-<session8>-NNNNN.jsonl` or `.jsonl.gz`.

## 3. When

A candidate is deleted iff **both**

* its modification time (the moment it was sealed) is older than `now - 7 days`, and
* the `YYYYMMDD` in its name (UTC date it was opened) is earlier than the UTC date of that cut-off.

Two clocks that must agree: a wrong clock keeps a file, never loses one.

## 4. What is never deleted

* a file still being written (`*.jsonl.open`) or a compression temporary (`*.tmp`);
* the current-state cache: `<root>/state` (a link to tmpfs) and anything a previous start moved
  aside as `state.disk-*`;
* the writer lock `<root>/.writer.lock`;
* a symbolic link, a directory, or a file whose name does not match section 2;
* any `session` file of the session the writer lock names (the running collector's header);
* anything outside `<root>`: no ledger, tape, run directory, paper or live data, frontend, nginx
  or other service path is ever listed. Retention does not know those paths exist.

A path that does not have both `<root>/.writer.lock` and `<root>/context/` is refused before
anything is listed (exit 2).

## 5. Isolation from trading

* Retention is its own short process (`prune`), started by its own oneshot unit and daily timer.
  It is never run inside the collector, takes no lock the collector uses, and opens no socket.
* No unit depends on it. A failure (exit 1 for a file that could not be removed, exit 2 for a
  refused root) leaves old files on disk and marks only the retention unit failed.
* It runs at `Nice=19`, `IOSchedulingClass=idle`, `MemoryMax=100M`.

## 6. Default is a dry run

`prune` without `--apply` lists what it would delete and deletes nothing. The timer unit passes
`--apply`. A hand-run on the host should start with the dry run.

## 7. Not decided here

* Off-host archiving of files before deletion: none. A deleted file is gone.
* A wall that stays open longer than the retention period loses its `OPEN` row with the hour that
  held it; a reader that starts inside the retained window sees it from its next `CHANGE`/`CLOSE`.
  Measured walls do not live that long, so this is recorded as a limit, not handled.
