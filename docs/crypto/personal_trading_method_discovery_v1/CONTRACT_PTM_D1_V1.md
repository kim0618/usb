# CONTRACT PTM-D1 V1 : Personal Trading Method Discovery (TRAIN only)

Track: US-B crypto / personal trading method. Separate from Q123 (contracts, stores, thresholds and
gates are neither read nor changed), from Wall/Collector/Recorder and from production. LOCAL ONLY.
No orders, no server change, no deployment. R1/R2 results are used only to narrow hypotheses.

## 0. Phases and stop rule

| phase | content | performance read? |
|---|---|---|
| 1 | user disposition -> data questions (section 1) | no |
| 2 | TRAIN market-state census and transitions (section 4) | forward excursion by state only, no trade P&L |
| 3 | finalize <= 5 entry families from the drafts of section 5 (drop or merge only; thresholds fixed here) | no |
| 4 | component study: entries, hold/recovery, loss exits, profit protection, leverage (sections 6 to 10) | yes |
| 5 | compose <= 3 methods by the pre-registered composition rule (section 11) | yes |
| 6 | controls, tail risk, overfitting audit, verdict, freeze <= 2 (sections 12 to 14) | yes |
| 7 | STOP. VALIDATION and HOLDOUT are not read. | |

Data window: TRAIN = 2021-02-01T00:00Z .. 2023-07-01T00:00Z (exclusive). Readers are the R1 readers
(`ptm_r1.data`), whose guard refuses any file outside TRAIN except the lineage parity files.

## 1. User disposition as data questions

| disposition | data question |
|---|---|
| loss alone is not an exit; exit when the thesis is objectively invalid | which observable state change, when it occurs during a losing trade, separates "later recovered" from "did not recover", and does exiting on it beat waiting? |
| cut when wrong, let winners run | does any entry family produce average winner / average loser >= 1.5 and PF >= 1.25 with event-driven exits only? |
| no time rules | all ENTRY/HOLD/EXIT rules below are state or event conditions; time appears only as the research censor (section 3) |
| accepts high leverage, refuses ruin | at which leverage does the safety/liquidation layer end fewer than 10 % of trades before a state exit acts, and what is the account-level tail? |
| wants to keep big winners | which state sequence precedes a large giveback often enough that acting on it keeps more of MFE than it cuts from winners? |

## 2. State arrays (fixed; from R1 V3)

`M` rolling 10 m taker lean; `H1`, `H4`, `D` lean of the latest completed UTC 1 h / 4 h / 1 day block
(`ptm_r1.v3.states_v3`). BUY=+1, SELL=-1, EVEN=0, NONE=9. Boundary `t` is the close of minute `t`.
A rule evaluated at boundary `t` acts at `open[t+1]`.

Price features (new, `ptm_d1.features`), all computed from completed UTC days or from `close[t]`:

- `V[t]` volatility unit = median of (high - low) / close over the last 7 completed UTC days.
- `z3[t]` = (close[t] / close of the completed day 3 days back - 1) / V[t]. Price regime `PR[t]` =
  +1 if z3 >= +1, -1 if z3 <= -1, else 0.
- `VR[t]` volatility regime = HIGH if V[t] > median of V over the last 30 completed days, else LOW.
- `pbz[t]` pullback depth within the current D episode, in V units: for D=+1 (highest high since the
  episode started - close[t]) / close[t] / V[t]; mirrored for D=-1. Deep pullback = pbz >= 1.
- `thesis_age[t]` minutes since the current D run started; `episode` id = D run id.
- `fund[t]` latest settled funding rate; `basis[t]` mark/index - 1 (both signed by trade direction).

Thresholds 1 V, 7 days, 30 days, 3 days are fixed here and are not searched.

## 3. Time

Research censor: 120 h after entry (`CENSOR_MIN = 7200`); the censor rate is reported, and a 48 h
censor sensitivity is reported for the final methods. Time never enters an ENTRY, HOLD or EXIT rule.

## 4. Phase 2 census (states only, no P&L)

For TRAIN: run lengths of D/H4/H1/M; transition matrix of (D, H4) pairs; share of time in each
regime (D x PR x VR); per regime and per alignment count (H4/H1/M agreeing with D), the forward
excursion from boundary to the end of the D episode: max favourable / max adverse excursion in V
units and the signed displacement at the episode end. Also: how often H4 opposite returns to D
within the episode; how often PR agrees with D. This informs section 5 (drop/merge only).

## 5. Entry family drafts (<= 5 after phase 3)

