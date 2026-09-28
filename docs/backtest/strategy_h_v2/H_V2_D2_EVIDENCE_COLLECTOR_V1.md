# Strategy H-V2 - D2 Research Evidence Collector V1

- declared: 2026-09-28
- status: **EVIDENCE COLLECTION - NOT AN INTERPRETATION, NOT AN ALPHA RESULT**
- stage: **H-V2-D2**
- parent contracts: `H_V2_D0_ARCHITECTURE_RESEARCH_CONTRACT_V1.md`,
  `H_V2_D1_UNIVERSE_CHANGE_ENGINE_V1.md`, `H_V2_D1_1_UNIVERSE_DATA_COVERAGE_V1.md`
- implementation: `backend/app/backtest/strategy_h_v2/evidence/` (new package: `sources.py`,
  `filing_selection.py`, `bundle.py`, `collector.py`), `backend/app/dev/run_strategy_h_v2_d2.py`
- tests: 39 new (`backend/tests/strategy_h_v2/evidence/`), 183 passing total alongside the existing
  Strategy H0/PV and H-V2 D1/D1.1 suites, 0 failures
- pilot run: `data/runtime/strategy_h_v2/d2/D2-PILOT-20260928T061049Z/` (20 candidates, gitignored)
- full run: `data/runtime/strategy_h_v2/d2/D2-20260928T061101Z/` (2,010 candidates, gitignored)

D2 converts D1.1's frozen E3 output (2,010 `P1_HIGH`/`P2_MEDIUM` Research Candidates) into
source-linked, PIT-safe, immutable Evidence Bundles for a future D3 AI Research Engine. It changes
none of D1/D1.1's frozen E1/E2/E3 logic - it only adds the raw-source layer D0's contract always
specified (`filings`, `material_events`, source provenance) and D1 had left as code-only metadata.
No company is judged, rated, or classified as good or bad anywhere in this stage.

## A. Repository State

- branch `main`, HEAD before this stage: `0bdd292` (`fix(strategy-h-v2): expand universe
  fundamental coverage`), confirmed to match D1.1's commit.
- `ab4c0a5` (D0), `4bbafa6` (D1), `0bdd292` (D1.1) all present in `git log`, exactly as declared.
- origin: ahead 18, behind 0. Pre-existing dirty worktree (177 entries) untouched throughout.

## B. D1.1 Input State

D1.1's real rerun (`D1-20260928T054937Z`) is the frozen input: 5,192-security universe, 2,716
`ELIGIBLE`, 1,952 `INELIGIBLE`, 524 `DATA_NOT_READY`, 2,010 `P1_HIGH`/`P2_MEDIUM` candidate evidence
stubs already written to `data/runtime/strategy_h_v2/d1/D1-20260928T054937Z/candidates/`. D2 reads
these 2,010 files as-is; none of D1's field values are recomputed or altered.

## C. Existing Assets Reused

| Asset | Reused as |
|---|---|
| `strategy_c_e0.sec_store.rows_of` | Flattens a cached submissions document into the six fields (`accessionNumber`, `form`, `filingDate`, `acceptanceDateTime`, `items`, `primaryDocument`) `filing_selection.py` needs - no new SEC response parsing was written. |
| `strategy_c_e0.sec_store.ledger_path` | Reads each cached document's own fetch ledger (`fetched_at`, `file_sha256`) for D2's provenance `fetched_at`/`checksum` fields. |
| D1.1's local submissions cache (H0, PV2C, H0.5, and `strategy_h_v2/d1_1`) | The entire source of D2's filing metadata - zero new HTTP requests anywhere in this stage. |
| `app.dev.run_strategy_h_v2_d1.find_submission_doc`/`SUBMISSION_ROOTS` | Reused directly, not reimplemented, for the multi-root cache lookup. |
| Strategy A/E's "external documents are research data, not instructions" contract (`docs/GPT_RESEARCH.md`) | The basis for `SOURCE_BOUNDARY = "UNTRUSTED_RESEARCH_DATA"`, fixed on every `SourceProvenance` (§I). |
| D1's `NotResearched` enum pattern (`evidence_bundle.py`) | Extended with a sixth field, `decision`, in D2's bundle. |

