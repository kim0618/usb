# H-V2-D6 - Integrated Decision Engine Pilot

```
H-V2-D6
=
READY WITH LIMITATIONS

D4 universe              13
D5 valuation ready        9
D6 decision eligible      8

APPROVE   0
WATCH     6
REJECT    2

live model calls          0
cost                      $0.00
push                      NO
```

Strategy H has had three layers that each worked and never met. This step is the first time one issuer
is looked at through all three at once - what the company is (D3), what the market expects of it (D4),
and what the price implies (D5) - and a single judgement comes out the other side.

Zero APPROVE, and that is the finding rather than a disappointment. Not one of the thirteen issuers D4
graded carries a `POSITIVE` or `WIDE_POSITIVE` expectation gap: six are `NEUTRAL`, five are `UNKNOWN`,
and two produced no D4 output at all. D0 §L froze the rule that the gap must be `POSITIVE` or
`WIDE_POSITIVE` at `HIGH` or `MEDIUM` confidence for APPROVE to be *reachable*, so APPROVE was
unreachable on this sample before any valuation was computed. The engine reports that as the binding
clause on every eligible issuer and does not route around it.

The two REJECTs are the more interesting output, because they come from two different triggers. IDCC's
entire fair value range sits below its price - its EV/EBIT is at the 94.7th percentile of its own
two-year history. AEYE's future business is `STORY` with no §J evidence of any kind and no catalyst
that is both material and dated.

The deepest finding is not about any issuer: **D5-D2's window rule has a hole, and applying it to
thirteen new issuers is what exposed it.** The rule asks whether the FULL_2Y panel is `TRENDING_STRONG`
and shortens the window if it is. VRRM's EV/EBIT rose over the first half of the panel (rho +0.830) and
collapsed over the second (rho -0.918), so the two cancel to a full-panel rho of -0.522, the rule
concludes the panel did not drift strongly, and it keeps the full panel - while RECENT_12M reads -0.930.
The published TP1 is therefore **+404.7%** on an issuer whose price fell 87% over the panel. The rule is
not adjusted here, the counterfactual is published, and §K.1 is the whole argument.

Not an alpha result, not a backtest, not a forward test, and not a statement about any issuer.

---

## A. Purpose

D5-D2 ended with a blocking prerequisite rather than a defect. Its §Q:

