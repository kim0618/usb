"""Every constant this collector fixes, and the ones it borrows, in one place.

Nothing here restates a threshold another frozen contract owns. Freshness bounds come from
`btc-ms.v0.1` and the Liquidity Map viewer, the wall rule is `lm-wall.v2` as `wallrule.py`
implements it, and the vanished-wall and absorption rules are `mc-display.v1` as the vendored
copy implements them. What is new is only the persistence policy and the record shapes.
"""
from __future__ import annotations

from ..liquidity_map.view import JOURNAL_STALE_MS
from ..market_structure_v0 import EXCHANGE, SYMBOL
from ..market_structure_v0.contract import DEPTH_STALE_MS, KINDS, TRADE_STALE_MS, WALL_BAND
from .mcv1_vendored import liquidity as MCL

# --------------------------------------------------------------------------- persistence policy

#: V0 kinds written unchanged. These three plus the compact state file are everything the
#: Liquidity Map viewer reads on its normal path, which is what keeps this root readable by it.
PERSISTED_V0_KINDS: tuple[str, ...] = ("session", "telemetry", "storage_stats")

#: V0 kinds that are never written. Grouped by why, because the reasons differ:
#: replay material is what a research collector exists to keep and a context collector does not;
#: `derived` is rewritten whole into the state file every second; V0 `wall` rows are open/close
#: transitions of every loose V0 candidate (2.8 million a day), and the resting set they would
#: reconstruct is already in the state file.
DROPPED_REPLAY_KINDS: tuple[str, ...] = ("raw_depth", "raw_trade", "trade", "snapshot",
                                         "checkpoint")
DROPPED_DUPLICATE_KINDS: tuple[str, ...] = ("derived", "wall")
DROPPED_V0_KINDS: tuple[str, ...] = DROPPED_REPLAY_KINDS + DROPPED_DUPLICATE_KINDS

#: This collector's own kinds.
CONTEXT_KIND = "context"
WALL_V2_KIND = "wall_v2"
#: The V0 candidate rows Market Context R0's replay needs and nothing else. See `wallr0.py`.
WALL_R0_KIND = "wall_r0"
CONTEXT_KINDS: tuple[str, ...] = (CONTEXT_KIND, WALL_V2_KIND, WALL_R0_KIND)

#: Kinds whose sealed hourly files are gzip-compressed. Only this collector's own: the V0 streams
#: stay plain so the frozen viewer's journal reader can read them.
COMPRESSED_KINDS: tuple[str, ...] = CONTEXT_KINDS
COMPRESSED_SUFFIX = ".jsonl.gz"

#: The latest `context` payload, replaced atomically every sample. What the API serves.
LATEST_FILENAME = "context_latest.json"

# --------------------------------------------------------------------------- scope

#: BTCUSDT only. Every validated layer was built on it, and the V0 stream URLs name it.
SUPPORTED_SYMBOL = SYMBOL
VENUE = EXCHANGE

# --------------------------------------------------------------------------- display parity

#: What the Market Context V1 panel passes to the viewer, carried so the nearest wall, the shown
#: walls and the vanished-wall tracker see exactly the set the panel sees.
DISPLAY_MIN_NOTIONAL_USDT = MCL.DEFAULT_MIN_NOTIONAL_USDT
DISPLAY_WALL_LIMIT = MCL.DEFAULT_WALL_LIMIT

# --------------------------------------------------------------------------- feed vocabulary

#: The collector's own whole-feed word, published beside the per-layer states and never instead
#: of them. SYNCING is kept apart from UNKNOWN because "the book is being built" and "there is no
#: reading" call for different patience, and the panel's four-state layer vocabulary maps both to
#: UNKNOWN. There is no NEUTRAL in this set and no state that renders as a balanced middle.
FEED_LIVE = "LIVE"
FEED_PARTIAL = "PARTIAL"
FEED_SYNCING = "SYNCING"
FEED_STALE = "STALE"
FEED_UNKNOWN = "UNKNOWN"
FEED_STATES: tuple[str, ...] = (FEED_LIVE, FEED_PARTIAL, FEED_SYNCING, FEED_STALE, FEED_UNKNOWN)

