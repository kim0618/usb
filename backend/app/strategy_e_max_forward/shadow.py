"""E-MAX forward shadow path: 09:25 decision seal, exact-bar shadow outcome, append-only registry.

Phase 1 (``shadow_decision``) takes the V1.1 decision seal and its 09:25 frame and fixes, for both
strategies, everything that is decided before the open: E-BASE's canonical max-3 selection, E-MAX's
R1 order and max-3 selection, and the B2 breadth state. The record is hashed and written once.

Phase 2 (``shadow_outcome``) accepts only a verified record and the exact 09:30 / 09:34 bars, runs
both strategies through ``capacity.execute`` (E-D2 / E-D3 / E-D4 / E-D5 unmodified) and scales
E-MAX with ``v1.compose``. It never reads an outcome back into a decision.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from app.backtest.strategy_e1_forward import checkpoint as E1C
from app.backtest.strategy_e1_forward.layout import ForwardViolation, require_forward_session
from app.backtest.strategy_e1_forward.seal import SEALED_FEATURES
from app.strategy_e.exits import EXIT_BAR_START_ET
from app.strategy_e.execution import ENTRY_BAR_START_ET
from app.strategy_e_max import breadth, capacity, ranking, v1
from app.strategy_e_max_forward import rules as F
from app.strategy_e_v1_1 import context, decision

SEAL_FORMAT = "strategy-e-max-forward-seal-v1"
ET = context.ET


def _canonical(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _finite(value: float) -> float | None:
    return float(value) if math.isfinite(float(value)) else None


def _frac(value: Fraction) -> str:
    return f"{value.numerator}/{value.denominator}"


# -- phase 1: the 09:25 seal ----------------------------------------------------------------------

def shadow_decision(sealed: decision.DecisionSeal, frame, *, forward_mode: str,
                    decided_at: datetime) -> dict[str, Any]:
    """Both strategies' pre-open decisions, from the V1.1 seal and its 09:25 frame only."""
    decision.verify(sealed)
    session = date.fromisoformat(sealed.session)
    require_forward_session(session)
    if frame.session != session:
        raise ForwardViolation("frame and V1.1 seal are for different sessions")
    if forward_mode not in F.EVIDENCE_CLASS:
        raise ForwardViolation(f"unknown forward mode {forward_mode!r}")
    if forward_mode == F.LIVE and decided_at >= datetime.combine(session, time(9, 30), tzinfo=ET):
        raise context.FutureContextViolation("a LIVE seal must be written before 09:30 ET")
    signal = sealed.signal
    if len(frame.symbols) != signal.eligible_count or len(signal.h5_mask) != len(frame.symbols):
        raise ForwardViolation("frame universe differs from the sealed universe")
    index = {s: k for k, s in enumerate(frame.symbols)}
    candidates = tuple(signal.candidate_symbols)
    features = {s: {n: float(frame.features[n][index[s]]) for n in SEALED_FEATURES} for s in candidates}
    r1 = ranking.order(v1.RANKING, candidates, features)
    k = breadth.multiplier(signal.candidate_count, signal.eligible_count)
    rate = breadth.h5_rate(signal.candidate_count, signal.eligible_count)
    universe = [{"symbol": s, "h5": bool(m), **{n: _finite(frame.features[n][index[s]]) for n in SEALED_FEATURES}}
                for s, m in zip(frame.symbols, signal.h5_mask)]
    record = {
        "format": SEAL_FORMAT, "session": sealed.session, "forward_mode": forward_mode,
        "evidence_class": F.EVIDENCE_CLASS[forward_mode], "decision_cutoff_et": "09:25",
        "decision_timestamp": decided_at.isoformat(),
        "universe": {"eligible_count": signal.eligible_count,
                     "symbols_digest": hashlib.sha256("|".join(frame.symbols).encode()).hexdigest(),
                     "rows": universe},
        "h5": {"count": signal.candidate_count, "candidates": list(candidates),
               "signal_decision_digest": signal.decision_digest},
        F.E_BASE: {"order": list(candidates), "selected": list(sealed.selected), "exposure": "1/1"},
        F.E_MAX: {"order": list(r1), "selected": list(r1[:v1.MAX_SELECTED]),
                  "breadth": {"universe_rows": signal.eligible_count, "h5_count": signal.candidate_count,
                              "h5_rate": rate, "high_breadth": k != 1, "multiplier": _frac(k)},
                  "global_multiplier": _frac(v1.GLOBAL), "final_exposure": _frac(k * v1.GLOBAL)},
        "digests": {"v1_1_seal": sealed.seal_digest, "v1_1_rules": decision.RULES_CANONICAL_SHA256,
                    "e_max_v1_rules": v1.RULES_CANONICAL_SHA256, "forward_rules": F.RULES_CANONICAL_SHA256,
                    "context_contract": context.CONTRACT_CANONICAL_SHA256},
    }
    record["decision_digest"] = hashlib.sha256(_canonical(record)).hexdigest()
    return record


