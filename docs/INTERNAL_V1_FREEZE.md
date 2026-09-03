# USB Internal V1 Freeze

## Freeze declaration

- Freeze date: 2026-09-02
- Status: **PASS — INTERNAL V1 FROZEN**
- Baseline: `USB Internal V1 — FROZEN`
- Next stage: `Kiwoom Integration Spike`

This freeze means that the internal USB V1 behavior is the baseline for the
Kiwoom spike and subsequent adapter work. It does not mean that the code can
never change. Until Paper operation produces real evidence, the frozen formulas
and contracts must not be changed merely for convenience or tuning.

## Frozen versions

| Component | Version |
|---|---|
| Quant scanner | `quant_v0` |
| Top-8 research prompt | `top8_research_v0` |
| Stock-detail research prompt | `stock_detail_research_v0` |
| Research import schema | `gpt_research_v0` |
| Evidence confidence | `evidence_v0` |
| Risk | `risk_v0` |
| Execution simulation | `execution_v0` |
| Strategy | `strategy_v0` |
| Shadow variants | `shadow_variants_v0` |
| Runtime safety / operations | `operations_v0` |
| Backend API | `/api/v1` (30 OpenAPI paths at freeze) |
| Frontend workflow | Frontend V1 / Stage 9.8 baseline |

## Completed internal stages

The baseline includes Foundation, Data Layer, Quant Scanner and stabilization,
manual GPT Research and Human Approval, Trading/Risk foundation, SimBroker and
Shadow execution, Strategy lifecycle and repair, Replay Smoke, Runtime Safety,
Backend API V1, Frontend operations/UX/localization, and deterministic UI Review
data.

## Validation baseline

- Backend: 187 passed, 0 failed, 0 skipped. Four upstream
  `exchange_calendars`/NumPy deprecation warnings are non-blocking.
- Backend byte compilation and package import smoke: PASS.
- Python dependency consistency (`pip check`): PASS.
- Alembic: head `20260901_0008`; current/check PASS; fresh upgrade, downgrade to
  `20260901_0007`, and re-upgrade PASS.
- Fresh migrated SQLite: `/health`, dashboard, market status, runtime, settings,
  and capabilities all return HTTP 200 with safe empty states.
- Frontend: lint PASS, typecheck PASS, 20 tests PASS, production build PASS, and
  seven application routes generated.
- Frontend production dependency audit: 0 vulnerabilities.
- Replay Smoke: 20 XNYS sessions, 20 scanner runs, 160 Top-8 candidates, 800
  A-E paths, 570 closed trades; determinism and order independence PASS; all
  invariants PASS with zero violations.
- UI Review seed: Candidate Pool 21, Top 8 8, Research 8, APPROVE 2, REJECT 2,
  undecided 4, Sources 11, Orders 8, Fills 6, Closed Trades 95, A-E present,
  Runtime Failures 4. Two independent temporary databases produced the same
  business summary.

Synthetic Replay validates implementation behavior only. Synthetic PnL,
win rate, and variant differences are not profitability evidence, expected
return, or proof of strategy edge.

## Frozen architecture boundaries

- Market data truth enters through `MarketDataProvider`; scanner and replay
  retain `available_at` point-in-time filtering.
- Quant truth is the persisted Scanner snapshot. Research, API, and Frontend do
  not recalculate Quant.
- Research truth is immutable imported GPT analysis plus sources; evidence is
  calculated once by `evidence_v0`. There is no automatic GPT API call.
- Human truth is `HumanDecision`; at most two current approvals are allowed and
  `APPROVE` never means `BUY`.
- Risk truth is the common Decimal `RiskEngine` plus persisted daily reservation.
  Broker lot, tick, and price rounding do not belong in Risk.
- Strategy truth is the common persisted `StrategyState`; Strategy does not
  import a concrete Broker and Shadow does not have a separate strategy engine.
- Execution and position truth comes from the Broker. SimBroker is the current
  deterministic adapter; persisted execution rows are audit/research records.
- Shadow A-E share Strategy/Risk/SimBroker behavior while preserving variant and
  risk isolation. C remains the control.
- Runtime truth is persisted `RuntimeState` and failure history. Health and mode
  are separate; SAFE_MODE/HALTED survive restart and reconciliation cannot
  silently normalize HALTED.
- Stage 9.15 simplifies only the System frontend: Runtime emphasizes health,
  unresolved failures, compact component status, operations and emergency control;
  Settings emphasizes connection/environment state and hides detailed versions by
  default. Runtime counts, resolved history, raw failure metadata, capability data,
  security policy and all API contracts remain frozen and retained.
- API routers are HTTP adapters over existing services/repositories. The
  Frontend uses Backend API data as its only source of truth and sends raw English
  mutation enums such as `APPROVE` and `REJECT`.

## Kiwoom adapter boundary

- Market data: add a future `KiwoomMarketDataProvider` behind
  `MarketDataProvider` after the spike establishes availability, session, minute
  bar, realtime, and PIT semantics.
- Execution: add a future `KiwoomPaperBroker` behind the Broker/adapter boundary
  after the spike establishes order, cancellation, fill, position, balance,
  open-order, rejection, and partial-fill semantics.
- Reference data may receive a provider behind the existing metadata abstraction
  if the measured API supplies it.
- Quant, Research, Human Approval, Risk formulas, Strategy lifecycle, Shadow
  definitions, Runtime policy, API V1, and the Frontend workflow are not to be
  redesigned merely to connect Kiwoom.
