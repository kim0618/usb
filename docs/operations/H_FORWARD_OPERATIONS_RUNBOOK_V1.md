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

The queue only queues. Clearing a REFRESH_DUE needs a D3→D4→D5→D6 re-evaluation. In production that is
the deterministic evaluator `usb-h-evaluation` (§V3: no model, no cost, no approval step); until it is
deployed the decisions stand. A scan that would clear a REFRESH_DUE, or drop a known filing, is refused
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
* New-candidate intake (D1/E1/E2/E3): GATE runs in `update`; SCAN runs only in `usb-h-evaluation`
  (§V3, not yet deployed).
* No model call exists in production. There is no AI budget and no preregistered AI batch to run; the
  R1 preregistration is historical.

## V3. Deterministic evaluation (H_D3D4_DETERMINISTIC_PRODUCTION_V3) and production acceptance

Status 2026-10-15 15:30 KST: contract V3 = PASS and FROZEN; **runtime DEPLOYED** (steps 1-7 done,
`H_V2_DETERMINISTIC_V3_PRODUCTION_ACTIVATION_V1.md`); `usb-h-research` removed; `usb-h-evaluation` installed,
manual run PASS; **step 8 NOT done: timer DISABLED** because the run pushed the 1 GB host to memory PSI
about 50 % and crypto terminal stalls up to 12.7 s. Until the resource condition is met, run by hand:
`systemctl start usb-h-evaluation.service` (after the 01:10 ET update; about 65 minutes on trader-j) with
`/root/h_v3_deploy_20261015/monitor.sh` running alongside.
Result: `docs/backtest/strategy_h_v2/H_V2_D3D4_DETERMINISTIC_PRODUCTION_CONTRACT_V3.md`.

Production H no longer uses any of: Claude CLI, Claude OAuth, Anthropic/OpenAI runtime, AI authorization
(`authorization.json`, `auto-authorize`), daily AI budget, per-candidate AI reservation, model repair loop.

Deterministic evaluation flow (one queue item, inside `no_ai_guard`):

```text
Universe -> E1/E2/E3 -> SEC / fundamentals / price -> D3 deterministic (V3) -> D4 deterministic (V3)
         -> D5_D2R_V1 -> D6 -> decision transition (forward ledger) -> Paper
```

Units (target):

```text
usb-h-forward     existing daily update (01:10 ET), unchanged
usb-h-research    REMOVED 2026-10-15 (unit files kept in /root/h_v3_deploy_20261015/removed/)
usb-h-evaluation  deterministic evaluator: ExecStart 1 = intake-scan, ExecStart 2 = auto-evaluate --skip-scan,
                  both under /run/usb-h-forward.lock, 01:40 ET; timer NOT ENABLED until step 8 below
```

Memory (measured locally on production-copy data): SCAN about 246 MB, evaluation-only about 218 MB, the old
single process about 314 MB. trader-j has about 1 GB RAM and historically 150-200 MB free, so 1 GB
operational readiness = NEEDS OPTIMIZATION / RESOURCE UPGRADE (recommended: 2 GB or more, then step 5). This
is separate from the strategy verdict.

Acceptance procedure (in order; stop at the first failure and roll back):

1. Back up the V3-touched backend files and `data/runtime/strategy_h_v2/d7` (tar under `/root`).
2. Deploy the deterministic files (`h_auto/{business_evidence, guidance_parser, guidance_parser_v2,
   deterministic_v2, deterministic_v3, executor, contract, facts_cache, inputs, intake}.py`,
   `dev/run_h_v2_d7.py`, `docs/operations/h_d3d4_deterministic_v{1,2,3}.*`,
   `h_autonomous_pipeline_v2.{json,sha256}`); check the contract checksums load.
3. Delete the old unit: `systemctl disable --now usb-h-research.timer`, remove
   `/etc/systemd/system/usb-h-research.{service,timer}`, `systemctl daemon-reload`.
4. Install `usb-h-evaluation.{service,timer}` with the timer **disabled**.
5. Manual evaluation once: `systemctl start usb-h-evaluation.service` (after the 01:10 ET update).
6. Observe memory and latency during the run (host PSI / available memory, backend and crypto terminal
   response times); any thrash -> stop the unit, keep the timer disabled.
7. Integrity: `verify` PASS, `paper-verify` PASS, forward ledger append-only, A/E/C files and API
   byte-identical before/after, `auto-status` ai_calls 0.
8. Only then: `systemctl enable --now usb-h-evaluation.timer`.

Rollback: disable/remove `usb-h-evaluation`, restore the step-1 backup; the append-only forward ledger and
`d7/auto/` stay. Strategy rules are frozen (state document §0): only bug, data, parser-correctness,
performance, infrastructure, scheduler, UI and observability changes are allowed.

