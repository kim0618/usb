# Manual Market Context V1 - isolated preview report

    2026-10-05 · local only · 0 commits · 0 pushes · 0 deploys
    contract  mc-display.v1  sha256 25891d6267dcd9e4e0b0e719802e1f95c79a6aa6b1d99676fc637870e169a3b1
    panel     mc-v1.0

**Status: preview ready, production readiness NO.** The five validated layers are composed into one
read-only context panel beside the manual order ticket, verified against a live Binance book and a
live collector run, and the forward context tape is running. What is missing for production is
listed in section K and is not code.

**git**: every file in this step is **new and untracked**. The 38 modified tracked files in the
working tree are all dated 2026-10-04 and belong to the Liquidity Map V1.5 session; none of them was
touched here. One exception worth naming: `frontend/next-env.d.ts` was rewritten by Next's dev
server when the preview was rendered and has been restored to `HEAD`, because leaving it modified
would attribute an unrelated change to this work.

---

## A. Data sources

Every figure on the panel is read from a contract that already exists. Nothing is re-implemented and
no threshold is moved.

| Layer | Source | Frozen under |
|---|---|---|
| PRICE | `research.price_structure_r1.engine` + `.levels`, R0 swings/VWAP/ATR, projected by `research.market_context_r0.structure.ContextEngine` | R1 contract; thetas from R0's held-out calibration (`theta_1h = 3.0`, `theta_5m = 6.0`) |
| LIQUIDITY | `liquidity_map.api.snapshot` - the function the Liquidity Map preview itself serves | `lm-wall.v2`, `lm-continuity.v5`, `btc-ms.v0.1` |
| FLOW | `liquidity_map.view.flow_view` (the V0 5 s / 15 s / 60 s windows) | `btc-ms.v0.1` |
| DERIVATIVES | Binance USD-M public `GET /fapi/v1/premiumIndex`, `/fapi/v1/openInterest`, `/futures/data/openInterestHist` | n/a - raw venue figures |
| AUXILIARY | C1 store files read as bytes; `data/research/crypto/directional_probability_r0/results.json` | C1 contract (not evaluated here); Directional Probability R0 |

**Venue.** Binance USD-M BTCUSDT throughout. Price Structure R0/R1/R2 ran on Bybit; Market Context
R0 recomputed the same curation on Binance and got `repaint 0`, `vanished 0` over 48,001 minutes
with an identical active-level distribution. The reason for one venue is the measured basis tail -
median 1.40 USDT but **max 622.9 USDT (0.894%)** against a 0.25% level tolerance.

**Isolation, as a property rather than a promise.** Its own ASGI app on its own port (8012),
imported by nothing that deploys. The import graph cannot reach `paper`, `live`, `terminal`,
`strategy`, `backtest` or any C1 module; a subprocess import proves none of them loads. The
environment keys it can read are a closed list of six, none a credential. The only host it may reach
is `fapi.binance.com` and the only paths are the four public ones above, behind a guard that refuses
`/fapi/v1/order`, `/fapi/v1/account`, `/fapi/v1/leverage`, `/fapi/v1/listenKey` and
`/fapi/v3/positionRisk`. Exactly one module opens a file for writing, in mode `a`: the forward
journal.

---

## B. Price Structure

**Read-only parity is measured, not asserted.** The panel's published rows are compared field for
field against `market_context_r0.structure.run` over that study's own frozen 76,800-bar series, and
the `Bars` the panel builds are compared array for array against the research loader's, ATR
included. Both are identical.

**The series is anchored once and extended forward only.** A rolling window would delete history off
the back, changing level identities and lifecycles - repaint under another name. With a fixed anchor
every run is a prefix extension, and `prefix_check` measures the consequence rather than assuming
it. On the live series: cuts of 1 / 60 / 720 minutes, **1,440 / 1,381 / 721 minutes compared, all
identical**. On synthetic series in the test suite, three more cuts, identical.