- The current Broker ABC is deliberately execution-only. The spike must define a
  typed broker snapshot/capability boundary for balances, complete order status,
  and precise reconciliation without leaking Kiwoom types into Strategy or Risk.

## Pre-Kiwoom capability checklist

The spike must measure authentication/token renewal, US-equity quotes,
premarket coverage, historical/minute bars, realtime subscription, order and
cancel behavior, fills, balances, positions, open orders, partial fills, order
status and rejection taxonomy, rate limits, token expiry, tick/lot/fractional
rules, broker-native stop support, session support, and liquidation semantics.

## Known limitations and operational notes

- Active SimBroker positions are process-local, so a seed-only UI Review database
  correctly shows no active positions.
- Shadow pyramid persistence may be null; API and Frontend are null-safe.
- There is no application authentication. Do not expose the API directly to the
  public Internet. Before Naver Cloud Paper operation, require firewall/private
  access or reverse-proxy authentication.
- An in-process heartbeat cannot report that its own process has died. Paper
  operation requires systemd plus an external watchdog/Naver Cloud monitoring.
- SQLite is local runtime state and is not shared through Git. PC A and PC B
  independently recreate deterministic review data; one Naver Cloud server will
  be the future Paper/Live operational source of truth.
- Runtime directories must remain writable on Ubuntu. Configuration uses
  environment variables and project-relative/pathlib paths; no development-PC
  absolute path or Windows-only runtime dependency is present.
- Actual commissions, slippage, FX costs, broker rounding, protective orders,
  and bot-managed stop/liquidation risks cannot be finalized before the Kiwoom
  spike and Paper evidence.
- Early architecture/planning documents still describe the originally proposed
  Vite/React Router frontend, while the implemented and current Frontend V1 is
  Next.js App Router. This is documentation history, not a runtime contract.

## Explicitly frozen

- Quant V0 formula and ranking
- Research and evidence contracts
- Human approval rule
- Risk V0
- Strategy V0
- Shadow A-E definitions
- Execution contract baseline
- Runtime Safety policy
- API V1 contract
- Frontend core workflow and raw-enum/display localization boundary

## Explicitly not frozen

- Kiwoom market-data and Broker adapters, authentication, capability/status
  mapping, actual commissions/slippage, tick/lot/fractional rounding, realtime
  transport, Paper and Live modes
- Naver Cloud deployment configuration, reverse proxy, systemd/watchdog
- Actual historical provider and any strategy performance conclusion

Future product and strategy ideas remain in `docs/V2_BACKLOG.md`.

Stage 9.10 is a Frontend navigation presentation refinement: the Sidebar uses
four trading-first work areas and `/` redirects to `/trading`. The frozen
business behavior and Backend `/api/v1` contract, including the Dashboard API,
remain unchanged.

Stage 9.11 is a Frontend Trading UX refinement that makes Portfolio and Account
the primary presentation while preserving unavailable broker truth explicitly.
Quant, Risk, Strategy, Execution semantics and the frozen Backend contract remain unchanged.

Stage 9.12 is a Frontend Analysis UX refinement: Candidates is Quant-first and
Research is GPT-result-first when an imported analysis exists. Metric units,
conditional columns, compact metadata, and import visibility are display-only;
quant_v0, prompt/schema, evidence_v0, Human Approval, and `/api/v1` remain frozen.

Stage 9.13 is a frontend-only Research detail refinement. The comparison table
opens a Candidate Detail Drawer backed by the existing Candidate Detail API and
the referenced ScannerRun snapshot. It adds no frontend inference and changes no
Research schema, prompt, evidence formula, approval rule, Backend API, or database.

Stage 9.13.1 is a Frontend Human Decision UX refinement. Research Table actions
open detail only; final decisions are made in the Drawer and displayed as 채택,
거절, or 미결정. Backend `APPROVE`/`REJECT`, the maximum-two rule, and the
meaning that APPROVE is eligibility rather than BUY remain unchanged.

Stage 9.14 is a Frontend Strategy Performance comparison refinement. It replaces
five repeated Shadow cards with a compact C control summary, an A–E comparison
table, and the existing filtered trade detail. The current API supports only an
all-time summary, so the UI labels that range honestly and does not invent a
period selector. Synthetic results remain validation-only. Backend business
logic/API, variant configuration, Shadow execution semantics, and persistence
remain frozen.

Stage 9.14.1 is a frontend-only Strategy Performance simplification. The primary
`/shadow` UI contains only the A–E aggregate comparison table, with C marked as
control and R values rounded to two decimals for display. Individual Shadow
trade records, Synthetic Replay data, filters, and their existing Backend APIs
remain retained but are not exposed on this primary screen. Period filtering is
deferred until the summary API supports ranges. No Backend, persistence,
strategy, Shadow, Replay, Risk, or Execution semantics changed.

Stage 9.14.2 adds backward-compatible optional period filtering to
`GET /api/v1/shadow/summary`. Calls without parameters retain the all-time response;
inclusive `start_date`/`end_date` bounds only restrict the input dataset. Closed trades
use the `exit_at` date in America/New_York and non-closed paths use scanner trading date,
without changing aggregate fields or A–E/control semantics. The `/shadow` UI offers
recent 7 days, recent 30 days (default), and all-time selection. No schema, migration,
strategy parameter, execution behavior, or performance metric changed.

## Scope confirmation

This gate implemented no Kiwoom or external market API, PaperBroker, LiveBroker,
authentication, strategy tuning, Quant/Risk/Shadow parameter change, new endpoint,
schema, or migration. No commit or push was performed.
