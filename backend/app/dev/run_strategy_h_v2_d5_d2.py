"""H-V2-D5-D2: the fair value / valuation framework pilot.

Offline, deterministic, 0 model calls, $0. It reads local SEC companyfacts, the stored D2.1
packages, the local Polygon reference-ticker snapshots and the local unadjusted grouped-daily panel.
Nothing is fetched and nothing is written outside `data/runtime/strategy_h_v2/d5_d2`.

What is new here, and it is the whole step: D5-D1 published the multiple the market pays today. This
builds the same issuer's multiple at every session of the local two-year panel, as it would have been
observed on that session, and then uses those observations - and only those - as the target multiples
for a Bear / Base / Bull fair value, TP1 and TP2.

**The point-in-time panel.** For each session the primitives are re-resolved with the decision time
set to that session, so a filing accepted afterwards cannot reach it: `resolve_fact` admits a fact
only when `accepted_at <= decision_time`, and `load_reference_snapshot` refuses a share-class
snapshot published after the decision date for the same reason. The panel is therefore a series of
multiples that were observable at the time, not today's fundamentals priced at yesterday's prices -
which would be a price series wearing a multiple's name. The check that this is wired correctly is
that the panel's last session reproduces D5-D1's published multiple for every method and issuer,
bit for bit; `d5_d1_agreement` asserts it.

**What code owns and what the model owns.** Code owns every number: the panel, the percentiles, the
per-share metric, the implied enterprise and equity values, the fair values, TP1, TP2 and every
upside. The model owns exactly two things, both textual, both recorded in `METHOD_JUDGEMENTS` in
this file: whether a computable method is economically suitable for that business, and which method
should be preferred as the primary anchor. No model output is a number, and no number here passes
through a model.

**Why the judgements are a preference ORDER and not a choice.** A model that picked one method per
issuer after seeing the fair values would be fitting, and the fitting would be invisible afterwards.
So each issuer declares an ordered preference over methods on business grounds, code takes the first
one whose observed panel is contract-eligible, and the window is chosen by one rule applied to every
issuer identically - see `WINDOW_RULE`. Every window's fair value is published for every issuer
regardless, so what the rule chose and what it passed over are both on the table.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from app.backtest.strategy_h0.facts import CanonicalFact
from app.backtest.strategy_h_v2.valuation.capital_structure import (
    CAPITAL_STRUCTURE_FIELD_SPECS,
    resolve_net_debt,
    resolve_valuation_instant,
)
from app.backtest.strategy_h_v2.valuation.fair_value import (
    FAIR_VALUE_CONTRACT_VERSION,
    GUIDANCE_UNAVAILABLE,
    FairValueRange,
    MetricChain,
    MultipleObservation,
    PEER_CONTEXT_UNAVAILABLE,
    PanelWindow,
    ReconciliationStatus,
    Scenario,
    TrendClass,
    ValuationConfidence,
    ValuationStatus,
    identity_residual,
    metric_chain,
    methods_are_correlated,
    reconcile,
    scenario_value,
    target_prices,
    valuation_confidence,
    window_stats,
)
from app.backtest.strategy_h_v2.valuation.multiples import (
    METHOD_ORDER,
    MultipleStatus,
    compute_multiples,
)
from app.backtest.strategy_h_v2.valuation.share_class import (
    load_reference_snapshot,
    reopened_enterprise_value,
    resolve_market_cap,
    resolve_share_class,
)
from app.backtest.strategy_h_v2.valuation.ttm import construct_ttm_bundle
from app.dev.audit_strategy_h_v2_d5_p1 import load_issuer
from app.dev.run_strategy_h_v2_d4_1 import load_price_panel
from app.dev.run_strategy_h_v2_d5_d1 import _market_opens
from app.dev.run_strategy_h_v2_d5_d1 import run as run_d5_d1

D5_D2_CONTRACT = "h_v2_d5_d2_fair_value_framework_pilot_v1"
OUTPUT_DIR = Path("data/runtime/strategy_h_v2/d5_d2")

#: The decision session is the panel's last, which is also D5-D1's. Not a parameter: a pilot whose
#: decision date could be moved is a pilot whose coverage could be chosen.
DECISION_SESSION = date(2026, 9, 16)

#: §2's design candidates and §2's abstention candidates, in the brief's order. The five remaining
#: D5-D1 issuers produce no multiple at all and §2 forbids forcing a valuation onto them; they are
#: carried in the report as `NO_MULTIPLE_AT_D5_D1` and nothing about their D5-D1 row is restated.
DESIGN_CANDIDATES: tuple[str, ...] = ("ADBE", "WBD", "COHR", "NATR")
ABSTENTION_CANDIDATES: tuple[str, ...] = ("CHRS", "STAA", "GNW")
PILOT_ISSUERS: tuple[str, ...] = DESIGN_CANDIDATES + ABSTENTION_CANDIDATES

NOT_RECLASSIFIED = (
    "No D5-D1 row is modified, re-run with different bounds, or reclassified. The five issuers that "
    "produced no multiple there produce none here either, for the identical reason, and §2 of this "
    "brief forbids both forcing a valuation onto them and editing their readiness. D5-D1's "
    "READY / READY_WITH_LIMITATIONS / NOT_READY of 4 / 3 / 5 is quoted in this step's report and is "
    "not recomputed."
)


# -------------------------------------------------------------------------------------------------
# Model-owned judgements (§9). Text only - no number below is used in any calculation.
# -------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class MethodJudgement:
    """One issuer's economic reading of its own computable methods.

    `primary_preference` and `secondary_preference` are ORDERED. Code takes the first entry whose
    observed panel is contract-eligible, so the selection is reproducible from this table plus the
    panel, and cannot have been steered by a fair value that had not been computed yet.

    `unsuitable` is the `MethodSuitability.NOT_SUITABLE` that D5-D1's `NOT_SUITABLE_IS_AI_OWNED`
    reserved for this step: code there declined to PROMOTE a method and explicitly refused to assert
    that one was wrong, because "whether book value is this issuer's earning asset is not a question
    the balance sheet answers about itself". These entries are that assertion, made where the
    business supports it and nowhere else.
    """

    ticker: str
    business: str
    primary_preference: tuple[str, ...]
    secondary_preference: tuple[str, ...]
    unsuitable: Mapping[str, str]
    valuation_risk: tuple[str, ...]


METHOD_JUDGEMENTS: Mapping[str, MethodJudgement] = {j.ticker: j for j in (
    MethodJudgement(
        ticker="ADBE",
        business="Subscription creative and document software. Asset-light: the earning assets are "
                 "the installed base, the file formats and the brand, and almost none of them is on "
                 "the balance sheet. TTM revenue 25.2bn at a 36% operating margin, free cash flow "
                 "10.3bn (41% of revenue), and net debt of -117m against a 99.5bn enterprise value.",
        primary_preference=("P/FCF", "EV/EBIT"),
        secondary_preference=("EV/EBIT", "EV/EBITDA", "EV/Sales"),
        unsuitable={
            "P/B": "book equity of 11.5bn supports 25.2bn of revenue at a 36% margin, which is the "
                   "arithmetic signature of an issuer whose earning assets are not capitalised. A "
                   "P/B of 8.65x is not a valuation of Adobe, it is a measure of how little of "
                   "Adobe is on its balance sheet. This is D5-D0 §N's named case: for an "
                   "asset-light issuer P/B is NOT_APPLICABLE on its own terms.",
            "EV/FCF": "divides the identical free cash flow as P/FCF against an enterprise value "
                      "that is the market cap to within 0.1%. D5-D1 measured the pair at 9.6748x "
                      "and 9.6862x. It is not an independent cross-check and §25 forbids counting "
                      "it as one.",
        },
        valuation_risk=(
            "The de-rating is the story and it is not a technical artifact: the observed P/FCF fell "
            "from the low 30s to under 10x across the panel without free cash flow falling, which "
            "is the market repricing the durability of the cash flow rather than the cash flow "
            "itself. Any Base multiple drawn from the early panel assumes that repricing was wrong.",
            "A generative-AI displacement thesis is the stated reason for the compression and it is "
            "a claim about future revenue, not about the trailing 10.3bn of free cash flow. Nothing "
            "in D5 can evaluate it: there is no D3 research or D4 expectation gap for ADBE.",
            "Free cash flow is flattered relative to operating income by non-cash share-based "
            "compensation, so P/FCF reads cheaper than EV/EBIT on the same business. That is why "
            "EV/EBIT is the cross-check and why a conflict between them is informative.",
        ),
    ),
    MethodJudgement(
        ticker="WBD",
        business="Studios, streaming and declining linear television networks, assembled by the "
                 "2022 WarnerMedia-Discovery merger and carrying its debt. TTM revenue 36.1bn, "
                 "operating income -1.27bn (-3.5% margin), derived EBITDA 3.80bn, free cash flow "
                 "2.18bn, net debt 28.7bn - 29% of a 99.1bn enterprise value.",
        primary_preference=("EV/Sales", "EV/EBITDA"),
        secondary_preference=("EV/EBITDA", "EV/FCF"),
        unsuitable={
            "P/B": "book equity of 32.8bn is the unamortised residue of the merger's purchase "
                   "accounting, repeatedly written down since. Valuing a levered loss-making media "
                   "group on a multiple of that residue prices the accounting for the acquisition "
                   "rather than the business, and an equity-side multiple on a 29%-net-debt capital "
                   "structure answers a question about the equity stub rather than about the assets.",
            "P/FCF": "an equity-side cash-flow multiple on an issuer whose net debt is 29% of "
                     "enterprise value: the 32.33x says as much about the leverage as about the "
                     "cash generation, and EV/FCF is the same fundamental read at the right point "
                     "in the capital structure.",
        },
        valuation_risk=(
            "Revenue is the denominator precisely because earnings are negative, and a sales "
            "multiple is silent on whether the loss narrows. It is the appropriate method for a "
            "pre-profit or thin-margin levered issuer and it is also the least informative one.",
            "The capital structure is the valuation. At 29% net debt a change in the enterprise "
            "multiple moves the equity value by roughly 1.4x as much, so the Bear anchor is a "
            "statement about the equity stub and not about the enterprise.",
            "D5-D1 recorded that WBD's debt scope is not established by the filing: LongTermDebt "
            "32.023bn equals the sum of LongTermDebtAndCapitalLeaseObligations 30.530bn and its "
            "current portion 1.493bn, so whether a capital lease sits inside the total is a "
            "question the filing does not answer. Every enterprise-chain fair value here inherits "
            "that ambiguity.",
            "Derived EBITDA is operating income plus D&A, and for this issuer that is -1.27bn plus "
            "5.08bn. A measure that is positive only because of 5bn of amortisation on acquired "
            "content and intangibles is a weak proxy for cash earnings, which is why it is the "
            "cross-check and not the anchor.",
        ),
    ),
    MethodJudgement(
        ticker="COHR",
        business="Photonics, optical communications components and industrial lasers. Capital "
                 "intensive: TTM capex 1.10bn against 7.12bn of revenue, which is what drives free "
                 "cash flow to -1.02bn while net income is +805m. Net debt 2.06bn, 3.5% of a "
                 "58.8bn enterprise value.",
        primary_preference=("P/E", "EV/Sales"),
        secondary_preference=("EV/Sales", "P/E"),
        unsuitable={
            "P/B": "book equity of 10.9bn is dominated by goodwill and intangibles from the II-VI / "
                   "Coherent combination rather than by the fabs that earn the money, so a 5.21x "
                   "P/B is a multiple of acquisition accounting. For a manufacturer the asset base "
                   "matters, but this particular book value is not a measure of it.",
        },
        valuation_risk=(
            "This issuer's income statement does not resolve as a unit and that is the dominant "
            "limitation. COHR stopped reporting OperatingIncomeLoss in 2024, so its newest "
            "operating income is 820 days old and D5-D1 refused EV/EBIT and EV/EBITDA on "
            "staleness. The consequence for valuation is not a missing method, it is that no "
            "margin regime is measurable: whether 8.27x revenue or 70x earnings is appropriate "
            "depends on an operating margin this repository cannot currently observe.",
            "Free cash flow is negative because of a capex cycle, not because the business loses "
            "money. P/FCF and EV/FCF were refused as negative denominators, correctly, but the "
            "refusal removes the method that would otherwise arbitrate between the earnings and "
            "the sales multiple.",
            "A trailing P/E of 70x on a cyclical hardware manufacturer embeds an expectation about "
            "the next cycle. D5 cannot evaluate that expectation - there is no D3 or D4 output for "
            "COHR - so the Base fair value is a re-rating statement and nothing more.",
        ),
    ),
    MethodJudgement(
        ticker="NATR",
        business="Nature's Sunshine: manufactured nutritional supplements sold through direct "
                 "selling. Small cap, 230m. TTM revenue 492m at a 6.0% operating margin, free cash "
                 "flow 18.1m, book equity 169m. No borrowing balance resolves at the cash date, so "
                 "there is no enterprise value and every enterprise method is unavailable.",
        primary_preference=("P/FCF",),
        secondary_preference=("P/B",),
        unsuitable={},
        valuation_risk=(
            "Free cash flow of 18.1m on a 230m market cap is small enough in absolute terms that "
            "one working-capital swing or one year's inventory build moves the multiple "
            "materially. The 6.0% operating margin clears the diagnostic's 2% floor but not by "
            "much, so the cash-flow multiple is more volatile than the same multiple on a large "
            "issuer.",
            "P/B is the secondary here rather than unsuitable, and the distinction is deliberate: "
            "a supplement manufacturer does hold inventory, plant and real estate that earn money, "
            "so 1.36x book is a meaningful asset check. It is still not an earnings cross-check, "
            "and D5-D0 forbids promoting it, so it corroborates the floor rather than the target.",
            "NATR's newest borrowing balance is 899 days old and is never netted against 2026 cash, "
            "so net debt is MISSING rather than zero. The valuation is equity-side by necessity, "
            "and if the issuer does carry debt that this repository cannot see, every per-share "
            "figure here is overstated.",
            "Direct selling depends on an active distributor count that no financial statement "
            "field measures, so the durability of the revenue is outside everything D5 reads.",
        ),
    ),
    MethodJudgement(
        ticker="CHRS",
        business="Coherus BioSciences: commercial-stage biopharmaceuticals, biosimilars and "
                 "oncology. Research-driven and asset-light in the accounting sense - the value is "
                 "in approvals, patents and a pipeline, none of which is a balance-sheet asset. "
                 "P/B 3.10x is the only multiple D5-D1 could form; P/FCF was refused as stale.",
        primary_preference=(),
        secondary_preference=(),
        unsuitable={
            "P/B": "this is D5-D0 §N's case stated in its own words: 'for an asset-light issuer P/B "
                   "is NOT_APPLICABLE on its own terms, which makes it NOT_READY'. A biopharma's "
                   "book equity is cash plus working capital, and what it is actually worth is the "
                   "probability-weighted value of approvals and a pipeline. A multiple of book "
                   "value answers neither question, and multiplying it by a percentile of its own "
                   "history would produce a target price with no economic content at all.",
        },
        valuation_risk=(
            "The honest output is an abstention. The single computable method is economically "
            "inapplicable, and no other method resolved, so there is no fair value to publish.",
        ),
    ),
    MethodJudgement(
        ticker="STAA",
        business="STAAR Surgical: implantable collamer lenses for refractive correction. A "
                 "single-franchise medical device maker whose value sits in regulatory approvals, "
                 "the implant platform and surgeon adoption. P/B 3.08x is the only multiple; "
                 "P/FCF was refused on a negative denominator.",
        primary_preference=(),
        secondary_preference=(),
        unsuitable={
            "P/B": "the manufacturing assets are real but they are not what the market is paying "
                   "for: the franchise is the approvals and the surgeon base. With free cash flow "
                   "negative and no earnings or cash-flow multiple computable, book value is the "
                   "only thing left to divide by rather than the right thing, and §8's 'data "
                   "availability is not economic suitability' is exactly this situation.",
        },
        valuation_risk=(
            "The honest output is an abstention. A negative free cash flow removed the method that "
            "would have valued this issuer, and the surviving method does not value it.",
        ),
    ),
    MethodJudgement(
        ticker="GNW",
        business="Genworth Financial: an insurance holding company - long-term care insurance in "
                 "runoff, plus a majority stake in Enact, a mortgage insurer. Book equity 8.7bn "
                 "against a 3.8bn market cap, so P/B is 0.44x. It is the only multiple D5-D1 could "
                 "form for this issuer.",
        primary_preference=("P/B",),
        secondary_preference=(),
        unsuitable={},
        valuation_risk=(
            "P/B is the primary method here and that is the finding, not an exception grudgingly "
            "made. For an insurer, book equity IS the earning asset: the balance sheet is the "
            "business, net assets back the reserves, and price-to-book is the method the industry "
            "itself values insurers on. The three single-method issuers in D5-D1 all had P/B as "
            "that method, and whether P/B-only means NOT_READY turns on the business rather than "
            "on the method count - two of the three abstain and this one does not.",
            "A 0.44x multiple is the market's doubt about the long-term care reserves, and it may "
            "well be right. A fair value computed from percentiles of that same discount values "
            "the book at a multiple the market has paid before; it does not assert that the "
            "reserves are adequate, and if they are not, the book value itself is wrong and every "
            "figure derived from it with it.",
            "An insurer's GAAP book value moves with accumulated other comprehensive income, so "
            "interest-rate moves change the denominator without anything happening to the "
            "business. A P/B percentile drawn across a period of rate moves is partly a rate "
            "series.",
            "Single method, so there is no cross-check of any kind. Confidence is LOW by rule.",
        ),
    ),
)}

WINDOW_RULE = (
    "One rule, applied to every issuer identically, with no per-issuer discretion:\n\n"
    "    if the primary method's FULL_2Y trend is TRENDING_STRONG\n"
    "        the contract window is the SHORTEST contract-eligible window\n"
    "    otherwise\n"
    "        the contract window is FULL_2Y\n\n"
    "The reason is `DEAD_REGIME_IS_THE_CENTRAL_TRAP`. A strong monotone drift across the whole "
    "panel is evidence that the early panel is a different regime from the late panel, and D5-D0 §H "
    "already bounds the panel at one macro regime even before that drift is considered. Taking the "
    "median of a transition and calling it a level is how a valuation framework manufactures upside "
    "from the fact that the past was more expensive.\n\n"
    "It is stated as a rule rather than a judgement so that it cannot be fitted. A model choosing "
    "the window per issuer would choose the window that produced the answer it preferred, and no "
    "later reader could tell. Here the rule is mechanical, the trend that triggers it is a code-owned "
    "rank correlation, and every window's fair value is published for every issuer regardless of "
    "which one the rule selected - so what the rule passed over is on the table next to what it chose."
)


# -------------------------------------------------------------------------------------------------
# The point-in-time observed multiple panel
# -------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class IssuerSession:
    """Everything the seven multiples need for one issuer on one session, resolved AT that session."""

    session: date
    price: float
    shares: float | None
    net_debt: float | None
    enterprise_value: float | None
    market_cap: float | None
    share_class_confidence: str
    share_class_topology: str
    results: Mapping[str, object]
    ttm: Mapping[str, object]
    equity: object


def resolve_session(ticker: str, cik: str, facts: Sequence[CanonicalFact], session: date,
                    price: float) -> IssuerSession:
    """Every primitive for one issuer at one session, with the decision time set to that session.

    `decision_time` is the session at 21:00Z, which is the bound `run_strategy_h_v2_d4_1.pit_trim`
    already uses for "this session's close is knowable". Facts are admitted by `accepted_at <=
    decision_time` inside `resolve_fact`, and the reference snapshot by `as_of <= session` inside
    `load_reference_snapshot`, so neither a later filing nor a later share-class observation can
    reach this row. The primitives are called, never reimplemented: a historical multiple computed by
    a second code path would be a different quantity from D5-D1's current one, and the panel's whole
    purpose is that its last session IS D5-D1's row.
    """
    decision_time = datetime(session.year, session.month, session.day, 21, 0, tzinfo=timezone.utc)
    share_class = resolve_share_class(cik, session, load_reference_snapshot(session))
    market_cap = resolve_market_cap(ticker, facts, decision_time, session, _market_opens(session),
                                    price, session, share_class)
    net = resolve_net_debt(facts, decision_time, session)
    ev = reopened_enterprise_value(market_cap, net, decision_time, session)
    ttm = construct_ttm_bundle(facts, decision_time, specs=CAPITAL_STRUCTURE_FIELD_SPECS)
    equity = resolve_valuation_instant(facts, "equity", decision_time, session)
    results = compute_multiples(market_cap=market_cap.resolution, enterprise_value=ev, ttm=ttm,
                                equity=equity, price=price)
    shares = None
    if market_cap.shares is not None and market_cap.shares.fact is not None:
        shares = float(market_cap.shares.fact.value)
    return IssuerSession(
        session=session, price=price, shares=shares,
        net_debt=net.value if net.ok else None,
        enterprise_value=ev.value if ev.ok else None,
        market_cap=market_cap.value if market_cap.ok else None,
        share_class_confidence=share_class.confidence.value,
        share_class_topology=share_class.topology.value,
        results=results, ttm=ttm, equity=equity,
    )


def build_panel(ticker: str, closes: Mapping[date, float],
                until: date) -> tuple[dict[str, list[MultipleObservation]], IssuerSession | None,
                                      dict]:
    """The observed multiple panel for one issuer: every method, every session it resolved on.

    Returns the per-method observation lists, the decision session's own resolution, and a coverage
    record. A session on which a method refuses contributes nothing and is not interpolated - a gap
    in an observed series is a gap, and filling it would invent an observation.
    """
    loaded = load_issuer(ticker)
    if loaded is None:
        return {}, None, {"ticker": ticker, "status": "NO_PACKAGE"}
    facts, _cutoff, cik, _document = loaded

    sessions = sorted(s for s in closes if s <= until)
    panel: dict[str, list[MultipleObservation]] = {m: [] for m in METHOD_ORDER}
    decision_row: IssuerSession | None = None
    refusals: dict[str, int] = {}

    for session in sessions:
        row = resolve_session(ticker, cik, facts, session, closes[session])
        if session == until:
            decision_row = row
        for method in METHOD_ORDER:
            result = row.results[method]
            if result.ok:
                panel[method].append(MultipleObservation(
                    session=session, multiple=result.value,
                    numerator_value=result.numerator_value,
                    denominator_value=result.denominator_value,
                    denominator_period_end=result.denominator_period_end))
            else:
                key = f"{method}:{result.status.value}"
                refusals[key] = refusals.get(key, 0) + 1

    coverage = {
        "ticker": ticker, "status": "OK", "panel_sessions": len(sessions),
        "first_session": sessions[0].isoformat() if sessions else None,
        "last_session": sessions[-1].isoformat() if sessions else None,
        "observations": {m: len(panel[m]) for m in METHOD_ORDER},
        "refusals": dict(sorted(refusals.items())),
    }
    return panel, decision_row, coverage


# -------------------------------------------------------------------------------------------------
# Selection, then valuation
# -------------------------------------------------------------------------------------------------

def _windows_for(method: str, observations: Sequence[MultipleObservation],
                 current_multiple: float | None) -> dict[PanelWindow, object]:
    return {w: window_stats(observations, method=method, window=w,
                            current_multiple=current_multiple)
            for w in PanelWindow}


def select_contract_window(windows: Mapping[PanelWindow, object]) -> tuple[PanelWindow | None, str]:
    """`WINDOW_RULE`, executed. Mechanical, and the same for every issuer."""
    eligible = [w for w in PanelWindow if windows[w].contract_eligible]
    if not eligible:
        return None, "no window is contract-eligible"
    full = windows[PanelWindow.FULL_2Y]
    # `rho` is None whenever the trend is UNDETERMINED - too few observations to rank, or a constant
    # multiple - so it is formatted defensively rather than interpolated directly. UNDETERMINED is
    # not TRENDING_STRONG, so it takes the FULL_2Y branch and would otherwise format a None.
    trend_desc = (f"{full.trend.value} (rho {full.rho:+.3f})" if full.rho is not None
                  else f"{full.trend.value} (rho not measurable)")
    if full.contract_eligible and full.trend is not TrendClass.TRENDING_STRONG:
        return PanelWindow.FULL_2Y, (
            f"FULL_2Y trend is {trend_desc}, not TRENDING_STRONG, so the whole panel is the "
            f"observation range")
    shortest = min(eligible, key=lambda w: windows[w].n)
    return shortest, (
        f"FULL_2Y trend is {trend_desc}: the early panel is a different regime from the late "
        f"panel, so the shortest contract-eligible window ({shortest.value}, "
        f"n={windows[shortest].n}) governs rather than the median of a transition")


def _denominator_of(row: IssuerSession, method: str) -> tuple[float | None, str | None, date | None]:
    """The current code-owned denominator for a method, from the decision session's own result."""
    result = row.results[method]
    if not result.ok:
        return None, None, None
    return result.denominator_value, result.denominator_field, result.denominator_period_end


