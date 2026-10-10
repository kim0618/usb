# Strategy A: the mover scan is reviewed in the Korean day and traded the next session

> **CURRENT PRODUCTION CONTRACT for Strategy A.** This is the one document that states what A
> is. Every other A document is research history, a superseded stage record, or a reference
> runbook, and says so in its first lines. Deployment state, dates and commit SHAs live in
> `A_CURRENT_STATUS_20261010.md`, not here.

Code commit: `4b0e7baa3e2a719a4f45bdb0896baa867de24e3b`
(`fix(strategy-a): restore mover next-session approval authority`). Sections 6 and 8 below are
the record of the stage that wrote this contract (2026-10-10); sections 1-5, 7 and 9-12 are the
contract itself. No real order exists anywhere in A. The stage read the production database
through a `mode=ro` connection with `PRAGMA query_only=ON` whose mtimes were unchanged.

## 0. A on one screen

| item | value | where it is enforced |
|---|---|---|
| status | **PRODUCTION / FORWARD PAPER** | `usb-backend` entry runtime, `SimulationBroker` |
| candidate source | `ScannerRun.score_version = a_mover_live_v1`, provider `KIWOOM_AE_SHARED_PREMARKET` | `strategy_a_mover_live.contract` |
| live scanner | `A-MOVER-LIVE-V1`, checksum `382ba2c16409cb1009006b992c2c0d05e0c8a25c29ee768f997c5027fbd3f22b` | `strategy_a_mover_live.contract.verify` |
| research parent | `a-mover-scanner-v1.2`, handoff checksum `f05e53cce5a431e8e132a0fc1698085b64f8e1d015e11028a77dc62754a11f25` | `mover_scanner_v1.contract.verify` (`ContractDrift` on mismatch) |
| scan cut | **09:15 ET** of session D (`SCAN_CUT_MINUTE = 555`), run stamped `trading_date = D` | `mover_scanner_v1.contract` |
| pool | **TOP35** (`POOL_SIZE = 35`) | `mover_scanner_v1.contract` |
| handoff | actionable mask, then **at most TOP8** (`top_count = 8`; fewer is normal) | `mover_scanner_v1.handoff` |
| baseline | `A_MOVER_PM_VOLUME_V1`, median, **per symbol**: a symbol with fewer than 20 covered prior sessions inside the 40-session lookback is excluded from that scan (`INSUFFICIENT_COVERED_SESSIONS`) and joins automatically once it has 20. The run refuses only when **no** symbol is ready | `strategy_a_mover_live.baseline` / `features.baseline_readiness` |
| review | next **Korean day** after the cut: manual GPT research, manual APPROVE / REJECT | `api.router`, `research.authority` |
| entry session | **`next_trading_day(D)`** | `research.current_run.entry_session_for` |
| runtime read | `previous_trading_day(E) = D` -> exactly the mover run of D -> active GPT analysis -> `APPROVE` rows | `entry_management_runtime.analysis_session_date`, `paper_adapter.live_run_for` |
| entry / risk | unchanged Strategy V0 -> Risk V0 -> `SimulationBroker` | `EntryLifecycleService.evaluate` |
| legacy `quant_v0` | recorded by `usb-morning-scan`, **not entry authority**, never a fallback | `current_run.source_criteria` |
| GPT | **manual** (no API call, `NO_GPT_CALL`) | `strategy_a_mover_live.config` |
| human | **manual** (no auto-approve) | `api.router` |
| real orders | **0** | Paper only |

`APPROVE != BUY`. An APPROVE registers the symbol for that night's Strategy V0 / Risk V0
evaluation and nothing else.

This supersedes the disposition in `A_MORNING_APPROVAL_CONTRACT_V1.md`, which is kept as the
record of that audit. That stage restored the morning review by *unbinding* entry from the live
mover source and letting it fall back to the pre-live trade-value scanner. The workflow came
back and the candidate source went backwards. This stage restores the same workflow **on the
mover source**, which is the contract that was wanted in the first place.

## 1. The contract

```
mover scan of session D        09:15 ET cut, run stamped trading_date = D, COMPLETED ~09:27 ET
  -> GPT research              the Korean day after the cut
  -> human APPROVE / REJECT    the same Korean day
  -> entry session D+1         next_trading_day(D), 09:45-10:30 ET, Strategy V0 + Risk V0
```

`APPROVE` is an admission to be evaluated, never a buy. The premarket gap mask, the premarket
volume ratio, the 15-minute opening range and the 10:30 ET deadline are all measured against
the **entry session's own** market by the unmodified
`EntryLifecycleService.evaluate -> StrategyV0Engine -> RiskEngine -> StrategyLifecycleRunner`
path.

