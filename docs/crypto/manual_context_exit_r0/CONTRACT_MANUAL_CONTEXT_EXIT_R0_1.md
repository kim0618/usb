# CONTRACT: Manual Context & Exit R0.1 (BTCUSDT)

Status: **FROZEN before any outcome is computed.** This contract supersedes R0 (commit edb2675,
sha256 17d1dd44…) **before any outcome of R0 was computed**. R0 has no verdict and never will; this
file governs. Nothing below may change after this file's commit; a change makes R0.2.

Why R0.1 exists. While building bars, before any forward return was read, the frozen D2 files that
R0 §1 called "Binance USD-M" were found to be **Bybit linear** BTCUSDT:
- 1m bar 2026-09-10T00:26Z in D2 is O 78209.8 / H 78239.9 / L 78209.8 / C 78213.3 / V 9.881, equal to
  Bybit `/v5/market/kline` and not to Binance (O 78214.4 … V 55.656);
- the D2 funding rates equal Bybit's (0.0001 at 2020-03-25T16:00Z; Binance 0.00009813);
- the D2 open interest equals Bybit `/v5/market/open-interest` (60563.603 at 1790127000000).

The 15 s bars of R0 (Binance aggTrades) would then mix venues with every other timeframe. R0.1
fixes the venue statements and makes each study single-venue. Nothing else changes: the
questions, methods, horizons, bins, gates, costs, comparisons and verdict mapping are R0's, word
for word.

The kline timeframes are Bybit. That is also the venue of the production terminal's chart
candles (the chart is fed by Bybit public data; LIVE orders go to Binance). The wall and flow
studies are Binance, the venue of the Context Collector. The two are never mixed inside one
measurement.

Scope: research and a local preview only. This contract produces no trading signal, does not touch
AUTO, places no order, does not change Context Collector, Q123 or HOT, and deploys no UI. No output
of this study may be labelled LONG/SHORT *signal*. "LONG"/"SHORT" below name the side of a
hypothetical position whose outcome is measured, nothing more.

## 0. Questions (frozen)

- **Q1.** For each chart timeframe (15s, 1m, 10m, 1h, 4h, 1d), does that timeframe's own nearest
  support/resistance relate to the subsequent price reaction more than a placebo price does?
- **Q2.** On which timeframes do order-book walls and aggressive flow carry information for manual
  decisions?
- **Q3.** Does a wall that persists, shrinks, is consumed or is pulled differ in subsequent price
  reaction from a wall that merely exists?
- **Q4.** After entry, which conditions, while they hold, are followed by better outcomes from
  continuing to hold?
- **Q5.** Which changes of condition are useful as exit, take-profit or invalidation points?
- **Q6.** Do structure-based target/invalidation exits beat fixed-time and fixed-percent exits on
  the same entries?

## 1. Data (sources, frozen)

The full audit is in Appendix A. The sources used are:

| id | source | span used | granularity | used by |
|---|---|---|---|---|
| K1 | **Bybit linear** BTCUSDT 1m klines, D2 frozen file `data/runtime/crypto/BTCUSDT/historical/kline_1m/1585132560000_1790127239999.jsonl` | 2020-03-25 → 2026-09-23T00:00Z (exclusive) | 1m | 1m, 10m, 1h, 4h, 1d structure, exits, hold |
| K15 | **Bybit linear** BTCUSDT public trades, `public.bybit.com/trading/BTCUSDT/BTCUSDT<date>.csv.gz` (no published checksum: `gzip -t` plus sha256 recorded at download) | 14 UTC days 2026-09-09 → 2026-09-22 inclusive | trade | 15s bars |
| D1 | D2 frozen **Bybit** funding / index_1m / open_interest_5m files (same directory) | to 2026-09-23 | 8h / 1m / 5m | derivatives context, funding cost |
| C1J | Context Collector V1.1 production journal, read-only copy `~/usb-q123-evidence/first-24h-20261011/journal` (copied 2026-10-11 21:4x KST by the Q123 24H summary), sha manifest recorded at run | 2026-10-10 21:47 → 2026-10-11 ~21:48 KST (~24.0 h) | 1 s context, wall_v2 / wall_r0 events | walls, flow (Q2, Q3, wall families) |
| K1C | **Binance USD-M** 1m klines for the C1J window, from the same Q123 evidence (`klines/binance_kline_1m_1788998400000_1791723000000.jsonl`) | 2026-09-10 → 2026-10-11 | 1m | structure inside the C1J window (same venue as the collector) |
| MT | Manual trade provenance: `data/research/crypto/manual_r1/trade_provenance_v1.parquet` plus Binance LIVE fills as read from the production terminal API (GET) at run time | all | fill | backfill (§13) |

