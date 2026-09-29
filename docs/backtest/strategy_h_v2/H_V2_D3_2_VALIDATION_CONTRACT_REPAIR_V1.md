# Strategy H-V2 - D3.2 Validation Contract Repair + Offline Re-Audit V1

- declared: 2026-09-29
- status: **VALIDATION-LAYER REPAIR - NOT AN INVESTMENT DECISION, NOT A VALUATION, NOT A NEW
  RESEARCH RUN**
- stage: **H-V2-D3.2**
- parent contracts: `H_V2_D3_AI_RESEARCH_ENGINE_V1.md` (D3), `H_V2_D3_1_BATCH2_VALIDATION_V1.md`
  (D3.1 - **FAIL**, preserved unmodified; see §A)
- implementation added: `backend/app/backtest/strategy_h_v2/research/validation_v2.py`,
  `backend/app/dev/audit_strategy_h_v2_d3_2.py`,
  `backend/tests/strategy_h_v2/research/test_validation_v2.py` (39 tests)
- model used for coding: Claude Sonnet 5
- **live model calls made in this stage: 0. Cost: $0.00.**

This is not an attempt to turn D3.1's FAIL into a PASS. D3.1's result stands. D3.2 exists because
D3.1's own R10 manual audit found the engine's research content clean (0 defects in 50 hand-read
claims) while its automatic gates failed for three different reasons that turned out to be mostly
validator and audit-method defects, not research-content defects. This document repairs those,
re-measures the frozen Batch-2 artifacts against the repaired validators at zero cost, and states
plainly what is and is not now known.

## A. D3.1 Is Immutable Baseline

**D3.1 = FAIL. D4 = NOT AUTHORIZED. The 2,010-company full Opus run = NOT AUTHORIZED.** Nothing in
this document changes that verdict, retunes a D3.1 threshold, or amends the frozen Batch-2 sample,
manifest, or ledger outputs. `H_V2_D3_1_BATCH2_VALIDATION_V1.md` is read-only from here.

D3.1's gate results were evaluated under **Validation Contract V1** (`gates.py`,
`audit_strategy_h_v2_d3_1.py`). D3.2 introduces **Validation Contract V2**
(`validation_v2.py`, `audit_strategy_h_v2_d3_2.py`) as a separate, additional measurement layer.
**V1 and V2 gate rates are NOT directly comparable, and are not presented as before/after.** In
particular:

