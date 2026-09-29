"""Operating state machine (CRYPTO_TRADING_STATE_MACHINE_V1.md).

The enum and the transition table are defined here in full. AUTO's entry and exit decision
logic is deliberately absent: D3 implements no strategy, so `AUTO_ON` is refused by
`TerminalState.transition` with `AUTO_NOT_READY` and the UI shows the control disabled.
Defining the states without the decisions is what keeps "AUTO was switched off but a position
is still open" from being an expressible state later.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MANUAL = "MANUAL"
AUTO = "AUTO"
AUTO_STOPPING = "AUTO_STOPPING"
EMERGENCY = "EMERGENCY"
MODES = (MANUAL, AUTO, AUTO_STOPPING, EMERGENCY)

AUTO_ON = "AUTO_ON"
AUTO_OFF = "AUTO_OFF"
EMERGENCY_ON = "EMERGENCY_ON"
EMERGENCY_RELEASE = "EMERGENCY_RELEASE"
POSITION_FLAT = "POSITION_FLAT"

# from -> action -> to. Anything absent is not a transition.
TRANSITIONS: dict[str, dict[str, str]] = {
    MANUAL: {AUTO_ON: AUTO, EMERGENCY_ON: EMERGENCY},
    AUTO: {AUTO_OFF: AUTO_STOPPING, EMERGENCY_ON: EMERGENCY},
    AUTO_STOPPING: {AUTO_ON: AUTO, POSITION_FLAT: MANUAL, EMERGENCY_ON: EMERGENCY},
    EMERGENCY: {EMERGENCY_RELEASE: MANUAL},
}

# D3 refuses these actions outright, with the reason the UI shows.
NOT_READY = {AUTO_ON: "AUTO_NOT_READY"}


class TransitionRejected(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class TerminalState:
    mode: str = MANUAL

    def can_open_new_position(self) -> bool:
        """Contract I1: neither AUTO_STOPPING nor EMERGENCY may create a new entry."""
        return self.mode in (MANUAL, AUTO)

    def blocks_new_entry_reason(self) -> str | None:
        if self.mode == AUTO_STOPPING:
            return "NEW_ENTRY_BLOCKED_AUTO_STOPPING"
        if self.mode == EMERGENCY:
            return "NEW_ENTRY_BLOCKED_EMERGENCY"
        return None

    def check(self, action: str, *, confirmed: bool = False) -> str:
        """Validate a transition without performing it, so an API can answer before the tape
        records anything. Returns the mode the action would land in."""
        if action in NOT_READY:
            raise TransitionRejected(NOT_READY[action], f"{action} is not available in D3")
        if action == EMERGENCY_ON and not confirmed:
            raise TransitionRejected("CONFIRMATION_REQUIRED", "EMERGENCY requires explicit confirmation")
        allowed = TRANSITIONS.get(self.mode, {})
        if action not in allowed:
            raise TransitionRejected("TRANSITION_NOT_ALLOWED", f"{action} is not allowed from {self.mode}")
        return allowed[action]

    def transition(self, action: str, *, confirmed: bool = False) -> str:
        if action in NOT_READY:
            raise TransitionRejected(NOT_READY[action], f"{action} is not available in D3")
        if action == EMERGENCY_ON and not confirmed:
            # Confirmation gate inherited from the equity kill switch.
            raise TransitionRejected("CONFIRMATION_REQUIRED", "EMERGENCY requires explicit confirmation")
        allowed = TRANSITIONS.get(self.mode, {})
        if action not in allowed:
            raise TransitionRejected("TRANSITION_NOT_ALLOWED", f"{action} is not allowed from {self.mode}")
        previous = self.mode
        self.mode = allowed[action]
        return previous

    def view(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "modes": list(MODES),
            "can_open_new_position": self.can_open_new_position(),
            "new_entry_blocked_reason": self.blocks_new_entry_reason(),
            "auto_available": False,
            "auto_unavailable_reason": "AUTO_NOT_READY",
        }
