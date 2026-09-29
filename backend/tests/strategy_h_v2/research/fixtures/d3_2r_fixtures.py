"""D3.2R offline fixtures (brief §16): real examples pulled from D3/D3.1/D3.2's own frozen
artifacts, not synthetic cases. Used to test whether the V2 prompt/schema contract explicitly
addresses each real failure shape D3.1/D3.2 already found - with 0 new model calls. Every entry
below traces to a specific document/manifest; nothing here is invented.

Sources:
- `data/runtime/strategy_h_v2/d3/D3_1-BATCH2-20260928T103544Z.manifest.json` (frozen, gitignored)
- `docs/backtest/strategy_h_v2/H_V2_D3_1_BATCH2_VALIDATION_V1.md` §I.1, §I.2
- `docs/backtest/strategy_h_v2/H_V2_D3_2_VALIDATION_CONTRACT_REPAIR_V1.md` §F.1, §H.1, §I.1, §J.6
"""

from __future__ import annotations

#: D3.2 §H.1 - every stage-floor repair case in Batch 2, read individually. `flags_now` is the
#: evidence-flag count AFTER repair (i.e. what the final, schema-valid output actually carries).
FUTURE_BUSINESS_REPAIR_CASES: tuple[dict, ...] = (
    {"ticker": "DSP", "proposed_stage": "EARLY_EVIDENCE", "proposed_flags": 0,
     "repaired_stage": "STORY", "repaired_flags": 0, "correction": "DOWNGRADE"},
    {"ticker": "OOMA", "proposed_stage": "COMMERCIALIZING", "proposed_flags": 1,
     "repaired_stage": "EARLY_EVIDENCE", "repaired_flags": 1, "correction": "DOWNGRADE"},
    {"ticker": "AIP", "proposed_stage": "COMMERCIALIZING", "proposed_flags": 1,
     "repaired_stage": "EARLY_EVIDENCE", "repaired_flags": 1, "correction": "DOWNGRADE"},
    {"ticker": "DXCM", "proposed_stage": "COMMERCIALIZING", "proposed_flags": 0,
     "repaired_stage": "UNKNOWN", "repaired_flags": 0, "correction": "ABANDONED"},
    {"ticker": "HL", "proposed_stage": "EARLY_EVIDENCE", "proposed_flags": 0,
     "repaired_stage": "STORY", "repaired_flags": 0, "correction": "DOWNGRADE"},
    {"ticker": "LNG", "proposed_stage": "EARLY_EVIDENCE", "proposed_flags": 0,
     "repaired_stage": "STORY", "repaired_flags": 0, "correction": "DOWNGRADE"},
    {"ticker": "FLS", "proposed_stage": "EARLY_EVIDENCE", "proposed_flags": 0,
     "repaired_stage": "EARLY_EVIDENCE", "repaired_flags": 1, "correction": "FLAG_ADDED"},
    {"ticker": "MOV", "proposed_stage": "EARLY_EVIDENCE", "proposed_flags": 0,
     "repaired_stage": "STORY", "repaired_flags": 0, "correction": "DOWNGRADE"},
    {"ticker": "PSNL", "proposed_stage": "EARLY_EVIDENCE", "proposed_flags": 0,
     "repaired_stage": "STORY", "repaired_flags": 0, "correction": "DOWNGRADE"},
)

#: MRVI is excluded from the table above on purpose: it never reached a repaired (final valid)
#: output, so there is no "repaired_stage" to report - see `telemetry_contract_v2` and D3.2 §J.7.
MRVI_UNREPAIRED_CASE = {
    "ticker": "MRVI", "proposed_stage": "COMMERCIALIZING", "proposed_flags": 1,
    "repaired_stage": None, "final_status": "SCHEMA_VALIDATION_FAILED",
    "failure_reason": ("stage=COMMERCIALIZING requires at least 2 evidence flag(s), got 1 - a "
                       "future-business item cannot be staged above what its own evidence flags "
                       "support @ future_business.0"),
}

#: D3.1 §I.2 / D3.2 §G - the exact three sentences from Batch 2's own filings that the V1
#: `fair value` ban mis-flagged. All three are GAAP measurement language about the company's own
#: assets/liabilities, none is a stock valuation opinion.
FAIR_VALUE_GAAP_SAFE_EXAMPLES: tuple[str, ...] = (
    "There is also contingent consideration, whose remeasurement increased its carrying "
    "amount by $2,058K based on fair value.",
    "We currently account for our derivatives at fair value, with immediate recognition "
    "of changes in the fair value in earnings.",
    "The excess of the purchase price over the fair value of assets acquired, including "
    "identifiable intangible assets, and liabilities assumed.",
)

