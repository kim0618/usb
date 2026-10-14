"""New-candidate intake on production data: A's reference and prices, SEC on demand, the frozen E1->E3.

D1 was a one-off dev run on the 2024-10-25 reference snapshot, the HIST-V1 mirror (ends 2026-09-16)
and companyfacts fetched on 2026-09-28. None of those is a production source, so this step reads
the stores Strategy A runs on (``h_auto.sources``) and fetches SEC fundamentals per issuer on
demand (``h_auto.facts_cache``), then calls ``pipeline.assemble_candidate`` - the same E1, E2 and E3
code, unchanged - per universe row. Nothing here tunes a threshold; a candidate set that differs
from the 2026-09-28 research run differs because the data is current.

Two modes, two units:

* ``GATE`` (``update``, usb-h-forward, 01:10 ET): refresh H's own CS reference snapshot for the
  publishable session (A's fetcher, H's directory) and record the freshness gate. No SEC call.
* ``SCAN`` (``auto-research``, usb-h-research, 01:40 ET): if the gate is READY, identify the
  universe, apply E1's market tier from A's grouped store (one streaming pass), fetch SEC for at
  most ``fetch_cap_per_run`` market-passing CIKs whose facts are not fresh, run E1->E3 on the rows
  whose facts were (re)fetched, fold the results into ``auto/intake_index.json`` and write
  ``new_candidates.json`` for the research queue in the contract's order.

A row that cannot be scanned is not skipped silently: it is counted under a readiness state
(``REFERENCE_NOT_READY``, ``PRICE_NOT_READY``, ``MARKET_INELIGIBLE``, ``FUNDAMENTALS_FETCH_REQUIRED``,
``SEC_NOT_READY``) in ``intake_status.json`` and in the index. No model is called here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from zoneinfo import ZoneInfo

from app.market.calendar import MarketCalendar
from app.strategies.h_auto import contract as AC
from app.strategies.h_auto import facts_cache as FC
from app.strategies.h_auto import inputs as IN
from app.strategies.h_auto import sources as SRC
from app.strategies.h_auto import store as AS
from app.strategies.h_forward import sec_refresh as SR
from app.strategies.h_forward import store as ST

READY, DATA_NOT_READY = "READY", "DATA_NOT_READY"
GATE, SCAN = "GATE", "SCAN"
# Per-row readiness states (structural, recorded; never a silent skip).
REFERENCE_NOT_READY = "REFERENCE_NOT_READY"
IDENTITY_INELIGIBLE = "IDENTITY_INELIGIBLE"            # not common stock / unsupported exchange (E1 hard)
PRICE_NOT_READY = "PRICE_NOT_READY"
MARKET_INELIGIBLE = "MARKET_INELIGIBLE"                # E1 hard: close < $1 or ADV < $1M
FUNDAMENTALS_FETCH_REQUIRED = "FUNDAMENTALS_FETCH_REQUIRED"
SEC_NOT_READY = "SEC_NOT_READY"
COHORT = "COHORT_REFRESHED_NOT_ADMITTED"               # already in H's forward ledger
SCANNED = "READY"
ET = ZoneInfo("America/New_York")
NEW_CANDIDATES = "new_candidates.json"
INTAKE_STATUS = "intake_status.json"
INTAKE_INDEX = "intake_index.json"
INDEX_SCHEMA = "h_intake_index_v1"
#: Issuers whose SEC documents are in memory at once during a scan.
SCAN_BATCH = 20
#: New MethodJudgement verdicts per run (each builds one 2-year D5 panel, about 10-20 s).
MJ_SCREEN_LIMIT = 40


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def default_massive_client():
    from app.core.config import get_settings
    from app.integrations.massive.client import build_massive_client
    return build_massive_client(get_settings())


# -------------------------------------------------------------------------------------------------
# Freshness gate
# -------------------------------------------------------------------------------------------------

def freshness(now: datetime | None = None, *, snapshots: Sequence[SRC.ReferenceSnapshot] | None = None,
              grouped: Path | None = None, calendar: MarketCalendar | None = None) -> dict[str, Any]:
    """Whether the new-candidate scan may run. Pure read; every check reports what it saw.

    Three inputs and three refusals: the reference snapshot in force for the publishable session must
    be no older than the contract's ``reference_snapshot_max_age_days`` and carry active CS rows with
    a CIK; A's grouped store must hold the publishable session; and it must reach back at least
    ``MIN_HISTORY_SESSIONS``. Fundamentals are not a gate: they are fetched per issuer in the scan.
    """
    from app.backtest.collector.range import previous_trading_day
    now = now or _now()
    publishable = previous_trading_day(calendar or MarketCalendar(), now)
    reasons: list[str] = []
    checks: dict[str, Any] = {"publishable_session": publishable.isoformat()}
    snaps = SRC.reference_snapshots() if snapshots is None else list(snapshots)
    snap = SRC.reference_in_force(publishable, snaps)
    if snap is None:
        reasons.append("REFERENCE_SNAPSHOT_MISSING")
    else:
        age = (publishable - snap.as_of).days
        rows = [r for r in SRC.active_cs_rows(SRC.reference_rows(snap.path)) if str(r.get("cik") or "").strip()]
        checks["reference"] = {"as_of": snap.as_of.isoformat(), "owner": snap.owner, "age_days": age,
                               "path": str(snap.path), "active_cs_with_cik": len(rows)}
        if age > AC.reference_max_age_days():
            reasons.append(f"REFERENCE_SNAPSHOT_STALE:{snap.as_of.isoformat()}:{age}d>{AC.reference_max_age_days()}d")
        if not rows:
            reasons.append("REFERENCE_HAS_NO_ACTIVE_CS_WITH_CIK")
    have = SRC.grouped_sessions(grouped)
    checks["grouped"] = {"store": str(grouped) if grouped is not None else f"{SRC.A_GROUPED_DIR} + {IN.history_dir()}",
                         "a_store_latest": next((d.isoformat() for d in reversed(SRC.grouped_sessions(SRC.A_GROUPED_DIR))), None)
                         if grouped is None else None,
                         "latest": have[-1].isoformat() if have else None,
                         "first": have[0].isoformat() if have else None, "files": len(have)}
    if not have or have[-1] < publishable:
        reasons.append(f"LATEST_PRICE_SESSION_STALE:{have[-1].isoformat() if have else None}<{publishable.isoformat()}")
    if len(have) < SRC.MIN_HISTORY_SESSIONS:
        reasons.append(f"PRICE_HISTORY_INSUFFICIENT:{len(have)}<{SRC.MIN_HISTORY_SESSIONS}")
    return {"state": READY if not reasons else DATA_NOT_READY, "reasons": reasons, "checks": checks,
            "checked_at": _iso(now)}


# -------------------------------------------------------------------------------------------------
# The frozen E1 -> E2 -> E3 per row
# -------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ScanInputs:
    """Everything ``assemble_candidate`` needs per universe row. Built from production stores in
    ``production_inputs``, from fixtures in tests."""

    rows: Sequence[Any]                                   # UniverseRow
    facts_for: Callable[[str | None], tuple[list, bool]]
    series_for: Callable[[str], Mapping[date, tuple[float, float]]]
    spy: Mapping[date, tuple[float, float]]
    latest_filing: Callable[[str | None], date | None]
    split_dates: Callable[[str], Sequence[date]]


def scan(inputs: ScanInputs, *, now: datetime, run_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """E1 -> E2 -> E3 over ``inputs.rows``, the frozen pipeline function per row. No model call.

    Returns (candidates with E3 P1/P2, one index record per row scanned).
    """
    from app.backtest.strategy_h_v2.pipeline import assemble_candidate
    from app.backtest.strategy_h_v2.research_priority import PriorityState
    from app.dev.run_strategy_h_v2_d1 import market_snapshot, price_context_of

    candidates, records = [], []
    ledgered = set(ST.latest_per_ticker())
    for row in inputs.rows:
        if row.ticker in ledgered:
            continue
        facts, fetched = inputs.facts_for(row.cik)
        series = dict(inputs.series_for(row.ticker))
        latest = inputs.latest_filing(row.cik)
        days_since = None if latest is None else (now.date() - latest).days
        result = assemble_candidate(
            row, run_id=run_id, generated_at=now, data_cutoff=now, facts=facts, facts_fetched=fetched,
            split_dates=inputs.split_dates(row.ticker), market=market_snapshot(series),
            days_since_latest_filing=days_since, price_context=price_context_of(series, dict(inputs.spy)))
        priority = result.priority.value if result.priority is not None else None
        records.append({"ticker": row.ticker, "cik": row.cik, "security_id": row.security_id,
                        "exchange": row.exchange, "snapshot_date": row.snapshot_date,
                        "scanned_at": _iso(now), "run_id": run_id,
                        "eligibility": result.eligibility.status.value,
                        "eligibility_reasons": [r.value for r in result.eligibility.reasons],
                        "e3_priority": priority, "days_since_latest_filing": days_since,
                        "latest_filing": None if latest is None else latest.isoformat(),
                        "trailing_sessions": len(series)})
        if result.priority not in (PriorityState.P1_HIGH, PriorityState.P2_MEDIUM):
            continue
        candidates.append({"ticker": row.ticker, "cik": row.cik, "security_id": row.security_id,
                           "exchange": row.exchange, "snapshot_date": row.snapshot_date,
                           "e3_priority": priority, "days_since_latest_filing": days_since,
                           "source_event": [{"kind": "E3_PRIORITY", "state": priority, "run_id": run_id}]})
    return order(candidates), records


def order(candidates: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The contract's intake order: E3 class, evidence recency, ticker - never a price or a return."""
    return sorted((dict(c) for c in candidates),
                  key=lambda c: (0 if c["e3_priority"] == "P1_HIGH" else 1,
                                 c["days_since_latest_filing"] if c["days_since_latest_filing"] is not None else 10**6,
                                 c["ticker"]))


