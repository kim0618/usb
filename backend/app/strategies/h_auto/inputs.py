"""Inputs for one re-evaluation at a new decision session, built with the frozen D1/D2/D2.1 code.

The research runners were written for one run on 2026-09-28: their package directory, fundamentals
roots and price panel are module constants, and the panel stops at 2026-09-16 because that is where
the frozen HIST-V1 mirror stops. Nothing here edits those runners. This module assembles the same
kinds of inputs at a later session from sources that exist in production, and ``d5_binding`` points
the D5 loaders at them for the duration of one valuation, then restores every name it touched.

Sources, all read-only except the executor's own work directory:

* **prices** - one closes-and-volume panel from, in order, the frozen mirror (or, on a host without
  it, the pinned cohort extraction ``auto/price_base``), Strategy A's grouped-daily store (same
  Massive grouped payload, written daily in production; the only history a candidate admitted after
  launch has) and H's own D7 session store. Two sources
  that both hold a session must agree on the close exactly, or the panel is refused
  (``PRICE_SOURCE_CONFLICT``). The decision session's close must exist.
* **SEC** - submissions (back to 2022) and companyfacts are fetched once per ET day into
  ``auto/sec/<day>/`` with the repository's existing SecClient (same User-Agent, 5 req/s).
* **D1 stub** - ``pipeline.assemble_candidate`` at the new cutoff (pure function, unchanged).
* **D2** - ``collector.assemble_evidence_bundle_v2`` on the fresh submissions (unchanged).
* **D2.1** - ``materialize.materialize_candidate`` into the immutable document cache
  ``auto/d2_1_cache`` (unchanged depth policy).

No model call happens in this module.
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

from app.market.calendar import MarketCalendar
from app.strategies.h_auto import store as AS
from app.strategies.h_forward import prices as PR

REPO_ROOT = Path(__file__).resolve().parents[4]
MIRROR_DIR = REPO_ROOT / "data/runtime/strategy_b_e0/mirror/market_data/raw/massive/grouped_daily"
GROUPED_DIR = REPO_ROOT / "data/runtime/strategy_c/raw/grouped"
SPLITS_DIRS = (REPO_ROOT / "data/runtime/strategy_c/raw/splits",
               REPO_ROOT / "data/runtime/strategy_b_e0/mirror/market_data/raw/massive/splits")
PRICE_BASE = "price_base/H_PRICE_BASE_V1.json.gz"
BENCHMARK = "SPY"
SUBMISSIONS_REQUIRED_FROM = date(2022, 1, 1)


class InputsNotReady(RuntimeError):
    """A deterministic data gap: no money is spent and the item waits (DATA_NOT_READY)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class SecUnavailable(RuntimeError):
    """A transient SEC failure: retried on the next daily run under the queue's attempt cap."""


# -------------------------------------------------------------------------------------------------
# Prices
# -------------------------------------------------------------------------------------------------

@dataclass
class Panel:
    bars: dict[str, dict[date, tuple[float, float | None]]]      # ticker -> session -> (close, volume)
    sources: dict[str, int] = field(default_factory=dict)
    until: date | None = None

    def closes(self, ticker: str) -> dict[date, float]:
        return {d: v[0] for d, v in sorted(self.bars.get(ticker, {}).items())}

    def close_panel(self) -> dict[str, dict[date, float]]:
        return {t: self.closes(t) for t in self.bars}

    def series(self, ticker: str) -> dict[date, tuple[float, float]]:
        return {d: (c, float(v or 0.0)) for d, (c, v) in sorted(self.bars.get(ticker, {}).items())}


def _merge(panel: Panel, ticker: str, day: date, close: float, volume: float | None, source: str) -> None:
    have = panel.bars.setdefault(ticker, {}).get(day)
    if have is not None and have[0] != close:
        raise InputsNotReady("PRICE_SOURCE_CONFLICT", f"{ticker} {day}: {have[0]} vs {close} ({source})")
    if have is None:
        panel.bars[ticker][day] = (close, volume)
        panel.sources[source] = panel.sources.get(source, 0) + 1


