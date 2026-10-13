# H Forward Shadow Operations Runbook V1

Procedures only. Current numbers and state are in
`docs/backtest/strategy_h_v2/H_V2_CURRENT_AUTHORITATIVE_STATE.md`.

Every server command runs as `ssh traderj 'bash -s'` with a `hostname` guard (`trader-j`), from
`/root/usb` with `PYTHONPATH=backend`. Prefix: `.venv/bin/python -m app.dev.run_h_v2_d7`.

## 1. Daily job

`usb-h-forward.timer`: `Mon..Fri 01:10 America/New_York`, `Persistent=true`. It runs
`usb-h-forward.service` (oneshot, `WorkingDirectory=/root/usb`, flock `/run/usb-h-forward.lock`), which
runs `update`:

1. **Price / outcome.** Collect every missing Massive grouped-daily session into H's own store.
   Mature 1D/5D/21D/63D, TP1/TP2 and Bear monitoring, then `verify`. A stored session is never
   rewritten. A gap stays a gap. `verify` FAIL → exit 2 → unit failed.
2. **SEC submissions refresh.** One primary submissions page per launch-cohort CIK (8 requests,
   `sec_store` limiter and the D1.1 User-Agent) into
   `data/runtime/strategy_h_v2/d7/sec_submissions/<ET date>/`. A same-day rerun makes 0 requests.
3. **Material refresh scan.** Runs only if every CIK is in one root fetched at most 4 days ago and
   today's fetch was complete. The result is validated and then atomically replaces
   `refresh_queue.json`.

**SEC never stops price/outcome.** Steps 2-3 run after step 1 is written and cannot raise into it.

## 2. Degraded behaviour

If steps 2-3 fail (SEC unreachable, partial fetch, stale cache, refused scan), the unit still
succeeds, and:

* stderr / journal shows `REFRESH_DEGRADED: <reasons> (prices and outcomes were updated; refresh queue left unchanged)`;
* `d7/refresh_status.json` holds `refresh_run=REFRESH_DEGRADED`, `refresh_data`, the reasons and the per-CIK fetch;
* `refresh_queue.json` stays byte-identical (`queue_sha256` in the status equals the file).

Verified locally against fixtures from the real 2026-10-04 store and queue
(`test_d7_refresh_independence.py`). With SEC down, price/outcome continues, the queue is preserved,
AEYE stays REFRESH_DUE and REFRESH_DEGRADED is recorded.

Check:

```bash
journalctl -u usb-h-forward.service -n 80 --no-pager | grep -E "REFRESH_DEGRADED|INTEGRITY|refresh_run"
cat data/runtime/strategy_h_v2/d7/refresh_status.json
```

## 3. Manual recovery

| Situation | Command |
|---|---|
| Missed sessions | `update` (collects every gap from the decision session) |
| SEC refresh only | `sec-refresh`, then `refresh-scan --dry-run`, then `refresh-scan` |
| Inspect | `status`, `verify` |
| Hand run while the timer may fire | `flock -w 300 /run/usb-h-forward.lock <prefix> update` |

Never run `launch`. It is refused once launched, and the timer never calls it.

## 4. Health check

```bash
systemctl status usb-h-forward.timer usb-h-forward.service --no-pager
systemctl list-timers usb-h-forward.timer --no-pager
<prefix> verify          # problems = []
<prefix> status          # decision_counts, maturity
```

## 5. AEYE / any REFRESH_DUE

The queue only queues. Clearing a REFRESH_DUE needs a D3→D4→D5→D6 re-evaluation, which costs model
calls and needs operator approval. A scan that would clear it, or drop a known filing, is refused
(`REFRESH_DUE_CLEARED_BY_SCAN`, `KNOWN_FILING_LOST`).

## 6. Sizing

Superseded by H paper execution (§8). Until `paper-launch` is run, the old behaviour holds: an
APPROVE is only an entry candidate, and positions are refused with `SIZING_CONTRACT_REQUIRED`.
After the launch, `H_V2_PAPER_EXECUTION_V1` sizes it at 3 equal slots.

## 7. Rollback

```bash
systemctl disable --now usb-h-forward.timer
rm /etc/systemd/system/usb-h-forward.{service,timer} && systemctl daemon-reload
tar xzf /root/h_d7_backup_pre_repair_<ts>.tar.gz -C /root/usb   # code + d7 runtime as before
rm -f backend/app/strategies/h_forward/sec_refresh.py           # if the backup predates it
```

The rollback touches no A or E unit, file or database.

## 8. Paper execution (H_V2_PAPER_EXECUTION_V1)

Contract: `docs/operations/h_paper_execution_v1.json` (sha `89f7beed…`), explained in
`docs/backtest/strategy_h_v2/H_V2_PAPER_EXECUTION_CONTRACT_V1.md`. Paper only: SimBroker
`execution_v0`, no broker and no exchange.

