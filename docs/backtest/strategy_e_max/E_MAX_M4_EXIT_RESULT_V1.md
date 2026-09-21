# Strategy E-MAX - M4 Aggressive Exit Result V1

| | |
|---|---|
| Stage | `E-MAX-M4`, phase B, run once under protocol `362b978` (rules `6b3405a7…`) |
| Run id | `em4-99617c1874d4` |
| Result | `strategy_e_max_m4_result_v1.json`, sha256 `a4f5323bc20a4b93…` (byte-identical on a second, independent run) |
| Runtime | `data/runtime/strategy_e_max/m4_runs/em4-99617c1874d4/` (gitignored) |
| Evidence label | **E-MAX DEVELOPMENT** (reused development data; not OOS) |
| **Final gate** | **`E-MAX-M4 NO_USEFUL_ENHANCEMENT`**: X1 is kept. **`M5 AUTHORIZED`** |

## A. Preflight / protocol freeze

- The start HEAD was `7312f45` (M3 result). M0 to M3 are ancestors and every checksum verified.
  M3 is B2, with M4 AUTHORIZED.
- The protocol was committed as `362b978` before any M4 number existed.
- The E-R3 prechecks were re-run and all passed.
- **X1 reproduces M3 B2 byte for byte** (trades `fee44b84…`, daily `97f9d50c…`).
- **Invariants held.** X1 and X2 share selection, entries and the B2 exposure map. Positions that
  were not extended have identical records, and delta is 0 on sessions with no extended position.

## B. Comparator X1

X1 is R1 with max 3, B2 exposure and the exact 09:34 exit. At 10 bp:

| metric | value |
|---|---|
| CAGR | 19.54% |
| cumulative | +40.49% |
| MDD | -15.45% |
| Calmar | 1.26 |
| Sharpe | 1.05 |
| session mean | +7.76 bp |

These equal M3's B2.

## C. X2 definition (M0)

E-D3 decides at 09:34 first. If that exit is VALID and close(09:34) > open(09:30) (strict, raw
prices), the position is held and exits at the exact 09:44 close. Otherwise it exits at 09:34.

The decision is taken once the 09:34 bar has closed. A missing 09:44 bar leaves the position
`UNRESOLVED_EXIT`, with no fallback. The cost is the same time-independent 10 bp round trip; this
is a stated limitation.

## D. Exit decision funnel

| | count |
|---|---:|
| X1 valid 09:34 exits | 480 (unresolved 21) |
| X2 09:34 exits (not extended) | 228 |
| X2 extension attempts | 252 |
| X2 valid 09:44 extended exits | 251 |
| X2 09:44 unresolved | 1 (`NO_TRADE_MISSING_EXIT_BAR`) |
| extension rate among valid 09:34 exits | **52.5%** |

X2 ends with 479 standard trades and 95.99% coverage, against X1's 480 and 96.19%.

## E. Continuation diagnostics (the 251 extended positions, gross, unweighted)

| | mean | median | positive rate |
|---|---:|---:|---:|
| gross at 09:34 | +161.97 bp | +90.92 bp | 100% (by construction) |
| gross at 09:44 | +165.43 bp | +88.02 bp | 80.1% |
| incremental 09:34 → 09:44 | **+3.08 bp** | +2.75 bp | 51.4% |

The extra ten minutes add about 3 bp on average, and the direction is close to a coin flip.
About 20% of positions that were profitable at 09:34 end the hold at or below entry.

## F. Performance at 10 bp

| metric | X1 | X2 |
|---|---:|---:|
| trades / coverage | 480 / 96.19% | 479 / 95.99% |
| trade mean | +21.43 bp | +23.17 bp |
| median trade | -2.42 bp | -24.01 bp |
| win rate | 49.4% | 41.3% |
| PF | 1.367 | 1.338 |
| session mean | +7.76 bp | +9.90 bp |
| cumulative | +40.49% | +54.48% |
| CAGR | 19.54% | 25.65% |
| MDD | -15.45% | -11.84% |
| Calmar | 1.26 | 2.17 |
| Sharpe | 1.05 | 1.21 |
| Sortino | 1.70 | 2.09 |
| worst session | -7.06% (2026-06-29) | -7.06% (2026-06-29) |
| worst week | -8.38% (2026-W27) | -8.74% (2026-W27) |
| worst month | -8.20% (2025-10) | -7.77% (2025-10) |
| worst quarter | -7.13% (2025Q4) | -6.02% (2025Q2) |
| recovery duration | 277 sessions | 233 sessions |
| longest losing run | 4 | 6 |
| positive E-D6 blocks | 2/4 | **4/4** |

Cost sensitivity, as CAGR:

| cost | 0 bp | 5 bp | 10 bp | 15 bp | 20 bp |
|---|---:|---:|---:|---:|---:|
| X1 | 36.27% | 27.63% | 19.54% | 11.96% | 4.85% |
| X2 | 43.22% | 34.15% | 25.65% | 17.68% | 10.21% |

## G. Eligibility of X2 (M0 gate)

