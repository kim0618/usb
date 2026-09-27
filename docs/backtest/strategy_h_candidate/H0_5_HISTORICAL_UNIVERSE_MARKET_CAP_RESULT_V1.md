# Strategy H H0.5 Historical Universe + PIT Market Cap Result V1

## A. Repository State

- Branch: `main`
- Pre-pilot HEAD: `20328a6d8c0a` (H0.5 freeze); origin comparison at execution: ahead 4, behind 0.
- Authoritative H0 commits match the request: freeze `a5c683c5c53b4c47ce17528ee6de68ee2771019f`, result `f3ac1feb8ec61a9b678210b650bb1debe423f0e1`.
- Pre-existing dirty files were neither edited nor staged. No push was performed.

## B. H0 Input State

H0 remains `INCONCLUSIVE`; H1 remains `NOT AUTHORIZED`. This study addresses only the three H0 blockers: PIT shares/market cap, survivorship-safe security master, and a five-year daily/actions path. It does not revise the H0 contract or evaluate returns, factors, alpha, valuation, entry/exit, or portfolios.

## C. Historical Security Master Findings

Massive's dated ticker details returned historical `ticker`, `type`, `market`, `locale`, `primary_exchange`, CIK, composite FIGI, share-class FIGI, `list_date`, and active status. Its All Tickers endpoint documents an `active=false` route for inactive securities and dated records back to 2003-09-10. This is a plausible security-master seed, but not a complete event ledger: the Ticker Events endpoint is experimental and documents ticker changes, not delisting reason, acquisition, or bankruptcy classification.

SEC is a company filing universe, not an exchange security master. It cannot independently prove historical tradability, exchange interval, security type, delisting, or one-company/multiple-security allocation. SEC-only reconstruction is rejected.

