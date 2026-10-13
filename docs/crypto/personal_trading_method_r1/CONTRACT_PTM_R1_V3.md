# PTM-R1 Contract V3: 1D-Only Thesis (Stage 0)

Status: FROZEN before the V3 Stage 0 run (2026-10-13). **Last structural candidate** of the
flow-thesis PTM-R1 line. If V3 Stage 0 does not match the user's holding style, flow-thesis PTM-R1
becomes a termination candidate.

Unchanged from `CONTRACT_PTM_R1_V1.md` (sha 752cbd19...): split, data sources, parity rule, lean rule
(no dead band), policies A/B/C, catastrophic layer, MAX HOLD 48 h, cost, controls, overfitting rules.
Replaced: state definitions, families, invalidation, plus new warning states.

## 0. Structural search lineage (multiple-testing record)

| version | big direction / thesis | Stage 0 verdict | outcomes seen |
|---|---|---|---|
| V1 | rolling 1D = 4H (production lean), thesis I1..I4 | STAGE0_RESEARCHABLE, thesis too fast | none |
| V2-A | completed-block 1D = 4H, thesis I4 | V2_THESIS_TOO_FAST | none |
| V2-B | persistent rolling 1D = 4H, thesis I4 | V2_THESIS_TOO_FAST | none |
| V3 | completed-block 1D alone | this run | none |

Total structural variants on TRAIN: 4. No price, return, PnL, MFE, MAE, PF or win rate of any split
has been computed or read at any version. VALIDATION and HOLDOUT have never been read.
**If V3 passes Stage 0: RULE FREEZE.** No further thesis-structure change in R1; the main study
uses V3 exactly as written here.

## 1. States (all from Binance 1m taker flow, no price)

| horizon | state at boundary t | role |
|---|---|---|
| D | lean of the last **completed UTC day** block | **thesis** |
| H4 | lean of the last completed UTC 4 h block | entry filter, warning |
| H1 | lean of the last completed UTC 1 h block | entry confirmation / pullback, warning |
| M | rolling 10 minutes | timing only, warning |

Lean: BUY if buy > sell, SELL if sell > buy, EVEN if equal, NONE if any minute missing/invalid or
total zero. Identical to V2-A's arrays. D therefore changes only at UTC 00:00.

## 2. Thesis

- thesis(t) = D(t) when D(t) is BUY (+1) or SELL (-1).
- D(t) = EVEN: **NEUTRAL / NO_NEW_POSITION**. D(t) = NONE: undefined. No setup in either.
- **Thesis episode**: a maximal run of minutes with the same non-zero D.
- **I1 (primary invalidation)**: for a position in direction dir opened at t, the first minute
  t' > t with D(t') = -dir.
- NEUTRAL during a position: counted (first D = EVEN or NONE before I1), not treated as I1 in Stage 0.
- 4H, 1H and 10M never invalidate the thesis.

## 3. Entry families (LONG dir = +1, SHORT dir = -1, symmetric; at most 3)

FULL(t, dir) := D = H4 = H1 = M = dir. A setup is the onset of FULL (FULL(t) and not FULL(t-1));
entry for later stages is the open of minute t+1. The 10M state turning to dir (or already being dir
when the higher states align) is the timing confirmation.

Family by the gap segment G = maximal run of minutes ending at t-1 with FULL false (bounded below by
the first minute where all four states are defined), checked in this order:

1. G empty (series start) or some minute of G has D != dir: **F1 TREND_CONFIRM** (sub-tag
   THESIS_START).
2. else some minute of G has H4 != dir: **F3 DEEP_PULLBACK_RETURN** if some minute has H4 = -dir,
   else UNCLASSIFIED_EVEN.
3. else some minute of G has H1 != dir: **F2 SHALLOW_PULLBACK_RETURN** if some minute has
   H1 = -dir, else UNCLASSIFIED_EVEN.
4. else (only M was off): **F1 TREND_CONFIRM** (sub-tag M_REALIGN) if some minute has M = -dir,
   else UNCLASSIFIED_EVEN.

So F1 = all four aligned with the 10M confirming (fresh thesis or a 10M-only dip), F2 = 1H dipped
against while 1D and 4H held, F3 = 4H dipped against while 1D held and has returned with 1H and 10M.
NO_TRADE is a state (D neutral/undefined, or the family conditions absent), not a family.

## 4. Dedup (three counts, none treated as independent outcome samples)

- raw: every setup.
- **primary (same thesis)**: duplicate if an earlier accepted setup of the same family and direction
  is in the same thesis episode and fewer than 48 h earlier.
- **48 h overlap-adjusted N**: per family, LONG and SHORT merged in time, skip a setup within 48 h of
  the family's previous accepted setup. Also reported across all families.

## 5. Warning states (diagnostic only, never an exit in Stage 0)

For a thesis in direction dir: **W1** H4 = -dir; **W2** H1 = -dir and H4 = -dir;
**W3** M = -dir and H1 = -dir and H4 = -dir.

## 6. Stage 0 measurements (TRAIN only)

A. D state frequency and thesis-episode duration (median, mean, p90; episodes touching the series
ends dropped). B. Thesis survival after entry at 4/8/12/24/48 h (I1 not yet occurred), per family,
LONG+SHORT pooled and per direction; a setup whose horizon passes the TRAIN end without I1 is
excluded from that horizon's denominator. C. Time-to-I1 distribution. D. F1/F2/F3 LONG/SHORT
counts (raw, primary). E. 48 h overlap-adjusted N. F. W1/W2/W3: share of thesis minutes, number of
runs, run duration. G. Per thesis episode: number of H4, H1, M state changes (and per thesis day).
H. Per thesis episode: H4 excursions to -dir and how many return to dir before the episode ends.
I. Per primary setup, within [entry, min(I1, entry + 48 h)]: share where W1 occurs before I1; among
those, share where I1 follows within the window; share of I1 events that occur while W1 is active;
number of W1 runs before I1. Setups whose 48 h window passes the TRAIN end are excluded from I.
Also: NEUTRAL occurrences, direction balance (LONG share) per family, and V1/V2-A/V2-B/V3 state
comparison (1D flips/day, thesis median duration, 12/24/48 h survival, family counts, overlap N).

## 7. Verdict rules (fixed now)

A family "qualifies" when, pooled LONG+SHORT over its primary setups: survival 12 h >= 40 %,
24 h >= 20 %, and 48 h >= 5 % (some long-lived theses must exist).

- **E CONTRACT_OR_DATA_PROBLEM**: parity, coverage or the in-run leak check fails.
- **B V3_THESIS_TOO_SLOW**: D thesis-episode median > 96 h **and**, for every family, the share of
  primary setups invalidated (I1) within 48 h is < 25 % (the thesis would almost never act as a
  position-management rule inside MAX HOLD).
- **A V3_THESIS_TOO_FAST**: D thesis-episode median < 24 h, or no family qualifies.
- **C V3_FAMILY_TOO_RARE**: some family qualifies but no qualifying family has overlap-adjusted
  N >= 100.
- **D V3_RESEARCHABLE**: otherwise.

Checked in the order E, B, A, C, D. Direction balance is reported, not gated.
