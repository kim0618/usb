# Directional Probability Model R0 — frozen before fitting

Research only; no C1/C1x, PAPER/LIVE, UI or production edits; no commit/push.
Existing archive reused: model-level out-of-sample, NOT an untouched research holdout.

## Samples and PIT
Full market, 5-minute UTC decision boundaries, from 2021-03-04 through 2026-09-22.
The preceding 1-minute candle is closed at decision time. OI uses existing conservative
timestamp +5m availability; funding features use only settled observations. Binance closes
are available at candle close; exclude external age >5m within previous 24h. No interpolation.
One-minute OHLC paths start at next-bar open. Require complete 240m future and finite features.
5m chosen because OI updates at 5m; denser sampling adds dependent copies. No C1 gating.

## Fixed feature set (20)
Returns 5/30/60/240m divided by trailing 24h realized volatility; 4h range position;
log RV 1h and log RV 24h; RV 1h/24h; OI log changes 1h/4h/24h;
settled funding bp; mark/index basis bp and 1h change; Binance perp/spot premium bp,
its 1h change; Bybit-minus-Binance 1h return divided by RV24h;
Binance spot-minus-perp 1h return divided by RV24h; UTC hour sine/cosine.
No selection, feature additions or parameter search after outcomes.

## Paths, direction, target
Horizons 30/60/120/240 minutes compared descriptively; no chosen time-exit strategy.
U=max(0,(future max high / next open -1)*10000), D=max(0,(1-future min low/next open)*10000).
LONG MFE=U, adverse magnitude=D; SHORT MFE=D, adverse magnitude=U.
LONG favorable iff U-D > max(0, realized full-horizon LONG MARKET cost bp).
SHORT favorable iff D-U > max(0, realized full-horizon SHORT MARKET cost bp).
This is cost-adjusted excursion dominance, not the probability of a profitable executable trade.
Neither is possible; independent marginal estimates are not normalized to sum to 1.
Tie is unfavorable. Future cost enters labels only, never features.
One fixed model family: existing numpy RidgeLogistic(l2=10,iters=30,tol=1e-8),
train-only median/MAD scaler. Calibrate raw logits using independent chronological
calibration slice and RidgeLogistic(l2=1,scale_inputs=False). No score-to-probability mapping.
Baseline = calibration-slice favorable frequency (recent historical constant).
Distribution: ridge regression (l2=10) on log1p(U), log1p(D), same train scaler.
Calibrated residual empirical CDFs within trailing-RV24h tertiles (boundaries from fit only).
Quantiles fixed at 0.50 and 0.80 (TP1/TP2; claimed hit probabilities 0.50/0.20).
Also report CDF-implied hit probabilities for fixed 40/80bp targets. Quantiles floored at zero.
Price target at decision = latest close*(1 +/- predicted bp/10000); execution evaluation uses
next open and separately records close-to-open reference error. No retrospectively optimal targets.
Unconditional distribution baseline = calibration-slice U/D quantiles.

## Walk-forward and dependence
Nine expanding folds: evaluation starts 2022-01-01, then every Jan/Jul through 2026-01-01;
last fold ends 2026-09-23. Last 90 days before fold start form calibration, earlier data fit.
240m purge at fit/cal and cal/test boundaries; test labels must finish before fold end.
All eligible 5m rows used; separate C1 episode timestamps predicted with same frozen fold model.
Metrics on all rows are dependent observations, not portfolio returns.
Fixed UTC 4h decision phase is a nonoverlapping diagnostic on each horizon.
Calendar-week clustered bootstrap 2000 draws seed=20261003, stratified by test fold;
95% CI plus 98.75% CI for directional Brier improvement / net mean (four horizons).
Clusters are approximations to dependence, not iid sample counts. No IID significance claims.
Fixed probability buckets [0,.1,...,1], minimum 500 rows for monotonicity diagnosis.
Report direction calibration, Brier/BSS, AUC, ECE, bucket outcomes, Spearman monotonicity,
MFE/MAE, net/PF/win, target hit/calibration/pinball, yearly/fold results and C1 relation.

## Costs and economic diagnostic
Reuse local D6-C MARKET contract: taker .00055 per entry and exit notional;
SyntheticBook spread .10 USDT, tick .10; no claim of historical orderbook execution.
Actual settlement rates on crossed boundaries, marked notional funding, signed by side.
Funding includes entry < settlement <= endpoint exit (existing contract).
Base uses synthetic bid/ask; stress subtracts 2.525318158974054bp per round trip.
Net, PF and win rate are next-open to horizon-end-open diagnostics only, NOT an exit policy.
Report all states and fixed p>=.60 states whose predicted mean MFE minus predicted mean
adverse magnitude exceeds forecast cost (11bp + spread + abs(last funding)*H/480 + stress).
This single fixed diagnostic is not a threshold optimization or deployable trading strategy.
Path quantiles below forecast costs are marked non-economic; touch is not realized profit.

## Verdict gates fixed in advance
C requires at least one horizon with BOTH sides: BSS>.01, lower 98.75% weekly-clustered
Brier-improvement CI>0, ECE<=.05, bucket Spearman>=.8 over >=3 populated buckets,
BSS>0 in >=7/9 folds and >=4/5 years; q50/q80 hit error<=.05 overall and in >=7/9 folds,
positive pinball skill for both quantiles; economic diagnostic >=200 nonoverlap rows per side
and positive lower 98.75% net CI. Report all horizons; no winner retraining.
B if C fails and any horizon BOTH sides have BSS>0, AUC>.52 and positive bucket monotonicity.
A otherwise. A is failure of this preregistered R0 specification, not proof all models impossible.
No iteration after first valid outcome. Fix demonstrated implementation bugs only, log them.
