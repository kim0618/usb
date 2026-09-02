"""Human-decision-independent Top 8 shadow fan-out foundation."""

from collections.abc import Callable, Iterable

from app.shadow.domain import ShadowResult, ShadowStatus, ShadowVariant


class ShadowService:
    def fan_out(self, candidates: Iterable[object],
                decision: Callable[[object, ShadowVariant], ShadowStatus] | None = None) -> tuple[ShadowResult, ...]:
        """Fan out every supplied Top-8 candidate; human decisions are intentionally absent."""
        results: list[ShadowResult] = []
        for candidate in candidates:
            if not getattr(candidate, "is_top8", False):
                continue
            for variant in ShadowVariant:
                status = decision(candidate, variant) if decision else ShadowStatus.NO_TRADE
                results.append(ShadowResult(getattr(candidate, "symbol"), variant, status=status))
        return tuple(results)
