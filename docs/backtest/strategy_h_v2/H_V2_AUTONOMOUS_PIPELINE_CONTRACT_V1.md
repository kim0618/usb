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
| `inputs.py` | prices (incl. H's pinned `price_history`), SEC, D1 stub, D2 bundle, D2.1 package, trigger inclusion, D5 binding (incl. reference snapshots) |
| `executor.py` | one queue item through D2 to D6 and the ledger write |
| `intake.py` | GATE (reference refresh + freshness) and SCAN (SEC slice, E1/E2/E3, MethodJudgement screen); no model call |
| `sources.py` | production sources: A's reference/grouped/splits read-only, H's daily reference, market tier, readiness reports (2026-10-14) |
| `facts_cache.py` | rolling per-CIK SEC cache `auto/sec/<day>`: plan, refresh, prune, compact (2026-10-14) |
| `method_judgement.py` | code-owned MethodJudgement from D5-D0 section 17 for issuers outside the frozen tables (2026-10-14) |
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

## 9. New-candidate intake (completed 2026-10-14)

### 9.1 What was wrong

The first wiring gated the scan on H's **research** inputs: the newest `CS_*` file across three
directories (one of them the 2024 research universe), the HIST-V1 mirror for history, and the
2026-09-28 full-universe companyfacts store (`strategy_h_v2/d1_1`). On trader-j the mirror and the
companyfacts store do not exist and the grouped store holds 44 files, so the gate could never open.
**H was reading research-frozen sources in production: YES.** Per input:

| Input | Old H source | Class | Production source now |
|---|---|---|---|
| universe | newest `CS_*` in strategy_c / RU v2 / RU u1 | STALE (A's manual 2026-10-01, never refreshed) | newest CS snapshot on or before the session: A's `strategy_c/raw/tickers` (read) or H's own `auto/reference/CS_<as_of>` refreshed daily with A's fetcher |
| ticker -> CIK | same rows | STALE | `cik` of that row (`cik_map` rule) |
| price history | HIST-V1 mirror | RESEARCH_FROZEN / MISSING on trader-j | A's `strategy_c/raw/grouped` (server: 2026-08-11.., local 2024-09-16..) |
| companyfacts | `strategy_h_v2/d1_1` full store | RESEARCH_FROZEN / MISSING on trader-j | per CIK on demand, `auto/sec/<day>` |
| submissions | same store | RESEARCH_FROZEN | per CIK on demand (same fetch) |

### 9.2 Strategy A / E / C audit (read-only, 2026-10-14)

| Component | A | E | C / shared | Latest | H reuses? |
|---|---|---|---|---|---|
| universe / reference | `strategy_c/raw/tickers/CS_2026-10-01` (manual, ages) | frozen `base_slice.npz` + E0 daily eligibility, no CIK | same tickers dir; `cik_map` rule | 2026-10-01 | YES (identity rules); H refreshes its own copy daily with A's `raw_fetch.fetch_tickers` into `auto/reference/` |
| grouped daily | `strategy_c/raw/grouped`, `usb-grouped-daily` 00:40 ET | own 20-session forward store under `/root/usb_runtime/workspace` | same as A | 2026-10-12 (server) | YES, read-only |
| splits | `strategy_c/raw/splits`, `usb-splits-refresh` 00:55 ET | `splits_asof` in its workspace | same as A | window to 2026-10-27 | YES, read-only (dilution check) |
| calendar | `MarketCalendar` | same | same | - | YES |
| SEC | none | none | `sec_store`, `xbrl_store` | - | YES (client reused, no new HTTP client) |
| adjustment | unadjusted | unadjusted | unadjusted | - | unadjusted, as D1 |

E carries no CIK and only 20 sessions, so it cannot be H's identity or history source.

### 9.3 Flow

* **GATE** (`update`, usb-h-forward 01:10 ET): refresh `auto/reference/CS_<publishable>` (about 6
  Massive calls), then the gate: reference <= 7 days with active CS rows carrying a CIK, A's grouped
  store holds the publishable session, at least 252 sessions. No SEC call.
* **SCAN** (start of `auto-research`, usb-h-research 01:40 ET): identity (frozen `build_universe`), E1's
  market tier from one streaming pass over A's store (>= 60 sessions, close >= $1, 21-session ADV >=
  $1M; a row failing it is INELIGIBLE or DATA_NOT_READY regardless of fundamentals, so no SEC request
  is made for it), SEC for at most 700 CIKs per run (missing first, then stale oldest, by CIK), then
  the frozen `assemble_candidate` (E1, E2, E3 unchanged) in batches of 20 issuers, an
  `auto/intake_index.json` of every scanned row, and `new_candidates.json` in the contract order
  (P1 before P2, evidence recency, ticker), capped at 10 open items, after the MethodJudgement screen
  (only AUTO_READY candidates are written; at most 40 new verdicts per run; a ticker already queued is
  never written again). Afterwards every SEC body not in
  `new_candidates.json` is deleted and its request ledger kept.
* Per-row readiness, never a silent skip: READY, REFERENCE_NOT_READY, IDENTITY_INELIGIBLE,
  PRICE_NOT_READY, MARKET_INELIGIBLE, FUNDAMENTALS_FETCH_REQUIRED, SEC_NOT_READY,
  COHORT_REFRESHED_NOT_ADMITTED.

### 9.4 Measured on production-shaped data (2026-10-14)

Run on the local tree with trader-j's exact A store (its 44 grouped files, `CS_2026-10-01` and 13
split files, byte-compared by sha256), no HIST-V1 mirror, no research universes, H's pinned history
(476 sessions 2024-09-16..2026-08-10, 58 MB) and H's own reference fetched by GATE. Four SCAN runs
covered the whole market-pass universe (SEC 600 + 700 + 700 + 410 CIKs).