def write_candidates(candidates: Sequence[Mapping[str, Any]], *, open_new_items: int, now: datetime,
                     run_id: str, source: Mapping[str, Any] | None = None) -> dict[str, Any]:
    cap = int(AC.contract()["new_candidate_intake"]["max_open_new_candidate_items"])
    room = max(0, cap - open_new_items)
    chosen = [dict(c) | {"intake_order": n, "readiness": SCANNED} for n, c in enumerate(candidates[:room], 1)]
    body = {"generated_at": _iso(now), "run_id": run_id, "schema": "h_new_candidates_v1",
            "candidates": chosen, "eligible_total": len(candidates),
            "held_back_by_open_item_cap": max(0, len(candidates) - len(chosen)), "model_calls": 0,
            "source": dict(source or {})}
    SR.atomic_write_json(ST.path(NEW_CANDIDATES), body)
    return body


# -------------------------------------------------------------------------------------------------
# Index of what has been scanned
# -------------------------------------------------------------------------------------------------

def read_index() -> dict[str, dict[str, Any]]:
    body = AS.read_json(INTAKE_INDEX) or {}
    return dict(body.get("rows") or {})


def write_index(rows: Mapping[str, Mapping[str, Any]], *, now: datetime) -> str:
    return AS.write_json(INTAKE_INDEX, {"schema": INDEX_SCHEMA, "updated_at": _iso(now),
                                        "rows": {t: dict(r) for t, r in sorted(rows.items())}})


