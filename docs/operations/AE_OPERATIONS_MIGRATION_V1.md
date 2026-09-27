# A/E Operations Migration V1 (2026-09-27)

Operating UI and paper reporting moved to the two active strategies, A and E. B, C and D are
closed research. This file records the audit, what changed, and what was deliberately left alone.

## 1. Strategy lifecycles

| Strategy | Research lifecycle | Operations | Internal id | Book |
|---|---|---|---|---|
| A | PASSED_TO_PAPER | PAPER | `STRATEGY_A` | paper DB (`simulation_trades`, `account_daily_performance`) |
| E | PASSED_TO_PAPER | PAPER | `STRATEGY_E_MAX_V1` (not renamed) | `paper_state/<evidence>/STRATEGY_E_MAX_V1/` files |
| B | CLOSED 2026-09-23 | RETIRED | `STRATEGY_B` | none |
| C | CLOSED 2026-09-21 | RETIRED | `STRATEGY_C` | none |
| D | CLOSED 2026-09-21 | RETIRED | `STRATEGY_D` | none |

The registry (`backend/app/strategies/registry.py`) is the single source. The live operational
state (RUNNING / WAITING_SIGNAL / POSITION_OPEN / PAUSED / DATA_ERROR) is computed per request.

## 2. Legacy B audit

Word-boundary search (`Strategy B`, `strategy_b`, `strategy-b`, `STRATEGY_B`, `전략 B`, `B 계좌`, ...):

| Type | Where | Action |
|---|---|---|
| 1. UI display | `/trading-b`, `/strategy-b`, `/strategy-compare` pages; `strategyTabs`; nav `activePaths`; `OPERATING_ROUTES` | changed |
| 2. Internal id reused by E | none. E never used a B id, route, account or ledger key | nothing to migrate |
| 3. Closed B research | `backend/app/strategy_b`, `backend/app/backtest/strategy_b*`, `backend/app/dev/*b*`, B tests, `docs/backtest/strategy_b`, `frontend/components/strategy-b-*`, `frontend/mocks/strategy-b*` | untouched |

Finding: the B screens never read B or E data. They rendered a frontend mock module
(`STRATEGY_B_MOCK = true`) and were still offered as a result tab ("전략 B · 실시간 모멘텀",
"전략 A/B 비교"), so a closed strategy read as a running one (case C).

`lib/dashboard-source.ts`, `components/dashboard-summary.tsx` and `mocks/dashboard.ts` are the old
A/B mock dashboard. No page imports them; only their tests do. They were left in place.

## 3. Data migration

None. No DB column, persisted key, ledger row or runtime file carried a B identifier for E, so
nothing was renamed or rewritten, and no paper record was changed.

## 4. Defects fixed on the way

- `GET /strategies/STRATEGY_A/trades` read `shadow_trades` control rows, which the paper runtime
  never writes. It returned `[]` while A had 3 closed paper trades. It now reads `simulation_trades`
  of the active account.
- A positions reported `cost_basis` from a key the broker projection does not have
  (`invested_notional`), so it was always null.

## 5. Recorded-accounting finding (not corrected)

Every stored paper row, A and E, was written under the pre-contract SimBroker convention
`net = gross - total_cost`, which charges price-embedded spread and slippage twice
(`TOTAL_COST_DEDUCTED_V0`). The ledger adapter labels each row. A's ledger net (72.36) is 8.12
below its equity change (80.48) for this reason. Restating the rows, and deploying the accounting
contract, is an operator decision.

## 6. New read API

| Route | Purpose |
|---|---|
| `GET /api/v1/strategies/cards` | one card per operating strategy (status, equity, today realised/unrealised, net, trades, last signal/trade) |
| `GET /api/v1/strategies/ledger` | canonical trade records, each owned by one book |
| `GET /api/v1/strategies/performance` | A, E (OFFICIAL), Combined, and the frozen paper gate |
| `GET /api/v1/strategies/portfolio` | exposure by symbol, cash usage, 50/50 risk-budget simulation baseline |

## 7. 5-year data purchase

Not now. Purchase is considered when A or E has a backtest PASS, a sufficient paper sample under
`AE_PAPER_EVALUATION_GATE_V1`, and no forward or execution defect open. It is preferably bought
once, when both look promising, for historical OOS, regime robustness and long-horizon portfolio
interaction.
