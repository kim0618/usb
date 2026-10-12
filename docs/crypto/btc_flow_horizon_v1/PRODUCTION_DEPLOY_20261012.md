# Production apply: Manual Trade Recorder V1 + BTC Wall/Flow Horizon UI (2026-10-12)

Window 14:35-14:54 KST. Source `8f3dfa2` (local main == origin/main). Entry Advisor not deployed.
Host evidence: `~/usb-btc-context-evidence/production-deploy-20261012/` (pre, post, installs,
frontend sampling, recorder cycles, event timing). Browser QA: `production_qa_20261012/`.

## Verdicts
- A. Manual Trade Recorder: **PRODUCTION_MANUAL_TRADE_RECORDER_PASS**
- B. Wall + Flow UI: **PRODUCTION_WALL_FLOW_UI_PASS**
- Overall: functionality, mutation, request safety PASS. **One criterion not met strictly: existing
  service impact.** The frontend restart drove the host into memory thrash (PSI some avg10 up to 99.9
  for ~90 s) and crypto-paper's own `memory.events max` rose 1984 -> 2248 in that window (oom 0,
  no restart). See section F.

## A. Recorder
- Release `manual-trade-research-8f3dfa2.tgz` (sha256 f8b95f86..., 15 files, MANIFEST OK),
  `/root/usb_runtime/manual_trade_research/{src -> releases/..., config (600 env), store (700), logs}`.
  Owner fixed to root (tar carried the local uid).
- Env on host: `MTR_TRIGGER_URL=` (empty); every run logged `"trigger": null`, `"s1_error": null`.
- 3 manual runs + timer `*:*:20`: 12 runs logged OK, backfill 3,715 snapshots done by 14:47
  (500 per run, 12 public requests per run), steady state 0 writes / 0 requests.
  One run (14:39:20) was killed by `TimeoutStartSec=120` during the frontend restart thrash; it
  left nothing half written: store events 47 / snapshots 2,731 / episodes 19, duplicates 0,
  unparseable 0. Open episode = the LIVE LONG recovered from fills (no synthetic OPEN).
- 429/418 lines in the recorder log: 0. S1 mtime unchanged (no refresh triggered).

## B. BTC context API + flow worker
- Release `btc-context-ui-8f3dfa2.tgz` (sha256 b2b72e6b..., 60 files, MANIFEST OK). Old src kept as
  `src.pre-flow-20261012-143807`; env backup `config/btc_context_ui.env.pre-flow-*`; unit backup in
  `/root/usb_runtime/releases/`. Structure fingerprint files unchanged (no rebuild; STEPPED as before).
- Flow worker: first run 1,440 minutes fetched, 0.9 s, coverage 100 % on all four; timer `*:*:10`,
  11 runs 0 failures, 0.26-0.38 s, one klines row per run.
- API restarted only (PID 1701557 -> 1826565), health 8 s, RSS 52.7 MB then 29.5 MB.
  `/snapshot` keeps every V1/V3 key and adds `flow_horizons`. `/entry-analysis`, `/docs`,
  `/openapi.json` 404; POST 405.

## C. Frontend
- Built from `git archive 8f3dfa2 frontend` with node v20.20.1 / npm 10.8.2 (server versions),
  `.env.production.local` = API base, `/crypto-api`, `/btc-context-api` only.
  BUILD_ID `_7ZVp-E2ahV720vAUb8yE` -> **`fsRtGYt770MY7GQwENQha`** (tgz 6846946...). Rollback:
  `/root/usb/frontend/.next-build.pre-flow-20261012-143915`. package-lock identical to the server's.
- Bundle: `최근 5분`, `매도벽`, `매수벽`, `8012`, `MARKET_CONTEXT` 0. Entry advisor code is present but
  gated on `process.env.NEXT_PUBLIC_ENTRY_ANALYSIS_PREVIEW`, which is empty in the browser; DOM check
  found no `entry-analysis` element; the API route is 404 as well.
- Restart 14:39:44 -> 200 at 14:42:25 (2 min 40 s; 33 s on 10/11).

## D. Production-data QA (same artifact, GET-only caching proxy over an ssh tunnel)
- API JSON == UI on all four horizons at 1440 first load, after reload, 390 and 1600
  (e.g. 10분 매수 66% / 1시간 매도 51% / 4시간 매수 53% / 1일 매수 53%).
- Values on screen 2.95 s after navigation, 0.51 s after reload (no warm-up).
- HOT: strip absent, 0 btc-context requests in 15 s, no context lines; back on BTC values in 7 ms.
- 390 / 1440 / 1600: ticket shift 0 (details open, strip hidden), overflow 0 (doc and body), chart
  box / visible bars / price span unchanged, wall tag overlap 0.
- Proxy: 8100 upstream 27 GETs (once per URL), `/binance` refused 6, browser non-GET 0.

## E. Mutation and request safety (pre 14:35:55 / post 14:48:10)
Identical: BTC LIVE `LONG 0.108 @ 83913.1` (update 1791339778021, 20x CROSSED ONE_WAY), S1 max fill id
8151740768, armed false / arm_count 0, exit-guard OFF, c1-auto enabled + has_position,
trade_requests 0, mirror ORDER_INTENT 110 / ORDER_SENT 54, all six paper ledger sha.
`rest.signed_requests` 24128 -> 24135 (+7) = one TTL RECONCILE at 14:48:13, which is the post
snapshot's own `/account` read; nginx shows 0 browser `binance` calls in the window.
`rate_limited` 116 -> 116 (429/418 during deploy: 0). Recorder/flow/UI signed requests: 0.
Deployment-caused order / CLOSE / leverage / AUTO / account mutation: 0.

## F. Existing services and memory
PID / NRestarts unchanged: crypto-paper 1669431, context 1582777, q123 1684303, liqfwd 3250885,
backend 1556760, e-rvol 2515148, nginx 707. Restarted by design: usb-frontend, usb-btc-context-ui.

| | pre 14:35 | during restart | post 14:48 | 14:53 |
|---|---|---|---|---|
| crypto-paper memory.events max | 1984 | 2248 (14:41) | 2253 | 2659 |
| crypto-paper oom_kill | 0 | 0 | 0 | 0 |
| crypto-paper MemoryCurrent | 199.8 MB | 176-198 MB | 209.4 MB | 190 MB |
| available | 246 MB | 121-237 MB | 201 MB | 225-229 MB |
| PSI memory some avg10 / avg300 | 0.00 / 0.11 | up to 99.97 | 1.56 / 24.76 | 0.1-1.5 / 16.5 |

Before the deploy the counter already rose 177 (08:52) -> 1984 (14:30), about 5 a minute on average.
The restart window added ~260 at once. A further +406 came between 14:48 and 14:51 with no deploy
step running; a 1 s sample from 14:51:41 to 14:53:56, covering two runs each of the structure, flow
and recorder timers, saw 0 increments, so the new timers are not what drives it. crypto-paper's
pressure is its own cgroup at its 200 MB limit (pre-existing item: C1 runtime rescan, chart cache).

## G. Rollback (not executed)
- Recorder: `systemctl disable --now usb-crypto-manual-research.timer` (store kept).
- Flow: `systemctl disable --now usb-btc-context-flow.timer`; API back to `src.pre-flow-*` + unit backup.
- UI: swap `.next-build.pre-flow-20261012-143915` back and restart usb-frontend (this repeats the
  restart memory spike).
