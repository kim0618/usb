# H-V2 Autonomous Pipeline: Contract V1

Frozen 2026-10-14 KST, before any autonomous model call. **Live Opus calls made by this work: 0.**
Machine-readable contract: `docs/operations/h_autonomous_pipeline_v1.json`, sha256 in
`docs/operations/h_autonomous_pipeline_v1.sha256` (`app.strategies.h_auto.contract` refuses to load a
JSON that does not match). Status: **implemented and tested locally, NOT DEPLOYED.**

This step adds no research rule. It re-runs the frozen D1, D2, D2.1, D3, D4, D5 (`D5_D2R_V1`) and D6
code on new evidence, inside a code-enforced budget, and hands the decision to the existing H paper
book (`H_V2_PAPER_EXECUTION_V1`, unchanged).

## 1. Flow

```text
usb-h-forward  (Mon..Fri 01:10 ET, unchanged unit)          run_h_v2_d7 update
  1  prices + forward outcomes + verify      hard: forward integrity -> exit 2
  2  SEC submissions (launch cohort + admitted issuers)
  3  material scan                           soft: REFRESH_DEGRADED
  4  new-candidate intake (freshness gate)   soft: DATA_NOT_READY
  5  unified research queue                  soft; queue corruption -> exit 4
  8-9 paper entries/exits + NAV              hard: paper accounting -> exit 3

usb-h-research (Mon..Fri 01:40 ET, NEW unit, same flock)    run_h_v2_d7 auto-research
  5  unified research queue (rebuilt)
  6  budgeted research: D2 -> D3 -> D4 -> D5 -> D6       soft: a failure is a queue state
  7  decision transition (h_forward.transitions)
  8-9 paper entries/exits + NAV
```

A failure in steps 2 to 6 never stops prices, outcomes, open-position exits or NAV. Hard failures:
forward ledger integrity, the autonomy contract checksum, the queue's own integrity, paper accounting.

## 2. Modules (`backend/app/strategies/h_auto/`)

| Module | Role |
|---|---|
| `contract.py` | loads the frozen JSON, checksum-verified |
| `store.py` | `<d7>/auto/`: `queue_events.jsonl`, `spend.jsonl`, `thesis_evidence.jsonl` (append-only), `run_status.json`, `authorization.json` |
| `dedup.py` | is a filing already in the thesis (accession, filing date, source id) |
| `queue.py` | persistent unified queue, fold of append-only events |
| `budget.py` | reservation ledger, daily cap, per-day count, operator cap |
| `providers.py` | metered model call: canonical model per call, no fallback, cost metering, reservation ceiling |
| `inputs.py` | prices, SEC, D1 stub, D2 bundle, D2.1 package, trigger inclusion, D5 binding |
| `executor.py` | one queue item through D2 to D6 and the ledger write |
| `intake.py` | freshness gate and E1/E2/E3 scan (no model call) |
| `orchestrator.py` | queue step, research step, runtime timestamps |

Changed existing files: `h_forward/research_queue.py` (dedup through `h_auto.dedup`, P0 to P3),
`h_forward/transitions.py` (`admit` for NEW_FORWARD_CANDIDATE), `dev/run_h_v2_d7.py` (order, failure
domains, commands, price request bound), `api/strategies.py` (`autonomy` in `/strategies/STRATEGY_H_V2/paper`).

## 3. Budget (code-enforced)

| Item | Value | Basis |
|---|---|---|
| daily_hard_budget | $24.00 per ET date | reuses the FG/VRRM preregistration cap |
| per_candidate_reservation | $12.00 | contract worst case: D3 3 calls x $2 + D4 3 calls x $2 (`--max-budget-usd 2.00` per call) |
| max_research_candidates_per_day | 2 | 24 / 12 |
| operator cumulative cap | `authorization.json` | first batch: $24 |
| over budget | `BUDGET_DEFERRED`, never dropped | |

Observed in the attempt ledger (every stored D3 attempt and D4 analysis): D3 per candidate median
$1.33, max $1.54, 0 repairs used in 18 attempts (12 OK, 6 MODEL_CALL_FAILED at $0); D4 per candidate
median $1.42, max $2.43, per call median $0.96, max $1.54, repairs 0:14 / 1:9 / 2:6 (25 OK, 4
SCHEMA_VALIDATION_FAILED); 0 canonical-model mismatches. Expected spend per candidate is about $3; the
reservation is the worst case so an admitted candidate cannot overrun the daily cap.

