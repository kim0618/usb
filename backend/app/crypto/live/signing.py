"""HMAC SHA256 request signing for Binance signed endpoints.

Binance signs the *exact* string it receives, so the query is built once and both signed and
sent; re-encoding it between signing and sending is the classic source of `-1022 Signature for
this request is not valid`. `signed_query` therefore returns the finished query string and the
caller appends nothing to it.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Any, Mapping
from urllib.parse import urlencode

SIGNATURE_PARAM = "signature"
TIMESTAMP_PARAM = "timestamp"
RECV_WINDOW_PARAM = "recvWindow"


def canonical(params: Mapping[str, Any]) -> str:
    """Insertion-ordered form encoding, `None` values dropped, booleans lowercased.

    Binance accepts any parameter order as long as the signature covers the same string, so the
    caller's order is kept rather than sorted: a test can then compare against a literal.
    """
    pairs: list[tuple[str, str]] = []
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            pairs.append((key, "true" if value else "false"))
        else:
            pairs.append((key, str(value)))
    return urlencode(pairs)


def signature(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def signed_query(params: Mapping[str, Any], *, secret: str) -> str:
    """The complete query string for a signed request, signature last."""
    if SIGNATURE_PARAM in params:
        raise ValueError("signature must not be supplied by the caller")
    payload = canonical(params)
    return f"{payload}&{SIGNATURE_PARAM}={signature(secret, payload)}"
