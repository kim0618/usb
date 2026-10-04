"""A's 09:15 ET snapshot of one symbol, raw facts kept apart from derived values.

The snapshot is what the live pipeline knows at its cut, and it is stored in two layers on
purpose. ``raw`` is what the provider said: the minutes themselves, which ``raw_store`` keeps,
plus the counts and sums that are only additions over them. ``derived`` is what the feature
contract made of those facts, and every number in it is reproducible from the raw layer by
replaying the same function. A later feature contract therefore recomputes rather than
recollects, which is the mistake E's aggregate-only schema made once already.

The derived layer is not a reimplementation. It calls the research scanner's own
``premarket._aggregate`` over the same ``[04:00, cut)`` offsets and the same column order, so a
live row and a research row are the same arithmetic over different bars - which is the only way
the parity numbers mean anything.

Two things the cut genuinely cannot know, recorded as unknown rather than filled in:

* the **gate window**. Strategy A's deployed premarket gate reads ``[04:00, 09:30)`` and
  evaluates at the open. At 09:15 that window is not over, so ``gate_*`` is absent and
  ``scan_session`` reads the gate as unavailable. A live candidate is still refused or admitted
  by the real gate at the open, in the entry path, exactly as today.
* the **09:00 anchor**. A's momentum reference is not 09:00: ``momentum_late_window_minutes``
  is 60, so the reference is the last close strictly before 08:15. 09:00 belongs to E's
  ``return_0900_0925``. The snapshot records the reference minute it actually used.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import numpy as np

from app.backtest.mover_scanner_v1 import premarket as P
from app.backtest.mover_scanner_v1.config import PREMARKET_START_MINUTE, MoverScannerConfig
from app.strategy_a_mover_live import contract as LC
from app.strategy_a_mover_live.schedule import A_CUT_MINUTE

#: The derived fields the research panel names. Repeated here so a reader sees the contract.
DERIVED_FIELDS = P.SCAN_FIELDS


class SnapshotIncomplete(RuntimeError):
    """The collector did not finalize this symbol, so no point-in-time snapshot exists."""


@dataclass(frozen=True)
class SymbolSnapshot:
    """One symbol at the cut. ``raw`` is observation; ``derived`` is this contract's reading."""

    session_date: date
    symbol: str
    cut_minute: int
    provider: str
    collector_version: str
    feature_contract_version: str
    #: Minute of day of the last bar that had ended by the cut, or None when there was none.
    last_complete_minute: int | None
    #: Raw observation: counts and sums over the stored minutes, nothing shaped.
    raw: Mapping[str, float]
    #: This feature contract's reading of the raw layer, by the research scanner's own function.
    derived: Mapping[str, float]
    #: Which minute the momentum reference close was taken from (08:15 band), for audit.
    late_reference_minute: int | None
    #: Collector facts, so a stale or non-contiguous symbol is visible downstream.
    data_source: str | None = None
    finalized_at: datetime | None = None
    contiguous: bool | None = None
    observed_at: datetime | None = None
    #: Named rather than filled: the gate window is not over at the cut.
    unavailable: tuple[str, ...] = field(default_factory=lambda: P.GATE_FIELDS)

    @property
    def cut_time_label(self) -> str:
        return f"{self.cut_minute // 60:02d}:{self.cut_minute % 60:02d} ET"

    @property
    def has_premarket(self) -> bool:
        return bool(self.raw.get("pm_bar_count", 0.0) > 0)

    def row(self) -> dict[str, Any]:
        return {
            "session_date": self.session_date.isoformat(),
            "symbol": self.symbol,
            "provider": self.provider,
            "collector_version": self.collector_version,
            "feature_contract_version": self.feature_contract_version,
            "cut_time_et": self.cut_time_label,
            "cut_minute": self.cut_minute,
            "last_complete_minute": self.last_complete_minute,
            "raw": dict(self.raw),
            "derived": dict(self.derived),
            "late_reference_minute": self.late_reference_minute,
            "data_source": self.data_source,
            "finalized_at": self.finalized_at.isoformat() if self.finalized_at else None,
            "contiguous": self.contiguous,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
            "unavailable_at_cut": list(self.unavailable),
        }


