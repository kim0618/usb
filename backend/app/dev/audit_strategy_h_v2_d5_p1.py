"""H-V2-D5-P1 audit: cash, total debt, net debt, market cap and enterprise value coverage.

Offline, 0 model calls, $0. It reads local SEC companyfacts, the stored D2.1 packages and the local
unadjusted grouped-daily panel. No multiple is computed, no fair value, no target price, no decision.

Usage:
    python -m app.dev.audit_strategy_h_v2_d5_p1              # the ten D4-contacted issuers
    python -m app.dev.audit_strategy_h_v2_d5_p1 --d5-d1      # the frozen D5-D1 twelve, read-only
    python -m app.dev.audit_strategy_h_v2_d5_p1 --residual   # the debt-tag residual scan only
"""

from __future__ import annotations

import gzip
import json
import re
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

from app.backtest.strategy_h0.facts import extract_companyfacts
from app.backtest.strategy_h0.h0_5 import resolve_pit_shares
from app.backtest.strategy_h_v2.valuation.capital_structure import (
    CAPITAL_STRUCTURE_FIELD_SPECS,
    DEBT_SLOT_TAGS,
    DEBT_TAGS_THAT_ARE_NOT_A_BALANCE,
    LEASE_BUNDLED_DEBT_TAGS,
    LEASE_LIABILITY_TAGS_OUT_OF_SCOPE,
    MAX_INSTANT_STALENESS_DAYS,
    CapitalStructureStatus,
    DebtMethod,
    DebtSlot,
    DebtStatus,
    InstantStatus,
    compose_total_debt,
    enterprise_value,
    method_feasibility,
    resolve_cash_for_ev,
    resolve_net_debt,
    resolve_valuation_instant,
)
from app.backtest.strategy_h_v2.valuation.d5_d0_contract import D5_D1_SAMPLE
from app.backtest.strategy_h_v2.valuation.market_cap_gate import (
    MarketCapStatus,
    ShareClassState,
    valuation_market_cap,
)
from app.backtest.strategy_h_v2.valuation.ttm import construct_ttm_bundle
from app.dev.audit_strategy_h_v2_d5_p0_1 import (
    D2_1_PACKAGES,
    D4_ISSUERS,
    FACTS_ROOTS,
    SUBMISSION_ROOTS,
    _gz,
)
from app.backtest.strategy_h0.pilot import acceptance_index
from app.market.calendar import MarketCalendar

SCHEMA = "H_V2_D5_P1_CAPITAL_STRUCTURE_COVERAGE_AUDIT_V1"

DAILY_PANEL_DIR = Path("data/runtime/strategy_b_e0/mirror/market_data/raw/massive/grouped_daily")

#: The share-class state every issuer is in, and the audit states it rather than letting a default
#: state it. This repository has no detector, so the honest state is UNRESOLVED and every market cap
#: and every enterprise value below is a refusal. That is the measured outcome, not a bug in the run.
SHARE_CLASS_STATE = ShareClassState.UNRESOLVED

#: Tags whose name mentions debt and which are not a balance of borrowings, for the residual scan.
_NOT_A_BALANCE = re.compile(
    r"Maturities|FaceAmount|FairValue|MaximumBorrowingCapacity|Unamortized|Discount|Premium|"
    r"IssuanceCost|InterestRate|Covenant|PeriodicPayment|Collateral|Pledged|Securit(y|ies)|"
    r"AvailableForSale|Investment|Proceeds|Repayments|RightOfUse|PaymentsDue|UndiscountedExcess|"
    r"FutureMinimumPayments|LeasedAssets|BalanceSheetAssets|BusinessAcquisition|Accrued|"
    r"WeightedAverage|RemainingTerm|Fee|Equity[Cc]omponent|Expense|AllowanceForCreditLoss", re.I)
_DEBT_LIKE_NAME = re.compile(
    r"Debt|Borrow|LineOfCredit|LinesOfCredit|Revolv|Notes?Payable|LoansPayable|FinanceLease|"
    r"CapitalLease|Commercial[Pp]aper|Overdraft|Obligation", re.I)
