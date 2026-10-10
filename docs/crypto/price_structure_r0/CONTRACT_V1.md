# US-B CRYPTO Price Structure R0, Contract V1

Status: **FROZEN BEFORE ANY R0 RESULT IS COMPUTED**
Frozen at: 2026-10-04 (Asia/Seoul)
Scope: research and an isolated preview only. C1 / C1x, AUTO, order paths, PAPER, LIVE, the
production deploy path, and the liquidity / order flow layers are all out of scope and unchanged.

This contract defines a **price structure layer**, not a signal. Nothing here decides a trade,
sizes a position, or produces an entry. The deliverable is a per decision time description of the
structure a human reads off a BTC chart, produced mechanically and without hindsight.

## 1. Question

At every decision time, can the layer publish the structure a competent discretionary trader would
mark on the same chart, using only information available at that moment, such that no published
object is ever revised later?

Three things are being tested, and they are separable:

1. **Causality.** Is every object confirmable at a stated timestamp with no forward data?
2. **Stability.** Once published, does an object ever change or vanish without a logged transition?
3. **Fidelity.** Does the output look like the chart, with neither level inflation nor missed
   major levels?

Fidelity is judged against pre-registered numeric criteria in section 13 plus a rendered overlay.
"Looks right" alone is not evidence.

## 2. Timeframe roles

The same structure is deliberately **not** computed on all three timeframes. Each frame owns a
different question, and the ownership is exclusive.

| Frame | Owns | Produces | Does not produce |
|---|---|---|---|
| **1h** | Where are we | confirmed swings, HH / HL / LH / LL, `trend_structure`, `range_high` / `range_low`, major levels | breakout timing of 5m levels, VWAP |
| **5m** | What just happened to a level | actionable levels, breakout / retest / failed break / sweep state machines | trend labels, range geometry |
| **1m** | Where exactly is price now | `vwap_position`, distance to nearest levels, the publication grid | swings, levels, events |

The middle frame is **5m**, not 10m. The choice is pre-registered and is not swept: 5m gives twelve
bars per hour, so a two close confirmation resolves inside fifteen minutes, and 5m is the frame the
existing chart already renders.

The 1m frame deliberately has **no swings and no levels of its own**. A 1m zigzag would add tens of
levels per day, and section 13 penalises exactly that.

## 3. Data and PIT rules

Source: BTCUSDT linear perpetual 1m klines from the public Bybit v5 endpoint, collected read only
into `data/research/crypto/price_structure_r0/raw/` with a sha256 manifest.

- Window collected: 2026-08-12T00:00:00Z inclusive to 2026-10-04T00:00:00Z exclusive, 76,320 bars.
- **Warmup**: 2026-08-12T00:00:00Z to 2026-09-01T00:00:00Z. The engine consumes these bars and
  publishes nothing.
- **Replay window**: 2026-09-01T00:00:00Z inclusive to 2026-10-04T00:00:00Z exclusive.
- The overlap with the frozen D2 history (`kline_1m`, ends 2026-09-23T01:33Z) must be compared field
  by field on open, high, low, close, volume, turnover. Any mismatch invalidates the run.

PIT rule, applied without exception: **a bar of any timeframe is known at its close and not one
instant earlier.** The 1h bar opening at T is first usable at T + 60 min. The 5m bar opening at T is
first usable at T + 5 min. Higher timeframe bars are built by aggregating 1m bars on UTC aligned
boundaries, and a partial bar is never used for anything.

Decision grid: every 1m bar close inside the replay window. Information time for the bar opening at
`ts` is `ts + 60_000`.

## 4. Scale unit: ATR

Thresholds are expressed in ATR, not in dollars, so that they mean the same thing at 60k and at 120k.

`ATR_tf[t]` is Wilder's ATR over 14 completed bars of timeframe `tf`, seeded with the simple mean of
the first 14 true ranges and updated as `ATR = (13*ATR_prev + TR) / 14`. It is known at the close of
bar `t`. Nothing uses an ATR of a bar that has not closed.

