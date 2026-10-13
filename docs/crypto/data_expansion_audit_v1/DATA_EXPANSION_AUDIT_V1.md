# US-B CRYPTO DATA EXPANSION AUDIT V1 (2026-10-13)

Data audit only. No strategy, no backtest, no return computed, no threshold searched, no VALIDATION or
HOLDOUT outcome read, no Q123 store opened, no download, no external API call, no server access, no
`find /`, no broad `du`, no root grep. Only known local paths. Nothing deleted, nothing committed.

## A. Audit scope

Paths inspected (one level at a time):
- `data/runtime/crypto/d5_2/archive/` : Binance spot, USD-M, COIN-M archive and OKX REST (D5.2).
- `data/runtime/crypto/BTCUSDT/` : the D2 collection (historical, realtime session features, aggTrades).
- `data/runtime/crypto/hot_edge_r0/archive/` : 900-symbol 5 m klines + funding (downloaded 2026-10-13).
- `data/research/crypto/*` : research outputs (market_context_r0 raw, manual_r1, manual_r2 and others).
- Docs for server-only collectors: D5.3 liquidation forward, Context Collector V1 (wall), Manual Recorder.

Not inspected: the production server (liquidation, wall_r0, Recorder episodes; metadata taken from local docs
and memory only, marked UNVERIFIED_THIS_AUDIT). Google Drive: the connector is not authorized in this
session, so nothing was read there.

## B. Inventory (raw, long-horizon sources)

| dataset | venue (verified by) | symbol / market | start .. end | grain | rows / files | fields | missing / dup | provenance | class |
|---|---|---|---|---|---|---|---|---|---|
| UM perp klines | Binance (archive path + parity vs REST, PTM) | BTCUSDT perp | 2021-02-01 .. 2026-09-22 | 1 m | 67 monthly + 22 daily | OHLCV, quote vol, taker_buy_quote | 0 / 0 | data.binance.vision, CHECKSUM verified | price/flow (exhausted) |
| UM mark / index klines | Binance | BTCUSDT | 2021-02 .. 2026-09-22 | 1 m | 67 + 22 each | OHLC | NaN 1.9 % in TRAIN basis | archive, CHECKSUM | basis input |
| UM funding | Binance | BTCUSDT | 2021-02 .. 2026-08 | per settlement (8 h) | 67 files | calc_time, interval_h, last_funding_rate | 0 | archive, CHECKSUM | funding |
| **UM metrics** | **Binance** (archive path; OI 31,301 BTC vs Bybit 4,415 BTC at 2021-02-01 00:00) | BTCUSDT perp | **2021-02-01 .. 2026-09-22** | **5 m** | 2,060 daily, 592,696 unique rows | sum_open_interest, sum_open_interest_value, count_toptrader_long_short_ratio, sum_toptrader_long_short_ratio, count_long_short_ratio, sum_taker_long_short_vol_ratio | 584 slots missing (0.10 %; 451 in 2021, 127 in 2024), 31,241 duplicates all byte-identical, 0 conflicting, 0 off-grid; blank values 34..83 per column | archive, CHECKSUM, 2,060/2,060 verified | **new information** |
| UM quarterly klines | Binance | BTCUSDT_210326 .. _261225 (24 contracts, contiguous) | 2021-02 .. 2026-09-22 | 1 m | 145 files | OHLCV | not audited row by row | archive, CHECKSUM | futures basis |
| COIN-M quarterly + index | Binance | BTCUSD_210326 .. _261225 | 2021 .. 2026-09 | 1 m | 291 files, 248 MB | OHLCV / index | not audited | archive | basis (other margin) |
| Spot klines | Binance | BTCUSDT spot | 2021-02 .. 2026-09-22 | 1 m | 67 + 22 | OHLCV + taker buy | not audited | archive | spot leg for carry / basis |
| OKX swap last / mark, index | OKX | BTC-USDT-SWAP | 2021-02-01 .. 2026-09-22 | 5 m | 593,280 rows each | OHLC | 0 gaps (manifest) | REST paging | cross-venue price |
| D2 historical OI | **Bybit** (manifest provider=bybit; values 7x below Binance) | BTCUSDT linear | 2020-08-04 .. 2026-09-22 | 5 m | 645,329 | open_interest (BTC) | 4 intervals missing | Bybit REST, d2.1 | OI (other venue) |
| D2 historical funding | Bybit | BTCUSDT linear | 2020-03-26 .. 2026-09-22 | 8 h | 7,118 | funding_rate | 0 | Bybit REST | funding (other venue) |
| D2 kline / mark / index | Bybit (no taker field) | BTCUSDT | 2020-03 .. 2026-09-22 | 1 m | ~3.42 M each | OHLC, volume, turnover | 0 | Bybit REST | price (other venue) |
| 900-symbol klines + funding | Binance | all USDT perps incl. delisted | per symbol, 2019 .. 2026-09 | 5 m / 8 h | 43,340 files, 6.67 GB | OHLCV, quote vol, taker buy; funding | not audited per symbol | archive, CHECKSUM | cross-section |

