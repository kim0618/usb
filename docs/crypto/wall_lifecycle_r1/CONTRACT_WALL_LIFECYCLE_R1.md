# CONTRACT: Wall Lifecycle & Interaction R1 (`wall-lifecycle.r1`)

Frozen 2026-10-12 before any result was computed. Research and a local preview only: no trading
signal, no production change, no order path. Every threshold below is either an existing frozen
figure (cited) or a structural choice justified here; none may be changed after results are seen.

## 1. Questions

| | question | unit | primary test |
|---|---|---|---|
| Q1 | Does a wall's persistence (before and after the first touch) relate to the price reaction that follows? | level episode | mean 15 min rejection-direction return per post-touch survival bin |
| Q2 | Does a wall's notional change (first observed vs later) relate to hold / cross / reversal? | V2 bin episode | continuation rate across pre-touch size-ratio terciles |
| Q3 | After a touch, do outcomes differ by how the wall ended (held, consumed, pulled, rank-evicted, out of coverage, unknown)? | level episode | mean 15 min return per end class |
| Q4 | Does ASK/BID wall × FLOW carry direction? | level episode | classes A to D, mean 15 min return in the class's natural direction |
| Q5 | After wall 1 goes, is reaching wall 2 / wall 3 (probability, time) informative, and does the gap matter? | wall-1 end event | reach rate and time; continuation rate across gap quartiles |
| Q6 | Is a cluster of walls different from a single wall? | touched level episode | reversal rate, MFE, MAE, 15 min return: cluster vs single |

## 2. Sources

