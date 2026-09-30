"""Manual LIVE arming: a deliberate act with a deadline, held only in memory.

The problem this solves is an operational one. Live order routing sits behind two environment
variables, which is right for a laptop running a one-off validation and wrong for a server: an
`Environment=` line in a unit file is set once and is then true forever, so every restart comes
back armed and the gate has quietly become a permanent setting. Asking the operator to `export`
a variable before each session instead is safe and unusable.

So the two gates are re-cut along the line that actually matters:

    BINANCE_LIVE_TRADING_ENABLED   capability - *may* this deployment ever send an order
    ArmSession                     intent     - is the operator sending orders *right now*

The capability flag stays in the environment where an operator has to go and change a file to
move it. The intent lives here, in a process-memory session that:

* starts **disarmed**, always, because there is nowhere for it to be persisted from;
* is raised only by an explicit request carrying the confirmation phrase, so no page load, no
  prefetch and no retried GET can arm an account;
* expires on its own after `ttl_s`, so walking away from the screen disarms it;
* dies with the process, so a restart - crash, deploy, reboot - is a disarm.

`BINANCE_LIVE_CLIENT_ARMED` is still honoured and still means what it meant: a machine that sets
it is armed from boot without a session. That is the local validation path and it is deliberately
left alone. A deployment that wants the session semantics simply does not set it.

The session is not a third gate laid on top of the second. It *is* the second gate: arming writes
through to `BinanceFuturesClient.trading_enabled`, which is the flag `rest.call` checks before a
TRADE request is constructed. There is one place that decides, and it is the same place as
before.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

#: Long enough for a session of manual trading, short enough that an abandoned screen is not
#: left armed. The operator re-arms with one click; the cost of it being too short is an extra
#: click, and the cost of it being too long is an account nobody is watching.
DEFAULT_TTL_S = 900

#: Required verbatim in the arm request. A typed phrase rather than a boolean because the whole
#: point of this call is that it cannot be arrived at by accident.
CONFIRMATION = "ARM LIVE TRADING"

ARMED_BY_ENV = "ENV"
ARMED_BY_SESSION = "SESSION"

DISARM_EXPIRED = "EXPIRED"
DISARM_MANUAL = "MANUAL"
DISARM_BOOT = "BOOT"


class ArmRefused(RuntimeError):
    """An arm request that was not honoured, with the reason the operator needs to see."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ArmSession:
    """One account's manual arm state. Not thread-safe by lock; see `_sync`.

    Every read goes through `_sync`, so an expired session is observed as disarmed by whoever
    looks next rather than by a timer. A timer would have to run on a thread, would have to be
    cancelled on shutdown, and would give the same answer later; expiry checked on read cannot
    drift from what the gate reports.
    """
    #: The client whose `trading_enabled` flag this session owns. Written through on every
    #: transition so there is exactly one flag the order path consults.
    client: Any
    #: True when `BINANCE_LIVE_CLIENT_ARMED` is set. Then this deployment is armed from boot and
    #: the session neither adds nor removes anything - it reports `ENV` and stays out of the way.
    env_armed: bool = False
    ttl_s: int = DEFAULT_TTL_S
    armed_at_ms: int | None = None
    expires_at_ms: int | None = None
    arm_count: int = 0
    disarm_count: int = 0
    last_disarm_reason: str | None = DISARM_BOOT
    last_disarm_ms: int | None = None
    #: Free-text label from the arm request, for the audit trail only.
    operator_note: str | None = None
    _history: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Boot is a disarm, and it is written down as one. A deployment reading its own audit
        # trail should see the restart, not an unexplained gap.
        self._write_through()

    # ------------------------------------------------------------------ internals

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    def _sync(self) -> None:
        """Expire the session if its deadline has passed, then make the client agree."""
        if self.expires_at_ms is not None and self._now_ms() >= self.expires_at_ms:
            self._clear(DISARM_EXPIRED)
        self._write_through()

    def _clear(self, reason: str) -> None:
        if self.expires_at_ms is not None or self.armed_at_ms is not None:
            self.disarm_count += 1
            self._history.append({"at_ms": self._now_ms(), "action": "DISARM", "reason": reason})
        self.armed_at_ms = None
        self.expires_at_ms = None
        self.operator_note = None
        self.last_disarm_reason = reason
        self.last_disarm_ms = self._now_ms()

    def _write_through(self) -> None:
        """The session's state *is* the client's flag. Nothing else may set it."""
        self.client.trading_enabled = self.env_armed or self.expires_at_ms is not None

    # ------------------------------------------------------------------ state

    @property
    def armed(self) -> bool:
        self._sync()
        return bool(self.env_armed or self.expires_at_ms is not None)

    @property
    def armed_by(self) -> str | None:
        if not self.armed:
            return None
        return ARMED_BY_ENV if self.env_armed else ARMED_BY_SESSION

    def remaining_s(self) -> int | None:
        self._sync()
        if self.env_armed or self.expires_at_ms is None:
            return None
        return max(0, (self.expires_at_ms - self._now_ms()) // 1000)

    # ------------------------------------------------------------------ transitions

    def arm(self, *, confirmation: str, note: str | None = None,
            ttl_s: int | None = None) -> dict[str, Any]:
        """Arm for a bounded window. Refuses anything but the exact confirmation phrase."""
        if self.env_armed:
            raise ArmRefused(
                "ALREADY_ARMED_BY_ENV",
                "이 프로세스는 BINANCE_LIVE_CLIENT_ARMED로 이미 무장돼 있어 세션 무장이 필요 없습니다.")
        if confirmation != CONFIRMATION:
            raise ArmRefused("CONFIRMATION_REQUIRED",
                             f"확인 문구가 정확히 일치해야 합니다: {CONFIRMATION!r}")
        window = self.ttl_s if ttl_s is None else int(ttl_s)
        if not 0 < window <= self.ttl_s:
            raise ArmRefused("TTL_OUT_OF_RANGE",
                             f"무장 시간은 1초 이상 {self.ttl_s}초 이하여야 합니다.")
        now = self._now_ms()
        self.armed_at_ms = now
        self.expires_at_ms = now + window * 1000
        self.operator_note = (note or None)
        self.arm_count += 1
        self._history.append({"at_ms": now, "action": "ARM", "ttl_s": window, "note": note})
        self._write_through()
        return self.view()

    def disarm(self, reason: str = DISARM_MANUAL) -> dict[str, Any]:
        self._clear(reason)
        self._write_through()
        return self.view()

    # ------------------------------------------------------------------ view

    def view(self) -> dict[str, Any]:
        armed = self.armed  # syncs
        return {
            "armed": armed,
            "armed_by": self.armed_by,
            "armed_at_ms": self.armed_at_ms,
            "expires_at_ms": self.expires_at_ms,
            "remaining_s": self.remaining_s(),
            "ttl_s": self.ttl_s,
            "arm_count": self.arm_count,
            "disarm_count": self.disarm_count,
            "last_disarm_reason": self.last_disarm_reason,
            "last_disarm_ms": self.last_disarm_ms,
            "operator_note": self.operator_note,
            "confirmation_phrase": CONFIRMATION,
            "env_armed": self.env_armed,
            "role": ("MANUAL_ONLY: 서버 부팅은 항상 해제 상태이고, 무장은 사용자가 명시적으로 "
                     "요청할 때만 제한 시간 동안 열린다. AUTO는 이 경로에 접근할 수 없다."),
        }


__all__ = ["ArmSession", "ArmRefused", "CONFIRMATION", "DEFAULT_TTL_S", "ARMED_BY_ENV",
           "ARMED_BY_SESSION", "DISARM_EXPIRED", "DISARM_MANUAL", "DISARM_BOOT"]
