# RESULTS: Wall Lifecycle & Interaction R1 (`wall-lifecycle.r1`), 2026-10-12

Verdict: **B. WALL_INTERACTION_R1_PASS.** Cells reach STRONGER_EVIDENCE, and no cell has a
Holm-adjusted p ≤ 0.05 with a mean effect at or above the 11.25 bp round-trip cost in its natural
direction. No wall edge candidate. Contract `CONTRACT_WALL_LIFECYCLE_R1.md` (sha ccde6bde…,
commit 6899583) hashes as recorded; nothing in it was changed after the results.

Code: `backend/app/crypto/research/wall_lifecycle_r1/` (replay, outcomes, stats, study, advisor,
preview, run). Evidence: `evidence_20261012/` (results.json, counters, timing, prefix and repeat
run logs). Reproduce: `python -m app.crypto.research.wall_lifecycle_r1.run --journal … --binance
… --out … --session 31cc8177`.

## A. Data coverage

| | |
|---|---|
| session | `31cc8177`, one session, no state crossing |
| window | 2026-10-10 21:47:23 to 2026-10-12 11:47:44 KST, sealed files only, 38.0 h |
| context samples | 136,822 (1 s); 32 gaps > 2.5 s, longest 35.7 s; 1 context record without a sample |
| `wall_v2` | 71,836 OPEN, 71,810 CLOSE (NOT_SELECTED 71,659, NO_USABLE_READING 151), 183,295 CHANGE |
| `wall_r0` | 326,431 OPENED rows passing the lm-wall.v2 open floors (250 k, 5x) = level episodes; 190,846 of them display-qualified (≥ 500 k) |
| Binance public | 2,374 closed 1 m klines, 477 OI 5 m buckets, 6 funding rows, 2,387 premium 1 m bars; unsigned GETs from the research machine |
| production | read once (`ssh … tar` of sealed files); write 0; no service touched |

## B. Wall episode counts

| | ASK | BID | total |
|---|---|---|---|
| level episodes | 152,264 | 174,167 | 326,431 |
| display-qualified | 87,393 | 103,453 | 190,846 |
| approached (≤ 5 bp) | | | 60,855 |
| touched | 6,822 | 7,142 | 13,964 (display-qualified 8,755) |
| V2 bin episodes | | | 71,836 (collector end classification matched on 18,140) |
| open at the end of the data | | | 50 |

Most levels end without ever being approached: 293k of 326k are CANCEL_LIKE (section F).

## C. Touch counts

Touch instants 14,627 on 13,964 touched levels (a level is mostly touched once). V2 bins
touched by their own rule: **0** of 71,836 (R0.1 §F again: the 1 bp distance floor closes a V2
bin in the second the mid enters it). This is why every touch statistic here is on `wall_r0`
level episodes, and why Q2 and Q6 join the touched level to its bin (deviations recorded below).

## D. Persistence distribution (post-touch survival, level episodes)

| | n | median s | mean s | p95 s | 0-1 s | 1-5 s | 5-15 s | 15-30 s | 30 s+ |
|---|---|---|---|---|---|---|---|---|---|
| ASK | 6,822 | 3.0 | 17.3 | 80.9 | 2,731 | 1,202 | 1,264 | 648 | 977 |
| BID | 7,142 | 4.0 | 20.4 | 99.0 | 2,568 | 1,320 | 1,371 | 756 | 1,127 |

Q1, 15 min return in the rejection direction (ASK: down +; BID: up +), bp, Holm over 10:

