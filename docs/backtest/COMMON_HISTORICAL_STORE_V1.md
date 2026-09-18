# US-B Common Historical Store V1 (USB-HIST-V1)

Status: FROZEN snapshot `USB-HIST-V1`, built 2026-09-18. Uncommitted code:
`backend/app/backtest/historical_store/`, CLIs `app.dev.fetch_common_historical_raw` and
`app.dev.build_historical_snapshot`, tests `backend/tests/test_common_historical_store.py`.

## 1. Principle

Provider market data is stored **once** in the shared workspace (`1_US-B`, Google Drive
`G:\내 드라이브\1_US-B` = WSL `/mnt/g/내 드라이브/1_US-B`). Strategies never download their own
copy; each reads a **view** of the same raw:

```
USB-HIST-V1 / V2 / ...  ->  strategy view  ->  strategy features  ->  backtest
```

A longer paid history (5y/10y) extends the same store and becomes a new snapshot id. No
strategy-specific raw copy is ever created.

## 2. Layout (added; nothing legacy moved)

```
1_US-B/
  market_data/
    raw/massive/
      grouped_daily/<YYYY>/<date>.json.gz        C seed, byte copy (wrapped JSON, see 4)
      reference_tickers/CS_<date>.json.gz        C seed, 8 quarterly CS snapshots
      splits/splits_<start>_<end>.json.gz        C seed, market-wide splits
      minute/<SYMBOL>/<SYMBOL>_<start>_<end>.pNN.json.gz + .request.json   provider bytes
      per_symbol_daily/<SYMBOL>/...             provider bytes, ticker-aggregate authority
    normalized/{minute,daily}/massive/...        LEGACY A (unchanged, manifest-owned)
    normalized/grouped_daily/                    reserved (empty)
    metadata/
      historical_snapshot/USB-HIST-V1/           snapshot.json + members + audit (immutable)
      authority/                                 legacy A recorded_authority (unchanged)
      coverage/                                  reserved
  state/historical/CURRENT_HISTORICAL_SNAPSHOT.json   pointer to the frozen snapshot
```

## 3. Daily authority contract (USB-DAILY-AUTHORITY-V1)

| Authority | Endpoint | Stored at |
|---|---|---|
| `MASSIVE_GROUPED_DAILY` | `/v2/aggs/grouped/.../{date}?adjusted=false` | `raw/massive/grouped_daily/` |
| `MASSIVE_TICKER_AGGREGATE` | `/v2/aggs/ticker/{sym}/range/1/day/...?adjusted=false` | `raw/massive/per_symbol_daily/`, legacy `normalized/daily/massive/` |

`GROUPED_DAILY != PER_SYMBOL_DAILY`. They are never merged and never fill each other's gaps. A
view reads exactly one daily authority, and the run identity names it. Evidence (overlap
2025-08-11..2026-09-15, 101 symbols, 27,876 symbol-days): volume differs on 3,853, transactions
on 3,703, vwap on 4,294, high on 22, low on 26, open on 1 and close on 0.

## 4. Raw payload forms

* Minute / per-symbol daily (new): the **exact response body** of every page, gzip (mtime 0).
  The `.request.json` ledger (written last) holds the request, `raw_sha256` of the provider
  bytes, `file_sha256`, rows and status. Missing ledger = not fetched. `NOT_AVAILABLE` = 403
  outside the plan window.
* Grouped / reference / splits (C seed): **wrapped** JSON `{format, session|as_of, body|results}`
  written by the C fetcher with `sort_keys`. They are *not* provider bytes and are kept
  byte-identical to the C cache (no re-serialization). Reference rows keep only the fields the C
  fetcher selected (`TICKER_FIELDS`, no `name`).

## 5. Common Minute Raw contract

* Bars exactly as Massive returned them, `adjusted=false`, 04:00-20:00 ET.
* No silent minute is created. An incomplete session keeps every bar it has.
* No completeness rule at storage time. Completeness is a Session Audit field.
* Provenance per request: symbol, date range, provider, sha256 of provider bytes and file.
* Legacy A `minute_bars` (Parquet, STRICT) stays the frozen Legacy STRICT dataset. It counts as
  existing coverage and is never re-fetched. The snapshot references it read-only.

### Session Audit (`minute_session_audit.parquet`, one row per symbol x session)