def build_range(row: IssuerSession, method: str, window: PanelWindow,
                windows: Mapping[PanelWindow, object]) -> FairValueRange:
    """One method's Bear / Base / Bull in one window. Every number here is code-owned."""
    stats = windows[window]
    denominator, field_name, period_end = _denominator_of(row, method)
    targets = {Scenario.BEAR: stats.bear, Scenario.BASE: stats.base, Scenario.BULL: stats.bull}
    built = {
        scenario: scenario_value(
            scenario=scenario, method=method, target=targets[scenario], window=window,
            denominator_value=denominator, denominator_field=field_name,
            denominator_period_end=period_end, shares=row.shares, net_debt=row.net_debt)
        for scenario in (Scenario.BEAR, Scenario.BASE, Scenario.BULL)
    }
    return FairValueRange(method=method, window=window, bear=built[Scenario.BEAR],
                          base=built[Scenario.BASE], bull=built[Scenario.BULL])


def value_issuer(ticker: str, panel: Mapping[str, Sequence[MultipleObservation]],
                 row: IssuerSession) -> dict:
    """One issuer, end to end: selection, windows, fair value, targets, reconciliation, confidence."""
    judgement = METHOD_JUDGEMENTS[ticker]
    computable = tuple(m for m in METHOD_ORDER if row.results[m].ok)
    current = {m: row.results[m].value for m in computable}

    windows_by_method = {m: _windows_for(m, panel[m], current[m]) for m in computable}

    # Selection: the first entry in the declared preference order whose panel is contract-eligible.
    selection_trace: list[dict] = []
    primary: str | None = None
    for candidate in judgement.primary_preference:
        if candidate not in computable:
            selection_trace.append({"method": candidate, "taken": False,
                                    "why": "no multiple at the decision session"})
            continue
        if candidate in judgement.unsuitable:
            selection_trace.append({"method": candidate, "taken": False,
                                    "why": "asserted NOT_SUITABLE: "
                                           + judgement.unsuitable[candidate]})
            continue
        chosen, _why = select_contract_window(windows_by_method[candidate])
        if chosen is None:
            reasons = {w.value: windows_by_method[candidate][w].ineligible_reason
                       for w in PanelWindow}
            selection_trace.append({"method": candidate, "taken": False,
                                    "why": f"no contract-eligible window: {reasons}"})
            continue
        primary = candidate
        selection_trace.append({"method": candidate, "taken": True,
                                "why": "first entry in the declared preference order with a "
                                       "contract-eligible observed panel"})
        break

    if primary is None:
        return {
            "ticker": ticker, "status": ValuationStatus.VALUATION_NOT_READY.value,
            "business": judgement.business,
            "computable_methods": list(computable),
            "current_multiples": current,
            "unsuitable": dict(judgement.unsuitable),
            "selection_trace": selection_trace,
            "confidence": valuation_confidence(
                valued=False, independent_method_count=0, total_method_count=len(computable),
                reconciliation=ReconciliationStatus.NO_SECONDARY,
                trend=TrendClass.UNDETERMINED, window=PanelWindow.FULL_2Y, stale_inputs=(),
                share_class_high_confidence=row.share_class_confidence == "HIGH",
                peer_context_available=False).to_dict(),
            "valuation_risk": list(judgement.valuation_risk),
            "fair_value": None, "target_prices": None,
        }

    contract_window, window_reason = select_contract_window(windows_by_method[primary])
    assert contract_window is not None

    # Secondary: first declared, computable, suitable, contract-eligible method that is not the
    # primary. Independence is reported rather than required - §23 permits a one-method valuation.
    secondary: str | None = None
    secondary_trace: list[dict] = []
    for candidate in judgement.secondary_preference:
        if candidate == primary or candidate not in computable:
            secondary_trace.append({"method": candidate, "taken": False,
                                    "why": "the primary, or no multiple at the decision session"})
            continue
        if candidate in judgement.unsuitable:
            secondary_trace.append({"method": candidate, "taken": False,
                                    "why": "asserted NOT_SUITABLE: "
                                           + judgement.unsuitable[candidate]})
            continue
        if not any(windows_by_method[candidate][w].contract_eligible for w in PanelWindow):
            secondary_trace.append({"method": candidate, "taken": False,
                                    "why": "no contract-eligible window"})
            continue
        secondary = candidate
        secondary_trace.append({"method": candidate, "taken": True, "why": "first eligible"})
        break

    primary_ranges = {w.value: build_range(row, primary, w, windows_by_method[primary]).to_dict()
                      for w in PanelWindow if windows_by_method[primary][w].contract_eligible}
    primary_range = build_range(row, primary, contract_window, windows_by_method[primary])
    targets = target_prices(primary_range, current_price=row.price)

    secondary_range: FairValueRange | None = None
    if secondary is not None:
        sec_window, _ = select_contract_window(windows_by_method[secondary])
        assert sec_window is not None
        secondary_range = build_range(row, secondary, sec_window, windows_by_method[secondary])

    rec = reconcile(primary=primary_range, secondary=secondary_range,
                    net_debt=row.net_debt, enterprise_value=row.enterprise_value)

    independent = 1
    if secondary is not None:
        correlated, _ = methods_are_correlated(primary, secondary, net_debt=row.net_debt,
                                               enterprise_value=row.enterprise_value)
        independent = 1 if correlated else 2

    confidence = valuation_confidence(
        valued=primary_range.base.ok,
        independent_method_count=independent,
        total_method_count=1 if secondary is None else 2,
        reconciliation=rec.status,
        trend=windows_by_method[primary][contract_window].trend,
        window=contract_window,
        stale_inputs=(),
        share_class_high_confidence=row.share_class_confidence == "HIGH",
        peer_context_available=False,
    )

    return {
        "ticker": ticker,
        "status": (ValuationStatus.VALUED.value if primary_range.base.ok
                   else ValuationStatus.VALUATION_NOT_READY.value),
        "business": judgement.business,
        "decision_session": row.session.isoformat(),
        "current_price": row.price,
        "shares": row.shares, "net_debt": row.net_debt,
        "enterprise_value": row.enterprise_value, "market_cap": row.market_cap,
        "share_class": {"topology": row.share_class_topology,
                        "confidence": row.share_class_confidence},
        "computable_methods": list(computable),
        "current_multiples": current,
        "unsuitable": dict(judgement.unsuitable),
        "primary_method": primary, "secondary_method": secondary,
        "selection_trace": selection_trace, "secondary_trace": secondary_trace,
        "chain": metric_chain(primary).value,
        "contract_window": contract_window.value, "contract_window_reason": window_reason,
        "windows": {w.value: windows_by_method[primary][w].to_dict() for w in PanelWindow},
        "fair_value": primary_range.to_dict(),
        "fair_value_by_window": primary_ranges,
        "secondary_cross_check": None if secondary_range is None else secondary_range.to_dict(),
        "reconciliation": rec.to_dict(),
        "target_prices": targets.to_dict(),
        "confidence": confidence.to_dict(),
        "valuation_risk": list(judgement.valuation_risk),
        "peer_context": "PEER_CONTEXT_UNAVAILABLE",
        "guidance_context": "GUIDANCE_UNKNOWN",
        "d3_context": "D3_NOT_EVALUATED",
        "d4_context": "D4_NOT_EVALUATED",
    }


