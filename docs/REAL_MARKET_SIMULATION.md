# Stage 10B-2 — Real Market Simulation

## Architecture

```text
KIWOOM_REAL market data
→ canonical DailyBar / MinuteBar
→ frozen Strategy V0
→ frozen Risk V0
→ SimBroker
→ simulated order / fill / position / PnL
```

Market data is real; broker, orders, fills, positions, and account are
simulation-only. No Kiwoom broker or account adapter is constructed.

## Session Policy

| Session | Market data | New entry | Pyramid | Position management | Overnight |
| --- | --- | --- | --- | --- | --- |
| PREMARKET | Yes | No | No | Observe/context only | No |
| REGULAR | Yes | Yes | Yes | Yes | Review at close |
| POSTMARKET | Yes | No | No | Monitor existing position | Review only |

`MarketCalendar`/XNYS is authoritative, including holidays, DST, and early
closes. The execution coordinator also refuses to use a fill bar outside the
signal's regular session. No threshold or entry signal was added.

## Operator Workflow

1. Run the bounded real scanner and persist its TOP8.
2. Copy the existing prompt, import the user's GPT JSON, and choose at most two
   candidates in the UI.
3. During a live review, explicitly enable both opt-ins and run:

```bash
MARKET_DATA_PROVIDER=kiwoom \
BROKER_PROVIDER=simulation \
KIWOOM_MODE=market_data_only \
RUN_KIWOOM_REAL_SCANNER=1 \
RUN_REAL_MARKET_SIMULATION=1 \
.venv/bin/python -m app.dev.run_real_market_simulation
```

The command validates an existing imported analysis and human approval. It
fetches canonical minute data and reports the actual XNYS session. Approval
does not create an order. Strategy/Risk must still produce and approve an
intent. Outside REGULAR it reports that new-entry evaluation is blocked.

Read-only readiness check (no Kiwoom request):

```bash
MARKET_DATA_PROVIDER=kiwoom \
BROKER_PROVIDER=simulation \
KIWOOM_MODE=market_data_only \
RUN_KIWOOM_REAL_SCANNER=1 \
RUN_REAL_MARKET_SIMULATION=1 \
.venv/bin/python -m app.dev.run_real_market_simulation --check-ready
```

This reports the real ScannerRun/TOP8, imported candidate count, decision
counts, live XNYS session, and provider boundaries without printing secrets.
Zero approvals is a normal no-op in execution mode; it never selects a symbol.

## Simulation Account

`SimBroker` owns cash, fills, average cost, positions, realized trade PnL, and
mark-to-market equity. Marks come from canonical market data. This truth is
process-local and is lost on restart. Existing order/fill and strategy-state
records can be persisted, but there is no durable SimBroker account/position
rehydration contract. `/trading` therefore correctly reports
`NO_ACTIVE_SIM_BROKER` except for the explicitly gated UI-review fixture.

## Safety

- Market: `KIWOOM_REAL`
- Broker/account/orders/fills/positions: `SIMULATION`
- Kiwoom account: `NOT USED`
- Both explicit opt-ins default to off.
- Provider construction requires the exact
  `kiwoom/simulation/market_data_only` triple.
- The Kiwoom client exposes and reports its order-request counter.

## Controlled Smoke

The controlled-clock integration verifies human-gated Strategy → Risk →
SimBroker order/fill → position → real-mark-shaped mark-to-market/PnL without
network or broker mutation. A credentialed live invocation is optional and is
not forced outside REGULAR. The last bounded Stage 10B-1 live scanner result
remains the evidence for Kiwoom real-data acquisition.

On 2026-09-04 the live command passed its provider/broker/mode and dual opt-in
gate, then stopped before provider construction because the isolated review DB
had no imported GPT research. Consequently it made zero market-data requests,
zero Kiwoom order requests, and zero account mutations. This is the intended
no-automatic-research/no-automatic-approval behavior; the operator live cycle
remains pending a real import and human decision.

## Stage 10B-3 Live Operator Readiness

Read-only DB and prompt verification on 2026-09-04:

- ScannerRun: `1`, provider `KIWOOM_REAL`, trading date `2026-09-03`
- TOP8: TSLA, SPCX, META, AVGO, NVDA, MU, AAPL, MSFT
- Existing `ResearchPromptService` output: `top8_research_v0`, 15,445 chars,
  all eight symbols present. The API/UI prompt endpoint uses this same service.
- Imported research: none; decisions: none; execution artifacts: none
- Live session during readiness check: POSTMARKET
- Result: `Operator ready: NO`; zero Kiwoom order requests/account mutations

The remaining action is intentionally human-owned: copy the prompt in
`/candidates`, obtain the ChatGPT JSON, import it in `/research`, inspect the
details, and approve zero to two symbols. Research import and decisions persist
in SQLite across process restarts and remain separate records.

## SimBroker Process Boundary

Classification: **B + D**. The current architecture prevents a separate API
process from seeing the command's in-memory SimBroker account, and durable
position/account rehydration requires an explicit persistence contract. The
existing order/fill tables alone cannot safely recreate cash, partial open
trades, and cost truth. A no-migration global singleton would only work inside
one process and would not solve restart or multi-process correctness, so it was
not introduced.

Consequently, `/api/v1/trading` can show persisted strategy/order/fill history
but returns `NO_ACTIVE_SIM_BROKER` for the account/open-position snapshot in a
normal real review DB. The `SIMULATED_UI_REVIEW` fixture remains isolated and
must not be confused with this operator run.

## Limitations

- `usa20550.mac` unit contract: PARTIAL
- adjusted-price/corporate-action semantics: PARTIAL
- durable historical and SimBroker account cache: deferred
- bounded universe only; ETF/ADR production policy unresolved
- Kiwoom minute coverage: PARTIAL; WebSocket: deferred
- no Kiwoom paper/live broker
- Shadow records currently do not carry a distinct real-market-simulation
  source column; a migration would be required, so production aggregation must
  not mix fixture/replay results with this source yet.
