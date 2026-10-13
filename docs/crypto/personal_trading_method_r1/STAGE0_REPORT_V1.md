# PTM-R1 Stage 0 Report V1 (TRAIN census, no outcomes)

Run 2026-10-13 KST. Output `data/research/crypto/ptm_r1/stage0_v1.json`.
Code `backend/app/crypto/research/ptm_r1/{data,states,stage0}.py`, tests
`backend/tests/crypto/test_ptm_r1_stage0.py` (26 passed). Run time 6.4 s.

No price was parsed. No return, PnL, MFE, MAE, PF or win rate was computed or written.
VALIDATION and HOLDOUT were not read. Nothing operational was touched. Not committed.

**Verdict: B. STAGE0_RESEARCHABLE**, with a contract-semantics warning (section N) that the user
should decide on before R1 outcomes are computed.

---

## A. Contract sha

`docs/crypto/personal_trading_method_r1/CONTRACT_PTM_R1_V1.md`
sha256 `752cbd19339530bb0a9439e9037db430e09c7b122f7cfb82280b485df74eef61`
(the run re-hashes before and after and refuses to write if it changed).

## B. Data lineage

| source | role | rows | check |
|---|---|---|---|
| D5.2 archive monthly `BTCUSDT-1m-2021-02 .. 2023-06.zip` (29) | TRAIN | 1,267,200 | sha256 per file in output `files_read` |
| D5.2 archive daily `BTCUSDT-1m-2026-09-01 .. 09-22.zip` (22) | parity (volume fields only) | 31,680 | |
| MC R0 REST copy `binance_kline_1m_1786492800000_1791100800000.jsonl` | parity (volume fields only) | 76,800 | |

Parity re-verified: overlap **31,680 minutes, differences 0** on `quote_volume` and
`taker_buy_quote_volume`, compared as exact integers (1e-5 USDT). Files read: 52, roles
`train` and `parity` only.

## C. Train coverage

2021-02-01 00:00 to 2023-06-30 23:59 UTC: expected 1,267,200 minutes, read 1,267,200, missing 0,
invalid (taker > quote or unparseable) 0. All four horizons are defined from boundary index 1439
(first full 1440-minute window); evaluated **1,265,761 minutes = 879.0 days**.

## D. Flow state frequencies (evaluated minutes, %)

| horizon | BUY | SELL | EVEN | NONE |
|---|---|---|---|---|
| 10M | 49.114 | 50.878 | 0 | 0.008 (96 min, zero-volume windows) |
| 1H | 48.225 | 51.775 | 0 | 0 |
| 4H | 46.661 | 53.339 | 0 | 0 |
| 1D | 44.306 | 55.694 | 0 | 0 |

The lean leans SELL more often, and more so at longer horizons. This is a property of the taker
split, not a forecast. EVEN never occurred, so `UNCLASSIFIED_EVEN` setups = 0.

Alignment:

| state | % of minutes |
|---|---|
| 1D = 4H (big direction defined) | 65.225 |
| 1D = 4H = 1H | 43.208 |
| 1D = 4H = 1H = 10M (full alignment) | 26.443 |
| NO_TRADE (1D != 4H) | 34.775 |

Big-direction regimes: 31,537 runs in 879 days (LONG 7,282, SHORT 8,594), about 36 per day.

## E. Flip frequencies

| horizon | changes | per day | per week | mean run length (derived, min) |
|---|---|---|---|---|
| 10M | 146,782 | 166.99 | 1,168.9 | ~8.6 |
| 1H | 54,688 | 62.22 | 435.5 | ~23 |
| 4H | 25,145 | 28.61 | 200.2 | ~50 |
| 1D | 6,687 | 7.61 | 53.3 | ~189 |

All changes were direct BUY<->SELL (no EVEN). **The 1D lean flips 7.6 times a day on average.**

## F. F1 TREND_FOLLOW

| | raw | primary dedup | one-position 48h | dup share (primary) | interval raw p50 / mean (min) |
|---|---|---|---|---|---|
| LONG | 4,613 | 4,613 | 100 | 0.0 | 66 / 274 |
| SHORT | 5,548 | 5,548 | 122 | 0.0 | 59 / 228 |

Primary dedup removes nothing for F1: F1 by definition starts after the big direction broke, so
every F1 is in a new regime.

## G. F2 PULLBACK_RETURN

| | raw | primary dedup | one-position 48h | dup share | interval primary p50 / mean |
|---|---|---|---|---|---|
| LONG | 9,961 | 1,818 | 51 | 0.8175 | 346 / 696 |
| SHORT | 13,431 | 2,239 | 76 | 0.8333 | 330 / 564 |

## H. F3 DEEP_PULLBACK_RETURN

| | raw | primary dedup | one-position 48h | dup share | interval primary p50 / mean |
|---|---|---|---|---|---|
| LONG | 4,653 | 1,296 | 27 | 0.7215 | 470 / 977 |
| SHORT | 6,531 | 1,681 | 56 | 0.7426 | 439 / 751 |

All setups (raw 44,737): 99.03 % fall within 48 h of an accepted setup under the one-position rule.

## I. Thesis candidate frequencies (accepted setups, first occurrence after entry)

Minutes to invalidation, median (p25 to p75):

