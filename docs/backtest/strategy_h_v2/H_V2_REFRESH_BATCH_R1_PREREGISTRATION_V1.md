# H-V2 Refresh Batch R1: Preregistration V1

Frozen 2026-10-13 KST, before any model call. **Live Opus calls made for this batch so far: 0.**
Execution needs operator approval, and it is blocked until the refresh executor below exists.

## 1. Queue as of the production cache (2026-10-12)

`refresh_queue.json` on the server (generated 2026-10-12T05:10:56Z, cache as of 2026-10-12) lists
`REFRESH_DUE = AEYE, FG, VRRM`. Each filing was checked against the accessions the thesis actually
read: an accession counts as read when at least one of its chunks is in the D2.1 package
`D2_1-20260928T072430Z` (data cutoff 2026-09-28T05:49Z) that D3 consumed. The index is frozen in
`docs/operations/h_thesis_evidence_v1.json`.

| Ticker | New filing (accession, filed) | Items (server cache) | In thesis evidence? | Research queue |
|---|---|---|---|---|
| FG | `0001934850-26-000095`, 2026-10-05 (accepted 10-06T00:13Z) | 2.02, 7.01 | No | **QUEUED, priority 1** (earnings) |
| VRRM | `0001193125-26-412497`, 2026-10-02 (accepted 10-03T00:40Z) | 5.02, 7.01, 9.01 | No | **QUEUED, priority 2** (management change) |
| AEYE | `0001104659-26-108940`, 2026-09-18 | 1.01, 2.03, 9.01 | **Yes** (2 chunks, and cited by the BR_C D3 output) | **EVIDENCE_ALREADY_IN_THESIS**, not queued |

AEYE's REFRESH_DUE is a scan artefact. `refresh_scan` compares the filing date with the decision
session (2026-09-16), not with the evidence cutoff (2026-09-28). Since 2026-09-16, AEYE has filed
only this 8-K and six Form 4s, and a Form 4 is not a material form. No AEYE evidence remains that
the thesis has not read, so re-researching it would spend model money on evidence it already used.
The deployed scan is not changed here: the research queue makes the distinction, and the refresh
queue keeps AEYE flagged. Including AEYE anyway is an operator decision, and it would add $12 to the
cap below.

## 2. Frozen batch parameters

| Item | Value |
|---|---|
| Sample | FG, VRRM, in that order. One candidate at a time, sequentially |
| Requested model | `claude-opus-5-5` |
| Model verification | `modelUsage[...].canonicalModel` must equal `claude-opus-5-5` on every call. On a mismatch the candidate is refused, nothing is written to the forward ledger, and there is no fallback (`--fallback-model` is never passed) |
| D3 contract | `d3_3_contract` / `schema_v2`, unchanged; at most 2 repairs (3 calls), `--max-budget-usd 2.00` per call |
| D4 contract | `d4_2_contract` (in-force leg), unchanged; at most 2 repairs, `--max-budget-usd 2.00` per call |
| D5 | `D5_D2R_V1`, `value_issuer(..., window_contract=D5_D2R_V1)` |
| D6 | `h_v2_d6_integrated_decision_v1`, `decide()` unchanged |
| Ledger write | `app.strategies.h_forward.transitions.record`. It refuses non-D7 transitions, a cause other than `MATERIAL_REFRESH` or `THESIS_INVALIDATED`, a D5 contract other than D2R, and a reused thesis version |
| Per-candidate reserve | $12.00 (D3 3 × $2 + D4 3 × $2), the contract-bounded worst case. The D4 Tier-B $1.60 D3 planning estimate is **not** used, because TG's D3 alone cost $2.086 |
| **Hard cap** | **$24.00** (2 × $12). A candidate is admitted only while the remaining budget is at least $12 |
| Expected spend | about $6: observed chained D3+D4 cost per ticker, median $3.03 and max $3.39 over 10 tickers (FG $2.16, VRRM $2.94 in Tier B / BR_C), and every recorded canonicalModel was `claude-opus-5-5` (0 mismatches) |
| Outcomes recorded | per ticker: `WATCH→APPROVE`, `WATCH→REJECT`, `WATCH→WATCH (UNCHANGED)`, or a refusal or failure with its reason. Nothing is predicted beforehand |

Cost source: every D3 and D4 attempt record under `data/runtime/strategy_h_v2/{d3_3,d4_b,d4_br_c,d4_1,d4_2}`
(D3: 28 attempts, D4: 19 analyses; 4 D4 runs ended `SCHEMA_VALIDATION_FAILED` at $1.99 to $2.43 with no
usable output).

A failure is paid for and counted. A `SCHEMA_VALIDATION_FAILED` or `MODEL_CALL_FAILED` leaves the
decision unchanged (WATCH stays WATCH) and is recorded, not retried outside the repair loop. The
Claude Code subscription session limit is a known failure mode (6 D3 records on 2026-09-29).

## 3. What a result does to the paper book

* A `WATCH→APPROVE` row written after the paper launch becomes `PENDING_ENTRY`. It enters at the next
  regular-session open, 1 slot (1/3 NAV).
* `WATCH→REJECT` and `WATCH→WATCH` take no paper action.
* The forward shadow keeps measuring T1 from the launch baseline. A new thesis version does not reset
  it.

## 4. Executor status: NOT READY (blocks execution even after approval)

The audit found no code path that performs a single-ticker refresh at a new decision session using
the frozen modules. These are the blockers:

1. **Evidence.** The production cache holds only submissions JSON. FG's and VRRM's 8-K documents must
   be fetched from SEC into the immutable D2.1 document cache, and a single-ticker D2.1 package must
   be built at a new cutoff. `run_strategy_h_v2_d2_1` fixes `BUNDLES_DIR` to the full run.
2. **D3 and D4 runners** fix `PACKAGES_DIR` to `D2_1-20260928T072430Z`, so a wrapper must call
   `research_one_v3` and `analyze_one_v2` with the new package.
3. **Prices.** `load_price_panel` reads the frozen HIST-V1 mirror, which ends 2026-09-16. D4's bundle
   and D5's panel need the sessions up to the new decision session, which only H's D7 store holds,
   and `_market_opens` is bound to the mirror.
4. **Fundamentals.** Companyfacts are dated 2026-09-28 and the root lists are hardcoded. FG's Q3
   XBRL will not exist before its 10-Q, so D5 can only re-anchor on price.
5. **D6.** `load_legs()` is hardcoded to the launch runs, so the wrapper must call
   `decide(project_d3, project_d4, project_d5)` directly.
6. **The writer exists** (`transitions.record`), and the paper book consumes its rows.

Building the executor is the next step: **H-V2 REFRESH EXECUTOR V1**. It reuses the frozen modules
and changes no rule. Its own risk is data vintage: the bundle must not mix a 10-13 filing with 09-28
market data without saying so. Until it exists, the decisions stand (FG WATCH, VRRM WATCH, AEYE
REJECT).
