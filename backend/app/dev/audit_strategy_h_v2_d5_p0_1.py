"""H-V2-D5-P0.1 audit: TTM coverage over the stored issuers. Offline, 0 model calls, $0.

It reads local SEC companyfacts and the stored D2.1 packages only. No price, no return, no multiple,
no valuation, no decision - the whole output is "which TTM fields construct, by which method, over
which period, and why not when not".

Usage:
    python -m app.dev.audit_strategy_h_v2_d5_p0_1              # the ten D4-contacted issuers
    python -m app.dev.audit_strategy_h_v2_d5_p0_1 --d5-d1      # the frozen D5-D1 twelve, read-only
"""

from __future__ import annotations

import gzip
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from app.backtest.strategy_h0.facts import DurationFamily, extract_companyfacts
from app.backtest.strategy_h0.pilot import acceptance_index
from app.backtest.strategy_h_v2.valuation.d5_d0_contract import D5_D1_SAMPLE
from app.backtest.strategy_h_v2.valuation.fundamental_fields import VALUATION_FIELD_SPECS
from app.backtest.strategy_h_v2.valuation.ttm import (
    TTM_FIELDS,
    TtmStatus,
    construct_ttm,
    construct_ttm_bundle,
    ebitda_feasible,
)

SCHEMA = "H_V2_D5_P0_1_TTM_COVERAGE_AUDIT_V1"

D2_1_PACKAGES = Path("data/runtime/strategy_h_v2/d2_1/D2_1-20260928T072430Z/packages")
FACTS_ROOTS = (Path("data/runtime/strategy_h/h0/raw"),
               Path("data/runtime/strategy_h/h_pv2c/sec_raw"),
               Path("data/runtime/strategy_h_v2/d1_1/sec_raw"))
SUBMISSION_ROOTS = (Path("data/runtime/strategy_h/h_pv2c/sec_raw/submissions"),
                    Path("data/runtime/strategy_c/e0/raw/submissions"),
                    Path("data/runtime/strategy_h/h0_5/sec_raw/submissions"),
                    Path("data/runtime/strategy_h_v2/d1_1/sec_raw/submissions"))

#: The ten issuers D4's live runs actually contacted, and the corpus P0 measured.
D4_ISSUERS: tuple[str, ...] = ("AEYE", "COLL", "FG", "VRRM", "IDCC", "DORM", "FRPT", "TG", "CRK",
                               "SPSC")

REPORTED_FIELDS: tuple[str, ...] = TTM_FIELDS + ("free_cash_flow",)


def _gz(path: Path) -> dict:
    return json.loads(gzip.decompress(path.read_bytes()))


def load_issuer(ticker: str, cik: str | None = None):
    """Stored companyfacts plus the D2.1 data cutoff. Returns None when the local store lacks it."""
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
    facts = extract_companyfacts(_gz(facts_root / "companyfacts" / f"CIK{cik}.json.gz"),
                                 acceptance_index(submissions), specs=VALUATION_FIELD_SPECS)
    return facts, cutoff


def audit_issuer(ticker: str, cik: str | None = None) -> dict | None:
    loaded = load_issuer(ticker, cik)
    if loaded is None:
        return None
    facts, cutoff = loaded
    bundle = construct_ttm_bundle(facts, cutoff, specs=VALUATION_FIELD_SPECS)
    row = {
        "ticker": ticker,
        "decision_time": cutoff.isoformat(),
        "fields": {name: bundle[name].to_dict() for name in REPORTED_FIELDS},
        "ebitda_candidate_feasible": ebitda_feasible(bundle["operating_income"],
                                                    bundle["depreciation_amortization"]),
    }
    # Cross-check: where both paths construct the same field, do they agree? A disagreement is a
    # finding about the data, not an error to hide, so it is measured rather than asserted away.
    agreement = {}
    for name in TTM_FIELDS:
        best = bundle[name]
        if not best.ok or best.period_end is None:
            continue
        pinned = construct_ttm(facts, name, cutoff, period_end=best.period_end,
                               specs=VALUATION_FIELD_SPECS)
        agreement[name] = {
            "method": best.method.value if best.method else None,
            "pinned_same_value": pinned.ok and pinned.value == best.value,
        }
    row["pinned_replay"] = agreement
    return row


