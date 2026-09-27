# Strategy H — H0 DATA / PIT FEASIBILITY PREREGISTRATION V1

- strategy: **AI QUANTAMENTAL RE-RATING**
- market: US equities
- status: **CANDIDATE / PREVALIDATION**
- stage: **H0 — DATA / PIT FEASIBILITY**
- primary research horizon: **3M**
- declaration date: 2026-09-27
- declaration status: **FROZEN BEFORE H0 PILOT RESULTS**
- machine-readable contract: `h0_data_pit_contract_v1.json`

This is a data-feasibility contract, not an alpha result. H0 must not calculate Strategy H
returns, optimize a factor, select stocks with GPT, or implement entries, exits, a broker, paper
trading, or automatic execution. A H0 PASS does not mean Strategy H alpha passed.

## 1. Research question

For a historical decision time `T`, can USB reconstruct the financial, valuation, reference,
event, and market information that a participant could actually have known at `T`, without using
later filings, restatements, mappings, classifications, corporate actions, or survivor-only lists?

The long-run candidate thesis is:

```text
VALUE + GROWTH + QUALITY + FUTURE BUSINESS + CATALYST + PRICE
```

H0 tests only whether a reliable historical input panel can be built. `FUTURE BUSINESS` and
qualitative `CATALYST` interpretation are not implemented in H0.

## 2. Frozen universe contract

Eligible venues are NYSE, NASDAQ, and AMEX/XASE. The intended security set is common stock.
ETF, ETN, fund, preferred, warrant, SPAC shell, OTC, extreme penny stock, trading-halt or
delisting-risk security, and insufficient-data security are initial exclusions when the historical
reference source can identify them as of `T`. Unknown classification must remain `UNKNOWN`; it
must not silently become common stock.

Current listings alone are not an eligible historical universe. The design must preserve or source
delisted, acquired, bankrupt, ticker-changed, and exchange-moved issuers. CIK or another stable
company identifier is required alongside the point-in-time ticker mapping.

### Market-cap lanes

These boundaries are frozen and may not be changed after feasibility or future outcome results:

| Lane | Historical market cap at `T` | Role |
| --- | ---: | --- |
| MICRO | `< $0.5B` | initial exclusion |
| H-SMALL | `$0.5B <= cap < $2B` | aggressive |
| H-MID | `$2B <= cap < $10B` | primary |
| H-UPPER-MID | `$10B <= cap < $50B` | primary |
| H-LARGE | `$50B+` | secondary |

Primary hypothesis range is `$2B–$50B`; secondary is `$50B+`; aggressive is `$0.5B–$2B`.
Lane membership must use historical point-in-time market cap. Current market cap is forbidden for
historical selection. Preferred construction is split-consistent historical close multiplied by
shares outstanding whose knowledge time is no later than `T`; a provider-supplied historical PIT
market cap is acceptable only when its knowledge-time semantics are documented and audited.

If neither construction is defensible, historical lane selection is an H0 blocker. A current-cap
pilot may test ingestion only and must be marked `NOT VALID FOR BACKTEST`.

## 3. Frozen periods and labels

- minimum historical research period: **5 years**
- preferred historical research period: **7–10 years**
- future primary return horizon for H1–H5: **3M**
- future secondary return horizons: **1M and 6M**

H0 computes no return label. Trading-session alignment, delisting returns, corporate actions, and
the exact 1M/3M/6M label contract must be separately frozen before H1.

## 4. Required raw data

### Fundamentals

| Group | Required raw fields |
| --- | --- |
| Income statement | revenue, gross profit, operating income, net income, EPS |
| Cash flow | operating cash flow, capital expenditure, or directly reported free cash flow |
| Balance sheet | cash, total debt, assets, equity |
| Equity | shares outstanding |

Every fact must retain: ticker as known at the relevant time, CIK or stable issuer identifier,
taxonomy/tag, value, unit, start/end or instant, report period, fiscal period, fiscal year, form,
accession, filing date, knowledge/available time, amendment status, and source provenance.

### Market and reference

Required inputs are daily OHLCV, reference/security type, point-in-time ticker/CIK mapping,
exchange, splits, dividends, listing/delisting status, and trading-calendar/session information.
Minute OHLCV is not required and will not be newly collected for H0.

### Earnings and events

H0 minimally asks whether an earnings announcement can be linked to its historical knowledge time.
Historical/next earnings reconstruction, actual EPS, actual revenue, guidance, and surprise are
audited for availability but missing optional fields do not by themselves invalidate H1–H3.

Analyst forward EPS, forward revenue, estimate revision, and consensus change are optional at H0.
If no PIT source exists, record `H4 REVISION = DEFERRED`; this does not block H1–H3.

## 5. Point-in-time contract

For decision time `T`, a datum is eligible only when its authoritative knowledge time is `<= T`.
For SEC filings, `acceptanceDateTime` is preferred over a date-only filing field. Where only a
date is available, the fact is conservatively unavailable until the next eligible market session;
the implementation must not guess an intraday time.

The following are forbidden:

- filings accepted after `T`, including later comparative facts used as if known at `T`;
- later restatements or amended facts injected into earlier decisions;
- future shares outstanding, sector/industry, earnings dates, analyst estimates, corporate actions,
  ticker mappings, listing status, or delisting status;
- a latest-only companyfacts value propagated through historical dates;
- current survivors used as the complete historical universe.

The H0 store must be append-only in knowledge time. Original `10-K`/`10-Q` facts and later
`10-K/A`/`10-Q/A` facts remain separate versions keyed at least by CIK, accession, concept, unit,
period, frame/context when present, and accepted time. At `T`, resolution may inspect only versions
known by `T`. Amendments do not overwrite the original record.

