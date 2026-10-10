# US-B CRYPTO Market Context R0, Results

Contract: `docs/crypto/market_context_r0/CONTRACT_MC_R0.md`
sha256 `e05edec6a3c35670ebe8a1644915c502caed23ed318c63e5b88c98af0f5a0854`, frozen before any outcome
was computed, hash re-checked by the replay at run time and recorded in `results_mc_r0.json`.
Local only. Commits 0, pushes 0, deploys 0. AUTO, PAPER, LIVE and the order paths are untouched.

## Verdict

**A_NO_CONTEXT_EDGE**, with a qualification the verdict line is required to carry: of the four
research questions, **one was answered and three were not asked by the archive.**

| Question | Status |
|---|---|
| Q1, resistance + holding ask wall + buy aggression without progress to a fall | **structure x wall: NOT_MEASURABLE** (3 episodes). The flow half alone: **measured, no edge** |
| Q2, resistance + wall consumption + thin liquidity above to continuation | **NOT_MEASURABLE on both tracks.** 15 consumption events exist in the whole archive |
| Q3, the same at support | same as Q1 and Q2, mirrored |
| Q4, Liquidity and Flow alone, no structural level | **measured, no edge**. 4 of 6 families cleared the gate and none cleared its own cost |

Nothing in the 17 measured `(family x variant x horizon)` tests survived Holm-Bonferroni, and no
measured family's own mean `net_move_bp` was above zero at any horizon. The largest effect found
anywhere was **+4.83 bp** (H1 / DOM at 60 minutes, p = 0.276), against a round trip cost of
**11.25 bp** and a rotation null whose own 99th percentile was larger than the effect.

## 1. Mechanism, before the families

All of contract section 11 passed, and the two that would have stopped the study passed first.

| Check | Result |
|---|---|
| C1 repaint on the Binance series | **0 violations, 0 vanished**, over 48,001 published minutes |
| C2 prefix truncation, R1's 6 cut points | **6 of 6 identical**, every field |
| C3 theta transfer | the held out procedure on the Binance warmup chooses **3.0 / 6.0**, the frozen pair |
| C4 curation transfer | active levels **median 9, p95 13** &mdash; R1's Bybit figures exactly |
| C5 wall rule identity | `lm-wall.v2`, `deaa9db8…`, agrees |
| C6 journal wholeness | the replay starts at each session's first record, so the resting set is whole by construction |
| C7 venue basis | median 1.40 USDT, p95 12.3, **max 622.9 (0.894 percent)** over 76,320 overlapping minutes |

C4 is worth a sentence of its own. The R1 curation was tuned on Bybit and had never been run
anywhere else. Moved to a different venue, with thresholds frozen, it reproduces its own headline
numbers to the unit. Whatever R1 failed at, it did not fail at being a stable mechanism.

C7 is why the structure layer moved venue at all: the median basis is negligible, but the tail is
0.894 percent against a proximity tolerance of 0.25 percent.

## 2. What the archive contains

| | |
|---|---|
| Layer A | 76,800 Binance 1m klines, 2026-08-12 to 2026-10-04T08:00Z, sha256 `fa2deba9…` |
| Layer B | **12,779 one second samples, 12,992 s = 3.61 hours**, five sessions, 2026-10-04T03:57:43Z to 07:34:16Z |
| Layer B quality | book SYNCED 12,775 / 12,779; +-0.1 percent band COMPLETE 94.0 percent; 60s flow COMPLETE 93.1 percent; 8 generations, so 7 re-snapshots |
| Layer B density | 277.1 resting candidates per sample, **28.9 walls per sample** after `lm-wall.v2` |
| Layer C long | taker side split on every one of the 76,800 minutes |

**This is the whole constraint.** There is no level resolution order book history for BTCUSDT
futures outside this journal: Binance's own public archive publishes aggregate trades, best bid and
ask, and a five level percentage depth summary, none of which contain a wall. So Layer B cannot be
extended backwards at any price, only forwards by collecting.

## 3. Episodes

| Track | Episodes | After the gate |
|---|---|---|
| T3, structural approach, 33.3 days | **1,664** (801 resistance, 863 support) | 7 of 12 family variants MEASURED |
| T2, wall approach, 3.61 hours | **1,162** (583 ask, 579 bid) | 4 of 6 MEASURED |
| T1, both at once | **3 resistance, 4 support** | 0 of 4 MEASURED |

