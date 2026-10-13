# PTM-R1 V3 Historical Study Contract (TRAIN only)

Status: FROZEN before any outcome is computed (2026-10-13). Rules are the V3 Stage 0 rules
(`CONTRACT_PTM_R1_V3.md` sha c34cc801..., RULE FREEZE) plus the execution, cost, metric, inference,
control and gate definitions below. Nothing here may change after the TRAIN results are seen; a
change is a new version and voids this study.

Scope: TRAIN 2021-02-01 00:00 .. 2023-06-30 23:59 UTC only. VALIDATION and HOLDOUT are not read
(code-level guard; any attempt fails the run). Q123, production and the Manual Recorder are not read
or changed. No new download.

## 0. Lineage

Structural variants examined on TRAIN with state statistics only: V1, V2-A, V2-B, V3 (4). This is
the first time any price outcome is computed, and it is computed for V3 only.

## 1. Frozen V3 rules (restated, not changed)

Thesis = lean of the last completed UTC day (Binance 1m taker flow, no dead band). Entry states:
completed 4 h and 1 h blocks and rolling 10 m. Families F1 TREND_CONFIRM, F2
SHALLOW_PULLBACK_RETURN, F3 DEEP_PULLBACK_RETURN by the V3 gap-segment rule; UNCLASSIFIED_EVEN is not
studied. Primary entry set = V3 primary dedup (same family, direction, thesis episode and < 48 h ->
duplicate). I1 = first boundary after entry at which the completed-day lean is opposite. MAX HOLD
48 h. W1/W2/W3 are diagnostic only and never trigger an exit.

## 2. Data

- Prices: Binance USD-M BTCUSDT perpetual 1m klines, the same 29 monthly TRAIN files as Stage 0
  (now also columns 1..4: open, high, low, close; last-trade prices, not mark).
- Funding: Binance data.vision `futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-YYYY-MM.zip`
  for 2021-02 .. 2023-06 (`calc_time`, `last_funding_rate`).
- Parity of the volume source re-verified as in Stage 0.
- The 14-day aggTrades files (2026-09-09 .. 22) lie inside the HOLDOUT window; reading them would
  break holdout protection, so the intrabar ordering diagnostic is **NOT_RUN**. Ambiguity is instead
  counted on TRAIN (section 4).

## 3. Entry execution

- A setup is detected at boundary t (close of minute t, using only minutes <= t).
- Entry e = minute t+1; entry price P0 = open[e]. No price at or before t is used for the fill.
- An entry is evaluated only if the 48 h window fits in TRAIN: e + 2880 <= last TRAIN minute index.
  Others are `TRAIN_TAIL_EXCLUDED` (counted).
- Direction dir = +1 LONG, -1 SHORT. Returns are fractions of entry notional (underlying move),
  not of margin.

## 4. Exits (all policies share the catastrophic layer and MAX HOLD)

Time exits fill at the **open** of the exit minute x; the path is bars e .. x-1.
- MAX HOLD: x = e + 2880.
- I1 (policy C only): j = first boundary > t with D(j) = -dir; x = min(j + 1, e + 2880).

Price-level exits are adverse stops only (there is no take-profit):
- level for a fraction s: LONG P0 (1 - s), SHORT P0 (1 + s).
- the first bar k in [e, x_time - 1] whose low (LONG) / high (SHORT) touches any active level exits
  in bar k; fill = the nearest active level, or the bar open if the open is already beyond it (gap).
- **Intrabar rule (fixed now): adverse first.** In the exit bar the favourable extreme is ignored
  for MFE; MAE includes the fill. The number of stop exits whose exit bar also printed a new
  favourable extreme is reported as the ordering-ambiguity count.
- Catastrophic: s_cat = 0.40 x (1/L - 0.004). Primary L = 20: s_cat = 0.0184. Sensitivity L = 10:
  0.0384.

Policies on the identical entry set:

| policy | exits |
|---|---|
| A1 | fixed stop 0.5 %, catastrophic, MAX HOLD (I1 ignored) |
| A2 | fixed stop 1.0 %, catastrophic, MAX HOLD (I1 ignored) |
| A3 | fixed stop 2.0 %, catastrophic, MAX HOLD. At L = 20 it is NON_OPERATIVE (catastrophic 1.84 % is hit first); reported as censored. Operative only in the L = 10 sensitivity. |
| B | catastrophic, MAX HOLD (I1 ignored) |
| C | I1, catastrophic, MAX HOLD |

Diagnostic fixed horizons 1, 4, 8, 12, 24, 48 h: exit at open of e + 60 h, no stop of any kind.

## 5. Costs

- Fee: actual taker 5.0 bp per side, charged on entry and exit notional: 0.0005 x (1 + P_exit/P0).
- Spread/depth: pre-registered BASE = 1.184e-6 round trip (D5 realtime measurement p50, as D5.1/D5.2).
- Funding: every settlement whose minute s satisfies open_ms(e) <= s <= open_ms(exit bar) is
  charged (inclusive at both ends, fixed now). Cost fraction = dir x rate x open[s] / P0 (LONG pays a
  positive rate). No fixed funding assumption.
