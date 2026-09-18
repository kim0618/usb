"""What each strategy's 2-year backtest reads, as data (STRATEGY_REQUIREMENTS_V2).

Established by reading the code and the frozen rule documents on 2026-09-18; the evidence column
names the file each row comes from. The grid is the 501 XNYS sessions 2024-09-17..2026-09-16
(index 0 = 2024-09-17). ``eval`` is the range a strategy can be *scored* on with the raw data
range it has; it is narrower than the data range when warmup or forward labels need sessions the
grid (or the Basic rolling window) does not hold.

Status values: REQUIRED, OPTIONAL, PLANNED (in a spec, no code yet), NOT_REQUIRED, UNAVAILABLE.
"""

import hashlib
import json

REQUIREMENTS_ID = "STRATEGY_REQUIREMENTS_V2"
GRID = {"start": "2024-09-17", "end": "2026-09-16", "sessions": 501}

MATRIX = {
    "grouped_daily": {"A": "NOT_REQUIRED", "B": "REQUIRED", "C": "REQUIRED", "D": "REQUIRED",
                      "common_raw": True, "authority": "MASSIVE_GROUPED_DAILY",
                      "note": "A only to rebuild its universe (not at run time); B scope (spec 3.2, estimate)"},
    "per_symbol_daily": {"A": "REQUIRED", "B": "REQUIRED", "C": "NOT_REQUIRED", "D": "NOT_REQUIRED",
                         "common_raw": True, "authority": "MASSIVE_TICKER_AGGREGATE",
                         "note": "A scanner (29 + SPY); B scope in code + D/D-1 sparse-session audit"},
    "minute_premarket": {"A": "REQUIRED", "B": "REQUIRED", "C": "NOT_REQUIRED", "D": "NOT_REQUIRED",
                         "common_raw": True, "authority": "MASSIVE_TICKER_AGGREGATE",
                         "note": "A gap reference + PM volume; B return_scope=EXTENDED_DAY reads PM closes"},
    "minute_regular": {"A": "REQUIRED", "B": "REQUIRED", "C": "NOT_REQUIRED", "D": "NOT_REQUIRED",
                       "common_raw": True, "authority": "MASSIVE_TICKER_AGGREGATE",
                       "note": "A STRICT (regular complete); B HYBRID-S (sparse, nothing filled)"},
    "minute_after": {"A": "NOT_REQUIRED", "B": "OPTIONAL", "C": "NOT_REQUIRED", "D": "NOT_REQUIRED",
                     "common_raw": True, "authority": "MASSIVE_TICKER_AGGREGATE",
                     "note": "arrives in the same 04:00-20:00 request; never an entry session"},
    "cs_reference": {"A": "NOT_REQUIRED", "B": "REQUIRED", "C": "REQUIRED", "D": "REQUIRED",
                     "common_raw": True, "authority": "MASSIVE_REFERENCE_TICKERS",
                     "note": "C/D quarterly (latest as_of <= D); B as of D-1 (spec: daily cadence = PLANNED)"},
    "ticker_details": {"A": "REQUIRED", "B": "OPTIONAL", "C": "NOT_REQUIRED", "D": "NOT_REQUIRED",
                       "common_raw": False, "authority": "MASSIVE_TICKER_DETAILS",
                       "note": "A market cap (metadata as_of 2025-09-12, static); B list_date for IPO flag only"},
    "splits": {"A": "NOT_REQUIRED", "B": "REQUIRED", "C": "REQUIRED", "D": "REQUIRED",
               "common_raw": True, "authority": "MASSIVE_SPLITS",
               "note": "A applies no corporate action; B/C/D use execution_date <= t"},
    "ticker_events": {"A": "NOT_REQUIRED", "B": "OPTIONAL", "C": "NOT_REQUIRED", "D": "NOT_REQUIRED",
                      "common_raw": False, "authority": "UNKNOWN", "note": "B symbol-change flag only; no loader"},
    "delisting": {"A": "NOT_REQUIRED", "B": "OPTIONAL", "C": "NOT_REQUIRED", "D": "NOT_REQUIRED",
                  "common_raw": False, "authority": "MASSIVE_REFERENCE_TICKERS",
                  "note": "B flag only; C/D detect disappearance from grouped daily"},
    "benchmark": {"A": "REQUIRED", "B": "NOT_REQUIRED", "C": "REQUIRED", "D": "NOT_REQUIRED",
                  "common_raw": True, "authority": "A: SPY per-symbol daily; C: SPY row of grouped daily",
                  "note": "D uses the universe median, not SPY"},
    "sec_filings": {"A": "NOT_REQUIRED", "B": "NOT_REQUIRED", "C": "PLANNED", "D": "NOT_REQUIRED",
                    "common_raw": False, "authority": "SEC EDGAR (not Massive)",
                    "note": "C-E only; User-Agent undecided; outside this store"},
    "historical_quotes_spread": {"A": "NOT_REQUIRED", "B": "UNAVAILABLE", "C": "NOT_REQUIRED",
                                 "D": "NOT_REQUIRED", "common_raw": False, "authority": "none on Basic",
                                 "note": "B spread check is NOT_EVALUATED"},
    "halt_feed": {"A": "NOT_REQUIRED", "B": "UNAVAILABLE", "C": "NOT_REQUIRED", "D": "NOT_REQUIRED",
                  "common_raw": False, "authority": "none on Basic", "note": "B infers halts from minute gaps"},
}

