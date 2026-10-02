# H-V2-D5-P1.1 - Practical Share-Class / Market-Cap Resolution (V1)

Status: **READY FOR VALUATION DATA PILOT**

Scope: `backend/app/backtest/strategy_h_v2/valuation/share_class.py`,
`backend/app/dev/audit_strategy_h_v2_d5_p1_1.py`,
`backend/tests/strategy_h_v2/valuation/test_d5_p1_1_share_class.py`.

No valuation was generated, no multiple computed, no fair value, no TP1/TP2, no target price, no
decision, no BUY/SELL, no APPROVE/WATCH/REJECT, no forward return, 0 live model calls, $0, no
historical H verdict modified, no push. No P0, P0.1 or P1 primitive was modified: `h0_5.py`,
`market_cap_gate.py`, `capital_structure.py` and `ttm.py` are byte-identical to `f42312d`.

Repository preflight at the start of this step: branch `main`, HEAD `d2711aa` (the P1 whole-suite
regression doc commit), 5 ahead of `origin/main` and 0 behind. The P1 commit `f42312d` was confirmed
in history. The working tree carried unrelated dirty and untracked files from concurrent sessions;
nothing outside the three files listed above was read for modification, and the commit is
path-specific.

---

## A. Purpose

Decide, well enough to use, whether an issuer has one class of common stock, so that market cap and
enterprise value stop being UNKNOWN for every issuer in the programme.

The step produces a share-class determination with a stated confidence, a market cap built from
existing primitives, and an enterprise value reassembled from P1's own. It produces no ratio and no
judgement about any issuer.

---

## B. Why P1.1 Exists

D5-P0 found that `h0_5.resolve_pit_shares` took a `multiple_share_classes` boolean that defaulted to
`False` - not "single class" but "nobody asked" - and repaired it by making the determination a
required argument of `valuation_market_cap` with no default. P0 deliberately added no detector, so
every issuer resolved UNRESOLVED and every market cap was a refusal.

D5-P1 then built cash, debt, net debt and EV, and measured the result:

```text
Cash          = 10 / 10        Market Cap  = 0 / 10
Total Debt    =  6 / 10        EV          = 0 / 10
Net Debt      =  6 / 10

every EV-based and market-cap-based method feasible = 0 / 10
```

P1's own note (`capital_structure.SHARE_CLASS_DETERMINATION_IS_STILL_ABSENT`) named the blocker
precisely: SEC companyfacts carries no class signal at all, while the local Polygon reference-ticker
store carries a validated one, and two things stopped P1 from wiring it - the only snapshot it knew of
was dated 2025-09-12, 381 days before the decision date, and the signal sees listed classes only.

**P1.1's first finding is that the first of those two objections was a search failure, not a data
limitation.** The store P1 found has a second, denser series on the same disk:

```text
data/runtime/strategy_c/raw/tickers/
    CS_2024-10-01  CS_2025-01-02  CS_2025-04-01  CS_2025-07-01
    CS_2025-10-01  CS_2026-01-02  CS_2026-04-01  CS_2026-07-01
```

The newest snapshot at or before the D5 decision date of 2026-09-16 is **2026-07-01, 77 days old**,
not 381. The second objection - that a listed-security reference sees only listed classes - is real,
cannot be closed from this data, and is handled in §J by disclosing it rather than by refusing
everything.

---

## C. Practical vs Perfect

```text
PERFECT PROOF REQUIRED      = NO
PRACTICALLY RELIABLE        = YES
KNOWN LIMITATION DISCLOSED  = YES
SILENT WRONG NUMBER         = NO
```

Stated in code as `share_class.PRACTICALLY_RELIABLE_NOT_PERFECTLY_PROVEN`. The reasoning: no
listed-security reference can prove the *absence* of an unlisted share class, so a contract that
requires proof produces zero multiples forever. P1.1 replaces proof with a stated confidence. A
reading may be used when the reference positively shows one common share class and shows nothing that
competes with it; it carries that confidence and the named limitation of what the reference cannot
see; and every other reading still denies market cap. What is forbidden is not an imperfect number, it
is a wrong number presented as a fact.

