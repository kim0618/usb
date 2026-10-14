"""New-candidate intake: reference universe -> E1 -> E2 -> E3, with a freshness gate in front.

D1 was a one-off dev run on the 2024-10-25 reference snapshot, the HIST-V1 mirror (ends 2026-09-16)
and companyfacts fetched on 2026-09-28. Run on those today it would rank issuers on data that is
weeks or years old, so the scan refuses to run unless every input is current:

* a reference snapshot (``CS_<as_of>.json.gz``, the dirs D5's share-class code already reads) no
  older than the contract's ``reference_snapshot_max_age_days``, with active US common-stock rows
  that carry a CIK (ticker <-> CIK and listing status come from the same rows);
* a market-wide grouped-daily file for the publishable session (Strategy A's daily store);
* market-wide price history reaching back at least 252 sessions (the HIST-V1 mirror, or a store
  that replaces it);
* a market-wide companyfacts store fetched within 7 days.

If any is missing the result is ``NEW_CANDIDATE_SCAN = DATA_NOT_READY`` with the reasons, the intake
file is left as it is, and nothing else in the daily job is affected. No model is called here; the
scan only writes candidates for the research queue, at most ``max_open_new_candidate_items`` open at
a time, in the contract's order (E3 class, evidence recency, ticker - never a price or a return).
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from zoneinfo import ZoneInfo

from app.market.calendar import MarketCalendar
from app.strategies.h_auto import contract as AC
from app.strategies.h_auto import inputs as IN
from app.strategies.h_forward import sec_refresh as SR
from app.strategies.h_forward import store as ST

REFERENCE_DIRS = (IN.REPO_ROOT / "data/runtime/strategy_c/raw/tickers",
                  IN.REPO_ROOT / "data/runtime/research_universe_v2/reference/tickers",
                  IN.REPO_ROOT / "data/runtime/research_universe_u1/reference/tickers")
FACTS_STORE = IN.REPO_ROOT / "data/runtime/strategy_h_v2/d1_1/sec_raw"
FACTS_MAX_AGE_DAYS = 7
MIN_HISTORY_SESSIONS = 252
READY, DATA_NOT_READY = "READY", "DATA_NOT_READY"
ET = ZoneInfo("America/New_York")
NEW_CANDIDATES = "new_candidates.json"
INTAKE_STATUS = "intake_status.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def newest_reference(dirs: Sequence[Path] = REFERENCE_DIRS) -> tuple[date, Path] | None:
    best: tuple[date, Path] | None = None
    for directory in dirs:
        if not directory.is_dir():
            continue
        for path in directory.glob("CS_*.json.gz"):
            try:
                as_of = date.fromisoformat(path.name[3:13])
            except ValueError:
                continue
            if best is None or as_of > best[0]:
                best = (as_of, path)
    return best


def reference_rows(path: Path) -> list[dict[str, Any]]:
    doc = json.loads(gzip.decompress(path.read_bytes()))
    if isinstance(doc.get("results"), list):
        return list(doc["results"])
    return [row for page in doc.get("pages") or [] for row in page.get("results") or []]


def _facts_as_of(store: Path) -> date | None:
    folder = store / "companyfacts"
    if not folder.is_dir():
        return None
    newest = None
    for ledger in folder.glob("*.request.json"):
        try:
            fetched = str(json.loads(ledger.read_text(encoding="utf-8")).get("fetched_at"))[:10]
            day = date.fromisoformat(fetched)
        except (OSError, ValueError):
            continue
        newest = day if newest is None or day > newest else newest
    return newest


def freshness(now: datetime | None = None, *, reference_dirs: Sequence[Path] = REFERENCE_DIRS,
              grouped: Path = IN.GROUPED_DIR, mirror: Path = IN.MIRROR_DIR,
              facts_store: Path = FACTS_STORE) -> dict[str, Any]:
    """Whether the new-candidate scan may run. Pure read; every check reports what it saw."""
    from app.backtest.collector.range import previous_trading_day
    now = now or _now()
    today = now.astimezone(ET).date()
    reasons: list[str] = []
    checks: dict[str, Any] = {}
    ref = newest_reference(reference_dirs)
    if ref is None:
        reasons.append("REFERENCE_SNAPSHOT_MISSING")
    else:
        age = (today - ref[0]).days
        rows = [r for r in reference_rows(ref[1])
                if r.get("type") == "CS" and r.get("active") is True and (r.get("cik") or "").strip()]
        checks["reference"] = {"as_of": ref[0].isoformat(), "age_days": age, "path": str(ref[1]),
                               "active_cs_with_cik": len(rows)}
        if age > AC.reference_max_age_days():
            reasons.append(f"REFERENCE_SNAPSHOT_STALE:{ref[0].isoformat()}:{age}d>{AC.reference_max_age_days()}d")
        if not rows:
            reasons.append("REFERENCE_HAS_NO_ACTIVE_CS_WITH_CIK")
    publishable = previous_trading_day(MarketCalendar(), now)
    have = sorted(d for d, _ in IN._grouped_files(grouped, date.max))
    checks["grouped"] = {"publishable_session": publishable.isoformat(),
                         "latest": have[-1].isoformat() if have else None, "files": len(have)}
    if not have or have[-1] < publishable:
        reasons.append(f"LATEST_PRICE_SESSION_STALE:{have[-1].isoformat() if have else None}<{publishable.isoformat()}")
    history = len(have) + (len(list(mirror.rglob("*.json.gz"))) if mirror.is_dir() else 0)
    checks["price_history_files"] = history
    if not mirror.is_dir() and len(have) < MIN_HISTORY_SESSIONS:
        reasons.append(f"PRICE_HISTORY_INSUFFICIENT:{len(have)}<{MIN_HISTORY_SESSIONS}")
    facts_day = _facts_as_of(facts_store)
    checks["companyfacts_store"] = {"path": str(facts_store), "fetched_as_of": facts_day.isoformat() if facts_day else None}
    if facts_day is None:
        reasons.append("COMPANYFACTS_STORE_MISSING")
    elif (today - facts_day).days > FACTS_MAX_AGE_DAYS:
        reasons.append(f"COMPANYFACTS_STORE_STALE:{facts_day.isoformat()}")
    return {"state": READY if not reasons else DATA_NOT_READY, "reasons": reasons, "checks": checks,
            "checked_at": now.astimezone(timezone.utc).isoformat(timespec="seconds")}


@dataclass(frozen=True)
class ScanInputs:
    """Everything ``assemble_candidate`` needs per universe row. Built from stores in production,
    from fixtures in tests."""

    rows: Sequence[Any]                                   # UniverseRow
    facts_for: Callable[[str | None], tuple[list, bool]]
    series_for: Callable[[str], Mapping[date, tuple[float, float]]]
    spy: Mapping[date, tuple[float, float]]
    latest_filing: Callable[[str | None], date | None]
    split_dates: Callable[[str], Sequence[date]]


def scan(inputs: ScanInputs, *, now: datetime, run_id: str) -> list[dict[str, Any]]:
    """E1 -> E2 -> E3 over the universe, the frozen pipeline function per row. No model call."""
    from app.backtest.strategy_h_v2.pipeline import assemble_candidate
    from app.backtest.strategy_h_v2.research_priority import PriorityState
    from app.dev.run_strategy_h_v2_d1 import market_snapshot, price_context_of

    out = []
    ledgered = set(ST.latest_per_ticker())
    for row in inputs.rows:
        if row.ticker in ledgered:
            continue
        facts, fetched = inputs.facts_for(row.cik)
        series = dict(inputs.series_for(row.ticker))
        latest = inputs.latest_filing(row.cik)
        result = assemble_candidate(
            row, run_id=run_id, generated_at=now, data_cutoff=now, facts=facts, facts_fetched=fetched,
            split_dates=inputs.split_dates(row.ticker), market=market_snapshot(series),
            days_since_latest_filing=None if latest is None else (now.date() - latest).days,
            price_context=price_context_of(series, dict(inputs.spy)))
        if result.priority not in (PriorityState.P1_HIGH, PriorityState.P2_MEDIUM):
            continue
        out.append({"ticker": row.ticker, "cik": row.cik, "security_id": row.security_id,
                    "exchange": row.exchange, "snapshot_date": row.snapshot_date,
                    "e3_priority": result.priority.value,
                    "days_since_latest_filing": None if latest is None else (now.date() - latest).days,
                    "source_event": [{"kind": "E3_PRIORITY", "state": result.priority.value,
                                      "run_id": run_id}]})
    out.sort(key=lambda c: (0 if c["e3_priority"] == "P1_HIGH" else 1,
                            c["days_since_latest_filing"] if c["days_since_latest_filing"] is not None else 10**6,
                            c["ticker"]))
    return out


def write_candidates(candidates: Sequence[Mapping[str, Any]], *, open_new_items: int, now: datetime,
                     run_id: str) -> dict[str, Any]:
    cap = int(AC.contract()["new_candidate_intake"]["max_open_new_candidate_items"])
    room = max(0, cap - open_new_items)
    chosen = [dict(c) | {"intake_order": n} for n, c in enumerate(candidates[:room], 1)]
    body = {"generated_at": now.astimezone(timezone.utc).isoformat(timespec="seconds"), "run_id": run_id,
            "schema": "h_new_candidates_v1", "candidates": chosen, "eligible_total": len(candidates),
            "held_back_by_open_item_cap": max(0, len(candidates) - len(chosen)), "model_calls": 0}
    SR.atomic_write_json(ST.path(NEW_CANDIDATES), body)
    return body


def intake_step(*, now: datetime | None = None, open_new_items: int = 0,
                build_inputs: Callable[[datetime], ScanInputs] | None = None) -> dict[str, Any]:
    """Step 4 of the daily job. DATA_NOT_READY leaves the intake file untouched."""
    now = now or _now()
    gate = freshness(now)
    status = {"new_candidate_scan": gate["state"], **gate}
    if gate["state"] == READY and build_inputs is not None:
        run_id = now.strftime("HINTAKE-%Y%m%dT%H%M%SZ")
        body = write_candidates(scan(build_inputs(now), now=now, run_id=run_id),
                                open_new_items=open_new_items, now=now, run_id=run_id)
        status["written"] = len(body["candidates"])
        status["eligible_total"] = body["eligible_total"]
    elif gate["state"] == READY:
        status["new_candidate_scan"] = DATA_NOT_READY
        status["reasons"] = ["SCAN_INPUT_BUILDER_NOT_WIRED"]
    SR.atomic_write_json(ST.path(INTAKE_STATUS), status)
    return status


def open_new_candidate_items(items: Iterable[Mapping[str, Any]]) -> int:
    from app.strategies.h_auto import queue as Q
    return sum(1 for i in items if i.get("reason") == "NEW_CANDIDATE" and i.get("status") not in Q.TERMINAL)
