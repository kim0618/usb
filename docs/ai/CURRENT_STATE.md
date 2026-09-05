# USB Current State

Audit date: 2026-09-04. Written by reading the repository (code, tests, config,
runtime SQLite files, existing `docs/`). No application code, test, migration,
environment file, or database was changed by this audit.

Companion documents:

- `docs/ai/FROZEN_CONTRACTS.md` - what must not be changed casually.
- `docs/ai/SAFETY_RULES.md` - what an agent must never do, and the required
  verification procedure. Section 14 now also defines Development Server
  Ownership, Operator DB Protection, and the Sandbox Process Rule
  (2026-09-04).

Latest operator validation (2026-09-05, Stage 10B-9):

- Stage 10B-8.5 added opt-in scoped Simulation order/fill IDs while preserving
  legacy IDs and the existing schema. Commit `01f6bb6` (`simulation 주문 식별자
  충돌 방지`) prevents new broker instances from colliding with persisted
  history when the generated execution scope is used.
- The controlled path used the persisted TSLA `APPROVE`, reached Strategy
  `ENTER` and Risk `APPROVE`, and submitted one scoped SimBroker order. The
  09:46 ET eligible next bar fully filled it at `102.153`; the fill and order
  were persisted without changing the historical `SIM-00000001` rejection.
- The runner-authoritative position held
  `166.6666666666666666666666667` TSLA at average entry `102.153`, with cash
  `82957.50` and initial net PnL `-42.50`. Kiwoom order requests remained zero.
- Orders and fills are durable history. SimBroker cash, positions, open orders,
  trades, and PnL remain process-local and are not rehydrated by the Backend.
  Older runtime/database counts below are historical snapshots from their
  stated dates, not the latest operator-state totals.

---

## Project Goal

USB (`US Catalyst Momentum Research & Trading System`) is a single-operator US
equity day/overnight trading system with a deliberate human gate in the middle.

The intended daily cycle is:

1. Scan a bounded US equity universe after the regular close.
2. Rank it with a frozen quantitative model (`quant_v0`) and take the TOP 8.
3. Generate a manual research prompt; the operator runs it in ChatGPT and pastes
   the returned JSON back in. USB never calls a GPT API itself.
4. The operator approves at most two symbols. `APPROVE` grants today's strategy
   permission, not a buy.
5. Strategy V0 (premarket gap gate, opening range, VWAP breakout, ATR trailing,
   winner-only pyramid, conditional overnight to Day 2) and Risk V0 (1R sizing,
   capacity/reservation limits) decide and size any actual order.
6. Orders go to a broker adapter. Today the only implementation is
   `SimBroker`.
7. All eight TOP8 candidates additionally fan out to Shadow variants A-E (C is
   the control) regardless of the human decision, for strategy comparison.
8. Runtime safety (`operations_v0`) supervises NORMAL / SAFE_MODE / HALTED,
   failure history, reconciliation, and the kill switch.
9. A Next.js operations dashboard renders all of the above from `/api/v1` only.

Kiwoom Paper/Live execution is a future stage. It does not exist in the code.

---

## Current Stage

`Stage 10B-9 - Simulation Fill to Position Controlled Smoke / Passed`

- Persisted HumanDecision: TSLA `APPROVE`; Strategy `ENTER`; Risk `APPROVE`.
- Scoped order `SIM-20260905T145420Z-bdd295-00000001` completed `FILLED` with
  scoped fill `FILL-20260905T145420Z-bdd295-00000002` at the first eligible
  next-bar open plus Execution V0 costs.
- One process-local TSLA position was created and its quantity, average entry,
  cash, and initial trade cost were verified. Position/account projection from
  the separate Backend process was explicitly deferred.
- The scoped ID did not collide; exactly one order and one fill were added. The
  full Risk quantity matched, costs were spread `17`, slippage `8.50`,
  commission `17`, and FX `0`, and the historical rejected order was preserved.
- No Strategy, Risk, Execution, schema, migration, API, frontend, HumanDecision,
  or Kiwoom ordering contract changed during the smoke.

Previous stage:

`Stage 10B-4.2 - Adoption Rank / Market State Runtime Fix / Implemented`

- Display and runtime wiring only. No classification, threshold, recommendation
  rule, Quant, Research, HumanDecision, schema, or migration change.
