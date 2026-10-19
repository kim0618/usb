# H-N1 Event-Fundamental Swing Quant Core Census: Contract V1

```text
contract   H_N1_CONTRACT_V1
status     FROZEN_BEFORE_OUTCOME (2026-10-19 KST)
file       h_n1_contract_v1.json   (this document explains it; the JSON is the authority)
canonical  e08f3d8cab90ac4917cfbf91b7e0f9fb5a2ade9826b1ed25007a68f5bd3545e7
file sha   b83bf917efb409da93069e01e81c3cf72e3db3e5dbf8ae306fa7642e88beb976
known at freeze  prevalidation coverage counts only; no return, reaction or feature distribution computed
LLM        prohibited for alpha, scoring, approval, ranking
```

## 1. Question
Do US common stocks whose quarterly revenue and net income improved year over year, and whose price reaction
to that information was weak, earn SPY-relative excess returns after costs over 5/21/63 sessions, in both
development and holdout?

## 2. Timeline per event (primary = 10-Q)
```text
8-K 2.02 (same CIK, preceding, <=60d, nearest)   10-Q filingDate        signal formation           entry
         |                                           |                  (next session, CLOSE)   (next session, OPEN)
 reaction start = close of session before anchor ----+-------------- reaction end = signal-formation close
                                                                          XBRL usable here   returns start here
```
No 8-K in 60 days: the anchor is the 10-Q itself. acceptanceDateTime is not used anywhere.

## 3. Universe and data
CS rows of the 2024-10-25 snapshot with a CIK on XNYS/XNAS/XASE. Close >= $5 and 20-session median dollar volume >= $5M
at signal formation (E V1.1 rule reused). Ticker changes followed by CIK through later snapshots. Local data only:
companyfacts (4,906 CIK), submissions (two stores, merged by accession), unadjusted grouped daily + splits, SPY.

## 4. Fundamentals
As known at the event: latest `filed` <= event filingDate. 10-Q uses the 80-100 day period of the event accession;
the prior period ends 365 +/- 20 days earlier with a matching duration. Tag fallbacks are fixed in the JSON.
- **F+ = revenue improved (prior > 0, current > prior) AND net income improved (current > prior).**
- F- = both evaluable and not F+. Not evaluable -> excluded and counted.
- EPS, operating income, margin, accelerations, FCF: diagnostic only.
- 10-K: separate diagnostic cohort with annual YoY, never in the primary aggregate.

## 5. Reaction and cohorts
Reaction = split-adjusted close-to-close from the session before the anchor to the signal-formation close.
q33/q67 are computed once on all evaluable DEVELOPMENT primary events and reused unchanged on HOLDOUT.
**PRIMARY = F+ AND WEAK (<= q33).** CONTRAST = F- AND WEAK. Other cells, cap buckets and sectors are diagnostic.

## 6. Returns
Horizon h counts the entry session as 1 and exits at the close of session h (5/21/63 primary, 1 diagnostic).
Excess = stock - SPY on the same open-to-close window. Costs 0/10/25 bp round trip, deducted once.
Delisting before the horizon is flagged DELISTED_BEFORE_HORIZON and reported twice: OBSERVED (last close) and
CONSERVATIVE (-100%). Each event is independent; the 63D diagnostic adds next-filing overlap share and a truncated result.

## 7. Split and statistics
DEVELOPMENT = signal formation 2024-10-02..2025-12-31, HOLDOUT = 2026-01-01..end. Block bootstrap by ISO week of
signal formation, 10,000 resamples, seed 20261019.

## 8. Gates (all frozen)
Sample: N < 100 INSUFFICIENT, 100-299 WEAK_SAMPLE (max BORDERLINE), >= 300 OK.
A horizon passes only if all hold:
1. mean net excess at 25 bp > 0 in DEV and HOLDOUT
2. median net excess at 10 bp > 0 in DEV and HOLDOUT
3. DEV bootstrap 95% CI lower bound at 10 bp > 0
4. CONSERVATIVE delisting mean at 10 bp > 0 in both splits
5. top 5 events < 50% of summed net excess, and mean after removing the top 1% > 0 (both splits)
6. positive mean at 10 bp in >= 60% of seasons with N >= 30
7. PRIMARY mean excess > CONTRAST mean excess at 0 bp in both splits
8. every shorter primary horizon has mean excess >= 0 at 0 bp in both splits

**H_N1_PASS** = 21D or 63D passes with sample OK. Otherwise, in order: INSUFFICIENT, FAIL_SIGNAL (DEV gross <= 0 at all
three horizons), FAIL_COST_BOUND (gross > 0 somewhere but no horizon > 0 at 25 bp), FAIL_HOLDOUT, BORDERLINE, FAIL.
Diagnostics never upgrade a verdict.

## 9. Prohibited after freeze
Any change to universe, filters, tags, YoY rules, F+, reaction window, anchor, cutoffs, horizons, costs, split dates or gates;
new primary cohorts; promoting a diagnostic; historical LLM decisions; any runtime/scheduler/API/UI/paper before H-N2 PASS.
A change is H_N1_CONTRACT_V2 on new data. The legacy Strategy H forward ledger is never read.
