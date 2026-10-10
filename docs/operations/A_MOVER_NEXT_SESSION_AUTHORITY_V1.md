# Strategy A: the mover scan is reviewed in the Korean day and traded the next session

Status: **A_MOVER_NEXT_SESSION_AUTHORITY_READY** (local only; not committed, not pushed, not
deployed). No real order exists anywhere in this stage, no past Paper trade was created, and
the production database was read through a `mode=ro` connection with `PRAGMA query_only=ON`
whose mtimes are unchanged before and after. The production `.env` is untouched.

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

**Deploy impact.** The production `.env` declares `A_MOVER_LIVE_ENABLED=true` and does not
declare `A_MOVER_LIVE_ENTRY_AUTHORITY`. Deploying this change therefore binds entry to the
mover source with no `.env` edit. That is the intended contract, and it is the one decision in
this stage that changes a running process.

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
