# H-V2-D5-D0 - Valuation Fundamentals / Contract Design

```
H-V2-D5-D0
= READY FOR VALUATION DATA PILOT

live Opus investment calls   0
live cost                    $0.00
valuation results            none
fair value / TP1 / TP2       none generated
D4 historical results        unmodified
methods applicable today     1 of 12   (P/B)
D5-D1                        PROPOSED, not executed
```

D0 left the valuation layer empty and said so: `"valuation_view": {}`, "structure is deferred to
§M/D5 and is not implemented here", and §AA.5 "which of the candidate methods are actually
implementable depends on resolving H0's EBITDA/debt-normalization gap; this is a D5 question, not a
D0 one". This document answers that question from what the repository measurably has.

The answer is uncomfortable and is the main finding of this step. On the ten issuers D4's live runs
actually touched, **every income-statement field resolves AMBIGUOUS or MISSING**, and the canonical
`total_debt` resolves to a single long-term component chosen by tag priority rather than to total
debt - silently, with status `OK`. So P/E, P/S, EV/EBITDA, EV/EBIT and EV/Sales are not computable
today, P/FCF needs a trailing-twelve-month construction that does not exist, and exactly one method
in the contract has all of its inputs: P/B, which is also the narrowest.

That is not a reason to loosen anything. It is the reason this contract is mostly refusals, and it
makes the next step a data measurement rather than a valuation run.

Not an alpha result, not a backtest, no target price, and not a statement about any issuer.

---

## A. Purpose

D5 answers one question, for a candidate D3 and D4 have already characterised:

```
good business?                     D3
real change?                       D3
better than the market expects?    D4
how much is already in the price?  D5
```

D5 is not an independent stock-ranking engine and a cheap multiple is not a finding on its own. That
distinction is what separates this layer from H-PV1's value factor, which ranked on low PER and high
FCF yield and is not what Strategy H is. `VALUATION_IS_NOT_A_RANKING_ENGINE` says it in code.

This step freezes four contracts before any result exists: the PIT data contract, the valuation
method contract, the comparability contract, and the target-price mechanics contract. It computes no
valuation.

---

## B. D4 Handoff

What D0 actually asked for, quotable rather than paraphrased:

```
D0 §N1   "valuation view supports upside under at least the Base case
          (§M/valuation, arithmetic deferred to D5)"      - one of seven APPROVE preconditions
D0 §L    WIDE_POSITIVE also requires "valuation has not yet re-rated to reflect it"
D0 §N2   WATCH: "company attractive but current price leaves insufficient margin of safety"
D0 §N3   REJECT: "valuation already reflects the full positive case (fully priced)"
D0 §M    valuation against the company's own historical range, and against a stated peer set
          whose relevance must be argued rather than assumed
```

The §L conjunct is already carried in code: `gap_contract.WIDE_POSITIVE_VALUATION_CONJUNCT_DEFERRED`
records on every WIDE_POSITIVE D4 produces that the valuation clause is `UNEVALUATED, not met`, and
that a D4 WIDE_POSITIVE is "provisional on D5 and must be re-checked by D6, never inherited as
satisfied". D5 is the stage that evaluates it.

**What D0 never defined.** `TP1`, `TP2`, `Entry1`, `Entry2`, `fair_value` and `exit` appear nowhere
in this repository except as exclusions. `H_V2_ARCHITECTURE_REDESIGN_V1.md` §B states it directly:
these terms "are never defined anywhere else in the repository", "their only appearances in the
entire codebase are as exclusions inside the H0 documents", and "there is no prior document that
specifies what Entry1/Entry2 or TP1/TP2 actually mean". H0's own §L repeats that they were not
implemented.

So §L of this document **defines** TP1 and TP2. It does not restore an intent, and a document
claiming to recover their original meaning would be inventing a provenance.

