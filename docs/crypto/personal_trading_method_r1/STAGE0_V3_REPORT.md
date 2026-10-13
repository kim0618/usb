# PTM-R1 V3 Stage 0 Report (1D-only thesis, TRAIN census, no outcomes)

Run 2026-10-13 KST. Output `data/research/crypto/ptm_r1/stage0_v3.json`. Code
`backend/app/crypto/research/ptm_r1/{v3,stage0_v3}.py`; tests
`backend/tests/crypto/test_ptm_r1_stage0_v3.py` (20) + V1 (26) + V2 (14) = **60 passed**. Run 5.1 s.

No price parsed; no return/PnL/gross/net/MFE/MAE/PF/win rate/profit/loss computed or written;
VALIDATION and HOLDOUT not read; Q123 store not read, nothing operational touched; not committed.

**Verdict: D. V3_RESEARCHABLE → RULE FREEZE (contract V3 section 0).**

## A. V3 contract sha

`CONTRACT_PTM_R1_V3.md` sha256 `c34cc8011204c136fd6debafc8e1e2f1d4e31cea9f755a0f8ac9da1e6b9cb14e`.
Verdict thresholds, including TOO_SLOW, were fixed before the run. One pre-run edit: output
sub-tags renamed `*_RETURN` → `*_REALIGN` because the performance-key scanner (token "return")
flagged them; no definition changed and nothing had been run on TRAIN with V3 before the rename.
V1 `752cbd19…`, V2 `b711e419…` re-hashed in the same run, unchanged.

## B. Lineage V1 → V2 → V3

| version | thesis | Stage 0 | outcomes seen |
|---|---|---|---|
| V1 | rolling D = H4 regime (I4) | researchable counts, thesis median 7 min | none |
| V2-A | completed-block D = H4 (I4) | THESIS_TOO_FAST (12 h survival 17.7 to 19.3 %) | none |
| V2-B | persistent rolling D = H4 (I4) | THESIS_TOO_FAST (16.8 to 18.4 %) | none |
| V3 | completed-block D alone (I1) | **RESEARCHABLE** | none |

Four structural variants on TRAIN, recorded as the multiple-testing count. V3 passing freezes the
thesis structure: no further change in R1.

## C. Data coverage

Parity re-verified: 31,680 minutes, 0 differences (volume fields only). TRAIN 2021-02-01 ..
2023-06-30: 1,267,200 minutes read, missing 0, invalid 0. Evaluated 879.0 days (from the first
completed day). Files read: 29 TRAIN monthly + 22 parity daily + 1 REST copy, all with sha256 in the
output.

## D. 1D thesis frequency

| D state | % of minutes |
|---|---|
| BUY | 43.80 |
| SELL | 56.20 |
| EVEN (neutral) | 0 (0 days) |

1D flips 0.43 times per day (only at UTC 00:00). Complete thesis episodes: 380 (190 BUY, 190 SELL).

## E. Thesis duration (complete episodes)

median **48 h**, mean 55.3 h, p25 24 h, p75 72 h, p90 96 h.
Episode length in days: 1 day 187 (49 %), 2 days 84, 3 days 47, 4 days 29, 5 to 9 days 27,
10 days or more 6 (longest 26 days).

## F. Thesis survival after entry (I1 not yet occurred, pooled LONG+SHORT, primary setups)

| family | N | 4 h | 8 h | 12 h | 24 h | 48 h | time to I1 median L / S |
|---|---|---|---|---|---|---|---|
| F1 TREND_CONFIRM | 551 | 98.9 % | 96.4 % | **92.6 %** | **56.6 %** | **33.6 %** | 39.4 h / 44.0 h |
| F2 SHALLOW_PULLBACK | 478 | 97.3 % | 93.3 % | **88.1 %** | **62.8 %** | **36.6 %** | 38.0 h / 41.0 h |
| F3 DEEP_PULLBACK | 415 | 89.4 % | 78.3 % | **69.4 %** | **56.6 %** | **35.4 %** | 30.0 h / 33.9 h |

