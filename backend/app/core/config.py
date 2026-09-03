"""Environment-backed application configuration."""

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Validated settings loaded from environment variables or ``.env``."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = "development"
    app_name: str = "usb"
    log_level: str = "INFO"
    database_url: str = "sqlite:///data/runtime/usb.sqlite3"
    data_dir: Path = Path("data")
    market_timezone: str = "America/New_York"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    market_data_provider: str = "fake"
    broker_provider: str = "simulation"
    kiwoom_env: str = "real"
    kiwoom_mode: str = "market_data_only"
    kiwoom_app_key: SecretStr | None = None
    kiwoom_app_secret: SecretStr | None = None
    kiwoom_default_exchange: str = "ND"
    kiwoom_timeout_seconds: float = 10.0
    kiwoom_max_retries: int = 2
    run_kiwoom_live_smoke: bool = False

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        level = value.upper()
        if level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ValueError(f"Unsupported LOG_LEVEL: {value}")
        return level

    @field_validator("market_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown MARKET_TIMEZONE: {value}") from exc
        return value

    @field_validator("market_data_provider")
    @classmethod
    def validate_market_data_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"fake", "replay", "kiwoom"}:
            raise ValueError("MARKET_DATA_PROVIDER must be fake, replay, or kiwoom")
        return normalized

    @field_validator("broker_provider")
    @classmethod
    def validate_broker_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized != "simulation":
            raise ValueError("Stage 10A permits only BROKER_PROVIDER=simulation")
        return normalized

    @field_validator("kiwoom_env")
    @classmethod
    def validate_kiwoom_env(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"real", "mock"}:
            raise ValueError("KIWOOM_ENV must be real or mock")
        return normalized

    @field_validator("kiwoom_mode")
    @classmethod
    def validate_kiwoom_mode(cls, value: str) -> str:
        if value.strip().lower() != "market_data_only":
            raise ValueError("Stage 10A requires KIWOOM_MODE=market_data_only")
        return "market_data_only"

    @field_validator("kiwoom_default_exchange")
    @classmethod
    def validate_kiwoom_exchange(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in {"NA", "ND", "NY"}:
            raise ValueError("KIWOOM_DEFAULT_EXCHANGE must be NA, ND, or NY")
        return normalized

    @model_validator(mode="after")
    def validate_stage_10a_safety(self) -> "Settings":
        if self.market_data_provider == "kiwoom":
            if self.broker_provider != "simulation" or self.kiwoom_mode != "market_data_only":
                raise ValueError("Kiwoom market data requires simulation broker and MARKET_DATA_ONLY")
        return self

    @property
    def kiwoom_base_url(self) -> str:
        return "https://api.kiwoom.com" if self.kiwoom_env == "real" else "https://mockapi.kiwoom.com"

    @property
    def has_kiwoom_credentials(self) -> bool:
        return bool(
            self.kiwoom_app_key
            and self.kiwoom_app_key.get_secret_value()
            and self.kiwoom_app_secret
            and self.kiwoom_app_secret.get_secret_value()
        )

    @property
    def resolved_data_dir(self) -> Path:
        return self.data_dir if self.data_dir.is_absolute() else PROJECT_ROOT / self.data_dir

    @property
    def resolved_database_url(self) -> str:
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            return self.database_url
        raw_path = self.database_url.removeprefix(prefix)
        if raw_path == ":memory:" or Path(raw_path).is_absolute():
            return self.database_url
        return f"{prefix}{(PROJECT_ROOT / raw_path).resolve()}"

    @property
    def allowed_cors_origins(self) -> list[str]:
        """Return explicit origins only; wildcard credentials are never enabled."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide immutable-by-convention settings instance."""

    return Settings()
