# USB Frontend Operations Dashboard V1

## Purpose and stack

Stage 9.5 is a desktop-first operations UI for the Stage 9 `/api/v1` contract. It uses
Next.js 15, React, TypeScript, App Router, and Tailwind CSS. There is no frontend trading
logic, generated API client, global state library, chart framework, authentication, or
mock-data fallback. The Backend remains the source of truth for Quant, research evidence,
approval limits, risk, strategy, execution, and runtime transitions.

## Layout and routes

The shared shell has a collapsible sidebar, global header, Backend connectivity, broker
mode, market session, runtime mode, and persistent SAFE_MODE/HALTED banner.

| Route | Purpose |
|---|---|
| `/` | Dashboard and empty-safe operational overview |
| `/candidates` | Top8, Quant snapshot, prompt preview/copy |
| `/research` | Raw JSON import, GPT ranking, evidence, sources, decisions |
| `/trading` | Broker-neutral positions, strategy state, orders, fills, trades |
| `/shadow` | A–E comparison, C control, simulation warning and filters |
| `/runtime` | Runtime health, failures, reconciliation and dangerous controls |
| `/settings` | Version/capability allowlist, disabled Kiwoom placeholder |

## API and data policy

Set `NEXT_PUBLIC_API_BASE_URL`; development falls back to `http://127.0.0.1:8000`.
`lib/api.ts` owns fetch, JSON parsing, and the common `ApiError`. Components do not repeat
raw fetch code. A network failure displays Backend unavailable and never substitutes demo
data. Financial Decimal fields remain TypeScript `string`; helpers only format a copy for
display and do not use JavaScript arithmetic. Analytical scores remain numbers.

Backend timezone-aware timestamps are displayed in America/New_York for operational market
context. Screen clocks are display-only. Dashboard polls every 20 seconds; Runtime and
Trading every 10 seconds. The common hook prevents interval duplication within a request.
Manual refresh remains available.

## Operator workflows

The morning workflow is Candidates → copy prompt → manual ChatGPT research → paste raw JSON
in Research → inspect independent GPT/Quant ranks and evidence coverage → APPROVE/REJECT.
The UI does not pre-enforce the two-approval limit; it displays Backend 409 conflicts.
Evidence is described as source quality and core-claim coverage, never factual probability.

Trading shows the Backend broker mode without a PAPER/LIVE selector. A missing active
SimBroker is a normal empty state. Shadow clearly labels synthetic/simulation results as
implementation validation, keeps A–E ordering, and marks C as CONTROL.

Runtime resolve changes only the failure record. Safe Mode, Halt, Recovery, Reconciliation,
and Kill Switch are never optimistic; the UI refetches Backend truth. Kill Switch is inside
the Runtime danger zone and requires a reason plus an explicit liquidation-awareness
checkbox before sending `confirm: true`.

## Empty, error, and accessibility policy

Fresh databases show descriptive empty states. Null values render as an em dash. API errors
stay within the current screen with retry. Toasts report prompt copy, imports, decisions,
and runtime mutations. Tables are semantic and scroll on narrow screens. Buttons have focus
states and destructive actions include text, so meaning is not conveyed by color alone.

## Local development and build

```bash
PYTHONPATH=backend .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
cd frontend
npm install
npm run dev
```

Validation:

```bash
npm run lint
npm run typecheck
npm test
npm run build
```

## Security and future broker modes

This pre-Kiwoom V1 has no application authentication and must not be exposed directly to
the public Internet. Naver Cloud firewall/private access or reverse-proxy authentication is
required. No secret belongs in `NEXT_PUBLIC_*`. Kiwoom is displayed only as disabled; there
is no key form or adapter. When Backend capabilities later expose PAPER/LIVE, the same
broker-neutral Trading and Runtime screens can render those modes without redesigning the
workflow.
