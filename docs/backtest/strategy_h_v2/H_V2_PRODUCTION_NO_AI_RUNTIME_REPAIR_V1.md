# H-V2 Production NO-AI Runtime Repair V1 (incident and repair record)

Date: 2026-10-14 KST. Scope: H-V2's production evaluation path only. This is not a research stage, not
H-V3 and not D8. No D3/D4 historical document was edited. Live AI calls made by this work: **0**.
Status: **implemented and verified locally, NOT DEPLOYED, not pushed.**

Verdict: **H-V2 PRODUCTION NO-AI RUNTIME = NEEDS REVISION.** The production path now has no AI
dependency and is enforced to stay that way, but the frozen H-V2 contract does not allow code to
produce the D3 interpretation or the D4 expectation-gap state. Without a model, every evaluation
ends `NOT_DECISION_ELIGIBLE`: existing decisions stand and no new candidate can be admitted. Closing
that gap needs a new rule set, which this repair was not allowed to invent (§9).

## 1. Incident

`H_V2_AUTONOMOUS_PIPELINE_V1` (1719b2c, deployed 2026-10-14 13:22 KST) wired the research-time model
runners into production:

* `h_auto/executor.py` called `run_strategy_h_v2_d3_3.research_one_v3` (D3) and
  `run_strategy_h_v2_d4_2.analyze_one_v2` (D4) through `h_auto/providers.live_call`, which started the
  Claude CLI (`CLAUDE_CODE_EXECPATH`, `claude-opus-5-5`, canonical-model check);
* `h_auto/budget.py` kept a $24/day budget, a $12 reservation per candidate and an operator
  `authorization.json`;
* `deploy/systemd/usb-h-research.service` carried `CLAUDE_CODE_EXECPATH=/usr/bin/claude` and a
  `HOME` login.

Root cause: the autonomous pipeline was specified as "re-run the frozen D2->D6 code on new
evidence", and D3 and D4 *are* model stages. The pipeline contract therefore re-ran the model; it
never asked whether production H may depend on a model. On trader-j it never ran: the server CLI
login is invalid (401), no authorization existed, 0 calls were made. The 252 MB host thrash of the
first research-unit run came from the SEC/E1-E3 scan, not from a model (§7).

## 2. What the frozen contract says (audit, not memory)

* D0 §V (AI vs Code matrix): business-model interpretation = AI; future-business classification
  (STORY vs REAL) = AI, code only extracts evidence; catalyst materiality / priced-in = AI;
  **expectation-gap synthesis = AI, code does evidence preparation only**; APPROVE/WATCH/REJECT = a
  code gate over content the AI produces.
* D0 §L: the gap is "a frozen categorical state, **judged by the AI and checked by code** for citation
  completeness". POSITIVE / NEUTRAL / NEGATIVE are defined in prose ("evidence suggests ..."), with no
  threshold.
* D0 §T: "H-V2's central bet: that AI judgment on top of the Evidence Bundle adds value".

So "AI was used only for research and contract validation" is not what the frozen contract says. The
contract made AI judgement part of every decision. This document records that plainly instead of
pretending a deterministic equivalent exists.

## 3. D6 required inputs (backtrace from `run_strategy_h_v2_d6.project_d3/project_d4/project_d5`)

Pinned in code as `h_auto.deterministic.D6_INPUT_BACKTRACE`; a test checks it against the
projection functions' source.

