"""The opportunity score: shaped qualities, cross-sectional normalisation, pool, then output.

Three rules shape this module.

**No feature may win on its raw scale.** Premarket notional spans six orders of magnitude and
relative volume spans four, so a weighted sum of raw values would be a dollar-volume ranking
with decoration. Every component is therefore normalised across the session's own candidates
before it is weighted, using the recipe Quant V0 already uses: log1p for the heavy-tailed
non-negative components, then winsorise at the 5th and 95th percentiles, then standardise. The
log path delegates to ``app.scanner.normalization.normalize_metric`` so there is one recipe.

**A bigger gap is not a better gap.** ``gap_quality`` maps the gap through the declared knots
first and the normalisation ranks the quality, not the gap. Rank is preserved by the monotone
standardisation, so a 20% gap still scores below a 4% one. Nothing is rejected for its gap; a
sub-2% gap simply scores low, and Strategy A's entry gate remains the only thing that refuses.

**Relative volume never ranks alone.** It is one of two pool components, beside premarket
notional, which is what keeps a microcap whose 200-share premarket is 40x its median from
displacing a name with real money behind it.
"""

from collections.abc import Mapping, Sequence
from math import isfinite

import numpy as np

from app.backtest.mover_scanner_v1.config import MoverScannerConfig
from app.core.exceptions import DataError
from app.scanner.normalization import normalize_metric


def gap_quality(gap_pct: float, config: MoverScannerConfig) -> float:
    """The declared gap shape: linear between knots, flat beyond the ends."""
    knots = config.gap_quality_knots
    if gap_pct <= knots[0][0]:
        return float(knots[0][1])
    if gap_pct >= knots[-1][0]:
        return float(knots[-1][1])
    gaps = np.asarray([point[0] for point in knots], dtype=np.float64)
    qualities = np.asarray([point[1] for point in knots], dtype=np.float64)
    return float(np.interp(gap_pct, gaps, qualities))


def momentum_parts(last_price: float, reference_price: float, high: float, low: float,
                   up_bar_share: float, config: MoverScannerConfig) -> dict[str, float]:
    """Whether the premarket move is still being held, as three bounded readings.

    ``high_proximity`` is where the last print sits in the premarket range: a name that gapped
    and faded prints its last trade near the low. ``late_return`` is the move over the final
    window before the cut. ``up_bar_share`` is the share of premarket bars that closed up.
    A flat premarket range is neutral rather than undefined.
    """
    span = high - low
    proximity = 0.5 if span <= 0 else float(np.clip((last_price - low) / span, 0.0, 1.0))
    late = 0.0 if reference_price <= 0 else last_price / reference_price - 1.0
    late_quality = float(np.clip(0.5 + late / (2.0 * config.momentum_late_return_full), 0.0, 1.0))
    weights = config.momentum_weights
    composite = (weights[0] * proximity + weights[1] * float(np.clip(up_bar_share, 0.0, 1.0))
                 + weights[2] * late_quality)
    return {"pm_momentum": composite, "high_proximity": proximity,
            "late_return": late, "late_return_quality": late_quality,
            "up_bar_share": float(up_bar_share)}


def tradability(price: float, addv20_dollar: float, pm_bars: float,
                config: MoverScannerConfig) -> dict[str, float]:
    """Can this be got into and out of, as an executability reading in [0, 1].

    Three independent obstacles, equally weighted: no depth in the regular session, no
    premarket prints to transact against, and a price low enough that the tick is a material
    share of it. It is a proxy, not a spread: the store holds no quotes.
    """
    low, full = config.tradability_addv_floor, config.tradability_addv_full
    depth = 0.0 if addv20_dollar <= low else float(
        np.clip(np.log10(addv20_dollar / low) / np.log10(full / low), 0.0, 1.0))
    prints = float(np.clip(pm_bars / config.tradability_prints_full, 0.0, 1.0))
    level = float(np.clip((price - config.minimum_price)
                          / (config.tradability_price_full - config.minimum_price), 0.0, 1.0))
    return {"tradability_score": (depth + prints + level) / 3.0,
            "depth": depth, "prints": prints, "level": level}


def _standardize(values: Mapping[str, float], lower: float, upper: float) -> dict[str, float]:
    """Winsorise then standardise a bounded component, with no log transform."""
    symbols = sorted(values)
    data = np.asarray([values[symbol] for symbol in symbols], dtype=np.float64)
    if not np.isfinite(data).all():
        raise DataError("normalization input contains NaN or infinity")
    bounds = np.percentile(data, [lower, upper])
    clipped = np.clip(data, bounds[0], bounds[1])
    deviation = float(np.std(clipped))
    normalized = (np.zeros_like(clipped) if deviation == 0.0
                  else (clipped - float(np.mean(clipped))) / deviation)
    return {symbol: float(value) for symbol, value in zip(symbols, normalized, strict=True)}


def normalize_components(raw_by_symbol: Mapping[str, Mapping[str, float]],
                         config: MoverScannerConfig) -> dict[str, dict[str, float]]:
    """Cross-sectional normalisation of every scored component, one session at a time."""
    if not raw_by_symbol:
        return {}
    output: dict[str, dict[str, float]] = {symbol: {} for symbol in raw_by_symbol}
    for component in config.opportunity_weights:
        values = {symbol: float(row[component]) for symbol, row in raw_by_symbol.items()}
        if component in config.log_components:
            if any(value < 0 for value in values.values()):
                raise DataError(f"{component} must be non-negative to be log normalised")
            normalized = normalize_metric(values, config.winsor_lower_percentile,
                                          config.winsor_upper_percentile)
        else:
            normalized = _standardize(values, config.winsor_lower_percentile,
                                      config.winsor_upper_percentile)
        for symbol, value in normalized.items():
            output[symbol][component] = value
    return output


def contributions(normalized: Mapping[str, float], config: MoverScannerConfig,
                  ) -> dict[str, float]:
    return {component: normalized[component] * weight
            for component, weight in config.opportunity_weights.items()}


def pool_score(normalized: Mapping[str, float], config: MoverScannerConfig) -> float:
    """Stage 1: participation evidence only, at the components' own relative weights."""
    weights = {component: config.opportunity_weights[component]
               for component in config.pool_components}
    total = sum(weights.values())
    return sum(normalized[component] * weight / total for component, weight in weights.items())


def dominance(contributions_by_symbol: Sequence[Mapping[str, float]]) -> dict[str, float]:
    """The largest share any one component takes of a row's total absolute contribution.

    Reported so 'no feature dominates the score' is a measured claim rather than an intention.
    """
    shares: dict[str, list[float]] = {}
    for row in contributions_by_symbol:
        magnitude = sum(abs(value) for value in row.values())
        if magnitude <= 0:
            continue
        for component, value in row.items():
            shares.setdefault(component, []).append(abs(value) / magnitude)
    return {component: float(np.mean(values)) for component, values in sorted(shares.items())
            if values}


def finite(*values: float) -> bool:
    return all(isinstance(value, (int, float)) and isfinite(value) for value in values)