- Root cause of the four observed `/adoption` defects (empty `#`, `추천 #` with
  no number, GPT-rank-looking row order, `최근 흐름 -`) was a **stale running
  Backend process** serving pre-10B-4.1 code: the live
  `GET /api/v1/research/adoption` response carried no `recommendation_rank` and
  no `momentum` key at all, and was ordered by GPT rank. The committed backend
  code already produces both. `BACKEND RESTART REQUIRED` for the running host
  process; no backend source change was needed or made.
- The UI label is now `순위` (the word `추천` is gone from the Adoption screen).
  Table cells render `#1`..`#7`, the drawer header renders `순위 #1`, and the
  `순위` header is `whitespace-nowrap` so it can no longer wrap to two lines.
- A missing `recommendation_rank` now renders as `-` (shared display contract)
  instead of a bare `#`. The frontend still never derives a rank from row index
  and never re-sorts; it renders the backend's own order.
- Hidden 핵심 강점 / 핵심 주의 items are now counted as `· 외 n건` instead of
  the ambiguous `+n`.
- New tests: `backend/tests/test_adoption_rank_api_stage10b42.py` (Run-2
  equivalent HTTP fixture: `recommendation_rank` 1..7 as TSLA, NVDA, MSFT, META,
  AVGO, SPCX, AAPL; momentum surfaced for every item; counts still 3/2/2; no
  `HumanDecisionRecord` written) and
  `frontend/components/adoption-rank-runtime.test.tsx` (renders the page against
  a mocked API and asserts the label, the `#1`..`#7` cells, the visible order,
  the drawer rank, TSLA `최근 흐름 +16.0%` in the success tone, and `외 n건`).

Previous stage:

`Stage 10B-4.1 - Adoption Review UX Refinement / Implemented`

- `GET /api/v1/research/adoption` now returns a deterministic
  `recommendation_rank` computed from persisted truth only (classification
  group, then GPT rank, then Quant rank, then symbol) and returns items in that
  order. No new score, no threshold change, no persistence, no migration.
  `adoption_filter_v0` classification is untouched; Run 2 stays 3/2/2 and its
  read-only recommendation order is TSLA, NVDA, MSFT, META, AVGO, then the
  excluded SPCX, AAPL.
- Adoption items also carry the ScannerRun snapshot `momentum` value alongside
  the existing `rvol` / `relative_strength`, for display only.
- The Adoption table gained a leading rank column (labelled `순위` since Stage
  10B-4.2); the Human Review drawer is now `종목 · 채택 검토` and follows
  순위/헤더 → 회사 설명 → 판단 요약 → 단기 가격 상태 → 채택 이유 →
  순위 변화 이유 → 위험 / 주의 → 최종 결정.
- 근거 자료, 무효화 조건, and the raw 미확인 사항 list were removed from the
  adoption drawer only. `sources`, `invalidation_summary`, and `unknown_fields`
  remain in the Research schema, persistence, and API responses.
- 단기 가격 상태 shows only backend-observed values (전일 종가/고가/등락,
  RVOL, 시장 대비, 최근 흐름, Pre/Post market). There is no authoritative live
  quote, so no `현재가` field exists and the snapshot close is never labelled as
  one.
- HumanDecision is unchanged: APPROVE/REJECT, max 2 approvals, 409 guard.
  `순위 #1` is a review order, not an approval.

Previous stage:

`Stage 10B-4 - Adoption Candidates Human Review Workflow / Implemented`

- `risk_score` is displayed as safety (higher is safer) across Research UI.
- Shared analysis tabs include `/adoption`; GPT Analysis is result-only, while
  detail and APPROVE/REJECT live exclusively in Adoption.
- Backend `adoption_filter_v0` classifies persisted research deterministically
  without persistence or GPT calls. Run-2-equivalent regression is 3/2/2.
- `GET /api/v1/research/adoption` is read-only. No migration was added and the
  existing maximum of two human approvals remains unchanged.

Previous stage:

`Stage 10B-3.4 - Minute Pagination Depth Fix / Implemented`

- Kiwoom minute history now follows continuation until the oldest canonical ET
  timestamp reaches the requested start boundary or continuation ends, with a
  bounded 50-page hard cap.
- Reaching the cap while continuation remains and the target is still unreached
  raises `INSUFFICIENT_HISTORY`; it is no longer misreported as provider no-data.