Live cost at the default 30-day anchor: **43,454 bars, 749-807 ms to recompute, 77 ms to fetch the
tail**, once per closed minute. A poll that does not cross a minute boundary costs 2-14 ms.

**Fields on screen:** trend structure with its swing labels, nearest resistance and nearest support
(price, signed distance, lifecycle, origin, strength, touches, confirm timeframe, sources, id),
breakout state with its level and age, retest state, failed-break state, last sweep, range state
with bounds and position, active level count, ATR 5m/1h, VWAP position.

**Previous session high/low is carried outside the level book**, in its own block, with R2's
measurement beside it: merged into the book that source was worth one reversal in twenty-seven, and
that one was not a new line but a `PROVISIONAL` level promoted by the merge. One case in a 27-case
sample is the resolution limit, so R2's extra sources stay out of the production rule.

**Repaint audit on the live run: 0 violations, 0 vanished levels.**

---

## C. Liquidity

Parity is **structural**: the panel calls `liquidity_map.api.snapshot` and projects fields out of the
dict it returns, so there is no second implementation of the wall rule, the continuity rule, the
coverage accounting or the depth bands. The tests assert the projection is a projection - each wall
figure is the identical value the viewer published.

**The four ways there is no wall are kept apart, and all four were photographed.**

| Word | Meaning | Seen |
|---|---|---|
| `OK` | a wall qualifies under `lm-wall.v2` | live collector run, both sides |
| `NONE` | the set is proven complete and nothing qualifies | `preview/liq-none.png` - "벽 없음 (완전관측)" + `NO_CANDIDATE_QUALIFIES_UNDER_LM_WALL_V2` |
| `STALE` | the journal was readable but is older than its bound | `preview/liq-stale.png` - "저널 정지", reasons "세션 종료됨 · 저널이 갱신되지 않음 · 샘플이 현재가 아님" |
| `UNKNOWN` | no journal, no session, or an unprovable set | `preview/liq-unknown.png` - "미관측", every depth band a dash |
| `PARTIAL` | the wall set could not be proven complete | backend + frontend tests only; see K |

Live reading in `btc-expanded-1440.png`: on the ask side 106 candidates, 8 selected by
`lm-wall.v2`, 4 above the operator's 500,000 USDT display filter, nearest at 86,000.0 (5.11 bp,
703,394 USDT, 8.179 BTC, observed 3m 11s, carried); on the bid side 166 / 18 / 10, nearest at
85,900.0 (6.52 bp, 1,019,633 USDT, 11.870 BTC, observed 5m 3s, carried). A separate API read two
minutes earlier showed 279 candidates and 31 selected across both sides, with 37 carried - the
figures move every second, which is why each of them is published with its own `values_as_of`.

**Bounds are marked, nulls are dashes.** The ±0.1% band reads `COMPLETE`; ±0.25%, ±0.5% and ±1% each
render `≥` plus `PARTIAL 하한`, because a `limit=1000` snapshot reaches about ±0.15%. An unobserved
band is `-` with its reason, never `0`.

**A wall is not a signal, and the panel says so where the walls are.** Market Context R0 measured
near-wall presence at **99.0% (ask) / 98.5% (bid)** of samples, averaging 8.8 per side when present.
That figure is rendered beside the wall rows, and no wall carries a bullish or bearish mark.

---

## D. Flow

The three contract windows with their own coverage, the aggressor rule restated by the contract that
owns it, and the trade stream's connection and age.

**A defect found by reading a live stale journal, not by reasoning.** Every age inside a flow window
is measured from the sample the collector wrote - by design, so a collector that died an hour ago
does not look current *within* its own last sample. The consequence is that on a 19-hour-old record
`trade_stream.age_ms` reads 80 ms and is perfectly true, and read on its own it says the feed is
live. The first version of this layer printed `FLOW: LIVE` over a day-old journal. The journal's own
freshness is now the floor under every window state, the screen says what the age is measured from,
and six tests pin it.