This step builds no historical security master, reconstructs no delisted universe, researches no
private share classes and buys no data.

---

## D. Reference Data

The snapshot actually used, `CS_2026-07-01.json.gz`, carries 5,305 active US common-stock rows over
5,167 distinct CIKs, and every field needed is present on every row:

| field | present | used for |
|---|---|---|
| `ticker` | 5305 / 5305 | the identity of last resort, and provenance |
| `cik` | 5293 / 5305 (12 blank) | the join to the issuer; blank-CIK rows are dropped, not grouped |
| `type` | 5305 / 5305 | filter, with the caveat below |
| `active`, `delisted_utc` | 5305 / 5305 | filter |
| `primary_exchange` | 5305 / 5305 | provenance |
| `composite_figi` | 5305 / 5305 | the security's identity, recorded as `security_id` |
| `share_class_figi` | 4199 / 5305 (1106 blank) | **the share class's identity** |

Two stored shapes exist and both are read: a flat `{"as_of", "results"}` document (the `strategy_c`
series) and a `{"pages": [...]}` document (the `research_universe` series). The `as_of` date is taken
from the filename, which is the one field both shapes agree on.

**The caveat that shaped the whole rule:** `type == "CS"` is necessary and *not sufficient* to mean
common stock in this store. It types FULTP, AGNCL, AGNCO, BHFAL/M/N/O, CNOBP and ~1,000 other
preferred shares and baby bonds as CS alongside their issuer's common stock. A rule that counted CS
rows per CIK, as §7 of the brief proposed, would therefore call Fulton Financial multi-class because
its preferred stock exists. Security-type metadata cannot do this job here, so identity does it
instead (§H), and where identity cannot settle it the reading fails closed.

---

## E. Share-Class States

`share_class.ShareClassTopology`:

```text
SINGLE_CLASS_CONFIRMED   one share-class identity, FIGI-identified, nothing competing
SINGLE_CLASS_LIKELY      one security, no competing one, identity resting on the ticker
MULTI_CLASS_CONFIRMED    two or more distinct share-class FIGIs under the CIK
UNRESOLVED               not determined; blocks exactly like MULTI_CLASS_CONFIRMED
```

`UNRESOLVED` carries a reason, because the reasons are not interchangeable: `NO_CIK`,
`NO_REFERENCE_SNAPSHOT`, `REFERENCE_SNAPSHOT_STALE` and `CIK_ABSENT_FROM_REFERENCE` are failures of
the inputs and say nothing about the issuer, while `UNIDENTIFIABLE_SIBLING_SECURITY` is a statement
about what the reference showed. The input failures are checked first, so a refusal names what actually
stopped it.

---

## F. Confidence Contract

```text
HIGH     one active CS security under the CIK, identified by a share-class FIGI,
         no other active CS security, reference present and inside its bound
MEDIUM   one active CS security, nothing competing with it, but no share-class FIGI,
         so the class identity is the ticker string
LOW      missing CIK, missing reference, stale reference, CIK absent from reference,
         or more than one security of which at least one cannot be identified
```

Aggregation is a total function of the inputs, frozen before the resolver was run on any issuer:
`len(class_identities)` and whether any identity is ticker-backed. There is no scoring and no
weighting, so there is nothing to tune after seeing a result.

`REFERENCE_TOPOLOGY_MAX_AGE_DAYS = 186`, and it is **not** the financial bound.
`MAX_INSTANT_STALENESS_DAYS == MAX_SHARES_STALENESS_DAYS == 135` governs cash, debt, equity and PIT
shares, which are restated every quarter and whose value at T differs from their value a quarter
earlier. Share-class topology is not that kind of quantity: a company has the same number of common
classes for years, and a change to it is a corporate action rather than a measurement. The reference
bound is therefore derived from the collector rather than from the quantity, because what can actually
go wrong is that the snapshot series stopped. The observed series is quarterly with a maximum interval
of 93 days; two intervals tolerate exactly one missed collection and no more. A third would mean the
series has stopped and is being used anyway.