# -------------------------------------------------------------------------------------------------
# Production inputs
# -------------------------------------------------------------------------------------------------

def _identity_state(row: Any) -> str:
    from app.backtest.strategy_h_v2.universe import SecurityTypeStatus
    if row.security_type_status is not SecurityTypeStatus.COMMON_STOCK or not row.exchange_supported:
        return IDENTITY_INELIGIBLE
    if not row.cik or not row.security_id:
        return REFERENCE_NOT_READY
    return SCANNED


def _market_state(gate: SRC.MarketGate | None) -> str:
    from app.backtest.strategy_h_v2 import eligibility as E
    if gate is None or gate.sessions < E.MIN_TRAILING_SESSIONS:
        return PRICE_NOT_READY
    adv = gate.trailing_avg_dollar_volume
    if gate.latest_close < E.MIN_CLOSE_PRICE or adv is None or adv < E.MIN_TRAILING_DOLLAR_VOLUME:
        return MARKET_INELIGIBLE
    return SCANNED


def screen_method_judgement(ordered: Sequence[Mapping[str, Any]], *, index: dict[str, dict[str, Any]],
                            now: datetime, publishable: date, room: int, exclude: Iterable[str],
                            sec_client_factory: Callable[[], Any], limit: int = MJ_SCREEN_LIMIT,
                            log: Callable[[str], None] = lambda _m: None) -> dict[str, Any]:
    """Walk the intake order and keep the first ``room`` candidates whose code-owned MethodJudgement
    is AUTO_READY on the production D5 panel; record every verdict in the index.

    Measured 2026-10-14: 19 of 30 current candidates are METHOD_JUDGEMENT_NOT_READY (no cover-page
    share count, no section-17 primary, financial SIC). Written to the queue unscreened they would
    hold the 10 open new-candidate slots for good, and no candidate after them would ever be read.
    A verdict is reused while the issuer's facts day is unchanged, so a NOT_READY issuer is re-judged
    only when its SEC facts are refetched (the executor still re-derives before any model call).
    At most ``limit`` new verdicts per run; no model call; nothing here reads a return.
    """
    from app.strategies.h_auto import method_judgement as MJ
    from app.backtest.strategy_c_e0 import sec_store
    from app.dev.run_strategy_h_v2_d5_d2 import build_panel
    excluded = set(exclude)
    pending = [c for c in ordered if c["ticker"] not in excluded]
    need = [c for c in pending if (index.get(c["ticker"]) or {}).get("mj_facts_day") != (index.get(c["ticker"]) or {}).get("facts_day")]
    need = need[:limit]
    series = SRC.series_for((c["ticker"] for c in need), until=publishable) if need else {}
    need_set = {c["ticker"] for c in need}
    chosen, judged, counts = [], 0, {MJ.AUTO_READY: 0, MJ.NOT_READY: 0}
    client = None
    screen_dir = AS.root() / "intake_screen"
    try:
        for cand in pending:
            if len(chosen) >= room:
                break
            ticker, record = cand["ticker"], index.get(cand["ticker"]) or {}
            if record.get("mj_facts_day") != record.get("facts_day"):
                if ticker not in need_set:
                    continue                                   # beyond this run's verdict budget
                if client is None:
                    client = sec_client_factory()
                day = record["facts_day"]
                cik = sec_store.cik10(cand["cik"])
                root = IN.sec_root(day)
                try:
                    IN.fetch_sec(cik, day, client=client)      # refetches a compacted body, same root
                except (IN.InputsNotReady, IN.SecUnavailable) as exc:
                    record.update({"method_judgement": "SEC_NOT_READY", "mj_reasons": [str(exc)]})
                    continue
                doc = sec_store.read_gz_json(sec_store.submissions_path(root, cik, f"CIK{cik}.json"))
                screen_dir.mkdir(parents=True, exist_ok=True)
                (screen_dir / f"{ticker}.json").write_text(json.dumps(
                    {"evidence_bundle": {"identity": {"cik": cik}, "data_cutoff": _iso(now)}}), encoding="utf-8")
                panel = IN.Panel(bars={t: dict(series.get(t, {})) for t in (ticker, SRC.BENCHMARK)}, until=publishable)
                try:
                    with IN.d5_binding(packages_dir=screen_dir, panel=panel, facts_roots=[root],
                                       submission_roots=[root / "submissions"]):
                        _obs, row, _cov = build_panel(ticker, panel.closes(ticker), publishable)
                    derived = (MJ.derive(ticker, row, sic=doc.get("sic"), sic_description=doc.get("sicDescription"))
                               if row is not None else MJ.Derived(ticker, MJ.NOT_READY, ("NO_DECISION_ROW",), None))
                    state, reasons = derived.state, [r.split(":")[0] for r in derived.reasons]
                except Exception as exc:                       # a data defect for one issuer, recorded
                    state, reasons = MJ.NOT_READY, [f"SCREEN_ERROR:{type(exc).__name__}"]
                record.update({"method_judgement": state, "mj_reasons": reasons, "mj_facts_day": record.get("facts_day"),
                               "mj_session": publishable.isoformat(), "sic": doc.get("sic")})
                index[ticker] = record
                judged += 1
            state = record.get("method_judgement")
            counts[state] = counts.get(state, 0) + 1
            if state == MJ.AUTO_READY:
                chosen.append(dict(cand) | {"method_judgement": state})
    finally:
        if client is not None and getattr(client, "close", None):
            client.close()
    log(f"intake: method judgement screen judged {judged}, chosen {len(chosen)}")
    return {"chosen": chosen, "judged_this_run": judged, "counts": counts, "room": room,
            "limit": limit, "excluded_already_queued": len(excluded)}


