# RESULTS: Manual Context & Exit R0.1 (BTCUSDT)

Verdict: **B. MANUAL_CONTEXT_R0_PASS**

The study ran validly, every §10 check passed, and every family reached n ≥ 50 on the 15s-1h
timeframes. **No candidate qualifies**:
- no timeframe's structure beats a placebo;
- no structure exit beats fixed-time or fixed-ATR exits;
- no hold state or entry context survives the pre-registered gate and Holm correction.

No UI V3 preview is built, because §12 builds one only at C or D. Nothing is promoted to a signal.

**Provenance**
- Contract: `CONTRACT_MANUAL_CONTEXT_EXIT_R0_1.md`, sha256 `10d6f7fc…`, commit ea601f3.
  - It supersedes R0 (edb2675) before any outcome was computed. R0 named the D2 files as Binance;
    they are Bybit linear.
- Results: `data/research/crypto/manual_context_exit_r0/results_r0_1.json`, sha256 `63b0bb95…`.
  Two full runs were byte-identical.
- Code: `backend/app/crypto/research/manual_context_exit_r0/`.
- Tests: `backend/tests/crypto/test_manual_context_exit_r0.py` (20 passed).

## A. Data sources / B. Coverage

| id | source (venue) | span used | rows | role |
|---|---|---|---|---|
| K1 | D2 1m klines (Bybit linear), no gaps | 2020-03-25 → 2026-09-23 | 3 416 484 | 1m, 10m, 1h, 4h, 1d |
| K15 | Bybit public trades → 15 s bars | 2026-09-09 → 09-22 | 80 640 bars, 0 empty | 15s |
| D1 | Bybit funding, index 1m, OI 5m | same span as K1 | | context, funding cost |
| C1J | Context Collector journal copy (Binance) | 2026-10-10 12:47Z → 10-11 12:50Z (24.05 h) | 86 580 context, 39 637 V2 walls | walls, flow |
| K1C | Binance 1m klines | 2026-09-10 → 10-11 | 46 k | structure inside C1J |
| MT | paper BTC manual ledger copy (sha f24b16bd…), Binance LIVE fills (GET) | all on record | 24 opens + 10 fills | backfill |

15 s → 1m aggregation against K1 on 20 160 minutes:
- high matches 99.85%, low 99.90%, close 99.98%, volume 99.54%;
- open matches 98.8%.

Liquidation forward (starts 09-27), C1 and Q123 were not joined (contract §1).

## C. Timeframe structure method

The unmodified R1 engine runs on each timeframe's own bars, time-scaled:
- 10m → internal 50m / 10h;
- 1h → 5h / 60h;
- 1d → 5d / 60d;
- 15s → 75s / 15m;
- 1m runs natively.

Warm-up is 1 440 bars. The R1 audit shows **0 repaint violations and 0 vanished levels on every
timeframe**. Parity holds: the recorded nearest R/S equals MC R0's independent `ContextEngine` rows
exactly.

Level coverage (share of published bars with a resistance / a support):

| | 15s | 1m | 10m | 1h | 4h | 1d |
|---|---|---|---|---|---|---|
| resistance | 0.86 | 0.87 | 0.85 | 0.73 | 0.60 | 0.35 |
| support | 0.88 | 0.91 | 0.94 | 0.88 | 0.90 | 1.00 |

## D. Structure reaction (Q1)

Reaction is the share of touches with no close beyond the level within H = 20 bars. The placebo is
the level ± 0.5 ATR.

| TF | RES real n / react | placebo n / react | real − placebo [95% CI] | SUP real n / react | placebo | real − placebo |
|---|---|---|---|---|---|---|
| 15s | 1626 / 6.5% | 908 / 5.8% | +0.6pp [−1.3, +2.5] | 1631 / 6.3% | 7.2% | −0.9pp [−2.8, +1.0] |
| 1m | 4487 / 10.3% | 2030 / 10.5% | −0.2pp [−1.7, +1.3] | 4549 / 10.6% | 11.0% | −0.4pp [−2.1, +1.1] |
| 10m | 1332 / 12.2% | 716 / 13.6% | −1.4pp [−4.2, +1.4] | 1357 / 13.4% | 14.8% | −1.4pp [−4.3, +1.5] |
| 1h | 422 / 14.9% | 294 / 15.3% | −0.4pp [−5.4, +4.3] | 460 / 18.5% | 19.5% | −1.0pp [−5.6, +3.7] |
| 4h | 53 / 24.5% | 26 / 19.2% | +5.3pp [−10.7, +23.6] | 52 / 23.1% | 26.5% | −3.4pp |
| 1d | 10 / 10% | 8 / 12.5% | INSUFFICIENT | 6 / 0% | 14.3% | INSUFFICIENT |

