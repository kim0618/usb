# US-B CRYPTO Market Context R0, Contract

Status: **FROZEN BEFORE ANY OUTCOME IS COMPUTED**
Frozen at: 2026-10-05 (Asia/Seoul)
Parents: `docs/crypto/price_structure_r1/CONTRACT_R1.md` (`68eca59e2c5b…`),
`docs/crypto/market_structure_v0/DATA_CONTRACT_V0.md` (`btc-ms.v0.1`, `9eed3862…`),
`docs/crypto/liquidity_map_v1/WALL_RULE_V2.md` (`lm-wall.v2`, `deaa9db8…`)

Scope: research and an isolated preview only. AUTO, PAPER, LIVE, the order paths, the production
deploy path and the live Liquidity Map screen are out of scope and unchanged. Nothing here is
committed, pushed or deployed. C1 / C1x and Directional Probability are not in this model.

## 1. Question

Price Structure R0, R1 and R2 ended at `A_NOT_USABLE` for one reason and one reason only: major
reversal coverage (M4) is capped by the swing source set, and R2 proved the cap is a property of the
sources rather than of the curation. The mechanism itself passed every test it was given: repaint 0,
prefix truncation identical, retest liveness 1.0.

So the structure is not a signal. The question here is whether it is a **position**: does conditioning
on where price sits inside the R1 structure change what Liquidity and Trade Flow mean?

Concretely, and these are the task's four questions restated so they can be scored:

* **Q1** Near structural resistance, do a holding or replenishing ask wall plus aggressive buying
  that fails to move price forward precede a fall?
* **Q2** Near structural resistance, do ask wall consumption plus buy aggression plus thinner
  liquidity above precede continuation?
* **Q3** The same two questions at support, symmetrically.
* **Q4** Where there is no structural level at all, do Liquidity and Flow alone separate a
  reversal state from a breakout state?

## 2. What is carried over unchanged, by reference

Not re-derived, not re-tuned, not re-implemented. Enforced by import, not by assertion.

| Carried | How |
|---|---|
| Layer A level book, lifecycle, retest pool, publication cap, curation rules | imports `price_structure_r1.engine`, `.levels`, `.events` |
| Swings, HH/HL/LH/LL, `trend_structure`, range geometry, session VWAP | imports `price_structure_r0.swings`, `.vwap` |
| Bars, ATR, higher timeframe folding, "a bar is known at its close" | imports `price_structure_r0.data` |
| Swing thresholds `theta_1h = 3.0`, `theta_5m = 6.0` | frozen constants, R0's held out warmup calibration. **Not recalibrated.** Section 11 reports whether the same held out procedure on the Binance warmup window would have chosen the same pair; the frozen pair is used either way |
| Wall selection | imports `liquidity_map.wallrule`, `lm-wall.v2`, hash checked at run time |
| Book band and flow window coverage semantics, including the permanently PARTIAL bands | imports `market_structure_v0.contract` vocabulary; read from the journal as published |
| Round trip taker cost **11.25 bp** and the meaningful move floor **22.5 bp** | `CRYPTO_D5_4_COST_FIRST_EVENT_CONTRACT_V1` section, `CRYPTO_D5_5` K5. Binance's own taker schedule is slightly cheaper, so reusing the project figure is the conservative choice |
| Minimum event count for a measurable family, **30** | the liquidation forward gate, `CRYPTO_D5_3_LIQUIDATION_FORWARD_CONTRACT_V1` |

**No new Price Structure source is added.** R2's S1 (previous session high and low) is carried as a
context field that never enters the level book, which is the only use the task permits.

## 3. One venue, and why

Layers B and C exist as exactly one artefact: the `binance_usdm` Market Structure V0 journal. Price
Structure R0 / R1 / R2 ran on Bybit. Joining a Bybit level book to a Binance order book would place a
cross venue basis inside a proximity tolerance that is itself 0.25 percent wide.

Measured on the 76,320 overlapping minutes: median close difference 1.40 USDT (0.0051 percent of
price), p95 12.3 USDT (0.0156 percent), **max 622.9 USDT (0.894 percent)**. The tail exceeds the
tolerance, so the cross venue join is rejected and the structure layer is recomputed on the Binance
series. Every layer in this study then reads one venue.

