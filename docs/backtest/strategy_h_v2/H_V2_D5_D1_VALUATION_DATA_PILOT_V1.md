# H-V2-D5-D1 - Valuation Data / Method Applicability Pilot

```
STEP          H-V2-D5-D1
VERDICT       READY WITH LIMITATIONS
D5-D2         AUTHORIZED
SAMPLE        12, frozen at D5-D0, checksum verified
MODEL CALLS   0
COST          $0
PUSH          NO
```

| | |
|---|---|
| decision date | 2026-09-16 |
| sample | ADBE DALN CHRS FET NATR STAA CHWY BRY WBD FULT COHR GNW |
| checksum | `0ef2bb56f1affa202b5db33b28b9afb6ebeb78ac28655231bb570ba92d7a6732`, recomputed and matching |
| READY / READY_WITH_LIMITATIONS / NOT_READY | 4 / 3 / 5 |
| silent wrong values | 0 |
| stale inputs used | 0 |
| future inputs used | 0 |
| manual arithmetic defects | 0 |
| fair value, TP1, TP2 | none generated |

---

## A. Purpose

Everything in D5 before this step answered *does the input exist*. This step is the first that
divides one number by another, and it asks the only question that decides whether a valuation layer
is worth building:

> can Strategy H form at least one trustworthy valuation multiple for a real company?

It publishes current observed multiples and nothing else. A current multiple is what the market is
paying today against a fundamental the filer has already reported; it is not a fair value, not a
target multiple and not a judgement that an issuer is cheap. Those begin at D5-D2.

The result is that **the machinery is correct and the coverage is thin**. Seven of twelve issuers
produce at least one multiple, four produce two or more, and not one published number failed any
correctness check. The two coverage clauses of the pre-registered gate both missed. Both findings
are below, and neither is softened.

---

## B. Frozen Sample

The twelve are D5-D0's, unchanged: a seeded hash over the D2.1 package universe, chosen before any
valuation data was measured and blind to every D3 and D4 outcome. `regenerate_d5_d1_sample()`
re-derives the list from the packages and `d5_d1_checksum` recomputes the digest, which matched on
this run. No issuer was added, removed or substituted.

This matters more here than it did at D5-D0. A pilot that reports 7 of 12 is a pilot whose headline
number could have been 10 of 12 by dropping three issuers, so the sample being fixed in advance - and
verified against a fresh computation rather than against a stored hash of itself - is what makes the
coverage figure a measurement instead of a choice.

---

## C. Primitive Inputs

Every input is P0/P0.1/P1/P1.1's primitive, called unchanged. This step adds no resolver, no tag, no
staleness rule and no fallback. The input half of each row is bit-identical to what the P1.1 audit
reports for the same twelve, which is deliberate: if a multiple is wrong, the division is the only
new place it can be wrong.

```
price, PIT shares, market cap, share class    P1.1 share_class.resolve_market_cap
cash, total debt, net debt, enterprise value  P1  capital_structure + P1.1 reopened_enterprise_value
book equity                                   P1  resolve_valuation_instant  (135-day bound)
TTM revenue/OI/NI/EPS/OCF/CapEx/FCF/D&A       P0.1 construct_ttm_bundle      (135-day bound)
derived EBITDA                                D5-D1, this step               (see §D)
```

Four representative issuers, with every figure as resolved:

| | ADBE | NATR | WBD | COHR |
|---|---|---|---|---|
| price (2026-09-16) | 250.50 | 13.09 | 28.07 | 289.93 |
| PIT shares | 397,500,000 @ 2026-06-11 (97d) | 17,595,520 @ 2026-07-24 (54d) | 2,510,703,314 @ 2026-07-23 (55d) | 195,832,246 @ 2026-08-10 (37d) |
| market cap | 99,573,750,000 | 230,325,356.80 | 70,475,442,023.98 | 56,777,643,082.78 |
| cash | 4,919,000,000 @ 2026-05-29 | 82,503,000 @ 2026-06-30 | 3,369,000,000 @ 2026-06-30 | 1,162,018,000 @ 2026-06-30 |
| total debt | 4,802,000,000 REPORTED_TOTAL | **STALE** (2024-03-31, 899d) | 32,023,000,000 REPORTED_TOTAL | 3,222,224,000 REPORTED_TOTAL |
| net debt | -117,000,000 | **MISSING_DEBT** | 28,654,000,000 | 2,060,206,000 |
| enterprise value | 99,456,750,000 | **MISSING_DEBT** | 99,129,442,023.98 | 58,837,849,082.78 |
| book equity | 11,518,000,000 | 169,316,000 | 32,838,000,000 | 10,903,495,000 |
| TTM revenue | 25,198,000,000 | 492,023,000 | 36,115,000,000 | 7,118,181,000 |
| TTM operating income | 9,090,000,000 | 29,323,000 | **-1,272,000,000** | **STALE_PERIOD** (2024-06-30, 820d) |
| TTM net income | 7,229,000,000 | 18,097,000 | -3,167,000,000 | 804,998,000 |
| TTM diluted EPS | **MISSING_COMPONENT** | **MISSING_COMPONENT** | **MISSING_COMPONENT** | 4.12 |
| TTM OCF | 10,481,000,000 | 27,361,000 | 3,423,000,000 | 79,514,000 |
| TTM CapEx | 201,000,000 | 9,276,000 | 1,243,000,000 | 1,102,909,000 |
| TTM FCF | 10,280,000,000 | 18,085,000 | 2,180,000,000 | **-1,023,395,000** |
| TTM D&A | 759,000,000 | 13,369,000 | 5,075,000,000 | 521,895,000 |
| derived EBITDA | 9,849,000,000 | 42,692,000 | 3,803,000,000 | **STALE_INPUT** |

