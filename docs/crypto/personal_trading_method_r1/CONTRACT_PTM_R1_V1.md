# PTM-R1 Contract V1: Flow-Horizon Thesis Replay (Personal Trading Method)

Status: FROZEN at Stage 0 (2026-10-13). Discovery: `docs/crypto/personal_trading_method_r0/DISCOVERY_REPORT_V1.md`.
This file's sha256 is recorded in every Stage 0 output. Any edit makes a new version (V2) and voids
outputs bound to V1.

Research only. No production module, order path, PAPER, LIVE, Q123, Context Collector or Manual
Trade Recorder is read or changed by R1 code. Q123 is out of scope entirely.

---

## 1. Split (fixed, UTC, inclusive dates)

| split | from | to (inclusive) | open_ms range |
|---|---|---|---|
| TRAIN | 2021-02-01 00:00 | 2023-06-30 23:59 | [1612137600000, 1688169600000) |
| VALIDATION | 2023-07-01 00:00 | 2025-03-31 23:59 | [1688169600000, 1743465600000) |
| SEALED HOLDOUT | 2025-04-01 00:00 | available end (2026-10-04 07:59) | [1743465600000, 1791100800000) |

The holdout is not read for any outcome until the R1 candidate (one family, one direction rule,
one invalidation, one policy) is frozen in a later contract version and the user approves opening it.

Stage 0 reads **TRAIN only**, and inside TRAIN reads **only** `open_time`, `quote_volume`,
`taker_buy_quote_volume`. It does not parse open/high/low/close, so it cannot compute any price
return. Stage 0 states or setups whose look-ahead window would leave TRAIN are counted as
`TRAIN_TAIL_CENSORED`; VALIDATION is not read to complete them.

Lineage exception (Stage 0): the parity check (section 2) reads `quote_volume` and
`taker_buy_quote_volume` of 2026-09-01..09-22 from both sources. These are volume fields, not
outcomes; no price field of that range is parsed.

## 2. Data

Primary: Binance USD-M BTCUSDT perpetual 1m klines, data.vision archive
`data/runtime/crypto/d5_2/archive/binance/futures/um/{monthly,daily}/klines/BTCUSDT/1m/*.zip`
(2021-02-01 .. 2026-09-22) plus the Market Context R0 REST copy
`data/research/crypto/market_context_r0/raw/binance_kline_1m_1786492800000_1791100800000.jsonl`
(2026-08-12 .. 2026-10-04 07:59).

Parity (re-verified at every Stage 0 run): on the overlap minutes of the archive daily files and the
REST copy, `quote_volume` and `taker_buy_quote` must be identical as exact decimals. Expected overlap
31,680 minutes, expected differences 0. A non-zero difference stops the run (`DATA_PROBLEM`).

No new download.

## 3. Flow state (identical to production `btc_context_ui_v1/flow_horizon.py`)

For a closed-minute boundary `end_ms` and horizon `N` minutes, the window is the closed minutes
with `open_ms` in `[end_ms - N*60s, end_ms)`.

