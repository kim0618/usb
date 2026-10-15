# H-V2 D3/D4 Deterministic Production Contract V3 (result)

Date: 2026-10-15 KST. Not a research stage, not H-V3, not D8. Live AI calls: **0**. Not deployed, not pushed.
Contract: `docs/operations/h_d3d4_deterministic_v3.json` (`H_D3D4_DETERMINISTIC_PRODUCTION_V3`, sha256
`82f8319f…cea9f`), frozen in `181794a` with source, rule-table and fixture hashes
(`h_d3d4_deterministic_v3.freeze.json`) **before** any replay or real-data run. Implementation `5302fb9`
(files equal the frozen hashes). One class-D fix after the replay: `2bddbaa` (D-2, recorded in
`h_d3d4_deterministic_v3.class_d_fixes.json`; no rule changed). Process separation for memory: `7ccdd0c`.
V1 and V2 contracts, generators and results unchanged.

Verdict: **H-V2 D3/D4 DETERMINISTIC PRODUCTION CONTRACT V3 = PASS.**

Every §45 condition holds: structured guidance parsing works on real filings and produced real directions
(4, all verified against the source text); priced-in, catalyst and invalidation are code-owned and READY;
D4 POSITIVE, NEUTRAL and UNKNOWN are reachable; the deterministic pipeline alone takes a clear synthetic
positive to D6 APPROVE and paper PENDING_ENTRY; FG, VRRM and the new candidate ADI receive AI-free D6
decisions; production LLM dependency NONE; new numeric thresholds 0; D5, D6, paper unchanged. The one open
item, large re-rating NEGATIVE, is the operator's pre-declared deferral and is not on the APPROVE path.

What PASS does **not** mean (read before the paper observation stage):

* On real data every D4 so far is UNKNOWN: the D2.1 package (depth policy unchanged: 1-2 10-Qs, 1-3 material
  8-Ks) rarely holds the same metric's guidance in two publications, so the comparable pair C1 needs is
  usually one-sided. APPROVE is structurally reachable; how often real issuers reach it is what the paper
  observation stage will show. No rule is to be tuned for it.
* The catalyst clause is satisfied for nearly every issuer with a derivable next report (the next 10-Q/10-K
  is a frozen material earnings event), and the risk clause by the code-owned reversal risks. Both are the
  "minimal code-owned state" the operator chose; the gate that actually separates issuers is D4 plus D5.

## 1. Rules (frozen before results)

| Component | Rule | Numbers |
|---|---|---|
| Guidance parser V2 | scope from a located heading / table header; S1 prose (+ period after range, coordination, list continuation, scope period), S2 bullets, S3 table rows (column periods, Low/High), S4 label/value lines; lexical metric key; bare year = fiscal year only for a 12/31 year end | 0 |
| Priced-in V1 | D5 not VALUED -> UNKNOWN; upside_to_TP2 <= 0 -> LIKELY_PRICED; upside_to_TP1 <= 0 < upside_to_TP2 -> PARTIALLY_PRICED; upside_to_TP1 > 0 -> LIKELY_NOT_PRICED; confidence = D5's | the two D6 boundaries |
| Catalyst V1 | next periodic report (R3.5) 10-Q/10-K inside the horizon = MATERIAL (forward material_events, queue rank 1), timing BOUNDED by the statutory deadline | V1's 3-month horizon, SEC deadlines |
| Invalidation V1 | ACTIVE reversal condition per supporting core state and per RAISED guidance; TRIGGERED when the previous V3 thesis's condition now holds (reason code + evidence id); code-owned BUSINESS risks for adverse states, ACTIVE and TRIGGERED conditions | 0 |
| D4 gap V3 | U1 no D3 / U2 no comparable pair / U3 mixed or conflict / U4 priced-in unknown -> UNKNOWN; RAISED + improving core state + not LIKELY_PRICED -> POSITIVE; otherwise NEUTRAL; confidence min(C4 ceiling, D5 confidence); NEGATIVE and WIDE_POSITIVE not produced | C4 MEDIUM |

Inputs audited and not used for priced-in: event and benchmark-adjusted returns, pre-event run-up, relative
strength, drawdown, volatility (numbers only, no frozen state), D5 TrendClass ("diagnostic, never a gate").

## 2. Synthetic fixtures (`test_h_d3d4_deterministic_v3.py`, 25 tests)

| Fixture | Expected (frozen) | Result |
|---|---|---|
| A: guidance RAISED (bullets under "2026 Outlook"), improving core states, upside_to_TP1 > 0, D5 MEDIUM | POSITIVE / MEDIUM | PASS |
| B: same, upside_to_TP2 <= 0 | NEUTRAL | PASS |
| C: no comparable pair | UNKNOWN | PASS |
| D: guidance LOWERED | NEUTRAL (no frozen NEGATIVE) | PASS |
| D6 on A (deterministic D3/D4 only, D5 MEDIUM upside) | APPROVE, no blocker | PASS |
| Executor + paper on A | WATCH -> APPROVE, PENDING_ENTRY | PASS |

Parser structures (sentence, bullet, table columns, Low/High, label/value, subheading, two-period heading,
scope end), priced-in boundaries, catalyst states, invalidation ACTIVE/TRIGGERED, raw-number preservation and
the AI guard are tested.

## 3. Historical replay (`/home/tjd618/h_d3d4_v3/replay_v3contract.json`)

22 stored packages (13 with D4/D6). Guidance: 176 scopes, 93 candidate blocks, 208 transcriptions
(S3 table 149, T1 23, S3 column 14, T5 9, S2 bullet 7, T2 6), 148 with an unknown period, **4 paired:
GD RAISED 1, LOWERED 2, MAINTAINED 1** (10-K 2026-01-30 vs 10-Q 2026-07-29: corporate operating costs
160 -> 180, net interest expense 340 -> 270, net other income 50 -> 15, tax rate 17.5% -> 17.5%), 38 UNKNOWN.
V2 had 0.

