# Liquidity Map wall selection rule V2 — frozen

Status: FROZEN, 2026-10-04 (KST). Rule version: `lm-wall.v2`.
Frozen **before** the resulting wall sets were examined. Changes require a new version,
never a silent edit, and never a re-tuning against an outcome somebody preferred.

## What this rule is, and what it is not

The Market Structure V0 data contract (`btc-ms.v0.1`) is **unchanged by this document** and
stays the authority for the journal. V0 records a *candidate* whenever a level is at least 3x
the mean of up to 5 occupied neighbours on each side, within ±1% of mid, once per second. That
rule is deliberately loose because it feeds a dataset: a superset can always be narrowed offline,
a subset can never be widened without re-collecting.

This document is the **selection** rule the Liquidity Map viewer applies on top of that
superset to decide what it is willing to call a wall on a screen. It is applied by the reader,
not by the collector. No journal bytes change, no contract version moves, and an operator
cannot move these thresholds from the UI — they are frozen here, and the display filter
(default 500,000 USDT notional) remains a separate, operator-movable zoom applied after them.

## The defect being fixed

Measured on the live book, 2026-10-04:

* V0's rule qualified **284 resting candidates** out of 1,997 retained levels.
* Their notional was median **165,607 USDT**, p25 66,777, p10 30,354 — the median candidate is
  ordinary book, not a wall.
* Their `multiple` was median **5.62**, p90 29.96, max **713.87**.
* **19 candidates sat within 1 bp of mid**, with median notional of only 43,403 USDT and
  multiples up to 97x. Two sat within 0.5 USDT of the touch, with a median multiple of 61x.
* The neighbourhood those multiples are measured against is small in absolute terms: median
  neighbour average **20,337 USDT**, p10 3,900, min **543**.

So the top of the book is finely sliced, the local mean collapses, and a trivially small level
clears "3x its neighbours" by a huge factor. The published "nearest major wall" was then
routinely the touch level itself, at 77x and in one sample 1,473x. The multiple was not wrong;
it was being asked a question it cannot answer alone.

## The rule

Evaluated per candidate, in this order. A candidate must pass R1–R4; survivors are then grouped
by R5.

**R1 — absolute notional.** `notional_usdt >= 250000`.
The primary fix. A wall is first of all *big*, in money, independent of what is next to it. At
the measured distribution this keeps 51 of 284 candidates (18%), cutting at roughly p78. It is
set below the 500,000 display default on purpose: R1 answers "is this a wall at all", the
display filter answers "how much do I want on screen", and collapsing the two would leave no
way to see how many walls exist beyond the ones being shown.

**R2 — local relative size.** `multiple >= 5`.
V0's 3x is below the median candidate (5.62), so it selects nothing. 5x is deliberately modest
rather than aggressive: raising it preferentially deletes *large* walls that sit in a thick
neighbourhood, and a 2,000,000 USDT level beside 400,000 USDT levels is a wall at 5x. With R1
carrying the absolute question, the multiple only has to establish that the level stands out
locally at all.

**R3 — distance from mid.** `distance_bps >= 1.0`, where
`distance_bps = (price - mid) / mid * 10000` for ASK and `(mid - price) / mid * 10000` for BID.
The touch and its immediate neighbourhood are where slicing lives: 19 of 284 candidates are
inside 1 bp and their median notional is a quarter of the overall median. 1.0 bp is about 8.5
USDT at BTC near 85,000, about 85 ticks.
**Known cost, stated rather than discovered later:** a genuine wall resting within 1 bp of mid
is excluded by this rule. The count excluded by R3 is published on every response so that
"nothing near the touch" is never confused with "nothing is being looked at near the touch".

**R4 — minimum persistence.** `observed_persistence_ms >= 10000`.
`observed_persistence_ms` is the viewer's span (`latest sample ms - first_seen_ms`), not the
journal row's `persistence_ms`, which is 0 forever on a resting candidate's OPENED row. 10 s is
ten samples, and it is low on purpose: coverage never crosses a session boundary, so right after
a collector restart *no* candidate can have a long span and a high floor would blank the screen
for minutes. `session_age_ms` is published alongside so an operator can see when this floor is
not yet meaningful.

**R5 — price-bin grouping.** Bin width **5.0 USDT = 50 ticks** (0.59 bps at BTC near 85,000),
edges anchored absolutely at `floor(price / 5.0)`, computed per side.
Adjacent levels qualify together: at the measured distribution a 1 USDT bin left 202 bins for
284 candidates with 71 bins holding more than one, and a 5 USDT bin left 54 bins with up to 9
members. Nine lines on a ladder that are one structure is a misread of the same data. Each bin
yields **one** wall, represented by its **largest-notional member**, carrying `bin_members` and
`bin_candidate_notional_usdt`.
The width is stated in ticks, not in bps, because bin edges must be stable in absolute price: a
bps-defined bin moves its own edges as mid moves and a wall would change identity without
changing.
`bin_candidate_notional_usdt` is a **lower bound over candidates only** — levels in the bin that
did not qualify are not counted — and is labelled as such. It is never the bin's liquidity.

**Order is part of the rule.** R1–R4 filter members, *then* R5 groups. Binning first and summing
into the thresholds would let nine ordinary levels add up to a wall that no level in the bin is.
That ordering is rejected here, before anybody can prefer the number it produces.

## Values, and which moment they describe

A wall's size is read from the collector's **compact state checkpoint** when one is present and
belongs to the live session, which carries each active candidate's *current* size, multiple and
sample count. When it is absent, the viewer falls back to reconstructing from journal
transitions, where a resting candidate's only row is its OPENED row and the figures therefore
describe the moment it opened, which can be far from now.
These are different claims, so the response states which was used in `values_as_of`
(`CHECKPOINT_CURRENT` or `JOURNAL_OPEN_ROW`) and the screen shows it.

## Coverage

A wall inherits the V0 candidate's own `coverage`. A bin that is not entirely inside the
snapshot's known price interval is `PARTIAL` regardless of its member's value. A wall is never
reported as absent from a region the book did not reach; the observed interval is published and
unobserved space is never rendered as zero liquidity.

## What this rule still does not do

No direction. No rating or composite number of any kind. No spoofing, absorption or iceberg
verdict — this is aggregated depth, and a level that shrank may have been cancelled or filled
with no way to tell from this data. `order_identity_proven` stays false: persistence is a
sampled observation span, and a level that vanished between two samples and returned identical
is indistinguishable from one that never moved.
