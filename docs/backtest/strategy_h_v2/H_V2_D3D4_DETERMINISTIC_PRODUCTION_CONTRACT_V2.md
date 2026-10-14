# H-V2 D3/D4 Deterministic Production Contract V2 (result)

Date: 2026-10-14 KST. Not a research stage, not H-V3, not D8. Live AI calls: **0**. Not deployed,
not pushed. Contract: `docs/operations/h_d3d4_deterministic_v2.json` (`H_D3D4_DETERMINISTIC_PRODUCTION_V2`,
sha256 `65c631ca…4738a`), frozen in `8b55439` with source/rule/fixture hashes
(`h_d3d4_deterministic_v2.freeze.json`) **before** any replay or real-data run. Implementation `cce15b3`
(files equal the frozen hashes). One class-D fix after the replay: `c904426` (recorded in
`h_d3d4_deterministic_v2.class_d_fixes.json`, no rule changed). V1 (`9f31eb7`, `523ef09`, `39740f4`) unchanged.

Verdict: **H-V2 D3/D4 DETERMINISTIC PRODUCTION CONTRACT V2 = NEEDS REVISION.**

* R3.1 repaired: a missing BUSINESS label no longer becomes a REJECT. `thesis_unsupported` fired 0 times in
  the replay (V1: 7 packages read as unsupported, DORM WATCH->REJECT).
* Guidance transcription exists, is deterministic, keeps raw numbers, and its direction is the existing
  `classify_range_movement`. On the real corpus it found **0** directional items (see §3).
* APPROVE is **not** reachable from the deterministic pipeline, as the contract declared before running:
  D0 §L POSITIVE needs a priced-in conjunct and a confidence (AI-owned, D4 §N/§Q), and D0 §N1 also needs a
  material+timed catalyst and an invalidation condition (AI-owned, R3.5/R3.7). Operator decisions A-C did
  not cover these. Synthetic D3/D4 records carrying those fields do reach D6 APPROVE -> paper PENDING_ENTRY
  through the unchanged D6 and paper engine (test).

## 1. R3.1 V2

Priority: P1 explicit BUSINESS chunk -> P2 deterministic Item 1 fallback on the LATEST_10K text in the
package (heading grammar incl. `ITEM 1. Business.`, NBSP, `ITEM 1 -DESCRIPTION OF BUSINESS`, two-line
`ITEM 1` + `Business`; ends at Item 1A; table lines never headings) -> P3 none frozen -> P4
`BUSINESS_EVIDENCE_NOT_READY`, which refuses D3 (abstention, D6 NOT_DECISION_ELIGIBLE), never an empty
business model. Provenance: accession, form, filing date, source id, offsets, spanned evidence ids,
method `H_ITEM1_FALLBACK_V1`, sha256.

Replay (22 stored packages): P1 15, P2 5 (WEN, CWBC, MOFG, DORM, IDCC), NOT_READY 2 (LUV, AEYE: the
LATEST_10K is a Part III 10-K/A, `AMENDMENT_WITHOUT_ITEM_1`; no other filing is fetched).

## 2. Replay (`/home/tjd618/h_d3d4_v2/replay_v2contract.json`)

| Layer | Cases | V2 = V1 | V2 = historical |
|---|---|---|---|
| D3 | 22 | 20 (LUV, AEYE now refused) | - |
| D4 gap | 13 | 13 (UNKNOWN) | 5 |
| D6 | 13 | 11 | 11 |

D6 differences: DORM V1 REJECT -> V2 WATCH (= historical WATCH); AEYE V1 REJECT -> V2
NOT_DECISION_ELIGIBLE (class B: business evidence not ready); vs historical SPSC (B, model D4 refused) and
AEYE (B). IDCC stays REJECT on `valuation_fully_prices_the_positive_case` only.

## 3. Guidance parser V1

Transcriptions in the replay: 3 (all `GUIDANCE_PERIOD_UNKNOWN`), directional states 0; the stored Opus
outputs had 14 directional states in 5 issuers. Causes (all contract grammar, not fixed after seeing
results): outlook tables / bullets (SCCO, VRRM), period in the preceding sentence or heading (DORM), period
after the range (AEYE), list continuations, metric not named (IDCC). Class-D fix D-1: a connector was
absorbed into a label (`guidance to`). A V3 grammar (table rows under a period heading, trailing period)
would be needed for real coverage; it still would not open APPROVE by itself (§0).

## 4. Real data (production copy, server state 2026-10-14 15:17 KST; `v2_evidence.json`)

SCAN 941 s, 707 rows identical to the V1 SCAN, same 10 candidates; first AUTO_READY candidate preregistered
before any stage: **ADI**.

| | FG | VRRM | ADI (new) |
|---|---|---|---|
| D3 | OK, P1, revenue DETERIORATING, EPS PROFIT_TO_LOSS, catalyst 10-Q 2026-11-09 | OK, P1, 6 states, catalyst 10-Q 2026-11-09 | OK, P1, revenue ACCELERATING, op inc/margin/EPS IMPROVING, catalyst 10-K 2026-12-31 |
| D4 | UNKNOWN/UNKNOWN, ceiling MEDIUM, guidance non-price 0 | same | same |
| MethodJudgement | frozen table | frozen table | CODE_OWNED_V1 AUTO_READY P/FCF |
| D5 | P/B LOW, TP1 27.41 / TP2 29.81 / Bear 21.98 | EV/EBIT MEDIUM, TP1 3.25 / TP2 5.66 / Bear refused | P/FCF LOW, TP1 354.99 / TP2 422.90 / Bear 320.03 |
| D6 | **WATCH** -> WATCH | **WATCH** -> WATCH | **WATCH** (NONE -> WATCH) |

AI 0, cost $0, NOT_DECISION_ELIGIBLE 0/3. Ledger 8 -> 11, verify PASS; paper verify PASS, $10,000,
0 positions, 0 pending entries.

## 5. Memory

SCAN now reads companyfacts with `facts_cache.read_facts_lean` (only the canonical FIELD_SPECS concepts;
member-by-member decode, never the whole DOM). Equivalence: 2,816/2,816 stored documents give identical
`extract_companyfacts` output; SCAN intake 707/707 rows identical. Largest stored document: +48 MB -> +18 MB.
Combined scan+evaluation process max RSS 322 MB (V1) -> 314 MB. Per-stage SCAN re-measurement
(200-CIK cap, same harness and store as V1, `mem_scan3.json`): companyfacts load 257 -> 215 MB, E1-E3 batch
244 -> 226 MB, MethodJudgement screen 229 -> 212 MB, SEC fetch 195 -> 198 MB; **SCAN process max RSS
278 -> 246 MB**; same 10 candidates written. 1 GB host: **NEEDS OPTIMIZATION** (process stays above the
150-200 MB the host had available on 2026-10-14; RAM or scan partitioning remains a deployment condition).

## 6. Operations

Server (read-only check): LLM keys in `/root/usb/.env*` 0; `usb-h-evaluation` not installed;
`usb-h-research` unit files still present on the server (timer disabled) - removal is a deployment step.
Nothing deployed, nothing pushed.
