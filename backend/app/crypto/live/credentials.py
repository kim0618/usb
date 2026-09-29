"""Credentials and feature flags, read from the environment and from nowhere else.

The secret is held in a field that no `repr`, log line or JSON response can reach: the dataclass
sets `repr=False` on it, `__str__` is overridden, and `redact()` is applied to every message that
leaves this package. `describe()` is the only thing the API is ever allowed to publish about a
key, and it publishes a fingerprint - never a prefix of the key itself, because a prefix of an
API key is still part of the key.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from .endpoints import DEFAULT_BASE_URL, DEFAULT_WS_PRIVATE_URL, DEFAULT_WS_PUBLIC_URL

API_KEY_ENV = "BINANCE_API_KEY"
API_SECRET_ENV = "BINANCE_API_SECRET"
TRADING_FLAG_ENV = "BINANCE_LIVE_TRADING_ENABLED"
#: The second, independent arming flag. Both this and `TRADING_FLAG_ENV` must be affirmative
#: before an order can be sent, and neither is a default. Two variables rather than one because
#: the two gates are meant to fail separately: one of them being set by a stale shell, a copied
#: unit file or a half-finished edit must not on its own arm a real account.
CLIENT_ARM_ENV = "BINANCE_LIVE_CLIENT_ARMED"
#: An order-size ceiling this process imposes on itself, below whatever Binance would allow.
#: Unset means only the exchange's filters apply. It bounds OPEN orders; a CLOSE is never
#: bounded, because a position must always be closable whatever size it is.
MAX_QTY_ENV = "BINANCE_LIVE_MAX_QTY"
BASE_URL_ENV = "BINANCE_FUTURES_BASE_URL"
WS_PUBLIC_URL_ENV = "BINANCE_FUTURES_WS_PUBLIC_URL"
WS_PRIVATE_URL_ENV = "BINANCE_FUTURES_WS_PRIVATE_URL"
RECV_WINDOW_ENV = "BINANCE_RECV_WINDOW_MS"
SYMBOL_ENV = "BINANCE_LIVE_SYMBOL"

#: V1 supports exactly one instrument. Anything else is refused rather than attempted.
SUPPORTED_SYMBOL = "BTCUSDT"

#: Binance's own ceiling for `recvWindow`.
MAX_RECV_WINDOW_MS = 60_000
DEFAULT_RECV_WINDOW_MS = 5_000

_REDACTED = "[REDACTED]"


class CredentialsMissing(RuntimeError):
    """No API key or secret in the environment. Read-only LIVE cannot start."""


@dataclass(frozen=True)
class Credentials:
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.api_key or not self.api_secret:
            raise CredentialsMissing("both an API key and an API secret are required")

    def __repr__(self) -> str:  # pragma: no cover - exercised by the redaction test
        return f"Credentials(api_key={_REDACTED}, api_secret={_REDACTED})"

    __str__ = __repr__

    @property
    def fingerprint(self) -> str:
        """First 8 hex characters of sha256(api_key). Identifies which key is loaded without
        revealing any part of it, so two keys can be told apart in a report."""
        return hashlib.sha256(self.api_key.encode()).hexdigest()[:8]

    def redact(self, text: str) -> str:
        """Every place a message could have picked up a credential. Applied to exception text
        before it is logged or returned, so a mistake upstream still cannot leak the key."""
        for secret in (self.api_secret, self.api_key):
            if secret and secret in text:
                text = text.replace(secret, _REDACTED)
        return text


@dataclass(frozen=True)
class LiveConfig:
    """Everything the LIVE path reads from the environment, resolved once."""
    symbol: str
    base_url: str
    ws_public_url: str
    ws_private_url: str
    recv_window_ms: int
    trading_enabled: bool
    client_armed: bool
    max_open_qty: Decimal | None
    credentials_present: bool
    fingerprint: str | None

    @property
    def armed(self) -> bool:
        """Both environment gates. `LiveOrderRouter` checks the client's own flag as well, so
        this is a necessary condition for sending an order and not a sufficient one."""
        return self.trading_enabled and self.client_armed

    def view(self) -> dict[str, object]:
        """Safe to serialise. No key, no secret, no prefix of either."""
        return {"symbol": self.symbol, "base_url": self.base_url,
                "ws_private_url": self.ws_private_url,
                "recv_window_ms": self.recv_window_ms,
                "trading_enabled": self.trading_enabled,
                "client_armed": self.client_armed,
                "max_open_qty": self.max_open_qty,
                "credentials_present": self.credentials_present,
                "api_key_fingerprint": self.fingerprint}


def trading_enabled(environ: dict[str, str] | None = None) -> bool:
    """False unless the flag is one of a small closed set of affirmatives.

    Default false, and an unrecognised value is false: a typo in the variable must not arm live
    order routing.
    """
    env = os.environ if environ is None else environ
    return env.get(TRADING_FLAG_ENV, "false").strip().lower() in {"1", "true", "yes", "on"}


def client_armed(environ: dict[str, str] | None = None) -> bool:
    """The second gate, read the same way and defaulting the same way: false.

    Deliberately a separate function over a separate variable rather than a second reading of
    `TRADING_FLAG_ENV`. Collapsing the two into one flag would mean a single mistake arms a real
    account, which is exactly the property the two gates exist to deny.
    """
    env = os.environ if environ is None else environ
    return env.get(CLIENT_ARM_ENV, "false").strip().lower() in {"1", "true", "yes", "on"}


def _max_open_qty(env: dict[str, str]) -> Decimal | None:
    """The self-imposed OPEN ceiling, or `None` when the variable is absent.

    A malformed value raises rather than falling back to "no limit": someone who set this
    variable was trying to bound their risk, and silently ignoring a typo would hand them the
    opposite of what they asked for.
    """
    raw = (env.get(MAX_QTY_ENV) or "").strip()
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"{MAX_QTY_ENV} is not a number: {raw!r}") from exc
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{MAX_QTY_ENV} must be a positive, finite quantity: {raw!r}")
    return value


def load_credentials(environ: dict[str, str] | None = None) -> Credentials:
    env = os.environ if environ is None else environ
    key = (env.get(API_KEY_ENV) or "").strip()
    secret = (env.get(API_SECRET_ENV) or "").strip()
    if not key or not secret:
        missing = [name for name, value in ((API_KEY_ENV, key), (API_SECRET_ENV, secret)) if not value]
        raise CredentialsMissing(f"missing environment variable(s): {', '.join(missing)}")
    return Credentials(api_key=key, api_secret=secret)


def _recv_window(env: dict[str, str]) -> int:
    raw = (env.get(RECV_WINDOW_ENV) or "").strip()
    if not raw:
        return DEFAULT_RECV_WINDOW_MS
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{RECV_WINDOW_ENV} is not an integer: {raw!r}") from exc
    if not 0 < value <= MAX_RECV_WINDOW_MS:
        raise ValueError(f"{RECV_WINDOW_ENV} must be in (0, {MAX_RECV_WINDOW_MS}]: {value}")
    return value


def load_config(environ: dict[str, str] | None = None) -> LiveConfig:
    """Resolve the LIVE configuration. Never raises for a missing credential: the status route
    has to be able to say "no key configured" rather than fail."""
    env = dict(os.environ if environ is None else environ)
    symbol = (env.get(SYMBOL_ENV) or SUPPORTED_SYMBOL).strip().upper()
    if symbol != SUPPORTED_SYMBOL:
        raise ValueError(f"V1 supports {SUPPORTED_SYMBOL} only, got {symbol!r}")
    try:
        credentials = load_credentials(env)
    except CredentialsMissing:
        credentials = None
    return LiveConfig(
        symbol=symbol,
        base_url=(env.get(BASE_URL_ENV) or DEFAULT_BASE_URL).rstrip("/"),
        ws_public_url=(env.get(WS_PUBLIC_URL_ENV) or DEFAULT_WS_PUBLIC_URL).rstrip("/"),
        ws_private_url=(env.get(WS_PRIVATE_URL_ENV) or DEFAULT_WS_PRIVATE_URL).rstrip("/"),
        recv_window_ms=_recv_window(env),
        trading_enabled=trading_enabled(env),
        client_armed=client_armed(env),
        max_open_qty=_max_open_qty(env),
        credentials_present=credentials is not None,
        fingerprint=credentials.fingerprint if credentials is not None else None,
    )