## D. D2 Source Contract

Source types, from D0's frozen priority order:

```text
SEC_10K, SEC_10Q, SEC_8K, EARNINGS_RELEASE, EARNINGS_CALL, IR_PRESENTATION,
MAJOR_NEWS, USER_SUPPLIED, OTHER
```

Every `SourceProvenance` (`evidence/sources.py`) is `extra="forbid"` Pydantic with: `source_id`,
`source_type`, `publisher`, `title`, `url`, `published_at`, `date_precision`
(`DATETIME`/`DATE_ONLY`/`UNKNOWN` - D1.1 brief §16's "date-only + confidence" requirement),
`available_at`, `fetched_at`, `checksum`, `accession`, `confidence`, `pit_eligible`, `verified`
(`False` only for `USER_SUPPLIED`), and the fixed `source_boundary`. `user_supplied_source()` is the
only constructor that can produce `verified=False`; every source D2 itself collects is `True` by
construction.

## E. Evidence Bundle Schema

`EvidenceBundleV2` (`evidence/bundle.py`) is D1's Candidate Evidence Stub plus the raw-source layer:

```json
{
  "identity": {}, "market": {}, "fundamentals": {}, "fundamental_changes": {},
  "balance_sheet": {}, "cashflow": {}, "price_context": {}, "earnings": {},
  "filings": [], "company_releases": [], "earnings_materials": [],
  "investor_materials": [], "material_events": [], "source_manifest": [],
  "data_quality": {}, "unknown_fields": []
}
```

The code-owned sections (`identity` through `earnings`) are copied verbatim from D1's stub - D2
never recomputes a number. The six AI-interpretable fields (`future_business`, `catalysts`,
`expectation_gap`, `competitive_position`, `thesis`, and the new `decision`) are fixed to
`NOT_RESEARCHED`; a Pydantic enum, not a string default, so no value other than
`NOT_RESEARCHED` can be assigned to them at all (`test_ai_fields_cannot_be_fabricated`). A
model-level validator additionally rejects a fixed list of interpretive phrases
(`undervalued`, `strong moat`, `APPROVE`, `good company`, ...) anywhere inside any code-owned
section, so an interpretation cannot even be smuggled into a field that is nominally numeric
(`H_V2_D1_1` brief §37 - "가능하면 unit test로 차단"; here it is a schema-level constraint, not just
a test).

## F. SEC Evidence

`filing_selection.py`'s deterministic, PIT-safe selection (thresholds justified by research
usefulness, never adjusted after seeing a result):

```text
latest_10k:   most recently accepted 10-K/10-K-A as of data_cutoff
recent_10q:   up to 4 most recently accepted 10-Q/10-Q-A (MAX_RECENT_10Q - a year of quarters,
              matching E2's own 4-period trend window)
recent_8k:    every 8-K/8-K-A accepted within the trailing 180 days of data_cutoff
              (MATERIAL_8K_WINDOW_DAYS - twice D0's stated thesis horizon)
```

A row with no parseable `acceptanceDateTime`, or one accepted after `data_cutoff`, is excluded and
counted in `excluded_future_filings` - never guessed, never silently dropped without a trace (§I).

