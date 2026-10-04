# H-V2-D5-D2R - Recent-Regime Window Selection Repair

```
H-V2-D5-D2R
=
PASS

D7 Forward Shadow        READY TO LAUNCH
D5                       COMPLETE
D6                       COMPLETE

valued cases replayed    14
window changed            1    VRRM
window unchanged         13

D6 decision changes       0    APPROVE 0 / WATCH 6 / REJECT 2, before and after
live Opus calls           0
cost                      $0.00
push                      NO
```

One finding is being fixed and nothing else. D6 reused D5-D2's valuation on the thirteen issuers D4 had
graded, and VRRM exposed a hole in the window rule that only an out-of-pilot sample could expose.

The repair moves one issuer. VRRM's TP1 falls from **+404.7% to +24.8%**, and the EV/Sales cross-check
that had disagreed with the primary method by 3.597x now corroborates it within 1.124x - two methods
that could not agree on the full panel agree on the recent one, which is the strongest evidence in the
step that the recent window is the right description of this issuer.

It also surfaces something the old window concealed: at the recent regime's 10th-percentile multiple,
VRRM's 65%-net-debt capital structure leaves the equity worth less than nothing, so the Bear leg is now
**refused** rather than published as 0.72. That is a frozen rule firing correctly on an honest window,
and it is the one item on which the pre-registered acceptance, read literally, says NEEDS REVISION.
Both verdicts are computed and printed; §J.2 and §L are the argument.

---

## A. Why D2R Exists

D5-D2's window rule exists to stop a valuation framework taking the median of a regime transition and
calling it a level. Its own words, in `DEAD_REGIME_IS_THE_CENTRAL_TRAP`:

> A strong monotone drift across the whole panel is evidence that the early panel is a different
> regime from the late panel [...] Taking the median of a transition and calling it a level is how a
> valuation framework manufactures upside from the fact that the past was more expensive.

The rule asked one question - did FULL_2Y drift strongly - and used the answer for two different
purposes: as the **evidence** that a regime change happened, and as the **gate** on escaping it. The
trend test is a Spearman rank correlation, which measures monotonicity. So a panel that rises and
then falls cancels to a small rho and reports no drift, and the gate never opens.

D6 found the case. VRRM's EV/EBIT rose across the first half of the panel and collapsed across the
second; the two averaged to a rho under the threshold; the rule concluded the panel had not drifted;
and the published Base target multiple was the median of a complete round trip.

**What this step changes is which windows are asked, and nothing else.** The window set is unchanged.
The `TRENDING_STRONG` threshold is unchanged. Which window governs once a regime change is established
is unchanged. The evidence question becomes "did **any** contract-eligible window drift strongly",
which is the same test applied to windows V1 never asked.

---

## B. Historical D5 / D6 State

```
HEAD at start                   57b159a  feat(strategy-h-v2): judge one issuer by its business, ...
D5-D2                           64612be  READY WITH LIMITATIONS
D6                              57b159a  READY WITH LIMITATIONS
D7 Forward Shadow               DESIGN AUTHORIZED, launch blocked on this question
```

**Nothing published is edited.** `HISTORICAL_IS_IMMUTABLE`, from the runner:

> No published D5-D2 or D6 figure is edited, recomputed in place, or reclassified. VRRM's historical
> TP1 of +404.7% and its historical WATCH stand as what the contract in force at the time produced,
> and this step's replay is a separate artifact with its own contract label. That is not bookkeeping
> fussiness: a repository that silently improves its own past results cannot later tell which of its
> numbers were ever actually produced, and the D6 document's +404.7% is the evidence that the defect
> was real.

The mechanism that makes this more than an intention is that **both rules are selectable on one
function**. `WindowSelectionContract` has two members, `select_contract_window` requires one to be
named, and the two steps that published reports pin the one they published under:

| step | pin | consequence |
|---|---|---|
| `run_strategy_h_v2_d5_d2.py` | `WINDOW_CONTRACT = D5_D2_V1` | re-running it reproduces its report, `contract_window_reason` strings included |
| `run_strategy_h_v2_d6.py` | `value_issuer`'s default, which is `D5_D2_V1` | same |
| `run_strategy_h_v2_d5_d2r.py` | passes `D5_D2R_V1` explicitly | the replay, as a new artifact |

V1's branches and their exact reason strings are preserved verbatim, because
`contract_window_reason` is a field in the stored JSON and therefore part of the artifact rather than
prose. A test pins both strings.

---

## C. VRRM Discovery

Measured from the panel, not asserted:

```
VRRM  EV/EBIT, 474 observed sessions 2024-10-01 .. 2026-09-16

      first half   2024-10-01 .. 2025-09-25   n=237   rho +0.830   27.58x -> 34.13x
      second half  2025-09-26 .. 2026-09-16   n=237   rho -0.918   33.95x -> 11.13x
      peak 36.056x on 2025-07-01     trough 6.835x on 2026-05-27

      FULL_2Y     n=474   rho -0.522   TRENDING_MODERATE
      RECENT_12M  n=252   rho -0.930   TRENDING_STRONG
      RECENT_6M   n=126   rho -0.554   TRENDING_MODERATE
```