| Field | D6 uses it for | Producer before | AI-produced | Validator only | Code-owned evidence exists | Frozen deterministic rule | Production generator now |
|---|---|---|---|---|---|---|---|
| business_model.revenue_drivers | APPROVE business_understood; REJECT thesis_unsupported | D3 model | YES | ClaimV2 citation | no | no | NOT PRODUCED |
| fundamental_change (count, source_id) | APPROVE anchored change; REJECT thesis_unsupported | D3 model (state copied from E2, explanation AI) | YES (explanation) | state cross-check vs E2 | YES: E2 states + SEC accession | detection yes, interpretation no | code states recorded, D3 entry NOT PRODUCED |
| growth_durability.state | basis text only (deterioration trigger NOT_EVALUATED) | D3 model | YES | none | no | no | UNKNOWN |
| future_business stage | REJECT story-only; WATCH | D3 model | YES | floor table (stage -> min flags) | no | no (floor is not evidence -> stage) | NOT PRODUCED |
| future-business §J evidence flags (5) | APPROVE future/catalyst; REJECT story-only | D3 model | YES (model-authored booleans) | none links a flag to a chunk | no | no | NOT PRODUCED |
| catalyst materiality + timing | APPROVE; REJECT no_catalyst; WATCH | D3 model | YES | enum only | dates only | no | NOT PRODUCED |
| risks, invalidation_candidates | APPROVE risks_with_invalidation | D3 model | YES | sources non-empty | no | no | NOT PRODUCED |
| unknown_fields | APPROVE no_blocking_unknown | D3 model | YES | none | no | no | NOT PRODUCED |
| management signal | (D4 only) | D4 model | YES | >= 2 evidence ids | excerpt location only | no | not produced |
| guidance state / change | (D4 only) | D4 model writes ranges | YES | arithmetic recheck (NOT_COMPARABLE never disagrees) | passage location (regex) | no | not produced |
| expectation_gap | APPROVE §L; REJECT adverse gap; WATCH | D4 model | YES | C1, C2, C5, C6 reject only | bundle yes | **no** | UNKNOWN |
| gap confidence | APPROVE §L | D4 model | YES | <= ceiling, C5 | - | C4 ceiling only | UNKNOWN (C5) |
| priced-in | (record) | D4 model | YES | C7 | - | no | UNKNOWN |
| conflicts | count, no clause | D4 model | YES | two-sided evidence ids | - | no | [] |
| confidence_ceiling | record | code (C4) + model conflicts (C3) | partly | - | YES | C4 | code (MEDIUM: no consensus) |
| d6_approve_precondition | cross-check | code on model inputs | inputs AI | - | - | YES | code: BLOCKED |
| TP1 / TP2 / Bear, valuation confidence | APPROVE upside + confidence; REJECT fully priced | D5_D2R_V1 code | NO | - | YES | YES | unchanged (code) |

Two §N3 REJECT triggers stay NOT_EVALUATED in D6 itself (deterioration severity, risk severity), as
before.

## 4. D3