This is a venue transfer, not a change of method: the same code, the same thresholds, the same
window. Section 11 reports the transfer check.

## 4. Data, frozen

| Id | What | Bound |
|---|---|---|
| D-A | Binance USD-M BTCUSDT 1m klines, `/fapi/v1/klines`, read only | 2026-08-12T00:00Z to 2026-10-04T08:00Z exclusive, 76,800 rows, sha256 `fa2deba911a825a9ee6b1ded7cccbe73e4a47f23f5fce0481ffcc4ab03436fdb` |
| D-B | Market Structure V0 journal, roots `data/runtime/ms_v0_preview` and `data/runtime/ms_v0_v11`, 5 sessions, contract `btc-ms.v0.1` | 2026-10-04T03:57:43Z to 07:34:16Z, 12,779 one second samples, span 12,992 s |
| D-C-long | Aggressive side split per minute, `taker_buy_base` / `taker_buy_quote`, inside D-A | same as D-A |
| D-C-short | Flow windows 5s / 15s / 60s as the collector published them, inside D-B | same as D-B |

Warmup 2026-08-12T00:00Z to 2026-09-01T00:00Z is held out: it carries the theta transfer check and
the one flow quantile of section 7, and no outcome is ever read from it. Replay is
2026-09-01T00:00Z to 2026-10-04T08:00Z.

## 5. Tracks, declared before any outcome exists

The Layer B ceiling is a fact of the archive and is stated here, not discovered later: **Layer B is
3.61 hours long.** No order book history for BTCUSDT futures at level resolution exists outside this
journal, and no public archive publishes one. The study is therefore split into three tracks, and
each track is scored on its own evidence.

| Track | Layers | Window | Answers |
|---|---|---|---|
| **T1** | A x B x C | D-B, 3.61 h | Q1, Q2, Q3 as literally asked |
| **T2** | B x C, no structural conditioning | D-B, 3.61 h | Q4, and the wall mechanics of Q1 / Q2 stripped of the structural condition |
| **T3** | A x C at 1m | D-A replay, 33.3 d | the structure conditioned flow half of Q1 / Q2 / Q3, with no wall |

T1 and T2 share the same seconds; T3 contains them. The point of the split is that T2 and T3 can
each be measurable while T1 is not, and a negative T2 together with a negative T3 is evidence about
the joint model that T1 cannot supply on its own.

## 6. PIT safety

1. Layer A fields at minute `t` come from the R1 snapshot whose `info_ts` is the close of `t`.
   Prefix truncation proved this is reproducible from closed bars only, and section 11 re-runs that
   proof on the Binance series.
2. Layer B and C fields at second `s` come from the `derived` record written at `s`, merged with the
   `wall` rows whose `seq` is below that record's `seq`. No later row can reach a sample.
3. A family whose condition spans a window `[s, s + W]` has **decision time `s + W`**, and its
   outcome is measured strictly after `s + W`. A condition that reads the future is allowed only by
   paying for it with that delay.
4. The previous session high and low of minute `t` are the previous **completed** UTC day's extremes.
5. Flow windows are trailing. The 3 minute Layer C feature at minute `t` uses minutes `t-2 .. t`.

## 7. Features, by layer

**Layer A** (per minute, from the R1 snapshot): `trend_structure`, `last_high_label`,
`last_low_label`, `range_state`, `range_position`, nearest support and nearest resistance each with
price, `distance_pct`, lifecycle, strength, touches, `confirm_tf`, sources and origin,
`retest_state`, `failed_break_state`, last breakout level / direction / age, last sweep direction /
age, `vwap_z`, `vwap_position`, `active_level_count`, `atr_5m`, `atr_1h`, previous session high and
low with their distances.

**Layer B** (per second, from `derived` plus the V2 wall selection): `mid`, `best_bid`, `best_ask`,
book `state` / `fresh` / `age_ms` / `generation`, the +-0.1 percent band's bid and ask notional with
its coverage and imbalance, `known_low` / `known_high`, and for each side the ordered V2 walls with
price, notional, multiple, `distance_bps`, `observed_persistence_ms`, bin and coverage.
Bands wider than +-0.1 percent are read as **lower bounds** and are never used as a quantity.

