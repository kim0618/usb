# Stage 10A — Kiwoom Market Data Only Integration Audit

Audited against the Kiwoom REST API portal and the official
`Kiwoom-Securities/Kiwoom-REST-API` specification on 2026-09-03. This stage
does not create a Kiwoom broker and does not call order, account, withdrawal,
or FX endpoints.

## Verdict

The USB provider boundary can accept Kiwoom US market data without changing
Scanner, Quant, Strategy, Risk, or SimBroker semantics. OAuth, HTTP request,
canonical mapping, continuation, bounded retry, timeout, caching, and explicit
read-only endpoint controls are implemented. Live behavior remains unverified
until the operator explicitly supplies credentials and opts into the smoke.

## Existing Architecture Audit

- `QuantScanner` receives `MarketDataProvider` and `SymbolMetadataProvider`.
- `FakeMarketDataProvider` and `ReplayMarketDataProvider` remain unchanged.
- Replay and Scanner independently enforce `available_at <= scan_as_of`.
- Scanner/Quant consume USB `DailyBar`, `MinuteBar`, and `SymbolMetadata`; they
  do not consume provider JSON.
- The only broker implementation is `SimBroker`; no paper/live Kiwoom broker
  exists or is registered.
- Existing dependencies already include `httpx` and `websockets`; no SDK or
  new dependency is required.

## Authentication

| Item | Official contract | Result |
| --- | --- | --- |
| API | `au10001`, `POST /oauth2/token` | Implemented |
| Real domain | `https://api.kiwoom.com` | Configured |
| Mock domain | `https://mockapi.kiwoom.com` | Configured, not tested |
| Request | `grant_type=client_credentials`, `appkey`, `secretkey` | Implemented |
| Response | `token`, `token_type`, `expires_dt` | Validated |
| Lifecycle | reissue when cached token is within 60 seconds of expiry | In-memory only |

`expires_dt` is interpreted as Korea time, matching the official client. The
token and credentials are never logged, serialized to frontend, or persisted.
The portal documents token issue/revoke, not a refresh-token grant; renewal is
therefore a new client-credentials issue.

## Market Data Capability

| Capability | Kiwoom API | USB need | Result |
| --- | --- | --- | --- |
| Symbol/company/exchange | `usa10100`, `/api/us/stkinfo` | metadata | PASS/PARTIAL |
| Current quote/market cap/volume | `usa20100`, `/api/us/mrkcond` | Scanner metadata/latest | PASS |
| Daily OHLCV | `usa06012`, `/api/us/chart` | Quant history | PASS, live depth unverified |
| Alternate daily history | `usa20590`, `/api/us/mrkcond` | audit | Available, not wired |
| Minute OHLCV | `usa06011`, `/api/us/chart` | strategy/recorder | PASS, live smoke unverified |
| Realtime trade | WebSocket `FE` | mark/trade | AUDITED, deferred |
| Realtime 10-level book | WebSocket `FT` | spread/quote | AUDITED, deferred |
| REST 10-level book | `usa20101` | spread/quote | AUDITED, deferred |
| Trade history | `usa20150`, `usa20151` | intraday audit | Available, not wired |
| Session | realtime `FE` field `290` plus timestamps | session semantics | PARTIAL |

### REST fields used

- Daily: `dt`, `open_pric`, `high_pric`, `low_pric`, `cur_prc`,
  `acc_trde_qty`; adjusted-price flag `upd_stkpc_tp=1`, FX application off.
- Minute: `bus_dt`, `cntr_tm`, OHLC, `trde_qty`; one-minute scope,
  unadjusted, FX application off.
- Quote/metadata: `stex_tp`, `stk_cd`, `stk_enm`/`stk_nm`, `mac`,
  `trd_susp_tp`.

Official responses commonly prefix numeric prices with a direction sign. The
mapper converts price magnitudes to positive numeric values and rejects invalid
OHLC. Unknown response fields are ignored.

## Canonical Mapping