def _matrix(minutes: Mapping[int, Sequence[float]], cut_minute: int,
            ) -> tuple[np.ndarray, np.ndarray]:
    """``(offsets, values)`` over ``[04:00, cut)``, in the research panel's column order."""
    usable = sorted((minute, values) for minute, values in minutes.items()
                    if PREMARKET_START_MINUTE <= minute < cut_minute)
    if not usable:
        return np.zeros(0, dtype=np.int64), np.zeros((0, 5), dtype=np.float64)
    offsets = np.asarray([minute - PREMARKET_START_MINUTE for minute, _ in usable],
                         dtype=np.int64)
    values = np.asarray([[float(row[index]) for index in range(5)] for _, row in usable],
                        dtype=np.float64)
    return offsets, values


def build(symbol: str, session: date, minutes: Mapping[int, Sequence[float]], *,
          observed_at: datetime | None = None, config: MoverScannerConfig | None = None,
          cut_minute: int = A_CUT_MINUTE, data_source: str | None = None,
          finalized_at: datetime | None = None, contiguous: bool | None = None,
          ) -> SymbolSnapshot:
    """One symbol's snapshot from the collector's minute cache. No network, no provider."""
    config = config or MoverScannerConfig()
    if cut_minute != config.scan_cut_minute:
        raise ValueError("the snapshot cut must be the scanner's declared cut")
    scan_limit = cut_minute - PREMARKET_START_MINUTE
    late_start = max(0, scan_limit - config.momentum_late_window_minutes)
    offsets, values = _matrix(minutes, cut_minute)
    derived = P._aggregate(offsets, values, scan_limit, late_start)  # noqa: SLF001
    inside = offsets.size
    raw = {
        "pm_bar_count": float(inside),
        "pm_share_volume": float(values[:, P.VOLUME].sum()) if inside else 0.0,
        "pm_dollar_volume": (float((values[:, P.CLOSE] * values[:, P.VOLUME]).sum())
                             if inside else 0.0),
        "pm_high": float(values[:, P.HIGH].max()) if inside else float("nan"),
        "pm_low": float(values[:, P.LOW].min()) if inside else float("nan"),
        "pm_first_price": float(values[0, P.OPEN]) if inside else float("nan"),
        "last_price": float(values[-1, P.CLOSE]) if inside else float("nan"),
        "pm_up_bar_count": (float(np.count_nonzero(values[:, P.CLOSE] > values[:, P.OPEN]))
                            if inside else 0.0),
        "pm_down_bar_count": (float(np.count_nonzero(values[:, P.CLOSE] < values[:, P.OPEN]))
                              if inside else 0.0),
    }
    before_late = np.flatnonzero(offsets < late_start) if inside else np.zeros(0, dtype=np.int64)
    reference_minute = (int(offsets[before_late[-1]]) + PREMARKET_START_MINUTE
                        if before_late.size else (int(offsets[0]) + PREMARKET_START_MINUTE
                                                  if inside else None))
    last_minute = int(offsets[-1]) + PREMARKET_START_MINUTE if inside else None
    return SymbolSnapshot(
        session_date=session, symbol=symbol, cut_minute=cut_minute, provider=LC.LIVE_PROVIDER,
        collector_version=LC.COLLECTOR_VERSION,
        feature_contract_version=LC.FEATURE_CONTRACT_VERSION,
        last_complete_minute=last_minute, raw=raw, derived=derived,
        late_reference_minute=reference_minute, data_source=data_source,
        finalized_at=finalized_at, contiguous=contiguous, observed_at=observed_at)


def from_symbol_cache(cache: Any, session: date, *, observed_at: datetime | None = None,
                      config: MoverScannerConfig | None = None,
                      cut_minute: int = A_CUT_MINUTE) -> SymbolSnapshot:
    """The snapshot of one ``strategy_e_max_rt.finalizer.SymbolCache``, as the shared
    collector leaves it. Only ``symbol``, ``bars`` and the collector verdicts are read."""
    return build(cache.symbol, session, cache.bars, observed_at=observed_at, config=config,
                 cut_minute=cut_minute, data_source=getattr(cache, "data_source", None),
                 finalized_at=getattr(cache, "finalized_at", None),
                 contiguous=getattr(cache, "contiguous", None))
