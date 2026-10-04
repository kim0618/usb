# A <- Kiwoom Candidate Acquisition Recall Audit V1

| | |
|---|---|
| Verdict | **KIWOOM_ONLY_FAIL** for a ranking prefilter · **the audited premise is false** |
| A contract | `a-mover-scanner-v1.2`, handoff checksum `f05e53cce5a431e8e132a0fc1698085b64f8e1d015e11028a77dc62754a11f25` (recomputed, PASS) |
| Window | 83 sessions, 2026-05-18 .. 2026-09-15 |
| Tape | `data/runtime/strategy_b_e0/cache/ca1ce9d030cdcb88` (104 sessions, 3,882 symbols, uncovered 0) |
| Changes | Strategy A 0 · Strategy E 0 · production 0 · network 0 · PnL 0 · replay 0 · orders 0 · commit 0 |
| Added | `app.dev.run_a_kiwoom_prefilter_audit`, `app.dev.run_a_kiwoom_prefilter_budget` (new files only) |
| Artifacts | `data/runtime/research_reports/a_kiwoom_prefilter_recall_v1/` |

## 0. The premise this audit was given is false

The stage was commissioned on the belief that Strategy E's live paper runtime builds a bounded
candidate universe from Kiwoom ranking TRs and only then processes market data. It does not.

E's own frozen feasibility document says so in its section E, titled *Safe prefilter*: *"No prefilter
meets `false_negative = 0` ... The false-negative proof is therefore not constructible, and **no
prefilter is used**. The breadth denominator stays the canonical universe."*

What E actually does (`app/strategy_e_max_rt/finalizer.py`, `app/dev/run_e_rt2_dryrun.py`):

| Step | Actual |
|---|---|
| Universe | `e-canonical-universe-v2`, **Massive** grouped daily D-1 eligibility, 2,561 symbols, built by `usb-e-universe.timer` at 00:20 ET |
| Acquisition | the **whole** universe over two Kiwoom chart lanes, `usa06011` (minute) + `usa06010` (tick), each limited to 4.9 req/s |
| Ranking TRs | **never used.** "Quotes / rankings are snapshots and are never a finalization lane" |
| Rolling | 04:00 - 09:20:40, latest minute page per symbol; measured 37 cycles, 95,425 lane-A calls |
| Finalization | T0 09:25:00.019 -> T1 09:29:26.698, 2,609 calls (A 1,307 + B 1,302), 9.78 req/s, 0 x 429 |
| Second stage | `availability` (FEATURE_COMPLETE / SPARSE_NO_PREMARKET / MARKET_DATA_UNAVAILABLE / STALE), then H5 `RVOL >= 3.0`, then R1, then Top3 |
| RVOL baseline | `rvol_store` SQLite, `source=KIWOOM`, median of 20 prior staged sessions, minimum 5 |

The ranking prefilter the stage asked about exists in **Strategy A's own** code, not E's:
`app/market/universe.py` `KiwoomUniverseSource.acquire(limit)` reads `usa20540` and `usa20550`,
caps `limit` at 100, and `deploy/systemd/usb-morning-scan.service` passes `--limit 10`.
`volume_ranking()` (`usa20530`) is defined and **never called** anywhere in production.

The union figures the stage quoted (80 / 155 / 313) are **Strategy B's**, from
`B_KIWOOM_BROAD_SCANNER_PREVALIDATION.md` section 18.2, measured 09:35-09:54 ET in the regular
session. Overnight the same unions were 91 / 169 / 330.

## 1. A's live blocker is a clock, not a depth

`usb-morning-scan.timer` fires at `07:00 Asia/Seoul` = **18:00 ET of the previous session**, with
the unit described as *"Run USB morning Scanner after the latest XNYS close"*. At that hour the
ranking carries the just-closed session's totals: the overnight probe read NVDA 109,806,067 and
INTC 191,638,352 accumulated shares, matched them to Kiwoom's own 9/21 daily bars, and watched them
not move at all over eight minutes (Jaccard 1.000, rank moves 0, volume field changes 0).

So A's deployed acquisition is a **previous-close dollar-volume ranking taken the evening before**,
which is a complete explanation of the 13 fixed mega-caps observed 9/8 - 10/1. It is not a bounded
view of the premarket; it has no premarket content whatsoever. A V1.2's 09:15 ET cut is an
operational slot that does not exist in production today.

## 2. Reconstruction