**The D4 banned-field list is a layer boundary, not an obstacle.**
`analysis_schema.BANNED_D4_FIELD_NAMES` forbids `valuation`, `valuation_view`, `fair_value`,
`upside`, `tp1`, `tp2`, `entry` and `exit` inside a **D4 output**. That list is unchanged by this
step. D5 writes its own artifact under its own schema; a D5 field appearing in a D4 response is still
a defect.

**D4 limitations carried forward, unresolved.** C1 is `NOT_EVALUATED` across three consecutive graded
runs (Tier A V3, Tier B, D4-BR-C) because none produced a POSITIVE-family gap. R3 is
`OBSERVED / DEFERRED`, exposure 1, wired to no gate. Neither is resolved here and neither blocks D5:
D5's arithmetic does not depend on the gap state. D5 also inherits E7R's repaired decision-leakage
authority, which is what keeps a valuation narrative from becoming a recommendation.

---

## C. Valuation Philosophy

```
arithmetic, period and unit errors    forced by code
qualitative reading                   the model's
UNKNOWN                               stays UNKNOWN
```

Code owns market cap, enterprise value, every multiple, every growth rate, every margin, net debt,
free cash flow, TTM construction, period classification, any DCF arithmetic, the valuation range, the
target prices, the upside measures, and every peer fundamental. The model owns which framework fits
the business, whether a proposed peer is economically comparable, future-business maturity carried
from D3, the rationale for multiple expansion or contraction, and where in an **observed** range to
sit. It never names the number.

A model-supplied value for a code-owned quantity is **rejected rather than checked**, which is the
D4 precedent applied unchanged: a response that writes a code-owned field is a defect, not a
disagreement.

**There is no default multiple.** A fixed "PER 20x" for every issuer is forbidden, and so is a
multiple the model supplies from its own prior. Every multiple in a fair-value computation must be an
observed value - the issuer's own measured range or a measured peer range - carried with its
provenance.

---

## D. PIT Data Contract

Source for every fundamental: SEC companyfacts with each accession joined to submissions
`acceptanceDateTime`, via H0's `facts.py`, reused unchanged. Facts without that join stay unavailable
rather than falling back to `filed`.

Measured on the ten issuers D4's live runs touched - AEYE, COLL, FG, VRRM, IDCC, DORM, FRPT, TG, CRK,
SPSC - from their stored D2.1 packages at cutoff `2026-09-28T05:49:37Z`:

| canonical field | OK | AMBIGUOUS | MISSING | D5 availability |
|---|---|---|---|---|
| revenue | 0 | 9 | 1 | BLOCKED_DURATION_AMBIGUITY |
| gross_profit | 1 | 5 | 4 | BLOCKED_DURATION_AMBIGUITY |
| operating_income | 0 | 8 | 2 | BLOCKED_DURATION_AMBIGUITY |
| net_income | 0 | 10 | 0 | BLOCKED_DURATION_AMBIGUITY |
| eps_diluted | 0 | 10 | 0 | BLOCKED_DURATION_AMBIGUITY |
| operating_cash_flow | 10 | 0 | 0 | AVAILABLE |
| capex | 8 | 0 | 2 | AVAILABLE |
| cash | 10 | 0 | 0 | AVAILABLE |
| total_debt | 9 | 0 | 1 | BLOCKED_COMPOSITION |
| assets | 10 | 0 | 0 | AVAILABLE |
| equity | 10 | 0 | 0 | AVAILABLE |
| shares_outstanding | 10 | 0 | 0 | AVAILABLE |

**This is a bounded spot-check, not the pilot.** Ten issuers, chosen because D4 already touched all
of them rather than because of how they scored, measure a mechanism and not a prevalence. D1.1's
universe-wide figure is the one that bounds the population: its `FUNDAMENTAL_FACT_AMBIGUOUS` rule
fired on **2,143 of 5,192 securities (41%)**, and sampling 200 showed every instance was one 10-Q
legitimately reporting a line item for both a discrete quarter and the year to date.

Two fields the filings carry and the pipeline cannot see:

