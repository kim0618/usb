# PTM-R1 V3 Historical Study: TRAIN Report

Run 2026-10-13 KST. Output `data/research/crypto/ptm_r1/r1_train_v1.json` (+ per-trade
`r1_train_v1_trades.jsonl`). Code `backend/app/crypto/research/ptm_r1/r1_train.py` (+ TRAIN-guarded
price/funding readers in `data.py`). Tests `backend/tests/crypto/test_ptm_r1_train_study.py` (17);
all PTM tests 77 passed. Full run 6.4 s, executed twice: JSON and trade file byte-identical.

VALIDATION and HOLDOUT not read; Q123 not read; nothing operational touched; not committed.
Units: bp of entry notional (underlying move), not of margin. L20 = primary 20x.

**Verdict: no validation candidate.** F1 A TRAIN_FAIL, F2 B TRAIN_WEAK, F3 B TRAIN_WEAK.

## A. Contract sha

- Study contract `CONTRACT_PTM_R1_V3_TRAIN_STUDY.md` sha256
  `310290c2380212a4193ed7144fc1939e118fd518208359fe8da8720512985a53` (frozen before any outcome;
  re-hashed after the run).
- V3 rules `CONTRACT_PTM_R1_V3.md` `c34cc801...` (RULE FREEZE, unchanged).

## B. Frozen V3 rules

Thesis = last completed UTC day taker-flow lean; entry states completed 4 h / 1 h blocks and
rolling 10 m; F1/F2/F3 by the V3 gap-segment rule; primary dedup; I1 = next completed day opposite;
MAX HOLD 48 h; W1..W3 diagnostic only. Entry at open[t+1]; time exits at the exit minute open;
stops fill at level or gap open, adverse first inside a bar.

## C. TRAIN coverage

1,267,200 price/volume minutes (missing 0); 2,640 funding settlements (880 days x 3); parity
31,680 / 0. Files read: 29 monthly klines (volumes and prices), 29 monthly funding, 22 + 1 parity;
roles `train`, `train_funding`, `parity` only. 2 primary setups excluded at the TRAIN tail.
The 14-day aggTrades ordering diagnostic was **NOT_RUN** (those files lie in the HOLDOUT window).
TRAIN ordering ambiguity (stop bar that also printed a new favourable extreme): 0 to 6 trades per
family and policy.

## D. Entry counts

| family | raw | primary | evaluated | LONG | SHORT | 48 h overlap-adjusted |
|---|---|---|---|---|---|---|
| F1 | 21,609 | 551 | 550 | 248 | 302 | 403 |
| F2 | 2,059 | 478 | 477 | 210 | 267 | 366 |
| F3 | 924 | 415 | 415 | 181 | 234 | 317 |

## E/F/G. Family results (L20, primary cost, LONG+SHORT pooled)

| family | policy | N | gross mean / median | net mean / median | net > 0 | PF | hold mean / median | catastrophic |
|---|---|---|---|---|---|---|---|---|
| F1 | A1 | 550 | -7.8 / -50.0 | -17.8 / -60.0 | 7.6 % | 0.68 | 8.4 / 1.9 h | 0 |
| F1 | A2 | 550 | 13.4 / -100.0 | 3.8 / -110.0 | 20.4 % | 1.04 | 18.2 / 10.0 h | 0 |
| F1 | B (= A3) | 550 | 4.2 / -184.0 | -5.1 / -192.4 | 31.1 % | 0.96 | 27.3 / 24.6 h | 63.6 % |
| F1 | **C** | 550 | 6.2 / -184.0 | **-3.1** / -190.1 | 30.4 % | **0.97** | 24.0 / 19.9 h | **55.6 %** |
| F2 | A1 | 477 | 3.3 / -50.0 | -6.3 / -60.0 | 9.4 % | 0.88 | 9.7 / 2.7 h | 0 |
| F2 | A2 | 477 | 15.4 / -100.0 | 6.1 / -110.0 | 21.0 % | 1.07 | 18.7 / 9.9 h | 0 |
| F2 | B (= A3) | 477 | 23.5 / -184.0 | 14.7 / -191.6 | 33.3 % | 1.12 | 28.4 / 30.8 h | 59.5 % |
| F2 | **C** | 477 | 20.6 / -184.0 | **11.9** / -171.5 | 32.3 % | **1.11** | 23.9 / 19.5 h | **50.9 %** |
| F3 | A1 | 415 | 21.6 / -50.0 | 12.1 / -60.0 | 10.8 % | 1.23 | 9.8 / 2.1 h | 0 |
| F3 | A2 | 415 | 21.7 / -100.0 | 12.3 / -110.0 | 19.3 % | 1.14 | 16.6 / 6.4 h | 0 |
| F3 | B (= A3) | 415 | 26.9 / -184.0 | 17.8 / -192.3 | 32.5 % | 1.15 | 27.1 / 25.8 h | 61.0 % |
| F3 | **C** | 415 | 24.8 / -88.4 | **15.7** / -99.3 | 32.3 % | **1.15** | 20.1 / 10.9 h | **46.7 %** |

