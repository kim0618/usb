"""A's failure boundary inside E's worker, and the audit record a failure leaves behind.

A's cut runs *inside* E's acquisition process, because a second process cannot have either
Kiwoom lane: both are already held at 4.9 req/s against a measured 5 req/s per-API-ID limit.
That arrangement hands A one power it must not have. An exception raised on A's side unwinds
E's worker, and E's worker is official paper trading.

``run_cut`` was already wrapped. ``attach`` was not - and ``attach`` is precisely where the
data authority is read (``universe.build`` reads the reference cache, the grouped daily panel
and the split calendar), so a genuine data defect raised there and took E down with it. One
such defect was real: ``UNI.build`` demanded the grouped daily file of the session it was
planning, which on any live morning cannot exist yet.

The boundary lives here rather than in the worker for two reasons. The worker's edit stays four
expressions, which is what makes "E is unchanged when A is off" readable instead of promised;
and the classification is A's own knowledge, so it belongs in A's package beside A's tests.

**This is not ``except Exception: pass``.** Two kinds of failure are told apart:

* an *expected* data or authority failure - one of A's named refusals, a missing store file, a
  database that will not open - is A failing closed. The candidate count is zero, no GPT call
  is made, no paper injection can happen, and the named reason is recorded;
* an *unexpected* failure - a ``TypeError``, an ``AttributeError``, anything meaning A's own
  code is wrong - is recorded under ``ATTACH_FAILED`` / ``SCANNER_NOT_RUN`` with reason
  ``UNEXPECTED_ERROR`` *and its traceback*, and is not re-raised in the worker.

That second choice is deliberate, and the alternative is defensible in the abstract: re-raising
surfaces a programming bug at once. In *this* architecture the process that would receive the
exception is E's live paper worker, so re-raising turns "A has a bug" into "E stopped trading",
which is exactly the coupling this module exists to remove. The unexpected case is therefore
audited loudly and not re-raised - and ``strict=True`` re-raises it, for the dry run and the
tests, where surfacing costs nothing. ``KeyboardInterrupt`` and ``SystemExit`` are never caught.

**Nothing is substituted for a failed A.** The legacy trade-value scanner is not run, the
previous session's candidates are not reused, no same-day Massive premarket read is attempted
and no stale snapshot is read. ``FALLBACKS_DISABLED`` names each of those so a reader of a run
record can see which substitutions were considered and refused, and the status says ``FAILED``
rather than an empty success - "A found nobody" must not be recorded for a morning A never
scanned.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import StrEnum
import json
import os
from pathlib import Path
import traceback
from typing import Any

from app.strategy_a_mover_live import config as CFG

#: One file per session, in the runtime convention the raw store already uses.
STATUS_ROOT = "data/runtime/strategy_a_mover_live/status"
STATUS_FORMAT = "a-mover-live-status-v1"
#: What an audit entry is *the same entry* by. Repeating a failure must not grow the file.
IDENTITY = ("phase", "refusal", "reason", "detail")
#: How much of a traceback is kept. Enough to locate the bug, not enough to fill a disk.
TRACEBACK_LIMIT = 8_000

#: The substitutions that are not made when A fails. Named, so the refusal is reviewable.
FALLBACKS_DISABLED = (
    "LEGACY_QUANT_V0_SCANNER",
    "PREVIOUS_SESSION_CANDIDATES",
    "MASSIVE_CURRENT_SESSION_PREMARKET",
    "STALE_SNAPSHOT",
    "EMPTY_SUCCESS",
)


class Phase(StrEnum):
    """Where A was when it failed. ``ATTACH`` is outside E's loop; ``CUT`` is at 09:15."""

    ATTACH = "ATTACH"
    CUT = "CUT"