T1's exclusion ledger is one line long: **796 of 799 resistance episodes and 858 of 862 support
episodes are `OUTSIDE_JOURNAL`.** Not a single episode was lost to a missing sample, an unsynced
book, an absent wall or an incomplete flow window. The joint model is unmeasurable for exactly one
reason, and it is the 3.61 hours.

## 4. The measured families

`net_move_bp` is the directional close to close move for the family's lean, minus the 11.25 bp round
trip. `edge` is against the complement, the same side episodes where the family did not fire. `p` is
the circular shift test of contract section 13.

### T3, structure x flow, 33.3 days

| Family | Variant | h | n fire | n comp | family net | edge bp | p | Holm |
|---|---|---|---|---|---|---|---|---|
| H1 resistance, buy aggression, no progress | SIGN | 5m | 359 | 442 | -10.93 | +0.51 | 0.655 | no |
| H1 | SIGN | 60m | 357 | 442 | -11.65 | +2.32 | 0.420 | no |
| H1 | DOM | 60m | 99 | 700 | -8.70 | **+4.83** | 0.276 | no |
| H3 support, sell aggression, no progress | SIGN | 5m | 467 | 396 | -10.60 | +1.48 | 0.184 | no |
| H3 | SIGN | 60m | 466 | 395 | -9.71 | -2.33 | 0.386 | no |
| H3 | DOM | 60m | 147 | 714 | -8.63 | +0.02 | 0.995 | no |
| H6 support with failure memory | SIGN | 5m | 52 | 811 | -7.43 | +4.09 | 0.073 | no |

### T2, wall x flow, 3.61 hours

| Family | h | n fire | n comp | family net | edge bp | p | Holm |
|---|---|---|---|---|---|---|---|
| G1 ask wall held under buying | 300s | 194 | 309 | -11.96 | **-1.34** | 0.024 | no |
| G3 ask wall replenished | 300s | 71 | 432 | -12.36 | **-1.43** | 0.010 | no |
| G4 bid wall held under selling | 60s | 195 | 337 | -11.46 | **-0.76** | 0.013 | no |
| G6 bid wall replenished | 60s | 69 | 463 | -10.91 | +0.07 | 0.795 | no |

Three T2 edges clear an uncorrected 0.05 and **all three have the wrong sign**: a held ask wall
under buying was followed by slightly *less* downside than the complement, not more. None survives
Holm, none is larger than 1.5 bp, and none of them is a tenth of the cost of acting on it.

## 5. Why Q2 is not a question this archive can ask

Of the 1,162 wall approach episodes, the wall's fate after 60 seconds was:

| Side | held | gone, price traded through | gone, price never reached it |
|---|---|---|---|
| ASK | 446 | **5** | 126 |
| BID | 423 | **10** | 136 |

**Of every near wall that disappeared, 96.2 percent on the ask and 93.2 percent on the bid
disappeared without price reaching its bin.** Fifteen consumption candidates exist in the whole
archive. The honest caveat is that `btc-ms.v0.1` cannot prove order identity, so a wall that fell
below the 250k notional floor while being partially eaten also lands in the "never reached"
column &mdash; but price not arriving is itself an observation, and it is the usual one.

The structural half of Q2 fails for a separate and equally concrete reason. Of 801 resistance
approaches, 366 (45.7 percent) do eventually clear the level by a round trip within 60 minutes, at
a **median of 16 minutes**. Only **10** clear at the very next minute. The contract anchors an
episode at the first approach after an hour of silence, so H2 had 6 firings out of 801 and H4 had
2 out of 863. Contract section 16 forbids re-anchoring after seeing that, and it is not re-anchored
here; it is recorded as the design finding it is.

## 6. The one result that looked like something, and what it actually was

H1 and H3's pre-registered `reversal` outcome &mdash; the close an hour later being on the far side
of the level by more than a round trip &mdash; separates by about ten points, and under the rotation
test of section 13 it separates convincingly:

| | fires | reversal rate | complement | reversal rate | rotation p | null abs p99 |
|---|---|---|---|---|---|---|
| H1, resistance | 357 | **0.538** | 442 | 0.414 | **0.000** | 0.089 |
| H3, support | 466 | **0.547** | 395 | 0.446 | **0.005** | 0.088 |

