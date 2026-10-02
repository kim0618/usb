# H-V2-D5-P0.1 - TTM Fundamental Construction

```
H-V2-D5-P0.1
= READY FOR DEBT / EV PRIMITIVE

wrong-period TTM components     0
future components               0
annualization guesses           0
cash-flow YTD handling          PASS
deterministic                   same input -> same result, verified under input reordering
live Opus investment calls      0
live cost                       $0.00
valuation results               none
fair value / TP1 / TP2          none generated
historical H verdicts           unmodified
D5-D1                           NOT executed
push                            NO
```

P0 ended `READY FOR TTM CONSTRUCTION` with one blocker named above all others: "every multiple needs
a TTM or annual denominator and no canonical field is TTM". This step builds that denominator. It
builds no multiple with it.

---

## A. Purpose

Construct PIT-safe trailing-twelve-month fundamentals, deterministically, from facts the filings
actually contain - and refuse, by name, everywhere they do not.

The step produces no valuation multiple, no fair value, no target price, no EBITDA value and no
decision. `test_d5_p0_1_ttm_construction.py::TestLayerBoundary` asserts that against the source: the
two new modules contain no `target_price`, `fair_value`, `call_opus`, `tp1`, `tp2`,
`forward_return` or `realized_return`.

---

## B. P0 Handoff

What P0 left, and what each piece turned out to be for:

| P0 left | used here as |
|---|---|
| `DurationFamily` / `classify_duration` | the only period authority; no second parser was written |
| `resolve_fact(..., duration_family=)` | every single component goes through it |
| `resolve_period_aligned` | the cross-field alignment for free cash flow - its first consumer |
| day-count windows owned by `facts.py` | `ANNUAL_SPAN_DAYS` confirms a constructed period is a year |
| `fact.duration_days` on the bundle | the span arithmetic and the provenance record |
| §L.1 "TTM does not exist" | closed by this step |
| §L.2 "alignment available but unused" | closed by this step |
| §L.5 "D&A is not canonical" | closed for the valuation layer, §H |
| §L.3/L.4 debt composition, staleness | left for D5-P1, §N |

Two P0 measurements drove the design rather than decorating it:

1. **Cash-flow statements report no discrete second quarter**, on all ten issuers. A `QUARTER`
   request for `operating_cash_flow` lands a quarter earlier than one for `revenue`.
2. **A 10-K reports the fiscal year and its own discrete fourth quarter under one `end`.** P0
   separated them. Whether a filer reports the Q4 at all turns out to decide which construction is
   available.

---

## C. TTM Contract

Three constructions, in the order they are tried at any one period end:

```
REPORTED_FISCAL_YEAR                 a fiscal year that is itself the trailing twelve months
FOUR_DISCRETE_QUARTERS               Q(t) + Q(t-1) + Q(t-2) + Q(t-3)
FY_PLUS_CURRENT_YTD_MINUS_PRIOR_YTD  prior FY + current YTD - prior comparable YTD
```

The ordering is by how much arithmetic each performs on the filer's own figures: none, a sum of four
non-overlapping reported periods, and a difference whose correctness additionally requires the three
figures to be stated on one basis - which no filing asserts and this module cannot check. Period ends
are tried newest first, so the ordering only decides between constructions that reach the same end.

### Status, fail-closed

```
OK                       constructed
MISSING_COMPONENT        a component does not exist, or is not known at the decision time
PERIOD_MISMATCH          components exist and do not compose into a year
UNIT_MISMATCH            components are in different units
TAG_MISMATCH             components come from different XBRL tags
SIGN_CONVENTION_MISMATCH a declared cash_outflow_positive field totalled negative
AMBIGUOUS_COMPONENT      a component resolved AMBIGUOUS
STALE_PERIOD             a construction exists and its twelve months ended too long ago
NOT_APPLICABLE           the construction is not defined for the field (see §G)
```

`value` is `None` for every status except `OK`. No `UNKNOWN` is replaced by zero. Two statuses are
additions to the brief's list, each because the data demanded one: `TAG_MISMATCH` (§H) and
`SIGN_CONVENTION_MISMATCH` (§I). `STALE_PERIOD` is a third, and §C.1 is the reason it exists.

### C.1 The staleness bound, and the defect that produced it

The first measurement of this step reported **TTM diluted EPS 8 of 10 and TTM D&A 9 of 10**. Both
numbers were false, and false in the exact way P0 was written to end.