```
depreciation_amortization    reported by 10 of 10 issuers, absent from FIELD_SPECS
interest_expense             reported by  9 of 10 issuers, absent from FIELD_SPECS
```

So H0's "missing EBITDA normalization" needs restating: **D&A is available in the filings.** EBITDA's
blocker is `operating_income`, plus the fact that D&A was never made canonical.

Price: the local grouped-daily panel, **501 sessions, 2024-09-17 to 2026-09-16**, every session
carrying `adjusted: false`. That is what H0.5's market-cap primitive requires, and it is satisfied.
The panel's last session is 2026-09-16; a decision time after it has no close and therefore no market
cap, which is UNKNOWN rather than the last close carried forward.

Missing behaviour, for every field: `UNKNOWN`. No substitution, no interpolation, no current-value
fallback.

**One naming trap worth recording.** The D2.1 bundle's `identity.snapshot_date` is the universe
snapshot (2024-10-25), not the decision time; `data_cutoff` (2026-09-28) is the decision time. Using
the former as the PIT reference makes every balance-sheet fact look like it came from the future.

---

## E. Period Consistency

The dominant correctness problem, and the one D5 was always going to inherit.

**Failure one: a legitimate double report becomes AMBIGUOUS.** `facts.resolve_fact` narrows candidate
facts by tag, acceptance time and accession, but **not by duration family**. A single 10-Q reporting
revenue for 2026-04-01..2026-06-30 and for 2026-01-01..2026-06-30 therefore has two facts sharing one
winning provenance, and the resolver reports `AMBIGUOUS` - truthfully, under its own promise, and
uselessly for valuation. This is why the income statement is 0 of 10.

**Failure two: a single report resolves OK and is a partial year.** A field reported only as year to
date has nothing to conflict with, so it resolves `OK` and carries no warning. Measured: AEYE's
`operating_cash_flow` at the D2.1 cutoff resolves OK with `start 2026-01-01, end 2026-06-30` - a
**180-day** figure. A market cap divided by that is a price-to-half-year-FCF, and it looks exactly
like a multiple.

Worse, the D2.1 bundle stores `status`, `value`, `end`, `accession` and `accepted_at` for each field
and **not `start`**, so a downstream consumer cannot tell a quarter from a year to date from the
bundle alone.

The contract therefore classifies every duration before using it:

```
INSTANT   no start
QUARTER   80-100 days
YTD       170-195 or 260-285 days, or any span whose start is the fiscal-year start
ANNUAL    350-380 days
UNKNOWN   anything else - never assigned to the nearest family
```

Windows are deliberately wide enough for 52/53-week fiscal calendars and deliberately
non-overlapping. A first quarter and a first-quarter year-to-date figure are the same span, so the
fiscal-year start decides: with it, the span is YTD; without it the ambiguity is recorded in
`YTD_Q1_INDISTINGUISHABLE` rather than resolved by assumption.

```
growth rate    both sides must be the SAME family
               a quarter over a year-to-date is not a growth rate, it is an arithmetic accident
denominator    TTM or ANNUAL only
               QUARTER, YTD, INSTANT and UNKNOWN are all refused
```

No canonical field is TTM today. A TTM flow must be **constructed** by code from four
non-overlapping quarters, or from an annual fact plus the difference of two year-to-date facts
sharing one fiscal-year start. Interpolating a missing quarter, annualizing a partial year, and
multiplying a quarter by four are each forbidden by name: each invents a number the filings do not
contain.

When any of this fails, the output is `UNKNOWN` / `NOT_COMPARABLE`. There is no forced calculation.

---

## F. Market Cap / EV

**Market cap** is H0.5's frozen primitive, reused unchanged through `h0_5.resolve_pit_shares` and
`h0_5.historical_market_cap`:

```
MarketCap(T) = unadjusted regular-session close(T) x raw PIT shares(T)
```