| Measure | Value |
|---|---|
| reference snapshot | CS_2026-10-12 (H), 5,320 rows, 5,310 active CS with CIK, age 0 days |
| price sessions | 520 (A 44 + H history 476), latest 2026-10-12 = publishable |
| identified (CS, supported exchange, CIK and FIGI) | 4,154; unique CIKs with SEC cached 3,110 |
| REFERENCE_NOT_READY (no FIGI in the Massive row, frozen E1 rule) | 1,150 |
| market pass / MARKET_INELIGIBLE / PRICE_NOT_READY / IDENTITY_INELIGIBLE / cohort | 3,130 / 952 / 72 / 8 / 8 |
| E1 over the 3,130 | ELIGIBLE 2,615, INELIGIBLE 337, DATA_NOT_READY 178 |
| E3 | P1_HIGH 233, P2_MEDIUM 1,659, P3_LOW 698, HOLD 25 |
| P1/P2 candidates / written (open-item cap 10) | 1,892 / 10, all AUTO_READY |
| MethodJudgement screen to fill 10 | 25 judged: 10 AUTO_READY, 15 NOT_READY |
| SEC per run | 700 CIKs, 1,406 requests, about 10 min, 0 not found |
| peak RSS | 307 MB (SCAN incl. screen), 200 MB (E2E); first version OOM-killed, now streamed |
| SEC cache after compaction | 4 MB bodies plus ledgers |
| wall time | SCAN 14-19 min (SEC 8-10, screen 10) |

A full cycle of 3,130 market-pass CIKs takes 5 weekday runs, which matches the 7-day freshness bound.

Two production gaps were found on the way and fixed:

* **Price history.** trader-j's grouped store holds 44 sessions; E1, the executor and D5 need 252.
  H now keeps its own pinned slim extract of the same Massive grouped files (`auto/price_history`,
  manifest of sha256s), merged under A's store (A wins on a shared session, a disagreeing close is
  PRICE_SOURCE_CONFLICT). A's directory is not written.
* **D5 reference snapshots.** D5's share-class step needs a CS snapshot within 186 days before every
  panel session; trader-j has only `CS_2026-10-01`, so D5 would have refused every session before
  October for every issuer, cohort refreshes included. `d5_binding` now points D5-D2's
  `load_reference_snapshot` at share_class's directories plus `auto/reference` and a pinned
  `auto/reference_history` (8 quarterly snapshots). FG and VRRM still reproduce their launch rows
  byte for byte.

### 9.5 MethodJudgement

Where it came from: `run_strategy_h_v2_d6.METHOD_JUDGEMENTS` (13 issuers) and
`run_strategy_h_v2_d5_d2.METHOD_JUDGEMENTS` (7), hand-written per ticker; no classifier and no
business-type table exist. `multiples.method_suitability` (D5-D0 section 17) is the only deterministic
suitability rule, and it marks `NOT_SUITABLE` as AI-owned.

