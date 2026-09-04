# Stage 10B-4 Audit - Adoption Candidates Tab / Human Review Workflow

Audit date: 2026-09-04. READ-ONLY design audit. No application code, frontend,
backend, migration, database, scanner, research import, human decision,
simulation, or server process was changed or started by this audit. The three
runtime SQLite files were opened with `mode=ro` only.

Companion documents: `docs/ai/CURRENT_STATE.md`, `docs/ai/FROZEN_CONTRACTS.md`,
`docs/ai/SAFETY_RULES.md`, `docs/DESIGN_SYSTEM.md`, `docs/FRONTEND_V1.md`.

**Superseded in part by Stage 10B-4.1 (2026-09-04).** The classification rule,
thresholds, warnings, rank delta and data sources below are still current. The
`UI Layout` table columns and the `Detail Layout` section list are not: the
table now leads with `추천 순위`, and the drawer is 헤더 → 회사 설명 → 판단 요약
→ 단기 가격 상태 → 채택 이유 → 순위 변화 이유 → 위험 / 주의 → 최종 결정, with
근거 자료 / 무효화 조건 / 미확인 raw 목록 removed from the drawer only. See
`docs/FRONTEND_V1.md` and `docs/ai/CURRENT_STATE.md` for the current layout.

Every number in the Run 2 Simulation section was computed from
`data/runtime/usb_real_market_review.sqlite3`, `gpt_analyses.id = 2` /
`scanner_runs.id = 2`. Nothing is remembered or estimated.

---

## Verdict

| Question | Verdict |
| --- | --- |
| Third tab feasibility | **YES, no new tab system needed.** `frontend/components/section-tabs.tsx` already renders `analysisTabs` from a plain array. Adding `{ href: "/adoption", label: "채택 후보" }` plus `/adoption` in the `종목 분석` entry of `navigationItems` (`components/app-shell.tsx:16`) is the entire navigation change. |
| Classification feasibility | **YES, fully deterministic.** Every input (GPT 5 scores, evidence score, catalyst duration, unknown_fields, catalyst source presence, quant rank, quant raw metrics) is already persisted and immutable per analysis. No new field and no GPT call is required. |
| Backend/API need | **YES, one new read-only route.** Classification is business logic and must not live in the frontend (`docs/ai/FROZEN_CONTRACTS.md` - "The backend is the only source of truth"). Recommended: `GET /api/v1/research/adoption`. This moves the OpenAPI path count from 30 to 31, which is a documented frozen figure and must be updated in the same change. |
| DB migration need | **NO.** Classification is a pure function of already-persisted immutable rows. No new column, table, or Alembic revision. |
| HumanDecision compatibility | **COMPATIBLE, no contract change.** `채택 후보` count is unbounded; `APPROVE` stays capped at 2 per analysis. The two are different objects and the UI must say so on screen. |
| Design compatibility | **YES, zero new tokens.** Every element needed (`section-tabs`, `table-wrap`, `StatusBadge` + `tone-*`, `InfoTooltip`, `btn-action-secondary-compact`, `Drawer`, `ResearchDecisionControl`, `EmptyState`) already exists and already carries Dark/Light and focus-visible behaviour. |

### Blocking defect found during this audit

**`risk_score` polarity is inverted in the frontend today.**

- Contract (`backend/app/research/domain.py:81`, `backend/app/research/prompt.py`
  rules text, `docs/GPT_RESEARCH.md:31`): *"risk_score is higher when trading
  risk is lower / risk-reward is better (100 safest, 0 highest risk)."*
