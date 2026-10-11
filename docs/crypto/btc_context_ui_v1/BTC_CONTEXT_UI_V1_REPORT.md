# BTC Manual Trading Market Context UI V1 (read-only integration)

2026-10-11. Development and verification only. **Not deployed.** Final verdict:
`READY_FOR_PRODUCTION_READ_ONLY_UI` (deploy is a separate, approved step; see K).

## A. Data sources

| Section | Source | How it is read |
|---|---|---|
| LIQUIDITY | Production Context Collector (`ctx-collector.v1`, running since 2026-10-10) | `<CTX_V1_ROOT>/state/context_latest.json` through the collector API's own `judged()` (its staleness floor applies unchanged) |
| FLOW | same file | the collector's `flow.windows["60s"]` buy/sell notional |
| STRUCTURE | Price Structure R1 level book | Market Context V1 `structure.Series` (fixed anchor, 30 d of public Binance 1m klines, R1 engine, thetas 3.0/6.0 unchanged) |
| DERIVATIVES | Binance USDⓈ-M public endpoints | Market Context V1 `derivatives.read` (premiumIndex, openInterest, openInterestHist 5m) |

Not connected: Q1/Q2/Q3 detector or recorder, C1, absorption/vanish rows, research labels.
Liquidation was **not** added: liqfwd has no read-only API today, only its own hourly segment files,
and the task allowed it only "if already available read-only".

No new threshold and no new signal. The only arithmetic is a share of a sum (buy / (buy+sell)), an
absolute value, a sign and display rounding (`view.py` header lists every rule).

## B. API

`backend/app/crypto/btc_context_ui_v1/`, its own process, port **8014**, bound to 127.0.0.1.

- `GET /health` -> `{ok, symbol, collector_root_set, server_time_ms}`
- `GET /snapshot` -> the display payload (C). Nothing else is served: FastAPI's `/docs`, `/redoc`
  and `/openapi.json` are disabled, and a test asserts the route set is exactly these two.

POST/PUT/PATCH/DELETE answer 405 (test). No order/account/leverage/AUTO route. The package imports no
paper, live, terminal, C1, Q123, HOT or exit-guard module and calls no write API (AST tests).
Structure and derivatives refresh in one background thread, started by a request only when due,
never awaited by the request: no viewer, no venue traffic. Derivatives floor 15 s; structure
re-runs only when a new 1m bar has closed. Local measurement: RSS 112 MB, `/snapshot` 1-4 ms,
first full series load 3.6 s.

## C. UI fields

```
STRUCTURE    R 0.17% / S 0.07%          nearest R1 resistance / support distance, abs %
LIQUIDITY    ASK STRONG / BID STRONG    collector wall_state OK -> STRONG, NONE -> NORMAL
FLOW         BUY 81 / SELL 19           collector 60 s aggressive buy/sell notional share
DERIVATIVES  OI ↑ 7.71B                 OI value (USDT) and sign of its 1 h change (closed 5m buckets)
             FUND -0.0011% · BASIS -0.046%
```

Payload keys are exactly `symbol, server_time_ms, structure{live,resistance_pct,support_pct},
liquidity{live,ask,bid}, flow{live,window,buy_pct,sell_pct}, derivatives{live,oi_direction,
oi_change_pct,oi_window,oi_value_usdt,funding_pct,basis_pct}` (test). A backend test scans the
payload, live and stale, for Q1/Q2/Q3, SHORT, LONG, CANDIDATE, RESEARCH, UNVALIDATED, SAMPLE,
P_VALUE, CONFIDENCE, SIGNAL, CONTRACT, VERSION, HASH, ABSORPTION, CANCEL, CONSUMED, STALE, UNKNOWN,
PARTIAL, UNAVAILABLE, REASON, AUTO: 0 hits. The frontend test scans the rendered text the same way.

## D. Layout

The terminal's existing context slot (`marketContext`, gated on `wide`, mounted once):
PC = left column directly under the chart; phone = under the order ticket.
Phone renders one row per section (label left); PC renders four content-width cells that wrap
rather than truncate.

Measured in headless Chromium (production `next build` + `next start`), ticket rect measured with
the strip in layout and then removed from layout **in the same page load**:

| width | strip w x h | order ticket shift (x,y,w,h) | horizontal overflow | truncated text |
|---|---|---|---|---|
| 1600 | 916 x 66 | 0, 0, 0, 0 | 0 | 0 |
| 1440 | 756 x 66 | 0, 0, 0, 0 | 0 | 0 |
| 1280 | 596 x 106 (wrapped) | 0, 0, 0, 0 | 0 | 0 |
| 390 | 358 x 110 | 0, 0, 0, 0 | 0 | 0 |

The first QA pass found LIQUIDITY and the FUND/BASIS line cut to an ellipsis at 1440 (cells of
~180 px); fixed by content-width cells before the numbers above were taken.

## E. BTC-only behaviour

`renderBtcContext(symbol)` returns `null` for anything but BTCUSDT before the strip's chunk is even
requested; the strip component returns `null` as well. Browser: on the HOT tab the strip count is 0
and `/snapshot` requests in the following 7 s are 0 at all four widths; returning to BTC brings it
back with live values.

## F. Failure fallback

- API unreachable, HTTP 500, or a stale payload: every value `--`, every dot grey, no error text,
  no `role=alert` inside the strip, LONG still enabled, ticket position unchanged (browser, 1440/390).
