"""Which leverage steps this account may actually select, learned from Binance's own answers.

The bracket table is not the answer. `GET /fapi/v1/leverageBracket` describes the *symbol*: for
BTCUSDT it reports `initialLeverage` 150 in the first bracket on an account that Binance will
nevertheless refuse at 50x, because a newly registered Futures account is capped at 20x for its
first thirty days. The refusal observed on the real account on 2026-10-01 was

    POST /fapi/v1/leverage  ->  400  code -4300
    "You can start trading with more than 20x leverage by 2026-10-29 01:34 (UTC), because
     higher leverage is available 30 days after Futures account registration."

There is no read endpoint in this package's registry that publishes that restriction, so the
only honest source for it is the refusal itself. This module remembers one: the threshold it
names, the moment it lifts, and nothing else. From then on the ladder can grey the step out and
say why instead of offering a button whose press is a write that Binance will reject.

Three properties are deliberate:

* **Nothing is hardcoded.** No step is assumed available and none is assumed forbidden. A fresh
  process knows nothing, offers every step the bracket table reaches, and learns from the first
  refusal. A cap written into this file would be exactly the "symbol max confused with account
  capability" mistake in the other direction.
* **It expires by itself.** The restriction carries the instant Binance said it lifts, and
  `restriction_for` stops reporting it after that. 50x and 100x come back without a deploy.
* **A success overrides it.** If Binance accepts a leverage a stored restriction said it would
  refuse, the restriction was wrong or stale and is dropped.

The restriction is also recovered from the LIVE audit mirror at startup, because the refusal
above was already recorded there: making the operator press 50x once more after every deploy,
only to send a write Binance will reject again, is a worse screen than reading the answer the
file already holds. `restriction_from_mirror` is the whole of that path and it is a read of a
local file - no Binance call of any kind, and in particular no write.

That is not the mirror being replayed into a snapshot, which `live.mirror` says never happens
and which still never happens. Balance, position, fills and funding are re-read from Binance on
every restart as before. What is recovered here is not account state: it is a sentence Binance
addressed to this process, whose truth is bounded by a deadline Binance itself supplied, and
which no amount of re-reading the account would reveal.

Nothing here sends anything. It is told what happened; it never asks.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Sequence

#: Binance's code for "this account may not use leverage this high yet". Kept as a set because
#: the restriction family is likely to grow and the handling is identical for all of them.
RESTRICTION_CODES = frozenset({-4300})

#: The code this package reports to the screen for the above. Distinct from the bracket-table
#: refusal (`UNSUPPORTED_LEVERAGE`), because the remedy is different: one is waiting, the other
#: is choosing a different number.
ACCOUNT_LEVERAGE_NOT_YET_AVAILABLE = "ACCOUNT_LEVERAGE_NOT_YET_AVAILABLE"

#: "...more than 20x leverage..." - the threshold the account is currently held to.
_THRESHOLD = re.compile(r"more than (\d{1,3})\s*x", re.IGNORECASE)
#: "...by 2026-10-29 01:34 (UTC)..." - when it lifts. Both are read out of Binance's own
#: sentence; neither is inferred, and a sentence that carries neither yields a restriction that
#: covers only the step that was refused and never expires on its own.
_UNTIL = re.compile(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})\s*\(UTC\)")


def _now_ms() -> int:
    return int(time.time() * 1000)


def parse_threshold(message: str) -> int | None:
    found = _THRESHOLD.search(message or "")
    return int(found.group(1)) if found else None


def parse_until_ms(message: str) -> int | None:
    found = _UNTIL.search(message or "")
    if not found:
        return None
    try:
        stamp = datetime.strptime(f"{found.group(1)} {found.group(2)}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return int(stamp.replace(tzinfo=timezone.utc).timestamp() * 1000)


#: How a restriction came to be known. Both are Binance's own words; they differ in whether this
#: process heard them live or read them back out of its own audit file.
FROM_EXCHANGE = "EXCHANGE_REFUSAL"
FROM_MIRROR = "AUDIT_MIRROR_BOOTSTRAP"


@dataclass(frozen=True)
class Restriction:
    """One learned limit: every leverage strictly above `above` is refused until `until_ms`."""
    above: int
    code: int | None
    until_ms: int | None
    observed_at_ms: int
    #: Binance's own sentence, kept for the audit trail. Never rendered: the screen shows
    #: `message` below, which is this package's Korean.
    exchange_message: str
    #: `FROM_EXCHANGE` or `FROM_MIRROR`. Carried so an operator reading the API or the stored
    #: file can tell a refusal this process received from one it recovered at startup.
    source: str = FROM_EXCHANGE

    @property
    def until_utc(self) -> str | None:
        if self.until_ms is None:
            return None
        return datetime.fromtimestamp(self.until_ms / 1000, tz=timezone.utc) \
            .strftime("%Y-%m-%d %H:%M UTC")

    def expired(self, now_ms: int) -> bool:
        return self.until_ms is not None and now_ms >= self.until_ms

    def message(self, leverage: int) -> str:
        """What the operator is told. Short, Korean, and about their account - no endpoint, no
        error code, no English passthrough."""
        head = f"현재 계정에서 {leverage}x를 사용할 수 없습니다."
        detail = f" {self.above}x를 넘는 레버리지는"
        when = f" {self.until_utc} 이후에 열립니다." if self.until_utc else " 아직 열려 있지 않습니다."
        return head + detail + when

    def view(self) -> dict[str, Any]:
        return {"above": self.above, "code": self.code, "until_ms": self.until_ms,
                "until_utc": self.until_utc, "observed_at_ms": self.observed_at_ms,
                "source": self.source}

    def store(self) -> dict[str, Any]:
        return {**self.view(), "exchange_message": self.exchange_message}


def build_restriction(*, leverage: int, code: int | None, message: str, observed_at_ms: int,
                      source: str = FROM_EXCHANGE) -> Restriction:
    """One reading of one Binance sentence, used by both the live path and the bootstrap.

    Shared on purpose: two readings of the same message would be two chances to disagree about
    which steps are forbidden, and the screen would then depend on whether the process heard the
    refusal or read it back.

    With no threshold in the sentence, only the step that was actually refused is known to be
    refused. Guessing a lower bound would grey out buttons Binance never spoke about.
    """
    threshold = parse_threshold(message)
    above = threshold if threshold is not None else max(0, int(leverage) - 1)
    return Restriction(above=int(above), code=code, until_ms=parse_until_ms(message),
                       observed_at_ms=int(observed_at_ms),
                       exchange_message=str(message or ""), source=source)


#: The only two mirror lines the bootstrap looks at. Everything else in the audit file - orders,
#: snapshots, arm sessions, reconciles - is not read.
REFUSAL_EVENT = "LIVE_LEVERAGE_REFUSED"
RESULT_EVENT = "LIVE_LEVERAGE_RESULT"

#: Why nothing was recovered, so a quiet startup is still explicable.
NO_MIRROR = "NO_MIRROR_FILE"
NO_EVENT = "NO_RESTRICTION_EVENT"
UNREADABLE_UNLOCK = "UNLOCK_TIME_UNREADABLE"
ALREADY_EXPIRED = "RESTRICTION_EXPIRED"
CONTRADICTED = "CONTRADICTED_BY_LATER_SUCCESS"
ALREADY_PERSISTED = "PERSISTED_CAPABILITY_PRESENT"
RECOVERED = "RECOVERED_FROM_AUDIT_MIRROR"


@dataclass(frozen=True)
class BootstrapOutcome:
    """What the startup scan concluded, and from how much of the file."""
    restriction: Restriction | None
    reason: str
    scanned: int = 0
    unreadable: int = 0

    def view(self) -> dict[str, Any]:
        return {"reason": self.reason, "scanned": self.scanned, "unreadable": self.unreadable,
                "restriction": self.restriction.view() if self.restriction else None}


def _event_symbol(event: Mapping[str, Any]) -> str | None:
    """The symbol a mirror line is about, or None when the line does not name one.

    A line written before this field existed does not name one, and at that time this package
    traded a single symbol, so an unnamed line belongs to the configured symbol. A line that
    names a *different* symbol is somebody else's restriction and is skipped.
    """
    named = event.get("symbol")
    if isinstance(named, str) and named:
        return named
    response = event.get("response")
    if isinstance(response, Mapping):
        inner = response.get("symbol")
        if isinstance(inner, str) and inner:
            return inner
    return None


def _leverage_of(event: Mapping[str, Any]) -> int | None:
    for key in ("requested", "leverage"):
        value = event.get(key)
        if isinstance(value, (int, float, str)) and str(value).strip():
            try:
                return int(Decimal(str(value)))
            except (InvalidOperation, ValueError):
                continue
    return None


def restriction_from_mirror(path: Path, *, symbol: str,
                            now_ms: int | None = None) -> BootstrapOutcome:
    """Recover a still-current account restriction from the LIVE audit mirror. Read only.

    The file is streamed a line at a time and only two event types are consulted. The newest
    refusal in the restriction family wins, and it is discarded when a *later* line shows
    Binance accepting a leverage that refusal said it would reject - the limit has been lifted
    since and the file simply has not been told.

    A historical refusal must carry a readable unlock time to be restored, which a live one does
    not. The difference is deliberate: a refusal happening now is authoritative whatever it
    says, while a line of unknown age with no deadline on it could be from any point in the past
    and nothing in the file would say whether it still applies. The 30-day new-account cap this
    was written for always names its own end, so requiring it costs nothing real and removes the
    only way this path could grey out a working button indefinitely.

    No Binance request of any kind is made here, and nothing is written.
    """
    now = _now_ms() if now_ms is None else now_ms
    if not path.exists():
        return BootstrapOutcome(None, NO_MIRROR)

    newest: tuple[int, Restriction] | None = None
    newest_success: tuple[int, int] | None = None
    scanned = unreadable = 0
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                unreadable += 1
                continue
            if not isinstance(event, Mapping):
                unreadable += 1
                continue
            kind = event.get("event_type")
            if kind not in (REFUSAL_EVENT, RESULT_EVENT):
                continue
            named = _event_symbol(event)
            if named is not None and named != symbol:
                continue
            try:
                stamp = int(event.get("ts_ms") or 0)
            except (TypeError, ValueError):
                unreadable += 1
                continue
            scanned += 1
            leverage = _leverage_of(event)
            if kind == RESULT_EVENT:
                # A success. Only its size and when it happened matter.
                if leverage is not None and (newest_success is None or stamp > newest_success[0]):
                    newest_success = (stamp, leverage)
                continue
            # A refusal. Only the restriction family teaches anything; -4028, a held position,
            # a timeout, a 5xx and a transport failure all say nothing about what this account
            # may select, and each of those reaches this file with a different `code`.
            if event.get("code") not in RESTRICTION_CODES or leverage is None:
                continue
            learned = build_restriction(leverage=leverage, code=event.get("code"),
                                        message=str(event.get("message") or ""),
                                        observed_at_ms=stamp, source=FROM_MIRROR)
            if newest is None or stamp > newest[0]:
                newest = (stamp, learned)

    if newest is None:
        return BootstrapOutcome(None, NO_EVENT, scanned, unreadable)
    stamp, learned = newest
    if learned.until_ms is None:
        return BootstrapOutcome(None, UNREADABLE_UNLOCK, scanned, unreadable)
    if learned.expired(now):
        return BootstrapOutcome(None, ALREADY_EXPIRED, scanned, unreadable)
    if (newest_success is not None and newest_success[0] > stamp
            and newest_success[1] > learned.above):
        return BootstrapOutcome(None, CONTRADICTED, scanned, unreadable)
    return BootstrapOutcome(learned, RECOVERED, scanned, unreadable)


class LeverageCapability:
    """The account's learned leverage ceiling, optionally persisted beside the LIVE mirror.

    Persisted so the knowledge outlives a restart: without a file, every deploy would cost the
    operator one more refused click to rediscover the same limit. The file holds no credential -
    a threshold, an instant, and Binance's message.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._restrictions: list[Restriction] = []
        self._load()

    # ------------------------------------------------------------------ storage

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text())
            rows = raw.get("restrictions") if isinstance(raw, dict) else None
            for row in rows or []:
                self._restrictions.append(Restriction(
                    above=int(row["above"]), code=row.get("code"),
                    until_ms=row.get("until_ms"),
                    observed_at_ms=int(row.get("observed_at_ms") or 0),
                    exchange_message=str(row.get("exchange_message") or "")))
        except Exception:
            # A damaged file must not make the panel unusable, and it must not be read as
            # "everything is allowed" either: the list stays empty and the next refusal relearns
            # the limit, which is the same state a fresh process is in.
            self._restrictions = []

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps({"restrictions": [item.store() for item in
                                                          self._restrictions]},
                                        ensure_ascii=False, sort_keys=True))
        os.replace(temporary, self.path)

    # ------------------------------------------------------------------ reads

    def restriction_for(self, leverage: int, *,
                        now_ms: int | None = None) -> Restriction | None:
        """The tightest live restriction that forbids `leverage`, or None."""
        now = _now_ms() if now_ms is None else now_ms
        live = [item for item in self._restrictions
                if not item.expired(now) and leverage > item.above]
        if not live:
            return None
        return min(live, key=lambda item: item.above)

    def unavailable(self, options: Sequence[int], *,
                    now_ms: int | None = None) -> dict[str, dict[str, Any]]:
        """Which of the offered steps are currently refused, keyed as the UI reads them."""
        now = _now_ms() if now_ms is None else now_ms
        out: dict[str, dict[str, Any]] = {}
        for value in options:
            found = self.restriction_for(int(value), now_ms=now)
            if found is not None:
                out[str(int(value))] = {**found.view(),
                                        "code_name": ACCOUNT_LEVERAGE_NOT_YET_AVAILABLE,
                                        "message": found.message(int(value))}
        return out

    def view(self, options: Sequence[int] = (), *, now_ms: int | None = None) -> dict[str, Any]:
        now = _now_ms() if now_ms is None else now_ms
        return {"unavailable": self.unavailable(options, now_ms=now),
                "restrictions": [item.view() for item in self._restrictions
                                 if not item.expired(now)],
                "authority": "learned from binance POST /fapi/v1/leverage refusals"}

    # ------------------------------------------------------------------ writes

    def adopt(self, learned: Restriction, *, now_ms: int | None = None) -> Restriction:
        """Store a restriction built elsewhere, replacing any for the same threshold."""
        now = _now_ms() if now_ms is None else now_ms
        self._restrictions = [item for item in self._restrictions
                              if item.above != learned.above and not item.expired(now)]
        self._restrictions.append(learned)
        self._save()
        return learned

    def note_refusal(self, *, leverage: int, code: int | None, message: str,
                     now_ms: int | None = None) -> Restriction | None:
        """Record a Binance refusal. Returns the restriction when one was learned.

        Only the restriction family is learned from. Every other refusal - a bad number, an open
        position, an outage - says nothing about what this account may select and must not
        silently narrow the ladder.
        """
        if code not in RESTRICTION_CODES:
            return None
        now = _now_ms() if now_ms is None else now_ms
        return self.adopt(build_restriction(leverage=leverage, code=code, message=message,
                                            observed_at_ms=now), now_ms=now)

    def bootstrap_from_mirror(self, mirror_path: Path, *, symbol: str,
                              now_ms: int | None = None) -> BootstrapOutcome:
        """Recover a known restriction at startup, when nothing is stored yet.

        The stored file wins. If it already holds a live restriction, the audit scan is not even
        run: the persisted record is this process's own conclusion, written after the refusal
        and kept up to date by every success since, while the mirror is the raw material it was
        drawn from. Reading the raw material again could only reach the same answer or an older
        one.

        Read only, and no Binance request of any kind.
        """
        now = _now_ms() if now_ms is None else now_ms
        if any(not item.expired(now) for item in self._restrictions):
            return BootstrapOutcome(None, ALREADY_PERSISTED)
        outcome = restriction_from_mirror(mirror_path, symbol=symbol, now_ms=now)
        if outcome.restriction is not None:
            self.adopt(outcome.restriction, now_ms=now)
        return outcome

    def note_success(self, leverage: int, *, now_ms: int | None = None) -> None:
        """Binance accepted this leverage, so nothing may claim it is forbidden."""
        now = _now_ms() if now_ms is None else now_ms
        kept = [item for item in self._restrictions
                if not item.expired(now) and int(leverage) <= item.above]
        if len(kept) != len(self._restrictions):
            self._restrictions = kept
            self._save()


__all__ = ["ACCOUNT_LEVERAGE_NOT_YET_AVAILABLE", "ALREADY_EXPIRED", "ALREADY_PERSISTED",
           "BootstrapOutcome", "CONTRADICTED", "FROM_EXCHANGE", "FROM_MIRROR",
           "LeverageCapability", "NO_EVENT", "NO_MIRROR", "RECOVERED", "REFUSAL_EVENT",
           "RESTRICTION_CODES", "RESULT_EVENT", "Restriction", "UNREADABLE_UNLOCK",
           "build_restriction", "parse_threshold", "parse_until_ms",
           "restriction_from_mirror"]
