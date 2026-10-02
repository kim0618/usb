# H-V2-D5-P1 - Debt / Net Debt / Enterprise Value Primitive (V1)

Status: **READY FOR VALUATION DATA PILOT**, with the pilot's numerator gated on a share-class
determination this step measured as unavailable from stored data. See §Q and §R.

Scope: `backend/app/backtest/strategy_h_v2/valuation/capital_structure.py`,
`backend/app/dev/audit_strategy_h_v2_d5_p1.py`,
`backend/tests/strategy_h_v2/valuation/test_d5_p1_capital_structure.py`.

No valuation was generated, no multiple computed, no fair value, no target price, no decision, no
forward return, 0 live model calls, $0, no historical H verdict modified, no push.

---

## A. Purpose

Construct PIT-safe cash, total debt, net debt and enterprise value deterministically, with every
component fact preserved, so that a later step can form a multiple without inheriting a number that
is wrong and carries status OK.

The step produces four balance-sheet-dated quantities and their provenance. It produces no ratio.

---

## B. P0.1 Handoff

P0 repaired the period primitive; P0.1 built trailing-twelve-month flows on it and measured:

```text
TTM Revenue          = 9 / 10      TTM OCF    = 10 / 10
TTM Operating Income = 8 / 10      TTM CapEx  =  8 / 10
TTM Net Income       = 10 / 10     TTM FCF    =  8 / 10
TTM EPS              = 1 / 10      TTM D&A    =  7 / 10

wrong-period components = 0   future components = 0   estimated/annualized = 0
```

P0.1 reproduced identically through this step's wider field registry - asserted by
`test_measured_ttm_outcomes_are_unmoved_by_the_wider_registry`, because a registry that moved them
would be changing a historical H result.

P0.1 left two things named and undone, both in `ttm.py`:
`INSTANT_FIELD_STALENESS_IS_STILL_UNBOUNDED` and `DEBT_AND_EV_REMAIN_BLOCKED`. Both are closed here.

P0.1's governing principle carries over unchanged and is the reason this step refuses as often as it
does:

```text
stale-but-internally-valid  ≠  usable valuation input
```

Repository preflight at the start of this step: branch `main`, HEAD `99cdd66` (the P0.1 commit,
confirmed in history), 3 ahead of `origin/main` and 0 behind. The working tree carried unrelated
dirty and untracked files from concurrent sessions; nothing outside the four files listed above was
read for modification, and the commit is path-specific.

---

## C. Instant Freshness

D5-D0 recorded the asymmetry: `h0_5` bounded `shares_outstanding` at 135 days and nothing bounded
`cash`, `total_debt`, `assets` or `equity`. So COLL resolved `total_debt` from a 2019-12-31 period
end - 2,463 days before the cutoff - with status OK.

```text
MAX_INSTANT_STALENESS_DAYS = h0_5.MAX_SHARES_STALENESS_DAYS = 135

decision_date - fact.end > 135   →   STALE   →   not used, and no value reported
```

The number is reused rather than invented, for the third time in this program: H0.5 bounded shares
with it, P0.1 bounded the constructed TTM period with it, and it bounds the instant fields here. It
answers the identical question - how far may a fact's period end lag the decision date before it
stops describing the present - and it is applied the identical way. A second number for the same
question would be a second thing to keep in agreement, which is the drift P0 spent its whole length
removing.

Two boundary rules, both tested:

- the comparison is `> bound`, so a balance sheet exactly 135 days old is still fresh;
- a balance sheet dated *after* the decision date is MISSING, not stale. Dating into the future of
  the valuation instant is not a freshness question.

Measured effect, 22 issuers: it is what refuses SPSC's 2013 debt (4,642 days) and, on the frozen
D5-D1 twelve, 5 of 12 debt balances and 4 of 12 cash balances. On the D4 ten it refuses 1 of 10 debt
balances and 0 of 10 cash balances.

---

## D. Cash Contract

```text
CASH_FOR_EV = CashAndCashEquivalentsAtCarryingValue, within the staleness bound
```

