# PTM-R2 Contract V1: Recovery, Profit Protection, Leverage Feasibility (TRAIN only)

Status: FROZEN before any R2 computation (2026-10-13). Any change after the TRAIN results is a new
version and voids this one.

Scope: LOCAL only. TRAIN 2021-02-01 00:00 .. 2023-06-30 23:59 UTC. VALIDATION (2023-07-01 ..
2025-03-31) and HOLDOUT (2025-04-01 ..) are not read; the readers refuse them and the run fails.
No server work, no download, no API call. Q123, Collector, Manual Recorder, crypto-paper untouched.

## 0. Relation to R1 (multiple-testing record)

R1 (V1, V2-A, V2-B, V3 structures; V3 TRAIN study sha 310290c2...) is closed: F1 TRAIN_FAIL, F2/F3
TRAIN_WEAK, 0 validation candidates. R1 families and thresholds are not modified or reused as
entries. **R2's questions were formed after seeing R1 TRAIN outcomes** (large giveback; 20x
safety ending about half of the holds; frequent recovery after -0.5 % / -1 %). R2 TRAIN results are
therefore discovery only and need strict VALIDATION before any use.

## 1. Data (existing local files only)

- Binance USD-M BTCUSDT perp 1m klines (volumes for flow, OHLC last prices), funding rate, mark and
  index price 1m klines: D5.2 data.vision archive, TRAIN months 2021-02 .. 2023-06 only.
- States: V3 arrays (completed UTC day D, completed 4 h H4, completed 1 h H1, rolling 10 m M), lean
  without dead band.
- Basis b(t) = mark close / index close - 1 at boundary t (NA where either is missing).
- OI, wall and Q123 are excluded (no long Binance OI; no wall history; Q123 separate).

## 2. Entry population (state sampling, no trigger)

- Every 4 h grid boundary b with (b + 1) mod 240 = 0 (closes of 03:59, 07:59, ..., 23:59 UTC).
- Direction dir = D(b) (the 1D thesis); no entry when D(b) is EVEN or NONE.
- Entry e = b + 1, price P0 = open[e]. Included only if e + 2880 <= last TRAIN minute.
- **raw** = all grid entries (primary sample, inference clustered).
- **dedup** = only the 12:00 UTC entries (one per day).
- **48 h overlap-adjusted** = 12:00 UTC entries on even TRAIN day index (non-overlapping 48 h).
- Base exit (P0): open of the minute after I1 (first boundary > b with D = -dir), or e + 2880.

## 3. Recovery cohorts (Q1)

Anchors (only these): R0 = 0.5 %, R1 = 1.0 %, R2 = 1.5 % adverse underlying move.
- k_a = first bar in [e, x_P0) whose low (LONG) / high (SHORT) reaches P0 (1 -+ a). RECOVERY_ANCHOR.
- Recovered = a later bar k > k_a, before the P0 exit, whose high (LONG) / low (SHORT) reaches P0.
  RECOVERY_COMPLETE = that bar k_r. (Adverse first: the anchor bar itself cannot recover.)
- State at the anchor = values at boundary k_a - 1 (known before the anchor touch):
  D (= dir while alive), H4, H1, M relative to dir, alignment count of {H4, H1, M} equal to dir,
  last settled funding signed by dir (dir x rate > 0 = position pays), basis signed by dir,
  position direction, adverse duration (minutes since the last bar whose favourable extreme reached
  P0, or since entry), time since entry, thesis age (minutes since D took its current value),
  W1 / W2 / W3.
- Outcomes: recovered yes/no, time to recovery (k_r - k_a), further adverse excursion from k_a to
  k_r (or to the end), ended by I1 without recovery, and per leverage whether the safety or
  liquidation level was touched before recovery.
- **Primary endpoint**: P(recover before I1 or MAX HOLD), leverage-free.

Pre-registered splits (group 1 vs group 0 recovery rate), 9 per anchor:
S1 W1 (H4 opposite); S2 H1 opposite; S3 M opposite; S4 alignment count >= 2; S5 funding paying
(dir x rate > 0); S6 basis favourable (dir x basis > 0; NA excluded); S7 thesis age >= 24 h;
S8 time since entry <= 4 h; S9 direction LONG.

## 4. Profit protection cohorts (Q2)

Recovered trades of each anchor. From the close of the recovery bar k_r (boundaries j >= k_r):
- **P0** hold to I1 or MAX HOLD (base).
- **P1** first j with H1 = -dir: exit at open[j + 1].
- **P2** first j with H1 = -dir and H4 = -dir.
- **P3** H4 opposite at the recovery boundary or at a 4 h completion, and still opposite at the next
  4 h completion: exit at the open after that second completion.
- **P4** 1D thesis invalidation. Under V3, I1 *is* the 1D invalidation, so P4 is identical to P0;
  it is reported once as `P4 = P0` and is not tested separately.