C exit reasons: F1 catastrophic 306 / I1 126 / max hold 118; F2 243 / 128 / 106; F3 194 / 133 / 88.

By direction (C net mean / PF): F1 LONG -0.7 / 0.99, SHORT -5.1 / 0.96; F2 LONG 13.4 / 1.12,
SHORT 10.7 / 1.10; F3 LONG 27.4 / 1.27, SHORT 6.7 / 1.06. No sign flip hidden by pooling in F2/F3;
F1 is negative in both directions.

## H. Fixed-stop comparison (paired, same entries)

C minus A1 net mean: F1 +14.7 (CI -6.1..39.2), F2 +18.2 (-8.8..44.1), F3 +3.6 (-18.8..29.3).
C minus A2: F1 -6.9 (-21.4..9.1), F2 +5.8 (-14.4..24.7), F3 +3.4 (-13.7..23.2).
Medians of the paired differences are strongly negative (C - A1: -132 / -114 / -49; C - A2:
-83 / -82 / -76): on a typical trade the fixed stop loses less; the thesis hold's mean advantage
comes from a minority of large recoveries (p90 +198 to +314 vs A1).
A3 (2.0 %) is non-operative at 20x (identical to B). At 10x it is operative: C - A3 is not
reported as a primary test.

## I. Max-hold comparison

B equals A3 at 20x. C minus B net mean: F1 +2.0, F2 -2.9, F3 -2.1 (median 0): exiting at I1 instead
of holding to 48 h changes little on average, because the catastrophic layer ends about 60 % of B
trades first.

## J. Thesis-invalidation comparison (policy C, primary endpoints)

| family | P1 C net mean (block bootstrap SE, 95 % CI) | raw p | Holm p | thesis-episode cluster p |
|---|---|---|---|---|
| F1 | -3.1 (13.9; -29.8 .. 24.7) | 0.82 | 1.0 | 0.83 |
| F2 | 11.9 (16.0; -19.0 .. 42.9) | 0.46 | 1.0 | 0.43 |
| F3 | 15.7 (18.7; -20.3 .. 52.2) | 0.40 | 1.0 | 0.37 |

All nine Holm-adjusted p = 1.0 (P1, P2a, P2b x 3 families).

## K. Loss → recovery (policy C path, before the C exit)

| family | reached -0.5 %: recovered to entry / net break-even | reached -1.0 %: entry / net BE | any dip: entry |
|---|---|---|---|
| F1 | 499: 72.9 % / 67.5 % | 398: 43.5 % / 40.2 % | 98.2 % |
| F2 | 411: 74.0 % / 69.6 % | 331: 48.6 % / 44.7 % | 98.7 % |
| F3 | 340: 63.2 % / 57.6 % | 290: 41.0 % / 38.6 % | 95.7 % |

## L. False fixed-stop exits (stopped, then the C path recovered before its exit)

| family | A1 stops: later entry / net BE | A2 stops: later entry / net BE |
|---|---|---|
| F1 | 508: 71.7 % / 66.3 % | 429: 40.3 % / 37.3 % |
| F2 | 431: 70.5 % / 66.4 % | 369: 43.6 % / 40.1 % |
| F3 | 370: 58.1 % / 53.0 % | 332: 35.8 % / 33.7 % |

Recovery to entry does not mean the trade then ended positive (section N).

## M. MFE / MAE (policy C, mean / median, bp)

F1 MFE 295 / 152, MAE 144 / 184; F2 297 / 157, 137 / 184; F3 281 / 120, 133 / 171.
(MAE median = 184 is the catastrophic level: more than half of C trades end at it.)

