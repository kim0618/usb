# H-V2-D5-P0 - Fundamental Period / Duration Primitive Repair

```
H-V2-D5-P0
= READY FOR TTM CONSTRUCTION

quarter/YTD ambiguity          resolved, deterministically
wrong-duration silent fallback 0
start/end downstream           preserved
live Opus investment calls     0
live cost                      $0.00
valuation results              none
fair value / TP1 / TP2         none generated
historical H verdicts          unmodified
D5-D1                          NOT executed
push                           NO
```

D5-D0 ended `READY FOR VALUATION DATA PILOT`. Its own coverage audit is the reason this step
exists instead: on the ten issuers D4's live runs touched, **revenue, operating income, net income
and diluted EPS each resolved 0 of 10**. The pilot would have measured a broken primitive.

This step repairs that primitive. It produces no valuation, no fair value and no decision.

---

## A. Why P0 Exists

D5-D1 was preregistered to measure which valuation methods are computable on frozen issuers. A
measurement is only worth running if the thing being measured works. Two defects made that false:

1. **The resolver could not tell a quarter from a year to date.** One 10-Q reports the same line
   item twice from one accession - once for the discrete quarter, once cumulative - and
   `resolve_fact` narrowed by tag, acceptance time and accession but never by period. The two rows
   survived every tiebreak and collided at the last one, so the field reported `AMBIGUOUS`. That
   is truthful and useless.

2. **The opposite failure, which is worse because it is silent.** A field reported *only* as year
   to date has nothing to collide with, so it resolved `OK` - and the bundle carried `end` without
   `start`, so nothing downstream could tell it was a partial year. A price divided by that is a
   price-to-half-year ratio wearing the name of an annual multiple.

The first defect blocks a valuation. The second one produces a wrong valuation that looks right.
P0 fixes the second as deliberately as the first.

---

## B. D5-D0 Evidence, Reproduced From Stored Data

Not a synthetic example. The rows below are the real stored companyfacts for AEYE at the D2.1
cutoff `2026-09-28T05:49:37.377541Z`, which is the exact shape D5-D0 measured:

```
field      status     start        end          days  fp  form  accession              value
revenue    AMBIGUOUS  2026-01-01   2026-06-30   180   Q2  10-Q  0001104659-26-096032   21,269,000
                      2026-04-01   2026-06-30    90   Q2  10-Q  0001104659-26-096032   10,716,000
```

Same tag, same accession, same `end`, **same `fp`**. Only the span differs. That last point drove
the design: fiscal-period metadata alone cannot separate these, because the filer labels both rows
Q2. The collision is identical on COLL, FG, VRRM, IDCC, DORM, FRPT, TG, CRK and SPSC.

The silent case, also measured, is AEYE's `operating_cash_flow`: status `OK`, start 2026-01-01, end
2026-06-30, **180 days**, with no second row to conflict with.

A second collision axis the audit surfaced and D5-D0 had not named: a **10-K carries the full
fiscal year and its own discrete fourth quarter under one `end`** - 1,732 such rows across the same
ten issuers. FY-versus-quarter is the same defect wearing annual clothes.

---

## C. Existing Pipeline, And Where Period Information Dies

```
SEC companyfacts (raw)
  -> extract_companyfacts        facts.py
  -> CanonicalFact               facts.py
  -> resolve_fact                facts.py
  -> _snapshot                   pipeline.py
  -> Evidence Bundle             evidence_bundle.py
  -> D2.1 package JSON           data/runtime/strategy_h_v2/d2_1/
  -> D3 / D4 / D5
```

| field | raw | CanonicalFact | resolve_fact | bundle (before P0) | bundle (after P0) |
|---|---|---|---|---|---|
| `start` | yes | yes | yes | **LOST** | yes |
| `end` | yes | yes | yes | yes | yes |
| `duration_days` | derivable | **absent** | **absent** | **LOST** | yes |
| `duration_family` | derivable | **absent** | **absent** | **LOST** | yes |
| `form` | yes | yes | yes | **LOST** | yes |
| `fiscal_year` | yes | yes | yes | **LOST** | not emitted (see note) |
| `fiscal_period` | yes | yes | yes | **LOST** | not emitted (see note) |
| `accession` | yes | yes | yes | yes | yes |
| `acceptanceDateTime` | submissions join | yes | yes | yes | yes |
| `unit` | yes | yes | yes | **LOST** | yes |
| `tag` | yes | yes | yes | **LOST** | yes |

