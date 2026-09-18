"""What a historical run is allowed to evaluate, and where that permission came from.

Five permissions decide a historical run, and the tape contains none of them. Which
symbol was a candidate on a date, whether a human approved it, how it may be carried
overnight, which trailing profile the stop reads, and at what rank it competed are all
Scanner/GPT/Human records, not prices. This module is the vocabulary for stating which
of those a run actually had and which it declared.

The rule the whole layer exists to enforce is one line: an authority that is not on
record stays ``UNKNOWN``. It is never quietly promoted to eligible, APPROVE, HIGH, or
NORMAL. A caller that wants an assumed value has to name the mode that produces it, and
that mode is carried in every result so a performance figure can never be read without
the permission it was produced under.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from enum import StrEnum

from app.backtest.authority.errors import AuthorityModeInvalid
from app.market.symbols import normalize_symbol
from app.strategy.lifecycle import OvernightSuitability, TrailingProfile

#: Stamped on every record, every resolved context, and every result that used one.
#: Two runs whose authority version differs are not the same backtest run.
AUTHORITY_VERSION = "historical-authority-v1"


class AuthoritySource(StrEnum):
    """Where one permission came from. This is provenance, never a quality score."""

    #: Read from a durable record written at the time by the Production path.
    RECORDED = "RECORDED"
    #: Recomputed from past data by the same contract Production uses, deterministically.
    #: No V1 path produces this: the historical Scanner that would is a later stage.
    RECONSTRUCTED = "RECONSTRUCTED"
    #: An explicit declaration made for sensitivity analysis. Not Production-equivalent.
    ASSUMED = "ASSUMED"
    #: No record and no declaration. The value is not known and is not invented.
    UNKNOWN = "UNKNOWN"


class CandidateMode(StrEnum):
    """Which symbols a run may evaluate on a date."""

    #: Only symbols a durable scanner run actually carried as candidates.
    RECORDED_ONLY = "RECORDED_ONLY"
    #: An explicitly supplied symbol universe. This is not a Scanner reproduction.
    RESEARCH_UNIVERSE = "RESEARCH_UNIVERSE"


class ApprovalMode(StrEnum):
    """Which candidates count as approved for evaluation."""

    #: Scanner run -> active GPT analysis -> human APPROVE, all durable, or nothing.
    RECORDED_ONLY = "RECORDED_ONLY"
    #: Every eligible research symbol is treated as approved. Strategy mechanics only.
    ALL_RESEARCH_SYMBOLS = "ALL_RESEARCH_SYMBOLS"


class OvernightMode(StrEnum):
    """What the closing review's carry gate is told."""

    #: The recorded GPT value, or UNKNOWN when there is none.
    RECORDED = "RECORDED"
    #: UNKNOWN regardless of what is on record. The Production default, and the most
    #: conservative baseline: the gate refuses the carry and the position closes at EOD.
    UNKNOWN_CLOSE = "UNKNOWN_CLOSE"
    #: A declared suitability for every eligible position. Sensitivity analysis only;
    #: never Production-equivalent. HIGH and MEDIUM both open the carry gate, LOW does
    #: not - which is exactly why a declaration has to name which one it made.
    ASSUMED_HIGH = "ASSUMED_HIGH"
    ASSUMED_MEDIUM = "ASSUMED_MEDIUM"
    ASSUMED_LOW = "ASSUMED_LOW"


class TrailingMode(StrEnum):
    """Which trailing profile the trailing stop reads."""

    RECORDED = "RECORDED"
    #: ``TrailingProfile.UNKNOWN``, which the engine maps to the same multiplier as NORMAL.
    UNKNOWN_DEFAULT = "UNKNOWN_DEFAULT"
    ASSUMED_NORMAL = "ASSUMED_NORMAL"
    ASSUMED_TIGHT = "ASSUMED_TIGHT"
    ASSUMED_WIDE = "ASSUMED_WIDE"