Each is one onset event at boundary `t`, direction `dir`, with NO-TRADE applied (section 10).

- **E1 TREND_ALIGN**: D=dir, PR=dir, H4=dir, and H1 turns to dir (onset of H1==dir).
- **E2 PULLBACK_REALIGN**: D=dir and H4 turns back to dir after having been opposite inside the same
  D episode (onset of H4==dir with a prior H4==-dir in the episode).
- **E3 DEEP_PULLBACK_RETURN**: D=dir, pbz >= 1 at some boundary of the current episode before `t`,
  and H1 turns to dir (onset of H1==dir).
- **E4 FRESH_TURN**: D turns to dir (the completed day's lean is opposite to the previous completed
  day's) and H4==dir at that boundary.
- **E5 VOL_EXPANSION_ALIGN**: VR=HIGH, D=dir, PR=dir and full alignment (H4=H1=M=dir) begins.

One position at a time per family: an onset while that family's trade is open is skipped (so N is
non-overlapping within a family). Entry at `open[t+1]`.

## 6. Baseline exit set, hold and loss exits (<= 5)

Always on: catastrophic safety at the 10x level, `S_CAT(10) = 0.40 * (1/10 - 0.004) = 3.84 %` in the
component study (R1 definition), and the censor. Fills as in R1 (`r1_train.simulate`).

Loss-exit candidates (they act while the trade is at a loss, `gross < 0` at the event boundary;
X1 acts regardless of P&L because it is the thesis itself):

- **X1 FLOW_FLIP**: completed 1D lean is opposite (`D == -dir`). Baseline thesis exit.
- **X2 FLOW_FLIP_CONFIRMED**: X1 only when H4 is also opposite at that boundary; otherwise keep holding
  (next completed day decides again). Weaker than X1.
- **X3 X1 + H4_SEQUENCE**: two consecutive completed H4 blocks opposite with no H1 realignment (H1==dir)
  between them, while at a loss.
- **X4 X1 + ADVERSE_EXPANSION**: while at a loss, a recovery attempt (favourable move >= 0.5 V from the
  adverse extreme without reaching entry) is followed by a new adverse extreme >= 0.5 V beyond the old one.
- **X5 X1 + PRICE_REGIME_FLIP**: PR == -dir at a boundary while at a loss.

HOLD is the complement: while none of the active exits fires, the trade is held whatever the P&L.
Hold/recovery study (section 8) uses X1 as the reference.

## 7. Profit protection (<= 5)

Arming: a protection may act only after MFE >= 1 V (one daily range of open profit). Below that the
trade is left to the loss exits. Candidates:

- **P0 NONE** (reference: X exits only).
- **P1 H1_THEN_H4**: after arming, a completed H1 opposite followed by a completed H4 opposite with no
  new MFE in between.
- **P2 MFE_STALL**: two consecutive completed H4 blocks without a new MFE and H4 opposite at the second.
- **P3 VOL_TRAIL**: retrace from MFE >= 1 V (level fill, adverse-first inside the bar).
- **P4 REALIGN_FAIL**: after arming, H4 opposite, then H1 realigns to dir, then H1 turns opposite again
  without a new MFE since the realignment.

## 8. Hold / recovery study

Population: all family entries under X1 + safety + censor. Anchors (from R2) -0.5 % and -1.0 %.
At the anchor record H4_rel, H1_rel, M_rel, alignment count, thesis_age, fund, basis, VR, pbz,
adverse depth so far, number of recovery attempts so far. Report recovery rate (entry price printed
later) and the net result of waiting (trade net) by the four states of the brief: (A) D held (all),
(B) D held + H4 opposite, (C) H4 realigns to dir after the anchor, (D) H4=H1=M all opposite.
Effect sizes with 7-day block bootstrap and episode-cluster SE. No new thresholds.

## 9. Leverage and account

Isolated margin, bracket 1, MMR 0.004, liquidation distance 1/L - MMR, safety 0.40 x that.
Leverages 5, 10, 20, 50; 100 diagnostic only. Scenarios: A fixed notional (margin = 1/L of notional),
B margin fraction 10 % of equity (notional = 10 % x L). Report liquidation + safety rate, max single
trade loss, p5 trade loss, account return mean and sum, max drawdown of the cumulative account curve
(sequential, no compounding), recovery of that curve.

Quality tiers: three pre-registered quality variables at entry, `Qa` alignment count (3 vs < 3),
`Qb` PR agrees with D, `Qc` VR HIGH. A tier is adopted only if the variable splits net mean with
block-bootstrap p <= 0.10 and the same sign in LONG and SHORT in the component study. Otherwise uniform
leverage. No tier is built from any other variable.

