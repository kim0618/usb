# Strategy E-MAX - M5 Exposure Scaling Result V1

| | |
|---|---|
| Stage | `E-MAX-M5`, phase B, run once under protocol `d413da0` (rules `b2a89a71…`) |
| Run id | `em5-7e65042026b5` |
| Result | `strategy_e_max_m5_result_v1.json`, sha256 `084c0d352cead4cf…` (byte-identical on a second, independent run) |
| Runtime | `data/runtime/strategy_e_max/m5_runs/em5-7e65042026b5/` (gitignored) |
| Evidence label | **E-MAX DEVELOPMENT** (reused development data; not OOS) |
| Financing | FINANCING_COST NOT MODELED; BROKER_LEVERAGE_FEASIBILITY NOT VALIDATED; research only |
| **Final gate** | **`E-MAX-M5 PASS — 2.0X SELECTED`**; **`M6 AUTHORIZED`** |

## A. Preflight / protocol freeze

- The start HEAD was `0c74c22` (M4 result). M0 to M4 are ancestors and every checksum verified.
  M4 kept X1 and authorized M5.
- `exposure.py` had been written at 18:00 on 2026-09-21, before the session was interrupted.
  No M5 performance had been computed; there was no `m5_runs` directory. The file was committed
  unchanged with the protocol.
- The protocol was committed as `d413da0` before any M5 number existed.
- The E-R3 prechecks were re-run and all passed.
- **E1 reproduces M4 X1 byte for byte** (trades `fee44b84…`, daily `97f9d50c…`).
- **Invariants held.** The trade set is identical across E1, E15 and E20. E15 and E20 are exactly
  1.5× and 2.0× E1 in every scenario, and the result was deterministic.

## B. Current E-MAX comparator (E1)

The comparator is R1 with max 3, B2 and X1, at a 1.0x global multiplier. At 10 bp:

| metric | value |
|---|---|
| CAGR | 19.54% |
| MDD | -15.45% |
| Calmar | 1.26 |
| Sharpe | 1.05 |

These equal M3's B2 and M4's X1.

## C. Exposure definitions

| candidate | normal session | high-breadth session (26 sessions) |
|---|---:|---:|
| E1 | 1.0x | 1.5x |
| E15 | 1.5x | 2.25x |
| E20 | 2.0x | 3.0x |

Weights are equal across executable positions and scaled; for example, E20 on a high-breadth
session with 3 positions is 1.0 each. Cost scales with notional (exposure × net). The MDD comes
from the levered, compounded series.

## D. Trade-set identity

All three candidates have 480 standard trades, 96.19% coverage, the same entries, exits and
prices, a trade-level PF of 1.367, and a top1 share of HIMS 15.6%. A global scalar leaves the
contribution shares unchanged.

## E. Performance at 10 bp

| metric | E1 | E15 | E20 |
|---|---:|---:|---:|
| cumulative | +40.49% | +62.52% | **+85.00%** |
| CAGR | 19.54% | 29.04% | **38.12%** |
| MDD | -15.45% | -22.80% | **-29.85%** |
| Calmar | 1.26 | 1.27 | 1.28 |
| Sharpe | 1.05 | 1.05 | 1.05 |
| Sortino | 1.70 | 1.70 | 1.70 |
| PF (trade) | 1.367 | 1.367 | 1.367 |
| session mean | +7.76 bp | +11.65 bp | +15.53 bp |

Cost sensitivity, as CAGR / MDD:

| cost | E1 | E15 | E20 |
|---|---|---|---|
| 0 bp | 36.27% / -9.74% | 57.03% / -14.44% | 79.41% / -19.03% |
| 5 bp | 27.63% / -12.08% | 42.35% / -18.13% | 57.43% / -24.13% |
| **10 bp** | **19.54% / -15.45%** | **29.04% / -22.80%** | **38.12% / -29.85%** |
| 15 bp | 11.96% / -18.69% | 16.96% / -27.20% | 21.17% / **-35.14%** |
| 20 bp | 4.85% / -21.81% | 6.01% / -31.36% | 6.27% / **-40.04%** |

