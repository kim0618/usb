# E-RT0 - E-MAX V1 on the Common Virtual Trading Runtime V1

| | |
|---|---|
| Verdict | **E-RT0 PARTIAL — ENGINE READY, REALTIME DATA BLOCKER** · **A CONTINUES INDEPENDENTLY** |
| Strategy | `STRATEGY_E_MAX_V1`, unchanged (rules `b30a3e95…`) |
| Code | `app.strategy_e_max_rt.{config,decision,engine,runtime}`; `app.main` lifespan (+10 lines, flag-gated) |
| Tests | `backend/tests/strategy_e_max/test_e_rt0_runtime.py` |
| Mode | SIMULATION / VIRTUAL ONLY · `LIVE_MARGIN_APPROVED = False` |

## A. Existing A runtime (code path)

A runs in production (`traderj:/root/usb`, `usb-backend.service`, uvicorn) with
`BROKER_PROVIDER=simulation`, `KIWOOM_MODE=market_data_only` and `RUNTIME_PROFILE=real_market_operator`.

| Layer | Module | What it does |
|---|---|---|
| Entry point | `app/main.py` `lifespan` | activates the runtime, starts three minute loops |
| Broker ownership | `services/simulation_runtime.py` | process-level singleton `SimulationRuntimeContext` for the one SIM account `operator`, rehydrated from the paper DB |
| Market data | `market/factory.build_kiwoom_provider` → `market/kiwoom.KiwoomMarketDataProvider` | REST polling, `usa06011` minute chart, one symbol per call, `KiwoomRateLimits.chart` = 3 req/s |
| Scanner / candidates | `usb-morning-scan.timer` → `services/morning_scanner.py`, `scanner/` | Kiwoom scanner TOP8, GPT research, human APPROVE |
| Strategy | `strategy/engine.StrategyV0Engine`, `strategy/lifecycle`, `strategy/runner` | A's gap / OR / VWAP logic |
| Risk | `risk/engine.RiskEngine`, `risk/config` | 0.5% risk per trade with stops, 3 open, no leverage |
| Loops | `services/entry_management_runtime`, `position_management_runtime`, `end_of_day_runtime` | minute cadence over the singleton broker |
| Broker / fills | `broker/sim.SimBroker`, `execution/config.ExecutionConfig` | next-bar open fill, spread / slippage / commission bps, long only, cash-limited |
| Accounting / PnL | `broker/accounting`, `services/simulation`, `services/daily_performance` | per account |
| Persistence | paper SQLite (`usb_paper_trading.sqlite3`): `simulation_*` (account-scoped); `strategy_states`, `daily_symbol_states`, `execution_orders/fills`, `paper_entry_evaluations`, `runtime_state` (**not** account- or strategy-scoped) | |
| Restart | `rehydrate_sim_broker` at startup | |

## B. Common versus A-specific

| COMMON (reused by E) | STRATEGY_A_SPECIFIC (not used by E) |
|---|---|
| app lifespan / process host | `StrategyV0Engine`, lifecycle, runner |
| `MarketDataProvider` and the Kiwoom provider factory | `RiskEngine` (stop-based sizing, no leverage) |
| `MarketCalendar` (XNYS clock) | entry / position / EOD services and their tables |
| `SimBroker` + `ExecutionConfig` (order / fill / cost model) | the singleton `operator` account and paper DB |
| `OrderIntent`, `MinuteBar` domains | scanner / GPT / APPROVE candidate path |

A's code is not modified. The only change to a shared file is 10 lines in `app/main.py`.
`start_from_env()` returns immediately unless `STRATEGY_E_MAX_ENABLED` is true, and `stop()` does
nothing when E was never started.

## C. E integration design

```text
app.main lifespan ──> A: activate_operator_simulation_runtime + 3 loops   (unchanged)
                  └─> E: strategy_e_max_runtime.start_from_env(provider_factory)   (flag, off by default)
                           Engine.tick every 15 s in a worker thread
                           ├─ DecisionSource (09:25): V1.1 seal → H5 → R1 → max 3 → B2 → 2x / 3x
                           ├─ SimBroker (E's own book; profile buying power)
                           └─ Store: data/runtime/strategy_e_max/rt/STRATEGY_E_MAX_V1/{sessions/<D>.json, book.json}
```

**Lifecycle:** WAITING → (09:25) DECIDED → (09:30 bar) POSITION_OPEN → (09:35 bar) COMPLETE.
Refusals and failures go to NO_DECISION (FEATURE_CONTEXT_INCOMPLETE, or the decision window was
missed) or ERROR. A non-session day is IDLE, and a disabled strategy is DISABLED.