Note: `fiscal_year`/`fiscal_period` are consumed by the classifier and summarised into
`duration_family`. They are deliberately not re-emitted raw, because a consumer that reads `fp`
itself would re-derive the period by the rule this step exists to replace.

---

## D. Duration Ambiguity

The two ambiguities are structurally different and need different answers.

- **Quarter vs YTD within one `fp`** - separable, because the spans differ. Code-owned.
- **Q1 vs Q1-YTD** - *not* separable, because they are the same period. The first quarter of a
  fiscal year and the first-quarter year to date are one fact. D5-D0 recorded this as
  `YTD_Q1_INDISTINGUISHABLE` and treated it as an open problem; P0 treats it as an identity, which
  is what it is, and encodes it as one.

---

## E. Duration Family Contract

`facts.DurationFamily`, code-owned, total, and never inferred by a model:

```
INSTANT           no start
QUARTER           discrete ~3 months, including a 10-K's own Q4
YTD_Q1            request-only; the Q1 identity above
YTD_Q2            ~6 months from the fiscal year start
YTD_Q3            ~9 months
FY                a full fiscal year
OTHER_DURATION    coherent, matches no window - a transition stub, a multi-year span
UNKNOWN           unclassifiable: no end, non-positive span, or contradictory form/fp
```

`OTHER_DURATION` and `UNKNOWN` are distinct on purpose. The first means "we classified it and it is
not a period a multiple may use". The second means "we could not classify it". Collapsing them
would hide metadata corruption behind a benign-looking label.

### Classification inputs

`start`, `end`, `duration_days`, `form` and `fiscal_period`. Not day count alone - §6 of the brief
forbids "90 days means quarter", and the measured data shows why: `fp=Q2` appears on both a 90-day
and a 180-day row, while `fp=FY` appears on both a 365-day and a 90-day row. The span resolves what
`fp` cannot, and `form`/`fp` consistency rejects what the span cannot.

One rule is deliberately strict: **a YTD family is never asserted without `fp`.** `QUARTER` and
`FY` are determined by the span alone, but `YTD_Q2`/`YTD_Q3` additionally claim *which* quarter the
figure accumulates to, and only the fiscal-period metadata supports that claim. Without it a
six-month span is `OTHER_DURATION`. This costs almost nothing in practice - `fp` is absent on 72 of
10,361 duration facts (0.7%) across the ten issuers.

### Day-count windows - deduplicated

The repository held **three** disagreeing sets of duration tolerances: `h_pv2.period_family`,
`h_pv3.DISCRETE`/`YTD`, and `d5_d0_contract.QUARTER_DAYS` et al. P0 adopts the frozen H-PV2/H-PV3
numbers, which are the ones already validated in a scored run, and makes `facts.py` their single
owner:

```
QUARTER_SPAN_DAYS     (70, 110)
SEMI_SPAN_DAYS        (160, 200)
NINE_MONTH_SPAN_DAYS  (250, 299)
ANNUAL_SPAN_DAYS      (300, 400)
```

`d5_d0_contract` now re-exports these instead of defining its own narrower copies, and a test
asserts the windows are pairwise disjoint, so no span can belong to two families. Nine-month closes
at 299 rather than PV2's 300 precisely to keep that disjointness; no observed fact has a 300-day
span. `h_pv2.py` and `h_pv3.py` are **frozen and untouched** - they keep their inline copies, which
now agree numerically with the owner rather than contradicting it.

---

## F. Resolver Contract

```python
resolve_fact(facts, field, decision_time, *, report_end=None, duration_family=None)
```

- `duration_family=None` preserves pre-P0 behaviour **exactly**, including reporting the collision
  as `AMBIGUOUS`. D1/D2/D3/D4 are unaffected until they opt in.
- A requested family filters **before** any other narrowing. Order matters: filtering after the
  latest-`end` step would let a quarter request pick a period end that only a YTD row reports, then
  report `MISSING` while an older discrete quarter sat available. Tested.
- `UNKNOWN` raises rather than being requestable - it is a classification outcome, not a period.
- `Resolution` now carries `requested_duration_family` and a `duration_family` property, and an
  ambiguity reason now names the colliding families.

### Period alignment across fields

The resolver guarantees the *family* of the fact it returns. It does not guarantee that two fields
resolved independently describe the *same period*, and measurement shows they routinely do not:

> On **all ten** issuers, cash-flow statements report no discrete second quarter. A `QUARTER`
> request for `operating_cash_flow` therefore resolves to the **Q1** fact ending 2026-03-31, while
> `revenue` resolves to the Q2 fact ending 2026-06-30. Both are genuine quarters.

