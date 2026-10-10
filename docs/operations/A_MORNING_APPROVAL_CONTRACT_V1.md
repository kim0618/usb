# Strategy A: the morning approval contract, and what the live source did to it

Audited 2026-10-10 (Asia/Seoul) against the production database on `trader-j`, read-only
through a WAL-consistent `sqlite3.Connection.backup()` snapshot. No row was written, no Paper
trade was created, and no real order exists anywhere in this stage. Status:
**A_MORNING_APPROVAL_CONTRACT_RESTORED (local only; not committed, not deployed)**. The
production `.env` is untouched: nothing here changes a running process until someone deploys.

## 1. The original contract, from the rows

The deployed morning Scanner ranks Kiwoom's trade-value board **after the close**, so its
`trading_date` is the session that has just finished and the entry session that consumes it is
the next XNYS session. `usb-morning-scan.timer` fires at **07:00 Asia/Seoul**
(`docs/MORNING_SCANNER.md`), which is 18:01 ET of that session, and renders the GPT prompt from
the run it just wrote. The operator reads it through the Korean day and the approvals are traded
that night. In code the rule is one line:

```
analysis_session_date(calendar, entry_session_date) = calendar.previous_trading_day(entry_session_date)
```

Production used the whole window and never anything else. Every one of the 22 analyses ever
imported was imported on the day *after* its run's `trading_date`, between 00:04 and 13:06 UTC:

| run | `trading_date` | `completed_at` (UTC) | analysis | `imported_at` (UTC) | decisions | intended entry session |
|---|---|---|---|---|---|---|
| 19 | 2026-10-01 | 2026-10-01 22:01 | 19 | 2026-10-02 08:54 | 7 APPROVE | 2026-10-02 |
| 20 | 2026-10-02 | 2026-10-02 22:01 | 20 | 2026-10-03 06:20 | 5 APPROVE | 2026-10-05 |
| 24 | 2026-10-06 | 2026-10-06 22:02 | 21 | 2026-10-07 08:12 | **6 APPROVE** | 2026-10-07 |
| 26 | 2026-10-07 | 2026-10-07 22:01 | 22 | 2026-10-08 06:55 | **6 APPROVE** | 2026-10-08 |

The two rows the earlier audit saw as "10-06 APPROVE 6" and "10-07 APPROVE 6" are therefore
**analysis** sessions, not entry sessions: analysis 21 (AMD, AVGO, INTC, MU, NVDA, SPCX) belongs
to entry session 2026-10-07 and analysis 22 (AMD, MSFT, MU, NVDA, SPCX, TSLA) to 2026-10-08.
`paper_entry_evaluations` records the same pairing from the other side, every session up to the
flag flip: `(10-02, 10-01)`, `(10-01, 09-30)`, `(09-28, 09-25)`, `(09-21, 09-18)`.

## 2. What the mover transition changed

Nothing moved the date arithmetic by accident. `MoverLiveEntryLifecycleService` overrides
`analysis_session_date` to the identity on purpose, and that override is **correct** for the run
it reads: the live cut is taken at 09:15 ET *of the session it trades*, so its `trading_date`
already is the entry session. The regression is not in the arithmetic.

The regression is that **the candidates do not exist when the operator works**. A live TOP8 is a
function of that session's own premarket, which runs 04:00-09:15 ET - 17:00-22:15 KST - so the
earliest moment a live run can be reviewed is its completion at 09:27 ET and the last is the
`entry_deadline_et` of 10:30 ET. That is a **22:27-23:30 KST** review window. The Korean morning
is 18:00-20:00 ET of the *previous* day, before the entry session's premarket has started, so no
amount of date mapping can put a live run's candidates in front of the operator in the morning.

`A_MOVER_LIVE_ENABLED=true` was added to `/root/usb/.env` on 2026-10-04 20:42 KST. It turned on
the scan **and** rebound entry in one flag, and the second half silently relocated the
operator's working hours. The design document (`A_MOVER_LIVE_V1.md` §6) states the override and
its reason; it never states the review window, and its own funnel approves candidates with a
mock. The cost is in the rows: the operator kept reviewing in the Korean day, on the legacy run,
and five consecutive entry sessions resolved nothing.

Reproduced with the two deployed loaders against the production snapshot, read-only:

| entry session | predecessor analysis date | legacy loader | live loader |
|---|---|---|---|
| 2026-10-05 | 2026-10-02 | **5** (AMD, MU, NVDA, SPCX, TSLA) | 0 |
| 2026-10-06 | 2026-10-05 | 0 (run 22 never analysed) | 0 |
| 2026-10-07 | 2026-10-06 | **6** (AMD, AVGO, INTC, MU, NVDA, SPCX) | 0 |
| 2026-10-08 | 2026-10-07 | **6** (AMD, MSFT, MU, NVDA, SPCX, TSLA) | 0 |
| 2026-10-09 | 2026-10-08 | 0 (run 28 never analysed) | 0 |

