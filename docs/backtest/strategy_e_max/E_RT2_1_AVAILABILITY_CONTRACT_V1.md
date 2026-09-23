# E-RT2.1 — MARKET_DATA_UNAVAILABLE Contract (E-MAX V1 realtime)

Status: **CONTRACT DEFINED AND WIRED**. Session: 2026-09-23. Frozen strategy rules unchanged
(`strategy_e_max_v1_rules.json`, canonical sha256 `b30a3e95…`). No rule, threshold, universe or
denominator was modified by this stage.

## 1. Why this stage exists

The E-RT2 live dry run (2026-09-22, server `traderj`, single Kiwoom app key) finalized 2,560 of the
2,561 canonical symbols by 09:29:26.698 ET, with 0 cutoff violations and 0 rate-limit rejections.
One symbol, **PS**, is present in Kiwoom's canonical listing (`usa10099`) but is refused by both
chart lanes (`usa06011` minute, `usa06010` tick) on ND, NY and NA, reproducibly on two sessions.

E-RT2 had no state for "the source will not serve this symbol", so it recorded PS as `STALE`, the
same label a slow or broken fetch gets. Those are different facts and they must not share a label.

Two separate verdicts follow, and this document keeps them apart:

* **E-RT2 CAPACITY RESULT: SINGLE-APPKEY THROUGHPUT SUFFICIENT** — the measured question (can one
  app key finalize the full canonical universe before 09:30?) is answered yes, with 33.3 s of margin.
* **E-RT2 FORMAL GATE: BLOCKED DUE TO 1 SOURCE-UNAVAILABLE SYMBOL** — the gate as written required
  every canonical symbol to reach a cutoff state. One symbol cannot, for a reason no amount of
  throughput fixes. That is a contract question, resolved here, not a capacity failure.

The frozen E-RT2 result artifacts are not modified by this document.

## 2. States

Per symbol, at the 09:25 cutoff (`app/strategy_e_max_rt/availability.py`):

| state | meaning | H5 | can be selected |
|---|---|---|---|
| `FEATURE_COMPLETE` | served, premarket prints exist | evaluated normally | yes |
| `SPARSE_NO_PREMARKET` | served, simply did not print premarket | `H5_FALSE` (never a candidate) | yes, and it never is |
| `MARKET_DATA_UNAVAILABLE` | every lane refused the symbol (`MARKET_DATA_UNAVAILABLE` / `INVALID_SYMBOL`) | `H5_UNKNOWN` | no |
| `STALE` | transport failure, non-contiguous, or finalized after the deadline | `H5_UNKNOWN` | no |

A refusal is decided by the **source's own answer on every lane**, not by an empty result. A
transport failure (`PROVIDER_TIMEOUT`, network error) is `STALE`, not unavailable.

## 3. Rules

1. **Never silently H5 = False.** An unavailable or stale symbol is `H5_UNKNOWN`. It is not a
   negative signal answer; it is the absence of one.
2. **Per-symbol fail-closed.** An unavailable symbol cannot enter the candidate list, the R1 order
   or the selection. The session is not blocked by it; every other symbol decides normally.
3. **No backfill.** Nothing is substituted for the missing symbol and **no other candidate is
   promoted in its place**. The frozen rule takes the first three of the R1 order over the H5
   candidates; a symbol that never became a candidate is simply absent, and the selection is
   whatever the remaining candidates produce — never "the next one up" to refill a slot.
4. **The canonical universe is unchanged.** The unavailable symbol stays in the universe list, is
   counted, and is named in the session record.
5. **The B2 denominator is unchanged.** Breadth divides by the frozen decision seal's eligible-row
   count (`SignalResult.eligible_count`); nothing here adds to or removes from it. Verified in the
   live run: with every RVOL NaN, the seal still reported 854 eligible rows.
6. **Sparse is not unavailable.** Both live-run examples (AAMI, BH.A — 251 such symbols on
   2026-09-22) were served and simply had no premarket prints; they stay executable and non-candidate.
7. **No tolerated share is declared here.** The count and the share are recorded next to the
   decision so a later stage can judge them with evidence.

## 4. What was wired

* `app/strategy_e_max_rt/availability.py` — the states, `classify`, `h5_status`, `executable`,
  `diagnostics` (recorded counts), `outcome_from_cache` (live finalizer caches) and `reclassify`
  (re-reading an existing E-RT2 status row).
* `app/dev/run_e_rt2_dryrun.py` — the runtime path now classifies through this contract instead of
  its own three-way test, marks rows whose H5 cannot be answered as `H5_UNKNOWN`, and writes an
  `availability.json` artifact next to the run.

Re-reading the 2026-09-22 run under this contract: 2,309 `FEATURE_COMPLETE`, 251
`SPARSE_NO_PREMARKET`, **1 `MARKET_DATA_UNAVAILABLE` (PS)**, 0 `STALE`.

## 5. Tests

`backend/tests/strategy_e_max/test_e_rt2_1_rt3.py`: unavailable is neither sparse nor stale; H5 is
UNKNOWN and never a silent False; nothing backfills; the canonical denominator is the seal's count;
a PS fixture beside a normal symbol; a session with an unavailable symbol still decides; the
recorded E-RT2 rows re-read correctly; a finalizer cache maps to the right state.