class Reason(StrEnum):
    """The precise reason, under the broader refusal. Every value is recorded, never absorbed."""

    #: A's universe inputs: the dated reference cache in force on the session.
    REFERENCE_UNAVAILABLE = "REFERENCE_UNAVAILABLE"
    #: Massive grouped daily for a session *before* the scan session.
    NO_GROUPED_DAILY = "NO_GROUPED_DAILY"
    #: The 20-session premarket denominator, Kiwoom or bootstrap.
    BASELINE_UNAVAILABLE = "BASELINE_UNAVAILABLE"
    #: The research parent's checksums no longer reproduce.
    CONTRACT_DRIFT = "CONTRACT_DRIFT"
    #: Stored data contradicts itself - a duplicated minute with two values, a short snapshot.
    DATA_INTEGRITY_ERROR = "DATA_INTEGRITY_ERROR"
    #: E staged no artifact for this session, or staged one that is not this session's.
    STALE_STAGING_ARTIFACT = "STALE_STAGING_ARTIFACT"
    #: The two cuts are not in the order the shared schedule requires.
    SCHEDULE_INVALID = "SCHEDULE_INVALID"
    #: The session has no minute tape or snapshot to scan at all.
    NO_PREMARKET_DATA = "NO_PREMARKET_DATA"
    #: The runtime database could not be read or written.
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
    #: A store file could not be read, for a filesystem reason rather than a contract one.
    STORE_IO_ERROR = "STORE_IO_ERROR"
    #: The catch-all for a named data refusal with no A-level synonym.
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    #: A's own code is wrong. Recorded with a traceback; never silently absorbed.
    UNEXPECTED_ERROR = "UNEXPECTED_ERROR"


#: ``DataUnavailable`` values that have an A-level synonym. Anything else keeps its own name.
_DATA_REASONS: dict[str, Reason] = {
    "NO_GROUPED_DAILY": Reason.NO_GROUPED_DAILY,
    "NO_REFERENCE_UNIVERSE": Reason.REFERENCE_UNAVAILABLE,
    "PREMARKET_BASELINE_TOO_SHORT": Reason.BASELINE_UNAVAILABLE,
    "NO_MINUTE_TAPE": Reason.NO_PREMARKET_DATA,
    "NO_LIVE_PREMARKET_SOURCE": Reason.NO_PREMARKET_DATA,
    "SESSION_NOT_COVERED": Reason.DATA_UNAVAILABLE,
}


@dataclass(frozen=True)
class Failure:
    """One classified failure, as plain data an audit row and a run record can both carry."""

    phase: Phase
    refusal: CFG.Refusal
    reason: Reason | str
    detail: str
    expected: bool
    error: str
    traceback: str | None = None

    def entry(self) -> dict[str, Any]:
        """The audit row. ``IDENTITY`` over this is what makes a repeat idempotent."""
        body: dict[str, Any] = {
            "phase": str(self.phase), "status": "FAILED", "refusal": str(self.refusal),
            "reason": str(self.reason), "detail": self.detail, "expected": self.expected,
            "error": self.error,
        }
        if self.traceback is not None:
            body["traceback"] = self.traceback
        return body

    def status(self) -> dict[str, Any]:
        """What the run record says about A. ``FAILED``, never an empty success."""
        return {
            "status": "FAILED", "phase": str(self.phase), "refusal": str(self.refusal),
            "reason": str(self.reason), "detail": self.detail,
            "expected_failure": self.expected, "error": self.error,
            "candidates": 0, "gpt_calls": 0, "paper_injections": 0,
            "fallback": "NONE", "fallbacks_disabled": list(FALLBACKS_DISABLED),
            "e_run_continues": True,
            "boundary": ("AUDITED_FAIL_CLOSED" if self.expected
                         else "AUDITED_NOT_RERAISED_TO_PROTECT_E"),
        }