#: Every tag the contract has already placed: in a debt slot, out of scope as a lease, or declared
#: not to be a balance of borrowings at all. What the scan reports is therefore the tags the contract
#: has not accounted for, which is the only thing worth looking at.
_ACCOUNTED_FOR = (
    {tag for tags in DEBT_SLOT_TAGS.values() for tag in tags}
    | set(LEASE_LIABILITY_TAGS_OUT_OF_SCOPE)
    | set(LEASE_BUNDLED_DEBT_TAGS)
    | {tag for tag, _ in DEBT_TAGS_THAT_ARE_NOT_A_BALANCE})

INSTANT_SIDE_FIELDS = ("equity", "assets")


def last_session_close(tickers: set[str], on_or_before: date,
                       panel_dir: Path = DAILY_PANEL_DIR) -> tuple[dict[str, float], date | None]:
    """The newest unadjusted close at or before `on_or_before`, per ticker, and its session.

    Sessions are read newest-first and the walk stops once every ticker has a close, because one
    instant is wanted rather than a series. `run_strategy_h_v2_d4_1.load_price_panel` builds the full
    501-session panel, which is the right shape for a return and the wrong shape for this.
    """
    closes: dict[str, float] = {}
    session: date | None = None
    for path in sorted(panel_dir.rglob("*.json.gz"), reverse=True):
        doc = json.loads(gzip.decompress(path.read_bytes()))
        if "body" not in doc or "session" not in doc:
            continue
        day = date.fromisoformat(doc["session"])
        if day > on_or_before:
            continue
        if session is None:
            session = day
        for row in doc["body"].get("results", []):
            ticker = row.get("T")
            if ticker in tickers and ticker not in closes and isinstance(row.get("c"), (int, float)):
                closes[ticker] = float(row["c"])
        if len(closes) == len(tickers):
            break
    return closes, session


def load_issuer(ticker: str, cik: str | None = None):
    """Companyfacts extracted with the capital-structure registry, plus the D2.1 data cutoff."""
    package = D2_1_PACKAGES / f"{ticker}.json"
    cutoff: datetime | None = None
    if package.exists():
        bundle = json.loads(package.read_text())["evidence_bundle"]
        cik = bundle["identity"]["cik"]
        cutoff = datetime.fromisoformat(bundle["data_cutoff"])
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=timezone.utc)
    if cik is None or cutoff is None:
        return None
    facts_root = next((r for r in FACTS_ROOTS
                       if (r / "companyfacts" / f"CIK{cik}.json.gz").exists()), None)
    submissions = next((_gz(r / f"CIK{cik}" / f"CIK{cik}.json.gz") for r in SUBMISSION_ROOTS
                        if (r / f"CIK{cik}" / f"CIK{cik}.json.gz").exists()), None)
    if facts_root is None or submissions is None:
        return None
    document = _gz(facts_root / "companyfacts" / f"CIK{cik}.json.gz")
    facts = extract_companyfacts(document, acceptance_index(submissions),
                                 specs=CAPITAL_STRUCTURE_FIELD_SPECS)
    return facts, cutoff, cik, document


def residual_debt_tags(document: dict, period_end: date) -> list[tuple[str, float]]:
    """Debt-named instant tags at `period_end` that are in no slot and nonzero.

    This is the test of `DEBT_COMPOSITION_RESIDUAL_RISK`: a composition is complete only over the tags
    it knows, so the way to find out whether a borrowing balance was left out is to look at what else
    the filer tagged on the same balance sheet.
    """
    gaap = (document.get("facts") or {}).get("us-gaap") or {}
    out: list[tuple[str, float]] = []
    for tag, concept in gaap.items():
        if tag in _ACCOUNTED_FOR or not _DEBT_LIKE_NAME.search(tag) or _NOT_A_BALANCE.search(tag):
            continue
        for row in (concept.get("units") or {}).get("USD") or []:
            if row.get("start") is None and row.get("end") == period_end.isoformat() and row.get("val"):
                out.append((tag, float(row["val"])))
    return sorted(out)


