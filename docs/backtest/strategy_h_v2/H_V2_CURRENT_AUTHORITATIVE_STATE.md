# Strategy H-V2: Current Authoritative State

This is the one document that states where Strategy H is **now**. Every other file in this folder
records a stage result as of its own date and is never edited after the fact. When this document
and a result document disagree about the present, this document wins. When they disagree about
what happened in a past stage, the result document wins.

Last verified: 2026-10-19 14:30 KST, read-only finalization audit of the suspended mode (server health,
units, forward/paper verify, production file sha256 against this repository, disk cleanup result). The change
log below section 0 is history.

## 0. Current state (2026-10-19, RESEARCH_SUSPENDED: new evaluation OFF, forward observation ON)

**2026-10-19 13:46 KST operator decision (this block supersedes the 10-18 state below).**

```text
Strategy H                      = RESEARCH_SUSPENDED
Reason                          = INSUFFICIENT_ALPHA_EVIDENCE (PV1 FAIL, PV2C FAIL, V3 never return-tested,
                                  20 ledger rows APPROVE 0 / WATCH 14 / REJECT 4, paper trades 0)
                                  + COST_OF_CONTINUED_EVALUATION (SCAN ~246 MB on a 1 GB host, crypto stalls)
usb-h-evaluation.timer          = DISABLED 2026-10-19 13:46 KST (never ran successfully after the 10-16 failure;
                                  the 10-19 14:40 KST first try did not happen). Unit files kept; `systemctl enable --now` restores it.
usb-h-forward.timer             = KEEP ENABLED (14:10 KST daily; matures 1D/5D/21D/63D, TP1/TP2 and Bear; no SCAN)
New intake / D3-D6 evaluation   = OFF
D3/D4/D5/D6 contracts           = FROZEN, no edits
Forward ledger (20 rows), paper ledger ($10,000, 0 positions) = PRESERVED, append-only, unchanged
                                  (forward_ledger sha256 9916f2dc…, paper events sha256 88850387… before and after)
Forward observation             = ACTIVE: launch cohort 21D matures 2026-11-02, 63D 2027-01-04
Historical replay of V3 rules   = DEFERRED
Restart condition               = forward evidence (WATCH vs REJECT, TP1/TP2 realism) or a future research
                                  priority that warrants a historical replay; never a rule change made to raise APPROVE
Data infrastructure (SEC submissions/companyfacts caches, PIT handling, MarketCalendar, reference cache) = retained for reuse
Production AI                   = NONE (no model call anywhere in H production)
P1 MethodJudgement IO (ec66400) = DEPLOYED, ACTIVE (code path idle while evaluation is OFF)
P2.1 import footprint (99db463) = DEPLOYED, ACTIVE (run_h_v2_d7.py, also used by the forward unit)
Facts cache repair (1e8d1bd)    = DEPLOYED; autonomous evaluation suspended by strategy decision, not by a defect
Disk cleanup (2026-10-19)       = PASS, 334.76 MB recovered (see change log); forward/paper data untouched
```

**Finalization audit 2026-10-19 14:30 KST (read-only).**

```text
usb-h-evaluation.timer          = disabled / inactive, no next trigger; usb-h-evaluation(-guard).service inactive
usb-h-forward.timer             = enabled / active (waiting), next Tue 2026-10-20 14:10 KST (01:10 ET)
last forward run                = 2026-10-19 14:10:00 -> 14:12:25 KST, exit 0, integrity PASS, refresh_run OK,
                                  paper_run OK
forward                         = verify PASS, ledger 20 rows sha256 9916f2dc… (unchanged since the suspension),
                                  price store latest 2026-10-16 (22 session files), decisions APPROVE 0 / WATCH 6 / REJECT 2
maturity                        = 1D matured 8/8, 5D matured 8/8, 21D pending 8 (2026-11-02), 63D pending 8 (2027-01-04)
refresh queue                   = cache_as_of 2026-10-19, REFRESH_DUE AEYE, FG, VRRM, PG (queued; no evaluator runs while suspended)
paper                           = verify PASS (16 stored = 16 recomputed), $10,000 cash = equity, 0 positions,
                                  0 trades, 0 pending entries, realized/unrealized 0; 12 SIGNAL_NO_ACTION, 0 entries
paper events                    = 16 rows sha256 b0784488…: the 2026-10-19 forward run appended one NAV row
                                  (session 2026-10-16); the first 15 rows still hash to 88850387…, so no row was rewritten
production files                = 62 H files (h_auto, h_forward, h_paper, run_h_v2_d7.py, deploy/h_evaluation,
                                  usb-h-* units, docs/operations/h_* contracts) byte-identical to this repository's HEAD
```

Other services were untouched (usb-backend 2248392, usb-crypto-paper 2519676, usb-frontend 2520193,
usb-crypto-context 1582777, usb-crypto-q123-forward 1684303 PIDs identical before and after).

## 0a. State as of 2026-10-18 (historical; superseded by the block above)

**What H is.** Strategy H is a US-equity, medium-horizon strategy that looks for issuers whose actual
fundamentals or business are improving, whose price does not yet fully reflect that change, and whose
valuation still leaves upside, and aims to capture the re-rating over several weeks to a few months.
Structure: Fundamental Change + Expectation Gap + Valuation -> Decision. Forward horizons 1D / 5D / 21D /
63D; 21D and 63D are the ones that judge it.

