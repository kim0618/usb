# HOT-EDGE R0 : TRAIN report

Contract `CONTRACT_HOT_EDGE_R0_V1.md` sha **3fee835d…** (unchanged through the run). Output
`data/research/crypto/hot_edge_r0/r0_train_v1.json` (+ read log, 2 runs: see section K). Code
`backend/app/crypto/research/hot_edge_r0/` (download, data, study). Tests `tests/crypto/test_hot_edge_r0.py` 7.
TRAIN only (2021-02 .. 2023-06); VALIDATION / HOLDOUT not read. LOCAL ONLY, no orders, no server change,
nothing committed.

## A. Question

When a Binance USDT perpetual is "hot" by the HOT Finder V1 rule (largest 1 h range among liquid coins),
does its price continue or revert over the next 4 h to 5 days by more than small-coin costs, and is that
any different from a random liquid coin at the same moment?

## B. Data

data.binance.vision, USD-M monthly 5 m klines + funding for all 900 USDT symbols in the archive listing
(43,340 files, 6.67 GB, every file sha256-verified against the published CHECKSUM). Delisted symbols are in
the archive (LUNAUSDT, SRMUSDT, FTTUSDT, BTSUSDT, ...), so the universe has no survivorship bias.
TRAIN: 208 symbols with data, 35.7 M bars on a 253,440-bar grid. Reader refuses non-TRAIN months.
Trap met: five non-ASCII symbols (龙虾USDT etc.) need URL encoding; the first download attempt stopped there.

## C. Census (states only)

| | value |
|---|---|
| scan times (UTC 4 h grid) | 5,274; 5,100 with >= 5 eligible coins |
| eligible coins per scan (qv24h >= 20 M, qv1h >= 0.5 M, age >= 30 d, excl. BTC) | median 96, p5 55, p95 123 |
| by year | 2021 92, 2022 91, 2023 H1 95 |
| symbols ever eligible | 199 |
| events per variant (top 5 per scan) | about 25,100; 5,069 scan times; 192 to 196 symbols |
| LONG share of events (sign of the 1 h / 24 h move) | V1 62 %, V2 68 %, V3 64 % |

## D. Primary cell: V1 (1 h range top 5) x 24 h, costs all-in round trip

| metric | value |
|---|---|
| N / scan times / symbols | 25,089 / 5,069 / 194 |
| gross signed return (continuation +) mean / median | **-5.0 bp / -38.8 bp**, win share 48 % |
| funding mean | -2.4 bp |
| net BASE (40 bp) mean / PF | -42.7 bp / 0.89, CI95 [-60.5, -24.3], p < 0.01, scan-cluster p < 0.01 |
| net LOW (15 bp) / HIGH (80 bp) | -17.7 bp / -82.7 bp |
| break-even round-trip cost | -2.7 bp (there is nothing to pay costs with) |
| RANDOM_COIN (rank > 20, same scan, 200 draws) net BASE | -40.9 bp; hot minus random **-1.7 bp**, CI [-20, +17], p 0.85 |
| dose: top 5 / ranks 6..20 / 21+ | -42.7 / -45.0 / -40.0 bp (flat) |
| BTC at the same scan, own 1 h sign, LOW cost | -15.3 bp, win 46 % |
| time shift +1 d / +3 d | -25.9 / -48.4 bp |
| by year 2021 / 2022 / 2023 H1 | -48.4 / -49.0 / -19.7 bp |
| by direction LONG / SHORT | -36.8 / -52.2 bp |
| concentration: top 5 % of events share of positive gross; top 3 symbols | 39 %; AXS, SHIB, DOGE = 18 % |
| MFE / MAE mean over 120 h | 17.1 % / 15.8 % |

## E. All cells (net BASE mean, bp; CI excludes 0 marked *)

