# Strategy H — H0 DATA / PIT FEASIBILITY RESULT V1

- audit date: 2026-09-27
- frozen contract commit: `a5c683c`
- readiness: **READY WITH LIMITATIONS**
- H0 data feasibility: **INCONCLUSIVE**
- alpha/return calculation: **NOT PERFORMED**
- pilot validity: **NOT VALID FOR BACKTEST**

## A. Current US-B State

Audit start state was `main` at `8a6ec2d8db5d69088e622bf92b6d63ba3844874c`, one commit ahead
of `origin/main`, with no staged files and many pre-existing modified and untracked files. Those
files were not reset, formatted, staged, or included in H commits.

Repository evidence gives the current strategy state:

| Strategy | Actual state |
| --- | --- |
| A | OFFICIAL PAPER V1 / ACTIVE |
| E | OFFICIAL PAPER V1 / ACTIVE (`E-MAX V1`) |
| B | RETIRED / CLOSED |
| C | RETIRED / CLOSED |
| D | RETIRED / CLOSED |
| F | F0 FAIL / CLOSED (`8a6ec2d`) |
| G | G0 FAIL / CLOSED (`cfb29c1`) |

The F/G pattern is preregistration and checksum, immutable input identity, coverage and PIT audit,
evaluation/gate, result, and closeout. H reuses that separation. Existing A/E paper code and all
closed strategies were left unchanged.

## B. Strategy H Definition

`AI QUANTAMENTAL RE-RATING` is a US-equity candidate/prevalidation study with a days-to-months
horizon. Its long-run inputs are Value + Growth + Quality + Future Business + Catalyst + Price.
The primary future research horizon is 3M; secondary horizons are 1M and 6M. H0 asks only whether
historical PIT inputs can be reconstructed. H1–H5 returns, scoring, GPT analysis, entries, targets,
portfolio construction, paper/live trading, and optimization were not implemented.

## C. Required Data

Required fundamentals are revenue, gross profit, operating income, net income, diluted EPS,
operating cash flow, capex, cash, total debt, assets, equity, and shares outstanding. Required
metadata are stable issuer ID/CIK, ticker history, taxonomy tag, unit, instant/duration period,
report/fiscal period, form, accession, filing date, acceptance/available time, amendment status,
and provenance. Market/reference requirements are daily OHLCV, security type, exchange, PIT
ticker/CIK mapping, splits, dividends, listing/delisting status, and market sessions.

## D. Existing Data Reuse

| Data | Path / format | Measured coverage | PIT/reuse assessment |
| --- | --- | --- | --- |
| Daily OHLCV | `data/runtime/strategy_b_e0/mirror/market_data/raw/massive/grouped_daily/`; gzip JSON | 502 files, 501 valid, 2024-09-16..2026-09-16, 5,760,100 rows, 15,995 ticker strings | unadjusted daily bars; reusable but only two years and includes non-common securities |
| Minute OHLCV | same mirror, `minute/<ticker>/`; gzip JSON pages | manifest: 4,121 data pages and 3,913 ledgers, mainly 2026-04-20..09-16 | not required by H; no new minute collection |
| Splits | same mirror, one gzip JSON | 3,328 rows, 2024-09-16..2026-09-16 | reusable only for covered interval |
| Reference details | `data/runtime/research_universe_u1|v2/reference/details/`; gzip JSON | 175 + 190 files, sparse snapshots primarily 2024-10-25 and 2025-09-12 | not a historical universe or ticker-history store |
| SEC submissions | `data/runtime/strategy_c/e0/raw/submissions/`; gzip provider bytes + request ledgers | 2,344 CIK files; manifest reports 2,339 requested/OK and 1,770,876 filing rows | reusable acceptance-time evidence; C remains closed |
| SEC companyfacts pilot | `data/runtime/strategy_h/h0/raw/companyfacts/`; gzip provider bytes + ledgers | deterministic 40 CIK, 40/40 HTTP 200, 157,328,782 downloaded bytes, 10,332,221 stored bytes | ingestion/PIT technical pilot only |
| Prior EQM evidence | code/docs under `strategy_eqm_v0` | docs report an earlier 834-CIK store, but raw `data/runtime/strategy_eqm/` is absent now | design reusable; old coverage is not reproducible from current workspace |
| Market calendar/status | application calendar code and session grids | calendar logic exists; no independent historical halt/status panel found | sessions reusable; status/halt history missing |

