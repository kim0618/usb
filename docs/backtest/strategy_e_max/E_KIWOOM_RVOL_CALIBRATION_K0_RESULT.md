# E-KIWOOM-CAL K0 result — source translation, Massive RVOL vs Kiwoom RVOL

Protocol frozen before the run: `e_kiwoom_rvol_calibration_k0_rules.json`, canonical sha256
`cc47269f63837c4ad573d6db4ecb39d13a34d8ce6d686af6253a2237bfa84039`. No limit was changed after
seeing a number. No return was computed in this phase.

**Verdict: `KIWOOM_RVOL_RECALIBRATION_REQUIRED`** — and the way it fails matters more than the
verdict, see "What actually disagrees".

## Overlap

| | |
|---|---|
| Kiwoom store symbols | 2,565 |
| development tape symbols | 3,889 |
| overlap symbols | 2,564 |
| Massive eligible rows (whole development window) | 125,782 |
| **overlap rows (both sides finite RVOL)** | **10,814** |
| overlap sessions | 41, **2026-06-15 .. 2026-09-16** |
| sessions carrying >= 50 rows | 14 (10,676 of the 10,814 rows) |
| Kiwoom denominator depth | median 10 priors, min 5, max 19 |
| rows at the full 20-prior window | **0** |

This is a three-month window whose weight sits in late August and September 2026, not a two-year
validation. Kiwoom's history does not reach far enough back for a single overlap row to use the
frozen twenty-session window; the depth-matched control below exists because of that.

## Distribution

| | Massive | Kiwoom |
|---|---|---|
| RVOL p10 / p50 / p90 | 0.50 / 1.69 / 8.52 | 0.36 / 1.59 / 20.33 |

| measure | value | limit | |
|---|---|---|---|
| Spearman | 0.668 | >= 0.85 | FAIL |
| Pearson | 0.545 | >= 0.70 | FAIL |
| median ratio (K/M) | 1.067 | 0.80 - 1.25 | PASS |
| ratio p10 | 0.292 | >= 0.40 | FAIL |
| ratio p90 | 4.149 | <= 2.50 | FAIL |
| log-ratio sd | 1.33 (about +/-3.8x at one sigma) | - | |
| relative error p50 | 51% | - | |

The **level** translates well: the median Kiwoom RVOL is 6.7% above Massive's, comfortably inside
the band. The **dispersion** does not: half the rows are off by more than half their value, and the
ratio spans 0.29 to 4.15 between the tenth and ninetieth percentiles.

## Threshold 3.0 agreement

| | value | limit | |
|---|---|---|---|
| Massive passes | 3,355 | | |
| Kiwoom passes | 3,632 | | |
| precision | 0.670 | >= 0.80 | FAIL |
| recall | 0.726 | >= 0.80 | FAIL |
| Jaccard | 0.535 | >= 0.80 | FAIL |
| raw agreement | 0.804 | | |

920 rows that pass on Massive fail on Kiwoom; 1,197 that fail on Massive pass on Kiwoom.

## H5 / R1 / Top3 / B2

| | value | limit | |
|---|---|---|---|
| H5 count, per-session median relative distortion | 0.061 | <= 0.20 | PASS |
| total H5 rows (Massive / Kiwoom) | 291 / 315 | | |
| B2 high-breadth state agreement | 0.976 | >= 0.90 | PASS |
| **identical Top3 set** | **0.364** (8 of 22 decision sessions) | >= 0.80 | **FAIL** |
| identical Top3 among sessions with >= 50 overlap rows | **1 of 14** | | |

## What actually disagrees

The two sources agree on **how many** names pass and on the breadth state. They disagree on **which**
names, and on their order. That is the part R1 and Top3 are built from: on the fourteen sessions with
a real row count, the Kiwoom Top3 matched the Massive Top3 once.

A threshold move cannot repair this. `K_MAP` (3.38) and `K_ROBUST` (3.20) change the pass *rate*;
they do not change the ranking. The disagreement is in the rank of the RVOL column itself.

## Depth-matched control

Recomputing the Massive denominator over the same number of priors Kiwoom used (10,776 rows):

| | as stored | depth matched |
|---|---|---|
| Spearman | 0.668 | 0.717 |
| Jaccard at 3.0 | 0.535 | 0.566 |
| precision / recall | 0.670 / 0.726 | 0.688 / 0.762 |

The shallow Kiwoom window explains only a small part of the gap. What remains is a source
difference: Kiwoom's premarket prints are a different measurement of the same session, and the
rolling median does not normalize that away row by row.

## Threshold candidates (distribution only, no return used)

| id | value | derivation |
|---|---|---|
| K3.0 | 3.00 | unchanged |
| K_MAP | 3.3819 | Kiwoom quantile matching the Massive pass rate at 3.0 (31.02%) |
| K_ROBUST | 3.20 | 3.0 scaled by the median per-row ratio 1.067 |

## K1 data sufficiency (phase 9, before any performance number)

The Kiwoom store holds `pm_dollar_volume`, `pm_bars`, `has_open_0930` per (symbol, session). It does
**not** hold premarket high/low, the 09:00 anchor, the 09:25 price, the 09:30 open or the 09:34
close. A Kiwoom-native performance check is therefore impossible with the data on hand, and
re-walking the minute history for 2,500 symbols over the overlap window (about 12.6 pages per
symbol-session, roughly 1.3 million calls) is exactly the full re-collection the protocol forbids.

The only permitted form is the hybrid **SOURCE_TRANSLATION_DIAGNOSTIC**: the Massive feature frame
with the Kiwoom RVOL column substituted. Its sample is the overlap, so it can only speak about 22
decision sessions (14 with a real row count) against a strategy whose development edge sat in 26
high-breadth sessions over two years. That is not enough to satisfy the selection rule, which
requires paired improvement evidence and forbids choosing on a higher CAGR alone.

## Where this leaves E-MAX V1

The frozen contract is untouched, the paper runtime keeps `RVOL >= 3.0`, and no V2 is preregistered.
The operational consequence is worth stating plainly: the realtime Kiwoom decision is **not** a
reproduction of the development backtest's selection. Same rules, same threshold, materially
different Top3 on most sessions. Whatever the paper book earns is evidence about the Kiwoom-sourced
strategy, not a live confirmation of the Massive-sourced backtest.