- A good answer older than 15 s (API went away mid-session) ages out to dashes (test).
- Backend: an old collector file is floored to STALE by the collector API and publishes `null`;
  a missing or unreadable file is UNKNOWN and publishes `null`; venue down keeps the worker alive
  and STRUCTURE/DERIVATIVES read `--`.
- A build without `NEXT_PUBLIC_BTC_CONTEXT_BASE` mounts no strip and sends no request.

## G. Browser QA

Production build of `HEAD` frontend (clean `git archive` export) plus the five changed frontend
files; a fake 8100 serving the recorded paper fixture (refuses every non-GET with 405 and counts
it); the real `btc_context_ui_v1` on 8014 over a local Context Collector instance (live Binance).
Screenshots and raw measurements: `qa/` (strip, full page, HOT tab, failure modes).

## H. non-GET proof

- Browser (all origins, all pages): 1,044 requests, **GET 1,044, non-GET 0**; `/snapshot` 25, all GET.
- Fake 8100 receiving end: GET 728, **non-GET 0** (`qa/mock_8100_tally.json`). It also counted 376
  OPTIONS: CORS preflights of the terminal's own GETs (they carry `Content-Type`), cross-origin only
  in this QA setup; production is same-origin behind nginx. The strip's GETs carry no header and
  are not preflighted.
- No order button was pressed in the browser. The order path is covered in vitest instead (I).

## I. Tests

- Backend `tests/crypto/test_btc_context_ui_v1.py`: 25 passed (fixtures recorded from the production
  collector file and live MC V1 layers).
- Frontend `components/btc-context-strip.test.tsx`: 18 passed - render, API success, API down,
  stale/unknown, aged-out hold, BTC-only and HOT hidden (no poll), 1600/1440 placement under chart,
  390 placement after ticket, ticket container unchanged, non-GET 0, **LONG sends the identical
  request (url, method, body) with and without the strip**, ticket usable with API down.
- Frontend full suite: 1,102 / 60 files passed on 17 of 18 runs. One early run had 1 failure that
  never reproduced in 17 further runs; its name was not captured.
- Backend `backend/tests/crypto` full suite (run from the repo root): **3,047 passed**, 0 failed.
- tsc and eslint clean.

## J. Changed files

New: `backend/app/crypto/btc_context_ui_v1/{__init__,__main__,api,view}.py`,
`backend/tests/crypto/test_btc_context_ui_v1.py`, `backend/tests/crypto/fixtures/btc_context_ui_v1/*`,
`frontend/lib/btc-context.ts`, `frontend/components/btc-context-strip.tsx`,
`frontend/components/btc-context-strip.test.tsx`, `frontend/components/__fixtures__/btc-context-snapshot.json`,
`deploy/btc_context_ui/*`, this report and `qa/`.

Modified: `frontend/components/crypto-terminal-context-slot.tsx` (slot now mounts this strip, BTC only,
env `NEXT_PUBLIC_BTC_CONTEXT_BASE`; it no longer mounts the MC V1 research panel),
`frontend/app/crypto-paper/page.tsx` (comment only).

Untouched: order logic, CLOSE, Safe MAX, leverage, AUTO, ExitGuard, HOT trading, Context Collector,
Q123 recorder, `crypto-terminal.tsx`, the MC V1 panel and its preview route.

## K. Production readiness

> **Superseded for STRUCTURE by `MEMORY_LIGHT_V1_REPORT.md` (2026-10-11).** The API no longer
> holds the 30-day series: a oneshot worker (`usb-btc-context-structure.timer`) steps R1 forward a
> minute at a time and writes a snapshot the API reads. API 54 MB instead of ~106-112 MB; payload
> unchanged. The items below still apply, plus the worker unit, its state dir and the deploy order
> in that report's section I.

Ready for a read-only deploy, with these preconditions:

1. **Commit set must include five Market Context V1 modules that were never committed**:
   `backend/app/crypto/market_context_v1/{__init__,contract,venue,structure,derivatives}.py`
   (untracked since 2026-10-05, byte content unchanged by this work). They are in the import closure;
   every other closure file (36) is tracked and unmodified at HEAD.
2. Server: port 8014 free; RAM headroom for ~112 MB RSS (unit `MemoryMax=200M`); the earlier
   host check showed ~300 MB available.
3. Install: `deploy/btc_context_ui/stage_release.sh <commit> <out>` -> `/root/usb_runtime/btc_context_ui/
   {src,config,logs}`, unit `usb-btc-context-ui.service` (read-only on the collector root).
4. nginx, inside the usb.jptcalc.kr 443 block:
   `location /btc-context-api/ { limit_except GET { deny all; } proxy_pass http://127.0.0.1:8014/; }`
5. Frontend build: add `NEXT_PUBLIC_BTC_CONTEXT_BASE=/btc-context-api` to `.env.production.local`
   (clean `git archive` export as in the existing deploy recipe), frontend restart only.
6. Rollback: remove the env line and rebuild (strip disappears, no request), or stop the unit
   (strip shows dashes, terminal unaffected).

Known limits: the structure series grows by one bar a minute for the life of the process (MC V1
design; a restart re-anchors); after a period with no viewer the first poll shows STRUCTURE and
DERIVATIVES as `--` for one cycle while the refresh runs; liquidation is not shown.
