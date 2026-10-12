# BTC Flow Horizon V1: aggressive-flow source audit (2026-10-12)

Question: can 10m / 1h / 4h / 1d aggressive buy and sell notional be rebuilt **exactly** (no
double count, no estimate) from the Context Collector's journal? Answer: **no**. An exact source
exists and is used instead (section D). The user's rule was "if the journal cannot, report it";
this is that report, and the verdict below says which source the feature reads.

## A. What the journal holds (production `/root/usb_runtime/crypto_context/journal`, read once)

Read-only: one `ls`, one `cat` of the sealed hour `context-20261012-31cc8177-00040.jsonl.gz`
(1.2 MB, 3,601 rows, 60.0 min). Nothing else touched; no 8100 poll, no signed request.

| kind | persisted | trade-level data |
|---|---|---|
| `context` | 1 row/s: `mono_ns`, `receive_ms`, `payload.flow.windows{5s,15s,60s}` buy/sell btc+usdt, trades | **rolling windows only** |
| `telemetry`, `session`, `storage_stats` | yes | no trade content |
| `raw_trade`, `trade`, `raw_depth`, `snapshot`, `checkpoint`, `derived`, `wall` | **dropped by design** (`contract.py` DROPPED_V0_KINDS) | the raw trades are not kept |

Window semantics (`market_structure_v0/flow.py`): a window at sample time `t` sums trades whose
**receipt** monotonic time is in `(t - W, t]`, W in {5, 15, 60} s, aggressor from `m` (buyer is
maker = SELL). Session boundary: windows start `PARTIAL` until a full W of coverage exists.

## B. Why journal aggregation is not exact

1. **Rolling windows overlap.** Summing the 1 s samples of the 60 s window counts every trade 60
   times (synthetic test: > 50x). Not an option.
2. **Tiling with 60 s windows is not exact.** Taking the sample nearest each minute boundary on
   the real hour: consecutive chosen samples were `60 s - 1.59 ms .. 60 s + 1.48 ms` apart on the
   monotonic clock, so each junction leaves a gap or an overlap of up to ~1.6 ms of trades. The
   boundary trades cannot be identified afterwards (no per-trade rows).
3. **Samples go missing.** In that one hour there were 6 inter-sample gaps above 1.5 s
   (2.2, 2.4, 2.6, 3.9, 13.7 and 14.7 s). A boundary that falls in such a gap has no sample; the
   nearest one is seconds away, so the tile is off by seconds of trades.
4. **Receipt clock, not exchange clock.** A minute here is "received in", not "traded in".

Measured against the venue's own per-minute taker volume (same hour, 59 whole minutes): per-minute
buy notional error median 0.00 %, max 1.62 %; sell max 0.43 %. The 59-minute total matched to the
dollar (244,680,999 buy / 163,793,695 sell on both), which is expected (the inner boundaries
telescope) and also shows the journal is a good cross-check. But "good" is an approximation, and
the brief forbids approximation.

## C. Other candidates

* Q123 forward / liquidation collectors: store liquidation and market-context rows, no trade tape.
* D2 historical data: Bybit klines (not the Binance book the walls come from).
* MC V1 FLOW layer: the same 5/15/60 s windows as the journal.

## D. The exact source

Binance USD-M `/fapi/v1/klines` 1m rows carry, per minute, **quote volume** and **taker buy quote
volume**: the notional of trades whose buyer was the aggressor. Sell = quote - taker buy. They are
the venue's own sums over whole minutes, so they never overlap and a trailing N-minute window is
exactly the sum of N rows, with the same aggressor definition as the collector (`m=false` -> BUY).

* The path is already on `market_context_v1.venue.ALLOWED_PATHS`, is public, unsigned, and is already
  read once a minute by `structure_worker` (R1 anchor series). No account, key or order path.
* Cost: first build one page (limit 1500, weight 10), then 1 row per minute (weight 1).
* Trade-off stated plainly: this is the venue's exchange-time minute, not the collector's receipt
  time, and it is a different source from the Context Collector journal.

## E. Rolling-snapshot double count (test)
`test_rolling_snapshots_would_double_count_and_the_minute_sum_does_not`: summing 600 one-second
samples of the 60 s window over 10 minutes gives > 50x the real notional; the minute sum equals the
brute-force sum over the raw synthetic trades to the cent.