- The collection result retains pages used, target reach, and remaining
  continuation for bounded diagnostics without exposing payloads.
- A bounded TSLA check for 2026-09-03 reached the 04:00 ET target boundary in
  12 pages (continuation still available), but returned zero canonical bars in
  the requested 04:00-20:00 ET window. Pagination truncation is fixed; this
  particular historical sample remains unavailable from the current canonical
  provider result. Premarket coverage remains unconfirmed.
- ScannerRun 2's persisted `latest_close` (`370.51`) is the same-day regular
  close and is used as the safe postmarket return base when regular minute bars
  are absent. No database, frontend, or server lifecycle change was made.

Previous stage:

`Stage 10B-3.3 - Extended Session History & KST Display Polish / Implemented`

- Research Detail now resolves both premarket and postmarket from the selected
  US `trading_date`, even when the current display session is CLOSED.
- Premarket uses the last valid 04:00-open ET bar against the persisted previous
  regular close. Postmarket uses the last valid regular-close-20:00 ET bar
  against that trading date's regular close.
- Missing extended-hours coverage is displayed as `데이터 없음`; no synthetic
  value or current-time substitution is used.
- Evidence `published_at` now uses the shared KST-primary formatter, with ET
  retained only as a tooltip. Stored timestamps and API timezone semantics are
  unchanged.
- The existing bounded market-context cache is keyed by symbol and US trading
  date, and one minute-bar fetch is reused for both extended sessions.

Previous UI/data-composition state:

`Stage 10B-3.2 - Research Detail Market Context Completion / Implemented`

- Scanner snapshots now retain company name, exchange, previous daily O/H/L/C,
  previous return, volume, and RVOL in the existing JSON persistence contract.
- Research Detail composes bounded, read-only extended-hours context with a
  60-second per-symbol/session cache and typed missing reasons. No DB migration
  was added.
- User-facing primary timestamps are KST, while XNYS trading dates and internal
  America/New_York semantics remain unchanged.
- Display-only market status distinguishes PREMARKET, REGULAR, POSTMARKET, and
  CLOSED using 04:00/20:00 ET boundaries and the actual XNYS close.
- A bounded AAPL verification found company/exchange fields in `usa10100`, no
  industry/sector candidate field, and 1,000 canonical minute OHLCV rows, all
  observed as POSTMARKET. Premarket coverage remains unconfirmed. Order requests
  and account mutations remained zero.

Previous operator state:

`Stage 10B-3.1 - Real Market Review Runtime Alignment / Human Input Pending`

- Stage 10B-1 (real market scanner) and Stage 10B-2 (real market simulation,
  session policy) are implemented and documented.
- Stage 10B-3 is a read-only operator readiness check. On 2026-09-04 it reported
  `Operator ready: NO` because the isolated real-market review database contains
  a real ScannerRun but no imported GPT research and no human decision
  (`docs/REAL_MARKET_SIMULATION.md`, verified again below against the live DB).
- Stage 10B-3.1 adds the explicit `real_market_operator` runtime profile so the
  scanner, Backend API, and Frontend operator session share the existing real
  review DB without copying data (`docs/REAL_MARKET_OPERATOR.md`).
- Market data can be real Kiwoom data. Execution is Simulation only. Kiwoom
  order requests and account mutations are still zero, and the Kiwoom client
  cannot reach an order endpoint (`ALLOWED_ENDPOINTS` in
  `backend/app/integrations/kiwoom/client.py`).

### Completed Stages