### The operator's clock

| moment | ET | KST |
|---|---|---|
| mover cut of session D | D 09:15 | D 22:15 |
| mover run D COMPLETED | D 09:27 | D 22:27 |
| GPT research and APPROVE | D 20:00 - D+1 05:00 | **D+1 09:00 - 18:00** |
| entry evaluation on D+1 | D+1 09:45 - 10:30 | D+1 22:45 - 23:30 |

The review window is the whole Korean working day. Under the previous same-session mapping it
was the 63 minutes from 22:27 to 23:30 KST, which is the regression this stage removes.

## 2. The root cause, and what it was not

It was **not** date arithmetic. `MoverLiveEntryLifecycleService.analysis_session_date` was
overridden to the identity, and for the row it describes that override is arithmetically
correct: a cut taken at 09:15 ET of session D really is an observation of D. What was wrong is
the consumer. With entry bound to the identity, the candidates of the session being traded did
not exist until 09:27 ET and the entry deadline is 10:30 ET, so the entire research-and-approve
step had to fit inside the entry window. No design document ever stated that window; it was a
consequence nobody wrote down.

The fix is therefore an **authority mapping** change and not a data change. A mover run keeps
its own `trading_date` - it still records the premarket session it observed - and only the
session that consumes it moves forward by one.

## 3. Where the mapping lives

One function forward, one function back, and they are inverses on every XNYS session:

| direction | function | rule |
|---|---|---|
| run -> session that trades it | `app.research.current_run.entry_session_for` | `calendar.next_trading_day(run.trading_date)` |
| session -> run it must read | `app.services.entry_management_runtime.analysis_session_date` | `calendar.previous_trading_day(entry_session_date)` |

Both are source-independent now. The pre-live trade-value scanner ranks session D after D's
close and the mover scan ranks D's premarket at 09:15 ET of D; either way the ranking is
reviewed by a human and consumed on `next_trading_day(D)`.

`MoverLiveEntryLifecycleService` no longer overrides `analysis_session_date`. It overrides
exactly one member with behaviour, `approved_candidates`, which applies the live source filter
to the run selection and changes nothing else about what "approved" means.

## 4. No fallback, in either direction

| boundary | behaviour |
|---|---|
| entry session whose predecessor has no COMPLETED mover run | zero candidates, nothing traded. No legacy `quant_v0` substitution |
| mover run with no active GPT analysis | zero candidates |
| mover run with an analysis but no `APPROVE` | zero candidates |
| UI with the authority bound to the mover source and no mover run | HTTP 404 "No completed scanner run", `NO_SCANNER_RUN`. Not the legacy row |
| GPT import aimed at a run the bound source does not own | refused by `router.require_current_run_target`, writes nothing |

The import guard is the enforcement point for the other end of the chain: it is what prevents a
prompt rendered for one source from binding an analysis that the runtime of the other source
would never read. That is the exact silence that emptied 2026-10-05..10-09.

Any run of any source stays reachable for inspection through an explicit `score_version` or
`scanner_run_id` query. Nothing is reclassified and no stored row is rewritten.

## 5. The configuration

| flag | default | meaning |
|---|---|---|
| `A_MOVER_LIVE_ENABLED` | off | the live mover scan runs and is recorded |
| `A_MOVER_LIVE_ENTRY_AUTHORITY` | **follows `A_MOVER_LIVE_ENABLED`** | entry and the review UI resolve candidates from the mover runs |

The second flag used to default to off, because binding entry to a mover run then meant
relocating the operator's working hours. It no longer does, so the default follows the scan: a
recorded scan whose candidates nothing consumes is not a configuration anyone asks for on
purpose, and defaulting it off would mean that deploying this change does nothing until someone
remembers an environment variable.

Declining is explicit and still supported: `A_MOVER_LIVE_ENTRY_AUTHORITY=false` leaves the
pre-live trade-value source bound to entry, which is the configuration
`backend/tests/test_morning_approval_contract.py` runs under. A value that is neither a true
word nor a false word is not guessed at - `entry_authority_unparsed` names it, `main.py` logs
it, and the authority stays off, which is the side that trades less. Asking for the authority
without the scan is still `entry_authority_misconfigured`, reported and never absorbed.

**Production declares both flags explicitly**: `A_MOVER_LIVE_ENABLED=true` and
`A_MOVER_LIVE_ENTRY_AUTHORITY=true`. The "follows the scan flag" default above only matters for
an environment that omits the second flag; production does not rely on it, so a future change
to the default cannot silently move the authority.