- Any protection exit later than the P0 exit becomes the P0 exit.
- 10 m opposite is diagnostic only (hypothetical exit at its first occurrence), as are W1 and W3.

Per policy: MFE_after_recovery = max favourable return over bars k_r .. exit-1 and the exit price;
exit_return = gross at exit; giveback_bp = MFE_after - exit_return; giveback_ratio = giveback /
MFE_after when MFE_after > 0, else NA. Net preservation = net exit return.

## 5. Execution and cost (as R1 V3 study)

Time exits fill at the exit minute open; levels fill at the level or the gap open, adverse first in a
bar. Fee 5.0 bp/side on entry and exit notional; BASE spread 1.184e-6 round trip; funding = actual
Binance settlements with open_ms(e) <= s <= open_ms(exit bar), sign by dir. Q1 and Q2 primary are
leverage-free (no stop of any kind).

## 6. Leverage (Q3)

Leverages 5, 10, 20, 50 (primary); 100 diagnostic only (never a candidate or profile).
Isolated model, Binance BTCUSDT bracket 1 (MMR 0.004, notional within 300,000 USDT, no cum):
- liquidation distance liq(L) = 1/L - 0.004: 19.6 %, 9.6 %, 4.6 %, 1.6 % (100x: 0.6 %);
- safety buffer (R1 rule) s(L) = 0.40 x liq(L): 7.84 %, 3.84 %, 1.84 %, 0.64 % (100x: 0.24 %).
Modes: **SAFETY_ON** (safety stop, liquidation only through a gap beyond liq) and **LIQ_ONLY**
(no safety buffer, actual liquidation geometry). Prices are last-trade 1m high/low (more adverse
than mark; conservative). Liquidation = the whole isolated margin is lost plus the entry fee and
funding paid to that point; no liquidation clearance fee is modelled. The real account is CROSS
margin, where liquidation depends on wallet equity and is later than this isolated model.
Exit kinds counted per leverage and mode: LIQUIDATION, SAFETY, I1 (thesis), MAX_HOLD, and for
protection policies PROTECTION.

Q3 primary method = P0 on the raw population. Protection policies at leverage are secondary.

## 7. Account scenarios (trade level, no compounding)

- **A FIXED_NOTIONAL**: notional = 1.0 x account equity at every leverage; margin = 1/L.
- **B FIXED_MARGIN**: margin = 10 % of account equity (only value); notional = 0.10 x L.
Per trade: margin, notional, leverage, underlying net return, account PnL % = notional x net
(liquidation: -margin - notional x (fee_side + funding paid)), fee, funding, liquidation proximity
(MAE / liq distance), max adverse account PnL = -notional x MAE (floored at -margin).
Utility shown together (no single optimum): expected account return, 5th percentile trade return,
safety+liquidation exit rate, recovery survival (R0 cohort recovering before any safety/liquidation
exit), max single-trade account loss, gain per unit tail risk = mean / |p5|.

## 8. Inference and multiple testing

Primary: 7-day moving-block bootstrap on entry UTC days, 2,000 resamples, seed 20261013 (ratio of
sums; group differences resample the same days jointly). Secondary: cluster bootstrap by thesis
episode. Two-sided p from z = estimate / SE.
Holm-Bonferroni at family-wise 0.05 **within each question family**:
Q1 27 tests (9 splits x 3 anchors, recovery-rate difference); Q2 9 tests (P1..P3 x 3 anchors,
paired net difference vs P0); Q3 4 tests (Scenario B, SAFETY_ON, P0 mean account PnL at
5/10/20/50x).

## 9. Validation-candidate gates (fixed now)

Recovery filter (split x anchor): both groups N >= 300 trades and >= 30 thesis episodes;
|difference| >= 10 percentage points; Holm-adjusted p < 0.05; same sign of the difference within
LONG and within SHORT; thesis-episode cluster p < 0.05. At most 2 (smallest Holm p).

Profit protection (policy x anchor): paired net difference vs P0 > 0 with Holm-adjusted p < 0.05;
mean giveback lower than P0; 20x SAFETY_ON safety+liquidation rate not higher than P0's on the same
trades; same sign of the net difference within LONG and SHORT. At most 2.

Leverage: no single "best leverage". Each primary leverage is labelled by the SAFETY_ON P0
safety+liquidation exit rate r: SAFE r <= 10 %, BALANCED 10 % < r <= 30 %, AGGRESSIVE
30 % < r <= 50 %, INFEASIBLE r > 50 %. At most one leverage per profile (the highest leverage that
fits it). A leverage profile becomes a validation candidate only together with a passing method
(base P0 with Q3 Holm p < 0.05 and positive mean in Scenario B, or a passing protection candidate);
otherwise profiles are descriptive only.