#: How old the latest record may be when it is read before every state in it is floored to
#: STALE. The viewer's own journal bound: three sample intervals.
READ_STALE_MS = JOURNAL_STALE_MS

# --------------------------------------------------------------------------- wall_v2 events

WALL_V2_OPEN = "OPEN"
WALL_V2_CHANGE = "CHANGE"
WALL_V2_CLOSE = "CLOSE"
#: Fields whose change in an open bin is published. Everything else about the bin is either fixed
#: by its identity (side, bin edges) or derivable from the `context` record of the same second
#: (distance from mid). Notional is deliberately absent: it moves almost every second, and the
#: nearest wall's notional is already in every `context` record.
WALL_V2_TRACKED: tuple[str, ...] = ("price", "coverage", "generation", "bin_members",
                                    "persistence_source")
#: Why a bin stopped being in the selection.
CLOSE_NOT_SELECTED = "NOT_SELECTED"
CLOSE_NO_READING = "NO_USABLE_READING"
CLOSE_SESSION_END = "SESSION_END"

# --------------------------------------------------------------------------- V1.1 (CONTRACT_CTX_V1_1)

#: Section 1. The warm-up gate's reason. Its length is `lm-wall.v2 MIN_PERSISTENCE_MS`, imported
#: where it is used; no number of its own.
WARMUP_REASON = "WALL_PERSISTENCE_WARMUP"

#: Section 2. End classes of a display disappearance, in the order they are checked.
END_UNKNOWN = "UNKNOWN"
END_RANK_EVICTED = "RANK_EVICTED"
END_OUT_OF_COVERAGE = "OUT_OF_COVERAGE"
END_TRUE_ENDED = "TRUE_ENDED"
END_CLASSES: tuple[str, ...] = (END_UNKNOWN, END_RANK_EVICTED, END_OUT_OF_COVERAGE,
                                END_TRUE_ENDED)
#: Section 2. Classes that are an end, and so may carry the panel's verdict.
END_CLASSES_PUBLISHED_AS_VANISHED: tuple[str, ...] = (END_TRUE_ENDED, END_UNKNOWN)
RANK_BELOW_DISPLAY_FILTER = "BELOW_DISPLAY_FILTER"
RANK_BEYOND_DISPLAY_LIMIT = "BEYOND_DISPLAY_LIMIT"
COVERAGE_OUTSIDE_KNOWN_INTERVAL = "OUTSIDE_KNOWN_INTERVAL"
COVERAGE_BEYOND_CANDIDATE_BAND = "BEYOND_V0_CANDIDATE_BAND"
#: The V0 candidate band (`btc-ms.v0.1 WALL_BAND`), restated by import.
CANDIDATE_BAND = WALL_BAND

#: Section 3. Where the volatile current-state cache directory may be named.
STATE_CACHE_ENV = "CTX_V1_STATE_CACHE"

#: Section 4. Which wall stream a forward study treats as primary.
WALL_ROLES = {"PRIMARY": "wall_r0 (OPENED_ROW values; reproduces Market Context R0)",
              "SECONDARY_DIAGNOSTIC": "wall_v2 (CURRENT values; the panel's semantics)"}

# --------------------------------------------------------------------------- borrowed bounds

BOOK_STALE_MS = DEPTH_STALE_MS
TRADES_STALE_MS = TRADE_STALE_MS


def _self_check() -> None:
    assert set(PERSISTED_V0_KINDS) | set(DROPPED_V0_KINDS) == set(KINDS), "every V0 kind decided"
    assert not set(PERSISTED_V0_KINDS) & set(DROPPED_V0_KINDS)
    assert not set(CONTEXT_KINDS) & set(KINDS), "own kinds must not shadow V0 kinds"
    assert set(COMPRESSED_KINDS) <= set(CONTEXT_KINDS)


_self_check()