def verify_record(record: Mapping[str, Any]) -> None:
    body = {k: v for k, v in record.items() if k != "decision_digest"}
    if hashlib.sha256(_canonical(body)).hexdigest() != record.get("decision_digest"):
        raise ForwardViolation("forward decision seal was altered after 09:25")


def write_seal(path: Path, record: Mapping[str, Any]) -> Path:
    """Write once. A second write for the same path is refused, whatever its content."""
    verify_record(record)
    if path.exists():
        raise ForwardViolation(f"{path.name} already sealed; a forward seal is immutable")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False, indent=1) + "\n")
    return path


def read_seal(path: Path) -> dict[str, Any]:
    record = json.loads(path.read_text(encoding="utf-8"))
    verify_record(record)
    return record


# -- phase 2: the exact-bar shadow outcome ---------------------------------------------------------

def _descriptors(frame, symbols: Sequence[str]) -> dict[str, dict[str, float]]:
    index = {s: k for k, s in enumerate(frame.symbols)}
    return {s: {"close_price": float(frame.features["close_price"][index[s]]),
                "previous_day_dollar_volume": float(frame.features["previous_day_dollar_volume"][index[s]])}
            for s in symbols}


def _execute(signal, selected, bars, frame, calendar) -> dict[str, Any]:
    out = capacity.execute(signal, tuple(selected), bars, _descriptors(frame, selected), calendar,
                           capacity=v1.MAX_SELECTED)
    out["candidates"] = signal.candidate_count
    return out


def shadow_outcome(record: Mapping[str, Any], sealed: decision.DecisionSeal, frame,
                   bars: Mapping[str, Mapping[str, Sequence]], calendar) -> dict[str, dict[str, Any]]:
    """{strategy: session} for E-BASE (1.0x) and E-MAX (B2 x 2.0x) from the sealed selections."""
    verify_record(record)
    decision.verify(sealed)
    if record["digests"]["v1_1_seal"] != sealed.seal_digest:
        raise ForwardViolation("outcome bars offered to a different seal")
    signal = sealed.signal
    base = _execute(signal, record[F.E_BASE]["selected"], bars, frame, calendar)
    raw = _execute(signal, record[F.E_MAX]["selected"], bars, frame, calendar)
    _, emax = v1.compose(raw)
    if emax["final_exposure_multiplier"] != record[F.E_MAX]["final_exposure"]:
        raise ForwardViolation("E-MAX exposure differs from the sealed breadth state")
    return {F.E_BASE: base, F.E_MAX: emax}


def _bar_volume(bars, symbol: str, start: str) -> float | None:
    found = [b for b in bars.get(symbol, {}).get(start, ()) if b.bar_start_et == start]
    return float(found[0].volume) if len(found) == 1 else None


