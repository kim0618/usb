# Massive Historical Data Source Spike

NON-TRADING, local only. One symbol (AAPL), 1-minute aggregates, one completed XNYS
session. No database, migration, Parquet store, Kiwoom call, scheduler, or order path.
The spike answers whether Massive Stocks Basic can serve as a historical minute-data
source (premarket included) before any replay or backtest work is designed.

## Secret setup

- Key name: `MASSIVE_API_KEY`, read by `Settings.massive_api_key` (`SecretStr`).
- Location: the repository-root `.env` (git-ignored, mode 600), the same file that
  holds the Kiwoom credentials. `.env.example` only declares the blank name.
- Never in `frontend/`, a `NEXT_PUBLIC_*` variable, a fixture, a log, or a document.
- Missing or blank key: `NOT RUN: MISSING_API_KEY`, `http_requests=0`.

## Official contract (Massive docs, checked 2026-09-15)

| Item | Contract | Source |
| --- | --- | --- |
| Plan | Stocks Basic, free: 5 API calls/minute, 2 years history, End of Day data, minute aggregates included | massive.com/pricing, KB "request limit" |
| Endpoint | `GET /v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from}/{to}`, `from`/`to` = `YYYY-MM-DD` or Unix ms | docs/rest/stocks/aggregates/custom-bars |
| Query | `adjusted` (default true), `sort` asc/desc, `limit` default 5000, max 50000 (counts base aggregates) | same, KB "limit parameter" |
| Fields | `o h l c v vw t n otc`; root `status request_id resultsCount queryCount next_url` | same |
| Timestamp | `t` = "Unix millisecond timestamp for the start of the aggregate window" | same |
| Extended hours | Included by default (pre-market 4:00-9:30, after-hours 16:00-20:00 ET). Most extended-hours trades carry sale conditions that do not update aggregates, so expect fewer bars | KB "pre-market and after-hours" |
| Empty minutes | Omitted when there are no eligible trades | KB "how aggregate bars are created" |
| Pagination | `next_url`, when present, fetches the next page | custom-bars |
| Auth | `Authorization: Bearer <key>` header or `apiKey` query parameter | REST quickstart |

UNKNOWN (not stated in the docs reached): the exact time End of Day data becomes
available, whether `to` is inclusive, the exact 429 body or `Retry-After` behaviour, the
403 body for plan limits, and whether `next_url` ever embeds the key.

Implementation choices that follow from the contract:

- Bearer header only, so no URL ever carries the key; a `next_url` is followed only on
  the same https origin and `/v2/aggs/ticker/` path, with any `apiKey` parameter removed.
- The request covers the whole ET date (Unix ms), `adjusted=false`, `sort=asc`,
  `limit=50000`, at most 3 pages.
- Every attempt passes a 5/minute limiter (12 s spacing). 429 retries twice, waiting
  `Retry-After` capped at 60 s (60 s when absent). Timeout, transport, and 5xx errors
  retry twice with 1 s and 2 s backoff. 401/403 and other 4xx never retry.
- Typed failures: `RATE_LIMITED`, `PROVIDER_TIMEOUT`, `NETWORK_ERROR`,
  `MALFORMED_PAYLOAD`, `NOT_AUTHORIZED`, `PROVIDER_ERROR`, `PAGINATION_LIMIT`,
  `UNTRUSTED_NEXT_URL`.

## Timestamp and session contract

`t` is the bar start, so a bar covers `[bar_start, bar_start + 1 minute)`.
`bar_start + 1 minute` is exposed only as a candidate replay availability boundary; it
is not adopted and no `available_at` is stored, because Basic publication latency is
UNKNOWN.

Sessions are classified from the XNYS calendar in America/New_York, never from a
provider label: PREMARKET `04:00 <= t < open`, REGULAR `open <= t < close`, POSTMARKET
`close <= t < 20:00`, anything else OUTSIDE. The target is the latest XNYS session whose
20:00 ET extended-hours end has passed, so a session in progress is never requested.

## Run

After putting the key in the root `.env`:

```bash
cd /home/tjd618/usb
PYTHONPATH=backend .venv/bin/python -m app.dev.run_massive_spike
```

Bounded multi-symbol validation (at most 3 symbols x 5 sessions; every symbol uses the
same most recent completed XNYS sessions, all through one 5/minute limiter):

```bash
PYTHONPATH=backend .venv/bin/python -m app.dev.run_massive_spike --symbols AAPL AMD ORCL --sessions 5
```

It prints one block per symbol-session (`venue=NOT_IN_AGGREGATES_PAYLOAD`, since the
aggregates response has no exchange field), then a `=== SUMMARY` with counts, per-symbol
averages, and mechanical gates G1, G2, G3, G5 (`PASS`, `FAIL`, or `INCOMPLETE` when a
session could not be fetched). G4 and G6 need operator judgment. A failed session is
counted and the run continues; the exit code is 1 when any session failed.

Optional: `--save-raw` writes sanitized response bodies (no key, header, or request URL)
to the git-ignored `data/runtime/massive_spike/`. Exit code 0 means data was fetched and
validated; 1 is a typed provider failure; `NOT RUN` means nothing was requested.

The report prints request accounting (`http_requests`, `pagination_pages`,
`elapsed_seconds`, `status_codes`, `provider_status`), then row and session counts, first
and last ET timestamps, the 09:30 and 15:59 rows, rows at or after the close, duplicate,
non-monotonic, OHLC, null, negative-volume, zero-volume, unaligned, and off-date counts,
missing regular minutes, and separate premarket data, OHLC, and volume verdicts.
`data_quality` is `CLEAN`, `ANOMALIES:<checks>`, or `NO_DATA`. Missing regular minutes
and zero-volume rows are reported but not failed.
