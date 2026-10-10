# Current CRYPTO Status

As of 2026-10-10 (KST). Written by reading the repository and by measuring the production host
read-only. Every number below is either quoted from the frozen document that owns it or was
measured on `trader-j` at 2026-10-10 16:30 to 16:50 KST; the text says which.

This document succeeds `CRYPTO_CURRENT_STATUS_20261004.md` for the Manual Trading, Chart,
Storage and Exit Guard sections. It does not restate that document's Market Structure or
Liquidity Map findings, which are being revised in a separate session and are still that
document's to own. No pointer was added to the 2026-10-04 document because it has uncommitted
edits belonging to that other session, and editing it here would have collided with them.

## Scope note on roles

`AGENTS.md` holds operating principles. This document holds current facts, figures, commits and
dates. `CRYPTO_SERVER_RUNBOOK_V1.md` holds procedures. A fact does not belong in the first, a
principle does not belong here, and neither belongs in the third.

## Manual Trading

* **BTC/ETH/SOL multi-symbol is live in production.** Measured against
  `/api/crypto/binance/status`: `source=BINANCE_LIVE`, `symbols=["BTCUSDT","ETHUSDT","SOLUSDT"]`,
  `default_symbol=BTCUSDT`, `trading_enabled=true`, `credentials_present=true`,
  `client_armed=false`. Arming is per session and is disarmed at boot by the unit description's
  own promise.
* **One live position was open at the time of measurement**: BTCUSDT LONG 0.108 at entry
  83,913.1, mark 82,685.90, unrealised -132.5376 USDT (-177,732.92 KRW), liquidation 79,891.70,
  initial margin 446.50. ETHUSDT and SOLUSDT were flat.
* **Per-symbol behaviour** (chart, history, feed, position, Safe MAX, leverage, filters, order,
  CLOSE) is unchanged from V1. Selected symbol persistence lives in the terminal layout and
  survives reload through `localStorage`; the symbol tab strip reads it on mount rather than
  defaulting to BTC and correcting afterwards.
* **1H volatility and HOT are deployed.** `98feb31` is in the running artifact, verified by
  finding its three Korean UI strings in the deployed chunks with a control group (the same grep
  found none of the Market Context strings, so a hit means presence rather than a broken search).
  HOT is a label on the largest 1H range of the three symbols. It does not select a symbol and it
  does not place an order.

## Chart

* **Two different feeds, two different jobs.** The execution feed is `/api/crypto/chart` at
  `limit=120`, and it is what draws first. The history feed is `/api/crypto/chart-history`, whose
  1m initial depth is `CHART_INITIAL_BARS["1m"] = 2880`, which is 48 hours of one-minute bars.
  The 2,880 bars are not on the critical path to a drawn chart.
* **Restart seed fix is in production.** `12759f2` is on `origin/main` and was applied to the
  running runtime. Its verified effect is that all three symbols return 120 bars with
  `seed=READY` and 0 gaps on the first `/chart` of a cold process, where before the restart BTC
  returned 120 and ETH and SOL returned 28.
* **The initial-load bottleneck was never the 2,880 bars.** It was three sequential requests:
  `next/dynamic` fetched the chart component chunk, the component mounted, and only then did its
  effect `await import("lightweight-charts")`. `ec3df85` removes the middle hop. See
  **Initial Load** below.
* **Two-phase history was tried and rejected.** Serving the seed as 360 bars instead of 2,880
  moved first candles by 1 ms (2,142 vs 2,143) against the deployed API. Only the "full history"
  marker arrived sooner, which is the marker for having less history.

## Storage and disk

* **Measured free space**: `/dev/vda2` 9.8G total, 7.8G used, **1.5G available, 85% used**. This
  is after the disk cleanup. RAM measured 961 MB total with 297 MB available, and 422 MB of the
  2,047 MB swap in use. This host is small enough that a server-side Next build is a real risk to
  it, which is why the frontend artifact is built elsewhere.
* **New-segment compression is live.** `CRYPTO_PAPER_COMPRESS_SEGMENTS=true` is on the
  `usb-crypto-paper` unit, and `/api/crypto/state` reports
  `storage.compress_new_segments = true`.
* **No compressed segment exists yet, and that is the expected reading, not a defect.** Measured
  `storage.compressed_segments = 0`; on disk BTC has 64 plain segments and 0 `.gz` (553 MB,
  `segment_bytes` 573,723,198), ETH 21 and 0 (171 MB), SOL 21 and 0 (173 MB). The newest cut per
  symbol was BTC 14:03:11, ETH 10:27:07 and SOL 14:00:09, all **before** the service restarted at
  15:14:56, so no segment has been cut under the new setting. `CRYPTO_PAPER_TAPE_COMPRESSION_V1.md`
  states the same: "compressed_segments: 0 (다음 cut까지 0이 정상)".
* **The first real gz cut is therefore still unverified in production** and remains an open check:
  `compressed_segments` must move 0 to 1 and `verify_segments` must stay clean.
* **Projected effect**, quoted from `CRYPTO_PAPER_TAPE_COMPRESSION_V1.md`: 79.4 MB/day
  (BTC 31.7, SOL 24.2, ETH 23.5) becomes **8.08 MB/day**, and the runway on 1,520 MB free goes
  from 19.2 days to 188 days.
* **Historical migration has not been run.** Only new segments compress. The 897 MB already on
  disk as plain segments stays plain until a migration is separately approved.

## Exit Guard

* **Measured OFF.** `/api/crypto/binance/exit-guard` returns `state=OFF`, `enabled=false`,
  `available=true`, `guard_symbol=BTCUSDT`, `last_error=null`.