## 6. The production snapshot under the new mapping

Read-only, 2026-10-10. Nothing was written: the `sqlite3` and `-wal` mtimes
(`2026-10-09 22:26:21` and `2026-10-10 07:01:23` KST) are identical before and after.

| run | source | `trading_date` | TOP8 | intended entry session | GPT analysis | HumanDecision | would-be approved |
|---|---|---|---|---|---|---|---|
| 21 | mover live | 2026-10-05 | 6 | 2026-10-06 | none | none | 0 |
| 22 | `quant_v0` | 2026-10-05 | 8 | 2026-10-06 | none | none | 0 |
| 23 | mover live | 2026-10-06 | 4 | 2026-10-07 | none | none | 0 |
| 24 | `quant_v0` | 2026-10-06 | 8 | 2026-10-07 | 21 | 6 APPROVE | 6 |
| 25 | mover live | 2026-10-07 | 2 | 2026-10-08 | none | none | 0 |
| 26 | `quant_v0` | 2026-10-07 | 8 | 2026-10-08 | 22 | 6 APPROVE | 6 |
| 27 | mover live | 2026-10-08 | 3 | 2026-10-09 | none | none | 0 |
| 28 | `quant_v0` | 2026-10-08 | 8 | 2026-10-09 | none | none | 0 |
| 29 | mover live | 2026-10-09 | 8 | **2026-10-12** | none | none | 0 |
| 30 | `quant_v0` | 2026-10-09 | 8 | **2026-10-12** | none | none | 0 |

Two things to read off it.

**Every mover run is `NO_DECISION`.** All five live runs have zero stored analyses and zero
decision rows, so under the new mapping they map to entry sessions that resolve nothing. That is
left exactly as it is: no legacy approval is copied onto a mover run, no analysis is
back-attached and no Paper trade is created for a past session. The six APPROVEs on runs 24 and
26 belong to the `quant_v0` chain and stay there.

**The mapping is now source-independent.** Runs 21/22, 23/24, 25/26, 27/28 and 29/30 are
same-date pairs, and each pair resolves to the same intended entry session. Before this change
the mover row of a pair resolved one session earlier than its legacy twin.

`paper_entry_evaluations` still stops at `(trading_date 2026-10-02, analysis_trading_date
2026-10-01)`, which is the flag flip of 2026-10-04 and is unchanged by this stage.

## 7. What was not touched

`MoverScannerConfig`, the pool size (35), the output maximum (8), the gap bounds, the score
weights, `StrategyConfig`, `StrategyV0Engine`, `RiskEngine`, the execution path, position
management, the exit rules, the Day-2 policy, and E / H / Crypto. `features.py`, `baseline.py`
and `bootstrap.py` keep their own `entry_session_date` parameter name, which in the scanner's
own vocabulary means the session being scanned - the 20-session baseline lookback still ends at
the scan session, and must, because the scan is still of session D.

## 8. Tests

| file | what it pins |
|---|---|
| `backend/tests/test_mover_next_session_authority.py` | this contract: the mapping on the real XNYS calendar including every 2026 session, holidays, both DST boundaries, the resolver through the Korean review day, the import and decision binding, no fallback onto `quant_v0`, and one APPROVE through the deployed gate to a `SimBroker` position |
| `backend/tests/strategy_a_mover_live/test_a_live_ui_approval_chain.py` | the row-selection failure, over the deployed HTTP endpoints, now with analysis and entry on different sessions |
| `backend/tests/test_morning_approval_contract.py` | the pre-live source under the explicit opt-out, end to end |
| `backend/tests/strategy_a_mover_live/test_a_live_paper.py` | APPROVE-only injection, the inherited date rule, and the one place where the unfiltered pre-live loader and the mover source meet |

Known pre-existing breakage, unrelated and not introduced here: 20 test modules fail to import
`SettlementAction` from `app.services.entry_management_runtime`. That name does not exist at
HEAD either, and this stage does not modify that file.

## 9. The operator's day

| step | when (KST) | who | what |
|---|---|---|---|
| 1 | D 22:15-22:27 | system | mover run D is cut at 09:15 ET and COMPLETED |
| 2 | D+1 09:00-18:00 | operator | open the review UI. It shows **Analysis Session D** and **Entry Session `next_trading_day(D)`** for the current mover run |
| 3 | same | operator | copy the GPT research prompt, run it in ChatGPT by hand |
| 4 | same | operator | import the returned JSON. The import is refused if it targets a run the bound source does not own |
| 5 | same | operator | APPROVE / REJECT each symbol. No decision = `NO_DECISION`, nothing is traded, and that is not a strategy failure |
| 6 | D+1 22:45-23:30 | system | the entry runtime reads the run of `previous_trading_day(E)` and evaluates the APPROVE rows only, 09:45-10:30 ET |
| 7 | after | operator | read the Paper result (evaluations, positions, PnL) |