| | Count |
|---|---|
| D3 content fields (business model, change explanation, durability, future business + 5 flags, catalysts, competitive, management, why-now, risks, invalidation, conflicts, unknown_fields, completeness) | 14 groups |
| code-owned before this repair | 0 content fields (13 metadata fields; `code_owned_state` is E2's value copied by the model) |
| AI-only previously | all 14 |
| deterministic now | code-owned evidence block only: E2 fundamental-change states with SEC source ids, trigger chunks, package checksum |
| UNKNOWN-only now | growth_durability, future_business (stated UNKNOWN); the rest NOT PRODUCED |

`H_D3_CODE_OWNED_V1` (`h_auto.deterministic.d3_code_owned`) has **no `final_output`** by design. An
empty interpretation would be read by D6 as evidence: `business_model` empty fires
`thesis_unsupported_by_the_evidence_bundle` and turns a missing model into a REJECT (tested). So D6
projects the record as `D3_REFUSED_NO_FINAL_OUTPUT` and the issuer is `NOT_DECISION_ELIGIBLE`, which
D6 defines as "outside the sample", not a decision. The Future Business table could not be used as a
generator: it maps a stage to a minimum number of model-written flags (no ceiling), and D3.1's
proposal to move it onto code-owned evidence was never adopted (D3.2, D3.2R kept model flags).

## 5. D4

| | |
|---|---|
| code-owned evidence | price returns, relative strength, pre-event run-up, event reactions (1d/3d, benchmark-adjusted), located guidance / management passages, consensus and estimate-revision availability (always SOURCE_NOT_AVAILABLE), C4 ceiling, precondition function |
| AI-only final decisions previously | expectation_gap, gap confidence, priced-in, guidance state, result-vs-guidance, management direction, conflicts, why-now |
| deterministic now | `H_D4_CODE_OWNED_V1`: the frozen bundle (`build_bundle_for`), C4 ceiling, C5, C6 (when no price reaction), `d6_approve_precondition` |
| Expectation Gap generator | **does not exist in the frozen contract**. Code returns UNKNOWN/UNKNOWN: D0 §L's UNKNOWN ("a pricing-in assessment cannot be made responsibly"); C1 makes POSITIVE inadmissible because every non-price expectation item is model-written; NEUTRAL/NEGATIVE need a judged rationale no frozen rule supplies |

No threshold was added. The engine never produces POSITIVE, NEUTRAL or NEGATIVE.

## 6. Historical replay (oracle = stored Opus outputs; `/home/tjd618/h_noai_repair/replay.json`)

D3: 22 stored final outputs (d3_3 seg 1 and 2, d4_b and d4_br_c D3 legs), each matched to the exact
D2.1 package the model read (D3's own `package_checksum`, 22/22).

| | |
|---|---|
| D3 cases | 22 |
| same (deterministic reproduces the interpretation) | 0 |
| different | 22: the deterministic record produces no interpretation (AI-owned, §4) |
| E2 `code_owned_state` copies compared | 129 of 129 identical (the only D3 values code owns) |

D4: 17 stored analyses with their own expectation bundles (d4_2 three runs, d4_b, d4_br_c).

| Run | Ticker | Historical gap | Deterministic | Same |
|---|---|---|---|---|
| D4_2_A-…072105Z | BSY / GOOG | UNKNOWN/UNKNOWN | UNKNOWN/UNKNOWN | yes / yes |
| D4_2_A-…072105Z | SCCO | NEUTRAL/LOW | UNKNOWN/UNKNOWN | no |
| D4_2_A-…012115Z (Tier A V3) | BSY / GOOG | UNKNOWN/UNKNOWN | UNKNOWN/UNKNOWN | yes / yes |
| D4_2_A-…012115Z | SCCO | NEUTRAL/LOW | UNKNOWN/UNKNOWN | no |
| D4_2_A-…034348Z (S1 smoke) | SCCO | NEUTRAL/LOW | UNKNOWN/UNKNOWN | no |
| D4_B | CRK, IDCC, TG | NEUTRAL/LOW | UNKNOWN/UNKNOWN | no |
| D4_B | DORM | UNKNOWN/UNKNOWN | UNKNOWN/UNKNOWN | yes |
| D4_B | FRPT, SPSC | refused (no final output) | UNKNOWN/UNKNOWN | no |
| D4_BR_C | AEYE, COLL | NEUTRAL/LOW | UNKNOWN/UNKNOWN | no |
| D4_BR_C | FG, VRRM | UNKNOWN/UNKNOWN | UNKNOWN/UNKNOWN | yes |

Total 17: same 7, different 10 (8 NEUTRAL/LOW -> UNKNOWN, 2 refused -> UNKNOWN). Ceiling and
precondition agree on all 15 historical outputs (MEDIUM, BLOCKED). Every NEUTRAL rests on model-written
items (priced-in LIKELY_PRICED, rationale claims); no frozen rule derives NEUTRAL from the bundle.
Nothing was tuned after seeing this table.

## 7. Memory (measured locally on production-copy data, Python process RSS; no model process exists)

| Stage | Peak RSS |
|---|---|
| imports | 126 MB |
| universe (reference rows) | 138 MB |
| market tier (A grouped pass) | 148 MB |
| SEC submissions + companyfacts fetch/parse | 253 MB |
| price series for scanned rows | 231 MB |
| E1/E2/E3, batches of 20 with companyfacts loaded | 273 MB |
| MethodJudgement screen (D5 panels) | 231 MB |
| SCAN process max (`/usr/bin/time`) | 279 MB (cap 200 CIKs), 261 MB (cap 700, full run incl. evaluation) |
| evaluation only, 3 items (prepare/D2.1, code D3, code D4, D5, D6) | 235 MB process max, 88 s |
| code D3 / code D4 increments | below sampler resolution (< 1 MB) |

The 252 MB peak and the 2026-10-14 thrash were the scan (SEC parsing and E1-E3 batches), not a model.
Removing AI removes no scan memory. The fetch cap does not lower the peak (batches stream); it only
changes the cycle length. The evaluation unit needs about 280 MB of headroom; trader-j had 150-200 MB
available during the incident, so the 2 GB upgrade recommendation (host resource audit, 2026-10-13)
still applies before the evaluation timer is enabled.

## 8. Real-data runs (production-copy store, `/home/tjd618/h_noai_repair/`)

Store: trader-j's H state copied read-only at 2026-10-14 15:17 KST (ledger 8 rows, snapshot, refresh
queue, queue events, prices, SEC submissions, reference CS_2026-10-13), H's pinned history (sha equal),
A's grouped/tickers/splits (45 files to 2026-10-13, sha equal). Production code path, real SEC.
GATE READY -> SCAN READY (1,105 s, 700 CIKs, 10 written) -> queue FG P0, VRRM P1, ADI P2, AEYE
EVIDENCE_ALREADY_IN_THESIS -> evaluation (104 s) -> paper.

| | FG | VRRM | ADI (new) |
|---|---|---|---|
| AI calls / blocked | 0 / 0 | 0 / 0 | 0 / 0 |
| trigger | 8-K 0001934850-26-000095 chunked (REFRESH_TRIGGER) | 8-K 0001193125-26-412497 chunked (8-K/A slot fix kept) | - |
| D3 | CODE_OWNED_EVIDENCE_ONLY: revenue DETERIORATING, EPS PROFIT_TO_LOSS, shares STABLE, op income/margin/FCF UNKNOWN; 356 chunks | revenue DETERIORATING, op income/margin INFLECTION_NEGATIVE, FCF DETERIORATING, EPS PROFIT_TO_LOSS; 181 chunks | revenue ACCELERATING, op income/margin/EPS IMPROVING, FCF DETERIORATING; 171 chunks |
| D4 | UNKNOWN/UNKNOWN, ceiling MEDIUM (C4), BLOCKED; 11 reactions, 24 guidance passages | UNKNOWN/UNKNOWN, MEDIUM, BLOCKED; 18 reactions | UNKNOWN/UNKNOWN, MEDIUM, BLOCKED; 14 reactions |
| MethodJudgement | frozen table | frozen table | CODE_OWNED_V1 AUTO_READY, P/FCF |
| D5 (D5_D2R_V1) | VALUED, P/B, RECENT_6M, LOW; price 19.09, TP1 27.41, TP2 29.81, Bear 21.98 | VALUED, EV/EBIT + EV/Sales, RECENT_6M, MEDIUM; 3.01, TP1 3.25, TP2 5.66, Bear null | VALUED, P/FCF, FULL_2Y, LOW; 414.70, TP1 354.99, TP2 422.90, Bear 320.03 |
| D6 | NOT_DECISION_ELIGIBLE (D3_REFUSED_NO_FINAL_OUTPUT) | same | same |
| transition | WATCH -> WATCH unchanged (no ledger row) | WATCH unchanged | not admitted |

ADI selection, recorded before any stage ran on it (`noai_preregistration.json`): first candidate of
the current SCAN's intake order (E3 P1_HIGH before P2_MEDIUM, fewest days since latest filing, ticker;
AUTO_READY only) not in H's ledger and not a validation ticker. ADI: E1 ELIGIBLE, E3 P1_HIGH, SIC 3674,
520 sessions. The 10-14 morning run picked CRGY by the same rule on the 10-12 session; the order moved.
DATA_NOT_READY caused by H wiring: 0. Ledger 8 -> 8, verify PASS, paper verify PASS, cash $10,000,
0 positions, 0 pending entries, thesis evidence rows 0.