Searching period ends newest-first and taking the first that constructs will walk back years when
recent filings do not support a construction. At a `2026-09-28` decision time it produced:

```
DORM  depreciation_amortization  OK   2013-09-29 .. 2014-09-27
COLL  depreciation_amortization  OK   2019-10-01 .. 2020-09-30
VRRM  eps_diluted                OK   2020-10-01 .. 2021-09-30
IDCC  eps_diluted                OK   2020-10-01 .. 2021-09-30
FRPT  eps_diluted                OK   2020-10-01 .. 2021-09-30
CRK   eps_diluted                OK   2019-10-01 .. 2020-09-30
SPSC  eps_diluted                OK   2019-10-01 .. 2020-09-30
TG    eps_diluted                OK   2024-10-01 .. 2025-09-30
DORM  eps_diluted                OK   2019-09-29 .. 2020-09-26
```

Each is internally consistent and each is a correct trailing twelve months - of the wrong twelve
months. A P/E formed from DORM's 2020 EPS against a 2026 price is not a near miss; it is P0's silent
partial-year defect with a larger error and the same shape. So:

```
MAX_TTM_PERIOD_AGE_DAYS = h0_5.MAX_SHARES_STALENESS_DAYS = 135
```

reused rather than invented. It is the only validated staleness bound in this repository, it answers
the identical question - how far may a fact's period end lag the decision date before it stops
describing the present - and it is applied the identical way, `(decision_date - end).days > bound`.
D5-D0 recorded the asymmetry that H0.5 bounded `shares_outstanding` and nothing bounded anything
else; this bounds the constructed flows. `cash` and `total_debt` stay with D5-P1, §N.

The bound applies to a pinned `period_end` too. A caller wanting a trailing year that ended in 2014
is asking a historical question this layer does not answer, and raising `max_period_age_days`
explicitly is how to ask it.

How much room the bound actually leaves, measured rather than assumed - the oldest period end that
still resolved OK, per corpus:

```
D4 ten         DORM 93 days,  then 90 for most                 bound 135
D5-D1 twelve   ADBE 122 days, then 90 for most                 bound 135
```

ADBE at 122 of 135 is the tightest case and worth naming for D5-P1: a November fiscal-year filer
measured in late September sits two thirds of the way through its gap between the Q3 10-Q and the
10-K. A filer that files late, or a decision date a fortnight further on, would be refused as stale
while holding the newest figure that exists. That is the correct fail-closed direction, and it means
`STALE_PERIOD` will sometimes mean "the filer has not reported recently" rather than "the data is
unusable" - a distinction the pilot should report rather than aggregate away.

---

## D. Discrete-quarter Path

Walk back from the period end, taking the quarter whose `end` is the day before the last one began.
`report_end` is pinned to that exact date at every step, so **adjacency is exact by construction
rather than by tolerance**: the four periods cannot overlap and cannot leave a gap. Then the total
span is checked against `ANNUAL_SPAN_DAYS` - four quarters are not automatically a year, and a test
builds four genuine quarter-length periods totalling 283 days to prove the check is load-bearing.

Measured: **the chain closes for one issuer of ten.** COLL's 10-K reports its own discrete fourth
quarter, so COLL has Q2-26, Q1-26, Q4-25 and Q3-25. For the other nine the chain breaks after two,
because their 10-K reports the fiscal year and not its fourth quarter:

```
AEYE  only 1 of 4 adjacent discrete quarters back from 2026-06-30; none ends 2026-03-31
FG    only 2 of 4 adjacent discrete quarters back from 2026-06-30; none ends 2025-12-31
```

So the quarter chain is not the main path on this corpus. It is the most direct one where it exists,
and for diluted EPS it is one of only two permitted at all (§G).

---

## E. YTD-difference Path

The arithmetic is exact, not approximate, and two equalities are the whole proof. Given a prior
fiscal year `FY`, a current year to date `YTDc` and a prior year to date `YTDp`:

```
require  YTDc.start == FY.end + 1 day      the current YTD accumulates from the fiscal year that
                                           begins the day the prior one ended
require  YTDp.start == FY.start            the prior YTD accumulates from the prior FY's own start

then     FY - YTDp  = [YTDp.end + 1, FY.end]
         + YTDc     = [FY.end + 1,   YTDc.end]
         -----------------------------------------
         = exactly    [YTDp.end + 1, YTDc.end]
```

