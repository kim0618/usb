# CONTRACT HOT-EDGE R0 V1 : Is there a tradable edge in "hot" Binance USDT perpetuals? (TRAIN only)

Track: US-B crypto / personal trading method, branch "hot coin". Separate from PTM-D1 (closed as
no entry information in BTC), from the HOT Coin Finder V1 (a read-only scanner, deployed, no edge claim),
from Q123, Wall, Collector, Recorder and production. LOCAL ONLY. No orders, no server change, no deployment.

## 0. Question and stop rule

Question: when a coin is "hot" by the HOT Finder V1 rule, does its price keep going (continuation) or
come back (reversal) over the next hours to days, by enough to pay realistic small-coin costs, and is that
different from any other coin at the same moment?

Phases: 1 data (download all symbols, read TRAIN only) -> 2 universe and event census (counts, no returns)
-> 3 edge study -> 4 controls, concentration, stability, leverage geometry -> 5 verdict -> STOP.
VALIDATION and HOLDOUT are not read. If the verdict is A, the hot-coin branch of data-mined methods is
closed, like the BTC branch.

## 1. Data

Source: data.binance.vision, USD-M futures, monthly files, sha256 verified against the published CHECKSUM.
- `klines/{SYMBOL}/5m` for every symbol ending in USDT in the archive listing (900 on 2026-10-13, delisted
  symbols included: LUNAUSDT 2021-01..2022-05, SRMUSDT ..2024-05, etc.). This removes survivorship bias.
- `fundingRate/{SYMBOL}` monthly.
- Not used in R0: 1 m klines (fills are at 5 m opens, see 6), `metrics` (OI, from 2021-12), mark/index.
Local path `data/runtime/crypto/hot_edge_r0/archive/`. The reader refuses any month outside TRAIN and logs
every file opened (sha256), as in PTM.

Split (same as PTM): TRAIN 2021-02-01 .. 2023-06-30; VALIDATION 2023-07 .. 2025-03; HOLDOUT 2025-04 ..
2026-09. R0 reads TRAIN only. A symbol's first 30 days after its first archive bar are excluded
(listing pumps are a different regime), and so is BTCUSDT (the user keeps BTC as the main instrument and
wants the hot coin as a separate button; the Finder excludes BTC too).

## 2. Scan times and the "hot" rule (fixed, from HOT Finder V1 R0)

Scan times: every UTC 4 h boundary (00, 04, 08, 12, 16, 20). At a scan time S, a symbol is eligible when it
has complete 5 m bars for the previous 24 h and passes the liquidity gates of the Finder:
`qv24h >= 20M USDT` (sum of quote volume, 288 bars) and `qv1h >= 0.5M USDT` (12 bars). The Finder's spread
and depth gates cannot be reproduced historically and are replaced by the cost scenarios of section 6.

Primary ranking (the Finder's rule): **1H range** = (max high - min low) / min low over the last 12 bars.
HOT set = top 5 by 1H range among eligible symbols. Dose-response set = ranks 6..20. Rest = ranks 21+.

Direction of the event (research convention, both hypotheses pre-registered):
`dir = sign(close[S] / close[S - 1h] - 1)`. Continuation = forward return in `dir` is positive; reversal =
negative. A zero 1 h change is dropped.

Secondary selection variants (two, fixed): V2 = top 5 by **24h return magnitude** |close[S]/close[S-24h]-1|,
dir = its sign; V3 = top 5 by **volume surge** qv1h / (qv24h / 24), dir = sign of the 1 h change. No other
variant may be added after reading results.

## 3. Measurement windows (research only, not trading rules)

Forward signed return r_h = dir * (open[S + h] / open[S + 5m] - 1) for h in 4h, 12h, 24h, 48h, 72h, 120h,
entry at the open of the first bar after the scan. **Primary cell: V1 x 24h.** Also per event: MFE, MAE
(5 m highs/lows) over 120 h, time of MFE. The user's method will be event-driven; these windows exist only
to measure whether an edge exists and over which span it lives.

## 4. Controls (per cell)

- RANDOM_COIN: at the same scan time, one eligible symbol drawn uniformly from ranks 21+, with its own
  `dir` by the same convention. Paired with the HOT event by scan time. 200 draws.
- DOSE: ranks 6..20 and ranks 21+ as groups; an edge in "hot" should be monotone top5 > 6..20 > 21+.
- BTC_SAME_TIME: BTCUSDT signed by its own 1 h change (market beta check).
- TIME_SHIFT: the same symbol and direction at S + 1 day and S + 3 days.

## 5. Statistics

Events cluster by scan time (several coins move together) and by day. Inference: 7-day moving block
bootstrap over UTC days (2000 reps, seed 20261013) on the per-event series, plus scan-time cluster SE.
Holm over the three variants at the primary horizon. Report N events, N scan times, N distinct symbols.

## 6. Costs and leverage geometry

Fees 5 bp/side taker. Slippage cannot be measured historically for small coins, so three fixed round-trip
cost scenarios are applied and the break-even cost is reported:
LOW 15 bp (BTC-like), **BASE 40 bp**, HIGH 80 bp. Funding: actual settlements of the symbol between entry
and the window end, signed by `dir`. Verdict uses BASE.

Leverage: per-symbol brackets are not in the archive. Report, for L in 5, 10, 20, the share of events whose
MAE within the window exceeds the liquidation distance 1/L - MMR with MMR = 1.0 % (typical small-coin
bracket 1) and with the PTM safety at 0.4 x that distance. This is geometry, not a recommendation.

## 7. Concentration and stability

- Share of total positive gross from the top 5 % of events (if > 50 %, the edge is a few lottery tickets).
- Number of distinct symbols contributing positive net; top 3 symbols' share.
- Net mean by calendar year (2021, 2022, 2023 H1) and by direction (LONG / SHORT events).
- Share of events ended by the 120 h window still above water (sanity, not a rule).

## 8. Verdict gates (fixed before any return is read)

Let m = BASE-cost net mean at the primary cell, in the direction of the hypothesis that the sign of m
supports (continuation if m > 0, reversal if m < 0; the gates then apply to |m| with the sign noted).

**C. HOT_EDGE_TRAIN_CANDIDATE**: N events >= 300 and distinct symbols >= 30; |m| > 0 with block-bootstrap
95 % CI excluding 0 after Holm (3 variants); paired difference versus RANDOM_COIN CI excludes 0 in the same
sign; dose response monotone in the same sign; top 5 % concentration <= 50 %; same sign in at least 2 of 3
calendar years; break-even cost >= 60 bp.
**B. WEAK_HOT_EDGE**: N >= 150; |m| > 0 with p <= 0.10; beats RANDOM_COIN with p <= 0.10 in the same sign;
at least one C gate fails.
**A. NO_HOT_EDGE**: otherwise.

The direction verdict is reported alongside: CONTINUATION, REVERSAL or NONE.

## 9. Overfitting controls

3 variants x 6 horizons = 18 cells read; 1 primary. No threshold search: 20M / 0.5M / 12 bars / top 5 /
30 days / cost scenarios are fixed here. The 24 h primary horizon was chosen before download because the
user's holding span (12 to 48 h) sits there. No other variant, gate or horizon may be added after results.

## 10. Output and provenance

`data/research/crypto/hot_edge_r0/r0_train_v1.json` with read log, contract sha before and after, two runs
byte-identical. Code `backend/app/crypto/research/hot_edge_r0/`. Tests `backend/tests/crypto/test_hot_edge_r0.py`.
Report `docs/crypto/hot_coin_edge_r0/R0_TRAIN_REPORT.md`. Nothing committed; the user commits.
