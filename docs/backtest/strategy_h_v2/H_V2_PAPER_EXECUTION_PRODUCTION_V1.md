# H-V2 Paper Execution: Production V1

Deployed 2026-10-13 16:05-16:12 KST (03:05-03:12 ET) on `trader-j`. This is the H paper book
running in production. It is paper only: SimBroker `execution_v0`, with no broker and no exchange.
The contract and its rules are in `H_V2_PAPER_EXECUTION_CONTRACT_V1.md`, which is unchanged.

## 1. Result

```text
H-V2 PAPER EXECUTION PRODUCTION = ACTIVE (backend + runtime). Frontend build DEFERRED (see section 6)
runtime source commit           = 5093f6c   (contract docs b75ddea)
server git HEAD                 = db41f04 + file overlays (as for the D7 repair)
H_PAPER_V1_LAUNCH               = 2026-10-13T07:09:00Z (03:09 ET, before the 10-13 open)
initial equity / cash           = $10,000 / $10,000
open positions / pending / trades = 0 / 0 / 0
realized / unrealized PnL       = 0 / 0
decisions                       = APPROVE 0, WATCH 6, REJECT 2 (unchanged)
paper events                    = 0 (no backfill); paper-verify PASS
```

The first session the book can act on is 2026-10-13. Its bar is collected by the
`usb-h-forward` run on Wed 2026-10-14 01:10 ET. With APPROVE = 0, that run must append NAV only.

## 2. What was deployed

The 15 files from `5093f6c` were copied path by path, and each copy was sha-verified against the
commit:

* `backend/app/strategies/h_paper/{__init__,contract,engine,service,store}.py` (new)
* `backend/app/strategies/h_forward/{transitions,research_queue}.py` (new)
* `backend/app/strategies/h_forward/{cohort,prices,views}.py`
* `backend/app/dev/run_h_v2_d7.py`
* `backend/app/api/strategies.py`
* `docs/operations/h_paper_execution_v1.{json,sha256}`
* `docs/operations/h_thesis_evidence_v1.json`

Before the copy, all six modified files on the server were byte-identical to `5093f6c^`, including
`api/strategies.py` (`1e4d98c1…`). Copying the `5093f6c` versions therefore applied exactly the H
hunks. No A hunk, no hunk from another session, and no other file was deployed. Staged imports
passed before the live tree was touched.

Not deployed: the frontend, the docs commit, the systemd units (unchanged: service `fd7d81a5…`,
timer `111678d0…`) and any crypto file.

Restarts: `usb-backend` once (16:07:51 KST), up in about 21 s. The A runtime came back as
`account=1 cash=7509.3985 positions=0`. No A or E runner unit, `usb-frontend` or `usb-crypto-*`
unit was restarted.

## 3. Verification

| Check | Result |
|---|---|
| D7 `verify` before and after | PASS, `problems=[]`, 8 ledger rows |
| D7 runtime files (70, excluding the new `paper/`) | sha-identical before and after |
| Forward outcomes | 1D 8/8 MATURED, 5D 8/8 MATURED, 21D 8 PENDING, 63D 8 PENDING; `/forward` decision counts, maturity, evaluation, launch and every row's outcomes identical to before |
| D5 contract | `D5_D2R_V1`. VRRM Bear = null, `NEGATIVE_IMPLIED_EQUITY` |
| Paper contract on the server | sha `89f7beed…`; TP1 sells floor(initial × 0.5); TP2 sells all remaining; downgrade WATCH/REJECT; Bear NOT_AVAILABLE → NOT_EVALUATED; fixed % stop false |
| Engine smoke | In memory on the server, nothing written: APPROVE → PENDING_ENTRY → BUY at the next open → OPEN, `bear_rule=NOT_EVALUATED` |
| A | API account / trades / positions / equity byte-identical before and after (equity 7509.40, 0 positions) |
| E | API account / trades / positions / equity byte-identical (equity 10190.39); 15 paper_state files sha-identical |
| Isolation | A's `/paper` route refuses (`NOT_AN_H_PAPER_BOOK`); H is absent from the A+E combined column; H ids are `STRATEGY_H_V2|…` |
| API | `/STRATEGY_H_V2/{account,positions,trades,equity,paper}` 200 with the expected payloads; `/cards` H mode `PAPER + SHADOW`, equity 10000 |
| Research queue | FG, VRRM queued; AEYE `EVIDENCE_ALREADY_IN_THESIS`; intake `NOT_WIRED`; 0 model calls |
| OOM | 0 in the deploy window |

Evidence is in `/root/h_paper_deploy_20261013-160451/` (pre/post API bodies, sha lists, the deployed
tar). Backup: `/root/h_paper_backup_pre_deploy_20261013-160451.tar.gz` (126 entries: h_forward,
run_h_v2_d7, api/strategies, registry, d7 runtime, H frontend sources).

## 4. Daily operation

`usb-h-forward.timer` is unchanged (Mon..Fri 01:10 ET, next run Wed 2026-10-14 14:10 KST). The same
`update` command now also runs:

* step 4, paper execution, which exits 3 on `PAPER_INTEGRITY_FAILURE`;
* step 5, the research queue rebuild.

With APPROVE = 0, the expected result is NAV rows only, cash $10,000 and 0 trades.

## 5. Rollback

```bash
mv /root/usb/data/runtime/strategy_h_v2/d7/paper /root/h_paper_rollback_<ts>
tar xzf /root/h_paper_backup_pre_deploy_20261013-160451.tar.gz -C /root/usb \
  backend/app/strategies/h_forward backend/app/dev/run_h_v2_d7.py backend/app/api/strategies.py
rm -rf /root/usb/backend/app/strategies/h_paper \
  /root/usb/backend/app/strategies/h_forward/{transitions,research_queue}.py
systemctl restart usb-backend
```

The forward snapshot, ledger, prices, SEC cache and refresh queue are not restored, because the
paper book never writes them.

## 6. Not done in this window

* **Frontend (DEFERRED).** The running build `.next-build` (BUILD_ID `w3LLcmPdetY4iarG7TXCX`,
  15:12 KST) was produced by another session from a tree other than the server's
  `frontend/` sources, which lack `trading-h/page.tsx` and the card component. Rebuilding from any
  other tree would deploy, or roll back, unrelated UI. The memory and io PSI were also elevated
  (io avg300 about 47), with the e-paper window starting at 16:45 KST.
  * The current UI still works: `/dashboard`, `/trading-h`, `/strategy-h` and `/strategy-compare`
    all return 200.
  * The old card shows "-" for H money until the five H frontend files are built into the next
    coordinated frontend build.
* **Crypto ACTIVE TAPE optimization (SKIPPED).** Host PSI was not stable. The target
  `segments.py` exists only as another session's uncommitted local edit (`2346e39…`), and differs
  from both the server copy (`c43edb2…`) and HEAD (`6337805…`), so no verified source could be
  identified. `usb-crypto-paper` was not restarted.
* **No research.** No Opus call. No FG/VRRM/AEYE refresh. The refresh executor is NOT IMPLEMENTED.
  New-candidate intake is NOT_WIRED.
