"""H-V2-D5-D1: valuation data / method applicability pilot over the frozen twelve.

Offline, deterministic, 0 model calls, $0. It reads local SEC companyfacts, the stored D2.1
packages, the local Polygon reference-ticker snapshot and the local unadjusted grouped-daily panel.
Every primitive - PIT shares, market cap, cash, debt, net debt, enterprise value, the TTM bundle and
the instant balance-sheet fields - is P0/P0.1/P1/P1.1's and is called unchanged. What this step adds
is the division, the readiness classification and the audit of both.

No fair value, no target multiple, no TP1/TP2, no scenario, no recommendation, no forward return.

Usage:
    python -m app.dev.run_strategy_h_v2_d5_d1              # the frozen twelve
    python -m app.dev.run_strategy_h_v2_d5_d1 --summary    # the §32 report lines only
    python -m app.dev.run_strategy_h_v2_d5_d1 --table      # the §22 company-level table
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import date, datetime
from typing import Mapping

from app.backtest.strategy_h_v2.valuation.capital_structure import (
    CAPITAL_STRUCTURE_FIELD_SPECS,
    MAX_INSTANT_STALENESS_DAYS,
    compose_total_debt,
    method_feasibility,
    resolve_cash_for_ev,
    resolve_net_debt,
    resolve_valuation_instant,
)
from app.backtest.strategy_h_v2.valuation.d5_d0_contract import (
    D5_D1_CHECKSUM,
    D5_D1_SAMPLE,
    MIN_METHODS_FOR_COMPLETE,
    V_GATES,
    d5_d1_checksum,
)
from app.backtest.strategy_h_v2.valuation.multiples import (
    METHOD_ORDER,
    MULTIPLE_SPECS,
    MethodSuitability,
    MultipleBucket,
    MultipleStatus,
    compute_multiples,
    d0_completeness,
    data_readiness,
    derive_ebitda,
    method_suitability,
    usable_methods,
)
from app.backtest.strategy_h_v2.valuation.share_class import (
    REFERENCE_TOPOLOGY_MAX_AGE_DAYS,
    load_reference_snapshot,
    reopened_enterprise_value,
    resolve_market_cap,
    resolve_share_class,
)
from app.backtest.strategy_h_v2.valuation.ttm import (
    MAX_TTM_PERIOD_AGE_DAYS,
    construct_ttm_bundle,
)
from app.dev.audit_strategy_h_v2_d5_p1 import (
    D2_1_PACKAGES,
    _market_opens,
    last_session_close,
    load_issuer,
)

SCHEMA = "H_V2_D5_D1_VALUATION_DATA_PILOT_V1"

# -------------------------------------------------------------------------------------------------
# §19. The practical success gate, frozen before the pilot ran
# -------------------------------------------------------------------------------------------------
#
# D5-D0 §Q froze eight gates (V1..V8) and they remain authoritative; every one of them is a
# correctness gate with a threshold of zero, and NONE of them is a coverage threshold. §19 of this
# brief therefore applies: where the frozen contract has no authoritative coverage threshold, this
# step freezes the brief's, verbatim, before running. They are asserted against the result below and
# are not read off it.
#
# Stated plainly, because it matters for how the verdict is read: at the time these were written the
# P1.1 input coverage for this same twelve was already on record - market cap 7/12, enterprise value
# 3/12 - so the 8/12 readiness bar and the "3 methods at >= 4/12" bar were known to be at risk. They
# are frozen as the brief wrote them anyway. A threshold adjusted to what the data will produce is
# not a threshold.
SUCCESS_GATE: Mapping[str, int] = {
    "valuation_ready_issuers_min": 8,          # §19, of 12, READY + READY_WITH_LIMITATIONS
    "methods_with_coverage_at_least_4_min": 3,  # §19, methods whose OK count is >= 4 of 12
    "silent_wrong_values_max": 0,              # §19 and D5-D0 V7
    "stale_inputs_used_max": 0,                # §19 and D5-D0 V1/V3
    "future_inputs_used_max": 0,               # §19
}
METHOD_COVERAGE_FLOOR = 4  # the "coverage >= 4 / 12" in the second clause


# -------------------------------------------------------------------------------------------------
# Input snapshot (§6)
# -------------------------------------------------------------------------------------------------

def _instant_record(res) -> dict:
    d = res.to_dict()
    return {"status": d["status"], "value": d["value"], "period": d["period_end"],
            "freshness_days": d["age_days"], "freshness_bound_days": MAX_INSTANT_STALENESS_DAYS,
            "provenance": None if res.fact is None else f"{res.fact.tag} @ {res.fact.accession}"}


def _ttm_record(res) -> dict:
    return {"status": res.status.value, "value": res.value, "unit": res.unit,
            "period": None if res.period_end is None else
            f"{res.period_start}..{res.period_end}",
            "freshness_days": None, "freshness_bound_days": MAX_TTM_PERIOD_AGE_DAYS,
            "provenance": None if res.method is None else res.method.value,
            "components": [c.fact_id for c in res.components]}


def snapshot_issuer(ticker: str, cik: str | None, closes: dict[str, float], decision_date: date,
                    price_date: date, snapshot) -> dict | None:
    """Every §6 primitive for one issuer, then the seven multiples. Primitives are called, not
    reimplemented: the input half of this row is bit-identical to what the P1.1 audit reports."""
    loaded = load_issuer(ticker, cik)
    if loaded is None:
        return None
    facts, cutoff, cik, _document = loaded
    opens = _market_opens(decision_date)
    price = closes.get(ticker)

    share_class = resolve_share_class(cik, decision_date, snapshot)
    market_cap = resolve_market_cap(ticker, facts, cutoff, decision_date, opens, price,
                                    price_date, share_class)
    cash = resolve_cash_for_ev(facts, cutoff, decision_date)
    debt = compose_total_debt(facts, cutoff, decision_date)
    net = resolve_net_debt(facts, cutoff, decision_date)
    ev = reopened_enterprise_value(market_cap, net, cutoff, decision_date)
    ttm = construct_ttm_bundle(facts, cutoff, specs=CAPITAL_STRUCTURE_FIELD_SPECS)
    equity = resolve_valuation_instant(facts, "equity", cutoff, decision_date)
    ebitda = derive_ebitda(ttm.get("operating_income"), ttm.get("depreciation_amortization"))

    results = compute_multiples(market_cap=market_cap.resolution, enterprise_value=ev, ttm=ttm,
                                equity=equity, price=price)
    usable = usable_methods(results)
    readiness = data_readiness(len(usable))
    completeness = d0_completeness(market_cap_valid=market_cap.ok,
                                   usable_method_count=len(usable))

    revenue = ttm.get("revenue")
    oi = ttm.get("operating_income")
    suitability = {
        method: dict(zip(("suitability", "suitability_reason"),
                         (lambda t: (t[0].value, t[1]))(method_suitability(
                             method,
                             net_debt=net.value if net.ok else None,
                             enterprise_value=ev.value if ev.ok else None,
                             ttm_revenue=revenue.value if revenue is not None and revenue.ok
                             else None,
                             ttm_operating_income=oi.value if oi is not None and oi.ok else None))))
        for method in usable
    }
    primary = tuple(m for m in usable
                    if suitability[m]["suitability"] == MethodSuitability.ECONOMICALLY_REASONABLE)
    secondary = tuple(m for m in usable if m not in primary)

    return {
        "ticker": ticker,
        "cik": cik,
        "decision_time": cutoff.isoformat(),
        "decision_date": decision_date.isoformat(),
        "inputs": {
            "price": {"status": "OK" if price is not None else "MISSING", "value": price,
                      "period": price_date.isoformat(), "freshness_days": 0,
                      "provenance": "local unadjusted grouped-daily panel, regular-session close"},
            "pit_shares": (market_cap.to_dict()["shares"] or {"status": "MISSING"}),
            "share_class": {"status": share_class.topology.value,
                            "confidence": share_class.confidence.value,
                            "reason": share_class.reason.value,
                            "valuation_allowed": share_class.valuation_allowed,
                            "provenance": share_class.to_dict().get("snapshot_as_of"),
                            "limitations": list(share_class.limitations)},
            "market_cap": {"status": market_cap.resolution.status.value,
                           "value": market_cap.value, "period": decision_date.isoformat(),
                           "provenance": "unadjusted close x PIT raw shares, single class"},
            "cash": _instant_record(cash),
            "total_debt": {"status": debt.status.value, "value": debt.value,
                           "period": None if debt.period_end is None
                           else debt.period_end.isoformat(),
                           "freshness_days": debt.age_days,
                           "freshness_bound_days": MAX_INSTANT_STALENESS_DAYS,
                           "provenance": None if debt.method is None else debt.method.value},
            "net_debt": {"status": net.status.value, "value": net.value,
                         "period": None if net.period_end is None
                         else net.period_end.isoformat(),
                         "provenance": "total debt - cash at one common balance-sheet date"},
            "enterprise_value": {"status": ev.status.value, "value": ev.value,
                                 "period": decision_date.isoformat(),
                                 "provenance": "market cap + net debt",
                                 "components_sum": ev.components_sum},
            "book_equity": _instant_record(equity),
            "ttm_revenue": _ttm_record(ttm["revenue"]),
            "ttm_operating_income": _ttm_record(ttm["operating_income"]),
            "ttm_net_income": _ttm_record(ttm["net_income"]),
            "ttm_eps_diluted": _ttm_record(ttm["eps_diluted"]),
            "ttm_operating_cash_flow": _ttm_record(ttm["operating_cash_flow"]),
            "ttm_capex": _ttm_record(ttm["capex"]),
            "ttm_free_cash_flow": _ttm_record(ttm["free_cash_flow"]),
            "ttm_depreciation_amortization": _ttm_record(ttm["depreciation_amortization"]),
            "derived_ebitda": ebitda.to_dict(),
        },
        "multiples": {m: results[m].to_dict() for m in METHOD_ORDER},
        "feasibility": {k: v.to_dict()
                        for k, v in method_feasibility(ev, ttm, equity=equity).items()},
        "usable_methods": list(usable),
        "usable_method_count": len(usable),
        "readiness": readiness.value,
        "d0_completeness": completeness.value,
        "suitability": suitability,
        "primary_candidate_methods": list(primary),
        "secondary_methods": list(secondary),
        "limitations": _issuer_limitations(results, share_class, ev, debt),
    }


def _issuer_limitations(results, share_class, ev, debt) -> list[str]:
    out: list[str] = []
    if not share_class.valuation_allowed:
        out.append(f"share class {share_class.topology.value}/{share_class.reason.value}: "
                   f"market cap fail-closed")
    out.extend(share_class.limitations)
    if not ev.ok:
        out.append(f"enterprise value {ev.status.value}: every EV method blocked")
    elif debt.lease_bundled_tags_at_same_end:
        # P1 records this on the debt resolution and deliberately does not gate on it. It becomes a
        # published-multiple limitation here, because every EV multiple this issuer reports inherits
        # a debt figure whose scope the filing does not establish.
        bundled = ", ".join(f"{tag}={value:,.0f}"
                            for tag, value in debt.lease_bundled_tags_at_same_end)
        out.append(f"total debt {debt.value:,.0f} is reported alongside lease-bundled debt tags "
                   f"({bundled}) at the same balance-sheet date, so whether it contains a lease "
                   f"liability is not established by the filing; every EV multiple below inherits "
                   f"that scope question")
    blocked = Counter(r["status"] for r in (x.to_dict() for x in results.values())
                      if r["status"] != MultipleStatus.OK.value)
    for status, n in sorted(blocked.items()):
        out.append(f"{n} method(s) {status}")
    return out


# -------------------------------------------------------------------------------------------------
# §23. Correctness audit
# -------------------------------------------------------------------------------------------------

def audit(rows: list[dict], decision_date: date) -> dict:
    """Every §19/§32 defect class, each as a LIST of offenders rather than a count, so a non-zero
    result names the issuer and the method instead of asserting a number."""
    value_on_refusal, negative_multiple, unit_mismatch = [], [], []
    stale_used, future_used, nondeterministic, period_too_short = [], [], [], []
    ev_identity = []

    for row in rows:
        t = row["ticker"]
        for method, r in row["multiples"].items():
            tag = f"{t}/{method}"
            if r["status"] != MultipleStatus.OK.value:
                if r["multiple"] is not None:
                    value_on_refusal.append(tag)
                continue
            if r["multiple"] is None or r["multiple"] <= 0:
                negative_multiple.append(f"{tag}={r['multiple']}")
            if r["numerator_unit"] != r["denominator_unit"]:
                unit_mismatch.append(f"{tag}: {r['numerator_unit']} / {r['denominator_unit']}")
            # Deterministic arithmetic: the stored numerator over the stored denominator must be
            # the stored multiple to the bit. Division is exact given the same two operands, so any
            # difference at all means the published number did not come from the published inputs.
            if r["numerator_value"] / r["denominator_value"] != r["multiple"]:
                nondeterministic.append(tag)
            end = r["denominator_period_end"]
            if end is not None:
                end_d = date.fromisoformat(end)
                if end_d > decision_date:
                    future_used.append(f"{tag}: denominator ends {end} after {decision_date}")
                bound = (MAX_INSTANT_STALENESS_DAYS
                         if MULTIPLE_SPECS[method].denominator_kind.value == "INSTANT"
                         else MAX_TTM_PERIOD_AGE_DAYS)
                if (decision_date - end_d).days > bound:
                    stale_used.append(f"{tag}: denominator ends {end}, "
                                      f"{(decision_date - end_d).days} days > {bound}")
            start = r["denominator_period_start"]
            if start is not None and end is not None:
                span = (date.fromisoformat(end) - date.fromisoformat(start)).days
                if not 330 <= span <= 400:
                    period_too_short.append(f"{tag}: denominator spans {span} days")

        inp = row["inputs"]
        shares = inp["pit_shares"]
        if shares.get("shares_date") and date.fromisoformat(shares["shares_date"]) > decision_date:
            future_used.append(f"{t}: PIT shares end {shares['shares_date']}")
        if date.fromisoformat(inp["price"]["period"]) > decision_date:
            future_used.append(f"{t}: price date {inp['price']['period']}")
        for name in ("cash", "book_equity"):
            rec = inp[name]
            if rec["status"] == "OK" and rec["freshness_days"] is not None \
                    and rec["freshness_days"] > MAX_INSTANT_STALENESS_DAYS:
                stale_used.append(f"{t}/{name}: {rec['freshness_days']} days")
        ev = inp["enterprise_value"]
        if ev["status"] == "OK" and ev["components_sum"] is not None \
                and abs(ev["value"] - ev["components_sum"]) > 1e-6:
            ev_identity.append(f"{t}: EV {ev['value']} vs market cap + debt - cash "
                               f"{ev['components_sum']}")

    return {
        "value_reported_on_a_refusal": value_on_refusal,
        "negative_or_zero_multiple_published": negative_multiple,
        "numerator_denominator_unit_mismatch": unit_mismatch,
        "multiple_not_equal_to_its_own_inputs": nondeterministic,
        "denominator_period_not_a_full_year": period_too_short,
        "stale_input_used_in_a_published_multiple": stale_used,
        "future_input_used_in_a_published_multiple": future_used,
        "enterprise_value_identity_violated": ev_identity,
    }


def determinism_check(tickers, ciks, closes, decision_date, price_date, snapshot) -> dict:
    """§29's deterministic-arithmetic test at the pilot's own scale: the whole pilot, twice.

    Re-running every primitive and every division must reproduce the identical multiples. This is a
    stronger claim than the per-row recomputation in `audit`, which checks a published number
    against its own published inputs; this checks that the inputs themselves do not move.
    """
    again = []
    for ticker in tickers:
        row = snapshot_issuer(ticker, ciks.get(ticker), closes, decision_date, price_date, snapshot)
        if row is not None:
            again.append({"ticker": row["ticker"],
                          "multiples": {m: row["multiples"][m]["multiple"] for m in METHOD_ORDER},
                          "statuses": {m: row["multiples"][m]["status"] for m in METHOD_ORDER}})
    return {"rows": again}


# -------------------------------------------------------------------------------------------------
# Cross-sectional report (§21)
# -------------------------------------------------------------------------------------------------

def cross_section(rows: list[dict]) -> dict:
    n = len(rows)
    per_method = {}
    for method in METHOD_ORDER:
        buckets = Counter(rows_row["multiples"][method]["bucket"] for rows_row in rows)
        statuses = Counter(rows_row["multiples"][method]["status"] for rows_row in rows)
        values = sorted(r["multiples"][method]["multiple"] for r in rows
                        if r["multiples"][method]["status"] == MultipleStatus.OK.value)
        per_method[method] = {
            "ok": buckets.get(MultipleBucket.OK.value, 0),
            "not_applicable": buckets.get(MultipleBucket.NOT_APPLICABLE.value, 0),
            "missing": buckets.get(MultipleBucket.MISSING.value, 0),
            "of": n,
            "statuses": dict(sorted(statuses.items())),
            "values": values,
            "median": None if not values else _median(values),
        }
    counts = sorted(r["usable_method_count"] for r in rows)
    readiness = Counter(r["readiness"] for r in rows)
    return {
        "issuers": n,
        "per_method": per_method,
        "usable_method_count": {"median": _median(counts), "min": min(counts), "max": max(counts),
                                "distribution": dict(sorted(Counter(counts).items()))},
        "readiness": dict(sorted(readiness.items())),
        "d0_completeness": dict(sorted(Counter(r["d0_completeness"] for r in rows).items())),
        "methods_at_or_above_coverage_floor": sorted(
            m for m in METHOD_ORDER if per_method[m]["ok"] >= METHOD_COVERAGE_FLOOR),
    }


def _median(values: list[float]) -> float:
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def pe_lower_bound_cost(rows: list[dict]) -> list[str]:
    """§13/`PE_IS_PRICE_OVER_EPS`: issuers where P/E is blocked ONLY by the shares leg of the market
    cap gate, which price-over-EPS does not use. A measurement of this step's own conservatism, and
    not a repair: nothing is recomputed for these issuers."""
    out = []
    for row in rows:
        pe = row["multiples"]["P/E"]
        eps = row["inputs"]["ttm_eps_diluted"]
        if (pe["status"] == MultipleStatus.MARKET_CAP_UNAVAILABLE.value
                and eps["status"] == "OK"
                and row["inputs"]["share_class"]["valuation_allowed"]
                and row["inputs"]["price"]["status"] == "OK"):
            out.append(row["ticker"])
    return out


# -------------------------------------------------------------------------------------------------

def run() -> dict:
    tickers = tuple(t for t, _ in D5_D1_SAMPLE)
    ciks = {t: c for t, c in D5_D1_SAMPLE}

    cutoffs = []
    for ticker in tickers:
        package = D2_1_PACKAGES / f"{ticker}.json"
        if package.exists():
            raw = json.loads(package.read_text())["evidence_bundle"]["data_cutoff"]
            cutoffs.append(datetime.fromisoformat(raw).date())
    assert cutoffs, "no D2.1 package found for the frozen twelve"
    closes, session = last_session_close(set(tickers), max(cutoffs))
    assert session is not None
    snapshot = load_reference_snapshot(session)

    rows, skipped = [], []
    for ticker in tickers:
        row = snapshot_issuer(ticker, ciks.get(ticker), closes, session, session, snapshot)
        (rows if row is not None else skipped).append(row if row is not None else ticker)

    xs = cross_section(rows)
    defects = audit(rows, session)
    # Keyed by ticker rather than zipped by position: a drift check that silently compared the wrong
    # pair of rows would be worse than no drift check, and the ticker sets are asserted equal so a
    # row appearing or vanishing between runs is itself drift.
    repeat = {again["ticker"]: again for again in determinism_check(
        tickers, ciks, closes, session, session, snapshot)["rows"]}
    assert set(repeat) == {r["ticker"] for r in rows}, "the two runs resolved different issuers"
    drift = [r["ticker"] for r in rows
             if {m: r["multiples"][m]["multiple"] for m in METHOD_ORDER}
             != repeat[r["ticker"]]["multiples"]
             or {m: r["multiples"][m]["status"] for m in METHOD_ORDER}
             != repeat[r["ticker"]]["statuses"]]

    ready = (xs["readiness"].get("READY", 0)
             + xs["readiness"].get("READY_WITH_LIMITATIONS", 0))
    silent = (len(defects["value_reported_on_a_refusal"])
              + len(defects["negative_or_zero_multiple_published"])
              + len(defects["numerator_denominator_unit_mismatch"])
              + len(defects["multiple_not_equal_to_its_own_inputs"])
              + len(defects["denominator_period_not_a_full_year"])
              + len(defects["enterprise_value_identity_violated"])
              + len(drift))
    gate = {
        "valuation_ready_issuers": {"value": ready, "min": SUCCESS_GATE[
            "valuation_ready_issuers_min"], "of": len(rows),
            "pass": ready >= SUCCESS_GATE["valuation_ready_issuers_min"]},
        "methods_with_coverage_at_least_4": {
            "value": len(xs["methods_at_or_above_coverage_floor"]),
            "min": SUCCESS_GATE["methods_with_coverage_at_least_4_min"],
            "methods": xs["methods_at_or_above_coverage_floor"],
            "pass": len(xs["methods_at_or_above_coverage_floor"])
            >= SUCCESS_GATE["methods_with_coverage_at_least_4_min"]},
        "silent_wrong_values": {"value": silent, "max": SUCCESS_GATE["silent_wrong_values_max"],
                                "pass": silent <= SUCCESS_GATE["silent_wrong_values_max"]},
        "stale_inputs_used": {
            "value": len(defects["stale_input_used_in_a_published_multiple"]),
            "max": SUCCESS_GATE["stale_inputs_used_max"],
            "pass": not defects["stale_input_used_in_a_published_multiple"]},
        "future_inputs_used": {
            "value": len(defects["future_input_used_in_a_published_multiple"]),
            "max": SUCCESS_GATE["future_inputs_used_max"],
            "pass": not defects["future_input_used_in_a_published_multiple"]},
    }
    correctness_pass = all(gate[k]["pass"] for k in
                           ("silent_wrong_values", "stale_inputs_used", "future_inputs_used"))
    coverage_pass = all(gate[k]["pass"] for k in
                        ("valuation_ready_issuers", "methods_with_coverage_at_least_4"))

    return {
        "schema": SCHEMA,
        "corpus": "D5_D1_FROZEN_TWELVE",
        "sample": list(tickers),
        "sample_checksum": D5_D1_CHECKSUM,
        "sample_checksum_recomputed": d5_d1_checksum(D5_D1_SAMPLE),
        "sample_checksum_matches": d5_d1_checksum(D5_D1_SAMPLE) == D5_D1_CHECKSUM,
        "issuers": len(rows),
        "skipped": skipped,
        "decision_date": session.isoformat(),
        "reference_snapshot": {
            "as_of": None if snapshot is None else snapshot.as_of.isoformat(),
            "age_days": None if snapshot is None else (session - snapshot.as_of).days,
            "max_age_days": REFERENCE_TOPOLOGY_MAX_AGE_DAYS,
        },
        "bounds": {"instant_staleness_days": MAX_INSTANT_STALENESS_DAYS,
                   "ttm_period_age_days": MAX_TTM_PERIOD_AGE_DAYS,
                   "min_methods_for_ready": MIN_METHODS_FOR_COMPLETE,
                   "method_coverage_floor": METHOD_COVERAGE_FLOOR},
        "success_gate_frozen": dict(SUCCESS_GATE),
        "d0_v_gates": [{"gate": g.gate, "name": g.name, "threshold": g.threshold, "core": g.core}
                       for g in V_GATES],
        "cross_section": xs,
        "defects": defects,
        "determinism_drift": drift,
        "pe_blocked_only_by_shares": pe_lower_bound_cost(rows),
        "gate": gate,
        "correctness_pass": correctness_pass,
        "coverage_pass": coverage_pass,
        "rows": rows,
    }


def _fmt(x: float | None, suffix: str = "x") -> str:
    return "-" if x is None else f"{x:,.2f}{suffix}"


def main(argv: list[str]) -> None:
    report = run()
    if "--table" in argv:
        head = ["Ticker", "MktCap($m)", "EV($m)", *METHOD_ORDER, "N", "Readiness"]
        print(" | ".join(head))
        for row in report["rows"]:
            mc = row["inputs"]["market_cap"]["value"]
            ev = row["inputs"]["enterprise_value"]["value"]
            cells = [row["ticker"],
                     "-" if mc is None else f"{mc / 1e6:,.0f}",
                     "-" if ev is None else f"{ev / 1e6:,.0f}"]
            for m in METHOD_ORDER:
                r = row["multiples"][m]
                cells.append(_fmt(r["multiple"]) if r["status"] == "OK" else r["status"])
            cells += [str(row["usable_method_count"]), row["readiness"]]
            print(" | ".join(cells))
        return
    if "--summary" in argv:
        print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=2))
        return
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main(sys.argv[1:])
