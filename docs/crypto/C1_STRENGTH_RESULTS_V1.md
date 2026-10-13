# US-B CRYPTO — C1 Signal Strength Research Results V1

Research date: 2026-10-03. Contract: `C1_STRENGTH_CONTRACT_V1.md`.

## Conclusion

**Verdict: `NO_STRENGTH_SIGNAL`.** None of the three base features satisfies the frozen
directional-repeatability rule. Premium extremeness passes the full-sample direction checks, but its
Q1→Q4 ordering reverses after the global top 5% winners are removed. OI-drop strength and volatility
intensity already fail the full-sample continuous-direction test. The two permitted interaction
diagnostics do not repair this result.

This is post-E1/R0 research using the same 1,603 historical executable C1 trades. It is **not a new
out-of-sample confirmation**. No C1 entry condition, execution assumption, cost, exit, or operational
path was changed.

## Continuous feature relationships

Spearman rank correlation with forward gross/net bp is shown below. Positive values mean that a
larger feature value was associated with better performance.

| Feature | 30m gross/net | 1h gross/net | 2h gross/net | 4h gross/net | 8h gross/net | 4h MFE | 4h MAE |
|---|---:|---:|---:|---:|---:|---:|---:|
| Premium extremeness | +.030 / +.030 | +.038 / +.039 | +.026 / +.026 | +.024 / +.025 | -.015 / -.015 | +.137 | -.099 |
| OI 1H drop | +.052 / +.052 | +.043 / +.043 | -.018 / -.018 | -.026 / -.026 | -.026 / -.025 | +.072 | -.074 |
| Volatility intensity | -.025 / -.025 | -.031 / -.030 | -.011 / -.010 | -.021 / -.020 | -.052 / -.051 | +.162 | -.196 |
| Premium×OI rank | +.055 / +.055 | +.056 / +.056 | +.007 / +.007 | -.013 / -.013 | -.032 / -.031 | +.137 | -.119 |
| OI×volatility rank | +.017 / +.017 | +.011 / +.011 | -.013 / -.012 | -.023 / -.022 | -.048 / -.047 | +.139 | -.150 |

Premium is the only base feature with positive net association at 4/5 horizons including 4H, but
the correlations are small. A stronger OI decline helps only at 30–60m and is adverse from 2H onward.
Higher volatility is adverse at every return horizon. Volatility and OI strength both increase MFE,
but their negative MAE correlations mean stronger readings also coincide with more severe adverse
excursion; the extra movement is not clean directional edge.

## Four-hour quartiles

Quartiles are equal-count and ordered low-to-high feature strength. Counts are 401/401/401/400 for
each full-sample split.

| Feature | Q1 mean net | Q2 mean net | Q3 mean net | Q4 mean net | Order rho | Favorable steps |
|---|---:|---:|---:|---:|---:|---:|
| Premium extremeness | +6.00 | +20.91 | -19.20 | +33.43 | +.40 | 2/3 |
| OI 1H drop | +22.48 | +12.26 | +5.84 | +0.48 | -1.00 | 0/3 |
| Volatility intensity | -1.45 | +4.21 | +28.77 | +9.55 | +.80 | 2/3 |
| Premium×OI rank | +24.27 | +11.65 | +2.44 | +2.71 | -.80 | 1/3 |
| OI×volatility rank | +13.69 | +18.54 | -2.46 | +11.32 | -.60 | 2/3 |

The premium result is not smooth: Q3 is the worst bucket and Q4 carries the apparent advantage.
OI decline is strictly inverse to the proposed strength direction. Volatility has a hump-shaped
result, with Q3 stronger than Q4, so the positive bucket-order statistic conflicts with its negative
continuous correlations.

Additional 4H distribution metrics for the three base features:

| Feature | Quartile | Median net | Win rate | PF | MFE mean | MAE mean |
|---|---|---:|---:|---:|---:|---:|
| Premium | Q1 / Q2 / Q3 / Q4 | -8.20 / +6.28 / -17.30 / +18.69 | 47.4 / 52.4 / 46.6 / 55.2% | 1.105 / 1.337 / .782 / 1.457 | 138 / 175 / 159 / 224 | -129 / -144 / -185 / -174 |
| OI drop | Q1 / Q2 / Q3 / Q4 | +10.42 / -8.20 / +2.42 / -2.48 | 54.4 / 47.6 / 50.1 / 49.5% | 1.367 / 1.194 / 1.079 / 1.006 | 161 / 166 / 180 / 189 | -143 / -145 / -159 / -186 |
| Volatility | Q1 / Q2 / Q3 / Q4 | +5.21 / -2.95 / +19.10 / -15.33 | 52.9 / 48.4 / 54.4 / 46.0% | .977 / 1.074 / 1.467 / 1.096 | 138 / 140 / 179 / 239 | -135 / -133 / -139 / -225 |