| source | period | granularity | venue | timestamp | session boundary | missing / gaps | forward-safe |
|---|---|---|---|---|---|---|---|
| Context Collector `wall_r0` (V0 candidate level rows, OPENED / ENDED / UNKNOWN, OPENED-row values only; `emit_updates=False`) | 2026-10-10 21:47:34 to 2026-10-12 11:48 KST sealed files, session `31cc8177` only | event | Binance USD-M depth | envelope `receive_ms`; `first_seen_ms` per level | one session; state never crosses sessions | UNKNOWN = interrupted level | yes: a row is known at its `receive_ms` |
| `wall_v2` (lm-wall.v2 bin OPEN / CHANGE / CLOSE) | same | event, 1 s cadence | same | `sample_ms` = the 1 s sample | same | CLOSE `NO_USABLE_READING` (151) = book unusable, not a wall end | yes |
| `context` (1 s: `book.mid`, `flow.windows`, `vanished`, `display_exits`, `liquidity.*.nearest`) | same, 136,823 samples | 1 s | same + Binance aggTrade | `sample_ms` | same | 32 gaps > 2.5 s, max 35.7 s | yes |
| `telemetry` (resync, stale, gap) | same | event | same | `receive_ms` | same | used only to flag | yes |
| Binance `/fapi/v1/klines` 1m | 2026-10-10 20:00 KST to run time | 1m closed bars | Binance USD-M | bar open ms; a bar is usable from its close | n/a | none seen | yes, closed bars only |
| Binance `/futures/data/openInterestHist` 5m | same | 5m | same | bucket timestamp; usable from timestamp + 5 min | n/a | none | yes |
| Binance `/fapi/v1/fundingRate` | same | 8h | same | `fundingTime`; usable from then | n/a | none | yes |
| Binance `/fapi/v1/premiumIndexKlines` 1m (basis) | same | 1m | same | bar open; usable from close | n/a | none | yes |
| current wall hierarchy (the V3 screen's set) | at any second t | 1 s | derived | = the display set below at t | same | derived | yes |

Bybit series are not used: every wall and flow figure here is Binance, and mixing venues was the
V3 preview's own defect. Public, unsigned GETs from the research machine only; the production
host is read once (`ssh cat` of sealed files) and written to never.

## 3. Units and identities

- **Level episode (primary, Q1, Q3, Q4, Q5, Q6).** One `wall_r0` OPENED row whose OPENED values
  pass the lm-wall.v2 open floors already frozen in `wallrule.py` (`MIN_NOTIONAL_USDT` 250,000,
  `MIN_MULTIPLE` 5), from its OPENED `receive_ms` to its ENDED or UNKNOWN row (matched on
  `side, price` and the row's `first_seen_ms`). `episode_id = r0:<session8>:<side>:<price>:<v0_seq of OPENED>`.
  Display-qualified subset: OPENED notional ≥ 500,000 (`DISPLAY_MIN_NOTIONAL_USDT`, what the screen
  shows). The level's bin is `floor(price / 5) × 5` to `+5` (`wallrule` bin), its near edge
  `bin_low` (ASK) / `bin_high` (BID), its far edge the other one.
- **V2 bin episode (secondary, Q2 and the hierarchy).** One `wall_v2` OPEN to its CLOSE for the
  same `(side, bin_low)`. `episode_id = v2:<session8>:<side>:<bin_low>:<OPEN sample_index>`.
  Notional observations: OPEN `notional_usdt`, every CHANGE `notional_usdt` (the CHANGE payload
  carries the current value even when `changed` does not name it), CLOSE `last_notional_usdt`.
  The collector does not store the size at open elsewhere, so **first observed = OPEN** is the
  baseline, and only observations at or after the OPEN sample are used.
- **Display set at second t** (hierarchy, targets, Q5): the V2 bins open at t with current
  notional ≥ 500,000, nearest first, at most 6 per side. Same rule as the collector's own
  `walls_shown` (`DISPLAY_WALL_LIMIT`), replayed from `wall_v2`.
- Recorded per episode: `price, notional, multiple, persistence_ms (ENDED row), first_seen_ms,
  bin_low, bin_high, coverage, continuity (V2 OPEN continuity_status of the bin if open at the
  level's open, else NONE)`.

## 4. Mid path, approach, touch, cross, consumed (frozen before results)

Mid = `context.book.mid` at `sample_ms`, 1 s. The level is observed only while its row is
resting (`is_resting`).

ASK (BID mirrored by sign):
- `NO_APPROACH`: never within 5 bp of `bin_low`.
- `APPROACH`: `(bin_low − mid) / mid × 1e4 ≤ 5` (R0.1 `APPROACH_BP`, the collector's near-wall
  bound), first instant `t_approach`.
- `TOUCH`: `mid ≥ bin_low` (inside the bin or beyond), first instant `t_touch`.
- `CROSS`: `mid > bin_high`, first instant `t_cross`.
- `CONSUMED`: the episode ENDED and the mid path reached `bin_high` at or before the end sample
  (MC V1 `traded_through` on the far edge).
End class of a level episode (exactly one):
`CONSUMED`; `TOUCHED_PULLED` (touched, never traded through, ENDED); `CANCEL_LIKE` (ENDED with no
touch; MC V1's own word); `OUT_OF_COVERAGE` (ENDED while `price` is outside `book.known_low..known_high`
of the end sample, or beyond `CANDIDATE_BAND` 1 % of mid); `UNKNOWN` (UNKNOWN row, or a context gap
> 2.5 s at the end sample, or no mid). Attribute, not a class: `RANK_EVICTED_V2` = the level's V2
bin CLOSEd `NOT_SELECTED` while the level was still resting (R0.1 §4), with the second it happened.

Post-touch survival `s = t_end − t_touch` (level episodes only; V2 bins close in the touch second
by construction of the 1 bp distance floor, R0.1 §F). Bins, fixed now:
`[0,1) [1,5) [5,15) [15,30) [30,∞)` seconds.

## 5. Outcomes (every question uses these)

From a classification instant `t_c` (the touch for Q1, Q3, Q4, Q6; the end second for Q5):
- `r_H = (mid(t_c + H) − mid(t_c)) / mid(t_c) × 1e4` bp, H ∈ {5, 15, 30, 60} min, signed in the
  **rejection direction** (ASK: down is +; BID: up is +). Q5 and the breakout classes read the
  opposite sign and say so.
- MFE / MAE over 15 min in the same direction, on the 1 s mid path.
- `continuation`: `CROSS` of the far edge within 15 min of `t_c`.
- `reversal`: the mid at least 22.5 bp (MC R0 `MEANINGFUL_BP` = 2 × round-trip cost) from the near
  edge in the rejection direction within 15 min, before any continuation.
- A window that contains a context gap > 60 s, or runs past the last sample, is excluded and
  counted.

Flow at an instant: the `60s` window's larger share (`buy_usdt` vs `sell_usdt`), **sign only, no
threshold** (R0.1 §4; Q123 "SIGN"). Exactly equal = no reading.

OI / funding / basis at an instant (diagnostic only, §8): OI value and 1 h change from the last
5 m bucket closed at or before the instant; last settled funding rate; basis = premium index 1m
close of the last closed bar, bp. Reported per class only where n ≥ 20; no test.

## 6. Clusters (Q6), pre-registered

Distance normalisation: **ATR**. `ATR14(1m)` = mean true range of the 14 closed 1 m bars before
the instant. Two adjacent display-set walls on the same side form a cluster when their price gap
≤ 0.5 × ATR14(1m); a cluster is the maximal chain. Reason for 0.5: half of a one-minute true range
is the distance the mid typically crosses inside one bar, so two walls closer than that are met
as one obstacle. Median ATR14(1m) over the window is 11.3 USDT (1.4 bp), p10 4.4, p90 38.5; the bin
is 5 USDT. The touched level is `cluster` if its bin belongs to a cluster of ≥ 2 display-set walls
at `t_touch`, else `single`.

## 7. Q5 next-wall path, pre-registered

Event: a display-qualified level episode that was wall 1 (nearest display-set wall on its side
at its end second) ends as CONSUMED or TOUCHED_PULLED or CANCEL_LIKE. At that second: wall 2 and
wall 3 = the next display-set walls beyond on the same side (nearest first), `gap_1_2`,
`gap_2_3` in bp. Measured over 15 and 60 min: reach of wall 2's near edge, time to reach, reach of
wall 3, intermediate reversal (≥ 22.5 bp back before reaching wall 2), max move before reversal.
Gap test: continuation (= reach wall 2 within 15 min) rate per `gap_1_2` quartile; Spearman ρ of
`gap_1_2` vs reach, permutation p (1,000, seed 20261012).

## 8. Statistics

- Point estimates: mean, median, p5, p95, n.
- Uncertainty: moving-block bootstrap on episodes in time order, block = all episodes inside one
  60 min wall-clock block, 2,000 resamples, seed 20261012; two-sided p = 2 × min(share ≤ 0, share
  ≥ 0) of the bootstrap mean, 95 % CI percentile.
- Multiplicity: Holm over the pre-registered family of each question: Q1 10 tests (2 sides × 5
  bins), Q2 2 (sides), Q3 2 × 5 classes = 10, Q4 8 (4 classes × 2 sides), Q5 2, Q6 2 × 4 outcomes = 8.
- Sample stages per cell: n < 20 INSUFFICIENT, 20 to 49 EARLY, 50 to 99 REVIEW_ELIGIBLE,
  ≥ 100 STRONGER_EVIDENCE (R0.1 §8). No interpretation below 20.

## 9. Reproducibility requirements (tested)

Deterministic replay (same input, byte-equal JSON output); prefix invariance (the first half of
the journal yields the same episodes as the first-half subset of the full run); no future leak
(every feature at t reads records with `sample_ms`/`receive_ms` ≤ t, and OI/funding/basis from
buckets closed at or before t); one session only, state reset at a session change; end
classification; next-wall ordering; duplicate protection (one episode per `episode_id`).

## 10. Verdict mapping

- **A. NOT_ENOUGH_DATA**: no Q1/Q3/Q4/Q6 cell reaches REVIEW_ELIGIBLE.
- **B. WALL_INTERACTION_R1_PASS**: cells reach ≥ 50 and no Holm-adjusted p ≤ 0.05 with a mean
  effect ≥ 11.25 bp (round-trip cost) in its natural direction.
- **C. WALL_EDGE_CANDIDATES_FOUND**: at least one cell with n ≥ 50, Holm p ≤ 0.05 and mean net
  effect > 11.25 bp in its natural direction. A candidate, not a rule.

Nothing in this document is a production threshold. The V3 screen keeps showing figures only.