UNKNOWN on missing, stale (>135 days), ambiguous or multi-class shares; on a split between the shares
instant and T without a reported post-split fact; and on a missing unadjusted close. Current
shares/market-cap fallback is forbidden, **including for a current-date decision** - §8 of the brief
asked for the PIT shares appropriate to the decision point, and that is what the primitive already
does.

**Multi-class is fail-closed in capability and unwired in practice.**
`resolve_pit_shares(multiple_share_classes=True)` returns UNKNOWN, and the only caller anywhere that
passes `True` is a test. Nothing in the production pipeline detects a multi-class issuer. This is the
same shape as D4-H's finding that its gates were built and never wired, and it means D5-D1 must
either supply the detector or report multi-class as an unmeasured risk. It may not treat the
capability as coverage.

**Enterprise value is UNKNOWN in v1, and the reason is a mechanism rather than a worry.**
`facts.resolve_fact` selects one tag by priority - `best_tag = min(spec.tags.index(...))` - and never
sums. `total_debt`'s priority order begins with two current-portion-only tags:

```
LongTermDebtAndFinanceLeaseObligationsCurrent
LongTermDebtCurrent
LongTermDebtNoncurrent
LongTermDebt
```

Demonstrated on a fixture: a filer reporting current 50M, noncurrent 900M and total 950M resolves to
`LongTermDebtCurrent` = **50M with status OK**. Measured on the ten issuers:

| issuer | canonical tags present | resolver picks | resolved value | period end |
|---|---|---|---|---|
| AEYE | Current, Noncurrent, LongTermDebt | **LongTermDebtCurrent** | 850,000 | 2026-06-30 |
| VRRM | Current, Noncurrent, LongTermDebt | **LongTermDebtCurrent** | 10,000,000 | 2026-06-30 |
| DORM | Current, Noncurrent, LongTermDebt | **LongTermDebtCurrent** | **0** | 2026-06-27 |
| IDCC | Current, Noncurrent, LongTermDebt | **LongTermDebtCurrent** | 378,239,000 | 2026-06-30 |
| FRPT | Current, Noncurrent, LongTermDebt | **LongTermDebtCurrent** | 72,872,000 | **2022-09-30** |
| TG | Current, Noncurrent, LongTermDebt | **LongTermDebtCurrent** | 46,000,000 | 2026-06-30 |
| CRK | Current, Noncurrent, LongTermDebt | **LongTermDebtCurrent** | 3,098,770,000 | 2026-06-30 |
| COLL | LongTermDebt only | LongTermDebt | 11,500,000 | **2019-12-31** |
| FG | LongTermDebt only | LongTermDebt | 2,239,000,000 | 2026-06-30 |
| SPSC | none of the four | **MISSING** | - | - |

Seven of ten resolve to the current portion while the same filer also reports `LongTermDebt`. No
short-term borrowing tag is canonical at all: the filers report `ShortTermBorrowings`,
`NotesPayableCurrent`, `DebtCurrent`, `CommercialPaper` and
`LineOfCreditFacilityAmountOutstanding`, and `FIELD_SPECS` has none of them. SPSC resolves MISSING
while reporting a drawn revolver.

**Balance-sheet staleness is unbounded.** Unlike shares, `resolve_fact` applies no staleness limit to
instant fields. COLL's debt comes from a period end **2,463 days** before the cutoff with status OK;
FRPT's from 1,459 days. And 3 of 10 issuers have a cash period end that does not match their debt
period end, so a net-debt figure would be built from two different balance sheets.

Until a debt-composition contract exists - defining the summation set, whether operating leases are
included, a staleness bound, and a cash/debt same-period requirement - `enterprise_value` is UNKNOWN
and no method whose name starts with EV is applicable. `DEBT_COMPOSITION_CONTRACT = "NOT_IMPLEMENTED"`.

One more normalization note: `cash`'s second tag is
`CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents`, which **includes restricted cash**.
When it is the resolved tag the value overstates the cash available to net against debt.

---

## G. Valuation Methods