Not joined into the kline studies: liquidation_forward (starts 2026-09-27, after K1 ends), C1
(a separate research track), Q123 events (they come from the same C1J window; they are cited, not
re-derived). Any of these used in §13 only as recorded fields.

## 2. Timeframe structure (frozen method)

Every timeframe runs **the unmodified Price Structure R1 engine**
(`research/price_structure_r1/engine.py`, thetas `THETA_1H=3.0`, `THETA_5M=6.0` from
`market_context_v1/contract.py`, `require_touch_to_publish=True`, default expiry) on that
timeframe's own bars, **time-scaled**:

- The timeframe's bars are fed to the engine as its "1m" series with synthetic timestamps
  `t0 + i·60 000 ms`. The engine's internal 5m and 1h layers therefore become 5× and 60× the
  timeframe (10m → 50m / 10h; 1h → 5h / 60h; 1d → 5d / 60d; 15s → 75s / 15m). All thresholds in
  R1 are in ATR units or bar counts, so the engine is scale-free apart from this relabelling.
- 1m uses the engine natively (synthetic time = real time).
- Bars: 10m/1h/4h/1d are UTC-aligned aggregations of K1 (`price_structure_r0.data.aggregate`, a
  bar exists only when complete). 15s bars come from K15 trades (OHLC of trade prices, volume
  and quote turnover). A 15 s interval with no trade is a flat bar at the previous close with
  volume 0.
- Published nearest resistance / support at bar *i*'s close is what R1's `book.publish(close,
  atr5, at_ms)` returns at that close. A level is known only from the close of the bar that
  published it. There is no repaint: the R1 audit must report 0 violations.
- Warm-up: the first 1 440 bars of each series (1 synthetic day) publish nothing used.
- Study spans: 15s = all of K15 after warm-up. 1m = the 180 days before 2026-09-23. 10m = the
  730 days before 2026-09-23. 1h, 4h, 1d = all of K1 after warm-up.

## 3. Structure reaction study (Q1)

Unit: **touch episode** of a published level *L* on timeframe *T*.

- Resistance touch: bar `high ≥ L` while the previous bar closed below *L* and *L* was published at
  the previous close. Support is mirrored.
- Re-arm: after an episode, the same level can start a new episode only after a bar closes more
  than 1 ATR(T) away from it on the near side.
- Classified on the touch bar and the H bars that follow:
  - **hold/reject**: no bar closes beyond *L* within H.
  - **break**: some bar closes beyond *L* within H (the first such bar = break latency, in bars).
  - **retest** (breaks only): after the break, a later bar within H trades back to *L*.
- MFE / MAE: measured from the touch bar's close, in the **rejection direction** (down for
  resistance, up for support), over H bars, in bp and in ATR(T) units.
- Reaction probability = share of episodes that hold/reject. Break probability = 1 − that.
- Distance bins (pre-registered, ATR(T) units, distance from close to the nearest level at each
  bar): `[0,0.5) [0.5,1) [1,2) [2,4) [4,∞)`. They are reported as the share of bars in each bin and
  as the touch rate within H for each bin.
- **Placebo**: for every real episode, a placebo level at `L + 0.5·ATR` (resistance) or
  `L − 0.5·ATR` (support), with the same touch / break rules over the same bars. Placebos within
  0.25 ATR of any published level are dropped.
