# H-V2-D5-D2 - Fair Value / Valuation Framework Pilot

```
STEP          H-V2-D5-D2
VERDICT       READY WITH LIMITATIONS
D6            AUTHORIZED, WITH ONE BLOCKING PREREQUISITE
PILOT         7 issuers, from D5-D1's frozen twelve
MODEL CALLS   0 live
COST          $0
PUSH          NO
```

| | |
|---|---|
| decision session | 2026-09-16, the panel's last and D5-D1's own |
| fair value generated | **5 of 7** - ADBE, WBD, COHR, NATR, GNW |
| VALUATION_NOT_READY | **2 of 7** - CHRS, STAA |
| confidence | HIGH 0 / MEDIUM 3 / LOW 2 / NOT_READY 2 |
| manual arithmetic defects | **0** over 15 fair values and 15 upsides, recomputed at 40-digit precision |
| fabricated valuation inputs | **0** |
| TP1 / TP2 reproducible from code-owned arithmetic | **YES**, both |
| D5-D1 multiples reproduced by the PIT panel | **19 of 19**, bit for bit |
| BUY / SELL | **NO** |
| APPROVE / WATCH / REJECT | **NO** |
| forward returns read | **NO** |

---

## A. Purpose

D5-D1 answered "what is the market paying today" and stopped there, deliberately and structurally:
`MultipleResult` had no field a fair value could occupy. This step is the first in H-V2 that answers

> what is this issuer worth, and how far is a realistic target from here?

It is a valuation *engine* pilot and not an investment decision. Nothing here is a buy, a sell, an
approve, a watch, a reject, an entry, an exit or a position size, and §35's D6 is where any of those
would first become possible.

The result: **the framework works, and the honest version of it produces much less upside than a
careless version would.** Five of seven issuers get a defensible fair value range. Two abstain. Every
published number reproduces from its stated operands. And the single most important finding is not
about any issuer - it is that the one design decision which separates a valuation framework from an
upside generator is the choice of observation window, and that decision was worth between 20 and 60
percentage points of fabricated upside on three of the five issuers.

---

## B. D5-D1 Handoff

Carried in unchanged, and **nothing in D5-D1 was modified, re-run under different bounds, or
reclassified**:

```
D5-D1                  READY WITH LIMITATIONS
sample                 12, checksum 0ef2bb56...2d7a6732
READY / RWL / NOT_READY         4 / 3 / 5
D5-D0 §N COMPLETE / PARTIAL / NOT_READY   0 / 7 / 5
usable methods         median 1.0, min 0, max 6
P/B 7/12   P/FCF 3/12   EV/Sales 3/12   EV/EBITDA 2/12
EV/FCF 2/12   EV/EBIT 1/12   P/E 1/12
silent wrong values 0  stale inputs used 0  future inputs used 0
```

The five issuers that produced no multiple at all - DALN, FET, CHWY, BRY, FULT - produce none here
either, for the identical reasons (three share-class refusals, two stale PIT share counts). §2 of
this step's brief forbids both forcing a valuation onto them and editing their readiness, and
`NOT_RECLASSIFIED` in the runner records that neither was done. Their D5-D1 rows are quoted, never
recomputed.

**The inherited ceiling is unchanged and it is the dominant coverage fact.** D5-D1 measured that
every blocked issuer was blocked by the market-cap numerator rather than by the income statement.
This step adds nothing that could lift it: a fair value needs a per-share figure, a per-share figure
needs the share count, and the share count is what the resolver refuses.

---

## C. Pilot Companies

§2's split, used as given:

| role | issuers | why these |
|---|---|---|
| valuation design / execution | ADBE, WBD, COHR, NATR | D5-D1's four READY issuers - the only ones with more than one method, or with a method code was willing to promote |
| abstention / limitation | CHRS, STAA, GNW | D5-D1's three READY_WITH_LIMITATIONS issuers, all three single-method, and all three with P/B as that method |

The abstention group is the more interesting half of the pilot, and §24 is why: D5-D1 found that all
three single-method issuers had P/B as the single method, and warned that "a reader should treat
'READY_WITH_LIMITATIONS, P/B only' as one argument away from NOT_READY". This step makes the
argument, per issuer, and it does **not** come out the same way for all three - see §J.

---

## D. Method Selection

### D.1 What code owns and what the model owns

D5-D1 left a deliberate gap. Its `method_suitability` returns `SECONDARY_ONLY` for P/B in every
case and returns `NOT_SUITABLE` in **zero** cases, with the reason recorded as
`NOT_SUITABLE_IS_AI_OWNED`: "asserting that a computable multiple is unsuitable requires knowing
what the business earns its money with [...] and that is D5-D0's AI_OWNED column, not something the
balance sheet states about itself. [...] D5-D2 is where the argument gets made."

This step makes those assertions and makes nothing else. Precisely:

```
MODEL-OWNED, text only     whether a computable method is economically suitable for this business
                           which method is preferred as the primary anchor, as an ORDERED list
                           the business description and the valuation-risk narrative

CODE-OWNED, every number   the PIT observed multiple panel
                           the percentiles and which session each was observed on
                           the trend diagnostic and window eligibility
                           the per-share metric, implied EV, implied equity
                           Bear / Base / Bull fair value, TP1, TP2, BEAR_ANCHOR
                           every upside, the reconciliation, the confidence
```

No model output is a number. No number passes through a model. The judgements live in
`METHOD_JUDGEMENTS` in `run_strategy_h_v2_d5_d2.py`, as text, next to the code that ignores their
prose and reads only their ordering.

### D.2 Why the judgements are a preference order rather than a choice

A model that picked one method per issuer *after* seeing the fair values would be fitting, and the
fitting would be undetectable afterwards: a chosen method looks exactly like a reasoned one. So each
issuer declares an **ordered preference** over methods on business grounds, and code takes the first
entry whose observed panel is contract-eligible. The selection is therefore reproducible from the
table plus the panel, and the trace of what was passed over and why is published per issuer.

This was load-bearing exactly once, and it is worth recording because it is the mechanism working
rather than a formality. COHR's declared preference is `P/E` then `EV/Sales`. P/E was **rejected by
code**, not by argument: COHR's TTM diluted EPS resolves on only 23 of 501 sessions, far below the
60-observation floor, so no window of its P/E panel is eligible. `EV/Sales` governs as a consequence
of a measurement, and the trace says so.

### D.3 The selections, and the assertions behind them

| issuer | primary | secondary | asserted NOT_SUITABLE | the argument |
|---|---|---|---|---|
| ADBE | **P/FCF** | EV/EBIT | P/B, EV/FCF | 41% FCF margin on a subscription business; book equity of 11.5bn supporting 25.2bn of revenue at a 36% margin is the arithmetic signature of an issuer whose earning assets are not capitalised |
| WBD | **EV/Sales** | EV/EBITDA | P/B, P/FCF | operating margin -3.5% and net debt 29% of EV: an enterprise sales multiple is the only method whose denominator is positive and whose numerator spans the capital structure |
| COHR | **EV/Sales** | *none* | P/B | P/E preferred and refused on panel coverage; book equity is dominated by II-VI merger goodwill rather than by the fabs |
| NATR | **P/FCF** | P/B | *none* | 6.0% operating margin supports a cash-flow multiple; P/B is a meaningful asset check for a supplement manufacturer, so it is a secondary rather than unsuitable |
| GNW | **P/B** | *none* | *none* | for an insurer book equity **is** the earning asset - see §J.2 |
| CHRS | *none* | *none* | P/B | research-driven biopharma: book equity is cash plus working capital, and the value is approvals and a pipeline |
| STAA | *none* | *none* | P/B | single-franchise device maker whose value is approvals and surgeon adoption; negative FCF removed the method that would have valued it |