- net = gross - fee - spread - funding. Gross and net are both reported.
- Stress scenario: fee 5.5 bp per side + STRESS 2.537e-4 round trip (existing contract values).

## 6. Metrics (family x direction x policy, and LONG+SHORT pooled)

N, gross mean/median, net mean/median, net > 0 ratio, PF = sum(net > 0) / |sum(net < 0)|, MFE and
MAE mean/median (fractions, from the realised path), holding minutes mean/median, funding cost mean,
total cost mean, catastrophic exit rate, exit-reason counts. Diagnostic horizons: gross/net
mean/median, MFE/MAE.

User-style metrics:
- **LOSS_THEN_RECOVERY** (policy C path): among trades whose adverse excursion reached depth d
  (d in {> 0, 0.5 %, 1.0 %}; the first bar reaching it = k_a), the share that later (a bar after
  k_a, before the C exit) printed the entry price (R_GROSS) and the net break-even price
  P0 (1 + 0.001 + BASE) for LONG, mirrored for SHORT (R_NET).
- **FIXED_STOP_FALSE_EXIT**: among A1 / A2 trades exited by their fixed stop at bar k, the share
  whose policy C path (bars after k, before the C exit) reached R_GROSS / R_NET.
- **MFE_GIVEBACK** per policy: absolute = MFE - gross; percentage = (MFE - gross) / MFE for MFE > 0.
- **INVALIDATION_LOSS**: C trades exited by I1: share with gross < 0, share with net < 0, and the
  net mean/median of the net < 0 ones.
- **HOLD_VALUE**: per trade C net - A1 net and C net - A2 net; quantiles and inference (section 7).

## 7. Independence and inference

- Raw setups are never treated as independent. Reported samples: raw setups (count only), primary
  trades (all metrics), 48 h overlap-adjusted trades (per family, LONG+SHORT merged; net mean and
  PF as robustness).
- **Primary inference: moving-block bootstrap** over consecutive TRAIN UTC days, block 7 days,
  2,000 resamples, seed 20261013, ratio estimator (sum over resampled days / count). Each trade
  belongs to its entry day. SE = bootstrap SD; two-sided p from z = mean / SE (normal).
- Secondary: cluster bootstrap by thesis episode (same resamples count and seed) for P1.
- Paired and control differences resample the same days jointly.

## 8. Primary endpoints and multiple testing

Per family (LONG+SHORT pooled, primary trades, primary cost, L = 20):
- **P1** policy C net mean (test: mean != 0).
- **P2a** paired C - A1 net mean; **P2b** paired C - A2 net mean (test: != 0).
- **P3** PF of C. **P4** catastrophic rate of C. **P5** MFE giveback of C. (P3..P5 descriptive.)

Hypothesis tests: 3 families x {P1, P2a, P2b} = 9, **Holm-Bonferroni at family-wise alpha 0.05**.
Everything else (per direction, other policies, horizons, L = 10, stress, controls) is
secondary/diagnostic and carries no significance claim.

## 9. Controls (pre-registered in V1 section 12, applied to V3)

- **Random timestamp**: per family x direction, as many entries as that cell's primary trades,
  drawn uniformly without replacement (numpy default_rng, seed 20261013 + cell index in the order
  F1 L, F1 S, F2 L, F2 S, F3 L, F3 S) from boundaries t with D(t) = dir that are not raw setups of
  any family and whose 48 h window fits in TRAIN. Policy C exits, same costs.
- **Time shift**: all four state arrays circularly shifted by +7, +14, +30 days inside TRAIN;
  V3 setups, primary dedup and I1 re-derived on the shifted states; outcomes on the unshifted prices;
  policy C.
- **Direction placebo**: same entries, opposite direction, exit at the original C exit time (or the
  placebo's own catastrophic level if touched first), same costs.

## 10. TRAIN gate (fixed now; evaluated per family on policy C, primary cost, L = 20)

- **A TRAIN_FAIL**: C net mean <= 0 or PF <= 1.
- **C TRAIN_PASS_VALIDATION_CANDIDATE**: all of
  1. P1 net mean > 0 and Holm-adjusted p < 0.05;
  2. PF > 1;
  3. lower 95 % bound of (C - random-timestamp control) net mean > 0;
  4. catastrophic rate <= 10 %;
  5. LONG and SHORT C net means both > 0;
  6. 48 h overlap-adjusted C net mean > 0;
  7. neither P2a nor P2b significantly negative (Holm-adjusted p < 0.05 with a negative mean);
  8. stress-cost C net mean > 0.
- **B TRAIN_WEAK**: not A and not C.
- At most 2 candidates. If more than 2 pass, order by Holm-adjusted P1 p, then by the P1 lower
  95 % bound; keep the first 2. Candidates are (family, policy C) only.

VALIDATION is not run in this study.
