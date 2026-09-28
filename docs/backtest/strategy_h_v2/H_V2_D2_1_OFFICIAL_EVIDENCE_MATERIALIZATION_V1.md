# Strategy H-V2 - D2.1 Official Evidence Materialization V1

- declared: 2026-09-28
- status: **SOURCE FETCH / TEXT EXTRACTION / CHUNKING - NOT AN INTERPRETATION, NOT AN ALPHA RESULT**
- stage: **H-V2-D2.1**
- parent contracts: `H_V2_D0_ARCHITECTURE_RESEARCH_CONTRACT_V1.md`,
  `H_V2_D1_UNIVERSE_CHANGE_ENGINE_V1.md`, `H_V2_D1_1_UNIVERSE_DATA_COVERAGE_V1.md`,
  `H_V2_D2_EVIDENCE_COLLECTOR_V1.md`
- implementation: `backend/app/backtest/strategy_h_v2/evidence/{filing_index,text_extraction,
  chunking,chunk_schema,document_cache,materialize}.py`, `backend/app/dev/run_strategy_h_v2_d2_1.py`
- tests: 77 new (`backend/tests/strategy_h_v2/evidence/`), 221 passing total alongside every prior
  Strategy H0/PV and H-V2 suite, 0 failures
- pilot run: `data/runtime/strategy_h_v2/d2_1/D2_1-PILOT-20260928T063142Z/` (20 candidates: 10
  `FULL` + 10 `CORE`, gitignored; pre-dates the extraction fix in §L)
- full run (pre-fix): `data/runtime/strategy_h_v2/d2_1/D2_1-20260928T063233Z/` (2,010 candidates,
  all real network fetches, gitignored)
- full run (post-fix, authoritative): `data/runtime/strategy_h_v2/d2_1/D2_1-20260928T072430Z/`
  (2,010 candidates, re-extracted entirely from the already-cached raw documents, 0 new network
  requests, gitignored; see §L)

D2.1 fetches the actual filing/exhibit document content D2 only referenced, extracts plain text,
splits it into deterministic, source-linked chunks, and packages each candidate's D2 bundle plus
its chunks into an immutable `AIResearchInputV1`. No company is judged anywhere in this stage; the
only new capability is that a future D3 engine can now actually *read* the evidence, not just know
that it exists.

## A. Repository State

- branch `main`, HEAD before this stage: `2d7f137` (`feat(strategy-h-v2): implement research
  evidence collector`), confirmed to match D2's commit.
- `ab4c0a5` (D0), `4bbafa6` (D1), `0bdd292` (D1.1), `2d7f137` (D2) all present in `git log`.
- origin: ahead 19, behind 0. Pre-existing dirty worktree (177 entries) untouched throughout.

## B. D2 Input State

D2's full run (`D2-20260928T061101Z`) is the frozen input, re-audited directly from its own
artifacts rather than trusted from memory:

```text
bundles:                 2,010 / 2,010 (100%), 0 errors, 100% COMPLETE
depth:                   FULL 200 (P1_HIGH)   CORE 1,810 (P2_MEDIUM)
SEC_10K references:      2,010
SEC_10Q references:      8,033
SEC_8K references:       11,187
company_releases (2.02): 3,717 refs across 1,804/2,010 candidates (90%)
investor_materials (7.01): 2,982 refs across 1,169/2,010 candidates (58%)
```

These match the counts the D2.1 brief itself cited as "currently known"; the real artifact was
re-read and confirms them exactly - no discrepancy to reconcile.

## C. Why D2.1 Was Required

D2's `SourceProvenance` proves a source *exists*, knows its PIT eligibility, and (for filings)
constructs its URL - but D2 never fetched a single byte of filing or exhibit content. Before D2.1,
every one of D2's 2,010 bundles was `REFERENCE_ONLY`: a future D3 engine reading a bundle alone
would know a 10-K exists and where, but would have to either fetch it itself mid-research (breaking
the "immutable, pre-materialized evidence" contract D0 froze) or guess at its content - exactly the
failure mode D0 §12 forbids ("AI가 재무 숫자 invent 금지... source 없는 미래사업 주장 금지"). D2.1
closes that gap for the specific documents D0's materialization priority names.

## D. Materialization Policy

Bounded by collection depth, frozen before any content was read (never adjusted after seeing a
result):