def _grouped_files(directory: Path, until: date) -> Iterator[tuple[date, Path]]:
    if not directory.is_dir():
        return
    for path in sorted(directory.rglob("*.json.gz")):
        try:
            day = date.fromisoformat(path.name[:10])
        except ValueError:
            continue
        if day <= until:
            yield day, path


def _read_grouped(panel: Panel, directory: Path, wanted: set[str], until: date, source: str) -> None:
    for day, path in _grouped_files(directory, until):
        doc = json.loads(gzip.decompress(path.read_bytes()))
        body = doc.get("body") or {}
        if doc.get("session") and doc["session"] != day.isoformat():
            continue
        for row in body.get("results") or []:
            ticker = row.get("T")
            if ticker in wanted and isinstance(row.get("c"), (int, float)):
                _merge(panel, ticker, day, float(row["c"]),
                       float(row["v"]) if isinstance(row.get("v"), (int, float)) else None, source)


# -------------------------------------------------------------------------------------------------
# H's pinned market-wide history (sessions before the production grouped store begins)
# -------------------------------------------------------------------------------------------------

PRICE_HISTORY = "price_history"
HISTORY_FORMAT = "h-price-history-v1"
HISTORY_MANIFEST = "MANIFEST.json"


def history_dir() -> Path:
    """``<H store>/auto/price_history``: one slim file per session (ticker, close, volume) extracted
    from Strategy A's grouped-daily files where they go back further than the production store.

    trader-j's ``strategy_c/raw/grouped`` starts 2026-08-11 (44 sessions on 2026-10-14) while E1 and
    D5 need 252. The local tree holds the same Massive grouped payloads back to 2024-09-16. Copying
    them into A's directory would write A's store; this is H's own, pinned by a manifest of sha256s,
    and A's live files win for any session both hold.
    """
    return AS.root() / PRICE_HISTORY


def history_files(until: date = date.max, *, directory: Path | None = None) -> Iterator[tuple[date, Path]]:
    folder = directory if directory is not None else history_dir()
    if not folder.is_dir():
        return
    for path in sorted(folder.glob("*.json.gz")):
        try:
            day = date.fromisoformat(path.name[:10])
        except ValueError:
            continue
        if day <= until:
            yield day, path


def read_session_rows(path: Path) -> Iterator[tuple[str, float, float | None]]:
    """(ticker, close, volume) from either an A grouped file or an H history file."""
    doc = json.loads(gzip.decompress(path.read_bytes()))
    if doc.get("format") == HISTORY_FORMAT:
        for ticker, close, volume in doc.get("rows") or []:
            yield ticker, float(close), (float(volume) if volume is not None else None)
        return
    for row in (doc.get("body") or {}).get("results") or []:
        ticker, close = row.get("T"), row.get("c")
        if isinstance(ticker, str) and isinstance(close, (int, float)):
            volume = row.get("v")
            yield ticker, float(close), float(volume) if isinstance(volume, (int, float)) else None


