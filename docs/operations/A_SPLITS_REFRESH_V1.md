# Strategy A Splits Refresh V1 (2026-10-05)

> **REFERENCE runbook, in production.** Current A contract: `docs/operations/A_MOVER_NEXT_SESSION_AUTHORITY_V1.md`.

```text
entrypoint  app.dev.collect_splits               backend/app/dev/collect_splits.py
units       usb-splits-refresh.service / .timer  deploy/systemd/
schedule    daily 00:55 America/New_York         Persistent=true, DST-safe
authority   <repo>/data/runtime/strategy_c/raw/splits/splits_<start>_<end>.json.gz
collector   strategy_c_selection.raw_fetch.fetch_splits (unchanged), 1 request per run
```

## Why
`mover_scanner_v1.universe.split_sessions` unions every `splits_*.json.gz` in the store and A
drops a symbol whose split executes that morning. The store ended at 2026-10-09; beyond it the
split map is empty and a split symbol would show a false gap (old-basis close vs new-basis
premarket) as a silent bad candidate, not a refusal.

## Policy (pre-registered, not tuned)
Window `[today_ET - 7d, today_ET + 14d]`, one new file per night, never rewritten. The reader
unions files, so overlapping windows are harmless and a split announced later inside an old window
is picked up by the next night's file. Splits are reference data, so no after-close wait. 00:55 ET
is 15 min behind grouped daily (00:40) and 2h50m ahead of the 03:45 ET paper start.

## Contract
* Idempotency: same-named file is validated and reused (0 requests); never overwritten.
* Validation: format, requested range, rows (ticker, id, execution_date inside the window,
  positive split_from/split_to), duplicate ids, zero events refused. A failing file is moved to
  `strategy_c/raw/quarantine/splits/`; `fetch_splits` writes atomically, so a network failure
  leaves no file.
* Parity: the new window is compared with every older file inside the overlapping dates and the
  difference is recorded (informational; A reads ticker+date only).
* Mirror: hardlink into `--mirror-repo` (`/root/usb`), the other production tree.
* Ledger: `data/runtime/ops/splits/run_<ts>.json` + `runs.jsonl` with window, events, HTTP calls,
  `latest_split_coverage_date`, `current_session`, `days_ahead_covered`. No new A fail rule.
