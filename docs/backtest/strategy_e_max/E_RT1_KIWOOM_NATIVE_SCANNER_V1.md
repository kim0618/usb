# E-RT1 - Kiwoom-Native Realtime Scanner / Execution Feasibility V1

| | |
|---|---|
| Verdict | **E-RT1 BLOCKED — KIWOOM DATA CAPABILITY** · **A CONTINUES INDEPENDENTLY** |
| Strategy | `STRATEGY_E_MAX_V1`, unchanged |
| Measured | 2026-09-22 15:00-15:40 KST (ET 02:00-02:40, Kiwoom overnight session), real app key, market data only |
| Artifacts | `strategy_e_max_kiwoom_scanner_capabilities_v1.json`; `app.strategy_e_max_rt.{kiwoom_capability,premarket_state}`; probe `app.dev.run_e_rt1_kiwoom_probe` |
| Tests | `backend/tests/strategy_e_max/test_e_rt1_kiwoom.py` |
| Orders sent | none (no order or account TR exists in the client; `STRATEGY_E_MAX_ENABLED` stays false) |

## A. Kiwoom APIs actually tested

| API | Result |
|---|---|
| `usa10099` symbol list | ND 5,189 · NY 5,573 · NA 1,937 listings (the exchange code needed per symbol) |
| `usa20100` quote | one symbol per call; `cur_prc`, `base_close_pric`, `pre_open/high/low_pric`, `acc_trde_qty` |
| `usa20530` / `usa20540` / `usa20550` rankings | each stops at **1,000 rows** (20 rows × 50 pages) |
| `usa06011` minute chart | **100 bars per page**, 04:00-20:00 plus overnight (ET business-day hours 24-27), ET bar-start timestamps, no VWAP |
| `strt_dt` paging | starts at 23:59 of D and walks back; 04:00 of D is on page 11-12 |
| REST rate | 30 calls each at 3, 5 and 8 req/s all 200; at 12 req/s HTTP 429 `[1700 … 유량=5, API ID=usa20100]` → **5 req/s per API ID** |
| WebSocket FE | REG of more than **200 items in a session** refused (`105115 … 허용 개수(200)`) |
| Same-key sessions | 6 two-session trials with registrations: the second LOGIN closed the first (SYSTEM, `1000 Bye`) in **4**, both stayed in 2 (once holding 200 + 200) → **one reliable subscribing session per key** |
| US condition search | not available among the US endpoints (no US TR found; unverified with Kiwoom) |
| Orders | not implemented; the client allowlist holds market-data paths only; `Settings` forces `simulation` + `market_data_only` with Kiwoom |

## B. Premarket support

The data exists:

- `usa06011` returns 04:00-09:29 bars.
- FE carries premarket prints (FID 290 = `1`). FID 13 is the business-day cumulative volume, reset at
  the premarket start, and its delta equals FID 15 (probe of 2026-09-17).
- FIDs 16-18 are **fixed** during premarket, so the premarket high and low must be built from
  every print.

## C. Limits relevant to E

- REST: 5 req/s per API ID, and only `usa06011` gives minute bars.
- Realtime: 200 symbols per app key.
- Rankings: at most 1,000 rows each.

## D. Full-universe feasibility (canonical D-1 universe = 2,553)

Every H5 input (gap, RVOL, range position, 09:00 return) needs the whole universe's bars starting
<= 09:24 with no later print. A 09:25 decision therefore needs, for each symbol, information that
exists only after 09:24:59.

| Mode | Result |
|---|---|
| Full WebSocket from 04:00 | 2,553 > 200 per key → **infeasible** |
| A: safe prefilter + WS | no false-negative-free prefilter (see E) → **infeasible** |
| B: REST scan + rotating WS | unsubscribed periods lose prints → high / low and 09:00 window inexact; quote snapshots after 09:24 contain post-cutoff prints → **infeasible** |
| C: condition search | not available → **infeasible** |
| D: REST rolling minute | the final page of every symbol can only be read after 09:24:59: 2,553 / 5 = **511 s > 300 s** → **infeasible** |

The rolling 04:00-09:24 design is right in principle, and D keeps every symbol's history current
until the end. The last few minutes before the cutoff still have to be read for every symbol after
the cutoff, and 5 req/s is too slow for that.

## E. Safe prefilter

No prefilter meets `false_negative = 0`:

- rankings stop at 1,000 rows, while more than 1,000 of about 12,700 listings are regularly
  positive;
- ranking price or volume filters use today's values rather than E's D-1 values;
- no US condition search;
- none of the H5 conditions is monotone before 09:24. Gap, range position and the 09:00 return can
  flip until the cutoff, and volume only rises.

The false-negative proof is therefore not constructible, and no prefilter is used. The breadth
denominator stays the canonical universe (test).

## F. False-negative proof