# -------------------------------------------------------------------------------------------------
# Audit (§32) - every defect class as a list of offenders, never a count
# -------------------------------------------------------------------------------------------------

#: The tolerance for `FAIR_VALUE_AT_CURRENT_MULTIPLE_IS_PRICE`, relative to the price. Division and
#: multiplication of doubles are each exact to half an ulp, and the enterprise chain performs four
#: such operations plus a subtraction that can cancel significant digits, so the bound is a few ulp
#: rather than zero. 1e-9 relative is roughly a million times machine epsilon: tight enough that a
#: wrong share count, a wrong net debt sign or a mismatched denominator cannot hide inside it.
IDENTITY_TOLERANCE_RELATIVE = 1e-9


def audit(rows: Sequence[dict], panels: Mapping[str, Mapping[str, Sequence[MultipleObservation]]],
          decision_rows: Mapping[str, IssuerSession]) -> dict:
    """§32's defect classes. Each is a list of named offenders so a non-zero result is actionable."""
    identity_failures: list[str] = []
    unordered: list[str] = []
    non_observed_target: list[str] = []
    refusal_with_value: list[str] = []
    negative_published: list[str] = []
    upside_mismatch: list[str] = []
    target_not_from_operands: list[str] = []
    future_observation: list[str] = []
    price_only_window_governed: list[str] = []
    incomplete_range: list[str] = []

    for row in rows:
        ticker = row["ticker"]
        if row["status"] != ValuationStatus.VALUED.value:
            if row.get("target_prices") is not None:
                refusal_with_value.append(f"{ticker}: NOT_READY carries target prices")
            continue

        decision = decision_rows[ticker]
        method = row["primary_method"]
        fv = row["fair_value"]
        tp = row["target_prices"]
        price = row["current_price"]

        # The invariant: the chain at the CURRENT observed multiple must return the current price.
        residual = identity_residual(
            method=method, current_multiple=row["current_multiples"][method],
            denominator_value=fv["base"]["metric_total"], shares=decision.shares,
            net_debt=decision.net_debt, price=price)
        if residual is None or residual > IDENTITY_TOLERANCE_RELATIVE * price:
            identity_failures.append(f"{ticker} {method}: residual {residual}")

        if not fv["ordered"]:
            unordered.append(f"{ticker} {method}: bear/base/bull not monotone")
        # `ordered` is vacuously True on an incomplete range, so incompleteness is reported in its
        # own right: a range missing a leg is a weaker output than a range, and it must not pass
        # the monotone check silently.
        if not fv["complete"]:
            legs = [leg for leg in ("bear", "base", "bull") if fv[leg]["refusal"] is not None]
            incomplete_range.append(f"{ticker} {method}: refused legs {legs}")

        # Every target multiple must be an element of the observed panel for that method, and the
        # session it is attributed to must actually carry that multiple.
        observed = {(o.session, o.multiple) for o in panels[ticker][method]}
        for leg in ("bear", "base", "bull"):
            scenario = fv[leg]
            if scenario["refusal"] is not None:
                continue
            key = (date.fromisoformat(scenario["target_observed_on"]), scenario["target_multiple"])
            if key not in observed:
                non_observed_target.append(f"{ticker} {method} {leg}: {key} not in the panel")
            if key[0] > decision.session:
                future_observation.append(f"{ticker} {method} {leg}: observed after the decision")
            value = scenario["fair_value_per_share"]
            if value is not None and value <= 0.0:
                negative_published.append(f"{ticker} {method} {leg}")

            # The fair value must equal its own two stated operands, recomputed from the record.
            chain = metric_chain(method)
            m, t = scenario["metric_per_share"], scenario["target_multiple"]
            if chain is MetricChain.ENTERPRISE_BRIDGE:
                recomputed = (scenario["metric_total"] * t - scenario["net_debt"]) \
                    / scenario["shares"]
            else:
                recomputed = m * t
            if recomputed != scenario["fair_value_per_share"]:
                target_not_from_operands.append(
                    f"{ticker} {method} {leg}: {recomputed} != {scenario['fair_value_per_share']}")

        for level, upside in (("TP1", "upside_to_TP1"), ("TP2", "upside_to_TP2"),
                              ("BEAR_ANCHOR", "downside_to_bear")):
            if tp[level] is None:
                continue
            if tp[upside] != tp[level] / price - 1.0:
                upside_mismatch.append(f"{ticker} {level}")

        if not row["windows"][row["contract_window"]]["denominator_moved"]:
            price_only_window_governed.append(f"{ticker} {row['contract_window']}")

    return {
        "fair_value_at_current_multiple_is_not_price": identity_failures,
        "bear_base_bull_not_monotone": unordered,
        "target_multiple_not_an_observation": non_observed_target,
        "target_multiple_observed_after_decision": future_observation,
        "not_ready_issuer_carrying_a_target_price": refusal_with_value,
        "non_positive_fair_value_published": negative_published,
        "upside_not_equal_to_its_own_formula": upside_mismatch,
        "fair_value_not_equal_to_its_own_operands": target_not_from_operands,
        "price_only_window_governed_a_target": price_only_window_governed,
        "fair_value_range_missing_a_leg": incomplete_range,
    }