- Holm over 12 tests: every adjusted p = 1.0. **No timeframe is "relevant"**: R1's nearest
  levels react no differently from an arbitrary price half an ATR away.
- The same holds at H = 5 and H = 60. The one nominal exception is 10m RES h60 at −2.6pp, in the
  wrong direction.
- Median break latency is **0 bars**: in most touches the touch bar itself closes beyond. After a
  break, the level is retested within H in 73-89% of cases.
- MFE and MAE are symmetric. Medians at H = 20, in the rejection direction:

  | | 15s | 1m | 10m | 1h |
  |---|---|---|---|---|
  | MFE / MAE (bp) | 5 / 5 | 12 / 11 | 50 / 49 | 134 / 165 |

- Distance bins (share of bars → touch within 20 bars). 1m resistance:

  | ATR band | <0.5 | 0.5-1 | 1-2 | 2-4 | ≥4 |
  |---|---|---|---|---|---|
  | share of bars | 5.6% | 5.2% | 9.5% | 15.1% | 64.7% |
  | touched within 20 bars | 90% | 79% | 62% | 36% | 5% |

  The nearest level is more than 4 ATR away about two thirds of the time on every timeframe below
  1d.

This replicates Price Structure R0, R1 and R2 (A_NOT_USABLE), now on every chart timeframe and
against a placebo.

## E. Wall states (Q3, C1J only, Binance)

Closed V2 walls by the collector's own end state:

| | ASK | BID | median life | touched by mid |
|---|---|---|---|---|
| CANCEL_LIKE | 4 610 | 4 694 | 6-8 s (p90 ~122 s) | 0.3-0.5% |
| CONSUMED_CANDIDATE | 9 | 11 | 31-66 s | 100% |
| NOT_PUBLISHED (rank-evicted / out of coverage) | 15 032 | 14 940 | 10-12 s | 0.5-0.8% |
| UNKNOWN (generation / sync / path gap) | 150 | 152 | ~150 s | ~0 |

- The median end-over-open size is 1.00 in every class, so walls do not visibly shrink before they
  go.
- Mid move after the end, toward the wall's side, n_eff = 60-minute blocks:
  - CANCEL_LIKE and NOT_PUBLISHED: every horizon's CI includes 0 (25 blocks, EARLY).
  - CONSUMED_CANDIDATE: BID m60 −11.8bp [−21.7, −5.8] and ASK m60 −7.8bp. These rest on 6-7
    blocks, so they are **INSUFFICIENT and not interpreted**.

## F. Wall interaction (Q3)

| class | ASK events (blocks) | BID | reading |
|---|---|---|---|
| I-A flow into wall + wall holds ≥ 60 s after touch | 0 | 0 | not measurable by construction (below) |
| I-B flow into wall + consumed + mid through | 8 (6) | 11 (6) | INSUFFICIENT |
| I-C cancel-like, never touched | 4 597 (25) | 4 672 (25) | EARLY. m60 +2.2 / −0.9bp, median absolute move 6 bp, CI includes 0 |
| I-D wall holds + flow against | 0 | 0 | not measurable by construction |

Finding: all 258 V2 walls that the mid ever touched were closed in the **same 1 s sample** as the
touch (post-touch persistence 0 s, p100 = 0). The V2 selection only keeps walls away from the
mid, so with V2 semantics "a wall that holds after being touched" cannot be observed. Measuring
I-A/I-D needs `wall_r0` (OPENED-row) semantics or raw depth. That is a contract decision for R1,
not something to patch here.

## G. Timeframe × wall relevance (Q2)

- **1m:** 32 structure touches inside C1J, all 32 with a near wall (≤ 5 bp), 0 without. There is
  no contrast to measure, so it is INSUFFICIENT.
- **10m:** 5 touches.
- **15s:** NOT_AVAILABLE (no Binance 15 s bars in C1J).
- **1h, 4h, 1d:** NOT_EVALUABLE (K1C cannot warm them up).

A near wall is present at essentially every touch. This restates MC R0's "walls exist in 99% of
samples, presence is not an event". **Show / assist / hide cannot be decided from data yet on any
timeframe.**

## H. Manual entry context (Q8)

No real manual trade has context (§P). The research cohort is the FADE and BREAK entries.
Binary features at entry:
- reward/risk ≥ 1;
- OI up over the last bar;
- funding > 0;
- basis > 0.

