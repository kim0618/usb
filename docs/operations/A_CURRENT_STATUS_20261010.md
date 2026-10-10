# Strategy A current status (2026-10-10)

> **HANDOFF.** Read this first in any Strategy A session. Contract and rules:
> `A_MOVER_NEXT_SESSION_AUTHORITY_V1.md` (the one current contract). This file holds the state
> that changes: commits, production, dates, open items. Newer `A_CURRENT_STATUS_<date>.md`
> files supersede this one.

## STRATEGY A CURRENT AUTHORITATIVE STATE

```text
STATUS     PRODUCTION / FORWARD PAPER
SCANNER    A-MOVER-LIVE-V1 (checksum 382ba2c1...), parent a-mover-scanner-v1.2 (f05e53cc...)
           09:15 ET cut -> TOP35 -> actionable -> max TOP8, run trading_date = D
AUTHORITY  Mover D -> GPT/Human on the Korean day D+1 -> Entry next_trading_day(D)
DATA       grouped daily + splits automated, reference manual (CS_2026-10-01)
FLAGS      A_MOVER_LIVE_ENABLED=true, A_MOVER_LIVE_ENTRY_AUTHORITY=true (both explicit)
ENTRY      Strategy V0 -> Risk V0 -> SimulationBroker, 09:45-10:30 ET
GPT        MANUAL
HUMAN      MANUAL (APPROVE != BUY)
LEGACY     quant_v0 still recorded by usb-morning-scan, NOT AUTHORITY, never a fallback
ORDERS     real orders 0
```

## Architecture

```text
usb-e-paper (16:45 KST)  attach_isolated -> mover cut 09:15 ET of D -> ScannerRun(a_mover_live_v1, D)
usb-backend (8000)       review UI/API (current_run) + GPT import guard + HumanDecision
                         entry runtime: E -> previous_trading_day(E)=D -> live_run_for(D)
                         -> active GPT analysis -> APPROVE rows -> Strategy V0 -> Risk V0 -> SimBroker
```

The scan runs inside `usb-e-paper` from `/root/usb_runtime/strategy_e_paper/src/backend`; the
authority (UI, import guard, entry runtime) runs inside `usb-backend` from `/root/usb/backend`.
A has no unit of its own. A change to the authority needs a `usb-backend` restart only.

## Commits

| stage | commit | `/root/usb` (usb-backend) | scan runtime tree (usb-e-paper) |
|---|---|---|---|
| mover scanner V1 / V1.1 / V1.2 research | `mover_scanner_v1/` | yes | yes |
| live pipeline on shared collector | `6b184b4`, `1c12a02` | yes | yes |
| mixed bootstrap baseline | `e2d6860` | yes | yes |
| union plan fix, attach isolation | `10c80fe`, `3ed57e5` | yes | yes |
| durable DB sessions | `c8351d2` | yes | yes |
| per-symbol readiness audit | `4ad46e2` | yes | **no** (see open item 1) |
| grouped daily automation | `55fea13` | yes (units + CLI) | n/a |
| splits automation | `a650252` | yes (units + CLI) | n/a |
| **next-session authority** | **`4b0e7ba`** | see Production | n/a (authority is backend-only) |
| this consolidation (docs only) | see `git log` of this file | docs are not deployed | n/a |

"Scan runtime tree" is `/root/usb_runtime/strategy_e_paper/src/backend`, a file copy that
`usb-e-paper` imports A's scan from. It is not a git checkout; it was matched file by file by
sha256 against commits on 2026-10-10.

## Timers (production, America/New_York)

| unit | schedule | ledger |
|---|---|---|
| `usb-grouped-daily.timer` | Mon..Fri 00:40 | `<e-paper tree>/data/runtime/ops/grouped_daily/runs.jsonl` |
| `usb-splits-refresh.timer` | daily 00:55, window `[-7d, +14d]` | `<e-paper tree>/data/runtime/ops/splits/runs.jsonl` |
| `usb-e-paper.timer` | 16:45 KST (03:45 ET) | `/root/usb_runtime/strategy_e_paper/run/` |
| `usb-morning-scan.timer` | 07:00 KST | legacy `quant_v0` rows (not authority) |