No censoring affected these (all eligible counts equal N). I1 within 48 h: F1 66.4 %, F2 63.4 %,
F3 64.6 %. So MAX HOLD 48 h, not I1, ends about a third of theses.

## G. F1 counts (TREND_CONFIRM)

| | raw | of which 10M re-align / thesis start | primary | interval primary median |
|---|---|---|---|---|
| LONG | 8,574 | 8,390 / 184 | 249 | 66.7 h |
| SHORT | 13,035 | 12,849 / 186 | 302 | 55.2 h |

## H. F2 counts (SHALLOW_PULLBACK_RETURN)

LONG raw 839, primary 211; SHORT raw 1,220, primary 267.

## I. F3 counts (DEEP_PULLBACK_RETURN)

LONG raw 392, primary 181; SHORT raw 532, primary 234. UNCLASSIFIED_EVEN: 1 (SHORT).

Direction balance (reported, not gated): LONG share of primary setups F1 45.2 %, F2 44.1 %,
F3 43.6 %. Thesis episodes are balanced (190/190); the tilt comes from the taker split leaning SELL
more often on the lower horizons. Not linked to any outcome.

## J. 48 h overlap-adjusted N

| family | per-family 48 h N | across all families (one position) |
|---|---|---|
| F1 | 404 | 195 |
| F2 | 366 | 62 |
| F3 | 317 | 148 |

Raw, primary and overlap-adjusted counts are all reported; none is an independent outcome sample.

## K. Warning-state counts (over all thesis minutes)

| state | share of thesis minutes | runs | runs per thesis day | run median / mean |
|---|---|---|---|---|
| W1 4H against | 44.0 % | 1,246 | 1.42 | 4 h / 7.5 h |
| W2 1H + 4H against | 22.9 % | 2,716 | 3.09 | 1 h / 1.8 h |
| W3 10M + 1H + 4H against | 11.3 % | 18,113 | 20.6 | 5 min / 7.9 min |

Within one thesis episode (380): 4H changes median 4 (2.58 per thesis day), 1H 18 (12.0 per day),
10M 302 (167 per day). Every episode had at least one 4H excursion against the thesis: 1,313
excursions, **81.2 % returned to the thesis direction** before the thesis ended, 247 were still
against when it ended, 0 left via EVEN/NONE.

## L. Warning → 1D invalidation sequence (per primary setup, window = entry to min(I1, +48 h))

| family | W1 before I1 | I1 within 48 h after a W1 | I1 while W1 active | W1 runs before end, median |
|---|---|---|---|---|
| F1 | 97.6 % | 65.7 % | 69.6 % | 2 |
| F2 | 95.6 % | 61.6 % | 70.2 % | 2 |
| F3 | 84.1 % | 58.2 % | 67.2 % | 2 |

State sequences only. W1 appears in almost every holding window, so it is common rather than
specific: about 4 in 10 W1-first windows reach 48 h without I1. NEUTRAL before I1: 0 (EVEN never occurs).

## M. V1 / V2 / V3 state comparison (no outcomes)

| | 1D flips/day | thesis median | F1 surv 12/24/48 h | F2 | F3 | primary F1/F2/F3 | overlap N F1/F2/F3 |
|---|---|---|---|---|---|---|---|
| V1 | 7.61 | 7 min | 0.7 / 0.0 / 0 % | 1.5 / 0.1 / 0 % | 1.4 / 0.0 / 0 % | 10,161 / 4,057 / 2,977 | 414 / 422 / 415 |
| V2-A | 0.43 | 8 h | 17.8 / 3.3 / 0.2 % | 17.7 / 3.2 / 0.2 % | 19.3 / 3.7 / 0.2 % | 1,294 / 1,295 / 914 | 365 / 404 / 366 |
| V2-B | 0.52 | 5.4 h | 17.6 / 4.2 / 0.3 % | 18.4 / 4.4 / 0.4 % | 16.8 / 4.1 / 0.3 % | 1,561 / 1,399 / 1,430 | 368 / 408 / 407 |
| **V3** | 0.43 | **48 h** | **92.6 / 56.6 / 33.6 %** | **88.1 / 62.8 / 36.6 %** | **69.4 / 56.6 / 35.4 %** | 551 / 478 / 415 | 404 / 366 / 317 |

