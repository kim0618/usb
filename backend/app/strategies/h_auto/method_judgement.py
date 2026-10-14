"""Code-owned ``MethodJudgement`` for an issuer the frozen D5 tables never read.

D5-D2 and D6 value an issuer through a declared ``MethodJudgement``: an ordered method preference,
an ``unsuitable`` map and a prose reading of the business, written by hand for the 13 D6 issuers
and the 7 D5-D2 issuers (``run_strategy_h_v2_d6.METHOD_JUDGEMENTS``, ``run_strategy_h_v2_d5_d2``).
A new candidate has no entry, and the autonomous executor refused it before any model call
(``D5_METHOD_JUDGEMENT_REQUIRED``). This module derives a judgement for such an issuer from the
rules the valuation contract already froze, and nothing else:

* **method preference** is ``multiples.method_suitability`` (D5-D0 section 17, pre-registered,
  ``MIN_MARGIN_FOR_PRIMARY`` 2% and ``MATERIAL_NET_DEBT_SHARE_OF_EV`` 10%) evaluated on the decision
  session's own ``IssuerSession`` with the inputs D5-D1 gave it (net debt, enterprise value, TTM
  revenue, TTM operating income): ``ECONOMICALLY_REASONABLE`` methods are the primary preference and
  ``SECONDARY_ONLY`` methods the secondary preference, both in ``METHOD_ORDER``;
* **P/B is never used**: D5-D0's own spec says its applicability "is narrow and must be argued per
  issuer", the human tables argued it (FG, GNW as the primary; NATR, TG as a secondary; asserted
  unsuitable for twelve others) and code cannot make that argument either way;
* **``unsuitable`` is empty**: ``NOT_SUITABLE`` is AI-owned (``multiples.NOT_SUITABLE_IS_AI_OWNED``)
  and this module makes no economic assertion;
* **not ready, fail closed**: an issuer the section-17 rules cannot read is
  ``METHOD_JUDGEMENT_NOT_READY`` - a financial issuer (SIC 6000-6799: the only P/B-primary
  judgements in the frozen tables are the two insurers, and section 17 reads an operating margin
  that is not the economics of a bank or an insurer), an issuer with no computable multiple, or one
  with no ``ECONOMICALLY_REASONABLE`` method at the decision session.

No threshold is introduced. ``business`` is a factual sentence from the session's resolved
fundamentals and the issuer's SEC SIC description; ``valuation_risk`` states that the judgement is
code-owned and records sign-level facts only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from app.backtest.strategy_h_v2.valuation.multiples import (
    METHOD_ORDER,
    MethodSuitability,
    method_suitability,
)

VERSION = "H_METHOD_JUDGEMENT_AUTO_V1"
AUTO_READY, NOT_READY = "AUTO_READY", "METHOD_JUDGEMENT_NOT_READY"
FINANCIAL_SIC_RANGE = (6000, 6799)
WITHHELD = {"P/B": "applicability must be argued per issuer (D5-D0 P/B spec); code does not argue it"}


@dataclass(frozen=True)
class Derived:
    ticker: str
    state: str
    reasons: tuple[str, ...]
    judgement: Any | None                 # run_strategy_h_v2_d5_d2.MethodJudgement when AUTO_READY
    suitability: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    inputs: Mapping[str, Any] = field(default_factory=dict)

    def report(self) -> dict[str, Any]:
        body = {"version": VERSION, "ticker": self.ticker, "state": self.state, "reasons": list(self.reasons),
                "suitability": {m: dict(v) for m, v in self.suitability.items()}, "inputs": dict(self.inputs),
                "withheld": dict(WITHHELD)}
        if self.judgement is not None:
            body["judgement"] = {"primary_preference": list(self.judgement.primary_preference),
                                 "secondary_preference": list(self.judgement.secondary_preference),
                                 "unsuitable": dict(self.judgement.unsuitable),
                                 "business": self.judgement.business,
                                 "valuation_risk": list(self.judgement.valuation_risk)}
        return body


def _value(item: Any) -> float | None:
    return float(item.value) if item is not None and getattr(item, "ok", False) and item.value is not None else None


def is_financial(sic: Any) -> bool | None:
    try:
        code = int(str(sic).strip())
    except (TypeError, ValueError):
        return None
    return FINANCIAL_SIC_RANGE[0] <= code <= FINANCIAL_SIC_RANGE[1]


def _money(value: float | None) -> str:
    if value is None:
        return "n/a"
    for unit, div in (("bn", 1e9), ("m", 1e6), ("k", 1e3)):
        if abs(value) >= div:
            return f"{value / div:.1f}{unit}"
    return f"{value:.0f}"


def derive(ticker: str, row: Any, *, sic: Any = None, sic_description: str | None = None) -> Derived:
    """A judgement from the decision session's ``IssuerSession`` under the frozen section-17 rules."""
    from app.dev.run_strategy_h_v2_d5_d2 import MethodJudgement

    net_debt, ev = row.net_debt, row.enterprise_value
    ttm = row.ttm or {}
    revenue = _value(ttm.get("revenue"))
    operating_income = _value(ttm.get("operating_income"))
    fcf = _value(ttm.get("free_cash_flow"))
    equity = _value(row.equity)
    inputs = {"session": row.session.isoformat(), "price": row.price, "market_cap": row.market_cap,
              "net_debt": net_debt, "enterprise_value": ev, "ttm_revenue": revenue,
              "ttm_operating_income": operating_income, "ttm_free_cash_flow": fcf, "equity": equity,
              "sic": None if sic is None else str(sic), "sic_description": sic_description,
              "share_class_confidence": row.share_class_confidence}
    reasons: list[str] = []
    financial = is_financial(sic)
    if financial is None:
        reasons.append("SIC_UNRESOLVED: the issuer's SEC SIC code is unknown, so the issuer type cannot be checked")
    elif financial:
        reasons.append(f"FINANCIAL_SECTOR:SIC_{str(sic).strip()}: section-17 margin rules do not read a bank "
                       f"or insurer; the frozen tables valued such issuers only through an argued P/B")

    computable = [m for m in METHOD_ORDER if getattr(row.results.get(m), "ok", False)]
    suitability: dict[str, dict[str, str]] = {}
    primary: list[str] = []
    secondary: list[str] = []
    for method in computable:
        if method in WITHHELD:
            suitability[method] = {"suitability": "WITHHELD", "reason": WITHHELD[method]}
            continue
        label, why = method_suitability(method, net_debt=net_debt, enterprise_value=ev,
                                        ttm_revenue=revenue, ttm_operating_income=operating_income)
        suitability[method] = {"suitability": label.value, "reason": why}
        if label is MethodSuitability.ECONOMICALLY_REASONABLE:
            primary.append(method)
        elif label is MethodSuitability.SECONDARY_ONLY:
            secondary.append(method)
    if not computable:
        reasons.append("NO_COMPUTABLE_METHOD: no multiple resolved at the decision session")
    elif not primary:
        reasons.append("NO_ECONOMICALLY_REASONABLE_METHOD: every computable method is SECONDARY_ONLY, "
                       "DATA_APPLICABLE or withheld; a primary anchor would need an economic argument")
    if reasons:
        return Derived(ticker, NOT_READY, tuple(reasons), None, suitability, inputs)

    margin = None if revenue in (None, 0) or operating_income is None else operating_income / revenue
    share = None if net_debt is None or ev in (None, 0) else abs(net_debt) / abs(ev)
    business = (f"{sic_description or 'SIC ' + str(sic)} (SIC {str(sic).strip()}). {VERSION}: a code-owned "
                f"judgement from the decision session's resolved fundamentals, with no reading of the "
                f"business. TTM revenue {_money(revenue)}, operating income {_money(operating_income)}"
                f"{'' if margin is None else f' ({margin:.1%} margin)'}, free cash flow {_money(fcf)}, "
                f"book equity {_money(equity)}, net debt {_money(net_debt)}"
                f"{'' if share is None else f' ({share:.1%} of enterprise value {_money(ev)})'}. "
                f"Method preference is D5-D0 section-17 suitability in METHOD_ORDER; P/B is withheld.")
    risks = [f"{VERSION}: no human reading of the business supports this method order; the primary is the "
             f"first section-17 ECONOMICALLY_REASONABLE method, not an argued framework choice",
             "P/B withheld: whether book value is this issuer's earning asset was not argued"]
    if fcf is not None and operating_income is not None and fcf > 0 and operating_income < 0:
        risks.append("free cash flow is positive while operating income is negative: the valuation rests on "
                     "the gap between them, which the TTM bundle cannot explain")
    if fcf is not None and operating_income is not None and fcf < 0 and operating_income > 0:
        risks.append("operating income is positive while free cash flow is negative: accrual earnings and "
                     "cash disagree, which the TTM bundle cannot explain")
    judgement = MethodJudgement(ticker=ticker, business=business, primary_preference=tuple(primary),
                                secondary_preference=tuple(secondary), unsuitable={},
                                valuation_risk=tuple(risks))
    return Derived(ticker, AUTO_READY, (), judgement, suitability, inputs)


def coverage(results: Sequence[Derived]) -> dict[str, Any]:
    """AUTO_READY / NOT_READY counts and the reason mix, for the section-12 measurement."""
    out = {"n": len(results), AUTO_READY: 0, NOT_READY: 0, "reasons": {}}
    for r in results:
        out[r.state] += 1
        for reason in r.reasons:
            key = reason.split(":")[0]
            out["reasons"][key] = out["reasons"].get(key, 0) + 1
    return out
