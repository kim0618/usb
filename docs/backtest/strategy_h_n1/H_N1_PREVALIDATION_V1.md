# H-N1 Event-Fundamental Swing: PREVALIDATION V1

2026-10-19, local read-only. No forward return, no cohort outcome, no feature distribution was computed.
Verdict: **H-N1 PREVALIDATION = FEASIBLE_WITH_DESIGN_AMENDMENT** (section K).

## A. Reusable assets
| asset | path | state |
|---|---|---|
| SEC companyfacts (XBRL) | `data/runtime/strategy_h_v2/d1_1/sec_raw/companyfacts/` | 4,906 CIK, 692 MB, fetched 2026-09-28; every fact row carries `filed`, `form`, `accn`, `start`, `end` |
| SEC submissions | `strategy_h_v2/d1_1/sec_raw/submissions/` (2,836) + `strategy_c/e0/raw/submissions/` (2,339) | union covers 4,905 / 4,906 companyfacts CIKs; `filingDate`, `form`, `items`, `reportDate`, SIC |
| Universe | `research_universe_u1/reference/tickers/CS_2024-10-25.json.gz` | 5,192 CS rows with CIK (dated at window start, so later delistings stay in) |
| Later reference snapshots | 12 `CS_*.json.gz` up to `strategy_c/raw/tickers/CS_2026-10-01` | for CIK -> ticker changes |
| Daily prices | `strategy_c/raw/grouped/` | Massive grouped daily, **unadjusted** (`adjusted=false`) |
| Splits | `strategy_c/raw/splits/` | 3,328 rows 2024-09-16..2026-10-09 |

## B. Price coverage
519 usable sessions 2024-09-17..2026-10-12 (2024-09-16 is a NOT_AUTHORIZED stub). The 21 missing weekdays are exactly
the XNYS holidays incl. 2025-01-09. SPY present in every sampled session. Price return only (no dividends) for stocks and SPY.

## C. Universe
5,192 rows; 5,093 trade on 2024-10-25, 3,554 of them at >= $5. Same ticker still trading: 4,585 on 2025-10-24, 4,160 on 2026-10-12
(933 gone = delistings + ticker changes; must be resolved by CIK).

## D. XBRL coverage (31,188 10-Q/10-K filings, filed 2024-10-01..2026-10-09, 4,216 CIK; 10-Q 23,343 / 10-K 7,845)
Current-period value with the expected duration (10-Q ~quarter, 10-K ~year) and a PIT prior-year comparable (filed <= event):
| field | current | YoY comparable (PIT) |
|---|---:|---:|
| Revenue (4 tag fallbacks) | 82.8% | 81.8% |
| EPS (diluted, basic fallback) | 94.0% | 93.7% |
| Net income | 99.5% | 99.0% |
| Operating income | 80.2% | 79.8% |
| Operating cash flow | 49.9% | 49.5% |
| CapEx | 34.9% | 34.1% |
CFO/CapEx in 10-Q are year-to-date, so quarterly FCF needs YTD differencing: **FCF = diagnostic only**.
10-K has no Q4 quarter fact; Q4 must be FY minus 9M YTD, or 10-K events use annual YoY (contract must pick one).

## E. PIT
`filed` is on every fact, so "usable after the close of the session after filingDate" is applicable to 100% of rows without
acceptanceDateTime. Prior-year values must be taken as known at the event (latest `filed` <= event), not as later restated.

## F. Event count (estimate, before cohorts)
~31k filings -> price >= $5 (~70%) -> Revenue & EPS YoY (~80%) -> ~17-18k events; 63D-complete (filed <= ~2026-07-10) ~15k.
About 8 earnings seasons in total.

## G. Overlap / clustering
Quarterly filings are ~63 sessions apart, so 63D overlaps the next event by construction; 5D/21D do not.
Events cluster in earnings-season weeks: confidence intervals must be block/cluster bootstrapped by filing week, not iid.

## H. Benchmark
SPY daily in the same grouped store; price return, same dividend omission as the stocks.

## I. Free-data scope
Everything above is local and free. Not available: consensus/surprise (Massive earnings endpoint 403), dividends, delisting returns, analyst revisions.

## J. Data defects
1. **Two-step information (critical).** Of 16,741 10-Q/10-K in the submissions-covered set, 76.9% have an earnings 8-K (Item 2.02)
   within 60 days before; 55.7% of those are filed the same day, lag p75 6 days, p90 18 days. The market reaction happens at the 8-K;
   the XBRL numbers arrive with the 10-Q. A reaction measured at the 10-Q filing date mismeasures the expectation gap for about a third of events,
   and the 8-K numbers themselves are not in XBRL.
2. Grouped daily is unadjusted -> split adjustment required.
3. Ticker changes/delistings (933) -> CIK mapping via later snapshots; delisting return unknown -> last close + flag, report share.
4. Universe fixed at 2024-10-25 -> later IPOs excluded (no lookahead, slight age bias).
5. Two years / ~8 seasons -> "yearly breakdown" is two points; regime robustness is limited by construction.
6. Restatements: companyfacts holds several values per period; use as-known-at-event.

## K. Feasibility
FEASIBLE, but the brief's primary reaction definition must change before freezing:
- information event = 10-Q/10-K filingDate (fundamentals become PIT-usable), entry = close of the next session after it;
- expectation-gap reaction = close(session before the matched 8-K 2.02 filingDate, or before the 10-Q date when no 8-K within 60d)
  -> close(entry session). One window that always contains the market's reaction, whatever the release time.
This keeps the conservative filingDate PIT and does not use acceptanceDateTime.