(V1/V2 thesis = D = H4 regime, invalidation I4; V3 thesis = D alone, invalidation I1. Family
definitions differ between V1/V2 and V3, so counts are not like for like.)

## N. Future-leak proof

In-run check on real TRAIN data (20 days, cut at day 10 + 737 min, every later minute rewritten as
all-buy): D, H4, H1 and M states, setups (with family and tag) and W1/W2/W3 masks up to the cut all
equal; the perturbation is visible after the cut. Unit tests repeat this on synthetic data, and
check that D changes only at UTC midnight and each block equals its brute-force sum.

## O. Holdout protection

Reader refuses monthly files after 2023-06 and rows at or after 2023-07-01 (tested for 2023-07,
2024-12, 2025-04); only columns 0, 7, 10 parsed; a fixture with `NOT_A_PRICE` in every price column
gives a byte-identical census to the same volumes with valid prices; output scanned for
return/pnl/gross/net/mfe/mae/pf/win_rate/profit/loss/price keys (scanner tested to catch them).

## P. Tests (60 passed)

V3 (20): completed 1D/4H/1H blocks vs brute force; D changes only at UTC midnight; rolling 10M;
F1 (thesis start, 10M re-align), F2, F3, UNCLASSIFIED detection; no setup in neutral/undefined
thesis; LONG/SHORT symmetry (setups, counts and survival mirror); W1/W2/W3 masks; I1 timing,
12/24/48 h survival and warning sequence on a hand example; thesis duration with edge runs
dropped; TRAIN-end censoring; dedup (raw, primary, overlap per family and global); future leak;
validation/holdout refusal and TRAIN boundary; garbage-price invariance; no performance fields;
deterministic byte-equal output; verdict rules A to E. Plus V1 (26) and V2 (14) unchanged and
passing. The full run was executed twice: output files are byte-identical.

## Q. Limitations

1. **The thesis is a daily lag.** D is yesterday's (UTC) flow and changes only at 00:00 UTC. A
   position opened at 01:00 carries a thesis that is already up to 25 h old, and I1 can only fire at
   midnight. Survival therefore depends on time of day of entry.
2. **Half of all theses last one day** (187 of 380). The 48 h median comes from a long tail.
3. **W1 is common** (44 % of thesis time, about 1.4 new runs per day) and precedes I1 in 84 to 98 % of
   windows; as a warning it is frequent, not selective. A user holding through W1 will see the 4H
   against them most days.
4. **MAX HOLD binds about a third of the time** (I1 within 48 h only 63 to 66 %). Policy B
   (thesis hold) will often exit by MAX HOLD, not by thesis.
5. **F1 raw counts are dominated by 10M re-alignment** (8,390 of 8,574 LONG); the same thesis gives
   many entries. Inference must cluster by thesis episode or UTC day.
6. SELL-leaning taker split gives more SHORT setups (LONG share 44 to 45 %).
7. Four structural variants were examined on TRAIN (state statistics only). VALIDATION and HOLDOUT
   remain unread, which is what protects the main study.

## R. Does V3 match the user's holding style?

On state properties, yes: after entry the thesis is still valid at 12 h in 69 to 93 % and at 24 h
in 57 to 63 % of setups; median time to invalidation is 30 to 44 h, inside the user's 12 to 45 h
range, and a third of theses outlive 48 h. The invalidation is a single, explicit event (the next
completed day's flow turns against), which is the "my reason is gone" rule the user describes;
4H/1H/10M changes are warnings, not exits. Whether holding through those warnings is good or bad is
not known and is not judged here.

## S. Final verdict

**D. V3_RESEARCHABLE** (E no: data and leak pass; B no: median 48 h <= 96 h and I1-within-48 h
63 to 66 % >= 25 %; A no: median >= 24 h and all three families qualify; C no: overlap N 317 to
404 >= 100). **RULE FREEZE** on V3. The next step (R1 main study contract: policies A/B/C on V3
setups, I1 as the only thesis exit, TRAIN outcomes first, VALIDATION, then the one-time HOLDOUT)
needs the user's approval. STOP.