Twelve candidates, each with required fields, a valid denominator rule, when it is appropriate, when
it is forbidden, and its v1 status. Frozen in `METHOD_SPECS`.

| method | denominator | v1 status | blocker |
|---|---|---|---|
| P/B | common equity, instant | **APPLICABLE** | inputs OK 10 of 10; narrow application |
| P/E | net income, TTM or annual | BLOCKED_DATA | income-statement duration ambiguity |
| P/Sales | revenue, TTM or annual | BLOCKED_DATA | income-statement duration ambiguity |
| P/FCF | free cash flow, TTM or annual | BLOCKED_DATA | inputs OK but YTD; needs TTM construction |
| FCF yield | FCF / market cap | BLOCKED_DATA | same TTM gap |
| EV/EBITDA | EBITDA, TTM or annual | BLOCKED_DATA | debt composition; D&A not canonical |
| EV/EBIT | EBIT, TTM or annual | BLOCKED_DATA | debt composition; duration ambiguity |
| EV/Sales | revenue, TTM or annual | BLOCKED_DATA | debt composition; duration ambiguity |
| growth-contextualized multiple | a multiple read against a same-family growth rate | BLOCKED_DATA | duration ambiguity |
| own RECENT_2Y multiple range | the issuer's own multiple | BLOCKED_DATA | the multiple itself is blocked |
| peer comparable range | peer multiples | BLOCKED_DATA | peers inherit the same defects |
| any forward multiple | a forward estimate | BLOCKED_NO_PROVIDER | no PIT consensus has ever been proven |

**Negative and invalid denominators.** A multiple whose denominator is zero, negative, or on a
partial period is `NOT_APPLICABLE` - never a negative multiple. A P/E of -14 reads like a cheap stock
and means a loss. Such an issuer is assessed by whichever other method is valid, and if none is,
the candidate is `NOT_READY`.

**PEG is not published as a number.** A single PEG figure hides which multiple and which growth
window produced it; the contract permits reading a computed multiple against a measured, same-family
growth rate and forbids collapsing the two into one ratio.

**What must land before any of this opens up**, ordered by how many methods each unblocks:

```
duration-family narrowing   8 methods   narrow duration facts by period family before selecting
TTM construction            4 methods   four quarters, or annual plus two YTD differences
debt composition            3 methods   a summation contract, a staleness bound, same-period cash
D&A as a canonical field    1 method    tags 10 of 10 issuers already report
multi-class detection       conditions every method - it gates market cap rather than unblocking
```

D5-D0 authorizes none of these. Each changes a primitive that H0, D1 and D4 all already depend on, so
each needs its own contract and its own regression evidence.

---

## H. Peer / Historical Context

**Peer eligibility** must be argued against five measured criteria: same or adjacent business
(what is sold, to whom, under what revenue model), comparable margin regime, comparable growth regime
on the same period family, comparable capital intensity, and the same method computable on the peer
from code-owned fields. A shared sector or SIC code is **not** peer eligibility and may not be the
only argument - D0 §M already froze that. The model may **propose** peers and must argue each; code
computes every peer fundamental and every peer multiple. A peer whose own fields are blocked is not a
peer for that method.

Below `PEER_MIN_ELIGIBLE = 3`, the output is a comparison to a named company and must be labelled
that way rather than presented as a range.

**Historical own-multiple range is `RECENT_2Y_RANGE`.** The panel is 501 sessions, 2024-09-17 to
2026-09-16. `LONG_TERM_HISTORICAL_RANGE` is a forbidden label in v1, and not out of modesty: a
two-year window starting in September 2024 contains one macro regime and at most eight reported
quarters, so it cannot show how an issuer was valued in a different one. H0 measured the same bound
and the paid paths that would extend it to five, ten or twenty years were priced and never purchased.

---

## I. Guidance

Management guidance becomes a code-owned scenario input only when metric, period, low, high and unit
are all present, and the period is comparable to the denominator the scenario uses. This reuses
D4-BR's operand completeness unchanged - `expectation.comparability.REQUIRED_OPERANDS` - rather than
restating it.