Duplicate resolution must be deterministic and explainable. It must filter by accession, unit,
instant/duration semantics, form, report period, and knowledge time before applying a documented
tie-break. Missing or ambiguous facts remain missing/ambiguous; no future value may fill them.

Cash-flow facts in 10-Q filings are commonly year-to-date durations. They must not be labeled as a
standalone quarter without a separately frozen, PIT-safe differencing rule.

## 6. Canonical field feasibility

H0 will measure whether these fields can be normalized, not score them:

```text
revenue, gross_profit, operating_income, net_income, eps_diluted,
operating_cash_flow, capex, cash, total_debt, assets, equity,
shares_outstanding
```

For each field the H0 report must record primary and fallback taxonomy tags, sign convention, unit,
instant/duration type, quarter/YTD handling, duplicate selection, and missingness. A fallback is not
accepted merely because it raises coverage; its accounting meaning must match the canonical field.

## 7. Future feature feasibility matrix

H0 records raw-field coverage for, but does not calculate scores or optimize weights over:

- VALUE: earnings yield, FCF yield, EV/EBITDA, EV/sales, price/sales, price/FCF;
- GROWTH: revenue YoY/CAGR, EPS YoY, operating-income YoY, FCF YoY;
- QUALITY: gross/operating/FCF margin, ROE, ROIC, cash conversion, net debt/EBITDA,
  interest coverage;
- MARKET: historical market cap, ADV, relative strength, volatility, 52-week range.

Unavailable EBITDA, interest expense, invested-capital inputs, or other components must produce
`MISSING`/`DEFERRED`, not an invented proxy unless that proxy is frozen before H1.

## 8. Deterministic pilot

If source access permits, H0 uses 30–100 issuers selected without outcome data. The target design is
equal counts by the four eligible market-cap lanes with deterministic ordering and broad industry
coverage. Historical PIT lane assignment is preferred. If unavailable, a current-cap stratified
sample is ingestion-only and prominently marked `NOT VALID FOR BACKTEST`.

Pilot checks are limited to endpoint access, ticker/CIK mapping, 10-K/10-Q and amendment metadata,
report period and acceptance time, canonical normalization, knowledge-time history, and coverage.
H0 must not download the full US issuer history.

## 9. Required H0 audits and tests

The implementation must fail closed on:

1. future filing rejection and the `decision_time` cutoff;
2. original versus amended/restated filing resolution;
3. deterministic ticker/CIK mapping;
4. duplicate fact selection;
5. unit mismatch;
6. instant versus duration and quarter versus YTD handling;
7. missing and ambiguous values;
8. canonical field mapping;
9. future reference/classification/corporate-action poison tests where data permits;
10. survivor-only universe detection or an explicit unresolved blocker.

Coverage and PIT audits run before any future return computation. Tests must never relax the PIT
contract to obtain a pass.

## 10. Planned research sequence

Only after H0 authorizes historical prevalidation may these separate, preregistered studies run:

| Stage | Frozen research theme |
| --- | --- |
| H1 | Value |
| H2 | Value + Growth |
| H3 | Value + Quality |
| H4 | Value + Growth + Quality + Revision |
| H5 | Composite Quantamental Candidate |

No post-hoc factor tuning, factor-weight optimization, universe change, market-cap lane change, or
horizon substitution may rescue a failed study. Any structural alternative requires a new research
ID and a new preregistration before outcomes are inspected.

## 11. H0 decision semantics

Repository-facing readiness and research verdict are reported separately:

| Readiness | Meaning |
| --- | --- |
| READY FOR H0 | required audit/pilot can run with the present source and implementation |
| READY WITH LIMITATIONS | meaningful H0 evidence exists, but named limitations remain |
| BLOCKED | a critical source or PIT construction cannot presently be tested |

| H0 verdict | Meaning |
| --- | --- |
| PASS | a PIT-safe panel suitable for at least five years of quantitative historical research is realistically buildable |
| INCONCLUSIVE | core data are promising, but historical market cap, survivorship, coverage, or another critical audit remains unresolved |
| FAIL | the currently accessible sources cannot realistically produce a PIT historical fundamental panel |

PASS requires evidence for five-year coverage, knowledge-time fundamentals (including amendment
history), PIT historical market-cap construction, and a survivorship-aware universe. A successful
API request or a current-survivor pilot alone cannot produce PASS. Preferred 7–10-year coverage,
analyst revisions, and all optional features may remain limitations if the five-year core passes.

## 12. Storage and provenance

Use the existing USB conventions: provider bytes are immutable raw evidence; normalized facts and
PIT resolutions are separate derived artifacts; manifests include source URL/type, retrieval time,
content checksum, code/contract identity, request accounting, and coverage. Existing C-E0 SEC
submissions and EQM-V0 companyfacts stores may be read and audited; Strategy C remains closed.

Any new H store belongs under `data/runtime/strategy_h/h0/` and is not committed unless repository
policy explicitly says otherwise. H0 must report measured pilot bytes and API calls, plus grounded
5Y/10Y estimates or `UNKNOWN`. Secrets and a personal SEC contact string must not enter Git.

## 13. Scope and prohibitions

H0 does not implement or alter Strategy A/E paper systems and does not reopen B/C/D/F/G. It does
not implement GPT company analysis, Entry1/Entry2, TP1/TP2, valuation targets, broker connectivity,
paper/live trading, or automatic execution. It performs no alpha optimization, weight tuning, or
return-conditioned rule change. Push is prohibited.