Last observed before this stage: grouped daily `2026-10-09T00:40 ET` collected 10-08, OK;
splits `2026-10-10T00:56 ET` window 10-03..10-24, OK, 79 events.

## Production

Filled in by the apply step of this stage; see the section below.

## Current blockers

NONE for the contract. Open items, none of which stops A from running:

1. **The scan runtime tree lacks `4ad46e2`.** Five A files in
   `/root/usb_runtime/strategy_e_paper/src/backend` (`features.py`, `gpt_handoff.py`,
   `integration.py`, `scanner.py`, `services/mover_scanner_source.py`) are byte-identical to
   `c8351d2`, not `4ad46e2`. Behaviour is still per symbol: `c8351d2` has no global baseline
   gate, so a symbol without 20 covered sessions simply produces no rvol and drops out (runs
   21-29 completed with TOP8 6/4/2/3/8). What is missing is the readiness **audit**
   (`baseline_ready_count`, exclusion reasons, `ScannerUniverseInput` rows) and the refusal when
   zero symbols are ready. Syncing that tree changes E's runtime and needs its own approval.
2. **Reference universe is manual.** One snapshot, `CS_2026-10-01`. It ages (new listings
   missed, delisted names linger) rather than refusing. Needs a producer or a manual refresh.
3. **Baseline coverage.** About 1,672 of 5,015 symbols had no stored baseline at 2026-10-05 and
   are excluded per symbol; they join as Kiwoom forward sessions reach 20.
4. **Legacy morning scan still runs.** It writes `quant_v0` rows and spends Kiwoom calls that no
   authority consumes. Retiring it is a separate decision.
5. **Pre-existing test breakage.** About twenty modules fail to import `SettlementAction`
   (absent at HEAD). Not A.
6. **Old mover runs 21, 23, 25, 27, 29 (2026-10-05..10-09) have no analysis and no decision.**
   They stay that way; nothing is backfilled. Run 29's entry session is 2026-10-12, so it can
   still be reviewed on the Korean day before.

## Next operator action

1. Korean day 2026-10-12 (Mon), before 22:27 KST: open the review UI. The current run should be
   mover run 29 with **Analysis Session 2026-10-09 / Entry Session 2026-10-12**. From then on,
   every Korean day reviews mover run D with Entry Session `next_trading_day(D)`.
2. Copy the GPT prompt, research by hand, import the JSON.
3. APPROVE / REJECT. Nothing else is needed; the entry runtime trades APPROVE rows on the entry
   session at 09:45-10:30 ET (22:45-23:30 KST).
4. Next morning: read the Paper evaluations and positions.

## Production verification checklist

```text
[ ] usb-backend active, NRestarts 0, no traceback since start
[ ] .env: A_MOVER_LIVE_ENABLED=true, A_MOVER_LIVE_ENTRY_AUTHORITY=true
[ ] /api current run = newest a_mover_live_v1 run (not quant_v0)
[ ] entry board: analysis_session_date = run.trading_date, entry_session_date = next_trading_day
[ ] import aimed at a quant_v0 run is refused
[ ] grouped_daily/runs.jsonl last line status OK, target = previous session
[ ] splits/runs.jsonl last line status OK, days_ahead_covered 14
[ ] DB: no analysis/decision/evaluation/trade added to old mover runs
```

## Do not

See `A_MOVER_NEXT_SESSION_AUTHORITY_V1.md` section 12. In short: no `quant_v0` authority or
fallback, no same-night approval requirement, no auto GPT, no auto approve, no legacy/mover
intersection, no threshold change without research, no backfill, `NO_DECISION` is not failure,
no Strategy/Risk/Exit change inside scanner work.
