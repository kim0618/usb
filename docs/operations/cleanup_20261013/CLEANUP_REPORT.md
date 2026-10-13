# US-B Production Hygiene & Cleanup V1 (2026-10-13)

Verdict: **CLEANUP_PARTIAL_UNKNOWN_REMAINS**

Scope: host `trader-j` (961 MB RAM, 2 GB swap, 9.8 GB disk). Read-only audit plus 10 approved deletions.
Trade mutation 0. No service restarted, no unit or timer changed. Manifests: `DELETE_MANIFEST_BEFORE.txt`,
`DELETE_MANIFEST_AFTER.txt` in this directory.

## 0. Host state during the audit

| time | load | PSI memory some/full avg10 | avail | swap used |
|---|---|---|---|---|
| 10:18 (start) | 21.9 | 96.9 / 77.7 | 214 MB | 733 MB |
| 10:30 (end) | 2.6 | 2.4 / n.a. | 207 MB | 699 MB |

The 10:15 to 10:25 stall was not caused by this audit and not by a single identified trigger. Evidence:
`usb-crypto-liqfwd-daily` took 3 m 52 s today versus 17 s and 22 s on 10/11 and 10/12 with the same CPU time (2.4 s),
so it was a victim. `usb-btc-context-structure` hit its 120 s start timeout once at 10:18 (Failed 'timeout', next run
succeeded). `fwupd-refresh` ran 4 s at 10:10 and is not a plausible cause. Kernel log: no OOM, no hung task.
Chronic baseline from the sampler log (20 days, 10 s samples): daily avail average 355 MB (9/24) to 224 MB (10/13),
daily swap max 569 MB to 793 MB. The host is at capacity and drifts toward less headroom.

## A. Units (systemctl, 10/13 10:30)

| unit | state | PID | NRestarts | memory / cap | role |
|---|---|---|---|---|---|
| usb-crypto-paper | active | 1669431 | 0 | 199 MB / 200 MB | LIVE+PAPER terminal :8100, Binance LIVE env, HOT/multisymbol |
| usb-crypto-context | active | 1582777 | 0 | 61 MB / 200 MB | context collector, journal 237 MB |
| usb-crypto-q123-forward | active | 1684303 | 0 | 23 MB / 180 MB | Q1/Q2/Q3 forward recorder |
| usb-btc-context-ui | active | 1826565 | 0 | 9 MB / 96 MB | strip API :8014 |
| usb-crypto-liqfwd | active | 3250885 | 0 | 22 MB / 100 MB | liquidation forward collector |
| usb-e-rvol | active | 2515148 | 0 | 16 MB / 250 MB | Kiwoom RVOL worker |
| usb-backend | active | 1556760 | 0 | 34 MB / none | A/E authority API :8000 |
| usb-frontend | active | 1826838 | 0 | 32 MB / none | Next.js :3000, `.next-build` |
| nginx | active | 707 | n.a. | 7 MB | proxy, jptcalc shares host |

Timers (KST): btc-context-flow 60 s, btc-context-structure ~60 s, manual-research 60 s, feedlog 60 s,
liqfwd-daily 10:15, context-retention 04:40 (success 10/12, 10/13), morning-scan 07:00, e-universe 13:20,
grouped-daily 13:40, splits-refresh 13:55, h-forward 14:10, e-paper 16:45. All last Result=success at 10:30.
`usb-e-rt2.service`: disabled, never started this boot (see L).

## B. KEEP_ACTIVE

- `/root/usb_runtime/strategy_e_paper/src` (104 MB) as a whole. It is the production A/E tree, not a code copy:
  grouped-daily and splits-refresh run with `--repo` pointing here, A's scan imports from `src/backend`, and
  `src/data/runtime/{strategy_c/raw, strategy_a_mover_live, ops}` are written daily (files from 10/12 13:55 and 22:25).
  `/root/usb` is a mirror of that data (identical name, size, mtime listings).
- `/root/usb_runtime/{crypto_paper/run, crypto_paper/data, crypto_paper/symbols, crypto_context/journal,
  q123_forward/{store,runtime}, manual_trade_research/store, btc_context_ui/{state,flow,config}, crypto_liqfwd/data,
  strategy_e_rvol, strategy_e_paper/{run,universe}, strategy_e_universe, workspace/1_US-B}`.
- `/root/usb/{.venv, frontend/node_modules, frontend/.next-build, data/runtime, .env*}`.

## C. KEEP_REFERENCE

- `crypto_paper/run/paper-server-001-*` per-symbol ledgers (MAGIC, STRK, ETH, SOL, c1-auto).
- `/root/usb/data/runtime/strategy_b_e0` (58 MB) and its twin in the e-paper tree. B is closed but see L.
- `/root/usb/data/research/{crypto,expert_execution}` (AOA, crypto research results).
- `strategy_b_e1/regular_ranking_probe_20260922`, `strategy_e_max_rt2/run` and `universe_2026-09-22.json`.
- `/root/h_deploy_20261010-184543` (H forward deploy manifests, 144 KB), `backups/ae_v1_20260927`.

## D. KEEP_ROLLBACK

- Frontend: `.next-build.pre-flow-20261012-143915` (BUILD_ID `_7ZVp-E2...`), env backup
  `frontend-rollback/env.production.local.pre-btcctx-20261011-181400`.