- Frontend (`frontend/lib/display.ts`, `researchRiskLabel` / `researchRiskTone`;
  `frontend/app/research/page.tsx` column `위험도` + tooltip *"높을수록
  위험합니다"*) treats a **higher** `risk_score` as **more** dangerous.

Consequence on real Run 2 data: NVDA (`risk_score 67`, actually the second
safest name in the pool) is rendered as `높음` in amber, and SPCX
(`risk_score 31`, the single riskiest name) is rendered as `보통` in neutral.
The task brief's own example (`NVDA 주의: 위험도 높음`, `TSLA 위험 47 · 보통`)
reproduces the inverted reading, which is evidence of how far the defect has
already propagated into operator judgement.

This audit designs the adoption filter against the **backend contract**
(higher = safer), because the persisted data and the prompt that produced it
both use that direction. Any implementation must therefore either fix
`display.ts` in the same stage or the new tab will contradict the existing
GPT 분석 tab on the same screen group. This is a correctness fix, not a
tuning change, so it does not violate `docs/ai/SAFETY_RULES.md` section 12 -
but it touches an existing shipped screen and needs to be stated explicitly in
the implementation task.

---

## Proposed Workflow

```text
Quant Scanner (quant_v0, frozen)
  -> ScannerRun + ScannerCandidate            persisted quant truth
  -> ResearchPromptService                    manual prompt
  -> GPTImportService                         immutable GPTAnalysis + evidence_v0
  -> AdoptionFilter (adoption_filter_v0)      NEW, deterministic, read-only
       ADOPTION_CANDIDATE | REVIEW_REQUIRED | EXCLUDED  + strengths + warnings
  -> 채택 후보 tab                             human review surface
  -> Human reads why-candidate / why-caution
  -> HumanDecisionService APPROVE | REJECT     unchanged, max 2 APPROVE
  -> StrategyV0Engine -> SessionPolicy -> RiskEngine -> SimBroker
```

The filter sits strictly between research import and the human decision. It
never writes a decision, never reorders the persisted GPT rank, never
recomputes a quant or evidence score, and never gates Strategy or Risk. It is a
presentation-layer triage of already-final data.

Count is not fixed. On any given day the filter may return 0, 1, 2, 3, or more
adoption candidates. Fixed TOP2 auto-selection is explicitly rejected.

---

## Classification Rule

Version string: `adoption_filter_v0`.

Four dimensions, each resolved to `PASS` / `MARGINAL` / `FAIL`:

| Dimension | Field | PASS | MARGINAL | FAIL |
| --- | --- | --- | --- | --- |
| Overall | `overall_score` | `>= 70` | `60 .. 69` | `< 60` |
| Catalyst | `catalyst_score` | `>= 75` | `60 .. 74` | `< 60` |
| Momentum | `momentum_score` | `>= 60` | `40 .. 59` | `< 40` |
| Risk ceiling | `risk_score` (higher = safer) | `>= 30` | none | `< 30` |

Status resolution, in order:

1. Any dimension `FAIL` -> `EXCLUDED`, **except** the near-miss rule below.
2. All four `PASS` -> `ADOPTION_CANDIDATE`.
3. No `FAIL` and at least one `MARGINAL` -> `REVIEW_REQUIRED`.

**Near-miss promotion.** Exactly one dimension `FAIL`, every other dimension
`PASS`, and the failing value is within `5` points of its own FAIL boundary ->
`REVIEW_REQUIRED` instead of `EXCLUDED`.

Why these thresholds, and why they are not invented numbers:

- `70 / 60`, `75 / 60`, `60 / 40`, `30 / 50 / 70` are the **already shipped
  display band edges** in `frontend/lib/display.ts`
  (`researchOverallLabel` 60/70/80/90, `researchStrengthLabel` 40/60/75/90,
  `researchRiskLabel` 30/50/70). Reusing them means the status chip can never
  disagree with the score label printed in the same row. No new band vocabulary
  is introduced anywhere in the product.
- Catalyst carries the highest bar (`강함` and above) because this is a
  Catalyst + Momentum short-horizon system (`docs/V1_FINAL_SPEC.md`,
  `docs/STRATEGY_V0.md`). Momentum carries a lower bar (`양호` and above)
  because Strategy V0 independently re-tests momentum the next morning through
  the premarket gap gate (`+2% .. +15%`), the opening range breakout, and the
  VWAP condition. Double-gating momentum here would silently narrow a frozen
  strategy.
- Risk is a **ceiling only**, never a quality bar. The brief requires
  "risk가 치명적이지 않음" for adoption and "risk elevated" as a warning; the
  gate therefore only removes the bottom safety band, and elevated risk becomes
  a warning that stays visible on an adoption candidate.
- The `5`-point near-miss tolerance is the single most tunable parameter in the
  rule and is stated as such. Justification: GPT scores are integers on a 0-100
  scale with no calibration guarantee, and 5 points is half of the narrowest
  display band in use (10 points). It exists so that a name failing one axis by
  a rounding-scale margin reaches a human instead of disappearing.

Explicitly **not** gates, per the brief:

- Low `evidence_confidence` alone never excludes.
- Low `fundamental_score` alone never excludes (it is not a dimension at all;
  it only produces a strength line).
- Low quant rank never excludes.

---

## Hard Gates

A hard gate is a condition that makes `ADOPTION_CANDIDATE` impossible.

| Gate | Rule | Rationale | Contract support |
| --- | --- | --- | --- |
| `GATE_OVERALL` | `overall_score >= 70` | The composite GPT judgement must at least be `좋음`. | `GPTCandidateResult.overall_score`, persisted |
| `GATE_CATALYST` | `catalyst_score >= 75` | Core axis of the strategy. | persisted |
| `GATE_MOMENTUM` | `momentum_score >= 60` | Core axis of the strategy. | persisted |
| `GATE_RISK_CEILING` | `risk_score >= 30` | Removes only the catastrophic-risk band. Direction per backend contract: higher = safer. | persisted |

All four are computable today from `gpt_candidate_analyses`. No gate requires
sources, narrative text, market data, or any new call.

Deliberately rejected as hard gates:

- Evidence floor. `evidence_v0` is a structural coverage score, not a truth
  probability (`docs/ai/FROZEN_CONTRACTS.md`). Making it a gate would turn a
  documentation-quality metric into a trading filter. Run 2 evidence ranges
  25..65, so any evidence gate above 40 would have emptied the tab entirely.
- Quant rank floor. The brief forbids it, and Run 2 shows exactly why: the
  quant #7 name (MSFT) is GPT #4, and the quant #2 name (SPCX) is GPT #6.
- `overnight_suitability`. Strategy V0 already gates overnight independently in
  the closing review. Duplicating it here would pre-empt a frozen rule.

---

## Warning Rules

A warning never changes the status. It is attached to the row and shown in the
detail. All are computable from persisted data.

| Code | Condition | Display (Korean) | Source |
| --- | --- | --- | --- |
| `LOW_EVIDENCE` | `evidence_confidence < 40` | `근거 신뢰도 낮음 {n}` | `gpt_candidate_analyses.evidence_confidence` |
| `ELEVATED_RISK` | `risk_score < 50` | `위험 지표 높음 {n}` | same table |
| `RANK_DIVERGENCE` | `abs(quant_rank - gpt_rank) >= 3` | `Quant/GPT 순위 괴리 {+n}` | scanner + gpt rank |
| `UNKNOWN_FIELDS` | `len(unknown_fields) >= 3` | `미확인 항목 {n}건` | `unknown_fields_json` |
| `UNKNOWN_CATALYST_DURATION` | `catalyst_duration == "UNKNOWN"` | `재료 지속성 확인 불가` | same table |
| `NO_CATALYST_SOURCE` | no `gpt_sources` row with `claim = 'catalyst'` | `촉매 출처 없음` | `gpt_sources` |
| `CATALYST_MOMENTUM_CONFLICT` | `abs(catalyst_score - momentum_score) >= 30` | `촉매·모멘텀 불일치 {a}/{b}` | same table |
| `LOW_RVOL` | `raw.rvol < 1.2` | `거래량 관심 부족 RVOL {n}` | `scanner_candidates.score_components_json.raw.rvol` |
| `WEAK_QUANT_RANK` | `quant_rank > floor(pool * 2 / 3)` | `Quant 하위권 #{r}/{n}` | scanner rank |

Notes:

- `RANK_DIVERGENCE` at `>= 3` is calibrated to a pool of at most 8 (Run 2 has
  7). If the TOP-N size ever changes, this threshold must be restated as a
  fraction of pool size, not left at 3.
- `NO_CATALYST_SOURCE` is the only warning that touches `gpt_sources`. The list
  endpoint does not currently join that table, so the new endpoint has to add
  one aggregate query (`count(*) filter claim='catalyst' group by candidate`).
  This is cheap and read-only.
- `unknown_fields` is already normalized (deduplicated, sorted) at import, so
  the count is stable.

---

## Rank Delta

```text
delta = quant_rank - gpt_rank
```

| delta | Meaning | Display | Tone |
| --- | --- | --- | --- |
| `> 0` | GPT promoted the symbol above its quant rank | `↑{delta}` | success (green) |
| `< 0` | GPT demoted the symbol below its quant rank | `↓{abs(delta)}` | warning (amber) |
| `= 0` | agreement | `유지` | neutral |
| `quant_rank == null` | quant candidate row missing | `순위 없음` | neutral |

Safely derivable in the frontend today: `ResearchCandidate` already carries
`quant_rank: number | null` and `gpt_rank: number`
(`frontend/types/api.ts:13`). **However**, the delta must still be computed
backend-side and returned, for one reason: `rank_direction` participates in the
classification explanation and in the `RANK_DIVERGENCE` warning, and the
frontend must not own any part of a business rule. The frontend renders
`rank_delta` and `rank_direction` as given.

The `null` case is real and must be handled: `research_candidate()` in
`backend/app/api/service.py:72` resolves `quant_rank` through
`session.get(ScannerCandidate, ...)` and returns `None` if the row is gone.

Semantic caution: green for "GPT raised it" is a **directional** signal, not a
quality signal. On Run 2, both `↑3` names (NVDA, MSFT) also carry
`WEAK_QUANT_RANK` and `LOW_RVOL`. The UI must therefore never let the green
arrow stand alone; the warning chips sit in the same row.

---

## Explanation Generation

**Question asked: can a deterministic explanation be built from raw data alone?
Answer: mostly yes, with one explicit limit.**

No new GPT call is added in this stage.

### Derivable (rule-based, backend)

Strength lines, emitted in fixed order, each only when its condition holds:

```text
catalyst_score >= 90        -> "촉매 {n} 매우 강함"
catalyst_score >= 75        -> "촉매 {n} 강함"
momentum_score >= 90        -> "모멘텀 {n} 매우 강함"
momentum_score >= 75        -> "모멘텀 {n} 강함"
fundamental_score >= 90     -> "기업 체력 {n} 매우 강함"
risk_score >= 70            -> "위험 지표 {n} 낮음"
raw.rvol >= 1.5             -> "거래량 {n}배 활발"
delta == 0 and gpt_rank<=2  -> "Quant·GPT 모두 #{r}"
evidence_confidence >= 70   -> "근거 {n} 높음"
```

Cap at 4 lines for the table, full list in the detail. The brief asks for 2-4
strengths and 1-3 warnings; on Run 2 every classified symbol produced at least
one strength and between 3 and 8 warnings, so the table must truncate warnings
with a `+{n}` affordance rather than assume the list is short.

Rank-change explanation (detail only), fully derivable:

```text
if delta > 0:
  "순위 상승 이유"  = GPT dimensions in their top band, with values
  "Quant 약점"      = quant raw metrics below their display band, with values
if delta < 0:
  "순위 하락 이유"  = GPT dimensions in FAIL/MARGINAL, with values
  "Quant 강점"      = quant raw metrics above their display band, with values
```

Worked example, NVDA, from persisted Run 2 values only:

```text
순위 상승 이유:  촉매 97 매우 강함 · 기업 체력 98 매우 강함 · 모멘텀 82 강함
Quant 약점:      Quant #5 · 거래량 강도 1.07배(보통) · 시장 대비 +0.47%(제한적)
```

Both halves come from `gpt_candidate_analyses` and
`scanner_candidates.score_components_json.raw` respectively, through the
existing display band functions. Nothing is inferred.

### One-line conclusion

Derivable as a fixed template:

```text
"{rank clause}. {strength clause}. {caution clause}"
```

TSLA on Run 2 renders as:

```text
"Quant·GPT 모두 #1인 강한 촉매·모멘텀 후보. 다만 근거 신뢰도와 위험 지표를 확인해야 합니다."
```

### NOT derivable - explicit limit

The brief's example conclusion names **규제 리스크** (regulatory risk). That
category exists only inside `risk_summary`, a free-text narrative field. There
is no risk-category enum, no tag list, and no structured risk taxonomy anywhere
in `gpt_research_v0`. Extracting it would require either text classification
(prohibited: `docs/FRONTEND_V1.md` - the frontend never extracts metadata from
GPT narrative) or a new GPT call (prohibited this stage).

Resolution: the deterministic conclusion names only measured dimensions
(`근거 신뢰도`, `위험 지표`, `촉매`, `모멘텀`, `순위`). The named risk category
stays where it already is - in the `위험 요인` narrative, reachable in one click
from the adoption detail. Do not fabricate a category name.

---

## Run 2 Simulation

Source: `usb_real_market_review.sqlite3`, `scanner_runs.id = 2`
(trading date 2026-09-03, provider `KIWOOM_REAL`, universe 10, excluded 3,
candidates 7), `gpt_analyses.id = 2` (`top8_research_v0`, `gpt_research_v0`,
`evidence_v0`, OpenAI / GPT-5.6 Sol, imported 2026-09-04T04:00:39Z),
7 candidates, 0 human decisions.

**Correction to the brief:** Run 2 has **7** candidates, not 8, and does
**not** contain `MU`. `MU` was in Run 1 (`scanner_runs.id = 1`, 8 candidates)
and was excluded from Run 2's eligible pool. The brief's list of seven symbols
is otherwise exactly right.

Raw persisted inputs:

| Symbol | Quant # | GPT # | quant_score | overall | catalyst | fundamental | momentum | risk (higher=safer) | evidence | RVOL |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TSLA | 1 | 1 | 0.7317 | 91 | 98 | 70 | 98 | 47 | 25 | 1.83 |
| SPCX | 2 | 6 | 0.4897 | 68 | 47 | 79 | 99 | 31 | 31 | 1.16 |
| AVGO | 3 | 5 | 0.1060 | 70 | 95 | 95 | 38 | 53 | 36 | 2.98 |
| META | 4 | 3 | 0.0769 | 77 | 64 | 88 | 82 | 59 | 31 | 1.28 |
| NVDA | 5 | 2 | -0.1808 | 89 | 97 | 98 | 82 | 67 | 55 | 1.07 |
| AAPL | 6 | 7 | -0.4556 | 59 | 35 | 93 | 65 | 69 | 28 | 0.93 |
| MSFT | 7 | 4 | -0.7679 | 75 | 80 | 96 | 62 | 82 | 65 | 1.00 |

`adoption_filter_v0` result: **3 채택 후보 / 2 검토 필요 / 2 제외.**

| Symbol | Status | Quant -> GPT | Gate detail | Reason (1-3 lines) |
| --- | --- | --- | --- | --- |
| **TSLA** | `채택 후보` | `#1 → #1` `유지` | overall 91 P, catalyst 98 P, momentum 98 P, risk 47 P | Both engines rank it first. Highest catalyst and momentum in the pool, RVOL 1.83. Warnings: `근거 신뢰도 낮음 25`, `위험 지표 높음 47`, `미확인 항목 3건`. |
| **NVDA** | `채택 후보` | `#5 → #2` `↑3` | overall 89 P, catalyst 97 P, momentum 82 P, risk 67 P | GPT lifted it three places on catalyst 97 and fundamental 98 against a middling quant profile. Warnings: `Quant/GPT 순위 괴리 +3`, `미확인 항목 3건`, `거래량 관심 부족 RVOL 1.07`, `Quant 하위권 #5/7`. |
| **MSFT** | `채택 후보` | `#7 → #4` `↑3` | overall 75 P, catalyst 80 P, momentum 62 P, risk 82 P | Passes every gate but by the smallest margins in the pool, off the weakest quant profile (quant_score -0.77, RVOL 1.00). Safest risk score (82) and best evidence (65). Warnings: `Quant/GPT 순위 괴리 +3`, `거래량 관심 부족 RVOL 1.00`, `Quant 하위권 #7/7`. |
| **META** | `검토 필요` | `#4 → #3` `↑1` | catalyst 64 **MARGINAL**, others PASS | Only the catalyst axis falls short (`양호`, below the `강함` bar). GPT itself listed "direct September 3 company-specific positive catalyst" as unknown. Warnings: `근거 신뢰도 낮음 31`, `미확인 항목 3건`, `촉매 출처 없음`. |
| **AVGO** | `검토 필요` (near-miss) | `#3 → #5` `↓2` | momentum 38 **FAIL** (boundary 40, gap 2), others PASS | Strongest volume surge in the pool (RVOL 2.98) and catalyst 95, but post-earnings price weakness pushes momentum just below the floor. Promoted by the 5-point near-miss rule so a human sees the conflict instead of losing it. Warnings: `근거 신뢰도 낮음 36`, `촉매 출처 없음`, `촉매·모멘텀 불일치 95/38`. |
| **SPCX** | `제외` | `#2 → #6` `↓4` | catalyst 47 **FAIL** (13 below boundary), overall 68 MARGINAL | Momentum 99 with no identifiable catalyst; GPT flagged "direct September 3 core catalyst" and "official explanation for September 3 abnormal trading activity" as unknown, `catalyst_duration = UNKNOWN`, no catalyst source, and the lowest safety score in the pool (31). Carries 8 warnings, the most of any name. Not promoted: the failing axis is far from its boundary and overall is not PASS. |
| **AAPL** | `제외` | `#6 → #7` `↓1` | overall 59 **FAIL**, catalyst 35 **FAIL** | Two independent gate failures. GPT found no direct September 3 Apple-specific catalyst; fundamental 93 is strong but fundamental is deliberately not a gate. |

### Is the threshold set too wide or too narrow?

Sensitivity, recomputed on the same Run 2 data (`A` = 채택 후보,
`R` = 검토 필요, `X` = 제외):

| Scenario | TSLA | NVDA | META | MSFT | AVGO | SPCX | AAPL | A/R/X |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **base (proposed)** | A | A | R | A | R | X | X | **3/2/2** |
| catalyst PASS 85 | A | A | R | R | R | X | X | 2/3/2 |
| momentum PASS 70 | A | A | R | R | R | X | X | 2/3/2 |
| overall PASS 80 | A | A | R | R | X | X | X | 2/2/3 |
| risk gate 50 | R | A | R | A | R | X | X | 2/3/2 |
| catalyst PASS 65 | A | A | R | A | R | X | X | 3/2/2 |
| momentum PASS 50 | A | A | R | A | R | X | X | 3/2/2 |

Reading:

1. The rule is **stable**. Loosening catalyst to 65 or momentum to 50 changes
   nothing, which means the base thresholds are not sitting on top of a cliff.
2. All tightening scenarios move exactly one name: **MSFT**. MSFT is the
   marginal third candidate under every variation, which is the honest answer -
   it passes on GPT judgement and fails on market participation (RVOL 1.00,
   quant #7 of 7). The warning set already says exactly that, so the operator
   is not misled by its `채택 후보` chip.
3. `risk gate 50` would demote TSLA, the pool's strongest name, on a risk score
   of 47. That is the wrong trade for a catalyst-momentum system and confirms
   that risk belongs as a ceiling (30) plus a warning (50), not as a quality
   bar.
4. 3 adoption candidates against a hard cap of 2 approvals is a healthy funnel:
   the operator makes a real choice rather than rubber-stamping a list that
   already matches the cap.

Conclusion: the proposed thresholds are neither too wide (2 of 7 excluded
outright, both for substantive reasons) nor too narrow (the tab is never empty
on this data). Recommend shipping `adoption_filter_v0` as specified and
revisiting only after several real runs.

---

## UI Layout

Route: `/adoption`. Sidebar group `종목 분석`. Tab label `채택 후보`,
third position after `후보 종목` and `GPT 분석`.

### Exclusion display - recommendation

**Option 3.** Default view shows `채택 후보` + `검토 필요`; `제외` is behind a
compact toggle (`제외 {n}개 보기`), rendered muted when expanded.

Rationale: the tab's purpose is review efficiency, so excluded names must not
consume default attention. But on Run 2 the excluded set contains SPCX, a name
that is quant #2 with momentum 99 - an operator who cannot see why it was
dropped will not trust the filter. Option 1 hides that answer permanently;
Option 2 makes a 7-row table where 2 rows are noise on every load. Option 3
keeps the truth reachable at zero default cost. The toggle state is local UI
state only, never persisted or sent to the backend.

### Table columns (final)

| # | Column | Content | Component / token |
| --- | --- | --- | --- |
| 1 | 상태 | `채택 후보` / `검토 필요` / `제외` | `StatusBadge` with explicit `tone` |
| 2 | 종목 | symbol button, opens detail | existing underlined symbol button pattern from `/research` |
| 3 | Quant → GPT | `#5 → #2` | plain text, `text-foreground-secondary` for the quant half |
| 4 | 순위 변화 | `↑3` / `↓4` / `유지` | `tone-text-success` / `tone-text-warning` / `tone-text-neutral` |
| 5 | 핵심 강점 | top 2 strength lines, truncated | `truncate` + `title` attribute, same as the `회사명` cell in `/candidates` |
| 6 | 핵심 주의 | top 2 warning lines + `+{n}` | same truncation pattern |
| 7 | 상세 | `상세보기` | `btn-action-secondary-compact` |
| 8 | 최종 결정 | `채택` / `거절` / `미결정` | `StatusBadge`, labels via `formatDecisionStatus` |

Header row uses the existing neutral table header surface; `InfoTooltip` on
`상태`, `순위 변화`, and `핵심 주의`, matching the `/research` header pattern.

Sort order: `채택 후보` -> `검토 필요` -> `제외`, and within each group by
`gpt_rank` ascending. Backend returns rows already sorted; the frontend does not
re-sort.

### Status tone mapping (no new tokens)

| Status | Tone class | Reason |
| --- | --- | --- |
| `채택 후보` | `tone-success` | matches `docs/DESIGN_SYSTEM.md` "Success Green: 정상, 연결, 완료, 채택" |
| `검토 필요` | `tone-warning` | matches "Warning Amber: 확인 필요" |
| `제외` | `tone-neutral` | muted, informational, not a failure |
| `채택` (human) | `tone-success` | existing `StatusBadge` inference for `APPROVE` |
| `거절` (human) | `tone-danger` | existing inference for `REJECT` |
| `미결정` | `tone-neutral` | existing default |

Note the deliberate collision: `채택 후보` and `채택` are both green. They are
distinguished by column position (column 1 = system classification, column 8 =
human decision) and by the header counter described under HumanDecision
Contract. If review shows this is still confusable, the fallback is
`tone-info` (blue) for `채택 후보`, which stays inside the existing token set
and reads as "system selected" rather than "approved".

### Header metadata line

Reuse the `/candidates` compact metadata pattern:

```text
기준 거래일 2026-09-03 · 분석 완료 {kstTime} · 분류 대상 7종목 ·
채택 후보 3 · 검토 필요 2 · 제외 2 · adoption_filter_v0
```

### Responsive

Reuse `table-wrap` (`overflow-x-auto`) exactly as `/candidates` and `/research`
do. Column priority on narrow screens follows the existing precedent of hiding
the label suffix rather than the value (`hidden xl:inline` in
`ResearchScore` / the RVOL cell):

- always visible: 상태, 종목, 순위 변화, 상세, 최종 결정
- `hidden lg:table-cell`: 핵심 강점, 핵심 주의 (their content is fully repeated
  in the detail drawer, so hiding them loses nothing)
- `Quant → GPT` collapses to `#5→#2` without spaces below `lg`

No new responsive mechanism, no card-per-row rewrite.

### Empty states (`EmptyState`, existing component)

| Condition | Title | Description |
| --- | --- | --- |
| No `IMPORTED` analysis for the run | `GPT 분석이 완료되면 채택 후보가 생성됩니다.` | `후보 종목 화면에서 프롬프트를 복사해 분석을 먼저 진행하세요.` |
| Analysis exists, 0 adoption candidates, 0 review required | `현재 기준을 충족한 채택 후보가 없습니다.` | `제외 {n}개 보기로 각 종목의 탈락 사유를 확인할 수 있습니다.` |
| 0 adoption candidates, >=1 review required | `채택 후보는 없지만 검토가 필요한 종목이 있습니다.` | rendered as a compact banner above the table, not instead of it |
| No completed scanner run | reuse `/candidates` wording | - |

Human decisions never alter the classification. A rejected adoption candidate
still displays `채택 후보` in column 1 and `거절` in column 8. Classification is
system truth; the decision is human truth; neither overwrites the other.

---

## Detail Layout

Presentation: the existing `Drawer` component (right side, 640px, Escape to
close), identical to `/research`. Not a new page, not a modal.

Sections, in order:

1. **Header** - symbol, company name, status badge, `Quant #n → GPT #n` and the
   delta chip.
2. **판단 요약** - the four classification dimensions as `{score} · {band label}`
   using the existing display band functions, plus `근거 {evidence} · {band}`.
   One line, four to five items, no chart.
3. **한 줄 결론** - the deterministic template sentence.
4. **왜 후보인가** - full strength list (green bullets).
5. **왜 주의해야 하나** - full warning list (amber bullets), each with the
   measured value that triggered it.
6. **순위 변화 근거** - the two-part rank-change explanation (GPT reasons /
   Quant counter-evidence), only when `delta != 0`.
7. **핵심 촉매** - `catalyst_summary`, clamped to roughly 3 lines with a
   `더 보기` expander.
8. **무효화 조건** - `invalidation_summary`, same clamping.
9. **분석 원문 보기** - `btn-action-secondary`, links to
   `/research?symbol={symbol}` (see Frontend Plan).
10. **최종 결정** - drawer footer, the existing `ResearchDecisionControl`
    component unchanged, plus the approval-count helper line.

Deliberately **not** repeated here (they live in the `/research` drawer and
would be pure duplication): 회사 개요, 거래소/업종/시가총액, 최근 주가 흐름
(daily OHLC, premarket, postmarket), the full source list, 전략 참고
(catalyst_duration / trailing_profile / overnight_suitability), and the raw
`unknown_fields` list. `위험 요인` narrative is also not repeated; the adoption
detail shows the measured risk warnings and links out for the prose.

---

## Data Sources

Everything the design needs, and where it already exists:

| Field | Table / file | Notes |
| --- | --- | --- |
| `overall/catalyst/fundamental/momentum/risk_score` | `gpt_candidate_analyses` | persisted at import, immutable |
| `evidence_confidence` | `gpt_candidate_analyses` | computed once by `evidence_v0` at import |
| `catalyst_duration`, `overnight_suitability`, profiles | `gpt_candidate_analyses` | |
| `unknown_fields` | `gpt_candidate_analyses.unknown_fields_json` | normalized at import |
| catalyst source presence | `gpt_sources` where `claim = 'catalyst'` | new aggregate join, read-only |
| `gpt_rank` | `gpt_candidate_analyses` | validated unique and contiguous at import |
| `quant_rank`, `quant_score` | `scanner_candidates` | nullable in the API layer, must be handled |
| `raw.rvol`, `raw.momentum`, `raw.relative_strength`, `raw.dollar_volume` | `scanner_candidates.score_components_json.raw` | already surfaced by `candidate_dict()` |
| `human_decision` | `human_decisions` | already joined by `APIQueryService.analysis()` |
| display band labels/tones | `frontend/lib/display.ts` | reused verbatim, no new bands |

**Nothing requires** market data, a Kiwoom call, minute bars, the market-context
service, `strategy_states`, `execution_*`, or `shadow_trades`. The endpoint is
therefore not affected by the open minute-coverage or SimBroker blockers in
`docs/ai/CURRENT_STATE.md`.

---

## Backend Plan

New module: `backend/app/research/adoption.py`.

```text
adoption.py
  ADOPTION_FILTER_VERSION = "adoption_filter_v0"
  AdoptionStatus   StrEnum: ADOPTION_CANDIDATE | REVIEW_REQUIRED | EXCLUDED
  GateResult       frozen dataclass: code, dimension, value, level, threshold
  WarningResult    frozen dataclass: code, value
  AdoptionResult   frozen dataclass: symbol, status, gates, warnings,
                                     strengths, rank_delta, rank_direction,
                                     summary_line
  AdoptionFilterConfig  frozen dataclass holding every threshold,
                        validated in __post_init__ (PASS >= MARGINAL floor)
  classify(candidate_scores, quant_snapshot, has_catalyst_source, pool_size,
           config) -> AdoptionResult      # pure function, no session, no I/O
```

Placement rationale: thresholds are strategy-adjacent constants and belong in a
versioned config object next to the other frozen configs, exactly as
`quant_v0`, `risk_v0`, and `evidence_v0` are structured. `classify` takes plain
values, so it is unit-testable without a database, matching
`backend/app/research/evidence.py`.

New service method on `APIQueryService`
(`backend/app/api/service.py`): `adoption(analysis: GPTAnalysis) -> dict`.
It loads candidates, their quant rows, the catalyst-source counts, and the
decisions, calls `classify` per candidate, sorts, and serializes. It performs no
writes.

New route, declared **before** `/research/{analysis_id}/...` so the literal
segment cannot be shadowed by the int path parameter:

```text
GET /api/v1/research/adoption?analysis_id={optional}
```

Defaults to the latest `IMPORTED` analysis, same selection logic as
`/research/latest`. Response shape:

```json
{
  "analysis": { "id": 2, "scanner_run_id": 2, "trading_date": "2026-09-03", "...": "..." },
  "filter_version": "adoption_filter_v0",
  "thresholds": { "overall": [70, 60], "catalyst": [75, 60], "momentum": [60, 40], "risk_floor": 30, "near_miss_tolerance": 5 },
  "counts": { "adoption_candidate": 3, "review_required": 2, "excluded": 2, "total": 7 },
  "approval": { "approved_count": 0, "max_approvals": 2 },
  "candidates": [
    {
      "symbol": "NVDA", "status": "ADOPTION_CANDIDATE",
      "quant_rank": 5, "gpt_rank": 2, "rank_delta": 3, "rank_direction": "UP",
      "gates": [{ "code": "GATE_CATALYST", "value": 97.0, "level": "PASS", "threshold": 75 }],
      "strengths": [{ "code": "STRONG_CATALYST", "value": 97.0 }],
      "warnings": [{ "code": "RANK_DIVERGENCE", "value": 3 }],
      "summary_line": "...",
      "human_decision": null
    }
  ]
}
```

Contract rules this respects:

- Codes are stable English enums; Korean labels are frontend display only
  (`docs/ai/FROZEN_CONTRACTS.md`, UI/Display contracts).
- Analytical float scores stay JSON numbers; no `Decimal` is involved.
- The router stays a thin adapter and does not itself classify.
- Errors follow the existing `{"error": {...}}` envelope; a missing analysis is
  404 with the same message the frontend already special-cases.

Frozen figures that must be updated in the same change:
`docs/ai/FROZEN_CONTRACTS.md` and `docs/API_V1.md` both record
"29 routes + `/health` = 30 OpenAPI paths". Adding this route makes it 30 + 1 =
31. Leaving the old count in place would be a documentation defect.

Backend tests to add (new file `backend/tests/test_adoption_filter_stage10b4.py`):

1. Each gate boundary exactly on and one below its threshold.
2. Near-miss promotion at gap 0, gap 5, gap 6 (6 must not promote).
3. Two simultaneous FAILs never promote.
4. `quant_rank = None` produces `rank_delta = None`, `rank_direction = "UNKNOWN"`,
   and no `RANK_DIVERGENCE` / `WEAK_QUANT_RANK` warning.
5. Determinism: classifying the same fixture twice returns identical output.
6. A frozen fixture reproducing Run 2's seven symbols asserts exactly
   `TSLA/NVDA/MSFT = ADOPTION_CANDIDATE`, `META/AVGO = REVIEW_REQUIRED`,
   `SPCX/AAPL = EXCLUDED`. Fixture values only, no live DB.
7. Route test: response sorted by status group then `gpt_rank`, counts
   consistent with the candidate list, and no write occurs.

---

## Frontend Plan

New files:

- `frontend/app/adoption/page.tsx` - the tab, structurally a sibling of
  `app/research/page.tsx` (list + `Drawer` + `ResearchDecisionControl`).
- `frontend/components/adoption-detail.tsx` - drawer body.
- `frontend/lib/adoption.ts` - display mapping only: status code -> Korean label
  + tone, gate/strength/warning code -> Korean sentence, delta -> arrow + tone.
  **No thresholds and no classification logic in this file.** A frontend test
  should assert that the numeric thresholds do not appear in the frontend
  source at all.
- `frontend/components/adoption.test.tsx` - Vitest coverage mirroring the
  existing `research-detail.test.tsx` style.

Edits to existing files:

- `frontend/components/section-tabs.tsx` - one array entry in `analysisTabs`.
- `frontend/components/app-shell.tsx` - add `/adoption` to the `종목 분석`
  entry's `activePaths`.
- `frontend/lib/api.ts` - `adoption: (id?) => apiFetch<AdoptionSnapshot>(...)`.
- `frontend/types/api.ts` - `AdoptionCandidate`, `AdoptionSnapshot`.
- `frontend/app/research/page.tsx` - accept an optional `?symbol=` query
  parameter and open that symbol's drawer on mount. This is the only change to
  a shipped screen's behaviour, and it is additive: with no query parameter the
  page behaves exactly as today.
- `frontend/lib/display.ts` - **fix the `risk_score` polarity** (see Verdict)
  and the `위험도` column header / tooltip in `app/research/page.tsx`.

Existing frontend tests that will need attention:

- `frontend/components/navigation.test.tsx` - the `AnalysisTabs` assertions
  check specific links, not a count, so they pass unchanged; but the test named
  "keeps all seven route pages and grouped tabs in place" becomes stale in name
  (there will be eight). Rename and extend it rather than weakening it.
- Any test asserting the `위험도` tooltip wording must be updated together with
  the polarity fix.

Polling: reuse `useApi` at the existing 20s research cadence. After a decision
mutation, re-fetch the adoption endpoint (not just the research one) so column
8 and the approval counter stay consistent.

---

## Persistence / Versioning

**Recommendation: backend composition, no DB persistence, no migration, in this
stage.**

Reasoning:

- Both inputs are immutable. `GPTAnalysis.raw_json` is persisted with a
  `payload_hash` and has no edit path; `ScannerCandidate` snapshots are the
  frozen quant truth and are never recomputed. A pure function over immutable
  inputs therefore returns the same classification forever - persisting it would
  store a derivable value, which is exactly what
  `docs/ai/FROZEN_CONTRACTS.md` avoids for evidence and quant scores.
- The classification must not live in the frontend. It is a business rule, and
  `docs/FRONTEND_V1.md` is explicit that the frontend never recomputes and
  never infers. Backend composition satisfies both constraints without a
  schema change.
- Adding a migration here would also collide with open blocker #1 in
  `docs/ai/CURRENT_STATE.md`: the real-market review DB has no
  `alembic_version` row. Any new migration forces the DB-adoption decision as a
  side effect, which is a separate approved task.

**Versioning: yes, implement `adoption_filter_v0` now.** Cost is one constant
plus a `thresholds` block in the response; benefit is that every future
comparison ("did v1 pick better names than v0?") is answerable. Follow the
existing precedent: a threshold change gets a new version string, never an
in-place edit, because the change alters how historical runs would be read.

Because v0 is not persisted per row, the version-to-date mapping must be
recorded somewhere. For v0 the response block plus this document is enough.
**When `adoption_filter_v1` is introduced, persistence becomes mandatory** -
at that point historical classifications can no longer be reconstructed from
the version string alone if the code has moved on. Recommended future shape
(deferred, needs its own migration task): a
`gpt_candidate_analyses.adoption_status` + `adoption_filter_version` pair
written at import time, or a separate `adoption_classifications` table.

Also expose `adoption_filter_version` from `GET /api/v1/settings` alongside the
existing nine version strings, so the Settings screen's "상세 버전 보기" list
stays complete.

---

## HumanDecision Contract

**Unchanged. No modification is proposed, designed, or implied by this audit.**

| Object | Cardinality | Owner |
| --- | --- | --- |
| `채택 후보` (`ADOPTION_CANDIDATE`) | unbounded, 0..N per analysis | system, `adoption_filter_v0` |
| `APPROVE` | **at most 2 per `GPTAnalysis`** | human, `HumanDecisionService` |

The cap is enforced in
`backend/app/services/research.py:138` and surfaced as HTTP 409. Nothing in
this design touches it.

The UX risk is real: a tab that shows three green `채택 후보` chips next to a
hard cap of two approvals invites the reading "the system picked 3, so I can
approve 3". Mitigations, all display-only:

1. A persistent counter in the page header, always rendered even at zero:
   `채택 후보 3개 · 최종 채택 {approved}/2`.
2. Helper text under the counter:
   `채택 후보는 시스템이 검토 대상으로 추천한 종목입니다. 최종 채택은 최대 2개까지 가능합니다.`
3. In the drawer footer, when `approved_count == 2` and this symbol is not one
   of them, the `채택` button is disabled with the reason shown inline rather
   than only failing on the 409. The existing 409 toast
   ("최대 2개 종목까지 채택할 수 있습니다.") stays as the backstop, because the
   backend remains the authority.
4. `채택 후보` is never worded as `채택` anywhere. The status column header is
   `상태`, the decision column header is `최종 결정`.

If a future policy genuinely needs more than two approvals, that is a frozen
contract change requiring its own approved stage. **DEFER.** It is not designed
here, and the max-2 rule must not be softened as a side effect of this tab.

---

## Deferred Items

| Item | Why deferred |
| --- | --- |
| Persisting classification to the DB | Not needed for v0 (pure function over immutable inputs). Becomes mandatory at `adoption_filter_v1`; needs a migration and the review-DB Alembic adoption decision first. |
| Shadow evaluation of filter quality | See below. Structurally close, but blocked. |
| Changing `max APPROVE = 2` | Frozen contract. Separate stage. |
| GPT-generated explanation text | Would require a new LLM call. Prohibited in V1. |
| Risk-category extraction (`규제`, `소송`, `희석` ...) | No structured field exists; text classification is prohibited for the frontend and out of scope for the backend this stage. Would need a `gpt_research_v1` schema field. |
| Filter thresholds as operator-editable settings | Would make a strategy parameter runtime-mutable with no version trail. Version-bump-only is the correct discipline. |
| Applying the filter to Run 1 or to historical runs | Only Run 2 has both a completed scanner run and a current analysis worth judging. Retroactive classification is possible but has no operator value today. |

### Shadow evaluation feasibility (brief section 22)

Audited against the actual schema:

| Question | Answerable today? | Evidence |
| --- | --- | --- |
| Was this symbol an adoption candidate? | **Yes**, recomputable from `gpt_candidate_analyses` + `scanner_candidates` for any historical analysis, as long as the filter version in force is known. |
| Did the human approve it? | **Yes.** `human_decisions` joins on `gpt_analysis_id` + `symbol`. |
| Did the shadow book produce an R for it? | **Yes, structurally.** `shadow_trades` already carries `gpt_analysis_id`, `scanner_candidate_id`, `symbol`, `net_r`, `status`, and TOP8 fans out to all five variants regardless of the human decision - which is exactly the counterfactual needed. |
| Did the ACTUAL book enter, and at what R? | **No.** SimBroker state is process-local and in-memory (`docs/ai/CURRENT_STATE.md` blocker #2); `execution_orders` / `execution_fills` are audit records that cannot reconstruct R. |
| Can real-market and synthetic shadow results be separated? | **No.** `shadow_trades` has no source column and `/api/v1/shadow/*` hardcodes `"source": "SIMULATION"` (blocker #10). They must not be aggregated. |

Verdict on persistence for shadow evaluation: **not required in this stage.**
Two prerequisites (durable ACTUAL account/trade truth, and the shadow source
column) are already tracked as separate blockers and both must land first.
Adding adoption persistence now would produce a column no analysis can yet use.
Revisit when blocker #2 and #10 are closed.

---

## Codex Implementation Scope

Safe to implement, in this order:

1. **Fix `risk_score` polarity** in `frontend/lib/display.ts`
   (`researchRiskLabel`, `researchRiskTone`) and the `위험도` header/tooltip in
   `frontend/app/research/page.tsx`, so the direction matches
   `backend/app/research/domain.py` and `docs/GPT_RESEARCH.md`. Update the
   affected frontend tests. Do this first; the new tab depends on the correct
   direction.
2. `backend/app/research/adoption.py` - config, enums, pure `classify`.
3. `APIQueryService.adoption()` + route `GET /api/v1/research/adoption`,
   declared above the `/research/{analysis_id}` routes.
4. `backend/tests/test_adoption_filter_stage10b4.py` - the seven cases listed in
   Backend Plan, fixtures only, no runtime DB.
5. Frontend types, `api.adoption`, `lib/adoption.ts` display mappers.
6. `frontend/app/adoption/page.tsx` + `components/adoption-detail.tsx`, reusing
   `SectionTabs`, `table-wrap`, `StatusBadge`, `InfoTooltip`, `Drawer`,
   `ResearchDecisionControl`, `EmptyState` unchanged.
7. `analysisTabs` third entry, `app-shell` `activePaths`.
8. `/research?symbol=` deep link support (additive).
9. Docs: this file's rule table into `docs/FRONTEND_V1.md` route table and
   `docs/DESIGN_SYSTEM.md` Analysis Navigation; correct the OpenAPI path count
   in `docs/API_V1.md` and `docs/ai/FROZEN_CONTRACTS.md` from 30 to 31; add
   `adoption_filter_v0` to the version-strings table.
10. Verification per `docs/ai/SAFETY_RULES.md` section 11
    (backend pytest, compileall, pip check; frontend lint, typecheck, test,
    build with `NEXT_DIST_DIR=.next-build`).

Out of scope and forbidden in the implementation task:

- Any Alembic migration or schema change.
- Any write to `data/runtime/usb_real_market_review.sqlite3`, including a
  research import, a decision, or a scanner run.
- Any change to `quant_v0`, `risk_v0`, `strategy_v0`, `execution_v0`,
  `evidence_v0`, or the `gpt_research_v0` import schema.
- Any change to the max-2 APPROVE rule.
- Any auto-decision, auto-approve, or rank-order-derived decision.
- Any new GPT/LLM call.
- Starting, restarting, or killing the backend or frontend process, and any
  change to the running dev `distDir` (`.next-dev`).
- Any commit, push, or reset.