`paper_entry_evaluations` ends at 2026-10-02, which is the same statement from the other side.

## 3. The two authorities, named

They are not one scanner_run and were never going to be:

**Human research authority** - the morning scanner's run of session D, its GPT analysis and its
HumanDecisions. Available from 07:01 KST, reviewed through the Korean day, consumed by entry
session D+1.

**Live market authority** - the 09:15 ET mover scan of the session being traded. A recorded,
same-session observation of the premarket.

The mover scan is a **candidate authority** in every document that describes it (TOP35 discovery
pool -> StrategyConfig mask -> TOP8 handoff -> GPT -> human). No research or design document
anywhere in this repository describes it as a qualification filter over symbols approved by
another scanner, so joining the two that way would be a **new strategy rule** and is not done
here.

The runtime market qualification the contract asks for is already in the deployed path and needs
no new rule: every approved candidate travels `EntryLifecycleService.evaluate` ->
`StrategyV0Engine` -> `RiskEngine`, so the gap mask, the premarket volume ratio and the
opening-range breakout are read from the **entry session's own** bars at the open. A morning
approval is an admission to be evaluated, never an entry.

## 4. The fix: one flag becomes two

`A_MOVER_LIVE_ENABLED` kept the scan half; the entry half is now its own switch.

| flag | default | what it decides |
|---|---|---|
| `A_MOVER_LIVE_ENABLED` | off | whether the live mover scan runs, ranks and persists its run |
| `A_MOVER_LIVE_ENTRY_AUTHORITY` | **off** | whether entry **and the review UI** resolve candidates from those live runs |

The second never turns on the first: asking for it alone is a configuration error that
`config.entry_authority_misconfigured` names and `app/main.py` logs at startup, and entry keeps
the predecessor contract rather than resolving nothing all session.

`app/research/current_run.py` is the single resolver for "the current run", and it is now
**symmetric**. Whichever source entry is bound to, the review chain resolves a run of that
source and of no other, with **no cross-source fallback in either direction** - a fallback is
exactly the silent mismatch above, a prompt rendered for a run whose approvals nobody reads.
`require_current_run_target` refuses the matching import at the moment it is made instead of
letting it become a no-trade session. Every run of the other source stays reachable through an
explicit `score_version` or `scanner_run_id`, so nothing is hidden and no stored row is
reclassified.

Measured on the production snapshot at a Korean-morning instant (2026-10-10 08:00 KST):

| configuration | current run | entry session the board states |
|---|---|---|
| entry authority **live** (as deployed) | 29 `a_mover_live_v1` 10-09 | 2026-10-09, already past |
| entry authority **off** (restored) | 30 `quant_v0` 10-09 | **2026-10-12**, the next XNYS session |

## 5. Date mapping, including the closures

`next_trading_day` / `previous_trading_day` round-trip over every boundary that matters, so an
entry session never looks for an analysis no scan was stamped with:

| analysis `trading_date` | entry session | why |
|---|---|---|
| 2026-09-18 Fri | 2026-09-21 Mon | weekend |
| 2026-10-09 Fri | 2026-10-12 Mon | weekend |
| 2026-11-25 Wed | 2026-11-27 Fri | Thanksgiving |
| 2026-12-24 Thu | 2026-12-28 Mon | Christmas Day, then the weekend |
| 2026-12-31 Thu | 2027-01-04 Mon | New Year's Day, then the weekend |

## 6. Scope

Not touched: the scanner, the TOP35 pool and the TOP8 handoff, GPT invocation (still manual,
still zero automatic calls), approval (still a human, still `APPROVE` only), entry, risk, exit,
sizing, and the baseline. The frontend is unchanged. No `ScannerRun`, `GPTAnalysis` or
`HumanDecisionRecord` row was written, updated or reclassified.

## 7. Tests

`backend/tests/test_morning_approval_contract.py`, 13 checks over the deployed HTTP endpoints:
the morning run resolves while a live run of the same date is present; the prompt carries its
id; the import activates it; APPROVE one, REJECT one, leave one undecided; the next entry
session's loader returns the one APPROVE and the session after it returns nothing; the board
states analysis session D and entry session D+1 and is READY at 16:00 KST; an import aimed at
the live run is refused and writes nothing; and the approval is qualified by the entry session's
own bars through the deployed gate to `final_status=TRADED`, `POSITION_OPEN` on `SimBroker`.

`backend/tests/strategy_a_mover_live/test_a_live_ui_approval_chain.py` keeps its 12 checks for
the other configuration, now setting both flags.

Pre-existing and unrelated: 20 test modules fail to import because they reference
`SettlementAction`, which exists in neither `HEAD` nor the worktree.