### 8.1 Paper account

* Files: `data/runtime/strategy_h_v2/d7/paper/`
  * `launch.json`: `H_PAPER_V1_LAUNCH`, written once.
  * `events.jsonl`: append-only.
  * `status.json`: the last run.
* Initialize once, after the code is deployed and approved:
  `flock -w 300 /run/usb-h-forward.lock <prefix> paper-launch`. A second call is refused.
* Initial state with 0 APPROVE:
  * equity = cash = $10,000
  * positions 0, trades 0
  * WATCH 6, REJECT 2, APPROVE 0

### 8.2 Entry queue

* An APPROVE row written after the launch becomes `PENDING_ENTRY`. It fills at the open of the first
  session whose open is after the decision. The daily job records the fill once that session's bar
  is stored (01:10 ET the next day).
* No free slot → `APPROVE_WAITING_SLOT`. A missing open → `ENTRY_PRICE_MISSING` (it waits, and is
  never filled at a close).
* Pending state: `paper-status` (`account.pending_entries`), `status.json` `pending`.

### 8.3 Position ledger

`paper-status` shows the account, open positions (entry, mark, TP1, TP2, Bear or `NOT_EVALUATED`,
realized and unrealized) and recent fills. API: `GET /api/v1/strategies/STRATEGY_H_V2/paper`.
The six shared routes (account/positions/trades/equity/cards) answer from the same events.

### 8.4 Exit processing

Exits run in `update` step 4, on the newly stored session bar:

* TP1: half at TP1
* TP2: the rest at TP2
* BEAR_BREACH: everything, at Bear or a lower open
* DECISION_DOWNGRADE: APPROVE→WATCH or APPROVE→REJECT, everything at the next open
* THESIS_INVALIDATED: everything at the next open

If one bar touches both Bear and a TP, Bear is taken (WORST_CASE).

### 8.5 Daily flow (one unit, unchanged schedule)

```text
usb-h-forward.service (Mon..Fri 01:10 ET) → run_h_v2_d7 update
  1. prices (now incl. open) + outcomes + verify      FAIL → exit 2
  2. SEC submissions refresh                          REFRESH_DEGRADED, exit 0
  3. material refresh scan                            same domain
  4. H paper execution: entries / exits / TP / NAV    PAPER_INTEGRITY_FAILURE or PAPER_ERROR → exit 3
  5. research queue rebuild (plan only, 0 model calls)
```

Step 4 reads what steps 1-3 wrote and never writes them. A paper failure leaves the forward shadow
updated and the paper events unchanged.

### 8.6 Failure recovery

| Situation | Action |
|---|---|
| `exit 3`, `PAPER_INTEGRITY_FAILURE` | Do not edit `events.jsonl`. Run `paper-verify` to name the first event that is no longer reproduced. Usually this means a price file or a ledger row changed. Restore that input from backup, then `paper-run` |
| `stalled_at` set | A session is missing from the price store. Run `collect-prices` (or `update`). Processing resumes and nothing is skipped |
| `ENTRY_PRICE_MISSING` repeated | The grouped-daily row has no `o`. Check the Massive response. The entry stays pending |
| Hand run | `flock -w 300 /run/usb-h-forward.lock <prefix> paper-run` (idempotent: a rerun appends 0) |

### 8.7 Manual audit

```bash
<prefix> paper-verify     # verdict PASS, problems [], stored == recomputed prefix
<prefix> paper-status
python3 - <<'PY'           # cash + positions_value == equity on every NAV row
import json; [print(e['session'], e['equity']) for e in map(json.loads, open('data/runtime/strategy_h_v2/d7/paper/events.jsonl')) if e['event']=='NAV']
PY
```

### 8.8 Rollback

```bash
mv data/runtime/strategy_h_v2/d7/paper /root/h_paper_rollback_<ts>   # keeps the evidence
# restore code from the pre-deploy backup tar
```

Removing `paper/` returns H to SHADOW_ONLY. The forward snapshot, ledger, prices, SEC cache and
refresh queue are not touched by the paper book, so they need no restore.

## 9. Research queue and refresh batches

* `research-queue` (and `update` step 5 → `d7/research_queue.json`) merges REFRESH_DUE with any
  new-candidate intake.
* Each filing is checked against the accessions the thesis read
  (`docs/operations/h_thesis_evidence_v1.json`). A filing the thesis already read is
  `EVIDENCE_ALREADY_IN_THESIS`.
* New-candidate intake (D1/E1/E2/E3) is **NOT_WIRED**. It is a manual dev script that runs on the
  stale universe snapshot `CS_2024-10-25` and the HIST mirror, which ends on 2026-09-16.
* No model call is scheduled automatically, and the production daily budget is NOT_APPROVED. A batch
  runs only under its own preregistration (`H_V2_REFRESH_BATCH_R1_PREREGISTRATION_V1.md`).