# Grid indices; dates are resolved against the calendar by the CLI.
RANGES = {
    "A": {"universe": "research universe V2 (29) + SPY benchmark (fixed list)",
          "warmup": "minute: 20 sessions before the first entry E; per-symbol daily: 25 sessions before scanner day D=E-1",
          "eval_start_index": 26, "eval_end_index": 500,
          "forward": "settlement session E+1 (minute); eval end 2026-09-16 needs minute 2026-09-17",
          "pre_grid_needed": "20 minute + 25 daily sessions before 2024-09-17: UNAVAILABLE (rolling window)",
          "evidence": ["backtest/baseline/runner.py:185", "backtest/baseline/coverage.py:93-124",
                       "backtest/baseline/contract.py:43-45", "services/entry_management_runtime.py:303-311"]},
    "B": {"universe": "B_FETCH_UNIVERSE_Q1 (PIT scope, 4,953 symbols)",
          "warmup": "RVOL 20 sessions (PARTIAL from 5), scope median 20 daily (min 5)",
          "eval_start_index": 11, "eval_start_full_warmup_index": 20, "eval_end_index": 500,
          "forward": "none (exits intraday, PLANNED)",
          "pre_grid_needed": "20 sessions before 2024-09-17 for FULL RVOL on the first days: UNAVAILABLE",
          "evidence": ["strategy_b/config.py:183-186,228-238", "backtest/strategy_b/preparation.py:144-159",
                       "docs/backtest/STRATEGY_B_V1_SPEC.md:3.2,3.5"]},
    "C": {"universe": "grouped daily, CS quarterly snapshot, splits",
          "warmup": "base 60 sessions, M2 52W 251 sessions",
          "eval_start_index": 251, "eval_secondary_start_index": 60, "eval_end_index": 490,
          "forward": "labels to D+10 (index 500); range_end 2026-09-16 frozen in rules c769aea5",
          "pre_grid_needed": "none",
          "evidence": ["backtest/strategy_c_selection/run.py:33-35,213", "backtest/strategy_c_selection/labels.py:16"]},
    "D": {"universe": "grouped daily, CS quarterly snapshot, splits (reads the C freeze)",
          "warmup": "library d >= 60, min library span 120, W up to 60",
          "eval_start_index": 260, "eval_end_index": 480,
          "forward": "labels to D+20 (index 500); grid pinned to FREEZE_V1 501 sessions (D1 preflight)",
          "pre_grid_needed": "none",
          "evidence": ["docs/backtest/strategy_d/d_analog_rules_v1.json", "docs/backtest/strategy_d/D_D1_PREFLIGHT_CONTRACT_V1.md"]},
}


def document() -> dict:
    body = {"requirements_id": REQUIREMENTS_ID, "grid": GRID, "matrix": MATRIX, "ranges": RANGES}
    body["digest"] = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return body