#: A stock-valuation opinion, in the same lexical neighbourhood as the safe examples above -
#: what INVESTMENT_LANGUAGE_CLARIFICATION must still block.
FAIR_VALUE_INVESTMENT_OPINION_EXAMPLE = (
    "The stock's fair value is $180, well above the current price."
)

#: D3.2 §J.2/§I.1 - real citation-precision defects: a claim whose text is accurate but whose
#: evidence_id points at the wrong (though nearby, same-source) chunk. `wrong_evidence_id` is what
#: the real D3.1 output actually cited; `correct_evidence_id` is where the fact actually is.
CITATION_PRECISION_DEFECT_EXAMPLES: tuple[dict, ...] = (
    {"ticker": "LNG", "claim_text": (
        "Growth capital remains heavy: about $1.1 billion invested in Q2 2026 and $2.1 billion "
        "in the first half, with part of it funded by equity."),
     "wrong_evidence_id": "SEC:0000003570:0000003570-26-000030:EX-99.1:CHUNK:1",
     "correct_evidence_id": "SEC:0000003570:0000003570-26-000030:EX-99.1:CHUNK:0"},
    {"ticker": "HL", "claim_text": (
        "Weighted average basic shares were 670,763 thousand in Q2 2026 and 670,392 thousand in "
        "Q1 2026. No equity was issued in Q2 2026. In 1H25, stock issuance brought in $174,132 "
        "thousand."),
     "wrong_evidence_id": "SEC:0000719413:0001193125-26-333077:EX-99.1:CHUNK:15",
     "correct_evidence_id": "SEC:0000719413:0001193125-26-333077:EX-99.1:CHUNK:14"},
    {"ticker": "PSNL", "claim_text": (
        "Q2 2026 revenue included related-party revenue of $8.4 million, against $1.9 million in "
        "Q2 2025. The related parties are Tempus and Merck, each of which owns more than 10% of "
        "the common stock."),
     "wrong_evidence_id": "SEC:0001527753:0001193125-26-332748:EX-99.1:CHUNK:4",
     "correct_evidence_id": "SEC:0001527753:0001193125-26-076615:CHUNK:134"},
)

#: D3.2 §J.6 - a manual sample of what the compound-claim heuristic flags. `genuinely_compound=True`
#: means two independent factual propositions sharing one citation (the brief §6/§7 concern);
#: `genuinely_compound=False` means a noun-phrase list the detector cannot tell apart from one
#: (the reason D3.2 declared the detector a screening upper bound, not a gate).
COMPOUND_CLAIM_EXAMPLES: tuple[dict, ...] = (
    {"text": ("FY2025 segment revenues were: Synodex $7.3 million, down about 8% after a customer "
              "contract was terminated, and Agility $23.5 million, up about 9% on higher "
              "subscription volumes."),
     "genuinely_compound": True,
     "why": "two distinct segments, each with its own independent fact"},
    {"text": "International markets are 56.7% of total sales.",
     "genuinely_compound": False, "why": "single proposition, no conjunction at all"},
    {"text": ("As of January 31, 2026 there were about 1.4 million Ooma Business and Ooma "
              "Residential core users, including 164,000 from the FluentStream and Phone.com "
              "acquisitions."),
     "genuinely_compound": False,
     "why": "\"and\" joins two proper-noun lists (product names, acquisition names), not two "
           "independent propositions"},
)

#: D3.2 §F.1 - real numeric formatting variants the batch's own evidence actually used, that a
#: naive parser (or a model taught to only recognize "$X million"/"X%") would miss.
NUMERIC_FORMAT_EXAMPLES: tuple[dict, ...] = (
    {"claim": "The U.S. & Canada segment operating profit margin was 79% in Q2 2026.",
     "evidence_style": "number and '%' in separate table cells: 'Segment Operating Profit Margin "
                       "| 79 | % |'"},
    {"claim": "Excluding IEEPA refunds, Q2 gross margin expanded 340 basis points.",
     "evidence_style": "hyphenated singular: 'basis-point', or line-wrapped: 'basis\\npoints'"},
    {"claim": "It was 46.57% in Q2 2026, 48.19% in Q1 2026 and 48.43% in Q2 2025.",
     "evidence_style": "a ratio table repeats '%' only in its header row, not every data cell"},
    {"claim": "FY2026 FCF guidance is $5.0-9.0M.",
     "evidence_style": "a range's second bound carries no '$' of its own ('$5.0 - $9.0' in the "
                       "source has one per bound; the claim's own prose does not)"},
)