## 9. What would close the gap (operator decision, not done here)

1. Accept that production H without AI keeps the 0/6/2 decisions and only observes (forward shadow,
   D5 refresh, TP/Bear exits on any future position).
2. A new, preregistered deterministic D3/D4 rule set (evidence -> stage, evidence -> gap), validated
   against the oracle before use. That is a new research stage, explicitly out of scope here.
3. Re-allow a model in production. Contradicts the stated intent.

## 10. Code changes (local commit, not pushed)

* `docs/operations/h_autonomous_pipeline_v2.json` (+ `.sha256`): `H_V2_AUTONOMOUS_PIPELINE_V2_NO_AI`;
  no model, budget or authorization section; V1 kept unchanged as history.
* `h_auto/deterministic.py` (new): code-owned D3/D4 generators, D6 backtrace table.
* `h_auto/no_ai_guard.py` (new): runtime block of Claude subprocesses, LLM client imports, LLM hosts and
  the research runners' model entry points; wraps every evaluation item.
* `h_auto/clock.py` (new): ET day helper (was in `budget.py`).
* removed: `h_auto/budget.py`, `h_auto/providers.py`, `deploy/systemd/usb-h-research.{service,timer}`.
* `h_auto/executor.py`, `orchestrator.py` (`evaluation_step`, status without budget), `queue.py`
  (Candidate Evaluation Queue; BUDGET_DEFERRED legacy; D5/D6 summary kept), `contract.py`, `store.py`,
  `intake.py` (clock), `dev/run_h_v2_d7.py` (`auto-evaluate`; `auto-research` is an alias of it;
  `auto-authorize` removed).