def d5_d1_agreement(panels: Mapping[str, Mapping[str, Sequence[MultipleObservation]]],
                    decision_rows: Mapping[str, IssuerSession]) -> dict:
    """The panel's last session against D5-D1's published multiples, bit for bit.

    This is the one check that establishes the historical panel is the same quantity as D5-D1's
    current multiple rather than a parallel computation that happens to look similar. If the PIT
    wiring were wrong - a cutoff passed as a decision date, a snapshot read too late, a TTM bundle
    built at the package cutoff instead of at the session - the last session would still produce
    plausible numbers and they would not be these numbers.
    """
    published = {row["ticker"]: row["multiples"] for row in run_d5_d1()["rows"]}
    mismatches: list[str] = []
    compared = 0
    for ticker, panel in panels.items():
        for method in METHOD_ORDER:
            theirs = published.get(ticker, {}).get(method, {})
            last = [o for o in panel[method] if o.session == decision_rows[ticker].session]
            if theirs.get("status") == MultipleStatus.OK.value:
                if not last:
                    mismatches.append(f"{ticker} {method}: D5-D1 OK, panel has no observation")
                elif last[0].multiple != theirs["multiple"]:
                    mismatches.append(f"{ticker} {method}: {last[0].multiple} != {theirs['multiple']}")
                else:
                    compared += 1
            elif last:
                mismatches.append(f"{ticker} {method}: D5-D1 refused, panel published a value")
    return {"compared": compared, "mismatches": mismatches}