def audit_issuer(ticker: str, cik: str | None, closes: dict[str, float],
                 decision_date: date) -> dict | None:
    loaded = load_issuer(ticker, cik)
    if loaded is None:
        return None
    facts, cutoff, cik, document = loaded
    opens = _market_opens(decision_date)

    cash = resolve_cash_for_ev(facts, cutoff, decision_date)
    debt = compose_total_debt(facts, cutoff, decision_date)
    net = resolve_net_debt(facts, cutoff, decision_date)
    market_cap = valuation_market_cap(facts, decision_date, opens, closes.get(ticker),
                                      SHARE_CLASS_STATE)
    ev = enterprise_value(market_cap, net, cutoff, decision_date)

    ttm = construct_ttm_bundle(facts, cutoff, specs=CAPITAL_STRUCTURE_FIELD_SPECS)
    equity = resolve_valuation_instant(facts, "equity", cutoff, decision_date)
    assets = resolve_valuation_instant(facts, "assets", cutoff, decision_date)
    feasibility = method_feasibility(ev, ttm, equity=equity)

    # What the market cap would need, measured without producing one: the share-class gate is first in
    # `valuation_market_cap`, so a shares or price failure behind it is invisible in the status.
    shares = resolve_pit_shares(facts, decision_date, opens)
    return {
        "ticker": ticker,
        "cik": cik,
        "decision_time": cutoff.isoformat(),
        "decision_date": decision_date.isoformat(),
        "cash": cash.to_dict(),
        "debt": debt.to_dict(),
        "net_debt": net.to_dict(),
        "enterprise_value": ev.to_dict(),
        "equity": equity.to_dict(),
        "assets": assets.to_dict(),
        "ttm": {name: {"status": r.status.value, "method": r.method.value if r.method else None,
                       "period_end": r.period_end.isoformat() if r.period_end else None}
                for name, r in ttm.items()},
        "feasibility": {k: v.to_dict() for k, v in feasibility.items()},
        "market_cap_inputs": {
            "share_class_state": SHARE_CLASS_STATE.value,
            "shares_resolved": shares.fact is not None,
            "shares_reason": shares.reason,
            "unadjusted_close_available": closes.get(ticker) is not None,
        },
        "residual_debt_tags": (residual_debt_tags(document, debt.period_end)
                               if debt.period_end else []),
    }


_OPENS_CACHE: dict[date, list] = {}


def _market_opens(decision_date: date) -> list:
    """Regular-session opens up to the decision date, which `resolve_pit_shares` needs to decide the
    first session on which a filing's acceptance time was tradable."""
    if decision_date not in _OPENS_CACHE:
        calendar = MarketCalendar()
        days = [d for d in _panel_sessions() if d <= decision_date]
        opens = [calendar.regular_market_open(d) for d in days]
        _OPENS_CACHE[decision_date] = [o for o in opens if o]
    return _OPENS_CACHE[decision_date]


_SESSIONS: list[date] | None = None


def _panel_sessions() -> list[date]:
    global _SESSIONS
    if _SESSIONS is None:
        _SESSIONS = sorted(date.fromisoformat(p.stem.removesuffix(".json"))
                           for p in DAILY_PANEL_DIR.rglob("*.json.gz"))
    return _SESSIONS