**P/B was asserted NOT_SUITABLE on four issuers and accepted as primary on one.** That asymmetry is
the §24 answer and it is not a hedge: whether book value is the earning asset is a question about the
business, and it has different answers for a software company, a levered media group, a biopharma and
an insurer.

---

## E. Multiple Context

### E.1 The point-in-time observed multiple panel

D5-D0 §K permits exactly one source for a scenario multiple:

> value = (code-owned per-share metric for that scenario) x (**observed** multiple for that scenario)

Not a multiple a model considers fair, not a sector rule of thumb, not a peer average from a prior.
So this step builds the issuer's own multiple at **every session of the local panel**, as it would
have been observed on that session, and draws every target from that series.

For each of 501 sessions the primitives are re-resolved with the decision time set to that session:

```
facts admitted            accepted_at <= session 21:00Z, inside resolve_fact
share-class snapshot      as_of <= session, inside load_reference_snapshot
TTM bundle                constructed at the session, not at the package cutoff
PIT shares                the 135-day bound applied at the session
price                     that session's unadjusted regular-session close
```

The primitives are **called, never reimplemented**. A historical multiple computed by a second code
path would be a different quantity from D5-D1's current one, and the panel's entire value rests on
them being the same quantity.

### E.2 The check that the wiring is right

If the PIT wiring were wrong - a package cutoff passed where a session was wanted, a snapshot read
too late, a TTM bundle built at the wrong instant - the panel's last session would still produce
plausible numbers, and they would not be *these* numbers.

```
d5_d1_agreement:  compared 19,  mismatches 0
```

Every multiple D5-D1 published for these seven issuers - ADBE 6, WBD 5, COHR 3, NATR 2, CHRS 1,
STAA 1, GNW 1 - is reproduced by the panel's final session to the bit, and every refusal is
reproduced as a refusal. That is what establishes the history and the present are one series.

### E.3 Panel coverage

Observations per method, out of 501 sessions:

| issuer | P/B | P/FCF | EV/Sales | EV/EBIT | EV/EBITDA | EV/FCF | P/E |
|---|---|---|---|---|---|---|---|
| ADBE | 486 | 486 | 486 | 486 | 486 | 486 | 120 |
| WBD | 472 | 472 | 472 | 178 | 269 | 472 | 54 |
| COHR | 490 | 336 | 490 | 31 | 31 | 336 | 23 |
| NATR | 456 | 456 | 0 | 0 | 0 | 0 | 93 |
| CHRS | 264 | 0 | 0 | 0 | 0 | 0 | 91 |
| STAA | 444 | 89 | 0 | 0 | 0 | 0 | 0 |
| GNW | 471 | 0 | 0 | 0 | 0 | 0 | 200 |

The ~15-session shortfall from 501 on every row has one cause: the earliest reference snapshot in the
local store is `CS_2024-10-01` and the panel begins 2024-09-17, so the share class - and therefore
the market cap - is unresolvable for the first ten sessions. Fail-closed, and not repaired.

Two rows are worth reading against D5-D1's current-session table rather than past it:

- **GNW's P/E resolved on 200 sessions and does not resolve today.** Its TTM diluted EPS was
  constructible for much of the panel and is `MISSING_COMPONENT` at the decision session. A method
  whose history exists and whose present does not cannot value the issuer, and code requires the
  method to be OK *at the decision session* before it may be selected. GNW remains single-method.
- **STAA's P/FCF resolved on 89 sessions and is refused today** on a negative denominator. Same
  consequence, and it is why STAA has nothing left but P/B.

### E.4 Percentiles are observations, never interpolations

Every percentile here is **nearest-rank**: it returns an element of the observed series together with
the session that element was observed on. The interpolating percentile that every statistics library
defaults to would return a weighted average of two neighbours - a multiple nobody ever paid for the
issuer - and D5-D0 §K's "observed" and §L's "the multiple is still an observed value" do not permit
it. On a four-element series `[10, 20, 30, 40]` the interpolating median is 25.0 and nothing was ever
25.0; nearest-rank returns 20.0 and the date it traded there. Ties break by session so that a target
price's provenance cannot drift between runs while its value stays the same.

`BEAR_PERCENTILE = 10`, `BASE_PERCENTILE = 50`, `BULL_PERCENTILE = 90`. The extremes of a 500-session
panel are single sessions and a single session is an accident; the 10th and 90th are the edges of
where the multiple actually spent its time.

### E.5 Target multiple provenance, against §11's hierarchy

§11 requires a source hierarchy and provenance on every target multiple. What was actually used, and
what was not:

| § | source | used | what it contributed |
|---|---|---|---|
| 1 | current observed company multiple | **reported, never a target** | the anchor every upside is measured against, and the `current_percentile_rank` that locates it in its own history |
| 2 | recent own-multiple context | **every target multiple** | nearest-rank P10/P50/P90 of the issuer's own PIT panel, each carrying the session it traded at |
| 3 | comparable-company context | **no** | `PEER_CONTEXT_UNAVAILABLE` on all seven (§F.4) |
| 4 | growth / profitability / business-quality adjustment | **as an argument, never as a number** | the model's economic reading selects the method and argues the business; it never adjusts a multiple numerically |
| 5 | explicit assumption | **no** | zero target multiples came from an assumed level |

So **all fifteen published target multiples come from level 2 and nothing else**, and each carries its
percentile, its observed session and its window. That is narrower than §11 permits, and deliberately:
level 5 is the one source that cannot be audited after the fact, and the audit asserts set membership
of every target in the issuer's own observed panel precisely so that level 5 cannot have been used
without the check failing.

The §7 third tier - `OPTIONAL / SUPPORTING METHOD` - was **not implemented**. §7 permits it rather
than requiring it, §22's reconciliation is defined over a primary and one secondary, and no issuer in
this pilot had a third economically suitable method that was independent of the first two. Every
computable method's current multiple is published for every issuer regardless, so a reader is not
missing a number; what is missing is a third fair value, and nothing in this step would have consumed
one.

### E.6 RECENT_2Y_CONTEXT, and the forbidden label

The panel is at most 501 sessions, 2024-09-17 to 2026-09-16. Per D5-D0 §H and §12 of this brief,
`LONG_TERM_HISTORICAL_RANGE` is a **forbidden label** and no statement in this document claims a
long-term historical normal. Every historical figure is tagged with the window it came from:
`FULL_2Y`, `RECENT_12M` or `RECENT_6M`. The paths that would extend the panel to five or ten years
were priced by H0.5 and never purchased, and no amount of care with a two-year window turns it into
a cycle.

---

## F. Scenario Contract

### F.1 The three scenarios, and which operand moves

D5-D0 §J requires every scenario input to carry provenance from three sources: the code-owned current
fundamentals named field by field with period end and family; an explicit assumption source - a
complete guidance range, a measured own or peer multiple observation, or a measured historical growth
rate; and the direction and magnitude of every deviation from the code-owned current value.

In this pilot the assumption source is always **a measured own-multiple observation**, and the metric
is **identical across Bear, Base and Bull**:

