# Context Collector V1.1 - active-session retention fix: report

    contract: RETENTION_ACTIVE_SESSION_FIX_V1.md (sha256 72313e93...), frozen before any result
    date: 2026-10-11 KST
    verdict: READY_FOR_RETENTION_FIX_PRODUCTION_APPLY (not applied)

## 1. Root cause

`retention.py` (ctx-retention.v1), `plan()`:

* the kind loop covers `session`, `telemetry`, `storage_stats`, `context`, `wall_v2`, `wall_r0`;
* the only ownership test was `if kind == SESSION_KIND and session8 == protected_session8`, so
  only the running session's **header** was exempt;
* every other file of the running session fell through to the two-clock age test
  (`stat.st_mtime < cutoff_s and name_date < cutoff_date`) and became a candidate after 7 days.

The Q123 stream refuses a session whose first segment is missing (`UnrecoverableStart`), so after
day 7 a lost recorder checkpoint/cursor could not be recovered from the journal.

Production evidence (read-only, 2026-10-11 15:12 KST, session `31cc8177`, 17 sealed hours): the
installed planner, evaluated at now + 8 days, lists **85 active-session files (45.7 MB)**,
including `context`/`wall_v2`/`wall_r0` segment 00001.

## 2. Change

`retention.py` only. Active session = writer lock `session_id[:8]`, corroborated by the state
file when it names a session; unknown or contradictory identity deletes nothing. Active-session
files of `session`, `context`, `wall_v2`, `wall_r0` are kept at any age. All other rules unchanged.
Report gains `identity_failure`, `kept.active_session`, `kept.identity_unknown`; the
`kept.current_session_header` counter is replaced by `kept.active_session`. Version string
`ctx-retention.v1.1`. No collector, store, wall, flow, schema, unit or env change.

## 3. Tests

* `test_ctx_v1_retention.py`: 28 passed (11 of them fail on ctx-retention.v1).
* `test_ctx_v1_retention_q123_recovery.py`: 7 passed in a tree of Q123 `ebc78db` + this fix
  (skipped where the Q123 package is absent). Against v1 the planner lists 13 active-session files
  at 8.5 and 30 days in the same fixture; v1.1 lists 0, and only the 7 closed-session files.
* ctx + Q123 suites in that tree: 233 passed, 3 skipped.

## 4. Production dry-run (read-only, candidate module loaded from stdin, nothing written)

| planner | now + days | delete files | delete MB | of which rebuild sources of 31cc8177 |
|---|---|---|---|---|
| installed v1 | 0 | 0 | 0 | 0 |
| installed v1 | 8 / 14 / 30 | 85 | 45.7 | 51 |
| candidate v1.1 | 0 | 0 | 0 | 0 |
| candidate v1.1 | 8 / 14 / 30 | 34 | 10.7 | **0** (68 kept) |

The 34 candidates are the running session's `telemetry` and `storage_stats` (17 + 17), aged by
the unchanged 7-day rule (contract section 4). Lock and state both name `31cc8177`.

## 5. Storage

Measured on 16 sealed production hours (first, plain hour excluded): rebuild sources
(`context` 17.2 + `wall_v2` 5.1 + `wall_r0` 10.9 MB) = **49.8 MB/day**; `telemetry` +
`storage_stats` = **15.4 MB/day** (bounded at about 8 days = 123 MB).

| one session lasting | v1 (bounded) | v1.1 |
|---|---|---|
| 7 days | ~520 MB | ~460 MB |
| 14 days | ~520 MB | ~820 MB |
| 30 days | ~520 MB | **~1.62 GB** |

The growth is the intended price of recoverability and ends at the next collector start. Host
free disk on 2026-10-11 was 2.2 GB; a session past about 2 weeks needs a disk check.

## 6. Production apply plan (not executed)

Deadline: the installed v1 first deletes active-session files at the
**2026-10-19 04:40 KST** timer run (first segment named 20261010, cut-off date passes it then).

Only `retention.py` and the two amendment documents are replaced inside the running release.
The collector never imports `retention` (only `prune` does), so the running process is
unaffected. No unit file changes, so no `daemon-reload`; no restart.

```bash
# local, from ~/usb-ctx-collector-predeploy, C = this commit
C=<commit>; OUT=$(mktemp -d)
for f in backend/app/crypto/context_collector_v1/retention.py \
         docs/crypto/context_collector_v1/RETENTION_ACTIVE_SESSION_FIX_V1.md \
         docs/crypto/context_collector_v1/RETENTION_ACTIVE_SESSION_FIX_V1.sha256; do
  mkdir -p "$OUT/$(dirname $f)"; git show "$C:$f" > "$OUT/$f"; done
(cd "$OUT" && find . -type f | sort | xargs sha256sum)
scp -r "$OUT" traderj:/root/usb_runtime/crypto_context/staging-retention-${C:0:7}

# host
ssh traderj 'bash -s' <<'SH'
set -euo pipefail
[ "$(hostname)" = trader-j ] || exit 9
R=/root/usb_runtime/crypto_context; REL=$(readlink -f $R/src); C7=<commit7>
ST=$R/staging-retention-$C7; F=backend/app/crypto/context_collector_v1/retention.py
systemctl show usb-crypto-context -p MainPID -p NRestarts --value      # expect 1582777 / 0
systemctl is-active usb-crypto-context-retention.service || true      # expect inactive
sha256sum $REL/$F                                                      # expect 7309699d...
mkdir -p $R/backup && cp -p $REL/$F $R/backup/retention.py.f3806e7
for f in $F docs/crypto/context_collector_v1/RETENTION_ACTIVE_SESSION_FIX_V1.md \
         docs/crypto/context_collector_v1/RETENTION_ACTIVE_SESSION_FIX_V1.sha256; do
  install -o root -g root -m 644 $ST/$f $REL/$f.new && mv -f $REL/$f.new $REL/$f; done
sha256sum $REL/$F                                                      # expect 42c8f6a0...
cd $R && PYTHONPATH=$R/src/backend PYTHONDONTWRITEBYTECODE=1 /root/usb/.venv/bin/python -B \
  -m app.crypto.context_collector_v1 prune --root $R/journal --days 7   # dry run: v1.1, delete 0
systemctl show usb-crypto-context -p MainPID -p NRestarts --value      # unchanged
SH
```

Rollback: `cp -p /root/usb_runtime/crypto_context/backup/retention.py.f3806e7
<release>/backend/app/crypto/context_collector_v1/retention.py`. The next timer run (04:40 KST)
uses whichever file is in place; nothing else needs to change.