- Horizons per timeframe, in bars: **H ∈ {5, 20, 60}**. H = 20 is primary.
- **Relevant at T** (per side): n ≥ 100 real episodes, and real reaction rate − placebo reaction
  rate > 0 with a 95% block-bootstrap CI excluding 0, at H = 20, Holm-adjusted over the 12 tests
  (6 TF × 2 sides).

## 4. Wall and flow studies (Q2, Q3; C1J window only)

Vocabulary is the existing one only.

- Wall identity and lifecycle: `wall_r0` (the collector's PRIMARY wall role).
  - OPENED → ENDED, with `persistence_ms`, `notional`, `multiple`.
  - Size change = notional at ENDED vs OPENED.
- **Rank-evicted**: a `wall_v2` CLOSE with reason `NOT_SELECTED` while a `wall_r0` row for that bin
  is still ACTIVE.
- **Consumed vs pulled**: MC V1 `VanishTracker` semantics, reproduced through
  `mcv1_vendored.flow.traded_through` on the 1 s mid path from the context journal.
  - `CONSUMED_CANDIDATE` = the mid path reached the bin.
  - `CANCEL_LIKE` = it did not.
  - `UNKNOWN` = undecidable, for the tracker's own reasons.
- Approach / touch: the mid comes within the bin (touch) or within 5 bp of it (approach). These are
  MC R0's own near-wall bounds. Post-touch persistence = how long the wall lasts after the first
  touch.
- Flow: the collector's `60s` window, buy/sell share as published. Flow side = the larger share,
  with no threshold.
- **Interaction classes** (ASK; BID mirrored), evaluated at a wall's first touch:
  - **I-A**: buy-flow side + wall persists ≥ 60 s after the touch + no mid beyond the bin.
  - **I-B**: buy-flow side + the wall ends `CONSUMED_CANDIDATE` + the mid trades beyond.
  - **I-C**: the wall ends `CANCEL_LIKE` without a touch.
  - **I-D**: the wall persists + sell-flow side.
- Outcomes: mid return after 5 / 15 / 30 / 60 min from the classification instant, in bp, with
  the sign fixed by the class's natural reading (I-A, I-D: away from the wall; I-B: through it;
  I-C: none, reported two-sided).
- Timeframe wall relevance (Q2): for touch episodes of the §3 structure on timeframe T that fall
  inside C1J, compare the §3 outcome split by "a near wall on the same side at the touch"
  (yes/no). A timeframe gets the label "wall relevant" only under the §8 gate. With ~24 h of data,
  T ≥ 1h has, by construction, almost no episodes in C1J. Those are reported as INSUFFICIENT and
  are not hidden.

## 5. Entry cohorts (no signal is created)

The hypothetical entries are the §3 events. No other entry exists.

- **FADE**: at a touch bar that closes back on the near side, enter against the level at that
  close: SHORT at a resistance touch, LONG at a support touch.
- **BREAK**: at the first bar that closes beyond a level, enter in the break direction at that
  close.
- Dedup: one open hypothetical position per cohort × side × timeframe at a time. An event during an
  open position is not entered.

## 6. Exit families (Q5, Q6)

All are evaluated on the same entries, with the same maximum hold `Hmax = 60` bars. A position
still open at Hmax exits at that close, and the timeout share is reported for every family.

Levels are read from the R1 snapshot at the entry bar's close:
- **target** = the nearest published level on the profit side;
- **invalidation** = the nearest published level on the loss side.

| family | rule |
|---|---|
| A structure target | exit at the target price when a bar trades to it |
| B opposite wall target | target = the nearest opposite-side `wall_v2` price (C1J only) |
| C invalidation | exit at the close of the first bar that closes beyond the invalidation level |
| S3 (primary structure) | A + C. If one bar does both, C is assumed (conservative) |
| D wall-interaction invalidation | C1J only: LONG exits when the nearest bid wall ends `CONSUMED_CANDIDATE` while the 60 s flow is sell-side. SHORT mirrored |
| E flow reversal | C1J only: exit when the 60 s flow side flips against the position |
| F breakeven | S3, but once a bar has traded to the midpoint between entry and target, the invalidation becomes the entry price |
| G structure trailing | S3, but at every close the invalidation moves to the current nearest loss-side level whenever that is tighter |
| H fixed time | exit at the close after Hmax bars (also reported at 5 and 20) |
| I fixed percent | TP and SL each 1·ATR(T) from entry, at entry. Scale-adaptive, not a fixed %. Same intrabar convention as S3 |

