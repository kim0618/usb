"""Layer DERIVATIVES: open interest, funding and basis, read raw and per symbol.

Everything here is a figure the venue publishes, shown with its own timestamp. The one piece of
arithmetic is `basis = mark - index` and its percentage restatement, which the display contract asks
for by name. There is no code in this module that takes two of these fields and returns a third
reading: no open-interest-against-price, no funding regime, no crowding measure, and no combination
with any other layer. That is the section 8 ban, and it is satisfied structurally - there is simply
no such function to call.

Open-interest *change* is a difference of the venue's own closed 5 m buckets rather than a
difference of this process's own polls. The difference matters on a restart: a poll-difference has
nothing to show for its first hour and then shows a number whose window depends on when the panel
happened to start, whereas a bucket-difference is the same figure whoever asks and is available on
the first read.

This layer is per symbol with no shared state, which is why it is the only layer that exists for
ETHUSDT and SOLUSDT: there is no BTC figure here that could leak into another instrument.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from . import contract as C
from . import venue as VN

#: How many 5 m buckets to ask for. 4 h needs 48 closed buckets plus the newest one to difference
#: against, and the endpoint's own cap is 500.
OI_HIST_LIMIT = 60

#: Each reported horizon, as a count of 5 m buckets back from the newest closed one.
OI_WINDOW_BUCKETS: dict[str, int] = {"5m": 1, "15m": 3, "1h": 12, "4h": 48}


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed.is_finite() else None


def _str(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


@dataclass
class DerivativesRead:
    """One completed read, with each endpoint's own failure kept separate.

    Separate on purpose: funding arriving while open interest fails is a perfectly normal reading
    and the panel shows the half it has. One combined error would blank both.
    """

    symbol: str
    read_at_ms: int
    premium: dict[str, Any] | None = None
    premium_error: str | None = None
    open_interest: dict[str, Any] | None = None
    open_interest_error: str | None = None
    hist: list[dict[str, Any]] | None = None
    hist_error: str | None = None


def read(symbol: str, now_ms: int, *, opener: Any = None) -> DerivativesRead:
    """Three public GETs. Each failure is recorded and none of them aborts the others."""
    out = DerivativesRead(symbol=symbol, read_at_ms=now_ms)
    try:
        out.premium = VN.premium_index(symbol, opener=opener)
    except VN.VenueError as caught:
        out.premium_error = str(caught)
    try:
        out.open_interest = VN.open_interest(symbol, opener=opener)
    except VN.VenueError as caught:
        out.open_interest_error = str(caught)
    try:
        out.hist = VN.open_interest_hist(symbol, C.OI_HIST_PERIOD, OI_HIST_LIMIT, opener=opener)
    except VN.VenueError as caught:
        out.hist_error = str(caught)
    return out


def _oi_changes(hist: list[dict[str, Any]] | None,
                error: str | None) -> dict[str, Any]:
    """Change in open interest over each horizon, differenced on the venue's closed buckets.

    Ascending input is asserted rather than assumed: the endpoint returns oldest first today, and
    a silent reversal would flip the sign of every row here.
    """
    if not hist:
        return {"available": False, "unavailable_reason": error or "NO_HISTORY",
                "windows": {label: None for label in C.OI_CHANGE_WINDOWS},
                "period": C.OI_HIST_PERIOD, "buckets": 0}
    stamps = [int(row.get("timestamp") or 0) for row in hist]
    if stamps != sorted(stamps):
        return {"available": False, "unavailable_reason": "HISTORY_NOT_ASCENDING",
                "windows": {label: None for label in C.OI_CHANGE_WINDOWS},
                "period": C.OI_HIST_PERIOD, "buckets": len(hist)}
    newest = hist[-1]
    now_oi = _dec(newest.get("sumOpenInterest"))
    now_value = _dec(newest.get("sumOpenInterestValue"))
    windows: dict[str, Any] = {}
    for label in C.OI_CHANGE_WINDOWS:
        back = OI_WINDOW_BUCKETS[label]
        index = len(hist) - 1 - back
        if index < 0 or now_oi is None:
            windows[label] = {"available": False,
                              "unavailable_reason": "NOT_ENOUGH_CLOSED_BUCKETS"}
            continue
        then = hist[index]
        then_oi = _dec(then.get("sumOpenInterest"))
        then_value = _dec(then.get("sumOpenInterestValue"))
        if then_oi is None:
            windows[label] = {"available": False, "unavailable_reason": "BUCKET_HAS_NO_VALUE"}
            continue
        delta = now_oi - then_oi
        windows[label] = {
            "available": True,
            "unavailable_reason": None,
            "base": _str(delta),
            "base_pct": (None if then_oi == 0 else
                         _str((delta / then_oi * Decimal(100)).quantize(Decimal("0.0001")))),
            "value_usdt": (None if now_value is None or then_value is None
                           else _str(now_value - then_value)),
            "from_bucket_ms": int(then.get("timestamp") or 0),
            "to_bucket_ms": int(newest.get("timestamp") or 0),
        }
    return {"available": True, "unavailable_reason": None, "windows": windows,
            "period": C.OI_HIST_PERIOD, "buckets": len(hist),
            "newest_bucket_ms": int(newest.get("timestamp") or 0),
            "newest_sum_open_interest": newest.get("sumOpenInterest"),
            "newest_sum_open_interest_value": newest.get("sumOpenInterestValue"),
            "source": "CLOSED_VENUE_BUCKETS_NOT_LOCAL_POLLS"}


def derivatives_view(read_result: DerivativesRead | None, *, symbol: str,
                     now_ms: int, reason: str | None = None) -> dict[str, Any]:
    """The DERIVATIVES layer payload for one poll."""
    if read_result is None:
        return {"state": C.UNKNOWN, "reasons": [reason or "NOT_READ_YET"], "symbol": symbol,
                "open_interest": None, "open_interest_change": None, "funding": None,
                "basis": None, "note": C.DERIVATIVES_NOTE}

    premium = read_result.premium or {}
    oi = read_result.open_interest or {}
    mark = _dec(premium.get("markPrice"))
    index = _dec(premium.get("indexPrice"))
    venue_ms = premium.get("time")
    age_ms = (None if not isinstance(venue_ms, int) else max(0, now_ms - int(venue_ms)))

    basis = None if mark is None or index is None else mark - index
    premium_pct = (None if basis is None or index is None or index == 0
                   else (basis / index * Decimal(100)).quantize(Decimal("0.000001")))

    oi_ms = oi.get("time")
    oi_age_ms = (None if not isinstance(oi_ms, int) else max(0, now_ms - int(oi_ms)))
    changes = _oi_changes(read_result.hist, read_result.hist_error)

    reasons: list[str] = []
    for label, error in (("PREMIUM_INDEX", read_result.premium_error),
                         ("OPEN_INTEREST", read_result.open_interest_error),
                         ("OPEN_INTEREST_HIST", read_result.hist_error)):
        if error:
            reasons.append(f"{label}:{error}")

    have_any = bool(premium) or bool(oi) or changes["available"]
    if not have_any:
        state = C.UNKNOWN
    elif read_result.premium_error or read_result.open_interest_error or read_result.hist_error:
        state = C.PARTIAL
    else:
        oldest = max([value for value in (age_ms, oi_age_ms) if value is not None] or [0])
        if oldest > C.DERIVATIVES_STALE_MS:
            state = C.STALE
            reasons.append("VENUE_TIMESTAMP_OLDER_THAN_FRESHNESS_BOUND")
        else:
            state = C.LIVE

    return {
        "state": state,
        "reasons": reasons,
        "symbol": symbol,
        "read_at_ms": read_result.read_at_ms,
        "open_interest": {
            "base": oi.get("openInterest"),
            "venue_time_ms": oi_ms,
            "age_ms": oi_age_ms,
            "unit": "BASE_ASSET",
            "available": bool(oi.get("openInterest")),
            "unavailable_reason": read_result.open_interest_error,
        },
        "open_interest_change": changes,
        "funding": {
            "last_rate": premium.get("lastFundingRate"),
            "last_rate_pct": _str(_dec(premium.get("lastFundingRate")) * Decimal(100)
                                  if _dec(premium.get("lastFundingRate")) is not None else None),
            "interest_rate": premium.get("interestRate"),
            "next_funding_time_ms": premium.get("nextFundingTime"),
            "venue_time_ms": venue_ms,
            "age_ms": age_ms,
            "available": premium.get("lastFundingRate") is not None,
            "unavailable_reason": read_result.premium_error,
        },
        "basis": {
            "mark_price": premium.get("markPrice"),
            "index_price": premium.get("indexPrice"),
            "estimated_settle_price": premium.get("estimatedSettlePrice"),
            "basis_usdt": _str(basis),
            "premium_pct": _str(premium_pct),
            "formula": "basis = mark - index; premium_pct = (mark - index) / index * 100",
            "venue_time_ms": venue_ms,
            "age_ms": age_ms,
            "available": basis is not None,
            "unavailable_reason": read_result.premium_error,
        },
        "note": C.DERIVATIVES_NOTE,
    }


__all__ = ["DerivativesRead", "read", "derivatives_view", "OI_HIST_LIMIT", "OI_WINDOW_BUCKETS"]
