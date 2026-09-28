# Strategy H — H-PV2 2Y Growth-Only Preregistration V1

- declared: 2026-09-28
- status: **FROZEN BEFORE ANY PV2 FORWARD RETURN IS READ**
- independent hypothesis; H-PV1 Value remains UNPROMISING/CLOSED
- checksum: `17464dd446601d960863c79692efb133f659355263d0ee591f9745f304040eeb`

## Question and immutable inputs

Does higher PIT comparable-period fundamental Growth predict higher 63-session SPY excess return? Value Score, Value+Growth, Quality, GPT, revisions, qualitative forecasts, optimization, and lane selection are forbidden. PV2 reuses PV1's 501 sessions (2024-09-17..2026-09-16), exact 20 month-ends (2024-10-31..2026-05-29), 37 single-class securities, 21/63-session labels, SPY benchmark, 10/20bp costs, ranking, deciles, bootstrap, and robustness arithmetic. PV1 files and verdict may not change.

The universe is a **LIMITED 2Y PREVALIDATION UNIVERSE, NOT A SURVIVORSHIP-SAFE FULL HISTORICAL UNIVERSE**. Missing Growth data removes the feature/row, never the security from the declared universe.

## PIT and comparable periods

Facts must be accepted no later than the decision session's regular open. A later acceptance becomes usable next session. Current/future fallback and amendment back-injection are forbidden; current and prior accession and acceptance timestamp remain in row provenance.

Revenue, operating income, and diluted EPS use either discrete quarters lasting 70–110 days from 10-Q/10-Q-A with FP Q1/Q2/Q3, or fiscal years lasting 300–400 days from 10-K/10-K-A with FP FY. FCF uses OCF−capex for comparable Q1 70–110, Q2 YTD 160–200, Q3 YTD 250–300, or FY 300–400 day periods. OCF and capex must share exact start/end dates.

Current/prior must have the same field, unit, fiscal period and period family, prioritized canonical tag, end dates 345–385 days apart, and duration difference <=7 days for quarters/YTD or <=15 days for FY. Annual, quarter and YTD families never mix.

## Frozen Growth features

- Revenue Growth = current revenue / prior comparable revenue − 1; both positive.
- Operating Income Growth = current / prior comparable operating income − 1; both positive.
- Diluted EPS Growth = current / prior comparable diluted EPS − 1; both positive and prior >=$0.05/share.
- FCF Growth = current comparable (OCF−capex) / prior comparable (OCF−capex) − 1; both positive.

Loss→profit, profit→loss, negative→positive FCF, positive→negative FCF, zero/near-zero denominators, duration mismatch and missing comparable periods are excluded from percentage growth and counted separately. No transition bonus exists.

Before labels are joined, each feature independently passes a data-quality gate only if it covers >=40% of the 740 declared security-date rows and has median >=10 valid securities/date. Every feature passing both gates enters the composite regardless of eventual return performance. Each date winsorizes valid growth at 2.5/97.5 percentiles and ascending percentile-ranks it. GrowthScore is the equal-weight mean of all quality-passing feature ranks available for that row and requires at least two valid features.

## Evaluation and gates

Primary is exact 63-session and secondary exact 21-session unadjusted close-to-close price return. Excess subtracts aligned SPY price return; dividends are excluded. Base cost is 10bp round trip and stress 20bp; D10−D1 pays two legs. D10 is highest GrowthScore. Top 10%/20% are the only shortlist diagnostics.

Report D1–D10, D10, D10−D1, monthly IC distribution, decile monotonicity, monthly/quarterly stability, individual-feature IC, lanes, issuer concentration, and the same three PV1 extreme-removal audits. Coverage requires >=15 dates, median >=20 names/date and >=50% declared rows. Concentration limits are <=35% single, <=75% top five and <=90% top ten positive contribution.

P1 D10 net excess >0; P2 D10−D1 net spread >0; P3 mean monthly IC >0; P4 decile monotonicity >=0.30; P5 both directions survive all removals; P6 concentration passes; P7 base cost preserves both; P8 coverage passes. PROMISING requires all eight. UNPROMISING requires P8 and at most one of P1–P4. Otherwise INCONCLUSIVE. No result-driven feature, threshold, weight, date, universe or cost change is allowed. PROMISING does not authorize a composite, PV3, paid data, or H1.