def build_price_history(source: Path, *, before: date) -> dict[str, Any]:
    """Extract every A grouped session strictly before ``before`` into H's history (run where they exist)."""
    target = history_dir()
    target.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, str] = {}
    for day, path in _grouped_files(source, before - timedelta(days=1)):
        doc = json.loads(gzip.decompress(path.read_bytes()))
        if doc.get("session") != day.isoformat():
            continue
        rows = [[r["T"], r["c"], r.get("v") if isinstance(r.get("v"), (int, float)) else None]
                for r in (doc.get("body") or {}).get("results") or []
                if isinstance(r.get("T"), str) and isinstance(r.get("c"), (int, float))]
        body = {"format": HISTORY_FORMAT, "session": day.isoformat(),
                "source": "strategy_c/raw/grouped (Massive grouped daily, unadjusted)",
                "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "rows": rows}
        raw = gzip.compress(json.dumps(body, separators=(",", ":")).encode(), mtime=0)
        (target / f"{day.isoformat()}.json.gz").write_bytes(raw)
        manifest[day.isoformat()] = hashlib.sha256(raw).hexdigest()
    (target / HISTORY_MANIFEST).write_text(json.dumps({"format": HISTORY_FORMAT, "before": before.isoformat(),
                                                       "files": manifest}, indent=0, sort_keys=True), encoding="utf-8")
    days = sorted(manifest)
    return {"dir": str(target), "sessions": len(days), "first": days[0] if days else None,
            "last": days[-1] if days else None,
            "bytes": sum(p.stat().st_size for p in target.glob("*.json.gz")),
            "manifest_sha256": hashlib.sha256((target / HISTORY_MANIFEST).read_bytes()).hexdigest()}


def verify_price_history() -> dict[str, Any]:
    """Every history file matches the manifest; extra or missing files are reported."""
    folder = history_dir()
    if not (folder / HISTORY_MANIFEST).is_file():
        return {"state": "ABSENT", "problems": []}
    manifest = json.loads((folder / HISTORY_MANIFEST).read_text(encoding="utf-8"))["files"]
    problems = []
    present = {p.name[:10]: p for p in folder.glob("*.json.gz")}
    for day, sha in manifest.items():
        path = present.get(day)
        if path is None:
            problems.append(f"MISSING:{day}")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            problems.append(f"CHECKSUM:{day}")
    problems += [f"UNLISTED:{d}" for d in sorted(set(present) - set(manifest))]
    return {"state": "OK" if not problems else "CORRUPT", "sessions": len(manifest), "problems": problems}


def price_base_path() -> Path:
    return AS.root() / PRICE_BASE


def build_price_base(tickers: Sequence[str], *, mirror: Path = MIRROR_DIR) -> dict[str, Any]:
    """Extract the cohort's mirror history into one pinned file, for a host without the mirror."""
    wanted = set(tickers) | {BENCHMARK}
    panel = Panel(bars={})
    _read_grouped(panel, mirror, wanted, date.max, "mirror")
    body = {"schema": "h_price_base_v1", "source": "USB-HIST-V1 b_e0 grouped_daily mirror (read-only)",
            "tickers": sorted(wanted),
            "bars": {t: {d.isoformat(): list(v) for d, v in sorted(panel.bars.get(t, {}).items())} for t in sorted(wanted)}}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    target = price_base_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(gzip.compress(raw, mtime=0))
    sha = hashlib.sha256(raw).hexdigest()
    target.with_suffix(".sha256").write_text(sha + "\n", encoding="utf-8")
    return {"path": str(target), "sha256": sha, "tickers": len(wanted),
            "last_session": max((d for t in body["bars"].values() for d in t), default=None)}


def _read_price_base(panel: Panel, wanted: set[str], until: date) -> bool:
    target = price_base_path()
    if not target.is_file():
        return False
    raw = gzip.decompress(target.read_bytes())
    pinned = target.with_suffix(".sha256")
    if not pinned.is_file() or hashlib.sha256(raw).hexdigest() != pinned.read_text().strip():
        raise InputsNotReady("PRICE_BASE_CHECKSUM_MISMATCH", str(target))
    body = json.loads(raw)
    # The base pins the launch cohort's mirror history. A ticker it lacks (a candidate admitted after
    # launch) is priced from A's grouped store below; ``check_panel`` still demands the full lookback.
    missing = wanted - set(body.get("tickers") or [])
    if missing:
        panel.sources["price_base_lacks"] = len(missing)
    for ticker in wanted - missing:
        for day_text, (close, volume) in (body["bars"].get(ticker) or {}).items():
            day = date.fromisoformat(day_text)
            if day <= until:
                _merge(panel, ticker, day, float(close), volume, "price_base")
    return True


def load_panel(tickers: Sequence[str], until: date, *, mirror: Path = MIRROR_DIR,
               grouped: Path = GROUPED_DIR, use_d7: bool = True, history: bool = True) -> Panel:
    """Closes and volumes for ``tickers`` + SPY through ``until``, from every source that holds them."""
    wanted = set(tickers) | {BENCHMARK}
    panel = Panel(bars={}, until=until)
    if mirror.is_dir():
        _read_grouped(panel, mirror, wanted, until, "mirror")
    elif not _read_price_base(panel, wanted, until):
        panel.sources["no_mirror_no_price_base"] = 1       # production: A's grouped store is the history
    _read_grouped(panel, grouped, wanted, until, "a_grouped")
    if history:
        for day, path in history_files(until):
            for ticker, close, volume in read_session_rows(path):
                if ticker in wanted:
                    _merge(panel, ticker, day, close, volume, "h_history")
    if use_d7:
        for session in PR.stored_sessions():
            day = date.fromisoformat(session)
            if day > until:
                continue
            for ticker, bar in PR.bars_of(session).items():
                if ticker in wanted:
                    _merge(panel, ticker, day, bar.close, bar.volume, "h_d7")
    return panel


def check_panel(panel: Panel, ticker: str, until: date, *, calendar: MarketCalendar | None = None,
                min_sessions: int = 252) -> dict[str, Any]:
    """The decision session must be priced for the issuer and SPY, and SPY must have no gap."""
    cal = calendar or MarketCalendar()
    if until not in panel.bars.get(ticker, {}):
        raise InputsNotReady("DECISION_SESSION_UNPRICED", f"{ticker} {until}")
    if until not in panel.bars.get(BENCHMARK, {}):
        raise InputsNotReady("DECISION_SESSION_UNPRICED", f"{BENCHMARK} {until}")
    spy = sorted(panel.bars[BENCHMARK])
    first = spy[0]
    expected = [d for d in _sessions(cal, first, until)]
    missing = [d.isoformat() for d in expected if d not in panel.bars[BENCHMARK]]
    if missing:
        raise InputsNotReady("PRICE_SESSION_GAP", f"{BENCHMARK} missing {missing[:5]}")
    n = len(panel.bars.get(ticker, {}))
    if n < min_sessions:
        raise InputsNotReady("PRICE_HISTORY_INSUFFICIENT", f"{ticker} {n} < {min_sessions}")
    issuer_missing = [d.isoformat() for d in expected if d >= min(panel.bars[ticker]) and d not in panel.bars[ticker]]
    return {"sessions": n, "first": min(panel.bars[ticker]).isoformat(), "until": until.isoformat(),
            "issuer_missing_sessions": issuer_missing, "sources": dict(panel.sources)}


def _sessions(cal: MarketCalendar, first: date, last: date) -> list[date]:
    out, day = [], first
    if not cal.is_trading_day(day):
        day = cal.next_trading_day(day)
    while day <= last:
        out.append(day)
        day = cal.next_trading_day(day)
    return out


def decision_session(now: datetime, *, calendar: MarketCalendar | None = None) -> date:
    """The newest XNYS session that has published at ``now`` and is in H's price store.

    The same publishing rule the price collector uses (``previous_trading_day`` of now in ET); a
    re-evaluation never values on a session whose prices it cannot have seen.
    """
    from app.backtest.collector.range import previous_trading_day
    cal = calendar or MarketCalendar()
    publishable = previous_trading_day(cal, now)
    stored = [date.fromisoformat(s) for s in PR.stored_sessions() if date.fromisoformat(s) <= publishable]
    if not stored:
        raise InputsNotReady("NO_STORED_SESSION")
    return stored[-1]


# -------------------------------------------------------------------------------------------------
# SEC
# -------------------------------------------------------------------------------------------------

def sec_root(day: str) -> Path:
    return AS.root() / "sec" / day


def default_sec_client():
    from app.backtest.strategy_c_e0 import sec_store
    from app.dev.acquire_strategy_h_v2_fundamentals import USER_AGENT
    return sec_store.SecClient(USER_AGENT)


@dataclass
class SecInputs:
    cik: str
    root: Path
    submissions_rows: list[dict[str, Any]]
    submissions_doc: dict[str, Any]
    submissions_checksum: str | None
    submissions_fetched_at: datetime
    facts_fetched: bool
    facts: list
    facts_document: dict[str, Any] | None


def fetch_sec_files(cik: str, day: str, *, client) -> str:
    """Download (or find cached) submissions and companyfacts for one CIK under ``auto/sec/<day>``,
    without reading them back. The bytes and ledgers written are exactly ``fetch_sec``'s (same two
    store calls); only the parse of the documents, which the intake's refresh step never used, is
    skipped. Returns the companyfacts status. Raises SecUnavailable / InputsNotReady like ``fetch_sec``.
    """
    from app.backtest.strategy_c_e0 import sec_store
    from app.backtest.strategy_eqm_v0 import xbrl_store

    cik = sec_store.cik10(cik)
    root = sec_root(day)
    try:
        result = sec_store.fetch_cik(client, root, cik, required_from=SUBMISSIONS_REQUIRED_FROM)
        facts_status = xbrl_store.fetch_cik(client, root, cik)
    except sec_store.SecFetchError as exc:
        raise SecUnavailable(f"SEC_FETCH_ERROR:{exc.code}") from exc
    if result.status == "NOT_FOUND":
        raise InputsNotReady("SEC_SUBMISSIONS_NOT_FOUND", cik)
    return facts_status


def fetch_sec(cik: str, day: str, *, client, facts_reader=None) -> SecInputs:
    """Submissions back to 2022 and companyfacts for one CIK, cached under ``auto/sec/<day>``.

    ``facts_reader(root, cik)`` replaces ``xbrl_store.read_facts`` (``facts_cache.load(lean=True)``)."""
    from app.backtest.strategy_c_e0 import sec_store
    from app.backtest.strategy_eqm_v0 import xbrl_store
    from app.backtest.strategy_h0.facts import extract_companyfacts
    from app.backtest.strategy_h0.pilot import acceptance_index

    cik = sec_store.cik10(cik)
    root = sec_root(day)
    try:
        result = sec_store.fetch_cik(client, root, cik, required_from=SUBMISSIONS_REQUIRED_FROM)
        facts_status = xbrl_store.fetch_cik(client, root, cik)
    except sec_store.SecFetchError as exc:
        raise SecUnavailable(f"SEC_FETCH_ERROR:{exc.code}") from exc
    if result.status == "NOT_FOUND":
        raise InputsNotReady("SEC_SUBMISSIONS_NOT_FOUND", cik)
    primary = sec_store.submissions_path(root, cik, f"CIK{cik}.json")
    doc = sec_store.read_gz_json(primary)
    rows: list[dict[str, Any]] = []
    for page in result.pages:
        rows.extend(sec_store.rows_of(sec_store.read_gz_json(sec_store.submissions_path(root, cik, page))))
    ledger = json.loads(sec_store.ledger_path(primary).read_text(encoding="utf-8"))
    fetched_at = datetime.fromisoformat(str(ledger["fetched_at"]).replace("Z", "+00:00"))
    facts_doc = (facts_reader or xbrl_store.read_facts)(root, cik) if facts_status != "MISSING" else None
    facts = extract_companyfacts(facts_doc, acceptance_index(doc)) if facts_doc else []
    return SecInputs(cik=cik, root=root, submissions_rows=rows, submissions_doc=doc,
                     submissions_checksum=ledger.get("file_sha256") or ledger.get("raw_sha256"),
                     submissions_fetched_at=fetched_at, facts_fetched=facts_doc is not None,
                     facts=facts, facts_document=facts_doc)


def split_dates(ticker: str) -> list[date]:
    out: set[date] = set()
    for directory in SPLITS_DIRS:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("splits_*.json.gz")):
            try:
                body = json.loads(gzip.decompress(path.read_bytes()))
            except (OSError, ValueError):
                continue
            for row in body.get("results") or []:
                if row.get("ticker") == ticker and row.get("execution_date"):
                    out.add(date.fromisoformat(row["execution_date"]))
    return sorted(out)


