# A / E Shared Premarket Collector + A Live Feature Parity V1

| | |
|---|---|
| Verdict | **A_LIVE_SHADOW_REQUIRED** |
| Shared collector | **POSSIBLE** · rate limit **PASS** · E's SLA preserved by construction |
| A 20-day baseline | **NEW_FORWARD_BUILD_REQUIRED** from stored data (0 sessions), backfill available |
| Massive ↔ Kiwoom | A TOP35 overlap **0.578**, actionable TOP8 overlap **0.618**, identical TOP8 on 1 of 14 sessions |
| A contract | `a-mover-scanner-v1.2`, checksum `f05e53cce5a431e8e132a0fc1698085b64f8e1d015e11028a77dc62754a11f25`, recomputed PASS |
| Changes | A 0 · E 0 · production 0 · network 0 · PnL 0 · orders 0 · commit 0 · push 0 · deploy 0 |
| Added | `app.dev.run_a_kiwoom_parity_audit`, `app.dev.run_a_shared_collector_budget` (new files only) |
| Artifacts | `data/runtime/research_reports/a_kiwoom_prefilter_recall_v1/{parity_report,shared_collector_budget}.json` |

E50 / E100 / E200 is closed. No ranking prefilter appears anywhere below.

## A. What E's collector actually holds, against what A needs

