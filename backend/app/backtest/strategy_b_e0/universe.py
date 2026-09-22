"""The run universe, read from an immutable artifact and never from a number someone typed.

The contract's rule is that the symbol set is a source of truth on disk, hashed into the run
identity, and that every count is derived by reading it. The artifact holds two different
things and the difference is the whole point:

* ``symbols`` is the union over the window of the per-session scope sets. As a *set* it looks
  forward: a symbol that first qualifies late in the window is a member. That is the same
  construction the fetch planner uses, and on its own it would be survivorship selection.
* ``scope_sessions`` is, per symbol, the sessions on which that symbol was in scope, each
  decided from information before that session only.

Point-in-time safety comes from the second, not the first. A session may only ever be handed
the symbols whose ``scope_sessions`` contain it, and ``symbols_for`` is the only supported way
to ask. ``membership_violations`` exists so the preflight can prove the run obeyed that.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Any

UNIVERSE_SCHEMA = "b-e0-run-universe-v1"


class UniverseInvalid(ValueError):
    """The artifact cannot be trusted as a source of truth."""


class UniverseChanged(RuntimeError):
    """The artifact no longer hashes to the value a run recorded for it."""


@dataclass(frozen=True, slots=True)
class UniverseMember:
    symbol: str
    scope_sessions: tuple[date, ...]
    fetch_start: date
    fetch_end: date


@dataclass(frozen=True, slots=True)
class RunUniverse:
    """An immutable symbol set for one run window, plus the hash that pins it."""

    path: Path
    sha256: str
    scope_start: date
    scope_end: date
    sessions: tuple[date, ...]
    members: Mapping[str, UniverseMember]
    exclusions: Mapping[str, str]
    built_from: Mapping[str, Any]

    @property
    def symbol_count(self) -> int:
        """Derived by reading the artifact. No count is ever taken from a document."""
        return len(self.members)

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted(self.members))

    def symbols_for(self, session: date) -> tuple[str, ...]:
        """The only sanctioned way to ask what may trade on a session."""
        return tuple(symbol for symbol in sorted(self.members)
                     if session in self.members[symbol].scope_sessions)

    def membership_violations(self, session: date, symbols: Iterable[str]) -> tuple[str, ...]:
        """Symbols offered to a session that the session's scope set does not contain.

        The preflight calls this on whatever the run is about to replay. A non-empty answer
        means the forward-looking union leaked into a session, which is the exact failure the
        per-session re-filter exists to prevent.
        """
        allowed = set(self.symbols_for(session))
        return tuple(sorted(symbol for symbol in symbols if symbol not in allowed))

    def required_pairs(self) -> tuple[tuple[str, date], ...]:
        """Every (symbol, session) the run needs bars for, warmup excluded."""
        return tuple((symbol, session) for symbol in sorted(self.members)
                     for session in self.members[symbol].scope_sessions)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_universe(path: Path, *, expected_sha256: str | None = None) -> RunUniverse:
    """Read the artifact, refusing anything a run could quietly misread.

    ``expected_sha256`` is how a rerun proves it used the same universe as the run it claims to
    reproduce; a mismatch is a different study, not a warning.
    """
    digest = file_sha256(path)
    if expected_sha256 is not None and digest != expected_sha256:
        raise UniverseChanged(f"universe sha256 {digest} != expected {expected_sha256}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema") != UNIVERSE_SCHEMA:
        raise UniverseInvalid(f"{path} is not a {UNIVERSE_SCHEMA} artifact")

    scope_start, scope_end = _day(raw, "scope_start"), _day(raw, "scope_end")
    if scope_end < scope_start:
        raise UniverseInvalid("scope_end precedes scope_start")
    sessions = tuple(date.fromisoformat(day) for day in raw["sessions"])
    if list(sessions) != sorted(set(sessions)):
        raise UniverseInvalid("sessions must be sorted and unique")
    if sessions and (sessions[0] < scope_start or sessions[-1] > scope_end):
        raise UniverseInvalid("a session falls outside the declared scope window")

    known = set(sessions)
    members: dict[str, UniverseMember] = {}
    for entry in raw["symbols"]:
        symbol = str(entry["symbol"])
        if symbol in members:
            raise UniverseInvalid(f"{symbol} appears twice in the universe")
        scope_sessions = tuple(date.fromisoformat(day) for day in entry["scope_sessions"])
        if not scope_sessions:
            raise UniverseInvalid(f"{symbol} has no scope session and must not be a member")
        if list(scope_sessions) != sorted(set(scope_sessions)):
            raise UniverseInvalid(f"{symbol} scope sessions must be sorted and unique")
        outside = [day for day in scope_sessions if day not in known]
        if outside:
            raise UniverseInvalid(f"{symbol} is in scope on {outside[0]}, which is not a session")
        members[symbol] = UniverseMember(
            symbol=symbol, scope_sessions=scope_sessions,
            fetch_start=date.fromisoformat(entry["fetch_start"]),
            fetch_end=date.fromisoformat(entry["fetch_end"]))
        member = members[symbol]
        if member.fetch_start > scope_sessions[0] or member.fetch_end < scope_sessions[-1]:
            raise UniverseInvalid(f"{symbol} fetch range does not cover its scope sessions")

    exclusions = {str(item["symbol"]): str(item["reason"])
                  for item in raw.get("exclusions", ())}
    overlap = sorted(set(exclusions) & set(members))
    if overlap:
        raise UniverseInvalid(f"{overlap} are both members and exclusions")
    if not members:
        raise UniverseInvalid("a universe with no member cannot gate a run")

    return RunUniverse(path=path, sha256=digest, scope_start=scope_start, scope_end=scope_end,
                       sessions=sessions, members=members, exclusions=exclusions,
                       built_from=raw.get("built_from", {}))


def _day(raw: Mapping[str, Any], key: str) -> date:
    return date.fromisoformat(str(raw[key]))


def write_universe(path: Path, *, scope_start: date, scope_end: date, sessions: Sequence[date],
                   members: Sequence[UniverseMember], exclusions: Mapping[str, str],
                   built_from: Mapping[str, Any]) -> RunUniverse:
    """Write the artifact once, deterministically, and hand back the loaded, hashed result.

    Deterministic bytes matter: two builds from the same inputs must produce the same sha256,
    or the identity that carries the hash would change for no reason. Sorting everything and
    writing with fixed separators is what makes that true.
    """
    if path.exists():
        raise UniverseInvalid(
            f"{path} already exists. A universe an authoritative run may have referenced is "
            "never edited; write a correction to a new path.")
    body = {
        "schema": UNIVERSE_SCHEMA,
        "scope_start": scope_start.isoformat(),
        "scope_end": scope_end.isoformat(),
        "sessions": [day.isoformat() for day in sorted(sessions)],
        "symbols": [{"symbol": member.symbol,
                     "scope_sessions": [day.isoformat() for day in member.scope_sessions],
                     "fetch_start": member.fetch_start.isoformat(),
                     "fetch_end": member.fetch_end.isoformat()}
                    for member in sorted(members, key=lambda item: item.symbol)],
        "exclusions": [{"symbol": symbol, "reason": exclusions[symbol]}
                       for symbol in sorted(exclusions)],
        "built_from": dict(built_from),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=1, sort_keys=False, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return load_universe(path)
