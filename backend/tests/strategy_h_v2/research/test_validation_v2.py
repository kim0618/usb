"""D3.2 Validation Contract V2 - offline unit tests (brief §32). Zero model calls."""

from __future__ import annotations

from app.backtest.strategy_h_v2.research.validation_v2 import (
    LanguageVerdict,
    NumericUnit,
    PrecisionStatus,
    ProvenanceStatus,
    citation_precision,
    classify_investment_language,
    compound_claim_segments,
    is_compound_claim,
    numeric_support,
    parse_numeric_tokens,
    structural_provenance,
)


# ---------------------------------------------------------------------------------------------
# Numeric V2
# ---------------------------------------------------------------------------------------------

class TestNumericParsing:
    def test_currency_billion(self):
        tokens = parse_numeric_tokens("about $1.1 billion of growth capital")
        assert len(tokens) == 1
        assert tokens[0].unit == NumericUnit.USD
        assert tokens[0].scaled_value("about $1.1 billion of growth capital") == 1.1e9

    def test_percent(self):
        tokens = parse_numeric_tokens("organic sales fell 3.3%")
        assert [t.unit for t in tokens] == [NumericUnit.PERCENT]
        assert tokens[0].value == 3.3

    def test_basis_points(self):
        tokens = parse_numeric_tokens("offset by acquisition (+90 bps) and FX (+80 bps)")
        assert [t.unit for t in tokens] == [NumericUnit.BASIS_POINTS, NumericUnit.BASIS_POINTS]
        assert [t.value for t in tokens] == [90.0, 80.0]

    def test_multiple(self):
        tokens = parse_numeric_tokens("trading at 1.5x book value")
        assert [t.unit for t in tokens] == [NumericUnit.MULTIPLE]
        assert tokens[0].value == 1.5

    def test_small_integer_is_auditable(self):
        """The V1 blind spot this whole stage exists to close: values below 10 must not be
        silently dropped."""
        tokens = parse_numeric_tokens("Enterprise computing averaged 29% of ACV")
        assert any(t.unit == NumericUnit.PERCENT for t in tokens)
        tokens2 = parse_numeric_tokens("3 new customers were added this quarter")
        assert any(t.value == 3.0 for t in tokens2)

    def test_shares(self):
        tokens = parse_numeric_tokens("repurchased 4.9 million shares of common stock")
        assert tokens[0].unit == NumericUnit.SHARES

    def test_date_excluded_entirely(self):
        tokens = parse_numeric_tokens("payable August 18, 2026")
        assert tokens == []

    def test_year_followed_by_comma_still_recognized_as_a_date_fragment(self):
        """Batch-2's own evidence text regression: '2026,' (a year immediately followed by a
        comma, as in 'in Q2 2026, and') must not be captured as its own dangling-comma token - the
        permissive `[\\d,]*` grouping this module started with did exactly that, and the trailing
        comma then broke the year-recognition check downstream, turning an ordinary date fragment
        into a spurious COUNT value nothing in any evidence pool was ever going to support."""
        tokens = parse_numeric_tokens("in Q2 2026, and 48.19% in Q1 2026")
        assert all(t.raw != "2026," for t in tokens)
        year_tokens = [t for t in tokens if t.unit == NumericUnit.UNKNOWN]
        assert all(t.raw == "2026" for t in year_tokens)

    def test_footnote_superscript_not_misread_as_thousands_grouping(self):
        """Batch-2's own evidence text regression: 'Net income (loss)1,2' (footnote markers 1 and
        2, no separating space) must not be read as the single value 12 via a false
        thousands-separator match."""
        tokens = parse_numeric_tokens("Net income (loss)1,2 was strong")
        assert 12.0 not in [t.value for t in tokens]

    def test_bare_year_kept_as_unknown_not_dropped(self):
        tokens = parse_numeric_tokens("in fiscal 2026 the company grew")
        assert len(tokens) == 1
        assert tokens[0].unit == NumericUnit.UNKNOWN

    def test_unit_mismatch_not_conflated(self):
        """3.3 (percent) must never be treated as interchangeable with 3.3 (a bare count)."""
        claim = "organic sales fell 3.3%"
        chunk = "the company operates 3.3 million square feet of warehouse space"
        tokens = parse_numeric_tokens(claim)
        result = numeric_support(tokens[0], claim, cited_chunk_text=chunk)
        assert result.scope.value == "NOT_FOUND"


