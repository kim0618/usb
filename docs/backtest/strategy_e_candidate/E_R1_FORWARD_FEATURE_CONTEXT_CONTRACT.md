# Strategy E - E-R1 Forward Feature Context Contract

| | |
|---|---|
| Machine authority | `strategy_e_forward_feature_context_v1.json`, canonical `78bb90b4…3fad` |
| Gate code | `app.strategy_e_v1_1.context.require_complete` (no fetching, no loading) |
| Trading rules | `strategy_e_trading_v1_1_rules.json` |
| Status | PREREGISTERED; the forward feature builder itself is **not** built in E-R1 |

The E-F0 audit found that `run_strategy_e1_forward._features_for` computes only premarket-only
columns. It has no daily join, no rolling history and no SPY close. As a result `premarket_gap`,
`premarket_rvol`, `close_price` and five other sealed columns came out NaN, and H5 was always
empty. This contract defines what the next builder must supply, as of when, and what happens
when something is missing. The existing forward package is not modified.

## 1. Principles

- **Daily:** information available no later than the close of D-1.
- **Minute:** bars starting at or before 09:24 ET of D, plus complete earlier sessions.
- **Chronology:** each forward session adds only D-1 to the daily context. Reading future daily
  rows in bulk is forbidden.
- **Two distinct refusals:**
  - `FutureContextViolation`: an input dated after its cutoff. The session is not valid forward
    evidence.
  - `FEATURE_CONTEXT_INCOMPLETE`: a required input was never collected. No decision is made.
    Nothing is filled, defaulted or taken from a later date, and no H5 flag is turned into
    False on the builder's behalf.
- **Research-undefined is not incomplete.** A collected source can legitimately yield NaN: RVOL
  with fewer than 5 qualifying prior sessions, fewer than 2 bars in a return window, a zero
  premarket range, or no SPY premarket print. The row is then excluded by Research's mask, exactly
  as in E1.

## 2. Per-feature context

| feature / input | source | as of | session dependency | lookback | when missing |
|---|---|---|---|---|---|
| close(D-1) | grouped daily `c`, adjusted=false | close D-1 | D-1 | 1 | D-1 file absent: INCOMPLETE; symbol absent from a present file: not eligible (E0) |
| close(D-2) > 0 | grouped daily | close D-2 | D-2 | 1 | not eligible (E0 `prev_price`) |
| CS membership | CS reference snapshot (historical_v2 reference infra) | latest <= D-1 | <= D-1 | latest | INCOMPLETE; a snapshot dated after D-1 is a FutureContextViolation |
| median dollar volume | grouped daily close x volume | close D-2 | [D-21, D-2] | 20 sessions, >= 15 present | missing file in window: INCOMPLETE; symbol absent: not present (E0) |
| split exclusion | split list (execution_date, from, to) | LIVE: published <= 09:25 D; RECONSTRUCTED: later list, only execution_date <= D read | execution in (D-1, D] | calendar (D-1, D] | INCOMPLETE unless the list covers executions through D |
| previous_day_dollar_volume | grouped daily | close D-1 | D-1 | 1 | as close(D-1) |
| premarket block (bars, prices, $vol, windows) | 1-minute aggregates, adjusted=false | 09:25 D | bars in [04:00, 09:24] of D | D | page not fetched: INCOMPLETE; fetched page, no premarket bar: no row (E1) |
| premarket_gap | premarket last / close(D-1) - 1 | 09:25 D | D, D-1 | - | inherits |
| premarket_rvol | prior minute pages | 09:25 D | sessions < D | back from D-1 until 20 qualifying sessions (a [04:00,09:24] bar and a 09:30 bar, positive $vol) or the symbol's first collected session; undefined below 5 | unfetched page inside that range: INCOMPLETE; short complete history: NaN (Research) |
| position_in_premarket_range, return_0900_0925, return_last30m | premarket block | 09:25 D | D | - | NaN per Research on zero range / < 2 bars |
| spy_premarket_return | SPY page of D, SPY close(D-1) | 09:25 D | D, D-1 | - | SPY close(D-1) or SPY page not fetched: INCOMPLETE; no SPY premarket print: NaN. SPY's 09:30 bar is **not** required (V1 required it) |
| relative_strength_vs_spy | gap - SPY return | 09:25 D | as inputs | - | inherits |

A LIVE seal must be written before 09:30:00 ET of D. A later timestamp is a FutureContextViolation,
not a downgrade to RECONSTRUCTED.

## 3. Daily snapshot: what the store can supply today (checked 2026-09-21)

| input | available | covers |
|---|---|---|
| grouped daily (`market_data/raw/massive/grouped_daily`, USB-HIST-V1) | through **2026-09-16** | D-1 context and its 21-session window for **D = 2026-09-17 only** |
| grouped daily for 2026-09-17+ | **not collected** (`market_data/forward` does not exist) | needed for every D >= 2026-09-18 |
| splits (`splits_2024-09-16_2026-09-16`) | executions through 2026-09-16 | **no forward session**: each D needs `splits_asof_<D>` covering executions through D |
| CS reference | latest `CS_2026-07-01` | valid "latest <= D-1" for current forward sessions |
| minute history | Common Raw pages <= 2026-09-16; 1,612 of 2,936 daily-eligible symbols covered in the broad window | RVOL history exists only for covered symbols |

Consequently no forward session can pass `require_complete` today. The first session that can is
the first one for which D-1 grouped daily, a split list through D, SPY data, and whole-universe
minute coverage (including RVOL history) have all been collected. This is a data-collection
prerequisite, not a research decision. Per forward protocol section 13, partial universe coverage
makes a session INVALID as forward evidence.

## 4. Minimum daily window

21 XNYS sessions ending exactly at D-1: the [D-21, D-2] liquidity median window plus the D-1 row.
`universe.daily_eligibility` refuses a panel that carries a row dated D or later, or that stops
short of D-1.

## 5. LIVE vs RECONSTRUCTED

| mode | inputs | evidence |
|---|---|---|
| LIVE | fetched by 09:25, seal written before 09:30:00 ET | PRIMARY |
| RECONSTRUCTED | fetched after the close, truncated at the same cutoffs | SECONDARY |
