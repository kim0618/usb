# BTC Wall UI Compact V1 (2026-10-12)

Verdict: READY_FOR_WALL_UI_COMPACT_REVIEW. Local preview only, not deployed. Frontend-only change.

## Terminology
Chart tags 상1/상2/상3, 하1/하2/하3. Detail rows 상단벽N / 하단벽N. No user-facing 매도벽/매수벽/매도N/매수N
(scan test over chart tags, compact panel, detail, all selections). API fields ask/bid unchanged.
Also renamed in the entry-analysis preview's wall rows (label text only, no logic).

## Colour
Upper walls red, lower walls teal. Step = collector rank (distance), 1 darkest (line alpha .95), 2 (.6), 3 (.35).
Same in 큰 순 mode (step follows the wall's own rank, not its size rank). Mark grey dashed, entry blue
(unchanged). R/S hidden by default; the detail's 참고선 option draws them grey dotted.

## Compact panel (default)
Header: mode badge, current price, [entry], 가까운 순 / 큰 순 switch. Two cells: "상단벽 · 가까운 순 /
4개 · 현재 대비 +0.09%" and the lower side. 최근 5분 marks + count. OI direction, funding. "상세 ▾" closed.
Basis wording is explicit: 현재 대비 without a position, 진입 대비 with one (the brief's "최근 +0.09%" was
replaced by this to avoid confusion with 최근 5분).
Hidden until 상세: prices, gaps, notional, persistence, NEW/CARRIED, multiple, first seen, basis, 60 s flow,
entry vs current rows, recent ended walls, R/S switch.

## 5-minute pressure
Frontend only (no Collector/API change). The poll loop keeps the latest 60 s flow reading per wall-clock
minute (server_time_ms/60000). The panel counts the 5 completed minutes before the current one:
buy>sell = 매수, sell>buy = 매도, equal = 중립 (no threshold). A minute not seen stays unknown (never
filled); "수집 중" / "n/5분" is shown while collecting; stale or missing = "--". History lives in memory:
it restarts from empty on reload and when leaving the BTC tab (needs ~6 min to fill).
Marks: ● more buying, ○ more selling, ◐ even, · unknown, one neutral colour.

## Layout (preview_20261012/qa_shots.json)
390 / 1440 / 1600: ticket delta 0 px (details open, size sort, strip hidden), horizontal overflow 0,
chart box / visible bars / price span unchanged, chart tag overlap 0 in every shot, non-GET 0.

## Preview method
Cached production GET responses (GET-only proxy, upstream blocked) + the real btc-context API replaying one
cached collector snapshot. The 5-minute history in the screenshots is an accelerated replay: the browser
route moves each /snapshot answer one minute forward with scripted flow (buy 70/55/30/62/80%). Shot 8 is the
untouched real clock ("수집 중"). Cached terminal state (Bybit mark 83,752.5) and the snapshot (Binance mid
83,656.5) are from different moments, so mark and walls look offset; known preview artefact.
OI/funding values come from the local derivatives layer.

## Tests
New `btc-wall-compact.test.tsx` (21). Updated btc-context-strip / btc-position-context / entry-analysis
tests for the new DOM. Full frontend vitest 63 files / 1179 tests pass; tsc clean; eslint 0 errors.