def _message(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


def classify(error: BaseException, *, phase: Phase) -> Failure:
    """Name the failure. Imports are local so a classification costs nothing until it happens."""
    from app.backtest.mover_scanner_v1 import contract as KC
    from app.services.mover_scanner_source import MoverDataUnavailableError
    from app.strategy_a_mover_live import bootstrap as BOOT
    from app.strategy_a_mover_live import contract as LC
    from app.strategy_a_mover_live import raw_store as RAW
    from app.strategy_a_mover_live import scanner as SCAN
    from app.strategy_a_mover_live import schedule as SCHED
    from app.strategy_a_mover_live import snapshot as SNAP
    from app.strategy_a_mover_live import universe as UNI

    text = _message(error)

    def expected(refusal: CFG.Refusal, reason: Reason | str) -> Failure:
        return Failure(phase, refusal, reason, str(error), True, text)

    if isinstance(error, CFG.LivePipelineDisabled):
        return expected(CFG.Refusal.DISABLED, str(error.refusal))
    if isinstance(error, SCAN.LiveScanRefused):
        return expected(error.refusal, _reason_of_detail(error.detail))
    if isinstance(error, MoverDataUnavailableError):
        return expected(CFG.Refusal.DATA_UNAVAILABLE,
                        _DATA_REASONS.get(str(error.reason), str(error.reason)))
    if isinstance(error, UNI.UniverseUnavailable):
        return expected(CFG.Refusal.DATA_UNAVAILABLE,
                        getattr(error, "reason", None) or Reason.DATA_UNAVAILABLE)
    if isinstance(error, (LC.LiveContractDrift, KC.ContractDrift)):
        return expected(CFG.Refusal.CONTRACT_DRIFT, Reason.CONTRACT_DRIFT)
    if isinstance(error, BOOT.TapeUnavailable):
        return expected(CFG.Refusal.DATA_UNAVAILABLE, Reason.BASELINE_UNAVAILABLE)
    if isinstance(error, (RAW.RawConflict, SNAP.SnapshotIncomplete)):
        return expected(CFG.Refusal.DATA_UNAVAILABLE, Reason.DATA_INTEGRITY_ERROR)
    if isinstance(error, SCHED.ScheduleInvalid):
        return expected(CFG.Refusal.SCANNER_NOT_RUN, Reason.SCHEDULE_INVALID)
    if isinstance(error, json.JSONDecodeError):
        return expected(CFG.Refusal.DATA_UNAVAILABLE, Reason.DATA_INTEGRITY_ERROR)
    if _is_database_error(error):
        return expected(CFG.Refusal.DATA_UNAVAILABLE, Reason.DATABASE_UNAVAILABLE)
    if isinstance(error, OSError):
        return expected(CFG.Refusal.DATA_UNAVAILABLE, Reason.STORE_IO_ERROR)
    unexpected = (CFG.Refusal.ATTACH_FAILED if phase is Phase.ATTACH
                  else CFG.Refusal.SCANNER_NOT_RUN)
    return Failure(phase, unexpected, Reason.UNEXPECTED_ERROR, str(error), False, text,
                   traceback="".join(traceback.format_exception(error))[:TRACEBACK_LIMIT])


def _reason_of_detail(detail: str) -> Reason | str:
    """``run_from_source`` prefixes a data refusal with its ``DataUnavailable`` name."""
    head = detail.split(":", 1)[0].strip()
    return _DATA_REASONS.get(head, head or Reason.DATA_UNAVAILABLE)


def _is_database_error(error: BaseException) -> bool:
    try:
        from sqlalchemy.exc import SQLAlchemyError
    except Exception:                                    # pragma: no cover - sqlalchemy is a dep
        return False
    return isinstance(error, SQLAlchemyError)


# -- the audit record ------------------------------------------------------------------------

def status_path(repo: Path, session: date, root: str = STATUS_ROOT) -> Path:
    return Path(repo) / root / f"{session.isoformat()}.json"


def _identity(entry: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(entry.get(name) for name in IDENTITY)


def read_status(repo: Path, session: date, root: str = STATUS_ROOT) -> dict[str, Any] | None:
    path = status_path(repo, session, root)
    if not path.is_file():
        return None
    body = json.loads(path.read_text(encoding="utf-8"))
    if body.get("format") != STATUS_FORMAT:
        raise ValueError(f"{path} is not {STATUS_FORMAT}")
    return body


def record(repo: Path, session: date, entry: dict[str, Any], *, root: str = STATUS_ROOT,
           at: datetime | None = None) -> Path:
    """Append one audit entry, idempotently: the same failure twice is one entry, counted.

    A worker restarted into the same broken morning must not grow this file without bound, and
    an operator reading it must not have to count duplicates to see what happened. So identity
    is the failure itself - phase, refusal, reason, detail - and a repeat updates
    ``occurrences`` and ``last_seen_at`` while leaving the entry list exactly as it was.
    """
    path = status_path(repo, session, root)
    body: dict[str, Any] = {"format": STATUS_FORMAT, "session": session.isoformat(),
                            "entries": []}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            loaded = None                    # a truncated file is replaced, never appended to
        if isinstance(loaded, dict) and loaded.get("format") == STATUS_FORMAT:
            body["entries"] = [dict(item) for item in loaded.get("entries") or []]
    stamp = (at or datetime.now(timezone.utc)).isoformat()
    identity = _identity(entry)
    touched: dict[str, Any] | None = None
    for existing in body["entries"]:
        if _identity(existing) == identity:
            existing["occurrences"] = int(existing.get("occurrences", 1)) + 1
            existing["last_seen_at"] = stamp
            touched = existing
            break
    if touched is None:
        touched = dict(entry) | {"occurrences": 1, "first_seen_at": stamp,
                                 "last_seen_at": stamp}
        body["entries"].append(touched)
    body["latest"] = {name: touched.get(name) for name in
                      ("phase", "status", "refusal", "reason", "last_seen_at")}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(json.dumps(body, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return path


# -- the boundary ----------------------------------------------------------------------------

@dataclass
class IsolatedAttach:
    """The attach seam's result. ``handle`` is None whenever A is not running this morning.

    E's side reads three things and nothing else: ``handle`` for the two guarded expressions,
    ``active`` to decide whether A belongs in the run record at all, and ``run_cut`` which
    never raises. With the flag off ``active`` is False and ``run_cut`` is never called, so no
    key is added to E's report and the report stays what it is today.
    """

    active: bool
    handle: Any | None
    session: date
    repo: Path
    status: dict[str, Any] | None = None
    strict: bool = False
    log: Callable[[str], None] = print
    status_root: str = STATUS_ROOT
    #: Every audit write attempted by this attach, in order. Read by the tests and the runner.
    audited: list[Path] = field(default_factory=list)

    def run_cut(self) -> dict[str, Any]:
        """A's cut behind the boundary. Returns a status body; it does not raise."""
        if not self.active:
            return {"status": str(CFG.Refusal.DISABLED), "candidates": 0, "gpt_calls": 0,
                    "paper_injections": 0}
        if self.handle is None:
            return dict(self.status or {})
        try:
            body = self.handle.run_cut()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as error:
            failure = classify(error, phase=Phase.CUT)
            if self.strict and not failure.expected:
                raise
            return self.fail(failure)
        body["isolation"] = self.audit(self._outcome(body))
        return body

    # -- recording
    def fail(self, failure: Failure) -> dict[str, Any]:
        """Record a classified failure and return the status the run record carries."""
        self.log(f"A_MOVER_LIVE {failure.phase} {failure.refusal}/{failure.reason}: "
                 f"{failure.error}")
        return failure.status() | {"isolation": self.audit(failure.entry())}

    def audit(self, entry: dict[str, Any]) -> dict[str, Any]:
        """Persist the entry. An audit that cannot be written must not take E's run down."""
        body = {"boundary": "A_ATTACH_AND_CUT", "e_run_continues": True,
                "fallbacks_disabled": list(FALLBACKS_DISABLED)}
        try:
            path = record(self.repo, self.session, entry, root=self.status_root)
        except Exception as error:                 # the audit is not more important than E
            self.log(f"A_MOVER_LIVE_AUDIT_UNWRITABLE {_message(error)}")
            return body | {"audit": None, "audit_error": _message(error)}
        self.audited.append(path)
        return body | {"audit": str(path)}

    def _outcome(self, body: dict[str, Any]) -> dict[str, Any]:
        """The cut's own outcome as an audit entry, refusal or not.

        A refusal that ``run_cut`` *returns* - the scan could not assemble its inputs, or it
        admitted nobody - must land in the same audit file as one that was raised. Otherwise
        the file answers "did A crash" when the question is "did A produce candidates".
        """
        scan = body.get("scan") or {}
        refusal = str(scan.get("status") or "RAN")
        failed = refusal in {str(item) for item in CFG.Refusal} and refusal != str(
            CFG.Refusal.NO_CANDIDATES)
        handoff = body.get("handoff") or {}
        return {"phase": str(Phase.CUT),
                "status": "FAILED" if failed else "OK",
                "refusal": refusal,
                "reason": str(scan.get("reason") or ""),
                "detail": str(scan.get("detail") or ""),
                "expected": True,
                "error": "",
                "candidates": int(scan.get("candidates") or 0),
                "gpt_calls": int(handoff.get("gpt_calls") or 0)}
