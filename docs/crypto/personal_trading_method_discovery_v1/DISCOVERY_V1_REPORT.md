# PTM-D1 Personal Trading Method Discovery V1 : TRAIN report

Contract `CONTRACT_PTM_D1_V1.md` sha **d373c14c…** (before run == after run). Census
`PHASE2_STATE_REPORT.md`. Output `data/research/crypto/ptm_d1/d1_train_v1.json` (two runs byte-identical,
sha e3318ef8…). Code `backend/app/crypto/research/ptm_d1/` (features, entries, sim, phase2, study).
Tests `tests/crypto/test_ptm_d1_sim.py` 17, PTM total 109 passed. TRAIN only; VALIDATION and HOLDOUT not read.
LOCAL ONLY, no orders, no server change, nothing committed.

Units: bp = 1/100 of a percent of entry price per trade, unlevered unless stated. Costs: fee 5 bp/side,
spread 1.18e-6, actual funding. Component study at the 10x catastrophic level (3.84 %), censor 120 h.

## A. User disposition, restated as what was tested

1. Loss is not an exit; an objective thesis change is. Tested: thesis = completed-day taker flow (X1) and
   four stricter or looser event exits (X2..X5), all state-based, none a loss percentage.
2. Control when wrong, large when right. Tested: PF, average winner / average loser, MFE capture, tail.
3. No time rules. Only a 120 h research censor (6 % of trades, 48 h sensitivity reported).
4. Keep big winners but protect against full giveback. Tested: four state-sequence protections (P1..P4)
   armed only after one daily range of open profit.
5. Accepts high leverage, not ruin. Tested: 5/10/20/50x (100x diagnostic) with liquidation geometry and a
   10 % margin-fraction account model.

## B. Data

Binance USD-M BTCUSDT 1 m klines (taker_buy_quote for flow), funding, mark and index klines, TRAIN
2021-02-01 .. 2023-06-30, 1,267,200 minutes, 0 missing, basis NaN 1.9 %. Parity with the REST copy
31,680 / 0. OI not used (Binance history absent), Wall not used (2026-10 only), Q123 not read.

## C. What R1/R2 contributed (hypothesis narrowing only)

Completed-day flow is the only slow state (median run 48 h); 1 h / 10 m states are noise-speed; after -0.5 %
most trades print the entry again but the median trade still ends negative; simple 1 h / 4 h reversal
exits cut winners; 20x safety ends about half the trades before the thesis acts. D1 therefore used the
completed-day flow as the thesis, added price displacement and volatility as regime axes, made every
exit an event, and let protection act only after one daily range of profit.

## D. Market-state structure (phase 2, states only)

See `PHASE2_STATE_REPORT.md`. Following the completed-day flow to the end of its episode is positive in
mean (+0.31 V) and negative in median (-0.09 V, 42 % positive): a few large moves pay for many small
losses. Deep pullback inside the episode is the only state with a positive median (+0.03 V, 52 %).
Thesis age >= 24 h triples the mean displacement. H4 opposite returns to the day direction 81 % of the time.

## E. Entry families (X1 exit, no protection, 10x safety, one position per family)

| family | N | raw | net mean | net median | PF | win | avg W | avg L | W/L | MFE capture | cat. | hold med | CI95 | p |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| E1 TREND_ALIGN | 144 | 682 | +38.3 | -67 | 1.26 | 0.41 | 456 | 252 | 1.81 | 0.10 | 26 % | 36 h | [-42, +132] | 0.38 |
| E2 PULLBACK_REALIGN | 380 | 961 | +3.0 | -77 | 1.02 | 0.36 | 396 | 221 | 1.79 | 0.03 | 23 % | 20 h | [-39, +53] | 0.90 |
| E3 DEEP_PULLBACK_RETURN | 198 | 1,462 | +38.8 | -24 | 1.39 | 0.43 | 320 | 177 | 1.81 | 0.13 | 18 % | 11 h | [-13, +99] | 0.18 |
| E4 FRESH_TURN | 227 | 227 | +6.6 | -79 | 1.05 | 0.36 | 417 | 225 | 1.85 | 0.04 | 24 % | 24 h | [-47, +68] | 0.82 |
| E5 VOL_EXPANSION_ALIGN | 80 | 2,677 | +34.3 | -160 | 1.19 | 0.35 | 613 | 277 | 2.21 | 0.08 | 34 % | 24 h | [-90, +178] | 0.62 |
| reference (union) | 999 | | +14.0 | | 1.10 | | 398 | 221 | 1.80 | 0.06 | 23 % | | [-28, +64] | |