> D6's whole purpose is `D3 company quality + D4 expectation gap + D5 valuation -> APPROVE / WATCH /
> REJECT` and the join is **empty**. D4 graded 13 issuers, D5 valued 5, and the intersection is 0.

It named three ways out and this step takes the first, which it called "the cheapest path by a wide
margin": apply the identical D5-D2 evaluator to the thirteen issuers D4 already graded. So the step has
two halves and they are not equally novel.

| half | what it is | how much is new |
|---|---|---|
| D5 on D4's thirteen | D5-D2's evaluator, unchanged, with a new declared judgement table | no new rule, no new arithmetic |
| the decision engine | D0 §N implemented as a conjunction over the three stored layers | all of it, and it owns no number |

What this step is for is the pipeline question - whether the three layers join on one issuer at all -
and that question does not care how the issuers were chosen. What it is not for is measuring anything.

### A.1 Repository State

```
branch                          main
HEAD                            64612be  feat(strategy-h-v2): value an issuer from its own observed multiples
origin/main vs HEAD             behind 0 / ahead 2   (D5-D1 and D5-D2, neither pushed)
staged                          0
modified (tracked, all paths)   47
untracked (all paths)           534
H-V2 tracked files dirty        1   backend/app/dev/run_strategy_h_v2_d5_d2.py  (this step's own edit)
```

The 47 modified and 534 untracked files belong to concurrent crypto and frontend sessions and are not
touched here; two of those sessions were running their own test suites while this step ran (§L). The commit is path-specific (§M.3). The one H-V2 tracked file this step modifies is the
D5-D2 runner and §B.3 is the proof the modification changed nothing.

---

## B. D3 / D4 / D5 Handoff

### B.1 What each layer hands over

| layer | artifact | what D6 reads | what D6 may not do |
|---|---|---|---|
| D3 | `h_research_interpretation_v2` | business model, fundamental change, growth durability, future business stages and §J evidence flags, catalysts, risks, invalidation candidates, unknown fields | author a research fact, re-read a filing |
| D4 | `h_expectation_gap_analysis_v2` | `expectation_gap`, `expectation_gap_confidence`, `confidence_ceiling`, `d6_approve_precondition` | recompute the gap, convert an absence into a state |
| D5 | D5-D2 valuation row | status, confidence and drivers, primary and secondary method, contract window, price, Bear / TP1 / TP2, three upsides, reconciliation | recompute a target, adjust a multiple |

`D6_OWNS_NO_NUMBERS`, from the engine module:

> D6 performs no arithmetic on a price, a multiple, a fundamental or a target. Every number it reports
> is carried through from the D5 row it was given, and the only numeric operation in this module is a
> comparison against zero to ask whether an upside D5 already computed is positive.

### B.2 The authoritative runs, and the one that looks newer but is not

| tier | D4 run | D3 leg | issuers |
|---|---|---|---|
| Tier A V3 | `D4_2_A-20260930T012115Z` | `D3_3-20260929T021659Z` | BSY GOOG SCCO |
| Tier B | `D4_B-20260930T053146Z` | same run's `d3_leg` | CRK DORM FRPT IDCC SPSC TG |
| BR-C | `D4_BR_C-20261001T005758Z` | same run's `d3_leg` | AEYE COLL FG VRRM |

`D4_2_A-20260930T034348Z` is newer on disk and holds a SCCO analysis, and it is **not** used: it is the
D4-S1 limited live smoke, a different step's artifact for the same issuer. Taking the newest file per
ticker rather than the run the result documents name would have silently swapped one issuer's evidence
for another step's.

Two of these three steps carry a FAIL verdict and both are usable, for reasons the documents state:

- **Tier B = FAIL**, on E1 alone: 4 of 6 final outputs against a frozen ≥95%. The two that produced
  nothing are FRPT and SPSC, and §J.2 is what D6 does with them. The content gates all passed.
- **D4-BR-C = FAIL**, on E7, one finding: COLL's output contains "there was neither a run-up nor a
  sell-off before the event" and the detector matched `\bsell\b` inside "sell-off". D4-E7R replaced the
  word list with a decision-shape classifier, replayed every stored output at $0, removed that one
  false positive, exposed no new violation, and recorded `D4 = COMPLETE` while leaving BR-C's historical
  verdict standing. So COLL's output is usable and the step that failed on it stays failed.

### B.3 The one edit to D5-D2, and the proof it changed nothing

`value_issuer` took its per-issuer judgement from a module-level table. It now takes an optional
`judgements` parameter whose default **is** that table:

```python
def value_issuer(ticker, panel, row, *, judgements=METHOD_JUDGEMENTS) -> dict:
```

The alternative was copying three hundred lines of selection, window and reconciliation logic into this
step, and a second copy of an evaluator is how two samples stop being held to the same arithmetic. The
proof that D5-D2 is unaffected is not an argument:

```
D5_D2-20261004T045748Z.json   (before the edit)   sha256 80b3a903...0804484c
D5_D2-20261004T053947Z.json   (after the edit)    sha256 80b3a903...0804484c
diff -q                                            IDENTICAL
```

All five of D5-D2's fair values, its ten target prices, its fifteen upsides, its two abstentions and its
`D5-D1 agreement: compared 19 mismatches 0` reproduce bit for bit with the parameter in place.

### B.4 The judgement table was declared before any fair value existed

`METHOD_JUDGEMENTS` for the thirteen is written from two things that exist without computing a single
scenario: each issuer's D5-D1-layer fundamentals (TTM revenue, margin, cash flow, book equity, market
cap, net debt, and which of the seven methods resolve at all) and its stored D3 business model. The
sequence was literally that - the probe that produced §H.1's table ran first, the table was written
against it, and the valuation ran afterwards.

This matters because `primary_preference` is an ORDERED list and code takes the first entry whose
observed panel is contract-eligible. A model that chose the method after seeing the target price would
be fitting, and the fitting would be invisible afterwards. Two structural checks are asserted in tests:
no judgement names a method in its own `unsuitable` map, and every `NOT_SUITABLE` assertion carries an
argument longer than a label.

---

## C. Overlap Sample

### C.1 The three counts the brief asks for

```
13 total          the issuers D4 graded
 9 D5 valuation ready
 8 D6 decision eligible
```

| | issuers | why |
|---|---|---|
| D5 valuation ready | SCCO DORM IDCC SPSC TG AEYE COLL FG VRRM | a declared method had a contract-eligible observed panel |
| D5 abstained | BSY GOOG CRK FRPT | §I.3 |
| D6 decision eligible | SCCO DORM IDCC TG AEYE COLL FG VRRM | D3 valid **and** D4 evaluated **and** D5 ready |
| not decision eligible | BSY GOOG CRK FRPT SPSC | §C.3 |

SPSC is the one issuer that is valuation-ready and not decision-eligible, and it is the case §10 of the
brief exists for: D4 ran on it three times and its contract declined every attempt.

### C.2 Selection effect, stated rather than footnoted

D5-D0 drew its twelve by a seeded hash over the whole D2.1 package universe and said why: "No exclusion
list and no stratification by D3 or D4 outcome: whether valuation *data* exists has nothing to do with
how a candidate scored."

These thirteen are the opposite. They exist because D4's Tier A, Tier B and BR-C validation runs needed
issuers, and those tiers picked for contract-validation reasons - BR-C's four were chosen specifically
for being disjoint from every earlier tier. The consequences are specific:

- "D5 valued 9 of 13" is a statement about D4's sample, not about valuation coverage in general. D5-D1
  measured 7 of 12 on its blind sample; the two numbers are not comparable.
- Any count of APPROVE / WATCH / REJECT here describes thirteen issuers chosen upstream.
- Nothing about the gap distribution - six NEUTRAL, five UNKNOWN, zero POSITIVE - generalises. It is
  what D4's validation tiers happened to produce.

### C.3 Eligibility is a statement about the sample, not a fourth decision

| ticker | blocker |
|---|---|
| BSY | D5 `VALUATION_NOT_READY` - no multiple resolves (`MARKET_CAP_UNAVAILABLE`) |
| GOOG | D5 `VALUATION_NOT_READY` - no multiple resolves (`MULTI_CLASS_UNRESOLVED`) |
| CRK | D5 `VALUATION_NOT_READY` - P/B is the only computable method and it is asserted NOT_SUITABLE |
| FRPT | D4 `D4_REFUSED_NO_FINAL_OUTPUT` **and** D5 `VALUATION_NOT_READY` |
| SPSC | D4 `D4_REFUSED_NO_FINAL_OUTPUT` |

`NOT_DECISION_ELIGIBLE` is never counted among the decisions and is never reported as a WATCH. An issuer
missing a layer is one the engine could not reach, which is a different fact from an issuer whose thesis
is incomplete, and collapsing the two would make D6's coverage look better than it is.

---

## D. Decision Contract

### D.1 D0 §N, read rather than restated

Three outcomes, no numeric composite. D0 §N1 lists the minimum evidence APPROVE requires and **all**
must hold; §N3 lists triggers **any one** of which is sufficient for REJECT; §N2 lists WATCH's
representative triggers. The engine quotes each contract line next to the clause that implements it, so
every clause in every output names the line it came from.

### D.2 Resolution order, and why it is a reading of D0 rather than a preference

```
1. evaluate eligibility   -> if a layer is missing, no decision is produced at all
2. evaluate §N3 triggers  -> any one fired  => REJECT
3. evaluate §N1 clauses   -> all hold       => APPROVE
4. otherwise                                 => WATCH
```

D0 does not state a precedence between §N1 and §N3, and REJECT is checked first because the two lists
are not independent: "valuation already reflects the full positive case" is the negation of "supports
upside under at least the Base case", and an adverse gap is the negation of §L's precondition. An
issuer that fires a §N3 trigger could not satisfy §N1's conjunction anyway, so the order is a statement
about reporting rather than about outcomes - and a test asserts the decision is a pure function of the
two clause lists over every gap state and three valuation shapes.

### D.3 §18's audit: WATCH is the abstention, and no fourth state is needed

The brief asked whether D0's three-state contract forces a decision on an issuer the evidence cannot
support, and whether a `DECISION_NOT_READY` is needed at the contract level. The audit comes out in
D0's favour. Every §N2 trigger is a form of "this thesis is not complete yet":

```
company attractive but current price leaves insufficient margin of safety
catalyst plausible but not yet dated/confirmed
expectation_gap.state positive but confidence is LOW or UNKNOWN
thesis needs confirmation from the next earnings print or filing
future business evidence exists but is presently STORY_UNVERIFIED only
```

So WATCH already *is* the abstention, and a `DECISION_NOT_READY` would duplicate it while implying the
engine failed rather than that the evidence is incomplete. What WATCH must never be is a stand-in for
evidence that does not exist, and that is what `Eligibility` separates out. No state was added.

### D.4 What a valuation may and may not do to a gap

D5-D0 §M, carried unchanged: a cheap valuation does not promote a NEGATIVE gap, an expensive one does
not demote a POSITIVE gap, and a disagreement is a finding for D6 rather than something D5 resolves. In
this engine that is structural rather than promised - the expectation clause and the two valuation
clauses are separate conjuncts of one AND, so neither can compensate for the other. Two tests hold the
line in both directions: a `WIDE_NEGATIVE` gap rejects an issuer with +300% to TP1, and a company
satisfying every §N1 company clause cannot APPROVE on a negative Base-case upside.

### D.5 What D6 may not produce, carried forward from D5-D2 §Q

```
an entry level, an entry zone or an entry price
an exit level, a stop, a trailing stop or a take-profit level
a position size, a share count, a notional or a portfolio weight
an order of any kind, paper or live
a ranking of the eligible issuers against each other
a numeric composite that determines APPROVE, WATCH or REJECT
a target price, a fair value or a multiple of its own - every number comes from D5
a forward return, a realized return or any outcome label
```

Asserted by running rather than by reading: `BANNED_D6_FIELD_NAME_TOKENS` is checked against every field
of every dataclass in the module and against every key the emitted record produces, recursively.

---

## E. APPROVE

### E.1 The eight conjuncts

| clause | contract line | how D6 reads it from the stored artifact |
|---|---|---|
| `business_understood` | D0 §N1: business understood (§F/§I answered, not UNKNOWN) | D3 valid and `business_model.revenue_drivers` populated |
| `fundamental_change_anchored` | D0 §N1: fundamental change evidence exists and is anchored to the bundle (§I) | ≥1 `fundamental_change` entry and **every** entry carries a `source_id` |
| `future_business_or_catalyst_meaningful` | D0 §N1: not STORY_UNVERIFIED alone (§J/§K) | ≥1 future-business entry carrying ≥1 of §J's six evidence kinds, **or** ≥1 catalyst at HIGH/MEDIUM materiality **and** HIGH/MEDIUM timing |
| `expectation_gap_permits_approve` | D0 §N1 / §L | gap ∈ {POSITIVE, WIDE_POSITIVE} **and** confidence ∈ {HIGH, MEDIUM} |
| `valuation_supports_base_case_upside` | D0 §N1: upside under at least the Base case | D5's `upside_to_TP1 > 0`, where TP1 **is** the Base fair value per D5-D0 §L |
| `valuation_confidence_sufficient` | D6's own clause, derived from D5-D0 §N | D5 `ValuationConfidence` ∈ {HIGH, MEDIUM} |
| `risks_with_an_invalidation_condition` | D0 §N1 | ≥1 risk **and** ≥1 invalidation candidate |
| `no_approve_blocking_unknown_field` | D0 §N1 | no `unknown_fields` entry naming business model, fundamental_change or expectation_gap |

### E.2 §J's six evidence kinds map onto D3's own booleans

D0 §J froze what makes a future-business claim REAL BUSINESS: reported revenue contribution, disclosed
order intake or backlog, a named customer with disclosed terms, a capacity build with a date or status,
disclosed margin contribution, or an executed contract. D3's v2 schema carries five of those as
per-entry booleans - `current_revenue_evidence`, `order_backlog_evidence`, `customer_evidence`,
`capacity_evidence`, `margin_evidence` - so the clause is "at least one entry carries at least one of
them". That is §J's test applied to the field D3 already published, not a re-reading of the filings.

The sixth kind, "an executed commercial contract (not a letter of intent alone)", has **no** boolean in
D3's schema and is therefore unreachable by this clause. It costs nothing on this sample - the four
issuers whose clause turns on future business all satisfy it through revenue or capacity evidence - but
it means the clause is slightly stricter than §J, and a candidate whose only evidence were an executed
contract would be read as story-only. Recorded rather than patched by inferring one from prose.

### E.3 The one clause D0 did not freeze

`valuation_confidence_sufficient` has no D0 §N1 counterpart, because D0 deferred valuation arithmetic to
D5 entirely. It is derived from D5-D0 §N's own sentence - "one method is a number and two agreeing or
disagreeing methods are a valuation" - which is precisely what `valuation_confidence` demotes to LOW
for, and from the brief's §6 prohibition on using a LOW valuation at MEDIUM's strength. It is reported as
D6's clause rather than as D0's, it was declared before any result existed, and it is **untested on this
sample**: the gap clause fails on all eight eligible issuers, so this clause is never the binding one.
That is recorded in §K.4 rather than presented as validation.

### E.4 APPROVE was unreachable before a single valuation was computed

| gap state | issuers | APPROVE reachable under D0 §L |
|---|---|---|
| `POSITIVE` / `WIDE_POSITIVE` | none | - |
| `NEUTRAL` at LOW confidence | SCCO CRK IDCC TG AEYE COLL | NO |
| `UNKNOWN` at UNKNOWN confidence | BSY GOOG DORM FG VRRM | NO |
| no output | FRPT SPSC | not eligible |

Every one of the eleven evaluated issuers published `d6_approve_precondition = BLOCKED` and
`confidence_ceiling = MEDIUM`. D4 computed that field itself, in code, before D6 existed. D6 reads it
**and** recomputes §L's rule from the state and the confidence, and reports a disagreement as a defect
rather than choosing a winner:

```
D4 precondition disagreements   0 / 11
```

Two implementations of one frozen rule agreeing on all eleven is worth more than either alone.

---

## F. WATCH

Six of eight, and the binding clause is the same one on all six.

| ticker | approve blockers | §N2 triggers matched |
|---|---|---|
| SCCO | gap; valuation confidence | thesis needs confirmation; valuation confidence LOW |
| DORM | gap | catalyst not dated; thesis needs confirmation |
| TG | gap; valuation confidence; unknown field | catalyst not dated; thesis needs confirmation; valuation confidence LOW |
| COLL | gap; valuation confidence | thesis needs confirmation; valuation confidence LOW |
| FG | gap; valuation confidence | catalyst not dated; thesis needs confirmation; valuation confidence LOW |
| VRRM | gap; valuation confidence | catalyst not dated; thesis needs confirmation; valuation confidence LOW |

Two things about this table are worth stating plainly.

**The §N2 triggers explain a WATCH and never cause one.** An issuer can be a WATCH with no §N2 trigger
matched at all - the state is where you land by being neither APPROVE nor REJECT. The clearest case is
the undated catalyst: DORM, TG, FG and VRRM all match "catalyst plausible but not yet dated/confirmed",
and on an issuer whose future-business evidence satisfies the other half of §N1's OR, that match does
**not** demote anything. A test asserts exactly that, because reading the matched trigger as a demotion
would quietly turn §N1's disjunction into a conjunction.

**TG's third blocker is an over-match and is left in.** `no_approve_blocking_unknown_field` fires on TG
because its D3 `unknown_fields` contains `fundamental_changes.free_cash_flow` and
`fundamental_changes.operating_margin`, and the matcher is a substring test against the token
`fundamental_change`. Those are sub-fields of fundamental_change rather than the field itself, so the
match is conservative - arguably too conservative. It is reported rather than fixed, because tightening a
matcher after seeing which issuer it hit is fitting the contract to the sample. It changes nothing: TG's
gap clause already blocks APPROVE. The clause is the right one to revisit before D7 and §K.5 records it.

---

## G. REJECT

Two of eight, from two different triggers, and neither is a score crossing a threshold.

### G.1 IDCC - the full positive case is already priced

```
current price        328.72
primary method       EV/EBIT, FULL_2Y, n=491
current multiple     23.9253x   -> the 94.7th percentile of its own two-year panel
Bear  (P10)          11.9803x  observed 2025-06-13   ->  168.98   -48.6%
TP1   (P50)          15.3999x  observed 2025-09-16   ->  214.71   -34.7%
TP2   (P90)          19.8560x  observed 2026-02-27   ->  274.30   -16.6%
secondary EV/Sales   Base 282.72   ratio 1.317   CORROBORATES
trigger fired        valuation_fully_prices_the_positive_case  (D0 §N3)
```

Every leg of the range sits below the price and the Bull leg is 16.6% below it, which is D0 §N3's
"valuation already reflects the full positive case" in its literal form. The trigger is on TP2 and not on
TP1 deliberately: the Bull leg **is** the full positive case, and an issuer whose Base case is below price
but whose Bull case is above it has insufficient margin of safety, which D0 §N2 makes a WATCH. ADBE in
D5-D2 was exactly that shape and a test holds the distinction.

The secondary corroborates at 1.317x, so this is not one method's opinion. What the range cannot say is
whether 23.9x is *wrong*: D3 records a full-year outlook explicitly assuming new agreements not yet
signed, plus the Amazon and Lenovo arbitrations, and D4 read NEUTRAL at LOW confidence. The honest
reading is that the price already pays for a re-rating this repository cannot evaluate.

### G.2 AEYE - future business is STORY with no §J evidence and no dated catalyst

```
future_business      1 entry, stage STORY
                     current_revenue / backlog / customer / capacity / margin evidence  all false
catalysts            EARNINGS_RELEASE         materiality HIGH   timing LOW     undated
                     CAPITAL_RETURN           materiality MEDIUM timing LOW     undated
                     GOVERNANCE_EQUITY_EVENT  materiality LOW    timing HIGH    2027-01-09
trigger fired        future_business_story_only_without_corroborating_catalyst  (D0 §N3)
```

D3's own words on the single future-business entry: the incoming CFO referred to "AI initiatives
underway", and "the only AI effects quantified in the filings are internal cost efficiencies". D0 §J
calls that `STORY_UNVERIFIED`, and §N3 rejects story-only future business **with no corroborating
catalyst**. None of the three candidates is both material and dated: the two material ones are undated at
LOW timing confidence, and the one that is dated - a governance/equity event on 2027-01-09, which is in
any case past the thesis horizon - is LOW materiality. So nothing corroborates.

The valuation is incidental to this REJECT and is reported anyway: TP1 7.07 against a price of 7.21 is
-1.9%, TP2 8.80 is +22.1%, so AEYE is **not** fully priced. Two independent §N3 triggers would have been
one too many; only one fired.

---

## H. Company Results

### H.1 The D5-D1 layer on D4's thirteen

Decision session 2026-09-16, every figure code-owned, no scenario or target in this table. This is what
the judgement table was written against.

| ticker | price | market cap | net debt | TTM revenue | op income | FCF | book equity | computable methods |
|---|---|---|---|---|---|---|---|---|
| AEYE | 7.21 | 90.6m | 7.7m | 42.0m | -3.6m | 5.8m | 3.2m | P/B 28.12 · P/FCF 15.60 · EV/Sales 2.342 · EV/EBITDA 371.1 · EV/FCF 16.92 |
| BSY | 31.10 | **-** | 1,070.2m | - | - | - | 1,202.0m | *none* - `MARKET_CAP_UNAVAILABLE` |
| COLL | 22.19 | 722.4m | **-** | 808.2m | 157.4m | 328.3m | 311.9m | P/B 2.316 · P/FCF 2.201 · P/E 17.90 |
| CRK | 13.10 | 3,846.4m | **-** | 2,177.8m | 627.7m | -735.2m | 2,579.1m | P/B 1.491 |
| DORM | 124.96 | 3,708.3m | 308.5m | 2,155.0m | 312.0m | 214.2m | 1,515.0m | P/B 2.448 · P/FCF 17.32 · EV/Sales 1.864 · EV/EBIT 12.87 · EV/FCF 18.75 |
| FG | 23.05 | 3,018.1m | 136.0m | 6,067.0m | **-** | **-** | 4,609.0m | P/B 0.655 · EV/Sales 0.520 |
| FRPT | 62.03 | 2,981.0m | **-** | 1,177.3m | 95.4m | **-** | 1,237.7m | P/B 2.409 |
| GOOG | 339.36 | **-** | **-** | - | - | - | 640,480m | *none* - `MULTI_CLASS_UNRESOLVED` |
| IDCC | 328.72 | 8,483.0m | -226.5m | 788.5m | 345.1m | 554.6m | 1,202.2m | P/B 7.056 · P/FCF 15.30 · EV/Sales 10.47 · EV/EBIT 23.93 · EV/EBITDA 19.49 · EV/FCF 14.89 |
| SCCO | 189.88 | 158,422.9m | **-** | 15,787.5m | 8,982.8m | 5,100.4m | 12,632.2m | P/B 12.54 · P/FCF 31.06 |
| SPSC | 80.68 | 2,904.5m | **-** | 772.5m | 98.8m | 198.7m | 938.5m | P/B 3.095 · P/FCF 14.62 |
| TG | 6.89 | 240.8m | 28.8m | **-** | **-** | 22.6m | 228.6m | P/B 1.053 · P/FCF 10.64 · EV/FCF 11.91 |
| VRRM | 3.54 | 538.0m | 985.1m | 1,007.0m | 136.9m | 96.9m | 223.6m | P/B 2.406 · P/FCF 5.552 · EV/Sales 1.513 · EV/EBIT 11.13 · EV/EBITDA 6.043 · EV/FCF 15.72 |

D5-D1's central finding reproduces on a disjoint sample: **the binding constraint is the numerator, not
the fundamentals.** Both zero-method issuers fail on the market cap, and seven of the thirteen have no
enterprise value because no borrowing balance resolves at the cash date - including SCCO, which plainly
carries debt.

### H.2 The decision table

| ticker | D3 summary | D4 gap | D4 conf | price | TP1 | up TP1 | TP2 | up TP2 | val conf | decision | key reason |
|---|---|---|---|---|---|---|---|---|---|---|---|
| SCCO | copper miner, growth MIXED, price-driven | NEUTRAL | LOW | 189.88 | 190.26 | **+0.2%** | 260.17 | +37.0% | LOW | **WATCH** | gap not positive; single method |
| DORM | auto aftermarket, growth MIXED, tariff recovery one-off | UNKNOWN | UNKNOWN | 124.96 | 128.85 | **+3.1%** | 143.41 | +14.8% | MEDIUM | **WATCH** | gap UNKNOWN blocks §L |
| IDCC | patent licensing, growth MIXED, lumpy | NEUTRAL | LOW | 328.72 | 214.71 | **-34.7%** | 274.30 | **-16.6%** | MEDIUM | **REJECT** | whole range below price |
| TG | aluminium + films, growth MIXED, FIFO tailwind ending | NEUTRAL | LOW | 6.89 | 8.78 | **+27.5%** | 13.40 | +94.6% | LOW | **WATCH** | gap not positive; strong drift |
| AEYE | accessibility SaaS, growth POSSIBLY_DURABLE, ARR +11% | NEUTRAL | LOW | 7.21 | 7.07 | **-1.9%** | 8.80 | +22.1% | MEDIUM | **REJECT** | future business STORY only |
| COLL | specialty pharma, growth MIXED, acquired products | NEUTRAL | LOW | 22.19 | 24.66 | **+11.1%** | 41.50 | +87.0% | LOW | **WATCH** | gap not positive; method conflict 1.97x |
| FG | annuity / life insurer, growth MIXED | UNKNOWN | UNKNOWN | 23.05 | 27.42 | **+19.0%** | 29.81 | +29.3% | LOW | **WATCH** | gap UNKNOWN; single method |
| VRRM | tolling + enforcement, growth MIXED, worse contract terms | UNKNOWN | UNKNOWN | 3.54 | 17.87 | **+404.7%** | 24.56 | +593.7% | LOW | **WATCH** | gap UNKNOWN; conflict 3.60x - **see §K.1** |
| SPSC | supply-chain SaaS, growth MIXED, +6% recurring | *no output* | - | 80.68 | 74.07 | -8.2% | 81.39 | +0.9% | LOW | *not eligible* | D4 refused |
| BSY | infrastructure software, growth DURABLE, ARR +12% | UNKNOWN | UNKNOWN | 31.10 | - | - | - | - | NOT_READY | *not eligible* | no market cap |
| GOOG | advertising + cloud, growth MIXED, Cloud +82% | UNKNOWN | UNKNOWN | 339.36 | - | - | - | - | NOT_READY | *not eligible* | share class unresolved |
| CRK | gas E&P, growth MIXED, FCF negative | NEUTRAL | LOW | 13.10 | - | - | - | - | NOT_READY | *not eligible* | P/B only, NOT_SUITABLE |
| FRPT | fresh pet food, growth POSSIBLY_DURABLE, +10-12% guided | *no output* | - | 62.03 | - | - | - | - | NOT_READY | *not eligible* | D4 refused; P/B only |

BSY is the sharpest single row in the table. D3 reads growth durability `DURABLE` - the only one of the
thirteen - with ARR of 1,536.0m growing 12% in constant currency, and the pipeline cannot reach a
decision about it because the share count does not resolve. The layer that stopped it is the oldest and
least interesting one.

### H.3 Decision detail, per eligible issuer

Facts and numbers below come only from the stored D3, D4 and D5 artifacts.

---

**SCCO - WATCH**

- *Company evidence.* Copper mining in Peru and Mexico, ~75.9% of revenue from copper, 10.9% molybdenum,
  5.7% silver (D3, 10-K). Growth durability `MIXED`: the company attributes 2Q26 sales growth primarily
  to higher metal prices while copper volumes fell, and states it cannot predict whether prices rise or
  fall. Future business: Tia Maria at 42% completion, 693m of 1,101m committed and invested, stage
  `EARLY_EVIDENCE` with capacity evidence but no production, revenue or offtake. 14 risks, 4 invalidation
  candidates.
- *Expectation evidence.* `NEUTRAL` at `LOW` confidence. `d6_approve_precondition = BLOCKED`.
- *Valuation evidence.* P/FCF 31.06x, FULL_2Y (n=471, trend `TRENDING_MODERATE`, rho +0.467), current at
  the 49.7th percentile. Bear 21.91x (2025-05-12) -> 133.96. TP1 31.12x (2024-12-04) -> 190.26. TP2 42.56x
  (2024-10-01) -> 260.17. No enterprise value, so no second method; confidence `LOW` on single method.
- *Why now.* The one catalyst that is both material and timed is the US-China reciprocal tariff
  suspension expiring **2026-11-10** at MEDIUM materiality and MEDIUM timing confidence - which is a
  macro event the company does not control, not a company why-now. It is why SCCO does not match §N2's
  undated-catalyst trigger.
- *TP1 190.26 (+0.2%) · TP2 260.17 (+37.0%) · Bear 133.96 (-29.4%).*
- *Risks.* The denominator is a commodity price in disguise: 5,100.4m of free cash flow is what this
  asset base produces at the trailing copper price. The RECENT_6M window reads `TRENDING_STRONG` at
  rho -0.725, so the most recent half-year is a sharp de-rating the full panel averages away.
- *Invalidation.* D3 names four candidates; a sustained copper price break is the operative one and D5
  cannot price it.
- *Limitations.* Single method, no peer context, no enterprise value on an issuer that carries debt.

---

**DORM - WATCH**

- *Company evidence.* Leading supplier of replacement and upgrade parts to the motor vehicle aftermarket,
  ~144,000 distinct parts, 5,560 introduced in 2025 including 1,608 new-to-the-aftermarket (D3, 10-K).
  Future business `REAL_BUSINESS` at MEDIUM strength with both revenue and margin evidence, from a new
  product programme rather than a new line of business. Growth durability `MIXED`, and D3
  is explicit about why: Q2 2026 reflected recovery of IEEPA tariff costs recognised in earlier periods,
  which D3 reads as "more likely a one-off than a durable run-rate".
- *Expectation evidence.* `UNKNOWN` at `UNKNOWN` confidence - a measured absence, not a missing run.
- *Valuation evidence.* EV/EBIT 12.87x, FULL_2Y (n=139, `RANGE_BOUND`, rho -0.269), 30.9th percentile.
  Bear 11.84x -> 114.10, TP1 13.24x -> 128.85, TP2 14.63x -> 143.41. Secondary EV/FCF Base 150.82, ratio
  1.170, `CORROBORATES`. **The only MEDIUM-confidence WATCH whose single blocker is the gap.**
- *Why now.* No dated catalyst. Q3 2026 is the first report after the updated guidance.
- *TP1 128.85 (+3.1%) · TP2 143.41 (+14.8%) · Bear 114.10 (-8.7%).*
- *Risks.* TTM operating income contains the prior-period tariff recovery, so a target multiple applied
  to it prices the recovery as recurring. EV/EBITDA cannot be formed - D&A refuses on a tag mismatch.
- *Invalidation.* 4 candidates; new tariff measures after 2026-08-03 are excluded from guidance.
- *Limitations.* Panel of 139 sessions on the primary method, the shortest in the sample.

---

**IDCC - REJECT**

Detail in §G.1. Company evidence: patent licensing, 93% of 2025 revenue from fixed-fee agreements, ARR
625.7m up 13%, ~1.5bn of contracted future payments; future business `COMMERCIALIZING` at MEDIUM with
revenue and customer evidence (the Amazon agreement, final terms to binding arbitration). 10 risks, 4
invalidation candidates; the one material-and-timed catalyst is `NEW_LICENSE_AGREEMENTS` at HIGH
materiality and MEDIUM timing confidence, undated, which is why IDCC does not match §N2's
undated-catalyst trigger. Expectation: `NEUTRAL` at `LOW`. Limitation: revenue is lumpy by construction -
a trailing twelve months either contains a catch-up settlement or does not.

---

**TG - WATCH**

- *Company evidence.* Custom aluminium extrusions for building, construction and automotive, plus
  surface-protection films for electronics (D3). Future business `REAL_BUSINESS` at MEDIUM with revenue
  and backlog evidence: TSLOTS structural framing was ~11% of Bonnell's Q2 2026 volume. Growth
  durability `MIXED`, and D3 quotes management expecting the FIFO inventory and metal-price benefit "to
  be substantially neutralized during the third quarter". 9 risks, 6 invalidation candidates.
- *Expectation evidence.* `NEUTRAL` at `LOW` confidence.
- *Valuation evidence.* EV/FCF 11.91x, RECENT_6M (n=126) - and this is the one issuer where the window
  rule fired on its own terms: FULL_2Y is `TRENDING_STRONG` at rho -0.749, so the shortest eligible
  window governs. Full-panel Base FV would have been 13.02 against 8.78. Current multiple is at the
  **2.4th percentile** of the chosen window, so Bear 13.25x -> 7.76 is **above** the price (§K.3).
  Secondary P/B Base 7.98, ratio 1.101, `CORROBORATES`. Confidence `LOW` on `TRENDING_STRONG`.
- *Why now.* New CFO effective 2026-09-21 is the only dated item, at LOW materiality.
- *TP1 8.78 (+27.5%) · TP2 13.40 (+94.6%) · Bear 7.76 (**+12.6%**).*
- *Risks.* Revenue and operating income do not resolve at all, so no margin regime is observable - the
  structural limitation D5-D2 recorded for COHR. The trailing free cash flow carries a tailwind the
  issuer has said is ending.
- *Invalidation.* 6 candidates.
- *Limitations.* Every leg of the range is above the price, so the range contains no downside scenario
  at all. "BEAR_ANCHOR" is the wrong name for 7.76 and the number is not adjusted.

---

**AEYE - REJECT**

Detail in §G.2. Company evidence: subscription web-accessibility testing and remediation, ARR +11% year
over year, growth durability `POSSIBLY_DURABLE` - the strongest growth reading among the eligible eight.
7 risks, 5 invalidation candidates. Expectation `NEUTRAL` at `LOW`. The window rule did its largest piece
of work here: FULL_2Y is `TRENDING_STRONG` at rho -0.915 and its Base FV would have been 24.87 against a
price of 7.21 - **+244.9%** instead of the published **-1.9%** - so the rule removed 247 percentage points
of manufactured upside before the decision engine saw anything.

---

**COLL - WATCH**

- *Company evidence.* Six marketed products including the acquired Azstarys, product revenue 199.9m in
  Q2 2026 against 188.0m a year earlier (D3). Future business `REAL_BUSINESS` at MEDIUM with revenue and
  capacity evidence: Azstarys generated 12.9m from 2026-05-12 to 2026-06-30 and full-year guidance was
  raised to 65-75m. Growth durability `MIXED`; Jornay PM prescriptions +13.1%, written by over 30,000
  providers, +17.6%. 12 risks, 4 invalidation candidates.
- *Expectation evidence.* `NEUTRAL` at `LOW` confidence.
- *Valuation evidence.* P/E 17.90x, FULL_2Y (n=473, `TRENDING_MODERATE`, rho +0.403), 41.9th percentile.
  Bear 13.41x -> 16.63, TP1 19.89x -> 24.66, TP2 33.47x -> 41.50. Secondary P/FCF Base **48.54**, ratio
  **1.968** -> `VALUATION_CONFLICT`, confidence `LOW`.
- *Why now.* The one material-and-timed catalyst is Nucynta IR pediatric exclusivity expiring
  **2027-01-03** at MEDIUM materiality and HIGH timing confidence - an adverse dated event just past the
  thesis horizon, which is why COLL does not match §N2's undated-catalyst trigger and is not a why-now in
  any useful sense. Q3 2026 is the first full Azstarys quarter and is undated in the evidence.
- *TP1 24.66 (+11.1%) · TP2 41.50 (+87.0%) · Bear 16.63 (-25.0%).*
- *Risks.* The conflict is the finding and it was predicted in the declared judgement: free cash flow of
  328.3m against net income of 47.9m is largely amortisation of acquired product rights, and for an
  issuer that grows by buying products that amortisation is the cost of replenishing the portfolio
  rather than a non-cash add-back. P/E says 24.66 and P/FCF says 48.54; the contract reports both and
  averages neither. Nucynta IR pediatric exclusivity expires 2027-01-03, just past the thesis horizon.
- *Invalidation.* 4 candidates. No total debt resolves - D3's newest debt fact is from 2019.
- *Limitations.* Both methods share the equity numerator, so the cross-check is weaker than two
  denominators would be; it still disagreed by 1.97x.

---

**FG - WATCH**

- *Company evidence.* Spread-based fixed and indexed annuities plus pension risk transfer, with fee
  income from a Blackstone-backed reinsurance sidecar; AUM before reinsurance a record 74.7bn, retained
  AUM 55.9bn (D3). Four future-business entries: the Blackstone-backed Fort Greene reinsurance sidecar
  `COMMERCIALIZING` at MEDIUM with customer and capacity evidence, two `REAL_BUSINESS` entries (one at
  HIGH strength) carrying revenue, customer and margin evidence, and one `UNKNOWN`. Growth durability
  `MIXED`. 11 risks, 6 invalidation candidates.
- *Expectation evidence.* `UNKNOWN` at `UNKNOWN` confidence.
- *Valuation evidence.* P/B 0.655x, RECENT_6M (n=126, `RANGE_BOUND`, rho -0.006) - the window rule fired
  here too, FULL_2Y being `TRENDING_STRONG` at rho -0.927 with a Base FV of 33.44 against 27.42. Current
  at the 4.0th percentile, so Bear 0.6693x -> 23.56 is **above** the 23.05 price (§K.3). Single method;
  EV/Sales is asserted NOT_SUITABLE. Confidence `LOW`.
- *Why now.* Nothing dated. D3's regulatory catalyst (DOL fiduciary rule litigation) is UNKNOWN timing.
- *TP1 27.42 (+19.0%) · TP2 29.81 (+29.3%) · Bear 23.56 (**+2.2%**).*
- *Risks.* A 0.655x multiple is the market's discount to stated book and the discount may be right. A
  fair value from percentiles of that same discount asserts only that the market has paid those
  multiples of this book before, not that the book is correct. An insurer's GAAP book moves with AOCI, so
  a P/B percentile across a period of rate moves is partly a rate series. D3 lists the 2026 statutory RBC
  ratio and the holding-company cash position among its unknown fields; neither is in the multiple.
- *Invalidation.* 6 candidates.
- *Limitations.* Single method, no cross-check of any kind, operating income and free cash flow do not
  resolve at all for this issuer.

---

**VRRM - WATCH, and the row to read §K.1 before using**

- *Company evidence.* Tolling for rental fleets, automated photo enforcement, title and registration;
  toll management ~39% of 2025 revenue. Future business `REAL_BUSINESS` at **HIGH** strength - the
  strongest §J reading in the sample - with revenue, customer, capacity and margin evidence: a new
  five-year NYCDOT contract effective 2026-01-01, New York City revenue up 12.0m in Q2 2026 from new
  camera installations. Growth durability `MIXED`, and D3 records the deterioration directly: two
  significant Commercial Services customers signed seven- and five-year extensions "on terms materially
  less favorable to the company, including fleet volume modulation rights", with a third renewal
  outstanding. 14 risks, 5 invalidation candidates.
- *Expectation evidence.* `UNKNOWN` at `UNKNOWN` confidence.
- *Valuation evidence.* EV/EBIT 11.13x, FULL_2Y (n=474, `TRENDING_MODERATE`, rho **-0.522**), 10.8th
  percentile. Bear 7.99x -> 0.72, TP1 **27.04x** -> 17.87, TP2 34.47x -> 24.56. Secondary EV/Sales Base
  **4.97**, ratio **3.597** -> `VALUATION_CONFLICT`, confidence `LOW`.
- *Why now.* No dated catalyst; a permanent CEO appointment and a third contract renewal are both
  undated.
- *TP1 17.87 (+404.7%) · TP2 24.56 (+593.7%) · Bear 0.72 (-79.8%).*
- *Risks.* The price fell from 26.99 to 3.54 across the panel and 3.54 **is** the panel minimum, so every
  percentile of every observed multiple lies above the current one and upside is produced mechanically.
  Net debt is 64.7% of enterprise value, so the equity is a geared claim: a modest enterprise re-rating
  moves the equity by ~2.8x as much, which is why the Bear anchor is 0.72 - 80% below the price - on the
  same arithmetic that produces +404.7% to TP1. Both numbers are correct and neither is a forecast.
- *Invalidation.* 5 candidates; the third Commercial Services renewal on Avis/Hertz-like terms is the
  operative one and the valuation prices the old terms.
- *Limitations.* §K.1. The secondary method disagrees by 3.6x and the contract demoted confidence to LOW
  for exactly that reason, so the system's own cross-check caught what its window rule did not.

---

## I. Valuation Integration

### I.1 What the window rule did, issuer by issuer

The rule, applied identically to all thirteen with no per-issuer discretion: if the primary method's
FULL_2Y trend is `TRENDING_STRONG`, take the shortest contract-eligible window; otherwise take FULL_2Y.

| ticker | primary | FULL_2Y trend / rho | window taken | Base FV: FULL_2Y -> chosen | price | upside the rule removed |
|---|---|---|---|---|---|---|
| AEYE | P/FCF | TRENDING_STRONG -0.915 | RECENT_6M | 24.87 -> **7.07** | 7.21 | **-247pp** |
| SPSC | P/FCF | TRENDING_STRONG -0.945 | RECENT_6M | 159.23 -> **74.07** | 80.68 | **-106pp** |
| FG | P/B | TRENDING_STRONG -0.927 | RECENT_6M | 33.44 -> **27.42** | 23.05 | -26pp |
| TG | EV/FCF | TRENDING_STRONG -0.749 | RECENT_6M | 13.02 -> **8.78** | 6.89 | -62pp |
| SCCO | P/FCF | TRENDING_MODERATE +0.467 | FULL_2Y | 190.26 | 189.88 | - |
| DORM | EV/EBIT | RANGE_BOUND -0.269 | FULL_2Y | 128.85 | 124.96 | - |
| IDCC | EV/EBIT | TRENDING_MODERATE +0.620 | FULL_2Y | 214.71 | 328.72 | - |
| COLL | P/E | TRENDING_MODERATE +0.403 | FULL_2Y | 24.66 | 22.19 | - |
| VRRM | EV/EBIT | TRENDING_MODERATE **-0.522** | FULL_2Y | **17.87** | 3.54 | **0 - and §K.1** |

On four of nine the rule removed between 26 and 247 percentage points of upside that was an artifact of a
dead regime. On one it removed nothing and should have.

### I.2 Reconciliation did the work the window rule missed

| ticker | primary Base | secondary | secondary Base | ratio | status |
|---|---|---|---|---|---|
| AEYE | 7.07 | EV/Sales | 7.14 | 1.010 | CORROBORATES |
| DORM | 128.85 | EV/FCF | 150.82 | 1.170 | CORROBORATES |
| IDCC | 214.71 | EV/Sales | 282.72 | 1.317 | CORROBORATES |
| TG | 8.78 | P/B | 7.98 | 1.101 | CORROBORATES |
| COLL | 24.66 | P/FCF | 48.54 | **1.968** | **VALUATION_CONFLICT** |
| VRRM | 17.87 | EV/Sales | 4.97 | **3.597** | **VALUATION_CONFLICT** |
| SCCO, SPSC, FG | - | *none* | - | - | NO_SECONDARY |

Both conflicts were predicted by the declared judgement before the numbers existed - COLL's as the
amortisation gap between P/E and P/FCF, VRRM's as the leverage artifact - and both cost the issuer a
demotion to `LOW`, which blocks APPROVE under clause F. **The 1.5x conflict ratio is the only thing in
this pipeline that caught VRRM.** It was pre-registered in D5-D2 and never tested; this is the first time
it has fired on an issuer it mattered for, and that is a property of the sample rather than evidence that
1.5x is the right line.

### I.3 Abstentions, argued per issuer

- **BSY / GOOG** - zero computable methods. Not an economic judgement: the market-cap numerator refuses
  before any economic question is reached. BSY's shares do not resolve (D3 lists `shares_outstanding`
  among its own unknown fields); GOOG has three share classes and resolution returns `UNRESOLVED` at LOW
  confidence (D3 lists `shares_outstanding_code_owned`). Both layers agree about what is missing.
- **CRK** - P/B 1.491x is the only computable multiple and it is asserted `NOT_SUITABLE`. An E&P's book
  equity is historical property cost less depletion and impairments; what the equity is worth is the
  reserve base at forward gas prices, and a price-driven impairment lowers book value while the gas in
  the ground is unchanged. D3's own evidence sharpens it: a letter of intent under which SOCAR would pay
  **1.65bn in cash** for non-operated working interests plus part of a Pinnacle interest, against total
  book equity of 2.58bn. A transaction pricing a partial interest at that level is direct evidence that
  book value is not the measure of this asset base.
- **FRPT** - P/B 2.409x only, asserted `NOT_SUITABLE` on a different argument. The fridges and plants are
  real earning assets, so this is not D5-D0 §N's asset-light case. What the market pays 2.41x book for is
  growth - FY2026 net sales guidance raised to 10-12%, Q2 volume +15.7% - so a percentile of this
  issuer's own P/B history is a percentile of how much growth the market was willing to capitalise. As
  the sole method with nothing to arbitrate it, it would publish a target whose entire content is a
  re-rating. D5-D2 allowed P/B as a *secondary* asset check for NATR for the same reason it is refused as
  a *primary* here: it can corroborate a floor and it cannot carry an anchor.

Eleven `NOT_SUITABLE` assertions were made across the thirteen and eight of them are P/B - AEYE, COLL,
CRK, FRPT, IDCC, SCCO, SPSC and VRRM - but they are not the same argument eight times: asset-light
accounting for AEYE, COLL, IDCC and SPSC; acquisition-accounting residue plus 65% leverage for VRRM; an
unpriced ore body for SCCO; an unpriced reserve base for CRK; and a growth multiple wearing an asset
multiple's name for FRPT. The other three are EV/EBITDA on AEYE (a +0.265m denominator that is the
residue of two numbers forty times its size), EV/Sales on FG (an insurer has no meaningful enterprise
value because the policyholder liabilities are the business, not financing for it), and P/FCF on VRRM.

P/B is accepted as the primary method on exactly one issuer, FG, and the reason is the GNW reason: for an
insurer, book equity **is** the earning asset.

### I.4 TP1 and TP2 are D5's, unchanged

D6 generates no target. TP1 is the Base fair value and TP2 is the Bull fair value, both as D5-D0 §L
defines them and as D5-D2 computed them. Hand-recomputed at 50-digit decimal precision over all eight
valued eligible issuers:

```
fair values recomputed by hand        24 of 24 match (Bear / Base / Bull on 8 issuers)
upsides recomputed by hand            24 of 24 match
identity invariant residuals          8 of 8 below 2.2e-14 absolute
manual arithmetic defects             0
```

The identity invariant is the one that catches the defect class where every operand is individually
plausible: at the **current** observed multiple the chain must return the current price exactly. Largest
residual 2.199e-14 on IDCC's four-operation enterprise bridge; smallest 8.0e-16 on COLL's single
multiplication.

---

## J. Expectation Integration

### J.1 D6 does not recompute a gap, and reads D4's own precondition

The six frozen states are used as D4 wrote them. The gap is never a number that adjusts a multiple, and
nothing in D6 scales a target by a gap. D4 publishes `d6_approve_precondition` itself, in code, and D6
both reads it and independently evaluates D0 §L from the state and the confidence:

```
issuers with an evaluated D4         11
d6_approve_precondition = BLOCKED    11
D0 §L recomputed = BLOCKED           11
disagreements                         0
```

A disagreement would have been reported as a defect rather than resolved by preferring one side.

### J.2 Three absence states, and none of them is a reading

| state | meaning | issuers | what D6 does |
|---|---|---|---|
| `D4_EVALUATED` | a final output exists | 11 | decides on it |
| `D4_REFUSED_NO_FINAL_OUTPUT` | D4 ran and its contract declined every attempt | FRPT, SPSC | ineligible, no decision |
| `D4_NOT_EVALUATED` | no D4 record exists | none here | ineligible, no decision |

FRPT and SPSC are what the distinction is for. Both were attempted three times in Tier B and both failed
schema validation on the same clause family:

```
FRPT   previous guidance needs both bounds or neither - a half-stated range is how a point
       guidance and a truncated range become indistinguishable
         @ market_expectation_evidence.guidance_assessments.2

SPSC   state=ABOVE_COMPANY_GUIDANCE is a comparison and requires the reported value and both
       prior-guidance bounds - a comparison with one side missing is an assertion
         @ market_expectation_evidence.result_vs_guidance.0 and .1
```

That is the contract holding, not leaking. But a refusal is an absence of evidence, and `UNKNOWN` is a
*measured finding* - D4 examined the issuer and found the expectation evidence absent or contradictory.
`NEUTRAL` is a stronger claim still: that the market's pricing is a reasonable reflection of the
trajectory. Mapping either absence onto either state would let a decision inherit a confidence nothing
supports. So the two refused issuers are outside the eligible sample and SPSC's valuation row is
published without a decision attached.

The contrast is asserted as a difference in outcome, not in wording: an `UNKNOWN` gap is decided on - it
blocks APPROVE under §L and lands on WATCH, as it does for DORM, FG and VRRM - and an absence is not
decided on at all.

### J.3 The gap distribution is the reason there is no APPROVE

```
WIDE_POSITIVE  0        NEUTRAL  6        NEGATIVE       0
POSITIVE       0        UNKNOWN  5        WIDE_NEGATIVE  0
                                          no output      2
```

Zero positive gaps means APPROVE was unreachable; zero adverse gaps means the §N3 gap trigger never
fired, so both REJECTs came from elsewhere. Eleven of eleven at `confidence_ceiling = MEDIUM` and none
at `HIGH`. This is a fact about thirteen issuers chosen by D4's validation tiers (§C.2) and is not a
measurement of how often an expectation gap is positive.

---

## K. Limitations

### K.1 The window rule tests the wrong window, and VRRM is the proof

This is the most important finding in the step and it is a defect in a D5-D2 rule that out-of-pilot
application exposed.

The rule asks whether **FULL_2Y** is `TRENDING_STRONG` and shortens the window if it is. The trend test
is a Spearman rank correlation against time with a 0.7 threshold, which measures **monotonicity**. A
panel that rises and then collapses is not monotone, so its full-panel rho is small even though the
recent panel is a violent de-rating. VRRM's panel is measured here rather than asserted:

```
VRRM  EV/EBIT, 474 observed sessions 2024-10-01 .. 2026-09-16

      first half   2024-10-01 .. 2025-09-25   n=237   rho +0.830   27.58x -> 34.13x
      second half  2025-09-26 .. 2026-09-16   n=237   rho -0.918   33.95x -> 11.13x
      peak 36.056x on 2025-07-01     trough 6.835x on 2026-05-27

      FULL_2Y     n=474   rho -0.522   TRENDING_MODERATE   -> window TAKEN
      RECENT_12M  n=252   rho -0.930   TRENDING_STRONG
      RECENT_6M   n=126   rho -0.554   TRENDING_MODERATE

      Base FV     FULL_2Y     17.87   (published, TP1, +404.7%)
                  RECENT_12M   5.86   (+65.5% had the rule shortened)
                  RECENT_6M    4.42   (+24.9%)
```

The two halves cancel: +0.830 then -0.918 average to -0.522, which is below the 0.7 threshold, so the
rule concludes the panel did not drift strongly and takes the median of a complete round trip. The P50 it
takes - 27.0379x, observed 2026-01-26 - sits inside the collapse, which is the `DEAD_REGIME_IS_THE_CENTRAL_TRAP`
case the rule exists to prevent.

One detail makes this worse rather than better: the de-rating is not only a price move. Operating income
rose over the panel - 136.0m at 2024-12-31 to 238.4m at 2025-12-31 - so EV/EBIT fell both because the
numerator collapsed and because the denominator grew. A reader who assumed a falling multiple means a
falling business would have the direction backwards, and neither D5 nor D6 makes that claim; what D5
cannot do is tell the two causes apart when choosing a target multiple.

The published TP1 is +404.7% and the counterfactual on the window that *is* `TRENDING_STRONG` is +65.5%.
The gate the rule exists to be - "a mechanical rule rejects the full panel when it drifts strongly" -
does not fire, because the rule only ever asks the full panel whether it drifted.

**The rule is not changed here.** Retuning a target-multiple rule after seeing which issuer it mishandled
is the fitting the brief's §3 forbids and is how a framework quietly becomes an upside generator. The
finding is recorded, the counterfactual is published, and the repair is a D7-or-later question stated as
a question: *should the window rule shorten when ANY longer window is `TRENDING_STRONG`, rather than only
when FULL_2Y is?* Nothing in this step tests whether that would be better.

Two things limited the damage and neither was the window rule. The reconciliation flagged
`VALUATION_CONFLICT` at 3.597x, and the resulting `LOW` confidence blocks APPROVE under clause F. And
the expectation gap was `UNKNOWN`, which blocks APPROVE under §L independently. So VRRM is a WATCH with a
published +404.7% TP1 next to a published -79.8% Bear anchor, and a reader who takes the first number
without the second has been misled by a correct output.

### K.2 A re-rating-only Bear is at its most dangerous on a geared capital structure

D5-D2's §N.2 limitation, inherited and worse here. The Bear anchor is a multiple-compression scenario and
prices no fundamental deterioration. On VRRM, net debt is 64.7% of enterprise value, so the equity is a
geared claim: compressing the enterprise multiple from 11.13x to 7.99x - a 28% enterprise move - takes
the equity from 3.54 to 0.72, an 80% move. The arithmetic is right and the label "Bear" invites a reader
to treat 0.72 as a floor. It is not a floor; it is what one observed multiple implies for a stub.

### K.3 On two issuers the Bear anchor is above the price, and "downside" is the wrong word

New in this sample and absent from D5-D2's seven.

```
TG   current EV/FCF 11.909x at the 2.4th percentile of RECENT_6M
     Bear P10 13.252x  ->  7.76   against a 6.89 price   downside_to_bear  = +12.6%

FG   current P/B 0.6548x at the 4.0th percentile of RECENT_6M
     Bear P10 0.6693x  ->  23.56  against a 23.05 price  downside_to_bear  = +2.2%
```

When the current multiple sits below the window's 10th percentile, the P10 target is above it and the
whole range contains no downside scenario. The arithmetic is correct and `downside_to_bear` is correctly
computed as `level / price - 1`; the field name asserts a direction the number does not have. Not
renamed here, because a field rename in D5-D2's contract is a D5-D2 change.

### K.4 Clause F is declared and untested

`valuation_confidence_sufficient` is the one APPROVE conjunct D0 did not freeze (§E.3). On this sample it
is never the binding clause - the gap clause fails on all eight eligible issuers - so whether
{HIGH, MEDIUM} is the right admissible set is pre-registered and unexercised. Worse, `HIGH` is
unreachable by construction: `PEER_CONTEXT_UNAVAILABLE` demotes every issuer to MEDIUM at best, so the
clause is in practice "MEDIUM". Five of the six WATCHes cite it - all but DORM - and not one of them
would have been decided differently without it, because the gap clause fails on all five as well.

### K.5 The unknown-field matcher over-matches, and is left as it is

`no_approve_blocking_unknown_field` matches D0 §N1's three field names as substrings of D3's free-text
`unknown_fields` entries. On TG it fires on `fundamental_changes.free_cash_flow` and
`fundamental_changes.operating_margin`, which are sub-fields of fundamental_change rather than the field
itself. It is reported rather than tightened, because narrowing a matcher after seeing which issuer it
hit is fitting the contract to the sample. It changes no decision - TG's gap clause already blocks
APPROVE - and it is the first thing to settle before D7, on the contract rather than on the data.

### K.6 Two of D0 §N3's seven triggers cannot be evaluated mechanically

"Structural deterioration evident with no offsetting catalyst" and "risk level assessed as unacceptable
relative to any plausible upside" both require weighing evidence rather than reading a field, and no
stored D3 field settles either: `growth_durability` publishes a state (`MIXED` on ten of thirteen) and
not a severity, and `risks` is a list whose length says nothing about how bad they are. Implementing
them from a proxy - MIXED as deterioration, a risk count as severity - would manufacture REJECTs out of
a threshold nobody set. They are reported per issuer as `NOT_EVALUATED` with the reason attached, and a
test asserts both never fire and both say so, because a trigger that is silently never evaluated reads in
a report exactly like a trigger that never fired.

The consequence is specific: **VRRM is the issuer those two triggers were written for.** D3 records
customer extensions on materially less favourable terms with a third outstanding, and the price is at a
two-year low. A human would call that structural deterioration. D6 cannot, and so VRRM is a WATCH with a
+404.7% TP1 rather than a REJECT.

### K.7 Inherited and unrepaired

- `PEER_CONTEXT_UNAVAILABLE` on all thirteen: no external check on whether an issuer's own multiple
  range is itself reasonable (D5-D2 §F.4), and it caps every confidence at MEDIUM.
- `GUIDANCE_UNKNOWN` on all thirteen: no scenario operand comes from management guidance.
- `RECENT_2Y_CONTEXT` only: at most 501 sessions, 2024-09-17 to 2026-09-16. No long-term range is claimed.
- Seven of thirteen have no enterprise value, so their valuations are equity-side by necessity rather
  than by choice. Six of those are the borrowing balance: COLL, CRK, FRPT, GOOG, SCCO and SPSC resolve
  no total debt at the cash date. BSY is the reverse - it resolves net debt of 1,070.2m and no market
  cap. The absence is a data gap rather than a statement that these issuers are unlevered, and COLL is
  where that is provable from the artifact: D3 lists `current_total_debt_code_owned (stale 2019 fact)`
  among its own unknown fields, so a debt fact exists and is 2,400 days too old to use.
- D5-D2's §N.1 stands: the framework cannot tell that an issuer's whole observed multiple range is
  elevated. SCCO is the clearest instance in this sample - a 56.9% operating margin at a trailing copper
  price.
- D3's `research_completeness` is `PARTIAL` on all thirteen. There is no `COMPLETE` in this sample.

### K.8 Of this step's own making

- The resolution order (REJECT before APPROVE) is a reading of D0 rather than a line D0 contains. §D.2
  argues the two lists are not independent; the argument has not been tested against a case where it
  matters, because no issuer here both fires a trigger and satisfies the conjunction.
- `catalyst_material_and_timed` requires HIGH/MEDIUM on both materiality and timing confidence. Nothing
  in D0 fixes that pairing; §N2 makes an undated catalyst a WATCH trigger, which is where the pairing
  came from, but the threshold is this step's.
- The judgement table is one model's reading of thirteen businesses. It was declared before the numbers
  and it is still a judgement, and a different reading of, say, whether P/B can anchor FRPT would change
  that issuer's eligibility.

---

## L. Tests

```
new in this step                    85   backend/tests/strategy_h_v2/decision/
H-V2 suite before this step       1608   passed, 1 skipped  (suite run with this step's tests excluded)
H-V2 suite after                  1693   passed, 1 skipped
failures introduced                  0
```

**Regression scope, and why it is the H-V2 suite rather than the whole backend.** `pytest backend/tests`
cannot be collected in this working tree: 20 collection errors, all tracing to a concurrent session's
**untracked** `backend/app/backtest/replay/session_replay.py`, which imports `SettlementAction` from
`app.services.entry_management_runtime`. That name exists neither in the working tree nor at HEAD, so the
breakage is another session's in-progress code and predates this step:

```
grep -c SettlementAction  working tree entry_management_runtime.py   0
grep -c SettlementAction  git show HEAD:... same file                0
git status --porcelain    backend/app/backtest/replay/session_replay.py   ?? (untracked)
```

The H-V2 suite is unaffected and is the scope reported above, measured three times with the same result.
A `--continue-on-collection-errors` run over the whole backend was started and **deliberately abandoned**
rather than completed: two concurrent sessions were running their own `pytest backend/tests` at the same
time, so the tree was being edited underneath it, and their completion waiters poll
`pgrep -f "pytest backend/tests -q"`, which this run's own command line matched. A number produced under
those conditions would not have been a regression signal for this step, and producing it was interfering
with two other sessions. The scope of the regression claim is therefore the H-V2 suite, and the files this
step touches are all inside it.

Every test in the brief's §28 list, and the file each one lives in:

| §28 requirement | test |
|---|---|
| D3 positive + D4 acceptable + valuation upside -> decision contract | `test_the_only_combination_that_approves_does_approve` |
| every conjunct is necessary | `test_every_approve_clause_is_individually_necessary` (parametrised over the clause names, so a new clause with no negation fails the test) |
| great company but overvalued | `test_a_great_company_that_is_fully_priced_is_rejected_on_the_bull_leg` |
| cheap but weak thesis | `test_a_cheap_valuation_cannot_carry_a_weak_thesis` |
| D4 NOT_EVALUATED | `test_an_absent_d4_makes_the_issuer_ineligible_and_produces_no_decision` (parametrised over both absence states) |
| D4 negative | `test_a_negative_gap_rejects_however_cheap_the_valuation_is` |
| valuation LOW confidence | `test_a_low_confidence_valuation_cannot_carry_an_approve_but_is_not_a_reject` |
| valuation NOT_READY | `test_a_valuation_not_ready_issuer_is_ineligible_and_is_neither_positive_nor_negative` |
| TP1 / TP2 immutable | `test_d6_never_mutates_a_d5_target_price` and `test_the_audit_catches_a_mutated_target_price` |
| no score gating | `test_no_score_or_execution_field_exists_anywhere_in_the_decision_module`, `test_the_record_emits_no_execution_or_score_key` |
| no invented valuation | `test_project_d5_on_a_not_ready_row_carries_no_target`, `test_the_decision_is_a_pure_function_of_the_clause_lists` |

Beyond the list, and the ones worth naming:

- `test_gap_precondition_is_exactly_d0_section_l` walks the full 6 × 4 grid of gap state × confidence,
  36 cells, and asserts APPROVE is reachable on exactly the eight §L permits.
- `test_unknown_is_decided_on_while_an_absence_is_not` asserts §10's distinction as a difference in
  outcome rather than in wording.
- `test_a_different_d3_output_than_d4_consumed_is_a_chain_break` and
  `test_a_matching_id_with_a_different_checksum_is_still_a_chain_break` - D4 stored the id **and** the
  checksum of the D3 output it consumed, so the chain is checkable and not a naming convention. The
  defect it guards would be invisible everywhere else: both legs would look internally consistent.
- `test_the_audit_catches_a_mutated_target_price` and `test_the_audit_catches_a_decision_on_an_ineligible_issuer`
  build records the engine cannot produce, because an audit that cannot fail proves nothing.
- `test_no_issuer_in_the_universe_carries_a_positive_expectation_gap` measures the claim this document's
  headline rests on against the stored artifacts rather than asserting it.

The artifact-backed tests skip when `data/runtime/` has no run, following D4.1's pattern.

### L.1 Defects, each as a list of named offenders rather than a count

```
D5 layer (D5-D2's own audit, over the 13)
  fair_value_at_current_multiple_is_not_price          0
  bear_base_bull_not_monotone                          0
  target_multiple_not_an_observation                   0
  target_multiple_observed_after_decision              0
  not_ready_issuer_carrying_a_target_price             0
  non_positive_fair_value_published                    0
  upside_not_equal_to_its_own_formula                  0
  fair_value_not_equal_to_its_own_operands             0
  price_only_window_governed_a_target                  0
  fair_value_range_missing_a_leg                       0

D6 layer
  d5_number_mutated_by_d6                              0
  approve_without_every_clause_holding                 0
  decision_produced_for_an_ineligible_issuer           0
  eligible_issuer_without_a_decision                   0
  approve_on_an_adverse_or_absent_expectation_gap      0
  approve_without_a_positive_base_case_upside          0
  decision_resting_on_an_absent_layer                  0
  d3_to_d4_provenance_chain_broken                     0

D4 precondition disagreements                          0
```

### L.2 Determinism and provenance

Three consecutive full runs of the pilot, compared by sha256 over the substance - every valuation row,
every decision record, every coverage record, every defect list:

```
d5_rows            identical across all three runs
decisions          identical
coverage           identical
decision_counts    identical
d5_defects         identical
d6_defects         identical
provenance_chain   identical
```

### L.3 The D3 -> D4 chain, checked rather than assumed

D4 stored both the id and the **checksum** of the D3 output it consumed, so whether the D3 record D6 read
is the one D4 was computed from is a checkable fact and not a naming convention:

```
issuers checked                           13 / 13
research_input_id matches D3 research_id  13 / 13
research output checksum matches          13 / 13
chain breaks                               0
```

It holds on FRPT and SPSC too: D4 recorded which D3 output it consumed even on the attempts its contract
then declined, so the two ineligible issuers' research legs are provenance-complete and only their
expectation leg is absent. The defect this guards would be invisible in every other output - a company
thesis integrated with an expectation gap derived from a different one, both legs internally consistent.

---

## M. Verdict

```
H-V2-D6
=
READY WITH LIMITATIONS
```

Against the brief's §21 practical success criteria:

| criterion | required | measured |
|---|---|---|
| decision-eligible issuers | at least 3 | **8** |
| D3 / D4 / D5 provenance preserved | yes | **every decision carries its D3 and D4 output checksums; chain verified 13 of 13, breaks 0** |
| invented facts or numbers | 0 | **0** |
| valuation numbers bitwise unchanged from the D5 evaluator | yes | **0 mutations; D5-D2's own report reproduces by sha256** |
| APPROVE / WATCH / REJECT rationale reproducible | yes | **every clause names its contract line and the stored field that settled it** |

READY **WITH LIMITATIONS** rather than READY FOR FORWARD SHADOW, and §K.1 is the whole reason. The
integration works: three layers join on eight issuers, the conjunction holds in both directions, the
abstentions are argued, the arithmetic reproduces by hand at fifty digits, and the engine owns no number.
But the pilot published a +404.7% TP1 on an issuer in evident distress, and it did so because a rule from
the previous step asks only the longest window whether it drifted. A forward shadow launched on this
engine as it stands would be shadowing that defect as well as the contract.

What is established:

- The three layers can be joined on one issuer and produce one coherent judgement with full provenance.
- APPROVE is genuinely hard to reach and the engine says exactly which clause blocked it - on this sample
  the same clause, D0 §L's expectation precondition, on all eight.
- REJECT fires from distinct evidence rather than from a score: one on the Bull leg being below price,
  one on §J future-business evidence being absent.
- The absence / UNKNOWN distinction survives contact with real artifacts: two issuers whose D4 refused
  are outside the sample rather than neutral inside it.
- D5-D2's conflict ratio and its confidence rule caught the one issuer the window rule mishandled. The
  cross-checks are load-bearing, not decorative.

What is not established:

- That any decision is *right*. Nothing here is measured against an outcome.
- That the window rule is sound (§K.1), that 1.5x is the right conflict line, or that {HIGH, MEDIUM} is
  the right admissible valuation confidence (§K.4).
- Anything about how often an expectation gap is positive, or about valuation coverage in general. The
  sample is D4's (§C.2).
- That the two unevaluable REJECT triggers do not matter. VRRM is evidence that they do (§K.6).

---

## N. Forward Authorization

```
H-V2-D7
FORWARD SHADOW
= AUTHORIZED TO DESIGN
= NOT AUTHORIZED TO LAUNCH UNTIL §K.1 IS RESOLVED ON THE CONTRACT
```

A READY-class D6 authorises D7's design and this document authorises it. The condition is narrow and is
not a general caution: the window rule's trend test is evaluated on FULL_2Y only, and a forward shadow
would record the consequences of that as if they were the contract's. Resolving it means deciding, on the
contract and before looking at any issuer's result, whether the rule should shorten when **any** longer
window is `TRENDING_STRONG`. That decision is the user's and this step does not make it.

What D7 must carry forward unchanged:

- **The first validation is forward.** No decision made here is checked against what any issuer
  subsequently did. No price after 2026-09-16 is read by anything in this step, and the panel's last
  session is asserted to be that date. A D6 whose clauses had been nudged until the APPROVEs looked right
  would produce a forward test of its own tuning.
- **No entry, no exit, no size, no order, no ranking** (§D.5), asserted by field name and by emitted key.
- The thesis horizon stays weeks to three months (D0 §K/§R), so a forward shadow has to be long enough to
  contain one and short enough that the thesis has not been replaced.
- Zero APPROVE means a forward shadow of this sample would shadow six WATCHes and two REJECTs. Whether
  that is informative is a D7 design question: a WATCH makes no claim about a return, so "did the WATCH
  go up" is not a test of anything. The things that *are* falsifiable forward are narrower - whether an
  issuer whose Bull leg was below price failed to exceed it, and whether a named invalidation condition
  fired.

```
BUY / SELL                           NO
entry / exit / position size         NO
portfolio generated                  NO
orders generated                     NO
forward returns used                 NO
D5-D2 rules changed                  NO
D5-D1 or D5-D2 verdicts modified     NO
new primitive project opened         NO
target multiples retuned             NO
live model calls                     0
cost                                 $0.00
push                                 NO
```