```
BEAR    metric = current TTM (or balance-sheet instant)   multiple = P10 of the window
BASE    metric = current TTM                              multiple = P50 of the window
BULL    metric = current TTM                              multiple = P90 of the window
```

`metric_moved = False` and `multiple_moved = True` on all fifteen scenarios. D5-D0 §L requires that
which operand moved be reported separately, and the report is plain: **the multiple moved, the
fundamental did not.** Every scenario here is a re-rating scenario.

That is a constraint rather than a preference, and the reasons are three:

- §15 of this brief forbids a model-authored revenue, EPS or margin forecast.
- D5-D0 §I admits management guidance as a scenario input only when metric, period, low, high and
  unit are all present *and* the period is comparable to the denominator the scenario uses. See §F.3.
- Projecting a measured historical growth rate forward is a forecast, and §33 forbids opening that as
  a new phase here.

**The limitation this creates is the most important one in the document and is restated in §N:** a
Bear case built only on multiple compression does not price a fundamental deterioration. An issuer
whose earnings fall will breach its Bear anchor without the multiple compressing at all.

### F.2 Window eligibility

A window may govern a published target price only if both hold:

```
n >= MIN_WINDOW_OBSERVATIONS = 60                     about one reported quarter of trading days
distinct denominator period ends >= 2                 the denominator actually moved
```

The second is the subtler gate and it closes a real trap. If every observation in a window divides by
one denominator period end, the "multiple range" is the **price range rescaled by a constant**.
Reporting it as an observed multiple range would claim the market re-rated the issuer when all that
happened is the price moved against a fundamental that had not yet been restated. `denominator_moved`
is False for such a window, `contract_eligible` is False with it, and the window may be shown as
context and can never govern a target. It fired on no window in this pilot, because every eligible
window spans at least three filed periods, and it is reported as a zero rather than omitted.

### F.3 Guidance: UNKNOWN, and why that is the honest state

No scenario input here comes from management guidance. The guidance machinery this repository owns is
D4's, and `expectation.comparability.REQUIRED_OPERANDS` defines completeness for two comparison
kinds: `GUIDANCE_RANGE_CHANGE` and `RESULT_VS_COMPANY_GUIDANCE`. Both are about guidance *changes* and
results *versus* prior guidance. Neither produces a forward revenue or EPS **level** on a period
comparable to a TTM valuation denominator, which is what a scenario would need.

Per D5-D0 §I a one-sided floor is not a range and a missing bound may not be interpolated, so the
state is `UNKNOWN`, which §I names as a valid scenario state. `GUIDANCE_UNAVAILABLE` in the module
records it.

### F.4 Peer context: PEER_CONTEXT_UNAVAILABLE on every issuer

D5-D0 §H requires peer eligibility to be argued against five measured criteria - same or adjacent
business, comparable margin regime, comparable growth regime on the same period family, comparable
capital intensity, and the same method computable on the peer from code-owned fields - with
`PEER_MIN_ELIGIBLE = 3`.

The twelve-issuer sample is a seeded hash over the whole D2.1 package universe and contains no two
issuers in the same business. A peer set would therefore require resolving multiples across a far
wider universe, which is a new project; §13 forbids building a peer engine here and §33 forbids
opening it as a rabbit hole.

So the label is `PEER_CONTEXT_UNAVAILABLE`, it is a stated absence rather than a silent one, and
**it costs every issuer a confidence demotion**, which is why no issuer in this pilot can reach HIGH.
§N.1 explains why this particular absence matters more than it looks.

---

## G. Fair Value Arithmetic

### G.1 The three chains

Each method's numerator determines how a target multiple becomes a value per share. The chain is read
off D5-D1's `MULTIPLE_SPECS` rather than listed again, so a method cannot have one numerator there
and a different bridge here.

```
EQUITY_PER_SHARE    value = (denominator / shares) x target          P/B, P/FCF
ALREADY_PER_SHARE   value = denominator x target                     P/E only
ENTERPRISE_BRIDGE   implied_ev     = denominator x target            EV/Sales, EV/EBIT,
                    implied_equity = implied_ev - net_debt           EV/EBITDA, EV/FCF
                    value          = implied_equity / shares
```

### G.2 The invariant that makes the chain checkable

> At the current observed multiple, the fair value per share must equal the current price, exactly.

`identity_residual` computes it and the audit asserts it on every published scenario. For the equity
and per-share chains this is near-trivial. For the enterprise chain it is not: the implied enterprise
value must come back to the observed enterprise value, subtracting net debt must return the market
cap, and dividing by the PIT share count must return the close. That the same invariant holds across
all three chains is what establishes that the per-share metric, the net debt bridge and the share
count are all the ones the observed multiple was built from.

This is the only check that catches the defect class where **every operand is individually
plausible**. A share count off by 1% produces a fair value off by 1%, and nothing about the number
looks wrong. The tolerance is `1e-9` relative - roughly a million times machine epsilon, tight enough
that a wrong share count, a wrong net-debt sign or a mismatched denominator cannot hide inside it.

```
fair_value_at_current_multiple_is_not_price:  0 offenders
```

### G.3 A negative implied equity is refused, never published

At a low enough target multiple an enterprise method's implied equity is negative. A negative fair
value per share is the same class of defect as D5-D1's negative multiple: it reads like a number and
means "the debt exceeds the business". `ScenarioRefusal.NEGATIVE_IMPLIED_EQUITY` refuses it. It fired
on no published scenario in this pilot and is exercised by test on WBD's real operands - at a 0.5x
EV/Sales its implied enterprise value of 18.1bn does not cover 28.7bn of net debt.

### G.4 A refusal cannot carry a number

`ScenarioValue.__post_init__` asserts that an accepted scenario has a positive value and that a
refusal has none, mirroring `MultipleResult`'s discipline exactly. So a fair value attached to a
refusal **raises rather than reports**, and "silent wrong fair value" is structurally
unrepresentable here in the same way "silent wrong multiple" was closed at D5-D1.

`FairValueRange.ordered` is vacuously True on an incomplete range - a range missing a leg has
nothing to order - so incompleteness is audited in its own right as
`fair_value_range_missing_a_leg` rather than being allowed to pass the monotone check silently. It
is 0 on this sample: all five valued issuers produced all three legs.

---

## H. TP1 / TP2

D5-D0 §L, applied unchanged:

```
TP1         = base_scenario_per_share_metric x base_multiple
TP2         = bull_scenario_per_share_metric x upper_multiple
BEAR_ANCHOR = bear_scenario_per_share_metric x lower_multiple

UpsideToTP1    = TP1 / current_unadjusted_close - 1
UpsideToTP2    = TP2 / current_unadjusted_close - 1
DownsideToBear = BEAR_ANCHOR / current_unadjusted_close - 1
```

The close is the **unadjusted** regular-session close the observed multiple was computed from, so the
upside is measured against the same price the denominator was divided into. Mixing an adjusted close
in here would silently rescale every target by the issuer's dividend history.

**TP2 is not TP1 plus a margin.** It is a different observed multiple applied to the same metric, and
`tp2_operand_that_moved` names which operand differs - `"multiple"` on all five issuers, because the
metric is held. The ratio TP2/TP1 is therefore exactly the ratio P90/P50 of the observed window, and
it is 1.10 for ADBE and 1.38 for NATR because those issuers' own multiple distributions have
different widths, not because a margin was chosen.