Short-window and forward-only sources are in section P.

## C. Price / flow already exhausted

Binance BTC 1 m price, volume, taker_buy_quote and every 10 m / 1 h / 4 h / 1 d flow state, alignment,
reversal and their combinations with price displacement and volatility: PTM-R1, R2, D1 and HOT-EDGE R0.
Not to be re-mined (section X).

## D. Open interest

- **Binance OI exists locally as a long historical series.** `um/daily/metrics/BTCUSDT`, 5 m,
  2021-02-01 .. 2026-09-22, 5.6 years, 0.10 % missing, same venue as the price and flow series. This
  corrects the earlier note "Binance OI NOT_AVAILABLE, only Bybit": the D5.2 archive downloaded it in
  September and no study has used it. Units: `sum_open_interest` in BTC, `sum_open_interest_value` in USDT
  (no price normalization needed; both given).
- Bybit OI: 5 m, 2020-08 .. 2026-09-22, 645,329 rows, 4 missing intervals, BTC units. A different venue,
  roughly one seventh of Binance's OI in 2021.
- No Binance REST snapshot store was needed or found; the archive series is the historical one.
- Timestamp semantics: `create_time` is a 5 m grid label. Whether it marks the start or the end of the
  measured interval is not documented in the file. Forward-safe use: treat the value as known only at
  create_time + 5 m. The first study must verify this before use.
- Classification: Binance metrics OI **LONG_TERM_PRIMARY**; Bybit OI **LONG_TERM_SECONDARY** (venue).

## E. Long / short positioning (Binance metrics, same file as OI)

| field | meaning | coverage | class |
|---|---|---|---|
| count_long_short_ratio | global account ratio: accounts net long / net short | 5 m, 2021-02 .. 2026-09 | LONG_TERM_PRIMARY |
| count_toptrader_long_short_ratio | top-trader account ratio | same | LONG_TERM_PRIMARY |
| sum_toptrader_long_short_ratio | top-trader position ratio | same | LONG_TERM_PRIMARY |
| sum_taker_long_short_vol_ratio | taker buy / sell volume ratio | same | **REDUNDANT** with taker_buy_quote already used |

Venue Binance only. Bybit or other-venue ratios: NOT_AVAILABLE locally. Blank values per column 34 to 83
rows in 5.6 years.

## F. Funding

| source | settlement time | rate | coverage | paid / received reconstruction |
|---|---|---|---|---|
| Binance BTCUSDT | calc_time (ms) + interval hours | last_funding_rate | 2021-02 .. 2026-08 (September 2026 not in the archive) | yes: rate x position notional at mark (mark klines local); PTM R1 already does it |
| Bybit BTCUSDT | timestamp_ms | funding_rate | 2020-03 .. 2026-09-22 | yes, with Bybit price |
| Binance 900 symbols | per settlement | per symbol, full history incl. delisted | 2019 .. 2026-09 | yes with the symbol's 5 m perp price |

Premium-index component (`premiumIndexKlines`): **NOT_AVAILABLE locally** (not in either archive). Mark minus
index gives the premium level at 1 m but not the published premium-index candles.

Classification: **BOTH** (DIRECTION_FEATURE and CARRY_RESEARCH). A separate carry track is possible:
- BTC cash-and-carry (spot long + perp short, or quarterly basis): spot 1 m, perp 1 m, mark, index, funding,
  24 contiguous quarterlies, COIN-M quarterlies are all local for 2021-02 .. 2026-09. READY.