No family's CI excludes zero. Direction split: E3 SHORT +61 (PF 1.57) vs LONG +12; E2 LONG negative.
The census expectation (E3 strongest, E4 weak) held; E1 was stronger than expected.

## F. Hold candidates (states at the loss anchor)

Population: 999 reference trades. 87 % reach -0.5 %, 72 % reach -1.0 %.

| anchor | n | recovery (entry printed later) | value of waiting vs stop at anchor: mean / median |
|---|---|---|---|
| -0.5 % | 869 | 78.3 % | +30 bp / -42 bp |
| -1.0 % | 715 | 59.3 % | +15 bp / -41 bp |

Decision-time states at the anchor do not separate outcomes: H4 opposite (B) recovery -2.5 pp (p 0.42),
all three horizons opposite (D) -0.3 pp (p 0.94), H4 agreeing +2.5 pp; the value of waiting differs by
less than 7 bp in every case (p > 0.8). At -1.0 % the same (B -6.3 pp p 0.13, D -7.6 pp p 0.14).
**There is no state visible at the anchor that says "this loss is worth waiting for".**

## G. Loss-exit candidates (paired against X1 on the 999 reference trades)

| exit | fires in | net mean | paired diff vs X1 | CI95 | p | avg winner change | exits changed |
|---|---|---|---|---|---|---|---|
| X1 FLOW_FLIP (reference) | | +14.0 | | | | | |
| X2 FLOW_FLIP_CONFIRMED (needs H4 opposite too) | | +19.7 | +5.7 | [-10, +22] | 0.49 | +18 % | 21 % |
| X3 + two opposite 4 h blocks, no 1 h realign | 58 | +14.7 | +0.7 | [-2, +3] | 0.62 | +1 % | 6 % |
| X4 + adverse expansion after failed attempt | 3 | +13.7 | -0.3 | [-1, 0] | 0.23 | 0 % | 0 % |
| X5 + price regime flip | 121 | +9.9 | -4.1 | [-13, +4] | 0.37 | -1 % | 12 % |

None passes the selection rule (p <= 0.10 with positive mean). X4 almost never fires before X1 or the
safety level acts (3 of 999): a 0.5 V attempt followed by a 0.5 V expansion is rarer than a day flip.
X2 (hold through a flip unless 4 h agrees) raises the average winner 18 % and the catastrophic count
(233 -> 275); net +5.7 bp, noise. Chosen loss exit: **X1**.

## H. Recovery structure

What separates recovered from unrecovered losses is only what happens next, not what is visible at the
anchor: if H4 realigns to the thesis after the anchor, recovery is 90 % (-0.5 %) / 81 % (-1.0 %) and
waiting is worth +79 / +105 bp (p 0.02 / 0.01); if H4 is opposite at the anchor and never realigns before
the exit, recovery is 54 % / 25 % and waiting costs -118 / -123 bp (p < 0.01). Both groups are defined
by a later event, so they describe the mechanism (recoveries are 4 h realignments) and are not rules.
Descriptive: recovered losses sit more often in HIGH volatility (median VR 1 vs 0), with a slightly
shallower adverse depth (0.13 vs 0.15 V) and slightly younger thesis (17 vs 20 h); funding, basis,
pullback depth and attempt count do not differ.

## I. Profit-hold candidates

Protection arms only after MFE >= 1 V; 26 % of reference trades ever arm. Below that threshold the trade
is held by construction, which is the only "profit hold" rule tested. Under X1 the median giveback ratio is
1.13 (E3) to 1.64 (E5): the typical trade gives back all of its best profit and closes negative. MFE
capture (mean gross / mean MFE) is 0.06 to 0.13 everywhere.

## J. Profit-protection candidates (paired against P0 on the reference trades, X1 exit)

