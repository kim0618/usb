"""Execute the Strategy H-V2 D1/D1.1 pipeline against real local repository data.

PIPELINE VALIDATION, not alpha evaluation: no forward return is read or computed anywhere in this
script. See `docs/backtest/strategy_h_v2/H_V2_D1_UNIVERSE_CHANGE_ENGINE_V1.md` and
`docs/backtest/strategy_h_v2/H_V2_D1_1_UNIVERSE_DATA_COVERAGE_V1.md`.

Universe -> E1 Eligibility -> E2 Change Detection -> E3 Research Priority -> Candidate Evidence
Stub, for the current dated CS reference snapshot. Local companyfacts/submissions coverage is
whatever `data/runtime/strategy_h/h0`, `.../h_pv2c`, and `.../strategy_h_v2/d1_1` (D1.1's
acquisition output) currently hold; a CIK with nothing in any of them correctly reports
`DATA_NOT_READY`, never a fabricated fundamentals value and never `INELIGIBLE`. No new SEC network
calls are made by this script - acquisition is a separate step
(`app.dev.acquire_strategy_h_v2_fundamentals`).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from app.backtest.strategy_eqm_v0.xbrl_store import read_facts
from app.backtest.strategy_h0.facts import FIELD_SPECS, extract_companyfacts
from app.backtest.strategy_h0.pilot import acceptance_index, read_gzip_json
from app.backtest.strategy_h_v2.eligibility import EligibilityStatus, MarketSnapshot
from app.backtest.strategy_h_v2.pipeline import assemble_candidate
from app.backtest.strategy_h_v2.research_priority import PriorityState
from app.backtest.strategy_h_v2.universe import SecurityTypeStatus, build_universe

REFERENCE = Path("data/runtime/research_universe_u1/reference/tickers/CS_2024-10-25.json.gz")
SNAPSHOT_DATE = "2024-10-25"
DAILY = Path("data/runtime/strategy_b_e0/mirror/market_data/raw/massive/grouped_daily")
SPLITS = Path("data/runtime/strategy_b_e0/mirror/market_data/raw/massive/splits/splits_2024-09-16_2026-09-16.json.gz")
FACTS_ROOTS = [
    Path("data/runtime/strategy_h/h0/raw"),
    Path("data/runtime/strategy_h/h_pv2c/sec_raw"),
    Path("data/runtime/strategy_h_v2/d1_1/sec_raw"),  # D1.1 acquisition output
]
SUBMISSION_ROOTS = [
    Path("data/runtime/strategy_h/h_pv2c/sec_raw/submissions"),
    Path("data/runtime/strategy_c/e0/raw/submissions"),
    Path("data/runtime/strategy_h/h0_5/sec_raw/submissions"),
    Path("data/runtime/strategy_h_v2/d1_1/sec_raw/submissions"),  # D1.1 acquisition output
]
OUTPUT_ROOT = Path("data/runtime/strategy_h_v2/d1")


def load_market(daily_root: Path) -> tuple[dict[str, dict[date, tuple[float, float]]], dict[str, int]]:
    per_ticker: dict[str, dict[date, tuple[float, float]]] = defaultdict(dict)
    quality = {"files": 0, "rows": 0, "invalid": 0}
    for path in sorted(daily_root.rglob("*.json.gz")):
        doc = json.loads(gzip.decompress(path.read_bytes()))
        if "body" not in doc or "session" not in doc:
            continue
        day = date.fromisoformat(doc["session"])
        quality["files"] += 1
        for row in doc["body"].get("results", []):
            quality["rows"] += 1
            ticker = row.get("T")
            values = [row.get(k) for k in ("o", "h", "l", "c", "v")]
            if not ticker or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
                quality["invalid"] += 1
                continue
            per_ticker[ticker][day] = (float(row["c"]), float(row["v"]))
    return per_ticker, quality


def market_snapshot(series: dict[date, tuple[float, float]]) -> MarketSnapshot | None:
    if not series:
        return None
    days = sorted(series)
    latest_close = series[days[-1]][0]
    trailing_days = days[-21:]
    dollar_volumes = [series[d][0] * series[d][1] for d in trailing_days]
    avg_dollar_volume = sum(dollar_volumes) / len(dollar_volumes) if dollar_volumes else None
    return MarketSnapshot(
        latest_close=latest_close, trailing_sessions=len(days),
        trailing_avg_dollar_volume=avg_dollar_volume,
    )


def price_context_of(series: dict[date, tuple[float, float]], spy_series: dict[date, tuple[float, float]]) -> dict[str, Any]:
    if not series:
        return {"status": "UNKNOWN"}
    days = sorted(series)
    latest_day, latest_close = days[-1], series[days[-1]][0]

    def _return(session_lookback: int) -> float | None:
        if len(days) <= session_lookback:
            return None
        past_close = series[days[-1 - session_lookback]][0]
        return None if past_close <= 0 else latest_close / past_close - 1

    def _spy_return(session_lookback: int) -> float | None:
        spy_days = sorted(spy_series)
        if latest_day not in spy_series or len(spy_days) <= session_lookback:
            return None
        idx = spy_days.index(latest_day)
        if idx < session_lookback:
            return None
        past = spy_series[spy_days[idx - session_lookback]][0]
        return None if past <= 0 else spy_series[latest_day][0] / past - 1

    r1m, r3m = _return(21), _return(63)
    spy1m, spy3m = _spy_return(21), _spy_return(63)
    highs = [series[d][0] for d in days[-252:]]
    return {
        "latest_close": latest_close,
        "return_1m": r1m,
        "return_3m": r3m,
        "relative_strength_1m": None if r1m is None or spy1m is None else r1m - spy1m,
        "relative_strength_3m": None if r3m is None or spy3m is None else r3m - spy3m,
        "trailing_52w_high": max(highs) if highs else None,
        "trailing_52w_low": min(highs) if highs else None,
    }


def find_facts_root(cik: str) -> Path | None:
    for root in FACTS_ROOTS:
        if (root / "companyfacts" / f"CIK{cik}.json.gz").exists():
            return root
    return None


def find_submission_doc(cik: str) -> dict[str, Any] | None:
    for root in SUBMISSION_ROOTS:
        path = root / f"CIK{cik}" / f"CIK{cik}.json.gz"
        if path.exists():
            return read_gzip_json(path)
    return None


def days_since(latest: date | None, as_of: date) -> int | None:
    return None if latest is None else (as_of - latest).days


def main() -> None:
    run_id = datetime.now(timezone.utc).strftime("D1-%Y%m%dT%H%M%SZ")
    generated_at = datetime.now(timezone.utc)
    data_cutoff = generated_at

    reference_rows = [
        row for page in read_gzip_json(REFERENCE)["pages"] for row in page.get("results", [])
    ]
    universe = build_universe(reference_rows, snapshot_date=SNAPSHOT_DATE, known_at=generated_at)

    print(f"[1/5] universe rows: {len(universe)}")

    market_by_ticker, market_quality = load_market(DAILY)
    print(f"[2/5] daily store loaded: {market_quality}, distinct tickers: {len(market_by_ticker)}")
    spy_series = market_by_ticker.get("SPY", {})

    split_doc = read_gzip_json(SPLITS)
    split_dates_by_ticker: dict[str, list[date]] = defaultdict(list)
    for row in split_doc.get("results", []):
        split_dates_by_ticker[row["ticker"]].append(date.fromisoformat(row["execution_date"]))

    facts_cache: dict[str, list] = {}
    facts_fetched_cache: dict[str, bool] = {}
    submission_cache: dict[str, dict[str, datetime] | None] = {}

    def facts_for(cik: str | None) -> tuple[list, bool]:
        """Returns (facts, facts_fetched). `facts_fetched` is True whenever a local companyfacts
        document exists for this CIK, independently of whether any canonical field resolved from
        it - the D1.1 distinction `eligibility.py` needs (§3 of the D1.1 brief)."""
        if cik is None:
            return [], False
        if cik not in facts_cache:
            root = find_facts_root(cik)
            if root is None:
                facts_cache[cik] = []
                facts_fetched_cache[cik] = False
            else:
                doc = read_facts(root, cik)
                sub = find_submission_doc(cik)
                facts_fetched_cache[cik] = doc is not None
                facts_cache[cik] = (
                    extract_companyfacts(doc, acceptance_index(sub)) if doc and sub else []
                )
        return facts_cache[cik], facts_fetched_cache[cik]

    def latest_filing_date(cik: str | None) -> date | None:
        if cik is None:
            return None
        if cik not in submission_cache:
            sub = find_submission_doc(cik)
            submission_cache[cik] = acceptance_index(sub) if sub else None
        index = submission_cache[cik]
        if not index:
            return None
        return max(ts.date() for ts in index.values())

    eligibility_counts: Counter = Counter()
    ineligible_reasons: Counter = Counter()
    unknown_status_reasons: Counter = Counter()
    data_not_ready_reasons: Counter = Counter()
    eligible_soft_reasons: Counter = Counter()
    priority_counts: Counter = Counter()
    change_state_counts: dict[str, Counter] = defaultdict(Counter)
    facts_coverage_hist: Counter = Counter()
    candidates_written = 0
    evidence_out_dir = OUTPUT_ROOT / run_id / "candidates"
    evidence_out_dir.mkdir(parents=True, exist_ok=True)

    for i, row in enumerate(universe):
        facts, facts_fetched = facts_for(row.cik)
        facts_coverage_hist[facts_fetched] += 1
        series = market_by_ticker.get(row.ticker, {})
        market = market_snapshot(series)
        result = assemble_candidate(
            row, run_id=run_id, generated_at=generated_at, data_cutoff=data_cutoff,
            facts=facts, facts_fetched=facts_fetched, split_dates=split_dates_by_ticker.get(row.ticker, ()),
            market=market, days_since_latest_filing=days_since(latest_filing_date(row.cik), data_cutoff.date()),
            price_context=price_context_of(series, spy_series),
        )
        eligibility_counts[result.eligibility.status.value] += 1
        target = {
            EligibilityStatus.INELIGIBLE: ineligible_reasons,
            EligibilityStatus.UNKNOWN: unknown_status_reasons,
            EligibilityStatus.DATA_NOT_READY: data_not_ready_reasons,
            EligibilityStatus.ELIGIBLE: eligible_soft_reasons,
        }[result.eligibility.status]
        for reason in result.eligibility.reasons:
            target[reason.value] += 1
        for ce in result.change_evidence:
            change_state_counts[ce.metric][ce.state.value] += 1
        if result.priority is not None:
            priority_counts[result.priority.value] += 1
            if result.priority in (PriorityState.P1_HIGH, PriorityState.P2_MEDIUM):
                candidates_written += 1
                out_path = evidence_out_dir / f"{row.ticker}.json"
                out_path.write_text(result.evidence.model_dump_json(indent=2))

    manifest = {
        "schema": "H_V2_D1_MANIFEST_V1",
        "run_id": run_id,
        "generated_at": generated_at.isoformat(),
        "reference_snapshot": SNAPSHOT_DATE,
        "universe_count": len(universe),
        "market_quality": market_quality,
        "distinct_priced_tickers": len(market_by_ticker),
        "universe_rows_with_fetched_companyfacts": facts_coverage_hist.get(True, 0),
        "unique_ciks_with_fetched_companyfacts": sum(1 for v in facts_fetched_cache.values() if v),
        "unique_ciks_with_resolved_facts": sum(1 for v in facts_cache.values() if v),
        "eligibility_counts": dict(eligibility_counts),
        "ineligible_reason_counts": dict(ineligible_reasons),
        "unknown_status_reason_counts": dict(unknown_status_reasons),
        "data_not_ready_reason_counts": dict(data_not_ready_reasons),
        "eligible_soft_reason_counts": dict(eligible_soft_reasons),
        "priority_counts": dict(priority_counts),
        "change_state_counts": {k: dict(v) for k, v in change_state_counts.items()},
        "candidates_written_p1_p2": candidates_written,
        "constraints": {
            "forward_return_used": False, "gpt_used": False, "valuation_computed": False,
            "expectation_gap_evaluated": False, "broker": False, "paper_trading": False,
        },
    }
    (OUTPUT_ROOT / run_id).mkdir(parents=True, exist_ok=True)
    manifest_json = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    (OUTPUT_ROOT / run_id / "manifest.json").write_text(manifest_json)
    (OUTPUT_ROOT / run_id / "manifest.sha256").write_text(hashlib.sha256(manifest_json.encode()).hexdigest() + "\n")
    print(f"[3/5] eligibility: {dict(eligibility_counts)}")
    print(f"[4/5] priority: {dict(priority_counts)}")
    print(f"[5/5] wrote {candidates_written} candidate evidence stubs -> {evidence_out_dir}")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