# -------------------------------------------------------------------------------------------------
# D1 stub -> D2 bundle -> D2.1 package
# -------------------------------------------------------------------------------------------------

@dataclass
class Identity:
    ticker: str
    cik: str
    security_id: str | None
    exchange: str | None
    snapshot_date: str | None


def build_stub(identity: Identity, sec: SecInputs, panel: Panel, *, run_id: str, cutoff: datetime):
    """``pipeline.assemble_candidate`` at the new cutoff: the frozen D1 code, the new data."""
    from app.backtest.strategy_h_v2.pipeline import assemble_candidate
    from app.backtest.strategy_h_v2.universe import SecurityTypeStatus, UniverseRow
    from app.backtest.strategy_h0.pilot import acceptance_index
    from app.dev.run_strategy_h_v2_d1 import market_snapshot, price_context_of

    row = UniverseRow(ticker=identity.ticker, cik=identity.cik, security_id=identity.security_id,
                      exchange=identity.exchange, exchange_supported=True,
                      security_type_status=SecurityTypeStatus.COMMON_STOCK, market="stocks", locale="us",
                      snapshot_date=identity.snapshot_date or cutoff.date().isoformat(), known_at=cutoff)
    series = panel.series(identity.ticker)
    accepted = acceptance_index(sec.submissions_doc)
    latest = max((t.date() for t in accepted.values() if t <= cutoff), default=None)
    return assemble_candidate(
        row, run_id=run_id, generated_at=cutoff, data_cutoff=cutoff, facts=sec.facts,
        facts_fetched=sec.facts_fetched, split_dates=split_dates(identity.ticker),
        market=market_snapshot(series),
        days_since_latest_filing=None if latest is None else (cutoff.date() - latest).days,
        price_context=price_context_of(series, panel.series(BENCHMARK)))