- buy = sum(taker_buy_quote), sell = sum(quote - taker_buy_quote).
- a minute with taker_buy_quote > quote, or an unreadable value, is a missing minute.
- any missing minute in the window, or buy + sell == 0: state `NONE`.
- otherwise state `BUY` if buy > sell, `SELL` if sell > buy, `EVEN` if equal.
- horizons: M = 10, H1 = 60, H4 = 240, D = 1440 (trailing, not calendar day).
- no dead band, no threshold. Sums are exact (integer at 1e-5 USDT, the data's precision).

Sign: BUY = +1, SELL = -1, EVEN = 0, NONE = None. A state at boundary `end_ms` is known at
`end_ms` and uses no minute at or after `end_ms`.

Step: one evaluation per closed minute. Minute index `t` refers to the boundary at the close of
minute `t` (`end_ms = open_ms(t) + 60s`).

## 4. Big direction, NO_TRADE

- Big direction at t: `dir = D(t)` if `D(t) == H4(t)` and `D(t) in {+1, -1}`; otherwise NO_TRADE.
- NO_TRADE is a state, not a family. No setup can occur in NO_TRADE.
- Regime: a maximal run of consecutive minutes with the same non-null `dir`. Regime id is used for
  episode dedup (section 6).

## 5. Entry families (LONG for dir = +1, SHORT for dir = -1, symmetric)

FULL(t, dir) := D(t) = H4(t) = H1(t) = M(t) = dir.

A **setup** occurs at t when FULL(t, dir) and not FULL(t-1, dir). Entry (for later stages) is the
open of minute t+1. Every setup is assigned exactly one family by the **gap segment** G = the
maximal run of minutes ending at t-1 in which FULL(., dir) is false (bounded below by the first
minute with all four states defined):

- **F2 PULLBACK_RETURN**: every minute of G has D = H4 = H1 = dir, and some minute of G has M = -dir.
- **F3 DEEP_PULLBACK_RETURN**: every minute of G has D = H4 = dir, some minute of G has H1 != dir,
  and some minute of G has H1 = -dir.
- **F1 TREND_FOLLOW**: some minute of G has D != dir or H4 != dir (full alignment formed while the
  big direction was not yet in place), or G is empty because the series starts there.
- **UNCLASSIFIED_EVEN**: G stays in D = H4 = dir but the departure was only through EVEN/NONE
  (no -dir minute on the relevant horizon). Counted, never studied.

The families partition all full-alignment onsets. LONG and SHORT use the same code with the sign
flipped (tested by mirroring the input).

## 6. Episode dedup (pre-registered before any count)

- **Primary (same-thesis dedup)**: within one family and one direction, a setup is a duplicate if
  an earlier accepted setup of the same family and direction is in the same regime (section 4)
  and fewer than 48 h earlier. Accepted setups form the family's independent N.
- **Conservative secondary (one position at a time, 48 h envelope)**: across all families and both
  directions, a setup is skipped if it occurs within 48 h of the previous accepted setup.
- Also reported: share of raw setups that are duplicates under each rule.

## 7. Thesis invalidation candidates (checked from t+1, first occurrence)

| id | definition (for a setup in direction dir) |
|---|---|
| I1 | H1 = -dir |
| I2 | H4 = -dir |
| I3 | H1 = -dir and H4 = -dir in the same minute |
| I4 | not (D = dir and H4 = dir), i.e. the big direction no longer holds (disagreement or NONE) |

Not selected yet. R1 main study selects one on VALIDATION, before the holdout.
Excluded from R1 primary, recorded as future extensions: I5 flow + OI (no Binance OI), I6 wall,
Q123 conditions.

## 8. Policies (for later stages; Stage 0 computes none)

All three share the catastrophic layer (section 9) and MAX HOLD (section 10), applied to the same
entry set.

- **A. FIXED_STOP**: exit when the underlying (BTC perp price) moves adversely by 0.5 %, 1.0 % or
  2.0 % from entry. Three variants, all reported, none selected after seeing results.
- **B. THESIS_HOLD**: hold regardless of PnL while the thesis is valid; exit only at catastrophic or
  MAX HOLD.
- **C. THESIS_INVALIDATION**: exit at the first pre-registered invalidation (one of I1..I4 chosen on
  VALIDATION).

## 9. Catastrophic safety layer (not a thesis signal)

Exit when the adverse move from entry reaches **40 % of the entry-to-liquidation distance**.

Liquidation distance model (isolated-equivalent): `dist = 1 / L - MMR`, with
`MMR = 0.004` (Binance BTCUSDT bracket 1, notional cap 300,000 USDT, from
`docs/crypto/hot_symbol_v1/server_readonly_20261010/server_result.json`).

- Primary leverage L = 20 (account symbol config 20x; Recorder events 33 of 47 at 20x):
  dist = 4.6 %, catastrophic trigger = **1.84 %** adverse.
- Sensitivity L = 10: dist = 9.6 %, trigger = 3.84 %.
- The account is CROSS margin, where the real distance depends on wallet equity and is larger than
  this model. The model is therefore the conservative bound.
- Interaction recorded now, not changed: at L = 20 the FIXED_STOP 2.0 % variant can never trigger
  before the catastrophic layer (1.84 % < 2.0 %), so A(2.0 %) collapses into the catastrophic exit.
  It stays registered; the L = 10 sensitivity is where it is distinct.
- Adverse move is measured on the 1m low (LONG) / high (SHORT); when the same minute also reaches a
  favourable level, the adverse side is assumed first.

## 10. Maximum hold and horizons

MAX HOLD = 48 h. Measurement horizons: 1 h, 4 h, 8 h, 12 h, 24 h, 48 h.

## 11. Cost (later stages; Stage 0 computes no net)

- Fee: actual taker 5.0 bp per side (Binance commissionRate 0.000500, Recorder fills 47/47).
  Sensitivity: existing contract 5.5 bp per side.
- Spread/slippage: reuse the existing measured BASE component (D5 `realtime_cost_measurement_v1`,
  as used by D5.4/D6), read from source at run time, not restated.
- Funding: actual historical settlements crossed while holding, Binance fundingRate archive, sign by
  direction. No fixed funding assumption. Where Binance funding is absent (2026-09 on), the episode
  is flagged `FUNDING_MISSING`, not filled with an assumption.

## 12. Controls (definitions only; not run in Stage 0)

- **Random timestamp control**: for each family x direction, the same number of entries drawn
  uniformly (fixed seed 20261013) from minutes of the same split in the same big-direction regime
  type (dir equal), excluding minutes that are setups. Same exits, same costs.
- **Time-shifted control**: the flow-state series circularly shifted against the price series by
  7, 14 and 30 days inside the split; setups re-detected on the shifted states, outcomes read on the
  unshifted price.
- **Direction placebo**: the same setup timestamps traded in the opposite direction.
- Results are read as differences to controls, and as drift-adjusted by direction.

## 13. Overfitting controls

- Families fixed at F1..F3; invalidation candidates fixed at I1..I4; policy variants fixed above.
- No dead band, window or threshold change after any count or result. A change is a new contract
  version with a new, unread holdout.
- VALIDATION selects at most 2 (family, invalidation) candidates; HOLDOUT is read once.
- Independent N is the primary-dedup count, never minutes.

## 14. Stage 0 scope and viability

Stage 0 outputs only: state frequencies, flip frequencies, alignment shares, setup counts by family
and direction, dedup counts, setup intervals, invalidation counts and time-to-invalidation
distributions, 48 h invalidation share. No price, return, PnL, MFE, MAE, PF or win rate.

Viability per family (LONG + SHORT, primary-dedup N on TRAIN):
`N < 100` TOO_RARE, `100 <= N < 500` LIMITED, `N >= 500` RESEARCHABLE. This is about statistical
sample only, not profitability.

Verdict: A FAMILY_TOO_RARE (every family TOO_RARE), B STAGE0_RESEARCHABLE (at least one family
LIMITED or RESEARCHABLE and the data/contract checks pass; per-family labels are reported and a
TOO_RARE family is dropped from R1), C DATA/CONTRACT_PROBLEM (parity, coverage or a test fails).