Outcome is S3 net. Across 48 tests, the 16 with n ≥ 50 per arm have a smallest raw p of 0.013
(1m FADE rr ≥ 1: +2.8bp [0.6, 5.3], n = 75). Holm-adjusted p is 0.47, or 0.21 within the 16. **No
entry-context candidate.** The cells with adjusted p = 0 (1d, 4h) have n = 1-4: the bootstrap is
degenerate there, the gate is INSUFFICIENT, and they are not interpreted.

## I. Hold-state analysis (Q4)

States (a)-(f) were tested along fixed-time paths:
- (a) invalidation intact;
- (b) target not reached;
- (c) new level before target;
- (d) OI aligned;
- (e) funding favours;
- (f) basis favours.

Outcomes are the move over the next 5 bars and the move to Hmax.
- Of 144 tests, 96 have n ≥ 50.
- The strongest is 1h BREAK (f) basis favours, fwd5 +12.0bp [3.5, 20.3], n = 183, raw p 0.003. Its
  Holm p is 0.41 (0.29 within the 96).
- **No hold candidate.**
- States (g) wall and (h) flow could not be tested: 14 C1J trades.

## J-O. Exit families (Q5, Q6)

Same entries, Hmax 60 bars, cost 11.25 bp plus actual funding. Mean net bp (median):

| TF / cohort (n) | S3 structure | A target | C invalidation | F breakeven | G trail | H60 time | I ±1 ATR |
|---|---|---|---|---|---|---|---|
| 15s BREAK (882) | −11.4 (−12.8) | −11.6 | −11.4 | −11.4 | −11.4 | −11.6 | −11.2 |
| 15s FADE (455) | −11.6 | −11.8 | −10.9 | −11.7 | −11.6 | −11.7 | −11.5 |
| 1m BREAK (2606) | −11.5 (−14.4) | −11.4 | −11.2 | −11.5 | −11.5 | −11.2 | −11.2 |
| 1m FADE (1890) | −11.0 (−14.6) | −10.6 | −11.3 | −11.0 | −11.0 | −11.0 | −11.2 |
| 10m BREAK (874) | −14.9 | −18.6 | −13.1 | −14.5 | −15.0 | −14.1 | −13.0 |
| 10m FADE (713) | −14.9 | −11.7 | −16.5 | −14.8 | −15.0 | −12.4 | −10.9 |
| 1h BREAK (272) | +15.2 (−46.2) | +2.5 | +29.4 | +16.9 | +14.8 | +33.6 | −9.6 |
| 1h FADE (242) | −11.9 | −37.6 | −6.4 | −11.7 | −11.6 | −20.8 | −18.0 |
| 4h BREAK (33) / FADE (27) | EARLY | | | | | | |
| 1d (4 / 4) | INSUFFICIENT | | | | | | |

- **K. Net:** on 15s, 1m and 10m every family nets about −cost. Gross is about 0 whatever the exit,
  so the entries carry no direction and the exit rule only reshapes variance.
- **L. MFE/MAE:**
  - S3 median MFE ≈ MAE (1m: 4.3 / 4.6 bp). Its median hold is 2-3 bars.
  - Median opportunity loss is 7-8 bp on 1m, the MFE given back.
- **M/N. Baselines:**
  - H60 and the ±1 ATR bracket I are indistinguishable from S3 on every timeframe with n ≥ 100.
  - I has the thinnest tail (1m p5 −22.6 vs S3 −26.0, H60 −78).
- **O. Structure exit:**
  - The structure invalidation is close: median 0.38 ATR on 1m, 0.33-0.45 elsewhere. The target is
    far, about 6 ATR. So S3 ends at INVALID in 66-81% of trades (cohorts with n ≥ 100), mostly within
    2-3 bars, and reaches TARGET in 12-22%.
  - Paired S3 − H60 and S3 − I: no Holm-adjusted p is below 0.84 (24 tests). **Structure-based
    exits are not superior.**
  - The only nominal positive with n ≥ 100 is 1h BREAK S3 − I = +24.8bp [−1.4, +50.8], raw p 0.07.
  - 4h BREAK S3 − I +227bp has raw p 0.035 but n = 33 (EARLY) and Holm p 0.84.

## P. Manual trade backfill

| source | entries | CONTEXT_AVAILABLE | PARTIAL | NOT_AVAILABLE |
|---|---|---|---|---|
| paper BTC manual account (2026-09-23 → 10-07) | 24 | 0 | 0 | 24 |
| Binance LIVE fills (10-05 → 10-07) | 10 | 0 | 0 | 10 |