Catalyst MATERIAL 21, UNKNOWN 1 (SIF, deadline beyond the horizon). Invalidation ACTIVE 13, UNKNOWN 9 (no
supporting core state). Priced-in on the 13 D6 rows: LIKELY_NOT_PRICED 6, PARTIALLY_PRICED 2, LIKELY_PRICED 1,
UNKNOWN 4 (D5 not valued). D4: 13/13 UNKNOWN (no comparable pair in any of those packages).

D6: 13 cases, V3 = V2 on 13; V3 = historical on 11, different 2 (SPSC: model D4 refused -> V3 WATCH, class B;
AEYE: business evidence NOT_READY -> NOT_DECISION_ELIGIBLE, class B). Classes: A 0 on D6, B 2, C 0, D 1 found
and fixed (D-2, WEN year read as a value, not a D6 case), E 0 on the APPROVE path.

## 4. Real data (production copy, server state 2026-10-14 15:17 KST; `/home/tjd618/h_d3d4_v3/v3_evidence.json`)

SCAN 1,114 s; preregistered new candidate (first AUTO_READY of the current order, not in the ledger, not a
validation ticker): **ADI**.

| | FG | VRRM | ADI (new) |
|---|---|---|---|
| D2/D3 | OK, P1 business, revenue DETERIORATING, EPS PROFIT_TO_LOSS | OK, P1, 6 states, all adverse/stable | E1 ELIGIBLE, E3 P1_HIGH, D2.1 built; OK, P1, revenue ACCELERATING, op inc / margin / EPS IMPROVING, FCF DETERIORATING |
| Guidance | 5 scopes, 0 transcriptions | 7 bullets (FY2026 total revenue, adj. EBITDA, adj. EPS, FCF, ...), one-sided | 43 table rows, period unknown, one-sided |
| Priced-in | LIKELY_NOT_PRICED / LOW | LIKELY_NOT_PRICED / MEDIUM | PARTIALLY_PRICED / LOW |
| Catalyst | MATERIAL, 10-Q by 2026-11-09 | MATERIAL, 10-Q by 2026-11-09 | MATERIAL, 10-K by 2026-12-31 |
| Invalidation | UNKNOWN (no supporting state) | UNKNOWN | ACTIVE (4) |
| D4 | UNKNOWN (U2) | UNKNOWN (U2) | UNKNOWN (U2) |
| MethodJudgement | frozen table | frozen table | CODE_OWNED_V1 AUTO_READY, P/FCF |
| D5 | P/B LOW, TP1 27.41 | EV/EBIT MEDIUM, TP1 3.25 | P/FCF LOW, TP1 354.99 (price above) |
| D6 | **WATCH** -> WATCH | **WATCH** -> WATCH | **WATCH** (NONE -> WATCH) |

AI 0, cost $0. VRRM's original trigger 8-K (5.02) chunked as before. Ledger 8 -> 11, paper verify PASS,
0 pending entries.

## 5. Memory and operations

| Process | Max RSS |
|---|---|
| SCAN (lean companyfacts, `fc24cc3`) | 246 MB |
| evaluation alone (`auto-evaluate --skip-scan` path, 3 items) | 218 MB |
| one process for both (before `7ccdd0c`) | 314 MB |

`usb-h-evaluation.service` now runs `intake-scan`, then `auto-evaluate --skip-scan`, as two processes under
the forward lock, so the unit's peak is the larger single step (246 MB). The host had 150-200 MB available on
2026-10-14: **1 GB operational readiness = NEEDS OPTIMIZATION** (RAM, or a measured host window). Strategy
PASS does not depend on it. Server (read-only): LLM keys 0; `usb-h-research` unit files still present
(timer disabled); `usb-h-evaluation` not installed.

## 6. Deployment plan (not executed)

1. Backend files: `h_auto/{business_evidence, guidance_parser, guidance_parser_v2, deterministic_v2,
   deterministic_v3, executor, contract, facts_cache, inputs, intake}.py`, `dev/run_h_v2_d7.py`,
   `docs/operations/h_d3d4_deterministic_v{1,2,3}.*`, `h_autonomous_pipeline_v2.{json,sha256}`.
2. Units: remove `usb-h-research.{service,timer}` from `/etc/systemd/system`; install
   `usb-h-evaluation.{service,timer}` with the timer **disabled**; daemon-reload. No restart of usb-backend
   is needed for the evaluation unit itself (the H API reads the same store).
3. Enable the timer only after the memory condition is met and a manual run shows no backend/crypto latency.
4. Rollback: `/root/h_auto_backup_pre_deploy_20261014-130816.tar.gz` (backend files), unit removal.

## 7. Regression (repo root, 2026-10-15)

`backend/tests/strategy_h_v2` 1,966 passed, 1 skipped, 1 failed: the pre-existing UTC-vs-ET day defect in
`test_d7_refresh_independence::test_a_partial_sec_failure_preserves_the_queue_and_scans_nothing_older` (fails
between 00:00 and about 04:00 UTC; code untouched since 39740f4; recorded in the V2 result). `strategy_h0` 63,
A/E (`strategy_a_mover_live`, `strategy_e_trading`, `strategy_e1_forward`) 558, `strategy_c` 55,
`strategy_eqm_v0` 14, crypto paper sizing 40: all passed. Frontend `trading-h` + futures paper position size:
18 passed (no local 8100 listener). V3 tests 25, auto-evaluate `--skip-scan` 1.

A, E, C, D5, D6, H paper, H forward and the futures paper position size: unchanged.