- Cross-sectional funding carry across altcoins: perp prices and funding local for 900 symbols; altcoin spot
  klines are not local, and historical spreads are unknowable. READY_WITH_LIMITS.

## G. Basis / premium

- Mark minus index (perpetual premium), Binance 1 m: local, already used as a split in R2 (basis_signed).
- Quarterly futures basis (quarterly close vs index or spot), Binance USD-M 1 m, 24 contracts, and COIN-M:
  local, never used in PTM. This is the term structure of leverage demand, a different object from the
  perpetual premium.
- OKX mark and index 5 m: cross-venue check only.
Classification: quarterly basis **LONG_TERM_PRIMARY** (same venue); perp premium LONG_TERM_SECONDARY
(already touched).

## H. Liquidation

- Raw events: **server only**, D5.3 forward collector, canonical start 2026-09-27T10:35Z (Bybit allLiquidation
  WS raw side and size; Binance forceOrder aggregated; OKX REST canonical). About 2.5 weeks. Not local.
  UNVERIFIED_THIS_AUDIT. **FORWARD_ONLY.**
- No historical liquidation archive exists locally. Binance does not publish liquidation history in the
  public archive.
- A long-horizon proxy (abrupt OI fall together with a fast price move) can be built from Binance metrics.
  It is derived, not observed liquidation, and must be labeled as such.
- Worth collecting forward: yes, as the collector already does; usable for long research only after many
  stress events (the D5.3 gate is 30 down events).

## I. Trade-level

| dataset | period | rows | integrity | side | ordering |
|---|---|---|---|---|---|
| Binance aggTrades BTCUSDT | 2026-09-09 .. 2026-09-22 (14 days) | 18,052,124 | 0 ID gaps, 0 duplicate IDs, 0 timestamps out of order | is_buyer_maker (taker side exact) | 11.3 M consecutive same-ms rows: order inside a millisecond is by aggregate ID only |
| Bybit public trades BTCUSDT | 2026-09-09 .. 2026-09-22 (14 days) | not counted | not audited | side field | sub-ms timestamp |
| D2 realtime public_trade sessions | 2026-09-23, three short sessions | small | telemetry 0 gaps | yes | smoke only |

All of it lies **inside HOLDOUT** (2025-04 ..). Long-term alpha discovery: impossible. Short microstructure
experiments (burst, intensity, intra-minute imbalance): possible, but any use reads holdout-period data, so
it needs an explicit decision that this window is a separate diagnostic sample. **SHORT_WINDOW_DIAGNOSTIC.**

## J. Orderbook / wall

- Wall (wall_r0, Context Collector V1): server only, since 2026-10-10. **FORWARD_ONLY**. Unsuitable for
  long research.
- D2 orderbook_1s features (Bybit, 3 sessions on 2026-09-23, 174 + 1,462 + 1,742 rows): smoke-test only,
  UNUSABLE for research.
- No historical depth archive locally.

## K. Volatility / range

Realized volatility, intraday range and volatility regime are price-derived (**LOW_DERIVED**); D1 already used
them (V unit, VR). Implied volatility (Deribit) is not local (BTC-VOL-P0 noted Tardis free tier as a source).

## L. Manual trade data

- `manual_r1`: 17 trades, 2026-09-23 .. 09-24; provenance USER_MANUAL 1, TEST_UNSPECIFIED 13, SIZING_TEST 2,
  DEV_SMOKE 1 (the R1 conclusion was withdrawn for mixed provenance). Entry context with 4,483 to 78,723
  snapshots before entry; 680 matched controls.
- `manual_r2`: decision schema and provenance contract designed (reason codes, conviction, point-in-time
  fields); collection not started according to the schema status.
- Manual Recorder on the server: about 20 episodes (16 manual_only) per memory; Manual Snapshot V1 installed
  2026-10-12. UNVERIFIED_THIS_AUDIT.
- Overlap with long-term data: Recorder episodes fall in HOLDOUT time (2026-09/10), after the local archive ends
  (2026-09-22) for most of them. Overlap with Context/Wall: from 2026-10-10 only.
Usable as a research feature today: **no** (one labeled user decision).

## M. Cross-venue risks