| family | dir | I1 1H rev | I2 4H rev | I3 1H+4H | I4 1D/4H break |
|---|---|---|---|---|---|
| F1 | LONG | 34 (10-63) | 30 (5-149) | 75 (28-241) | 13 (3-63) |
| F1 | SHORT | 34 (10-66) | 25 (5-151) | 80 (27-248) | 13 (3-71) |
| F2 | LONG | 41 (23-64) | 109 (31-241) | 136.5 (49-275) | 73.5 (21-227) |
| F2 | SHORT | 41 (20-65) | 115 (28-263) | 154 (47-303) | 81 (21-244) |
| F3 | LONG | 9 (3-38) | 114 (38-202) | 130 (50-258) | 96 (30-188) |
| F3 | SHORT | 10 (3-36) | 120 (38-241) | 136 (52-269) | 106 (30-209) |

## J. 48h invalidation availability

Every candidate fired within 48 h for **100 %** of accepted setups in every family and direction;
`not_by_48h` = 0 and `train_tail_censored` = 0 everywhere. The 48 h MAX HOLD is therefore never the
binding exit for policy C on TRAIN, and for policy B ("hold while the thesis is valid") the thesis
is gone within roughly 10 minutes to 2.5 hours (median).

## K. Sample viability (contract rule: primary-dedup N, LONG + SHORT)

| family | primary N | label | one-position 48h N (conservative, reported only) |
|---|---|---|---|
| F1 | 10,161 | RESEARCHABLE | 222 (would be LIMITED) |
| F2 | 4,057 | RESEARCHABLE | 127 (would be LIMITED) |
| F3 | 2,977 | RESEARCHABLE | 83 (would be TOO_RARE) |

Sample size is about statistics only, not profitability. Because setups cluster (F1 raw interval
median about 1 hour), the primary N overstates independence. R1 has to use cluster-robust
inference (block by day or by regime), not per-setup i.i.d. intervals.

## L. Holdout protection

- `_open_zip` refuses any monthly file outside 2021-02..2023-06 and any daily file outside the 22
  parity days; refusals tested for 2023-07, 2025-04, 2026-09-23.
- `to_volumes` refuses any row at or after 2023-07-01 00:00.
- The reader parses only columns 0, 7, 10. Tested with a zip whose price columns are not numbers.
- Output scanned for performance keys (return, pnl, mfe, mae, pf, win_rate, profit, price, gross,
  net_bp) before writing; tested both ways.
- `files_read` in the output lists every file with role and sha256: 29 train, 22 + 1 parity.

## M. Tests (26 passed)

10m / 1h / 4h / 1440m aggregation against the production `flow_horizon.horizon_view` and against
brute force; exact tie = EVEN and missing minute = NONE; fixed-point parse; overlap parity on the
real files (31,680 / 0); no future leak (rewriting later minutes leaves earlier states and setups
unchanged); deterministic census; LONG/SHORT symmetry by mirroring the aggressor; flip counting;
family detection (F1, F2, F3, UNCLASSIFIED_EVEN, NO_TRADE, LONG and SHORT); both dedup rules;
invalidation masks; holdout guards; price columns never parsed; no performance fields.

## N. Limitations and contract-semantics warning

1. **The "1D = big direction" premise does not hold for the no-dead-band lean.** The trailing
   24 h buy/sell split sits close to 50/50, so its sign flips 7.6 times a day (mean run about
   3 hours). 4H flips about 29 times a day. A "big direction" that changes every few hours cannot
   carry the multi-hour to multi-day thesis the user holds (manual episodes of 12 to 45 h).
2. Consequence for the policies: under I1..I4 the thesis is invalidated within 48 h in 100 % of
   cases, median 9 to 154 minutes. Policy C on these definitions is a short-hold method; policy B
   cannot express "hold for a day while the thesis lives".
3. This is a state property measured without any outcome. It is not an edge or no-edge finding.
4. Clustering: 99 % of setups sit within 48 h of another; the independent sample is far smaller
   than the primary N.
5. The production meaning was kept exactly (contract section 3, user instruction "새 dead-band 생성 금지").
   Any persistence rule or dead band would be a threshold and needs a new contract version.

## O. Recommendation for R1

Two paths, the choice is the user's:

- **Path 1, run R1 as frozen (V1).** It answers a legitimate question, "if I act on the screen's
  leans exactly as shown, what happens", but it tests a short-hold method, not the user's
  holding style. Use cluster-robust intervals (block by UTC day).
- **Path 2, contract V2 before any outcome.** Redefine the big direction so that it persists on the
  user's horizon, chosen only from state statistics already measured here (no returns seen), e.g.
  1D/4H from closed calendar candles of the same taker data, or a lean that must hold for k
  consecutive boundaries. This is a threshold, so V2 must pre-register at most 2 variants and keep
  the same sealed holdout unread. Stage 0 would be re-run for V2 on TRAIN only.

Recommendation: Path 2, because the purpose of PTM is to test the user's holding style and V1
cannot represent it. Either way, VALIDATION and HOLDOUT stay unread until then.

## P. Final verdict

**B. STAGE0_RESEARCHABLE** (contract rule: parity and coverage pass; F1, F2, F3 primary N all
>= 500). With the warning in N: the counts are large mainly because the states are unstable. No
performance or edge judgment is made. STOP.
