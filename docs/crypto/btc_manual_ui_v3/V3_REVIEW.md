# BTC Manual Trading UI V3: position-aware context (review, not deployed)

2026-10-12. Verdict: **C. READY_FOR_V3_USER_REVIEW.** Committed on `main`, not deployed. Production
still runs V1 (757f768). This commit also carries the V2 chart-native work (`../btc_context_ui_v2/`),
which V3 is built on and which had never been committed.

Nothing on this screen is advice. It has no HOLD, EXIT, TAKE PROFIT, STOP, TP or LONG/SHORT
recommendation, no colour that grades a wall, and no word for a change ("강화", "약화"). Manual
Context R0 found no validated entry, hold or exit rule, so the screen gives figures only.

## 1. Wall source audit (production Context Collector, read on 2026-10-12)

| field | where | in V3 |
|---|---|---|
| walls per side | `liquidity.sides.{ASK,BID}.walls[]`: the display set, ≥ 500,000 USDT, at most `DISPLAY_WALL_LIMIT` = 6, nearest first. The 08:51 reading had ASK 6 of 11 selected, BID 4 of 16 | all published, up to 6 |
| ranking | nearest first only (`liquidity_map.view.side_walls`); no strength order is published | `rank` = that order; `size_rank` = sort of the published notionals (a sort, not a grade) |
| current size | `notional_usdt`, `qty_btc`, `multiple` | notional, multiple |
| persistence | `persistence_ms` (carried across collector refreshes), `own_persistence_ms`, `continuity_status` (NEW / CARRIED), `first_seen_ms` | age, continuity |
| size when first seen ("opened") | **not published** | not shown. A size-change word would need it, or a new collector field |
| consumed / cancel-like / rank-evicted | `flow.vanished.recent[]` with `state` (CANCEL_LIKE / CONSUMED_CANDIDATE / UNKNOWN) and `end_class`; totals in `classified_totals` (e.g. RANK_EVICTED 33,867, TRUE_ENDED 14,902) | the fact the collector classified on: `price_reached` = `mid_path_touched_bin` ("가격 도달 / 미도달"). Class names are not shown: the payload guard bans CANCEL/CONSUMED, and the collector itself says a vanished wall is not to be called consumed |

**Nearest vs largest.** They differ often: in the preview the largest ask wall was `매도벽2` (14 m,
1.4 M) while `매도벽1` was a 16 s, 825 K wall. Both are offered (`가까운 순` / `큰 순`, a switch in the
panel that the chart lines follow). **Which one the screen uses is the user's decision.** "Largest"
can only choose among the six the collector publishes, not the whole book.

## 2. What is on screen

**Chart.** Up to three wall lines per side, dotted, tag `매도1..3` / `매수1..3` on the left edge
(name only, so six tags cover little of the chart; the price is on the axis label and in the
panel). Resistance and support stay as in V2. Lines per side by timeframe, a **preview candidate**:

| 15s | 1m | 10m | 1h | 4h | 1d |
|---|---|---|---|---|---|
| 3 | 3 | 2 | 1 | 0 | 0 |

The panel always lists three per side, whatever the timeframe.

**Panel under the chart.** One block, same slot as V1/V2.

| | CURRENT-MARKET (no position) | POSITION |
|---|---|---|
| header | `현재가 기준 · 현재 83,816.9` | `진입가 기준 · 진입 83,645.4 · 현재 83,816.9 · 진입 대비 -0.21%` |
| up path | `위 · 다음 매도벽 (현재 대비)`: three rows, price, % from current, gap from the previous step | same rows, % = distance from the entry in the position's favour (+ = the side it gains on); % from current is the row tooltip and in the detail |
| down path | `아래 · 다음 매수벽` likewise | likewise |
| flow, OI, funding | `체결흐름 매수우세 75% ▬ · OI ↑ · 펀딩 +0.0004%` | `체결흐름 진입 매도우세 77% → 현재 매도우세 52% · OI 7.73B → 7.74B · 펀딩 +0.0013% → +0.0004%` |
| ▾ detail | OI size, 1 h change, basis; per wall: age, size, size rank; the collector's last three wall ends | the same, plus `진입 당시(10/12 08:20) 매도벽 83,723.5 · 매수벽 83,593.0`, basis entry → current, and % from current per wall |

