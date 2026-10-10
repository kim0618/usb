# Production Context Collector V1.1 - contract

    identity: ctx-collector.v1.1
    supersedes: ctx-collector.v1 (record shapes kept; additions only, listed below)
    frozen: 2026-10-10, before any V1.1 count was computed

This document fixes the five things V1.1 changes. It is frozen before the V1.1 trial and before the
V1 recordings are re-classified under it, for the reason every contract in this repository is:
a classification chosen after looking at its counts will always find counts that flatter it.

Nothing here moves a threshold. Every number below is quoted from a frozen source and named.
Nothing here edits `mc-display.v1`, `lm-wall.v2` or `lm-continuity.v5`; where V1.1 publishes
something those contracts did not decide, it is published beside their values, never instead of
them, and the original value stays in the payload.

---

## 1. Observation warm-up gate (LIQUIDITY)

**Problem.** `lm-wall.v2` R4 requires an observed span of `MIN_PERSISTENCE_MS = 10_000`. After a
session starts, or after any transition that ends wall observation (a HARD resnapshot, or any
sample whose book is not SYNCED), no candidate can have that span for ten seconds. During that
window `mc-display.v1` reports LIQUIDITY `LIVE` with wall state `NONE` - "a complete reading in
which nothing qualifies" - when the truth is "not observed long enough to say".

**Rule.**

* `observation_since_ms` is the `sample_ms` of the first sample, after the session start or after
  the last observation break, whose book state is `SYNCED`. An observation break is: a sample
  whose book state is not `SYNCED`, or a newly published continuity transition whose
  `refresh_type` is `HARD`. A `SOFT` transition is not a break (its walls are carried by
  `lm-continuity.v5`).
* The gate is **open** iff `observation_since_ms` is known and
  `sample_ms - observation_since_ms >= lm-wall.v2 MIN_PERSISTENCE_MS`. At that instant a candidate
  present since the first observed sample has exactly that span, so it qualifies at the moment the
  gate opens; a `NONE` after it means no candidate that was observable for the whole window
  qualifies.
* While the gate is **closed**, after `mc-display.v1` has produced the LIQUIDITY layer:
  * a side whose wall state is `NONE` becomes `UNKNOWN`, reason `WALL_PERSISTENCE_WARMUP`;
  * a layer state of `LIVE` or `PARTIAL` becomes `UNKNOWN`, reason `WALL_PERSISTENCE_WARMUP`
    (section 4 of `mc-display.v1`: a layer short of data reports UNKNOWN);
  * `OK` walls are left as they are (only a carried span can make one, and it is proven);
  * a `warmup` block publishes `active`, `observation_since_ms`, `observed_ms`, `required_ms`.
* `collector.state` is `SYNCING` with reason `WALL_PERSISTENCE_WARMUP` while the gate is closed.
* FLOW is computed from the ungated LIQUIDITY payload, exactly as `mc-display.v1` computes it, and
  is not gated: its own windows already report `PARTIAL` during their warm-up.

**Target.** A published LIQUIDITY `LIVE` together with a wall state `NONE` while the gate is closed
(**false LIVE/NONE**) occurs zero times.

## 2. Classification of a display disappearance

**Problem.** `mc-display.v1` section 7 tracks the walls the panel *shows*: `lm-wall.v2`, then the
display filter (`min_notional_usdt = 500000`), then the first `wall_limit = 6` per side. A wall
that leaves that list because a nearer wall appeared, or because its notional fell under the
display filter while it stayed a wall, is counted as having vanished and is almost always called
`CANCEL_LIKE`. Measured on the V1 trial: 741 of 1,229 such events were still `lm-wall.v2` walls at
that very second.

**Rule.** Every item `mc-display.v1` reports in `vanished.this_reading` is first given one end
class, judged on the full `lm-wall.v2` selection of the same sample (the viewer's own
`side_walls` with the display filter at 0 and no limit) and on the same sample's book:

| End class | Condition, checked in this order |
|---|---|
| `UNKNOWN` | `mc-display.v1` itself returned `UNKNOWN` for the item, or the sample has no mid or no known interval |
| `RANK_EVICTED` | the item's bin (`side`, `bin_low`) is in the full selection of this sample. Reason `BELOW_DISPLAY_FILTER` if that wall's notional is under the display filter, else `BEYOND_DISPLAY_LIMIT` |
| `OUT_OF_COVERAGE` | the bin is not in the full selection and `[bin_low, bin_high]` is not inside `[known_low, known_high]`, or its distance from mid exceeds the V0 candidate band (`btc-ms.v0.1 WALL_BAND = 1%`) |
| `TRUE_ENDED` | otherwise: the bin lies in observed book and no longer qualifies |

Publication:

* `vanished.this_reading` and `vanished.recent` carry only `TRUE_ENDED` and `UNKNOWN` items, each
  with its `mc-display.v1` verdict unchanged (`CANCEL_LIKE` / `CONSUMED_CANDIDATE` / `UNKNOWN`) and
  `end_class`.
* `RANK_EVICTED` and `OUT_OF_COVERAGE` items go to `display_exits`, with the verdict
  `mc-display.v1` would have printed kept as `mc_v1_raw_state` for audit. They are never published
  as `CANCEL_LIKE`, `CONSUMED_CANDIDATE` or as an end.
* `vanished.totals` (the tracker's own counts) is kept unchanged and labelled raw;
  `vanished.classified_totals` counts by end class.
* The `mc-display.v1` vocabulary is not extended: no new value appears in a field that held
  `CANCEL_LIKE` / `CONSUMED_CANDIDATE` / `UNKNOWN`.

## 3. Current-state cache is volatile

The compact state file (`state/collector_state.json`) and the latest context file
(`state/context_latest.json`) are a cache of the current state, rewritten every second, and are not
authority (`is_authority: false`). The persistent journal is the authority.

* With a cache directory configured, `<root>/state` is a symbolic link to a directory on a volatile
  filesystem. Readers (the Liquidity Map viewer, the Market Context panel, this API) keep reading
  `<root>/state/...` unchanged.
* A real `<root>/state` directory found at start is renamed to `state.disk-<ms>`, never deleted.
* A missing link target is recreated before every cache write. A lost or corrupt cache costs at
  most one sample of readability and is never a journal loss.
* The persistent journal (`session`, `telemetry`, `storage_stats`, `context`, `wall_v2`,
  `wall_r0`) stays on disk.

## 4. Wall semantics roles

* **PRIMARY** for Q1-Q3 forward research: `wall_r0` (OPENED-row values), which reproduces
  Market Context R0's T2 frame. Unchanged from V1.
* **SECONDARY_DIAGNOSTIC**: `wall_v2` (current values, the panel's semantics). Kept for audit and
  for the display, never the research primary.

## 5. BTCUSDT only

The collector refuses to start for any symbol other than `BTCUSDT` (CLI `--symbol`, and the
`build()` entry point), and the context step reports `UNKNOWN` if the state it reads names any
other symbol. The API answers non-BTC symbols with `UNAVAILABLE / COLLECTOR_IS_BTC_ONLY` without
opening the root.

## 6. Unchanged

`ABSORPTION_CANDIDATE` (rule, inputs, threshold) is unchanged and remains a known limitation
(V1: fired in 48.5% of samples). Record shapes of V1 are unchanged apart from the additions above:
`liquidity.warmup`, `vanished[].end_class`, `display_exits[]`, `vanished.classified_totals`,
`v0_derived_seq` (V1), and the role labels in the session configuration.