**Layer C** short (per second): the 5s / 15s / 60s flow windows with their coverage, published
imbalance and observed lower bounds.
**Layer C** long (per minute): `flow_imb_1m = (buy - sell) / (buy + sell)` on taker base volume, and
`flow_imb_3m`, the same ratio over the trailing three minutes summed before dividing.

### The one quantile

`BUY_DOM` and `SELL_DOM` in T3 need a notion of "dominant" that is not invented. It is the **p80 of
`|flow_imb_3m|` over the held out warmup window**, computed once, frozen, and reported in the
results. T2 has no held out stretch that would leave the replay intact, so T2 uses sign only.

Every family is therefore reported in two pre-registered variants and no others:

* **SIGN**: dominance means the imbalance has the hypothesised sign.
* **DOM**: dominance means it also clears the frozen warmup quantile. T2 has no DOM variant.

## 8. Episodes

**T1 / T3 structural approach.** Minute `t` is an approach instant to the nearest resistance when
`res_price` exists and `0 <= res_distance_pct <= 0.25`; symmetric at support. The 0.25 percent is
R0's own M4 tolerance, reused unchanged.

**T2 wall approach.** Second `s` is an ask wall approach instant when a V2 ask wall exists with
`1.0 <= distance_bps <= 10.0`; symmetric on the bid. The lower bound is the V2 rule's own R3; the
upper bound is the +-0.1 percent boundary, the only band the `limit=1000` snapshot covers COMPLETE.
Nothing beyond it is used, because beyond it "thin" and "unobserved" are the same number.

**Overlap control.** Episodes of the same `(level_id, side)` in T1 / T3 must be at least 60 minutes
apart; episodes of the same `(bin_low, side)` in T2 at least 300 seconds apart. The first qualifying
instant after the required silence is the episode. Residual dependence between episodes of
*different* levels is not removed and is stated as a limitation.

## 9. Condition families

Finite, pre-registered, and no family is added or removed after a result is seen. `W` is the
condition window and the decision time is the end of it. `RT = 11.25 bp`.

### T1, structure x wall x flow, W = 60 s

| Id | Lean | Condition at a structural approach instant |
|---|---|---|
| F1 | SHORT | resistance approach, a V2 ask wall within 10 bp, the wall still qualifies at `s+W`, flow buy dominant, and `mid` has not cleared the level by `RT` |
| F2 | LONG | resistance approach, a V2 ask wall within 10 bp, the wall is gone at `s+W` and `mid` traded through its bin, flow buy dominant, ask side +-0.1 percent notional below bid side |
| F3 | LONG | support approach, mirror of F1 |
| F4 | SHORT | support approach, mirror of F2 |

### T2, wall x flow, no structure, W = 60 s

| Id | Lean | Condition at a wall approach instant |
|---|---|---|
| G1 | SHORT | ask wall held through `W`, 60s flow buy dominant |
| G2 | LONG | ask wall gone at `W` and `mid` traded through its bin, 60s flow buy dominant |
| G3 | SHORT | ask wall absent at some second inside `W` and qualifying again at `W` (replenished) |
| G4 | LONG | bid wall held through `W`, 60s flow sell dominant |
| G5 | SHORT | bid wall gone at `W` and `mid` traded through its bin, 60s flow sell dominant |
| G6 | LONG | bid wall replenished inside `W` |

A wall that is gone without price reaching its bin is **CANCELLED**, not consumed, and is excluded
from G2 / G5. The V0 contract cannot prove order identity, so consumption is a candidate label and
is named as one.

### T3, structure x flow, no wall, W = 0

| Id | Lean | Condition at a structural approach instant |
|---|---|---|
| H1 | SHORT | resistance approach, `flow_imb_3m` buy dominant, close has not cleared the level by `RT`. Aggression without progress |
| H2 | LONG | resistance approach, close has cleared the level by `RT` this minute, `flow_imb_3m` buy dominant. Consumption |
| H3 | LONG | support approach, mirror of H1 |
| H4 | SHORT | support approach, mirror of H2 |
| H5 | SHORT | resistance approach, `flow_imb_3m` buy dominant, close not cleared, **and** `retest_state` is one of `RETEST_TOUCHED` / `RETEST_FAILED`, or `failed_break_state` is active. H1 with the structure's own failure memory |
| H6 | LONG | support mirror of H5 |