* `deploy/systemd/usb-h-evaluation.{service,timer}` (new, 01:40 ET, same flock, no model env,
  MemoryMax 400M, IOSchedulingClass idle).
* frontend `trading-h` 자동 평가 panel: 마지막 자동 평가, 평가 결과, 최신 유니버스, 최신 가격 세션, SEC 갱신,
  후보 큐 갱신, 다음 자동 실행; no model/approval/budget wording.

Unchanged: D1, D2, D2.1 (and the trigger inclusion), MethodJudgement, D5_D2R_V1, D6, transitions,
paper engine, forward shadow, A/E/C code and data.

## 11. Tests

`test_h_no_ai_runtime.py` (20): backtrace pinned to D6 source, D3 has no interpretation and projects
refused, real package E2 states with sources, empty interpretation would REJECT (why it is not
produced), D4 UNKNOWN on positive/negative/neutral/no-evidence fixtures and on four stored bundles, C4
ceiling is the frozen function, guard blocks 7 AI routes and restores everything, an item reaching for
a model fails without a ledger write, no model wiring in the package or unit files, WATCH and REJECT
members keep their decision and enter nothing, new candidate not admitted, status shows evaluation
not budget. `test_h_autonomous_pipeline.py` and `test_h_production_intake.py` ported to the NO-AI seams
(synthetic D3/D4 fixtures drive D6 APPROVE -> PENDING_ENTRY -> BUY -> TP1 -> TP2, APPROVE->WATCH
downgrade exit, REJECT no entry). Full `strategy_h_v2` + `strategy_h0`: 1,961 passed, 1 skipped.
Frontend `trading-h` + futures position size: 18 passed.

## 12. Deployment plan (not executed)

Files: the backend files of §10, both contract files, the unit files. `usb-backend` restart once
(API reads the new status). Frontend rebuild for the panel (coordinate per the autonomous contract
§14; the 88ceb58 preview removal is still not in the running build).

Units: `systemctl disable --now usb-h-research.timer` (already disabled), remove
`usb-h-research.{service,timer}`, install `usb-h-evaluation.{service,timer}` **disabled**; enable only
after the RAM decision (§7). `usb-h-forward` unchanged. Delete nothing in `d7/auto/` (V1
`authorization.json` never existed on the server; any `spend.jsonl` is history).

Smoke: `auto-status` (mode DETERMINISTIC_NO_AI, ai_calls 0), `verify`, `paper-verify`, one
`auto-evaluate` by hand under `systemd-run -p MemoryMax=400M` while watching host PSI.

Rollback: restore the pre-deploy backup tar of the backend files, `systemctl disable --now
usb-h-evaluation.timer`, restart `usb-backend`. The ledger and queue are append-only; NO-AI items
write no ledger rows.