## V3.1. Gated evaluation timer and resource guard (2026-10-16, d696c78)

Status: installed; **timer DISABLED** after the first gated run failed in SCAN (compaction defect, state
document §0). Re-enable with `systemctl enable --now usb-h-evaluation.timer` once the SCAN fix is deployed
(fix `1e8d1bd`, §V3.2).

* Tries: Mon..Fri 01:40, 02:10, 02:40 ET. `ExecCondition=deploy/h_evaluation/precondition.sh` runs the
  evaluation only when today's (ET) `usb-h-forward` succeeded, none of `usb-e-universe`, `usb-e-rvol-once`,
  `usb-grouped-daily`, `usb-splits-refresh`, `usb-e-paper` is active, the time is 01:30-02:45 ET, and
  `d7/auto/evaluation_attempt_day` is not today. A skip is logged (`h-evaluation precondition: SKIP ...`)
  and is not a failure. The attempt day is written before the run: a failed or stopped run is not retried
  the same day.
* `usb-h-evaluation-guard.service` (started by the unit's ExecStartPre, outside its cgroup, Nice -5) samples
  every 5 s and stops only `usb-h-evaluation` on 30 s in a row of: crypto or backend health > 5 s, memory
  PSI full avg10 > 50, MemAvailable < 100 MB, swap traffic > 10 MB per 5 s; at once on a new OOM kill; and at
  03:35 ET. It never stops another unit. Evidence: `d7/auto/guard/<start>.csv` (last 30) and the journal
  line `h-guard: summary ...` (MemAvailable min, swap delta, PSI max, crypto >1 s / >5 s / timeouts,
  backend max, cgroup max) plus `h-guard: pid ... pyarrow_maps=N` (P2.1 check, 0 expected).
* Look: `journalctl -u usb-h-evaluation -u usb-h-evaluation-guard --since today`.
* Rollback: `systemctl disable --now usb-h-evaluation.timer`; units and `run_h_v2_d7.py` from
  `/root/h_p21_backup_pre_activation_20261016-152754.tar.gz` (P2.1 alone: `/root/h_p21_backup_pre_deploy_20261016-135735.tar.gz`).
* Never run a standalone Python import benchmark on trader-j (2026-10-16: a cold import alone took >55 s and
  raised IO PSI full to 73 %).

## V3.2. SEC facts cache lifecycle (facts cache compaction repair V1, 2026-10-18, 1e8d1bd)

`d7/auto/sec/<ET day>/` is H's rolling SEC cache. A CIK copy is reused only when every document has body and
ledger; `compact` drops non-candidate bodies after each SCAN and keeps ledgers, so those copies read as
`EVICTED` and are refetched by a later run (cap 700 per run, missing first, then oldest fetch).

* Look: the SCAN status `sec_cache` (`FRESH` / `STALE` / `EVICTED` counts) and `readiness_counts`
  (`FUNDAMENTALS_FETCH_REQUIRED` = not fetched yet this rotation, not a failure).
* Never copy, create or hand-edit SEC bodies or ledgers to "repair" the cache; the planner refetches.
* A damaged body that is present fails the SCAN closed (reader error, exit 1): remove that CIK's ledgers
  in that dated root and let the next run refetch it.
* Deploy: `backend/app/strategies/h_auto/facts_cache.py` only; no backend restart (the API does not import
  it); the next `usb-h-evaluation` process picks it up.

## 10A. Production evaluation is NO-AI (H_V2_AUTONOMOUS_PIPELINE_V2_NO_AI, 2026-10-14) - superseded by §V3 for D3/D4, memory and units

This section supersedes the model, budget and authorization parts of §10. Write-up:
`docs/backtest/strategy_h_v2/H_V2_PRODUCTION_NO_AI_RUNTIME_REPAIR_V1.md`.

* Research-time AI = historical only. Production-time AI = NONE. There is no Claude CLI, OAuth login,
  API key, `authorization.json` or AI budget to maintain. Do not log in, do not create an
  authorization file; `auto-authorize` no longer exists.
* `auto-evaluate` (unit `usb-h-evaluation`, 01:40 ET, same flock; `auto-research` is the same command)
  runs SCAN, the candidate evaluation queue, the code-owned D3/D4, D5_D2R_V1, D6, then paper/NAV.
  Every item runs inside `no_ai_guard`; an attempt to reach a model fails the item
  (`PRODUCTION_AI_CALL_BLOCKED`) and writes no ledger row.
* D3/D4 run under the frozen `H_D3D4_DETERMINISTIC_PRODUCTION_V1` (`docs/operations/h_d3d4_deterministic_v1.json`,
  checksum-verified on load). Expected results: WATCH or REJECT written to the ledger as usual (WATCH and
  REJECT enter nothing); APPROVE is unreachable under V1; NOT_DECISION_ELIGIBLE only when the next
  periodic report cannot be derived (fewer than 4 periodic reports) or D5 abstains. The queue item keeps
  the D5 values and the D6 blockers. `METHOD_NOT_READY` = no code-owned MethodJudgement; retried next run.
* `usb-h-research` is removed. Install `usb-h-evaluation` disabled; enable only after the RAM decision
  (measured peak about 280 MB, trader-j had 150-200 MB available on 2026-10-14).
* Look: `auto-status` (mode DETERMINISTIC_NO_AI, ai_calls 0, latest_evaluation, timestamps incl.
  latest universe), `journalctl -u usb-h-evaluation`. Stderr `H_EVALUATION_DEGRADED` when the step fails.

## 10. Autonomous pipeline (H_V2_AUTONOMOUS_PIPELINE_V1) - HISTORICAL, AI path retired; kept for the intake/GATE details

Contract: `docs/operations/h_autonomous_pipeline_v1.json`; write-up
`docs/backtest/strategy_h_v2/H_V2_AUTONOMOUS_PIPELINE_CONTRACT_V1.md`. Once deployed:

* `update` (usb-h-forward, 01:10 ET) runs prices/outcomes, SEC refresh, material scan, intake GATE
  (refreshes `d7/auto/reference/CS_<publishable session>` with Strategy A's reference fetcher, about
  6 Massive calls, then checks reference age <= 7 days, A's grouped store holds the publishable
  session and >= 252 sessions), the unified queue, then paper/NAV. Its price requests stop at the previous XNYS session in ET, so
  the nightly 403 on today's unopened session no longer appears. A 403 on an earlier session is a
  real failure.
* `auto-research` (usb-h-research, 01:40 ET, same flock) first runs the intake SCAN (SEC on demand
  for at most 700 market-passing CIKs, about 10 minutes and 1,400 SEC requests at 5/s; then the
  frozen E1/E2/E3 in batches of 20 issuers, measured peak 286 MB; writes `auto/intake_index.json`
  and `new_candidates.json`; deletes SEC bodies of every non-candidate CIK), then rebuilds the queue,
  runs budgeted research, then paper/NAV again. Exit 0 even when research fails (stderr `H_RESEARCH_DEGRADED`); exit 3 paper
  accounting; exit 4 queue or autonomy-contract integrity.
* Turn research on: `.venv/bin/python -m app.dev.run_h_v2_d7 auto-authorize --cap 24 --by <name>`
  (`--scope REFRESH_AND_NEW_CANDIDATE` to include new candidates). Turn it off: delete
  `data/runtime/strategy_h_v2/d7/auto/authorization.json`. Nothing else is needed.
* Look: `auto-status` (queue counts, items, budget, runtime timestamps), `research-queue` (planner),
  `intake-readiness` (gate, universe counts, 20-ticker price coverage sample; pure read),
  `intake-gate` (reference refresh + gate), `intake-scan` (SEC slice + E1->E3 by hand),
  `cat d7/intake_status.json` (per-row readiness counts), `journalctl -u usb-h-research`.
* Intake failure states (recorded, never skipped): REFERENCE_SNAPSHOT_STALE / LATEST_PRICE_SESSION_STALE
  / PRICE_HISTORY_INSUFFICIENT (gate: the whole scan waits, new_candidates.json untouched);
  per row REFERENCE_NOT_READY (no CIK or FIGI), IDENTITY_INELIGIBLE, PRICE_NOT_READY, MARKET_INELIGIBLE,
  FUNDAMENTALS_FETCH_REQUIRED (not yet in the rolling SEC slice), SEC_NOT_READY (SEC 404).
  A SEC transport failure ends the slice for the day (`sec.degraded`), the rest waits.
* Executor failure states for a new candidate, before any model call: METHOD_JUDGEMENT_NOT_READY
  (financial SIC 6000-6799, unknown SIC, no computable multiple - e.g. no cover-page share count -
  or no section-17 ECONOMICALLY_REASONABLE method; reasons in `research/<run>/<item>/method_judgement.json`),
  PRICE_HISTORY_INSUFFICIENT, DECISION_SESSION_UNPRICED, PRICE_SOURCE_CONFLICT.
* A FAILED item is not retried by hand for its result. An infra failure retries once on the next
  day by itself. A new filing for the same issuer creates a new item.
* Rollback: `systemctl disable --now usb-h-research.timer`, restore the backed-up backend files,
  restart `usb-backend`. `d7/auto/` and the append-only forward ledger stay.
* Note: the `usb-h-forward.service` comment still describes the old "today's session reported as
  no_result" behaviour; the unit itself is not changed by this work.

