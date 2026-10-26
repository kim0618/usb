# PAPER RUN RETENTION V1

Code: `backend/app/crypto/paper/retention.py`. Tests: `backend/tests/crypto/test_paper_run_retention.py`.
Status 2026-10-26: applied on production. Run root 379.7MB → 34.7MB: 1,084 files, 345,135,491B (345.1MB) archived and removed in two phases. Wallet, positions and ledgers unchanged. Evidence and rollback: `~/usb_archive/2026-10/crypto/paper-run-retention-deploy-20261026/`.

## Why

`CRYPTO_PAPER_ROOT` (`/root/usb_runtime/crypto_paper/run`) only grows. A restart reads very little of it:

- A run that recovers alone loads its newest parseable checkpoint and replays what follows.
- The runs that share the wallet start from checkpoints at a common cut.
- Charts never read the tape.
- Every fill, charge and MAE/MFE is in the ledger.

Older segments are only a safety net for a checkpoint that cannot be used. Without them, recovery refuses (fail-closed); it never produces a wrong state.

## What stays and what may leave

| Class (decided from ledger, checkpoints and `hot_slot.json`, never from the name) | Stays on the server | May leave after a receipt |
|---|---|---|
| ACTIVE_OPEN_POSITION, ACTIVE_DEFAULT (BTC), ACTIVE_CURRENT_HOT | ledger, config, active tape, every segment and checkpoint from the second newest usable checkpoint on | older segments, manifests and checkpoints |
| CLOSED_TRADED | ledger, config, active tape, final checkpoint, last segment | older segments and checkpoints |
| CLOSED_NO_TRADE (no wallet event ever, flat, not HOT; ETH/SOL today) | nothing | the whole run directory, **only while `usb-crypto-paper` is stopped** |
| RETIRED_C1 (not discovered by the terminal, C1 state file, no money) | nothing | the whole directory, service stopped |
| BLOCKED | everything | nothing |

How the boundary is chosen:

- The boundary is not a fixed number. It is verified by simulating `recover_wallet`'s cut search with the same `_pick`.
- The simulation runs on the kept set, and again with each active member's newest checkpoint removed.
- A member that would have to replay from its first record counts as a failure.
- When the simulation fails, the boundary moves back one checkpoint at a time.

The plan blocks a run when anything is unclear:

- unreadable ledger
- ledger and checkpoint disagree on the position
- a command on the tape the ledger has not recorded
- a segment without a manifest
- an unreadable HOT slot (blocks every dynamic run)
- an undiscovered directory without C1 state

A ledger that records any wallet event is never a candidate.

## Procedure (manual, no timer)

1. **Server: run the preflight first**, then plan and write manifests. This only reads run files and writes into `archive/`.

   ```bash
   cd /root/usb_runtime/crypto_paper && PYTHONPATH=src/backend ionice -c3 nice -n19 \
     /root/usb/.venv/bin/python -m app.crypto.paper.retention plan \
     --root run --archive-dir archive --write-manifests
   ```

2. **Operator PC: pull.** The rsync goes through the throttled wrapper. The pull verifies size, sha256, gzip and JSON, then writes and uploads sealed receipts.

   ```bash
   cd ~/usb/backend && PYTHONPATH=. python3 -m app.crypto.paper.retention pull --host traderj \
     --remote-root /root/usb_runtime/crypto_paper/run \
     --remote-archive /root/usb_runtime/crypto_paper/archive \
     --dest ~/usb_archive/crypto_paper --rsync <throttled rsync wrapper>
   ```

3. **Server: prune dry run** (same command without `--apply`). Check the totals:
   - `PRUNE_ELIGIBLE` must equal what the receipts cover.
   - While the service is running, whole-run classes show `BLOCKED:SERVICE_RUNNING`. That is expected.

4. **Server: `prune --apply`.**
   - Segment-level pruning is safe while the service runs.
   - Removing whole runs needs a stop/start window. The service holds those runs as retired sessions and would write into them if one were revived.

## When to run it again

There is no timer and no calendar.

- Run `plan` (read-only, cheap) whenever the server preflight runs.
- Run the full procedure once `ARCHIVE_REQUIRED` bytes exceed the bytes the plan keeps, i.e. the server holds more archivable tape than recovery data.
  - With BTC alone at about 3.4MB a day, that takes about ten days. A HOT symbol in use shortens it.
- Plan the manifest, dry run and apply when IO PSI is low. Each step hashes the candidates on the server.
- Whole-run removal (a symbol that stopped being HOT without trading) waits for the next planned paper restart.

## Evidence (production snapshot of 2026-10-26 13:10, booted at the production path)

| Item | Value |
|---|---|
| Run size | 374.0MB → 31.1MB |
| Removed | 1,084 files, 342.9MB, failed 0, 13 run directories removed |
| Unchanged | shared wallet 9386.742942610005018506785960, available, used margin, fixed-mark equity, the 15 remaining member accounts, ledger hashes, BTC SHORT 0.064 @ 83612.90 |
| Removed members | 12, each with realised PnL, fees and position all 0 |
| BTC newest checkpoint torn or refused | recovery used checkpoint 122 and the kept segment 123; wallet identical |
| Simulated wallet plan with every checkpoint | identical to the real recovery's choice for all 14 members |
