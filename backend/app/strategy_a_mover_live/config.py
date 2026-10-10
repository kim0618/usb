"""The live pipeline's two switches and its named refusals. No silent fallback, either way.

There are two switches because the pipeline has two independent halves, and the second one
exists so that recording a scan is never the same decision as trading it:

``A_MOVER_LIVE_ENABLED``
    whether the live mover scan runs at all - the shared A/E premarket acquisition, the 09:15
    ET cut, the ranking and the persisted ``a_mover_live_v1`` run. This is the **live market
    authority**: a recorded observation of session D's premarket.

``A_MOVER_LIVE_ENTRY_AUTHORITY``
    whether the entry runtime and the review UI resolve *candidates* from those live runs.
    This is the **human research authority** binding. It **defaults to the scan flag**: with
    the live scan on, the live runs are the candidate authority, and an operator who wants the
    pre-live trade-value source instead has to say so.

That default is a correction. The flag was introduced defaulting to OFF because binding entry
to a live run meant binding it to the *same* session: a live run's TOP8 does not exist until
09:27 ET and the entry deadline is 10:30 ET, so the GPT research and the human APPROVE were
squeezed into 22:27-23:30 KST. ``paper_adapter`` no longer makes that mapping - a run of
session D is consumed on ``next_trading_day(D)``, the same rule the trade-value scanner has
always had - so live entry authority now costs the operator nothing, and defaulting it off
would only mean that deploying the fix changes nothing until someone remembers an env var.

Declining is still possible and still explicit: ``false``/``0``/``no``/``off`` turns the
authority off and leaves the pre-live source bound to entry. A value that is neither a true
word nor a false word is not guessed at - :func:`entry_authority_unparsed` names it and the
authority stays off, which is the side that trades less.

The second flag never turns the first one on. Asking for the live entry authority without the
live scan is a configuration error, not a fallback, and :func:`entry_authority` reports it as
``False`` with :func:`entry_authority_misconfigured` naming it, so nothing silently trades the
pre-live scanner's approvals while claiming the live source.

Both flags are read from the environment here rather than added to ``app.core.config.Settings``
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

#: Whether the live mover scan runs. Unset, empty or anything but a true word means off.
ENV_FLAG = "A_MOVER_LIVE_ENABLED"
#: Whether entry and the review UI resolve candidates from the live runs. Tri-state: unset
#: follows :data:`ENV_FLAG`, a false word declines, anything else is reported unparsed.
ENV_ENTRY_AUTHORITY_FLAG = "A_MOVER_LIVE_ENTRY_AUTHORITY"
TRUE_WORDS = frozenset({"1", "true", "yes", "on"})
#: Only for the tri-state flag. ``ENV_FLAG`` keeps its two-valued reading: a scan that is not
#: explicitly asked for does not run, and there is no default for it to follow.
FALSE_WORDS = frozenset({"0", "false", "no", "off"})


class Refusal(StrEnum):
    """Why the live pipeline produced nothing. Every value is recorded, never absorbed."""

    #: The flag is off. The only non-failure refusal.
    DISABLED = "DISABLED"
    #: The flag is on but the stage did not reach the scan at all.
    SCANNER_NOT_RUN = "SCANNER_NOT_RUN"
    #: The attach seam itself failed, so A never entered E's collection cycle. A's own
    #: failure, isolated: E's worker keeps running and records this instead of stopping.
    ATTACH_FAILED = "ATTACH_FAILED"
    #: The scan was reached and its inputs could not be assembled from the live source.
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    #: The research parent's checksums no longer reproduce, so the rules are not the frozen ones.
    CONTRACT_DRIFT = "CONTRACT_DRIFT"
    #: The scan ran and admitted nobody. Not a failure; the handoff is simply empty.
    NO_CANDIDATES = "NO_CANDIDATES"


#: Refusals that mean a GPT call must not be made. ``NO_CANDIDATES`` is here too: an empty
#: handoff renders no prompt, which is section N's "0 candidate -> 0 GPT call".
NO_GPT_CALL = frozenset(Refusal)


def _flag(name: str, environ: dict[str, str] | None = None) -> bool:
    raw = (environ if environ is not None else os.environ).get(name, "")
    return raw.strip().lower() in TRUE_WORDS


def enabled(environ: dict[str, str] | None = None) -> bool:
    """Whether the live mover scan runs. The scan half; reads nothing about entry."""
    return _flag(ENV_FLAG, environ)


def _tristate(name: str, environ: dict[str, str] | None = None) -> bool | None:
    """``True``/``False`` for a recognised word, ``None`` for unset or empty."""
    raw = (environ if environ is not None else os.environ).get(name, "").strip().lower()
    if not raw:
        return None
    if raw in TRUE_WORDS:
        return True
    if raw in FALSE_WORDS:
        return False
    return None


def entry_authority_asked(environ: dict[str, str] | None = None) -> bool | None:
    """What the operator asked for, before the scan requirement.

    ``None`` means the value said nothing this function can read, which is either unset/empty
    or an unrecognised word; :func:`entry_authority_unparsed` is what tells the two apart, and
    :func:`entry_authority` consults it first so an unrecognised word never inherits the
    "unset follows the scan flag" default.
    """
    return _tristate(ENV_ENTRY_AUTHORITY_FLAG, environ)


def entry_authority_unparsed(environ: dict[str, str] | None = None) -> str | None:
    """A value that is neither a true word nor a false word, returned so it can be named."""
    raw = (environ if environ is not None else os.environ).get(
        ENV_ENTRY_AUTHORITY_FLAG, "").strip()
    if not raw or raw.lower() in TRUE_WORDS or raw.lower() in FALSE_WORDS:
        return None
    return raw


def entry_authority(environ: dict[str, str] | None = None) -> bool:
    """Whether entry and the review UI resolve candidates from the live runs.

    Unset, this follows the scan flag: a recorded live scan whose candidates nothing consumes
    is not a configuration anyone asks for on purpose, and the date rule that once made the
    two decisions different no longer does.

    Requires the scan either way: a live entry authority with no live run would resolve no
    candidates at all, every session, and the honest reading of that configuration is that it
    was not meant. An unparsed value declines rather than guesses.
    """
    if entry_authority_unparsed(environ) is not None:
        return False
    asked = entry_authority_asked(environ)
    return enabled(environ) if asked is None else (asked and enabled(environ))


def entry_authority_misconfigured(environ: dict[str, str] | None = None) -> bool:
    """The live entry authority was asked for without the live scan. Named, never absorbed."""
    return entry_authority_asked(environ) is True and not enabled(environ)


@dataclass(frozen=True)
class LiveSwitch:
    """The resolved switch, as plain data for a run record."""

    flag: str
    value: bool
    entry_authority_flag: str = ENV_ENTRY_AUTHORITY_FLAG
    entry_authority: bool = False

    @classmethod
    def current(cls, environ: dict[str, str] | None = None) -> "LiveSwitch":
        return cls(ENV_FLAG, enabled(environ), ENV_ENTRY_AUTHORITY_FLAG,
                   entry_authority(environ))

    def declaration(self) -> dict[str, object]:
        return {"flag": self.flag, "enabled": self.value,
                "entry_authority_flag": self.entry_authority_flag,
                "entry_authority": self.entry_authority,
                "scan_default": False,
                "entry_authority_default": "follows " + self.flag,
                "entry_session_rule": "next_trading_day(run.trading_date)",
                "on_failure": "named refusal; no cross-source scanner fallback"}


def require_enabled(environ: dict[str, str] | None = None) -> None:
    """Raise the one refusal a caller must not interpret."""
    if not enabled(environ):
        raise LivePipelineDisabled(Refusal.DISABLED)


class LivePipelineDisabled(RuntimeError):
    def __init__(self, refusal: Refusal) -> None:
        super().__init__(str(refusal))
        self.refusal = refusal