## N. Giveback (policy C)

MFE minus exit, mean: F1 289 bp, F2 277 bp, F3 256 bp. Median giveback as a share of MFE: 1.85,
1.67, 1.75, i.e. the median trade gave back all of its best open profit and ended below entry.
INVALIDATION_LOSS: of I1 exits (126 / 128 / 133), gross < 0 in 54 / 50 / 52 %, net < 0 in
55 / 55 / 56 %; those losers average -64 / -60 / -64 bp net. All I1 exits average +35 / +42 / +37 bp
(median -17 / -11 / -11).

## O. Funding and cost (policy C, mean bp)

Funding -0.8 / -1.3 / -1.0 (negative = received on average; SHORT entries are the majority and
TRAIN funding was mostly positive). Total cost 9.3 / 8.7 / 9.0. Stress cost (5.5 bp/side + STRESS):
C net F1 -6.6, F2 +8.3, F3 +12.2.

## P. Catastrophic stops

At 20x (1.84 %) the safety layer ends **47 to 56 %** of C trades and 60 to 64 % of B trades within
48 h. At 10x (3.84 %): C 20 to 24 %, with C net mean F1 +0.6, F2 +6.4, F3 +2.6. The 20x safety layer,
not the thesis, is the dominant exit for a 12 to 48 h hold.

## Q. Controls (policy C)

| family | C net | random timestamp net (C minus control, 95 % CI) | time shift 7 / 14 / 30 d | direction placebo |
|---|---|---|---|---|
| F1 | -3.1 | +16.6 (-19.7; -53.6 .. 11.3) | -23.1 / -2.8 / -4.4 | -23.6 |
| F2 | +11.9 | -3.2 (+15.1; -22.0 .. 45.4) | -9.3 / -19.2 / -19.3 | -32.6 |
| F3 | +15.7 | -7.6 (+23.3; -17.3 .. 60.3) | -12.4 / +2.4 / -21.0 | -27.3 |

F1 is worse than random entry within the same thesis direction. F2/F3 are above every control on
the point estimate, but no CI excludes zero.

## R. Clustered inference

Primary: 7-day moving-block bootstrap on entry days (2,000 reps, seed 20261013). Secondary:
cluster bootstrap by thesis episode (370 / 330 / 297 clusters) gives the same picture (p 0.37 to 0.83).
48 h overlap-adjusted C net mean: F1 +4.6 (PF 1.04), F2 +4.5 (1.04), F3 -2.7 (0.98).

## S. Multiple testing

Structural lineage: V1, V2-A, V2-B, V3 (state statistics only). Outcome tests: 9 (P1, P2a, P2b x 3),
Holm at family-wise 0.05. Everything else is secondary. No gate, threshold or definition was changed
after the results.

## T. Validation candidates

| family | net > 0 | PF > 1 | 1 Holm | 3 > random LB | 4 cat <= 10 % | 5 both dirs | 6 overlap | 7 not worse than A | 8 stress | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 | no | no | no | no | no | no | yes | yes | no | **A TRAIN_FAIL** |
| F2 | yes | yes | no | no | no | yes | yes | yes | yes | **B TRAIN_WEAK** |
| F3 | yes | yes | no | no | no | yes | no | yes | yes | **B TRAIN_WEAK** |

**Validation candidates: none.** Validation stays sealed.

## U. Limitations

1. Gate 4 (catastrophic <= 10 %) cannot be met by any 12 to 48 h BTC hold at 20x with a 1.84 % layer;
   this was knowable from the contract and was kept as written.
2. 1m bars: intrabar order unknown; adverse-first is conservative. The aggTrades diagnostic could
   not be run without breaching the holdout.
3. Last-trade prices stand in for mark price for the catastrophic level (more adverse wicks, so
   conservative).
4. Only TRAIN (2021-02 .. 2023-06, one bull and one bear phase). F2/F3 point estimates of +12 to +16 bp
   have SE of 16 to 19 bp: the sample cannot distinguish them from zero.
5. Returns are per unit notional; at 20x a -184 bp catastrophic exit is about -37 % of margin.

## V. Final verdict

F1 **A TRAIN_FAIL**; F2 **B TRAIN_WEAK**; F3 **B TRAIN_WEAK**; **validation candidates: 0**.
No profitability claim is made. STOP.