The two bounds are applied to their own inputs inside the same market cap, and
`test_the_reference_bound_is_not_the_financial_bound` plus
`test_stale_pit_shares_are_still_refused_under_the_135_day_rule` assert that the reference bound does
not loosen the shares bound. The snapshot actually used is 77 days old, inside even one interval.

---

## G. Current Active-CS Rule

Rows are filtered to `type == "CS"`, `market == "stocks"`, `locale == "us"`, `active is True`,
`delisted_utc` empty and a non-blank CIK, then grouped by CIK. Within a CIK the resolver counts
**share-class identities**, not rows and not tickers:

```text
1 identity, all FIGI-backed          -> SINGLE_CLASS_CONFIRMED  (HIGH)
1 identity, ticker-backed            -> SINGLE_CLASS_LIKELY     (MEDIUM)
2+ identities, all FIGI-backed       -> MULTI_CLASS_CONFIRMED   (HIGH, denied)
2+ identities, any ticker-backed     -> UNRESOLVED              (LOW, denied)
0 identities / CIK absent / no CIK   -> UNRESOLVED              (LOW, denied)
```

The fourth line is the one that earns its keep. More than one security under a CIK where one of them
has no share-class FIGI is consistent with two readings - a preferred share the store mistyped as CS,
or an undisclosed second common class - and the snapshot does not say which. Guessing from the
ticker's suffix letter is exactly the kind of string heuristic a FIGI exists to replace, so the
reading refuses. Both candidate readings deny market cap anyway, which is why this is reported as
UNRESOLVED rather than as a confirmed multi-class: the refusal names what is actually unknown.

---

## H. FIGI Identity

A row's class identity is its `share_class_figi` when the snapshot carries one, and
`("TICKER", ticker)` otherwise. `composite_figi` identifies the *security* and is recorded as
`security_id` in provenance; it is never used to count classes, because one class listed on two
composites is still one class.

This is what makes a ticker rename safe. Two rows sharing a share-class FIGI are one class listed
twice, asserted by `test_ticker_mutation_under_one_identity_is_not_multi_class`. It is also the
property that distinguishes HIGH from MEDIUM: a FIGI survives a rename, a ticker string does not, so a
determination resting on a ticker is the weaker of the two and says so.

---

## I. Known Multi-Class Controls

Run against the stored 2026-07-01 snapshot. Every control is a company whose multiple common classes
are a matter of public record, so a resolver that allows any of them has failed whatever it does
elsewhere.

| control | CIK | tickers seen | topology | allowed |
|---|---|---|---|---|
| GOOG/GOOGL | 0001652044 | GOOG, GOOGL, GOOGM, GOOGN | UNRESOLVED (unidentifiable sibling) | no |
| FOX/FOXA | 0001754301 | FOX, FOXA | MULTI_CLASS_CONFIRMED | no |
| BRK.A/BRK.B | 0001067983 | BRK.A, BRK.B | MULTI_CLASS_CONFIRMED | no |
| BF.A/BF.B | 0000014693 | BF.A, BF.B | MULTI_CLASS_CONFIRMED | no |
| BIO/BIO.B | 0000012208 | BIO, BIO.B | MULTI_CLASS_CONFIRMED | no |
| CENT/CENTA | 0000887733 | CENT, CENTA | MULTI_CLASS_CONFIRMED | no |
| BELFA/BELFB | 0000729580 | BELFA, BELFB | MULTI_CLASS_CONFIRMED | no |
| AGM/AGM.A | 0000845877 | AGM, AGM.A | MULTI_CLASS_CONFIRMED | no |
| LBTYA/LBTYB/LBTYK | 0001570585 | LBTYA, LBTYB, LBTYK | UNRESOLVED (unidentifiable sibling) | no |
| LILA/LILAK | 0001712184 | LILA, LILAK | UNRESOLVED (unidentifiable sibling) | no |