| combination | class |
|---|---|
| Binance price/flow + Binance metrics OI / ratios | **SAME_VENUE_SAFE** |
| Binance perp + Binance spot + Binance quarterlies + Binance funding | SAME_VENUE_SAFE |
| Binance price/flow + Bybit OI | **DO_NOT_COMBINE** (OI 7x apart, different participants; and now unnecessary) |
| Binance price + Bybit funding | CROSS_VENUE_RISKY |
| Binance index vs OKX index | CROSS_VENUE_INTERPRETABLE (a sanity check) |
| Binance flow + Bybit / OKX liquidations (forward) | CROSS_VENUE_INTERPRETABLE only as market-wide stress counts, never as per-venue flow |
| D2 "BTCUSDT" folder name | provider is Bybit in every manifest; the folder name does not say so (the old trap) |

## N. New-information ranking (meaning only; nothing computed)

| source | economic information not in price / taker flow | rating |
|---|---|---|
| Binance OI | whether positions are being opened or closed behind a move | HIGH_NEW_INFORMATION |
| Global / top-trader account and position ratios | which side retail and large accounts hold | HIGH_NEW_INFORMATION |
| Quarterly basis term structure | price of leverage over months; carry available | HIGH_NEW_INFORMATION |
| Funding (as carry) | cost of holding a crowded side; a return source independent of direction | HIGH (carry), MEDIUM (direction; R2 already split on it) |
| Perp premium (mark - index) | short-term perp pressure | MEDIUM (touched in R2) |
| Liquidations (forward) | forced flow | HIGH, but FORWARD_ONLY |
| aggTrades microstructure | intra-minute order flow | MEDIUM, 14 days in HOLDOUT |
| Wall / depth | waiting liquidity | MEDIUM, FORWARD_ONLY |
| Taker long/short vol ratio | same as taker flow | REDUNDANT |
| Realized volatility / range | price-derived | LOW_DERIVED |

## O. Long-term usable datasets

LONG_TERM_PRIMARY: Binance metrics (OI, three positioning ratios), Binance funding BTC, Binance quarterly and
spot klines (basis / carry), Binance 900-symbol perp klines + funding (cross-section).
LONG_TERM_SECONDARY: Bybit OI, Bybit funding, OKX price series, COIN-M quarterlies.

## P. Short-window / forward-only

SHORT_WINDOW_DIAGNOSTIC: Binance aggTrades 14 d, Bybit trades 14 d (both in HOLDOUT).
FORWARD_ONLY: liquidation collector (since 2026-09-27), wall_r0 / Context Collector (since 2026-10-10),
Manual Recorder and Manual Snapshot (since 2026-09/10).
UNUSABLE: D2 orderbook_1s and ticker session files (smoke tests).

## Q. Missing datasets

Binance premiumIndexKlines; Binance metrics for symbols other than BTC; altcoin spot klines; historical
liquidations (no free archive); historical order book depth; implied volatility; any historical record of
the user's own decisions.

## R. Additional-data candidates (not downloaded)

| dataset | why | period | interval | expected size | cost | backfill |
|---|---|---|---|---|---|---|
| Binance premiumIndexKlines BTCUSDT | funding's premium component for a carry study | 2021-02 .. now | 1 m | ~80 MB | free (archive) | yes |
| Binance metrics for top altcoins | positioning for the cross-section | 2021-12 .. now | 5 m | ~10 to 25 MB per symbol | free | yes |
| Binance spot klines for altcoins with perps | spot leg for altcoin carry | 2021 .. now | 5 m | a few GB | free | yes |
| Deribit implied volatility | the only forward-looking volatility | 2021 .. now | 1 h | unknown | Tardis free tier limited, otherwise paid | partial |
| Historical liquidations | forced flow | 2021 .. now | event | large | paid only (Tardis / Coinalyze-type vendors) | vendor-dependent |
| User decision labels | discretionary research | from now | per trade | tiny | free | forward only |

## S. Track A, positioning direction (price + flow + OI + long/short)

**READY_WITH_LIMITS.** Same-venue Binance data for 5.6 years at 5 m, 0.10 % missing. Limits: (1) the
create_time semantics must be verified (use a +5 m lag); (2) the TRAIN window 2021-02 .. 2023-06 has been read
by seven earlier designs, so a new study must keep VALIDATION and HOLDOUT sealed and declare hypotheses
before reading any outcome; (3) Bybit OI must not be mixed in; (4) the taker ratio column is redundant.