Not applicable: no candidate prefilter exists to prove. The arithmetic is tested. It shows the
modes would pass if the limits allowed it (for example with 3,000 realtime items, or a universe of
1,000 under mode D).

## G. RVOL source compatibility

**Sample:** 15 symbols × 3 sessions (2026-09-17, 09-18, 09-21), Kiwoom `usa06011` against the
Massive forward minute data, with the frozen `premarket_block` applied to both.

| Measure | Result |
|---|---|
| premarket volume Kiwoom / Massive | median 0.669, min 0.44, **max 47.1** |
| per-symbol dollar-volume ratio spread over 3 sessions | 1.13 (ALB) to **76.8** (AMD) |
| last price / high / low within 0.1% | 30 / 27 / 18 of 42 |
| range position >= 0.8 agree / 09:00-return sign agree | 30 / 35 of 42 |
| 09:30 open / 09:34 close within 0.1% | 26 / **42** of 42 |

**Cause.** Kiwoom carries prints the consolidated tape does not have, and irregularly. On AAPL
2026-09-21 the 07:00 bar is 760,207 (Kiwoom) against 2,311 (Massive), and the 16:00 bar is
9,387,048 against 792,257. A same-source Kiwoom RVOL would spike on those days, and a Kiwoom
numerator over a Massive denominator is biased (about 0.5-0.7x).

**Meaning.** `KIWOOM_NATIVE_EVIDENCE` is a different measurement of the same rule. It is not
Massive development evidence. The exit price (09:34 close) matched in all 42 cases.

## H. 04:00-09:25 rolling scanner (implemented part)

`premarket_state.PremarketBook` keeps, per symbol, the incremental FE state:

- last price, high and low built from every print, the first price and the cumulative volume
  (FID 13);
- dollar volume as trade volume × price;
- the 09:00 window open and its minutes.

Behaviour:

- It drops events stamped 09:25 or later.
- It skips duplicates and ignores stale (older) snapshots.
- A feed gap inside the premarket, or a subscription that began after 04:00, makes the symbol
  FEATURE_CONTEXT_INCOMPLETE. Nothing is set to zero.
- On synthetic events it reproduces the frozen `premarket_block` values (test).

It is the component any WebSocket path needs. It is not wired to a live feed because no feed can
cover the universe (D).

## I. 09:25 latency

Not measurable end to end, because the state cannot be completed for the universe. The computation
itself is in memory: the 09:25 decision reuses RT0's `decide_from_frame`.

## J. A / E concurrent runtime

- Unchanged from RT0. The runtime's default decision source now applies the measured capabilities
  (`runtime.KiwoomMeasuredCapacity`) and fails closed with all five mode blockers plus the RVOL
  blocker.
- `STRATEGY_E_MAX_ENABLED` remains false, and A is untouched.
- **Caution:** Kiwoom WebSocket sessions of one key displace each other. Any future E WebSocket must
  be the key's only subscribing consumer, and dev probes must not run while it is live.

## K. Kiwoom broker compatibility

- **No Kiwoom US order TR is implemented** in the repository, and none was called.
- RT0's order intents are `SimBroker` intents: symbol, side, integer quantity, reference price and
  `market_as_of`. A Kiwoom order adapter would map them one to one, but it does not exist yet.
- Market data and broker stay separate interfaces, and live routing stays Kiwoom-only by
  construction (`Settings` guard).

## L. Alternatives without changing the strategy (to verify, not assumed)

1. **Realtime capacity from Kiwoom itself.** Verify with Kiwoom whether more realtime capacity is
   available: more registrations per session, or more app keys or accounts each with its own
   session. The universe needs about 13 × 200. This is the only Kiwoom-native route to the exact
   09:24 state for the full universe.
2. **Same-source RVOL history.** A daily after-close `usa06011` collection of every eligible
   symbol's session costs about 12 pages × 2,553 ≈ 30,600 calls, about 1.7 h at 5 req/s. After 20
   sessions it gives a Kiwoom-on-Kiwoom denominator. The evidence would be KIWOOM_NATIVE, a separate
   class that needs its own declaration, because Kiwoom volume differs from the development measure.
3. **Not allowed:** a later decision time, a narrowed universe, or a volume-based candidate cut.
   Each of these changes E.
4. External data is not the conclusion here. The Massive forward collection continues as
   independent reconstructed evidence.

## M. Final gate

```text
E-RT1 BLOCKED — KIWOOM DATA CAPABILITY
A CONTINUES INDEPENDENTLY
```

What cannot be obtained for the full canonical universe by 09:25 is the 09:24-cutoff premarket
state behind all four H5 inputs, especially the premarket high / low / last and the 09:00-window
price. The realtime push is capped at 200 symbols per key, and the REST path needs 511 s after the
cutoff. In addition, a Kiwoom-sourced `premarket_rvol` measures a different volume from the
development RVOL.