**Declared interpretation of the "no new indicators" rule.** The instruction forbids adding
oscillators such as RSI or MACD. ATR is used here only as the unit of length, never as a signal
input or a filter on direction, and VWAP is item 9 of the requested output. No other indicator is
introduced. This is a judgment call and it is recorded as one.

## 5. Swings, confirmed and non repainting

Per frame (1h and 5m only), with `theta_1h = 1.5` and `theta_5m = 2.0` fixed:

State is `mode` in {UNDECIDED, SEEK_HIGH, SEEK_LOW} plus a running extreme.

- In `SEEK_HIGH`: if `high[t] > ext_price`, the extreme moves to `(ts[t], high[t])`. A **swing high is
  confirmed at the close of bar t** when `ext_price - close[t] >= theta * ATR_tf[t]`. The swing is
  emitted with `pivot_at = ts_ext`, `price = ext_price`, `confirmed_at = close time of bar t`. Mode
  becomes `SEEK_LOW` and the new extreme is the minimum low over bars `[bar(ts_ext), t]`, which is
  already known.
- `SEEK_LOW` is the mirror image, using `close[t] - ext_price >= theta * ATR_tf[t]`.
- `UNDECIDED` is the state at the first bar with a finite ATR. Both extremes are tracked. The first
  side whose condition is met decides. If both are met on the same bar, the larger excess wins, and
  an exact tie resolves to the swing high. The priority is part of the definition.

The retracement test uses **close**, never the intrabar low or high. Using the low would let the
layer claim a confirmation at a moment inside the bar, which the 1m grid cannot honour at higher
timeframes. Close based is slower and is the honest version.

**Repaint statement.** `pivot_at` and `price` point backwards, which is unavoidable: a swing is by
definition recognised after the fact. What matters is that the object is **first published at
`confirmed_at` and never afterwards altered**. There is no state in which a swing exists at one
decision time and has a different price or pivot at the next, and none in which it disappears.

## 6. HH / HL / LH / LL and trend_structure

On each newly confirmed **1h** swing:

- a swing high is `HH` when its price is greater than or equal to the previous confirmed 1h swing
  high, else `LH`
- a swing low is `HL` when its price is greater than or equal to the previous confirmed 1h swing
  low, else `LL`
- the first high and the first low get `UNLABELED`

`trend_structure` at a decision time:

- `BULLISH` when the latest high label is `HH` and the latest low label is `HL`
- `BEARISH` when the latest high label is `LH` and the latest low label is `LL`
- `NEUTRAL` in every other case, including before two highs and two lows exist

`trend_structure` and `range_state` are independent fields and neither overrides the other. Their
joint distribution is reported in the results (section 13, M7). For the overlay badge only, the
display precedence is `IN_RANGE` first, otherwise `trend_structure`.

## 7. Range high and range low

Evaluated on the **1h** frame from the last four confirmed swings, which must contain at least two
highs and two lows.

Let `H` be the maximum of those swing highs, `L` the minimum of those swing lows, `W = H - L`.

`range_state = IN_RANGE` when all of:

1. `W <= 6.0 * ATR_1h[t]`
2. every swing high in the set is within `0.35 * W` of `H`
3. every swing low in the set is within `0.35 * W` of `L`

Then `range_high = H`, `range_low = L`, and both are **frozen** while the state holds.

Exits, each logged:

- a confirmed 5m breakout above `range_high` sets `RANGE_BROKEN_UP`, below `range_low` sets
  `RANGE_BROKEN_DOWN`. The last `range_high` / `range_low` are retained for reference for 24 further
  1h bars, then cleared to `NO_RANGE`.
- a new set of four confirmed swings failing conditions 1 to 3 sets `NO_RANGE`.

Conditions 2 and 3 are what separate a range from a drift: the edges have to be revisited.

## 8. Support and resistance level book

Every confirmed swing creates a candidate level. Range edges create none, because they are already
swing derived.

Fields: `id`, `tf`, `price`, `origin_kind` in {SWING_HIGH, SWING_LOW}, `pivot_at`, `created_at`,
`touches`, `last_touch_at`, `breaks`, `last_break`, `flips`, `state` in {ACTIVE, EXPIRED}.

