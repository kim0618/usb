"""Safe construction boundary for configured external market data."""

import httpx

from app.core.config import Settings
from app.core.exceptions import ConfigurationError
from app.integrations.kiwoom.auth import KiwoomAuthClient
from app.integrations.kiwoom.client import KiwoomMarketDataClient
from app.integrations.kiwoom.rate_limit import KiwoomRateLimits
from app.market.kiwoom import KiwoomMarketDataProvider


def build_kiwoom_provider(settings: Settings, *, http: httpx.Client | None = None) -> KiwoomMarketDataProvider:
    if settings.market_data_provider != "kiwoom":
        raise ConfigurationError("Kiwoom provider is not selected")
    if settings.broker_provider != "simulation" or settings.kiwoom_mode != "market_data_only":
        raise ConfigurationError("Kiwoom is restricted to MARKET_DATA_ONLY with SimulationBroker")
    if not settings.has_kiwoom_credentials:
        raise ConfigurationError("Kiwoom credentials are not configured")
    limits = KiwoomRateLimits(mock=settings.kiwoom_env == "mock")
    app_key = settings.kiwoom_app_key
    app_secret = settings.kiwoom_app_secret
    assert app_key is not None and app_secret is not None
    auth = KiwoomAuthClient(
        base_url=settings.kiwoom_base_url,
        app_key=app_key.get_secret_value(), app_secret=app_secret.get_secret_value(),
        http=http, timeout=settings.kiwoom_timeout_seconds, limiter=limits.auth,
    )
    client = KiwoomMarketDataClient(
        base_url=settings.kiwoom_base_url, auth=auth, http=http,
        timeout=settings.kiwoom_timeout_seconds, max_retries=settings.kiwoom_max_retries,
        rate_limits=limits,
    )
    return KiwoomMarketDataProvider(client, default_exchange=settings.kiwoom_default_exchange)
