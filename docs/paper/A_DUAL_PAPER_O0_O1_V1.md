# A Dual Paper O0/O1 Forward Validation V1: Audit and Blockers

Status: **BLOCKED** (2026-09-21). Nothing was changed in the running paper system, no paper
account was created, and no parameter was changed. This file records the audit of the current
Strategy A paper, the parity gap to the frozen research candidates, and what has to be decided
before two forward paper accounts can exist.

## 1. Frozen candidates

| | A-STABLE (O0) | A-OVERNIGHT (O1) |
|---|---|---|
| Research run | `exp1-32cf23503bba977528fa` | `exp1-aad5293b786fab2006ae` |
| config_fingerprint | `e9cdefba...cb22f871` | same |
| strategy_config_fingerprint | `7fcd37a6...0ea8b658` | same |
| execution_risk_config_fingerprint | `70ebba1a...724697b6` | same |
| Overrides | gap 0.02->0.03, strategy/risk `max_pyramid_adds` 1->0 | same |
| Overnight authority | UNKNOWN_CLOSE | ASSUMED_HIGH (research-only) |
| Candidate authority | RESEARCH_UNIVERSE (Research Universe V2, 29 symbols), TOP8 scanner | same |
| Approval authority | ALL_RESEARCH_SYMBOLS (ASSUMED) | same |
| Market data | Massive consolidated (legacy manifest) | same |
| Starting cash | 10,000 USD (ASSUMED_RESEARCH) | same |
| Status | 1-year research candidate, 2-year validation PENDING | same |

## 2. Current (legacy) paper

Runs in production (`traderj:/root/usb`, HEAD `0efeaf6`) inside `usb-backend.service`
(uvicorn), with `BROKER_PROVIDER=simulation`, `KIWOOM_MODE=market_data_only`,
`PAPER_DATABASE_URL=sqlite:////root/usb/data/runtime/usb_paper_trading.sqlite3`, and the
`usb-morning-scan.timer` (Kiwoom real scanner `--limit 10`, 07:00 KST).

- Runtime: `app/main.py` activates `activate_operator_simulation_runtime` and three minute
  loops (entry, position, end of day). The runtime is a process-level singleton
  (`app/services/simulation_runtime.py`).
- Configs: code defaults only. `StrategyV0Engine()`, `RiskEngine()`, `ExecutionConfig()`,
  variant C hard-wired (`position_protection.ACTUAL_VARIANT`). Nothing is read from env or DB.
- Account: one SIM account `operator`, bootstrap cash `PAPER_INITIAL_CASH = 7428.92` USD,
  `paper_started_at` 2026-09-08. Bootstrap refuses a second account.
- Candidates: morning scanner TOP8 (Kiwoom) -> GPT research import -> human APPROVE.
  Overnight suitability comes from the GPT record (`GPTCandidateAnalysis.overnight_suitability`).
- Performance scope: from 2026-09-14 (`performance_baseline.py`); 2026-09-08..11 excluded.
- Account state, open positions, pending intents and trade history: **NOT AUDITED**. A
  read-only fetch of the production SQLite file was refused by the session permission
  classifier (Production Reads); no workaround was attempted.

### Legacy vs O0/O1