**No target price is fitted to any return.** `NO_RETURN_INPUT_HERE` states the defence and it is
structural rather than procedural: no realized return, forward return, price path, outcome label or
performance measure is an argument to any function in the module, a field on any dataclass in it, or
a key in anything it emits. A test asserts the absence by scanning every signature and every
dataclass field in the module for the vocabulary. A fitted number and a derived number look identical
once written down, so the only defence is that the quantity is not available to be fitted to.

### H.1 A cross-check that falls out of the arithmetic

For an equity-chain method, the fair value at a past observed multiple equals *that session's close*
whenever the per-share fundamental has not changed since, and otherwise differs from it by exactly the
ratio of the two per-share fundamentals. This is visible in the published numbers and is a useful
independent read on each target:

| issuer | leg | target observed on | close that session | published fair value | what the difference says |
|---|---|---|---|---|---|
| ADBE | Bear | 2026-07-01 | 210.98 | **210.98** | FCF per share unchanged since |
| ADBE | Bull | 2026-08-19 | 272.47 | **272.47** | FCF per share unchanged since |
| ADBE | Base | 2026-06-08 | 244.99 | 248.23 | FCF per share is 1.3% higher now |
| WBD | Bull | 2026-09-04 | 28.25 | **28.25** | revenue per share unchanged since |
| NATR | Base | 2025-04-08 | 12.01 | 15.93 | FCF per share is 1.33x what it was |
| GNW | Base | 2024-11-19 | 7.43 | 8.82 | book value per share is 1.19x what it was |

The identity holding exactly where the fundamental is unchanged is a second, independent confirmation
that the panel's multiples and the decision session's metric are the same quantities.

---

## I. Company Results

Decision session 2026-09-16. Every figure code-owned.

| | ADBE | WBD | COHR | NATR | GNW |
|---|---|---|---|---|---|
| current price | 250.50 | 28.07 | 289.93 | 13.09 | 10.09 |
| primary method | P/FCF | EV/Sales | EV/Sales | P/FCF | P/B |
| chain | equity | enterprise | enterprise | equity | equity |
| contract window | RECENT_6M | RECENT_6M | RECENT_6M | FULL_2Y | FULL_2Y |
| current multiple | 9.69x | 2.74x | 8.27x | 12.74x | 0.44x |
| percentile of its own window | 56th | 87th | 22nd | 19th | **97th** |
| Bear multiple (P10) | 8.16x | 2.54x | 7.78x | 11.95x | 0.335x |
| Base multiple (P50) | 9.60x | 2.59x | 9.73x | 15.49x | 0.382x |
| Bull multiple (P90) | 10.54x | 2.76x | 11.79x | 21.39x | 0.421x |
| per-share metric | 25.86 FCF | 14.38 rev | 36.35 rev | 1.028 FCF | 23.10 book |
| **Bear fair value** | **210.98** | **25.12** | **272.13** | **12.28** | **7.74** |
| **Base fair value = TP1** | **248.23** | **25.81** | **343.18** | **15.93** | **8.82** |
| **Bull fair value = TP2** | **272.47** | **28.25** | **417.90** | **21.99** | **9.73** |
| upside to TP1 | **-0.9%** | **-8.0%** | **+18.4%** | **+21.7%** | **-12.6%** |
| upside to TP2 | **+8.8%** | **+0.6%** | **+44.1%** | **+68.0%** | **-3.6%** |
| downside to Bear | -15.8% | -10.5% | -6.1% | -6.2% | -23.3% |
| secondary method | EV/EBIT | EV/EBITDA | *none* | P/B | *none* |
| secondary Base cross-check | 250.23 | 27.93 | - | 16.73 | - |
| reconciliation | CORROBORATES **1.008x** | CORROBORATES 1.082x | NO_SECONDARY | CORROBORATES 1.050x | NO_SECONDARY |
| confidence | MEDIUM | MEDIUM | LOW | MEDIUM | LOW |

### I.1 The window rule did most of the work, and it is worth seeing what it cost

One rule, applied identically to every issuer, no per-issuer discretion:

```
if the primary method's FULL_2Y trend is TRENDING_STRONG
    the contract window is the SHORTEST contract-eligible window
otherwise
    the contract window is FULL_2Y
```

The trend is a code-owned Spearman rank correlation between session index and multiple, with the
conventional effect-size bands (`|rho| >= 0.7` strong, `>= 0.4` moderate). The rule is mechanical so
that it cannot be fitted, and **every window's fair value is published for every issuer regardless**,
so what the rule passed over is on the table next to what it chose.

What it passed over, on the three issuers it fired for:

| issuer | FULL_2Y rho | FULL_2Y Base mult | TP1 under FULL_2Y | its upside | TP2 under FULL_2Y | its upside | TP1 published | its upside |
|---|---|---|---|---|---|---|---|---|
| ADBE | **-0.962** | 15.54x | 401.79 | **+60.4%** | 836.62 | **+234.0%** | 248.23 | **-0.9%** |
| WBD | **+0.861** | 1.949x | 16.62 | **-40.8%** | 26.55 | -5.4% | 25.81 | **-8.0%** |
| COHR | **+0.753** | 3.873x | 130.26 | **-55.1%** | 358.96 | +23.8% | 343.18 | **+18.4%** |

**ADBE is the case the rule exists for.** Its observed P/FCF was 33.79x on the panel's first usable
session (2024-10-01) and 9.69x at the decision session, with a 37.16x peak on 2024-12-06 and a 7.48x
trough on 2026-06-25 - rank correlation -0.962, about as monotone as a market series gets - while
free cash flow did not fall. A Base multiple drawn from the full panel would have published a 15.5x
target and +60% upside, and that upside would have been manufactured entirely out of the fact that
the past was more expensive. The 6-month window has rho +0.246 and is genuinely range-bound, its
median is 9.60x against a current 9.69x, and the honest answer is that **ADBE trades almost exactly
at its own recent median**: TP1 -0.9%.

ADBE's FULL_2Y Bull leg is the clearest illustration: a P90 of 32.35x free cash flow, observed on
2024-11-04, applied to today's cash flow gives a TP2 of **836.62, or +234%**. Every operand is real
and the arithmetic is exact, and the number is still meaningless, because the multiple it uses
belongs to a regime the market has spent two years leaving.

The rule is not biased toward caution - it moved COHR's and WBD's numbers *up*. For COHR the full
panel's median of 3.87x sales belongs to a pre-boom regime and would have implied -55%; the recent
window's 9.73x implies +18.4%. Whether that is better is precisely the question §N.1 raises, and it
is the one place where the rule's uniformity is doing something a reader should look at rather than
trust.

### I.2 ADBE: the strongest result in the pilot

Two methods with **genuinely different denominators** - free cash flow and operating income - each
with its own 2-year observed panel and its own window, agree on Base fair value to **0.8%**:
248.23 against 250.23. D5-D1 established that ADBE's six multiples are not six independent readings
because five share a numerator; this pair is independent on the side that matters, and the agreement
is the cross-check D5-D0 §N meant by "two agreeing or disagreeing methods are a valuation".

Both say the same thing: at 250.50 Adobe is priced at its own recent median, TP1 is essentially the
current price, and the Bull case is +8.8%.

### I.3 GNW: already more than fully reflected

GNW trades at the **97th percentile** of its own two-year P/B range - 0.437x against a range of
0.302x to 0.459x. All three fair values sit below the current price: TP1 -12.6%, TP2 -3.6%, Bear
-23.3%. Even the Bull case does not reach today's price.

