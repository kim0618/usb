"""H-V2-D5-P1.1 audit: practical share-class resolution, market cap and reopened enterprise value.

Offline, 0 model calls, $0. It reads the local reference-ticker snapshots, local SEC companyfacts, the
stored D2.1 packages and the local unadjusted grouped-daily panel. P1's cash, debt and net-debt
primitives are reused unchanged; no multiple is computed, no fair value, no target price, no decision.

Usage:
    python -m app.dev.audit_strategy_h_v2_d5_p1_1               # the ten D4-contacted issuers
    python -m app.dev.audit_strategy_h_v2_d5_p1_1 --d5-d1       # the frozen D5-D1 twelve, read-only
    python -m app.dev.audit_strategy_h_v2_d5_p1_1 --all         # all 22, coverage only
    python -m app.dev.audit_strategy_h_v2_d5_p1_1 --controls    # the known-multi-class controls
    python -m app.dev.audit_strategy_h_v2_d5_p1_1 --universe    # the resolver over every CIK on file
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import date, datetime

from app.backtest.strategy_h_v2.valuation.capital_structure import (
    CAPITAL_STRUCTURE_FIELD_SPECS,
    MAX_INSTANT_STALENESS_DAYS,
    CapitalStructureStatus,
    DebtStatus,
    InstantStatus,
    compose_total_debt,
    method_feasibility,
    resolve_cash_for_ev,
    resolve_net_debt,
    resolve_valuation_instant,
)
from app.backtest.strategy_h_v2.valuation.d5_d0_contract import D5_D1_SAMPLE
from app.backtest.strategy_h_v2.valuation.market_cap_gate import MarketCapStatus
from app.backtest.strategy_h_v2.valuation.share_class import (
    REFERENCE_TOPOLOGY_MAX_AGE_DAYS,
    ShareClassTopology,
    load_reference_snapshot,
    reopened_enterprise_value,
    resolve_market_cap,
    resolve_share_class,
)
from app.backtest.strategy_h_v2.valuation.ttm import construct_ttm_bundle
from app.dev.audit_strategy_h_v2_d5_p0_1 import D4_ISSUERS
from app.dev.audit_strategy_h_v2_d5_p1 import (
    _market_opens,
    last_session_close,
    load_issuer,
)
from app.dev.audit_strategy_h_v2_d5_p1 import D2_1_PACKAGES

SCHEMA = "H_V2_D5_P1_1_PRACTICAL_SHARE_CLASS_RESOLUTION_AUDIT_V1"

#: The §20 practical success thresholds, frozen in the contract document before the resolver was run
#: on any issuer and asserted here rather than read off the result.
SUCCESS_THRESHOLDS = {"market_cap_of_ten": 7, "enterprise_value_of_ten": 4,
                      "unsafe_market_cap": 0, "known_multi_class_misclassified": 0}

#: §9 controls. Every one is a company whose multiple common classes are a matter of public record, so
#: a resolver that calls any of them single-class has failed, whatever it does elsewhere.
KNOWN_MULTI_CLASS_CONTROLS: tuple[tuple[str, str], ...] = (
    ("GOOG/GOOGL", "0001652044"),
    ("FOX/FOXA", "0001754301"),
    ("BRK.A/BRK.B", "0001067983"),
    ("BF.A/BF.B", "0000014693"),
    ("BIO/BIO.B", "0000012208"),
    ("CENT/CENTA", "0000887733"),
    ("BELFA/BELFB", "0000729580"),
    ("AGM/AGM.A", "0000845877"),
    ("LBTYA/LBTYB/LBTYK", "0001570585"),
    ("LILA/LILAK", "0001712184"),
)

#: §9 single-class controls, for the other direction: a resolver that refuses these has no coverage.
KNOWN_SINGLE_CLASS_CONTROLS: tuple[tuple[str, str], ...] = (
    ("AAPL", "0000320193"),
    ("MSFT", "0000789019"),
    ("NVDA", "0001045810"),
)


def audit_issuer(ticker: str, cik: str | None, closes: dict[str, float], decision_date: date,
                 price_date: date, snapshot) -> dict | None:
    loaded = load_issuer(ticker, cik)
    if loaded is None:
        return None
    facts, cutoff, cik, _document = loaded
    opens = _market_opens(decision_date)

    share_class = resolve_share_class(cik, decision_date, snapshot)
    market_cap = resolve_market_cap(ticker, facts, cutoff, decision_date, opens,
                                    closes.get(ticker), price_date, share_class)
    cash = resolve_cash_for_ev(facts, cutoff, decision_date)
    debt = compose_total_debt(facts, cutoff, decision_date)
    net = resolve_net_debt(facts, cutoff, decision_date)
    ev = reopened_enterprise_value(market_cap, net, cutoff, decision_date)

    ttm = construct_ttm_bundle(facts, cutoff, specs=CAPITAL_STRUCTURE_FIELD_SPECS)
    equity = resolve_valuation_instant(facts, "equity", cutoff, decision_date)
    assets = resolve_valuation_instant(facts, "assets", cutoff, decision_date)
    feasibility = method_feasibility(ev, ttm, equity=equity)
    return {
        "ticker": ticker,
        "cik": cik,
        "decision_time": cutoff.isoformat(),
        "decision_date": decision_date.isoformat(),
        "share_class": share_class.to_dict(),
        "market_cap": market_cap.to_dict(),
        "cash": cash.to_dict(),
        "debt": debt.to_dict(),
        "net_debt": net.to_dict(),
        "enterprise_value": ev.to_dict(),
        "equity": equity.to_dict(),
        "assets": assets.to_dict(),
        "feasibility": {k: v.to_dict() for k, v in feasibility.items()},
    }


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
    assert session is not None
    snapshot = load_reference_snapshot(session)

    rows, skipped = [], []
    for ticker in tickers:
        row = audit_issuer(ticker, (ciks or {}).get(ticker), closes, session, session, snapshot)
        (rows if row is not None else skipped).append(row if row is not None else ticker)

    topology = Counter(r["share_class"]["topology"] for r in rows)
    confidence = Counter(r["share_class"]["confidence"] for r in rows)
    methods = sorted({m for r in rows for m in r["feasibility"]})
    return {
        "schema": SCHEMA,
        "issuers": len(rows),
        "skipped": skipped,
        "decision_date": session.isoformat(),
        "reference_snapshot": {
            "as_of": None if snapshot is None else snapshot.as_of.isoformat(),
            "path": None if snapshot is None else str(snapshot.path),
            "rows": None if snapshot is None else snapshot.rows,
            "ciks": None if snapshot is None else len(snapshot.by_cik),
            "age_days": None if snapshot is None else (session - snapshot.as_of).days,
            "max_age_days": REFERENCE_TOPOLOGY_MAX_AGE_DAYS,
        },
        "share_class_topology": dict(sorted(topology.items())),
        "share_class_confidence": dict(sorted(confidence.items())),
        "coverage": {
            "market_cap": _tally(rows, lambda r: r["market_cap"]["status"], MarketCapStatus.OK.value),
            "enterprise_value": _tally(rows, lambda r: r["enterprise_value"]["status"],
                                       CapitalStructureStatus.OK.value),
            "cash": _tally(rows, lambda r: r["cash"]["status"], InstantStatus.OK.value),
            "total_debt": _tally(rows, lambda r: r["debt"]["status"], DebtStatus.OK.value),
            "net_debt": _tally(rows, lambda r: r["net_debt"]["status"],
                               CapitalStructureStatus.OK.value),
            "equity": _tally(rows, lambda r: r["equity"]["status"], InstantStatus.OK.value),
        },
        "method_feasibility": {
            m: {"feasible": sum(1 for r in rows if r["feasibility"][m]["feasible"]),
                "numerator_ready": sum(1 for r in rows if r["feasibility"][m]["numerator_ready"]),
                "denominator_ready": sum(1 for r in rows if r["feasibility"][m]["denominator_ready"]),
                "of": len(rows)}
            for m in methods},
        "acceptance": acceptance(rows, snapshot, session),
        "rows": rows,
    }


def _tally(rows: list[dict], read, ok_value: str) -> dict:
    statuses = Counter(read(row) for row in rows)
    return {"ok": statuses.get(ok_value, 0), "of": len(rows),
            "statuses": dict(sorted(statuses.items()))}


def controls(decision_date: date, snapshot) -> dict:
    """§9: the resolver run against companies whose topology is a matter of public record."""
    multi, single = [], []
    for name, cik in KNOWN_MULTI_CLASS_CONTROLS:
        r = resolve_share_class(cik, decision_date, snapshot)
        multi.append({"control": name, "cik": cik, "topology": r.topology.value,
                      "reason": r.reason.value, "valuation_allowed": r.valuation_allowed,
                      "tickers": [s.ticker for s in r.securities],
                      "blocked": not r.valuation_allowed})
    for name, cik in KNOWN_SINGLE_CLASS_CONTROLS:
        r = resolve_share_class(cik, decision_date, snapshot)
        single.append({"control": name, "cik": cik, "topology": r.topology.value,
                       "confidence": r.confidence.value, "valuation_allowed": r.valuation_allowed,
                       "tickers": [s.ticker for s in r.securities]})
    return {
        "known_multi_class": multi,
        "known_multi_class_misclassified_as_single": [c["control"] for c in multi
                                                     if c["valuation_allowed"]],
        "known_multi_class_blocked": sum(1 for c in multi if c["blocked"]),
        "known_single_class": single,
        "known_single_class_allowed": sum(1 for c in single if c["valuation_allowed"]),
    }


def universe_coverage(decision_date: date, snapshot) -> dict:
    """The resolver run over every CIK in the snapshot, to see the shape of its refusals at scale.

    The ten and the twelve say whether this programme's issuers can be valued. This says what the rule
    costs in general, which is the only way to know whether a 100% single-class reading on 19 of 22 is
    the rule working or the corpus being unrepresentative.
    """
    if snapshot is None:
        return {"snapshot": None}
    topology: Counter = Counter()
    confidence: Counter = Counter()
    reason: Counter = Counter()
    for cik in snapshot.by_cik:
        r = resolve_share_class(cik, decision_date, snapshot)
        topology[r.topology.value] += 1
        confidence[r.confidence.value] += 1
        reason[r.reason.value] += 1
    allowed = sum(v for k, v in topology.items()
                  if k in (ShareClassTopology.SINGLE_CLASS_CONFIRMED.value,
                           ShareClassTopology.SINGLE_CLASS_LIKELY.value))
    return {
        "snapshot_as_of": snapshot.as_of.isoformat(),
        "ciks": len(snapshot.by_cik),
        "topology": dict(sorted(topology.items())),
        "confidence": dict(sorted(confidence.items())),
        "reason": dict(sorted(reason.items())),
        "valuation_allowed": allowed,
        "valuation_allowed_share": round(allowed / len(snapshot.by_cik), 4),
    }


def acceptance(rows: list[dict], snapshot, decision_date: date) -> dict:
    """The §21 safety criteria, counted rather than claimed."""
    unsafe_multi, silent_missing_reference, stale_shares = [], [], []
    future_reference, future_shares, value_on_refusal = [], [], []
    allowed_without_limitation, stale_reference_used = [], []
    for row in rows:
        sc, mc = row["share_class"], row["market_cap"]
        if mc["status"] == MarketCapStatus.OK.value:
            if sc["topology"] not in (ShareClassTopology.SINGLE_CLASS_CONFIRMED.value,
                                      ShareClassTopology.SINGLE_CLASS_LIKELY.value):
                unsafe_multi.append((row["ticker"], sc["topology"]))
            if sc["reason"] in ("NO_CIK", "NO_REFERENCE_SNAPSHOT", "CIK_ABSENT_FROM_REFERENCE",
                               "REFERENCE_SNAPSHOT_STALE"):
                silent_missing_reference.append((row["ticker"], sc["reason"]))
            if (sc["reference_snapshot_age_days"] or 0) > REFERENCE_TOPOLOGY_MAX_AGE_DAYS:
                stale_reference_used.append((row["ticker"], sc["reference_snapshot_age_days"]))
            if not mc["limitations"]:
                allowed_without_limitation.append(row["ticker"])
            shares = mc["shares"]
            if shares is None:
                unsafe_multi.append((row["ticker"], "OK market cap with no shares fact"))
            else:
                if shares["shares_age_days"] > shares["shares_max_age_days"]:
                    stale_shares.append((row["ticker"], shares["shares_age_days"]))
                if datetime.fromisoformat(shares["acceptance_time"]) > \
                        datetime.fromisoformat(mc["decision_time"]):
                    future_shares.append((row["ticker"], shares["acceptance_time"]))
        elif mc["market_cap"] is not None:
            value_on_refusal.append((row["ticker"], "market_cap", mc["status"]))

        if (sc["reference_snapshot_date"] is not None
                and date.fromisoformat(sc["reference_snapshot_date"]) > decision_date):
            future_reference.append((row["ticker"], sc["reference_snapshot_date"]))

        ev = row["enterprise_value"]
        if ev["status"] != CapitalStructureStatus.OK.value and ev["enterprise_value"] is not None:
            value_on_refusal.append((row["ticker"], "enterprise_value", ev["status"]))
    return {
        "known_multi_class_misclassified_as_single":
            controls(decision_date, snapshot)["known_multi_class_misclassified_as_single"],
        "unsafe_market_cap": unsafe_multi,
        "missing_reference_treated_as_single": silent_missing_reference,
        "stale_reference_used": stale_reference_used,
        "stale_shares_accepted": stale_shares,
        "future_reference_snapshot_used": future_reference,
        "future_shares_used": future_shares,
        "value_reported_on_a_refusal": value_on_refusal,
        "allowed_market_cap_without_disclosed_limitation": allowed_without_limitation,
    }


def verdict(ten: dict) -> dict:
    """§20 thresholds against the ten-issuer run, evaluated rather than asserted in prose."""
    mc = ten["coverage"]["market_cap"]["ok"]
    ev = ten["coverage"]["enterprise_value"]["ok"]
    acc = ten["acceptance"]
    unsafe = len(acc["unsafe_market_cap"])
    misclassified = len(acc["known_multi_class_misclassified_as_single"])
    checks = {
        "market_cap_coverage": (mc, SUCCESS_THRESHOLDS["market_cap_of_ten"],
                                mc >= SUCCESS_THRESHOLDS["market_cap_of_ten"]),
        "enterprise_value_coverage": (ev, SUCCESS_THRESHOLDS["enterprise_value_of_ten"],
                                      ev >= SUCCESS_THRESHOLDS["enterprise_value_of_ten"]),
        "unsafe_market_cap": (unsafe, SUCCESS_THRESHOLDS["unsafe_market_cap"], unsafe == 0),
        "known_multi_class_misclassified": (misclassified,
                                            SUCCESS_THRESHOLDS["known_multi_class_misclassified"],
                                            misclassified == 0),
        "no_value_on_a_refusal": (len(acc["value_reported_on_a_refusal"]), 0,
                                  not acc["value_reported_on_a_refusal"]),
        "no_stale_shares": (len(acc["stale_shares_accepted"]), 0, not acc["stale_shares_accepted"]),
        "no_future_inputs": (len(acc["future_reference_snapshot_used"])
                             + len(acc["future_shares_used"]), 0,
                             not acc["future_reference_snapshot_used"]
                             and not acc["future_shares_used"]),
        "no_missing_reference_as_single": (len(acc["missing_reference_treated_as_single"]), 0,
                                          not acc["missing_reference_treated_as_single"]),
    }
    return {"checks": {k: {"observed": v[0], "required": v[1], "pass": v[2]}
                       for k, v in checks.items()},
            "all_pass": all(v[2] for v in checks.values())}


def main() -> None:
    argv = sys.argv[1:]
    if "--d5-d1" in argv:
        tickers = tuple(t for t, _ in D5_D1_SAMPLE)
        ciks = {t: c for t, c in D5_D1_SAMPLE}
    elif "--all" in argv:
        tickers = D4_ISSUERS + tuple(t for t, _ in D5_D1_SAMPLE)
        ciks = {t: c for t, c in D5_D1_SAMPLE}
    else:
        tickers, ciks = D4_ISSUERS, {}
    report = audit(tickers, ciks)

    if "--universe" in argv:
        day = date.fromisoformat(report["decision_date"])
        print(json.dumps({"schema": report["schema"], "decision_date": report["decision_date"],
                          "universe": universe_coverage(day, load_reference_snapshot(day))},
                         indent=2, default=str))
        return

    if "--controls" in argv:
        snapshot = load_reference_snapshot(date.fromisoformat(report["decision_date"]))
        print(json.dumps({"schema": report["schema"],
                          "decision_date": report["decision_date"],
                          "controls": controls(date.fromisoformat(report["decision_date"]),
                                               snapshot)}, indent=2, default=str))
        return

    out = {
        "schema": report["schema"],
        "corpus": ("ALL_22" if "--all" in argv else
                   "D5_D1_FROZEN_TWELVE" if "--d5-d1" in argv else "D4_TEN"),
        "issuers": report["issuers"],
        "skipped": report["skipped"],
        "decision_date": report["decision_date"],
        "reference_snapshot": report["reference_snapshot"],
        "staleness_bounds": {"financial_instant_days": MAX_INSTANT_STALENESS_DAYS,
                             "reference_topology_days": REFERENCE_TOPOLOGY_MAX_AGE_DAYS},
        "share_class_topology": report["share_class_topology"],
        "share_class_confidence": report["share_class_confidence"],
        "coverage": {k: f"{v['ok']}/{v['of']}  {v['statuses']}"
                     for k, v in report["coverage"].items()},
        "method_feasibility": {k: f"{v['feasible']}/{v['of']}  "
                                  f"(num {v['numerator_ready']}, den {v['denominator_ready']})"
                               for k, v in report["method_feasibility"].items()},
        "acceptance": report["acceptance"],
        "per_issuer": [{"ticker": r["ticker"],
                        "topology": r["share_class"]["topology"],
                        "confidence": r["share_class"]["confidence"],
                        "reason": r["share_class"]["reason"],
                        "market_cap": r["market_cap"]["status"],
                        "market_cap_value": r["market_cap"]["market_cap"],
                        "net_debt": r["net_debt"]["status"],
                        "enterprise_value": r["enterprise_value"]["status"],
                        "ev_value": r["enterprise_value"]["enterprise_value"]}
                       for r in report["rows"]],
    }
    if out["corpus"] == "D4_TEN":
        out["verdict"] = verdict(report)
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