| protection | fired | net mean | paired diff vs P0 | CI95 | p | MFE capture change | giveback mean |
|---|---|---|---|---|---|---|---|
| P0 none | | +14.0 | | | | | 325 bp |
| P1 1 h opposite then 4 h opposite, no new MFE | 25 % | +8.6 | -5.4 | [-35, +18] | 0.68 | 0.00 | 290 |
| P2 two 4 h blocks without new MFE, 4 h opposite | 23 % | +11.0 | -3.0 | [-27, +16] | 0.78 | 0.00 | 307 |
| P3 retrace 1 V from MFE | 19 % | -0.3 | -14.3 | [-40, +7] | 0.25 | -0.03 | 321 |
| P4 4 h opposite, 1 h realigns then fails | 24 % | +2.8 | -11.2 | [-40, +12] | 0.39 | -0.02 | 295 |

Every protection lowers the mean; none raises MFE capture. Same finding as R2: whatever cuts giveback
cuts the few large winners that make the mean. Chosen protection: **P0 (none)**.

## K. Leverage / position sizing (final methods, isolated, bracket 1, safety = 0.4 x liquidation distance)

| leverage | safety dist. | safety-exit rate M1 / M2 / M3 | liquidation if no safety | account B (10 % margin) mean / max DD, M1 | M2 | M3 |
|---|---|---|---|---|---|---|
| 5x | 7.84 % | 3 % / 8 % / 9 % | 0 / 1 / 0 % | +29 bp / -13 % | +22 / -16 % | +35 / -22 % |
| 10x | 3.84 % | 18 / 26 / 34 % | 2 / 3 / 6 % | +39 / -21 % | +38 / -31 % | +34 / -34 % |
| 20x | 1.84 % | 37 / 56 / 64 % | 12 / 24 / 28 % | +48 / -36 % | +118 / -36 % | +131 / -41 % |
| 50x | 0.64 % | 71 / 83 / 93 % | 43 / 59 / 71 % | -77 / -160 % | +171 / -136 % | -99 / -184 % |
| 100x (diagnostic) | 0.24 % | 90 / 97 / 97 % | 71 / 85 / 93 % | -103 / -255 % | -90 / -245 % | +30 / -214 % |

Only 5x keeps the safety + liquidation layer under the 10 % gate. At 20x more than half the trades are
closed by the safety level, not by the thesis; the apparent W/L of 2.2 to 4.2 there is the cap on losers,
and the account drawdown is 36 to 41 % of equity. Quality tiers: none adopted. Full alignment at entry
(+48 bp, p 0.14, same sign both directions) and price-regime agreement (+31 bp, p 0.44) miss the 0.10
threshold; HIGH volatility (-72 bp, p 0.06) has opposite signs for LONG (+6) and SHORT (-147), so it is
not a tier. **Uniform 5x, no tiers.**

## L. NO-TRADE

Undefined states, D even (0 % of defined time), price regime conflicting with the day flow (7.2 %). The
census could not justify adding more; VR HIGH with a flat price regime is the poorest state (+0.11 V) but
was not pre-registered as a veto and is not adopted.

## M. Candidate method 1 (frozen) : E3 DEEP_PULLBACK_RETURN

시장: 완료된 UTC 하루의 taker flow 방향(D)이 "큰 방향". 3일 가격 변위가 D와 반대 방향으로 하루 범위(1 V) 이상이면 매매 금지.
LONG: D=BUY인 에피소드 안에서, 에피소드 고점 대비 종가가 1 V 이상 눌린 적이 있은 뒤, 완료된 1시간 flow가 BUY로 돌아선 첫 봉의 다음 분 시가.
SHORT: 반대.
손실 중 HOLD: 완료된 하루 flow가 아직 D 방향이면 손익과 무관하게 보유.
손실 청산: 완료된 하루 flow가 반대로 바뀐 직후 시가(X1). 그 외 계좌 보호선(5x: -7.84 %).
수익 중 HOLD: 하루 flow가 유지되면 보유. 수익 보호 규칙 없음(모든 후보가 평균을 깎음).
레버리지: 5x 균일, 증거금 10 %. 티어 없음.
TRAIN: N 198, 평균 +38.8 bp, 중앙 -24 bp, PF 1.39, 승률 43 %, 평균 승 320 / 평균 패 177, 보유 중앙 11 h, 5x 안전선 청산 3 %, 계좌(B) 거래당 +29 bp·최대 DD -13 %.
Why it exists: the deep pullback was the only state with a positive median forward displacement in the census,
and a 1 h turn is the earliest cheap confirmation inside the day.

## N. Candidate method 2 (frozen) : E1 TREND_ALIGN

