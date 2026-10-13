# US-B CRYPTO — C1 Signal Strength Research Contract V1

Status: **FROZEN BEFORE STRENGTH RESULT COMPUTATION**  
Frozen: 2026-10-03 (Asia/Seoul)

## Scope

Use exactly the 1,603 executable trades in frozen E1. This is reused-data, post-E1/R0 research and
is not new out-of-sample confirmation. C1 entry, execution, costs, exits, PAPER/LIVE/UI, and
operational code must not change. Regime is context only: no trade is filtered.

## Continuous features

All values are known at the C1 decision bar.

- `premium_extreme_bp = (s1_cut10 - s1) * 1e4`; larger means further below that day's frozen B1 cutoff.
- `oi_drop_bp = -oi_chg_1h * 1e4`; larger means a stronger one-hour OI decline.
- `vol_intensity = rv24h / 0.0010924950359531157 - 1`; larger means further above E1's frozen HIGH cutoff.
- `regime`: R1–R5 joined at `signal_ts_ms - 1 minute`, context only.

Two exploratory interactions are allowed and no others: the product of within-sample percentile ranks
for premium×OI and OI×volatility. They are interaction diagnostics, not scores or proposed weights.

## Analysis

For each continuous feature and interaction, report Spearman rank association with gross and net at
30/60/120/240/480 minutes and with 4H MFE/MAE. Split each feature into exactly four equal-count
quantiles, low to high strength, and report count, gross/net mean, net median, win rate, PF, and MFE/MAE.
The main table is 4H net. Monotonicity is the Spearman correlation of Q1–Q4 mean net with quantile
number plus the count of favorable adjacent steps.

Repeat the 4H continuous and quantile-order diagnostics in R1–R5 and F1–F9 without filtering the
primary population. Remove the largest ceil(5%) of C1_ALL 4H net trades globally and repeat the
4H quantile order. This is sensitivity analysis, not a candidate strategy.

## Verdict

A base feature is `directionally repeatable` only when all hold:

1. full-sample continuous 4H net rho > 0;
2. at least 3/5 net horizons have rho > 0;
3. Q1→Q4 4H mean-net order has positive rho and at least 2/3 favorable adjacent steps;
4. at least 3/5 regimes and 6/9 folds have positive 4H net rho;
5. after global top-5% removal, quantile-order rho remains positive and at least 2/3 adjacent steps
   remain favorable.

Interactions cannot independently satisfy the required two-feature rule.

- `USABLE_FOR_FORWARD_SCORING`: at least two of the three base features are directionally repeatable.
- `WEAK_STRENGTH_SIGNAL`: exactly one base feature is repeatable, or at least two meet the first three
  direction tests but fail regime/fold/tail robustness.
- `NO_STRENGTH_SIGNAL`: neither condition holds.

Only `USABLE_FOR_FORWARD_SCORING` may propose a LOW/MEDIUM/HIGH forward score, and even then no
operational application is authorized. Otherwise no score rule is proposed. End with STOP.