| Kiwoom field/API | USB canonical | Classification | Notes |
| --- | --- | --- | --- |
| `stk_cd` | `symbol` | DIRECT | uppercase; dot/dash retained |
| `dt` / `bus_dt` | `trading_date` | DIRECT | ET-aware conversion |
| `bus_dt + cntr_tm` | `timestamp` | DIRECT | `America/New_York` |
| OHLC fields | `open/high/low/close` | DIRECT | validated positive prices |
| `acc_trde_qty` / `trde_qty` | `volume` | DIRECT | non-negative integer |
| latest daily row | `latest_close/latest_volume` | DERIVED | existing Scanner behavior |
| `stk_enm` / `stk_nm` | `company_name` | DIRECT | English preferred |
| `mac` | `market_cap` | DIRECT | unit must be confirmed live |
| `stex_tp` | `exchange` | DIRECT | ND/NASDAQ, NY/NYSE, NA/AMEX |
| ET timestamp | `session` | DERIVED | pre/regular/post boundaries |
| receipt time + market close | `available_at` | DERIVED | aware, no future bar |
| daily OHLCV history | RVOL | DERIVED | volume history |
| symbol + SPY history | Relative Strength | DERIVED | existing formula |
| close × volume | Dollar Volume | DERIVED | existing formula |
| close history | Momentum | DERIVED | existing formula |

Market-cap units, corporate-action completeness, and delisted/security-type
coverage require live payload verification. If `mac` units differ from USB USD
semantics, metadata needs a documented scale or a second source.

## Point-in-Time and Calendar Safety

All canonical datetimes are timezone-aware. Daily rows are treated as observed
at 16:00 ET and a same-day row is excluded before that point. Minute rows are
treated as available no earlier than the next minute and future/incomplete rows
are excluded. Scanner still performs its own `available_at` check. Existing XNYS
calendar remains authoritative for holidays, early closes, and DST; the simple
timestamp session label does not override calendar truth.

## Pagination, History Depth, and Request Budget

Kiwoom continuation uses response headers `cont-yn: Y` and `next-key`; the next
request echoes both. The client caps a query at 10 pages, preventing an
unbounded loop. The official spec does not state a stable rows-per-page or
maximum historical depth, so USB's required 21+ aligned daily observations are
structurally supported but must be confirmed by live smoke.

For universe size `N`, the direct Scanner pattern costs approximately:

`N metadata/quote calls + (N + benchmark) daily chart page calls`.

At the conservative peak-safe query rate of 3 requests/second, 1,000 symbols
requires at least about 667 seconds before continuation pages. Therefore a
large universe **requires caching** and likely batching/universe preselection;
if historical depth or corporate-action quality is insufficient, it requires
an external historical source. Metadata is cached for the provider process;
durable daily caching remains the existing Parquet recorder's responsibility.

## Rate Limits

Official published limits:

- US query TR: 5/sec normally, 3/sec during 09:00–10:00 KST.
- US order TR: 10/sec normally, 3/sec at peak (not used).
- US FX TR: 1/sec (not used).
- All US categories: 50/sec total; chart category: 20/sec total.
- `usa10099` symbol list: 5/minute.
- Mock: each US TR 1/sec.
- One session per account/token; 200 realtime quote symbols per session.

Implementation uses separate auth/query/chart/realtime buckets and limits REST
query/chart traffic to 3/sec for real mode and 1/sec for mock mode.

## Retry, Timeout, and Errors

Every request has a finite timeout. Network errors, timeouts, 429, and 5xx use
bounded exponential backoff (maximum two retries by default). Auth rejection,
permission failure, invalid request, and invalid symbol are not retried. USB
normalizes errors to `AUTH_FAILED`, `RATE_LIMITED`,
`MARKET_DATA_UNAVAILABLE`, `INVALID_SYMBOL`, `PROVIDER_TIMEOUT`, or
`ENDPOINT_BLOCKED`; raw bodies are not returned to UI.

## Realtime Audit

- Real: `wss://api.kiwoom.com:10000/api/us/websocket`; mock uses the matching
  mock host.
- Login packet is `{"trnm":"LOGIN","token":"..."}`. Server `PING` must be
  echoed/answered by the connection client.
