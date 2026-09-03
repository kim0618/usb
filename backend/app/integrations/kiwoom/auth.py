"""Expiry-aware in-memory OAuth client which never serializes credentials."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging

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
        self._token: AccessToken | None = None

    def access_token(self) -> str:
        now = self._clock()
        if self._token is None or self._token.expires_at <= now + timedelta(seconds=60):
            self._token = self._issue_token()
        return self._token.value

    @property
    def expires_at(self) -> datetime | None:
        return None if self._token is None else self._token.expires_at

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