"Gap" is the room to the previous step on the same path: for wall 1 from the current price, then
wall to wall.

**"Current" is the collector's mid**, the middle of the same Binance book the walls come from, so an
ask wall is always above it. The terminal's mark is used only when there is no mid. The first
preview build used the mark. On PAPER that is Bybit's price, and an ask wall at 83,800 then read as
below the price (83,825.1). The fix has a regression test, and that test fails on the old rule.

## 3. Entry context: Manual Trade Snapshot Recorder integration

- The API reads the recorder's store read only. `BTC_CONTEXT_MANUAL_STORE` points at the directory
  holding `events.jsonl`. Unset means no entry context, which is the production default, and the
  screen shows only the current context.
- The open episode is the episode of the newest event, if it has no closed summary and leaves a
  position.
- The entry is that episode's first `OPEN_*` event. The context is the recorder's EVENT snapshot
  of it (`ev:<event_id>`), as of the fill.
- The store files are read only when their size or mtime changed. There is no lock and no import
  from the recorder or any order path. A reader fault is "not available", never an error.
- The payload restates the snapshot in the current sections' units: funding `rate × 100`, basis
  `bp / 100`, and the 60 s flow as shares. OI is `sumOpenInterestValue` USDT on both sides.
- Direction is a sign (+1 / -1). The screen compares only when it matches the account's position;
  otherwise it shows `진입 --` and `진입 기록 없음`.
- The screen freezes the first entry context it sees per episode, and later answers cannot change
  it (tested).

Limits that follow from the sources:
- **The recorder records LIVE only.** A PAPER position never has an entry context. The real PAPER
  SHORT 0.064 @ 83,612.9 shows `진입 --`.
- **The open LIVE LONG (0.108 @ 83,913.1, filled 2026-10-07 11:22) predates the collector**
  (production since 2026-10-10 21:47). Its entry flow and walls will be `--` for good. Its entry
  OI and funding exist (8.04B, -0.0007%).
- Entry walls are the nearest wall per side only. The recorder stores no hierarchy.

## 4. Preview (production data, `preview_20261012/`)

- **Context:** the production collector's `context_latest.json` and the structure snapshot are
  streamed read only over one ssh connection to local V3 APIs. The remote loop is bounded and was
  confirmed gone afterwards.
- **Entry stores:** built in the recorder's row shapes (`make_stores.py`).
  - Simulated entries at 2026-10-12 08:20 KST (mid 83,645.4) use the collector row the recorder's
    own `Journal.rows_at` / `collector_fields` read from a copy of the production journal segment.
  - Market fields come from 8 public Binance GETs.
  - The real LIVE LONG entry has collector `NOT_AVAILABLE`.
- **Terminal:** production 8100 through a caching GET-only proxy (`cproxy.js`).
  - Every `/api/crypto/binance/*` path is refused.
  - Each other URL is fetched once, with a cap.
  - Production received **35 GETs** for the final runs. Non-GET 0.
- **Simulated positions** (SHORT / LONG 0.010 @ 83,645.4) rewrite the PAPER state answer in the
  proxy, for display only.

Screens:

| file | what |
|---|---|
| `1440-no-position.png` | CURRENT-MARKET |
| `1440-short-paper-real.png` | the real PAPER SHORT 0.064 @ 83,612.9 (no recorder entry: `진입 --`) |
| `1440-short-sim.png`, `1440-long-sim.png` | POSITION with entry context |
| `live-long-real-earlier-build-1440.png` | the real LIVE LONG @ 83,913.1, from the first (pre-fix) build: long tags, mark-anchored "current" |
| `1440-wall-hierarchy-compact.png` / `-expanded.png` | the panel closed / open |
| `1440-selection-size.png` | `큰 순` |
| `390-position-mode.png`, `390-position-mode-expanded.png`, `390-full-page.png` | phone |
| `1600-full-page.png` | full page |
| `tf-{15s,1m,10m,1h,4h,1d}-1440.png` | timeframes (SHORT sim) |
| `interaction-pan-1440.png`, `reconnect-down-1440.png`, `hot-paper-1440.png` | zoom/pan, API down, HOT |

Measured (`v3_qa.json`):

