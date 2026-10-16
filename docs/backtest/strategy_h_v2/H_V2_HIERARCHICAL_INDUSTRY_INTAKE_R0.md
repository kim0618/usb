# H-V2 Hierarchical Industry Intake R0 (offline feasibility)

Date: 2026-10-16 KST. Offline research on the new-candidate intake only. D1/E1/E2/E3, D3-D6, paper, forward
and production are unchanged and were only read; no forward return, paper P&L or outcome was used. Code:
`backend/app/backtest/strategy_h_v2/industry_intake_r0.py` (pure functions, rules fixed before the replay),
`backend/app/dev/run_h_v2_industry_intake_r0.py` (replay), tests `backend/tests/strategy_h_v2/test_industry_intake_r0.py`.
Evidence (local): `/home/tjd618/h_industry_r0/` (`full/report.json`, `scan_full.json`, `scan_subset.json`).

Verdict: **H-V2 HIERARCHICAL INDUSTRY INTAKE R0 = NOT_USEFUL** as the fix for the 1 GB problem.

* The price-only **Industry Radar is a weak proxy** (representative basket vs the rest of the industry:
  Spearman p50 0.36 per session, direction agreement 67 %, 51 % in small industries). At equal deep-scan cost it
  is dominated by a structure without radar.
* What does improve candidate coverage at the **same** daily deep-scan load as production is the **filing-event
  bypass** (E3's own 14-day recency, periodic reports only) plus a slow rotation: P1 capture 74 % -> 92 % and
  top-10 capture 70 % -> 91 % at about 700-900 deep scans per session.
* But the deep-scan count is not what loads the 1 GB host. In the measured SCAN, **68 % of all bytes read come
  from the MethodJudgement screen** (D5 panels re-reading the price files) and another 13 % from two full passes
  over the price store; SEC + companyfacts are 23 %. Restricting the deep scan to a hierarchical subset cut peak
  RSS (311 -> 224 MB) but raised total reads (1.82 -> 2.85 GB) and time (1,055 -> 1,448 s), because the screen
  judged more candidates to fill its 10 slots.

## 1. Current full SCAN cost (production-copy store, decision time 2026-10-15 01:00 UTC, fetch cap 700)

| Stage | Input -> output | Time | Bytes read | Files | Stage RSS peak |
|---|---|---|---|---|---|
| universe / reference | 5,320 rows -> 4,154 identified | 0 s | 0.1 MB | 1 | 138 MB |
| market tier (every price file) | 4,154 -> 3,127 market pass | 5 s | 78 MB | 521 | 148 MB |
| SEC submissions + companyfacts download | 700 CIKs (rolling cap) | 566 s | 192 MB | 1,453 | 194 MB |
| price series for scanned rows | 2 passes | 8 s | 156 MB | 1,042 | 248 MB |
| companyfacts parse (lean) | 699 parses | 24 s | 231 MB | 2,917 | 266 MB |
| E1 / E2 / E3 | 707 rows | 8 s | 0 | 0 | 261 MB |
| MethodJudgement screen | judged until 10 AUTO_READY | 447 s | **1,236 MB** | **10,048** | 285 MB |
| total | 10 candidates written | 1,055 s | 1,816 MB | 15,475 | process max 311 MB |

Bytes are counted at `Path.read_bytes` (WSL has no `/proc/self/io`), so they are the IO demand on a host with a
small page cache. Cheaply reducible before companyfacts: 5,320 -> 3,127 by reference + market tier, already done.

## 2. Taxonomy and representatives

SEC submissions `sic`, 4 digits (no industry field in the Massive reference): 378 industries over the 4,135
issuers ever in the full-scan pool; 4,075 classified, 60 unclassified (1.5 %); issuers per industry p10 / p50 /
p90 = 1 / 5 / 18 (SIC3: 247 groups, SIC2: 69). Representatives: top 3 by E1 trailing-21-session average dollar
volume, fixed per quarter: 902 / 905 / 922 representatives in 2026Q1-Q3. Radar input: grouped-daily closes of
the representatives only (no companyfacts, no SEC).

## 3. Replay (research universe CS_2024-10-25, 5,192 rows; 28 sessions, every 5th trading day 2026-03-02 .. 2026-09-16)

Baseline per session (point-in-time D1 pipeline): market-passing pool mean 3,610, P1 mean 282, P1+P2 mean 1,980
(55,429 candidate-sessions). AUTO_READY was not replayed (the screen is too heavy offline); "top-10" = the first
10 in the intake order (P1 first, filing recency, ticker), the downstream proxy.

Capture is measured against a persistent intake index (production keeps `intake_index.json`): a candidate is
captured when its latest deep scan gave the same E3 result as the full scan at that session.

| Arm | Deep scans / session | Reduction | Capture all | P1 | P2 | Top-10 |
|---|---|---|---|---|---|---|
| A. Radar RS>0 only | 1,701 | 53 % | 85.9 % | 77.9 % | 87.3 % | 77.5 % |
| A. Radar top-25 % only | 942 | 74 % | 69.0 % | 58.5 % | 70.7 % | 58.2 % |
| B. RS>0 + periodic bypass | 2,068 | 43 % | 95.8 % | 95.4 % | 95.9 % | 95.7 % |
| B. top-25 % + periodic bypass | 1,402 | 61 % | 91.3 % | 92.6 % | 91.1 % | 92.9 % |
| C. RS>0 + bypass + 10-day rotation | 2,215 | 39 % | 96.5 % | 95.8 % | 96.6 % | 95.7 % |
| no radar, periodic bypass only | 614 | 83 % | 79.8 % | 88.8 % | 78.4 % | 90.4 % |
| **no radar, bypass + 10-day rotation** | **899** | **75 %** | **90.6 %** | **91.6 %** | **90.4 %** | **91.4 %** |
| no radar, bypass + 5-day rotation | 1,202 | 67 % | 93.8 % | 92.2 % | 94.0 % | 89.6 % |
| production-like: 5-day rotation, no bypass | 707 | 80 % | 83.6 % | 73.6 % | 85.3 % | 70.0 % |
| production-like: 4-day rotation, no bypass | 889 | 75 % | 87.7 % | 79.1 % | 89.1 % | 77.9 % |
| any-filing bypass (E3's exact input) | 3,100+ | 11-20 % | 96-99 % | 99.9 % | | 100 % |

The full table (all radar variants x bypass x rotation) is in `report.json`; none of the variants is adopted.
Event bypass recovered 5,480 of RS>0's 7,808 misses; the 10-day rotation another 391.

Misses (B, RS>0 + bypass: 2,328): radar dormant with quiet representatives 852, E3 state changed without a
periodic report since the last deep scan 880 (P1's recency counts any filing), dormant small-cap 484, unclassified
69, radar unknown 43. Miss rate by size inside the industry: representatives 4.5 %, top quartile 4.1 %, mid 4.0 %,
small half 3.9 %: no large-cap bias; misses follow "no event and not rotated", not size.

## 4. Dry-run memory and IO (same store, same clock, fetch cap 700)

Hierarchical subset for 2026-10-14 = periodic bypass (0 issuers: no 10-K/10-Q in the 14 days before the Q3
season; local submissions are mostly as of 2026-09-28) + rotation slice 1/10 (39 industries) = 488 tickers.

| | full | hierarchical subset |
|---|---|---|
| SEC fetches / companyfacts parses | 700 / 699 | 342 / 342 |
| SEC + companyfacts bytes | 423 MB | 156 MB |
| MethodJudgement screen bytes / time | 1,236 MB / 447 s | 2,541 MB / 1,159 s |
| price-store passes (bytes) | 5 (234 MB) | 5 (234 MB) |
| total bytes / files | 1,816 MB / 15,475 | 2,853 MB / 23,975 |
| peak RSS (stages / process) | 285 / 311 MB | 218 / 224 MB |
| time | 1,055 s | 1,448 s |

Crypto/server impact estimate: lower RSS helps, but the host stalls are memory and IO together (this morning's
production run stalled crypto before it logged the market pass, i.e. before any companyfacts work); a subset
that doubles the screen's reads does not remove them.

## 5. What R1 should be (not an industry radar)

1. **MethodJudgement screen IO** (infrastructure, no rule change): build each judged issuer's D5 panel from one
   shared pass over the price store, or from H's pinned per-ticker history, instead of re-reading the store per
   candidate; byte-equivalent judgements required.
2. **Incremental price state**: keep the market gate (sessions, last close, 21-session ADV) and the scanned
   rows' series as a rolling state updated with the newest session file, instead of two full passes per run.
3. **Event-driven intake order** (strategy-adjacent, preregister first): deep scan issuers with a new
   10-K/10-Q (E3's 14-day window) first, then a slow rotation; at production's current load it raised P1 and
   top-10 capture by 13-21 points in the replay. The industry radar is not needed for it.

Production, D3-D6, paper and forward: unchanged.