```text
CORE  (P2_MEDIUM, 1,810 candidates): latest 10-K, latest 1 10-Q, latest earnings-release
                                      exhibit (if a 2.02 8-K exists), 1 most recent other
                                      material 8-K
FULL  (P1_HIGH, 200 candidates):     latest 10-K, latest 2 10-Q, latest earnings-release
                                      exhibit, up to 3 most recent other material 8-K,
                                      the 7.01 exhibit (if any, role depends on §H)
```

(`materialize.py`: `CORE_MAX_10Q=1`, `FULL_MAX_10Q=2`, `CORE_MAX_MATERIAL_8K=1`,
`FULL_MAX_MATERIAL_8K=3`.) This is the same research-priority meaning D0/D1/D2 already carry: `FULL`
is not "buy," it is a larger, still-bounded research budget for the candidate E3 flagged as most
urgent to read first.

## E. SEC Filing / Exhibit Mapping

`filing_index.py` parses SEC's own per-filing document-table page (`.../{accession}-index.htm`) -
the same official `Seq | Description | Document | Type | Size` table a human reviewer sees on
EDGAR - rather than guessing a document's role from its filename. Verified against a real filing
(Apple's `0000320193-26-000018`, an actual 8-K) before use: the parser correctly identified
`EX-99.1` (the earnings-release exhibit) and resolved the primary document's inline-XBRL-viewer-
wrapped link (`/ix?doc=...`) to its real path.

## F. Earnings Release Content

For a candidate's most recent 2.02-classified 8-K, D2.1 fetches that filing's index page and takes
the first `EX-99.*` document in SEC's own listed order - never assumes "99.1 is always the earnings
release" (D2.1 brief §7): if the filing index lists no `EX-99.*` document at all, the result is
recorded as `EXHIBIT_UNRESOLVED`, not silently substituted with something else. Full run: see §M.

## G. Periodic Filing Content

The latest 10-K's and up to `max_10q` 10-Qs' **primary documents** (already known from D1's
`filings[]`, no index-page lookup needed) are fetched directly and their full text extracted. Both
are always attempted regardless of depth (only the 10-Q *count* differs between `CORE` and `FULL`).

## H. IR / Reg-FD Content

Only `FULL`-depth candidates attempt a 7.01 exhibit at all. D2.1 brief §8's requirement -
`7.01 exists != IR presentation confirmed` - is enforced by `is_presentation_confirmed()`: the
resolved `EX-99.*` exhibit's own SEC-listed `Description` text is checked (case-insensitively) for
`presentation`/`investor`/`slide`/`deck`. A match materializes under the `IR_PRESENTATION` role; no
match still materializes the same content (it may still be useful evidence) but under the generic
`REG_FD_MATERIAL` role, never overclaiming what SEC's own metadata does not say. Pilot evidence: of
10 `FULL` candidates with a 7.01 filing, **0 confirmed as `IR_PRESENTATION`, 7 materialized as
generic `REG_FD_MATERIAL`** - the classifier is conservative by construction, exactly as intended,
not tuned to produce a particular split.

## I. Extraction / Chunking

`text_extraction.py` uses only the Python standard library (`html.parser`) - no new HTML/PDF
dependency was added, per the brief's explicit instruction not to build a new heavy parsing system.
Script/style content is dropped; table cells are joined with `" | "`; block-tag boundaries become
newlines so the section-heading regexes in §11's design can anchor on line starts. A PDF (detected
by extension or `%PDF-` magic bytes) is always `CONTENT_NOT_EXTRACTABLE` - no OCR was attempted or
even considered as a fallback, matching the brief exactly. `chunking.py` splits deterministically at
line boundaries near a 4,000-character target (~1,000-token estimate at 4 chars/token), preferring
section boundaries when `detect_sections()` resolved any - a chunk never spans two named sections
and never merges text from two different sources. `evidence_id` is `{source_id}:CHUNK:{index}` and
`content_checksum` is a SHA-256 of the chunk text; both are provably stable
(`test_same_text_produces_same_chunks_and_checksums`, and confirmed on the real full run - see §M).