**At 15 bp or more, E20's MDD falls below the -35% ceiling.** The gate uses 10 bp only, so this
does not change the verdict. But E20 has only 5.15 pp of drawdown room at 10 bp, and about 5 bp
of extra cost removes it.

## F. Eligibility (M0 gate plus the catastrophic rule)

All three candidates pass:

| | coverage | mean | PF | MDD >= -35% | top1 <= 73.38% | no catastrophic session |
|---|---|---|---|---|---|---|
| E1 | 96.19% | +7.76 bp | 1.367 | -15.45% | 15.6% | yes |
| E15 | 96.19% | +11.65 bp | 1.367 | -22.80% | 15.6% | yes |
| E20 | 96.19% | +15.53 bp | 1.367 | -29.85% | 15.6% | yes |

No session reaches -100%; the worst E20 session is -14.12%.

## G. Improvement (each candidate against E1)

| | cumulative > E1 | paired mean delta | 95% CI | P(delta <= 0) | result |
|---|---|---:|---|---:|---|
| E15 | yes | +3.88 bp | [-1.28, +9.25] | 0.0691 | PASS |
| E20 | yes | +7.76 bp | [-2.55, +18.49] | 0.0691 | PASS |

**As preregistered, this is not an exposure effect.** Because delta_t = (k − 1) × E1_t, both
candidates have the same P, 0.0691, which is the bootstrap probability that E1's own mean is at
or below zero. The test confirms that the E1 edge is positive at the 90% one-sided level. It says
nothing about whether more leverage is better.

**Winner pool {E15, E20}. The highest CAGR is E20 (38.12%). No tie arose.**

## H. Risk / reward

| | CAGR ratio vs E1 | MDD ratio vs E1 | Calmar delta vs E1 | Stop B (vs E1) | Stop B (vs E-Base) |
|---|---:|---:|---:|---|---|
| E15 | 1.486 | 1.476 | +0.009 | not flagged | not flagged (7.83 vs 1.39) |
| E20 | 1.951 | 1.932 | +0.012 | not flagged | not flagged (10.28 vs 1.81) |

Against E1, Stop B clears by less than 0.02 for both candidates. Leverage raises return and
drawdown almost one for one, so risk-adjusted efficiency does not change (Calmar 1.26 → 1.28,
Sharpe 1.05 in all three).

## I. Tail risk (10 bp)

| | worst session | worst week | worst month | worst quarter | longest recovery | longest losing run | equity <= 0 |
|---|---:|---:|---:|---:|---:|---:|---|
| E1 | -7.06% (2026-06-29) | -8.38% (W27) | -8.20% (2025-10) | -7.13% (2025Q4) | 277 | 4 | no |
| E15 | -10.59% | -12.50% | -12.15% | -10.65% | 277 | 4 | no |
| E20 | **-14.12%** | **-16.56%** | **-16.00%** | **-14.12%** | 278 | 4 | no |

**Session-return concentration** is the same for all three candidates, as scaling predicts.
Summed over the development period:

- the top session (2026-08-04) supplies **20.2%**;
- the top 3 (2026-08-04, 2026-04-30, 2026-07-28) supply **54.8%**;
- the top 5 supply **84.6%**.

Leverage multiplies a return stream whose total leans on a handful of sessions.

## J. Breadth interaction

| | normal sessions (454; 211 active) | high-breadth sessions (26; all active) |
|---|---|---|
| E1 exposure / summed return | 1.0x / +19.8% | 1.5x / +17.4% |
| E15 | 1.5x / +29.8% | 2.25x / +26.1% |
| E20 | 2.0x / +39.7% | **3.0x** / +34.9% |

- **The high-breadth days carry about 47% of the summed return from 5% of the sessions.**
- **The MDD window** runs from 2025-03-14 to 2026-02-13 (232 sessions) and contains **no
  high-breadth session**. It is driven entirely by normal sessions in the legacy period, summing
  to -15.8%, -23.8% and -31.7% for E1, E15 and E20.
- The 3.0x days have so far only added return, but that is a property of this sample, which holds
  26 high-breadth sessions, all in block 4.

## K. Concentration