def audit(tickers: tuple[str, ...], ciks: dict[str, str] | None = None) -> dict:
    cutoffs = []
    for ticker in tickers:
        package = D2_1_PACKAGES / f"{ticker}.json"
        if package.exists():
            raw = json.loads(package.read_text())["evidence_bundle"]["data_cutoff"]
            cutoffs.append(datetime.fromisoformat(raw).date())
    if not cutoffs:
        return {"schema": SCHEMA, "issuers": 0, "skipped": list(tickers), "rows": []}
    closes, session = last_session_close(set(tickers), max(cutoffs))
    decision_date = session
    assert decision_date is not None

    rows, skipped = [], []
    for ticker in tickers:
        row = audit_issuer(ticker, (ciks or {}).get(ticker), closes, decision_date)
        (rows if row is not None else skipped).append(row if row is not None else ticker)

    coverage = {
        "cash": _tally(rows, lambda r: r["cash"]["status"], InstantStatus.OK.value),
        "total_debt": _tally(rows, lambda r: r["debt"]["status"], DebtStatus.OK.value),
        "net_debt": _tally(rows, lambda r: r["net_debt"]["status"], CapitalStructureStatus.OK.value),
        "market_cap": _tally(rows, lambda r: r["enterprise_value"]["market_cap_provenance"]["status"],
                             MarketCapStatus.OK.value),
        "enterprise_value": _tally(rows, lambda r: r["enterprise_value"]["status"],
                                   CapitalStructureStatus.OK.value),
        "equity": _tally(rows, lambda r: r["equity"]["status"], InstantStatus.OK.value),
        "assets": _tally(rows, lambda r: r["assets"]["status"], InstantStatus.OK.value),
    }
    methods = sorted({m for r in rows for m in r["feasibility"]})
    feasibility = {
        m: {"feasible": sum(1 for r in rows if r["feasibility"][m]["feasible"]),
            "denominator_ready": sum(1 for r in rows if r["feasibility"][m]["denominator_ready"]),
            "numerator_ready": sum(1 for r in rows if r["feasibility"][m]["numerator_ready"]),
            "of": len(rows)}
        for m in methods
    }

    return {
        "schema": SCHEMA,
        "issuers": len(rows),
        "skipped": skipped,
        "decision_date": decision_date.isoformat(),
        "staleness_bound_days": MAX_INSTANT_STALENESS_DAYS,
        "coverage": coverage,
        "debt_methods": dict(Counter(r["debt"]["construction_method"] for r in rows
                                     if r["debt"]["status"] == DebtStatus.OK.value)),
        "method_feasibility": feasibility,
        "acceptance": acceptance(rows),
        "rows": rows,
    }


def _tally(rows: list[dict], read, ok_value: str) -> dict:
    statuses = Counter(read(row) for row in rows)
    return {"ok": statuses.get(ok_value, 0), "of": len(rows),
            "statuses": dict(sorted(statuses.items()))}