Section detection (`_SECTION_PATTERNS`) is deliberately conservative: a heading must occupy its own
line with (almost) nothing else on it. An inline cross-reference ("as discussed in Item 1A. Risk
Factors above...") correctly does not match and the surrounding text falls through to
`section=null` rather than a wrong label - the brief's explicit preference
("잘못된 section label보다 UNKNOWN 우선").

## J. PIT / Source Boundary

`materialize_candidate` never fetches a `SourceProvenance` whose `pit_eligible` is `False` -
defensive, since D2 itself already guarantees every `filings[]` entry is PIT-eligible, but D2.1
checks again rather than trusting the upstream guarantee silently
(`test_future_source_is_never_fetched`). Every `EvidenceChunk` carries its source's own
`published_at`/`available_at`/`pit_eligible` unchanged - materialization never re-derives or
loosens PIT metadata. Every chunk also carries the same fixed `source_boundary =
"UNTRUSTED_RESEARCH_DATA"` constant D2 already established (`sources.SOURCE_BOUNDARY`), reused
verbatim, not redefined - a future D3 prompt can rely on one single boundary marker across both
structured facts and raw text.

## K. Pilot Results

`D2_1-PILOT-20260928T063142Z`, 20 deterministic candidates (first 10 `FULL` + first 10 `CORE`
alphabetically, not outcome-selected): **20/20 packages written, 0 errors.** Every attempted
document extracted successfully (20/20 10-K, 30/30 10-Q, 20/20 earnings release, 39/39 material
8-K, 7/7 attempted 7.01 exhibits - all classified `REG_FD_MATERIAL`, none `IR_PRESENTATION`).
4,140 chunks, ~16.4MB of extracted text (~4.1M estimated tokens), 141 HTTP requests, 143MB
downloaded, 0 retries. Manual spot-check confirmed the resolved earnings-release exhibit for the
pilot's first `FULL` candidate matches its actual accession's `EX-99.1` on SEC EDGAR.

## L. Full Results

`D2_1-20260928T063233Z`, all 2,010 D2 bundles, reusing the same `SecClient` (5 req/s, same
`USB Research tjd6189@gmail.com` identifier already used throughout this repository): **2,010/2,010
packages, 0 errors, 0 retries**, 10,016 HTTP requests, ~14.17GB downloaded, 393,564 chunks.

**A real extraction-quality defect was then found by spot-checking real output**, not assumed in
advance: the first chunk of a materialized 10-K (AAON) was XBRL header noise
(`0000824142falseFY202512/3112/31/2025...http://fasb.org/us-gaap/2024#AccruedLiabilitiesCurrent...`)
rather than filing prose. Tracing it to the raw cached HTML showed the cause: modern SEC filings use
inline XBRL, and the entire machine-readable header/hidden-facts block is wrapped in
`<div style="display:none"><ix:header>...` - `text_extraction.py`'s tag skip-list only covered
`script`/`style`/`head`, not arbitrary `display:none` containers. Fixed by rewriting
`_HTMLTextExtractor` to track a proper tag stack and skip the content of *any* element whose own
`style` attribute says `display:none` (a general, correct fix, not a special case for the `ix:`
namespace specifically - which was still verified to be exactly where the noise came from). Four
regression tests were added (`test_inline_xbrl_hidden_header_block_is_dropped` and three related
cases), and a genuine second bug was caught while writing them: a self-closing void tag (`<br/>`)
inside a hidden block was popping the wrong entry off the skip-tracking stack, silently ending the
skip early - fixed by never pushing void elements (`br`, `img`, `hr`, ...) onto the stack at all.

**Re-ran the full 2,010-candidate materialization from the already-cached raw documents** - zero new
network requests, zero new bytes downloaded, ~15 minutes wall time (pure local re-extraction).
`D2_1-20260928T072430Z` is the authoritative result:

```text
input bundles:              2,010
packages written:           2,010   (100%)
errors:                          0
content readiness:      CORE 1,807   FULL 200   PARTIAL 3   (0 REFERENCE_ONLY, 0 INSUFFICIENT)

LATEST_10K       EXTRACTED 2,010
RECENT_10Q       EXTRACTED 2,210
EARNINGS_RELEASE EXTRACTED 1,801   EXHIBIT_UNRESOLVED 3
MATERIAL_8K      EXTRACTED 2,249
REG_FD_MATERIAL  EXTRACTED   117   EXHIBIT_UNRESOLVED 12   (0 confirmed IR_PRESENTATION)

chunks:                    384,666   (was 393,564 pre-fix - the noisy header chunks are gone)
extracted text:          ~1.40 GB   (was ~1.56 GB pre-fix)
estimated tokens:      ~350,439,440
```

Document-level success counts are **identical** between the pre-fix and post-fix runs (same 2,010
packages, same readiness distribution, same per-role EXTRACTED/UNRESOLVED counts) - the fix changed
*what the text says*, not *whether extraction succeeded*, which is exactly the expected signature of
a content-quality fix rather than a coverage regression.

## M. Content Coverage

Coverage expressed against the 2,010-candidate input, using the authoritative post-fix run:

```text
10-K content coverage:            2,010 / 2,010   (100%)
10-Q content coverage:            2,210 / up to 4,020 targeted (2,010 CORE x1 + 200 FULL x2)
earnings-release content coverage: 1,801 / 1,804 candidates with a 2.02 filing (99.8%; 3 unresolved)
material-8K content coverage:      2,249 refs materialized (CORE 1 each, FULL up to 3 each)
IR/Reg-FD material coverage:         129 / 1,169 candidates with a 7.01 filing attempted
                                    (only FULL depth attempts this - 200 FULL candidates,
                                    129 of which actually had an investor_materials entry;
                                    117 extracted, 12 exhibit-unresolved, 0 confirmed as
                                    IR_PRESENTATION specifically - see §H)
```

The 100% 10-K coverage and 99.8% earnings-release coverage confirm the filing-index-based exhibit
resolution (§E) works reliably at real scale, not just on the hand-picked pilot sample.

## N. Cache / Network

New raw-document cache, separate from D1.1's submissions/companyfacts store (D2.1 brief §6/§16):

```text
data/runtime/strategy_h_v2/d2_1/
  raw_source_cache/{cik}/{accession}/{document_name}.gz(.request.json)   # raw bytes, immutable
  {run_id}/packages/{ticker}.json                                       # AIResearchInputV1
  {run_id}/manifest.json / .sha256
```

Every document fetch checks the cache (raw bytes + ledger) before any HTTP request - a rerun over
the same candidates makes zero new requests (`test_rerun_against_cache_produces_identical_chunks`).
No existing D1.1/D2 store was touched. `data/runtime/` is gitignored repository-wide.

## O. D3 Input Package

`AIResearchInputV1` (`chunk_schema.py`) = `EvidenceBundleV2` + `list[EvidenceChunk]` +
`CandidateMaterializationRecord` + a deduplicated `source_manifest`. No AI interpretation field was
added; the six `NOT_RESEARCHED` fields are inherited unchanged from `EvidenceBundleV2`. This is the
literal package a future D3 engine reads - nothing else needs to be joined at read time.

## P. Tests

77 new tests in `backend/tests/strategy_h_v2/evidence/`: `test_filing_index.py` (6: real-shape
parsing, `/ix?doc=` resolution, case-insensitive type matching, malformed-HTML fail-soft),
`test_text_extraction.py` (13: tag stripping, table join, PDF fail-soft, section detection precision
including the false-positive-cross-reference case, and four regression tests for the
`display:none`/void-tag fix in §L), `test_chunking.py` (7: determinism, size bound,
single-source-per-chunk, section-respecting boundaries), `test_document_cache.py` (4: cache hit,
checksum stability, missing-without-client), `test_materialize.py` (13: full vs core depth, 2.02
exhibit resolution, 7.01 confirmed-vs-generic classification, future-source rejection,
exhibit-unresolved handling, full package schema validation, cache-based rerun identity). Full
suite alongside every existing Strategy H0/PV and H-V2 test: **221 passed, 0 failed.**

Two real defects were found and fixed during this stage, neither by inspection alone - both were
caught by actually running the code against real data or real assertions, matching this whole
session's working pattern:

1. **In test code**: the first version of `test_materialize.py` keyed its fake HTTP response map by
   `SourceProvenance.url` directly (a Pydantic `AnyHttpUrl` object) while the code under test builds
   plain `str` URLs to look sources up - the two never compared equal, so early runs failed with a
   confusing downstream symptom (`NOT_FETCHED` where `EXTRACTED` was expected) rather than the real
   cause. Fixed by keying the fake responses with `str(...)`; a second test
   (`test_future_source_is_never_fetched`) was found to have relied on the same mismatch to pass
   vacuously and was rewritten to assert something real.
2. **In production code**: the `display:none`/inline-XBRL extraction defect and its void-tag
   follow-on bug, both described in full in §L, found only after inspecting real materialized
   output from the full 2,010-candidate run - not something any unit test would have caught without
   first seeing what real SEC HTML actually contains.

## Q. Limitations

1. **No OCR, ever.** A PDF is `CONTENT_NOT_EXTRACTABLE` unconditionally; this repository has no PDF
   parser and D2.1 does not add one (brief §9's explicit instruction).
2. **Section detection is conservative by design, so many chunks carry `section=null`.** This is
   intentional (§I) but means a future D3 reading a `null`-section chunk must treat it as
   "somewhere in this filing," not as a specific named part.
3. **7.01 exhibit classification depends entirely on SEC's own Description text containing an
   obvious keyword, and on the real full run this never fired: 0 of 129 attempted 7.01 exhibits
   were confirmed `IR_PRESENTATION` (all materialized as generic `REG_FD_MATERIAL` instead).** This
   is the intentional false-negative bias in §H working exactly as designed - most filers'
   EDGAR-listed exhibit descriptions are terse (often just "EX-99.1" or "EX-99.2" restated), so the
   keyword check almost never has enough signal to confirm. The content is still fully materialized
   either way; only the *role label* stays generic. Loosening the keyword list was considered and
   rejected for this stage - it would require either guessing from unread content or accepting more
   false positives, and no evidence yet justifies that trade-off (§T.5).
4. **Only the most recent 2.02/7.01/other-material 8-K is resolved to an exhibit per candidate** -
   older 8-Ks within D1's 180-day window are referenced in the bundle but not materialized past
   their primary document (if selected at all under the depth policy's 8-K count cap).
5. **`chunk_token_estimate` is a rough 4-chars-per-token heuristic**, not a real tokenizer count - no
   tokenizer library was added for this stage.

## R. Commit

D2.1-only files staged and committed locally; the pre-existing 177-entry dirty worktree was never
touched. No push.

## S. Verdict

Official source references are now real, readable, source-linked, PIT-safe text: 2,010/2,010
candidates materialized with 0 errors and 0 retries across 10,016 real HTTP requests; every 10-K
(100%) and 99.8% of identified earnings releases extracted successfully; a real content-quality
defect (inline-XBRL header noise) was found by inspecting actual output and fixed before this
document was finalized, with the corrected full run re-verified from cache at zero additional
network cost. No AI interpretation, judgment, or decision field exists anywhere in the resulting
`AIResearchInputV1` package - every one of them is still the fixed `NOT_RESEARCHED` value inherited
unchanged from D2.

```text
H-V2-D2.1 = PASS
```

```text
D3 AI Research Engine = READY
```

## T. Next Action (max 7)

1. User reviews this document and a sample of real `AIResearchInputV1` packages before D3 begins.
2. If approved, begin D3 (AI Research Engine): freeze the manual-paste prompt/output contract that
   reads `AIResearchInputV1` as-is, matching the existing Strategy A/E workflow pattern unless a
   scope decision changes it.
3. Decide whether `INSUFFICIENT_EVIDENCE`-tier candidates (§Q) should be excluded from D3's initial
   candidate pool or explicitly flagged for the AI to decline judgment on.
4. Consider whether a second D2.1 pass targeting older 8-Ks (§Q.4) is worth the added fetch volume,
   or left for D3 to request on demand for a specific candidate under active research.
5. Leave the 7.01 classifier as conservative (§H/§Q.3) until a real need for tighter recall is
   demonstrated - do not loosen the keyword list without new evidence.
6. Do not resume PV-track factor ranking or any Value/Growth/Quality composite - that role remains
   retired per `H_V2_D0` §X.
7. Do not begin GPT/Claude company judgment, valuation, or paper trading until D3's own contract is
   frozen and reviewed.

## Final Declarations

```text
model used                          = Claude Sonnet 5
AI company interpretation executed? NO
Future Business evaluated?          NO
Catalyst evaluated?                 NO
Expectation Gap evaluated?          NO
APPROVE/WATCH/REJECT generated?     NO
valuation decision made?            NO
forward return used?                NO
paid data used?                     NO
existing dirty files modified?      NO
push?                               NO
```
