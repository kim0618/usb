# Strategy E-MAX - M4 Aggressive Exit Protocol V1

| | |
|---|---|
| Stage | `E-MAX-M4`, phase A (protocol), frozen before any M4 performance |
| Machine authority | `strategy_e_max_m4_rules_v1.json`, canonical `6b3405a7…125c` |
| Upstream | M0 `b05bbf6`, M1 `d5bdc6b` (R1), M2 `9dc80b1` (max 3), M3 `7e240a8` / `7312f45` (B2, M4 AUTHORIZED) |
| Code | `app.strategy_e_max.exit_x2` (X2 exit), `app.strategy_e_max.m4` (loader, decision, M5 authorization) |
| Tests | `backend/tests/strategy_e_max/test_e_max_m4_protocol.py` |
| Evidence label | `E-MAX DEVELOPMENT` (reused development data, not OOS) |

## 1. Variants

Both variants use the same configuration: R1 ordering, max 3, and B2 exposure (1.5x when the
universe has at least 100 rows and `h5_rate` >= 0.030741, 1.0x otherwise).

- **X1 (comparator).** Exit at the exact 09:34 ET close (E-D3 `FIXED_FIVE_MINUTE_CLOSE_PROXY_V1`),
  with no fallback.
- **X2 (M0 hypothesis).** E-D3 decides at 09:34 first. If that exit is VALID and **close(09:34) >
  open(09:30)** (strict, raw prices), the position is held and exits at the exact **09:44** ET
  close. Otherwise it exits at close(09:34).

## 2. Semantics

- **Timing.** The hold decision is taken once the 09:34 bar has closed, at 09:35:00 ET. It reads
  only the 09:30 open and the 09:34 close. The position is then held to the close of the 09:44 bar.
- **Condition.** The test uses raw prices only. Net-of-cost tests, return thresholds and RVOL
  conditions are forbidden.
- **Missing 09:34.** A position without a valid 09:34 exit cannot be evaluated and stays
  `UNRESOLVED_EXIT`, exactly as in X1.
- **Missing 09:44.** An extended position without exactly one valid 09:44 bar is
  `UNRESOLVED_EXIT / INVALID_FOR_STANDARD_PNL`. "Valid" uses E-D3's own rules: the bar must
  exist, be unique, have a finite positive close, and belong to the right session and symbol.
  There is no fallback to 09:43, 09:45, the last print or the session close.
- **Unchanged trades.** A position that is not extended keeps its E-D3 record unchanged. X1 and X2
  can therefore differ only on positions that were in profit at 09:34.
- **Sizing.** E-D5 classification and 1/n weights, as in every earlier stage. An extended position
  that ends unresolved leaves the standard-PnL set.
- **Exposure.** B2 is applied after the exit and is unchanged.
- **Cost.** 0/5/10/15/20 bp, primary 10 bp. **Limitation:** the E-D4 cost is a time-independent
  round-trip stress. The roughly 15-minute hold from 09:30 to 09:44 receives no extra slippage or
  holding cost.
- **Data.** The 09:44 bars are read from the same bound development view, and only for selected
  symbol-sessions.

## 3. Integrity (checked before X2 is read)

- The E-R3 prechecks are re-run first.
- X1 must hash to the committed M3 B2 artifacts: trades `fee44b84…`, daily `97f9d50c…`.
- **Invariants:** X1 and X2 share the universe, H5 candidates, R1 order, selection, entry records
  and B2 exposure map. Positions that were not extended have identical records, and delta is 0
  on sessions with no extended position.

## 4. Gates (M0 values, unchanged)

- **Eligibility of X2.** Integrity PASS, coverage >= 95%, 10 bp session mean > 0, PF > 1,
  MDD >= -35%, top1 share <= 0.7338.
- **Improvement over X1.** Cumulative 10 bp X2 > X1. The paired delta_t = X2 − X1 over 480
  sessions must have mean > 0 and P(delta <= 0) <= 0.10 (IID bootstrap, 10,000 replicates, seed
  20260921).
- **Verdict.**
  - `E-MAX-M4 PASS — X2 SELECTED` if X2 passes both gates.
  - `E-MAX-M4 NO_USEFUL_ENHANCEMENT` otherwise, and X1 is kept.
  - `E-MAX-M4 BLOCKED — INTEGRITY` on any integrity failure.
- **No new exit.** Other horizons (09:39, 09:49, 09:59 and so on), trailing stops, ATR,
  highest-high and VWAP exits stay forbidden.

## 5. M5 authorization (M0 exposure precondition)

M5 is **AUTHORIZED** only when all three hold:

1. at least one of M1-M4 produced a winner (R1 in M1 and B2 in M3 already did);
2. stop conditions A and D do not hold;
3. the configuration carried out of M4 passes every M0 eligibility condition.

M5 may only use the M0 multipliers 1.0, 1.5 and 2.0.

## 6. Diagnostics (not used to decide)

- **Exit funnel.** X1 09:34 exits; X2 09:34 exits, 09:44 extended exits, 09:44 unresolved, and the
  extension rate.
- **Continuation outcome.** For extended positions: 09:34 gross return, 09:44 gross return, and the
  incremental 09:44/09:34 change (mean, median, positive rate).
- **Changed sessions.** Positive, negative and zero delta.
- **Tail dependence.** The share of the total delta from the top 1, 3 and 5 sessions.
- **Other.** Risk (worst session, week, month and quarter; recovery; losing run), FULL / LEGACY /
  BROAD periods, the four blocks with paired delta, Stop B, and concentration.

## 7. Reproducibility and outputs

The run is executed twice. Both runs must give identical exit decisions, trades, daily returns,
metrics, bootstrap, verdict and result digest.

- Runtime: `data/runtime/strategy_e_max/m4_runs/<run_id>/` (gitignored).
- Committed: `E_MAX_M4_EXIT_RESULT_V1.md`, `strategy_e_max_m4_result_v1.json`, `.sha256`.
