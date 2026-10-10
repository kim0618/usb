# US-B CRYPTO Q1/Q2/Q3 Forward Detector V1 - contract

    identity: q123-forward.v1
    status:   FROZEN BEFORE ANY FORWARD OUTCOME IS COMPUTED
    frozen:   2026-10-10 (Asia/Seoul)
    parents:  docs/crypto/market_context_r0/CONTRACT_MC_R0.md   (sha256 e05edec6a3c3...)
              docs/crypto/context_collector_v1/CONTRACT_CTX_V1_1.md (sha256 a9f8becfd18a...)
              docs/crypto/CRYPTO_D5_4_COST_FIRST_EVENT_CONTRACT_V1.md (cost, line 62)
    scope:    research and forward tracking only. No order, CLOSE, leverage, AUTO or account path is
              reachable. No LONG / SHORT / WAIT verdict is produced. The Context Collector, lm-wall.v2
              and lm-continuity.v5 are read, never changed.

Nothing in this document is a new rule. Every condition, bound, window, cost and gate below is the
one already frozen in a parent and is cited where it is used. What this document adds is the
**forward mapping**: which parent definition each question is, which data feeds it now that the
Context Collector runs continuously, and how an event is identified, gated and followed.

---

## 1. The questions are Market Context R0's T1 families, unchanged

Market Context R0 section 1 states Q1, Q2 and Q3 and section 9 scores them as the T1 families
(structure x wall x flow, W = 60 s). R0 could not measure T1 because Layer B was 3.61 hours long
(RESULTS_MC_R0). The Context Collector now writes Layer B continuously, so T1 is collected forward.

| Question | R0 family | Lean | Condition (R0 section 9, closed by section 17) |
|---|---|---|---|
| **Q1** resistance rejection | **F1** | SHORT | resistance approach; a V2 ask wall with `1.0 <= distance_bps <= 10.0` from `mid` at the anchor second; the wall still qualifies at `s+W`; 60s flow buy dominant (SIGN); `mid` at `s+W` has not cleared the level by `RT` |
| **Q2** resistance breakout continuation | **F2** | LONG | resistance approach; ask wall as above; the wall is gone at `s+W` **and** some sample in `(s, s+W]` has `mid >= bin_high` (consumption candidate); 60s flow buy dominant (SIGN); the +-0.1% ask notional below the bid notional at the anchor |
| **Q3A** support hold, LONG reversal | **F3** | LONG | mirror of F1 at support with the bid wall and sell dominance |
| **Q3B** support breakdown, SHORT continuation | **F4** | SHORT | mirror of F2 at support |

Q3 is split into Q3A / Q3B because R0 already declares it as two families (F3, F4); nothing is split
that R0 did not split. The implementation is `market_context_r0.families.joint_family`, imported and
called unchanged.

**Consumption.** A wall that is gone at `s+W` without `mid` reaching its bin is CANCELLED and never
satisfies Q2 / Q3B (R0 section 9). The wall selection is the full `lm-wall.v2` set as R0 reads it
(`wall_r0`, PRIMARY role, CONTRACT_CTX_V1_1 section 4), not the panel's display list, so a display
rank eviction (`RANK_EVICTED`) is not a disappearance here at all. `CANCEL_LIKE`, `OUT_OF_COVERAGE`
and `UNKNOWN` are never read as consumption. "Consumed" is a candidate label only: `btc-ms.v0.1`
does not prove order identity.

**Flow dominance** is SIGN only (R0 section 17.11: the journal has no held out stretch for DOM): the
60s window published at the decision second has `imbalance_usdt > 0` for buy, `< 0` for sell.

**ABSORPTION_CANDIDATE** is never a trigger. It is stored as a diagnostic column only.

## 2. Inputs

| Layer | Source | Use |
|---|---|---|
| A, PRICE / structure | Binance USD-M BTCUSDT 1m klines, public `/fapi/v1/klines`, fetched read only | R1 engine via `market_context_r0.structure.ContextEngine`, thetas 3.0 / 6.0 (R0 frozen) |
| B, LIQUIDITY | Context Collector V1.1 journal: `context` records and `wall_r0` rows, merged by `reader.seconds()` | `walls_primary` = R0's V2 wall set (parity proven 5,516 / 5,516) |
| C, FLOW | the same `context` records, `flow.windows.60s` | dominance and its coverage |
| Diagnostics only | `flow.absorption.state`, UTC session bucket, `atr_5m` | never a condition |

OI, funding, basis and C1 are **not collected by the Context Collector** and are not joined in V1.
They are listed as `NOT_COLLECTED` rather than filled.

The second frame is built with the adapter already proven against R0 (CONTEXT_COLLECTOR_V1_1_REPORT
section K): `ok = book.state == SYNCED and depth[0.1].bid_coverage == COMPLETE and mid present`;
`band_bid / band_ask` = the 0.1% depth notionals when their coverage is COMPLETE, else absent.

## 3. Structure series anchoring

The R1 engine is a single pass over a fixed series. Market Context V1 section 5 fixes the anchor rule
(30 days before process start, floored to a UTC day). The Context Collector production session
started 2026-10-10T12:47Z, so:

    SERIES_START = 2026-09-10T00:00:00Z   (warmup, never published, no event can anchor in it)
    PUBLISH_FROM = 2026-10-10T00:00:00Z

Both are constants. The series end is the last **closed** minute available at run time. R1's prefix
truncation invariance (R1 C2, R0 section 11 C2) is what makes a later run's published minutes
identical to an earlier run's; it is re-tested here (section 9).

## 4. Episodes and identity

