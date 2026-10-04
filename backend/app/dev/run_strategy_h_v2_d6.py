"""H-V2-D6: the integrated decision engine pilot.

Offline, deterministic, 0 model calls, $0. D5-D2 ended with a blocking prerequisite rather than a
defect: its valuation framework worked and had no D3 or D4 counterpart on any issuer it could value,
so the D4 x D5 join was empty and D6 had nothing to integrate. D5-D2 §Q named three ways out and this
step takes the first - apply the identical D5-D2 evaluator to the thirteen issuers D4 already graded.

**What is reused and what is new.** The valuation evaluator is imported, not reimplemented:
`build_panel`, `resolve_session`, `value_issuer` and `audit` are D5-D2's own functions, called with
this step's declared `METHOD_JUDGEMENTS` instead of D5-D2's. That is the only parameter that differs,
D5-D2's default is untouched, and the D5-D2 report reproduces bit for bit with the parameter in place.
The new code is the decision engine in `app.backtest.strategy_h_v2.decision.d6_contract`, which owns
no number at all.

**The sample is selected by D4's coverage and that is a limitation, not a sampling choice.** D5-D0
drew its twelve by a seeded hash over the whole package universe precisely so that valuation coverage
could not be correlated with how a candidate scored. These thirteen are the issuers D4's live
validation tiers happened to run on - Tier A V3's three, Tier B's six, D4-BR-C's four - chosen for D4
contract reasons. So this is an out-of-pilot application of the valuation framework, which is worth
something, and it is not a random market sample, a performance sample or an alpha test. `SELECTION_EFFECT`
states it in the report rather than in a footnote.

**The judgements were declared before any fair value existed.** `METHOD_JUDGEMENTS` below is written
from each issuer's D5-D1-layer fundamentals and its stored D3 business model, both of which are
available without computing a single scenario. A model that picked the method after seeing the target
price would be fitting, so the preference order is declared, code takes the first entry whose observed
panel is contract-eligible, and `WINDOW_RULE` is applied unchanged and identically to all of them.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from app.backtest.strategy_h_v2.decision.d6_contract import (
    APPROVE_CLAUSES,
    D3Evidence,
    D3Provenance,
    D4Evidence,
    D4Provenance,
    D5Evidence,
    D6_CONTRACT_VERSION,
    D6_NEVER_PRODUCED,
    D6_OWNS_NO_NUMBERS,
    D4_ABSENCE_IS_NOT_A_READING,
    Decision,
    DecisionRecord,
    Eligibility,
    ExpectationGap,
    Confidence,
    NOT_MECHANICALLY_EVALUABLE,
    REJECT_TRIGGERS,
    UPSTREAM_CONTRACTS,
    VALUATION_DOES_NOT_OVERRIDE,
    WATCH_IS_THE_ABSTENTION,
    audit_decisions,
    decide,
)
from app.backtest.strategy_h_v2.valuation.fair_value import (
    ValuationStatus,
    WindowSelectionContract,
)
from app.dev.run_strategy_h_v2_d4_1 import load_price_panel
from app.dev.run_strategy_h_v2_d5_d2 import (
    DECISION_SESSION,
    MethodJudgement,
    WINDOW_RULE,
    audit as d5_audit,
    build_panel,
    value_issuer,
)

D6_CONTRACT = "h_v2_d6_integrated_decision_engine_pilot_v1"
OUTPUT_DIR = Path("data/runtime/strategy_h_v2/d6")

#: The thirteen issuers D4 graded, in the order their runs produced them. Not chosen here: this IS
#: D4's coverage, and `SELECTION_EFFECT` is the consequence.
D4_UNIVERSE: tuple[str, ...] = (
    "BSY", "GOOG", "SCCO",                             # D4.4A Final Tier A V3
    "CRK", "DORM", "FRPT", "IDCC", "SPSC", "TG",        # D4 Tier B
    "AEYE", "COLL", "FG", "VRRM",                       # D4-BR-C
)

#: Where each ticker's authoritative D4 analysis and its D3 research leg live. The run ids are the
#: ones the result documents name as authoritative, not the newest on disk: D4.4A names
#: `D4_2_A-20260930T012115Z`, and `D4_2_A-20260930T034348Z` is the D4-S1 SCCO smoke, which is a later
#: file for the same issuer and a different step's artifact.
D4_RUNS: tuple[tuple[str, str, str], ...] = (
    ("TIER_A_V3", "d4_2/analyses/D4_2_A-20260930T012115Z",
     "d3_3/attempts/D3_3-20260929T021659Z"),
    ("TIER_B", "d4_b/analyses/D4_B-20260930T053146Z",
     "d4_b/d3_leg/attempts/D4_B-20260930T053146Z"),
    ("BR_C", "d4_br_c/analyses/D4_BR_C-20261001T005758Z",
     "d4_br_c/d3_leg/attempts/D4_BR_C-20261001T005758Z"),
)

RUNTIME_ROOT = Path("data/runtime/strategy_h_v2")

#: The window-selection rule THIS step ran under, stated at the call site rather than inherited from a
#: default. D5-D2R repaired the rule after D6 published, and a published result keeps the contract it
#: was produced by: re-running this file reproduces its report rather than quietly becoming a D2R run.
#: A later step that wants the repaired rule - D7 - passes `D5_D2R_V1` explicitly.
WINDOW_CONTRACT = WindowSelectionContract.D5_D2_V1

SELECTION_EFFECT = (
    "This sample is selected by D4's coverage. D5-D0 chose its twelve with a seeded hash over the "
    "D2.1 package universe and said why: 'No exclusion list and no stratification by D3 or D4 "
    "outcome: whether valuation data exists has nothing to do with how a candidate scored.' These "
    "thirteen are the opposite - they exist because D4's Tier A, Tier B and BR-C validation runs "
    "needed issuers, and those tiers picked for contract-validation reasons including deliberate "
    "disjointness from earlier tiers. The consequence is specific rather than general: a statement "
    "like 'D5 valued 9 of 13' is a statement about D4's sample, and any count of APPROVE / WATCH / "
    "REJECT here describes thirteen issuers chosen by an upstream step, not a market. What the "
    "sample DOES support is the pipeline question - whether the three layers join on one issuer at "
    "all - and that question does not care how the issuers were chosen."
)

OUT_OF_PILOT_APPLICATION = (
    "The D5-D2 fair-value framework was designed on seven issuers and is applied here to a disjoint "
    "set of thirteen with no change to any rule, which is worth more than a re-run on the same seven: "
    "the method judgements are new, the panels are new, and the window rule faces businesses it was "
    "not written against. It is still not validation. Nothing here is measured against an outcome, "
    "no window, percentile or conflict ratio is tested, and thirteen issuers at one decision session "
    "is not a sample a framework can be validated on. The honest claim is out-of-pilot APPLICATION."
)

NO_FORWARD_RETURNS = (
    "No price after 2026-09-16 is read by anything in this step, and no decision is checked against "
    "what the issuer subsequently did. That is not caution, it is the only way D7's forward shadow "
    "can mean anything: a D6 whose thresholds had been nudged until the APPROVEs looked right would "
    "produce a forward test of its own tuning. The first validation is forward, by construction."
)


# -------------------------------------------------------------------------------------------------
# Model-owned judgements (text only - no number below enters any calculation)
# -------------------------------------------------------------------------------------------------

#: Declared from the D5-D1-layer fundamentals and the stored D3 business model, before any scenario,
#: fair value or target price for these issuers existed. Same dataclass D5-D2 uses, same semantics:
#: `primary_preference` and `secondary_preference` are ORDERED and code takes the first entry whose
#: observed panel is contract-eligible, and `unsuitable` is the NOT_SUITABLE assertion D5-D1 reserved
#: for the model to make where the business supports it.
METHOD_JUDGEMENTS: Mapping[str, MethodJudgement] = {j.ticker: j for j in (
    MethodJudgement(
        ticker="AEYE",
        business="AudioEye: subscription web-accessibility testing, automated remediation and "
                 "monitoring, sold month-to-month and on one- and multi-year terms. TTM revenue "
                 "42.0m, operating income -3.6m (-8.6%), free cash flow +5.8m (13.8% of revenue), "
                 "book equity 3.2m, market cap 90.6m, net debt 7.7m - 7.8% of a 98.3m enterprise "
                 "value. D3 records ARR +11% year over year.",
        primary_preference=("P/FCF", "EV/Sales"),
        secondary_preference=("EV/Sales", "EV/FCF"),
        unsuitable={
            "P/B": "book equity is 3.2m against 42.0m of revenue, so the balance sheet carries "
                   "roughly four weeks of sales. The earning asset is a subscription base and an "
                   "automation stack, neither capitalised. A P/B of 28.1x measures how little of "
                   "this company is on its balance sheet, which is D5-D0 §N's asset-light case.",
            "EV/EBITDA": "derived EBITDA is operating income -3.613m plus D&A +3.878m, so the "
                         "denominator is +0.265m - the residue of two numbers each more than ten "
                         "times its size. The resulting 371.1x is arithmetically correct and "
                         "economically empty: a 7% change in either operand moves it by a third, and "
                         "a percentile of such a series is a percentile of a cancellation.",
        },
        valuation_risk=(
            "Free cash flow is positive while GAAP operating income is negative, so the entire "
            "valuation rests on the gap between them. For a software issuer that gap is share-based "
            "compensation and capitalised cost amortisation, and whether it is a real economic "
            "margin or a presentation is a question the TTM bundle cannot settle.",
            "Absolute size is the risk the multiple hides: 5.8m of free cash flow on a 90.6m market "
            "cap means one collections quarter moves the denominator by double digits, so the P/FCF "
            "panel is noisier than the same panel on a large issuer.",
            "D3 classifies the only future-business entry - AI initiatives named by the incoming CFO "
            "- as STORY with no revenue, customer or capacity evidence, and records that the only "
            "quantified AI effect in the filings is internal cost reduction. Nothing in the "
            "valuation prices an AI product and nothing should be read as doing so.",
        ),
    ),
    MethodJudgement(
        ticker="BSY",
        business="Bentley Systems: infrastructure engineering software, 92% of 2025 revenue from "
                 "subscriptions and 93% recurring, ARR 1,536.0m at June 2026 with 12% constant "
                 "currency growth per D3. Book equity 1,202.0m, net debt 1,070.2m. No multiple "
                 "resolves: the market cap is unavailable at the decision session, so every method "
                 "refuses MARKET_CAP_UNAVAILABLE before any economic question is reached.",
        primary_preference=(),
        secondary_preference=(),
        unsuitable={},
        valuation_risk=(
            "There is no valuation to carry a risk. The numerator is missing, not the fundamentals: "
            "D3 itself lists `shares_outstanding` among its unknown fields, and D5-D1 already found "
            "that the market-cap numerator rather than the fundamentals is the binding constraint on "
            "this whole layer. Repairing it is a primitive project and is out of scope here.",
        ),
    ),
    MethodJudgement(
        ticker="COLL",
        business="Collegium Pharmaceutical: commercial-stage specialty pharma marketing Jornay PM, "
                 "Belbuca, Xtampza ER, the Nucynta products, Symproic and the acquired Azstarys. TTM "
                 "revenue 808.2m, operating income 157.4m (19.5%), net income 47.9m, EPS 1.24, free "
                 "cash flow 328.3m (40.6% of revenue), book equity 311.9m, market cap 722.4m. No "
                 "borrowing balance resolves at the cash date, so there is no enterprise value.",
        primary_preference=("P/E", "P/FCF"),
        secondary_preference=("P/FCF",),
        unsuitable={
            "P/B": "book equity of 311.9m is cash plus the unamortised cost of acquired product "
                   "rights. What this issuer is worth is the remaining exclusivity on six marketed "
                   "products, and a multiple of the accounting residue of past acquisitions values "
                   "neither the products nor the pipeline. This is the argument D5-D2 made against "
                   "P/B for CHRS, and it holds for the same reason.",
        },
        valuation_risk=(
            "Free cash flow of 328.3m against net income of 47.9m is the dominant fact and the "
            "dominant trap. The 280m gap is largely amortisation of acquired product rights, and for "
            "an issuer whose growth comes from buying products - Azstarys closed in May 2026 - that "
            "amortisation is the cost of replenishing the portfolio rather than a non-cash add-back. "
            "P/FCF at 2.2x prices the collections and ignores the replenishment, which is why P/E is "
            "the anchor and P/FCF is the cross-check rather than the reverse.",
            "The two methods share the equity numerator and read earnings two ways, so the "
            "cross-check is weaker than two different denominators would be. It is reported as the "
            "cross-check it is.",
            "No total debt resolves, and D3 names the reason - the newest debt fact is from 2019 and "
            "is listed in its unknown fields. The equity chain does not divide by net debt so no "
            "per-share figure here is distorted by the absence, but it means leverage is unmeasured: "
            "if this issuer carries convertible notes the market knows about and this repository "
            "cannot see, the equity multiples are a levered read presented as a plain one.",
            "D3 records Nucynta IR pediatric exclusivity expiring 2027-01-03, just past the thesis "
            "horizon. A multiple built on TTM earnings cannot price a dated exclusivity cliff.",
        ),
    ),
    MethodJudgement(
        ticker="CRK",
        business="Comstock Resources: Haynesville and Western Haynesville natural gas exploration "
                 "and production, selling at market prices at the wellhead. TTM revenue 2,177.8m, "
                 "operating income 627.7m (28.8%), net income 508.3m, free cash flow -735.2m against "
                 "capex of 1,554.7m, book equity 2,579.1m, market cap 3,846.4m. No borrowing balance "
                 "resolves, so there is no enterprise value, and a negative free cash flow removes "
                 "the only other equity method. P/B at 1.49x is the single computable multiple.",
        primary_preference=(),
        secondary_preference=(),
        unsuitable={
            "P/B": "an exploration and production company's book equity is the historical cost of "
                   "its properties less depletion and impairments. What the equity is worth is the "
                   "reserve base at forward gas prices, and the two are different quantities that "
                   "move independently - a price-driven impairment lowers book value while the gas "
                   "in the ground is unchanged. D3's own evidence makes the point sharper than any "
                   "general argument: the company signed a letter of intent under which SOCAR would "
                   "pay 1.65bn in cash for non-operated working interests in the Legacy and Western "
                   "Haynesville assets plus part of its Pinnacle interest, against total book equity "
                   "of 2.58bn. A transaction pricing a partial interest at that level is direct "
                   "evidence that book value is not the measure of this asset base. Multiplying it "
                   "by a percentile of its own two-year P/B history would produce a target price "
                   "with no economic content, and §8's 'data availability is not economic "
                   "suitability' is exactly this situation.",
        },
        valuation_risk=(
            "The honest output is an abstention. The single computable method does not value a gas "
            "producer, and the methods that would - an enterprise multiple on EBITDA, or a reserve "
            "value - need a borrowing balance this repository cannot resolve and a reserve report it "
            "does not read.",
        ),
    ),
    MethodJudgement(
        ticker="DORM",
        business="Dorman Products: replacement and upgrade parts for the motor vehicle aftermarket, "
                 "roughly 144,000 distinct parts per D3, 5,560 of them introduced in 2025. TTM "
                 "revenue 2,155.0m, operating income 312.0m (14.5%), net income 219.3m, free cash "
                 "flow 214.2m, book equity 1,515.0m, market cap 3,708.3m, net debt 308.5m - 7.7% of "
                 "a 4,016.8m enterprise value.",
        primary_preference=("EV/EBIT", "EV/Sales"),
        secondary_preference=("EV/FCF", "EV/Sales"),
        unsuitable={},
        valuation_risk=(
            "Operating income is the anchor because it is the one measure that is neither flattered "
            "by working capital nor silent on margin, and D3 records the specific reason to distrust "
            "the trailing figure: management attributes part of the Q2 2026 result to recovery of "
            "IEEPA tariff costs recognised in earlier periods, which D3 reads as more likely a "
            "one-off than a durable run rate. A target multiple applied to a TTM operating income "
            "that contains a prior-period recovery prices the recovery as recurring.",
            "Free cash flow of 214.2m against operating income of 312.0m is a 69% conversion, which "
            "for a distributor holding 144,000 SKUs is inventory rather than a defect. It is why "
            "EV/FCF is the cross-check: the two methods read the same business through accrual "
            "earnings and through cash, and a disagreement between them is informative about "
            "working capital.",
            "P/B is in neither preference list and is not asserted unsuitable. A distributor does "
            "hold inventory and plant that earn money, so 2.45x book is a meaningful asset check in "
            "the way D5-D2 allowed for NATR - but with two earnings-side methods computable the "
            "question of whether book value could anchor this issuer never has to be answered, and "
            "asserting an answer it did not need would be an assertion without an argument.",
            "Derived EBITDA is unavailable: D&A refuses on a tag mismatch, so EV/EBITDA cannot be "
            "formed and the capital-intensity cross-check it would provide is absent.",
        ),
    ),
    MethodJudgement(
        ticker="FG",
        business="F&G Annuities & Life: fixed annuities, indexed annuities and pension risk "
                 "transfer, earning a spread on invested assets plus fee income from a "
                 "Blackstone-backed reinsurance sidecar and owned distribution. D3 records AUM "
                 "before reinsurance of 74.7bn and retained AUM of 55.9bn at June 2026. TTM revenue "
                 "6,067.0m, net income 418.0m, book equity 4,609.0m, market cap 3,018.1m - so P/B "
                 "is 0.655x. Operating income and free cash flow do not resolve at all, which D3 "
                 "lists among its own unknown fields.",
        primary_preference=("P/B",),
        secondary_preference=(),
        unsuitable={
            "EV/Sales": "an insurer's enterprise value is not a meaningful quantity. The bridge adds "
                        "net debt to the market cap and treats the result as the value of the "
                        "operating assets, but for a life and annuity writer the policyholder "
                        "liabilities ARE the business rather than financing for it, and they are not "
                        "in the bridge. The denominator is no better: 'revenue' here is premiums "
                        "plus net investment income plus realised and unrealised investment results, "
                        "so a 0.52x EV/Sales is partly a mark-to-market series. D5-D2 reached the "
                        "same conclusion from the other side for GNW - for an insurer book equity is "
                        "the earning asset - and the corollary is that the enterprise chain is not.",
        },
        valuation_risk=(
            "P/B is the primary method on business grounds, and the GNW precedent is the argument: "
            "for an insurer net assets back the reserves, the balance sheet is the business, and "
            "price-to-book is the method the industry values itself on. It is still a single method, "
            "so confidence is LOW by rule and there is no cross-check of any kind.",
            "A 0.655x multiple is the market's discount to stated book, and the discount may be "
            "right. A fair value computed from percentiles of that same discount asserts only that "
            "the market has paid those multiples of this book before; it does not assert that the "
            "book is correct. If the reserves or the alternative-investment marks are wrong, the "
            "denominator is wrong and every figure derived from it with it.",
            "An insurer's GAAP book value moves with accumulated other comprehensive income, so a "
            "P/B percentile measured across a period of interest-rate moves is partly a rate series "
            "and only partly a valuation series.",
            "D3 records two material unknowns that bear directly on the book value: the 2026 "
            "statutory RBC ratio and the holding-company cash position. Neither is in the "
            "multiple.",
        ),
    ),
    MethodJudgement(
        ticker="FRPT",
        business="Freshpet: refrigerated fresh pet food sold through company-owned branded fridges "
                 "placed in retail stores. TTM revenue 1,177.3m, operating income 95.4m (8.1%), net "
                 "income 203.5m, book equity 1,237.7m, market cap 2,981.0m. Capex does not resolve, "
                 "so there is no free cash flow, and no borrowing balance resolves, so there is no "
                 "enterprise value. P/B at 2.41x is the single computable multiple.",
        primary_preference=(),
        secondary_preference=(),
        unsuitable={
            "P/B": "the fridges and the plants are real earning assets, so this is not the "
                   "asset-light case and the objection is a different one. What the market pays "
                   "2.41x book for is growth - D3 records FY2026 net sales growth guidance raised to "
                   "10-12% and Q2 volume gains of 15.7% - and a percentile of this issuer's own P/B "
                   "history is therefore a percentile of how much growth the market was willing to "
                   "capitalise, which is a sentiment series rather than an asset series. As the sole "
                   "method, with no earnings or cash multiple computable to arbitrate it, it would "
                   "publish a target price whose entire content is a re-rating. D5-D2 allowed P/B as "
                   "a SECONDARY asset check for NATR for the same reason it is refused as a primary "
                   "here: it can corroborate a floor and it cannot carry an anchor.",
        },
        valuation_risk=(
            "The honest output is an abstention, and the cause is a missing operand rather than a "
            "missing business: net income of 203.5m exceeds operating income of 95.4m, which for "
            "this issuer is a non-operating item the TTM bundle does not decompose, and capex - the "
            "defining cost of a fridge-placement model - does not resolve at all. The methods that "
            "would value this issuer are the ones the data refuses.",
        ),
    ),
    MethodJudgement(
        ticker="GOOG",
        business="Alphabet: online advertising - more than 70% of 2025 revenue per D3 - plus Google "
                 "Cloud, whose Q2 2026 revenue grew 82% to 24.8bn on enterprise AI infrastructure "
                 "and solutions, against a Q1 2026 Cloud backlog above 460bn. Book equity "
                 "640,480m. No multiple resolves: the share class does not resolve at the decision "
                 "session, so every method refuses MULTI_CLASS_UNRESOLVED.",
        primary_preference=(),
        secondary_preference=(),
        unsuitable={},
        valuation_risk=(
            "There is no valuation to carry a risk, and the reason is structural rather than "
            "economic: this issuer has Class A, Class B and Class C shares, D5-P1.1's resolution "
            "returns UNRESOLVED topology at LOW confidence, and a market cap computed from one "
            "class's count would be wrong by the size of the others. D3 lists "
            "`shares_outstanding_code_owned` among its own unknown fields, so both layers agree "
            "about what is missing. Repairing share-class resolution is a primitive project and is "
            "out of scope for this step.",
        ),
    ),
    MethodJudgement(
        ticker="IDCC",
        business="InterDigital: patent licensing, with 93% of 2025 revenue from fixed-fee licence "
                 "agreements per D3 and ARR of 625.7m. TTM revenue 788.5m, operating income 345.1m "
                 "(43.8%), net income 302.2m, free cash flow 554.6m (70.3% of revenue), book equity "
                 "1,202.2m, market cap 8,483.0m, net debt -226.5m - net cash, so the enterprise "
                 "value of 8,256.5m is 97.3% of the market cap. Six of seven methods compute, the "
                 "richest coverage in this sample.",
        primary_preference=("EV/EBIT", "EV/EBITDA"),
        secondary_preference=("EV/Sales", "EV/FCF"),
        unsuitable={
            "P/B": "a patent portfolio is carried at the cost of filing and acquiring it, less "
                   "amortisation, and what it is worth is the licence revenue it can compel. Those "
                   "two numbers have no relationship: 7.06x book says the market values the "
                   "portfolio at seven times what it cost to assemble, which is a fact about "
                   "accounting for intangibles rather than a valuation. D5-D0 §N's asset-light case, "
                   "in its clearest form in this sample.",
        },
        valuation_risk=(
            "Revenue is lumpy by construction and that is the central risk to every multiple here. "
            "A new fixed-fee agreement brings catch-up revenue for past unlicensed periods, so a "
            "trailing twelve months either contains such a settlement or does not. D3 records the "
            "full-year 2026 outlook of 775-845m as explicitly assuming contributions from new "
            "agreements or enforcement actions not yet signed. Operating income is the anchor "
            "because the revenue-recognition treatment of fixed-fee licences smooths more of this "
            "than cash collections do, not because it removes it.",
            "Free cash flow of 554.6m exceeds operating income of 345.1m, which for a licensor is "
            "the timing of contracted cash against the periods it is recognised in - D3 records "
            "about 1.5bn of contracted future payments. So EV/FCF reads cheaper than EV/EBIT on the "
            "same business, and the gap is collections timing rather than margin.",
            "Two outcomes outside the financial statements dominate the forward economics: the "
            "Amazon agreement whose final terms are to be set by binding arbitration, and the Lenovo "
            "arbitration. A valuation anchored to trailing operating income prices neither, and D4 "
            "is the layer that would read the market's expectation of them.",
        ),
    ),
    MethodJudgement(
        ticker="SCCO",
        business="Southern Copper: copper mining in Peru and Mexico, about 75.9% of revenue from "
                 "copper with molybdenum and silver the rest, per D3. TTM revenue 15,787.5m, "
                 "operating income 8,982.8m (56.9%), net income 5,678.9m, free cash flow 5,100.4m, "
                 "book equity 12,632.2m, market cap 158,422.9m. No borrowing balance resolves at the "
                 "cash date, so there is no enterprise value and every enterprise method is "
                 "unavailable.",
        primary_preference=("P/FCF",),
        secondary_preference=(),
        unsuitable={
            "P/B": "12.54x book is the gap between what an ore body cost to develop and what the "
                   "market thinks the metal in it is worth. A miner's book equity is historical "
                   "development cost less depletion; the reserve is not on the balance sheet at any "
                   "value related to the copper price. A percentile of this issuer's own P/B history "
                   "would be a copper-price series with a balance-sheet denominator attached.",
        },
        valuation_risk=(
            "The denominator is a commodity price in disguise and this is the sharpest instance in "
            "the sample of D5-D2's §N.1 limitation. A 56.9% operating margin and 5,100.4m of free "
            "cash flow are what this asset base produces at the copper price that prevailed over the "
            "trailing year, and D3 records the company attributing 2Q26 sales growth primarily to "
            "higher metal prices while copper volumes fell, and stating it cannot predict whether "
            "prices will rise or fall. A P/FCF percentile over a panel in which the share price went "
            "from 99.18 to 189.88 is partly a measure of how much of the cycle the market had "
            "already capitalised, and no window choice makes that not so.",
            "Single method, so confidence is LOW by rule: there is nothing to cross-check the cash "
            "multiple against, and the methods that would - an enterprise multiple on operating "
            "income or EBITDA - need a borrowing balance that does not resolve even though this "
            "issuer plainly carries debt. The absence forces an equity-side read and is recorded "
            "rather than worked around.",
            "D3 records Tia Maria at 42% completion with 693m of 1,101m committed and invested, and "
            "no production start, revenue or offtake. A trailing cash-flow multiple prices none of "
            "that: the capex is in the denominator as a cost and the future production is nowhere.",
        ),
    ),
    MethodJudgement(
        ticker="SPSC",
        business="SPS Commerce: subscription supply-chain integration, 96% of 2025 revenue recurring "
                 "per D3. TTM revenue 772.5m, operating income 98.8m (12.8%), net income 78.0m, free "
                 "cash flow 198.7m (25.7% of revenue), book equity 938.5m, market cap 2,904.5m. No "
                 "borrowing balance resolves, so there is no enterprise value; D3 lists "
                 "`balance_sheet.total_debt` among its unknown fields.",
        primary_preference=("P/FCF",),
        secondary_preference=(),
        unsuitable={
            "P/B": "book equity is cash plus goodwill from a series of acquisitions, against a "
                   "recurring-revenue network whose value is the retailer-supplier connections it "
                   "sits on. 3.09x book prices the accounting for the acquisitions, not the network. "
                   "D5-D0 §N's asset-light case.",
        },
        valuation_risk=(
            "Free cash flow of 198.7m against operating income of 98.8m is the usual "
            "subscription-software pattern - deferred revenue collected ahead of recognition, plus "
            "non-cash charges - and it means the cash multiple reads roughly twice as cheap as an "
            "earnings multiple would on the same business. With no second method computable there is "
            "nothing to arbitrate that, and confidence is LOW by rule.",
            "The growth that justified the historical multiple is slowing: D3 records recurring "
            "revenue up 6% year over year and classifies growth durability MIXED, and the share "
            "price fell from 192.18 to 80.68 across the panel. A P/FCF percentile drawn from that "
            "panel spans two different expectations about the same business.",
            "D3 records a 3P Revenue Recovery divestiture completed in the period and lists revenue, "
            "operating income, net income and EPS as code-owned AMBIGUOUS. A trailing free cash flow "
            "spanning a divestiture is not a clean run rate for the business that remains.",
        ),
    ),
    MethodJudgement(
        ticker="TG",
        business="Tredegar: custom aluminium extrusions for North American building, construction "
                 "and automotive markets, plus surface-protection films for electronics. TTM revenue "
                 "and operating income do not resolve - D3 lists both as code-owned MISSING - so "
                 "there is no sales or earnings method. Net income 33.3m, free cash flow 22.6m, book "
                 "equity 228.6m, market cap 240.8m, net debt 28.8m, enterprise value 269.6m, so net "
                 "debt is 10.7% of EV.",
        primary_preference=("EV/FCF", "P/FCF"),
        secondary_preference=("P/B",),
        unsuitable={},
        valuation_risk=(
            "The trailing free cash flow is flattered by an effect management itself calls "
            "temporary. D3 records the company expecting the benefit from FIFO inventory positions "
            "and metal price trends to be substantially neutralised during the third quarter, and "
            "describes it as a timing mismatch between raw material costs and the pass-through to "
            "customers. An 11.9x EV/FCF built on that denominator prices a tailwind the issuer has "
            "said is ending.",
            "EV/FCF is the primary rather than P/FCF because net debt is 10.7% of enterprise value, "
            "which is small but not nothing: the equity-side multiple reads 10.6x against 11.9x, and "
            "the enterprise chain asks the question at the point in the capital structure where the "
            "cash is actually generated. The two share the free-cash-flow denominator, so the pair "
            "is one fundamental read at two points rather than two readings, which is why P/B rather "
            "than P/FCF is the declared secondary.",
            "P/B at 1.05x is the secondary, and the NATR precedent is the reason it is not asserted "
            "unsuitable: an aluminium extruder's plant genuinely earns the money, and book value "
            "roughly equal to market cap is a meaningful floor check on a commodity converter. It "
            "corroborates a floor and D5-D0 forbids promoting it to the anchor.",
            "Revenue and operating income do not resolve at all, so no margin regime is observable "
            "for this issuer. Whether 11.9x cash flow is appropriate depends on a margin this "
            "repository cannot measure - the same structural limitation D5-D2 recorded for COHR.",
        ),
    ),
    MethodJudgement(
        ticker="VRRM",
        business="Verra Mobility: tolling services for rental fleets, automated photo enforcement "
                 "for municipalities, and title and registration services. TTM revenue 1,007.0m, "
                 "operating income 136.9m (13.6%), net income 44.3m, D&A 115.2m, free cash flow "
                 "96.9m, book equity 223.6m, market cap 538.0m, net debt 985.1m - 64.7% of a "
                 "1,523.1m enterprise value. The highest leverage in this sample by a wide margin.",
        primary_preference=("EV/EBIT", "EV/Sales"),
        secondary_preference=("EV/Sales", "EV/EBITDA"),
        unsuitable={
            "P/B": "book equity of 223.6m is the residue of the 2018 combination's purchase "
                   "accounting against 985.1m of net debt, so an equity-side multiple of it asks "
                   "what the market pays for a stub levered more than four times its own book. The "
                   "answer says more about the leverage than about the camera network, and D5-D2's "
                   "WBD argument applies here with net debt at 65% of EV rather than 29%.",
            "P/FCF": "5.55x free cash flow looks like the cheapest multiple in this sample and it is "
                     "a leverage artifact. With net debt at 64.7% of enterprise value, equity free "
                     "cash flow is what is left after the debt service on an enterprise worth three "
                     "times the equity, so the multiple compresses as the leverage rises. EV/FCF "
                     "reads 15.72x on the identical cash flow - the same fundamental read at the "
                     "right point in the capital structure - and that 2.8x spread between the pair "
                     "is the measure of how much of the equity multiple is gearing.",
        },
        valuation_risk=(
            "This issuer's price fell from 26.99 at the start of the panel to 3.54 at the decision "
            "session, which IS the panel minimum, so every percentile of every observed multiple "
            "lies above the current one and any window produces upside mechanically. That is not a "
            "finding about Verra Mobility, it is the shape of the arithmetic, and it is the single "
            "most important thing to hold in mind about this row. D5-D2's window rule exists for "
            "exactly this case and what it does here is reported rather than assumed.",
            "At 64.7% net debt the equity is a geared claim on the enterprise, so a change in the "
            "enterprise multiple moves the equity value by roughly 2.8 times as much. The Bear "
            "anchor is a statement about that geared stub and not about the business, and a Bear "
            "multiple that compresses the enterprise modestly can take the equity most of the way "
            "to nothing. D5-D2's §N.2 limitation - a re-rating-only Bear does not price a "
            "fundamental deterioration - is at its most dangerous on this capital structure.",
            "D3 records the deterioration the price is reacting to and the valuation cannot see: two "
            "significant Commercial Services customers signed seven- and five-year extensions on "
            "terms materially less favourable to the company, including fleet volume modulation "
            "rights, and a third renewal is outstanding. A target multiple applied to trailing "
            "operating income prices the old contract terms.",
            "Derived EBITDA is 136.9m of operating income plus 115.2m of D&A, so EV/EBITDA at 6.04x "
            "is positive largely because of depreciation on the camera and toll infrastructure that "
            "has to be replaced. For a capital-intensive services business that add-back is a real "
            "cost, which is why EV/EBIT is the anchor and EV/EBITDA is at best a third reading.",
        ),
    ),
)}


# -------------------------------------------------------------------------------------------------
# Reading the stored D3 / D4 artifacts
# -------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class StoredLegs:
    d3: D3Evidence
    d4: D4Evidence
    d4_tier: str | None


def _latest_record(directory: Path, ticker: str) -> dict | None:
    """The one analysis or attempt record for `ticker` in a run directory, or None.

    Records are named `<TICKER>-<uuid>.json`. A run directory holds at most one per issuer in every
    artifact this step reads; where more than one exists the newest by modification time is taken and
    the choice is recorded in the report rather than left implicit.
    """
    folder = directory / ticker
    if not folder.is_dir():
        return None
    candidates = sorted((p for p in folder.iterdir()
                         if p.name.startswith(f"{ticker}-") and p.suffix == ".json"),
                        key=lambda p: p.stat().st_mtime)
    if not candidates:
        return None
    return json.loads(candidates[-1].read_text())


def _claims_are_sourced(entries: Sequence[dict]) -> bool:
    """Every entry carries a source id somewhere in its claim structure (D0 §I's anchoring rule)."""
    def sourced(obj: object) -> bool:
        if isinstance(obj, dict):
            if obj.get("source_id"):
                return True
            return any(sourced(v) for v in obj.values())
        if isinstance(obj, list):
            return any(sourced(v) for v in obj)
        return False
    return bool(entries) and all(sourced(entry) for entry in entries)


_JJ_EVIDENCE_FLAGS: tuple[str, ...] = (
    "current_revenue_evidence", "order_backlog_evidence", "customer_evidence",
    "capacity_evidence", "margin_evidence")


def project_d3(ticker: str, record: dict | None) -> D3Evidence:
    """A stored `h_research_interpretation_v2` reduced to what D0 §N1 asks about it."""
    if record is None:
        return D3Evidence(ticker=ticker, provenance=D3Provenance.NOT_EVALUATED, research_id=None,
                          research_output_checksum=None, research_completeness=None,
                          business_model_populated=False, fundamental_change_count=0,
                          fundamental_change_anchored=False, growth_durability_state=None,
                          future_business_stages=(), future_business_evidence_present=False,
                          catalyst_count=0, catalyst_material_and_timed=False,
                          competitive_position_count=0, risk_count=0, invalidation_count=0,
                          unknown_fields=(), evidence_conflict_count=0)
    out = record.get("final_output")
    if out is None:
        return D3Evidence(ticker=ticker, provenance=D3Provenance.REFUSED_NO_FINAL_OUTPUT,
                          research_id=None, research_output_checksum=None,
                          research_completeness=None, business_model_populated=False,
                          fundamental_change_count=0, fundamental_change_anchored=False,
                          growth_durability_state=None, future_business_stages=(),
                          future_business_evidence_present=False, catalyst_count=0,
                          catalyst_material_and_timed=False, competitive_position_count=0,
                          risk_count=0, invalidation_count=0, unknown_fields=(),
                          evidence_conflict_count=0)

    future = out.get("future_business") or []
    catalysts = out.get("catalyst_candidates") or []
    return D3Evidence(
        ticker=ticker,
        provenance=D3Provenance.VALID,
        research_id=out.get("research_id"),
        research_output_checksum=record.get("final_output_checksum"),
        research_completeness=out.get("research_completeness"),
        business_model_populated=bool((out.get("business_model") or {}).get("revenue_drivers")),
        fundamental_change_count=len(out.get("fundamental_change") or []),
        fundamental_change_anchored=_claims_are_sourced(out.get("fundamental_change") or []),
        growth_durability_state=(out.get("growth_durability") or {}).get("state"),
        future_business_stages=tuple(entry.get("stage") for entry in future),
        future_business_evidence_present=any(
            any(entry.get(flag) for flag in _JJ_EVIDENCE_FLAGS) for entry in future),
        catalyst_count=len(catalysts),
        catalyst_material_and_timed=any(
            entry.get("materiality_candidate") in {"HIGH", "MEDIUM"}
            and entry.get("timing_confidence") in {"HIGH", "MEDIUM"} for entry in catalysts),
        competitive_position_count=len(out.get("competitive_position") or []),
        risk_count=len(out.get("risks") or []),
        invalidation_count=len(out.get("invalidation_candidates") or []),
        unknown_fields=tuple(out.get("unknown_fields") or []),
        evidence_conflict_count=len(out.get("evidence_conflicts") or []),
    )


def project_d4(ticker: str, record: dict | None) -> D4Evidence:
    """A stored `h_expectation_gap_analysis_v2` reduced to D0 §L's two fields plus provenance.

    The three provenance states are the point of this function. `NOT_EVALUATED` means no record
    exists; `REFUSED_NO_FINAL_OUTPUT` means D4 ran and its contract declined every attempt, which is
    FRPT's and SPSC's case in Tier B; `EVALUATED` means a final output exists. None of the three is
    converted into a gap state - see `D4_ABSENCE_IS_NOT_A_READING`.
    """
    if record is None:
        return D4Evidence(ticker=ticker, provenance=D4Provenance.NOT_EVALUATED, analysis_id=None,
                          final_output_checksum=None, research_input_id=None,
                          research_input_checksum=None, gap=None, gap_confidence=None,
                          confidence_ceiling=None, d6_approve_precondition=None, conflict_count=0,
                          unknown_field_count=0, terminal_failure_codes=())
    out = record.get("final_output")
    if out is None:
        return D4Evidence(
            ticker=ticker, provenance=D4Provenance.REFUSED_NO_FINAL_OUTPUT,
            analysis_id=record.get("analysis_id"), final_output_checksum=None,
            research_input_id=record.get("research_input_id"),
            research_input_checksum=record.get("research_output_checksum"), gap=None,
            gap_confidence=None, confidence_ceiling=None, d6_approve_precondition=None,
            conflict_count=0, unknown_field_count=0,
            terminal_failure_codes=tuple(record.get("initial_failure_codes") or ()))
    return D4Evidence(
        ticker=ticker,
        provenance=D4Provenance.EVALUATED,
        analysis_id=out.get("analysis_id"),
        final_output_checksum=record.get("final_output_checksum"),
        research_input_id=record.get("research_input_id"),
        research_input_checksum=record.get("research_output_checksum"),
        gap=ExpectationGap(out["expectation_gap"]),
        gap_confidence=Confidence(out["expectation_gap_confidence"]),
        confidence_ceiling=out.get("confidence_ceiling"),
        d6_approve_precondition=out.get("d6_approve_precondition"),
        conflict_count=len(out.get("conflicts") or []),
        unknown_field_count=len(out.get("unknown_fields") or []),
        terminal_failure_codes=tuple(record.get("terminal_failure_codes") or ()),
    )


def load_legs(root: Path = RUNTIME_ROOT) -> dict[str, StoredLegs]:
    """The authoritative D3 and D4 artifact for each of the thirteen, from the runs §A names."""
    legs: dict[str, StoredLegs] = {}
    for tier, d4_dir, d3_dir in D4_RUNS:
        d4_path, d3_path = root / d4_dir, root / d3_dir
        if not d4_path.is_dir():
            continue
        for ticker in sorted(p.name for p in d4_path.iterdir() if p.is_dir()):
            if ticker not in D4_UNIVERSE:
                continue
            legs[ticker] = StoredLegs(
                d3=project_d3(ticker, _latest_record(d3_path, ticker)),
                d4=project_d4(ticker, _latest_record(d4_path, ticker)),
                d4_tier=tier)
    for ticker in D4_UNIVERSE:
        legs.setdefault(ticker, StoredLegs(d3=project_d3(ticker, None),
                                           d4=project_d4(ticker, None), d4_tier=None))
    return legs


# -------------------------------------------------------------------------------------------------
# D5, on D4's thirteen
# -------------------------------------------------------------------------------------------------

def project_d5(row: dict) -> D5Evidence:
    """A D5-D2 valuation row, carried into the decision engine without touching a number."""
    targets = row.get("target_prices") or {}
    return D5Evidence(
        ticker=row["ticker"],
        status=row["status"],
        confidence=row["confidence"]["confidence"],
        primary_method=row.get("primary_method"),
        secondary_method=row.get("secondary_method"),
        contract_window=row.get("contract_window"),
        current_price=row.get("current_price"),
        tp1=targets.get("TP1"),
        tp2=targets.get("TP2"),
        bear_anchor=targets.get("BEAR_ANCHOR"),
        upside_to_tp1=targets.get("upside_to_TP1"),
        upside_to_tp2=targets.get("upside_to_TP2"),
        downside_to_bear=targets.get("downside_to_bear"),
        reconciliation=(row.get("reconciliation") or {}).get("status"),
        confidence_drivers=tuple(row["confidence"].get("drivers") or ()),
        valuation_risk=tuple(row.get("valuation_risk") or ()),
    )


def value_universe(tickers: Sequence[str]) -> tuple[list[dict], dict, dict, list[dict]]:
    """D5-D2's evaluator over `tickers`, with this step's declared judgements.

    Returns the valuation rows, the panels, the decision-session resolutions and the per-issuer panel
    coverage. `build_panel`, `resolve_session` and `value_issuer` are D5-D2's functions; the only
    difference from a D5-D2 run is the `judgements` table and the issuer list.
    """
    closes_panel = load_price_panel(tickers)
    panels: dict[str, dict] = {}
    decision_rows: dict = {}
    coverage: list[dict] = []
    rows: list[dict] = []
    for ticker in tickers:
        panel, decision, cover = build_panel(ticker, closes_panel[ticker], DECISION_SESSION)
        coverage.append(cover)
        if decision is None:
            continue
        panels[ticker] = panel
        decision_rows[ticker] = decision
        rows.append(value_issuer(ticker, panel, decision, judgements=METHOD_JUDGEMENTS,
                                 window_contract=WINDOW_CONTRACT))
    return rows, panels, decision_rows, coverage


# -------------------------------------------------------------------------------------------------
# The run
# -------------------------------------------------------------------------------------------------

def run() -> dict:
    legs = load_legs()
    rows, panels, decision_rows, coverage = value_universe(D4_UNIVERSE)
    by_ticker = {row["ticker"]: row for row in rows}

    d5_evidence: dict[str, D5Evidence] = {}
    d5_published: dict[str, dict] = {}
    records: list[DecisionRecord] = []
    for ticker in D4_UNIVERSE:
        row = by_ticker.get(ticker)
        if row is None:
            d5 = D5Evidence(ticker=ticker, status="VALUATION_NOT_READY", confidence="NOT_READY",
                            primary_method=None, secondary_method=None, contract_window=None,
                            current_price=None, tp1=None, tp2=None, bear_anchor=None,
                            upside_to_tp1=None, upside_to_tp2=None, downside_to_bear=None,
                            reconciliation=None)
        else:
            d5 = project_d5(row)
            d5_published[ticker] = dict(row.get("target_prices") or {})
            d5_published[ticker]["current_price"] = row.get("current_price")
        d5_evidence[ticker] = d5
        records.append(decide(legs[ticker].d3, legs[ticker].d4, d5))

    eligible = [r for r in records if r.eligibility.eligibility is Eligibility.DECISION_ELIGIBLE]
    counts = {d.value: sum(1 for r in records if r.decision is d) for d in Decision}
    precondition_disagreements = [
        d for d in (legs[t].d4.precondition_disagreement for t in D4_UNIVERSE) if d]
    chain_breaks = {t: legs[t].d4.chain_break(legs[t].d3) for t in D4_UNIVERSE}
    chain_breaks = {t: reason for t, reason in chain_breaks.items() if reason}

    report = {
        "contract": D6_CONTRACT,
        "decision_contract_version": D6_CONTRACT_VERSION,
        "upstream_contracts": list(UPSTREAM_CONTRACTS),
        "decision_session": DECISION_SESSION.isoformat(),
        "d4_universe": list(D4_UNIVERSE),
        "d4_runs": [{"tier": tier, "d4": d4, "d3": d3} for tier, d4, d3 in D4_RUNS],
        "coverage": coverage,
        "d5_rows": rows,
        "d5_valued": [r["ticker"] for r in rows
                      if r["status"] == ValuationStatus.VALUED.value],
        "d5_valuation_not_ready": [t for t in D4_UNIVERSE
                                   if t not in {r["ticker"] for r in rows
                                                if r["status"] == ValuationStatus.VALUED.value}],
        "d4_provenance": {t: legs[t].d4.provenance.value for t in D4_UNIVERSE},
        "d4_gap": {t: (None if legs[t].d4.gap is None else legs[t].d4.gap.value)
                   for t in D4_UNIVERSE},
        "d4_gap_confidence": {t: (None if legs[t].d4.gap_confidence is None
                                  else legs[t].d4.gap_confidence.value) for t in D4_UNIVERSE},
        "d3_provenance": {t: legs[t].d3.provenance.value for t in D4_UNIVERSE},
        "decision_eligible": [r.ticker for r in eligible],
        "not_decision_eligible": {r.ticker: list(r.eligibility.blockers) for r in records
                                  if r.eligibility.eligibility
                                  is Eligibility.NOT_DECISION_ELIGIBLE},
        "decisions": [r.to_dict() for r in records],
        "decision_counts": counts,
        "d4_precondition_disagreements": precondition_disagreements,
        "d5_defects": d5_audit(rows, panels, decision_rows),
        "d6_defects": audit_decisions(records, d5_evidence, d5_published, chain_breaks),
        "provenance_chain": {t: {"d3_research_id": legs[t].d3.research_id,
                                 "d4_consumed_research_input_id": legs[t].d4.research_input_id,
                                 "d3_output_checksum": legs[t].d3.research_output_checksum,
                                 "d4_consumed_research_checksum":
                                     legs[t].d4.research_input_checksum,
                                 "d4_output_checksum": legs[t].d4.final_output_checksum}
                             for t in D4_UNIVERSE},
        "approve_clauses": [{"name": n, "contract_reference": r} for n, r in APPROVE_CLAUSES],
        "reject_triggers": [{"name": n, "contract_reference": r, "mechanically_evaluable": m}
                            for n, r, m in REJECT_TRIGGERS],
        "model_calls": 0,
        "cost_usd": 0.0,
        "selection_effect": SELECTION_EFFECT,
        "out_of_pilot_application": OUT_OF_PILOT_APPLICATION,
        "no_forward_returns": NO_FORWARD_RETURNS,
        "d6_owns_no_numbers": D6_OWNS_NO_NUMBERS,
        "d6_never_produced": list(D6_NEVER_PRODUCED),
        "watch_is_the_abstention": WATCH_IS_THE_ABSTENTION,
        "d4_absence_is_not_a_reading": D4_ABSENCE_IS_NOT_A_READING,
        "valuation_does_not_override": VALUATION_DOES_NOT_OVERRIDE,
        "not_mechanically_evaluable": NOT_MECHANICALLY_EVALUABLE,
        "window_rule": WINDOW_RULE,
    }
    return report


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x:+.1%}"