A one-sided floor ("at least 48%") is not a range and may not be completed. A missing bound may not
be interpolated from the other one. Incomplete guidance yields `UNKNOWN` for that scenario input, and
UNKNOWN is a valid scenario state.

Guidance is the company's own statement, not the market's expectation. Using it as a stand-in for
consensus would manufacture the provider D4 established does not exist.

---

## J. Scenario Framework

`BEAR`, `BASE`, `BULL`. Every scenario input needs provenance from exactly three sources:

```
code-owned current fundamentals, named field by field with period end and period family
an explicit assumption source: a complete guidance range, a measured own/peer multiple
   observation, or a measured historical growth rate
the direction and magnitude of every deviation from the code-owned current value
```

A scenario whose operating assumption has no entry in that list is not a scenario, and the model may
not supply one. Three numbers the model chose are three guesses wearing a framework.

---

## K. Fair Value

A **range**, never a point: `lower` / `base` / `upper` per share. The arithmetic is code-owned:

```
value = (code-owned per-share metric for that scenario) x (observed multiple for that scenario)
```

with both operands carried as provenance. A point estimate implies a precision that neither the
multiple nor the period data supports.

---

## L. TP1 / TP2

Defined here, because nothing defined them before (§B).

```
TP1 = base_scenario_per_share_metric x base_multiple
```

The price at which the BASE case is fully reflected. Reaching TP1 means the thesis was right and the
market has finished agreeing with it. This is the level D0 §N1's "supports upside under at least the
Base case" is measured against, and D0 §N3's "already reflects the full positive case" is the same
level reached *before* entry rather than after.

```
TP2 = bull_scenario_per_share_metric x upper_multiple
```

The upper-scenario level, where the multiple is still an observed value. **TP2 is not TP1 plus a
margin**, and which of the two operands moved - the metric, the multiple, or both - is reported
separately.

```
BEAR_ANCHOR = bear_scenario_per_share_metric x lower_multiple
```

Each requires the per-share metric's field, period end and period family; the multiple's observed
source and where in that observed range it sits; and the argument for that position.

**No target price may be fitted to the return it would have predicted.** There is no realized-return,
forward-return or price-outcome input anywhere in the D5 contract, and a test asserts the package
contains none. That is the one failure mode no later gate can detect, because a fitted number looks
exactly like a derived one.

**Upside**, code-owned, and where D5 stops:

```
UpsideToTP1    = TP1 / current_unadjusted_close - 1
UpsideToTP2    = TP2 / current_unadjusted_close - 1
DownsideToBear = BEAR_ANCHOR / current_unadjusted_close - 1
```

D5 does not convert these into buy, sell, approve, watch, reject, an entry level, an exit level or a
position size. D6 combines D4's expectation gap with D5's valuation, and D6 does not exist.

---

## M. D4 Integration

```
D5 reads D4's output and never rewrites it.
A cheap valuation does not promote a NEGATIVE expectation gap.
An expensive valuation does not demote a POSITIVE gap either - D5 reports insufficient upside.
D0 §L's WIDE_POSITIVE valuation conjunct is D5's to evaluate; until D5 runs it stays UNEVALUATED.
```

The two layers are allowed to disagree, and a disagreement is a finding for D6 rather than something
D5 resolves. `valuation cheap + D4 NEGATIVE` is not an automatic anything.

---

## N. Unknown / Not Ready

```
COMPLETE     market cap valid, >= 2 applicable methods, scenario set with full provenance
PARTIAL      market cap valid, exactly 1 applicable method, or provenance incomplete
NOT_READY    no safely computable method, or market cap invalid
```

Two methods rather than one, because one method is a number and two agreeing or disagreeing methods
are a valuation; a single method cannot be cross-checked, and cross-checking is most of what makes a
multiple trustworthy. Since P/B is the only applicable method today, **every candidate is PARTIAL at
best right now** - and for an asset-light issuer P/B is NOT_APPLICABLE on its own terms, which makes
it NOT_READY.