No complete local dividends store, earnings-calendar store, analyst-estimate store, historical sector
panel, historical shares panel, or delisted-security master was found.

## E. Missing Data

1. Survivorship-aware historical US security master with list/delist dates, asset type, exchange,
   ticker changes, and stable issuer linkage.
2. A validated PIT shares-outstanding series with the exact acceptance/availability time needed for
   historical market cap.
3. At least five years of local daily OHLCV and split/dividend actions; current local coverage is
   about two years.
4. Historical sector/industry classifications or an explicit policy to treat them as non-PIT.
5. Historical earnings announcement/next-earnings reconstruction, actuals, guidance, and surprise.
6. Analyst estimates/revisions; therefore `H4 REVISION = DEFERRED`.
7. Interest expense and a reliable EBITDA/invested-capital normalization needed by some features.

## F. PIT Risk

SEC companyfacts values are usable only after joining their accession to submissions
`acceptanceDateTime`. A date-only `filed` field is insufficient intraday. The H0 implementation
rejects facts without that join and rejects facts accepted after decision time. Original and
`/A` facts remain separate; the 40-CIK pilot found 498 amended fact rows. Tests prove that the
original is selected before amendment acceptance and the amendment only afterward.

Further high risks remain: companyfacts may include later comparative presentations; historical
ticker/exchange/sector records can be revised; shares outstanding is an instant fact whose report
date and public time differ from the valuation date; 10-Q cash-flow facts are often YTD; and a
latest reference snapshot creates survivorship bias. The H code keeps YTD as duration and does not
pretend it is a standalone quarter. Total debt may require summing current and noncurrent concepts;
the pilot tag coverage is discovery evidence, not a finalized accounting normalization.

## G. Universe

The intended universe is NYSE/NASDAQ/AMEX common stock with the frozen exclusions. The local daily
feed contains ETFs, preferred-like tickers, funds, warrants, and other securities and has no full
historical asset-type classification. Current SEC ticker files are useful for present ticker↔CIK
discovery but the SEC warns that their association files are periodically updated and do not
guarantee accuracy or scope. Consequently a survivorship-safe H universe is not implemented.

## H. Market Cap Lanes

Frozen lanes are MICRO `<$0.5B` excluded; H-SMALL `$0.5B–$2B`; H-MID `$2B–$10B`;
H-UPPER-MID `$10B–$50B`; H-LARGE `$50B+`. Primary is `$2B–$50B`, secondary `$50B+`, aggressive
`$0.5B–$2B`. The code provides a fail-closed `historical close × PIT shares` primitive. The local
stores do not yet prove a complete PIT shares panel or historical membership, so lane construction
is unresolved. The pilot is CIK-order ingestion and explicitly not a lane sample.

## I. Quant Feature Set

Pilot issuer coverage is discovery-only: revenue 39/40, gross profit 29/40, operating income 33/40,
net income 39/40, diluted EPS 37/40, operating cash flow 39/40, capex 31/40, cash 39/40, total-debt
tag candidates 38/40, assets 39/40, equity 39/40, and shares outstanding 36/40.

| Feature | Raw requirements | H0 feasibility |
| --- | --- | --- |
| Earnings yield | PIT price, market cap, earnings | partial; historical cap blocked |
| FCF yield / Price-FCF | OCF, capex, PIT cap/price | partial; OCF/capex gaps and YTD normalization |
| EV/sales | PIT cap, debt, cash, revenue | partial; cap blocked, debt composition incomplete |
| EV/EBITDA | above + normalized EBITDA | missing EBITDA normalization |
| Price/sales | PIT cap and revenue | partial; cap blocked |
| Revenue YoY/CAGR | comparable duration revenue facts | feasible in principle; fiscal-period rules needed |
| EPS/operating income/FCF YoY | comparable duration facts | partial; missingness and YTD differencing |
| Gross/operating/FCF margin | revenue plus numerator facts | partial; gross profit 29/40 is weakest common margin input |
| ROE | net income and equity | feasible in principle; average-equity policy not frozen |
| ROIC | NOPAT and invested capital | missing tax/interest/invested-capital contract |
| Cash conversion | OCF and net income | feasible in principle |
| Net debt/EBITDA | debt, cash, EBITDA | blocked on EBITDA/debt normalization |
| Interest coverage | EBIT and interest expense | interest expense not in H0 canonical pilot |
| Market cap | close × PIT shares | critical blocker |
| ADV/relative strength/volatility/52W range | daily OHLCV | feasible for local two-year interval; 5Y not present |