## 10. NO-TRADE

Any undefined state (NONE in D/H4/H1/M, V or z3 undefined); CONFLICT regime (PR == -D); D == EVEN.
Reported, not tuned.

## 11. Composition rule (phase 5)

Reference population for component selection = union of all family entries (each family's own
non-overlapping trades). For each loss exit X2..X5 and protection P1..P4 the paired net difference
versus the reference (X1 / P0) is computed on that population.

- Loss exit chosen = the candidate with the largest positive paired net mean whose block-bootstrap
  p <= 0.10 and whose average winner is not reduced by more than 10 % versus X1. If none, X1.
- Protection chosen = the candidate with the largest positive paired net mean whose p <= 0.10 and whose
  MFE capture ratio (mean gross / mean MFE) rises versus P0. If none, P0.
- Methods: the three entry families with the highest net mean under X1/P0 that also have N >= 60
  become METHOD 1..3 (fewer if fewer qualify), each combined with the chosen loss exit and protection.
  No per-method re-selection of exits.

Total planned performance comparisons: 5 entries + 4 loss exits + 4 protections + 3 quality splits
+ 3 methods = 19, plus controls. Holm is applied over the final methods (<= 3).

## 12. Controls (per method)

Random entry (200 draws of the same N with uniform random boundaries and random direction, same exit
rules; one-sided share of draws with net mean >= observed), time-shift entry (+1, +3, +7 days, same
direction, same exits), opposite-direction placebo (same entries, direction reversed, paired),
directional baseline (same entries, censor-only exit plus safety), fixed-stop baselines (X1 plus a
fixed 0.5 % or 1.0 % stop).

## 13. Metrics

N, mean net, median net, PF, win rate, mean/median MFE, MAE, giveback (abs and ratio), recovery rate,
average winner, average loser, winner/loser ratio, tail loss (p5, min), catastrophic/liquidation rate
by leverage, account-level return, overlap-adjusted N (= N, trades do not overlap within a method),
7-day moving block bootstrap CI (2000 reps, seed 20261013) and episode-cluster SE.

Costs: fee 5.0 bp/side, BASE spread 1.184e-6 round trip, actual funding settlements between entry and
exit (R1 definitions). Stress (5.5 bp, 2.537e-4) reported for the final methods.

## 14. Verdict gates (fixed before any performance is read)

**C. PERSONAL_METHOD_TRAIN_CANDIDATE**: N >= 100; net mean > 0 with block-bootstrap 95 % CI excluding 0
after Holm over the methods; PF >= 1.25; average winner / average loser >= 1.5; random-entry share
<= 0.05; opposite placebo paired difference CI excludes 0; MFE capture ratio >= 0.35; at the
recommended leverage the safety + liquidation rate <= 10 %.
**B. WEAK_METHOD_CANDIDATE**: N >= 60; net mean > 0; PF >= 1.10; point estimate above the random-entry
mean and above the opposite placebo; at least one C gate fails.
**A. NO_PERSONAL_METHOD_FOUND**: otherwise.

At most 2 methods with verdict B or C are frozen for VALIDATION. Rules are frozen as written here
plus the phase 3 amendment (which may only drop or merge drafts). Report sections A..W as in the brief.

## 15. Determinism and provenance

Output `data/research/crypto/ptm_d1/d1_train_v1.json` with read log (file, sha256), contract sha
before and after the run, two runs byte-identical. Code `backend/app/crypto/research/ptm_d1/`.
Tests under `backend/tests/crypto/test_ptm_d1_*.py`.

## 16. Phase 3 amendment (before any performance was read)

- Census (phase2_census_v1.json) showed every draft family has raw onsets >= 227 and a 48 h
  non-overlapping upper bound >= 84. All five drafts are kept unchanged; no threshold was changed.
- Control definitions made precise while writing the simulator, before the study ran:
  (a) random entry draws a random tradable boundary and takes the completed-day flow direction at that
  boundary (a random direction would make X1 fire at the first boundary for half the draws, a trivial
  control); (b) the opposite-direction placebo mirrors the trade (same entry and exit bar, reversed
  direction P&L with its own fees and funding), because state exits are defined relative to the trade
  direction and a reversed trade would see its thesis already invalid at entry.
- A 5-rep smoke run of the study was executed to test the code path; its numbers were not used for
  any rule decision.
