# BTC Context UI V1: production deployment (memory-light), 2026-10-11

Verdict: **PRODUCTION_BTC_CONTEXT_UI_PASS**. Deployed source 757f768 (on top of 31b6f2d).
Window 18:13-18:38 KST. Only these changed: usb-frontend (one restart), nginx (reload) and the
new btc-context units. Evidence: `production_qa_20261011/` here; host logs in
`~/usb-btc-context-evidence/production-deploy-20261011/` (pre/post snapshot, observe, nginx, worker).

## A. Source

- local main = 757f768. origin/main = 31b6f2d. The push failed because HTTPS has no credentials,
  so the user runs `cd ~/usb && git push origin main`.
- The server repo was not pulled: it stays at db41f04. Everything was deployed from clean exports
  of 757f768.

## B. Frontend

- Built with `git archive 757f768 frontend` on node v20.20.1 / npm 10.8.2 (the same versions as the
  server). `.env.production.local` sets only `NEXT_PUBLIC_API_BASE_URL=https://usb.jptcalc.kr`,
  `NEXT_PUBLIC_CRYPTO_API_BASE=/crypto-api` and `NEXT_PUBLIC_BTC_CONTEXT_BASE=/btc-context-api`.
  The Market Context V1 panel env is unset.
- BUILD_ID `oYxgOEmwT_rWeGhyRnEP1` → **`_7ZVp-E2ahV720vAUb8yE`**. The tgz (cache excluded) has sha256
  `c6176f28…cf452`. 19 routes. `/btc-context-api` is inlined in the strip chunk `895.6c14774837efc13a.js`.
  That chunk has 0 banned words (Q1/Q2/Q3, LONG/SHORT candidate, research, sample, p-value,
  confidence) and 0 `MARKET_CONTEXT`/`8012` references.
- Restart: stop 18:14:00, start 18:14:08, `/crypto-paper` 200 at 18:14:41. PID 1672252 → 1700688,
  NRestarts 0. Rollback copy: `/root/usb/frontend/.next-build.pre-btcctx-20261011-181400`.
- The server `.env.production.local` got the same `NEXT_PUBLIC_BTC_CONTEXT_BASE` line. Backup:
  `/root/usb_runtime/frontend-rollback/env.production.local.pre-btcctx-20261011-181400`.
- Peak at restart: frontend cgroup 200 MB, avail 186 MB, PSI memory avg10 92.9 for under 30 s.
  It settled to avg10 0.15 / avail 202 MB within 2 minutes. crypto-paper max events stayed at 98.

## C. Release

- `deploy/btc_context_ui/stage_release.sh 757f768` built 54 files. tgz sha256 `a2cee004…23d13`.
  MANIFEST verified locally and on the host. Every file is identical to its `git show 757f768:` blob,
  and the file set matches RELEASE_FILES exactly.
- Loaded from the staged tree alone, the API, worker and `__main__` imports resolve no module from
  outside the release.
- Host layout: `/root/usb_runtime/btc_context_ui/{src,config(600 env),state(700),logs}`. src is owned
  by root and not group/other writable.

## D. Structure worker

- The manual first run (systemd, before the timer existed) returned `REBUILT` with 43,757 rows.
  elapsed 6.6 s (including the 30-day venue fetch), CPU 1.5 s, checkpoint 3.7 MB.
- An identical rebuild into a /dev/shm scratch dir (deleted afterwards) measured peak RSS 51.6 MB,
  3.7 s and CPU 1.4 s. Its snapshot was identical except for `written_ms`.
- Snapshot: BTCUSDT, R 83113.9 (+0.13%), S 82912.9 (-0.11%), repaint_violations 0, written 16 s after
  the bar close.
- Timer: `*:*:05`. The first fire at 18:19:05 returned `STEPPED 2`. Snapshot and checkpoint were
  replaced by rename (new inode). 13 runs in the 12-minute window: 0 failures, stray processes 0,
  step elapsed 0.5-1.8 s, cgroup peak seen 34-47 MB.

## E. API

- `usb-btc-context-ui.service` (MemoryHigh 72M / MemoryMax 96M) listens on 127.0.0.1:8014 only.
  `/health` returned 200 at 13 s.
- RSS 52.6 MB at start and 51 MB flat over 12 minutes. cgroup 42-48 MB.
- Routes are exactly `/health` and `/snapshot`. `/docs`, `/redoc`, `/openapi.json` and `/` return 404.
  POST/PUT/PATCH/DELETE return 405.
- Env holds only the 4 config keys plus systemd defaults: 0 secrets, 0 Binance credentials.

## F. nginx

- `location /btc-context-api/` (with `limit_except GET { deny all; }` and the same proxy headers as
  `/crypto-api/`) sits right after `/crypto-api/` in the 443 server block, so it inherits the
  server-level auth_basic. Backup: `/root/usb_runtime/releases/nginx-usb.jptcalc.kr.pre-btcctx-20261011-181942`.