One tag. No summation. The single-element tuple is the contract rather than an accident, because
`FIELD_SPECS["cash"]` carries `CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents` as its
second priority and that tag is a **different measure, not a fallback**. Measured on COLL at
2026-06-30: 129,467k unrestricted against 150,377k restricted-inclusive, the 20,910k difference
being restricted cash COLL is not free to apply against debt. A priority that silently moved between
the two would overstate cash and understate enterprise value, and only for the filers that happen not
to tag the unrestricted line.

Excluded by name, each with its reason recorded in `CASH_EXCLUDED_FROM_EV`:

| Excluded | Why | Measured |
|---|---|---|
| `CashCashEquivalentsRestrictedCash…` | includes restricted cash | COLL 150,377k vs 129,467k |
| `RestrictedCash`, `RestrictedCashCurrent` | restricted by definition | COLL 19,850k, also VRRM, IDCC |
| `ShortTermInvestments`, `OtherShortTermInvestments` | a security, not cash | IDCC 496,821k, FG 545,000k |
| `MarketableSecuritiesCurrent`, `AvailableForSaleSecuritiesDebtSecuritiesCurrent` | same | COLL 157,341k, dated 2026-03-31 while cash is 2026-06-30 |
| `CashAndDueFromBanks` | a bank's operating cash; netting it against a bank's borrowings is not a defined EV, since deposits are funding | FULT 325,259k, and FULT reports no unrestricted-cash tag at all |

Excluding short-term investments raises enterprise value, which is the conservative direction. It is
a choice between defensible definitions and it is recorded rather than made silently.

Cost: zero on the D4 ten (the included tag is fresh for all ten). On the D5-D1 twelve it costs FULT,
which is the intended answer: a bank needs a valuation contract of its own and this version has none.

---

## E. Debt Tag Inventory

Read-only contract discovery over the stored SEC companyfacts of 22 issuers - the ten D4 contacted
and the twelve frozen for D5-D1. What it established, and what it changed:

| Shape | Issuers | What the old resolver returned |
|---|---|---|
| total + current + noncurrent, total = current + noncurrent | AEYE, VRRM | the **current portion**, called total debt |
| current + noncurrent, no total at a fresh date | IDCC, DORM, TG | IDCC the current portion alone; DORM **0** |
| total only | FG, ADBE, CHWY, WBD, COHR, GNW, FULT | - |
| no canonical tag, non-canonical borrowings instead | COLL (`LoansPayableCurrent`, `LongTermLoansPayable`, `ConvertibleLongTermNotesPayable`) | the **2019** `LongTermDebt`, status OK |
| noncurrent only at a fresh date | FRPT, CRK | - |
| no borrowing balance at any fresh date | SPSC, CHRS, NATR, STAA, FET, BRY, DALN | SPSC MISSING, while reporting a 2013 facility |

The inventory changed the contract twice, which is the argument for doing it rather than guessing:

1. **`DebtInstrumentCarryingAmount` had to be excluded from the reported-total slot.** AEYE reports it
   as 16,787k at 2026-06-30 against `LongTermDebt` 16,418k - the difference is unamortised issuance
   cost - so admitting a note disclosure would have made AEYE AMBIGUOUS and lost a correct answer.
2. **`ConvertibleSubordinatedDebtNoncurrent` had to be added** to the noncurrent slot. The audit's
   residual scan found it on CHRS at 227,220k, outside every slot. It is a general us-gaap element,
   not an issuer special case, which is the line §20 draws. Adding it moved CHRS's newest borrowing
   balance from 2024-03-31 to 2025-03-31: CHRS stays STALE either way, and the refusal now names the
   right date, which is the difference between a correct answer and a correct answer for the right
   reason.

`DEBT_TAGS_THAT_ARE_NOT_A_BALANCE` records eight names that mention debt and are not a balance of
borrowings: one-instrument carrying amounts, face amounts, fair-value disclosures, maturity-schedule
rows, facility capacity, facility amount outstanding, unamortised issuance cost and cash-flow
movements. Each was observed in the corpus and each would corrupt a composition if admitted.

---

## F. Debt Composition

Debt is a composition with a declared hierarchy, not a tag lookup. Three slots, each resolved to **one
tag** at **one** balance-sheet date:

```text
REPORTED_TOTAL   DebtLongtermAndShorttermCombinedAmount, LongTermDebt
CURRENT          DebtCurrent, LongTermDebtCurrent, ShortTermBorrowings, LinesOfCreditCurrent,
                 LineOfCredit, NotesPayableCurrent, LoansPayableCurrent, CommercialPaper, … (16)
NONCURRENT       LongTermDebtNoncurrent, LongTermLoansPayable, ConvertibleLongTermNotesPayable,
                 ConvertibleSubordinatedDebtNoncurrent, SeniorNotes, … (15)
```

Each candidate tag goes through `resolve_fact` on its own, with a one-tag spec and the rows filtered
to that tag, so the PIT rules are inherited exactly - acceptance before the decision time, amendment
versioning, duplicate collapse. What is deliberately **not** inherited is tag priority as a silent
tie-break between slot members, because that is the defect.

Within a slot:

```text
0 tags resolve                    → MISSING
1 value (even under 2 synonyms)   → OK, carried by the highest-priority tag
2 different values                → AMBIGUOUS_TAGS, and never summed
```

The brief's third candidate method, `SHORT_TERM_PLUS_LONG_TERM`, is **not** defined. The tags it would
use - `ShortTermBorrowings`, `LinesOfCreditCurrent`, `CommercialPaper` - are current borrowings and
already fill the CURRENT slot. A separate method name would assert that the filer distinguished
"short-term borrowings" from "the current portion of long-term debt" as two additive things, and when
a filer reports both at one date this module reports AMBIGUOUS_TAGS rather than adding them.

### Methods

```text
REPORTED_TOTAL            = one tag the filer states is its whole borrowings, as reported
CURRENT_PLUS_NONCURRENT   = CURRENT + NONCURRENT, one tag each, one date
```

Measured on the D4 ten: REPORTED_TOTAL 3, CURRENT_PLUS_NONCURRENT 3. On the D5-D1 twelve:
REPORTED_TOTAL 6, and FULT is the only issuer for which
`DebtLongtermAndShorttermCombinedAmount` is the resolving tag.

Every result carries the construction method and the fact id, tag, value, period end, form, accession
and acceptance time of every component.

---

## G. Double-Counting Prevention

The hierarchy is an **ordering**, not a check, because a check would need to know whether a particular
filer's total includes its current portion.

```text
AEYE, 2026-06-30
  LongTermDebt            16,418,000     ← REPORTED_TOTAL subsumes the two below
  LongTermDebtCurrent        850,000
  LongTermDebtNoncurrent  15,568,000
  850,000 + 15,568,000 = 16,418,000

  composed total = 16,418,000       (not 32,836,000, which every component supports)
```

When a reported total is used and both components also resolve at the same date, the arithmetic
difference is recorded as `reported_total_minus_components`. Measured: AEYE 0.0, VRRM 0.0, and `None`
elsewhere because the components are not reported at that date. The field is **recorded, not gated** - a filer whose total does not equal its parts still gets its total, with the gap on the record - and the
audit counts nonzero differences, of which there were 0 across 22 issuers.

Components of a composition cannot span balance-sheet dates, because all three slots are pinned to one
`period_end`. §12's example - current debt recent, noncurrent debt three years old - therefore resolves
`INCOMPLETE_COMPONENTS` rather than being summed.

---

## H. Revolver / Missing Debt

```text
no canonical debt tag  ≠  zero debt
```

- **SPSC**: `LineOfCreditFacilityAmountOutstanding` 0 at 2013-12-31 and a facility capacity of 1,000k,
  and no borrowing balance since. Standalone `compose_total_debt` returns STALE and names the date and
  the 4,642-day age; inside `resolve_net_debt`, where the candidate date is the 2026-06-30 balance
  sheet that cash reports on, it returns MISSING_DEBT. Both are true of different questions - "what is
  this issuer's debt" against "what is its debt on the valuation balance sheet" - and the distinction
  is deliberate rather than a disagreement.
- **COLL**: the canonical `LongTermDebt` is the 2019 figure, refused as STALE. At 2026-06-30 COLL
  reports `LoansPayableCurrent` 55,000k, `LongTermLoansPayable` 797,824k and
  `ConvertibleLongTermNotesPayable` 238,733k. The current slot resolves; the noncurrent slot has two
  tags with different values, so the result is AMBIGUOUS_TAGS. Their sum may well be COLL's noncurrent
  debt; no part of the filing states that those two are the complete set, and adding them because they
  are the two present is the same completeness assumption `fundamental_fields` already refuses for D&A
  components.