| Stage | Scope | Evidence |
| --- | --- | --- |
| 1-8 | Foundation, Data Layer, Quant Scanner, GPT Research + Human Approval, Trading/Risk, SimBroker + Shadow, Strategy lifecycle, Runtime Safety | `docs/INTERNAL_V1_FREEZE.md` |
| 9.x | Backend API V1 (`/api/v1`, 29 routes + `/health` = 30 OpenAPI paths), Next.js frontend, UX/localization refinements, design system, UI review seed | `docs/API_V1.md`, `docs/FRONTEND_V1.md`, `docs/DESIGN_SYSTEM.md`, `docs/UI_REVIEW_DATA.md` |
| Internal V1 Freeze | 2026-09-02 baseline `USB Internal V1 - FROZEN` | `docs/INTERNAL_V1_FREEZE.md` |
| 10A | Kiwoom market-data-only integration audit + adapter (OAuth, quote, daily, minute, mapping, rate limit, allowlist, live smoke) | `docs/KIWOOM_INTEGRATION_SPIKE.md` |
| 10A.1 | Session policy audit (PREMARKET / REGULAR / POSTMARKET intent vs implementation) | `docs/SESSION_POLICY_AUDIT.md` |
| 10B-1 | Bounded real-market scanner over Kiwoom ranking TRs; real TOP8 persisted to an isolated review DB | `docs/REAL_MARKET_SCANNER.md` |
| 10B-2 | Real market data into frozen Strategy/Risk/SimBroker; XNYS-aware execution session guard | `docs/REAL_MARKET_SIMULATION.md`, `backend/app/strategy/session_policy.py` |
| 10B-3 | Read-only operator readiness reporting; still pending real human research import/decision | `backend/app/dev/run_real_market_simulation.py` (`--check-ready`) |

---

## Current Architecture

```text
Market Data Provider (fake | replay | kiwoom)
  -> Symbol metadata + DailyBar / MinuteBar (point-in-time, aware datetimes)
  -> QuantScanner (quant_v0)            -> ScannerRun + ScannerCandidate (TOP8)
  -> ResearchPromptService              -> manual prompt text (no GPT call)
  -> GPTImportService                   -> immutable GPTAnalysis + sources + evidence_v0
  -> HumanDecisionService               -> APPROVE / REJECT, at most 2 approvals
  -> StrategyV0Engine                   -> ENTER / HOLD / ADD / EXIT / OVERNIGHT_HOLD
  -> SessionPolicy (XNYS)               -> entry/pyramid allowed only in REGULAR
  -> RiskEngine (risk_v0, Decimal)      -> OrderIntent or rejection reason
  -> SimBroker (execution_v0)           -> order / fill / position / trade (in memory)
  -> FastAPI /api/v1                    -> Next.js operations dashboard

Parallel: TOP8 -> Shadow variants A-E (same Strategy/Risk/SimBroker code,
isolated per-variant daily risk state, C = control).
Supervising: RuntimeHealthService (operations_v0) - NORMAL / SAFE_MODE / HALTED.
```

Configured providers:

| Item | Code default (`backend/app/core/config.py`) | Repository-root `.env` today |
| --- | --- | --- |
| `MARKET_DATA_PROVIDER` | `fake` | `kiwoom` |
| `BROKER_PROVIDER` | `simulation` (validator rejects anything else) | `simulation` |
| `KIWOOM_MODE` | `market_data_only` (validator rejects anything else) | `market_data_only` |
| `KIWOOM_ENV` | `real` | default (`real`) |
| `RUN_KIWOOM_LIVE_SMOKE` | `false` | `1` |
| `RUN_KIWOOM_REAL_SCANNER` | `false` | not set (false) |
| `RUN_REAL_MARKET_SIMULATION` | `false` | not set (false) |
| `DATABASE_URL` | `sqlite:///data/runtime/usb.sqlite3` | not set (default) |

So the effective posture is `Market Data: KIWOOM_REAL`, `Broker: SIMULATION`.
Note that the checked-in `.env` also carries live Kiwoom credentials and
`RUN_KIWOOM_LIVE_SMOKE=1`; any process that constructs `Settings()` without
`_env_file=None` inherits that live-capable posture. See
`docs/ai/SAFETY_RULES.md`.

---

## Backend Baseline

- Latest baseline recorded inside the repository: **187 passed, 0 failed, 0
  skipped** at the Internal V1 freeze (2026-09-02,
  `docs/INTERNAL_V1_FREEZE.md`). That number predates Stages 10A, 10A.1, 10B-1,
  and 10B-2 and is therefore stale.
- Latest reported baseline outside the repository (conversation-level, not
  written into any doc): **212+ passed**. Treat it as `latest reported
  baseline`, not as a repository-verified number.
- Static count today: 29 files under `backend/tests/`, 179 module-level test
  functions, plus 11 `parametrize` decorators that expand further. This is
  consistent with a ~212 collected total but is not a substitute for a run.
- Alembic head: `20260901_0008` (`backend/migrations/versions/`).
- The audit did not execute `pytest`. See `docs/ai/SAFETY_RULES.md` for why
  running the suite while the root `.env` is live-capable needs care.

## Frontend Baseline