def production_inputs(rows: Sequence[Any], *, series: Mapping[str, Mapping[date, tuple[float, float]]],
                      sec_by_cik: Mapping[str, IN.SecInputs], splits: Mapping[str, Sequence[date]]) -> ScanInputs:
    from app.backtest.strategy_h0.pilot import acceptance_index
    filing_cache: dict[str, date | None] = {}

    def facts_for(cik: str | None) -> tuple[list, bool]:
        sec = sec_by_cik.get(cik or "")
        return ([], False) if sec is None else (list(sec.facts), sec.facts_fetched)

    def latest_filing(cik: str | None) -> date | None:
        sec = sec_by_cik.get(cik or "")
        if sec is None:
            return None
        if cik not in filing_cache:
            index = acceptance_index(sec.submissions_doc)
            filing_cache[cik] = max((t.date() for t in index.values()), default=None)
        return filing_cache[cik]

    return ScanInputs(rows=rows, facts_for=facts_for, series_for=lambda t: series.get(t, {}),
                      spy=series.get(SRC.BENCHMARK, {}), latest_filing=latest_filing,
                      split_dates=lambda t: splits.get(t, ()))


def scan_production(now: datetime, *, gate: Mapping[str, Any], sec_client_factory: Callable[[], Any],
                    fetch_cap: int, open_new_items: int, run_id: str, queued_tickers: Iterable[str] = (),
                    calendar: MarketCalendar | None = None, log: Callable[[str], None] = lambda _m: None) -> dict[str, Any]:
    """The SCAN mode body. Raises nothing it can report; the caller records exceptions."""
    from app.backtest.collector.range import previous_trading_day
    from app.strategies.h_auto import budget as BU
    publishable = previous_trading_day(calendar or MarketCalendar(), now)
    today = now.astimezone(ET).date()
    snap = SRC.reference_in_force(publishable)
    assert snap is not None, "gate READY implies a snapshot"
    rows = SRC.universe_rows(snap, known_at=now)
    ledgered = set(ST.latest_per_ticker())
    states: dict[str, str] = {}
    identified = []
    for row in rows:
        state = COHORT if row.ticker in ledgered else _identity_state(row)
        states[row.ticker] = state
        if state == SCANNED:
            identified.append(row)
    log(f"intake: universe {len(rows)} identified {len(identified)}")
    gates = SRC.market_gate((r.ticker for r in identified), until=publishable)
    market_pass = []
    for row in identified:
        state = _market_state(gates.get(row.ticker))
        states[row.ticker] = state
        if state == SCANNED:
            market_pass.append(row)
    log(f"intake: market pass {len(market_pass)}")

    entries = FC.index()
    needed = sorted({row.cik for row in market_pass})
    planned = FC.plan(needed, today, cap=fetch_cap, entries=entries)
    sec_report = FC.refresh(planned, day=BU.et_day(now), client_factory=sec_client_factory, log=log)
    entries = FC.index()
    not_found = {n["cik"] for n in sec_report["not_found"]}
    index = read_index()
    to_scan, facts_state = [], {}
    for row in market_pass:
        entry = entries.get(row.cik)
        state = FC.state_of(entry, today)
        if row.cik in not_found:
            states[row.ticker] = SEC_NOT_READY
            facts_state[row.ticker] = FC.NOT_FOUND
            continue
        facts_state[row.ticker] = state
        if state != FC.FRESH:
            states[row.ticker] = FUNDAMENTALS_FETCH_REQUIRED
            continue
        previous = index.get(row.ticker) or {}
        if previous.get("facts_day") != entry.day or previous.get("snapshot_date") != row.snapshot_date:
            to_scan.append(row)
    log(f"intake: scanning {len(to_scan)} rows with fresh facts")
    # One price pass for every row, then SEC loaded SCAN_BATCH issuers at a time and released: holding
    # 600 parsed companyfacts at once was measured on 2026-10-14 to exceed the host's memory (the
    # local run was OOM-killed right after the SEC slice), and the research unit is capped at 650M.
    series = SRC.series_for((r.ticker for r in to_scan), until=publishable) if to_scan else {SRC.BENCHMARK: {}}
    splits = SRC.splits_index()
    records = []
    for start in range(0, len(to_scan), SCAN_BATCH):
        batch = to_scan[start:start + SCAN_BATCH]
        sec_by_cik: dict[str, IN.SecInputs] = {}
        for row in batch:
            if row.cik not in sec_by_cik:
                sec = FC.load(row.cik, entries[row.cik])
                sec.facts_document = None                  # parsed facts are kept, the raw document is not
                sec.submissions_rows = []
                sec_by_cik[row.cik] = sec
        _, batch_records = scan(production_inputs(batch, series=series, sec_by_cik=sec_by_cik, splits=splits),
                                now=now, run_id=run_id)
        records.extend(batch_records)
        del sec_by_cik
        if (start // SCAN_BATCH) % 10 == 9:
            log(f"intake: scanned {start + len(batch)}/{len(to_scan)}")
    for record in records:
        record["facts_day"] = entries[record["cik"]].day
        record["scan_session"] = publishable.isoformat()
        index[record["ticker"]] = record
    universe_tickers = {r.ticker for r in rows}
    for ticker in list(index):
        if ticker not in universe_tickers:
            del index[ticker]
    for ticker, record in index.items():
        record["readiness"] = states.get(ticker, REFERENCE_NOT_READY)
        record["facts_state"] = facts_state.get(ticker)
    write_index(index, now=now)
    pruned = FC.prune()

    fresh_cutoff = (today.toordinal() - FC.FACTS_MAX_AGE_DAYS)
    candidates = [r for r in index.values()
                  if r.get("readiness") == SCANNED and r.get("e3_priority") in ("P1_HIGH", "P2_MEDIUM")
                  and r["ticker"] not in ledgered and r.get("facts_state") == FC.FRESH
                  and date.fromisoformat(r["scanned_at"][:10]).toordinal() >= fresh_cutoff]
    ordered = order([{k: r.get(k) for k in ("ticker", "cik", "security_id", "exchange",
                                            "snapshot_date", "e3_priority", "days_since_latest_filing")}
                     | {"source_event": [{"kind": "E3_PRIORITY", "state": r["e3_priority"], "run_id": r.get("run_id")}]}
                     for r in candidates])
    cap = int(AC.contract()["new_candidate_intake"]["max_open_new_candidate_items"])
    screened = screen_method_judgement(ordered, index=index, now=now, publishable=publishable,
                                       room=max(0, cap - open_new_items), exclude=queued_tickers,
                                       sec_client_factory=sec_client_factory, log=log)
    write_index(index, now=now)
    body = write_candidates(screened["chosen"], open_new_items=open_new_items, now=now, run_id=run_id,
                            source={"reference": {"as_of": snap.as_of.isoformat(), "owner": snap.owner},
                                    "prices": str(SRC.A_GROUPED_DIR), "scan_session": publishable.isoformat(),
                                    "contract": SRC.SOURCE_CONTRACT["fundamentals"],
                                    "method_judgement_screen": {k: v for k, v in screened.items() if k != "chosen"}})
    body["eligible_total"] = len(ordered)
    # Only the written candidates' documents are kept; everything else keeps its request ledger only
    # (measured 2026-10-14: keeping every P1/P2 body left 337 MB for 2,000 CIKs, and 67% of scanned
    # rows are P1/P2). The executor fetches its own copy of a candidate's documents anyway.
    compacted = FC.compact(c["cik"] for c in body["candidates"] if c.get("cik"))
    counts: dict[str, int] = {}
    for state in states.values():
        counts[state] = counts.get(state, 0) + 1
    scanned = [r for r in index.values() if r.get("readiness") == SCANNED]
    e1 = {}
    for r in scanned:
        key = str(r.get("eligibility"))
        e1[key] = e1.get(key, 0) + 1
    e3 = {}
    for r in scanned:
        key = str(r.get("e3_priority"))                # None (not ELIGIBLE) is a key too: "None"
        e3[key] = e3.get(key, 0) + 1
    return {"written": len(body["candidates"]), "eligible_total": body["eligible_total"],
            "method_judgement_screen": body["source"]["method_judgement_screen"],
            "held_back_by_open_item_cap": body["held_back_by_open_item_cap"],
            "universe": {"snapshot": snap.as_of.isoformat(), "owner": snap.owner, "rows": len(rows),
                         "identified": len(identified), "market_pass": len(market_pass)},
            "readiness_counts": counts, "sec": {k: (v if k != "fetched" else len(v)) for k, v in sec_report.items()},
            "sec_cache": FC.summary(today, entries), "sec_roots_pruned": pruned, "sec_compacted": compacted,
            "scanned_this_run": len(records), "index_rows": len(index), "index_scanned": len(scanned),
            "e1_counts": e1, "e3_counts": e3, "model_calls": 0}


# -------------------------------------------------------------------------------------------------
# The step
# -------------------------------------------------------------------------------------------------

def intake_step(*, now: datetime | None = None, open_new_items: int = 0, mode: str = GATE,
                sec_client_factory: Callable[[], Any] = IN.default_sec_client,
                massive_client_factory: Callable[[], Any] | None = None,
                log: Callable[[str], None] = lambda _m: None) -> dict[str, Any]:
    """Step 4 of the daily job. GATE in ``update``; SCAN in ``auto-research``. DATA_NOT_READY leaves
    the intake file untouched.

    The reference refresh (Massive) runs only in GATE mode and only when a client factory is given:
    ``update`` passes ``default_massive_client``; a hand run or a test that passes none just reads
    the snapshots that exist."""
    if mode not in (GATE, SCAN):
        raise ValueError(f"unknown intake mode {mode!r}")
    now = now or _now()
    status: dict[str, Any] = {"mode": mode, "ran_at": _iso(now)}
    if mode == GATE and massive_client_factory is not None:
        try:
            status["reference_refresh"] = SRC.refresh_reference(now, client_factory=massive_client_factory, log=log)
        except Exception as exc:                        # Massive refusal: the gate reports what exists
            status["reference_refresh"] = {"status": f"ERROR:{type(exc).__name__}: {exc}"}
    gate = freshness(now)
    status.update({"new_candidate_scan": gate["state"], **gate})
    if mode == SCAN and gate["state"] == READY:
        run_id = now.strftime("HINTAKE-%Y%m%dT%H%M%SZ")
        status["scan"] = scan_production(now, gate=gate, sec_client_factory=sec_client_factory,
                                         fetch_cap=AC.intake_fetch_cap(), open_new_items=open_new_items,
                                         run_id=run_id, queued_tickers=queued_new_candidates(), log=log)
        status["written"] = status["scan"]["written"]
        status["eligible_total"] = status["scan"]["eligible_total"]
    elif mode == GATE and gate["state"] == READY:
        status["new_candidate_scan"] = "GATE_READY_SCAN_IN_RESEARCH_UNIT"
    SR.atomic_write_json(ST.path(INTAKE_STATUS), status)
    return status


def queued_new_candidates() -> set[str]:
    """Tickers that already have a NEW_CANDIDATE queue item in any status: never written again."""
    from app.strategies.h_auto import queue as Q
    return {i["ticker"] for i in Q.fold().values() if i.get("reason") == "NEW_CANDIDATE"}


def open_new_candidate_items(items: Iterable[Mapping[str, Any]]) -> int:
    from app.strategies.h_auto import queue as Q
    return sum(1 for i in items if i.get("reason") == "NEW_CANDIDATE" and i.get("status") not in Q.TERMINAL)