`h_auto.method_judgement` (H_METHOD_JUDGEMENT_AUTO_V1) is used only for an issuer outside those
tables: primary = section-17 ECONOMICALLY_REASONABLE methods, secondary = SECONDARY_ONLY, both in
METHOD_ORDER; P/B withheld; `unsuitable` empty. METHOD_JUDGEMENT_NOT_READY (DATA_NOT_READY before
any model call) for SIC 6000-6799, unresolved SIC, no computable multiple, or no
ECONOMICALLY_REASONABLE method. A cohort issuer always uses the frozen table.

Coverage on the 20 issuers with frozen packages (2026-09-16 session, frozen inputs):

| | count |
|---|---|
| AUTO_READY | 8 |
| NOT_READY: no ECONOMICALLY_REASONABLE method | 9 |
| NOT_READY: no computable multiple (BSY, GOOG, market cap unavailable) | 2 |
| NOT_READY: financial SIC (FG, GNW insurers; IDCC SIC 6794 patent licensor) | 3 |
| human table has a primary | 14; both have one 8; same first method 5 |
| code primary the human table asserted unsuitable | 1 (VRRM P/FCF; VRRM is cohort, so the frozen table is used) |

So about 40% of issuers get a code-owned judgement and the rest fail closed. That is the price of
adding no new threshold. IDCC shows the SIC rule is coarse: SIC 6794 is a licensor, not a lender.

On current candidates (2026-10-12 session, production data): 11 of 30 AUTO_READY (10 written + 20
drawn every k-th from the P1/P2 list). NOT_READY reasons: 11 no section-17 primary, 8 no computable
multiple (mostly no cover-page share count in companyfacts, e.g. ANIP's last `dei` share fact is from
2013), 3 financial SIC. Written unscreened, these would have held the 10 open new-candidate slots for
good, so the intake now screens before writing (§9.3) and records each verdict in the index; a
verdict is reused until the issuer's SEC facts are refetched.

### 9.6 End-to-end dry run (fake D3/D4, everything else real, 0 live model calls)