The constructed period is therefore **known**, not estimated from day counts. The prior fiscal year
is not searched for - it is pinned to `YTDc.start - 1 day`. The prior year to date is not "the
nearest thing about a year back" - it must accumulate from `FY.start`, in the same family, under the
same tag and unit. More than one candidate is `AMBIGUOUS_COMPONENT`, never a choice.

### Comparability, on existing tolerances

§7 forbids inventing one, so these are frozen H-PV2's, lifted from `h_pv2.comparable_pair` into
`facts.py` for the same reason P0 moved the span windows there - a second definition of "one year
apart" would recreate exactly the drift P0 removed. `h_pv2.py` keeps its inline literals and stays
frozen, and a test asserts the numbers still agree with its source text:

```
YEAR_APART_DAYS                     (345, 385)
COMPARABLE_DURATION_TOLERANCE_DAYS  7
COMPARABLE_FY_DURATION_TOLERANCE_DAYS  15
```

One measured property is worth recording rather than leaving as a hypothetical: **for a six-month
year to date the exact anchors are strictly stronger than the tolerance.** Once `YTDp` must start on
`FY.start` and its span must sit in the frozen 160-200 day window, the two ends cannot fall outside
345-385 days apart. The tolerance is reachable only for the nine-month family, where a 299-day
"nine months" against a 272-day one ends 338 days apart and is refused. Both cases are tested.

---

## F. Cash Flow Treatment

P0 measured that cash-flow statements report Q1 discretely and then switch to year to date. The
consequence, measured here:

```
operating_cash_flow   10 / 10 OK   all via FY_PLUS_CURRENT_YTD_MINUS_PRIOR_YTD
capex                  8 / 10 OK   all via FY_PLUS_CURRENT_YTD_MINUS_PRIOR_YTD
free_cash_flow         8 / 10 OK   all via FY_PLUS_CURRENT_YTD_MINUS_PRIOR_YTD
```

No `QUARTER` request is forced anywhere near them: the quarter chain breaks after one component on
all ten, and the code does not try to rescue it. AEYE's known case, which P0 left labelled `YTD_Q2`
and unusable as a denominator, now has a trailing year:

```
AEYE  TTM operating cash flow = 4,753,000 + 2,277,000 - 1,171,000 = 5,859,000
      over 2025-07-01 .. 2026-06-30, 364 days
            FY 2025        current 6M      prior 6M
```

`resolve_period_aligned` is used for free cash flow and is this step's reason it exists (§I).

---

## G. EPS Treatment

§12 asked for an audit of whether the YTD-difference path is safe for diluted EPS. It is not, and the
audit is measurement rather than assertion. Diluted EPS is a ratio whose denominator is a weighted
average share count over the period reported, so it is not additive across periods:

```
COLL  fiscal 2025   reported diluted EPS  1.73    sum of its four discrete quarters  1.71
COLL  fiscal 2024   reported diluted EPS  1.86    sum of its four discrete quarters  1.86
TG    fiscal 2024   reported diluted EPS -1.88    sum of its four discrete quarters -1.87
```

The first and third are the non-additivity itself - a cent or two, from each quarter carrying its own
weighted share count and its own rounding. The consequence is the decisive figure, on the one issuer
where both constructions reach the same period end:

```
COLL  TTM diluted EPS to 2026-06-30
      four discrete quarters                  -0.46 + 0.40 + 0.46 + 0.84  =  1.24
      prior FY + current YTD - prior YTD       1.73 + (-0.02) - 0.44      =  1.27
      disagreement                                                           0.03  (2.4%)
```

So:

- **Permitted**: `FOUR_DISCRETE_QUARTERS`, because every term is a reported diluted EPS for a period
  inside the trailing year and the residual is bounded by what the filer rounded.
- **Permitted**: `REPORTED_FISCAL_YEAR`, because it is one figure the filer published.
- **Refused**: the YTD difference, as `NOT_APPLICABLE`. Subtracting two differently-weighted
  denominators has no residual derivable from the filing - a mid-year share issuance moves the
  current year to date's weighting and the prior year's not at all, and nothing in companyfacts says
  by how much.
- **Refused outright**: `TTM net income / shares`, §12's absolute, listed in `NEVER_CONSTRUCTED` and
  tested behaviourally - a filer with a constructible TTM net income and a current share count still
  has no TTM EPS.

