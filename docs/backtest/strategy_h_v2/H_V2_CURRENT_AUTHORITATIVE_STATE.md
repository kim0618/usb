# Strategy H-V2: Current Authoritative State

This is the one document that states where Strategy H is **now**. Every other file in this folder
records a stage result as of its own date and is never edited after the fact. When this document
and a result document disagree about the present, this document wins. When they disagree about
what happened in a past stage, the result document wins.

Last verified: 2026-10-14 16:00 KST (NO-AI repair, local), before that 2026-10-14 14:50 KST, after the autonomous pipeline production deployment
(`H_V2_AUTONOMOUS_PIPELINE_PRODUCTION_V1.md`), against the production host.

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
issuer's business is about to show, using official SEC evidence, AI research under a frozen
contract, an expectation-gap judgement, a valuation range and an integrated decision. It is not an
intraday strategy and it never shares a book with A or E.

## B. Lifecycle

```text
Research build (D1-D6)   = COMPLETE, CLOSED unless a structural bug is found
D7 forward shadow        = LAUNCHED 2026-10-04T08:34:30Z (baseline session 2026-10-02)
Forward observation      = ACTIVE (prices and outcomes validated, 1D/5D matured)
Production operations    = HEALTHY (repair 81f06c8 deployed, timer ENABLED, 2026-10-10)
Paper execution          = ACTIVE in production (backend, 2026-10-13 16:12 KST; H_PAPER_V1_LAUNCH
                           2026-10-13T07:09:00Z; $10,000, 0 positions, 0 trades). Frontend build DEFERRED
Paper/H UI               = DEPLOYED (frontend Rh0hJ2skzuYNTyehDWSI-, 2026-10-14)
Refresh executor         = DEPLOYED (1719b2c), not run: research unit DISABLED
New-candidate intake     = GATE ACTIVE in the 01:10 ET update (READY 2026-10-14); SCAN not affordable on
                           trader-j RAM (first run stopped, host thrash)
Autonomous research      = NOT AUTHORIZED (no authorization.json; server Claude CLI login invalid);
                           live model calls so far = 0. Superseded locally by the NO-AI repair:
                           production AI = NONE, evaluation deterministic, NOT DEPLOYED
```

No further research is planned. A return to research is allowed only for a structural bug. It is
not allowed because APPROVE has not appeared or because of forward results.

## C. D1-D7 status

| Stage | Status | Result document |
|---|---|---|
| D1 candidate / eligibility | COMPLETE | `H_V2_D1_UNIVERSE_CHANGE_ENGINE_V1.md`, `H_V2_D1_1_UNIVERSE_DATA_COVERAGE_V1.md` |
| D2 evidence | COMPLETE | `H_V2_D2_EVIDENCE_COLLECTOR_V1.md`, `H_V2_D2_1_OFFICIAL_EVIDENCE_MATERIALIZATION_V1.md` |
| D3 AI research | COMPLETE | `H_V2_D3_*` (final: `H_V2_D3_3_LIVE_CONFIRMATION_RESULT_V1.md`) |
| D4 expectation gap | COMPLETE | `H_V2_D4_*` (final tier A: `H_V2_D4_4A_FINAL_TIER_A_V3_MECHANICAL_RESULT_V1.md`) |
| D5 valuation | COMPLETE | `H_V2_D5_*` (in force: `H_V2_D5_D2R_RECENT_REGIME_WINDOW_REPAIR_V1.md`) |
| D6 integrated decision | COMPLETE | `H_V2_D6_INTEGRATED_DECISION_ENGINE_PILOT_V1.md` |
| D7 forward shadow | LAUNCHED | `H_V2_D7_FORWARD_SHADOW_PAPER_INTEGRATION_V1.md` |
| D7 operations, 1st activation | FAILED, rolled back | `H_V2_D7_OPERATIONS_ACTIVATION_V1.md` |
| D7 operations repair | DEPLOYED | `H_V2_D7_OPERATIONS_REPAIR_V1.md` |
| D7 production activation | HEALTHY | `H_V2_D7_PRODUCTION_ACTIVATION_V1.md` |
| Paper execution V1 | READY (local) | `H_V2_PAPER_EXECUTION_CONTRACT_V1.md` |
| Paper execution production | ACTIVE (backend), frontend DEFERRED | `H_V2_PAPER_EXECUTION_PRODUCTION_V1.md` |
| Refresh batch R1 | PREREGISTERED, executor NOT READY | `H_V2_REFRESH_BATCH_R1_PREREGISTRATION_V1.md` |
| Autonomous pipeline V1 | READY locally, NOT DEPLOYED | `H_V2_AUTONOMOUS_PIPELINE_CONTRACT_V1.md` |
| Autonomous pipeline production | DEGRADED (deployed; research unit DISABLED, not authorized) | `H_V2_AUTONOMOUS_PIPELINE_PRODUCTION_V1.md` |

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
| Autonomous pipeline contract | `docs/operations/h_autonomous_pipeline_v1.json` + `.sha256` (daily $24, reservation $12, max 2/day, `claude-opus-5-5` canonical, no fallback, P0..P3, dedup, retry 2) |

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

  A D3→D6 re-evaluation needs operator approval because it costs model calls. Until then the
  decisions stand (AEYE REJECT, FG/VRRM WATCH).
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
* **Refresh executor**: implemented locally (2026-10-14), not deployed. Before that date no path
  re-ran D2→D6 for one ticker.
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

2026-10-14 (supersedes items 4 below where they overlap): the open operator decisions are (a) RAM for
the research scan on trader-j (2 GB upgrade or a smaller daily scan, a code change), (b) Claude CLI
login on trader-j, then a probe and `auto-authorize --cap 24 --by user-approved-2026-10-14`, (c)
re-enabling `usb-h-research.timer` under watch for FG then VRRM. Until then the 01:10 ET update keeps
prices, SEC refresh, the queue and paper NAV running.

Observation only. No research unless a structural bug is found:

1. Check the first timer run's journal (Mon 2026-10-12 01:10 ET): `refresh_run=OK`, integrity PASS.
2. Watch daily forward outcomes, new material filings, WATCH/REJECT transitions and a future APPROVE.
3. Watch 21D (2026-11-02) and 63D (2027-01-04) maturation.
4. Operator decisions still open (2026-10-14: the autonomous pipeline's deployment plan, RAM risk and
   authorization step are in `H_V2_AUTONOMOUS_PIPELINE_CONTRACT_V1.md` §13; the frontend build plan
   is its §14):
   * build the H frontend files into the next coordinated frontend build;
   * approve building the refresh executor, then batch R1 (FG and VRRM, hard cap $24.00, `claude-opus-5-5`);
   * whether to include AEYE despite EVIDENCE_ALREADY_IN_THESIS;
   * new-candidate intake is wired (2026-10-14); deploying it needs the files and pinned data in
     `H_V2_AUTONOMOUS_PIPELINE_CONTRACT_V1.md` §13 and the `usb-h-research` unit.

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
| 49502bc | failed activation result | no (local) |
| 81f06c8 | D7 operations repair (runtime source) | no (local) |
| c44dc79 | pre-production authoritative sync (docs) | no (local) |
| e64c08f | production activation result (docs) | no (local) |