| cell | n | mean | median | p5 | p95 | CI95 | p | Holm | continuation | reversal |
|---|---|---|---|---|---|---|---|---|---|---|
| ASK 0-1 s | 2,712 | -1.90 | -1.28 | -21.9 | 19.8 | [-5.5, 1.3] | 0.35 | 1.00 | 93.5 % | 1.1 % |
| ASK 1-5 s | 1,191 | -2.80 | -2.05 | -26.4 | 19.3 | [-7.2, 0.4] | 0.12 | 1.00 | 91.3 % | 1.3 % |
| ASK 5-15 s | 1,244 | -2.05 | -1.14 | -21.8 | 18.0 | [-5.8, 1.0] | 0.27 | 1.00 | 88.5 % | 1.5 % |
| ASK 15-30 s | 639 | -1.41 | -0.48 | -18.4 | 14.9 | [-4.3, 0.7] | 0.23 | 1.00 | 87.2 % | 2.5 % |
| ASK 30 s+ | 977 | +0.28 | +0.07 | -12.5 | 14.4 | [-1.4, 1.6] | 0.67 | 1.00 | 80.0 % | 2.5 % |
| BID 0-1 s | 2,536 | +3.93 | +2.68 | -18.6 | 27.2 | [-0.5, 8.9] | 0.12 | 1.00 | 91.9 % | 2.4 % |
| BID 1-5 s | 1,302 | +3.44 | +2.29 | -19.7 | 27.4 | [-0.7, 8.6] | 0.13 | 1.00 | 88.3 % | 3.2 % |
| BID 5-15 s | 1,348 | +1.85 | +1.04 | -18.5 | 20.9 | [-0.8, 5.4] | 0.24 | 1.00 | 84.1 % | 3.3 % |
| BID 15-30 s | 743 | +1.67 | +1.01 | -17.0 | 18.7 | [-0.5, 4.5] | 0.17 | 1.00 | 81.7 % | 4.3 % |
| BID 30 s+ | 1,118 | +0.73 | +0.60 | -10.6 | 13.2 | [-0.3, 2.0] | 0.18 | 1.00 | 74.8 % | 1.8 % |

Reading: a touched wall is crossed (continuation) in 75 to 94 % of cases within 15 min, the
longer it survives the touch the less often, and the 15 min return is a few bp with CI through 0
everywhere. The window's drift (BTC rose over the 38 h) shows as the BID + / ASK - asymmetry.
MFE / MAE over 15 min: ASK 0-1 s 8.2 / 9.8 bp, ASK 30 s+ 5.7 / 5.3; BID 0-1 s 12.0 / 7.7, BID 30 s+
5.8 / 4.6 (means). **Q1: no relation that survives cost or Holm.**

## E. Notional dynamics (Q2)

V2 bin episodes (71,810 closed): ratio last / first median 1.00 (p5 0.71, p95 1.44), max / first
median 1.00 (p95 2.55), min / first median 1.00 (p5 0.64); observations per bin median 2.
**The collector carries the size at OPEN and re-observes it only on a tracked change, so a bin's
size is seen 2 times in the median.**

Pre-touch ratio (touched level joined to its bin, 943 pairs; unit deviation, see C):

| side | n | share with any change | tercile cuts | low tercile continuation | high tercile continuation | Spearman ρ | p | Holm |
|---|---|---|---|---|---|---|---|---|
| ASK | 500 | 53.8 % | 1.00 / 1.00 (degenerate: median ratio = 1) | 89.8 % (n 354) | 97.3 % (n 146) | +0.141 | 0.001 | 0.002 |
| BID | 443 | 53.3 % | 1.00 / 1.00 | 91.1 % (n 315) | 89.8 % (n 128) | -0.008 | 0.87 | 0.87 |

ASK only: a bin that grew before the touch was crossed a little more often; the 15 min return
of both terciles is 0.2 bp with CI through 0 (no money in it). BID shows nothing. The terciles are
degenerate because the median ratio is exactly 1; the Spearman is the test that was frozen. Not a
candidate: effect on a rate, not on a net return.

## F. End reason distribution (level episodes)

| class | ASK | BID | meaning |
|---|---|---|---|
| CANCEL_LIKE | 135,809 | 157,132 | ended, never touched |
| TOUCHED_PULLED | 3,861 | 4,024 | touched, not traded through, ended |
| CONSUMED | 2,541 | 2,623 | mid reached the far edge before or at the end |
| OUT_OF_COVERAGE | 336 | 395 | ended outside the book's known interval or the 1 % band |
| UNKNOWN | 9,717 | 9,993 | interrupted row, gap at the end, or open at the cut |
| RANK_EVICTED_V2 (attribute) | 28,319 | 28,696 | the level's V2 bin closed NOT_SELECTED while the level rested |