8-K item classification (`sources.py::classify_8k_items`) uses SEC's own fixed, published General
Instructions to Form 8-K - a lookup table, not an inference, the same way `strategy_h0.facts`
reuses SEC's XBRL tag taxonomy. An item code the table does not recognize is reported as
`UNKNOWN_ITEM_CODE`, never guessed. Filing URLs are constructed from SEC EDGAR's own deterministic
Archives path (`.../edgar/data/{cik}/{accession}/{primaryDocument}`) - spot-checked against a real
bundle (Agilent's actual 10-K) and confirmed correct.

## G. Earnings Release Evidence

D2 does not fetch exhibit content. It identifies **candidate** earnings-release exhibits by their
official SEC item code: an 8-K carrying item `2.02` ("Results of Operations and Financial
Condition") is referenced in `company_releases`, with `item_category = EARNINGS_RESULTS`, pointing
back to that filing's `SourceProvenance` in `filings`/`source_manifest`. This satisfies D1.1 brief
§11's "SEC 8-K attached exhibit" priority without downloading unbounded exhibit text for 2,010
candidates. Full run: **3,717 company-release references across 1,804 of 2,010 candidates (90%)**.

## H. Earnings Call / IR / News Availability

- **Earnings call transcripts**: no transcript provider exists anywhere in this repository
  (confirmed by search before implementation, D1.1 brief §12). `FULL`-depth bundles record an
  explicit `SOURCE_NOT_AVAILABLE` marker in `earnings_materials`; `CORE`-depth bundles do not even
  attempt it. Not required for D2 PASS.
- **Investor presentations**: identified the same way as earnings releases, via SEC's own item
  `7.01` (Regulation FD Disclosure), the item code companies commonly use when filing an investor
  deck as an exhibit. **2,982 references across 1,169 of 2,010 candidates (58%)**. Not assumed
  available for every company.
- **Major news**: no news provider exists in this repository. `FULL`-depth bundles record an
  explicit `MAJOR_NEWS = NOT_CONNECTED` marker in `source_manifest`; no fabricated source is ever
  produced (D1.1 brief §14).

## I. PIT Audit

Every `SourceProvenance` this stage produces for a real SEC filing sets `published_at = available_at
= acceptanceDateTime` (`date_precision = DATETIME`) and `pit_eligible = (published_at is not None)`.
`filing_selection.select_filings` rejects, before any bundle is built, every row whose acceptance
time is missing or after `data_cutoff` - `test_pit_excludes_filing_after_cutoff_from_bundle` and
`test_missing_acceptance_time_is_excluded_not_guessed` exercise this directly. `data_cutoff` for
every candidate is the **same value D1.1 already froze** for that candidate (read from its own
`CandidateEvidenceStub.data_cutoff`), not "now" - so D2 cannot let a filing that postdates the
original E1/E2/E3 decision leak into that candidate's evidence.

## J. Provenance

`MaterialEventRef.source_id` must resolve to a real `SourceProvenance` already present in `filings`
or `source_manifest`; a `model_validator` rejects any bundle containing an orphan reference before
it can ever be constructed (`test_orphan_material_event_rejected`). `source_manifest` is
deduplicated by `source_id` (`test_source_manifest_deduplicated`). Every filing reference carries
the checksum of the cached document it was read from (`test_submissions_checksum_is_preserved...`),
so a future audit can verify which exact cached bytes a given fact provenance traces back to.

## K. Collection Depth P1/P2

```text
P1_HIGH -> CandidateSource.E3_P1_HIGH, CollectionDepth.FULL
  = required SEC filings + an explicit attempt at earnings-call/news identification
    (honest NOT_AVAILABLE/NOT_CONNECTED markers, not a richer real result today)

P2_MEDIUM -> CandidateSource.E3_P2_MEDIUM, CollectionDepth.CORE
  = required SEC filings only; optional sources are not attempted at all
```

This is D0/D1's research-priority meaning carried through unchanged - `P1_HIGH` is not "buy," it is
"research this one more deeply first" (`H_V2_D0` §E3). Earnings-release and investor-presentation
candidates are identified for **both** depths (they come free from the required 8-K set already
fetched), matching §K/§26's framing that this depth split is a budget allocation, not a quality
signal.

## L. Pilot Results

`D2-PILOT-20260928T061049Z`, 20 deterministic candidates (alphabetically first by ticker, not
outcome-selected): **20/20 bundles written, 0 errors, 20/20 `COMPLETE`**, depth split 5 `FULL`/15
`CORE` (matching the sample's own P1/P2 mix). Validated schema, PIT logic, source-URL construction,
and artifact size before the full run.

## M. Full Collection Results

`D2-20260928T061101Z`, all 2,010 `P1_HIGH`/`P2_MEDIUM` candidates, run against already-local data
(no network calls), wall time well under a minute:

```text
input candidates:        2,010
bundles written:          2,010   (100%)
errors:                        0
COMPLETE:               2,010   (100%)
PARTIAL / INSUFFICIENT:        0 / 0

depth:      FULL 200  (all P1_HIGH)   CORE 1,810  (all P2_MEDIUM)

SEC_10K references:     2,010  (100% - every candidate has a local latest 10-K)
SEC_10Q references:      8,033  (~4.0/candidate, at the MAX_RECENT_10Q cap)
SEC_8K references:      11,187  (~5.6/candidate within the 180-day window)
company_releases:         3,717  refs, 1,804/2,010 candidates (90%) have >=1
investor_materials:       2,982  refs, 1,169/2,010 candidates (58%) have >=1
material_events (other): 18,023  refs (governance, agreements, etc.)
EARNINGS_CALL markers:      200  (= every FULL-depth candidate, all NOT_AVAILABLE)
MAJOR_NEWS markers:          200  (= every FULL-depth candidate, all NOT_CONNECTED)
```

No candidate reduction, no threshold change, no re-selection to hit a target number - this is D1.1's
2,010-candidate output collected as-is (D1.1 brief §36).

## N. Bundle Completeness

100% `COMPLETE` is expected, not suspicious: D2's completeness check (`identity`, resolved
fundamentals, at least one filing found, at least one non-`UNKNOWN` E2 change, market context
present) mirrors exactly what E1's `ELIGIBLE` gate already required before a candidate could reach
E3 at all (D1.1's fixed state model, `H_V2_D1_1` §C). `COMPLETE` means research-input completeness,
never "this is a good company" (§24 of the brief; enforced structurally by §E's banned-language
validator, which would reject the word "good" itself if it ever appeared).

## O. Cache / Storage

```text
data/runtime/strategy_h_v2/d2/{run_id}/
  manifest.json / .sha256      # collection-quality metrics only, no forward return
  bundles/{ticker}.json        # EvidenceBundleV2
```

No new raw-source cache was created by D2 - it reads D1.1's existing
`data/runtime/strategy_h/{h0,h_pv2c,h0_5}` and `data/runtime/strategy_h_v2/d1_1/sec_raw` stores
read-only. `data/runtime/` is gitignored repository-wide; nothing here is committed.

## P. Manual Research Path

D0's manual-input path (§31 of the D1.1 brief) is `CandidateSource.MANUAL` - a distinct enum value
from `E3_P1_HIGH`/`E3_P2_MEDIUM`, checked structurally
(`test_manual_candidate_source_is_distinguishable_from_e3_output`). `assemble_evidence_bundle_v2`
accepts any `candidate_source`/`collection_depth` combination, so a future manual-request tool can
call it directly with a hand-built candidate stub and `candidate_source=MANUAL` without pretending
to be an E3 output. Not wired into `run_strategy_h_v2_d2.py`'s batch runner in this stage - that
runner is E3-only by design; a manual single-ticker CLI is a natural, separate follow-up.

## Q. User-Supplied Evidence

`sources.user_supplied_source()` is the only path that can construct a `SourceType.USER_SUPPLIED`
source, and it always sets `verified=False` and `pit_eligible=False` - a user-supplied source can
never silently pass as an official one (`test_user_supplied_source_is_unverified_by_construction`).
No user-supplied evidence was collected in this D2 run; the type exists for a future stage.

## R. Tests

39 new tests in `backend/tests/strategy_h_v2/evidence/`: `test_sources.py` (8: item-code
classification, unknown-code honesty, aware-datetime, fixed source boundary, user-supplied
unverified-by-construction, extra-field rejection), `test_filing_selection.py` (8: PIT
future/missing-acceptance rejection, latest-10-K selection, 10-Q cap, 8-K window boundary),
`test_bundle.py` (11: extra field, aware datetime, AI-field lock, orphan-reference rejection valid
and invalid cases, banned-language rejection parametrized over 5 phrases, manual-vs-E3
distinguishability), `test_collector.py` (12: P1 full vs P2 core behavior, unavailable-source
markers, failed-optional-does-not-break-core, manifest dedup, completeness downgrade, rerun
idempotence, manual provenance, PIT exclusion, checksum preservation end-to-end). Full suite
alongside the existing Strategy H0/PV and H-V2 D1/D1.1 tests: **183 passed, 0 failed**.

## S. Changed / New Files

New: `backend/app/backtest/strategy_h_v2/evidence/{__init__,sources,filing_selection,bundle,
collector}.py`, `backend/app/dev/run_strategy_h_v2_d2.py`, `backend/tests/strategy_h_v2/evidence/
{helpers,test_sources,test_filing_selection,test_bundle,test_collector}.py`, this document. No D0,
D1, or D1.1 file was modified.

## T. Commit

D2-only files staged and committed locally; the pre-existing 177-entry dirty worktree was never
touched. No push.

## U. Limitations

1. **No exhibit full text is fetched.** `company_releases`/`investor_materials` are *candidate*
   references (an item-2.02 or item-7.01 8-K exists), not a confirmation that the attached exhibit
   is in fact an earnings release or a deck, and not its content. A future stage that needs the
   actual text would fetch it per-accession, bounded, on top of this reference layer.
2. **Earnings-call transcripts and news remain architecturally absent**, not degraded - no provider
   exists in this repository for either, and D2 correctly reports that rather than inventing a
   result.
3. **8-K item-to-category mapping is a coarse rollup**, e.g. item `2.01` covers both acquisitions and
   dispositions with no way to tell which from the code alone; `material_events` preserves the exact
   official item code and label alongside the rollup so nothing is lost, but the category itself
   should not be over-read.
4. **`DatePrecision.DATE_ONLY`/`UNKNOWN` are schema-supported but unexercised by this run** - every
   SEC source has a full `acceptanceDateTime`, so no real bundle currently uses date-only precision.
   The field exists for a future non-SEC source (e.g. a news item with only a publish date).
5. **The manual research path is a library function, not yet a CLI/API entry point** - §P.

## V. Verdict

```text
H-V2-D2 = PASS
```

Research Candidates convert into source-linked, AI-ready evidence bundles (2,010/2,010, 0 errors).
Every numeric/factual field remains code-owned, enforced by schema, not just convention. Sources are
PIT-safe by construction and audited directly. Required official evidence (SEC filings) is
reproducible from already-cached, checksummed data. Optional-source failures fail soft (`CORE`
depth never attempts them; `FULL` depth records the honest unavailable/not-connected result without
ever blocking a bundle). Evidence is immutable per run (`data/runtime/strategy_h_v2/d2/{run_id}/`,
never overwritten). No AI investment judgment - or even a hard-coded stand-in for one - occurred
anywhere in this stage.

```text
D3 AI Research Engine = READY
```

## W. Next Action (max 7)

1. User reviews this document and a sample of real bundles (e.g. `data/runtime/strategy_h_v2/d2/
   D2-20260928T061101Z/bundles/`) before D3 begins.
2. If approved, begin D3 (AI Research Engine): freeze the manual-paste prompt/output contract
   (matching the existing Strategy A/E workflow pattern per `H_V2_D0` §Z unless a scope decision
   changes it), consuming `EvidenceBundleV2` as-is.
3. Decide whether a bounded, per-accession exhibit-text fetch (§U.1) is worth adding before D3, or
   left for D3 to request on demand for a specific candidate it is actively researching.
4. Build the manual single-ticker CLI on top of `assemble_evidence_bundle_v2` (§P) if the manual
   research path is needed before D3 ships.
5. Leave earnings-call and news sources as `NOT_AVAILABLE`/`NOT_CONNECTED` until a specific provider
   is proposed and separately reviewed - do not silently add a scraper.
6. Do not resume PV-track factor ranking or any Value/Growth/Quality composite on this evidence -
   that role remains retired per `H_V2_D0` §X.
7. Do not begin GPT/Claude company judgment, valuation, or paper trading until D3's own contract is
   frozen and reviewed.

## Final Declarations

```text
model used                              = Claude Sonnet 5
forward returns used?                   NO
alpha ranking used?                     NO
GPT/Claude company judgment executed?   NO
Expectation Gap evaluated?              NO
valuation decision made?                NO
APPROVE/WATCH/REJECT generated?         NO
investment decision generated?          NO
paid data used?                         NO
existing dirty files modified?          NO
push?                                   NO
```
