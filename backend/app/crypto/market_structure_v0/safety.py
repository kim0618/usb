"""Read-only by construction, checked rather than promised.

The collector must not be *able* to trade, not merely refrain from it. Three separate mechanisms
say so, and the tests assert each one:

1. `assert_public_url` is called before every socket and every HTTP request. It accepts exactly
   three URL shapes - the two public websocket streams and the public depth endpoint - and
   refuses anything else, so a typo cannot reach an account path.
2. `DENIED_FRAGMENTS` refuses the vocabulary of a mutating or private call even if somebody later
   adds it to the allow list. The allow list says what we meant; the deny list says what must
   never be reachable whatever anyone means. This is the same two-sided guard
   `app/crypto/live/endpoints.py` uses, re-rooted here so that this package depends on nothing
   that holds credentials.
3. This package reads no API key, no secret file and no environment credential. The only
   environment variable it consults is its own output root.

The third point is why `app.crypto.live` is not imported for its endpoint registry even though
the registry is good: importing it would put a credential-aware module in this process's import
graph for no gain, and `tests/crypto/test_ms_v0_isolation.py` enforces the separation.
"""
from __future__ import annotations

from .contract import DEPTH_REST_URL, DEPTH_WS_URL, TRADE_WS_URL

#: The complete set of endpoints this process may contact.
ALLOWED_URLS: frozenset[str] = frozenset({DEPTH_WS_URL, TRADE_WS_URL, DEPTH_REST_URL})

#: Refused wherever they appear, regardless of the allow list.
DENIED_FRAGMENTS: tuple[str, ...] = (
    "order", "leverage", "margin", "position", "listenkey", "userdata", "account",
    "balance", "withdraw", "transfer", "capital", "sub-account", "apikey", "/sapi/",
    "/api/v3/", "private",
)


class NotPublicData(RuntimeError):
    """A URL outside the public market-data allow list was asked for."""

    def __init__(self, url: str, reason: str) -> None:
        super().__init__(f"{url} is not reachable from the market structure collector: {reason}")
        self.url = url
        self.reason = reason


def assert_public_url(url: str) -> str:
    """Raise unless `url` is one of the three public market-data endpoints."""
    lowered = url.lower()
    for fragment in DENIED_FRAGMENTS:
        if fragment in lowered:
            raise NotPublicData(url, f"denied fragment {fragment!r}")
    if url not in ALLOWED_URLS:
        raise NotPublicData(url, "not in the public market-data allow list")
    return url


def safety_view() -> dict[str, object]:
    """What the session record states about this process's reach."""
    return {
        "mode": "READ_ONLY_PUBLIC_MARKET_DATA",
        "allowed_urls": sorted(ALLOWED_URLS),
        "denied_fragments": list(DENIED_FRAGMENTS),
        "credentials_read": False,
        "private_streams": False,
        "order_capability": False,
        "trading_service_coupling": "NONE: separate process, no shared import, state or file",
    }


__all__ = ["assert_public_url", "safety_view", "ALLOWED_URLS", "DENIED_FRAGMENTS", "NotPublicData"]