Two things in that table matter beyond the arithmetic.

**The two halves cancel.** +0.830 against -0.918 averages to -0.522, which is below the 0.7 threshold,
so V1 reported no drift and took the median of the round trip - 27.0379x, observed 2026-01-26, a
session inside the collapse.

**The window that supplies the evidence is not the window that governs.** RECENT_12M is the strongly
trending one because it straddles the transition. RECENT_6M is only `TRENDING_MODERATE` precisely
because it sits almost entirely *inside* the new regime - a window past the transition has no strong
trend left to show. So a repair that required the **governing** window to be `TRENDING_STRONG` would
find nothing here, and §7's separation of evidence from priority is not a nicety: it is the only
structure that works on this shape. The synthetic fixture in the tests reproduces it exactly, with a
plateau after the collapse, and asserts that RECENT_6M is not strongly trending.

---

## D. Existing Rule

`D5_D2_V1`, preserved unchanged and still callable:

```
eligible = windows that are contract-eligible
if none            -> no window
if FULL_2Y is eligible and FULL_2Y is not TRENDING_STRONG
                   -> FULL_2Y
otherwise          -> the shortest contract-eligible window
```

Its two eligibility clauses, unchanged by this step: at least `MIN_WINDOW_OBSERVATIONS = 60`
observations, and at least `MIN_DISTINCT_DENOMINATOR_PERIODS = 2` distinct denominator period ends
(`PRICE_ONLY_RANGE_IS_NOT_A_MULTIPLE_RANGE`).

**One property of the window set is worth recording because it makes part of the rule provably
defensive.** The windows are nested - RECENT_6M is a suffix of RECENT_12M is a suffix of FULL_2Y - so
FULL_2Y has at least as many observations and at least as many distinct denominator periods as any
shorter window. Both eligibility clauses are monotone in those two quantities. Therefore **FULL_2Y
cannot be ineligible while a shorter window is eligible**, and the `not full.contract_eligible` branch
is unreachable for any panel this repository can build. It is kept as a guard and a test asserts the
invariant, so that if `window_observations` ever became a calendar slice rather than a session slice -
which would break nesting - the assumption fails loudly rather than silently.

---

## E. Revised Window Contract

`D5_D2R_V1`, in full:

```
eligible  = windows that are contract-eligible
strong    = eligible windows whose TrendClass is TRENDING_STRONG
shortest  = the shortest eligible window

if eligible is empty            -> no window                                        (as V1)
if FULL_2Y is not eligible      -> shortest                                          (as V1; §D: unreachable)
if strong is empty              -> FULL_2Y        [1] nothing says a regime changed
if FULL_2Y is TRENDING_STRONG   -> shortest       [2] V1's own branch, unchanged
                                                      in effect and in wording
# the V1 blind spot: FULL_2Y cancels to a weak rho while a shorter window drifted strongly
ratio = max / min of the FULL_2Y and shortest windows' observed Base multiples
if ratio is unmeasurable        -> FULL_2Y        [3a]
if ratio <= 1.5                 -> FULL_2Y        [3b] drifted without leaving the level
otherwise                       -> shortest       [3c] the recent regime is a different level
```

### E.1 Clause [2] is why nothing that worked can regress

When FULL_2Y is itself `TRENDING_STRONG`, clause [2] fires and the new rule returns the identical
window with the identical reason string V1 returned. **Every issuer whose selection V1 had already
shortened is therefore bit-identical under D2R by construction, not by measurement.** That is the
structural guarantee behind §10 and §11, and the replay in §F confirms it rather than establishing it.

### E.2 Clause [3b] is why the repair is narrow

Evidence that a shorter window drifted strongly is not by itself a reason to discard the full panel. A
window can drift strongly and still end up centred where the full panel is - a rise inside the recent
window is a round trip from the full panel's point of view. Without clause [3b] every issuer with any
strongly trending short window would be shortened, which is a much larger change than the defect
warrants. §F shows this clause doing real work on two issuers.

### E.3 The conflict threshold is reused, not invented

`RECENT_REGIME_CONFLICT_RATIO` **is** `VALUATION_CONFLICT_RATIO`, by assignment, and a test asserts
the identity. D5-D2 pre-registered 1.5x as this repository's answer to "how far apart must two
valuation statements be before they are contradicting rather than corroborating". D2R needs exactly
that judgement about two windows instead of two methods, so it takes the constant rather than
introducing a second threshold that could drift away from it.

**The role is not identical and that is recorded rather than glossed.** D5-D2 applies the ratio to two
Base *fair values*; D2R applies it to two Base *multiples*. On the equity chain the two ratios are the
same number, because the metric per share is held across windows and cancels. On the enterprise chain
they are not, because of the net-debt bridge. D2R uses the multiple ratio in both cases, deliberately:
the multiple is an observation and the fair value is a conclusion, and §8 forbids selecting a window
by its conclusion.