This is D5-D0 §N3's case reached from the other direction - "already reflects the full positive case"
- and it is the output shape that matters most for a framework that could otherwise only ever produce
upside. A valuation layer that cannot say "this is expensive relative to its own history" is a
screen, not a valuation.

### I.4 WBD and NATR

**WBD** sits at the 87th percentile of its recent EV/Sales range and the 96th of its full-panel
range. TP1 is -8.0% and TP2 is +0.6%, so the enterprise sales multiple says the equity is fully
valued. The EV/EBITDA cross-check agrees within 8%. The Bear anchor at -10.5% is the weakest number
in this row and §N.2 says why it should not be read as a downside estimate.

**NATR** is the only issuer whose Base and Bull multiples sit well above the current one, on a
FULL_2Y window the rule allowed because its rho is -0.436 rather than strong. Its P/FCF has spent
most of the panel between 10x and 25x and currently sits at the 19th percentile, which produces
+21.7% to TP1 and +68.0% to TP2. The P/B cross-check on an entirely separate denominator agrees
within 5% at the Base (16.73 against 15.93), which is the strongest corroboration available for an
issuer with no enterprise value. §N.3 is the counterweight.

---

## J. Abstentions

### J.1 CHRS and STAA: VALUATION_NOT_READY

Both are single-method issuers whose single method is P/B, and for both the model asserts
`NOT_SUITABLE`:

- **CHRS** is commercial-stage biopharma. Book equity is cash plus working capital; what the issuer
  is worth is the probability-weighted value of approvals and a pipeline. A multiple of book value
  answers neither question. This is D5-D0 §N's named case word for word: "for an asset-light issuer
  P/B is NOT_APPLICABLE on its own terms, which makes it NOT_READY." Its P/FCF was refused at D5-D1
  on staleness and its P/E does not resolve at the decision session.
- **STAA** is a single-franchise ophthalmic device maker. The manufacturing assets are real but are
  not what the market is paying for - the franchise is the approvals and the surgeon base. Free cash
  flow is negative, which removed the method that would have valued it, and §8's "data availability
  is not economic suitability" is exactly this situation: book value is the only thing left to divide
  by rather than the right thing.

Neither carries a target price, and the audit asserts it:

```
not_ready_issuer_carrying_a_target_price:  0 offenders
```

`NOT_READY_IS_AN_OUTPUT` governs. D4-BR is where this lesson was learned and the sentence is
unchanged: an honest abstention is an output, and only an empty output is a failure.

### J.2 GNW: the P/B-only issuer that is not an abstention

D5-D1 observed that all three single-method issuers had P/B as that method and framed it as "one
argument away from NOT_READY". The argument, made per issuer, does not come out the same way for all
three - and **GNW is valued on P/B as its primary method**.

For an insurance holding company, book equity **is** the earning asset. The balance sheet is the
business, net assets back the reserves, and price-to-book is the method the industry itself values
insurers on. P/B is not a reluctant fallback for GNW; it is the correct primary method, and the fact
that it is the only computable one is a coverage limitation rather than an economic one.

**This is the §24 finding.** Whether "P/B only" means `VALUATION_NOT_READY` turns on the business and
not on the method count. Two of the three single-method issuers abstain and one does not, and a rule
that abstained on all three - or valued all three - would have been wrong in one direction or the
other on this sample of three.

The counterweight is stated in the output rather than left to a reader: a 0.44x multiple is the
market's doubt about the long-term care reserves and it may well be right. A fair value computed from
percentiles of that same discount values the book at a multiple the market has paid before; it does
not assert the reserves are adequate, and if they are not, the book value itself is wrong and
everything derived from it with it. An insurer's GAAP book value also moves with accumulated other
comprehensive income, so a P/B percentile drawn across a period of rate moves is partly a rate
series.

---

## K. D3 / D4 Integration

**There is no D3 research output and no D4 expectation gap for any issuer in this pilot, and the
reason is structural rather than an omission.**

Measured over every stored artifact in `data/runtime/strategy_h_v2/d3*` and `d4*`, the tickers D4
graded are:

```
AEYE BSY COLL CRK DORM FG FRPT GOOG IDCC SCCO SPSC TG VRRM
```

Thirteen issuers, and the intersection with D5-D1's twelve is **empty**. That is a direct consequence
of D5-D0's own sampling decision, which was deliberate and is quoted here rather than second-guessed:
the D5 sample is "a seeded hash over the D2.1 package universe [...] No exclusion list and no
stratification by D3 or D4 outcome: whether valuation *data* exists has nothing to do with how a
candidate scored."

So for all seven issuers:

```
d3_context = D3_NOT_EVALUATED
d4_context = D4_NOT_EVALUATED
```

Per §26, D4's gap is never used as a number that adjusts a multiple, and here it is not used at all.
The consequences, stated plainly:

- `D4_NOT_EVALUATED` is **not** the same as `D4 UNKNOWN`. UNKNOWN is a measured absence of consensus
  for an issuer D4 examined; NOT_EVALUATED means D4 never ran. Treating them alike would let a
  valuation inherit a confidence it has no evidence for.
- The valuation-risk narratives in §I and §J therefore rest on the business economics and the
  code-owned fundamentals alone. Where a growth-durability or catalyst argument would normally come
  from D3 - ADBE's AI-displacement thesis is the obvious case - the document says that D5 cannot
  evaluate it rather than supplying a view.
- **This is the blocking prerequisite for D6**, and it is §Q.

---

## L. Confidence

`ValuationConfidence` is built as a **ceiling that each adverse finding lowers**, not as a score,
because a score would let two unrelated weaknesses cancel an unrelated strength. Every demotion
records its reason, so a confidence with no stated driver is unrepresentable.

| issuer | confidence | the drivers that fired |
|---|---|---|
| ADBE | MEDIUM | PEER_CONTEXT_UNAVAILABLE |
| WBD | MEDIUM | trend TRENDING_MODERATE on the contract window; PEER_CONTEXT_UNAVAILABLE |
| NATR | MEDIUM | trend TRENDING_MODERATE; PEER_CONTEXT_UNAVAILABLE |
| COHR | LOW | **single method**; PEER_CONTEXT_UNAVAILABLE |
| GNW | LOW | **single method**; trend TRENDING_MODERATE; PEER_CONTEXT_UNAVAILABLE |
| CHRS, STAA | NOT_READY | no economically suitable method produced a fair value |

```
HIGH 0    MEDIUM 3    LOW 2    NOT_READY 2
```

**No issuer can reach HIGH in this pilot, and that is a property of the step rather than of the
issuers.** `PEER_CONTEXT_UNAVAILABLE` demotes every issuer to MEDIUM at best, by rule, because the
only external check on whether an issuer's own multiple range is itself reasonable is absent. A
framework anchored entirely to an issuer's own two-year history cannot confirm that history was sane,
and the confidence rule is required to say so rather than to report HIGH on evidence it never had.

`stale_inputs` is empty for every issuer, and the reason is structural: D5-D1's gates refuse the
multiple when an input is stale, so a stale input cannot reach a published fair value. The parameter
exists on the rule anyway, so a later step whose chain could admit one does not have to change the
signature to report it.

