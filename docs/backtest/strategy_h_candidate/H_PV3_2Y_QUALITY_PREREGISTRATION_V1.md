# Strategy H — H-PV3 2Y Quality-only Prevalidation Preregistration V1

- declared: 2026-09-28
- status: **FROZEN BEFORE RETURN EVALUATION**
- universe: PV2C's frozen 120 disjoint issuers, checksum `6517b10e0fcec3a04a5e553a96e4b1c41052af94321434832533d1d6327fd729`
- contract checksum: `f794ef1ac3add1cdc7ac9482c4d921197fa0a817625987fd4160d3ec94eba809`

## Purpose and universe

Test whether financial Quality level contains 63-session forward SPY-relative cross-sectional information. The exact PV2C 120-issuer sample is reused without additions or replacements, regardless of fundamental coverage or outcomes. It avoids dependence on PV2's 37-company development universe and reduces issuer concentration. It remains a **LIMITED 2Y PREVALIDATION UNIVERSE; NOT SURVIVORSHIP-SAFE FULL HISTORICAL UNIVERSE**.

## Window, PIT, labels, and costs

Reuse the validated 501 grouped sessions from 2024-09-17 through 2026-09-16 and the exact 20 month-end decisions from 2024-10-31 through 2026-05-29. A fact is usable only when `acceptanceDateTime <=` the decision session's regular-market open; later filings and amendments cannot backfill earlier observations. Preserve accession, filing, acceptance, period and tag provenance. Labels are exact unadjusted close-to-close 63-session returns (primary) and 21-session returns (secondary), minus the aligned SPY return, with no endpoint fallback and no dividends. Base round-trip cost is 10bp and stress is 20bp; D10−D1 pays both legs.

## Frozen Quality candidates

All factors are levels and higher is better. Duration facts must have identical start/end and a common family. Deterministic canonical tag priority, latest eligible acceptance/accession and ambiguity rejection remain in force.

- Operating Margin = operating income / revenue. Use discrete Q1/Q2/Q3 (70–110 days) or FY (300–400 days). Revenue must be positive; negative operating income is valid.
- FCF Margin = (operating cash flow − CapEx) / revenue. Use identical Q1 (70–110), Q2 YTD (160–200), Q3 YTD (250–300), or FY (300–400) periods. Revenue must be positive. Quarter/YTD mixing is invalid.
- ROA = annualized net income / average beginning and ending assets. Net income uses discrete Q1/Q2/Q3 or FY. Assets must be positive and resolved at an instant equal to each flow boundary or one day earlier. Missing or ambiguous balances invalidate the observation. Annualization is `365 / duration_days`.
- Cash Conversion = operating cash flow / net income on identical Q1/Q2 YTD/Q3 YTD/FY periods. Net income must exceed $1,000,000, fixed ex ante to exclude loss and near-zero denominator artifacts. Negative OCF remains valid.
- Cash/Assets = cash / assets at the same instant. Assets must be positive and cash nonnegative.

ROE is excluded because negative/small equity distorts the ratio. ROIC is excluded because tax, debt and invested-capital normalization are not sufficiently stable. Debt-derived leverage is excluded because total-debt mapping is incomplete. These exclusions are data-contract decisions, not performance decisions.

## Feature quality gate and score

Before attaching returns, report formula, raw fields, valid rows, coverage, missing reasons and outlier behavior for every candidate. A feature enters the frozen composite only if it is valid for at least 40% of all 120×20 declared rows and has a median of at least 10 valid issuers per decision date. Failures are excluded only by this gate and remain reported.

Economically invalid ratios become UNKNOWN before outlier processing. Each included feature is winsorized date-wise at 2.5%/97.5%, then converted to an ascending percentile rank with average ranks for ties. Quality Score is the equal-weight mean of available included ranks and requires at least two. No weights, thresholds, features, dates or subuniverses may change after returns are observed.

## Diagnostics and frozen gates

Reuse deciles, Top 10%/20%, monthly Spearman IC, decile monotonicity, issuer-cluster bootstrap, monthly/quarterly stability, market-cap lanes, and individual-factor IC. Robustness removes (a) top 1% winning observations, (b) top five winning observations, and (c) top ten issuers by positive contribution. Positive-contribution concentration limits are 35% single issuer, 75% top five and 90% top ten.

- P1: D10 3M net excess > 0.
- P2: D10−D1 3M net spread > 0.
- P3: mean monthly IC > 0.
- P4: decile monotonicity >= 0.30.
- P5: D10 and spread remain positive in all three removals.
- P6: all frozen concentration limits pass.
- P7: base costs preserve both D10 and spread.
- P8: at least 15 valid dates, median 20 names/date and 50% of 2,400 rows.

PROMISING requires P1–P8. UNPROMISING requires P8 and at most one of P1–P4. Otherwise the verdict is INCONCLUSIVE and must be classified as DATA or SIGNAL limitation. PROMISING continues only to issuer-disjoint PV3C, not a composite or H1. INCONCLUSIVE triggers REVIEW / HOLD with data-versus-signal assessment. UNPROMISING triggers RECOMMEND CLOSE QUANT CORE and forbids automatic overlays. Paid history remains unapproved; any recommendation must consider PV1, PV2, PV2C and PV3 together.