- Latest baseline recorded inside the repository: lint PASS, typecheck PASS,
  **20 tests PASS**, production build PASS, 7 routes
  (`docs/INTERNAL_V1_FREEZE.md`). That number predates Stages 9.10-9.16 and is
  clearly stale.
- Latest reported baseline outside the repository: **117+ passed**.
- Static count today: 13 test files (`frontend/components/*.test.tsx`,
  `frontend/lib/*.test.ts`), 106 `it(` blocks.
- Stack in the repository: Next.js 15 App Router, React 19, TypeScript,
  Tailwind 3, Vitest + Testing Library. Routes: `/` (redirect to `/trading`),
  `/trading`, `/candidates`, `/research`, `/shadow`, `/runtime`, `/settings`.
- Frontend build artifacts are isolated by an explicit `NEXT_DIST_DIR` contract:
  `npm run dev` uses `.next-dev`, while `npm run build` and `npm run start` use
  `.next-build`. This prevents production validation from replacing chunks or
  manifests held by the running development server. The legacy `.next` tree is
  not used by these package scripts.

---

## Runtime / DB

Current operator snapshot (verified read-only on 2026-09-06):

- HumanDecision: 1 (`TSLA = APPROVE`); Strategy states: 0; daily Risk states: 0.
- Execution orders: 2; execution fills: 1.
- Historical `SIM-00000001` remains `REJECTED / NO_NEXT_BAR / filled 0`.
- Scoped `SIM-20260905T145420Z-bdd295-00000001` is `FILLED` and is linked to
  `FILL-20260905T145420Z-bdd295-00000002`.
- SQLite `quick_check` is `ok`. The older table below is the original
  2026-09-04 audit snapshot and is retained as history.

Three SQLite files exist under `data/runtime/` (all git-ignored). Read-only
inspection on 2026-09-04:

| File | Created by | Alembic | Contents |
| --- | --- | --- | --- |
| `usb.sqlite3` | `alembic upgrade head` | `20260901_0008` | **Empty**: 0 scanner runs, 0 research, 0 decisions, 0 orders/fills, 0 shadow trades, 0 failures |
| `usb_ui_review.sqlite3` | `python -m app.dev.seed_ui` | `20260901_0008` | Synthetic: 1 run (`USB_UI_REVIEW_SEED`, trading date 2025-10-31), 21 candidates, 1 GPT analysis, **0 human decisions**, 8 orders, 6 fills, 120 shadow trades, 4 runtime failures |
| `usb_real_market_review.sqlite3` | `python -m app.dev.run_real_scanner --persist` | **none** (created with `Base.metadata.create_all`, no `alembic_version` table) | Real: 1 run (`KIWOOM_REAL`, trading date 2026-09-03, universe 10, 8 candidates, TOP8 8), 0 GPT analyses, 0 decisions, 0 execution rows |

Real TOP8 stored in the review DB (rank order): TSLA, SPCX, META, AVGO, NVDA,
MU, AAPL, MSFT. This matches `docs/REAL_MARKET_SCANNER.md`.

The default API process still reads `DATABASE_URL` and resolves to the **empty**
`usb.sqlite3`. An explicit `RUNTIME_PROFILE=real_market_operator` session now
resolves to the existing real review DB. The scanner uses that same profile
target, and the Frontend continues to use only the Backend API.

`data/runtime/replay_smoke_report.json` holds the last synthetic replay smoke
run: 20 XNYS sessions (2025-11-03 to 2025-12-01), 20 scanner runs, 160 TOP8
candidates, 800 shadow paths, 570 trades, 0 invariant violations, determinism
and order-independence both true. It is explicitly labelled implementation
validation only, not profitability evidence.

**SimBroker authoritative state is process-local and in-memory.**
`backend/app/broker/sim.py` keeps cash, orders, fills, positions, and trades in
memory. Persisted records include ScannerRun/ScannerCandidate, GPT research,
HumanDecision, and immutable `execution_orders`/`execution_fills` history.
`ExecutionRepository` writes the execution history copies,
but positions, cash, open-order state, trades, and PnL have no persistence or
rehydration path. DB execution history alone therefore cannot authoritatively
reconstruct current account state. A separate API process cannot see an account
or position created by a controlled runner. It can query persisted order/fill
history, while `GET /api/v1/trading` correctly answers
`broker_mode: SIMULATION`, `availability: NO_ACTIVE_SIM_BROKER` with an empty
account, except for one narrowly gated UI-review fixture (see below).
`backend/tests/test_real_market_simulation_stage10b2.py::test_sim_broker_is_process_local`
locks this in.

