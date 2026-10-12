# BTC Context UI V2: chart-native context (review, not deployed)

2026-10-11. Verdict: **READY_FOR_BTC_CONTEXT_UI_V2_REVIEW**. The work is not committed and not deployed:
production still runs V1, which was deployed from 757f768.

The preview captures in `preview_20261011/` use production data:
- **Context API:** the production Context Collector state file and the structure worker snapshot,
  streamed read-only over one ssh connection every 1 s, are fed to this V2 API running locally.
- **Terminal:** the production 8100, through an ssh tunnel and a GET-only proxy.
- **Frontend:** the V2 artifact (`next start`, node 20.20.1).

## What changed on screen

| V1 (4-cell strip under the chart) | V2 |
|---|---|
| `R 0.09% / S 0.16%` | Lines on the BTC chart. The right axis label shows the price. A tag on the chart's left edge shows `저항 83,137.2 +0.01%` / `지지 83,113.9 -0.02%`. |
| `ASK STRONG / BID STRONG` | The collector's own nearest wall per side, as a dotted line with tag `매도벽 83,162.3` / `매수벽 83,112.0`. At most one per side. No line when the side is not OK or the source is stale. |
| `BUY 28 / SELL 72` | One compact line under the chart: `체결흐름 매도우세 72%` plus a buy/sell proportion bar. |
| `OI ↓ 7.71B / FUND · BASIS` | `OI ↑ · 펀딩 -0.0014%` on the line. OI size, the 1 h change and basis sit behind the ▾ disclosure. |

## Display rules

- **No new thresholds.** The flow shows the larger of the backend's two shares. `중립` appears
  only at exactly 50/50. Any wider neutral band needs the user's decision first.
- **The price scale is never widened.** In lightweight-charts 5.2.1, series autoscale reads only
  bar high/low and primitives, never price lines. Resistance or support off the pane gets an edge
  tag with an arrow (`저항 83,137.2 +0.01% ↑`). A wall off the pane gets nothing.
- **Label collision.** The axis label of a context line is hidden when it would land within 16 px
  of the last-price label, the account's lines, or a higher-ranked context label. The line and its
  left tag stay. Tags are pushed apart without reordering and stay on the pane.
- **Off-screen note.** Context lines are not added to the note above the chart. A longer note
  wraps at 390 px and moved the ticket by 15 px, which was measured and fixed.
- **BTC only.** Lines are drawn only when the chart symbol is BTCUSDT, whatever the caller
  passes. The HOT tab draws no lines and sends no context request.
- **One poll loop.** The chart lines and the line under the chart share a single 5 s GET loop.
  A mutation test that starts a second loop is caught by the test suite.

## API change

`btc_context_ui_v1/view.py` adds four fields:
- `structure.resistance_price` / `support_price`: the R1 level price, which the snapshot already
  carried.
- `liquidity.ask_wall_price` / `bid_wall_price`: the collector's own `nearest_wall.price`, only
  while that side's `wall_state` is `OK`.

Both pass through the existing LIVE/PARTIAL gate. All V1 fields are unchanged. The regenerated
fixture equals the old one on every old key.

## Measured (preview QA, production data)

| | |
|---|---|
| widths 390 / 1440 / 1600 | ticket shift 0 px (same width, context vs context blocked), horizontal overflow 0, banned words 0 |
| 15s 1m 10m 1h 4h 1d | price span and visible bars identical with and without context on every timeframe |
| first load | 1440 and 1600: 2 bare + 2 context pages identical. 390 at DPR 2: bare pages already differ from each other (277.8 vs 171.0, same range), so that variance predates this work |
| zoom / pan / history | lines stay at their prices; history load (3 requests) moves both levels off the top, and they show as edge tags |
| reconnect | API blocked: lines 0, tags 0, line under the chart `--`. API back: lines return |
| HOT (MAGICUSDT, 12 s) | lines 0, tags 0, line under the chart absent, context requests 0 |
| requests | 3,402 browser requests, all GET; non-GET 0 |

## Tests

- **frontend:** 60 files, 1,113 tests pass. tsc is clean. Lint shows one warning, which was
  already present at HEAD.
- **backend:** 54 tests pass (`test_btc_context_ui_v1*`, vendored parity).

## Open point for the reviewer

On 1h, 4h and 1d the four lines sit within a few pixels of the price. Their tags stack as four
rows at the left edge (see `tf-1h-1440.png`). This follows the rules above, but whether those
timeframes should show walls at all is a display choice for the reviewer.