시장: 큰 방향 D와 3일 가격 변위(1 V 이상)가 같은 방향, 완료된 4시간 flow도 같은 방향.
LONG: 위 상태에서 완료된 1시간 flow가 BUY로 돌아선 첫 봉의 다음 분 시가. SHORT: 반대.
HOLD / 손실 청산 / 수익 보호 / 레버리지: 방법 1과 동일(X1, 보호 없음, 5x 균일).
TRAIN: N 144, 평균 +38.3 bp, 중앙 -67 bp, PF 1.26, 승률 41 %, 평균 승 456 / 평균 패 252, 보유 중앙 36 h, 5x 안전선 청산 8 %, 계좌(B) +22 bp·최대 DD -16 %.
Why it exists: price and flow agreeing is the one regime where the census mean was largest (+0.44 V, +0.60 V with HIGH volatility).

## O. Candidate method 3 (not frozen) : E5 VOL_EXPANSION_ALIGN

Same regime as method 2 plus HIGH volatility and full 4 h / 1 h / 10 m alignment onset. N 80, +34.3 bp,
PF 1.19, median -160 bp, 34 % safety exits at 10x. Fails N >= 100 and PF >= 1.25; third by net mean, not frozen.

## P. TRAIN results of the three methods (X1, no protection, 10x component level)

| | M1 E3 | M2 E1 | M3 E5 |
|---|---|---|---|
| N / clusters (D episodes) | 198 / 163 | 144 / 127 | 80 / 69 |
| net mean / median | +38.8 / -24 | +38.3 / -67 | +34.3 / -160 |
| CI95 (7-day block) / p / cluster p | [-13, +99] / 0.18 / 0.19 | [-42, +132] / 0.38 / 0.35 | [-90, +178] / 0.62 / 0.60 |
| Holm p (3 methods) | 0.53 | 0.76 | 0.76 |
| PF / win rate | 1.39 / 0.43 | 1.26 / 0.41 | 1.19 / 0.35 |
| avg winner / avg loser / ratio | 320 / 177 / 1.81 | 456 / 252 / 1.81 | 613 / 277 / 2.21 |
| MFE mean / MAE mean / capture | 338 / 174 / 0.13 | 492 / 219 / 0.10 | 534 / 247 / 0.08 |
| giveback mean / median ratio | 292 / 1.13 | 444 / 1.28 | 489 / 1.64 |
| tail p5 / min | -394 / -403 | -396 / -415 | -396 / -398 |
| exits: thesis / catastrophic / censor | 150 / 36 / 12 | 99 / 37 / 8 | 49 / 27 / 4 |
| hold median / mean | 11 / 28 h | 36 / 42 h | 24 / 38 h |
| stress costs (5.5 bp, 2.5e-4) net / PF | +35.2 / 1.35 | +34.7 / 1.23 | +30.7 / 1.17 |
| 48 h censor net / PF / censored | +31.3 / 1.31 / 26 % | +48.6 / 1.36 / 35 % | +26.1 / 1.16 / 29 % |

## Q. Controls

| control | M1 E3 | M2 E1 | M3 E5 |
|---|---|---|---|
| random tradable boundary, direction = day flow, same exits (200 draws): mean of means / p5..p95 / share >= observed | +33.8 / -22..+92 / **0.45** | +34.6 / -34..+98 / **0.47** | +31.7 / -56..+118 / **0.50** |
| time shift +1 d / +3 d / +7 d | +2 / +4 / +1 | +22 / +27 / +53 | +99 / -10 / +24 |
| opposite placebo (mirror) net / paired diff / p | -59 / +98 / 0.09 | -58 / +97 / 0.27 | -54 / +89 / 0.52 |
| directional baseline (censor + safety only) net / paired method minus baseline / p | +60 / -21 / 0.44 | +27 / +11 / 0.68 | +39 / -4 / 0.92 |
| X1 + fixed stop 0.5 % net / paired method minus fixed / p | -11 / +50 / 0.06 | +21 / +17 / 0.63 | -11 / +45 / 0.46 |
| X1 + fixed stop 1.0 % net / paired / p | -10 / +49 / 0.03 | +50 / -12 / 0.66 | +17 / +17 / 0.74 |