| | |
|---|---|
| 390 / 1440 / 1600 | ticket shift 0 px (context vs context blocked), chart y and height shift 0, horizontal overflow 0, **text overlap 0** |
| autoscale | price span and visible bars identical with and without context on all six timeframes and in every width sample |
| wall lines per timeframe | 6 / 6 / 4 / 2 / 0 / 0, as the table above |
| reconnect | API blocked: lines 0, paths `--`, flow and OI `--`. Back: lines and rows return |
| HOT (MAGICUSDT) | panel absent, context requests 0 |
| banned words on the panel | 0 (HOLD, EXIT, STOP, TP, 익절, 손절, 추천, 강화, 약화, CANCEL, CONSUMED, ...) |
| requests | 3,749 browser requests, all GET |

Defects the preview found and fixed:
1. **Ask wall below "current" on PAPER** (cross-venue mark). Fixed by anchoring on the collector mid (section 2).
2. **390 px: price and percentage overprinted** in the two-column layout. The overflow check missed
   it because the grid cells did not overflow, only their text did. The QA check now measures text
   extents: it found 6 overlapping rows on the old build and finds 0 on the fix. The paths stack on
   a phone.
3. Six full wall tags at the left edge covered candles and each other, so tags are now name only.

## 5. Preview incident: Binance 429 on the production IP (09:11-09:21 KST)

The first QA runs opened BINANCE_LIVE pages through a pass-through GET proxy. Each LIVE poll makes
production 8100 send signed Binance reads.

What happened:
- LIVE_RECONCILE rose from about 1 per 15 min to 41 per min.
- 70 reconciles answered `BINANCE_UNREACHABLE` (-1003, IP weight limit 2400/min) between 09:11 and 09:21.
- No 418, no order, no mutation.
- The LIVE position was unchanged, and PIDs and restarts were unchanged.
- Traffic was cut at 09:22:21 and the next reconcile was ready.

Side effects:
- The audit mirror got about 240 extra LIVE_RECONCILE lines.
- S1 (`manual_performance.json`) was refreshed by the terminal at 09:21:24.
- crypto-paper's cgroup `memory.max` count went 177 → 890 (OOM 0) across the preview window. It was flat (0 per minute) once the preview stopped.

The final runs used the caching proxy above, which cannot reach `/binance/*`.

**Rule for later previews:** never open the LIVE source through a pass-through proxy. The real LIVE
LONG screenshot therefore comes from the first build only.

## 6. Tests

- Backend: `test_btc_context_ui_v1.py`, `_structure.py` and the new `test_btc_context_ui_v3.py`:
  60 pass. They cover walls equal to the collector's set, nearest order, size rank, side OK gating,
  stale, recent ends, entry selection, closed or flat episodes, entry not yet snapshotted, stale
  collector at entry, the cache, writes nothing, torn line, the API with and without a store, a
  broken store, and no internal vocabulary.
- Frontend: `btc-position-context.test.tsx` (31) covers:
  - 3 ask / 3 bid render, nearest order, published walls only;
  - entry distance LONG and SHORT, current distance without a position, mid anchoring;
  - entry freeze, current update, wrong-side entry;
  - wall replacement, disappearance and arrival;
  - flow, OI and funding entry → current, no advice words, stale or none;
  - lines per timeframe, selection follows, off-pane walls untagged, tag stack on the pane;
  - in the terminal: POSITION/MARKET switch, timeframe switch, selection, HOT, API down and
    reconnect, no non-GET, the same order request with and without V3.
- Mutation checks, each confirmed to fail and then reverted: SHORT sign, 4h line count, mark-first anchoring.
- Whole frontend: 61 files / 1,144 tests pass, tsc clean. The lint warnings are pre-existing and not in these files.

## 7. Decisions for the reviewer

1. Wall selection: `가까운 순` or `큰 순` (or keep the switch).
2. Wall lines per timeframe (table in section 2), or hide walls on 1h as well.
3. Whether the V2 resistance and support lines stay. R0 found no edge in them; V3 neither adds to nor emphasises them.
4. Whether a size-change or "강화/약화" state is wanted. It needs a rule on figures the collector does not publish yet (size when first seen).

## 8. Before any production deploy (not done)

- Deploy order as V1: frontend artifact, then the API.
- `BTC_CONTEXT_MANUAL_STORE` stays unset until the recorder runs in production.
- The API's added reads are one stat per store file per request and a re-read only on change.
