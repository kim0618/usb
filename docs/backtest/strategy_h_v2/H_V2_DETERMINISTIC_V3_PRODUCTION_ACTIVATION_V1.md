# H-V2 Deterministic V3 Production Activation V1 (result)

Date: 2026-10-15 KST, operator-approved production deploy. Not a strategy change: the V3 contract
(`docs/operations/h_d3d4_deterministic_v3.json`, PASS/FROZEN) was deployed as committed; no rule was
changed during or after the deploy. Not pushed.

Verdict: **H-V2 DETERMINISTIC V3 PRODUCTION = DEGRADED** (runtime deployed, manual acceptance run
functionally PASS, evaluation timer kept **DISABLED** because the 1 GB host showed memory/IO pressure and
crypto terminal latency spikes during the run). Strategy = PASS/FROZEN; operations = NEEDS OPTIMIZATION.
These are separate verdicts.

## 1. What was deployed

* Source: committed tree at local `16e7597` (V3 files from `181794a` / `5302fb9` / `2bddbaa` / `7ccdd0c`;
  the H files did not change after `7ccdd0c`). Server git HEAD stays `db41f04`; files copied over it as in
  every previous H deploy.
* Pre-check: all 15 H files on the server were byte-identical to `1719b2c` (the last H deploy), so the copy
  overwrote nothing else.
* 31 files: `backend/app/dev/run_h_v2_d7.py`; `h_auto/{business_evidence, clock, contract, deterministic,
  deterministic_v2, deterministic_v3, executor, facts_cache, guidance_parser, guidance_parser_v2, inputs,
  intake, no_ai_guard, orchestrator, queue, store}.py`; `docs/operations/h_autonomous_pipeline_v2.{json,sha256}`,
  `h_d3d4_deterministic_v{1,2,3}.*`; `deploy/systemd/usb-h-evaluation.{service,timer}`. SHA256 verified
  (0 mismatches), owner root, all three contract checksums load, executor = `deterministic_v3`.
* Removed (moved to `/root/h_v3_deploy_20261015/removed/`): `h_auto/budget.py`, `h_auto/providers.py`,
  `/etc/systemd/system/usb-h-research.{service,timer}` (timer was already disabled).
* Installed: `usb-h-evaluation.{service,timer}` (ExecStart 1 `intake-scan`, ExecStart 2
  `auto-evaluate --skip-scan`, both under `/run/usb-h-forward.lock`, MemoryMax 400M, Nice 10); timer disabled.
* Frontend: **not rebuilt**. The H page diff since the deployed build is labels only ("자동 운영" -> "자동 평가",
  "리서치 큐" -> "후보 큐"); the deployed page renders the new API (missing budget fields show "-"). Shared
  frontend files also carry another session's crypto change, and a frontend rebuild thrashed this host on
  2026-10-14, so the label change waits for the next coordinated frontend build.
* `usb-backend` restarted once (13:09:49 -> health 200 at 13:10:34, NRestarts 0) so the H API serves the V3
  status. A/E/B/C/D API responses before/after the restart: identical apart from A's dashboard timer stamp.
* Backup: `/root/h_v3_backup_pre_deploy_20261015-040700.tar.gz` (64 MB: H modules, h_forward, h_paper,
  run_h_v2_d7, H API, the whole `d7` runtime, the four H units, H contract JSON).

## 2. Production AI guard

H runtime code: 0 imports of anthropic/openai/claude, 0 LLM API hosts outside the guard definitions; no
Claude process; no `authorization.json`; neither `anthropic` nor `openai` is installed in the server venv;
`.env` / `.env.massive` contain 0 LLM keys; the evaluation unit has 0 AI environment variables. Manual run:
`ai_calls` 0, `ai_calls_blocked` [] on every item; AI cost $0.

## 3. Manual acceptance run (2026-10-15, after the 14:10 KST forward update succeeded at 14:11:19)

```text
unit                 usb-h-evaluation.service, started by hand 14:12:20 KST, timer disabled
process 1 SCAN       14:12:20 -> 14:49:18 (37 min): universe 5,319, market pass 3,126, 718 rows scanned,
                     MethodJudgement screen, 10 candidates written, model_calls 0
process 2 evaluate   14:49:45 -> 15:17:27 (28 min): 12 items COMPLETE, 0 FAILED, decisions_changed 12
result               Result=success, exit 0, no restart, no OOM (kernel log 0)
```