**Frozen rule reused, not rewritten:**

- `strategy_e_v1_1.decision.seal`: PIT universe, H5 through Research's mask;
- `strategy_e_max.ranking` for R1, max 3;
- `strategy_e_max.breadth` for B2;
- `strategy_e_max.v1` for the global 2.0x.

The decision is hashed and verified before every step. It is written to disk before any order
exists. A decision is never made after 09:30.

## D. Realtime data availability: the blocker

E needs, at 09:25 ET, the 04:00-09:24 bars of the **whole D-1 daily-eligible universe**. The latest
measurement is 2,553 symbols. It also needs a 20-session premarket history from the same source for
RVOL. With the common provider:

| Item | Common provider | E needs | Status |
|---|---|---|---|
| Premarket minute bars | `usa06011` returns extended hours (A's premarket-volume history reads 04:00-09:30 from it) | yes | available per symbol |
| Throughput | one symbol per call, chart limiter 3 req/s → 2,553 calls ≈ **851 s** after 09:25 | ≤ 300 s (09:25 → 09:30) | **BLOCKER** |
| Realtime push | none in the runtime (the FE/FT WebSocket exists only in a dev probe; its subscription limit is unverified) | whole universe | not available |
| RVOL denominator | Kiwoom premarket history exists only for scanner candidates (`premarket_volume_sessions`) | whole universe, same source | **BLOCKER** |
| Massive | Stocks Basic is T-1 (same-day 403) | – | not a realtime source |

**Why the universe is not narrowed.** Doing so (for example to a Kiwoom ranking list) would change
H5's population and the B2 denominator `h5_rate = candidates / universe rows`, which is a rule
change. Mixing Kiwoom numerators with a Massive RVOL history is also excluded, because Kiwoom
premarket volume is about 50-56% of consolidated.

**What the runtime does instead.** Its default decision source is `RealtimeSourceGate`. It computes
the provider's real budget (`KiwoomRateLimits().chart`, 1 call per symbol, the latest measured
universe) and refuses with FEATURE_CONTEXT_INCOMPLETE, listing both blockers. So an enabled E would
make no trade until a feature builder with enough source capacity is plugged in.

**Source equivalence.** Once a realtime source exists, its provenance is recorded per decision
(`decision.source`). Realtime-source virtual evidence is never pooled with Massive
development / forward evidence. The equivalence diagnostic (premarket volume, RVOL, H5, R1, top 3
on the same symbol and session) needs that realtime feature builder first.

## E. Strategy state isolation

- Every E record carries `strategy_id = STRATEGY_E_MAX_V1`. Idempotency keys are
  `STRATEGY_E_MAX_V1|<session>|<symbol>|<ENTRY|EXIT>`.
- E's decision, entries, exits, breadth state, events and checkpoint live in E's own JSON store.
- Nothing is written to A's paper DB or to A's non-scoped tables (`strategy_states` and the
  others). That is why no schema migration is needed.
- **E failure:** a tick exception becomes E's ERROR state. The asyncio loop keeps ticking, and A's
  loops are separate tasks. Startup and shutdown errors are caught in `start_from_env` / `stop`.
- **A failure:** A's broker and account are never read or written by E, and E's state files
  survive (test).

## F. Virtual book isolation

- E's book (`book.json`) holds `initial_equity` (default 10,000 USD, `STRATEGY_E_MAX_INITIAL_EQUITY`),
  `equity`, `realized_pnl`, a per-session history and `live_margin_approved=false`.
- Each session uses its own `SimBroker` instance (scope `STRATEGY_E_MAX_V1-<D>`). A's `operator`
  account is separate.
- A and E can hold the same symbol at the same time; attribution follows the book (test).

## G. Risk and exposure

| Check | Result |
|---|---|
| Common Risk V1 (`RiskEngine`) for E | `SIMULATION_BLOCKED_BY_COMMON_RISK`: stop-based sizing, no leverage, and E has no stop |
| Chosen path | **`REQUIRES_STRATEGY_SPECIFIC_SIM_RISK_PROFILE`**, implemented as `E_MAX_V1_SIM_EXPOSURE_PROFILE_V1` |

How the profile works:

- Normal sessions run at 2.0x and high-breadth sessions at 3.0x.
- Equal weight over the executable positions: 2 / 3 or 1 each for three positions, 1 or 3 / 2 each
  for two.
- `SimBroker` refuses a BUY above its cash. So E's broker is funded with virtual buying power =
  equity x 3.0 x 1.05. That is buying power, not equity; equity stays in the book.
- A's risk configuration is not relaxed and is not read.
- Nothing reaches a real account: the broker is `SimBroker`, the Kiwoom mode stays
  `market_data_only` (enforced by `Settings`), and `LIVE_MARGIN_APPROVED = False`.