`CONFIDENCE_IS_NOT_ATTRACTIVENESS` governs the reading: a HIGH-confidence valuation can say an issuer
is expensive and a LOW-confidence one can show large upside. The label is not a probability, because
nothing in D5 is calibrated against an outcome - D5 reads no outcomes.

---

## M. Manual Validation

All five valued issuers were recomputed by hand from the stored operands, in 40-digit decimal
arithmetic, independently of the module - §32 asks for a minimum of two.

**ADBE** (equity chain)

```
FCF per share   10,280,000,000 / 397,500,000       = 25.861635220125786...
Bear            25.861635220125786 x 8.158030156   = 210.98000000000002   published 210.98000000000002
Base = TP1      25.861635220125786 x 9.598231850   = 248.22597087179186   published 248.22597087179187
Bull = TP2      25.861635220125786 x 10.535683366  = 272.47000000000006   published 272.4700000000001
UpsideToTP1     248.22597087179187 / 250.50 - 1    = -0.0090779606        published -0.0090779606
UpsideToTP2     272.4700000000001 / 250.50 - 1     = +0.0877045908        published +0.0877045908
DownsideToBear  210.98000000000002 / 250.50 - 1    = -0.1577644711        published -0.1577644711
```

**ADBE's secondary cross-check** (enterprise chain, a different denominator and a different panel)

```
EV/EBIT P50     10.929424394598817                 observed 2026-04-15, RECENT_6M
implied EV      9,090,000,000 x 10.929424394598817 = 99,348,467,746.90325
implied equity  99,348,467,746.90325 - (-117,000,000) = 99,465,467,746.90325
Base            99,465,467,746.90325 / 397,500,000 = 250.22759181610880   published 250.2275918161088
vs primary      248.22597087179187 (P/FCF)         ratio 1.0081           CORROBORATES
```

The two use different denominators (operating income 9.09bn against free cash flow 10.28bn),
different chains (enterprise bridge against equity per share) and their own separately-selected
windows, and they land 0.8% apart.

**WBD** (enterprise chain - the bridge is the part worth checking)

```
implied EV      36,115,000,000 x 2.587984033       = 93,465,043,356.32873
implied equity  93,465,043,356.32873 - 28,654,000,000 = 64,811,043,356.32873
Base = TP1      64,811,043,356.32873 / 2,510,703,314 = 25.81389963319606  published 25.813899633196062
Bull = TP2      (36,115,000,000 x 2.757340956 - 28,654,000,000) / 2,510,703,314
                                                   = 28.24999999999999    published 28.25
UpsideToTP1     25.813899633196062 / 28.07 - 1     = -0.0803740779        published -0.0803740779
```

**COHR** (enterprise chain)

```
implied EV      7,118,181,000 x 9.730784198        = 69,265,483,192.30441
implied equity  69,265,483,192.30441 - 2,060,206,000 = 67,205,277,192.30441
Base = TP1      67,205,277,192.30441 / 195,832,246 = 343.17778897508232   published 343.17778897508236
UpsideToTP1     343.17778897508236 / 289.93 - 1    = +0.1836573965        published +0.1836573965
```

**NATR** (equity chain)

```
FCF per share   18,085,000 / 17,595,520            = 1.0278184446950133
Base = TP1      1.0278184446950133 x 15.494300761  = 15.925328110320935   published 15.925328110320937
Bull = TP2      1.0278184446950133 x 21.390771486  = 21.985829479213121   published 21.985829479213123
UpsideToTP2     21.985829479213123 / 13.09 - 1     = +0.6795897234        published +0.6795897234
```

**GNW** (equity chain, single method)

```
book per share  8,728,000,000 / 377,851,037        = 23.099050010017572
Base = TP1      23.099050010017572 x 0.381765234   = 8.8184142281831146   published 8.818414228183114
Bull = TP2      23.099050010017572 x 0.421269231   = 9.7309190384197623   published 9.730919038419762
UpsideToTP1     8.818414228183114 / 10.09 - 1      = -0.1260243580        published -0.1260243580
DownsideToBear  7.739135967220055 / 10.09 - 1      = -0.2329894978        published -0.2329894978
```

**Manual arithmetic defects: 0**, over 15 fair values and 15 upsides.

Ten automated defect classes, each reported as a list of named offenders rather than a count, so a
non-zero result names the issuer, the method and the leg. All ten empty:

```
fair value at the current multiple is not the current price      0
bear / base / bull not monotone                                  0
fair value range missing a leg                                   0
target multiple not an observation of the issuer's own panel     0
target multiple observed after the decision session              0
NOT_READY issuer carrying a target price                         0
non-positive fair value published                                0
upside not equal to its own formula                              0
fair value not equal to its own two stated operands              0
a price-only window governed a published target                  0
```

Three deserve a note on how they are checked rather than asserted:

- **"Target multiple is an observation"** is checked against the panel itself, as a set membership of
  `(session, multiple)` pairs - not against a recomputation. A percentile that returned an
  interpolated value, or attributed a real value to the wrong session, fails it.
- **"Fair value equals its own operands"** recomputes the chain from the *record*, with the exact
  arithmetic the chain uses, and requires bitwise equality. Division and multiplication are exact
  given the same operands, so any difference at all means the number did not come from its stated
  inputs.
- **Determinism** was checked by three independent full runs of the whole pilot. All three produce
  byte-identical numbers and verdicts. The second differs from the first only in COHR's secondary
  selection trace, for the specification fix §N.5 documents, and the third adds the
  `fair_value_range_missing_a_leg` class above; no published figure moved across any of them.

---

## N. Limitations

### N.1 The framework cannot tell that an issuer's whole multiple range is a bubble

This is the deepest limitation and it is a property of the method, not of the data. Every target
multiple is drawn from the issuer's **own** observed history, so the framework asks "what has the
market paid for this issuer" and never "was the market right to". When an entire two-year range sits
at an elevated level, a Base multiple drawn from it inherits that level and the fair value is
circular: the recent market becomes the arbiter of fair value.

**COHR is the live instance.** Its observed EV/Sales was 3.57x on the panel's first usable session,
troughed at 2.03x on 2025-04-04, peaked at 12.89x on 2026-06-02 and is 8.27x at the decision session
- a six-fold move from trough to peak inside two years. The contract window's median is 9.73x, and
9.73x revenue is an extremely high multiple for a capital-intensive hardware manufacturer by any
external standard. The published +18.4% to TP1 rests entirely on "the market recently paid 9.73x, so
9.73x is a fair level".

The two defences against this are peer context and a long history. **Both are unavailable**
(§F.4, §E.6), which is why COHR is LOW confidence and why no issuer reaches HIGH. A reader should
treat the COHR row as a demonstration that the arithmetic works, not as a view that COHR is cheap.

### N.2 A re-rating-only Bear does not price a fundamental deterioration

`METRIC_HELD_AT_CURRENT_TTM` holds the fundamental identical across all three scenarios, so the Bear
anchor is a multiple-compression scenario and nothing else. An issuer whose earnings fall will breach
its Bear anchor without the multiple compressing at all.

**WBD is where this bites hardest.** Its Bear anchor is -10.5%, for an issuer with a -3.5% operating
margin, declining linear revenue and net debt at 29% of enterprise value. A genuine bear case for WBD
is a revenue decline *and* a multiple compression, and -10.5% prices neither. The number is a correct
execution of the contract and it is not a downside estimate. The same caveat applies to COHR, whose
-6.1% Bear is the narrowest in the pilot while its denominator is the least verifiable.