Subtracting capex of one period from cash flow of another produces a number that is not a ratio of
anything, so `resolve_period_aligned(facts, fields, cutoff, family)` returns the newest period end
at which *every* requested field resolves in the requested family, and `None` otherwise. It adds no
selection rule of its own and never relaxes the end or the family to find a match.

---

## G. No Silent Fallback

If the requested family is absent, the result is `MISSING`, with a reason naming the family. A fact
of another family is never substituted. Instant/duration mixing is impossible by construction,
because `INSTANT` is itself a family.

```
request = QUARTER, available = 180d YTD only   ->  MISSING   (never the 180-day number)
request = YTD_Q2,  available = 90d quarter only ->  MISSING
request = FY,      available = 10-K Q4 only     ->  MISSING
```

Swept over 10 issuers x 12 canonical fields x 7 requestable families, **every** fact returned
matched the family requested. Zero exceptions.

---

## H. 10-Issuer Offline Replay

AEYE, COLL, FG, VRRM, IDCC, DORM, FRPT, TG, CRK, SPSC at the stored D2.1 cutoff. No valuation, no
returns, no performance figure - coverage only.

| field | before (unnarrowed) | QUARTER | YTD_Q2 | FY | notes |
|---|---|---|---|---|---|
| revenue | **0** / 10 | 9 | 9 | 9 | TG reports no canonical revenue tag |
| operating_income | **0** / 10 | 8 | 8 | 8 | FG, TG report no `OperatingIncomeLoss` |
| net_income | **0** / 10 | **10** | 10 | 10 | |
| eps_diluted | **0** / 10 | **10** | 10 | 10 | |
| operating_cash_flow | 10 / 10 | 10 | 10 | 10 | was silently YTD; now labelled |
| capex | 8 / 10 | 8 | 8 | 8 | FG, FRPT report no canonical capex tag |
| D&A | **not canonical** | - | - | - | see below |

Every remaining gap is a **missing tag**, not a period defect. The residual 1-2 per field are
issuers that do not report the canonical tag at all.

**D&A is unchanged and still 0 of 10 resolvable.** All ten issuers *do* carry a D&A tag
(`DepreciationDepletionAndAmortization`, `DepreciationAndAmortization` or
`DepreciationAmortizationAndAccretionNet`) and the classifier families them correctly, but
`FIELD_SPECS` has no `depreciation_amortization` entry, so `resolve_fact` cannot see it. That is
D5-D0's `NOT_CANONICAL` finding, a canonical-coverage gap rather than a period one, and P0 does not
widen `FIELD_SPECS`. TG reports D&A only annually.

Duration-family distribution over every eligible fact (10 issuers):

```
INSTANT          6,159
QUARTER          5,435
FY               2,009
YTD_Q2           1,438
YTD_Q3           1,332
OTHER_DURATION     147
UNKNOWN              0
```

`UNKNOWN` is zero on real data: no stored fact has contradictory form/fiscal-period metadata. The
147 `OTHER_DURATION` are genuine odd spans (transition periods, multi-year comparatives).

---

## I. Backward Compatibility

Historical verdicts are **not** recomputed and **not** modified. The comparison below is an offline
audit of the old unnarrowed call against the new narrowed call, over 10 issuers x 12 canonical
fields = 120 resolutions:

```
UNCHANGED                60
AMBIGUITY_CORRECTED      42
PREVIOUSLY_SILENT_YTD    18
NEW_UNKNOWN               0
```

- `AMBIGUITY_CORRECTED` (42): previously `AMBIGUOUS`, now resolves in a named family.
- `PREVIOUSLY_SILENT_YTD` (18): previously `OK` while actually a partial-year figure - the
  `operating_cash_flow` and `capex` masquerade. The value was never wrong; the *label* was absent.
- `NEW_UNKNOWN` (0): the repair takes nothing away.

Existing PIT rules are preserved and tested under narrowing: acceptance-time gating, after-hours
availability, and amendment versioning all behave identically within a family. `h0_5.py` is
byte-identical, asserted by a test, so the frozen H-PV1/PV2/PV2C/PV3 runs are unchanged.

---

## J. Multi-Class Wiring

D5-D0 recorded that `resolve_pit_shares(multiple_share_classes=True)` fails closed but that its
only `True` caller is a test. The audit confirms this and adds the reason it matters: the parameter
is a boolean **defaulting to the permissive answer**, so every production caller silently asserts
"single class" when it has in fact asserted nothing.

