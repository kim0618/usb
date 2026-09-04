# USB Safety Rules

Read this before touching anything in this repository. It is written for coding
agents (Claude Code, Codex) and it overrides convenience.

This project can place real money at risk once a Kiwoom broker exists. Today it
cannot, and keeping it that way is a deliberate engineering property, not an
accident. Every rule below exists because breaking it is either irreversible or
silently corrupts evidence.

Companion documents: `docs/ai/CURRENT_STATE.md` (where the project is now),
`docs/ai/FROZEN_CONTRACTS.md` (what must not be changed). Project-wide agent
rules also live in `AGENTS.md`.

---

## 1. Git safety

Without an explicit user instruction in the current task, never run:

`commit`, `push`, `merge`, `rebase`, `reset` (especially `--hard`),
`checkout`/`switch` that discards work, `stash`, `clean -fd`, tag or branch
deletion, force push.

- The working tree usually carries uncommitted stage work. Preserve it. Do not
  "tidy up" a dirty tree.
- Never revert or overwrite a file you did not change in this task.
- Reading git state (`status`, `log`, `diff`) is always fine.

## 2. Credential safety

- Never hardcode a Kiwoom App Key or App Secret in code, tests, fixtures, docs,
  commit messages, or log lines.
- Never print, log, serialize, or return a token, key, or secret. Credentials
  are `SecretStr` in `Settings` and must stay backend-only.
- Never send credentials or tokens to the frontend, and never put a secret in a
  `NEXT_PUBLIC_*` variable.
- Never write a real credential into `.env.example` or any tracked file. `.env`
  and `.env.*` are git-ignored (with `.env.example` excepted); keep it that way.
- If you must show configuration, show presence (`configured` / `missing`),
  never the value.
- **The repository-root `.env` currently contains live Kiwoom credentials and
  `RUN_KIWOOM_LIVE_SMOKE=1`.** Any `Settings()` constructed without
  `_env_file=None` inherits that posture. Treat that file as hot: do not print
  it, do not copy it, do not commit it.

## 3. Kiwoom trading safety

Absolutely forbidden in the current stage:

- any real buy, sell, cancel, or modify order (`ust20000`-`ust20003`,
  reservation orders, `/api/us/ordr`)
- any account mutation, deposit/withdrawal, or FX exchange call
- adding an order endpoint to `KiwoomMarketDataClient.ALLOWED_ENDPOINTS`
- writing a Kiwoom broker, order builder, or broker registry entry
- calling account/balance/position/fills TRs, even read-only, at this stage

Currently allowed:

- OAuth token issue for market data
- the allowlisted read-only market-data and ranking TRs
- the bounded, opt-in real scanner

Invariants that must remain true after any change: **Kiwoom order requests = 0**
and **Kiwoom account mutations = 0**. `order_request_count` exists to prove it;
do not remove or weaken that instrumentation.

## 4. Provider / broker gate

The only safe combination right now:

```text
MARKET_DATA_PROVIDER=kiwoom   (or fake / replay)
BROKER_PROVIDER=simulation
KIWOOM_MODE=market_data_only
```

- Do not relax `Settings` validators to accept `paper` or `live` brokers, or any
  other Kiwoom mode. Those validators are the gate.
- Do not remove the duplicate check in `build_kiwoom_provider`. Defense in depth
  is intentional.
- Both live-run opt-ins default to false and must stay that way:
  `RUN_KIWOOM_REAL_SCANNER`, `RUN_REAL_MARKET_SIMULATION`, plus
  `RUN_KIWOOM_LIVE_SMOKE` for the smoke script.
- Never wire a live scanner, smoke, or simulation run into application startup,
  Alembic, pytest collection, or a scheduler.

## 5. Network and command safety

Do not run, without an explicit request in the current task:

- the Kiwoom live smoke (`python -m app.integrations.kiwoom.smoke ...`)
- the real scanner (`python -m app.dev.run_real_scanner`), with or without
  `--persist`
- the real market simulation (`python -m app.dev.run_real_market_simulation`),
  including `--check-ready`
- the UI review seed (`python -m app.dev.seed_ui`), especially `--reset`
- `alembic upgrade` / `downgrade` against a database you did not create

Each of these either contacts a live external API, spends a rate-limited request
budget, or mutates a runtime database.

## 6. Human-in-the-loop safety

- **Never generate research.** USB has no GPT API call by design. Do not add
  one, do not stub one, do not paste model output into an import as if it came
  from the operator.