Store: copies of production's forward ledger, launch snapshot, refresh queue and SEC submissions
cache (sha256 equal to trader-j's), the production-shaped A store above, a fresh paper book. D3 and
D4 are the test fakes (D4 says POSITIVE/MEDIUM for everyone); D2, D2.1 with trigger supplement, the
MethodJudgement, D5 (D5_D2R_V1), D6, the ledger writer and the paper engine are the real code.

NEW candidate selection, fixed before any stage ran: the first candidate in the screened intake order
that is neither in H's ledger nor a D3/D4/D5 validation ticker. That was **CRGY** (P1_HIGH, SIC 1311).
An earlier unscreened run picked ANIP, which the executor refused as METHOD_JUDGEMENT_NOT_READY before
any model call; that run is what showed the open-slot problem.

| Item | Result |
|---|---|
| FLOW A FG (P0) | trigger 8-K `0001934850-26-000095` chunked (role REFRESH_TRIGGER); D5 frozen table; WATCH -> WATCH |
| FLOW A VRRM (P1) | trigger 8-K `0001193125-26-412497` chunked although the 8-K/A holds the CORE slot; WATCH -> APPROVE (synthetic D4) |
| AEYE | EVIDENCE_ALREADY_IN_THESIS, no item run |
| FLOW B CRGY day 1 | BUDGET_DEFERRED (MAX_RESEARCH_CANDIDATES_PER_DAY: FG and VRRM used both starts) |
| FLOW B CRGY day 2 | D2.1 FULL (365 chunks, 8 documents); MethodJudgement AUTO_READY, primary EV/EBIT then EV/EBITDA, secondary EV/Sales; D5_D2R_V1 valued (TP1 46.78, TP2 95.51, Bear 13.85); D6 WATCH; admitted NONE -> WATCH as NEW_FORWARD_CANDIDATE |
| price sources (CRGY) | h_history 475 + a_grouped 62 sessions; cohort price_base 1,002 + a_grouped 36 |
| paper | VRRM APPROVE -> PENDING_ENTRY for 2026-10-14; FG and CRGY WATCH -> SIGNAL_NO_ACTION; paper verify PASS, cash 10,000 |
| ledger | 8 -> 11 rows, verify PASS |
| model | 6 fake calls, 0 live; budget day 1 $5.20 of $24, 2 of 2 starts |
| DATA_NOT_READY from stale or wrong H wiring | 0 |

Evidence: `/home/tjd618/h_auto_completion/` (`e2e_evidence.json`, `e2e_preregistration.json`,
`scan_prodlike.json`, `candidate_readiness.json`, `mj_coverage_20.json`, logs).

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

`backend/tests/strategy_h_v2/forward/test_h_autonomous_pipeline.py` (30),
`test_h_auto_d5_reproduction.py` (4) and, for the completion, `test_h_production_intake.py` (35):
reference in force and H precedence, frozen `build_universe` identity, A-fetcher reference refresh
that never writes A's store, duplicate-ticker rejection, market tier equal to the frozen
`market_snapshot`, price coverage sample, gate refusals (stale reference, stale prices, short
history), GATE makes no SEC call, SCAN DATA_NOT_READY leaves the intake file, SEC plan order and
cap, root pruning, body compaction keeps ledgers, NOT_FOUND and transport failure, code-owned
judgement order and P/B withheld, determinism and frozen table untouched, five fail-closed cases,
pre-profit EV/Sales, SCAN end to end on fakes (only market-passing CIKs fetched, A store
byte-identical, rescan skipped when facts unchanged, queue order after refreshes), fetch cap,
SEC_NOT_READY, executor NOT_READY before any call, AUTO_READY new candidate -> D5_D2R_V1 -> D6 ->
admission -> paper entry, cohort ticker never takes a code-owned judgement, price base lacking a
candidate, the screen (only AUTO_READY written, verdict reuse, verdict budget, not-ready candidates
cannot fill the slots), H history under a 44-session A store, D5 reference binding restored, contract
values, no A/E runtime imports.

## 13. Production plan (not executed)

Prerequisites, all operator decisions:

1. **RAM.** trader-j has 961 MB. Measured locally: the intake SCAN peaks at 307 MB RSS (SEC slice of
   700 CIKs, E1->E3 streamed in batches of 20, MethodJudgement screen). `usb-h-research` is capped at
   650M, so an overrun kills only itself; the first run must be watched (journal, `memory.peak`).
   The Claude CLI (node) has never run there under load. The 2 GB upgrade recommendation applies.
2. **Disk.** About 1.9 GB free. H adds: price history 58 MB, reference history about 1 MB, daily
   references about 0.12 MB each (30 kept), SEC cache after compaction about 30 MB plus ledgers.
3. **SEC traffic.** About 1,400 requests per weekday run at sec_store's 5/s, about 10 minutes.
4. **Claude CLI login on trader-j** (`/usr/bin/claude` exists; login not verified, no call made).
5. **Authorization**: `auto-authorize --cap 24 --by <name>` (scope REFRESH_ONLY first; the first batch
   is FG then VRRM). Without it the research unit scans and queues but calls no model.

Deployment (file copy over the server tree, as with 5093f6c; take a hash of each target first):

* backend: `app/strategies/h_auto/*.py` (14: the 11 of 2b1285d plus `sources.py`, `facts_cache.py`,
  `method_judgement.py`), `h_forward/research_queue.py`, `h_forward/transitions.py`,
  `dev/run_h_v2_d7.py`, `api/strategies.py`; docs `h_autonomous_pipeline_v1.json` + `.sha256`.
* data built locally, then copied into `/root/usb/data/runtime/strategy_h_v2/d7/auto/`:
  `price_base/` (cohort mirror extract, sha `1451d76a…`), `price_history/` (476 sessions
  2024-09-16..2026-08-10 + MANIFEST, built with `H_PRICE_HISTORY_BEFORE=2026-08-11 build-price-history`),
  `reference_history/` (8 quarterly CS snapshots before 2026-10-01 + MANIFEST, `build-reference-history`).
  Verify on the server with `verify-price-history`.
* units: install `usb-h-research.service` + `.timer`; `usb-h-forward` unchanged.
* `usb-backend` restart once (the API module changed). The forward/research units need no restart.
* A, E, C files and units: none touched. H reads `strategy_c/raw/{grouped,tickers,splits}` only.
* smoke: `intake-gate` (expect READY: reference fetched for the publishable session), `intake-readiness`,
  `auto-research` without authorization (expect the SCAN to run, RESEARCH_NOT_AUTHORIZED, 0 model
  calls), `auto-status`, `verify`, `paper-verify`.

Rollback: `systemctl disable --now usb-h-research.timer`, restore the backed-up files, restart
`usb-backend`. `d7/auto/` can stay (nothing else reads it); the forward ledger is append-only.
Remove `d7/auto/authorization.json` to stop all model calls at once.

First live batch after authorization: FG (P0), then VRRM (P1), then new candidates by priority and
budget (at most 2 starts and $24 a day).

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