It is not a context effect. `reversal` is measured against the **level**, while the episodes do not
start at the level, and the two groups do not start in the same place:

| | entry distance to the level, median | mean |
|---|---|---|
| H1 fires | **0.1485 percent** | 0.1391 |
| H1 complement | **0.0437 percent** | 0.0700 |
| H3 fires | 0.1386 | 0.1347 |
| H3 complement | 0.0411 | 0.0671 |

The firing group starts roughly three times further from its level, which puts it past the reversal
gate before anything happens. Stratifying on entry distance in quintiles, so that both groups face
the same gate, the separation collapses:

| | unstratified | stratified |
|---|---|---|
| H1 | +0.1238 | **+0.0068** |
| H3 | +0.1016 | **+0.0141** |

Inside the strata the differences alternate sign (H1: -0.025, -0.039, +0.015, +0.099, -0.017), while
the reversal rate itself climbs monotonically with entry distance alone, from 0.30 in the nearest
quintile to 0.68 in the furthest. The whole apparent effect was the starting position.

The money measure agreed all along and was not fooled: H1's `net_move_bp` advantage over its
complement is +2.32 bp at 60 minutes with rotation p = 0.420, and H3's is -2.33 bp at p = 0.386.
`net_move_bp` is anchored at the entry rather than at the level, which is why it never showed the
artefact. That is the reason contract section 14 fixes the verdict on it, and the verdict does not
move.

## 7. Why Layer B is not informative here, in one number

Taken from the preview's own census over 6,391 two second buckets:

* an ask wall inside 10 bp of mid existed in **99.0 percent** of buckets, a bid wall in **98.5
  percent**;
* when one existed there were on average **8.8 of them per side**.

"The nearest wall" is therefore not an event. Both sides of the book are walled essentially all the
time at this proximity, which is the same finding the Liquidity Map V1 report reached from the other
direction when it measured the nearest major wall landing on the touch level itself. A condition
that is true 99 percent of the time cannot separate states, and three of the four measured T2
families are built on exactly that condition.

The state label of contract section 15 says the same thing from the screen: over the journal window
it is CONFLICT 35.4 percent, SHORT_LEANING 29.3, LONG_LEANING 21.9, UNKNOWN 12.9 and **NEUTRAL 0.4
percent**. A clause set that almost never says "nothing is happening" is not describing events.

## 8. What this does and does not license

* **It does not license AUTO, PAPER or forward context.** Verdict A.
* **It does not close the joint question.** T1 was never asked. What was shown is that neither half
  carries an edge on its own, which lowers the prior on the joint model without testing it.
* **It does identify the blocking resource precisely.** It is not the model, the curation, the
  wall rule or the cost assumption. It is that Layer B is 3.61 hours long and can only be extended
  forwards. At the observed rate of 1,664 structural episodes per 33 days, a T1 sample of 30
  episodes per family needs on the order of **three to four weeks of continuous collection**, and
  the collector that would do it already exists, is read only, and has run a 2 hour session without
  a gap or a dropped record.
* **It retires the consumption hypothesis at this proximity** regardless of collection length, at
  least in a normal tape: 15 events in 3.61 hours extrapolates to roughly 100 per day, which is
  collectable, but the far larger finding is that near walls are cancelled rather than eaten about
  19 times out of 20.

## 9. Artefacts

| What | Where |
|---|---|
| Contract | `docs/crypto/market_context_r0/CONTRACT_MC_R0.md` + `.sha256` |
| Results | `data/research/crypto/market_context_r0/results_mc_r0.json` |
| Post verdict attribution | `data/research/crypto/market_context_r0/diagnostics_mc_r0.json` |
| Code | `backend/app/crypto/research/market_context_r0/` (collect, data, structure, journal, layers, families, outcomes, stats, replay, diagnose, state, preview_build) |
| Tests | `backend/tests/crypto/test_market_context_r0.py`, 45 passed; R0 37, R1 33, R2 34, liquidity map and MS-V0 isolation all unchanged and passing |
| Preview | `data/research/crypto/market_context_r0/preview/`, `python3 -m http.server 8734 --bind 127.0.0.1` |

Every file above is new. `git status` shows no tracked file modified by this study, so the claim
that the trading service is unaffected is structural rather than asserted.