- `nginx -t` passed and nginx was reloaded, not restarted (PID 707).
- Public path without credentials: GET 401 (control `/crypto-api/` also 401), POST 403.
- The basic-auth plaintext is not on the host any more, so the location itself was exercised in a
  throwaway nginx on 127.0.0.1:18914. It used the same block, byte for byte, without auth:
  - GET 200 (`/btc-context-api/health` → upstream `/health`, prefix stripped);
  - HEAD 405 from the API;
  - POST/PUT/PATCH/DELETE 403;
  - `/docs` 404.

  It was stopped by its PID and its directory removed.

## G. Browser QA (production data, same artifact)

Method: the same `_7ZVp…` artifact served locally (`next start`), behind a GET-only proxy. The proxy
sends `/crypto-api/` and `/btc-context-api/` through an ssh tunnel to the production 8100/8014,
refuses every non-GET with 405 and counts it. The browser aborts any other origin.

- Requests: 1,147 GET from the browser and 0 non-GET. The proxy saw 0 browser non-GET (the only one
  it logged was the manual curl check). 9 calls to the inlined `https://usb.jptcalc.kr/api/v1/dashboard`
  were aborted; they belong to the existing equity header and are unrelated.
- Values (1440): `R 0.13% / S 0.12` · `ASK STRONG / BID STRONG` · `BUY 93 / SELL 7` ·
  `OI ↑ 7.73B / FUND -0.0012% · BASIS -0.051%`. All four sections show live=1.
- Banned strings: 0 in the strip and 0 in the whole page body (390/1440/1600).
- Layout at 390/1440/1600:
  - order ticket shift 0 px on every axis (the strip was toggled in the same load);
  - horizontal overflow 0 (doc and body);
  - strip overflow 0, truncated text 0.
- HOT: a bounded probe held HOT (MAGICUSDT) for 15 s at 1440 and at 390. Context panel 0,
  `/btc-context-api` requests 0, chart canvas and ticket present. After returning to BTC the panel
  came back and polling resumed.
- Failure (browser-side routes only; production API untouched): abort, 500 and stale snapshot at
  1440 and 390. Every value shows `--`, all four dots turn grey (live=0) and the strip raises no
  alert. The LONG button state matched the normal run in every scenario ([False], caused by the
  position and gates, not by the strip). Overflow 0.

## H. Mutation proof (pre 18:13:25 / post 18:38)

These are identical before and after:
- BTC LIVE `LONG 0.108 @ 83913.1`, update_time 1791339778021, 20x CROSSED, ONE_WAY;
- fills max_id 8151740768. The count went 13 → 10, which is the userTrades period window dropping
  old fills; the newest id is the same;
- armed false / arm_count 0, client_armed false;
- exit-guard OFF / enabled=false;
- c1-auto enabled + has_position;
- REST trade_requests 0, errors 0;
- the paper ledger sha for main, c1-auto, ETH, SOL and STRK.

The **PAPER MAGICUSDT ledger changed** (1 → 5 lines): one MANUAL OPEN at 18:14:11
(`open-1791710051000-1657`). nginx shows it as `POST /crypto-api/api/crypto/order` from a Windows
Chrome 155 under basic-auth user `tjd618`. That is the operator's own browser, not this deployment:
this deployment's only non-GET on the host was the 18:20:17 nginx check POST from 127.0.0.1, which
got 403.

Deployment-caused order / CLOSE / leverage / AUTO / account mutations: **0**.

## I. Resources (12 minutes after deploy, every 60 s)

| | |
|---|---|
| API RSS | 51 MB flat |
| worker cgroup peak (sampled) | 34-47 MB |
| frontend | next-server 136 MB RSS, cgroup 153-171 MB |
| crypto-paper | 171-183 / 200 MB, **memory.events max 98 → 98, oom_kill 0** |
| q123 | 93-96 MB |
| context | 38-43 MB |
| available | 170-197 MB (before: 186-236) |
| swap | 383-394 MB (before: 439) |
| PSI memory avg300 | 1.3-2.5 |

Available memory is about 30-40 MB lower, which is the new API. crypto-paper pressure did not get
worse.

## J. Other services

PID / NRestarts unchanged:
- crypto-paper 1669431;
- context 1582777;
- q123 1684303;
- liqfwd 3250885;
- backend 1556760;
- e-rvol 2515148;
- nginx 707 (reload only).

Of the existing services, only usb-frontend changed: PID 1700688, NRestarts 0. The 9 existing usb
timers are unchanged.

## K. Rollback (not used)

Context Collector, Q123 and crypto-paper are not restarted in any of these steps.

1. Stop usb-frontend, move `.next-build` aside, move `.next-build.pre-btcctx-20261011-181400` back,
   then start usb-frontend.
2. Restore `.env.production.local` from its backup.
3. `systemctl disable --now usb-btc-context-ui.service usb-btc-context-structure.timer`.
4. Copy the nginx backup back, run `nginx -t`, then reload.