`usa20530.acc_trde_qty` reproduced the tape's cumulative share volume since 04:00 and
`usa20540.trde_prica` its cumulative dollar volume since 04:00, in thousands (B prevalidation
section 18.5, three symbols, error 0 - 0.4%). So on this tape a 09:15 ranking is top-N by
`pm_volume` and by `pm_dollar_volume` at the 09:15 cut, which is what the arms rank on.

Arms, all scored by the frozen V1.2 pipeline with only the `symbols` list changed:

| Arm | Definition |
|---|---|
| `FULL` | A's own broad eligible universe (mean 3,812 symbols) |
| `E50` / `E100` / `E200` | top-N by premarket share volume ∪ top-N by premarket dollar volume |
| `E50_TAPE` / `E100_TAPE` / `E200_TAPE` | the same ranked over the whole minute tape before the universe rule |
| `DV10` | top-10 by premarket dollar volume = A's deployed acquisition shape |
| `DV100` | top-100 by premarket dollar volume = the cap `KiwoomUniverseSource` allows |

`FULL` reproduces the frozen V1.2 artifact: 550 of 591 handoff slots verified identical to
`mover_scanner_v1_2/P35_top8_rows.json` on symbol, rank, total score, gap and PM RVOL, with 0
mismatches (the other 41 were retained by every arm and so never entered the missed-symbol file).

Declared limits: the real rankings range over every Kiwoom listing (~12,700), so the headline arms
are an **upper bound**; and Kiwoom premarket volume is a different tally from the consolidated tape
(median ratio 0.669, max 47x), so the ranking *order* on Kiwoom is not this order.

## 3. Recall

| Arm | mean universe | TOP8 recall mean / median / p10 / min | TOP35 recall mean | 8/8 | >=7/8 | >=6/8 | >=4/8 | <4/8 |
|---|---|---|---|---|---|---|---|---|
| `FULL` | 3,812.5 | 1.000 / 1.000 / 1.000 / 1.000 | 1.000 | 55 | 61 | 71 | 80 | 3 |
| `E50` | 75.8 | **0.398** / 0.375 / 0.125 / 0.000 | 0.411 | 0 | 0 | 3 | 23 | 60 |
| `E100` | 143.1 | **0.520** / 0.500 / 0.295 / 0.000 | 0.549 | 0 | 4 | 8 | 46 | 37 |
| `E200` | 276.6 | **0.624** / 0.625 / 0.375 / 0.000 | 0.685 | 1 | 3 | 21 | 61 | 22 |
| `DV10` | 10.0 | **0.066** / 0.000 / 0.000 / 0.000 | 0.051 | 0 | 0 | 0 | 0 | 83 |
| `DV100` | 100.0 | **0.432** / 0.429 / 0.167 / 0.000 | 0.470 | 0 | 0 | 4 | 31 | 52 |

The `_TAPE` variants land within 0.3pp of their headline twins, so the universe-rule dilution is not
what drives the loss.

Pre-mask discovery TOP8 recall and gate-passing-candidate recall:

| Arm | discovery TOP8 recall | gate-pass candidate recall | its own gate-pass slots (FULL = 385) |
|---|---|---|---|
| `E50` | 0.434 | 0.496 | 340 |
| `E100` | 0.566 | 0.618 | 375 |
| `E200` | 0.655 | 0.722 | **410** |
| `DV10` | 0.069 | 0.101 | 121 |
| `DV100` | 0.465 | 0.520 | 346 |

## 4. What gets missed, and why

| Arm | missed slots | rank 1-3 | not in prefilter | kept but lost to re-rank | missed that would pass the full gate |
|---|---|---|---|---|---|
| `E50` | 354 | 119 | 266 | 88 | 194 |
| `E100` | 281 | 69 | 110 | 171 | 147 |
| `E200` | 225 | 40 | 21 | 204 | 107 |
| `DV10` | 550 | 230 | 550 | 0 | 346 |
| `DV100` | 333 | 99 | 155 | 178 | 185 |

The missed names are **not low quality**. `E50`'s 266 hard prefilter misses have median gap +5.1%,
median PM RVOL 27.3, median momentum 0.612, median tradability 0.952, and 127 of them would have
passed the deployed premarket gate. Core movers are lost, including at rank 1-3.

Two distinct loss mechanisms, and their balance flips with N:

* **acquisition loss** dominates at `E50` (266 of 354). The name never enters;
* **re-ranking loss** dominates at `E200` (204 of 225). V1.2 normalises every component across the
  session's own candidate cross-section. A bounded universe is a different cross-section, so the
  z-scores, the pool cut and the output order all move. This is not an artifact of the measurement:
  it is literally what a bounded-universe V1.2 computes, and no prefilter can avoid it, because the
  normalisation reference cannot include symbols that were never acquired.

## 5. Bias

| Metric | `FULL` | `E50` | `E100` | `E200` | `DV10` | `DV100` |
|---|---|---|---|---|---|---|
| unique symbols | **416** | 208 | 265 | 318 | 37 | 219 |
| repeat ratio | **0.296** | 0.595 | 0.507 | 0.445 | 0.817 | 0.581 |
| turnover | 0.946 | 0.898 | 0.919 | 0.929 | 0.842 | 0.902 |
| median gap | +5.61% | +4.98% | +5.35% | +5.58% | +4.49% | +5.02% |
| median PM RVOL | **22.69** | 4.54 | 8.59 | 14.93 | **1.69** | 4.87 |
| median PM dollar volume | **$19.2M** | $151.7M | $118.5M | $69.4M | $573.0M | $148.6M |
| median ADDV percentile | **86.8** | 98.9 | 98.0 | 96.1 | **99.8** | 98.7 |
| top-liquidity (>=p99) share | **0.068** | 0.493 | 0.402 | 0.321 | **0.896** | 0.471 |
| most repeated symbol | KLAC 6/83 | SNDK 28/83 | SNDK 21/83 | NBIS 17/83 | MU 29/83 | SNDK 28/83 |

`DV10`'s profile (PM RVOL 1.69, ADDV percentile 99.8, repeat ratio 0.82, MU and SNDK 29/83) is
within noise of the frozen `CURRENT` scanner V1 replaced (1.06, 99.87, 0.962, MU 83/83). **The
prefilter, not the scoring, was the root cause of the concentration V1 was built to remove, and a
ranking prefilter puts it back.**

One number favours the prefilter and is reported as measured: `E200` produces 410 gate-passing slots
against `FULL`'s 385. A premarket-timed ranking restores *gate throughput* even though it does not
reproduce *V1.2's selection*.

## 6. Premarket ranking authority

**`PREMARKET_RANKING_AUTHORITY = UNKNOWN`.** Every ranking snapshot in this repository was taken at
ET 02:20-02:47 (Kiwoom overnight, previous-session totals, frozen) or ET 09:35-09:54 (regular
session, accumulating from 04:00). There is **no observation of `usa20530` or `usa20540` inside
[04:00, 09:30) ET**, which is the only window A's 09:15 cut can use.

What is known bounds the question without answering it: the accumulator serves previous-session
closed totals at 02:20 and today's 04:00-inclusive totals at 09:35, so the reset falls somewhere in
(02:47, 09:35]. The WebSocket `FE` lane's own cumulative-volume field was measured resetting at the
premarket start (04:12 ET, session field `290 = "1"`), which makes 04:00 the likely boundary for the
ranking too, but that is a different lane and an inference, not a measurement. Closing it costs one
read-only probe of two TRs inside the premarket.

Related unknowns, unchanged: `_ranking_body()` sends `stk_tp = "1"` and four zeroed condition
filters whose meanings are unverified; and `KiwoomUniverseSource` defaults every unrecognised venue
to `ND`.

## 7. Request budget at a 09:15 cut

Nothing can finish *before* 09:15: the 09:14 bar is only complete at 09:15:00, so T0 is 09:15:00 for
every arm. Rates are the measured ones (5 req/s per API ID; E's deployed two lanes at 4.9 each,
9.78 aggregate observed).

