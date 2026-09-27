# Strategy H — H0.5 Historical Universe + PIT Market Cap Contract V1

- declaration date: 2026-09-27
- status: **FROZEN BEFORE H0.5 PILOT RESULTS**
- parent: H0 `INCONCLUSIVE`, H1 `NOT AUTHORIZED`
- machine contract: `h0_5_historical_universe_market_cap_contract_v1.json`

H0.5 asks whether the investable US common-stock universe and frozen market-cap lane can be
reconstructed at a historical decision session `T`. It computes no return, factor score, alpha,
valuation target, entry/exit, portfolio, or trading result. It does not amend the H0 contract.

## 1. Fixed decision sessions

The returns-free date pilot uses exactly these XNYS sessions:

```text
2021-06-30
2022-06-30
2023-06-30
2024-06-28
2025-06-30
```

If a listed date is later found not to be an XNYS session, use its immediately preceding XNYS
session and record both dates. No date may be changed because of market performance or coverage.

## 2. Identity contract

Ticker is an effective-dated attribute, never a permanent identifier.

```text
security_id = provider share_class_figi when present
              else provider composite_figi when present
              else UNKNOWN (ticker alone is insufficient)
company_id  = zero-padded SEC CIK when present, else UNKNOWN
mapping     = security_id, company_id, ticker, effective_from, effective_to,
              source, source_known_at
```

A ticker change preserves security identity only when an authoritative event chain or stable FIGI
connects both ticker intervals. CIK alone does not prove that two share classes are the same
security. An acquisition, bankruptcy, or delisting ends the security interval; it does not erase
the interval before the event. Ticker reuse by a different security must create a new interval.

## 3. Historical universe rule

At `T`, a row is eligible only if all are proven by records known no later than `T`:

1. `effective_from <= T < effective_to` (open end allowed);
2. listed and active/tradable on `T`;
3. market `stocks`, locale `us`, type exactly `CS`;
4. primary exchange in `XNYS`, `XNAS`, or `XASE`;
5. stable `security_id` and CIK exist;
6. an unadjusted regular-session close exists on `T`;
7. a qualifying PIT shares fact exists under §5.

Current ticker lists, current `active`, current exchange, current CIK/FIGI, and current market cap
may not be projected backward. Missing or conflicting evidence produces `UNKNOWN / INELIGIBLE`.
Inactive securities must remain eligible before their effective delisting date and become excluded
on and after it. Halt, delisting reason, acquisition, and bankruptcy are recorded when sourced;
absence of such a flag is not evidence that none occurred.

## 4. Filing availability session

SEC `acceptanceDateTime` is converted to `America/New_York`. A filing becomes usable at the first
XNYS regular-session open strictly after its acceptance timestamp, except that a filing accepted
before a session's regular open is usable at that open. Therefore an acceptance during or after a
regular session is usable from the next XNYS session. A filing accepted at 17:30 ET is never usable
for that day's close. Holidays and early closes use the exchange calendar. Date-only `filed` is not
a substitute for acceptance time.

## 5. PIT shares outstanding

Market-cap shares are point-in-time shares, not period-average shares.

- primary SEC concept: `dei:EntityCommonStockSharesOutstanding`;
- provider historical `share_class_shares_outstanding` may be used only when the endpoint is
  explicitly dated as of `T` and its historical semantics are verified;
- `WeightedAverageNumberOfSharesOutstanding` and diluted weighted-average shares are forbidden
  for market cap;
- `CommonStocksIncludingAdditionalPaidInCapital...` is an equity amount, not a share count, and is
  forbidden for market cap.

For an SEC fact, `instant <= T`, accession acceptance must pass §4, unit must be `shares`, and the
fact must describe the eligible security's share class. The fact is valid from its availability
session until the earlier of the next eligible fact or **135 calendar days after its instant**.
Past that limit the value is stale and becomes `UNKNOWN`. No future filing or current shares
fallback is allowed.

If one CIK has multiple eligible common share classes and the SEC fact cannot allocate shares by
class, every affected class is `UNKNOWN`. Treasury shares, weighted averages, or a current provider
value may not repair the ambiguity.

## 6. PIT market-cap rule

```text
MarketCap(T) = unadjusted regular-session Close(T)
               × raw reported PIT SharesOutstanding(T)
```

Both sides remain on the historical share basis. `adjusted price × raw shares` and `unadjusted
price × currently split-adjusted shares` are forbidden. The split ledger is audited around `T`:
on the execution date, price and shares must both be post-split; before it, both must be pre-split.
If the shares fact straddles a split without a provable conversion, market cap is `UNKNOWN` until a
post-split fact becomes available. H0.5 does not synthetically roll shares through a split.

For a non-trading calendar date, use no price and return `UNKNOWN`; the fixed pilot itself is
session-based. Missing close or shares is `UNKNOWN`, never forward/back-filled from current data.

## 7. Market-cap lanes

The H0 lanes remain unchanged:

| Lane | MarketCap(T) |
| --- | ---: |
| MICRO / excluded | `< $0.5B` |
| H-SMALL | `$0.5B <= cap < $2B` |
| H-MID | `$2B <= cap < $10B` |
| H-UPPER-MID | `$10B <= cap < $50B` |
| H-LARGE | `$50B+` |

All lower bounds are inclusive and all finite upper bounds exclusive. Missing market cap has lane
`UNKNOWN`. Lane boundaries and the 135-day staleness limit cannot change after pilot results.

## 8. Provider evidence contract

Massive/Polygon provider bytes, request URL/parameters, request time, plan response/error, and
checksum must be retained. Dated `/v3/reference/tickers` snapshots are membership evidence only for
their requested date. Ticker details with `date=` are not assumed to supply an effective-dated
history beyond that date. `active=false`, ticker events, splits, dividends, and unadjusted aggregate
bars are distinct evidence streams.

Ticker events are corroborating identity evidence, not a complete delisting/M&A/bankruptcy master.
SEC submissions/companyfacts provide issuer and filing knowledge history, not exchange tradability
or a complete security master. No one source is silently promoted beyond its documented meaning.

## 9. Deterministic pilot and survivorship cases

For each fixed date, attempt a small deterministic sample ordered by stable identifier, not return.
Rows report security ID, ticker at T, CIK, listing/exchange/type status, close, PIT shares,
market cap, lane, source, and known-at time. If a full dated snapshot is unavailable under the
current plan, record the endpoint result and do not substitute a present-day sample.

Audit at least one real case where evidence permits for ticker change, acquisition, delisting,
bankruptcy, and still-active security. Each is checked before and after its event. A case without
authoritative effective dates remains `UNRESOLVED`, not a synthetic pass.

## 10. Required tests

Tests cover future ticker-state rejection, historical inclusion before delisting, exclusion on or
after delisting, ticker-change identity, future shares rejection, 135-day stale shares, split-basis
consistency, market-cap multiplication, exact lane boundaries, after-hours filing availability,
missing shares, and rejection of current-market-cap/current-shares fallback.

## 11. Verdict

- **PASS:** survivorship-safe historical universe, PIT shares, PIT cap lanes, and a realistic >=5Y
  daily/actions path are all demonstrated.
- **INCONCLUSIVE:** some paths work but at least one critical component remains unproven.
- **FAIL:** accessible sources cannot realistically construct the historical universe or PIT cap.

PASS does not automatically authorize H1. H0.5 changes require a new preregistration. No push.