**The decisive control is the first row.** Entering at a random moment in the direction of the completed-day
flow, with the same exits, earns the same +32 to +35 bp as the three entry families; the observed means
sit at the 45th to 50th percentile of the random draws. The entry states add no information. What is
positive in TRAIN is the structure "trade with the completed day's flow, hold until it flips, 10x safety,
120 h censor", and even that is not separable from zero (block CI of the reference population
[-28, +64]). The fixed-stop baselines are worse for E3 (p 0.03 to 0.06, the one control it beats), which
restates R1: cutting at -0.5 / -1 % before the thesis acts loses.

## R. Tail risk

At 10x the worst trade is the safety level plus costs (-4.0 to -4.2 %); at 5x -7.9 to -8.0 % of notional,
which with 10 % margin is -4 % of equity per trade and a 13 to 22 % account drawdown over TRAIN. At 20x the
safety layer becomes the main exit (37 to 64 %); at 50x the account loses money in two of three methods and
draws down more than its equity in the no-compounding model (ruin). Liquidation never occurs with the safety
layer on; with it off, 12 to 28 % of 20x trades and 43 to 71 % of 50x trades would be liquidated.

## S. Overfitting audit

- Performance comparisons made: 5 entries + 4 loss exits + 4 protections + 3 quality splits + 3 methods = 19
  as planned, plus 6 controls per method. No threshold was searched; V unit, 1 V, 0.5 V, 7 / 30 / 3 days were
  fixed in the contract before the census.
- The entry drafts were written before the census; the census only confirmed keeping all five.
- Lineage of this track: R1 V1, V2-A, V2-B, V3, R1 train, R2, D1 = seven designs on the same TRAIN window.
  The TRAIN window has now been read many times; any VALIDATION result must be judged with that in mind.
- The B gate ("above the random mean") is met by 4 to 5 bp with a random-draw p of 0.45 to 0.50. The gate
  was pre-registered but is too weak to mean "the entry carries information"; see W.
- The recovery flag (any dip below entry followed by printing entry) is 99 to 100 % at 1 m resolution and
  carries no information; the anchor-based study (F, H) is the meaningful one.
- The opposite placebo is a mirror (same exit bar), so its paired difference is about twice the gross
  mean; it tests direction, not exit mechanics.

## T. Candidates passed to VALIDATION (frozen, by the contract rule)

METHOD_1 (E3 DEEP_PULLBACK_RETURN) and METHOD_2 (E1 TREND_ALIGN), both with X1, no protection, uniform 5x.
Rules are exactly those in sections M and N; nothing may change before VALIDATION. **See W before running it.**

## U. Fit with the user's disposition

Matches: no time rule, exit only on an objective thesis change, no loss-percentage stop, winners 1.8x the
losers, high leverage examined rather than assumed bad. Does not match: the median trade is a loss and the
typical trade gives back more than its whole MFE; there is no state-based HOLD rule beyond "the day flow is
still on your side", and no profit protection that preserves the mean. The user's "hold while the thesis
lives" is exactly X1, and X1 alone is what the data supports; nothing finer than the completed day was
found to carry information.

## V. Limitations

TRAIN only (2021-02 .. 2023-06, 29 months, one bull-bear cycle). Thesis is taker-flow of the completed
UTC day, not the user's own judgement. Isolated margin, bracket 1, no liquidation fee, last-trade prices.
No compounding, one position at a time. Funding applied at actual settlements; slippage beyond BASE spread
not modelled. Random control shares the TRAIN period with the methods (its spread describes sampling
noise, not a test against zero). Protection and loss-exit candidates were evaluated on the pooled
reference population, not per family.

## W. Final verdict

**B. WEAK_METHOD_CANDIDATE** (contract gates) for METHOD_1 and METHOD_2, with this qualification, which
the user should weigh before spending VALIDATION on them:

1. The entry families are indistinguishable from random timing within the same direction regime
   (random-draw percentile 45 to 50). The positive TRAIN mean belongs to the structure "follow the completed
   day's flow until it flips", not to the entry.
2. That structure's own mean is not separable from zero (reference CI [-28, +64] bp).
3. No state-based hold, loss-exit or protection rule improved on X1 alone; the recovered / unrecovered split
   is explained only by later 4 h realignment, which is not knowable at decision time.
4. Leverage above 5x hands the exit to the safety layer; 5x is the only non-ruinous setting and even there the
   account drawdown is 13 to 22 % over TRAIN.

Not run: VALIDATION, HOLDOUT. Not changed: production, Q123, any deployed file. Nothing committed.
