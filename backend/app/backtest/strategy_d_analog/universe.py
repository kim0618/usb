"""The D universe rule: which ``(ticker, end session)`` windows exist at all.

Declared in ``d_analog_rules_v1.json`` §universe and applied identically to query windows and
library windows (the rule is symmetric by declaration). Everything here reads values as of the
end session ``e`` and nothing later, so the mask of a truncated dataset equals the mask of the
full one - the property the PIT audit re-runs.

Failing the rule is normal: each ticker-date gets exactly one reason, evaluated in the declared
order, so the histogram partitions the excluded names and the counts add up.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from app.backtest.strategy_d_analog.config import AnalogRules
from app.backtest.strategy_d_analog.models import REASON_ORDER, HardFail, IneligibleReason
from app.backtest.strategy_d_analog.source import AsOfView

#: ADV20 reads ``e-20..e-1``: the 20 sessions before the end session, never the end session itself.
ADV_LOOKBACK = 20


@dataclass(frozen=True)
class Eligibility:
    """One end session's mask over the panel's tickers, plus the reason every excluded name failed."""

    end_idx: int
    eligible: np.ndarray
    reason: np.ndarray  # int8 index into REASON_ORDER, -1 where eligible

    @property
    def count(self) -> int:
        return int(self.eligible.sum())

    def tickers(self, names: Sequence[str]) -> tuple[str, ...]:
        return tuple(name for name, ok in zip(names, self.eligible) if ok)

    def reason_counts(self) -> dict[str, int]:
        out = {r.value: int((self.reason == i).sum()) for i, r in enumerate(REASON_ORDER)}
        out["ELIGIBLE"] = self.count
        return out


def evaluate(view: AsOfView, member: np.ndarray, rules: AnalogRules) -> Eligibility:
    """Apply the universe rule at ``view.as_of_idx``. ``member`` is the CS/exchange mask of that date."""
    e = view.as_of_idx
    seasoning = rules.seasoning_sessions
    if member.shape != (view.close.shape[1],):
        raise HardFail("R5", f"membership mask {member.shape} does not match the panel width")
    reason = np.full(view.close.shape[1], -1, dtype=np.int8)

    def fail(index: int, mask: np.ndarray) -> None:
        reason[(reason < 0) & mask] = index

    fail(REASON_ORDER.index(IneligibleReason.NOT_MEMBER), ~member)
    if e < seasoning:
        # No 61-bar window can end here; the whole date is normal-ineligible, never a hard fail.
        fail(REASON_ORDER.index(IneligibleReason.NO_HISTORY), np.ones_like(reason, dtype=bool))
        return Eligibility(e, reason < 0, reason)

    close, volume, price = view.close, view.volume, view.price
    window = close[e - seasoning:e + 1]
    history_ok = np.isfinite(window).all(axis=0) & (np.nan_to_num(window, nan=-1.0) > 0).all(axis=0)
    fail(REASON_ORDER.index(IneligibleReason.NO_HISTORY), ~history_ok)

    with np.errstate(invalid="ignore"):
        fail(REASON_ORDER.index(IneligibleReason.LOW_PRICE), ~(close[e] >= rules.min_close))
        adv = (close[e - ADV_LOOKBACK:e] * volume[e - ADV_LOOKBACK:e]).mean(axis=0)
        fail(REASON_ORDER.index(IneligibleReason.LOW_ADV), ~(adv >= rules.min_adv20_dollar))

    # A split executed in ``(session e-60, session e]`` moves the raw path inside the window.
    split_in_window = (view.split_count[e] - view.split_count[e - seasoning]) > 0
    fail(REASON_ORDER.index(IneligibleReason.SPLIT_WINDOW), split_in_window)

    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = price[e - seasoning + 1:e + 1] / price[e - seasoning:e]
        suspect = ((ratio >= rules.ca_suspect_ratio) | (ratio <= 1.0 / rules.ca_suspect_ratio)).any(axis=0)
    fail(REASON_ORDER.index(IneligibleReason.CA_SUSPECT), suspect)
    return Eligibility(e, reason < 0, reason)


def library_end_indices(rules: AnalogRules, first: int, last: int) -> tuple[int, ...]:
    """Stride anchor 0 on the grid (D0 ``library_stride_rule``); 61 bars make 60 the first candidate."""
    start = max(first, rules.seasoning_sessions)
    stride = rules.library_stride
    begin = start + (-start) % stride
    return tuple(range(begin, last + 1, stride))