class TestNumericSupportScope:
    def test_same_number_wrong_chunk_is_not_found_by_default(self):
        claim = "revenue was $116,047 thousand in Q2"
        wrong_chunk = "no relevant figures here at all"
        right_chunk = "by-product credits were $116,047 thousand in the quarter"
        tokens = parse_numeric_tokens(claim)
        usd_token = next(t for t in tokens if t.unit == NumericUnit.USD)
        result = numeric_support(usd_token, claim, cited_chunk_text=wrong_chunk)
        assert result.scope.value == "NOT_FOUND"
        result2 = numeric_support(usd_token, claim, cited_chunk_text=right_chunk)
        assert result2.scope.value == "CITED_CHUNK"

    def test_adjacent_chunk_recoverable_not_counted_as_full_support(self):
        claim = "weighted average shares were 670,763 thousand"
        cited = "unrelated text with no share count"
        adjacent = "weighted average basic shares outstanding were 670,763"
        tokens = parse_numeric_tokens(claim)
        token = next(t for t in tokens if t.value == 670763.0)
        result = numeric_support(token, claim, cited_chunk_text=cited,
                                 adjacent_chunk_texts=(adjacent,))
        assert result.scope.value == "ADJACENT_CHUNK"

    def test_scale_and_rounding_tolerance_preserved_from_v1(self):
        claim = "goodwill was $1,430.4 million"
        chunk = "goodwill of 1,430,379 (in thousands) as of the period end"
        tokens = parse_numeric_tokens(claim)
        usd_token = next(t for t in tokens if t.unit == NumericUnit.USD)
        result = numeric_support(usd_token, claim, cited_chunk_text=chunk)
        assert result.scope.value == "CITED_CHUNK"

    def test_pipe_delimited_table_cell_amt_regression(self):
        """AMT's own D3.1 Batch-2 evidence: 'Segment Operating Profit Margin | 79 | % |' - number
        and unit sign in separate table cells. The first version of this module reported this
        exact real claim NOT_FOUND against the chunk that states it plainly."""
        claim = "The U.S. & Canada segment operating profit margin was 79% in Q2 2026."
        chunk = "| Segment Operating Profit Margin | 79  | % | | 65  | % | | 59  | % |"
        tokens = [t for t in parse_numeric_tokens(claim) if t.unit == NumericUnit.PERCENT]
        result = numeric_support(tokens[0], claim, cited_chunk_text=chunk)
        assert result.scope.value == "CITED_CHUNK"

    def test_hyphenated_and_linewrapped_basis_points_regression(self):
        """MOV's own D3.1 Batch-2 evidence: 'basis-point' (hyphenated singular) and a hard
        line-wrap inside 'basis\\npoints'."""
        t1 = parse_numeric_tokens("the 530 basis-point improvement in gross margin")
        assert [t.unit for t in t1] == [NumericUnit.BASIS_POINTS]
        t2 = parse_numeric_tokens("gross margin expanded 340 basis\npoints in the quarter")
        assert [t.unit for t in t2] == [NumericUnit.BASIS_POINTS]

    def test_bare_percent_in_unlabeled_table_column(self):
        """SRCE's own D3.1 Batch-2 evidence: a ratio table repeats the '%' sign only in its header,
        not in every data cell ('Efficiency ratio ... | 46.57 | 48.19 | 48.43')."""
        claim = "It was 46.57% in Q2 2026, 48.19% in Q1 2026 and 48.43% in Q2 2025."
        chunk = "Efficiency ratio: expense to revenue | 46.57  | | 48.19  | | 48.43  |"
        for tok in parse_numeric_tokens(claim):
            if tok.unit != NumericUnit.PERCENT:
                continue
            result = numeric_support(tok, claim, cited_chunk_text=chunk)
            assert result.scope.value == "CITED_CHUNK", tok

    def test_asymmetric_dollar_range_regression(self):
        """AIP's own D3.1 Batch-2 evidence: a claim states a range as '$5.0-9.0M' - the first bound
        gets a '$' and the second does not, an entirely ordinary way to write a range - while the
        cited chunk spells out '$5.0 - $9.0' with a dollar sign on both bounds. The claim's second
        number parses as a bare COUNT; without a COUNT->USD fallback it could never match the
        evidence's USD-typed '$9.0', regardless of how the tolerance or scale trial behaves."""
        claim = "FY2026 FCF guidance is $5.0-9.0M"
        chunk = "Free cash flow | * | $5.0 - $9.0"
        tokens = parse_numeric_tokens(claim)
        second_bound = next(t for t in tokens if t.value == 9.0)
        assert second_bound.unit == NumericUnit.COUNT
        result = numeric_support(second_bound, claim, cited_chunk_text=chunk)
        assert result.scope.value == "CITED_CHUNK"

    def test_scale_trial_tolerance_does_not_blow_up_on_division(self):
        """LNG's own D3.1 Batch-2 evidence: an unfixed version of this module's scale trial let
        the claim's own "$1.1 billion" match an unrelated "$3.1 billion" in the same (wrong) cited
        chunk, because the tolerance for the value/scale branch was not shrunk along with the
        target - only caught once magnitude-10 values became auditable at all (this whole module's
        purpose), since V1's floor happened to keep every checked value far from this blow-up."""
        claim = "about $1.1 billion invested"
        chunk = "the company reported $3.1 billion of net income for the period"
        tokens = parse_numeric_tokens(claim)
        usd_token = next(t for t in tokens if t.unit == NumericUnit.USD)
        result = numeric_support(usd_token, claim, cited_chunk_text=chunk)
        assert result.scope.value == "NOT_FOUND"

    def test_bare_percent_fallback_does_not_apply_to_low_precision_values(self):
        """The unlabeled-column fallback above is restricted to >=2 decimal places precisely so it
        cannot re-open the unit-mismatch collision the test above this one guards against."""
        claim = "organic sales fell 19%"
        chunk = "the company operates 19 distribution centers nationwide"
        tokens = parse_numeric_tokens(claim)
        result = numeric_support(tokens[0], claim, cited_chunk_text=chunk)
        assert result.scope.value == "NOT_FOUND"