**Role is derived, never stored.** With `tol_role = 0.15 * ATR_5m`, a level is `RESISTANCE` when its
price is above `close + tol_role`, `SUPPORT` when below `close - tol_role`, and `AT_PRICE` in
between. A level is therefore not born as support or resistance; it changes role by where price is,
which is the whole point of item 8.

**Merge, and why the line never moves.** A candidate level merges into an existing ACTIVE level when
`|p_new - p_incumbent| <= merge_tol`, where `merge_tol = 0.25 * ATR_1h` for a 1h candidate and
`0.25 * ATR_5m` for a 5m candidate. On merge the **candidate is discarded** and the incumbent's
`touches` is incremented. The incumbent's price is never recomputed. A drawn line therefore never
moves, at any time, for any reason.

A 5m candidate within `0.25 * ATR_5m` of an ACTIVE 1h level is not created at all: the 1h level
already represents that price and is the stronger object.

**Touch.** Level `L` is touched by 5m bar `t` when `low[t] <= L.price + tol_touch` and
`high[t] >= L.price - tol_touch`, with `tol_touch = 0.15 * ATR_5m`, and at least
`touch_cooldown = 6` 5m bars have passed since `last_touch_at`. The cooldown stops one thirty minute
visit from counting as six touches.

**Strength**, an integer used only for ranking: `touches + (2 if tf == "1h" else 0) +
(1 if touched within the last 24h else 0)`.

**Expiry**, each logged, evaluated on 5m bar closes:

1. a 1h level with `now - max(created_at, last_touch_at) > 10 days`
2. a 5m level with `now - max(created_at, last_touch_at) > 48 hours`
3. any level with `|close - L.price| > 10 * ATR_1h` that has not been touched for 24 hours

**Publication cap.** `nearest_resistance` is the lowest ACTIVE level strictly above close and
`nearest_support` the highest strictly below. The overlay set is the top `MAX_PUBLISHED = 10` ACTIVE
levels, at most 5 above and at most 5 below, ranked by strength descending then by absolute distance
to close ascending. The cap is fixed here and is not tuned later.

## 9. Breakout confirmation

A breakout is confirmed only against an ACTIVE level or a frozen range edge, and only by closes.

`buffer(p, tf) = max(0.0010 * p, 0.25 * ATR_tf)`, that is ten basis points of price or a quarter ATR,
whichever is larger.

- A **5m level** breaks up at the close of 5m bar `t` when `close_5m[t-1] > p + buffer(p, 5m)` and
  `close_5m[t] > p + buffer(p, 5m)`.
- A **1h level** breaks up at the close of 1h bar `t` when `close_1h[t-1] > p + buffer(p, 1h)` and
  `close_1h[t] > p + buffer(p, 1h)`.
- Down breaks are the mirror image.
- A level cannot re-break in the same direction within `rebreak_cooldown = 24` bars of the
  confirming frame.

The asymmetry is the role split from section 2, not an oversight: a major level is a 1h object and
needs a 1h close to be considered broken. The cost is latency of up to two hours, and that cost is
accepted rather than hidden.

`last_breakout` publishes the most recent confirmed breakout across both frames, with `level_id`,
`level_price`, `tf`, `direction`, `confirmed_at`, and age in minutes.

## 10. Retest

Exactly one retest machine is active at a time, attached to the most recent confirmed breakout. A new
confirmed breakout terminates the previous machine with `SUPERSEDED`.

The machine is evaluated on **5m bars** whatever the level's own timeframe, because the retest is a
timing question and timing belongs to the 5m frame. Window is `48` bars of the breakout level's
timeframe, converted to minutes.

For an UP breakout of level price `p`, with `b = buffer(p, tf_of_level)`:

| State | Entered when | Terminal |
|---|---|---|
| `AWAITING_RETEST` | at confirmation | no |
| `RETEST_TOUCHED` | `low_5m[t] <= p + b` | no |
| `RETEST_HELD` | after TOUCHED, two consecutive 5m closes `> p + b`; increments `L.flips` | yes |
| `RETEST_FAILED` | at any point, two consecutive 5m closes `< p - b` | yes |
| `NO_RETEST` | window elapses while still AWAITING_RETEST | yes |
| `SUPERSEDED` | a newer breakout is confirmed | yes |

