# H-N1 Event-Fundamental Swing Quant Core Census: Contract V1 (pre-outcome amendment 1)

```text
contract   H_N1_CONTRACT_V1
status     FROZEN_BEFORE_OUTCOME
revision   PRE_OUTCOME_AMENDMENT_1 (amendments A-E)
frozen at  2026-10-19 15:40:35 KST
file       h_n1_contract_v1.json   (the JSON is the authority; this document explains it)
canonical  5353017cdf4d59388a46c7882b4f95c78a4d6c88d2d33de7ad4f63c1d560d9f8
file sha   e990dc1c151077397d50c60e0b49007050ce98be0ab01dd94cba117acc66fe71
supersedes canonical e08f3d8cab90ac4917cfbf91b7e0f9fb5a2ade9826b1ed25007a68f5bd3545e7 -> superseded_e08f3d8c/ (SUPERSEDED_BY_PRE_OUTCOME_AMENDMENT)
LLM        prohibited for alpha, scoring, approval, ranking
```

## 0. State at freeze
```text
outcome return calculations     0
event table                     NOT BUILT
reaction values / q33 / q67     NOT CALCULATED
feature outcome distribution    NOT INSPECTED
holdout / 21D / 63D outcome     NOT INSPECTED
```

## 1. Question
Do US common stocks whose quarterly revenue and net income improved year over year, and whose price reaction to that
information was weak, earn SPY-relative excess returns after costs over 5/21/63 sessions, in both development and holdout?

## 2. Amendments in this revision
| id | change |
|---|---|
| A | Universe = CS_2024-10-25 snapshot. Only signal-formation sessions >= 2024-10-25. 2024-10-02..2024-10-24 -> EXCLUDED_PRE_UNIVERSE_SNAPSHOT. No earlier universe is reconstructed. DEV becomes 2024-10-25..2025-12-31. |
| B | FUNDAMENTAL_VALID = current and prior-year Revenue and Net Income all present + period/comparability validation PASS. F+ = VALID and prior Rev > 0 and Rev up and NI up. F- = VALID and not F+. Missing XBRL, missing fields, missing prior, invalid period or filing mapping are never F-. |
| C | Shorter-horizon consistency on gross (0 bp) mean excess: 21D needs DEV and HOLDOUT 5D >= 0; 63D needs DEV and HOLDOUT 5D and 21D >= 0. |
| D | Delisting consistency: every gate evaluated under OBSERVED and CONSERVATIVE; if the horizon verdicts differ, no PASS. |
| E | Implementation details fixed before any number: numpy linear quantile, WEAK = r <= q33 inclusive, share class = highest 20D median dollar volume, SPY window matches the stock close used, outcome-window ticker continuity by CIK through any snapshot, bootstrap RNG default_rng(20261019), concentration with net total <= 0 fails, zero eligible seasons fails, 10-Q filed > 180 days after period end = INVALID_PERIOD. No threshold, horizon, cost or filter value changed. |

## 3. Timeline per event (primary = 10-Q)
8-K 2.02 (same CIK, filingDate in [10-Q date - 60d, 10-Q date], nearest, ties earliest accession; none -> the 10-Q itself).
Reaction = split-adjusted close of the last session before the anchor -> close of the signal-formation session
(first session strictly after the 10-Q filingDate). Entry = OPEN of the next session. acceptanceDateTime is not used.

## 4. Universe, filters, fundamentals
CS rows with CIK on XNYS/XNAS/XASE in CS_2024-10-25. Close >= $5 and 20-session median close*volume >= $5M at signal formation
(all 20 sessions present). 10-Q only (amendments excluded), one event per (CIK, filingDate). As-known values (latest filed <= filingDate),
current = 80-100 day period of the event accession, prior = same tag, end 365 +/- 20 days earlier, duration within 15 days.
EPS, operating income, margin, accelerations, FCF, 10-K: diagnostic only.

## 5. Reaction cutoffs and cohorts
q33/q67 computed once on DEV events that pass every filter, are FUNDAMENTAL_VALID (F+ and F- together) and have a reaction;
frozen in h_n1_dev_reaction_cutoff_v1.json before any forward price is read; reused unchanged for HOLDOUT.
**PRIMARY = F+ AND WEAK. CONTRAST = F- AND WEAK.** Everything else is diagnostic.

## 6. Returns
Horizon h counts the entry session as 1, exit at the close of session h (5/21/63 primary, 1 diagnostic). Excess = stock - SPY on the same window.
Costs 0/10/25 bp round trip, once per event. Split-adjusted. EXIT_GAP -> next available close. TRUE_DELISTING (CIK has no row under any
successor ticker through the end of the store) -> OBSERVED (last close, cash afterwards) and CONSERVATIVE (-100%).

## 7. Split and statistics
DEV = signal 2024-10-25..2025-12-31, HOLDOUT = 2026-01-01..end. Block bootstrap by ISO week of signal formation, 10,000 resamples, seed 20261019.
Season = calendar quarter of signal formation.

## 8. Gates (all frozen)
Sample: N < 100 INSUFFICIENT, 100-299 WEAK_SAMPLE (max BORDERLINE), >= 300 in both splits = OK.
1. mean net at 25 bp > 0, DEV and HOLDOUT
2. median net at 10 bp > 0, DEV and HOLDOUT
3. DEV bootstrap 95% CI lower bound at 10 bp > 0
4. CONSERVATIVE delisting mean at 10 bp > 0, both splits
5. top 5 < 50% of summed net at 10 bp and mean after removing the top 1% > 0, both splits
6. mean at 10 bp > 0 in >= 60% of seasons with N >= 30 (DEV+HOLDOUT)
7. PRIMARY mean > CONTRAST mean at 0 bp, both splits
8. shorter-horizon consistency (amendment C)
+ delisting consistency (amendment D)

**H_N1_PASS** = 21D or 63D passes everything with sample OK. 5D alone can never PASS.
Order: INSUFFICIENT, FAIL_SIGNAL, FAIL_COST_BOUND, H_N1_PASS, FAIL_HOLDOUT, BORDERLINE, FAIL. Diagnostics never upgrade.

## 9. Prohibited after freeze
Any change to universe, filters, tags, YoY rules, F+, reaction window, anchor, cutoffs, horizons, costs, split dates or gates;
new primary cohorts; promoting a diagnostic; small-cap/sector subsets; historical LLM decisions; runtime/scheduler/API/UI/paper.
A change is a new version on new data. The legacy Strategy H forward ledger is never read.