def _both_paths_agree(ticker: str) -> dict | None:
    """For an issuer where both constructions reach one period end, compare them directly."""
    loaded = load_issuer(ticker)
    if loaded is None:
        return None
    facts, cutoff = loaded
    from app.backtest.strategy_h_v2.valuation.ttm import (
        _four_discrete_quarters, _ytd_difference,
    )
    out = {}
    for name in TTM_FIELDS:
        rows = [f for f in facts if f.field == name]
        chosen = construct_ttm(facts, name, cutoff, specs=VALUATION_FIELD_SPECS)
        if not chosen.ok or chosen.period_end is None:
            continue
        quarters = _four_discrete_quarters(rows, name, cutoff, chosen.period_end,
                                          VALUATION_FIELD_SPECS)
        ytd = None
        for family in (DurationFamily.YTD_Q3, DurationFamily.YTD_Q2, DurationFamily.YTD_Q1):
            candidate = _ytd_difference(rows, name, cutoff, chosen.period_end, family,
                                       VALUATION_FIELD_SPECS)
            if candidate.status is TtmStatus.OK:
                ytd = candidate
                break
        if quarters.status is TtmStatus.OK and ytd is not None:
            out[name] = {"four_quarters": quarters.value, "ytd_difference": ytd.value,
                         "period_end": chosen.period_end.isoformat()}
    return out


def audit(tickers: tuple[str, ...], ciks: dict[str, str] | None = None) -> dict:
    rows, skipped = [], []
    for ticker in tickers:
        row = audit_issuer(ticker, (ciks or {}).get(ticker))
        (rows if row is not None else skipped).append(row if row is not None else ticker)

    coverage: dict[str, dict] = {}
    for name in REPORTED_FIELDS:
        statuses = Counter(row["fields"][name]["status"] for row in rows)
        methods = Counter(row["fields"][name]["construction_method"] for row in rows
                          if row["fields"][name]["status"] == TtmStatus.OK.value)
        coverage[name] = {
            "ok": statuses.get(TtmStatus.OK.value, 0),
            "of": len(rows),
            "statuses": dict(sorted(statuses.items())),
            "methods": dict(sorted((k or "none", v) for k, v in methods.items())),
        }

    # Acceptance criteria, counted rather than claimed.
    future, wrong_period, deterministic_failures = [], [], []
    for row in rows:
        decision = datetime.fromisoformat(row["decision_time"])
        for name in REPORTED_FIELDS:
            record = row["fields"][name]
            if record["status"] != TtmStatus.OK.value:
                continue
            for component in record["components"]:
                if datetime.fromisoformat(component["acceptance_time"]) > decision:
                    future.append((row["ticker"], name, component["fact_id"]))
            span = record["duration_days"]
            if span is None or not 300 <= span <= 400:
                wrong_period.append((row["ticker"], name, span))
            families = {c["duration_family"] for c in record["components"]}
            if record["construction_method"] == "FOUR_DISCRETE_QUARTERS" and families != {"QUARTER"}:
                wrong_period.append((row["ticker"], name, sorted(families)))
        for name, replay in row["pinned_replay"].items():
            if not replay["pinned_same_value"]:
                deterministic_failures.append((row["ticker"], name))

    return {
        "schema": SCHEMA,
        "issuers": len(rows),
        "skipped": skipped,
        "coverage": coverage,
        "ebitda_candidate_feasible": sum(1 for r in rows if r["ebitda_candidate_feasible"]),
        "acceptance": {
            "future_components": future,
            "wrong_period_components": wrong_period,
            "nondeterministic_replays": deterministic_failures,
        },
        "rows": rows,
    }


def main() -> None:
    use_d5_d1 = "--d5-d1" in sys.argv[1:]
    if use_d5_d1:
        tickers = tuple(t for t, _ in D5_D1_SAMPLE)
        ciks = {t: c for t, c in D5_D1_SAMPLE}
    else:
        tickers, ciks = D4_ISSUERS, {}
    report = audit(tickers, ciks)
    print(json.dumps({
        "schema": report["schema"],
        "corpus": "D5_D1_FROZEN_TWELVE" if use_d5_d1 else "D4_TEN",
        "issuers": report["issuers"],
        "skipped": report["skipped"],
        "coverage": {k: f"{v['ok']}/{v['of']}  {v['statuses']}  {v['methods']}"
                     for k, v in report["coverage"].items()},
        "ebitda_candidate_feasible": report["ebitda_candidate_feasible"],
        "acceptance": report["acceptance"],
    }, indent=2, default=str))
    if not use_d5_d1:
        print(json.dumps({"both_paths_agree": {t: _both_paths_agree(t) or {}
                                               for t in ("COLL",)}}, indent=2, default=str))


if __name__ == "__main__":
    main()
