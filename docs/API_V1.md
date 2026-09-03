# USB Backend API V1

## Purpose and architecture

Stage 9 exposes the existing scanner, research, execution, shadow, and runtime-safety
capabilities as a thin FastAPI control plane. The path is Frontend → router → API
query/application service → existing domain service/repository. Routers do not recalculate
Quant scores, evidence confidence, approval limits, risk sizing, strategy state, or runtime
transitions. The stable contract prefix is `/api/v1`; `/health`, `/docs`, and
`/openapi.json` remain unversioned infrastructure endpoints.

The API starts without running migrations, generating fixtures, scanning, replaying, or
starting trading. A missing active in-process SimBroker is represented by
`broker_mode: "SIMULATION"`, `availability: "NO_ACTIVE_SIM_BROKER"`, and empty live
positions/orders. No fake account or position is created at startup.

## Security and CORS

V1 Pre-Kiwoom has no application-level authentication. Never expose it directly to the
public Internet. Restrict access with the Naver Cloud firewall/private network or
reverse-proxy authentication, especially for decision, halt, recovery, safe-mode, and
kill-switch mutations. Revisit authentication before Kiwoom or a real account is added.

`CORS_ORIGINS` is a comma-separated configuration value. Development defaults are
`http://localhost:3000` and `http://127.0.0.1:3000`. Wildcard origin is not used and only
GET/POST/PUT are allowed. Kill switch requires `confirm: true`; all operator reasons are
non-empty and bounded. Failure resolution alone never restores NORMAL.

## Contract policies

- Success bodies are resource-oriented JSON without a generic wrapper.
- Errors use `{"error":{"code":"...","message":"...","details":...}}`.
- Request validation is 422, missing resource 404, malformed input 400, and workflow
  conflict 409. Stack traces and internal paths are never returned.
- Financial `Decimal` values are strings, preserving scale where stored. Analytical float
  scores remain JSON numbers.
- Datetimes are ISO 8601 timezone-aware strings. Dates are ISO 8601 dates.
- Enums are stable string values. Read ordering is explicit (rank/time/id/variant).
- Settings output is an allowlist; database URLs, credentials, keys, tokens, and `.env`
  content are never serialized.
- Replay smoke returns HTTP 200 with `available: false` when its report is missing or
  unreadable. It returns only summary fields, never the complete large report.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Process health |
| GET | `/api/v1/dashboard` | Null-safe dashboard composition |
| GET | `/api/v1/market/status` | XNYS calendar/session status |
| GET | `/api/v1/scanner/latest` | Latest completed scanner snapshot |
| GET | `/api/v1/scanner/runs` | Scanner history |
| GET | `/api/v1/scanner/runs/{run_id}` | Full stored candidate pool |
| GET | `/api/v1/research/prompt` | Stored Top-8 manual GPT prompt |
| GET | `/api/v1/research/prompt/{symbol}` | Stock detail prompt |
| POST | `/api/v1/research/import` | Atomic raw GPT JSON import |
| GET | `/api/v1/research/latest` | Latest imported analysis |
| GET | `/api/v1/research/{analysis_id}/candidates/{symbol}` | Candidate, evidence, decision, Quant reference |
| PUT | `/api/v1/research/{analysis_id}/decisions/{symbol}` | APPROVE/REJECT through existing service |
| GET | `/api/v1/trading` | Simulation availability and strategy states |
| GET | `/api/v1/trading/positions/{symbol}` | Persisted strategy/position reference |
| GET | `/api/v1/trading/orders` | Persisted execution orders |
| GET | `/api/v1/trading/fills` | Persisted fills and costs |
| GET | `/api/v1/trading/trades` | Persisted simulation/shadow trades |
| GET | `/api/v1/shadow/summary` | Deterministic A–E summary; C control; optional inclusive period |
| GET | `/api/v1/shadow/trades` | Filtered shadow results |
| GET | `/api/v1/replay-smoke/latest` | Bounded runtime report summary |
| GET | `/api/v1/runtime` | Persisted runtime health/status |
| GET | `/api/v1/runtime/failures` | Filtered failure history |
| POST | `/api/v1/runtime/failures/{id}/resolve` | Resolve through runtime service |
| POST | `/api/v1/runtime/safe-mode` | Manual SAFE_MODE transition |
| POST | `/api/v1/runtime/halt` | Manual HALTED transition |
| POST | `/api/v1/runtime/recover` | Acknowledged, reconciled recovery |
| POST | `/api/v1/runtime/reconcile` | Current persisted execution-state reconciliation |
| POST | `/api/v1/runtime/kill-switch` | Confirmed halt; liquidation only when broker runtime exists |
| GET | `/api/v1/settings` | Version/environment allowlist |
| GET | `/api/v1/capabilities` | Actual implemented adapter capabilities |

## Important requests

Research import preserves the exact raw string:

```json
{"raw_json":"{\"schema_version\":\"gpt_research_v0\", ...}"}
```

Decision mutation:

```json
{"decision":"APPROVE","note":"operator review complete"}
```

Runtime mutation and kill switch:

```json
{"reason":"manual operator request"}
```

```json
{"confirm":true,"reason":"manual emergency stop"}
```

Shadow summary is backward-compatible: without query parameters it returns the existing
all-time response unchanged. `start_date` and `end_date` are optional ISO `YYYY-MM-DD`
inclusive bounds. Closed performance uses the `exit_at` date in `America/New_York`;
non-closed paths such as `NO_TRADE` use their `ScannerRun.trading_date`, keeping
`candidate_paths` and `no_trade` on the same period grain. Undated legacy paths cannot be
assigned to a bounded period and are excluded only when a period parameter is present.

```text
GET /api/v1/shadow/summary?start_date=2026-08-05&end_date=2026-09-03
```

An inverted range returns the common `400 INVALID_REQUEST` contract. Invalid date syntax
returns `422 REQUEST_VALIDATION_ERROR`.

## Frontend connection

Run from the project root:

```bash
PYTHONPATH=backend .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Use `http://127.0.0.1:8000/docs` while developing the frontend contract. The frontend can
follow Scanner → prompt/import → Human decision → Trading/Shadow → Runtime Safety using
only this API. There is no Kiwoom, external market API, paper/live broker, GPT API call,
authentication/login, or frontend implementation in Stage 9.
