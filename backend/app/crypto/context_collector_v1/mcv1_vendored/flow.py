"""Layer FLOW: aggressive trades, and the two things that may be said about a wall that vanished.

The windows are the Liquidity Map viewer's `flow_view`, unchanged: the V0 contract's 5 s / 15 s /
60 s aggregates with their own coverage, their own lower-bound flags and the aggressor rule stated
by the contract that produced them. Nothing is re-aggregated here.

What this module owns is the part that needs memory. A wall that is in one reading and gone from the
next raises exactly one question - was it eaten or was it pulled - and the honest answer is almost
always "pulled" and sometimes "cannot tell". So the tracker keeps the previous reading's qualifying
bins and the mid path since, and applies Market Context R0's own `traded_through` predicate: for an
ask bin, did the mid path reach `bin_high`; for a bid bin, did it reach `bin_low`. That predicate is
reproduced rather than re-invented, and a test compares it against the research module's on
constructed samples.

Three facts bound every word this layer prints, and all three are measured:

* a near wall is present in ~99% of samples, so presence is not an event;
* of near walls that vanished, the mid path had reached the bin in 3.8% (ask) / 6.9% (bid) of
  cases, so `CANCEL_LIKE` is the normal outcome and `CONSUMED` is not a word this panel owns;
* the whole 3.61 h journal held 15 consumption candidates, so an empty row here is the expected
  reading and must render as `NONE` with its coverage rather than as silence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from . import contract as C

#: The sample cadence the collector writes at. A gap wider than this between two readings means
#: the mid path between them has a hole, and a hole makes the touch test undecidable.
SAMPLE_INTERVAL_MS = 1_000
#: How much slack a poll gets before its interval counts as holed. Two sample intervals: one for
#: the cadence itself and one for scheduling jitter.
PATH_GAP_TOLERANCE_MS = 2 * SAMPLE_INTERVAL_MS
#: The window the absorption candidate is evaluated on. The longest window, because the condition
#: is about size accumulated against a wall rather than about an instant.
ABSORPTION_WINDOW = "60s"


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed.is_finite() else None


def traded_through(path: list[Decimal], *, side: str, bin_low: Decimal,
                   bin_high: Decimal) -> bool:
    """Did the mid path reach into the bin. Market Context R0's predicate, same inequalities.

    For an ask bin the path has to reach up to `bin_high`; for a bid bin, down to `bin_low`. An
    empty path is `False`, which is why the caller must decide `UNKNOWN` *before* calling this: a
    path with no samples is not evidence of no touch.
    """
    if not path:
        return False
    if side == "ASK":
        return max(path) >= bin_high
    return min(path) <= bin_low


@dataclass
class _Bin:
    """One qualifying wall bin as the previous reading saw it."""

    side: str
    bin_low: Decimal
    bin_high: Decimal
    price: str | None
    notional_usdt: str | None
    last_seen_ms: int
    generation: Any


@dataclass
class VanishTracker:
    """Remembers the previous reading so the current one can be compared with it.

    Holds no history beyond the previous reading and the mid path since it, so its memory is
    bounded regardless of how long the panel runs. Everything it cannot establish is `UNKNOWN`
    rather than assumed, and the reason is carried.
    """

    bins: dict[tuple[str, str], _Bin] = field(default_factory=dict)
    #: Mids observed since the previous reading, not including it. Emptied by every `observe`.
    pending: list[Decimal] = field(default_factory=list)
    #: The path the last `observe` actually judged on: `pending` plus that reading's own mid.
    #:
    #: The two halves of that sentence are the whole correctness of the touch test, and getting
    #: either wrong is silent. Market Context R0's predicate reads `mids[anchor+1 : j+1]` - it
    #: **excludes** the sample the wall was last seen in and **includes** the sample it was found
    #: missing in. A first implementation here kept the anchor and dropped the decision sample,
    #: which classified a wall the price had just traded through as `CANCEL_LIKE` - the wrong
    #: answer in the direction of the 96% base rate, so it would have looked right.
    path: list[Decimal] = field(default_factory=list)
    last_sample_ms: int | None = None
    last_session_id: str | None = None
    last_generation: Any = None
    vanished: list[dict[str, Any]] = field(default_factory=list)
    #: Totals since the process started, so a single quiet reading is not mistaken for a quiet
    #: market and the 96% cancel share can be checked against this panel's own tape.
    totals: dict[str, int] = field(
        default_factory=lambda: {C.VANISH_CONSUMED_CANDIDATE: 0, C.VANISH_CANCEL_LIKE: 0,
                                 C.UNKNOWN: 0, "observed_disappearances": 0})

    def _undecidable(self, sample_ms: int | None, session_id: str | None,
                     generation: Any, book_state: str | None) -> str | None:
        if self.last_sample_ms is None or sample_ms is None:
            return "NO_PREVIOUS_READING"
        if session_id != self.last_session_id:
            return "SESSION_BOUNDARY"
        # Compared only when both readings know their generation. An unknown generation is not a
        # changed one: a book with no state file never publishes it, and treating that as a
        # resnapshot would make every disappearance undecidable forever - which reads as "the
        # panel cannot tell" when the truth is "nothing happened".
        if (generation is not None and self.last_generation is not None
                and generation != self.last_generation):
            return "RESNAPSHOT_GENERATION_CHANGED"
        if book_state != "SYNCED":
            return "BOOK_NOT_SYNCED"
        if sample_ms - self.last_sample_ms > PATH_GAP_TOLERANCE_MS:
            return "PATH_GAP_WIDER_THAN_SAMPLE_INTERVAL"
        if sample_ms == self.last_sample_ms:
            return "SAME_SAMPLE_AS_PREVIOUS_READING"
        return None

    def observe(self, liquidity: dict[str, Any]) -> list[dict[str, Any]]:
        """Fold one LIQUIDITY payload in and return what disappeared since the last one."""
        journal = liquidity.get("journal") or {}
        book = liquidity.get("book") or {}
        sample_ms = journal.get("sample_receive_ms")
        session_id = journal.get("session_id")
        book_state = journal.get("book_state")
        mid = _dec(book.get("mid"))

        current: dict[tuple[str, str], _Bin] = {}
        # The collector's own generation where it publishes one, and the rows' otherwise. The
        # fallback is there for a session with no state checkpoint; it is weaker precisely because
        # an emptied set carries no row to read.
        generation: Any = journal.get("book_generation")
        for side_key, side in (liquidity.get("sides") or {}).items():
            for wall in (side.get("walls") or []):
                low, high = _dec(wall.get("bin_low")), _dec(wall.get("bin_high"))
                if low is None or high is None:
                    continue
                generation = wall.get("generation") if generation is None else generation
                current[(side_key, str(wall.get("bin_low")))] = _Bin(
                    side=side_key, bin_low=low, bin_high=high, price=wall.get("price"),
                    notional_usdt=wall.get("notional_usdt"),
                    last_seen_ms=int(sample_ms) if isinstance(sample_ms, int) else 0,
                    generation=wall.get("generation"))

        undecidable = self._undecidable(sample_ms if isinstance(sample_ms, int) else None,
                                        session_id, generation, book_state)
        path = list(self.pending) + ([mid] if mid is not None else [])
        gone: list[dict[str, Any]] = []
        for key, previous in self.bins.items():
            if key in current:
                continue
            self.totals["observed_disappearances"] += 1
            if undecidable is not None:
                state, reason, touched = C.UNKNOWN, undecidable, None
            elif not path:
                state, reason, touched = C.UNKNOWN, "NO_MID_PATH_BETWEEN_READINGS", None
            else:
                touched = traded_through(path, side=previous.side,
                                         bin_low=previous.bin_low, bin_high=previous.bin_high)
                state = C.VANISH_CONSUMED_CANDIDATE if touched else C.VANISH_CANCEL_LIKE
                reason = ("MID_PATH_REACHED_THE_BIN" if touched
                          else "MID_PATH_NEVER_REACHED_THE_BIN")
            self.totals[state] = self.totals.get(state, 0) + 1
            gone.append({"state": state, "reason": reason, "side": previous.side,
                         "bin_low": str(previous.bin_low), "bin_high": str(previous.bin_high),
                         "price": previous.price, "notional_usdt": previous.notional_usdt,
                         "last_seen_ms": previous.last_seen_ms,
                         "observed_at_ms": sample_ms,
                         "path_samples": len(path),
                         "mid_path_touched_bin": touched,
                         "order_identity_proven": False})

        self.bins = current
        self.last_sample_ms = sample_ms if isinstance(sample_ms, int) else None
        self.last_session_id = session_id
        self.last_generation = generation if generation is not None else self.last_generation
        self.path = path
        self.pending = []
        if gone:
            self.vanished = (gone + self.vanished)[:20]
        return gone

    def note_mid(self, liquidity: dict[str, Any]) -> None:
        """Add a mid to the path without ending the interval.

        The panel polls at the collector's cadence, so each poll adds one sample to the path and
        then closes the interval. This exists for a caller that samples the book faster than it
        evaluates walls, so the path stays at the resolution the touch test was measured at.
        """
        mid = _dec((liquidity.get("book") or {}).get("mid"))
        if mid is not None:
            self.pending.append(mid)


# --------------------------------------------------------------------------- absorption candidate


def absorption_candidate(liquidity: dict[str, Any], flow_windows: dict[str, Any],
                         tracker: VanishTracker) -> dict[str, Any]:
    """One row: `ABSORPTION_CANDIDATE` with its inputs, or `NONE` with the reason it is not.

    The condition, stated so it can be argued with: over the 60 s window, aggressive flow pushing
    into one side, in USDT, reached at least the notional of the nearest qualifying wall on that
    side, that wall was present in both readings, and the mid path did not reach into its bin. In
    words: the wall absorbed at least its own size and is still standing.

    The size comparison is what makes this a candidate worth a row. Dropping it leaves "a wall is
    present and buyers are in front" - which is true in about half of all samples, since a near
    wall is present in 99% of them. A row that fires half the time is not a candidate, it is
    wallpaper.

    It is never a verdict. `btc-ms.v0.1` cannot prove order identity, so the same reading is
    produced by a resting wall being hit and by a wall being replenished behind the fills, and
    this panel has no way to separate them. That is in the payload as
    `order_identity_proven: false`, and the value itself carries the word CANDIDATE.
    """
    none = lambda reason: {"state": C.WALL_NONE, "reason": reason,
                           "order_identity_proven": False, "note": C.ABSORPTION_NOTE}
    window = (flow_windows or {}).get(ABSORPTION_WINDOW) or {}
    if str(window.get("coverage")) != C.COMPLETE:
        return none("FLOW_WINDOW_NOT_COMPLETE")
    buy = _dec(window.get("buy_usdt"))
    sell = _dec(window.get("sell_usdt"))
    if buy is None or sell is None:
        return none("FLOW_WINDOW_HAS_NO_USDT_TOTALS")
    pushing_side = "ASK" if buy > sell else "BID"
    pushing_usdt = buy if pushing_side == "ASK" else sell
    side = (liquidity.get("sides") or {}).get(pushing_side) or {}
    wall = side.get("nearest_wall")
    if wall is None:
        return none(f"NO_QUALIFYING_WALL_ON_{pushing_side}")
    notional = _dec(wall.get("notional_usdt"))
    low, high = _dec(wall.get("bin_low")), _dec(wall.get("bin_high"))
    if notional is None or low is None or high is None:
        return none("WALL_HAS_NO_MEASURED_NOTIONAL")
    if pushing_usdt < notional:
        return none("AGGRESSIVE_FLOW_BELOW_WALL_NOTIONAL")
    key = (pushing_side, str(wall.get("bin_low")))
    if key not in tracker.bins:
        return none("WALL_NOT_PRESENT_IN_PREVIOUS_READING")
    # The path the last reading judged on, which is the same interval the wall's survival is
    # being asserted over. Using the still-accumulating `pending` would ask about an interval that
    # has not closed yet.
    if traded_through(tracker.path, side=pushing_side, bin_low=low, bin_high=high):
        return none("MID_PATH_REACHED_THE_BIN")
    return {"state": C.ABSORPTION_CANDIDATE,
            "reason": "AGGRESSIVE_FLOW_AT_OR_ABOVE_WALL_NOTIONAL_AND_WALL_STILL_PRESENT",
            "side": pushing_side,
            "window": ABSORPTION_WINDOW,
            "aggressive_usdt": str(pushing_usdt),
            "wall_notional_usdt": wall.get("notional_usdt"),
            "wall_price": wall.get("price"),
            "bin_low": wall.get("bin_low"),
            "bin_high": wall.get("bin_high"),
            "wall_persistence_ms": wall.get("persistence_ms"),
            "order_identity_proven": False,
            "note": C.ABSORPTION_NOTE}


# --------------------------------------------------------------------------- display projection


def unavailable_view(symbol: str, reason: str) -> dict[str, Any]:
    return {"state": C.UNAVAILABLE, "reasons": [reason], "symbol": symbol,
            "windows": None, "trade_stream": None, "vanished": None, "absorption": None,
            "vanish_base_rate": None, "vanish_note": C.VANISH_NOTE,
            "absorption_note": C.ABSORPTION_NOTE}


def _window_state(window: dict[str, Any], stream_age_ms: Any, journal_state: str) -> str:
    """One window's state, floored by the journal the window came out of.

    The flooring is not belt and braces, it is the whole correctness of this row. Every age inside
    a `flow_view` window is measured from the sample the collector wrote, by design - that is what
    stops a collector which died an hour ago from looking current *within* its own last sample.
    The consequence is that `trade_stream.age_ms` on a 20-hour-old record reads 80 ms and is
    perfectly true: 80 ms before that sample was written. Read on its own it says the trade feed
    is live, which is the exact failure this panel exists to prevent, and it was observed on the
    first live reading of a stale journal rather than reasoned about.
    """
    if journal_state in (C.UNKNOWN, C.STALE):
        return journal_state
    coverage = str(window.get("coverage"))
    if coverage == C.UNKNOWN:
        return C.UNKNOWN
    if isinstance(stream_age_ms, int) and stream_age_ms > C.TRADE_STALE_MS:
        return C.STALE
    return C.LIVE if coverage == C.COMPLETE else C.PARTIAL


def journal_state_of(liquidity: dict[str, Any]) -> tuple[str, str | None]:
    """How fresh the shared journal is, as a state plus the reason. LIQUIDITY has already
    decided this from the same record, so it is read off that decision rather than re-derived."""
    state = str(liquidity.get("state"))
    if state in (C.UNKNOWN, C.UNAVAILABLE):
        return C.UNKNOWN, "JOURNAL_NOT_READABLE"
    if state == C.STALE:
        return C.STALE, "JOURNAL_OLDER_THAN_FRESHNESS_BOUND"
    return C.LIVE, None


def flow_view(snapshot: dict[str, Any] | None, liquidity: dict[str, Any], *, symbol: str,
              tracker: VanishTracker, reason: str | None = None) -> dict[str, Any]:
    """The FLOW layer payload for one poll."""
    if symbol != C.BTC:
        return unavailable_view(symbol, C.REASON_COLLECTOR_BTC_ONLY)
    if snapshot is None or "__error__" in snapshot:
        view = unavailable_view(symbol, reason or "VIEWER_UNREADABLE")
        view["state"] = C.UNKNOWN
        return view
    flow = snapshot.get("flow") or {}
    stream = flow.get("trade_stream") or {}
    raw_windows = flow.get("windows") or {}
    stream_age = stream.get("age_ms")
    connected = bool(stream.get("connected"))
    journal_state, journal_reason = journal_state_of(liquidity)

    windows: dict[str, Any] = {}
    for label in C.FLOW_WINDOWS:
        window = raw_windows.get(label) or {}
        windows[label] = {
            "window": label,
            "state": _window_state(window, stream_age, journal_state),
            "coverage": window.get("coverage"),
            "coverage_reason": window.get("coverage_reason"),
            "coverage_age_ms": window.get("coverage_age_ms"),
            "trades": window.get("trades"),
            "buy_btc": window.get("buy_btc"),
            "sell_btc": window.get("sell_btc"),
            "buy_usdt": window.get("buy_usdt"),
            "sell_usdt": window.get("sell_usdt"),
            "net_btc": window.get("net_btc"),
            "net_usdt": window.get("net_usdt"),
            "imbalance_usdt": window.get("imbalance_usdt"),
            "imbalance_btc": window.get("imbalance_btc"),
            "is_lower_bound": window.get("is_lower_bound"),
        }

    states = {window["state"] for window in windows.values()}
    if not connected or C.UNKNOWN in states:
        state = C.UNKNOWN
    elif C.STALE in states:
        state = C.STALE
    elif C.PARTIAL in states:
        state = C.PARTIAL
    else:
        state = C.LIVE
    reasons: list[str] = []
    if journal_reason is not None:
        reasons.append(journal_reason)
    if not connected:
        reasons.append("TRADE_STREAM_NOT_CONNECTED")
    if journal_state == C.LIVE and isinstance(stream_age, int) \
            and stream_age > C.TRADE_STALE_MS:
        reasons.append("TRADE_STREAM_OLDER_THAN_FRESHNESS_BOUND")

    gone = tracker.observe(liquidity)
    return {
        "state": state,
        "reasons": reasons,
        "symbol": symbol,
        "windows": windows,
        "aggressor_rule": flow.get("aggressor_rule"),
        "clock": flow.get("clock"),
        "trade_stream": {
            "connected": connected,
            "age_ms": stream_age,
            "last_receive_ms": stream.get("last_receive_ms"),
            "stale_ms": stream.get("stale_ms"),
            "state": (journal_state if journal_state != C.LIVE
                      else C.LIVE if connected and isinstance(stream_age, int)
                      and stream_age <= C.TRADE_STALE_MS
                      else C.STALE if connected else C.UNKNOWN),
            # Said on the screen because the number is otherwise read as an age from now, and on
            # a stale record that reading is wrong by the whole staleness.
            "age_measured_from": "THE_SAMPLE_THE_COLLECTOR_WROTE",
            "journal_state": journal_state,
        },
        "vanished": {
            "this_reading": gone,
            "recent": tracker.vanished,
            "totals": dict(tracker.totals),
            "tracked_bins": len(tracker.bins),
            "path_samples": len(tracker.path),
        },
        "absorption": (absorption_candidate(liquidity, windows, tracker)
                       if journal_state == C.LIVE
                       else {"state": C.WALL_NONE, "reason": journal_reason,
                             "order_identity_proven": False, "note": C.ABSORPTION_NOTE}),
        "vanish_base_rate": {
            "ask_touched_bin_pct": C.VANISH_TOUCHED_BIN_ASK_PCT,
            "bid_touched_bin_pct": C.VANISH_TOUCHED_BIN_BID_PCT,
            "window": "Market Context R0, 3.61h journal, 15 consumption candidates in total",
        },
        "vanish_note": C.VANISH_NOTE,
        "absorption_note": C.ABSORPTION_NOTE,
        "imbalance_note": flow.get("imbalance_note"),
    }


__all__ = ["VanishTracker", "traded_through", "absorption_candidate", "flow_view",
           "journal_state_of",
           "unavailable_view", "SAMPLE_INTERVAL_MS", "PATH_GAP_TOLERANCE_MS",
           "ABSORPTION_WINDOW"]
