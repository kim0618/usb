# Manual Market Context V1 - display contract

    identity: mc-display.v1

This document fixes what the Manual Market Context panel shows, where each figure comes from, and
what the panel is forbidden to do. It is frozen **before** the panel is built and before any live
reading is looked at, for the same reason the research contracts in this repository are frozen
before results: a display that decides its own vocabulary after seeing the data will always find a
vocabulary that flatters the data.

This is a **display** contract. It creates no research question, moves no threshold and publishes
no verdict. Every number on the panel is produced by an already frozen contract elsewhere, and this
document's job is to say which one, and to say what the panel does when that source has nothing to
give.

---

## 1. Scope, and the bans that define it

The panel is a read-only context strip beside the manual order ticket. It exists so that a person
placing an order by hand can see, in one place, what the already-validated layers of this system
currently observe.

It is **banned** from all of the following, and each ban is enforced by a test rather than promised:

| # | Ban | Enforced by |
|---|---|---|
| B1 | No LONG/SHORT verdict, no direction word, no score of any kind | vocabulary scan over the package AST |
| B2 | No newly generated probability | no model is loaded, no inference is run; the only probabilities shown are read verbatim from a frozen research artifact |
| B3 | No AUTO mutation | the package imports nothing from `app.crypto.c1.runtime`, `app.crypto.paper`, `app.crypto.live`, `app.crypto.terminal` |
| B4 | No order path change | same import ban; a subprocess import proves the trading modules are not loaded |
| B5 | No C1/C1x contract change | the C1 store is read as bytes; no C1 module is imported, no C1 file is written |
| B6 | No new Price Structure research and no threshold change | the R1 engine, the R1 level book and the R0-calibrated thetas are imported and called, never reimplemented; the thetas are asserted equal to the frozen pair |
| B7 | No production deploy | the panel is served by its own ASGI app on its own port, registered on nothing; nothing in the deployed terminal imports it |
| B8 | No promotion of R2's extra level sources to a production rule | the level book is R1's; previous-session high/low is carried as **context**, outside the book, which is where R2 measured it as worth one reversal in twenty-seven |

The panel writes exactly one thing: the append-only forward context journal of section 10. It opens
no other file for writing, and it never writes into the Market Structure V0 journal, the C1 store,
the paper ledger or the live mirror.

---

## 2. Layers, priority, and why they are separate

Five layers, always rendered in this order, top to bottom:

    1. PRICE       price structure
    2. LIQUIDITY   resting book
    3. FLOW        aggressive trades
    4. DERIVATIVES open interest, funding, basis
    5. AUXILIARY   closed research results, BTC only

The order is the priority order and does not change with the data. A layer that has nothing to show
keeps its slot and says why, because a panel that silently reorders itself teaches the operator to
look in the wrong place.

The layers are never combined. There is no joint reading, no agreement count and no "three of five
agree" row. Market Context R0 measured the joint model directly
(`docs/crypto/market_context_r0/RESULTS_MC_R0.md`): structure x wall x flow was `NOT_MEASURABLE` on
3.61 hours of book data, and the two frames that were measurable came in at +4.83 bp against a
11.25 bp round trip, with the wall x flow family's significant cells carrying **the wrong sign**.
A combined reading is therefore not a feature this panel is withholding for later; it is a reading
the data has already refused to support.

---

## 3. Symbol support matrix

Every validated layer in this system was built on BTCUSDT. The Market Structure V0 collector's
websocket URLs name `btcusdt` in the frozen contract; Price Structure R0/R1/R2 calibrated their
thetas on BTCUSDT; C1, BTC-P1, BTC-P2 and Directional Probability R0 are BTC studies.

| Layer | BTCUSDT | ETHUSDT | SOLUSDT |
|---|---|---|---|
| PRICE | full | `UNAVAILABLE / NOT_CALIBRATED_FOR_SYMBOL` | `UNAVAILABLE / NOT_CALIBRATED_FOR_SYMBOL` |
| LIQUIDITY | full | `UNAVAILABLE / COLLECTOR_IS_BTC_ONLY` | `UNAVAILABLE / COLLECTOR_IS_BTC_ONLY` |
| FLOW | full | `UNAVAILABLE / COLLECTOR_IS_BTC_ONLY` | `UNAVAILABLE / COLLECTOR_IS_BTC_ONLY` |
| DERIVATIVES | full | full | full |
| AUXILIARY | full | **not rendered** | **not rendered** |

