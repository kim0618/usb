# Strategy E — E-D6 Development Trading Backtest Protocol V1

## Status and interpretation

- Stage: `E-D6_DEVELOPMENT_TRADING_BACKTEST`
- Protocol status: **FROZEN BEFORE THE FIRST HISTORICAL PERFORMANCE CALCULATION**
- Machine authority: `strategy_e_backtest_rules_v1.json`
- Primary cost: `COST_10BP`, total round trip

E-D6 uses Development data already seen during Strategy E research. A pass means ready for paper or
forward validation. It is not OOS confirmation, live approval, or evidence that actual execution
cost is 10 bp.

## 1. Frozen replay

The replay must use H5 unchanged; select at most three symbols in canonical ascending order with no
replacement; enter at exact 09:30 aggregate open; exit at exact 09:34 aggregate close without
fallback; equal-weight the standard-PnL-eligible set; apply no stop, target, or partial exit; and
report 0/5/10/15/20 bp total round-trip costs. `COST_10BP` is primary and cannot be changed after
results are known.

## 2. Dataset and point-in-time boundary

The runner must identify its exact frozen input files/store snapshot, date range, XNYS sessions,
symbols, rows, and content digest. H5 may read only the pre-09:25 features frozen by E-D1. Candidate
selection occurs before execution checks. Exact 09:30 and 09:34 fields are used only by their
respective execution layers. Post-09:25 values cannot alter H5, ordering, replacement, or sizing.

Every valid XNYS session in the dataset range appears in the daily series. A no-trade session has
zero return. Active-session, all-session, and trade-level expectations are reported separately.

## 3. Coverage and invalid exits

```text
standard_pnl_coverage = standard-PnL trades / valid entries
normal evaluation requires coverage >= 95%
```

A valid entry without exact 09:34 close is retained as `UNRESOLVED_EXIT /
INVALID_FOR_STANDARD_PNL`. It receives no invented return or substitute price. Coverage below 95%
forces `E-D6 INCONCLUSIVE — DATA QUALITY`, whatever the observed economics.

## 4. Portfolio and metrics

For each scenario and session:

```text
portfolio_return = sum(normalized_weight_i * net_return_i)
```

Profit factor is positive trade returns divided by the absolute sum of negative trade returns and
is null when undefined. Cumulative return compounds all-session returns from equity 1. Maximum
drawdown is the minimum `equity / running_peak - 1`.

Sharpe uses `sqrt(252) * mean / sample_std(ddof=1)` on all XNYS session returns with zero risk-free
rate. Sortino uses `sqrt(252) * mean / sqrt(mean(min(return,0)^2))` with zero target. Undefined
denominators yield null; no value is manufactured.

## 5. Statistical and chronological gate

The primary statistical observation is one complete `COST_10BP` session portfolio return. The
bootstrap samples these session values IID with replacement, 10,000 times, seed `20260921`, and
uses the 2.5/97.5 percentile interval for the arithmetic mean. Aggregating trades to sessions first
preserves the within-day cluster.

All ordered XNYS evaluation sessions are split into four contiguous equal-count blocks (counts may
differ by one). Blocks never depend on trade count or outcomes. At least three blocks must have a
positive primary mean.

## 6. Verdict order

1. Any frozen-integrity, deterministic-replay, or PIT failure blocks evaluation.
2. Coverage below 95% gives `E-D6 INCONCLUSIVE — DATA QUALITY`.
3. With valid integrity/coverage, mean session return <= 0 or trade PF <= 1 gives `E-D6 FAIL`.
4. Positive economics with CI lower bound <= 0 or fewer than three positive blocks gives
   `E-D6 INCONCLUSIVE — PROMISING` in the protocol, reported using the requested final label
   `E-D6 INCONCLUSIVE — STATISTICAL`.
5. Only positive economics, CI lower bound > 0, and at least three positive blocks gives
   `E-D6 PASS — READY FOR PAPER / FORWARD`.

## 7. Diagnostics only

Monthly, quarterly, price/liquidity buckets, break-even cost, and symbol concentration are reported
without gates. Price buckets are frozen at 5–10, 10–20, 20–50, 50–100, 100–200, and 200+ USD,
left-inclusive/right-exclusive. Liquidity reuses Research D-1 dollar-volume buckets when available;
otherwise deterministic full-sample quantiles are explicitly descriptive. No diagnostic may add a
filter, remove a period, or change the frozen strategy.

The matched Research H5 lift of roughly 17 bp is observational matched-control evidence. Trading
results incorporate exact proxies, selection capacity, failures, normalized sizing, and cost. Their
difference is an implementation delta, not a requirement to reproduce 17 bp.

## 8. Reproducibility and outputs

Two runs over identical frozen input must have identical canonical digests. Raw trades, daily
returns and detailed tables are retained under the gitignored
`data/runtime/strategy_e/backtest_runs/<run_id>/`. The repository stores the canonical JSON and
Markdown summaries. No frozen E-D0 through E-D5 artifact may be modified.
