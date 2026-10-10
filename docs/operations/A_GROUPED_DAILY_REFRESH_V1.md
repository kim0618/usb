# Strategy A Grouped Daily Refresh V1 (2026-10-05)

> **REFERENCE runbook, in production.** Section 5's splits gap is closed by `A_SPLITS_REFRESH_V1.md`. Current A contract: `docs/operations/A_MOVER_NEXT_SESSION_AUTHORITY_V1.md`.

```text
entrypoint  app.dev.collect_grouped_daily          backend/app/dev/collect_grouped_daily.py
collector   a-grouped-daily-refresh/2026-10-05.a
units       usb-grouped-daily.service / .timer     deploy/systemd/
schedule    Mon..Fri 00:40 America/New_York        Persistent=true, DST-safe
authority   <repo>/data/runtime/strategy_c/raw/grouped/<D>.json.gz
```

## 1. What was missing

A's union universe reads the market-wide grouped daily store for every session in the
forty-five-calendar-day window it scans, and `mover_scanner_v1.daily.load_panel` refuses the
whole build when a **prior** session's file is absent (`NO_GROUPED_DAILY`). The session's own
file is allowed to be absent, because a session is published only after it closes.

So A needs one new file per trading day, and nothing in production produced one. The store had
been filled by hand and stopped at 2026-10-02, which made 2026-10-05 the last session A could
build. Measured before the change:

```text
UNI.build(2026-10-05)  union 5015, a 5015, reference as_of 2026-10-01, pruned 221 + 6
UNI.build(2026-10-06)  UniverseUnavailable NO_GROUPED_DAILY: grouped daily is missing for 2026-10-05
```

## 2. Why 00:40 ET, measured not assumed

Stocks Basic does not publish a session until after midnight ET: `collector/range.py` states it
and the collector's end is the previous trading day, always. The availability hour is taken from
evidence, not from the plan documentation. The E universe build sits at 00:20 ET and its eleven
forward grouped files were each written at 00:20-00:22 ET on the session's next calendar day,
seven nights running and across a weekend.

```text
00:20 ET  usb-e-universe   builds E's canonical universe; completes in ~20s (10-02: 20s)
00:40 ET  usb-grouped-daily   this job; one Massive call on a normal night
03:45 ET  usb-e-paper      starts the paper session that attaches Strategy A
```

Twenty minutes behind a job that takes twenty seconds keeps the shared Massive key free, and the
D-1 file lands three hours five minutes before A reads it. `Mon..Fri` covers every session: a
Friday close is collected on Monday, and a holiday Monday collects Friday again as a no-op. The
calendar is XNYS through `exchange_calendars`, so holidays and early closes are the exchange's
own and no wall-clock hour is written into the code.

## 3. Contract

* **Target**: the XNYS previous trading day of *now in America/New_York*
  (`collector.range.previous_trading_day`). A session that has not closed is refused, not
  requested.
* **Plan**: every session A will read for the next trading session whose file is absent, oldest
  first, capped at forty. A missed night is repaired instead of leaving a hole that refuses A
  forever; a long outage can therefore never become a silent bulk pull.
* **Collection**: `strategy_c_selection.raw_fetch.fetch_grouped`, unchanged: serial on the
  5-calls-per-minute limiter, 13s spacing, atomic `.partial` then rename.
* **Store**: the first path `mover_scanner_v1.daily.grouped_path` looks at. No second store.
  `--mirror-repo` hardlinks the file into another tree that already carries the same store, so
  the two production checkouts stay identical at zero bytes.
* **Idempotency**: a file that is already there is validated and **reused**; a re-run costs zero
  requests and never rewrites it. Byte-for-byte comparison against a fresh fetch is impossible
  by construction: the provider stamps a per-call `request_id`, so the same session's file has a
  different sha256 on every fetch while every bar is identical. Reuse-after-validation is the
  idempotency rule, not checksum equality across calls.
* **Validation**: format version, session identity, `adjusted=false`, row count, declared
  `resultsCount`, per-row `T`/`c`/`v` usability, duplicate tickers, SPY presence, sha256, bytes.
* **Failure**: no stale fallback of any kind. A file that fails validation is moved to
  `strategy_c/raw/quarantine/grouped/` and kept, including the `error` stub `fetch_grouped`
  writes when the plan refuses a timeframe: left in place that stub would hand A a session with
  no rows and A would reject every symbol against it silently. The session then stays absent and
  A fails closed with `NO_GROUPED_DAILY`, which is the existing contract. The oneshot is
  isolated, so a failure never touches E, H or Crypto.
* **Evidence**: every run writes `data/runtime/ops/grouped_daily/run_<ts>.json` and one
  `runs.jsonl` line, because this host's journal is vacuumed and a collection failure has to
  outlive it.

`--revalidate` validates every prior grid file rather than the target session alone. It is
deliberately **not** in the unit: a nightly run owns the session it collected, and a validation
rule turning one night into a forty-request refetch is a worse failure than the defect it finds.

## 4. Verification

```text
tests      backend/tests/test_collect_grouped_daily.py   21 passed (local and production venv)
revalidate all 38 prior sessions of the live store valid, 0 invalid, 1.9s
real probe isolated scratch root, 37 sessions hardlinked, 2026-10-02 withheld
           -> 1 HTTP call, COLLECTED, 12,601 rows, SPY present, duplicates 0
           -> every bar identical to the authority copy across c/v/o/h/l/n/t/vw, 0 differing rows
           -> only request_id differs, which is why the file sha256 differs
re-run     NO_WORK, 0 HTTP, file bytes unchanged
unit       systemctl start -> Result=success, exit 0, 2s, ledger written
```

## 5. Not in scope, and the deadline that is not covered

The reference snapshot and the split list are A's other two dated inputs and neither has an
automated producer. `usb-e-universe` does collect both, but into the **forward** store
(`market_data/forward/massive/`), never into `strategy_c/raw/` where A reads them.

* **Reference**: `strategy_c/raw/tickers/CS_2026-10-01.json.gz`, one file.
  `universe.universe_for` takes the newest snapshot dated on or before the session, and the
  series is quarterly by design, so this ages rather than expires: A keeps building, with a
  universe that slowly misses new listings and keeps delisted names.
* **Splits**: `strategy_c/raw/splits/` ends at `splits_2026-10-03_2026-10-09.json.gz`.
  **After 2026-10-09 there is no split data at all**, and `universe.split_sessions` returns
  nothing for new executions, so A stops dropping symbols whose split executes that morning.
  That is not a refusal; it is a silent false gap on exactly the symbols the prune exists to
  remove, because the stored previous close is on the old basis while the premarket prints are
  on the new one. `raw_fetch.fetch_splits` already exists and takes one call per range.