The price is coverage: **TTM diluted EPS is 1 of 10**, COLL alone. That is the honest number. The
first draft's 8 of 10 was seven stale trailing years and one real one.

---

## H. D&A Canonicalisation

D5-D0 recorded `depreciation_amortization` as `NOT_CANONICAL`: the filings carry it, `FIELD_SPECS`
had no entry. It is now canonical **for the valuation layer only**, in
`valuation/fundamental_fields.py`.

### Why not widen `FIELD_SPECS`

`len(FIELD_SPECS)` is D1's `total_field_count`; the count of fields resolving OK out of it feeds
`eligibility.MIN_RESOLVED_CANONICAL_FIELDS` and E3's research priority, and the whole dict is written
into every D2.1 package as `canonical_field_coverage`. A thirteenth canonical field would move
historical D1/D2 outcomes for borderline securities with nobody asking. §21 forbids that. So
`FIELD_SPECS` is untouched, `extract_companyfacts`/`resolve_fact`/`resolve_period_aligned` take an
optional `specs` registry defaulting to `FIELD_SPECS`, and the valuation layer passes the superset.
One parser, two registries, sharing the same `FieldSpec` objects so they cannot drift on the twelve.
D5-D0's own `NOT_CANONICAL` record is left as written, the way P0 left D0's 0-of-10 figures.

### Tags, investigated rather than guessed

Priority order, and the order matters because these tags **do not agree**:

```
1  DepreciationDepletionAndAmortization        the us-gaap cash-flow-statement D&A line
2  DepreciationAndAmortization
3  DepreciationAmortizationAndAccretionNet     accretion is not amortisation; last resort
```

```
FRPT  six months to 2026-06-30   DepreciationDepletionAndAmortization       47,859,000
                                 DepreciationAndAmortization                49,954,000
DORM  fiscal 2025                DepreciationAndAmortization                33,600,000
                                 DepreciationAmortizationAndAccretionNet    55,732,000
```

Tag 3 stays in the list because for AEYE and DORM it is the only unified tag carrying current periods
at all - AEYE's `DepreciationAndAmortization` stops in 2018, DORM's
`DepreciationDepletionAndAmortization` in 2014 - and `resolve_fact` reaches tag priority only *after*
narrowing to the latest period end, so a tag with no recent data cannot win.

### Duplicate prevention

A unified tag is used as reported and is **never added to another tag**. A filer reporting only
components resolves MISSING. Fourteen component tags are named and excluded, `Depreciation` and
`AmortizationOfIntangibleAssets` among them.

Summing them would be the `total_debt` composition defect in a new costume: the sum is complete only
if those are the only components, and no filing says so. The measured cost is one issuer of ten, and
it is instructive. COLL's unified tag stops in 2020; it now reports only `Depreciation` (2,275,000
for the six months to 2026-06-30) and `AmortizationOfIntangibleAssets` (118,426,000), alongside
`AmortizationOfFinancingCostsAndDiscounts`, `CostOfGoodsAndServicesSoldAmortization` and
`AccretionAmortizationOfDiscountsAndPremiumsInvestments`. Their sum may well be COLL's D&A; nothing
in the filing says which subset is complete. And the one unified tag COLL did once use reported
589,000 for the nine months to 2020-09-30 - depreciation-scale, not
depreciation-plus-amortisation-scale - so for this filer even the unified tag was narrower than its
name.

### TAG_MISMATCH, the new refusal this produced

`FIELD_SPECS` maps several tags to one field and tag priority is applied per period end, so a filer
can state its annual figure under one tag and its interim figures under another. DORM does exactly
that, and its TTM D&A therefore refuses:

```
DORM  depreciation_amortization  TAG_MISMATCH
      components come from different tags
      ['DepreciationAmortizationAndAccretionNet', 'DepreciationAndAmortization']
```

Adding a fiscal year stated under one tag to a year to date stated under another, and subtracting a
third, produces a number no filing contains. The check applies to every field and every
construction, not only to D&A.

---

## I. FCF

```
TTM FCF = TTM OCF - TTM CapEx
```

Code-owned, one subtraction, no sign flip. `capex`'s `FieldSpec.sign` is `cash_outflow_positive` and
every capex value in the stored companyfacts is a positive outflow, so this is byte-for-byte the
convention `change_detection.fcf_trend` already uses - the valuation layer and the change layer
cannot disagree about what free cash flow is. A test locks the signs the components enter with.