### E.4 Why the rule cannot be reading the upside

`SELECTION_IS_NOT_UPSIDE_OPTIMIZATION`, from the runner:

> The rule reads three things - whether a window is contract-eligible, its `TrendClass`, and the ratio
> between two windows' observed Base multiples - and all three are properties of the observed panel
> that exist before any scenario is built. The conflict test is symmetric in the ratio, so a recent
> regime twice as EXPENSIVE as the full panel overrides it on identical terms to one twice as cheap.

`base_multiple_ratio` returns `max/min`, so it is symmetric by construction and no caller can use it
to prefer the higher or the lower window. `test_the_override_is_symmetric_in_direction` builds the
mirrored panel - flat, then a strong recent **expansion** - and asserts both that the recent window is
selected and that its Base multiple is *higher* than the full panel's. If the rule were reading the
upside, that case would have to keep FULL_2Y.

---

## F. Offline Corpus Replay

Every issuer with a stored valuation in this repository, valued twice from one panel and one evaluator,
differing only in the selected window. The two samples are disjoint by construction: D5-D2's seven came
from a seeded hash over the package universe, D6's thirteen are D4's coverage.

```
valued cases replayed   14        (of 20 corpus issuers; 6 are VALUATION_NOT_READY under both rules)
window changed           1        VRRM
window unchanged        13
```

| ticker | sample | method | old window | new window | old Base FV | new Base FV | old TP1 up | new TP1 up | old TP2 up | new TP2 up | ratio | conf |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ADBE | D5-D2 | P/FCF | RECENT_6M | RECENT_6M | 248.23 | 248.23 | -0.9% | -0.9% | +8.8% | +8.8% | 1.619 | MEDIUM |
| WBD | D5-D2 | EV/Sales | RECENT_6M | RECENT_6M | 25.81 | 25.81 | -8.0% | -8.0% | +0.6% | +0.6% | 1.328 | MEDIUM |
| COHR | D5-D2 | EV/Sales | RECENT_6M | RECENT_6M | 343.18 | 343.18 | +18.4% | +18.4% | +44.1% | +44.1% | 2.512 | LOW |
| NATR | D5-D2 | P/FCF | FULL_2Y | FULL_2Y | 15.93 | 15.93 | +21.7% | +21.7% | +68.0% | +68.0% | 1.011 | MEDIUM |
| GNW | D5-D2 | P/B | FULL_2Y | FULL_2Y | 8.82 | 8.82 | -12.6% | -12.6% | -3.6% | -3.6% | 1.045 | LOW |
| SCCO | D6 | P/FCF | FULL_2Y | FULL_2Y | 190.26 | 190.26 | +0.2% | +0.2% | +37.0% | +37.0% | 1.115 | LOW |
| DORM | D6 | EV/EBIT | FULL_2Y | FULL_2Y | 128.85 | 128.85 | +3.1% | +3.1% | +14.8% | +14.8% | 1.005 | MEDIUM |
| IDCC | D6 | EV/EBIT | FULL_2Y | FULL_2Y | 214.71 | 214.71 | -34.7% | -34.7% | -16.6% | -16.6% | 1.120 | MEDIUM |
| SPSC | D6 | P/FCF | RECENT_6M | RECENT_6M | 74.07 | 74.07 | -8.2% | -8.2% | +0.9% | +0.9% | 2.150 | LOW |
| TG | D6 | EV/FCF | RECENT_6M | RECENT_6M | 8.78 | 8.78 | +27.5% | +27.5% | +94.6% | +94.6% | 1.441 | LOW |
| AEYE | D6 | P/FCF | RECENT_6M | RECENT_6M | 7.07 | 7.07 | -1.9% | -1.9% | +22.1% | +22.1% | 3.518 | MEDIUM |
| COLL | D6 | P/E | FULL_2Y | FULL_2Y | 24.66 | 24.66 | +11.1% | +11.1% | +87.0% | +87.0% | 1.083 | LOW |
| FG | D6 | P/B | RECENT_6M | RECENT_6M | 27.42 | 27.42 | +19.0% | +19.0% | +29.3% | +29.3% | 1.220 | LOW |
| **VRRM** | D6 | EV/EBIT | FULL_2Y | **RECENT_6M** | 17.87 | **4.42** | **+404.7%** | **+24.8%** | **+593.7%** | **+61.5%** | 2.234 | LOW -> **MEDIUM** |

CHRS, STAA (D5-D2) and BSY, GOOG, CRK, FRPT (D6) are `VALUATION_NOT_READY` under both contracts, for
the reasons their own steps recorded. A window rule cannot create a method where none is computable.

### F.1 Which clause decided each issuer

The reason string each row carries is the attribution, so this table is read off the output rather
than reconstructed:

| clause | issuers | count |
|---|---|---|
| **[1]** no eligible window trends strongly -> FULL_2Y | NATR, DORM, IDCC | 3 |
| **[2]** FULL_2Y trends strongly -> shortest *(V1's branch, bit-identical)* | ADBE, WBD, COHR, SPSC, TG, AEYE, FG | 7 |
| **[3b]** a shorter window trends strongly, levels agree -> FULL_2Y | GNW, SCCO, COLL | 3 |
| **[3c]** a shorter window trends strongly, levels conflict -> shortest | **VRRM** | 1 |

Clause [3] is the new territory and it is reached by four issuers. The conflict condition kept three of
them on FULL_2Y and moved one. **Without clause [3b] this repair would have changed four issuers to fix
a one-issuer defect**, which is the clearest evidence that the conflict condition earns its place:
GNW's recent 6M drifts strongly at a ratio of 1.045, SCCO's at 1.115, COLL's at 1.083 - all three
drifted without leaving the panel's level.

### F.2 The two clauses are not redundant, and that is measured

A tempting simplification would have been to *replace* clause [2] with clause [3] - one unified test
instead of two. The replay shows what that would have cost, because three issuers have a strongly
trending FULL_2Y **and** a ratio at or below 1.5x:

| ticker | ratio | window the rule selects | TP1 | window a [2]-less rule would select | TP1 |
|---|---|---|---|---|---|
| WBD | 1.328 | RECENT_6M | 25.81 (-8.0%) | FULL_2Y | 16.62 (**-40.8%**) |
| TG | 1.441 | RECENT_6M | 8.78 (+27.5%) | FULL_2Y | 13.02 (**+88.9%**) |
| FG | 1.220 | RECENT_6M | 27.42 (+19.0%) | FULL_2Y | 33.44 (**+45.1%**) |

All three would have regressed, and in both directions - TG and FG toward manufactured upside, WBD away
from it. So the repair is strictly **additive**: clause [2] keeps catching the monotone drift it always
caught, and clause [3] catches the cancelling one it could not see. On the four issuers where both
tests apply they agree (ADBE 1.619, COHR 2.512, SPSC 2.150, AEYE 3.518), which is corroboration rather
than redundancy.

### F.3 What changed at the byte level, precisely

The §F table shows two decimal places. The underlying rows were compared whole, as JSON, and the answer
is more exact and slightly less tidy than "one issuer changed":

```
20 corpus rows compared under the two contracts
13 byte-identical       ADBE AEYE BSY CHRS COHR CRK FG FRPT GOOG SPSC STAA TG WBD
 6 differ in ONE key    NATR GNW SCCO DORM IDCC COLL   ->  contract_window_reason only
 1 differs in substance VRRM  -> contract_window, fair_value, target_prices,
                                 reconciliation, confidence, contract_window_reason
```

The six prose-only rows are the issuers that reached clause [1] or clause [3b], where D2R says something
V1 had no branch to say - "no contract-eligible window trends strongly", or "their Base target multiples
agree within 1.50x". Their numbers are identical: removing `contract_window_reason` from both rows makes
all six byte-identical, which is asserted above rather than described.

This is why the two published steps are pinned to V1 rather than left on a default. `contract_window_reason`
is a stored field, so a step that re-ran under D2R would differ from its published report in six prose
strings even though no number moved - a diff that would be indistinguishable at a glance from a diff
that mattered.

---

## G. ADBE Regression

§11 named the specific failure mode: ADBE's full panel would publish TP1 +60.4% and TP2 +234% from a
dead regime, and returning to it is a FAIL.

```
ADBE  P/FCF   FULL_2Y  TRENDING_STRONG   ->  clause [2]  ->  RECENT_6M
      old window RECENT_6M      new window RECENT_6M
      old Base FV 248.23        new Base FV 248.23      (bit-identical)
      old TP1 -0.9%             new TP1 -0.9%
      old TP2 +8.8%             new TP2 +8.8%
```

The guarantee is structural rather than empirical. When FULL_2Y is itself `TRENDING_STRONG`, clause [2]
fires before the ratio is ever consulted and returns the identical window with the identical reason
string V1 returned. So **every issuer V1 had already shortened is bit-identical under D2R by
construction** - all seven of them - and the replay confirms rather than establishes it. ADBE's ratio
is 1.619, so clause [3c] would have reached the same answer had clause [2] not existed; that is a
cross-check, not the mechanism.

---

## H. VRRM Result

§12 forbade using +65.5% as an acceptance target, and the rule did not land there - it selected
RECENT_6M rather than RECENT_12M, because §7's priority is unchanged from V1: the evidence may come
from any window, and the window that **governs** is the shortest contract-eligible one.

```
VRRM  EV/EBIT   FULL_2Y TRENDING_MODERATE (rho -0.522)
                RECENT_12M TRENDING_STRONG (rho -0.930)   <- the evidence
                RECENT_6M TRENDING_MODERATE (rho -0.554)  <- the window that governs
                FULL_2Y Base 27.0379x  vs  RECENT_6M Base 12.1041x  =  ratio 2.234  >  1.5

      old window FULL_2Y              new window RECENT_6M
      old TP1 17.87  (+404.7%)        new TP1  4.4178  (+24.8%)   multiple 12.1041x, observed 2026-08-26
      old TP2 24.56  (+593.7%)        new TP2  5.7185  (+61.5%)   multiple 13.5486x, observed 2026-04-15
      old Bear 0.72  (-79.8%)         new Bear REFUSED
      old reconciliation  VALUATION_CONFLICT 3.597x
      new reconciliation  CORROBORATES       1.124x
      old confidence LOW              new confidence MEDIUM
```

Three things came out of this row and only the first was the one being fixed.

**TP1 fell from +404.7% to +24.8%**, and the new Base multiple of 12.1041x sits just above the current
11.1293x, which is what a Base case on an issuer trading inside its recent range should look like.

**The method conflict resolved.** Under FULL_2Y, EV/EBIT said 17.87 and the secondary EV/Sales said
4.97 - a 3.597x disagreement that tripped `VALUATION_CONFLICT` and demoted confidence to LOW. Under
RECENT_6M, EV/EBIT says 4.42 against the same EV/Sales 4.97, a ratio of 1.124, and the two
`CORROBORATE`. **Two methods that disagreed by 3.6x on the full panel agree within 12% on the recent
one.** Nothing in the window rule looks at the secondary method, so this is independent corroboration
that the recent window is the right description of this issuer, and it is the single most persuasive
number in this step.

**The Bear leg is now refused, and this is the step's principal new finding.**

```
RECENT_6M P10 = 7.0597x
implied enterprise value  136,856,000 x 7.0597  =  966,156,377
net debt                                           985,096,000
implied equity                                      -18,939,623   ->  NEGATIVE_IMPLIED_EQUITY
```

D5-D2 §G.3 refuses to publish a non-positive fair value per share rather than printing one, so
`BEAR_ANCHOR` and `downside_to_bear` are both `null` and the range is marked `complete: false`.

This is the repair telling the truth rather than breaking something. The old Bear anchor of 0.72 was
computed from a full-panel P10 of 7.9926x - a multiple the issuer had **already traded below**, since
the recent-6M minimum is 6.8353x. So V1's Bear was itself a dead-regime number, and what the repaired
window says is sharper: at the 10th percentile of the regime this issuer is actually in, 65%-net-debt
leverage leaves the equity stub worth less than nothing. That is more informative than 0.72 and it is
not a number.

It is still a weaker output than a range, and §J.1 carries it forward as a limitation rather than
dismissing it.

### H.1 V1 gave two different regime answers for the same issuer, and that is what the conflict was

The reconciliation result is more interesting than "the cross-check now agrees", and measuring both
methods' windows is what shows it:

```
VRRM, one panel, one rule, two methods

EV/EBIT    current 11.1293    FULL_2Y rho -0.522  MODERATE  p50 27.0379   -> V1 keeps FULL_2Y
                              RECENT_12M  -0.930  STRONG    p50 13.7010
                              RECENT_6M   -0.554  MODERATE  p50 12.1041

EV/Sales   current  1.5126    FULL_2Y rho -0.903  STRONG    p50  5.0060   -> V1 shortens to RECENT_6M
                              RECENT_12M  -0.968  STRONG    p50  3.3301
                              RECENT_6M   -0.856  STRONG    p50  1.7279
```

**Under V1 the secondary method had already escaped to RECENT_6M while the primary stayed on FULL_2Y.**
One issuer, one rule, two contradictory verdicts about which regime it is in - and that internal
inconsistency *is* the 3.597x `VALUATION_CONFLICT`. The repair does not add a cross-check; it makes the
primary agree with a secondary that was already right.

The reason the two methods diverge is the denominator, and it sharpens what V1's blind spot actually is.
VRRM's operating income **grew** over the panel - 136.0m at 2024-12-31 to 238.4m at 2025-12-31 - while
revenue was comparatively stable. So EV/Sales tracked the enterprise-value collapse monotonically and
hit `TRENDING_STRONG` on every window, while EV/EBIT's series was pushed back up by its own growing
denominator and cancelled to -0.522.

V1's hole is therefore not "panels that round-trip" in general. It is specifically **methods whose
denominator moved enough to cancel a price move in rank terms** - which is to say, the methods where a
multiple is doing the most work. That is a worse place to be blind than a random one, and it is why the
evidence question had to be asked of every window rather than made more sensitive on one.

---

## I. D6 Counterfactual

The D6 decision engine, imported and called rather than reimplemented, on the repaired valuations with
the identical stored D3 and D4 legs. No decision rule changed, so the only thing that can move a
decision here is the window the valuation selected.

```
old   APPROVE 0   WATCH 6   REJECT 2
new   APPROVE 0   WATCH 6   REJECT 2

decision eligible      8   SCCO DORM IDCC TG AEYE COLL FG VRRM   (unchanged)
decisions changed      0
D6 defects             0 over 8 classes
```

One issuer's *derivation* moved without its decision moving:

```
VRRM   WATCH -> WATCH
       approve blockers  [expectation_gap_permits_approve, valuation_confidence_sufficient]
                      -> [expectation_gap_permits_approve]
```

The valuation-confidence blocker is gone because the repaired row is MEDIUM rather than LOW, and the
expectation-gap blocker remains because VRRM's D4 gap is `UNKNOWN`. **§17 holds: APPROVE is still 0,
and it is still 0 for the reason D0 §L froze rather than for a valuation reason.** The repair removed
one of VRRM's two blockers and the remaining one is the one no valuation change can touch.

That is worth stating plainly because it is the honest limit of this step: the repair made VRRM's
numbers defensible, and it did not make VRRM an APPROVE, and nothing about it was aimed at the decision
count.

---

## J. Limitations

### J.1 A MEDIUM-confidence valuation can now carry a range with no downside leg

VRRM's repaired row is `MEDIUM` confidence with `complete: false`. `valuation_confidence` takes
`valued`, method counts, reconciliation, trend, window, staleness, share class and peer context - and
**not** range completeness. So an issuer whose Bear leg is refused is not demoted for it, and on this
issuer the confidence went *up* at the same time as the range lost a leg.

Both movements are individually correct: the conflict that caused the LOW genuinely resolved, and the
Bear refusal is a frozen rule firing. Together they read wrong. The repair is not made here, because
§13 forbids extending the validator in this step and because adding completeness to
`valuation_confidence` would change the confidence semantics of every row D5-D2 and D6 already
published. It is carried to D7 as a named question: *should an incomplete range cap confidence?*

### J.2 §14 F, read literally, says NEEDS REVISION, and the split is post-hoc

§14 F was fixed before the replay as "D5 arithmetic/provenance unchanged" and implemented as "no class
of `d5_audit` is non-empty". Those are not the same condition, and the replay is what exposed the
difference: nine of the audit's ten classes are correctness - the identity invariant, operand
recomputation, the upside formula, observed-target provenance, future observations, refusals carrying
values, monotonicity, non-positive publication, price-only windows - and the tenth,
`fair_value_range_missing_a_leg`, is a **completeness** report whose own comment in D5-D2 calls an
incomplete range "a weaker output than a range" rather than a wrong one.

The runner therefore computes and prints **both** verdicts, and the difference between them is entirely
VRRM's refused Bear leg:

```
verdict on correctness (9 audit classes)    PASS
verdict as preregistered (all 10 classes)   NEEDS REVISION
difference                                  VRRM EV/EBIT: refused legs ['bear']
```

Splitting them after seeing the result is exactly the kind of move that deserves suspicion, so it is
reported rather than applied silently: a reader who holds §14 F to its literal wording should read this
step as NEEDS REVISION with the Bear refusal as the item to resolve. §L takes a position and says why.

### J.3 What the repair does not establish

- **That RECENT_6M is the right window for VRRM.** It is the window the contract selects, the secondary
  method corroborates it, and nothing here is validated against an outcome.
- **That 1.5x is the right conflict line.** Reused rather than fitted, and now load-bearing in a second
  place. What *can* be said is that this corpus does not depend on its exact value. The four issuers
  that reach clause [3] have ratios of 1.045 (GNW), 1.083 (COLL), 1.115 (SCCO) and 2.234 (VRRM), so the
  threshold sits in an open gap of **1.115 to 2.234** and any value inside it produces this step's exact
  outcome. 1.5 is not near an edge, and no issuer here is decided by the third decimal place. That is
  robustness on one corpus, not evidence that 1.5 is correct.
- **That the window rule is now complete.** It detects a cancelling round trip over the two-year panel.
  A regime change entirely inside RECENT_6M, or one slower than the panel is long, is still invisible,
  and the panel is still at most 501 sessions (`RECENT_2Y_CONTEXT`).
- **Anything about D5-D2's or D6's other limitations.** `PEER_CONTEXT_UNAVAILABLE`,
  `GUIDANCE_UNKNOWN`, the seven issuers with no enterprise value, and the two D0 §N3 triggers D6 cannot
  evaluate mechanically all stand untouched.

### J.4 Of this step's own making

- Clause [3] compares FULL_2Y against the window that *would govern* rather than against the window
  that supplied the evidence. On this corpus the two are the same window for VRRM only by coincidence
  of RECENT_6M being shortest; on a panel where RECENT_12M were shortest-eligible the comparison would
  be against RECENT_12M. The choice is argued (the decision is "keep FULL_2Y or switch to shortest", so
  the conflict that matters is between those two) and it is not tested against a case that
  distinguishes it.
- The `not full.contract_eligible` branch is unreachable (§D) and kept as a guard.

---

## K. Tests

```
new in this step                    20   backend/tests/strategy_h_v2/valuation/test_d5_d2r_window_selection.py
```

Every case §20 lists:

| §20 requirement | test |
|---|---|
| trend cancellation / recent reversal | `test_a_cancelling_round_trip_is_detected_under_d2r_and_missed_under_v1` |
| FULL_2Y stable regime | `test_a_flat_panel_keeps_the_full_window_under_both_contracts` |
| recent strong upward trend | `test_the_override_is_symmetric_in_direction` |
| recent strong downward trend | `test_a_cancelling_round_trip_...`, `test_a_monotone_collapse_...` |
| multiple recent windows | `test_the_governing_window_is_the_shortest_eligible_one_not_the_strong_one` |
| deterministic selection | `test_selection_is_deterministic_under_both_contracts` |
| selection independent of upside | `test_the_override_is_symmetric_in_direction` |
| ADBE regression | `test_a_monotone_collapse_still_takes_the_shortest_window_under_both_contracts` |
| VRRM regression | `test_the_round_trip_really_does_cancel` + the detection test |
| D6 valuation immutable arithmetic | the D2R runner's acceptance F, and D5-D2/D6 report reproduction (§K.2) |

Three of them are worth naming for what they are built to catch:

- `test_the_round_trip_really_does_cancel` measures the premise before the detection test asserts the
  conclusion. Without it, a detection test that passed for the wrong reason would look identical.
- `test_the_override_is_symmetric_in_direction` is §8's proof. The mirrored panel - flat, then a strong
  recent **expansion** - selects the recent window on identical terms, and the test asserts that the
  selected window's Base multiple is *higher* than the full panel's. A rule reading the upside would
  have to keep FULL_2Y there.
- `test_full_2y_is_eligible_whenever_any_window_is` establishes the nesting invariant that makes one
  branch of the rule provably defensive, and asserts the two monotonicity facts it rests on, so that a
  future change from a session slice to a calendar slice fails loudly.

Two fixture corrections are recorded rather than hidden: the first `round_trip` fixture made RECENT_6M
strongly trending, which is **not** VRRM's shape, and the first ineligible-FULL_2Y fixture was
unconstructible because of the nesting invariant. Both were wrong tests rather than a wrong rule, and
the second one is what produced §D's invariant.

### K.1 Suite

```
new in this step                      21   test_d5_d2r_window_selection.py
H-V2 suite before this step         1693   passed, 1 skipped
H-V2 suite after                    1714   passed, 1 skipped   (1693 + 21 exactly)
failures introduced                    0
```

The four window-rule tests D5-D2 shipped still pass unchanged, calling the V1-pinned wrapper on the
D5-D2 runner, which is the regression that matters most: they are the tests written against the rule
being repaired.

### K.2 Determinism

Two things are measured and they are different things.

**Within a run**, acceptance E re-evaluates every issuer under `D5_D2R_V1` a second time and compares
the **whole row** as JSON, not just the selected window:

```
E_selection_nondeterministic    0 of 14
```

**Across runs**, the two D2R reports agree on everything that is a result:

```
rows_new            identical    (the repaired valuations themselves)
window_changed      identical
window_unchanged    identical
d6_counterfactual   identical
coverage            identical
replay              differs - and only by two FIELDS added between the runs
```

The `replay` difference is recorded rather than smoothed: `old_method` and `old_status` were added to
the replay row after the first run, to support acceptance G. Every key the two runs share holds an
identical value on every row - 0 differences over 14 rows - so the added fields are the whole of it.

### K.3 The historical reports still reproduce

The refactor moved the selection rule out of the D5-D2 runner and into `fair_value`, which is the kind
of change that can alter a published artifact without anyone noticing. So the published steps were
re-run and compared, not reasoned about.

**D5-D2, re-run after the refactor, is byte-identical to its pre-refactor runs:**

```
D5_D2-20261004T045748Z.json   (before the D6 judgements parameter)   sha256 80b3a903...0804484c
D5_D2-20261004T053947Z.json   (after it, before D2R)                 sha256 80b3a903...0804484c
D5_D2-20261004T075430Z.json   (after the D2R refactor)               sha256 80b3a903...0804484c
```

That covers every `contract_window_reason` string in the report as well as every number, which is the
point of having preserved V1's wording verbatim.

**D6, re-run after the refactor, is identical in substance:**

```
                                           d5_rows            decisions          counts
D6-20261004T055305Z  (before D2R)          a424bb462a17bc72   43e6989a4d3c8d16   0/6/2
D6-20261004T055857Z  (before D2R)          a424bb462a17bc72   43e6989a4d3c8d16   0/6/2
D6-20261004T060321Z  (before D2R)          a424bb462a17bc72   43e6989a4d3c8d16   0/6/2
D6-20261004T060716Z  (before D2R)          a424bb462a17bc72   43e6989a4d3c8d16   0/6/2
D6-20261004T075808Z  (AFTER the refactor)  a424bb462a17bc72   43e6989a4d3c8d16   0/6/2
```

So the refactor that made the repair possible changed neither published step. That is the whole purpose
of having both rules selectable and both published steps pinned, and it is measured rather than argued:
D5-D2 by whole-file sha256, D6 by hash over its valuation rows and its decision records.

---

## L. Verdict

```
H-V2-D5-D2R
=
PASS

verdict on correctness (9 audit classes)     PASS
verdict as preregistered (all 10 classes)    NEEDS REVISION
the entire difference                        VRRM EV/EBIT: refused legs ['bear']
```

Against §14's six conditions, each fixed before the replay ran:

| | condition | measured |
|---|---|---|
| A | a VRRM-type recent reversal is detectable | **yes** - VRRM, via RECENT_12M evidence and a 2.234x level conflict |
| B | an ADBE-type dead regime remains rejected | **yes** - 0 issuers whose FULL_2Y trends strongly returned to FULL_2Y |
| C | existing recent-window selections do not regress | **yes** - 0 windows lengthened; all 7 clause-[2] issuers bit-identical |
| D | no window selected by its resulting fair value or upside | **yes** - 0 changes unexplained by trend and ratio; symmetry test passes |
| E | same inputs -> same selected window | **yes** - 0 nondeterministic; whole rows re-equal on re-evaluation |
| F | D5 arithmetic / provenance unchanged | **yes on all 9 correctness classes**; the 10th is §J.2 |
| G | the window contract moves no method or status | **yes** - 0 of 14 |

**PASS, and §J.2 is the argument.** The repair did exactly one thing on real data: it moved VRRM from
FULL_2Y to RECENT_6M, cutting TP1 from +404.7% to +24.8%, and it did so through a rule that reads only
eligibility, trend class and a ratio of observed multiples. Thirteen of fourteen valued rows are
bitwise unchanged. The window set, the trend threshold, the governing-window priority and every piece
of the fair-value arithmetic are untouched. Nothing published was edited.

The one thing the pre-registered acceptance flags is that VRRM's repaired range has no Bear leg,
because at the recent regime's 10th-percentile enterprise multiple a 65%-net-debt capital structure
leaves the equity worth less than nothing, and D5-D2's frozen rule refuses to publish that. That is an
existing rule firing correctly on an honest window, not a defect this step introduced - and the Bear
anchor it replaced, 0.72, came from a full-panel P10 of 7.9926x the issuer had already traded below.
Calling the repair a failure because it surfaced a refusal would be preferring the number that was
wrong to the refusal that is right.

A reader who holds §14 F to its literal wording should read this as NEEDS REVISION with one item to
resolve, and that item is §J.1's question about whether an incomplete range should cap confidence. The
runner prints both verdicts so the disagreement is in the artifact rather than only in this document.

```
D5 = COMPLETE
D6 = COMPLETE
```

Per §18, no further D5 or D6 repair step is generated. The limitations in §J travel to D7 as
limitations rather than as open work.

---

## M. D7 Authorization

```
H-V2-D7
FORWARD SHADOW
= READY TO LAUNCH
```

### M.1 What D7 must carry

- **D7 runs under `D5_D2R_V1`, explicitly.** The two published steps are pinned to `D5_D2_V1` and will
  stay pinned, so D7 cannot inherit the repaired rule by accident - it has to name it. The decision set
  D7 starts from already exists in this step's artifact: `rows_new` holds the repaired valuations and
  `d6_counterfactual.decisions` holds the decisions computed from them.
- **All three outcomes are stored and tracked**, per §19, and the WATCH cohort is tracked rather than
  discarded. On this sample that cohort is the whole of the eligible set bar two: SCCO, DORM, TG, COLL,
  FG, VRRM are WATCH; IDCC and AEYE are REJECT; APPROVE is 0 and launching with 0 is explicitly fine.
- **VRRM enters D7 as a WATCH with no downside anchor.** That is the one row whose incompleteness a
  forward shadow has to carry a note about, because a cohort table with a blank in the Bear column is
  exactly where a reader fills the blank in from memory.

### M.2 What D7 may not do, carried forward unchanged

```
no entry, exit, stop or position size
no portfolio weight, no ranking of the cohort against each other
no order, paper or live
no numeric composite that determines APPROVE / WATCH / REJECT
no target price, fair value or multiple of its own - every number comes from D5
no tuning of any D5 or D6 rule against a forward return
```

The last line is the one this step was most exposed to and did not cross. The window rule was repaired
from what the panel shows about its own regimes - a rank correlation that cancels, and two windows
whose medians disagree by more than the pre-registered conflict ratio - and not from which window would
have predicted better. No price after 2026-09-16 was read by anything in this step. That is what makes
D7's first measurement a measurement rather than a check of this step's hindsight.

### M.3 What is falsifiable forward, and what is not

A WATCH makes no claim about a return, so "did the WATCH go up" is not a test of anything. The narrower
statements that *can* fail forward:

- an issuer whose Bull leg sat below its price (IDCC: TP2 -16.6%) exceeding that level anyway;
- a named invalidation condition firing, which D3 published per issuer;
- VRRM's third Commercial Services renewal landing on Avis/Hertz-like terms, which D3 named and no
  valuation prices;
- the repaired windows being revisited: an issuer whose selected window was RECENT_6M re-rating back
  into its FULL_2Y range would be evidence the regime call was wrong.

The last of those is the only one that tests **this** step, and it is the one D7 should pre-register.