The bolded cells are the refusals, and they are the point of the table. Two of them are defects
D5-D0 measured as passing silently: NATR's balance-sheet staleness, which had no bound at all at
D5-D0 (COLL's debt resolved `OK` from a period end 2,463 days before the cutoff), and the TTM period
staleness that catches COHR's operating income, which P0.1 added and which had no equivalent before.
The EPS refusals are the duration-family and TTM work of P0/P0.1 declining to build a year it cannot
build, rather than annualizing a partial period. WBD's negative operating income and COHR's negative
free cash flow are not defects at all: they are the businesses, refusing a multiple that would
otherwise be negative.

**NATR's debt reads STALE in one column and MISSING in the next, and both are right.** Standalone,
`compose_total_debt` finds NATR's newest borrowing balance at 2024-03-31 and calls it STALE at 899
days. `resolve_net_debt` then asks a different question - what is the debt *at the date the cash is
from* - and at 2026-06-30 NATR reports no borrowing balance of any slot, so the answer is MISSING.
The 899-day-old figure is never netted against 2026 cash. Under D5-D0 it would have been: COLL's
2,463-day debt resolved `OK` there, and that is the defect this now refuses.

**COHR reports revenue, net income, EPS and cash flows for the year to 2026-06-30 and stopped
reporting `OperatingIncomeLoss` in 2024.** Its newest operating income is 820 days old. The income
statement does not resolve as a unit - each field resolves at its own newest workable period - so
COHR's EV/Sales and P/E compute while its EV/EBIT and EV/EBITDA refuse. That asymmetry is correct,
and a step that required one period end across the income statement would have refused all four.

---

## D. Multiple Contracts

Seven methods, in `multiples.MULTIPLE_SPECS`, each defined once:

| method | numerator | denominator | D5-D0 `METHOD_SPECS` |
|---|---|---|---|
| P/B | market cap | book equity, instant | `P/BOOK` |
| P/FCF | market cap | TTM FCF | `P/FCF` |
| EV/Sales | enterprise value | TTM revenue | `EV/SALES` |
| EV/EBIT | enterprise value | TTM operating income | `EV/EBIT` |
| EV/EBITDA | enterprise value | derived EBITDA | `EV/EBITDA` |
| EV/FCF | enterprise value | TTM FCF | **absent** |
| P/E | **price per share** | TTM diluted EPS | `P/E`, differently defined |

Three contract decisions, each of which is a trap rather than a preference.

**A negative denominator is not a cheap multiple.** `denominator_applicable` is D5-D0's frozen rule
and is called rather than reimplemented: a zero, negative or partial-period denominator yields
`NEGATIVE_DENOMINATOR` and no number. The period family it judges is **measured from the
denominator's own dates** by `period_family`, not asserted to be TTM because P0.1 builds
twelve-month windows: hardcoding it would reduce the frozen rule to a sign check and the clause that
refuses a partial year would never fire. A six-month figure arriving in a TTM slot is therefore
refused by the contract rather than by a convention, and an undated flow yields `UNKNOWN`, which is
not a valid denominator family. WBD's operating income is -1,272,000,000, and the alternative
to refusing is publishing an EV/EBIT of **-77.9x**, which reads like the cheapest stock in the sample.
Four such refusals occurred: WBD EV/EBIT, STAA P/FCF, COHR P/FCF and COHR EV/FCF.

**Derived EBITDA is `EBITDA_DERIVED`, and its two legs must cover one identical period.** It is
operating income plus D&A, a definition this repository chose; it is not a reported line item and it
is not any issuer's own "adjusted EBITDA". `ebitda_feasible` is P0.1's verdict and is asked rather
than re-derived, so "may these two be added" has one answer in the repository. WBD is why the method
is worth having separately from EV/EBIT: EBIT -1,272,000,000 plus D&A +5,075,000,000 is EBITDA
+3,803,000,000, so EV/EBIT refuses and EV/EBITDA publishes 26.07x, and both are correct.

**P/E is price over EPS, not market cap over EPS.** `market_cap / eps_diluted` is dollars divided by
dollars-per-share, which is a share count wearing a multiple's name - it would have printed
~13,800,000,000x for COHR. Two consequences are recorded rather than repaired:

- D5-D0's `METHOD_SPECS` defines P/E's denominator as *net income*, and §13 of this brief directs
  that P/E flow through P0.1's TTM EPS contract. So D5-D1's P/E is narrower than D5-D0 described.
  ADBE, NATR and WBD all have TTM **net income** resolving OK while their TTM **EPS** is
  `MISSING_COMPONENT`, so a net-income P/E would have reported 4 of 12 rather than 1 of 12. That
  figure is recorded here and no such multiple was computed: reconstructing EPS to widen P/E is
  forbidden by §13 and by `ttm.NEVER_CONSTRUCTED` before it.
- The gate is stricter than the ratio's own inputs. Price over EPS needs a price and a share-class
  determination and *no share count*, yet P/E is gated on the market cap being OK, which also
  demands PIT shares inside 135 days. That is fail-closed and consistent with P1's
  `method_feasibility`, and it makes P/E coverage a lower bound. Measured cost on this sample:
  `pe_blocked_only_by_shares` = **0 issuers**, because FET and CHWY - the two blocked by shares
  alone - have no resolvable TTM EPS either. The conservatism cost nothing here and is still
  recorded, because on another sample it would not be free.

**EV/FCF has no D5-D0 entry.** D5-D0's twelve candidates include `FCF_YIELD` and no EV/FCF; the
method was introduced by P1's `EV_METHOD_DENOMINATORS` and §2 of this brief names it. It is computed
and the divergence is recorded on the spec (`d0_method = None`). D5-D0's frozen tuple was not edited.

---

## E. Method Coverage

| method | OK | NOT_APPLICABLE | MISSING | median | observed values |
|---|---|---|---|---|---|
| P/B | **7/12** | 0/12 | 5/12 | 3.08x | 0.44, 1.36, 2.15, 3.08, 3.10, 5.21, 8.65 |
| P/FCF | 3/12 | 2/12 | 7/12 | 12.74x | 9.69, 12.74, 32.33 |
| EV/Sales | 3/12 | 0/12 | 9/12 | 3.95x | 2.74, 3.95, 8.27 |
| EV/EBIT | 1/12 | 1/12 | 10/12 | 10.94x | 10.94 |
| EV/EBITDA | 2/12 | 0/12 | 10/12 | 18.08x | 10.10, 26.07 |
| EV/FCF | 2/12 | 1/12 | 9/12 | 27.57x | 9.67, 45.47 |
| P/E | 1/12 | 0/12 | 11/12 | 70.37x | 70.37 |

Why each method is missing where it is. `MISSING` is never bare - every cell carries the status that
produced it:

```
MULTI_CLASS_UNRESOLVED   3 issuers x 7 methods   DALN, BRY (CIK absent from reference), FULT (sibling
                                                 security not identifiable) - market cap fail-closed
MARKET_CAP_UNAVAILABLE   2 issuers x 7 methods   FET, CHWY - PIT shares outside the 135-day bound
MISSING_INPUT            per method              a TTM leg or an EV leg does not resolve
STALE_INPUT              per method              CHRS P/FCF; COHR EV/EBIT and EV/EBITDA
NEGATIVE_DENOMINATOR     4 cells                 WBD EV/EBIT; STAA P/FCF; COHR P/FCF and EV/FCF
```

**Five of twelve issuers produce no multiple at all, and in every case the blocker is the market-cap
numerator rather than anything on the income statement.** Three are share-class refusals and two are
stale PIT shares. Because every one of the seven methods needs either a market cap or an enterprise
value - and EV is market cap plus net debt - a market-cap refusal takes all seven with it. That is
the single highest-leverage fact in this pilot: the valuation layer's coverage ceiling is set by the
share-class resolver and the shares staleness bound, not by the fundamentals.

Only **P/B** clears the 4-of-12 coverage floor. P/E at 1 of 12 and EV/EBITDA at 2 of 12 are reported
as they are; §20 of the brief forbids opening a repair phase for either, and none was opened.

---

## F. Company-Level Results

| issuer | MktCap $m | EV $m | P/B | P/FCF | EV/Sales | EV/EBIT | EV/EBITDA | EV/FCF | P/E | N | readiness |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ADBE | 99,574 | 99,457 | **8.65x** | **9.69x** | **3.95x** | **10.94x** | **10.10x** | **9.67x** | MISSING | 6 | READY |
| DALN | - | - | MULTI_CLASS | MULTI_CLASS | MULTI_CLASS | MULTI_CLASS | MULTI_CLASS | MULTI_CLASS | MULTI_CLASS | 0 | NOT_READY |
| CHRS | 188 | - | **3.10x** | STALE | MISSING | MISSING | MISSING | MISSING | MISSING | 1 | READY_WITH_LIMITATIONS |
| FET | - | - | MC_UNAVAIL | MC_UNAVAIL | MC_UNAVAIL | MC_UNAVAIL | MC_UNAVAIL | MC_UNAVAIL | MC_UNAVAIL | 0 | NOT_READY |
| NATR | 230 | - | **1.36x** | **12.74x** | MISSING | MISSING | MISSING | MISSING | MISSING | 2 | READY |
| STAA | 1,124 | - | **3.08x** | NEG_DENOM | MISSING | MISSING | MISSING | MISSING | MISSING | 1 | READY_WITH_LIMITATIONS |
| CHWY | - | - | MC_UNAVAIL | MC_UNAVAIL | MC_UNAVAIL | MC_UNAVAIL | MC_UNAVAIL | MC_UNAVAIL | MC_UNAVAIL | 0 | NOT_READY |
| BRY | - | - | MULTI_CLASS | MULTI_CLASS | MULTI_CLASS | MULTI_CLASS | MULTI_CLASS | MULTI_CLASS | MULTI_CLASS | 0 | NOT_READY |
| WBD | 70,475 | 99,129 | **2.15x** | **32.33x** | **2.74x** | NEG_DENOM | **26.07x** | **45.47x** | MISSING | 5 | READY |
| FULT | - | - | MULTI_CLASS | MULTI_CLASS | MULTI_CLASS | MULTI_CLASS | MULTI_CLASS | MULTI_CLASS | MULTI_CLASS | 0 | NOT_READY |
| COHR | 56,778 | 58,838 | **5.21x** | NEG_DENOM | **8.27x** | STALE | STALE | NEG_DENOM | **70.37x** | 3 | READY |
| GNW | 3,813 | - | **0.44x** | MISSING | MISSING | MISSING | MISSING | MISSING | MISSING | 1 | READY_WITH_LIMITATIONS |

No `Fair Value`, `TP1` or `TP2` column exists, here or in the code: `MultipleResult` has no field one
could occupy, and a test asserts that no dataclass in the module does.

### Primary and secondary candidates, per the §17 diagnostic

| issuer | methods code will promote | methods code declines to promote | why |
|---|---|---|---|
| ADBE | P/FCF | P/B, EV/Sales, EV/EBIT, EV/EBITDA, EV/FCF | net debt is **0.1%** of EV, so the EV numerator carries nothing the market cap does not |
| WBD | EV/Sales | P/B, P/FCF, EV/EBITDA, EV/FCF | operating margin -3.5%: EV/Sales is the appropriate method and the margin-sensitive ones are noise |
| NATR | P/FCF | P/B | 6.0% operating margin supports a cash-flow multiple |
| COHR | *none* | P/B, EV/Sales, P/E | net debt 3.5% of EV; and with operating income stale, no margin regime is measurable for P/E |
| CHRS, STAA, GNW | *none* | P/B | P/B is never promoted by code - see below |

**Only 3 of 12 issuers have a method this step is willing to call `ECONOMICALLY_REASONABLE`.** This
is a diagnostic and explicitly not a gate: `usable_methods` counts every method that produced a
number, `SECONDARY_ONLY` included, and no issuer's readiness changes because of a label. But it is
the honest sharpening of the 7-of-12 headline, and it should be read alongside it rather than
instead of it.

Two rules do most of that work, and both are statements about arithmetic rather than about these
twelve:

- **P/B is never promoted by code.** D5-D0's own P/B spec says its applicability "is narrow and must
  be argued per issuer", and whether book value is a business's earning asset is not a question the
  balance sheet answers about itself. So code declines to promote it and never asserts it is wrong -
  `NOT_SUITABLE` is in the vocabulary and this module returns it in zero cases, verified over 896
  input combinations. The argument is D5-D2's, which is D5-D0's `AI_OWNED` column.
- **An EV method whose net debt is under 10% of EV is `SECONDARY_ONLY`.** ADBE's net debt is
  -117,000,000 against a 99,456,750,000 EV, so its enterprise value and its market cap agree to a
  tenth of a percent and the EV numerator carries no information the market cap does not. Measured
  on ADBE: **EV/FCF 9.6748x against P/FCF 9.6862x** is one method reported twice, and EV/Sales,
  EV/EBIT and EV/EBITDA are effectively P/Sales, P/EBIT and P/EBITDA under an EV label.
  To be precise about what this does and does not collapse: ADBE's six multiples use **five distinct
  denominators** - equity, FCF, revenue, operating income and derived EBITDA - and only the FCF pair
  is duplicated. So six methods is not six independent readings, but the overlap is on the numerator
  side and the denominators remain genuinely different. COHR is the same shape at 3.5% net debt.

---

## G. Readiness

The brief's §16 classification and D5-D0 §N's authoritative one, reported side by side because they
answer different questions:

| | READY | READY_WITH_LIMITATIONS | NOT_READY |
|---|---|---|---|
| §16 valuation-data readiness | **4** (ADBE 6, WBD 5, COHR 3, NATR 2) | **3** (CHRS, STAA, GNW - P/B alone) | **5** (DALN, FET, CHWY, BRY, FULT) |

| | COMPLETE | PARTIAL | NOT_READY |
|---|---|---|---|
| D5-D0 §N valuation completeness | **0** | **7** | **5** |

```
usable method count    median 1.0    min 0    max 6
distribution           0 methods: 5   1: 3   2: 1   3: 1   5: 1   6: 1
```

The 2-method boundary is not this step's invention: `data_readiness` imports
`MIN_METHODS_FOR_COMPLETE = 2` from the frozen contract rather than restating it, so the brief's
thresholds and D5-D0's cannot drift. One method is a number; two agreeing or disagreeing methods are
a valuation, because two can be cross-checked.

**D5-D0's COMPLETE is unreachable today, and that is the stage rather than the data.** COMPLETE
requires "a scenario set with full provenance", and §4 of this brief forbids scenarios here. So every
issuer with a valid market cap is PARTIAL at best regardless of how many methods it has - ADBE's six
and CHRS's one classify identically. That is exactly why both classifications are reported:
collapsing them would read a staging constraint as a data defect.

**All three single-method issuers have P/B as that single method.** This is the case D5-D0 warned
about in §N: "for an asset-light issuer P/B is NOT_APPLICABLE on its own terms, which makes it
NOT_READY." Code cannot tell whether CHRS, STAA or GNW is asset-light, so it reports them as
READY_WITH_LIMITATIONS and leaves the judgement to the step that owns it. A reader should treat
"READY_WITH_LIMITATIONS, P/B only" as one argument away from NOT_READY, not as a near-miss on READY.

---

## H. Manual Arithmetic Audit

Four issuers recomputed by hand from the resolved inputs in §C, independently of the module. Every
figure below was computed from the stored primitives and compared against the published multiple.

**ADBE**

```
market cap   250.50 x 397,500,000                          = 99,573,750,000     matches
net debt     4,802,000,000 - 4,919,000,000                 = -117,000,000       matches
EV           99,573,750,000 + 4,802,000,000 - 4,919,000,000 = 99,456,750,000    matches
FCF          10,481,000,000 - 201,000,000                  = 10,280,000,000     matches
P/B          99,573,750,000 / 11,518,000,000               = 8.6451             published 8.65x
P/FCF        99,573,750,000 / 10,280,000,000               = 9.6862             published 9.69x
EV/Sales     99,456,750,000 / 25,198,000,000               = 3.9470             published 3.95x
EV/EBIT      99,456,750,000 / 9,090,000,000                = 10.9413            published 10.94x
EBITDA       9,090,000,000 + 759,000,000                   = 9,849,000,000      matches
EV/EBITDA    99,456,750,000 / 9,849,000,000                = 10.0982            published 10.10x
```

**WBD**

```
market cap   28.07 x 2,510,703,314                         = 70,475,442,023.98  matches
EV           70,475,442,023.98 + 32,023,000,000 - 3,369,000,000 = 99,129,442,023.98  matches
EBITDA       -1,272,000,000 + 5,075,000,000                = 3,803,000,000      matches
EV/EBITDA    99,129,442,023.98 / 3,803,000,000             = 26.0661            published 26.07x
EV/FCF       99,129,442,023.98 / 2,180,000,000             = 45.4723            published 45.47x
EV/EBIT      operating income -1,272,000,000               -> refused, not -77.9x
```

**COHR**

```
market cap   289.93 x 195,832,246                          = 56,777,643,082.78  matches
EV           56,777,643,082.78 + 3,222,224,000 - 1,162,018,000 = 58,837,849,082.78  matches
EV/Sales     58,837,849,082.78 / 7,118,181,000             = 8.2659             published 8.27x
P/E          289.93 / 4.12                                 = 70.3714            published 70.37x
FCF          79,514,000 - 1,102,909,000                    = -1,023,395,000     -> refused
```

**NATR**

```
market cap   13.09 x 17,595,520                            = 230,325,356.80     matches
P/B          230,325,356.80 / 169,316,000                  = 1.3603             published 1.36x
FCF          27,361,000 - 9,276,000                        = 18,085,000         matches
P/FCF        230,325,356.80 / 18,085,000                   = 12.7357            published 12.74x
EV           debt MISSING at the cash date                 -> refused, 899-day debt never used
```

**Manual arithmetic defects: 0.**

Nine automated defect classes, each reported as a list of offenders rather than a count, so a
non-zero result names the issuer and the method. All nine empty:

```
value reported on a refusal                        0
negative or zero multiple published                0
numerator / denominator unit mismatch              0
multiple not equal to its own published inputs     0
denominator period not a full year                 0
stale input used in a published multiple           0
future input used in a published multiple          0
enterprise value identity violated                 0
determinism drift on a second full run             0
```

Three of these deserve a note on how they are checked rather than asserted:

- **Deterministic arithmetic** is checked twice over. Per published number, the stored numerator
  divided by the stored denominator must equal the stored multiple *to the bit* - division is exact
  given the same operands, so any difference at all means the number did not come from its stated
  inputs. Then the entire pilot is re-run from the primitives and all 84 cells compared, which
  catches inputs that move rather than arithmetic that does.
- **The EV identity** is checked in the other association order: `value` is market cap plus net debt,
  and `components_sum` is market cap plus debt minus cash. They agree to double precision on all
  three issuers with a valid EV.
- **"Silent wrong value" is structurally unrepresentable, not merely absent.**
  `MultipleResult.__post_init__` asserts that an `OK` carries a value and that anything else does
  not, so a refusal with a number attached raises rather than reports. Two tests exercise both
  directions of that assertion.

---

## I. Limitations

Carried forward unchanged from P1.1, and not retuned because of anything in this pilot:

- **The unlisted-class blind spot.** A privately held or unlisted second common class is not
  observable in a listed-security reference, so a `SINGLE_CLASS_CONFIRMED` reading cannot exclude
  one. This is the permissive direction of error and it is attached to all nine single-class readings
  in this sample rather than resolved. Every market cap in §F inherits it.
- **HIGH/MEDIUM confidence semantics** and **fail-closed unresolved issuers** are P1.1's and
  unchanged. DALN, BRY and FULT are refused, which costs 3 of 12 issuers entirely, and the resolver
  was not loosened to recover them.

New to this step:

- **WBD's debt scope is not established by the filing.** Its `LongTermDebt` of 32,023,000,000 sits on
  the same balance sheet as `LongTermDebtAndCapitalLeaseObligations` 30,530,000,000 and
  `...ObligationsCurrent` 1,493,000,000, whose sum is *exactly* 32,023,000,000. So whether WBD's
  total debt contains a capital lease liability is a question the filing does not answer either way.
  P1 records this and deliberately does not gate on it; this step promotes it to a published-multiple
  limitation, because WBD's EV and therefore its EV/Sales, EV/EBITDA and EV/FCF all inherit it.
- **P/E is reported under a narrower definition than D5-D0's**, and a net-income P/E would have been
  4 of 12 rather than 1 of 12 (§D). No such multiple was computed.
- **EV/FCF is absent from D5-D0's frozen `METHOD_SPECS`** (§D).
- **The suitability diagnostic is pre-registered but not validated.** `MIN_MARGIN_FOR_PRIMARY = 2%`
  and `MATERIAL_NET_DEBT_SHARE_OF_EV = 10%` are generic statements about division and materiality,
  not values fitted to these twelve, and nothing was tuned after seeing a result. They have also
  never been tested against an outcome of any kind, because this step reads no returns. They are
  diagnostics, and §17's prohibition on choosing methods by their results is asserted by test.
- **R3 = OBSERVED/DEFERRED and C1 = historically often NOT_EVALUATED** are unchanged from D4. Neither
  bears on this result and no work was opened on either.

---

## J. Practical Assessment

The gate was frozen before the pilot ran. D5-D0 §Q's eight V-gates remain authoritative and are all
correctness gates with a threshold of zero; none of them is a coverage threshold, so §19 of this
brief applies and its coverage thresholds were frozen verbatim in `run_strategy_h_v2_d5_d1.SUCCESS_GATE`.

At the time they were written, P1.1's input coverage for this same twelve was already on record -
market cap 7 of 12, EV 3 of 12 - so the 8-of-12 and "3 methods at 4 of 12" bars were known to be at
risk. They were frozen as written anyway. A threshold adjusted to what the data will produce is not a
threshold.

| clause | required | measured | |
|---|---|---|---|
| valuation-ready issuers | >= 8 / 12 | **7 / 12** | **MISS** |
| methods with coverage >= 4/12 | >= 3 | **1** (P/B) | **MISS** |
| silent wrong valuation values | 0 | 0 | PASS |
| stale inputs incorrectly used | 0 | 0 | PASS |
| future inputs incorrectly used | 0 | 0 | PASS |

```
correctness clauses   3 / 3 PASS
coverage clauses      0 / 2 PASS
```

**What this means, stated without softening.** The valuation machinery is correct: of the 84 issuer x
method cells, 19 published a multiple and not one of them failed any arithmetic, period, unit,
provenance, staleness or determinism check, and every one of the 65 refusals carries the name of the
input that caused it. Every defect D5-D0 identified as blocking now refuses rather than passing
silently: unbounded instant staleness (NATR, §C), duration ambiguity and partial-period flows
(P0/P0.1, visible as the EPS refusals in §C), debt composition (P1, and NATR's EV), D&A as a
canonical field (P0.1, which is what makes derived EBITDA possible at all) and multi-class
(P1.1, fail-closed on DALN, BRY and FULT).

The coverage is thin, and thinner than the headline. Seven issuers produce a multiple; four can
cross-check one method against another; three have P/B alone, which is the case D5-D0 itself says may
be NOT_READY on its own terms; and only three issuers have any method this step will promote
economically. The binding constraint is not the fundamentals - it is the market-cap numerator, which
five issuers cannot obtain, three because of share-class resolution and two because of shares
staleness.

**Why this is READY WITH LIMITATIONS and not NEEDS REVISION.** Every repair that would raise coverage
is forbidden by this step's own §4: no P/E repair, no debt v2, no security-master expansion, no
market-data purchase. A NEEDS_REVISION verdict would therefore demand work the brief prohibits, which
is not a coherent instruction to the next step. What remains is a question about D5-D2's design -
whether a fair-value framework can be designed and validated on four cross-checkable issuers - and
that is a design question, which is what D5-D2 is. There is no systemic wrong number, which §26 makes
the deciding test, and §20 explicitly directs that a 1-of-12 P/E and a low EV/EBITDA be recorded as
limitations rather than turned into repair phases. Neither was.

**Why it is not plain READY.** Two of five pre-registered clauses missed, and reporting READY would
be reading the gate off the result.

---

## K. Tests

```
new in this step                      67 tests, backend/tests/strategy_h_v2/valuation/test_d5_d1_multiples.py
H-V2 + H0 suite                       1,613 passed, 1 skipped  (1,546 before, +67 exactly)
whole repository                      7,408 passed, 24 skipped, 0 failed  (two files excluded, see N)
```

Both suite figures were measured on the final code, after the three hardening changes in §D and the
unit fix in §C were already in. The whole-repository count is not attributable to this step alone:
concurrent sessions hold 272 uncommitted files, so the only delta this step can claim is the +67 in
the H suite, which is exact.

Every §29 case is covered, each named for the measured issuer its fixture numbers came from:

```
P/B valid                       ADBE 99,573,750,000 / 11,518,000,000
negative equity                 refused, and zero equity separately
P/FCF positive                  ADBE
P/FCF negative                  COHR -1,023,395,000, and zero separately
EV/Sales                        ADBE
EV/EBIT positive                ADBE
EV/EBIT negative                WBD -1,272,000,000
derived EBITDA                  sum, provenance label, period mismatch, leg refusal inheritance
EV/EBITDA                       ADBE, and the WBD case where EBIT refuses and EBITDA does not
EV/FCF                          ADBE, plus the missing-D5-D0-spec assertion
P/E unavailable                 EPS MISSING_COMPONENT while net income resolves and is unused
stale input rejection           TTM period, instant equity, and all four EV methods via stale debt
future input rejection          refusal propagates by name
partial-period denominator      a 179-day flow in a TTM slot, refused on its measured family
undated denominator             UNKNOWN family, refused
unit incompatibility            a per-share equity fact and a USD EPS, both refused
deterministic arithmetic        twice, plus every multiple against its own inputs
one-method READY_WITH_LIMITATIONS   P/B alone
zero-method NOT_READY           no market cap
```

Four tests are worth calling out as guards rather than cases:

- **Enum totality.** Every member of `MarketCapStatus`, `CapitalStructureStatus`, `TtmStatus` and
  `InstantStatus` has an explicit row in the refusal tables, and every `MultipleStatus` has exactly
  one bucket. A new status in a primitive breaks this test rather than falling through to a
  fallback silently.
- **The no-silent-number invariant**, asserted in both directions.
- **`EV/EBITDA < EV/EBIT` whenever both compute** - a one-line sanity identity that catches an
  add/subtract inversion in the EBITDA derivation.
- **No dataclass in the module has a field named** `fair_value`, `target_price`, `tp1`, `tp2`,
  `scenario`, `bear`, `base`, `bull`, `verdict`, `recommendation`, `upside` or `forward`. §4's
  prohibition made structural rather than conventional.

All tests run from the repository root; `pytest.ini` sets `pythonpath = backend` and the data paths
are repo-root-relative.

---

## L. Verdict

```
H-V2-D5-D1 = READY WITH LIMITATIONS
```

The valuation layer can form a trustworthy multiple, and it can do so for fewer companies than the
pre-registered gate asked for. Correctness is unqualified: zero silent wrong values, zero stale
inputs used, zero future inputs used, zero manual arithmetic defects, zero determinism drift, and
every refusal named. Coverage missed both of its clauses and the shortfall is concentrated in one
place - the market-cap numerator, which five of twelve issuers cannot obtain.

Four issuers support a cross-checked valuation today. That is enough to design a fair-value framework
against and not enough to claim a portfolio-wide valuation layer, and D5-D2 should be built in the
knowledge that both halves of that sentence are true.

---

## M. D5-D2 Authorization

```
H-V2-D5-D2
FAIR VALUE / VALUATION FRAMEWORK PILOT
= AUTHORIZED
= NOT STARTED
```

§27 authorizes D5-D2 on a READY-family verdict. What this pilot hands it, as design input rather than
as a new gate:

```
design and validate on        ADBE (6 methods), WBD (5), COHR (3), NATR (2)
must handle by abstention     CHRS, STAA, GNW - P/B alone, which may not stand alone
must handle by refusal        DALN, FET, CHWY, BRY, FULT - no numerator, no valuation
cannot assume                 more than ~2 independent methods per issuer; ADBE's 6 are ~2
cannot assume                 P/E (1/12) or EV/EBITDA (2/12) are available
inherits                      the unlisted-class blind spot on every market cap
inherits                      WBD's unestablished debt scope on three of its multiples
owes an argument for          whether P/B is the earning-asset anchor for a given issuer; code
                              declines to promote it and never asserts it is wrong
```

D5-D2 is where `Primary Valuation Method`, `Secondary Valuation Method`, `Bear`/`Base`/`Bull`,
`Fair Value Range`, `TP1` and `TP2` are designed for the first time. The division of labour D5-D0
froze still holds: code owns the arithmetic - current multiples, fundamentals, scenario arithmetic,
target-price arithmetic - and the model owns the argument - which method fits the business, what the
comparable and historical context means, how growth and risk should be read. `NO_UNIVERSAL_MULTIPLE`
governs: a fair value may use an observed range and never a multiple the model supplies from its own
prior.

Nothing in this step was authorized beyond it. No primitive project was opened, no P/E repair, no
debt v2, no security-master expansion, no market data purchased, no fair value, no target price, no
decision, no forward return, no historical H verdict modified, no live model call, and no push.

---

## N. Run Record

```
schema          H_V2_D5_D1_VALUATION_DATA_PILOT_V1
module          backend/app/backtest/strategy_h_v2/valuation/multiples.py          (new)
runner          backend/app/dev/run_strategy_h_v2_d5_d1.py                         (new)
tests           backend/tests/strategy_h_v2/valuation/test_d5_d1_multiples.py      (new)
files modified  none
decision date   2026-09-16
reference snap  CS_2026-07-01, 77 days old, bound 186
bounds          instant staleness 135d, TTM period age 135d, methods for READY 2
model calls     0
cost            $0
```

Repository preflight: branch `main`, HEAD `848a033`, 0 ahead / 0 behind `origin/main`, no staged
files, and no H-related dirty or untracked file. The working tree carries substantial uncommitted
work from concurrent sessions, none of it under any H path; it was neither modified nor staged.

Two test files collected with errors before this step and still do:
`backend/tests/strategy_b/test_strategy_b_scanner.py` and
`backend/tests/test_strategy_b_historical_scanner.py`. Both are **untracked** files belonging to
another session's work in progress, so the breakage is pre-existing and unrelated; the whole-suite
figure is measured with those two files excluded and nothing else.
