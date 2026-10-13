# US-B CRYPTO R0 — BTC Market Regime Results V1

Research date: 2026-10-03. Contract: `CRYPTO_R0_REGIME_CONTRACT_V1.md`.

## Executive conclusion

**R0 found no standalone tradable directional regime.** At the primary 4h benchmark every regime's
LONG and SHORT mean is negative after cost, every directional-edge clustered CI contains zero, and
the edge sign is unstable across years/folds. R1 through R5 are therefore all `NO_EDGE` under the
frozen verdict rule. This is not evidence that regime conditioning is useless: existing C1 is sharply
concentrated in R4 and its existing 4h net is positive in R1/R2/R3/R4 but not R5. Regime is more useful
as context for an existing signal than as a direction selector by itself.

The earlier C2 SHORT candidate remains failed overall. R3 is the only slice near break-even
(+0.33 bp, PF 1.00, n=93), too small and weak to call a rescued SHORT edge.

## A. Regime definitions

The definitions were written and frozen before result computation. In brief:

- R4 Deleveraging/Panic: negative 4h return + top-quartile PIT 24h volatility + falling 4h OI.
- R3 Leveraged Expansion: large 4h move + high volatility + rising OI + basis/funding aligned with move.
- R1 Risk-on: positive 24h return and 12h-vs-72h EWMA trend, plus positive derivatives confirmation.
- R2 Risk-off: symmetric negative trend and negative derivatives confirmation.
- R5 Range/Neutral: eligible residual state.

Priority is R4 → R3 → R1 → R2 → R5. All rolling cutoffs use `[UTC day-30d, UTC day)` only. OI uses
the existing conservative +5m availability convention. Full formulas and missing-data policy are in
the frozen contract.

## B. Coverage

- Window: 2021-02-01 00:00 UTC through 2026-09-23 00:00 UTC exclusive.
- Source grid: 2,966,400 one-minute rows from the existing D5 historical store.
- Eligible: 2,923,200 (98.54%); unclassified warm-up: 43,200 (30 days).
- Inputs: Bybit OHLC, mark/index basis, OI, settled funding, and derived volatility/trend.
- No new market data was collected. Cross-exchange/spot series were not needed by the frozen five-state
  definition; the existing C1 join retains its frozen cross-exchange spot/perp construction.

## C. Regime frequency / duration

| Regime | Eligible share | Minutes | Episodes | Median duration | Mean | P90 |
|---|---:|---:|---:|---:|---:|---:|
| R1 | 31.95% | 934,026 | 15,300 | 4m | 61.0m | 107m |
| R2 | 25.03% | 731,615 | 24,323 | 2m | 30.1m | 47m |
| R3 | 3.58% | 104,542 | 8,813 | 2m | 11.9m | 26m |
| R4 | 6.40% | 187,144 | 7,899 | 5m | 23.7m | 65m |
| R5 | 33.04% | 965,873 | 35,516 | 4m | 27.2m | 51m |

Frequent 1–4 minute flips mean the labels are descriptive states, not ready-to-trade events.

## D/E. LONG vs SHORT and cost-adjusted results

All values below are mean net bp. NO TRADE is exactly 0 bp.

| Horizon | R1 L / S | R2 L / S | R3 L / S | R4 L / S | R5 L / S |
|---|---:|---:|---:|---:|---:|
| 30m | -11.12 / -10.77 | -11.08 / -10.79 | -10.29 / -11.32 | -9.56 / -12.02 | -10.93 / -10.93 |
| 1h | -11.32 / -10.42 | -11.16 / -10.56 | -8.58 / -12.65 | -8.75 / -12.48 | -10.74 / -10.96 |
| 2h | -11.61 / -9.84 | -11.11 / -10.27 | -6.66 / -13.93 | -7.69 / -12.89 | -10.35 / -11.02 |
| 4h | -11.65 / -9.22 | -10.94 / -9.75 | -5.98 / -13.59 | -5.65 / -13.83 | -9.82 / -10.89 |
| 8h | -11.99 / -7.68 | -9.31 / -10.19 | -2.73 / -14.61 | -4.27 / -13.21 | -9.27 / -10.12 |

Primary 4h details (each row has hundreds of thousands of overlapping minute observations; the CI is
clustered by UTC day):

| Regime | n | L gross/net | L win/PF | S gross/net | S win/PF | Edge L−S | Edge 95% CI | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| R1 | 934,026 | -0.16/-11.65 | 39.8%/0.72 | +1.32/-9.22 | 45.0%/0.77 | -2.43 | [-8.45,+3.83] | NO_EDGE |
| R2 | 731,615 | +0.40/-10.94 | 44.2%/0.74 | +0.93/-9.75 | 39.5%/0.77 | -1.20 | [-8.16,+5.22] | NO_EDGE |
| R3 | 104,542 | +5.42/-5.98 | 45.8%/0.89 | -2.96/-13.59 | 44.7%/0.77 | +7.61 | [-12.16,+27.35] | NO_EDGE |
| R4 | 187,144 | +5.78/-5.65 | 49.5%/0.90 | -3.22/-13.83 | 40.0%/0.77 | +8.18 | [-8.36,+25.83] | NO_EDGE |
| R5 | 965,873 | +1.67/-9.82 | 43.0%/0.76 | -0.36/-10.89 | 40.7%/0.74 | +1.07 | [-4.90,+7.06] | NO_EDGE |