- **FRPT, CRK**: noncurrent borrowings reported at 2026-06-30 and no current tag at that date. Both
  probably carry no current debt. Companyfacts does not say so, and the difference between "the filer
  reported zero" and "the filer reported nothing" is the difference between a debt figure and a guess.
- **TG**: `ShortTermBorrowings` 0 and `LongTermDebtNoncurrent` 46,000k at 2026-06-30. The zero is a
  reported figure, so it composes - the one shape in which a zero is admissible.

No debt figure is read out of filing prose anywhere in this step.

---

## I. Lease Treatment

```text
LEASE_TREATMENT = DEFERRED
EV_DEBT_SCOPE   = BORROWINGS_ONLY
```

There is no prior Strategy H intent to follow: the only lease liability anywhere in H0 or H-V2 is the
tag name `LongTermDebtAndFinanceLeaseObligationsCurrent`, sitting **first** in
`FIELD_SPECS["total_debt"]` priority, which is how a current, lease-inclusive figure came to be the
canonical answer to "total debt".

The tag availability settles the version-1 question. A finance lease liability is reported at a fresh
period end by **1 of the 10** measured issuers (FRPT, 28,979k at 2026-06-30). The other nine either
never tag it or last tagged it as zero years ago - AEYE 0 at 2025-06-30, VRRM 0 at 2019-12-31, IDCC 0
at 2025-12-31, DORM 0 at 2020-12-26, and COLL, TG, CRK and SPSC not at all. Under this module's own
rule that an absent component is UNKNOWN rather than zero, **requiring** a finance lease component
would refuse nine of ten issuers to add a figure that is zero or untagged in every one of them.

Measured cost of the exclusion, stated as exposure rather than reassurance: none of the six issuers
whose debt composes reports a fresh non-zero finance lease liability, so their debt figures would be
unchanged by the opposite decision. That is a fact about this corpus, not a general argument, which is
why the treatment is DEFERRED and not CLOSED.

### The scope is a declaration, not a guarantee

Excluding lease-bundled tags does not establish that the tags admitted are lease-free. **WBD** is the
measured counter-example:

```text
WBD, 2026-06-30
  LongTermDebt                                   32,023,000,000   ← the resolving tag
  LongTermDebtAndCapitalLeaseObligations         30,530,000,000
  LongTermDebtAndCapitalLeaseObligationsCurrent   1,493,000,000
  30,530,000,000 + 1,493,000,000 = 32,023,000,000   exactly

  FinanceLeaseLiability (2025-12-31)                683,000,000
```

WBD uses the two tag families as one measure, so whether its 32,023,000k contains its 683,000k of
finance lease liability is not stated anywhere in the filing - a ±2% scope uncertainty inside a
reported total, undetectable in general.

The response is to attach it rather than resolve it:
`DebtResolution.lease_bundled_tags_at_same_end` records every lease-bundled tag the filer reports on
the same balance sheet, with its value, so a consumer comparing two issuers' debt can see that one
carries an unresolved lease question of a stated size. Refusing WBD was rejected: 32,023,000k is
WBD's total debt under either reading, and discarding a real number over a question the filing does
not answer either way is a worse error than carrying the question.

Measured: non-empty for **1 of 22** issuers (WBD), and 0 of 10 on the D4 ten.

---

## J. Net Debt

```text
Net Debt = Total Debt - Cash,   both valid, both fresh, both on the SAME balance-sheet date
```

The shared date is chosen first, for both legs at once, rather than each leg resolving to its own
newest date and the difference being taken anyway. D5-D0 measured 3 of 10 issuers whose cash period
end did not match their debt period end, and DORM is the live reminder that near is not the same: its
balance sheet is dated 2026-06-27 while its peers' are 2026-06-30.

A mismatch is `COMPONENT_DATE_MISMATCH` and carries no value. A missing component is never zero: a
debt that resolves against a cash that does not gives `MISSING_CASH`, not the debt figure.

Negative net debt is returned as a negative number. **IDCC** at 2026-06-30: debt 389,100k against
cash 615,590k gives **-226,490,000**. Clamping that at zero would overstate the enterprise value of
every cash-rich issuer.

Measured, D4 ten:

| Ticker | Date | Method | Total debt | Cash | Net debt |
|---|---|---|---:|---:|---:|
| AEYE | 2026-06-30 | REPORTED_TOTAL | 16,418,000 | 8,717,000 | 7,701,000 |
| FG | 2026-06-30 | REPORTED_TOTAL | 2,239,000,000 | 2,103,000,000 | 136,000,000 |
| VRRM | 2026-06-30 | REPORTED_TOTAL | 1,034,657,000 | 49,561,000 | 985,096,000 |
| IDCC | 2026-06-30 | CURRENT_PLUS_NONCURRENT | 389,100,000 | 615,590,000 | **-226,490,000** |
| DORM | 2026-06-27 | CURRENT_PLUS_NONCURRENT | 440,479,000 | 131,982,000 | 308,497,000 |
| TG | 2026-06-30 | CURRENT_PLUS_NONCURRENT | 46,000,000 | 17,179,000 | 28,821,000 |
| COLL | - | AMBIGUOUS_DEBT_TAGS | - | 129,467,000 | - |
| FRPT | - | INCOMPLETE_DEBT_COMPONENTS | - | 350,809,000 | - |
| CRK | - | INCOMPLETE_DEBT_COMPONENTS | - | 45,008,000 | - |
| SPSC | - | MISSING_DEBT | - | 173,167,000 | - |

The two repairs D5-D0 named, side by side: IDCC was 378,239,000 (the current portion alone) and is
389,100,000; DORM was **0** and is 440,479,000.

---

## K. Enterprise Value

```text
EV = Market Cap + Net Debt = Market Cap + Total Debt - Cash
```

Defined as `market_cap + net_debt`, which makes the §15 identity definitional rather than asserted;
`EnterpriseValueResolution.components_sum` computes the other association order so a test checks the
two agree to double precision rather than the module asserting it about itself.

Market cap is P0's valuation-facing path, `valuation_market_cap`, supplied by the caller rather than
computed here, because it needs a price panel and a market calendar this module has no business
knowing about. The gate ordering is deliberate: the share-class gate runs before anything else, so a
NOT_READY result names the gate that actually stopped it.

```text
share class UNRESOLVED or MULTIPLE  →  market cap UNKNOWN  →  EV = MULTI_CLASS_UNKNOWN
shares or price absent              →  market cap UNKNOWN  →  EV = MARKET_CAP_UNKNOWN
any debt or cash leg not OK         →                         EV = that leg's status
```

§16 is structural rather than checked: a 2026 market cap cannot meet a 2019 balance sheet, because the
2019 balance sheet is STALE before it reaches the arithmetic. Tested directly.

Measured: **EV = 0 / 10** and **0 / 12**, every one `MULTI_CLASS_UNKNOWN`. That is the fail-closed
behaviour §13 requires and the measured state of the repository, not a run that went wrong. §P says
exactly what blocks it.

---

## L. Provenance

`EnterpriseValueResolution.to_dict()` carries:

```text
contract_version          h_v2_d5_p1_capital_structure_v1
status, reason
decision_time             the fact-acceptance cutoff
decision_date             the valuation instant (the price session)
enterprise_value
market_cap                + status, reason, shares fact, shares reason
net_debt                  + status, reason, period_end
  cash                    + status, value, period_end, age_days, fact id/tag/unit/end/form/
                            accession/acceptance_time, candidate tags
  debt                    + status, value, construction_method, period_end, age_days, scope,
                            lease_treatment, reported_total_minus_components,
                            lease_bundled_tags_at_same_end,
                            every component's slot + fact id/tag/value/end/form/accession/
                            acceptance_time, and all three slot resolutions
staleness_bound_days      135
```

`fact_id` is the same identity tuple `ttm.TtmComponent.fact_id` uses - accession, tag, unit, period - because companyfacts has no row id and that tuple is what `resolve_fact` narrows on. Repeated
resolution of the same facts produces byte-identical records, asserted by test.

---

## M. 10-Issuer Coverage

Decision date 2026-09-16 (the newest session in the local unadjusted panel at or before the D2.1
cutoff of 2026-09-28), staleness bound 135 days.