**Costs.** Round trip 11.25 bp (fee 11.0 + 0.25; D5_4 contract l.62, as reused by Q123), plus the
actual (Bybit) funding from D1 for each funding timestamp strictly inside the hold, signed by side.
C1J trades (Binance) add the Binance funding rate of any 00/08/16 UTC funding timestamp strictly
inside the hold, read by GET from Binance `/fapi/v1/fundingRate`.

**Per family and cohort**, the following are reported:
- n;
- mean, median and p5 net bp;
- win rate;
- median MFE and MAE;
- median hold in bars;
- worst trade;
- opportunity loss = MFE − realized;
- timeout share.

**Primary comparison (Q6):** paired, on the same entries.
- S3 − H(Hmax) and S3 − I, mean of the paired net bp.
- 95% block-bootstrap CI.
- Holm over 6 TF × 2 cohorts × 2 comparators = 24 tests.
- **Structure exit superior at (T, cohort)**: n ≥ 100 and both paired differences > 0 with
  Holm-adjusted CIs excluding 0.
- Post-hoc selection of the best family is forbidden. Families other than S3 are reported
  descriptively.

## 7. Hold-state and entry-context research (Q4, Q8)

No HOLD/EXIT label is created. Only conditional outcomes are measured.

- **Hold states.** These are evaluated at every bar *t* of an H-family (fixed time) trade,
  `1 ≤ t < Hmax`, as booleans at that close:
  - (a) the entry invalidation level has not been closed beyond;
  - (b) the entry target has not been reached;
  - (c) a new level has been published between price and target;
  - (d) the sign of OI change over the last bar of T is aligned with the position (OI rises while
    price moves the position's way);
  - (e) the funding sign favours the position;
  - (f) the basis sign favours the position;
  - (g) C1J only: the same-side wall is present;
  - (h) C1J only: the 60 s flow side is with the position.

  The outcome is the net forward move in the position's direction from *t* to *t* + 5 bars, and
  from *t* to Hmax. The comparison is state true − state false, with a bootstrap over trades.
  A **hold candidate** needs n ≥ 50 trades on each side of the split and a Holm-adjusted CI
  (over all states × TF × cohort) that excludes 0.
- **Entry context.** These features are evaluated at the entry close:
  - reward/risk = distance to target ÷ distance to invalidation, split at 1;
  - distance to target in the §3 bins;
  - OI direction over the last bar;
  - funding sign;
  - basis sign;
  - C1J only: near wall same side / opposite side, and flow side.

  The outcome is S3 net bp. An **entry-context candidate** needs n ≥ 50 per arm and a
  Holm-adjusted CI excluding 0.

## 8. Sample gates (frozen)

Per reported cell, the gate reuses the bands proposed in the work order:

| n | label |
|---|---|
| < 20 | INSUFFICIENT: numbers are shown but not interpreted |
| 20-49 | EARLY |
| 50-99 | REVIEW |
| ≥ 100 | STRONGER |

The unit is the independent episode or trade, never the bar.

**Block bootstrap.** Blocks are contiguous runs of `Hmax` bars of T over entry time, with 2 000
resamples and seed 20261011. For C1J studies the blocks are 60 min.

## 9. Verdict (frozen mapping, highest applicable wins)

- **E. READY_FOR_MANUAL_CONTEXT_UI_V3_REVIEW**: (C or D) holds and a local preview has been
  built. The preview shows only items that passed, and no HOLD/EXIT/LONG/SHORT instruction text.
- **D. HOLD_EXIT_CANDIDATES_FOUND**: at least one "structure exit superior" or "hold candidate".
- **C. ENTRY_CONTEXT_CANDIDATES_FOUND**: at least one "entry-context candidate", or "relevant at T"
  on at least one timeframe.
- **B. MANUAL_CONTEXT_R0_PASS**: all §10 checks pass and at least one family reaches n ≥ 50, but
  no candidate qualifies.
- **A. NOT_ENOUGH_DATA**: no family reaches n ≥ 50, or a §10 check fails in a way that voids the
  measurement.

No result is promoted to an automatic signal under any verdict.

## 10. Reproducibility checks (must pass before any result is reported)

- Deterministic replay: two full runs produce byte-identical result files.
- Prefix invariance: structure and event rows up to a cut are identical when the input is cut
  there, at 6 cuts per timeframe.
- No future leak: every feature's time ≤ the decision time. Tested by perturbing all data after the
  decision time.
- Timeframe aggregation equals a direct recomputation from 1m (or from trades, for 15s).
- Structure parity: the light snapshot equals R1 `Engine.snapshot` nearest R/S on a sample run.
- Session boundary: C1J vanish classification treats a session or generation change as `UNKNOWN`.
- Rank-evicted vs consumed vs cancel-like: unit tests on constructed sequences.
- Cost accounting: hand-computed trades, including funding crossings.
- Manual trade alignment: fill times map onto the bar containing them, with no look-ahead.

## 11. Manual trade snapshot store (design only)

A read-only follower of the ledger and audit (never a hook in the order path) writes an entry
context snapshot when a position opens and an outcome row when it closes. The schema is in the
results report. Nothing is deployed.

## 12. UI V3 preview

This is built only if the verdict reaches C or D. It shows only what passed, on the timeframe where
it passed. Production is untouched.

## 13. Backfill of existing manual trades

Each trade is labelled by what context exists at its entry time:
- **CONTEXT_AVAILABLE**: inside C1J;
- **PARTIAL**: K1 structure and D1 derivatives only;
- **NOT_AVAILABLE**.

Missing layers are never estimated.

## Appendix A. Data audit (coverage only; no outcome was computed)

| source | span | granularity | venue | timestamp semantics | gaps / notes | forward-safe |
|---|---|---|---|---|---|---|
| K1 klines | 2020-03-25 → 2026-09-23 | 1m | **Bybit linear** | bar open, known at open + 60 s | D2 frozen, grid has no gap (3 416 484 rows checked) | yes (bar known at close) |
| K15 trades | 2026-09-09 → 22 | trade | **Bybit linear** | trade time | daily csv.gz ~69 MB, no published checksum | yes (15 s bar known at close) |
| funding | 2020-03 → 2026-09-23 | 8h | **Bybit** | funding time | | yes |
| index_1m / mark_1m | 2020-03 → 2026-09-23 | 1m | **Bybit** | bar open | | yes |
| open_interest_5m | 2020-08 → 2026-09-23 | 5m | **Bybit** | statistic time | | yes, if the value at 5m *t* is used from *t* + 5m |
| context (C1J) | 24.05 h, 86 580 rows | 1 s | Binance USD-M (collector) | `receive_ms` | 24H summary: reconnect 0, book resync 132, LIVE 93.1% | yes |
| wall_v2 (C1J) | 24.05 h, 200 840 events | event | collector | `receive_ms` | CLOSE reasons only `NOT_SELECTED` / `NO_USABLE_READING` | yes |
| wall_r0 (C1J) | 24.05 h, 370 929 events | event | collector | `receive_ms` | OPENED / ENDED / UNKNOWN | yes |
| flow (C1J) | in context, 5 s / 15 s / 60 s windows | 1 s | aggTrade WS | `receive_ms` | flow interruptions 103 / 24 h | yes |
| liquidation_forward | 2026-09-27 → | event | Bybit / Binance / OKX | event time | starts after K1; only C1J-adjacent | not joined |
| C1 | separate track | | | | not joined | n/a |
| Q123 store | 2026-10-10 → | event | derived from C1J | | all INSUFFICIENT (24H) | cited only |
| manual trades | manual_r1 provenance (USER 1 completed) + Binance LIVE fills | fill | Bybit paper / Binance | fill time | provenance mostly UNKNOWN_LEGACY | yes |
