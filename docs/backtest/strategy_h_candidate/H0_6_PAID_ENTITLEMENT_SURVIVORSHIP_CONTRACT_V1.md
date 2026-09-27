# Strategy H — H0.6 Paid Entitlement + Survivorship Coverage Contract V1

- declaration date: 2026-09-27
- status: **FROZEN BEFORE H0.6 LIVE RESULTS**
- parents: H0 `INCONCLUSIVE`; H0.5 `INCONCLUSIVE`; H1 `NOT AUTHORIZED`
- canonical JSON SHA-256: `9467110af5655149f44096856ad497c96d2d567d189f18711edfa60016b95ace`

## 1. Purpose and prohibition

This returns-free audit tests the active Massive Stocks Developer credential and whether a bounded pilot can construct ten-year daily data, dated/inactive membership, corporate actions, SEC PIT shares, and historical cap lanes without lookahead. It does not modify H0/H0.5 contracts. H1, returns, alpha, scoring, optimization, GPT research, valuation, entries/exits, portfolio, trading, broker work, bulk price ingestion, unrelated cleanup, and push are prohibited.

## 2. Fixed sessions and entitlement probes

Universe sessions are exactly 2017-06-30, 2018-06-29, 2019-06-28, 2020-06-30, 2021-06-30, 2022-06-30, 2023-06-30, 2024-06-28, and 2025-06-30. If one is not an XNYS session it becomes the immediately prior XNYS session, with both values recorded. Entitlement is probed with AAPL `adjusted=false` daily requests on one fixed session in every calendar year 2017–2026. Pricing copy is not entitlement evidence; every probe must return provider data for the ten-year gate to pass.

## 3. Historical enumeration and common-stock filter

For each fixed session, the dated reference ticker endpoint is enumerated with market `stocks`, locale `us`, type exactly `CS`, `active=true`, stable pagination bounds, and a retained sanitized ledger. Eligible exchanges are XNYS, XNAS, and XASE. ETF, ETN, fund, preferred, warrant, identifiable SPAC shell, OTC, and unknown type are excluded. A dated snapshot proves membership only at its requested T. Later inactivity never removes earlier membership; absence is `UNKNOWN`, not inferred delisting. At least two securities that later became inactive must occur in a past snapshot.

## 4. Deterministic 40-security sample

Eight mandatory security queries cover controls and structural cases: AAPL, GOOG, GOOGL, TWTR, BBBY, GE, XOM, and BRK.B. FB/META are a separate identity-mutation audit. The remaining 32 securities come from the union of eligible dated snapshots keyed by frozen `security_id`. For each identity retain first observed date/ticker, sort ascending by `SHA256(STRATEGY_H0_6_SAMPLE_V1|security_id|first_seen_date|first_ticker)`, exclude mandatory identities, and take the first 32. Missing mandatory identities remain recorded as missing and are not replaced except by the already frozen hash rule. No result or return may affect selection.

## 5. Daily and actions

Daily requests force `adjusted=false`. Expected sessions are XNYS sessions inside the proven listing interval. Observed bars, missing sessions, first/last bar, pre-event coverage, and post-event absence are recorded. Documented halt/suspension is reported separately and never silently deleted from the denominator. PASS requires at least 98% daily completeness. Splits, dividends, and ticker events are queried for the fixed sample. Every observed split must preserve economic basis between unadjusted close and raw reported shares, or affected cap rows become `UNKNOWN`. Systematic unexplained corruption fails the actions gate.

## 6. PIT shares and market cap

Only `dei:EntityCommonStockSharesOutstanding`, unit shares, instant no later than T, and an accession passing the frozen acceptance-session rule is eligible. Maximum age is 135 calendar days. Future/current/weighted-average fallback is forbidden. Single-class issuer-date coverage must be at least 70%. Unallocated multi-class facts are `MULTI_CLASS_UNRESOLVED`.

`MarketCap(T) = unadjusted regular close(T) × valid PIT raw shares(T)`. Current price, cap, or shares fallback is forbidden. At least 60% of otherwise eligible single-class sample-date rows must reconstruct. Missing or ambiguous input is `UNKNOWN`. Frozen lanes are MICRO <0.5B, SMALL [0.5B,2B), MID [2B,10B), UPPER_MID [10B,50B), and LARGE >=50B.

## 7. PASS / INCONCLUSIVE / FAIL

PASS requires: all ten annual entitlement probes succeed; all nine dated snapshots enumerate; at least two later-inactive historical members are demonstrated; the fixed ticker-mutation identity is stable; daily completeness is >=98%; PIT shares coverage is >=70%; market-cap coverage is >=60%; actions do not systematically corrupt cap; and every missing/ambiguous case fails closed. INCONCLUSIVE means a realistic path exists but any critical condition remains unproven. FAIL means the active entitlement or accessible sources cannot realistically construct the panel. PASS does not authorize H1.

## 8. Evidence and storage

Provider bytes or sanitized responses, exact parameters, timestamps, error codes, checksums, call counts, and byte counts are retained under the gitignored H0.6 runtime root. Reference snapshots are allowed because enumeration is the experiment; full ten-year price bulk download is forbidden. Full ingestion requires a later contract.