### Baselines

Each family is compared against the **approach baseline**: every episode of the same side and track
with the flow and wall conditions dropped. The **unconditional baseline**, every minute of T3 and
every second of T2, is also reported. A family's edge is always stated against the approach
baseline, because the approach itself is a selection and the question is what the context adds.

## 10. Outcomes

Measured from `P0`, the decision time's `close` in T3 and `mid` in T2, over pre-registered horizons
**5 / 15 / 60 minutes** in T1 and T3 and **60 / 300 / 900 seconds** in T2.

| Outcome | Definition |
|---|---|
| `mfe_up_bp`, `mfe_dn_bp` | the best excursion each way inside the horizon, `(high - P0)/P0` and `(P0 - low)/P0` in bp |
| `mae_bp` | the adverse excursion for the family's lean, which is `mfe_dn_bp` for LONG and `mfe_up_bp` for SHORT |
| `net_move_bp` | the directional close to close move for the lean, minus `RT` |
| `reachable_bp` | `max(0, directional MFE - RT)`, the best cost adjusted move that was actually reachable |
| `first_meaningful_direction` | the sign of the first excursion to exceed `22.5 bp` from `P0`; `NONE` if neither side does; **`AMBIGUOUS`** when a single 1m bar breaches both, which is not resolved by guessing |
| `breakout_continuation` | for a consumption family, whether the close at the horizon is beyond the level by more than `RT` |
| `reversal` | for a hold family, whether the close at the horizon is on the far side of the level by more than `RT` |

No score between 0 and 100 is constructed. No probability is calibrated. No outcome is converted
into a rank.

## 11. Mechanism checks, run before the families

| Check | Bound |
|---|---|
| C1 repaint on the Binance series | 0 violations, 0 vanished levels |
| C2 prefix truncation at R1's 6 cut points | the engine fed only closed bars reproduces the full run's snapshot at `T`, all fields |
| C3 theta transfer | the held out procedure on the Binance warmup window reports its choice; the frozen pair is used regardless |
| C4 curation transfer | active level median and p95 are reported against R1's 9 and 13 |
| C5 wall rule identity | `lm-wall.v2` hashes to `deaa9db8…` at run time |
| C6 journal wholeness | every resting wall a sample sees was OPENED inside the records already read, because the replay starts at the session's first record |
| C7 venue basis | section 3's figures |

A failed C1, C2 or C5 stops the study. C3, C4 and C7 are reported.

## 12. Sample sufficiency

A family is **MEASURED** when it has at least **30** episodes after overlap control, and
**NOT_MEASURABLE** otherwise. A NOT_MEASURABLE family is reported with its count and is excluded
from the verdict. The verdict text must name every research question that was left unmeasured.

The 30 is the liquidation forward gate, written here before any episode was counted.

## 13. Significance

The primary test is a **circular shift null**: the family indicator is rotated against the outcome
series by 1,000 random offsets, seed `20261005`, and the two sided p-value is the share of shifts
whose mean difference against the approach baseline is at least as extreme as the observed one.
Rotation is used rather than a plain shuffle because both series are autocorrelated.

A bootstrap 95 percent confidence interval on the difference in mean `net_move_bp`, 10,000
resamples, same seed, is reported alongside.

**Multiplicity.** The full grid of MEASURED `(family x variant x horizon)` tests is corrected with
Holm-Bonferroni at family wise 5 percent. The corrected result decides C; the uncorrected one may
support B.

## 14. Verdict

Deterministic, in this order.

* **C, USABLE_FOR_FORWARD_CONTEXT** when at least one MEASURED family at at least one horizon has,
  simultaneously: the sign of its mean `net_move_bp` difference matching its declared lean, a
  Holm corrected circular shift p of 0.05 or less, and its own mean `net_move_bp` above 0, meaning
  the state pays for its own round trip.