A filer using the negative convention would make that subtraction *add* capital expenditure, quietly
and with a plausible-looking result, so the declared convention is **checked** rather than assumed: a
`cash_outflow_positive` field totalling negative refuses as `SIGN_CONVENTION_MISMATCH` instead of
being negated twice.

Both legs are built over one aligned period by `construct_ttm_aligned`, which is
`resolve_period_aligned`'s first production consumer and the thing P0 left it for. The period, method
and unit are re-checked afterwards rather than assumed. Where no current end serves both legs there
is no free cash flow; where an older shared end does, it is used rather than mixing the newest end of
each leg.

---

## J. PIT / Amendment

Nothing here is re-implemented. Every component goes through `resolve_fact`, so:

- `accepted_at <= decision_time`, on exact timestamps. A filing accepted at 20:05 UTC is not
  knowable at that afternoon's close and is knowable the next morning. Tested at both times.
- Amendment versioning is inherited: an amendment known at the decision time supersedes the original
  within its period family; one accepted afterwards does not reach back. Tested in both directions,
  including that every component of a successful construction satisfies
  `accepted_at <= decision_time`.
- Measured over the ten issuers and every constructed field: **future components 0.**

---

## K. Provenance

Every result carries, per §16:

```
field, status, value, unit
construction_method
period_start, period_end, duration_days
decision_time
reason
components[]:  role, sign, fact_id, tag, unit, value,
               start, end, duration_days, duration_family,
               form, accession, acceptance_time
```

`fact_id` is `accession:tag:unit:start:end`. Companyfacts has no row id, so that tuple is the
identity - and it is exactly what `resolve_fact` narrows on, so two facts sharing it are the
duplicate rows the resolver already collapses.

Determinism is verified rather than claimed: the full bundle, constructed twice with the fact list
reversed, produces identical dictionaries for all ten issuers and for the synthetic fixtures.

---

## L. 10-Issuer Coverage

AEYE, COLL, FG, VRRM, IDCC, DORM, FRPT, TG, CRK, SPSC at the stored D2.1 cutoff
`2026-09-28T05:49:37.377541Z`. No price, no return, no multiple, no valuation. Reproduce with
`python -m app.dev.audit_strategy_h_v2_d5_p0_1`.

| field | P0 (point-in-time fact) | TTM here | method split | the gaps, named |
|---|---|---|---|---|
| revenue | 9/10 QUARTER | **9/10** | 1 quarters, 8 YTD | TG: no canonical revenue tag |
| operating_income | 8/10 | **8/10** | 1 quarters, 7 YTD | FG, TG: no `OperatingIncomeLoss` |
| net_income | 10/10 | **10/10** | 1 quarters, 9 YTD | - |
| eps_diluted | 10/10 | **1/10** | 1 quarters | 9: no closed quarter chain; YTD path refused (§G) |
| operating_cash_flow | 10/10 | **10/10** | 10 YTD | - |
| capex | 8/10 | **8/10** | 8 YTD | FG, FRPT: no canonical capex tag |
| D&A | **0/10, not canonical** | **7/10** | 7 YTD | COLL, TG stale; DORM TAG_MISMATCH |
| free_cash_flow | not constructible | **8/10** | 8 YTD | FG, FRPT, via capex |

What P0's blockers became:

- **§L.1 TTM does not exist** - closed. Seven of eight fields construct for most issuers.
- **§L.2 alignment unused** - closed. Free cash flow is its consumer.
- **§L.5 D&A not canonical** - closed for the valuation layer: 0/10 to 7/10, and the three residuals
  are a filer's tagging choices rather than a pipeline gap.

### Cross-check: the two constructions agree where both exist

COLL is the only issuer with both available. They are not asserted equal by construction, so agreeing
to the cent is evidence that the YTD arithmetic is the identity §E claims:

```
COLL, TTM to 2026-06-30      four discrete quarters     FY + YTD - YTD
revenue                           808,208,000             808,208,000
operating_income                  157,438,000             157,438,000
net_income                         47,915,000              47,915,000
```

### EBITDA feasibility, reported and not computed

```
TTM operating income + TTM D&A over one identical period:  feasible for 6 of 10
```