def acceptance(rows: list[dict]) -> dict:
    """The §21-§23 acceptance criteria, counted rather than claimed."""
    stale_accepted, zero_without_evidence, double_counted = [], [], []
    value_on_refusal, unsafe_multi_class, future_components = [], [], []
    date_mismatch_netted, residuals, lease_scope_unresolved = [], [], []
    for row in rows:
        decision_date = date.fromisoformat(row["decision_date"])
        decision_time = datetime.fromisoformat(row["decision_time"])
        for name in ("cash", "equity", "assets"):
            record = row[name]
            if record["status"] == InstantStatus.OK.value:
                if (record["age_days"] or 0) > MAX_INSTANT_STALENESS_DAYS:
                    stale_accepted.append((row["ticker"], name, record["age_days"]))
                if datetime.fromisoformat(record["fact"]["acceptance_time"]) > decision_time:
                    future_components.append((row["ticker"], name))
            elif record["value"] is not None:
                value_on_refusal.append((row["ticker"], name, record["status"]))

        debt = row["debt"]
        if debt["status"] == DebtStatus.OK.value:
            if (debt["age_days"] or 0) > MAX_INSTANT_STALENESS_DAYS:
                stale_accepted.append((row["ticker"], "total_debt", debt["age_days"]))
            if debt["value"] == 0 and not debt["components"]:
                zero_without_evidence.append(row["ticker"])
            slots = [c["slot"] for c in debt["components"]]
            expected = ([DebtSlot.REPORTED_TOTAL.value]
                        if debt["construction_method"] == DebtMethod.REPORTED_TOTAL.value
                        else [DebtSlot.CURRENT.value, DebtSlot.NONCURRENT.value])
            if slots != expected:
                double_counted.append((row["ticker"], debt["construction_method"], slots))
            ends = {c["end"] for c in debt["components"]}
            if len(ends) > 1:
                double_counted.append((row["ticker"], "components span period ends", sorted(ends)))
            for component in debt["components"]:
                if datetime.fromisoformat(component["acceptance_time"]) > decision_time:
                    future_components.append((row["ticker"], component["tag"]))
            difference = debt["reported_total_minus_components"]
            if difference not in (None, 0.0):
                residuals.append((row["ticker"], difference))
            if debt["lease_bundled_tags_at_same_end"]:
                lease_scope_unresolved.append((row["ticker"], debt["lease_bundled_tags_at_same_end"]))
        elif debt["value"] is not None:
            value_on_refusal.append((row["ticker"], "total_debt", debt["status"]))

        net = row["net_debt"]
        if net["status"] == CapitalStructureStatus.OK.value:
            if net["cash"]["period_end"] != net["debt"]["period_end"]:
                date_mismatch_netted.append((row["ticker"], net["cash"]["period_end"],
                                             net["debt"]["period_end"]))
        elif net["net_debt"] is not None:
            value_on_refusal.append((row["ticker"], "net_debt", net["status"]))

        ev = row["enterprise_value"]
        class_state = row["market_cap_inputs"]["share_class_state"]
        if ev["status"] == CapitalStructureStatus.OK.value:
            if class_state != ShareClassState.SINGLE_CLASS.value:
                unsafe_multi_class.append((row["ticker"], class_state))
        elif ev["enterprise_value"] is not None:
            value_on_refusal.append((row["ticker"], "enterprise_value", ev["status"]))
        if (date.fromisoformat(ev["decision_date"]) != decision_date):
            value_on_refusal.append((row["ticker"], "decision_date", ev["decision_date"]))

    blocked_only_by_share_class = [
        row["ticker"] for row in rows
        if row["net_debt"]["status"] == CapitalStructureStatus.OK.value
        and row["market_cap_inputs"]["shares_resolved"]
        and row["market_cap_inputs"]["unadjusted_close_available"]
        and row["enterprise_value"]["status"] == CapitalStructureStatus.MULTI_CLASS_UNKNOWN.value
    ]
    return {
        "stale_instant_accepted": stale_accepted,
        "debt_zero_without_fact_evidence": zero_without_evidence,
        "debt_double_counted": double_counted,
        "value_reported_on_a_refusal": value_on_refusal,
        "multi_class_unsafe_enterprise_value": unsafe_multi_class,
        "future_components": future_components,
        "net_debt_across_two_balance_sheets": date_mismatch_netted,
        "reported_total_versus_components_nonzero": residuals,
        "debt_scope_lease_question_unresolved": lease_scope_unresolved,
        "unslotted_nonzero_debt_named_tags": [(r["ticker"], r["residual_debt_tags"])
                                              for r in rows if r["residual_debt_tags"]],
        "enterprise_value_blocked_only_by_share_class": blocked_only_by_share_class,
    }


def main() -> None:
    argv = sys.argv[1:]
    use_d5_d1 = "--d5-d1" in argv
    if use_d5_d1:
        tickers = tuple(t for t, _ in D5_D1_SAMPLE)
        ciks = {t: c for t, c in D5_D1_SAMPLE}
    else:
        tickers, ciks = D4_ISSUERS, {}
    report = audit(tickers, ciks)
    if "--residual" in argv:
        print(json.dumps({"schema": report["schema"],
                          "residual": report["acceptance"]["unslotted_nonzero_debt_named_tags"]},
                         indent=2, default=str))
        return
    print(json.dumps({
        "schema": report["schema"],
        "corpus": "D5_D1_FROZEN_TWELVE" if use_d5_d1 else "D4_TEN",
        "issuers": report["issuers"],
        "skipped": report["skipped"],
        "decision_date": report.get("decision_date"),
        "staleness_bound_days": report.get("staleness_bound_days"),
        "coverage": {k: f"{v['ok']}/{v['of']}  {v['statuses']}" for k, v in
                     report.get("coverage", {}).items()},
        "debt_methods": report.get("debt_methods"),
        "method_feasibility": report.get("method_feasibility"),
        "acceptance": report.get("acceptance"),
    }, indent=2, default=str))
    if "--rows" in argv:
        print(json.dumps(report["rows"], indent=2, default=str))


if __name__ == "__main__":
    main()