def _num(x: float | None, nd: int = 2) -> str:
    return "-" if x is None else f"{x:,.{nd}f}"


def main(argv: list[str]) -> None:
    report = run()
    if "--json" in argv:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        out = OUTPUT_DIR / f"D6-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
        out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str))
        print(f"wrote {out}")

    print(f"\nH-V2-D6  decision session {report['decision_session']}  "
          f"model calls {report['model_calls']}  cost ${report['cost_usd']:.2f}")
    print(f"D4 universe {len(report['d4_universe'])}  "
          f"D5 valuation ready {len(report['d5_valued'])}: {', '.join(report['d5_valued'])}")
    print(f"D6 decision eligible {len(report['decision_eligible'])}: "
          f"{', '.join(report['decision_eligible'])}")
    print(f"decisions {report['decision_counts']}")

    rows = {r["ticker"]: r for r in report["d5_rows"]}
    print("\ntick  D4 gap        conf     D5     prim      win          price     TP1    upTP1"
          "     TP2    upTP2  vconf      decision")
    for record in report["decisions"]:
        t = record["ticker"]
        row = rows.get(t)
        tp = (row or {}).get("target_prices") or {}
        conf = (row or {}).get("confidence", {}).get("confidence", "NOT_READY")
        status = "VALUED" if (row or {}).get("status") == "VALUED" else "NOT_READY"
        print(f"{t:<5} {str(report['d4_gap'][t]):<13} "
              f"{str(report['d4_gap_confidence'][t]):<8} {status:<10} "
              f"{str((row or {}).get('primary_method') or '-'):<9} "
              f"{str((row or {}).get('contract_window') or '-'):<11} "
              f"{_num((row or {}).get('current_price')):>8} {_num(tp.get('TP1')):>7} "
              f"{_pct(tp.get('upside_to_TP1')):>7} {_num(tp.get('TP2')):>7} "
              f"{_pct(tp.get('upside_to_TP2')):>7} {conf:<10} "
              f"{record['decision'] or 'NOT_DECISION_ELIGIBLE'}")

    print("\nnot decision eligible:")
    for ticker, blockers in sorted(report["not_decision_eligible"].items()):
        print(f"  {ticker}: {'; '.join(blockers)}")

    print("\nD5 defects:")
    for name, offenders in report["d5_defects"].items():
        print(f"  {name:<52} {offenders if offenders else 0}")
    print("D6 defects:")
    for name, offenders in report["d6_defects"].items():
        print(f"  {name:<52} {offenders if offenders else 0}")
    print(f"D4 precondition disagreements: "
          f"{report['d4_precondition_disagreements'] or 0}")


if __name__ == "__main__":
    main(sys.argv[1:])