**Wall disappearance is classified, never asserted.** `CONSUMED_CANDIDATE` / `CANCEL_LIKE` /
`UNKNOWN`, using Market Context R0's own `traded_through` predicate - for an ask bin the mid path has
to reach `bin_high`, for a bid bin `bin_low` - with a test that runs the research module's expression
beside the panel's on constructed samples. The interval is R0's `mids[anchor+1 : j+1]`: excluding the
sample the wall was last seen in, **including** the one it was found missing in. The first
implementation had both ends wrong and called a wall the price had just traded through `CANCEL_LIKE`,
which is wrong in the direction of the 96% base rate and would have looked right.

`UNKNOWN` is published for a session boundary, a resnapshot generation change, an unsynced book, a
path gap wider than the sample interval, and a repeated sample. The word `CONSUMED` alone is not in
the vocabulary and a test over every string literal in the package enforces it: `btc-ms.v0.1` does
not prove order identity, so a bin partly filled below the notional floor is indistinguishable from
one that was cancelled.

Observed over the live run, on this panel's own tape: **17 `CANCEL_LIKE`, 2 `CONSUMED_CANDIDATE`, 7
`UNKNOWN` out of 26 disappearances** in one window, and 5 / 0 / 1 out of 6 in another. The
consumption share sits beside R0's measured 3.8% (ask) / 6.9% (bid), which is on screen next to the
counts.

**Absorption is a candidate only.** It fires when, over the 60 s window with `COMPLETE` coverage,
aggressive flow on the pushing side reached **at least the notional of the nearest qualifying wall on
that side**, that wall was in both readings, and the mid path did not reach its bin. The size
comparison is the clause that makes it a candidate rather than wallpaper: without it the row is "a
wall is present and buyers are in front", which is true in about half of all samples. It renders as
`ABSORPTION_CANDIDATE` with its inputs and `order identity 증명: 아니오`, and it fired live during the
preview run (2,998,661 USDT of aggressive buying against a 703,394 USDT ask wall that held).

---

## E. Derivatives

Per symbol, from the venue's own endpoints, with each endpoint's failure kept separate so that
funding arriving while open interest fails shows the half that arrived.

* `open_interest` in base units with the venue's timestamp
* `open_interest_change` over 5m / 15m / 1h / 4h, **differenced on the venue's closed 5 m buckets**
  rather than on this process's own polls - a poll difference has nothing to show for its first hour
  after a restart and then shows a window that depends on when the panel happened to start
* `funding_rate` (last) and the next funding time
* `mark_price`, `index_price`, and `basis = mark - index` with `premium_pct = basis / index`

**No new derivatives signal.** The ban is satisfied structurally: a test walks the module's AST and
asserts the only subtractions are `mark - index` and the open-interest bucket differences, and that
no expression mixes funding with a price. There is no function that takes two of these fields and
returns a third reading, and nothing here is combined with any other layer.

Verified live on all three instruments. BTCUSDT at the time of writing: OI 98,714.136, 5m +13.499,
1h +179.243, 4h +997.118, funding 0.00006766, basis -28.20 USDT, premium -0.0328%.

---

## F. Auxiliary - BTCUSDT only

**C1 / C1x, labelled `auxiliary`.** Read from the store's append-only files as bytes. No C1 module is
imported, no runtime is constructed, no directory is created, nothing is written - the mtimes before
and after a read are asserted equal. Freshness is judged on the engine's own `last_decided_at_ms`
and not on the file's mtime, because an atomic rewrite touches the mtime whether or not a bar was
decided. The row carries identity, instant and state, and deliberately **not** the shadow trade's
plan: a context panel carrying an entry price reads as a proposal however it is labelled.

In this environment the local C1 store holds a `state.json` whose last decision is 2.8 days old and
no signals file, so the row renders `STALE` with `C1_ENGINE_LAST_DECIDED_OLDER_THAN_FRESHNESS_BOUND`
plus the two missing filenames. That is the correct reading and is what the screenshots show.