```text
known multi-class blocked                  = 10 / 10
known multi-class misclassified as single  = 0
```

Three of the ten land in UNRESOLVED rather than MULTI_CLASS_CONFIRMED because their classes carry no
share-class FIGI in this snapshot. That is a weaker *statement* about them and the same *outcome*, and
it is reported honestly rather than upgraded: the resolver did not establish that Alphabet has two
classes, it established that it cannot account for all four of Alphabet's securities.

Single-class controls, the other direction:

| control | CIK | topology | confidence | allowed |
|---|---|---|---|---|
| AAPL | 0000320193 | SINGLE_CLASS_CONFIRMED | HIGH | yes |
| MSFT | 0000789019 | SINGLE_CLASS_CONFIRMED | HIGH | yes |
| NVDA | 0001045810 | SINGLE_CLASS_CONFIRMED | HIGH | yes |

---

## J. Unlisted-Class Limitation

```text
KNOWN LIMITATION: an unlisted or privately held secondary common class is not observable
in a listed-security reference, so a single-class reading cannot exclude one.
```

Stated in code as `share_class.UNLISTED_CLASS_BLIND_SPOT` and attached to the `limitations` list of
every reading that is allowed to be used, asserted by
`test_every_allowed_reading_discloses_the_unlisted_class_blind_spot`. It is the permissive direction
of error and it is accepted deliberately: closing it would require a security master this step is
forbidden to build, and leaving the whole single-class universe UNKNOWN over a theoretical second
class is the failure mode P1.1 exists to end.

`SINGLE_CLASS_LIKELY` additionally carries `TICKER_IDENTITY_LIMITATION`, which names the weaker
identity its reading rests on.

One secondary safety net is worth recording, though nothing depends on it: `h0_5.resolve_pit_shares`
drops dimensioned facts, so a filer that reports its cover-page share count per class supplies no
undimensioned total and the shares gate refuses independently of the class gate. That is a
coincidence of the companyfacts extractor, not a contract, and it is not relied on here.

---

## K. Valuation-Use Policy

Frozen before the resolver was run on any issuer, and expressed once as
`share_class.VALUATION_ALLOWED_TOPOLOGIES` so the gate and the audit cannot disagree about it:

```text
SINGLE_CLASS_CONFIRMED (HIGH)    -> market cap allowed
SINGLE_CLASS_LIKELY   (MEDIUM)   -> market cap allowed, limitations attached
MULTI_CLASS_CONFIRMED            -> market cap DENIED
UNRESOLVED            (LOW)      -> market cap DENIED
```

MEDIUM is allowed because Strategy H's goal is not a security master. When the visible reference shows
one listed common share class and shows nothing that competes with it, the theoretical possibility of
an unlisted class is recorded on the reading rather than used to suppress it.

`ShareClassResolution.gate_state` translates all four states into D5-P0's three-state gate vocabulary,
which is left unchanged: the two allowed topologies become `SINGLE_CLASS`, a confirmed multi-class
becomes `MULTIPLE_CLASSES` so the gate names the right refusal, and everything else becomes
`UNRESOLVED`.

### No silent aggregation for confirmed multi-class

For a confirmed multi-class issuer the one thing P1.1 must not do is multiply
`dei:EntityCommonStockSharesOutstanding` by the price of whichever class the panel priced. The
cover-page count covers every class; the price is one class's. Their product is not a market cap of
anything. No class-level share allocation exists in this repository, so the reading stays:

```text
MARKET_CAP = UNKNOWN
reason     = MULTI_CLASS_UNRESOLVED_ALLOCATION
```

Asserted by `test_confirmed_multi_class_market_cap_is_denied_and_names_the_allocation`.

---

## L. Market Cap

```text
MarketCap(T) = regular-session unadjusted close(T) x valid PIT raw shares(T)
```