DOWN breakouts mirror every comparison. `retest_state` publishes the active machine's state, or
`NONE`.

## 11. Failed break and sweep are two different objects

- **Failed break** requires a confirmed breakout that is then reclaimed against: it is exactly the
  `RETEST_FAILED` transition. `failed_break_state` publishes `FAILED_BREAK_UP` or
  `FAILED_BREAK_DOWN` for 48 5m bars after the failure, then returns to `NONE`.
- **Sweep** is a wick rejection with no confirmed breakout at all. For level `p` and 5m bar `t`, an
  up sweep is confirmed **at the close of bar t+1** when `high_5m[t] > p + b` and
  `close_5m[t] <= p` and `close_5m[t+1] <= p` and no up breakout of that level was confirmed at `t`
  or `t+1`. Down sweeps mirror. A sweep counts as a touch. `last_sweep` is published.

Needing bar `t+1` is why a sweep is stamped one bar late. That is the price of not repainting.

## 12. VWAP relative position

Session VWAP anchored at 00:00 UTC, computed from 1m bars of the current UTC day through bar `t`:

`vwap[t] = sum(turnover) / sum(volume)`, which is exact because `turnover` is quote volume.

Dispersion uses the volume weighted second moment of the typical price
`tp = (high + low + close) / 3`:
`sigma[t] = sqrt( max(0, sum(v*tp^2)/sum(v) - (sum(v*tp)/sum(v))^2 ) )`.

`vwap_z = (close - vwap) / sigma` when `sigma > 0`, else `0.0`.

`vwap_position` is `ABOVE_UPPER` when `vwap_z >= 2`, `ABOVE` when `0 < vwap_z < 2`, `AT` when exactly
0, `BELOW` when `-2 < vwap_z < 0`, `BELOW_LOWER` when `vwap_z <= -2`.

## 13. Pre-registered evaluation

All metrics are computed on the replay window only.

- **M1 `repaint_violations`.** Mutable fields are enumerated and closed: a level's `touches`,
  `last_touch_at`, `breaks`, `last_break`, `flips`, `state`; a retest machine's `state`. Every other
  field of every published object is immutable, including every level `price`. A violation is any
  change to an immutable field, or the disappearance of an object without a logged EXPIRED or
  terminal transition. Target 0.
- **M2 active level count** across snapshots: median, p95, max.
- **M3 published level count** must never exceed 10, verified rather than assumed.
- **M4 major reversal coverage.** A major reversal is a confirmed 1h swing inside the replay window
  whose price differs from the previous confirmed opposite 1h swing by at least **2.0%**. For each
  one, at the close of the 1h bar **immediately preceding the pivot bar**, did the published overlay
  set contain a level within **0.25%** of the pivot price? Coverage is hits over total. This is the
  "did it miss the level" test, and it is evaluated strictly before the turn was visible.
- **M5 liveness.** Of the retest machines whose window elapsed before the replay end, the fraction
  in a terminal state. Target 1.0.
- **M6 event rate per UTC day**: confirmed breakouts, retest terminations by kind, sweeps.
- **M7** the joint distribution of `trend_structure` and `range_state`.

## 14. Verdict rule, fixed before results

Evaluated in order, first match wins.

- **A. NOT_USABLE** if `M1 > 0`, or `M2 median > 15`, or `M4 < 0.40`, or `M5 < 1.0`.
- **B. USABLE_FOR_STRUCTURE_LAYER** if `M1 == 0` and `M2 median <= 10` and `M2 p95 <= 16` and
  `M4 >= 0.60` and `M5 == 1.0` and the median daily confirmed breakout count is `<= 12`.
- **C. NEEDS_LIMITED_REVISION** in every other case.

No threshold in this document may be changed once a result has been seen. If the outcome is C, the
failing metric is named and a revision is **proposed, not applied**.

## 15. Prohibitions carried from the instruction

No future data. No hindsight pivot. No threshold sweep after seeing results. No oscillator
indicators. No merge with the liquidity map or order flow layers. No change to C1 / C1x. No change to
AUTO or any order path. No production deployment. The preview is an isolated artifact that no
trading screen imports.