### N.3 NATR's trend diagnostic is reading noise as drift

NATR's rank correlations are **-0.436 on FULL_2Y, +0.668 on RECENT_12M and -0.417 on RECENT_6M**. The
sign flips across windows, which means the multiple is oscillating rather than drifting, and
`TRENDING_MODERATE` on a sign-flipping series is a label applied to noise. The window rule still
behaves correctly - it only fires on TRENDING_STRONG, and NATR is not strong on the full panel - but
the diagnostic's label should not be read as a direction for this issuer.

The substantive caution on NATR is separate and larger: a +68.0% TP2 comes from a P90 of 21.39x
observed on 2025-02-12, on a free cash flow of 18.1m for a 230m issuer. At that size one
working-capital swing moves the multiple materially, and the Bull leg rests on an elevated
observation from 19 months before the decision. The P/B cross-check agreeing within 5% at the Base
is real corroboration and it does not extend to the Bull leg.

### N.4 Inherited and unrepaired

- **The unlisted-class blind spot**, from P1.1 and unchanged: a privately held or unlisted second
  common class is not observable in a listed-security reference, so `SINGLE_CLASS_CONFIRMED` cannot
  exclude one. Every per-share figure in this document divides by a share count that inherits it.
- **WBD's debt scope is not established by the filing**, from D5-D1 and unchanged: `LongTermDebt`
  32.023bn equals the exact sum of `LongTermDebtAndCapitalLeaseObligations` 30.530bn and its current
  portion 1.493bn, so whether a capital lease sits inside the total is a question the filing does not
  answer either way. **Every WBD fair value here inherits it through the net-debt bridge**, and at 29%
  net debt the inheritance is material rather than cosmetic.
- **NATR has no enterprise value at all.** Its newest borrowing balance is 899 days old and is never
  netted against 2026 cash, so net debt is `MISSING` rather than zero. The valuation is equity-side by
  necessity, and if NATR does carry debt this repository cannot see, every per-share figure for it is
  overstated.
- **COHR's income statement does not resolve as a unit.** Its operating income is 820 days old, so no
  margin regime is measurable, and whether 9.73x revenue is appropriate depends on a margin this
  repository cannot currently observe. §20 of D5-D1's brief forbids opening a repair for it and none
  was opened.
- **P/E's coverage is the binding constraint on method independence.** COHR's TTM diluted EPS resolves
  on 23 of 501 sessions and GNW's resolves on 200 but not at the decision session. Both issuers are
  single-method as a direct result, and both are LOW confidence as a direct result of that.

### N.5 Of this step's own making

- **The window rule is a rule, which is its strength and its cost.** A mechanical rule cannot be
  fitted, and it also cannot notice that GNW's RECENT_6M window is `TRENDING_STRONG` (+0.873) while
  its FULL_2Y is only moderate. The rule reads the full panel's trend only, so GNW is valued on
  FULL_2Y. Publishing every window's fair value is the mitigation rather than a fix.
- **The percentile levels and the thresholds are pre-registered and untested against any outcome.**
  `BEAR/BASE/BULL = P10/P50/P90`, `MIN_WINDOW_OBSERVATIONS = 60`,
  `MIN_DISTINCT_DENOMINATOR_PERIODS = 2`, `TREND_STRONG_RHO = 0.7`, `TREND_MODERATE_RHO = 0.4` and
  `VALUATION_CONFLICT_RATIO = 1.5` are conventional statements about distributions, trading calendars
  and materiality. None was tuned after seeing a result, and **none has ever been tested against an
  outcome of any kind**, because this step reads no returns. They are pre-registered, not validated.
- **`VALUATION_CONFLICT` never fired, so it is untested on real data.** All three reconciled issuers
  corroborate within 1.008x, 1.050x and 1.082x - far inside the 1.5x line. The branch is exercised by
  test and not by this sample, which means the pilot provides no evidence about whether 1.5x is the
  right line.
- **One specification bug was found and fixed during the run, and the fix is proven
  outcome-neutral.** COHR's declared `secondary_preference` was `("EV/Sales",)`, and when EV/Sales
  was promoted to primary nothing remained to cross-check against. The well-formed list is
  `("EV/Sales", "P/E")`. Because COHR's P/E panel has 23 observations against a 60-observation floor,
  P/E cannot be eligible as a secondary either, so the correction changes no number: the two reports
  are byte-identical across every field except COHR's secondary trace, which now records that P/E was
  considered and rejected on coverage. The fix is reported here rather than quietly applied, because
  a change to a selection table after seeing results is exactly the kind of edit that needs its
  neutrality demonstrated rather than asserted.
- **The model-owned judgements were authored by this session's model, with 0 live API calls.** §9
  permits using Claude Opus 5.5 for valuation-framework interpretation and does not require a
  separate call; the economic arguments in `METHOD_JUDGEMENTS` are text authored in-session, recorded
  in the source, and reviewable. The tradeoff is explicit: this keeps the pilot deterministic,
  free and reproducible, and it means the suitability arguments did not go through the
  pre-registration-and-live-confirmation machinery that D3.3 and D4 used for their model calls. A
  later step that wants that discipline for valuation judgements would have to add it.
- **The first ten sessions of the panel are unusable** because the earliest local reference snapshot
  is 2024-10-01 and the panel begins 2024-09-17. Fail-closed and not repaired.

---

## O. Tests

`backend/tests/strategy_h_v2/valuation/test_d5_d2_fair_value.py`, **58 tests**, all pure - every
input built by hand, every expected value computed in the assertion rather than by calling the module
twice.

| §36 requirement | covered by |
|---|---|
| single-method valuation | `test_a_single_method_valuation_is_allowed_but_is_low_confidence` |
| primary + secondary | `test_a_primary_and_secondary_within_the_ratio_corroborate` |
| negative / invalid method rejected | `test_enterprise_chain_refuses_rather_than_publishing_a_negative_fair_value`, `test_enterprise_chain_refuses_without_net_debt`, `test_equity_chain_refuses_without_shares` |
| Bear/Base/Bull deterministic arithmetic | `test_equity_chain_is_denominator_per_share_times_the_multiple`, `test_enterprise_chain_bridges_through_net_debt`, `test_per_share_chain_uses_no_share_count` |
| TP1 arithmetic | `test_tp1_is_the_base_leg_and_tp2_is_the_bull_leg` |
| TP2 arithmetic | `test_tp2_is_not_tp1_plus_a_margin_and_names_the_operand_that_moved` |
| upside calculation | `test_upside_is_the_contract_formula_against_the_unadjusted_close`, `test_a_base_multiple_under_the_current_one_yields_negative_upside_not_an_error` |
| valuation conflict | `test_a_wide_disagreement_raises_valuation_conflict_and_is_never_averaged` |
| P/B-only unsuitable abstention | `test_an_unvalued_issuer_is_not_ready_and_states_why` + §J's per-issuer assertions |
| correlated methods not counted independent | `test_the_same_denominator_with_immaterial_net_debt_is_one_reading_twice`, `test_the_same_denominator_with_material_net_debt_is_a_second_reading`, `test_a_correlated_secondary_does_not_buy_a_second_method` |
| no fabricated input | `test_percentile_is_an_element_of_the_series_never_an_interpolation`, `test_no_return_or_outcome_is_an_input_anywhere_in_the_module` |
| NOT_READY output | `test_an_unvalued_issuer_is_not_ready_and_states_why` |
| the window rule itself | `test_window_rule_takes_the_full_panel_when_it_does_not_trend_strongly`, `test_window_rule_takes_the_shortest_window_on_a_strong_drift`, `test_window_rule_does_not_crash_when_the_drift_is_not_measurable`, `test_window_rule_returns_none_when_no_window_is_eligible` |

