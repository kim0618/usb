# BTC Context UI V1: Memory-Light Production Architecture V1

2026-10-11. On top of 31b6f2d. **Not deployed.** The screen, the payload and the R1 structure are
unchanged; what changes is which process holds the 30-day series.

## A. Where the 112 MB went (31b6f2d, measured)

Real process, one stage at a time (`qa/memory_stages_31b6f2d.py`, local, same Python 3.12.3 /
numpy 2.5.2 / fastapi 0.141.1 / uvicorn 0.52.4 as the server):

| stage | RSS MB | delta |
|---|---|---|
| python | 10.4 | |
| A fastapi + uvicorn | 42.4 | +32.0 |
| B view + collector `judged` API | 45.8 | +3.4 |
| C MC V1 venue + derivatives | 46.3 | +0.5 |
| D price structure imports (numpy, R0/R1) | 59.2 | +12.9 |
| E 30-day series loaded (43,700 kline **dicts**) | 96.9 | **+37.7** |
| F R1 run + derivatives read | 103.6 | +6.7 |
| G 10 min, no viewer | 103.6 | 0 |
| G 10 min, a refresh every 5 s (viewer) | 105.7 | +2.1 |

So: ~46 MB is the web stack, ~13 MB numpy/R1 code, ~38 MB the series as dicts, ~7-9 MB the run.
A full R1 re-run over 30 days takes 0.5 s.

## B. What STRUCTURE needs

The strip shows two numbers: the R1 nearest resistance and support distance of the last closed
minute. Producing them needs the R1 engine state after every minute since the anchor: zigzags
(1h/5m), level book, retest pool, range state, session VWAP, plus the 1m/5m/1h bars it indexes.
The series is needed to *build* that state, not to keep re-deriving it: R1 is a single forward pass
and publication (`snapshot`/`publish`) reads the book without changing it (only the repaint audit
records what it saw).

## C. Incremental R1

`btc_context_ui_v1/structure_state.py`:

- First build = `ContextEngine.run()` (R1's own loop), same anchor rule (30 days, UTC day floor),
  same thetas, same 10-minute publish window as 31b6f2d.
- A forward step adopts a series that extends the processed one and runs `Engine.run`'s per-minute
  body for the new minutes only (no study counters). The step refuses a series whose processed
  prefix differs in any field (ts, OHLCV, turnover, ATR, 5m/1h ts and ATR) and refuses gaps.
- Checkpoint = pickle of the engine, with a sha256 fingerprint over the ten source files that
  decide a level. Other code, other symbol or an unreadable file: discarded, rebuilt from the venue
  (what 31b6f2d did at every process start).

## D. Parity

Frozen BTC 1m file, 53 days (`qa/memory_light_parity.py`, result `qa/memory_light_parity_result.txt`):

- Reference: one R1 run over the whole file publishing every minute after day 30 (33,120 minutes).
- Incremental from a 30-day build: all 33,120 minutes stepped (single minutes plus 520 multi-minute
  steps of 2-241 minutes), 17 restarts from a pickled checkpoint; **7,902 published rows compared
  whole-dict, 0 differences** (nearest R/S price, distance, level id, lifecycle, strength, sources,
  every other row field). Audit: 0 violations, 0 vanished, both ways.
- The 31b6f2d code path (`Series.run` over truncated dict rows) equals the reference at 26 sampled
  minutes.
- Span covered: 24 UTC days, 66 confirmations, 53 weakenings, 95 expiries, 141 breakouts, 125
  sweeps, 576 touches, 63 distinct nearest levels, 1,665 nearest-id changes, max 1 h move 3.7 %.

The unit tests repeat this on a seeded synthetic series (CI has no frozen file).

## E. Snapshot architecture

```
timer (every minute, :05)  ->  structure_worker (oneshot)
                               load checkpoint | rebuild
                               fetch the closed 1m bars since the last one (normally 1)
                               step R1, write structure_snapshot.json (atomic), checkpoint (atomic)
                               exit
API (8014, resident)       ->  GET /snapshot reads structure_snapshot.json (<= 64 KB), collector
                               `judged` file, derivatives cache; no numpy, no R1, no series
```

- Atomic write: temp file in the same directory, flush + fsync, `os.replace`, directory fsync.
  300 replacements under a concurrent reader: 0 partial reads (test).
- Missing / unreadable / not JSON / foreign symbol / oversized / bad `bar_close_ms` / older than
  60 s + 90 s grace (MC V1's own `STRUCTURE_GRACE_MS`): STRUCTURE `--` (tests). Trading UI untouched.
- Cadence: one step per closed minute; no new bar -> nothing written. Derivatives keep their 15 s
  floor; LIQUIDITY/FLOW keep the collector's own file and staleness floor.
- Worker lock: `flock` on `state/.structure_worker.lock`; a second run exits `LOCKED`.
- Venue failure: nothing written; the snapshot ages to `--`.
- Snapshot 343 bytes. Checkpoint 3.7 MB, grows ~0.12 MB/day (the anchor stays fixed, as 31b6f2d's
  did for the life of a process). A rebuild (`rm` the checkpoint) re-anchors.

## F. API RSS, before and after

| | 31b6f2d | memory-light |
|---|---|---|
| cold | 59 (imports) | 51.3 |
| after first request | 104 | 54.1 |
| 10 min with a viewer | 105.7 | 54.1 |
| 30 min with a viewer (poll 5 s) | - | **54.2 (HWM 54.2)** |

## G. Worker

| run | peak RSS | wall | CPU |
|---|---|---|---|
| full rebuild (30 days, 30 venue pages) | 53.0 MB | 3.6 s | 1.1 s |
| one-minute step (30 runs) | 51.8 MB max | 0.32 s avg, 0.36 max | 0.6 s avg |
| no new bar | 42.3 MB | 0.15 s | 0.5 s |

On the 1-CPU host a step is ~1 % of a core. API and worker were measured running together for 30
minutes: resident 54.2 MB, plus 51.8 MB for ~0.3 s once a minute.

## H. Production projection (server measured 2026-10-11 16:59-17:57)

Host: 961 MB, available 185-215 MB across six samples, swap used ~403 MB of 2 GB, memory PSI
avg300 0-2 (one avg10 spike 24-47 % that cleared within a minute).

| | 31b6f2d | memory-light |
|---|---|---|
| steady added | ~106-112 MB | **~54 MB** |
| available after, steady | ~75-105 MB | **~130-160 MB** |
| momentary (worker step, 0.3 s/min) | n/a | ~80-110 MB |

## I. Deployment peak

Frontend restart: next-server MemoryPeak 188 MB vs ~108 MB running, about +80 MB for a moment.
Order that keeps the two transients apart:

1. frontend artifact swap + restart (nothing new running yet): available ~105-135 MB at the peak;
2. worker rebuild once by hand (53 MB for 3.6 s);
3. API start (+54 MB);
4. timer enable.

The worst case overlap (API + worker step + frontend restart at once) would leave ~0-30 MB; the
order above never produces it. Disk: +3.7 MB checkpoint, logs ~0.2 MB/day.

## J. API contract

`GET /snapshot` keys and values are unchanged; the frontend is not touched. A test feeds the same
R1 row through the 31b6f2d `structure_view` path and through the snapshot path and gets the same
STRUCTURE section at LIVE, at the grace edge and past it. Routes still exactly `/health`,
`/snapshot`; non-GET 405.

## K. Tests

- `tests/crypto/test_btc_context_ui_v1_structure.py` (21): bars parity with `build_bars`,
  incremental == full R1 at every published minute (with multi-minute steps and checkpoint
  restarts, level creation/expiry, day boundaries asserted non-vacuous), 31b6f2d path parity,
  rewritten history / gap refused, checkpoint round trip and refusals, worker rebuild/step/
  up-to-date against a venue stub through the real `fetch_json`, venue failure writes nothing,
  lock, corrupt checkpoint rebuilt, atomic replacement, fresh/missing/stale/corrupt/partial/
  foreign/huge/bad snapshot, API layer parity, API process never imports numpy / R1 / the worker,
  worker writes only through its state-dir paths.
- `tests/crypto/test_btc_context_ui_v1.py` (25): unchanged except two tests that asserted the old
  in-process series; "writes nothing" now covers the API's modules (the worker is tested apart).

## L. crypto-paper memory (read only, nothing changed)

Production pid 1669431 (since 13:46): RSS 175-200 MB sawtooth, anon 157-187 MB of which ~142 MB
anonymous arenas, cgroup at its 200 MB MemoryMax, `memory.events max` 13 -> 23 -> 98 between 16:59
and 17:57, OOM 0, swap 9-20 MB. No upward trend beyond the cap.

Code reading (static) points at:

1. **C1 signal runtime re-fetches 33 days every 20 s** (`c1/runtime.py:41,115-125,229-231`):
   ~47.5k Bybit bars + 47.5k spot closes + OI + funding rebuilt into a new grid each tick on a
   thread - tens of MB of temporaries every cycle. Class: **A (baseline) + large repeated
   temporary churn**; the most likely driver of the cap hits.
2. Chart-history caches (`terminal/chart_history.py`, `hot_coins/market.py`): 48 entries x up to
   3,000 bars each per provider, expired entries kept until LRU. **B**, bound high (~100 MB each in
   theory, ~40 MB realistic).
3. 15 s trade-candle dedupe sets, 50,000 ids per feed (`terminal/trade_candles.py:41`). **A**,
   ~5-7 MB per feed.
4. HOT exchangeInfo cache. **A**, ~5-12 MB.
5. Ledger, retired HOT sessions, manual performance trades: **C** in principle, per trade only,
   small.

Verdict: A + B with heavy churn; no evidence of a leak (D for exact attribution without a heap
profile). It is cgroup-local: this BTC context work neither adds to nor relieves it. Candidates for
a separate task: incremental C1 grid, `MALLOC_ARENA_MAX=2`, smaller chart caches.