No EBITDA value is produced and no EBITDA multiple is formed; `ebitda_feasible` returns a verdict. A
test asserts the module exports no EBITDA value of any kind. The label `EBITDA_DERIVED` is reserved
for whatever step does construct it, because operating income plus D&A is a definition this
repository chose rather than a disclosed line item, and a company's own "adjusted EBITDA" is a
different number under a different definition.

### The frozen D5-D1 twelve, read-only

Measured **after** the implementation was fixed on the ten, with no rule changed on its evidence - §19
permits coverage measurement and forbids tuning. No valuation, no multiple, no decision.

| field | OK / 12 | note |
|---|---|---|
| revenue | 9 | |
| operating_income | 6 | |
| net_income | 9 | |
| eps_diluted | 1 | via `REPORTED_FISCAL_YEAR` |
| operating_cash_flow | 9 | |
| capex | 5 | |
| depreciation_amortization | 6 | 1 TAG_MISMATCH |
| free_cash_flow | 5 | |

EBITDA candidate feasible for 4 of 12. Future components 0, wrong-period components 0.

Three issuers are `STALE_PERIOD` on every field, and the reason is the local store rather than the
contract: the newest fact of any kind is `2025-06-30` for DALN, `2026-03-31` for FET and
`2025-09-30` for BRY, against a `2026-09-28` decision time. Nothing recent exists to construct from.

**This corpus is what found the missing third construction.** COHR's fiscal year ends in June, its
10-K for `2025-07-01 .. 2026-06-30` was filed 90 days before the decision time, and the first
implementation refused every one of its fields as stale because it looked only for quarters and
year-to-date figures. A fiscal year that recent already *is* a trailing twelve months.
`REPORTED_FISCAL_YEAR` was added for it, and it changes nothing on the ten - whose fiscal years all
ended 2025-12-31, 270 days back and over the bound.

---

## M. Tests

`backend/tests/strategy_h_v2/valuation/test_d5_p0_1_ttm_construction.py`, 68 tests. Data-backed tests
skip when `data/runtime` is absent, as the repository's convention requires. Run from the repo root.

```
four discrete quarters TTM                      covered
a 10-K's own Q4 inside the chain                covered
missing quarter not interpolated                covered
gap between quarters refused                    covered
four quarters that do not span a year           covered (283 days, refused)
FY + current YTD - prior YTD                    covered
Q2 cash-flow 6M YTD                             covered (pure + stored AEYE)
Q3 cash-flow 9M YTD                             covered
YTD_Q1 identity supports the path               covered
missing prior YTD                               covered
missing prior FY                                covered
prior FY must end the day before the YTD        covered
prior YTD must start on the prior FY's start    covered
year-apart tolerance (nine-month family)        covered
duration-drift tolerance                        covered
anchors stricter than tolerance at six months   covered
period mismatch                                 covered
unit mismatch                                   covered
tag mismatch (FRPT shape, and DORM stored)      covered
reported fiscal year used as reported           covered
fiscal year preferred over the quarter chain    covered
fiscal year vs the 10-K's own Q4                covered
stale fiscal year refused                       covered
staleness bound is h0_5's existing number       covered
staleness measured period end to decision date  covered (edge day both sides)
future filing rejection                         covered
after-hours filing rule                         covered (16:00 vs next 09:30)
amendment knowledge-time                        covered (both directions)
CapEx sign convention                           covered (component signs locked)
negative capex refused, not double-negated      covered
TTM FCF arithmetic                              covered
FCF refuses with no shared current end          covered
FCF uses an older shared end                    covered
FCF over a shared fiscal-year end               covered
D&A canonicalisation                            covered
D&A duplicate-prevention                        covered (components extract to nothing)
EPS sums four quarters                           covered
EPS refuses the YTD difference                  covered
EPS never recomputed from net income / shares   covered (behavioural)
deterministic provenance                        covered (reversed input, identical dicts)
period-aligned cash-flow resolution             covered
no new tolerance invented                       covered (asserted against h_pv2 source text)
FIELD_SPECS unchanged                           covered
frozen h0_5/h_pv1/h_pv2/h_pv2c/h_pv3/pilot      covered (git diff empty)
no valuation / price / decision in this layer   covered
stored ten-issuer coverage                      covered (every number in §L)
both constructions agree on COLL                covered
```

Full H regression, from the repo root:

```
backend/tests/strategy_h0 + backend/tests/strategy_h_v2    1437 passed, 1 skipped
whole suite                                                6486 passed, 24 skipped
```

The whole-suite run excludes `backend/tests/strategy_b/test_strategy_b_scanner.py` and
`backend/tests/test_strategy_b_historical_scanner.py`, which fail to import at `HEAD` and are
untracked files belonging to a concurrent session:
`ImportError: cannot import name 'OBSERVATION_ONLY' from 'app.strategy_b.scanner'`. Strategy B was
closed at commit `b6afd95`; the breakage predates this step and nothing here touches it.

---

## N. Remaining Blockers

1. **`total_debt` composition.** Unchanged, §23. `resolve_fact` selects one debt tag by priority and
   never sums, so 7 of 10 issuers resolve to `LongTermDebtCurrent` - the current portion - while the
   same filer also reports `LongTermDebt`, and DORM resolves to 0. Enterprise value stays UNKNOWN.
2. **Instant-field staleness is still unbounded**, §24. COLL resolves `total_debt` OK from a period
   end 2,463 days before the cutoff. The flows are now bounded (§C.1) and `cash`, `total_debt`,
   `assets` and `equity` are not. D5-P1 needs the same contract for them, and `MAX_TTM_PERIOD_AGE_DAYS`
   is the precedent: reuse `MAX_SHARES_STALENESS_DAYS` or state why a balance-sheet instant deserves
   a different number.
3. **TTM diluted EPS is 1 of 10.** Not a defect to repair by loosening §G. A P/E on this corpus will
   be unavailable for nine issuers of ten, and D5-P1 should plan for that rather than discover it.
4. **D&A tag drift has no resolution.** `TAG_MISMATCH` refuses DORM rather than reconciling it.
   Reconciling would need a cross-tag equivalence claim that no filing supports.
5. **Multi-class is wired and undetected.** Unchanged from P0: `market_cap_gate` refuses every issuer
   and no detector exists, so no market cap and therefore no multiple can be formed yet regardless of
   what the denominator layer can now build.
6. **`operating_income` is absent for FG and TG**, so EV/EBIT and the EBITDA candidate are
   structurally unavailable for them whatever the debt work achieves.

---

## O. Verdict

```
H-V2-D5-P0.1 = READY FOR DEBT / EV PRIMITIVE
```

A PIT-safe trailing twelve months exists for seven of eight target fields on most of the corpus, by
three constructions with exact period arithmetic and complete provenance. Nothing is annualised,
interpolated or multiplied by four. The cash-flow year-to-date shape P0 measured is handled by the
construction that fits it rather than by forcing a quarter request. D&A is canonical for the
valuation layer without moving a single historical D1 number.

Coverage was not forced: diluted EPS fell from a false 8 of 10 to a true 1 of 10 when the staleness
bound exposed seven trailing years from 2020 and 2021, and D&A refuses three issuers by name.

---

## P. D5-P1 Proposal

```
H-V2-D5-P1   DEBT / NET DEBT / ENTERPRISE VALUE
```

1. **Compose `total_debt`** instead of selecting one tag. Establish component completeness from the
   filing, or report `BLOCKED_COMPOSITION` per issuer - the same discipline §H applied to D&A, which
   refused rather than summed where completeness was unstated.
2. **Bound instant-field staleness**, reusing `MAX_SHARES_STALENESS_DAYS` or stating why a
   balance-sheet instant differs. Blocker 2 above is the measured case.
3. **Net debt and enterprise value** on top of those two, each UNKNOWN until both land, and both
   gated by `market_cap_gate`, which still refuses every issuer.
4. No valuation multiple, no fair value, no TP1/TP2 and no decision in P1 either.

Only then the frozen 12-issuer `D5-D1` pilot. Its TTM primitive coverage is already measured above,
so the pilot's remaining question is debt, enterprise value and per-method applicability.

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
forward returns read?                NO
return-based tuning?                 NO
debt / EV implementation?            NO    (deferred to D5-P1)
EBITDA value generated?              NO    (feasibility only)
annualization or interpolation?      NO
historical H verdicts modified?      NO
FIELD_SPECS widened?                 NO
h0_5 / h_pv1 / h_pv2 / h_pv2c / h_pv3 / pilot modified?   NO
D5-D1 executed?                      NO    (TTM coverage measured read-only)
existing unrelated dirty modified?   NO
push?                                NO
```