## T. Track B, funding / carry

**READY** for BTC (spot, perp, quarterlies, mark, index, funding all local, same venue, 5.6 years).
**READY_WITH_LIMITS** for the altcoin cross-section (no altcoin spot, no historical spread, no premium index).

## U. Track C, liquidation / microstructure

**NOT_READY** for long-term research: liquidation is forward-only (2.5 weeks), trade-level is 14 days inside
HOLDOUT, depth is forward-only. A diagnostic microstructure sample is possible with an explicit holdout
exception. Recommendation: keep collecting; revisit when the liquidation gate (30 down events) is met.

## V. Track D, personal discretion

**NOT_AVAILABLE** today (one labeled user trade). The schema exists (manual_r2); collection has not started.
It becomes READY only after the user records decisions, forward.

## W. Recommended next tracks (max 3)

1. **Track A on Binance metrics** (OI and the three positioning ratios, same venue as the price and flow
   series). Best on sufficiency and independence. Highest leakage care needed (timestamp semantics, heavily
   read TRAIN).
2. **Track B, BTC funding and basis carry** (perp funding plus quarterly basis plus spot). Independent of
   direction, long, same venue, all local.
3. **Track D, start collecting user decision labels now** (forward only), so a discretionary study becomes
   possible later.

## X. What not to research again

- Any new combination of Binance BTC price, volume, taker_buy_quote and flow states (10 m .. 1 d), alignment,
  reversal, price displacement or realized-volatility regimes as direction predictors.
- Hot-coin entry by 1 h range, 24 h move or volume surge.
- State-based HOLD, loss-exit or profit-protection rules on top of the completed-day flow thesis.
- Bybit OI joined to Binance flow.
- The taker long/short volume ratio as if it were positioning.

## Y. Final verdict

**C. STRONG_NEW_RESEARCH_INPUT_AVAILABLE.**

The verdict is about input data, not about an edge. A same-venue Binance OI and positioning series
(5.6 years, 5 m, 0.10 % missing, checksum-verified) and a full BTC basis / funding term structure are already
on disk and have never been used. The earlier belief that long Binance OI did not exist was wrong. Liquidation,
microstructure, wall and personal-decision data remain short-window or forward-only.

STOP. No backtest, no outcome, no validation or holdout read.

---

# ADDENDUM: VENUE CONSISTENCY AUDIT (execution venue = Binance USD-M)

Added on the user's request during the same session. Rules are unchanged: no strategy rerun, no outcome,
no VALIDATION / HOLDOUT outcome. The price comparison below uses TRAIN minutes only and compares prices,
not trade results. Output `data/research/crypto/venue_price_diff_train_v1.json`.

## V1. File-level venue classification (provenance + schema + values)

| dataset (path) | class | evidence |
|---|---|---|
| `d5_2/archive/binance/futures/um/**` (klines, mark, index, funding, metrics, quarterlies) | **BINANCE_PRIMARY** | data.binance.vision keys in the manifest, CHECKSUM verified; Binance schema (taker_buy_quote, calc_time); metrics OI 31,301 BTC vs Bybit 4,415 BTC at 2021-02-01 |
| `d5_2/archive/binance/spot/**` | BINANCE_PRIMARY (spot market, not the execution market) | same |
| `d5_2/archive/binance_cm` (COIN-M) | BINANCE_PRIMARY (other margin type) | same |
| `hot_edge_r0/archive/**` (900 symbols) | BINANCE_PRIMARY | downloaded 2026-10-13 from data.binance.vision, CHECKSUM verified |
| `research/crypto/market_context_r0/raw/binance_kline_1m_*.jsonl` | BINANCE_PRIMARY | Binance REST copy; parity with the archive 31,680 / 0 (PTM) |
| `research/crypto/price_structure_r0/raw/kline_1m_*.jsonl` | **BYBIT** | collector URL api.bybit.com v5 /market/kline category=linear |
| `runtime/crypto/BTCUSDT/historical/{kline_1m, mark_1m, index_1m, open_interest_5m, funding}` | **BYBIT** (folder name does not say so) | every manifest `provider: bybit`; kline schema has `turnover`, no taker field; OI 7x below Binance |
| `runtime/crypto/BTCUSDT/historical/bybit_trades_daily` | BYBIT | Bybit public trade archive schema (tickDirection, trdMatchID, RPI) |
| `runtime/crypto/BTCUSDT/historical/aggtrades_daily`, `BTCUSDT-aggTrades-2026-09-12.csv` | BINANCE_PRIMARY | Binance aggTrades schema (agg_trade_id, is_buyer_maker) + .CHECKSUM files |
| `runtime/crypto/BTCUSDT/features/*`, `realtime/*` (D2 sessions) | BYBIT (D2 realtime collector) | D2 collector; smoke sessions only |
| `runtime/crypto/d5/grid_v1.npz` (research cache) | **BYBIT** (+ Binance spot column) | built by `research/dataset.py` from the Bybit files above |
| `d5_2/archive/okx/*` | OTHER (OKX) | OKX REST endpoints in the manifest |
| `runtime/crypto/reference/fee_source_verification_v1.json` | BYBIT (fee page) | adopted taker 0.055 %, maker 0.02 %, Bybit VIP 0 |
| Context Collector wall_r0 (server) | BINANCE_PRIMARY | collector report: Binance depth WS, aggTrade WS, REST /fapi/v1/depth |
| Liquidation forward (server) | mixed: Bybit, Binance (aggregated forceOrder), OKX | D5.3 docs; per-venue files |
| Manual Recorder / Snapshot (server) | BINANCE_PRIMARY (Binance live account) | binance_live records |