def run() -> dict:
    closes_panel = load_price_panel(PILOT_ISSUERS)
    panels: dict[str, dict[str, list[MultipleObservation]]] = {}
    decision_rows: dict[str, IssuerSession] = {}
    coverage: list[dict] = []
    rows: list[dict] = []

    for ticker in PILOT_ISSUERS:
        panel, decision, cover = build_panel(ticker, closes_panel[ticker], DECISION_SESSION)
        coverage.append(cover)
        if decision is None:
            continue
        panels[ticker] = panel
        decision_rows[ticker] = decision
        rows.append(value_issuer(ticker, panel, decision))

    valued = [r for r in rows if r["status"] == ValuationStatus.VALUED.value]
    not_ready = [r for r in rows if r["status"] != ValuationStatus.VALUED.value]

    report = {
        "contract": D5_D2_CONTRACT,
        "fair_value_contract": FAIR_VALUE_CONTRACT_VERSION,
        "decision_session": DECISION_SESSION.isoformat(),
        "pilot_issuers": list(PILOT_ISSUERS),
        "design_candidates": list(DESIGN_CANDIDATES),
        "abstention_candidates": list(ABSTENTION_CANDIDATES),
        "coverage": coverage,
        "rows": rows,
        "valued": [r["ticker"] for r in valued],
        "valuation_not_ready": [r["ticker"] for r in not_ready],
        "confidence_distribution": {
            c.value: sum(1 for r in rows if r["confidence"]["confidence"] == c.value)
            for c in ValuationConfidence},
        "defects": audit(rows, panels, decision_rows),
        "d5_d1_agreement": d5_d1_agreement(panels, decision_rows),
        "model_calls": 0,
        "cost_usd": 0.0,
        "peer_context": PEER_CONTEXT_UNAVAILABLE,
        "guidance_context": GUIDANCE_UNAVAILABLE,
        "not_reclassified": NOT_RECLASSIFIED,
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
        out = OUTPUT_DIR / f"D5_D2-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
        out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str))
        print(f"wrote {out}")

    print(f"\nH-V2-D5-D2  decision session {report['decision_session']}  "
          f"model calls {report['model_calls']}  cost ${report['cost_usd']:.2f}")
    print(f"valued {len(report['valued'])}: {', '.join(report['valued'])}")
    print(f"VALUATION_NOT_READY {len(report['valuation_not_ready'])}: "
          f"{', '.join(report['valuation_not_ready'])}")

    print("\nticker  prim      win        cur     bear    base    bull   "
          "BearFV    TP1     TP2    upTP1   upTP2   dnBear  conf")
    for row in report["rows"]:
        if row["status"] != "VALUED":
            print(f"{row['ticker']:<7} VALUATION_NOT_READY  "
                  f"({row['confidence']['confidence']})")
            continue
        w = row["windows"][row["contract_window"]]
        tp = row["target_prices"]
        fv = row["fair_value"]
        print(f"{row['ticker']:<7} {row['primary_method']:<9} {row['contract_window']:<10} "
              f"{_num(w['current_multiple']):>6} "
              f"{_num(fv['bear']['target_multiple']):>7} {_num(fv['base']['target_multiple']):>7} "
              f"{_num(fv['bull']['target_multiple']):>7} "
              f"{_num(tp['BEAR_ANCHOR']):>8} {_num(tp['TP1']):>7} {_num(tp['TP2']):>7} "
              f"{_pct(tp['upside_to_TP1']):>7} {_pct(tp['upside_to_TP2']):>7} "
              f"{_pct(tp['downside_to_bear']):>7}  {row['confidence']['confidence']}")

    print("\nD5-D1 agreement: compared", report["d5_d1_agreement"]["compared"],
          "mismatches", report["d5_d1_agreement"]["mismatches"] or 0)
    print("\ndefects:")
    for name, offenders in report["defects"].items():
        print(f"  {name:<52} {offenders if offenders else 0}")


if __name__ == "__main__":
    main(sys.argv[1:])
