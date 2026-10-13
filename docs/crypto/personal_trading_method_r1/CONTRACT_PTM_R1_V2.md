# PTM-R1 Contract V2: Slow Big-Direction States (Stage 0)

Status: FROZEN before the V2 Stage 0 run (2026-10-13). Supersedes V1 sections 3 to 5 (state
definitions) only for the two V2 variants below. Everything else in
`CONTRACT_PTM_R1_V1.md` (sha 752cbd19...) applies unchanged: split, data, parity, families' gap-segment
classification, dedup, invalidation candidates I1..I4, policies A/B/C, catastrophic layer,
MAX HOLD 48 h, cost, controls, overfitting rules.

Reason for V2: V1 Stage 0 (no outcomes) measured the production rolling 1D lean flipping 7.61 times a
day and every I1..I4 firing within 48 h for 100 % of setups. V1 cannot express a 12 to 45 h thesis.
No price, return or outcome of any split has been read before this contract.

Exactly two variants. No other variant, persistence length or dead band may be tried in R1 V2.

## 1. Common rules

- Lean of a set of minutes: buy = sum(taker_buy_quote), sell = sum(quote - taker_buy_quote);
  BUY if buy > sell, SELL if sell > buy, EVEN if equal; NONE if any minute is missing/invalid or the
  total is zero. No dead band. Identical to production and V1.
- Boundary convention as V1: index t = the close of minute t; a state at t uses only minutes whose
  close is <= that boundary.
- Blocks are UTC-aligned (day 00:00, 4 h at 00/04/08/12/16/20, hour at :00), same as Binance candles.

## 2. V2-A Completed-Bar Flow (PRIMARY)

| horizon | state at boundary t |
|---|---|
| D | lean of the most recent **completed** UTC day block |
| H4 | lean of the most recent completed UTC 4 h block |
| H1 | lean of the most recent completed UTC 1 h block |
| M | rolling 10 minutes (as V1), entry timing |

"Completed" = the block's end is <= the boundary. The block in progress never contributes.

## 3. V2-B Persistent Rolling Flow (SECONDARY)

Raw leans are the V1 rolling leans. The big-direction horizons get a confirmed state with one
pre-registered persistence rule: **a horizon's state is confirmed when its raw lean has held the
same non-zero value for the length of the next shorter horizon**, continuously, ending at t; the
confirmed state then stays until the opposite value is confirmed the same way.

| horizon | state | persistence |
|---|---|---|
| D | confirmed rolling 1440 m lean | raw held 240 consecutive minutes (= the 4H length) |
| H4 | confirmed rolling 240 m lean | raw held 60 consecutive minutes (= the 1H length) |
| H1 | raw rolling 60 m lean (as V1) | none |
| M | raw rolling 10 m lean (as V1) | none |

Before the first confirmation the state is NONE. A NONE or EVEN raw minute breaks a run.

## 4. Families, regimes, invalidation

V1 sections 4 to 7 applied to the V2 state arrays of each variant (FULL = D = H4 = H1 = M = dir,
gap-segment classification F1/F2/F3, regime = run of the same big direction, I1..I4).

## 5. Stage 0 outputs (TRAIN only; no price parsed; no performance field)

Per variant, and V1 recomputed in the same run for comparison:

1. state frequencies per horizon; 2. flip frequency; 3. state duration (run length, minutes)
median and mean per horizon, and big-direction regime duration; 4. D+H4 alignment %;
5. D+H4+H1 alignment %; 6. F1/F2/F3 LONG/SHORT setup counts (raw, primary dedup);
7. **48 h overlap-adjusted N** = per family, LONG and SHORT merged in time, a setup is skipped if it
is within 48 h of the family's previous accepted setup (also the V1 all-family rule);
8. I1..I4 occurrence within 48 h; 9. time-to-invalidation distribution;
10. **thesis survival**: share of primary-dedup setups whose first invalidation comes after 4, 8, 12,
24, 48 h, per I1..I4. Denominator per horizon excludes setups whose horizon passes the TRAIN end
without an invalidation (`TRAIN_TAIL_CENSORED`); 11. V1 vs V2 duration comparison.

Note fixed now: with a 48 h envelope the overlap-adjusted N can be at most about 440 per family on
879 TRAIN days, so it can reach LIMITED but never RESEARCHABLE.

## 6. Verdict rules (pre-registered; evaluated per variant)

The **thesis** for survival tests is I4 (the big direction D = H4 = dir no longer holds), because it
is the invalidation that matches "the direction is still alive". I1..I3 survival is reported only.

- **SLOWER**: D flips/day <= V1 D flips/day / 3, **and** median big-direction regime duration
  >= 3 x the V1 median, both from the same run.
- **HOLD_LENGTH**: some family F in {F1, F2, F3} has I4 survival at 12 h >= 20 % **and** at least
  100 of its setups (LONG + SHORT, primary dedup) survive 24 h.
- **SAMPLE**: that same family has 48 h overlap-adjusted N >= 100.
- **NO_LEAK**: the leak tests pass for the variant's state functions.

Variant verdict: D `CONTRACT_OR_DATA_PROBLEM` if parity, coverage or NO_LEAK fails; else
A `V2_THESIS_TOO_FAST` if not SLOWER or no family meets HOLD_LENGTH; else C `FAMILY_TOO_RARE` if no
family meeting HOLD_LENGTH also meets SAMPLE; else B `V2_RESEARCHABLE`.

Overall verdict: V2-A's if it is B; otherwise V2-B's if it is B; otherwise V2-A's (primary).
A variant that passes is the one R1 continues with; the other is not studied further. If both pass,
V2-A (primary) is used and V2-B is reported only.
