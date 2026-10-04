"""Every constant the frozen contract fixes, in one place, plus the contract's own hash.

Nothing here is a tuning knob. Each figure is a clause of
`docs/crypto/market_structure_v0/DATA_CONTRACT_V0.md`, and changing one changes the meaning of a
stored dataset, which is why the contract version and the contract SHA256 travel with every
session. `contract_identity()` is what the collector writes at startup; it recomputes the hash
from the document on disk and says whether the recorded `.sha256` sidecar still agrees, so a
dataset can never quietly claim a contract it was not built against.

Two measured facts are recorded here as comments because they are the kind of assumption that
looks obvious and is wrong:

* the two websocket bases are **not** interchangeable, and the wrong one fails silently. Measured
  2026-10-04: `btcusdt@depth@100ms` delivered 115 frames in 12 s on `/public/ws` and **0 frames**
  on `/market/ws`; `btcusdt@aggTrade` delivered frames on `/market/ws` and **0** on `/public/ws`.
  Both wrong-base sockets completed the handshake and answered ping, so a base mix-up looks like a
  quiet market rather than an error. This mirrors the user-data-stream base split recorded in
  `app/crypto/live/endpoints.py`.
* `U == pu + 1` is **not** true on futures, so it must never be asserted. Measured over 881 depth
  frames: `U - pu` was distributed across 51, 55, 66, 80, 119 and more. The only continuity rule
  is `pu == previous u`.
"""
from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import Path
from typing import Any

from . import VERSION

# --------------------------------------------------------------------------- sources

#: High-frequency book data lives on the `/public` base. See the module docstring.
DEPTH_WS_URL = "wss://fstream.binance.com/public/ws/btcusdt@depth@100ms"
#: Aggregate trades live on the `/market` base. See the module docstring.
TRADE_WS_URL = "wss://fstream.binance.com/market/ws/btcusdt@aggTrade"
#: The only REST call this package ever makes.
DEPTH_REST_URL = "https://fapi.binance.com/fapi/v1/depth"
DEPTH_REST_LIMIT = 1000
USER_AGENT = "usb-crypto-market-structure-v0/1 (public market data only)"

# --------------------------------------------------------------------------- freshness

#: Monotonic receipt age past which a book is stale. A 100 ms stream missing 2 s is 20 frames.
DEPTH_STALE_MS = 2_000
#: Trades are sporadic by nature, so silence tolerates more before it means a dead stream.
TRADE_STALE_MS = 5_000
#: Exchange lag `receive_ms - E` beyond this is reported UNKNOWN rather than as a number.
LAG_UNKNOWN_MS = 5_000
#: An event timestamp further than this *ahead* of local receipt means the clocks disagree.
CLOCK_SKEW_TOLERANCE_MS = 1_000

# --------------------------------------------------------------------------- bands and windows

#: Depth bands as (label, fraction of mid). Labels are percent, values are fractions.
BANDS: tuple[tuple[str, Decimal], ...] = (
    ("0.1", Decimal("0.001")),
    ("0.25", Decimal("0.0025")),
    ("0.5", Decimal("0.005")),
    ("1", Decimal("0.01")),
)
#: Trade flow windows in seconds, measured on local monotonic receipt time.
FLOW_WINDOWS_S: tuple[int, ...] = (5, 15, 60)
#: A window can only be COMPLETE once continuous fresh coverage reaches back its whole length, so
#: the longest window sets the session warmup. Asserted rather than written twice.
WARMUP_S = max(FLOW_WINDOWS_S)
#: Decimal places kept for the normalized imbalance ratios, which are inexact divisions.
RATIO_DECIMALS = 10

# --------------------------------------------------------------------------- walls

#: Candidates are looked for within this fraction of mid on each side.
WALL_BAND = Decimal("0.01")
#: Up to this many adjacent *occupied* levels on each side form the comparison set.
WALL_NEIGHBOURS_PER_SIDE = 5
#: Fewer neighbours than this and the local average is not a comparison, so no candidate.
WALL_MIN_NEIGHBOURS = 3
#: A level qualifies at or above this multiple of the neighbour mean.
WALL_MULTIPLE = Decimal("3")

# --------------------------------------------------------------------------- bounds