def registry_rows(record: Mapping[str, Any], outcome: Mapping[str, Mapping[str, Any]],
                  bars: Mapping[str, Mapping[str, Sequence]]) -> list[dict[str, Any]]:
    """One row per H5 candidate per strategy (the rules' ``registry.position_fields``)."""
    rows = []
    for strategy, version in ((F.E_BASE, decision.TRADING_VERSION), (F.E_MAX, v1.STRATEGY_ID)):
        decided = record[strategy]
        session = outcome[strategy]
        executed = {r["symbol"]: r for r in session["records"]}
        b = record[F.E_MAX]["breadth"]
        k = Fraction(b["multiplier"]) if strategy == F.E_MAX else Fraction(1)
        g = v1.GLOBAL if strategy == F.E_MAX else Fraction(1)
        for rank, symbol in enumerate(decided["order"], start=1):
            r = executed.get(symbol)
            final = Fraction(r["weight"]) if r else Fraction(0)
            sized = bool(r and r["standard_pnl"])
            rows.append({
                "strategy": strategy, "strategy_version": version,
                "forward_mode": record["forward_mode"], "evidence_class": record["evidence_class"],
                "session_date": record["session"], "decision_timestamp": record["decision_timestamp"],
                "decision_digest": record["decision_digest"],
                "universe_count": b["universe_rows"], "h5_count": b["h5_count"], "h5_rate": b["h5_rate"],
                "high_breadth": b["high_breadth"], "symbol": symbol, "rank": rank,
                "selected": symbol in decided["selected"],
                "entry_timestamp": ENTRY_BAR_START_ET if r else None,
                "entry_price": r["entry_price"] if r else None,
                "entry_valid": bool(r and r["entry_status"] == "EXECUTED_PROXY"),
                "entry_reason": r["entry_status"] if r else "NOT_SELECTED_CAPACITY",
                "exit_timestamp": EXIT_BAR_START_ET if r else None,
                "exit_price": r["exit_price"] if r else None,
                "exit_valid": sized, "exit_reason": (r["exit_reason"] or r["exit_status"]) if r else None,
                "base_weight": _frac(final / (k * g)) if r else "0/1",
                "breadth_multiplier": _frac(k), "global_multiplier": _frac(g), "final_weight": _frac(final),
                "gross_return": str(r["GROSS_0BP"]) if sized else None,
                **{f"net_{bp}bp": (str(r[f"COST_{bp}BP"]) if sized else None) for bp in ("05", "10", "15", "20")},
                "open_bar_volume_0930": _bar_volume(bars, symbol, ENTRY_BAR_START_ET) if r else None,
                "close_bar_volume_0934": _bar_volume(bars, symbol, EXIT_BAR_START_ET) if r else None,
                "bid_0930": None, "ask_0930": None, "bid_0934": None, "ask_0934": None,
                "friction_status": "QUOTES_NOT_AVAILABLE",
                "upstream_digests": record["digests"], "forward_rules_digest": F.RULES_CANONICAL_SHA256,
            })
    return rows


def session_rows(record: Mapping[str, Any], outcome: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    b = record[F.E_MAX]["breadth"]
    out = []
    for strategy, version in ((F.E_BASE, decision.TRADING_VERSION), (F.E_MAX, v1.STRATEGY_ID)):
        s = outcome[strategy]
        out.append({"strategy": strategy, "strategy_version": version, "forward_mode": record["forward_mode"],
                    "evidence_class": record["evidence_class"], "session_date": record["session"],
                    "decision_digest": record["decision_digest"], "universe_count": b["universe_rows"],
                    "h5_count": b["h5_count"], "h5_rate": b["h5_rate"], "high_breadth": b["high_breadth"],
                    "exposure": str(s["exposure"]), "active": bool(s["active"]),
                    "standard_trades": sum(bool(r["standard_pnl"]) for r in s["records"]),
                    **{c: str(s["returns"][c]) for c in F.COST_SCENARIOS}})
    return out


# -- append-only registry -----------------------------------------------------------------------------

def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def append(path: Path, rows: Sequence[Mapping[str, Any]]) -> int:
    """Append rows; refuse any (strategy, forward_mode, session_date) already present."""
    keys = {(r["strategy"], r["forward_mode"], r["session_date"]) for r in rows}
    for r in rows:
        require_forward_session(date.fromisoformat(r["session_date"]))
    present = {(r["strategy"], r["forward_mode"], r["session_date"]) for r in _read_jsonl(path)}
    clash = keys & present
    if clash:
        raise ForwardViolation(f"{sorted(clash)} already recorded; the forward registry is append-only")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for r in rows:
            handle.write(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n")
    return len(rows)


def checkpoint_n(session_path: Path, *, forward_mode: str | None = None) -> int:
    """N = H5 observation rows over recorded forward sessions (E1 registry definition); one strategy
    is counted because both share the seal."""
    return sum(int(r["h5_count"]) for r in _read_jsonl(session_path)
               if r["strategy"] == F.E_MAX and (forward_mode is None or r["forward_mode"] == forward_mode))


def checkpoint_status(session_path: Path, directory: Path) -> dict[str, Any]:
    n = checkpoint_n(session_path)
    return {**E1C.status(n, directory), "n_live": checkpoint_n(session_path, forward_mode=F.LIVE),
            "n_reconstructed": checkpoint_n(session_path, forward_mode=F.RECONSTRUCTED)}
