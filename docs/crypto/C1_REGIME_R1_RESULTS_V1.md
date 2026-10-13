# US-B CRYPTO R1 — C1 × Market Regime Results V1

Research date: 2026-10-03. Contract: `C1_REGIME_R1_CONTRACT_V1.md`
(sha256 `b682e01bd168ef1eb4ceadf9952e3befb2edeeb82f5dbed240e873479de7c9e1`).

## Conclusion

**Overall verdict: `INCONCLUSIVE`.** `C1_NO_R5` improves every headline point estimate and avoids a
small net loss, but the paired clustered CI includes zero. It is therefore not established as a C1
improvement. `C1_R3_R4` raises per-trade mean while retaining only 46.4% of signals and surrendering
3,587bp of historical total net; it is `WORSE_THAN_BASELINE`.

This is a post-R0 analysis on the same historical E1/R0 data. It is **not a new out-of-sample
confirmation** and does not authorize an operational regime gate.

## Primary comparison

| Candidate | Verdict | Trades / retention | Mean / median net | PF / win | 7-day clustered 95% CI | Total net |
|---|---|---:|---:|---:|---:|---:|
| C1_ALL | baseline | 1,603 / 100.0% | +10.27 / +1.25bp | 1.146 / 50.4% | [-9.85,+30.18] | +16,463bp |
| C1_NO_R5 | **INCONCLUSIVE** | 1,290 / 80.5% | +12.91 / +2.92bp | 1.183 / 51.0% | [-7.80,+34.15] | +16,653bp |
| C1_R3_R4 | **WORSE_THAN_BASELINE** | 744 / 46.4% | +17.31 / +2.74bp | 1.242 / 50.9% | [-2.91,+40.21] | +12,876bp |

For NO_R5, excluded-trade net is -189bp, so the gate's paired total-net delta is +189bp; its paired
7-day clustered CI is **[-6.81,+7.56]bp per original C1 signal**. The sign is unresolved. R3/R4-only
excludes trades totaling **+3,587bp** (paired delta -3,587bp; CI [-18.99,+14.57]), which is direct
opportunity loss despite the higher retained-trade average.

## Tail robustness

Mean net bp after removing the candidate's largest trades:

| Candidate | Top 1 | Top 3 | Top 1% | Top 5% |
|---|---:|---:|---:|---:|
| C1_ALL | +9.67 | +8.57 | +2.13 | -17.44 |
| C1_NO_R5 | +12.16 | +10.81 | +4.98 | -15.63 |
| C1_R3_R4 | +16.02 | +13.67 | +8.55 | -13.59 |

All variants remain dependent on the right tail: removing the top 5% makes every mean negative.
NO_R5 removes 8 of the 81 baseline large winners (9.9%), below its 19.5% overall exclusion rate.
R3/R4-only removes 34/81 (42.0%), below its 53.6% exclusion rate, but its low retention and positive
opportunity loss independently prevent an improvement verdict. Large winners are the frozen top 5%
of C1_ALL net, not a fitted threshold.

## Fold and year stability

| Candidate | F1 | F2 | F3 | F4 | F5 | F6 | F7 | F8 | F9 | Positive folds | Positive years |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| C1_ALL | +11.1 | -54.5 | +73.8 | +25.7 | +8.7 | +37.1 | +33.4 | -22.3 | +16.7 | 7/9 | 4/5 |
| C1_NO_R5 | +18.7 | -49.2 | +62.9 | +17.9 | +6.9 | +27.4 | +31.1 | -29.1 | +26.1 | 7/9 | 4/5 |
| C1_R3_R4 | +4.1 | -23.5 | +112.0 | +33.3 | +34.2 | +6.7 | +35.8 | -32.1 | +27.7 | 7/9 | 4/5 |

Values are mean net bp. All three retain the same sign pattern: F2 and F8 are negative; 2022 is the
only negative calendar year. Neither gate improves the count stability, and no fold has fewer than
20 retained trades.

## MFE / MAE

| Candidate | MFE mean / median | MAE mean / median |
|---|---:|---:|
| C1_ALL | +174.0 / +126.9bp | -158.0 / -103.5bp |
| C1_NO_R5 | +176.5 / +128.0bp | -162.6 / -109.0bp |
| C1_R3_R4 | +186.1 / +132.0bp | -174.2 / -116.4bp |

The gates select trades with slightly larger favorable excursion but also more adverse excursion.
These are unchanged E1 path observations, not stop simulations.

## Integrity and scope

- All 1,603 E1 trades matched exactly one R0 decision-bar regime: R1 163, R2 383, R3 182, R4 562,
  R5 313; missing/unclassified joins: 0.
- E1 trades and accounting were reused, not reconstructed with a different fill model.
- Exactly the three requested candidates were evaluated; no threshold sweep or added candidate.
- C1/C1x operational code, PAPER, LIVE, deployment, orders, and API paths were not modified.
- No commit or push was performed.

## STOP

Research result only. Do not implement either regime gate from this result.
