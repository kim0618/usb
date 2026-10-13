# PTM-D1 Phase 2: TRAIN market-state census (states only, no trade P&L)

Contract `CONTRACT_PTM_D1_V1.md` (sha before amendment d5a43d01…, after section 16 d373c14c…).
Output `data/research/crypto/ptm_d1/phase2_census_v1.json`. TRAIN 2021-02-01 .. 2023-06-30,
1,267,200 minutes, 0 missing. Defined share (all four flow states, V, z3, VR) 95.8 %, first defined
boundary = day 37.

## 1. State persistence (run lengths, hours)

| state | runs | median | mean | p10 | p90 | flips/day |
|---|---|---|---|---|---|---|
| D (completed day) | 381 | 48 | 55.4 | 24 | 96 | 0.43 |
| H4 (completed 4 h) | 2,444 | 8 | 8.6 | 4 | 16 | 2.78 |
| H1 (completed 1 h) | 10,784 | 1 | 2.0 | 1 | 4 | 12.3 |
| M (rolling 10 m) | 146,953 | 0.1 | 0.14 | 0.02 | 0.33 | 167 |

Same picture as R1 V3: the completed day is the only slow thesis; 4 h holds about 8 h; 1 h and 10 m are
noise-speed. Any rule that waits for a 1 h or 10 m "realignment" fires within the hour almost by default.

## 2. Time shares (while D is non-zero and everything is defined)

| quantity | share |
|---|---|
| H4 agrees with D / opposite | 56.1 % / 43.9 % |
| H1 agrees with D / opposite | 52.4 % / 47.6 % |
| price regime PR agrees with D / flat / conflicts | 23.9 % / 69.0 % / 7.2 % |
| volatility regime HIGH | 42.9 % |
| NO-TRADE (undefined, D even, PR conflict) | 7.2 % of defined time |
| deep pullback (pbz >= 1 V) in force | 15.2 % |
| H4 opposite onsets returning to D inside the episode | 1,072 / 1,320 = 81.2 % |

The 3-day price displacement is "flat" (within one daily range) 69 % of the time: price regime adds a
conflict veto, not a frequent confirmation.

## 3. Forward excursion to the end of the D episode (hourly samples, V units, signed by D)

20,231 hourly boundaries with D != 0. Displacement = (close at episode end / close now - 1) / V.

| group | n | disp mean | disp median | share > 0 | MFE med | MAE med | to end (h, med) |
|---|---|---|---|---|---|---|---|
| all | 20,231 | +0.31 | -0.09 | 0.42 | 0.45 | 0.45 | 31 |
| PR agrees | 4,793 | +0.44 | -0.08 | 0.44 | 0.64 | 0.46 | 35 |
| PR flat | 14,011 | +0.26 | -0.09 | 0.41 | 0.39 | 0.43 | 29 |
| PR conflict | 1,427 | +0.30 | -0.10 | 0.42 | 0.53 | 0.59 | 35 |
| VR HIGH | 8,688 | +0.24 | -0.11 | 0.39 | 0.41 | 0.43 | 30 |
| VR LOW | 11,543 | +0.36 | -0.06 | 0.44 | 0.49 | 0.46 | 32 |
| alignment 0 / 1 / 2 / 3 | 2,771 / 6,467 / 7,082 / 3,911 | +0.17 / +0.28 / +0.31 / +0.44 | -0.06 / -0.08 / -0.10 / -0.13 | 0.44 / 0.42 / 0.41 / 0.41 | 0.31 / 0.39 / 0.48 / 0.63 | 0.36 / 0.41 / 0.47 / 0.55 | 18 / 24 / 35 / 46 |
| H4 agrees / opposite | 11,348 / 8,883 | +0.39 / +0.20 | -0.11 / -0.07 | 0.41 / 0.42 | 0.56 / 0.33 | 0.51 / 0.38 | 41 / 19 |
| thesis age < 24 h / >= 24 h | 8,832 / 11,399 | +0.13 / +0.44 | -0.09 / -0.08 | 0.40 / 0.43 | 0.38 / 0.52 | 0.40 / 0.48 | 26 / 36 |
| deep pullback / shallow | 3,001 / 17,230 | +0.56 / +0.26 | **+0.03** / -0.11 | **0.52** / 0.40 | 0.67 / 0.43 | 0.48 / 0.44 | 32 / 31 |
| PR agree x VR HIGH | 2,044 | +0.60 | -0.05 | 0.46 | 0.58 | 0.40 | 35 |
| PR flat x VR HIGH | 6,112 | +0.11 | -0.13 | 0.36 | 0.36 | 0.42 | 27 |

Readings (states only, these are not trade results):

1. **Mean positive, median negative, everywhere.** Following the completed-day flow to the end of its
   episode loses more often than it wins (42 % positive), but the mean is positive: a few large moves pay
   for many small losses. That is the shape the user wants ("control when wrong, large when right"), and
   it is also why R1/R2 found means near zero after costs: the median trade is a small loss.
2. Part of the negative median is structural: the episode ends on a day whose flow was opposite, so the
   end point is measured after an adverse day. A thesis exit by construction gives back part of the move.
3. **Deep pullback inside the episode is the only state with a positive median and > 50 % positive share**
   (3,001 samples). It supports keeping E3 (deep pullback return).
4. Full alignment (3/3) has the largest MFE but also the largest MAE and the longest time to the episode
   end: it marks mature, volatile episodes rather than safer ones.
5. Thesis age >= 24 h roughly triples the mean displacement versus a thesis younger than a day
   (+0.44 vs +0.13 V). Half of all D runs last exactly one day (R1 V3), and those are where the flow
   direction fails. E4 (fresh turn) enters exactly at age 0; this census says E4 is the weakest
   starting point, but it is kept (contract allows drop or merge only; E4 is the user's "early turn" idea
   and is tested as drafted).
6. VR HIGH with a flat price regime is the poorest state (+0.11 V, 36 % positive): volatility without
   displacement. PR agree x VR HIGH is the best mean (+0.60 V). This is the split E5 uses.
7. PR conflict (7 %) is not clearly worse in mean, but has the deepest MAE (0.59 V) and is kept as
   NO-TRADE as pre-registered.

## 4. Entry draft onsets (states only)

| family | raw onsets | long / short | 48 h non-overlap upper bound |
|---|---|---|---|
| E1 TREND_ALIGN | 682 | 328 / 354 | 155 |
| E2 PULLBACK_REALIGN | 961 | 416 / 545 | 330 |
| E3 DEEP_PULLBACK_RETURN | 1,462 | 542 / 920 | 214 |
| E4 FRESH_TURN | 227 | 124 / 103 | 173 |
| E5 VOL_EXPANSION_ALIGN | 2,677 | 1,325 / 1,352 | 84 |

E5's 2,677 raw onsets are flicker of the 10 m state inside HIGH-volatility episodes (full alignment
re-forms many times a day); one position at a time collapses them to under 100.

## 5. Phase 3 decision

All five families kept as drafted. No threshold changed. Census-based expectations recorded before the
performance study: E3 strongest, E4 weakest, E5 small N.