All excursion values are bp. Rising OI-drop and volatility intensity expand both favorable and
adverse paths; neither produces a reliable improvement in net outcome.

## Regime stability

The table reports continuous 4H net rho in each regime. Regime is context only; no observations were
excluded from the primary population.

| Feature | R1 | R2 | R3 | R4 | R5 | Positive regimes |
|---|---:|---:|---:|---:|---:|---:|
| Premium extremeness | +.023 | +.001 | +.081 | +.084 | -.094 | 4/5 |
| OI 1H drop | +.067 | -.057 | +.058 | -.036 | -.032 | 2/5 |
| Volatility intensity | +.023 | +.124 | -.032 | -.061 | -.147 | 2/5 |
| Premium×OI rank | +.037 | -.044 | +.070 | +.008 | -.079 | 3/5 |
| OI×volatility rank | +.059 | +.072 | +.057 | -.074 | -.105 | 3/5 |

Premium is positive in four regimes but reverses in R5. OI and volatility are positive in only two
regimes each. Regime counts are R1 163, R2 383, R3 182, R4 562, and R5 313; these are diagnostics,
not hard gates.

## Fold stability

Continuous 4H net rho by frozen E1 fold:

| Feature | F1 | F2 | F3 | F4 | F5 | F6 | F7 | F8 | F9 | Positive |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Premium extremeness | +.031 | -.016 | +.009 | +.371 | +.241 | -.229 | -.056 | +.167 | +.058 | 6/9 |
| OI 1H drop | -.008 | -.117 | +.019 | +.387 | +.056 | -.025 | -.102 | -.157 | -.014 | 3/9 |
| Volatility intensity | -.050 | -.154 | +.023 | -.447 | +.072 | +.094 | +.282 | -.029 | +.266 | 5/9 |
| Premium×OI rank | -.002 | -.071 | -.003 | +.431 | +.195 | -.129 | -.133 | -.068 | +.008 | 3/9 |
| OI×volatility rank | -.033 | -.104 | +.018 | +.221 | +.078 | +.052 | +.101 | +.113 | +.164 | 7/9 |

Premium meets the 6/9 fold count but reverses materially in F6 and is concentrated in F4/F5/F8.
The base OI and volatility features do not meet the frozen fold requirement.

## Global top-5% removal

The largest 81 C1_ALL 4H net trades were removed once globally, then quartiles were recomputed on
the remaining observations. This is sensitivity analysis, not a candidate strategy.

| Feature | Q1 | Q2 | Q3 | Q4 | Order rho | Favorable steps |
|---|---:|---:|---:|---:|---:|---:|
| Premium extremeness | -7.77 | -6.72 | -40.01 | -15.24 | -.60 | 2/3 |
| OI 1H drop | -9.49 | -10.65 | -21.32 | -28.33 | -1.00 | 0/3 |
| Volatility intensity | -9.14 | -2.07 | -1.47 | -57.16 | -.20 | 2/3 |
| Premium×OI rank | -2.87 | -5.02 | -23.39 | -38.52 | -1.00 | 0/3 |
| OI×volatility rank | -3.28 | -1.25 | -24.92 | -40.35 | -.80 | 1/3 |

Every feature loses positive quantile ordering. Premium keeps 2/3 favorable adjacent steps but its
overall order reverses, so it fails the contract's tail-robustness condition.

## Strength decision

| Base feature | Full-sample direction core | Regime requirement | Fold requirement | Top-5% order | Repeatable |
|---|---|---|---|---|---|
| Premium extremeness | Pass | Pass (4/5) | Pass (6/9) | **Fail** | No |
| OI 1H drop | Fail | Fail (2/5) | Fail (3/9) | Fail | No |
| Volatility intensity | Fail | Fail (2/5) | Fail (5/9) | Fail | No |

- Strength classification: **`NO_STRENGTH_SIGNAL`**.
- LOW/MEDIUM/HIGH proposal: **No**. The contract permits it only for
  `USABLE_FOR_FORWARD_SCORING`.
- Operational recommendation: **Do not apply a strength score or filter.** Keep C1 unchanged. If
  pursued later, collect genuinely forward data under a separately frozen protocol.
- Operational/PAPER/LIVE/UI changes: none.

## STOP

Research result only. No score rule or operating filter is authorized.