Unchanged from the P0/H0.5 primitive: the arithmetic is `h0_5.historical_market_cap` and the share
gate is `h0_5.resolve_pit_shares`, both reached through P0's `valuation_market_cap`.
`share_class.resolve_market_cap` reimplements neither - it supplies the state P0 demanded and records
the provenance P0 had nowhere to put. PIT shares are still held to their own 135-day bound inside
`h0_5`.

Provenance per `MarketCapRecord.to_dict()`: `decision_time`, `decision_date`, `ticker`, `cik`,
`security_id` (composite FIGIs), `status`, `reason`, `market_cap`, `price`, `price_date`, `shares`
(`value`, `fact_id`, `shares_date`, `shares_age_days`, `shares_max_age_days`, `acceptance_time`),
`shares_reason`, `limitations`, and the full `share_class` record including `topology`, `confidence`,
`reason`, `reference_snapshot_date`, `reference_snapshot_age_days`, `securities` and
`class_identities`.

When the gate refuses before reaching the shares primitive, the shares are resolved anyway for
provenance: what a refusal cost is only legible next to what the other inputs would have supplied.

---

## M. EV Reopening

```text
EV = Market Cap + Total Debt - Cash = Market Cap + Net Debt
```

`share_class.reopened_enterprise_value` calls `capital_structure.enterprise_value` unchanged. P1.1
adds no EV arithmetic of its own; the only thing that changed between P1 and P1.1 is whether the
market cap handed to that function is OK, and reusing the primitive is what makes that statement
checkable. `EnterpriseValueResolution.components_sum` still cross-checks the two association orders.

---

## N. 10-Issuer Results

Decision date 2026-09-16, reference snapshot 2026-07-01 (age 77 days, bound 186). Cash, debt and net
debt are P1's results, reproduced unchanged.

```text
share-class HIGH     = 10 / 10        (all SINGLE_CLASS_CONFIRMED)
share-class MEDIUM   =  0 / 10
MULTI_CLASS          =  0 / 10
UNRESOLVED           =  0 / 10

Market Cap coverage  = 10 / 10        (was 0 / 10 in P1)
EV coverage          =  6 / 10        (was 0 / 10 in P1)
```

| ticker | topology | conf | market cap | net debt | EV |
|---|---|---|---|---|---|
| AEYE | SINGLE_CLASS_CONFIRMED | HIGH | 90,627,350 | OK | 98,328,350 |
| COLL | SINGLE_CLASS_CONFIRMED | HIGH | 722,409,052 | AMBIGUOUS_DEBT_TAGS | denied |
| FG | SINGLE_CLASS_CONFIRMED | HIGH | 3,018,056,567 | OK | 3,154,056,567 |
| VRRM | SINGLE_CLASS_CONFIRMED | HIGH | 538,019,126 | OK | 1,523,115,126 |
| IDCC | SINGLE_CLASS_CONFIRMED | HIGH | 8,482,950,621 | OK | 8,256,460,621 |
| DORM | SINGLE_CLASS_CONFIRMED | HIGH | 3,708,265,225 | OK | 4,016,762,225 |
| FRPT | SINGLE_CLASS_CONFIRMED | HIGH | 2,981,042,330 | INCOMPLETE_DEBT_COMPONENTS | denied |
| TG | SINGLE_CLASS_CONFIRMED | HIGH | 240,765,290 | OK | 269,586,290 |
| CRK | SINGLE_CLASS_CONFIRMED | HIGH | 3,846,429,179 | INCOMPLETE_DEBT_COMPONENTS | denied |
| SPSC | SINGLE_CLASS_CONFIRMED | HIGH | 2,904,494,038 | MISSING_DEBT | denied |

Every market cap equals `price x shares` to double precision against its own recorded inputs, checked
over all ten rather than asserted. The four EV refusals are P1's debt refusals unchanged, and none of
them is a share-class refusal. The six EV issuers are exactly P1's
`enterprise_value_blocked_only_by_share_class` list - AEYE, FG, VRRM, IDCC, DORM, TG - which is the
specific prediction P1 made about what a detector would unblock, now confirmed by recomputing both
lists in one run.

