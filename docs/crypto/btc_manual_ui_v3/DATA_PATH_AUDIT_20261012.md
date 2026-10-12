# BTC Manual Trading UI V3: production data path audit and repair (2026-10-12)

Verdict: **C. READY_FOR_V3_USER_REVIEW** (data path intact end to end; preview on real data; no
production change). Nothing was deployed, restarted or written on the production host. Evidence
is in `datapath_20261012/` (shots, trace, harness). Local preview only.

## A. Raw Collector walls (production, read directly, write 0)

Source: `/run/usb-crypto-context/ctx-state-d41ae8dd…/context_latest.json` (the collector's
tmpfs cache, `ctx-collector.v1.1`, session `31cc8177`, Binance USD-M) and, for the fixed
snapshot, `collector_state.json` written in the same sample. Read by `ssh … cat` once per
snapshot: 1 read per minute for 20.7 minutes (`trace/series_report_20min.txt`, 20 parsed of 22
reads; two reads lost their header line and were discarded), plus one fixed pair at
`written_ms 1791771034098` (11:10:34 KST, sample 134593).

Every read: collector LIVE, liquidity LIVE, flow LIVE, 60 s window LIVE, file age at read 212 to
1022 ms. Published walls per side over the series:

| | ASK | BID |
|---|---|---|
| `walls_selected` (lm-wall.v2, ≥ 250 k) | 9 to 16 | 3 to 14 |
| `walls_shown` (display filter, ≥ 500 k) | 3 to 10 | 0 to 7 |
| published rows (≤ `DISPLAY_WALL_LIMIT` 6) | 3 to 6 | 0 to 6 |
| `wall_state` | OK in 20/20 | OK in 19/20, NONE once (11:21:39, 3 selected, 0 shown) |

The fixed snapshot: ASK 4 published (83,730.2 / 83,735.4 / 83,747.2 / 83,777.6; 700 k, 737 k,
528 k, 930 k), BID 5 (83,587.4 / 83,564.6 / 83,548.7 / 83,540.6 / 83,535.7; 702 k, 518 k, 527 k,
590 k, 813 k), mid 83,656.45, flow 60 s buy 1,456,563 / sell 485,438 USDT (75 / 25).

Fields the collector publishes per wall: `price`, `qty_btc`, `notional_usdt`, `multiple`,
`distance_bps/pct`, `persistence_ms`, `own_persistence_ms`, `persistence_source`,
`carried_persistence_ms`, `continuity_status` (NEW / CARRIED), `first_seen_ms`, `generation`,
`bin_low/high`, `bin_members`, `bin_candidate_notional_usdt`, `coverage`, `values_as_of`
(CHECKPOINT_CURRENT). Not published per wall: an opened timestamp other than `first_seen_ms`,
the size at open, a per-wall status, and any consumed / cancel-like / rank-evicted value. Those
exist only for walls that have ended, in `flow.vanished.recent[]` (`state` CANCEL_LIKE /
CONSUMED_CANDIDATE / UNKNOWN, `end_class` TRUE_ENDED / RANK_EVICTED / OUT_OF_COVERAGE / UNKNOWN,
`mid_path_touched_bin`) and in `flow.display_exits[]`. `session_id` is top level only. Walls
turn over fast: of 55 distinct ASK prices seen in 20 reads, the most frequent appeared in 4
reads; the longest `persistence_ms` seen was 263 s.

## B. 500 k threshold origin

**A, the Collector's own contract.** `context_collector_v1/contract.py:56`
`DISPLAY_MIN_NOTIONAL_USDT = MCL.DEFAULT_MIN_NOTIONAL_USDT` (Market Context V1
`liquidity.py:32`, `"500000"`), applied by `ContextEngine.wall_filter` (`context.py:297`) to
the viewer's `side_walls` and recorded in the payload as
`liquidity.walls.filter_min_notional_usdt = "500000"`. It is the display filter the MC V1 panel
passed to the Liquidity Map viewer (`liquidity_map/wallstate.py:417`,
`DEFAULT_MIN_NOTIONAL_USDT = 500000`), carried so the published set equals the panel's.