```text
Strategy H research                         = CLOSED
H-V2 D3/D4 deterministic production V3      = PASS (H_V2_D3D4_DETERMINISTIC_PRODUCTION_CONTRACT_V3.md)
H deterministic strategy contract           = FROZEN (docs/operations/h_d3d4_deterministic_v3.json, sha256 82f8319f…cea9f)
Production AI / Claude / LLM API dependency = NONE        AI runtime cost = $0
D1 / E1 / E2 / E3                           = COMPLETE / FROZEN
D2 (D2.1 depth policy)                      = COMPLETE / FROZEN
D3 production                               = DETERMINISTIC (H_D3_DETERMINISTIC_V3)
D4 production                               = DETERMINISTIC (H_D4_DETERMINISTIC_V3)
D5                                          = D5_D2R_V1 / FROZEN
D6                                          = h_v2_d6_integrated_decision_v1 / FROZEN
Forward shadow                              = ACTIVE (production)
Paper execution                             = ACTIVE (production backend, launch 2026-10-13T07:09:00Z, $10,000, 0 positions)
H UI                                        = ACTIVE (production frontend Rh0hJ2skzuYNTyehDWSI-)
Futures paper position size                 = ACTIVE / PASS
usb-h-research                              = REMOVED from production (2026-10-15)
usb-h-evaluation                            = INSTALLED with precondition gate + resource guard (d696c78);
                                              timer DISABLED: first gated run 2026-10-16 FAILED in SCAN
                                              (SEC_SUBMISSIONS_NOT_FOUND after compaction, pre-existing defect)
V3 production runtime                       = DEPLOYED 2026-10-15 (files over db41f04, backend restarted once) = DEGRADED
IO optimisation P1 (ec66400)                = ACTIVE in production (share-class reference cache; MJ reads 1,236 -> 94 MB)
import footprint P2.1 (99db463)             = ACTIVE in production (H entrypoint does not load pyarrow; import RSS 133 -> 98 MB)
price-pass fusion / persistent price state  = DEFERRED / NOT IMPLEMENTED
facts_cache.compact bug                     = FIXED_AND_DEPLOYED (1e8d1bd, facts_cache.py sha f9f0604d…, 2026-10-18;
                                              a ledger without its body is EVICTED and refetched, never FRESH)
H-V2 AUTONOMOUS EVALUATION                  = ENABLED_FIRST_RUN_PENDING (timer enabled 2026-10-18 17:06 KST;
                                              first try Mon 2026-10-19 01:40 ET = 14:40 KST); FULLY_AUTONOMOUS_ACTIVE
                                              only after that run completes
```

The AI budget ($24/day, $12 reservation), `claude-opus-5-5` canonical model, `authorization.json` and the
model repair loop of `H_V2_AUTONOMOUS_PIPELINE_V1` are **historical only**; the production contract is
`H_V2_AUTONOMOUS_PIPELINE_V2_NO_AI` (`docs/operations/h_autonomous_pipeline_v2.json`) pointing to the V3
deterministic contract.

**Decisions.**

```text
launch cohort (8, status)                    APPROVE 0 / WATCH 6 / REJECT 2
production V3 run 2026-10-15 (AI 0)          FG WATCH->WATCH, VRRM WATCH->WATCH; new candidates admitted:
                                             ADI, CRUS, EQT, LCII, PG, UVV, BKTI, NSSC = WATCH; MTN, DAL = REJECT
forward ledger                               20 rows, verify PASS; paper: 0 positions, 0 pending, $10,000
actual APPROVE so far                        0
synthetic clear positive                     D4 POSITIVE -> D6 APPROVE -> paper PENDING_ENTRY = PASS
21D = PENDING (maturity 2026-11-02)          63D = PENDING (maturity 2027-01-04)
strategy edge / profitability                NOT YET PROVEN
```

**Known limitations** (stated as they are, neither defects nor hidden):

1. Actual APPROVE count is 0.
2. FG, VRRM and ADI all have D4 = UNKNOWN: the D2.1 package rarely holds the same guidance metric in two
   publications, so the comparable guidance pair is usually one-sided.
3. Guidance direction on the historical corpus: 4 (all GD, verified against the filings).
4. Comparable pairs can appear as real filings accumulate (each new 10-Q / earnings 8-K is a new side).
5. Large re-rating NEGATIVE = DEFERRED (operator decision; not on the APPROVE path).
6. Catalyst and risk use minimal code-owned semantics (the next 10-Q/10-K is MATERIAL; reversal risks).
7. The actual strategy edge is not yet proven.
8. 21D and 63D results are still the evaluation that matters.

**Freeze policy (after V3 PASS).** Allowed: obvious implementation bugs, data-collection bugs, parser
correctness bugs (class D, recorded with before/after hashes), memory/performance optimisation,
infrastructure, scheduler, API/UI, logging/observability. Prohibited: changing D4's economic meaning, the
guidance direction conditions, the priced-in rule, catalyst or invalidation semantics, D5 methods or
thresholds, D6 decision rules, paper entry/exit rules, or any threshold tuned on past or forward returns.
A performance problem is judged by paper evidence, not fixed by a rule change.