def depth_for(priority: str | None):
    """E3 P1 -> FULL, otherwise CORE (the D2 mapping; a cohort refresh that E3 no longer ranks is CORE)."""
    from app.backtest.strategy_h_v2.evidence.bundle import CandidateSource, CollectionDepth
    if priority == "P1_HIGH":
        return CandidateSource.E3_P1_HIGH, CollectionDepth.FULL, "E3_P1_HIGH"
    if priority == "P2_MEDIUM":
        return CandidateSource.E3_P2_MEDIUM, CollectionDepth.CORE, "E3_P2_MEDIUM"
    return CandidateSource.E3_P2_MEDIUM, CollectionDepth.CORE, "COHORT_REFRESH_DEFAULT_CORE"


def supplement_triggers(package, triggers: Sequence[str], sec: SecInputs, *, client, cache: Path):
    """Add each refresh-trigger filing the frozen D2.1 policy left out, with D2.1's own extractor.

    The depth policy reads an earnings release only from an EX-99 exhibit and caps material 8-Ks at
    one (CORE). Measured on 2026-10-13: FG's 8-K 0001934850-26-000095 (items 2.02, 7.01) carries its
    text in the primary document and has no EX-99, so the release resolved to EXHIBIT_UNRESOLVED;
    VRRM's 8-K 0001193125-26-412497 lost its one CORE slot to the later 8-K/A 0001193125-26-413031.
    In both cases the filing that triggered the refresh never reached D3. A refresh exists to read
    that filing, so the executor materialises its primary document through
    ``materialize._materialize_source`` (same fetch, extraction and chunking) as role
    ``REFRESH_TRIGGER`` and appends it. The frozen function and every earlier package are unchanged.
    Returns (package, [{accession, status, chunks}]).
    """
    from app.backtest.strategy_h_v2.evidence.chunk_schema import (
        AIResearchInputV1, CandidateMaterializationRecord)
    from app.backtest.strategy_h_v2.evidence.collector import _filing_source
    from app.backtest.strategy_h_v2.evidence.materialize import _materialize_source
    from app.strategies.h_forward.research_queue import chunked_accessions

    have = set(chunked_accessions(json.loads(package.model_dump_json())))
    rows = {r.get("accessionNumber"): r for r in sec.submissions_rows}
    added, documents, chunks, extra = [], list(package.materialization.documents), list(package.chunks), []
    for accession in sorted(set(triggers) - have):
        row = rows.get(accession)
        if row is None:
            added.append({"accession": accession, "status": "NOT_IN_SUBMISSIONS", "chunks": 0})
            continue
        source = _filing_source(sec.cik, row, fetched_at=sec.submissions_fetched_at, checksum=sec.submissions_checksum)
        record, new_chunks = _materialize_source(client, cache, source, role="REFRESH_TRIGGER",
                                                 candidate_id=package.evidence_bundle.ticker)
        documents.append(record)
        chunks.extend(new_chunks)
        if source.source_id not in {s.source_id for s in package.source_manifest}:
            extra.append(source)
        added.append({"accession": accession, "status": record.status.value, "chunks": len(new_chunks)})
    if not any(a["chunks"] for a in added):
        return package, added
    rebuilt = AIResearchInputV1(
        run_id=package.run_id, generated_at=package.generated_at, evidence_bundle=package.evidence_bundle,
        chunks=chunks, source_manifest=list(package.source_manifest) + extra,
        materialization=CandidateMaterializationRecord(
            ticker=package.materialization.ticker, candidate_id=package.materialization.candidate_id,
            content_readiness=package.materialization.content_readiness, documents=documents))
    return rebuilt, added