E's rolling collector keeps, per symbol, `SymbolCache.bars`: a `minute -> [open, high, low, close,
volume]` map over [04:00, 09:24], built from `usa06011` pages (fields `bus_dt, cntr_tm, open_pric,
high_pric, low_pric, cur_prc, trde_qty`; no VWAP) and from `usa06010` ticks aggregated to the same
shape. Every intraday input A V1.2 reads is in that structure.

| A V1.2 input | From E's collector | Note |
|---|---|---|
| 09:15 price (`pm_last_price`) | **AVAILABLE** | close of the 09:14 bar; A's cut is `< 555` |
| 04:00-09:15 share volume (`pm_volume`) | **AVAILABLE** | `sum(trde_qty)` over the window |
| PM dollar volume | **AVAILABLE** | `sum(close x volume)` - **A's own formula**, since neither the Kiwoom chart nor the B-E0 cache carries a VWAP column |
| PM high / low | **AVAILABLE** | `max(high_pric)` / `min(low_pric)` |
| PM first price | **AVAILABLE** | `open_pric` of the first bar |
| `pm_bars` (activity proxy) | **AVAILABLE** | Kiwoom does not pad no-trade minutes, so the returned bar count *is* A's print count |
| `pm_up_bar_share` | **DERIVABLE** | needs per-bar open; `open_pric` is present |
| 09:15 momentum reference | **DERIVABLE** | **A has no 09:00 anchor.** `momentum_late_window_minutes = 60` puts A's reference at the last close before **08:15**; the 09:00 anchor is E's `return_0900_0925` |
| 09:00 price anchor | AVAILABLE but **unused by A** | kept here only to answer the question as asked |
| `tradability` | **PARTIAL** | prints and price level are Kiwoom's; the depth term is `addv20_dollar`, which is daily |
| previous regular close (gap denominator) | **NOT_AVAILABLE** | `usa06012`-`06016` carry no extended-hours separation, so a Kiwoom daily close is not the regular-session close |
| `adv20_shares` / `addv20_dollar` | **NOT_AVAILABLE** | same objection; A's research path reads grouped daily |
| the CS universe rule | **NOT_AVAILABLE** | `NON_COMMON_BY_CIK_PREFIX_NO_FIGI` needs CIK, `composite_figi` and security type. `usa10099` is read for `stk_cd` only and `map_metadata` returns name, market cap, exchange and a suspension flag; no observed Kiwoom endpoint in this repo supplies the three |
| split execution dates | **NOT_AVAILABLE** | and an unremoved split session produces a large false gap |
| the 20-session RVOL baseline | **NOT_AVAILABLE** from stored data | see D and E |

So the live feature *set* is reachable from the collector A would share; the **daily** inputs and the
**universe definition** are not, which is why "Kiwoom-only" stays false independently of this stage.

## D. Retained pre-09:15 raw: 0 sessions

Searched: every file under `data/runtime/strategy_e_max/`, both local Kiwoom SQLite stores, the RT2
dry-run artifacts, the paper session records and A's own analytics tables.

| Asset | Holds | Reconstructs A's fields? |
|---|---|---|
| `strategy_e_max/calibration/kiwoom_snapshot.sqlite3` (13 MB, the K0 snapshot of the server store) | 67,455 rows, 2,566 symbols, 159 sessions 2026-03-02..09-24: `pm_dollar_volume`, `pm_bars`, `has_open_0930`, `staged` over **[04:00, 09:24]** | **NO** - dollars not shares, one aggregate not a split at 09:15, no price, no high / low |
| `strategy_e_max/rvol/kiwoom_premarket.sqlite3` | 16 KB, empty (the live store is the server's) | NO |
| RT2 run artifacts | `capacity.json`, `lane_stats.json`, `tick_pagination.json`, `cutoff_audit.json`, `symbol_status.csv` (symbol, lane, `last_complete_minute`, `final_pages`, status) | NO |
| `rvol_evidence.csv` per paper session | symbol, `kiwoom_pm_dollar_volume`, denominator, `kiwoom_rvol`, staged count, state | NO |
| A's `premarket_volume_sessions` | share volume, **[04:00, 09:30)**, `KIWOOM_USA06011` - the right quantity, the wrong window | **0 rows** |

E's own inventory says the same in one line: *"E-RT2 dry-run artifacts | status rows only; bars were
not persisted"* (`E_RT3_RVOL_DENOMINATOR_V1.md` section 2).

**Reconstructable sessions for A's required fields: 0 of 159.** The walk that built E's store read
the minute bars and kept only an aggregate, so the shares A needs were in memory and discarded. That
schema choice is the whole cost of this answer.

## E. A's 20-session baseline

**`NEW_FORWARD_BUILD_REQUIRED`** from what is stored. A backfill is nevertheless available and is
strictly better than waiting 20 sessions forward, because `usa06011` history reaches back more than
six months (2026-03-02 was read in the B-E1 probe).

Machinery to reuse is **A's own**, not E's: `app/models/analytics.py::PremarketVolumeSession` plus
`app/services/premarket_volume_history.py` already store Kiwoom share volume per (symbol, session)
with `source = KIWOOM_USA06011`, a 20-session median, and `collector_version` inside the row
identity, so a 09:15-cut contract starts its own rows instead of reinterpreting the 09:30 ones. Two
differences to carry: the window (04:00-09:30 against A V1.2's 04:00-09:15) and the baseline
membership (the service takes *the exact 20 prior XNYS sessions*; V1.2 takes *the 20 prior covered
sessions*, counts a covered-silent session as a zero and floors a zero median at 1,000 shares).

Backfill cost, scaled from E-RT3's measured per-symbol walk (mean **223 pages / 63 s** to reach 20
staged sessions; `usa06010` cannot reach deep history, so this is single-lane):

| Universe | Pages | at 4.9 req/s | at the pilot's measured 3.94 req/s |
|---|---|---|---|
| E's 2,561 (control) | 571,103 | 32.4 h | 40.3 h |
| A union, safe prune (4,915) | 1,096,045 | **62.1 h** | **77.3 h** |
| A union, CS upper bound (5,226) | 1,165,398 | 66.1 h | 82.2 h |

The control reproduces E-RT3's own published figure (571k calls, 33-39 h), which is what makes the
scaling usable. With the 03:55-09:35 ET collection guard that is 3.4 to 4.5 calendar days.

## B. The union universe

| | Symbols |
|---|---|
| E's canonical universe (Massive D-1 eligibility) | 2,561 |
| A's research universe on 2026-09-15 (tape-bounded) | 3,842 |
| A's live CS-active universe (reference cache, no tape bound) | 5,222 |
| ... with a full 20-session daily baseline (the only strictly D-1-derivable floor) | 4,911 |
| ... adding D-1 close >= $1 (a judgment: A rejects on the *premarket* print) | 4,563 |
| **E ∩ A (research / live)** | **2,556 / 2,557** |
| **E only** | **5 / 4** |
| **Union** | **3,847 / 5,226**, or 4,915 under the safe prune |

**E's universe is very nearly a subset of A's.** A shared collector therefore costs what A's own
collector would cost, and E rides along for four or five extra symbols.

## B/C. The shared schedule, and why a separate A process fails

There are exactly **two timestamp-safe lanes**, `usa06011` and `usa06010` (capability manifest).
There is no third: `usa06012`-`06016` are daily or longer, the other `06000`-`06030` ids return
1504, and `usa20100` is a snapshot at call time, so a pass over the union would straddle the cut and
cannot serve a point-in-time contract at all.

E already runs both lanes at a 4.9 req/s limiter against a measured 5 req/s per-API-ID ceiling. **A
separate A process is FAIL by construction: it would have 0.1 req/s to take.** One process serving
both cuts is PASS, because the cuts are ten minutes apart and the single process serialises them, so
no lane ever exceeds the rate it already runs at.

Costed schedule (unit of cost: E's measured 2,609 calls for 2,561 symbols in 266.7 s at 9.78 req/s):

| Variant | Union | A calls | A T0 | A T1 | slack to 09:25 | E's own refresh fits | tick-shard-last oldest stale | E SLA |
|---|---|---|---|---|---|---|---|---|
| A research | 3,847 | 3,919 | 09:15:00 | 09:21:41 | 199.3 s | **yes** (133.1 s) | 332.4 s | **preserved** |
| A live, safe prune | 4,915 | 5,007 | 09:15:00 | 09:23:32 | 88.0 s | no | **221.2 s** | **preserved** |
| A live, price prune | 4,567 | 4,653 | 09:15:00 | 09:22:56 | 124.3 s | no | **257.4 s** | **preserved** (tight) |
| A live, CS upper | 5,226 | 5,324 | 09:15:00 | 09:24:04 | 55.6 s | no | **188.8 s** | **preserved** |

E's finalization is untouched in every variant: T0 09:25:00, 2,609 calls, T1 09:29:26.7, inside both
its 09:29:45 deadline and A's 09:30 open.

Nothing can finish *before* 09:15: the 09:14 bar is complete only at 09:15:00, so A's T0 is 09:15:00
by the contract's own point-in-time rule.

**E's SLA is preserved by construction in every variant, by one of two zero-cost scheduling choices,
and the two cover opposite ends:**

* a small A universe finishes early and leaves room for E's existing 09:20:40 refresh of its tick
  shard (1,302 calls, 133.1 s) to run unchanged;
* a large A universe finishes late, so ordering A's pass to read **E's tick shard last** leaves those
  symbols touched in its final 133 s. Their stale window at E's cut is then 56-257 s, at or inside
  E's own measured 260 s window, so their tick-page cost cannot exceed the measured
  avg 1.037 / max 7 pages and no separate refresh is needed.

The rule is "take whichever of the two fits", and the previously unmeasured risk - tick pages at a
longer stale window, hard ceiling `MAX_TICK_PAGES = 30` - is removed rather than accepted.

## F. Provider parity, measured

The frozen K0 snapshot holds Kiwoom `pm_dollar_volume` over [04:00, 09:24] as `sum(volume x close)`.
The B-E0 cache carries no VWAP column either, so the Massive side of the identical window uses the
identical formula and the ratio between them is a source-content ratio, not a formula artifact.
**29,492 (symbol, session) pairs** matched. The parity sample is the **14 sessions
2026-08-26 .. 2026-09-15** on which the Kiwoom bootstrap had reached full universe depth
(>= 2,500 rows), with 1,586-1,870 paired symbols each.

**Assumption-free measurement** - the cross-sectional rank correlation of A's heaviest component:

| | |
|---|---|
| `pm_dollar_volume` Spearman, per session | 0.795 - 0.857, **mean 0.822** |
| Kiwoom / Massive ratio, per-session median | 0.445 - 0.670 |
| the same, p10 / p90 | 0.012 - 0.121 / 1.000 - 2.662 |
| pooled over all 29,492 pairs | median 0.491, p10 0.023, p90 1.116, min 6.3e-5, max 10,243 |

A direction note, because the repository contradicts itself: `E_RT3_RVOL_DENOMINATOR_V1.md` section 2
says *"Massive premarket volume is 0.49-0.68x Kiwoom's"* while the capability manifest records
`pm_volume_ratio_kiwoom_over_massive` median **0.669**. These point opposite ways. On 29,492 pairs
the ratio Kiwoom/Massive has median **0.491**, so **Kiwoom is the smaller of the two at the median**,
agreeing with the manifest and against the E-RT3 prose. The upper tail is real and fat, which is how
both readings came to be written down.

**Propagated through A V1.2, which is not modified.** A copy of the premarket panel is taken, the
named fields are multiplied by the measured per-(symbol, session) ratio, and the unmodified
`scan_session` and `actionability.select` run over it.

| Arm | eligible candidates | TOP35 overlap | actionable TOP8 overlap | discovery TOP8 overlap | score Spearman | identical TOP8 |
|---|---|---|---|---|---|---|
| `MASSIVE` (reference) | 1,056 | 1.000 | 1.000 | 1.000 | 1.000 | 14/14 |
| `K_DV` (dollar channel) | 852 | 0.874 | 0.901 | 0.821 | 0.967 | 8/14 |
| `K_RVOL` (volume channel) | 1,056 | 0.855 | 0.858 | 0.830 | 0.982 | 6/14 |
| **`K_BOTH`** | **852** | **0.578** | **0.618** | **0.652** | 0.946 | **1/14** |

Three things in that table matter more than the headline.

* **The channels compound rather than cancel.** Each alone keeps 86-90% of TOP8; together they keep
  62%. The same ratio scales the dollar component and the relative-volume numerator, so the two
  perturbations reinforce across 55% of the score weight.
* **The source change moves A's own eligibility**, not only its ranking: halving the dollar volume
  pushes 19% of candidates (1,056 -> 852) under A's `$50,000` premarket floor.
* **0.618 is an upper bound on the agreement.** The perturbation reaches only as far as the store
  does: 47-55% of each session's covered-eligible symbols have a measured ratio at all, and the mean
  share of a candidate's own 20 prior sessions that carry one runs from **5.2% (2026-08-26) to 37.6%
  (2026-09-15)**. The remaining priors kept ratio 1.0, so the denominator is only about a fifth
  Kiwoom-sourced. The true overlap is at most 0.618 and plausibly well below it.

That is consistent with, and independent of, the frozen K0 result on the same source pair: RVOL
Spearman 0.668, Jaccard 0.535 at the 3.0 threshold, and an identical E Top3 on 1 of 14 sessions.
A single 0.669 median ratio looks benign; what it does to a cross-sectionally normalised score is
the number above.

Not perturbed, and so not in the 0.618: gap, premarket high and low, and momentum. Kiwoom and
Massive agree on the premarket last price within 0.1% on 30 of 42 E-RT1 observations but on the high
on 27 and the **low on 18**, so the range and momentum channels would move too, in the same
direction as the measured effect.

## G. Decision

| Case | Condition | |
|---|---|---|
| 1 `A_LIVE_READY_FROM_SHARED_E` | parity sufficient **and** 20 sessions reconstructable from existing raw | parity 0.618 upper bound; 0 of 159 sessions reconstructable |
| **2 `A_LIVE_SHADOW_REQUIRED`** | collector shareable, baseline and parity need forward accumulation | **both hold** |
| 3 `BROAD_LIVE_PROVIDER_REQUIRED` | collector unshareable or semantics too far apart | collector is shareable and rate-safe |

**`A_LIVE_SHADOW_REQUIRED`.** The acquisition question is settled in the affirmative: one process can
serve both cuts, inside the rate ceiling, without touching E's measured latency, over a union that is
A's own universe plus four symbols. What is not settled is everything downstream of acquisition - the
baseline does not exist in any stored form, and a Kiwoom-sourced A V1.2 reproduces at most 62% of its
own TOP8. A shadow run is the only thing that can measure the second number properly, because it is
the only way to get both sources' features on the same sessions at full depth.

**`PAID PROVIDER NEEDED = NOT_YET`**, and for a narrower reason than last stage. Live acquisition is
not the blocker. But A's **daily** inputs - the previous regular close, the 20-session daily
baselines, the CS universe rule and the split calendar - have no Kiwoom equivalent, so the existing
Massive daily feed stays load-bearing whatever happens to the minute lane. The decision to buy a
broad *live* feed should be taken on the parity number after a shadow run, not before it.

Recommended order, as a statement of cost rather than a request: (1) one dry-run session with A's
pass inserted, to confirm the tick-shard-last ordering at a real stale window; (2) the 62-77 h
single-lane backfill of A's share-volume baseline, writing both A's and E's fields from the same
bars so the discarded-shares mistake is not repeated; (3) a shadow run measuring Massive-against-Kiwoom
TOP8 at full baseline depth.

## H. Confirmations

A score weights, pool size, actionability, GPT and entry changes: 0. E runtime changes: 0.
Production 0 · network 0 (`socket.connect` denied for the whole run) · PnL 0 · replay 0 · orders 0 ·
commit 0 · push 0 · deploy 0. `git diff` over `strategy_e_max_rt`, `strategy_e_max`, `market`,
`integrations`, `backtest/mover_scanner_v1`, `scanner`, `models` and `deploy` is empty. Targeted
regression: **114 passed** (`test_mover_scanner_v1`, `strategy_e_max/test_e_rt1_kiwoom`,
`test_real_market_scanner_stage10b`, `test_premarket_volume_v2`). The `MASSIVE` arm reproduces the
frozen V1.2 handoff on all 14 parity sessions.