- Register uses `trnm=REG`, group number, `refresh`, and data containing item
  objects such as `{jmcode: NVDA, stex_tp: ND}` with type `FE` or `FT`.
- Unsubscribe uses `trnm=REMOVE` with the same group/items/types.
- `refresh=0` replaces existing registrations; `1` retains them, so duplicate
  ownership must be managed by a single connection registry.
- Reconnect requires login followed by deterministic resubscription.
- Only market events `FE` and `FT` are allowed for a future Stage 10A socket.
  Account/order events `F4` and `F5` are expressly excluded.

The official public material does not specify a separate heartbeat interval,
maximum reconnect rate, or a stronger duplicate-subscription guarantee. No
WebSocket implementation or live subscription was added in this stage.

## Symbol and Exchange Audit

Kiwoom requests use a plain ticker plus a separate exchange code. The canonical
regex already permits dotted and dashed class-share symbols, but live checks are
needed for Kiwoom's exact representation of examples such as BRK.B. Frontend
never sees exchange suffixes. Unknown response exchange codes map to `UNKNOWN`
instead of crashing. Because request exchange is mandatory for quote/chart,
production universe construction should retain the exchange mapping from
`usa10099`; Stage 10A accepts an injected mapping and otherwise defaults to the
configured exchange.

## FX/KRW

`ust31301` exposes account/applicable buy and sell exchange rates; quote
`usa20100` also includes `base_exrt`. These are brokerage/account-context rates,
not demonstrated as an independent trusted spot USD/KRW source. Verdict:
**ACCOUNT-SPECIFIC FX ONLY**. Production KRW secondary display should remain
hidden until its trust contract is separately approved. No FX endpoint is
called or allowlisted.

## Account APIs — Audit Only

Official account TRs include cash/deposit (`ust21110`, `ust21160`), positions
and valuation (`ust21070`, `ust21120`, `ust21121`), open orders
(`ust21050`), fills/order history (`ust21150`, `ust21510`), and realized PnL
families (`ust21530`, `ust21630`–`ust21661`). Account access is deferred to
Stage 10B; none is allowlisted or called.

## Order APIs — Audit Only

The official API documents buy `ust20000`, sell `ust20001`, modify `ust20002`,
cancel `ust20003`, plus reservation-order APIs. They use `/api/us/ordr`.
They were not invoked, no request builder was created, and `/api/us/ordr` is
absent from the endpoint allowlist.

## Safety and Configuration

- `MARKET_DATA_PROVIDER=kiwoom` is valid only with
  `BROKER_PROVIDER=simulation` and `KIWOOM_MODE=market_data_only`.
- The market client can call only five exact `(api-id, path)` pairs.
- There is no order method, order client, live broker, or provider registration
  that could switch execution away from SimBroker.
- Credentials use Pydantic `SecretStr`, stay backend-only, and are accepted only
  from ignored local environment files or OS environment.
- Startup and smoke output state `ORDERING DISABLED` and `BROKER: SIMULATION`.

## Live Smoke

Run only with manually supplied environment variables:

```bash
MARKET_DATA_PROVIDER=kiwoom \
BROKER_PROVIDER=simulation \
KIWOOM_MODE=market_data_only \
RUN_KIWOOM_LIVE_SMOKE=1 \
KIWOOM_APP_KEY='<local value>' \
KIWOOM_APP_SECRET='<local value>' \
PYTHONPATH=backend .venv/bin/python -m app.integrations.kiwoom.smoke AAPL --exchange ND
```

The script reports only PASS/FAIL status, never tokens, credentials, accounts,
or raw payloads. Without both opt-in and credentials it exits without network
access. WebSocket smoke remains deferred.

## Missing / Deferred

- Live confirmation of token, quote, market-cap units, daily row depth,
  continuation behavior, adjusted-price semantics, and minute session coverage.
- Durable metadata/quote TTL cache and large-universe request planning.
- A single-owner realtime connection, reconnect/heartbeat, and canonical event
  models.
- Account/portfolio audit smoke (Stage 10B), paper broker, and any live broker.
- UI capability truth remains unchanged until a real smoke succeeds.
