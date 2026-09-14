"""Expiry-aware in-memory OAuth client which never serializes credentials."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import logging
from threading import Lock
from weakref import WeakValueDictionary

import httpx
from zoneinfo import ZoneInfo

from app.core.exceptions import MarketDataError
from app.integrations.kiwoom.rate_limit import RequestRateLimiter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AccessToken:
    value: str
    token_type: str
    expires_at: datetime


@dataclass(frozen=True)
class TokenLease:
    """A token value paired with its in-process issuance generation."""

    value: str
    generation: int


@dataclass
class _SharedTokenState:
    token: AccessToken | None = None
    generation: int = 0
    lock: Lock = field(default_factory=Lock)


_states_lock = Lock()
_states: WeakValueDictionary[str, _SharedTokenState] = WeakValueDictionary()


def _shared_state(base_url: str, app_key: str, app_secret: str) -> _SharedTokenState:
    """Share only token state for identical credentials inside this process.

    The registry key is a one-way fingerprint, so credentials never appear in logs,
    diagnostics, or object representations. Weak values keep independent test and
    short-lived command clients from retaining stale process-global tokens.
    """
    fingerprint = hashlib.sha256(
        "\0".join((base_url, app_key, app_secret)).encode()
    ).hexdigest()
    with _states_lock:
        state = _states.get(fingerprint)
        if state is None:
            state = _SharedTokenState()
            _states[fingerprint] = state
        return state


class KiwoomAuthClient:
    def __init__(
        self,
        *,
        base_url: str,
        app_key: str,
        app_secret: str,
        http: httpx.Client | None = None,
        timeout: float = 10.0,
        limiter: RequestRateLimiter | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if not app_key or not app_secret:
            raise MarketDataError("AUTH_FAILED", "Kiwoom credentials are not configured")
        self._base_url = base_url.rstrip("/")
        self._app_key = app_key
        self._app_secret = app_secret
        self._http = http or httpx.Client(timeout=timeout)
        self._limiter = limiter or RequestRateLimiter(1.0)
        self._clock = clock
        self._state = _shared_state(self._base_url, app_key, app_secret)

    def access_token(self) -> str:
        return self.token_lease().value

    def token_lease(self) -> TokenLease:
        """Return a valid token and the generation used by one logical request."""
        with self._state.lock:
            now = self._clock()
            token = self._state.token
            if token is None or token.expires_at <= now + timedelta(seconds=60):
                self._state.token = token = self._issue_token()
                self._state.generation += 1
            return TokenLease(token.value, self._state.generation)

    def recover_after_auth_failure(self, failed_generation: int) -> TokenLease:
        """Refresh once if the failed generation is still current.

        Another caller may have refreshed while this caller waited for the lock. In
        that case the newer generation is reused instead of issuing another token.
        """
        with self._state.lock:
            token = self._state.token
            if self._state.generation != failed_generation:
                if token is not None:
                    return TokenLease(token.value, self._state.generation)
                raise MarketDataError(
                    "AUTH_FAILED", "A concurrent Kiwoom token recovery failed"
                )
            self._state.token = None
            try:
                self._state.token = token = self._issue_token()
            except MarketDataError:
                # Advance the generation even on failure. Callers waiting with the
                # failed lease then observe this completed recovery attempt and do
                # not stampede the token endpoint. A later logical request may make
                # a fresh attempt through token_lease().
                self._state.generation += 1
                raise
            self._state.generation += 1
            return TokenLease(token.value, self._state.generation)

    @property
    def expires_at(self) -> datetime | None:
        with self._state.lock:
            return None if self._state.token is None else self._state.token.expires_at

    def _issue_token(self) -> AccessToken:
        self._limiter.acquire()
        try:
            response = self._http.post(
                f"{self._base_url}/oauth2/token",
                json={
                    "grant_type": "client_credentials",
                    "appkey": self._app_key,
                    "secretkey": self._app_secret,
                },
                headers={"Content-Type": "application/json;charset=UTF-8"},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Kiwoom token request failed")
            raise MarketDataError("AUTH_FAILED", "Kiwoom authentication failed") from exc
        if payload.get("return_code") not in (None, 0) or not payload.get("token"):
            logger.warning("Kiwoom token request rejected")
            raise MarketDataError("AUTH_FAILED", "Kiwoom authentication was rejected")
        try:
            expires_at = datetime.strptime(str(payload["expires_dt"]), "%Y%m%d%H%M%S").replace(
                tzinfo=ZoneInfo("Asia/Seoul")
            ).astimezone(timezone.utc)
        except (KeyError, TypeError, ValueError) as exc:
            raise MarketDataError("AUTH_FAILED", "Kiwoom returned an invalid token expiry") from exc
        logger.info("Kiwoom token issued; expires_at=%s", expires_at.isoformat())
        return AccessToken(str(payload["token"]), str(payload.get("token_type", "Bearer")), expires_at)
