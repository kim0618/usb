# Stage 10B-3.1 — Real Market Operator Runtime

## Runtime boundary

`RUNTIME_PROFILE=real_market_operator` is the explicit operator/review profile.
It selects the existing persisted database at
`data/runtime/usb_real_market_review.sqlite3` for application settings. The
profile also requires the frozen safe provider posture:

```text
MARKET_DATA_PROVIDER=kiwoom
BROKER_PROVIDER=simulation
KIWOOM_MODE=market_data_only
```

The profile does not enable a live smoke, scanner, or simulation opt-in. It does
not run a scanner or migration during Backend startup. `DATABASE_URL` continues
to own default/dev and synthetic UI-review sessions; the operator profile owns
its fixed real-market DB pointer so a stale default `DATABASE_URL` cannot split
the operator session.

## Start an operator review session

Start the Backend from the repository root:

```bash
RUNTIME_PROFILE=real_market_operator \
MARKET_DATA_PROVIDER=kiwoom \
BROKER_PROVIDER=simulation \
KIWOOM_MODE=market_data_only \
PYTHONPATH=backend .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Start the Frontend in a separate terminal:

```bash
cd frontend
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000 npm run dev
```

The Frontend never opens SQLite. It keeps using `/api/v1` through that Backend.
The default development DB and `usb_ui_review.sqlite3` workflow are unchanged.

For a future explicitly authorized real scanner run, use the same profile. The
scanner resolves its persistence target from the profile unless `--database` is
explicitly supplied:

```bash
RUNTIME_PROFILE=real_market_operator \
RUN_KIWOOM_REAL_SCANNER=1 \
MARKET_DATA_PROVIDER=kiwoom \
BROKER_PROVIDER=simulation \
KIWOOM_MODE=market_data_only \
PYTHONPATH=backend .venv/bin/python -m app.dev.run_real_scanner --limit 10 --persist
```

`RUN_KIWOOM_REAL_SCANNER`, `RUN_REAL_MARKET_SIMULATION`, and
`RUN_KIWOOM_LIVE_SMOKE` remain false-by-default, command-scoped opt-ins.

## Persisted truth verified on 2026-09-04

Read-only DB and API inspection found one completed `KIWOOM_REAL` ScannerRun for
trading date `2026-09-03`, with 8 candidates and 8 TOP8 rows. Rank order is
TSLA, SPCX, META, AVGO, NVDA, MU, AAPL, MSFT. GPT research and HumanDecision
counts are both 0, which is the expected human-pending state. No rows were
copied, seeded, merged, or generated.

## Schema audit

The real-market DB contains all current application tables and columns needed
by Scanner, Research, HumanDecision, Strategy, Execution, Shadow, and Runtime.
It is structurally compatible with the current ORM for this operator review.
It was created by `Base.metadata.create_all`, however, and has no
`alembic_version` table or revision row. Compared with the migration-managed
default DB at head `20260901_0008`, a few migration-added SQL defaults are also
absent even though the corresponding columns exist.

This stage deliberately does not stamp or migrate the existing real DB. The
risk is that a future migration cannot safely establish upgrade history from
this file as-is. Before any schema-changing stage, define and review a dedicated
adoption procedure (backup, exact schema comparison, then an explicit Alembic
stamp/migration decision). Do not run an ordinary upgrade against this DB until
that procedure exists.

## Safety boundary

- Kiwoom order and account endpoints remain absent from the allowlist.
- The Backend profile only selects persisted review truth; it does not contact
  Kiwoom on startup.
- Research import and HumanDecision writes remain operator actions through the
  existing API. Nothing is generated or decided automatically.
- SimBroker remains process-local and is outside this stage.
