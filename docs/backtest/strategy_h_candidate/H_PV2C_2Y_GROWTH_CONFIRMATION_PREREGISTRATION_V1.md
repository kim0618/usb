# Strategy H — H-PV2C 2Y Growth Independent Issuer Confirmation Preregistration V1

- declared: 2026-09-28
- status: **FROZEN BEFORE CONFIRMATION SAMPLE MATERIALIZATION OR SEC INGESTION**
- source model: H-PV2 contract checksum `17464dd446601d960863c79692efb133f659355263d0ee591f9745f304040eeb`
- checksum: `2e5b548cee80e6ab8c7336edb5033fa868a08a516d6affde531ce32c816fa31d`

## Purpose and development exclusion

Apply the unchanged PV2 Growth model to new issuers. Every PV2 primary CIK and every PV2 primary share-class/composite FIGI is excluded; either identity matching is leakage and aborts. Ticker differences cannot override company exclusion. PV1/PV2 files and artifacts are immutable.

## Deterministic confirmation selection

Start from the 2024-10-25 dated active US type-CS snapshot on XNYS/XNAS/XASE. Require CIK and stable FIGI, exactly one eligible snapshot security for that CIK, at least 451 of 501 local daily bars, and a bar on every one of PV2's 20 decision dates. Exclude all development identities. Within exchange sort by `SHA256(STRATEGY_H_PV2C_CONFIRMATION_V1|exchange|CIK|security_id|ticker)` and take fixed quotas XNYS 60, XNAS 55, XASE 5, target 120. Quotas are not redistributed; a short stratum makes the universe insufficient. Freeze the selected rows and checksum before SEC calls. No market-cap or return field participates in selection.

## Exact model equality

Use PV2's same 20 decisions, 21/63-session unadjusted labels, SPY benchmark, 10/20bp costs, four feature formulas, comparable-period families and tolerances, positive denominators, EPS prior >=$0.05, transition exclusions, feature quality gates (40%, median 10), 2.5/97.5 winsorization, ascending ranks, equal weights, minimum two features, deciles, Top 10/20%, bootstrap and three robustness removals. Current/future facts and amendment back-injection are forbidden; accession provenance is stored.

## SEC and coverage

Fetch only selected CIK submissions/companyfacts using the existing identified, rate-limited SEC client. Report requests, successes, failures, bytes and canonical rows. Composite coverage requires at least 15 valid dates, median 20 names/date, and 50% of 120×20 declared rows. Missing data never triggers replacement sampling or model changes.

## Confirmation gates and verdict

C1 D10 3M net excess >0; C2 D10−D1 net spread >0; C3 mean monthly IC >0; C4 decile monotonicity >=0.30; C5 D10 and spread stay positive after each frozen removal; C6 issuer positive-contribution shares <=35% single, <=75% top five, <=90% top ten; C7 base costs preserve D10 and spread; C8 coverage passes. CONFIRMED requires C1–C8. NOT CONFIRMED requires C8 and at most one of C1–C4. Otherwise INCONCLUSIVE. No threshold, feature, weight, date, sample, cost or verdict rule may change after results. Confirmation never authorizes H1 or paid acquisition automatically.