`premarket_rows`, `regular_rows`, `postmarket_rows`, `outside_rows`, `expected_regular_rows`
(390, or 210 on an early close), `regular_complete`, `api_loss_suspect` (regular minutes
missing while grouped daily shows >= 10 trades per regular minute), `empty_session`,
`corporate_action_suspect` (split executes that session), `grouped_trades`, `source`
(`LEGACY_STRICT` / `COMMON_RAW`).

Views: **A STRICT** = `regular_complete` sessions only. **B HYBRID-S** = every session with bars,
flags read, nothing filled.

## 6. Strategy mapping

| Strategy | Now | Later |
|---|---|---|
| A | Unchanged. Legacy manifest + `normalized/` Parquet. Baseline identity protected. | Separate adapter migration, regression-gated. |
| B | No stored data (the 9/17 smoke scratch is gone). | Reads Common Minute Raw through a HYBRID-S view. No `sparse_minute_bars` copies kept long term. |
| C | Unchanged, reads `data/runtime/strategy_c/raw`. Drive copy is the canonical backup. | Point `--raw` at the common layout once the C output is shown identical (done for V1, see Regression). |
| D | No collector. Reads common grouped daily, splits and reference only. | Stores only derived data (pattern vectors, neighbours, signals, results). |

## 7. Rules for later snapshots

* A snapshot directory is written once (`snapshot.json` via O_EXCL). Any change is `USB-HIST-V2`.
* Planning subtracts legacy COMPLETE sessions and COMPLETE common ledgers before a request.
* Massive Basic: serial, 13 s spacing, 429 bounded cooldown, 401/403 no retry, end = T-1,
  `adjusted=false`, rolling 2 years (oldest sessions fetched first).

## 8. V1 results (2026-09-18)

* C gate: 502/502 grouped files, 501 usable (2024-09-17..2026-09-16). 2024-09-16 = 403
  NOT_AUTHORIZED (outside the rolling window, not a gap). adjusted=false 501/501, corrupt 0,
  `.partial` 0, duplicate tickers 0, timestamp/session mismatch 0. CS snapshots 8/8, splits 3,328.
* `STRATEGY_C_RAW_FREEZE_V1`: 511 files, 179,041,683 bytes, freeze digest `9ebd6c29…`, C raw
  digest `adc4f191…` (= run `cmsel1-855b6a0ce64e3698fc74`). Drive copy 511/511 sha256 equal.
* `MINUTE_UNIVERSE_V1` = research universe V2 (29) + SPY = 30 symbols. B scope (PIT D-1,
  `ScopeConfig`) would be 4,953 symbols / 1,605,793 symbol-days: not stored in V1.
* Fetch: 245 Massive calls (all HTTP 200, 429 = 0, retries 0). Minute 185 requests /
  4,823,223 rows / 7,197 symbol-sessions; per-symbol daily 60 requests / 6,750 symbol-sessions.
  Nothing already present was requested (post-run plan = 0 requests).
* Coverage (30 symbols x 501 sessions): grouped 100%, reference 100%, splits 100%,
  per-symbol daily 100%, minute 100% of requested (14,898 traded symbol-sessions all have bars;
  132 empty = CRWV before its listing). Regular complete 14,808; incomplete 90 (all COMMON_RAW:
  UNH 69, ORCL 6, CRWV 5, others <= 2; median 2 missing minutes, max 224).
* `USB-HIST-V1`: 1,162 files (1,001 common raw + 161 legacy references), 529,125,463 bytes,
  minute rows 10,487,195, manifest digest `edd41108…`, coverage digest `6a45d6a3…`,
  snapshot.json sha256 `e2a8e5d6…`, FROZEN.
* Regression: A dry-run reproduced `csb1-e22abd32925e4a555c05` with 8/8 artifact sha256 equal.
  C rerun from the Drive copy reproduced `cmsel1-855b6a0ce64e3698fc74`: every artifact byte-equal,
  summary.json differs only in `elapsed_seconds`. Full pytest 2166 passed, 1 skipped.

Note: `api_loss_suspect` fires on all 90 incomplete sessions because every universe symbol is
liquid. With this universe it only means "incomplete regular session". The 10-trades-per-minute
threshold becomes informative once a small-cap (B) universe is audited.
