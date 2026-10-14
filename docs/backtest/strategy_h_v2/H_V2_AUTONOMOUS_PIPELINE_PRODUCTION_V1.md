# Strategy H-V2: Autonomous Pipeline Production Deployment V1

Date: 2026-10-14 KST (13:08 to 14:50). Host: trader-j. Operator approval: COMMIT + PRODUCTION DEPLOY.

## 1. Verdict

```text
H-V2 AUTONOMOUS PIPELINE PRODUCTION = DEGRADED
  deployed and ACTIVE : backend (h_auto, queue, transitions, runner, API), pinned H data, H GATE in the
                        01:10 ET update, research queue, paper/NAV, H UI, futures paper position size
  NOT ACTIVE          : usb-h-research (timer DISABLED after the first run thrashed the host)
  NOT AUTHORIZED      : no authorization.json (server Claude CLI login invalid, 401)
  live Opus calls     = 0, spend $0.00
```

FG and VRRM were not researched. Their queue items are QUEUED, untouched. Decisions are unchanged: 0/6/2.

## 2. Commit state

* Local `main` = `origin/main` = `1719b2c` (ahead 0, behind 0). `2b1285d`, `1719b2c`, `04428f1` and
  `8b86bcb` are all in origin.
* H, H frontend and futures paper position size had **no uncommitted file**. The 151 dirty or untracked
  paths in the shared tree are backtest work of other sessions (not touched). No new commit before
  the deploy (no empty commit).
* Clean-tree check: `git archive HEAD` imports every H module; `tests/strategy_h_v2/forward` 167 passed,
  4 skipped. All 167 `app.*` modules the H runner, executor and API load (including lazy imports of the
  D3/D4/D5/D6 runners) are byte-identical on the server except the 18 files deployed here.

## 3. What was deployed

Server git HEAD stays `db41f04`; files are copied over it as before.

* backend (22 files, all byte-identical to `1719b2c`): `app/strategies/h_auto/*.py` (14),
  `h_forward/research_queue.py`, `h_forward/transitions.py`, `dev/run_h_v2_d7.py`, `api/strategies.py`,
  `docs/operations/h_autonomous_pipeline_v1.json` + `.sha256`, units `usb-h-research.service/.timer`.
  The four modified server files were byte-identical to `2b1285d^` beforehand, so only H hunks changed.
  `api/strategies.py` gains one field (`autonomy`) on `GET /strategies/STRATEGY_H_V2/paper`; no A/E hunk.
* data into `d7/auto/` (H only): `price_base` (content sha `1451d76a…`), `price_history` (476 sessions
  2024-09-16..2026-08-10), `reference_history` (8 quarterly CS). Rebuilt locally from `1719b2c` and
  byte-compared with the completion artefacts (identical). Server `verify-price-history` = OK 476.
  Combined price availability = 520 sessions (A grouped 44 + H history 476).
