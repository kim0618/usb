# H-V2 Early Import Footprint P2.1 (result)

Date: 2026-10-16 KST. Runtime/import only: no E1/E2/E3, ordering, MethodJudgement, D3-D6, paper or forward
change; P1 retained; price-pass fusion deferred; no persistent state; not deployed, not pushed. Evidence (local):
`/home/tjd618/h_p2_1/`.

Verdict: **PASS.** The H entrypoint no longer loads pyarrow; every measured output is identical; RSS is 33-45 MB
lower in every SCAN stage (process peak 302.9 -> 258.2 MB).

## 1. Root cause (P2-A)

The morning production failure (2026-10-16 09:06) stalled crypto 41 s after H started, inside the market-gate
price pass, before SEC download and MethodJudgement. The H cgroup reached 172 MB in 50 s while crypto-paper's
resident set fell from 156 to 85 MB into swap; memory and IO PSI rose together. The price pass itself reads 78 MB
at about 2 MB/s and adds about 10 MB of RSS; the process floor is the import: **128-133 MB before any work**.

Import chain (traced with `-X importtime` and an `__import__` hook):

```text
python -m app.dev.run_h_v2_d7
  -> app.market.calendar (MarketCalendar)
       -> exchange_calendars 4.13.2 -> pandas 2.3.3
            -> pandas.compat.pyarrow: try: import pyarrow (optional-dependency probe; loads it when installed)
                 -> pyarrow 23.0.1: 9 shared libraries (101.7 MB) + 11 Python modules (5.8 MB)
```

pandas imports pyarrow eagerly whenever it is installed; it is not needed. `future.infer_string` is False (no
`PANDAS_FUTURE_INFER_STRING`), exchange_calendars has no pyarrow reference, and the only pandas user among H's
modules is `MarketCalendar`. pyarrow/parquet appear only in crypto research code, which H does not import.

## 2. Mechanism and scope

`backend/app/dev/run_h_v2_d7.py`, before any other import:

```python
if __name__ == "__main__":
    sys.modules.setdefault("pyarrow", None)
```

A `None` entry in `sys.modules` is the import system's documented "not available" (import raises
`ModuleNotFoundError`); pandas takes its own `except ImportError` branch (`HAS_PYARROW = False`). No fake module,
no site-packages edit, no environment variable, no unit change.

Scope: only a process started as `python -m app.dev.run_h_v2_d7` - the two H units (`usb-h-evaluation`,
`usb-h-forward`). The backend, tests and any other importer get the module as a library and are unchanged
(control test: the library import still loads pyarrow). A/E/C/crypto do not run this entrypoint.

## 3. Parity

| Check | Result |
|---|---|
| MarketCalendar, every date 2019-01-01..2028-12-31 (3,653 days) x two time zones: `is_trading_day`, `is_holiday`, `session` (open, close, early close), `is_early_close`, `next_trading_day`, `previous_trading_day`, `holding_day_number` (5 spans), including exception messages; plus `range` helpers | hash `8fdf527d...` identical with and without pyarrow (2,209 sessions, 85 holidays, 17 early closes, DST and year boundaries inside the range) |
| 22 issuers (P1 harness: MJ state/reasons/report, D5 panel, decision row, coverage, D5 row, D6 for FG/VRRM/ADI) | combined hash `ce53d68a...` = P1 baseline, pyarrow not loaded |
| Full SCAN dry run, both arms back to back (same store copy, decision time 2026-10-15 01:00 UTC, cap 700) | intake index 707/707 rows equal, 19/19 MethodJudgement equal, `new_candidates.json` equal (same 10, same order, same bodies) |
| H regression (`backend/tests/strategy_h_v2`, pyarrow blocked by a pytest plugin before pandas) | 1,989 passed, 1 skipped, 0 failed |

## 4. Resources (local)

Entrypoint import (`python -m app.dev.run_h_v2_d7 --help`, 7 fresh runs each):

| | before | after |
|---|---|---|
| max RSS (median, min-max) | 133.4 MB (133.3-133.6) | 97.6 MB (97.4-97.8) |
| wall | 0.95 s (0.83-1.05) | 0.70 s (0.69-0.77) |
| minor faults | ~20,140 | ~17,180 |
| file-backed mappings | 96 files, 192.8 MB | 86 files, 91.1 MB |

Full SCAN, stage peak RSS (MB): market tier 148.8 -> 114.8; SEC download 197.1 -> 162.7; price series 251.2 ->
217.7; companyfacts 269.4 -> 236.4; MethodJudgement 302.9 -> 258.2. Bytes read 674 MB / 6,117 files in both arms
(the change does not touch data IO). Durations are network-bound (SEC) and within noise (863 vs 897 s).

Cold-read proxy: up to about 107 MB of pyarrow files are no longer mapped at start (149 MB package on disk); how
many of those pages a cold trader-j start actually read was not measured on the server (a standalone import probe
there on 2026-10-16 13:15 took >55 s cold and raised IO PSI, and was stopped).

## 5. Expected production effect and limits

* The start-up grab drops by about 35 MB and the cold library read by up to about 107 MB - exactly the window where
  the morning run failed. Whether that is enough is unknown until the controlled production run.
* Peak RSS is still about 258 MB against about 290 MB available with crypto-paper partly swapped: later stages may
  still press the host. Price-pass fusion (pass 2/3, 156 MB of reads, series memory) stays deferred.

## 6. Production test plan (not executed)

1. Replace only `backend/app/dev/run_h_v2_d7.py` on trader-j (server copy = local HEAD before P2.1, sha
   `f0983a77...`; after = `a5e38a8d...`); backup first; no restart (both H units start a fresh process).
2. Timer stays OFF; no standalone import benchmark on the server.
3. One canonical `usb-h-evaluation` run in a clean window with the morning observer and auto-stop; watch the first
   2 minutes: H cgroup memory, MemAvailable, crypto-paper resident/swap, memory and IO PSI, crypto latency.
4. Compare with the morning failure (172 MB at 50 s, crypto 14 s at 41 s, 30 s timeouts from 2:40).

## 7. Code

`backend/app/dev/run_h_v2_d7.py` (entrypoint guard). Tests:
`backend/tests/strategy_h_v2/forward/test_h_entrypoint_import_footprint.py` (3: entrypoint without pyarrow, library
import control, calendar identical 2024-2027 with holiday and early-close checks).