One data oddity surfaced and is recorded rather than worked around: VRRM's cover-page share count has
`end = 2026-08-31` with `acceptance_time = 2026-08-05`, a period end *after* its own acceptance. Age
is measured from `end`, so the effect is a smaller age and a more permissive reading. It leaks nothing
- both dates precede the decision date and the decision time - but a period end later than the filing
that carries it is not a shape a real cover page has, and it is a property of this stored panel.

---

## O. 22-Issuer Audit

The ten above plus the twelve frozen for D5-D1, read-only, same decision date and snapshot. No issuer
special case exists anywhere in the resolver.

```text
22-issuer resolver coverage:
    SINGLE_CLASS_CONFIRMED (HIGH)  = 19 / 22
    SINGLE_CLASS_LIKELY   (MEDIUM) =  0 / 22
    MULTI_CLASS_CONFIRMED          =  0 / 22
    UNRESOLVED             (LOW)   =  3 / 22
    unresolved rate                = 13.6%

    Market Cap = 17 / 22        EV = 9 / 22
```

The frozen twelve on their own: market cap 7/12, EV 3/12. The five market-cap refusals split into
three the share class blocked and two it did not:

| ticker | topology | reason | market cap |
|---|---|---|---|
| DALN | UNRESOLVED | CIK_ABSENT_FROM_REFERENCE | denied |
| BRY | UNRESOLVED | CIK_ABSENT_FROM_REFERENCE | denied |
| FULT | UNRESOLVED | UNIDENTIFIABLE_SIBLING_SECURITY | denied |
| FET | SINGLE_CLASS_CONFIRMED | - | UNKNOWN_SHARES |
| CHWY | SINGLE_CLASS_CONFIRMED | - | UNKNOWN_SHARES |

DALN and BRY are absent from the 2026-07-01 snapshot, which is what a delisting looks like from here,
and absence fails closed rather than reading as single-class. Their PIT shares do not resolve either,
so the share-class gate is not the only thing stopping them; the gate simply runs first. FET and CHWY
are blocked by the PIT-shares bound alone, as they were in P1.

**FULT is the only issuer in the whole 22 that the new resolver is the sole blocker of.** Its shares
and price both resolve; what stops it is §D's caveat in the corpus, FULTP being preferred stock the
store types CS without a share-class FIGI, so the sibling cannot be accounted for. That is the price
of the §G line-four refusal, measured: one issuer in 22.

### Resolver at scale

Run over all 5,167 CIKs in the snapshot, to check that 19-of-22 is the rule working rather than the
corpus being unrepresentative:

```text
SINGLE_CLASS_CONFIRMED (HIGH)   = 4,078   78.9%
SINGLE_CLASS_LIKELY   (MEDIUM)  = 1,004   19.4%
MULTI_CLASS_CONFIRMED           =    35    0.7%
UNRESOLVED            (LOW)     =    50    1.0%

valuation allowed               = 5,082   98.4%
```

The MEDIUM band is almost entirely single-ticker issuers whose row simply has no `share_class_figi`
(ACN is one). The 50 UNRESOLVED are the mixed cases of §G line four. The programme's own 22 skew to
HIGH because they are exchange-listed operating companies with FIGI-bearing rows, which is the normal
case, not a favourable one.

---

## P. Method Feasibility

Inputs only. No multiple was computed.

Ten issuers, P1 then P1.1:

| method | P1 | P1.1 | numerator | denominator |
|---|---|---|---|---|
| P/B | 0 / 10 | **10 / 10** | 10 | 10 |
| P/FCF | 0 / 10 | **8 / 10** | 10 | 8 |
| EV/Sales | 0 / 10 | **5 / 10** | 6 | 9 |
| EV/FCF | 0 / 10 | **5 / 10** | 6 | 8 |
| EV/EBIT | 0 / 10 | **4 / 10** | 6 | 8 |
| EV/EBITDA | 0 / 10 | **3 / 10** | 6 | 6 |
| P/E | 0 / 10 | **1 / 10** | 10 | 1 |