**Directional Probability, labelled `weak_auxiliary`.** Read verbatim from the frozen artifact:
`verdict = WEAK_DIRECTIONAL_MODEL`, `usable_horizons = []`, `weak_horizons = [30, 60, 120]`. **No
probability is computed for the current moment** - no model is loaded and no inference is run, and
the payload says `computed_for_now: false` and `inference_run: false`. The two later results that
bound how this may be read travel with it, because an AUC of 0.56 shown alone will be over-read:
BTC-P1's directional AUC was 0.502-0.533, and BTC-P2 returned `STRONG 0/64` with all four primary
cells below 0.5 and `P3 NOT_AUTHORIZED`.

The bucket names are read out of the artifact rather than spelled in the panel's source. That keeps
the panel independent of the study's labelling and keeps its own source free of the direction
vocabulary ban B1 forbids it to contain.

**ETHUSDT and SOLUSDT do not render this layer at all.** Absent, not empty - a blank auxiliary panel
would read as "no signal right now" when the truth is "this work was never done on this instrument".

---

## G. Forward journal

Append-only JSONL, one file per UTC day, flushed and fsynced per record, never rewritten in place,
and **never read back by the panel**. It is not linked to an order, does not require a manual trade
to exist, and holds no intent, decision or outcome - a test asserts the record contains none of
`order_id`, `trade_ref`, `position_qty`, `entry_price`, `realized_pnl`, `signal_id` and seven more.

Written on each new closed minute and on each change of an anchor field (breakout, retest,
failed-break, nearest level id and side, each wall state, the liquidity and flow states). A figure
moving is not a change, because every figure moves every second. The cursor is per symbol, and for an
instrument with no structure layer the wall-clock minute stands in for the bar close - otherwise the
tape would hold exactly one line for ETHUSDT and SOLUSDT for a whole run.

**A gap is written, not skipped.** This is the property the whole file exists for. Market Context R0
could answer one of four research questions, and the other three were refused by the archive rather
than by the model: 796 of 799 resistance approaches fell `OUTSIDE_JOURNAL` because Layer B was 3.61
hours long, and that gap cannot be closed with history - Binance's public archive sells aggTrades,
bookTicker and percentage depth summaries, not level-resolution quotes. If the dropouts were omitted
the tape would quietly become a survivorship sample of the market's calm minutes.

Measured on the preview run: **49 records** - 7 `FIRST_RECORD`, 13 `NEW_MINUTE_BAR_CLOSE`, 6
`NEW_MINUTE_WALL_CLOCK_MINUTE`, 23 `CONTEXT_CHANGE`, against 41 polls skipped as unchanged. Liquidity
states recorded: 31 `LIVE`, 8 `STALE`, 10 `UNAVAILABLE`. Wall states recorded: 28 `OK`, 8 `STALE`, 10
`UNKNOWN`, 3 `NONE`. Every required field of contract section 10 is present, each nullable and each
carrying its reason when null.

---

## H. UI

`docs/crypto/market_context_v1/preview/`, with `report.json`, `cases.json` and the three
`liq-*.json` carrying the measurements each screenshot was taken with.

**Layout.** On a wide screen the panel sits under the chart in the terminal's own left column
(`max-w-[1600px]`, `xl:grid-cols-[minmax(0,1fr)_360px]`), or beside it in a 360px column. On a phone
it sits **below** the order ticket: measured at 390px, with the panel between the chart and the
ticket the ticket started 1,736px down the page, and below it the ticket starts where it always did.

**Collapse is the phone default, not a prop a caller has to remember.** With everything open the
panel is 3,178px tall at 390px; collapsed to PRICE it is 931px, and every collapsed header still
carries its layer's state word and a one-line summary. The order ticket starts at y=685 in all four
390px captures, open or collapsed, because the panel is below it.

**Horizontal overflow: 0 px at 1600, 1440 and 390**, measured as `scrollWidth - clientWidth` plus a
sweep for the single worst-overflowing node in the document. Fourteen captures, all zero.

**Every state is a word.** Five chips in the header strip, a word in each layer header, a word on
each wall side, a word on each flow window. Colour is an addition and the tests assert the text.