| Item | Legacy paper | O0 | O1 | Parity |
|---|---|---|---|---|
| Gap min | 0.02 | 0.03 | 0.03 | MISMATCH |
| Gap max | 0.15 | 0.15 | 0.15 | same |
| Premarket volume ratio | 0.05 (Kiwoom minute volume / Kiwoom daily) | 0.05 (Massive consolidated) | same | MISMATCH in data semantics (see 4) |
| Opening range | 15 | 15 | 15 | same |
| Entry deadline | 10:30 ET | 10:30 ET | 10:30 ET | same |
| Reference tolerance | 0 | 0 | 0 | same |
| Trailing ATR (normal) | 1.5 | 1.5 | 1.5 | same |
| max_pyramid_adds strategy / risk | 1 / 1 | 0 / 0 | 0 / 0 | MISMATCH |
| Overnight | GPT record (UNKNOWN when absent) | UNKNOWN_CLOSE | ASSUMED_HIGH | MISMATCH |
| Risk per trade | 0.5% | 0.5% | 0.5% | same |
| Other risk limits | 3R/day, 3 open, 3 new/day, 1 overnight | same | same | same |
| Starting cash | 7,428.92 USD | 10,000 USD | 10,000 USD | MISMATCH |
| Candidate universe | Kiwoom morning scanner TOP8 | Research Universe V2 TOP8 | same | MISMATCH |
| Approval | human APPROVE | ALL ASSUMED | ALL ASSUMED | MISMATCH |
| Market data | Kiwoom real (market_data_only) | Massive | Massive | MISMATCH |

## 3. Why two independent paper accounts cannot be added as is

- Account-scoped tables: `simulation_accounts`, `simulation_positions`, `simulation_trades`,
  `account_daily_performance`.
- Not account-scoped: `execution_orders`/`execution_fills` (broker_type only),
  `strategy_states` (unique symbol+trading_date+book+variant), `daily_symbol_states`
  (unique trading_date+symbol), `premarket_diagnostics`, `paper_entry_evaluations`
  (unique trading_date+scanner_candidate_id), `runtime_state`.
- Two accounts in one DB would share strategy state, daily risk and the evaluation funnel.
  The only isolation available today is a separate DB file per account, which with the
  singleton runtime means a separate backend process per account.
- The production VM has about 961 MB RAM (2026-09-08 OOM incident); a second uvicorn process
  next to the existing backend, frontend and the jptcalc site is an operational risk.
- There is no per-account config injection: the entry/EOD services accept an engine, risk
  engine and variant, but `start_entry_management_runtime` passes none, and there is no seam
  for an ASSUMED overnight authority in the paper path.
- Missing observability: no paper event ledger (the listed events exist only as phases,
  reasons or flags), no exit/overnight history table, no monthly report. The entry funnel
  (`paper_entry_evaluations`) does exist.

## 4. Data semantics (backtest vs paper)

| Item | Research (O0/O1) | Paper | Status |
|---|---|---|---|
| Minute bars | Massive consolidated, `available_at = start + 1 min` | Kiwoom real, `available_at` fixed 2026-09-11 | same completion rule; source differs |
| Premarket volume ratio | Massive consolidated volume | Kiwoom minute volume is about 50-56% of consolidated (2026-09-15 audit); 5% here is about 8-10% consolidated | MISMATCH |
| Opening range / VWAP | canonical indicators | same code | same code, different tape |
| Timezone / calendar | XNYS `MarketCalendar` | same | same |

## 5. Decisions required

1. Production read access for the legacy paper audit (account state, open positions, pending
   intents, trades) and for its archival as `LEGACY_A_PAPER`.
2. Where the two accounts run:
   - (a) the existing live Kiwoom paper, extended to per-account DB and config (schema or
     process split, deploy, RAM on the 1 GB VM); parity to research stays MISMATCH on
     universe, approval and volume source;
   - (b) a local forward replay: each day after the close, the unchanged research engine
     replays the new sessions for O0 and O1 as two independent accounts on the same
     authority as the research runs (Research Universe V2, Massive T-1 data). This gives exact
     research parity, month-first reports from existing analytics, and restart by
     deterministic replay, but it is end-of-day, not live;
   - (c) both.
3. Starting cash: 10,000 USD (research) or 7,428.92 USD (legacy paper bootstrap).
4. Whether the legacy paper keeps running as `LEGACY_A_PAPER` while the new accounts start.

## 6. Unchanged by this stage

Legacy paper (code, DB, services): untouched. U1 collector: untouched (running). Parameters: 0
changes. Research sweeps: 0. Real broker orders: 0. 2-year historical validation: PENDING
(U1 authority rebuild and the segmented runner).