Sources: [Massive All Tickers](https://massive.com/docs/rest/stocks/tickers/all-tickers), [Massive Stocks overview](https://massive.com/docs/rest/stocks/overview), [Massive Ticker Events](https://massive.com/docs/rest/stocks/corporate-actions/ticker-events), [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces).

## D. Ticker / CIK / Security Identity

The frozen contract uses `share_class_figi`, then `composite_figi`, else `UNKNOWN` as `security_id`; CIK is `company_id`. Ticker is a dated attribute with inclusive `effective_from` and exclusive `effective_to`, never a permanent key. Ambiguous/missing intervals are ineligible. A company CIK must not silently allocate company-wide shares across multiple share classes.

The pilot corroborated the rule: FB at 2021-06-30 and META at 2023-06-30 shared `BBG001SQCQC5`, while their dated tickers differed.

## E. Delisted / Acquired / Ticker Change Coverage

The provider proved dated lookup behavior but not event causes:

| Case | Before | After | Result |
|---|---|---|---|
| ticker change | FB 2021-06-30: OK | META 2023-06-30: OK | same share-class FIGI |
| acquired | TWTR 2022-06-30: OK | TWTR 2023-06-30: HTTP 404 | historical inclusion/post-event exclusion demonstrated; acquisition reason not supplied |
| bankrupt/delisted | BBBY 2022-06-30: OK | BBBY 2024-06-28: HTTP 404 | historical inclusion/post-event exclusion demonstrated; bankruptcy reason not supplied |
| active control | AAPL 2021-06-30: OK | AAPL 2025-06-30: OK | stable share-class FIGI |

The current endpoint response returning `active=true` for an old dated record is not, by itself, an effective-dated active-status ledger. Existence and eligibility must be derived from dated snapshots/events with explicit intervals; absence remains `UNKNOWN`, not inferred delisting.

## F. PIT Shares Findings

AAPL SEC submissions and companyfacts were fetched as provider bytes (2 requests, 3,953,090 bytes). Submissions coverage was complete for this test window (earliest 2015-07-28); 44 accepted `dei:EntityCommonStockSharesOutstanding` facts spanned 2015-10-09 through 2026-07-17. Each fixed date had a known, non-stale candidate:

| T | Instant | Shares | Accepted UTC | Age at T |
|---|---:|---:|---|---:|
| 2021-06-30 | 2021-04-16 | 16,687,631,000 | 2021-04-28 22:02:54 | 75d |
| 2022-06-30 | 2022-04-15 | 16,185,181,000 | 2022-04-28 22:03:58 | 76d |
| 2023-06-30 | 2023-04-21 | 15,728,702,000 | 2023-05-04 22:03:52 | 70d |
| 2024-06-28 | 2024-04-19 | 15,334,082,000 | 2024-05-02 22:04:25 | 70d |
| 2025-06-30 | 2025-04-18 | 14,935,826,000 | 2025-05-02 10:00:46 | 73d |

This is one large issuer, so it does not prove universe-wide coverage. `acceptanceDateTime` is preserved by accession. The instant is the fact `end`. Between reports the latest eligible fact remains valid for at most 135 calendar days. Weighted-average shares are forbidden. The former non-share fallback tag was removed from extraction.

## G. Historical Market Cap Contract

Frozen before execution in `H0_5_HISTORICAL_UNIVERSE_MARKET_CAP_CONTRACT_V1`:

`MarketCap(T) = unadjusted regular-session close(T) × raw PIT shares(T)`.

A filing accepted on/before that session's regular open becomes usable that session; otherwise on the next regular session. Non-session decisions normalize to the prior session. A split after the shares instant and through T makes the value `UNKNOWN` unless a reported post-split fact resolves it; synthetic rolling is forbidden. Missing/stale/ambiguous/multi-class shares and missing unadjusted close produce `UNKNOWN`. Current market cap/shares fallback is forbidden. Lane bounds use inclusive lower/exclusive upper: `<0.5B MICRO`, `[0.5B,2B) H-SMALL`, `[2B,10B) H-MID`, `[10B,50B) H-UPPER-MID`, `>=50B H-LARGE`.

## H. Daily / Corporate Action Coverage

The local daily store remains 501 sessions, 2024-09-16 through 2026-09-16, with unadjusted prices. In the live Basic-plan pilot, AAPL daily calls for 2021, 2022, 2023, and 2024 returned `PLAN_TIMEFRAME_NOT_INCLUDED`; 2025 succeeded. The split call did return AAPL's 2020-08-31 4-for-1 event, but this single-symbol success does not establish complete delisted-symbol actions.

Massive documents unadjusted custom bars and historical splits. Current pricing advertises Basic at two years, Starter at five years, Developer at ten years, and Advanced at 20+ years; therefore a paid path appears realistic but was not purchased or proven here. [Custom Bars](https://massive.com/docs/rest/stocks/aggregates/custom-bars), [Splits](https://massive.com/docs/rest/stocks/corporate-actions/splits), [Dividends](https://massive.com/docs/rest/stocks/corporate-actions/dividends), [Stocks pricing](https://massive.com/pricing).

## I. Deterministic Historical Date Pilot

All five preregistered dates are XNYS sessions. The sample is AAPL; source identity is dated Massive reference, shares are SEC accession/acceptance-time facts, and price is Massive `adjusted=false` daily.

| T | Security ID | CIK | listed/type/exchange | Close | PIT shares | Market cap | Lane |
|---|---|---|---|---:|---:|---:|---|
| 2021-06-30 | BBG001S5N8V8 | 0000320193 | yes / CS / XNAS | unavailable (plan) | 16,687,631,000 | UNKNOWN | UNKNOWN |
| 2022-06-30 | BBG001S5N8V8 | 0000320193 | yes / CS / XNAS | unavailable (plan) | 16,185,181,000 | UNKNOWN | UNKNOWN |
| 2023-06-30 | BBG001S5N8V8 | 0000320193 | yes / CS / XNAS | unavailable (plan) | 15,728,702,000 | UNKNOWN | UNKNOWN |
| 2024-06-28 | BBG001S5N8V8 | 0000320193 | yes / CS / XNAS | unavailable (plan) | 15,334,082,000 | UNKNOWN | UNKNOWN |
| 2025-06-30 | BBG001S5N8V8 | 0000320193 | yes / CS / XNAS | $205.17 | 14,935,826,000 | $3,064,380,627,420 | H-LARGE |

No returns were read or calculated. The raw bounded evidence is gitignored at `data/runtime/strategy_h/h0_5/pilot.json`.

## J. Survivorship Audit

FB/META proves ticker mutation without identity mutation. TWTR and BBBY prove a dated provider can find a security before disappearance and reject the old symbol later. AAPL is the still-active control. The sample does not prove reason classification, exchange-change intervals, all M&A forms, or complete inactive-universe enumeration. These remain requirements for a production security master.

## K. Tests

The H0.5 suite covers future ticker-state rejection, historical inclusion/post-delisting exclusion, ticker-change identity, future/stale/missing shares, split consistency, market-cap calculation/no current fallback, exact lane boundaries, after-hours availability, weighted-average exclusion, and multi-class rejection. Full relevant test results are recorded in the implementation commit handoff.

## L. Storage / API / Cost

| Path | Historical universe | Inactive/ticker history | PIT shares/cap | Daily/actions | Cost | PIT reliability / complexity |
|---|---|---|---|---|---|---|
| A. current provider | dated reference works in sample | inactive lookup works; event cause incomplete | provider cap not used | Basic only 2y daily; split sample works | current Basic $0 | medium; reference intervals still needed |
| B. SEC + current price | SEC alone insufficient | no exchange/tradability master | accepted DEI facts can work, with multi-class gaps | current provider still blocks >=5y | $0 plus engineering | medium-low / high complexity |
| C. paid historical/reference | documented dated reference | candidate path, must audit completeness | SEC shares or separately licensed PIT cap | Starter 5y, Developer 10y documented | $29/$79 monthly advertised | potentially higher; not yet empirically proven |

Runtime evidence is isolated under `data/runtime/strategy_h/h0_5`; only bounded sample requests were made (Massive 17 HTTP requests: 11×200, 4×403, 2×404). No bulk universe was downloaded.

## M. H0.5 Freeze Commit

`20328a6` — `research(strategy-h): freeze H0.5 historical universe contract`

Contract checksum: `9b260bd80dc439e25425a107360eaa13804c1478c852de2dee1fb1d228a95c7b`.

## N. Implementation Commit

Recorded after tests and this result document; see repository HEAD in the final handoff.

## O. Blockers

1. Basic plan cannot supply the required five-year daily history.
2. Complete inactive-universe enumeration and effective-dated exchange/security-type intervals were not run end-to-end.
3. Ticker Events does not establish acquisition/bankruptcy/delisting reasons.
4. SEC shares coverage was demonstrated for one issuer only; multi-class allocation remains unresolved.
5. Complete split/dividend/action coverage for delisted symbols is unproven.
6. Paid five-/ten-year paths are documented but not empirically validated with the active entitlement.

## P. Verdict

```text
H0.5 = INCONCLUSIVE
```

Historical reference identity and a conservative SEC PIT-shares method are feasible, but the required >=5Y daily path and complete survivorship-safe universe/action coverage are not proven under current access.

## Q. H1 Authorization

```text
NOT AUTHORIZED
```

A PASS would not auto-authorize H1; this result is INCONCLUSIVE.

## R. Next Action

1. Obtain approval for a time-bounded Starter/Developer entitlement test; do not purchase implicitly.
2. Freeze and run a deterministic inactive-ticker enumeration audit across all five dates.
3. Build explicit security/exchange/ticker effective intervals from dated snapshots and events.
4. Measure DEI shares coverage and staleness over a preregistered 30–100 security sample.
5. Audit multi-class issuers separately; never allocate company shares heuristically.
6. Validate split/dividend/delisted daily completeness over fixed corporate-action cases.
7. Repeat H0.5 only after those evidence gaps close; keep H1 unauthorized meanwhile.
