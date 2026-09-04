# Stage 10B-1 — Real Market Scanner

Audited and implemented on 2026-09-04. This integration is market-data-only;
it contains no account or order operation and keeps `SimBroker` as the only
broker.

## Architecture

```text
Kiwoom usa20540 transaction-amount ranking
  + usa20550 market-cap ranking
  -> bounded acquisition pre-filter (1..100)
  -> KiwoomMarketDataProvider (metadata + daily OHLCV)
  -> existing QuantScanner eligibility and quant_v0
  -> deterministic TOP8
  -> existing ResearchPromptService
```

`FakeMarketDataProvider` and `ReplayMarketDataProvider` are unchanged. The
pre-filter only bounds acquisition. It does not replace the existing $5 price,
$300M market-cap, $20M 20-day average-dollar-volume, history alignment, score,
weights, tie-break, or TOP8 rules.

## Existing Scanner Audit

- `ScannerRun` persists lifecycle, ET trading date, provider, score version,
  and universe/excluded/candidate/TOP counts.
- `ScannerCandidate` persists every eligible candidate, rank, TOP flag, score,
  component snapshot, `observed_at`, and `available_at`.
- Universe was previously supplied by callers (synthetic/replay fixtures); it
  was not part of `MarketDataProvider`.
- Eligibility is active metadata, market cap, 21 aligned daily bars including
  the scan date, price, and 20-day average dollar volume. Missing metadata and
  invalid/misaligned history have explicit reasons.
- Quant remains RVOL 35%, relative strength vs SPY 30%, latest dollar volume
  20%, and 20-day momentum 15%, with winsorized min-max normalization. Ranking
  remains score descending, normalized RVOL descending, symbol ascending.
- No separate exchange, ETF, or ADR business filter exists in Quant V0.
  Acquisition requests `stk_tp=1` (stocks); SPY is fetched only as benchmark.
- A provider failure for a candidate becomes absent history and exclusion;
  benchmark failure still aborts the run. Invalid OHLC rows are counted by
  symbol and never fabricated.

## Universe

The official Kiwoom examples expose the complete US symbol list (`usa10099`,
`/api/us/stkinfo`, `%` or NA/ND/NY), transaction-amount ranking (`usa20540`),
volume ranking (`usa20530`), and market-cap ranking (`usa20550`) under
`/api/us/rkinfo`. Ranking endpoints support NYSE/NASDAQ/AMEX, price, volume,
and transaction-value conditions and continuation headers.

Stage 10B-1 uses transaction-amount rank order as the liquidity-oriented
acquisition pre-filter, deduplicates normalized symbols, retains the exchange
mapping, excludes SPY from candidates, and joins official market-cap ranking
metadata. Full-list support is implemented at the client boundary but is not
used by the default command, avoiding thousands of chart calls. A production
universe over 100 remains intentionally disabled.

## Request Budget

Let `P_r` be the total continuation pages for the two ranking calls and `P_d`
the mean daily-chart pages per symbol. Cold-cache requests are approximately
`P_r + (N + 1) * P_d`; `+1` is SPY. Retry attempts add to this count. At the
conservative 3 requests/second real-mode limiter:

| Candidate limit | Chart symbols | Lower bound (`P_r=2`, `P_d=1`) | Time lower bound |
| --- | ---: | ---: | ---: |
| 10 | 11 | 13 | 4.3 s |
| 30 | 31 | 33 | 11 s |
| 100 | 101 | 103 | 34.3 s |
| Production | deliberately undefined | continuation-dependent | requires measured cache plan |

Kiwoom may return continuation pages, so observed wall time is higher. Provider
memory caches daily results by symbol/exchange/date range and metadata by
symbol/exchange for deterministic reuse in one process. No durable DB cache or
migration was added; production scale therefore remains partial.

## Market Cap

Status: **DIRECT, unit contract PARTIAL**. `usa20550` explicitly returns `mac`
as market capitalization and the live ranking response produced usable values.
USB does not calculate market cap and does not synthesize missing values.
Missing `mac` remains `None` and produces `MISSING_MARKET_CAP`; it is never
silently converted to zero. The public example does not explicitly state the
currency/unit, so production deployment still needs Kiwoom confirmation even
though the live magnitudes supported the small scanner run.

`usa10100` provides symbol/name/exchange/industry/ETF identity but neither
market cap nor shares outstanding. Market cap is therefore not derivable from
that endpoint; the rank endpoint is the current direct source.

## Historical Data and Benchmark

`usa06012` supplies more than the required 21 daily bars and continuation was
previously observed. The adapter canonicalizes bars oldest-first. SPY succeeds
with Kiwoom exchange code `NY`, is fetched once per snapshot, cached, and shared
across all candidates. The Scanner requires exact 21-date alignment and never
forward-fills.

`upd_stkpc_tp=1` is requested, but the public contract does not define enough
corporate-action semantics to prove that it meets production adjusted-history
requirements. Small-run Quant is operational; **production Quant remains
PARTIAL** until adjusted-price semantics are confirmed or an approved second
historical source is introduced.

## Live Small Run

Run at 2026-09-04 00:xx UTC for trading date 2026-09-03 ET with limit 10:

- universe 10; eligible 8; TOP8 8
- TOP8: TSLA, SPCX, META, AVGO, NVDA, MU, AAPL, MSFT
- exclusions: 2 `MARKET_CAP_TOO_LOW`
- canonical mapping issues: DELL 1 invalid OHLC row; no fabricated repair
- provider failures: none on the successful run
- separate DB: `data/runtime/usb_real_market_review.sqlite3`, scanner run 1
- Research Prompt: generated by existing service, 15,445 characters
- Kiwoom order requests: 0

The command is explicit opt-in and defaults to 10:

```bash
RUN_KIWOOM_REAL_SCANNER=1 \
MARKET_DATA_PROVIDER=kiwoom \
BROKER_PROVIDER=simulation \
KIWOOM_MODE=market_data_only \
PYTHONPATH=backend .venv/bin/python -m app.dev.run_real_scanner --limit 10
```

Add `--persist` only to write the separate review DB. Startup never invokes
this command. The maximum CLI limit is 100.

## Operating Time Audit

The V1 lifecycle intends Scanner execution after the US regular close and
before next-day research/premarket gates. The command selects the latest closed
XNYS session in America/New_York; it does not use the Korean calendar date.
There is no scheduler implementation to change in this stage. Running after
regular close (including Korean morning) matches current intent; whether to
wait for postmarket is a Stage 10B-2 session-policy decision.

## Safety

- All three gates must match: `kiwoom`, `simulation`, `market_data_only`.
- `RUN_KIWOOM_REAL_SCANNER=1` is required; its default is false.
- The client allowlist contains only symbol, quote, ranking, and chart TRs.
- No account, FX, order, cancel, or modify endpoint is allowlisted.
- Output contains no credentials, token, or raw payload.
- Live result: Market Data `KIWOOM_REAL`; Broker `SIMULATION`; orders 0.

## Readiness

Real bounded Scanner, existing Quant, TOP8, persistence, Candidates API/UI data
path, and prompt generation are ready. Production-wide scanning remains
partial on durable caching, explicit market-cap unit confirmation, adjusted
price semantics, and final universe breadth/ETF/ADR policy. Stage 10B-2 can
proceed without adding a Kiwoom broker or changing strategy/risk.