The UI-review account/position fixture in `backend/app/api/router.py`
(`ui_review_mock_active`) is enabled only when `APP_ENV` is `development`/`test`
**and** the resolved database URL contains `ui_review`. It can never appear on
the real review DB or on a production DB. It must never be confused with an
operator run.

---

## Kiwoom Status

Read-only market data only. There is no Kiwoom broker, no order builder, and
`/api/us/ordr` is absent from the client allowlist.

| Capability | Status | Notes |
| --- | --- | --- |
| OAuth (`au10001`) | READY | Live PASS 2026-09-04; in-memory token cache, 60s pre-expiry reissue; never logged or persisted |
| Quote (`usa20100`) | READY | Live PASS; canonical mapping verified |
| Daily OHLCV (`usa06012`) | READY for bounded runs | Live PASS; 100 rows/page, 200 dates over two pages, continuation observed; production depth/adjustment still unproven |
| Symbol metadata (`usa10100`) | PARTIAL | Identity fields present; no market cap, no shares outstanding |
| Market cap (`usa20550.mac`) | PARTIAL | Direct value usable, but the currency/unit contract is not stated by Kiwoom. Missing values stay `None` -> `MISSING_MARKET_CAP`, never 0 |
| Ranking universe (`usa20530`, `usa20540`, `usa10099`) | READY for bounded runs | Acquisition pre-filter only, limit 1..100; production universe deliberately disabled |
| Minute OHLCV (`usa06011`) | PARTIAL | Target-aware pagination implemented with a 50-page cap and explicit `INSUFFICIENT_HISTORY`; bounded TSLA 2026-09-03 reached the target in 12 pages but yielded no matching canonical window bars |
| Session field | PARTIAL | Derived from ET timestamps; XNYS calendar remains authoritative |
| Adjusted price (`upd_stkpc_tp=1`) | PARTIAL | Requested, corporate-action semantics unproven |
| WebSocket (`FE`/`FT`) | DEFERRED | Audited only; no implementation, no subscription |
| FX / KRW | DEFERRED | Only account-specific rates exist (`ust31301`, `base_exrt`); not approved as a trusted market FX source; no FX endpoint allowlisted |
| Account TRs | DEFERRED | Audited only; none allowlisted or called |
| Order TRs | FORBIDDEN | Not allowlisted; order request count instrumented and asserted zero |

Live evidence to date: Kiwoom order requests **0**, Kiwoom account mutations
**0**.

---

## Known Documentation Conflicts

Resolution order used: actual code > actual tests > newest stage document >
older document. No existing document was edited by this audit.

1. **UI review seed decisions.** `docs/INTERNAL_V1_FREEZE.md` says the UI Review
   seed produces `APPROVE 2, REJECT 2, undecided 4`. `docs/UI_REVIEW_DATA.md`
   says the seed creates no `HumanDecision` at all (`APPROVE 0 / REJECT 0 /
   Undecided 8`). Code wins: `backend/app/dev/seed_ui.py` never writes a
   `HumanDecisionRecord`, and `usb_ui_review.sqlite3` contains 0 rows in
   `human_decisions`. A stale comment in `backend/app/api/router.py`
   (`ui_review_mock_account`) also still refers to "the seeded APPROVE symbols
   (S04, S09)"; those symbols are the seed's TOP8, not approvals.
2. **Quant normalization wording.** `docs/REAL_MARKET_SCANNER.md` describes
   "winsorized min-max normalization". The implementation
   (`backend/app/scanner/normalization.py`) and `docs/QUANT_SCANNER.md` both
   specify winsorize at p5/p95 then a population z-score (`ddof=0`). The z-score
   description is correct.
3. **Frontend stack.** `AGENTS.md`, `docs/ARCHITECTURE.md`, and
   `docs/DEVELOPMENT_PLAN.md` describe Vite + React Router. The implemented
   frontend is Next.js 15 App Router. `docs/INTERNAL_V1_FREEZE.md` already
   records this as documentation history, not a runtime contract.