```text
                       D4 TEN                 D5-D1 TWELVE
cash                   10 / 10                 7 / 12
total debt              6 / 10                 6 / 12
net debt                6 / 10                 4 / 12
market cap              0 / 10                 0 / 12
enterprise value        0 / 10                 0 / 12
equity                 10 / 10                 9 / 12
assets                 10 / 10                 9 / 12
```

Refusal reasons, D4 ten: total debt - AMBIGUOUS_TAGS 1 (COLL), INCOMPLETE_COMPONENTS 2 (FRPT, CRK),
STALE 1 (SPSC). Market cap - UNKNOWN_SHARE_CLASS_UNRESOLVED 10.

`equity` and `assets` are included because D5-D0 measured 10 of 10 for both and no staleness bound on
either, so a price-to-book built on them would have inherited exactly the defect the debt side had.
They go through `resolve_valuation_instant`, which adds the bound **and nothing else**: selection stays
`resolve_fact`'s, because for these fields tag priority is a preference between whole definitions
rather than a tie-break between parts. `StockholdersEquity` and
`StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest` are book value excluding and
including non-controlling interests; price-to-book wants the first, which is why it is first. Applying
the debt slots' refusal rule to `equity` was tried and cost 2 of 10 issuers for no gain in
correctness - recorded in `TAG_PRIORITY_IS_A_PREFERENCE_FOR`.

**The frozen D5-D1 twelve have materially worse stored data than the D4 ten**: 5 of 12 debt balances
and 4 of 12 cash balances are stale, and DALN, FET and BRY have no fresh balance sheet at all. This is
an input to any D5-D1 authorization and is not a property of this step.

### Residual scan

A composition is complete only over the tags it knows, so the audit tests that claim rather than
asserting it: at each issuer's chosen balance-sheet date it lists every us-gaap USD instant tag whose
name mentions debt, borrowings, credit lines, notes, leases or obligations and which the contract has
not accounted for - in a slot, out of scope as a lease, lease-bundled, or declared not to be a balance.

Over 22 issuers it returns asset-retirement obligations (VRRM 18,898k, CRK 21,444k, BRY 194,585k,
STAA 205k), revenue performance obligations (IDCC 2,112,791k, ADBE 22,270,000k, VRRM, FET), purchase
obligations (SPSC, CHRS, COHR), contractual obligations (BRY), supplier finance programmes (WBD 277,000k,
COHR 27,000k) and one finance-lease payment schedule row (FRPT 2,111k) - **and no borrowing balance**.
It found exactly one real omission during this step, `ConvertibleSubordinatedDebtNoncurrent` on CHRS,
which is now slotted. That is evidence the slot lists are complete over this corpus, and it is not a
proof about an issuer outside it, which is why the scan is part of the audit rather than a one-off.

---

## N. Method Feasibility

Input availability, never a ratio. `MethodFeasibility` reports numerator and denominator readiness
separately, because when the numerator is blocked for one structural reason a single `feasible`
boolean would report every method as blocked and hide which denominators this program has earned.

```text
                   D4 TEN                        D5-D1 TWELVE
            feasible  num   den            feasible  num   den
EV/Sales       0       0     9 / 10           0       0     9 / 12
EV/EBIT        0       0     8 / 10           0       0     6 / 12
EV/EBITDA      0       0     6 / 10           0       0     4 / 12
EV/FCF         0       0     8 / 10           0       0     5 / 12
P/FCF          0       0     8 / 10           0       0     5 / 12
P/B            0       0    10 / 10           0       0     9 / 12
P/E            0       0     1 / 10           0       0     1 / 12
```

`num` is the enterprise value (or, for P/E, P/B and P/FCF, the market cap); `den` is the TTM flow or
the equity balance. Every numerator is 0 for the one reason in §P.

EV/EBITDA's denominator is `ttm.ebitda_feasible` - operating income and D&A both OK over one
**identical** constructed period - asked rather than reimplemented. 6 of 10 on the D4 ten, from
operating income 8/10 and D&A 7/10. No EBITDA value is produced and no EBITDA multiple is formed;
`EBITDA_DERIVED` stays the reserved label P0.1 created and this step emits none.

---

## O. Tests

`backend/tests/strategy_h_v2/valuation/test_d5_p1_capital_structure.py`, **57 tests**, every fixture
the shape of a real filing in the measured corpus and named in the test, so a test that stops holding
points at the issuer whose data it was built from.