A reservation that was never settled (crash mid-call) keeps counting in full. A dead model call is
charged its $2 cap. Every failure is paid for and counted.

Without `authorization.json` the research step returns `RESEARCH_NOT_AUTHORIZED`, makes no call and
changes no item. That is the state this code ships in.

## 4. Model contract

`claude-opus-5-5` requested, `modelUsage["claude-opus-5-5"].canonicalModel == "claude-opus-5-5"`
required on every non-error response, checked per call before the next call. A mismatch is
`FAILED:MODEL_CONTRACT_VIOLATION`, nothing is written to the ledger, no queue retry, no fallback. The
D3 and D4 schema, repair loop, validation, provenance lock and UNKNOWN discipline are the runners'
own (`research_one_v3`, `analyze_one_v2`), called unchanged.

## 5. Priority

| Class | Rule |
|---|---|
| P0 | REFRESH_DUE whose new filing is earnings: 10-Q, 10-K, 8-K item 2.02 |
| P1 | other material REFRESH_DUE (5.02, 1.01, 7.01 alone, ...) |
| P2 | NEW_CANDIDATE with E3 P1_HIGH |
| P3 | NEW_CANDIDATE with E3 P2_MEDIUM |

Ties: earliest new filing date (refresh) or intake order, then ticker. No price, return or outcome
takes part. Item 7.01 alone stays P1 because no code reads its text before research.
Current order checked on the production cache (2026-10-13): **FG (8-K 2.02, 7.01) = P0, then VRRM
(8-K 5.02, 7.01, 9.01) = P1.** AEYE = EVIDENCE_ALREADY_IN_THESIS.

## 6. Evidence dedup

A filing is in the thesis when its accession was chunked into a package the thesis read, or the
thesis bundle listed it, or (runtime theses only) its filing date is on or before the date the
thesis bundle's submissions were current. The frozen T1 index keeps its chunked-only rule, so AEYE,
FG and VRRM classify exactly as the preregistration recorded. Each completed re-evaluation appends
what it read to `thesis_evidence.jsonl`, so the same filing is never queued twice and a filing D2.1
saw and skipped is not re-queued forever. In-thesis filings never enter the queue and cost nothing.

## 7. Queue and retry

Statuses: QUEUED, RUNNING, COMPLETE, FAILED, BUDGET_DEFERRED, DATA_NOT_READY,
EVIDENCE_ALREADY_IN_THESIS. Fields: queue_id (`<reason>:<ticker>:<seq>`), ticker, CIK, reason,
priority, source accessions/events, queued_at, eligible_after, attempt_count, cost_usd, D2 to D6
versions, previous/final decision, decision transition, thesis version.

* Infra transient (SEC fetch, dead model process, MODEL_CALL_FAILED): at most 2 queue attempts, the
  retry waits for the next ET day. Then `FAILED:QUEUE_ATTEMPTS_EXHAUSTED`.
* Model/schema: only the stage's own bounded repair (max 2). `SCHEMA_VALIDATION_FAILED` is terminal.
* A terminal item is never reopened. A completed item is never re-run for its result.
* An item left RUNNING by a crash is recovered at the next run; its reservation stays counted.

## 8. Executor and the trigger-inclusion finding

Order: inputs (no money) -> D5 judgement check -> budget reservation -> D3 -> D4 -> D5 -> D6 ->
ledger write -> runtime thesis evidence -> spend settlement.

Real-data preflight (2026-10-14 KST, production D7 runtime copied read-only, SEC public API, **no
model call**) found that the frozen D2.1 depth policy leaves both triggers out:

* **FG** 8-K `0001934850-26-000095` (2.02, 7.01) has no EX-99; its text is in the primary document.
  D2.1 reads an earnings release only from EX-99, so it resolved to `EXHIBIT_UNRESOLVED`.
* **VRRM** 8-K `0001193125-26-412497` (5.02) lost the one CORE material-8-K slot to the later
  8-K/A `0001193125-26-413031` (10-05, 5.02).