- **R4** meant "does the `evidence_id` resolve and prefix-match its `source_id`" under V1. Under
  V2 that same check is renamed R4A (unchanged), and a new R4B ("does the *cited chunk itself*
  contain what the claim states") is added as a distinct measurement V1 never made.
- **R5** meant "is every numeric value >= magnitude 10 found somewhere in the union of all shown
  chunks plus the FACTS block" under V1. V2's numeric fidelity measurement has no magnitude floor
  and is scoped to the claim's own cited chunk by default - a stricter and differently-shaped
  check, not a stricter version of the same one.
- **R10** was a single undifferentiated manual verdict under V1 (FAIL, for citation precision,
  with content clean). V2 splits it into four independently-reported dimensions (§I).

A V2 metric on Batch 2 is a *new* reading of the same frozen artifacts, not a corrected V1 number.
Where this document reports both, it says so explicitly rather than implying an improvement.

## B. D3.1 Failure Taxonomy

Every D3.1 defect sorts into exactly one of five categories. This sort is the organizing structure
for the rest of this document.

| Category | D3.1 finding | Evidence |
|---|---|---|
| **A. Model / research content error** | **None observed.** | R10 manual audit: 0/50 claims contradict their source; 0 fabricated numeric facts in 570 automatically-checked material claims |
| **B. Citation precision error** | Claim cites a chunk that does not carry what it states | R10: 7/50 manually-audited claims (14%) |
| **C. Validator false positive** | `fair value` (GAAP measurement vocabulary) flagged as prohibited investment language | 3 of 24 candidates, all in genuine GAAP context; repair reworded a correct accounting statement away from the filing's own wording |
| **D. Validation blind spot** | R5's `extract_numbers` drops every value < 10 | 901/2,261 material-claim numbers (39.8%) never reached the fabrication check |
| **E. Ontology / contract friction** | Future Business stage floor rejects a stage the model's own evidence flags do not support | 10 of 11 repaired candidates; MRVI's sole unrepaired failure |

Categories B-E are what D3.2 addresses. Category A is not "fixed" because there was nothing in it
to fix - D3.2's job with respect to Category A is to keep measuring it at zero cost as the
validators around it change, so a future live batch's Category-A result stays comparable.

## C. What D3.1 Actually Showed, Restated

- Research content: clean. 0/50 manual, 0/570 automatic (old floor), and this stage's own
  independent recheck (§J) finds the same thing on a differently-computed basis.
  On unseen issuers is a good outcome to hold onto.
- Cost: predictable and moderate (D3.1 §G/K: ~$1.54/candidate on the reached denominator).
  Unaffected by anything in this document.
- Two of D3.1's own gates (R4, R5) did not fail - they *could not have caught* categories B-D
  because of how they were built, not because those categories are rare. That distinction is the
  entire reason this stage exists.

## D. Validation Contract V1 Weaknesses, Restated as Design Requirements

| V1 weakness | V2 requirement | Where |
|---|---|---|
| R4 checks pointer resolution, not pointer accuracy | Split into R4A (structural) / R4B (precision, scoped to the cited chunk) | §E |
| R5 floors at magnitude 10 | No floor; every unit (currency, percent, bps, multiple, shares, count) parsed | §F |
| R5's support scope is "anywhere in the candidate" | Scoped to the claim's own cited chunk, with same-source adjacency reported separately, never folded into "supported" | §F |
| `fair value` banned unconditionally | Lexical trigger + context classifier (SAFE / VIOLATION / UNCLASSIFIED) | §G |
| Future Business ontology undocumented independent of outcome | Stage definitions restated as economic meaning, not tuned | §H |
| R10 conflated content accuracy and citation precision into one verdict | Split into R10A/B/C/D | §I |

## E. Citation Integrity V2

`validation_v2.structural_provenance` (R4A) restates D3.1 §2.1's own check independently -
candidate/source/evidence graph validity - deliberately not imported from the schema that enforces
it, so the audit cannot simply be confirming its own assumptions. Five explicit failure modes:
`MISSING_SOURCE_ID`, `MISSING_EVIDENCE_ID`, `SOURCE_UNKNOWN_TO_CANDIDATE`,
`EVIDENCE_WRONG_CANDIDATE` (brief's cross-company rejection case), `EVIDENCE_SOURCE_MISMATCH`
(evidence resolves, but to a different source than the one named).

`validation_v2.citation_precision` (R4B) is new: does the claim's own numeric content resolve
*inside the chunk it actually cites*. Three outcomes - `PRECISE`, `ADJACENT_RECOVERABLE` (same
source, chunk index within 1 - D3.1's own defect shape), `UNSUPPORTED` (resolves nowhere nearby) -
plus `NO_NUMERIC_CONTENT` where there is nothing to check this way and content accuracy still needs
a human read. `ADJACENT_RECOVERABLE` is never counted as support; it is a defect that happens to be
recoverable, not a pass.

Compound-claim detection (`is_compound_claim`, brief §6) is a coarse, declared-conservative
screen, not a gate - see §K for why it cannot be more than that.

## F. Numeric Validation V2

`validation_v2.parse_numeric_tokens` recognizes USD, PERCENT, BASIS_POINTS, MULTIPLE, SHARES,
COUNT and UNKNOWN, at every magnitude - the "< 10 is not audited" rule is gone entirely. Matching
is unit-aware: PERCENT/BASIS_POINTS/MULTIPLE compare on rounding tolerance only (no scale
ambiguity), USD/SHARES/COUNT try every plausible reporting scale (thousands/millions/billions,
both directions), matching V1's own scale-trial approach for that class of value rather than
reinventing it.

Support is scoped to the claim's own cited chunk by default (brief §12 - no candidate-wide search).
`numeric_support` reports `CITED_CHUNK`, `ADJACENT_CHUNK` (reported, never counted as full support),
or `NOT_FOUND`.

### F.1 Bugs found building this against real evidence, not synthetic cases

Every one of the five defects below was found because this module was tested against Batch 2's own
real evidence text, not invented examples. Each has a regression test named for the real claim that
exposed it (`test_validation_v2.py`).

| # | Defect | Found on | Effect before fix |
|---|---|---|---|
| 1 | Number and unit sign in separate table cells (`"79 \| % \|"`) were not recognized as one token | AMT's own cited chunk, for a claim it states correctly | Its own correct 79% claim was reported NOT_FOUND against the chunk that states it |
| 2 | `"basis points"` required a literal single space; real text had `"basis-point"` (hyphenated) and a hard line-wrap inside `"basis\npoints"` | MOV | Two correctly-cited basis-point figures reported unsupported |
| 3 | A ratio table repeats `%` only in its header, not every cell (`"46.57 \| 48.19 \| 48.43"`) | SRCE | Three correctly-cited efficiency-ratio figures reported unsupported |
| 4 | **The scale-trial tolerance blew up on division**: `value / scale` used a tolerance scaled *up* by `scale` instead of down, so a small claimed value matched almost any nearby number | LNG's own "$1.1 billion" matched an unrelated "$3.1 billion" in the very evidence being audited | A real citation defect (§J.2's original finding) was being silently masked as PRECISE |
| 5 | A year immediately followed by a comma (`"2026,"`) fell outside the year-recognition pattern and a footnote superscript with no space (`"loss)1,2"`) was read as the value 12 via a false thousands-separator match | Pervasive across the batch | 19 claims spuriously reported as having unsupported content that was never a real number |
| 6 | A range's second bound often carries no unit of its own (`"$5.0-9.0M"` - only the first number gets a `$`), and the fallback from a bare COUNT to USD/SHARES was one-directional | AIP | A correctly-cited FCF guidance figure reported unsupported |

Defect 4 is the most consequential: it is a **correctness bug in this stage's own matcher**, not a
finding about the research engine, and it was masking a real defect rather than manufacturing a
false one - the opposite failure mode from D3.1's own AAON false-fabrication story (§F of that
document), and a reminder that a numeric-audit tool needs its own regression tests against
real evidence before its output is trusted, which is why §J's results below are reported only after
all six were fixed and re-verified.

### F.2 What "unsupported" means after the fixes, verified

On the full audited population (§J), every remaining `UNSUPPORTED` claim was additionally checked
against the *entire source document* (not just the cited chunk and its immediate neighbours) and
the *entire package* (every other source), read-only, no model calls:

| Recoverable where | Count | What it means |
|---|---|---|
| Same source document, non-adjacent chunk | 34 | A worse version of D3.1's own defect shape: right document, wrong (and distant) location within it |
| A different source in the same package | 15 | Cited to the wrong filing entirely, though the fact is real and present elsewhere in this candidate's own evidence |
| Nowhere found by string match | 3 | Spot-verified by hand (§F.3) - none confirmed fabricated |

**Zero of the 52 residual `UNSUPPORTED` claims were confirmed fabricated content.** All are
citation-precision defects of two severities, plus a small residual this module cannot resolve by
string matching alone.

### F.3 The 3 claims found nowhere

- MIDD: "Food Processing net sales were... (26.6%)" - not printed anywhere; is the correct
  arithmetic result of the two dollar figures the same sentence states ($850.2M / $3,201.2M total
  = 26.6%), i.e. a correct model-computed derivation, not a fabrication, that this string-matching
  tool cannot itself verify as arithmetic (brief §7's own limit).
- MIDD: "no acquisition contribution (0.0%)" - same shape: a derived zero, not a filing quote.
- DKNG: "issued at 99.50" - a real bond reoffer price; not found in the specific cited chunk's text
  by this matcher, most likely a formatting variant (e.g. "99.500") beyond its tolerance rather than
  an absent fact.

None of these three is evidence of hallucination. They are the honest residue of a method that
verifies string-level citation, not arithmetic - stated as a limit, not glossed over.

## G. Investment Language V2

`validation_v2.classify_investment_language` splits every lexical trigger into `SAFE`, `VIOLATION`,
or `UNCLASSIFIED` (never silently passed).

**`fair value`**: SAFE by default when the surrounding text names the GAAP object being measured
(a derivative, an asset, a liability, a note receivable, goodwill, a reporting unit, ...) or uses a
GAAP measurement frame ("measured at fair value", "fair value hierarchy", "changes in the fair
value of..."). VIOLATION only when it explicitly frames the company's own stock or shares as the
object of a valuation opinion ("the stock's fair value is $180", "we believe the fair value of the
shares is..."). This default - SAFE absent an explicit opinion marker - is a deliberate,
declared choice: in this batch's own evidence, `fair value` occurs 552 times across three
candidates' own filings (AIP 181, LNG 215, MIDD 156) and not once as an investment opinion. A
detector that defaults to VIOLATION would need to re-litigate that same false-positive class every
batch; one that defaults to UNCLASSIFIED would produce a permanent backlog of "manual review" items
for a term that is overwhelmingly ordinary filing vocabulary. SAFE-by-default, with an explicit and
narrow opinion pattern to escape it, is the trade-off D3.2 makes and states plainly.

**`approve`/`reject`**: kept for defensive completeness (D3's own pilot already fixed the bare-word
substring problem in `schema.py`'s `BANNED_INVESTMENT_LANGUAGE_PATTERNS`, which this module does
not touch), split the same way: "the Board of Directors approved a share repurchase" is SAFE, "we
approve this stock as a core holding" is VIOLATION.

The other terms in `BANNED_INVESTMENT_LANGUAGE_PATTERNS` ("price target", "strong buy", "entry
zone", ...) are unambiguous analyst/strategy jargon that does not occur in ordinary filing prose and
is unchanged.

**Regression**: `test_real_batch2_defect_reproduced_and_fixed` runs the exact three sentences D3.1
§I.2 found mis-flagged (AIP's contingent-consideration remeasurement, LNG's derivative fair-value
accounting, MIDD's purchase-price-allocation language) and asserts all three now classify SAFE.

**Measured against the final ledger outputs, all three counts are 0/0/0** (violations /
false-positives-prevented / unclassified) - not because the fix has nothing to show, but because
D3.1's own repair already reworded the three affected sentences away from "fair value" *before*
V2 existed (§I.2's own point: the repair made the output diverge from its source). The
demonstration of the fix is therefore the regression test against the original (pre-repair) wording
plus the counterfactual repair count in §K, not a nonzero count on the already-repaired ledger.

## H. Future Business Ontology, Audited Not Redefined

The stage floor table (`AUDIT_STAGE_MIN_FLAGS`) and the five evidence-flag fields are **unchanged**.
`validation_v2.STAGE_DEFINITIONS` restates each stage's economic meaning independent of any
repair-rate outcome, exactly as it already existed in the schema's own docstrings - this is
documentation, not a rule change:

| Stage | Economic meaning |
|---|---|
| STORY | Announcement or stated intent only - no commercial evidence yet |
| EARLY_EVIDENCE | A prototype, pilot, or early named-customer validation exists |
| COMMERCIALIZING | A commercial contract, production ramp, or customer deployment exists, short of durable recurring revenue |
| REAL_BUSINESS | Real recurring revenue, or a material backlog/order book with commercialization evidence behind it |
| MATURE | An established, ongoing part of the company's operations |

### H.1 Every stage-floor repair case, read individually

For all 9 OK candidates that hit this rule (MRVI excluded - no usable final output, see §J.4),
what the model proposed, what it was rejected for, and what it repaired to:

| Ticker | Proposed stage (flags) | Repaired to (flags) | What changed |
|---|---|---|---|
| DSP | EARLY_EVIDENCE (0) | STORY (0) | Stage downgraded |
| OOMA | COMMERCIALIZING (1) | EARLY_EVIDENCE (1) | Stage downgraded |
| AIP | COMMERCIALIZING (1) | EARLY_EVIDENCE (1) | Stage downgraded |
| DXCM | COMMERCIALIZING (0) | UNKNOWN (0) | Stage abandoned entirely |
| HL | EARLY_EVIDENCE (0) | STORY (0) | Stage downgraded |
| LNG | EARLY_EVIDENCE (0) | STORY (0) | Stage downgraded |
| FLS | EARLY_EVIDENCE (0) | EARLY_EVIDENCE (**1**) | **Flag added, stage kept** |
| MOV | EARLY_EVIDENCE (0) | STORY (0) | Stage downgraded |
| PSNL | EARLY_EVIDENCE (0) | STORY (0) | Stage downgraded |

**8 of 9 repairs downgraded the stage to match the evidence it actually had. Only FLS added an
evidence flag instead of lowering the stage.** D3.1 §I.1 raised, as the reason for not disclosing
the floor numbers in the prompt, the concern that doing so "invites the model to raise a flag
rather than lower a stage" - and this table is the closest thing to direct evidence on that
question this stage can produce without a live call: in Batch 2's own *undisclosed-floor* repair
behavior, the model overwhelmingly chose the honest correction (downgrade) over the convenient one
(inflate a flag), 8 to 1. This does not resolve the disclosure question - it is one batch, and
disclosure could still change incentives in a way non-disclosure never tests - but it is evidence
against assuming inflation is the default failure mode, and belongs in whatever brief eventually
decides §I.1's open question, not lost.

MRVI's two stage-floor repair rounds are unauditable beyond the validation error itself: no final
output exists (validation never passed) and the stored `raw_output_preview` is truncated before
`future_business` in both attempts (see §J.4).

## I. R10 V2, Four Dimensions

| Dimension | What it measures | Result |
|---|---|---|
| **R10A** Content accuracy | Does the claim contradict its source | 0/50 defects (D3.1's manual read, preserved - not recomputed; content truth is a human judgment this stage does not automate) |
| **R10B** Citation precision | Does the cited chunk itself support the claim | **8/50 defects (16%)** - computed by `validation_v2.citation_precision`, independently re-deriving D3.1's manual finding |
| **R10C** Numeric fidelity | Numeric tokens present in the sample, at any magnitude | 94 tokens across 50 claims, now all auditable (vs. an unstated fraction under V1's floor) |
| **R10D** UNKNOWN/qualifier preservation | Does an INFERENCE claim carry its own hedge language | 8/12 checked claims (67%) - a coarse keyword proxy (`does not`, `may`, `plausible`, ...) for the qualitative judgment already made by hand in D3.1 §J.1, not a replacement for it |

### I.1 R10B reproduces the manual read, and finds 3 more

Run against the *exact same 50 claims* D3.1 §J read by hand (same selection rule, same tickers,
verified by re-deriving the same claim count and the same path convention the manual audit used -
an earlier version of this script used a slightly different path string and silently audited a
different 52 claims; caught by cross-checking the claim count against D3.1's own reported 50 before
trusting any result from it):

- **4 of the original 5 manual defects reproduced automatically**: LNG's growth-capital citation,
  HL's share-count citation, HL's by-product-credits citation, SRCE's noninterest-expense citation.
- **FCUV's building-price citation, the 5th original defect, reproduces as `UNSUPPORTED`** rather
  than `ADJACENT_RECOVERABLE` - correctly, since the actual chunk (CHUNK:42) is 5 positions away
  from the cited one (CHUNK:37), outside the 1-hop adjacency window D3.1's manual read did not
  itself distinguish by distance.
- **3 new defects the manual read missed**: PSNL's related-party ownership claim (cites an 8-K
  earnings release; the "more than 10%" ownership fact is in the 10-K's Note 8, a different
  source entirely), a second HL claim (an INFERENCE about the 2H26 price assumption, citing a
  chunk that does not carry the $63.06/oz realized-price figure it references), and one FLS claim.

The PSNL miss is notable for a specific reason: it was manually read and marked clean during this
same D3.2 investigation, before the automated check existed - an error in the very manual-audit
method D3.1 relied on, caught by the validator built to formalize it. That is offered as a point in
favor of R10B existing, not as a criticism of D3.1's manual pass, which was never claimed to be
exhaustive against every possible wrong-source citation.

## J. Offline Batch-2 Re-Audit Under V2

`audit_strategy_h_v2_d3_2.py`, run against `D3_1-BATCH2-20260928T103544Z` - the same frozen
manifest, ledger, and D2.1 packages D3.1 used. **0 live model calls. $0 cost.** Full output:
`data/runtime/strategy_h_v2/d3/D3_1-BATCH2-20260928T103544Z.v2audit.json` (gitignored, as all
`data/runtime/` artifacts are).

### J.1 Structural provenance (R4A)

**1,040/1,040 material claims: OK.** No cross-company, cross-source, or dangling-pointer citation
in the batch. This confirms D3.1's own R4=100% result on an independently-written check - the part
of §2.1 that *was* closed stays closed under a check that does not import from the code it is
checking.

### J.2 Citation precision (R4B) - the full 18-candidate population

| Status | Count | Share |
|---|---|---|
| PRECISE | 519 | 49.9% |
| NO_NUMERIC_CONTENT | 428 | 41.2% |
| UNSUPPORTED | 52 | 5.0% |
| ADJACENT_RECOVERABLE | 41 | 3.9% |

**8.9% of material claims (93/1,040) have a citation-precision defect.** Every one characterized in
§F.2/F.3: two-thirds same-document-wrong-location, most of the remainder wrong-source-same-package,
and a residual of 3 that are most likely correct model-computed derivations this tool cannot verify
arithmetically. This is a meaningfully higher rate than R10's original 14% manual estimate applied
to the full population would suggest was possible to bound tightly from a 50-claim sample - the
sample's own confidence interval covers it, but the full recount is the number to use going
forward, not the extrapolation.

Numeric-token-level: 1,534/1,666 tokens (92.1%) supported in their own cited chunk, 65 (3.9%)
adjacent-recoverable, 67 (4.0%) not found.

### J.3 Numeric fidelity coverage

No magnitude floor. 1,666 numeric tokens parsed across 1,040 material claims (up from an unstated
subset under V1's < 10 exclusion), of which 100% are now checked, against the caveats in §F.3.

### J.4 Investment language

0 violations, 0 false positives prevented, 0 unclassified matches - measured against the *final*
ledger outputs, which already have the three fair-value-flagged sentences reworded by V1's own
repair (§G explains why this is the expected, not a null, result). Evidence of the fix's effect is
in §K's counterfactual repair count instead.

### J.5 Conflict integrity

Unchanged and re-verified structurally: both audited `evidence_conflicts` entries carry >= 2
`evidence_ids`, both resolve to real chunks in their candidate's own package, and one is left
`UNRESOLVED` where D3.1 originally found it. No conflict entry was fabricated or silently resolved.

### J.6 Compound claim candidates

**379/1,040 (36.4%) flagged by the coarse and deliberately conservative detector.** A manual sample
of 20 found the detector's actual usefulness is limited: most flags are noun-phrase conjunctions
("Asia and the Middle East", "FluentStream and Phone.com") the regex-level detector cannot
distinguish from two independent clauses sharing one `evidence_id` (brief §6's real concern). An
earlier, less restricted version of this detector (splitting on every sentence boundary) flagged
83% of the batch and was discarded as useless before this number was ever reported. **379 is a
screening upper bound, not a precision count of genuine compound claims**, and this remains an open
D3.3 prompt-design question (whether to require atomic claims, brief §6 Option A), not something
this offline stage can resolve without a live re-generation to test against.

### J.7 MRVI

Unaffected by anything in this document. Both of MRVI's stage-floor repair rounds are unchanged
under V2's ontology (§H says why: the floor itself is not touched). Its final claim content is
**NOT_AUDITABLE**: no ledger output exists (validation never passed within the 2-call budget), and
the stored `raw_output_preview` for both the original generation and the repair attempt is
truncated before `future_business` - the section containing the rejected item - so there is nothing
beyond the validation error itself (`stage=COMMERCIALIZING requires at least 2 evidence flag(s),
got 1`) to audit. **No new repair call was made to find out more.** This is a real, stated gap in
what can be known about MRVI without spending live budget, not a result papered over.

## K. Counterfactual Repair, V1 vs. V2

Computed directly from the manifest's own repair-round records (which round had which error), no
new model calls. Each of Batch 2's 13 original repair rounds classified by cause (`fair value`
lexical match, stage-floor evidence-flag error, or other):

| Ticker | Round | Cause | Would V2 still repair this round? |
|---|---|---|---|
| DSP | 0 | stage floor | Yes (ontology unchanged) |
| OOMA | 0 | stage floor | Yes |
| AIP | 0 | fair value only | **No** |
| AIP | 1 | stage floor | Yes |
| DXCM | 0 | stage floor | Yes |
| HL | 0 | stage floor | Yes |
| LNG | 0 | fair value + stage floor (bundled) | Yes (stage cause remains) |
| FLS | 0 | stage floor | Yes |
| MIDD | 0 | fair value only | **No** |
| MOV | 0 | stage floor | Yes |
| PSNL | 0 | stage floor | Yes |
| MRVI | 0, 1 | stage floor | Yes |

| | V1 (as run) | V2 (counterfactual) |
|---|---|---|
| Repaired candidates | 11 | **10** (MIDD becomes repair-free) |
| Repair rounds | 13 | **11** |
| Repair rate, of 24 attempted | 45.8% | 41.7% |
| Repair rate, of 19 reached | 57.9% | 52.6% |

**R3's gate (<= 20%) still fails under V2, by a wide margin, on either denominator.** This is the
expected and correctly-reported result: V2 fixes the `fair value` false positive (Category C,
§B) and the citation-precision blind spot (Category B), neither of which was ever the dominant
cause of D3.1's R3 failure. The stage-floor friction (Category E) is 10 of 11 original rounds and
remains 10 of 11 under V2, because §H deliberately does not touch the ontology. **No V2 fix was
capable of passing R3, and none was built to.** A repair-rate gate that could pass would require
either resolving §H.1's disclosure question (D3.3's job, if authorized) or redefining what R3
counts (§L, item 1) - not a validator patch.

## L. Limits of Comparability

1. **D3.1's V1 gate percentages and D3.2's V2 metrics measure different things and must not be
   read as before/after on the same scale.** R4 (V1) meant pointer resolution; R4B (V2) means
   pointer precision. R5 (V1) had a magnitude floor and a candidate-wide support scope; the V2
   numeric-fidelity measurement has neither. A "V1 45.8% -> V2 41.7%" repair-rate comparison in §K
   is the one number in this document computed on an identical basis (both counted the same way
   against the same 13 recorded rounds) and is explicitly a counterfactual, not an observed rerun.
2. This stage's own numeric matcher had six real bugs found against real evidence (§F.1),
   including one (the tolerance blow-up) that was *masking* a genuine defect rather than inventing
   a false one. Every number in §J is reported only after all six fixes, with a regression test
   named for the real claim that exposed each - but a seventh has not been ruled out, and §F.3's
   residual of 3 unexplained values is reported as a limit, not resolved.
3. §J.6's compound-claim count (379) is an explicitly coarse upper bound, not a precision measure -
   see §J.6 for why a tighter one was not built.
4. Five of Batch 2's 24 sampled candidates never reached the model (an account-level rate limit,
   D3.1 §G) and remain unreached here; this document does not spend live budget to fill them in,
   consistent with D3.1's own decision not to (D3.1 §J.4).
5. R10D's hedge-language check is a keyword proxy for a qualitative judgment, stated as such - a
   claim without one of the listed hedge words can still be honestly qualified in other language.

## M. Tests

`backend/tests/strategy_h_v2/research/test_validation_v2.py` - 39 tests, all passing, 0 model
calls:

- **Numeric**: currency/percent/bps/multiple/shares parsing, magnitude-10 auditability (the whole
  point of this stage), date exclusion, bare-year preservation, unit-mismatch non-conflation,
  same-number-wrong-chunk, adjacent-chunk-not-full-support, scale/rounding tolerance (the original
  AAON case, preserved), plus 6 regression tests named for real Batch-2 defects (§F.1/F.2).
- **Citation precision**: precise / adjacent-recoverable / unsupported / no-numeric-content /
  missing-cited-chunk, each exercised on a real or realistic claim shape.
- **Structural provenance**: OK, cross-company rejection, cross-source-within-candidate mismatch,
  missing fields.
- **Compound claim**: two-distinct-facts flagged, reasoning-clause and qualifier-sentence not
  flagged.
- **Investment language**: GAAP fair-value allowed (5 cases including the 3 real batch sentences),
  investment-opinion fair-value blocked, board-approved allowed, investment-approve blocked, the
  exact D3-pilot substring-false-positive regression, unambiguous analyst jargon still blocked.

Full H-V2 regression (`backend/tests/strategy_h_v2`): **284 passed**, 0 failures, 0 model calls.
No existing test was modified.

## N. Changed Files

New files only - no existing dirty file in the working tree was read, modified, staged, or
committed:

- `backend/app/backtest/strategy_h_v2/research/validation_v2.py` (new)
- `backend/app/dev/audit_strategy_h_v2_d3_2.py` (new)
- `backend/tests/strategy_h_v2/research/test_validation_v2.py` (new)
- `docs/backtest/strategy_h_v2/H_V2_D3_2_VALIDATION_CONTRACT_REPAIR_V1.md` (this file)

Not modified: `schema.py`, `prompt_builder.py`, `validate.py`, `repair.py`, `gates.py`,
`run_strategy_h_v2_d3*.py`, `audit_strategy_h_v2_d3_1.py`, or any D3.1 artifact under
`data/runtime/`. The 191 pre-existing dirty files in the working tree at the start of this stage
remain exactly as they were.

## O. Verdict

**H-V2-D3.2 VALIDATION CONTRACT = NEEDS REVISION.**

Not READY FOR LIVE CONFIRMATION, and not FAIL. What D3.2 fixed is real and verified: the citation
scope is now precision-checked rather than pointer-checked, numbers below magnitude 10 are
auditable, the `fair value` false positive is closed with a declared and tested trade-off, and R10
now reports content and citation as separate dimensions rather than one blended verdict. What it
did not fix, and was not designed to: the stage-floor ontology friction that drove 10 of 11 of
D3.1's actual repair rounds, MRVI's genuinely unauditable content, and the compound-claim question,
all three of which are D3.3-scope decisions this stage can inform but not make. A validation
contract with an unresolved disclosure question sitting at the center of its dominant repair cause
is not yet ready to certify a live confirmation batch's results - it would correctly measure
citation and numeric quality, and still be unable to say whether a repeat of Batch 2's repair
pattern reflects the contract working as designed or something worth revisiting first.

## P. D3.3 Requirements (proposal only - not authorized, not started)

If H-V2-D3.3 (new-issuer live confirmation) is authorized by the user:

1. **Resolve §H.1's disclosure question explicitly**, one way or the other, before the batch runs -
   not implicitly by what the prompt happens to say. §H.1's 8-of-9-downgrade finding is evidence to
   weigh, not a decision already made.
2. Sample size, FULL/CORE split, and budget: unchanged from D3.1's own design (12+12, CIK-disjoint
   from both the D3 pilot and Batch 2, checksummed before execution) unless the disclosure decision
   in (1) changes what needs measuring.
3. **Quality gates**: R1/R2/R3 as V1 defines them, R4 split into R4A/R4B per this document, R5
   replaced by the V2 numeric-fidelity measurement (coverage + cited-chunk precision, no floor),
   R6-R9 unchanged, R10 split into R10A-D per §I.
4. **Manual audit size**: same 4 P1 + 4 P2 as D3.1, but run R10B/C automatically first and use the
   manual read to verify the automatic result rather than duplicate it from scratch - R10B/C are
   now fast and free; spend the human read where only a person can judge (R10A, R10D).
5. Report D3.3 against its own V2 baseline. Do not present any V2 metric on D3.3 as an improvement
   over the V1 numbers this document explicitly says are not comparable (§A, §L).

## Q. Declarations

```text
model used for coding
= Claude Sonnet 5

new live Opus calls?
NO

live model cost?
$0.00

D3.1 result modified?
NO

D3.1 thresholds retuned?
NO

new issuer batch?
NO

forward returns?
NO

Expectation Gap?
NO

APPROVE/WATCH/REJECT?
NO

D4 executed?
NO

existing dirty files modified?
NO

push?
NO
```