| Arm | ranking calls | rolling calls 04:00-cut | finalization calls | T1 worst case | <09:15 | <09:20 | <09:25 |
|---|---|---|---|---|---|---|---|
| `E50` | 6 | 0 | 76 - 303 | 09:15:32 | NO | YES | YES |
| `E100` | 10 | 0 | 143 - 572 | 09:16:00 | NO | YES | YES |
| `E200` | 20 | 0 | 277 - 1,106 | 09:16:57 | NO | YES | YES |
| `FULL` (E's mechanism) | 0 | ~92,449 | 2,609 | **09:19:26** | NO | **YES** | YES |

The full broad universe therefore **fits inside 09:15 - 09:20 using E's existing, already-measured
mechanism**. The prefilter's real saving is the rolling pass (~92k calls to ~20), bought at a 40-62%
TOP8 recall.

**Lane contention is the actual constraint.** There are exactly two timestamp-safe lanes
(`usa06011`, `usa06010`) and E occupies both at 4.9/s from 04:00 to 09:29:45 on the same app key,
leaving 0.1 req/s of headroom against the measured 5/s per-API-ID ceiling. A's 09:15 finalization
cannot run beside E's rolling pass. Two ways out: share one collector (09:15 is a prefix of E's
09:24 cut, so one rolling cache plus two finalization passes serves both), or add a second app key,
whose REST independence is **untested** since only one key was ever measured.

## 8. RVOL baseline reuse

**`PARTIAL`** - the machinery is reusable, the stored values are not.

| Axis | E's store | A V1.2 needs |
|---|---|---|
| quantity | `pm_dollar_volume` (dollars) | `pm_volume` (shares) |
| window | [04:00, 09:24], and a 09:30 bar required to stage | [04:00, 09:15) |
| staging | a covered-but-silent session is **not** staged and drops out | contributes a **zero** to the median |
| depth | median of 20 priors, **minimum 5**, NaN below | **requires all 20**, otherwise `NO_PREMARKET_RVOL_BASELINE` |
| floor | none | 1,000 shares against a zero median |
| source | KIWOOM | Massive, in the research path |

The store also holds no premarket high, low, 09:00 anchor or up-bar share, all of which V1.2's
momentum and tradability components read. Live, E's finalizer already produces the per-minute bars
those need; the problem is historical.

A prefilter saves nothing here: tomorrow's prefilter members are unknown tonight, so the baseline
must cover the whole universe at any prefilter size. E measured that collection at **571-663k calls,
33-39 hours** for 2,561 symbols x 20 sessions.

And the source itself is not neutral. The frozen K0 calibration measured a Kiwoom-native RVOL
against the Massive RVOL on 10,814 overlapping rows: Spearman **0.668**, Pearson 0.545, precision
0.670 and recall 0.726 at the 3.0 threshold, Jaccard 0.535, identical Top3 on **1 of 14** sessions
with a real row count. A depth-matched control moved Spearman only to 0.717, so the residue is the
source. PM RVOL is 25% of A's V1.2 score, so a Kiwoom-only A would reorder its own pool even with a
perfect prefilter. That is a second, independent obstacle to "Kiwoom-only A V1.2" and the audit's
recall numbers do not include it.

## 9. Decision

Against section M of the stage contract:

| Condition | Required | Measured | |
|---|---|---|---|
| TOP8 mean recall | >= 90% | 0.398 / 0.520 / 0.624 | FAIL |
| sessions >= 6/8 retained | >= 90% | 3.6% / 9.6% / 25.3% | FAIL |
| rank 1-3 misses very few | - | 119 / 69 / 40 of 248 slots | FAIL |
| 09:15-09:25 runtime budget | - | met by every arm, and by `FULL` | PASS |
| premarket ranking authority | confirmed | **UNKNOWN** | FAIL |

**`KIWOOM_ONLY_FAIL` for a ranking prefilter. `RECOMMENDED PREFILTER = NONE.`**

`NEED PAID BROAD LIVE PROVIDER = NOT_YET`, on two measured grounds: Kiwoom already covers the whole
broad universe inside 09:15-09:20 by E's own mechanism, so the live acquisition is not the blocker;
and the remaining blockers (lane contention on one app key, an unmeasured premarket ranking, a
Kiwoom-native RVOL baseline that does not exist and would reorder the pool if it did) are not
blockers a broad live price feed removes. The one thing a paid broad provider does supply is the
daily grouped data the D-1 universe and the gap denominator are built from, which Kiwoom's daily TRs
cannot replace (`usa06012`-`06016` carry no extended-hours separation), so "Kiwoom-only" is already
false at the universe step regardless of the prefilter.

## 10. Confirmations

Strategy A scanner changes 0 · Strategy E changes 0 (read-only audit) · PnL 0 · replay 0 ·
network 0 (`socket.connect` denied for the whole run) · production 0 · real orders 0 · commit 0 ·
push 0 · deploy 0. Targeted regression: 61 passed
(`test_mover_scanner_v1`, `strategy_e_max/test_e_rt1_kiwoom`, `test_real_market_scanner_stage10b`).