No feature score or factor weight was calculated.

## J. GPT Role

**NOT IMPLEMENTED.** GPT company analysis, future-business interpretation, catalyst interpretation,
stock selection, and ranking are outside H0.

## K. Earnings / Event Requirements

SEC submissions can timestamp 10-Q/10-K/8-K filings, and the existing C-E0 event store can link
filing acceptance to a market session. That is not equivalent to a complete earnings announcement
calendar: 8-K exhibits may contain results, and announcement time can precede the periodic filing.
No local source was found for historical next-earnings dates, consensus, standardized actual EPS or
revenue, guidance, or surprise. Minimum earnings-announcement linkage therefore remains partial.

## L. Valuation Requirements

Future valuation requires PIT shares/market cap, cash, correctly composed total debt, comparable
revenue/earnings/FCF/EBITDA, units and currency, diluted versus basic shares, split consistency, and
enterprise-value timing. Entry1/Entry2 and TP1/TP2 were not implemented.

## M. Backtest Feasibility

SEC companyfacts supports long histories and the pilot's joined facts span 2010-12-08 through
2026-09-10, so five and 7–10-year fundamental histories are technically plausible. The current
local market panel covers only two years, and historical universe, delistings, PIT shares/market
cap, dividends, and earnings dates are unresolved. Therefore neither 5Y nor 7–10Y Strategy H
backtesting is authorized today. Building them appears realistic, but it has not yet been proved.

## N. Expected Storage / API Cost

Pilot measurement: 40 companyfacts calls, 157.3MB provider bytes, 10.3MB deterministic gzip, or
about 258KB compressed per sampled CIK. A crude 10,000-CIK extrapolation is about 2.6GB compressed
for one cumulative companyfacts snapshot and about 10,000 requests. SEC provides nightly bulk ZIPs,
which should be preferred for a full build. The existing two-year grouped daily store is about
171MB compressed; linear-only estimates are roughly 0.43GB for 5Y and 0.86GB for 10Y, excluding
reference/actions/manifests. Full normalized/PIT sizes and refresh deltas are `UNKNOWN` until schema
and issuer counts are fixed. SEC APIs require no API key; direct access must identify a User-Agent
and stay below the SEC's current 10 requests/second ceiling. Monetary SEC API cost is $0.

## O. H0 Preregistration

- document: `docs/backtest/strategy_h_candidate/H0_DATA_PIT_PREREGISTRATION_V1.md`
- contract: `docs/backtest/strategy_h_candidate/h0_data_pit_contract_v1.json`
- canonical checksum: `afed4de246898c41b7891288ca1b1c4a05c26aaf17444e04d413ae2c60f8af17`
- freeze commit: `a5c683c`

## P. Blockers

1. Historical PIT shares and market-cap lane membership are not proven.
2. Survivorship-safe security master/ticker history/delisting coverage is absent.
3. Local daily market and split data cover about two years, below the five-year minimum.
4. Earnings announcement and dividend histories are absent; sector/industry history is sparse.
5. Canonical debt, quarterly cash flow, EBITDA, interest, and cross-taxonomy normalization need a
   broader representative pilot and accounting rules.
6. The prior 834-CIK EQM raw store referenced by docs is absent, so that prior result cannot serve
   as current raw evidence.
7. Analyst revision data are absent; H4 is deferred.

## Q. Verdict

```text
READY WITH LIMITATIONS
H0 DATA FEASIBILITY = INCONCLUSIVE
```

The pilot proves free SEC ingestion, accession-to-acceptance joins, amendment preservation, and
broad raw fundamental availability. It does not prove the historical investable universe or cap
lanes. This is not an alpha pass and does not authorize H1.

## R. Next Action

1. Acquire/build a dated security master including inactive securities and ticker/CIK history.
2. Run a deterministic 30–100 issuer pilot stratified by the frozen historical cap lanes and
   industry only after PIT cap can be formed.
3. Extend daily OHLCV, splits, and dividends to at least five years and audit delisted coverage.
4. Freeze and test shares-outstanding selection, split alignment, and total-debt composition.
5. Add earnings-announcement linkage and classify unknown versus non-event explicitly.
6. Broaden canonical-tag coverage across filer types and freeze quarter/YTD differencing rules.
7. Re-run H0; begin H1 only if historical cap and survivorship gates pass.