**Operational resources (separate from the strategy verdict).** V3 strategy contract = PASS; 1 GB
production resource readiness = NOT YET PASS (NEEDS OPTIMIZATION / RESOURCE UPGRADE). Measured: SCAN peak
about 246 MB, evaluation-only about 218 MB, old combined same-process about 314 MB; the unit now runs them
as two processes (`7ccdd0c`). trader-j has about 1 GB RAM and historically 150-200 MB free. Recommended
path: upgrade to 2 GB or more, then manual acceptance; optimising further on 1 GB first is the operator's
alternative. RAM is not a strategy dependency.

**Next action (one).** Read the first automated run (Mon 2026-10-19 from 14:40 KST): SEC fetches, SCAN
complete, evaluation complete, decisions, guard summary (crypto latency, MemAvailable, PSI, OOM). Do not change
a rule because of its results. If it fails, `systemctl disable --now usb-h-evaluation.timer` and keep the
journal; the rollback file is `/root/h_fc_repair_backup_pre_deploy_20261018-170447.tar.gz`.

*Superseded next action (2026-10-18, before deploy):* Deploy `facts_cache.py` from `1e8d1bd` (no backend
restart: the API does not import it), check paper/forward verify and the cache plan on the host, then
`systemctl enable --now usb-h-evaluation.timer`.

**Facts cache compaction repair V1 (2026-10-18, 1e8d1bd).** Root cause: `facts_cache.index` read a copy as
reusable from the request ledger alone; the stores' own reuse rule is body + ledger. A copy missing any body
is now `EVICTED` (ranked with stale copies, oldest fetch first) and the SCAN reports it as
FUNDAMENTALS_FETCH_REQUIRED until it is refetched. On a read-only copy of the production cache: 1,413 cached
CIKs, old reading 1,413 FRESH, new 712 FRESH + 701 EVICTED; the old cache-only load failed 50/50 on the
evicted ones; the 712 usable copies load identically. compact, plan ranking, E1-E3, MJ, D3-D6, paper and
forward unchanged.

*Superseded next action (2026-10-16):* Fix the SCAN compaction defect (data-collection bug, allowed by the freeze policy):
`facts_cache.compact` drops bodies but keeps the request ledger, so the entry stays FRESH and `plan` never
refetches it; when the reference snapshot changes, `scan_production` re-scans those rows and `FC.load`
raises `SEC_SUBMISSIONS_NOT_FOUND`. Then re-enable `usb-h-evaluation.timer` (gate and guard already
installed, runbook §V3.1). Strategy research is not a next action.

**P1 + P2.1 activation (2026-10-16).** P2.1 deployed (`run_h_v2_d7.py` only, sha `a5e38a8d…`, no restart),
P1 files unchanged; gate + guard units installed from `d696c78`; timer enabled 15:29 KST. The 02:40 ET try
started at 15:40:05 (precondition RUN); the guard saw the SCAN process with pandas mapped and pyarrow 0.
First 2 minutes versus the morning failure (09:06): one crypto 10 s timeout at 38 s (market-gate pass), then
under 3.1 s, no sustained stall (morning: 30 s timeouts from 2:40 on); H process RSS 107-118 MB (cgroup
with page cache 160-174 MB); memory PSI full max 34.6 % and near 0 after 1 min (morning 62-88 %); swap
about 8 MB (morning: crypto-paper lost 70 MB to swap); IO PSI full 30-63 %. Whole run (833 s, through the
SEC download): MemAvailable min 177 MB, swap -43 MB, memory PSI full max 69.7 %, IO PSI full max 75.6 %,
crypto max 10 s, >1 s 32 / >5 s 8 / timeouts 3 of 134 samples, backend max 0.03 s, cgroup max 329 MB, the
guard did not stop it (no 30 s run of trouble). Then the SCAN failed: 1,413 rows to re-scan (reference
snapshot changed), CIK 0000006201's body compacted by the 10-15 run, `SEC_SUBMISSIONS_NOT_FOUND`, exit 1,
before any index, queue, ledger or paper write. Forward verify PASS (20), paper verify PASS (15/15), ledgers
and service PIDs identical to before; 178 MB of today's SEC cache written. Timer disabled again 15:56 KST.
Guard CSV `d7/auto/guard/20261016T064006Z.csv`.

**Production activation (2026-10-15).** Manual run 65 min (SCAN 37, evaluation 28), 12 items COMPLETE, no
OOM, integrity PASS; host MemAvailable min 179 MB, memory PSI full max 49.6 %, swap-out about 100 MB, unit
cgroup 397/400 MB, crypto terminal stalls up to 12.7 s (idle median 0.001 s). Decision: timer disabled,
1 GB readiness NEEDS OPTIMIZATION. Evidence `H_V2_DETERMINISTIC_V3_PRODUCTION_ACTIVATION_V1.md`.

**Evidence.** Production activation `H_V2_DETERMINISTIC_V3_PRODUCTION_ACTIVATION_V1.md`; V3 result `H_V2_D3D4_DETERMINISTIC_PRODUCTION_CONTRACT_V3.md`; V2 `…_V2.md`; V1 `…_V1.md`;
NO-AI repair `H_V2_PRODUCTION_NO_AI_RUNTIME_REPAIR_V1.md`.

## Change log (dated entries; history, not the present)