A refresh exists to read the filing that triggered it, so the executor materialises each trigger the
package did not chunk from its primary document with D2.1's own `_materialize_source` (role
`REFRESH_TRIGGER`). The frozen function and every earlier package are unchanged. A trigger that still
has no chunk makes the item `DATA_NOT_READY:NEW_EVIDENCE_NOT_MATERIALIZED` before any model call.
After the fix: FG trigger 3 chunks, VRRM trigger 4 chunks.

Preflight D5 at session 2026-10-12 (inputs only, no D3/D4): FG VALUED, P/B, RECENT_6M, LOW, TP1
27.41; VRRM VALUED, EV/EBIT, RECENT_6M, MEDIUM, TP1 3.38, Bear null (NEGATIVE_IMPLIED_EQUITY). These
are D5 legs only, not decisions.

**D5 binding.** `inputs.d5_binding` points `audit_strategy_h_v2_d5_p1`'s package dir, fundamentals
roots, submissions roots and market-open list at the re-evaluation's inputs and restores them after.
Proof: with the launch roots, the new panel + binding reproduce the stored D5-D2R rows for FG and
VRRM byte for byte (`test_h_auto_d5_reproduction.py`), and the new price panel equals the published
loader's panel.

**Prices.** Panel = HIST-V1 mirror (or, on trader-j, the pinned cohort extraction
`auto/price_base/H_PRICE_BASE_V1.json.gz`, sha256 `1451d76a…5f03c`, 9 tickers, through 2026-09-16)
+ Strategy A's grouped store + H's D7 store. Two sources that hold the same session must agree exactly
(`PRICE_SOURCE_CONFLICT`). FG and VRRM: 519 sessions to 2026-10-12, 0 issuer gaps, 0 conflicts.

**Decision write.** WATCH->APPROVE/REJECT/WATCH and the other contract transitions go through
`transitions.record` (cause MATERIAL_REFRESH, D5_D2R_V1 only, new thesis version `T<n>:`).
REJECT->REJECT and APPROVE->APPROVE are not ledger rows; the queue records them. A new candidate is
appended by `transitions.admit` (record ADMISSION, cohort NEW_FORWARD_CANDIDATE) for APPROVE, WATCH and
REJECT alike. Thesis invalidation stays the existing explicit `THESIS_INVALIDATED` path; no new
invalidation rule is added.

## 9. New-candidate intake

Pipeline reused: reference universe -> `pipeline.assemble_candidate` (E1, E2, E3), no model call.
Freshness gate before any scan:

| Check | Local (2026-10-14) | trader-j |
|---|---|---|
| reference snapshot <= 7 days, active CS with CIK | CS_2026-10-01, 12 days: STALE | same file: STALE |
| grouped file for the publishable session | latest 2026-10-02: STALE | 2026-10-12: OK |
| price history >= 252 sessions | mirror: OK | 44 files: INSUFFICIENT |
| companyfacts store fetched <= 7 days | 2026-09-28: STALE | absent: MISSING |

So **NEW_CANDIDATE_SCAN = DATA_NOT_READY** in both places; nothing else in the daily job is affected.
Two further gates sit before money even when the data is fresh: a candidate without a declared D5
`MethodJudgement` is `DATA_NOT_READY:D5_METHOD_JUDGEMENT_REQUIRED` (D5 values only issuers with one,
and producing one is a model-owned judgement no stage automates), and at most 10 new-candidate items
are open at once.

## 10. Massive future-date noise

`collect_prices` used `date.today()` as its upper bound, so the 01:10 ET run asked for today's
session, which has not opened, and Massive answered 403 every night (journal 2026-10-12 and 10-13).
The bound is now `previous_trading_day(now in ET)`, the rule Strategy A's grouped collector uses. A
403 on a session at or before the bound is still a failure. Strategy semantics unchanged.

## 11. UI

`/trading-h` adds one panel, **자동 운영**: 마지막 자동 실행, 최신 가격 세션, SEC 갱신, 리서치 큐 실행,
다음 자동 실행, and the queue counts 대기 / 실행 중 / 예산 이월 / 실패. Every value comes from the server's
runtime files through `GET /strategies/STRATEGY_H_V2/paper` (`autonomy.timestamps`), never from a build
time. The paper account cards already show initial/current equity, cash, realised/unrealised PnL,
trade count (open positions added to its detail); decisions and 1D/5D/21D/63D are unchanged.

