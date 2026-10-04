"""Paper run configuration. Nothing here has a silent default.

Two figures cannot be measured from a public Bybit endpoint: the account's fee tier and the
KRW/USDT rate. Both are therefore required run parameters carrying their own provenance, so
a run can never be created with an invented number. A config that omits either does not build.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

from .instrument import InstrumentSpec


def _decimal(value: Any, name: str) -> Decimal:
    if isinstance(value, float):
        raise TypeError(f"{name} must not be a float; pass a string or Decimal")
    try:
        parsed = Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"{name} is not a decimal: {value!r}") from exc
    if not parsed.is_finite():
        raise ValueError(f"{name} must be finite: {value!r}")
    return parsed


@dataclass(frozen=True)
class FeeSchedule:
    """Taker/maker rates with the evidence that chose them.

    `version`, `source` and `effective_date` are mandatory. The rates are an ASSUMPTION: the
    project holds no exchange account, so its actual VIP tier is unknown and unknowable from
    public data.
    """
    version: str
    taker_rate: Decimal
    maker_rate: Decimal
    source: str
    effective_date: str
    basis: str = "ASSUMED_PUBLIC_NON_VIP"

    #: How much of the schedule is measured rather than guessed. A closed vocabulary on
    #: purpose: a fee basis nobody named is a fee basis nobody thought about.
    BASES = (
        "ASSUMED_PUBLIC_NON_VIP",
        "OFFICIAL_PUBLIC_VIP0_TIER_ASSUMED",
        # Sensitivity runs. The rate is published; the tier is one US-B has not reached.
        "OFFICIAL_PUBLIC_TIER_HYPOTHETICAL",
        # Not a fee estimate at all: the bound a result has to clear costs from.
        "NO_COST_BOUND",
    )

    def __post_init__(self) -> None:
        if self.basis not in self.BASES:
            raise ValueError(f"unknown fee basis: {self.basis}")
        for name in ("version", "source", "effective_date", "basis"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"fee schedule {name} is required")
        for name in ("taker_rate", "maker_rate"):
            rate = getattr(self, name)
            if not isinstance(rate, Decimal):
                raise TypeError(f"fee schedule {name} must be Decimal")
            if rate < 0 or rate > Decimal("0.01"):
                raise ValueError(f"fee schedule {name} out of sane range: {rate}")

    def rate_for(self, liquidity: str) -> Decimal:
        if liquidity == "TAKER":
            return self.taker_rate
        if liquidity == "MAKER":
            return self.maker_rate
        raise ValueError(f"unknown liquidity role: {liquidity}")


@dataclass(frozen=True)
class SlippageModel:
    """Adverse price adjustment applied *on top of* walking the book.

    `NONE` and `FIXED_BPS` are the two models D3 implements. Book depth consumption is not a
    slippage model; it is the fill model itself, and it always applies.
    """
    model: str
    bps: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        if self.model not in {"NONE", "FIXED_BPS"}:
            raise ValueError(f"unknown slippage model: {self.model}")
        if not isinstance(self.bps, Decimal):
            raise TypeError("slippage bps must be Decimal")
        if self.model == "NONE" and self.bps != 0:
            raise ValueError("slippage model NONE must carry bps 0")
        if self.bps < 0 or self.bps > Decimal("100"):
            raise ValueError(f"slippage bps out of sane range: {self.bps}")

    def adjust(self, price: Decimal, direction: int) -> Decimal:
        """`direction` +1 buys (price moves up against us), -1 sells (price moves down)."""
        if self.model == "NONE":
            return price
        return price * (Decimal(1) + Decimal(direction) * self.bps / Decimal(10_000))


@dataclass(frozen=True)
class FxFixing:
    """KRW/USDT fixed once at run start. Run-time moves never reach strategy PnL."""
    krw_per_usdt: Decimal
    source: str
    asof_utc: str

    def __post_init__(self) -> None:
        if not isinstance(self.krw_per_usdt, Decimal):
            raise TypeError("krw_per_usdt must be Decimal")
        if self.krw_per_usdt <= 0:
            raise ValueError("krw_per_usdt must be positive")
        for name in ("source", "asof_utc"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"fx fixing {name} is required")

    def to_usdt(self, krw: Decimal) -> Decimal:
        return krw / self.krw_per_usdt


# The virtual account a new run starts with, and the figure a reset restores. One constant so
# the CLI, the reset endpoint and the docs cannot drift apart. It is deliberately NOT written
# into `PaperRunConfig.snapshot()`: that snapshot is inside the RUN_START ledger event, and a
# run already on disk must keep rebuilding byte for byte when its tape is replayed.
DEFAULT_STARTING_CAPITAL_KRW = Decimal("10000000")


@dataclass(frozen=True)
class PaperRunConfig:
    run_id: str
    starting_capital_krw: Decimal
    fx: FxFixing
    fees: FeeSchedule
    slippage: SlippageModel
    leverage: Decimal
    risk_limit_source: str
    risk_limit_sha256: str
    instrument: InstrumentSpec = field(default_factory=InstrumentSpec)
    qty_off_grid_policy: str = "REJECT"
    max_market_qty_policy: str = "REJECT"
    reverse_policy: str = "REJECT"
    liquidation_fill_basis: str = "MARK_AT_TRIGGER"

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id is required")
        if not isinstance(self.starting_capital_krw, Decimal) or self.starting_capital_krw <= 0:
            raise ValueError("starting_capital_krw must be a positive Decimal")
        if self.qty_off_grid_policy != "REJECT":
            raise ValueError("D3 fixes the off-grid quantity policy at REJECT")
        if self.max_market_qty_policy != "REJECT":
            raise ValueError("D3 fixes the max market quantity policy at REJECT")
        if self.reverse_policy != "REJECT":
            raise ValueError("D3 fixes the reverse policy at REJECT")
        if self.liquidation_fill_basis != "MARK_AT_TRIGGER":
            raise ValueError("D3 fixes the liquidation fill basis at MARK_AT_TRIGGER")
        self.validate_leverage(self.leverage)

    def validate_leverage(self, leverage: Decimal) -> None:
        spec = self.instrument
        if not isinstance(leverage, Decimal):
            raise TypeError("leverage must be Decimal")
        if leverage < spec.min_leverage or leverage > spec.max_leverage:
            raise ValueError(f"leverage {leverage} outside [{spec.min_leverage}, {spec.max_leverage}]")
        if (leverage / spec.leverage_step) % 1 != 0:
            raise ValueError(f"leverage {leverage} is not on the {spec.leverage_step} step grid")

    @property
    def starting_capital_usdt(self) -> Decimal:
        return self.fx.to_usdt(self.starting_capital_krw)

    def with_leverage(self, leverage: Decimal) -> "PaperRunConfig":
        self.validate_leverage(leverage)
        return PaperRunConfig(
            run_id=self.run_id, starting_capital_krw=self.starting_capital_krw, fx=self.fx,
            fees=self.fees, slippage=self.slippage, leverage=leverage,
            risk_limit_source=self.risk_limit_source, risk_limit_sha256=self.risk_limit_sha256,
            instrument=self.instrument)

    def snapshot(self) -> dict[str, Any]:
        """Canonical, JSON-safe config record written as the first ledger event."""
        def convert(value: Any) -> Any:
            if isinstance(value, Decimal):
                return str(value)
            if isinstance(value, dict):
                return {k: convert(v) for k, v in value.items()}
            return value
        data = convert(asdict(self))
        data["paper_engine_version"] = __import__("app.crypto.paper", fromlist=["x"]).PAPER_ENGINE_VERSION
        data["starting_capital_usdt"] = str(self.starting_capital_usdt)
        return data


def build_config(
    *, run_id: str, starting_capital_krw: str, fx_krw_per_usdt: str, fx_source: str, fx_asof_utc: str,
    fee_version: str, fee_taker_rate: str, fee_maker_rate: str, fee_source: str, fee_effective_date: str,
    slippage_model: str, slippage_bps: str, leverage: str,
    risk_limit_source: str, risk_limit_sha256: str,
    fee_basis: str = "ASSUMED_PUBLIC_NON_VIP",
    instrument: InstrumentSpec | None = None,
) -> PaperRunConfig:
    """Single construction path, so no caller can skip the provenance fields.

    `instrument` is optional and defaults to the BTCUSDT spec measured in D1, which is what
    every pre-existing caller gets. A run on another instrument must pass one built from that
    instrument's own saved `instruments-info` response; it is not derivable from the symbol
    name, and the engine's quantity rules are only as correct as this object.
    """
    return PaperRunConfig(
        run_id=run_id,
        starting_capital_krw=_decimal(starting_capital_krw, "starting_capital_krw"),
        fx=FxFixing(krw_per_usdt=_decimal(fx_krw_per_usdt, "fx_krw_per_usdt"),
                    source=fx_source, asof_utc=fx_asof_utc),
        fees=FeeSchedule(version=fee_version, taker_rate=_decimal(fee_taker_rate, "fee_taker_rate"),
                         maker_rate=_decimal(fee_maker_rate, "fee_maker_rate"),
                         source=fee_source, effective_date=fee_effective_date, basis=fee_basis),
        slippage=SlippageModel(model=slippage_model, bps=_decimal(slippage_bps, "slippage_bps")),
        leverage=_decimal(leverage, "leverage"),
        risk_limit_source=risk_limit_source, risk_limit_sha256=risk_limit_sha256,
        **({"instrument": instrument} if instrument is not None else {}))