## H. Entry and exit semantics

**Entry.** A BUY with `market_as_of = 09:29` fills at the **open of the 09:30 bar**. That is
`SimBroker`'s next-bar convention, the same as A's.

- It is submitted only after the bar has completed (from 09:31) and only after every selected
  symbol's 09:30 bar is resolved. The number of executable positions n must be known for equal
  weights, and it is fixed in the state before the first order.
- A symbol with no exact 09:30 bar by 09:33 is ENTRY_INVALID. There is no backfill.

**Exit.** A SELL with `market_as_of = 09:34` fills at the **09:35 bar open**, the first executable
price after 09:34.

- The development proxy (the exact 09:34 close) is recorded beside it as `development_proxy`, not
  as the fill.
- If the 09:35 bar has not arrived by 09:45, the position closes on the first later bar and is
  flagged `LATE_UNRESOLVED_EXIT`.

**PnL, recorded in separate fields:**

- the SimBroker fill with its own spread / slippage / commission;
- `fixed_bp_views`: weight x (raw gross - 0 / 5 / 10 / 15 / 20 bp), comparable with development.

**Execution evidence:** signal, order and fill timestamps, raw and fill price, bar volume. Bid, ask
and spread are `null` with `QUOTES_NOT_AVAILABLE_FROM_PROVIDER`; nothing is fabricated.

## I. Persistence and recovery

- The session state (phase, decision, `executable`, entries, exits, keys) is written after every
  tick, and the decision also right after deciding.
- **Restart before entry:** the stored decision is used. The decision source is not called again
  (test).
- **Restart with an open position:** `SimBroker` is rebuilt by replaying the recorded intents on
  their recorded bars. This is deterministic and checked, and the exit then proceeds (test).
- **Duplicate ticks:** they produce one entry and one exit per symbol. The book finalises each
  session once (test).

## J. A + E concurrent test

In the tests, A's broker (`SimBroker` for `operator`) buys S03 at 09:30 and E trades S03 in the same
session. A's cash and positions are unchanged, E's records are all `STRATEGY_E_MAX_V1`, and E writes
no database. With E disabled, the app lifespan starts, `/health` answers, and E's `start` is never
called.

## K. Massive forward collector

The F1 collector runs on the local workstation and writes to Drive `market_data/forward`. The A
runtime runs on the production VM. E's state goes to `data/runtime/strategy_e_max/rt`, and E does
not use the workspace writer lock. There is no writer or resource conflict. The collector was left
running (about 130 of 2,565 minute requests done at 14:27 KST).

## L. Tests

- **E-RT0:** 19 tests cover the E lifecycle (2x, 3x, H5 / R1 / max 3 / B2 identity, no backfill,
  equal weight, decision immutability, late start), the capacity gate, restart before entry and with
  an open position, duplicate ticks, session rollover, enable / disable / idle, simulation-only
  protection, A + E same-symbol isolation, E failure versus A, async loop isolation, the lifespan
  no-op when disabled, and the frozen identity.
- **Regression:** the A runtime suites (simulation runtime, operator activation, entry / position /
  EOD lifecycles, SimBroker, risk, persistence, e2e simulation smoke: 26 modules) together with the
  E-MAX, E trading and E1 forward suites give **1,019 passed**. The full-repository run was stopped
  after 16 minutes. Two Strategy B test modules fail at collection (`ImportError: ScannerDecision`)
  on uncommitted B work, unrelated to this change.

## M. Runtime start

**E is not enabled in the running system.**

1. **The realtime data blocker (section D).** Enabled today, E would make no decision on any session
   (fail closed, no orders).
2. **Deployment.** The runtime lives on the production VM, and deployment and service changes there
   are operator-run.

To enable after a feature source exists: deploy this commit, set `STRATEGY_E_MAX_ENABLED=true`
(optionally `STRATEGY_E_MAX_INITIAL_EQUITY`, `STRATEGY_E_MAX_STATE_DIR`) for `usb-backend`, and
restart. A starts exactly as before either way.

## N. Final gate

```text
E-RT0 PARTIAL — ENGINE READY, REALTIME DATA BLOCKER
A CONTINUES INDEPENDENTLY
```

The engine is ready. The blocker is one specific missing piece: a realtime feature source able to
deliver the whole D-1 universe's 04:00-09:24 bars between 09:25 and 09:30, together with a
same-source 20-session RVOL history. The candidate is a Kiwoom realtime push path, and it needs its
subscription limits measured and a source-equivalence contract. The Massive forward collection is
not the blocker.
