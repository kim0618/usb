# E-MAX V1 — Kiwoom RVOL calibration, K0 protocol (frozen before any result)

Rules file: `e_kiwoom_rvol_calibration_k0_rules.json`, canonical sha256 in the `.sha256` beside it.
This document is the human-readable copy; the JSON is authoritative.

## Why

E-MAX V1 was developed on Massive data, where the frozen H5 condition is `premarket_rvol >= 3.0`.
The realtime runtime computes RVOL from Kiwoom, whose premarket volume is a different measurement
(0.49-0.68x Massive on the same sessions, measured in CURRENT_PAPER_BASELINE_V1). RVOL is a ratio of
that measure to its own rolling median, so a constant source factor cancels; what is unknown is
whether it cancels *stably* enough for the same 3.0 line to select the same names.

This stage answers that one question. The strategy is not re-optimized: the only axis is the RVOL
threshold, and even that is only reconsidered if the translation fails the limits fixed below.

## What is fixed

`premarket_gap > 0`, `position_in_premarket_range >= 0.8`, `return_0900_0925 > 0`, R1 (RVOL DESC),
max 3 with no backfill, B2, exposure, 09:30 entry, 09:34 exit, the cost model, and the Massive
reference threshold 3.0 itself.

## Kiwoom RVOL

Exactly the frozen definition, from Kiwoom only: today's premarket dollar volume over the rolling
median of the prior staged sessions, window 20, minimum 5. Mixing a Massive numerator with a Kiwoom
denominator, or the reverse, is forbidden.

## Overlap

A row is compared only when both stores hold that `(symbol, session)` and both produce a finite
RVOL. Overlap symbols, sessions and rows are reported with their date range. Kiwoom history is
recent, so the result is stated as covering that range and never as a two-year validation.

## The limits, fixed before looking

| measure | limit |
|---|---|
| Spearman correlation of RVOL | >= 0.85 |
| Pearson correlation of RVOL | >= 0.70 |
| median Kiwoom/Massive RVOL ratio | 0.80 - 1.25 |
| ratio p10 and p90 | inside 0.40 - 2.50 |
| pass agreement at 3.0 (Jaccard) | >= 0.80 |
| recall of Massive passes | >= 0.80 |
| precision of Kiwoom passes | >= 0.80 |
| per-session H5 count distortion (median relative) | <= 0.20 |
| identical Top3 sets | >= 80% of decision sessions |
| identical B2 high-breadth state | >= 90% of sessions |

Sufficiency first: at least 10 overlap sessions, 5,000 overlap rows and 5 decision sessions with
candidates, otherwise the verdict is `E-KIWOOM-CAL INCONCLUSIVE - INSUFFICIENT OVERLAP`.

All limits hold -> `KIWOOM_RVOL_3P0_TRANSLATION_ACCEPTED`. Any limit fails ->
`KIWOOM_RVOL_RECALIBRATION_REQUIRED`, which is the only thing that authorizes K1.

## Threshold candidates (only if K1 is authorized)

At most three, none of them derived from a return: `K3.0` (unchanged), `K_MAP` (the Kiwoom threshold
whose pass rate equals Massive's pass rate at 3.0) and optionally `K_ROBUST` (3.0 scaled by the
median per-row ratio). A grid over 2.0, 2.1, 2.2 ... is forbidden.

## Paper runtime

The running paper session keeps `RVOL >= 3.0` throughout this study. Even a K1 result cannot change
the paper configuration; that needs a separately approved `STRATEGY_E_MAX_V2_KIWOOM` contract. V1 and
its artifacts stay untouched.