Costs reverse even the modest R3/R4 LONG gross drift. The roughly 11 bp round-trip taker burden is
the dominant fact at short horizons. Full medians, side-specific CIs and all horizon PF/win rates are
in `data/research/crypto/r0_regime/results_v1.json`.

## F. Fold/year stability

4h directional edge (bp) in six equal chronological folds:

| Regime | F1 | F2 | F3 | F4 | F5 | F6 |
|---|---:|---:|---:|---:|---:|---:|
| R1 | -12.7 | -14.8 | +1.2 | +4.3 | +4.9 | -1.1 |
| R2 | -1.3 | -6.5 | +2.8 | -11.3 | +4.2 | +2.5 |
| R3 | -1.6 | +37.4 | +35.1 | +3.0 | -4.2 | -23.6 |
| R4 | +30.7 | -25.6 | +11.6 | +39.9 | +15.9 | -25.3 |
| R5 | -3.1 | -8.5 | +12.3 | +9.2 | +0.9 | -2.9 |

Every regime changes sign. Calendar-year tables tell the same story: each regime has positive edge in
exactly 3 of 6 partial/full calendar years. No stable R1-LONG or R2-SHORT symmetry exists.

## G. Transition analysis

Transitions use only the first minute after a state change. Net bp shown as LONG / SHORT at 4h.

| Transition | n | 30m edge | 4h L / S / edge | 8h edge | Reading |
|---|---:|---:|---:|---:|---|
| R1→R2 | 4 | +60.64 | +17.93 / -39.10 / +57.03 | +77.61 | Too sparse; no claim |
| R2→R1 | 2 | -30.20 | -36.75 / +16.47 / -53.22 | -151.49 | Too sparse; no claim |
| R3→R2 | 2,120 | +3.77 | +0.56 / -20.00 / +20.56 | +42.90 | Rebound-shaped, not R2 SHORT |
| R4→R1 | 2,236 | -2.88 | -15.55 / -5.07 / -10.48 | -19.91 | No LONG hand-off edge |

Direct R1↔R2 changes are rare because R5 or an extreme state usually intervenes. Treating the four
R1→R2 and two R2→R1 observations as evidence would be invalid. Complete transition tables are in the
JSON artifact.

## H. C1 placement by regime

| Regime | Event starts | Share | C1 minutes | Existing C1 4h net | PF | Win rate |
|---|---:|---:|---:|---:|---:|---:|
| R1 | 163 | 10.2% | 1,554 | +14.32 bp | 1.31 | 45.4% |
| R2 | 383 | 23.9% | 4,955 | +3.77 bp | 1.05 | 53.5% |
| R3 | 182 | 11.4% | 1,769 | +27.52 bp | 1.40 | 49.5% |
| R4 | 562 | 35.1% | 6,753 | +14.00 bp | 1.19 | 51.4% |
| R5 | 313 | 19.5% | 3,468 | -0.61 bp | 0.99 | 47.9% |

C1 is disproportionately a panic/deleveraging signal: 35.1% of starts occur in R4 although R4 covers
only 6.4% of eligible time. C1's strongest mean occurs in R3, while R5 removes the historical edge.
This is a post-hoc contextual split and not permission to gate C1 in operation.

## I. Existing SHORT failures by regime

| Regime | All C2 trades | Losing trades | Mean net | PF | Win rate |
|---|---:|---:|---:|---:|---:|
| R1 | 173 | 97 | -41.93 bp | 0.48 | 43.9% |
| R2 | 91 | 50 | -7.84 bp | 0.86 | 45.1% |
| R3 | 93 | 44 | +0.33 bp | 1.00 | 52.7% |
| R4 | 40 | 23 | -5.77 bp | 0.92 | 42.5% |
| R5 | 139 | 83 | -28.65 bp | 0.63 | 40.3% |

Most failures by count occur in R1 and R5. R3 does not establish a viable rescued SHORT: the mean is
economically negligible, PF is effectively 1, and n=93. It deserves diagnosis, not a new entry rule.

## J/K/L. Research routing

- **LONG setup research:** R3 and R4 only, because their unconditional 4h/8h LONG gross drift and
  directional edge are the least negative after cost, and C1 is strongest/concentrated there. The next
  question should be event-conditioned execution/cost reduction, not a regime-only LONG.
- **SHORT setup research:** no regime earns promotion. R3 may receive a narrowly scoped audit of the
  existing C2 trades to determine whether +0.33 bp is noise; R1 is actively hostile to the tested C2
  SHORT (-41.93 bp).
- **NO TRADE:** all five regimes for standalone direction. R5 is also the clearest C1 no-edge context.

## M. Next research plan

1. Keep these definitions frozen and validate them forward; do not refit the 30-day quartiles.
2. Test only the interaction `existing C1 × {R3,R4 versus R5}` using event-level, non-overlapping
   observations, clustered uncertainty, and unchanged C1 execution/costs.
3. Audit C2-in-R3 with case studies and outlier/period concentration; do not create a new SHORT rule
   unless independent forward evidence appears.
4. Examine state persistence/hysteresis as measurement quality research, not as an entry condition;
   median 2–5 minute episodes make raw state changes too noisy.
5. Preserve 30m/1h/2h/4h/8h strictly as benchmarks.

## STOP

R0 ends here. No C1/C1x or C2 implementation changed; no new entry strategy was created; PAPER, LIVE,
deployment and production were not changed; no threshold sweep was run; no commit or push was made.