def build_package(stub_result, sec: SecInputs, *, run_id: str, cutoff: datetime, client,
                  work: Path, triggers: Sequence[str] = ()) -> tuple[Any, dict[str, Any], Path]:
    """D2 bundle and D2.1 package for one issuer, written immutably under ``work``."""
    from app.backtest.strategy_h_v2.evidence.collector import assemble_evidence_bundle_v2
    from app.backtest.strategy_h_v2.evidence.materialize import materialize_candidate

    stub = json.loads(stub_result.evidence.model_dump_json())
    priority = (stub.get("research_priority") or {}).get("state")
    source, depth, depth_rule = depth_for(priority)
    bundle = assemble_evidence_bundle_v2(
        stub, submissions_rows=sec.submissions_rows, cik=sec.cik, run_id=run_id, generated_at=cutoff,
        data_cutoff=cutoff, candidate_source=source, collection_depth=depth,
        submissions_checksum=sec.submissions_checksum, submissions_fetched_at=sec.submissions_fetched_at)
    cache = AS.root() / "d2_1_cache"
    package = materialize_candidate(client, cache, bundle, candidate_id=bundle.ticker)
    package, supplemented = supplement_triggers(package, triggers, sec, client=client, cache=cache)
    packages = work / "packages"
    packages.mkdir(parents=True, exist_ok=True)
    target = packages / f"{bundle.ticker}.json"
    if target.exists():
        raise FileExistsError(f"{target} exists; a package is never rewritten")
    target.write_text(package.model_dump_json(indent=2), encoding="utf-8")
    (work / "d1_stub.json").write_text(json.dumps(stub, indent=1, sort_keys=True), encoding="utf-8")
    meta = {"e3_priority": priority, "eligibility": (stub.get("eligibility") or {}).get("status"),
            "collection_depth": depth.value, "depth_rule": depth_rule,
            "content_readiness": package.materialization.content_readiness,
            "package_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "chunks": len(package.chunks), "trigger_supplement": supplemented}
    return package, meta, target


# -------------------------------------------------------------------------------------------------
# D5 binding
# -------------------------------------------------------------------------------------------------

REFERENCE_HISTORY = "reference_history"


def reference_history_dir() -> Path:
    """``<H store>/auto/reference_history``: the dated CS snapshots D5's share-class step needs for
    sessions before H's own daily reference fetches began (copied from A's quarterly series; never
    pruned). trader-j holds only ``CS_2026-10-01`` in A's directory, and ``load_reference_snapshot``
    refuses a snapshot older than 186 days, so without these every panel session before 2026-10-01
    would be MARKET_CAP_UNAVAILABLE on the server."""
    return AS.root() / REFERENCE_HISTORY


def d5_reference_dirs() -> tuple[Path, ...]:
    """share_class's own directories (anchored at the repository, not the cwd), then H's daily
    references, then H's pinned history. Newest ``as_of <= session`` wins; on a tie the first wins."""
    from app.backtest.strategy_h_v2.valuation import share_class as SC
    return tuple(REPO_ROOT / d for d in SC.REFERENCE_SNAPSHOT_DIRS) + (AS.root() / "reference", reference_history_dir())


def build_reference_history(source: Path, *, before: date) -> dict[str, Any]:
    target = reference_history_dir()
    target.mkdir(parents=True, exist_ok=True)
    copied = {}
    for path in sorted(source.glob("CS_*.json.gz")):
        try:
            as_of = date.fromisoformat(path.name[3:13])
        except ValueError:
            continue
        if as_of < before:
            raw = path.read_bytes()
            (target / path.name).write_bytes(raw)
            copied[path.name] = hashlib.sha256(raw).hexdigest()
    (target / "MANIFEST.json").write_text(json.dumps({"before": before.isoformat(), "files": copied},
                                                     indent=0, sort_keys=True), encoding="utf-8")
    return {"dir": str(target), "files": copied}


@contextlib.contextmanager
def d5_binding(*, packages_dir: Path, panel: Panel, sec: SecInputs | None = None,
               facts_roots: Sequence[Path] | None = None, submission_roots: Sequence[Path] | None = None,
               calendar: MarketCalendar | None = None) -> Iterator[dict[str, Any]]:
    """Point the frozen D5 loaders at this re-evaluation's inputs, then restore them.

    ``audit_strategy_h_v2_d5_p1.load_issuer`` reads the issuer's CIK from a D2.1 package and its
    companyfacts and submissions from fixed roots; ``_market_opens`` lists regular-session opens from
    the mirror's file names, which end on 2026-09-16. The binding replaces exactly those four names
    (and the opens cache) in that one module, and points D5-D2's ``load_reference_snapshot`` at
    ``d5_reference_dirs()`` (share_class's directories plus H's references). The valuation arithmetic
    is not touched.
    """
    from app.dev import audit_strategy_h_v2_d5_p1 as P1
    from app.dev import run_strategy_h_v2_d5_d2 as D52
    from app.backtest.strategy_h_v2.valuation.share_class import load_reference_snapshot
    cal = calendar or MarketCalendar()
    days = sorted(panel.bars.get(BENCHMARK, {}))
    sessions = _sessions(cal, days[0], days[-1]) if days else []
    names = ("D2_1_PACKAGES", "FACTS_ROOTS", "SUBMISSION_ROOTS", "_SESSIONS", "_OPENS_CACHE")
    saved = {n: getattr(P1, n) for n in names}
    saved_reference = D52.load_reference_snapshot
    dirs = d5_reference_dirs()
    try:
        D52.load_reference_snapshot = lambda on_or_before: load_reference_snapshot(on_or_before, dirs)
        P1.D2_1_PACKAGES = packages_dir
        P1.FACTS_ROOTS = tuple(facts_roots) if facts_roots is not None else (sec.root,)
        P1.SUBMISSION_ROOTS = (tuple(submission_roots) if submission_roots is not None
                               else (sec.root / "submissions",))
        P1._SESSIONS = sessions
        P1._OPENS_CACHE = {}
        yield {"bound": list(names) + ["run_strategy_h_v2_d5_d2.load_reference_snapshot"], "sessions": len(sessions),
               "reference_dirs": [str(d) for d in dirs]}
    finally:
        for n, v in saved.items():
            setattr(P1, n, v)
        D52.load_reference_snapshot = saved_reference


def sha(body: Any) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                                     default=str).encode()).hexdigest()


ClientFactory = Callable[[], Any]
