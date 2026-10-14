"""The model call the autonomous pipeline hands to the frozen D3 and D4 runners.

The D3 and D4 runners already take ``call_fn(system, user) -> raw`` so they can be driven without a
live call. This wrapper is that ``call_fn``, with three duties the runners do not have on their own:

* **canonical model, per call.** Every non-error response must report
  ``modelUsage["claude-opus-5-5"].canonicalModel == "claude-opus-5-5"``. Anything else raises
  ``ModelContractViolation`` at once, before the next call is made. There is no fallback model:
  the live call passes ``--model claude-opus-5-5`` and never ``--fallback-model``.
* **metering.** Every call's ``total_cost_usd`` is summed. A call that dies without a JSON body
  (timeout, killed process) is charged its per-call cap, because what it cost is unknown.
* **the reservation as a ceiling.** A call is refused (``BudgetCeilingReached``) if the spend so far
  plus one per-call cap would exceed the candidate's reservation. With three D3 and three D4 calls at
  most, this never binds under the frozen contract; it is the guard against a contract that drifts.

``live_call`` delegates to the D3 runner's own ``call_opus`` (one CLI invocation, unchanged).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from app.strategies.h_auto import contract as AC

CallFn = Callable[[str, str], dict]


class ModelContractViolation(RuntimeError):
    pass


class BudgetCeilingReached(RuntimeError):
    pass


class ModelProcessError(RuntimeError):
    pass


def canonical_of(raw: dict, requested: str) -> str | None:
    return ((raw.get("modelUsage") or {}).get(requested) or {}).get("canonicalModel")


def live_call(system_prompt: str, user_prompt: str) -> dict:
    """The one live model call. Imported lazily so a test never loads it by accident."""
    from app.dev.run_strategy_h_v2_d3_3 import MODEL, call_opus
    if MODEL != AC.model():
        raise ModelContractViolation(f"D3 runner model {MODEL} != contract {AC.model()}")
    return call_opus(system_prompt, user_prompt)


@dataclass
class MeteredModel:
    call: CallFn
    ceiling_usd: float
    per_call_cap_usd: float = field(default_factory=lambda: float(AC.contract()["model"]["per_call_max_budget_usd"]))
    requested: str = field(default_factory=AC.model)
    required: str = field(default_factory=AC.canonical_model)
    spent_usd: float = 0.0
    calls: int = 0
    canonical_models: list[str | None] = field(default_factory=list)
    stage: str = "?"
    by_stage: dict[str, float] = field(default_factory=dict)

    def __call__(self, system_prompt: str, user_prompt: str) -> dict:
        if self.spent_usd + self.per_call_cap_usd > self.ceiling_usd + 1e-9:
            raise BudgetCeilingReached(f"spent {self.spent_usd:.4f} + cap {self.per_call_cap_usd} > {self.ceiling_usd}")
        self.calls += 1
        try:
            raw = self.call(system_prompt, user_prompt)
        except Exception as exc:                        # a dead process: charged its cap, unknown cost
            self._charge(self.per_call_cap_usd)
            raise ModelProcessError(f"{type(exc).__name__}: {exc}") from exc
        if not isinstance(raw, dict):
            self._charge(self.per_call_cap_usd)
            raise ModelProcessError("model call returned no JSON object")
        self._charge(float(raw.get("total_cost_usd") or 0.0))
        canonical = canonical_of(raw, self.requested)
        self.canonical_models.append(canonical)
        if not raw.get("is_error") and canonical != self.required:
            raise ModelContractViolation(
                f"canonical model {canonical!r} != required {self.required!r} (no fallback)")
        return raw

    def _charge(self, amount: float) -> None:
        self.spent_usd += amount
        self.by_stage[self.stage] = self.by_stage.get(self.stage, 0.0) + amount

    def report(self) -> dict[str, Any]:
        return {"spent_usd": round(self.spent_usd, 6), "calls": self.calls,
                "by_stage": {k: round(v, 6) for k, v in self.by_stage.items()},
                "canonical_models": self.canonical_models, "requested_model": self.requested}
