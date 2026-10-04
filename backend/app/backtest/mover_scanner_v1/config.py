"""The declared A-MOVER-SCANNER-V1 rules. Code carries no threshold of its own.

Every number a report depends on is a field here, and ``MoverScannerConfig.checksum`` hashes
the whole declaration so an artifact can be tied to the rules that made it. The defaults are
the contract; a study that changes one produces a different checksum rather than a quietly
different answer under the same name.

Two numbers are deliberately borrowed rather than invented: the minimum premarket print count
(3) and the minimum premarket dollar volume ($50,000) are E1's audited premarket eligibility
floors (``E1_PREMARKET_OPEN_PREVALIDATION``). They are data-quality floors, not opinions about
company size, and nothing here filters on market capitalisation or on volatility.
"""

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
import hashlib
import json

CONTRACT_VERSION = "a-mover-scanner-v1"
SCORE_VERSION = "mover_v1"

#: ET minute of day. 240 = 04:00, 555 = 09:15, 570 = 09:30.
PREMARKET_START_MINUTE = 240
REGULAR_OPEN_MINUTE = 570


@dataclass(frozen=True)
class MoverScannerConfig:
    """One frozen rule set. Ordered as the scanner applies it."""

    contract_version: str = CONTRACT_VERSION
    score_version: str = SCORE_VERSION

    # -- scan time (section D) ------------------------------------------------------------
    #: ET minute of day the scan is taken at; only bars that *ended* by then are read.
    scan_cut_minute: int = 555

    # -- universe hygiene (section C) -----------------------------------------------------
    #: Common stock only. The dated reference cache lists security type CS, so ETFs, funds
    #: and the provider's test tickers are already absent. The provider nevertheless labels
    #: preferred shares and baby bonds CS, and ``universe`` removes those by the declared
    #: NON_COMMON_BY_CIK_PREFIX_NO_FIGI rule.
    exclude_non_common_by_cik_prefix: bool = True
    #: A hard floor against extreme penny names whose quoted price is not executable in the
    #: way the strategy assumes. Deliberately far below the deployed scanner's $5.
    minimum_price: float = 1.0
    #: Minimum premarket prints inside the scan window; fewer is not a premarket.
    minimum_premarket_bars: int = 3
    #: Minimum premarket notional inside the scan window.
    minimum_premarket_dollar_volume: float = 50_000.0
    #: Sessions needing a split-adjusted previous close cannot be compared to an unadjusted
    #: premarket print, so the symbol is dropped for that session as unusable data.
    exclude_split_execution_sessions: bool = True

    # -- feature baselines (section E, I) -------------------------------------------------
    #: Prior sessions in the premarket relative-volume baseline, and the median is its method.
    premarket_rvol_baseline_sessions: int = 20
    #: A symbol that normally does not trade premarket has a zero median; this floor keeps
    #: the ratio finite and lets genuine first-time premarket activity read as abnormal.
    premarket_rvol_baseline_floor_shares: float = 1_000.0
    #: Prior sessions in the regular-session daily baselines (ADV in shares, ADDV in dollars).
    daily_baseline_sessions: int = 20
    #: Minutes before the cut that the momentum late-return window opens.
    momentum_late_window_minutes: int = 60

    # -- gap quality (section H) ----------------------------------------------------------
    #: (gap, quality) knots, linearly interpolated, flat outside the ends. The plateau sits
    #: on 10-15%, inside Strategy A's own 2-15% entry band, and quality falls away above it
    #: so a 20% gap never outranks a mid-single-digit one on this component.
    gap_quality_knots: tuple[tuple[float, float], ...] = (
        (-0.02, 0.00), (0.00, 0.05), (0.02, 0.35), (0.05, 0.75),
        (0.10, 1.00), (0.15, 1.00), (0.20, 0.55), (0.30, 0.20), (0.50, 0.05),
    )

    # -- momentum composite (section K) ---------------------------------------------------
    #: Weights over (close-to-high proximity, up-bar share, late-return quality).
    momentum_weights: tuple[float, float, float] = (0.40, 0.20, 0.40)
    #: The late return that maps to full quality; its negative maps to zero.
    momentum_late_return_full: float = 0.02

    # -- tradability proxy (section E) ----------------------------------------------------
    #: Dollar depth: this ADDV reads as zero, and ``tradability_addv_full`` as one.
    tradability_addv_floor: float = 1_000_000.0
    tradability_addv_full: float = 100_000_000.0
    #: Premarket prints that read as full print density.
    tradability_prints_full: int = 60
    #: Price band over which the penny-name execution penalty is removed.
    tradability_price_full: float = 5.0

    # -- scoring (section G) --------------------------------------------------------------
    opportunity_weights: Mapping[str, float] = field(default_factory=lambda: {
        "pm_dollar_volume": 0.30,
        "pm_rvol": 0.25,
        "gap_quality": 0.20,
        "pm_momentum": 0.15,
        "tradability": 0.10,
    })
    #: Components ranked on a heavy-tailed, non-negative raw scale take log1p first; the rest
    #: are already bounded qualities. Both then winsorize and standardise, the recipe
    #: ``app.scanner.normalization`` already uses for Quant V0.
    log_components: tuple[str, ...] = ("pm_dollar_volume", "pm_rvol")
    winsor_lower_percentile: float = 5.0
    winsor_upper_percentile: float = 95.0

    # -- pool and output (section F, L) ---------------------------------------------------
    #: Stage 1 finds movers on participation evidence alone - notional and relative volume,
    #: never relative volume by itself (section I, J). Stage 2 re-ranks the pool on the full
    #: opportunity score. Keeping the two stages distinct is what makes a pool rank mean
    #: something other than the final rank.
    pool_components: tuple[str, ...] = ("pm_dollar_volume", "pm_rvol")
    pool_size: int = 25
    #: A maximum, never a quota: fewer eligible candidates produce a shorter output.
    top_count: int = 8

    def __post_init__(self) -> None:
        if not PREMARKET_START_MINUTE < self.scan_cut_minute <= REGULAR_OPEN_MINUTE:
            raise ValueError("the scan cut must fall inside the premarket")
        if abs(sum(self.opportunity_weights.values()) - 1.0) > 1e-12:
            raise ValueError("opportunity weights must sum to 1.0")
        if set(self.log_components) - set(self.opportunity_weights):
            raise ValueError("a log component must be a scored component")
        if set(self.pool_components) - set(self.opportunity_weights):
            raise ValueError("a pool component must be a scored component")
        if abs(sum(self.momentum_weights) - 1.0) > 1e-12:
            raise ValueError("momentum weights must sum to 1.0")
        if not 0 <= self.winsor_lower_percentile < self.winsor_upper_percentile <= 100:
            raise ValueError("winsor percentiles are invalid")
        knots = self.gap_quality_knots
        if len(knots) < 2 or any(b[0] <= a[0] for a, b in zip(knots, knots[1:])):
            raise ValueError("gap quality knots must be strictly increasing in gap")
        if any(not 0.0 <= quality <= 1.0 for _, quality in knots):
            raise ValueError("gap quality must stay within [0, 1]")
        if self.pool_size < self.top_count:
            raise ValueError("the pool cannot be smaller than the output")
        if self.premarket_rvol_baseline_sessions < 1 or self.daily_baseline_sessions < 1:
            raise ValueError("baselines need at least one session")

    @property
    def premarket_window(self) -> tuple[int, int]:
        """``[start, end)`` ET minutes the scan reads. A bar starting at ``end - 1`` has ended."""
        return PREMARKET_START_MINUTE, self.scan_cut_minute

    @property
    def gate_window(self) -> tuple[int, int]:
        """``[start, end)`` ET minutes Production's own premarket gate sees, for measurement only."""
        return PREMARKET_START_MINUTE, REGULAR_OPEN_MINUTE

    def declaration(self) -> dict:
        """The whole rule set as plain data, for the artifact and the checksum."""
        payload = asdict(self)
        payload["opportunity_weights"] = dict(self.opportunity_weights)
        return payload

    @property
    def checksum(self) -> str:
        body = json.dumps(self.declaration(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode("utf-8")).hexdigest()
