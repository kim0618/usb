# CONTRACT POSITIONING DISCOVERY R0 V1 (TRAIN only)

Track: US-B crypto / BTC manual trading. New input family: Binance USD-M positioning (open interest and
long/short ratios). LOCAL ONLY. No server, no download, no API, no order, no Q123, no production change.
VALIDATION and HOLDOUT are not read (code guard). Every primary input and outcome is Binance.

## 0. Exploration history (declared)

TRAIN 2021-02-01 .. 2023-06-30 has been read by seven price/flow designs (PTM R1 V1, V2-A, V2-B, V3, R1 train,
R2, D1) and HOT-EDGE R0. Positioning data has never been used. This is therefore not an independent
discovery on TRAIN: only the positioning family is new, no price/flow condition may be added after results,
and VALIDATION / HOLDOUT stay sealed.

## 1. Sources (all Binance USD-M, BTCUSDT perpetual)

| input | file | schema | timestamp semantics |
|---|---|---|---|
| positioning | `data/runtime/crypto/d5_2/archive/binance/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-YYYY-MM-DD.zip` | create_time, symbol, sum_open_interest (BTC), sum_open_interest_value (USDT), count_toptrader_long_short_ratio (TA), sum_toptrader_long_short_ratio (TP), count_long_short_ratio (G), sum_taker_long_short_vol_ratio | 5 m label T; **treated as known at T + 5 min** (start/end semantics undocumented, so one full interval late) |
| price | `.../monthly/klines/BTCUSDT/1m` (PTM reader) | OHLC, quote volume, taker_buy_quote | 1 m bar known at its close |
| funding | `.../monthly/fundingRate/BTCUSDT` | calc_time, interval, rate | settlement time |
| taker flow (comparison only) | same klines | rolling 60 m lean (PTM `states.lean`) | known at minute close |

`sum_taker_long_short_vol_ratio` duplicates taker flow and is not used as new information (reported only).
Bybit files are forbidden: the readers resolve only the paths above.

## 2. Cleaning

Rows with the same create_time and identical payload: dedup. Same create_time with a different payload:
DATA_PROBLEM, excluded and counted. No interpolation. A blank or non-positive field makes that field
undefined for that row. Grid 5 m; missing slots and gaps are reported.
Guard: only daily files dated 2021-02-01 .. 2023-06-30 are opened; a row stamped at or after
2023-07-01T00:00Z raises.

## 3. Decision grid and states

Decision boundaries H = every UTC hour in TRAIN. At H the latest usable metrics row is stamped H - 5 min.

- O1..O4: OI change % over 5 m, 1 h, 4 h, 24 h on `sum_open_interest` (BTC units; chosen before data because
  coin units are not moved mechanically by price; `sum_open_interest_value` is reported descriptively only).
  O_k = OI(H-5m) / OI(H-5m-k) - 1. OI direction = sign(O2).
- Ratio states for G, TA, TP: LONG if > 1, SHORT if < 1, EVEN if = 1, NONE if undefined. No dead band.
- Price direction: sign of the 1 h return of Binance close (minute ending at H vs minute ending at H - 1 h).
- Flow direction: PTM rolling 60 m taker lean at the minute ending at H.
- Price/OI 4-state: A = price up + OI up, B = up + down, C = down + up, D = down + down.
- G/TA/TP 8-state; divergences D1 (G vs TP disagree), D2 (TA vs TP disagree), D3 (= the 4-state),
  D4 (flow sign vs OI sign: aligned / opposed).

## 4. Stage 0 (states only, no outcome)

Coverage, distributions (min, p1, p5, p25, median, p75, p95, p99, max, invalid), state frequencies on the 5 m
and hourly grids, run lengths, flips per day, transition matrices (4-state, 8-state), divergence counts,
overlaps, LONG/SHORT balance and candidate sample counts (raw onsets, 4 h dedup episodes, 48 h
overlap-adjusted N).

## 5. Candidate menu and selection rule (fixed now, before Stage 0)

Each candidate is an onset on the hourly grid: the condition holds at H and did not hold at H - 1 h
(per direction). Direction rule is part of the candidate.

| id | condition at H | direction | positioning information |
|---|---|---|---|
| P1 NEW_POSITIONS_WITH_MOVE | price/OI state A -> LONG; state C -> SHORT | with the 1 h move | positions opening behind the move |
| P2 TOP_VS_CROWD | G and TP disagree (one > 1, other < 1) | TP direction | large accounts' net position against the crowd's accounts |
| P3 FLOW_WITHOUT_OI | 60 m flow BUY and OI down -> SHORT; flow SELL and OI down -> LONG | against the flow | aggressive flow that closes positions instead of opening them |
| P4 TOP_ACCOUNT_VS_POSITION | TA and TP disagree | TP direction | few large positions vs many top accounts |

