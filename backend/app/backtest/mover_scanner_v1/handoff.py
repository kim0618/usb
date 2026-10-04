"""Hand the mover scanner's output to the existing GPT pipeline without touching the prompt.

The deployed prompt is rendered by ``ResearchPromptService`` from persisted ``ScannerRun`` and
``ScannerCandidate`` rows: it reads ``rank``, ``symbol``, ``score`` and four keys inside
``score_components_json`` (``latest_close``, ``latest_volume``, ``market_cap``, ``raw``,
``normalized``, ``weighted_contributions``), and it states the candidate count from the number
of rows it is given. So a scanner that fills those rows needs no prompt change at all, and a
session with five candidates renders a five-symbol prompt by itself.

This module only shapes the payload. It opens no session, writes no row and calls no provider;
``ScannerCandidateData`` is built so that a shadow run against a research database could
persist it through the existing repository unchanged.

``market_cap`` is sent as None. The store has dated market caps for a few hundred symbols only,
and a market-wide premarket scan cannot supply one for most of its candidates; None is what the
prompt already renders for an unknown, and inventing a number would be worse than omitting it.
The premarket context GPT actually needs travels in ``raw`` and ``normalized``.
"""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from app.backtest.mover_scanner_v1.config import MoverScannerConfig
from app.backtest.mover_scanner_v1.scan import MoverCandidate
from app.repositories.scanner import ScannerCandidateData


def candidate_components(candidate: MoverCandidate) -> dict[str, Any]:
    """``score_components_json`` as the prompt reads it, carrying the premarket context."""
    return {
        "raw": {
            "gap_pct": candidate.gap_pct,
            "pm_volume": candidate.pm_volume,
            "pm_dollar_volume": candidate.pm_dollar_volume,
            "pm_rvol": candidate.pm_rvol,
            "pm_momentum": candidate.pm_momentum,
            "pm_range": candidate.pm_range,
            "pm_bars": float(candidate.pm_bars),
            "tradability": candidate.tradability_score,
            "gap_quality": candidate.gap_quality,
            "previous_close": candidate.previous_close,
            "average_daily_volume_20": candidate.adv20_shares,
            "average_dollar_volume_20": candidate.addv20_dollar,
        },
        "normalized": dict(candidate.normalized_components),
        "weighted_contributions": dict(candidate.component_scores),
        "final_score": candidate.total_score,
        "market_cap": None,
        "latest_close": candidate.last_price,
        "latest_volume": candidate.pm_volume,
        "candidate_pool_rank": candidate.candidate_pool_rank,
    }


def candidate_rows(top: Sequence[MoverCandidate], observed_at: datetime,
                   ) -> list[ScannerCandidateData]:
    """The persistable rows for one session's output, in rank order.

    ``is_top8`` marks membership of the handed-off set, which is at most ``top_count`` and may
    be shorter; the prompt counts rows rather than assuming eight.
    """
    return [ScannerCandidateData(
        symbol=candidate.symbol, rank=candidate.rank, is_top8=True,
        score=candidate.total_score, score_components=candidate_components(candidate),
        observed_at=observed_at, available_at=observed_at) for candidate in top]


def handoff_payload(session: date, top: Sequence[MoverCandidate], config: MoverScannerConfig,
                    observed_at: datetime) -> dict[str, Any]:
    """Everything the GPT step needs: the symbols, their context, and the rules that chose them."""
    return {
        "trading_date": session.isoformat(),
        "scanner": config.contract_version,
        "score_version": config.score_version,
        "rules_checksum": config.checksum,
        "scan_time_et_minute": config.scan_cut_minute,
        "candidate_count": len(top),
        "top_count_maximum": config.top_count,
        "candidates": [{"rank": candidate.rank, "symbol": candidate.symbol,
                        "score": candidate.total_score,
                        "score_components": candidate_components(candidate)}
                       for candidate in top],
    }