* **The stale historical ERROR is gone**, cleared by the restart rather than by a code change.
* **Do not enable it in production before a V2.** The current structure disables itself
  permanently on a single exception, and it has a rate-limit problem. Both are reasons the OFF
  state is deliberate. A live position being open, as one was during this audit, is exactly the
  case where a guard that silently died would be worse than no guard.

## Signal and research standing

* **C1 is auxiliary.** It is not the main automatic entry strategy and is not used as one. The
  Regime, Strength, Lifecycle and Directional Probability work is recorded in
  `C1_REGIME_R1_RESULTS_V1.md`, `C1_STRENGTH_RESULTS_V1.md` and `CRYPTO_C1_SIGNAL_V1.md`, which
  own their own verdicts.
* **Price Structure R0, R1 and R2 are closed as standalone research**, each landing on
  `A_NOT_USABLE`. Across R0, R1 and R2 the whole-book coverage never beat R0's 0.4444, which
  places the ceiling on the *source set* rather than on how levels are published. `repaint = 0`
  held throughout, so the mechanism is sound even though the signal is not.
* **Curated structure is usable as a position and context layer and not as an independent
  LONG/SHORT signal.** Further source fishing is stopped.
* **No LONG/SHORT score is produced or displayed anywhere in production.**

## Market Context

* **Manual Market Context V1 frontend integration is verified and the frontend is READY**
  (`9bbc2da`). It is **not** enabled in production, by design.
* **Production is disabled by four missing pieces, none of them frontend**:
  1. no `8012` systemd service, and nothing listening on 8012 (measured);
  2. no `/market-context-api` nginx band. The complete production location list is `/`, `/api/`,
     `/crypto-api/`, `= /health` and `^~ /.well-known/acme-challenge/`, measured with `nginx -T`
     against a control (`/crypto-api/` was found by the same grep that found no market-context
     band);
  3. no LIQUIDITY or FLOW runtime data on the host;
  4. the existing MS V0 raw collector costs 4.51 GB/day, which this 1.5 GB host cannot take. No
     MS V0 collector process is running (measured).
* **With `NEXT_PUBLIC_MARKET_CONTEXT_BASE_URL` unset the screen is the one already deployed.**
  `marketContextSlot()` returns `undefined`, so the panel is never rendered and its chunk is
  never requested. The flag is inlined at build time, so this costs the critical path nothing.
* **The unset default is deliberately not harmless-looking.** The panel module's development
  default base is `http://127.0.0.1:8012`, which in a production build is the *operator's own
  laptop*. A panel shipped without the configured check would poll nothing once a second and show
  dashes. The check is what makes the unset case correct rather than merely quiet.
* **`crypto-terminal-preview` has no route** and is referenced only by a test. Its mutation
  sentinel patches `window.fetch` to refuse every non-GET, which is right for a QA route and
  would block the operator's own LONG, SHORT and CLOSE. It must never reach a route the operator
  uses.
* **Next direction**: design a separate lightweight production context collector. The blocker is
  data cost and service topology, not the panel.

## Initial Load

Five cold loads each at 150 ms / 5 Mbps, medians, local production builds against the deployed
API. Quoted from `ec3df85`.

| Milestone | Baseline | After | Change |
| --- | --- | --- | --- |
| shell visible | 397 ms | 394 ms | -3 |
| price / order ticket | 1,651 ms | 1,635 ms | -16 |
| 1H volatility, HOT | 1,842 ms | 1,978 ms | +136 |
| **first candles drawn** | **3,213 ms** | **2,918 ms** | **-295** |
| full 2,880 ready | 3,310 ms | 3,296 ms | -14 |

* The gain is one round trip plus one payload, so it scales with the connection: -295 ms at
  150 ms / 5 Mbps, -78 ms at 60 ms / 20 Mbps, and nothing measurable on a LAN. It is never a
  regression.
* **P0 is untouched.** The order path, the price and the symbol tabs are unchanged, because the
  chart renders only after they are already on screen.
* **Structurally verified in the artifact.** The deployed build's
  `react-loadable-manifest.json` has two entries, `crypto-candle-chart.tsx -> lightweight-charts`
  and `crypto-terminal-layout.tsx -> @/components/crypto-candle-chart`, which is the two-hop
  chain. The new build has one chart entry whose two files are fetched together: the 162 KB
  library chunk and the 15.5 KB component chunk.

## Deployment standing

* `origin/main` was at `12759f2` before this work. `6464b0f` (compression) and `12759f2` (seed
  fix) are both ancestors of it and are deployed.
* Unpushed at the time of writing: `1df41d3` (test safety), `9bbc2da` (Market Context frontend,
  inert unless configured) and `ec3df85` (initial-load performance).
* **The production repository checkout is not the deployed artifact.** `/root/usb` was at
  `98feb31` while the running frontend artifact was built at 2026-10-10 13:13 and the crypto
  backend runs from `/root/usb_runtime/crypto_paper/src/backend` by `PYTHONPATH`, not from
  `/root/usb`. Judge what is deployed from the artifact and the runtime, never from the server's
  git HEAD.
* Frontend serves from `.next-build` via `NEXT_DIST_DIR`, with `.next-build.before-vol1h`
  retained as the previous rollback point. Node on the host is v20.20.1 and npm 10.8.2.

## Open items

1. First production gz segment cut, and `verify_segments` clean afterwards.
2. Historical segment migration, which needs separate approval.
3. Exit Guard V2, before the guard is enabled again.
4. A lightweight production Market Context collector, if that panel is ever to be turned on.
5. Liquidity Map has had no 24-hour trial and is not production. The deployed
   `/liquidity-preview` route has no backend band and nothing listening on 8011, so it is inert
   in production as well.