* `usb-backend` restarted once (13:22 KST, PID 2069055). A and E API payloads, E paper state, A DB row
  counts: identical before and after (E `status` differs only by E's own 13:20 universe build).
* frontend: built locally from `git archive 3e2a8ad` (the running build's base) plus the frontend diffs
  of `5093f6c`, `1c6c900`, `04428f1`, `2b1285d` only, node v20.20.1, production `NEXT_PUBLIC_*`.
  `88ceb58` (dead preview removal), `5504863`, `4be1d9d` were **not** included, so `/liquidity-preview`
  and the terminal's preview code stay exactly as in production. BUILD_ID `w3LLcmPdetY4iarG7TXCX` ->
  `Rh0hJ2skzuYNTyehDWSI-`. vitest (H, position size, polling, dashboard card) 43 passed.

Backup `/root/h_auto_backup_pre_deploy_20261014-130816.tar.gz` (sha `880c476c…`, 312 entries).
Frontend rollback `/root/usb/frontend/.next-build.pre-hauto-20261014-1354`. Evidence
`/root/h_auto_deploy_20261014-130816/` (baselines before/afterrestart/after, smoke JSON, journal).

## 4. Production readiness (real sources)

| Item | Value |
|---|---|
| H reference | `auto/reference/CS_2026-10-13` (H-owned, A's fetcher, 6 pages, 5,320 rows) |
| active CS with CIK | 5,310 |
| A reference (read) | `CS_2026-10-01`, 12 days old (A's manual snapshot) |
| latest price session | 2026-10-13 (A grouped 13:40 KST) |
| GATE at 01:10 ET | READY, reasons none; DATA_NOT_READY from old H research sources = 0 |
| SCAN (first run) | universe 5,320, identified 4,154, market pass 3,127, then killed (section 6) |
| companyfacts | on demand per CIK, `auto/sec/2026-10-14`; 21 requests, 21 x 200, 0 x 429, 0 x 5xx, 0 retry |

MethodJudgement and the queue-slot rule were not reached in production (the scan was stopped in its
first batch). Locally: AUTO_READY only enters the queue, not-ready rows cannot occupy open slots.

## 5. First unattended forward run on the new code (01:10 ET = 14:10 KST)

`usb-h-forward` exit 0, 5 min 20 s, peak 157 MB. Steps: prices PASS (2026-10-13 written, request upper
bound 2026-10-13, no_result 0, failed 0), SEC submissions 8 x 200, intake GATE READY, queue OK
(FG and VRRM QUEUED, AEYE EVIDENCE_ALREADY_IN_THESIS), paper OK (1 NAV event, 0 positions, cash $10,000).
Massive future-date 403 in the journal: 0 (1 the day before). Forward verify PASS (8 rows), paper verify
PASS (1 stored = 1 recomputed). 1D 8/8, 5D 8/8, 21D 0/8 (2026-11-02), 63D 0/8 (2027-01-04).

## 6. First research run (01:40 ET = 14:40 KST): stopped

No authorization, so the run could only scan. Within six minutes the research cgroup grew from 79 MB
to 252 MB (peak) and the host went into swap thrash: memory PSI full avg10 70-96%, `usb-backend`
answered in 20-26 s, the crypto terminal (8100) did not answer within 30 s. Under the operator rule
(severe swap thrash, backend instability) the unit was stopped at 14:46:34 and
`usb-h-research.timer` disabled. No OOM kill anywhere, no service restart, no queue item RUNNING,
queue/forward/paper verify PASS afterwards, backend back to 0.05 s and crypto to 0.1 s.

The 650M `MemoryMax` protected nothing here: the host has 961 MB with about 150-200 MB available, so
the scan pushed the trading services into swap long before its own cap. The local 307 MB peak is
therefore not affordable on trader-j as it stands.

The frontend restart (13:54) also thrashed the host for about 15 minutes (ready after 6.5 min, PSI
back to 0.4 at 14:09), the known pattern; no OOM, no restart.

## 7. Model contract and authorization

* `/usr/bin/claude` 2.1.76 exists; `/root/.claude/.credentials.json` holds an OAuth token that expired
  2026-03-15. A one-word probe through the runner's own `call_opus` hung to its 300 s timeout; a direct
  call returned `401 OAuth access token is invalid`, cost $0, `modelUsage` empty.
* So the canonical model could not be confirmed. Authorization was **not** created
  (`auto-authorize` never run). Requested model in contract = `claude-opus-5-5`, fallback none.

## 8. Isolation

| Check | Result |
|---|---|
| A account/positions/trades/equity API | identical (equity $7,509.40, 0 positions) |
| E account/positions/trades/equity API, paper_state sha | identical ($10,000, 0 positions) |
| A paper DB row counts, `ae_official_paper_v1.json` | identical |
| C store (`strategy_c/raw`) | +`grouped/2026-10-13` (A grouped-daily 13:40) and +`splits_2026-10-07_2026-10-28` (A splits 13:55-14:00); nothing written by H |
| H forward ledger, launch snapshot | unchanged |
| H paper | $10,000 initial, cash $10,000, 0 positions, 0 pending, 0 trades, realized 0, unrealized 0 |
| WATCH / REJECT entries | 0 / 0 |

## 9. UI (headless, production build through ssh tunnels, writes refused, Binance refused)

* `/trading-h`: "Strategy H · Paper + Forward Shadow", 현재 자산 $10,000.00, 현금, 실현/미실현,
  거래 수 0, 관찰 8 / WATCH 6 / REJECT 2 / APPROVE 0, 자동 운영 panel (마지막 자동 실행 14:14, 최신
  가격 세션 2026-10-13, SEC 갱신 14:11, 리서치 큐 실행 14:14, 다음 자동 실행, queue 대기 2, 승인 전).
  The panel has no separate "research last run" field (the committed UI shows the queue run).
* `/dashboard`, `/strategy-h`, `/strategy-compare`: render, 0 page errors, desktop and mobile.
* `/crypto-paper` PAPER BTCUSDT SHORT 0.064 BTC: 포지션 규모 7,163,721원 / 5,342.07 USDT
  (= 0.064 x mark 83,470.0), desktop and mobile. LONG and no-position states covered by the 10 unit tests;
  LIVE not rendered (no signed Binance reads from QA).
* Desktop width overflow (header row 1,471 px at 1,440) predates this build: no layout file changed since 3e2a8ad.

## 10. Current production state

```text
usb-h-forward.timer   = ENABLED, Mon..Fri 01:10 ET (unit unchanged), last run 14:10 KST success
usb-h-research.timer  = DISABLED (installed, not scheduled)
authorization.json    = ABSENT
research queue        = FG QUEUED (P0), VRRM QUEUED (P1), AEYE EVIDENCE_ALREADY_IN_THESIS
```

## 11. Blockers (operator)

1. **RAM**: the research scan cannot run on the 961 MB host without starving A/E/crypto. Options: the
   2 GB upgrade already recommended, or a smaller scan (fewer CIKs per day, run outside crypto load).
   Changing the scan is a code change, not done here.
2. **Claude CLI login on trader-j** (`claude` interactive login as root, or another credential the CLI
   accepts). Then re-probe, then `auto-authorize --cap 24 --by user-approved-2026-10-14`.
3. Then enable `usb-h-research.timer` (or run it once under watch) for FG then VRRM.

Rollback (not needed so far): restore the backup tar, restart `usb-backend`; frontend `mv .next-build
.next-build.hauto && mv .next-build.pre-hauto-20261014-1354 .next-build`, restart `usb-frontend`.