Four tests are worth naming because they assert an absence rather than a behaviour, which is the only
kind of guarantee that survives a later edit:

- `test_no_return_or_outcome_is_an_input_anywhere_in_the_module` scans every function signature and
  every dataclass field in the module for `return`, `forward`, `realized`, `outcome`, `performance`,
  `pnl`, `alpha`, `excess`, `benchmark`, `future_price` and `subsequent`. Zero offenders.
- `test_no_dataclass_in_the_module_can_hold_a_recommendation` does the same for `buy`, `sell`,
  `approve`, `watch`, `reject`, `verdict`, `entry`, `exit`, `position`, `size`, `weight`, `signal`,
  `recommend` and `action`. Zero offenders, so D5 cannot grow a decision field by accident.
- `test_the_identity_catches_a_wrong_share_count` perturbs ADBE's share count by 1% and asserts the
  identity residual exceeds tolerance - a test of the test, because an invariant that cannot fail
  proves nothing.
- `test_percentile_median_of_an_even_series_is_not_the_mean_of_the_middle_two` pins nearest-rank
  behaviour against the interpolating default, which is the single easiest regression to introduce by
  swapping in a library percentile.

### Regression

```
backend/tests/strategy_h_v2/valuation           433 passed
backend/tests/strategy_h_v2 + strategy_h0     1,667 passed,  1 skipped
whole suite (backend/tests)                   7,614 passed, 24 skipped, 1 failed
```

Two findings in the whole-suite run, and **neither is this step's**. Both are reported rather than
waved past, because "pre-existing" is a claim that has to be shown.

**Two modules fail to collect.** `backend/tests/strategy_b/test_strategy_b_scanner.py` and
`backend/tests/test_strategy_b_historical_scanner.py`, both with
`ImportError: cannot import name 'OBSERVATION_ONLY' from 'app.strategy_b.scanner'`. Both are `??` in
`git status` - another session's working files - and neither imports anything this step touches. They
were excluded from the counted run, and were not modified, repaired or staged.

**One test failed:**
`test_massive_year_feasibility.py::test_every_long_range_request_passes_the_basic_plan_limiter`. The
evidence that it is not this step's:

```
the module is untracked (??), another session's working file
it references nothing this step added: 0 hits for "fair_value" or "d5_d2"
nothing in the repository imports this step's new modules except this step's own runner and tests
the assertion is sleeps == approx([12.0 * n for n in range(1, pages)], abs=1.0)
  - a wall-clock rate-limiter interval, with a one-second tolerance
the suite ran concurrently with this step's 2.5-minute pilot run at 85% CPU
in isolation: 1 passed; and the whole module 50 passed, three consecutive runs
```

So it is a timing-sensitive assertion that failed under CPU contention this step caused by running
the pilot at the same time, not a behaviour this step changed. The honest statement is that the
whole-suite number above contains one environment-dependent failure in another session's untracked
module, reproducible only under load.

---

## P. Verdict

```
H-V2-D5-D2
=
READY WITH LIMITATIONS
```

Against §31's practical success criteria:

| criterion | required | measured |
|---|---|---|
| issuers producing defensible fair value ranges | at least 2 | **5** |
| fabricated valuation inputs | 0 | **0** |
| target prices reproduce from code-owned arithmetic | all | **all 15, bitwise, plus 15 upsides** |
| abstention works when evidence is insufficient | yes | **2 of 7, both argued per issuer** |

READY rather than READY FOR DECISION ENGINE, and the reason is §K rather than anything in the
arithmetic. The valuation layer is correct and reproducible; it has **no D3 or D4 counterpart on any
issuer it can value**, so the thing D6 exists to do cannot be done on this sample.

What is established:

- A fair value range and a target price can be produced from observed multiples only, with every
  operand carrying provenance, and the result reproduces bitwise from its stated inputs.
- The framework abstains on economic grounds rather than on method count, and the P/B-only question
  resolves differently for a biopharma, a device maker and an insurer.
- It can say an issuer is expensive. GNW's entire fair value range sits below its price.
- The window choice is the dominant design decision, worth up to +234 percentage points of
  manufactured upside on a single leg, and it is made by a mechanical rule with every alternative
  published.

What is not established:

- That any target multiple level is *right*. Nothing here is validated against an outcome, and the
  framework cannot detect that an issuer's whole observed range is elevated (§N.1).
- That the Bear anchors are downside estimates. They are multiple-compression scenarios only (§N.2).
- That 1.5x is the right conflict line, or that P10/P50/P90 are the right percentiles. Pre-registered,
  never tested (§N.5).

---

## Q. D6 Authorization

```
H-V2-D6
FINAL DECISION ENGINE
= AUTHORIZED TO DESIGN
= NOT AUTHORIZED TO PRODUCE A VERDICT ON THIS SAMPLE
```

§35 authorizes D6's design on a READY-class D5-D2, and the design is authorized. The contract D6 would
implement is now fully specified on the D5 side: D5 hands over a Bear/Base/Bull range, TP1, TP2, a
BEAR_ANCHOR, three upsides, a reconciliation status and a confidence with stated drivers, and D5-D0
§M governs how it combines - a cheap valuation does not promote a NEGATIVE gap, an expensive one does
not demote a POSITIVE gap, and a disagreement is a finding for D6 rather than something D5 resolves.

**The blocking prerequisite.** D6's whole purpose is

```
D3 company quality  +  D4 expectation gap  +  D5 valuation  ->  APPROVE / WATCH / REJECT
```

and the join is **empty**. D4 graded 13 issuers, D5 valued 5, and the intersection is 0. D6 cannot
produce a verdict on any issuer today - not because its logic is unready but because no issuer has
both inputs. Running D6 on the five valued issuers would mean combining a real valuation with
`D4_NOT_EVALUATED`, which is an absence of evidence rather than a neutral reading, and `D4_NOT_EVALUATED`
is not `D4 UNKNOWN` (§K).

So the next step is one of these, and it is the user's decision rather than this step's:

1. **Run D5-D2's valuation on the issuers D4 already graded.** The cheapest path by a wide margin:
   the thirteen D4 tickers already have expectation-gap output, and this step's machinery is
   issuer-agnostic - it needs a D2.1 package, companyfacts and the price panel, all of which those
   issuers have. The cost is that the valuation sample would then be selected by D4's coverage
   rather than by a blind seeded hash, and that selection effect must be recorded.
2. **Run D3 and D4 on the five valued issuers.** Preserves the blind D5 sample and costs live model
   calls on seven new issuers plus D3's research pass.
3. **Design D6 against the contract and leave it unexecuted**, as D5-D0 was designed and unexecuted
   before D5-D1 ran.

What D6 may not do, carried forward unchanged: no entry level, no exit level, no position size, no
target price fitted to a return, and no promotion of a candidate on valuation alone.

```
BUY / SELL                           NO
APPROVE / WATCH / REJECT             NO
entry / exit / position size         NO
forward returns read                 NO
D5-D1 verdicts modified              NO
new primitive project opened         NO
live model calls                     0
cost                                 $0
push                                 NO
```