**Section 10 states, each confirmed on screen:**

| Case | Minutes in the published window | Screenshot |
|---|---|---|
| near resistance | 5,839 | `case-NEAR_RESISTANCE.png` |
| near support | 6,418 | `case-NEAR_SUPPORT.png` |
| breakout (fresh) | 2,389 | `case-BREAKOUT_FRESH.png` |
| in range | 2,025 | `case-IN_RANGE.png` |
| retest open | 17,880 | `case-RETEST_OPEN.png` |
| failed break | 1,014 | `case-FAILED_BREAK.png` |
| no level above | 4,100 | `case-NO_LEVEL_ABOVE.png` |
| no level below | 2,327 | `case-NO_LEVEL_BELOW.png` |
| wall present | live | `btc-expanded-1440.png` |
| wall absent (`NONE`) | crafted journal | `liq-none.png` |
| stale | 2026-10-04 journal | `liq-stale.png` |
| partial coverage | every capture (±0.25 / 0.5 / 1% bands) | all |
| unknown | no journal root | `liq-unknown.png` |

The structure cases are reached through a replay strip, because near resistance, a range and a failed
break do not happen on request. Only the **structure** layer is replayed, the response's `mode` is
`READ_ONLY_STRUCTURE_REPLAY`, and both the payload and the screen name the four layers that are not
replayed. Four live layers beside one historical one under a single timestamp is the worst thing a
context panel can do.

---

## I. Tests

**Backend: 186 passed** (`backend/tests/crypto/test_market_context_v1_*.py`), in eight files.

| File | What it holds |
|---|---|
| `isolation` (34) | import graph, subprocess import, credential scan, closed environment-key list, one writer in mode `a`, no filesystem mutator, direction/score vocabulary, the `CONSUMED`/`ABSORPTION` shape rule over every string literal, note-constant coverage, thresholds read from the owning contracts, the URL guard, GET-only routes |
| `structure` (19) | byte-identical parity with the research engine and loader, grid-gap and alignment refusals, prefix invariance at three cuts, live/stale, the series basis, previous session outside the book, non-BTC absence with a serialised leak check |
| `liquidity` (16) | projection parity against the viewer's own dict, the four no-wall states each driven by a real journal, lower bounds, nulls, the observed interval, the presence base rate, non-BTC absence |
| `flow` (34) | journal-floored freshness, the touch predicate against the research module's expression, five undecidable cases, bounded memory, the interval endpoints, five absorption conditions, non-BTC absence |
| `derivatives` (16) | basis and premium arithmetic, bucket differencing and its sign, too-few buckets, non-ascending history, per-endpoint failure, staleness, per-symbol requests, the AST check that no two fields are combined |
| `auxiliary` (21) | no directory created, no mtime changed, decision-based freshness, torn lines, the fixed key list, the absent plan fields, verbatim verdict, no inference, artifact-driven bucket names, the bounding studies, BTC-only absence |
| `journal` (22) | the full record shape, nearest-level resolution, append-only, day rotation, torn tail, disabled root, write failure, cadence, per-symbol cursors, the wall-clock fallback, gaps written rather than skipped |
| `api` (24) | health, the symbol matrix, 400 on an unsupported symbol, 405 on POST, the five layers in order, the published `rollup: false`, stale not lending freshness, symbol switch with a leak check, journal on and off, the state index, the replay route's labelling and its 404/400, prefix check, a failed fetch keeping the last run, the fixed anchor |

**Frontend: 52 passed** in `market-context-panel.test.tsx`; **1,014 passed across 57 files** for the
whole frontend suite. `tsc --noEmit` clean.

**Adjacent suites unchanged:** `backend/tests/crypto` as a whole, **2,548 passed, 0 failed**.

One defect was found by a test rather than by review and is worth naming: the vanished-wall interval
endpoints (section D). Two were found by running against live data: the flow freshness floor
(section D) and the phone collapse default (section H).

---

## J. Known limitations

