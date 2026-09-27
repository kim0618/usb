# Strategy H — H-PV1 2Y Value Preregistration V1

- declared: 2026-09-27
- status: **FROZEN BEFORE ANY FORWARD RETURN IS READ**
- track: H-PV / 2Y QUANT PREVALIDATION; not official H1
- checksum: `3c87c84f65fc5d09a9da503ce70a343234e9e3e509026521c9e30d10cfead23a`

## Scope and data audit

Use only the local unadjusted daily panel (2024-09-16 through 2026-09-16, 502 complete XNYS sessions) and existing PIT SEC stores. The universe is the exact intersection of the 2024-10-25 dated active US `CS` snapshot on XNYS/XNAS/XASE, the existing deterministic H0 40-CIK companyfacts set, and a local daily ticker: 43 securities. It is explicitly a **LIMITED 2Y PREVALIDATION UNIVERSE, NOT A SURVIVORSHIP-SAFE FULL HISTORICAL UNIVERSE**. A CIK with multiple eligible share classes is `MULTI_CLASS_UNRESOLVED` and excluded from primary results. No result-driven replacement occurs.

Known limitations are the short period, few regimes, historical-security-master gaps, recent-universe bias, price-return-only labels, and coverage-conditional sector/lane diagnostics. Verdicts are only PROMISING, INCONCLUSIVE, or UNPROMISING.

## PIT and rebalance

Rebalance monthly on each month's last XNYS session, beginning 2024-10-31. Primary evaluation ends at the latest month-end whose exact 63-session endpoint is no later than 2026-09-16. Features use only filings usable by the decision session's regular open under the frozen H0.5 rule; later filings and current backfill are forbidden. Amendments are knowledge-time versioned.

Duration inputs are latest eligible 10-K/10-K/A fiscal-year facts lasting 300–400 days. Shares are only non-stale (<=135 calendar days) `dei:EntityCommonStockSharesOutstanding`. Market cap is unadjusted close times PIT raw shares. Multi-class, stale, missing, conflicting, or split-inconsistent shares yield UNKNOWN.

## Frozen Value features and score

- Earnings Yield = positive annual net income / positive market cap.
- FCF Yield = positive (annual operating cash flow − annual capex) / positive market cap.
- Sales Yield = positive annual revenue / positive market cap (inverse Price/Sales).

Negative or zero earnings/FCF, nonpositive revenue/cap, near-zero or invalid denominators, and missing inputs make that feature missing; they are not forced into numeric ratios. EV/EBITDA and EV/Sales are excluded because H0 did not freeze reliable EBITDA/debt composition.

Within each decision date, each raw feature is winsorized once at fixed 2.5/97.5 percentiles, then ascending percentile-ranked so higher is more attractive. `ValueScore` is the equal-weight mean of available ranks and requires at least two of three features. No weights or cutoffs may change.

## Returns, groups, benchmark, and costs

Primary label is exact 63-session (3M proxy) unadjusted close-to-close price return; secondary is exact 21-session (1M proxy). Missing endpoint invalidates the label. SPY same-session price return is the sole primary benchmark; excess is security minus SPY. Dividends are unavailable and omitted.

Within-date deterministic score order uses `security_id` as tie break. Ten near-equal groups are assigned with D10 highest; a date requires at least 20 valid rows. Top 10% and Top 20% are the only Top-N tests. Base round-trip cost is 10bp and stress is 20bp, deducted once from long groups; D10-D1 deducts both legs.

## Metrics and robustness

Report observation/date coverage, mean/median return and excess, hit rate, standard deviation, issuer-cluster bootstrap 95% CI, D1–D10, D10-D1, monthly/quarterly direction, monthly Spearman IC, and Spearman monotonicity of decile number versus decile mean excess. Sector and market-cap lanes are secondary only when PIT coverage exists.

Recompute frozen primary results after separately removing: top 1% winning observations, top five winning observations, and top ten securities ranked by positive contribution. Positive-contribution concentration limits are <=35% single security, <=75% top five, <=90% top ten.

## Coverage and gates

Coverage requires at least 15 valid decision dates, median >=20 securities/date, and >=50% of eligible single-class security-date rows with score and primary label.

- P1: D10 mean 3M net excess > 0.
- P2: D10-D1 mean 3M net spread > 0.
- P3: mean monthly 3M Spearman IC > 0.
- P4: Spearman(decile number, decile mean 3M excess) >= 0.30.
- P5: D10 excess and D10-D1 remain positive in every extreme-removal audit.
- P6: all contribution limits pass.
- P7: base 10bp leaves D10 excess and D10-D1 positive.
- P8: all coverage minima pass.

PROMISING requires P1–P8 all PASS. UNPROMISING requires P8 PASS and at most one of P1–P4 PASS. Every other case, including an unmeasurable required gate, is INCONCLUSIVE. A favorable result does not authorize PV2 or H1. No post-hoc change to features, thresholds, dates, winsorization, weights, groups, universe, costs, or gates is permitted.
