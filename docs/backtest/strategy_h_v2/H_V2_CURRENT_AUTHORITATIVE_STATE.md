# Strategy H-V2: Current Authoritative State

This is the one document that states where Strategy H is **now**. Every other file in this folder
records a stage result as of its own date and is never edited after the fact. When this document
and a result document disagree about the present, this document wins. When they disagree about
what happened in a past stage, the result document wins.

Last verified: 2026-10-10 18:51 KST, after the production activation, against the repository and the
production host.

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

## D. Frozen contracts in force

| Contract | Value |
|---|---|
| D5 window contract | `D5_D2R_V1` (8/8 launch rows). `D5_D2_V1` is superseded and refused by `require_d5_contract` |
| D6 decision contract | `h_v2_d6_integrated_decision_v1` |
| D7 contract | `docs/operations/h_forward_shadow_v1.json`, sha256 `33fe002f…c915b1a` (recomputed equal) |
| D7 runner contract id | `h_v2_d7_forward_shadow_paper_integration_v1` |
| D5-D2R artifact consumed at launch | sha256 `118cbd78…7d14af` |
| Refresh triggers | 10-Q, 10-K, 8-K, EARNINGS_RELEASE, GUIDANCE, MATERIAL_CORPORATE_EVENT; `daily_full_rerun = false` |

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
* **sizing = NOT_DEFINED.** An APPROVE becomes an entry candidate only. Position creation is refused
  with `SIZING_CONTRACT_REQUIRED`, so today's answer to "can a position be created" is NO.
* 21D/63D are PENDING until their own maturity sessions.
* A/E/H UI integration (`/dashboard`, `/strategy-h`, `/strategy-compare`) is implemented. H has no
  capital ledger and is excluded from combined P&L.

## L. Production deployment state

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

Observation only. No research unless a structural bug is found:

1. Check the first timer run's journal (Mon 2026-10-12 01:10 ET): `refresh_run=OK`, integrity PASS.
2. Watch daily forward outcomes, new material filings, WATCH/REJECT transitions and a future APPROVE.
3. Watch 21D (2026-11-02) and 63D (2027-01-04) maturation.
4. Operator decisions still open: re-evaluating AEYE/FG/VRRM, and whether to freeze a sizing contract.

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