class TestCitationPrecision:
    def test_precise(self):
        claim = "Q2 2026 revenue was $5.7 billion"
        chunk = "revenues of approximately $5.7 billion for the three months ended June 30, 2026"
        result = citation_precision(claim, cited_chunk_text=chunk)
        assert result.status == PrecisionStatus.PRECISE

    def test_adjacent_recoverable_matches_dsp_style_defect(self):
        claim = "growth capital invested was $1.1 billion and $2.1 billion for the half"
        cited = "unrelated FERC authorization text, no dollar figures"
        adjacent = "investing approximately $1.1 billion and $2.1 billion of growth capital"
        result = citation_precision(claim, cited_chunk_text=cited,
                                    adjacent_chunk_texts=(adjacent,))
        assert result.status == PrecisionStatus.ADJACENT_RECOVERABLE
        assert len(result.adjacent) == 2

    def test_unsupported_when_nowhere_found(self):
        claim = "revenue grew by $999,999 thousand"
        result = citation_precision(claim, cited_chunk_text="nothing relevant here")
        assert result.status == PrecisionStatus.UNSUPPORTED

    def test_no_numeric_content(self):
        result = citation_precision("management described the segment as strategically important",
                                    cited_chunk_text="anything")
        assert result.status == PrecisionStatus.NO_NUMERIC_CONTENT

    def test_missing_cited_chunk_is_unsupported_not_a_crash(self):
        result = citation_precision("revenue was $5 million", cited_chunk_text=None)
        assert result.status == PrecisionStatus.UNSUPPORTED


class TestStructuralProvenanceV2:
    SOURCES = frozenset({"SEC:0001:ACC1"})
    EVIDENCE = frozenset({"SEC:0001:ACC1:CHUNK:0", "SEC:0001:ACC1:CHUNK:1"})

    def test_ok(self):
        status = structural_provenance(
            source_id="SEC:0001:ACC1", evidence_id="SEC:0001:ACC1:CHUNK:0",
            candidate_source_ids=self.SOURCES, candidate_evidence_ids=self.EVIDENCE)
        assert status == ProvenanceStatus.OK

    def test_cross_company_rejection(self):
        """brief §9 - evidence that belongs to a different candidate entirely."""
        status = structural_provenance(
            source_id="SEC:0001:ACC1", evidence_id="SEC:9999:ACC9:CHUNK:0",
            candidate_source_ids=self.SOURCES, candidate_evidence_ids=self.EVIDENCE)
        assert status == ProvenanceStatus.EVIDENCE_WRONG_CANDIDATE

    def test_cross_source_mismatch_within_same_candidate(self):
        evidence = self.EVIDENCE | {"SEC:0002:ACC2:CHUNK:0"}
        sources = self.SOURCES | {"SEC:0002:ACC2"}
        status = structural_provenance(
            source_id="SEC:0001:ACC1", evidence_id="SEC:0002:ACC2:CHUNK:0",
            candidate_source_ids=sources, candidate_evidence_ids=evidence)
        assert status == ProvenanceStatus.EVIDENCE_SOURCE_MISMATCH

    def test_missing_fields(self):
        assert structural_provenance(source_id=None, evidence_id="x",
                                     candidate_source_ids=self.SOURCES,
                                     candidate_evidence_ids=self.EVIDENCE) == \
            ProvenanceStatus.MISSING_SOURCE_ID
        assert structural_provenance(source_id="SEC:0001:ACC1", evidence_id=None,
                                     candidate_source_ids=self.SOURCES,
                                     candidate_evidence_ids=self.EVIDENCE) == \
            ProvenanceStatus.MISSING_EVIDENCE_ID