```text
freshness        bound equals h0_5's 135; recent cash OK; stale cash refused; exactly-at-bound fresh;
                 future-dated balance sheet not used; fact accepted after the decision time excluded
cash             one tag; the restricted-inclusive tag is not a fallback; exclusions carry reasons
debt             reported total as reported; current + noncurrent; reported total prevents the
                 double count of its own components (AEYE 16,418k not 32,836k); total-minus-components
                 recorded not gated; LongTermDebtCurrent alone is not total debt; missing noncurrent
                 refuses; zero requires a fact that says zero (TG), including an all-zero composition;
                 no evidence at all is UNKNOWN; two disagreeing tags in one slot are AMBIGUOUS (COLL);
                 two agreeing tags collapse to the higher priority; a facility disclosure is not a
                 drawn balance (SPSC); a stale balance sheet is refused (COLL 2019); components from
                 two dates are never added; leases outside every slot; lease-bundled evidence recorded
                 (WBD); the evidence field cannot create an eligible date
net debt         debt less cash at one date; net cash is negative (IDCC); two balance sheets never
                 netted; newest date where both legs resolve; a missing cash leg is not the debt
EV               arithmetic; the two orderings of the identity agree; unresolved share class blocks;
                 multiple classes block; missing shares and missing price block; an unknown debt leg
                 blocks; a stale balance sheet cannot meet a current market cap
provenance       every component fact, period and acceptance time; the bound; deterministic across
                 repeated resolution; a refusal never carries a value
registries       strict superset sharing spec objects; len(FIELD_SPECS) == 12 unmoved; total_debt
                 left exactly as it was; every slot tag in exactly one slot; equity keeps its
                 preference priority and its staleness bound; a composed field refuses single-line
                 resolution
feasibility      numerator and denominator reported separately; verdicts carry no values
house rule       each prohibition states itself (D4-E7R)
measured (skips  acceptance criteria all empty; coverage is what this document reports; EV blocked
without data)    only by share class for the named six; the two D5-D0 defects repaired with their
                 figures; residual scan finds no unaccounted borrowing balance; P0.1's TTM outcomes
                 unmoved by the wider registry
```

Regression, from the repository root: **1,494 passed, 1 skipped** across `backend/tests/strategy_h_v2`
and `backend/tests/strategy_h0` - P0.1's 1,437 plus the 57 new. Full suite: see §Q.

---

## P. Limitations

1. **Enterprise value is 0 / 10, and the gate is the share-class determination.** P0 made the
   determination a required argument with no default and added no detector. P1 measured whether the
   stored data could supply one:
   - **SEC companyfacts cannot.** It excludes dimensioned facts, so a multi-class filer's per-class
     cover-page counts are simply absent, and the undimensioned
     `dei:EntityCommonStockSharesOutstanding` is a single number for all ten measured issuers. The
     multiple values `us-gaap:CommonStockSharesOutstanding` carries at some period ends are split
     restatements - DORM 18,078,261 and 36,156,522 at 2011-12-31 is one count and twice it - not
     classes.
   - **The local Polygon reference-ticker store carries a validated signal**: one CIK with more than
     one active common-stock ticker. Checked against known shapes, it identifies GOOG/GOOGL,
     FOX/FOXA, NWS/NWSA, LEN/LEN.B, BRK.A/BRK.B, HEI/HEI.A and UHAL/UHAL.B correctly, and finds all
     ten measured issuers single-tickered.
   - **Two things stop P1 from wiring it.** The only full snapshot is dated 2025-09-12, 381 days
     before the decision date and far outside this module's own 135-day bound. And the signal sees
     *listed* classes only, so an unlisted second class would read as single-class - which is the
     permissive direction of error, the same shape of defect as the boolean P0 replaced.

   So the determination is a step of its own with its own freshness and completeness contract, and
   `SHARE_CLASS_DETERMINATION_IS_STILL_ABSENT` records the measurement in code. Six of the ten
   issuers - AEYE, FG, VRRM, IDCC, DORM, TG - have cash, debt, net debt, PIT shares and an unadjusted
   close all resolving, and are blocked by nothing else. That count is reported as
   `enterprise_value_blocked_only_by_share_class`, named as a refusal count because a conditional is
   not coverage.

