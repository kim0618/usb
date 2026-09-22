"""Writing a run's artifacts: deterministic bytes, append-only, no wall clock in the content.

Determinism is the contract's requirement and it constrains the writers more than it looks:
keys are sorted, floats are written as Python repr through json, rows go out in a fixed order,
and nothing here reads the clock. The only time-dependent facts (when the run happened, on what
commit, with what working tree) live in the provenance block of identity.json, which is not part
of any digest.

``candidates.jsonl`` deserves its place among the required files. The drop-reason distribution
of candidates that never entered is the only evidence for whether the rules were too tight, and
the contract requires reading it before anyone proposes changing a rule.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
import json
from pathlib import Path

from app.backtest.strategy_b.engine import SessionReport
from app.backtest.strategy_b.portfolio import Trade
from app.strategy_b.fsm import Candidate


class ArtifactExists(RuntimeError):
    """A run directory already holds this file. Runs are append-only, never overwritten."""


class RunExists(ArtifactExists):
    """This run id already has a directory (COMPLETE or not) or a leftover staging directory."""


MARKER = "COMPLETE.json"
STAGING_PREFIX = ".staging-"


def staging_root(out_root: Path, run_id: str) -> Path:
    return out_root / f"{STAGING_PREFIX}{run_id}"


def refuse_existing(out_root: Path, run_id: str) -> None:
    """A run id is written once. A COMPLETE run is never rerun into place and never overwritten;
    an incomplete directory or a crashed staging directory is reported, never cleaned up here."""
    final = out_root / run_id
    if (final / MARKER).is_file():
        raise RunExists(f"{run_id} is already COMPLETE at {final}; a result is never rewritten. "
                        "Compare against it instead of rerunning into it.")
    if final.exists():
        raise RunExists(f"{final} exists without {MARKER}: an incomplete run. Inspect it by hand.")
    if staging_root(out_root, run_id).exists():
        raise RunExists(f"{staging_root(out_root, run_id)} is left from a crashed run; it is not a "
                        "result. Inspect and remove it by hand before rerunning.")


def finalize(staging: Path, out_root: Path, run_id: str, *, run_identity: str) -> Path:
    """Write the marker last, with every artifact's sha256, then move the directory into place."""
    import hashlib
    import os

    files = sorted(p for p in staging.rglob("*") if p.is_file())
    marker = {"run_id": run_id, "run_identity": run_identity, "status": "COMPLETE",
              "artifacts": {str(p.relative_to(staging)): hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in files}}
    RunWriter(staging).write_json(MARKER, marker)
    final = out_root / run_id
    if final.exists():
        raise RunExists(f"{final} appeared while the run was writing; not overwritten")
    os.rename(staging, final)
    return final


def is_complete(run_root: Path) -> bool:
    return (run_root / MARKER).is_file()


@dataclass(frozen=True, slots=True)
class RunWriter:
    root: Path

    def path(self, name: str) -> Path:
        return self.root / name

    def _open(self, name: str) -> Path:
        target = self.path(name)
        if target.exists():
            raise ArtifactExists(f"{target} already exists; a run never rewrites its own output")
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def write_json(self, name: str, body: Mapping[str, object]) -> Path:
        target = self._open(name)
        target.write_text(json.dumps(body, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
                          encoding="utf-8")
        return target

    def write_jsonl(self, name: str, rows: Iterable[Mapping[str, object]]) -> Path:
        target = self._open(name)
        lines = [json.dumps(row, sort_keys=True, ensure_ascii=False) for row in rows]
        target.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
        return target

    def missing(self, required: Sequence[str]) -> tuple[str, ...]:
        return tuple(name for name in required if not self.path(name).exists())


def trade_row(trade: Trade) -> dict[str, object]:
    return {
        "symbol": trade.symbol,
        "setup": str(trade.setup),
        "session_date": trade.session_date.isoformat(),
        "entered_at": _stamp(trade.entered_at),
        "entry_bar_timestamp": _stamp(trade.entry_bar_timestamp),
        "entry_price": trade.entry_price,
        "shares": trade.shares,
        "initial_stop": trade.initial_stop,
        "risk_per_share": trade.risk_per_share,
        "risk_amount": trade.risk_amount,
        "entry_fee": trade.entry_fee,
        "fees": trade.fees,
        "gross_pnl": trade.gross_pnl,
        "net_pnl": trade.net_pnl,
        "realized_r": trade.realized_r,
        "closed": trade.is_closed,
        "legs": [{"reason": str(leg.reason), "at": _stamp(leg.at), "price": leg.price,
                  "shares": leg.shares, "fee": leg.fee} for leg in trade.legs],
    }


def candidate_row(candidate: Candidate, session: date) -> dict[str, object]:
    return {
        "symbol": candidate.symbol,
        "session_date": session.isoformat(),
        "state": str(candidate.state),
        "score": candidate.score,
        "detected_at": _stamp(candidate.detected_at),
        "qualified_at": _stamp(candidate.qualified_at),
        "setup_ready_at": _stamp(candidate.setup_ready_at),
        "signal_at": _stamp(candidate.signal_at),
        "entered_at": _stamp(candidate.entered_at),
        "updated_at": _stamp(candidate.updated_at),
        "drop_reason": None if candidate.drop_reason is None else str(candidate.drop_reason),
        "eligibility_reasons": [str(reason) for reason in candidate.eligibility_reasons],
        "entry_price": candidate.entry_price,
    }


def session_row(report: SessionReport) -> dict[str, object]:
    return {
        "session_date": report.session_date.isoformat(),
        "ticks": report.ticks,
        "prefiltered_minutes": report.prefiltered_minutes,
        "candidates_opened": report.candidates_opened,
        "entries": report.entries,
        "trades": len(report.trades),
        "refusals": dict(sorted(report.refusals.items())),
    }


def _stamp(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()