The wall rule itself (**B**, `lm-wall.v2`, `wallrule.py`) is a different figure, 250 k
(`MIN_NOTIONAL_USDT`), with a multiple, a distance band, a persistence floor and binning; its
result is `walls_selected`. The btc-context API (**C**) and the V3 frontend (**D**) add no
threshold: `view.walls_section` republishes `sides.*.walls` as is (there is no number in that
module), and `btc-context.ts` has none. The only V3 artefact was the footnote text "50만 USDT
이상" on the panel, which is removed in this change (section L). No threshold was changed.

Diagnostic on the fixed snapshot's raw V0 candidate levels (`collector_state.json
walls.items`, 282 ACTIVE levels, before the v2 rule and before the display filter;
`trace/raw_wall_threshold_diagnostic.txt`):

| side | total | ≥ 1 M | ≥ 500 k | ≥ 250 k | ≥ 100 k | v2 selected | ≥ 500 k shown | published |
|---|---|---|---|---|---|---|---|---|
| ASK | 136 | 1 | 10 | 30 | 85 | 15 | 4 | 4 |
| BID | 146 | 1 | 7 | 31 | 91 | 11 | 5 | 5 |

Why 10 raw ASK levels ≥ 500 k become 4 published: the v2 persistence floor and binning. The
largest raw ASK level (83,759.1, 2.29 M) had `persistence_ms 0`; 83,761.3 (813 k) 8 s;
83,656.5 (749 k, the touch) 2 s; 83,696.7 (724 k) 0 s; 83,776.1 (555 k) shares the
[83,775, 83,780] bin with the published 83,777.6. The raw top list is in the diagnostic file.

## C. Raw / API / frontend wall counts (fixed snapshot, one file)

| stage | ask | bid | top ask (price, notional) | top bid | selected top 3 |
|---|---|---|---|---|---|
| A raw `context_latest.json` | 4 | 5 | 83730.2 700,738 · 83735.4 737,290 · 83747.2 527,691 · 83777.6 930,434 | 83587.4 701,800 · 83564.6 518,184 · 83548.7 526,524 · 83540.6 590,465 · 83535.7 812,552 | first 3 of each |
| B backend `view.display` (`trace/stageB_display.json`) | 4 | 5 | same prices, `notional_usdt` rounded to 1 USDT, `size_rank` 3/2/4/1 | same, `size_rank` 2/5/4/3/1 | same |
| C `GET /snapshot` (`trace/stageC_snapshot.json`) | 4 | 5 | `walls == B.walls` (dict equality) | same | same |
| D parser `wallPaths` (`trace/stageD_parser.json`) | 4 | 5 | 83730.2 +0.088 % · 83735.4 +0.094 % · 83747.2 +0.108 % | 83587.4 -0.083 % · 83564.6 -0.110 % · 83548.7 -0.129 % | NEAR: those; SIZE: 83730.2 / 83735.4 / 83777.6 and 83587.4 / 83540.6 / 83535.7 |
| E render input (`BtcContextStripView` props = C payload + position) | 4 | 5 | as D | as D | as D |
| F screen (`shots/qa_shots.json`, DOM) | `매도벽 4개` | `매수벽 5개` | 83,730.2 +0.09 % · 83,735.4 +0.09 % · 83,747.2 +0.11 % | 83,587.4 -0.08 % · 83,564.6 -0.11 % · 83,548.7 -0.13 % | NEAR and SIZE rows equal D |

Chart overlay at 1 m: 6 wall lines (`ctx-ask-wall-1..3`, `ctx-bid-wall-1..3`) at the stage D
prices, plus the support line. No stage drops a wall.

## D. FLOW trace

| stage | value |
|---|---|
| A raw `flow.windows.60s` | state LIVE, `buy_usdt` 1,456,563.4281, `sell_usdt` 485,437.6431, trades 623 |
| B `view.flow_section` | `{live: true, window: "60s", buy_pct: 75, sell_pct: 25}` |
| C `/snapshot.flow` | equal to B |
| D `flowReading` | `매수우세 75%`, buyPct 75, side BUY |
| F screen | `체결흐름 매수우세 75%` + bar at 75 % |

1,456,563 / (1,456,563 + 485,438) = 0.7500 → 75 / 25. Source and screen agree.

## E. Missing-data reason (why the screen read `--`)

**stale** (an existing reason: `context_collector_v1/api.py:154`, floor
`LATEST_OLDER_THAN_READ_STALE_MS`, `READ_STALE_MS = 3 × 1 s`). Reproduced on the fixed file
(`stageB` run): read at `written_ms + 1 s` → 4 / 5 walls, flow 75 / 25; the same file read 68 s
later → `read.floored = true`, every layer STALE, `walls.ask = []`, `walls.bid = []`,
`walls.mid = null`, `flow.buy_pct = null`, `liquidity.ask = null`. That is exactly the reported
screen: both paths `--`, FLOW `--`, 0 wall lines, while the ENTRY block (read from the recorder
store, which has no age floor) still showed figures. Any preview that serves a cached one-shot
file older than 3 s to the V3 API shows this. Production does not: the API reads the collector's
tmpfs file, which the series shows is 0.2 to 1.0 s old at every read, and the production 8014
(still V1, `757f768`) answered `flow 52 / 48, liquidity STRONG / STRONG` at the same time.

Not the cause: no PARTIAL, missing, mapping, session, source-path or field-name mismatch was
found at any stage (sections C and D, and the parity tests in section O).

For a cached preview the data path is therefore fed by a replay feeder
(`harness/replay_feeder.py`, debug only, outside production code) that rewrites only
`written_ms` / `sample_ms` of one cached file to now once a second. Every other value is the
cached file's.

## F. Entry context provenance

Where an entry figure can come from in V3, by code path:

| path | class | status |
|---|---|---|
| `entry_context.open_entry` on a store the recorder wrote (`recorded_at_ms` + `contract` stamped by `recorder._snapshot`) | **A** actual entry-time Recorder EVENT snapshot (`ev:<event_id>`, as of the fill; its `market` block is the recorder's own as-of read, and for a fill before the collector started its collector block is NOT_AVAILABLE) | the only production source; `BTC_CONTEXT_MANUAL_STORE` unset in production today → `{"available": false}` |
| the same on a hand-built store (no stamp, or a `preview*` key) | **C** preview fixture | **refused by default** (new: `FIXTURE_STORE_REFUSED`); served only by a process started with `--allow-fixture-entry`, and then labelled `source: "FIXTURE"` → screen `검증용 기록` |
| first current reading frozen as the entry | **D** | does not exist: `entryFor` freezes only an `entry.available` answer; with none it returns null and every entry figure is `--` (test "never turns the first current reading into an entry") |
| historical backfill | **B** | only inside the recorder (its `backfilled` flag); V3 reads whatever row the recorder wrote and does not backfill |

The earlier preview's `sim_short` / `sim_long` stores (`preview_20261012/make_stores.py`) were
**C** (built by hand, with a `preview_note`) and the earlier screens did not say so. Under this
change they would be refused by a production-configured API. The PAPER SHORT on screen today is
outside the recorder (LIVE only) and shows `진입 · 기록 없음` with every entry figure `--`.

## G. LIVE position example

Real LIVE position (mirror, 11:11 KST): LONG 0.108 @ 83,913.1, unchanged before and after.
Its fill (2026-10-07 11:22) predates the collector (2026-10-10 21:47), and no recorder runs in
production, so under V3: ENTRY FLOW `--`, walls `--`, OI `--`, funding `--`; CURRENT from the
collector and the venue. `shots/1440-live-long-entry-unavailable(-expanded).png` shows this with
the position simulated on the PAPER state answer (the LIVE source is never opened through a
proxy, see P). Header: `진입 83,913.1 · 현재 83,656.5 · 진입 대비 -0.31%`; walls measured from
the entry: 매도벽1 83,730.2 -0.22 %, 매수벽1 83,587.4 -0.39 %.

## H. Nearest top 3 (fixed snapshot, from current 83,656.5)

| up | | | down | | |
|---|---|---|---|---|---|
| 매도벽1 83,730.2 | +0.09 % | gap 0.09 % | 매수벽1 83,587.4 | -0.08 % | gap 0.08 % |
| 매도벽2 83,735.4 | +0.09 % | gap 0.01 % | 매수벽2 83,564.6 | -0.11 % | gap 0.03 % |
| 매도벽3 83,747.2 | +0.11 % | gap 0.01 % | 매수벽3 83,548.7 | -0.13 % | gap 0.02 % |

## I. Largest top 3 (same snapshot)

| up | | | down | | |
|---|---|---|---|---|---|
| 매도벽1 83,730.2 (701 k) | +0.09 % | gap 0.09 % | 매수벽1 83,587.4 (702 k) | -0.08 % | gap 0.08 % |
| 매도벽2 83,735.4 (737 k) | +0.09 % | gap 0.01 % | 매수벽2 83,540.6 (590 k) | -0.14 % | gap 0.06 % |
| 매도벽3 83,777.6 (930 k) | +0.14 % | gap 0.05 % | 매수벽3 83,535.7 (813 k) | -0.14 % | gap 0.01 % |

Differences from H: one of three ask rows, two of three bid rows. "Largest" chooses among the
collector's published set only (≤ 6 per side, all ≥ 500 k). The default stays `가까운 순` with
the switch on the panel; **not decided**.

## J. Distance rules (changed)

- No position: every row's leading figure is from the collector mid (`현재 대비`).
- Position: leading figure is from the entry (`진입 대비`); `현재 대비` is the row tooltip and in
  the detail; the header carries `진입 대비` for the current price.
- **Sign = price direction, for LONG and SHORT alike** (`entryDistancePct` now `pctFrom(entry,
  price)`): the PAPER SHORT @ 83,612.9 reads 매도벽1 83,730.2 `+0.14%`, 매수벽1 83,587.4 `-0.03%`,
  and the requested example (83,894.1 vs 83,612.9) is `+0.34%` (unit test). The earlier build
  turned the sign towards the position's gain; that is gone. No profit word anywhere.

## K. Wall gaps

`gapPct` = |pct from the previous step on the same path|, the price for the first row, then
wall to wall. Fixed snapshot: up 0.09 / 0.01 / 0.01 %, down 0.08 / 0.03 / 0.02 %.

## L. No-wall behaviour

The API now publishes `walls.empty = {ask, bid}`: true only when the collector read the side and
its `wall_state` is NONE (its own "no qualifying wall"). The screen then prints `현재 적격 매도벽
없음` / `현재 적격 매수벽 없음` with the count `0개`; a side the collector could not read, a stale
or missing reading, stays `--`. The threshold figure is no longer on the panel (footnote
reworded; test asserts no "50만", "500,000", "USDT 이상"). The reason stays in this report and
the API (`walls.empty`). Screen: `shots/1440-no-wall-fallback(-expanded).png` (fed the fixed
snapshot with both sides set to NONE; FLOW and the rest unchanged).

## M. R/S preview A/B

Resistance and support are drawn as a **reference annotation at most**: dotted, 45 % alpha
(`OVERLAY_STYLE`), tag is the name only (`저항` / `지지`), no price or distance in the tag (the
axis label has the price). A `저항·지지 참고선 | 숨김` switch in the panel detail drives the
chart (`setStructureDisplay`). Default in this preview: annotation (B). Screens:
`shots/1440-rs-annotation-B.png`, `shots/1440-rs-hidden-A.png` (only the support line exists in
this snapshot; resistance was null in the structure snapshot).

## N. Screenshots (`shots/`, all on the fixed production snapshot; headless Chromium)

| # | file | what |
|---|---|---|
| 1 | `trace/raw_wall_threshold_diagnostic.txt`, `trace/series_report_20min.txt` | raw wall table |
| 2 | `trace/stageC_snapshot.json` | API JSON |
| 3 | `1440-no-position-nearest.png` | 1440, no position, 가까운 순 |
| 4 | `1440-no-position-largest.png` | 1440, no position, 큰 순 |
| 5 | `1440-short-paper-real(-expanded).png` | the real PAPER SHORT 0.064 @ 83,612.9, entry `--` |
| 6 | `1440-long-sim-fixture(-expanded).png` | LONG 0.010 @ 83,656.4 with a class C store under `--allow-fixture-entry`, labelled `검증용 기록` |
| 7 | `1440-wall-hierarchy-expanded.png` | panel open |
| 8 | `1440-live-long-entry-unavailable(-expanded).png` | LONG 0.108 @ 83,913.1, no record |
| 9 | `1440-no-wall-fallback(-expanded).png` | both sides NONE |
| 10 | `390-position(-expanded).png`, `390-full.png` | phone, PAPER SHORT |
| 11 | `1600-full.png` | full page |
| | `tf-{15s,10m,1h,4h}-1440.png`, `1440-rs-annotation-B.png`, `1440-rs-hidden-A.png` | timeframes, R/S A/B |

Measured on every shot: horizontal overflow 0, text overlap 0 (Range-rect check per row).
Wall lines per side by timeframe 3 / 3 / 2 / 1 / 0 / 0 (15s, 1m, 10m, 1h, 4h, 1d), a preview
candidate only. Price scale, autoscale and visible range untouched by this change (no chart
option changed except the two level colours/styles).

Note on the preview: the PAPER screen's `Mark 83,752.5` is Bybit's live price through the cached
terminal answer, while `현재 83,656.5` is the fixed Binance collector mid (11:10:34); the two are
not the same instant. In production both are live.

## O. Tests

Backend (`PYTHONPATH=. ../.venv/bin/python -m pytest tests/crypto/test_btc_context_ui_v3.py
tests/crypto/test_btc_context_ui_v1.py tests/crypto/test_btc_context_ui_v1_structure.py`):
**72 passed**. New: collector raw → `GET /snapshot` wall and flow parity on both production
fixtures (`collector_latest_20261012.json` 6 / 2 and `collector_latest_20261012_1110.json`
4 / 5); published count = min(`walls_shown`, 6); `empty` is the collector's NONE only; 0 / 1 / 2 /
3+ walls, ask only, bid only pass through; fixture store refused unless allowed, labelled when
allowed; reader honours the flag per read.

Frontend (`npx vitest run`): **61 files, 1,154 + new tests pass**; `tsc --noEmit` clean.
`btc-position-context.test.tsx` covers: 3 ask / 3 bid nearest, published walls only, 0 / 1 / 2 /
3+ walls, ask only, bid only, nearest ordering, largest ordering (differs from nearest), wall
replacement, disappearance, arrival, no-position distance, LONG and SHORT entry distance in
the price's direction (+0.34 % example), header `진입 대비`, entry unavailable (`진입 · 기록 없음`,
every entry figure `--`), first current reading never becomes the entry, entry freeze, current
update, FIXTURE label vs record, no threshold figure, R/S annotation / hidden and the switch,
lines per timeframe, HOT hidden, API down and back, no non-GET, the same order request with
and without V3 (unchanged from 9f69f55). Stale reading and `--` cases kept.

## P. Signed request impact

Method: the production LIVE source was never opened. The terminal was reached only through the
caching GET-only proxy (`harness/cproxy.js`): `/api/crypto/binance/*` refused (16 refusals, all
answered 503 locally), every other URL fetched once (22 upstream GETs in total across the five
proxies, all PAPER state / klines / health), non-GET 0, WebSocket upgrades dropped.

| | before (11:11:04 KST) | after (11:29:30 KST) |
|---|---|---|
| mirror `binance_live_events.jsonl` lines | 16,196 | 16,196 |
| LIVE_RECONCILE | 15,104 | 15,104 (0 in the last 30 min) |
| ORDER_INTENT / SENT | 110 / 54 | 110 / 54 |
| S1 `manual_performance.json` mtime | 09:21:24 | 09:21:24 |
| LIVE position | LONG 0.108 @ 83,913.1 | same |
| PAPER BTC | SHORT 0.064 @ 83,612.9 | same |
| service PIDs / NRestarts | paper 1669431, context 1582777, btc-ctx 1701557, frontend 1700688, q123 1684303, all 0 | same |

Signed request increase attributable to this QA: **0**. Mutation: **0**. The nginx access log
grew by 110 lines in that window from other clients (the QA path is an ssh tunnel to 8100, not
nginx); its non-GET lines are not this session's.

Processes started here, by PID (`harness` scripts record them in `run/pids.txt`): one snapshot
loop (24 one-shot `ssh cat`, finished), 3 replay feeders, 3 local APIs (18014, 28015, 28016),
one ssh tunnel (18101), one `next start` (4101), 5 proxies (4100 to 4500). All ended by PID
after the run. Leftover listeners from an earlier session (18015 to 18017, 3100 to 3400, 3101,
18100) were left alone.

## Q. Changed files

- `backend/app/crypto/btc_context_ui_v1/view.py`: `walls.empty`, `entry.source`, `WALL_NONE`.
- `backend/app/crypto/btc_context_ui_v1/entry_context.py`: `snapshot_source`, fixture refusal,
  `allow_fixture` on `open_entry` / `EntryReader.read`, `BTC_CONTEXT_ALLOW_FIXTURE_ENTRY`.
- `backend/app/crypto/btc_context_ui_v1/api.py`: `--allow-fixture-entry` (preview only).
- `backend/tests/crypto/test_btc_context_ui_v3.py`, `test_btc_context_ui_v1.py`,
  `fixtures/btc_context_ui_v1/collector_latest_20261012_1110.json` (new).
- `frontend/lib/btc-context.ts`: price-direction sign, `empty` / counts in `Paths`,
  `StructureDisplay`, name-only level tags, `Comparison.source`.
- `frontend/components/btc-context-strip.tsx`: counts in titles, `현재 적격 … 없음`, ENTRY /
  CURRENT rows, record / 검증용 label, R/S switch, footnote without the threshold.
- `frontend/components/crypto-terminal.tsx`: structure display wired to the chart lines.
- `frontend/components/crypto-candle-chart.tsx`: level lines dotted and fainter.
- `frontend/components/btc-position-context.test.tsx`, `btc-context-strip.test.tsx`,
  `__fixtures__/btc-context-snapshot-v3.json` (regenerated from the backend).
- `docs/crypto/btc_manual_ui_v3/DATA_PATH_AUDIT_20261012.md` (this), `datapath_20261012/`.

Not touched: Context Collector, its API, the recorder, order paths, nginx, env files, any
threshold, any production unit.

## R. Commit / push

See the session report (commit `fix(crypto): repair manual context v3 data path`, explicit
paths only; push from this environment fails without credentials, the user pushes
`cd ~/usb && git push origin main`).

## S. Final verdict

**C. READY_FOR_V3_USER_REVIEW.**

- real raw walls → UI top 3: yes, at every stage (C, F).
- no raw wall → reason clear: `walls.empty` → `현재 적격 … 없음`; stale / missing → `--` (L, E).
- current FLOW source = UI: 75 / 25 = `매수우세 75%` (D).
- entry provenance: RECORDER only in production; FIXTURE refused unless a preview run asks,
  and then labelled; no first-seen freeze (F).
- fake entry context: 0.
- signed Binance QA requests: +0. Mutation: 0 (P).
- production deploy: none. Open decisions for the user remain: nearest vs largest default, lines
  per timeframe, R/S annotation vs hidden.
