"""The display payload: the collector's own figures, its own coverage words, nothing added.

The rules this module exists to keep, in the order they matter:

1. **No direction, no score.** There is no LONG/SHORT verdict here and no composite number. Flow
   imbalance and band imbalance are the contract's own normalized ratios of two observed sides,
   carried through unchanged; they are not a rating of anything.
2. **A value whose coverage is not COMPLETE is never shown as a value.** The contract already
   splits every band into `qty`/`notional` (canonical, COMPLETE only) and
   `observed_qty`/`observed_notional` (a labelled lower bound). This module keeps that split in
   the payload instead of flattening it, because flattening it is how a lower bound becomes a
   number somebody trades on.
3. **"Nearest" is only said when the candidate set is provably whole.** If the reconstruction in
   `wallstate` could not be verified, the nearest-wall fields are null with a reason, and the
   chart overlay drops its wall lines. A nearest wall computed from an incomplete set is not
   approximately right, it is wrong in the one direction that matters.
4. **A figure the contract cannot produce is reported as unavailable with its reason**, not
   omitted and not substituted. Mark price is the example: the V0 contract reaches exactly three
   public endpoints, none of which carries a mark, and the book's base is explicitly mid and
   "never last trade/mark/index". So `mark` is null and says so.

One derived quantity is computed here rather than read, and it needs stating plainly. The journal
persists wall candidates by transition, so a resting candidate's row is its OPENED row and that
row's `persistence_ms` is 0 and its `samples` is 1 forever. The span a resting candidate has
actually been observed for is therefore `latest sample time - first_seen_ms`: the same "sampled
observation span" the contract defines, measured against the newest sample this viewer can see.
Because each kind's writer flushes on its own second, the wall stream can trail the derived stream
by up to a flush, so the span can be up to about a second generous; `wall_stream_lag_ms` is
published next to it rather than hidden.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..market_structure_v0 import book as B
from ..market_structure_v0.contract import (BANDS, COMPLETE, DEPTH_STALE_MS, PARTIAL,
                                            SAMPLE_INTERVAL_S, TRADE_STALE_MS, UNKNOWN)
from ..market_structure_v0.envelope import decimal_out
from . import PREVIEW_VERSION
from . import checkpoint as CP
from . import continuity as CN
from . import wallrule as R
from .wallstate import WallFilter, WallSet

#: Preview-level feed states. The user-facing vocabulary of the quality strip.
LIVE = "LIVE"
STALE = "STALE"
SYNCING = "SYNCING"
NO_DATA = "NO_DATA"

#: How old the newest `derived` row may be before the *journal* is the stale thing, rather than
#: the market. The collector samples once a second and its writers flush once a second, so two
#: sample intervals plus a flush is the first age that cannot be explained by buffering.
JOURNAL_STALE_MS = int(SAMPLE_INTERVAL_S * 1000) * 3

# ----------------------------------------------------------------- operator-facing prose
#
# Every sentence the screen shows *about* the data lives here, in a constant whose name ends in
# `_NOTE`. That naming is load-bearing: `tests/crypto/test_liquidity_map_isolation.py` scans the
# executable source for banned vocabulary (`score`, `spoof`, `LONG`, ...) and strips these
# assignments before scanning, because these are the sentences that *declare* the bans and a raw
# scan would fail on its own documentation. A companion test asserts each of them still says what
# it is supposed to say, so the exclusion is not a hole. Prose that needs one of those words
# therefore belongs in a `_NOTE` constant and nowhere else.

SCOPE_NOTE = "표시 전용. 방향 판정·score·주문 경로 없음."
MARK_UNAVAILABLE_NOTE = ("V0 계약은 공개 엔드포인트 3개만 읽고 그 중 mark price를 주는 것이 "
                         "없습니다. 책의 기준은 mid 고정이며 계약이 last trade/mark/index "
                         "사용을 금지합니다.")
NO_VERDICT_NOTE = "후보는 큰 잔량 관측이며 spoofing/absorption/iceberg 판정이 아닙니다"
WALL_RULE_NOTE = ("후보는 ±1% 안에서 찾지만 limit=1000 스냅샷의 알려진 구간이 ±0.15% 수준이라 "
                  "실제 후보는 그 구간 안에만 존재하며 coverage는 PARTIAL로 기록됩니다")
IMBALANCE_NOTE = "imbalance는 두 관측 측면의 정규화 비율이며 방향 판정이 아닙니다"
COVERAGE_SCOPE_NOTE = ("coverage는 이 collector의 스트림 관측 범위이며 거래소가 전부 발행했다는 "
                       "보장이 아닙니다")
OBSERVED_RANGE_NOTE = ("limit=1000 스냅샷이 실제로 닿은 구간만 관측된 것입니다. 이 구간 밖은 "
                       "유동성이 0인 것이 아니라 보지 못한 것이며 그래서 값이 null입니다")
LOWER_BOUND_NOTE = ("PARTIAL 구간의 숫자는 관측된 하한값이고 ≥ 로 표시합니다. 정규 값은 "
                    "COMPLETE일 때만 존재합니다")
IDENTICAL_BOUNDS_NOTE = ("±0.25/0.5/1% 하한값이 서로 같은 것은 정상입니다. 관측 구간이 ±0.15% "
                         "수준이라 세 구간이 같은 레벨만 더하기 때문이며 값이 멈춘 것이 아닙니다")
WALL_RULE_V2_NOTE = ("wall 판정은 동결된 lm-wall.v2 규칙이며 화면에서 바꿀 수 없습니다. notional "
                     "필터만 운영자가 움직이는 표시 범위입니다")
RESNAPSHOT_NOTE = ("재스냅샷은 고정 주기 폴링이 아닙니다. gap·재접속·stale·crossed는 즉시, "
                   "관측 구간이 ±0.1% 약속에 가까워지면 쿨다운 안에서 1회, 그 밖에는 1시간 "
                   "안전 갱신입니다. 재스냅샷은 generation을 올려 모든 wall 관측을 UNKNOWN으로 "
                   "끝내므로 빈도는 그 손실로 묶여 있습니다")
CONTINUITY_NOTE = ("재스냅샷은 HARD와 SOFT로 나뉩니다. gap·재접속·stale·crossed·overflow는 HARD이고 "
                   "wall 관측 이력을 반드시 종료합니다. coverage edge와 안전 갱신은 게이트 5개가 "
                   "전부 통과할 때만 SOFT이고, 그때만 동일 side·동일 가격이 새 스냅샷에 존재함을 "
                   "증명한 후보의 관측 구간을 이어 갑니다. 증명이 없으면 HARD입니다. 이 규칙은 "
                   "lm-wall.v2 임계값을 바꾸지 않고 R4가 보는 구간만 늘립니다. order_identity_proven "
                   "은 이어진 wall에서도 false입니다.")
STATE_FILE_NOTE = ("활성 wall 집합은 collector가 원자적으로 교체하는 compact 상태 파일에서 "
                   "읽습니다. 저널은 여전히 유일한 재생 권위이며 이 파일은 캐시입니다")

#: Short reasons a field is missing. Codes for the screen, not prose about the scope.
NO_WALL_SET = "wall 후보 집합을 완전하다고 증명하지 못했습니다"
NO_BOOK = "사용 가능한 책이 없습니다"

SIDE_ASK = "ASK"
SIDE_BID = "BID"


def _d(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except Exception:  # noqa: BLE001 - any unparseable value is simply absent
        return None
    return parsed if parsed.is_finite() else None


def _pct(numerator: Decimal | None, denominator: Decimal | None) -> str | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return decimal_out((numerator / denominator * Decimal(100)).quantize(Decimal("0.0001")))


def _bps(numerator: Decimal | None, denominator: Decimal | None) -> str | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return decimal_out((numerator / denominator * Decimal(10_000)).quantize(Decimal("0.01")))


# --------------------------------------------------------------------------- quality


def quality_view(*, derived: dict[str, Any] | None, journal_age_ms: int | None,
                 session_ended: bool, trade_stream: dict[str, Any] | None) -> dict[str, Any]:
    """LIVE / STALE / SYNCING / NO_DATA plus every reason that produced it."""
    reasons: list[str] = []
    if derived is None:
        return {"state": NO_DATA, "reasons": ["NO_DERIVED_SAMPLE"], "sample_is_current": False,
                "book_state": UNKNOWN,
                "book_coverage": UNKNOWN, "trade_state": NO_DATA, "depth_age_ms": None,
                "trade_age_ms": None, "journal_age_ms": journal_age_ms, "lag_ms": None,
                "lag_state": UNKNOWN, "generation": None, "levels": None,
                "last_invalidation": None, "journal_stale_ms": JOURNAL_STALE_MS,
                "depth_stale_ms": DEPTH_STALE_MS, "trade_stale_ms": TRADE_STALE_MS}

    book = derived.get("book") or {}
    book_state = str(book.get("state") or UNKNOWN)
    fresh = bool(book.get("fresh"))
    depth_age = book.get("age_ms")

    state = LIVE
    # Whether the sample on screen describes now. Everything below that reads as a claim about
    # the present - "the book is synchronized", "the trade stream is live" - is only true as of
    # the sample it came from, and a sample from a collector that stopped 30 seconds ago says
    # nothing about the present at all.
    sample_is_current = True
    if session_ended:
        reasons.append("SESSION_ENDED")
        state = STALE
        sample_is_current = False
    if journal_age_ms is not None and journal_age_ms > JOURNAL_STALE_MS:
        reasons.append("JOURNAL_STALE")
        state = STALE
        sample_is_current = False
    if book_state == B.UNSYNCED:
        reasons.append("BOOK_UNSYNCED")
        state = STALE if state == STALE else SYNCING
    elif book_state in (B.STALE, B.CROSSED_BOOK):
        reasons.append(f"BOOK_{book_state}")
        state = STALE
    elif not fresh:
        reasons.append("BOOK_NOT_FRESH")
        state = STALE

    trade = trade_stream or {}
    trade_age = trade.get("age_ms")
    if not sample_is_current:
        # The stream's age was measured when the sample was written. Carrying "LIVE" forward from
        # a sample nobody is updating is how a dead collector looks healthy.
        trade_state = STALE
        reasons.append("SAMPLE_NOT_CURRENT")
    elif not trade.get("connected"):
        trade_state = STALE
        reasons.append("TRADE_STREAM_DISCONNECTED")
    elif trade_age is None:
        trade_state = SYNCING
    elif trade_age > TRADE_STALE_MS:
        trade_state = STALE
        reasons.append("TRADE_STREAM_STALE")
    else:
        trade_state = LIVE

    # The book's coverage is the best any band achieved: the +-0.1% band is COMPLETE on a
    # `limit=1000` snapshot and the wider ones are not, so a single word for "the book" would be
    # a lie in one direction or the other. This reports the best and the bands report themselves.
    coverages = {str((band.get("bid") or {}).get("coverage")) for band in book.get("bands") or []}
    coverages |= {str((band.get("ask") or {}).get("coverage")) for band in book.get("bands") or []}
    book_coverage = (COMPLETE if COMPLETE in coverages
                     else PARTIAL if PARTIAL in coverages else UNKNOWN)

    return {"state": state, "reasons": reasons, "sample_is_current": sample_is_current,
            "book_state": book_state,
            "book_coverage": book_coverage, "trade_state": trade_state,
            "depth_age_ms": depth_age, "trade_age_ms": trade_age,
            "journal_age_ms": journal_age_ms, "lag_ms": book.get("lag_ms"),
            "lag_state": book.get("lag_state") or UNKNOWN, "generation": book.get("generation"),
            "levels": book.get("levels"), "last_invalidation": book.get("last_invalidation"),
            "journal_stale_ms": JOURNAL_STALE_MS, "depth_stale_ms": DEPTH_STALE_MS,
            "trade_stale_ms": TRADE_STALE_MS}


# --------------------------------------------------------------------------- price


def price_view(derived: dict[str, Any] | None) -> dict[str, Any]:
    """bid / ask / spread / mid, and mark reported as unavailable with its reason."""
    book = (derived or {}).get("book") or {}
    bid, ask = _d(book.get("best_bid")), _d(book.get("best_ask"))
    mid = _d(book.get("mid"))
    low, high = _d(book.get("known_low")), _d(book.get("known_high"))
    spread = None if bid is None or ask is None else ask - bid
    return {
        "best_bid": book.get("best_bid"),
        "best_ask": book.get("best_ask"),
        "spread": decimal_out(spread),
        "spread_bps": _bps(spread, mid),
        "mid": book.get("mid"),
        "mid_rule": "BEST_BID_AND_BEST_ASK",
        "mark": None,
        "mark_unavailable_reason": MARK_UNAVAILABLE_NOTE,
        "known_low": book.get("known_low"),
        "known_high": book.get("known_high"),
        "known_low_pct": _pct(None if low is None or mid is None else low - mid, mid),
        "known_high_pct": _pct(None if high is None or mid is None else high - mid, mid),
        "source_u": book.get("source_u"),
        "snapshot_update_id": book.get("snapshot_update_id"),
        "event_ms": book.get("event_ms"),
        "receive_ms": book.get("receive_ms"),
    }


# --------------------------------------------------------------------------- depth bands


def _band_side(band: dict[str, Any], side_key: str) -> dict[str, Any]:
    side = band.get(side_key) or {}
    coverage = str(side.get("coverage") or UNKNOWN)
    return {
        "band_pct": band.get("band_pct"),
        "coverage": coverage,
        "qty": side.get("qty"),
        "notional": side.get("notional"),
        "observed_qty": side.get("observed_qty"),
        "observed_notional": side.get("observed_notional"),
        "is_lower_bound": bool(side.get("observed_is_lower_bound")),
        "levels": side.get("levels"),
        "price_low": side.get("price_low"),
        "price_high": side.get("price_high"),
        "imbalance_btc": band.get("imbalance_btc"),
        "imbalance_usdt": band.get("imbalance_usdt"),
    }


def depth_view(derived: dict[str, Any] | None, side: str) -> list[dict[str, Any]]:
    """Every contract band for one side, in contract order, coverage included."""
    book = (derived or {}).get("book") or {}
    bands = book.get("bands") or []
    by_label = {str(band.get("band_pct")): band for band in bands}
    key = "ask" if side == SIDE_ASK else "bid"
    out: list[dict[str, Any]] = []
    for label, _ in BANDS:
        band = by_label.get(label)
        if band is None:
            out.append({"band_pct": label, "coverage": UNKNOWN, "qty": None, "notional": None,
                        "observed_qty": None, "observed_notional": None, "is_lower_bound": False,
                        "levels": None, "price_low": None, "price_high": None,
                        "imbalance_btc": None, "imbalance_usdt": None})
            continue
        out.append(_band_side(band, key))
    return out


# --------------------------------------------------------------------------- walls


def wall_row(payload: dict[str, Any], *, mid: Decimal | None, latest_sample_ms: int | None
             ) -> dict[str, Any]:
    """One V0 candidate as the raw-candidate table shows it, before the V2 rule is applied.

    Kept because the rule's accounting is only meaningful if the thing being accounted for can be
    inspected. The one computed field is the observed span: a resting candidate's journal row is
    its OPENED row, whose `persistence_ms` is 0 and whose `samples` is 1 forever, so the span has
    to be measured against the newest sample this viewer can see.
    """
    price = _d(payload.get("price"))
    distance = None
    if price is not None and mid is not None:
        distance = (price - mid) if payload.get("side") == SIDE_ASK else (mid - price)
    return {
        "side": payload.get("side"),
        "price": payload.get("price"),
        "qty_btc": payload.get("qty"),
        "notional_usdt": payload.get("notional"),
        "distance": decimal_out(distance),
        "distance_pct": _pct(distance, mid),
        "distance_bps": _bps(distance, mid),
        "multiple": payload.get("multiple"),
        "local_average": payload.get("local_average"),
        "neighbours": payload.get("neighbours"),
        "first_seen_ms": payload.get("first_seen_ms"),
        "observed_persistence_ms": R.observed_persistence_ms(payload, latest_sample_ms),
        "persistence_rule": "LATEST_SAMPLE_MS_MINUS_FIRST_SEEN_MS",
        "row_persistence_ms": payload.get("persistence_ms"),
        "row_samples": payload.get("samples"),
        "coverage": payload.get("coverage"),
        "generation": payload.get("generation"),
        # The collector's continuity ledger for this exact level, passed through unchanged. The
        # candidate table is where the rule's accounting is checked, so the evidence the rule
        # acted on has to be inspectable here rather than only in its conclusion.
        "continuity_status": CN.member_status(payload),
        "continuity_first_seen_ms": payload.get("continuity_first_seen_ms"),
        "carried_persistence_ms": CN.carried_span_ms(payload, latest_sample_ms),
        "continuity_samples": payload.get("continuity_samples"),
        "continuity_refreshes": payload.get("continuity_refreshes"),
        "continuity_origin_generation": payload.get("continuity_origin_generation"),
        "persistence_is_sampled_span": True,
        "order_identity_proven": False,
    }


def side_walls(rows: list[dict[str, Any]], side: str, *, mid: Decimal | None,
               wall_set: WallSet, wall_filter: WallFilter, latest_sample_ms: int | None,
               limit: int, known_low: Decimal | None = None,
               known_high: Decimal | None = None) -> dict[str, Any]:
    """One side: the frozen rule applied, then the operator's filter, nearest first.

    Three numbers are published rather than one, because they answer different questions and
    collapsing them is how a screen misleads. `candidates_total` is what V0 qualified - the
    superset, mostly ordinary book. `walls_selected` is what `lm-wall.v2` called a wall.
    `walls_shown` is what survived the operator's notional filter, which is the only one of the
    three they can move. A screen showing nothing because the filter is high must not look like a
    market with no walls in it.
    """
    selection = R.select(rows, side=side, mid=mid, latest_sample_ms=latest_sample_ms,
                         known_low=known_low, known_high=known_high,
                         values_as_of=wall_set.values_as_of,
                         carried_span=CN.carried_span_ms,
                         wall_continuity=CN.wall_continuity)
    kept = [wall for wall in selection.walls if wall_filter.keeps(wall)]
    # "Nearest" is a claim about the whole set, so it is only said when the set is whole. An
    # incomplete set's nearest wall is not approximately right: the walls it misses are the
    # longest-resting ones, which are the ones being asked about.
    provable = wall_set.coverage == COMPLETE and mid is not None
    nearest = kept[0] if (provable and kept) else None
    return {
        "side": side,
        "coverage": wall_set.coverage if mid is not None else UNKNOWN,
        "nearest_wall": nearest,
        "nearest_unavailable_reason": None if provable else (
            NO_BOOK if mid is None else NO_WALL_SET),
        "candidates_total": selection.considered,
        "walls_selected": len(selection.walls),
        "walls_shown": len(kept),
        "selection": selection.view(),
        "walls": kept[:limit],
    }


def wall_summary(wall_set: WallSet, wall_filter: WallFilter,
                 sides: dict[str, Any] | None = None) -> dict[str, Any]:
    """The rule, where the set came from, and the accounting for everything refused."""
    rejected: dict[str, int] = {reason: 0 for reason in R.REJECT_REASONS}
    selected = 0
    for side in (sides or {}).values():
        for reason, count in ((side.get("selection") or {}).get("rejected") or {}).items():
            rejected[reason] = rejected.get(reason, 0) + count
        selected += side.get("walls_selected") or 0
    state = wall_set.state
    return {
        "coverage": wall_set.coverage,
        "verified_by": wall_set.verified_by,
        "unverified_reason": wall_set.unverified_reason,
        "source": ("COLLECTOR_STATE_CHECKPOINT" if state is not None and state.usable
                   else "COLLECTOR_STATE_CHECKPOINT_STALE" if state is not None
                   else "JOURNAL_RECONSTRUCTION"),
        "values_as_of": wall_set.values_as_of,
        "candidate_count": wall_set.count,
        "walls_selected": selected,
        "rejected": rejected,
        "truncated": wall_set.truncated,
        "authority_active": wall_set.authority_active,
        "authority_seq": wall_set.authority_seq,
        "authority_age_ms": wall_set.authority_age_ms,
        "reconstructed_at_authority": wall_set.reconstructed_at_authority,
        "missing_count": wall_set.missing_count,
        "missing_retired": wall_set.missing_retired,
        "scanned_bytes": wall_set.scanned_bytes,
        "scanned_records": wall_set.scanned_records,
        "tail_records": wall_set.tail_records,
        "tail_bytes": wall_set.tail_bytes,
        "tail_complete": wall_set.tail_complete,
        "window_first_receive_ms": wall_set.window_first_receive_ms,
        "transitions_applied": wall_set.transitions_applied,
        "carried": sum(1 for row in wall_set.rows()
                       if CN.member_status(row) == CN.CARRY_CARRIED),
        "state_file": None if state is None else state.view(),
        "state_file_note": STATE_FILE_NOTE,
        "filter": wall_filter.view(),
        "rule": R.rule_view(),
        "continuity_rule": CN.rule_view(),
        "rule_note": WALL_RULE_V2_NOTE,
        "v0_rule_note": WALL_RULE_NOTE,
        "no_verdict_note": NO_VERDICT_NOTE,
    }


# --------------------------------------------------------------------------- coverage


def coverage_view(derived: dict[str, Any] | None) -> dict[str, Any]:
    """What the book was actually observed to reach, stated before anything is read off it.

    This block exists because of a specific way the V1 screen could be misread. A `limit=1000`
    snapshot reaches about +-0.15% of mid, so three of the four contract bands are **permanently**
    PARTIAL and their observed totals are identical - the same levels summed three times. Three
    equal numbers next to three different band labels reads as a stuck value, and an empty region
    on a chart reads as empty book. Neither is what the data says.

    So the observed interval is published as a first-class figure, every PARTIAL value is marked
    as a lower bound, and the reason the wider bands agree is stated in words on the screen
    rather than left to be rediscovered.
    """
    book = (derived or {}).get("book") or {}
    mid = _d(book.get("mid"))
    low, high = _d(book.get("known_low")), _d(book.get("known_high"))
    low_pct = _pct(None if low is None or mid is None else low - mid, mid)
    high_pct = _pct(None if high is None or mid is None else high - mid, mid)
    symmetric = None
    if low is not None and high is not None and mid is not None and mid > 0:
        reach = min(mid - low, high - mid)
        symmetric = _pct(reach, mid)

    bands: list[dict[str, Any]] = []
    by_label = {str(band.get("band_pct")): band for band in (book.get("bands") or [])}
    for label, _ in BANDS:
        band = by_label.get(label) or {}
        bid, ask = band.get("bid") or {}, band.get("ask") or {}
        states = {str(bid.get("coverage") or UNKNOWN), str(ask.get("coverage") or UNKNOWN)}
        # The worst of the two sides, because a band is only as complete as its weaker side.
        worst = (UNKNOWN if UNKNOWN in states else PARTIAL if PARTIAL in states else COMPLETE)
        bands.append({
            "band_pct": label,
            "coverage": worst,
            "bid_coverage": str(bid.get("coverage") or UNKNOWN),
            "ask_coverage": str(ask.get("coverage") or UNKNOWN),
            "is_lower_bound": worst == PARTIAL,
            "qty_bid": bid.get("qty"), "qty_ask": ask.get("qty"),
            "observed_qty_bid": bid.get("observed_qty"),
            "observed_qty_ask": ask.get("observed_qty"),
            "observed_notional_bid": bid.get("observed_notional"),
            "observed_notional_ask": ask.get("observed_notional"),
        })
    partial = [band for band in bands if band["coverage"] == PARTIAL]
    identical = (len(partial) > 1 and len({
        (band["observed_notional_bid"], band["observed_notional_ask"]) for band in partial}) == 1)
    return {
        "observed_low_pct": low_pct,
        "observed_high_pct": high_pct,
        "observed_symmetric_pct": symmetric,
        "known_low": book.get("known_low"),
        "known_high": book.get("known_high"),
        "snapshot_limit": 1000,
        "bands": bands,
        "complete_bands": [band["band_pct"] for band in bands
                           if band["coverage"] == COMPLETE],
        "partial_bands": [band["band_pct"] for band in partial],
        "lower_bound_marker": "\u2265",
        "lower_bounds_identical": identical,
        "unobserved_is_null_not_zero": True,
        "observed_range_note": OBSERVED_RANGE_NOTE,
        "lower_bound_note": LOWER_BOUND_NOTE,
        "identical_bounds_note": IDENTICAL_BOUNDS_NOTE,
        "scope_note": COVERAGE_SCOPE_NOTE,
    }


# --------------------------------------------------------------------------- resnapshot


def resnapshot_view(state: CP.StateFile | None) -> dict[str, Any]:
    """The collector's own resnapshot policy and counters, or the reason they are unavailable.

    The policy is a property of the collector, not of this viewer, so it is reported rather than
    restated: a viewer that printed its own idea of the policy would keep printing it after the
    collector's changed.
    """
    if state is None or not state.present or not state.resnapshot:
        return {"available": False,
                "unavailable_reason": (CP.MISSING if state is None or not state.present
                                       else state.reason or "NO_POLICY_IN_STATE_FILE"),
                "note": RESNAPSHOT_NOTE}
    published = dict(state.resnapshot)
    published["available"] = True
    published["unavailable_reason"] = None
    published["stale"] = not state.usable
    published["note"] = RESNAPSHOT_NOTE
    return published


def continuity_view(wall_set: WallSet) -> dict[str, Any]:
    """The HARD/SOFT ledger: what the last transition did, and what it has done in total.

    The three counts are published together on purpose. A reader shown only the carries cannot
    tell a book whose walls genuinely rested from a rule that is carrying everything, and the
    two look identical on a ladder.
    """
    published = CN.ledger_view(wall_set.continuity)
    published["note"] = CONTINUITY_NOTE
    published["active_carried_in_view"] = sum(
        1 for row in wall_set.rows() if CN.member_status(row) == CN.CARRY_CARRIED)
    published["candidates_in_view"] = wall_set.count
    return published


# --------------------------------------------------------------------------- flow


def flow_view(derived: dict[str, Any] | None) -> dict[str, Any]:
    """The contract's 5 s / 15 s / 60 s windows, unchanged, plus the stream's freshness."""
    payload = derived or {}
    flow = payload.get("flow") or {}
    trade_stream = payload.get("trade_stream") or {}
    windows: dict[str, Any] = {}
    for label in ("5s", "15s", "60s"):
        window = flow.get(label) or {}
        windows[label] = {
            "window": label,
            "coverage": window.get("coverage") or UNKNOWN,
            "coverage_reason": window.get("coverage_reason"),
            "coverage_age_ms": window.get("coverage_age_ms"),
            "trades": window.get("trades"),
            "buy_btc": window.get("buy_btc"),
            "sell_btc": window.get("sell_btc"),
            "buy_usdt": window.get("buy_usdt"),
            "sell_usdt": window.get("sell_usdt"),
            "net_btc": window.get("net_btc"),
            "net_usdt": window.get("net_usdt"),
            "observed_buy_btc": window.get("observed_buy_btc"),
            "observed_sell_btc": window.get("observed_sell_btc"),
            "observed_buy_usdt": window.get("observed_buy_usdt"),
            "observed_sell_usdt": window.get("observed_sell_usdt"),
            "is_lower_bound": bool(window.get("observed_is_lower_bound")),
            "imbalance_btc": window.get("imbalance_btc"),
            "imbalance_usdt": window.get("imbalance_usdt"),
        }
    return {
        "windows": windows,
        "aggressor_rule": "aggressor=SELL if buyer is maker (m=true), else BUY",
        "clock": "LOCAL_MONOTONIC_RECEIPT",
        "trade_stream": {"connected": bool(trade_stream.get("connected")),
                         "age_ms": trade_stream.get("age_ms"),
                         "last_receive_ms": trade_stream.get("last_receive_ms"),
                         "stale_ms": TRADE_STALE_MS},
        "imbalance_note": IMBALANCE_NOTE,
    }


# --------------------------------------------------------------------------- chart overlay


def overlay_view(*, derived: dict[str, Any] | None, ask: dict[str, Any], bid: dict[str, Any],
                 quality: dict[str, Any]) -> dict[str, Any]:
    """The lines a chart may draw, or nothing and the reason why.

    The axis is the snapshot's observed interval rather than a band, because that interval is the
    only range this data can honestly describe: outside it the book is not thin, it is unseen.
    A wider axis would invite reading empty space as empty book, which is the one misreading this
    whole screen is built to prevent.
    """
    book = (derived or {}).get("book") or {}
    mid = _d(book.get("mid"))
    low, high = _d(book.get("known_low")), _d(book.get("known_high"))
    renderable = (quality.get("state") == LIVE and mid is not None and low is not None
                  and high is not None and low < high)
    if not renderable:
        return {"renderable": False,
                "suppressed_reason": NO_BOOK if mid is None else "|".join(
                    quality.get("reasons") or ["NOT_LIVE"]),
                "mid": book.get("mid"), "axis_low": book.get("known_low"),
                "axis_high": book.get("known_high"), "sell_wall": None, "buy_wall": None,
                "walls_suppressed_reason": NO_BOOK}
    nearest_ask = ask.get("nearest_wall")
    nearest_bid = bid.get("nearest_wall")
    wall_ok = nearest_ask is not None or nearest_bid is not None

    def line(wall: dict[str, Any] | None) -> dict[str, Any] | None:
        if wall is None:
            return None
        return {"price": wall["price"], "qty_btc": wall["qty_btc"],
                "notional_usdt": wall["notional_usdt"],
                "distance_bps": wall["distance_bps"],
                "bin_low": wall["bin_low"], "bin_high": wall["bin_high"],
                "bin_members": wall["bin_members"], "coverage": wall["coverage"]}

    return {
        "renderable": True,
        "suppressed_reason": None,
        "mid": book.get("mid"),
        "axis_low": book.get("known_low"),
        "axis_high": book.get("known_high"),
        "axis_rule": "SNAPSHOT_KNOWN_INTERVAL",
        "best_bid": book.get("best_bid"),
        "best_ask": book.get("best_ask"),
        "sell_wall": line(nearest_ask),
        "buy_wall": line(nearest_bid),
        "walls_suppressed_reason": None if wall_ok else (
            ask.get("nearest_unavailable_reason") or bid.get("nearest_unavailable_reason")),
    }


# --------------------------------------------------------------------------- assembly


def snapshot_view(*, root: str, session: Any, derived_record: dict[str, Any] | None,
                  wall_set: WallSet, wall_filter: WallFilter, now_ms: int,
                  wall_limit: int = 12, telemetry: list[dict[str, Any]] | None = None,
                  read_cost: dict[str, Any] | None = None) -> dict[str, Any]:
    """Everything one poll of the preview returns."""
    derived = (derived_record or {}).get("payload") if derived_record else None
    sample_ms = (derived_record or {}).get("receive_ms") if derived_record else None
    journal_age = None if not isinstance(sample_ms, int) else max(0, now_ms - sample_ms)
    quality = quality_view(derived=derived, journal_age_ms=journal_age,
                           session_ended=bool(session is not None and session.ended),
                           trade_stream=(derived or {}).get("trade_stream"))
    price = price_view(derived)
    mid = _d(price.get("mid"))
    book = (derived or {}).get("book") or {}
    known_low, known_high = _d(book.get("known_low")), _d(book.get("known_high"))
    rows = wall_set.rows()
    latest = sample_ms if isinstance(sample_ms, int) else None
    ask = side_walls(rows, SIDE_ASK, mid=mid, wall_set=wall_set, wall_filter=wall_filter,
                     latest_sample_ms=latest, limit=wall_limit, known_low=known_low,
                     known_high=known_high)
    bid = side_walls(rows, SIDE_BID, mid=mid, wall_set=wall_set, wall_filter=wall_filter,
                     latest_sample_ms=latest, limit=wall_limit, known_low=known_low,
                     known_high=known_high)
    ask["depth"] = depth_view(derived, SIDE_ASK)
    bid["depth"] = depth_view(derived, SIDE_BID)
    sides = {SIDE_ASK: ask, SIDE_BID: bid}

    # How far the wall stream's head trails the sample this view is priced on. With the state
    # checkpoint both come from the same file and this is zero; on the journal fallback it is
    # non-zero and normal, because each kind has its own writer and its own flush and a second
    # with no transition writes no wall row at all.
    wall_lag = None
    if isinstance(sample_ms, int) and isinstance(wall_set.last_receive_ms, int):
        wall_lag = max(0, sample_ms - wall_set.last_receive_ms)

    return {
        "preview": {
            "version": PREVIEW_VERSION,
            "mode": "READ_ONLY_JOURNAL_VIEWER",
            "scope": SCOPE_NOTE,
            "coverage_note": COVERAGE_SCOPE_NOTE,
            "sample_interval_s": SAMPLE_INTERVAL_S,
        },
        "source": {
            "root": root,
            "session_id": None if session is None else session.session_id,
            "session_started_ms": None if session is None else session.started_ms,
            # Observation never crosses a session boundary, so no candidate can have been seen
            # for longer than the session has run. Right after a restart every wall reads with
            # the same observed span, and without this figure that looks like a stuck value.
            "session_age_ms": None if session is None
            else max(0, now_ms - session.started_ms),
            "session_ended": bool(session is not None and session.ended),
            "contract": None if session is None else (session.start_payload.get("contract")),
            "collector_version": (derived_record or {}).get("collector_version"),
            "exchange": (derived_record or {}).get("exchange"),
            "symbol": (derived_record or {}).get("symbol"),
            "seq": (derived_record or {}).get("seq"),
            "sample_index": (derived or {}).get("sample_index"),
            "sample_receive_ms": sample_ms,
            "server_time_ms": now_ms,
            "journal_age_ms": journal_age,
            "wall_stream_lag_ms": wall_lag,
            "read_cost": read_cost or {},
        },
        "quality": quality,
        "price": price,
        "coverage": coverage_view(derived),
        "resnapshot": resnapshot_view(wall_set.state),
        "continuity": continuity_view(wall_set),
        "sides": sides,
        "flow": flow_view(derived),
        "walls": wall_summary(wall_set, wall_filter, sides),
        "candidates": {
            SIDE_ASK: [wall_row(payload, mid=mid, latest_sample_ms=latest)
                       for payload in rows if payload.get("side") == SIDE_ASK][:wall_limit],
            SIDE_BID: [wall_row(payload, mid=mid, latest_sample_ms=latest)
                       for payload in rows if payload.get("side") == SIDE_BID][:wall_limit],
        },
        "overlay": overlay_view(derived=derived, ask=ask, bid=bid, quality=quality),
        "telemetry": [
            {"seq": record.get("seq"), "receive_ms": record.get("receive_ms"),
             "event": (record.get("payload") or {}).get("event"),
             "stream": (record.get("payload") or {}).get("stream"),
             "reason": (record.get("payload") or {}).get("reason"),
             "refresh_type": (record.get("payload") or {}).get("refresh_type")}
            for record in (telemetry or [])
        ],
    }


def empty_view(*, root: str, now_ms: int, reason: str) -> dict[str, Any]:
    """What the preview returns when there is no journal to read at all."""
    from .wallstate import DEFAULT_WALL_FILTER
    view = snapshot_view(root=root, session=None, derived_record=None, wall_set=WallSet(),
                         wall_filter=DEFAULT_WALL_FILTER, now_ms=now_ms)
    view["quality"]["reasons"] = [reason]
    view["source"]["unavailable_reason"] = reason
    return view


__all__ = ["LIVE", "STALE", "SYNCING", "NO_DATA", "JOURNAL_STALE_MS",
           "SCOPE_NOTE", "MARK_UNAVAILABLE_NOTE", "NO_VERDICT_NOTE", "WALL_RULE_NOTE",
           "IMBALANCE_NOTE", "COVERAGE_SCOPE_NOTE", "OBSERVED_RANGE_NOTE", "LOWER_BOUND_NOTE",
           "IDENTICAL_BOUNDS_NOTE", "WALL_RULE_V2_NOTE", "RESNAPSHOT_NOTE", "STATE_FILE_NOTE",
           "CONTINUITY_NOTE", "NO_WALL_SET", "NO_BOOK",
           "quality_view", "price_view", "depth_view", "flow_view", "wall_row", "side_walls",
           "wall_summary", "coverage_view", "resnapshot_view", "continuity_view", "overlay_view",
           "snapshot_view", "empty_view"]