Two rules follow, and both are tested:

* **No BTC figure may appear under ETHUSDT or SOLUSDT.** The payload for a non-BTC symbol is built
  by a path that never reads the BTC structure engine, the BTC journal or the BTC research
  artifacts. It is not a BTC payload with fields blanked out.
* **AUXILIARY is absent, not empty, for a non-BTC symbol.** An empty auxiliary panel on ETHUSDT
  would read as "no signal right now" when the truth is "this research was never run on this
  instrument".

DERIVATIVES is per-symbol because its source is a per-symbol public endpoint, so there is no BTC
figure to leak.

---

## 4. Quality vocabulary

Each layer publishes exactly one state from this closed set, plus the reasons behind it:

| State | Meaning |
|---|---|
| `LIVE` | the layer's source is current within its own freshness bound |
| `STALE` | the source was readable but is older than its freshness bound |
| `PARTIAL` | the source is current and some of the requested quantities are observable only as a bound |
| `UNKNOWN` | the source gave nothing, or gave something this panel cannot interpret |

`UNAVAILABLE` is a fifth value used only in the symbol matrix of section 3: it means the layer does
not exist for this instrument, which is a different statement from "its data is missing right now".

Rules:

* **A layer that is short of data reports `UNKNOWN`. It never reports a neutral reading.** There is
  no `NEUTRAL` state in this vocabulary and no row that renders as a balanced middle when the input
  is absent. Market Context R0's own state labelling found `NEUTRAL` in 0.4% of samples while
  `UNKNOWN` was 12.9% - a panel with a neutral default would spend most of its missing-data time
  claiming balance.
* **The whole panel has no single rolled-up state.** There is no "Market Context: NEUTRAL" header.
  The panel header carries the per-layer states side by side and nothing else, so a live liquidity
  layer cannot lend its freshness to a stale structure layer.
* **A null is rendered as `-` plus its reason, never as `0`.** Zero is a real reading of a depth
  band and of a flow window, and the two must stay apart. (`-`, not an em dash: the existing crypto
  screens use `-` for a missing value.)
* **Colour never carries a state by itself.** Every state is rendered as its word; colour is an
  additional cue only.

Freshness bounds are read from the source contracts, not restated here:

* book: `market_structure_v0.contract.DEPTH_STALE_MS`
* trades: `market_structure_v0.contract.TRADE_STALE_MS`
* journal file age: `liquidity_map.view.JOURNAL_STALE_MS`
* structure: one closed minute plus a 90 s grace, because the 1m bar is only knowable at its close
* derivatives: 60 s for the mark/funding read, and the open-interest history's own 5 m period

---

## 5. PRICE - price structure

**Source.** The R1 curated level book, unchanged. `app.crypto.research.price_structure_r1.engine`
and `.levels`, the R0 swings/VWAP/ATR modules, and the thetas frozen by R0's held-out calibration
(`theta_1h = 3.0`, `theta_5m = 6.0`). The projection of a published minute into display fields is
`app.crypto.research.market_context_r0.structure.ContextEngine`, which Market Context R0 already
used for exactly this purpose. None of these are modified or re-implemented.

**Venue.** Binance USD-M `BTCUSDT`, 1m klines from the public `/fapi/v1/klines`. Price Structure
R0/R1/R2 ran on Bybit; Market Context R0 recomputed the same curation on Binance and reported
`repaint 0`, `vanished 0` over 48,001 minutes with the active-level distribution identical to Bybit
(median 9 / p95 13). The reason the panel uses Binance and not Bybit is the measured basis tail:
median 1.40 USDT but **max 622.9 USDT (0.894%)** against a level tolerance only 0.25% wide. One
venue, and it is the venue the liquidity journal and the live order path are on.

**Series anchoring, and why it is not a rolling window.** The engine is a single pass over a bar
series, so the panel re-runs it as new minutes close. The series start is **anchored once at process
start and never moved**. A rolling window would hand the operator a book whose level identities and
lifecycles change because history fell off the back - repaint by another name. With a fixed anchor,
every later run is a prefix extension of the same series, and R1's prefix-truncation invariance
(verified 6/6 in R1 and again in Market Context R0) makes the already-published minutes identical
run to run. The panel publishes the anchor, the series length and the recompute cost so that the
basis of every figure is visible.