## 12. Tests

`backend/tests/strategy_h_v2/forward/test_h_autonomous_pipeline.py` (30) and
`test_h_auto_d5_reproduction.py` (4): REFRESH_DUE -> D2..D6, already-seen filing dedup, FG queue
P0, VRRM queue P1, AEYE excluded, NEW candidate queue, stale universe fail-closed, budget hard cap,
BUDGET_DEFERRED, operator cap, priority ordering, retry cap, schema failure terminal, canonical model
validation, WATCH no entry, REJECT no entry, APPROVE pending entry, next-session fill, TP1 then TP2,
downgrade exit, new-candidate admission APPROVE -> entry -> exits, AI failure does not stop prices or
exits, queue integrity hard failure, A/E isolation, scheduler order, Massive bound, D5 reproduction,
price base on a host without the mirror. `strategy_h_v2/` total: 1846 passed + new (repo-root cwd).

## 13. Production plan (not executed)

Prerequisites, all operator decisions:

1. **RAM.** trader-j has 961 MB and about 200 MB available; the Claude CLI (node) has never run there
   under load. `usb-h-research` caps itself at 650M so an overrun kills only itself, but the first
   authorized run must be watched (journal, `memory.peak`). The host-resource audit's 2 GB upgrade
   recommendation applies.
2. **Claude CLI login on trader-j** (`/usr/bin/claude` exists; login not verified, no call made).
3. **Authorization**: `python -m app.dev.run_h_v2_d7 auto-authorize --cap 24 --by <name>` (scope
   REFRESH_ONLY). Until then the research unit runs and calls nothing.

Deployment (file copy over the server tree, same method as 5093f6c; the four edited files on the
server are byte-identical to this work's base: research_queue `86c88ccc`, transitions `3080b277`,
run_h_v2_d7 `c78a6206`, api/strategies `21451e8f`):

* backend: `app/strategies/h_auto/*.py` (11), `h_forward/research_queue.py`, `h_forward/transitions.py`,
  `dev/run_h_v2_d7.py`, `api/strategies.py`; docs `h_autonomous_pipeline_v1.json` + `.sha256`.
* data: `d7/auto/price_base/H_PRICE_BASE_V1.json.gz` + `.sha256` (built where the mirror is).
* units: install `usb-h-research.service` + `.timer`; `usb-h-forward` unchanged.
* `usb-backend` restart once (the API module changed). The forward/research units need no restart.
* frontend: rebuild needed; see §14.
* smoke: `auto-status`, `research-queue`, `auto-research` without authorization (expect
  RESEARCH_NOT_AUTHORIZED, 0 calls), `verify`, `paper-verify`.

Rollback: restore the backed-up files, `systemctl disable --now usb-h-research.timer`, restart
`usb-backend`; `d7/auto/` can stay (nothing else reads it) and the forward ledger is append-only.

Expected first autonomous batch after authorization: FG, then VRRM; hard cap $24.00.

## 14. Frontend coordination

The running build (`BUILD_ID w3LLcmPdetY4iarG7TXCX`, 2026-10-13 15:12 KST) was built from a clean
`git archive 3e2a8ad` plus the polling-V1 files. Since 3e2a8ad, `frontend/` has five committed changes:
5093f6c (H paper screens), 1c6c900 (polling V1, deployed), 04428f1 (futures paper position size),
5504863 (liquidity map preview), 4be1d9d (market context preview routes), plus this work's H files
(`app/trading-h/page.tsx`, `lib/strategies.ts`, `components/trading-h.test.tsx`). The next coordinated
build should be taken from a clean `git archive` of the commit that contains this work, after the
owners of 5504863 and 4be1d9d confirm those preview routes may ship; otherwise build from 3e2a8ad +
polling files + the H files + 04428f1's four files. Never from a working tree with other sessions'
uncommitted edits.

## 15. Prohibitions honoured

No threshold, window or decision rule changed; no WATCH or REJECT entry; no historical fitting; no
future data (D5 bound to published sessions, facts PIT by acceptance time); no real-money order; A and
E untouched; no fallback model; no full-universe research; AEYE not researched; D5 = D5_D2R_V1 only;
no stop invented (VRRM Bear stays null); nothing pushed or deployed.
