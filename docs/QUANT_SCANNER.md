# USB Quant Scanner V0

## Purpose and scope

Quant Scanner V0 turns a point-in-time universe snapshot into a deterministic,
ranked Candidate Pool and Top 8. It is deliberately a simple research baseline,
not an optimized prediction model. It performs no premarket, strategy, broker,
GPT, or frontend work.

## Universe and Candidate Pool

The Universe is the unique, normalized set of symbols explicitly supplied to a
scan. The Candidate Pool contains only symbols that are active, have available
metadata and sufficient current daily history, pass all three V0 filters, and
produce valid metrics. Filtered symbols remain in `ScannerResult.excluded` with
one deterministic reason, but are not added to the Candidate Pool to fill Top 8.

Run counts distinguish `universe_count`, `excluded_count`, `candidate_count`,
and `top8_count`.

## Reference data

Market capitalization, display name, exchange, and active status come through a
separate `SymbolMetadataProvider`. This avoids turning the bar-oriented
`MarketDataProvider` into an unrelated reference-data interface. Metadata has
its own `observed_at` and `available_at`; the fake implementation returns only
records available at `scan_as_of`.

## Explicit scan time and lookback

Every scan requires `trading_date` and an aware `scan_as_of`. The scanner never
calls the wall clock to decide its result. It requests all Universe symbols plus
the configured benchmark in one market-data query, then independently excludes
every bar whose `available_at > scan_as_of`.

Lookbacks use the benchmark's exact trading-date grid, not array positions:

- RVOL baseline: 20 completed bars before the latest bar.
- Relative Strength: latest close versus the close 5 bars earlier.
- Average Dollar Volume: 20 bars before the latest bar.
- Momentum: latest close versus the close 20 bars earlier.

The benchmark and each stock need the same 21-date available history ending on
`trading_date`. A differing required window is `MISALIGNED_HISTORY`, preventing
missing sessions from lengthening any metric window. Duplicate daily bars are
reduced after the PIT cutoff by greatest `available_at`, greatest `observed_at`,
then a deterministic OHLCV tie-break. Insufficient current SPY history fails the
scan rather than silently using stale or future data.

## Basic filters

V0 thresholds are versioned in `ScannerConfig`:

- latest close >= USD 5;
- market capitalization >= USD 300,000,000;
- average of `close × volume` over the 20 prior bars >= USD 20,000,000.

The current bar is excluded from the liquidity baseline. Premarket gap is not a
filter or score component.

## Raw metrics

For latest bar `t`:

```text
RVOL = volume[t] / mean(volume[t-20:t-1])

stock_5d_return = close[t] / close[t-5] - 1
spy_5d_return   = spy_close[t] / spy_close[t-5] - 1
relative_strength = stock_5d_return - spy_5d_return

latest_dollar_volume = close[t] × volume[t]

momentum_20d = close[t] / close[t-20] - 1
```

The latest day is never included in the RVOL or average-dollar-volume baseline.
Zero RVOL denominator, inadequate history, and non-finite metrics are rejected.

## Normalization and outliers

Normalization is cross-sectional within the filtered Candidate Pool:

1. Apply `log1p` to non-negative RVOL and latest Dollar Volume.
2. Winsorize each component at the deterministic 5th and 95th percentiles.
3. Calculate a population z-score (`ddof=0`).
4. If the population standard deviation is zero, assign zero to every symbol.

Relative Strength and Momentum are winsorized and standardized without a log
transform. Empty and one-member pools, small pools, repeated values, and extreme
outliers do not produce NaN or infinity. Both raw and normalized values are
stored for later research.

## Weight and score version

`quant_v0` uses:

```text
RVOL                 35%
Relative Strength    30%
Dollar Volume        20%
20D Momentum         15%
```

The final score is the sum of the four normalized weighted contributions.
Changing a formula, transform, winsor percentile, or weight requires a new
`score_version`; historical runs must not be silently mixed.

## Ranking and Top 8

Candidates are sorted by:

1. final score descending;
2. normalized RVOL descending;
3. symbol ascending.

Ranks start at 1. The first eight candidates are marked `is_top8`; if fewer than
eight pass, all available candidates are Top 8. Input or database insertion order
is never a tie-breaker.

## Exclusion reasons

V0 reports:

- `MISSING_METADATA`
- `INACTIVE`
- `MARKET_CAP_TOO_LOW`
- `INSUFFICIENT_HISTORY`
- `PRICE_TOO_LOW`
- `LOW_LIQUIDITY`
- `INVALID_MARKET_DATA`
- `MISALIGNED_HISTORY`
- `BENCHMARK_SYMBOL`

Only scored Candidate Pool rows are persisted in `scanner_candidates`. The run
counts and returned exclusions preserve the Universe/Candidate distinction.

## Persistence and transactions

`QuantScanner` calculates without knowing SQLAlchemy. `ScannerService` owns its
transaction: it closes a clean SELECT-only autobegin transaction, rejects a
Session with pending writes, and atomically commits the run, full Candidate Pool,
and COMPLETED status. Any persistence exception rolls everything back. Repeated
calls using the same Session are supported.

`scan_as_of` is solely the data cutoff. Actual wall-clock `started_at` and
`completed_at` are operational metadata and never affect scores. Same-day run
history is preserved; the canonical completed run is greatest `completed_at`,
then greatest `id`, with optional `score_version` filtering. The benchmark is
used for calculation but excluded from candidates if supplied in the Universe.

Each candidate stores rank, Top 8 flag, final score, raw metrics, normalized
metrics, weighted contributions, and the maximum observation/availability time
of the metadata, stock history, and benchmark history used in its score.

## Known V0 limitations

- Daily close and volume only; no intraday or premarket RVOL.
- Cross-sectional z-scores depend on that run's Candidate Pool composition.
- Market capitalization is accepted from the reference provider without
  historical corporate-action reconstruction.
- `float` is appropriate for bulk analytics but not a future money ledger.
- Thresholds, weights, and winsor percentiles are starting definitions and have
  not been tuned for profitability.
- Exclusion details are returned and counted but not persisted as per-symbol
  rows in V0.