`NOT_READY` is a valid, complete D5 output and is not a failure of the candidate. The model is never
asked for a target price on a NOT_READY candidate, and a narrative that implies one anyway is a
defect. This is D4-BR's lesson applied before it has to be learned again: an honest abstention is an
output, and only an empty output is a failure.

---

## O. Data Coverage

What this step measured, and the line it did not cross. The ten-issuer tables in §D and §F are a
**mechanism spot-check over already-stored artifacts**, which is repository audit. The coverage
measurement itself is D5-D1 and was not run, per §25 of the brief.

Prior measurements this contract rests on rather than re-deriving:

```
D1.1   FUNDAMENTAL_FACT_AMBIGUOUS fired on 2,143 of 5,192 securities (41%)
       200 sampled instances were all quarter-vs-YTD double reports
D1.1   SEC companyfacts coverage 5,065 of 5,089 unique CIKs
H0     gross profit 29 of 40 - the weakest common margin input
H0     EV/EBITDA "missing EBITDA normalization"; EV/sales "debt composition incomplete"
H0.5   local daily store 501 sessions, unadjusted; multi-class allocation unresolved
H0.5   paid 5y/10y/20y paths priced, never purchased
D4     consensus_status = SOURCE_NOT_AVAILABLE on every graded candidate
```

---

## P. D5-D1 Proposal

```
H-V2-D5-D1
VALUATION DATA FEASIBILITY PILOT
= PROPOSED, NOT EXECUTED
```

Purpose: measure valuation-data completeness, period consistency and method applicability. It
publishes no multiple, no fair value and no target price - it counts what is computable.

Sample: deterministic, performance-blind, 12 issuers, seeded hash over the D2.1 package universe by
the same convention D3.1 Batch 2, D3.3, Tier B and D4-BR all used. No exclusion list and no
stratification by D3 or D4 outcome: whether valuation **data** exists has nothing to do with how a
candidate scored, and excluding the issuers D4 touched would discard the only candidates a later D6
could join to.

```
seed       H_V2_D5_D1_VALUATION_FEASIBILITY_V1
n          12
sample     ADBE DALN CHRS FET NATR STAA CHWY BRY WBD FULT COHR GNW
checksum   0ef2bb56f1affa202b5db33b28b9afb6ebeb78ac28655231bb570ba92d7a6732
```

`regenerate_d5_d1_sample()` re-derives the literal from the package universe, so the list is checked
against a fresh computation rather than against a hash of itself.

What it measures:

```
market-cap validity rate, with the UNKNOWN reason for each failure
multi-class detection rate, or UNMEASURED if no detector exists
per-field OK / AMBIGUOUS / MISSING over the 12 canonical fields
duration-family distribution of every resolved duration field - which the D2.1 bundle omits
TTM constructibility per flow field
debt-composition exposure: which tag resolves, and whether a higher-priority total was skipped
cash-versus-debt period alignment, and balance-sheet staleness in days
D&A and interest-expense tag availability
per-method applicability counts under METHOD_SPECS
price-panel coverage at the chosen decision time
```

What it may not do:

```
no realized return and no forward return is read, at any point, for any purpose
no issuer is selected or excluded by its D3 or D4 outcome
no multiple is published as a finding, and no valuation of any issuer is published
no live model call is made: the pilot is a data measurement and costs $0
```

---

## Q. Gates

Eight, coarse on purpose, all core.

```
V1  PIT completeness        == 0 fields used without source, period end, family, acceptance time
V2  period/unit consistency == 0 incompatible pairs computed
V3  market-cap validity     == 0 caps from stale, ambiguous, multi-class or adjusted inputs
V4  EV validity             == 0 EV values published while the debt contract is NOT_IMPLEMENTED
V5  method applicability    >= 1 applicable method per COMPLETE/PARTIAL candidate, else NOT_READY
V6  no fabricated estimate  == 0 consensus, forward or model-supplied multiples
V7  deterministic arithmetic== 0 mismatches on recomputation from stored inputs
V8  TP provenance           == 0 target prices without metric and observed multiple
```

