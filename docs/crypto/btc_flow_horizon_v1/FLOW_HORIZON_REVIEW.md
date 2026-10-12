# BTC Wall UI Flow Horizon V1 (2026-10-12)

Verdict: **C. READY_FOR_FLOW_HORIZON_UI_REVIEW**, with one thing to confirm: the aggregation source
is Binance 1m taker volume, not the Collector journal (SOURCE_AUDIT.md: the journal cannot be exact).
Local preview only. Not deployed, nothing installed on production, no Collector change.

## Aggregation
Worker `btc_context_ui_v1/flow_horizon.py` (one-shot, like `structure_worker`): keeps 1440 minute
rows `(open, quote, taker_buy_quote)` in `flow_series.json` (68 KB), fetches only newly closed
minutes, writes `flow_horizons.json` (1 KB). Windows end at the newest closed minute:
`[end - N min, end)`, N = 10 / 60 / 240 / 1440, fixed, independent of the candle timeframe.
Per horizon: buy_notional, sell_notional (Decimal sums), buy_pct/sell_pct (whole percent, sum 100),
`lean` BUY/SELL/EVEN from the exact notionals, coverage_seconds / expected_seconds / coverage_ratio.
A horizon with any missing or unreadable minute, or a zero total, publishes null figures
(`quality` PARTIAL / UNKNOWN) -> the screen shows `--`. No new cut-off: "complete" is the
definition of "the last N minutes". The API publishes `flow_horizons` as LIVE while the newest
closed minute is within one minute plus Market Context V1's `STRUCTURE_GRACE_MS`, the rule STRUCTURE
already uses; otherwise `--`.

## API
`/snapshot` gains `flow_horizons: {live, end_ms, horizons{10m,1h,4h,1d}}`; every existing field and
the 60 s `flow` are unchanged. The API reads the 1 KB file per request (no journal, no numpy, no
series). Env `BTC_CONTEXT_FLOW_HORIZONS`, flag `--flow-horizons`; unset = every horizon `--`.

## Performance
API: +1 small file read per request, no new thread. Worker: 138-157 ms, one public GET per run
after the first. No journal read, no Collector/crypto-paper contact. Production install is not done;
it would be a systemd timer beside `usb-btc-context-structure.timer` (design only).

## UI
Default area = title "체결 흐름 / 매도 | 매수" and four two-sided bars (10분 1시간 4시간 1일),
centre line 50:50, sell red growing left, buy teal growing right (same hues as the wall lines), the
larger side's share only ("매수 70%", "중립", "--"). No strength words. Everything else (wall
counts, wall prices, gaps, size, persistence, NEW/CARRIED, multiple, first seen, OI, funding, basis,
60 s flow, entry vs current, coverage, R/S switch) is behind "상세". The 5-minute browser memory is
removed (no bins, no hook).
Sort switch placement: A (`/crypto-paper`) beside the title, B (`?sort=detail`) inside the detail;
default A, your choice. Screenshots of both.

## Measured (preview_20261012/qa_shots.json)
Ticket delta 0 px on details open at 390/1440, overflow 0 at 390/1440/1600, chart box and visible
bars unchanged, tag overlap 0, values identical on 15s/1m/1h/4h/1d, non-GET 0, signed Binance 0.
Real values at the time: 10m 61 %, 1h 58 %, 4h 52 %, 1d 53 % (all BUY lean; coverage 100 %).
Mixed / all-buy / all-sell / missing-4h screenshots are SYNTHETIC (named so), the rest REAL.

## Limits
* Exchange-time minutes: the window ends at the last closed minute, so values lag up to ~1 min.
* A venue outage ages the snapshot to `--`; nothing is back-filled.
* Not installed on production: needs the timer unit and the env var.
* Preview terminal data is cached production GET (mark and walls from different moments).
