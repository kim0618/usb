# Paper Trading Performance Baseline

## Pre-fix system validation period

| Item | Value |
|------|-------|
| Period | 2026-09-08 ~ 2026-09-11 |
| Classification | `SYSTEM_VALIDATION_PRE_FIX` |
| Orders / fills / positions | 0 |
| Strategy performance | Excluded |

During this period no approved candidate could be evaluated normally. The zero-trade
record is not a market or strategy outcome. Confirmed defects:

- entry session to analysis session mapping (an entry session never consumed its predecessor's analysis);
- morning scanner systemd environment override;
- GPT analysis authority silently replaced by the latest import;
- candidate status leaking across ScannerRuns;
- Kiwoom `cntr_tm` timestamp parsing (raw rows became zero canonical bars);
- minute and daily bar `available_at` tied to provider receipt time;
- Kiwoom daily query base-date direction (the previous-session close was missing).

The 09/09 INTC/AMD and 09/10 AMD/META `PREMARKET_REJECTED / INVALID_PREMARKET_DATA`
states were caused by these data-construction defects. The rows are kept unchanged as
history: nothing is deleted, re-dated, or back-filled.

## Post-fix performance start

POST-FIX PAPER TRADING DAY 1 is **2026-09-14**, the first XNYS session (checked with
`MarketCalendar`) after the Kiwoom pipeline fix (`f2f6cad`) and the max-3 entry risk
contract (`c0794bb`) reached production.

- The contract lives in `backend/app/services/performance_baseline.py`
  (`STRATEGY_PERFORMANCE_VALID_FROM`, `EXCLUDED_PERFORMANCE_PERIODS`).
- `/api/v1/trading/daily-performance` returns every row with `performance_scope` and a
  `strategy_cumulative_pnl` measured from the POST-FIX DAY 1 opening equity (the
  2026-09-11 recorded close).
- `/api/v1/shadow/summary` never counts shadow paths dated in an excluded period.
- The paper account, its cash (7428.92), and `paper_started_at` (2026-09-08) are unchanged.