These are not subdivided further. D5 does not repeat D4's pattern of growing a validator one
micro-rule per finding: a finding that is not an arithmetic, period or provenance error is reported
as a limitation, not converted into a gate.

---

## R. Limitations

**Only one method is applicable, and it is the weakest one.** P/B is applicable because equity and
shares resolve OK, not because book value is the right anchor for these businesses. Under
`MIN_METHODS_FOR_COMPLETE` a P/B-only candidate cannot reach COMPLETE, so in its current state the
contract would return PARTIAL or NOT_READY for every candidate. That is the contract working, and it
is also the reason the next step is a data measurement.

**The ten-issuer tables measure a mechanism, not a prevalence.** D1.1's 41% is the population
figure; everything in §D and §F beyond that is n=10.

**My own period-family windows are untested against real fiscal calendars.** The spans are chosen to
accommodate 52/53-week filers and the UNKNOWN default is conservative, but no stored fact set has
been run through them. D5-D1's duration-family distribution is the first measurement of that.

**Multi-class remains undetected**, so a multi-class issuer currently produces a market cap rather
than an UNKNOWN. This is the one gap that can produce a wrong number instead of a refusal, and it
conditions every method.

**C1 and R3 are carried forward unresolved**, exactly as D4 left them, and are not quietly closed
here. C1 has had a zero denominator for three graded runs; R3's exposure is one occurrence and it is
wired to no gate.

**A documentation finding about this project's own detector.** Writing this contract tripped E7R's
decision-token family twice: once on a verbatim quote of D0's "APPROVE preconditions", and once on a
prohibition list whose negation lived in the constant's name rather than in its text. The first is
accepted - rewording a quotation to satisfy a detector would misquote D0 - and the second was fixed
by making every prohibition state its own denial. That is the same lexical-hit-read-as-assertion
defect D4-BR and E7R both addressed, appearing a third time, in documentation. E7 grades model
output, and a contract constant is not one; the gate was not weakened to accommodate either case.

**No DCF.** The contract reserves DCF arithmetic as code-owned and v1 implements no DCF. With no
reliable EBITDA, no interest expense, no tax rate and a two-year price history, a discounted
cash-flow model would be almost entirely assumption, and its output would carry a precision nothing
in the data supports.

---

## S. Verdict

```
H-V2-D5-D0
= READY FOR VALUATION DATA PILOT
```

The four contracts the brief asked to freeze are frozen before any result exists: the PIT data
contract (§D), the period/comparability contract (§E), the valuation method contract (§G), and the
target-price mechanics contract (§K/§L). D0 §AA.5's open question is answered with a method-by-method
status and a named blocker for each.

Read honestly, this step's main product is a refusal with a reason attached. One method of twelve has
its inputs today; the other eleven are blocked by four named, measured data defects, and this
document does not authorize repairing any of them. What it authorizes is measuring how far they
reach.

---

## T. D5 Authorization Status

```
D5-D1 Valuation Data Feasibility Pilot   = PROPOSED, awaiting user authorization
D5 valuation execution                   = NOT AUTHORIZED
D6 Decision                              = does not exist
```

---

## Final Declaration

```
live Opus investment calls?          NO    0
live cost?                           $0
valuation results generated?         NO
fair value generated?                NO
TP1/TP2 generated?                   NO
BUY/SELL?                            NO
APPROVE/WATCH/REJECT?                NO
entry / exit / position sizing?      NO
forward returns?                     NO
post-hoc multiple tuning?            NO
target price fitted to returns?      NO
D4 historical results modified?      NO
C1/R3 limitations preserved?         YES
D4 banned-field list changed?        NO
D5-D1 executed?                      NO
existing unrelated dirty modified?   NO
push?                                NO
```
