# Strategy E - E-D6 Development Trading Backtest Result V1

| | |
|---|---|
| Stage | `E-D6_DEVELOPMENT_TRADING_BACKTEST` |
| Protocol | `E_D6_BACKTEST_PROTOCOL_V1.md`, rules canonical `73056ac8…4713`, frozen in commit `e2e822c` before any performance was computed |
| Run id | `ed6-603bedf94f19` |
| Result | `strategy_e_d6_result_v1.json`, sha256 `f70f9196894b2a42…` (identical on a second, independent run) |
| Runtime artifacts | `data/runtime/strategy_e/backtest_runs/ed6-603bedf94f19/` (gitignored) |
| Provider calls / writer lock | 0 / not taken |
| **Final gate** | **`E-D6 INCONCLUSIVE — STATISTICAL`** |

E-D6 uses Development data that Strategy E research has already seen. No outcome here is OOS
evidence or live approval. No rule was changed after the result was known.

---

## A. Preflight / Protocol Freeze

- HEAD at run: `e2e822c` (branch `main`). E-D0 `89f27c6`, E-D1 `c210f6f`, E-D2 `1ba0773`, horizon
  `4c343e3`, E-D3 `605d374`, E-D4 `01ddb00`, E-D5 `72e416d` and the E-D6 protocol `e2e822c` are
  all ancestors of HEAD.
- Every upstream artifact was loaded through its own fail-closed loader:

| artifact | canonical sha256 |
|---|---|
| E-D0 trading rules | `f1534f07688c801f…` |
| E-D2 execution rules | `d204b1dac9dd4053…` |
| Horizon semantics | `f96f78f4bbd47587…` |
| E-D3 exit rules | `f6f455641efebedf…` |
| E-D4 cost rules | `b2229fe5308df6b2…` |
| E-D5 risk rules | `2e1e796d7e3563da…` |
| E-D6 backtest rules | `73056ac8e8cd6308…` |

- The E-D6 protocol commit `e2e822c` precedes every performance calculation in this document.

## B. Dataset

**Minute input: the E1 development tape, recovered exactly.** E1 (`e1-32a530c8af9a`) bound itself
to tape digest `d12ff28a98cf9cc6…` over 1,902 symbols / 2,152 files. Its gitignored cache
(`tape_binding.json`) is not on this machine, and the shared store has grown since then. The
binding was recovered read-only, before any return was computed:

- Sorting the store's `.json.gz` pages by modification time, the first 2,152 files cover exactly
  1,902 symbols and hash (E1's `tape_digest` recipe) to
  `d12ff28a98cf9cc64cad86f595985fd5ae2308b555635501fa5729d4439222a4`, whose prefix is the
  documented binding.
- The next 99 files (93 symbols, PINE..QSR) hash to `4f96e04261781ed6`, the documented E1-H5
  CONFIRMATION binding. This independently confirms the cut point.
- 104 pages were added to development symbol folders on 2026-09-21 (for example
  `AAL_2024-09-25_2024-12-04.p01.json.gz`). E1's loader reads every page in a folder, so these
  would have contaminated the input. E-D6 therefore reads through a symlink view that exposes
  only the 2,152 bound pages, plus 59 legacy minute parquet files each verified against its
  sha256 in the frozen USB-HIST-V1 manifest. The file list is committed as
  `strategy_e_d6_development_tape_v1.json`.
- E1's own digest over the view equals the binding before and after the run.

**Reproduction gate (checked before any trade return).** Rebuilding the E1 premarket cohort from
the view with E1's unmodified code reproduces the documented E1-H5 development block exactly:
**70,738 universe rows and 1,729 H5 rows**.

| item | value |
|---|---|
| Daily snapshot | `USB-HIST-V1`, freeze `9ebd6c29c667…`, read-set `7e790a8dcf1d…` unchanged after the run |
| Grid | 2024-09-17 .. 2026-09-16, 501 XNYS sessions |
| Evaluation sessions | 480, 2024-10-16 .. 2026-09-16 (every session has a non-empty universe) |
| Premarket symbol-sessions | 138,615 |
| Universe rows / symbols | 70,738 / 1,612 |

Before 2026-04-20 the tape carries only the 30 `MINUTE_UNIVERSE_V1` symbols; after it, the broad
collection. Trading activity therefore concentrates in the last five months (block 4 below).

## C. Candidate Funnel

| stage | count | % of previous | % of H5 |
|---|---:|---:|---:|
| eligible universe rows | 70,738 | | |
| H5 candidates | 1,729 | 2.44% | 100% |
| selected (max 3, symbol order) | 501 | 28.98% | 28.98% |
| capacity skipped | 1,228 | 71.02% | 71.02% |
| valid entries | 499 | 99.60% | 28.86% |
| invalid entries | 2 (`NO_TRADE_SHORTENED_SESSION`) | 0.40% | |
| valid exact 09:34 exits | 485 | 97.19% | 28.05% |
| unresolved exits | 14 (`NO_TRADE_MISSING_EXIT_BAR`) | 2.81% | |
| standard-PnL trades | 485 | 97.19% | 28.05% |

The funnel's loss is capacity, not execution: 71% of H5 candidates arrive on crowded sessions and
are skipped by the frozen three-position cap. Execution and data quality lose only 16 of 501
selected candidates.

## D. Trade Sample

- Valid entries 499, valid exits 485, unresolved exits 14 (kept as `UNRESOLVED_EXIT /
  INVALID_FOR_STANDARD_PNL`, no substitute price).
- **Standard-PnL coverage 97.19%**, above the 95% minimum.
- 214 unique symbols, 237 active sessions out of 480.

## E. Gross Result (0 bp, diagnostic)

Mean trade +16.82 bp, median +3.75 bp, win rate 52.2%, PF 1.33. All-session mean +6.83 bp,
cumulative +35.8%, MDD -9.1%, Sharpe 1.15, Sortino 1.79.

## F. Cost Stress

Trade-level mean/median/win/PF; session-level cumulative/MDD/Sharpe/Sortino (480 sessions,
no-trade sessions at 0, Sharpe/Sortino annualized by sqrt(252) at zero risk-free rate).

| scenario | mean | median | win rate | PF | all-session mean | cumulative | MDD | Sharpe | Sortino |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 bp (diag.) | +16.82 bp | +3.75 bp | 52.2% | 1.330 | +6.83 bp | +35.8% | -9.1% | 1.15 | 1.79 |
| 5 bp | +11.82 bp | -1.25 bp | 49.5% | 1.221 | +4.36 bp | +20.7% | -12.2% | 0.73 | 1.11 |
| **10 bp (primary)** | **+6.82 bp** | **-6.25 bp** | **48.2%** | **1.122** | **+1.89 bp** | **+7.2%** | **-16.5%** | **0.32** | **0.47** |
| 15 bp | +1.82 bp | -11.25 bp | 45.8% | 1.031 | -0.58 bp | -4.8% | -21.0% | -0.10 | -0.14 |
| 20 bp | -3.18 bp | -16.25 bp | 43.9% | 0.948 | -3.05 bp | -15.4% | -25.2% | -0.51 | -0.72 |

Active-session mean at 10 bp: +3.83 bp. The median trade is negative from 5 bp onward; the mean is
carried by a right tail.

## G. Primary 10 bp Gate

| gate | requirement | observed | result |
|---|---|---|---|
| 1 Integrity | checksums, provenance, deterministic replay, no look-ahead | all pass (section L) | PASS |
| 2 Data quality | coverage >= 95% | 97.19% | PASS |
| 3 Net economics | mean session > 0 and PF > 1 | +1.89 bp, PF 1.122 | PASS |
| 4 Statistical support | bootstrap 95% CI low > 0 | [-6.71, +10.48] bp, P(mean <= 0) 33.0% | **FAIL** |
| 5 Chronological robustness | >= 3 of 4 blocks positive | 2 of 4 | **FAIL** |

Bootstrap: IID over 480 session portfolio returns, 10,000 replicates, seed 20260921, percentile
2.5/97.5.

| block | sessions | range | active | trades | mean (10 bp) |
|---|---:|---|---:|---:|---:|
| 1 | 120 | 2024-10-16 .. 2025-04-09 | 44 | 66 | +5.75 bp |
| 2 | 120 | 2025-04-10 .. 2025-10-01 | 48 | 70 | -3.40 bp |
| 3 | 120 | 2025-10-02 .. 2026-03-25 | 37 | 48 | -4.31 bp |
| 4 | 120 | 2026-03-26 .. 2026-09-16 | 108 | 301 | +9.52 bp |

Economics pass and statistics do not, which is the protocol's `INCONCLUSIVE — PROMISING` case,
reported under the requested label `E-D6 INCONCLUSIVE — STATISTICAL`.

## H. Stability

Quarterly (compounded session returns):

| quarter | sessions | active | trades | 0 bp | 10 bp | cumulative 10 bp |
|---|---:|---:|---:|---:|---:|---:|
| 2024Q4 | 53 | 26 | 41 | +2.31% | -0.32% | -0.32% |
| 2025Q1 | 60 | 16 | 21 | +8.24% | +6.53% | +6.19% |
| 2025Q2 | 62 | 30 | 38 | -2.41% | -5.29% | +0.57% |
| 2025Q3 | 64 | 19 | 34 | +5.04% | +3.07% | +3.65% |
| 2025Q4 | 64 | 18 | 23 | -5.44% | -7.13% | -3.74% |
| 2026Q1 | 61 | 21 | 29 | +1.95% | -0.17% | -3.91% |
| 2026Q2 | 62 | 53 | 147 | +19.09% | +12.96% | +8.54% |
| 2026Q3 | 54 | 54 | 152 | +4.23% | -1.25% | +7.19% |

At 10 bp, 3 of 8 quarters are positive. The cumulative result goes from -3.9% at the end of
2026Q1 to +8.5% in one quarter. June 2026 alone contributes +16.5% at 10 bp (the monthly table,
24 months, is in `monthly.json`). No month or quarter is excluded.

## I. Buckets (diagnostic)

Price (previous close; frozen E-D6 buckets; no trade fell outside them):

| price | trades | gross | 10 bp net | win (10 bp) | PF (10 bp) |
|---|---:|---:|---:|---:|---:|
| $5-10 | 26 | +94.8 bp | +84.8 bp | 42.3% | 2.54 |
| $10-20 | 42 | +27.2 bp | +17.2 bp | 45.2% | 1.23 |
| $20-50 | 111 | +18.7 bp | +8.7 bp | 50.5% | 1.14 |
| $50-100 | 94 | +22.5 bp | +12.5 bp | 53.2% | 1.27 |
| $100-200 | 92 | +5.3 bp | -4.8 bp | 51.1% | 0.93 |
| $200+ | 120 | -1.0 bp | -11.0 bp | 42.5% | 0.76 |

Liquidity (previous-day dollar volume; Research's reported bucket edges):

| D-1 dollar volume | trades | gross | 10 bp net | win (10 bp) | PF (10 bp) |
|---|---:|---:|---:|---:|---:|
| $5-20M | 15 | +82.5 bp | +72.5 bp | 46.7% | 2.32 |
| $20-100M | 80 | +49.1 bp | +39.1 bp | 47.5% | 1.63 |
| $100-500M | 127 | -1.8 bp | -11.8 bp | 44.9% | 0.82 |
| $500M+ | 263 | +12.3 bp | +2.3 bp | 50.2% | 1.05 |

No bucket becomes a filter. Low-price, low-liquidity rows carry most of the edge, on small
counts and with win rates below 50%.

## J. Concentration (diagnostic only; no frozen threshold)

Contribution = normalized weight x trade return, summed per symbol.

| basis | unique symbols | total | top1 | top5 | top10 | HHI (trade count) |
|---|---:|---:|---|---|---|---:|
| gross | 214 | +32.8% | ABSI 20.5% | 77.0% | 114.1% | 0.0107 |
| 10 bp | 214 | +9.1% | ABSI 73.4% | 260.5% | 374.8% | 0.0107 |

Top five by contribution: ABSI, HIMS, SOFI, UBER, INTC. At 10 bp the top five symbols contribute
2.6x the total, so the remaining 209 symbols are net negative together. Trades per symbol: mean 2.27,
median 1, max 18.

## K. Implementation Delta

| step | mean |
|---|---:|
| Research reference: matched H5 lift (session-demeaned, observational) | +17.01 bp |
| All 1,729 H5 rows, Research `R_5m` (raw) | +16.45 bp |
| 501 selected (capacity) | +17.45 bp |
| 485 standard trades, `R_5m` | +16.82 bp |
| 485 standard trades, `R_5m_strict` | +16.82 bp |
| 485 standard trades, Trading gross | +16.82 bp |
| Active-session equal-weight gross | +13.83 bp |
| Active-session net at 10 bp | +3.83 bp |
| All-session net at 10 bp (the gate metric) | +1.89 bp |

The Trading gross equals Research's `R_5m_strict` on every trade (max absolute difference
2.7e-16), so the two code paths read the same bars. Capacity selection does not dilute the raw
edge. The delta comes from three places. Session aggregation weights every active session equally:
three-name sessions (101) earned +23.3 bp per trade, while one-name (90) and two-name (46)
sessions earned +8.3 and +4.0 bp, so moving from trade to session weighting costs 3.0 bp. The
frozen 10 bp round-trip cost removes about 72% of the active-session gross. Averaging over the
243 no-trade sessions halves the result again. The +17 bp Research figure is a lift against matched controls,
not a return; it was never a reproduction target.

## L. Reproducibility

- Two independent runs, same input and frozen artifacts: identical result digest
  `f70f9196894b2a428c039fdd833cdd8ba4284dd9594778ceed6c02e9d5bf7eef` and byte-identical
  `trades.csv`, `daily_returns.csv` and every summary file (`repeat-2/REPEAT_CHECK.json`).
- In-process replay executed twice with identical signal/execution/exit/sizing digests.
- PIT: E1's own decision-boundary poison test on 41 bound symbols (2,607 sessions) found no
  feature change. Structurally, the signal layer receives only the sealed 09:25 features, and bars
  are loaded afterwards only for H5 candidates. Entry sees only 09:30 bars, exit only 09:34 bars.
- Tests: `backend/tests/strategy_e_trading/test_e_d6_backtest.py`, 35 tests covering the 20
  required items. All Strategy E trading tests pass (142).

## M. Git

- Commit 1 (protocol, before results): `e2e822c` Strategy E E-D6 백테스트 판정 계약 동결.
- Commit 2 (runner, tests, tape manifest, result): this commit. Files added:
  `backend/app/backtest/strategy_e_d6/{__init__,dataset,replay,metrics,run}.py`,
  `backend/app/dev/run_strategy_e_d6.py`, `backend/tests/strategy_e_trading/test_e_d6_backtest.py`,
  `docs/backtest/strategy_e_candidate/{E_D6_DEVELOPMENT_BACKTEST_RESULT_V1.md,
  strategy_e_d6_result_v1.json, strategy_e_d6_result_v1.sha256,
  strategy_e_d6_development_tape_v1.json}`.
- No frozen E-D0..E-D6 artifact, Research module or shared store was modified. The unrelated
  dirty worktree was left untouched and was not staged.

## N. FINAL GATE

```text
E-D6 INCONCLUSIVE — STATISTICAL
```

Economics clear the primary 10 bp scenario (mean session +1.89 bp, PF 1.12, coverage 97.2%).
The statistical gate fails (CI lower bound -6.7 bp) and so does the chronological gate (2 of 4
blocks). Break-even is about 16.8 bp round trip on the trade mean and 13.8 bp on the session
series (diagnostic only; not a cost assumption).
The result relies on a right tail concentrated in a few symbols and in 2026Q2. Per the protocol,
H5, selection, entry, exit, sizing and cost stay unchanged. Whether to open Paper/Forward
validation is a separate gate decision.
