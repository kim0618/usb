# PTM-R1 V2 Stage 0 Report (TRAIN census, no outcomes)

Run 2026-10-13 KST. Output `data/research/crypto/ptm_r1/stage0_v2.json`. Code
`backend/app/crypto/research/ptm_r1/{states,stage0_v2}.py`, tests
`backend/tests/crypto/test_ptm_r1_stage0_v2.py` (14) + V1 tests (26) = 40 passed. Run time 6.1 s.

No price parsed; no return/PnL/MFE/MAE/PF/win rate computed or written; VALIDATION and HOLDOUT
not read; nothing operational touched; Q123 untouched; not committed.

**Verdict: A. V2_THESIS_TOO_FAST** (both variants, by the pre-registered rules).

## 1. Contracts

- V1 `CONTRACT_PTM_R1_V1.md` sha `752cbd19339530bb0a9439e9037db430e09c7b122f7cfb82280b485df74eef61`
- V2 `CONTRACT_PTM_R1_V2.md` sha `b711e419f1ca52793cc844afea03afe0bdb04ead8524de228f08b4907bb70a0d`
  (frozen before the run, verdict thresholds included; re-hashed after the run).
- V2-A Completed-Bar: D/H4/H1 = last completed UTC day / 4 h / 1 h block lean; M = rolling 10 m.
- V2-B Persistent Rolling: D confirmed after its raw rolling lean holds 240 min, H4 after 60 min
  (the next shorter horizon's length), sticky until the opposite is confirmed; H1/M raw.

## 2. Data and leak checks

Parity re-verified 31,680 minutes / 0 differences. TRAIN 1,267,200 minutes read, missing 0,
invalid 0. In-run leak check on real TRAIN data (all minutes after a cut rewritten as all-buy):
V1, V2-A and V2-B states and setups up to the cut unchanged, perturbation visible after the cut.
Plus unit tests: block lean vs brute force, in-progress block ignored, persistence rule, leak,
LONG/SHORT symmetry for both variants.

## 3. State frequency (% of evaluated minutes)

| horizon | V1 BUY/SELL | V2-A BUY/SELL | V2-B BUY/SELL |
|---|---|---|---|
| 10M | 49.1 / 50.9 | 49.1 / 50.9 | 49.1 / 50.9 |
| 1H | 48.2 / 51.8 | 48.1 / 51.9 | 48.2 / 51.8 |
| 4H | 46.7 / 53.3 | 46.8 / 53.2 | 46.4 / 53.6 |
| 1D | 44.3 / 55.7 | 43.8 / 56.2 | 43.8 / 56.2 |

EVEN 0 everywhere; NONE only 0.008 % on 10M.

## 4. Flip frequency and state duration (V1 vs V2)

| horizon | V1 flips/day | V1 median / mean run | V2-A flips/day | V2-A median / mean | V2-B flips/day | V2-B median / mean |
|---|---|---|---|---|---|---|
| 10M | 167.0 | 6 / 8.6 min | 167.0 | 6 / 8.6 min | 167.0 | 6 / 8.6 min |
| 1H | 62.2 | 6 / 23 min | 12.3 | 60 / 118 min | 62.2 | 6 / 23 min |
| 4H | 28.6 | 7 / 50 min | 2.78 | 8 h / 8.6 h | 3.18 | 5.3 h / 7.5 h |
| 1D | 7.61 | 7 min / 3.1 h | **0.43** | **48 h / 55 h** | **0.52** | **30.6 h / 45.9 h** |
| big-direction regime (D = H4) | 36/day | 7 min / 52 min | 3.0/day | **8 h** / 8.8 h | 3.7/day | **5.4 h** / 7.7 h |

1D is now slow (once every 2 days). The regime is bounded by 4H, which still changes 3 times a day.

## 5. Alignment

| | V1 | V2-A | V2-B |
|---|---|---|---|
| D = H4 | 65.2 % | 56.0 % | 59.6 % |
| D = H4 = H1 | 43.2 % | 31.3 % | 32.3 % |

## 6. Setups (LONG raw / primary, SHORT raw / primary, 48 h overlap-adjusted N)

| | V1 | V2-A | V2-B |
|---|---|---|---|
| F1 | 4,613/4,613, 5,548/5,548, **414** | 576/576, 718/718, **365** | 699/699, 862/862, **368** |
| F2 | 9,961/1,818, 13,431/2,239, **422** | 8,390/576, 12,849/719, **404** | 7,225/618, 10,714/781, **408** |
| F3 | 4,653/1,296, 6,531/1,681, **415** | 839/387, 1,220/527, **366** | 5,045/628, 7,390/802, **407** |

All overlap-adjusted N are 365 to 422 (LIMITED band; the 48 h envelope caps them near 440).
In V2-A the F2 primary set nearly duplicates F1 (same 576 LONG, identical survival shares): with
block-based D/H4/H1, a 10M pullback usually completes within minutes of the regime's first full
alignment, so F1 and F2 mark almost the same moments.

## 7. Invalidation (primary-dedup setups; median minutes to first occurrence)

| family | I1 1H | I2 4H | I3 1H+4H | I4 D/H4 |
|---|---|---|---|---|
| V2-A F1 L / S | 60 / 60 | 360 / 420 | 420 / 480 | 334 / 420 |
| V2-A F2 L / S | 55 / 58 | 349 / 414 | 401 / 456 | 313 / 399 |
| V2-A F3 L / S | 60 / 114 | 298 / 300 | 356 / 358 | 296 / 300 |
| V2-B F1 L / S | 19 / 21 | 309 / 353 | 324 / 363 | 300 / 349 |
| V2-B F2 L / S | 32 / 31 | 307 / 355 | 325 / 364 | 301 / 352 |
| V2-B F3 L / S | 9 / 9 | 297 / 320 | 301 / 329 | 284 / 315 |

V1 for reference: I4 median 13 to 106 min. Within-48 h occurrence: 99.2 to 100 % for every
candidate in both variants.

## 8. Thesis survival (I4, share of setups still valid after h)

| variant / family | 4 h | 8 h | 12 h | 24 h | 48 h |
|---|---|---|---|---|---|
| V1 F2 L / S | 22.7 / 25.5 % | 4.7 / 6.8 % | 1.1 / 1.8 % | 0 / 0.1 % | 0 / 0 % |
| V2-A F1 L / S | 50.5 / 54.0 % | 26.6 / 30.9 % | 16.0 / 19.2 % | 2.4 / 3.9 % | 0 / 0.4 % |
| V2-A F2 L / S | 50.5 / 54.1 % | 26.6 / 31.0 % | 16.0 / 19.1 % | 2.4 / 3.9 % | 0 / 0.4 % |
| V2-A F3 L / S | 52.5 / 53.5 % | 28.2 / 31.1 % | 18.4 / 19.9 % | 2.6 / 4.6 % | 0 / 0.4 % |
| V2-B F1 L / S | 64.2 / 69.7 % | 30.8 / 36.7 % | 16.3 / 18.7 % | 3.7 / 4.5 % | 0 / 0.6 % |
| V2-B F2 L / S | 64.2 / 70.4 % | 31.9 / 36.1 % | 16.7 / 19.9 % | 3.7 / 4.9 % | 0 / 0.6 % |
| V2-B F3 L / S | 54.6 / 60.2 % | 29.6 / 32.9 % | 15.6 / 17.7 % | 3.3 / 4.6 % | 0 / 0.5 % |

I1 (1H) survival at 12 h is 0 to 0.2 % in every variant; I3 is a little longer than I4.

## 9. Verdict per pre-registered rule

| rule | V2-A | V2-B |
|---|---|---|
| SLOWER: D flips/day <= 2.537 and regime median >= 21 min | **pass** (0.43; 480 min) | **pass** (0.52; 323 min) |
| HOLD_LENGTH: I4 alive at 12 h >= 20 % and >= 100 alive at 24 h | **fail** (best F3 19.3 %, 34 alive) | **fail** (best F2 18.4 %, 61 alive) |
| SAMPLE: overlap-adjusted N >= 100 | pass (365 to 404) | pass (368 to 408) |
| NO_LEAK / data | pass | pass |
| verdict | **A V2_THESIS_TOO_FAST** | **A V2_THESIS_TOO_FAST** |

The 12 h share misses the 20 % line narrowly (V2-A F3 SHORT alone is 19.9 %), but the 24 h count
misses clearly (34 to 65 versus 100). The rule was fixed before the run and is applied as written.

## 10. What the numbers say (state properties only, no edge judgment)

- The 1D state now has the user's horizon: V2-A median run **48 h**, V2-B **30.6 h**.
- The thesis does not, because I4 requires D **and** H4 to agree, and H4 changes about 3 times a
  day (V2-A median 8 h, V2-B 5.3 h). The binding layer is 4H, not 1D.
- 1H as an invalidation (I1) is not compatible with a 12 h hold in any variant.
- Overlap-adjusted N is ample for statistics (about 400 per family) in every version.

## 11. Options for the user (nothing done; any of these is a new contract version)

1. **Stop PTM-R1 here** as "a flow-lean thesis cannot carry a 12 to 24 h hold" (A).
2. **V3 with the thesis on 1D alone** (4H only as an entry condition, not part of the thesis).
   The V2-A 1D run length (median 48 h) suggests the hold-length test could pass, but this is a new
   definition and must be pre-registered with the same verdict rules before it is counted. It would
   be the third variant set on the same TRAIN, which raises the multiple-testing count; the holdout
   remains unread so it is still protected.
3. Accept a shorter-hold method (4 to 8 h, where V2 survival is 27 to 70 %) and change the study
   question, which no longer matches the user's stated style.

No recommendation is made on edge. STOP.