Wiring it into the existing callers was rejected: they are the frozen PV dev runners, and changing
them would modify historical H results, which this phase forbids. There is no other production
market-cap caller, because the valuation layer does not exist yet.

So P0 adds the valuation-facing entry point itself, fail-closed:
`valuation/market_cap_gate.py`. `share_class_state` is a required argument with **no default**, and
only a positive `SINGLE_CLASS` determination proceeds:

```
MULTIPLE_CLASSES -> market cap UNKNOWN -> valuation NOT_READY
UNRESOLVED       -> market cap UNKNOWN -> valuation NOT_READY
```

**No detector is added.** `SINGLE_CLASS` is therefore unobtainable today and this gate returns
UNKNOWN for all ten issuers. That is the intended result: a refusal, not coverage. `h0_5` is
unchanged.

---

## K. Tests

`backend/tests/strategy_h_v2/valuation/test_d5_p0_period_primitive.py`, 49 tests, all passing.
Data-backed tests skip when `data/runtime` is absent, as the repository's convention requires.

```
quarter vs YTD in one accession            covered (pure + 10 stored issuers)
quarter request rejects YTD                covered
YTD request rejects quarter                covered
FY vs a 10-K's own Q4                      covered
instant classification                     covered
missing start / missing end                covered
non-positive span                          covered
ambiguous duration -> OTHER_DURATION       covered
contradictory form/fp -> UNKNOWN           covered
windows pairwise disjoint                  covered
YTD_Q1 identity                            covered
narrowing precedes latest-end selection    covered
after-hours PIT availability unchanged     covered
amendment versioning unchanged             covered
D2.1 start/end preservation                covered
known AEYE 180-day OCF case                covered
no fact outside the requested family       covered (full sweep, 0 violations)
period alignment refuses mixed ends        covered
multi-class fail-closed wiring             covered
h0_5.py byte-identical                     covered
```

Full H regression: `backend/tests/strategy_h0` + `backend/tests/strategy_h_v2`.

---

## L. Remaining Blockers

1. **TTM does not exist.** Every multiple needs a TTM or annual denominator and no canonical field
   is TTM. P0 deliberately did not build it (§15 of the brief). This is now the only thing between
   the repaired primitive and a P/FCF or P/E numerator.
2. **Cross-field period alignment is available but unused.** `resolve_period_aligned` exists and is
   tested; no caller uses it yet. TTM construction is its first consumer.
3. **`total_debt` composition.** Unchanged, out of scope (§14). EV remains UNKNOWN.
4. **Balance-sheet staleness is still unbounded.** COLL resolves `total_debt` from a period end
   2,463 days before the cutoff with status `OK`. P0 did not address this; it is not a duration
   defect but it is an adjacent silent one.
5. **D&A is not canonical.** All ten issuers report it; `FIELD_SPECS` cannot see it.
6. **Multi-class is wired but undetected.** The gate refuses; nothing can yet satisfy it.
7. **`fiscal_year_start` is still not carried** through the bundle, so the coarse
   `d5_d0_contract.period_family` keeps its Q1 caveat. The fact-level primitive does not need it.

---

## M. Next Step

```
D5-P0.1   TTM CONSTRUCTION
```

Deterministic construction of TTM revenue, operating income, net income, EPS, OCF, capex and FCF -
from four non-overlapping `QUARTER` facts, or from an `FY` fact plus the difference of two YTD facts
sharing one fiscal-year start. Interpolating a missing quarter, annualizing a partial year and
multiplying a quarter by four all remain forbidden. `resolve_period_aligned` is the alignment
primitive it should build on.

Then `D5-P1` (debt composition / net debt / EV, plus the staleness bound), and only then the frozen
12-issuer `D5-D1` valuation data pilot.

---

## N. Verdict

```
H-V2-D5-P0 = READY FOR TTM CONSTRUCTION
```

The quarter/YTD ambiguity is deterministically separated, wrong-duration silent fallback is zero on
a full sweep, period provenance survives to the bundle, and the known YTD-only case no longer
passes as a quarter. Coverage was not forced to 100%: every residual gap is a missing tag, named.

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
return-based tuning?                 NO
debt composition redesign?           NO    (deferred to D5-P1)
TTM construction?                    NO    (deferred to D5-P0.1)
new multi-class inference logic?     NO
historical H verdicts modified?      NO
h0_5 / h_pv2 / h_pv3 modified?       NO
D5-D1 executed?                      NO
existing unrelated dirty modified?   NO
push?                                NO
```