#: The suitability each assumed overnight mode declares.
ASSUMED_OVERNIGHT: dict[OvernightMode, OvernightSuitability] = {
    OvernightMode.ASSUMED_HIGH: OvernightSuitability.HIGH,
    OvernightMode.ASSUMED_MEDIUM: OvernightSuitability.MEDIUM,
    OvernightMode.ASSUMED_LOW: OvernightSuitability.LOW,
}

#: The profile each assumed trailing mode declares.
ASSUMED_TRAILING: dict[TrailingMode, TrailingProfile] = {
    TrailingMode.ASSUMED_NORMAL: TrailingProfile.NORMAL,
    TrailingMode.ASSUMED_TIGHT: TrailingProfile.TIGHT,
    TrailingMode.ASSUMED_WIDE: TrailingProfile.WIDE,
}


def overnight_mode_for(value: OvernightSuitability) -> OvernightMode:
    """The mode that declares a suitability directly. UNKNOWN is a declaration too.

    Every shorthand a caller can pass therefore has a mode that describes it, so a
    result never reports a value one mode produced under the name of another.
    """
    value = OvernightSuitability(value)
    for mode, declared in ASSUMED_OVERNIGHT.items():
        if declared is value:
            return mode
    return OvernightMode.UNKNOWN_CLOSE


@dataclass(frozen=True)
class AuthorityModes:
    """The four declarations a run makes, plus the universe a research run declares.

    Frozen and validated on construction: a mode set that cannot be read back
    unambiguously is refused here rather than producing a context that lies.
    """

    candidate: CandidateMode = CandidateMode.RESEARCH_UNIVERSE
    approval: ApprovalMode = ApprovalMode.ALL_RESEARCH_SYMBOLS
    overnight: OvernightMode = OvernightMode.UNKNOWN_CLOSE
    trailing: TrailingMode = TrailingMode.UNKNOWN_DEFAULT
    #: Ordered. Under RESEARCH_UNIVERSE the order is the declared priority, which is
    #: what the rank becomes - an assumed rank, never a reconstructed Scanner rank.
    research_universe: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate", CandidateMode(self.candidate))
        object.__setattr__(self, "approval", ApprovalMode(self.approval))
        object.__setattr__(self, "overnight", OvernightMode(self.overnight))
        object.__setattr__(self, "trailing", TrailingMode(self.trailing))
        universe = tuple(dict.fromkeys(normalize_symbol(item)
                                       for item in self.research_universe))
        object.__setattr__(self, "research_universe", universe)
        if self.candidate is CandidateMode.RESEARCH_UNIVERSE and not universe:
            raise AuthorityModeInvalid(
                "RESEARCH_UNIVERSE needs the symbol universe named explicitly; an empty "
                "universe admits nothing and hides which symbols were chosen")
        if self.candidate is CandidateMode.RECORDED_ONLY and universe:
            raise AuthorityModeInvalid(
                "RECORDED_ONLY reads the universe from the durable scanner run; passing "
                "a research universe beside it would leave two answers on record")

    @property
    def production_equivalent(self) -> bool:
        """True only when every declaration reads a record instead of declaring a value."""
        return (self.candidate is CandidateMode.RECORDED_ONLY
                and self.approval is ApprovalMode.RECORDED_ONLY
                and self.overnight is OvernightMode.RECORDED
                and self.trailing is TrailingMode.RECORDED)

    @property
    def research_label(self) -> str:
        """The label a result carries so a number is never read without its permission."""
        return "PRODUCTION_EQUIVALENT" if self.production_equivalent else "RESEARCH_ONLY"

    def as_dict(self) -> dict[str, object]:
        return {"candidate_mode": self.candidate.value, "approval_mode": self.approval.value,
                "overnight_mode": self.overnight.value, "trailing_mode": self.trailing.value,
                "research_universe": list(self.research_universe),
                "authority_label": self.research_label}

    def lines(self) -> tuple[str, ...]:
        universe = ",".join(self.research_universe) or "null"
        return (f"candidate_mode={self.candidate.value}",
                f"approval_mode={self.approval.value}",
                f"overnight_mode={self.overnight.value}",
                f"trailing_mode={self.trailing.value}",
                f"research_universe={universe}",
                f"authority_label={self.research_label}")

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "AuthorityModes":
        universe = payload.get("research_universe") or ()
        if isinstance(universe, str):  # pragma: no cover - defensive
            universe = [universe]
        return cls(candidate=CandidateMode(str(payload["candidate_mode"])),
                   approval=ApprovalMode(str(payload["approval_mode"])),
                   overnight=OvernightMode(str(payload["overnight_mode"])),
                   trailing=TrailingMode(str(payload["trailing_mode"])),
                   research_universe=tuple(str(item) for item in universe))