P3 precisely: flow BUY and OI down (sign(O2) < 0) -> SHORT; flow SELL and OI down -> LONG. (Closing flow is
treated as exhaustion; the opposite placebo tests the reverse reading.)

Selection: in menu order P1, P2, P3, P4, keep the first three whose 48 h overlap-adjusted N >= 100 (TOO_RARE
dropped). No other change is allowed: no threshold, no extra condition, no direction change. The selected
list is written to the Stage 0 output and copied into section 12 of this contract (CANDIDATE FREEZE) before
Stage 1 runs; Stage 1 refuses to run if they differ.

Viability labels per candidate (48 h N): < 100 TOO_RARE, 100..499 LIMITED, >= 500 RESEARCHABLE.

## 6. Stage 1 execution and costs

Entry at the open of the 1 m bar starting at H + 1 min (one minute after the value is known). Exit at the open
of the bar at entry + horizon. Horizons 15 m, 1 h, 4 h, 12 h, 24 h; **primary 1 h and 4 h**, others diagnostic.
No stops (this is an information test, not a method). Costs as PTM R1 (Binance): fee 5.0 bp/side, BASE
spread 1.184e-6 round trip, actual Binance funding settlements between entry and exit. Stress 5.5 bp and
2.537e-4 reported.

Samples: raw onsets; **primary = 4 h dedup episodes** (an onset of the same candidate and direction within
4 h of the last accepted one is skipped); 48 h overlap-adjusted sample (any direction, >= 48 h apart) as
sensitivity and for the N gate.

## 7. Statistics

Per candidate and horizon: mean and median net, gross, net > 0 share, PF, MFE, MAE, LONG and SHORT separately.
Inference: 7-day moving block bootstrap over UTC days (2,000 reps, seed 20261013) and 48 h bucket cluster
bootstrap. Holm over the 6 primary tests (3 candidates x 2 horizons) for the net mean and, separately, for
the incremental test.

## 8. Controls

- RANDOM_SAME_DIRECTION: 200 draws of the same N at random hourly boundaries with the event's direction.
- TIME_SHIFT: the same direction at H + 1 day and H + 3 days.
- OPPOSITE: the same entry and exit, reversed direction (paired).
- PRICE_ONLY_MATCHED: for each event, 20 hourly boundaries from the same UTC calendar month, same trade
  direction, same relation to the 1 h price move (with / against / flat), same tercile of |1 h return|
  (terciles over all TRAIN hours), where the candidate condition does not hold. Control value = their mean.
- FLOW_ONLY_MATCHED: the same with the 60 m flow sign instead of the price move (no tercile).
- Incremental information = event net minus matched-control net, paired, per event.

## 9. Validation gate (fixed now)

A (candidate, primary horizon) passes when all hold:
1. 48 h overlap-adjusted N >= 100;
2. mean net > 0;
3. PF > 1;
4. paired advantage over PRICE_ONLY_MATCHED > 0 and over FLOW_ONLY_MATCHED > 0;
5. Holm-adjusted block-bootstrap p <= 0.05 for the net mean, and Holm-adjusted p <= 0.05 for the larger of the
   two incremental p values;
6. LONG and SHORT both have mean net > 0 and N >= 30 each, and neither supplies more than 80 % of the summed net;
7. tail: share of events with MAE >= 3.84 % (the 10x safety distance) <= 2 %.

At most 2 candidates (best Holm p first) become VALIDATION candidates.

Verdict: **C. POSITIONING_TRAIN_CANDIDATE** if at least one passes. **B. POSITIONING_WEAK** if a candidate has
mean net > 0 at a primary horizon and both incremental advantages > 0 with unadjusted p <= 0.10, but fails
the gate. **A. POSITIONING_NO_EDGE** otherwise.

## 10. Tests and determinism

Tests listed in the brief section 23 under `backend/tests/crypto/test_positioning_r0.py`. Outputs
`data/research/crypto/positioning_r0/{stage0_v1.json, stage1_v1.json}`, each written twice byte-identical,
with the read log and the contract sha before and after.

## 11. Reporting

Report `docs/crypto/positioning_r0/POSITIONING_R0_REPORT.md`, sections A..W of the brief.

## 12. CANDIDATE FREEZE

(filled after Stage 0 by the rule of section 5; nothing else may be edited)

FROZEN: P1, P3

Applied by the section 5 rule to `stage0_v1.json` (no outcome read): P1 48 h N 424 LIMITED, P2 51 TOO_RARE,
P3 422 LIMITED, P4 46 TOO_RARE. Only two candidates qualify, so Holm in section 7 runs over 4 primary tests
(2 candidates x 2 horizons) instead of 6. Nothing else is changed.
Stage 0 facts recorded at freeze: TA and TP are undefined for most of 2022-01 .. 2022-12 in the archive
(92,153 of 253,440 slots); TP is > 1 in at least 99 % of defined slots (p1 1.009), so P2 and P4 are almost
long-only by construction.