Default anchor: 30 days before process start, floored to a UTC day boundary. The published window
is the last 24 h; everything before it is warmup, which gives the latest minute at least 29 days of
book history against the 20 days R0/R1 warmed up with.

**The published minute is the last *closed* minute.** A partial bar is never shown, so the panel's
price structure trails real time by up to one minute by construction, and says so.

**Fields.**

| Field | Source |
|---|---|
| `trend_structure` | R1 snapshot (`BULLISH` / `BEARISH` / `NEUTRAL_STRUCTURE` etc. - the engine's own closed set) |
| `last_high_label`, `last_low_label` | R1 snapshot (`HH`/`LH`/`HL`/`LL`) |
| `nearest_resistance` price, distance %, lifecycle, strength, touches, confirm_tf, sources, origin | R1 published level book |
| `nearest_support` - same fields | R1 published level book |
| `breakout_state`: direction, level id, confirmed at, age, confirm tf | R1 event engine, last breakout |
| `retest_state` + direction + level id | R1 retest pool |
| `failed_break_state` | R1 event engine |
| `last_sweep` direction, age, level id | R1 event engine |
| `range_state`, `range_high`, `range_low`, `range_position` | R1 range state |
| `active_level_count`, `published_prices` | R1 publication |
| `atr_5m`, `atr_1h`, `vwap_z`, `vwap_position` | R0 modules via R1 |
| `prev_session_high` / `_low` + distances | **context block, outside the level book** |

`trend_structure` is a structural label from the swing sequence, not a direction call, and it is
rendered under the heading it has in R1 with no colour that reads as a recommendation.

**Previous session high/low.** Carried as a separate context block with its own heading, never
merged into the level book and never counted in `active_level_count`. R2 measured this source
(token `D`) merged into the book: 106 candidates, 65 of which merged into existing levels, net gain
one reversal out of twenty-seven - and that one gain was not a new line but a `PROVISIONAL` level
being promoted by the merge. One case in a 27-case sample is the resolution limit of that
measurement, so the source stays out of the production rule and on the screen as context, which is
ban B8.

---

## 6. LIQUIDITY - the resting book

**Source.** `app.crypto.liquidity_map.view`, unchanged, reading the Market Structure V0 journal and
its state checkpoint. The wall rule is the frozen `lm-wall.v2`
(`docs/crypto/liquidity_map_v1/WALL_RULE_V2.md`); the continuity rule is the frozen
`lm-continuity.v5`. The panel selects nothing and re-ranks nothing: it reads
`sides.ASK.nearest_wall`, `sides.BID.nearest_wall`, the depth bands and the coverage block as the
viewer already publishes them.

**Fields.** Per side: nearest wall price, notional, qty, distance in bps and %, persistence (with
its source `OWN`/`CARRIED` and the continuity status), coverage, bin bounds and bin membership.
Plus: depth per band with its coverage and lower-bound flag, band imbalance, and the observed book
interval (`known_low`..`known_high`).

**The four ways there can be no wall**, which the panel must keep apart because they call for
opposite reactions:

| Word | Meaning |
|---|---|
| `NONE` | the book is current and complete over the observed interval, and no candidate in it qualifies under `lm-wall.v2` |
| `PARTIAL` | the book is current but the quantity is a lower bound - the `limit=1000` snapshot covers about ±0.15%, so the ±0.25%, ±0.5% and ±1% bands are bounds, not numbers |
| `STALE` | the journal was readable but its newest sample is older than the freshness bound |
| `UNKNOWN` | no journal, no session, an unsynced book, or a wall set that could not be reconstructed |

**Observed book range is published with every wall figure.** Outside the snapshot's known interval
the book is not thin, it is unseen, and the panel's axis and its "no wall above" statements are
scoped to that interval explicitly.

**A wall is not a signal.** Market Context R0 measured near-wall presence: an ask wall within 10 bp
was present in **99.0%** of samples and a bid wall in 98.5%, averaging 8.8 per side when present. A
condition true 99% of the time cannot separate states. The panel therefore renders wall presence as
a measurement with its distance and notional and **never as a bullish or bearish mark**, and it
carries that 99% figure on screen next to the wall rows so the operator reads presence correctly.

---

## 7. FLOW - aggressive trades

**Source.** `app.crypto.liquidity_map.view.flow_view`, unchanged: the V0 contract's 5 s / 15 s /
60 s windows with their own coverage, plus the trade stream's freshness. Aggressor side is the V0
rule (`aggressor = SELL if the buyer is the maker, else BUY`), restated by the viewer, not
recomputed here.

**Fields.** Per window: trade count, aggressive buy and sell in BTC and USDT, net, imbalance,
coverage with its reason and age, and the lower-bound flag. Plus the trade stream's connection
state and age, and the window-level freshness verdict.

**Wall disappearance is classified, never asserted.** When a near wall that was in the previous
reading is gone from the current one, the panel publishes one of:

| Word | Condition |
|---|---|
| `CONSUMED_CANDIDATE` | price traded inside the wall's bin during the interval the wall vanished in |
| `CANCEL_LIKE` | price did not reach the bin during that interval |
| `UNKNOWN` | the interval is not determinable: a coverage gap, a generation change from a resnapshot, or a session boundary |

`CONSUMED` on its own is not in the vocabulary and may not be rendered. The reason is both a
contract limit and a measurement. The limit: `btc-ms.v0.1` does not prove order identity, so a bin
that fell below the `lm-wall.v2` notional floor through partial fills lands in the same cell as one
that was cancelled. The measurement: of near walls that disappeared, price had touched the bin in
**3.8% (ask) / 6.9% (bid)** of cases - walls are overwhelmingly cancelled, not eaten, and the whole
3.61 h journal held **15** consumption candidates. A panel that printed `CONSUMED` would be wrong
about nine disappearances in ten.

**Absorption is a candidate only.** An `ABSORPTION_CANDIDATE` is published when, inside one
reading interval, aggressive flow on one side exceeded the V0 window's observable floor while a
qualifying wall on the opposite side of mid stayed present and price did not close through its bin.
It is published with its inputs, with `order_identity_proven: false`, and with the word `CANDIDATE`
in the value itself so the string cannot be rendered without it. It is never aggregated, never
counted toward anything and never coloured as a direction. Given the 15 candidates the journal
produced over 3.61 h, this row is expected to be empty almost always, and an empty row here reads
`NONE` with its coverage, not "no absorption".

---

## 8. DERIVATIVES

**Source.** Binance USD-M public endpoints, read-only, unsigned, per symbol:

| Field | Endpoint |
|---|---|
| `open_interest` (base units) and its `time` | `GET /fapi/v1/openInterest` |
| `open_interest_change` over 5 m / 15 m / 1 h / 4 h, in base units and in USDT value | `GET /futures/data/openInterestHist` (`period=5m`), differenced against the newest closed bucket |
| `funding_rate` (last), `next_funding_time` | `GET /fapi/v1/premiumIndex` |
| `mark_price`, `index_price` | `GET /fapi/v1/premiumIndex` |
| `basis` = `mark - index`, `premium_pct` = `(mark - index) / index` | the two prices above |

`basis` and `premium_pct` are arithmetic restatements of two published prices and are labelled as
such. Nothing else is derived: no OI-versus-price reading, no funding regime, no crowding
measure, no combination of any derivatives field with any other layer. That is the section 4 ban on
new derivatives signals, and the panel satisfies it by having no code that takes two of these
fields and returns a third, apart from the subtraction named above.

The endpoints are public market data. The package holds no key, signs nothing and reaches no
`/fapi/v*` path that requires `USER_DATA` or `TRADE` permission; the allowed URL list is a constant
and is enforced by a guard, the way the V0 collector enforces its three.

---

## 9. AUXILIARY - closed research, BTC only

Rendered for BTCUSDT only (section 3). Both entries are read-only and both carry their own weight
label in the payload, not only in the surrounding prose.

**C1 / C1x - `auxiliary`.** The latest C1 state is read from the C1 store's append-only files as
bytes. No C1 module is imported, no C1 runtime is constructed, no C1 file is written, and the panel
does not evaluate the C1 contract - if the store is empty or absent the row reads `UNKNOWN` with the
path it looked in. C1's own exit research (`project_usb_c1_exit`) returned `E1 INCONCLUSIVE`
(+10.27 bp, CI [-9.3, +30.0]) and `E2 PROMISING 0`, so the row is labelled `auxiliary` and is not a
recommendation.

**Directional Probability - `weak_auxiliary`.** Read verbatim from the frozen artifact
`data/research/crypto/directional_probability_r0/results.json`. What is shown is that study's
**verdict and its scope**, which are `verdict = WEAK_DIRECTIONAL_MODEL`, `usable_horizons = []` and
`weak_horizons = [30, 60, 120]`, together with the sample window and the per-horizon AUC as the
artifact records them. **No probability is computed for the current moment.** No model is loaded and
no inference is run; there is no live number here to be mistaken for one.

The row also carries the two later results that bound how this may be read, because a reader who
sees an AUC of 0.56 without them will over-read it:

* BTC-P1 found a calibrated **magnitude** predictor whose directional AUC was 0.502-0.533.
* BTC-P2 tested the direction layer and got `STRONG 0/64` with all four primary cells **below** 0.5,
  while the P1 gate made direction *worse*; `P3 NOT_AUTHORIZED`, direction research
  `CLOSED_PENDING_NEW_INFORMATION`.

`weak_auxiliary` therefore means: a published study that did not reach usable strength on any
horizon, shown so that the operator knows what exists and what it concluded.

---

## 10. Forward context journal

Purpose: keep the raw material that Market Context R0's unanswered questions Q1-Q3 need, so that the
3.61 h ceiling that made T1 `NOT_MEASURABLE` is not hit again. R0 established that a family needs
roughly 3-4 weeks of continuous Layer B collection to fill a 30-event gate, and that Binance's
public archive sells aggTrades, bookTicker and percentage depth summaries but **not** level-resolution
quotes - so the book layer exists only if it is collected.

**It is not a trade journal.** It is not linked to an order, it does not require a manual trade to
exist, and it records no intent, no decision and no outcome. It is a context tape.

**Append-only.** One JSON object per line, one file per UTC day, `fsync` on append, never rewritten
in place. A torn final line is dropped by the reader, not repaired.

Required fields per record:

    record, contract, written_at_ms, symbol
    price                     last closed 1m close, and the mark price with its own timestamp
    structure.nearest         price, side (SUPPORT/RESISTANCE), type (lifecycle + origin), distance_pct
    structure.breakout_state, structure.retest_state
    wall.ask / wall.bid       state (OK/NONE/PARTIAL/STALE/UNKNOWN), price, notional, distance_bps
    wall.persistence_ms       with its source and continuity status
    flow.w5s / w15s / w60s    buy, sell, net, imbalance, coverage
    depth                     per-band notional with coverage, and the observed book interval
    coverage                  the liquidity layer's coverage block verbatim
    derivatives               open_interest, oi_change_5m/15m/1h/4h, funding_rate, basis, premium_pct
    quality                   the per-layer state and reasons at the moment of writing

Every field is nullable and carries its reason when null. A record with a `STALE` liquidity layer is
written, not skipped: the gap is itself the datum that explains why a later analysis has no row.

Cadence: one record per closed minute while the panel runs, plus one on each structure change. The
journal is a research input and nothing in the panel reads it back.

---

## 11. What the panel never does

Collected here so that the list can be read in one place and tested as one list:

* no direction, no score, no rank of one side against the other;
* no newly generated probability, and no model inference of any kind;
* no combined or joint reading across layers, and no agreement count;
* no `NEUTRAL` state and no roll-up of the five layers into one verdict;
* no `CONSUMED` assertion on a vanished wall, and no absorption verdict;
* no wall-presence-as-signal rendering;
* no BTC figure under a non-BTC symbol;
* no write outside the forward context journal;
* no order, no arm, no AUTO control, and no C1 contract evaluation;
* no production registration: its own app, its own port, imported by nothing that deploys.

---

## 12. Gaps closed during implementation

Twelve places where sections 1-11 did not decide something the code had to decide. They are closed
here, in the contract, rather than settled quietly in the code - the practice this repository
already uses (Market Context R0 closed eleven the same way in its section 17, and
`lm-continuity.v5` closed two).

**What this amendment is and is not honest about.** It was written after the panel was built and
**before** the section 10 preview verification was run, so no gap below is closed in a direction
chosen by looking at the verification. It was not written before the code ran at all: the panel was
smoke-run against the live venue and the existing journal during implementation, and two of the
gaps below (G2, G4) were found that way rather than by reading these sections again. What those
runs showed was operational state - which layers were `LIVE` and which `STALE` - and that is what
G2 is about.

| # | Gap | Closed as |
|---|---|---|
| G1 | Which coverage decides the LIQUIDITY layer's state | The **wall set's**. Three of the four depth bands are permanently `PARTIAL` on a `limit=1000` snapshot, so letting them decide would pin the layer to `PARTIAL` forever and leave nothing to mean "the wall set could not be proven complete". Each band still carries its own `≥` marker. |
| G2 | Whether FLOW has its own freshness | No: it is **floored by the journal's**. Every age inside a flow window is measured from the sample the collector wrote, by design, so on a twenty-hour-old record `trade_stream.age_ms` reads 80 ms and is true - 80 ms before that sample. Read alone it says the feed is live. Found on the first live reading of a stale journal, not by reasoning. |
| G3 | What exactly makes an `ABSORPTION_CANDIDATE` | Over the 60 s window, with `COMPLETE` coverage: the pushing side is the sign of the USDT split; aggressive USDT on that side is **at or above the notional of the nearest qualifying wall on that side**; that wall's bin was present in both readings; the mid path did not reach into the bin. The size comparison is the clause that matters - without it the row is "a wall is present and buyers are in front", which is true in about half of all samples because a near wall is present in 99% of them. |
| G4 | Which interval the vanished-wall touch test runs over | R0's `mids[anchor+1 : j+1]`: **excluding** the sample the wall was last seen in, **including** the sample it was found missing in. The first implementation kept the anchor and dropped the decision sample, which called a wall the price had just traded through `CANCEL_LIKE` - wrong in the direction of the 96% base rate, so it would have looked right. |
| G5 | Where the resnapshot generation comes from | The collector's own state file, with the wall rows as a fallback. An **unknown** generation is not a changed one: a book with no state checkpoint never publishes it, and treating that as a resnapshot would make every disappearance undecidable forever - which reads as "the panel cannot tell" when the truth is "nothing happened". |
| G6 | C1's freshness bound | Five minutes on the engine's own `last_decided_at_ms`, not on the file's mtime: an atomic rewrite touches the mtime whether or not a bar was decided. C1 decides once per closed 1m bar, so five missed decisions is an engine that is not running. This is the panel's display bound and changes nothing in C1. |
| G7 | Which C1 fields the row carries | Identity, instant and state. **Not** the shadow trade's plan - its entry instant, its entry price, its planned exit. Those four are a proposal, and a context panel carrying a proposal reads as one however it is labelled. |
| G8 | The journal's cadence key | Per symbol, and the minute is the structure layer's closed bar where there is one or the wall-clock minute where there is not. A shared cursor would make every symbol switch look like a context change and would let one symbol's unchanged reading suppress another's; without the fallback the tape would hold exactly one line for ETHUSDT and SOLUSDT for a whole run. |
| G9 | `breakout_state` with no breakout on record | The string `NONE`, rendered as an absence with its reason, never as a blank or a direction. |
| G10 | The sign convention of a level's distance | Signed `(level - close) / close * 100`, and the sign is rendered. A level 0.1% above and one 0.1% below are different places to be. |
| G11 | Whether a past minute may be shown | Only the **structure** layer, only through a route whose `mode` is `READ_ONLY_STRUCTURE_REPLAY`, and with the four layers that are not replayed named in the payload. The book and flow layers have no reassembled history in this viewer and the derivatives endpoints have none at this resolution; four live layers beside one historical one under a single timestamp is the worst thing a context panel can do. |
| G12 | How the directional artifact's buckets are named | Read out of the artifact. That keeps the panel independent of the study's labelling, and it keeps the panel's own source free of the direction vocabulary ban B1 forbids it to contain - the words belong to the frozen artifact and are rendered as data read from it. |

**Unchanged by this amendment:** every ban in section 1, the layer order and the ban on combining
them in section 2, the symbol matrix in section 3, the four-state vocabulary and the absence of
`NEUTRAL` in section 4, the R1 curation and the frozen thetas in section 5, the `lm-wall.v2` and
`lm-continuity.v5` rules in section 6, the ban on `CONSUMED` in section 7, the derivatives field
list in section 8, the two weight labels in section 9, the journal's append-only and
not-a-trade-journal properties in section 10, and the whole of section 11.