- **Never create a `HumanDecisionRecord`.** Not in a seed, not in a migration,
  not in a helper script, not "to make the flow testable end to end". Test
  fixtures inside `backend/tests/` are the only place decisions may be
  fabricated, and they must stay inside test databases.
- Never auto-approve, auto-select, or rank-order your way into a decision.
- **`APPROVE` does not mean buy.** Do not add a path where approval directly
  produces an order. Strategy and Risk must independently signal and approve.
- Zero approvals is a normal, correct no-op. Do not "fix" it.
- Never exceed or relax the maximum of two approvals per analysis.

## 7. Database safety

- Three runtime databases exist and are not interchangeable:
  `data/runtime/usb.sqlite3` (default/dev),
  `data/runtime/usb_ui_review.sqlite3` (synthetic UI review seed),
  `data/runtime/usb_real_market_review.sqlite3` (real Kiwoom scanner output).
  See `docs/ai/CURRENT_STATE.md` for their current contents.
- **Never mix synthetic review data with real-market data in one database.**
  Never copy rows from one into the other.
- Never regenerate a real scanner result with a fake seed, and never fabricate a
  scanner run, research analysis, or decision to make a screen look populated.
  An empty screen that reflects the truth is correct.
- Do not solve a data-visibility problem by copying data. Fix the pointer
  (`DATABASE_URL`) or fix the contract.
- Migrations: do not add, edit, reorder, or delete a migration without an
  explicit request. Current head is `20260901_0008`. Note that the real-market
  review DB was created with `Base.metadata.create_all` and has no
  `alembic_version` row; deciding how to reconcile that is its own task.
- Never delete a runtime database, WAL, or SHM file to "reset" something.
- SQLite files, WAL/SHM, replay reports, and logs are git-ignored. Never commit
  them.

## 8. Simulation safety

- `SimBroker` state (cash, positions, average cost, open trades) is **in-memory
  and process-local**. A separate API process cannot see it. This is verified by
  `backend/tests/test_real_market_simulation_stage10b2.py::test_sim_broker_is_process_local`.
- Do not introduce a module-level global SimBroker, a process singleton, a
  cached instance, or a hidden module attribute to make the UI show an account.
  It would not survive restart and would be wrong across processes.
- Do not reconstruct account truth from `execution_orders` / `execution_fills`.
  Those are audit records and cannot recreate cash, partial open trades, or cost
  truth.
- If durable simulated account state is needed, design it as an explicit typed
  persistence contract with a migration, in its own task.
- `availability: "NO_ACTIVE_SIM_BROKER"` is the correct answer, not a bug.
- The UI-review account fixture in `backend/app/api/router.py` is gated on
  `APP_ENV in {development, test}` **and** `ui_review` in the database URL. Do
  not widen that gate, and never present it as an operator result.

## 9. Shadow safety

- Do not aggregate real-market simulation results together with synthetic
  replay or fixture results. `shadow_trades` has no source column yet, so the
  distinction cannot be made in the data.
- If source separation is needed, add the column and migration deliberately;
  do not approximate it with a symbol prefix, a date range, or a run id guess.
- Synthetic replay output is implementation validation only. Never describe it
  as profitability, expected return, or evidence of edge.
- Do not draw strategy conclusions below `MIN_ELIGIBLE_TRADES = 300` and
  `MIN_TRADING_DAYS = 60`.

## 10. Time and session safety

- Market time is `America/New_York`. Never hardcode KST market hours.
- `MarketCalendar` (XNYS) is authoritative for trading days, holidays, DST, and
  early closes. A clock-derived session label is a hint, never a policy gate.
- Every datetime crossing a domain boundary must be timezone-aware. Domain
  validators reject naive datetimes; do not loosen them.
- Preserve point-in-time discipline: `available_at <= as_of` and
  `trading_date <= trading_date`. Never let a future bar, a same-day incomplete
  bar, or a later-revised value leak into a historical calculation.
- No entry and no pyramid outside REGULAR. No fill bar outside the signal's own
  regular session.

## 11. Testing and verification

Baselines (see `docs/ai/CURRENT_STATE.md` for provenance and staleness):
repository-documented 187 backend / 20 frontend at the Internal V1 freeze;
latest reported 212+ backend / 117+ frontend.

Never let a change reduce the passing count or delete/weaken an existing test to
make something pass.

After a code change, run at minimum:

```bash
# backend
PYTHONPATH=backend .venv/bin/python -m pytest
PYTHONPATH=backend .venv/bin/python -m compileall -q backend/app
.venv/bin/python -m pip check

# frontend
cd frontend && npm run lint && npm run typecheck && npm test && npm run build

# repository hygiene
git diff --check
```

Notes:

- Before running the backend suite, be aware that the root `.env` is
  live-capable. Tests that construct `Settings()` without `_env_file=None` will
  pick it up. No current test performs a live Kiwoom call, but verify that
  before adding one.
- Market-dependent tests use fixed fixtures. Keep them deterministic; do not
  introduce `datetime.now()`, randomness, or network access into a test.
- A live smoke runs only on explicit opt-in, by the user, never as verification
  for a routine change.

## 12. Scope safety

Do not perform, as a side effect of an unrelated task:

- Quant tuning (weights, lookbacks, winsor percentiles, thresholds, tie-break)
- Strategy tuning (gap band, opening range, deadline, ATR multipliers, holding
  days, overnight conditions)
- Risk tuning (1R, daily units, capacity/reserve, exposure, pyramid rules)
- Execution cost changes (spread, slippage, commission, fill delay, ambiguity
  policy)
- UI redesign or navigation restructuring
- Broker implementation of any kind
- External FX integration
- Production universe expansion beyond the bounded 1..100 limit
- New endpoints, schema changes, or migrations

If you believe one of these is needed, write the proposal into the completion
report and stop. Implementation needs its own approved task.

## 13. Reporting

Report outcomes exactly as they happened. If a test failed, show it. If a step
was skipped, say so. If a live run was not performed, do not imply that it was.
Never present synthetic or fixture data as real-market evidence, and never
present a bounded development run as production readiness.

## 14. Host process and operator DB ownership

### 14.1 Development Server Ownership

During Real Market Operator work, an AI agent never kills or restarts a
running Backend or Frontend process on its own judgment. This applies
regardless of how the process was started or by whom.

The standard host operator Backend is:

```text
host: 127.0.0.1
port: 8000
RUNTIME_PROFILE=real_market_operator
MARKET_DATA_PROVIDER=kiwoom
BROKER_PROVIDER=simulation
KIWOOM_MODE=market_data_only
```

Without an explicit user instruction in the current task, an agent must not:

- kill or restart the host Backend
- kill or restart the Frontend
- launch a separate long-running `uvicorn` of its own
- change the port arbitrarily
- start the Backend under the `default` profile when operator work is in
  progress
- start the Backend with the operator profile env vars incomplete or
  partially applied

If a code change needs a restart to take effect, the default response is to
report `RESTART REQUIRED` and stop. Restart only when the user explicitly asks
for it in the current task, and even then only as a single controlled action
(graceful stop of the one verified process, then a restart with the exact
documented command) - never a routine or repeated habit.

### 14.2 Operator DB Protection

`data/runtime/usb_real_market_review.sqlite3` is the real-market operator
truth database. Without an explicit user request in the current task, an
agent must not perform any of the following against it:

- running the scanner
- seeding
- synthetic inserts
- Research import
- creating or editing a `HumanDecisionRecord`
- running a simulation
- running a migration
- manual SQL mutation
- writing test data to it

Tests and any exploratory write must use a temporary or test database, never
this file. A sandboxed process sharing the host filesystem but running in an
isolated network namespace is not exempt: filesystem access alone is enough to
corrupt this database, so the same write prohibition applies regardless of
network isolation.

### 14.3 Sandbox Process Rule

Claude Code and Codex sandbox sessions must not leave a long-running Backend
process behind after a task completes. Even when a short-lived verification
process is genuinely needed, it must not open the operator DB
(`usb_real_market_review.sqlite3`) unless the user has explicitly requested
that in the current task; point it at a temporary/test database instead.

### 14.4 Frontend Build Artifact Isolation

The running Frontend development server and an AI validation production build
must never use the same Next.js `distDir`. The standard directories are:

- development runtime: `.next-dev`
- production validation build: `.next-build`
- production start: `.next-build`

Without explicit user permission in the current task, an agent must not:

- delete or clean the running development `distDir`, including `.next-dev`
- run a production build against the running development `distDir`
- change the artifact directory used by the running development server
- kill or restart the Frontend process

If a Frontend code or configuration change needs a restart to take effect,
report `FRONTEND RESTART REQUIRED`. Perform a controlled restart only after the
user explicitly requests it.
