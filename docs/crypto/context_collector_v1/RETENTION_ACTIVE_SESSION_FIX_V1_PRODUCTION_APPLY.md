# Context Collector V1.1 - active-session retention fix: production apply record

Applied 2026-10-11 15:48 KST on trader-j. Source: main `3744627` (patch-identical to `dd7b3ed`).
Verdict: **ACTIVE_SESSION_RETENTION_FIX_PRODUCTION_PASS**.

## 1. What changed

Inside the running release `/root/usb_runtime/crypto_context/releases/crypto-context-f3806e7`:

| file | before | after |
|---|---|---|
| `backend/app/crypto/context_collector_v1/retention.py` | `7309699d…` (ctx-retention.v1) | `42c8f6a0…` (ctx-retention.v1.1) |
| `docs/crypto/context_collector_v1/RETENTION_ACTIVE_SESSION_FIX_V1.md` | absent | `72313e93…` |
| `docs/crypto/context_collector_v1/RETENTION_ACTIVE_SESSION_FIX_V1.sha256` | absent | `4c5dcca3…` |
| `PATCHES.txt` (release root) | absent | record of this patch |

`MANIFEST.sha256` and `SOURCE_COMMIT` were left as the record of the original f3806e7 release; no code
reads them at run time. Unit files, env, ingestion/store/wall/flow code: unchanged. No restart, no
`daemon-reload`, no manual `--apply`.

Backup: `releases/before-active-session-retention-fix-20261011/` (old `retention.py`, `MANIFEST.sha256`,
`SOURCE_COMMIT`, `SHA256SUMS`). Staging copy: `staging-retention-3744627/`.

## 2. Pre-state

Collector PID 1582777, NRestarts 0. Writer lock and state cache both name session `31cc8177`; it is
the only session in the journal file names. Journal 47M, 114 files. Disk / 2.2G free (77%).
Retention timer enabled, next run 2026-10-12 04:40 KST.

## 3. Dry-runs (nothing deleted)

Installed CLI with the unit's environment, without `--apply`: `ctx-retention.v1.1`, protected
`31cc8177`, identity_failure null, delete 0 (kept: active_session 72, young 36, not_sealed 6).

Plan of v1 (backup) against v1.1 (installed) on the live journal, read only:

| clock | v1 delete | v1 reconstruction sources | v1.1 delete | v1.1 reconstruction sources |
|---|---|---|---|---|
| now | 0 | 0 | 0 | 0 |
| +8d | 90 (48.2MB) | 54 (context/wall_v2/wall_r0 18 each) | 36 (telemetry/storage_stats) | 0 |
| +30d | 90 | 54 | 36 | 0 |

Synthetic roots at +8d (zero-byte copies of the journal names and mtimes, plus a fake closed
session `deadbeef` sealed 10 days earlier, in a temp dir removed afterwards):

| case | identity_failure | delete | active reconstruction | closed session |
|---|---|---|---|---|
| lock and state agree | null | 48 | 0 | 12 (all six kinds) |
| lock names no session | LOCK_NAMES_NO_SESSION | 0 | 0 | 0 |
| lock id not hex8 | LOCK_SESSION_NOT_HEX8 | 0 | 0 | 0 |
| state names another session | STATE_NAMES_OTHER_SESSION | 0 | 0 | 0 |
| state file missing | null (lock alone decides, by design) | 48 | 0 | 12 |

Non-context roots are refused before listing (exit 2): `/root/usb_runtime/crypto_paper/run`,
`/root/usb_runtime/crypto_liqfwd`.

## 4. No side effects

Before/after: usb-crypto-context, usb-crypto-paper, usb-crypto-liqfwd, usb-frontend, usb-backend,
nginx, usb-e-rvol: same MainPID, NRestarts 0. All nine timers (A, E, H, crypto) unchanged.
BTCUSDT LONG 0.108 @ 83913.1 (same update_time_ms), armed false, arm_count 0, REST trade_requests 0,
all five paper ledgers same sha. The only journal change was the collector's own hourly seal of
segment 00018.

## 5. Next timer run

`usb-crypto-context-retention.service` is a oneshot that starts a new Python process through the
`src` symlink each run, so the 2026-10-12 04:40 KST run uses v1.1 (log line `"version":
"ctx-retention.v1.1"` in `logs/retention.log`). No pyc files exist in the release.

## 6. Rollback

```bash
R=/root/usb_runtime/crypto_context; REL=$(readlink -f $R/src); B=$R/releases/before-active-session-retention-fix-20261011
F=backend/app/crypto/context_collector_v1/retention.py
install -o root -g root -m 644 $B/$F $REL/$F.new && mv -f $REL/$F.new $REL/$F
sha256sum $REL/$F   # expect 7309699d...
```

The two added documents and `PATCHES.txt` can stay; nothing imports them. No restart either way.

Evidence (local): `~/usb-crypto-context-collector-v1-evidence/retention-fix-apply-20261011/`.