No file was found whose content contradicts a Binance label. The one known mislabel risk is the D2
`BTCUSDT/historical` folder, which is Bybit without saying so.

## V2. Research lineage by venue

| study | price venue (entry/exit/high/low, MFE/MAE, stops) | flow venue | OI venue | funding venue | fee / execution assumption | class |
|---|---|---|---|---|---|---|
| PTM R1 / R2 / D1 | Binance perp 1 m | Binance taker_buy_quote | not used | Binance | Binance 5 bp/side (measured) | **VENUE_CONSISTENT** |
| HOT-EDGE R0 | Binance perp 5 m | not used | not used | Binance | all-in scenarios | VENUE_CONSISTENT |
| Wall Lifecycle R1 + advisor R0 | Binance REST klines / premium | Binance | Binance OI 5 m | Binance | Binance funding hours | VENUE_CONSISTENT |
| Market Context R0 | Binance kline (outcomes) + Bybit 1 m levels (Price Structure) | Binance taker | n/a | n/a | n/a | **CROSS_VENUE_INTENTIONAL** (module compares the two series minute by minute and documents it) |
| Market Context "R1" | not found as a separate study (Market Context V1 is a UI product) | | | | | n/a |
| Price Structure R0 / R1 / R2 | Bybit 1 m | n/a | n/a | n/a | no P&L (level quality only) | NEEDS_RECHECK only if its levels are shown on a Binance chart; no performance claim affected |
| Manual Context & Exit R0 / R0.1 | **Bybit 1 m + Bybit 15 s from Bybit trades** | Bybit trades | Bybit | Bybit | exit comparisons on Bybit prices for a Binance-traded user | **NEEDS_RECHECK** |
| C1 signal (production C1 AUTO) | S1 = ln(Bybit perp last / Binance spot) | n/a | **Bybit OI 1 h change** | n/a | executes on Binance | **CROSS_VENUE_UNJUSTIFIED for execution**: the signal is defined on Bybit inputs, the orders are on Binance |
| C1 Exit E1 / E2, D6 / D6-C, C2, C1 strength, R0 regime, C1 regime R1 | **Bybit 1 m** (`dataset.py`) | Bybit-derived | Bybit | Bybit | Bybit fee 0.055 % taker | **NEEDS_RECHECK** (Bybit price paths and fees, Binance execution) |
| BTC-P1 / P1 forward / P2, BTC-VOL-A / VOL-P0, D5.1 long horizon, experimental_ls, study.py | Bybit 1 m grid (+ Binance spot, Bybit-minus-Binance features) | Bybit | Bybit | Bybit | mostly no execution (direction / volatility skill) | NEEDS_RECHECK where an economic conclusion was drawn (VOL-P0 move economics); skill-only results CROSS_VENUE_INTERPRETABLE |
| D5.4 / D5.5 (closed, SURVIVE 0) | Bybit grid | | Bybit | Bybit | | NEEDS_RECHECK low priority (closed, nothing deployed from it) |

