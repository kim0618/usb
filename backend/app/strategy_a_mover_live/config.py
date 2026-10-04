"""The live pipeline's own switch and its named refusals. Default OFF, no silent fallback.

The flag is read from the environment here rather than added to ``app.core.config.Settings``
for one reason that is worth stating: this stage must not modify a file another session holds
open, and ``Settings`` is one. The reading is the same shape pydantic-settings would give it -
one name, false unless explicitly true - and ``declaration()`` puts the resolved value in the
run record, so a persisted run says whether the flag was on when it was made.

``Refusal`` is the whole vocabulary of not running. A live source that fails produces one of
these and stops: no legacy scanner is substituted, no GPT call is made and no candidate is
injected. A quiet fallback onto the legacy trade-value scanner would attribute one morning's
entries to a scanner that never saw that morning.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import os

#: The one flag. Unset, empty or anything but a true word means off.
ENV_FLAG = "A_MOVER_LIVE_ENABLED"
TRUE_WORDS = frozenset({"1", "true", "yes", "on"})


class Refusal(StrEnum):
    """Why the live pipeline produced nothing. Every value is recorded, never absorbed."""

    #: The flag is off. The only non-failure refusal.
    DISABLED = "DISABLED"
    #: The flag is on but the stage did not reach the scan at all.
    SCANNER_NOT_RUN = "SCANNER_NOT_RUN"
    #: The scan was reached and its inputs could not be assembled from the live source.
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    #: The research parent's checksums no longer reproduce, so the rules are not the frozen ones.
    CONTRACT_DRIFT = "CONTRACT_DRIFT"
    #: The scan ran and admitted nobody. Not a failure; the handoff is simply empty.
    NO_CANDIDATES = "NO_CANDIDATES"


#: Refusals that mean a GPT call must not be made. ``NO_CANDIDATES`` is here too: an empty
#: handoff renders no prompt, which is section N's "0 candidate -> 0 GPT call".
NO_GPT_CALL = frozenset(Refusal)


def enabled(environ: dict[str, str] | None = None) -> bool:
    raw = (environ if environ is not None else os.environ).get(ENV_FLAG, "")
    return raw.strip().lower() in TRUE_WORDS


@dataclass(frozen=True)
class LiveSwitch:
    """The resolved switch, as plain data for a run record."""

    flag: str
    value: bool

    @classmethod
    def current(cls, environ: dict[str, str] | None = None) -> "LiveSwitch":
        return cls(ENV_FLAG, enabled(environ))

    def declaration(self) -> dict[str, object]:
        return {"flag": self.flag, "enabled": self.value,
                "default": False,
                "on_failure": "named refusal; no legacy scanner fallback"}


def require_enabled(environ: dict[str, str] | None = None) -> None:
    """Raise the one refusal a caller must not interpret."""
    if not enabled(environ):
        raise LivePipelineDisabled(Refusal.DISABLED)


class LivePipelineDisabled(RuntimeError):
    def __init__(self, refusal: Refusal) -> None:
        super().__init__(str(refusal))
        self.refusal = refusal