Twenty-two issuers: P/B 17/22, P/FCF 13/22, EV/Sales 8/22, EV/FCF 8/22, EV/EBIT 6/22, EV/EBITDA 5/22,
P/E 2/22.

Every numerator that was 0 in P1 is now the market-cap or EV coverage of §N. Every denominator is
unchanged from P1 and P0.1, which is the point: P1.1 moved the numerator and nothing else.

---

## Q. Tests

`backend/tests/strategy_h_v2/valuation/test_d5_p1_1_share_class.py`, 32 test functions, 52 cases after
parametrisation, no skips. Pure tests do
not skip; data-backed tests read `data/runtime/` and skip when it is absent, and it was present.

```text
single active CS with a FIGI              -> SINGLE_CLASS_CONFIRMED / HIGH
two active CS, two FIGIs, same CIK        -> MULTI_CLASS_CONFIRMED
GOOG/GOOGL style, untagged siblings       -> UNRESOLVED, not single
ticker mutation under one identity        -> one class, not two
one ticker-identified class               -> SINGLE_CLASS_LIKELY / MEDIUM
FIGI class beside an untagged sibling     -> denied (the FULT shape)
all siblings untagged                     -> denied (the LBTY shape)
non-common / inactive / delisted rows     -> excluded before counting
missing CIK, missing FIGI, no snapshot    -> UNRESOLVED, never single
CIK absent from the reference             -> UNRESOLVED, never single
stale reference, and the exact boundary   -> refused / accepted
reference bound is not the financial bound
snapshot published after the decision date -> never loaded
allowed topologies are exactly the two single-class readings
every allowed reading discloses the blind spot
every denied reading maps to a blocking gate state
market cap = price x PIT raw shares
MEDIUM allowed, with limitations attached
LOW denied, with no value
confirmed multi-class denied, naming the allocation
stale PIT shares still refused under the 135-day rule
no price -> refused rather than valued
market-cap provenance carries every §16 field
EV reopened from the P1 primitive, both association orders agreeing
EV still UNKNOWN when the share class denies the market cap
EV still UNKNOWN when debt is not resolved
10 known multi-class controls blocked in the stored snapshot
3 known single-class controls allowed in the stored snapshot
the D4 ten resolve single-class in the stored snapshot
```

### Safety acceptance

Counted by `audit_strategy_h_v2_d5_p1_1.acceptance`, over the ten, the twelve and all 22:

```text
known multi-class misclassified as single       = 0
unsafe multi-class market cap                   = 0
missing reference silently treated as single    = 0
stale reference used                            = 0
stale shares accepted                           = 0
future reference snapshot used                  = 0
future shares used                              = 0
value reported on a refusal                     = 0
allowed market cap without disclosed limitation = 0
```

### Regression

From the repository root. `backend/tests/strategy_h_v2` and `backend/tests/strategy_h0`:
**1,546 passed, 1 skipped**, against P1's 1,494 - exactly the 52 new cases and nothing moved.

Whole suite: **7,002 passed, 24 skipped**, against P1's 6,943 and 24. The +59 is the 52 above plus 7
outside Strategy H that concurrent sessions' untracked test files added; the H delta being exactly 52
is what establishes that this step moved nothing else.

Two test modules fail to collect and are excluded from the whole-suite run:
`backend/tests/strategy_b/test_strategy_b_scanner.py` and
`backend/tests/test_strategy_b_historical_scanner.py`. Both are **untracked** files from a concurrent
session importing `OBSERVATION_ONLY` from `app.strategy_b.scanner`, which the tracked module does not
export. Pre-existing, unrelated to this step, and not repaired here.

---

## R. Limitations

1. **Unlisted secondary class.** §J. Not observable, disclosed on every allowed reading, the
   permissive direction of error.