Q3, 15 min return by end class (touched levels), Holm over 10:

| cell | n | mean | median | CI95 | p | Holm | continuation |
|---|---|---|---|---|---|---|---|
| ASK CONSUMED | 2,519 | -3.01 | -2.17 | [-6.6, -0.1] | 0.03 | 0.12 | 99.7 % |
| ASK TOUCHED_PULLED | 3,828 | -1.17 | -0.47 | [-4.6, 1.1] | 0.47 | 1.00 | 84.1 % |
| ASK UNKNOWN | 416 | +0.96 | +0.51 | [-2.7, 3.8] | 0.56 | 1.00 | 79.8 % |
| BID CONSUMED | 2,583 | +1.96 | +0.88 | [-1.6, 6.5] | 0.44 | 1.00 | 99.6 % |
| BID TOUCHED_PULLED | 3,976 | +3.16 | +1.95 | [0.3, 7.3] | 0.02 | 0.11 | 78.0 % |
| BID UNKNOWN | 488 | +2.79 | +2.16 | [0.4, 5.6] | 0.02 | 0.10 | 78.5 % |

A consumed wall is, by construction, crossed (99.7 %); a pulled wall is still crossed 78 to 84 %
of the time within 15 min. Means of 1 to 3 bp, Holm ≥ 0.10. **Q3: no edge.**

## G. FLOW combinations (Q4; flow side = larger 60 s share, sign only)

Flow side at touch: ASK walls BUY 3,222 / SELL 3,600; BID walls BUY 4,034 / SELL 3,108 (no
reading 0). Class D (cancel-like without a touch): 135,809 / 157,132. Holm over the 6 cells with
events (A, B, C per side; D has no instant to measure from):

| cell | n | sign | mean | median | CI95 | p | Holm |
|---|---|---|---|---|---|---|---|
| ASK A (buy flow, held ≥ 60 s) | 98 | away + | +1.33 | +1.38 | [0.0, 2.7] | 0.05 | 0.20 |
| ASK B (buy flow, consumed) | 1,390 | through + | +3.02 | +2.00 | [-0.8, 6.6] | 0.20 | 0.55 |
| ASK C (sell flow, held ≥ 60 s) | 196 | away + | +1.20 | +1.14 | [-0.5, 2.8] | 0.18 | 0.55 |
| BID A (sell flow, held ≥ 60 s) | 146 | away + | +2.47 | +1.63 | [0.4, 4.9] | 0.01 | 0.08 |
| BID B (sell flow, consumed) | 1,246 | through + | -0.92 | -0.70 | [-4.8, 1.9] | 0.57 | 0.57 |
| BID C (buy flow, held ≥ 60 s) | 221 | away + | +1.39 | +1.18 | [0.1, 2.8] | 0.04 | 0.20 |

Holding walls (A, C) show 1 to 2.5 bp away from the wall in 15 min, Holm ≥ 0.08, under a quarter
of the cost. Size change inside B: the bin's size is observed too rarely to split (section E).
**Q4: direction information is at most 2.5 bp and not significant after Holm.**

## H. OI / funding / basis (diagnostic, n ≥ 20 only)

| touched class | n | OI 1 h change mean % | funding mean | basis bp mean |
|---|---|---|---|---|
| ASK CONSUMED | 2,541 | +0.06 | 0.0000 | -4.98 |
| ASK TOUCHED_PULLED | 3,861 | +0.05 | -0.0000 | -5.01 |
| BID CONSUMED | 2,623 | +0.06 | 0.0000 | -4.93 |
| BID TOUCHED_PULLED | 4,024 | +0.05 | 0.0000 | -4.90 |

Indistinguishable across classes (the window had one funding regime and a flat OI). Not
interpreted further, as frozen.

## I. Next-wall behaviour (Q5; wall 1 ends, display set)

7,945 events (ASK 3,982, BID 3,963), almost all CANCEL_LIKE ends (wall 1 vanished untouched;
consumed wall-1 ends: 17 / 19, pulled: 20 / 35).