- btc_context_ui: `src.pre-flow-20261012-143807`, `releases/usb-btc-context-ui.service.pre-flow-20261012-143807`.
- crypto_paper/releases (`before-hot-v1`, `before-subdollar`, `before-seed-fix`, `before-segment-compression`),
  crypto_context/releases (`before-active-session-retention-fix-20261011`, f3806e7), q123 releases, liqfwd releases.
  Rollback ordering for these backend releases was not proven, so they are kept whole (3.9 MB).

## E. Processes terminated

| PID | what | proof |
|---|---|---|
| 2279157 | `bash -c 'while true; ... >> /tmp/crypto_mem.log; sleep 10'`, started 9/23 14:03, ppid 1, session-3466.scope | fd only /dev/null; no unit, cron, logrotate, env, tracked-source or deployed-source reference (server and local repo); no reader. Killed by PID after cmdline, ppid, start time matched. |

No other process was touched. Listening ports (22, 80, 443, 3000, 8000, 8014, 8100, 111, resolved) all map to
known units; no preview, `next dev`, QA uvicorn, replay feeder or tunnel helper exists.

## F. Files deleted

`/tmp/crypto_mem.log` (4.8 MB) and 6 stale frontend build generations plus 2 rollback tgz (about 247 MB),
detailed in the manifests. Five-proof detectors had positive controls (the same fd/env/grep scans hit the live
`.next-build`, `NEXT_DIST_DIR` and `package.json`).

## G. RAM freed

About 3 MB (the sampler bash and its sleep). Honest assessment: the cleanup scope contained no meaningful RAM.
Services are at their working set; see O for the decisions that would actually move RAM.

## H. Disk freed

+252,632 KB measured on `/` (1.88 GB to 2.12 GB free, 80% to 78%).

## I. Swap

733 MB (10:18) to 699 MB (10:30). This is load-driven drift after the stall, not an effect of the deletions.
Per-process swap: usb-backend 88 MB, next-server 84 MB, crypto-paper 57 MB, q123 51 MB, e-rvol 48 MB.

## J. Obsolete units / timers

None removed. `usb-e-rt2.service` (disabled, 9/22 capacity dry run, referenced by no timer, repo file or env) is the
only obsolete unit. `usb-crypto-feedlog` is documented in `CRYPTO_SERVER_RUNBOOK_V1.md` as an intended operations
monitor, so it was kept.

## K. Rules changed

`AGENTS.md` section 14, new subsection "운영 호스트 조회와 정리": no `find /`, no broad `du`, no root recursive grep,
known paths with `--max-depth`, idle IO class, PSI check first, five-proof, judge by unit `--repo`/WorkingDirectory
not directory names, kill by PID only, before/after manifests.

## L. UNKNOWN / needs the user

1. `strategy_e_max_rt2/src` (11 MB): only the disabled `usb-e-rt2` references it, but it is a file copy, not a git
   checkout, so regenerability is unproven. Delete together with the unit if the 9/22 dry run needs no re-run.
2. `strategy_b_e0` (58 MB) and `strategy_c` (18 MB) exist in two trees with identical metadata and different inodes.
   The A doc says "mirrored by hardlink", yet `nlink=1` on sampled files. Which tree the live scan reads, and what
   keeps them in sync, is not proven. Nothing deleted.
3. `strategy_c/raw` is A's live input despite the name. Anyone cleaning by name would break A's scan.
4. Unpushed/dirty state: local `~/usb` has 304 dirty paths from other sessions. Nothing was committed or staged.
   `AGENTS.md` and this directory are new/modified and uncommitted.

## M. Risky data not deleted

All ledgers, `crypto_context/journal` (retention v1.1 left alone, ran 04:40 success), Q123 store, Manual Recorder
store, liqfwd raw/normalized, strategy raw data, mover live raw, grouped daily/splits.

## N. Operations

All nine core units active, NRestarts 0, OOM 0 in the last 6 hours, all timers last Result=success. One transient:
`usb-btc-context-structure` start timeout at 10:18 during the stall. Mutation 0.

## O. Next candidates (decisions, not done)

1. RAM, US-B: `usb-crypto-paper` runs at 199/200 MB (cap pegged) and was settled as MONITOR_ONLY on 10/12 with the
   rule "re-open on delay or swap spike". Today's 10-minute stall plus one timeout is a delay event under that rule.
   Re-evaluating means a cap change or the undeployed 8551d5b, which are both user approvals.
2. RAM, timers: three 60 s oneshots spawn a Python each minute (flow, manual-research, feedlog). Moving feedlog to
   5 min is the cheapest cut; flow and manual-research are product features.
3. RAM, OS daemons not part of US-B, about 65 MB RSS in total: multipathd 27 MB, fwupd 10 MB, udisksd 7 MB,
   upowerd 6 MB, ModemManager 5 MB, unattended-upgrades 5 MB, networkd-dispatcher 5 MB. Disabling is outside this
   approval; the nbpmon agent (23 MB) is platform-managed.
4. Disk: the e-rt2 copy (11 MB), duplicate B/C data (76 MB if one tree is proven redundant), `/tmp` one-off files
   (about 1 MB, includes evidence-like `g1g7_manifest.txt`, `c1_*.json`).