Every study whose entry, exit, high/low, MFE/MAE, stop touch or catastrophic level was computed on Bybit
prices while the user executes on Binance is marked NEEDS_RECHECK (section 6 of the requirement).

## V3. Binance vs Bybit price difference (TRAIN minutes, 2021-02-01 .. 2023-06-30, 1,267,200 / 1,267,200 matched)

Bybit / Binance - 1, in bp, per 1 m bar:

| field | mean | median abs | p95 abs | p99 abs | p99.9 abs | max abs | share > 5 bp | share > 20 bp |
|---|---|---|---|---|---|---|---|---|
| open | -0.46 | 2.04 | 9.1 | 14.0 | 20.6 | 776 | 17.8 % | 0.12 % |
| high | -1.91 | 2.29 | 10.4 | 15.7 | 23.5 | 1,737 | 21.7 % | 0.23 % |
| low | +0.94 | 2.16 | 9.3 | 14.0 | 22.4 | 610 | 19.4 % | 0.16 % |
| close | -0.46 | 2.04 | 9.1 | 14.1 | 20.8 | 775 | 17.8 % | 0.12 % |

Bybit highs sit lower and lows higher than Binance on average: Binance wicks are wider. Typical gap 2 bp,
tail spikes up to 6 % to 17 % on single minutes (wick events on one venue only).

Stop-touch disagreement (generic, not a strategy: anchor = each venue's own open at every UTC hour; does a
stop at distance X get touched within the window on one venue but not the other; both directions):

| window | stop 0.5 % | 1.0 % | 1.84 % (20x safety) | 3.84 % (10x safety) |
|---|---|---|---|---|
| 60 min: disagree / touched by either | 7.9 % | 10.1 % | 9.2 % | 9.9 % |
| 24 h: disagree / touched by either | 1.6 % | 2.3 % | 3.1 % | 3.5 % |

So for a stop that gets hit, the two venues disagree on whether it was hit in about 1 of 10 one-hour cases and
1 of 30 one-day cases. That is large enough to change catastrophic / liquidation counts and short-hold exit
results, which is why the Bybit-priced performance studies are NEEDS_RECHECK. It is small for multi-day
mean-return conclusions (mean gap < 0.5 bp at open / close).

## V4. Decisions

**Binance data only, from now on:**
- Every performance study: entry and exit prices, high/low paths, MFE/MAE, stops, safety and liquidation
  geometry, funding cost, fees. Execution venue = Binance USD-M.
- OI and positioning for direction research: Binance metrics (same venue, 5.6 years). Bybit OI is not
  needed any more for this purpose.
- Funding / carry research on BTC: Binance funding, mark, index, spot and quarterlies.
- Any revisit of C1, its exits or the Manual Context exit conclusions: rerun on Binance prices and Binance
  fees before relying on them (a separate, authorized task; not done here).

**Bybit data worth keeping as BYBIT_CROSS_VENUE_FEATURE (never silently merged):**
- Bybit OI and funding: as a cross-venue positioning divergence feature (Binance vs Bybit crowding), from
  2020-08 / 2020-03, i.e. a few months longer than Binance.
- Bybit minus Binance price spread: the BTC-P2 F6 features already treat it this way; legitimate as a
  feature, never as an execution price.
- Bybit liquidation stream (forward): raw side and size, which Binance only publishes aggregated.
- Bybit 1 m klines before 2021-02: longer history, only as context with an explicit venue label.

**Rules:** a mixed-venue column must carry the venue in its name (as `spot_basis_bybit` does); the D2
`runtime/crypto/BTCUSDT/historical` folder should be treated as Bybit in every new contract; no file is
deleted or renamed by this audit.

## V5. Effect on the verdict

Unchanged: **C. STRONG_NEW_RESEARCH_INPUT_AVAILABLE**, and stronger on venue: the new primary inputs
(Binance metrics OI and positioning, Binance quarterly basis, Binance funding) are all on the execution
venue. New finding: the C1 family and the Manual Context exit study were priced on Bybit with Bybit fees
while execution is Binance, so their conclusions need a Binance recheck before reuse.

STOP.