| | ASK | BID |
|---|---|---|
| reach wall 2 within 15 min | 23.5 % | 19.2 % |
| reach wall 2 within 60 min | 50.7 % | 40.8 % |
| reach wall 3 within 15 / 60 min | 18.7 % / 44.8 % | 14.5 % / 35.7 % |
| time to wall 2 (reached within 15 min), median | 347 s | 310 s |
| intermediate reversal (≥ 22.5 bp back first) | 4.3 % | 6.8 % |
| max move before reversal, median | 4.2 bp | 4.4 bp |
| gap 1→2, median (p95) | 2.0 bp (6.9) | 1.7 bp (5.7) |
| gap 2→3, median | 1.2 bp | 1.2 bp |
| continuation by gap quartile Q1..Q4 | 24.1 / 28.7 / 24.0 / 17.1 % | 22.1 / 21.5 / 15.5 / 17.8 % |
| Spearman gap vs reach-wall-2 | ρ -0.080, p 0.001, Holm 0.002 | ρ -0.053, p 0.002, Holm 0.002 |
| after a consumed wall 1, reach wall 2 in 15 min | 47 % (n 17) | 89 % (n 19) |

A wider gap to wall 2 means it is reached a little less often (ρ -0.05 to -0.08, significant with
n 3,900 but a difference of 7 points between the first and last quartile). After a *consumed*
wall 1 the next wall is reached far more often, but n is 17 and 19: INSUFFICIENT.

## J. Cluster results (Q6)

Cluster membership read one sample before the touch (deviation, see C). **0 clustered touched
levels on either side** against 4,108 (ASK) and 4,647 (BID) single ones. With 5 USDT bins the only
possible cluster is two *adjacent* display bins, which needs ATR14(1m) ≥ 10 USDT; the
display-qualified touched level and an adjacent display wall did not coincide in this window.
Q6 cannot be answered on these 38 h: INSUFFICIENT. (Single-wall figures: ASK continuation
88.1 %, r15 -1.67 bp; BID 83.5 %, +2.27 bp; CI through 0.)

## Deviations recorded (all structural, none after seeing an outcome)

1. Q2 unit: the contract named the V2 bin's own touch; 0 such events exist. The touched level
   joined to its bin's notional observations is used; terciles, rates, Spearman, Holm 2 as frozen.
2. Q6 and the advisor's cluster flag are read one sample before the touch instant, for the same
   reason.
3. `analysis_request_id` (advisor events) is the contract's join key (session, second, family,
   timing) and repeats when two levels are consumed in the same second (665 of 8,192 events);
   `event_id` (episode, family, timing) is unique and is what the prefix check joins on.

## Reproducibility (contract §9)

| check | result |
|---|---|
| deterministic replay | two full runs: `results.json`, `levels.jsonl`, `advisor_events.jsonl` byte-equal (`evidence_20261012/run_full2.log`) |
| prefix invariance (real journal, cut 2026-10-11 16:47 KST) | 140,622 episodes ended before the cut: 0 field mismatches vs the full run; 2,145 advisor events wholly inside the prefix: 0 mismatches by event |
| no future leak | every feature reads samples ≤ t; external series by closed bar / bucket; unit tests on a scripted path |
| session boundary | state reset at a session change (unit test) |
| end classification, next-wall ordering, duplicate protection | unit tests on the scripted journal (`backend/tests/crypto/test_wall_lifecycle_r1.py`, 9 tests) |
| runtime | 41 s for 38 h (replay 25 s) |

## Limitations

- 38 h, one session, one regime (BTC drifted up; funding flat): the BID / ASK asymmetry is the
  drift, not a wall property.
- Size at open is the only size the collector stores; a wall's size path is seen on average
  4.6 times and in the median twice. A real Q2 needs the collector to publish size changes.
- Level episodes are V0 exact-price rows with OPENED values; a level that is re-quoted at a
  neighbouring tick becomes a new episode, which inflates CANCEL_LIKE and shortens persistence.
- Clusters need either a longer window or a cluster rule in bins, not USDT (not changed here:
  it was frozen).