2. **Ticker-identified classes (MEDIUM).** 19.4% of the universe has no `share_class_figi`. Those
   readings rest on a ticker string and are labelled MEDIUM. None of the programme's 22 is in this
   band today, so the band is allowed by policy and untested against a real MEDIUM issuer in the
   corpus.
3. **`type == "CS"` is not common stock.** §D. The store types ~1,000 preferred shares and baby bonds
   as CS. The resolver copes by counting identities and failing closed on mixed cases, which costs
   coverage at issuers like FULT that are genuinely single-class.
4. **Reference is quarterly.** A share class created and listed between 2026-07-01 and the decision
   date is invisible. The bound catches a stopped collector, not a mid-quarter corporate action.
5. **Snapshot absence is read as delisting.** DALN and BRY are refused because they are absent. The
   resolver cannot distinguish "delisted" from "the collector missed it", and treats both as
   UNRESOLVED.
6. **P/E stays 1/10.** The TTM EPS gap is P0.1's and is not addressed here; P/E is not a D5 v1
   primary method.
7. **Three multi-class controls report UNRESOLVED rather than MULTI_CLASS_CONFIRMED.** §I. The outcome
   is correct and the statement is weaker than the facts warrant.
8. **`MULTI_CLASS_UNKNOWN` covers both class refusals.** P1's `CapitalStructureStatus` maps
   `UNKNOWN_SHARE_CLASS_UNRESOLVED` and `UNKNOWN_MULTIPLE_SHARE_CLASSES` to one composite status, so
   an EV row cannot distinguish "unresolved" from "confirmed multi-class" without reading the market
   cap record beside it. P1's primitive was not modified to fix this; the distinction is present in
   `market_cap.status` and `share_class.topology`.
9. **VRRM's cover-page period end precedes its acceptance.** §N. Recorded, not worked around.

---

## S. Verdict

### Success thresholds, frozen before execution

```text
10-issuer Market Cap            >= 7 / 10
10-issuer EV                    >= 4 / 10
unsafe market cap                = 0
known multi-class blocked        = all
```

Stated in code as `audit_strategy_h_v2_d5_p1_1.SUCCESS_THRESHOLDS` and evaluated by its `verdict()`,
which reports every check rather than a single boolean.

### Observed

```text
market_cap_coverage               10 >= 7     PASS
enterprise_value_coverage          6 >= 4     PASS
unsafe_market_cap                  0 == 0     PASS
known_multi_class_misclassified    0 == 0     PASS
no_value_on_a_refusal              0 == 0     PASS
no_stale_shares                    0 == 0     PASS
no_future_inputs                   0 == 0     PASS
no_missing_reference_as_single      0 == 0    PASS

all_pass = true
```

### Regression

```text
H-V2 + H0 suites   = 1,546 passed, 1 skipped   (P1: 1,494 + 1)
whole suite        = 7,002 passed, 24 skipped  (P1: 6,943 + 24)
```

```text
H-V2-D5-P1.1 = READY FOR VALUATION DATA PILOT
```

Every threshold passed, every safety criterion is 0, and the limitations of §R are disclosed rather
than discovered. No additional primitive step is proposed: P1.2 and P1.3 do not exist.

---

## T. D5-D1 Authorization

```text
D5-D1 = AUTHORIZED
```

The frozen twelve, unchanged:

```text
ADBE  DALN  CHRS  FET  NATR  STAA  CHWY  BRY  WBD  FULT  COHR  GNW
```

What D5-D1 will find available on them, measured here read-only: market cap 7/12, EV 3/12, P/B 7/12,
P/FCF 5/12, EV/Sales 3/12, EV/FCF 3/12, EV/EBIT 2/12, EV/EBITDA 2/12, P/E 1/12. Five of the twelve
produce no market cap - DALN and BRY absent from the reference, FULT ambiguous, FET and CHWY blocked
by the PIT-shares bound - and D5-D1 should expect to abstain on them rather than treat the gap as a
defect to repair.

No fair value, no TP1/TP2, no decision and no forward return is authorized by this document. D5-D1 is
a data pilot.
