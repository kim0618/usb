# CONTRACT: Manual Entry Advisor R0 (`entry-advisor.r0`)

Frozen 2026-10-12 before any result was computed, on top of `wall-lifecycle.r1` (same data, same
definitions, same costs). Output labels LONG / SHORT / NO_TRADE are **research labels**, not a
production signal. No family may be promoted without the gate in §9, and the local preview shows
`관망` until one is.

## 1. Families (explainable conditions only; no score, no sum)

Conditions are evaluated at a timing instant `t` (§2) on a display-qualified level episode
(R1 §3) and the display set (R1 §3). Flow side = R1 §5 (sign only).

| id | name | direction | conditions at t |
|---|---|---|---|
| F1 | ASK_REJECTION | SHORT | ASK level touched (`t ≥ t_touch`), level still resting at t, mid not beyond `bin_high` at t, flow side = BUY (buyers push into the wall and it holds: R0.1 I-A reading). Diagnostic arm F1s: same with flow side = SELL. |
| F2 | ASK_BREAKOUT | LONG | ASK level CONSUMED (R1 §4) and `t ≥ t_cross`, mid above `bin_high` at t, flow side = BUY. |
| F3 | BID_REACTION | LONG | mirror of F1 on a BID level: touched, resting, mid not below `bin_low`, flow side = SELL (sellers push, wall holds). Diagnostic arm F3s: flow side = BUY. |
| F4 | BID_BREAKDOWN | SHORT | mirror of F2: BID level CONSUMED, mid below `bin_low`, flow side = SELL. |
| F5 | CLUSTER_REJECTION | as F1 / F3 | F1 or F3 where the level is `cluster` (R1 §6). |
| F6 | CLUSTER_BREAKOUT | as F2 / F4 | F2 or F4 where the level is `cluster`. |

An instant where no family's conditions hold is `NO_TRADE`. The advisor never returns a direction
otherwise.

## 2. Timing instants (all measured; none selected afterwards)

| | rejection families (F1, F3, F5) | breakout families (F2, F4, F6) |
|---|---|---|
| T0 | first APPROACH instant (within 5 bp) | first APPROACH instant |
| T1 | first TOUCH instant | first TOUCH instant |
| T2 | `t_touch + 15 s`, level still resting and mid back on the near side (outside the bin) | first CROSS instant |
| T3 | `t_touch + 30 s`, same conditions | the CONSUMED end second |

At T0 the family's touch condition is not yet met; T0 events are reported as the "early" arm
and excluded from the gate. One event per `(episode_id, family, timing)`.

## 3. Entry

Entry price = mid at the instant (taker; spread and slippage are inside the cost, §7). Entry zone
candidate (reported, not fitted): `range60 = max(mid) − min(mid)` over the 60 s before the instant.
- rejection SHORT at ASK: `[bin_low − range60, bin_high]`; rejection LONG at BID: `[bin_low, bin_high + range60]`;
- breakout LONG through ASK: `[bin_high, bin_high + range60]`; breakout SHORT through BID: `[bin_low − range60, bin_low]`.
Reported: zone width (bp), and whether the entry mid lies inside it.

## 4. Invalidation candidates (all measured)

| id | rejection | breakout |
|---|---|---|
| V1 | mid beyond the level's far edge (ASK: `> bin_high`) | mid back inside the level's bin (ASK breakout: `≤ bin_high`) |
| V2 | 60 s flow side flips against the trade | same |
| V3 | mid reaches the next same-side display wall beyond the level (wall 2's near edge) | mid reaches the near edge of the level's own bin from the far side, i.e. V1, plus wall 2 on the opposite side is not used (reserved) |
| V4 | a new display wall opens on the opposite side between the entry and target 1 (reserved; counted, not used for R) | same |

Risk for R multiples uses **V1** (the structural one); V2 and V3 are reported.

## 5. Targets (all measured)

T1 / T2 / T3 = near edges of the nearest, second and third **opposing-side** display walls at the
instant (SHORT: BID1..3 `bin_high`; LONG: ASK1..3 `bin_low`). Fixed-time comparisons: exit at
5 / 15 / 30 / 60 min. For each target: hit = the mid reaches it before V1 and within 60 min; time
to hit; MAE before hit; MFE over the hold; gross bp; net bp (§7). A missing wall (fewer than three
on that side) is a missing target, counted.

## 6. Risk / reward

`risk_bp = |entry − V1 level| / entry × 1e4`, `reward_bp = |target − entry| / entry × 1e4`,
`R = reward_bp / risk_bp`. Distributions only (mean, median, p5, p95); no minimum R:R.

## 7. Cost

Round trip 11.25 bp (fee 11.0 + 0.25 spread / slippage; D5_4 contract l.62, reused by Q123 and
R0.1), plus the Binance funding rate of every 00/08/16 UTC funding timestamp strictly inside the
hold, signed by side (`/fapi/v1/fundingRate`). Gross and net are reported separately. No other
cost; nothing tuned.

## 8. NO_TRADE

Reported as the share of context seconds and of touch instants at which no family holds, overall
and per family. A system that always returns a direction fails this contract.

## 9. Gate (per family × timing, primary = target T1 with V1 invalidation, net)

n < 20 INSUFFICIENT; 20 to 49 EARLY; 50 to 99 REVIEW_ELIGIBLE; ≥ 100 STRONGER_EVIDENCE.
REVIEW passes only when, in a REVIEW_ELIGIBLE or stronger cell: direction alignment (mean gross
15 min return has the family's sign), mean net > 0, median net reported (and > 0), Holm-adjusted
block-bootstrap p ≤ 0.05 over the 24 primary tests (6 families × 4 timings, T0 excluded from the
gate but counted in the family), concentration (the top 10 % of events hold ≤ 50 % of the total
positive net) and tail (p5 net) reported, costs included. No production recommendation before
that; a passing cell is a candidate for a forward trial, not a rule.

## 10. Preview and recorder separation

The `[진입분석]` preview reads the advisor's verdict for the current second from the research
module through the local btc-context API only. Pressing it sends no order, touches no order
callback, and is not wired to the Manual Trade Recorder. For a later study of "advice shown →
manual trade → outcome" each verdict carries `analysis_request_id = sha256(session8, sample_ms,
family, timing)[:16]`, deterministic, so a recorder could join on it without any hook here.

## 11. Verdict mapping

- **A. NOT_ENOUGH_DATA**: no family × timing cell reaches 50.
- **B. NO_VALID_ENTRY_FAMILY**: cells reach 50 and none passes §9.
- **C. ENTRY_ADVISOR_CANDIDATES_FOUND**: at least one cell passes §9 at REVIEW_ELIGIBLE.
- **D. READY_FOR_ENTRY_ADVISOR_FORWARD_TRIAL**: C with STRONGER_EVIDENCE and the same cell also
  passing on the second half of the window alone.

The preview verdict is **관망** unless the stored results carry a C or D cell for the family and
timing that holds at the current second.