#: Depth frames waiting to reach the single task that owns the book.
DEPTH_QUEUE_MAX = 2_048
#: Serialized records waiting for the writer.
PERSIST_QUEUE_MAX = 8_192
#: Websocket frame ceiling. A larger frame is a protocol surprise, not data.
WS_MAX_FRAME_BYTES = 1 << 20
#: Retained individual flow records. At the measured 0.65 trades/s a 60 s window holds ~40, so
#: this is a safety ceiling, not a working size.
FLOW_MAX_RECORDS = 200_000
#: Retained book levels across both sides.
MAX_BOOK_LEVELS = 20_000

# --------------------------------------------------------------------------- persistence

ROTATE_BYTES = 64 << 20
ROTATE_SECONDS = 3_600
WRITE_BUFFER_BYTES = 1 << 20
FLUSH_INTERVAL_S = 1.0
#: Never per event. A power loss may cost up to this much, and recovery says so.
FSYNC_INTERVAL_S = 10.0
OPEN_SUFFIX = ".jsonl.open"
FINAL_SUFFIX = ".jsonl"
LOCK_FILENAME = ".writer.lock"

#: Record kinds. A writer exists per kind, and `storage_stats` reports bytes by kind.
KINDS: tuple[str, ...] = (
    "session", "raw_depth", "snapshot", "checkpoint", "raw_trade", "trade",
    "telemetry", "derived", "wall", "storage_stats",
)

# --------------------------------------------------------------------------- scheduling

SAMPLE_INTERVAL_S = 1.0
STATS_INTERVAL_S = 60.0
#: Socket read timeout. Silence this long is treated as a dead connection and reconnected.
RECV_TIMEOUT_S = 30.0
RECONNECT_BACKOFF_MAX_S = 30.0
#: Default preview length. `0` means run until stopped.
DEFAULT_DURATION_S = 24 * 60 * 60

# --------------------------------------------------------------------------- operational only
# These do not appear in the frozen contract and change no stored semantics. They exist so an
# unattended run always reaches the point where it seals its files.

#: Ceiling on each stage of shutdown. Measured: a clean stop takes about 8 s, nearly all of it
#: the websocket close handshake. Past this the stage is abandoned, the abandonment is recorded,
#: and shutdown continues to sealing, because a trial that never seals its files is worse than
#: one that reports an unclean stop.
SHUTDOWN_TIMEOUT_S = 15.0

# --------------------------------------------------------------------------- coverage vocabulary

COMPLETE = "COMPLETE"
PARTIAL = "PARTIAL"
UNKNOWN = "UNKNOWN"
COVERAGE_STATES = (COMPLETE, PARTIAL, UNKNOWN)

# --------------------------------------------------------------------------- contract identity

CONTRACT_RELATIVE_PATH = "docs/crypto/market_structure_v0/DATA_CONTRACT_V0.md"


def repo_root() -> Path:
    """`backend/app/crypto/market_structure_v0/contract.py` -> repository root."""
    return Path(__file__).resolve().parents[4]


def contract_path() -> Path:
    return repo_root() / CONTRACT_RELATIVE_PATH


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def contract_identity(path: Path | None = None) -> dict[str, Any]:
    """The contract's version, its hash as computed now, and whether the sidecar agrees.

    A missing document is reported rather than raised: the collector must be runnable from a
    deployment that ships code without docs, but the session record then says so out loud
    instead of implying a contract it cannot see.
    """
    target = path or contract_path()
    recorded_path = target.with_suffix(".sha256")
    identity: dict[str, Any] = {
        "contract_version": VERSION,
        "contract_relative_path": CONTRACT_RELATIVE_PATH,
        "contract_sha256": None,
        "recorded_sha256": None,
        "sha256_agrees": None,
        "status": "MISSING",
    }
    if not target.exists():
        return identity
    identity["contract_sha256"] = sha256_bytes(target.read_bytes())
    identity["status"] = "PRESENT"
    if recorded_path.exists():
        recorded = recorded_path.read_text(encoding="utf-8").split()
        identity["recorded_sha256"] = recorded[0] if recorded else None
        identity["sha256_agrees"] = identity["recorded_sha256"] == identity["contract_sha256"]
    return identity


def _self_check() -> None:
    assert WARMUP_S == max(FLOW_WINDOWS_S)
    assert len({label for label, _ in BANDS}) == len(BANDS)
    assert all(fraction > 0 for _, fraction in BANDS)
    assert WALL_BAND >= max(fraction for _, fraction in BANDS)
    assert len(set(KINDS)) == len(KINDS)


_self_check()