Every BTC manual entry on record is after K1 ends (2026-09-23 00:00Z) and before C1J starts
(2026-10-10 12:47Z). There is no BTC manual entry inside C1J. Binance klines (K1C) do cover
09-10 → 10-11, but the contract counts only K1 + D1 as PARTIAL, so these trades stay
NOT_AVAILABLE and nothing was estimated. MANUAL-R1's point stands: the user's own sample is too
small and unlabelled. The store in §Q is what fixes that.

## Q. Snapshot-store design

`MANUAL_TRADE_SNAPSHOT_STORE_DESIGN.md`. It is a read-only follower of the ledger and mirror with no
order-path hook. It writes an entry snapshot (structure per timeframe, walls, flow,
OI/funding/basis, liquidations, C1, per-layer status) and an outcome row. It never estimates a
missing layer. Design only.

## R. UI V3 proposal (not built)

The contract allows a preview only at C or D, so **none is built**. What the data supports for the
next UI decision:
1. Do **not** add timeframe-specific R/S lines as decision aids. On every timeframe they behave like
   a placebo. V2's R/S lines are also R1 levels; if they stay, they are price annotations, not
   evidence.
2. Do **not** add target / invalidation numbers from structure. Structure targets are about 6 ATR
   away, invalidations about 0.4 ATR, and exits built from them do not beat time or ATR exits.
3. Walls: there is no measurable contrast (a wall is always present, and touched walls vanish the
   same second). The V2 wall lines carry no demonstrated information.
4. What would make a V3 testable:
   - the snapshot store (§Q) running for long enough to collect the user's own labelled trades;
   - a wall definition that can survive a touch (R1 of this study);
   - at least 2-3 weeks of C1J for flow and wall cells to reach REVIEW (Q123 estimates about 30
     events per question in 3-4 weeks).

## S. Sample gates

INSUFFICIENT < 20, EARLY 20-49, REVIEW 50-99, STRONGER ≥ 100.
- **Kline families:** 15s, 1m, 10m and 1h cohorts are STRONGER; 4h is EARLY; 1d is INSUFFICIENT.
- **C1J cells:** at most EARLY (≤ 25 blocks of 60 min).
- **C1J trades:** 14 (INSUFFICIENT).

## T. Changed files

All files are new apart from the contracts:
- `backend/app/crypto/research/manual_context_exit_r0/{__init__,bars,structure,study,walls,stats,run}.py`
- `backend/tests/crypto/test_manual_context_exit_r0.py`
- `data/research/crypto/manual_context_exit_r0/results_r0_1.json`
- `docs/crypto/manual_context_exit_r0/{CONTRACT_MANUAL_CONTEXT_EXIT_R0.md, CONTRACT_MANUAL_CONTEXT_EXIT_R0_1.md, RESULTS_MANUAL_CONTEXT_EXIT_R0_1.md, MANUAL_TRADE_SNAPSHOT_STORE_DESIGN.md}`

Raw inputs stay git-ignored: the 15 s trade archives at `data/runtime/.../bybit_trades_daily/`
(926 MB) and the cache. Evidence copies are in `~/usb-manual-context-r0-evidence/`. The Binance
aggTrades downloaded for R0 (209 MB, `aggtrades_daily/`) are unused after R0.1.

## U. Commits

- edb2675: R0 contract.
- ea601f3: R0.1 contract.
- The next commit: implementation, results and these docs.

## V. Limitations

- **Venue split.** Kline studies are Bybit, the venue of the chart candles. Wall and flow studies
  are Binance, the venue of the collector and of LIVE orders. Results do not transfer across the
  split without a check.
- **C1J is 24 hours.** Every wall and flow conclusion is "not enough data", not "no effect".
- **Cohort definition.** Entries are hypothetical FADE / BREAK events at R1 levels. A discretionary
  trader's entries could differ; that question needs §Q's labels.
- **Fixed-percent baseline.** It is ±1 ATR, scale-adaptive by contract rather than a literal fixed %.
- **Holm families.** These included cells too small to test. Their degenerate bootstrap p = 0 only
  lowers others' adjusted p. Restricting the families to n ≥ 50 leaves the conclusion unchanged
  (hold: min Holm p 0.29; entry: 0.21).
- **I-A / I-D.** These are unmeasurable with V2 semantics, as shown in §F.

## W. Final verdict

**B. MANUAL_CONTEXT_R0_PASS.** The mechanism is valid and reproducible, the samples are sufficient
on 15s-1h, and there are no entry, hold or exit candidates. No automatic signal, AUTO change or UI
deployment follows from this study.
