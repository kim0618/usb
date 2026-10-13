# US-B CRYPTO R1 — C1 × Market Regime Contract V1

Status: **FROZEN BEFORE R1 COMPUTATION**  
Frozen at: 2026-10-03 (Asia/Seoul)

## Scope and lineage

This is a post-R0 follow-up on reused data, not a new out-of-sample confirmation. C1 entry, its
FALSE→TRUE episode construction, E1 4H execution/cost accounting, and R0 regime definitions are
unchanged. Research only: C1/C1x operations, PAPER, LIVE, deployment, orders, and API paths are out
of scope.

Exactly three candidates are permitted:

- `C1_ALL`: all 1,603 frozen E1 trades.
- `C1_NO_R5`: exclude a C1 trade when its decision-bar regime is R5.
- `C1_R3_R4`: retain only decision-bar regimes R3 or R4.

No additional candidate, threshold sweep, exit change, or entry change is permitted.

## Join and units

The trade universe is `e1_trades_v1.parquet`. Regime is joined at `signal_ts_ms - 1 minute`, exactly
as in R0. The minute timestamp and regime must match `regime_minutes_v1.parquet`; any missing,
duplicate, `UNCLASSIFIED`, or outside join stops the run. E1 fields (`net_bp`, MFE/MAE, fold and entry
time) are reused without recomputation.

## Frozen reporting

For every candidate report trades, retention against C1_ALL, mean/median net, PF, win rate, total
net, 7-calendar-day moving-block clustered 95% CI (10,000 draws, seed 20261003), F1–F9 and calendar
year stability, removal of the largest 1/3/ceil(1%)/ceil(5%) candidate trades, and MFE/MAE mean and
median.

For each gated candidate also report excluded count and net sum. `total_opportunity_loss_bp` is the
sum of excluded E1 net bp: positive means profit surrendered; negative means losses avoided.

`large winner` is frozen as membership in the largest ceil(5%) of C1_ALL by 4H BASE net bp (ties are
resolved by net descending then signal_id ascending). Report (a) large winners among excluded trades,
(b) that count divided by excluded trades, and (c) the share of all baseline large winners removed.
The candidate disproportionately removes large winners when (c) exceeds its overall exclusion rate.

## Verdict

`C1_ALL` is the baseline, not a promotion candidate. A gated candidate is `IMPROVES_C1` only if all
of the following hold:

1. retention is at least 60%;
2. mean net, median net, PF, and win rate are all no worse than C1_ALL, with mean net strictly higher;
3. total opportunity loss is non-positive (candidate total net is at least baseline total net);
4. it does not disproportionately remove baseline large winners;
5. its paired opportunity delta CI excludes zero on the favorable side. For every baseline signal,
   paired delta is `-net_bp` if excluded and zero if retained, then the same 7-day moving-block
   bootstrap is used;
6. positive-fold count and positive-year count are each no worse than C1_ALL;
7. after each frozen top 1/3/1%/5% removal, candidate mean is no worse than the corresponding
   C1_ALL mean.

A candidate is `WORSE_THAN_BASELINE` if its mean net is no higher, its total opportunity loss is
positive, or its paired delta CI is wholly unfavorable. Otherwise it is `INCONCLUSIVE`. The overall
R1 verdict is `IMPROVES_C1` if at least one gated candidate improves; otherwise
`WORSE_THAN_BASELINE` only if both are worse, and `INCONCLUSIVE` in every other case.

Results end with STOP. No commit or push.
