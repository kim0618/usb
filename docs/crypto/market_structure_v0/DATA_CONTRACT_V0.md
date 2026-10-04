# BTC Market Structure V0 — frozen data contract

Status: FROZEN, 2026-10-03 (KST). Contract/schema/algorithm version: `btc-ms.v0.1`.
Changes to semantics require a new version, never silent edits to this contract.
Scope: independent read-only Binance USDⓈ-M Futures BTCUSDT forward research collector.
No strategies, scores, orders, private/account streams, UI or trading service integration.

## Identity and time
Every JSONL record: `version`, `exchange=binance_usdm`, `symbol=BTCUSDT`,
`session_id` (new UUID per process), `seq` (strictly increasing within session),
`kind`, `receive_ms` (UTC Unix local receive/sample time), `mono_ns` (local monotonic
capture time), `connection_id` (nullable), `payload`. Source E/T and U/u/pu or a/f/l
are retained without rewriting in raw payload. Prices/quantities and computed decimal
values are decimal strings; counts/times are integers; unavailable values are null.
Session metadata includes config, contract SHA256, start time. Raw WS payloads are
stored before interpretation, including duplicates. Receive time precedes queueing.

## Sources and synchronization
Depth: `wss://fstream.binance.com/public/ws/btcusdt@depth@100ms`.
Trades: `wss://fstream.binance.com/market/ws/btcusdt@aggTrade`.
Only REST call: GET `https://fapi.binance.com/fapi/v1/depth?symbol=BTCUSDT&limit=1000`.
Separate WS connections, library ping/pong; reconnect with bounded exponential backoff.
Depth reader starts buffering before REST request. Discard u < snapshot.lastUpdateId;
first delta must satisfy U <= lastUpdateId <= u (NOT spot's lastUpdateId+1).
Subsequent nonduplicate deltas require pu == preceding u; U <= u and increasing u.
Absolute quantities replace levels; zero deletes, including unknown levels.
A gap, overflow, stale connection, invalid/crossed book or reconnect invalidates book,
ends wall observations, and obtains a new snapshot. Never stitch across a gap.
Raw snapshot includes REST request/receive times; synchronized checkpoint includes
levels, boundaries, last u and source event time. Each resync has a generation ID.

## Trades and flow
Official aggTrade is aggregated public market trades, NOT individually enumerated
fills. `a` aggregate ID is deduplication key; `f/l` retain first/last individual IDs.
Normalized trades: price=p, qty=q, trade_id=a, first_trade_id=f, last_trade_id=l,
aggressor=SELL if m=true (buyer maker), else BUY; event_ms=E, trade_ms=T,
receive_ms from envelope. Preserve nq/st and all future fields in raw data.
q includes RPI trades; visible standard depth is not a complete RPI liquidity measure.
Per-process high-water ID rejects duplicate/older a; no reconnect backfill in V0.
ID jumps, reconnect, stale data and out-of-order exchange time invalidate flow coverage.
Restart starts a fresh session and fresh 60-second warmup (no cross-session counting).
Rolling windows (5/15/60 seconds) use local monotonic receive time, interval
(now-window, now], reflecting information available then; exchange-time replay remains
possible from raw E/T. Store buy/sell BTC and USDT, net BTC/USDT and normalized
flow imbalance (buy-sell)/(buy+sell), null for zero denominator.

## Freshness and coverage
Freshness uses monotonic receipt age; depth stale after 2 seconds, trades after
5 seconds. Exchange lag receive_ms-E is separately recorded; lag >5 seconds or
E > receive_ms+1 second is UNKNOWN. These are operational quality thresholds.
Book base is current mid=(best bid+best ask)/2, never last trade/mark/index.
Snapshot outer bid/ask boundaries establish the known price interval. Updates outside
it cannot prove completeness. Keep only prices inside that interval. Mid outside the
interval is invalid. Each side of each band independently has COMPLETE/PARTIAL/UNKNOWN.
Bands ±0.1%, ±0.25%, ±0.5%, ±1%: bids in [mid*(1-band),mid], asks in
[mid,mid*(1+band)]. COMPLETE requires fresh synchronized book AND entire side-band
inside known snapshot bounds. PARTIAL exposes observed lower-bound qty/notional but
canonical qty/notional=null. UNKNOWN has null observed and canonical values.
Imbalance=(B-A)/(B+A) for both BTC and USDT only when both sides COMPLETE;
zero denominator => null. Never replace missing observations with zero.
Flow COMPLETE requires uninterrupted, fresh coverage for the entire window; warmup
or a window overlapping interruption is PARTIAL with null canonical values and
explicit observed totals; disconnected/stale is UNKNOWN. Genuine complete empty totals
may be zero, normalized imbalance remains null. Coverage is stream-observation
coverage, not a guarantee of exchange completeness.

## Walls
One-second sampled candidates only, separate from raw depth. Use exact price as bin.
For each side within ±1% of mid, compare quantity against mean of up to 5 adjacent
occupied price levels on each side, excluding itself; require >=3 neighbors and >=3x
mean. No spoofing/absorption labels. Payload: side, price/bin, qty/current_size,
notional, local_average, multiple, first_seen, last_seen, persistence_ms, status,
coverage, generation. Persist active and ended candidates; loss of freshness/resync
ends continuity as UNKNOWN, disappearance as ENDED. Persistence measures sampled
observation span, not proof of continuous order identity. At most 20,000 book levels.

## Persistence, schemas and recovery
Kinds: session, raw_depth, snapshot, checkpoint, raw_trade, trade, telemetry,
derived, wall, storage_stats. Payloads are schema-specific objects. Telemetry has
`event` plus detail (connect/disconnect/retry/gap/resync/stale/duplicate/overflow/etc).
Derived has `book` (state, mid, source_u, age/lag, bands) and `flow` keyed by window.
Raw + snapshot + envelope seq + telemetry are the replay authority; checkpoint
accelerates auditing. Derived sampling is once per second without invented catch-up rows.
JSONL append journal is V0 capture format (explicit research exception to market-bar
Parquet); offline Parquet conversion is outside this task. Stream files by kind,
session UUID and UTC date; rotate at 64 MiB or 1 hour, whichever comes first.
Buffered writes: 1 MiB buffers, flush each second; fsync every 10 seconds and rotation/
shutdown, NEVER per event. Active `.open` files rename to `.jsonl` on close.
Single writer per output directory via advisory lock. Restart recovers complete valid
lines of orphan .open files and truncates only incomplete tail, reporting removed bytes;
interior corruption fails closed. New session always resnapshots. Power loss may lose
up to the fsync interval plus OS/device effects; recovery never claims lost coverage.
Bound all queues (2048 depth frames, 8192 persistence records), WS frame size (1 MiB),
flow records (200,000), and levels (20,000). Overflow causes explicit invalidation or
collector termination, never silent COMPLETE. Disk failure terminates only collector.
Stats: actual serialized bytes by kind and total, elapsed monotonic time,
projected bytes/day=bytes/elapsed_seconds*86400. Rotation and recovery counters exposed.
No automatic raw-data deletion. Preview defaults to 24 hours; duration 0 is continuous.

## Official references checked 2026-10-03
- https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/How-to-manage-a-local-order-book-correctly
- https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Connect
- https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/market