* **Structural approach** (R0 section 8): minute `t` with `0 <= res_distance_pct <= 0.25`
  (resistance) or `-0.25 <= sup_distance_pct <= 0` (support) in the R1 snapshot published at the
  close of `t`.
* **Overlap control** (R0 sections 8, 17.10): per `(level_id, side)` the first qualifying minute
  opens an episode and every qualifying minute in the next 60 minutes is skipped.
* **Anchor second** (R0 `joint_family`): the first collector sample at or after the minute close,
  no more than 5 s after it. **Decision second**: the last sample at or before anchor + 60 s, in the
  same collector session, no more than 5 s older than that target (R0 section 17.5).
* **event_id** = first 16 hex of sha256 over
  `q123-forward.v1|<question>|<side>|<level_id>|<level_price>|<anchor_ts>|<wall_bin_low>|<session_id>`.
  The same inputs always give the same id.

One structural episode is one record per question of its side (RES -> Q1 and Q2, SUP -> Q3A and
Q3B). A question **fires** on that episode or it does not; both are kept, because the non-firing
episodes are the complement every edge is measured against (R0 section 17.6).

## 5. Quality gate

An episode is **admitted** only when R0 section 17.4 holds: at the anchor `book SYNCED`, the +-0.1%
band COMPLETE and `mid` present; at the decision second the same plus the 60s flow window COMPLETE.
Otherwise it is recorded with the R0 reason, one of:

`OUTSIDE_JOURNAL`, `NO_SAMPLE_AT_MINUTE`, `ANCHOR_NOT_OK`, `NO_WALL_IN_BAND`,
`NO_DECISION_SAMPLE`, `DECISION_NOT_OK`, `FLOW_NOT_COMPLETE`.

One reason is added from CONTRACT_CTX_V1_1 section 2, where a resnapshot generation change makes a
disappearance `UNKNOWN`: **`GENERATION_CHANGED_IN_W`** when the book generation at the decision
second differs from the anchor's. Such an episode is excluded from every question of its side,
firing or not. It is counted separately so the R0-literal count can always be recovered.

No missing value is replaced by zero or by a neutral value.

## 6. Outcomes

Measured from `P0 = mid` at the decision second over **5, 15, 30 and 60 minutes**, on the 1 s `mid`
path of the same collector session, using R0's `second_outcome` unchanged (last sample at or before
the target, at most 5 s older; a horizon that crosses a session end is `MISSING`, never estimated).
R0 section 10 pre-registers 5 / 15 / 60 for T1; **30 is added here and is diagnostic only**.

Per horizon: `p0`, `close_h`, `raw_move_bp` (signed, price up positive), `directional_move_bp`
(positive in the question's lean), `mfe_bp` / `mae_bp` (in the lean: SHORT -> MFE is the downside
excursion, MAE the upside), `mfe_up_bp`, `mfe_dn_bp`, `est_fee_bp = 11.0`, `est_slippage_bp = 0.25`,
`net_move_bp = directional_move_bp - 11.25`, `reachable_bp`, `first_meaningful_direction`
(22.5 bp), `reversal`, `breakout_continuation`.

Cost (D5_4 contract line 62): round trip taker **11.25 bp = fee 11.0 + spread / funding 0.25**.
No other cost figure is used.

## 7. Journal

Append-only JSONL under `data/research/crypto/q123_forward_v1/`, separate from every trading and
collector path. `episodes.jsonl` holds one record per (episode, question); `outcomes.jsonl` holds one
record per (event_id, horizon) once that horizon is measurable. A record, once written, is never
rewritten; a rerun appends only ids and horizons not already present, and refuses if a recomputed
record for an existing id differs from the stored one (determinism check).

## 8. Statistics and gates

Per question and horizon, over admitted episodes with a measured outcome: firing `n`, win rate
(`net_move_bp > 0`), mean / median / p25 / p75 `net_move_bp`, median MFE and MAE, worst decile,
top 1% contribution to the sum, and the R0 tests unchanged: circular shift p vs the complement
(1,000 shifts, seed 20261005) and the bootstrap 95% CI of the mean difference (10,000 draws).

Multiplicity: Holm-Bonferroni over the grid of 4 questions x 3 pre-registered horizons (5, 15,
60). The 30 minute horizon is reported and never enters the grid. **No horizon is selected after
results are seen; there is no primary horizon.**

Sample stage per question, on firing events with a 60 minute outcome:

| n | Stage |
|---|---|
| < 20 | `INSUFFICIENT` |
| 20 - 49 | `EARLY_SIGNAL_ONLY` |
| 50 - 99 | `ELIGIBLE_FOR_SIGNAL_CONTRACT_REVIEW` |
| >= 100 | `STRONGER_EVIDENCE_AVAILABLE` |

R0's own measurability floor (30, section 12) still applies to any test. A stage is a count, never
a pass: no stage authorises a direction, and the R0 section 14 conditions (lean sign, Holm p <= 0.05,
own mean `net_move_bp` > 0) are the minimum for any later signal-contract review.

## 9. Determinism and forward-only proof

* The detector reads, for an episode anchored at minute `t`, only R1 rows published at or before
  `t`'s close and collector samples at or before its decision second.
* **Prefix invariance**: running on inputs truncated at a cut `T` (journal samples `<= T`, klines
  closed `<= T`) must reproduce every episode record whose decision second is `<= T` byte for byte,
  apart from outcomes, which are allowed to be `PENDING` in the truncated run.
* Two runs on the same inputs produce byte identical journals.

## 10. Prohibited

No threshold, bound, window, horizon of the test grid, cost or family is changed after a result is
seen. No absorption, OI, funding, basis or C1 field enters a condition. No output is labelled LONG /
SHORT / WAIT; a firing question is a `RESEARCH / UNVALIDATED` candidate.