* **B, WEAK_CONTEXT_EDGE** when at least one MEASURED family matches its lean and either clears the
  uncorrected p but not its own cost, or clears its own cost but not the uncorrected p.
* **A, NO_CONTEXT_EDGE** otherwise.

C does not authorise AUTO. The next step after C is Manual / PAPER forward context and nothing else.

A verdict of A on T2 and T3 with T1 NOT_MEASURABLE is reported as exactly that, and the report must
say which of the two it is: the model found nothing, or the archive could not ask.

## 15. State mapping

For the preview only, and it is a label, not a recommendation.

`UNKNOWN` when the book is not SYNCED, the +-0.1 percent band is not COMPLETE, or the flow window
driving the state is not COMPLETE. Otherwise `CONFLICT` when a LONG lean family and a SHORT lean
family both fire at the same instant, then the single firing family's lean as `LONG_LEANING` or
`SHORT_LEANING`, and `NEUTRAL` when none fires.

## 16. Prohibited after results exist

No threshold is moved. No source is added. No family is added, dropped or merged. No horizon is
added. No variant beyond SIGN and DOM is introduced. Attribution of a measured result is allowed and
must be labelled as not feeding the verdict.

## 17. Gaps closed during implementation, before any outcome was computed

The first freeze of this document is sha256
`2f529a48d8ec738f3d263f2b24fedaeb521ad8456608fe79f84175f23820e37c`. Writing the code against it
exposed eleven places where the text admitted more than one implementation, or none. Every one of
them is closed here, and this revision was written before a single forward outcome was computed.
No bound moves, no family is added or dropped, and no threshold changes value.

What had already been measured when this section was written, and is disclosed for that reason:
the data census of section 4, the mechanism checks C1, C3, C4 and C7 of section 11, the journal
census of section 5, and a count of how many minutes inside the journal window sit within 0.25
percent of a published level. All of those are properties of the data, not of an outcome.

1. **H2 and H4 could not fire as written.** An approach instant requires the close on the near side
   of the level, so "close has cleared the level by `RT` this minute" is self contradictory at the
   same minute. Closed: the anchor stays the approach minute, the decision minute is the **next**
   one, and the clearing test reads the decision minute's close. Section 9's "W = 0" for T3 is
   therefore `W` in {0, 1 minute}, with 1 minute for H2 and H4 only.
2. **"`mid` traded through its bin"** means: some sample in `(s, s+W]` has `mid` at or above
   `bin_high` for an ask wall, or at or below `bin_low` for a bid wall.
3. **Which flow window is "dominant" in T1 and T2**: the 60s window published at the decision
   second, which is exactly the condition window `[s, s+W]`.
4. **Episode admission.** An episode requires, at the anchor, `book.state == SYNCED`, the +-0.1
   percent band COMPLETE and `mid` present; and at the decision second the same plus the 60s flow
   window COMPLETE. An episode failing any of these is excluded by named reason from **both** the
   family and its baseline, so the contrast is always computed on one set.
5. **T2 horizon lookup**: the last sample at or before `t_dec + delta`, in the same session, and no
   more than 5 s older than the target. Otherwise that horizon is missing for that episode and the
   episode is dropped from that horizon's test only.
6. **The contrast is against the complement.** A family is compared with the same side episodes of
   the same track where it did **not** fire. `edge_vs_all_approach` is reported alongside, but the
   rotation null needs two groups and the complement is the one that keeps the episode set fixed.
7. **The rotation unit** is the position in the family's own ordered episode list, not clock time.
8. **T1 wall proximity is measured from `mid`**, which is section 8's wall approach definition, not
   from the structural level. The wall to level distance is recorded as a diagnostic.
9. **"`failed_break_state` is active"** in H5 and H6 means any value other than `NONE`.
10. **Overlap control**: the first qualifying instant becomes the episode and every qualifying
    instant inside the silence window of that episode is skipped.
11. **T1 has no DOM variant.** Section 7 withholds DOM from T2 because the journal has no held out
    stretch. T1's Layer C is the same journal window, so the same reason applies and T1 is SIGN
    only. T3 carries both variants.