1. **Wall-state `PARTIAL` was not photographed.** It needs a wall set the follower cannot prove
   complete, which is a transient condition that did not occur during the preview run and cannot be
   forced through the API. It is covered by a backend test driving a real truncated walk and by a
   frontend test asserting the rendered label. Depth-band `PARTIAL` is on screen in every capture.
2. **AUXILIARY read `STALE` throughout**, because the local C1 store has not been written to since
   2026-10-02. The `LIVE` path is covered by tests, not by a screenshot.
3. **The preview frame is not the deployed terminal.** The chart, order ticket, market header and
   position are labelled placeholders carrying the terminal's real dimensions and classes. This step
   may not modify the deployed screens, and importing their trees would pull the order path into
   this page's bundle. So the placement measurements are true of those dimensions, and the panel has
   never been rendered inside the real terminal.
4. **The structure recompute is a full pass.** 749-807 ms per closed minute on a 30-day series, and
   the series grows for as long as the process runs. A process left up for weeks will cost
   proportionally more; nothing trims it, because trimming is the repaint this design refuses.
5. **The collector ran for 25 minutes, not 24 hours.** Everything in sections C, D and G is measured
   on that window plus the 2026-10-04 journal. The Liquidity Map's own 24 h trial is still unrun and
   is the same blocker it was.
6. **The absorption candidate's size rule has never been evaluated against outcomes.** It is a
   display condition, not a measured edge, and the contract says so. The same is true of the
   `CONSUMED_CANDIDATE` / `CANCEL_LIKE` split, whose base rates come from 3.61 hours.
7. **`MC_V1_PUBLISH_WINDOW_H` exists for the verification.** It changes only how many published
   minutes the replay route retains - not the series, the anchor, the book or the live reading - and
   the default is 24 h. It was added because the last day of BTCUSDT held no range and no failed
   break.
8. **The panel has no chart overlay.** It names level and wall prices in text; drawing them on the
   terminal's chart would mean modifying the terminal.
9. **The journal timer and the browser poll both write.** Running two panel processes against one
   journal root would interleave two tapes in one file. There is no writer lock.

---

## K. Production readiness

**Not production ready, and the gap is not code.**

| # | What is missing | Why it blocks |
|---|---|---|
| 1 | The Liquidity Map 24 h trial | The LIQUIDITY and FLOW layers are this panel's, and that release's own readiness note says the same thing: it needs a day's worth of clock, not a change |
| 2 | A real-terminal render | Limitation 3. The placement numbers are measured on a faithful frame, not on the screen the operator uses |
| 3 | A long-running cost measurement | Limitation 4. Nobody has run this for a week |
| 4 | A writer policy for the tape | Limitation 9 |
| 5 | The operator's own reading | Nobody has traded beside this panel yet. Whether a context strip beside the ticket helps or distracts is not a thing this report can answer |

**What is ready:** the composition, the isolation, the vocabulary, the parity with every layer it
reads, the tape, and the verification that each state the task asks about renders correctly.

**Deliberately not done**, as instructed: no commit, no push, no production deploy, no AUTO change,
no order-path change, no C1 contract change, no Price Structure research and no threshold move.

---

## How to run it

```bash
# 1. the collector (read-only public market data) - needed for LIQUIDITY and FLOW
MS_V0_ROOT=/path/to/journal .venv/bin/python -m app.crypto.market_structure_v0.collector \
  --duration 0

# 2. the panel's own backend, on its own port
MS_V0_ROOT=/path/to/journal \
MC_V1_JOURNAL_ROOT=/path/to/tape \
PYTHONPATH=backend .venv/bin/python -m app.crypto.market_context_v1 8012

# 3. the isolated preview page
cd frontend && NEXT_PUBLIC_MARKET_CONTEXT_BASE_URL=http://127.0.0.1:8012 npx next dev -p 3031
# http://127.0.0.1:3031/market-context-preview
```

With the backend stopped the page renders its own "응답 없음" state and the rest of the dashboard is
unaffected. With `MC_V1_JOURNAL_ROOT` unset the process writes nothing at all.