**2026-10-19 14:13-14:25 KST suspended-mode disk cleanup V1 = PASS.** Read-only audit first (forward `update`
reads d7 ledger/snapshot/prices/sec_submissions/refresh files/paper, the auto queue files and `auto/reference`;
GATE only counts `auto/price_history` file names; `auto/sec`, `evaluation`, `d2_1_cache`, `reference_history`
are not imported on the forward path). Deleted on the server: `d7/auto/sec/2026-10-16` (186.2 MB, the 10-16
failed SCAN's companyfacts/submissions, referenced nowhere, STALE from 10-24 and never pruned while SCAN is OFF),
`d7/auto/d2_1_cache` (6.3 MB), `/root/h_auto_deploy_20261014-130816/h_auto_data.tar` (60.6 MB, 488/488 files
identical to the live store) and `next-build-Rh0hJ2sk.tgz` (18.0 MB): 271.06 MB. `/root/h_v3_backup_pre_deploy_20261015-040700.tar.gz`
(63.7 MB, sha256 `0d157abd…`) was copied to the operator's local `~/usb_archive/2026-10/h_v2_cleanup_20261019/`,
matched by sha256 and size, then removed from the server: 63.70 MB. Free space 1,029.4 MB -> 1,361.4 MB.
Forward ledger, prices, sec_submissions, price_history, reference_history, evaluation, sec 10-14/10-15 and the
contracts hash the same before and after; verify and paper-verify PASS. No restart, no timer change.
Resume cost: before 2026-10-23 the first SCAN refetches about 1,400 SEC documents (about 11 min, free API);
after that, nothing beyond what staleness already required.

**2026-10-19 13:46 KST RESEARCH_SUSPENDED.** Operator decision; see section 0.

**2026-10-15 D3/D4 deterministic production contract V3 = PASS (local, NOT DEPLOYED, not pushed;
`H_V2_D3D4_DETERMINISTIC_PRODUCTION_CONTRACT_V3.md`, freeze `181794a`, impl `5302fb9`, class-D fix `2bddbaa`,
process split `7ccdd0c`).** Production H evaluates with no AI and can APPROVE: structured guidance parsing,
priced-in from the issuer's own D5 range, catalyst/invalidation states and the V3 gap table; a clear
synthetic positive reaches D6 APPROVE and paper PENDING_ENTRY through the unchanged D6/paper. Real data:
FG WATCH, VRRM WATCH, ADI admitted WATCH (all D4 UNKNOWN: no comparable guidance pair in the D2.1 package).
H contract design is closed; next is paper observation with no further tuning. Deployment waits on the
1 GB memory condition (unit peak 246 MB with SCAN and evaluation as separate processes).

**2026-10-14 D3/D4 deterministic production contract V2 (local, NOT DEPLOYED, not pushed;
`H_V2_D3D4_DETERMINISTIC_PRODUCTION_CONTRACT_V2.md`, freeze `8b55439`, impl `cce15b3`, class-D fix `c904426`).**
Verdict NEEDS REVISION. R3.1 V2 (Item 1 fallback, fail-neutral NOT_READY) removes the collection-artifact
REJECT (DORM back to WATCH; Part III 10-K/A issuers abstain). Guidance transcription is deterministic but
found 0 directional items on real filings (tables / heading periods are outside its grammar). APPROVE stays
unreachable without AI: priced-in conjunct, catalyst materiality/timing and invalidation have no frozen code
rule. Real data: FG WATCH, VRRM WATCH, ADI admitted WATCH, AI 0. SCAN uses a lean companyfacts reader
(identical output).

**2026-10-14 D3/D4 deterministic production contract V1 (local, NOT DEPLOYED;
`H_V2_D3D4_DETERMINISTIC_PRODUCTION_CONTRACT_V1.md`, contract `docs/operations/h_d3d4_deterministic_v1.json`
frozen before replay).** Production H reaches real D6 decisions with no AI: FG WATCH->WATCH, VRRM
WATCH->WATCH, new candidate ADI admitted WATCH (production-copy data). Verdict NEEDS REVISION: R3.1
reads a D2.1 package without a labelled 10-K BUSINESS chunk as "unsupported" (REJECT on a collection
artifact; DORM in the replay), and the frozen contracts decide no non-UNKNOWN expectation gap, so APPROVE
is unreachable. V2 items are listed in §5 of that document.

**2026-10-14 NO-AI runtime repair (local, NOT DEPLOYED; `H_V2_PRODUCTION_NO_AI_RUNTIME_REPAIR_V1.md`).**
Research-time AI = historical only. Production-time AI = NONE. Production evaluation = deterministic
(`H_V2_AUTONOMOUS_PIPELINE_V2_NO_AI`; Claude CLI, OAuth, API key, `authorization.json` and the AI budget
are gone from the production path and blocked at runtime). The V1 wiring of the research-time Opus
runners into production is recorded as an incident. Finding: the frozen contract (D0 §V, §L) makes
the D3 interpretation and the D4 gap state AI judgements with no code generator, so the code-owned
D3/D4 produce evidence and UNKNOWN only and every evaluation ends NOT_DECISION_ELIGIBLE: decisions stay
0/6/2, no new candidate can be admitted. Verdict NEEDS REVISION; the way forward is an operator
decision (§9 of that document). `usb-h-research` is to be removed, `usb-h-evaluation` installed
disabled until the RAM decision (evaluation peak about 280 MB).

**2026-10-14 production (DEGRADED).** The autonomous pipeline code, H's pinned data, the H UI and the
futures paper position size are deployed (backend files from `1719b2c`, frontend BUILD_ID
`Rh0hJ2skzuYNTyehDWSI-`). The 01:10 ET update ran on the new code: GATE READY, prices 2026-10-13, queue
FG/VRRM QUEUED, AEYE EVIDENCE_ALREADY_IN_THESIS, paper NAV OK, no future-date 403. The first
`usb-h-research` run (scan only, no authorization) drove the 961 MB host into swap thrash (backend
20-26 s, crypto terminal unresponsive) and was stopped; `usb-h-research.timer` is DISABLED. The server
Claude CLI login is invalid (401), so no authorization exists and no model was called. FG and VRRM are
not researched. Decisions 0/6/2; paper $10,000, 0 positions, 0 trades.

**2026-10-14 update.** The refresh executor, unified research queue, evidence dedup, code-enforced
budget, new-candidate intake (gated) and the research unit are implemented and tested locally, not
deployed (`H_V2_AUTONOMOUS_PIPELINE_CONTRACT_V1.md`). A real-data preflight with no model call found
that the frozen D2.1 policy left both FG's and VRRM's trigger 8-Ks out of the package; the executor
now adds each missing trigger with D2.1's own extractor. FG = P0, VRRM = P1, AEYE =
EVIDENCE_ALREADY_IN_THESIS. The nightly 403 on the not-yet-opened session is fixed in code (request
bound = previous XNYS session in ET), not yet deployed. Decisions are unchanged: 0/6/2.

**2026-10-14 completion (local, not deployed).** New-candidate intake now reads production
sources: Strategy A's `strategy_c/raw/{tickers,grouped,splits}` (read-only), H's own daily CS reference
fetched with A's fetcher, H's pinned price history (sessions before trader-j's 44-session grouped store)
and reference history (for D5's share-class step), and SEC per CIK on demand. A code-owned
MethodJudgement (D5-D0 section 17, P/B withheld, fail-closed) covers issuers outside the frozen tables
and screens candidates before they enter the queue. A production-shaped dry run with fake D3/D4 took
FG and VRRM (refresh) and CRGY (new candidate) through D2..D6, the ledger and paper with 0 live model
calls and no DATA_NOT_READY from H wiring. Details: `H_V2_AUTONOMOUS_PIPELINE_CONTRACT_V1.md` §9, §13.

## A. Purpose

H is a fundamental, issuer-level strategy. It decides whether the market already prices what an
issuer's business is about to show, using official SEC evidence, code-owned (deterministic) research and
expectation-gap rules under frozen contracts (AI was used at research time only), a valuation range and an
integrated decision. It is not an
intraday strategy and it never shares a book with A or E.

## B. Lifecycle

```text
Research build (D1-D6)   = COMPLETE, CLOSED (deterministic contract V3 PASS 2026-10-15)
D7 forward shadow        = LAUNCHED 2026-10-04T08:34:30Z (baseline session 2026-10-02), ACTIVE
Production operations    = usb-h-forward ENABLED (Mon..Fri 01:10 ET)
Paper execution          = ACTIVE in production (backend 2026-10-13; H_PAPER_V1_LAUNCH 2026-10-13T07:09:00Z;
                           $10,000, 0 positions, 0 trades)
Paper/H UI               = DEPLOYED (frontend Rh0hJ2skzuYNTyehDWSI-, 2026-10-14)
Refresh executor         = V3 deterministic evaluator DEPLOYED 2026-10-15 (usb-h-evaluation, run by hand)
New-candidate intake     = GATE ACTIVE in the 01:10 ET update; SCAN runs in usb-h-evaluation (first run 2026-10-15)
Production evaluation    = deterministic V3 (no AI), DEPLOYED, timer DISABLED (DEGRADED). The AI research path
                           (usb-h-research, authorization, budget) is retired; live model calls ever made = 0
```

No further research is planned. Changes follow the freeze policy in section 0. A return to research is
not allowed because APPROVE has not appeared or because of forward results.

## C. D1-D7 status

| Stage | Status | Result document |
|---|---|---|
| D1 candidate / eligibility | COMPLETE | `H_V2_D1_UNIVERSE_CHANGE_ENGINE_V1.md`, `H_V2_D1_1_UNIVERSE_DATA_COVERAGE_V1.md` |
| D2 evidence | COMPLETE | `H_V2_D2_EVIDENCE_COLLECTOR_V1.md`, `H_V2_D2_1_OFFICIAL_EVIDENCE_MATERIALIZATION_V1.md` |
| D3 research (AI, research-time) | COMPLETE - production D3 is deterministic V3 | `H_V2_D3_*` (final: `H_V2_D3_3_LIVE_CONFIRMATION_RESULT_V1.md`) |
| D4 expectation gap (AI, research-time) | COMPLETE - production D4 is deterministic V3 | `H_V2_D4_*` (final tier A: `H_V2_D4_4A_FINAL_TIER_A_V3_MECHANICAL_RESULT_V1.md`) |
| D5 valuation | COMPLETE | `H_V2_D5_*` (in force: `H_V2_D5_D2R_RECENT_REGIME_WINDOW_REPAIR_V1.md`) |
| D6 integrated decision | COMPLETE | `H_V2_D6_INTEGRATED_DECISION_ENGINE_PILOT_V1.md` |
| D7 forward shadow | LAUNCHED | `H_V2_D7_FORWARD_SHADOW_PAPER_INTEGRATION_V1.md` |
| D7 operations, 1st activation | FAILED, rolled back | `H_V2_D7_OPERATIONS_ACTIVATION_V1.md` |
| D7 operations repair | DEPLOYED | `H_V2_D7_OPERATIONS_REPAIR_V1.md` |
| D7 production activation | HEALTHY | `H_V2_D7_PRODUCTION_ACTIVATION_V1.md` |
| Paper execution V1 | READY (local) | `H_V2_PAPER_EXECUTION_CONTRACT_V1.md` |
| Paper execution production | ACTIVE (backend), frontend DEFERRED | `H_V2_PAPER_EXECUTION_PRODUCTION_V1.md` |
| Refresh batch R1 | PREREGISTERED (AI batch, superseded by the deterministic evaluator) - historical | `H_V2_REFRESH_BATCH_R1_PREREGISTRATION_V1.md` |
| Autonomous pipeline V1 | superseded by V2_NO_AI - historical | `H_V2_AUTONOMOUS_PIPELINE_CONTRACT_V1.md` |
| Autonomous pipeline production | DEGRADED (deployed; research unit DISABLED, not authorized) - historical | `H_V2_AUTONOMOUS_PIPELINE_PRODUCTION_V1.md` |
| NO-AI runtime repair | NEEDS REVISION (local) - historical | `H_V2_PRODUCTION_NO_AI_RUNTIME_REPAIR_V1.md` |
| Deterministic D3/D4 contract V1 | NEEDS REVISION - historical | `H_V2_D3D4_DETERMINISTIC_PRODUCTION_CONTRACT_V1.md` |
| Deterministic D3/D4 contract V2 | NEEDS REVISION - historical | `H_V2_D3D4_DETERMINISTIC_PRODUCTION_CONTRACT_V2.md` |
| **Deterministic D3/D4 contract V3** | **PASS, FROZEN (local, production deployment pending)** | `H_V2_D3D4_DETERMINISTIC_PRODUCTION_CONTRACT_V3.md` |

## D. Frozen contracts in force

| Contract | Value |
|---|---|
| D5 window contract | `D5_D2R_V1` (8/8 launch rows). `D5_D2_V1` is superseded and refused by `require_d5_contract` |
| D6 decision contract | `h_v2_d6_integrated_decision_v1` |
| D7 contract | `docs/operations/h_forward_shadow_v1.json`, sha256 `33fe002f…c915b1a` (recomputed equal) |
| D7 runner contract id | `h_v2_d7_forward_shadow_paper_integration_v1` |
| D5-D2R artifact consumed at launch | sha256 `118cbd78…7d14af` |
| Refresh triggers | 10-Q, 10-K, 8-K, EARNINGS_RELEASE, GUIDANCE, MATERIAL_CORPORATE_EVENT; `daily_full_rerun = false` |
| Paper execution contract | `docs/operations/h_paper_execution_v1.json`, sha256 `89f7beed…6c696c9b` (3 equal slots of 1/3 NAV, $10,000, next-session open via SimBroker `execution_v0`, TP1 half / TP2 rest / downgrade / explicit invalidation / Bear where it exists) |
| Thesis evidence index | `docs/operations/h_thesis_evidence_v1.json` (chunked accessions of each T1 package) |
| Autonomous pipeline contract (in force) | `docs/operations/h_autonomous_pipeline_v2.json` + `.sha256` (`H_V2_AUTONOMOUS_PIPELINE_V2_NO_AI`, production model NONE, P0..P3, dedup, retry 2; D3/D4 = V3) |
| Deterministic D3/D4 contract (in force) | `docs/operations/h_d3d4_deterministic_v3.json`, sha256 `82f8319f…cea9f`, freeze `181794a` (+ `h_d3d4_deterministic_v3.freeze.json`, class-D record `…class_d_fixes.json`) |
| Autonomous pipeline V1 (historical, not in force) | `docs/operations/h_autonomous_pipeline_v1.json` (daily $24, reservation $12, `claude-opus-5-5`): research-time only |

## E. Initial cohort (decision session 2026-09-16)

| Ticker | CIK | Decision | Binding clause |
|---|---|---|---|
| SCCO | 0001001838 | WATCH | APPROVE_BLOCKED:expectation_gap_permits_approve |
| DORM | 0000868780 | WATCH | APPROVE_BLOCKED:expectation_gap_permits_approve |
| TG | 0000850429 | WATCH | APPROVE_BLOCKED:expectation_gap_permits_approve |
| COLL | 0001267565 | WATCH | APPROVE_BLOCKED:expectation_gap_permits_approve |
| FG | 0001934850 | WATCH | APPROVE_BLOCKED:expectation_gap_permits_approve |
| VRRM | 0001682745 | WATCH | APPROVE_BLOCKED:expectation_gap_permits_approve |
| IDCC | 0001405495 | REJECT | REJECT:valuation_fully_prices_the_positive_case |
| AEYE | 0001362190 | REJECT | REJECT:future_business_story_only_without_corroborating_catalyst |

## F. D5 valuation state (from the launch snapshot)

| Ticker | Window | Confidence | TP1 upside | Bear |
|---|---|---|---|---|
| SCCO | FULL_2Y | LOW | +0.2% | 133.96 |
| DORM | FULL_2Y | MEDIUM | +3.1% | 114.10 |
| TG | RECENT_6M | LOW | +27.5% | 7.76 |
| COLL | FULL_2Y | LOW | +11.1% | 16.63 |
| FG | RECENT_6M | LOW | +19.0% | 23.56 |
| VRRM | RECENT_6M | MEDIUM | **+24.8%** | **N/A: NEGATIVE_IMPLIED_EQUITY** (null, never 0) |
| IDCC | FULL_2Y | MEDIUM | -34.7% | 168.98 |
| AEYE | RECENT_6M | MEDIUM | -1.9% | 5.67 |

## G. D6 decisions

```text
APPROVE = 0
WATCH   = 6  (SCCO, DORM, TG, COLL, FG, VRRM)
REJECT  = 2  (IDCC, AEYE)
```

A decision moves only through a D3→D4→D5→D6 re-evaluation on new material evidence. A price never
moves a decision.

## H. D7 launch state

* `launch_snapshot.jsonl`: 8 LAUNCH rows, sha256 `dba04d4b…389106` (identical locally and on the server).
* `forward_ledger.jsonl`: 8 LAUNCH_STATE rows, 8 distinct thesis versions (all T1), 0 transitions.
* `verify`: PASS, `problems = []`, locally and on the server.

## I. D7 outcome state (production runtime)

Snapshot as of 2026-10-10; on 2026-10-15 the production price store reaches 2026-10-13 and 21D/63D are
still PENDING.

```text
price store         = 17 session files, 2026-09-17 .. 2026-10-09
forward sessions    = baseline 2026-10-02 + 2026-10-05..09 (6 observed)
1D  = 8/8 MATURED   (maturity 2026-10-05)
5D  = 8/8 MATURED   (maturity 2026-10-09)
21D = 8 PENDING     (maturity 2026-11-02)
63D = 8 PENDING     (maturity 2027-01-04)
```

The 2026-09-16 → 2026-10-02 interval is pre-launch drift and is not forward evidence.

## J. Operational architecture (repair 81f06c8)

```text
usb-grouped-daily (00:40 ET, Strategy A; H does not depend on it)
        ↓ (ordering only, no Wants=)
usb-h-forward.service  →  run_h_v2_d7 update
  1. prices + outcomes + verify      failure domain: forward shadow  (verify FAIL → exit 2)
  2. SEC submissions, 8 cohort CIKs   failure domain: material refresh (REFRESH_DEGRADED, exit 0)
  3. material refresh scan            same domain; runs only on a complete, fresh cache
```

* An SEC failure never stops or rolls back step 1. A missing, stale or partial cache leaves
  `refresh_queue.json` byte-identical and is recorded in `refresh_status.json`.
* Research state (`refresh_queue.json`, e.g. AEYE REFRESH_DUE) is separate from infrastructure
  state (`refresh_status.json`: `refresh_run`, `refresh_data`).
* Production contract: `usb-h-forward.service` and `usb-h-forward.timer`,
  `OnCalendar=Mon..Fri 01:10 America/New_York`, `WorkingDirectory=/root/usb`, `Persistent=true`,
  flock on `/run/usb-h-forward.lock`.
* Procedures: `docs/operations/H_FORWARD_OPERATIONS_RUNBOOK_V1.md`.

## K. Known limitations (not health failures)

* **Refresh queue = AEYE, FG, VRRM REFRESH_DUE** (as of the 2026-10-10 SEC cache):
  * AEYE: 8-K 2026-09-18 `0001104659-26-108940` (items 1.01/2.03/9.01).
  * FG: 8-K 2026-10-05 `0001934850-26-000095` (2.02 results of operations, 7.01).
  * VRRM: 8-K 2026-10-02 `0001193125-26-412497` (5.02 officer/director change, 7.01, 9.01).

  Production has not re-evaluated them yet: the deterministic evaluator (usb-h-evaluation) is not
  deployed, so the decisions stand (AEYE REJECT, FG/VRRM WATCH). The local V3 re-evaluation on a
  production copy gives FG WATCH and VRRM WATCH at no model cost.
* **AEYE REFRESH_DUE is a scan artefact (2026-10-13).** Its 2026-09-18 8-K is chunked into the T1
  package that D3 read (cutoff 2026-09-28). The scan compares against the decision session, not the
  evidence cutoff. The research queue marks AEYE `EVIDENCE_ALREADY_IN_THESIS`. FG and VRRM are genuine.
* **Sizing:** `H_V2_PAPER_EXECUTION_V1` is in force in production since the paper launch
  (2026-10-13T07:09:00Z). It applies only to an APPROVE recorded after that time. The forward shadow
  contract still says NOT_DEFINED for the shadow itself, which holds no position.
* **Frontend:** the H paper screens (card money, `/trading-h` positions and fills) are not yet in the
  production build. The API serves them already.
* **New-candidate intake (D1/E1/E2/E3)**: wired to production sources and tested on
  production-shaped data (2026-10-14, local). 1,892 P1/P2 candidates on 2026-10-12; 10 open at a time.
  About 37-42% of candidates get a code-owned MethodJudgement; the rest are METHOD_JUDGEMENT_NOT_READY
  (no cover-page share count, no section-17 primary, financial SIC) and are not queued. 1,150 reference
  rows lack a FIGI and are REFERENCE_NOT_READY under the frozen E1 rule.
* **Refresh executor**: deployed with 1719b2c but its evaluation unit is not; the deterministic V3
  executor runs D2->D6 for one ticker with no AI (local, deployment pending).
* 21D/63D are PENDING until their own maturity sessions.
* A/E/H UI integration (`/dashboard`, `/strategy-h`, `/strategy-compare`) is implemented. Once paper
  is launched, H's card shows its own paper account and `/trading-h` shows positions and fills. H
  stays out of the A+E combined P&L and the A/E paper gate.

## L. Production deployment state

2026-10-13 addition, H paper execution (`H_V2_PAPER_EXECUTION_PRODUCTION_V1.md`): 15 files from
`5093f6c` were copied over db41f04 and `usb-backend` was restarted once. `paper/launch.json` was
written at 2026-10-13T07:09:00Z. Units unchanged. A/E API and E paper files are byte-identical
before and after. The D7 runtime is unchanged apart from the new `paper/`.

```text
code (D7 launch, UI, API)   = DEPLOYED (server git HEAD db41f04)
operations repair           = DEPLOYED as files over db41f04, source 81f06c8
D7 production               = ACTIVE
price / outcome             = ACTIVE
SEC submissions refresh     = ACTIVE (8 cohort CIKs, 8/8 on 2026-10-10)
material scan               = ACTIVE
timer                       = ENABLED, Mon..Fri 01:10 America/New_York
first unattended run        = Mon 2026-10-12 01:10 ET
```

Deployed sha256: `run_h_v2_d7.py` `d28534bf…`, `sec_refresh.py` `55a0cec7…`, service `fd7d81a5…`,
timer `111678d0…`. Queue `b7dc6482…`, status `a3658e40…` (as of the smoke run).

History: the first activation (2026-10-10) failed. The server had no SEC submissions cache, so
`refresh_scan` wrote all 8 issuers as NO_SUBMISSIONS_CACHE and AEYE's REFRESH_DUE was lost. The
timer was rolled back, the queue was restored (`43123c04…`) and the price catch-up was kept. Repair
81f06c8 split price/outcome from SEC refresh/scan into separate failure domains, so a missing or
stale SEC cache can no longer overwrite the research queue. It was deployed the same day
(`H_V2_D7_PRODUCTION_ACTIVATION_V1.md`).

## M. Next operational actions

Research is suspended (section 0). There is no deployment or evaluation action pending. The 01:10 ET
update keeps prices, outcomes, SEC submissions refresh, the refresh queue and paper NAV running.

* Next check: 21D maturity of the launch cohort on 2026-11-02, then 63D on 2027-01-04
  (`<prefix> status`, `<prefix> verify`, `<prefix> paper-verify`; runbook §0 and §4).
* Resuming evaluation is an operator decision, taken only on forward evidence or a research priority that
  warrants a historical replay; a rule change made to raise APPROVE is not a reason. Procedure: runbook §0.
* The 2026-10-14 operator items (Claude CLI login, `auto-authorize`, re-enabling `usb-h-research`) are
  retired with the AI path.

## N. Authoritative commits

| SHA | Meaning | In origin |
|---|---|---|
| 714841e | pre-Tier-A checkpoint | yes |
| 47bca87 | D5-P1.1 share class | yes |
| f58bf9f | D5-D1 | yes |
| 64612be | D5-D2 | yes |
| 57b159a | D6 | yes |
| 2479ea2 | D5-D2R (window contract in force) | yes |
| d646586 | D7 integration / launch | yes |
| 75c42c0 | D7 operations scheduler units | yes |
| 49502bc | failed activation result | yes |
| 81f06c8 | D7 operations repair (runtime source) | yes |
| c44dc79 | pre-production authoritative sync (docs) | yes |
| e64c08f | production activation result (docs) | yes |
| 5093f6c | paper execution V1 | yes |
| 2b1285d / 1719b2c / 050107b | autonomous pipeline V1, intake wiring, production record (historical AI path) | yes |
| c61c495 | NO-AI runtime repair | yes |
| 9f31eb7 / 523ef09 / 39740f4 | deterministic contract V1 freeze / implementation / result | yes |
| 8b55439 / cce15b3 / c904426 / d101325 | deterministic contract V2 freeze / implementation / class-D fix / result | yes |
| fc24cc3 | lean companyfacts reader (SCAN memory) | yes |
| 516279b / 69651ea | V2 memory record / intake test fake + regression | no (local) |
| **181794a** | **V3 contract freeze** | no (local) |
| **5302fb9** | **V3 deterministic implementation** | no (local) |
| **2bddbaa** | **V3 class-D fix (a bare year is never a guidance value)** | no (local) |
| **7ccdd0c** | **SCAN / evaluation process separation** | no (local) |
| **63db022** | **V3 result and state** | no (local) |
