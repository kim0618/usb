# E-RT3 — Kiwoom-native RVOL denominator

Status: **E-RT3 PARTIAL — RVOL BOOTSTRAP REQUIRED**. Session: 2026-09-23. The store, the collector,
the daily append and the runtime wiring are built and tested; the history itself is 0.7 % collected.
Frozen rules unchanged. E realtime virtual trading remains disabled.

## 1. The frozen definition, read from the code

Taken from `app/backtest/strategy_e1_premarket/premarket.py` (`session_rows`), never restated by
hand — `rvol_store.py` reads `rvol_window` and `rvol_minimum` out of that function's own signature:

* a session is **staged** when it has at least one bar starting in [04:00, 09:24] **and** a 09:30 bar;
* `pm_dollar_volume` = Σ over those premarket bars of (VWAP when finite, else close) × volume.
  Kiwoom charts carry no VWAP, so the close is used — exactly the frozen fallback branch;
* the **denominator** of session D is the median of the previous **20** staged sessions'
  `pm_dollar_volume` strictly before D, counting only finite values > 0, and undefined below **5**
  history entries;
* `premarket_rvol` = today's premarket dollar volume ÷ that median; NaN when the median is
  undefined or not positive.

A NaN produced by too little history **is the frozen rule**, not missing data. The two are kept
apart by `readiness()`: `RVOL_NORMAL_NAN_BY_RULE` when every prior session was collected and the
rule still yields nothing, `RVOL_DATA_MISSING` when a session in the window was never collected.

## 2. Existing assets: inventoried first, nothing reusable found

| asset | verdict |
|---|---|
| Server table `premarket_volume_sessions` (Strategy A) | **0 rows**; and its semantics differ — share volume, 04:00–09:30 window, scanner candidates only, not dollar volume ≤ 09:24 |
| Common Store / Drive history | Massive only. Massive premarket volume is 0.49–0.68× Kiwoom's on the same sessions (CURRENT_PAPER_BASELINE_V1), so it cannot be the denominator of a Kiwoom numerator |
| `/root/usb_runtime/strategy_b_e1` | ranking probes, no premarket bars |
| E-RT2 dry-run artifacts | status rows only; bars were not persisted |

Coverage over the 2026-09-22 canonical universe (`e_rt3_rvol_coverage_2026-09-22.json`):
**2,561 symbols, 0 with any staged session, 51,220 staged sessions missing.**

## 3. The store

`app/strategy_e_max_rt/rvol_store.py` — SQLite, one row per (symbol, session):
`pm_dollar_volume`, `pm_bars`, `has_open_0930`, `staged`, `source`, `complete`, `checksum`,
`collected_at`. Append-only per key: a second write of the same value is `KEPT`, a different value
is `CONFLICT`, never a silent overwrite. `source` is always `KIWOOM`; Massive is never written.
Measured size: ~300 B per row → ~15 MB for the full 2,561 × 20 window.

`preload(symbols, session)` returns the whole universe's denominators in one pass, so the 09:25
decision path performs no history I/O and no API call for RVOL.

## 4. Measured collection cost (Kiwoom `usa06011`, 2026-09-22/23)

Per symbol, paging back to 20 staged sessions, single worker at the 4.9 req/s limiter:

| symbol | pages | seconds | staged reached |
|---|---|---|---|
| AAPL | 264 | 74.3 | 21 |
| SOFI | 209 | 56.0 | 21 |
| ALB | 110 | 29.7 | 21 |
| PLTR | 256 | 73.3 | 21 |
| CNC | 90 | 25.0 | 21 |
| GEF.B | 307 | 91.4 | 21 |
| BH.A | 326 | 92.1 | **5 — history exhausted** |

Mean 223 pages / 63 s per symbol. Illiquid symbols are **not** cheaper: their prints are spread over
months, so the walk reads as many pages (GEF.B reached 2026-03-19; BH.A ran out of Kiwoom history
entirely after 19 print days and 5 staged sessions).

Pilot with the real collector, 12 symbols, target 5 staged sessions, 2 workers on one API ID at a
4.0/s limiter: **734 pages, 186 s, 3.94 req/s, 0 errors, 2 rate-limit pauses**, 61.2 pages/symbol.

The second lane does not help here. `usa06010` (ticks) returns 100 ticks per page: 60 pages of AAPL
or ALB do not leave the current session, so deep history is a single-lane job on `usa06011`.

Projections for the 2,561-symbol universe at the measured 3.94 req/s:

| target | pages | wall clock | collection windows (18.3 h/day outside 03:55–09:35 ET) |
|---|---|---|---|
| 20 staged sessions (steady state) | ~571,000 | **~40 h** | ~2.2 nights |
| 5 staged sessions (frozen minimum) | ~157,000 | **~11 h** | 1 night |
| daily append only | 2,561/day | ~11 min/day | 20 trading sessions → full window ≈ 2026-10-21 |

## 5. Collector and daily append

`app/strategy_e_max_rt/rvol_bootstrap.py` + `app/dev/run_e_rt3_rvol.py`:

* `coverage` — read-only audit against a canonical universe artifact, no API calls;
* `bootstrap` — **missing-only**: a symbol already at the target is not requested at all; the rest
  are walked most-liquid-first, writing one row per complete session. The **oldest session of a walk
  is never written** (the walk stopped inside it, so its premarket sum would be truncated). A 429
  pauses the symbol's walk and retries the same page; it never abandons it. A wall-clock guard stops
  the collector before 03:55 ET so it can never contend with the premarket path or Strategy A;
* `append` — the daily step after 09:31 ET: one row per symbol, no history rebuild, idempotent.

## 6. Runtime wiring

`run_e_rt2_dryrun.py` now preloads the store before the cutoff and fills `premarket_rvol` from it.
A row whose denominator is absent gets NaN **and** is marked `H5_UNKNOWN` (E-RT2.1 rule 1), so it
can never be selected on missing data, while still counting as an eligible row — the B2 denominator
is untouched. `availability.json` records `rvol_ready_rows`, `rvol_missing_rows`,
`h5_unknown_due_to_rvol` and `enable_blocked_while_rvol_missing`.

The chain store → denominator → rvol → H5 → R1 → B2 → exposure is covered end to end by
`test_decision_reads_the_store_and_unavailable_rows_stay_unknown`, and the store's denominator is
asserted identical to the frozen `session_rows` output over a 25-session tape, including every NaN.

## 7. Enable conditions (none of these is met yet)

1. Coverage: every canonical symbol either at ≥ 5 staged sessions or explicitly recorded as
   history-exhausted at the source (BH.A is the known example).
2. `rvol_missing_rows` = 0 on a live 09:25 cutoff, i.e. no eligible row is `H5_UNKNOWN` for lack of
   a denominator.
3. A live read-only decision dry run at 09:25 with real denominators, repeated on two sessions.
4. The RT2.1 counts (`market_data_unavailable`) reviewed against the frozen gate wording.
5. Paper↔backtest parity re-run with the Kiwoom-native denominator in place.

Until then: `STRATEGY_E_MAX_ENABLED=false`, no orders, Strategy A untouched.