#: The declaration the Replay Core has been making implicitly since it was written: one
#: symbol handed to it by name, treated as approved, with no research values at all.
#: Naming it does not change what replay does; it makes the permission readable.
def research_modes(universe: Iterable[str]) -> AuthorityModes:
    return AuthorityModes(candidate=CandidateMode.RESEARCH_UNIVERSE,
                          approval=ApprovalMode.ALL_RESEARCH_SYMBOLS,
                          overnight=OvernightMode.UNKNOWN_CLOSE,
                          trailing=TrailingMode.UNKNOWN_DEFAULT,
                          research_universe=tuple(universe))


#: Every permission read from a record, and nothing declared.
RECORDED_MODES = AuthorityModes(candidate=CandidateMode.RECORDED_ONLY,
                                approval=ApprovalMode.RECORDED_ONLY,
                                overnight=OvernightMode.RECORDED,
                                trailing=TrailingMode.RECORDED)


@dataclass(frozen=True)
class HistoricalAuthorityContext:
    """One symbol, one entry session: what it was allowed to do and on whose word.

    Deliberately holds no strategy result. A run's outcome and the permission it ran
    under are recorded side by side and never merged, because the only way to misread a
    backtest number is to lose the permission that produced it.
    """

    symbol: str
    trading_date: date
    modes: AuthorityModes

    candidate_source: AuthoritySource = AuthoritySource.UNKNOWN
    approval_source: AuthoritySource = AuthoritySource.UNKNOWN
    overnight_source: AuthoritySource = AuthoritySource.UNKNOWN
    trailing_source: AuthoritySource = AuthoritySource.UNKNOWN
    rank_source: AuthoritySource = AuthoritySource.UNKNOWN

    candidate_eligible: bool = False
    approved: bool = False
    rank: int | None = None
    #: The scanner run's own rank, which is part of the candidate record rather than a
    #: separate authority. ``rank`` above is the GPT/priority rank Production reports.
    scanner_rank: int | None = None

    overnight_suitability: OvernightSuitability = OvernightSuitability.UNKNOWN
    trailing_profile: TrailingProfile = TrailingProfile.UNKNOWN

    analysis_trading_date: date | None = None
    scanner_run_id: int | None = None
    scanner_candidate_id: int | None = None
    gpt_analysis_id: int | None = None
    human_decision_id: int | None = None

    provenance_notes: tuple[str, ...] = field(default_factory=tuple)
    authority_version: str = AUTHORITY_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        object.__setattr__(self, "overnight_suitability",
                           OvernightSuitability(self.overnight_suitability))
        object.__setattr__(self, "trailing_profile", TrailingProfile(self.trailing_profile))
        if self.approved and not self.candidate_eligible:
            raise AuthorityModeInvalid(
                f"{self.symbol} {self.trading_date}: approved without being an eligible "
                "candidate; approval never admits a symbol the candidate authority refused")

    @property
    def evaluable(self) -> bool:
        """Whether a replay may evaluate this symbol on this date at all."""
        return self.candidate_eligible and self.approved

    @property
    def refusal(self) -> str | None:
        """Why an evaluation is refused, in the words of the authority that refused it."""
        if not self.candidate_eligible:
            return (f"{self.symbol} {self.trading_date} is not a candidate under "
                    f"{self.modes.candidate.value} (candidate_source="
                    f"{self.candidate_source.value})")
        if not self.approved:
            return (f"{self.symbol} {self.trading_date} is not approved under "
                    f"{self.modes.approval.value} (approval_source="
                    f"{self.approval_source.value})")
        return None

    def declaring_overnight(self, value: OvernightSuitability) -> "HistoricalAuthorityContext":
        """This permission with its overnight declaration replaced by a direct one.

        The shorthand a contract test uses. It replaces the value, the source and the
        mode together, so what the carry gate reads and what the result reports are
        always the same declaration.
        """
        value = OvernightSuitability(value)
        if value is self.overnight_suitability and self.overnight_source in {
                AuthoritySource.ASSUMED, AuthoritySource.UNKNOWN}:
            return self
        mode = overnight_mode_for(value)
        source = (AuthoritySource.UNKNOWN if value is OvernightSuitability.UNKNOWN
                  else AuthoritySource.ASSUMED)
        note = (f"overnight suitability is declared {value.value} directly by the run "
                f"({mode.value}); any recorded value is not read")
        return replace(self, overnight_suitability=value, overnight_source=source,
                       modes=replace(self.modes, overnight=mode),
                       provenance_notes=tuple(dict.fromkeys((*self.provenance_notes, note))))

    def provenance(self) -> dict[str, object]:
        """The provenance a result records, so a figure carries its permission with it."""
        return {"authority_version": self.authority_version,
                "candidate_authority_source": self.candidate_source.value,
                "approval_authority_source": self.approval_source.value,
                "overnight_authority_source": self.overnight_source.value,
                "trailing_authority_source": self.trailing_source.value,
                "rank_authority_source": self.rank_source.value,
                **self.modes.as_dict()}

    def lines(self) -> tuple[str, ...]:
        return (f"authority_version={self.authority_version}",
                f"symbol={self.symbol} trading_date={self.trading_date}",
                *self.modes.lines(),
                f"candidate_eligible={self.candidate_eligible} "
                f"candidate_source={self.candidate_source.value} "
                f"scanner_rank={_null(self.scanner_rank)}",
                f"approved={self.approved} approval_source={self.approval_source.value}",
                f"overnight_suitability={self.overnight_suitability.value} "
                f"overnight_source={self.overnight_source.value}",
                f"trailing_profile={self.trailing_profile.value} "
                f"trailing_source={self.trailing_source.value}",
                f"rank={_null(self.rank)} rank_source={self.rank_source.value}",
                f"analysis_trading_date={_null(self.analysis_trading_date)}",
                f"scanner_run_id={_null(self.scanner_run_id)} "
                f"scanner_candidate_id={_null(self.scanner_candidate_id)} "
                f"gpt_analysis_id={_null(self.gpt_analysis_id)} "
                f"human_decision_id={_null(self.human_decision_id)}",
                *(f"note {note}" for note in self.provenance_notes))


#: The provenance keys a replay result carries as its own fields. Fixed on purpose: a
#: result field and the context key that fills it are the same name, and the test that
#: compares the two sets is what keeps them that way.
RESULT_PROVENANCE_KEYS = (
    "authority_version", "candidate_authority_source", "approval_authority_source",
    "overnight_authority_source", "trailing_authority_source", "rank_authority_source",
    "candidate_mode", "approval_mode", "overnight_mode", "trailing_mode", "authority_label")


def result_provenance(context: "HistoricalAuthorityContext | None") -> dict[str, str]:
    """The authority fields a replay result records, or nothing when none was declared.

    ``research_universe`` is deliberately not among them: it is a list, a result field
    is a scalar, and the universe is already implied by the candidate mode plus the
    symbol the result is about.
    """
    if context is None:
        return {}
    provenance = context.provenance()
    return {key: str(provenance[key]) for key in RESULT_PROVENANCE_KEYS}


def _null(value: object) -> str:
    return "null" if value is None else str(value)


def universe_rank(universe: Sequence[str], symbol: str) -> int | None:
    """One-based position of a symbol in a declared universe, or ``None``."""
    symbol = normalize_symbol(symbol)
    return universe.index(symbol) + 1 if symbol in universe else None