X2 passes every condition:

| condition | observed |
|---|---|
| integrity | PASS |
| coverage | 95.99% |
| session mean | +9.90 bp |
| PF | 1.338 |
| MDD | -11.84% |
| top1 share | HIMS 16.5% |

## H. Improvement test (comparator X1)

| | value | required | result |
|---|---|---|---|
| cumulative delta | +13.98 pp | > 0 | PASS |
| paired mean delta | +2.13 bp | > 0 | PASS |
| 95% CI of delta | [-2.67, +6.82] bp | - | - |
| P(delta <= 0) | **0.193** | <= 0.10 | **FAIL** |

**X2 fails the frozen improvement gate.** Its point estimates are better, but the paired session
difference is not distinguishable from zero at the preregistered level. Under M0 the result is
`NO_USEFUL_ENHANCEMENT`, and it is not re-read.

## I. Tail dependence and changed sessions

- **Changed sessions.** 169 sessions had at least one extended position. Of these, 90 have a
  positive delta, 79 negative and 0 zero.
- **Tail.** The total delta is +1,022.8 bp-sessions. The top session (2026-05-08, INOD +20.0%
  net) supplies **38%**, and the top 3 (2026-05-08, 2026-08-19, 2026-04-13) supply **93%**. The
  top 5 sum to 140% of the total, so the other changed sessions are net negative together.

## J. Risk / reward (M0 stop B)

- CAGR ratio X2/X1 = 1.31.
- MDD ratio = 0.77.

Stop B is not flagged; the drawdown is shallower.

## K. Concentration (10 bp net contribution)

| | unique symbols | top1 | top5 | top10 | HHI | largest winner | largest loser |
|---|---:|---|---:|---:|---:|---|---|
| X1 | 272 | HIMS 15.6% | 66.1% | 115.4% | 0.0099 | BAND +12.34% (04-30) | INBX -7.76% (05-11) |
| X2 | 271 | HIMS 16.5% | 65.1% | 107.0% | 0.0100 | INOD +20.00% (05-08) | INBX -7.76% (05-11) |

## L. Narrow versus broad coverage (paired delta, diagnostic)

| period | sessions | changed | positive / negative | mean delta per session |
|---|---:|---:|---|---:|
| FULL | 480 | 169 | 90 / 79 | +2.13 bp |
| LEGACY_NARROW | 376 | 83 | 52 / 31 | **+3.65 bp** |
| BROAD_COVERAGE | 104 | 86 | 38 / 48 | **-3.37 bp** |

Unlike M1 and M3, the X2 effect is not confined to the broad period. It is **positive in the
narrow 30-symbol period and negative in the broad period**, where the M1 and M3 gains live. X2
and B2 pull in opposite directions in block 4.

## M. Chronological blocks (10 bp)

| block | trades X1 / X2 | X1 mean | X2 mean | X1 compounded | X2 compounded | paired delta mean | changed sessions (+/-) |
|---|---|---:|---:|---:|---:|---:|---|
| 1 | 66 / 66 | +4.65 bp | +6.67 bp | +5.39% | +7.86% | +2.03 bp | 28 (15/13) |
| 2 | 70 / 70 | -2.77 bp | +0.70 bp | -3.82% | +0.06% | +3.47 bp | 30 (20/10) |
| 3 | 48 / 48 | -4.31 bp | +0.96 bp | -5.46% | +0.62% | +5.27 bp | 20 (15/5) |
| 4 | 296 / 295 | +33.49 bp | +31.25 bp | +46.61% | +42.26% | -2.24 bp | 91 (40/51) |

## N. Reproducibility

Run 1 (747 s) and run 2 (`repeat-2`, separate process) give the same result digest `a4f5323b…`. `REPEAT_CHECK.json` records `identical` and `artifacts_identical` as true. All 6 artifacts, including every exit decision, are byte-equal. Both runs recorded HEAD `c27c401`, an unrelated Strategy D commit that another session made at 17:32. `362b978` is its ancestor, and it changes no Strategy E or E-MAX file.

## Q. Gate

```text
E-MAX-M4 NO_USEFUL_ENHANCEMENT
M5 AUTHORIZED
```

X2 is not selected. P(delta <= 0) = 0.193 is above the frozen 0.10, even though its CAGR, MDD and
block count are better on point estimates. **X1 (exact 09:34 exit) is kept.** No new exit
hypothesis may be added: 09:39, 09:49, trailing, ATR and VWAP exits all stay forbidden.

**M5 is authorized** under M0's precondition, which requires three things, all true here:

1. M1 (R1) and M3 (B2) produced winners;
2. stop conditions A and D do not hold;
3. the carried configuration passes every M0 eligibility condition.

The carried configuration is R1 + max 3 + B2 + X1, with coverage 96.19%, MDD -15.45% and top1
15.6%.

**Configuration carried into M5:** R1 ordering, max 3, B2 breadth exposure (1.0x / 1.5x), and the
exact 09:30 open to 09:34 close. M5 may only test the M0 multipliers 1.0, 1.5 and 2.0.