| Item | D3 business | Guidance | Priced-in | Catalyst | D4 | D5 | D6 |
|---|---|---|---|---|---|---|---|
| FG (refresh) | P1 | no pair | NOT_PRICED / LOW | MATERIAL | UNKNOWN | P/B LOW +41% | WATCH -> WATCH |
| VRRM (refresh) | P1 | no pair | NOT_PRICED / MEDIUM | MATERIAL | UNKNOWN | EV/EBIT MEDIUM +10% | WATCH -> WATCH |
| ADI | P1 | no pair | PARTIALLY_PRICED / LOW | MATERIAL | UNKNOWN | P/FCF LOW -12% | NONE -> WATCH |
| CRUS | P1 | no pair | NOT_PRICED / LOW | MATERIAL | UNKNOWN | P/FCF LOW +24% | NONE -> WATCH |
| EQT | P1 | no pair | PARTIALLY_PRICED / MEDIUM | MATERIAL | UNKNOWN | P/FCF MEDIUM -1% | NONE -> WATCH |
| LCII | P2 (Item 1) | no pair | NOT_PRICED / MEDIUM | MATERIAL | UNKNOWN | P/FCF MEDIUM +24% | NONE -> WATCH |
| PG | P2 | no pair | NOT_PRICED / LOW | MATERIAL | UNKNOWN | P/FCF LOW +2% | NONE -> WATCH |
| UVV | P1 | no pair | NOT_PRICED / LOW | MATERIAL | UNKNOWN | P/FCF LOW +163% | NONE -> WATCH |
| BKTI | P2 | no pair | NOT_PRICED / LOW | MATERIAL | UNKNOWN | P/FCF LOW +25% | NONE -> WATCH |
| NSSC | P2 | no pair | NOT_PRICED / LOW | MATERIAL | UNKNOWN | P/FCF LOW +25% | NONE -> WATCH |
| MTN | P2 | no pair | LIKELY_PRICED / MEDIUM | MATERIAL | UNKNOWN | EV/EBIT MEDIUM -39% | NONE -> **REJECT** (valuation_fully_prices_the_positive_case) |
| DAL | P1 | no pair | PARTIALLY_PRICED / MEDIUM | UNKNOWN | UNKNOWN | EV/EBIT MEDIUM -22% | NONE -> **REJECT** (no_catalyst_identifiable_in_the_thesis_horizon) |

AEYE: EVIDENCE_ALREADY_IN_THESIS (not evaluated). Totals of this run: APPROVE 0, WATCH 10, REJECT 2. The
new candidates are the current SCAN order (the queue admits the written candidates; none was chosen by
hand). Every D4 is UNKNOWN for the reason the V3 result recorded: no comparable guidance pair in the D2.1
packages.

Forward ledger 8 -> 20 rows, `verify` PASS. Paper: `paper-verify` PASS (14 events, recomputed equal),
12 `SIGNAL_NO_ACTION` events, positions 0, pending entries 0, trades 0, equity = cash = $10,000, realised and
unrealised 0. WATCH/REJECT accidental entries 0. The launch-cohort counts (`status`) stay APPROVE 0 /
WATCH 6 / REJECT 2. Forward maturity: 1D 8/8, 5D 8/8 matured; 21D 0/8 (2026-11-02), 63D 0/8 (2027-01-04).
Latest price session 2026-10-14.

## 4. Memory, latency and isolation (5-second samples, `/root/h_v3_deploy_20261015/monitor.tsv`, 740 samples)

| | SCAN process | evaluation process | idle afterwards (2 min) |
|---|---|---|---|
| MemAvailable min | 179 MB | 190 MB | 446 MB |
| unit cgroup memory max (incl. page cache) | 373 MB | 337 MB | - |
| systemd MemoryPeak (whole unit) | 397 MB of MemoryMax 400 MB | | |
| memory PSI full avg10 max | 49.6 % | 41.8 % | 0.0 % |
| IO PSI full avg10 max | 61.5 % | 61.6 % | (IO pressure stays 60-90 % with H idle) |
| backend /health | p95 7 ms, max 1.53 s, 0 errors | p95 7 ms, max 0.87 s | max 0.01 s |
| crypto 8100 /health | p95 0.74 s, max 12.7 s, 0 errors | p95 0.60 s, max 5.2 s | p50 0.001 s, max 1.26 s |

Swap free 1,615 -> 1,513 MB (about 100 MB pushed out). Mid-SCAN the unit held anon 201 MB + file cache
134 MB. The automatic stop rule (30 s of backend or crypto > 15 s, or memory PSI full > 50 %) never fired.

Isolation: no service restarted (usb-backend only by the deploy, crypto paper/context/q123/btc-context-ui
and frontend untouched); A/B/C/D API responses equal before and after the run; E's `status` changed only by
E's own 13:20 universe build. `usb-e-rvol-once` failed at 14:51 with `MarketDataError: Kiwoom market-data
request failed`; it has failed the same way on every run since 2026-10-13 (before this deploy), so it is a
pre-existing E issue, not caused by H.

## 5. Operational decision (§14)

**B.** The run completed with integrity PASS and no OOM, but the host showed what the stop list calls
pressure: memory PSI full near 50 %, MemAvailable down to 179 MB, about 100 MB of swap-out, the unit at
397 of its 400 MB cgroup cap, and crypto terminal stalls of 5-13 s (4 samples) against a 0.001 s idle
median. The crypto terminal carries manual Binance LIVE orders, so a daily 65-minute window of such stalls
is not accepted. `usb-h-evaluation.timer` stays **disabled**. 1 GB operational readiness = NEEDS
OPTIMIZATION. Options (operator): RAM / instance upgrade (recommended), or a scheduled window agreed not to
overlap manual crypto trading plus further SCAN slimming; the strategy stays frozen either way.

## 6. Current production state

```text
V3 deterministic runtime        DEPLOYED (files), backend serving V3 status
usb-h-forward.timer             ENABLED, unchanged (01:10 ET)
usb-h-research                  REMOVED
usb-h-evaluation                INSTALLED, timer DISABLED; run by hand only
H forward / paper / UI          ACTIVE; UI labels from the previous build
production AI dependency        NONE
```

Rollback (if ever needed): `systemctl disable --now usb-h-evaluation.timer`, remove the two unit files,
restore `/root/h_v3_backup_pre_deploy_20261015-040700.tar.gz` (backend files), restart `usb-backend`; keep the
append-only ledger and paper events; move new evaluation artefacts to an evidence folder instead of deleting.
