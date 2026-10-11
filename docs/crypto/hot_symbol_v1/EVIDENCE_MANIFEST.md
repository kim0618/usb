# HOT Evidence Manifest

Every file under `hot_coin_finder_r0/`, `hot_coin_finder_v0/` and `hot_symbol_v1/` as of the archive commit.
sha256 is of the file bytes; `generated` is the file's local (KST) mtime. Paths are relative to `docs/crypto/`.
Report files carry a FINAL STATUS note added 2026-10-11, so their hash is of the noted version.

## Excluded from Git (kept local only)

| path | size | sha256 | why |
|---|---|---|---|
| `hot_coin_finder_r0/evidence/raw_snapshots_and_klines.tgz` | 22,079,570 | `e4f47eeeda10935f54c6a291f32461c7eb4e0ac39dbcbe89d0c0f8dd24413ba3` | raw public Binance snapshots and 5m klines (12 entries, `data/snap_*.json` + klines) used by the R0 analysis. A 22 MB market dump does not belong in Git; the analysis scripts, logs and derived reports (`hist_report.txt`, `live_report.txt`, `hist_hot_seq.json`) are committed and the archive stays at this path in `~/usb-hot-coin-finder-r0` |

## Committed

| path | phase | related report | size | sha256 | generated |
|---|---|---|---|---|---|
| `hot_coin_finder_r0/HOT_COIN_FINDER_R0_REPORT.md` | R0 feasibility | [HOT_COIN_FINDER_R0_REPORT.md](../hot_coin_finder_r0/HOT_COIN_FINDER_R0_REPORT.md) | 18,019 | `593c96688d57a1ef…` | 2026-10-11 13:24 |
| `hot_coin_finder_r0/evidence/analyze.py` | R0 feasibility evidence | [HOT_COIN_FINDER_R0_REPORT.md](../hot_coin_finder_r0/HOT_COIN_FINDER_R0_REPORT.md) | 16,042 | `7d8ea9014167439e…` | 2026-10-10 20:50 |
| `hot_coin_finder_r0/evidence/backfill.log` | R0 feasibility evidence | [HOT_COIN_FINDER_R0_REPORT.md](../hot_coin_finder_r0/HOT_COIN_FINDER_R0_REPORT.md) | 40 | `b503587e21f05ffa…` | 2026-10-10 20:50 |
| `hot_coin_finder_r0/evidence/backfill.py` | R0 feasibility evidence | [HOT_COIN_FINDER_R0_REPORT.md](../hot_coin_finder_r0/HOT_COIN_FINDER_R0_REPORT.md) | 2,565 | `38a6d67cc3003e31…` | 2026-10-10 20:50 |
| `hot_coin_finder_r0/evidence/hist_analyze.py` | R0 feasibility evidence | [HOT_COIN_FINDER_R0_REPORT.md](../hot_coin_finder_r0/HOT_COIN_FINDER_R0_REPORT.md) | 7,672 | `6d3d8252bd33bf5e…` | 2026-10-10 20:50 |
| `hot_coin_finder_r0/evidence/hist_hot_seq.json` | R0 feasibility evidence | [HOT_COIN_FINDER_R0_REPORT.md](../hot_coin_finder_r0/HOT_COIN_FINDER_R0_REPORT.md) | 180,416 | `63ae6254be59399c…` | 2026-10-10 20:50 |
| `hot_coin_finder_r0/evidence/hist_report.txt` | R0 feasibility evidence | [HOT_COIN_FINDER_R0_REPORT.md](../hot_coin_finder_r0/HOT_COIN_FINDER_R0_REPORT.md) | 2,510 | `289cce8bb203deb8…` | 2026-10-10 20:50 |
| `hot_coin_finder_r0/evidence/live_report.txt` | R0 feasibility evidence | [HOT_COIN_FINDER_R0_REPORT.md](../hot_coin_finder_r0/HOT_COIN_FINDER_R0_REPORT.md) | 26,026 | `0bb683e710e46385…` | 2026-10-10 20:50 |
| `hot_coin_finder_r0/evidence/snap.py` | R0 feasibility evidence | [HOT_COIN_FINDER_R0_REPORT.md](../hot_coin_finder_r0/HOT_COIN_FINDER_R0_REPORT.md) | 8,893 | `af86d6756859b89e…` | 2026-10-10 20:50 |
| `hot_coin_finder_r0/evidence/trial.log` | R0 feasibility evidence | [HOT_COIN_FINDER_R0_REPORT.md](../hot_coin_finder_r0/HOT_COIN_FINDER_R0_REPORT.md) | 1,323 | `47b10ae62c6ac91c…` | 2026-10-10 20:50 |
| `hot_coin_finder_r0/evidence/trial_aborted.log` | R0 feasibility evidence | [HOT_COIN_FINDER_R0_REPORT.md](../hot_coin_finder_r0/HOT_COIN_FINDER_R0_REPORT.md) | 189 | `57b8d1563af81f03…` | 2026-10-10 20:50 |
| `hot_coin_finder_v0/HOT_COIN_FINDER_V0_REPORT.md` | V0 read-only scanner | [HOT_COIN_FINDER_V0_REPORT.md](../hot_coin_finder_v0/HOT_COIN_FINDER_V0_REPORT.md) | 13,696 | `a1b3d60d1a6394b5…` | 2026-10-11 13:24 |
| `hot_coin_finder_v0/ui/1440_2_result.png` | V0 read-only scanner | [HOT_COIN_FINDER_V0_REPORT.md](../hot_coin_finder_v0/HOT_COIN_FINDER_V0_REPORT.md) | 137,813 | `80856c544a4f533a…` | 2026-10-10 21:25 |
| `hot_coin_finder_v0/ui/1600_1_scanning.png` | V0 read-only scanner | [HOT_COIN_FINDER_V0_REPORT.md](../hot_coin_finder_v0/HOT_COIN_FINDER_V0_REPORT.md) | 104,759 | `54ba105846c776cc…` | 2026-10-10 21:25 |
| `hot_coin_finder_v0/ui/1600_3_failure_kept.png` | V0 read-only scanner | [HOT_COIN_FINDER_V0_REPORT.md](../hot_coin_finder_v0/HOT_COIN_FINDER_V0_REPORT.md) | 140,140 | `101d9ea54a5eb47f…` | 2026-10-10 21:25 |
| `hot_coin_finder_v0/ui/390_0_before.png` | V0 read-only scanner | [HOT_COIN_FINDER_V0_REPORT.md](../hot_coin_finder_v0/HOT_COIN_FINDER_V0_REPORT.md) | 58,512 | `3d2f03d694912c39…` | 2026-10-10 21:25 |
| `hot_coin_finder_v0/ui/390_2_result.png` | V0 read-only scanner | [HOT_COIN_FINDER_V0_REPORT.md](../hot_coin_finder_v0/HOT_COIN_FINDER_V0_REPORT.md) | 85,499 | `747b13ec4d708e34…` | 2026-10-10 21:25 |
| `hot_coin_finder_v0/ui/390_4_reload_restored.png` | V0 read-only scanner | [HOT_COIN_FINDER_V0_REPORT.md](../hot_coin_finder_v0/HOT_COIN_FINDER_V0_REPORT.md) | 85,492 | `c1e24aa096669246…` | 2026-10-10 21:25 |
| `hot_coin_finder_v0/ui/preview_backend.py` | V0 read-only scanner | [HOT_COIN_FINDER_V0_REPORT.md](../hot_coin_finder_v0/HOT_COIN_FINDER_V0_REPORT.md) | 2,190 | `a293b7382010b231…` | 2026-10-10 21:25 |
| `hot_coin_finder_v0/ui/report.json` | V0 read-only scanner | [HOT_COIN_FINDER_V0_REPORT.md](../hot_coin_finder_v0/HOT_COIN_FINDER_V0_REPORT.md) | 5,688 | `3ca6adec6357f886…` | 2026-10-10 21:25 |
| `hot_coin_finder_v0/ui/server_requests_qa_run_3.jsonl` | V0 read-only scanner | [HOT_COIN_FINDER_V0_REPORT.md](../hot_coin_finder_v0/HOT_COIN_FINDER_V0_REPORT.md) | 41,713 | `c45a2b6d43a0fd96…` | 2026-10-10 21:25 |
| `hot_coin_finder_v0/ui/server_requests_qa_runs_1_2.jsonl` | V0 read-only scanner | [HOT_COIN_FINDER_V0_REPORT.md](../hot_coin_finder_v0/HOT_COIN_FINDER_V0_REPORT.md) | 103,270 | `49169c1b2f9242c6…` | 2026-10-10 21:25 |
| `hot_coin_finder_v0/ui/ui_qa.py` | V0 read-only scanner | [HOT_COIN_FINDER_V0_REPORT.md](../hot_coin_finder_v0/HOT_COIN_FINDER_V0_REPORT.md) | 5,673 | `fe930d470970e679…` | 2026-10-10 21:25 |
| `hot_symbol_v1/DYNAMIC_HOT_TRADING_V1_REPORT.md` | V1 reports | [HOT_V1_FINAL_INDEX.md](HOT_V1_FINAL_INDEX.md) | 14,641 | `5efa2144e0468911…` | 2026-10-11 13:24 |
| `hot_symbol_v1/HOT_V1_FINAL_INDEX.md` | V1 reports | [HOT_V1_FINAL_INDEX.md](HOT_V1_FINAL_INDEX.md) | 8,140 | `01e3e6113fcf5974…` | 2026-10-11 13:25 |
| `hot_symbol_v1/MULTISYMBOL_RECOVERY_V1_REPORT.md` | V1 reports | [HOT_V1_FINAL_INDEX.md](HOT_V1_FINAL_INDEX.md) | 9,536 | `7d183a8def310e36…` | 2026-10-11 13:24 |
| `hot_symbol_v1/PRODUCTION_DEPLOY_HOT_V1_20261011.md` | V1 reports | [HOT_V1_FINAL_INDEX.md](HOT_V1_FINAL_INDEX.md) | 8,700 | `f4d4b30493cde6e7…` | 2026-10-11 13:24 |
| `hot_symbol_v1/SAFE_MAX_FIX_DRYRUN_REPORT.md` | V1 reports | [HOT_V1_FINAL_INDEX.md](HOT_V1_FINAL_INDEX.md) | 13,468 | `c500c2a0fc7a8c22…` | 2026-10-11 13:24 |
| `hot_symbol_v1/multisymbol_recovery_20261011/diag.py` | Multi-symbol recovery E2E + real-data probes | [MULTISYMBOL_RECOVERY_V1_REPORT.md](MULTISYMBOL_RECOVERY_V1_REPORT.md) | 2,954 | `6d0eaad8f86bdaa5…` | 2026-10-11 13:24 |
| `hot_symbol_v1/multisymbol_recovery_20261011/e2e_after_restart_hot.png` | Multi-symbol recovery E2E + real-data probes | [MULTISYMBOL_RECOVERY_V1_REPORT.md](MULTISYMBOL_RECOVERY_V1_REPORT.md) | 116,735 | `137275b3a6912d46…` | 2026-10-11 12:51 |
| `hot_symbol_v1/multisymbol_recovery_20261011/e2e_restart.py` | Multi-symbol recovery E2E + real-data probes | [MULTISYMBOL_RECOVERY_V1_REPORT.md](MULTISYMBOL_RECOVERY_V1_REPORT.md) | 6,230 | `0a62e86882815bd0…` | 2026-10-11 12:51 |
| `hot_symbol_v1/multisymbol_recovery_20261011/probe2.py` | Multi-symbol recovery E2E + real-data probes | [MULTISYMBOL_RECOVERY_V1_REPORT.md](MULTISYMBOL_RECOVERY_V1_REPORT.md) | 1,060 | `e58081a0d8649a21…` | 2026-10-11 13:24 |
| `hot_symbol_v1/paper_e2e_20261011/1440_2_hot_position_blocked.png` | PAPER E2E + UI regression | [SAFE_MAX_FIX_DRYRUN_REPORT.md](SAFE_MAX_FIX_DRYRUN_REPORT.md) | 124,371 | `8b895e2f10574af8…` | 2026-10-11 12:22 |
| `hot_symbol_v1/paper_e2e_20261011/1600_1_hot_tab.png` | PAPER E2E + UI regression | [SAFE_MAX_FIX_DRYRUN_REPORT.md](SAFE_MAX_FIX_DRYRUN_REPORT.md) | 107,591 | `e0255d8c16bc8e4b…` | 2026-10-11 12:22 |
| `hot_symbol_v1/paper_e2e_20261011/390_1_hot_tab.png` | PAPER E2E + UI regression | [SAFE_MAX_FIX_DRYRUN_REPORT.md](SAFE_MAX_FIX_DRYRUN_REPORT.md) | 64,321 | `b50c92918f48d704…` | 2026-10-11 12:22 |
| `hot_symbol_v1/paper_e2e_20261011/report.json` | PAPER E2E + UI regression | [SAFE_MAX_FIX_DRYRUN_REPORT.md](SAFE_MAX_FIX_DRYRUN_REPORT.md) | 4,340 | `fb742e670ccb6063…` | 2026-10-11 12:22 |
| `hot_symbol_v1/paper_e2e_20261011/ui_qa_v2.py` | PAPER E2E + UI regression | [SAFE_MAX_FIX_DRYRUN_REPORT.md](SAFE_MAX_FIX_DRYRUN_REPORT.md) | 12,641 | `f9deb208b6dacaff…` | 2026-10-11 13:24 |
| `hot_symbol_v1/production_deploy_20261011/after.json` | Production deploy QA | [PRODUCTION_DEPLOY_HOT_V1_20261011.md](PRODUCTION_DEPLOY_HOT_V1_20261011.md) | 5,399 | `8685819863c903cd…` | 2026-10-11 13:17 |
| `hot_symbol_v1/production_deploy_20261011/baseline.sh` | Production deploy QA | [PRODUCTION_DEPLOY_HOT_V1_20261011.md](PRODUCTION_DEPLOY_HOT_V1_20261011.md) | 5,435 | `d65bb6d2f53b7bd0…` | 2026-10-11 13:17 |
| `hot_symbol_v1/production_deploy_20261011/before.json` | Production deploy QA | [PRODUCTION_DEPLOY_HOT_V1_20261011.md](PRODUCTION_DEPLOY_HOT_V1_20261011.md) | 4,968 | `d3559831511f8248…` | 2026-10-11 13:17 |
| `hot_symbol_v1/production_deploy_20261011/check_after.json` | Production deploy QA | [PRODUCTION_DEPLOY_HOT_V1_20261011.md](PRODUCTION_DEPLOY_HOT_V1_20261011.md) | 1,259 | `5ba315a23804af18…` | 2026-10-11 13:17 |
| `hot_symbol_v1/production_deploy_20261011/check_before.json` | Production deploy QA | [PRODUCTION_DEPLOY_HOT_V1_20261011.md](PRODUCTION_DEPLOY_HOT_V1_20261011.md) | 1,254 | `da9c415868da513a…` | 2026-10-11 13:17 |
| `hot_symbol_v1/production_deploy_20261011/deploy_backend.out` | Production deploy QA | [PRODUCTION_DEPLOY_HOT_V1_20261011.md](PRODUCTION_DEPLOY_HOT_V1_20261011.md) | 521 | `32271d12be8c57bc…` | 2026-10-11 13:17 |
| `hot_symbol_v1/production_deploy_20261011/deploy_files.sha` | Production deploy QA | [PRODUCTION_DEPLOY_HOT_V1_20261011.md](PRODUCTION_DEPLOY_HOT_V1_20261011.md) | 1,915 | `15e3b48bf8c42bd1…` | 2026-10-11 13:17 |
| `hot_symbol_v1/production_deploy_20261011/live_ro.json` | Production deploy QA | [PRODUCTION_DEPLOY_HOT_V1_20261011.md](PRODUCTION_DEPLOY_HOT_V1_20261011.md) | 6,532 | `5f4fdb0f504c6027…` | 2026-10-11 13:17 |
| `hot_symbol_v1/production_deploy_20261011/live_ro.py` | Production deploy QA | [PRODUCTION_DEPLOY_HOT_V1_20261011.md](PRODUCTION_DEPLOY_HOT_V1_20261011.md) | 6,843 | `f91ed3c4db4a5a5b…` | 2026-10-11 13:17 |
| `hot_symbol_v1/production_deploy_20261011/paper_smoke.py` | Production deploy QA | [PRODUCTION_DEPLOY_HOT_V1_20261011.md](PRODUCTION_DEPLOY_HOT_V1_20261011.md) | 6,630 | `9bd87c58421006d2…` | 2026-10-11 13:17 |
| `hot_symbol_v1/production_deploy_20261011/ro_proxy.py` | Production deploy QA | [PRODUCTION_DEPLOY_HOT_V1_20261011.md](PRODUCTION_DEPLOY_HOT_V1_20261011.md) | 1,725 | `73b084546b7c57c1…` | 2026-10-11 13:17 |
| `hot_symbol_v1/production_deploy_20261011/smoke_close.json` | Production deploy QA | [PRODUCTION_DEPLOY_HOT_V1_20261011.md](PRODUCTION_DEPLOY_HOT_V1_20261011.md) | 1,134 | `3e5db072f675b07b…` | 2026-10-11 13:17 |
| `hot_symbol_v1/production_deploy_20261011/smoke_open.json` | Production deploy QA | [PRODUCTION_DEPLOY_HOT_V1_20261011.md](PRODUCTION_DEPLOY_HOT_V1_20261011.md) | 4,335 | `701f4e1c62e04743…` | 2026-10-11 13:17 |
| `hot_symbol_v1/production_deploy_20261011/ui_prod.py` | Production deploy QA | [PRODUCTION_DEPLOY_HOT_V1_20261011.md](PRODUCTION_DEPLOY_HOT_V1_20261011.md) | 4,546 | `57de939651f0b13a…` | 2026-10-11 13:17 |
| `hot_symbol_v1/production_deploy_20261011/ui/1440_live_hot.png` | Production deploy QA | [PRODUCTION_DEPLOY_HOT_V1_20261011.md](PRODUCTION_DEPLOY_HOT_V1_20261011.md) | 116,259 | `1f1f8fb1e31f3d22…` | 2026-10-11 13:17 |
| `hot_symbol_v1/production_deploy_20261011/ui/1440_paper_hot.png` | Production deploy QA | [PRODUCTION_DEPLOY_HOT_V1_20261011.md](PRODUCTION_DEPLOY_HOT_V1_20261011.md) | 107,540 | `da19dcb40fede174…` | 2026-10-11 13:17 |
| `hot_symbol_v1/production_deploy_20261011/ui/1600_live_hot.png` | Production deploy QA | [PRODUCTION_DEPLOY_HOT_V1_20261011.md](PRODUCTION_DEPLOY_HOT_V1_20261011.md) | 117,384 | `f589d41c5a823106…` | 2026-10-11 13:17 |
| `hot_symbol_v1/production_deploy_20261011/ui/1600_paper_hot.png` | Production deploy QA | [PRODUCTION_DEPLOY_HOT_V1_20261011.md](PRODUCTION_DEPLOY_HOT_V1_20261011.md) | 110,400 | `05a85b9f919c6b52…` | 2026-10-11 13:17 |
| `hot_symbol_v1/production_deploy_20261011/ui/390_live_hot.png` | Production deploy QA | [PRODUCTION_DEPLOY_HOT_V1_20261011.md](PRODUCTION_DEPLOY_HOT_V1_20261011.md) | 75,441 | `7ca5154e0d603e44…` | 2026-10-11 13:17 |
| `hot_symbol_v1/production_deploy_20261011/ui/390_paper_hot.png` | Production deploy QA | [PRODUCTION_DEPLOY_HOT_V1_20261011.md](PRODUCTION_DEPLOY_HOT_V1_20261011.md) | 61,579 | `e7b6820b3a30b549…` | 2026-10-11 13:17 |
| `hot_symbol_v1/production_deploy_20261011/ui/report.json` | Production deploy QA | [PRODUCTION_DEPLOY_HOT_V1_20261011.md](PRODUCTION_DEPLOY_HOT_V1_20261011.md) | 2,983 | `eb06e95858e0190b…` | 2026-10-11 13:17 |
| `hot_symbol_v1/server_readonly_20261010/README.md` | Production LIVE read-only #1 | [README.md](server_readonly_20261010/README.md) | 1,838 | `960a404773fe28dc…` | 2026-10-10 22:49 |
| `hot_symbol_v1/server_readonly_20261010/baseline.sh` | Production LIVE read-only #1 | [README.md](server_readonly_20261010/README.md) | 3,603 | `4b7dcc58b1864fef…` | 2026-10-10 22:49 |
| `hot_symbol_v1/server_readonly_20261010/baseline_after.json` | Production LIVE read-only #1 | [README.md](server_readonly_20261010/README.md) | 3,929 | `750b9ec95d2ff921…` | 2026-10-10 22:49 |
| `hot_symbol_v1/server_readonly_20261010/baseline_before.json` | Production LIVE read-only #1 | [README.md](server_readonly_20261010/README.md) | 3,929 | `6a73587a8e8ec4c6…` | 2026-10-10 22:49 |
| `hot_symbol_v1/server_readonly_20261010/server_result.json` | Production LIVE read-only #1 | [README.md](server_readonly_20261010/README.md) | 10,630 | `ee253b0e889d9919…` | 2026-10-10 22:49 |
| `hot_symbol_v1/server_readonly_20261010/validate.py` | Production LIVE read-only #1 | [README.md](server_readonly_20261010/README.md) | 13,294 | `c33c07fee2c0d1e5…` | 2026-10-10 22:49 |
| `hot_symbol_v1/server_readonly_postfix_20261010/server_result_and_mutation_proof.txt` | Production LIVE read-only #2 (after Safe MAX fix) | [SAFE_MAX_FIX_DRYRUN_REPORT.md](SAFE_MAX_FIX_DRYRUN_REPORT.md) | 21,765 | `5d39069b50bbefa0…` | 2026-10-11 12:22 |
| `hot_symbol_v1/ui/1440_1_hot_selected.png` | V1 PAPER browser QA | [DYNAMIC_HOT_TRADING_V1_REPORT.md](DYNAMIC_HOT_TRADING_V1_REPORT.md) | 104,552 | `731dcbef94bd7045…` | 2026-10-10 22:36 |
| `hot_symbol_v1/ui/1440_2_hot_position_blocked.png` | V1 PAPER browser QA | [DYNAMIC_HOT_TRADING_V1_REPORT.md](DYNAMIC_HOT_TRADING_V1_REPORT.md) | 114,900 | `508cd5a92f472214…` | 2026-10-10 22:36 |
| `hot_symbol_v1/ui/1440_5_hot_prices.png` | V1 PAPER browser QA | [DYNAMIC_HOT_TRADING_V1_REPORT.md](DYNAMIC_HOT_TRADING_V1_REPORT.md) | 102,189 | `4151cd2496cc307e…` | 2026-10-10 22:36 |
| `hot_symbol_v1/ui/1600_2_panel.png` | V1 PAPER browser QA | [DYNAMIC_HOT_TRADING_V1_REPORT.md](DYNAMIC_HOT_TRADING_V1_REPORT.md) | 140,299 | `177c690a94895b17…` | 2026-10-10 22:36 |
| `hot_symbol_v1/ui/390_0_tabs.png` | V1 PAPER browser QA | [DYNAMIC_HOT_TRADING_V1_REPORT.md](DYNAMIC_HOT_TRADING_V1_REPORT.md) | 75,662 | `a57931fffe2699f2…` | 2026-10-10 22:36 |
| `hot_symbol_v1/ui/390_5_hot_prices.png` | V1 PAPER browser QA | [DYNAMIC_HOT_TRADING_V1_REPORT.md](DYNAMIC_HOT_TRADING_V1_REPORT.md) | 63,873 | `5715de9ea59a3485…` | 2026-10-10 22:36 |
| `hot_symbol_v1/ui/report.json` | V1 PAPER browser QA | [DYNAMIC_HOT_TRADING_V1_REPORT.md](DYNAMIC_HOT_TRADING_V1_REPORT.md) | 3,336 | `f94d693f041526d0…` | 2026-10-10 22:36 |
| `hot_symbol_v1/ui/ui_qa_v1.py` | V1 PAPER browser QA | [DYNAMIC_HOT_TRADING_V1_REPORT.md](DYNAMIC_HOT_TRADING_V1_REPORT.md) | 10,953 | `d102a979cf0ec00e…` | 2026-10-10 22:36 |

Committed files: 73 (plus this manifest). Excluded: 1.