2. **Total debt coverage is 6 / 10, and the four refusals are the honest answers**, not gaps waiting
   to be filled. COLL would need a filing-level statement that its two noncurrent tags are the
   complete set; FRPT and CRK would need a reported zero they do not report; SPSC would need any
   borrowing balance since 2013.

3. **`EV_DEBT_SCOPE = BORROWINGS_ONLY` is a declaration about what this version measures, not a
   property the filings guarantee.** WBD's reported total may contain its 683,000k of finance leases
   and the filing does not say. The uncertainty is attached to the result rather than resolved.

4. **Operating leases are excluded and finance leases are deferred.** A later version that includes
   finance leases will produce different debt figures for filers that tag them, and comparing across
   versions will need the scope field this step records.

5. **P/E coverage is 1 / 10**, inherited unchanged from P0.1's TTM EPS 1/10. This step did not attempt
   to repair it, per §24 of its brief. A valuation pilot will be carried by other methods.

6. **A bank has no valuation contract here.** FULT resolves debt 1,713,976k and cash MISSING, because
   `CashAndDueFromBanks` is excluded and deposits are funding rather than debt. The refusal is
   intended; the contract is absent.

7. **The staleness bound is reused, not validated for instants.** 135 days was validated for a share
   count. It is the right order of magnitude for a quarterly balance sheet - one quarter plus filing
   lag - and the measured sensitivity is one issuer: FET, whose cash and debt are both 169 days old,
   is the only one of 22 inside the (135, 180] window, so a 180-day bound would change exactly that
   issuer. Every other refusal is 351 days or older. Reusing the number keeps one staleness bound in
   the repository; it is not a claim that 135 is optimal for instants.

8. **The residual scan is evidence over 22 issuers, not a proof.** It found one real omission during
   this step. Running it is how a 23rd issuer's missing tag gets found.

---

## Q. Verdict

```text
H-V2-D5-P1 = READY FOR VALUATION DATA PILOT
```

on the §23 criteria, every one counted on real filings rather than claimed:

```text
wrong debt composition              = 0
stale instant accepted as OK        = 0
silent zero fallback                = 0
multi-class unsafe EV               = 0
value reported on a refusal         = 0
future-dated components             = 0
net debt across two balance sheets  = 0
reported total vs components ≠ 0    = 0
deterministic provenance            = complete, and byte-identical on replay
```

Coverage is reported honestly and is low where the filings are: total debt 6/10, net debt 6/10,
enterprise value 0/10.

What this closes: `ttm.INSTANT_FIELD_STALENESS_IS_STILL_UNBOUNDED` and `ttm.DEBT_AND_EV_REMAIN_BLOCKED`,
and D5-D0's `DEBT_COMPOSITION_CONTRACT = NOT_IMPLEMENTED` and `DEBT_STALENESS_UNBOUNDED`. Those
constants state what was true when they were written and are left in place, the same way P0.1 left
D0's measured 0-of-10 figures: this document is the repair, not a rewrite of the finding.

---

## R. D5-D1 Authorization

**D5-D1 as specified - measuring P/B, P/FCF, EV/Sales, EV/EBIT and EV/EBITDA applicability and
coverage over the frozen twelve - is not executable today, and the blocker is not P1's output.**

Every one of those five methods needs a market capitalisation, and market cap is 0/12 because the
share-class determination is absent. Run today, D5-D1 would measure 0 for all five and its only
finding would be the one §P.1 already states. That is not worth a pilot.

What is authorized, and what is not:

```text
AUTHORIZED   nothing further in D5-D1 beyond what P1 already measured. The denominator coverage in
             §N is D5-D1's denominator answer for the D4 ten and the frozen twelve, measured.

NOT YET      any D5-D1 measurement whose numerator is a market cap or an enterprise value, which is
             all five named methods.

PREREQUISITE a share-class determination step: a reference snapshot inside the staleness bound, a
             CIK-to-active-common-tickers rule, and an explicit contract for the unlisted-class blind
             spot. Until it exists, UNRESOLVED and fail-closed stay correct.
```

The frozen twelve remain frozen and unchanged: ADBE, DALN, CHRS, FET, NATR, STAA, CHWY, BRY, WBD,
FULT, COHR, GNW. Nothing in this step modified a historical H verdict, generated a valuation, formed a
multiple, or produced a fair value or target price.
