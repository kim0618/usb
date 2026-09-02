# USB GPT Research — Stage 4

## Purpose and manual workflow

Stage 4 preserves three independent judgments: stored Quant rank/score, manual-web-research GPT rank/scores, and Human APPROVE/REJECT. It never recalculates or updates Quant snapshots.

```text
latest COMPLETED ScannerRun for trading_date (optional score_version)
→ rank-ordered is_top8 candidates (1–8)
→ deterministic prompt copied by the user to ChatGPT
→ user performs web research and pastes one JSON object back into USB
→ atomic validation/persistence and evidence_v0 calculation
→ Human decision (0–2 APPROVE; unrestricted REJECT)
```

USB does not call an LLM or perform web search. This keeps V1 explicit, inspectable, free of automatic API cost, and consistent with the manual approval workflow. HTTP endpoints and UI are deferred to Stage 9.

## Versions and prompts

- Top-candidate prompt: `top8_research_v0`
- Stock-detail prompt: `stock_detail_research_v0`
- Import schema: `gpt_research_v0`
- Evidence formula: `evidence_v0`

Version constants live in one module. The JSON Schema embedded in prompts is generated from the Pydantic import model. A Top prompt includes stored quant rank/score, close, volume, market cap, raw/normalized metrics, weighted contributions, and score version; it does not recalculate them. A zero-candidate run is a domain error. Detail prompts deepen research for one candidate only; V1 does not merge detail output into stored Top analysis.

Prompts require current web search, fact/interpretation separation, UNKNOWN rather than invention, prompt-injection resistance, and one bare JSON object. Source priority is SEC, IR, exchange/company filings, other official sources, high-quality news, other news, then OTHER. Blogs, communities, or social media alone cannot confirm the catalyst. `catalyst` is the mandatory standard claim key when catalyst evidence exists.

## JSON contract and import

Metadata includes schema/prompt version, actual non-blank provider/model supplied by the user, aware `analysis_at`, trading date, scanner run ID, and 1–8 candidates. Candidate symbols must exactly equal the stored Top set. Ranks must uniquely cover 1..N. Five scores are 0–100; `risk_score` is higher when risk is lower and risk/reward is better. Enums and HTTP(S) URL syntax are validated, and arbitrary schema fields are rejected. `published_at` may be null; if supplied it must be aware.

Imports are limited to 1 MB and are all-or-nothing. Exact duplicate sources within one candidate are collapsed deterministically. The original input string is retained in `raw_json`; a canonical, key-sorted JSON SHA-256 hash implements duplicate detection. The same scanner run and semantic payload hash is rejected as an accidental duplicate. A meaningfully changed payload creates a new immutable analysis history row.

## Evidence Confidence

Evidence Confidence is structural evidence coverage, not the probability that GPT text is true. USB performs no URL fetch and does not verify that a declared SourceType matches URL content.

`evidence_v0` assigns source quality SEC/IR/EXCHANGE/OFFICIAL = 1.0, NEWS = 0.8, OTHER = 0.4:

- Catalyst: `40 ×` best quality for claim key `catalyst`.
- Other claims: up to four distinct normalized claim keys, `10 ×` each claim's best quality.
- Primary bonus: +10 when any SEC/IR/EXCHANGE/OFFICIAL source exists.
- Independent-domain bonus: +10 for at least two distinct lowercased URL hostnames.
- UNKNOWN penalty: -5 per distinct normalized item, capped at -25.
- Clamp to 0–100; without catalyst evidence cap the result at 49.

Example: catalyst NEWS (32), one other NEWS claim (8), two domains (+10) gives 50. Ten URLs for the same other claim still contribute only that claim's best-quality amount. Source order cannot affect the score. Evidence Confidence is stored beside GPT rank and never changes it.

## Persistence and current selection

- `gpt_analyses`: ScannerRun FK, metadata/versions/status, raw JSON, payload hash.
- `gpt_candidate_analyses`: ScannerCandidate FK, GPT rank/scores, evidence score, profiles, summaries and UNKNOWN list. Unique analysis+symbol and analysis+rank.
- `gpt_sources`: candidate-analysis FK, claim, URL/type/title/date and parsed hostname.
- `human_decisions`: analysis and ScannerCandidate FKs with one mutable current decision per analysis+symbol.

Analysis deletion cascades to candidate analyses, sources, and decisions. Scanner candidate references use RESTRICT so research cannot silently orphan its quant snapshot. The current analysis for a ScannerRun is the latest `IMPORTED` row by `analysis_at DESC`, then `id DESC`; history is retained.

## Human decisions and Stage 5+

Users may change a symbol's current APPROVE/REJECT; `decision`, optional note, and aware `decided_at` are updated. At most two symbols may currently be APPROVED in one analysis. This simple V1 policy preserves current audit metadata but is not event sourcing.

APPROVE means only “allow today's Strategy execution for this symbol.” It creates no order and is not BUY. Stage 5+ must still apply later market, Strategy, and Risk gates. Human decisions never remove candidates from Quant or future Shadow processing.

## Known limitations

V1 does not call OpenAI/Claude, browse or verify URLs, infer SourceType from content, canonicalize registrable domains beyond lowercased hostnames, persist invalid imports, merge detail research, create orders, or expose frontend/API workflow endpoints.