For every candidate the concentration is: 272 symbols; top1 HIMS 15.6%; top five HIMS, BLZE,
ITRI, SOFI and FVRR with 66.1%; HHI 0.0099. These are identical because a global scalar cancels
out of the shares.

## L. Blocks (10 bp)

| block | E1 mean / compounded / block MDD | E15 | E20 |
|---|---|---|---|
| 1 | +4.65 bp / +5.39% / -4.56% | +6.97 / +7.92% / -6.82% | +9.30 / +10.34% / -9.06% |
| 2 | -2.77 / -3.82% / -8.08% | -4.15 / -6.08% / -12.14% | -5.54 / -8.54% / -16.19% |
| 3 | -4.31 / -5.46% / -10.13% | -6.47 / -8.39% / -15.03% | -8.63 / -11.42% / -19.81% |
| 4 | +33.49 / +46.61% / -10.56% | +50.24 / +75.01% / -15.61% | +66.99 / **+106.97%** / -20.51% |

All three candidates have **2 of 4 positive blocks**; leverage does not fix block instability.
Legacy and narrow: E20 -7.93% cumulative. Broad: E20 +100.94%.

## M. Monthly / quarterly (10 bp)

All three candidates have 18 positive and 6 negative months, and 5 positive and 3 negative
quarters.

| | best month | worst month | best quarter | worst quarter |
|---|---|---|---|---|
| E1 | 2026-08 +12.72% | 2025-10 -8.20% | 2026Q3 +24.64% | 2025Q4 -7.13% |
| E15 | 2026-08 +19.27% | 2025-10 -12.15% | 2026Q3 +38.31% | 2025Q4 -10.65% |
| E20 | 2026-08 +25.92% | 2025-10 -16.00% | 2026Q3 +52.90% | 2025Q4 -14.12% |

E20 quarters: 2024Q4 +0.9%, 2025Q1 +13.0%, Q2 -13.6%, Q3 +6.0%, Q4 -14.1%, 2026Q1 -0.9%,
**2026Q2 +36.2%, 2026Q3 +52.9%**. The leverage gain comes from the two broad-coverage quarters.

## N. Financing limitations

The following are not modelled:

- margin interest;
- intraday financing;
- broker leverage rules;
- buying-power reduction;
- forced liquidation.

`FINANCING_COST = NOT MODELED` and `BROKER_LEVERAGE_FEASIBILITY = NOT VALIDATED`. E20 means 2.0x on
normal sessions and 3.0x on high-breadth sessions. That is research notional, **not a live
authorization**, and Common Risk V1 allows no leverage.

## O. Reproducibility

Run 1 (628 s) and run 2 (`repeat-2`, separate process) give the same result digest `084c0d35…`. `REPEAT_CHECK.json` records `identical` and `artifacts_identical` as true. All 7 artifacts are byte-equal, and both runs recorded HEAD `d413da0`.

## R. Gate

```text
E-MAX-M5 PASS — 2.0X SELECTED
M6 AUTHORIZED
```

Under the frozen M0 rules, E20 is selected:

- both E15 and E20 are eligible and pass the improvement condition (P 0.0691);
- E20 has the highest CAGR.

M6 is authorized because the carried configuration (E20) passes every eligibility condition and
stop conditions A and D do not hold.

**Configuration carried into M6 (E-MAX V1 candidate):**

- R1 ordering (premarket RVOL descending);
- max 3;
- B2 breadth exposure;
- X1 (09:30 open to 09:34 close);
- 2.0x global exposure, so 2.0x on normal sessions and 3.0x on high-breadth sessions;
- 10 bp primary cost.

**What this does and does not establish:**

- M5 chose how much risk to put on an edge that already existed. It found no new edge: Calmar and
  Sharpe are flat.
- E20's drawdown room is thin. MDD is -29.85% against a -35% ceiling, and the ceiling is breached
  from 15 bp.
- Stop B clears by less than 0.02.
- Returns lean on a few sessions (the top 5 give 85%) and on 2026Q2-Q3.
- The data is reused development data, labelled E-MAX DEVELOPMENT.

No exposure multiplier outside {1.0, 1.5, 2.0} may be tested.
