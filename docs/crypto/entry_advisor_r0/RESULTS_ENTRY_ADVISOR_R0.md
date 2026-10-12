# RESULTS: Manual Entry Advisor R0 (`entry-advisor.r0`), 2026-10-12

Verdict: **B. NO_VALID_ENTRY_FAMILY.** Cells reach STRONGER_EVIDENCE; none passes the gate: every
family × timing has a mean and median net at or below -9 bp (the round-trip cost of 11.25 bp is
never recovered), direction alignment holds for some, Holm p ≤ 0.05 only for *negative* nets.
Contract `CONTRACT_ENTRY_ADVISOR_R0.md` (sha 9335d796…, commit 782fe07) hashes as recorded.

The preview button shows **관망 · 현재 검증된 진입 조건 없음**, which is the only answer this
contract allows today. Same data, code and evidence as `wall_lifecycle_r1/RESULTS_WALL_LIFECYCLE_R1.md`.

## K. Families and events (display-qualified level episodes, 38 h)

| family | direction | T0 (early) | T1 | T2 | T3 |
|---|---|---|---|---|---|
| F1 ASK_REJECTION | SHORT | 682 | 640 | 34 | 23 |
| F2 ASK_BREAKOUT | LONG | 618 | 269 | 828 | 744 |
| F3 BID_REACTION | LONG | 810 | 774 | 78 | 40 |
| F4 BID_BREAKDOWN | SHORT | 614 | 205 | 902 | 825 |
| F5 CLUSTER_REJECTION | by side | 6 | 0 | 0 | 2 |
| F6 CLUSTER_BREAKOUT | by side | 29 | 0 | 0 | 0 |

8,192 events in all; 4,078 seconds of 136,822 start one (NO_TRADE share of seconds 97.0 %), and
64.0 % of the 8,755 display-qualified touch instants start none.

## L. Entry timing

Rejection families collapse from T1 to T2 / T3 (640 → 34 → 23; 774 → 78 → 40): a touched wall is
rarely still resting with the mid back outside its bin 15 s later (R1 §D: median post-touch
survival 3 to 4 s). Breakout families mostly fire at T2 (first cross) and T3 (consumed end).
No timing was selected; all are reported.

## M. Entry zones

Zone = [near edge − range60, far edge] (rejection) or [far edge, far edge + range60] (breakout).
Median width 3.0 to 6.0 bp; the entry mid lies inside the zone in 99 to 100 % of T1..T3 events
and in 21 to 26 % of breakout T0 events (the approach instant is on the other side of the wall).

## N. Invalidation