# ---------------------------------------------------------------------------------------------
# Compound claim
# ---------------------------------------------------------------------------------------------

class TestCompoundClaim:
    def test_two_distinct_facts_flagged(self):
        text = "Revenue increased 12% and a new enterprise customer was added in the quarter."
        assert is_compound_claim(text)
        assert len(compound_claim_segments(text)) == 2

    def test_single_fact_with_reasoning_clause_not_flagged(self):
        text = ("Q2 income from operations was $4,290 million, which management attributes to "
                "mark-to-market changes on derivatives.")
        assert not is_compound_claim(text)

    def test_qualifier_sentence_not_flagged(self):
        text = "The evidence does not attribute this line to Cycuity, so part of the growth may be acquired."
        assert not is_compound_claim(text)


# ---------------------------------------------------------------------------------------------
# Investment language V2
# ---------------------------------------------------------------------------------------------

class TestInvestmentLanguageV2:
    def test_gaap_fair_value_allowed(self):
        cases = [
            "the derivatives are measured at fair value each period",
            "changes in the fair value of commodity derivatives",
            "the fair value hierarchy classifies these instruments as Level 2",
            "remeasurement of the note receivable at fair value increased its carrying amount",
            "we determined that our reporting unit had significant fair value in excess of "
            "carrying value",
        ]
        for text in cases:
            matches = classify_investment_language(text)
            assert matches, f"expected a lexical trigger in: {text!r}"
            assert all(m.verdict == LanguageVerdict.SAFE for m in matches), text

    def test_investment_fair_value_blocked(self):
        cases = [
            "the stock's fair value is $180, well above the current price",
            "we believe the fair value of the shares is materially higher than trading price",
        ]
        for text in cases:
            matches = classify_investment_language(text)
            assert any(m.verdict == LanguageVerdict.VIOLATION for m in matches), text

    def test_board_approved_allowed(self):
        cases = [
            "the Board of Directors approved a share repurchase program",
            "shareholders approved the merger agreement at the annual meeting",
        ]
        for text in cases:
            matches = classify_investment_language(text)
            assert all(m.verdict != LanguageVerdict.VIOLATION for m in matches), text

    def test_investment_approve_blocked(self):
        text = "we approve this stock as a core holding for the portfolio"
        matches = classify_investment_language(text)
        assert any(m.verdict == LanguageVerdict.VIOLATION for m in matches)

    def test_substring_false_positive_regression(self):
        """The exact D3-pilot false positive class (schema.py's own comment): 'approve'/'reject'
        as a substring of ordinary business prose must not fire at all."""
        cases = [
            "the Board of Directors approved a new manufacturing facility",
            "shareholders rejected the proposal at the annual meeting",
            "customers buy replacement parts through our distributor network",
            "the company sells HVAC equipment to commercial customers",
        ]
        for text in cases:
            matches = classify_investment_language(text)
            assert all(m.verdict != LanguageVerdict.VIOLATION for m in matches), text

    def test_unambiguous_analyst_jargon_still_blocked(self):
        for text in ("our price target is $50", "this is a strong buy",
                     "shares remain undervalued relative to peers"):
            matches = classify_investment_language(text)
            assert any(m.verdict == LanguageVerdict.VIOLATION for m in matches), text

    def test_real_batch2_defect_reproduced_and_fixed(self):
        """The exact three sentences D3.1 §I.2 found mis-flagged by V1."""
        cases = [
            "There is also contingent consideration, whose remeasurement increased its carrying "
            "amount by $2,058K based on fair value.",
            "We currently account for our derivatives at fair value, with immediate recognition "
            "of changes in the fair value in earnings.",
            "The excess of the purchase price over the fair value of assets acquired, including "
            "identifiable intangible assets, and liabilities assumed.",
        ]
        for text in cases:
            matches = classify_investment_language(text)
            assert matches and all(m.verdict == LanguageVerdict.SAFE for m in matches), text