4. **Kiwoom stage placement.** `docs/DEVELOPMENT_PLAN.md` Stage 10 says the
   Kiwoom spike lives in a separate `spikes/kiwoom/` tree and must not be
   attached to the main project until Stage 11. The implemented Stage 10A put a
   read-only `KiwoomMarketDataProvider` behind the existing
   `MarketDataProvider` interface inside the main tree. The newer stage
   documents govern; the plan's intent (no broker, no order path, adapter behind
   the interface) is preserved.
5. **Test baselines.** `docs/INTERNAL_V1_FREEZE.md` records 187 backend / 20
   frontend. Both are stale (see Baselines above). No document in the repository
   records the 212 / 117 figures.
6. **`GET /api/v1/capabilities` is static.** `backend/app/api/router.py` returns
   `market_data.kiwoom: false` and `broker.paper/live: false` unconditionally,
   independent of `Settings`. `docs/API_V1.md` calls this endpoint "Actual
   implemented adapter capabilities". Under the current `.env`
   (`MARKET_DATA_PROVIDER=kiwoom`) the frontend Settings screen therefore shows
   "키움증권 미연결" and "시장 데이터: 테스트 데이터". This is a reporting gap,
   not an execution risk: it under-reports, never over-reports.

---

## Current Blockers

Ordered by how much they block the next operator cycle.

1. **Real DB migration adoption.** Runtime alignment is complete through the
   explicit `real_market_operator` profile, but the review DB was created with
   `Base.metadata.create_all` and has no `alembic_version` row. It is
   structurally compatible today but is not migration-managed; define an
   explicit adoption procedure before the next schema change.
2. **SimBroker process-local persistence.** Cash, positions, average cost, and
   open trade truth exist only inside the process that ran the strategy. There
   is no rehydration contract, and the persisted `execution_orders` /
   `execution_fills` tables are audit records that cannot safely reconstruct
   account truth. Classified `B + D` in `docs/REAL_MARKET_SIMULATION.md`. A
   global singleton would not fix restart or multi-process correctness and was
   deliberately not introduced.
3. **Market-cap unit contract (`usa20550.mac`).** Direct value, unstated
   currency/unit. Scanner eligibility uses a `$300M` threshold against it. Fine
   for bounded development runs, unproven for production.
4. **Adjusted-price semantics.** `upd_stkpc_tp=1` is requested but corporate
   action coverage is not provable from the public contract. Production Quant is
   PARTIAL until this is confirmed or a second historical source is approved.
5. **No durable historical cache.** Daily bars and metadata are cached in
   provider process memory only. At the conservative 3 req/s limiter a large
   universe is not feasible without a durable cache; production universe over
   100 symbols stays disabled.
6. **ETF / ADR policy unresolved.** Quant V0 has no exchange, ETF, or ADR
   business filter. Acquisition requests `stk_tp=1` and excludes SPY as
   benchmark, but nothing else classifies security type.
7. **WebSocket not implemented.** Realtime `FE`/`FT` is audited only. Marks and
   intraday freshness rely on REST.
8. **Minute coverage PARTIAL.** Fixed-depth false-empty truncation is removed,
   but the bounded TSLA 2026-09-03 check still produced no canonical bars after
   reaching the requested boundary. Strategy V0 is minute-bar driven, so
   real-market intraday evaluation remains unproven end to end on live data.
9. **No trusted FX source.** Only account-specific Kiwoom rates exist. KRW
   secondary display must stay off in production.
10. **Shadow source distinction missing.** `shadow_trades` has no source column;
    `/api/v1/shadow/*` hardcodes `"source": "SIMULATION"`. Real-market simulation
    results and synthetic replay/fixture results cannot currently be told apart
    in aggregation, so they must not be mixed.

---

## Immediate Next Work

1. **Durable simulation account / position persistence design.** Define the
   authoritative snapshot/event contract for cash, positions, average cost,
   open trades, and PnL across processes and restarts. Include an explicit
   migration-adoption plan for the real review DB before any schema change; do
   not use a process singleton as a substitute for durability.
2. **Backend broker ownership and rehydration.** Only after the persistence
   contract is fixed, define which runtime composes and restores the active
   SimBroker account.
3. **Position/account API and UI projection.** Project the rehydrated source of
   truth rather than inferring holdings from execution history.
4. **Lifecycle continuation.** Validate mark-to-market before adding exit,
   trailing, or pyramid smokes.