| horizon | V1 range | V2 24 h move | V3 volume surge |
|---|---|---|---|
| 4 h | -44* | -44* | -42* |
| 12 h | -34* | -53* | -39* |
| 24 h (primary) | -43* | -52* | -51* |
| 48 h | -29 | -27 | -40* |
| 72 h | -34 | -26 | -39* |
| 120 h | -35 | +4 | -40 |

Gross (before costs) is between -15 and +16 bp in every cell except V2 x 120 h (+50 bp gross), which is
2021 alone (+95 bp in 2021, -79 bp in 2022, +15 bp in 2023 H1) and LONG alone (+46 vs SHORT -86): the
2021 altcoin bull market, not a hot-coin effect. Random-coin paired differences at 24 h: V1 -1.7 (p 0.85),
V2 +5.9 (p 0.66), V3 -10.9 (p 0.17). Dose response is flat or inverted in every variant.

## F. Shape of the move after a hot hour

Mean about zero, median clearly negative (-20 to -160 bp gross across cells), win share 44 to 48 %. The
typical hot coin gives part of the move back; a thin right tail of continuations (MANA, UNFI, BEL, AXS in
2021) brings the mean back to zero. Neither "ride the hot coin" nor "fade the hot coin" has an expectation
that survives even zero cost. MFE within 120 h has its median at bar 474 to 534 (about 40 to 45 h): the
best exit point is far away and it is symmetric with the adverse side (MAE mean 13 to 16 %).

## G. Leverage geometry (share of events whose MAE reaches the liquidation distance, MMR 1 %)

| | 5x | 10x | 20x |
|---|---|---|---|
| within 4 h | 0.6 % | 5.5 % | 28 % |
| within 24 h | 6.1 % | 28 % | 61 % |
| within 120 h | 27 % | 59 % | 81 % |
| PTM safety (0.4 x) hit within 24 h | 35 % | 65 % | 85 % |

Hot coins move 15 % both ways inside five days. 10x is a coin flip with liquidation inside a day; 20x is
liquidated more often than not.

## H. Controls summary

Random liquid coin at the same time: same result. Ranks 6..20 and 21+: same result. BTC at the same time:
same sign, same size. Shifting the entry by 1 or 3 days: same result. Costs scenario does not change the
sign of any conclusion because gross is already about zero.

## I. Verdict gates (primary cell, pre-registered)

C gates: n, symbols, CI (negative side), concentration, years pass; **beats random** fails, **dose** fails,
**break-even >= 60 bp** fails. B gates: beats random with p <= 0.10 fails. Holm p over 3 variants 1e-5 to
2e-3 for the negative mean, which is the cost, not an edge.

**A. NO_HOT_EDGE, direction NONE, for V1, V2 and V3.**

## J. What this means for the hot-coin button

The HOT Finder shows which coin is moving. In TRAIN that information had zero expected value for the next
hours to days, in either direction, versus any other liquid coin at the same moment. The finder remains
useful as a situational display; it is not an entry signal. A method built on "enter the hottest coin" has
the same expectation as entering a random coin and pays higher costs and higher liquidation risk.

## K. Limitations and provenance

- 5 m bars: fills at 5 m opens, no intrabar path finer than 5 m; slippage not measurable historically, so
  three fixed scenarios. Since gross is about zero, no cost scenario changes the verdict.
- The Finder's spread and depth gates were replaced by volume gates; a stricter historical spread gate would
  shrink the universe but cannot create an edge that the volume-gated universe does not show.
- "Hot" = 1 h range (the Finder's rule), 24 h move magnitude and volume surge. Other definitions (new 24 h
  high, funding extremes, OI jumps from `metrics` since 2021-12) were not pre-registered and not run.
- TRAIN covers one bull-bear cycle (2021 up, 2022 down, 2023 H1 recovery). The one positive cell is the
  2021 long side.
- Index products (BTCDOMUSDT, DEFIUSDT) and USDCUSDT are in the universe; they rarely rank top 5.
- Two runs byte-identical: `r0_train_v1.json` sha **1c4d633b…** (run 1 == run 2).
