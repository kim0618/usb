# USB Data Layer

## Scope

Stage 2 provides deterministic, point-in-time-safe market-data plumbing. It does
not calculate scanner scores, make trading decisions, place orders, or call an
external provider.

## Domain model

`DailyBar` and `MinuteBar` are immutable Pydantic models. Pydantic was selected
because provider and file boundaries require runtime validation and predictable
serialization. Symbols normalize to uppercase and allow only 1–32 characters
from `A-Z`, `0-9`, dot, and dash, rejecting separators and traversal syntax.
Models also reject non-finite or non-positive prices, negative volume,
impossible OHLC ranges, and naive datetimes.

`MinuteBar.timestamp` is the bar-open instant. It is distinct from:

- `observed_at`: when USB observed the completed datum.
- `available_at`: when downstream code was allowed to consume it.

`observed_at` cannot precede the minute timestamp, and `available_at` cannot
precede `observed_at`. `DailyBar.trading_date` identifies the exchange trading
date; its observation and availability are represented independently as aware
datetimes.

Datetime inputs may use any IANA timezone. Storage normalizes instants to UTC;
consumers can convert them to the configured `America/New_York` market timezone.
No KST market-time constants are used.

## Price policy

Market bars use Python `float`, matching NumPy, pandas, and Parquet's efficient
columnar representation. Domain validation guards invalid values, but binary
floating-point is not a money ledger. Future order, fill, fee, position-sizing,
and account-value code should define a separate `Decimal`/integer-minor-unit
policy at that boundary. Stage 2 deliberately does not force bulk market data
and financial accounting to share a numeric representation.

## Market sessions

`MarketSession` is a string enum with `PREMARKET`, `REGULAR`, and `POSTMARKET`.
Each minute bar carries its session explicitly, allowing exact filtering and
preservation during replay. Session classification and early-close truth use
New York exchange-local semantics regardless of display timezone; they must not
be inferred from KST or unzoned string comparisons. Strategy behavior by
session is outside Stage 2.

## Provider contract

`MarketDataProvider` is a synchronous ABC with daily and minute range queries.
Both methods accept multiple symbols and return stable time-then-symbol ordering;
minute queries also accept a session filter. A synchronous contract fits local
fixtures and filesystem Parquet without adding an event-loop boundary. A future
network adapter can perform transport concurrency internally while preserving
this contract, or the contract can be deliberately revised when real provider
behavior is measured.

No consumer needs to know whether data came from the fake or replay provider.

## Fake provider

`FakeMarketDataProvider` stores explicitly injected immutable fixtures, making
edge cases readable and repeatable. `minute_fixture` provides deterministic UP,
DOWN, FLAT, GAP_UP, and GAP_DOWN shapes; its volume argument expresses high- or
low-volume cases. Callers can inject arbitrary daily/minute bars when an exact
shape is more useful than the helper. It uses no random state and no network.

## Parquet storage

The local layout is:

```text
data/market/
  daily/YYYY/SYMBOL.parquet
  minute/YYYY/MM/DD/SYMBOL.parquet
```

Yearly per-symbol daily files keep long daily history compact. Day-and-symbol
minute partitions make one-session replay and replacement straightforward while
bounding file size on an ordinary Ubuntu filesystem. Files carry a
`usb.source` schema metadata value for provenance extension without adding
unused bar columns.

Writes use merge-and-atomic-replace. A daily key is `(symbol, trading_date)` and
a minute key is `(symbol, timestamp)`. When a key already exists, the newly
written validated bar replaces the old bar (last write wins), but its
`available_at` may not precede the stored value. Backward daily or minute
corrections raise `DataError` because they would rewrite PIT availability. The
resulting file is de-duplicated and sorted deterministically. Full revision
history remains outside Stage 2.

## Replay and point-in-time safety

`ReplayMarketDataProvider` has an explicit, timezone-aware, immutable
`current_time`. Every query applies the ordinary symbol/range/session filters and
then excludes rows where `available_at > current_time`, even if the market
timestamp is already in the past. `at(new_time)` returns a new view instead of
mutating shared global cursor state.

Input data is sorted by time and symbol. Repeated calls against the same dataset
and replay time therefore return equal sequences. This prevents look-ahead at
the provider boundary; future Strategy and Scanner code must continue to use the
provider rather than reading Parquet files directly.

## Scanner snapshot foundation

SQLite contains two Stage 2 tables:

- `scanner_runs`: trading date, lifecycle status, provider, and score version.
- `scanner_candidates`: the complete eligible pool, linked 1:N to its run.

Candidates have a unique `(scanner_run_id, symbol)` key and a run/rank index.
Rank, score, Top 8 membership, and JSON score components are storage fields only;
Stage 2 computes none of them. A UTC-aware SQLAlchemy type stores timestamps as
ISO-8601 UTC text so SQLite round-trips timezone awareness.

`ScannerSnapshotRepository` creates/completes runs, bulk-adds candidates, and
returns either the full rank-ordered pool or rows marked Top 8.

## Recorder

`MarketRecorder` performs explicit bounded queries from any
`MarketDataProvider`, writes the result through `ParquetMarketDataStorage`, and
returns counts in `RecordingResult`. There is no scheduler or automatic network
collection in this stage. Storage and provider failures are exposed as the
existing `DataError` hierarchy.

## Adding a provider later

A future provider should validate all raw values into `DailyBar`/`MinuteBar`,
assign exchange-calendar-derived sessions, preserve observation/availability
times, implement `MarketDataProvider`, and pass the same provider contract and
recorder/replay tests. No Kiwoom-specific class or behavior exists in Stage 2.