After 22:27 KST of D+1 the review board moves on to the next mover run; the runtime does not
read the board, it reads the run through the mapping in section 3, so the night's entry is
unaffected. Pinned by `test_mover_next_session_authority.py`.

## 10. Data the scan depends on

All three inputs are read from `<repo>/data/runtime/strategy_c/raw/`, where `<repo>` is the
attaching process's tree (`/root/usb_runtime/strategy_e_paper/src` in production, mirrored by
hardlink into `/root/usb`).

| input | producer | schedule | ledger | runbook |
|---|---|---|---|---|
| grouped daily (D-1 close, 20-session ADV) | `usb-grouped-daily.service` / `.timer` (`app.dev.collect_grouped_daily`) | **Mon..Fri 00:40 America/New_York**, Persistent | `data/runtime/ops/grouped_daily/runs.jsonl` | `A_GROUPED_DAILY_REFRESH_V1.md` |
| splits (same-morning split prune) | `usb-splits-refresh.service` / `.timer` (`app.dev.collect_splits`) | **daily 00:55 America/New_York**, Persistent, rolling window `[today_ET - 7d, today_ET + 14d]` | `data/runtime/ops/splits/runs.jsonl` | `A_SPLITS_REFRESH_V1.md` |
| reference (active common stocks) | **no automated producer.** One snapshot, `tickers/CS_2026-10-01.json.gz` | manual | - | `A_GROUPED_DAILY_REFRESH_V1.md` section 5 |

A missing prior grouped-daily session refuses the run (`NO_GROUPED_DAILY`, fail closed). The
reference snapshot is taken as "newest on or before the session", so it ages rather than
expires: new listings are missed and delisted names linger until a new snapshot is placed.

## 11. Tests that protect this contract

| test | protects |
|---|---|
| `backend/tests/test_mover_next_session_authority.py` | `entry_session_for` / `analysis_session_date` inverse on every 2026 XNYS session, holidays, DST, Korean review day, no `quant_v0` fallback, APPROVE -> `SimBroker` |
| `backend/tests/strategy_a_mover_live/test_a_live_ui_approval_chain.py` | UI/API current run = runtime run, over HTTP |
| `backend/tests/test_current_run_authority.py` | the single current-run resolver |
| `backend/tests/strategy_a_mover_live/test_a_live_paper.py` | APPROVE-only injection, live source filter |
| `backend/tests/strategy_a_mover_live/test_per_symbol_readiness.py`, `test_a_live_baseline*.py`, `test_a_live_mixed_baseline.py` | per-symbol 20-session readiness, bootstrap + forward baseline |
| `backend/tests/strategy_a_mover_live/test_a_live_scanner.py`, `test_a_live_handoff.py`, `test_a_live_contract.py` | live checksum, TOP35 -> actionable -> TOP8 |
| `backend/tests/test_mover_scanner_v1.py`, `_v1_1.py`, `_v1_2.py` | the frozen research parent (pool 35, handoff checksum) |
| `backend/tests/test_collect_grouped_daily.py`, `test_collect_splits.py` | data automation: idempotency, validation, quarantine |
| `backend/tests/test_morning_approval_contract.py` | the explicit opt-out (`A_MOVER_LIVE_ENTRY_AUTHORITY=false`) end to end |
| `backend/tests/test_research_stage4.py`, `test_entry_management_runtime.py`, `test_multi_approval_entry_caps.py`, `test_market_calendar.py`, `test_sim_broker_stage6.py` | GPT import, HumanDecision, entry runtime, calendar, SimBroker |

`test_research_overnight_authority.py` and about twenty other modules fail at import on
`SettlementAction`, which does not exist at HEAD. That breakage predates A's authority work.

## 12. Do not

- revert entry authority to legacy `quant_v0`, or add any `quant_v0` fallback
- require a same-session or same-night GPT/approval (the 22:27-23:30 KST window is retired)
- call GPT automatically, or approve automatically
- intersect or union legacy and mover candidates without a new research stage
- change scanner thresholds, pool 35, TOP8, score weights or gap bounds without a research stage
- backfill past analyses, approvals, evaluations or trades onto old mover runs
- treat `NO_DECISION` as a strategy failure
- change Strategy V0, Risk V0 or exit rules as part of scanner or data work
- edit a mover run's `trading_date`: it records the observed premarket session; only the consuming session moves