V1 (the level's far edge, rejection; back inside the bin, breakout) is 0.35 to 0.6 bp from the
entry in the median (the bin is 5 USDT = 0.6 bp): the structural invalidation sits inside one
bin. V1 is hit in 56 to 69 % of rejection events before any target. V2 (60 s flow flips) occurs
within 60 min in most events. V3 (next same-side wall) is recorded, not used for R.

## O. Targets 1 / 2 / 3 and fixed exits (net bp, after 11.25 bp + funding)

Breakout families (F2, F4, F6) have **no target**: the walls on the trade's side beyond the
consumed level are not in the display set at the instant (the set holds ≤ 6 bins per side, all
near the mid), so T1..T3 are missing for every breakout event (counted). Their fixed exits:

| family × timing | n | fixed 5 m net | 15 m | 30 m | 60 m |
|---|---|---|---|---|---|
| F2 T2 | 828 | -10.3 | -10.0 | -9.3 | -9.5 |
| F2 T3 | 744 | -10.3 | -10.0 | -9.2 | -9.4 |
| F4 T2 | 902 | -10.1 | -10.4 | -10.4 | -10.9 |
| F4 T3 | 825 | -10.2 | -10.5 | -10.5 | -10.9 |

Rejection families, target T1 with V1 invalidation (primary):

| family × timing | n | hit rate | invalidated | time to hit median | MAE before hit | mean net | median net | p5 | CI95 | Holm | R median |
|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 T1 | 586 | 32.3 % | 67.2 % | 205 s | 0.0 bp | -9.85 | -11.45 | -11.8 | [-10.3, -9.3] | < 0.01 | 18.6 |
| F1 T2 | 30 | 46.7 % | 53.3 % | 95 s | 0.3 | -9.23 | -12.25 | -13.9 | | < 0.01 | 4.2 |
| F1 T3 | 21 | 42.9 % | 57.1 % | 173 s | 0.0 | -10.11 | -12.29 | -13.6 | | < 0.01 | 3.3 |
| F3 T1 | 736 | 32.3 % | 67.3 % | 209 s | 0.0 | -9.78 | -11.44 | -11.8 | [-10.2, -9.3] | < 0.01 | 18.6 |
| F3 T2 | 72 | 33.3 % | 63.9 % | 574 s | 0.6 | -10.08 | -12.11 | -13.6 | | < 0.01 | 5.0 |
| F3 T3 | 39 | 38.5 % | 56.4 % | 521 s | 0.7 | -9.72 | -12.08 | -13.7 | | < 0.01 | 4.4 |
| F5 T3 | 2 | | | | | | | | | | INSUFFICIENT |

T2 / T3 targets (second and third opposing walls) hit 13 to 23 % with the same net range. Fixed
exits for F1 / F3 T1: -9.4 to -10.2 bp at every horizon.

## P. Hit rates

Target 1 is hit before V1 in 32 % of T1 events (46 % of the few T2 ones); V1 is hit first in 67 %.
Because V1 is 0.35 bp from the entry and target 1 is 7 to 9 bp away, the pattern is a 1 : 20
bet won one time in three.

## Q. Net after cost

Every cell: mean net between -8.1 and -11.9 bp, median between -10.9 and -13.1 bp. Gross means
are between -0.7 and +4.5 bp (R1 §D: 15 min gross returns of a few bp), so the cost dominates and
the sign of the gross is irrelevant to the verdict.

## R. R:R distribution

With V1 as the risk, median R is 17 to 19 at T1 (risk 0.35 bp) and 3 to 7 at T2 / T3 (risk 1.0 to
1.8 bp). These are geometry, not expectancy: the realised net is negative in every cell.

## S. NO_TRADE rate

97.0 % of seconds; 64.0 % of display-qualified touch instants. The advisor never returned a
direction without a family condition, and no family passed, so the preview's answer is 관망 at
every second of the window.

## T. Sample gates

STRONGER_EVIDENCE: F1 T0/T1, F2 all, F3 T0/T1, F4 all; REVIEW_ELIGIBLE: F3 T2; EARLY: F1 T2/T3,
F3 T3, F6 T0; INSUFFICIENT: F5 all, F6 T1..T3. Gate checks (per contract §9): direction
alignment passes for F3 (gross +2.6 bp) and fails for F1 (gross -1.8, i.e. the SHORT loses
gross); mean net > 0 fails everywhere; Holm p ≤ 0.05 holds everywhere but for a negative mean;
concentration of the top 10 % is 100 % of the (tiny) positive net. **No cell passes.**

## U. Advisor viability

Not viable as framed. What the numbers say about the frame, for the next contract rather than
for a change now:
- The structural invalidation (one bin) is too close to carry a taker cost; a different
  invalidation unit (ATR, or wall 2) would be a new contract, not a tweak.
- Breakout families need targets beyond the display set (the collector's full lm-wall.v2
  selection, `walls_selected`, is 9 to 16 per side).
- 38 h is one regime. The gate's 50 per cell is met, but the window is not.

## Button preview and recorder separation

`GET /entry-analysis` on the btc-context API reads `results.json` (`BTC_CONTEXT_ENTRY_ANALYSIS_RESULTS`,
unset in production) and the current snapshot; it answers `NO_TRADE` / `관망` with the statement
and the basis figures. The frontend button exists only in a build with
`NEXT_PUBLIC_ENTRY_ANALYSIS_PREVIEW=1`. One GET per click; no order, no callback, no recorder
write; `analysis_request_id = sha256(session8 | sample_ms | preview)[:16]`.
